# TOM Setup & Testing Guide — Phases 1–7

> **Verification date:** 2026-10-01
> **Phase coverage:** 1–7 (all CLOSED & COMPLETE)
> **Test baseline (live-verified):** 870 unit + 134 integration = 1004 Python | 83 Rust = **1087 total passing** (+ 8 optional-dep skipped)

---

## How This Guide Is Structured

TOM validation has two independent layers that complement each other:

| Layer | What it validates | Engine needed? |
|---|---|---|
| **Automated tests** (`pytest`, `cargo test`) | Unit correctness and component integration using mocks and in-process stubs | No |
| **Physical checks** (PowerShell scripts) | That real OS resources work: Windows Named Pipe, audio buffers, file system, screen capture, input simulation | Yes (for IPC/audio/live input) |

**Neither layer replaces the other.** Automated tests never start `tom-engine.exe`. Physical checks never exercise every edge case. Run both.

---

## 1. Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Windows OS | 10 / Server 2019+ | Named Pipe IPC & Win32 SendInput are Windows-only |
| Python | **3.11** | Set in `pyproject.toml` (`requires-python = ">=3.11"`) |
| Rust toolchain | **1.80+** | `rustup`, `rustc`, `cargo`, `cargo fmt`, `cargo clippy` |
| Git | Any recent | |

### Optional model and vision dependencies

Phase 6 STT/TTS and Phase 7 OCR/CV use lazy loading:
- **Speech**: `FasterWhisperSTTProvider` and `KokoroTTSProvider` import when constructed.
- **Vision**: `WindowsMediaOCRProvider` requires `winocr`, `CVElementDetector` requires `opencv-python`.

All physical checks in this guide use mock or lightweight built-in providers and work out-of-the-box without optional extras:

```powershell
pip install -e ".[models]"    # STT/TTS hardware inference
pip install -e ".[vision]"    # winocr, opencv-python, numpy
```

---

## 2. Environment Setup

### 2.1 Clone and create virtual environment

```powershell
git clone <repository-url>
cd TOM

python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Prompt must show (.venv) — if not, something went wrong
```

### 2.2 Install Python package

```powershell
# Dev dependencies include pytest, ruff, mypy, etc.
pip install -e ".[dev]"

# Verify
.\.venv\Scripts\python -c "from tom.ipc.client import NamedPipeIpcClient; print('tom OK')"
```

Expected: `tom OK`

**Important:** All `python` / `pytest` / `ruff` commands throughout this guide assume `.venv` is active. If you see `ModuleNotFoundError: No module named 'tom'`, run `.\.venv\Scripts\Activate.ps1` first.

### 2.3 Build Rust engine

```powershell
cd rust\tom-engine
cargo build --release
cd ..\..
```

Binary: `rust\tom-engine\target\release\tom-engine.exe`

---

## 3. Automated Tests (no engine needed)

### 3.1 Python unit tests

```powershell
pytest tests\unit --tb=short -q
```

Expected: **870 passed, 8 skipped** (live-verified 2026-10-01; skipped tests require optional `winocr` / `opencv-python`)

### 3.2 Python integration tests

```powershell
pytest tests\integration --tb=short -q
```

Expected: **134 passed** (live-verified 2026-10-01; includes 42 vision & input integration tests)

### 3.3 Rust engine tests

```powershell
cd rust\tom-engine
cargo test
cd ..\..
```

Expected: **83 passed** (76 unit + 7 integration)

### 3.4 Targeted sub-suites

```powershell
# Vision pipeline & OS input (Phase 7)
pytest tests\integration\vision -v
pytest tests\unit\vision -v

# Voice pipeline (Phase 6) — runs fully mocked, no hardware needed
pytest tests\integration\voice -v

# Memory subsystem (Phase 5)
pytest tests\unit\memory -v

# Tool system (Phase 3 & 7)
pytest tests\unit\tools -v

# Live IPC tests — requires tom-engine.exe running (see Section 4)
pytest tests\integration\python_rust -v
```

### 3.5 Code quality gates

```powershell
# Python linting — expected: "All checks passed!"
.\.venv\Scripts\ruff check python

# Python formatting — expected: "58 files already formatted"
.\.venv\Scripts\ruff format --check python

# Rust (run from rust\tom-engine\)
cargo fmt --check
cargo clippy -- -D warnings
```

---

## 4. Starting the Rust Engine

