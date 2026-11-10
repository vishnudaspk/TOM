# TOM Control Center

## Purpose and boundaries

The Control Center is an optional Windows Tauri 2 shell around the existing TOM runtime. Its Rust process supervisor is the only part of the GUI allowed to launch managed executables. TOM remains usable independently through `python -m tom.main` and its existing voice/CLI entry points. This application does not add a model, remote service, permission pathway, or TOM IPC protocol.

## Architecture

```text
Svelte 5 + TypeScript
       │ typed invoke commands / TOM events
       ▼
Tauri 2 Rust supervisor
       ├─ owned child processes and bounded logs
       ├─ loopback health probes (llama.cpp, wheel backend)
       ├─ TOM Engine Named Pipe NDJSON v1 ping
       └─ CPU/RAM and owned process telemetry
             │
             ├─ rust/tom-engine/target/release/tom-engine.exe
             ├─ .venv/Scripts/python.exe -m tom.main --cli / --voice
             ├─ configured local llama-server + existing GGUF
             └─ configured AutoHotkey v2 executable + desktop/tom_wheel.ahk
```

The Svelte frontend polls inexpensive status every five seconds and receives child stdout/stderr via the `tom-log` Tauri event. It does not spawn processes. Logs are held in memory, bounded to 2,000 entries in the backend and 1,500 in the frontend. The UI does not collect or upload telemetry.

## Process ownership and lifecycle

The supervisor tracks `tokio::process::Child` handles and their PIDs for processes it starts. A healthy engine/model/backend found outside the supervisor is shown as healthy without adoption or a PID claim. An engine already answering `engine.ping` prevents a duplicate engine launch. A healthy model endpoint likewise prevents a second server launch. The supervisor never discovers a process by executable name and kills it.

Python session IDs `python` and `cli` are mutually exclusive because TOM exposes one authoritative Python runtime per `TomApp` process. Voice is a capability inside that runtime, not a third launched mode. Each needs a healthy engine first. `Start TOM` starts the engine, waits up to 20 seconds for the actual v1 ping, starts the configured model only when both local paths are supplied and its endpoint is down, waits up to 180 seconds for model health, then starts the selected Python mode. An endpoint already healthy is treated as externally owned and is not stopped later. Partial failures are reported; an engine failing its startup check is rolled back.

Stopping an engine or the model is refused while a supervisor-owned Python session is active. CLI stop closes stdin and gives TOM two seconds to exit; other owned processes are terminated through their child handles. Closing the window uses the same reverse-order cleanup. The existing engine protocol has no shutdown request, and the voice runtime has no external stop endpoint, so those processes cannot receive a guaranteed in-process graceful shutdown through current TOM APIs. They are terminated only when this supervisor launched them.

## Dependency map

```text
Rust Engine (existing named pipe, NDJSON v1)
  └─ Python Runtime (service or CLI, one session at a time)
       ├─ local llama.cpp server for inference (may be externally owned)
       └─ wheel HTTP backend (started internally by TOM CLI/voice)
            └─ AutoHotkey v2 Command Wheel
```

The model is optional for process startup so diagnostics and startup remain available while it is unloaded. Inference itself requires the configured local llama.cpp service. STT and Kokoro are initialized by the existing voice startup path, not by opening the Control Center.

## Existing TOM integrations

* **Rust Engine:** a fresh Windows named-pipe connection sends `{id, version: 1, method: "engine.ping", params: {}}`; readiness requires a successful response with `data.pong == true`. The app does not implement an alternative protocol. Engine audio, hotkey, and detailed telemetry APIs are not yet surfaced in the UI.
* **Python Runtime:** service mode invokes the existing `.venv` Python executable with `-m tom.main --service --headless`; CLI mode uses `--cli`. `--no-engine` is deliberately not used for normal sessions. CLI stdin is writable from its terminal page, and stdout/stderr are streamed. The Voice pipeline, Faster-Whisper STT, and Kokoro TTS are managed inside this runtime through the authenticated API; opening Control Center does not start them.
* **Model:** calls the existing local server health and `/v1/models` APIs. Launch arguments are an argv vector bound to `127.0.0.1`, with configured context and GPU layers. No model file or server executable exists in this checkout, so defaults leave both paths empty. Configure the already-installed files manually; no download or VRAM availability claim is made.
* **Command Wheel:** launches the configured AutoHotkey v2 executable with the existing script. It requires the existing wheel backend `/health` response first. The backend is part of the Python CLI/voice runtime and remains inside its existing permission-aware executor path.
* **Memory:** Overview reports the existing `data/memory/tom.db` file presence and size. It does not open, migrate, mutate, or reveal memory content. SQLite connection integrity and Qdrant health are not currently probed.
* **Tools:** the page describes the existing permission boundary but does not dynamically inspect the Python registry. No direct tool invocation or permission override exists in the GUI.

