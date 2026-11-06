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
    pub capture_overruns: usize,
    pub playback_underruns: usize,
}

/// Consolidated playback state — single lock covers sender, cancel, controller, and config.
#[derive(Default)]
struct PlaybackState {
    sender: Option<mpsc::Sender<Vec<f32>>>,
    cancel: Option<CancellationToken>,
    controller: Option<AudioPlaybackController>,
    config: AudioPlaybackConfig,
}

impl PlaybackState {
    fn is_active(&self) -> bool {
        self.cancel
            .as_ref()
            .map(|c| !c.is_cancelled())
            .unwrap_or(false)
    }

    fn underruns(&self) -> usize {
        self.controller
            .as_ref()
            .map(|c| c.underrun_count.load(Ordering::Relaxed))
            .unwrap_or(0)
    }

    fn stop(&mut self) {
        if let Some(cancel) = self.cancel.take() {
            cancel.cancel();
        }
        self.sender = None;
        self.controller = None;
    }
}

/// Orchestrates live/mock audio capture and playback for TOM Engine.
pub struct AudioManager {
    capture_controller: Mutex<Option<AudioCaptureController>>,
    capture_cancel: Mutex<Option<CancellationToken>>,
    capture_buffer: Arc<Mutex<AudioBuffer>>,
    capture_config: Mutex<AudioCaptureConfig>,
    total_captured_samples: Arc<AtomicUsize>,
    total_overrun_samples: Arc<AtomicUsize>,

    playback: Mutex<PlaybackState>,

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
            total_overrun_samples: Arc::new(AtomicUsize::new(0)),

            playback: Mutex::new(PlaybackState::default()),

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

        let cap_cfg = self.capture_config.lock().unwrap();
        let total_samples = self.total_captured_samples.load(Ordering::Relaxed);
        let overrun_samples = self.total_overrun_samples.load(Ordering::Relaxed);
        let pb = self.playback.lock().unwrap();
        let is_playing = pb.is_active();
        let underruns = pb.underruns();

        AudioStatus {
            capture_active: is_capturing,
            playback_active: is_playing,
            capture_sample_rate: cap_cfg.sample_rate,
            capture_channels: cap_cfg.channels,
            captured_samples: total_samples,
            capture_overruns: overrun_samples,
            playback_underruns: underruns,
        }
    }

    /// Starts audio capture into the internal ring buffer.
    ///
    /// **No silent fallback to mock.** If the hardware device cannot be opened
    /// with a supported configuration, the error is returned to the caller.
    /// Mock capture is only used when explicitly requested via
    /// `AudioCaptureController::create_mock_capture()` (tests / CI).
    ///
    /// CPAL stream initialization is blocking (WASAPI device negotiation); it is
    /// offloaded to `spawn_blocking` so the Tokio runtime is not stalled.
    pub async fn start_capture(&self, config: AudioCaptureConfig) -> Result<(), AudioError> {
        // Stop any currently running capture session first
        let _ = self.stop_capture();

        // Clear existing audio buffer and reset cumulative sample counter for fresh session
        self.capture_buffer.lock().unwrap().clear();
        self.total_captured_samples.store(0, Ordering::Relaxed);
        self.total_overrun_samples.store(0, Ordering::Relaxed);

        let cancel = CancellationToken::new();

        // Offload blocking CPAL/WASAPI device initialization to the blocking thread pool.
        let config_clone = config.clone();
        let cancel_clone = cancel.clone();
        let (controller, mut rx) = tokio::task::spawn_blocking(move || {
            AudioCaptureController::start_live(config_clone, cancel_clone)
        })
        .await
        .map_err(|e| AudioError::StreamError(format!("spawn_blocking join error: {e}")))?
        .map_err(|e| {
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
        let overrun_samples = Arc::clone(&self.total_overrun_samples);
        tokio::spawn(async move {
            while let Some(chunk) = rx.recv().await {
                total_samples.fetch_add(chunk.len(), Ordering::Relaxed);
                let mut buf = buf_clone.lock().unwrap();
                let (_written, dropped) = buf.push_samples_with_overrun(&chunk);
                if dropped > 0 {
                    overrun_samples.fetch_add(dropped, Ordering::Relaxed);
                }
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
    ///
    /// CPAL stream initialization is blocking (WASAPI device negotiation); it is
    /// offloaded to `spawn_blocking` so the Tokio runtime is not stalled.
    pub async fn play_buffer(
        &self,
        samples: Vec<f32>,
        config: AudioPlaybackConfig,
    ) -> Result<usize, AudioError> {
        let count = samples.len();
        if count == 0 {
            return Ok(0);
        }

        // Check if playback is already active while holding the lock only briefly.
        let already_active = self.playback.lock().unwrap().is_active();

        if !already_active {
            // Offload blocking CPAL/WASAPI device initialization to the blocking thread pool.
            let config_clone = config.clone();
            let cancel = CancellationToken::new();
            let cancel_clone = cancel.clone();
            let (controller, tx) = tokio::task::spawn_blocking(move || {
                AudioPlaybackController::start_live(config_clone, cancel_clone)
            })
            .await
            .map_err(|e| AudioError::StreamError(format!("spawn_blocking join error: {e}")))?
            .map_err(|e| {
                error!(
                    "Failed to start live audio playback: {e}. Device: {:?}, requested: {}Hz {}ch",
                    config.device_name, config.sample_rate, config.channels
                );
                e
            })?;

            info!("Live hardware audio playback stream started");

            let mut pb = self.playback.lock().unwrap();
            // Double-check: another concurrent call may have started playback while we waited.
            if !pb.is_active() {
                pb.config = config;
                pb.cancel = Some(cancel);
                pb.controller = Some(controller);
                pb.sender = Some(tx);
            }
            // If already started by concurrent caller, the controller and cancel we built
            // are dropped cleanly here — stream stops on drop.
        }

        let sender = self.playback.lock().unwrap().sender.clone();

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
        self.playback.lock().unwrap().stop();
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
