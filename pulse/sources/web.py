"""Free web search through the `ddgs` metasearch library (DuckDuckGo, Bing, Brave, Google, ...). No key.

Profile:  "web": {"enabled": true, "timelimit": "w", "max_results": 20, "region": "us-en", "delay_seconds": 2,
                  "backend": "duckduckgo,brave,yahoo,startpage"}
Queries come from the profile's top-level `queries` (or `sources.web.queries`).

`backend` is explicit on purpose: ddgs' "auto" queries Wikipedia/Grokipedia first and ranks their pages on top,
and those can fill the whole result quota with pages that are never Reddit threads or Quora questions.
(Mojeek is left out because it ignores the time limit.)
"""
DEFAULT_BACKEND = "duckduckgo,brave,yahoo,startpage"
from .common import run_queries, search_lead

SOURCE = "web"


def search(query, timelimit="w", max_results=20, region="us-en", backend=DEFAULT_BACKEND):
    from ddgs import DDGS  # imported lazily so tests and other sources never need it
    from ddgs.exceptions import DDGSException
    try:
        return DDGS().text(query, region=region, timelimit=timelimit or None, max_results=max_results,
                           backend=backend or DEFAULT_BACKEND)
    except DDGSException as e:
        # ddgs raises the same exception for "nothing found" and "every engine failed"; only the first is OK.
        if str(e).strip().rstrip(".").lower() == "no results found":
            return []
        raise


def _rate_limit_errors():
    try:
        from ddgs.exceptions import RatelimitException
        return (RatelimitException,)
    except Exception:  # noqa: BLE001
        return ()


def collect(client, clock, now_iso):
    cfg = client.source(SOURCE)
    queries = {k: client.queries_for(SOURCE, k) for k in ("brand", "intent")}

    def fetch(query):
        return search(query, cfg.get("timelimit", "w"), int(cfg.get("max_results", 20)), cfg.get("region", "us-en"),
                      cfg.get("backend", DEFAULT_BACKEND))

    def to_lead(row, query, kind, position):
        return search_lead(row.get("href"), row.get("title"), row.get("body"), query=query, source=SOURCE,
                           kind=kind, clock=clock, now_iso=now_iso, position=position)

    return run_queries(queries, fetch, to_lead, SOURCE, delay=float(cfg.get("delay_seconds", 2)),
                       stop_on=_rate_limit_errors())
