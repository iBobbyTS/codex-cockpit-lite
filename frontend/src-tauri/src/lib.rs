use serde::{Deserialize, Serialize};
use std::fs;
use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Mutex;
use tauri::State;

// ─── Config types (mirrors Python backend models) ───

#[derive(Debug, Serialize, Deserialize, Clone)]
struct AppConfig {
    version: u32,
    api: ApiConfig,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
struct ApiConfig {
    port: u16,
    bind_host: String,
    speed: String,
    selected_accounts: Vec<String>,
    auto_switch: AutoSwitchConfig,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
struct AutoSwitchConfig {
    enabled: bool,
    strategy: String,
    quota_threshold_percent: u32,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
struct AccountMeta {
    id: String,
    name: String,
    email: String,
    auth_mode: String,
    plan_type: String,
    subscription_expires_at: Option<i64>,
    team_name: String,
    quota: QuotaSnapshot,
    enabled: bool,
    speed: String,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
struct QuotaSnapshot {
    weekly_percent: u32,
    hourly_percent: u32,
    weekly_resets_at: Option<i64>,
    hourly_resets_at: Option<i64>,
    queried_at: i64,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
struct CockpitStatus {
    running: bool,
    version: String,
    uptime_seconds: f64,
    actual_port: u16,
    active_account_index: usize,
    active_account_id: String,
    active_account_email: String,
    total_requests: u64,
    accounts: Vec<AccountMeta>,
    recent_requests: Vec<ProxyRequestLog>,
    backend_error: Option<String>,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
struct ProxyRequestLog {
    id: String,
    timestamp: f64,
    account_id: String,
    account_email: String,
    method: String,
    path: String,
    model: String,
    status: u16,
    duration_ms: u64,
    error: Option<String>,
}

// ─── Backend process manager ───

struct BackendProcess(Mutex<Option<Child>>);

fn get_config_dir() -> PathBuf {
    std::env::var("CODEX_COCKPIT_HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|_| {
            dirs_next::home_dir()
                .unwrap_or_else(|| PathBuf::from("."))
                .join(".config")
                .join("codex-cockpit")
        })
}

fn get_config_path() -> PathBuf {
    get_config_dir().join("config.json")
}

fn ensure_config_dir() -> PathBuf {
    let dir = get_config_dir();
    fs::create_dir_all(dir.join("accounts")).ok();
    dir
}

// ─── Tauri commands ───

#[tauri::command]
fn get_config() -> Result<AppConfig, String> {
    ensure_config_dir();
    let path = get_config_path();
    if !path.exists() {
        let default = AppConfig {
            version: 1,
            api: ApiConfig {
                port: 8844,
                bind_host: "127.0.0.1".into(),
                speed: "standard".into(),
                selected_accounts: vec![],
                auto_switch: AutoSwitchConfig {
                    enabled: true,
                    strategy: "sequential".into(),
                    quota_threshold_percent: 95,
                },
            },
        };
        let json = serde_json::to_string_pretty(&default).map_err(|e| e.to_string())?;
        fs::write(&path, json).map_err(|e| e.to_string())?;
        return Ok(default);
    }
    let content = fs::read_to_string(&path).map_err(|e| e.to_string())?;
    serde_json::from_str(&content).map_err(|e| e.to_string())
}

#[tauri::command]
fn save_config(config: AppConfig) -> Result<(), String> {
    let path = get_config_path();
    let json = serde_json::to_string_pretty(&config).map_err(|e| e.to_string())?;
    let tmp = path.with_extension("tmp");
    fs::write(&tmp, &json).map_err(|e| e.to_string())?;
    fs::rename(&tmp, &path).map_err(|e| e.to_string())
}

#[tauri::command]
fn import_account(auth_json: String, name: String) -> Result<AccountMeta, String> {
    let id = uuid::Uuid::new_v4().to_string();
    let accounts_dir = ensure_config_dir().join("accounts").join(&id);
    fs::create_dir_all(&accounts_dir).map_err(|e| e.to_string())?;

    // Validate JSON
    let _: serde_json::Value =
        serde_json::from_str(&auth_json).map_err(|e| format!("Invalid auth.json: {}", e))?;

    // Write auth.json
    fs::write(accounts_dir.join("auth.json"), &auth_json).map_err(|e| e.to_string())?;

    // Extract email from tokens
    let auth: serde_json::Value = serde_json::from_str(&auth_json).unwrap_or_default();
    let email = extract_email(&auth);

    let meta = AccountMeta {
        id: id.clone(),
        name,
        email,
        auth_mode: detect_auth_mode(&auth),
        plan_type: String::new(),
        subscription_expires_at: None,
        team_name: String::new(),
        quota: QuotaSnapshot {
            weekly_percent: 0,
            hourly_percent: 0,
            weekly_resets_at: None,
            hourly_resets_at: None,
            queried_at: 0,
        },
        enabled: true,
        speed: "standard".into(),
    };

    let meta_json =
        serde_json::to_string_pretty(&meta).map_err(|e| e.to_string())?;
    fs::write(accounts_dir.join("meta.json"), &meta_json).map_err(|e| e.to_string())?;

    // Auto-add to selected accounts
    let mut config = get_config()?;
    if !config.api.selected_accounts.contains(&id) {
        config.api.selected_accounts.push(id.clone());
        save_config(config)?;
    }

    Ok(meta)
}

#[tauri::command]
fn refresh_account(account_id: String) -> Result<AccountMeta, String> {
    let python = std::env::var("CODEX_BACKEND_PYTHON")
        .unwrap_or_else(|_| "python3".into());
    let backend_dir = std::env::current_dir()
        .unwrap_or_default()
        .parent()
        .map(|p| p.join("backend"))
        .unwrap_or_default();
    let config_dir = get_config_dir();

    let output = Command::new(&python)
        .arg(backend_dir.join("quota_cli.py"))
        .arg(&account_id)
        .arg(config_dir.to_string_lossy().to_string())
        .output()
        .map_err(|e| format!("配额刷新失败: {}", e))?;

    if !output.status.success() {
        return Err(format!("配额刷新进程异常: {}", String::from_utf8_lossy(&output.stderr)));
    }

    let stdout = String::from_utf8_lossy(&output.stdout);
    let result: serde_json::Value = serde_json::from_str(&stdout)
        .map_err(|e| format!("解析配额结果失败: {}: {}", e, stdout))?;

    if !result.get("ok").and_then(|v| v.as_bool()).unwrap_or(false) {
        return Err(result.get("error").and_then(|v| v.as_str()).unwrap_or("未知错误").into());
    }

    // Read updated meta
    let meta_path = ensure_config_dir().join("accounts").join(&account_id).join("meta.json");
    let content = fs::read_to_string(&meta_path).map_err(|e| format!("读取meta失败: {}", e))?;
    serde_json::from_str(&content).map_err(|e| e.to_string())
}

#[tauri::command]
fn import_from_official_codex() -> Result<AccountMeta, String> {
    let home = dirs_next::home_dir().ok_or("Cannot find home directory")?;
    let auth_path = home.join(".codex").join("auth.json");
    if !auth_path.exists() {
        return Err("~/.codex/auth.json not found".into());
    }
    let auth_json = fs::read_to_string(&auth_path).map_err(|e| e.to_string())?;
    let auth: serde_json::Value =
        serde_json::from_str(&auth_json).map_err(|e| format!("Invalid auth.json: {}", e))?;
    let email = extract_email(&auth);
    let name = email.split('@').next().unwrap_or("Codex Account").to_string();
    import_account(auth_json, name)
}

#[tauri::command]
fn delete_account(account_id: String) -> Result<(), String> {
    let accounts_dir = ensure_config_dir().join("accounts").join(&account_id);
    if !accounts_dir.exists() {
        return Err(format!("账号 {} 不存在或已被删除", account_id));
    }
    fs::remove_dir_all(&accounts_dir).map_err(|e| format!("删除账号目录失败: {}", e))?;
    let mut config = get_config()?;
    config.api.selected_accounts.retain(|id| id != &account_id);
    save_config(config)
}

#[tauri::command]
fn toggle_account(account_id: String, enabled: bool) -> Result<(), String> {
    let mut config = get_config()?;
    if enabled {
        if !config.api.selected_accounts.contains(&account_id) {
            config.api.selected_accounts.push(account_id);
        }
    } else {
        config.api.selected_accounts.retain(|id| id != &account_id);
    }
    save_config(config)
}

#[tauri::command]
fn reorder_accounts(account_ids: Vec<String>) -> Result<(), String> {
    let mut config = get_config()?;
    config.api.selected_accounts = account_ids;
    save_config(config)
}

#[tauri::command]
fn list_accounts() -> Result<Vec<AccountMeta>, String> {
    let accounts_dir = ensure_config_dir().join("accounts");
    if !accounts_dir.exists() {
        return Ok(vec![]);
    }
    let mut metas = Vec::new();
    if let Ok(entries) = fs::read_dir(&accounts_dir) {
        for entry in entries.flatten() {
            let meta_path = entry.path().join("meta.json");
            if meta_path.exists() {
                if let Ok(content) = fs::read_to_string(&meta_path) {
                    if let Ok(meta) = serde_json::from_str::<AccountMeta>(&content) {
                        metas.push(meta);
                    }
                }
            }
        }
    }
    metas.sort_by(|a, b| a.id.cmp(&b.id));
    Ok(metas)
}

#[tauri::command]
fn start_backend(state: State<BackendProcess>) -> Result<(), String> {
    let mut proc = state.0.lock().map_err(|e| e.to_string())?;
    if proc.is_some() {
        return Ok(()); // Already running
    }

    let config = get_config()?;
    if config.api.selected_accounts.is_empty() {
        return Err("NO_ACCOUNTS".into());
    }

    let python = std::env::var("CODEX_BACKEND_PYTHON")
        .unwrap_or_else(|_| "python3".into());
    let backend_dir = std::env::current_dir()
        .unwrap_or_default()
        .parent()
        .map(|p| p.join("backend"))
        .unwrap_or_default();

    let config_dir = get_config_dir();
    let port = config.api.port.to_string();

    let child = Command::new(&python)
        .arg(backend_dir.join("main.py"))
        .arg("--config-dir")
        .arg(config_dir.to_string_lossy().to_string())
        .arg("--port")
        .arg(&port)
        .spawn()
        .map_err(|e| format!("Failed to start backend: {}", e))?;

    *proc = Some(child);
    Ok(())
}

#[tauri::command]
fn stop_backend(state: State<BackendProcess>) -> Result<(), String> {
    let mut proc = state.0.lock().map_err(|e| e.to_string())?;
    if let Some(mut child) = proc.take() {
        child.kill().map_err(|e| e.to_string())?;
        child.wait().map_err(|e| e.to_string())?;
    }
    Ok(())
}

#[tauri::command]
fn get_config_dir_path() -> String {
    get_config_dir().to_string_lossy().to_string()
}

// ─── Helpers ───

fn extract_email(auth: &serde_json::Value) -> String {
    if let Some(tokens) = auth.get("tokens") {
        if let Some(id_token) = tokens.get("id_token").and_then(|v| v.as_str()) {
            // Decode JWT without verification
            if let Some(payload) = decode_jwt_payload(id_token) {
                if let Some(email) = payload.get("email").and_then(|v| v.as_str()) {
                    return email.to_string();
                }
            }
        }
    }
    if let Some(identity) = auth.get("agent_identity") {
        if let Some(email) = identity.get("email").and_then(|v| v.as_str()) {
            return email.to_string();
        }
    }
    String::new()
}

fn detect_auth_mode(auth: &serde_json::Value) -> String {
    if auth.get("agent_identity").is_some() {
        return "agent_identity".into();
    }
    if auth.get("auth_mode").and_then(|v| v.as_str()) == Some("apikey") {
        return "apikey".into();
    }
    if auth.get("tokens").is_some() {
        return "oauth".into();
    }
    if auth.get("OPENAI_API_KEY").is_some() {
        return "apikey".into();
    }
    "oauth".into()
}

fn decode_jwt_payload(token: &str) -> Option<serde_json::Value> {
    let parts: Vec<&str> = token.split('.').collect();
    if parts.len() < 2 {
        return None;
    }
    use base64::Engine;
    let decoded = base64::engine::general_purpose::URL_SAFE_NO_PAD
        .decode(parts[1])
        .ok()?;
    let payload: serde_json::Value = serde_json::from_slice(&decoded).ok()?;
    Some(payload)
}

pub fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(BackendProcess(Mutex::new(None)))
        .invoke_handler(tauri::generate_handler![
            get_config,
            save_config,
            list_accounts,
            refresh_account,
            import_account,
            import_from_official_codex,
            delete_account,
            toggle_account,
            reorder_accounts,
            start_backend,
            stop_backend,
            get_config_dir_path,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
