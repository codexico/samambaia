"""Web search (DDGS) and clean webpage reader tools with configurable limits."""

from __future__ import annotations

import re
from typing import Any, Callable

import bs4
import httpx
from ddgs import DDGS

from samambaia.config import AssistantConfig, load_config


def make_web_tools(cfg: AssistantConfig | None = None) -> list[Callable[..., Any]]:
    active_cfg = cfg or load_config()
    default_count = active_cfg.limits.web_search_default_results
    max_page_chars = active_cfg.limits.max_webpage_chars

    def web_search(query: str, max_results: int = default_count) -> str:
        """Searches the web for current documentation, articles, packages, and technical answers.

        Args:
            query: Concise search query.
            max_results: Number of results to return (1 to 10).
        """
        limit = max(1, min(int(max_results), 10))
        try:
            results = list(DDGS().text(query, max_results=limit))
        except Exception as exc:
            return f"Web search error: {type(exc).__name__}: {exc}"

        if not results:
            return "No results found for this query."

        formatted: list[str] = []
        for idx, item in enumerate(results, start=1):
            title = item.get("title", "(untitled)")
            href = item.get("href", "")
            body = item.get("body", "")
            formatted.append(f"{idx}. {title}\n   URL: {href}\n   Snippet: {body}")
        return "\n\n".join(formatted)

    def read_webpage(url: str) -> str:
        """Fetches and extracts clean readable text from a webpage or documentation URL.

        Args:
            url: Full http:// or https:// URL.
        """
        if not url.startswith(("http://", "https://")):
            return "Invalid URL: must start with http:// or https://."

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/json,text/plain;q=0.9,*/*;q=0.8",
        }
        try:
            with httpx.Client(
                timeout=20.0, follow_redirects=True, headers=headers
            ) as client:
                resp = client.get(url)
                resp.raise_for_status()
                content_type = resp.headers.get("content-type", "").lower()
                raw_text = resp.text
        except Exception as exc:
            return f"Failed to fetch {url}: {type(exc).__name__}: {exc}"

        if "json" in content_type or "text/plain" in content_type or "markdown" in content_type:
            cleaned = raw_text
        else:
            soup = bs4.BeautifulSoup(raw_text, "lxml")
            for tag in soup(
                ["script", "style", "noscript", "svg", "nav", "footer", "aside", "iframe"]
            ):
                tag.decompose()
            main_node = soup.find("main") or soup.find("article") or soup.body or soup
            text = main_node.get_text(separator="\n", strip=True)
            cleaned = re.sub(r"\n{3,}", "\n\n", text)

        if len(cleaned) > max_page_chars:
            return (
                cleaned[:max_page_chars]
                + f"\n\n[... content from {url} truncated at {max_page_chars} chars ...]"
            )
        return cleaned or "(Page had no extractable text content.)"

    return [web_search, read_webpage]
