"""
DuckDuckGo web search via the ddgs library. No API key required.
Returns labeled snippets for the LLM to phrase into a spoken reply.
"""
import logging
import time
from dataclasses import dataclass
from urllib.parse import urlparse

from ddgs import DDGS
from ancilla.log_fmt import info as log_line, warning as log_warn

log = logging.getLogger("assistant.search")

for _name in ("ddgs", "httpx", "httpcore", "primp"):
    logging.getLogger(_name).setLevel(logging.WARNING)


@dataclass(frozen=True)
class SearchResults:
    context: str
    sources: tuple[str, ...]


def _source_label(title: str, href: str) -> str:
    title = title.strip()
    if " - " in title:
        return title.rsplit(" - ", 1)[-1].strip()
    if title and len(title) <= 48:
        return title
    host = urlparse(href).netloc.replace("www.", "")
    if host:
        return host.split(".")[0].replace("-", " ").title()
    return "the web"


def search(query: str, max_results: int = 3) -> SearchResults:
    log_line(log, "Search", f"DuckDuckGo {query!r}")
    t0 = time.perf_counter()
    try:
        results = DDGS().text(query, max_results=max_results)
        if not results:
            log_line(log, "Search", f"no results ({time.perf_counter() - t0:.2f}s)")
            return SearchResults("No search results found.", ())

        blocks: list[str] = []
        sources: list[str] = []
        for item in results:
            title = item.get("title", "")
            href = item.get("href", "")
            body = item.get("body", "")
            if not (title or body):
                continue
            label = _source_label(title, href)
            sources.append(label)
            log_line(log, "Search", f"source {label} ({href or 'no url'})")
            blocks.append(f"[{label}]\n{body}".strip())

        elapsed = time.perf_counter() - t0
        if not blocks:
            log_line(log, "Search", f"no usable results ({elapsed:.2f}s)")
            return SearchResults("No search results found.", ())

        log_line(log, "Search", f"{len(blocks)} result(s) ({elapsed:.2f}s)")
        return SearchResults("\n\n".join(blocks), tuple(sources))
    except Exception as exc:
        log_warn(log, "Search", f"failed: {exc}")
        return SearchResults(f"Search failed: {exc}", ())
