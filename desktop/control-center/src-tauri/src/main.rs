#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
use chrono::Utc;
use rand::RngCore;
use reqwest::Client;
use serde::{Deserialize, Serialize};
use std::{
    collections::HashMap,
    path::{Path, PathBuf},
    process::Stdio,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
    time::{Duration, Instant},
};
use sysinfo::System;
use tauri::{Emitter, State};
use tokio::{
    io::{AsyncBufReadExt, AsyncRead, AsyncReadExt, AsyncWriteExt, BufReader},
    process::{Child, Command},
    sync::{oneshot, Mutex},
};

#[derive(Clone, Serialize, Deserialize, Debug, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
enum StateKind {
    Stopped,
    Starting,
    Running,
    Ready,
    Degraded,
    Stopping,
    Failed,
    Unknown,
    ExternallyRunning,
    NotConfigured,
    Unsupported,
}
#[derive(Clone, Serialize)]
struct Component {
    id: String,
    name: String,
    kind: String,
    state: StateKind,
    pid: Option<u32>,
    health: String,
    started_at: Option<String>,
    last_error: Option<String>,
    log: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    owner_mode: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    session_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    lifecycle_state: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    api_url: Option<String>,
}
#[derive(Clone, Serialize, Deserialize)]
struct PythonSession {
    mode: String,
    pid: u32,
    session_id: String,
    started_at: String,
    state: StateKind,
    api_url: Option<String>,
}
#[derive(Clone, Serialize)]
struct RuntimeProfileInfo {
    id: String,
    name: String,
    mode: String,
    debug: bool,
    verbose: bool,
}
#[derive(Clone, Serialize)]
struct RuntimeSessionInfo {
    profile_id: String,
    profile_name: String,
    mode: String,
    debug: bool,
    verbose: bool,
    session_id: String,
    pid: u32,
    state: StateKind,
    started_at: String,
    api_url: Option<String>,
}
#[derive(Clone, Copy)]
struct RuntimeProfile {
    id: &'static str,
    name: &'static str,
    mode: &'static str,
    debug: bool,
    verbose: bool,
}
const RUNTIME_PROFILES: &[RuntimeProfile] = &[
    RuntimeProfile {
        id: "cli",
        name: "TOM CLI",
        mode: "cli",
        debug: false,
        verbose: false,
    },
    RuntimeProfile {
        id: "cli_debug",
        name: "TOM CLI - Debug",
        mode: "cli",
        debug: true,
        verbose: false,
    },
    RuntimeProfile {
        id: "cli_verbose",
        name: "TOM CLI - Verbose",
        mode: "cli",
        debug: false,
        verbose: true,
    },
    RuntimeProfile {
        id: "voice",
        name: "TOM Voice",
        mode: "voice",
        debug: false,
        verbose: false,
    },
    RuntimeProfile {
        id: "voice_debug",
        name: "TOM Voice - Debug",
        mode: "voice",
        debug: true,
        verbose: false,
    },
    RuntimeProfile {
        id: "voice_verbose",
        name: "TOM Voice - Verbose",
        mode: "voice",
        debug: false,
        verbose: true,
    },
    RuntimeProfile {
        id: "service",
        name: "TOM Service",
        mode: "service",
        debug: false,
        verbose: false,
    },
];
fn runtime_profile(id: &str) -> Option<&'static RuntimeProfile> {
    RUNTIME_PROFILES.iter().find(|profile| profile.id == id)
}
fn runtime_profiles() -> Vec<RuntimeProfileInfo> {
    RUNTIME_PROFILES
        .iter()
        .map(|profile| RuntimeProfileInfo {
            id: profile.id.into(),
            name: profile.name.into(),
            mode: profile.mode.into(),
            debug: profile.debug,
            verbose: profile.verbose,
        })
        .collect()
}
fn runtime_key(profile_id: &str) -> String {
    format!("runtime:{profile_id}")
}
fn key_profile(key: &str) -> Option<&str> {
    key.strip_prefix("runtime:")
}
fn profile_name(profile_id: &str) -> String {
    runtime_profile(profile_id)
        .map(|profile| profile.name.to_string())
        .unwrap_or_else(|| profile_id.to_string())
}
fn session_info(session: &PythonSession) -> RuntimeSessionInfo {
    let profile = runtime_profile(&session.mode);
    RuntimeSessionInfo {
        profile_id: session.mode.clone(),
        profile_name: profile_name(&session.mode),
        mode: profile.map(|value| value.mode).unwrap_or("unknown").into(),
        debug: profile.is_some_and(|value| value.debug),
        verbose: profile.is_some_and(|value| value.verbose),
        session_id: session.session_id.clone(),
        pid: session.pid,
        state: session.state.clone(),
        started_at: session.started_at.clone(),
        api_url: session.api_url.clone(),
    }
}
#[derive(Clone, Serialize)]
struct SessionExit {
    mode: String,
    session: PythonSession,
    reason: String,
    at: String,
}
#[derive(Clone, Serialize, Deserialize)]
struct Config {
    version: u32,
    repo: String,
    python: String,
    engine: String,
    llama_server: String,
    model: String,
    ahk_executable: String,
    model_endpoint: String,
    context_size: u32,
    port: u16,
    gpu_layers: i32,
    wheel_script: String,
    wheel_endpoint: String,
}
struct Owned {
    child: Child,
    started: String,
    control: Option<(u16, String)>,
    session: Option<PythonSession>,
}
#[derive(Default)]
struct GpuCache {
    entry: Option<(Instant, serde_json::Value)>,
}
impl GpuCache {
    fn fresh(&self, now: Instant) -> Option<serde_json::Value> {
        self.entry
            .as_ref()
            .filter(|(at, _)| now.duration_since(*at) < GPU_CACHE_TTL)
            .map(|(_, value)| value.clone())
    }
    fn store(&mut self, now: Instant, value: serde_json::Value) {
        self.entry = Some((now, value));
    }
}
struct Supervisor {
    config_file: PathBuf,
    config: Mutex<Config>,
    owned: Mutex<HashMap<String, Owned>>,
    logs: Mutex<Vec<String>>,
    client: Client,
    closing: AtomicBool,
    last_exit: Mutex<Option<SessionExit>>,
    gpu_cache: Mutex<GpuCache>,
    start_lock: Mutex<()>,
    pending_pongs: Mutex<HashMap<String, oneshot::Sender<()>>>,
    wheel_backend_session: Mutex<Option<String>>,
}
type AppState = Arc<Supervisor>;

const GPU_CACHE_TTL: Duration = Duration::from_secs(10);
const PYTHON_READY_DEADLINE: Duration = Duration::from_secs(30);
const PYTHON_STATUS_TIMEOUT: Duration = Duration::from_millis(2500);
const CLI_PING_TIMEOUT: Duration = Duration::from_secs(5);
const WHEEL_BACKEND_DEADLINE: Duration = Duration::from_secs(15);
const CONTROL_PING: &str = "__tom_control_ping__";
const CONTROL_PONG: &str = "__tom_control_pong__";
const CREATE_NO_WINDOW: u32 = 0x0800_0000;
const CREATE_NEW_PROCESS_GROUP: u32 = 0x0000_0200;
const HELPER_CREATION_FLAGS: u32 = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP;

