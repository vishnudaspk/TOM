//! System time information types.

use chrono::{DateTime, Local, Utc};
use serde::{Deserialize, Serialize};

/// System time information with timezone details.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct TimeInfo {
    /// Unix timestamp in seconds since epoch.
    pub timestamp_secs: u64,
    /// Nanoseconds part of the timestamp.
    pub timestamp_nanos: u32,
    /// Local timezone offset from UTC in seconds.
    pub offset_seconds: i32,
    /// Timezone name (e.g., "UTC", "PST", "EST").
    pub timezone_name: String,
    /// ISO 8601 / RFC 3339 formatted timestamp in local time.
    pub iso8601: String,
}

impl TimeInfo {
    /// Get the local time as a formatted string (e.g., "10:24 PM").
    pub fn local_time_str(&self) -> String {
        let dt = DateTime::from_timestamp(self.timestamp_secs as i64, self.timestamp_nanos)
            .unwrap_or_else(|| Utc::now())
            .with_timezone(&Local);
        dt.format("%I:%M %p").to_string()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_time_info_local_time_str() {
        // Test with a known timestamp
        let time_info = TimeInfo {
            timestamp_secs: 1704067200, // 2024-01-01 00:00:00 UTC
            timestamp_nanos: 0,
            offset_seconds: -28800, // PST (UTC-8)
            timezone_name: "PST".to_string(),
            iso8601: "2024-01-01T00:00:00-08:00".to_string(),
        };
        
        // The formatted string should contain the time
        let time_str = time_info.local_time_str();
        assert!(time_str.contains(":"));
    }
}