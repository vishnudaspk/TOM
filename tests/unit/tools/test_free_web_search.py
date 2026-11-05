"""Unit tests for free, zero-cost, no-API web search and extraction enhancements."""

from typing import Any

import httpx
import pytest
from tom.tools.web import (
    BuiltinFetchProvider,
    CompositeSearchProvider,
    DuckDuckGoSearchProvider,
    SearchResult,
    WeatherSearchProvider,
    WikipediaSearchProvider,
    extract_weather_location,
    refine_search_query,
)


class TestQueryRefinement:
    """Verify that conversational utterances are distilled into high-signal search terms."""

    def test_strip_hey_tom_prefix(self) -> None:
        assert refine_search_query("Hey TOM, what is quantum computing?") == "quantum computing"
        assert (
            refine_search_query("tom: search for python 3.15 release date")
            == "python 3.15 release date"
        )

    def test_strip_conversational_verbs(self) -> None:
        assert (
            refine_search_query("can you please search online for NASA Artemis mission")
            == "NASA Artemis mission"
        )
        assert refine_search_query("could you look up who won the World Cup please?") == "World Cup"
        assert (
            refine_search_query("tell me about James Webb space telescope")
            == "James Webb space telescope"
        )

    def test_strip_trailing_polite_phrases(self) -> None:
        assert refine_search_query("latest nvidia drivers please") == "latest nvidia drivers"
        assert refine_search_query("weather in Tokyo right now") == "weather in Tokyo"

    def test_preserve_direct_queries(self) -> None:
        assert refine_search_query("python release notes") == "python release notes"
        assert refine_search_query("C++23 features") == "C++23 features"


class TestDuckDuckGoSearchProvider:
    """Verify DuckDuckGo HTML and Lite parsing without external APIs."""

    def test_clean_ddg_url(self) -> None:
        raw = "/l/?kh=-1&uddg=https%3A%2F%2Fwww.python.org%2Fdownloads%2F"
        assert DuckDuckGoSearchProvider._clean_ddg_url(raw) == "https://www.python.org/downloads/"

        direct = "https://example.com/page"
        assert DuckDuckGoSearchProvider._clean_ddg_url(direct) == "https://example.com/page"

    @pytest.mark.asyncio
    async def test_search_html_parsing(self) -> None:
        html_payload = """
        <html><body>
        <div class="result">
            <a class="result__a" href="/l/?kh=-1&uddg=https%3A%2F%2Fexample.com%2Fpython">Python Guide</a>
            <a class="result__snippet">Official guide to Python programming.</a>
        </div>
        <div class="result">
            <a class="result__a" href="https://example.org/news">Tech News</a>
            <a class="result__snippet">Daily technology updates and articles.</a>
        </div>
        </body></html>
        """

        def mock_handler(request: httpx.Request) -> httpx.Response:
            assert request.url == "https://html.duckduckgo.com/html/"
            return httpx.Response(status_code=200, text=html_payload)

        client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
        provider = DuckDuckGoSearchProvider(client=client)

        resp = await provider.search("python guide", max_results=5)
        assert resp.count == 2
        assert resp.results[0].title == "Python Guide"
        assert resp.results[0].url == "https://example.com/python"
        assert resp.results[0].summary == "Official guide to Python programming."
        assert resp.results[0].provider == "duckduckgo"
        assert resp.results[1].url == "https://example.org/news"

    @pytest.mark.asyncio
    async def test_fallback_to_lite_when_html_fails(self) -> None:
        lite_payload = """
        <html><body>
        <table>
            <tr><td><a class="result-link" href="https://lite.example.com">Lite Result</a></td></tr>
            <tr><td class="result-snippet">Snippet from DuckDuckGo Lite.</td></tr>
        </table>
        </body></html>
        """

        def mock_handler(request: httpx.Request) -> httpx.Response:
            if "html.duckduckgo.com" in str(request.url):
                return httpx.Response(status_code=403, text="Forbidden")
            return httpx.Response(status_code=200, text=lite_payload)

        client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
        provider = DuckDuckGoSearchProvider(client=client)

        resp = await provider.search("query", max_results=1)
        assert resp.count == 1
        assert resp.results[0].title == "Lite Result"
        assert resp.results[0].url == "https://lite.example.com"
        assert resp.results[0].summary == "Snippet from DuckDuckGo Lite."


