"""One classifier for every client and source (Reddit, Quora, LinkedIn, F5Bot, Google Alerts).

The prompt template (`prompts/classify.txt`) is filled in per client by `render_client_prompt`,
which leaves only {{SOURCE_NOTE}} and {{POSTS}} for `build_prompt`.

Providers are tried in order (Gemini -> Groq -> OpenRouter). Items a provider fails on are
passed to the next provider; anything still unclassified is marked `ai_status: "pending"`
so the next run retries it instead of silently dropping the lead.
"""
import json
import re
import time
from datetime import datetime, timezone

import requests

from . import config

CATEGORIES = ["PR Support", "Prime Target", "Brand Engagement", "Monitor", "Ignore"]
SENTIMENTS = ["Positive", "Neutral", "Negative"]
_CAT_LOOKUP = {c.lower(): c for c in CATEGORIES}
_SENT_LOOKUP = {s.lower(): s for s in SENTIMENTS}

SOURCE_NOTES = {
    "reddit": "These posts come from Reddit. Reply angles should suit a helpful, non-salesy Reddit comment.",
    "linkedin": "These posts come from LinkedIn. Reply angles should suit a professional LinkedIn comment.",
    "quora": ("These are Quora questions. A good answer keeps getting traffic from Google for years, so reply "
              "angles should suit a thorough, genuinely helpful Quora answer that lists the brand as one option."),
}

RESPONSE_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "index": {"type": "INTEGER"},
            "posture": {"type": "STRING"},
            "action_category": {"type": "STRING"},
            "sentiment": {"type": "STRING"},
            "reply_angle": {"type": "STRING"},
        },
        "required": ["index", "posture", "action_category", "sentiment", "reply_angle"],
    },
}


# ---------- grading / state helpers ----------
def grade_for(category):
    """Legacy grade the dashboard sorts and filters on."""
    if category in ("PR Support", "Prime Target"):
        return "High"
    if category == "Brand Engagement":
        return "Medium"
    return "Low"


def is_classified(item):
    return bool(item.get("action_category")) and item.get("ai_status") != "pending"


def needs_ai(item):
    return not is_classified(item)


def apply_classification(item, result):
    category = result["action_category"]
    item.update({
        "action_category": category,
        "posture": result.get("posture") or "Unknown",
        "reply_angle": result.get("reply_angle", ""),
        "grade": grade_for(category),
        "reason": result.get("reply_angle", ""),
        "sentiment": result.get("sentiment", "Neutral"),
        "ai_status": "ok",
        "classified_at": datetime.now(timezone.utc).isoformat(),
    })
    item.pop("ai_attempts", None)


CLASSIFICATION_FIELDS = (
    "action_category", "posture", "reply_angle", "grade", "reason",
    "sentiment", "ai_status", "classified_at",
)


def copy_classification(dst, src):
    for f in CLASSIFICATION_FIELDS:
        if f in src:
            dst[f] = src[f]


# ---------- prompt ----------
def load_prompt_template(path=None):
    return (path or config.PROMPT_PATH).read_text(encoding="utf-8")


def _join(items, sep="; "):
    return sep.join(items)


def render_client_prompt(client, template=None, reviewed_examples=()):
    """Fill the client placeholders; {{SOURCE_NOTE}} and {{POSTS}} are left for build_prompt.
    `reviewed_examples` are real posts a human graded in the dashboard (see pulse/feedback.py)."""
    template = template or load_prompt_template()
    terms = client.brand_terms + client.ambiguous_terms
    lines = [f'- "{ex["text"]}" -> {ex["category"]}' for ex in client.examples]
    lines += [f'- "{ex["text"]}" -> {ex["category"]}   (reviewed by the team)' for ex in reviewed_examples]
    examples = "\n".join(lines) or "- (none)"
    rules = "".join(f"\nRULE: {r}" for r in client.prompt_rules)
    values = {
        "{{BRAND}}": client.name,
        "{{BRAND_DESCRIPTION}}": f", {client.description}" if client.description else "",
        "{{CAN}}": _join(client.can),
        "{{CANNOT}}": _join(client.cannot) or "anything not listed under CAN.",
        "{{AUDIENCE}}": f"TYPICAL CUSTOMERS: {client.audience}\n" if client.audience else "",
        "{{COMPETITORS}}": (f"COMPETITORS (naming only these does not count as a {client.name} mention): "
                            f"{', '.join(client.competitors)}\n" if client.competitors else ""),
        "{{BRAND_TERMS}}": ", ".join(f'"{t}"' for t in terms),
        "{{BRAND_NOTE}}": f"\n  -> {client.brand_note}" if client.brand_note else "",
        "{{RULES}}": rules,
        "{{EXAMPLES}}": examples,
        "{{TONE}}": client.tone,
    }
    for key, value in values.items():
        template = template.replace(key, value)
    return template


def build_prompt(chunk, platform="reddit", template=None):
    template = template or load_prompt_template()
    lines = []
    for idx, m in enumerate(chunk):
        title = (m.get("title") or "").replace('"', "'")
        text = (m.get("text") or "").replace('"', "'")
        lines.append(f"INDEX: {idx} | Title: {title} | Snippet: {text}")
    return (template
            .replace("{{SOURCE_NOTE}}", SOURCE_NOTES.get(platform, ""))
            .replace("{{POSTS}}", "\n".join(lines)))


