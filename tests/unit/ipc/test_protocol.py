"""Unit tests for IPC protocol models, wire framing constants, and schema validation."""

import json

import pytest
from pydantic import ValidationError
from tom.ipc.protocol import (
    MAX_FRAME_BYTES,
    PIPE_NAME,
    PROTOCOL_VERSION,
    ConnectionState,
    ErrorCode,
    IpcError,
    IpcRequest,
    IpcResponse,
    new_request_id,
)


class TestProtocolConstants:
    """Validate protocol constants match Rust engine specifications."""

    def test_protocol_version(self) -> None:
        assert PROTOCOL_VERSION == 1

    def test_pipe_name(self) -> None:
        assert PIPE_NAME == r"\\.\pipe\tom-engine"

    def test_max_frame_bytes(self) -> None:
        assert MAX_FRAME_BYTES == 1_048_576

    def test_error_codes_match_rust_exhaustive(self) -> None:
        expected_codes = {
            "NOT_FOUND",
            "INVALID_REQUEST",
            "INVALID_PARAMS",
            "TIMEOUT",
            "VERSION_MISMATCH",
            "INTERNAL",
            "NOT_AVAILABLE",
        }
        actual_codes = {item.value for item in ErrorCode}
        assert actual_codes == expected_codes
        assert len(ErrorCode) == 7

    def test_connection_state_enum(self) -> None:
        states = {item.value for item in ConnectionState}
        assert states == {
            "disconnected",
            "connecting",
            "connected",
            "reconnecting",
            "closed",
        }


class TestRequestId:
    """Validate correlation request ID generation."""

    def test_new_request_id_format(self) -> None:
        req_id = new_request_id()
        assert req_id.startswith("req_")
        assert len(req_id) == 16

    def test_new_request_id_uniqueness(self) -> None:
        ids = {new_request_id() for _ in range(1000)}
        assert len(ids) == 1000


class TestIpcRequest:
    """Validate IpcRequest serialization and validation constraints."""

    def test_valid_request_minimal(self) -> None:
        req = IpcRequest(id="req_001", method="engine.ping")
        assert req.id == "req_001"
        assert req.version == 1
        assert req.method == "engine.ping"
        assert req.params == {}

    def test_valid_request_with_params(self) -> None:
        req = IpcRequest(
            id="req_002",
            method="system.processes",
            params={"limit": 5},
        )
        assert req.params == {"limit": 5}

    def test_serialization_matches_rust_wire_format(self) -> None:
        req = IpcRequest(
            id="req_123",
            version=1,
            method="system.cpu",
            params={},
        )
        dumped = req.model_dump(mode="json")
        assert dumped == {
            "id": "req_123",
            "version": 1,
            "method": "system.cpu",
            "params": {},
        }
        raw_json = json.dumps(dumped)
        parsed = json.loads(raw_json)
        assert parsed["id"] == "req_123"
        assert parsed["version"] == 1
        assert parsed["method"] == "system.cpu"
        assert parsed["params"] == {}

    def test_reject_extra_fields(self) -> None:
        with pytest.raises(ValidationError):
            IpcRequest(
                id="req_1",
                method="engine.ping",
                extra_field="disallowed",  # type: ignore[call-arg]
            )

    def test_reject_empty_id(self) -> None:
        with pytest.raises(ValidationError):
            IpcRequest(id="", method="engine.ping")

    def test_reject_whitespace_id(self) -> None:
        with pytest.raises(ValidationError):
            IpcRequest(id="   ", method="engine.ping")

    def test_reject_empty_method(self) -> None:
        with pytest.raises(ValidationError):
            IpcRequest(id="req_1", method="")

    def test_reject_whitespace_method(self) -> None:
        with pytest.raises(ValidationError):
            IpcRequest(id="req_1", method="   \t\n")

    def test_reject_unsupported_version(self) -> None:
        with pytest.raises(ValidationError):
            IpcRequest(id="req_1", method="engine.ping", version=2)

        with pytest.raises(ValidationError):
            IpcRequest(id="req_1", method="engine.ping", version=0)


class TestIpcResponse:
    """Validate IpcResponse parsing and forward compatibility."""

    def test_success_response_deserialization(self) -> None:
        # Rust sends success response without 'error' key
        raw_wire_json = (
            '{"id": "req_123", "version": 1, "success": true, "data": {"usage_percent": 15.2}}'
        )
        resp = IpcResponse.model_validate_json(raw_wire_json)
        assert resp.id == "req_123"
        assert resp.version == 1
        assert resp.success is True
        assert resp.data == {"usage_percent": 15.2}
        assert resp.error is None

    def test_error_response_deserialization(self) -> None:
        # Rust sends error response without 'data' key
        raw_wire_json = (
            '{"id": "req_123", "version": 1, "success": false, '
            '"error": {"code": "NOT_FOUND", "message": "Method \'unknown\' not registered"}}'
        )
        resp = IpcResponse.model_validate_json(raw_wire_json)
        assert resp.id == "req_123"
        assert resp.version == 1
        assert resp.success is False
        assert resp.data is None
        assert resp.error is not None
        assert resp.error.code == "NOT_FOUND"
        assert resp.error.message == "Method 'unknown' not registered"

    def test_forward_compatibility_ignores_extra_fields(self) -> None:
        # If Rust engine adds future metadata fields, Python must not fail
        raw_wire_json = (
            '{"id": "req_123", "version": 1, "success": true, "data": {}, '
            '"future_metric": 42, "engine_node": "primary"}'
        )
        resp = IpcResponse.model_validate_json(raw_wire_json)
        assert resp.id == "req_123"
        assert resp.success is True

    def test_ipc_error_ignores_extra_fields(self) -> None:
        err = IpcError.model_validate(
            {"code": "INTERNAL", "message": "error msg", "extra_trace": "ignored"}
        )
        assert err.code == "INTERNAL"
        assert err.message == "error msg"
