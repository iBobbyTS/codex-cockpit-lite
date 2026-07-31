use std::collections::VecDeque;
use std::fs::OpenOptions;
use std::io::Write;
use std::path::PathBuf;
use std::sync::{Condvar, Mutex};
use std::time::{Duration, Instant};

use tauri::{AppHandle, Manager, RunEvent};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

const BACKEND_READY_TIMEOUT: Duration = Duration::from_secs(15);
const BACKEND_STOP_TIMEOUT: Duration = Duration::from_secs(3);
const STDERR_LINES: usize = 20;

#[derive(Default)]
struct BackendInner {
    port: Option<u16>,
    error: Option<String>,
    child: Option<CommandChild>,
    terminated: bool,
    stopping: bool,
    stderr_tail: VecDeque<String>,
}

#[derive(Default)]
struct BackendState {
    inner: Mutex<BackendInner>,
    terminated: Condvar,
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

    {
        let state = app.state::<BackendState>();
        let mut inner = state
            .inner
            .lock()
            .unwrap_or_else(|poison| poison.into_inner());
        inner.child = Some(child);
        inner.terminated = false;
        inner.stopping = false;
        inner.error = None;
    }

    while let Some(event) = events.recv().await {
        match event {
            CommandEvent::Stdout(bytes) => {
                let line = String::from_utf8_lossy(&bytes);
                if let Some(port) = parse_port_line(line.trim()) {
                    let state = app.state::<BackendState>();
                    let mut inner = state
                        .inner
                        .lock()
                        .unwrap_or_else(|poison| poison.into_inner());
                    inner.port = Some(port);
                    inner.error = None;
                    log_line(&format!("[cockpit] Backend ready on port {port}"));
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
                state.terminated.notify_all();
            }
            _ => {}
        }
    }
}

fn stop_backend(app: &AppHandle) {
    let state = app.state::<BackendState>();
    let child = {
        let mut inner = state
            .inner
            .lock()
            .unwrap_or_else(|poison| poison.into_inner());
        inner.stopping = true;
        inner.child.take()
    };

    if let Some(child) = child {
        log_line("[cockpit] Stopping backend sidecar");
        if let Err(error) = child.kill() {
            log_line(&format!(
                "[cockpit] Failed to kill backend sidecar: {error}"
            ));
            return;
        }
        let inner = state
            .inner
            .lock()
            .unwrap_or_else(|poison| poison.into_inner());
        let _ = state
            .terminated
            .wait_timeout_while(inner, BACKEND_STOP_TIMEOUT, |value| !value.terminated);
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
            if let Some(port) = inner.port {
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
        .plugin(tauri_plugin_shell::init())
        .manage(BackendState::default())
        .setup(|app| {
            let handle = app.handle().clone();
            tauri::async_runtime::spawn(start_backend(handle));
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![api_call])
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::Destroyed = event {
                stop_backend(window.app_handle());
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building Tauri application");

    application.run(|app, event| {
        if matches!(event, RunEvent::Exit | RunEvent::ExitRequested { .. }) {
            stop_backend(app);
        }
    });
}

#[cfg(test)]
mod tests {
    use super::{error_detail, parse_port_line, wait_for_backend, BackendState};

    #[test]
    fn parses_only_valid_port_protocol_lines() {
        assert_eq!(parse_port_line("PORT=8845"), Some(8845));
        assert_eq!(parse_port_line("PORT=0"), Some(0));
        assert_eq!(parse_port_line("port=8844"), None);
        assert_eq!(parse_port_line("PORT=70000"), None);
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
        ready.inner.lock().unwrap().port = Some(8850);
        assert_eq!(wait_for_backend(&ready), Ok(8850));

        let failed = BackendState::default();
        failed.inner.lock().unwrap().error = Some("spawn failed".to_owned());
        assert_eq!(wait_for_backend(&failed), Err("spawn failed".to_owned()));
    }
}
