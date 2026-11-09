"""Turn a search/alert hit into a lead dict (the same schema the dashboard reads)."""
import time

from ..dates import parse_date, resolve_reddit_date
from ..leads import attach_identity, key_of
from ..text import clean_serp_snippet, html_to_text
from ..urls import platform_of_url, reddit_thread_id, unwrap

SEARCH_PLATFORMS = ("reddit", "quora")


def search_lead(url, title, snippet, *, query, source, kind, clock, now_iso, date=None, discovered_at=None,
                position=None):
    """A lead for a Reddit thread or Quora question, or None for any other page.

    `date` is the page's own date (Serper); `discovered_at` is only when a crawler saw it (Google Alerts),
    an upper bound that is used when nothing better is known.
    """
    url = unwrap(url or "")
    platform = platform_of_url(url)
    if platform not in SEARCH_PLATFORMS:
        return None
    raw = html_to_text(snippet)
    # Google appends the page's "related posts" sidebar to Reddit snippets; keep only the post text.
    text, had_sidebar = clean_serp_snippet(raw)
    if platform == "reddit":
        post_date, confidence = resolve_reddit_date(reddit_thread_id(url), date, text, clock, now_iso)
        if confidence == "unknown" and discovered_at:
            post_date, confidence = parse_date(discovered_at) or now_iso, "discovered"
    else:  # Quora has no id clock; questions are evergreen anyway
        iso = parse_date(date) if date else None
        found = parse_date(discovered_at) if discovered_at else None
        post_date, confidence = (iso, "approx") if iso else ((found, "discovered") if found else (now_iso, "unknown"))
    lead = {
        "id": 0, "type": "Mention" if kind == "brand" else "Lead",
        "subreddit": "Quora" if platform == "quora" else "reddit", "is_rss": False,
        "title": html_to_text(title), "url": url, "text": text,
        "post_date": post_date, "date_confidence": confidence, "date_found": now_iso,
        "serp_query": query, "source": source,
    }
    if position:
        lead["serp_position"] = position
    if had_sidebar:
        lead["serp_snippet_raw"] = raw
    attach_identity(lead, platform)
    if lead.get("subreddit_name"):
        lead["subreddit"] = lead["subreddit_name"]
    return lead


def run_queries(queries_by_kind, fetch, to_lead, source, delay=0.0, stop_on=()):
    """Run every query, turn rows into leads, de-dupe within the source.

    fetch(query) -> rows; to_lead(row, query, kind, position) -> lead | None.
    Returns (leads, executed) where executed = [{"source", "kind", "query", "results", "raw", ["error"]}]
    (`raw` = rows the source returned, `results` = rows that were Reddit threads / Quora questions).
    """
    leads, executed, seen, first = [], [], set(), True
    for kind in ("brand", "intent"):
        for query in queries_by_kind.get(kind, []):
            if not first and delay:
                time.sleep(delay)
            first = False
            try:
                rows = fetch(query) or []
            except stop_on as e:
                print(f"[{source}] stopping early ({type(e).__name__}): {e}")
                return leads, executed
            except Exception as e:  # noqa: BLE001 - never let one query kill the run
                print(f"[{source}] query failed [{query[:50]}]: {e}")
                executed.append({"source": source, "kind": kind, "query": query, "results": 0,
                                 "error": f"{type(e).__name__}: {e}"[:300]})
                continue
            count, dropped = 0, []
            for position, row in enumerate(rows, start=1):
                lead = to_lead(row, query, kind, position)
                if lead is None:
                    dropped.append(_host_of(row))
                    continue
                k = key_of(lead)
                if k in seen:
                    continue
                seen.add(k)
                count += 1
                leads.append(lead)
            executed.append({"source": source, "kind": kind, "query": query, "results": count, "raw": len(rows)})
            note = f" (dropped non-thread pages: {', '.join(sorted(set(dropped))[:4])})" if dropped and not count else ""
            print(f"[{source}] {query[:60]!r}: {len(rows)} rows, {count} kept{note}")
    return leads, executed


def _host_of(row):
    from urllib.parse import urlparse
    url = row.get("href") or row.get("link") or row.get("url") or ""
    try:
        return urlparse(url).netloc or "?"
    except Exception:  # noqa: BLE001
        return "?"
