import json
import re

from pulse import classifier, config, slack
from pulse.leads import build_known_index, key_of, merge_by_key, reuse_classification

from tests.conftest import make_client

TEMPLATE = "{{SOURCE_NOTE}}\n{{POSTS}}"


def good(*cats):
    return json.dumps([{"index": i, "posture": "p", "action_category": c, "sentiment": "neutral",
                        "reply_angle": "do x" if c != "Ignore" else "whatever"} for i, c in enumerate(cats)])


def posts(n):
    return [{"title": f"t{i}", "text": f"x{i}", "url": f"https://www.reddit.com/r/a/comments/1aaa{i}/t/"} for i in range(n)]


# ---------- prompt ----------
def test_client_prompt_is_fully_rendered_except_runtime_slots(client):
    tpl = classifier.render_client_prompt(client)
    assert set(re.findall(r"{{\w+}}", tpl)) == {"{{SOURCE_NOTE}}", "{{POSTS}}"}
    for needle in ("Tinydoc CAN: compress documents; merge documents", "CANNOT: video editing", "SquashIt",
                   '"tinydoc", "tiny doc"', "is NOT a Tinydoc mention", '"Best way to shrink a 30MB doc?" -> Prime Target'):
        assert needle in tpl
    for c in classifier.CATEGORIES:
        assert c in tpl
    built = classifier.build_prompt(posts(2), "quora", tpl)
    assert "INDEX: 1" in built and "Quora" in built and "{{" not in built


def test_optional_prompt_parts_disappear_cleanly():
    bare = make_client(description="", competitors=[], examples=[], cannot=[])
    tpl = classifier.render_client_prompt(bare)
    assert "COMPETITORS" not in tpl and "for Tinydoc." in tpl and "anything not listed under CAN" in tpl


# ---------- parsing & validation ----------
def test_extract_json_array_handles_fences_and_chatter():
    assert classifier.extract_json_array('```json\n[{"a":1}]\n```') == [{"a": 1}]
    assert classifier.extract_json_array('Sure! [{"a":1}] hope that helps') == [{"a": 1}]
    assert classifier.extract_json_array('{"results":[{"a":1}]}') == [{"a": 1}]
    assert classifier.extract_json_array("nothing") == []


def test_validation_rejects_bad_rows_and_normalises_case():
    data = [{"index": 0, "action_category": "prime target", "sentiment": "POSITIVE", "reply_angle": "x", "posture": "p"},
            {"index": 1, "action_category": "Bogus", "reply_angle": "x"},
            {"index": 9, "action_category": "Monitor"},
            {"index": "1", "action_category": "Ignore", "reply_angle": "blah"}]
    out = classifier.validate_results(data, size=3)
    assert out[0]["action_category"] == "Prime Target" and out[0]["sentiment"] == "Positive"
    assert 9 not in out and out[1]["reply_angle"] == "None"


# ---------- fallback chain & never dropping leads ----------
def test_provider_fallback_only_retries_the_missing_items():
    calls = []

    def flaky(prompt):
        calls.append("a")
        return json.dumps([{"index": 0, "posture": "p", "action_category": "Monitor", "sentiment": "Neutral", "reply_angle": "r"}])

    def backup(prompt):
        calls.append("b")
        return good("Prime Target")

    chunk = posts(2)
    assert classifier.classify_chunk(chunk, providers=[("a", flaky), ("b", backup)], template=TEMPLATE) == 2
    assert calls == ["a", "b"]
    assert chunk[0]["action_category"] == "Monitor" and chunk[1]["action_category"] == "Prime Target"


def test_total_outage_marks_pending_instead_of_dropping():
    def boom(prompt):
        raise RuntimeError("503")

    chunk = posts(3)
    classifier.classify_chunk(chunk, providers=[("g", boom)], template=TEMPLATE)
    assert all(c["ai_status"] == "pending" and c["ai_attempts"] == 1 for c in chunk)
    assert classifier.classify_items(chunk, providers=[], delay=0, template=TEMPLATE) == (0, 3)


def test_known_threads_reuse_classification_without_ai():
    old = {"url": "https://old.reddit.com/r/a/comments/1aaa0/t/?x=1", "action_category": "PR Support",
           "reply_angle": "help", "grade": "High", "sentiment": "Negative", "ai_status": "ok"}
    fresh = posts(1)[0]
    assert reuse_classification(fresh, build_known_index([old]))
    assert fresh["action_category"] == "PR Support" and not classifier.needs_ai(fresh)


