pub mod buffer;
pub mod capture;
pub mod device;
pub mod playback;

pub use buffer::AudioBuffer;
pub use capture::{AudioCaptureConfig, AudioCaptureController};
pub use device::{get_audio_devices, AudioDeviceInfo, AudioHostInfo};
pub use playback::{AudioPlaybackConfig, AudioPlaybackController};

/// Strongly typed errors for the audio foundation.
#[derive(Debug, thiserror::Error)]
pub enum AudioError {
    #[error("Audio host initialization failed: {0}")]
    HostError(String),

    #[error("Audio device unavailable or not found: {0}")]
    NoDeviceFound(String),

    #[error("Unsupported audio format or sample rate: {0}")]
    UnsupportedFormat(String),

    #[error("Failed to build audio stream: {0}")]
    StreamError(String),

    #[error("Audio operation cancelled")]
    Cancelled,

    #[error("Audio buffer error: {0}")]
    BufferError(String),
}
