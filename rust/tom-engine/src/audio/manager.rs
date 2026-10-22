use serde::{Deserialize, Serialize};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use tokio::sync::mpsc;
use tokio_util::sync::CancellationToken;
use tracing::{debug, error, info};

use crate::audio::{
    get_audio_devices, AudioBuffer, AudioCaptureConfig, AudioCaptureController, AudioError,
    AudioHostInfo, AudioPlaybackConfig, AudioPlaybackController,
};

/// Default capacity for the capture speech ring buffer (30 seconds @ 16 kHz mono).
pub const DEFAULT_CAPTURE_BUFFER_CAPACITY: usize = 16_000 * 30;

/// Sanitize a slice of f32 audio samples by replacing non-finite values (NaN, infinity)
/// with 0.0. This prevents JSON serialization from producing `null` values that would
/// break downstream Pydantic validation.
fn sanitize_samples(samples: &mut [f32]) {
    for sample in samples.iter_mut() {
        if !sample.is_finite() {
            *sample = 0.0;
        }
    }
}

/// High-level status of the engine's audio subsystem.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct AudioStatus {
    pub capture_active: bool,
    pub playback_active: bool,
    pub capture_sample_rate: u32,
    pub capture_channels: u16,
    pub captured_samples: usize,
}

/// Orchestrates live/mock audio capture and playback for TOM Engine.
pub struct AudioManager {
    capture_controller: Mutex<Option<AudioCaptureController>>,
    capture_cancel: Mutex<Option<CancellationToken>>,
    capture_buffer: Arc<Mutex<AudioBuffer>>,
    capture_config: Mutex<AudioCaptureConfig>,
    total_captured_samples: Arc<AtomicUsize>,

    playback_controller: Mutex<Option<AudioPlaybackController>>,
    playback_sender: Mutex<Option<mpsc::Sender<Vec<f32>>>>,
    playback_cancel: Mutex<Option<CancellationToken>>,
    playback_config: Mutex<AudioPlaybackConfig>,

    cached_devices: Mutex<Option<(std::time::Instant, AudioHostInfo)>>,
}

impl Default for AudioManager {
    fn default() -> Self {
        Self::new()
    }
}

impl AudioManager {
    /// Creates a new `AudioManager` with empty capture and playback streams.
    pub fn new() -> Self {
        Self {
            capture_controller: Mutex::new(None),
            capture_cancel: Mutex::new(None),
            capture_buffer: Arc::new(Mutex::new(AudioBuffer::new(
                DEFAULT_CAPTURE_BUFFER_CAPACITY,
            ))),
            capture_config: Mutex::new(AudioCaptureConfig::default()),
            total_captured_samples: Arc::new(AtomicUsize::new(0)),

            playback_controller: Mutex::new(None),
            playback_sender: Mutex::new(None),
            playback_cancel: Mutex::new(None),
            playback_config: Mutex::new(AudioPlaybackConfig::default()),

            cached_devices: Mutex::new(None),
        }
    }

    /// Enumerates all system audio input and output devices with a 5-second TTL cache.
    pub async fn get_devices(&self) -> Result<AudioHostInfo, AudioError> {
        {
            let cache = self.cached_devices.lock().unwrap();
            if let Some((ts, ref info)) = *cache {
                if ts.elapsed() < std::time::Duration::from_secs(5) {
                    return Ok(info.clone());
                }
            }
        }

        let info = get_audio_devices().await?;
        let mut cache = self.cached_devices.lock().unwrap();
        *cache = Some((std::time::Instant::now(), info.clone()));
        Ok(info)
    }

    /// Returns a structured snapshot of the current audio subsystem status.
    pub fn status(&self) -> AudioStatus {
        let is_capturing = self
            .capture_cancel
            .lock()
            .unwrap()
            .as_ref()
            .map(|c| !c.is_cancelled())
            .unwrap_or(false);

        let is_playing = self
            .playback_cancel
            .lock()
            .unwrap()
            .as_ref()
            .map(|c| !c.is_cancelled())
            .unwrap_or(false);

        let cap_cfg = self.capture_config.lock().unwrap();
        let total_samples = self.total_captured_samples.load(Ordering::Relaxed);

        AudioStatus {
            capture_active: is_capturing,
            playback_active: is_playing,
            capture_sample_rate: cap_cfg.sample_rate,
            capture_channels: cap_cfg.channels,
            captured_samples: total_samples,
        }
    }

    /// Starts audio capture into the internal ring buffer.
    ///
    /// **No silent fallback to mock.** If the hardware device cannot be opened
    /// with a supported configuration, the error is returned to the caller.
    /// Mock capture is only used when explicitly requested via
    /// `AudioCaptureController::create_mock_capture()` (tests / CI).
    pub fn start_capture(&self, config: AudioCaptureConfig) -> Result<(), AudioError> {
        // Stop any currently running capture session first
        let _ = self.stop_capture();

        // Clear existing audio buffer and reset cumulative sample counter for fresh session
        self.capture_buffer.lock().unwrap().clear();
        self.total_captured_samples.store(0, Ordering::Relaxed);

        let cancel = CancellationToken::new();

        // Attempt live hardware capture. On failure, return the error — NO silent fallback.
        let (controller, mut rx) =
            AudioCaptureController::start_live(config.clone(), cancel.clone()).map_err(|e| {
                error!(
                    "Failed to start live audio capture: {e}. Device: {:?}, requested: {}Hz {}ch",
                    config.device_name, config.sample_rate, config.channels
                );
                e
            })?;

        info!("Live hardware audio capture started successfully");

        // Spawn background worker to drain incoming PCM frames into the ring buffer
        let buf_clone = Arc::clone(&self.capture_buffer);
        let total_samples = Arc::clone(&self.total_captured_samples);
        tokio::spawn(async move {
            while let Some(chunk) = rx.recv().await {
                total_samples.fetch_add(chunk.len(), Ordering::Relaxed);
                let mut buf = buf_clone.lock().unwrap();
                buf.push_samples(&chunk);
            }
            debug!("Audio capture worker loop terminated cleanly");
        });

        *self.capture_config.lock().unwrap() = config;
        *self.capture_cancel.lock().unwrap() = Some(cancel);
        *self.capture_controller.lock().unwrap() = Some(controller);

        Ok(())
    }

