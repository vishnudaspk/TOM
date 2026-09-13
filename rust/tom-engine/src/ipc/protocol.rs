//! # IPC Protocol Schema
//!
//! Strongly typed, versioned IPC protocol structures for bidirectional communication
//! between TOM's Python brain and the Rust engine (`tom-engine`).
//!
//! Follows the protocol guidelines from `skills/rust/ipc/SKILL.md` and
//! `skills/system-design/python-rust-boundary/SKILL.md`.

use serde::{Deserialize, Serialize};

/// Current active protocol schema version.
pub const CURRENT_PROTOCOL_VERSION: u32 = 1;

/// Standard error codes used across the IPC boundary.
pub mod error_code {
    pub const NOT_FOUND: &str = "NOT_FOUND";
    pub const INVALID_REQUEST: &str = "INVALID_REQUEST";
    pub const INVALID_PARAMS: &str = "INVALID_PARAMS";
    pub const TIMEOUT: &str = "TIMEOUT";
    pub const VERSION_MISMATCH: &str = "VERSION_MISMATCH";
    pub const INTERNAL: &str = "INTERNAL";
    pub const NOT_AVAILABLE: &str = "NOT_AVAILABLE";
}

/// Structured IPC error object.
///
/// Contains a standardized error code and a human-readable message.
/// Avoids leaking internal Rust-specific types or backtraces across the IPC boundary.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct IpcError {
    pub code: String,
    pub message: String,
}

impl IpcError {
    /// Create a new `IpcError`.
    pub fn new(code: impl Into<String>, message: impl Into<String>) -> Self {
        Self {
            code: code.into(),
            message: message.into(),
        }
    }
}

/// Inbound IPC request payload (Python → Rust).
///
/// Represents an operation invocation with correlation ID, version, method name,
/// and parameters.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct IpcRequest {
    pub id: String,
    pub version: u32,
    pub method: String,
    pub params: serde_json::Value,
}

impl IpcRequest {
    /// Construct a new `IpcRequest` defaulting to `CURRENT_PROTOCOL_VERSION`.
    pub fn new(
        id: impl Into<String>,
        method: impl Into<String>,
        params: serde_json::Value,
    ) -> Self {
        Self {
            id: id.into(),
            version: CURRENT_PROTOCOL_VERSION,
            method: method.into(),
            params,
        }
    }

    /// Validate the request against protocol rules.
    ///
    /// Rejects:
    /// - Version mismatches (`VERSION_MISMATCH`)
    /// - Empty/whitespace-only request ID (`INVALID_REQUEST`)
    /// - Empty/whitespace-only method name (`INVALID_REQUEST`)
    pub fn validate(&self) -> Result<(), IpcError> {
        if self.version != CURRENT_PROTOCOL_VERSION {
            return Err(IpcError::new(
                error_code::VERSION_MISMATCH,
                format!(
                    "unsupported protocol version {}, expected {}",
                    self.version, CURRENT_PROTOCOL_VERSION
                ),
            ));
        }

        if self.id.trim().is_empty() {
            return Err(IpcError::new(
                error_code::INVALID_REQUEST,
                "request id must not be empty",
            ));
        }

        if self.method.trim().is_empty() {
            return Err(IpcError::new(
                error_code::INVALID_REQUEST,
                "request method must not be empty",
            ));
        }

        Ok(())
    }
}

/// Outbound IPC response payload (Rust → Python).
///
/// Corresponds to an `IpcRequest` via `id`.
/// On success: `success = true`, `data` contains payload, `error` is omitted.
/// On error: `success = false`, `error` contains details, `data` is omitted.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct IpcResponse {
    pub id: String,
    pub version: u32,
    pub success: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub data: Option<serde_json::Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<IpcError>,
}

impl IpcResponse {
    /// Create a successful response with payload data.
    pub fn ok(id: impl Into<String>, data: serde_json::Value) -> Self {
        Self {
            id: id.into(),
            version: CURRENT_PROTOCOL_VERSION,
            success: true,
            data: Some(data),
            error: None,
        }
    }

