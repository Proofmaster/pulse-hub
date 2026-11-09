"""End-to-end runs of the pipeline / CLI with network and AI mocked."""
import json

import pytest

import run as cli
from pulse import classifier, pipeline, slack, store
from pulse.sources import f5bot, web
from tools import import_legacy

from tests.conftest import TINYDOC, anchored_results, fresh_id, make_client, stale_id

LEGACY = {"id", "title", "url", "text", "post_date", "date_found", "subreddit"}


def row(tid, title, body, sub="docs"):
    return {"href": f"https://www.reddit.com/r/{sub}/comments/{tid}/t/", "title": title, "body": body}


def ai_by_title(mapping, calls=None):
    """Fake provider: answers per post from its title (robust to chunking/re-indexing)."""
    def provider(prompt):
        if calls is not None:
            calls.append(prompt)
        out = []
        for line in prompt.splitlines():
            if line.startswith("INDEX: "):
                idx = int(line.split("|")[0].split(":")[1])
                title = line.split("| Title: ")[1].split(" | Snippet:")[0]
                cat = mapping.get(title, "Ignore")
                out.append({"index": idx, "posture": "p", "action_category": cat, "sentiment": "Neutral",
                            "reply_angle": "None" if cat == "Ignore" else "reply"})
        return json.dumps(out)
    return provider


@pytest.fixture
def env(tmp_path, monkeypatch):
    delivered = []
    monkeypatch.setenv("SLACK_WEBHOOKS", json.dumps({"tinydoc": "https://hooks.example/tiny",
                                                     "acme": "https://hooks.example/acme"}))
    monkeypatch.setattr(slack, "send_slack_alert", lambda lead, hook, name="": delivered.append((hook, lead)) or True)
    monkeypatch.setattr(slack.time, "sleep", lambda s: None)
    monkeypatch.setattr(classifier.time, "sleep", lambda s: None)
    path = tmp_path / "data" / "tinydoc" / "results.json"
    store.save(path, anchored_results())

    def install(rows_by_query, ai):
        monkeypatch.setattr(web, "search", lambda q, *a: rows_by_query.get(q, []))
        monkeypatch.setattr(classifier, "default_providers", lambda: ai)
    return path, delivered, install


BRAND_Q = '"tinydoc" site:reddit.com'
INTENT_Q = 'site:reddit.com "compress doc"'


def test_brand_mention_flows_to_tab_vault_and_slack(env, client):
    path, delivered, install = env
    tid = fresh_id()
    install({BRAND_Q: [row(tid, "Tinydoc ruined my day", "tinydoc charged me twice")]},
            [("fake", ai_by_title({"Tinydoc ruined my day": "PR Support"}))])
    before = json.loads(path.read_text())
    pipeline.run_client(client, path)
    after = json.loads(path.read_text())
    assert set(before) <= set(after)
    lead = after["brand_alerts"][0]
    assert LEGACY <= set(lead) and lead["thread_key"] == f"reddit:{tid}" and lead["grade"] == "High"
    assert lead["date_confidence"] == "estimated"
    assert len(after["archive"]) == 1 and after["client"] == "tinydoc"
    assert [h for h, _ in delivered] == ["https://hooks.example/tiny"]
    pipeline.run_client(client, path)
    assert len(delivered) == 1  # never twice


def test_sidebar_only_brand_hit_becomes_an_ordinary_lead(env, client):
    path, delivered, install = env
    tid = fresh_id(1)
    install({BRAND_Q: [row(tid, "A different editor", "Nice one. Be careful with Tinydoc · r/docs. • 10mo ago.")]},
            [("fake", ai_by_title({}))])
    pipeline.run_client(client, path)
    after = json.loads(path.read_text())
    assert not after["brand_alerts"]
    lead = after["hot_leads"][0]
    assert lead["type"] == "Lead" and lead["brand_unverified"] and "Tinydoc" in lead["serp_snippet_raw"]
    assert not delivered


