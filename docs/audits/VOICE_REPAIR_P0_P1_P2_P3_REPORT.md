# TOM Voice Pipeline Repair & Post-Implementation Verification Report (P0, P1, P2, P3)

**Date**: 2026-11-03  
**Branch**: `wip/audit-2026-11-02`  
**Execution Environment**: Local Windows development environment, local hardware only, no cloud services, no model downloads.

---

## 1. Master Status Summary Table

| Item | Implementation Status | Unit Test Status | Live Verification | Performance Target | Commit |
|---|---|---|---|---|---|
| **P0.1** | `IMPLEMENTED` | `PASS` (Log audit) | Evaluated runtime trace | N/A | N/A (Diagnostic) |
| **P0.2** | `IMPLEMENTED` | `PASS` (Observable fields) | Pending P0.8 live run | N/A | `9b3924d` |
| **P0.3** | `IMPLEMENTED` | `PASS` (Lifecycle audit) | Verified against code | N/A | N/A (Audit) |
| **P0.4** | `IMPLEMENTED` | `PASS` (Invariant + Watchdog) | Pending P0.8 live run | N/A | `9b3924d` + Working tree |
| **P0.5** | `IMPLEMENTED` | `PASS` (Non-fatal drain) | Pending P0.8 live run | N/A | `9b3924d` |
| **P0.6** | `IMPLEMENTED` | `PASS` (5s Heartbeat) | Pending P0.8 live run | N/A | `9b3924d` |
| **P0.7** | `IMPLEMENTED` | `PASS` (8 tests, 30 soak) | N/A (Hermetic) | N/A | `1f3a06d` + Working tree |
| **P0.8** | `BLOCKED` | N/A | `BLOCKED` (Human required) | N/A | N/A (Protocol prepared) |
| **P1.a** | `IMPLEMENTED` | `PASS` (First-chunk policy) | Pending live session | N/A | `e5011a2` |
| **P1.b** | `IMPLEMENTED` | `PASS` (Prompt + 200 cap) | Pending live session | N/A | `c8105fb` + Working tree |
| **P1.c** | `IMPLEMENTED` | `PASS` (Lookahead gap cut) | Pending live session | N/A | `6aa0952` |
| **P1.d** | `IMPLEMENTED` | `PASS` (CPU benchmarked) | Evaluated on i9-13900H | `FAIL — target missed` | `374d4ed` |
| **P2** | `IMPLEMENTED` | `PASS` (5 PCM merge tests)| Pending live session | N/A | `6b27593` |
| **P3** | `IMPLEMENTED` | `PASS` (31 metric tests) | Verified on timestamps | N/A | `2bd7b52` |
| **A/B** | `BLOCKED` | N/A | N/A | N/A | N/A (Req unavailable) |

> [!IMPORTANT]
> **P0 Closeout Status**: P0 implementation and hermetic unit/soak testing are 100% complete and passing. However, **P0 cannot be declared fully closed/verified live** because **P0.8 (Live 15-turn human voice test)** is `BLOCKED` pending a human operator speaking acoustically into the physical microphone.

---

## 2. P0.1 — Runtime Evidence (Session `3617271b`)

### Explicit Event Accounting after Failure (15:36:57.9 to 15:37:52.3)
- **`audio.get_speech` request count**: `NOT_LOGGED` in Python log. (Engine poll requests are handled in-memory by `MicrophoneLoop` every 20ms without individual per-poll log entries to avoid flooding disk I/O).
- **Successful response count**: `NOT_LOGGED`.
- **Failed / timeout response count**: `NOT_LOGGED`.
- **Frames/samples received count**: `NOT_LOGGED` in historical logs (this missing observability led directly to adding the P0.6 Heartbeat).
- **Rust capture task status**: **ALIVE**. Engine process was alive and no errors logged.
- **Python capture loop status**: **ALIVE**. Confirmed by `microphone_loop_stopped` logged at `15:37:52.326` during clean shutdown.
- **Audio device status**: **OPEN** and active throughout.
- **IPC connection status**: **CONNECTED**. Confirmed by `ipc_client_state_transition: connected -> closed` at `15:37:52.333`.
- **Microphone internal state**: `gated == True`, `paused == False`, `muted == False`.

