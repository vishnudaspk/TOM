//! # System Telemetry Subsystem
//!
//! Provides hardware and OS-level telemetry probes for CPU, memory, GPU, battery,
//! disk storage, and process metrics.
//!
//! Follows `skills/system-design/python-rust-boundary/SKILL.md`,
//! `skills/rust/async-tokio/SKILL.md`, and `skills/rust/error-handling/SKILL.md`.

pub mod battery;
pub mod cpu;
pub mod disk;
pub mod gpu;
pub mod memory;
pub mod processes;

use serde::{Deserialize, Serialize};
use std::sync::Arc;
use std::time::{Duration, Instant};
use sysinfo::{ProcessesToUpdate, System};
use tokio::sync::Mutex;

/// Minimum update interval for CPU usage queries to ensure meaningful delta sampling.
const CPU_REFRESH_INTERVAL: Duration = Duration::from_millis(200);

/// Minimum update interval for process list refresh.
const PROCESS_REFRESH_INTERVAL: Duration = Duration::from_millis(500);

/// Aggregated system snapshot representing all telemetry domains in a single payload.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct SystemSnapshot {
    pub cpu: cpu::CpuInfo,
    pub memory: memory::MemoryInfo,
    pub gpu: gpu::GpuInfo,
    pub battery: battery::BatteryInfo,
    pub disk: disk::DiskInfo,
    pub processes: Vec<processes::ProcessItem>,
}

/// Thread-safe coordinator managing shared `sysinfo::System` state.
///
/// Encapsulates refresh throttling to prevent redundant, expensive hardware queries
/// while guaranteeing sub-millisecond query latency for the IPC dispatcher.
#[derive(Clone)]
pub struct SystemMonitor {
    sys: Arc<Mutex<System>>,
    last_cpu_refresh: Arc<Mutex<Instant>>,
    last_process_refresh: Arc<Mutex<Instant>>,
}

impl Default for SystemMonitor {
    fn default() -> Self {
        Self::new()
    }
}

impl SystemMonitor {
    /// Initialize a new `SystemMonitor` with pre-refreshed baseline metrics.
    pub fn new() -> Self {
        let mut sys = System::new_all();
        sys.refresh_cpu_usage();
        sys.refresh_memory();
        sys.refresh_processes(ProcessesToUpdate::All, true);

        Self {
            sys: Arc::new(Mutex::new(sys)),
            last_cpu_refresh: Arc::new(Mutex::new(Instant::now())),
            last_process_refresh: Arc::new(Mutex::new(Instant::now())),
        }
    }

    /// Retrieve structured CPU metrics, refreshing if the sampling interval has elapsed.
    pub async fn get_cpu(&self) -> cpu::CpuInfo {
        let mut sys = self.sys.lock().await;
        let mut last_refresh = self.last_cpu_refresh.lock().await;

        if last_refresh.elapsed() >= CPU_REFRESH_INTERVAL {
            sys.refresh_cpu_usage();
            *last_refresh = Instant::now();
        }

        cpu::get_cpu_info(&sys)
    }

    /// Retrieve structured memory metrics.
    pub async fn get_memory(&self) -> memory::MemoryInfo {
        let mut sys = self.sys.lock().await;
        sys.refresh_memory();
        memory::get_memory_info(&sys)
    }

    /// Retrieve GPU metrics with graceful fallback for unavailable hardware.
    pub fn get_gpu(&self) -> gpu::GpuInfo {
        gpu::get_gpu_info()
    }

    /// Retrieve battery metrics with graceful fallback for desktop systems.
    pub fn get_battery(&self) -> battery::BatteryInfo {
        battery::get_battery_info()
    }

    /// Retrieve storage metrics for all mounted disk volumes.
    pub fn get_disk(&self) -> disk::DiskInfo {
        disk::get_disk_info()
    }

    /// Retrieve top processes bounded by `limit`.
    pub async fn get_processes(&self, limit: usize) -> processes::ProcessInfo {
        let mut sys = self.sys.lock().await;
        let mut last_refresh = self.last_process_refresh.lock().await;

        if last_refresh.elapsed() >= PROCESS_REFRESH_INTERVAL {
            sys.refresh_processes(ProcessesToUpdate::All, true);
            *last_refresh = Instant::now();
        }

        processes::get_process_info(&sys, limit)
    }

    /// Retrieve an aggregated snapshot of all system telemetry domains.
    ///
    /// Always returns a complete snapshot even if optional hardware (GPU, battery)
    /// reports unavailable.
    pub async fn get_all(&self) -> SystemSnapshot {
        let cpu = self.get_cpu().await;
        let memory = self.get_memory().await;
        let gpu = self.get_gpu();
        let battery = self.get_battery();
        let disk = self.get_disk();
        let process_info = self.get_processes(processes::DEFAULT_PROCESS_LIMIT).await;

        SystemSnapshot {
            cpu,
            memory,
            gpu,
            battery,
            disk,
            processes: process_info.processes,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_system_monitor_all_domains() {
        let monitor = SystemMonitor::new();

        let cpu = monitor.get_cpu().await;
        assert!(cpu.core_count > 0);

        let mem = monitor.get_memory().await;
        assert!(mem.total_bytes > 0);

        let _gpu = monitor.get_gpu();
        let _battery = monitor.get_battery();
        let _disk = monitor.get_disk();

        let proc = monitor
            .get_processes(processes::DEFAULT_PROCESS_LIMIT)
            .await;
        assert!(proc.processes.len() <= processes::DEFAULT_PROCESS_LIMIT);

        let snapshot = monitor.get_all().await;
        assert!(snapshot.cpu.core_count > 0);
        assert!(snapshot.memory.total_bytes > 0);
    }
}
