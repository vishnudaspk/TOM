"""Bounded deterministic web search and fetch tools.

This module provides:
- web.search: Search a configured local/self-hosted SearXNG endpoint (SAFE)
- web.fetch: Retrieve and extract public URLs with SSRF protection (SAFE, read-only)

Returned URLs from search are metadata only. Fetched content is untrusted data
and must be sanitized, bounded, and isolated from tool authorization.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import socket
import ssl
import time
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict, Field, field_validator

from tom.schemas.config import ToolsConfig
from tom.security.permissions import PermissionLevel
from tom.tools.registry import ToolDefinition, ToolRegistry, default_registry

MAX_FALLBACK_ADDRESSES_PER_HOP: int = 3


def _extract_tls_error(exc: BaseException) -> ssl.SSLError | None:
    """Inspect the exception chain for any ssl.SSLError or ssl.CertificateError.

    In httpx (0.28.1, `httpx/_transports/default.py:86-93`) and httpcore (1.0.9,
    `httpcore/_backends/anyio.py:61-66`), TLS/SSL errors are caught and re-raised as
    `httpcore.ConnectError` (storing the original SSLError in `args` and `__context__`),
    which is then re-raised by httpx as `httpx.ConnectError(message) from exc`.
    """
    seen: set[int] = set()
    stack: list[BaseException] = [exc]
    while stack:
        cur = stack.pop()
        if cur is None or id(cur) in seen:
            continue
        seen.add(id(cur))
        if isinstance(cur, (ssl.SSLError, ssl.CertificateError)):
            return cur
        if hasattr(cur, "__cause__") and cur.__cause__:
            stack.append(cur.__cause__)
        if hasattr(cur, "__context__") and cur.__context__:
            stack.append(cur.__context__)
        if hasattr(cur, "args"):
            for arg in cur.args:
                if isinstance(arg, BaseException):
                    stack.append(arg)
    return None


def _format_tls_error_reason(tls_err: ssl.SSLError) -> str:
    """Extract a human-readable reason string from a TLS/SSL error."""
    return (
        getattr(tls_err, "verify_message", None) or getattr(tls_err, "reason", None) or str(tls_err)
    )


_CONVERSATIONAL_PREFIXES: list[re.Pattern[str]] = [
    re.compile(r"^(hey\s+)?tom[\s,\.:]+", re.IGNORECASE),
    re.compile(
        r"^(can\s+you\s+|could\s+you\s+|would\s+you\s+)?(please\s+)?(search(\s+for|\s+online|\s+the\s+web\s+for)?|look\s+up|find\s+out(\s+about)?|tell\s+me(\s+about)?|google|browse)(\s+for|\s+about)?\s+",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(what\s+is|who\s+is|where\s+is|how\s+much\s+is|when\s+is|who\s+was|what\s+are|who\s+are|who\s+won(\s+the)?)\s+(the\s+)?",
        re.IGNORECASE,
    ),
]

_CONVERSATIONAL_SUFFIXES: list[re.Pattern[str]] = [
    re.compile(
        r"[\s,\.\?!]+(please|thanks|thank\s+you|asap|right\s+now)\s*[\.\?!]*$", re.IGNORECASE
    ),
    re.compile(r"[\.\?!]+$"),
]


def refine_search_query(query: str) -> str:
    """Distill raw user utterance into a high-signal search query.

    Strips conversational greetings, polite phrasing, and query wrappers,
    leaving clean keywords for maximum relevance and fast search retrieval.
    """
    cleaned = query.strip()
    changed = True
    while changed:
        before = cleaned
        for pattern in _CONVERSATIONAL_SUFFIXES:
            cleaned = pattern.sub("", cleaned).strip()
        for pattern in _CONVERSATIONAL_PREFIXES:
            cleaned = pattern.sub("", cleaned).strip()
        changed = cleaned != before

    return cleaned if cleaned else query.strip()


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


def _is_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Check if an IP address is strictly public, unpacking IPv4-mapped IPv6."""
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return (
        not ip.is_loopback
        and not ip.is_private
        and not ip.is_link_local
        and not ip.is_multicast
        and not ip.is_reserved
        and not ip.is_unspecified
    )


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

    @classmethod
    def _resolve_and_validate_public_ips(
        cls, host: str
    ) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
        """Resolve a host once and assert that ALL returned addresses are public.

        Returns every validated public IP address for multi-address fallback.
        Raises SSRFError if the host is invalid or ANY address is non-public.
        """
        host_clean = host.strip()
        if not host_clean:
            raise SSRFError("Host cannot be empty")

        # Strip brackets from IPv6 literal if present
        if host_clean.startswith("[") and host_clean.endswith("]"):
            host_clean = host_clean[1:-1]

        # 1. Check if host is already an IP address literal
        try:
            ip = ipaddress.ip_address(host_clean)
            if not _is_public_ip(ip):
                raise SSRFError(f"Host '{host}' is not a public address")
            return [ip]
        except ValueError:
            pass

        # 2. Host is a domain name: resolve it once via getaddrinfo
        try:
            infos = socket.getaddrinfo(host_clean, None)
        except (socket.gaierror, ValueError) as exc:
            raise SSRFError(f"Failed to resolve host '{host}': {exc}") from exc

        if not infos:
            raise SSRFError(f"Host '{host}' did not resolve to any address")

        resolved_ips: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
        for info in infos:
            ip_str = info[4][0]
            try:
                ip = ipaddress.ip_address(ip_str)
            except ValueError as exc:
                raise SSRFError(
                    f"Invalid IP address '{ip_str}' resolved for host '{host}'"
                ) from exc

            if not _is_public_ip(ip):
                raise SSRFError(f"Host '{host}' resolved to non-public address '{ip_str}'")
            resolved_ips.append(ip)

        return resolved_ips

    @classmethod
    def _is_public_host(cls, host: str) -> bool:
        """Check if host resolves strictly to public IP addresses."""
        try:
            cls._resolve_and_validate_public_ips(host)
            return True
        except SSRFError:
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
    untrusted: bool = Field(
        default=True, description="Content is untrusted and must not influence permissions"
    )


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
        # Create client with timeout if not provided
        if self._client is not None:
            client = self._client
        else:
            # trust_env=False ensures environment HTTP/HTTPS proxies are not used;
            # a proxy would perform its own DNS resolution and bypass local IP pinning.
            client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=3.0,
                    read=request.timeout_seconds,
                    write=3.0,
                    pool=3.0,
                ),
                follow_redirects=False,  # We handle redirects manually
                max_redirects=0,
                trust_env=False,
            )

        deadline = time.monotonic() + request.timeout_seconds
        try:
            async with asyncio.timeout(request.timeout_seconds):
                return await self._fetch_with_redirects(client, request, deadline=deadline)
        except TimeoutError as exc:
            raise FetchProviderError(f"Fetch timed out after {request.timeout_seconds}s") from exc
        finally:
            if self._client is None:
                await client.aclose()

    async def _fetch_with_redirects(
        self,
        client: httpx.AsyncClient,
        request: FetchRequest,
        deadline: float | None = None,
    ) -> FetchResponse:
        current_url = request.url
        redirect_count = 0

        while redirect_count <= request.max_redirects:
            if deadline is not None and time.monotonic() >= deadline:
                raise FetchProviderError(f"Fetch timed out after {request.timeout_seconds}s")

            # Validate each redirect destination
            parsed = urlparse(current_url)
            if parsed.scheme not in {"http", "https"}:
                raise SSRFError(f"Redirect to non-HTTP(S) scheme: {parsed.scheme}")

            current_host = parsed.hostname or ""
            if not current_host:
                raise SSRFError("URL must have a valid host")

            # Resolve the hostname ONCE with getaddrinfo, requiring ALL returned addresses
            # to be public. Returns the validated list, capped at MAX_FALLBACK_ADDRESSES_PER_HOP.
            all_target_ips = FetchRequest._resolve_and_validate_public_ips(current_host)
            target_ips = all_target_ips[:MAX_FALLBACK_ADDRESSES_PER_HOP]

            # Build Host header: bracket IPv6, include port only when non-default.
            is_ipv6_host = target_ips[0].version == 6 and (
                current_host == str(target_ips[0]) or current_host == f"[{target_ips[0]}]"
            )
            host_name = f"[{target_ips[0]}]" if is_ipv6_host else current_host
            default_port = 443 if parsed.scheme == "https" else 80
            has_non_default_port = parsed.port is not None and parsed.port != default_port
            host_header = f"{host_name}:{parsed.port}" if has_non_default_port else host_name
            ua = (
                "TOMPersonalAssistant/1.0 (https://github.com/vishnudaspk/TOM; contact@tom.local)"
                if "wikipedia.org" in current_host
                else (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                )
            )
            req_headers = {
                "Host": host_header,
                "User-Agent": ua,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            }
            req_extensions: dict[str, Any] = {}
            if parsed.scheme == "https":
                # sni_hostname tells httpcore to use this as the TLS server_hostname
                # instead of the IP literal it sees in the URL
                # (httpcore/_async/connection.py:107, 151 — httpcore 1.0.9)
                req_extensions["sni_hostname"] = host_name

            # Try each validated IP in order; retry on plain connection-level errors only.
            # Never retry on TLS/cert errors or HTTP-status errors; never use an unvalidated address.
            last_connect_exc: Exception | None = None
            response = None
            for target_ip in target_ips:
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise FetchProviderError(
                            f"Fetch timed out after {request.timeout_seconds}s"
                        )
                    step_timeout = httpx.Timeout(
                        connect=min(3.0, remaining),
                        read=remaining,
                        write=min(3.0, remaining),
                        pool=min(3.0, remaining),
                    )
                else:
                    step_timeout = httpx.Timeout(
                        connect=3.0,
                        read=request.timeout_seconds,
                        write=3.0,
                        pool=3.0,
                    )

                ip_literal = f"[{target_ip}]" if target_ip.version == 6 else str(target_ip)
                netloc_with_ip = f"{ip_literal}:{parsed.port}" if parsed.port else ip_literal
                target_url = urlunparse(
                    (
                        parsed.scheme,
                        netloc_with_ip,
                        parsed.path or "/",
                        parsed.params,
                        parsed.query,
                        parsed.fragment,
                    )
                )
                try:
                    response = await client.get(
                        target_url,
                        headers=req_headers,
                        extensions=req_extensions if req_extensions else None,
                        timeout=step_timeout,
                    )
                    last_connect_exc = None
                    break  # Successful connection — stop trying
                except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                    tls_err = _extract_tls_error(exc)
                    if tls_err is not None:
                        reason = _format_tls_error_reason(tls_err)
                        raise FetchProviderError(
                            f"TLS certificate verification failed for '{current_host}': {reason}"
                        ) from exc
                    # Connection-level failure only — try the next validated address
                    last_connect_exc = exc
                    continue
                except httpx.TimeoutException as exc:
                    # Read/write timeout — not a connect error, propagate immediately
                    raise FetchProviderError(
                        f"Fetch timed out after {request.timeout_seconds}s"
                    ) from exc
                except httpx.HTTPError as exc:
                    tls_err = _extract_tls_error(exc)
                    if tls_err is not None:
                        reason = _format_tls_error_reason(tls_err)
                        raise FetchProviderError(
                            f"TLS certificate verification failed for '{current_host}': {reason}"
                        ) from exc
                    raise FetchProviderError(f"Fetch failed: {exc}") from exc

            if response is None:
                raise FetchProviderError(
                    f"All addresses for '{current_host}' failed to connect: {last_connect_exc}"
                ) from last_connect_exc

            # Check response size
            content_length = response.headers.get("content-length")
            if content_length and int(content_length) > request.max_response_bytes:
                raise FetchProviderError(
                    f"Response size {content_length} exceeds limit {request.max_response_bytes}"
                )

            # Read body with size limit
            body = b""
            async for chunk in response.aiter_bytes():
                body += chunk
                if len(body) > request.max_response_bytes:
                    raise FetchProviderError(
                        f"Response body exceeds {request.max_response_bytes} bytes"
                    )

            # Check content type
            content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
            if content_type and content_type not in request.allowed_content_types:
                raise FetchProviderError(f"Content type '{content_type}' not allowed")

            # Handle redirects manually
            if 300 <= response.status_code < 400 and "location" in response.headers:
                redirect_count += 1
                if redirect_count > request.max_redirects:
                    raise FetchProviderError(
                        f"Exceeded maximum redirects ({request.max_redirects})"
                    )

                location = response.headers["location"]
                # urljoin uses current_url (with original hostname, not the pinned IP)
                # so the reconstructed URL always carries the real hostname forward.
                current_url = urljoin(current_url, location)
                continue

            # Extract content
            extracted_text, title = self._extract_content(body, content_type)

            # final_url always shows the original hostname, never the pinned IP literal,
            # so callers see where they logically ended up, not transport internals.
            return FetchResponse(
                url=request.url,
                final_url=current_url,
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
        """Extract high-quality readable text content from HTML using BeautifulSoup."""
        try:
            soup = BeautifulSoup(html, "html.parser")

            # 1. Remove non-content, script, navigation, and advertisement tags
            noise_tags = [
                "script",
                "style",
                "noscript",
                "iframe",
                "embed",
                "object",
                "nav",
                "footer",
                "header",
                "aside",
                "form",
                "svg",
            ]
            for elem in soup(noise_tags):
                elem.decompose()

            # 2. Decompose common cookie banners and ad overlays by class / id
            for elem in soup.find_all(True):
                classes = " ".join(elem.get("class", [])) if elem.get("class") else ""
                elem_id = str(elem.get("id", ""))
                ident = f"{classes} {elem_id}".lower()
                if any(
                    x in ident
                    for x in [
                        "cookie",
                        "consent-banner",
                        "ad-container",
                        "advertisement",
                        "newsletter-signup",
                    ]
                ):
                    elem.decompose()

            # 3. Extract title
            title_elem = soup.find("title")
            title = title_elem.get_text(strip=True) if title_elem else ""

            # 4. Extract meta description for high-signal summary
            meta_desc = ""
            desc_elem = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
            if desc_elem and desc_elem.get("content"):
                meta_desc = str(desc_elem["content"]).strip()

            # 5. Locate semantic content container
            main_content = (
                soup.find("article")
                or soup.find("main")
                or soup.find(attrs={"role": "main"})
                or soup.find(class_=re.compile(r"(post|article|entry|main)[-_]content", re.I))
                or soup.find("body")
                or soup
            )

            # Format headings and paragraphs cleanly
            for h in main_content.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
                level = int(h.name[1]) if len(h.name) == 2 and h.name[1].isdigit() else 2
                h.string = f"\n\n{'#' * level} {h.get_text(strip=True)}\n"

            for li in main_content.find_all("li"):
                li.string = f"\n- {li.get_text(strip=True)}"

            text = main_content.get_text(separator="\n", strip=True)

            # Collapse redundant blank lines
            text = re.sub(r"\n{3,}", "\n\n", text).strip()

            # If content is sparse but meta description exists, prepend it
            if meta_desc and (len(text) < 200 or meta_desc[:50] not in text):
                text = f"Summary: {meta_desc}\n\n{text}".strip()

            # Limit length
            text = text[:1_000_000]

            return text, title[:500]
        except Exception:
            return html[:1_000_000], ""


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
            raise SearchProviderError(
                f"Search provider returned an invalid response: {exc}"
            ) from exc

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


class DuckDuckGoSearchProvider(SearchProvider):
    """Zero-cost, no-API web search provider using DuckDuckGo HTML and Lite."""

    _HTML_ENDPOINT = "https://html.duckduckgo.com/html/"
    _LITE_ENDPOINT = "https://lite.duckduckgo.com/lite/"
    _USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 8.0,
    ) -> None:
        self.client = client
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _clean_ddg_url(raw_href: str) -> str:
        """Extract the true destination URL from DuckDuckGo redirect link."""
        if not raw_href:
            return ""
        if "uddg=" in raw_href:
            try:
                parsed = urlparse(raw_href)
                qs = parse_qs(parsed.query)
                if "uddg" in qs and qs["uddg"]:
                    return qs["uddg"][0]
            except Exception:
                pass
        if raw_href.startswith("//"):
            return "https:" + raw_href
        return raw_href

    async def _search_html(
        self, client: httpx.AsyncClient, query: str, max_results: int
    ) -> list[SearchResult]:
        try:
            resp = await client.post(
                self._HTML_ENDPOINT,
                data={"q": query},
                headers={
                    "User-Agent": self._USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.5",
                },
                timeout=self.timeout_seconds,
            )
            if resp.status_code != 200:
                return []

            soup = BeautifulSoup(resp.text, "html.parser")
            results: list[SearchResult] = []
            seen_urls: set[str] = set()

            for r in soup.find_all("div", class_="result"):
                title_el = r.find("a", class_="result__a")
                snippet_el = r.find("a", class_="result__snippet")
                if not title_el:
                    continue

                raw_url = title_el.get("href", "")
                clean_url = self._clean_ddg_url(raw_url)
                if not clean_url or clean_url in seen_urls:
                    continue

                title = title_el.get_text(strip=True)
                snippet = snippet_el.get_text(strip=True) if snippet_el else ""
                if not title:
                    continue

                try:
                    domain = urlparse(clean_url).netloc
                    results.append(
                        SearchResult(
                            title=title[:500],
                            url=clean_url[:2048],
                            summary=snippet[:2000],
                            source=domain or "duckduckgo",
                            provider="duckduckgo",
                        )
                    )
                    seen_urls.add(clean_url)
                except ValueError:
                    continue

                if len(results) >= max_results:
                    break

            return results
        except Exception:
            return []

    async def _search_lite(
        self, client: httpx.AsyncClient, query: str, max_results: int
    ) -> list[SearchResult]:
        try:
            resp = await client.post(
                self._LITE_ENDPOINT,
                data={"q": query},
                headers={"User-Agent": self._USER_AGENT},
                timeout=self.timeout_seconds,
            )
            if resp.status_code != 200:
                return []

            soup = BeautifulSoup(resp.text, "html.parser")
            results: list[SearchResult] = []
            seen_urls: set[str] = set()

            links = soup.find_all("a", class_="result-link")
            snippets = soup.find_all("td", class_="result-snippet")

            for link, snip in zip(links, snippets, strict=False):
                raw_url = link.get("href", "")
                clean_url = self._clean_ddg_url(raw_url)
                if not clean_url or clean_url in seen_urls:
                    continue

                title = link.get_text(strip=True)
                snippet = snip.get_text(strip=True)
                if not title:
                    continue

                try:
                    domain = urlparse(clean_url).netloc
                    results.append(
                        SearchResult(
                            title=title[:500],
                            url=clean_url[:2048],
                            summary=snippet[:2000],
                            source=domain or "duckduckgo",
                            provider="duckduckgo",
                        )
                    )
                    seen_urls.add(clean_url)
                except ValueError:
                    continue

                if len(results) >= max_results:
                    break

            return results
        except Exception:
            return []

    async def search(self, query: str, *, max_results: int) -> SearchResponse:
        refined = refine_search_query(query)
        if not refined:
            raise SearchProviderError("Search query cannot be empty")
        if max_results < 1:
            raise SearchProviderError("max_results must be at least 1")

        created_client = False
        client = self.client
        if client is None:
            client = httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=True)
            created_client = True

        try:
            results = await self._search_html(client, refined, max_results)
            if not results:
                results = await self._search_lite(client, refined, max_results)

            if not results:
                raise SearchProviderError(f"No DuckDuckGo results found for '{refined}'")

            return SearchResponse(
                query=refined,
                count=len(results),
                results=results,
                provider="duckduckgo",
            )
        except SearchProviderError:
            raise
        except Exception as exc:
            raise SearchProviderError(f"DuckDuckGo search failed: {exc}") from exc
        finally:
            if created_client:
                await client.aclose()