fn apply_no_console(command: &mut Command) {
    #[cfg(windows)]
    command.creation_flags(HELPER_CREATION_FLAGS);
    #[cfg(not(windows))]
    let _ = command;
}
fn new_control_session() -> Result<(u16, String), String> {
    let listener = std::net::TcpListener::bind(("127.0.0.1", 0)).map_err(|e| e.to_string())?;
    let port = listener.local_addr().map_err(|e| e.to_string())?.port();
    drop(listener);
    let token = random_hex(32);
    Ok((port, token))
}
fn random_hex(bytes: usize) -> String {
    let mut buffer = vec![0u8; bytes];
    rand::thread_rng().fill_bytes(&mut buffer);
    buffer.iter().map(|b| format!("{b:02x}")).collect()
}
fn control_environment(port: u16, token: &str) -> [(String, String); 2] {
    [
        ("TOM_CONTROL_PORT".into(), port.to_string()),
        ("TOM_CONTROL_TOKEN".into(), token.to_string()),
    ]
}
fn python_state_from_body(body: &serde_json::Value) -> Result<String, String> {
    if body.get("api_version").and_then(|v| v.as_u64()) != Some(1) {
        return Err("Status response is missing api_version 1".into());
    }
    body.pointer("/data/python/state")
        .and_then(|v| v.as_str())
        .map(str::to_string)
        .ok_or_else(|| "Status response is missing data.python.state".into())
}
async fn probe_python_status(
    client: &Client,
    endpoint: &str,
    token: &str,
) -> Result<String, String> {
    let response = client
        .get(endpoint)
        .bearer_auth(token)
        .timeout(PYTHON_STATUS_TIMEOUT)
        .send()
        .await
        .map_err(|error| format!("request failed: {error}"))?;
    if !response.status().is_success() {
        return Err(format!("HTTP {}", response.status()));
    }
    let body: serde_json::Value = response
        .json()
        .await
        .map_err(|error| format!("invalid JSON: {error}"))?;
    python_state_from_body(&body)
}
fn python_conflict_message(session: &PythonSession) -> String {
    format!(
        "Control Center already owns the {} Python session (PID {}, state {:?}, session {}). Stop it before starting another Python mode.",
        session.mode, session.pid, session.state, session.session_id
    )
}
fn exit_description(status: std::process::ExitStatus) -> String {
    match status.code() {
        Some(code) => format!("exit code {code}"),
        None => "terminated without an exit code".into(),
    }
}
fn reap_exited_locked(owned: &mut HashMap<String, Owned>) -> Vec<SessionExit> {
    let ids: Vec<String> = owned
        .iter()
        .filter(|(_, entry)| entry.session.is_some())
        .map(|(id, _)| id.clone())
        .collect();
    let mut reaped = Vec::new();
    for id in ids {
        let outcome = owned.get_mut(&id).and_then(|entry| {
            let session = entry.session.clone()?;
            match entry.child.try_wait() {
                Ok(Some(status)) => Some((session, exit_description(status))),
                _ => None,
            }
        });
        if let Some((session, reason)) = outcome {
            owned.remove(&id);
            reaped.push(SessionExit {
                mode: session.mode.clone(),
                session,
                reason,
                at: Utc::now().to_rfc3339(),
            });
        }
    }
    reaped
}
fn reap_runtime_sessions_locked(owned: &mut HashMap<String, Owned>) -> Vec<PythonSession> {
    let ids: Vec<String> = owned
        .iter()
        .filter(|(id, entry)| key_profile(id).is_some() && entry.session.is_some())
        .map(|(id, _)| id.clone())
        .collect();
    let mut live = Vec::new();
    for id in ids {
        let exited = owned
            .get_mut(&id)
            .is_some_and(|entry| matches!(entry.child.try_wait(), Ok(Some(_))));
        if exited {
            owned.remove(&id);
        } else if let Some(session) = owned.get(&id).and_then(|entry| entry.session.clone()) {
            live.push(session);
        }
    }
    live
}
fn runtime_sessions_locked(owned: &mut HashMap<String, Owned>) -> Vec<PythonSession> {
    let mut sessions = reap_runtime_sessions_locked(owned);
    sessions.sort_by(|left, right| left.started_at.cmp(&right.started_at));
    sessions
}
fn live_python_session(owned: &mut HashMap<String, Owned>) -> Option<PythonSession> {
    runtime_sessions_locked(owned).into_iter().next()
}
fn live_session_for_profile(owned: &mut HashMap<String, Owned>, profile_id: &str) -> Option<PythonSession> {
    let key = runtime_key(profile_id);
    let exited = owned
        .get_mut(&key)
        .is_some_and(|entry| matches!(entry.child.try_wait(), Ok(Some(_))));
    if exited {
        owned.remove(&key);
        return None;
    }
    owned.get(&key).and_then(|entry| entry.session.clone())
}
fn live_voice_session(owned: &mut HashMap<String, Owned>) -> Option<PythonSession> {
    runtime_sessions_locked(owned)
        .into_iter()
        .find(|session| runtime_profile(&session.mode).is_some_and(|profile| profile.mode == "voice"))
}
fn session_by_id(owned: &mut HashMap<String, Owned>, session_id: &str) -> Option<(String, PythonSession)> {
    let sessions = runtime_sessions_locked(owned);
    sessions
        .into_iter()
        .find(|session| session.session_id == session_id)
        .map(|session| (runtime_key(&session.mode), session))
}
fn session_key_by_id(owned: &mut HashMap<String, Owned>, session_id: &str) -> Option<String> {
    session_by_id(owned, session_id).map(|(key, _)| key)
}
fn has_runtime_sessions(owned: &mut HashMap<String, Owned>) -> bool {
    !runtime_sessions_locked(owned).is_empty()
}

fn default_config(repo: &Path) -> Config {
    Config {
        version: 1,
        repo: repo.to_string_lossy().into(),
        python: repo
            .join(".venv/Scripts/python.exe")
            .to_string_lossy()
            .into(),
        engine: repo
            .join("rust/tom-engine/target/release/tom-engine.exe")
            .to_string_lossy()
            .into(),
        llama_server: "".into(),
        model: String::new(),
        ahk_executable: "C:/Program Files/AutoHotkey/v2/AutoHotkey64.exe".into(),
        model_endpoint: "http://127.0.0.1:65400".into(),
        context_size: 8192,
        port: 65400,
        gpu_layers: 0,
        wheel_script: repo.join("desktop/tom_wheel.ahk").to_string_lossy().into(),
        wheel_endpoint: "http://127.0.0.1:65401/health".into(),
    }
}
fn find_repo() -> PathBuf {
    let cwd = std::env::current_dir().unwrap_or_default();
    for p in cwd.ancestors() {
        if p.join("pyproject.toml").exists() && p.join("rust/tom-engine").exists() {
            return p.to_path_buf();
        }
    }
    cwd
}
impl Supervisor {
    async fn log(&self, app: &tauri::AppHandle, id: &str, msg: String) {
        let line = format!("{} [{}] {}", Utc::now().to_rfc3339(), id, msg);
        let mut l = self.logs.lock().await;
        l.push(line.clone());
        if l.len() > 2000 {
            let excess = l.len() - 2000;
            l.drain(0..excess);
        }
        drop(l);
        let _ = app.emit("tom-log", line);
    }
    async fn reap_exited_python(&self) -> Vec<SessionExit> {
        let mut owned = self.owned.lock().await;
        let reaped = reap_exited_locked(&mut owned);
        drop(owned);
        if let Some(last) = reaped.last() {
            *self.last_exit.lock().await = Some(last.clone());
        }
        reaped
    }
    async fn python_exit(&self, id: &str) -> Option<String> {
        let mut owned = self.owned.lock().await;
        match owned.get_mut(id) {
            Some(entry) => match entry.child.try_wait() {
                Ok(Some(status)) => Some(exit_description(status)),
                Ok(None) => None,
                Err(_) => None,
            },
            None => Some("process ownership was released".into()),
        }
    }
    async fn set_python_state(&self, id: &str, state: StateKind) {
        let mut owned = self.owned.lock().await;
        if let Some(session) = owned.get_mut(id).and_then(|entry| entry.session.as_mut()) {
            session.state = state;
        }
    }
    async fn current_python_control(&self) -> Option<(PythonSession, Option<(u16, String)>)> {
        let mut owned = self.owned.lock().await;
        runtime_sessions_locked(&mut owned)
            .into_iter()
            .find_map(|session| {
                let key = runtime_key(&session.mode);
                let control = owned.get(&key).and_then(|entry| entry.control.clone());
                Some((session, control))
            })
    }
    async fn control_for_session(
        &self,
        session_id: &str,
    ) -> Option<(PythonSession, Option<(u16, String)>)> {
        let mut owned = self.owned.lock().await;
        let (key, session) = session_by_id(&mut owned, session_id)?;
        let control = owned.get(&key).and_then(|entry| entry.control.clone());
        Some((session, control))
    }
    async fn log_tail(&self, id: &str, count: usize) -> String {
        let tail = self.log_lines(id, count).await;
        if tail.is_empty() {
            "No process output was captured.".into()
        } else {
            tail.join(" | ")
        }
    }
    async fn log_lines(&self, id: &str, count: usize) -> Vec<String> {
        let logs = self.logs.lock().await;
        let needle = format!("[{id}]");
        let mut tail: Vec<String> = logs
            .iter()
            .rev()
            .filter(|line| line.contains(&needle))
            .take(count)
            .cloned()
            .collect();
        tail.reverse();
        tail
    }
    async fn cached_gpu_telemetry(&self) -> serde_json::Value {
        let mut cache = self.gpu_cache.lock().await;
        if let Some(value) = cache.fresh(Instant::now()) {
            return value;
        }
        let value = gpu_telemetry().await;
        cache.store(Instant::now(), value.clone());
        value
    }
}
#[cfg(windows)]
async fn engine_request(
    method: &str,
    params: serde_json::Value,
) -> Result<serde_json::Value, String> {
    use tokio::net::windows::named_pipe::ClientOptions;
    let mut pipe = ClientOptions::new()
        .open(r"\\.\pipe\tom-engine")
        .map_err(|e| e.to_string())?;
    let request = serde_json::json!({"id":"control-center-request","version":1,"method":method,"params":params});
    pipe.write_all(format!("{}\n", request).as_bytes())
        .await
        .map_err(|e| e.to_string())?;
    let mut line = Vec::new();
    tokio::time::timeout(Duration::from_secs(2), async {
        loop {
            let mut byte = [0u8; 1];
            pipe.read_exact(&mut byte).await?;
            if byte[0] == b'\n' {
                break;
            }
            line.push(byte[0]);
        }
        Ok::<(), std::io::Error>(())
    })
    .await
    .map_err(|_| "engine ping timed out".to_string())?
    .map_err(|e| e.to_string())?;
    let response: serde_json::Value = serde_json::from_slice(&line).map_err(|e| e.to_string())?;
    if response["success"] == true {
        Ok(response["data"].clone())
    } else {
        Err(response.to_string())
    }
}
#[cfg(not(windows))]
async fn engine_request(
    _method: &str,
    _params: serde_json::Value,
) -> Result<serde_json::Value, String> {
    Err("Named-pipe health is Windows-only".into())
}
async fn engine_probe() -> Result<serde_json::Value, String> {
    let result = engine_request("engine.ping", serde_json::json!({})).await?;
    if result["pong"] == true {
        Ok(result)
    } else {
        Err("Engine ping returned an invalid response".into())
    }
}

