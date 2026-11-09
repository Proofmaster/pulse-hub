"""Lead identity, de-duplication and merge helpers shared by all scrapers."""
from .classifier import copy_classification, is_classified
from .urls import mention_key, reddit_thread_id, subreddit_from_url, thread_key


def is_linkedin(lead):
    platform = lead.get("platform")
    if platform:
        return platform == "linkedin"
    return str(lead.get("subreddit", "")).lower() == "linkedin"  # legacy rows


def is_rss(lead):
    return bool(lead.get("is_rss")) or lead.get("type") == "RSS" or str(lead.get("subreddit", "")).lower() == "rss feed"


def is_f5bot(lead):
    return bool(lead.get("is_f5bot")) or lead.get("type") == "F5Bot"


def key_of(lead):
    """Identity used for de-dupe / classification reuse: the thread, or the comment for F5Bot mentions."""
    return lead.get("mention_key") or mention_key(lead.get("url", ""))


def attach_identity(lead, platform):
    """Add additive identity fields; legacy fields (`subreddit`, `type`, ...) are untouched."""
    url = lead.get("url", "")
    lead["platform"] = platform
    lead["thread_key"] = thread_key(url)
    lead["mention_key"] = mention_key(url)
    if platform == "reddit":
        tid = reddit_thread_id(url)
        if tid:
            lead["thread_id"] = tid
        sub = subreddit_from_url(url)
        if sub:
            lead["subreddit_name"] = sub
    return lead


def _is_genuine(item):
    """Legacy F5Bot / RSS rows only ever carried hard-coded placeholder categories, never a real AI verdict."""
    if item.get("ai_status") == "ok":
        return True
    return not (is_f5bot(item) or is_rss(item))


def build_known_index(*collections):
    """thread_key -> best (classified) previous record, so we never re-spend AI quota on a known thread."""
    known = {}
    for coll in collections:
        for item in coll or []:
            k = key_of(item)
            if not k or not _is_genuine(item):
                continue
            if k not in known or (is_classified(item) and not is_classified(known[k])):
                known[k] = item
    return known


def reuse_classification(item, known):
    """Copy a finished classification from an earlier record of the same thread."""
    if is_classified(item):
        return True
    prev = known.get(key_of(item))
    if prev is not None and prev is not item and is_classified(prev):
        copy_classification(item, prev)
        return True
    return False


def merge_by_key(new_items, prev_items):
    """New records win; previous ones that were not re-fetched are retained. Order: new first."""
    merged, seen = [], set()
    for item in list(new_items) + list(prev_items):
        k = key_of(item)
        if k and k in seen:
            continue
        if k:
            seen.add(k)
        merged.append(item)
    return merged


def dedupe(items):
    return merge_by_key(items, [])
