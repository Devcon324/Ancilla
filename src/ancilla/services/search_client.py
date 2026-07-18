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


def _source_label(title: str, href: str, used: set[str]) -> str:
    title = title.strip()
    host = urlparse(href).netloc.replace("www.", "")
    host_label = ""
    if host:
        host_label = host.split(".")[0].replace("-", " ").title()

    candidates: list[str] = []
    if " - " in title:
        candidates.append(title.rsplit(" - ", 1)[-1].strip())
    if title and len(title) <= 40:
        candidates.append(title)
    if host_label:
        candidates.append(host_label)
    candidates.append("the web")

    for label in candidates:
        if label and label not in used:
            used.add(label)
            return label
    n = 2
    base = host_label or "Source"
    while f"{base} {n}" in used:
        n += 1
    label = f"{base} {n}"
    used.add(label)
    return label


def _dedupe_key(item: dict) -> str:
    href = (item.get("href") or item.get("url") or "").strip().lower()
    if href:
        return href
    return (item.get("title") or "").strip().lower()


def _normalize_item(item: dict) -> dict:
    if "href" not in item and item.get("url"):
        item = {**item, "href": item["url"]}
    if item.get("body") is None:
        item = {**item, "body": ""}
    return item


def _collect_items(query: str, max_results: int) -> list[dict]:
    """Prefer news for current events, then fill with web text results."""
    seen: set[str] = set()
    merged: list[dict] = []

    def _add(batch) -> None:
        for item in batch or []:
            item = _normalize_item(dict(item))
            key = _dedupe_key(item)
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(item)
            if len(merged) >= max_results:
                return

    with DDGS() as ddgs:
        try:
            _add(list(ddgs.news(query, max_results=max_results)))
        except Exception as exc:
            log_warn(log, "Search", f"news lookup failed: {exc}")
        if len(merged) < max_results:
            try:
                _add(list(ddgs.text(query, max_results=max_results)))
            except Exception as exc:
                log_warn(log, "Search", f"text lookup failed: {exc}")
    return merged


def search(query: str, max_results: int = 6) -> SearchResults:
    log_line(log, "Search", f"DuckDuckGo {query!r}")
    t0 = time.perf_counter()
    try:
        results = _collect_items(query, max_results=max_results)

        if not results:
            log_line(log, "Search", f"no results ({time.perf_counter() - t0:.2f}s)")
            return SearchResults("No search results found.", ())

        blocks: list[str] = []
        sources: list[str] = []
        used_labels: set[str] = set()
        for item in results:
            title = (item.get("title") or "").strip()
            href = (item.get("href") or item.get("url") or "").strip()
            body = (item.get("body") or "").strip()
            if not (title or body):
                continue
            label = _source_label(title, href, used_labels)
            sources.append(label)
            log_line(log, "Search", f"source {label} ({href or 'no url'})")
            parts = [p for p in (title, body) if p]
            blocks.append(f"[{label}]\n" + "\n".join(parts))

        elapsed = time.perf_counter() - t0
        if not blocks:
            log_line(log, "Search", f"no usable results ({elapsed:.2f}s)")
            return SearchResults("No search results found.", ())

        log_line(log, "Search", f"{len(blocks)} result(s) ({elapsed:.2f}s)")
        return SearchResults("\n\n".join(blocks), tuple(sources))
    except Exception as exc:
        log_warn(log, "Search", f"failed: {exc}")
        return SearchResults(f"Search failed: {exc}", ())