#[tauri::command]
async fn get_config(s: State<'_, AppState>) -> Result<Config, String> {
    Ok(s.config.lock().await.clone())
}
#[tauri::command]
async fn save_config(s: State<'_, AppState>, config: Config) -> Result<(), String> {
    if !loopback_http(&config.model_endpoint) || !loopback_http(&config.wheel_endpoint) {
        return Err("Service endpoints must use loopback HTTP".into());
    }
    if config.port == 0 || config.context_size < 512 || config.context_size > 131072 {
        return Err("Invalid model port or context size".into());
    }
    if s.config_file.exists() {
        let prior = std::fs::read(&s.config_file).map_err(|e| e.to_string())?;
        serde_json::from_slice::<Config>(&prior).map_err(|_|"Existing Control Center configuration is invalid; it was preserved. Repair or move it manually before saving.".to_string())?;
    }
    let mut config = config;
    config.version = 1;
    let body = serde_json::to_vec_pretty(&config).map_err(|e| e.to_string())?;
    if let Some(p) = s.config_file.parent() {
        std::fs::create_dir_all(p).map_err(|e| e.to_string())?
    }
    std::fs::write(&s.config_file, body).map_err(|e| e.to_string())?;
    *s.config.lock().await = config;
    Ok(())
}
fn loopback_http(value: &str) -> bool {
    reqwest::Url::parse(value).is_ok_and(|u| {
        u.scheme() == "http"
            && u.username().is_empty()
            && u.password().is_none()
            && matches!(
                u.host_str(),
                Some("127.0.0.1" | "localhost" | "[::1]" | "::1")
            )
    })
}
fn load_config(path: &Path, repo: &Path) -> Config {
    let candidate = std::fs::read(path)
        .ok()
        .and_then(|bytes| serde_json::from_slice::<Config>(&bytes).ok());
    match candidate {
        Some(c)
            if c.version == 1
                && loopback_http(&c.model_endpoint)
                && loopback_http(&c.wheel_endpoint) =>
        {
            c
        }
        _ => default_config(repo),
    }
}
fn state_name(state: &StateKind) -> String {
    match state {
        StateKind::Stopped => "stopped",
        StateKind::Starting => "starting",
        StateKind::Running => "running",
        StateKind::Ready => "ready",
        StateKind::Degraded => "degraded",
        StateKind::Stopping => "stopping",
        StateKind::Failed => "failed",
        StateKind::Unknown => "unknown",
        StateKind::ExternallyRunning => "externally_running",
        StateKind::NotConfigured => "not_configured",
        StateKind::Unsupported => "unsupported",
    }
    .into()
}
fn capability_component(
    id: &str,
    name: &str,
    state: StateKind,
    health: String,
    last_error: Option<String>,
    session: Option<&PythonSession>,
) -> Component {
    Component {
        id: id.into(),
        name: name.into(),
        kind: "capability".into(),
        state,
        pid: None,
        health,
        started_at: session.map(|current| current.started_at.clone()),
        last_error,
        log: vec![],
        owner_mode: session.map(|current| current.mode.clone()),
        session_id: session.map(|current| current.session_id.clone()),
        lifecycle_state: None,
        api_url: None,
    }
}

fn provider_component(
    id: &str,
    name: &str,
    label: &str,
    provider: Option<&serde_json::Value>,
    session: Option<&PythonSession>,
) -> Component {
    let Some(provider) = provider else {
        return capability_component(
            id,
            name,
            StateKind::Stopped,
            format!("{label} is not reported. Python Runtime is not running."),
            None,
            session,
        );
    };
    let status = provider
        .get("state")
        .and_then(|value| value.as_str())
        .unwrap_or("unknown");
    let state = match status {
        "loaded" => StateKind::Ready,
        "unloaded" | "not_initialized" => StateKind::Stopped,
        "configured" => StateKind::Degraded,
        _ => StateKind::Unknown,
    };
    let model = provider
        .get("model")
        .and_then(|value| value.as_str())
        .unwrap_or("model not reported");
    let device = provider
        .get("device")
        .and_then(|value| value.as_str())
        .unwrap_or("device not reported");
    let physical = provider
        .get("physical_memory_release")
        .and_then(|value| value.as_str());
    let health = match physical {
        Some(physical) => {
            format!("{label}: {status}; {model}; {device}; memory release {physical}")
        }
        None => format!("{label}: {status}; {model}; {device}"),
    };
    let last_error = provider
        .get("last_lifecycle_error")
        .and_then(|value| value.as_str())
        .map(str::to_string);
    capability_component(id, name, state, health, last_error, session)
}

fn voice_pipeline_component(
    voice: Option<&serde_json::Value>,
    session: Option<&PythonSession>,
) -> Component {
    let Some(session) = session else {
        return capability_component(
            "voice",
            "Voice Pipeline",
            StateKind::Stopped,
            "Stopped. The Voice pipeline starts inside the authoritative Python Runtime.".into(),
            None,
            None,
        );
    };
    let Some(voice) = voice else {
        return capability_component(
            "voice",
            "Voice Pipeline",
            StateKind::Degraded,
            "Python Runtime is running but returned no Voice diagnostics.".into(),
            Some("Missing data.voice in the management status response".into()),
            Some(session),
        );
    };
    let lifecycle = voice
        .get("lifecycle_state")
        .and_then(|value| value.as_str())
        .unwrap_or("unknown");
    let state = match lifecycle {
        "ready" => StateKind::Ready,
        "starting" => StateKind::Starting,
        "stopped" => StateKind::Stopped,
        "failed" => StateKind::Failed,
        "degraded" => StateKind::Degraded,
        _ => StateKind::Unknown,
    };
    let pipeline_active = voice
        .get("pipeline_active")
        .and_then(|value| value.as_bool())
        .unwrap_or(false);
    let pipeline_state = voice
        .get("pipeline_state")
        .and_then(|value| value.as_str())
        .unwrap_or("not_started");
    let microphone = voice
        .pointer("/microphone/state")
        .and_then(|value| value.as_str())
        .unwrap_or("unknown");
    let health = format!(
        "Pipeline {}; state {pipeline_state}; microphone {microphone}",
        if pipeline_active {
            "active"
        } else {
            "inactive"
        }
    );
    let last_error = voice
        .get("last_error")
        .and_then(|value| value.as_str())
        .map(str::to_string);
    let mut component = capability_component(
        "voice",
        "Voice Pipeline",
        state,
        health,
        last_error,
        Some(session),
    );
    component.lifecycle_state = Some(lifecycle.into());
    component
}

fn vision_component() -> Component {
    Component {
        id: "vision".into(),
        name: "Vision Model".into(),
        kind: "placeholder".into(),
        state: StateKind::Unsupported,
        pid: None,
        health: "Future capability. No vision runtime, model, or management route is implemented."
            .into(),
        started_at: None,
        last_error: None,
        log: vec![],
        owner_mode: None,
        session_id: None,
        lifecycle_state: None,
        api_url: None,
    }
}

fn process_component(
    id: &str,
    name: &str,
    state: StateKind,
    pid: Option<u32>,
    health: String,
    started_at: Option<String>,
) -> Component {
    Component {
        id: id.into(),
        name: name.into(),
        kind: "process".into(),
        state,
        pid,
        health,
        started_at,
        last_error: None,
        log: vec![],
        owner_mode: None,
        session_id: None,
        lifecycle_state: None,
        api_url: None,
    }
}

