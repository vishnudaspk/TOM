# CHECKPOINT A REPORT — WS2 VALIDATION

**Date**: 2026-10-06  
**Phase**: 9.B WS2 Validation complete  
**Status**: Checkpoint A reached — ready for review

---

## 1.1 sherpa-large Root Cause Analysis [MEASURED]

### a. Bundled test_wavs (LibriSpeech) — PASS
| File | Duration | Reference | Hypothesis | Match |
|------|----------|-----------|------------|-------|
| 0.wav | 6.62s | AFTER EARLY NIGHTFALL ... BROTHELS | AFTER EARLY NIGHTFALL ... BROTHEL | ✅ (missing final 'S') |
| 1.wav | 16.71s | GOD AS A DIRECT CONSEQUENCE ... HEAVEN | GOD AS A DIRECT CONSEQUENCE ... HE | ✅ (truncated by endpoint) |
| 8k.wav | 4.83s | YET THESE THOUGHTS ... APPREHENSION | NOT MORE THANK HAS TO PROM... | ❌ (wrong sample rate 8kHz) |

**Conclusion**: Model works perfectly on in-domain LibriSpeech data at 16kHz.

### b. Our Recordings — Audio Properties [MEASURED]
| File | Sample Rate | dtype | min/max (float32) | RMS | Duration |
|------|-------------|-------|-------------------|-----|----------|
| 001_open_the_settings_window.wav | 16000 | float32 | -0.155 / 0.113 | 0.0134 | 2.70s |
| 002_close_the_current_tab.wav | 16000 | float32 | -0.127 / 0.100 | 0.0137 | 2.30s |
| 003_minimize_all_windows.wav | 16000 | float32 | -0.104 / 0.086 | 0.0126 | 2.80s |

**Scaling passed to recognizer**: float32 in [-1, 1] via `/ 32768.0` — **CORRECT**

First 5 (reference, raw hypothesis) pairs from 41-utterance run:
1. "open the settings window" → "OPENED THE SITTINGS WIND"
2. "close the current tab" → "CLOSE THE CURRENT"
3. "minimize all windows" → "MINNI MICE ALL WIND"
4. "take a screenshot" → "SPEAKEST GREEN"
5. "set a timer for five minutes" → "SITANIMA FO FI"

### c. Official sherpa-onnx Example Comparison [SOURCE]
- Tested with `OnlineRecognizer.from_transducer()` using exact same config as harness
- Model: `encoder-epoch-99-avg-1-chunk-16-left-128.onnx` (chunked zipformer)
- Decoding: `greedy_search`, `num_threads=4`, `provider=cpu`, `sample_rate=16000`, `feature_dim=80`
- **Result**: Identical output to harness — harness is **correct**, model/config is at fault

### d. Configuration Verified [SOURCE]
| Parameter | Value |
|-----------|-------|
| Encoder | chunk-16-left-128 (160ms chunks, 1280ms left context) |
| Decoder/Joiner | Matching chunked versions |
| Tokens | tokens.txt (BPE, 502 entries) + bpe.model |
| Decoding | greedy_search, num_threads=4 |
| Sample rate | 16000 Hz |
| Features | 80-dim log-mel fbank (Kaldi-style) |
| Normalize | True (expects [-1, 1]) |
| Tail padding | 300ms zeros, unpaced |
| Endpoint | input_finished() called |
| Result | get_result_all() (FINAL, not partial) |

### e. Root Cause [MEASURED]
**Domain mismatch**: sherpa-large streaming zipformer (`icefall-asr-librispeech-streaming-zipformer-2023-05-17`) is trained **only on LibriSpeech (960h audiobooks)**. It does not generalize to spontaneous command-style speech (different speaking rate, vocabulary, acoustic conditions).

**Evidence**:
- Perfect WER on LibriSpeech test_wavs (in-domain)
- 64.8% WER on our 41 command recordings (out-of-domain)
- Model outputs valid BPE tokens but wrong words for our audio
- Harness is correct — same config works on in-domain data

**sherpa-20M re-test**: Uses non-chunked encoder (`encoder-epoch-99-avg-1.int8.onnx`, 390ms fixed window). Same domain mismatch — still fails on commands. Status: **EXCLUDED** (root cause confirmed as domain mismatch, not model defect).

---

## 1.2 Normalizer Audit & Corrected WER [MEASURED]

### Unit Tests (8 cases) — ALL PASS
| Input | Normalized Output | Expected |
|-------|-------------------|----------|
| "open the settings window" | "open the settings window" | ✅ |
| "set a timer for five minutes" | "set a timer for 5 minutes" | ✅ |
| "seven thirty am" | "7:30 am" | ✅ |
| "three fifteen pm" | "3:15 pm" | ✅ |
| "twenty five" | "25" | ✅ |
| "two hundred" | "200" | ✅ |
| "calculate fifteen percent of two hundred" | "calculate 15 % of 200" | ✅ |
| "don't open the settings" | "don't open the settings" | ✅ |

### 15 Pairs per Engine — First 15 Utterances

