"""Build the dashboard site: <out>/index.html + <out>/data.enc.json (+ .nojekyll).

    DASHBOARD_PASSPHRASE=... python tools/build_dashboard.py \
        --clients-dir ../pulse-hub-data/clients --data-dir ../pulse-hub-data/data --out site

The site is public (GitHub Pages) but the data is not readable without the passphrase: every client's results
are gzipped and encrypted with AES-256-GCM, the key derived from the passphrase with PBKDF2-SHA256. The page
decrypts in the browser (WebCrypto); the passphrase never leaves it. Use a long passphrase (5+ random words).
"""
import argparse
import base64
import gzip
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pulse import store  # noqa: E402
from pulse.client import load_clients  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ITERATIONS = 310_000
LEAD_FIELDS = ("title", "url", "text", "subreddit", "subreddit_name", "platform", "type", "source", "post_date",
               "date_confidence", "date_found", "action_category", "posture", "reply_angle", "sentiment", "ai_status",
               "thread_key", "mention_key", "serp_query", "num_comments", "author", "brand_unverified",
               "human_verdict", "human_category", "mention_kind", "is_f5bot")


def slim(lead, text_len):
    out = {k: lead[k] for k in LEAD_FIELDS if lead.get(k) not in (None, "")}
    if "text" in out:
        out["text"] = out["text"][:text_len]
    return out


def bundle(clients_dir, data_dir, data_repo=""):
    errors = []
    clients = load_clients(clients_dir, errors=errors)
    for e in errors:
        print(f"::warning::dashboard skipped a profile - {e}")
    out = {"v": 1, "generated_at": datetime.now(timezone.utc).isoformat(), "data_repo": data_repo, "clients": []}
    for c in clients:
        path = store.results_path(data_dir, c.slug)
        if not path.exists():
            continue
        r = json.loads(path.read_text(encoding="utf-8"))
        out["clients"].append({
            "slug": c.slug, "name": c.name, "last_updated": r.get("last_updated"),
            "source_health": r.get("source_health") or {}, "query_stats": r.get("query_stats") or {},
            "brand_alerts": [slim(x, 700) for x in r.get("brand_alerts", [])],
            "hot_leads": [slim(x, 700) for x in r.get("hot_leads", [])],
            "f5bot_leads": [slim(x, 700) for x in r.get("f5bot_leads", [])],
            "archive": [slim(x, 280) for x in r.get("archive", [])],
        })
    return out


def encrypt(obj, passphrase, iterations=ITERATIONS):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    salt, iv = os.urandom(16), os.urandom(12)
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iterations).derive(passphrase.encode())
    plain = gzip.compress(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    ct = AESGCM(key).encrypt(iv, plain, None)  # ciphertext || 16-byte tag, the layout WebCrypto expects
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return {"v": 1, "alg": "AES-256-GCM", "kdf": "PBKDF2-SHA256", "iter": iterations,
            "salt": b64(salt), "iv": b64(iv), "ct": b64(ct)}


def decrypt(envelope, passphrase):
    """Python mirror of the browser code (used by tests)."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    d = lambda s: base64.b64decode(s)  # noqa: E731
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=d(envelope["salt"]),
                     iterations=envelope["iter"]).derive(passphrase.encode())
    return json.loads(gzip.decompress(AESGCM(key).decrypt(d(envelope["iv"]), d(envelope["ct"]), None)))


def build(clients_dir, data_dir, out_dir, passphrase, data_repo="", iterations=ITERATIONS):
    if not passphrase or len(passphrase) < 12:
        raise SystemExit("DASHBOARD_PASSPHRASE must be at least 12 characters (use 5+ random words).")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    data = bundle(clients_dir, data_dir, data_repo)
    (out / "data.enc.json").write_text(json.dumps(encrypt(data, passphrase, iterations)), encoding="utf-8")
    shutil.copyfile(ROOT / "dashboard" / "index.html", out / "index.html")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    print(f"Dashboard built: {len(data['clients'])} client(s) -> {out}")
    return data


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--clients-dir", required=True)
    p.add_argument("--data-dir", required=True)
    p.add_argument("--out", default="site")
    a = p.parse_args()
    build(a.clients_dir, a.data_dir, a.out, os.environ.get("DASHBOARD_PASSPHRASE", ""), os.environ.get("DATA_REPO", ""))
