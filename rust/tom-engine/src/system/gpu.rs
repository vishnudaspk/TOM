//! # GPU Telemetry Module
//!
//! Provides GPU hardware telemetry with automatic fallback when NVML or NVIDIA
//! hardware is unavailable.
//!
//! Follows `skills/system-design/python-rust-boundary/SKILL.md` and
//! `skills/rust/error-handling/SKILL.md`.

use serde::{Deserialize, Serialize};

/// Structured GPU telemetry result.
///
/// Follows strict availability semantics:
/// - If a supported GPU is present and readable: `Available`
/// - If absent, permission denied, driver missing, or unsupported: `Unavailable`
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(untagged)]
pub enum GpuInfo {
    Available {
        available: bool,
        vendor: String,
        name: String,
        utilization_percent: f64,
        memory_total_bytes: u64,
        memory_used_bytes: u64,
    },
    Unavailable {
        available: bool,
        reason: String,
    },
}

impl GpuInfo {
    /// Returns `true` if GPU telemetry was successfully acquired.
    pub fn is_available(&self) -> bool {
        match self {
            GpuInfo::Available { available, .. } => *available,
            GpuInfo::Unavailable { available, .. } => *available,
        }
    }

    /// Construct an unavailable GPU telemetry result with a descriptive reason.
    pub fn unavailable(reason: impl Into<String>) -> Self {
        GpuInfo::Unavailable {
            available: false,
            reason: reason.into(),
        }
    }
}

/// Probe GPU telemetry using the NVML backend.
///
/// Returns `GpuInfo::Available` if an NVIDIA GPU is found and reporting metrics,
/// or `GpuInfo::Unavailable` if NVML is not installed, no GPU exists, or access fails.
/// Never panics or fabricates synthetic 0 metrics.
pub fn get_gpu_info() -> GpuInfo {
    let nvml = match nvml_wrapper::Nvml::init() {
        Ok(n) => n,
        Err(err) => {
            tracing::debug!(error = %err, "NVML init failed; GPU telemetry unavailable");
            return GpuInfo::unavailable("telemetry_unavailable");
        }
    };

    let device = match nvml.device_by_index(0) {
        Ok(d) => d,
        Err(err) => {
            tracing::debug!(error = %err, "NVML device probe failed");
            return GpuInfo::unavailable("telemetry_unavailable");
        }
    };

    let name = match device.name() {
        Ok(n) => n,
        Err(err) => {
            tracing::debug!(error = %err, "Failed to read GPU name");
            return GpuInfo::unavailable("telemetry_unavailable");
        }
    };

    let memory = match device.memory_info() {
        Ok(m) => m,
        Err(err) => {
            tracing::debug!(error = %err, "Failed to read GPU memory info");
            return GpuInfo::unavailable("telemetry_unavailable");
        }
    };

    let utilization = match device.utilization_rates() {
        Ok(u) => u,
        Err(err) => {
            tracing::debug!(error = %err, "Failed to read GPU utilization rates");
            return GpuInfo::unavailable("telemetry_unavailable");
        }
    };

    let util_percent = (utilization.gpu as f64).clamp(0.0, 100.0);

    GpuInfo::Available {
        available: true,
        vendor: "nvidia".to_string(),
        name,
        utilization_percent: util_percent,
        memory_total_bytes: memory.total,
        memory_used_bytes: memory.used,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_gpu_info_contract() {
        let info = get_gpu_info();
        match &info {
            GpuInfo::Available {
                available,
                vendor,
                name,
                utilization_percent,
                memory_total_bytes,
                memory_used_bytes,
            } => {
                assert!(*available);
                assert!(!vendor.is_empty());
                assert!(!name.is_empty());
                assert!(*utilization_percent >= 0.0 && *utilization_percent <= 100.0);
                assert!(*memory_total_bytes > 0);
                assert!(*memory_used_bytes <= *memory_total_bytes);
            }
            GpuInfo::Unavailable { available, reason } => {
                assert!(!(*available));
                assert!(!reason.is_empty());
            }
        }
    }

    #[test]
    fn test_gpu_unavailable_serialization() {
        let unavailable = GpuInfo::unavailable("telemetry_unavailable");
        let val = serde_json::to_value(&unavailable).expect("serialization failed");
        assert_eq!(val["available"], false);
        assert_eq!(val["reason"], "telemetry_unavailable");
    }

    #[test]
    fn test_gpu_available_serialization() {
        let available = GpuInfo::Available {
            available: true,
            vendor: "nvidia".to_string(),
            name: "NVIDIA GeForce RTX 4060 Laptop GPU".to_string(),
            utilization_percent: 42.0,
            memory_total_bytes: 8589934592,
            memory_used_bytes: 2147483648,
        };
        let val = serde_json::to_value(&available).expect("serialization failed");
        assert_eq!(val["available"], true);
        assert_eq!(val["vendor"], "nvidia");
        assert_eq!(val["name"], "NVIDIA GeForce RTX 4060 Laptop GPU");
        assert_eq!(val["utilization_percent"], 42.0);
        assert_eq!(val["memory_total_bytes"], 8589934592u64);
        assert_eq!(val["memory_used_bytes"], 2147483648u64);
    }
}
