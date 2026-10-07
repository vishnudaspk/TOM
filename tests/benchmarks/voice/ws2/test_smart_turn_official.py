#!/usr/bin/env python3
"""
Test LocalSmartTurnAnalyzerV3 on known clips and compare with harness output.
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
MODEL_PATH = None  # Use bundled model

async def test_smart_turn_on_clips():
    """Test Smart Turn V3 on hesitation and complete clips."""
    
    # Initialize Smart Turn V3
    params = BaseTurnParams(
        stop_secs=3.0,
        pre_speech_ms=500,
        max_duration_secs=8
    )
    
    analyzer = LocalSmartTurnAnalyzerV3(cpu_count=4)
    
    # Test clips - use recordings from WS2
    test_files = [
        "001_open_the_settings_window.wav",
        "016_what_is_the_current_time.wav", 
        "008_open_the_downloads_folder.wav",
        "028_tom_open_the_project_settings.wav",
        "040_what_is_the_date_next_friday.wav",
    ]
    
    for wav_file in test_files:
        filepath = RECORDINGS_DIR / wav_file
        if not filepath.exists():
            print(f"Missing: {wav_file}")
            continue
            
        with wave.open(str(filepath), 'rb') as wf:
            frames = wf.readframes(wf.getnframes())
            audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
            sample_rate = wf.getframerate()
        
        print(f"\n=== {wav_file} (duration: {len(audio)/sample_rate:.2f}s) ===")
        
        # Feed audio in chunks
        analyzer = LocalSmartTurnAnalyzerV3(cpu_count=4)
        analyzer.append_audio(
            (audio * 32767).astype(np.int16).tobytes(),
            is_speech=True
        )
        
        # Analyze end of turn
        state, metrics = await analyzer.analyze_end_of_turn()
        print(f"  State: {state}")
        if str(state) == "EndOfTurnState.COMPLETE":
            print(f"  COMPLETE")
        else:
            print(f"  INCOMPLETE")
        
        if metrics:
            print(f"  Metrics: probability={metrics.probability:.4f}, processing_time={metrics.e2e_processing_time_ms:.1f}ms")
        
        await analyzer.cleanup()

if __name__ == "__main__":
    asyncio.run(test_smart_turn_on_clips())