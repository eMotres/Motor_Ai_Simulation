"""Account deletion and data export (GDPR Art. 15, 17, 20).

Audit 2026-09-29 findings #5 and #6: ``users.delete_user`` removed only the
users.json record, the admin delete kept the workspace, and nothing could hand
a person their data.  This module is the ONE place that knows every store that
holds a person's data, so deletion and export cannot drift apart.

Deletion
--------
Self-service deletion is a REQUEST with a grace period
(``ACCOUNT_DELETE_GRACE_DAYS``, default 7): re-authentication is required to
file it, the account keeps working meanwhile so the person can cancel, and the
daily retention job (:mod:`motor_ai_sim.retention`) purges requests that fell
due.  An admin delete purges immediately.  :func:`purge` then:

==========================  ==================================================
store                       what happens
==========================  ==================================================
users.json                  record removed
workspace dir               removed (``<WORKSPACES_ROOT>/<ws_id>``), memory evicted
published/<ws_id>           removed (the person's published designs)
.sessions.json              every session record of the account removed
agent_keys.json             every key of the account removed
oauth_grants.json           grants, codes and pending requests removed
newsletter.json             subscriber record + notice reads removed
support access requests     removed (contact data the person gave us)
mcp_audit.jsonl             e-mail -> subject hash (security log, kept)
auth_events.jsonl           e-mail -> subject hash, IP re-hashed (kept)
usage (history.sqlite)      user column -> subject hash (billing aggregates kept)
admin_audit.jsonl           subject/target e-mail -> subject hash (kept)
deleted_subjects.jsonl      one line appended: subject hash, ws_id, when
==========================  ==================================================

The subject hash is ``subj_`` + HMAC-SHA256(deployment secret, e-mail)[:16]:
stable (the same person maps to the same token in every log, so aggregates
stay consistent) and not reversible without the secret.

Backups
-------
Restic snapshots keep deleted data until they expire (hourly 24 h, daily 30 d,
monthly 12 mo — see ``deploy/backup``).  The register ``deleted_subjects.jsonl``
lives in the identity directory, which is itself backed up, and
:func:`reapply_deletions` (``python -m motor_ai_sim.account_lifecycle
reapply``) must be run after ANY restore: it purges every account and
workspace whose subject hash or ws_id is in the register.  If a restore brings
back an OLDER register, feed the newest one with ``--register <file>``.

Export
------
:func:`build_export` writes a ZIP (account info, consents, sessions, keys,
grants, auth events, MCP audit, support requests, usage rows, admin-access
log, the whole workspace and published folders).  The HTTP side hands out a
signed, single-use link valid for ``ACCOUNT_EXPORT_TTL_S`` (default 900 s).
"""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import logging
import os
import secrets
import shutil
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from motor_ai_sim.private_files import (chmod_private, ensure_private_dir,
                                        open_private)

log = logging.getLogger(__name__)

_LOCK = threading.RLock()
DAY_S = 86400


def grace_days() -> int:
    try:
        return max(0, int(os.environ.get("ACCOUNT_DELETE_GRACE_DAYS", "") or 7))
    except ValueError:
        return 7


def export_ttl_s() -> int:
    try:
        return max(60, int(os.environ.get("ACCOUNT_EXPORT_TTL_S", "") or 900))
    except ValueError:
        return 900


def export_max_bytes() -> int:
    try:
        return int(os.environ.get("ACCOUNT_EXPORT_MAX_BYTES", "") or 2 * 1024 ** 3)
    except ValueError:
        return 2 * 1024 ** 3


# ── where ────────────────────────────────────────────────────────────────────

def identity_dir() -> Path:
    from motor_ai_sim import users as U
    return Path(U._USERS_FILE).parent


def _requests_file() -> Path:
    return identity_dir() / "account_deletions.json"


def register_file() -> Path:
    env = (os.environ.get("DELETED_SUBJECTS_FILE") or "").strip()
    return Path(env).expanduser() if env else identity_dir() / "deleted_subjects.jsonl"


def _norm(email: str) -> str:
    return (email or "").strip().lower()


def _key(label: bytes) -> bytes:
    from motor_ai_sim import users as U
    return hmac.new(U._secret().encode("utf-8"), label, hashlib.sha256).digest()


