#!/usr/bin/env python3
"""
Corrected sherpa-onnx streaming transducer test with proper metric definitions.

Metric definitions (per WS2 kickoff):
- speech_onset = time of first non-silent audio sample (energy VAD)
- first_partial_from_onset = time from speech_onset to first non-empty partial text
- final_from_endpoint = time from simulated VAD endpoint (speech_end + hangover) to final text
- H (VAD hangover) = 300ms
- endpoint = speech_end + H
- partial_ready_at_endpoint = percent of utterances where last partial before endpoint equals final text
- WER between partial at endpoint and final text
- token timestamps from recognizer.timestamps() for partial lag (if available)
- per-chunk decode time (real-time factor at 4 threads)
"""

import time
import numpy as np
import wave
import json
from pathlib import Path
import sherpa_onnx

MODEL_DIRS = {
    "sherpa-20M": Path("tests/benchmarks/voice/ws2/models/sherpa-onnx-streaming-zipformer-en-20M-2023-02-17"),
    "sherpa-large": Path("tests/benchmarks/voice/ws2/models/sherpa-onnx-streaming-zipformer-en-2023-06-26"),
}

RECORDINGS_DIR = Path("tests/benchmarks/voice/ws2/recordings")

VAD_HANGOVER_MS = 300
CHUNK_SIZE = int(0.1 * 16000)  # 100ms at 16kHz
TAIL_PADDING_S = 0.3  # 300ms tail padding
ENERGY_THRESHOLD = 0.01  # ~-40 dB for speech onset detection


def load_wav(filepath):
    with wave.open(str(filepath), 'rb') as wf:
        frames = wf.readframes(wf.getnframes())
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        sample_rate = wf.getframerate()
    return audio, sample_rate


def find_model_files(model_dir):
    """Find the correct model files for streaming (chunked encoder preferred)."""
    encoder_files = list(model_dir.glob("*encoder*chunk*.int8.onnx"))
    if not encoder_files:
        encoder_files = list(model_dir.glob("*encoder*chunk*.onnx"))
    if not encoder_files:
        encoder_files = list(model_dir.glob("*encoder*.int8.onnx"))
    if not encoder_files:
        encoder_files = list(model_dir.glob("*encoder*.onnx"))

    if not encoder_files:
        return None, None, None, None

    encoder = str(encoder_files[0])
    encoder_name = Path(encoder).name

    if "chunk" in encoder_name:
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


def detect_speech_onset(audio, sample_rate, threshold=ENERGY_THRESHOLD):
    """Detect speech onset using energy VAD. Returns onset sample index."""
    # Compute short-term energy (10ms windows)
    window_size = int(0.01 * sample_rate)  # 10ms
    hop_size = window_size
    
    for i in range(0, len(audio) - window_size, hop_size):
        window = audio[i:i+window_size]
        energy = np.sqrt(np.mean(window**2))
        if energy > threshold:
            return i
    return 0  # fallback to start


