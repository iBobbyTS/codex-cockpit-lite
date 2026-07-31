use std::collections::VecDeque;
use std::fs::OpenOptions;
use std::io::Write;
use std::path::PathBuf;
use std::sync::{Condvar, Mutex};
use std::time::{Duration, Instant};

use tauri::{AppHandle, ExitRequestApi, Manager, RunEvent};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

const BACKEND_READY_TIMEOUT: Duration = Duration::from_secs(15);
const BACKEND_STOP_TIMEOUT: Duration = Duration::from_secs(3);
const BACKEND_SHUTDOWN_REQUEST_TIMEOUT: Duration = Duration::from_secs(1);
const BACKEND_FINAL_SHUTDOWN_REQUEST_TIMEOUT: Duration = Duration::from_millis(250);
const CONTROL_HEADER: &str = "X-Codex-Cockpit-Control";
const STDERR_LINES: usize = 20;

#[derive(Default)]
struct BackendInner {
    port: Option<u16>,
    control_token: Option<String>,
    error: Option<String>,
    child: Option<CommandChild>,
    terminated: bool,
    stopping: bool,
    exit_cleanup_started: bool,
    exit_cleanup_finished: bool,
    stderr_tail: VecDeque<String>,
}

#[derive(Default)]
struct BackendState {
    inner: Mutex<BackendInner>,
    changed: Condvar,
}

#[derive(Debug, PartialEq, Eq)]
enum ExitAction {
    AllowExit,
    StartCleanup,
    WaitForCleanup,
}

fn config_dir() -> PathBuf {
    if let Some(path) = std::env::var_os("CODEX_COCKPIT_HOME") {
        return PathBuf::from(path);
    }
    if let Some(home) = std::env::var_os("HOME").or_else(|| std::env::var_os("USERPROFILE")) {
        return PathBuf::from(home).join(".config").join("codex-cockpit");
    }
    PathBuf::from(".").join(".config").join("codex-cockpit")
}

fn log_line(message: &str) {
    let dir = config_dir();
    if std::fs::create_dir_all(&dir).is_ok() {
        if let Ok(mut file) = OpenOptions::new()
            .create(true)
            .append(true)
            .open(dir.join("cockpit.log"))
        {
            let _ = writeln!(file, "{message}");
        }
    }
    eprintln!("{message}");
}

fn parse_port_line(line: &str) -> Option<u16> {
    line.strip_prefix("PORT=")?.trim().parse().ok()
}

fn parse_control_line(line: &str) -> Option<String> {
    let token = line.strip_prefix("CONTROL=")?.trim();
    (!token.is_empty()).then(|| token.to_owned())
}

fn error_detail(status: u16, body: &str) -> String {
    if let Ok(value) = serde_json::from_str::<serde_json::Value>(body) {
        if let Some(detail) = value.get("detail").and_then(serde_json::Value::as_str) {
            return detail.replace("UNSUPPORTED_AUTH: ", "");
        }
    }
    format!("HTTP {status}: {body}")
}

fn set_start_error(app: &AppHandle, message: String) {
    log_line(&format!("[cockpit] {message}"));
    let state = app.state::<BackendState>();
    let mut inner = state
        .inner
        .lock()
        .unwrap_or_else(|poison| poison.into_inner());
    inner.error = Some(message);
    state.changed.notify_all();
}