#[tauri::command]
async fn component_status(
    app: tauri::AppHandle,
    s: State<'_, AppState>,
) -> Result<Vec<Component>, String> {
    let reaped = s.reap_exited_python().await;
    for exit in &reaped {
        s.log(
            &app,
            &runtime_key(&exit.mode),
            format!(
                "released exited runtime PID {} ({}) after {}",
                exit.session.pid, exit.reason, exit.at
            ),
        )
        .await;
    }
    let last_exit = s.last_exit.lock().await.clone();
    let c = s.config.lock().await.clone();
    let mut out = Vec::new();

    let model_health = s
        .client
        .get(format!("{}/health", c.model_endpoint))
        .timeout(Duration::from_millis(900))
        .send()
        .await;
    let (model_owned, model_pid, model_started) = {
        let mut owned = s.owned.lock().await;
        if let Some(entry) = owned.get_mut("model") {
            match entry.child.try_wait() {
                Ok(None) => (true, entry.child.id(), Some(entry.started.clone())),
                Ok(Some(_)) => {
                    owned.remove("model");
                    (false, None, None)
                }
                Err(_) => (false, None, None),
            }
        } else {
            (false, None, None)
        }
    };
    let (model_state, model_health) = match model_health {
        Ok(response) if response.status().is_success() => (
            if model_owned {
                StateKind::Ready
            } else {
                StateKind::ExternallyRunning
            },
            if model_owned {
                "healthy endpoint; supervisor owns process"
            } else {
                "healthy endpoint; external/unowned"
            }
            .into(),
        ),
        Ok(response) => (StateKind::Degraded, format!("HTTP {}", response.status())),
        Err(error) => (
            if c.llama_server.is_empty() || c.model.is_empty() {
                StateKind::NotConfigured
            } else {
                StateKind::Stopped
            },
            error.to_string(),
        ),
    };
    out.push(process_component(
        "model",
        "AI Model Runtime",
        model_state,
        model_pid,
        model_health,
        model_started,
    ));

    let engine_health = engine_probe().await;
    let (engine_owned, engine_pid, engine_started) = {
        let mut owned = s.owned.lock().await;
        if let Some(entry) = owned.get_mut("engine") {
            match entry.child.try_wait() {
                Ok(None) => (true, entry.child.id(), Some(entry.started.clone())),
                Ok(Some(_)) => {
                    owned.remove("engine");
                    (false, None, None)
                }
                Err(_) => (false, None, None),
            }
        } else {
            (false, None, None)
        }
    };
    let (engine_state, engine_health) = match engine_health {
        Ok(value) => (
            if engine_owned {
                StateKind::Ready
            } else {
                StateKind::ExternallyRunning
            },
            format!("NDJSON v1 ping: {value}"),
        ),
        Err(error) => (StateKind::Stopped, error),
    };
    let _ = engine_pid;
    out.push(process_component(
        "engine",
        "Rust Engine",
        engine_state,
        engine_pid,
        engine_health,
        engine_started,
    ));

    let (wheel_ahk_running, wheel_pid, wheel_started) = {
        let mut owned = s.owned.lock().await;
        if let Some(entry) = owned.get_mut("wheel") {
            match entry.child.try_wait() {
                Ok(None) => (true, entry.child.id(), Some(entry.started.clone())),
                Ok(Some(_)) => {
                    owned.remove("wheel");
                    (false, None, None)
                }
                Err(_) => (false, None, None),
            }
        } else {
            (false, None, None)
        }
    };
    let wheel_backend_healthy = s
        .client
        .get(&c.wheel_endpoint)
        .timeout(Duration::from_millis(900))
        .send()
        .await
        .is_ok_and(|response| response.status().is_success());
    let backend_owned_by_wheel = s.wheel_backend_session.lock().await.is_some();
    let (wheel_state, wheel_health) = match (wheel_ahk_running, wheel_backend_healthy) {
        (true, true) => (
            StateKind::Ready,
            format!(
                "AHK wheel running; shared backend healthy ({})",
                if backend_owned_by_wheel {
                    "supervisor-owned"
                } else {
                    "external/shared"
                }
            ),
        ),
        (false, true) => (
            StateKind::Stopped,
            "Shared backend is healthy, but the AHK wheel is not running".into(),
        ),
        (true, false) => (StateKind::Degraded, "AHK wheel is running; backend health check failed".into()),
        (false, false) => (StateKind::Stopped, "Command Wheel is not running".into()),
    };
    out.push(process_component(
        "wheel",
        "Command Wheel",
        wheel_state,
        wheel_pid,
        wheel_health,
        wheel_started,
    ));
    out.push(vision_component());

    let sessions = {
        let mut owned = s.owned.lock().await;
        runtime_sessions_locked(&mut owned)
    };
    for profile in RUNTIME_PROFILES {
        let key = runtime_key(profile.id);
        let session = sessions.iter().find(|session| session.mode == profile.id).cloned();
        let (mut state, pid, started, mut health, last_error) = match session.as_ref() {
            Some(current) => (
                current.state.clone(),
                Some(current.pid),
                Some(current.started_at.clone()),
                "Process owned by supervisor".into(),
                None,
            ),
            None => match last_exit.as_ref().filter(|exit| exit.mode == profile.id) {
                Some(exit) => (
                    StateKind::Failed,
                    None,
                    Some(exit.session.started_at.clone()),
                    "Runtime exited before it became ready".into(),
                    Some(format!("{} ({})", exit.reason, exit.at)),
                ),
                None => (
                    StateKind::Stopped,
                    None,
                    None,
                    "Not started. This profile launches an independent TOM process.".into(),
                    None,
                ),
            },
        };
        let mut api_url = None;
        if let Some(current) = session.as_ref() {
            api_url = current.api_url.clone();
            let control = {
                let owned = s.owned.lock().await;
                owned.get(&key).and_then(|entry| entry.control.clone())
            };
            if let Some((port, token)) = control {
                let endpoint = format!("http://127.0.0.1:{port}/api/v1/status");
                match s
                    .client
                    .get(&endpoint)
                    .bearer_auth(token)
                    .timeout(PYTHON_STATUS_TIMEOUT)
                    .send()
                    .await
                {
                    Ok(response) if response.status().is_success() => {
                        let body: serde_json::Value = response.json().await.unwrap_or_default();
                        match python_state_from_body(&body) {
                            Ok(py_state) => {
                                health = format!("Authenticated Python API: {py_state}");
                                if current.state != StateKind::Starting {
                                    state = if py_state == "ready" {
                                        StateKind::Ready
                                    } else {
                                        StateKind::Degraded
                                    };
                                }
                            }
                            Err(error) => {
                                health = format!("Python API contract error: {error}");
                                if current.state != StateKind::Starting {
                                    state = StateKind::Degraded;
                                }
                            }
                        }
                    }
                    Ok(response) => {
                        health = format!("Python API returned HTTP {}", response.status());
                        if current.state != StateKind::Starting {
                            state = StateKind::Degraded;
                        }
                    }
                    Err(error) => {
                        health = format!("Python API unavailable: {error}");
                        if current.state != StateKind::Starting {
                            state = StateKind::Degraded;
                        }
                    }
                }
            }
        }
        out.push(Component {
            id: profile.id.into(),
            name: profile.name.into(),
            kind: "runtime".into(),
            lifecycle_state: session.as_ref().map(|_| state_name(&state)),
            api_url,
            owner_mode: session.as_ref().map(|_| profile.id.to_string()),
            session_id: session.as_ref().map(|current| current.session_id.clone()),
            state,
            pid,
            health,
            started_at: started,
            last_error,
            log: vec![],
        });
    }
    Ok(out)
}

