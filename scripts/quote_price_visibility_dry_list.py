"""Dry list (READ ONLY): who reads a project's shared quote BEFORE and AFTER the price rule (owner 2026-11-22), per ACCOUNT.

BEFORE: a global admin, or a member of the project (any project role).
AFTER:  ``access.sees_prices``, the one central rule: never a factory (oem) account; MOTRES staff (global admin / engineer)
        always; anybody else a project owner or a member with ``sees_prices: true``.

    python scripts/quote_price_visibility_dry_list.py --root <projects root> --users <users.json>

For every project with a shared quote the report gives the accounts that LOSE the view and the ones that GAIN it, whether the shared
snapshot is readable (a quote nobody can open is a problem of its own) and every input that could not be read.  Nothing is skipped
silently: an unreadable project.json, shared_quote.json, snapshot or users file is a ``problems`` row and the exit code is 1.
Run with PYTHONPATH=<repo>/src.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

SHARED = Path("cost_estimate") / "shared_quote.json"
SNAPSHOTS = Path("cost_estimate") / "snapshots"


def _read(path: Path):
    """(value, problem): a missing file is (None, None); a file that is there but cannot be read or parsed is (None, reason)."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        return None, "unreadable: %s" % exc
    try:
        return json.loads(raw), None
    except ValueError as exc:
        return None, "not valid JSON: %s" % exc


def load_accounts(users_file: Path | None):
    """{email: role or None for a disabled account}, and the problem when the users file cannot be used."""
    if users_file is None:
        return {}, "no users file given: global roles unknown"
    data, problem = _read(Path(users_file))
    if problem or not isinstance(data, dict):
        return {}, "users file %s" % (problem or "is not an object")
    out: dict[str, Any] = {}
    for email, rec in data.items():
        if isinstance(rec, dict):
            out[str(email).strip().lower()] = None if rec.get("disabled") else str(rec.get("role") or "user").lower()
    return out, None


def _before(email: str, role: str | None, project: dict) -> bool:
    if role == "admin":
        return True
    return any(isinstance(m, dict) and str(m.get("email") or "").strip().lower() == email for m in project.get("members", []))


def scan(root: Path, accounts: dict[str, Any] | None = None):
    """-> (rows, problems).  ``accounts`` as ``load_accounts`` returns it."""
    from motor_ai_sim.development import access
    accounts = accounts or {}
    rows, problems = [], []
    for pdir in sorted(p for p in Path(root).iterdir() if p.is_dir()):
        if not (pdir / "project.json").exists() and not (pdir / SHARED).exists():
            continue
        shared_doc, bad = _read(pdir / SHARED)
        if bad:
            problems.append({"project": pdir.name, "input": str(SHARED), "problem": bad})
            continue
        mark = shared_doc.get("current") if isinstance(shared_doc, dict) else None
        if not mark:
            continue
        project, bad = _read(pdir / "project.json")
        if bad or not isinstance(project, dict):
            problems.append({"project": pdir.name, "input": "project.json", "problem": bad or "missing or not an object"})
            continue
        snapshot = None
        try:
            n = int(mark.get("snapshot_n"))
            snapshot, bad = _read(pdir / SNAPSHOTS / f"{n:04d}.json")
        except (TypeError, ValueError, AttributeError):
            bad = "shared mark has no usable snapshot number"
        if bad is None and not (isinstance(snapshot, dict) and isinstance(snapshot.get("estimate"), dict)):
            bad = "shared snapshot missing or without an estimate"
        if bad:
            problems.append({"project": pdir.name, "input": "shared snapshot", "problem": bad})
        members = [m for m in project.get("members", []) if isinstance(m, dict) and m.get("email")]
        emails = {str(m["email"]).strip().lower() for m in members} | {e for e, r in accounts.items() if r in ("admin", "engineer")}
        lose, gain, no_account = [], [], []
        for email in sorted(emails):
            if email not in accounts:
                no_account.append(email)                       # a member address without an account: reads nothing after, nor before a sign-up
                role = None
                after = False
            else:
                role = accounts[email]
                after = False if role is None else access.sees_prices({"email": email, "role": role}, project)
            before = False if (email in accounts and role is None) else _before(email, role, project)
            if before and not after:
                lose.append(email)
            elif after and not before:
                gain.append(email)
        rows.append({"project": pdir.name, "shared_at": mark.get("shared_at"), "snapshot_readable": bad is None,
                     "owners": [m.get("email") for m in members if m.get("role") == "owner"],
                     "lose_the_view": lose, "gain_the_view": gain, "members_without_account": no_account})
    return rows, problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", required=True, type=Path)
    ap.add_argument("--users", type=Path, help="config/users.json (read only): the global role of every account")
    args = ap.parse_args(argv)
    accounts, bad = load_accounts(args.users)
    rows, problems = scan(args.root, accounts)
    if bad:
        problems.insert(0, {"project": "-", "input": "users", "problem": bad})
    for r in rows:
        print(r["project"], "snapshot:", "ok" if r["snapshot_readable"] else "UNREADABLE", "owners:", r["owners"],
              "lose the view:", r["lose_the_view"], "gain the view:", r["gain_the_view"], "no account:", r["members_without_account"])
    for p in problems:
        print("PROBLEM", p["project"], p["input"], p["problem"])
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