def test_ai_outage_keeps_leads_and_retries_next_run(env, client):
    path, delivered, install = env
    tid = fresh_id(2)
    install({INTENT_Q: [row(tid, "Best way to shrink a doc?", "how do I compress a doc")]}, [])
    pipeline.run_client(client, path)
    kept = json.loads(path.read_text())["hot_leads"]
    assert len(kept) == 1 and kept[0]["ai_status"] == "pending" and not delivered
    install({}, [("fake", ai_by_title({"Best way to shrink a doc?": "Prime Target"}))])  # result gone from search
    pipeline.run_client(client, path)
    after = json.loads(path.read_text())
    assert after["hot_leads"][0]["action_category"] == "Prime Target"
    assert len(after["archive"]) == 1 and len(delivered) == 1


def test_known_threads_cost_no_ai_calls(env, client):
    path, delivered, install = env
    calls = []
    install({INTENT_Q: [row(fresh_id(3), "Shrink?", "compress")]},
            [("fake", ai_by_title({"Shrink?": "Prime Target"}, calls))])
    pipeline.run_client(client, path)
    n = len(calls)
    pipeline.run_client(client, path)
    assert n == 1 and len(calls) == 1


def test_stale_threads_are_vaulted_but_not_live(env, client):
    path, delivered, install = env
    install({BRAND_Q: [row(stale_id(), "Old Tinydoc thread", "tinydoc rocks")]},
            [("fake", ai_by_title({"Old Tinydoc thread": "Brand Engagement"}))])
    pipeline.run_client(client, path)
    after = json.loads(path.read_text())
    assert not after["brand_alerts"] and len(after["archive"]) == 1 and not delivered


def test_quora_questions_are_leads_with_their_own_prompt_note(env, client):
    path, delivered, install = env
    calls = []
    install({INTENT_Q: [{"href": "https://www.quora.com/How-do-I-shrink-a-doc", "title": "How do I shrink a doc?",
                         "body": "Looking for a tool"}]},
            [("fake", ai_by_title({"How do I shrink a doc?": "Prime Target"}, calls))])
    pipeline.run_client(client, path)
    lead = json.loads(path.read_text())["hot_leads"][0]
    assert lead["platform"] == "quora" and lead["action_category"] == "Prime Target"
    assert "Quora questions" in calls[0] and "Tinydoc CAN" in calls[0]
    assert delivered and "Quora" in json.dumps(slack.build_payload(delivered[0][1]))


def test_first_run_on_an_existing_file_does_not_flood_slack(env, client):
    path, delivered, install = env
    old = anchored_results()
    old.pop("slack_policy")
    old["archive"] = [{"url": f"https://www.reddit.com/r/docs/comments/{fresh_id(4)}/t/", "title": "Tinydoc old",
                       "text": "tinydoc", "action_category": "Monitor", "ai_status": "ok", "grade": "Low"}]
    old["brand_alerts"] = list(old["archive"])
    store.save(path, old)
    install({}, [("fake", ai_by_title({}))])
    pipeline.run_client(client, path)
    assert not delivered


def test_min_hours_between_runs_protects_free_credits(env, monkeypatch):
    path, delivered, install = env
    c = make_client(sources={**TINYDOC["sources"], "serper": {"enabled": True, "min_hours_between_runs": 12}})
    calls = []
    monkeypatch.setattr(pipeline.serper, "collect", lambda *a: calls.append(1) or ([], []))
    install({}, [("fake", ai_by_title({}))])
    pipeline.run_client(c, path, now_iso="2026-11-09T00:00:00+00:00")
    pipeline.run_client(c, path, now_iso="2026-11-09T06:00:00+00:00")
    pipeline.run_client(c, path, now_iso="2026-11-09T11:57:00+00:00")  # cron jitter tolerated
    assert len(calls) == 2
    assert json.loads(path.read_text())["source_last_run"]["serper"] == "2026-11-09T11:57:00+00:00"