# ---------- parsing / validation ----------
def extract_json_array(raw):
    raw = (raw or "").replace("```json", "").replace("```", "").strip()
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for v in data.values():
                if isinstance(v, list):
                    return v
    except Exception:
        pass
    start, end = raw.find("["), raw.rfind("]") + 1
    if start != -1 and end > start:
        try:
            data = json.loads(raw[start:end])
            return data if isinstance(data, list) else []
        except Exception:
            return []
    return []


def validate_results(data, size):
    """Keep only well-formed results with an in-range index and a known category."""
    out = {}
    for r in data or []:
        if not isinstance(r, dict):
            continue
        try:
            idx = int(r.get("index"))
        except (TypeError, ValueError):
            continue
        cat = _CAT_LOOKUP.get(str(r.get("action_category", "")).strip().lower())
        if cat is None or not (0 <= idx < size) or idx in out:
            continue
        sentiment = _SENT_LOOKUP.get(str(r.get("sentiment", "")).strip().lower(), "Neutral")
        angle = str(r.get("reply_angle", "") or "").strip()
        if cat == "Ignore":
            angle = "None"
        out[idx] = {
            "index": idx,
            "action_category": cat,
            "posture": str(r.get("posture", "") or "Unknown").strip(),
            "sentiment": sentiment,
            "reply_angle": angle,
        }
    return out


# ---------- providers ----------
def _retry(fn, attempts=2, delay=6):
    last = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - provider SDKs raise many types
            last = e
            msg = str(e)
            if i + 1 < attempts and any(code in msg for code in ("429", "503", "500", "timeout", "Timeout")):
                time.sleep(delay)
                continue
            break
    raise last


def _gemini(prompt):
    from google import genai
    from google.genai import types

    def call():
        client = genai.Client(api_key=config.GEMINI_API_KEY)
        response = client.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=RESPONSE_SCHEMA,
            ),
        )
        return response.text
    return _retry(call)


_JSON_ONLY = ("\n\nReturn ONLY a valid JSON array. Do not use markdown backticks. "
              "Each object must have the properties: index, posture, action_category, sentiment, reply_angle.")


def _groq(prompt):
    from groq import Groq

    def call():
        client = Groq(api_key=config.GROQ_API_KEY)
        response = client.chat.completions.create(
            model=config.GROQ_MODEL,
            messages=[{"role": "user", "content": prompt + _JSON_ONLY}],
            temperature=0,
        )
        return response.choices[0].message.content
    return _retry(call)


def _openrouter(prompt):
    def call():
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {config.OPENROUTER_API_KEY}"},
            json={
                "model": config.OPENROUTER_MODEL,
                "messages": [{"role": "user", "content": prompt + _JSON_ONLY}],
                "temperature": 0,
            },
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    return _retry(call)


def default_providers():
    providers = []
    if config.GEMINI_API_KEY:
        providers.append(("gemini", _gemini))
    if config.GROQ_API_KEY:
        providers.append(("groq", _groq))
    if config.OPENROUTER_API_KEY:
        providers.append(("openrouter", _openrouter))
    return providers


# ---------- main entry point ----------
def classify_chunk(chunk, platform="reddit", providers=None, template=None):
    """Classify one chunk in place. Returns the number of items classified."""
    providers = default_providers() if providers is None else providers
    remaining = list(range(len(chunk)))
    done = 0
    for name, provider in providers:
        if not remaining:
            break
        sub = [chunk[i] for i in remaining]
        try:
            raw = provider(build_prompt(sub, platform, template))
            results = validate_results(extract_json_array(raw), len(sub))
        except Exception as e:  # noqa: BLE001
            print(f"[classifier] {name} failed: {e}")
            continue
        still = []
        for local_idx, orig_idx in enumerate(remaining):
            r = results.get(local_idx)
            if r:
                apply_classification(chunk[orig_idx], r)
                done += 1
            else:
                still.append(orig_idx)
        if still:
            print(f"[classifier] {name} left {len(still)}/{len(sub)} unclassified")
        remaining = still
    for i in remaining:  # nothing worked: keep the lead, retry next run
        item = chunk[i]
        item["ai_status"] = "pending"
        item["ai_attempts"] = int(item.get("ai_attempts", 0)) + 1
    return done


def classify_items(items, platform_of=lambda item: "reddit", providers=None,
                   chunk_size=None, delay=None, template=None):
    """Classify every item in `items` that needs it. Returns (classified, still_pending)."""
    chunk_size = chunk_size or config.CHUNK_SIZE
    delay = config.CHUNK_DELAY_SECONDS if delay is None else delay
    todo = [it for it in items if needs_ai(it)]
    if not todo:
        return 0, 0
    template = template or load_prompt_template()
    providers = default_providers() if providers is None else providers
    if not providers:
        print("[classifier] No AI provider configured - leads kept as pending.")
        for it in todo:
            it["ai_status"] = "pending"
            it["ai_attempts"] = int(it.get("ai_attempts", 0)) + 1
        return 0, len(todo)
    by_platform = {}
    for it in todo:
        by_platform.setdefault(platform_of(it), []).append(it)
    classified = 0
    for platform, group in by_platform.items():
        for i in range(0, len(group), chunk_size):
            classified += classify_chunk(group[i:i + chunk_size], platform, providers, template)
            if i + chunk_size < len(group) and delay:
                time.sleep(delay)
    return classified, len(todo) - classified