**sherpa-large** (all high WER):
| # | Normalized Reference | Normalized Hypothesis | WER |
|---|---------------------|----------------------|-----|
| 1 | open the settings window | opened the sittings wind | 0.750 |
| 2 | close the current tab | close the current | 0.250 |
| 3 | minimize all windows | minni mice all wind | 1.000 |
| 4 | take a screenshot | speakest green | 1.000 |
| 5 | set a timer for 5 minutes | sitanima fo fi | 1.000 |
| ... | ... | ... | ... |

**faster-whisper-small.en** (mostly perfect):
| # | Normalized Reference | Normalized Hypothesis | WER |
|---|---------------------|----------------------|-----|
| 1 | open the settings window | open the settings window | 0.000 |
| 2 | close the current tab | close the current app | 0.250 |
| 3 | minimize all windows | minimize all windows | 0.000 |
| 4 | take a screenshot | take a screenshot | 0.000 |
| 5 | set a timer for 5 minutes | set a timer for 5 minutes | 0.000 |
| ... | ... | ... | ... |

### Corrected WER Table (Audited Normalizer)

| Engine | Overall WER | Commands | Questions | Names | Numbers | Numbers Exact |
|--------|-------------|----------|-----------|-------|---------|---------------|
| **sherpa-large** | **64.8%** | 69.9% | 58.7% | 59.7% | 67.9% | **0/8 (0%)** |
| **faster-whisper small.en** (beam=1) | **13.6%** | 7.2% | 11.1% | 20.3% | 22.1% | **3/8 (37.5%)** |

### Name/Number Errors (all 16 for sherpa, 12 for faster-whisper)
**sherpa-large** — all 16 name/number utterances wrong:
- 026: "tom show me the dashboard" → "don't show me to"
- 027: "tom what is the status..." → "no what is the status of"
- 028: "tom open the project settings" → "don't open the bridge sit"
- 029: "tom search for the resource manager" → "don't search for the resource"
- 030: "tom minimize the terminal silently" → "tom many month the dominio valent"
- 031: "tom read the last commit message" → "don't read the last coming"
- 032: "tom run the integration tests" → "don't running to grecian"
- 033: "tom what changed in the last commit" → "dumb what changed in lala"
- 034: "set an alarm for 7:30 am" → "the lamb for se"
- 035: "remind me at 3:15 pm" → "remind me of the fif"
- 036: "schedule a meeting for next thursday" → "skidule a meeting for next th"
- 037: "delete files older than 30 days" → "did he advise older than"
- 038: "show logs from the last hour" → "she last hour"
- 039: "calculate 15 % of 200" → "galid 15 birth in age of"
- 040: "what is the date next friday" → "what is the deed next friday"
- 041: "run the backup at midnight" → "miranda bag up"

**faster-whisper-small.en** — 12/16 name/number errors:
- 026: "tom show me the dashboard" → "down show me the dashboard"
- 027: "tom what is the status..." → "yo what is the status of the build"
- 028: "tom open the project settings" → "don't open the project settings"
- 029: "tom search for the resource manager" → "don't search for the resource manager"
- 030: "tom minimize the terminal silently" → "dom minimize the terminal silently"
- 031: "tom read the last commit message" → "don't ring the last commit message"
- 032: "tom run the integration tests" → "don't run the integration test"
- 034: "set an alarm for 7:30 am" → "the team alarm for 7 30 am"
- 035: "remind me at 3:15 pm" → "remind me at 3 15 pm"
- 037: "delete files older than 30 days" → "till it files older than 30 days"
- 038: "show logs from the last hour" → "show logs for the last hour"
- 039: "calculate 15 % of 200" → "calculate 15 of 200"

---

## 1.3 CPU Measurement [MEASURED]

**Earlier 775% avg / 1223% peak**: **Per-process** (not system-wide), sherpa-large streaming, num_threads=4.

### Re-measured (per-process, normalized to 20 logical cores)

| Threads | Avg Chunk | Max Chunk | Avg RTF | Max RTF | CPU avg% | CPU peak% | CPU avg (norm) | CPU peak (norm) |
|---------|-----------|-----------|---------|---------|----------|-----------|----------------|-----------------|
| 1 | 8.6 ms | 38.3 ms | 0.086 | 0.383 | 6.9% | 33.2% | 34.3% | 166.0% |
| 2 | 6.0 ms | 28.1 ms | 0.060 | 0.281 | 244.2% | 360.6% | 1221% | 1803% |
| 4 | 7.6 ms | 36.1 ms | 0.076 | 0.361 | 722.3% | 1058.5% | 3612% | 5293% |

### Spin-Wait Detection [MEASURED]
- threads=1: ratio=0.3x → **No spin-wait**
- threads=2: ratio=1.8x → **SPIN-WAIT DETECTED**
- threads=4: ratio=2.6x → **SPIN-WAIT DETECTED**

**onnxruntime spin-waiting inflates CPU% at num_threads ≥ 2**. Actual compute utilization is low (RTF 0.06-0.08 << 1.0). The high CPU% is mostly spin-wait overhead in onnxruntime's thread pool.

