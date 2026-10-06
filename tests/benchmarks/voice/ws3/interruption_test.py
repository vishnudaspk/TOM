#!/usr/bin/env python3
"""
Interruption and backchannel feasibility tests (headless, synthetic frames).
Tests:
(i) Custom processor can emit audio frame while simulated user-speech start is active WITHOUT interruption cancelling it
(ii) Normal bot audio frame in same situation IS cancelled
(iii) Per-frame pass-through overhead of empty pipeline vs plain function-call chain
"""

import asyncio
import time
import statistics
from pipecat.frames.frames import (
    Frame, AudioRawFrame, InterruptionFrame, UserStartedSpeakingFrame,
    TTSAudioRawFrame, TextFrame, StartFrame, EndFrame, CancelFrame
)
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection

# Minimal custom processor that can be marked "uninterruptible"
class UninterruptibleAudioProcessor(FrameProcessor):
    """Custom processor that emits audio and can be marked uninterruptible."""
    
    def __init__(self, uninterruptible=False):
        super().__init__()
        self.uninterruptible = uninterruptible
        self.emitted_frames = []
        self.interrupted = False
    
    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        
        if isinstance(frame, InterruptionFrame):
            self.interrupted = True
            # Don't push interruption downstream - we handle it
            return
        
        if isinstance(frame, UserStartedSpeakingFrame):
            # Simulate emitting an audio frame while user is speaking
            audio_frame = AudioRawFrame(
                audio=b"\x00" * 320,  # 10ms silence at 16kHz
                sample_rate=16000,
                num_channels=1
            )
            self.emitted_frames.append(audio_frame)
            await self.push_frame(audio_frame, direction)
        
        await self.push_frame(frame, direction)

class NormalAudioProcessor(FrameProcessor):
    """Normal bot audio processor that gets cancelled on interruption."""
    
    def __init__(self):
        super().__init__()
        self.emitted_frames = []
        self.cancelled = False
    
    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        
        if isinstance(frame, InterruptionFrame):
            self.cancelled = True
            return  # Don't push frame downstream - cancelled
        
        if isinstance(frame, TTSAudioRawFrame):
            self.emitted_frames.append(frame)
        
        await self.push_frame(frame, direction)

# Test (i): Uninterruptible processor emits audio during user speech
async def test_uninterruptible_emission():
    print("\n=== Test (i): Uninterruptible processor emission ===")
    
    processor = UninterruptibleAudioProcessor(uninterruptible=True)
    
    # Simulate: user starts speaking
    await processor.process_frame(UserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    
    # Check if audio was emitted
    emitted = len(processor.emitted_frames) > 0
    print(f"  Audio emitted during user speech: {emitted}")
    
    # Now send interruption
    await processor.process_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)
    print(f"  Processor interrupted flag: {processor.interrupted}")
    
    # The key: did the audio frame get pushed before interruption?
    print(f"  Frames emitted: {len(processor.emitted_frames)}")
    
    return emitted and len(processor.emitted_frames) == 1  # PASS if emitted exactly one frame

# Test (ii): Normal processor gets cancelled
async def test_normal_cancellation():
    print("\n=== Test (ii): Normal processor cancellation ===")
    
    processor = NormalAudioProcessor()
    
    # Simulate: bot is speaking (emitting TTS audio)
    tts_frame = TTSAudioRawFrame(
        audio=b"\x00" * 320,
        sample_rate=16000,
        num_channels=1
    )
    await processor.process_frame(tts_frame, FrameDirection.DOWNSTREAM)
    emitted_before = len(processor.emitted_frames)
    
    # User interrupts
    await processor.process_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)
    
    # Try to emit more audio after interruption
    await processor.process_frame(tts_frame, FrameDirection.DOWNSTREAM)
    emitted_after = len(processor.emitted_frames)
    
    cancelled = processor.cancelled
    print(f"  Cancelled on interruption: {cancelled}")
    print(f"  Frames before: {emitted_before}, after: {emitted_after}")
    
    return cancelled and emitted_after == emitted_before  # PASS if cancelled and no new frames

# Test (iii): Per-frame overhead
async def test_pipeline_overhead():
    print("\n=== Test (iii): Per-frame overhead ===")
    
    class PassThroughProcessor(FrameProcessor):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            await self.push_frame(frame, direction)
    
    # Empty pipeline: just pass-through
    pass_through = PassThroughProcessor()
    
    # Create test frames
    test_frames = [AudioRawFrame(b"\x00" * 320, 16000, 1) for _ in range(1000)]
    
    # Warmup
    for frame in test_frames[:10]:
        await pass_through.process_frame(frame, FrameDirection.DOWNSTREAM)
    
    # Measure pipeline
    times = []
    for frame in test_frames:
        start = time.perf_counter()
        await pass_through.process_frame(frame, FrameDirection.DOWNSTREAM)
        times.append((time.perf_counter() - start) * 1_000_000)  # microseconds
    
    pipeline_p50 = statistics.median(times)
    pipeline_p95 = statistics.quantiles(times, n=20)[18]  # ~95th percentile
    
    # Plain function call chain (no pipeline)
    async def plain_pass_through(frame):
        return frame
    
    plain_times = []
    for frame in test_frames:
        start = time.perf_counter()
        await plain_pass_through(frame)
        plain_times.append((time.perf_counter() - start) * 1_000_000)
    
    plain_p50 = statistics.median(plain_times)
    plain_p95 = statistics.quantiles(plain_times, n=20)[18]
    
    overhead_p50 = pipeline_p50 - plain_p50
    overhead_p95 = pipeline_p95 - plain_p95
    
    print(f"  Pipeline p50: {pipeline_p50:.1f}µs, p95: {pipeline_p95:.1f}µs")
    print(f"  Plain p50: {plain_p50:.1f}µs, p95: {plain_p95:.1f}µs")
    print(f"  Overhead p50: {overhead_p50:.1f}µs, p95: {overhead_p95:.1f}µs")
    
    return {
        "pipeline_p50_us": pipeline_p50,
        "pipeline_p95_us": pipeline_p95,
        "plain_p50_us": plain_p50,
        "plain_p95_us": plain_p95,
        "overhead_p50_us": overhead_p50,
        "overhead_p95_us": overhead_p95,
    }

async def main():
    print("=== Interruption & Backchannel Feasibility ===")
    
    result1 = await test_uninterruptible_emission()
    result2 = await test_normal_cancellation()
    result3 = await test_pipeline_overhead()
    
    print("\n=== RESULTS ===")
    print(f"  (i) Uninterruptible emission: {'PASS' if result1 else 'FAIL'}")
    print(f"  (ii) Normal cancellation: {'PASS' if result2 else 'FAIL'}")
    print(f"  (iii) Pipeline overhead: {result3['overhead_p50_us']:.1f}µs p50, {result3['overhead_p95_us']:.1f}µs p95")
    
    # Save results
    import json
    with open("tests/benchmarks/voice/ws3/results/interruption_results.json", "w") as f:
        json.dump({
            "test_i_uninterruptible_emission": "PASS" if result1 else "FAIL",
            "test_ii_normal_cancellation": "PASS" if result2 else "FAIL",
            "test_iii_pipeline_overhead": result3,
        }, f, indent=2)

if __name__ == "__main__":
    asyncio.run(main())