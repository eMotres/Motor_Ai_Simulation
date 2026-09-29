"""Three things that must be true before this process serves anyone.

WHY
===
All three are Linux-port failures that Windows hides (migration plan §3):

1. **Two dies differing only by case.**  ``routes/family._die_dir()`` does an
   exact-name lookup, and on NTFS ``CILN28`` and ``ciln28`` are *one* folder —
   so the ambiguity cannot even be expressed here.  On ext4 they are two, and
   the pair behaves like a single die that intermittently answers with the
   other one's geometry: the write goes to whichever name the request spelled
   and the read to whichever the catalog listed.  There is no correct
   behaviour to fall back to, so this one is **fatal**.  It is also almost
   certainly created *by the transfer* — a catalog copied through a tool that
   case-folds, or two dies that were always distinct in the owner's head and
   never on his disk — which is exactly when a server must refuse rather than
   guess.

2. **``AUTH_SECRET`` not set explicitly** while the multi-user layout is on.
   ``users.py`` generates one into the state directory when the variable is
   empty, and deliberately refuses to mint a replacement if it is ever lost:
   losing it signs every account out permanently, with no recovery by design.
   That bargain is fine for one workstation and wrong for a server with
   customers, so with ``WORKSPACES_ROOT`` set this **warns, loudly, every
   boot**, and the warning names the backup consequence.

3. **A missing report dependency.**  ``reportlab``, ``python-docx`` and
   ``triangle`` were installed on the workstation and pinned nowhere until
   Stage 6, so the first server image built an API whose *default* report
   format 500s on the first customer click.  They are in ``requirements.txt``
   now; this check is what makes the next such gap a log line at boot instead
   of a support ticket.

WHY WARNINGS AND NOT MORE FATALS
--------------------------------
A fatal must be a condition where *every* answer the server could give is
wrong.  That is true of the case collision and of nothing else here: an API
with no reportlab still solves, still serves the catalog, still renders .docx.
Refusing to boot over it would trade a broken button for a dead server.

CALLED FROM the API lifespan (``api.py``), once, before the first request.
Importing this module has no side effects; nothing here writes.
"""
from __future__ import annotations

import importlib.util
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

log = logging.getLogger(__name__)

__all__ = ["Finding", "group_by_case", "case_collisions", "die_roots", "check_die_case",
           "check_auth_secret", "check_report_deps", "run_startup_checks",
           "StartupCheckError"]


class StartupCheckError(RuntimeError):
    """A fatal finding.  Raised from the lifespan, so the process never serves."""


@dataclass(frozen=True)
class Finding:
    #: ``"fatal"`` stops the boot; ``"warn"`` is logged and the server runs.
    level: str
    #: Stable, greppable identifier — the thing a runbook and an alert quote.
    code: str
    message: str

    def __str__(self) -> str:                                # pragma: no cover
        return f"[{self.level}] {self.code}: {self.message}"


# ── 1. case collisions ──────────────────────────────────────────────────────

#: Only a folder holding ``die.yaml`` is a die.  Anything else in a catalog
#: directory (``.owner.json``, an editor's backup, a stray ``runs/``) is not,
#: and must not be able to fail a boot.
DIE_MARKER = "die.yaml"


def group_by_case(names: Iterable[str]) -> List[Tuple[str, List[str]]]:
    """``[(lowercased name, [the real names]), …]`` for the ambiguous ones only.

    Split out from :func:`case_collisions` because it is the half that can be
    TESTED on the machine this was written on: NTFS cannot hold ``CILN28`` and
    ``ciln28`` side by side, so the only honest way to pin the rule here is to
    hand it the two names directly.

    Case-folding with ``str.lower()`` and not ``casefold()`` on purpose: the
    question is what *a case-insensitive filesystem* would merge, and NTFS
    folds per the simple upper/lower table, not per the Unicode full-fold that
    turns ``ß`` into ``ss``.  A check that flagged more than NTFS merges would
    refuse to boot over a pair that was never ambiguous anywhere.
    """
    groups: Dict[str, List[str]] = {}
    for name in names:
        groups.setdefault(name.lower(), []).append(name)
    return [(k, sorted(v)) for k, v in sorted(groups.items()) if len(v) > 1]


def case_collisions(directory) -> List[Tuple[str, List[str]]]:
    """:func:`group_by_case` over the die folders of one catalog directory.

    Empty on a directory that does not exist, is not readable, or holds no
    collisions — a missing layer is not a finding, it is a layer that is off.
    """
    d = Path(str(directory))
    try:
        names = [p.name for p in d.iterdir()
                 if p.is_dir() and (p / DIE_MARKER).is_file()]
    except OSError:
        return []
    return group_by_case(names)


