"""Google results via Serper (2,500 free credits once, then paid). Optional.

Profile:  "serper": {"enabled": true, "tbs": "qdr:w", "min_hours_between_runs": 12,
                     "queries": {"brand": [...], "intent": [...]}}
Give Serper its own short query list to make the free credits last.
"""
import requests

from .. import config
from .common import run_queries, search_lead

SOURCE = "serper"


def search(query, tbs="qdr:w"):
    response = requests.post(
        "https://google.serper.dev/search",
        headers={"X-API-KEY": config.SERPER_API_KEY, "Content-Type": "application/json"},
        json={"q": query, "tbs": tbs},
        timeout=30,
    )
    response.raise_for_status()
    return response.json().get("organic", [])


def collect(client, clock, now_iso):
    if not config.SERPER_API_KEY:
        print("[serper] SERPER_API_KEY missing - skipping.")
        return [], []
    cfg = client.source(SOURCE)
    queries = {k: client.queries_for(SOURCE, k) for k in ("brand", "intent")}

    def to_lead(row, query, kind, position):
        return search_lead(row.get("link"), row.get("title"), row.get("snippet"), query=query, source=SOURCE,
                           kind=kind, clock=clock, now_iso=now_iso, date=row.get("date"),
                           position=row.get("position", position))

    return run_queries(queries, lambda q: search(q, cfg.get("tbs", "qdr:w")), to_lead, SOURCE)
