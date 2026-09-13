//! # Windows Named Pipe Transport & Server
//!
//! Provides the named pipe listener for Windows (`\\.\pipe\tom-engine`) and a generic,
//! framing-aware connection handler wired into `IpcDispatcher`.
//!
//! Follows the architecture defined in `skills/rust/ipc/SKILL.md`,
//! `skills/rust/async-tokio/SKILL.md`, and `skills/system-design/python-rust-boundary/SKILL.md`.

use std::sync::Arc;
use std::time::Duration;
use tokio::io::{AsyncBufReadExt, AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt, BufReader};
use tokio_util::sync::CancellationToken;
use tracing::{debug, error, info, warn};

use super::dispatcher::IpcDispatcher;
use super::protocol::{error_code, IpcRequest, IpcResponse};

/// Standard named pipe path on Windows.
pub const PIPE_NAME: &str = r"\\.\pipe\tom-engine";

/// Maximum allowed size for an inbound IPC frame (1 MB).
/// Protects the engine from unbounded memory allocation.
pub const MAX_FRAME_BYTES: usize = 1024 * 1024;

/// Transport-level errors.
#[derive(Debug, thiserror::Error)]
pub enum IpcTransportError {
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),

    #[error("Serialization error: {0}")]
    Serialization(#[from] serde_json::Error),

    #[error("Frame of {size} bytes exceeds maximum allowed size of {max} bytes")]
    FrameTooLarge { size: usize, max: usize },

    #[error("Operation timed out after {0:?}")]
    Timeout(Duration),

    #[error("Connection cancelled")]
    Cancelled,
}

/// Read a single line terminated by `\n` with a strict byte limit.
///
/// If the stream reaches EOF, returns `Ok(0)`.
/// If the line exceeds `max_bytes` before encountering `\n`, returns `IpcTransportError::FrameTooLarge`.
async fn read_line_bounded<R: AsyncBufReadExt + Unpin>(
    reader: &mut R,
    buf: &mut String,
    max_bytes: usize,
) -> Result<usize, IpcTransportError> {
    let mut byte_buf = Vec::new();
    let mut take = reader.take((max_bytes + 1) as u64);
    let n = take.read_until(b'\n', &mut byte_buf).await?;

    if n == 0 {
        return Ok(0);
    }

    if byte_buf.len() > max_bytes || (!byte_buf.ends_with(b"\n") && n == max_bytes + 1) {
        return Err(IpcTransportError::FrameTooLarge {
            size: byte_buf.len(),
            max: max_bytes,
        });
    }

    let s = String::from_utf8(byte_buf).map_err(|e| {
        std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            format!("non-UTF8 payload: {e}"),
        )
    })?;

    *buf = s;
    Ok(n)
}

/// Generic connection handler operating on any stream implementing `AsyncRead + AsyncWrite + Unpin`.
///
/// Inbound requests are framed, deserialized, and dispatched through `IpcDispatcher`.
/// Responses are serialized with newline framing and transmitted back to the client.
pub async fn handle_connection<S>(
    stream: S,
    dispatcher: Arc<IpcDispatcher>,
    cancel: CancellationToken,
) -> Result<(), IpcTransportError>
where
    S: AsyncRead + AsyncWrite + Unpin + Send + 'static,
{
    let (read_half, mut write_half) = tokio::io::split(stream);
    let mut reader = BufReader::new(read_half);
    let mut line_buf = String::new();

    debug!("IPC connection worker started");

    loop {
        line_buf.clear();

        let read_res = tokio::select! {
            _ = cancel.cancelled() => {
                debug!("IPC connection worker cancelled");
                return Ok(());
            }
            res = read_line_bounded(&mut reader, &mut line_buf, MAX_FRAME_BYTES) => res,
        };

        match read_res {
            Ok(0) => {
                debug!("Client disconnected cleanly (EOF)");
                return Ok(());
            }
            Ok(_n) => {
                let trimmed = line_buf.trim();
                if trimmed.is_empty() {
                    continue;
                }

                let response = match serde_json::from_str::<IpcRequest>(trimmed) {
                    Ok(request) => {
                        debug!(
                            id = %request.id,
                            method = %request.method,
                            "Dispatching inbound IPC request"
                        );
                        dispatcher.dispatch(request).await
                    }
                    Err(json_err) => {
                        warn!(error = %json_err, "Failed to parse inbound JSON request");
                        let id = serde_json::from_str::<serde_json::Value>(trimmed)
                            .ok()
                            .and_then(|v| v.get("id").and_then(|i| i.as_str()).map(String::from))
                            .unwrap_or_else(|| "unknown".to_string());

                        IpcResponse::error(
                            id,
                            error_code::INVALID_REQUEST,
                            "malformed JSON request payload",
                        )
                    }
                };

                let mut out_bytes = serde_json::to_vec(&response)?;
                out_bytes.push(b'\n');

                tokio::select! {
                    _ = cancel.cancelled() => {
                        debug!("Cancelled while writing IPC response");
                        return Ok(());
                    }
                    write_res = write_half.write_all(&out_bytes) => {
                        write_res?;
                        write_half.flush().await?;
                    }
                }
            }
            Err(IpcTransportError::FrameTooLarge { size, max }) => {
                warn!(size, max, "Inbound frame exceeded maximum allowed size");
                let err_resp = IpcResponse::error(
                    "unknown",
                    error_code::INVALID_REQUEST,
                    format!("frame of {size} bytes exceeds maximum allowed size of {max} bytes"),
                );
                if let Ok(mut out_bytes) = serde_json::to_vec(&err_resp) {
                    out_bytes.push(b'\n');
                    let _ = write_half.write_all(&out_bytes).await;
                    let _ = write_half.flush().await;
                }
                return Err(IpcTransportError::FrameTooLarge { size, max });
            }
            Err(err) => {
                warn!(error = %err, "Connection read error");
                return Err(err);
            }
        }
    }
}

