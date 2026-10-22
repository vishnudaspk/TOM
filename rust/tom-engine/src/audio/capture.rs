use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use cpal::{SampleFormat, StreamConfig};
use rubato::{
    Async, FixedAsync, Resampler, SincInterpolationParameters, SincInterpolationType,
    WindowFunction,
};
use tokio::sync::mpsc;
use tokio_util::sync::CancellationToken;
use tracing::{debug, info, warn};

use crate::audio::AudioError;

/// Target output format for the voice pipeline.
const TARGET_SAMPLE_RATE: u32 = 16000;
const TARGET_CHANNELS: u16 = 1;
/// 20ms frames at 16kHz = 320 samples.
const TARGET_FRAME_SAMPLES: usize = 320;

/// Configuration for audio capture.
#[derive(Debug, Clone)]
pub struct AudioCaptureConfig {
    pub device_name: Option<String>,
    /// Output sample rate (must be 16000 for voice pipeline).
    pub sample_rate: u32,
    /// Output channels (must be 1 for voice pipeline).
    pub channels: u16,
    /// Ignored — frames are always emitted at TARGET_FRAME_SAMPLES (20ms at 16kHz).
    pub chunk_size: usize,
}

impl Default for AudioCaptureConfig {
    fn default() -> Self {
        Self {
            device_name: None,
            sample_rate: TARGET_SAMPLE_RATE,
            channels: TARGET_CHANNELS,
            chunk_size: TARGET_FRAME_SAMPLES,
        }
    }
}

/// Controller managing an active audio capture session.
pub struct AudioCaptureController {
    _stream: Option<cpal::Stream>,
}

unsafe impl Send for AudioCaptureController {}
unsafe impl Sync for AudioCaptureController {}

