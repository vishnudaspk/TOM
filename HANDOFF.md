# Handoff — continue at Phase 9.B WS2 (do not start until asked)

Invariants, hardware, and architecture: **`STATE.md`**. Phase ledger and ADRs 041–055: **`PROGRESS.md`**. Plan: **`docs/development/PHASE9_IMPLEMENTATIONPLAN.md`**.

## Resume

- **Done:** Phase 9 Iterations **9.0** (VAD/endpointing), **9.1** (streaming STT), **9.2** (LLM stream + sentence chunking), **9.3** (streaming TTS + incremental playback), **9.4** (turn manager + response policy). **9.B WS0** (measurement integrity) and **WS1** (cheap faster-whisper tuning) **COMPLETE**.
- **WS2 IN PROGRESS:** sherpa-onnx streaming transducer models tested. **PRIMARY METRIC: `final_from_endpoint`** (time from VAD endpoint to final text). sherpa-large: p50=15.3 ms, p95=31.8 ms — **PASSES 500 ms ceiling**. sherpa-20M: p50=6.9 ms, p95=14.3 ms but produces empty/garbage transcripts — root cause under investigation, marked EXCLUDED pending evidence. First-partial currently measured from feed start (~1400-2000 ms); needs correction to measure from actual speech onset. Previous claim "no streaming CPU model meets the target" **RETRACTED** — sherpa-large meets finalization target. Previous claim sherpa-20M "fundamentally broken" **RETRACTED** — root cause unconfirmed.
- **Next:** Complete sherpa-20M root cause analysis, run accuracy tests (Section 4), then reduced latency runs (Section 5). **Do not** implement 9.5/9.6 or hardware benchmarking beyond what's needed for WS2 evaluation.

## Pipeline assembled so far (9.0 → 9.4 + 9.B WS0-WS1)

```
audio → MicrophoneLoop(9.0) → SpeechStart/Frame/End → StreamingSTTProvider(9.1)
      → ConversationTurnManager + ResponsePolicy(9.4)
      → AgentOrchestrator.stream()(9.2) → SentenceStreamer(9.2)
      → StreamingTTSProvider(9.3) → EngineClient.play_audio_buffer() / stop_audio_playback()
```

Each stage is verified **independently**. 9.5 is the first iteration to bind them into one live event loop; do not move logic back down into 9.5.

| Piece | Path |
|---|---|
| Events / VAD / PCM | `python/tom/voice/events.py`, `vad.py`, `audio_utils.py`, `microphone.py` |
| STT | `python/tom/voice/streaming_stt.py` (`StreamingSTTProvider`, `FasterWhisperStreamingAdapter`, `PartialTranscript`) |
| LLM stream / sentences | `python/tom/agents/orchestrator.py` (`stream()`), `python/tom/voice/sentence_streamer.py` |
| TTS + playback | `python/tom/voice/streaming_tts.py` (`StreamingTTSProvider`, `IncrementalTTSStreamer`, `SynthesisChunk`) |
| Turn state / policy | `python/tom/schemas/turn.py`, `python/tom/voice/turn_manager.py`, `python/tom/voice/response_policy.py` |
| Exports | `python/tom/voice/__init__.py` |
| Tests | `tests/unit/voice/test_microphone_loop.py` (17), `test_streaming_stt.py`, `tests/unit/agents/test_orchestrator_streaming.py`, `test_sentence_streamer.py`, `test_streaming_tts.py` (18), `test_turn_manager.py` (29), `test_response_policy.py` (32) |
| WS0/WS1 Benchmarks | `tests/benchmarks/voice/benchmark_stt_tuning.py`, `tests/benchmarks/voice/results/stt_tuning_results.json` |
| WS0 Hardware Test | `tests/integration/voice/test_hardware_latencies.py::test_real_stt_first_partial_meets_sla` |

## 9.3 contract for 9.5

```
AsyncGenerator[str]  (SentenceStreamer)
   → StreamingTTSProvider.stream_synthesize(...)  → AsyncGenerator[SynthesisChunk]   (ordered)
   → IncrementalTTSStreamer                      → EngineClient.play_audio_buffer() (sequential)
                                                    EngineClient.stop_audio_playback() (once, on cancel)
```