def subject_hash(email: str) -> str:
    """Stable pseudonym of a person: ``subj_<16 hex>``."""
    d = hmac.new(_key(b"subject-hash-v1"), _norm(email).encode("utf-8"),
                 hashlib.sha256).hexdigest()
    return "subj_" + d[:16]


def _ws_dirs(email: str) -> Dict[str, Optional[Path]]:
    """The person's workspace and published folders — computed, never
    provisioned (``workspace_for_identity`` would CREATE the workspace)."""
    from motor_ai_sim import workspace as W
    root = W.workspaces_root()
    wsid = W.workspace_id(_norm(email))
    ws = (root / wsid) if root is not None else None
    pub = None
    if root is not None:
        try:
            pub = W.published_root() / wsid
        except Exception:                                   # noqa: BLE001
            pub = None
    return {"id": wsid, "workspace": ws, "published": pub, "root": root}


def _rmtree_contained(path: Optional[Path], base: Optional[Path]) -> bool:
    """rm -rf ``path`` only if it is a real directory strictly inside ``base``
    (never the base, never through a symlink, never the process config)."""
    if path is None or base is None:
        return False
    from motor_ai_sim.safe_paths import is_within
    p, b = Path(path), Path(base)
    if p.is_symlink():
        p.unlink()
        return True
    if not p.is_dir() or not is_within(p, b) or os.path.realpath(p) == os.path.realpath(b):
        return False
    shutil.rmtree(p)
    return True


# ── re-authentication ────────────────────────────────────────────────────────

REAUTH_MAX_AGE_S = 600


def reauthenticate(email: str, *, password: Optional[str] = None,
                   google_credential: Optional[str] = None) -> bool:
    """True when the caller proved, just now, that they hold the account.

    A password account answers with its password; a Google account with a
    FRESH Google ID token for the same address (issued within 10 minutes).
    """
    email = _norm(email)
    if password:
        from motor_ai_sim import users as U
        return U.check_login(email, password) is not None
    if google_credential:
        from motor_ai_sim import auth as A
        claims = A._verify_google_token(google_credential)
        if not claims:
            return False
        if _norm(claims.get("email") or "") != email:
            return False
        if claims.get("email_verified") is False:
            return False
        return time.time() - float(claims.get("iat") or 0) <= REAUTH_MAX_AGE_S
    return False


# ── deletion requests (grace period) ─────────────────────────────────────────

def _load_requests() -> Dict[str, Any]:
    from motor_ai_sim.json_store import read_json
    d = read_json(_requests_file(), default={})
    return d if isinstance(d, dict) else {}


def _mutate_requests(fn) -> Dict[str, Any]:
    from motor_ai_sim.json_store import mutate_json
    ensure_private_dir(identity_dir())
    out = mutate_json(_requests_file(), lambda d: fn(d if isinstance(d, dict) else {}) or d,
                      default={})
    chmod_private(_requests_file())
    return out


def pending(email: str) -> Optional[Dict[str, Any]]:
    rec = _load_requests().get(_norm(email))
    return dict(rec) if isinstance(rec, dict) else None


def list_pending() -> List[Dict[str, Any]]:
    return sorted((dict(v, email=k) for k, v in _load_requests().items()
                   if isinstance(v, dict)), key=lambda r: r.get("due_at") or 0)


def request_deletion(email: str, *, by: str = "self",
                     now: Optional[float] = None) -> Dict[str, Any]:
    """File (or return the existing) deletion request.  Re-auth is the
    CALLER's job (:func:`reauthenticate`); this only records the intent."""
    email = _norm(email)
    t = time.time() if now is None else float(now)
    rec = {"requested_at": t, "due_at": t + grace_days() * DAY_S, "by": by}

    def _fn(d):
        if not isinstance(d.get(email), dict):
            d[email] = rec
        return d
    d = _mutate_requests(_fn)
    try:
        from motor_ai_sim import sessions as S
        S.record_event("delete_requested", email=email, reason=by)
    except Exception:                                       # noqa: BLE001
        pass
    return dict(d.get(email) or rec)


def cancel_deletion(email: str) -> bool:
    email = _norm(email)
    hit = {"ok": False}

    def _fn(d):
        if d.pop(email, None) is not None:
            hit["ok"] = True
        return d
    _mutate_requests(_fn)
    if hit["ok"]:
        try:
            from motor_ai_sim import sessions as S
            S.record_event("delete_cancelled", email=email)
        except Exception:                                   # noqa: BLE001
            pass
    return hit["ok"]


