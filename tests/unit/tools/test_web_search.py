"""Focused tests for the deterministic local web.search tool."""

import asyncio
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from tom.schemas.config import ToolsConfig
from tom.security.permissions import PermissionEngine, PermissionLevel
from tom.tools.executor import ToolExecutor
from tom.tools.registry import ToolRegistry
from tom.tools.web import (
    SearchProviderError,
    SearchRequest,
    SearchResponse,
    SearXNGSearchProvider,
    create_web_tools,
)


def _provider_response(payload: dict[str, Any], *, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code=status,
        json=payload,
        request=httpx.Request("GET", "http://127.0.0.1:8080/search"),
    )


class TestSearchRequest:
    def test_valid_request_is_normalized(self) -> None:
        request = SearchRequest(query="  local AI tools  ")
        assert request.query == "local AI tools"

    def test_invalid_query_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SearchRequest(query="   ")

        with pytest.raises(ValidationError):
            SearchRequest(query="x" * 257)

    def test_unknown_fields_are_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SearchRequest.model_validate({"query": "AI", "unexpected": True})


class TestSearXNGSearchProvider:
    def test_successful_response_is_bounded_and_normalized(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params["q"] == "local AI tools"
            assert request.url.params["format"] == "json"
            return _provider_response(
                {
                    "results": [
                        {
                            "title": "TOM documentation",
                            "url": "https://example.com/tom",
                            "content": "Local documentation summary",
                            "engine": "example-engine",
                        }
                    ]
                }
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = SearXNGSearchProvider(
            client=client,
            endpoint="http://127.0.0.1:8080/search",
            max_response_bytes=1_000_000,
        )

        result = asyncio.run(provider.search("local AI tools", max_results=1))

        assert isinstance(result, SearchResponse)
        assert result.query == "local AI tools"
        assert result.count == 1
        assert result.results[0].url == "https://example.com/tom"
        assert result.results[0].summary == "Local documentation summary"
        assert result.results[0].source == "example-engine"
        assert result.provider == "searxng"

    def test_result_count_and_response_size_are_bounded(self) -> None:
        payload = {
            "results": [
                {
                    "title": f"Result {index}",
                    "url": f"https://example.com/{index}",
                    "content": f"Summary {index}",
                }
                for index in range(20)
            ]
        }
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: _provider_response(payload))
        )
        provider = SearXNGSearchProvider(
            client=client,
            endpoint="http://127.0.0.1:8080/search",
            max_response_bytes=1,
        )

        with pytest.raises(SearchProviderError, match="response exceeds"):
            asyncio.run(provider.search("test", max_results=10))

    def test_provider_failure_is_structured(self) -> None:
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: _provider_response({}, status=503)
            )
        )
        provider = SearXNGSearchProvider(
            client=client,
            endpoint="http://127.0.0.1:8080/search",
        )

        with pytest.raises(SearchProviderError, match="503"):
            asyncio.run(provider.search("test", max_results=10))


class TestWebSearchToolRegistration:
    def test_tool_is_registered_with_safe_permission_and_executor_path(self) -> None:
        config = ToolsConfig(
            web_search_endpoint="http://127.0.0.1:8080/search",
            web_search_max_response_bytes=1_000_000,
        )
        registry = ToolRegistry()
        response_payload = {
            "results": [
                {
                    "title": "Local result",
                    "url": "https://example.com/result",
                    "content": "Local summary",
                    "engine": "test-engine",
                }
            ]
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return _provider_response(response_payload)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = SearXNGSearchProvider(
            client=client,
            endpoint=config.web_search_endpoint,
            max_response_bytes=config.web_search_max_response_bytes,
        )
        try:
            definitions = create_web_tools(
                registry=registry,
                config=config,
                search_provider=provider,
            )
            definition = registry.get("web.search")

            assert len(definitions) == 2  # search + fetch
            assert definition.category == "web"
            assert definition.permission_level == PermissionLevel.SAFE
            assert definition.timeout_seconds <= config.default_timeout_seconds

            executor = ToolExecutor(
                registry=registry,
                permission_engine=PermissionEngine(),
                config=config,
            )
            result = asyncio.run(executor.execute("web.search", {"query": "local"}))
        finally:
            asyncio.run(client.aclose())

        assert result.success is True
        assert isinstance(result.data, SearchResponse)
        assert result.data.count == 1
        assert definition.permission_level == PermissionLevel.SAFE

    def test_tool_schema_is_exported_and_rejects_extra_fields(self) -> None:
        registry = ToolRegistry()
        create_web_tools(registry=registry)
        exported = registry.export_json_schemas(category="web")

        assert len(exported) == 2
        search_schema = next(s for s in exported if s["function"]["name"] == "web.search")
        assert search_schema["function"]["parameters"]["properties"]["query"]["minLength"] == 1
        assert search_schema["function"]["parameters"]["additionalProperties"] is False
