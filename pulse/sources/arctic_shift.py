"""New Reddit posts from target subreddits via Arctic Shift (free community archive API, no key).

Verified working from GitHub Actions (the free web search engines are not: they bot-block data centres).
Gives full post text and exact post times, which also calibrate the Reddit id -> date clock.

Profile:
  "arctic_shift": {"enabled": true,
                   "subreddits": ["pdf", "techsupport", ...],
                   "keywords": ["pdf"],        # keep posts whose title/text contains any of these ([] = keep all)
                   "lookback_hours": 8,        # window per run (runs are 6h apart; overlap is de-duplicated)
                   "max_pages": 2}             # x100 posts per subreddit per run

Arctic Shift is a free service with no uptime guarantee ("be considerate"): one request per subreddit page,
a pause between requests, and the source stops for this run on HTTP 429.
"""
import re
import time
from datetime import datetime, timedelta, timezone

import requests

from ..dates import to_ts
from ..leads import attach_identity
from .common import run_queries

SOURCE = "arctic_shift"
API = "https://arctic-shift.photon-reddit.com/api/posts/search"
HEADERS = {"User-Agent": "pulse-hub/1.0 (lead radar; low volume; github.com/Proofmaster/pulse-hub)"}
GONE = ("[removed]", "[deleted]")


class RateLimited(Exception):
    pass


RETRY_STATUSES = (422, 500, 502, 503, 504)  # 422 = "query timed out / busy" on this service; it is transient
RETRY_WAITS = (4, 10)


def _get(params):
    """One API call, retried when the service is busy. 429 stops the source for this run."""
    for attempt in range(len(RETRY_WAITS) + 1):
        resp = requests.get(API, params=params, headers=HEADERS, timeout=30)
        if resp.status_code == 429:
            raise RateLimited(f"HTTP 429 (reset in {resp.headers.get('X-RateLimit-Reset', '?')}s)")
        if resp.status_code not in RETRY_STATUSES:
            break
        if attempt < len(RETRY_WAITS):
            time.sleep(RETRY_WAITS[attempt])
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code} after retries: {resp.text[:160].strip()}")
    return resp


def fetch_subreddit(subreddit, after_ts, max_pages=2, delay=1.0):
    """Posts in r/<subreddit> created after `after_ts` (epoch seconds), newest first, up to max_pages*100."""
    posts, before = [], None
    for page in range(max_pages):
        if page and delay:
            time.sleep(delay)
        params = {"subreddit": subreddit, "after": int(after_ts), "limit": 100, "sort": "desc"}
        if before:
            params["before"] = int(before)
        resp = _get(params)
        body = resp.json()
        if isinstance(body, dict) and body.get("error"):
            raise RuntimeError(str(body["error"])[:200])
        batch = body.get("data", []) if isinstance(body, dict) else body
        batch = [p for p in batch or [] if isinstance(p, dict)]
        posts += batch
        if len(batch) < 100:
            break
        before = min(float(p.get("created_utc") or 0) for p in batch)
    return posts


def keyword_pattern(keywords):
    """Match a keyword at the START of a word: "pdf" finds "PDFs" and "pdf2word", "esign" finds "esignature"
    but not "design" (a plain substring test let "web design" posts through)."""
    if not keywords:
        return None
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(re.escape(k.strip().lower()) for k in keywords) + ")", re.I)


def _matches(text, pattern):
    return pattern is None or bool(pattern.search(text))


def post_to_lead(post, client, clock, now_iso, keywords):
    pid = str(post.get("id") or "").lower()
    sub = post.get("subreddit") or ""
    if not pid or not sub or post.get("removed_by_category"):
        return None
    title = (post.get("title") or "").strip()
    selftext = (post.get("selftext") or "").strip()
    if selftext in GONE:
        return None  # removed by mods / deleted: nothing to reply to
    is_mention = client.is_mention(f"{title} {selftext}")
    if not (is_mention or _matches(f"{title} {selftext}", keywords)):
        return None  # the keyword filter never drops a post that names the brand
    try:
        created = datetime.fromtimestamp(float(post["created_utc"]), timezone.utc)
    except (KeyError, TypeError, ValueError):
        return None
    clock.add(pid, created.timestamp())  # exact time: the best possible anchor for the id->date estimator
    permalink = post.get("permalink") or f"/r/{sub}/comments/{pid}/"
    url = permalink if permalink.startswith("http") else f"https://www.reddit.com{permalink}"
    text = " ".join(selftext.split())[:1500]
    lead = {
        "id": 0, "type": "Mention" if is_mention else "Lead",
        "subreddit": sub, "is_rss": False, "title": title, "url": url, "text": text,
        "post_date": created.isoformat(), "date_confidence": "exact", "date_found": now_iso,
        "serp_query": f"r/{sub}", "source": SOURCE,
        "author": post.get("author"), "num_comments": post.get("num_comments"),
    }
    attach_identity(lead, "reddit")
    return lead


def collect(client, clock, now_iso):
    cfg = client.source(SOURCE)
    subreddits = [s.strip().removeprefix("r/") for s in cfg.get("subreddits") or [] if s and s.strip()]
    if not subreddits:
        print("[arctic_shift] no subreddits configured - skipping.")
        return [], []
    keywords = keyword_pattern([k for k in cfg.get("keywords") or [] if k and k.strip()])
    now_ts = to_ts(now_iso) or datetime.now(timezone.utc).timestamp()
    after_ts = now_ts - timedelta(hours=float(cfg.get("lookback_hours", 8))).total_seconds()
    max_pages = int(cfg.get("max_pages", 2))
    delay = float(cfg.get("delay_seconds", 1.0))

    def to_lead(post, query, kind, position):
        return post_to_lead(post, client, clock, now_iso, keywords)

    # The "query" is "r/<sub>" everywhere (leads, executed, query_stats), so per-subreddit yield lines up.
    return run_queries({"intent": [f"r/{s}" for s in subreddits]},
                       lambda q: fetch_subreddit(q[2:], after_ts, max_pages, delay),
                       to_lead, SOURCE, delay=delay, stop_on=(RateLimited,))
