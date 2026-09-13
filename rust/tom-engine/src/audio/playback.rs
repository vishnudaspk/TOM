use std::collections::VecDeque;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use tokio::sync::mpsc;
use tokio_util::sync::CancellationToken;
use tracing::{debug, info, warn};

use crate::audio::AudioError;

/// Configuration for audio playback.
#[derive(Debug, Clone)]
pub struct AudioPlaybackConfig {
    pub device_name: Option<String>,
    pub sample_rate: u32,
    pub channels: u16,
}

impl Default for AudioPlaybackConfig {
    fn default() -> Self {
        Self {
            device_name: None,
            sample_rate: 24000,
            channels: 1,
        }
    }
}

/// Controller managing an active audio playback stream.
pub struct AudioPlaybackController {
    _stream: Option<cpal::Stream>,
}

unsafe impl Send for AudioPlaybackController {}
unsafe impl Sync for AudioPlaybackController {}

impl AudioPlaybackController {
    /// Starts live audio playback to the configured output device.
    ///
    /// Audio sample buffers sent to the returned `mpsc::Sender<Vec<f32>>` will be played.
    pub fn start_live(
        config: AudioPlaybackConfig,
        cancel: CancellationToken,
    ) -> Result<(Self, mpsc::Sender<Vec<f32>>), AudioError> {
        let host = cpal::default_host();

        let device = match config.device_name.as_deref() {
            Some(name) => host
                .output_devices()
                .map_err(|e| AudioError::HostError(e.to_string()))?
                .find(|d| d.name().map(|n| n == name).unwrap_or(false))
                .ok_or_else(|| {
                    AudioError::NoDeviceFound(format!("Output device '{name}' not found"))
                })?,
            None => host.default_output_device().ok_or_else(|| {
                AudioError::NoDeviceFound("No default output audio device available".into())
            })?,
        };

        let device_name = device.name().unwrap_or_else(|_| "unknown".to_string());
        info!(device = %device_name, sample_rate = config.sample_rate, channels = config.channels, "Initializing live audio playback");

        let stream_config = cpal::StreamConfig {
            channels: config.channels,
            sample_rate: cpal::SampleRate(config.sample_rate),
            buffer_size: cpal::BufferSize::Default,
        };

        let sample_queue: Arc<Mutex<VecDeque<f32>>> = Arc::new(Mutex::new(VecDeque::new()));
        let queue_clone = Arc::clone(&sample_queue);

        let err_fn = move |err| {
            warn!("Audio playback stream error: {err}");
        };

        let stream = device
            .build_output_stream(
                &stream_config,
                move |data: &mut [f32], _: &cpal::OutputCallbackInfo| {
                    let mut queue = match queue_clone.lock() {
                        Ok(q) => q,
                        Err(_) => {
                            for sample in data.iter_mut() {
                                *sample = 0.0;
                            }
                            return;
                        }
                    };

                    for sample in data.iter_mut() {
                        *sample = queue.pop_front().unwrap_or(0.0);
                    }
                },
                err_fn,
                None,
            )
            .map_err(|e| AudioError::StreamError(e.to_string()))?;

        stream.play().map_err(|e| {
            AudioError::StreamError(format!("Failed to start playback stream: {e}"))
        })?;

        let (sender, mut receiver) = mpsc::channel::<Vec<f32>>(64);

        // Bridge task: move samples from async channel into output stream queue
        let bridge_cancel = cancel.clone();
        tokio::spawn(async move {
            loop {
                tokio::select! {
                    _ = bridge_cancel.cancelled() => {
                        debug!("Audio playback cancelled");
                        break;
                    }
                    chunk = receiver.recv() => {
                        match chunk {
                            Some(samples) => {
                                if let Ok(mut q) = sample_queue.lock() {
                                    q.extend(samples);
                                }
                            }
                            None => break, // Channel closed
                        }
                    }
                }
            }
        });

        Ok((
            Self {
                _stream: Some(stream),
            },
            sender,
        ))
    }

    /// Creates a mock playback sink for testing without physical audio output hardware.
    ///
    /// Tracks total sample count received in the returned atomic counter.
    pub fn create_mock_playback(
        cancel: CancellationToken,
    ) -> (Self, mpsc::Sender<Vec<f32>>, Arc<AtomicUsize>) {
        let (sender, mut receiver) = mpsc::channel::<Vec<f32>>(32);
        let sample_count = Arc::new(AtomicUsize::new(0));
        let count_clone = Arc::clone(&sample_count);

        tokio::spawn(async move {
            loop {
                tokio::select! {
                    _ = cancel.cancelled() => {
                        debug!("Mock audio playback cancelled");
                        break;
                    }
                    chunk = receiver.recv() => {
                        match chunk {
                            Some(samples) => {
                                count_clone.fetch_add(samples.len(), Ordering::SeqCst);
                            }
                            None => break,
                        }
                    }
                }
            }
        });

        (Self { _stream: None }, sender, sample_count)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_mock_playback_and_sample_tracking() {
        let cancel = CancellationToken::new();
        let (_controller, sender, counter) =
            AudioPlaybackController::create_mock_playback(cancel.clone());

        assert_eq!(counter.load(Ordering::SeqCst), 0);

        sender
            .send(vec![0.1, 0.2, 0.3, 0.4])
            .await
            .expect("send failed");
        sender.send(vec![0.5, 0.6]).await.expect("send failed");

        // Allow bridge task to process
        tokio::time::sleep(std::time::Duration::from_millis(30)).await;
        assert_eq!(counter.load(Ordering::SeqCst), 6);

        cancel.cancel();
        tokio::time::sleep(std::time::Duration::from_millis(20)).await;
    }

    #[test]
    fn test_nonexistent_device_playback_fails_gracefully() {
        let cancel = CancellationToken::new();
        let config = AudioPlaybackConfig {
            device_name: Some("__nonexistent_output_dev_123__".to_string()),
            ..Default::default()
        };

        let result = AudioPlaybackController::start_live(config, cancel);
        assert!(result.is_err());
        match result.err().unwrap() {
            AudioError::NoDeviceFound(msg) => {
                assert!(msg.contains("__nonexistent_output_dev_123__"));
            }
            other => panic!("Expected NoDeviceFound error, got {other:?}"),
        }
    }
}
