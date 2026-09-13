//! # TOM Engine (`tom-engine`)
//!
//! Deterministic, low-level nervous system for TOM (The Operating Mind).
//! Responsible for hardware monitoring, audio capture/playback, Windows named pipe IPC,
//! hotkeys, and internal asynchronous event dispatch.

pub mod audio;
pub mod events;
pub mod input;
pub mod ipc;
pub mod lifecycle;
pub mod logging;
pub mod system;

/// Engine version metadata
pub const ENGINE_VERSION: &str = env!("CARGO_PKG_VERSION");
pub const ENGINE_NAME: &str = "tom-engine";

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_engine_metadata() {
        assert_eq!(ENGINE_NAME, "tom-engine");
        assert!(!ENGINE_VERSION.is_empty());
    }
}
