# Handoff — continue at Phase 9.1 (do not start until asked)

Invariants, hardware, and architecture: **`STATE.md`**. Phase ledger and ADRs 041–055: **`PROGRESS.md`**. Plan: **`docs/development/PHASE9_IMPLEMENTATIONPLAN.md`**.

## Resume

- **Done:** Phase 9 Iteration **9.0** (audio frontend / VAD / endpointing).
- **Next:** Iteration **9.1** `StreamingSTTProvider` consuming the 9.0 event stream.
- **Do not** implement 9.2+ or InteractionCore. **Do not** start 9.1 until the user asks.

## 9.0 contract for 9.1

```
audio → MicrophoneLoop → SpeechStartEvent → AudioFrameEvent* → SpeechEndEvent
```

VAD owns utterance bounds. 9.1 transcribes delimited audio; it must **not** add a second endpointer.

| Piece | Path |
|---|---|
| Loop | `python/tom/voice/microphone.py` (`MicrophoneLoop`, `MicrophoneConfig`) |
| Events | `python/tom/voice/events.py` |
| VAD | `python/tom/voice/vad.py` (`VADDetector`, `EnergyVADDetector`, `MockVADDetector`) |
| PCM | `python/tom/voice/audio_utils.py` (16 kHz, 16-bit, mono) |
| Exports | `python/tom/voice/__init__.py` |
| Tests | `tests/unit/voice/test_microphone_loop.py` (17) |

Reuse: `EngineClient.start_audio_capture` / `get_captured_speech` / `stop_audio_capture`; existing `STTProvider` / `FasterWhisperSTTProvider` (extend, don’t replace). Target format: 16 kHz, 16-bit, mono, ~20–30 ms frames.

## 9.0 adaptations (keep)

- Energy VAD in **Python**, not WebRTC/Rust. Swap later via `VADDetector`.
- Ingest **polls** `audio.get_speech` (Decision **041**). No new streaming IPC.
- `mute()` / `pause()` are sync: drain-and-discard samples; they do **not** call `stop_audio_capture`. OS capture stays up until `stop()`.

## Known gaps (account for in later iterations)

- Offline 9.0 tests ≠ real mic latency (**9.6**).
- Mute does not tear down OS capture (Python retains nothing).
- `VoicePipelineManager` unchanged; unified loop is **9.5**.
- Phase 9.0 tests are **not** in the last full-repo 1301 baseline.

## 9.0 verification (already run)

```powershell
pytest tests/unit/voice/ tests/integration/voice -q
ruff check python/tom/voice/microphone.py python/tom/voice/vad.py python/tom/voice/events.py python/tom/voice/audio_utils.py python/tom/voice/__init__.py tests/unit/voice/test_microphone_loop.py
```

Re-verified 2026-10-04: 220 targeted voice tests (17 in `test_microphone_loop.py`); ruff check clean. Full `pytest tests/unit/ tests/integration/` **not** re-run.

## After 9.1, targeted check

`tests/unit/voice/` (include new streaming-STT tests) + existing STT tests. Do not run the full repo suite as background validation.
