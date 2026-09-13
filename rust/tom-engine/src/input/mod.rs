pub mod hotkeys;
pub mod keyboard;
pub mod mouse;

pub use hotkeys::{HotkeyAction, HotkeyBinding, HotkeyManager, KeyCode, KeyModifiers};
pub use keyboard::{KeyAction, KeyboardEvent};
pub use mouse::{MouseAction, MouseButton, MouseEvent, MousePosition};

/// Strongly typed errors for the input and hotkey subsystem.
#[derive(Debug, thiserror::Error)]
pub enum InputError {
    #[error("Hotkey '{0}' is already registered")]
    AlreadyRegistered(String),

    #[error("Hotkey '{0}' was not found")]
    NotFound(String),

    #[error("Failed to register hotkey with operating system: {0}")]
    RegistrationFailed(String),

    #[error("Input listener error: {0}")]
    ListenerError(String),

    #[error("Input operation cancelled")]
    Cancelled,
}
