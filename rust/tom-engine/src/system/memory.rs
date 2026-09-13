//! # Memory Telemetry Module
//!
//! Exposes structured system memory metrics: total, used, available, and swap capacities in bytes.
//!
//! Follows `skills/system-design/python-rust-boundary/SKILL.md` and
//! `skills/rust/error-handling/SKILL.md`.

use serde::{Deserialize, Serialize};
use sysinfo::System;

/// Structured Memory telemetry snapshot (all sizes in bytes).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct MemoryInfo {
    /// Total physical memory capacity in bytes.
    pub total_bytes: u64,
    /// Currently used memory in bytes.
    pub used_bytes: u64,
    /// Available memory in bytes (free + readily reclaimable cache).
    pub available_bytes: u64,
    /// Total swap capacity in bytes.
    pub swap_total_bytes: u64,
    /// Currently used swap space in bytes.
    pub swap_used_bytes: u64,
}

/// Extract structured memory metrics from a refreshed `sysinfo::System` instance.
pub fn get_memory_info(sys: &System) -> MemoryInfo {
    let total_bytes = sys.total_memory();
    let used_bytes = sys.used_memory();
    let available_bytes = sys.available_memory();
    let swap_total_bytes = sys.total_swap();
    let swap_used_bytes = sys.used_swap();

    MemoryInfo {
        total_bytes,
        used_bytes,
        available_bytes,
        swap_total_bytes,
        swap_used_bytes,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_memory_info_invariants() {
        let mut sys = System::new();
        sys.refresh_memory();

        let info = get_memory_info(&sys);
        assert!(info.total_bytes > 0, "total memory must be > 0");
        assert!(
            info.used_bytes <= info.total_bytes,
            "used memory ({}) cannot exceed total ({})",
            info.used_bytes,
            info.total_bytes
        );
    }
}
