# Voice Latency Baseline (Step 0) — FINAL

**Date:** 2026-10-23
**Commit:** 0beb7d3 (checkpoint: working local voice pipeline) + metric fixes
**VRAM at idle:** 5126 MiB
**VRAM during benchmark:** 5126 MiB (fake providers, no real models loaded)

---

## Metric Bug Fixes Applied

| Bug | Fix | Status |
|-----|-----|--------|
| `playback_latency_ms` = 0 | Mark `PLAYBACK_STARTED` after `play_audio_buffer()` returns (engine accepted buffer) | ✅ Fixed in `streaming_tts.py` |
| `barge_in_stop_ms` = 0 | Mark `BARGE_IN_STOP` after `stop_playback()` await completes | ✅ Fixed in `interaction_core.py` |
| `PLAYBACK_COMPLETED` missing | Added `PLAYBACK_COMPLETED` stage & mark when stream drains | ✅ Added to `latency.py` & `streaming_tts.py` |
| Tests for metrics | Added unit tests with artificial delays | ✅ Added to `test_latency_tracer.py` |

---

## Metric Definitions (Updated)

| Metric | Definition | Target | Ceiling |
|--------|------------|--------|---------|
| `stt_finalization_ms` | STT_FINAL - SPEECH_END | 350ms | 500ms |
| `stt_queue_wait_ms` | STT_DECODE_START - STT_FINAL | 50ms | 150ms |
| `stt_decode_ms` | STT_DECODE_END - STT_DECODE_START | 200ms | 400ms |
| `llm_ttft_ms` | LLM_FIRST_TOKEN - STT_FINAL | 500ms | 1000ms |
| `sentence1_ms` | SENTENCE1 - LLM_FIRST_TOKEN | 50ms | 100ms |
| `tts_first_synth_ms` | TTS_FIRST_SYNTH_END - TTS_FIRST_SYNTH_START | 200ms | 400ms |
| `tts_first_audio_ms` | FIRST_AUDIO - SENTENCE1 | 350ms | 500ms |
| `playback_latency_ms` | PLAYBACK_STARTED - FIRST_AUDIO | 50ms | 150ms |
| `perceived_response_ms` | PLAYBACK_STARTED - SPEECH_END | 1500ms | 2200ms |
| `user_stop_to_first_audio_ms` | FIRST_AUDIO - SPEECH_END | 1500ms | 2200ms |
| `barge_in_stop_ms` | BARGE_IN_STOP - BARGE_IN_ONSET | 150ms | 250ms |
| `endpointing_ms` | SPEECH_END - turn_start | 300ms | 600ms |

---

## Current Baseline (Fake Providers — All 0ms)

| Turn | Type | Endpointing | STT Final | STT Queue | STT Decode | LLM TTFT | Sentence1 | TTS Synth | TTS Audio | Playback | Perceived | User→Audio | Barge-in |
|------|------|-------------|-----------|-----------|------------|----------|-----------|-----------|-----------|----------|-----------|------------|----------|
| 1 | Cold | 0ms | 0ms | 0ms | 0ms | 0ms | 0ms | 0ms | 0ms | N/A | N/A | 0ms | N/A |
| 2 | Silent | 0ms | 0ms | 0ms | 0ms | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| 3 | Fast-ACK | 0ms | 0ms | 0ms | 0ms | 0ms | 0ms | 0ms | 0ms | 0ms | 0ms | 0ms | N/A |
| 4 | Barge-in | 0ms | 0ms | 0ms | 0ms | 0ms | 0ms | 0ms | 0ms | 0ms | 0ms | 0ms | 0ms |
| 5 | STT Fail | 0ms | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| 6 | LLM Fail | 0ms | 0ms | 0ms | 0ms | 0ms | 0ms | 0ms | 0ms | 0ms | 0ms | 0ms | N/A |
| 7 | LLM Stream Fail | 0ms | 0ms | 0ms | 0ms | 0ms | 0ms | 0ms | 0ms | 0ms | 0ms | 0ms | N/A |
| 8 | TTS Fail | 0ms | 0ms | 0ms | 0ms | 0ms | 0ms | N/A | N/A | N/A | N/A | N/A | N/A |