async fn start_backend(app: AppHandle) {
    log_line("[cockpit] Starting bundled backend sidecar");
    let command = match app.shell().sidecar("codex-cockpit-backend") {
        Ok(command) => command,
        Err(error) => {
            set_start_error(&app, format!("无法创建后端 sidecar: {error}"));
            return;
        }
    };
    let (mut events, child) = match command.spawn() {
        Ok(spawned) => spawned,
        Err(error) => {
            set_start_error(&app, format!("后端启动失败: {error}"));
            return;
        }
    };

    let mut spawned_child = Some(child);
    let terminate_after_spawn = {
        let state = app.state::<BackendState>();
        let mut inner = state
            .inner
            .lock()
            .unwrap_or_else(|poison| poison.into_inner());
        if inner.exit_cleanup_finished {
            true
        } else {
            inner.child = spawned_child.take();
            inner.port = None;
            inner.control_token = None;
            inner.terminated = false;
            inner.error = None;
            state.changed.notify_all();
            false
        }
    };

    if terminate_after_spawn {
        log_line("[cockpit] App exited before backend startup completed; terminating sidecar");
        if let Err(error) = terminate_exact_child(
            spawned_child.expect("late sidecar child must remain available for termination"),
        ) {
            log_line(&format!(
                "[cockpit] Failed to terminate late backend sidecar: {error}"
            ));
        }
        return;
    }

    while let Some(event) = events.recv().await {
        match event {
            CommandEvent::Stdout(bytes) => {
                for line in String::from_utf8_lossy(&bytes).lines() {
                    if let Some(token) = parse_control_line(line.trim()) {
                        let state = app.state::<BackendState>();
                        let mut inner = state
                            .inner
                            .lock()
                            .unwrap_or_else(|poison| poison.into_inner());
                        inner.control_token = Some(token);
                        state.changed.notify_all();
                    } else if let Some(port) = parse_port_line(line.trim()) {
                        let state = app.state::<BackendState>();
                        let mut inner = state
                            .inner
                            .lock()
                            .unwrap_or_else(|poison| poison.into_inner());
                        inner.port = Some(port);
                        inner.error = None;
                        state.changed.notify_all();
                        log_line(&format!("[cockpit] Backend ready on port {port}"));
                    }
                }
            }
            CommandEvent::Stderr(bytes) => {
                let line = String::from_utf8_lossy(&bytes).trim().to_owned();
                if !line.is_empty() {
                    log_line(&format!("[cockpit] [backend] {line}"));
                    let state = app.state::<BackendState>();
                    let mut inner = state
                        .inner
                        .lock()
                        .unwrap_or_else(|poison| poison.into_inner());
                    inner.stderr_tail.push_back(line);
                    if inner.stderr_tail.len() > STDERR_LINES {
                        inner.stderr_tail.pop_front();
                    }
                }
            }
            CommandEvent::Error(error) => {
                set_start_error(&app, format!("读取后端进程输出失败: {error}"));
            }
            CommandEvent::Terminated(payload) => {
                let state = app.state::<BackendState>();
                let mut inner = state
                    .inner
                    .lock()
                    .unwrap_or_else(|poison| poison.into_inner());
                inner.terminated = true;
                inner.child = None;
                if !inner.stopping {
                    let tail = inner
                        .stderr_tail
                        .iter()
                        .cloned()
                        .collect::<Vec<_>>()
                        .join("\n");
                    let message = if tail.is_empty() {
                        format!("后端提前退出，退出码: {:?}", payload.code)
                    } else {
                        format!("后端提前退出，退出码: {:?}\n{tail}", payload.code)
                    };
                    inner.error = Some(message.clone());
                    log_line(&format!("[cockpit] {message}"));
                }
                state.changed.notify_all();
            }
            _ => {}
        }
    }
}

fn request_shutdown_with_timeout(
    port: u16,
    control_token: &str,
    timeout: Duration,
) -> Result<(), String> {
    let agent = ureq::Agent::config_builder()
        .http_status_as_error(false)
        .timeout_global(Some(timeout))
        .build()
        .new_agent();
    let url = format!("http://127.0.0.1:{port}/api/cockpit/shutdown");
    let response = agent
        .post(&url)
        .header(CONTROL_HEADER, control_token)
        .send_empty()
        .map_err(|error| format!("请求后端优雅退出失败: {error}"))?;
    if response.status().as_u16() != 204 {
        return Err(format!(
            "后端拒绝优雅退出，状态码: {}",
            response.status().as_u16()
        ));
    }
    Ok(())
}

fn request_shutdown(port: u16, control_token: &str) -> Result<(), String> {
    request_shutdown_with_timeout(port, control_token, BACKEND_SHUTDOWN_REQUEST_TIMEOUT)
}

#[cfg(unix)]
fn terminate_exact_child(child: CommandChild) -> Result<(), String> {
    let pid = child.pid();
    // SAFETY: `pid` comes from the still-owned CommandChild, and SIGTERM does not
    // dereference memory. PyInstaller's outer bootloader forwards it to its child.
    let result = unsafe { libc::kill(pid as i32, libc::SIGTERM) };
    if result == 0 {
        Ok(())
    } else {
        Err(std::io::Error::last_os_error().to_string())
    }
}

#[cfg(not(unix))]
fn terminate_exact_child(child: CommandChild) -> Result<(), String> {
    child.kill().map_err(|error| error.to_string())
}