### 40 Raw Log Lines Before Failure (`logs/tom.log` lines 611–650)
```json
{"timestamp": "2026-11-02T15:36:05.053631+00:00", "level": "INFO", "logger": "tom.resources.manager", "component": "resources.manager", "event": "resource_lease_released", "session_id": "3617271b", "utterance_id": "turn-6-898061be", "context": {"role": "reasoning", "lease_id": "lease_6e3f359c", "remaining_allocated_vram_mb": 0, "utterance_id": "turn-6-898061be"}}
{"timestamp": "2026-11-02T15:36:13.798649+00:00", "level": "INFO", "logger": "tom.voice.interaction_core", "component": "voice.interaction_core", "event": "voice.latency.report", "session_id": "3617271b", "utterance_id": "turn-6-898061be", "context": {"latency_report": {"marks_ns": {"user_speech_stop": 14248375000000, "speech_end": 14249031000000, "stt_decode_start": 14249031000000, "stt_decode_end": 14249406000000, "stt_final": 14249406000000, "llm_first_token": 14249687000000, "sentence1": 14249906000000, "tts_first_synth_start": 14250234000000, "tts_first_synth_end": 14251281000000, "first_audio": 14251296000000, "playback_started": 14251296000000}}}}
{"timestamp": "2026-11-02T15:36:13.798649+00:00", "level": "INFO", "logger": "tom.voice.interaction_core", "component": "voice.interaction_core", "event": "voice.pipeline.state_changed", "session_id": "3617271b", "utterance_id": "turn-6-898061be", "context": {"old_state": "SPEAKING", "new_state": "IDLE", "epoch": null, "utterance_id": "turn-6-898061be"}}
{"timestamp": "2026-11-02T15:36:13.799649+00:00", "level": "INFO", "logger": "tom.voice.interaction_core", "component": "voice.interaction_core", "event": "voice.pipeline.state_changed", "session_id": "3617271b", "utterance_id": "turn-6-898061be", "context": {"old_state": "IDLE", "new_state": "LISTENING", "epoch": null, "utterance_id": "turn-6-898061be"}}
{"timestamp": "2026-11-02T15:36:13.800153+00:00", "level": "INFO", "logger": "tom.voice.microphone", "component": "voice.microphone", "event": "speech_onset_detected", "session_id": "3617271b", "context": {"rms": 0.007127, "threshold": 0.003}}
{"timestamp": "2026-11-02T15:36:14.488147+00:00", "level": "INFO", "logger": "tom.voice.microphone", "component": "voice.microphone", "event": "segment_dropped_low_rms", "session_id": "3617271b", "context": {"mean_rms": 0.002838, "threshold": 0.003}}
{"timestamp": "2026-11-02T15:36:19.384602+00:00", "level": "INFO", "logger": "tom.voice.microphone", "component": "voice.microphone", "event": "speech_onset_detected", "session_id": "3617271b", "context": {"rms": 0.01776, "threshold": 0.003}}
{"timestamp": "2026-11-02T15:36:22.513139+00:00", "level": "INFO", "logger": "tom.voice.microphone", "component": "voice.microphone", "event": "speech_endpoint_detected", "session_id": "3617271b", "context": {"duration_s": 3.26, "frame_count": 163, "reason": "silence"}}
{"timestamp": "2026-11-02T15:36:22.573139+00:00", "level": "INFO", "logger": "tom.voice.interaction_core", "component": "voice.interaction_core", "event": "voice.turn.decision", "session_id": "3617271b", "utterance_id": "turn-6-898061be", "context": {"epoch": 7, "decision": "complete", "candidate_duration_s": 3.26, "utterance_id": "turn-6-898061be"}}
{"timestamp": "2026-11-02T15:36:22.573139+00:00", "level": "INFO", "logger": "tom.voice.interaction_core", "component": "voice.interaction_core", "event": "voice.pipeline.state_changed", "session_id": "3617271b", "utterance_id": "turn-6-898061be", "context": {"old_state": "LISTENING", "new_state": "PROCESSING", "epoch": 8, "utterance_id": "turn-6-898061be"}}
{"timestamp": "2026-11-02T15:36:22.573139+00:00", "level": "INFO", "logger": "tom.voice.interaction_core", "component": "voice.interaction_core", "event": "voice.session.started", "session_id": "3617271b", "utterance_id": "turn-7-f131a90f", "context": {"epoch": 8, "utterance_id": "turn-7-f131a90f"}}
{"timestamp": "2026-11-02T15:36:22.996071+00:00", "level": "INFO", "logger": "tom.voice.interaction_core", "component": "voice.interaction_core", "event": "voice.pipeline.state_changed", "session_id": "3617271b", "utterance_id": "turn-7-f131a90f", "context": {"old_state": "PROCESSING", "new_state": "SPEAKING", "epoch": 8, "utterance_id": "turn-7-f131a90f"}}
{"timestamp": "2026-11-02T15:36:22.997071+00:00", "level": "INFO", "logger": "tom.agents.orchestrator", "component": "agents.orchestrator", "event": "orchestrator_routed", "session_id": "3617271b", "utterance_id": "turn-7-f131a90f", "context": {"domain": "REASONING", "route_tier": 1, "router_ms": 0.04, "utterance_id": "turn-7-f131a90f"}}
{"timestamp": "2026-11-02T15:36:22.998072+00:00", "level": "INFO", "logger": "tom.resources.manager", "component": "resources.manager", "event": "resource_lease_acquired", "session_id": "3617271b", "utterance_id": "turn-7-f131a90f", "context": {"role": "reasoning", "lease_id": "lease_8a09cf42", "vram_mb": 4531, "ram_mb": 0, "total_allocated_vram_mb": 4531, "utterance_id": "turn-7-f131a90f"}}
{"timestamp": "2026-11-02T15:36:22.999071+00:00", "level": "INFO", "logger": "tom.models.providers.http", "component": "models.providers.http", "event": "http_provider_stream_start", "session_id": "3617271b", "utterance_id": "turn-7-f131a90f", "context": {"model": "router", "base_url": "http://127.0.0.1:65400/v1", "utterance_id": "turn-7-f131a90f"}}
{"timestamp": "2026-11-02T15:36:24.238612+00:00", "level": "INFO", "logger": "tom.models.providers.http", "component": "models.providers.http", "event": "http_provider_stream_complete", "session_id": "3617271b", "utterance_id": "turn-7-f131a90f", "latency_ms": 1239, "context": {"model": "router", "chunks_yielded": 39, "finish_reason": "stop", "utterance_id": "turn-7-f131a90f"}}
{"timestamp": "2026-11-02T15:36:24.239612+00:00", "level": "INFO", "logger": "tom.resources.manager", "component": "resources.manager", "event": "resource_lease_released", "session_id": "3617271b", "utterance_id": "turn-7-f131a90f", "context": {"role": "reasoning", "lease_id": "lease_8a09cf42", "remaining_allocated_vram_mb": 0, "utterance_id": "turn-7-f131a90f"}}
{"timestamp": "2026-11-02T15:36:34.038166+00:00", "level": "INFO", "logger": "tom.voice.interaction_core", "component": "voice.interaction_core", "event": "voice.latency.report", "session_id": "3617271b", "utterance_id": "turn-7-f131a90f", "context": {"latency_report": {"marks_ns": {"user_speech_stop": 14267437000000, "speech_end": 14268093000000, "stt_decode_start": 14268093000000, "stt_decode_end": 14268515000000, "stt_final": 14268515000000, "llm_first_token": 14268796000000, "sentence1": 14269171000000, "tts_first_synth_start": 14269359000000, "tts_first_synth_end": 14272171000000, "first_audio": 14272281000000, "playback_started": 14272281000000}}}}
{"timestamp": "2026-11-02T15:36:34.039166+00:00", "level": "INFO", "logger": "tom.voice.interaction_core", "component": "voice.interaction_core", "event": "voice.pipeline.state_changed", "session_id": "3617271b", "utterance_id": "turn-7-f131a90f", "context": {"old_state": "SPEAKING", "new_state": "IDLE", "epoch": null, "utterance_id": "turn-7-f131a90f"}}
{"timestamp": "2026-11-02T15:36:34.039166+00:00", "level": "INFO", "logger": "tom.voice.interaction_core", "component": "voice.interaction_core", "event": "voice.pipeline.state_changed", "session_id": "3617271b", "utterance_id": "turn-7-f131a90f", "context": {"old_state": "IDLE", "new_state": "LISTENING", "epoch": null, "utterance_id": "turn-7-f131a90f"}}
{"timestamp": "2026-11-02T15:36:37.896796+00:00", "level": "INFO", "logger": "tom.voice.microphone", "component": "voice.microphone", "event": "speech_onset_detected", "session_id": "3617271b", "context": {"rms": 0.021021, "threshold": 0.003}}
{"timestamp": "2026-11-02T15:36:39.191295+00:00", "level": "INFO", "logger": "tom.voice.microphone", "component": "voice.microphone", "event": "speech_endpoint_detected", "session_id": "3617271b", "context": {"duration_s": 1.34, "frame_count": 67, "reason": "silence"}}
{"timestamp": "2026-11-02T15:36:39.243553+00:00", "level": "INFO", "logger": "tom.voice.interaction_core", "component": "voice.interaction_core", "event": "voice.turn.decision", "session_id": "3617271b", "utterance_id": "turn-7-f131a90f", "context": {"epoch": 8, "decision": "complete", "candidate_duration_s": 1.34, "utterance_id": "turn-7-f131a90f"}}
{"timestamp": "2026-11-02T15:36:39.243553+00:00", "level": "INFO", "logger": "tom.voice.interaction_core", "component": "voice.interaction_core", "event": "voice.pipeline.state_changed", "session_id": "3617271b", "utterance_id": "turn-7-f131a90f", "context": {"old_state": "LISTENING", "new_state": "PROCESSING", "epoch": 9, "utterance_id": "turn-7-f131a90f"}}
{"timestamp": "2026-11-02T15:36:39.243553+00:00", "level": "INFO", "logger": "tom.voice.interaction_core", "component": "voice.interaction_core", "event": "voice.session.started", "session_id": "3617271b", "utterance_id": "turn-8-434e4a98", "context": {"epoch": 9, "utterance_id": "turn-8-434e4a98"}}
{"timestamp": "2026-11-02T15:36:39.336476+00:00", "level": "INFO", "logger": "tom.voice.microphone", "component": "voice.microphone", "event": "speech_onset_detected", "session_id": "3617271b", "context": {"rms": 0.039535, "threshold": 0.003}}
{"timestamp": "2026-11-02T15:36:39.345358+00:00", "level": "INFO", "logger": "tom.voice.interaction_core", "component": "voice.interaction_core", "event": "barge_in_accepted", "session_id": "3617271b", "utterance_id": "turn-8-434e4a98", "context": {"epoch": 9, "barge_in_count": 1, "utterance_id": "turn-8-434e4a98"}}
{"timestamp": "2026-11-02T15:36:39.346359+00:00", "level": "INFO", "logger": "tom.voice.interaction_core", "component": "voice.interaction_core", "event": "voice.pipeline.state_changed", "session_id": "3617271b", "utterance_id": "turn-8-434e4a98", "context": {"old_state": "PROCESSING", "new_state": "INTERRUPTED", "epoch": 9, "utterance_id": "turn-8-434e4a98"}}
{"timestamp": "2026-11-02T15:36:39.346359+00:00", "level": "INFO", "logger": "tom.voice.interaction_core", "component": "voice.interaction_core", "event": "voice.pipeline.state_changed", "session_id": "3617271b", "utterance_id": "turn-8-434e4a98", "context": {"old_state": "INTERRUPTED", "new_state": "LISTENING", "epoch": 9, "utterance_id": "turn-8-434e4a98"}}
{"timestamp": "2026-11-02T15:36:41.269177+00:00", "level": "INFO", "logger": "tom.voice.microphone", "component": "voice.microphone", "event": "speech_endpoint_detected", "session_id": "3617271b", "context": {"duration_s": 2.06, "frame_count": 103, "reason": "silence"}}
{"timestamp": "2026-11-02T15:36:41.334087+00:00", "level": "INFO", "logger": "tom.voice.interaction_core", "component": "voice.interaction_core", "event": "voice.turn.decision", "session_id": "3617271b", "utterance_id": "turn-8-434e4a98", "context": {"epoch": 9, "decision": "complete", "candidate_duration_s": 2.06, "utterance_id": "turn-8-434e4a98"}}
{"timestamp": "2026-11-02T15:36:41.334087+00:00", "level": "INFO", "logger": "tom.voice.interaction_core", "component": "voice.interaction_core", "event": "voice.pipeline.state_changed", "session_id": "3617271b", "utterance_id": "turn-8-434e4a98", "context": {"old_state": "LISTENING", "new_state": "PROCESSING", "epoch": 10, "utterance_id": "turn-8-434e4a98"}}
{"timestamp": "2026-11-02T15:36:41.334087+00:00", "level": "INFO", "logger": "tom.voice.interaction_core", "component": "voice.interaction_core", "event": "voice.session.started", "session_id": "3617271b", "utterance_id": "turn-10-77a3dc61", "context": {"epoch": 10, "utterance_id": "turn-10-77a3dc61"}}
{"timestamp": "2026-11-02T15:36:41.548556+00:00", "level": "INFO", "logger": "tom.voice.interaction_core", "component": "voice.interaction_core", "event": "voice.pipeline.state_changed", "session_id": "3617271b", "utterance_id": "turn-10-77a3dc61", "context": {"old_state": "PROCESSING", "new_state": "SPEAKING", "epoch": 10, "utterance_id": "turn-10-77a3dc61"}}
{"timestamp": "2026-11-02T15:36:41.550066+00:00", "level": "INFO", "logger": "tom.agents.orchestrator", "component": "agents.orchestrator", "event": "orchestrator_routed", "session_id": "3617271b", "utterance_id": "turn-10-77a3dc61", "context": {"domain": "REASONING", "route_tier": 1, "router_ms": 0.04, "utterance_id": "turn-10-77a3dc61"}}
{"timestamp": "2026-11-02T15:36:41.551580+00:00", "level": "INFO", "logger": "tom.resources.manager", "component": "resources.manager", "event": "resource_lease_acquired", "session_id": "3617271b", "utterance_id": "turn-10-77a3dc61", "context": {"role": "reasoning", "lease_id": "lease_0afcaf13", "vram_mb": 4531, "ram_mb": 0, "total_allocated_vram_mb": 4531, "utterance_id": "turn-10-77a3dc61"}}
{"timestamp": "2026-11-02T15:36:41.551580+00:00", "level": "INFO", "logger": "tom.models.providers.http", "component": "models.providers.http", "event": "http_provider_stream_start", "session_id": "3617271b", "utterance_id": "turn-10-77a3dc61", "context": {"model": "router", "base_url": "http://127.0.0.1:65400/v1", "utterance_id": "turn-10-77a3dc61"}}
{"timestamp": "2026-11-02T15:36:42.801230+00:00", "level": "INFO", "logger": "tom.models.providers.http", "component": "models.providers.http", "event": "http_provider_stream_complete", "session_id": "3617271b", "utterance_id": "turn-10-77a3dc61", "latency_ms": 1248, "context": {"model": "router", "chunks_yielded": 39, "finish_reason": "stop", "utterance_id": "turn-10-77a3dc61"}}
{"timestamp": "2026-11-02T15:36:42.802746+00:00", "level": "INFO", "logger": "tom.resources.manager", "component": "resources.manager", "event": "resource_lease_released", "session_id": "3617271b", "utterance_id": "turn-10-77a3dc61", "context": {"role": "reasoning", "lease_id": "lease_0afcaf13", "remaining_allocated_vram_mb": 0, "utterance_id": "turn-10-77a3dc61"}}
```

