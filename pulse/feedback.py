"""Human feedback from the dashboard: 👍/👎 and corrected categories.

The dashboard saves feedback as files in the PRIVATE data repo (`feedback/<slug>/<timestamp>.json`) through
GitHub's own "new file" page, so no token is needed in the browser:

    {"client": "acme", "items": [{"key": "reddit:1abc23", "title": "...", "text": "...",
                                  "verdict": "down", "category": "Ignore", "at": "2026-11-09T12:00:00Z"}]}

Each run then
  1. marks the stored leads with `human_verdict` / `human_category` (the dashboard shows the human call), and
  2. adds the most recent reviewed items to the client's prompt as examples, so the classifier learns from them.
"""
import json
from pathlib import Path

from .classifier import CATEGORIES

MAX_PROMPT_EXAMPLES = 12


def load(feedback_dir, slug):
    """{key: latest entry} for one client. Broken files are skipped with a warning, never fatal."""
    folder = Path(feedback_dir) / slug if feedback_dir else None
    if not folder or not folder.is_dir():
        return {}
    latest = {}
    for path in sorted(folder.glob("*.json")):  # timestamped names: later files win
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as e:
            print(f"::warning::feedback file {path.name} skipped: {e}")
            continue
        for item in (data.get("items") if isinstance(data, dict) else None) or []:
            if not isinstance(item, dict) or not item.get("key") or item.get("verdict") not in ("up", "down"):
                continue
            cat = item.get("category")
            if cat is not None and cat not in CATEGORIES:
                item = {**item, "category": None}
            latest[item["key"]] = item
    return latest


def apply(collections, entries):
    """Stamp human verdicts onto stored leads (in place). Returns how many leads were stamped."""
    if not entries:
        return 0
    stamped = 0
    for coll in collections:
        for lead in coll or []:
            fb = entries.get(lead.get("mention_key")) or entries.get(lead.get("thread_key"))
            if not fb:
                continue
            lead["human_verdict"] = fb["verdict"]
            lead["human_category"] = fb.get("category") or (lead.get("action_category") if fb["verdict"] == "up" else None)
            lead["human_reviewed_at"] = fb.get("at")
            stamped += 1
    return stamped


def prompt_examples(entries, limit=MAX_PROMPT_EXAMPLES):
    """Most recent reviewed items as {"text", "category"} examples (👍 keeps the AI's call, 👎 needs a category)."""
    out = []
    for fb in sorted(entries.values(), key=lambda e: str(e.get("at") or ""), reverse=True):
        category = fb.get("category")
        if not category:
            continue  # a bare 👎 says "wrong" but not what is right: not usable as an example
        text = " ".join(f"{fb.get('title', '')} {fb.get('text', '')}".split())[:220]
        if text:
            out.append({"text": text.replace('"', "'"), "category": category})
        if len(out) >= limit:
            break
    return out
