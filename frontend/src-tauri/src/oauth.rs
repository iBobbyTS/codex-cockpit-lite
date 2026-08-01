use std::io::{ErrorKind, Read, Write};
use std::net::TcpListener;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use rand::RngCore;
use sha2::{Digest, Sha256};
use tauri::{AppHandle, Manager, WebviewUrl, WebviewWindowBuilder, WindowEvent};
use url::Url;

const CLIENT_ID: &str = "app_EMoamEEZ73f0CkXaXp7hrann";
const AUTH_ENDPOINT: &str = "https://auth.openai.com/oauth/authorize";
const TOKEN_ENDPOINT: &str = "https://auth.openai.com/oauth/token";
const SCOPES: &str =
    "openid profile email offline_access api.connectors.read api.connectors.invoke";
const ORIGINATOR: &str = "codex_vscode";
const CALLBACK_PORT: u16 = 1455;
const CALLBACK_PATH: &str = "/auth/callback";
const WINDOW_LABEL: &str = "codex-oauth-incognito";
const LOGIN_TIMEOUT: Duration = Duration::from_secs(300);

static LOGIN_ACTIVE: AtomicBool = AtomicBool::new(false);

struct ActiveLoginGuard;

impl ActiveLoginGuard {
    fn acquire() -> Result<Self, String> {
        LOGIN_ACTIVE
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .map(|_| Self)
            .map_err(|_| "已有浏览器登录正在进行".to_owned())
    }
}

impl Drop for ActiveLoginGuard {
    fn drop(&mut self) {
        LOGIN_ACTIVE.store(false, Ordering::Release);
    }
}

fn random_base64url() -> String {
    let mut bytes = [0_u8; 32];
    rand::thread_rng().fill_bytes(&mut bytes);
    URL_SAFE_NO_PAD.encode(bytes)
}

fn code_challenge(verifier: &str) -> String {
    URL_SAFE_NO_PAD.encode(Sha256::digest(verifier.as_bytes()))
}

fn authorization_url(redirect_uri: &str, challenge: &str, state: &str) -> Result<Url, String> {
    let mut url = Url::parse(AUTH_ENDPOINT).map_err(|error| error.to_string())?;
    url.query_pairs_mut()
        .append_pair("response_type", "code")
        .append_pair("client_id", CLIENT_ID)
        .append_pair("redirect_uri", redirect_uri)
        .append_pair("scope", SCOPES)
        .append_pair("code_challenge", challenge)
        .append_pair("code_challenge_method", "S256")
        .append_pair("id_token_add_organizations", "true")
        .append_pair("codex_cli_simplified_flow", "true")
        .append_pair("state", state)
        .append_pair("originator", ORIGINATOR);
    Ok(url)
}

fn callback_code(target: &str, expected_state: &str) -> Result<String, String> {
    let url = Url::parse(&format!("http://localhost:{CALLBACK_PORT}{target}"))
        .map_err(|_| "OAuth 回调地址无效".to_owned())?;
    if url.path() != CALLBACK_PATH {
        return Err("OAuth 回调路径无效".to_owned());
    }
    let params = url
        .query_pairs()
        .collect::<std::collections::HashMap<_, _>>();
    if let Some(error) = params.get("error") {
        return Err(format!("浏览器登录被拒绝: {error}"));
    }
    let state = params
        .get("state")
        .filter(|value| !value.is_empty())
        .ok_or_else(|| "OAuth 回调缺少 state".to_owned())?;
    if state.as_ref() != expected_state {
        return Err("OAuth 回调 state 校验失败".to_owned());
    }
    params
        .get("code")
        .filter(|value| !value.is_empty())
        .map(|value| value.to_string())
        .ok_or_else(|| "OAuth 回调缺少 code".to_owned())
}

