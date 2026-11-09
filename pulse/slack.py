"""Slack alerts, one webhook per client.

Default policy (`slack.alert_on` in the profile): every brand mention (any source) and every Prime Target.
Leads that still await AI classification and do not mention the brand wait for the next run.
Webhooks come from secrets (`SLACK_WEBHOOK_<SLUG>` by default) and are never stored in a repo.
"""
import time

import requests

from . import config
from .leads import key_of

CATEGORY_STYLE = {
    "PR Support": ("🚨", "#f43f5e"),
    "Prime Target": ("🎯", "#0ea5e9"),
    "Brand Engagement": ("💬", "#10b981"),
    "Monitor": ("👁️", "#8b5cf6"),
    "Ignore": ("📌", "#94a3b8"),
}
PENDING_STYLE = ("🆕", "#f59e0b")
SOURCE_LABELS = {"linkedin": "LinkedIn", "f5bot": "F5Bot", "quora": "Quora", "alerts": "Google Alerts",
                 "reddit": "Reddit"}


def source_of(lead):
    if str(lead.get("platform", lead.get("subreddit", ""))).lower() == "linkedin":
        return "linkedin"
    if lead.get("is_f5bot") or lead.get("type") == "F5Bot":
        return "f5bot"
    if lead.get("platform") == "quora":
        return "quora"
    if lead.get("source") == "google_alerts":
        return "alerts"
    return "reddit"


def _classified(lead):
    return bool(lead.get("action_category")) and lead.get("ai_status") != "pending"


def should_alert(lead, is_mention, alert_on=("mention", "Prime Target")):
    """(alert?, reason)."""
    if "mention" in alert_on and is_mention(f"{lead.get('title', '')} {lead.get('text', '')}"):
        return True, "mention"
    category = lead.get("action_category")
    if _classified(lead) and category in alert_on:
        return True, "lead"
    return False, ""


def _esc(text):
    return str(text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_payload(lead, client_name=""):
    category = lead.get("action_category")
    classified = _classified(lead)
    emoji, color = CATEGORY_STYLE.get(category, PENDING_STYLE) if classified else PENDING_STYLE
    label = category if classified else "Unclassified mention"
    source = SOURCE_LABELS[source_of(lead)]
    sub = lead.get("subreddit_name")
    where = f"{source} · r/{sub}" if sub and lead.get("platform") == "reddit" else source
    prefix = f"[{client_name}] " if client_name else ""

    snippet = (lead.get("text") or "No snippet available.").strip()
    if len(snippet) > 350:
        snippet = snippet[:350] + "..."
    angle = lead.get("reply_angle")
    angle_block = (f"*Suggested angle:*\n_{_esc(angle)}_"
                   if classified and angle and angle != "None"
                   else f"*Post snippet:*\n_{_esc(snippet)}_")

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"{emoji} {prefix}{label} ({where})"[:150],
                                    "emoji": True}},
        {"type": "section", "text": {"type": "mrkdwn",
                                     "text": f"*{_esc(lead.get('title') or 'New mention')}*\n<{lead.get('url', '')}|View post>"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": angle_block}},
    ]
    return {"attachments": [{"color": color, "blocks": blocks}]}


def send_slack_alert(lead, webhook, client_name=""):
    """POST one alert. Returns True only when Slack accepted it."""
    if not webhook:
        return False
    try:
        resp = requests.post(webhook, json=build_payload(lead, client_name), timeout=15)
        if not resp.ok:
            print(f"[slack] rejected ({resp.status_code}): {resp.text[:120]}")
        return resp.ok
    except Exception as e:  # noqa: BLE001
        print(f"[slack] failed: {e}")
        return False


class SentLog:
    """Ordered de-dupe log stored as `slack_sent_urls` (raw URLs + canonical thread keys)."""

    def __init__(self, entries=None, limit=None):
        self.limit = limit or config.SLACK_LOG_LIMIT
        self._list = list(entries or [])
        self._set = set(self._list)

    def has(self, lead):
        return lead.get("url") in self._set or key_of(lead) in self._set

    def add(self, lead):
        for k in (lead.get("url"), key_of(lead)):
            self.add_key(k)

    def add_key(self, key):
        if key and key not in self._set:
            self._set.add(key)
            self._list.append(key)

    def to_list(self):
        return self._list[-self.limit:]


def dispatch_alerts(leads, sent, client, max_per_run=None, delay=1.1, sender=None):
    """Alert on qualifying, not-yet-sent leads for one client. Returns the number delivered."""
    max_per_run = max_per_run or config.MAX_SLACK_PER_RUN
    webhook = config.secret(client.slack_secret)
    if not webhook:
        print(f"[slack:{client.slug}] secret {client.slack_secret} is not set - skipping alerts.")
        return 0
    sender = sender or send_slack_alert
    delivered = 0
    for lead in leads:
        if delivered >= max_per_run:
            print(f"[slack:{client.slug}] per-run cap ({max_per_run}) reached; the rest go out next run.")
            break
        ok, _ = should_alert(lead, client.is_mention, client.alert_on)
        if not ok or sent.has(lead):
            continue
        if sender(lead, webhook, client.name):
            sent.add(lead)
            delivered += 1
            time.sleep(delay)
    return delivered