## Configuration and security

Version 1 configuration is stored at `%APPDATA%\TOM\ControlCenter\config.json` (with a repository-local fallback if `APPDATA` is unavailable). Existing valid files are loaded as-is. A malformed existing file is preserved and blocks saving rather than being silently replaced. The Settings page permits manual paths and model parameters. Service URLs are parsed and restricted to HTTP loopback hosts (`127.0.0.1`, `localhost`, or `::1`). Process arguments use `Command::args`; there is no shell command construction. Tauri capabilities grant core window functionality only; filesystem and shell plugins are not enabled.

The repository path is discovered from the launch working directory by locating `pyproject.toml` and `rust/tom-engine`; if the packaged app is launched from outside the checkout, set the repository path in Settings before starting TOM. Process defaults point at this checkout's `.venv`, Rust release executable, and existing wheel script.

## Telemetry and diagnostics

CPU and RAM values come from `sysinfo`; the active process list comes from child handles owned by this supervisor. GPU utilization and VRAM are displayed as unavailable because this adapter does not query an authoritative GPU source. Endpoint status is checked from actual responses, and the engine status comes from IPC. stdout/stderr and supervisor lifecycle messages are timestamped and bounded. The current UI supports copying the visible log buffer; diagnostic file export and severity filtering are not implemented yet.

## Build and run on Windows

Prerequisites: Windows 11, Node.js/npm, Rust stable with the MSVC target, Visual Studio C++ Build Tools, and WebView2 Runtime.

```powershell
cd desktop/control-center
npm ci
npm run check
npm run build
npm run tauri dev
```

To create the Windows installer and bundled application:

```powershell
npm run tauri build
```

Tauri's output is written under `src-tauri/target/release/bundle/` (NSIS/MSI where the corresponding bundler tools are installed). The directly built executable is under `src-tauri/target/release/tom-control-center.exe`.

## Windows acceptance checks

1. Launch `src-tauri/target/release/tom-control-center.exe` and confirm Overview loads while all managed services remain untouched.
2. Confirm engine state uses a named-pipe `engine.ping`; model and wheel states follow `/health` responses. CPU/RAM readings are local machine samples; GPU/VRAM must read unavailable.
3. In Settings, point to the already-installed Python, engine, llama-server, GGUF, and AutoHotkey v2 files. Save and reopen to confirm persistence.
4. With no active TOM session, start the engine and confirm it reaches Ready. Start CLI and verify a prompt/response is streamed; stop CLI and confirm EOF shutdown.
5. With the correct local model paths configured, start the model and wait for `/health`; verify a second model launch is refused.
6. Start the Python Service only when microphone/audio acceptance is intended. Open Voice and start the pipeline explicitly; load and test STT or Kokoro independently while the pipeline is stopped.
7. Start the wheel backend through TOM CLI or voice first, then start the AHK script. Confirm the backend health probe and script PID; stop only the supervisor-owned script.
8. Attempting to stop engine/model while a managed Python session is active must return the dependency reason. Stop TOM and confirm only supervisor-owned PIDs exit.

Only the executable window launch and idle dashboard rendering were exercised during this implementation. TOM services were not started or stopped during build validation; steps 2–8 remain operator acceptance checks.

## Validation performed (2026-11-10)

* `cargo fmt --check`: passed.
* `cargo clippy -- -D warnings`: passed.
* `cargo test`: 3 passed; tests cover loopback URL validation, empty model defaults, and invalid saved endpoint fallback.
* `npm run check`: passed with 0 errors and 0 warnings.
* `npm run build`: passed.
* `npm audit --omit=dev`: 0 production vulnerabilities.
* `npm run tauri build`: passed; produced the Windows executable, NSIS installer, and MSI installer.
* Packaged executable launched and created a titled Windows window. A final rendered-screen check was blocked by Windows foreground-focus restrictions, so interactive visual behavior is not claimed. No TOM runtime services were started or stopped during acceptance.

## Known limitations

The current application provides real process supervision, engine ping, model/backend health, CLI interaction, wheel launch, configuration, logs, CPU/RAM telemetry, and memory-file metadata. It is a functional foundation but does **not** implement every requested Control Center subsystem yet:

