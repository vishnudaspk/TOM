use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::{broadcast, RwLock};
use tokio_util::sync::CancellationToken;
use tracing::{debug, info, warn};

use crate::input::InputError;

/// Enumerates supported keyboard keys for hotkey combinations.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum KeyCode {
    Space,
    Escape,
    Enter,
    F1,
    F2,
    F3,
    F4,
    F5,
    F6,
    F7,
    F8,
    F9,
    F10,
    F11,
    F12,
    KeyA,
    KeyB,
    KeyC,
    KeyD,
    KeyE,
    KeyF,
    KeyG,
    KeyH,
    KeyI,
    KeyJ,
    KeyK,
    KeyL,
    KeyM,
    KeyN,
    KeyO,
    KeyP,
    KeyQ,
    KeyR,
    KeyS,
    KeyT,
    KeyU,
    KeyV,
    KeyW,
    KeyX,
    KeyY,
    KeyZ,
    Digit0,
    Digit1,
    Digit2,
    Digit3,
    Digit4,
    Digit5,
    Digit6,
    Digit7,
    Digit8,
    Digit9,
}

impl KeyCode {
    /// Returns the corresponding Windows Virtual-Key code.
    pub fn to_vk_code(self) -> u32 {
        match self {
            KeyCode::Space => 0x20,
            KeyCode::Escape => 0x1B,
            KeyCode::Enter => 0x0D,
            KeyCode::F1 => 0x70,
            KeyCode::F2 => 0x71,
            KeyCode::F3 => 0x72,
            KeyCode::F4 => 0x73,
            KeyCode::F5 => 0x74,
            KeyCode::F6 => 0x75,
            KeyCode::F7 => 0x76,
            KeyCode::F8 => 0x77,
            KeyCode::F9 => 0x78,
            KeyCode::F10 => 0x79,
            KeyCode::F11 => 0x7A,
            KeyCode::F12 => 0x7B,
            KeyCode::KeyA => 0x41,
            KeyCode::KeyB => 0x42,
            KeyCode::KeyC => 0x43,
            KeyCode::KeyD => 0x44,
            KeyCode::KeyE => 0x45,
            KeyCode::KeyF => 0x46,
            KeyCode::KeyG => 0x47,
            KeyCode::KeyH => 0x48,
            KeyCode::KeyI => 0x49,
            KeyCode::KeyJ => 0x4A,
            KeyCode::KeyK => 0x4B,
            KeyCode::KeyL => 0x4C,
            KeyCode::KeyM => 0x4D,
            KeyCode::KeyN => 0x4E,
            KeyCode::KeyO => 0x4F,
            KeyCode::KeyP => 0x50,
            KeyCode::KeyQ => 0x51,
            KeyCode::KeyR => 0x52,
            KeyCode::KeyS => 0x53,
            KeyCode::KeyT => 0x54,
            KeyCode::KeyU => 0x55,
            KeyCode::KeyV => 0x56,
            KeyCode::KeyW => 0x57,
            KeyCode::KeyX => 0x58,
            KeyCode::KeyY => 0x59,
            KeyCode::KeyZ => 0x5A,
            KeyCode::Digit0 => 0x30,
            KeyCode::Digit1 => 0x31,
            KeyCode::Digit2 => 0x32,
            KeyCode::Digit3 => 0x33,
            KeyCode::Digit4 => 0x34,
            KeyCode::Digit5 => 0x35,
            KeyCode::Digit6 => 0x36,
            KeyCode::Digit7 => 0x37,
            KeyCode::Digit8 => 0x38,
            KeyCode::Digit9 => 0x39,
        }
    }
}

/// Modifier keys pressed in conjunction with a hotkey.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Default, Serialize, Deserialize)]
pub struct KeyModifiers {
    pub ctrl: bool,
    pub alt: bool,
    pub shift: bool,
    pub win: bool,
}

impl KeyModifiers {
    /// Returns true if at least one modifier key is set.
    pub fn has_any(&self) -> bool {
        self.ctrl || self.alt || self.shift || self.win
    }

