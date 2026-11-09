import json

import pytest

from pulse.client import ProfileError, load_clients
from pulse.text import BrandMatcher, clean_f5bot_text, clean_serp_snippet, f5bot_kind, html_to_text

from tests.conftest import TINYDOC, make_client


# ---------- brand matcher (driven by the profile) ----------
@pytest.mark.parametrize("text", ["Tinydoc charged me twice", "TINYDOC alternative", "https://tinydoc.app/file#s=1",
                                  "just use tiny doc, it's free", "I used Tiny Doc to shrink it", "Tiny Doc lol"])
def test_real_mentions(client, text):
    assert client.is_mention(text)


@pytest.mark.parametrize("text", ["send me a tiny doc file", "very tiny doc files", "3 tiny doc pages", "a tiny doc viewer", "the tiny doc size is fine", "", None])
def test_ordinary_english_is_not_a_mention(client, text):
    assert not client.is_mention(text)


def test_false_positive_phrases_and_custom_not_before():
    m = BrandMatcher(["acmeinvoicing"], ["acme"], not_before=[], false_positives=["acme corp"])
    assert not m("bought an anvil from Acme Corp")
    assert m("an acme invoice")  # empty not_before list: articles no longer exclude
    assert m("acmeinvoicing rocks")


# ---------- snippet / alert hygiene ----------
def test_sidebar_only_mention_is_removed_from_snippet(client):
    raw = "... 6. 32. Be careful with Tinydoc subscriptions · r/docs. • 10mo ago. Be careful. 10. 27."
    clean, had = clean_serp_snippet(raw)
    assert had and not client.is_mention(clean)


def test_post_text_survives_and_related_titles_with_counters_are_peeled():
    raw = ("I'm looking for a free simple PDF editor. Accessible PDFs for testing? 7. 17. "
           "How to merge PDF files offline · r/PDFgear. • 1mo ago. How to merge PDF fi")
    assert clean_serp_snippet(raw) == ("I'm looking for a free simple PDF editor", True)


def test_snippet_without_sidebar_is_untouched():
    raw = "Does anyone know a good AI-powered PDF editor for iOS or Mac?"
    assert clean_serp_snippet(raw) == (raw, False)


def test_f5bot_text_cleanup():
    t = "Reddit Posts (/r/foss/): IStan - an app alt to tinydoc by bob Keyword was found in submission title. Body"
    assert f5bot_kind(t) == "post" and f5bot_kind("Reddit Comments (/r/x/): y") == "comment"
    assert clean_f5bot_text(t) == "IStan - an app alt to tinydoc by bob Body"
    assert f5bot_kind("other") is None


def test_html_to_text():
    assert html_to_text("<b>Compress</b> &amp; merge&nbsp;PDFs") == "Compress & merge PDFs"


# ---------- profiles ----------
def test_profile_defaults_and_query_resolution(client):
    assert client.slack_secret == "SLACK_WEBHOOK_TINYDOC"
    assert client.alert_on == ["mention", "Prime Target"]
    assert client.queries_for("web", "intent") == ['site:reddit.com "compress doc"']
    assert client.f5bot_keywords() == ["tinydoc", "tiny doc"]  # defaults to the brand terms
    assert client.enabled("web") and not client.enabled("serper")
    own = make_client(sources={"serper": {"enabled": True, "queries": {"brand": ["x"]}}})
    assert own.queries_for("serper", "brand") == ["x"] and own.queries_for("serper", "intent") == []


@pytest.mark.parametrize("patch,msg", [
    ({"name": ""}, "name"),
    ({"brand": {"terms": []}}, "brand.terms"),
    ({"can": []}, "can"),
    ({"sources": {"reddit_rss": {}}}, "unknown source"),
    ({"examples": [{"text": "x"}]}, "example"),
    ({"sources": {"google_alerts": {"feeds": [{"url": "nope"}]}}}, "url"),
])
def test_invalid_profiles_fail_with_a_clear_message(patch, msg):
    with pytest.raises(ProfileError, match=msg):
        make_client(**patch)


def test_load_clients_skips_templates_and_inactive(tmp_path):
    (tmp_path / "tinydoc.json").write_text(json.dumps(TINYDOC))
    (tmp_path / "_draft.json").write_text("{not json")
    (tmp_path / "paused.json").write_text(json.dumps({**TINYDOC, "active": False}))
    assert [c.slug for c in load_clients(tmp_path)] == ["tinydoc"]
    assert [c.slug for c in load_clients(tmp_path, only=["paused"])] == ["paused"]  # explicit wins
    with pytest.raises(ProfileError, match="no profile"):
        load_clients(tmp_path, only=["ghost"])
    (tmp_path / "Bad Name.json").write_text(json.dumps(TINYDOC))
    with pytest.raises(ProfileError, match="slug"):
        load_clients(tmp_path)


def test_shipped_example_profile_is_valid():
    from pulse.config import ROOT
    clients = load_clients(ROOT / "examples")
    assert clients and all(c.can for c in clients)
