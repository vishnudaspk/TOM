# TOM — Beta Testing Guide & Operations Manual

This guide provides step-by-step instructions for launching TOM, running health diagnostics, and conducting manual and automated beta testing on Windows 11.

---

## 1. Prerequisites & Environment Assumptions

### Hardware & Operating System
- **OS**: Windows 11 (x64)
- **CPU**: Intel Core i9-13900H (or equivalent x86_64 CPU)
- **RAM**: 16 GB Physical RAM (Warning: Health check alerts if available RAM < 2.0 GB)
- **GPU**: NVIDIA GeForce RTX 4060 Laptop GPU (8 GB VRAM; 7,000 MB configured software budget)

### Software & Toolchains
- **Python**: 3.11.x in repository virtual environment (`.venv`)
- **Rust / Cargo**: Stable toolchain (1.80+) for building `rust/tom-engine`
- **llama.cpp**: Local HTTP inference server running at `http://127.0.0.1:65400/v1`
  - Loaded model: `Qwen2.5-7B-Instruct` (Q4_K_M or equivalent)
  - Context size: `n_ctx` >= 4096 (budgeted in TOM config)

---

## 2. Launch Sequence & Execution Order

All components must be launched in the exact order below. Open separate PowerShell windows for long-running processes (or run background tasks as noted).

### Step 1: Start the Local LLM Server (llama.cpp)
Verify the inference server is running and accessible:
```powershell
# In PowerShell:
Invoke-RestMethod -Uri "http://127.0.0.1:65400/v1/models" -Method Get
```
*Expected Output*: Returns JSON containing model data with `id: "qwen2.5-7b-instruct"` (or active model name).

### Step 2: Build & Start the Rust Engine (`tom-engine`)
The Rust engine owns audio capture/playback, low-level OS input via `SendInput`, and hardware telemetry. It communicates with Python over Named Pipe `\\.\pipe\tom-engine`.

```powershell
# Build tom-engine (if not already compiled):
cargo build --manifest-path rust/tom-engine/Cargo.toml

# Start the engine in a dedicated PowerShell terminal:
.\rust\tom-engine\target\debug\tom-engine.exe
```
*Expected Output*:
```
INFO tom_engine: Starting TOM Engine engine="tom-engine" version="0.1.0"
INFO tom_engine::ipc::server: Initializing Windows Named Pipe server pipe="\\.\pipe\tom-engine"
DEBUG tom_engine::ipc::server: Waiting for client connection on named pipe pipe="\\.\pipe\tom-engine"
```

### Step 3: Verify Health Diagnostics
In your main terminal, run TOM's comprehensive diagnostic suite:
```powershell
cd C:\Users\vishnuu\Projects\TOM
.\.venv\Scripts\python.exe -m tom.main --health --verbose
```
*Expected Output*:
```
Check                Status       Details
--------------------------------------------------------------------------------
Engine IPC           HEALTHY      Engine responsive (version: 0.1.0)
Inference Endpoint   HEALTHY      Endpoint reachable and responding
GPU Telemetry        HEALTHY      VRAM headroom: ~3400 MB (total: 8188 MB, used: ~4788 MB)
System RAM           HEALTHY/DEG  System memory state and headroom
Model States         HEALTHY      No models registered
```

---

## 3. Supported Execution Modes & Commands

### 1. Interactive CLI Mode (`--cli`)
For text-based conversational interaction and deterministic tool use:
```powershell
.\.venv\Scripts\python.exe -m tom.main --cli
```
- In non-verbose mode (default), structured JSON telemetry is diverted to logs; stdout shows clean banner and prompt turns.
- Type `exit` or `quit` to exit cleanly.

### 2. Verbose CLI Mode (`--cli --verbose`)
Displays real-time structured telemetry (IPC state transitions, token streaming events, lease allocations) directly on the console:
```powershell
.\.venv\Scripts\python.exe -m tom.main --cli --verbose
```

### 3. Voice Interaction Mode (`--voice`)
Launches the full real-time speech pipeline (MicrophoneLoop → VAD → FasterWhisper STT → Agent Turn Manager → Kokoro TTS → Audio playback):
```powershell
.\.venv\Scripts\python.exe -m tom.main --voice
```
*Requirements*: Requires microphone and speaker devices accessible by the OS and `faster-whisper` installed.

### 4. Headless Proactive Mode (`--headless`)
Runs background task orchestration, scheduler, and memory maintenance without user terminal interaction:
```powershell
.\.venv\Scripts\python.exe -m tom.main --headless
```

---

## 4. Feature Readiness & Verification Matrix