class TestWikipediaSearchProvider:
    """Verify factual open-search integration."""

    @pytest.mark.asyncio
    async def test_wikipedia_opensearch_parsing(self) -> None:
        wiki_payload = [
            "Alan Turing",
            ["Alan Turing", "Turing machine"],
            ["English mathematician and computer scientist", "Mathematical model of computation"],
            [
                "https://en.wikipedia.org/wiki/Alan_Turing",
                "https://en.wikipedia.org/wiki/Turing_machine",
            ],
        ]

        def mock_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=200, json=wiki_payload)

        client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
        provider = WikipediaSearchProvider(client=client)

        resp = await provider.search("who was Alan Turing", max_results=2)
        assert resp.count == 2
        assert resp.results[0].title == "Alan Turing"
        assert resp.results[0].url == "https://en.wikipedia.org/wiki/Alan_Turing"
        assert "mathematician" in resp.results[0].summary
        assert resp.results[0].provider == "wikipedia"


class TestCompositeSearchProvider:
    """Verify tier orchestration and result deduplication."""

    @pytest.mark.asyncio
    async def test_composite_combines_and_deduplicates(self) -> None:
        class FakeSearchProvider:
            def __init__(self, items: list[SearchResult]):
                self.items = items

            async def search(self, query: str, *, max_results: int) -> Any:
                from tom.tools.web import SearchResponse

                return SearchResponse(
                    query=query, count=len(self.items), results=self.items, provider="fake"
                )

        item1 = SearchResult(
            title="Shared Item",
            url="https://example.com/shared",
            summary="From Wiki",
            source="wiki",
            provider="wikipedia",
        )
        item2 = SearchResult(
            title="Shared Item Dup",
            url="https://example.com/shared",
            summary="From DDG",
            source="ddg",
            provider="duckduckgo",
        )
        item3 = SearchResult(
            title="DDG Unique",
            url="https://example.com/unique",
            summary="Unique to DDG",
            source="ddg",
            provider="duckduckgo",
        )

        wiki = FakeSearchProvider([item1])
        ddg = FakeSearchProvider([item2, item3])

        composite = CompositeSearchProvider(ddg_provider=ddg, wiki_provider=wiki)  # type: ignore
        resp = await composite.search("test", max_results=5)

        # item2 should be deduplicated since url is already in item1
        assert resp.count == 2
        urls = [r.url for r in resp.results]
        assert urls == ["https://example.com/shared", "https://example.com/unique"]


class TestEnhancedHtmlExtraction:
    """Verify content extraction strips noise and structures article text."""

    def test_strips_ads_nav_cookie_banners(self) -> None:
        html = """
        <html>
        <head><title>Test Article Title</title></head>
        <body>
            <nav><a href="/home">Home</a><a href="/login">Login</a></nav>
            <header>Site Header Banner</header>
            <div id="cookie-consent-banner">Please accept all tracking cookies.</div>
            <article>
                <h1>Deep Space Exploration</h1>
                <p>Voyager 1 continues its journey beyond the heliosphere.</p>
                <ul>
                    <li>Launched: 1977</li>
                    <li>Distance: Over 24 billion kilometers</li>
                </ul>
            </article>
            <div class="ad-container sidebar">Sponsored Ad Content</div>
            <footer>Copyright 2026 Space News Inc.</footer>
        </body>
        </html>
        """
        fetcher = BuiltinFetchProvider()
        text, title = fetcher._extract_html(html)

        assert title == "Test Article Title"
        assert "Voyager 1 continues its journey" in text
        assert "Deep Space Exploration" in text
        assert "Launched: 1977" in text
        # Noise stripped
        assert "Home" not in text
        assert "cookie-consent" not in text
        assert "Please accept all tracking cookies" not in text
        assert "Sponsored Ad Content" not in text
        assert "Copyright 2026" not in text