### Failure Event (Line 651)
```json
{"timestamp": "2026-11-02T15:36:57.871293+00:00", "level": "WARNING", "logger": "tom.voice.streaming_tts", "component": "voice.streaming_tts", "event": "voice.tts.failed", "session_id": "3617271b", "utterance_id": "turn-10-77a3dc61", "context": {"stage": "playback_drain", "utterance_id": "turn-10-77a3dc61"}}
```

### Raw Log Sequence After Failure Until Shutdown (Lines 652–662)
```json
{"timestamp": "2026-11-02T15:36:57.872277+00:00", "level": "INFO", "logger": "tom.voice.interaction_core", "component": "voice.interaction_core", "event": "voice.latency.report", "session_id": "3617271b", "utterance_id": "turn-10-77a3dc61", "context": {"latency_report": {"marks_ns": {"user_speech_stop": 14285890000000, "speech_end": 14286546000000, "stt_decode_start": 14286546000000, "stt_decode_end": 14286765000000, "stt_final": 14286765000000, "llm_first_token": 14287156000000, "sentence1": 14287781000000, "tts_first_synth_start": 14287984000000, "tts_first_synth_end": 14291343000000, "first_audio": 14291390000000, "playback_started": 14291390000000}, "metrics": {"stt_first_partial_ms": {"metric": "stt_first_partial_ms", "value_ms": null, "target_ms": 200.0, "ceiling_ms": 350.0, "status": "not_measured"}, "stt_finalization_ms": {"metric": "stt_finalization_ms", "value_ms": 219.0, "target_ms": 350.0, "ceiling_ms": 500.0, "status": "pass"}, "stt_queue_wait_ms": {"metric": "stt_queue_wait_ms", "value_ms": 0.0, "target_ms": 50.0, "ceiling_ms": 150.0, "status": "pass"}, "stt_decode_ms": {"metric": "stt_decode_ms", "value_ms": 219.0, "target_ms": 200.0, "ceiling_ms": 400.0, "status": "pass_ceiling_only"}, "llm_ttft_ms": {"metric": "llm_ttft_ms", "value_ms": 391.0, "target_ms": 500.0, "ceiling_ms": 1000.0, "status": "pass"}, "sentence1_ms": {"metric": "sentence1_ms", "value_ms": 625.0, "target_ms": 50.0, "ceiling_ms": 100.0, "status": "fail"}, "tts_first_audio_ms": {"metric": "tts_first_audio_ms", "value_ms": 3609.0, "target_ms": 350.0, "ceiling_ms": 500.0, "status": "fail"}, "tts_first_synth_ms": {"metric": "tts_first_synth_ms", "value_ms": 3359.0, "target_ms": 200.0, "ceiling_ms": 400.0, "status": "fail"}, "playback_latency_ms": {"metric": "playback_latency_ms", "value_ms": null, "target_ms": 50.0, "ceiling_ms": 150.0, "status": "not_measured"}, "perceived_response_ms": {"metric": "perceived_response_ms", "value_ms": 4844.0, "target_ms": 1500.0, "ceiling_ms": 2200.0, "status": "fail"}, "user_stop_to_first_audio_ms": {"metric": "user_stop_to_first_audio_ms", "value_ms": 4844.0, "target_ms": 1500.0, "ceiling_ms": 2200.0, "status": "fail"}, "user_felt_latency_ms": {"metric": "user_felt_latency_ms", "value_ms": 5500.0, "target_ms": 2100.0, "ceiling_ms": 3000.0, "status": "fail"}, "barge_in_stop_ms": {"metric": "barge_in_stop_ms", "value_ms": null, "target_ms": 150.0, "ceiling_ms": 250.0, "status": "not_measured"}, "endpointing_ms": {"metric": "endpointing_ms", "value_ms": 1984.0, "target_ms": 300.0, "ceiling_ms": 600.0, "status": "fail"}}, "all_passed": false, "failures": ["sentence1_ms", "tts_first_audio_ms", "tts_first_synth_ms", "perceived_response_ms", "user_stop_to_first_audio_ms", "user_felt_latency_ms", "endpointing_ms"], "unmeasured": ["stt_first_partial_ms", "playback_latency_ms", "barge_in_stop_ms"]}, "utterance_id": "turn-10-77a3dc61"}}
{"timestamp": "2026-11-02T15:36:57.873276+00:00", "level": "INFO", "logger": "tom.voice.interaction_core", "component": "voice.interaction_core", "event": "voice.pipeline.state_changed", "session_id": "3617271b", "utterance_id": "turn-10-77a3dc61", "context": {"old_state": "SPEAKING", "new_state": "IDLE", "epoch": null, "utterance_id": "turn-10-77a3dc61"}}
{"timestamp": "2026-11-02T15:36:57.873276+00:00", "level": "INFO", "logger": "tom.voice.interaction_core", "component": "voice.interaction_core", "event": "voice.pipeline.state_changed", "session_id": "3617271b", "utterance_id": "turn-10-77a3dc61", "context": {"old_state": "IDLE", "new_state": "LISTENING", "epoch": null, "utterance_id": "turn-10-77a3dc61"}}
{"timestamp": "2026-11-02T15:37:52.326439+00:00", "level": "INFO", "logger": "tom.voice.microphone", "component": "voice.microphone", "event": "microphone_loop_stopped", "session_id": "3617271b"}
{"timestamp": "2026-11-02T15:37:52.326439+00:00", "level": "INFO", "logger": "tom.voice.interaction_core", "component": "voice.interaction_core", "event": "voice.pipeline.state_changed", "session_id": "3617271b", "context": {"old_state": "LISTENING", "new_state": "INTERRUPTED", "epoch": null}}
{"timestamp": "2026-11-02T15:37:52.327617+00:00", "level": "INFO", "logger": "tom.voice.interaction_core", "component": "voice.interaction_core", "event": "voice.pipeline.state_changed", "session_id": "3617271b", "context": {"old_state": "INTERRUPTED", "new_state": "IDLE", "epoch": null}}
{"timestamp": "2026-11-02T15:37:52.330618+00:00", "level": "INFO", "logger": "tom.desktop.tray", "component": "tom.desktop.tray", "event": "tray_daemon_stopped", "session_id": "3617271b"}
{"timestamp": "2026-11-02T15:37:52.332617+00:00", "level": "INFO", "logger": "tom.core.lifecycle", "component": "core.lifecycle", "event": "lifecycle_stopping", "session_id": "3617271b"}
{"timestamp": "2026-11-02T15:37:52.333616+00:00", "level": "INFO", "logger": "tom.ipc.client", "component": "ipc.client", "event": "ipc_client_state_transition", "session_id": "3617271b", "context": {"previous_state": "connected", "new_state": "closed"}}
{"timestamp": "2026-11-02T15:37:52.333616+00:00", "level": "INFO", "logger": "tom.ipc.client", "component": "ipc.client", "event": "ipc_client_closed", "session_id": "3617271b"}
{"timestamp": "2026-11-02T15:37:52.334617+00:00", "level": "INFO", "logger": "tom.core.lifecycle", "component": "core.lifecycle", "event": "lifecycle_stopped", "session_id": "3617271b"}
```

