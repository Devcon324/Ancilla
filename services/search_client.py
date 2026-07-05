"""
DuckDuckGo web search via the ddgs library. No API key required.
Returns compact snippets for the LLM to phrase into a spoken reply.
"""
import logging
import time

from ddgs import DDGS
from log_fmt import info as log_line, warning as log_warn

log = logging.getLogger("assistant.search")

for _name in ("ddgs", "httpx", "httpcore", "primp"):
    logging.getLogger(_name).setLevel(logging.WARNING)


def search(query: str, max_results: int = 3) -> str:
    log_line(log, "Search", f"DuckDuckGo {query!r}")
    t0 = time.perf_counter()
    try:
        results = DDGS().text(query, max_results=max_results)
        if not results:
            log_line(log, "Search", f"no results ({time.perf_counter() - t0:.2f}s)")
            return "No search results found."
        snippets = []
        for item in results:
            title = item.get("title", "")
            body = item.get("body", "")
            if title or body:
                snippets.append(f"{title}: {body}".strip(": "))
        log_line(
            log, "Search",
            f"{len(snippets)} result(s) ({time.perf_counter() - t0:.2f}s)",
        )
        return "\n".join(snippets) if snippets else "No search results found."
    except Exception as exc:
        log_warn(log, "Search", f"failed: {exc}")
        return f"Search failed: {exc}"
