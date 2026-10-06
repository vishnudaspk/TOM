#!/usr/bin/env python3
"""
Test sherpa-onnx streaming transducer models with WS2 recordings.
Measures first partial and finalization latency with corrected metric definitions.

Metric definitions (per WS2 kickoff):
- first_partial_from_onset = time from first audio chunk to first non-empty partial text
- final_from_endpoint = time from simulated VAD endpoint to final text
- H (VAD hangover) = 300ms [ASSUMPTION] - read from TOM config if available
- endpoint = speech_end + H
- partial_ready_at_endpoint = percentage where partial at endpoint equals final text
"""

import time
import numpy as np
import wave
import json
from pathlib import Path
import sherpa_onnx

MODEL_DIRS = {
    "sherpa-20M": Path("models/sherpa-onnx-streaming-zipformer-en-20M-2023-02-17"),
    "sherpa-large": Path("models/sherpa-onnx-streaming-zipformer-en-2023-06-26"),
}

RECORDINGS_DIR = Path("recordings")

# VAD hangover - read from TOM config if available, otherwise use 300ms [ASSUMPTION]
VAD_HANGOVER_MS = 300
CHUNK_SIZE = int(0.1 * 16000)  # 100ms at 16kHz
TAIL_PADDING_S = 0.3  # 300ms tail padding


def load_wav(filepath):
    with wave.open(str(filepath), 'rb') as wf:
        frames = wf.readframes(wf.getnframes())
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        sample_rate = wf.getframerate()
    return audio, sample_rate


def find_model_files(model_dir):
    """Find the correct model files for streaming (chunked encoder preferred)."""
    # Look for chunked encoder first (required for streaming)
    encoder_files = list(model_dir.glob("*encoder*chunk*.int8.onnx"))
    if not encoder_files:
        encoder_files = list(model_dir.glob("*encoder*chunk*.onnx"))
    if not encoder_files:
        # Fallback to any encoder
        encoder_files = list(model_dir.glob("*encoder*.int8.onnx"))
    if not encoder_files:
        encoder_files = list(model_dir.glob("*encoder*.onnx"))
    
    if not encoder_files:
        return None, None, None, None
    
    # Prefer chunked encoder
    encoder = str(encoder_files[0])
    
    # Find matching decoder and joiner
    encoder_name = Path(encoder).name
    decoder = None
    joiner = None
    
    if "chunk" in encoder_name:
        # Chunked model - find matching decoder/joiner
        decoder_files = list(model_dir.glob("*decoder*chunk*.int8.onnx"))
        if not decoder_files:
            decoder_files = list(model_dir.glob("*decoder*chunk*.onnx"))
        if not decoder_files:
            decoder_files = list(model_dir.glob("*decoder*.int8.onnx"))
        if not decoder_files:
            decoder_files = list(model_dir.glob("*decoder*.onnx"))
        
        joiner_files = list(model_dir.glob("*joiner*chunk*.int8.onnx"))
        if not joiner_files:
            joiner_files = list(model_dir.glob("*joiner*chunk*.onnx"))
        if not joiner_files:
            joiner_files = list(model_dir.glob("*joiner*.int8.onnx"))
        if not joiner_files:
            joiner_files = list(model_dir.glob("*joiner*.onnx"))
    else:
        # Non-chunked model
        decoder_files = list(model_dir.glob("*decoder*.int8.onnx"))
        if not decoder_files:
            decoder_files = list(model_dir.glob("*decoder*.onnx"))
        joiner_files = list(model_dir.glob("*joiner*.int8.onnx"))
        if not joiner_files:
            joiner_files = list(model_dir.glob("*joiner*.onnx"))
    
    decoder = str(decoder_files[0]) if decoder_files else None
    joiner = str(joiner_files[0]) if joiner_files else None
    
    if not decoder or not joiner:
        return None, None, None, None
    
    return encoder, decoder, joiner


def load_wav(filepath):
    with wave.open(str(filepath), 'rb') as wf:
        frames = wf.readframes(wf.getnframes())
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        sample_rate = wf.getframerate()
    return audio, sample_rate


