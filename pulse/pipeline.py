"""One run for one client: collect -> clean -> de-dupe -> classify -> vault -> Slack -> save.

Shared work (the F5Bot inbox) is done once in run.py and handed in already routed.
"""
from datetime import datetime, timezone

from . import config, feedback, store
from .classifier import classify_items, render_client_prompt
from .dates import RedditIdClock, is_within_hours, to_ts
from .leads import build_known_index, dedupe, is_f5bot, is_linkedin, key_of, merge_by_key, reuse_classification
from .slack import SentLog, dispatch_alerts
from .sources import arctic_shift, google_alerts, linkedin, serper, web
from .text import f5bot_kind
from .urls import reddit_thread_id

SLACK_POLICY_VERSION = 2
CATEGORY_PRIORITY = {"PR Support": 0, "Prime Target": 1, "Brand Engagement": 2, "Monitor": 3}
QUALIFIED = ("PR Support", "Prime Target", "Brand Engagement")
VAULT_GRADES = ("urgent", "high", "medium")

# name -> module with collect(client, clock, now_iso); order = de-dupe priority
# (arctic_shift first: full post text and exact dates beat a search snippet for the same thread)
SEARCH_SOURCES = (("arctic_shift", arctic_shift), ("google_alerts", google_alerts), ("serper", serper), ("web", web))


def is_fresh(lead, hours=config.FRESH_HOURS):
    return is_within_hours(lead.get("post_date"), hours)


# ---------- date clock ----------
def is_f5bot_post(lead):
    kind = lead.get("mention_kind") or f5bot_kind(lead.get("text"))  # legacy rows keep the prefix in text
    return is_f5bot(lead) and kind == "post"


def add_f5bot_anchor(clock, lead):
    """F5Bot alerts on new posts within minutes, so a *post* alert's e-mail time is a good date anchor.
    (Comment alerts are not: the thread they sit in can be years old.)"""
    if is_f5bot_post(lead):
        tid, ts = reddit_thread_id(lead.get("url", "")), to_ts(lead.get("post_date"))
        if tid and ts:
            clock.add(tid, ts)


def seed_clock(prev):
    """The Reddit id->date estimator from stored anchors plus every previously well-dated Reddit post."""
    clock = RedditIdClock(prev.get("date_anchors"))
    for x in prev.get("f5bot_leads", []):
        add_f5bot_anchor(clock, x)
    for coll in (prev.get("archive", []), prev.get("hot_leads", []), prev.get("brand_alerts", [])):
        for x in coll:
            if is_f5bot(x) or x.get("platform", "reddit") != "reddit" or is_linkedin(x):
                continue
            confidence = x.get("date_confidence")
            legacy_dated = confidence is None and x.get("post_date") != x.get("date_found")
            if confidence in ("exact", "approx") or legacy_dated:
                tid, ts = reddit_thread_id(x.get("url", "")), to_ts(x.get("post_date"))
                if tid and ts:
                    clock.add(tid, ts)
    return clock


# ---------- source scheduling ----------
def is_due(client, name, last_run, now_iso):
    """Enabled, and at least `min_hours_between_runs` since this source last ran (saves free credits)."""
    if not client.enabled(name):
        return False
    gap = float(client.source(name).get("min_hours_between_runs", 0) or 0)
    if gap <= 0 or not last_run.get(name):
        return True
    then, now = to_ts(last_run[name]), to_ts(now_iso)
    return then is None or now is None or (now - then) >= gap * 3600 - 300  # 5 min slack for cron jitter


# ---------- query yield stats ----------
def update_query_stats(stats, executed, found, prev_keys, now_iso):
    """Per query: runs, results, NEW threads found and how many of those qualified.
    Feeds the dashboard's "which queries actually work" view (and later automatic pruning)."""
    for ex in executed:
        k = f"{ex['source']}|{ex['query']}"
        s = stats.setdefault(k, {"source": ex["source"], "kind": ex["kind"], "query": ex["query"], "runs": 0,
                                 "results": 0, "new": 0, "new_qualified": 0})
        if ex.get("label"):
            s["label"] = ex["label"]
        if ex.get("error"):
            s["errors"] = s.get("errors", 0) + 1
            s["last_error"], s["last_error_at"] = ex["error"], now_iso
            continue
        s["runs"] += 1
        s["results"] += ex["results"]
        s["last_run"] = now_iso
    for lead in found:
        k = f"{lead.get('source')}|{lead.get('serp_query')}"
        if k not in stats or key_of(lead) in prev_keys:
            continue
        stats[k]["new"] += 1
        if lead.get("action_category") in QUALIFIED and lead.get("ai_status") == "ok":
            stats[k]["new_qualified"] += 1
            stats[k]["last_qualified_at"] = now_iso
    return stats