fn prepare_app_exit(state: &BackendState) -> ExitAction {
    let mut inner = state
        .inner
        .lock()
        .unwrap_or_else(|poison| poison.into_inner());
    if inner.exit_cleanup_finished
        || inner.terminated
        || (inner.child.is_none() && inner.error.is_some())
    {
        ExitAction::AllowExit
    } else if inner.exit_cleanup_started {
        ExitAction::WaitForCleanup
    } else {
        inner.exit_cleanup_started = true;
        ExitAction::StartCleanup
    }
}

fn finish_exit_cleanup(state: &BackendState) {
    let mut inner = state
        .inner
        .lock()
        .unwrap_or_else(|poison| poison.into_inner());
    inner.exit_cleanup_finished = true;
    state.changed.notify_all();
}

fn stop_backend(app: &AppHandle) {
    let state = app.state::<BackendState>();
    let shutdown_target = {
        let mut inner = state
            .inner
            .lock()
            .unwrap_or_else(|poison| poison.into_inner());
        if inner.stopping {
            return;
        }
        inner.stopping = true;

        let deadline = Instant::now() + BACKEND_STOP_TIMEOUT;
        loop {
            if inner.terminated || (inner.child.is_none() && inner.error.is_some()) {
                inner.exit_cleanup_finished = true;
                state.changed.notify_all();
                return;
            }
            if inner.child.is_some() {
                if let Some(target) = inner.port.zip(inner.control_token.clone()) {
                    break Some(target);
                }
            }
            let now = Instant::now();
            if now >= deadline {
                break None;
            }
            let (next, _) = state
                .changed
                .wait_timeout(inner, deadline.saturating_duration_since(now))
                .unwrap_or_else(|poison| poison.into_inner());
            inner = next;
        }
    };

    log_line("[cockpit] Stopping backend sidecar");
    if let Some((port, control_token)) = &shutdown_target {
        if let Err(error) = request_shutdown(*port, control_token) {
            log_line(&format!("[cockpit] {error}"));
        }
    } else {
        log_line("[cockpit] Backend control protocol was not ready before shutdown");
    }

    if shutdown_target.is_some() {
        let inner = state
            .inner
            .lock()
            .unwrap_or_else(|poison| poison.into_inner());
        let (inner, _) = state
            .changed
            .wait_timeout_while(inner, BACKEND_STOP_TIMEOUT, |value| !value.terminated)
            .unwrap_or_else(|poison| poison.into_inner());
        if inner.terminated {
            drop(inner);
            finish_exit_cleanup(&state);
            log_line("[cockpit] Backend sidecar stopped gracefully");
            return;
        }
    }

    let child = state
        .inner
        .lock()
        .unwrap_or_else(|poison| poison.into_inner())
        .child
        .take();
    if let Some(child) = child {
        log_line("[cockpit] Backend graceful shutdown timed out; terminating exact sidecar PID");
        if let Err(error) = terminate_exact_child(child) {
            log_line(&format!(
                "[cockpit] Failed to terminate backend sidecar: {error}"
            ));
        }
    }
    finish_exit_cleanup(&state);
}

fn handle_exit_requested(app: &AppHandle, code: Option<i32>, api: &ExitRequestApi) {
    let state = app.state::<BackendState>();
    match prepare_app_exit(&state) {
        ExitAction::AllowExit => {}
        ExitAction::WaitForCleanup => api.prevent_exit(),
        ExitAction::StartCleanup => {
            api.prevent_exit();
            for window in app.webview_windows().into_values() {
                if let Err(error) = window.hide() {
                    log_line(&format!(
                        "[cockpit] Failed to hide window during exit: {error}"
                    ));
                }
            }

            log_line("[cockpit] App exit requested; stopping backend in background");
            let app_handle = app.clone();
            tauri::async_runtime::spawn(async move {
                let worker_handle = app_handle.clone();
                if let Err(error) =
                    tauri::async_runtime::spawn_blocking(move || stop_backend(&worker_handle)).await
                {
                    log_line(&format!("[cockpit] Backend shutdown task failed: {error}"));
                    let state = app_handle.state::<BackendState>();
                    finish_exit_cleanup(&state);
                }
                log_line("[cockpit] Backend cleanup finished; exiting app");
                app_handle.exit(code.unwrap_or(0));
            });
        }
    }
}

