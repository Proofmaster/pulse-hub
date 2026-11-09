"""Text hygiene applied before brand matching and AI classification.

- Google's snippet of a Reddit thread often ends with the page's "related posts" sidebar
  ("... 6. 32. Be careful with X subscriptions · r/pdf. • 10mo ago."). That is not the post.
- Brand names that are also ordinary words ("tiny doc", "wave", "notion") need context to
  tell a mention from plain English. `BrandMatcher` does that from the client profile.
- F5Bot alerts carry a "Reddit Posts (/r/x/):" prefix and a "Keyword was found in ..." note.
"""
import re

# Words that, right before an ambiguous brand term, show it is used as ordinary English
# ("a tiny doc file", "very tiny doc files", "3 tiny doc pages").
DEFAULT_NOT_BEFORE = (
    "a", "an", "the", "one", "this", "that", "these", "those", "my", "your", "our", "their", "his", "her",
    "its", "very", "tiny", "super", "really", "quite", "relatively", "fairly", "extremely", "too", "so",
    "reasonably", "nice", "quick", "simple", "single", "few", "several", "some", "two", "three", "many",
)


def _words_regex(words):
    return "|".join(r"[\s\-_]+".join(re.escape(p) for p in w.split()) for w in words if w and w.strip())


class BrandMatcher:
    """Decides whether a text really names the client's brand.

    terms            distinctive spellings that are always a mention, matched anywhere ("tinydoc")
    ambiguous_terms  spellings that are also ordinary words ("tiny doc"); a mention unless the
                     surrounding words show ordinary use (not_before / not_after)
    false_positives  phrases that are never the brand ("acme corp" for an "Acme" client)
    """

    def __init__(self, terms, ambiguous_terms=(), not_before=None, not_after=(), false_positives=()):
        self.tight = [re.compile(re.escape(t.strip()), re.I) for t in terms if t and t.strip()]
        self.loose = [re.compile(r"\b(?:" + _words_regex([t]) + r")\b", re.I)
                      for t in ambiguous_terms if t and t.strip()]
        before = list(DEFAULT_NOT_BEFORE if not_before is None else not_before)
        self.not_before = (re.compile(r"\b(?:" + _words_regex(before) + r"|\d+)\s*$", re.I)
                           if before else re.compile(r"\b\d+\s*$"))
        self.not_after = re.compile(r"^\s*(?:" + _words_regex(not_after) + r")\b", re.I) if not_after else None
        self.false_pos = re.compile(_words_regex(false_positives), re.I) if false_positives else None

    def __call__(self, text):
        if not text:
            return False
        if self.false_pos is not None:
            text = self.false_pos.sub(" ", text)
        if any(p.search(text) for p in self.tight):
            return True
        for pattern in self.loose:
            for m in pattern.finditer(text):
                if self.not_before.search(text[:m.start()]):
                    continue
                if self.not_after is not None and self.not_after.match(text[m.end():]):
                    continue
                return True
        return False


# --- SERP snippet cleaning ------------------------------------------------
_SIDEBAR_MARK = re.compile(
    r"(·\s*r/\w+"                                            # "Title · r/pdf"
    r"|\br/\w+\.?\s*•"                                       # "r/degoogle. •"
    r"|•\s*\d+\s*(?:mo|yr|y|d|h|w|min|m)s?\.?\s*ago)",       # "• 10mo ago"
    re.I)
_SEGMENT_SPLIT = re.compile(r"(?<=[.!?…])\s+|\s+\.\.\.\s*|\s+-\s+")
_COUNTER = re.compile(r"^\d+[.,]?$")  # stray up-vote / comment counters ("10.", "27.")


def clean_serp_snippet(snippet):
    """Return (clean_text, had_sidebar). Keeps only the text before the related-posts block."""
    text = re.sub(r"\s+", " ", snippet or "").strip()
    if not text:
        return "", False
    m = _SIDEBAR_MARK.search(text)
    if not m:
        return text, False
    segments = [s for s in _SEGMENT_SPLIT.split(text[:m.start()]) if s and s.strip()]
    # The segment right before the marker is the related post's own title: drop it.
    if segments:
        segments.pop()
    # Related items look like "Title. 7. 17." (up-votes, comments): a title followed by counters is
    # also sidebar, so keep peeling until the remaining tail is real post text.
    while segments and _COUNTER.match(segments[-1].strip()):
        while segments and _COUNTER.match(segments[-1].strip()):
            segments.pop()
        if segments:
            segments.pop()
    kept = " ".join(s.strip() for s in segments).strip(" .·•-")
    return kept, True


# --- F5Bot alert rows -----------------------------------------------------
_F5_PREFIX = re.compile(r"^\s*Reddit\s+(Posts|Comments)\s*\(/r/[^)]*\)\s*:\s*", re.I)
_F5_KEYWORD_NOTE = re.compile(r"\s*Keyword was found in submission (?:title|url|text)\.?\s*", re.I)


def f5bot_kind(text):
    """'post' / 'comment' from the "Reddit Posts (/r/x/):" prefix F5Bot puts on every alert, else None."""
    m = _F5_PREFIX.match(text or "")
    if not m:
        return None
    return "post" if m.group(1).lower() == "posts" else "comment"


def clean_f5bot_text(text):
    text = _F5_PREFIX.sub("", text or "", count=1)
    return _F5_KEYWORD_NOTE.sub(" ", text).strip()


def html_to_text(html):
    """Strip tags and collapse whitespace (Google Alerts titles/snippets come as HTML)."""
    text = re.sub(r"<[^>]+>", " ", html or "")
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&quot;", '"')
            .replace("&#39;", "'").replace("&lt;", "<").replace("&gt;", ">"))
    return re.sub(r"\s+", " ", text).strip()