QUIET_IS_NORMAL = {"google_alerts", "linkedin"}


def source_health(prev_health, executed, now_iso):
    """Per source: how many queries ran / failed this run. A source where *every* query failed is
    flagged loudly, so a dead free source never looks like a quiet day."""
    health = dict(prev_health or {})
    by_source = {}
    for ex in executed:
        by_source.setdefault(ex["source"], []).append(ex)
    for name, rows in by_source.items():
        failed = [r for r in rows if r.get("error")]
        results = sum(r["results"] for r in rows)
        raw = sum(r.get("raw", r["results"]) for r in rows)
        if failed and len(failed) == len(rows):
            status, why = "down", f"failed on every query: {failed[-1]['error'][:200]}"
        elif rows and raw == 0 and name in QUIET_IS_NORMAL:
            status, why = "ok", ""  # e.g. a Google Alert with nothing new: normal, not a blocked source
        elif rows and raw == 0:
            status, why = "empty", "returned nothing for every query (engines may be blocking this server)"
        elif rows and results == 0:
            status, why = "filtered", f"returned {raw} rows but none were Reddit threads or Quora questions"
        else:
            status, why = ("degraded" if failed else "ok"), ""
        health[name] = {"last_run": now_iso, "queries": len(rows), "failed": len(failed), "raw": raw,
                        "results": results, "status": status}
        if failed:
            health[name]["last_error"] = failed[-1]["error"]
        if why:
            health[name]["note"] = why
            print(f"::warning::source '{name}' {why}")
    return health


def configured_queries(client):
    """{"source|query"} for everything the profile currently asks for (used to drop stale stats rows)."""
    keys = set()
    for name in ("serper", "web"):
        if client.enabled(name):
            keys |= {f"{name}|{q}" for kind in ("brand", "intent") for q in client.queries_for(name, kind)}
    if client.enabled("arctic_shift"):
        subs = client.source("arctic_shift").get("subreddits") or []
        keys |= {f"arctic_shift|r/{s.strip().removeprefix('r/')}" for s in subs if s and s.strip()}
    if client.enabled("google_alerts"):
        keys |= {f"google_alerts|{f['url']}" for f in client.source("google_alerts").get("feeds") or []}
    if client.enabled("linkedin"):
        keys.add(f"linkedin|{client.source('linkedin').get('keyword') or client.name}")
    return keys


def prune_stale(client, query_stats, health):
    """Forget stats/health for sources and queries the profile no longer uses, so the dashboard stays truthful."""
    wanted = configured_queries(client)
    query_stats = {k: v for k, v in query_stats.items() if k in wanted}
    health = {k: v for k, v in health.items() if client.enabled(k)}
    return query_stats, health