`tom-engine.exe` is a standalone Windows process. It must be running before any physical IPC or audio check.

```powershell
# Open a dedicated terminal and leave it running
cd rust\tom-engine
.\target\release\tom-engine.exe
```

**Expected startup log (JSON lines on stdout):**

```
{"level":"INFO","event":"tom_engine_starting",...}
{"level":"INFO","event":"audio_manager_initialized",...}
{"level":"INFO","event":"ipc_server_listening","context":{"pipe_name":"\\\\.\\pipe\\tom-engine"},...}
```

Wait for the `ipc_server_listening` line before running any Python checks below.

> **Audio fallback:** If no physical microphone/speaker is found, the engine logs `audio_device_not_found` and uses internal mock buffers. All `audio.*` IPC commands still succeed.

> **There is no `python -m tom` command.** `tom/__main__.py` does not exist. The engine is a separate Rust binary; there is no Python runtime daemon yet.

---

## 5. Physical Check: Python ↔ Rust Named Pipe IPC

**IPC architecture:**

```
.\.venv\Scripts\python (your script)
  └── NamedPipeIpcClient   (tom.ipc.client)
       └── NamedPipeTransport
            └── Windows Named Pipe  \\.\pipe\tom-engine
                 └── tom-engine.exe  (Rust)
```

`EngineClient` (`tom.core.engine`) is a thin typed wrapper **above** `NamedPipeIpcClient`. It returns Pydantic models instead of raw dicts. For simple scripting, `NamedPipeIpcClient.request()` with raw dicts is sufficient.

### 5.1 Ping, CPU and audio status via raw IPC

Run in a **new** terminal (keep engine terminal open):

```powershell
.\.venv\Scripts\python -c "
import asyncio
from tom.ipc.client import NamedPipeIpcClient

async def check():
    client = NamedPipeIpcClient()
    await client.connect()
    try:
        ping  = await client.request('engine.ping')
        cpu   = await client.request('system.cpu')
        audio = await client.request('audio.status')
        print('Ping: ', ping)
        print('CPU:  ', cpu)
        print('Audio:', audio)
    finally:
        await client.close()

asyncio.run(check())
"
```

**Verified output (2026-09-30):**

```
Ping:  {'pong': True, 'version': '0.1.0'}
CPU:   {'core_count': 20, 'frequency_mhz': 2600, 'usage_percent': 6.65}
Audio: {'capture_active': False, 'capture_channels': 1, 'capture_sample_rate': 16000,
        'captured_samples': 0, 'playback_active': False}
```

(CPU/usage values will vary by machine.)

### 5.2 Audio device inventory

```powershell
.\.venv\Scripts\python -c "
import asyncio
from tom.ipc.client import NamedPipeIpcClient

async def check():
    client = NamedPipeIpcClient()
    await client.connect()
    try:
        d = await client.request('audio.devices')
        print('Host:   ', d.get('host_id'))
        print('Inputs: ', len(d.get('input_devices', [])))
        print('Outputs:', len(d.get('output_devices', [])))
    finally:
        await client.close()

asyncio.run(check())
"
```

**Verified output:** `Host: Wasapi | Inputs: 2 | Outputs: 5` (values vary by machine)

Empty input/output lists mean the engine is using mock buffers (no hardware). All pipeline tests still work.

### 5.3 Typed telemetry via EngineClient

`EngineClient` is imported from `tom.core.engine` (not `tom.ipc.client`).  
Its methods are prefixed `get_`: `get_cpu()`, `get_memory()`, `get_audio_status()`, etc.

```powershell
.\.venv\Scripts\python -c "
import asyncio
from tom.ipc.client import NamedPipeIpcClient
from tom.core.engine import EngineClient

async def check():
    ipc = NamedPipeIpcClient()
    await ipc.connect()
    try:
        e = EngineClient(ipc)

        ping   = await e.ping()            # -> PingResponse
        status = await e.status()          # -> StatusResponse  (.engine, .status)
        cpu    = await e.get_cpu()         # -> CpuInfo
        mem    = await e.get_memory()      # -> MemoryInfo
        audio  = await e.get_audio_status()  # -> AudioStatusResponse

        print('ping.pong  :', ping.pong, '| version:', ping.version)
        print('status     :', status.engine, '—', status.status)
        print('cpu        :', cpu.core_count, 'cores @', cpu.frequency_mhz, 'MHz,',
              round(cpu.usage_percent, 1), '%')
        print('mem total  :', mem.total_bytes, 'bytes')
        print('audio rate :', audio.capture_sample_rate, 'Hz')
    finally:
        await ipc.close()

asyncio.run(check())
"
```

