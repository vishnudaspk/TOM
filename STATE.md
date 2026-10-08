# TOM — Current State

Last updated: 2026-10-08

## Now

| Item | Value |
|---|---|
| **Phases 0–8** | CLOSED & COMPLETE |
| **Phase 9 core** | **9.0–9.4 COMPLETE & VERIFIED**; 9.5/9.6 remain unstarted |
| **Phase 10** | **Iteration 1 (web.search) COMPLETE**; **Iteration 2 (web.fetch) COMPLETE** |
| **Model runtime** | llama.cpp at `http://127.0.0.1:65400/v1`; generic OpenAI-compatible HTTP provider is the active abstraction |
| **Backend migration** | Bionic/LM Studio is removed from active architecture; no active backend-specific alias remains |
| **Plan** | Temporary implementation plan removed after status migration |
| **Next** | Phase 10 Iteration 3 (personal.note, personal.calendar) ready to begin. Phase 9.5/9.6 require explicit user approval. |
| **Full-repo baseline** | 2026-10-02 Phase 8 exit: **1301 passed** (1218 Python + 83 Rust), **8 skipped**. Ruff/Cargo fmt/clippy clean. **Not rerun** since then. |
| **9.0–9.4 incremental** | Targeted verification **833 passed** across voice, agents, core, and IPC tests. Voice alone: **317**. Full repo **not** re-run. |
| **Phase 10 verification** | **191 unit tool tests passed**; **32 integration tool tests passed**; 30 tools registered (28 baseline + 2 web). |

## Architecture (locked Phases 0–8; 9.0–9.4 additive; 10.1–10.2 additive)

Python brain ↔ Rust `tom-engine` over Named Pipe `\\.\pipe\tom-engine`.

| Layer | Owns |
|---|---|
| **Python** | Router; `Agent` / `AgentOrchestrator` (6-state; **9.2** `stream()`); **30 tools** via `ToolExecutor` + `PermissionEngine`; `MemoryManager`; Phase 6 voice (`STTProvider`, `TTSProvider`, `VoicePipelineManager`, `VoiceInteractionManager`); Phase 7 vision; Phase 8 `TaskManager` / `TaskPlanner` / `TaskExecutor` / `ResourceManager` / `ProactiveScheduler`; **9.0** `MicrophoneLoop` + `VADDetector`; **9.1** `StreamingSTTProvider`; **9.2** `SentenceStreamer`; **9.3** `StreamingTTSProvider` + `IncrementalTTSStreamer`; **9.4** `ConversationTurnManager` + `ResponsePolicy`; **10.1** `web.search` (local SearXNG); **10.2** `web.fetch` (SSRF-safe, sanitized extraction) |
| **Rust** | IPC server; telemetry; `AudioManager` (capture/playback, ring buffer); `InputManager` (`SendInput`, clamp, cancel hotkey) |

Tools (30): system 7, file 6, memory 5, voice 2 (`voice.announce`, `voice.status` SAFE), vision 4 (`SAFE`), OS input 4 (`get_cursor_pos` SAFE; `click` / `type_text` / `hotkey` ASK_USER), web 2 (`web.search`, `web.fetch` SAFE).

## Invariants (do not violate)

1. **Extend, do not replace** closed Phase 0–8 subsystems (`AudioManager`, `VoicePipelineManager`, `ToolExecutor`, `PermissionEngine`, `MemoryManager`, `VisionManager`, `InputManager`, `AgentOrchestrator.run()`, `CancellationToken`, `ResourceManager`).
2. Deterministic routing, permissions, clamping, sentence parsing, and audio/task state machines stay **outside** LLM prompts.
3. All agent tools go through `ToolExecutor` → `PermissionEngine`. Permissions are **data-driven** (`DEFAULT_TOOL_PERMISSIONS`), never string-prefix inferred.
4. **SQLite** is memory ground truth; Qdrant is optional index only.
5. Voice turns and screen frames are **ephemeral RAM**; no raw speech/screenshots into SQLite/Qdrant.
6. **Local-only**: no cloud LLM/VLM, no cloud fallback, no remote screenshot send.
7. Offline tests: no GPU, network, or neural-weight downloads.
8. **VAD owns speech boundaries** (Phase 9). STT must not add a competing endpoint detector.
9. Python never injects mouse/keyboard; OS input is Rust `InputManager` via IPC.

## Hardware / env

- Windows 11; Python 3.11+ `.venv`; Rust stable `rust/tom-engine`
- Intel i9-13900H, **16 GB RAM**, RTX 4060 Laptop **8 GB VRAM**
- CPU-first STT/TTS/OCR/CV; VRAM reserved for primary LLM. `ResourceManager`: 500 MB VRAM headroom; `_heavy_lock` between heavy LLM (~5.2 GB) and VLM (~5.5 GB)
- STT FasterWhisper int8 CPU; TTS Kokoro CPU (0 VRAM)

## Phase 9 so far (done)

- **9.0** `MicrophoneLoop`: 16 kHz 16-bit mono ~20 ms frames → energy VAD → `SpeechStartEvent` → `AudioFrameEvent*` → `SpeechEndEvent`.
- **9.1** `StreamingSTTProvider` / `FasterWhisperStreamingAdapter`: VAD-delimited PCM → partials + final; no second endpointer.
- **9.2** `AgentOrchestrator.stream()` (token/delta generator; `run()` unchanged) + `SentenceStreamer` (speakable chunks).
- **9.3** `StreamingTTSProvider` (sentence → ordered `SynthesisChunk`) + `IncrementalTTSStreamer`: sentence N plays while N+1 synthesizes; cancellation discards pending work and calls `stop_audio_playback()` exactly once.
- **9.4** `ConversationTurnManager` (turn lifecycle, bounded history, entity referents, Phase 8 `TaskManager` snapshots) + `ResponsePolicy` (`SPEAK_DIRECT` / `FAST_ACK_THEN_WORK` / `SILENT_ACT` / `CLARIFY` / `REPORT_PROGRESS`). Referent *resolution* stays with the model; no NLP engine.
- **9.B WS0** Measurement integrity: complete and documented as preliminary from feed start, not speech onset.
- **9.B WS1** Cheap faster-whisper tuning: complete; beam_size=1 produced the best tested profile, but latency remains above the 350 ms target and requires separate acceptance work.
- **9.B WS2** Streaming-native STT evaluation: audit/superseded by the Checkpoint 1 remeasurement; no Phase 10 implementation is implied.
- **9.B WS3** Turn detection & Smart Turn: revision remains queued for explicit Task 4 review; no production claim is made here.

Files, gaps, and 9.5 contract: **`HANDOFF.md`**. Ledger and ADRs: **`PROGRESS.md`**. Phase 9.5/9.6 are intentionally deferred.
