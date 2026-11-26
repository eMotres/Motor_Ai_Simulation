#!/usr/bin/env python3
"""ask_owner: post a decision, question or message to the owner's queue and read the answer back (docs/OWNER_QUEUE.md).

Standard library only; runs on Linux (us1) and Windows (notebook).

    ask_owner.py --session "US · pump" ask --kind decision --question "Which winding?" --option "A*" --option "B" [--project p1]
                                       [--blocks "the next run waits"] [--detail-file report.md]
    ask_owner.py --session "US · pump" say --title "Report ready" [--detail-file report.md] [--project p1]
    ask_owner.py --session "US · pump" wait oq-0123456789ab [--timeout 3600] [--interval 60]
    ask_owner.py --session "US · pump" list [--state open]
    ask_owner.py --session "US · pump" withdraw oq-0123456789ab

The token file is placed by the OWNER (he creates the token on the Decisions page).  Looked up in this order: --token-file,
$MOTRES_OWNER_QUEUE_TOKEN_FILE, ~/.config/motres/owner_queue.token (Linux), %APPDATA%\\motres\\owner_queue.token (Windows).  The token
is never taken from the command line and never printed.  API base: --api, $MOTRES_API, https://aerostator.com.  Session name:
--session, $MOTRES_SESSION_NAME (required).

Exit codes: 0 done / answered; 1 server or network error; 2 usage or missing token file or local refusal; 3 withdrawn; 4 wait timeout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_API = "https://aerostator.com"
MAX_DETAIL = 4000
#: wait polls no faster than this many seconds (a test may lower it through the environment variable, nothing else may).
MIN_INTERVAL_DEFAULT = 30.0
ENV_MIN_INTERVAL = "MOTRES_OWNER_QUEUE_MIN_INTERVAL_TEST"


class CliError(Exception):
    def __init__(self, msg: str, code: int = 1) -> None:
        super().__init__(msg)
        self.msg = msg
        self.code = code


def default_token_paths() -> List[Path]:
    paths: List[Path] = []
    env = os.environ.get("MOTRES_OWNER_QUEUE_TOKEN_FILE", "").strip()
    if env:
        paths.append(Path(env).expanduser())
    if os.name == "nt":
        appdata = os.environ.get("APPDATA", "").strip()
        if appdata:
            paths.append(Path(appdata) / "motres" / "owner_queue.token")
    else:
        paths.append(Path.home() / ".config" / "motres" / "owner_queue.token")
    return paths


def read_token(token_file: Optional[str]) -> str:
    cands = [Path(token_file).expanduser()] if token_file else default_token_paths()
    for p in cands:
        try:
            lines = p.read_text(encoding="utf-8").strip().splitlines()
        except OSError:
            continue
        tok = lines[0].strip() if lines else ""
        if tok:
            return tok
    raise CliError("no owner-queue token file (looked in: %s); the owner creates the token on the Decisions page and places the file"
                   % ", ".join(str(p) for p in cands), 2)


class Client:
    def __init__(self, api: str, token: str) -> None:
        self.api = api.rstrip("/")
        self._token = token

    def _scrub(self, s: str) -> str:
        return s.replace(self._token, "***") if self._token else s

    def call(self, method: str, path: str, body: Optional[dict] = None, timeout: float = 30.0) -> Any:
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self.api + path, data=data, method=method)
        req.add_header("Authorization", "Bearer " + self._token)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read().decode("utf-8", "replace")).get("detail", "")
            except Exception:                                           # noqa: BLE001
                detail = ""
            if e.code == 401:
                detail = "the token was refused (unknown or revoked)"
            raise CliError(self._scrub("HTTP %d: %s" % (e.code, detail if isinstance(detail, str) else json.dumps(detail))[:400]), 1) from None
        except Exception as e:                                          # noqa: BLE001 - never let a traceback carry the request
            raise CliError(self._scrub("network error: %s" % type(e).__name__), 1) from None
        try:
            return json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            raise CliError("the server answered something that is not JSON", 1) from None


def approval_hash(kind: str, detail, recipient) -> str:
    """Copy of owner_queue.approval_hash (sha256 hex of the canonical JSON {kind, detail, recipient or null}); one shared test vector."""
    canon = json.dumps({"kind": kind, "detail": detail, "recipient": recipient or None},
                       sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def read_detail(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    try:
        text = Path(path).expanduser().read_text(encoding="utf-8")
    except OSError as e:
        raise CliError("cannot read the detail file: %s" % type(e).__name__, 2)
    if len(text) > MAX_DETAIL:
        raise CliError("the detail is %d characters, the limit is %d: one report = one item, do not split it; shorten it and put "
                       "the long version in a file the detail names" % (len(text), MAX_DETAIL), 2)
    return text


def parse_options(raw: List[str]) -> List[Dict[str, Any]]:
    out = []
    for o in raw or []:
        rec = o.endswith("*")
        out.append({"label": o[:-1].strip() if rec else o.strip(), "recommended": rec})
    return out


def answer_view(it: Dict[str, Any]) -> Dict[str, Any]:
    a = it.get("answer") or {}
    out = {"state": it.get("state"), "option": a.get("option"), "text": a.get("text"), "by": a.get("by"), "at": a.get("at")}
    if it.get("priority"):
        out["priority"] = it["priority"]
    if "approved_hash" in a:
        out["approved_hash"] = a["approved_hash"]
    return out


def cmd_wait(c: Client, iid: str, timeout: float, interval: float, sleep, clock) -> int:
    floor = float(os.environ.get(ENV_MIN_INTERVAL, MIN_INTERVAL_DEFAULT))
    interval = max(interval, floor)
    deadline = clock() + timeout
    while True:
        try:
            it = c.call("GET", "/api/owner-queue/agent/items/" + urllib.parse.quote(iid, safe=""))
            if it.get("state") in ("answered", "read"):
                print(json.dumps(answer_view(it), ensure_ascii=False))
                return 0
            if it.get("state") == "withdrawn":
                print(json.dumps(answer_view(it), ensure_ascii=False))
                return 3
        except CliError as e:
            if e.code == 1 and e.msg.startswith(("HTTP 404", "HTTP 401")):
                raise                                   # the item or the token is gone: retrying cannot help
            print("wait: %s (will retry)" % e.msg, file=sys.stderr)
        left = deadline - clock()
        if left <= 0:
            print("timeout: no answer yet for %s" % iid, file=sys.stderr)
            return 4
        sleep(min(interval, left))


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="ask_owner.py", description=__doc__.split("\n\n")[0], allow_abbrev=False)
    ap.add_argument("--api", help="API base (default $MOTRES_API, else %s)" % DEFAULT_API)
    ap.add_argument("--token-file", help="file with the oq_ token (the token itself is never an argument)")
    ap.add_argument("--session", help="the session name shown to the owner (default $MOTRES_SESSION_NAME)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("ask", help="post a decision / question / catalog_write / outside_message; prints the id")
    a.add_argument("--kind", required=True, choices=["decision", "question", "catalog_write", "outside_message"])
    a.add_argument("--question", required=True)
    a.add_argument("--option", action="append", default=[], help="an option; a trailing * marks the recommended one")
    a.add_argument("--project")
    a.add_argument("--blocks", help="one line: what waits for the answer")
    a.add_argument("--detail-file", help="markdown file, up to %d characters (required for catalog_write and outside_message)" % MAX_DETAIL)
    a.add_argument("--recipient", help="outside_message only (required): To / CC addresses, or 'project thread <id>'")
    h = sub.add_parser("hash", help="print the approval hash of local content (compare it with approved_hash before acting)")
    h.add_argument("--kind", required=True, choices=["catalog_write", "outside_message"])
    h.add_argument("--detail-file", required=True)
    h.add_argument("--recipient")
    s = sub.add_parser("say", help="post a message (a report, a notice); prints the id")
    s.add_argument("--title", required=True)
    s.add_argument("--detail-file")
    s.add_argument("--project")
    w = sub.add_parser("wait", help="poll until answered; prints the answer as JSON")
    w.add_argument("id")
    w.add_argument("--timeout", type=float, default=3600.0)
    w.add_argument("--interval", type=float, default=60.0, help="seconds between polls (not below 30)")
    ls = sub.add_parser("list", help="my items")
    ls.add_argument("--state", default="open", help="open | answered | read | withdrawn | '' for all (default open)")
    wd = sub.add_parser("withdraw", help="withdraw an open item")
    wd.add_argument("id")
    return ap


def run(argv: Optional[List[str]] = None, sleep=time.sleep, clock=time.monotonic) -> int:
    args = build_parser().parse_args(argv)
    try:
        session = (args.session or os.environ.get("MOTRES_SESSION_NAME", "")).strip()
        if not session:
            raise CliError("the session name is required: --session or $MOTRES_SESSION_NAME", 2)
        api = args.api or os.environ.get("MOTRES_API", "").strip() or DEFAULT_API
        if args.cmd == "hash":                          # local only: no token, no network
            if args.kind == "outside_message" and not args.recipient:
                raise CliError("--recipient is required for an outside_message", 2)
            print(approval_hash(args.kind, read_detail(args.detail_file), args.recipient if args.kind == "outside_message" else None))
            return 0
        if args.cmd == "ask" and args.kind == "outside_message" and not args.recipient:
            raise CliError("--recipient is required for an outside_message", 2)
        if args.cmd == "ask" and args.kind in ("catalog_write", "outside_message") and not args.detail_file:
            raise CliError("--detail-file is required for a %s: the full text that is approved" % args.kind, 2)
        c = Client(api, read_token(args.token_file))
        base = "/api/owner-queue/agent/items"
        if args.cmd == "ask":
            body = {"kind": args.kind, "asked_by": session, "question": args.question, "options": parse_options(args.option),
                    "project_id": args.project, "blocks": args.blocks, "detail": read_detail(args.detail_file),
                    "recipient": args.recipient}
            if args.kind in ("catalog_write", "outside_message"):
                body["options"] = []                     # the server fixes Approve / Reject
            print(c.call("POST", base, body)["id"])
        elif args.cmd == "say":
            body = {"kind": "message", "asked_by": session, "question": args.title, "project_id": args.project,
                    "detail": read_detail(args.detail_file)}
            print(c.call("POST", base, body)["id"])
        elif args.cmd == "wait":
            return cmd_wait(c, args.id, args.timeout, args.interval, sleep, clock)
        elif args.cmd == "list":
            items = c.call("GET", base + ("?state=" + urllib.parse.quote(args.state) if args.state else ""))["items"]
            for it in items:
                a = it.get("answer") or {}
                print("%s  %-9s %-7s %-13s %s%s" % (it["id"], it["state"], it.get("priority") or "normal", it["kind"], it["question"],
                                               ("  -> " + str(a.get("option") or a.get("text") or "")) if a else ""))
        elif args.cmd == "withdraw":
            c.call("POST", base + "/" + urllib.parse.quote(args.id, safe="") + "/withdraw")
            print("withdrawn " + args.id)
        return 0
    except CliError as e:
        print("ask_owner: " + e.msg, file=sys.stderr)
        return e.code


if __name__ == "__main__":
    raise SystemExit(run())