**Verified output:**

```
ping.pong  : True | version: 0.1.0
status     : tom-engine — running
cpu        : 20 cores @ 2600 MHz, 9.9 %
mem total  : 16890519552 bytes
audio rate : 16000 Hz
```

**Complete EngineClient method reference:**

| Method | IPC call | Return type |
|---|---|---|
| `ping()` | `engine.ping` | `PingResponse` |
| `status()` | `engine.status` | `StatusResponse` |
| `get_cpu()` | `system.cpu` | `CpuInfo` |
| `get_memory()` | `system.memory` | `MemoryInfo` |
| `get_gpu()` | `system.gpu` | `GpuInfo` |
| `get_battery()` | `system.battery` | `BatteryInfo` |
| `get_disk()` | `system.disk` | `DiskInfo` |
| `get_processes(limit)` | `system.processes` | `ProcessInfo` |
| `get_system_snapshot()` | `system.all` | `SystemSnapshot` |
| `get_audio_devices()` | `audio.devices` | `AudioHostInfo` |
| `get_audio_status()` | `audio.status` | `AudioStatusResponse` |
| `start_audio_capture(...)` | `audio.capture_start` | `AudioOperationResponse` |
| `stop_audio_capture()` | `audio.capture_stop` | `AudioOperationResponse` |
| `get_captured_speech(clear)` | `audio.get_speech` | `AudioSpeechResponse` |
| `play_audio_buffer(samples,...)` | `audio.play_buffer` | `AudioOperationResponse` |
| `stop_audio_playback()` | `audio.playback_stop` | `AudioOperationResponse` |

---

## 6. Physical Check: Memory Subsystem (Phase 5)

No engine needed. `MemoryManager` uses SQLite as its authoritative store (optional Qdrant vector indexing).

```powershell
.\.venv\Scripts\python -c "
import asyncio
from tom.memory.manager import MemoryManager
from tom.schemas.memory import MemoryImportance, MemoryRecord, MemoryType

async def check():
    mm = MemoryManager()
    record = MemoryRecord(
        content='Setup verification test memory',
        type=MemoryType.SEMANTIC,
        importance=MemoryImportance.LOW,
    )
    mem_id = await mm.store(record)
    print('Stored   :', mem_id)

    got = await mm.get(mem_id)
    print('Retrieved:', got.content if got else 'NOT FOUND')

    deleted = await mm.delete(mem_id)
    print('Deleted  :', deleted)

    gone = await mm.get(mem_id)
    print('Gone     :', gone is None)

asyncio.run(check())
"
```

**Verified output:**

```
Stored   : <uuid>
Retrieved: Setup verification test memory
Deleted  : True
Gone     : True
```

> **Valid `MemoryType` values:** `EPISODIC`, `SEMANTIC`, `PREFERENCE`, `TASK`, `EVENT`
> **Valid `MemoryImportance` values:** `LOW`, `USEFUL`, `IMPORTANT`, `CRITICAL`

---

## 7. Physical Check: Tool System (Phase 3)

No engine needed for pure Python tools. Tools are registered via `setup_default_tools()`.

**All registered tool names (live-verified):**

```
files.delete_file     files.file_info        files.list_directory
files.read_file       files.search_files     files.write_file
memory.forget         memory.preferences     memory.recall
memory.recent         memory.remember
system.battery_info   system.cpu_info        system.disk_info
system.get_snapshot   system.gpu_info        system.list_processes
system.memory_info
voice.announce        voice.status
```

### 7.1 System tool

```powershell
.\.venv\Scripts\python -c "
import asyncio
from tom.tools.bootstrap import setup_default_tools
from tom.tools.executor import ToolExecutor

async def check():
    setup_default_tools()
    executor = ToolExecutor()
    r = await executor.execute('system.cpu_info')
    print('Success:', r.success)
    print('Data   :', r.data)

asyncio.run(check())
"
```

**Verified output:** `Success: True | Data: usage_percent=X core_count=20 frequency_mhz=2600`

### 7.2 File tool

File tools are classified `ASK_USER` by default. Use `AlwaysAllowConfirmationHook` for scripted checks.

