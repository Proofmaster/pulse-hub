"""Shared fixtures. Everything here is synthetic: the public repo never ships real client data."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from pulse.client import Client

# A fictional client whose name is also ordinary English, like "tiny doc".
TINYDOC = {
    "name": "Tinydoc",
    "description": "an online document compressor",
    "brand": {
        "terms": ["tinydoc"],
        "ambiguous_terms": ["tiny doc"],
        "ambiguous_not_after": ["file", "files", "size", "viewer"],
        "note": "\"tiny doc\" used as ordinary words is NOT a Tinydoc mention.",
    },
    "can": ["compress documents", "merge documents"],
    "cannot": ["video editing"],
    "competitors": ["SquashIt"],
    "examples": [{"text": "Best way to shrink a 30MB doc?", "category": "Prime Target"}],
    "queries": {"brand": ["\"tinydoc\" site:reddit.com"], "intent": ["site:reddit.com \"compress doc\""]},
    "sources": {"web": {"enabled": True, "delay_seconds": 0}, "f5bot": {"enabled": True}},  # web: mocked in tests
}


def make_client(slug="tinydoc", **overrides):
    data = json.loads(json.dumps(TINYDOC))
    for k, v in overrides.items():
        data[k] = v
    return Client(slug, data)


@pytest.fixture
def client():
    return make_client()


def b36(n):
    chars = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = ""
    while n:
        n, r = divmod(n, 36)
        out = chars[r] + out
    return out or "0"


BASE_ID = int("1x00000", 36)


def anchored_results(slug="tinydoc"):
    """A prior results file whose date anchors are relative to *now*: thread id BASE_ID + n is estimated
    at (100 - n/1000) hours old, so ids above BASE_ID + 52_000 are fresh (< 48h) and lower ones stale."""
    now = datetime.now(timezone.utc)
    t0 = (now - timedelta(hours=100)).timestamp()
    anchors = [[BASE_ID, t0], [BASE_ID + 100_000, t0 + 100 * 3600]]
    return {"client": slug, "last_updated": now.isoformat(), "brand_alerts": [], "hot_leads": [], "archive": [],
            "f5bot_leads": [], "slack_sent_urls": [], "slack_policy": 2, "date_anchors": anchors}


def fresh_id(k=0):
    """Thread id estimated at ~1-2h old."""
    return b36(BASE_ID + 99_000 - k)


def stale_id(k=0):
    """Thread id estimated at ~90h old."""
    return b36(BASE_ID + 10_000 + k)