fn initiate_final_backend_shutdown(app: &AppHandle) {
    let state = app.state::<BackendState>();
    let shutdown_target = {
        let mut inner = state
            .inner
            .lock()
            .unwrap_or_else(|poison| poison.into_inner());
        if inner.terminated || (inner.child.is_none() && inner.error.is_some()) {
            return;
        }
        inner.stopping = true;
        inner.exit_cleanup_started = true;
        inner.exit_cleanup_finished = true;
        inner.port.zip(inner.control_token.clone())
    };

    log_line("[cockpit] Final app exit; initiating backend shutdown without waiting");
    if let Some((port, control_token)) = shutdown_target {
        if request_shutdown_with_timeout(
            port,
            &control_token,
            BACKEND_FINAL_SHUTDOWN_REQUEST_TIMEOUT,
        )
        .is_ok()
        {
            log_line("[cockpit] Backend graceful shutdown initiated during final app exit");
            return;
        }
    }

    let child = state
        .inner
        .lock()
        .unwrap_or_else(|poison| poison.into_inner())
        .child
        .take();
    if let Some(child) = child {
        log_line("[cockpit] Final graceful shutdown unavailable; terminating exact sidecar PID");
        if let Err(error) = terminate_exact_child(child) {
            log_line(&format!(
                "[cockpit] Failed final sidecar termination: {error}"
            ));
        }
    }
}

fn wait_for_backend(state: &BackendState) -> Result<u16, String> {
    let deadline = Instant::now() + BACKEND_READY_TIMEOUT;
    loop {
        {
            let inner = state
                .inner
                .lock()
                .unwrap_or_else(|poison| poison.into_inner());
            if let (Some(port), Some(_)) = (inner.port, inner.control_token.as_ref()) {
                return Ok(port);
            }
            if let Some(error) = &inner.error {
                return Err(error.clone());
            }
            if inner.terminated {
                return Err("后端已退出，未能提供服务".to_owned());
            }
        }
        if Instant::now() >= deadline {
            let inner = state
                .inner
                .lock()
                .unwrap_or_else(|poison| poison.into_inner());
            let tail = inner
                .stderr_tail
                .iter()
                .cloned()
                .collect::<Vec<_>>()
                .join("\n");
            let message = if tail.is_empty() {
                "等待后端启动超时（15 秒）".to_owned()
            } else {
                format!("等待后端启动超时（15 秒）\n{tail}")
            };
            log_line(&format!("[cockpit] {message}"));
            return Err(message);
        }
        std::thread::sleep(Duration::from_millis(100));
    }
}

fn proxy_request(method: &str, url: &str, body: Option<&str>) -> Result<String, String> {
    let agent = ureq::Agent::config_builder()
        .http_status_as_error(false)
        .build()
        .new_agent();
    let result = match method {
        "GET" => agent.get(url).call(),
        "POST" => agent
            .post(url)
            .content_type("application/json")
            .send(body.unwrap_or_default()),
        "PUT" => agent
            .put(url)
            .content_type("application/json")
            .send(body.unwrap_or_default()),
        "DELETE" => agent.delete(url).call(),
        _ => return Err(format!("不支持的请求方法: {method}")),
    }
    .map_err(|error| format!("请求失败: {error}"))?;

    let mut response = result;
    let status = response.status().as_u16();
    let response_body = response
        .body_mut()
        .read_to_string()
        .map_err(|error| format!("读取后端响应失败: {error}"))?;
    if status >= 400 {
        return Err(error_detail(status, &response_body));
    }
    Ok(response_body)
}