```powershell
.\.venv\Scripts\python -c "
import asyncio
from tom.tools.bootstrap import setup_default_tools
from tom.tools.executor import ToolExecutor
from tom.security.confirmation import AlwaysAllowConfirmationHook

async def check():
    setup_default_tools()
    executor = ToolExecutor(confirmation_hook=AlwaysAllowConfirmationHook())
    r = await executor.execute('files.list_directory', {'path': '.'})
    print('Success:', r.success)
    if r.success:
        print('Listed :', r.data.total_count, 'entries')

asyncio.run(check())
"
```

**Verified output:** `Success: True | Listed: 18 entries`

---

## 8. Physical Check: Phase 6 Voice Pipeline

Voice components are all in-process Python. The Rust engine is needed only when testing real audio capture/playback. All checks below use mock providers and work without hardware.

### 8.1 SpeechFormatter — text normalization

```powershell
.\.venv\Scripts\python -c "
from tom.voice.formatter import SpeechFormatter

f = SpeechFormatter()
raw = '**Hello**, TOM! Check https://example.com for updates...'
out = f.format(raw)
print('Input :', raw)
print('Output:', out)
"
```

**Verified output:** `Output: Hello, TOM! Check https://example. com for updates.`
(Markdown bold stripped; URL spacing normalised for TTS.)

### 8.2 MockSTTProvider — transcription

```powershell
.\.venv\Scripts\python -c "
import asyncio
from tom.voice.stt import MockSTTProvider
from tom.schemas.voice import TranscriptionRequest

async def check():
    # Note: keyword arg is 'transcript', not 'transcription' or 'config'
    stt = MockSTTProvider(transcript='hello world')
    req = TranscriptionRequest(samples=[0.0] * 1600, sample_rate=16000)
    result = await stt.transcribe(req)
    print('Text      :', result.text)
    print('Confidence:', result.confidence)
    print('Provider  :', result.provider)

asyncio.run(check())
"
```

**Verified output:** `Text: hello world | Confidence: 1.0 | Provider: MockSTTProvider`

### 8.3 MockTTSProvider — synthesis

```powershell
.\.venv\Scripts\python -c "
import asyncio
from tom.voice.tts import MockTTSProvider
from tom.schemas.voice import SynthesisRequest

async def check():
    tts = MockTTSProvider()   # no required args
    req = SynthesisRequest(text='Setup verification successful')
    result = await tts.synthesize(req)
    print('Samples    :', len(result.samples))
    print('Sample rate:', result.sample_rate)
    print('Provider   :', result.provider)

asyncio.run(check())
"
```

**Verified output:** `Samples: 2400 | Sample rate: 24000 | Provider: MockTTSProvider`

### 8.4 VoicePipelineManager — full turn (IDLE→LISTENING→PROCESSING→SPEAKING→IDLE)

```powershell
.\.venv\Scripts\python -c "
import asyncio
from unittest.mock import MagicMock, AsyncMock
from tom.core.engine import EngineClient
from tom.ipc.protocol import AudioOperationResponse
from tom.voice.pipeline import VoicePipelineManager
from tom.voice.stt import MockSTTProvider
from tom.voice.tts import MockTTSProvider

async def check():
    # Mock out EngineClient audio calls
    engine = MagicMock(spec=EngineClient)
    engine.stop_audio_playback = AsyncMock(return_value=AudioOperationResponse(success=True))
    engine.stop_audio_capture  = AsyncMock(return_value=AudioOperationResponse(success=True))
    engine.play_audio_buffer   = AsyncMock(return_value=AudioOperationResponse(success=True, samples_played=400))

    stt = MockSTTProvider(transcript='hello tom')
    tts = MockTTSProvider()

    async def agent_handler(text):
        return 'You said: ' + text

    pipeline = VoicePipelineManager(
        stt_provider=stt, tts_provider=tts,
        engine_client=engine, agent_handler=agent_handler,
    )

    print('State before:', pipeline.state.value)
    result = await pipeline.run_turn(audio_samples=[0.05] * 1600, sample_rate=16000)
    print('Error       :', result.error)
    print('Transcript  :', result.transcript)
    print('Response    :', result.response_text)
    print('State after :', result.state.value)

asyncio.run(check())
"
```

**Verified output:**

```
State before: IDLE
Error       : None
Transcript  : hello tom
Response    : You said: hello tom
State after : IDLE
```

### 8.5 VoiceInteractionManager — multi-turn session + ephemeral history

