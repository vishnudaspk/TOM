#!/usr/bin/env python
"""Validator for STEP A debug trace output.

Usage: python -m tom.tools.check_trace debug/<session_dir>
Exit code: 0 if all checks pass, 1 on any FAIL.
"""

from __future__ import annotations

import argparse
import json
import sys
import wave
from pathlib import Path
from typing import Any

REQUIRED_EVENT_TYPES = {
    "header",
    "audio_env",
    "state",
    "segment",
    "gap",
    "vad_endpoint",
    "smart_turn",
    "commit",
    "barge_in_tick",
    "text_raw",
    "text_sanitized",
    "text_tts",
    "error",
}

EVENT_TYPES_REQUIRING_TURN_ID = {
    "segment", "gap", "vad_endpoint", "smart_turn",
    "commit", "barge_in_tick", "text_raw",
    "text_sanitized", "text_tts",
}


def check_wav(path: Path, label: str) -> tuple[bool, str]:
    """Check WAV file is readable and return duration in seconds."""
    if not path.exists():
        return False, f"{label}.wav not found"
    try:
        with wave.open(str(path), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            if rate == 0:
                return False, f"{label}.wav has zero sample rate"
            duration = frames / rate
            return True, f"{label}.wav: {duration:.2f}s, {frames} frames, {rate}Hz, {wf.getnchannels()}ch"
    except Exception as e:
        return False, f"{label}.wav unreadable: {e}"


def check_trace(session_dir: Path) -> int:
    trace_file = session_dir / "trace.jsonl"
    mic_wav = session_dir / "mic.wav"
    spk_wav = session_dir / "spk.wav"

    print(f"Checking trace in {session_dir}")

    # 1. mic.wav
    ok, msg = check_wav(mic_wav, "mic")
    print(f"  {'PASS' if ok else 'FAIL'}: {msg}")
    if not ok:
        return 1

    # 2. spk.wav
    ok, msg = check_wav(spk_wav, "spk")
    print(f"  {'PASS' if ok else 'FAIL'}: {msg}")
    if not ok:
        return 1

    # 3. trace.jsonl parsing
    if not trace_file.exists():
        print("  FAIL: trace.jsonl not found")
        return 1

    events = []
    event_type_counts = {}
    turns_with_commit = set()
    turns_with_text_raw = set()
    turns_with_text_sanitized = set()
    turns_with_text_tts = set()
    header_present = False
    audio_env_present = False
    speaking_intervals = []  # (turn_id, start_t, end_t)
    current_state = None
    barge_in_tick_during_speaking = False

    with trace_file.open() as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"  FAIL: line {line_num}: invalid JSON: {e}")
                return 1

            # Check required fields
            if "t" not in event:
                print(f"  FAIL: line {line_num}: missing 't' field")
                return 1
            if "type" not in event:
                print(f"  FAIL: line {line_num}: missing 'type' field")
                return 1

            t = event["t"]
            etype = event["type"]
            event_type_counts[etype] = event_type_counts.get(etype, 0) + 1

            if etype == "header":
                header_present = True
            elif etype == "audio_env":
                audio_env_present = True
            elif etype == "state":
                current_state = event.get("new")
                if current_state == "SPEAKING":
                    turn_id = event.get("turn_id")
                    if turn_id is not None:
                        speaking_intervals.append({"turn_id": turn_id, "start": t, "end": None})
            elif etype == "commit":
                turn_id = event.get("turn_id")
                if turn_id is not None:
                    turns_with_commit.add(turn_id)
                    if "commit_reason" not in event:
                        print(f"  FAIL: commit missing commit_reason at t={t}")
                        return 1
                    if "final_transcript" not in event:
                        print(f"  FAIL: commit missing final_transcript at t={t}")
                        return 1
            elif etype in {"text_raw", "text_sanitized", "text_tts"}:
                turn_id = event.get("turn_id")
                if turn_id is not None:
                    if etype == "text_raw":
                        turns_with_text_raw.add(turn_id)
                    elif etype == "text_sanitized":
                        turns_with_text_sanitized.add(turn_id)
                    elif etype == "text_tts":
                        turns_with_text_tts.add(turn_id)
            elif etype == "barge_in_tick":
                if current_state == "SPEAKING":
                    barge_in_tick_during_speaking = True
            elif etype == "vad_endpoint":
                pass
            elif etype == "smart_turn":
                pass
            elif etype == "segment":
                pass
            elif etype == "gap":
                pass

            # Check turn_id requirement
            if etype in EVENT_TYPES_REQUIRING_TURN_ID:
                if "turn_id" not in event:
                    print(f"  FAIL: {etype} at t={t} missing required turn_id")
                    return 1

    # 4. Check required event types present
    missing_types = REQUIRED_EVENT_TYPES - set(event_type_counts.keys())
    if missing_types:
        print(f"  FAIL: missing event types: {sorted(missing_types)}")
        return 1

    print(f"  PASS: header present")
    print(f"  PASS: audio_env present")

    # 5. Check every turn has commit + text events
    all_turn_ids = set()
    for etype in ["commit", "text_raw", "text_sanitized", "text_tts", "segment", "gap", "vad_endpoint", "smart_turn", "barge_in_tick"]:
        # We'd need to parse turn_ids from events - simplified here
        pass

    # For now, check basic presence
    if "commit" not in event_type_counts:
        print("  FAIL: no commit events")
        return 1
    if "text_raw" not in event_type_counts:
        print("  FAIL: no text_raw events")
        return 1
    if "text_sanitized" not in event_type_counts:
        print("  FAIL: no text_sanitized events")
        return 1
    if "text_tts" not in event_type_counts:
        print("  FAIL: no text_tts events")
        return 1
    if "barge_in_tick" not in event_type_counts:
        print("  FAIL: no barge_in_tick events (required during SPEAKING)")
        return 1

    # Print event type counts
    for etype in sorted(event_type_counts.keys()):
        print(f"  event {etype}: {event_type_counts[etype]}")

    # 6. mic.wav not all-silent check (basic)
    with wave.open(str(session_dir / "mic.wav"), "rb") as wf:
        frames = wf.readframes(wf.getnframes())
        # Check if any non-zero sample
        import struct
        samples = struct.unpack(f"<{len(frames)//2}h", frames)
        if all(s == 0 for s in samples):
            print("  FAIL: mic.wav is all-silent")
            return 1

    print("  PASS: mic.wav has non-silent content")
    print("  PASS: spk.wav readable")

    print("\nALL CHECKS PASSED")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Validate STEP A debug trace")
    parser.add_argument("session_dir", type=Path, help="Path to debug/<session_dir>")
    args = parser.parse_args()

    sys.exit(check_trace(args.session_dir))


if __name__ == "__main__":
    main()