    /// Converts modifier flags to Windows Win32 `fsModifiers` bitfield.
    pub fn to_win32_modifiers(&self) -> u32 {
        let mut flags = 0u32;
        if self.alt {
            flags |= 0x0001; // MOD_ALT
        }
        if self.ctrl {
            flags |= 0x0002; // MOD_CONTROL
        }
        if self.shift {
            flags |= 0x0004; // MOD_SHIFT
        }
        if self.win {
            flags |= 0x0008; // MOD_WIN
        }
        // MOD_NOREPEAT (0x4000) prevents repeated WM_HOTKEY messages on hold
        flags | 0x4000
    }
}

/// Strongly typed action triggered by a hotkey press.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum HotkeyAction {
    ToggleListening,
    CancelTask,
    PushToTalk,
    Custom(String),
}

/// A registered hotkey binding.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct HotkeyBinding {
    pub id: String,
    pub key: KeyCode,
    pub modifiers: KeyModifiers,
    pub action: HotkeyAction,
    pub enabled: bool,
}

impl HotkeyBinding {
    pub fn new(
        id: impl Into<String>,
        key: KeyCode,
        modifiers: KeyModifiers,
        action: HotkeyAction,
    ) -> Self {
        Self {
            id: id.into(),
            key,
            modifiers,
            action,
            enabled: true,
        }
    }
}

/// Manages hotkey registration, action dispatching, and OS integration.
#[derive(Clone)]
pub struct HotkeyManager {
    bindings: Arc<RwLock<HashMap<String, HotkeyBinding>>>,
    action_sender: broadcast::Sender<HotkeyAction>,
}

impl HotkeyManager {
    /// Creates a new `HotkeyManager` with an internal broadcast capacity of 64.
    pub fn new() -> Self {
        let (action_sender, _) = broadcast::channel(64);
        Self {
            bindings: Arc::new(RwLock::new(HashMap::new())),
            action_sender,
        }
    }

    /// Registers a new hotkey binding.
    ///
    /// Rejects duplicate IDs or duplicate key+modifier combinations.
    pub async fn register_hotkey(&self, binding: HotkeyBinding) -> Result<(), InputError> {
        let mut map = self.bindings.write().await;

        if map.contains_key(&binding.id) {
            return Err(InputError::AlreadyRegistered(binding.id));
        }

        // Verify key combination isn't already assigned
        for existing in map.values() {
            if existing.key == binding.key && existing.modifiers == binding.modifiers {
                return Err(InputError::AlreadyRegistered(format!(
                    "Key combination already bound to '{}'",
                    existing.id
                )));
            }
        }

        info!(id = %binding.id, key = ?binding.key, action = ?binding.action, "Registered hotkey binding");
        map.insert(binding.id.clone(), binding);
        Ok(())
    }

    /// Unregisters an existing hotkey binding by ID.
    pub async fn unregister_hotkey(&self, id: &str) -> Result<(), InputError> {
        let mut map = self.bindings.write().await;
        if map.remove(id).is_some() {
            info!(id = %id, "Unregistered hotkey binding");
            Ok(())
        } else {
            Err(InputError::NotFound(id.to_string()))
        }
    }

    /// Enables or disables a hotkey binding.
    pub async fn set_enabled(&self, id: &str, enabled: bool) -> Result<(), InputError> {
        let mut map = self.bindings.write().await;
        if let Some(binding) = map.get_mut(id) {
            binding.enabled = enabled;
            debug!(id = %id, enabled = enabled, "Updated hotkey enabled status");
            Ok(())
        } else {
            Err(InputError::NotFound(id.to_string()))
        }
    }

    /// Returns a list of all currently registered hotkey bindings.
    pub async fn list_hotkeys(&self) -> Vec<HotkeyBinding> {
        let map = self.bindings.read().await;
        map.values().cloned().collect()
    }

    /// Subscribes to the stream of actions dispatched by triggered hotkeys.
    pub fn subscribe(&self) -> broadcast::Receiver<HotkeyAction> {
        self.action_sender.subscribe()
    }

    /// Dispatches a hotkey action given a binding ID.
    ///
    /// Used by OS message loops or synthetic test drivers.
    pub async fn trigger_action(&self, id: &str) -> Result<HotkeyAction, InputError> {
        let map = self.bindings.read().await;
        let binding = map
            .get(id)
            .ok_or_else(|| InputError::NotFound(id.to_string()))?;

        if !binding.enabled {
            return Err(InputError::NotFound(format!("Hotkey '{id}' is disabled")));
        }

        let action = binding.action.clone();
        drop(map);

        // Publish to broadcast subscribers
        let _ = self.action_sender.send(action.clone());
        info!(id = %id, action = ?action, "Hotkey action triggered and dispatched");
        Ok(action)
    }

