"""Move a die between the OPEN and the PRIVATE data set — journaled, resumable.

PUBLICATION = THE FIRST PUSH TO THE PUBLIC REPOSITORY.  A branch in a public
repository is public the moment it is pushed; the pull request that follows is
only for review and merge into ``main``, never a confidentiality gate.  So
EVERYTHING is decided before that push:

1. :func:`preview` validates the die fail-closed (``data_sources.move_blockers``:
   unreadable/wrong-shape files, file whitelist, size limits, secrets,
   e-mails, customer tags, MOTRES ownership, public materials/devices) and
   freezes the export as a file list with sha256 hashes and a SNAPSHOT id.
2. The admin confirms THAT snapshot (``confirm: true`` + ``snapshot``).  A die
   that changed since the preview is refused.
3. Only then does :func:`move_die` write the INTENT record to the move journal
   (``data_moves.json``: id, source, target, files + hashes, planned steps)
   and run the steps, recording each one:

   ``commit:private`` → ``commit:open`` → ``push:private`` → ``push:open`` →
   ``pr:private`` → ``pr:open``

   (local commits first, the private repository always before the public
   one).  Every step is idempotent: a commit already on the branch, a branch
   already on the remote at the recorded commit, a PR already opened — each is
   recognised and skipped on a retry.
4. A failure leaves the move ``incomplete`` with the failed step and error;
   the admin gets **Resume** (re-run from the first unfinished step) and
   **Rollback** (delete the pushed branches, close the PRs — allowed until a
   PR is merged).
5. After a human merges, :func:`reconcile` runs ``merged`` → ``ff:private`` →
   ``ff:open`` → ``verify``: the server checkouts are fast-forwarded and the
   files on disk are checked against the journal hashes.  A checkout that
   cannot be updated leaves the move ``incomplete`` — never ``done``.

All journal writes are serialized across threads AND processes (lock file).

Environment (``X`` is ``OPEN`` or ``PRIVATE``): ``X_DATA_REPO_DIR``,
``X_DATA_REPO_SLUG``, ``X_DATA_BASE_BRANCH``, ``X_DATA_DEPLOY_KEY``,
``X_DATA_GITHUB_TOKEN``; ``DATA_GIT_AUTHOR_NAME`` / ``DATA_GIT_AUTHOR_EMAIL``.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from motor_ai_sim import data_sources as DS

log = logging.getLogger(__name__)

STATUS_PREPARED = "prepared"      # intent written, no step run yet
STATUS_RUNNING = "running"
STATUS_INCOMPLETE = "incomplete"  # a step failed — Resume or Rollback
STATUS_PENDING = "pending"        # branches pushed, PRs open, waiting for merge
STATUS_DONE = "done"
STATUS_ROLLED_BACK = "rolled_back"
ACTIVE = (STATUS_PREPARED, STATUS_RUNNING, STATUS_INCOMPLETE, STATUS_PENDING)

#: The private repository always goes first, the public one last.
REPO_ORDER = (DS.SOURCE_PRIVATE, DS.SOURCE_OPEN)
PLAN = tuple([f"commit:{r}" for r in REPO_ORDER] + [f"push:{r}" for r in REPO_ORDER]
             + [f"pr:{r}" for r in REPO_ORDER])
FINISH = ("merged",) + tuple(f"ff:{r}" for r in REPO_ORDER) + ("verify",)

PUBLISH_NOTICE = (
    "Confirming publishes IMMEDIATELY: the die is pushed as a branch to the "
    "PUBLIC repository, and a branch in a public repository is public the "
    "moment it is pushed. The pull request is only for review and merge into "
    "main — it is not a confidentiality gate.")
PUBLISH_WARNING = (
    PUBLISH_NOTICE + " Publication cannot be undone: the files can be cloned "
    "and forked at once, under AGPL. Moving the die back to private later "
    "does NOT unpublish it.")
WITHDRAW_WARNING = (
    "Moving to private removes the die from the public repository going "
    "forward only. Everything already published stays in the public git "
    "history, forks and clones — it cannot be unpublished.")


class MoveError(RuntimeError):
    """A move that could not be carried out; the message is user-facing."""


# ── configuration ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RepoSpec:
    source: str
    checkout: Optional[Path]
    rel: Tuple[str, ...]
    slug: str
    base: str
    deploy_key: str
    token: str

    @property
    def label(self) -> str:
        return self.slug or self.source


def _env(source: str, key: str, default: str = "") -> str:
    return os.environ.get(f"{source.upper()}_DATA_{key}", default).strip()


def repo_spec(source: str) -> RepoSpec:
    raw = _env(source, "REPO_DIR")
    checkout: Optional[Path] = Path(raw).expanduser() if raw else DS.repo_dir(source)
    default_slug = ("eMotres/Motor_Ai_Simulation" if source == DS.SOURCE_OPEN
                    else "eMotres/motor-ai-sim-private")
    return RepoSpec(source=source, checkout=checkout, rel=DS.rel_of(source),
                    slug=_env(source, "REPO_SLUG", default_slug),
                    base=_env(source, "BASE_BRANCH", "main"),
                    deploy_key=_env(source, "DEPLOY_KEY"),
                    token=_env(source, "GITHUB_TOKEN"))


def _identity() -> Tuple[str, str]:
    return (os.environ.get("DATA_GIT_AUTHOR_NAME", "").strip() or "Motres data publisher",
            os.environ.get("DATA_GIT_AUTHOR_EMAIL", "").strip() or "data-publisher@users.noreply.github.com")


# ── git ─────────────────────────────────────────────────────────────────────

def _git(spec: RepoSpec, cwd: Path, *args: str, check: bool = True) -> str:
    env = dict(os.environ)
    if spec.deploy_key:
        env["GIT_SSH_COMMAND"] = (f'ssh -i "{spec.deploy_key}" -o IdentitiesOnly=yes '
                                  "-o StrictHostKeyChecking=yes")
    name, email = _identity()
    cmd = ["git", "-c", f"user.name={name}", "-c", f"user.email={email}",
           "-c", "commit.gpgsign=false", *args]
    r = subprocess.run(cmd, cwd=str(cwd), env=env, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=300)
    if check and r.returncode != 0:
        raise MoveError(f"git {' '.join(args[:2])} failed in {spec.label}: "
                        f"{(r.stderr or r.stdout).strip()[:400]}")
    return r.stdout


def _checkout(spec: RepoSpec) -> Path:
    if spec.checkout is None or not (spec.checkout / ".git").exists():
        raise MoveError(f"the {spec.source} data checkout is not configured "
                        f"({spec.source.upper()}_DATA_REPO_DIR)")
    return spec.checkout


def _rev(spec: RepoSpec, ref: str) -> str:
    return _git(spec, _checkout(spec), "rev-parse", "--verify", "-q", ref, check=False).strip()


def _remote_branch_sha(spec: RepoSpec, branch: str) -> str:
    out = _git(spec, _checkout(spec), "ls-remote", "origin", f"refs/heads/{branch}")
    return out.split()[0] if out.strip() else ""


def _slug(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower()
    return s[:40] or "die"


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── GitHub ──────────────────────────────────────────────────────────────────

def _gh(spec: RepoSpec, method: str, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    import urllib.request
    req = urllib.request.Request(
        f"https://api.github.com/repos/{spec.slug}{path}",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {spec.token}",
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28",
                 "User-Agent": "motor-ai-sim-data-publisher"},
        method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def github_open_pr(spec: RepoSpec, branch: str, title: str, body: str) -> Dict[str, Any]:
    """Open a PR on GitHub.  No token → no call; the compare URL is returned
    so the admin can open it by hand."""
    compare = f"https://github.com/{spec.slug}/compare/{spec.base}...{branch}?expand=1"
    if not spec.token:
        return {"repo": spec.slug, "branch": branch, "url": compare, "number": None,
                "opened": False}
    try:
        j = _gh(spec, "POST", "/pulls", {"title": title, "head": branch, "base": spec.base,
                                         "body": body, "maintainer_can_modify": True})
        return {"repo": spec.slug, "branch": branch, "url": j.get("html_url") or compare,
                "number": j.get("number"), "opened": True}
    except Exception as exc:                                # noqa: BLE001
        log.warning("data_publish: PR on %s failed: %s", spec.slug, exc)
        return {"repo": spec.slug, "branch": branch, "url": compare, "number": None,
                "opened": False, "error": str(exc)[:300]}


def github_close_pr(spec: RepoSpec, number: Optional[int]) -> Dict[str, Any]:
    if not number or not spec.token:
        return {"closed": False, "reason": "no PR number or no token — close it by hand"}
    try:
        _gh(spec, "PATCH", f"/pulls/{number}", {"state": "closed"})
        return {"closed": True}
    except Exception as exc:                                # noqa: BLE001
        return {"closed": False, "reason": str(exc)[:300]}


#: Swappable for tests (a fake remote has no GitHub).
PR_OPENER: Callable[..., Dict[str, Any]] = github_open_pr
PR_CLOSER: Callable[..., Dict[str, Any]] = github_close_pr
#: Called before every step with ``(step, record)`` — tests raise here to
#: simulate an interruption at that exact step.
BEFORE_STEP: Callable[[str, Dict[str, Any]], None] = lambda step, rec: None


# ── the journal + audit ─────────────────────────────────────────────────────

_TLOCK = threading.RLock()
LOCK_STALE_S = 30 * 60


def _state_dir() -> Path:
    from motor_ai_sim import config as _config
    return Path(str(_config.DEFAULT_CONFIG_PATH)).parent


def moves_file() -> Path:
    return _state_dir() / "data_moves.json"


def audit_file() -> Path:
    return _state_dir() / "data_moves_audit.jsonl"


def _lock_file() -> Path:
    return _state_dir() / "data_moves.lock"


@contextmanager
def journal_lock() -> Iterator[None]:
    """Serialize moves across threads AND processes (O_EXCL lock file; a lock
    older than :data:`LOCK_STALE_S` is a crashed holder and is broken)."""
    with _TLOCK:
        p = _lock_file()
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = None
        for _ in range(2):
            try:
                fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, f"{os.getpid()} {_now()}".encode())
                break
            except FileExistsError:
                try:
                    age = time.time() - p.stat().st_mtime
                except OSError:
                    age = 0
                if age > LOCK_STALE_S:
                    log.warning("data_publish: breaking stale lock %s (%.0f s old)", p, age)
                    p.unlink(missing_ok=True)
                    continue
                raise MoveError("another data move is running — try again when it finishes")
        try:
            yield
        finally:
            if fd is not None:
                os.close(fd)
            p.unlink(missing_ok=True)


def load_moves() -> Dict[str, Dict[str, Any]]:
    """``{move_id: record}``.  An unreadable journal raises MoveError — no
    move may start or resume on a journal we cannot trust."""
    try:
        d = json.loads(moves_file().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as exc:                                # noqa: BLE001
        raise MoveError(f"the move journal {moves_file()} is unreadable ({exc}) — "
                        "fix it by hand before any move") from exc
    moves = d.get("moves") if isinstance(d, dict) else None
    if not isinstance(moves, dict):
        raise MoveError(f"the move journal {moves_file()} has the wrong shape")
    return moves


def _save_moves(moves: Dict[str, Dict[str, Any]]) -> None:
    p = moves_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"version": 2, "moves": moves}, indent=1,
                              ensure_ascii=False, sort_keys=True), encoding="utf-8")
    tmp.replace(p)


def _put(rec: Dict[str, Any]) -> None:
    """Write one record back (caller holds the journal lock)."""
    rec["updated_at"] = _now()
    moves = load_moves()
    moves[rec["id"]] = rec
    _save_moves(moves)


def audit(event: str, **fields: Any) -> Dict[str, Any]:
    """One append-only JSON line per move event, plus the admin event log."""
    row = {"at": _now(), "event": event, **fields}
    p = audit_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")
    try:
        from motor_ai_sim import sessions as S
        S.record_event("die_source_move", email=str(fields.get("by") or ""),
                       reason=f"{fields.get('die')}: {event} "
                              f"{fields.get('from', '')}->{fields.get('to', '')}",
                       path="/api/admin/dies/source")
    except Exception:                                       # noqa: BLE001
        log.debug("data_publish: sessions.record_event unavailable", exc_info=True)
    return row


def read_audit(die: Optional[str] = None) -> List[Dict[str, Any]]:
    try:
        lines = audit_file().read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    rows = [json.loads(x) for x in lines if x.strip()]
    return [r for r in rows if die is None or r.get("die") == die]


def active_move(die: str) -> Optional[Dict[str, Any]]:
    """The newest not-finished move of this die, or ``None``."""
    recs = [r for r in load_moves().values()
            if r.get("die") == str(die) and r.get("status") in ACTIVE]
    return max(recs, key=lambda r: r.get("at", "")) if recs else None


def pending(die: str) -> Optional[Dict[str, Any]]:
    return active_move(die)


def get_move(move_id: str) -> Dict[str, Any]:
    rec = load_moves().get(str(move_id))
    if rec is None:
        raise MoveError(f"no move '{move_id}' in the journal")
    return rec


# ── preview (checks + frozen snapshot) ──────────────────────────────────────

def preview(die: str, target: str) -> Dict[str, Any]:
    """What moving ``die`` to ``target`` would carry (the validated
    whitelist, with hashes), what blocks it, the snapshot id the admin
    confirms, and the warning the dialog must show."""
    if target not in DS.SOURCES:
        raise MoveError(f"target must be one of {list(DS.SOURCES)}")
    e = DS.scan().get(str(die))
    if e and e["clash"]:
        raise MoveError(f"die '{die}' is in more than one source — {e['error']}")
    src_dir = DS.locate(die)
    if src_dir is None:
        raise MoveError(f"die '{die}' is in neither data set "
                        "(only dies of the open/private checkouts can be moved; "
                        "customer dies live in the application storage only)")
    current = DS.source_of_dir(src_dir) or DS.SOURCE_PRIVATE
    if current == target:
        raise MoveError(f"die '{die}' is already {target}")
    blockers = DS.move_blockers(src_dir, target)
    man = DS.manifest(src_dir)
    snap = DS.snapshot_id(die, current, target, man["files"])
    return {"die": die, "from": current, "to": target,
            "manifest": man, "blockers": blockers, "snapshot": snap,
            "pending": active_move(die),
            "public_push": target == DS.SOURCE_OPEN,
            "notice": PUBLISH_NOTICE if target == DS.SOURCE_OPEN else "",
            "warning": PUBLISH_WARNING if target == DS.SOURCE_OPEN else WITHDRAW_WARNING}


# ── steps ───────────────────────────────────────────────────────────────────

def _die_path(spec: RepoSpec, root: Path, die: str) -> Path:
    return root.joinpath(*spec.rel, die)


def _messages(rec: Dict[str, Any], source: str) -> Tuple[str, str, str]:
    """(commit message, PR title, PR body) for the repository ``source``."""
    die, verb = rec["die"], ("publish" if rec["to"] == DS.SOURCE_OPEN else "withdraw")
    n = len(rec["files"])
    if source == rec["to"]:
        msg = (f"data: {verb} die '{die}' ({rec['from']} -> {rec['to']})\n\n"
               f"{n} file(s), snapshot {rec['snapshot'][:16]}, move {rec['id']}.\n"
               "Opened by the admin Motors-access panel.")
        body = (f"Moves die **{die}** from the {rec['from']} to the {rec['to']} data set "
                f"(move `{rec['id']}`, snapshot `{rec['snapshot'][:16]}`, {n} file(s)).\n\n"
                + (PUBLISH_WARNING if rec["to"] == DS.SOURCE_OPEN else WITHDRAW_WARNING))
        return msg, f"data: {verb} die '{die}'", body
    msg = (f"data: remove die '{die}' (moved to the {rec['to']} data set)\n\n"
           f"Move {rec['id']}; merge together with the matching PR.")
    return msg, f"data: remove die '{die}' ({verb})", f"Companion PR of move `{rec['id']}`."


def _step_commit(rec: Dict[str, Any], source: str, st: Dict[str, Any]) -> Dict[str, Any]:
    spec = repo_spec(source)
    repo = _checkout(spec)
    branch = rec["branch"]
    have = _rev(spec, f"refs/heads/{branch}")
    if st.get("sha") and have == st["sha"]:
        return st                                            # idempotent
    _git(spec, repo, "worktree", "prune", check=False)
    if have:                                                 # stale half-attempt
        _git(spec, repo, "branch", "-D", branch)
    _git(spec, repo, "fetch", "origin", spec.base)
    base_sha = _rev(spec, f"origin/{spec.base}")
    tmp = Path(tempfile.mkdtemp(prefix="data-move-"))
    wt = tmp / "wt"
    _git(spec, repo, "worktree", "add", "-b", branch, str(wt), f"origin/{spec.base}")
    try:
        dest = _die_path(spec, wt, rec["die"])
        if source == rec["to"]:
            if dest.exists():
                raise MoveError(f"'{rec['die']}' already exists in the {source} repository")
            src_root = DS.dies_dir(rec["from"])
            if src_root is None:
                raise MoveError(f"the {rec['from']} data set is not configured")
            src_dir = src_root / rec["die"]
            for f in rec["files"]:                  # EXACTLY the confirmed snapshot
                sp = src_dir / f["path"]
                if not sp.is_file() or sp.is_symlink() or DS.sha256_file(sp) != f["sha256"]:
                    raise MoveError(f"{f['path']} changed since it was confirmed — "
                                    "roll back and preview again")
                dp = dest / f["path"]
                dp.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(sp, dp)
        else:
            if not dest.is_dir():
                raise MoveError(f"'{rec['die']}' is not on {spec.base} of the {source} repository")
            shutil.rmtree(dest)
        _git(spec, wt, "add", "-A", "--", "/".join(spec.rel))
        if not _git(spec, wt, "status", "--porcelain").strip():
            raise MoveError(f"nothing to commit in {spec.label}")
        _git(spec, wt, "commit", "-s", "-m", _messages(rec, source)[0])
        sha = _git(spec, wt, "rev-parse", "HEAD").strip()
    finally:
        _git(spec, repo, "worktree", "remove", "--force", str(wt), check=False)
        shutil.rmtree(tmp, ignore_errors=True)
    return {**st, "sha": sha, "base_sha": base_sha}


def _step_push(rec: Dict[str, Any], source: str, st: Dict[str, Any]) -> Dict[str, Any]:
    spec = repo_spec(source)
    sha = rec["steps"][f"commit:{source}"].get("sha")
    if not sha:
        raise MoveError(f"no commit recorded for {source}")
    remote = _remote_branch_sha(spec, rec["branch"])
    if remote == sha:
        return {**st, "sha": sha}                            # idempotent
    if remote:
        raise MoveError(f"branch {rec['branch']} on {spec.label} points at {remote[:10]}, "
                        f"not the recorded {sha[:10]} — refusing to overwrite")
    _git(spec, _checkout(spec), "push", "origin", f"{sha}:refs/heads/{rec['branch']}")
    if _remote_branch_sha(spec, rec["branch"]) != sha:
        raise MoveError(f"push to {spec.label} did not land")
    return {**st, "sha": sha, "pushed_at": _now()}


def _step_pr(rec: Dict[str, Any], source: str, st: Dict[str, Any]) -> Dict[str, Any]:
    if st.get("pr"):
        return st                                            # idempotent
    spec = repo_spec(source)
    _msg, title, body = _messages(rec, source)
    pr = PR_OPENER(spec, rec["branch"], title, body)
    if pr.get("error"):
        raise MoveError(f"opening the PR on {spec.label} failed: {pr['error']}")
    return {**st, "pr": pr}


def _merged(rec: Dict[str, Any]) -> bool:
    """Both PRs merged: the die is on the target base and gone from the source base."""
    dst, src = repo_spec(rec["to"]), repo_spec(rec["from"])
    for spec in (dst, src):
        _git(spec, _checkout(spec), "fetch", "origin", spec.base)
    def on_base(spec: RepoSpec) -> bool:
        path = "/".join((*spec.rel, rec["die"], "die.yaml"))
        return subprocess.run(["git", "cat-file", "-e", f"origin/{spec.base}:{path}"],
                              cwd=str(_checkout(spec)), capture_output=True).returncode == 0
    return on_base(dst) and not on_base(src)


def _step_ff(rec: Dict[str, Any], source: str, st: Dict[str, Any]) -> Dict[str, Any]:
    """Fast-forward the server checkout the loader reads — only on the base
    branch, never a reset or stash.  Anything else is a FAILURE."""
    spec = repo_spec(source)
    repo = _checkout(spec)
    head = _git(spec, repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
    if head != spec.base:
        raise MoveError(f"the {source} checkout is on '{head}', not '{spec.base}' — "
                        "switch it to the base branch, then Resume")
    _git(spec, repo, "merge", "--ff-only", f"origin/{spec.base}")
    if _rev(spec, "HEAD") != _rev(spec, f"origin/{spec.base}"):
        raise MoveError(f"the {source} checkout did not reach origin/{spec.base}")
    return {**st, "sha": _rev(spec, "HEAD")}


def _step_verify(rec: Dict[str, Any], st: Dict[str, Any]) -> Dict[str, Any]:
    dst_dir = DS.dies_dir(rec["to"])
    src_dir = DS.dies_dir(rec["from"])
    if dst_dir is None or src_dir is None:
        raise MoveError("a data checkout is not configured")
    bad = []
    for f in rec["files"]:
        p = dst_dir / rec["die"] / f["path"]
        if not p.is_file() or not DS.sha256_matches(p, f["sha256"]):
            bad.append(f["path"])
    if bad:
        raise MoveError(f"the {rec['to']} checkout does not hold the confirmed files: "
                        + ", ".join(bad[:10]))
    if (src_dir / rec["die"]).exists():
        raise MoveError(f"'{rec['die']}' is still in the {rec['from']} checkout")
    return {**st, "verified_files": len(rec["files"])}


def _run_step(rec: Dict[str, Any], step: str) -> None:
    kind, _, source = step.partition(":")
    st = dict(rec["steps"].get(step) or {})
    st.update(status="running", started_at=_now(), error=None)
    rec["steps"][step] = st
    _put(rec)
    BEFORE_STEP(step, rec)
    if kind == "commit":
        st = _step_commit(rec, source, st)
    elif kind == "push":
        st = _step_push(rec, source, st)
    elif kind == "pr":
        st = _step_pr(rec, source, st)
    elif kind == "ff":
        st = _step_ff(rec, source, st)
    elif kind == "verify":
        st = _step_verify(rec, st)
    elif kind == "merged":
        pass
    st.update(status="done", done_at=_now())
    rec["steps"][step] = st
    _put(rec)


def _fail(rec: Dict[str, Any], step: str, exc: Exception, by: str) -> None:
    st = dict(rec["steps"].get(step) or {})
    st.update(status="failed", error=str(exc)[:500])
    rec["steps"][step] = st
    rec.update(status=STATUS_INCOMPLETE, failed_step=step, error=str(exc)[:500])
    _put(rec)
    audit("move_failed", die=rec["die"], **{"from": rec["from"]}, to=rec["to"], by=by,
          move=rec["id"], step=step, error=str(exc)[:300])


def _done(rec: Dict[str, Any], step: str) -> bool:
    return (rec["steps"].get(step) or {}).get("status") == "done"


def _run_plan(rec: Dict[str, Any], by: str) -> Dict[str, Any]:
    rec.update(status=STATUS_RUNNING, error=None, failed_step=None)
    _put(rec)
    for step in rec["plan"]:
        if _done(rec, step):
            continue
        try:
            _run_step(rec, step)
        except Exception as exc:                            # noqa: BLE001
            _fail(rec, step, exc, by)
            raise MoveError(f"move {rec['id']} stopped at {step}: {exc}") from exc
    rec["prs"] = [rec["steps"][f"pr:{r}"]["pr"] for r in REPO_ORDER]
    rec.update(status=STATUS_PENDING)
    _put(rec)
    audit("move_requested", die=rec["die"], **{"from": rec["from"]}, to=rec["to"], by=by,
          move=rec["id"], branch=rec["branch"], prs=[p.get("url") for p in rec["prs"]],
          files=len(rec["files"]))
    return rec


def _run_finish(rec: Dict[str, Any], by: str) -> Dict[str, Any]:
    """merged → ff:private → ff:open → verify.  Not merged yet = stays pending."""
    if not _done(rec, "merged"):
        try:
            merged = _merged(rec)
        except Exception as exc:                            # noqa: BLE001
            _fail(rec, "merged", exc, by)
            raise MoveError(f"move {rec['id']}: {exc}") from exc
        if not merged:
            rec.update(status=STATUS_PENDING)
            _put(rec)
            return rec
    rec.update(status=STATUS_RUNNING, error=None, failed_step=None)
    for step in rec["finish"]:
        if _done(rec, step):
            continue
        try:
            _run_step(rec, step)
        except Exception as exc:                            # noqa: BLE001
            _fail(rec, step, exc, by)
            raise MoveError(f"move {rec['id']} stopped at {step}: {exc}") from exc
    rec.update(status=STATUS_DONE, done_at=_now())
    _put(rec)
    audit("move_completed", die=rec["die"], **{"from": rec["from"]}, to=rec["to"],
          by=by or "reconcile", move=rec["id"], branch=rec.get("branch"))
    return rec


# ── public API ──────────────────────────────────────────────────────────────

def move_die(die: str, target: str, *, by: str, snapshot: str) -> Dict[str, Any]:
    """Validate, write the intent record, run the steps.  ``snapshot`` must be
    the id the admin confirmed in :func:`preview`."""
    with journal_lock():
        pv = preview(die, target)
        if pv["pending"]:
            raise MoveError(f"die '{die}' already has an unfinished move "
                            f"({pv['pending']['id']}, {pv['pending']['status']}) — "
                            "resume or roll it back first")
        if pv["blockers"]:
            names = ", ".join(f"{b['kind']} {b['name']}" for b in pv["blockers"])
            raise MoveError(f"die '{die}' cannot be moved: {names}")
        if not snapshot or snapshot != pv["snapshot"]:
            raise MoveError("the die changed since you previewed it (snapshot mismatch) — "
                            "preview again and confirm the new content")
        verb = "publish" if target == DS.SOURCE_OPEN else "withdraw"
        mid = f"{_dt.datetime.now(_dt.timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
        rec = {"id": mid, "die": die, "from": pv["from"], "to": target,
               "status": STATUS_PREPARED, "branch": f"data/{verb}-{_slug(die)}-{mid}",
               "snapshot": pv["snapshot"], "files": pv["manifest"]["files"],
               "plan": list(PLAN), "finish": list(FINISH), "steps": {}, "prs": [],
               "at": _now(), "by": by, "error": None, "failed_step": None}
        _put(rec)                                            # INTENT before any action
        audit("move_intent", die=die, **{"from": pv["from"]}, to=target, by=by,
              move=mid, snapshot=pv["snapshot"], files=len(rec["files"]))
        return _run_plan(rec, by)


def resume(move_id: str, *, by: str) -> Dict[str, Any]:
    """Re-run an unfinished move from its first step that is not done."""
    with journal_lock():
        rec = get_move(move_id)
        if rec.get("status") not in (STATUS_PREPARED, STATUS_RUNNING, STATUS_INCOMPLETE):
            raise MoveError(f"move {move_id} is {rec.get('status')} — nothing to resume")
        audit("move_resumed", die=rec["die"], **{"from": rec["from"]}, to=rec["to"],
              by=by, move=move_id, step=rec.get("failed_step"))
        if all(_done(rec, s) for s in rec["plan"]):
            return _run_finish(rec, by)
        return _run_plan(rec, by)


def rollback(move_id: str, *, by: str) -> Dict[str, Any]:
    """Undo an unmerged move: delete the pushed branches, close the PRs, drop
    the local branches.  Refused once a PR is merged (then: Resume)."""
    with journal_lock():
        rec = get_move(move_id)
        if rec.get("status") in (STATUS_DONE, STATUS_ROLLED_BACK):
            raise MoveError(f"move {move_id} is {rec.get('status')}")
        if _done(rec, "merged"):
            raise MoveError(f"move {move_id} is already merged — Resume it instead")
        try:
            if all(_done(rec, s) for s in rec["plan"]) and _merged(rec):
                raise MoveError(f"move {move_id}: the PRs are merged — Resume it instead")
        except MoveError:
            raise
        except Exception:                                    # noqa: BLE001
            pass
        notes = []
        for source in reversed(REPO_ORDER):
            spec = repo_spec(source)
            try:
                repo = _checkout(spec)
            except MoveError as exc:
                notes.append(str(exc))
                continue
            pr = (rec["steps"].get(f"pr:{source}") or {}).get("pr")
            if pr:
                res = PR_CLOSER(spec, pr.get("number"))
                notes.append(f"{spec.label} PR: " + ("closed" if res.get("closed")
                                                     else res.get("reason", "not closed")))
            try:
                if _remote_branch_sha(spec, rec["branch"]):
                    _git(spec, repo, "push", "origin", "--delete", rec["branch"])
                    notes.append(f"deleted {rec['branch']} on {spec.label}")
            except MoveError as exc:
                rec.update(status=STATUS_INCOMPLETE, error=f"rollback: {exc}")
                _put(rec)
                raise
            if _rev(spec, f"refs/heads/{rec['branch']}"):
                _git(spec, repo, "worktree", "prune", check=False)
                _git(spec, repo, "branch", "-D", rec["branch"], check=False)
        was_public = _done(rec, f"push:{DS.SOURCE_OPEN}") and rec["to"] == DS.SOURCE_OPEN
        if was_public:
            notes.append("the branch WAS public in the open repository — anyone may "
                         "have cloned it; rollback cannot unpublish that")
        rec.update(status=STATUS_ROLLED_BACK, rolled_back_at=_now(), rollback_notes=notes)
        _put(rec)
        audit("move_rolled_back", die=rec["die"], **{"from": rec["from"]}, to=rec["to"],
              by=by, move=move_id, notes=notes)
        return rec


def reconcile(by: str = "") -> List[Dict[str, Any]]:
    """Complete every pending move whose PRs were merged; return those that
    reached ``done`` (a failed finish leaves the move ``incomplete``)."""
    changed = []
    with journal_lock():
        for mid, rec in sorted(load_moves().items()):
            if rec.get("status") != STATUS_PENDING:
                continue
            try:
                rec = _run_finish(rec, by)
            except MoveError as exc:
                log.warning("data_publish: %s", exc)
                continue
            if rec["status"] == STATUS_DONE:
                changed.append(rec)
    return changed