class WikipediaSearchProvider(SearchProvider):
    """Zero-cost Wikipedia search and summary provider for factual queries."""

    _OPENSEARCH_ENDPOINT = "https://en.wikipedia.org/w/api.php"
    _USER_AGENT = "TOM-Assistant/1.0 (https://github.com/vishnudaspk/TOM)"

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 6.0,
    ) -> None:
        self.client = client
        self.timeout_seconds = timeout_seconds

    async def search(self, query: str, *, max_results: int) -> SearchResponse:
        refined = refine_search_query(query)
        if not refined:
            raise SearchProviderError("Search query cannot be empty")

        created_client = False
        client = self.client
        if client is None:
            client = httpx.AsyncClient(
                timeout=self.timeout_seconds,
                headers={"User-Agent": self._USER_AGENT},
                follow_redirects=True,
            )
            created_client = True

        try:
            resp = await client.get(
                self._OPENSEARCH_ENDPOINT,
                params={
                    "action": "opensearch",
                    "search": refined,
                    "limit": min(max_results, 5),
                    "namespace": 0,
                    "format": "json",
                },
            )
            results: list[SearchResult] = []
            if resp.status_code == 200:
                data = resp.json()
                if len(data) >= 4 and isinstance(data[1], list):
                    titles, descs, urls = data[1], data[2], data[3]
                    for title, desc, url in zip(titles, descs, urls, strict=False):
                        if url and title:
                            results.append(
                                SearchResult(
                                    title=title[:500],
                                    url=url[:2048],
                                    summary=desc[:2000] if desc else f"Wikipedia entry for {title}",
                                    source="wikipedia.org",
                                    provider="wikipedia",
                                )
                            )
            return SearchResponse(
                query=refined,
                count=len(results),
                results=results,
                provider="wikipedia",
            )
        except Exception as exc:
            raise SearchProviderError(f"Wikipedia search failed: {exc}") from exc
        finally:
            if created_client:
                await client.aclose()


