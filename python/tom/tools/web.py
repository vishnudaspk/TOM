"""Bounded deterministic web search and fetch tools.

This module provides:
- web.search: Search a configured local/self-hosted SearXNG endpoint (SAFE)
- web.fetch: Retrieve and extract public URLs with SSRF protection (SAFE, read-only)

Returned URLs from search are metadata only. Fetched content is untrusted data
and must be sanitized, bounded, and isolated from tool authorization.
"""

from __future__ import annotations

import ipaddress
import json
import socket
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict, Field, field_validator

from tom.schemas.config import ToolsConfig
from tom.security.permissions import PermissionLevel
from tom.tools.registry import ToolDefinition, ToolRegistry, default_registry


class SearchRequest(BaseModel):
    """Validated input for the deterministic `web.search` tool."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        min_length=1,
        max_length=256,
        description="Non-empty search query; values are normalized before execution",
    )
    max_results: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Maximum number of results to return",
    )

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > 256:
            raise ValueError("query must be 1-256 characters after trimming")
        return normalized


class SearchResult(BaseModel):
    """A single bounded, untrusted search result."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=500)
    url: str = Field(min_length=1, max_length=2048)
    summary: str = Field(default="", max_length=2000)
    source: str = Field(default="", max_length=255)
    provider: str = Field(default="searxng", max_length=255)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        url = httpx.URL(value)
        if url.scheme not in {"http", "https"} or not url.host:
            raise ValueError("Search result URL must be an HTTP or HTTPS URL")
        return str(url)


