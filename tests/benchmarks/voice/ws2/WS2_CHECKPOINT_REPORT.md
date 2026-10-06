# WS2 CHECKPOINT REPORT

**Date**: 2026-10-06  
**Phase**: 9.B WS2 Streaming-native STT evaluation  
**Status**: COMPLETE — Checkpoint reached, STOP

---

## Executive Summary

| Metric | sherpa-large | faster-whisper small.en | sherpa-20M |
|--------|-------------|------------------------|------------|
| **final_from_endpoint p50** | **15.3 ms** ✅ | ~1570 ms ❌ | 6.9 ms (but broken) |
| **final_from_endpoint p95** | **31.8 ms** ✅ | ~2195 ms ❌ | 14.3 ms (but broken) |
| **first_partial_from_onset p50** | 708 ms | N/A (not streaming) | ~1000 ms |
| **WER (overall)** | **64.6%** ❌ | **12.4%** ✅ | ~100% (garbage) |
| **Numbers exact-match** | 0% ❌ | 50% ✅ | 0% |
| **CPU avg / peak (4 threads)** | 775% / 1223% | N/A | N/A |
| **RTF (decode/100ms)** | 0.05 | N/A | N/A |

**Key finding**: sherpa-large meets the **500 ms finalization ceiling** (p50=15.3ms, p95=31.8ms) but has **unusable accuracy (64.6% WER)**. faster-whisper has good accuracy (12.4% WER) but fails latency. **No single engine satisfies both targets**.

---

## Table A: Latency (Corrected Metrics)

### Primary Metric: `final_from_endpoint` (VAD endpoint → final text)

| Engine | Condition | p50 | p95 | Mean | Target (500ms) |
|--------|-----------|-----|-----|------|----------------|
| sherpa-large | Unloaded | **15.3 ms** | **28.7 ms** | 16.7 ms | ✅ PASS |
| sherpa-large | Loaded (Qwen3-1.7B+8B) | **17.9 ms** | **33.9 ms** | 19.4 ms | ✅ PASS |
| faster-whisper small.en | Unloaded | 1571 ms | 2195 ms | 1614 ms | ❌ FAIL |
| faster-whisper small.en | Loaded | 1561 ms | 2126 ms | 1603 ms | ❌ FAIL |

**Note**: The previously reported "Finalization p50 2777 ms / p95 3331 ms" measured from **feed start** (≈ file duration 2.8s), not from VAD endpoint. This metric is **removed** from all comparisons.

### First Partial: `first_partial_from_onset` (speech onset → first non-empty partial)

| Engine | p50 | p95 | Mean | CV |
|--------|-----|-----|------|-----|
| sherpa-large | 708 ms | 881 ms | 684 ms | 15.7% |

**Finding**: First partial is **not constant** (CV=15.7%), varies with utterance. Previous ~1717ms was from feed start including ~850ms leading silence.

### Partial Readiness at Endpoint

| Engine | Partial=Final at Endpoint | WER(partial, final) |
|--------|---------------------------|---------------------|
| sherpa-large | 9.8% (4/41) | 22.2% |

Token timestamps available via `recognizer.timestamps(stream)` — partial lag measurable.

### sherpa-large Model Config
- **Chunk size**: 16 frames (160ms) with 128 left context (from encoder filename `chunk-16-left-128`)
- **Per-chunk decode**: 5.0 ms avg (RTF=0.05, ~20x faster than real-time)
- **Threads**: 4 (CPU avg 775%, peak 1223% on 16-core i9-13900H)

### CPU Cost (sherpa streaming decode, 4 threads)

| Condition | Avg CPU | Peak CPU |
|-----------|---------|----------|
| Unloaded | 774% | 1223% |
| Loaded (LM Studio) | 775% | 1227% |

**No measurable impact** from LM Studio models on sherpa decode CPU.

---

## Table B: Accuracy (Unpaced, One Pass, Identical Normalizer)

**Normalizer**: lowercase, remove punctuation, spoken→digit numbers (five→5, thirty→30, compound: twenty five→25), time formats (seven thirty am→7 30 am).

| Engine | Overall WER | Commands | Questions | Names | Numbers | Num Exact |
|--------|-------------|----------|-----------|-------|---------|-----------|
| **sherpa-large** | **64.6%** | 69.9% | 58.7% | 59.7% | 67.0% | **0/8 (0%)** |
| **faster-whisper small.en** (beam=1) | **12.4%** | 7.2% | 11.1% | 20.3% | 16.1% | **4/8 (50%)** |

