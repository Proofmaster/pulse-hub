"""URL helpers: unwrap tracking redirects, identify threads/questions, extract subreddits."""
import re
from urllib.parse import urlparse, parse_qs

_REDDIT_HOST = re.compile(r"(^|\.)reddit\.com$", re.I)
_COMMENTS = re.compile(r"/comments/([a-z0-9]+)", re.I)
_SUBREDDIT = re.compile(r"/r/([A-Za-z0-9_]+)", re.I)
_LINKEDIN_ID = re.compile(r"(\d{19})")


def unwrap_f5bot(url):
    """F5Bot emails wrap links as https://f5bot.com/url?u=<encoded target>&i=..&h=..."""
    if not url:
        return url
    try:
        p = urlparse(url)
        if p.netloc.lower().endswith("f5bot.com") and p.path == "/url":
            target = parse_qs(p.query).get("u")
            if target:
                return target[0]
    except Exception:
        pass
    return url


def unwrap_google(url):
    """Google Alerts wraps links as https://www.google.com/url?rct=j&sa=t&url=<target>&ct=ga..."""
    if not url:
        return url
    try:
        p = urlparse(url)
        host = p.netloc.lower()
        if (host.startswith("www.google.") or host.startswith("google.")) and p.path == "/url":
            qs = parse_qs(p.query)
            target = qs.get("url") or qs.get("q")
            if target:
                return target[0]
    except Exception:
        pass
    return url


def unwrap(url):
    return unwrap_google(unwrap_f5bot(url))


def _host(url):
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def is_reddit_url(url):
    host = _host(url)
    return bool(_REDDIT_HOST.search(host)) or host == "redd.it"


def reddit_thread_id(url):
    """Base36 submission id for /comments/<id>/... and redd.it/<id> links, else None."""
    url = unwrap_f5bot(url or "")
    if not is_reddit_url(url):
        return None
    m = _COMMENTS.search(urlparse(url).path)
    if m:
        return m.group(1).lower()
    p = urlparse(url)
    if p.netloc.lower() == "redd.it":
        seg = p.path.strip("/").split("/")[0]
        return seg.lower() or None
    return None


def subreddit_from_url(url):
    url = unwrap_f5bot(url or "")
    if not is_reddit_url(url):
        return None
    m = _SUBREDDIT.search(urlparse(url).path)
    return m.group(1) if m else None


def is_reddit_thread_url(url):
    return reddit_thread_id(url) is not None


_QUORA_HOST = re.compile(r"(^|\.)quora\.com$", re.I)
_QUORA_RESERVED = {
    "profile", "topic", "q", "search", "spaces", "space", "about", "contact", "settings", "careers", "privacy",
    "answer", "answers", "following", "notifications", "messages", "stats", "login", "signup", "main",
    "business", "partners", "press", "ads", "challenges", "log", "share", "poll", "tos", "unanswered",
}


def is_quora_url(url):
    return bool(_QUORA_HOST.search(_host(url)))


def quora_question_slug(url):
    """'How-do-I-compress-a-PDF' for question (and answer) URLs, else None."""
    url = unwrap(url or "")
    if not is_quora_url(url):
        return None
    segs = [s for s in urlparse(url).path.split("/") if s]
    if segs and segs[0].lower() == "unanswered":
        segs = segs[1:]
    if not segs:
        return None
    first = segs[0]
    if first.lower() in _QUORA_RESERVED or "-" not in first:
        return None
    return first


def _quora_lang(url):
    sub = _host(url).split(".quora.com")[0] if ".quora.com" in _host(url) else ""
    return "" if sub in ("", "www", "m") else sub


def platform_of_url(url):
    """'reddit' (threads), 'quora' (questions), 'linkedin', or None for anything else."""
    url = unwrap(url or "")
    if is_reddit_thread_url(url):
        return "reddit"
    if quora_question_slug(url):
        return "quora"
    if "linkedin.com" in _host(url):
        return "linkedin"
    return None


def linkedin_activity_id(url):
    if not url or "linkedin.com" not in url.lower():
        return None
    m = _LINKEDIN_ID.search(url)
    return m.group(1) if m else None


def thread_key(url):
    """Stable identity for de-duplication across sources and URL variants.

    www/old/np/sh hosts, tracking params, trailing slashes and F5Bot redirect
    wrappers all collapse to the same key; comment-permalinks collapse to their thread.
    """
    url = unwrap((url or "").strip())
    if not url:
        return ""
    tid = reddit_thread_id(url)
    if tid:
        return f"reddit:{tid}"
    lid = linkedin_activity_id(url)
    if lid:
        return f"linkedin:{lid}"
    slug = quora_question_slug(url)
    if slug:
        lang = _quora_lang(url)
        return f"quora:{lang + ':' if lang else ''}{slug.lower()}"
    try:
        p = urlparse(url)
        host = re.sub(r"^(www|m)\.", "", p.netloc.lower())
        return f"{host}{p.path.rstrip('/')}"
    except Exception:
        return url.split("?")[0].rstrip("/").lower()


def mention_key(url):
    """Like thread_key, but distinguishes individual Reddit comments (F5Bot alerts are per comment)."""
    key = thread_key(url)
    if not key.startswith("reddit:"):
        return key
    url = unwrap_f5bot((url or "").strip())
    try:
        segs = [s for s in urlparse(url).path.split("/") if s]
        i = segs.index("comments")
        rest = segs[i + 2:]  # after <id>
        if rest and rest[0] == "c" and len(rest) > 1:
            return f"{key}:{rest[1].lower()}"
        if len(rest) >= 2:  # <slug>/<comment id>
            return f"{key}:{rest[1].lower()}"
    except (ValueError, IndexError):
        pass
    return key