class SearchResponse(BaseModel):
    """Structured response returned by the local search adapter."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=256)
    count: int = Field(ge=0)
    results: list[SearchResult] = Field(default_factory=list, max_length=100)
    provider: str = Field(default="searxng", max_length=255)


# =============================================================================
# Web Fetch Tool (Iteration 2)
# =============================================================================


class FetchRequest(BaseModel):
    """Validated input for the deterministic `web.fetch` tool."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(
        min_length=1,
        max_length=2048,
        description="HTTP/HTTPS URL to fetch; validated for public address",
    )
    max_response_bytes: int = Field(
        default=1_048_576,
        ge=1,
        le=10_485_760,
        description="Maximum response body size in bytes (default 1 MiB, max 10 MiB)",
    )
    timeout_seconds: float = Field(
        default=10.0,
        gt=0.0,
        le=30.0,
        description="Total request timeout in seconds",
    )
    max_redirects: int = Field(
        default=10,
        ge=0,
        le=20,
        description="Maximum number of redirects to follow",
    )
    allowed_content_types: list[str] = Field(
        default_factory=lambda: [
            "text/html",
            "text/plain",
            "text/markdown",
            "application/xhtml+xml",
            "application/xml",
        ],
        description="Allowed MIME types for extraction",
    )

    @field_validator("url")
    @classmethod
    def validate_and_normalize_url(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("URL cannot be empty")
        if len(normalized) > 2048:
            raise ValueError("URL exceeds maximum length of 2048 characters")

        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("URL must use HTTP or HTTPS scheme")
        if not parsed.netloc:
            raise ValueError("URL must have a valid host")

        # Validate host is public (no loopback, private, link-local, multicast, reserved)
        host = parsed.hostname or ""
        if not cls._is_public_host(host):
            raise ValueError(f"Host '{host}' is not a public address")

        return normalized

    @staticmethod
    def _is_public_host(host: str) -> bool:
        """Check if host resolves to a public IP address."""
        try:
            # Handle IPv6 addresses in brackets
            if host.startswith("[") and host.endswith("]"):
                host = host[1:-1]

            # Try to parse as IP address directly
            ip = ipaddress.ip_address(host)
            return (
                not ip.is_loopback
                and not ip.is_private
                and not ip.is_link_local
                and not ip.is_multicast
                and not ip.is_reserved
                and not ip.is_unspecified
            )
        except ValueError:
            # Host is a domain name, resolve it
            try:
                infos = socket.getaddrinfo(host, None)
                for info in infos:
                    ip_str = info[4][0]
                    ip = ipaddress.ip_address(ip_str)
                    if (
                        ip.is_loopback
                        or ip.is_private
                        or ip.is_link_local
                        or ip.is_multicast
                        or ip.is_reserved
                        or ip.is_unspecified
                    ):
                        return False
                return True
            except (socket.gaierror, ValueError):
                return False


class FetchResponse(BaseModel):
    """Structured response returned by the web fetch tool."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=2048)
    final_url: str = Field(min_length=1, max_length=2048)
    status_code: int = Field(ge=100, le=599)
    content_type: str = Field(default="", max_length=255)
    content_length: int = Field(default=0, ge=0)
    extracted_text: str = Field(default="", max_length=1_000_000)
    title: str = Field(default="", max_length=500)
    redirect_count: int = Field(default=0, ge=0)
    provider: str = Field(default="builtin", max_length=255)
    untrusted: bool = Field(default=True, description="Content is untrusted and must not influence permissions")


class FetchProviderError(RuntimeError):
    """Raised when the fetch provider cannot produce a bounded response."""


class SSRFError(FetchProviderError):
    """Raised when SSRF protection blocks a request."""


class FetchProvider:
    """Fetch-provider interface used by the tool handler."""

    async def fetch(self, request: FetchRequest) -> FetchResponse:
        raise NotImplementedError


class BuiltinFetchProvider(FetchProvider):
    """Built-in fetch provider with SSRF protection, redirect validation, and content extraction."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        max_response_bytes: int = 1_048_576,
        default_timeout_seconds: float = 10.0,
        default_max_redirects: int = 10,
    ) -> None:
        self._client = client
        self._default_max_response_bytes = max_response_bytes
        self._default_timeout_seconds = default_timeout_seconds
        self._default_max_redirects = default_max_redirects

    async def fetch(self, request: FetchRequest) -> FetchResponse:
        # Validate URL again at fetch time (defense in depth)
        if not FetchRequest._is_public_host(urlparse(request.url).hostname or ""):
            raise SSRFError(f"Host '{urlparse(request.url).hostname}' is not a public address")

        # Create client with timeout if not provided
        if self._client is not None:
            client = self._client
        else:
            client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=3.0,
                    read=request.timeout_seconds,
                    write=3.0,
                    pool=3.0,
                ),
                follow_redirects=False,  # We handle redirects manually
                max_redirects=0,
            )

        try:
            return await self._fetch_with_redirects(client, request)
        finally:
            if self._client is None:
                await client.aclose()

    async def _fetch_with_redirects(self, client: httpx.AsyncClient, request: FetchRequest) -> FetchResponse:
        current_url = request.url
        redirect_count = 0
        original_host = urlparse(request.url).hostname or ""

        while redirect_count <= request.max_redirects:
            # Validate each redirect destination
            parsed = urlparse(current_url)
            if parsed.scheme not in {"http", "https"}:
                raise SSRFError(f"Redirect to non-HTTP(S) scheme: {parsed.scheme}")

            # Check DNS rebinding - validate host matches original or is public
            current_host = parsed.hostname or ""
            if current_host != original_host:
                if not FetchRequest._is_public_host(current_host):
                    raise SSRFError(f"Redirect host '{current_host}' is not a public address")

            # Make request with size limit
            try:
                response = await client.get(
                    current_url,
                    timeout=httpx.Timeout(
                        connect=3.0,
                        read=request.timeout_seconds,
                        write=3.0,
                        pool=3.0,
                    ),
                )
            except httpx.TimeoutException as exc:
                raise FetchProviderError(f"Fetch timed out after {request.timeout_seconds}s") from exc
            except httpx.HTTPError as exc:
                raise FetchProviderError(f"Fetch failed: {exc}") from exc

            # Check response size
            content_length = response.headers.get("content-length")
            if content_length and int(content_length) > request.max_response_bytes:
                raise FetchProviderError(f"Response size {content_length} exceeds limit {request.max_response_bytes}")

            # Read body with size limit
            body = b""
            async for chunk in response.aiter_bytes():
                body += chunk
                if len(body) > request.max_response_bytes:
                    raise FetchProviderError(f"Response body exceeds {request.max_response_bytes} bytes")

            # Check content type
            content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
            if content_type and content_type not in request.allowed_content_types:
                raise FetchProviderError(f"Content type '{content_type}' not allowed")

            # Handle redirects manually
            if 300 <= response.status_code < 400 and "location" in response.headers:
                redirect_count += 1
                if redirect_count > request.max_redirects:
                    raise FetchProviderError(f"Exceeded maximum redirects ({request.max_redirects})")

                location = response.headers["location"]
                current_url = urljoin(current_url, location)
                continue

            # Extract content
            extracted_text, title = self._extract_content(body, content_type)

            return FetchResponse(
                url=request.url,
                final_url=str(response.url),
                status_code=response.status_code,
                content_type=content_type,
                content_length=len(body),
                extracted_text=extracted_text,
                title=title,
                redirect_count=redirect_count,
            )

        raise FetchProviderError(f"Exceeded maximum redirects ({request.max_redirects})")

    def _extract_content(self, body: bytes, content_type: str) -> tuple[str, str]:
        """Extract readable text from response body."""
        try:
            # Decode with fallback
            text = body.decode("utf-8", errors="replace")
        except Exception:
            text = body.decode("latin-1", errors="replace")

        if not text.strip():
            return "", ""

        # HTML extraction
        if content_type in {"text/html", "application/xhtml+xml"}:
            return self._extract_html(text)

        # Plain text / other
        return text[:1_000_000], ""

    def _extract_html(self, html: str) -> tuple[str, str]:
        """Extract text content from HTML using BeautifulSoup."""
        try:
            soup = BeautifulSoup(html, "html.parser")

            # Remove script and style elements
            for elem in soup(["script", "style", "noscript", "iframe", "embed", "object"]):
                elem.decompose()

            # Get title
            title_elem = soup.find("title")
            title = title_elem.get_text(strip=True) if title_elem else ""

            # Get main content - prefer article, main, or body
            main_content = soup.find("article") or soup.find("main") or soup.find("body") or soup
            text = main_content.get_text(separator="\n", strip=True)

            # Limit length
            text = text[:1_000_000]

            return text, title[:500]
        except Exception:
            return text[:1_000_000], ""