def die_roots() -> List[Path]:
    """Every directory whose immediate children are die folders, all layers.

    Resolved from the environment rather than from ``workspace.layers()``: this
    runs at boot, with no request and therefore no caller, and it must inspect
    *every* account's layer — not the one the process workspace happens to be.
    """
    out: List[Path] = []

    def add(p: Optional[Path]) -> None:
        if p is not None and p not in out:
            out.append(p)

    def env_path(name: str) -> Optional[Path]:
        raw = os.environ.get(name, "").strip()
        return Path(raw) if raw else None

    # the process workspace's own catalog (the single-user case, and the
    # anonymous layer on the server)
    try:
        from motor_ai_sim.workspace import root as _ws_root
        add(Path(str(_ws_root())) / "dies")
    except Exception:                                        # noqa: BLE001
        pass

    shared = env_path("SHARED_ROOT")
    if shared is not None:
        add(shared / "dies")

    workspaces = env_path("WORKSPACES_ROOT")
    if workspaces is not None:
        try:
            for ws in sorted(workspaces.iterdir()):
                if ws.is_dir():
                    add(ws / "dies")
        except OSError:
            pass

    # published/<ws_id>/ IS a dies directory — a whole little catalog per
    # owner namespace, which is why it is added without a "dies" suffix.
    published = env_path("PUBLISHED_ROOT")
    if published is None and workspaces is not None:
        published = workspaces.parent / "published"
    if published is not None:
        try:
            for owner in sorted(published.iterdir()):
                if owner.is_dir():
                    add(owner)
        except OSError:
            pass

    return out


def check_die_case(roots: Optional[Iterable] = None) -> List[Finding]:
    """FATAL for every catalog directory holding two dies that differ by case."""
    out: List[Finding] = []
    for d in (die_roots() if roots is None else [Path(str(r)) for r in roots]):
        for _folded, names in case_collisions(d):
            out.append(Finding(
                "fatal", "die_case_collision",
                f"{d}: {' and '.join(repr(n) for n in names)} differ only by "
                f"case. On this filesystem they are separate dies; on the "
                f"Windows box they were one folder. Rename one (and its "
                f"grants in users.json) before starting the server."))
    return out


# ── 2. the session secret ───────────────────────────────────────────────────

def check_auth_secret(env: Optional[Dict[str, str]] = None) -> List[Finding]:
    """WARN when the multi-user layout is on and ``AUTH_SECRET`` is implicit."""
    e = os.environ if env is None else env
    if not str(e.get("WORKSPACES_ROOT", "")).strip():
        return []                       # single-user: the generated file is fine
    if str(e.get("AUTH_SECRET", "")).strip():
        return []
    return [Finding(
        "warn", "auth_secret_implicit",
        "AUTH_SECRET is not set, so the session-signing key is generated into "
        "the state directory on first use. Losing that file signs EVERY "
        "account out permanently and users.py deliberately refuses to mint a "
        "replacement. Set AUTH_SECRET explicitly in /etc/motres/api.env "
        "(openssl rand -hex 32) and keep it in the backup set.")]


# ── 3. report dependencies ──────────────────────────────────────────────────

#: ``(import name, pip name, what breaks without it)``.
REPORT_DEPS = (
    ("reportlab", "reportlab",
     "GET /api/family/report/...?format=pdf returns 500"),
    ("docx", "python-docx",
     "the DEFAULT report format (.docx) returns 500"),
    ("triangle", "triangle",
     "the earcut triangulation fallback raises ImportError instead of "
     "falling back (only reached if mapbox_earcut fails to load)"),
)


def check_report_deps() -> List[Finding]:
    """WARN once per missing optional-in-practice, required-in-product import."""
    out: List[Finding] = []
    for mod, pip_name, consequence in REPORT_DEPS:
        try:
            found = importlib.util.find_spec(mod) is not None
        except (ImportError, ValueError):                    # namespace oddities
            found = False
        if not found:
            out.append(Finding(
                "warn", "missing_dependency",
                f"{pip_name} is not installed — {consequence}. "
                f"It is pinned in requirements.txt; this image or venv was "
                f"built from something else."))
    return out


# ── the entry point ─────────────────────────────────────────────────────────

def run_startup_checks(raise_on_fatal: bool = True) -> List[Finding]:
    """Run all three, log every finding, raise on the fatal one.

    Returns the findings so a test can assert on them without reading the log.
    Never raises anything but :class:`StartupCheckError`: a check that blew up
    on its own is a bug in this file and must not be able to stop a boot, so
    each is wrapped.
    """
    findings: List[Finding] = []
    for fn in (check_die_case, check_auth_secret, check_report_deps):
        try:
            findings.extend(fn())
        except Exception as exc:                             # noqa: BLE001
            log.warning("startup check %s failed to run: %s", fn.__name__, exc)

    for f in findings:
        (log.error if f.level == "fatal" else log.warning)(
            "startup check %s: %s", f.code, f.message)

    fatal = [f for f in findings if f.level == "fatal"]
    if fatal and raise_on_fatal:
        raise StartupCheckError(
            "refusing to start — " + "; ".join(f.message for f in fatal))
    if not findings:
        log.info("startup checks: ok (die names, AUTH_SECRET, report deps)")
    return findings