#[tauri::command]
async fn api_call(
    app: AppHandle,
    method: String,
    path: String,
    body: Option<String>,
) -> Result<String, String> {
    let wait_app = app.clone();
    let port = tauri::async_runtime::spawn_blocking(move || {
        let state = wait_app.state::<BackendState>();
        wait_for_backend(&state)
    })
    .await
    .map_err(|error| format!("等待后端任务失败: {error}"))??;
    let url = format!("http://127.0.0.1:{port}{path}");
    tauri::async_runtime::spawn_blocking(move || proxy_request(&method, &url, body.as_deref()))
        .await
        .map_err(|error| format!("后端请求任务失败: {error}"))?
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    log_line("[cockpit] App starting");
    let application = tauri::Builder::default()
        .plugin(tauri_plugin_clipboard_manager::init())
        .plugin(tauri_plugin_shell::init())
        .manage(BackendState::default())
        .setup(|app| {
            let handle = app.handle().clone();
            tauri::async_runtime::spawn(start_backend(handle));
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![api_call])
        .build(tauri::generate_context!())
        .expect("error while building Tauri application");

    application.run(|app, event| match event {
        RunEvent::ExitRequested { code, api, .. } => {
            handle_exit_requested(app, code, &api);
        }
        RunEvent::Exit => initiate_final_backend_shutdown(app),
        _ => {}
    });
}

#[cfg(test)]
mod tests {
    use std::io::{Read, Write};
    use std::net::TcpListener;

    use super::{
        error_detail, parse_control_line, parse_port_line, prepare_app_exit, request_shutdown,
        wait_for_backend, BackendState, ExitAction,
    };

    #[test]
    fn parses_only_valid_port_protocol_lines() {
        assert_eq!(parse_port_line("PORT=8845"), Some(8845));
        assert_eq!(parse_port_line("PORT=0"), Some(0));
        assert_eq!(parse_port_line("port=8844"), None);
        assert_eq!(parse_port_line("PORT=70000"), None);
    }

    #[test]
    fn parses_only_non_empty_control_protocol_lines() {
        assert_eq!(
            parse_control_line("CONTROL=secret-token"),
            Some("secret-token".to_owned())
        );
        assert_eq!(parse_control_line("CONTROL="), None);
        assert_eq!(parse_control_line("control=secret-token"), None);
    }

    #[test]
    fn extracts_backend_detail_from_error_response() {
        assert_eq!(
            error_detail(
                400,
                r#"{"detail":"Codex Cockpit Lite 只支持 ChatGPT 登录"}"#
            ),
            "Codex Cockpit Lite 只支持 ChatGPT 登录"
        );
        assert_eq!(error_detail(404, "missing"), "HTTP 404: missing");
    }

    #[test]
    fn readiness_returns_port_or_start_error() {
        let ready = BackendState::default();
        {
            let mut inner = ready.inner.lock().unwrap();
            inner.port = Some(8850);
            inner.control_token = Some("secret-token".to_owned());
        }
        assert_eq!(wait_for_backend(&ready), Ok(8850));

        let failed = BackendState::default();
        failed.inner.lock().unwrap().error = Some("spawn failed".to_owned());
        assert_eq!(wait_for_backend(&failed), Err("spawn failed".to_owned()));
    }

    #[test]
    fn readiness_waits_for_both_port_and_control_token() {
        let missing_control = BackendState::default();
        {
            let mut inner = missing_control.inner.lock().unwrap();
            inner.port = Some(8850);
            inner.terminated = true;
        }
        assert_eq!(
            wait_for_backend(&missing_control),
            Err("后端已退出，未能提供服务".to_owned())
        );
    }

    #[test]
    fn graceful_shutdown_sends_control_token_to_hidden_endpoint() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let server = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut buffer = [0_u8; 2048];
            let length = stream.read(&mut buffer).unwrap();
            let request = String::from_utf8_lossy(&buffer[..length]);
            assert!(request.starts_with("POST /api/cockpit/shutdown HTTP/1.1\r\n"));
            assert!(request
                .to_ascii_lowercase()
                .contains("x-codex-cockpit-control: secret-token\r\n"));
            stream
                .write_all(b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\n\r\n")
                .unwrap();
        });

        assert_eq!(request_shutdown(port, "secret-token"), Ok(()));
        server.join().unwrap();
    }

    #[test]
    fn first_exit_request_starts_cleanup_and_repeated_requests_wait() {
        let state = BackendState::default();

        assert_eq!(prepare_app_exit(&state), ExitAction::StartCleanup);
        assert_eq!(prepare_app_exit(&state), ExitAction::WaitForCleanup);
    }

    #[test]
    fn completed_cleanup_allows_tauri_to_exit() {
        let state = BackendState::default();
        {
            let mut inner = state.inner.lock().unwrap();
            inner.exit_cleanup_started = true;
            inner.exit_cleanup_finished = true;
        }

        assert_eq!(prepare_app_exit(&state), ExitAction::AllowExit);
    }

    #[test]
    fn failed_backend_start_allows_tauri_to_exit_without_cleanup() {
        let state = BackendState::default();
        state.inner.lock().unwrap().error = Some("spawn failed".to_owned());

        assert_eq!(prepare_app_exit(&state), ExitAction::AllowExit);
    }

    #[test]
    fn window_disables_native_drag_drop_for_html5_account_sorting() {
        let config: serde_json::Value =
            serde_json::from_str(include_str!("../tauri.conf.json")).unwrap();
        let drag_drop_enabled = config["app"]["windows"][0]["dragDropEnabled"].as_bool();

        assert_eq!(drag_drop_enabled, Some(false));
    }
}
