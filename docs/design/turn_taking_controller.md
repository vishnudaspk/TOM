# Turn-Taking Controller Design Note

**Status**: PROPOSED — for WS4-WS6 implementation  
**Owner**: VoicePipelineManager (no new state owner)  
**Sources**: FireRedChat README/paper [UNVERIFIED], Pipecat turn-strategy docs [SOURCE: pipecat/turns/user_turn_strategies.py, pipecat/turns/user_stop/*.py]

---

## 1. Turn-Taking Controller (owned by VoicePipelineManager)

### States
```
IDLE → USER_SPEAKING → USER_PAUSED → THINKING → BOT_SPEAKING → IDLE
```

### Events & Transitions
| From State | Event | To State | Action |
|------------|-------|----------|--------|
| IDLE | VADUserStartedSpeakingFrame | USER_SPEAKING | Start STT streaming, arm Smart Turn |
| USER_SPEAKING | SmartTurn=COMPLETE | USER_PAUSED | Queue for LLM, start thinking |
| USER_SPEAKING | SmartTurn=INCOMPLETE | USER_SPEAKING | Continue streaming, hold |
| USER_SPEAKING | VADUserStoppedSpeakingFrame (no Smart Turn) | USER_PAUSED | Fallback to VAD hangover |
| USER_PAUSED | LLM first token | THINKING | Cancel partial STT, start TTS pipeline |
| THINKING | TTS first chunk | BOT_SPEAKING | Start playback, enable barge-in |
| BOT_SPEAKING | InterruptionFrame | USER_SPEAKING | Cancel TTS, flush STT, restart |
| BOT_SPEAKING | BotStoppedSpeakingFrame | IDLE | Record turn, update referents |

### Listener-Loop / Thinker-Loop Split
| Loop | Responsibility | Latency Budget |
|------|----------------|----------------|
| **Listener (fast, CPU, always on)** | VAD + Smart Turn inference, STT streaming partials, interruption detection | < 50ms per frame |
| **Thinker (LLM, async)** | LLM inference, TTS synthesis, tool execution, referent resolution | Unbounded (streaming) |

---

## 2. Backchannel & Acknowledgement Injection Path

### Separation from Barge-In/Interruption
| Path | Trigger | Action | Cancellation |
|------|---------|--------|--------------|
| **Backchannel** | Listener detects hesitation (Smart Turn INCOMPLETE + VAD pause < 500ms) | Inject "mm-hm", "uh-huh" via TTS | NOT cancelled by user speech; `interruptible=False` |
| **Acknowledgement** | Thinker produces first LLM token | Inject "OK", "Got it" via TTS | Cancelled if user interrupts |
| **Barge-In** | InterruptionFrame during BOT_SPEAKING | Stop TTS immediately, flush STT | Cancels ALL pending audio |

### Policy Knobs (from WS4 brief, constrained by Part 2.3)
- `backchannel_enabled: bool` — default True
- `backchannel_min_pause_ms: int` — default 300ms (validated: VAD baseline 100% correct at 300ms)
- `acknowledgement_enabled: bool` — default True
- `acknowledgement_phrases: List[str]` — ["OK", "Got it", "Sure"]
- `interruptible_backchannel: bool` — default False (Part 2.3: `interruptible=False` works)

---

## 3. Barge-In Policy

### Minimum Duration / Word Count
- **Problem**: User's own "mm-hm" or "uh-huh" during bot speech should NOT stop TTS
- **Solution**: Require ≥ 2 words OR ≥ 500ms continuous speech before triggering interruption
- **Implementation**: In `UserStartedSpeakingFrame` handler, buffer user audio for 500ms; if still speaking and ≥ 2 words (STT partial), broadcast InterruptionFrame

### Personalized VAD (Speaker Embedding)
- **Cost**: UNVERIFIED on CPU — speaker embedding (e.g., ECAPA-TDNN) ~50-100ms per utterance on i9-13900H
- **Recommendation**: Defer to bounded spike (WS5); for now, use energy-based discrimination (user mic vs bot speaker separation via audio routing)

---

## 4. Pipecat Adoption Decision

| Component | Adopt as Library? | Use Pipeline? | Rationale (Part 2 Data) |
|-----------|-------------------|---------------|-------------------------|
| **LocalSmartTurnAnalyzerV3** | YES | NO | 100% false-complete on hesitation prefix makes it unsafe as sole endpoint detector; use with VAD hangover override |
| **SileroVADAnalyzer** | YES | NO | 100% correct on hesitation sets; reliable, low latency |
| **Turn Strategies (UserTurnStrategies)** | YES | NO | Flexible extension points; adopt patterns not pipeline |
| **Pipeline/Runner/Task** | NO | DEFER | Adds 32 deps, 200ms import, no backchannel support; our VoicePipelineManager already owns state |

### Recommendation: **Use Pipecat components as libraries; do NOT use Pipecat pipeline inside VoicePipelineManager**

---

## 5. Risks & Revised Task List (WS4-WS6)

### Risks
| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Dual ONNX runtimes (sherpa-onnx + pipecat ONNX) | HIGH | CPU contention | Run in same process with `ORT_SEQUENTIAL`; monitor RAM |
| CPU contention (STT + Kokoro + Smart Turn) | MEDIUM | Latency spikes | Limit Smart Turn to 1 thread; STT 4 threads; Kokoro 2 threads |
| Smart Turn false-completes | HIGH (measured 100%) | Premature cutoff | Always combine with VAD hangover; require dual confirmation |

### Revised Task List

| WS | Task | Exit Criteria |
|----|------|---------------|
| **WS4** | Implement TurnTakingController in VoicePipelineManager with listener/thinker split | Unit tests pass; <50ms listener latency; <5ms thinker dispatch |
| **WS5** | Add backchannel/acknowledgement injection path with policy knobs | Backchannel fires on hesitation; ack fires on first token; both respect interruption |
| **WS6** | Hybrid STT: Moonshine-tiny offline final + streaming STT partials; evaluate on real mic | Moonshine-tiny p50 ≤ 350ms; WER < 13.6% (faster-whisper baseline); partial latency < 500ms |

---

**Next**: Begin WS4 when instructed. Phase 9 stays OPEN.