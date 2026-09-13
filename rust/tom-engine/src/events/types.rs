use serde::{Deserialize, Serialize};

/// Strongly typed engine events emitted by internal subsystems.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum EngineEvent {
    /// Emitted after tom-engine successfully completes initialization.
    EngineStarted,
    /// Emitted immediately before tom-engine initiates graceful shutdown.
    EngineStopping,
    /// Emitted when CPU usage crosses an anomalous threshold.
    CpuSpike { usage_percent: f64 },
    /// Emitted when system memory usage crosses an anomalous pressure threshold.
    MemoryPressure { used_bytes: u64, total_bytes: u64 },
    /// Emitted when a client connects to the IPC server.
    IpcClientConnected,
    /// Emitted when an IPC client disconnects.
    IpcClientDisconnected,
}