    /// Stops any active audio capture session and returns the total count of captured samples.
    pub fn stop_capture(&self) -> Result<usize, AudioError> {
        if let Some(cancel) = self.capture_cancel.lock().unwrap().take() {
            cancel.cancel();
        }
        *self.capture_controller.lock().unwrap() = None;

        let total_samples = self.total_captured_samples.load(Ordering::Relaxed);
        let buffered_remaining = self.capture_buffer.lock().unwrap().len();
        debug!(
            captured_samples = total_samples,
            buffered_remaining = buffered_remaining,
            "Audio capture stopped"
        );
        Ok(total_samples)
    }

    /// Retrieves captured speech samples from the buffer.
    /// If `clear` is true, removes the returned samples from the buffer.
    pub fn get_speech(&self, clear: bool) -> (Vec<f32>, u32, u16) {
        let sample_rate = self.capture_config.lock().unwrap().sample_rate;
        let channels = self.capture_config.lock().unwrap().channels;

        let mut buf = self.capture_buffer.lock().unwrap();
        let mut samples = if clear {
            let len = buf.len();
            buf.pop_samples(len)
        } else {
            let len = buf.len();
            buf.peek_samples(len)
        };

        // Sanitize samples to prevent NaN/infinity from becoming JSON null
        sanitize_samples(&mut samples);

        (samples, sample_rate, channels)
    }

    /// Enqueues PCM samples for asynchronous audio playback.
    /// Spawns or reuses playback controller; returns error if physical device cannot be initialized.
    /// Mock playback is only available via explicit test paths.
    pub async fn play_buffer(
        &self,
        samples: Vec<f32>,
        config: AudioPlaybackConfig,
    ) -> Result<usize, AudioError> {
        let count = samples.len();
        if count == 0 {
            return Ok(0);
        }

        let sender = {
            let mut sender_opt = self.playback_sender.lock().unwrap();
            let mut needs_init = sender_opt.is_none();

            if let Some(ref cancel) = *self.playback_cancel.lock().unwrap() {
                if cancel.is_cancelled() {
                    needs_init = true;
                }
            }

            if needs_init {
                let cancel = CancellationToken::new();
                let (controller, tx) = AudioPlaybackController::start_live(config.clone(), cancel.clone())
                    .map_err(|e| {
                        error!(
                            "Failed to start live audio playback: {e}. Device: {:?}, requested: {}Hz {}ch",
                            config.device_name, config.sample_rate, config.channels
                        );
                        e
                    })?;

                info!("Live hardware audio playback stream started");

                *self.playback_config.lock().unwrap() = config;
                *self.playback_cancel.lock().unwrap() = Some(cancel);
                *self.playback_controller.lock().unwrap() = Some(controller);
                *sender_opt = Some(tx);
            }

            sender_opt.clone()
        };

        if let Some(tx) = sender {
            tx.send(samples).await.map_err(|e| {
                AudioError::StreamError(format!(
                    "Failed to send audio samples to playback queue: {e}"
                ))
            })?;
        }

        Ok(count)
    }

    /// Stops audio playback immediately and cancels the playback task (barge-in).
    pub fn stop_playback(&self) -> Result<(), AudioError> {
        if let Some(cancel) = self.playback_cancel.lock().unwrap().take() {
            cancel.cancel();
        }
        *self.playback_sender.lock().unwrap() = None;
        *self.playback_controller.lock().unwrap() = None;
        debug!("Audio playback stopped immediately (barge-in)");
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_sanitize_samples_replaces_nan() {
        let mut samples = vec![1.0, f32::NAN, 3.0];
        sanitize_samples(&mut samples);
        assert_eq!(samples, vec![1.0, 0.0, 3.0]);
    }

    #[test]
    fn test_sanitize_samples_replaces_infinity() {
        let mut samples = vec![1.0, f32::INFINITY, -f32::INFINITY, 4.0];
        sanitize_samples(&mut samples);
        assert_eq!(samples, vec![1.0, 0.0, 0.0, 4.0]);
    }

    #[test]
    fn test_sanitize_samples_preserves_valid() {
        let mut samples = vec![-1.0, 0.0, 0.5, -0.5];
        let original = samples.clone();
        sanitize_samples(&mut samples);
        assert_eq!(samples, original);
    }

    #[test]
    fn test_sanitize_samples_empty() {
        let mut samples: Vec<f32> = vec![];
        sanitize_samples(&mut samples);
        assert!(samples.is_empty());
    }
}
