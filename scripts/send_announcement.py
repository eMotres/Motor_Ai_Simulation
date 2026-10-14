#!/usr/bin/env python
"""Send the one-time announcement e-mail (the launch mailing) to registered accounts.

DRY-RUN IS THE DEFAULT.  Nothing is sent unless you pass ``--send --confirm <count>``.

    # 1. see who would get it and read every rendered message (writes .eml files, sends nothing)
    PYTHONPATH=src python scripts/send_announcement.py

    # 2. one copy to your own address (links work if that address is an account)
    PYTHONPATH=src python scripts/send_announcement.py --test-to you@example.com

    # 3. the real send; --confirm must equal the "would send" count printed by step 1
    PYTHONPATH=src python scripts/send_announcement.py --send --confirm 14

On the server run it inside the API container (or with the API's environment: MOTOR_AI_SIM_CONFIG, SMTP_*, PUBLIC_APP_URL,
AUTH_SECRET / COMM_PREFS_SECRET) so it reads the real users and signs links with the real key.

Rules: only verified, enabled accounts; never an account that unsubscribed (communication preferences or the legacy newsletter);
one message per account per campaign id (``<identity root>/mailing/<campaign>.sendlog.jsonl`` is the record: user, time,
message-id); ``--delay`` seconds between messages (default 3); stops after 5 failures in a row.  Language: the account's stored
UI language, else the language of its last preference change, else English (``--default-lang``).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from motor_ai_sim import auth_email as E          # noqa: E402
from motor_ai_sim import mailing as M             # noqa: E402


def _args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--campaign", default=M.DEFAULT_CAMPAIGN, help="campaign id = template name (default %(default)s)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="the default: render .eml files, send nothing")
    g.add_argument("--send", action="store_true", help="really send (needs --confirm)")
    ap.add_argument("--confirm", type=int, default=None, help="with --send: the number of messages this run will send")
    ap.add_argument("--test-to", metavar="ADDRESS", help="send ONE test copy to this address only, then stop")
    ap.add_argument("--out-dir", help="dry-run output folder (default: a temp folder, printed)")
    ap.add_argument("--delay", type=float, default=3.0, help="seconds between messages (default 3)")
    ap.add_argument("--limit", type=int, default=None, help="mail at most N accounts in this run")
    ap.add_argument("--retry-failed", action="store_true", help="also retry accounts whose earlier attempt FAILED")
    ap.add_argument("--default-lang", default="en", choices=("en", "zh-CN"))
    ap.add_argument("--base-url", default=None, help="site base for the links (default: PUBLIC_APP_URL or https://aerostator.com)")
    ap.add_argument("--users-file", help="read a copy of users.json instead of the live registry")
    ap.add_argument("--locales-file", help="read a copy of .user_prefs.json instead of the live language store")
    ap.add_argument("--log", help="send-log path (default <identity root>/mailing/<campaign>.sendlog.jsonl)")
    return ap.parse_args(argv)


def main(argv=None, *, deliver=None, sleep=None) -> int:
    a = _args(argv)
    try:
        M.check_campaign(a.campaign)
        M.load_template(a.campaign, "en")
    except (ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    base = (a.base_url or os.environ.get("PUBLIC_APP_URL") or M.DEFAULT_BASE_URL).rstrip("/")
    api_base = (os.environ.get("PUBLIC_API_URL") or "").strip().rstrip("/") or base
    sender = E.mail_from() or "noreply@aerostator.com"
    deliver = deliver or E.deliver
    sleep = sleep or __import__("time").sleep

    try:
        users = M.load_users(Path(a.users_file) if a.users_file else None)
    except Exception as e:                                   # noqa: BLE001
        print(f"error: cannot read the user registry ({type(e).__name__}: {e}); nothing sent", file=sys.stderr)
        return 2
    locales = None
    if a.locales_file:
        raw = json.loads(Path(a.locales_file).read_text(encoding="utf-8"))
        locales = {str(k).lower(): (v or {}).get("locale") for k, v in raw.items() if isinstance(v, dict)}

    if a.test_to:
        return _test(a, users, base, api_base, sender, deliver)

    recipients, skipped = M.collect_audience(users, locales=locales, default_lang=a.default_lang)
    log_file = Path(a.log) if a.log else M.log_path(a.campaign)
    p = M.plan(a.campaign, recipients, skipped, M.read_log(log_file), retry_failed=a.retry_failed, limit=a.limit)
    langs = Counter(r.lang for r in p.to_send)
    reasons = Counter(why for _, why in p.skipped)

    print(f"campaign {a.campaign}: {len(users)} accounts, would send {len(p.to_send)} "
          f"({', '.join(f'{n} {k}' for k, n in sorted(langs.items())) or 'none'})")
    for why, n in sorted(reasons.items()):
        print(f"  skipped {n}: {why}")

    if not a.send:
        out = Path(a.out_dir) if a.out_dir else Path(tempfile.mkdtemp(prefix=f"aerostator_mailing_{a.campaign}_"))
        paths = M.write_eml(p, out, base_url=base, api_base=api_base, sender=sender)
        print(f"DRY RUN: wrote {len(paths)} .eml file(s) to {out}")
        print(f"nothing was sent. To send for real: --send --confirm {len(p.to_send)}")
        return 0

    if a.confirm is None or a.confirm != len(p.to_send):
        print(f"refused: --send needs --confirm {len(p.to_send)} (the number of messages this run would send); got {a.confirm}",
              file=sys.stderr)
        return 2
    if not p.to_send:
        print("nothing to send.")
        return 0
    if not E.smtp_configured():
        print("refused: SMTP is not configured (SMTP_USER / SMTP_PASS).", file=sys.stderr)
        return 2
    try:
        r = M.send(p, deliver=deliver, log_file=log_file, base_url=base, api_base=api_base, sender=sender, delay_s=a.delay, sleep=sleep)
    except M.SendAborted as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    print(f"sent {r['sent']}, failed {r['failed']}. log: {log_file}")
    if r["aborted"]:
        print(f"ABORTED: {r['aborted']}", file=sys.stderr)
        return 1
    return 1 if r["failed"] else 0


def _test(a, users, base, api_base, sender, deliver) -> int:
    """One copy to --test-to.  The links are those of the account with that address when there is one, else dead links.
    Not recorded in the campaign log: a test never counts as 'sent'."""
    addr = a.test_to.strip().lower()
    if not M._valid_email(addr):
        print(f"error: {a.test_to!r} is not an e-mail address", file=sys.stderr)
        return 2
    if not E.smtp_configured():
        print("refused: SMTP is not configured (SMTP_USER / SMTP_PASS).", file=sys.stderr)
        return 2
    rec = users.get(addr) if isinstance(users.get(addr), dict) else {}
    lang = (M.stored_locales().get(addr) if not a.locales_file else None) or a.default_lang
    msg = M.build_message(a.campaign, lang, email=addr, name=str(rec.get("name") or ""), base_url=base, api_base=api_base,
                          sender=sender, subject_prefix="[TEST] ")
    deliver(msg)
    print(f"test copy sent to {addr} ({M.CP.norm_lang(lang)}), message-id {msg['Message-ID']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