fn spec(c: &Config, id: &str) -> Result<(PathBuf, Vec<String>, PathBuf), String> {
    let repo = PathBuf::from(&c.repo);
    let (exe, args, cwd) = match id {
        "engine" => (PathBuf::from(&c.engine), vec![], repo.clone()),
        "python" => (
            PathBuf::from(&c.python),
            vec![
                "-m".into(),
                "tom.main".into(),
                "--service".into(),
                "--headless".into(),
            ],
            repo.clone(),
        ),
        "cli" => (
            PathBuf::from(&c.python),
            vec!["-m".into(), "tom.main".into(), "--cli".into()],
            repo.clone(),
        ),
        "model" => {
            if c.llama_server.is_empty() || c.model.is_empty() {
                return Err("No existing local llama-server executable and GGUF model path are configured. Set both in Settings.".into());
            }
            if !PathBuf::from(&c.model).is_file() {
                return Err(format!("Configured model file does not exist: {}", c.model));
            }
            (
                PathBuf::from(&c.llama_server),
                vec![
                    "-m".into(),
                    c.model.clone(),
                    "--host".into(),
                    "127.0.0.1".into(),
                    "--port".into(),
                    c.port.to_string(),
                    "-c".into(),
                    c.context_size.to_string(),
                    "-ngl".into(),
                    c.gpu_layers.to_string(),
                ],
                repo.clone(),
            )
        }
        "wheel_script" => (
            PathBuf::from(&c.ahk_executable),
            vec![c.wheel_script.clone()],
            repo.clone(),
        ),
        _ => return Err("Unknown component".into()),
    };
    if !exe.exists() {
        return Err(format!("Executable not found: {}", exe.display()));
    }
    if id == "wheel_script" && !PathBuf::from(&c.wheel_script).is_file() {
        return Err(format!(
            "Command Wheel script does not exist: {}",
            c.wheel_script
        ));
    }
    Ok((exe, args, cwd))
}
#[tauri::command]
#[allow(unused_assignments)]
async fn start_component(
    app: tauri::AppHandle,
    s: State<'_, AppState>,
    id: String,
) -> Result<u32, String> {
    let python = ["python", "cli"].contains(&id.as_str());
    if id == "model" {
        let c = s.config.lock().await.clone();
        let response = s
            .client
            .get(format!("{}/health", c.model_endpoint))
            .timeout(Duration::from_millis(900))
            .send()
            .await;
        if response.is_ok_and(|r| r.status().is_success()) {
            return Err("A model endpoint is already healthy; refusing to launch a duplicate server. It will remain externally owned unless this supervisor started it.".into());
        }
    }
    if id == "engine" && engine_probe().await.is_ok() {
        return Err(
            "A healthy engine is already responding on its named pipe; refusing a duplicate launch"
                .into(),
        );
    }
    if python && engine_probe().await.is_err() {
        return Err("Rust Engine dependency is not healthy. Start Rust Engine first.".into());
    }
    let reaped = s.reap_exited_python().await;
    for exit in &reaped {
        s.log(
            &app,
            &exit.mode,
            format!(
                "released exited Python session PID {} ({}) before startup",
                exit.session.pid, exit.reason
            ),
        )
        .await;
    }
    let c = s.config.lock().await.clone();
    if python && !PathBuf::from(&c.repo).join("python/tom").exists() {
        return Err("Python package directory is missing".into());
    }
    if id == "wheel_script" {
        let healthy = s
            .client
            .get(&c.wheel_endpoint)
            .timeout(Duration::from_millis(900))
            .send()
            .await
            .is_ok_and(|r| r.status().is_success());
        if !healthy {
            return Err("Wheel backend health check failed. Start the Python Runtime or CLI first; the backend belongs to the Python runtime.".into());
        }
    }
    let (exe, args, cwd) = spec(&c, &id)?;
    let control = if python {
        Some(new_control_session()?)
    } else {
        None
    };
    let session_id = python.then(|| random_hex(16));
    let mut cmd = Command::new(exe);
    cmd.args(args)
        .current_dir(cwd)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(false);
    apply_no_console(&mut cmd);
    if let Some((port, token)) = &control {
        for (key, value) in control_environment(*port, token) {
            cmd.env(key, value);
        }
    }
    let mut owned = s.owned.lock().await;
    if python {
        if let Some(existing) = live_python_session(&mut owned) {
            return Err(python_conflict_message(&existing));
        }
    } else if let Some(existing) = owned.get_mut(&id) {
        match existing.child.try_wait() {
            Ok(None) => {
                return Err(format!(
                    "{id} is already managed (PID {:?})",
                    existing.child.id()
                ))
            }
            Ok(Some(_)) => {
                owned.remove(&id);
            }
            Err(error) => return Err(error.to_string()),
        }
    }
    let mut child = cmd.spawn().map_err(|e| e.to_string())?;
    let pid = child.id().ok_or("Child process did not expose a PID")?;
    let started = Utc::now().to_rfc3339();
    if let Some(reader) = child.stdout.take() {
        spawn_log_stream(app.clone(), s.inner().clone(), id.clone(), "stdout", reader);
    }
    if let Some(reader) = child.stderr.take() {
        spawn_log_stream(app.clone(), s.inner().clone(), id.clone(), "stderr", reader);
    }
    let session = control.as_ref().map(|(port, _)| PythonSession {
        mode: id.clone(),
        pid,
        session_id: session_id.unwrap_or_default(),
        started_at: started.clone(),
        state: StateKind::Starting,
        api_url: Some(format!("http://127.0.0.1:{port}/api/v1")),
    });
    owned.insert(
        id.clone(),
        Owned {
            child,
            started,
            control: control.clone(),
            session,
        },
    );
    drop(owned);
    {
        let mut last_exit = s.last_exit.lock().await;
        if last_exit.as_ref().is_some_and(|exit| exit.mode == id) {
            *last_exit = None;
        }
    }
    s.log(
        &app,
        &id,
        match &control {
            Some((port, _)) => {
                format!("started process PID {pid}; control API reserved on 127.0.0.1:{port}")
            }
            None => format!("started process PID {pid}"),
        },
    )
    .await;
    if let Some((port, token)) = control {
        let endpoint = format!("http://127.0.0.1:{port}/api/v1/status");
        let deadline = Instant::now() + PYTHON_READY_DEADLINE;
        let mut delay = Duration::from_millis(200);
        let mut last: Option<String> = None;
        loop {
            if let Some(reason) = s.python_exit(&id).await {
                let _ = s.reap_exited_python().await;
                let tail = s.log_tail(&id, 20).await;
                return Err(format!(
                    "Python session exited before its management API became ready ({reason}). {tail}"
                ));
            }
            match probe_python_status(&s.client, &endpoint, &token).await {
                Ok(state) if state == "ready" => {
                    s.set_python_state(&id, StateKind::Ready).await;
                    s.log(&app, &id, "authenticated Python readiness passed".into())
                        .await;
                    return Ok(pid);
                }
                Ok(state) => {
                    last = Some(format!("Python reported state '{state}'"));
                }
                Err(error) => {
                    last = Some(error);
                }
            }
            if Instant::now() >= deadline {
                let _ = stop_owned(app.clone(), s.inner().clone(), &id).await;
                let tail = s.log_tail(&id, 20).await;
                let reason = last.as_deref().unwrap_or("no successful status response");
                return Err(format!(
                    "Python management API did not become ready within {} seconds ({reason}). {tail}",
                    PYTHON_READY_DEADLINE.as_secs()
                ));
            }
            tokio::time::sleep(delay).await;
            delay = (delay * 2).min(Duration::from_millis(1000));
        }
    }
    Ok(pid)
}
fn spawn_log_stream<R: AsyncRead + Unpin + Send + 'static>(
    app: tauri::AppHandle,
    supervisor: AppState,
    id: String,
    stream: &'static str,
    reader: R,
) {
    tokio::spawn(async move {
        let mut lines = BufReader::new(reader).lines();
        while let Ok(Some(line)) = lines.next_line().await {
            supervisor.log(&app, &id, format!("{stream}: {line}")).await;
        }
    });
}
#[tauri::command]
async fn stop_component(
    app: tauri::AppHandle,
    s: State<'_, AppState>,
    id: String,
) -> Result<(), String> {
    stop_owned(app, s.inner().clone(), &id).await
}
async fn stop_owned(app: tauri::AppHandle, s: AppState, id: &str) -> Result<(), String> {
    let reaped = s.reap_exited_python().await;
    for exit in &reaped {
        s.log(
            &app,
            &exit.mode,
            format!(
                "released exited Python session PID {} ({})",
                exit.session.pid, exit.reason
            ),
        )
        .await;
    }
    let running_python = s.current_python_control().await.is_some();
    if id == "engine" && running_python {
        return Err("Cannot stop Rust Engine while a TOM Python session depends on it".into());
    }
    if id == "model" && running_python {
        return Err("Cannot stop the model runtime while a TOM Python session is active".into());
    }
    let mut owned = s.owned.lock().await;
    let entry = owned
        .get_mut(id)
        .ok_or("Component is not owned by Control Center")?;
    let control = entry.control.clone();
    if let Some(session) = entry.session.as_mut() {
        session.state = StateKind::Stopping;
    }
    let graceful = if let Some((port, token)) = control.as_ref() {
        let _ = s
            .client
            .post(format!("http://127.0.0.1:{port}/api/v1/shutdown"))
            .bearer_auth(token)
            .timeout(Duration::from_secs(2))
            .send()
            .await;
        tokio::time::timeout(Duration::from_secs(8), entry.child.wait())
            .await
            .is_ok_and(|result| result.is_ok())
    } else {
        false
    };
    if graceful {
        owned.remove(id);
        drop(owned);
        s.log(&app, id, "Python service shut down gracefully".into())
            .await;
        return Ok(());
    }
    let engine_closed = if id == "engine" {
        let _ = engine_request("engine.shutdown", serde_json::json!({})).await;
        tokio::time::timeout(Duration::from_secs(6), entry.child.wait())
            .await
            .is_ok_and(|result| result.is_ok())
    } else {
        false
    };
    if engine_closed {
        owned.remove(id);
        drop(owned);
        s.log(&app, id, "Engine acknowledged graceful shutdown".into())
            .await;
        return Ok(());
    }
    let cli_closed = if id == "cli" {
        entry.child.stdin.take();
        tokio::time::timeout(Duration::from_secs(2), entry.child.wait())
            .await
            .is_ok_and(|result| result.is_ok())
    } else {
        false
    };
    if cli_closed {
        owned.remove(id);
        drop(owned);
        s.log(&app, id, "CLI exited after stdin closed".into())
            .await;
        return Ok(());
    }
    if let Err(error) = entry.child.kill().await {
        if let Some(session) = entry.session.as_mut() {
            session.state = StateKind::Failed;
        }
        return Err(format!(
            "failed to stop process: {error}; ownership retained"
        ));
    }
    let stopped = tokio::time::timeout(Duration::from_secs(5), entry.child.wait()).await;
    match stopped {
        Ok(Ok(_)) => {
            owned.remove(id);
            drop(owned);
            s.log(&app, id, "stopped supervisor-owned process".into())
                .await;
            Ok(())
        }
        Ok(Err(error)) => {
            if let Some(session) = entry.session.as_mut() {
                session.state = StateKind::Failed;
            }
            Err(format!(
                "process was signalled but wait failed: {error}; ownership retained"
            ))
        }
        Err(_) => {
            if let Some(session) = entry.session.as_mut() {
                session.state = StateKind::Failed;
            }
            Err(
                "process was signalled but did not exit within 5 seconds; ownership retained"
                    .into(),
            )
        }
    }
}
#[tauri::command]
async fn restart_component(
    app: tauri::AppHandle,
    s: State<'_, AppState>,
    id: String,
) -> Result<u32, String> {
    let reaped = s.reap_exited_python().await;
    for exit in &reaped {
        s.log(
            &app,
            &exit.mode,
            format!(
                "released exited Python session PID {} ({}) before restart",
                exit.session.pid, exit.reason
            ),
        )
        .await;
    }
    if s.owned.lock().await.contains_key(&id) {
        stop_component(app.clone(), s.clone(), id.clone()).await?;
    }
    start_component(app, s, id).await
}
#[tauri::command]
async fn send_cli_input(s: State<'_, AppState>, line: String) -> Result<(), String> {
    if line.len() > 16_000 {
        return Err("Input exceeds the 16 KB limit".into());
    }
    let mut owned = s.owned.lock().await;
    let child = owned
        .get_mut("cli")
        .ok_or("TOM CLI is not running under supervisor ownership")?;
    let stdin = child
        .child
        .stdin
        .as_mut()
        .ok_or("CLI input stream is unavailable")?;
    stdin
        .write_all(line.as_bytes())
        .await
        .map_err(|e| e.to_string())?;
    stdin.write_all(b"\n").await.map_err(|e| e.to_string())?;
    stdin.flush().await.map_err(|e| e.to_string())
}
#[tauri::command]
async fn start_tom(
    app: tauri::AppHandle,
    s: State<'_, AppState>,
    mode: String,
) -> Result<Vec<String>, String> {
    if !["python", "cli"].contains(&mode.as_str()) {
        return Err(
            "Choose the Python Service or CLI as the TOM interaction mode. Voice is managed inside the Python Runtime."
                .into(),
        );
    }
    let reaped = s.reap_exited_python().await;
    for exit in &reaped {
        s.log(
            &app,
            &exit.mode,
            format!(
                "released exited Python session PID {} ({}) before startup",
                exit.session.pid, exit.reason
            ),
        )
        .await;
    }
    if let Some((existing, _)) = s.current_python_control().await {
        return Err(python_conflict_message(&existing));
    }
    let mut started = Vec::new();
    if engine_probe().await.is_err() {
        match start_component(app.clone(), s.clone(), "engine".into()).await {
            Ok(_) => {
                started.push("engine".into());
                let ready = tokio::time::timeout(Duration::from_secs(20), async {
                    loop {
                        if engine_probe().await.is_ok() {
                            break true;
                        }
                        tokio::time::sleep(Duration::from_millis(250)).await
                    }
                })
                .await
                .unwrap_or(false);
                if !ready {
                    let _ = stop_owned(app.clone(), s.inner().clone(), "engine").await;
                    return Err(
                        "Rust Engine did not pass its NDJSON v1 health check within 20 seconds"
                            .into(),
                    );
                }
            }
            Err(e) => return Err(format!("Rust Engine dependency failed: {e}")),
        }
    }
    let c = s.config.lock().await.clone();
    if !c.llama_server.is_empty() && !c.model.is_empty() {
        let healthy = s
            .client
            .get(format!("{}/health", c.model_endpoint))
            .timeout(Duration::from_millis(700))
            .send()
            .await
            .is_ok_and(|r| r.status().is_success());
        if !healthy {
            match start_component(app.clone(), s.clone(), "model".into()).await {
                Ok(_) => {
                    started.push("model".into());
                    let endpoint = c.model_endpoint.clone();
                    let client = s.client.clone();
                    let ready = tokio::time::timeout(Duration::from_secs(180), async move {
                        loop {
                            if client
                                .get(format!("{endpoint}/health"))
                                .timeout(Duration::from_secs(2))
                                .send()
                                .await
                                .is_ok_and(|r| r.status().is_success())
                            {
                                break true;
                            }
                            tokio::time::sleep(Duration::from_secs(1)).await
                        }
                    })
                    .await
                    .unwrap_or(false);
                    if !ready {
                        rollback_started(app.clone(), s.inner().clone(), &started).await;
                        return Err("Model server did not become healthy within 180 seconds".into());
                    }
                }
                Err(e) => {
                    rollback_started(app.clone(), s.inner().clone(), &started).await;
                    return Err(format!("Model runtime dependency failed: {e}"));
                }
            }
        }
    }
    match start_component(app.clone(), s.clone(), mode.clone()).await {
        Ok(_) => started.push(mode),
        Err(e) => {
            rollback_started(app.clone(), s.inner().clone(), &started).await;
            return Err(format!(
                "Python Runtime startup failed after starting {started:?}: {e}"
            ));
        }
    }
    Ok(started)
}
async fn rollback_started(app: tauri::AppHandle, s: AppState, started: &[String]) {
    for id in started.iter().rev() {
        if let Err(error) = stop_owned(app.clone(), s.clone(), id).await {
            s.log(&app, id, format!("startup rollback failed: {error}"))
                .await;
        }
    }
}
#[tauri::command]
async fn stop_tom(app: tauri::AppHandle, s: State<'_, AppState>) -> Result<Vec<String>, String> {
    let reaped = s.reap_exited_python().await;
    for exit in &reaped {
        s.log(
            &app,
            &exit.mode,
            format!(
                "released exited Python session PID {} ({})",
                exit.session.pid, exit.reason
            ),
        )
        .await;
    }
    let mut stopped = Vec::new();
    for id in ["python", "cli", "wheel_script", "model", "engine"] {
        if s.owned.lock().await.contains_key(id) {
            stop_owned(app.clone(), s.inner().clone(), id).await?;
            stopped.push(id.to_string());
        }
    }
    Ok(stopped)
}
#[tauri::command]
async fn resolve_close(
    app: tauri::AppHandle,
    s: State<'_, AppState>,
    policy: String,
) -> Result<(), String> {
    match policy.as_str() {
        "keep" => app.exit(0),
        "stop" => {
            stop_tom(app.clone(), s).await?;
            app.exit(0);
        }
        "cancel" => {
            s.closing.store(false, Ordering::SeqCst);
        }
        _ => return Err("Close policy must be keep, stop, or cancel".into()),
    }
    Ok(())
}
#[tauri::command]
async fn get_logs(s: State<'_, AppState>) -> Result<Vec<String>, String> {
    Ok(s.logs.lock().await.clone())
}
#[tauri::command]
async fn telemetry(s: State<'_, AppState>) -> Result<serde_json::Value, String> {
    let mut sys = System::new();
    sys.refresh_cpu_usage();
    tokio::time::sleep(Duration::from_millis(250)).await;
    sys.refresh_cpu_usage();
    sys.refresh_memory();
    let cpu = sys.global_cpu_usage();
    let mem = if sys.total_memory() > 0 {
        100.0 * sys.used_memory() as f64 / sys.total_memory() as f64
    } else {
        0.0
    };
    let owned = s.owned.lock().await;
    let names = [
        ("engine", "Rust Engine"),
        ("python", "Python Runtime"),
        ("cli", "TOM CLI"),
        ("model", "AI Model Runtime"),
        ("wheel_script", "Command Wheel"),
    ];
    let processes = names
        .iter()
        .filter_map(|(id, name)| {
            owned.get(*id).and_then(|p| {
                p.child
                    .id()
                    .map(|pid| serde_json::json!({"pid":pid,"name":name}))
            })
        })
        .collect::<Vec<_>>();
    let gpu = s.cached_gpu_telemetry().await;
    Ok(
        serde_json::json!({"cpu_percent":cpu,"ram_percent":mem,"ram_used_mb":sys.used_memory()/1024/1024,"ram_total_mb":sys.total_memory()/1024/1024,"gpu":gpu,"processes":processes}),
    )
}

