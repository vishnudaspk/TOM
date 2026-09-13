use serde::{Deserialize, Serialize};

use crate::input::hotkeys::{KeyCode, KeyModifiers};

/// Indicates whether a key was pressed down or released.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum KeyAction {
    KeyDown,
    KeyUp,
}

/// A structured, validated keyboard input event.
///
/// Designed strictly as a typed event representation without allowing arbitrary
/// or unrestricted string command execution.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct KeyboardEvent {
    pub key: KeyCode,
    pub modifiers: KeyModifiers,
    pub action: KeyAction,
    pub timestamp_ms: u64,
}

impl KeyboardEvent {
    /// Creates a new keyboard event with current system timestamp.
    pub fn new(key: KeyCode, modifiers: KeyModifiers, action: KeyAction) -> Self {
        let timestamp_ms = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_millis() as u64)
            .unwrap_or(0);

        Self {
            key,
            modifiers,
            action,
            timestamp_ms,
        }
    }

    /// Returns `true` if the event represents a key down action.
    pub fn is_down(&self) -> bool {
        self.action == KeyAction::KeyDown
    }

    /// Returns `true` if any modifier key was held during this event.
    pub fn has_modifiers(&self) -> bool {
        self.modifiers.has_any()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_keyboard_event_creation_and_properties() {
        let mods = KeyModifiers {
            ctrl: true,
            alt: false,
            shift: false,
            win: false,
        };

        let ev = KeyboardEvent::new(KeyCode::KeyC, mods, KeyAction::KeyDown);
        assert_eq!(ev.key, KeyCode::KeyC);
        assert!(ev.is_down());
        assert!(ev.has_modifiers());
        assert!(ev.timestamp_ms > 0);
    }

    #[test]
    fn test_keyboard_event_serialization() {
        let ev = KeyboardEvent::new(KeyCode::Space, KeyModifiers::default(), KeyAction::KeyUp);

        let json = serde_json::to_string(&ev).expect("serialization failed");
        assert!(json.contains("Space"));
        assert!(json.contains("KeyUp"));

        let deserialized: KeyboardEvent =
            serde_json::from_str(&json).expect("deserialization failed");
        assert_eq!(deserialized.key, ev.key);
        assert_eq!(deserialized.action, ev.action);
    }
}
