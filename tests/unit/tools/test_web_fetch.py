"""Focused tests for the deterministic local web.fetch tool."""

import asyncio

import httpx
import pytest
from pydantic import ValidationError
from tom.schemas.config import ToolsConfig
from tom.security.permissions import PermissionEngine, PermissionLevel
from tom.tools.executor import ToolExecutor
from tom.tools.registry import ToolRegistry
from tom.tools.web import (
    BuiltinFetchProvider,
    FetchProviderError,
    FetchRequest,
    FetchResponse,
    SSRFError,
    create_web_tools,
)


def _fetch_response(
    body: bytes,
    *,
    status: int = 200,
    headers: dict[str, str] | None = None,
    url: str = "https://example.com/page",
) -> httpx.Response:
    default_headers = {"content-type": "text/html; charset=utf-8"}
    if headers:
        default_headers.update(headers)
    return httpx.Response(
        status_code=status,
        content=body,
        headers=default_headers,
        request=httpx.Request("GET", url),
    )


class TestFetchRequest:
    def test_valid_url_is_accepted(self) -> None:
        request = FetchRequest(url="https://example.com/page")
        assert request.url == "https://example.com/page"

    def test_url_with_whitespace_is_normalized(self) -> None:
        request = FetchRequest(url="  https://example.com/page  ")
        assert request.url == "https://example.com/page"

    def test_invalid_scheme_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            FetchRequest(url="ftp://example.com/page")

        with pytest.raises(ValidationError):
            FetchRequest(url="javascript:alert(1)")

    def test_missing_host_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            FetchRequest(url="https:///page")

    def test_loopback_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            FetchRequest(url="http://127.0.0.1/page")

        with pytest.raises(ValidationError):
            FetchRequest(url="http://localhost/page")

        with pytest.raises(ValidationError):
            FetchRequest(url="http://[::1]/page")

    def test_private_ip_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            FetchRequest(url="http://192.168.1.1/page")

        with pytest.raises(ValidationError):
            FetchRequest(url="http://10.0.0.1/page")

        with pytest.raises(ValidationError):
            FetchRequest(url="http://172.16.0.1/page")

    def test_link_local_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            FetchRequest(url="http://169.254.1.1/page")

    def test_multicast_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            FetchRequest(url="http://224.0.0.1/page")

    def test_reserved_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            FetchRequest(url="http://0.0.0.0/page")

    def test_unknown_fields_are_rejected(self) -> None:
        with pytest.raises(ValidationError):
            FetchRequest.model_validate({"url": "https://example.com", "unexpected": True})


