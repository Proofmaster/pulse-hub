import email.message

from pulse.dates import RedditIdClock
from pulse.sources import common, f5bot, google_alerts, serper, web

from tests.conftest import fresh_id, make_client

NOW = "2026-11-09T12:00:00+00:00"


def clock():
    return RedditIdClock()


def test_web_collect_keeps_reddit_and_quora_only_and_cleans_snippets(client, monkeypatch):
    rows = {
        '"tinydoc" site:reddit.com': [
            {"href": "https://www.reddit.com/r/docs/comments/1abc23/hi/", "title": "Tinydoc question",
             "body": "Is tinydoc safe? Other post · r/docs. • 2mo ago."},
            {"href": "https://www.reddit.com/user/someone/", "title": "profile", "body": "tinydoc"},
        ],
        'site:reddit.com "compress doc"': [
            {"href": "https://www.quora.com/How-do-I-compress-a-doc", "title": "How do I compress a doc?", "body": "x"},
            {"href": "https://old.reddit.com/r/docs/comments/1abc23/hi/?x=1", "title": "dupe", "body": ""},
            {"href": "https://example.com/blog", "title": "blog", "body": "y"},
        ],
    }
    monkeypatch.setattr(web, "search", lambda q, *a: rows.get(q, []))
    leads, executed = web.collect(client, clock(), NOW)
    assert [x["platform"] for x in leads] == ["reddit", "quora"]  # profile/blog dropped, cross-query dupe dropped
    reddit, quora = leads
    assert reddit["type"] == "Mention" and reddit["text"] == "Is tinydoc safe?" and "serp_snippet_raw" in reddit
    assert reddit["subreddit"] == "docs" and reddit["source"] == "web"
    assert quora["type"] == "Lead" and quora["subreddit"] == "Quora" and quora["date_confidence"] == "unknown"
    assert [(e["kind"], e["results"]) for e in executed] == [("brand", 1), ("intent", 1)]


def test_one_failing_query_does_not_stop_the_others(client, monkeypatch):
    def search(q, *a):
        if "tinydoc" in q:
            raise RuntimeError("boom")
        return [{"href": "https://www.reddit.com/r/a/comments/1abc24/t/", "title": "t", "body": "b"}]
    monkeypatch.setattr(web, "search", search)
    leads, executed = web.collect(client, clock(), NOW)
    assert len(leads) == 1 and [(e["kind"], "error" in e) for e in executed] == [("brand", True), ("intent", False)]


def test_rate_limit_stops_the_source_early():
    class Limited(Exception):
        pass
    calls = []

    def fetch(q):
        calls.append(q)
        raise Limited("slow down")
    leads, executed = common.run_queries({"brand": ["a", "b"], "intent": ["c"]}, fetch, lambda *a: None, "web",
                                         stop_on=(Limited,))
    assert calls == ["a"] and leads == [] and executed == []


def test_serper_without_key_is_skipped(monkeypatch):
    monkeypatch.setattr(serper.config, "SERPER_API_KEY", None)
    c = make_client(sources={"serper": {"enabled": True}})
    assert serper.collect(c, clock(), NOW) == ([], [])


def test_serper_uses_its_own_short_query_list_and_page_dates(monkeypatch):
    monkeypatch.setattr(serper.config, "SERPER_API_KEY", "k")
    seen = []
    monkeypatch.setattr(serper, "search", lambda q, tbs: seen.append(q) or [
        {"link": "https://www.reddit.com/r/a/comments/1abc25/t/", "title": "Tinydoc", "snippet": "tinydoc",
         "date": "Nov 8, 2026", "position": 3}])
    c = make_client(sources={"serper": {"enabled": True, "queries": {"brand": ["only-this"]}}})
    leads, _ = serper.collect(c, clock(), NOW)
    assert seen == ["only-this"]
    assert leads[0]["date_confidence"] == "approx" and leads[0]["post_date"].startswith("2026-11-08")
    assert leads[0]["serp_position"] == 3


ALERT_FEED = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title type="html">Is &lt;b&gt;Tinydoc&lt;/b&gt; down?</title>
    <link href="https://www.google.com/url?rct=j&amp;sa=t&amp;url=https://www.reddit.com/r/docs/comments/%s/down/&amp;ct=ga"/>
    <published>2026-11-09T08:00:00Z</published>
    <content type="html">Tinydoc keeps failing &lt;b&gt;today&lt;/b&gt;</content>
  </entry>
  <entry>
    <title type="html">Best doc compressor?</title>
    <link href="https://www.google.com/url?rct=j&amp;sa=t&amp;url=https://www.quora.com/Best-doc-compressor&amp;ct=ga"/>
    <published>2026-11-09T09:00:00Z</published>
    <content type="html">Which one?</content>
  </entry>
