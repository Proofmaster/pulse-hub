"""Which free search endpoints answer from *this* machine? (Run on GitHub Actions: "Search probe" workflow.)

Search engines often serve empty or bot-check pages to data-centre IPs, which looks exactly like "no results".
This prints, per engine and query style, how many rows come back, so the `web` source can be pointed at
whatever actually works. No secrets needed; nothing is written anywhere.
"""
import logging
import time

import requests

QUERIES = ['site:reddit.com "merge pdf"', "merge pdf reddit"]
ENGINES = ["duckduckgo", "brave", "yahoo", "startpage", "mojeek"]
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"}


def probe_ddgs():
    from ddgs import DDGS
    print("== ddgs, one engine at a time ==")
    for engine in ENGINES:
        for q in QUERIES:
            for tl in ("w", None):
                t = time.time()
                try:
                    rows = DDGS(timeout=15).text(q, backend=engine, timelimit=tl, max_results=10)
                    reddit = sum("reddit.com" in (r.get("href") or "") for r in rows)
                    res = f"{len(rows):>2} rows, {reddit:>2} reddit"
                except Exception as e:  # noqa: BLE001
                    res = f"ERROR {type(e).__name__}: {str(e)[:110]}"
                print(f"{engine:<11} tl={str(tl):<4} {q!r:<32} {res}  ({time.time() - t:.1f}s)")
                time.sleep(1.5)


def probe_http():
    print("== raw endpoints ==")
    checks = {
        "bing rss": "https://www.bing.com/search?format=rss&q=site%3Areddit.com+%22merge+pdf%22",
        "ddg lite": "https://lite.duckduckgo.com/lite/?q=site%3Areddit.com+%22merge+pdf%22",
        "ddg html": "https://html.duckduckgo.com/html/?q=site%3Areddit.com+%22merge+pdf%22",
        "arctic shift": "https://arctic-shift.photon-reddit.com/api/posts/search?subreddit=pdf&limit=5",
        "google news rss": "https://news.google.com/rss/search?q=pdf+editor",
    }
    for name, url in checks.items():
        try:
            r = requests.get(url, headers=UA, timeout=20)
            body = r.text
            hint = next((w for w in ("captcha", "anomaly", "unusual traffic", "blocked", "challenge")
                         if w in body.lower()), "")
            print(f"{name:<16} HTTP {r.status_code}  {len(body):>7} bytes  reddit-links={body.count('reddit.com/r/'):>3}"
                  f"  {('BOT-CHECK: ' + hint) if hint else ''}")
        except Exception as e:  # noqa: BLE001
            print(f"{name:<16} ERROR {type(e).__name__}: {str(e)[:120]}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    probe_http()
    probe_ddgs()