/// Run the Windows Named Pipe server accept loop on the specified pipe name.
///
/// Listens on `pipe_name`, accepting client connections and spawning a worker task
/// for each connection using the shared `IpcDispatcher`. Bounded by `cancel`.
#[cfg(windows)]
pub async fn run_named_pipe_server(
    pipe_name: &str,
    dispatcher: Arc<IpcDispatcher>,
    cancel: CancellationToken,
) -> Result<(), IpcTransportError> {
    use tokio::net::windows::named_pipe::ServerOptions;

    info!(pipe = pipe_name, "Initializing Windows Named Pipe server");

    let mut is_first = true;

    loop {
        let server = match ServerOptions::new()
            .first_pipe_instance(is_first)
            .in_buffer_size(65536)
            .out_buffer_size(65536)
            .create(pipe_name)
        {
            Ok(s) => s,
            Err(err) => {
                error!(pipe = pipe_name, error = %err, "Failed to create named pipe instance");
                return Err(IpcTransportError::Io(err));
            }
        };

        is_first = false;

        debug!(
            pipe = pipe_name,
            "Waiting for client connection on named pipe"
        );

        tokio::select! {
            _ = cancel.cancelled() => {
                info!("Named pipe server accept loop cancelled");
                break;
            }
            conn_res = server.connect() => {
                match conn_res {
                    Ok(()) => {
                        info!("Client successfully connected to named pipe");
                        let client_dispatcher = dispatcher.clone();
                        let client_cancel = cancel.child_token();
                        tokio::spawn(async move {
                            if let Err(err) = handle_connection(server, client_dispatcher, client_cancel).await {
                                match err {
                                    IpcTransportError::Io(ref io_err)
                                        if io_err.kind() == std::io::ErrorKind::UnexpectedEof
                                            || io_err.kind() == std::io::ErrorKind::BrokenPipe =>
                                    {
                                        debug!("Client disconnected cleanly");
                                    }
                                    _ => {
                                        warn!(error = %err, "Connection handler closed with error");
                                    }
                                }
                            }
                        });
                    }
                    Err(err) => {
                        warn!(error = %err, "Failed to connect client to named pipe instance");
                    }
                }
            }
        }
    }

    info!("Windows Named Pipe server terminated cleanly");
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ipc::handlers::create_default_dispatcher;
    use crate::ENGINE_VERSION;
    use serde_json::json;
    use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};

    #[tokio::test]
    async fn test_request_response_round_trip() {
        let (client, server) = tokio::io::duplex(4096);
        let cancel = CancellationToken::new();
        let dispatcher = Arc::new(create_default_dispatcher());

        let server_cancel = cancel.child_token();
        let server_task = tokio::spawn(handle_connection(server, dispatcher, server_cancel));

        let (client_read, mut client_write) = tokio::io::split(client);
        let mut client_reader = BufReader::new(client_read);

        // Send valid request for engine.ping
        let req = IpcRequest::new("req_1", "engine.ping", json!({}));
        let mut req_bytes = serde_json::to_vec(&req).unwrap();
        req_bytes.push(b'\n');
        client_write.write_all(&req_bytes).await.unwrap();
        client_write.flush().await.unwrap();

        // Read response
        let mut resp_line = String::new();
        client_reader.read_line(&mut resp_line).await.unwrap();
        let resp: IpcResponse = serde_json::from_str(resp_line.trim()).unwrap();

        assert_eq!(resp.id, "req_1");
        assert_eq!(resp.version, 1);
        assert!(resp.success);
        assert!(resp.error.is_none());
        assert_eq!(resp.data.as_ref().unwrap()["pong"], true);
        assert_eq!(resp.data.unwrap()["version"], ENGINE_VERSION);

        cancel.cancel();
        let _ = server_task.await;
    }

    #[tokio::test]
    async fn test_multiple_requests_sequential() {
        let (client, server) = tokio::io::duplex(4096);
        let cancel = CancellationToken::new();
        let dispatcher = Arc::new(create_default_dispatcher());

        let server_cancel = cancel.child_token();
        let server_task = tokio::spawn(handle_connection(server, dispatcher, server_cancel));

        let (client_read, mut client_write) = tokio::io::split(client);
        let mut client_reader = BufReader::new(client_read);

        // Request 1: engine.ping
        let req1 = IpcRequest::new("req_1", "engine.ping", json!({}));
        let mut req1_bytes = serde_json::to_vec(&req1).unwrap();
        req1_bytes.push(b'\n');
        client_write.write_all(&req1_bytes).await.unwrap();
        client_write.flush().await.unwrap();

        let mut resp1_line = String::new();
        client_reader.read_line(&mut resp1_line).await.unwrap();
        let resp1: IpcResponse = serde_json::from_str(resp1_line.trim()).unwrap();
        assert_eq!(resp1.id, "req_1");
        assert!(resp1.success);
        assert_eq!(resp1.data.unwrap()["pong"], true);

        // Request 2: engine.status
        let req2 = IpcRequest::new("req_2", "engine.status", json!({}));
        let mut req2_bytes = serde_json::to_vec(&req2).unwrap();
        req2_bytes.push(b'\n');
        client_write.write_all(&req2_bytes).await.unwrap();
        client_write.flush().await.unwrap();

        let mut resp2_line = String::new();
        client_reader.read_line(&mut resp2_line).await.unwrap();
        let resp2: IpcResponse = serde_json::from_str(resp2_line.trim()).unwrap();
        assert_eq!(resp2.id, "req_2");
        assert!(resp2.success);
        assert_eq!(resp2.data.unwrap()["status"], "running");

        // Request 3: unknown method -> NOT_FOUND
        let req3 = IpcRequest::new("req_3", "unknown.op", json!({}));
        let mut req3_bytes = serde_json::to_vec(&req3).unwrap();
        req3_bytes.push(b'\n');
        client_write.write_all(&req3_bytes).await.unwrap();
        client_write.flush().await.unwrap();

        let mut resp3_line = String::new();
        client_reader.read_line(&mut resp3_line).await.unwrap();
        let resp3: IpcResponse = serde_json::from_str(resp3_line.trim()).unwrap();
        assert_eq!(resp3.id, "req_3");
        assert!(!resp3.success);
        assert_eq!(resp3.error.unwrap().code, error_code::NOT_FOUND);

        cancel.cancel();
        let _ = server_task.await;
    }

    #[tokio::test]
    async fn test_clean_disconnect() {
        let (client, server) = tokio::io::duplex(4096);
        let cancel = CancellationToken::new();
        let dispatcher = Arc::new(create_default_dispatcher());

        let server_task = tokio::spawn(handle_connection(server, dispatcher, cancel.child_token()));

        // Drop client stream immediately to simulate clean EOF
        drop(client);

        let result = server_task.await.unwrap();
        assert!(result.is_ok(), "handler should terminate cleanly on EOF");
    }

    #[tokio::test]
    async fn test_cancellation_terminates_promptly() {
        let (_client, server) = tokio::io::duplex(4096);
        let cancel = CancellationToken::new();
        let dispatcher = Arc::new(create_default_dispatcher());

        let server_cancel = cancel.child_token();
        let server_task = tokio::spawn(handle_connection(server, dispatcher, server_cancel));

        tokio::time::sleep(Duration::from_millis(20)).await;
        cancel.cancel();

        let res = tokio::time::timeout(Duration::from_millis(200), server_task).await;
        assert!(res.is_ok(), "server should terminate promptly on cancel");
        assert!(res.unwrap().unwrap().is_ok());
    }

    #[tokio::test]
    async fn test_malformed_json_handling() {
        let (client, server) = tokio::io::duplex(4096);
        let cancel = CancellationToken::new();
        let dispatcher = Arc::new(create_default_dispatcher());

        let server_task = tokio::spawn(handle_connection(server, dispatcher, cancel.child_token()));

        let (client_read, mut client_write) = tokio::io::split(client);
        let mut client_reader = BufReader::new(client_read);

        // Send malformed JSON line
        client_write
            .write_all(b"{\"id\": \"bad_1\", \"invalid_json\n")
            .await
            .unwrap();
        client_write.flush().await.unwrap();

        let mut resp_line = String::new();
        client_reader.read_line(&mut resp_line).await.unwrap();
        let resp: IpcResponse = serde_json::from_str(resp_line.trim()).unwrap();

        assert!(!resp.success);
        assert_eq!(resp.error.unwrap().code, error_code::INVALID_REQUEST);

        cancel.cancel();
        let _ = server_task.await;
    }

    #[tokio::test]
    async fn test_oversized_frame_rejected() {
        let (client, server) = tokio::io::duplex(4096);
        let cancel = CancellationToken::new();
        let dispatcher = Arc::new(create_default_dispatcher());

        let server_task = tokio::spawn(handle_connection(server, dispatcher, cancel.child_token()));

        let (client_read, mut client_write) = tokio::io::split(client);
        let mut client_reader = BufReader::new(client_read);

        // Send a frame that exceeds MAX_FRAME_BYTES (1MB + 16 bytes without newline)
        let oversized = vec![b'a'; MAX_FRAME_BYTES + 16];
        client_write.write_all(&oversized).await.unwrap();
        client_write.flush().await.unwrap();

        let mut resp_line = String::new();
        client_reader.read_line(&mut resp_line).await.unwrap();
        let resp: IpcResponse = serde_json::from_str(resp_line.trim()).unwrap();

        assert!(!resp.success);
        let err = resp.error.unwrap();
        assert_eq!(err.code, error_code::INVALID_REQUEST);
        assert!(err.message.contains("exceeds maximum"));

        cancel.cancel();
        let server_res = server_task.await.unwrap();
        assert!(matches!(
            server_res,
            Err(IpcTransportError::FrameTooLarge { .. })
        ));
    }

    #[tokio::test]
    async fn test_version_mismatch_returns_error() {
        let (client, server) = tokio::io::duplex(4096);
        let cancel = CancellationToken::new();
        let dispatcher = Arc::new(create_default_dispatcher());

        let server_task = tokio::spawn(handle_connection(server, dispatcher, cancel.child_token()));

        let (client_read, mut client_write) = tokio::io::split(client);
        let mut client_reader = BufReader::new(client_read);

        let req = IpcRequest {
            id: "ver_test".into(),
            version: 999,
            method: "engine.ping".into(),
            params: json!({}),
        };
        let mut req_bytes = serde_json::to_vec(&req).unwrap();
        req_bytes.push(b'\n');
        client_write.write_all(&req_bytes).await.unwrap();
        client_write.flush().await.unwrap();

        let mut resp_line = String::new();
        client_reader.read_line(&mut resp_line).await.unwrap();
        let resp: IpcResponse = serde_json::from_str(resp_line.trim()).unwrap();

        assert_eq!(resp.id, "ver_test");
        assert!(!resp.success);
        assert_eq!(resp.error.unwrap().code, error_code::VERSION_MISMATCH);

        cancel.cancel();
        let _ = server_task.await;
    }

    #[cfg(windows)]
    #[tokio::test]
    async fn test_windows_named_pipe_live_roundtrip() {
        use tokio::net::windows::named_pipe::ClientOptions;

        // Unique pipe name for isolated test
        let test_pipe_name = format!(
            r"\\.\pipe\tom-engine-test-{}",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        );

        let cancel = CancellationToken::new();
        let server_cancel = cancel.child_token();
        let pipe_name_clone = test_pipe_name.clone();
        let dispatcher = Arc::new(create_default_dispatcher());

        let server_task = tokio::spawn(async move {
            run_named_pipe_server(&pipe_name_clone, dispatcher, server_cancel).await
        });

        // Give server a brief moment to create the pipe
        tokio::time::sleep(Duration::from_millis(50)).await;

        // Connect client
        let client = ClientOptions::new().open(&test_pipe_name).unwrap();
        let (client_read, mut client_write) = tokio::io::split(client);
        let mut client_reader = BufReader::new(client_read);

        // Send request for engine.status
        let req = IpcRequest::new("win_pipe_1", "engine.status", json!({}));
        let mut req_bytes = serde_json::to_vec(&req).unwrap();
        req_bytes.push(b'\n');
        client_write.write_all(&req_bytes).await.unwrap();
        client_write.flush().await.unwrap();

        // Read response
        let mut resp_line = String::new();
        client_reader.read_line(&mut resp_line).await.unwrap();
        let resp: IpcResponse = serde_json::from_str(resp_line.trim()).unwrap();

        assert_eq!(resp.id, "win_pipe_1");
        assert!(resp.success);
        assert_eq!(resp.data.as_ref().unwrap()["engine"], "tom-engine");
        assert_eq!(resp.data.unwrap()["status"], "running");

        cancel.cancel();
        let _ = server_task.await;
    }
}
