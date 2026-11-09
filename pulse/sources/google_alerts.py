"""Google Alerts delivered as RSS (free, up to 1,000 alerts per Google account).

Create alerts at google.com/alerts with "Deliver to: RSS feed", e.g. `"acme invoicing" site:reddit.com`
or `"invoice software" site:quora.com`, then paste each feed URL into the profile:
  "google_alerts": {"enabled": true, "feeds": [{"url": "https://www.google.com/alerts/feeds/..."}]}

`kind` is optional: "auto" (default) makes each item a brand mention when the post names the brand and a lead
otherwise; "brand" / "intent" force it. The feed's own title ("Google Alert - <query>") becomes its label in the
dashboard. A feed with no new items is normal (Google only lists what it found since the alert was created).
"""
import xml.etree.ElementTree as ET

import requests

from .common import run_queries, search_lead

SOURCE = "google_alerts"
ATOM = "{http://www.w3.org/2005/Atom}"


def fetch(url):
    """(feed label, rows)."""
    resp = requests.get(url, timeout=20, headers={"User-Agent": "pulse-hub/1.0 (feed reader)"})
    resp.raise_for_status()
    return feed_label(resp.content), parse(resp.content)


def feed_label(xml_bytes):
    try:
        title = ET.fromstring(xml_bytes).findtext(f"{ATOM}title") or ""
    except ET.ParseError:
        return ""
    return title.split(" - ", 1)[1].strip() if title.lower().startswith("google alert - ") else title.strip()


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
    kinds = {f["url"]: f.get("kind", "auto") for f in feeds}
    labels = {f["url"]: f["label"] for f in feeds if f.get("label")}
    by_kind = {"brand": [u for u, k in kinds.items() if k == "brand"],
               "intent": [u for u, k in kinds.items() if k != "brand"]}

    def fetch_rows(url):
        label, rows = fetch(url)
        labels.setdefault(url, label)
        return rows

    def to_lead(row, query, kind, position):
        if kinds.get(query, "auto") == "auto":
            kind = "brand" if client.is_mention(f"{row['title']} {row['snippet']}") else "intent"
        # The alert time is when Google *found* the page (can be days late), so it is never a date anchor.
        return search_lead(row["url"], row["title"], row["snippet"], query=query,
                           source=SOURCE, kind=kind, clock=clock, now_iso=now_iso, discovered_at=row.get("date"))

    leads, executed = run_queries(by_kind, fetch_rows, to_lead, SOURCE)
    for ex in executed:
        if labels.get(ex["query"]):
            ex["label"] = labels[ex["query"]]
    return leads, executed