    /// Starts a background OS hotkey listener that respects cancellation.
    ///
    /// On Windows, runs a dedicated OS thread with a message pump and `RegisterHotKey`.
    /// On non-Windows, safely awaits cancellation.
    pub async fn run_os_listener(
        self: Arc<Self>,
        cancel: CancellationToken,
    ) -> Result<(), InputError> {
        #[cfg(windows)]
        {
            let manager = Arc::clone(&self);
            let listener_cancel = cancel.clone();

            // Run Windows message loop on a dedicated thread to avoid blocking Tokio workers
            tokio::task::spawn_blocking(move || run_windows_hotkey_loop(manager, listener_cancel))
                .await
                .map_err(|e| InputError::ListenerError(e.to_string()))?
        }

        #[cfg(not(windows))]
        {
            cancel.cancelled().await;
            Ok(())
        }
    }
}

impl Default for HotkeyManager {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(windows)]
#[repr(C)]
#[derive(Debug, Clone, Copy, Default)]
struct Win32Point {
    x: i32,
    y: i32,
}

#[cfg(windows)]
#[repr(C)]
#[derive(Debug, Clone, Copy, Default)]
struct Win32Msg {
    hwnd: *mut std::ffi::c_void,
    message: u32,
    w_param: usize,
    l_param: isize,
    time: u32,
    pt: Win32Point,
}

#[cfg(windows)]
extern "system" {
    fn RegisterHotKey(hwnd: *mut std::ffi::c_void, id: i32, fs_modifiers: u32, vk: u32) -> i32;
    fn UnregisterHotKey(hwnd: *mut std::ffi::c_void, id: i32) -> i32;
    fn PeekMessageW(
        msg: *mut Win32Msg,
        hwnd: *mut std::ffi::c_void,
        filter_min: u32,
        filter_max: u32,
        remove_msg: u32,
    ) -> i32;
}

#[cfg(windows)]
const WM_HOTKEY: u32 = 0x0312;
#[cfg(windows)]
const PM_REMOVE: u32 = 0x0001;