class TestWeatherSearchProvider:
    """Verify zero-cost real-time weather querying and fallback mechanisms."""

    def test_extract_weather_location(self) -> None:
        assert extract_weather_location("what is the weather in New York?") == "New York"
        assert extract_weather_location("weather of New York") == "New York"
        assert extract_weather_location("how is the weather in London?") == "London"
        assert extract_weather_location("is it raining in Seattle right now?") == "Seattle"
        assert extract_weather_location("weather forecast for Paris tomorrow") == "Paris"
        assert extract_weather_location("temperature in Tokyo") == "Tokyo"
        assert extract_weather_location("who won the 2024 ICC T20 World Cup?") is None
        assert extract_weather_location("what is quantum computing?") is None
        assert extract_weather_location("python tutorial") is None

    @pytest.mark.asyncio
    async def test_weather_search_wttr_success(self) -> None:
        wttr_data = {
            "current_condition": [
                {
                    "temp_C": "22",
                    "temp_F": "71",
                    "FeelsLikeC": "21",
                    "FeelsLikeF": "70",
                    "humidity": "74",
                    "windspeedMiles": "8",
                    "winddir16Point": "S",
                    "weatherDesc": [{"value": "Partly cloudy"}],
                }
            ],
            "weather": [
                {
                    "maxtempC": "24",
                    "maxtempF": "75",
                    "mintempC": "16",
                    "mintempF": "61",
                }
            ],
        }

        def mock_handler(request: httpx.Request) -> httpx.Response:
            if "wttr.in" in str(request.url):
                return httpx.Response(status_code=200, json=wttr_data)
            return httpx.Response(status_code=404)

        client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
        provider = WeatherSearchProvider(client=client)

        resp = await provider.search("weather in New York")
        assert resp.count == 1
        res = resp.results[0]
        assert "New York" in res.title
        assert "Partly cloudy" in res.summary
        assert "71°F (22°C)" in res.summary
        assert "74%" in res.summary
        assert res.source == "wttr.in"
        assert res.provider == "weather"

    @pytest.mark.asyncio
    async def test_weather_search_open_meteo_fallback(self) -> None:
        geo_data = {
            "results": [
                {
                    "name": "London",
                    "latitude": 51.5074,
                    "longitude": -0.1278,
                    "country": "United Kingdom",
                }
            ]
        }
        forecast_data = {
            "current": {
                "temperature_2m": 15.0,
                "apparent_temperature": 14.0,
                "relative_humidity_2m": 65,
                "wind_speed_10m": 12.0,
                "weather_code": 2,  # Partly cloudy
            },
            "daily": {
                "temperature_2m_max": [18.0],
                "temperature_2m_min": [10.0],
            },
        }

        def mock_handler(request: httpx.Request) -> httpx.Response:
            url_str = str(request.url)
            if "wttr.in" in url_str:
                return httpx.Response(status_code=503)
            if "geocoding-api.open-meteo.com" in url_str:
                return httpx.Response(status_code=200, json=geo_data)
            if "api.open-meteo.com" in url_str:
                return httpx.Response(status_code=200, json=forecast_data)
            return httpx.Response(status_code=404)

        client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
        provider = WeatherSearchProvider(client=client)

        resp = await provider.search("weather in London")
        assert resp.count == 1
        res = resp.results[0]
        assert "London" in res.title
        assert "Partly cloudy" in res.summary
        assert "15.0°C" in res.summary
        assert res.source == "open-meteo.com"
        assert res.provider == "weather"

    @pytest.mark.asyncio
    async def test_composite_weather_placed_first(self) -> None:
        weather_item = SearchResult(
            title="Current Weather in New York",
            url="https://wttr.in/New%20York",
            summary="Partly cloudy, 71°F (22°C)",
            source="wttr.in",
            provider="weather",
        )
        ddg_item = SearchResult(
            title="Weather Channel New York",
            url="https://weather.com/new-york",
            summary="Today's weather forecast",
            source="weather.com",
            provider="duckduckgo",
        )

        class MockWeatherProvider(WeatherSearchProvider):
            async def search(self, query: str, *, max_results: int = 1) -> Any:
                from tom.tools.web import SearchResponse

                return SearchResponse(
                    query=query, count=1, results=[weather_item], provider="weather"
                )

        class MockDDGProvider(DuckDuckGoSearchProvider):
            async def search(self, query: str, *, max_results: int = 5) -> Any:
                from tom.tools.web import SearchResponse

                return SearchResponse(
                    query=query, count=1, results=[ddg_item], provider="duckduckgo"
                )

        composite = CompositeSearchProvider(
            weather_provider=MockWeatherProvider(),
            ddg_provider=MockDDGProvider(),
        )

        resp = await composite.search("what is the weather in New York?", max_results=5)
        assert resp.count == 2
        # Weather result must be placed first
        assert resp.results[0].provider == "weather"
        assert "Partly cloudy, 71°F (22°C)" in resp.results[0].summary
        assert resp.results[1].provider == "duckduckgo"
