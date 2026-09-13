use cpal::traits::{DeviceTrait, HostTrait};
use serde::{Deserialize, Serialize};
use tracing::{debug, warn};

use crate::audio::AudioError;

/// Information describing an audio input or output device.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AudioDeviceInfo {
    pub name: String,
    pub is_default: bool,
    pub is_input: bool,
    pub min_channels: u16,
    pub max_channels: u16,
    pub min_sample_rate: u32,
    pub max_sample_rate: u32,
}

/// System-wide audio host and available device inventory.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AudioHostInfo {
    pub host_id: String,
    pub input_devices: Vec<AudioDeviceInfo>,
    pub output_devices: Vec<AudioDeviceInfo>,
    pub default_input_device: Option<String>,
    pub default_output_device: Option<String>,
}

/// Enumerates all audio input and output devices available on the system.
///
/// Runs on a blocking thread via `tokio::task::spawn_blocking` to prevent
/// COM/WASAPI initialization from blocking the async runtime worker threads.
pub async fn get_audio_devices() -> Result<AudioHostInfo, AudioError> {
    tokio::task::spawn_blocking(get_audio_devices_sync)
        .await
        .map_err(|e| {
            AudioError::HostError(format!("Audio enumeration task panicked or cancelled: {e}"))
        })?
}

/// Synchronous audio device enumeration.
pub fn get_audio_devices_sync() -> Result<AudioHostInfo, AudioError> {
    let host = cpal::default_host();
    let host_id = format!("{:?}", host.id());

    let default_input_name = host.default_input_device().and_then(|d| d.name().ok());
    let default_output_name = host.default_output_device().and_then(|d| d.name().ok());

    let mut input_devices = Vec::new();
    match host.input_devices() {
        Ok(devices) => {
            for device in devices {
                let name = match device.name() {
                    Ok(n) => n,
                    Err(e) => {
                        debug!("Skipping input device with unreadable name: {e}");
                        continue;
                    }
                };

                let is_default = default_input_name.as_deref() == Some(&name);
                let (min_ch, max_ch, min_sr, max_sr) = match device.supported_input_configs() {
                    Ok(configs) => {
                        let mut min_c = u16::MAX;
                        let mut max_c = 0;
                        let mut min_s = u32::MAX;
                        let mut max_s = 0;

                        for c in configs {
                            min_c = min_c.min(c.channels());
                            max_c = max_c.max(c.channels());
                            min_s = min_s.min(c.min_sample_rate().0);
                            max_s = max_s.max(c.max_sample_rate().0);
                        }

                        if max_c == 0 {
                            (1, 2, 16000, 48000)
                        } else {
                            (min_c, max_c, min_s, max_s)
                        }
                    }
                    Err(err) => {
                        debug!("Could not query input configs for device '{name}': {err}");
                        (1, 2, 16000, 48000)
                    }
                };

                input_devices.push(AudioDeviceInfo {
                    name,
                    is_default,
                    is_input: true,
                    min_channels: min_ch,
                    max_channels: max_ch,
                    min_sample_rate: min_sr,
                    max_sample_rate: max_sr,
                });
            }
        }
        Err(e) => {
            warn!("Failed to query audio input devices: {e}");
        }
    }

    let mut output_devices = Vec::new();
    match host.output_devices() {
        Ok(devices) => {
            for device in devices {
                let name = match device.name() {
                    Ok(n) => n,
                    Err(e) => {
                        debug!("Skipping output device with unreadable name: {e}");
                        continue;
                    }
                };

                let is_default = default_output_name.as_deref() == Some(&name);
                let (min_ch, max_ch, min_sr, max_sr) = match device.supported_output_configs() {
                    Ok(configs) => {
                        let mut min_c = u16::MAX;
                        let mut max_c = 0;
                        let mut min_s = u32::MAX;
                        let mut max_s = 0;

                        for c in configs {
                            min_c = min_c.min(c.channels());
                            max_c = max_c.max(c.channels());
                            min_s = min_s.min(c.min_sample_rate().0);
                            max_s = max_s.max(c.max_sample_rate().0);
                        }

                        if max_c == 0 {
                            (1, 2, 16000, 48000)
                        } else {
                            (min_c, max_c, min_s, max_s)
                        }
                    }
                    Err(err) => {
                        debug!("Could not query output configs for device '{name}': {err}");
                        (1, 2, 16000, 48000)
                    }
                };

                output_devices.push(AudioDeviceInfo {
                    name,
                    is_default,
                    is_input: false,
                    min_channels: min_ch,
                    max_channels: max_ch,
                    min_sample_rate: min_sr,
                    max_sample_rate: max_sr,
                });
            }
        }
        Err(e) => {
            warn!("Failed to query audio output devices: {e}");
        }
    }

    Ok(AudioHostInfo {
        host_id,
        input_devices,
        output_devices,
        default_input_device: default_input_name,
        default_output_device: default_output_name,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_get_audio_devices_does_not_panic() {
        // Must succeed without panic on all platforms (even without audio devices)
        let result = get_audio_devices().await;
        assert!(
            result.is_ok(),
            "Audio device enumeration failed: {:?}",
            result.err()
        );
        let info = result.unwrap();
        assert!(!info.host_id.is_empty());
        // Devices list can be empty or populated depending on hardware, neither panics
    }

    #[test]
    fn test_device_info_serialization() {
        let dev = AudioDeviceInfo {
            name: "Microphone Array".to_string(),
            is_default: true,
            is_input: true,
            min_channels: 1,
            max_channels: 2,
            min_sample_rate: 16000,
            max_sample_rate: 48000,
        };

        let json = serde_json::to_string(&dev).expect("serialization failed");
        assert!(json.contains("Microphone Array"));
        assert!(json.contains("min_sample_rate"));

        let deserialized: AudioDeviceInfo =
            serde_json::from_str(&json).expect("deserialization failed");
        assert_eq!(deserialized, dev);
    }
}
