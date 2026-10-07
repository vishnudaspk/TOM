# Handoff — Phase 9 closeout complete

Final architecture: TOM uses llama.cpp at `http://127.0.0.1:65400/v1` through the generic OpenAI-compatible `HttpModelProvider`. Bionic/LM Studio is not an active runtime dependency.

Invariants, hardware, and architecture: **`STATE.md`**. Phase ledger and ADRs 041–055: **`PROGRESS.md`**. The temporary Phase 9 implementation plan was removed after migration.

## Resume

- **Done:** Phase 9 Iterations **9.0** (VAD/endpointing), **9.1** (streaming STT), **9.2** (LLM stream + sentence chunking), **9.3** (streaming TTS + incremental playback), **9.4** (turn manager + response policy), and the applicable 9.B checkpoint work.
- **Runtime:** Generic HTTP provider with llama.cpp as the production endpoint; no backend-specific alias remains.
- **Current validation:** Targeted Phase 9 verification: **833 passed**, including **317 voice tests**. Full repository rerun was not performed.
- **Next:** No Phase 10 work. Phase 9.5/9.6 are explicitly deferred and require user approval.

## Pipeline assembled so far (9.0 → 9.4)

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
- **STT tradeoffs observed (Checkpoint 1 Fix Round)**:
  - `faster-whisper small.en (beam_size=1)`: Best accuracy overall (WER 13.02%, commands 7.22%, numbers exact 5/8, numbers WER 9.82%) [MEASURED: `tests/benchmarks/voice/ws2/results/rescored_accuracy.json`]. CPU scales cleanly with zero spin-waiting (406% of 1 core / 20.3% of machine at 4 threads) [MEASURED: `f5_cpu_rebuild.json`]. Latency is p50=1570.9 ms, p95=2195.2 ms from endpoint due to 30 s window padding.
  - `Moonshine-base int8 offline`: Best latency-accuracy tradeoff among fast offline models (p50=70.42 ms, p95=99.99 ms in 2–3 s bucket, peak RAM 349.1 MB, zero high-variance flags) [MEASURED: `f4_candidate_models.json`]. Commands WER is 13.89%, but overall WER is 26.55% (questions 35.05%, names 34.90%). Exhibits ONNX Runtime spin-waiting at threads>=2 (1298% of 1 core / 64.9% of machine at 4 threads).
  - `Moonshine-tiny int8 offline`: p50=33.93 ms, p95=57.17 ms, but high variance (31.7%) and overall WER is 40.92% (commands 35.22%).
  - `sherpa-large streaming`: Meets latency (p50=28.98 ms with real H=600 ms) [MEASURED: `f6_h600_latency.json`], but REJECTED FOR ACCURACY (WER 55.40%, commands 55.78%, numbers exact 0/8) due to LibriSpeech training domain mismatch.
  - `NeMo Parakeet TDT`: 110M model is NOT FOUND on HF (empty repo); 0.6B int8 model is NOT SUPPORTED in installed sherpa-onnx 1.13.8 (crashes with `InitDecoder:303 'vocab_size' does not exist in metadata`).
  - `Real VAD hangover`: `H = 600 ms` in TOM config [SOURCE: `python/tom/voice/microphone.py` L48].
  - `Smart Turn`: Suspect claims suspended; queued for Task 4 re-test using all 15 hesitation clips and real config H=600 ms.

## Verification & Reproduction Commands (Checkpoint 1 Fix Round)

```powershell
# F1: Package versions audit
.venv-ws2-bench\Scripts\python.exe tests\benchmarks\voice\ws2\audit_versions.py

# F2: Normalizer unit tests & rescoring across all engines
.venv-ws2-bench\Scripts\python.exe tests\benchmarks\voice\ws2\normalizer.py
.venv-ws2-bench\Scripts\python.exe tests\benchmarks\voice\ws2\rescore_all.py

# F3: Clipped-start silence padding diagnostic (A/B)
.venv-ws2-bench\Scripts\python.exe tests\benchmarks\voice\ws2\f3_clipped_diagnostic.py

# F4: Candidate models evaluation (Moonshine-base & Parakeet)
.venv-ws2-bench\Scripts\python.exe tests\benchmarks\voice\ws2\eval_candidates_f4.py
.venv-ws2-bench\Scripts\python.exe tests\benchmarks\voice\ws2\test_parakeet.py

# F5: CPU measurement rebuild (continuous 100ms sampling)
.venv-ws2-bench\Scripts\python.exe tests\benchmarks\voice\ws2\f5_cpu_rebuild.py

# F6: Latency recomputation with real H=600ms
.venv-ws2-bench\Scripts\python.exe tests\benchmarks\voice\ws2\measure_h600.py
```

## What NOT to do
- Do NOT edit files under `python/tom/`, `config/`, `rust/`, or touch IPC protocol.
- Do NOT modify production `.venv` (use throwaway `.venv-ws2-bench` or `.venv-ws3-bench`).
- Do NOT start WS4–WS7 or revise `docs/design/turn_taking_controller.md` before user sign-off.
- Do NOT use GPU STT (CPU-first invariant).
- Do NOT trust unconsumed generator timings or unpaced streaming audio feeds.

## Next Step (awaiting user review)
- User review of Checkpoint 1 Fix Round report.
- Upon approval, proceed immediately to **Task 4** (turn-taking re-test with all 15 clips, split points, H=600 ms, Smart Turn v3 official comparison, and Plan B rule-based completeness).