impl AudioCaptureController {
    /// Starts live audio capture from the configured input device.
    ///
    /// The device is opened at its **native** sample rate / channel count / sample format.
    /// Frames are converted to mono f32 @ 16kHz and emitted in 20ms chunks (320 samples).
    ///
    /// Audio frames are streamed to the returned `mpsc::Receiver<Vec<f32>>`.
    /// When `cancel` is triggered, the capture stream is stopped and dropped cleanly.
    pub fn start_live(
        config: AudioCaptureConfig,
        cancel: CancellationToken,
    ) -> Result<(Self, mpsc::Receiver<Vec<f32>>), AudioError> {
        if config.sample_rate != TARGET_SAMPLE_RATE || config.channels != TARGET_CHANNELS {
            return Err(AudioError::UnsupportedFormat(format!(
                "Voice pipeline requires {}Hz mono output, got {}Hz {}ch",
                TARGET_SAMPLE_RATE, config.sample_rate, config.channels
            )));
        }

        let host = cpal::default_host();

        fn select_input_device(
    host: &cpal::Host,
    device_name: Option<&str>,
) -> Result<cpal::Device, AudioError> {
    let devices = host
        .input_devices()
        .map_err(|e| AudioError::HostError(e.to_string()))?;

    // Collect all devices first to allow multiple passes
    let devices: Vec<_> = devices.collect();

    // If a specific device name is configured, try to find it exactly
    if let Some(name) = device_name {
        if let Some(device) = devices.iter().find(|d| {
            d.name().map(|n| n == name).unwrap_or(false)
        }) {
            info!("Selected configured input device: {}", device.name().unwrap_or_else(|_| "unknown".to_string()));
            return Ok(device.clone());
        }
        // Configured device not found - return error instead of falling back
        return Err(AudioError::NoDeviceFound(format!("Input device '{}' not found", name)));
    }

    // Auto-select: prefer non-loopback physical microphones
    // First, try to find a non-loopback device (not "stereo mix", "loopback", etc.)
    let loopback_keywords = ["stereo mix", "loopback", "what u hear", "wave out mix"];
    let non_loopback = devices.iter().find(|d| {
        d.name().map(|n| {
            let lower = n.to_lowercase();
            !loopback_keywords.iter().any(|kw| lower.contains(kw))
        }).unwrap_or(false)
    });

    if let Some(device) = non_loopback {
        info!("Auto-selected input device: {}", device.name().unwrap_or_else(|_| "unknown".to_string()));
        return Ok(device.clone());
    }

    // Fallback: use the first available device
    if let Some(device) = devices.first() {
        warn!("No non-loopback input device found; using first available: {}", device.name().unwrap_or_else(|_| "unknown".to_string()));
        return Ok(device.clone());
    }

    Err(AudioError::NoDeviceFound("No input audio device available".into()))
}

        let device = select_input_device(&host, config.device_name.as_deref())?;

        let device_name = device.name().unwrap_or_else(|_| "unknown".to_string());

        // Get the device's default input configuration (native format).
        let native_config = device.default_input_config().map_err(|e| {
            AudioError::StreamError(format!("Failed to get default input config: {e}"))
        })?;

        let native_sample_rate = native_config.sample_rate().0;
        let native_channels = native_config.channels();
        let native_format = native_config.sample_format();

        info!(
            device = %device_name,
            native_sample_rate = native_sample_rate,
            native_channels = native_channels,
            native_format = ?native_format,
            target_sample_rate = TARGET_SAMPLE_RATE,
            target_channels = TARGET_CHANNELS,
            "Initializing live audio capture with native device config"
        );

        // Build stream config using the device's native format.
        let stream_config: StreamConfig = native_config.into();

        // Bounded channel to prevent memory leaks if consumer is slow.
        // We send 20ms frames (320 samples @ 16kHz).
        let (sender, receiver) = mpsc::channel::<Vec<f32>>(64);

        let err_fn = move |err| {
            warn!("Audio capture stream error: {err}");
        };

        // Channel for passing raw frames from the cpal callback to the conversion task.
        let (raw_tx, raw_rx) = mpsc::channel::<Vec<f32>>(128);

        // CPAL input callback runs on the audio thread — must be fast and non-blocking.
        let stream = match native_format {
            SampleFormat::F32 => device.build_input_stream(
                &stream_config,
                move |data: &[f32], _: &cpal::InputCallbackInfo| {
                    if !data.is_empty() {
                        let _ = raw_tx.try_send(data.to_vec());
                    }
                },
                err_fn,
                None,
            ),
            SampleFormat::I16 => device.build_input_stream(
                &stream_config,
                move |data: &[i16], _: &cpal::InputCallbackInfo| {
                    if !data.is_empty() {
                        let frames: Vec<f32> = data
                            .iter()
                            .map(|&s| {
                                if s >= 0 {
                                    s as f32 / 32767.0
                                } else {
                                    s as f32 / 32768.0
                                }
                            })
                            .collect();
                        let _ = raw_tx.try_send(frames);
                    }
                },
                err_fn,
                None,
            ),
            SampleFormat::U16 => device.build_input_stream(
                &stream_config,
                move |data: &[u16], _: &cpal::InputCallbackInfo| {
                    if !data.is_empty() {
                        const U16_MID: f32 = 32768.0;
                        let frames: Vec<f32> =
                            data.iter().map(|&s| (s as f32 - U16_MID) / U16_MID).collect();
                        let _ = raw_tx.try_send(frames);
                    }
                },
                err_fn,
                None,
            ),
            unsupported => {
                return Err(AudioError::UnsupportedFormat(format!(
                    "Device sample format {:?} is not supported for capture",
                    unsupported
                )));
            }
        }
        .map_err(|e| AudioError::StreamError(e.to_string()))?;

        stream
            .play()
            .map_err(|e| AudioError::StreamError(format!("Failed to start capture stream: {e}")))?;

        // Spawn conversion task: raw frames -> resample/downmix -> 20ms mono 16kHz frames.
        let cancel_clone = cancel.clone();
        tokio::spawn(async move {
            conversion_task(
                raw_rx,
                sender,
                native_sample_rate,
                native_channels as usize,
                cancel_clone,
            )
            .await;
        });

        // Supervisor task to manage stream lifetime against the cancellation token.
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
    /// **Requires explicit opt-in** — not used as a silent fallback.
    pub fn create_mock_capture(
        config: AudioCaptureConfig,
        cancel: CancellationToken,
    ) -> (Self, mpsc::Receiver<Vec<f32>>) {
        let (sender, receiver) = mpsc::channel::<Vec<f32>>(32);
        let chunk_size = config.chunk_size; // Use configured chunk size

        tokio::spawn(async move {
            let interval_ms = if config.sample_rate > 0 {
                (chunk_size as f32 / config.sample_rate as f32 * 1000.0).round() as u64
            } else {
                20
            };
            let mut interval =
                tokio::time::interval(std::time::Duration::from_millis(interval_ms.max(1)));
            let mut phase: f32 = 0.0;

            loop {
                tokio::select! {
                    _ = cancel.cancelled() => {
                        debug!("Mock audio capture cancelled");
                        break;
                    }
                    _ = interval.tick() => {
                        // Generate synthetic 440 Hz test tone (normalized amplitude).
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

/// Simple adapter wrapper for a mono f32 buffer to satisfy rubato's Adapter trait.
struct MonoBufferAdapter<'a> {
    data: &'a [f32],
    frames: usize,
}

unsafe impl<'a> audioadapter::Adapter<f32> for MonoBufferAdapter<'a> {
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

/// Conversion task: receives raw frames (native rate, native channels, f32),
/// downsamples to mono, resamples to 16kHz, and emits 20ms frames (320 samples).
async fn conversion_task(
    mut raw_rx: mpsc::Receiver<Vec<f32>>,
    sender: mpsc::Sender<Vec<f32>>,
    native_sample_rate: u32,
    native_channels: usize,
    cancel: CancellationToken,
) {
    if native_sample_rate == TARGET_SAMPLE_RATE {
        let mut mono_buffer = Vec::new();
        loop {
            tokio::select! {
                _ = cancel.cancelled() => {
                    debug!("Audio capture conversion task cancelled");
                    break;
                }
                frames = raw_rx.recv() => {
                    match frames {
                        Some(raw_frames) => {
                            if raw_frames.is_empty() {
                                continue;
                            }
                            let mono_frames = if native_channels > 1 {
                                downmix_to_mono(&raw_frames, native_channels)
                            } else {
                                raw_frames
                            };
                            mono_buffer.extend(mono_frames);
                            while mono_buffer.len() >= TARGET_FRAME_SAMPLES {
                                let frame: Vec<f32> = mono_buffer.drain(0..TARGET_FRAME_SAMPLES).collect();
                                if sender.send(frame).await.is_err() {
                                    return;
                                }
                            }
                        }
                        None => break,
                    }
                }
            }
        }
        return;
    }

    // Create resampler using rubato's Async sinc resampler (adjustable, fixed input size).
    // Always use 1 channel since we downmix to mono before resampling.
    let mut resampler = match build_resampler(native_sample_rate, TARGET_SAMPLE_RATE, 1) {
        Ok(r) => r,
        Err(e) => {
            warn!("Failed to create resampler: {e}");
            return;
        }
    };

    // Get the resampler's required input chunk size.
    let chunk_size = resampler.input_frames_next();

    // Accumulator for raw mono frames before resampling.
    let mut raw_mono_buffer = Vec::new();
    // Accumulator for mono 16kHz samples before framing into 20ms chunks.
    let mut mono_buffer = Vec::new();

    loop {
        tokio::select! {
            _ = cancel.cancelled() => {
                debug!("Audio capture conversion task cancelled");
                break;
            }
            frames = raw_rx.recv() => {
                match frames {
                    Some(raw_frames) => {
                        if raw_frames.is_empty() {
                            continue;
                        }

                        // Downmix to mono (average channels).
                        let mono_frames = if native_channels > 1 {
                            downmix_to_mono(&raw_frames, native_channels)
                        } else {
                            raw_frames
                        };

                        // Accumulate until we have enough for the resampler.
                        raw_mono_buffer.extend(mono_frames);

                        // Process full chunks through the resampler.
                        while raw_mono_buffer.len() >= chunk_size {
                            let chunk: Vec<f32> = raw_mono_buffer.drain(0..chunk_size).collect();
                            let input = MonoBufferAdapter {
                                data: &chunk,
                                frames: chunk.len(),
                            };
                            match resampler.process(&input, None) {
                                Ok(waves_out) => {
                                    // waves_out is InterleavedOwned<f32> with 1 channel (mono).
                                    // For mono, the interleaved data IS the channel data.
                                    // Take ownership of the underlying data vector.
                                    let resampled = waves_out.take_data();
                                    mono_buffer.extend(resampled);

                                    // Emit 20ms frames (320 samples @ 16kHz).
                                    while mono_buffer.len() >= TARGET_FRAME_SAMPLES {
                                        let frame: Vec<f32> = mono_buffer.drain(0..TARGET_FRAME_SAMPLES).collect();
                                        if sender.send(frame).await.is_err() {
                                            debug!("Audio capture sender closed, stopping conversion task");
                                            return;
                                        }
                                    }
                                }
                                Err(e) => {
                                    warn!("Resampling error: {e}");
                                }
                            }
                        }
                    }
                    None => {
                        // Channel closed.
                        debug!("Raw frame channel closed, stopping conversion task");
                        break;
                    }
                }
            }
        }
    }
}

/// Build a sinc resampler with fixed input size for native_rate -> TARGET_SAMPLE_RATE.
fn build_resampler(
    native_rate: u32,
    target_rate: u32,
    _channels: usize,
) -> Result<Async<f32>, AudioError> {
    let ratio = target_rate as f64 / native_rate as f64;

    // Sinc interpolation parameters: moderate quality, reasonable latency.
    let params = SincInterpolationParameters {
        sinc_len: 128,
        f_cutoff: Some(0.95),
        interpolation: SincInterpolationType::Cubic,
        oversampling_factor: 16,
        window: WindowFunction::BlackmanHarris2,
    };

    // Input chunk size: ~20ms at native rate (allows faster processing for tests).
    let chunk_size = (native_rate as f64 * 0.02).ceil() as usize;

    // Always use 1 channel since we downmix to mono before resampling.
    let channels = 1;

    let resampler = Async::<f32>::new_sinc(
        ratio,
        2.0, // max ratio deviation
        &params,
        chunk_size,
        channels,
        FixedAsync::Input, // fixed input size
    )
    .map_err(|e| AudioError::StreamError(format!("Failed to create resampler: {e}")))?;

    Ok(resampler)
}

/// Convert raw bytes from cpal to normalized f32 samples (interleaved channels).
#[cfg(test)]
fn convert_to_f32(data: &[u8], format: SampleFormat, channels: usize) -> Vec<f32> {
    let sample_size = format.sample_size();
    let frame_count = data.len() / (sample_size * channels);
    let mut output = Vec::with_capacity(frame_count * channels);

    match format {
        SampleFormat::F32 => {
            let samples: &[f32] = bytemuck::cast_slice(data);
            output.extend_from_slice(samples);
        }
        SampleFormat::I16 => {
            let samples: &[i16] = bytemuck::cast_slice(data);
            // i16 range: -32768 to 32767. Use asymmetric normalization for symmetric [-1, 1] range.
            for &s in samples {
                let val = if s >= 0 {
                    s as f32 / 32767.0 // i16::MAX = 32767
                } else {
                    s as f32 / 32768.0 // |i16::MIN| = 32768
                };
                output.push(val);
            }
        }
        SampleFormat::U16 => {
            let samples: &[u16] = bytemuck::cast_slice(data);
            // u16 range: 0 to 65535. Midpoint is 32768. Use 32768.0 for symmetric [-1, 1] range.
            const U16_MID: f32 = 32768.0;
            for &s in samples {
                output.push((s as f32 - U16_MID) / U16_MID);
            }
        }
        SampleFormat::I8 => {
            let samples: &[i8] = bytemuck::cast_slice(data);
            const I8_DIV: f32 = 128.0;
            for &s in samples {
                output.push(s as f32 / I8_DIV);
            }
        }
        SampleFormat::U8 => {
            let samples: &[u8] = bytemuck::cast_slice(data);
            const U8_MID: f32 = 128.0;
            for &s in samples {
                output.push((s as f32 - U8_MID) / U8_MID);
            }
        }
        _ => {
            warn!("Unsupported sample format: {:?}", format);
            return Vec::new();
        }
    }

    output
}

/// Downmix interleaved multi-channel audio to mono by averaging channels.
fn downmix_to_mono(interleaved: &[f32], channels: usize) -> Vec<f32> {
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

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::time::{timeout, Duration};

    #[tokio::test]
    async fn test_mock_capture_and_cancellation() {
        let cancel = CancellationToken::new();
        let config = AudioCaptureConfig {
            chunk_size: TARGET_FRAME_SAMPLES,
            sample_rate: TARGET_SAMPLE_RATE,
            channels: TARGET_CHANNELS,
            ..Default::default()
        };

        let (_controller, mut rx) =
            AudioCaptureController::create_mock_capture(config, cancel.clone());

        // Must receive at least one frame promptly.
        let first_frame = timeout(Duration::from_millis(100), rx.recv())
            .await
            .expect("Timeout receiving mock frame")
            .expect("Failed to receive mock audio frame");
        assert_eq!(first_frame.len(), TARGET_FRAME_SAMPLES);

        // Signal cancellation.
        cancel.cancel();

        // Drain any already buffered frame, then stream should cleanly terminate.
        tokio::time::sleep(Duration::from_millis(50)).await;
        assert!(
            rx.try_recv().is_err() || timeout(Duration::from_millis(50), rx.recv()).await.is_err()
        );
    }

    #[tokio::test]
    async fn test_nonexistent_device_capture_fails_gracefully() {
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
    fn test_convert_to_f32_i16() {
        let i16_samples = [32767i16, 0, -32768];
        let bytes = bytemuck::cast_slice(&i16_samples);
        let f32_samples = convert_to_f32(bytes, SampleFormat::I16, 1);
        assert!((f32_samples[0] - 1.0).abs() < 1e-5);
        assert_eq!(f32_samples[1], 0.0);
        assert!((f32_samples[2] + 1.0).abs() < 1e-5);
    }

    #[test]
    fn test_convert_to_f32_u16() {
        let u16_samples = [65535u16, 32768, 0];
        let bytes = bytemuck::cast_slice(&u16_samples);
        let f32_samples = convert_to_f32(bytes, SampleFormat::U16, 1);
        // Note: exact 1.0 at 65535 and 0.0 at 32768 are mathematically incompatible with linear mapping.
        // Using tolerance 1e-4 to account for linear mapping approximation.
        assert!((f32_samples[0] - 1.0).abs() < 1e-4);
        assert!((f32_samples[1] - 0.0).abs() < 1e-4);
        assert!((f32_samples[2] + 1.0).abs() < 1e-4);
    }

    #[test]
    fn test_convert_to_f32_f32() {
        let f32_samples = [0.5f32, -0.25, 1.0];
        let bytes = bytemuck::cast_slice(&f32_samples);
        let result = convert_to_f32(bytes, SampleFormat::F32, 1);
        assert_eq!(result, vec![0.5, -0.25, 1.0]);
    }

    #[test]
    fn test_resampler_build() {
        for native in [8000, 16000, 44100, 48000, 96000] {
            let resampler = build_resampler(native, TARGET_SAMPLE_RATE, 1);
            assert!(
                resampler.is_ok(),
                "Failed to build resampler for {native}Hz"
            );
        }
        // Test stereo
        let resampler = build_resampler(48000, TARGET_SAMPLE_RATE, 2);
        assert!(resampler.is_ok());
    }
}