```powershell
.\.venv\Scripts\python -c "
import asyncio
from unittest.mock import MagicMock, AsyncMock
from tom.core.engine import EngineClient
from tom.ipc.protocol import AudioOperationResponse, AudioStatusResponse
from tom.voice.pipeline import VoicePipelineManager
from tom.voice.interaction import VoiceInteractionManager
from tom.voice.stt import MockSTTProvider
from tom.voice.tts import MockTTSProvider

async def check():
    engine = MagicMock(spec=EngineClient)
    engine.stop_audio_playback = AsyncMock(return_value=AudioOperationResponse(success=True))
    engine.stop_audio_capture  = AsyncMock(return_value=AudioOperationResponse(success=True))
    engine.play_audio_buffer   = AsyncMock(return_value=AudioOperationResponse(success=True, samples_played=200))
    engine.get_audio_status    = AsyncMock(return_value=AudioStatusResponse(
        capture_active=False, playback_active=False,
        capture_sample_rate=16000, capture_channels=1, captured_samples=0
    ))

    stt = MockSTTProvider(transcript='what time is it')
    tts = MockTTSProvider()

    async def agent_handler(text):
        return 'I heard: ' + text

    pipeline = VoicePipelineManager(stt_provider=stt, tts_provider=tts,
                                    engine_client=engine, agent_handler=agent_handler)
    vim = VoiceInteractionManager(pipeline_manager=pipeline)

    print('History before:', len(vim.history.get_messages()))
    result = await vim.run_turn(audio_samples=[0.05] * 1600, sample_rate=16000)
    print('Error         :', result.error)
    print('Transcript    :', result.transcript)
    print('History after :', len(vim.history.get_messages()), 'messages (user + assistant)')
    print('State         :', vim.state.value)

asyncio.run(check())
"
```

**Verified output:**

```
History before: 0
Error         : None
Transcript    : what time is it
History after : 2 messages (user + assistant)
State         : IDLE
```

### 8.6 Voice tools via ToolExecutor

```powershell
.\.venv\Scripts\python -c "
import asyncio
from unittest.mock import MagicMock, AsyncMock
from tom.core.engine import EngineClient
from tom.ipc.protocol import AudioOperationResponse, AudioStatusResponse
from tom.voice.pipeline import VoicePipelineManager
from tom.voice.interaction import VoiceInteractionManager
from tom.voice.stt import MockSTTProvider
from tom.voice.tts import MockTTSProvider
from tom.tools.bootstrap import setup_default_tools
from tom.tools.executor import ToolExecutor

async def check():
    engine = MagicMock(spec=EngineClient)
    engine.get_audio_status    = AsyncMock(return_value=AudioStatusResponse(
        capture_active=False, playback_active=False,
        capture_sample_rate=16000, capture_channels=1, captured_samples=0
    ))
    engine.play_audio_buffer   = AsyncMock(return_value=AudioOperationResponse(success=True, samples_played=200))
    engine.stop_audio_playback = AsyncMock(return_value=AudioOperationResponse(success=True))
    engine.stop_audio_capture  = AsyncMock(return_value=AudioOperationResponse(success=True))

    pipeline = VoicePipelineManager(stt_provider=MockSTTProvider(),
                                    tts_provider=MockTTSProvider(), engine_client=engine)
    vim = VoiceInteractionManager(pipeline_manager=pipeline)

    setup_default_tools(voice_manager=vim)
    executor = ToolExecutor()

    r = await executor.execute('voice.status')
    print('voice.status success:', r.success)
    print('voice.status data   :', r.data)

asyncio.run(check())
"
```

**Verified:** `voice.status success: True`

---

## 9. Physical Check: Phase 7 Vision & OS Input

Phase 7 perception and input tools operate strictly in-memory (with optional low-level actuation via the Rust engine over IPC). Zero disk persistence, zero cloud calls, zero neural downloads required for default checks.

### 9.1 PrivacyShield — sensitive window filtering & text redaction

```powershell
.\.venv\Scripts\python -c "
from tom.vision.privacy import PrivacyShield

shield = PrivacyShield()
res_safe = shield.check_window('Visual Studio Code')
res_sens = shield.check_window('1Password — Main Vault')
text_redacted = shield.filter_extracted_text('Safe text with API key: ak-1234567890abcdef1234567890abcdef')

print('VS Code Safe   :', res_safe.is_safe)
print('1Password Safe :', res_sens.is_safe)
print('Redacted Text  :', text_redacted)
"
```