fn write_callback_response(mut stream: std::net::TcpStream, success: bool) {
    let (status, title, detail) = if success {
        ("200 OK", "授权成功", "正在返回 Codex Cockpit Lite…")
    } else {
        ("400 Bad Request", "授权失败", "请返回应用后重试。")
    };
    let body = format!(
        "<!doctype html><meta charset=\"utf-8\"><title>{title}</title><style>body{{font-family:-apple-system,sans-serif;display:grid;place-items:center;height:100vh;margin:0;background:#111827;color:#f9fafb}}main{{text-align:center}}p{{color:#9ca3af}}</style><main><h1>{title}</h1><p>{detail}</p></main>"
    );
    let response = format!(
        "HTTP/1.1 {status}\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
        body.len()
    );
    let _ = stream.write_all(response.as_bytes());
    let _ = stream.flush();
}

fn wait_for_callback(
    listener: TcpListener,
    expected_state: String,
    cancelled: Arc<AtomicBool>,
) -> Result<String, String> {
    listener
        .set_nonblocking(true)
        .map_err(|error| format!("配置 OAuth 回调监听失败: {error}"))?;
    let started = Instant::now();
    while started.elapsed() < LOGIN_TIMEOUT {
        if cancelled.load(Ordering::Acquire) {
            return Err("浏览器登录已取消".to_owned());
        }
        match listener.accept() {
            Ok((mut stream, _)) => {
                let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
                let mut bytes = [0_u8; 16 * 1024];
                let read = stream
                    .read(&mut bytes)
                    .map_err(|error| format!("读取 OAuth 回调失败: {error}"))?;
                let first_line = String::from_utf8_lossy(&bytes[..read])
                    .lines()
                    .next()
                    .unwrap_or_default()
                    .to_owned();
                let target = first_line
                    .strip_prefix("GET ")
                    .and_then(|value| value.split_once(' '))
                    .map(|(value, _)| value)
                    .ok_or_else(|| "OAuth 回调请求格式无效".to_owned())?;
                let result = callback_code(target, &expected_state);
                write_callback_response(stream, result.is_ok());
                return result;
            }
            Err(error) if error.kind() == ErrorKind::WouldBlock => {
                std::thread::sleep(Duration::from_millis(100));
            }
            Err(error) => return Err(format!("接收 OAuth 回调失败: {error}")),
        }
    }
    Err("浏览器登录已超时，请重试".to_owned())
}

fn exchange_code(
    code: &str,
    verifier: &str,
    redirect_uri: &str,
) -> Result<serde_json::Value, String> {
    let body = url::form_urlencoded::Serializer::new(String::new())
        .append_pair("grant_type", "authorization_code")
        .append_pair("code", code)
        .append_pair("redirect_uri", redirect_uri)
        .append_pair("client_id", CLIENT_ID)
        .append_pair("code_verifier", verifier)
        .finish();
    let agent = ureq::Agent::config_builder()
        .http_status_as_error(false)
        .timeout_global(Some(Duration::from_secs(25)))
        .build()
        .new_agent();
    let mut response = agent
        .post(TOKEN_ENDPOINT)
        .content_type("application/x-www-form-urlencoded")
        .send(body)
        .map_err(|error| format!("OAuth Token 请求失败: {error}"))?;
    let status = response.status();
    let response_body = response
        .body_mut()
        .read_to_string()
        .map_err(|error| format!("读取 OAuth Token 响应失败: {error}"))?;
    let data: serde_json::Value = serde_json::from_str(&response_body)
        .map_err(|error| format!("OAuth Token 响应无效: {error}"))?;
    if !status.is_success() {
        return Err(format!("OAuth Token 交换失败: HTTP {status}"));
    }
    for field in ["id_token", "access_token", "refresh_token"] {
        if !data
            .get(field)
            .and_then(serde_json::Value::as_str)
            .is_some_and(|v| !v.is_empty())
        {
            return Err(format!("OAuth Token 响应缺少 {field}"));
        }
    }
    Ok(serde_json::json!({
        "auth_mode": "chatgpt",
        "tokens": {
            "id_token": data["id_token"],
            "access_token": data["access_token"],
            "refresh_token": data["refresh_token"],
        }
    }))
}