| Subsystem / Feature | Automated Status | Live Integration Status | Limitations & Notes |
|---|---|---|---|
| **Health Check (`--health`)** | **PASS** (37/37 unit tests pass) | **PASS** (Verified with live engine & llama-server) | Exit 0 if all healthy, exit 1 if degraded (e.g. RAM), exit 2 if failed. |
| **Engine IPC (`tom-engine`)** | **PASS** (Rust cargo check clean) | **PASS** (Named Pipe ping/gpu/memory verified) | Communicates over `\\.\pipe\tom-engine`. |
| **Model Connectivity (llama.cpp)** | **PASS** (Provider unit tests pass) | **PASS** (Verified live streaming & multi-turn) | Connects to `http://127.0.0.1:65400/v1`. |
| **Multi-turn CLI Dialogue** | **PASS** (E2E live tests pass) | **PASS** (Context continuity verified) | `test_cli_real_path.py` verified 2-turn memory. |
| **Deterministic Tool Dispatch** | **PASS** (34 canonical tools verified) | **PASS** (File, personal, web, system tools) | Enforces path confinement and permission prompts. |
| **Secret Scrubbing & Safety** | **PASS** (101 adversarial tests pass) | **PASS** (Entropy pattern ignores file paths) | Redacts secrets, preserves typed `BaseModel` outputs. |
| **Resource & VRAM Governor** | **PASS** (105 resource tests pass) | **PASS** (Headroom & heavy-lock mutual exclusion) | 500 MB safety headroom, 7,000 MB allocation cap. |
| **Voice STT (FasterWhisper)** | **PASS** (52 unit tests pass) | **MANUAL TEST REQUIRED** | Requires real microphone; offline model on disk. |
| **Voice TTS (Kokoro CPU)** | **PASS** (Unit tests pass) | **MANUAL TEST REQUIRED** | Requires real speaker playback through engine ringbuffer. |
| **Voice Turn Arbitration** | **SKIPPED** | **UNAVAILABLE** | `pipecat` is not installed; VAD remains authoritative. |
| **Computer Vision (OCR/CV)** | **SKIPPED** | **UNAVAILABLE** | `opencv-python` / `winocr` optional dependencies omitted. |
| **System Tray / Desktop UI** | **UNVERIFIED** | **MANUAL TEST REQUIRED** | Desktop UI requires Windows display session. |

---

## 5. Sequential Manual Beta-Testing Checklist

For tests requiring user interaction, record results in the observed fields below:

### Test 1: Cold Boot & Diagnostics
- **Action**: Run `.\.venv\Scripts\python.exe -m tom.main --health --verbose` with engine running.
- **Expected Result**: Table output with all checks reported. Engine IPC and Inference Endpoint are `HEALTHY`.
- **Pass/Fail**: [ ] PASS  [ ] FAIL
- **Observed Result**: _________________________________________________
- **Log Location**: Console stdout / `logs/tom.log`

### Test 2: Multi-Turn Conversational Memory
- **Action**: Run `.\.venv\Scripts\python.exe -m tom.main --cli`.
  - Turn 1: "My favorite color is cerulean."
  - Turn 2: "What is my favorite color?"
- **Expected Result**: Response identifies "cerulean" based on multi-turn context retention.
- **Pass/Fail**: [ ] PASS  [ ] FAIL
- **Observed Result**: _________________________________________________
- **Log Location**: `logs/tom.log`

### Test 3: Tool Execution & Interactive Confirmation
- **Action**: In `--cli`, request a write action: "Write a note with title 'Shopping' and content 'Milk and bread'".
- **Expected Result**: Tool authorization prompt requests confirmation for `personal.note` mutation. Upon approving with `y`, note is committed to SQLite store.
- **Pass/Fail**: [ ] PASS  [ ] FAIL
- **Observed Result**: _________________________________________________
- **Log Location**: `logs/tom.log`

### Test 4: Path Jail & Security Enforcement
- **Action**: In `--cli`, ask: "Read the file C:\Windows\System32\drivers\etc\hosts".
- **Expected Result**: Tool executor denies access via `PathPolicy` jail boundary; no arbitrary directory escape.
- **Pass/Fail**: [ ] PASS  [ ] FAIL
- **Observed Result**: _________________________________________________
- **Log Location**: `logs/tom.log`

### Test 5: Speech Input & Audio Playback (Hardware Required)
- **Action**: Start `.\.venv\Scripts\python.exe -m tom.main --voice`. Speak into microphone: "What time is it?".
- **Expected Result**: MicrophoneLoop captures speech, VAD marks boundaries, STT transcribes, TOM speaks reply through Kokoro TTS.
- **Pass/Fail**: [ ] PASS  [ ] FAIL
- **Observed Result**: _________________________________________________
- **Log Location**: `logs/tom.log`

### Test 6: Graceful Shutdown
- **Action**: Press `Ctrl+C` in running `--cli` or `--voice` session.
- **Expected Result**: IPC client transitions `connected -> closed`, file handles flush, process exits with code 0 without traceback.
- **Pass/Fail**: [ ] PASS  [ ] FAIL
- **Observed Result**: _________________________________________________
- **Log Location**: Console output

---

## 6. Troubleshooting & Common Pitfalls

1. **`Named pipe not found: '\\.\pipe\tom-engine'`**:
   - Cause: The Rust `tom-engine` binary is not running.
   - Solution: In a separate PowerShell terminal, launch `.\rust\tom-engine\target\debug\tom-engine.exe`.
2. **`Endpoint unreachable or returned non-200`**:
   - Cause: `llama.cpp` server is not running at `http://127.0.0.1:65400`.
   - Solution: Start your local llama-server instance on port 65400.
3. **`Low available RAM: X GB (threshold: 2 GB)`**:
   - Cause: System physical memory under contention.
   - Solution: Close heavy browser tabs or background applications.
4. **Secret Redaction of File Paths**:
   - Verified Fixed: Path separators `/` and `\` are recognized as delimiters, preventing legitimate Windows absolute file paths from triggering Shannon entropy redaction.