async fn gpu_telemetry() -> serde_json::Value {
    let sampled_at = Utc::now().to_rfc3339();
    let mut gpu_command = Command::new("nvidia-smi");
    gpu_command
        .args(["--query-gpu=name,driver_version,utilization.gpu,memory.total,memory.used,memory.free,temperature.gpu", "--format=csv,noheader,nounits"])
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true);
    apply_no_console(&mut gpu_command);
    let output = tokio::time::timeout(Duration::from_secs(2), gpu_command.output()).await;
    match output {
        Ok(Ok(out)) if out.status.success() => {
            let row = String::from_utf8_lossy(&out.stdout)
                .lines()
                .next()
                .unwrap_or("")
                .to_string();
            let Some(mut value) = parse_gpu_row(&row) else {
                return serde_json::json!({"available":false,"sampled_at":sampled_at,"reason":"Unrecognized nvidia-smi output"});
            };
            value["sampled_at"] = serde_json::json!(sampled_at);
            let mut process_command = Command::new("nvidia-smi");
            process_command
                .args([
                    "--query-compute-apps=pid,process_name,used_memory",
                    "--format=csv,noheader,nounits",
                ])
                .stdout(Stdio::piped())
                .stderr(Stdio::piped())
                .kill_on_drop(true);
            apply_no_console(&mut process_command);
            let processes =
                tokio::time::timeout(Duration::from_secs(2), process_command.output()).await;
            value["processes"] = match processes {
                Ok(Ok(result)) if result.status.success() => serde_json::json!(
                    parse_gpu_processes(&String::from_utf8_lossy(&result.stdout))
                ),
                Ok(Ok(result)) => {
                    serde_json::json!({"available":false,"reason":String::from_utf8_lossy(&result.stderr).trim()})
                }
                Ok(Err(error)) => serde_json::json!({"available":false,"reason":error.to_string()}),
                Err(_) => {
                    serde_json::json!({"available":false,"reason":"nvidia-smi process query timed out"})
                }
            };
            value
        }
        Ok(Ok(out)) => {
            serde_json::json!({"available":false,"sampled_at":sampled_at,"reason":String::from_utf8_lossy(&out.stderr).trim()})
        }
        Ok(Err(e)) => {
            serde_json::json!({"available":false,"sampled_at":sampled_at,"reason":e.to_string()})
        }
        Err(_) => {
            serde_json::json!({"available":false,"sampled_at":sampled_at,"reason":"nvidia-smi timed out"})
        }
    }
}
fn parse_gpu_row(row: &str) -> Option<serde_json::Value> {
    let fields: Vec<&str> = row.split(',').map(str::trim).collect();
    (fields.len() == 7).then(|| serde_json::json!({
        "available":true,"name":fields[0],"driver_version":fields[1],
        "utilization_percent":fields[2].parse::<f64>().ok(),
        "vram_total_mb":fields[3].parse::<u64>().ok(),"vram_used_mb":fields[4].parse::<u64>().ok(),
        "vram_free_mb":fields[5].parse::<u64>().ok(),"temperature_c":fields[6].parse::<u64>().ok()
    }))
}
fn parse_gpu_processes(csv: &str) -> Vec<serde_json::Value> {
    csv.lines()
        .filter_map(|line| {
            let mut fields = line.splitn(3, ',').map(str::trim);
            Some(serde_json::json!({
                "pid":fields.next()?.parse::<u32>().ok()?,
                "process":fields.next()?,
                "vram_mb":fields.next()?.parse::<u64>().ok()
            }))
        })
        .collect()
}
#[tauri::command]
async fn probe_model(s: State<'_, AppState>) -> Result<serde_json::Value, String> {
    let c = s.config.lock().await;
    let v = s
        .client
        .get(format!("{}/v1/models", c.model_endpoint))
        .timeout(Duration::from_secs(2))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    v.json().await.map_err(|e| e.to_string())
}
#[tauri::command]
async fn memory_status(s: State<'_, AppState>) -> Result<serde_json::Value, String> {
    let c = s.config.lock().await;
    let p = PathBuf::from(&c.repo).join("data/memory/tom.db");
    let size = std::fs::metadata(&p).map(|m| m.len()).unwrap_or(0);
    Ok(serde_json::json!({"path":p,"available":p.exists(),"size_bytes":size,"qdrant":"not probed"}))
}
async fn fetch_control_route(
    client: &Client,
    base: &str,
    token: &str,
    route: &str,
) -> Result<serde_json::Value, String> {
    let value: serde_json::Value = client
        .get(format!("{base}/{route}"))
        .bearer_auth(token)
        .timeout(PYTHON_STATUS_TIMEOUT)
        .send()
        .await
        .map_err(|e| e.to_string())?
        .error_for_status()
        .map_err(|e| e.to_string())?
        .json()
        .await
        .map_err(|e| e.to_string())?;
    Ok(value.get("data").cloned().unwrap_or(value))
}
#[tauri::command]
async fn python_diagnostics(s: State<'_, AppState>) -> Result<serde_json::Value, String> {
    let _ = s.reap_exited_python().await;
    let Some((session, control)) = s.current_python_control().await else {
        return Ok(
            serde_json::json!({"available":false,"reason":"No supervisor-owned Python session exposes diagnostics"}),
        );
    };
    let Some((port, token)) = control else {
        return Ok(
            serde_json::json!({"available":false,"reason":"The owned Python session does not expose a control API"}),
        );
    };
    let base = format!("http://127.0.0.1:{port}/api/v1");
    let (status, tools, memory) = tokio::join!(
        fetch_control_route(&s.client, &base, &token, "status"),
        fetch_control_route(&s.client, &base, &token, "tools"),
        fetch_control_route(&s.client, &base, &token, "memory"),
    );
    let mut result = serde_json::Map::new();
    result.insert(
        "session".into(),
        serde_json::json!({
            "mode": session.mode,
            "pid": session.pid,
            "session_id": session.session_id,
            "state": session.state,
            "api_url": session.api_url,
        }),
    );
    for (route, value) in [("status", status), ("tools", tools), ("memory", memory)] {
        result.insert(
            route.into(),
            value.map_err(|error| format!("{route} diagnostics failed: {error}"))?,
        );
    }
    Ok(serde_json::Value::Object(result))
}

