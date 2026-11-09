"""Post-date resolution.

Serper frequently returns no date, and the old fallback ("now") made stale threads
look fresh. Reddit submission ids are sequential base36 numbers, so once we know the
real timestamp of a few posts we can estimate the date of any other id. Every resolved
date carries a `date_confidence`: exact | approx | estimated | unknown.
"""
import bisect
import re
from datetime import datetime, timedelta, timezone

_dateparser = None
_REL = re.compile(r"((?:\d+\s+)?(?:days?|hours?|mins?|minutes?|weeks?)\s+ago)", re.I)
_DP_SETTINGS = {"TIMEZONE": "UTC", "RETURN_AS_TIMEZONE_AWARE": True}


def _dp():
    global _dateparser
    if _dateparser is None:
        import dateparser  # slow import, load lazily
        _dateparser = dateparser
    return _dateparser


def parse_date(value):
    """Best-effort parse of absolute or relative dates to an aware ISO string."""
    if not value:
        return None
    try:
        parsed = _dp().parse(str(value), settings=_DP_SETTINGS)
        return parsed.isoformat() if parsed else None
    except Exception:
        return None


def parse_relative_snippet(snippet):
    if not snippet:
        return None
    m = _REL.search(snippet)
    return parse_date(m.group(1)) if m else None


def to_ts(iso):
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


def is_within_hours(iso, hours=48):
    """True when the date is recent. Unparseable dates count as recent (never drop a lead on a bad date)."""
    ts = to_ts(iso) if iso else None
    if ts is None:
        return True
    return (datetime.now(timezone.utc).timestamp() - ts) <= hours * 3600


def linkedin_date_from_url(url):
    """LinkedIn post ids are Snowflake-like: the top bits are a millisecond timestamp."""
    if not url:
        return None
    m = re.search(r"(?:activity-|ugcPost-|urn:li:activity:)?(\d{19})", url)
    if not m:
        return None
    try:
        ts_ms = int(m.group(1)) >> 22
        return datetime.fromtimestamp(ts_ms / 1000.0, timezone.utc).isoformat()
    except Exception:
        return None


class RedditIdClock:
    """Maps Reddit base36 submission ids to timestamps by interpolating known anchors."""

    def __init__(self, anchors=None, max_anchors=400):
        self.max_anchors = max_anchors
        self._by_id = {}
        for a in anchors or []:
            try:
                self._by_id[int(a[0])] = float(a[1])
            except (TypeError, ValueError, IndexError):
                continue

    @staticmethod
    def _to_int(thread_id):
        if isinstance(thread_id, int):
            return thread_id
        try:
            return int(str(thread_id).lower(), 36)
        except (TypeError, ValueError):
            return None

    def add(self, thread_id, ts):
        n = self._to_int(thread_id)
        if n is not None and ts:
            self._by_id[n] = float(ts)

    def __len__(self):
        return len(self._by_id)

    def _sorted(self):
        ids = sorted(self._by_id)
        return ids, [self._by_id[i] for i in ids]

    def estimate(self, thread_id):
        """Estimated unix timestamp, or None when there are too few anchors."""
        r = self._to_int(thread_id)
        ids, ts = self._sorted()
        if r is None or len(ids) < 2:
            return None
        j = bisect.bisect_left(ids, r)
        if j < len(ids) and ids[j] == r:
            return ts[j]
        if 0 < j < len(ids):
            r0, t0, r1, t1 = ids[j - 1], ts[j - 1], ids[j], ts[j]
            if r1 == r0 or t1 < t0:
                return t0
            return t0 + (t1 - t0) * (r - r0) / (r1 - r0)
        two_days = 2 * 86400
        if j == 0:  # older than every anchor: extrapolate with the oldest segment's slope
            r0, t0 = ids[0], ts[0]
            k = next((i for i in range(1, len(ids)) if ts[i] - t0 >= two_days), len(ids) - 1)
            slope = (ts[k] - t0) / (ids[k] - r0)
            return t0 - slope * (r0 - r)
        # newer than every anchor: extrapolate with the newest segment's slope, never into the future
        r1, t1 = ids[-1], ts[-1]
        k = next((i for i in range(len(ids) - 2, -1, -1) if t1 - ts[i] >= two_days), 0)
        slope = (t1 - ts[k]) / (r1 - ids[k])
        return min(t1 + slope * (r - r1), datetime.now(timezone.utc).timestamp())

    def to_list(self):
        """Anchors sorted by id, thinned to `max_anchors` (newest kept densely, old ones spread evenly)."""
        ids, ts = self._sorted()
        pairs = list(zip(ids, ts))
        if len(pairs) > self.max_anchors:
            newest = pairs[-(self.max_anchors * 2 // 3):]
            older = pairs[:-(self.max_anchors * 2 // 3)]
            keep = self.max_anchors - len(newest)
            step = len(older) / keep
            older = [older[int(i * step)] for i in range(keep)]
            pairs = older + newest
        return [[i, t] for i, t in pairs]


def resolve_reddit_date(thread_id, serper_date, snippet, clock, now_iso):
    """Return (iso_date, confidence) using: source date > snippet hint > id estimate > unknown."""
    iso = parse_date(serper_date)
    if iso:
        return iso, "approx"
    iso = parse_relative_snippet(snippet)
    if iso:
        return iso, "approx"
    if thread_id and clock is not None:
        est = clock.estimate(thread_id)
        if est:
            return datetime.fromtimestamp(est, timezone.utc).isoformat(), "estimated"
    return now_iso, "unknown"