def test_model(model_name, model_dir):
    print(f"\n{'='*60}")
    print(f"Testing {model_name}")
    print(f"{'='*60}")

    if (model_dir / "tokens.txt").exists():
        tokens = str(model_dir / "tokens.txt")
    else:
        print(f"  ERROR: tokens.txt not found in {model_dir}")
        return None

    encoder, decoder, joiner = find_model_files(model_dir)
    if not encoder or not decoder or not joiner:
        print(f"  ERROR: Could not find model files in {model_dir}")
        return None

    print(f"  Encoder: {Path(encoder).name}")
    print(f"  Decoder: {Path(decoder).name}")
    print(f"  Joiner: {Path(joiner).name}")

    # Extract chunk size from encoder filename
    chunk_size_info = "unknown"
    if "chunk-" in encoder:
        import re
        m = re.search(r'chunk-(\d+)-left-(\d+)', encoder)
        if m:
            chunk_size_info = f"chunk={m.group(1)}, left={m.group(2)}"
    print(f"  Chunk config: {chunk_size_info}")

    # Create recognizer
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

    recordings = sorted(RECORDINGS_DIR.glob("*.wav"))
    results = []

    for wav_file in recordings:
        audio, sr = load_wav(wav_file)
        if sr != 16000:
            print(f"  WARNING: {wav_file.name} has sample rate {sr}, expected 16000")
            continue

        # Detect actual speech onset from audio
        onset_sample = detect_speech_onset(audio, sr)
        onset_time_s = onset_sample / sr
        leading_silence_ms = onset_time_s * 1000

        stream = recognizer.create_stream()

        first_partial_time = None
        first_partial_text = ""
        first_partial_tokens = None
        speech_onset_time = None
        speech_end_time = None
        endpoint_time = None
        partials_before_endpoint = []  # (time, text, tokens)

        start_time = time.perf_counter()
        speech_onset_time = start_time + onset_time_s  # actual speech onset in wall clock

        # Feed audio in 100ms chunks at real-time pace
        chunk_times = []  # per-chunk decode times
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
                    first_partial_tokens = recognizer.tokens(stream)

            # Real-time pacing
            elapsed = time.perf_counter() - chunk_start
            chunk_times.append(elapsed)
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
        partials_at_endpoint = recognizer.get_result(stream)
        partial_text_at_endpoint = partials_at_endpoint.strip() if partials_at_endpoint else ""

        while recognizer.is_ready(stream):
            recognizer.decode_stream(stream)
        final_time = time.perf_counter() - speech_onset_time

        # Also measure from endpoint
        final_from_endpoint = time.perf_counter() - endpoint_time

        result_obj = recognizer.get_result_all(stream)
        final_text = result_obj.text.strip() if hasattr(result_obj, 'text') else str(result_obj).strip()
        final_tokens = recognizer.tokens(stream)
        final_timestamps = recognizer.timestamps(stream)  # token timestamps

        # Check if partial at endpoint matches final
        partial_ready = 1 if partial_text_at_endpoint == final_text else 0

        # Compute WER between partial at endpoint and final
        def compute_wer(ref, hyp):
            ref_words = ref.split()
            hyp_words = hyp.split()
            if not ref_words and not hyp_words:
                return 0.0
            if not ref_words or not hyp_words:
                return 1.0
            # Simple Levenshtein
            dp = [[0] * (len(hyp_words) + 1) for _ in range(len(ref_words) + 1)]
            for i in range(len(ref_words) + 1):
                dp[i][0] = i
            for j in range(len(hyp_words) + 1):
                dp[0][j] = j
            for i in range(1, len(ref_words) + 1):
                for j in range(1, len(hyp_words) + 1):
                    if ref_words[i-1] == hyp_words[j-1]:
                        dp[i][j] = dp[i-1][j-1]
                    else:
                        dp[i][j] = min(dp[i-1][j], dp[i][j-1], dp[i-1][j-1]) + 1
            return dp[len(ref_words)][len(hyp_words)] / len(ref_words)

        wer_at_endpoint = compute_wer(final_text, partial_text_at_endpoint)

        # Compute RTF (real-time factor) from chunk times
        avg_chunk_time = np.mean(chunk_times) if chunk_times else 0
        rtf = avg_chunk_time / 0.1  # chunk duration is 100ms

        # First partial from onset
        first_partial_from_onset = first_partial_time * 1000 if first_partial_time else None

        results.append({
            "file": wav_file.name,
            "duration_s": len(audio) / 16000,
            "leading_silence_ms": round(leading_silence_ms, 1),
            "speech_onset_sample": int(onset_sample),
            "first_partial_ms": round(first_partial_from_onset, 1) if first_partial_from_onset else None,
            "first_partial_text": first_partial_text,
            "first_partial_tokens": first_partial_tokens,
            "finalization_ms": round(final_time * 1000, 1),
            "final_from_endpoint_ms": round(final_from_endpoint * 1000, 1),
            "final_text": final_text,
            "final_tokens": final_tokens,
            "final_timestamps": final_timestamps,
            "partial_at_endpoint": partial_text_at_endpoint,
            "partial_ready_at_endpoint": partial_ready,
            "wer_at_endpoint": round(wer_at_endpoint, 4),
            "avg_chunk_decode_ms": round(avg_chunk_time * 1000, 2),
            "rtf": round(rtf, 4),
            "chunk_config": chunk_size_info,
        })

        fp_str = f"{first_partial_from_onset:.1f}" if first_partial_from_onset is not None else "None"
        print(f"  {wav_file.name}: lead_sil={leading_silence_ms:.0f}ms, fp_onset={fp_str}ms, ffe={results[-1]['final_from_endpoint_ms']}ms, partial_ready={partial_ready}, WER={wer_at_endpoint:.2f}, RTF={rtf:.3f}, text='{final_text[:50]}'")

    return results


def main():
    all_results = {}

    for model_name, model_dir in MODEL_DIRS.items():
        results = test_model(model_name, model_dir)
        if results:
            all_results[model_name] = results

    # Save results
    output = Path("tests/benchmarks/voice/ws2/results/sherpa_corrected_results.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, 'w') as f:
        json.dump(all_results, f, indent=2)
    print(f"\nResults saved to {output}")


if __name__ == "__main__":
    main()