def run_due(now: Optional[float] = None) -> List[str]:
    """Purge every request whose grace period has ended.  Returns the subject
    hashes purged (never e-mails — this list goes into logs)."""
    t = time.time() if now is None else float(now)
    done: List[str] = []
    for r in list_pending():
        if float(r.get("due_at") or 0) <= t:
            rep = purge(r["email"], actor="retention", reason="self_request_due")
            done.append(rep["subject"])
    return done


# ── the purge ────────────────────────────────────────────────────────────────

def _rewrite_jsonl(path: Path, fn) -> int:
    """Apply ``fn(rec) -> bool changed`` to every line; atomic, 0600."""
    if not path.is_file():
        return 0
    from motor_ai_sim.json_store import lock_for
    n = 0
    with lock_for(path):
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        out = []
        for ln in lines:
            try:
                rec = json.loads(ln)
            except ValueError:
                out.append(ln)
                continue
            if isinstance(rec, dict) and fn(rec):
                n += 1
            out.append(json.dumps(rec, ensure_ascii=False, default=str))
        if n:
            tmp = path.with_suffix(path.suffix + ".tmp")
            with open_private(tmp, "w") as f:
                f.write("\n".join(out) + ("\n" if out else ""))
            os.replace(tmp, path)
            chmod_private(path)
    return n


def _step(report: Dict[str, Any], name: str, fn) -> None:
    try:
        report["steps"][name] = fn()
    except Exception as e:                                  # noqa: BLE001
        report["steps"][name] = f"FAILED: {type(e).__name__}: {e}"
        report["ok"] = False
        log.error("account purge step %s failed (%s: %s)", name, type(e).__name__, e)