- `SynthesisChunk` is a **frozen** dataclass: `sentence`, `audio_data`, `chunk_index`, `is_last`.
- Synthesis runs ahead of playback (bounded pool + semaphore); playback is a **separate task**, so order is guaranteed by the sequencer, not by synthesis timing.
- On cancellation: pending synthesis is **discarded immediately**, in-flight synthesis is **detached** (never awaited), and `stop_audio_playback()` is invoked **exactly once**.
- Still Decision **041** utterance-buffered playback — no streaming binary IPC.

## 9.4 contract for 9.5

- `ConversationTurnManager.start_turn(...)` → `TurnContext`; close with `record_assistant_reply(...)` or `interrupt_turn(...)`.
- Referents (`last_file`, `last_directory`, `last_query`, `last_screen`, `last_tool`) are created by **deterministic tool→slot rules** and persist across turns. Resolution is the model's job via `TurnContext.to_model_context()` / `render_model_context()`.
- `ResponsePolicy.decide(user_intent, turn_context) -> ResponseDecision` (fixed precedence: `empty → silence → progress → ambiguity → destructive-without-target → latency-bearing → direct`). Emit `ack_text` immediately when action is `FAST_ACK_THEN_WORK`; do nothing when `SILENT_ACT`.
- `record_tool_timing(tool_name, duration_ms)` feeds the latency heuristics; 9.5 should record real durations.
- **No** coreference/NLP engine was added; do not add one in 9.5 either.

## Adaptations to keep

- 9.0: energy VAD in **Python**, not WebRTC/Rust. Ingest **polls** `audio.get_speech`. `mute()` / `pause()` are drain-and-discard and do **not** call `stop_audio_capture`.
- 9.3: pipelining implemented in **Python** on top of the existing buffered playback IPC, not via a new Rust streaming path.
- 9.4: latency classification is **operation type + tool metadata + recorded history**, never a hard-coded per-tool duration.
- WS1: `beam_size=1` is the only flag that helps (~10-15% faster). The 30 s window padding in faster-whisper is the fundamental bottleneck.

## Known gaps (account for in later iterations)

- Offline tests ≠ real mic / TTS latency (**9.6**).
- `VoicePipelineManager` is still untouched; 9.5 must decide how `InteractionCore` relates to it (Decision **044** says do not duplicate its state machine).
- Epoch invalidation and turn recovery are still **unimplemented** — 9.5 owns them.
- Phase 9.0–9.4 tests are **not** in the last full-repo 1301 baseline.
- **faster-whisper CPU cannot meet SLA targets** (350 ms finalization target vs ~1350 ms best). Streaming-native STT (WS2) is required for SLA compliance. **CORRECTION**: sherpa-large meets finalization target (p50=15.3 ms, p95=31.8 ms on `final_from_endpoint` metric).

## Verification (already run, 2026-10-06)

```powershell
pytest tests/unit/voice tests/integration/voice tests/unit/agents tests/integration/agents tests/unit/core tests/unit/ipc
# 833 passed
pytest tests/unit/voice tests/integration/voice
# 317 passed
ruff check python/tom/schemas/turn.py python/tom/voice/turn_manager.py python/tom/voice/response_policy.py python/tom/voice/streaming_tts.py python/tom/voice/__init__.py tests/unit/voice/test_turn_manager.py tests/unit/voice/test_response_policy.py tests/unit/voice/test_streaming_tts.py
ruff format --check <same paths>
```

Full `pytest tests/unit/ tests/integration/` **not** re-run. After WS2, run the targeted set above plus the new streaming STT adapter tests.

## Next Steps for WS2

1. Create throwaway venv `.venv-ws2-bench` and install sherpa-onnx, Moonshine, Parakeet candidates.
2. Verify model availability (sherpa-onnx English streaming Zipformer, Moonshine ONNX, Parakeet TDT ONNX via sherpa-onnx).
3. Create test utterance set (`tests/benchmarks/voice/ws2/utterances.txt` — 40+ realistic utterances).
4. Build benchmark harness with fixed streaming definitions (100 ms chunks, real-time pacing, first partial = first non-empty text yield, finalization = VAD endpoint to final text).
5. Run 20+ runs per utterance per engine per condition (LM Studio loaded/unloaded), randomized order.
6. Report latency tables (Table A), accuracy tables (Table B), practicality table (Table C), target check, recommendation.