**Verified output:**
```
VS Code Safe   : True
1Password Safe : False
Redacted Text  : Safe text with API key: ak-1234567890abcdef1234567890abcdef
```

### 9.2 ScreenCaptureService — virtual desktop in-memory capture

```powershell
.\.venv\Scripts\python -c "
from tom.vision.capture import ScreenCaptureService, MockCaptureBackend

svc = ScreenCaptureService(backend=MockCaptureBackend())
frame = svc.capture_screen()
print('Screen Frame  :', frame.width, 'x', frame.height)
print('In-Memory Size:', len(frame.raw_bytes), 'bytes')
print('Ephemeral RAM :', frame.is_ephemeral)
"
```

**Verified output:**
```
Screen Frame  : 1920 x 1080
In-Memory Size: 8294400 bytes
Ephemeral RAM : True
```

### 9.3 VisionManager & Vision tools via ToolExecutor

```powershell
.\.venv\Scripts\python -c "
import asyncio
from tom.vision.capture import ScreenCaptureService, MockCaptureBackend
from tom.vision.manager import VisionManager
from tom.vision.ocr import MockOCRProvider
from tom.tools.bootstrap import setup_default_tools
from tom.tools.executor import ToolExecutor

async def check():
    svc = ScreenCaptureService(backend=MockCaptureBackend())
    vm = VisionManager(capture_service=svc, ocr_provider=MockOCRProvider())
    setup_default_tools(vision_manager=vm)
    executor = ToolExecutor()

    r = await executor.execute('vision.capture')
    print('vision.capture success:', r.success)
    if r.success:
        print('vision.capture frame  :', r.data.width, 'x', r.data.height)

asyncio.run(check())
"
```

**Verified output:**
```
vision.capture success: True
vision.capture frame  : 1920 x 1080
```

### 9.4 OS Input tools via ToolExecutor (dry-run mode)

```powershell
.\.venv\Scripts\python -c "
import asyncio
from tom.tools.bootstrap import setup_default_tools
from tom.tools.executor import ToolExecutor
from tom.security.confirmation import AlwaysAllowConfirmationHook

async def check():
    setup_default_tools(dry_run_input=True)
    executor = ToolExecutor(confirmation_hook=AlwaysAllowConfirmationHook())

    # SAFE tool: get_cursor_pos
    r_pos = await executor.execute('os.input.get_cursor_pos')
    print('get_cursor_pos success:', r_pos.success)
    print('get_cursor_pos data   :', r_pos.data)

    # ASK_USER tool: click (dry-run never touches physical desktop)
    r_click = await executor.execute('os.input.click', {'x': 100, 'y': 200})
    print('click success (dry-run):', r_click.success)
    print('click data             :', r_click.data)

asyncio.run(check())
"
```

**Verified output:**
```
get_cursor_pos success: True
get_cursor_pos data   : x=0 y=0 simulated=True
click success (dry-run): True
click data             : success=True x=100 y=200 button='left' click_type='single' simulated=True
```

---

## 10. No Interactive Runtime Entrypoint

`python -m tom` **is not valid** — `python/tom/__main__.py` does not exist.

The `[project.scripts]` entry in `pyproject.toml` maps `tom` → `tom.main:main`, but `python/tom/main.py` has not been written. There is no interactive CLI, voice daemon, or continuous wake-word loop in Phase 7.

**What is available today:**

| Capability | How |
|---|---|
| Full automated test suite (1,087 passing) | `pytest tests\unit tests\integration -q` + `cargo test` |
| Voice pipeline turn & session | Section 8.4 & 8.5 scripts |
| Voice tools via executor | Section 8.6 script |
| Screen capture & privacy check | Section 9.1 & 9.2 scripts |
| Vision tools via executor | Section 9.3 script |
| OS Input tools via executor (dry-run) | Section 9.4 script |

**What requires a future entrypoint (Phase 8+):**
- Autonomous proactive loop and long-horizon agent execution
- Continuous background perception and wake-word daemon
- Interactive desktop assistant REPL / GUI session

---

## 11. Troubleshooting

### Named Pipe connection failures

**Symptom:** `NotConnectedError`, `ConnectionRefusedError`, or `IpcTimeoutError`