def purge(email: str, *, actor: str, reason: str = "") -> Dict[str, Any]:
    """Hard-delete / pseudonymise everything about ``email``.  Idempotent: a
    second run finds nothing and reports zeros.  Returns a report WITHOUT the
    e-mail in it (it is logged)."""
    email = _norm(email)
    subj = subject_hash(email)
    dirs = _ws_dirs(email)
    report: Dict[str, Any] = {"subject": subj, "ws_id": dirs["id"], "ok": True,
                              "steps": {}}

    def _sessions():
        from motor_ai_sim import sessions as S
        with S._LOCK:
            try:
                d = S._load()
            except S.StoreUnavailable:
                raise
            gone = [sid for sid, r in d.items()
                    if isinstance(r, dict) and _norm(r.get("email")) == email]
            for sid in gone:
                d.pop(sid, None)
            if gone:
                S._save(d)
        return len(gone)

    def _agent_keys():
        from motor_ai_sim import agent_keys as K
        from motor_ai_sim.json_store import mutate_json
        n = {"n": 0}

        def _fn(d):
            d = d if isinstance(d, dict) else {}
            keys = d.get("keys") or {}
            for kid in [k for k, r in keys.items() if _norm((r or {}).get("owner")) == email]:
                keys.pop(kid, None)
                n["n"] += 1
            d["keys"] = keys
            return d
        if K.store_path().is_file():
            mutate_json(K.store_path(), _fn, default={})
        return n["n"]

    def _oauth():
        from motor_ai_sim import oauth as O
        from motor_ai_sim.json_store import mutate_json
        n = {"n": 0}

        def _fn(d):
            d = d if isinstance(d, dict) else {}
            for tbl in ("grants", "codes", "requests"):
                t = d.get(tbl) or {}
                for k in [k for k, r in t.items()
                          if isinstance(r, dict) and _norm(r.get("owner")) == email]:
                    t.pop(k, None)
                    n["n"] += 1
                d[tbl] = t
            return d
        if O.store_path().is_file():
            mutate_json(O.store_path(), _fn, default={})
        return n["n"]

    def _newsletter():
        from motor_ai_sim import newsletter as N
        with N._LOCK:
            if not N._STORE_FILE.is_file():
                return 0
            d = N._load()
            n = 0
            if d["subscribers"].pop(email, None) is not None:
                n += 1
            if d["notice_reads"].pop(email, None) is not None:
                n += 1
            if n:
                N._save(d)
        return n

    def _support():
        from motor_ai_sim import support_store as SS
        ids = [r["id"] for r in SS.list_access_requests()
               if _norm(r.get("email")) == email]
        return sum(1 for rid in ids if SS.delete_request(rid))

    def _mcp_audit():
        from motor_ai_sim import agent_keys as K

        def _fn(rec):
            if _norm(rec.get("email")) == email:
                rec["email"] = subj
                return True
            return False
        return _rewrite_jsonl(K.audit_path(), _fn)

    def _auth_events():
        from motor_ai_sim import sessions as S
        from motor_ai_sim.log_redaction import hash_ip

        def _fn(rec):
            if _norm(rec.get("email")) == email:
                rec["email"] = subj
                if rec.get("ip"):
                    rec["ip"] = hash_ip(str(rec["ip"]))
                rec.pop("user_agent", None)
                return True
            return False
        return _rewrite_jsonl(S._EVENTS_FILE, _fn)

    def _usage():
        from motor_ai_sim import cluster_monitor as CM
        if not CM._db_file().is_file():
            return 0
        from motor_ai_sim import usage_stats as US
        con = US._db()
        try:
            n = 0
            for tbl in ("activity", "storage", "usage"):
                try:
                    cur = con.execute(f"UPDATE OR REPLACE {tbl} SET user=? "
                                      f"WHERE lower(user)=?", (subj, email))
                    n += cur.rowcount or 0
                except Exception:                           # noqa: BLE001
                    continue
            con.commit()
        finally:
            con.close()
        return n

    def _admin_audit():
        from motor_ai_sim import admin_audit as AA
        return AA.pseudonymise_subject(email, subj)

    def _workspace():
        from motor_ai_sim import workspace as W
        root = dirs["root"]
        removed = {"workspace": False, "published": False}
        if root is None:
            return "single-user install: no per-account workspace"
        key = (str(root), dirs["id"])
        with W._REG_LOCK:
            ws = W._REG.pop(key, None)
        if ws is not None:
            try:
                ws.state.evict()
            except Exception:                               # noqa: BLE001
                pass
        removed["workspace"] = _rmtree_contained(dirs["workspace"], root)
        try:
            removed["published"] = _rmtree_contained(dirs["published"],
                                                     W.published_root())
        except Exception:                                   # noqa: BLE001
            pass
        return removed

    def _user():
        from motor_ai_sim import users as U
        try:
            U.delete_user(email)
            return 1
        except KeyError:
            return 0

    def _request():
        hit = {"n": 0}

        def _fn(d):
            if d.pop(email, None) is not None:
                hit["n"] = 1
            return d
        if _requests_file().is_file():
            _mutate_requests(_fn)
        return hit["n"]

    for name, fn in (("deletion_request", _request), ("sessions", _sessions), ("agent_keys", _agent_keys),
                     ("oauth", _oauth), ("newsletter", _newsletter),
                     ("support_requests", _support), ("mcp_audit", _mcp_audit),
                     ("auth_events", _auth_events), ("usage", _usage),
                     ("admin_audit", _admin_audit), ("workspace", _workspace),
                     ("users", _user)):
        _step(report, name, fn)

    _append_register(subj, dirs["id"], actor=actor, reason=reason)
    try:
        from motor_ai_sim import admin_audit as AA
        AA.record(actor, "user.delete", subj, subject=subj,
                  details={"reason": reason, "ok": report["ok"]})
    except Exception:                                       # noqa: BLE001
        pass
    log.warning("account purged: %s (ws %s) by %s — ok=%s",
                subj, dirs["id"], actor, report["ok"])
    return report


def _append_register(subj: str, wsid: str, *, actor: str, reason: str) -> None:
    p = register_file()
    ensure_private_dir(p.parent)
    rec = {"subject": subj, "ws_id": wsid, "deleted_at": round(time.time(), 3),
           "actor": "admin" if actor not in ("self", "retention") else actor,
           "reason": reason}
    with _LOCK:
        with open_private(p, "a") as f:
            f.write(json.dumps(rec, sort_keys=True) + "\n")


def deleted_subjects(register: Optional[Path] = None) -> List[Dict[str, Any]]:
    p = Path(register) if register else register_file()
    if not p.is_file():
        return []
    out = []
    for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if isinstance(r, dict) and r.get("subject"):
            out.append(r)
    return out