pub async fn login(app: AppHandle) -> Result<serde_json::Value, String> {
    let _active = ActiveLoginGuard::acquire()?;
    let listener = TcpListener::bind(("127.0.0.1", CALLBACK_PORT)).map_err(|error| {
        if error.kind() == ErrorKind::AddrInUse {
            format!("OAuth 回调端口 {CALLBACK_PORT} 已被占用")
        } else {
            format!("无法启动 OAuth 回调监听: {error}")
        }
    })?;
    let verifier = random_base64url();
    let state = random_base64url();
    let redirect_uri = format!("http://localhost:{CALLBACK_PORT}{CALLBACK_PATH}");
    let auth_url = authorization_url(&redirect_uri, &code_challenge(&verifier), &state)?;
    if let Some(window) = app.get_webview_window(WINDOW_LABEL) {
        let _ = window.destroy();
    }
    let cancelled = Arc::new(AtomicBool::new(false));
    let cancelled_for_window = Arc::clone(&cancelled);
    let window = WebviewWindowBuilder::new(&app, WINDOW_LABEL, WebviewUrl::External(auth_url))
        .title("ChatGPT 登录")
        .inner_size(920.0, 720.0)
        .min_inner_size(640.0, 560.0)
        .center()
        .incognito(true)
        .on_navigation(|url| {
            matches!(url.scheme(), "https" | "about")
                || (url.scheme() == "http"
                    && url.host_str() == Some("localhost")
                    && url.port() == Some(CALLBACK_PORT)
                    && url.path() == CALLBACK_PATH)
        })
        .build()
        .map_err(|error| format!("打开 ChatGPT 登录窗口失败: {error}"))?;
    window.on_window_event(move |event| {
        if matches!(
            event,
            WindowEvent::CloseRequested { .. } | WindowEvent::Destroyed
        ) {
            cancelled_for_window.store(true, Ordering::Release);
        }
    });

    let callback_state = state.clone();
    let callback_result = tauri::async_runtime::spawn_blocking(move || {
        wait_for_callback(listener, callback_state, cancelled)
    })
    .await
    .map_err(|error| format!("OAuth 回调任务失败: {error}"))?;
    if let Some(window) = app.get_webview_window(WINDOW_LABEL) {
        let _ = window.destroy();
    }
    let code = callback_result?;
    tauri::async_runtime::spawn_blocking(move || exchange_code(&code, &verifier, &redirect_uri))
        .await
        .map_err(|error| format!("OAuth Token 交换任务失败: {error}"))?
}

#[cfg(test)]
mod tests {
    use super::{authorization_url, callback_code, code_challenge};

    #[test]
    fn creates_pkce_s256_challenge() {
        assert_eq!(
            code_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"),
            "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
        );
    }

    #[test]
    fn authorization_url_contains_required_security_fields() {
        let url = authorization_url(
            "http://localhost:1455/auth/callback",
            "challenge",
            "state-1",
        )
        .unwrap();
        let query = url
            .query_pairs()
            .collect::<std::collections::HashMap<_, _>>();
        assert!(url
            .as_str()
            .starts_with("https://auth.openai.com/oauth/authorize?"));
        assert_eq!(query.get("state").map(|v| v.as_ref()), Some("state-1"));
        assert_eq!(
            query.get("code_challenge").map(|v| v.as_ref()),
            Some("challenge")
        );
        assert_eq!(
            query.get("code_challenge_method").map(|v| v.as_ref()),
            Some("S256")
        );
    }

    #[test]
    fn callback_requires_matching_state_and_code() {
        assert_eq!(
            callback_code("/auth/callback?code=code-1&state=state-1", "state-1"),
            Ok("code-1".to_owned())
        );
        assert!(callback_code("/auth/callback?code=code-1&state=other", "state-1").is_err());
        assert!(callback_code("/auth/callback?state=state-1", "state-1").is_err());
    }
}