class SearchProviderError(RuntimeError):
    """Raised when the local search provider cannot produce a bounded response."""


class SearchProvider:
    """Search-provider interface used by the tool handler."""

    async def search(self, query: str, *, max_results: int) -> SearchResponse:
        raise NotImplementedError


class SearXNGSearchProvider(SearchProvider):
    """Minimal SearXNG-compatible provider for local/self-hosted search."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        endpoint: str = "http://127.0.0.1:8080/search",
        max_response_bytes: int = 1_000_000,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self.endpoint = endpoint
        self.max_response_bytes = max_response_bytes
        self.timeout_seconds = timeout_seconds

    async def search(self, query: str, *, max_results: int) -> SearchResponse:
        normalized_query = query.strip()
        if not normalized_query:
            raise SearchProviderError("Search query cannot be empty")
        if max_results < 1:
            raise SearchProviderError("max_results must be at least 1")

        params = {"q": normalized_query, "format": "json", "categories": "general"}
        try:
            response = await self.client.get(
                self.endpoint,
                params=params,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            body = response.content
            if len(body) > self.max_response_bytes:
                raise SearchProviderError(
                    f"Search response exceeds {self.max_response_bytes} bytes"
                )
            payload: Any = response.json()
        except httpx.HTTPStatusError as exc:
            raise SearchProviderError(
                f"Search provider returned HTTP {exc.response.status_code}"
            ) from exc
        except httpx.TimeoutException as exc:
            raise SearchProviderError("Search provider timed out") from exc
        except (httpx.HTTPError, ValueError, json.JSONDecodeError) as exc:
            raise SearchProviderError(f"Search provider returned an invalid response: {exc}") from exc

        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise SearchProviderError("Search provider response has no results list")

        results: list[SearchResult] = []
        for raw_result in payload["results"][:max_results]:
            if not isinstance(raw_result, dict):
                continue
            try:
                results.append(
                    SearchResult(
                        title=str(raw_result.get("title", "")),
                        url=str(raw_result.get("url", "")),
                        summary=str(raw_result.get("content", "")),
                        source=str(raw_result.get("engine", "searxng")),
                        provider="searxng",
                    )
                )
            except ValueError:
                continue

        return SearchResponse(
            query=normalized_query,
            count=len(results),
            results=results,
            provider="searxng",
        )


def _create_handler(
    provider: SearchProvider,
) -> Any:
    async def search_handler(params: SearchRequest) -> SearchResponse:
        return await provider.search(params.query, max_results=params.max_results)

    return search_handler


def _create_fetch_handler(
    provider: FetchProvider,
) -> Any:
    async def fetch_handler(params: FetchRequest) -> FetchResponse:
        return await provider.fetch(params)

    return fetch_handler


def create_web_tools(
    registry: ToolRegistry | None = None,
    config: ToolsConfig | None = None,
    search_provider: SearchProvider | None = None,
    fetch_provider: FetchProvider | None = None,
    replace: bool = True,
) -> list[ToolDefinition]:
    """Create and register the read-only `web.search` and `web.fetch` tools."""

    target_registry = registry if registry is not None else default_registry
    tools_config = config or ToolsConfig()

    # Register web.search
    search_provider = search_provider or SearXNGSearchProvider(
        endpoint=tools_config.web_search_endpoint,
        max_response_bytes=tools_config.web_search_max_response_bytes,
        timeout_seconds=tools_config.web_search_timeout_seconds,
    )
    search_handler = _create_handler(search_provider)
    search_definition = ToolDefinition(
        name="web.search",
        description="Search a configured local or self-hosted SearXNG-compatible endpoint; returns bounded structured metadata only.",
        category="web",
        input_schema=SearchRequest,
        output_schema=SearchResponse,
        permission_level=PermissionLevel.SAFE,
        timeout_seconds=tools_config.web_search_timeout_seconds,
        handler=search_handler,
    )
    target_registry.register(search_definition, replace=replace)

    # Register web.fetch
    fetch_provider = fetch_provider or BuiltinFetchProvider(
        max_response_bytes=tools_config.web_fetch_max_response_bytes,
        default_timeout_seconds=tools_config.web_fetch_timeout_seconds,
        default_max_redirects=tools_config.web_fetch_max_redirects,
    )
    fetch_handler = _create_fetch_handler(fetch_provider)
    fetch_definition = ToolDefinition(
        name="web.fetch",
        description="Retrieve and extract content from a public HTTP/HTTPS URL with SSRF protection, redirect validation, and content sanitization. Returns untrusted extracted text only.",
        category="web",
        input_schema=FetchRequest,
        output_schema=FetchResponse,
        permission_level=PermissionLevel.SAFE,
        timeout_seconds=tools_config.web_fetch_timeout_seconds,
        handler=fetch_handler,
    )
    target_registry.register(fetch_definition, replace=replace)

    return [search_definition, fetch_definition]