#[tauri::command]
async fn python_voice_operation(
    s: State<'_, AppState>,
    operation: String,
    payload: serde_json::Value,
) -> Result<serde_json::Value, String> {
    if ![
        "status",
        "start_pipeline",
        "stop_pipeline",
        "load_stt",
        "unload_stt",
        "test_stt",
        "load_tts",
        "unload_tts",
        "test_tts",
    ]
    .contains(&operation.as_str())
    {
        return Err("Unsupported voice operation".into());
    }
    let _ = s.reap_exited_python().await;
    let (_, control) = s
        .current_python_control()
        .await
        .ok_or("No supervisor-owned Python session is available")?;
    let (port, token) = control.ok_or("The owned Python session does not expose a control API")?;
    let response = s
        .client
        .post(format!("http://127.0.0.1:{port}/api/v1/voice/{operation}"))
        .bearer_auth(token)
        .timeout(Duration::from_secs(190))
        .json(&payload)
        .send()
        .await
        .map_err(|e| e.to_string())?;
    let status = response.status();
    let value: serde_json::Value = response.json().await.map_err(|e| e.to_string())?;
    if !status.is_success() {
        return Err(value
            .pointer("/data/error")
            .and_then(|v| v.as_str())
            .unwrap_or("Voice operation failed")
            .to_string());
    }
    Ok(value.get("data").cloned().unwrap_or(value))
}

