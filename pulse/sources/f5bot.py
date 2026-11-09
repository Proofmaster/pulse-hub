"""F5Bot e-mail alerts (free; Reddit + Hacker News + Lobsters keyword alerts).

One inbox serves every client: add all clients' keywords to the same F5Bot account, and each alert
is routed to the client(s) whose `sources.f5bot.keywords` it contains. Read once per run, marked
read only after every client's results were saved.
"""
import copy
import email
import imaplib
from datetime import timezone
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime

from bs4 import BeautifulSoup

from .. import config
from ..leads import attach_identity, key_of
from ..text import clean_f5bot_text, f5bot_kind
from ..urls import unwrap_f5bot

SENDER = "alerts@f5bot.com"


def _email_body(msg):
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                return part.get_payload(decode=True).decode("utf-8", errors="ignore")
        return ""
    payload = msg.get_payload(decode=True)
    return payload.decode("utf-8", errors="ignore") if payload else ""


def parse_email(html, mail_date, now_iso, subject=""):
    """Alert e-mail HTML -> leads (unclassified)."""
    leads, seen = [], set()
    soup = BeautifulSoup(html, "html.parser")
    for a_tag in soup.find_all("a", href=True):
        href = unwrap_f5bot(a_tag["href"])  # F5Bot wraps links in a tracking redirect
        if "reddit.com" not in href:
            continue
        title = a_tag.get_text(" ", strip=True)
        parent = a_tag.find_parent()
        # " " separator: get_text(strip=True) alone glues words ("tryTinydocor SquashIt").
        raw = parent.get_text(" ", strip=True) if parent else title
        lead = {
            "id": 0, "type": "F5Bot", "subreddit": "Reddit (F5Bot)", "is_f5bot": True,
            "title": title, "text": clean_f5bot_text(raw)[:1000].replace("\n", " "), "url": href,
            "f5bot_url": a_tag["href"], "mention_kind": f5bot_kind(raw), "f5bot_subject": subject,
            "post_date": mail_date, "date_confidence": "email", "date_found": now_iso, "source": "f5bot",
        }
        attach_identity(lead, "reddit")
        if lead.get("subreddit_name"):
            lead["subreddit"] = f"{lead['subreddit_name']} (F5Bot)"
        k = key_of(lead)
        if k not in seen:
            seen.add(k)
            leads.append(lead)
    return leads


def fetch(now_iso):
    """Returns (leads, email_ids). Uses BODY.PEEK so nothing is marked read here."""
    if not config.EMAIL_USER or not config.EMAIL_PASS:
        print("[f5bot] EMAIL_USER/EMAIL_PASS missing - skipping.")
        return [], []
    leads, email_ids = [], []
    try:
        mail = imaplib.IMAP4_SSL(config.EMAIL_IMAP_HOST)
        mail.login(config.EMAIL_USER, config.EMAIL_PASS)
        mail.select("inbox")
        status, messages = mail.search(None, f'(UNSEEN FROM "{SENDER}")')
        if status == "OK" and messages[0]:
            for e_id in messages[0].split():
                _, msg_data = mail.fetch(e_id, "(BODY.PEEK[])")
                email_ids.append(e_id)
                for part in msg_data:
                    if not isinstance(part, tuple):
                        continue
                    msg = email.message_from_bytes(part[1])
                    body = _email_body(msg)
                    if not body:
                        continue
                    try:
                        mail_date = parsedate_to_datetime(msg["Date"]).astimezone(timezone.utc).isoformat()
                    except Exception:  # noqa: BLE001
                        mail_date = now_iso
                    try:
                        subject = str(make_header(decode_header(msg.get("Subject", ""))))
                    except Exception:  # noqa: BLE001
                        subject = msg.get("Subject", "") or ""
                    leads.extend(parse_email(body, mail_date, now_iso, subject))
        mail.logout()
    except Exception as e:  # noqa: BLE001
        print(f"[f5bot] IMAP retrieval failed: {e}")
    return leads, email_ids


def mark_seen(email_ids):
    if not email_ids or not config.EMAIL_USER or not config.EMAIL_PASS:
        return
    try:
        mail = imaplib.IMAP4_SSL(config.EMAIL_IMAP_HOST)
        mail.login(config.EMAIL_USER, config.EMAIL_PASS)
        mail.select("inbox")
        for e_id in email_ids:
            mail.store(e_id, "+FLAGS", "\\Seen")
        mail.close()
        mail.logout()
    except Exception as e:  # noqa: BLE001
        print(f"[f5bot] could not mark e-mails read (they will be re-read next run): {e}")


def route(leads, clients):
    """{slug: [lead copies]}. An alert goes to every client whose keyword it contains; with a single
    F5Bot client, unmatched alerts go to it too (the keyword may sit in text the e-mail truncated)."""
    takers = [c for c in clients if c.enabled("f5bot")]
    routed = {c.slug: [] for c in takers}
    unrouted = 0
    for lead in leads:
        hay = f"{lead.get('f5bot_subject', '')} {lead.get('title', '')} {lead.get('text', '')}".lower()
        matched = [c for c in takers if any(k.lower() in hay for k in c.f5bot_keywords())]
        if not matched and len(takers) == 1:
            matched = takers
        if not matched:
            unrouted += 1
        for c in matched:
            routed[c.slug].append(copy.deepcopy(lead))
    if unrouted:
        print(f"[f5bot] {unrouted} alert(s) matched no client keyword and were skipped.")
    return routed