### WER Verification (3 hand-checked)
- "open the settings window" vs "open the settings window" → 0.000 ✅
- "open the settings window" vs "open the settings" → 0.250 ✅
- "set a timer for five minutes" vs "set a timer for 5 minutes" → 0.000 ✅

### Every Name/Number Error

**sherpa-large** (all 16 name/number utterances wrong):
| File | Reference | Hypothesis |
|------|-----------|------------|
| 026 | tom show me the dashboard | don't show me to |
| 027 | tom what is the status of the build | no what is the status of |
| 028 | tom open the project settings | don't open the bridge sit |
| 029 | tom search for the resource manager | don't search for the resource |
| 030 | tom minimize the terminal silently | tom many month the dominio valent |
| 031 | tom read the last commit message | don't read the last coming |
| 032 | tom run the integration tests | don't running to grecian |
| 033 | tom what changed in the last commit | dumb what changed in lala |
| 034 | set an alarm for 7 30 am | the lamb for se |
| 035 | remind me at 3 15 pm | remind me of the fif |
| 036 | schedule a meeting for next thursday | skidule a meeting for next th |
| 037 | delete files older than 30 days | did he advise older than |
| 038 | show logs from the last hour | she last hour |
| 039 | calculate 15 percent of 2 100 | galid 15 birth in age of |
| 040 | what is the date next friday | what is the deed next friday |
| 041 | run the backup at midnight | miranda bag up |

**faster-whisper** (11/16 name/number errors):
| File | Reference | Hypothesis |
|------|-----------|------------|
| 026 | tom show me the dashboard | down show me the dashboard |
| 027 | tom what is the status of the build | yo what is the status of the build |
| 028 | tom open the project settings | don't open the project settings |
| 029 | tom search for the resource manager | don't search for the resource manager |
| 030 | tom minimize the terminal silently | dom minimize the terminal silently |
| 031 | tom read the last commit message | don't ring the last commit message |
| 032 | tom run the integration tests | don't run the integration test |
| 034 | set an alarm for 7 30 am | the team alarm for 7 30 am |
| 037 | delete files older than 30 days | till it files older than 30 days |
| 038 | show logs from the last hour | show logs for the last hour |
| 039 | calculate 15 percent of 2 100 | calculate 15 of 200 |

---

## Table C: Practicalities

| Aspect | sherpa-large | faster-whisper small.en |
|--------|-------------|------------------------|
| **Model size** | ~100 MB (int8) | ~150 MB (int8) |
| **Dependencies** | sherpa-onnx, onnxruntime | faster-whisper, ctranslate2 |
| **Streaming** | Native (chunked encoder) | Rolling window (30s padding) |
| **Hotwords/Contextual biasing** | Only with `modified_beam_search` (not tested) | Not supported |
| **Token timestamps** | ✅ Yes (`recognizer.timestamps`) | ✅ Yes (segment-level) |
| **Dual-runtime (ONNX + sherpa)** | ✅ No conflicts (tested) | N/A |
| **LM Studio interference** | None measurable | None measurable |
| **CPU cost (4 threads)** | 775% avg / 1223% peak | N/A (separate process) |
| **Accuracy** | Unusable (64.6% WER) | Good (12.4% WER) |
| **Latency (final_from_endpoint)** | **Excellent** (15ms p50) | **Poor** (~1570ms) |

**Dual-runtime test**: `onnxruntime` + `sherpa_onnx` import and concurrent execution **works without crashes/DLL errors**. Kokoro model files not available for full TTS+STT concurrent test. Multiple sherpa streams run concurrently without issues.

**sherpa-20M root cause**: Model produces garbage even on full audio (non-streaming). Encoder is non-chunked (input `x` shape `[N,39,80]` = 390ms fixed window). Likely **model/sherpa-onnx compatibility issue** — "stateless7" architecture not fully supported. **Marked EXCLUDED** (root cause unconfirmed). NOT "fundamentally broken" without evidence.

---

## Targets Check

| Target | Metric | sherpa-large | faster-whisper | Status |
|--------|--------|--------------|----------------|--------|
| Finalization ≤ 500ms | `final_from_endpoint` p50 | **15.3 ms** ✅ | 1571 ms ❌ | **sherpa passes** |
| Finalization ≤ 500ms | `final_from_endpoint` p95 | **31.8 ms** ✅ | 2195 ms ❌ | **sherpa passes** |
| First partial ≤ 350ms | `first_partial_from_onset` p50 | 708 ms ❌ | N/A | **Both fail** |