1. Physical VRAM release after STT unload is unconfirmed, and live microphone/speaker acceptance has not been performed. The authenticated API exposes bounded operations but no full audio-device inventory.
2. Tool registry/permission availability and last-execution diagnostics are not exported by TOM; the GUI does not mirror those records.
3. Rust IPC detail views (audio devices/status, hotkeys, engine telemetry) are not yet implemented beyond the v1 ping.
4. No GPU/VRAM telemetry or preflight admission check is implemented. GPU layers are configurable but default to zero; the app does not claim memory headroom.
5. Python readiness comes from authenticated `/api/v1/status`; model and wheel endpoints remain separate probes.
6. Start TOM launches the selected Python Service or CLI; Voice is started from its capability page, not as a separate process.
7. SQLite file metadata is shown; a read-only SQLite connectivity probe, Qdrant status, configuration reset/path picker, severity filtering, and diagnostics export remain unavailable.
8. Engine and Python shutdown use existing or added endpoints where available; force-stop remains scoped to supervisor-owned PIDs, and externally launched processes are never killed.

These are interface gaps in current TOM components, not simulated capabilities. The Control Center does not alter voice behavior or introduce parallel implementations to fill them.

## Alpha 2 integration status ? 2026-11-10

This section supersedes earlier statements above where they describe the former Alpha 1 behavior. Opening Control Center does not start services. `Start TOM` requires an explicit Python Service or CLI selection; it starts the Engine dependency, optionally starts the configured local llama.cpp server, then starts only that Python mode. Python modes are mutually exclusive.

### Python management API

`python -m tom.main --service --headless` uses the existing `TomApp` configuration, lifecycle manager, tool registry, model provider, and orchestrator without starting CLI or voice interaction. The Rust supervisor chooses an ephemeral loopback port and 256-bit random bearer token per launch, passes them through environment variables, and waits up to 30 seconds for authenticated readiness. Python binds `127.0.0.1` only. Version 1 routes are `GET /api/v1/status`, `/tools`, `/memory`, `POST /api/v1/voice/{operation}`, and `POST /shutdown`. Requests are bounded to 64 KiB and every route requires constant-time token comparison. This interface cannot execute tools or change permissions. Managed CLI and Voice launches also start the API; standalone CLI/Voice without supervisor credentials retain existing behavior.

Status distinguishes Python orchestration readiness from process existence; Engine telemetry and model endpoint health are probed with bounded timeouts. STT/TTS export existing provider metadata and loaded/unloaded state without forcing model loads. Voice state reports pipeline lifecycle, microphone state, and reusable provider snapshots; unloaded providers are expected while the pipeline is stopped. Tool metadata comes from the live registry, including permission classification and schema; execution history is explicitly untracked. SQLite uses a read-only URI, query-only mode, a 300 ms busy timeout, and `quick_check`; it does not create a missing database. Qdrant is probed only when configured for loopback and remains optional.

### Lifecycle and shutdown

Python start succeeds only after the authenticated API responds. Python stop posts `/shutdown`, waits eight seconds, then force-stops only its owned child. Engine stop sends `engine.shutdown` through existing NDJSON v1, waits six seconds, then falls back to the supervisor-owned child. The Engine handler waits 100 ms after the successful response before cancelling its existing root lifecycle token. Healthy external Engine/model/wheel endpoints are not adopted or stopped. Closing the Control Center prompts to keep TOM running, stop managed components, or cancel. Children kept running after supervisor exit become unowned; fixed endpoint probes report them as external.

Starting a full TOM profile rolls back only components started during that operation if a later dependency fails. A healthy model endpoint blocks duplicate launches. Model paths and executable are validated before launch; argv invocation is bound to loopback.

### GPU, model runtime, and scope limits

CPU/RAM use local `sysinfo`. GPU telemetry invokes `nvidia-smi` directly with a fixed argument vector and a two-second bound; missing tooling reports an unavailable reason. This release does not implement model VRAM preflight, configurable safety margins, context/KV-cache estimates, expected-model identity checks, or voice/model GPU allocation policy. No model/server paths are guessed and nothing is downloaded.

Implemented and checked: authenticated Python management/readiness; service mode and explicit launch mode; live tool registry and permissions export; read-only SQLite and optional loopback Qdrant checks; graceful Python/Engine stop; backend Engine diagnostic command; local NVIDIA telemetry; close policy; NSIS/MSI packaging. The Engine diagnostics command covers `engine.status`, audio status/devices, hotkeys, and `system.all`, but detailed results are not rendered in the Rust Engine page.