# ---------- main entry ----------
def run_client(client, results_path, now_iso=None, f5bot_leads=(), providers=None, feedback_dir=None):
    now_iso = now_iso or datetime.now(timezone.utc).isoformat()
    reviews = feedback.load(feedback_dir, client.slug)
    prev = store.load(results_path, now_iso, client.slug)
    clock = seed_clock(prev)
    prev_colls = (prev.get("archive"), prev.get("hot_leads"), prev.get("brand_alerts"), prev.get("f5bot_leads"))
    known = build_known_index(*prev_colls)
    prev_keys = {key_of(x) for coll in prev_colls for x in coll or []}

    sent = SentLog(prev.get("slack_sent_urls"))
    if prev.get("slack_policy") != SLACK_POLICY_VERSION:
        # First run for this file: everything already stored counts as alerted (no Slack flood).
        for k in prev_keys:
            sent.add_key(k)

    last_run = dict(prev.get("source_last_run") or {})
    f5bot_new = list(f5bot_leads)
    for lead in f5bot_new:  # before searching, so the search results get better date estimates
        add_f5bot_anchor(clock, lead)

    found, executed = [], []
    for name, module in SEARCH_SOURCES:
        if is_due(client, name, last_run, now_iso):
            leads, ran = module.collect(client, clock, now_iso)
            found += leads
            executed += ran
            last_run[name] = now_iso
    fetched_linkedin = []
    if is_due(client, "linkedin", last_run, now_iso):
        fetched_linkedin, ran = linkedin.collect(client, clock, now_iso)
        executed += ran
        last_run["linkedin"] = now_iso

    # One record per thread across sources; a brand hit wins over an intent hit for the same thread.
    fetched = dedupe(sorted(found, key=lambda x: x["type"] != "Mention"))
    fetched_brand, fetched_intent = [], []
    for lead in fetched:
        if lead["type"] != "Mention":
            fetched_intent.append(lead)
        elif client.is_mention(f"{lead.get('title', '')} {lead.get('text', '')}"):
            fetched_brand.append(lead)
        else:
            # The search engine matched the brand somewhere on the page (sidebar, comments), but the post
            # itself does not name it: an ordinary lead, not a mention.
            lead["type"], lead["brand_unverified"] = "Lead", True
            fetched_intent.append(lead)

    # Retain still-fresh items from earlier runs so nothing flaps out (or loses its pending AI retry).
    live_brand = merge_by_key([x for x in fetched_brand if is_fresh(x)],
                              [x for x in prev.get("brand_alerts", []) if is_fresh(x)])
    brand_keys = {key_of(x) for x in live_brand}
    prev_intent = [x for x in prev.get("hot_leads", []) if not is_linkedin(x)]
    live_intent = [x for x in merge_by_key([x for x in fetched_intent if is_fresh(x)],
                                           [x for x in prev_intent if is_fresh(x)])
                   if key_of(x) not in brand_keys]
    live_linkedin = merge_by_key([x for x in fetched_linkedin if is_fresh(x)],
                                 [x for x in prev.get("hot_leads", []) if is_linkedin(x) and is_fresh(x)])
    f5bot_all = merge_by_key(f5bot_new, [x for x in prev.get("f5bot_leads", [])
                                         if is_within_hours(x.get("date_found"))])

    # ---- Classification: live items first, so a quota cut-off never starves fresh leads ----
    pool, seen_ids = [], set()
    for coll in (live_brand, live_intent, live_linkedin, f5bot_all, fetched_brand, fetched_intent):
        for item in coll:
            if id(item) not in seen_ids:
                seen_ids.add(id(item))
                pool.append(item)
    for item in pool:
        reuse_classification(item, known)  # known threads cost no AI quota
    classified, pending = classify_items(pool, platform_of=lambda it: it.get("platform", "reddit"),
                                         providers=providers,
                                         template=render_client_prompt(client, reviewed_examples=feedback.prompt_examples(reviews)))
    print(f"[{client.slug}] AI: {classified} classified, {pending} pending (retried next run).")
    by_key = {key_of(x): x for x in pool}
    for lead in found:  # cross-source duplicates share the winner's verdict (for query stats)
        if lead.get("ai_status") != "ok" and key_of(lead) in by_key:
            reuse_classification(lead, {key_of(lead): by_key[key_of(lead)]})

    # ---- Vault: every qualified thread ever found by search/alerts ----
    archive = prev.get("archive", [])
    existing = {key_of(x) for x in archive if x.get("url")}
    for lead in dedupe(fetched_brand + fetched_intent + live_brand + live_intent):
        k = key_of(lead)
        if k not in existing and str(lead.get("grade", "Low")).lower() in VAULT_GRADES:
            archive.append(lead)
            existing.add(k)

    # ---- Slack: PR Support first ----
    candidates = [x for x in dedupe(live_brand + f5bot_all + live_linkedin + live_intent) if is_fresh(x)]
    candidates.sort(key=lambda x: CATEGORY_PRIORITY.get(x.get("action_category"), 9))
    delivered = dispatch_alerts(candidates, sent, client)

    stamped = feedback.apply((live_brand, live_intent, live_linkedin, f5bot_all, archive), reviews)
    if reviews:
        print(f"[{client.slug}] feedback: {len(reviews)} reviewed item(s), {stamped} stored lead(s) stamped.")

    output = dict(prev)
    output.update({
        "schema_version": store.SCHEMA_VERSION,
        "client": client.slug,
        "client_name": client.name,
        "last_updated": now_iso,
        "brand_alerts": live_brand,
        "hot_leads": live_linkedin + live_intent,
        "f5bot_leads": f5bot_all,
        "archive": archive,
        "slack_sent_urls": sent.to_list(),
        "slack_policy": SLACK_POLICY_VERSION,
        "date_anchors": clock.to_list(),
        "source_last_run": last_run,
        "source_health": source_health(prev.get("source_health"), executed, now_iso),
        "query_stats": update_query_stats(dict(prev.get("query_stats") or {}), executed, found, prev_keys, now_iso),
    })
    output["query_stats"], output["source_health"] = prune_stale(client, output["query_stats"], output["source_health"])
    store.save(results_path, output)
    summary = {"client": client.slug, "found": len(fetched), "brand": len(live_brand), "intent": len(live_intent),
               "linkedin": len(live_linkedin), "f5bot": len(f5bot_all), "archive": len(archive),
               "classified": classified, "pending": pending, "slack": delivered}
    print(f"[{client.slug}] done: {summary}")
    return summary