def test_query_stats_count_new_and_qualified_threads_once(env, client):
    path, delivered, install = env
    install({INTENT_Q: [row(fresh_id(5), "Shrink?", "x"), row(fresh_id(6), "Meme", "y")]},
            [("fake", ai_by_title({"Shrink?": "Prime Target"}))])
    pipeline.run_client(client, path)
    pipeline.run_client(client, path)
    s = json.loads(path.read_text())["query_stats"][f"web|{INTENT_Q}"]
    assert (s["runs"], s["results"], s["new"], s["new_qualified"]) == (2, 4, 2, 1)
    assert json.loads(path.read_text())["query_stats"][f"web|{BRAND_Q}"]["results"] == 0


def test_f5bot_post_alerts_calibrate_the_date_clock():
    clock = pipeline.seed_clock({"f5bot_leads": [
        {"type": "F5Bot", "url": "https://www.reddit.com/r/a/comments/1xa0000/t/",
         "post_date": "2026-11-01T00:00:00+00:00", "text": "Reddit Posts (/r/a/):t"},
        {"type": "F5Bot", "url": "https://www.reddit.com/r/a/comments/1xb0000/t/",
         "post_date": "2026-11-03T00:00:00+00:00", "mention_kind": "post"},
        {"type": "F5Bot", "url": "https://www.reddit.com/r/a/comments/1aaaaaa/c/x/",
         "post_date": "2026-11-03T00:00:00+00:00", "mention_kind": "comment"},
    ]})
    assert len(clock) == 2


# ---------- CLI: many clients, one inbox ----------
def write_clients(tmp_path):
    d = tmp_path / "clients"
    d.mkdir()
    (d / "tinydoc.json").write_text(json.dumps(TINYDOC))
    acme = {**TINYDOC, "name": "Acme", "brand": {"terms": ["acme invoicing"]},
            "queries": {"brand": ['"acme invoicing" site:reddit.com'], "intent": []}}
    (d / "acme.json").write_text(json.dumps(acme))
    return d


def test_cli_runs_each_client_into_its_own_file_and_marks_inbox_once(env, tmp_path, monkeypatch):
    path, delivered, install = env
    clients_dir, data_dir = write_clients(tmp_path), tmp_path / "data"
    store.save(data_dir / "acme" / "results.json", anchored_results("acme"))
    install({BRAND_Q: [row(fresh_id(7), "Tinydoc rocks", "tinydoc")],
             '"acme invoicing" site:reddit.com': [row(fresh_id(8), "Acme Invoicing broke", "acme invoicing")]},
            [("fake", ai_by_title({"Tinydoc rocks": "Brand Engagement", "Acme Invoicing broke": "PR Support"}))])
    alert = {"url": f"https://www.reddit.com/r/x/comments/{fresh_id(9)}/t/", "title": "acme invoicing rules",
             "text": "acme invoicing", "type": "F5Bot", "is_f5bot": True, "post_date": "2026-11-09T00:00:00+00:00",
             "date_found": "2026-11-09T00:00:00+00:00", "mention_kind": "post"}
    marked = []
    monkeypatch.setattr(f5bot, "fetch", lambda now: ([alert], [b"1"]))
    monkeypatch.setattr(f5bot, "mark_seen", lambda ids: marked.append(ids))
    assert cli.main(["--clients-dir", str(clients_dir), "--data-dir", str(data_dir)]) == 0
    tiny = json.loads((data_dir / "tinydoc" / "results.json").read_text())
    acme = json.loads((data_dir / "acme" / "results.json").read_text())
    assert [x["title"] for x in tiny["brand_alerts"]] == ["Tinydoc rocks"] and not tiny["f5bot_leads"]
    assert [x["title"] for x in acme["brand_alerts"]] == ["Acme Invoicing broke"]
    assert [x["title"] for x in acme["f5bot_leads"]] == ["acme invoicing rules"]
    hooks = sorted(h for h, _ in delivered)
    assert hooks == ["https://hooks.example/acme", "https://hooks.example/acme", "https://hooks.example/tiny"]
    assert marked == [[b"1"]]


