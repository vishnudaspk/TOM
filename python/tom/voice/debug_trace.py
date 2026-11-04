"""Debug trace infrastructure for voice pipeline session recording and analysis.

When voice.debug_trace is enabled, this module provides:
1. Session directory creation (debug/<session_id>/)
2. Audio recording: mic.wav (16 kHz mono) and spk.wav (24 kHz mono)
3. Timeline JSONL logging (trace.jsonl) with shared monotonic clock
4. Endpointing trace: speech segments, silence gaps, VAD events, Smart Turn results
5. Barge-in trace: mic/playback RMS, thresholds, decisions
6. Text trace: LLM chunks, sanitized text, TTS chunks
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
import wave
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from tom.telemetry.logging import get_logger

logger = get_logger(__name__)


@dataclass
class DebugTraceSession:
    """Manages a single debug trace session with all recording and logging."""
    session_id: str
    session_dir: Path
    voice_config: Any  # VoiceConfig
    _start_time_ns: int = field(default_factory=time.monotonic_ns)
    _mic_wav: wave.Wave_write | None = None
    _spk_wav: wave.Wave_write | None = None
    _timeline_file: Any = None
    _mic_frames_written: int = 0
    _spk_frames_written: int = 0
    _monotonic_offset_s: float = 0.0

    def __post_init__(self):
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self._init_audio_files()
        self._init_timeline_file()
        self._log_session_start()

    def _init_audio_files(self):
        """Initialize WAV files for microphone and speaker recording."""
        mic_path = self.session_dir / "mic.wav"
        spk_path = self.session_dir / "spk.wav"

        self._mic_wav = wave.open(str(mic_path), "wb")
        self._mic_wav.setnchannels(1)
        self._mic_wav.setsampwidth(2)  # 16-bit
        self._mic_wav.setframerate(16000)

        self._spk_wav = wave.open(str(spk_path), "wb")
        self._spk_wav.setnchannels(1)
        self._spk_wav.setsampwidth(2)  # 16-bit
        self._spk_wav.setframerate(24000)

    def _init_timeline_file(self):
        """Initialize the timeline JSONL file."""
        timeline_path = self.session_dir / "trace.jsonl"
        self._timeline_file = open(timeline_path, "w", buffering=1)

    def _log_session_start(self):
        """Log session metadata."""
        self._write_timeline({
            "type": "session_start",
            "session_id": self.session_id,
            "timestamp_ns": self._start_time_ns,
            "wall_time": datetime.utcnow().isoformat() + "Z",
            "config": {
                "silence_duration_ms": self.voice_config.silence_duration_ms,
                "energy_threshold": self.voice_config.energy_threshold,
                "playback_tail_ms": self.voice_config.playback_tail_ms,
                "barge_in_min_rms_multiplier": self.voice_config.barge_in_min_rms_multiplier,
                "barge_in_min_duration_ms": self.voice_config.barge_in_min_duration_ms,
                "min_speech_ms": self.voice_config.min_speech_ms,
                "smart_turn_max_incomplete_wait_ms": self.voice_config.smart_turn_max_incomplete_wait_ms,
            },
        })

    def _monotonic_s(self) -> float:
        """Return monotonic time in seconds since session start."""
        return (time.monotonic_ns() - self._start_time_ns) / 1e9

    def _write_timeline(self, event: dict[str, Any]):
        """Write a timeline event with monotonic timestamp."""
        event["monotonic_s"] = round(self._monotonic_s(), 6)
        event["session_id"] = self.session_id
        self._timeline_file.write(json.dumps(event, separators=(",", ":")) + "\n")

    # ------------------------------------------------------------------
    # Audio recording
    # ------------------------------------------------------------------

    def write_mic_samples(self, samples: list[float]):
        """Write microphone PCM samples to mic.wav (16 kHz, 16-bit mono)."""
        if self._mic_wav is None:
            return
        import struct
        pcm_bytes = b"".join(struct.pack("<h", max(-32768, min(32767, int(s * 32767)))) for s in samples)
        self._mic_wav.writeframes(pcm_bytes)
        self._mic_frames_written += len(samples)

    def write_spk_samples(self, samples: list[float]):
        """Write speaker PCM samples to spk.wav (24 kHz, 16-bit mono)."""
        if self._spk_wav is None:
            return
        import struct
        pcm_bytes = b"".join(struct.pack("<h", max(-32768, min(32767, int(s * 32767)))) for s in samples)
        self._spk_wav.writeframes(pcm_bytes)
        self._spk_frames_written += len(samples)

    # ------------------------------------------------------------------
    # Timeline events
    # ------------------------------------------------------------------

    def log_endpointing(self, event_type: str, **kwargs):
        """Log endpointing-related event."""
        self._write_timeline({"type": "endpointing", "subtype": event_type, **kwargs})

    def log_barge_in(self, event_type: str, **kwargs):
        """Log barge-in related event."""
        self._write_timeline({"type": "barge_in", "subtype": event_type, **kwargs})

    def log_text(self, event_type: str, **kwargs):
        """Log text processing event."""
        self._write_timeline({"type": "text", "subtype": event_type, **kwargs})

    def log_state(self, old_state: str, new_state: str, **kwargs):
        """Log pipeline state transition."""
        self._write_timeline({"type": "state_transition", "old": old_state, "new": new_state, **kwargs})

    def log_audio_env(self, **kwargs):
        """Log audio environment inspection results."""
        env_info = self._inspect_audio_environment()
        env_info.update(kwargs)
        self._write_timeline({"type": "audio_env", **env_info})

    def _inspect_audio_environment(self) -> dict[str, Any]:
        """Inspect audio environment: devices, effects, echo measurement."""
        info: dict[str, Any] = {
            "inspection_time_ns": time.monotonic_ns(),
        }

        # 1. Input/Output device info via Windows API
        try:
            import ctypes
            from ctypes import wintypes

            # Try to get default audio devices via PowerShell
            ps_script = """
            Add-Type -AssemblyName System.Speech
            $recognizer = New-Object System.Speech.Recognition.SpeechRecognitionEngine
            $audioDevices = $recognizer.GetAudioInputs()
            foreach ($dev in $audioDevices) {
                Write-Output "INPUT:`t$($dev.DeviceName)`t`t$($dev.Format.SampleRate)Hz`t`t$($dev.Format.Channels)ch"
            }
            $outputDevices = [System.Media.SoundPlayer]::new().GetType().Assembly.GetTypes() | Where-Object {$_.Name -like '*Audio*'}
            """
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_script],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                info["device_enumeration_ps"] = result.stdout.strip()
        except Exception as e:
            info["device_enumeration_error"] = str(e)

        # 2. Check for Nahimic and audio effects processes
        nahimic_processes = []
        try:
            for proc in ["Nahimic", "NahimicService", "SonicStudio", "RealtekAudioConsole"]:
                result = subprocess.run(
                    ["tasklist", "/FI", f"IMAGENAME eq {proc}*"],
                    capture_output=True, text=True, timeout=5
                )
                if proc.lower() in result.stdout.lower():
                    nahimic_processes.append(proc)
        except Exception:
            pass
        info["nahimic_processes_found"] = nahimic_processes if nahimic_processes else "none_detected"

        # 3. Windows Audio Enhancements setting (via registry)
        try:
            import winreg
            key_path = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e96c-e325-11ce-bfc1-08002be10318}"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
                i = 0
                enhancements = []
                while True:
                    try:
                        subkey_name = winreg.EnumKey(key, i)
                        with winreg.OpenKey(key, subkey_name) as subkey:
                            try:
                                val, _ = winreg.QueryValueEx(subkey, "EnableAudioEnhancements")
                                if val:
                                    enhancements.append(subkey_name)
                            except FileNotFoundError:
                                pass
                        i += 1
                    except OSError:
                        break
            info["audio_enhancements_enabled_devices"] = enhancements
        except Exception as e:
            info["audio_enhancements_check_error"] = str(e)

        # 4. Echo measurement (2s speech sample at current volume)
        try:
            # This is a placeholder - real measurement would require
            # coordinating with the Rust engine to play a test tone
            info["echo_measurement"] = "not_implemented_requires_engine_coordination"
        except Exception as e:
            info["echo_measurement_error"] = str(e)

        # 4b. Capture owner vs playback owner
        info["capture_owner"] = "Python (MicrophoneLoop via EngineClient)"
        info["playback_owner"] = "Rust (tom-engine AudioManager)"
        info["shared_clock_possible"] = "yes (monotonic clock via time.monotonic_ns)"

        # 5. AEC library availability test
        aec_libs = {}
        for lib_name in ["webrtc_audio_processing", "speexdsp"]:
            try:
                # Test in a subprocess to avoid contaminating main env
                result = subprocess.run(
                    [sys.executable, "-c", f"import {lib_name}; print('OK')"],
                    capture_output=True, text=True, timeout=10
                )
                aec_libs[lib_name] = "available" if result.returncode == 0 else f"unavailable: {result.stderr.strip()}"
            except Exception as e:
                aec_libs[lib_name] = f"test_failed: {e}"
        info["aec_libraries"] = aec_libs

        # 6. Input/Output device names via Python sounddevice if available
        try:
            import sounddevice as sd
            devices = sd.query_devices()
            default_input = sd.default.device[0]
            default_output = sd.default.device[1]
            info["default_input_device"] = devices[default_input]["name"] if default_input >= 0 else "unknown"
            info["default_output_device"] = devices[default_output]["name"] if default_output >= 0 else "unknown"
            info["host_apis"] = list(set(d["hostapi"] for d in devices))
        except Exception as e:
            info["sounddevice_info"] = f"unavailable: {e}"

        return info

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def close(self):
        """Close all files and finalize."""
        if self._mic_wav:
            self._mic_wav.close()
            self._mic_wav = None
        if self._spk_wav:
            self._spk_wav.close()
            self._spk_wav = None
        if self._timeline_file:
            self._write_timeline({"type": "session_end", "monotonic_s": round(self._monotonic_s(), 6)})
            self._timeline_file.close()
            self._timeline_file = None
        logger.info(
            "debug_trace_session_closed",
            session_id=self.session_id,
            mic_frames=self._mic_frames_written,
            spk_frames=self._spk_frames_written,
            duration_s=round(self._monotonic_s(), 3),
        )


class DebugTraceManager:
    """Manages debug trace sessions. Created once per TomApp when debug_trace is enabled."""

    def __init__(self, voice_config: Any):
        self.voice_config = voice_config
        self._current_session: DebugTraceSession | None = None

    @property
    def is_active(self) -> bool:
        return self.voice_config.debug_trace

    def start_session(self) -> DebugTraceSession | None:
        """Start a new debug trace session."""
        if not self.is_active:
            return None
        session_id = f"{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        debug_root = Path("debug")
        session = DebugTraceSession(
            session_id=session_id,
            session_dir=debug_root / session_id,
            voice_config=self.voice_config,
        )
        self._current_session = session
        logger.info("debug_trace_session_started", session_id=session_id, dir=str(session.session_dir))
        return session

    def current_session(self) -> DebugTraceSession | None:
        return self._current_session

    def close(self):
        """Close the current session."""
        if self._current_session:
            self._current_session.close()
            self._current_session = None


def create_debug_trace_manager(voice_config: Any) -> DebugTraceManager | None:
    """Factory function to create DebugTraceManager if debug_trace is enabled."""
    if voice_config.debug_trace:
        return DebugTraceManager(voice_config)
    return None
