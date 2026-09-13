//! # Process Telemetry Module
//!
//! Exposes a bounded list of top processes sorted by CPU and memory consumption.
//! Strictly omits sensitive information such as environment variables, command line arguments,
//! or user credentials.
//!
//! Follows `skills/system-design/python-rust-boundary/SKILL.md` and
//! `skills/rust/error-handling/SKILL.md`.

use serde::{Deserialize, Serialize};
use sysinfo::System;

/// Default maximum number of top processes to return.
pub const DEFAULT_PROCESS_LIMIT: usize = 10;

/// Information about an individual running process.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ProcessItem {
    /// Process Identifier (PID).
    pub pid: u32,
    /// Executable binary name (without path or command-line arguments).
    pub name: String,
    /// CPU utilization percentage for this process.
    pub cpu_percent: f64,
    /// Memory consumption in bytes.
    pub memory_bytes: u64,
}

/// Structured process telemetry snapshot.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ProcessInfo {
    pub processes: Vec<ProcessItem>,
}

/// Extract top processes sorted by CPU utilization descending, bounded by `limit`.
pub fn get_process_info(sys: &System, limit: usize) -> ProcessInfo {
    let mut items: Vec<ProcessItem> = sys
        .processes()
        .iter()
        .map(|(pid, proc_info)| {
            let cpu_usage = (proc_info.cpu_usage() as f64).max(0.0);
            let cpu_percent = (cpu_usage * 100.0).round() / 100.0;
            ProcessItem {
                pid: pid.as_u32(),
                name: proc_info.name().to_string_lossy().into_owned(),
                cpu_percent,
                memory_bytes: proc_info.memory(),
            }
        })
        .collect();

    // Sort descending by CPU utilization, fallback to memory consumption
    items.sort_by(|a, b| {
        b.cpu_percent
            .partial_cmp(&a.cpu_percent)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| b.memory_bytes.cmp(&a.memory_bytes))
    });

    items.truncate(limit);

    ProcessInfo { processes: items }
}

#[cfg(test)]
mod tests {
    use super::*;
    use sysinfo::ProcessesToUpdate;

    #[test]
    fn test_process_info_bounded_and_valid() {
        let mut sys = System::new();
        sys.refresh_processes(ProcessesToUpdate::All, true);

        let info = get_process_info(&sys, 5);
        assert!(
            info.processes.len() <= 5,
            "processes list must be bounded by limit"
        );

        for p in &info.processes {
            assert!(!p.name.is_empty(), "process name should not be empty");
            assert!(p.cpu_percent >= 0.0, "cpu_percent must be non-negative");
        }
    }

    #[test]
    fn test_process_serialization() {
        let info = ProcessInfo {
            processes: vec![ProcessItem {
                pid: 1234,
                name: "example.exe".to_string(),
                cpu_percent: 12.4,
                memory_bytes: 524_288_000,
            }],
        };

        let val = serde_json::to_value(&info).expect("serialization failed");
        assert_eq!(val["processes"][0]["pid"], 1234);
        assert_eq!(val["processes"][0]["name"], "example.exe");
        assert_eq!(val["processes"][0]["cpu_percent"], 12.4);
        assert_eq!(val["processes"][0]["memory_bytes"], 524_288_000u64);
    }
}
