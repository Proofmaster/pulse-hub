"""Google Alerts delivered as RSS (free, up to 1,000 alerts per Google account).

Create alerts at google.com/alerts with "Deliver to: RSS feed", e.g. `"acme invoicing" site:reddit.com`
or `"invoice software" site:quora.com`, then paste each feed URL into the profile:
  "google_alerts": {"enabled": true, "feeds": [{"url": "https://www.google.com/alerts/feeds/...", "kind": "brand"}]}
"""
import xml.etree.ElementTree as ET

import requests

from .common import run_queries, search_lead

SOURCE = "google_alerts"
ATOM = "{http://www.w3.org/2005/Atom}"


def fetch(url):
    resp = requests.get(url, timeout=20, headers={"User-Agent": "pulse-hub/1.0 (feed reader)"})
    resp.raise_for_status()
    return parse(resp.content)


def parse(xml_bytes):
    """Atom feed -> [{"url", "title", "snippet", "date"}]."""
    root = ET.fromstring(xml_bytes)
    rows = []
    for entry in root.findall(f"{ATOM}entry"):
        link = entry.find(f"{ATOM}link")
        url = link.attrib.get("href", "") if link is not None else ""
        title = entry.findtext(f"{ATOM}title") or ""
        content = entry.findtext(f"{ATOM}content") or ""
        date = entry.findtext(f"{ATOM}published") or entry.findtext(f"{ATOM}updated")
        if url:
            rows.append({"url": url, "title": title, "snippet": content, "date": date})
    return rows


def collect(client, clock, now_iso):
    feeds = client.source(SOURCE).get("feeds") or []
    by_kind = {"brand": [], "intent": []}
    for feed in feeds:
        by_kind[feed.get("kind", "intent")].append(feed["url"])

    def to_lead(row, query, kind, position):
        # The alert time is when Google *found* the page (can be days late), so it is never a date anchor.
        return search_lead(row["url"], row["title"], row["snippet"], query=query,
                           source=SOURCE, kind=kind, clock=clock, now_iso=now_iso, discovered_at=row.get("date"))

    return run_queries(by_kind, fetch, to_lead, SOURCE)
