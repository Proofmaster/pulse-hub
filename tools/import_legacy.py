"""Merge an old single-client results.json (e.g. the RedditScraper repo) into a hub client's file.

    python tools/import_legacy.py OLD_results.json ../pulse-hub-data/data/<slug>/results.json

Safe to run more than once (e.g. again at switch-over): the vault, Slack de-dupe log, date anchors and
live tabs are merged by thread identity, never duplicated; the hub's own records win.
Run it right before turning the old system off so nothing it already alerted is alerted again.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pulse import store  # noqa: E402
from pulse.leads import key_of, merge_by_key  # noqa: E402


def merge(hub, old):
    out = dict(hub)
    for coll in ("archive", "brand_alerts", "hot_leads", "f5bot_leads"):
        out[coll] = merge_by_key(hub.get(coll, []), old.get(coll, []))
    sent = list(hub.get("slack_sent_urls", []))
    have = set(sent)
    for k in old.get("slack_sent_urls", []):
        if k not in have:
            have.add(k)
            sent.append(k)
    # Everything the old system ever stored counts as alerted, so the switch never floods Slack.
    for coll in ("archive", "brand_alerts", "hot_leads", "f5bot_leads"):
        for x in old.get(coll, []):
            for k in (x.get("url"), key_of(x)):
                if k and k not in have:
                    have.add(k)
                    sent.append(k)
    out["slack_sent_urls"] = sent
    out["slack_policy"] = 2
    anchors = {int(a[0]): a[1] for a in old.get("date_anchors", []) if isinstance(a, list) and len(a) == 2}
    anchors.update({int(a[0]): a[1] for a in hub.get("date_anchors", []) if isinstance(a, list) and len(a) == 2})
    out["date_anchors"] = [[k, anchors[k]] for k in sorted(anchors)]
    out["imported_from_legacy_at"] = datetime.now(timezone.utc).isoformat()
    return out


def main(old_path, hub_path):
    old = json.loads(Path(old_path).read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc).isoformat()
    hub = store.load(hub_path, now, Path(hub_path).parent.name)
    merged = merge(hub, old)
    store.save(hub_path, merged)
    print(f"Imported: archive {len(merged['archive'])}, sent-log {len(merged['slack_sent_urls'])}, "
          f"anchors {len(merged['date_anchors'])} -> {hub_path}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