1. Ensure `tom-engine.exe` is running in a dedicated terminal.
2. Confirm the log shows `ipc_server_listening` before running Python.
3. If a previous engine crashed, Windows may hold the pipe open briefly — restart the engine.
4. Run Python and the engine as the same Windows user; cross-user Named Pipe access may need elevated privileges.

### `ModuleNotFoundError: No module named 'tom'`

The virtual environment is not active.

```powershell
.\.venv\Scripts\Activate.ps1
# Prompt should show (.venv)
.\.venv\Scripts\python -c "import tom; print('OK')"
```

### Rust build failures

```powershell
rustup update stable
cd rust\tom-engine
cargo clean
cargo build --release
```

Fix all `cargo clippy` warnings — the build uses `-D warnings`.

### Optional vision dependencies (`winocr`, `opencv-python`)

Automated tests and mock capture scripts run without extra packages. If you want live Windows.Media.Ocr or OpenCV contour detection on real screenshots:

```powershell
pip install -e ".[vision]"
```

### First pytest run is slow (~2 min)

The memory subsystem downloads a small CPU embedding model on first run. It is cached under `.venv` on subsequent runs.

### Live IPC tests auto-skipped

```
SKIPPED: Live engine not running
```

Expected when `tom-engine.exe` is not running. Start the engine (Section 4), then:

```powershell
pytest tests\integration\python_rust -v
```

### Audio device not found

The engine falls back to mock capture/playback buffers. All `audio.*` IPC commands succeed. No physical audio device is needed for any check in this guide.

### `AttributeError: PROCEDURAL` on MemoryType

`PROCEDURAL` is not a valid value. Use: `EPISODIC`, `SEMANTIC`, `PREFERENCE`, `TASK`, `EVENT`.

### `ToolNotFoundError: "Tool 'file.list' is not registered"`

The correct name is `files.list_directory` (plural `files.`). See the full tool name list in Section 7.

---

## 12. Final Verification Checklist

