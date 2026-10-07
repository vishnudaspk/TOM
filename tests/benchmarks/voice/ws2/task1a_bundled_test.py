#!/usr/bin/env python3
"""
Task 1a: Run sherpa-large on bundled test_wavs to confirm model works correctly.
Correct protocol: after each accept_waveform, run while is_ready: decode_stream;
then feed tail padding, call input_finished, final decode loop, read result.
Produces: tests/benchmarks/voice/ws2/results/task1a_bundled_results.json
"""

import json
import time
import wave
import numpy as np
from pathlib import Path

import sherpa_onnx

MODEL_DIR = Path("tests/benchmarks/voice/ws2/models/sherpa-onnx-streaming-zipformer-en-2023-06-26")
TEST_WAVS_DIR = MODEL_DIR / "test_wavs"
RESULTS_FILE = Path("tests/benchmarks/voice/ws2/results/task1a_bundled_results.json")

# Bundled references (from trans.txt)
BUNDLED_REFS = {
    "0.wav": "AFTER EARLY NIGHTFALL THE YELLOW LAMPS WOULD LIGHT UP HERE AND THERE THE SQUALID QUARTER OF THE BROTHELS",
    "1.wav": "GOD AS A DIRECT CONSEQUENCE OF THE SIN WHICH MAN THUS PUNISHED HAD GIVEN HER A LOVELY CHILD WHOSE PLACE WAS ON THAT SAME DISHONOURED BOSOM TO CONNECT HER PARENT FOR EVER WITH THE RACE AND DESCENT OF MORTALS AND TO BE FINALLY A BLESSED SOUL IN HEAVEN",
    "8k.wav": "YET THESE THOUGHTS AFFECTED HESTER PRYNNE LESS WITH HOPE THAN APPREHENSION",
}

CHUNK_SAMPLES = 1600  # 100 ms at 16000 Hz
TAIL_PADDING_MS = 500
TAIL_SAMPLES = int(16000 * TAIL_PADDING_MS / 1000)


def load_wav_float32(filepath: Path):
    with wave.open(str(filepath), "rb") as wf:
        sr = wf.getframerate()
        frames = wf.readframes(wf.getnframes())
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    return audio, sr


def make_recognizer(enable_endpoint=False):
    encoder = str(list(MODEL_DIR.glob("*encoder*chunk*.int8.onnx"))[0])
    decoder = str(list(MODEL_DIR.glob("*decoder*chunk*.int8.onnx"))[0])
    joiner = str(list(MODEL_DIR.glob("*joiner*chunk*.int8.onnx"))[0])
    tokens = str(MODEL_DIR / "tokens.txt")
    return sherpa_onnx.OnlineRecognizer.from_transducer(
        tokens=tokens, encoder=encoder, decoder=decoder, joiner=joiner,
        num_threads=4, decoding_method="greedy_search",
        enable_endpoint_detection=enable_endpoint,
        rule1_min_trailing_silence=2.4, rule2_min_trailing_silence=1.2,
        rule3_min_utterance_length=20.0,
        provider="cpu",
    )


def decode_audio_correct_protocol(recognizer, audio, sample_rate=16000):
    """
    Correct streaming decode protocol per sherpa-onnx docs:
    1. Feed audio in chunks
    2. After EACH accept_waveform, run while is_ready: decode_stream
    3. Feed tail padding (silence) unpaced
    4. Call input_finished()
    5. Final decode loop
    6. Read result
    """
    stream = recognizer.create_stream()
    # Feed audio in 100 ms chunks
    for start in range(0, len(audio), CHUNK_SAMPLES):
        chunk = audio[start:start + CHUNK_SAMPLES]
        stream.accept_waveform(sample_rate, chunk)
        while recognizer.is_ready(stream):
            recognizer.decode_stream(stream)
    # Feed tail padding (silence) to flush encoder
    tail = np.zeros(TAIL_SAMPLES, dtype=np.float32)
    stream.accept_waveform(sample_rate, tail)
    while recognizer.is_ready(stream):
        recognizer.decode_stream(stream)
    # Signal end of input
    stream.input_finished()
    # Final decode loop
    while recognizer.is_ready(stream):
        recognizer.decode_stream(stream)
    # Read result
    return recognizer.get_result(stream)


def main():
    print("=== Task 1a: Bundled test_wavs verification ===")
    print(f"Model: {MODEL_DIR.name}")
    recognizer = make_recognizer(enable_endpoint=False)

    results = []
    for wav_name, ref in BUNDLED_REFS.items():
        wav_path = TEST_WAVS_DIR / wav_name
        if not wav_path.exists():
            print(f"  SKIP (not found): {wav_name}")
            continue
        audio, sr = load_wav_float32(wav_path)
        print(f"\n  File: {wav_name} | SR={sr} | Duration={len(audio)/sr:.2f}s")
        t0 = time.perf_counter()
        hyp = decode_audio_correct_protocol(recognizer, audio, sample_rate=min(sr, 16000))
        elapsed = time.perf_counter() - t0
        print(f"  REF: {ref}")
        print(f"  HYP: {hyp}")
        match = hyp.strip().upper() == ref.strip().upper()
        print(f"  MATCH: {match} | Decode time: {elapsed:.3f}s")
        results.append({
            "file": wav_name,
            "reference": ref,
            "hypothesis": hyp,
            "exact_match": match,
            "decode_time_s": round(elapsed, 4),
            "duration_s": round(len(audio)/sr, 3),
        })

    out = {
        "protocol": "correct (chunk feed + decode loop + tail padding + input_finished + final decode)",
        "model": MODEL_DIR.name,
        "sherpa_onnx_version": sherpa_onnx.__version__,
        "results": results,
    }
    RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(RESULTS_FILE, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nResults written to {RESULTS_FILE}")


if __name__ == "__main__":
    main()
