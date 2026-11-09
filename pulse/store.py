"""Per-client results files: <data-dir>/<slug>/results.json (same schema the dashboard reads)."""
import json
import os
from pathlib import Path

SCHEMA_VERSION = 1


def new_results(now_iso, slug=""):
    return {"schema_version": SCHEMA_VERSION, "client": slug, "last_updated": now_iso,
            "brand_alerts": [], "hot_leads": [], "archive": [], "f5bot_leads": [], "slack_sent_urls": []}


def results_path(data_dir, slug):
    return Path(data_dir) / slug / "results.json"


def load(path, now_iso, slug=""):
    path = Path(path)
    if not path.exists():
        return new_results(now_iso, slug)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)  # a corrupt file must fail loudly, never be silently replaced


def save(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)  # atomic: a crash never leaves a half-written file