def test_merge_keeps_new_and_retains_unseen_old():
    a = {"url": "https://www.reddit.com/r/a/comments/1aaa0/t/", "v": "new"}
    a_old = {"url": "https://old.reddit.com/r/a/comments/1aaa0/t/", "v": "old"}
    b_old = {"url": "https://www.reddit.com/r/a/comments/1bbb1/t/", "v": "old"}
    assert [m["v"] for m in merge_by_key([a], [a_old, b_old])] == ["new", "old"]


# ---------- slack ----------
def lead(cat, text="hello", status="ok", **kw):
    d = {"title": "T", "text": text, "url": "https://www.reddit.com/r/a/comments/1zzz9/t/",
         "action_category": cat, "ai_status": status}
    d.update(kw)
    return d


def test_policy_alerts_every_brand_mention_and_prime_targets(client):
    m = client.is_mention
    assert slack.should_alert(lead("Ignore", text="I used Tinydoc yesterday"), m)[0]
    assert slack.should_alert(lead("Prime Target"), m)[0]
    assert not slack.should_alert(lead("Ignore"), m)[0]
    assert not slack.should_alert(lead("Brand Engagement", text="great tool"), m)[0]
    assert not slack.should_alert(lead(None, status="pending"), m)[0]
    assert slack.should_alert(lead(None, status="pending", text="tiny doc broke"), m)[0]
    assert not slack.should_alert(lead(None, text="a tiny doc file"), m)[0]  # plain English


def test_alert_on_is_configurable(client):
    m = client.is_mention
    assert slack.should_alert(lead("PR Support"), m, ["PR Support"])[0]
    assert not slack.should_alert(lead("Ignore", text="tinydoc"), m, ["Prime Target"])[0]


def test_dispatch_uses_the_clients_own_webhook_and_dedupes(client, monkeypatch):
    monkeypatch.setenv("PULSE_SECRETS", json.dumps({"SLACK_WEBHOOK_TINYDOC": "https://hooks.example/tiny"}))
    sent, got = slack.SentLog(), []
    items = [lead("Prime Target", url=f"https://www.reddit.com/r/a/comments/1z{i}/t/") for i in range(5)]
    sender = lambda l, w, n: got.append((w, n)) or True  # noqa: E731
    assert slack.dispatch_alerts(items, sent, client, max_per_run=3, delay=0, sender=sender) == 3
    assert got[0] == ("https://hooks.example/tiny", "Tinydoc")
    assert slack.dispatch_alerts(items, sent, client, max_per_run=10, delay=0, sender=sender) == 2
    sent2 = slack.SentLog()
    assert slack.dispatch_alerts(items[:1], sent2, client, delay=0, sender=lambda l, w, n: False) == 0
    assert not sent2.has(items[0])  # failed delivery is retried next run


def test_missing_webhook_skips_without_marking_sent(client, monkeypatch):
    monkeypatch.delenv("PULSE_SECRETS", raising=False)
    monkeypatch.delenv("SLACK_WEBHOOK_TINYDOC", raising=False)
    sent = slack.SentLog()
    assert slack.dispatch_alerts([lead("Prime Target")], sent, client, delay=0) == 0
    assert not sent.has(lead("Prime Target"))


def test_secret_lookup_prefers_env_then_json_blob(monkeypatch):
    monkeypatch.setenv("PULSE_SECRETS", json.dumps({"A": "from-json"}))
    assert config.secret("A") == "from-json"
    monkeypatch.setenv("A", "from-env")
    assert config.secret("A") == "from-env"
    monkeypatch.setenv("PULSE_SECRETS", "not json")
    assert config.secret("B") is None and config.secret("") is None


def test_payload_is_escaped_and_names_client_and_source():
    p = slack.build_payload(lead("PR Support", title="<b>&x", subreddit_name="pdf", platform="reddit",
                                 reply_angle="apologise"), "Tinydoc")
    blocks = p["attachments"][0]["blocks"]
    assert "[Tinydoc] PR Support" in blocks[0]["text"]["text"] and "r/pdf" in blocks[0]["text"]["text"]
    assert "&lt;b&gt;&amp;x" in blocks[1]["text"]["text"]
    q = slack.build_payload(lead("Prime Target", platform="quora"))
    assert "Quora" in q["attachments"][0]["blocks"][0]["text"]["text"]


def test_no_webhook_or_real_client_data_in_the_public_repo():
    # Real profiles and results live in the private data repo; this repo ships only fictional examples.
    forbidden = ["hooks.slack.com" + "/services", "small" + "pdf"]
    for f in config.ROOT.rglob("*"):
        if f.is_file() and ".git" not in f.parts and f.suffix in (".py", ".json", ".yml", ".md", ".txt"):
            text = f.read_text(errors="ignore").lower()
            for needle in forbidden:
                assert needle not in text, f"{needle!r} found in {f}"
