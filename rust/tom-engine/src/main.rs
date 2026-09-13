use tom_engine::events::{EngineEvent, EventBus, DEFAULT_EVENT_BUS_CAPACITY};
use tom_engine::lifecycle::EngineLifecycle;
use tom_engine::{logging, ENGINE_NAME, ENGINE_VERSION};
use tracing::info;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    logging::init_logging();

    info!(
        engine = ENGINE_NAME,
        version = ENGINE_VERSION,
        "Starting TOM Engine"
    );

    let lifecycle = EngineLifecycle::default();

    // Initialize internal event bus
    let (event_bus, _event_rx) = EventBus::new(DEFAULT_EVENT_BUS_CAPACITY);

    // Spawn a lightweight engine heartbeat task as proof of background task orchestration
    let heartbeat_token = lifecycle.child_token();
    let heartbeat_handle = tokio::spawn(async move {
        info!("Engine supervisor heartbeat active");
        heartbeat_token.cancelled().await;
        info!("Engine supervisor heartbeat stopping");
        Ok(())
    });

    let mut lifecycle = lifecycle;
    lifecycle.spawn_task("heartbeat", heartbeat_handle);

    #[cfg(windows)]
    {
        let dispatcher = std::sync::Arc::new(tom_engine::ipc::create_default_dispatcher());
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