Not implemented: Windows Job Objects/process-tree cleanup, durable lifecycle history/operation IDs, diagnostics export and rich filters, browse/reset settings, full model VRAM admission policy, and a future vision runtime. Voice provider load/unload and bounded microphone/TTS tests are implemented. No live TOM Engine, voice, model, Qdrant, microphone, or screen interaction was started for validation. Installer execution and rendered-screen acceptance remain operator checks.

## Control Center Alpha 2.1 � voice operations and resource safety (2026-11-10)

The authenticated loopback API now exposes a narrow voice-operation route for status, explicit STT/TTS load and unload, bounded microphone transcription, and bounded Kokoro synthesis/playback. The GUI exposes these operations and disables independent tests while the full Voice pipeline is active. Operations use the same lazy Faster Whisper and Kokoro provider instances used by TOM Voice. STT admission rejects CUDA loading if VRAM telemetry is unavailable or the configured ResourceManager budget fails; independent STT operations disable CPU fallback temporarily so the selected device is honored. Provider locks serialize inference and lifecycle actions. A timed-out native worker prevents unloading while it may still use the model. Unload reports logical residency cleared, while physical memory release remains unconfirmed.

The Engine playback handler test now uses an in-memory playback queue and no longer opens a real system audio device. This makes the dispatcher timeout test deterministic; production playback is unchanged.

Validation: focused voice/API pytest suite (115 passed); Ruff clean; Engine tests (96 unit, 7 integration) and Clippy clean; Control Center Rust tests (4 passed) and Clippy clean; Svelte check (0 errors/warnings); production npm audit (0 vulnerabilities). Windows bundle is being rebuilt after these changes. No live microphone, speaker, TOM service, installer, or interactive screen acceptance was performed.

Still outstanding from the Alpha 2.1 request: full process-tree ownership via Windows Job Objects, durable lifecycle history/operation IDs, native path browse/reset controls, severity-filtered diagnostics and export, broader GPU admission/preflight covering all model classes, and interactive Windows/hardware acceptance. Do not infer physical VRAM release from provider unload.

Final Alpha 2.1 verification update (2026-11-10): the complete `tests/unit` suite passed (1906 passed, 4 skipped); repository-wide collection remains unavailable because optional WS2 benchmark tests require missing `sherpa_onnx`. `git diff --check` is clean. `npm run tauri -- build` completed and produced EXE, NSIS, and MSI artifacts. No installer or live audio acceptance was run.

## Control Center Alpha 3 - authoritative Python runtime hierarchy (2026-11-10)

This section supersedes earlier component lists where they describe `brain` or a separately launched Voice process. The Control Center now models the actual runtime hierarchy:

* **Processes** (`kind: process`): Rust Engine (`engine`), AI Model Runtime (`model`), Python Runtime (`python`, launched as `--service --headless`), TOM CLI (`cli`), and Command Wheel (`wheel_script`). Only these IDs can be started, restarted, or stopped by the supervisor.
* **Capabilities** (`kind: capability`): Voice Pipeline (`voice`), Faster-Whisper STT (`stt`), and Kokoro CPU TTS (`tts`). These have no owned PID and no independent process. Their state is derived from the authoritative Python `/api/v1/status` response. They remain visible as stopped/unloaded when Python is not running, and the UI never presents that expected state as a failure.
* **Future capability** (`kind: placeholder`): Vision Model (`vision`), reported as `unsupported`. It has no executable, model, management route, VRAM reservation, or download path.

The Python Runtime is the single authoritative process for text and voice orchestration. Voice is no longer a launch mode in the Control Center. The Voice page can explicitly start and stop the pipeline through `start_pipeline`/`stop_pipeline`, and can load, unload, and test STT and Kokoro independently only while the pipeline is stopped. Pipeline start reuses the same STT/TTS provider instances and never loads a second model. The microphone starts only on an explicit pipeline-start request.

STT/TTS lifecycle actions, microphone transcription, Kokoro synthesis/playback, CUDA budget admission, and provider locks are unchanged from Alpha 2.1. The Rust supervisor whitelist now also accepts `start_pipeline` and `stop_pipeline`; no tool execution or permission bypass is exposed. `start_tom` accepts only `python` or `cli`, and `spec("brain")`/`spec("voice")` are rejected.

Validation for this hierarchy: `cargo test --bin tom-control-center` (12 passed); focused `tests/unit/desktop/test_voice_operations.py` (8 passed); `tests/unit/desktop` (83 passed); `ruff check` on changed Python files; `cargo fmt --check`/`cargo clippy --all-targets -- -D warnings`; `svelte-check` (0 errors/warnings); Vite production build; `git diff --check` (CRLF warnings only). No live Python service, microphone, speaker, model, or vision runtime was started.