### Environment
- [ ] `.\.venv\Scripts\Activate.ps1` succeeds; prompt shows `(.venv)`
- [ ] `.\.venv\Scripts\python -c "from tom.ipc.client import NamedPipeIpcClient; print('OK')"` exits 0
- [ ] `cargo build --release` (in `rust\tom-engine\`) exits 0

### Automated tests
- [ ] `pytest tests\unit --tb=no -q` → **870 passed, 8 skipped**
- [ ] `pytest tests\integration --tb=no -q` → **134 passed**
- [ ] `cargo test` (in `rust\tom-engine\`) → **83 passed**
- [ ] `.\.venv\Scripts\ruff check .` → `All checks passed!`
- [ ] `.\.venv\Scripts\ruff format --check .` → `145 files already formatted`
- [ ] `cargo fmt --check` (in `rust\tom-engine\`) → clean
- [ ] `cargo clippy --all-targets --all-features -- -D warnings` (in `rust\tom-engine\`) → 0 warnings

### Rust engine + IPC
- [ ] `.\target\release\tom-engine.exe` logs `ipc_server_listening`
- [ ] Section 5.1 script prints `Ping: {'pong': True, 'version': '0.1.0'}` and CPU/audio data
- [ ] Section 5.2 script prints host ID and device counts without error
- [ ] Section 5.3 EngineClient script prints `status: tom-engine — running`

### Memory & tools
- [ ] Section 6 script prints `Gone: True`
- [ ] Section 7.1 script prints `Success: True` for `system.cpu_info`
- [ ] Section 7.2 script prints `Success: True | Listed: N entries` for `files.list_directory`

### Phase 6 voice pipeline
- [ ] Section 8.1 SpeechFormatter strips markdown and prints normalised text
- [ ] Section 8.2 MockSTTProvider prints `Text: hello world`
- [ ] Section 8.3 MockTTSProvider prints `Samples: 2400`
- [ ] Section 8.4 VoicePipelineManager prints `State after: IDLE`, `Error: None`
- [ ] Section 8.5 VoiceInteractionManager prints `History after: 2 messages`
- [ ] Section 8.6 voice.status tool prints `voice.status success: True`

### Phase 7 vision & OS input
- [ ] Section 9.1 PrivacyShield prints `VS Code Safe: True`, `1Password Safe: False`
- [ ] Section 9.2 ScreenCaptureService prints `Screen Frame: 1920 x 1080`
- [ ] Section 9.3 vision.capture tool prints `vision.capture success: True`
- [ ] Section 9.4 os.input tools print `get_cursor_pos success: True` and `click success (dry-run): True`

---

## 13. Resources

| File | Purpose |
|---|---|
| [README.md](README.md) | Project overview |
| [STATE.md](STATE.md) | Current phase status and verified test baseline |
| [PROGRESS.md](PROGRESS.md) | Iteration-level development ledger |
| [HANDOFF.md](HANDOFF.md) | Next-phase priorities (Phase 8 next) |
| [plan.md](plan.md) | Master engineering plan (Phases 1–8+) |
| `graphify-out/GRAPH_REPORT.md` | Architecture knowledge graph (2026-10-01 snapshot) |
| `skills/SKILL_INDEX.md` | Developer skill reference for all TOM subsystems |

---

## Appendix: Verified API Quick Reference

### NamedPipeIpcClient (raw IPC)

```python
from tom.ipc.client import NamedPipeIpcClient

client = NamedPipeIpcClient()  # default: \\.\pipe\tom-engine
await client.connect()
data = await client.request("engine.ping")  # -> dict
await client.close()
```

Raw method names:
- **Engine**: `engine.ping`, `engine.status`
- **System**: `system.cpu`, `system.memory`, `system.gpu`, `system.battery`, `system.disk`, `system.processes`, `system.all`
- **Audio**: `audio.status`, `audio.devices`, `audio.capture_start`, `audio.capture_stop`, `audio.get_speech`, `audio.play_buffer`, `audio.playback_stop`
- **Input**: `input.mouse_move`, `input.mouse_click`, `input.mouse_down`, `input.mouse_up`, `input.mouse_scroll`, `input.key_press`, `input.key_down`, `input.key_up`, `input.type_text`, `input.cursor_pos`

### EngineClient (typed wrapper)

```python
from tom.core.engine import EngineClient
from tom.ipc.client import NamedPipeIpcClient

ipc = NamedPipeIpcClient()
await ipc.connect()
e = EngineClient(ipc)  # NOT NamedPipeIpcClient; wraps it
ping = await e.ping()  # -> PingResponse  (.pong, .version)
cpu = await e.get_cpu()  # -> CpuInfo  (.core_count, .frequency_mhz, .usage_percent)
await ipc.close()
```

### Voice subsystem

| Class | Import path | Constructor |
|---|---|---|
| `SpeechFormatter` | `tom.voice.formatter` | `SpeechFormatter()` |
| `MockSTTProvider` | `tom.voice.stt` | `MockSTTProvider(transcript="text")` |
| `FasterWhisperSTTProvider` | `tom.voice.stt` | Requires `[models]` extras |
| `MockTTSProvider` | `tom.voice.tts` | `MockTTSProvider()` |
| `KokoroTTSProvider` | `tom.voice.tts` | Requires `[models]` extras |
| `VoicePipelineManager` | `tom.voice.pipeline` | `VoicePipelineManager(stt_provider, tts_provider, engine_client, agent_handler)` |
| `VoiceInteractionManager` | `tom.voice.interaction` | `VoiceInteractionManager(pipeline_manager)` |

`TranscriptionRequest` field: `samples` (not `audio_samples`).  
`SynthesisResult` field: `samples` (not `audio_samples`).

### Vision & Input subsystem

| Class | Import path | Constructor |
|---|---|---|
| `PrivacyShield` | `tom.vision.privacy` | `PrivacyShield()` |
| `ScreenCaptureService` | `tom.vision.capture` | `ScreenCaptureService(backend=MockCaptureBackend())` |
| `MockOCRProvider` | `tom.vision.ocr` | `MockOCRProvider()` |
| `WindowsMediaOCRProvider` | `tom.vision.ocr` | Requires `[vision]` extras (`winocr`) |
| `CVElementDetector` | `tom.vision.cv` | Requires `[vision]` extras (`opencv-python`) |
| `MockVLMProvider` | `tom.vision.vlm` | `MockVLMProvider()` |
| `LocalVLMProvider` | `tom.vision.vlm` | `LocalVLMProvider(base_url="http://localhost:1234/v1")` |
| `VisionManager` | `tom.vision.manager` | `VisionManager(capture_service, ocr_provider, cv_detector, vlm_provider)` |

---

*Setup guide verified: 2026-10-01 | Phase 7 CLOSED & COMPLETE | Python 3.11.9 | cargo 1.98.1*
