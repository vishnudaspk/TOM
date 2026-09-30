//! # Baseline IPC Handlers
//!
//! Diagnostic, status, and system telemetry handlers registered with `tom-engine`'s IPC dispatcher.
//!
//! Follows the architecture defined in `skills/rust/ipc/SKILL.md` and
//! `skills/system-design/python-rust-boundary/SKILL.md`.

use serde_json::json;
use std::sync::Arc;

use super::dispatcher::IpcDispatcher;
use super::protocol::{error_code, IpcError};
use crate::system::SystemMonitor;
use crate::{ENGINE_NAME, ENGINE_VERSION};

/// Register the foundational engine handlers, system telemetry handlers, and audio handlers.
pub fn register_baseline_handlers(dispatcher: &mut IpcDispatcher) {
    let monitor = Arc::new(SystemMonitor::new());
    let audio = Arc::new(crate::audio::AudioManager::new());
    register_baseline_handlers_with_components(dispatcher, monitor, audio);
}

/// Register baseline and system telemetry handlers using an injected `SystemMonitor`.
pub fn register_baseline_handlers_with_monitor(
    dispatcher: &mut IpcDispatcher,
    monitor: Arc<SystemMonitor>,
) {
    let audio = Arc::new(crate::audio::AudioManager::new());
    register_baseline_handlers_with_components(dispatcher, monitor, audio);
}

/// Register baseline, system telemetry, and audio handlers using injected components.
pub fn register_baseline_handlers_with_components(
    dispatcher: &mut IpcDispatcher,
    monitor: Arc<SystemMonitor>,
    audio: Arc<crate::audio::AudioManager>,
) {
    // engine.ping -> confirms liveness and version correlation
    dispatcher.register("engine.ping", |_params| async {
        Ok(json!({
            "pong": true,
            "version": ENGINE_VERSION,
        }))
    });

    // engine.status -> reports high-level engine identity and health
    dispatcher.register("engine.status", |_params| async {
        Ok(json!({
            "engine": ENGINE_NAME,
            "status": "running",
        }))
    });

    // system.cpu -> structured CPU metrics
    let m_cpu = monitor.clone();
    dispatcher.register("system.cpu", move |_params| {
        let m = m_cpu.clone();
        async move {
            let cpu_info = m.get_cpu().await;
            serde_json::to_value(cpu_info).map_err(|e| {
                IpcError::new(
                    error_code::INTERNAL,
                    format!("Failed to serialize CPU info: {e}"),
                )
            })
        }
    });

    // system.memory -> structured memory metrics
    let m_mem = monitor.clone();
    dispatcher.register("system.memory", move |_params| {
        let m = m_mem.clone();
        async move {
            let mem_info = m.get_memory().await;
            serde_json::to_value(mem_info).map_err(|e| {
                IpcError::new(
                    error_code::INTERNAL,
                    format!("Failed to serialize memory info: {e}"),
                )
            })
        }
    });

    // system.gpu -> GPU metrics (with graceful fallback)
    let m_gpu = monitor.clone();
    dispatcher.register("system.gpu", move |_params| {
        let m = m_gpu.clone();
        async move {
            let gpu_info = m.get_gpu();
            serde_json::to_value(gpu_info).map_err(|e| {
                IpcError::new(
                    error_code::INTERNAL,
                    format!("Failed to serialize GPU info: {e}"),
                )
            })
        }
    });

    // system.battery -> battery metrics (with graceful fallback)
    let m_bat = monitor.clone();
    dispatcher.register("system.battery", move |_params| {
        let m = m_bat.clone();
        async move {
            let bat_info = m.get_battery();
            serde_json::to_value(bat_info).map_err(|e| {
                IpcError::new(
                    error_code::INTERNAL,
                    format!("Failed to serialize battery info: {e}"),
                )
            })
        }
    });

    // system.disk -> mounted volume storage metrics
    let m_disk = monitor.clone();
    dispatcher.register("system.disk", move |_params| {
        let m = m_disk.clone();
        async move {
            let disk_info = m.get_disk();
            serde_json::to_value(disk_info).map_err(|e| {
                IpcError::new(
                    error_code::INTERNAL,
                    format!("Failed to serialize disk info: {e}"),
                )
            })
        }
    });

    // system.processes -> bounded process metrics
    let m_proc = monitor.clone();
    dispatcher.register("system.processes", move |params| {
        let m = m_proc.clone();
        async move {
            let limit = params
                .get("limit")
                .and_then(|v| v.as_u64())
                .map(|l| l as usize)
                .unwrap_or(crate::system::processes::DEFAULT_PROCESS_LIMIT);
            let proc_info = m.get_processes(limit).await;
            serde_json::to_value(proc_info).map_err(|e| {
                IpcError::new(
                    error_code::INTERNAL,
                    format!("Failed to serialize process info: {e}"),
                )
            })
        }
    });

    // system.all -> aggregated system snapshot
    let m_all = monitor.clone();
    dispatcher.register("system.all", move |_params| {
        let m = m_all.clone();
        async move {
            let snapshot = m.get_all().await;
            serde_json::to_value(snapshot).map_err(|e| {
                IpcError::new(
                    error_code::INTERNAL,
                    format!("Failed to serialize system snapshot: {e}"),
                )
            })
        }
    });

    register_audio_handlers(dispatcher, audio);
}

