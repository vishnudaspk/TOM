#!/usr/bin/env python3
"""
Test LocalSmartTurnAnalyzerV3 on all WS2 recordings and compare with harness output.
"""

import asyncio
import numpy as np
import wave
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "python"))

from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.audio.turn.base_turn_analyzer import BaseTurnParams

RECORDINGS_DIR = Path(__file__).parent / "recordings"
MODEL_DIRS = {
    "sherpa-20M": Path(__file__).parent.parent.parent.parent / "models" / "faster-whisper-small.en",
    "sherpa-large": Path(__file__).parent.parent.parent.parent / "models" / "sherpa-onnx-streaming-zipformer-en-2023-06-26",
}

async def test_smart_turn_on_all():
    """Test Smart Turn V3 on all WS2 recordings."""
    
    # Initialize Smart Turn V3
    params = BaseTurnParams(
        stop_secs=3.0,
        pre_speech_ms=500,
        max_duration_secs=8
    )
    
    analyzer = LocalSmartTurnAnalyzerV3(cpu_count=4)
    
    # Test all recordings
    recordings = sorted(RECORDINGS_DIR.glob("*.wav"))
    results = []
    
    for wav_file in recordings:
        filepath = wav_file
        if not filepath.exists():
            print(f"Missing: {wav_file}")
            continue
            
        with wave.open(str(filepath), 'rb') as wf:
            frames = wf.readframes(wf.getnframes())
            audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
            sample_rate = wf.getframerate()
        
        duration = len(audio) / sample_rate
        
        analyzer = LocalSmartTurnAnalyzerV3(cpu_count=4)
        analyzer.append_audio(
            (audio * 32767).astype(np.int16).tobytes(),
            is_speech=True
        )
        
        state, metrics = await analyzer.analyze_end_of_turn()
        is_complete = str(state) == "EndOfTurnState.COMPLETE"
        
        result = {
            "file": str(wav_file),
            "duration_s": len(audio) / sample_rate,
            "state": str(state),
            "is_complete": is_complete,
            "probability": metrics.probability if metrics else None,
            "processing_time_ms": metrics.e2e_processing_time_ms if metrics else None,
        }
        results.append(result)
        
        status = "COMPLETE" if is_complete else "INCOMPLETE"
        prob = metrics.probability if metrics else None
        proc_time = metrics.e2e_processing_time_ms if metrics else None
        print(f"  {wav_file}: {state} (prob={prob:.4f}, time={proc_time:.1f}ms)" if prob is not None else f"  {wav_file}: {state} (time={proc_time:.1f}ms)")
        
        await analyzer.cleanup()
    
    # Summary
    complete_count = sum(1 for r in results if r["is_complete"])
    incomplete_count = len(results) - complete_count
    print(f"\n=== SUMMARY ===")
    print(f"Total: {len(results)}")
    print(f"COMPLETE: {complete_count}")
    print(f"INCOMPLETE: {incomplete_count}")
    
    # Save results
    import json
    output = Path("results") / "smart_turn_v3_official_results.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {output}")

if __name__ == "__main__":
    asyncio.run(test_smart_turn_on_all())