class CompositeSearchProvider(SearchProvider):
    """High-accuracy, multi-tier, zero-cost search provider.

    Tiers:
    1. Local SearXNG (if configured and responding).
    2. DuckDuckGo HTML / Lite (100% free web search, no API key).
    3. Wikipedia instant knowledge enrichment.
    """

    def __init__(
        self,
        *,
        searxng_provider: SearXNGSearchProvider | None = None,
        ddg_provider: DuckDuckGoSearchProvider | None = None,
        wiki_provider: WikipediaSearchProvider | None = None,
    ) -> None:
        self.searxng = searxng_provider
        self.ddg = ddg_provider or DuckDuckGoSearchProvider()
        self.wiki = wiki_provider or WikipediaSearchProvider()

    async def search(self, query: str, *, max_results: int) -> SearchResponse:
        refined = refine_search_query(query)

        # 1. Try SearXNG if configured
        if self.searxng is not None:
            try:
                res = await self.searxng.search(refined, max_results=max_results)
                if res.results:
                    return res
            except Exception:
                pass  # Fall through to zero-cost free providers

        # 2. Query DuckDuckGo
        ddg_results: list[SearchResult] = []
        try:
            ddg_resp = await self.ddg.search(refined, max_results=max_results)
            ddg_results = ddg_resp.results
        except Exception:
            pass

        # 3. Enrich with Wikipedia if results are sparse or query is encyclopedic
        wiki_results: list[SearchResult] = []
        if len(ddg_results) < max_results:
            try:
                wiki_resp = await self.wiki.search(refined, max_results=2)
                wiki_results = wiki_resp.results
            except Exception:
                pass

        # Combine, rank, and deduplicate by URL
        combined: list[SearchResult] = []
        seen_urls: set[str] = set()

        for r in wiki_results:
            if r.url not in seen_urls:
                seen_urls.add(r.url)
                combined.append(r)

        for r in ddg_results:
            if r.url not in seen_urls:
                seen_urls.add(r.url)
                combined.append(r)

        if not combined:
            raise SearchProviderError(f"No search results found for: {refined}")

        return SearchResponse(
            query=refined,
            count=len(combined[:max_results]),
            results=combined[:max_results],
            provider="composite",
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
    search_provider = search_provider or CompositeSearchProvider(
        searxng_provider=SearXNGSearchProvider(
            endpoint=tools_config.web_search_endpoint,
            max_response_bytes=tools_config.web_search_max_response_bytes,
            timeout_seconds=tools_config.web_search_timeout_seconds,
        ),
        ddg_provider=DuckDuckGoSearchProvider(
            timeout_seconds=tools_config.web_search_timeout_seconds
        ),
        wiki_provider=WikipediaSearchProvider(
            timeout_seconds=tools_config.web_search_timeout_seconds
        ),
    )
    search_handler = _create_handler(search_provider)
    search_definition = ToolDefinition(
        name="web.search",
        description="Search the web using zero-cost DuckDuckGo, Wikipedia, and local search; returns bounded structured metadata with high-relevance summaries.",
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
