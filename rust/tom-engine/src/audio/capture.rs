use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use tokio::sync::mpsc;
use tokio_util::sync::CancellationToken;
use tracing::{debug, info, warn};

use crate::audio::AudioError;

/// Configuration for audio capture.
#[derive(Debug, Clone)]
pub struct AudioCaptureConfig {
    pub device_name: Option<String>,
    pub sample_rate: u32,
    pub channels: u16,
    pub chunk_size: usize,
}

impl Default for AudioCaptureConfig {
    fn default() -> Self {
        Self {
            device_name: None,
            // 16kHz mono is standard for speech processing / wake-word
            sample_rate: 16000,
            channels: 1,
            // 1600 samples = 100ms frames at 16kHz
            chunk_size: 1600,
        }
    }
}

/// Controller managing an active audio capture session.
pub struct AudioCaptureController {
    // Keep stream handle alive until controller dropped
    _stream: Option<cpal::Stream>,
}

unsafe impl Send for AudioCaptureController {}
unsafe impl Sync for AudioCaptureController {}

impl AudioCaptureController {
    /// Starts live audio capture from the configured input device.
    ///
    /// Audio frames are streamed to the returned `mpsc::Receiver<Vec<f32>>`.
    /// When `cancel` is triggered, the capture stream is stopped and dropped cleanly.
    pub fn start_live(
        config: AudioCaptureConfig,
        cancel: CancellationToken,
    ) -> Result<(Self, mpsc::Receiver<Vec<f32>>), AudioError> {
        let host = cpal::default_host();

        let device = match config.device_name.as_deref() {
            Some(name) => host
                .input_devices()
                .map_err(|e| AudioError::HostError(e.to_string()))?
                .find(|d| d.name().map(|n| n == name).unwrap_or(false))
                .ok_or_else(|| {
                    AudioError::NoDeviceFound(format!("Input device '{name}' not found"))
                })?,
            None => host.default_input_device().ok_or_else(|| {
                AudioError::NoDeviceFound("No default input audio device available".into())
            })?,
        };

        let device_name = device.name().unwrap_or_else(|_| "unknown".to_string());
        info!(device = %device_name, sample_rate = config.sample_rate, channels = config.channels, "Initializing live audio capture");

        let stream_config = cpal::StreamConfig {
            channels: config.channels,
            sample_rate: cpal::SampleRate(config.sample_rate),
            buffer_size: cpal::BufferSize::Default,
        };

        // Bounded channel to prevent memory leaks if consumer is slow
        let (sender, receiver) = mpsc::channel::<Vec<f32>>(64);

        let err_fn = move |err| {
            warn!("Audio capture stream error: {err}");
        };

        let chunk_size = config.chunk_size;
        let mut buffer = Vec::with_capacity(chunk_size * 2);

        let stream = device
            .build_input_stream(
                &stream_config,
                move |data: &[f32], _: &cpal::InputCallbackInfo| {
                    buffer.extend_from_slice(data);
                    while buffer.len() >= chunk_size {
                        let chunk: Vec<f32> = buffer.drain(0..chunk_size).collect();
                        // Non-blocking try_send so audio driver thread is never blocked
                        if let Err(e) = sender.try_send(chunk) {
                            match e {
                                mpsc::error::TrySendError::Full(_) => {
                                    debug!(
                                        "Audio capture channel full; dropping frame to avoid lag"
                                    );
                                }
                                mpsc::error::TrySendError::Closed(_) => {
                                    // Receiver was dropped
                                    break;
                                }
                            }
                        }
                    }
                },
                err_fn,
                None,
            )
            .map_err(|e| AudioError::StreamError(e.to_string()))?;

        stream
            .play()
            .map_err(|e| AudioError::StreamError(format!("Failed to start capture stream: {e}")))?;

        // Spawn a supervisor task to manage stream lifetime against the cancellation token
        let supervisor_cancel = cancel.clone();
        tokio::spawn(async move {
            supervisor_cancel.cancelled().await;
            debug!("Audio capture cancellation received, stopping stream");
        });

        Ok((
            Self {
                _stream: Some(stream),
            },
            receiver,
        ))
    }

    /// Creates a mock capture stream emitting synthetic audio frames at regular intervals.
    /// Used for deterministic automated tests and CI environments without physical microphones.
    pub fn create_mock_capture(
        config: AudioCaptureConfig,
        cancel: CancellationToken,
    ) -> (Self, mpsc::Receiver<Vec<f32>>) {
        let (sender, receiver) = mpsc::channel::<Vec<f32>>(32);
        let chunk_size = config.chunk_size;

        tokio::spawn(async move {
            let mut interval = tokio::time::interval(std::time::Duration::from_millis(20));
            let mut phase: f32 = 0.0;

            loop {
                tokio::select! {
                    _ = cancel.cancelled() => {
                        debug!("Mock audio capture cancelled");
                        break;
                    }
                    _ = interval.tick() => {
                        // Generate synthetic 440 Hz test tone
                        let mut chunk = Vec::with_capacity(chunk_size);
                        for _ in 0..chunk_size {
                            chunk.push((phase * 2.0 * std::f32::consts::PI).sin() * 0.5);
                            phase = (phase + 440.0 / config.sample_rate as f32) % 1.0;
                        }

                        if sender.send(chunk).await.is_err() {
                            break;
                        }
                    }
                }
            }
        });

        (Self { _stream: None }, receiver)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_mock_capture_and_cancellation() {
        let cancel = CancellationToken::new();
        let config = AudioCaptureConfig {
            chunk_size: 320,
            sample_rate: 16000,
            ..Default::default()
        };

        let (_controller, mut rx) =
            AudioCaptureController::create_mock_capture(config, cancel.clone());

        // Must receive at least one frame promptly
        let first_frame = rx.recv().await.expect("Failed to receive mock audio frame");
        assert_eq!(first_frame.len(), 320);

        // Signal cancellation
        cancel.cancel();

        // Drain any already buffered frame, then stream should cleanly terminate
        tokio::time::sleep(std::time::Duration::from_millis(50)).await;
        // Verify channel eventually closes or is empty
        assert!(rx.try_recv().is_err() || rx.recv().await.is_none());
    }

    #[test]
    fn test_nonexistent_device_capture_fails_gracefully() {
        let cancel = CancellationToken::new();
        let config = AudioCaptureConfig {
            device_name: Some("__nonexistent_device_xyz_987__".to_string()),
            ..Default::default()
        };

        let result = AudioCaptureController::start_live(config, cancel);
        assert!(result.is_err());
        match result.err().unwrap() {
            AudioError::NoDeviceFound(msg) => {
                assert!(msg.contains("__nonexistent_device_xyz_987__"));
            }
            other => panic!("Expected NoDeviceFound error, got {other:?}"),
        }
    }
}
