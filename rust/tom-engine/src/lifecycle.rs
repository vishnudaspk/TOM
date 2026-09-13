use std::time::Duration;
use tokio::task::JoinHandle;
use tokio::time::timeout;
use tokio_util::sync::CancellationToken;
use tracing::{error, info, warn};

/// Default timeout waiting for subsystems to terminate during graceful shutdown.
pub const DEFAULT_SHUTDOWN_TIMEOUT: Duration = Duration::from_secs(5);

/// Coordinates the lifecycle, cancellation tokens, and background subsystem tasks
/// for `tom-engine`.
///
/// Follows the Tokio patterns defined in `skills/rust/async-tokio/SKILL.md`:
/// - Single root `CancellationToken` propagating to child tokens.
/// - Explicit collection and awaiting of `JoinHandle`s.
/// - Bounded shutdown timeout preventing hangs.
pub struct EngineLifecycle {
    root_token: CancellationToken,
    handles: Vec<(&'static str, JoinHandle<anyhow::Result<()>>)>,
    shutdown_timeout: Duration,
}

impl Default for EngineLifecycle {
    fn default() -> Self {
        Self::new(DEFAULT_SHUTDOWN_TIMEOUT)
    }
}

impl EngineLifecycle {
    /// Create a new `EngineLifecycle` with a specified shutdown timeout.
    pub fn new(shutdown_timeout: Duration) -> Self {
        Self {
            root_token: CancellationToken::new(),
            handles: Vec::new(),
            shutdown_timeout,
        }
    }

    /// Obtain a child cancellation token for a subsystem.
    pub fn child_token(&self) -> CancellationToken {
        self.root_token.child_token()
    }

    /// Access the root cancellation token directly.
    pub fn root_token(&self) -> &CancellationToken {
        &self.root_token
    }

    /// Register a named background subsystem task handle.
    pub fn spawn_task(&mut self, name: &'static str, handle: JoinHandle<anyhow::Result<()>>) {
        self.handles.push((name, handle));
    }

    /// Manually request engine shutdown.
    pub fn request_shutdown(&self) {
        info!("Shutdown requested: cancelling root token");
        self.root_token.cancel();
    }

    /// Wait for an OS interrupt signal (Ctrl+C) or external cancellation.
    pub async fn wait_for_signal_or_cancellation(&self) {
        tokio::select! {
            _ = self.root_token.cancelled() => {
                info!("Lifecycle: cancellation token triggered");
            }
            res = tokio::signal::ctrl_c() => {
                match res {
                    Ok(()) => {
                        info!("Lifecycle: received Ctrl+C signal");
                    }
                    Err(err) => {
                        error!(error = %err, "Lifecycle: failed to listen for Ctrl+C signal");
                    }
                }
                self.root_token.cancel();
            }
        }
    }

    /// Perform graceful shutdown: cancel root token and await all tracked subsystem tasks.
    /// Returns the count of successfully completed tasks.
    pub async fn shutdown(mut self) -> Result<usize, Vec<String>> {
        info!("Commencing graceful shutdown of tom-engine");
        self.root_token.cancel();

        let mut errors = Vec::new();
        let mut successful = 0;

        for (name, handle) in self.handles.drain(..) {
            match timeout(self.shutdown_timeout, handle).await {
                Ok(join_res) => match join_res {
                    Ok(task_res) => match task_res {
                        Ok(()) => {
                            info!(subsystem = name, "Subsystem stopped cleanly");
                            successful += 1;
                        }
                        Err(err) => {
                            error!(subsystem = name, error = %err, "Subsystem exited with error");
                            errors.push(format!("{name}: task error: {err:#}"));
                        }
                    },
                    Err(join_err) => {
                        error!(subsystem = name, error = %join_err, "Subsystem task panicked");
                        errors.push(format!("{name}: panicked: {join_err}"));
                    }
                },
                Err(_) => {
                    warn!(
                        subsystem = name,
                        timeout_secs = self.shutdown_timeout.as_secs(),
                        "Subsystem did not shut down within timeout window"
                    );
                    errors.push(format!("{name}: shutdown timed out"));
                }
            }
        }

        if errors.is_empty() {
            info!(completed_tasks = successful, "Graceful shutdown complete");
            Ok(successful)
        } else {
            error!(
                completed_tasks = successful,
                failed_tasks = errors.len(),
                "Graceful shutdown finished with errors"
            );
            Err(errors)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::Arc;

    #[tokio::test]
    async fn test_lifecycle_cancellation_propagation() {
        let lifecycle = EngineLifecycle::default();
        let child = lifecycle.child_token();

        assert!(!child.is_cancelled());
        lifecycle.request_shutdown();
        assert!(child.is_cancelled());
    }

    #[tokio::test]
    async fn test_lifecycle_graceful_shutdown_success() {
        let mut lifecycle = EngineLifecycle::new(Duration::from_millis(500));
        let token = lifecycle.child_token();

        let completed = Arc::new(AtomicBool::new(false));
        let completed_clone = completed.clone();

        let handle = tokio::spawn(async move {
            tokio::select! {
                _ = token.cancelled() => {
                    completed_clone.store(true, Ordering::SeqCst);
                    Ok(())
                }
            }
        });

        lifecycle.spawn_task("test_worker", handle);

        let result = lifecycle.shutdown().await;
        assert!(result.is_ok());
        assert_eq!(result.unwrap(), 1);
        assert!(completed.load(Ordering::SeqCst));
    }

    #[tokio::test]
    async fn test_lifecycle_shutdown_timeout() {
        let mut lifecycle = EngineLifecycle::new(Duration::from_millis(50));
        let _token = lifecycle.child_token();

        // Worker that ignores cancellation and sleeps longer than shutdown timeout
        let handle = tokio::spawn(async move {
            tokio::time::sleep(Duration::from_millis(200)).await;
            Ok(())
        });

        lifecycle.spawn_task("hanging_worker", handle);

        let result = lifecycle.shutdown().await;
        assert!(result.is_err());
        let errors = result.unwrap_err();
        assert_eq!(errors.len(), 1);
        assert!(errors[0].contains("shutdown timed out"));
    }

    #[tokio::test]
    async fn test_lifecycle_subsystem_error_reported() {
        let mut lifecycle = EngineLifecycle::new(Duration::from_millis(500));
        let token = lifecycle.child_token();

        let handle = tokio::spawn(async move {
            token.cancelled().await;
            anyhow::bail!("simulated failure");
        });

        lifecycle.spawn_task("failing_worker", handle);

        let result = lifecycle.shutdown().await;
        assert!(result.is_err());
        let errors = result.unwrap_err();
        assert_eq!(errors.len(), 1);
        assert!(errors[0].contains("simulated failure"));
    }
}