def reapply_deletions(register: Optional[Path] = None) -> Dict[str, Any]:
    """After a backup restore: purge every account / workspace that the
    register says was deleted.  Idempotent."""
    from motor_ai_sim import users as U
    from motor_ai_sim import workspace as W
    reg = deleted_subjects(register)
    subjects = {r["subject"] for r in reg}
    ws_ids = {r.get("ws_id") for r in reg if r.get("ws_id")}
    purged = 0
    for email in list(U._load_soft().keys()):
        if subject_hash(email) in subjects:
            purge(email, actor="restore-replay", reason="reapply_deletions")
            purged += 1
    orphans = 0
    root = W.workspaces_root()
    if root is not None:
        for wsid in ws_ids:
            if _rmtree_contained(root / wsid, root):
                orphans += 1
            try:
                if _rmtree_contained(W.published_root() / wsid, W.published_root()):
                    orphans += 1
            except Exception:                               # noqa: BLE001
                pass
    return {"register_entries": len(reg), "accounts_purged": purged,
            "orphan_dirs_removed": orphans}


# ── export ───────────────────────────────────────────────────────────────────

_USED_NONCES: Dict[str, float] = {}
_EXPORT_ISS = "motor-ai-sim-export"


def issue_export_token(email: str, *, for_admin: str = "") -> Dict[str, Any]:
    import jwt
    now = int(time.time())
    exp = now + export_ttl_s()
    claims = {"sub": _norm(email), "purpose": "export", "iat": now, "exp": exp,
              "nonce": secrets.token_urlsafe(16), "iss": _EXPORT_ISS}
    if for_admin:
        claims["adm"] = _norm(for_admin)
    tok = jwt.encode(claims, _key(b"account-export-v1"), algorithm="HS256")
    return {"token": tok, "expires_at": exp}


def consume_export_token(token: str) -> Optional[Dict[str, Any]]:
    """Claims of a valid, unexpired, not-yet-used export token, else None."""
    import jwt
    try:
        c = jwt.decode(token or "", _key(b"account-export-v1"), algorithms=["HS256"],
                       issuer=_EXPORT_ISS, options={"require": ["exp", "sub", "nonce"]})
    except Exception:                                       # noqa: BLE001
        return None
    if c.get("purpose") != "export":
        return None
    now = time.time()
    with _LOCK:
        for n in [n for n, t in _USED_NONCES.items() if t < now]:
            _USED_NONCES.pop(n, None)
        if c["nonce"] in _USED_NONCES:
            return None
        _USED_NONCES[c["nonce"]] = float(c["exp"])
    return c


def _jsonl_for(path: Path, email: str, field: str = "email") -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    out = []
    for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if isinstance(r, dict) and _norm(r.get(field)) == email:
            out.append(r)
    return out


def _add_tree(zf: zipfile.ZipFile, src: Optional[Path], arc: str, budget: List[int]) -> int:
    """Add every regular file under ``src`` (symlinks NOT followed)."""
    if src is None or not Path(src).is_dir():
        return 0
    n = 0
    for dirpath, dirnames, filenames in os.walk(src, followlinks=False):
        dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))]
        for fn in filenames:
            fp = os.path.join(dirpath, fn)
            if os.path.islink(fp) or not os.path.isfile(fp):
                continue
            size = os.path.getsize(fp)
            if budget[0] - size < 0:
                raise ValueError("export exceeds ACCOUNT_EXPORT_MAX_BYTES")
            budget[0] -= size
            rel = os.path.relpath(fp, src).replace(os.sep, "/")
            zf.write(fp, f"{arc}/{rel}")
            n += 1
    return n