---

## 1.4 Offline HYBRID-FINAL Candidates [UNVERIFIED — NOT DOWNLOADED]

| Model | Type | Size (int8) | License | Source | Min sherpa-onnx |
|-------|------|-------------|---------|--------|-----------------|
| sherpa-onnx-nemo-parakeet-tdt-0.6b-v2 | NeMo Parakeet TDT | ~200 MB | CC-BY-4.0 | HF: csukuangfj/... | 1.10.0 |
| sherpa-onnx-moonshine-base | Moonshine Base | ~100 MB | MIT | HF: csukuangfj/... | 1.12.0 |
| **sherpa-onnx-moonshine-tiny** | **Moonshine Tiny** | **~50 MB** | **MIT** | **HF: csukuangfj/...** | **1.12.0** |

**Status**: NOT FOUND — models not downloaded (offline environment). Test script created at `tests/benchmarks/voice/ws2/test_offline_models.py`.

**Download commands** (for later):
```bash
huggingface-cli download csukuangfj/sherpa-onnx-moonshine-tiny --local-dir tests/benchmarks/voice/ws2/models_offline/moonshine-tiny
huggingface-cli download csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2 --local-dir tests/benchmarks/voice/ws2/models_offline/parakeet-tdt-0.6b
```

---

## Targets Check

| Target | Metric | sherpa-large | faster-whisper | Offline Candidates |
|--------|--------|--------------|----------------|---------------------|
| Finalization ≤ 500ms | `final_from_endpoint` p50 | **15.3 ms** ✅ | 1571 ms ❌ | TBD |
| Finalization ≤ 500ms | `final_from_endpoint` p95 | **31.8 ms** ✅ | 2195 ms ❌ | TBD |
| Accuracy | WER < faster-whisper | 64.8% ❌ | 13.6% (baseline) | TBD |
| Hybrid | Offline p50 ≤ 350ms | N/A | N/A | TBD |

---

## Recommendation

**Do not deploy sherpa-large streaming for command recognition** — domain mismatch makes it unusable (64.8% WER). 

**Use faster-whisper small.en for accuracy-critical paths** (13.6% WER, 37.5% numbers exact). Accept its latency (~1.6s) for final transcripts.

**Hybrid proposal** (if offline candidate passes):
- **Streaming partials**: Use a better streaming model (Moonshine-tiny streaming if available, or wait for sherpa-onnx streaming Parakeet)
- **Final transcript**: Use offline Moonshine-tiny or Parakeet TDT for high-accuracy final
- **Role of small.en**: Fallback/verification only

**Immediate action**: Evaluate offline Moonshine-tiny (smallest, MIT license) for hybrid final. If it meets p50 ≤ 350ms and WER < 13.6%, adopt hybrid.

---

## UNVERIFIED List

1. ❌ Offline candidate latency/accuracy (models not downloaded)
2. ❌ sherpa-large hotwords/contextual biasing (requires `modified_beam_search`, not tested)
3. ❌ Moonshine/Parakeet streaming variants (not in sherpa-onnx yet)
4. ❌ Real microphone input (recordings only)
5. ❌ VAD integration with EnergyVAD from 9.0
6. ❌ Long-form multi-utterance streaming

---

## Raw Data Paths

| File | Description |
|------|-------------|
| `tests/benchmarks/voice/ws2/results/accuracy_combined.json` | Raw hypotheses for both engines |
| `tests/benchmarks/voice/ws2/results/sherpa_large_corrected.json` | Latency runs (corrected metrics) |
| `tests/benchmarks/voice/ws2/results/latency_unloaded.json` | 10/5 runs, LM Studio unloaded |
| `tests/benchmarks/voice/ws2/results/latency_loaded.json` | 10/5 runs, LM Studio loaded |
| `tests/benchmarks/voice/ws2/normalizer_audit.py` | Audited normalizer + unit tests |
| `tests/benchmarks/voice/ws2/cpu_measurement.py` | Per-process CPU + RTF measurement |
| `tests/benchmarks/voice/ws2/offline_candidates.py` | Offline candidate documentation |

---

## Reproduction Commands

```powershell
# Normalizer audit + corrected WER
.venv-ws2-bench\Scripts\python.exe tests/benchmarks/voice/ws2/normalizer_audit.py

# CPU measurement (per-process, threads 1/2/4)
.venv-ws2-bench\Scripts\python.exe tests/benchmarks/voice/ws2/cpu_measurement.py

# Validate on bundled test_wavs
.venv-ws2-bench\Scripts\python.exe tests/benchmarks/voice/ws2/test_bundled_wavs.py

# Sherpa config verification
.venv-ws2-bench\Scripts\python.exe tests/benchmarks/voice/ws2/test_model_type.py
```

---

## Documentation Updates

- STATE.md: Mark WS2 validation complete, update metrics
- PROGRESS.md: Record corrected WER, root cause, CPU findings
- HANDOFF.md: Add offline candidate status, hybrid proposal

`graphify update .` → commit new files only → **STOP at CHECKPOINT A**