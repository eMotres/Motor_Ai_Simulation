"""Move a die between the OPEN and the PRIVATE data set — by pull request only.

The server holds a git checkout of each data repository (the public one for
``data/open/dies``, the private one for ``config/dies``).  A move never touches
either repository's base branch.  It:

1. makes a throw-away ``git worktree`` of the TARGET repository at
   ``origin/<base>``, copies the die folder in, commits (``-s``) on a fresh
   branch and pushes that branch;
2. does the same in the SOURCE repository with the folder removed;
3. opens one pull request per repository through the GitHub API (when a token
   is configured — otherwise the result carries the compare URL to open by
   hand);
4. records the move as PENDING in ``data_moves.json`` and writes an audit line.

A human merges.  :func:`reconcile` then notices the die has arrived on the
target's base branch, fast-forwards the server checkouts and clears PENDING.

Secrets — deploy keys and API tokens — are read from the environment only
(``/etc/motres/api.env`` on the server).  Nothing here writes them anywhere.

Environment (``X`` is ``OPEN`` or ``PRIVATE``):

``X_DATA_REPO_DIR``      the checkout (OPEN defaults to this source tree, PRIVATE
                         to ``MOTOR_AI_SIM_PRIVATE_DATA``)
``X_DATA_REPO_SLUG``     ``owner/name`` on GitHub, for the PR API
``X_DATA_BASE_BRANCH``   the branch PRs target (default ``main``)
``X_DATA_DEPLOY_KEY``    path of the SSH deploy key with WRITE access (push)
``X_DATA_GITHUB_TOKEN``  token allowed to open PRs on that repository
``DATA_GIT_AUTHOR_NAME`` / ``DATA_GIT_AUTHOR_EMAIL``  commit identity
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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from motor_ai_sim import data_sources as DS

log = logging.getLogger(__name__)

STATUS_PENDING = "pending"
STATUS_DONE = "done"

PUBLISH_WARNING = (
    "Publishing on GitHub is irreversible in practice: once the pull request "
    "is merged the files are in the public history, in every fork and clone, "
    "under AGPL. Moving the die back to private later does NOT unpublish it.")
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
    if raw:
        checkout: Optional[Path] = Path(raw).expanduser()
    else:
        checkout = DS.repo_dir(source)
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


def _slug(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower()
    return s[:40] or "die"


def _stamp() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d-%H%M%S")


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _branch_commit(spec: RepoSpec, branch: str, message: str,
                   change: Callable[[Path], None]) -> None:
    """Commit ``change`` on a NEW branch cut from ``origin/<base>`` and push it.

    Done in a temporary worktree, so the server checkout the loader reads is
    never switched, dirtied or stashed.
    """
    if spec.checkout is None or not (spec.checkout / ".git").exists():
        raise MoveError(f"the {spec.source} data checkout is not configured "
                        f"({spec.source.upper()}_DATA_REPO_DIR)")
    repo = spec.checkout
    _git(spec, repo, "fetch", "origin", spec.base)
    tmp = Path(tempfile.mkdtemp(prefix="data-move-"))
    wt = tmp / "wt"
    _git(spec, repo, "worktree", "add", "-b", branch, str(wt), f"origin/{spec.base}")
    try:
        change(wt)
        _git(spec, wt, "add", "-A", "--", "/".join(spec.rel))
        status = _git(spec, wt, "status", "--porcelain")
        if not status.strip():
            raise MoveError(f"nothing to commit in {spec.label} — is the move already done?")
        _git(spec, wt, "commit", "-s", "-m", message)
        _git(spec, wt, "push", "origin", f"{branch}:{branch}")
    finally:
        _git(spec, repo, "worktree", "remove", "--force", str(wt), check=False)
        shutil.rmtree(tmp, ignore_errors=True)


# ── GitHub ──────────────────────────────────────────────────────────────────

def github_open_pr(spec: RepoSpec, branch: str, title: str, body: str) -> Dict[str, Any]:
    """Open a PR on GitHub.  No token → no call; the compare URL is returned
    so the admin can open it by hand."""
    compare = f"https://github.com/{spec.slug}/compare/{spec.base}...{branch}?expand=1"
    if not spec.token:
        return {"repo": spec.slug, "branch": branch, "url": compare, "number": None,
                "opened": False}
    import urllib.request
    req = urllib.request.Request(
        f"https://api.github.com/repos/{spec.slug}/pulls",
        data=json.dumps({"title": title, "head": branch, "base": spec.base,
                         "body": body, "maintainer_can_modify": True}).encode(),
        headers={"Authorization": f"Bearer {spec.token}",
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28",
                 "User-Agent": "motor-ai-sim-data-publisher"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            j = json.loads(r.read().decode())
        return {"repo": spec.slug, "branch": branch, "url": j.get("html_url") or compare,
                "number": j.get("number"), "opened": True}
    except Exception as exc:                                # noqa: BLE001
        log.warning("data_publish: PR on %s failed: %s", spec.slug, exc)
        return {"repo": spec.slug, "branch": branch, "url": compare, "number": None,
                "opened": False, "error": str(exc)[:300]}


#: Swappable for tests (a fake remote has no GitHub).
PR_OPENER: Callable[..., Dict[str, Any]] = github_open_pr


# ── the pending store + audit ───────────────────────────────────────────────

_LOCK = threading.Lock()


def _state_dir() -> Path:
    from motor_ai_sim import config as _config
    return Path(str(_config.DEFAULT_CONFIG_PATH)).parent


def moves_file() -> Path:
    return _state_dir() / "data_moves.json"


def audit_file() -> Path:
    return _state_dir() / "data_moves_audit.jsonl"


def load_moves() -> Dict[str, Any]:
    try:
        d = json.loads(moves_file().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:                                       # noqa: BLE001
        log.warning("data_publish: unreadable %s", moves_file(), exc_info=True)
        return {}


def _save_moves(d: Dict[str, Any]) -> None:
    p = moves_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1, ensure_ascii=False, sort_keys=True),
                   encoding="utf-8")
    tmp.replace(p)


def audit(event: str, **fields: Any) -> Dict[str, Any]:
    """One append-only JSON line per move event, plus the admin event log."""
    row = {"at": _now(), "event": event, **fields}
    p = audit_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
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


def pending(die: str) -> Optional[Dict[str, Any]]:
    m = load_moves().get(str(die))
    return m if m and m.get("status") == STATUS_PENDING else None


# ── the move ────────────────────────────────────────────────────────────────

def _die_path(spec: RepoSpec, root: Path, die: str) -> Path:
    return root.joinpath(*spec.rel, die)


def preview(die: str, target: str) -> Dict[str, Any]:
    """What moving ``die`` to ``target`` would carry, what blocks it, and the
    warning the dialog must show.  Raises MoveError when it cannot be moved."""
    if target not in DS.SOURCES:
        raise MoveError(f"target must be one of {list(DS.SOURCES)}")
    src_dir = DS.locate(die)
    if src_dir is None:
        raise MoveError(f"die '{die}' is in neither data set "
                        "(only dies of the open/private checkouts can be moved)")
    current = DS.source_of_dir(src_dir) or DS.SOURCE_PRIVATE
    if current == target:
        raise MoveError(f"die '{die}' is already {target}")
    blockers = DS.publish_blockers(src_dir) if target == DS.SOURCE_OPEN else []
    return {"die": die, "from": current, "to": target,
            "manifest": DS.manifest(src_dir), "blockers": blockers,
            "pending": pending(die),
            "warning": PUBLISH_WARNING if target == DS.SOURCE_OPEN else WITHDRAW_WARNING}


def move_die(die: str, target: str, *, by: str) -> Dict[str, Any]:
    """Carry out the move by pull request.  Validation first, git second."""
    pv = preview(die, target)
    if pv["pending"]:
        raise MoveError(f"die '{die}' already has a pending move "
                        f"({pv['pending'].get('from')} → {pv['pending'].get('to')})")
    if pv["blockers"]:
        names = ", ".join(f"{b['kind']} {b['name']}" for b in pv["blockers"])
        raise MoveError(f"die '{die}' cannot be published yet: {names}")
    src = repo_spec(pv["from"])
    dst = repo_spec(target)
    src_dir = DS.locate(die)
    assert src_dir is not None
    branch = f"data/{'publish' if target == DS.SOURCE_OPEN else 'withdraw'}-{_slug(die)}-{_stamp()}"
    verb = "publish" if target == DS.SOURCE_OPEN else "withdraw"
    files = [f["path"] for f in pv["manifest"]["files"]]

    def add(wt: Path) -> None:
        dest = _die_path(dst, wt, die)
        if dest.exists():
            raise MoveError(f"'{die}' already exists in the {target} repository")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src_dir, dest)

    def remove(wt: Path) -> None:
        shutil.rmtree(_die_path(src, wt, die), ignore_errors=True)

    msg_add = (f"data: {verb} die '{die}' ({pv['from']} -> {target})\n\n"
               f"{len(files)} file(s) moved from the {pv['from']} data set.\n"
               "Opened by the admin Motors-access panel; merge to complete.")
    msg_rm = (f"data: remove die '{die}' (moved to the {target} data set)\n\n"
              "Opened by the admin Motors-access panel; merge together with "
              f"the matching PR in {dst.label}.")
    _branch_commit(dst, branch, msg_add, add)
    _branch_commit(src, branch, msg_rm, remove)

    body_add = (f"Moves die **{die}** from the {pv['from']} to the {target} data set.\n\n"
                f"Files: {len(files)}\n\n"
                + (PUBLISH_WARNING if target == DS.SOURCE_OPEN else WITHDRAW_WARNING))
    prs = [PR_OPENER(dst, branch, f"data: {verb} die '{die}'", body_add),
           PR_OPENER(src, branch, f"data: remove die '{die}' ({verb})",
                     f"Companion of the {dst.label} PR moving **{die}** to {target}.")]
    rec = {"die": die, "from": pv["from"], "to": target, "status": STATUS_PENDING,
           "branch": branch, "prs": prs, "at": _now(), "by": by, "files": len(files)}
    with _LOCK:
        store = load_moves()
        store[die] = rec
        _save_moves(store)
    audit("move_requested", die=die, **{"from": pv["from"]}, to=target, by=by,
          branch=branch, prs=[p.get("url") for p in prs], files=len(files))
    return rec


def _on_base(spec: RepoSpec, die: str) -> Optional[bool]:
    """Is the die present on ``origin/<base>`` of this repository?"""
    if spec.checkout is None or not (spec.checkout / ".git").exists():
        return None
    _git(spec, spec.checkout, "fetch", "origin", spec.base, check=False)
    path = "/".join((*spec.rel, die, "die.yaml"))
    out = subprocess.run(["git", "cat-file", "-e", f"origin/{spec.base}:{path}"],
                         cwd=str(spec.checkout), capture_output=True)
    return out.returncode == 0


def _fast_forward(spec: RepoSpec) -> None:
    """Bring the checkout the loader reads up to the merged base — only when it
    is ON the base branch; never a reset, never a stash."""
    if spec.checkout is None:
        return
    head = _git(spec, spec.checkout, "rev-parse", "--abbrev-ref", "HEAD", check=False).strip()
    if head == spec.base:
        _git(spec, spec.checkout, "merge", "--ff-only", f"origin/{spec.base}", check=False)


def reconcile(by: str = "") -> List[Dict[str, Any]]:
    """Clear every pending move whose PRs were merged; return what changed."""
    changed = []
    with _LOCK:
        store = load_moves()
        for die, rec in list(store.items()):
            if rec.get("status") != STATUS_PENDING:
                continue
            dst, src = repo_spec(rec["to"]), repo_spec(rec["from"])
            arrived = _on_base(dst, die)
            gone = _on_base(src, die)
            if arrived and gone is False:
                _fast_forward(dst)
                _fast_forward(src)
                rec = {**rec, "status": STATUS_DONE, "done_at": _now()}
                store[die] = rec
                changed.append(rec)
        if changed:
            _save_moves(store)
    for rec in changed:
        audit("move_completed", die=rec["die"], **{"from": rec["from"]},
              to=rec["to"], by=by or "reconcile", branch=rec.get("branch"))
    return changed
