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

/// Register the foundational engine handlers and system telemetry handlers.
pub fn register_baseline_handlers(dispatcher: &mut IpcDispatcher) {
    let monitor = Arc::new(SystemMonitor::new());
    register_baseline_handlers_with_monitor(dispatcher, monitor);
}

/// Register baseline and system telemetry handlers using an injected `SystemMonitor`.
pub fn register_baseline_handlers_with_monitor(
    dispatcher: &mut IpcDispatcher,
    monitor: Arc<SystemMonitor>,
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
}

/// Create a new `IpcDispatcher` pre-configured with default timeout, baseline, and system handlers.
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
}