/// Register audio subsystem IPC handlers.
pub fn register_audio_handlers(
    dispatcher: &mut IpcDispatcher,
    audio: Arc<crate::audio::AudioManager>,
) {
    // audio.devices -> enumerates input and output audio devices
    let a_dev = audio.clone();
    dispatcher.register("audio.devices", move |_params| {
        let a = a_dev.clone();
        async move {
            let devices = a.get_devices().await.map_err(|e| {
                IpcError::new(
                    error_code::INTERNAL,
                    format!("Failed to get audio devices: {e}"),
                )
            })?;
            serde_json::to_value(devices).map_err(|e| {
                IpcError::new(
                    error_code::INTERNAL,
                    format!("Failed to serialize audio devices: {e}"),
                )
            })
        }
    });

    // audio.status -> returns capture/playback active state and sample counts
    let a_stat = audio.clone();
    dispatcher.register("audio.status", move |_params| {
        let a = a_stat.clone();
        async move {
            let status = a.status();
            serde_json::to_value(status).map_err(|e| {
                IpcError::new(
                    error_code::INTERNAL,
                    format!("Failed to serialize audio status: {e}"),
                )
            })
        }
    });

    // audio.capture_start -> begins audio capture session into ring buffer
    let a_cap_start = audio.clone();
    dispatcher.register("audio.capture_start", move |params| {
        let a = a_cap_start.clone();
        async move {
            let config = crate::audio::AudioCaptureConfig {
                device_name: params
                    .get("device_name")
                    .and_then(|v| v.as_str())
                    .map(|s| s.to_string()),
                sample_rate: params
                    .get("sample_rate")
                    .and_then(|v| v.as_u64())
                    .map(|v| v as u32)
                    .unwrap_or(16000),
                channels: params
                    .get("channels")
                    .and_then(|v| v.as_u64())
                    .map(|v| v as u16)
                    .unwrap_or(1),
                chunk_size: params
                    .get("chunk_size")
                    .and_then(|v| v.as_u64())
                    .map(|v| v as usize)
                    .unwrap_or(1600),
            };
            a.start_capture(config).map_err(|e| {
                IpcError::new(
                    error_code::INTERNAL,
                    format!("Failed to start audio capture: {e}"),
                )
            })?;
            Ok(json!({ "success": true, "message": "Audio capture started" }))
        }
    });

    // audio.capture_stop -> stops capture and returns count of buffered samples
    let a_cap_stop = audio.clone();
    dispatcher.register("audio.capture_stop", move |_params| {
        let a = a_cap_stop.clone();
        async move {
            let count = a.stop_capture().map_err(|e| {
                IpcError::new(
                    error_code::INTERNAL,
                    format!("Failed to stop audio capture: {e}"),
                )
            })?;
            Ok(json!({ "success": true, "captured_samples": count }))
        }
    });

    // audio.get_speech -> retrieves captured speech PCM buffer
    let a_speech = audio.clone();
    dispatcher.register("audio.get_speech", move |params| {
        let a = a_speech.clone();
        async move {
            let clear = params
                .get("clear")
                .and_then(|v| v.as_bool())
                .unwrap_or(true);
            let (samples, sample_rate, channels) = a.get_speech(clear);
            let count = samples.len();
            Ok(json!({
                "samples": samples,
                "sample_rate": sample_rate,
                "channels": channels,
                "sample_count": count
            }))
        }
    });

    // audio.play_buffer -> plays synthesized PCM buffer asynchronously
    let a_play = audio.clone();
    dispatcher.register("audio.play_buffer", move |params| {
        let a = a_play.clone();
        async move {
            let samples: Vec<f32> = params
                .get("samples")
                .and_then(|v| serde_json::from_value(v.clone()).ok())
                .ok_or_else(|| {
                    IpcError::new(
                        error_code::INVALID_PARAMS,
                        "Missing or invalid 'samples' array",
                    )
                })?;
            let config = crate::audio::AudioPlaybackConfig {
                device_name: params
                    .get("device_name")
                    .and_then(|v| v.as_str())
                    .map(|s| s.to_string()),
                sample_rate: params
                    .get("sample_rate")
                    .and_then(|v| v.as_u64())
                    .map(|v| v as u32)
                    .unwrap_or(24000),
                channels: params
                    .get("channels")
                    .and_then(|v| v.as_u64())
                    .map(|v| v as u16)
                    .unwrap_or(1),
            };
            let played = a.play_buffer(samples, config).await.map_err(|e| {
                IpcError::new(
                    error_code::INTERNAL,
                    format!("Failed to play audio buffer: {e}"),
                )
            })?;
            Ok(json!({ "success": true, "samples_played": played }))
        }
    });

    // audio.playback_stop -> halts playback immediately (barge-in)
    let a_play_stop = audio;
    dispatcher.register("audio.playback_stop", move |_params| {
        let a = a_play_stop.clone();
        async move {
            a.stop_playback().map_err(|e| {
                IpcError::new(
                    error_code::INTERNAL,
                    format!("Failed to stop audio playback: {e}"),
                )
            })?;
            Ok(json!({ "success": true, "message": "Playback stopped" }))
        }
    });
}