    /// Create an error response with an error code and descriptive message.
    pub fn error(
        id: impl Into<String>,
        code: impl Into<String>,
        message: impl Into<String>,
    ) -> Self {
        Self {
            id: id.into(),
            version: CURRENT_PROTOCOL_VERSION,
            success: false,
            data: None,
            error: Some(IpcError::new(code, message)),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn test_valid_request_deserialization() {
        let json_str = r#"{
            "id": "req_001",
            "version": 1,
            "method": "system.cpu_usage",
            "params": {"interval_ms": 100}
        }"#;

        let req: IpcRequest = serde_json::from_str(json_str).expect("should deserialize");
        assert_eq!(req.id, "req_001");
        assert_eq!(req.version, 1);
        assert_eq!(req.method, "system.cpu_usage");
        assert_eq!(req.params["interval_ms"], 100);

        assert!(req.validate().is_ok());
    }

    #[test]
    fn test_success_response_serialization() {
        let data = json!({
            "usage_percent": 42.5,
            "cores": 8
        });
        let resp = IpcResponse::ok("req_001", data);

        let serialized = serde_json::to_string(&resp).expect("should serialize");
        let val: serde_json::Value = serde_json::from_str(&serialized).expect("should parse json");

        assert_eq!(val["id"], "req_001");
        assert_eq!(val["version"], CURRENT_PROTOCOL_VERSION);
        assert_eq!(val["success"], true);
        assert_eq!(val["data"]["usage_percent"], 42.5);
        assert_eq!(val["data"]["cores"], 8);

        // Ensure error field is omitted, not null
        assert!(val.get("error").is_none());
    }

    #[test]
    fn test_error_response_serialization() {
        let resp = IpcResponse::error(
            "req_002",
            error_code::NOT_AVAILABLE,
            "GPU sensor unavailable",
        );

        let serialized = serde_json::to_string(&resp).expect("should serialize");
        let val: serde_json::Value = serde_json::from_str(&serialized).expect("should parse json");

        assert_eq!(val["id"], "req_002");
        assert_eq!(val["version"], CURRENT_PROTOCOL_VERSION);
        assert_eq!(val["success"], false);
        assert_eq!(val["error"]["code"], "NOT_AVAILABLE");
        assert_eq!(val["error"]["message"], "GPU sensor unavailable");

        // Ensure data field is omitted, not null
        assert!(val.get("data").is_none());
    }

    #[test]
    fn test_version_mismatch_validation() {
        let req = IpcRequest {
            id: "req_003".into(),
            version: 999,
            method: "system.memory".into(),
            params: json!({}),
        };

        let err = req
            .validate()
            .expect_err("should reject unsupported version");
        assert_eq!(err.code, error_code::VERSION_MISMATCH);
        assert!(err.message.contains("999"));
    }

    #[test]
    fn test_empty_id_validation() {
        let req = IpcRequest::new("   ", "system.memory", json!({}));
        let err = req.validate().expect_err("should reject empty id");
        assert_eq!(err.code, error_code::INVALID_REQUEST);
        assert!(err.message.contains("id"));
    }

    #[test]
    fn test_empty_method_validation() {
        let req = IpcRequest::new("req_004", "   ", json!({}));
        let err = req.validate().expect_err("should reject empty method");
        assert_eq!(err.code, error_code::INVALID_REQUEST);
        assert!(err.message.contains("method"));
    }

    #[test]
    fn test_malformed_json_deserialization() {
        let malformed = r#"{"id": "req_005", "version": "not_a_number"}"#;
        let res: Result<IpcRequest, _> = serde_json::from_str(malformed);
        assert!(res.is_err());
    }

    #[test]
    fn test_error_code_constants() {
        assert_eq!(error_code::NOT_FOUND, "NOT_FOUND");
        assert_eq!(error_code::INVALID_REQUEST, "INVALID_REQUEST");
        assert_eq!(error_code::INVALID_PARAMS, "INVALID_PARAMS");
        assert_eq!(error_code::TIMEOUT, "TIMEOUT");
        assert_eq!(error_code::VERSION_MISMATCH, "VERSION_MISMATCH");
        assert_eq!(error_code::INTERNAL, "INTERNAL");
        assert_eq!(error_code::NOT_AVAILABLE, "NOT_AVAILABLE");
    }

    #[test]
    fn test_request_roundtrip() {
        let original = IpcRequest::new("req_100", "audio.devices", json!({"type": "input"}));
        let json_str = serde_json::to_string(&original).expect("serialize");
        let parsed: IpcRequest = serde_json::from_str(&json_str).expect("deserialize");
        assert_eq!(original, parsed);
    }

    #[test]
    fn test_response_roundtrip() {
        let ok_resp = IpcResponse::ok("req_101", json!({"status": "ready"}));
        let ok_str = serde_json::to_string(&ok_resp).expect("serialize ok");
        let parsed_ok: IpcResponse = serde_json::from_str(&ok_str).expect("deserialize ok");
        assert_eq!(ok_resp, parsed_ok);

        let err_resp = IpcResponse::error("req_102", error_code::TIMEOUT, "operation timed out");
        let err_str = serde_json::to_string(&err_resp).expect("serialize err");
        let parsed_err: IpcResponse = serde_json::from_str(&err_str).expect("deserialize err");
        assert_eq!(err_resp, parsed_err);
    }
}