#[cfg(windows)]
fn run_windows_hotkey_loop(
    manager: Arc<HotkeyManager>,
    cancel: CancellationToken,
) -> Result<(), InputError> {
    debug!("Starting Windows background hotkey message loop");

    // Fetch snapshot of bindings to register with OS
    let rt = match tokio::runtime::Handle::try_current() {
        Ok(h) => h,
        Err(_) => {
            return Err(InputError::ListenerError(
                "No tokio runtime handle available".into(),
            ))
        }
    };

    let bindings = rt.block_on(manager.list_hotkeys());
    let mut registered_ids = Vec::new();

    for (index, binding) in bindings.iter().enumerate() {
        let hotkey_id = (index + 1) as i32;
        let vk = binding.key.to_vk_code();
        let fs = binding.modifiers.to_win32_modifiers();

        let ret = unsafe { RegisterHotKey(std::ptr::null_mut(), hotkey_id, fs, vk) };
        if ret != 0 {
            registered_ids.push((hotkey_id, binding.id.clone()));
            debug!(id = %binding.id, hotkey_id = hotkey_id, "Registered OS hotkey");
        } else {
            warn!(id = %binding.id, "Failed to register OS hotkey (may already be reserved by another app)");
        }
    }

    while !cancel.is_cancelled() {
        let mut msg = Win32Msg::default();
        let has_msg = unsafe { PeekMessageW(&mut msg, std::ptr::null_mut(), 0, 0, PM_REMOVE) };

        if has_msg != 0 {
            if msg.message == WM_HOTKEY {
                let id = msg.w_param as i32;
                if let Some((_, binding_id)) = registered_ids.iter().find(|(hid, _)| *hid == id) {
                    let b_id = binding_id.clone();
                    let mgr = Arc::clone(&manager);
                    rt.block_on(async move {
                        let _ = mgr.trigger_action(&b_id).await;
                    });
                }
            }
        } else {
            // Sleep briefly to yield CPU when no Windows messages are pending
            std::thread::sleep(std::time::Duration::from_millis(15));
        }
    }

    // Unregister all hotkeys cleanly on loop exit
    for (hid, _) in registered_ids {
        unsafe {
            UnregisterHotKey(std::ptr::null_mut(), hid);
        }
    }

    debug!("Windows background hotkey message loop terminated cleanly");
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_hotkey_manager_registration_and_list() {
        let manager = HotkeyManager::new();

        let binding1 = HotkeyBinding::new(
            "toggle_listen",
            KeyCode::Space,
            KeyModifiers {
                ctrl: true,
                alt: false,
                shift: false,
                win: false,
            },
            HotkeyAction::ToggleListening,
        );

        let binding2 = HotkeyBinding::new(
            "cancel_task",
            KeyCode::Escape,
            KeyModifiers {
                ctrl: false,
                alt: false,
                shift: false,
                win: false,
            },
            HotkeyAction::CancelTask,
        );

        manager
            .register_hotkey(binding1)
            .await
            .expect("registration failed");
        manager
            .register_hotkey(binding2)
            .await
            .expect("registration failed");

        let list = manager.list_hotkeys().await;
        assert_eq!(list.len(), 2);
    }

    #[tokio::test]
    async fn test_duplicate_registration_rejected() {
        let manager = HotkeyManager::new();

        let binding1 = HotkeyBinding::new(
            "action_one",
            KeyCode::KeyT,
            KeyModifiers {
                ctrl: true,
                alt: true,
                shift: false,
                win: false,
            },
            HotkeyAction::PushToTalk,
        );

        let binding_duplicate_id = HotkeyBinding::new(
            "action_one",
            KeyCode::KeyK,
            KeyModifiers::default(),
            HotkeyAction::CancelTask,
        );

        let binding_duplicate_key = HotkeyBinding::new(
            "action_two",
            KeyCode::KeyT,
            KeyModifiers {
                ctrl: true,
                alt: true,
                shift: false,
                win: false,
            },
            HotkeyAction::ToggleListening,
        );

        manager
            .register_hotkey(binding1)
            .await
            .expect("first reg ok");

        let err_id = manager.register_hotkey(binding_duplicate_id).await;
        assert!(err_id.is_err());

        let err_key = manager.register_hotkey(binding_duplicate_key).await;
        assert!(err_key.is_err());
    }

    #[tokio::test]
    async fn test_trigger_action_and_subscription_channel() {
        let manager = HotkeyManager::new();
        let mut rx = manager.subscribe();

        let binding = HotkeyBinding::new(
            "my_action",
            KeyCode::KeyA,
            KeyModifiers::default(),
            HotkeyAction::ToggleListening,
        );

        manager.register_hotkey(binding).await.expect("reg ok");

        let action = manager
            .trigger_action("my_action")
            .await
            .expect("trigger failed");
        assert_eq!(action, HotkeyAction::ToggleListening);

        let received = rx.recv().await.expect("failed to receive broadcast action");
        assert_eq!(received, HotkeyAction::ToggleListening);
    }

    #[tokio::test]
    async fn test_disabled_hotkey_cannot_trigger() {
        let manager = HotkeyManager::new();

        let binding = HotkeyBinding::new(
            "disabled_act",
            KeyCode::KeyD,
            KeyModifiers::default(),
            HotkeyAction::CancelTask,
        );

        manager.register_hotkey(binding).await.expect("reg ok");
        manager
            .set_enabled("disabled_act", false)
            .await
            .expect("set enabled ok");

        let res = manager.trigger_action("disabled_act").await;
        assert!(res.is_err());
    }

    #[tokio::test]
    async fn test_unregister_hotkey() {
        let manager = HotkeyManager::new();

        let binding = HotkeyBinding::new(
            "temp_act",
            KeyCode::KeyX,
            KeyModifiers::default(),
            HotkeyAction::PushToTalk,
        );

        manager.register_hotkey(binding).await.expect("reg ok");
        assert_eq!(manager.list_hotkeys().await.len(), 1);

        manager
            .unregister_hotkey("temp_act")
            .await
            .expect("unreg ok");
        assert_eq!(manager.list_hotkeys().await.len(), 0);

        // Second unregister should return NotFound
        let res = manager.unregister_hotkey("temp_act").await;
        assert!(res.is_err());
    }
}
