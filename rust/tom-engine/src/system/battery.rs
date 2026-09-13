//! # Battery Telemetry Module
//!
//! Provides battery state telemetry with graceful fallback for desktop systems
//! and machines without battery hardware.
//!
//! Follows `skills/system-design/python-rust-boundary/SKILL.md` and
//! `skills/rust/error-handling/SKILL.md`.

use serde::{Deserialize, Serialize};

/// Structured Battery telemetry snapshot.
///
/// Follows strict availability semantics:
/// - If a battery is detected: `Available`
/// - On desktops, servers, VMs, or if status cannot be read: `Unavailable`
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(untagged)]
pub enum BatteryInfo {
    Available {
        available: bool,
        percent: f64,
        charging: bool,
    },
    Unavailable {
        available: bool,
        reason: String,
    },
}

impl BatteryInfo {
    /// Returns `true` if battery telemetry is available.
    pub fn is_available(&self) -> bool {
        match self {
            BatteryInfo::Available { available, .. } => *available,
            BatteryInfo::Unavailable { available, .. } => *available,
        }
    }

    /// Construct an unavailable battery telemetry result.
    pub fn unavailable(reason: impl Into<String>) -> Self {
        BatteryInfo::Unavailable {
            available: false,
            reason: reason.into(),
        }
    }
}

#[cfg(windows)]
#[repr(C)]
#[derive(Debug, Default, Clone, Copy)]
struct Win32SystemPowerStatus {
    ac_line_status: u8,
    battery_flag: u8,
    battery_life_percent: u8,
    system_status_flag: u8,
    battery_life_time: u32,
    battery_full_life_time: u32,
}

#[cfg(windows)]
extern "system" {
    fn GetSystemPowerStatus(lp_system_power_status: *mut Win32SystemPowerStatus) -> i32;
}

/// Query system battery status.
///
/// On Windows, probes `GetSystemPowerStatus`. If no battery is installed or the
/// machine is a desktop/VM, returns `BatteryInfo::Unavailable`. Never returns
/// synthetic 0% values.
pub fn get_battery_info() -> BatteryInfo {
    #[cfg(windows)]
    {
        let mut status = Win32SystemPowerStatus::default();
        let ret = unsafe { GetSystemPowerStatus(&mut status) };

        if ret == 0 {
            tracing::debug!("GetSystemPowerStatus failed");
            return BatteryInfo::unavailable("battery_unavailable");
        }

        // Bit 7 (128) means "No system battery", and 255 means "Unknown status"
        if status.battery_flag & 128 != 0 || status.battery_life_percent == 255 {
            return BatteryInfo::unavailable("battery_unavailable");
        }

        // Bit 3 (8) means Charging, or AC line is online and battery is below 100%
        let charging = (status.battery_flag & 8 != 0)
            || (status.ac_line_status == 1 && status.battery_life_percent < 100);
        let percent = (status.battery_life_percent as f64).clamp(0.0, 100.0);

        BatteryInfo::Available {
            available: true,
            percent,
            charging,
        }
    }

    #[cfg(not(windows))]
    {
        BatteryInfo::unavailable("unsupported_platform")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_battery_info_contract() {
        let info = get_battery_info();
        match &info {
            BatteryInfo::Available {
                available,
                percent,
                charging: _,
            } => {
                assert!(*available);
                assert!(*percent >= 0.0 && *percent <= 100.0);
            }
            BatteryInfo::Unavailable { available, reason } => {
                assert!(!(*available));
                assert!(!reason.is_empty());
            }
        }
    }

    #[test]
    fn test_battery_unavailable_serialization() {
        let unavailable = BatteryInfo::unavailable("battery_unavailable");
        let val = serde_json::to_value(&unavailable).expect("serialization failed");
        assert_eq!(val["available"], false);
        assert_eq!(val["reason"], "battery_unavailable");
    }

    #[test]
    fn test_battery_available_serialization() {
        let available = BatteryInfo::Available {
            available: true,
            percent: 74.0,
            charging: true,
        };
        let val = serde_json::to_value(&available).expect("serialization failed");
        assert_eq!(val["available"], true);
        assert_eq!(val["percent"], 74.0);
        assert_eq!(val["charging"], true);
    }
}