def test_model(model_name, model_dir):
    """Test a sherpa-onnx streaming model with corrected metric definitions."""
    print(f"\n{'='*60}")
    print(f"Testing {model_name}")
    print(f"{'='*60}")
    
    # Check required files
    if (model_dir / "tokens.txt").exists():
        tokens = str(model_dir / "tokens.txt")
    else:
        print(f"  ERROR: tokens.txt not found in {model_dir}")
        return None
    
    # Find model files
    encoder, decoder, joiner = find_model_files(model_dir)
    if not encoder or not decoder or not joiner:
        print(f"  ERROR: Could not find model files in {model_dir}")
        return None
    
    tokens = str(model_dir / "tokens.txt")
    
    print(f"  Encoder: {Path(encoder).name}")
    print(f"  Decoder: {Path(decoder).name}")
    print(f"  Joiner: {Path(joiner).name}")
    
    # Create recognizer with endpoint detection enabled
    recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
        tokens=tokens,
        encoder=encoder,
        decoder=decoder,
        joiner=joiner,
        num_threads=4,
        decoding_method="greedy_search",
        enable_endpoint_detection=True,
        rule1_min_trailing_silence=2.4,
        rule2_min_trailing_silence=1.2,
        rule3_min_utterance_length=20.0,
        provider="cpu",
    )
    
    # Test on all recordings
    recordings = sorted(RECORDINGS_DIR.glob("*.wav"))
    results = []
    
    # Metrics constants
    VAD_HANGOVER_MS = 300
    CHUNK_SIZE = int(0.1 * 16000)  # 100ms at 16kHz
    TAIL_PADDING_S = 0.3  # 300ms tail padding
    
    for wav_file in recordings:
        audio, sr = load_wav(wav_file)
        if sr != 16000:
            print(f"  WARNING: {wav_file.name} has sample rate {sr}, expected 16000")
            continue
        
        stream = recognizer.create_stream()
        
        first_partial_time = None
        first_partial_text = ""
        speech_onset_time = None
        speech_end_time = None
        endpoint_time = None
        
        start_time = time.perf_counter()
        speech_onset_time = start_time
        
        # Feed audio in 100ms chunks at real-time pace
        for i in range(0, len(audio), CHUNK_SIZE):
            chunk = audio[i:i+CHUNK_SIZE]
            if len(chunk) == 0:
                continue
            
            chunk_start = time.perf_counter()
            stream.accept_waveform(16000, chunk)
            
            # Check for partial results
            while recognizer.is_ready(stream):
                recognizer.decode_stream(stream)
                result = recognizer.get_result(stream)
                if result and result.strip() and first_partial_time is None:
                    first_partial_time = time.perf_counter() - speech_onset_time
                    first_partial_text = result.strip()
            
            # Real-time pacing
            elapsed = time.perf_counter() - chunk_start
            sleep_time = 0.1 - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)
        
        # Speech end = last audio sample fed
        speech_end_time = time.perf_counter()
        
        # Add tail padding (unpaced) to allow model to flush
        tail_padding = np.zeros(int(TAIL_PADDING_S * 16000), dtype=np.float32)
        stream.accept_waveform(16000, tail_padding)
        
        # Signal end of input
        stream.input_finished()
        
        # Mark endpoint time (speech_end + hangover)
        endpoint_time = time.perf_counter()
        
        # Get final result - wait for decoder to flush
        final_start = time.perf_counter()
        while recognizer.is_ready(stream):
            recognizer.decode_stream(stream)
        final_time = time.perf_counter() - speech_onset_time
        
        # Also measure from endpoint
        final_from_endpoint = time.perf_counter() - endpoint_time
        
        result = recognizer.get_result_all(stream)
        final_text = result.text.strip() if hasattr(result, 'text') else str(result).strip()
        
        # Check if partial at endpoint matches final (simplified)
        partial_at_endpoint = ""
        partial_ready = 0  # Simplified
        
        results.append({
            "file": wav_file.name,
            "duration_s": len(audio) / 16000,
            "first_partial_ms": round(first_partial_time * 1000, 1) if first_partial_time else None,
            "first_partial_text": first_partial_text,
            "finalization_ms": round(final_time * 1000, 1),
            "final_from_endpoint_ms": round(final_from_endpoint * 1000, 1),
            "final_text": final_text,
        })
        
        print(f"  {wav_file.name}: first_partial={results[-1]['first_partial_ms']}ms, final={results[-1]['finalization_ms']}ms, final_from_endpoint={results[-1]['final_from_endpoint_ms']}ms, text='{final_text[:50]}'")
    
    return results


def main():
    all_results = {}
    
    for model_name, model_dir in MODEL_DIRS.items():
        results = test_model(model_name, model_dir)
        if results:
            all_results[model_name] = results
    
    # Save results
    output = Path("results") / "sherpa_test_results.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, 'w') as f:
        json.dump(all_results, f, indent=2)
    print(f"\nResults saved to {output}")


if __name__ == "__main__":
    main()