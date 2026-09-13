//! # CPU Telemetry Module
//!
//! Exposes structured CPU metrics: usage percentage, core count, and operating frequency.
//!
//! Follows `skills/system-design/python-rust-boundary/SKILL.md` and
//! `skills/rust/error-handling/SKILL.md`.

use serde::{Deserialize, Serialize};
use sysinfo::System;

/// Structured CPU telemetry snapshot.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct CpuInfo {
    /// Overall CPU utilization percentage (0.0 to 100.0).
    pub usage_percent: f64,
    /// Number of available CPU cores (logical).
    pub core_count: usize,
    /// Operating frequency in MHz (0 if unavailable).
    pub frequency_mhz: u64,
}

/// Extract structured CPU metrics from a refreshed `sysinfo::System` instance.
pub fn get_cpu_info(sys: &System) -> CpuInfo {
    let raw_usage = sys.global_cpu_usage();
    // Clamp to valid range [0.0, 100.0] and round to 2 decimal places
    let clamped_usage = (raw_usage as f64).clamp(0.0, 100.0);
    let usage_percent = (clamped_usage * 100.0).round() / 100.0;

    let cpus = sys.cpus();
    let core_count = if cpus.is_empty() {
        sys.physical_core_count().unwrap_or(1)
    } else {
        cpus.len()
    };

    let frequency_mhz = cpus.first().map(|c| c.frequency()).unwrap_or(0);

    CpuInfo {
        usage_percent,
        core_count,
        frequency_mhz,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_cpu_info_invariants() {
        let mut sys = System::new();
        sys.refresh_cpu_usage();

        let info = get_cpu_info(&sys);
        assert!(
            info.usage_percent >= 0.0 && info.usage_percent <= 100.0,
            "usage_percent must be in [0, 100], got {}",
            info.usage_percent
        );
        assert!(info.core_count > 0, "core_count must be > 0");
    }
}