def test_cli_failure_in_one_client_keeps_others_and_leaves_inbox_unread(env, tmp_path, monkeypatch):
    path, delivered, install = env
    clients_dir, data_dir = write_clients(tmp_path), tmp_path / "data"
    store.save(data_dir / "tinydoc" / "results.json", anchored_results())
    (data_dir / "acme").mkdir(parents=True)
    (data_dir / "acme" / "results.json").write_text("{corrupt")  # must fail loudly, not be overwritten
    install({BRAND_Q: [row(fresh_id(10), "Tinydoc again", "tinydoc")]}, [("fake", ai_by_title({}))])
    marked = []
    monkeypatch.setattr(f5bot, "fetch", lambda now: ([], [b"1"]))
    monkeypatch.setattr(f5bot, "mark_seen", lambda ids: marked.append(ids))
    assert cli.main(["--clients-dir", str(clients_dir), "--data-dir", str(data_dir)]) == 1
    assert (data_dir / "acme" / "results.json").read_text() == "{corrupt"
    assert json.loads((data_dir / "tinydoc" / "results.json").read_text())["brand_alerts"]
    assert marked == []


def test_cli_validate_and_bad_profile(tmp_path, capsys):
    d = write_clients(tmp_path)
    assert cli.main(["--validate", "--clients-dir", str(d)]) == 0
    (d / "broken.json").write_text(json.dumps({"name": "x"}))
    assert cli.main(["--validate", "--clients-dir", str(d)]) == 2
    assert "broken.json" in capsys.readouterr().out


# ---------- legacy import ----------
def test_legacy_import_merges_history_without_duplicates_or_floods(tmp_path):
    tid_a, tid_b = fresh_id(11), fresh_id(12)
    old = {"archive": [{"url": f"https://www.reddit.com/r/a/comments/{tid_a}/t/", "title": "old"}],
           "brand_alerts": [{"url": f"https://www.reddit.com/r/a/comments/{tid_b}/t/", "title": "live"}],
           "slack_sent_urls": ["https://x/1"], "date_anchors": [[5, 1.0], [9, 2.0]]}
    hub_path = tmp_path / "data" / "tinydoc" / "results.json"
    store.save(hub_path, {**anchored_results(), "archive": [{"url": f"https://old.reddit.com/r/a/comments/{tid_a}/t/",
                                                               "title": "hub wins"}]})
    (tmp_path / "old.json").write_text(json.dumps(old))
    import_legacy.main(tmp_path / "old.json", hub_path)
    import_legacy.main(tmp_path / "old.json", hub_path)  # idempotent
    out = json.loads(hub_path.read_text())
    assert [x["title"] for x in out["archive"]] == ["hub wins"]
    assert {f"reddit:{tid_a}", f"reddit:{tid_b}", "https://x/1"} <= set(out["slack_sent_urls"])
    assert len(out["slack_sent_urls"]) == len(set(out["slack_sent_urls"]))
    assert [a[0] for a in out["date_anchors"]][:2] == [5, 9] and out["slack_policy"] == 2


def test_scheduled_run_skips_a_broken_profile_but_serves_the_rest(env, tmp_path, monkeypatch):
    path, delivered, install = env
    d = write_clients(tmp_path)
    (d / "broken.json").write_text("{nope")
    install({}, [("fake", ai_by_title({}))])
    monkeypatch.setattr(f5bot, "fetch", lambda now: ([], []))
    data_dir = tmp_path / "data"
    assert cli.main(["--clients-dir", str(d), "--data-dir", str(data_dir)]) == 1  # visible failure...
    assert (data_dir / "acme" / "results.json").exists()  # ...but the healthy clients still ran


def test_a_dead_source_is_flagged_not_mistaken_for_a_quiet_day(env, client, monkeypatch):
    path, delivered, install = env
    install({}, [("fake", ai_by_title({}))])
    monkeypatch.setattr(web, "search", lambda q, *a: (_ for _ in ()).throw(RuntimeError("403 Forbidden")))
    pipeline.run_client(client, path)
    out = json.loads(path.read_text())
    assert out["source_health"]["web"]["status"] == "down" and "403" in out["source_health"]["web"]["last_error"]
    assert out["query_stats"][f"web|{BRAND_Q}"]["errors"] == 1 and out["query_stats"][f"web|{BRAND_Q}"]["runs"] == 0
