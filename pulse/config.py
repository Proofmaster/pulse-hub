import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROMPT_PATH = ROOT / "prompts" / "classify.txt"

# --- Shared credentials (all optional; a missing key just disables that provider/source) ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
SERPER_API_KEY = os.environ.get("SERPER_API_KEY")
APIFY_API_TOKEN = os.environ.get("APIFY_API_TOKEN")
EMAIL_USER = os.environ.get("EMAIL_USER")  # the inbox F5Bot (and later Quora digests) deliver to
EMAIL_PASS = os.environ.get("EMAIL_PASS")
EMAIL_IMAP_HOST = os.environ.get("EMAIL_IMAP_HOST", "imap.gmail.com")

# --- Model names (override via env when a free-tier model is retired) ---
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free")

# --- Tunables ---
CHUNK_SIZE = int(os.environ.get("AI_CHUNK_SIZE", "10"))
CHUNK_DELAY_SECONDS = float(os.environ.get("AI_CHUNK_DELAY", "4"))
FRESH_HOURS = 48
MAX_SLACK_PER_RUN = int(os.environ.get("MAX_SLACK_PER_RUN", "20"))
SLACK_LOG_LIMIT = 3000
# Set to "false" while another system reads the same F5Bot inbox (e.g. during a shadow run).
F5BOT_MARK_SEEN = os.environ.get("F5BOT_MARK_SEEN", "true").strip().lower() not in ("0", "false", "no", "off")


def secret(name):
    """A per-client secret (e.g. SLACK_WEBHOOK_ACME): plain env var first, then the PULSE_SECRETS JSON blob.

    The workflow passes `toJSON(secrets)` as PULSE_SECRETS, so adding a client never needs a workflow edit:
    just add its secret in GitHub. GitHub masks every secret value in the logs.
    """
    if not name:
        return None
    value = os.environ.get(name)
    if value:
        return value
    try:
        return json.loads(os.environ.get("PULSE_SECRETS") or "{}").get(name) or None
    except (ValueError, AttributeError):
        return None
