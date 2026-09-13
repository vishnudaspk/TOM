use serde::{Deserialize, Serialize};

/// Enumerates standard mouse buttons.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum MouseButton {
    Left,
    Right,
    Middle,
}

/// Enumerates typed mouse movement or interaction actions.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum MouseAction {
    Down,
    Up,
    Click,
    Move,
    Scroll { delta_y: i32 },
}

/// 2D screen coordinate for mouse position.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Default, Serialize, Deserialize)]
pub struct MousePosition {
    pub x: i32,
    pub y: i32,
}

impl MousePosition {
    pub fn new(x: i32, y: i32) -> Self {
        Self { x, y }
    }

    /// Clamps position to screen boundaries to prevent out-of-bounds positioning.
    pub fn clamp_to_bounds(&mut self, min_x: i32, min_y: i32, max_x: i32, max_y: i32) {
        self.x = self.x.clamp(min_x, max_x);
        self.y = self.y.clamp(min_y, max_y);
    }
}

/// A structured, validated mouse input event.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct MouseEvent {
    pub position: MousePosition,
    pub button: Option<MouseButton>,
    pub action: MouseAction,
    pub timestamp_ms: u64,
}

impl MouseEvent {
    pub fn new(position: MousePosition, button: Option<MouseButton>, action: MouseAction) -> Self {
        let timestamp_ms = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_millis() as u64)
            .unwrap_or(0);

        Self {
            position,
            button,
            action,
            timestamp_ms,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_mouse_position_clamping() {
        let mut pos = MousePosition::new(-50, 2500);
        pos.clamp_to_bounds(0, 0, 1920, 1080);
        assert_eq!(pos.x, 0);
        assert_eq!(pos.y, 1080);
    }

    #[test]
    fn test_mouse_event_creation_and_serialization() {
        let ev = MouseEvent::new(
            MousePosition::new(100, 200),
            Some(MouseButton::Left),
            MouseAction::Click,
        );

        assert_eq!(ev.position.x, 100);
        assert_eq!(ev.position.y, 200);
        assert_eq!(ev.button, Some(MouseButton::Left));

        let json = serde_json::to_string(&ev).expect("serialization failed");
        assert!(json.contains("Click"));
        assert!(json.contains("Left"));

        let deserialized: MouseEvent = serde_json::from_str(&json).expect("deserialization failed");
        assert_eq!(deserialized.position, ev.position);
        assert_eq!(deserialized.button, ev.button);
    }
}
