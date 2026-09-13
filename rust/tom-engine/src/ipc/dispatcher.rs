//! # IPC Request Dispatcher
//!
//! Method routing, handler registry, and per-request timeout enforcement for `tom-engine`.
//!
//! Follows the architecture defined in `skills/rust/ipc/SKILL.md`,
//! `skills/rust/error-handling/SKILL.md`, and `skills/rust/async-tokio/SKILL.md`.

use std::collections::HashMap;
use std::future::Future;
use std::pin::Pin;
use std::time::Duration;
use tracing::{debug, warn};

use super::protocol::{error_code, IpcError, IpcRequest, IpcResponse};

/// Default per-request execution timeout (500 milliseconds).
pub const DEFAULT_REQUEST_TIMEOUT: Duration = Duration::from_millis(500);

/// Pinned, boxed future returned by an asynchronous IPC handler.
pub type BoxFuture<'a, T> = Pin<Box<dyn Future<Output = T> + Send + 'a>>;

/// Handler function signature: accepts JSON params, returns Result<JSON data, IpcError>.
pub type HandlerFn = Box<
    dyn Fn(serde_json::Value) -> BoxFuture<'static, Result<serde_json::Value, IpcError>>
        + Send
        + Sync,
>;

/// Dispatches inbound `IpcRequest`s to registered handlers under bounded timeouts.
pub struct IpcDispatcher {
    handlers: HashMap<String, HandlerFn>,
    timeout: Duration,
}

impl Default for IpcDispatcher {
    fn default() -> Self {
        Self::new()
    }
}

impl IpcDispatcher {
    /// Create a new `IpcDispatcher` with the default timeout (500ms).
    pub fn new() -> Self {
        Self::with_timeout(DEFAULT_REQUEST_TIMEOUT)
    }

    /// Create a new `IpcDispatcher` with a custom per-request timeout.
    pub fn with_timeout(timeout: Duration) -> Self {
        Self {
            handlers: HashMap::new(),
            timeout,
        }
    }

    /// Register an asynchronous handler for a named IPC method.
    ///
    /// Accepts any closure returning a Future of `Result<serde_json::Value, IpcError>`.
    pub fn register<F, Fut>(&mut self, method: impl Into<String>, handler: F)
    where
        F: Fn(serde_json::Value) -> Fut + Send + Sync + 'static,
        Fut: Future<Output = Result<serde_json::Value, IpcError>> + Send + 'static,
    {
        let method_str = method.into();
        let boxed: HandlerFn = Box::new(move |params| Box::pin(handler(params)));
        self.handlers.insert(method_str, boxed);
    }

    /// Check if a method is registered.
    pub fn has_method(&self, method: &str) -> bool {
        self.handlers.contains_key(method)
    }

    /// Dispatch an `IpcRequest` to its registered handler under a timeout.
    ///
    /// - Validates the request first (rejects empty ID/method, version mismatch).
    /// - Returns `error_code::NOT_FOUND` if the method is not registered.
    /// - Returns `error_code::TIMEOUT` if handler execution exceeds `self.timeout`.
    /// - Returns structured `IpcResponse` with payload or error.
    pub async fn dispatch(&self, request: IpcRequest) -> IpcResponse {
        // Step 1: Protocol validation
        if let Err(val_err) = request.validate() {
            warn!(
                id = %request.id,
                code = %val_err.code,
                "Request validation failed during dispatch"
            );
            return IpcResponse::error(request.id, val_err.code, val_err.message);
        }

        // Step 2: Method lookup
        let handler = match self.handlers.get(&request.method) {
            Some(h) => h,
            None => {
                debug!(
                    id = %request.id,
                    method = %request.method,
                    "Requested method not found"
                );
                return IpcResponse::error(
                    request.id,
                    error_code::NOT_FOUND,
                    format!("method '{}' not found", request.method),
                );
            }
        };

        // Step 3: Bounded execution under timeout
        let fut = (handler)(request.params);
        match tokio::time::timeout(self.timeout, fut).await {
            Ok(handler_res) => match handler_res {
                Ok(data) => {
                    debug!(
                        id = %request.id,
                        method = %request.method,
                        "Handler executed successfully"
                    );
                    IpcResponse::ok(request.id, data)
                }
                Err(ipc_err) => {
                    warn!(
                        id = %request.id,
                        method = %request.method,
                        code = %ipc_err.code,
                        "Handler returned error"
                    );
                    IpcResponse::error(request.id, ipc_err.code, ipc_err.message)
                }
            },
            Err(_) => {
                warn!(
                    id = %request.id,
                    method = %request.method,
                    timeout_ms = self.timeout.as_millis(),
                    "Handler timed out"
                );
                IpcResponse::error(
                    request.id,
                    error_code::TIMEOUT,
                    format!("handler timed out after {}ms", self.timeout.as_millis()),
                )
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[tokio::test]
    async fn test_registered_handler_success() {
        let mut dispatcher = IpcDispatcher::new();
        dispatcher.register("test.add", |params| async move {
            let a = params["a"].as_i64().unwrap_or(0);
            let b = params["b"].as_i64().unwrap_or(0);
            Ok(json!({ "sum": a + b }))
        });

        let req = IpcRequest::new("req_10", "test.add", json!({ "a": 3, "b": 7 }));
        let resp = dispatcher.dispatch(req).await;

        assert_eq!(resp.id, "req_10");
        assert!(resp.success);
        assert_eq!(resp.data.unwrap()["sum"], 10);
        assert!(resp.error.is_none());
    }

    #[tokio::test]
    async fn test_unknown_method_returns_not_found() {
        let dispatcher = IpcDispatcher::new();
        let req = IpcRequest::new("req_11", "nonexistent.method", json!({}));
        let resp = dispatcher.dispatch(req).await;

        assert_eq!(resp.id, "req_11");
        assert!(!resp.success);
        assert_eq!(resp.error.as_ref().unwrap().code, error_code::NOT_FOUND);
        assert!(resp.error.unwrap().message.contains("not found"));
    }

    #[tokio::test]
    async fn test_handler_timeout_returns_timeout_error() {
        let mut dispatcher = IpcDispatcher::with_timeout(Duration::from_millis(25));
        dispatcher.register("slow.method", |_| async move {
            tokio::time::sleep(Duration::from_millis(100)).await;
            Ok(json!({ "done": true }))
        });

        let req = IpcRequest::new("req_12", "slow.method", json!({}));
        let resp = dispatcher.dispatch(req).await;

        assert_eq!(resp.id, "req_12");
        assert!(!resp.success);
        assert_eq!(resp.error.as_ref().unwrap().code, error_code::TIMEOUT);
        assert!(resp.error.unwrap().message.contains("timed out"));
    }

    #[tokio::test]
    async fn test_handler_returns_custom_error() {
        let mut dispatcher = IpcDispatcher::new();
        dispatcher.register("fail.method", |_| async move {
            Err(IpcError::new(
                error_code::NOT_AVAILABLE,
                "subsystem offline",
            ))
        });

        let req = IpcRequest::new("req_13", "fail.method", json!({}));
        let resp = dispatcher.dispatch(req).await;

        assert_eq!(resp.id, "req_13");
        assert!(!resp.success);
        let err = resp.error.unwrap();
        assert_eq!(err.code, error_code::NOT_AVAILABLE);
        assert_eq!(err.message, "subsystem offline");
    }
}