#[tauri::command]
async fn engine_diagnostics() -> Result<serde_json::Value, String> {
    let mut data = serde_json::Map::new();
    for method in [
        "engine.status",
        "audio.status",
        "audio.devices",
        "input.list_hotkeys",
        "system.all",
    ] {
        let result = match tokio::time::timeout(
            Duration::from_secs(3),
            engine_request(method, serde_json::json!({})),
        )
        .await
        {
            Ok(Ok(value)) => value,
            Ok(Err(error)) => serde_json::json!({"state":"unavailable","error":error}),
            Err(_) => serde_json::json!({"state":"unavailable","error":"request timed out"}),
        };
        data.insert(method.replace('.', "_"), result);
    }
    Ok(serde_json::Value::Object(data))
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let repo = find_repo();
    let cfgdir = std::env::var_os("APPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(|| repo.clone())
        .join("TOM")
        .join("ControlCenter");
    let file = cfgdir.join("config.json");
    let config = load_config(&file, &repo);
    let state = Arc::new(Supervisor {
        config_file: file,
        config: Mutex::new(config),
        owned: Mutex::new(HashMap::new()),
        logs: Mutex::new(Vec::new()),
        client: Client::new(),
        closing: AtomicBool::new(false),
        last_exit: Mutex::new(None),
        gpu_cache: Mutex::new(GpuCache::default()),
        start_lock: Mutex::new(()),
        pending_pongs: Mutex::new(HashMap::new()),
        wheel_backend_session: Mutex::new(None),
    });
    tauri::Builder::default()
        .manage(state)
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.emit("close-policy-required", ());
            }
        })
        .invoke_handler(tauri::generate_handler![
            get_config,
            save_config,
            component_status,
            start_component,
            stop_component,
            restart_component,
            send_cli_input,
            start_tom,
            stop_tom,
            resolve_close,
            get_logs,
            telemetry,
            probe_model,
            memory_status,
            python_diagnostics,
            python_voice_operation,
            engine_diagnostics
        ])
        .run(tauri::generate_context!())
        .expect("TOM Control Center failed");
}
fn main() {
    run()
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn service_urls_must_parse_as_loopback_http() {
        assert!(loopback_http("http://127.0.0.1:65400"));
        assert!(loopback_http("http://localhost:65401/health"));
        assert!(!loopback_http("https://127.0.0.1:65400"));
        assert!(!loopback_http("http://127.0.0.1.attacker.test/"));
        assert!(!loopback_http("http://127.0.0.1@attacker.test/"));
        assert!(!loopback_http("https://example.com/"));
    }
    #[test]
    fn defaults_do_not_guess_or_download_a_model() {
        let cfg = default_config(Path::new("C:/TOM"));
        assert_eq!(cfg.version, 1);
        assert!(cfg.llama_server.is_empty());
        assert!(cfg.model.is_empty());
        assert_eq!(cfg.port, 65400);
        assert_eq!(cfg.model_endpoint, "http://127.0.0.1:65400");
    }
    #[test]
    fn invalid_saved_endpoints_fall_back_without_network_access() {
        let path = std::env::temp_dir().join(format!("tom-cc-config-{}.json", std::process::id()));
        let mut config = default_config(Path::new("C:/TOM"));
        config.model_endpoint = "http://example.com".into();
        std::fs::write(&path, serde_json::to_vec(&config).unwrap()).unwrap();
        let loaded = load_config(&path, Path::new("C:/TOM"));
        let _ = std::fs::remove_file(&path);
        assert_eq!(loaded.model_endpoint, "http://127.0.0.1:65400");
    }
    #[test]
    fn nvidia_smi_gpu_and_process_rows_are_parsed_without_shell_assumptions() {
        let gpu =
            parse_gpu_row("NVIDIA RTX 4060 Laptop GPU, 566.14, 37, 8192, 4096, 4096, 61").unwrap();
        assert_eq!(gpu["name"], "NVIDIA RTX 4060 Laptop GPU");
        assert_eq!(gpu["vram_free_mb"], 4096);
        assert!(parse_gpu_row("malformed").is_none());
        let processes =
            parse_gpu_processes("1234, C:\\TOM\\llama-server.exe, 3500\n5678, python.exe, 420");
        assert_eq!(processes.len(), 2);
        assert_eq!(processes[0]["pid"], 1234);
        assert_eq!(processes[0]["vram_mb"], 3500);
    }
    #[test]
    fn readiness_requires_api_v1_and_a_python_state() {
        let ready = serde_json::json!({"api_version":1,"data":{"python":{"state":"ready"}}});
        assert_eq!(python_state_from_body(&ready).unwrap(), "ready");
        let starting = serde_json::json!({"api_version":1,"data":{"python":{"state":"starting"}}});
        assert_eq!(python_state_from_body(&starting).unwrap(), "starting");
        assert!(
            python_state_from_body(&serde_json::json!({"data":{"python":{"state":"ready"}}}))
                .is_err()
        );
        assert!(python_state_from_body(
            &serde_json::json!({"api_version":2,"data":{"python":{"state":"ready"}}})
        )
        .is_err());
        assert!(
            python_state_from_body(&serde_json::json!({"api_version":1,"data":{"python":{}}}))
                .is_err()
        );
        assert!(python_state_from_body(&serde_json::json!({"api_version":1,"data":{}})).is_err());
    }
    #[test]
    fn control_session_uses_an_ephemeral_loopback_port_and_private_token() {
        let (port, token) = new_control_session().unwrap();
        assert!(port > 0);
        assert_eq!(token.len(), 64);
        assert!(token.chars().all(|c| c.is_ascii_hexdigit()));
        let environment = control_environment(port, &token);
        assert_eq!(environment[0].0, "TOM_CONTROL_PORT");
        assert_eq!(environment[0].1, port.to_string());
        assert_eq!(environment[1].0, "TOM_CONTROL_TOKEN");
        assert_eq!(environment[1].1, token);
        assert!(std::net::TcpListener::bind(("127.0.0.1", port)).is_ok());
    }
    #[test]
    fn helper_processes_request_no_console_and_their_own_process_group() {
        assert_eq!(HELPER_CREATION_FLAGS & CREATE_NO_WINDOW, CREATE_NO_WINDOW);
        assert_eq!(
            HELPER_CREATION_FLAGS & CREATE_NEW_PROCESS_GROUP,
            CREATE_NEW_PROCESS_GROUP
        );
        let mut command = Command::new("nvidia-smi");
        apply_no_console(&mut command);
    }
    #[test]
    fn gpu_cache_avoids_repeated_helper_spawns_within_its_ttl() {
        let mut cache = GpuCache::default();
        let now = Instant::now();
        assert!(cache.fresh(now).is_none());
        cache.store(now, serde_json::json!({"available":true,"name":"test-gpu"}));
        let fresh = cache.fresh(now + Duration::from_secs(5)).unwrap();
        assert_eq!(fresh["name"], "test-gpu");
        assert!(cache.fresh(now + GPU_CACHE_TTL).is_none());
    }
    #[test]
    fn python_runtime_replaces_brain_and_voice_process_modes() {
        let mut cfg = default_config(Path::new("C:/TOM"));
        cfg.python = std::env::current_exe()
            .unwrap()
            .to_string_lossy()
            .into_owned();
        let (_, args, _) = spec(&cfg, "python").unwrap();
        assert_eq!(args, ["-m", "tom.main", "--service", "--headless"]);
        assert!(spec(&cfg, "brain").is_err());
        assert!(spec(&cfg, "voice").is_err());
    }

    #[test]
    fn voice_capabilities_are_derived_from_the_authoritative_python_status() {
        let session = test_session("python", 1234);
        let stt = serde_json::json!({
            "state": "loaded",
            "model": "faster-whisper-small.en",
            "device": "cuda",
            "physical_memory_release": "unloaded"
        });
        let tts = serde_json::json!({
            "state": "unloaded",
            "model": "kokoro-v0_19.onnx",
            "device": "cpu"
        });
        let voice = serde_json::json!({
            "lifecycle_state": "ready",
            "pipeline_active": false,
            "pipeline_state": "not_started",
            "microphone": {"state": "idle"},
            "stt": stt,
            "tts": tts
        });
        let pipeline = voice_pipeline_component(Some(&voice), Some(&session));
        assert_eq!(pipeline.id, "voice");
        assert_eq!(pipeline.kind, "capability");
        assert_eq!(pipeline.state, StateKind::Ready);
        assert_eq!(pipeline.session_id.as_deref(), Some("session-test"));
        assert!(pipeline.health.contains("Pipeline inactive"));
        let speech = provider_component(
            "stt",
            "Speech-to-Text",
            "Faster-Whisper",
            voice.get("stt"),
            Some(&session),
        );
        assert_eq!(speech.state, StateKind::Ready);
        assert!(speech.health.contains("cuda"));
        let speech_out = provider_component(
            "tts",
            "Text-to-Speech",
            "Kokoro CPU",
            voice.get("tts"),
            Some(&session),
        );
        assert_eq!(speech_out.state, StateKind::Stopped);
        let stopped = voice_pipeline_component(None, None);
        assert_eq!(stopped.state, StateKind::Stopped);
        assert!(stopped.owner_mode.is_none());
        assert_eq!(vision_component().state, StateKind::Unsupported);
    }

    fn test_session(mode: &str, pid: u32) -> PythonSession {
        PythonSession {
            mode: mode.into(),
            pid,
            session_id: "session-test".into(),
            started_at: Utc::now().to_rfc3339(),
            state: StateKind::Starting,
            api_url: Some("http://127.0.0.1:12345/api/v1".into()),
        }
    }
    async fn spawn_test_child(exits_immediately: bool) -> Child {
        #[cfg(windows)]
        let mut command = {
            let mut command = Command::new("cmd");
            if exits_immediately {
                command.args(["/C", "exit", "0"]);
            } else {
                command.args(["/C", "ping", "-n", "30", "127.0.0.1"]);
            }
            command
        };
        #[cfg(not(windows))]
        let mut command = {
            let mut command = Command::new("sh");
            if exits_immediately {
                command.args(["-c", "exit 0"]);
            } else {
                command.args(["-c", "sleep 30"]);
            }
            command
        };
        command
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .kill_on_drop(true);
        apply_no_console(&mut command);
        command.spawn().expect("spawn test child")
    }
    #[tokio::test]
    async fn exited_python_child_is_reaped_before_another_session_can_start() {
        let child = spawn_test_child(true).await;
        let pid = child.id().expect("test child pid");
        let mut owned = HashMap::new();
        owned.insert(
            "python".into(),
            Owned {
                child,
                started: Utc::now().to_rfc3339(),
                control: Some((12345, "secret".into())),
                session: Some(test_session("python", pid)),
            },
        );
        tokio::time::sleep(Duration::from_millis(400)).await;
        let reaped = reap_exited_locked(&mut owned);
        assert_eq!(reaped.len(), 1);
        assert_eq!(reaped[0].mode, "python");
        assert!(owned.is_empty());
        assert!(live_python_session(&mut owned).is_none());
    }
    #[tokio::test]
    async fn live_python_child_keeps_ownership_when_its_api_is_unavailable() {
        let child = spawn_test_child(false).await;
        let pid = child.id().expect("test child pid");
        let mut owned = HashMap::new();
        owned.insert(
            "cli".into(),
            Owned {
                child,
                started: Utc::now().to_rfc3339(),
                control: Some((23456, "secret".into())),
                session: Some(test_session("cli", pid)),
            },
        );
        let existing = live_python_session(&mut owned).expect("live child must retain ownership");
        assert_eq!(existing.mode, "cli");
        assert_eq!(existing.pid, pid);
        let message = python_conflict_message(&existing);
        assert!(message.contains("cli"));
        assert!(message.contains(&pid.to_string()));
        assert!(message.contains("session-test"));
        let entry = owned.get_mut("cli").expect("owned child");
        let _ = entry.child.kill().await;
        let _ = entry.child.wait().await;
    }
}