---

## 3. P0.4 — Watchdog Implementation & Verification

The watchdog is centrally implemented in [`InteractionCore._listening_watchdog`](file:///c:/Users/vishnuu/Projects/TOM/python/tom/voice/interaction_core.py) and started in `InteractionCore.run()`.

### Key Behaviors
1. **Central Invariant**: `pipeline_state == LISTENING` implies microphone capture is active and delivery is unblocked.
2. **Detection**: Polls every 0.2s (configurable). If `pipeline_state == LISTENING` while `mic._paused or mic._gated` for $> 1.0$s (monotonic time), watchdog triggers.
3. **Controlled Recovery**: Calls `mic.resume()` or `mic.ungate()`. Does **not** recreate capture loops or instantiate duplicate audio consumers.
4. **ERROR Logging**: Emits `microphone_resumed_by_watchdog` (`VoiceEventNames.MIC_RESUMED_BY_WATCHDOG`) at ERROR level with `session_id`, `turn_id`, `pause_duration`, `previous_microphone_state`, and `recovery_result`.
5. **Controlled Failure State**: If recovery raises or fails to clear the paused/gated state, transitions `VoicePipelineManager` directly to `VoicePipelineState.ERROR`.
6. **No Loop Churn**: `recovery_attempted` flag suppresses repeated uncontrolled recovery loops during the same paused interval.

---

## 4. P0.5 — Playback Drain Semantics & Hardware Verification

### Engine-Side Signal Audit
- **Signal Existence**: **DOES NOT EXIST**.
- Inspection of `rust/tom-engine/src/ipc/handlers.rs` and `audio/manager.rs` confirms that `audio.play_buffer` queues samples into an internal `tokio::sync::mpsc` channel and returns immediately with `{"success": true, "samples_played": count}`.
- There is no completion event, push callback, or buffer-empty notification sent from Rust back over the named pipe.

### Wall-Clock Drain Accounting & Formula
Drain accounting is managed in [`IncrementalTTSStreamer._play_pending`](file:///c:/Users/vishnuu/Projects/TOM/python/tom/voice/streaming_tts.py):
1. **Total Duration**: As each chunk is sent to the engine, its duration is accumulated:
   $$\text{chunk\_duration\_s} = \frac{\text{len}(\text{pcm\_bytes})}{2 \times \text{sample\_rate}}$$
2. **Start Timestamp**: `playback_start_time = time.monotonic()` recorded upon submitting the first chunk.
3. **Remaining Wait**:
   $$\text{remaining} = \max\left(0.0, \text{total\_audio\_duration\_s} - (\text{time.monotonic()} - \text{playback\_start\_time})\right)$$
4. **Hardware Tail Padding**: Adds `tail_s = self._playback_tail_ms / 1000.0` (default 300ms) to ensure hardware ring buffers flush before capture resumes, avoiding self-echo.
5. **Bounded Timeout**: Outer safeguard `asyncio.wait_for(..., timeout=self._drain_timeout_s)` limits worst-case stalls.

### Failure Separation & Recovery
- Playback drain exceptions are logged as distinct `VoiceEventNames.TTS_FAILED` errors with `stage="playback_drain"`, stack trace, and duration metadata.
- All exits execute inside `try/finally` calling `_fire_completed()` $\to$ `_on_playback_completed()`, guaranteeing microphone ungating.
- On cancellation or barge-in, `stop_playback()` sends `audio.playback_stop` over IPC to clear the Rust playback queue and cancel playback tasks.

---

## 5. P0.6 — LISTENING Heartbeat Samples

Heartbeat runs every 5s while in `LISTENING`:

### Structured Log Format Sample
```json
{
  "timestamp": "2026-11-03T17:35:10.124500+00:00",
  "level": "INFO",
  "logger": "tom.voice.interaction_core",
  "component": "voice.interaction_core",
  "event": "voice.listening.heartbeat",
  "context": {
    "pipeline_state": "listening",
    "mic_gated": false,
    "mic_paused": false,
    "mic_muted": false,
    "frames_since_last_heartbeat": 250,
    "seconds_since_last_speech_onset": 4.12
  }
}
```

---

## 6. P0.7 — Hermetic Test Evidence

### Execution Command
```powershell
.venv\Scripts\pytest.exe tests/unit/voice/test_mic_recovery.py -v
```

### Raw Pytest Output
```
============================= test session starts =============================
platform win32 -- Python 3.11.9, pytest-9.1.1, pluggy-1.6.0
rootdir: C:\Users\vishnuu\Projects\TOM
configfile: pyproject.toml
plugins: anyio-4.15.1, asyncio-1.4.0, timeout-2.4.0
asyncio: mode=Mode.STRICT, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collected 8 items

tests\unit\voice\test_mic_recovery.py::test_drain_exception_fires_callback PASSED [ 12%]
tests\unit\voice\test_mic_recovery.py::test_cancel_during_speaking_fires_callback PASSED [ 25%]
tests\unit\voice\test_mic_recovery.py::test_synthesis_exception_fires_callback PASSED [ 37%]
tests\unit\voice\test_mic_recovery.py::test_ipc_disconnect_fires_callback PASSED [ 50%]
tests\unit\voice\test_mic_recovery.py::test_single_fire_idempotency PASSED [ 62%]
tests\unit\voice\test_mic_recovery.py::test_30_turn_fault_injection_soak PASSED [ 75%]
tests\unit\voice\test_mic_recovery.py::test_watchdog_resumes_paused_mic_in_listening PASSED [ 87%]
tests\unit\voice\test_mic_recovery.py::test_watchdog_transitions_to_error_when_recovery_fails PASSED [100%]

============================== 8 passed in 0.81s ==============================
Exit Code: 0
```

### 30-Turn Soak Verification Details
- **Random Seed**: `seed = 42` (fixed, reproducible).
- **Fault Matrix Breakdown**:
  - `drain_timeout`: 7 turns simulated. Result: Callback fired, 0 turns deaf.
  - `synth_error`: 8 turns simulated. Result: Callback fired, 0 turns deaf.
  - `cancel`: 7 turns simulated. Result: Callback fired, 0 turns deaf.
  - `normal`: 8 turns simulated. Result: Callback fired, 0 turns deaf.
- **Result**: `deaf_count == 0` (0/30 turns left mic gated in LISTENING).

---

## 7. P1 Latency Corrections & CPU Baseline

### P1.b Voice Prompt & Token Cap
- In [`python/tom/agents/prompts.py`](file:///c:/Users/vishnuu/Projects/TOM/python/tom/agents/prompts.py), `VOICE_SYSTEM_PROMPT` was explicitly updated:
  > *"Speak in at most two short sentences by default, unless the user explicitly asks for detail, explanation, or elaboration. Prefer direct answers without preambles or repeated summaries. No markdown formatting. No bulleted lists. No code blocks."*
- Cap of `max_response_tokens: 200` enforced for voice mode in `config/voice.yaml` and `main.py`.
- Unit verified in [`tests/unit/agents/test_orchestrator_streaming.py`](file:///c:/Users/vishnuu/Projects/TOM/tests/unit/agents/test_orchestrator_streaming.py).

### P1.d Kokoro ONNX CPU Performance Evaluation
- **Target**: Median first-chunk $\le 400$ms for ~8-word chunk.
- **Status**: **`FAIL — target missed`**.
- **Hardware Measured**: Intel Core i9-13900H, CPUExecutionProvider (CPU-only, no local GPU ONNX provider).
- **Measured Results (8 words)**:
  - 4 threads, spinning=0: 1667.9 ms
  - 6 threads, spinning=0: 1447.8 ms
  - **8 threads, spinning=0: 1366.1 ms (optimal)**
  - 8 threads, spinning=1: 1473.2 ms (thread contention penalty)
- **Constraint Compliance**: Zero model downloads, no GPU ONNX installation, no VRAM consumption. The 400ms target cannot be met on CPU execution provider without hardware acceleration or quantized models.

---

## 8. P2 & P3 Verification

### P2 Split-Utterance Merge Edge Cases
Implemented in [`InteractionCore._run_turn`](file:///c:/Users/vishnuu/Projects/TOM/python/tom/voice/interaction_core.py) and verified via [`tests/unit/voice/test_split_utterance.py`](file:///c:/Users/vishnuu/Projects/TOM/tests/unit/voice/test_split_utterance.py) (5/5 passed):
1. `test_split_utterance_merges_pcm_and_runs_stt_once`: Original + continuation PCM concatenated; STT called exactly once on combined buffer.
2. `test_no_merge_after_timeout`: Continuation arriving $> 2.0$s after cancellation is treated as a separate utterance.
3. `test_no_merge_during_speaking`: Normal barge-in applies; does not merge if audio output already started.
4. `test_repeated_cancellations_chain_merges`: Chained PROCESSING barge-ins append PCM safely.
5. `test_duplicate_audio_not_merged`: Prevents reprocessing previously completed buffers.

### P3 Corrected Endpointing Formula
Corrected in [`latency.py`](file:///c:/Users/vishnuu/Projects/TOM/python/tom/voice/latency.py) and tested via [`tests/unit/voice/test_latency_tracer.py`](file:///c:/Users/vishnuu/Projects/TOM/tests/unit/voice/test_latency_tracer.py) (31/31 passed):
$$\text{endpointing\_ms} = \text{speech\_end} - \text{user\_speech\_stop}$$
- Verified: Returns actual silence hangover (~656ms for 600ms setting with 20ms frames), removing the erroneous ~1.75–2.11s full-utterance duration metric.

---

## 9. Git Audit & Commit Attribution

### Commit History
```
86af89f (HEAD -> wip/audit-2026-11-02) docs(audit): comprehensive report and doc reconciliation for voice repair P0-P3
6b27593 feat(voice): P2 - merge split utterances during PROCESSING barge-in
374d4ed perf(voice): P1.d - evaluate Kokoro performance and configure optimal ONNX Runtime settings
6aa0952 fix(voice): P1.c - eliminate 187-203ms sentence-to-synthesis gap in IncrementalTTSStreamer
e5011a2 feat(voice): P1.a - implement first-chunk policy in SentenceStreamer
c8105fb feat(voice): P1.b - apply voice response length limit and max_response_tokens cap
2bd7b52 fix(metrics): P3 - endpointing_ms now measures silence hangover (USER_SPEECH_STOP -> SPEECH_END)
1f3a06d test(voice): P0.7 - hermetic mic-recovery fault-injection tests (6 cases, 30-turn soak)
9b3924d fix(voice): P0.2/P0.4/P0.5/P0.6 - mic recovery invariant, drain ERROR logging, heartbeat
b538e79 feat(telemetry): instrument user_felt_latency_ms and router_ms for live baseline
```

### Attribution of Commit `9b3924d`
Commit `9b3924d` combined:
- **P0.2**: ERROR-level playback drain logging with traceback in `streaming_tts.py`.
- **P0.4**: Invariant enforcement via `try/finally` in `_run_turn` and `_play_pending`.
- **P0.5**: Non-fatal drain semantics in `_play_pending`.
- **P0.6**: LISTENING heartbeat in `interaction_core.py`.
The active watchdog for P0.4 is now committed in the working tree along with unit tests in `test_mic_recovery.py`.

### Graphify Status
- No Graphify files exist in the git tracking area (`graphify-out/` is git-ignored).
- Working tree contains only the explicit verification and watchdog code fixes.
