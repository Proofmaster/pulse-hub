"""LinkedIn posts via an Apify actor (Apify's free plan includes monthly credits). Optional.

Profile:  "linkedin": {"enabled": true, "keyword": "Acme", "max_results": 15, "min_hours_between_runs": 24}
Only posts from the last 48h that really name the brand are kept.
"""
from .. import config
from ..dates import is_within_hours, linkedin_date_from_url, parse_date
from ..leads import attach_identity

SOURCE = "linkedin"
ACTOR = "apimaestro/linkedin-posts-search-scraper-no-cookies"


def search(keyword, max_results):
    from apify_client import ApifyClient
    client = ApifyClient(config.APIFY_API_TOKEN)
    run = client.actor(ACTOR).call(run_input={"keyword": keyword, "maxResults": max_results,
                                              "sortType": "date_posted"})
    return list(client.dataset(run["defaultDatasetId"]).iterate_items())


def collect(client, clock, now_iso):
    if not config.APIFY_API_TOKEN:
        print("[linkedin] APIFY_API_TOKEN missing - skipping.")
        return [], []
    cfg = client.source(SOURCE)
    keyword = cfg.get("keyword") or client.name
    try:
        items = search(keyword, int(cfg.get("max_results", 15)))
    except Exception as e:  # noqa: BLE001
        print(f"[linkedin] failed: {e}")
        return [], []
    leads, seen = [], set()
    for item in items:
        url = item.get("post_url") or item.get("url") or item.get("link") or ""
        raw_date = (item.get("postedAt") or item.get("date_posted") or item.get("postDate")
                    or item.get("time") or item.get("date"))
        exact = linkedin_date_from_url(url)
        post_date = exact or parse_date(raw_date) or now_iso
        confidence = "exact" if exact else ("approx" if post_date != now_iso else "unknown")
        if not is_within_hours(post_date, 48):
            continue
        author = item.get("author", {})
        title = author.get("name") if isinstance(author, dict) else str(author)
        if not title or title == "{}":
            title = "LinkedIn User"
        text = item.get("text") or item.get("post_content") or item.get("content") or ""
        if not text:
            text = " ".join(str(v) for v in item.values() if isinstance(v, str) and len(v) > 15)
        if not client.is_mention(f"{title} {text}"):
            continue
        if url and url not in seen and text:
            seen.add(url)
            lead = {"id": 0, "type": "LinkedIn", "subreddit": "LinkedIn", "title": title,
                    "text": text[:1000].replace("\n", " "), "url": url, "post_date": post_date,
                    "date_confidence": confidence, "date_found": now_iso, "source": SOURCE}
            attach_identity(lead, "linkedin")
            leads.append(lead)
    return leads, [{"source": SOURCE, "kind": "brand", "query": keyword, "results": len(leads)}]
