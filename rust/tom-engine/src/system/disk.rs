//! # Disk Telemetry Module
//!
//! Exposes structured disk capacity and mount point telemetry across all mounted volumes.
//!
//! Follows `skills/system-design/python-rust-boundary/SKILL.md` and
//! `skills/rust/error-handling/SKILL.md`.

use serde::{Deserialize, Serialize};
use sysinfo::Disks;

/// Information about a single disk volume or mount point.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct DiskItem {
    /// Mount path (e.g. `C:\` on Windows, `/` on Unix).
    pub mount_point: String,
    /// Total capacity of the volume in bytes.
    pub total_bytes: u64,
    /// Available / free capacity in bytes.
    pub available_bytes: u64,
}

/// Structured disk telemetry snapshot containing all active mount points.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct DiskInfo {
    pub disks: Vec<DiskItem>,
}

/// Extract disk capacity metrics for all currently mounted drives.
pub fn get_disk_info() -> DiskInfo {
    let disks = Disks::new_with_refreshed_list();

    let items = disks
        .list()
        .iter()
        .map(|disk| DiskItem {
            mount_point: disk.mount_point().to_string_lossy().into_owned(),
            total_bytes: disk.total_space(),
            available_bytes: disk.available_space(),
        })
        .collect();

    DiskInfo { disks: items }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_disk_info_contract() {
        let info = get_disk_info();
        // On any standard OS/CI machine, at least one mount point exists
        if !info.disks.is_empty() {
            for disk in &info.disks {
                assert!(!disk.mount_point.is_empty(), "mount_point cannot be empty");
                assert!(disk.total_bytes > 0, "total_bytes must be > 0");
                assert!(
                    disk.available_bytes <= disk.total_bytes,
                    "available_bytes ({}) cannot exceed total_bytes ({})",
                    disk.available_bytes,
                    disk.total_bytes
                );
            }
        }
    }

    #[test]
    fn test_disk_serialization() {
        let info = DiskInfo {
            disks: vec![DiskItem {
                mount_point: "C:\\".to_string(),
                total_bytes: 512_000_000_000,
                available_bytes: 220_000_000_000,
            }],
        };

        let val = serde_json::to_value(&info).expect("serialization failed");
        assert_eq!(val["disks"][0]["mount_point"], "C:\\");
        assert_eq!(val["disks"][0]["total_bytes"], 512_000_000_000u64);
        assert_eq!(val["disks"][0]["available_bytes"], 220_000_000_000u64);
    }
}
