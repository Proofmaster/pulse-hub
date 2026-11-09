"""Run Pulse for every active client (or the ones named with --client).

    python run.py --clients-dir ../pulse-hub-data/clients --data-dir ../pulse-hub-data/data
    python run.py --client acme --clients-dir ... --data-dir ...
    python run.py --validate --clients-dir ...        # check profiles only, no network
"""
import argparse
import os
import sys
import traceback
from datetime import datetime, timezone

from pulse import config, store
from pulse.client import ProfileError, load_clients
from pulse.pipeline import run_client
from pulse.sources import f5bot


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--clients-dir", default=os.environ.get("PULSE_CLIENTS_DIR", "clients"))
    p.add_argument("--data-dir", default=os.environ.get("PULSE_DATA_DIR", "data"))
    p.add_argument("--client", action="append", help="only this client slug (repeatable)")
    p.add_argument("--validate", action="store_true", help="validate profiles and exit")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    profile_errors = []
    try:
        # --validate is strict; a scheduled run skips a broken profile and still serves everyone else.
        clients = load_clients(args.clients_dir, only=args.client,
                               errors=None if args.validate else profile_errors)
    except ProfileError as e:
        print(f"Profile error: {e}")
        return 2
    for err in profile_errors:
        print(f"::error::Skipped client profile - {err}")
    if args.validate:
        for c in clients:
            sources = [n for n in c.sources if c.enabled(n)]
            print(f"OK  {c.slug:<20} {c.name}  sources={sources}  slack_secret={c.slack_secret}")
        print(f"{len(clients)} profile(s) valid.")
        return 0
    if not clients:
        print("No active clients.")
        return 1 if profile_errors else 0

    now_iso = datetime.now(timezone.utc).isoformat()
    f5_leads, f5_ids = [], []
    if any(c.enabled("f5bot") for c in clients):
        f5_leads, f5_ids = f5bot.fetch(now_iso)
    routed = f5bot.route(f5_leads, clients)

    failed = []
    for client in clients:
        print(f"===== {client.slug} =====")
        try:
            run_client(client, store.results_path(args.data_dir, client.slug), now_iso,
                       f5bot_leads=routed.get(client.slug, []))
        except Exception:  # noqa: BLE001 - one broken client must not block the others
            traceback.print_exc()
            failed.append(client.slug)

    if f5_ids and not failed and config.F5BOT_MARK_SEEN:
        f5bot.mark_seen(f5_ids)  # only once every client's data is safely written
    elif f5_ids:
        print("[f5bot] alerts left unread (a client failed, or F5BOT_MARK_SEEN=false).")
    if failed:
        print(f"FAILED clients: {failed}")
    return 1 if failed or profile_errors else 0


if __name__ == "__main__":
    sys.exit(main())