/// Create a new `IpcDispatcher` pre-configured with default timeout, baseline, system, and audio handlers.
pub fn create_default_dispatcher() -> IpcDispatcher {
    let mut dispatcher = IpcDispatcher::new();
    register_baseline_handlers(&mut dispatcher);
    dispatcher
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ipc::protocol::IpcRequest;

    #[tokio::test]
    async fn test_engine_ping_handler() {
        let dispatcher = create_default_dispatcher();
        let req = IpcRequest::new("ping_1", "engine.ping", json!({}));
        let resp = dispatcher.dispatch(req).await;

        assert_eq!(resp.id, "ping_1");
        assert!(resp.success);
        let data = resp.data.expect("should have data");
        assert_eq!(data["pong"], true);
        assert_eq!(data["version"], ENGINE_VERSION);
    }

    #[tokio::test]
    async fn test_engine_status_handler() {
        let dispatcher = create_default_dispatcher();
        let req = IpcRequest::new("status_1", "engine.status", json!({}));
        let resp = dispatcher.dispatch(req).await;

        assert_eq!(resp.id, "status_1");
        assert!(resp.success);
        let data = resp.data.expect("should have data");
        assert_eq!(data["engine"], ENGINE_NAME);
        assert_eq!(data["status"], "running");
    }

    #[tokio::test]
    async fn test_system_cpu_handler() {
        let dispatcher = create_default_dispatcher();
        let req = IpcRequest::new("cpu_1", "system.cpu", json!({}));
        let resp = dispatcher.dispatch(req).await;

        assert_eq!(resp.id, "cpu_1");
        assert!(resp.success);
        let data = resp.data.expect("should have data");
        let usage = data["usage_percent"].as_f64().expect("valid usage_percent");
        assert!((0.0..=100.0).contains(&usage));
        assert!(data["core_count"].as_u64().expect("valid core_count") > 0);
    }

    #[tokio::test]
    async fn test_system_memory_handler() {
        let dispatcher = create_default_dispatcher();
        let req = IpcRequest::new("mem_1", "system.memory", json!({}));
        let resp = dispatcher.dispatch(req).await;

        assert_eq!(resp.id, "mem_1");
        assert!(resp.success);
        let data = resp.data.expect("should have data");
        assert!(data["total_bytes"].as_u64().expect("valid total_bytes") > 0);
    }

    #[tokio::test]
    async fn test_system_gpu_handler() {
        let dispatcher = create_default_dispatcher();
        let req = IpcRequest::new("gpu_1", "system.gpu", json!({}));
        let resp = dispatcher.dispatch(req).await;

        assert_eq!(resp.id, "gpu_1");
        assert!(
            resp.success,
            "system.gpu must return success even if unavailable"
        );
        let data = resp.data.expect("should have data");
        let available = data["available"].as_bool().expect("has available field");
        if available {
            assert!(data.get("name").is_some());
            assert!(data.get("utilization_percent").is_some());
        } else {
            assert_eq!(data["reason"], "telemetry_unavailable");
        }
    }

    #[tokio::test]
    async fn test_system_battery_handler() {
        let dispatcher = create_default_dispatcher();
        let req = IpcRequest::new("bat_1", "system.battery", json!({}));
        let resp = dispatcher.dispatch(req).await;

        assert_eq!(resp.id, "bat_1");
        assert!(
            resp.success,
            "system.battery must return success even if unavailable"
        );
        let data = resp.data.expect("should have data");
        let available = data["available"].as_bool().expect("has available field");
        if available {
            let pct = data["percent"].as_f64().expect("has percent");
            assert!((0.0..=100.0).contains(&pct));
        } else {
            assert_eq!(data["reason"], "battery_unavailable");
        }
    }

    #[tokio::test]
    async fn test_system_disk_handler() {
        let dispatcher = create_default_dispatcher();
        let req = IpcRequest::new("disk_1", "system.disk", json!({}));
        let resp = dispatcher.dispatch(req).await;

        assert_eq!(resp.id, "disk_1");
        assert!(resp.success);
        let data = resp.data.expect("should have data");
        let disks = data["disks"].as_array().expect("disks array");
        for d in disks {
            assert!(d["total_bytes"].as_u64().is_some());
        }
    }

    #[tokio::test]
    async fn test_system_processes_handler() {
        let dispatcher = create_default_dispatcher();
        let req = IpcRequest::new("proc_1", "system.processes", json!({"limit": 5}));
        let resp = dispatcher.dispatch(req).await;

        assert_eq!(resp.id, "proc_1");
        assert!(resp.success);
        let data = resp.data.expect("should have data");
        let procs = data["processes"].as_array().expect("processes array");
        assert!(procs.len() <= 5);
    }

    #[tokio::test]
    async fn test_system_all_handler() {
        let dispatcher = create_default_dispatcher();
        let req = IpcRequest::new("all_1", "system.all", json!({}));
        let resp = dispatcher.dispatch(req).await;

        assert_eq!(resp.id, "all_1");
        assert!(resp.success);
        let data = resp.data.expect("should have data");

        // Verify all 6 domains are present in aggregate snapshot
        assert!(data.get("cpu").is_some(), "cpu field present");
        assert!(data.get("memory").is_some(), "memory field present");
        assert!(data.get("gpu").is_some(), "gpu field present");
        assert!(data.get("battery").is_some(), "battery field present");
        assert!(data.get("disk").is_some(), "disk field present");
        assert!(data.get("processes").is_some(), "processes field present");

        // Verify cpu and memory are populated
        assert!(data["cpu"]["core_count"].as_u64().unwrap_or(0) > 0);
        assert!(data["memory"]["total_bytes"].as_u64().unwrap_or(0) > 0);
    }

    #[tokio::test]
    async fn test_audio_devices_handler() {
        let dispatcher = create_default_dispatcher();
        let req = IpcRequest::new("aud_dev_1", "audio.devices", json!({}));
        let resp = dispatcher.dispatch(req).await;

        assert_eq!(resp.id, "aud_dev_1");
        assert!(resp.success);
        let data = resp.data.expect("audio.devices should succeed");
        assert!(data.get("host_id").is_some());
        assert!(data.get("input_devices").is_some());
        assert!(data.get("output_devices").is_some());
    }

    #[tokio::test]
    async fn test_audio_status_handler() {
        let dispatcher = create_default_dispatcher();
        let req = IpcRequest::new("aud_stat_1", "audio.status", json!({}));
        let resp = dispatcher.dispatch(req).await;

        assert_eq!(resp.id, "aud_stat_1");
        assert!(resp.success);
        let data = resp.data.expect("audio.status should succeed");
        assert_eq!(data["capture_active"], false);
        assert_eq!(data["playback_active"], false);
        assert_eq!(data["capture_sample_rate"], 16000);
        assert_eq!(data["capture_channels"], 1);
        assert_eq!(data["captured_samples"], 0);
    }

    #[tokio::test]
    async fn test_audio_capture_flow_handler() {
        let dispatcher = create_default_dispatcher();

        // 1. Start capture
        let start_req = IpcRequest::new(
            "aud_cap_1",
            "audio.capture_start",
            json!({"sample_rate": 16000, "channels": 1, "chunk_size": 320}),
        );
        let start_resp = dispatcher.dispatch(start_req).await;
        assert!(start_resp.success);

        // Verify status shows capture active
        let stat_resp = dispatcher
            .dispatch(IpcRequest::new("aud_stat_2", "audio.status", json!({})))
            .await;
        assert_eq!(stat_resp.data.unwrap()["capture_active"], true);

        // Wait briefly for mock frames to accumulate
        tokio::time::sleep(std::time::Duration::from_millis(60)).await;

        // 2. Stop capture
        let stop_req = IpcRequest::new("aud_cap_2", "audio.capture_stop", json!({}));
        let stop_resp = dispatcher.dispatch(stop_req).await;
        assert!(stop_resp.success);

        // 3. Retrieve captured speech
        let speech_req =
            IpcRequest::new("aud_speech_1", "audio.get_speech", json!({"clear": true}));
        let speech_resp = dispatcher.dispatch(speech_req).await;
        assert!(speech_resp.success);
        let data = speech_resp.data.unwrap();
        assert_eq!(data["sample_rate"], 16000);
        assert_eq!(data["channels"], 1);
        let sample_count = data["sample_count"].as_u64().unwrap();
        assert!(sample_count > 0, "should have accumulated samples");
    }

    #[tokio::test]
    async fn test_audio_play_and_stop_handler() {
        let dispatcher = create_default_dispatcher();

        // Play synthetic sine wave samples
        let samples = vec![0.1f32, 0.2, 0.3, 0.2, 0.1, 0.0, -0.1, -0.2];
        let play_req = IpcRequest::new(
            "aud_play_1",
            "audio.play_buffer",
            json!({"samples": samples, "sample_rate": 24000, "channels": 1}),
        );
        let play_resp = dispatcher.dispatch(play_req).await;
        assert!(play_resp.success);
        assert_eq!(play_resp.data.unwrap()["samples_played"], 8);

        // Stop playback (barge-in)
        let stop_req = IpcRequest::new("aud_stop_1", "audio.playback_stop", json!({}));
        let stop_resp = dispatcher.dispatch(stop_req).await;
        assert!(stop_resp.success);
    }
}
