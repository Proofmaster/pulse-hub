import json

import pytest

from pulse import classifier, feedback, pipeline, store
from tools import build_dashboard

from tests.conftest import TINYDOC, anchored_results, fresh_id, make_client
from tests.test_pipeline import BRAND_Q, INTENT_Q, ai_by_title, env, row  # noqa: F401 (fixture reuse)


def write_fb(root, slug, name, items):
    d = root / "feedback" / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(json.dumps({"client": slug, "items": items}))


# ---------- feedback ----------
def test_feedback_latest_file_wins_and_bad_rows_are_ignored(tmp_path, capsys):
    write_fb(tmp_path, "tinydoc", "20261101-000000.json", [
        {"key": "reddit:a", "verdict": "down", "category": "Ignore", "title": "A", "at": "2026-11-01T00:00:00Z"},
        {"key": "reddit:b", "verdict": "maybe"}, {"verdict": "up"}, "junk"])
    write_fb(tmp_path, "tinydoc", "20261102-000000.json", [
        {"key": "reddit:a", "verdict": "down", "category": "Prime Target", "title": "A2", "at": "2026-11-02T00:00:00Z"},
        {"key": "reddit:c", "verdict": "up", "category": "Bogus", "at": "2026-11-02T00:00:00Z"}])
    (tmp_path / "feedback" / "tinydoc" / "zzz.json").write_text("{broken")
    fb = feedback.load(tmp_path / "feedback", "tinydoc")
    assert set(fb) == {"reddit:a", "reddit:c"}
    assert fb["reddit:a"]["category"] == "Prime Target" and fb["reddit:c"]["category"] is None
    assert "skipped" in capsys.readouterr().out
    assert feedback.load(tmp_path / "feedback", "other") == {} and feedback.load(None, "x") == {}


def test_feedback_stamps_leads_and_becomes_prompt_examples(client):
    entries = {
        "reddit:a": {"key": "reddit:a", "verdict": "down", "category": "Ignore", "title": 'I built a "doc" tool',
                     "text": "launch", "at": "2026-11-02T00:00:00Z"},
        "reddit:b": {"key": "reddit:b", "verdict": "up", "category": "Prime Target", "title": "Shrink my doc?",
                     "at": "2026-11-01T00:00:00Z"},
        "reddit:c": {"key": "reddit:c", "verdict": "down", "category": None, "title": "no category", "at": "2026-11-03"},
    }
    leads = [{"thread_key": "reddit:a", "mention_key": "reddit:a", "action_category": "Prime Target"},
             {"thread_key": "reddit:b", "mention_key": "reddit:b", "action_category": "Prime Target"},
             {"thread_key": "reddit:z", "mention_key": "reddit:z"}]
    assert feedback.apply([leads], entries) == 2
    assert leads[0]["human_category"] == "Ignore" and leads[1]["human_category"] == "Prime Target"
    assert "human_verdict" not in leads[2]
    ex = feedback.prompt_examples(entries)
    assert [e["category"] for e in ex] == ["Ignore", "Prime Target"]  # newest first; bare 👎 skipped
    tpl = classifier.render_client_prompt(client, reviewed_examples=ex)
    assert "(reviewed by the team)" in tpl and "I built a 'doc' tool launch" in tpl


def test_pipeline_uses_feedback_from_the_data_repo(env, client, tmp_path):  # noqa: F811
    path, delivered, install = env
    tid = fresh_id(20)
    calls = []
    install({INTENT_Q: [row(tid, "Shrink a doc?", "x")]}, [("fake", ai_by_title({"Shrink a doc?": "Prime Target"}, calls))])
    write_fb(tmp_path, "tinydoc", "1.json", [{"key": f"reddit:{tid}", "verdict": "down", "category": "Ignore",
                                              "title": "Shrink a doc?", "at": "2026-11-09T00:00:00Z"}])
    pipeline.run_client(client, path, feedback_dir=tmp_path / "feedback")
    lead = json.loads(path.read_text())["hot_leads"][0]
    assert lead["action_category"] == "Prime Target" and lead["human_category"] == "Ignore"
    assert "(reviewed by the team)" in calls[0]


# ---------- stale stats ----------
def test_stats_and_health_for_removed_sources_and_queries_are_dropped():
    c = make_client(sources={"arctic_shift": {"enabled": True, "subreddits": ["docs"]}, "web": {"enabled": False}})
    stats = {"arctic_shift|r/docs": {}, "arctic_shift|r/gone": {}, "web|q": {}}
    health = {"arctic_shift": {}, "web": {}}
    s, h = pipeline.prune_stale(c, stats, health)
    assert list(s) == ["arctic_shift|r/docs"] and list(h) == ["arctic_shift"]


# ---------- dashboard bundle ----------
def test_dashboard_bundle_round_trips_and_needs_the_passphrase(tmp_path):
    clients, data = tmp_path / "clients", tmp_path / "data"
    clients.mkdir()
    (clients / "tinydoc.json").write_text(json.dumps(TINYDOC))
    (clients / "nodata.json").write_text(json.dumps({**TINYDOC, "name": "No data yet"}))
    res = anchored_results()
    res["archive"] = [{"title": "T", "url": "https://www.reddit.com/r/a/comments/1abc/t/", "text": "x" * 1000,
                       "action_category": "Prime Target", "slack_only_field": 1}]
    store.save(data / "tinydoc" / "results.json", res)
    out = tmp_path / "site"
    build_dashboard.build(clients, data, out, "a long test passphrase", "owner/data-repo", iterations=1000)
    env_ = json.loads((out / "data.enc.json").read_text())
    assert (out / "index.html").exists() and (out / ".nojekyll").exists()
    assert "Prime Target" not in (out / "data.enc.json").read_text()  # nothing readable without the key
    d = build_dashboard.decrypt(env_, "a long test passphrase")
    assert d["data_repo"] == "owner/data-repo" and [c["slug"] for c in d["clients"]] == ["tinydoc"]
    item = d["clients"][0]["archive"][0]
    assert len(item["text"]) == 280 and "slack_only_field" not in item
    with pytest.raises(Exception):
        build_dashboard.decrypt(env_, "wrong passphrase")
    with pytest.raises(SystemExit):
        build_dashboard.build(clients, data, out, "short", "x")


def test_dashboard_page_has_no_external_resources():
    from pulse.config import ROOT
    html = (ROOT / "dashboard" / "index.html").read_text()
    assert "<script src" not in html and "stylesheet" not in html  # nothing can be swapped in from a CDN