</feed>"""


def test_google_alerts_feed_is_parsed_and_unwrapped(monkeypatch):
    feed = ALERT_FEED.replace(b"%s", b"zzzzzz1")
    rows = google_alerts.parse(feed)
    assert rows[0]["title"] == "Is <b>Tinydoc</b> down?" and rows[0]["date"] == "2026-11-09T08:00:00Z"
    monkeypatch.setattr(google_alerts, "fetch", lambda url: rows)
    c = make_client(sources={"google_alerts": {"enabled": True, "feeds": [
        {"url": "https://www.google.com/alerts/feeds/1/abc", "kind": "brand"}]}})
    leads, executed = google_alerts.collect(c, clock(), NOW)
    reddit, quora = leads
    assert reddit["url"] == "https://www.reddit.com/r/docs/comments/zzzzzz1/down/"
    assert reddit["title"] == "Is Tinydoc down?" and reddit["text"] == "Tinydoc keeps failing today"
    # no clock anchors -> the alert time (an upper bound) is used, labelled as such
    assert reddit["date_confidence"] == "discovered" and quora["date_confidence"] == "discovered"
    assert executed[0]["query"] == reddit["serp_query"] == "https://www.google.com/alerts/feeds/1/abc"


F5_HTML = """<html><body>
<p>Reddit Posts (/r/docs/): <a href="https://f5bot.com/url?u=https%3A%2F%2Fwww.reddit.com%2Fr%2Fdocs%2Fcomments%2F{tid}%2Ft%2F&i=1&h=2">Try Tinydoc or SquashIt</a> by bob Keyword was found in submission title.</p>
<p>Reddit Comments (/r/x/): <a href="https://f5bot.com/url?u=https%3A%2F%2Fwww.reddit.com%2Fr%2Fx%2Fcomments%2F1aaaaaa%2Fc%2Fk1%3Fcontext%3D3&i=3&h=4">Old thread</a> by amy I use acme invoicing daily</p>
</body></html>"""


def test_f5bot_email_parsing_keeps_word_boundaries_and_kind():
    leads = f5bot.parse_email(F5_HTML.format(tid=fresh_id()), NOW, NOW, subject="F5Bot found something")
    post, comment = leads
    assert post["mention_kind"] == "post" and comment["mention_kind"] == "comment"
    assert post["text"] == "Try Tinydoc or SquashIt by bob"  # spaces kept, prefix and keyword note stripped
    assert post["subreddit"] == "docs (F5Bot)" and post["url"].startswith("https://www.reddit.com/r/docs/")
    assert comment["mention_key"].endswith(":k1")


def test_f5bot_alerts_route_to_the_right_clients():
    leads = f5bot.parse_email(F5_HTML.format(tid=fresh_id()), NOW, NOW)
    tiny = make_client()
    acme = make_client("acme", name="Acme", brand={"terms": ["acme invoicing"]},
                       sources={"f5bot": {"enabled": True}})
    off = make_client("off", sources={"f5bot": {"enabled": False}})
    routed = f5bot.route(leads, [tiny, acme, off])
    assert [x["title"] for x in routed["tinydoc"]] == ["Try Tinydoc or SquashIt"]
    assert [x["title"] for x in routed["acme"]] == ["Old thread"]
    assert "off" not in routed
    routed["tinydoc"][0]["title"] = "changed"
    assert leads[0]["title"] != "changed"  # each client gets its own copy


def test_single_f5bot_client_takes_unmatched_alerts():
    leads = f5bot.parse_email(F5_HTML.format(tid=fresh_id()), NOW, NOW)
    assert len(f5bot.route(leads, [make_client()])["tinydoc"]) == 2


def test_f5bot_fetch_without_credentials(monkeypatch):
    monkeypatch.setattr(f5bot.config, "EMAIL_USER", None)
    assert f5bot.fetch(NOW) == ([], [])


def test_email_body_prefers_html():
    msg = email.message.EmailMessage()
    msg.set_content("plain")
    msg.add_alternative("<p>html</p>", subtype="html")
    assert "html" in f5bot._email_body(msg)


def test_ddgs_no_results_is_empty_but_engine_failure_is_an_error(monkeypatch):
    import ddgs
    from ddgs.exceptions import DDGSException

    class Fake:
        def __init__(self, msg):
            self.msg = msg

        def text(self, *a, **k):
            raise DDGSException(self.msg)
    monkeypatch.setattr(ddgs, "DDGS", lambda: Fake("No results found."))
    assert web.search("q") == []
    monkeypatch.setattr(ddgs, "DDGS", lambda: Fake("RequestError: proxy CONNECT failed: 403"))
    import pytest
    with pytest.raises(DDGSException):
        web.search("q")