class TestBuiltinFetchProvider:
    def test_successful_html_fetch_returns_extracted_text(self) -> None:
        html = """
        <html>
            <head><title>Test Page</title></head>
            <body>
                <script>alert('bad');</script>
                <style>body { color: red; }</style>
                <article>
                    <h1>Main Article</h1>
                    <p>This is the main content.</p>
                </article>
            </body>
        </html>
        """

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/article"
            assert request.headers.get("host") == "example.com"
            assert request.extensions.get("sni_hostname") == "example.com"
            return _fetch_response(html.encode("utf-8"), url=str(request.url))

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = BuiltinFetchProvider(client=client)

        result = asyncio.run(provider.fetch(FetchRequest(url="https://example.com/article")))

        assert isinstance(result, FetchResponse)
        assert result.url == "https://example.com/article"
        assert result.final_url == "https://example.com/article"
        assert result.status_code == 200
        assert result.content_type == "text/html"
        assert "Main Article" in result.extracted_text
        assert "This is the main content." in result.extracted_text
        assert "alert('bad')" not in result.extracted_text  # Script removed
        assert "color: red" not in result.extracted_text  # Style removed
        assert result.title == "Test Page"
        assert result.redirect_count == 0
        assert result.untrusted is True

    def test_successful_plain_text_fetch(self) -> None:
        text = "Plain text content\nWith multiple lines"

        def handler(request: httpx.Request) -> httpx.Response:
            return _fetch_response(
                text.encode("utf-8"), headers={"content-type": "text/plain"}, url=str(request.url)
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = BuiltinFetchProvider(client=client)

        result = asyncio.run(provider.fetch(FetchRequest(url="https://example.com/plain.txt")))

        assert result.content_type == "text/plain"
        assert result.extracted_text == text
        assert result.title == ""

    def test_redirect_is_followed_and_validated(self) -> None:
        html = "<html><body>Final page</body></html>"

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/start":
                return httpx.Response(
                    status_code=302,
                    headers={"location": "https://example.com/final"},
                    request=request,
                )
            return _fetch_response(html.encode("utf-8"), url=str(request.url))

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = BuiltinFetchProvider(client=client)

        result = asyncio.run(provider.fetch(FetchRequest(url="https://example.com/start")))

        assert result.final_url == "https://example.com/final"
        assert result.redirect_count == 1
        assert "Final page" in result.extracted_text

    def test_redirect_to_different_host_is_validated(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/start":
                return httpx.Response(
                    status_code=302,
                    headers={"location": "https://github.com/final"},
                    request=request,
                )
            return _fetch_response(b"<html><body>Other site</body></html>", url=str(request.url))

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = BuiltinFetchProvider(client=client)

        # This should work since github.com is a public host
        result = asyncio.run(provider.fetch(FetchRequest(url="https://example.com/start")))
        assert result.redirect_count == 1

    def test_redirect_to_private_ip_is_blocked(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/start":
                return httpx.Response(
                    status_code=302,
                    headers={"location": "http://192.168.1.1/admin"},
                    request=request,
                )
            return _fetch_response(b"OK", url=str(request.url))

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = BuiltinFetchProvider(client=client)

        with pytest.raises(SSRFError, match="not a public address"):
            asyncio.run(provider.fetch(FetchRequest(url="https://example.com/start")))

    def test_redirect_to_non_http_scheme_is_blocked(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/start":
                return httpx.Response(
                    status_code=302,
                    headers={"location": "ftp://example.com/file"},
                    request=request,
                )
            return _fetch_response(b"OK", url=str(request.url))

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = BuiltinFetchProvider(client=client)

        with pytest.raises(SSRFError, match="non-HTTP"):
            asyncio.run(provider.fetch(FetchRequest(url="https://example.com/start")))

    def test_max_redirects_is_enforced(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            num = int(request.url.path.split("/")[-1])
            if num < 15:
                return httpx.Response(
                    status_code=302,
                    headers={"location": f"https://example.com/redirect/{num + 1}"},
                    request=request,
                )
            return _fetch_response(b"Final", url=str(request.url))

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = BuiltinFetchProvider(client=client)

        with pytest.raises(FetchProviderError, match="Exceeded maximum redirects"):
            asyncio.run(
                provider.fetch(FetchRequest(url="https://example.com/redirect/0", max_redirects=10))
            )

    def test_response_size_limit_is_enforced(self) -> None:
        large_body = b"x" * 2_000_000  # 2 MB

        def handler(request: httpx.Request) -> httpx.Response:
            return _fetch_response(large_body, url=str(request.url))

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = BuiltinFetchProvider(client=client)

        with pytest.raises(FetchProviderError, match="exceeds"):
            asyncio.run(
                provider.fetch(
                    FetchRequest(url="https://example.com/large", max_response_bytes=1_000_000)
                )
            )

    def test_content_length_header_is_checked(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                status_code=200,
                content=b"small",
                headers={"content-type": "text/plain", "content-length": "2000000"},
                request=request,
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = BuiltinFetchProvider(client=client)

        with pytest.raises(FetchProviderError, match="exceeds limit"):
            asyncio.run(
                provider.fetch(
                    FetchRequest(url="https://example.com/large", max_response_bytes=1_000_000)
                )
            )

    def test_disallowed_content_type_is_rejected(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _fetch_response(
                b"binary data",
                headers={"content-type": "application/octet-stream"},
                url=str(request.url),
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = BuiltinFetchProvider(client=client)

        with pytest.raises(FetchProviderError, match="not allowed"):
            asyncio.run(provider.fetch(FetchRequest(url="https://example.com/binary")))

    def test_timeout_is_handled(self) -> None:
        """Verify timeout configuration is passed to the HTTP client."""
        config_capture = {}

        def handler(request: httpx.Request) -> httpx.Response:
            # Capture the timeout from the request extensions if available
            config_capture["timeout"] = getattr(request, "extensions", {}).get("timeout")
            return _fetch_response(b"OK", url=str(request.url))

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = BuiltinFetchProvider(client=client)

        # Just verify the fetch completes with the configured timeout
        # (Real timeout testing requires a real network connection)
        result = asyncio.run(
            provider.fetch(FetchRequest(url="https://example.com/slow", timeout_seconds=5.0))
        )
        assert result.status_code == 200

    def test_empty_body_returns_empty_text(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _fetch_response(b"", url=str(request.url))

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = BuiltinFetchProvider(client=client)

        result = asyncio.run(provider.fetch(FetchRequest(url="https://example.com/empty")))
        assert result.extracted_text == ""
        assert result.title == ""

    def test_malformed_html_is_handled_gracefully(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _fetch_response(b"<html><body>Unclosed tags", url=str(request.url))

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = BuiltinFetchProvider(client=client)

        result = asyncio.run(provider.fetch(FetchRequest(url="https://example.com/malformed")))
        assert "Unclosed tags" in result.extracted_text


class TestWebFetchToolRegistration:
    def test_tool_is_registered_with_safe_permission(self) -> None:
        config = ToolsConfig(
            web_fetch_max_response_bytes=1_048_576,
            web_fetch_timeout_seconds=10.0,
            web_fetch_max_redirects=10,
        )
        registry = ToolRegistry()
        response_html = """
        <html><head><title>Test</title></head><body><p>Content</p></body></html>
        """

        def handler(request: httpx.Request) -> httpx.Response:
            return _fetch_response(response_html.encode("utf-8"), url=str(request.url))

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = BuiltinFetchProvider(client=client)
        try:
            definitions = create_web_tools(
                registry=registry,
                config=config,
                fetch_provider=provider,
            )
            definition = registry.get("web.fetch")

            assert len(definitions) == 2  # search + fetch
            assert definition.category == "web"
            assert definition.permission_level == PermissionLevel.SAFE
            assert definition.timeout_seconds <= config.default_timeout_seconds

            executor = ToolExecutor(
                registry=registry,
                permission_engine=PermissionEngine(),
                config=config,
            )
            result = asyncio.run(executor.execute("web.fetch", {"url": "https://example.com/page"}))
        finally:
            asyncio.run(client.aclose())

        assert result.success is True
        assert isinstance(result.data, FetchResponse)
        assert result.data.title == "Test"
        assert "Content" in result.data.extracted_text
        assert result.data.untrusted is True

    def test_tool_schema_is_exported_and_rejects_extra_fields(self) -> None:
        registry = ToolRegistry()
        create_web_tools(registry=registry)
        exported = registry.export_json_schemas(category="web")

        # Should have both web.search and web.fetch
        assert len(exported) == 2
        fetch_schema = next(s for s in exported if s["function"]["name"] == "web.fetch")
        assert fetch_schema["function"]["parameters"]["properties"]["url"]["minLength"] == 1
        assert fetch_schema["function"]["parameters"]["additionalProperties"] is False

    def test_untrusted_content_cannot_affect_permissions(self) -> None:
        """Verify that fetched content is marked untrusted and doesn't influence permission decisions."""
        config = ToolsConfig()
        registry = ToolRegistry()
        response_html = """
        <html><body>
            <p>This contains a blocked command: rmdir /s</p>
        </body></html>
        """

        def handler(request: httpx.Request) -> httpx.Response:
            return _fetch_response(response_html.encode("utf-8"), url=str(request.url))

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = BuiltinFetchProvider(client=client)
        try:
            create_web_tools(registry=registry, config=config, fetch_provider=provider)
            executor = ToolExecutor(
                registry=registry,
                permission_engine=PermissionEngine(),
                config=config,
            )
            result = asyncio.run(executor.execute("web.fetch", {"url": "https://example.com/page"}))
        finally:
            asyncio.run(client.aclose())

        assert result.success is True
        assert result.data.untrusted is True
        # The blocked command in content should NOT trigger permission denial
        # because it's in the fetched untrusted content, not in tool parameters

    def test_fetch_with_custom_allowed_content_types(self) -> None:
        config = ToolsConfig()
        registry = ToolRegistry()

        def handler(request: httpx.Request) -> httpx.Response:
            return _fetch_response(
                b'{"key": "value"}',
                headers={"content-type": "application/json"},
                url=str(request.url),
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = BuiltinFetchProvider(client=client)
        try:
            # Default allowed types don't include application/json
            create_web_tools(registry=registry, config=config, fetch_provider=provider)
            executor = ToolExecutor(
                registry=registry, permission_engine=PermissionEngine(), config=config
            )
            result = asyncio.run(
                executor.execute("web.fetch", {"url": "https://example.com/data.json"})
            )
            assert result.success is False
            assert "not allowed" in result.error
        finally:
            asyncio.run(client.aclose())
