"""Web tools: DuckDuckGo search + URL fetch-to-markdown."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from mybot.tools.base import BaseTool, ToolResult

try:  # Optional at import time — the error surfaces on execute if missing.
    from duckduckgo_search import DDGS  # type: ignore
except Exception:  # noqa: BLE001
    DDGS = None  # type: ignore[assignment]

try:
    import html2text  # type: ignore
except Exception:  # noqa: BLE001
    html2text = None  # type: ignore[assignment]


DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_LENGTH = 5000


class WebSearchTool(BaseTool):
    """Search the web via DuckDuckGo (no API key required)."""

    name = "web_search"
    description = (
        "Search the web using DuckDuckGo and return the top results "
        "(title, URL, snippet). Good for finding recent info, docs, or references."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query, e.g. 'LiteLLM function calling docs'.",
            },
            "num_results": {
                "type": "integer",
                "description": "Number of results to return (default 5, max 20).",
                "default": 5,
            },
        },
        "required": ["query"],
    }

    def __init__(self, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.timeout = float(timeout)

    async def execute(self, **params) -> ToolResult:
        query = params.get("query")
        if not query or not isinstance(query, str):
            return ToolResult(success=False, output="", error="Missing required parameter 'query'.")
        try:
            num_results = int(params.get("num_results") or 5)
        except (TypeError, ValueError):
            return ToolResult(success=False, output="", error="'num_results' must be an integer.")
        num_results = max(1, min(num_results, 20))

        if DDGS is None:
            return ToolResult(
                success=False,
                output="",
                error="duckduckgo_search is not installed. Install with `pip install duckduckgo-search`.",
            )

        try:
            results: list[dict[str, Any]] = await asyncio.to_thread(
                self._search_sync, query, num_results,
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, output="", error=f"Search failed: {exc}")

        if not results:
            return ToolResult(success=True, output=f"No results for query: {query!r}")

        lines: list[str] = [f"Top {len(results)} results for {query!r}:\n"]
        for i, r in enumerate(results, start=1):
            title = r.get("title") or r.get("heading") or "(no title)"
            url = r.get("href") or r.get("url") or ""
            snippet = r.get("body") or r.get("snippet") or ""
            lines.append(f"{i}. {title}\n   {url}\n   {snippet}")
        return ToolResult(success=True, output="\n".join(lines))

    def _search_sync(self, query: str, num_results: int) -> list[dict[str, Any]]:
        with DDGS() as ddgs:
            # DDGS.text is a generator; cap length to num_results.
            out: list[dict[str, Any]] = []
            for r in ddgs.text(query, max_results=num_results):
                out.append(r)
                if len(out) >= num_results:
                    break
            return out


class WebFetchTool(BaseTool):
    """Fetch a URL and return its content as truncated Markdown."""

    name = "web_fetch"
    description = (
        "Fetch a URL via HTTP and convert the HTML body to Markdown. "
        "Use this to read articles, docs, or pages whose text you want to reason about."
    )
    parameters = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Absolute URL starting with http:// or https://",
            },
            "max_length": {
                "type": "integer",
                "description": f"Max characters to return (default {DEFAULT_MAX_LENGTH}).",
                "default": DEFAULT_MAX_LENGTH,
            },
        },
        "required": ["url"],
    }

    def __init__(self, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.timeout = float(timeout)
        self._client = httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=True,
            headers={"User-Agent": "MyBotAgent/0.1 (+https://github.com/walkxing-glitch)"},
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def execute(self, **params) -> ToolResult:
        url = params.get("url")
        if not url or not isinstance(url, str):
            return ToolResult(success=False, output="", error="Missing required parameter 'url'.")
        if not (url.startswith("http://") or url.startswith("https://")):
            return ToolResult(success=False, output="", error="URL must start with http:// or https://")

        try:
            max_length = int(params.get("max_length") or DEFAULT_MAX_LENGTH)
        except (TypeError, ValueError):
            return ToolResult(success=False, output="", error="'max_length' must be an integer.")
        max_length = max(200, min(max_length, 200_000))

        try:
            resp = await self._client.get(url)
        except httpx.TimeoutException:
            return ToolResult(success=False, output="", error=f"Fetch timed out after {self.timeout:.0f}s.")
        except httpx.HTTPError as exc:
            return ToolResult(success=False, output="", error=f"HTTP error: {exc}")
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, output="", error=f"Fetch failed: {exc}")

        if resp.status_code >= 400:
            return ToolResult(
                success=False,
                output="",
                error=f"HTTP {resp.status_code} fetching {url}",
            )

        content_type = resp.headers.get("content-type", "").lower()
        body = resp.text

        # Only convert text-ish responses; otherwise return a short descriptor.
        if "html" in content_type or "xml" in content_type or content_type.startswith("text/"):
            md = self._to_markdown(body)
        else:
            return ToolResult(
                success=True,
                output=f"Non-text response from {url} (Content-Type: {content_type}, {len(body)} bytes).",
            )

        truncated = md[:max_length]
        suffix = "" if len(md) <= max_length else f"\n\n... [truncated {len(md) - max_length} chars]"
        header = f"# Fetched: {url}\n\n"
        return ToolResult(success=True, output=header + truncated + suffix)

    def _to_markdown(self, html: str) -> str:
        if html2text is None:
            # Fallback: strip tags crudely.
            import re as _re
            text = _re.sub(r"<script.*?</script>", "", html, flags=_re.DOTALL | _re.IGNORECASE)
            text = _re.sub(r"<style.*?</style>", "", text, flags=_re.DOTALL | _re.IGNORECASE)
            text = _re.sub(r"<[^>]+>", "", text)
            return text.strip()
        h = html2text.HTML2Text()
        h.ignore_images = True
        h.ignore_emphasis = False
        h.ignore_links = False
        h.body_width = 0  # don't wrap
        return h.handle(html).strip()


tools = [WebSearchTool(), WebFetchTool()]