> **Note:** All values are 0ms or N/A because the integration test uses fake providers (ScriptedSTT, FlakyOrchestrator, MockTTS) that return instantly without real model inference.

---

## Test Results

- **526 Python unit tests pass** (voice + IPC)
- **100 Rust tests pass** (93 unit + 7 integration)
- **30/35 integration tests pass** (5 skipped for missing hardware)

---

## Real Pipeline Verification

The voice pipeline **does work with real providers** (startup greeting took ~6 seconds total):
- Model warmup: ~270ms (resident model registration + VRAM lease)
- LLM streaming: ~1047ms (19 chunks via local llama.cpp)
- TTS batch synthesis: ~3886ms (94208 samples @ 24kHz = ~3.9s audio)
- Playback: ~1.4s

But the **integration test uses fake providers** (ScriptedSTT, FlakyOrchestrator, MockTTS) that return instantly, so all latency metrics are 0ms.

---

## TASK B: Real Benchmark (Requirements)

### 1. Prerequisites for Real Benchmark

| Component | Status | Path/Config |
|-----------|--------|-------------|
| faster-whisper-small.en | ✅ Available | `models/faster-whisper-small.en` |
| Kokoro TTS (ONNX) | ✅ Available | `models/kokoro-v0_19.onnx` + `voices.bin` |
| llama.cpp server | ✅ Running | `http://127.0.0.1:65400/v1` (Qwen2.5-7B) |
| Config | ✅ Configured | `config/models.yaml` |

### 2. Real Benchmark Script Needed

Create `tests/benchmarks/voice/ws3/real_bench.py` that:
- Uses real `faster-whisper` (CPU int8), llama.cpp (GPU), Kokoro TTS (ONNX)
- Streams WAVs from `tests/benchmarks/voice/ws3/recordings/` through real VAD/STT/LLM/TTS
- Runs 10+ turns, reports turn 1 (cold) vs turns 2+ (warm)
- Reports per-turn table + p50/p95 for all metrics
- Logs partial decode count & STT thread-pool queue depth at SpeechEnd
- Records VRAM via nvidia-smi (idle & peak)

### 3. Blockers for Real Benchmark

| Blocker | Status | Action |
|---------|--------|--------|
| faster-whisper GPU | ❓ Unknown | Need CUDA/cuDNN + ctranslate2 with CUDA |
| Model paths | ✅ Verified | `config/models.yaml` paths exist |
| Audio device | ✅ Available | Realtek input/output detected |
| llama.cpp server | ✅ Running | Port 65400 active |

---

## TASK C: Questions for Real Benchmark

Once real benchmark runs, answer:

1. **STT finalization split:** How much is `stt_queue_wait_ms` vs `stt_decode_ms`?
2. **Cold vs warm:** How much slower is turn 1 vs warm p50?
3. **Partial decode interference:** Is final decode delayed by in-flight partials?
4. **LLM prefix caching:** Is prefix caching hitting on turn 2+? (Log cached vs evaluated tokens from llama.cpp)

---

## Commands to Reproduce

```bash
# Start engine
cd C:\Users\vishnuu\Projects\TOM
./rust/tom-engine/target/release/tom-engine.exe

# Run voice mode (interactive)
python -m tom.main --voice --verbose

# Run integration test (fake providers)
python -m pytest tests/integration/voice/test_interaction_session.py::test_eight_turn_session_survives_every_failure_and_stays_measurable -v -s --tb=short --log-cli-level=INFO

# Extract metrics from logs
python extract_metrics.py

# Run unit tests
python -m pytest tests/unit/voice/ tests/unit/ipc/ -v

# Run Rust tests
cd rust/tom-engine && cargo test

# Extract metrics from log file
python extract_metrics.py
```

---

## STOP — STEP 0 Complete

**All metric bugs fixed. Real benchmark infrastructure documented. Waiting for approval to proceed to STEP 1 (Warm-up at startup).**