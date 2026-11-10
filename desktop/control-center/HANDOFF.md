
Final Alpha 2.1 verification update (2026-11-10): the complete `tests/unit` suite passed (1906 passed, 4 skipped); repository-wide collection remains unavailable because optional WS2 benchmark tests require missing `sherpa_onnx`. `git diff --check` is clean. `npm run tauri -- build` completed and produced EXE, NSIS, and MSI artifacts. No installer or live audio acceptance was run.

## 2026-11-10 Control Center runtime hierarchy redesign - IMPLEMENTED

Scope: removed the independent Python Brain and Voice process modes, added a
single authoritative Python Runtime (`--service --headless`), modeled Voice,
Faster-Whisper STT, and Kokoro TTS as API-derived capabilities, and added a
clearly non-functional Vision Model placeholder.

Changes:

- Rust (`src-tauri/src/main.rs`): process components are `engine`, `model`,
  `python`, `cli`, and `wheel_script`; `voice`, `stt`, `tts`, and `vision` are
  derived capability/placeholder components. `/api/v1/status` bodies are parsed
  for provider and pipeline state. `start_tom` accepts `python` or `cli`;
  `brain`/`voice` specs are rejected. The voice-operation whitelist adds
  `start_pipeline` and `stop_pipeline`. Component records expose `kind`.
- Python (`python/tom/main.py`): voice runtime construction is shared by
  `--voice` and the management API; `_voice_pipeline_active`,
  `_voice_status_snapshot`, `_start_managed_voice_pipeline`, and
  `_stop_managed_voice_pipeline` were added. `_management_voice_operation`
  implements start/stop pipeline and gates provider unload/tests on the shared
  active-pipeline helper. Service-mode Voice can report ready when its core and
  microphone are running.
- Frontend (`src/App.svelte`): nav is Overview, AI Models, Rust Engine, Python
  Runtime, CLI, Voice, Vision Model, Desktop Assistant, Tools, Memory,
  Diagnostics, Settings. Overview groups supervised processes, voice
  capabilities, and future capabilities. The Voice page has separate pipeline,
  STT, and Kokoro cards with explicit actions disabled while the pipeline is
  active. Vision is a placeholder with no controls.

Validation: `cargo test --bin tom-control-center` - 12 passed; focused Python
voice-operation tests - 8 passed; `svelte-check` - 0 errors/0 warnings. Full
desktop pytest, Ruff, Clippy, Vite build, and `git diff --check` results are in
the root state/handoff update.

Not verified live: microphone capture, speaker playback, STT/Kokoro loading,
pipeline start/stop against a real Python Runtime, GPU budget behavior under
load, and Windows installer acceptance. These require operator hardware and
were deliberately not started.