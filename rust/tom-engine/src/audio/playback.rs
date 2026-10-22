use std::collections::VecDeque;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use audioadapter::Adapter;
use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use cpal::{SampleFormat, SampleRate, StreamConfig, SupportedStreamConfigRange};
use rubato::{
    Async, FixedAsync, Resampler, SincInterpolationParameters, SincInterpolationType,
    WindowFunction,
};
use tokio::sync::mpsc;
use tokio_util::sync::CancellationToken;
use tracing::{debug, error, info, warn};

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
#[allow(dead_code)]
pub struct AudioPlaybackController {
    _stream: Option<cpal::Stream>,
    output_sample_rate: u32,
    output_channels: u16,
    output_format: SampleFormat,
}

unsafe impl Send for AudioPlaybackController {}
unsafe impl Sync for AudioPlaybackController {}

impl AudioPlaybackController {
    /// Starts live audio playback to the configured output device.
    ///
    /// Audio sample buffers sent to the returned `mpsc::Sender<Vec<f32>>` will be played.
    /// The input samples are expected at `config.sample_rate` and `config.channels`
    /// and will be resampled/converted to the device's supported configuration.
    pub fn start_live(
        config: AudioPlaybackConfig,
        cancel: CancellationToken,
    ) -> Result<(Self, mpsc::Sender<Vec<f32>>), AudioError> {
        let host = cpal::default_host();

        let device = select_output_device(&host, config.device_name.as_deref())?;

        let device_name = device.name().unwrap_or_else(|_| "unknown".to_string());

        // Query supported output configuration ranges
        let supported_config_ranges = device
            .supported_output_configs()
            .map_err(|e| AudioError::StreamError(format!("Failed to query supported output configs: {e}")))?
            .collect::<Vec<_>>();

        if supported_config_ranges.is_empty() {
            return Err(AudioError::UnsupportedFormat(
                "No supported output configurations found".into(),
            ));
        }

        // Choose the best supported configuration
        let (negotiated_config, output_format) = negotiate_output_config(
            &supported_config_ranges,
            config.sample_rate,
            config.channels,
        )?;

        let output_sample_rate = negotiated_config.sample_rate().0;
        let output_channels = negotiated_config.channels();

        info!(
            device = %device_name,
            requested_sample_rate = config.sample_rate,
            actual_sample_rate = output_sample_rate,
            requested_channels = config.channels,
            actual_channels = output_channels,
            output_format = ?output_format,
            "Initializing live audio playback with negotiated config"
        );

        // Build the stream config from the negotiated config
        let stream_config: StreamConfig = negotiated_config.into();

        // Create resampler if needed
        let needs_resampling = config.sample_rate != output_sample_rate || config.channels != output_channels;

        let sample_queue: Arc<Mutex<VecDeque<f32>>> = Arc::new(Mutex::new(VecDeque::new()));
        let queue_clone = Arc::clone(&sample_queue);

        let err_fn = move |err| {
            warn!("Audio playback stream error: {err}");
        };

        // Build the output stream based on the device's sample format
        let stream = match output_format {
            SampleFormat::F32 => build_stream_f32(&device, &stream_config, queue_clone, err_fn)?,
            SampleFormat::I16 => build_stream_i16(&device, &stream_config, queue_clone, err_fn)?,
            SampleFormat::U16 => build_stream_u16(&device, &stream_config, queue_clone, err_fn)?,
            SampleFormat::U8 => build_stream_u8(&device, &stream_config, queue_clone, err_fn)?,
            unsupported => {
                return Err(AudioError::UnsupportedFormat(format!(
                    "Device sample format {:?} is not supported for playback",
                    unsupported
                )));
            }
        };

        stream.play().map_err(|e| {
            AudioError::StreamError(format!("Failed to start playback stream: {e}"))
        })?;

        let (sender, mut receiver) = mpsc::channel::<Vec<f32>>(64);

        // Bridge task: move samples from async channel, resample/convert, push to output stream queue
        let bridge_cancel = cancel.clone();
        let input_sample_rate = config.sample_rate;
        let input_channels = config.channels as usize;
        let output_sample_rate = output_sample_rate;
        let output_channels = output_channels as usize;
        let output_format = output_format;

        tokio::spawn(async move {
            let mut resampler: Option<Async<f32>> = None;

            if needs_resampling {
                match build_resampler(input_sample_rate, output_sample_rate) {
                    Ok(r) => resampler = Some(r),
                    Err(e) => {
                        error!("Failed to create resampler: {e}");
                        return;
                    }
                }
            }

            let mut input_buffer = Vec::new();
            let mut output_buffer = Vec::new();

            loop {
                tokio::select! {
                    _ = bridge_cancel.cancelled() => {
                        debug!("Audio playback cancelled");
                        break;
                    }
                    chunk = receiver.recv() => {
                        match chunk {
                            Some(mut samples) => {
                                if samples.is_empty() {
                                    continue;
                                }

                                // Sanitize input samples
                                for s in &mut samples {
                                    if !s.is_finite() {
                                        *s = 0.0;
                                    }
                                }

                                let processed = if needs_resampling {
                                    if let Some(ref mut resampler) = resampler {
                                        // Convert input to mono if multi-channel, or keep as-is
                                        let mono_input = if input_channels > 1 {
                                            downmix_to_mono(&samples, input_channels)
                                        } else {
                                            samples
                                        };

                                        input_buffer.extend(mono_input);

                                        // Process through resampler
                                        let chunk_size = resampler.input_frames_next();
                                        while input_buffer.len() >= chunk_size {
                                            let chunk: Vec<f32> = input_buffer.drain(0..chunk_size).collect();
                                            let input_adapter = MonoBufferAdapter {
                                                data: &chunk,
                                                frames: chunk.len(),
                                            };
                                            match resampler.process(&input_adapter, None) {
                                                Ok(waves_out) => {
                                                    let resampled = waves_out.take_data();
                                                    // If output is stereo, duplicate mono to stereo
                                                    let converted = if output_channels > 1 {
                                                        mono_to_stereo(&resampled)
                                                    } else {
                                                        resampled
                                                    };
                                                    output_buffer.extend(converted);
                                                }
                                                Err(e) => {
                                                    warn!("Resampling error: {e}");
                                                }
                                            }
                                        }
                                    }
                                    // Drain output buffer
                                    if !output_buffer.is_empty() {
                                        let to_send: Vec<f32> = output_buffer.drain(..).collect();
                                        to_send
                                    } else {
                                        Vec::new()
                                    }
                                } else {
                                    // No resampling needed, just channel conversion if needed
                                    if input_channels == output_channels {
                                        samples
                                    } else if input_channels == 1 && output_channels == 2 {
                                        mono_to_stereo(&samples)
                                    } else {
                                        // Downmix to mono if input is multi and output is mono
                                        downmix_to_mono(&samples, input_channels)
                                    }
                                };

                                if !processed.is_empty() {
                                    if let Ok(mut q) = sample_queue.lock() {
                                        q.extend(processed);
                                    }
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
                output_sample_rate,
                output_channels: output_channels as u16,
                output_format,
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

        (Self {
            _stream: None,
            output_sample_rate: 0,
            output_channels: 0,
            output_format: SampleFormat::F32,
        }, sender, sample_count)
    }
}

fn negotiate_output_config(
    supported_config_ranges: &[SupportedStreamConfigRange],
    requested_sample_rate: u32,
    requested_channels: u16,
) -> Result<(cpal::SupportedStreamConfig, SampleFormat), AudioError> {
    // Try to find a config that matches requested sample rate and channels exactly
    for range in supported_config_ranges {
        if range.channels() >= requested_channels {
            if let Some(config) = range.try_with_sample_rate(SampleRate(requested_sample_rate)) {
                if config.channels() == requested_channels {
                    return Ok((config, range.sample_format()));
                }
            }
        }
    }

    // Otherwise, prefer higher sample rates (48000, 44100) with sufficient channels
    let mut best_config: Option<cpal::SupportedStreamConfig> = None;
    let mut best_format: Option<SampleFormat> = None;

    // Sort by preference: 48000, 44100, then higher rates
    let mut sorted_ranges: Vec<_> = supported_config_ranges.iter().collect();
    sorted_ranges.sort_by(|a, b| {
        let a_sr = a.max_sample_rate().0;
        let b_sr = b.max_sample_rate().0;
        let a_pref = match a_sr {
            48000 => 0,
            44100 => 1,
            _ => 2,
        };
        let b_pref = match b_sr {
            48000 => 0,
            44100 => 1,
            _ => 2,
        };
        a_pref.cmp(&b_pref).then_with(|| b_sr.cmp(&a_sr))
    });

    for range in sorted_ranges {
        if range.channels() < requested_channels {
            continue;
        }
        // Try to get a config at the max sample rate for this range
        if let Some(config) = range.try_with_sample_rate(range.max_sample_rate()) {
            if config.channels() >= requested_channels {
                best_config = Some(config);
                best_format = Some(range.sample_format());
                break;
            }
        }
    }

    best_config
        .zip(best_format)
        .ok_or_else(|| AudioError::UnsupportedFormat("No suitable output configuration found".into()))
}

fn build_resampler(
    input_rate: u32,
    output_rate: u32,
) -> Result<Async<f32>, AudioError> {
    // We always resample mono -> mono, then handle channel conversion separately
    let ratio = output_rate as f64 / input_rate as f64;

    let params = SincInterpolationParameters {
        sinc_len: 128,
        f_cutoff: Some(0.95),
        interpolation: SincInterpolationType::Cubic,
        oversampling_factor: 16,
        window: WindowFunction::BlackmanHarris2,
    };

    let chunk_size = (input_rate as f64 * 0.02).ceil() as usize; // ~20ms chunks
    let channels = 1; // Always mono for resampling

    let resampler = Async::<f32>::new_sinc(
        ratio,
        2.0,
        &params,
        chunk_size,
        channels,
        FixedAsync::Input,
    )
    .map_err(|e| AudioError::StreamError(format!("Failed to create resampler: {e}")))?;

    Ok(resampler)
}

fn downmix_to_mono(interleaved: &[f32], channels: usize) -> Vec<f32> {
    if channels <= 1 {
        return interleaved.to_vec();
    }
    let frame_count = interleaved.len() / channels;
    let mut mono = Vec::with_capacity(frame_count);
    for i in 0..frame_count {
        let mut sum = 0.0;
        for c in 0..channels {
            sum += interleaved[i * channels + c];
        }
        mono.push(sum / channels as f32);
    }
    mono
}

fn mono_to_stereo(mono: &[f32]) -> Vec<f32> {
    let mut stereo = Vec::with_capacity(mono.len() * 2);
    for &s in mono {
        stereo.push(s);
        stereo.push(s);
    }
    stereo
}

struct MonoBufferAdapter<'a> {
    data: &'a [f32],
    frames: usize,
}

unsafe impl<'a> Adapter<f32> for MonoBufferAdapter<'a> {
    unsafe fn read_sample_unchecked(&self, channel: usize, frame: usize) -> f32 {
        debug_assert!(channel == 0);
        debug_assert!(frame < self.frames);
        *self.data.get_unchecked(frame)
    }

    fn channels(&self) -> usize {
        1
    }

    fn frames(&self) -> usize {
        self.frames
    }
}

fn build_stream_f32(
    device: &cpal::Device,
    config: &StreamConfig,
    queue: Arc<Mutex<VecDeque<f32>>>,
    err_fn: impl Fn(cpal::StreamError) + Send + 'static,
) -> Result<cpal::Stream, AudioError> {
    device
        .build_output_stream(
            config,
            move |data: &mut [f32], _: &cpal::OutputCallbackInfo| {
                let mut q = match queue.lock() {
                    Ok(q) => q,
                    Err(_) => {
                        data.fill(0.0);
                        return;
                    }
                };
                for sample in data.iter_mut() {
                    *sample = q.pop_front().unwrap_or(0.0);
                }
            },
            err_fn,
            None,
        )
        .map_err(|e| AudioError::StreamError(e.to_string()))
}

fn build_stream_i16(
    device: &cpal::Device,
    config: &StreamConfig,
    queue: Arc<Mutex<VecDeque<f32>>>,
    err_fn: impl Fn(cpal::StreamError) + Send + 'static,
) -> Result<cpal::Stream, AudioError> {
    device
        .build_output_stream(
            config,
            move |data: &mut [i16], _: &cpal::OutputCallbackInfo| {
                let mut q = match queue.lock() {
                    Ok(q) => q,
                    Err(_) => {
                        data.fill(0);
                        return;
                    }
                };
                for sample in data.iter_mut() {
                    let f = q.pop_front().unwrap_or(0.0);
                    *sample = (f.clamp(-1.0, 1.0) * 32767.0) as i16;
                }
            },
            err_fn,
            None,
        )
        .map_err(|e| AudioError::StreamError(e.to_string()))
}

fn build_stream_u16(
    device: &cpal::Device,
    config: &StreamConfig,
    queue: Arc<Mutex<VecDeque<f32>>>,
    err_fn: impl Fn(cpal::StreamError) + Send + 'static,
) -> Result<cpal::Stream, AudioError> {
    device
        .build_output_stream(
            config,
            move |data: &mut [u16], _: &cpal::OutputCallbackInfo| {
                let mut q = match queue.lock() {
                    Ok(q) => q,
                    Err(_) => {
                        data.fill(32768);
                        return;
                    }
                };
                for sample in data.iter_mut() {
                    let f = q.pop_front().unwrap_or(0.0);
                    *sample = ((f.clamp(-1.0, 1.0) + 1.0) * 32767.5) as u16;
                }
            },
            err_fn,
            None,
        )
        .map_err(|e| AudioError::StreamError(e.to_string()))
}

fn build_stream_u8(
    device: &cpal::Device,
    config: &StreamConfig,
    queue: Arc<Mutex<VecDeque<f32>>>,
    err_fn: impl Fn(cpal::StreamError) + Send + 'static,
) -> Result<cpal::Stream, AudioError> {
    device
        .build_output_stream(
            config,
            move |data: &mut [u8], _: &cpal::OutputCallbackInfo| {
                let mut q = match queue.lock() {
                    Ok(q) => q,
                    Err(_) => {
                        data.fill(128);
                        return;
                    }
                };
                for sample in data.iter_mut() {
                    let f = q.pop_front().unwrap_or(0.0);
                    *sample = ((f.clamp(-1.0, 1.0) + 1.0) * 127.5) as u8;
                }
            },
            err_fn,
            None,
        )
        .map_err(|e| AudioError::StreamError(e.to_string()))
}

fn select_output_device(
    host: &cpal::Host,
    device_name: Option<&str>,
) -> Result<cpal::Device, AudioError> {
    let devices = host
        .output_devices()
        .map_err(|e| AudioError::HostError(e.to_string()))?;

    let devices: Vec<_> = devices.collect();

    if let Some(name) = device_name {
        if let Some(device) = devices.iter().find(|d| d.name().map(|n| n == name).unwrap_or(false)) {
            info!("Selected configured output device: {}", device.name().unwrap_or_else(|_| "unknown".to_string()));
            return Ok(device.clone());
        }
        return Err(AudioError::NoDeviceFound(format!("Output device '{}' not found", name)));
    }

    if let Some(device) = host.default_output_device() {
        info!("Auto-selected output device: {}", device.name().unwrap_or_else(|_| "unknown".to_string()));
        return Ok(device);
    }

    if let Some(device) = devices.first() {
        warn!("No default output device; using first available: {}", device.name().unwrap_or_else(|_| "unknown".to_string()));
        return Ok(device.clone());
    }

    Err(AudioError::NoDeviceFound("No output audio device available".into()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::time::Duration;

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

        tokio::time::sleep(Duration::from_millis(30)).await;
        assert_eq!(counter.load(Ordering::SeqCst), 6);

        cancel.cancel();
        tokio::time::sleep(Duration::from_millis(20)).await;
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

    #[test]
    fn test_downmix_to_mono_stereo() {
        let stereo = vec![1.0, 3.0, 2.0, 4.0];
        let mono = downmix_to_mono(&stereo, 2);
        assert_eq!(mono, vec![2.0, 3.0]);
    }

    #[test]
    fn test_downmix_to_mono_mono() {
        let mono = vec![0.5, -0.5, 0.25];
        let result = downmix_to_mono(&mono, 1);
        assert_eq!(result, mono);
    }

    #[test]
    fn test_downmix_to_mono_4ch() {
        let four_ch = vec![1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0];
        let mono = downmix_to_mono(&four_ch, 4);
        assert_eq!(mono, vec![2.5, 6.5]);
    }

    #[test]
    fn test_mono_to_stereo() {
        let mono = vec![0.5, -0.5, 0.25];
        let stereo = mono_to_stereo(&mono);
        assert_eq!(stereo, vec![0.5, 0.5, -0.5, -0.5, 0.25, 0.25]);
    }

    #[test]
    fn test_build_resampler() {
        for native in [8000, 16000, 44100, 48000, 96000] {
            let resampler = build_resampler(native, 48000);
            assert!(
                resampler.is_ok(),
                "Failed to build resampler for {native}Hz -> 48000Hz"
            );
        }
        // Test same rate
        let resampler = build_resampler(48000, 48000);
        assert!(resampler.is_ok());
    }
}