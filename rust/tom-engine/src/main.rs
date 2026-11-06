use tom_engine::events::{EngineEvent, EventBus, DEFAULT_EVENT_BUS_CAPACITY};
use tom_engine::input::{HotkeyAction, HotkeyBinding, HotkeyManager, KeyCode, KeyModifiers};
use tom_engine::lifecycle::EngineLifecycle;
use tom_engine::{logging, ENGINE_NAME, ENGINE_VERSION};
use tracing::{info, warn};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    logging::init_logging();

    info!(
        engine = ENGINE_NAME,
        version = ENGINE_VERSION,
        "Starting TOM Engine"
    );

    let mut lifecycle = EngineLifecycle::default();

    // Initialize internal event bus and wire a log-sink subscriber so events aren't silently dropped
    let (event_bus, event_rx) = EventBus::new(DEFAULT_EVENT_BUS_CAPACITY);
    let log_sink_token = lifecycle.child_token();
    let log_sink = tokio::spawn(async move {
        let mut rx = event_rx;
        loop {
            tokio::select! {
                _ = log_sink_token.cancelled() => break,
                result = rx.recv() => match result {
                    Ok(event) => info!(event = ?event, "EngineEvent"),
                    Err(tokio::sync::broadcast::error::RecvError::Lagged(n)) => {
                        warn!(skipped = n, "EventBus log-sink lagged; events dropped");
                    }
                    Err(_) => break,
                }
            }
        }
        Ok(())
    });
    lifecycle.spawn_task("event_log_sink", log_sink);

    // Initialize global hotkey manager and register default bindings
    let hotkey_manager = std::sync::Arc::new(HotkeyManager::new());
    // Default: Ctrl+Space toggles listening; additional bindings can be added via IPC
    if let Err(e) = hotkey_manager
        .register_hotkey(HotkeyBinding::new(
            "toggle_listening",
            KeyCode::Space,
            KeyModifiers {
                ctrl: true,
                alt: false,
                shift: false,
                win: false,
            },
            HotkeyAction::ToggleListening,
        ))
        .await
    {
        warn!(error = %e, "Failed to register default toggle_listening hotkey");
    }

    // Spawn hotkey action log-sink (actions will be dispatched via IPC in a future iteration)
    let hk_manager_clone = hotkey_manager.clone();
    let hk_token = lifecycle.child_token();
    let hk_sink = tokio::spawn(async move {
        let mut rx = hk_manager_clone.subscribe();
        loop {
            tokio::select! {
                _ = hk_token.cancelled() => break,
                result = rx.recv() => match result {
                    Ok(action) => info!(action = ?action, "HotkeyAction triggered"),
                    Err(tokio::sync::broadcast::error::RecvError::Lagged(n)) => {
                        warn!(skipped = n, "Hotkey sink lagged; actions dropped");
                    }
                    Err(_) => break,
                }
            }
        }
        Ok(())
    });
    lifecycle.spawn_task("hotkey_sink", hk_sink);

    // Spawn the OS hotkey message loop so registered hotkeys actually fire on Windows.
    // run_os_listener blocks a dedicated thread (via spawn_blocking internally) and respects
    // the cancellation token; it is a no-op on non-Windows targets.
    let hk_os_manager = hotkey_manager.clone(); // Arc<HotkeyManager>
    let hk_os_token = lifecycle.child_token();
    let hk_os_handle = tokio::spawn(async move {
        hk_os_manager
            .run_os_listener(hk_os_token)
            .await
            .map_err(|e| anyhow::anyhow!("OS hotkey listener error: {e}"))
    });
    lifecycle.spawn_task("hotkey_os_listener", hk_os_handle);

    #[cfg(windows)]
    {
        // Audio hardware initialization (CPAL/WASAPI) can take up to several seconds on Windows.
        // Use a generous per-request timeout so capture_start and play_buffer do not time out
        // before the blocking device enumeration completes.
        let mut dispatcher_inner =
            tom_engine::ipc::IpcDispatcher::with_timeout(std::time::Duration::from_secs(10));
        tom_engine::ipc::handlers::register_baseline_handlers(&mut dispatcher_inner);
        tom_engine::ipc::handlers::register_input_handlers(
            &mut dispatcher_inner,
            hotkey_manager.clone(),
        );
        let dispatcher = std::sync::Arc::new(dispatcher_inner);
        let ipc_cancel = lifecycle.child_token();
        let ipc_handle = tokio::spawn(async move {
            tom_engine::ipc::server::run_named_pipe_server(
                tom_engine::ipc::server::PIPE_NAME,
                dispatcher,
                ipc_cancel,
            )
            .await
            .map_err(|e| anyhow::anyhow!("Named pipe server error: {e}"))
        });
        lifecycle.spawn_task("named_pipe_server", ipc_handle);
    }

    // Publish EngineStarted event after initialization completes
    event_bus.publish(EngineEvent::EngineStarted);
    info!("TOM Engine initialized. Awaiting signals or shutdown...");

    // For interactive runs, listen for Ctrl+C or cancellation token
    tokio::select! {
        _ = lifecycle.wait_for_signal_or_cancellation() => {}
    }

    // Publish EngineStopping event immediately prior to shutdown
    event_bus.publish(EngineEvent::EngineStopping);

    lifecycle
        .shutdown()
        .await
        .map_err(|errs| anyhow::anyhow!("Shutdown finished with errors: {}", errs.join("; ")))?;

    info!("TOM Engine stopped cleanly");
    Ok(())
}