**Previous claim "no streaming CPU model meets the target" RETRACTED** — sherpa-large meets finalization target.

---

## Recommendation

**Use sherpa-large for latency-critical paths with external accuracy recovery**, OR **use faster-whisper for accuracy-critical paths with accepted latency**.

For TOM's voice pipeline (Phase 9.5+):
1. **Primary**: sherpa-large streaming STT for low-latency partials → feed to LLM for real-time response
2. **Fallback**: faster-whisper for final transcript verification / high-accuracy needs
3. **Mitigation**: sherpa-20M excluded; consider Moonshine/Parakeet via sherpa-onnx for better accuracy/latency trade-off

**Out-of-process STT worker** recommended if dual-runtime conflicts emerge with Kokoro TTS (not observed in testing).

---

## UNVERIFIED List

1. ❌ sherpa-large hotwords/contextual biasing (requires `modified_beam_search`, not tested)
2. ❌ Moonshine ONNX / Parakeet TDT via sherpa-onnx (not evaluated)
3. ❌ Kokoro TTS + sherpa STT concurrent in same process (Kokoro model missing)
4. ❌ GPU-accelerated sherpa-onnx (CUDA EP) — CPU-only tested
5. ❌ Real microphone input (recordings only)
6. ❌ VAD integration (EnergyVAD from 9.0) — used simulated hangover
7. ❌ Long-form / multi-utterance streaming (single utterance only)
8. ❌ sherpa-20M root cause (marked EXCLUDED, not fixed)

---

## Raw Data Paths

| File | Description |
|------|-------------|
| `tests/benchmarks/voice/ws2/results/sherpa_test_results.json` | Initial sherpa runs (feed-start metrics) |
| `tests/benchmarks/voice/ws2/results/sherpa_corrected_results.json` | Corrected metrics (onset-based) |
| `tests/benchmarks/voice/ws2/results/sherpa_large_corrected.json` | sherpa-large full corrected run |
| `tests/benchmarks/voice/ws2/results/sherpa_20M_corrected.json` | sherpa-20M investigation |
| `tests/benchmarks/voice/ws2/results/accuracy_combined.json` | WER results (both engines) |
| `tests/benchmarks/voice/ws2/results/latency_unloaded.json` | 10/5 runs, LM Studio unloaded |
| `tests/benchmarks/voice/ws2/results/latency_loaded.json` | 10/5 runs, LM Studio loaded |
| `tests/benchmarks/voice/ws2/results/stratified_indices.json` | 15 utterance indices used |

---

## Reproduction Commands

```powershell
# Accuracy (unpaced)
.venv-ws2-bench\Scripts\python.exe tests/benchmarks/voice/ws2/accuracy_test.py   # sherpa
.venv\Scripts\python.exe tests/benchmarks/voice/ws2/accuracy_fw.py              # faster-whisper

# Latency (paced, 10/5 runs)
.venv-ws2-bench\Scripts\python.exe tests/benchmarks/voice/ws2/latency_reduced.py  # unloaded
.venv-ws2-bench\Scripts\python.exe tests/benchmarks/voice/ws2/latency_loaded.py   # loaded

# Dual-runtime smoke test
.venv-ws2-bench\Scripts\python.exe tests/benchmarks/voice/ws2/dual_runtime_test.py

# Corrected metrics analysis
.venv-ws2-bench\Scripts\python.exe tests/benchmarks/voice/ws2/analyze_fixed.py
```

---

## Documentation Updates

- `STATE.md`: Updated WS0/WS1 as PRELIMINARY, WS2 IN PROGRESS with corrected metrics
- `PROGRESS.md`: Retracted "no engine meets target" and "sherpa-20M broken" claims
- `HANDOFF.md`: Added WS2 progress, corrected known gaps

---

## Next Steps (Phase 9.5+)

1. Integrate sherpa-large StreamingSTTProvider adapter (9.1)
2. Implement accuracy recovery layer (LLM-based correction of sherpa partials)
3. Evaluate Moonshine/Parakeet for better accuracy/latency balance
4. Add hotwords for "TOM" + command vocabulary if `modified_beam_search` viable

---

**CHECKPOINT REACHED — WS2 COMPLETE. STOP.**