def build_export(email: str, dest: Optional[Path] = None) -> Path:
    """Write the person's data to a ZIP and return its path (0600)."""
    from motor_ai_sim import agent_keys as K
    from motor_ai_sim import oauth as O
    from motor_ai_sim import sessions as S
    from motor_ai_sim import users as U
    email = _norm(email)
    if dest is None:
        tmpdir = ensure_private_dir(Path(tempfile.gettempdir()) / "motres-exports")
        dest = tmpdir / f"export-{secrets.token_hex(8)}.zip"
    dest = Path(dest)
    fd = os.open(str(dest), os.O_WRONLY | os.O_CREAT | os.O_TRUNC
                 | getattr(os, "O_BINARY", 0), 0o600)
    os.close(fd)
    chmod_private(dest)
    budget = [export_max_bytes()]
    dirs = _ws_dirs(email)

    def js(obj) -> str:
        return json.dumps(obj, indent=1, ensure_ascii=False, default=str)

    rec = U.get_user(email) or {}
    account = {k: v for k, v in rec.items()
               if not str(k).startswith("pw_")
               and k not in ("password", "link_nonces", "hash", "salt")}
    account["email"] = email
    try:
        from motor_ai_sim import newsletter as N
        nl = N._load()["subscribers"].get(email) or {}
    except Exception:                                       # noqa: BLE001
        nl = {}
    try:
        from motor_ai_sim import support_store as SS
        tickets = [r for r in SS.list_access_requests() if _norm(r.get("email")) == email]
    except Exception:                                       # noqa: BLE001
        tickets = []
    try:
        from motor_ai_sim import usage_stats as US
        con = US._db()
        try:
            usage = {"activity": [list(r) for r in con.execute(
                        "SELECT day,event,key,n FROM activity WHERE lower(user)=?", (email,))],
                     "storage": [list(r) for r in con.execute(
                        "SELECT day,category,bytes FROM storage WHERE lower(user)=?", (email,))],
                     "jobs": [list(r) for r in con.execute(
                        "SELECT run_id,ts_start,ts_end,kind,machine,wall_s,cpu_s,status "
                        "FROM usage WHERE lower(user)=?", (email,))]}
        finally:
            con.close()
    except Exception:                                       # noqa: BLE001
        usage = {}
    try:
        from motor_ai_sim import admin_audit as AA
        admin_access = AA.for_subject(email, limit=10000)
    except Exception:                                       # noqa: BLE001
        admin_access = []

    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("README.txt", (
            "Your data from the eMotres / Aerostator simulation service.\n"
            "account.json        your account record (no password hash)\n"
            "consents.json       newsletter consent and its history\n"
            "deletion.json       a pending deletion request, if any\n"
            "sessions.json       your sign-in sessions (IP addresses are stored hashed)\n"
            "agent_keys.json     your agent keys (public part only)\n"
            "oauth_grants.json   apps you authorised\n"
            "auth_events.jsonl   sign-in events about your account\n"
            "mcp_audit.jsonl     calls made with your agent keys\n"
            "support_requests.json  access/support requests you filed\n"
            "usage.json          usage counters and job records\n"
            "admin_access.json   every time an administrator acted on your account\n"
            "workspace/          your machines, dies, configurations and results\n"
            "published/          what you published to other users\n"))
        zf.writestr("account.json", js(account))
        zf.writestr("consents.json", js(nl))
        zf.writestr("deletion.json", js(pending(email)))
        zf.writestr("sessions.json", js([S.public(r) for r in S.list_for(email)]))
        zf.writestr("agent_keys.json", js(K.list_keys(email)))
        zf.writestr("oauth_grants.json", js(O.list_grants(email)))
        zf.writestr("auth_events.jsonl", "\n".join(
            json.dumps(r, ensure_ascii=False) for r in _jsonl_for(S._EVENTS_FILE, email)))
        zf.writestr("mcp_audit.jsonl", "\n".join(
            json.dumps(r, ensure_ascii=False) for r in _jsonl_for(K.audit_path(), email)))
        zf.writestr("support_requests.json", js(tickets))
        zf.writestr("usage.json", js(usage))
        zf.writestr("admin_access.json", js(admin_access))
        if dirs["root"] is not None:
            _add_tree(zf, dirs["workspace"], "workspace", budget)
            _add_tree(zf, dirs["published"], "published", budget)
        else:
            zf.writestr("workspace/NOTE.txt",
                        "Single-user installation: there is no per-account workspace.\n")
    chmod_private(dest)
    return dest


# ── CLI ──────────────────────────────────────────────────────────────────────

def _main(argv: List[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m motor_ai_sim.account_lifecycle")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("reapply", help="re-apply deletions after a backup restore")
    r.add_argument("--register", default=None,
                   help="a newer deleted_subjects.jsonl than the restored one")
    sub.add_parser("run-due", help="purge deletion requests whose grace ended")
    sub.add_parser("pending", help="list pending deletion requests (hashed)")
    a = ap.parse_args(argv)
    if a.cmd == "reapply":
        print(json.dumps(reapply_deletions(Path(a.register) if a.register else None)))
    elif a.cmd == "run-due":
        print(json.dumps({"purged": run_due()}))
    else:
        print(json.dumps([{"subject": subject_hash(r["email"]), "due_at": r.get("due_at")}
                          for r in list_pending()]))
    return 0


if __name__ == "__main__":                                  # pragma: no cover
    import sys
    raise SystemExit(_main(sys.argv[1:]))
