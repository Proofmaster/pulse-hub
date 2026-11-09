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


_masked = set()


def slack_webhook(slug, env_name=None):
    """The Slack webhook for one client.

    Local runs may set an env var per client (`env_name`, e.g. SLACK_WEBHOOK_ACME). In GitHub Actions all
    webhooks live in ONE secret, SLACK_WEBHOOKS = {"<slug>": "https://hooks.slack.com/..."}: the workflow then
    passes only that secret (never the whole secrets context) and the public repo never names a client.
    """
    if env_name and os.environ.get(env_name):
        return os.environ[env_name]
    raw = os.environ.get("SLACK_WEBHOOKS")
    if not raw:
        return None
    try:
        hooks = json.loads(raw)
    except ValueError:
        print("::warning::SLACK_WEBHOOKS is not valid JSON - expected {\"<client-slug>\": \"<webhook url>\"}")
        return None
    if not isinstance(hooks, dict):
        print("::warning::SLACK_WEBHOOKS must be a JSON object keyed by client slug")
        return None
    if os.environ.get("GITHUB_ACTIONS") == "true":
        # GitHub masks the secret as a whole, not the values inside it: mask each URL explicitly.
        for url in hooks.values():
            if isinstance(url, str) and url.strip() and url.strip() not in _masked:
                _masked.add(url.strip())
                print(f"::add-mask::{url.strip()}")
    url = hooks.get(slug)
    return url.strip() if isinstance(url, str) and url.strip() else None
