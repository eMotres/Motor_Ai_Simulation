"""Product REVISIONS of a motor: Rev A, Rev B, ... (owner 2026-10-27).

«Моторы должны быть версии - как правило нужно несколько итераций для получения готового продукта.»  A motor gets ONE product
revision letter that is shown in the project, in the Motors catalogue and in Production: "CIANO56 400_80 · L100 · Rev A".

* Rev A is assigned when the design is agreed with the client (``assign``).
* A new revision is made from the current one (``new_revision``): a duplicate of its configuration - or of its whole die when the
  lamination changes - plus a record "what changed and why".  The revision it replaces becomes READ-ONLY (its configuration is
  locked with the existing lock, the catalogue write seam refuses edits of it) and is NEVER deleted.
* The product revision is the level ABOVE the production revisions (``production.models.normalize_revision``: 1.4, 1.5 ... the
  revisions of a production document / production change).  Those stay as they are.

Store (``<shared>/motor_revisions/``, override ``MOTRES_MOTOR_REVISIONS_ROOT``; next to ``projects/`` and ``production/``)::

    revisions.json                        {"lineages": {<lineage id>: Lineage}}
    snapshots/<lineage>/<rev>/die.yaml    a copy of the files the revision stands for
    snapshots/<lineage>/<rev>/config.yaml

    Lineage  = {"id", "title", "created_at", "created_by", "revisions": [Revision, ...]}   (oldest first, A first)
    Revision = {"rev": "A", "die", "config", "created_at", "created_by", "reason", "agreed_with_client", "project_id",
                "frozen", "frozen_at", "parent_rev", "new_die", "snapshot": {"taken_at", "die_sha256", "config_sha256"}}

A lineage is the motor FAMILY the revisions belong to (named after its first revision).  Revision N+1 may live in another
configuration of the same die (only winding / stack changed) or in another die (the lamination changed).  Only the LAST
revision of a lineage is the working one; every earlier one is ``frozen`` - read-only for good.

This module holds the logic and the guards; ``routes/motor_revisions.py`` is the HTTP face.  ``routes/family.py`` calls the
guards (``guard_config_write`` in the catalogue write seam, ``refuse_delete_*``), ``die_rename.py`` follows a die rename.
"""
from __future__ import annotations

import contextlib
import contextvars
import hashlib
import logging
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from fastapi import HTTPException

from motor_ai_sim import json_store

log = logging.getLogger(__name__)

#: lineage ids that the routes keep for themselves
RESERVED_IDS = frozenset({"index", "assign"})


# ── letters ──────────────────────────────────────────────────────────────────

def rev_letters(n: int) -> str:
    """The revision letter of the ``n``-th revision (1 = "A"): A..Z, AA, AB, ... AZ, BA, ... ZZ, AAA (spreadsheet columns)."""
    if n < 1:
        raise ValueError("revision numbers start at 1")
    out = ""
    while n:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


def rev_number(letters: str) -> int:
    """Inverse of :func:`rev_letters` ("A" -> 1, "AA" -> 27); ValueError for anything that is not upper-case letters."""
    s = str(letters or "")
    if not re.fullmatch(r"[A-Z]+", s):
        raise ValueError(f"not a revision letter: {letters!r}")
    n = 0
    for ch in s:
        n = n * 26 + (ord(ch) - 64)
    return n


def next_rev(prev: str) -> str:
    return rev_letters(rev_number(prev) + 1)


def label(rev: Optional[str]) -> str:
    return f"Rev {rev}" if rev else ""


# ── the store ────────────────────────────────────────────────────────────────

def root() -> Path:
    configured = os.environ.get("MOTRES_MOTOR_REVISIONS_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser()
    from motor_ai_sim.workspace import shared_root
    return Path(str(shared_root())) / "motor_revisions"


def _file() -> Path:
    return root() / "revisions.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _lock():
    """ONE lock for every read-modify-write of the revision records (re-entrant)."""
    return json_store.lock_for(_file())


def _load() -> Dict[str, dict]:
    doc = json_store.read_json(_file(), default=None)
    lineages = doc.get("lineages") if isinstance(doc, dict) else None
    return lineages if isinstance(lineages, dict) else {}


def _mutate(fn) -> Any:
    out: Dict[str, Any] = {}

    def edit(doc):
        if not isinstance(doc.get("lineages"), dict):
            doc["lineages"] = {}
        out["value"] = fn(doc["lineages"])

    root().mkdir(parents=True, exist_ok=True)
    json_store.mutate_json(_file(), edit, default={"lineages": {}})
    return out.get("value")


def _same(a: str, b: str) -> bool:
    return str(a) == str(b) or str(a).casefold() == str(b).casefold()


def find(die: str, config: str) -> Optional[Tuple[dict, dict]]:
    """``(lineage, revision)`` of the motor ``die/config`` or ``None`` (exact name first, then case-insensitive)."""
    die, config = str(die or ""), str(config or "")
    if not die or not config:
        return None
    loose: Optional[Tuple[dict, dict]] = None
    for lin in _load().values():
        for rev in lin.get("revisions") or []:
            if rev.get("die") == die and rev.get("config") == config:
                return lin, rev
            if loose is None and _same(rev.get("die", ""), die) and _same(rev.get("config", ""), config):
                loose = (lin, rev)
    return loose


def get_lineage(lineage_id: str) -> Optional[dict]:
    lin = _load().get(str(lineage_id or ""))
    return lin if isinstance(lin, dict) else None


def revisions_of_die(die: str) -> List[Tuple[dict, dict]]:
    out = []
    for lin in _load().values():
        for rev in lin.get("revisions") or []:
            if _same(rev.get("die", ""), die):
                out.append((lin, rev))
    return out


def is_latest(lineage: dict, rev: dict) -> bool:
    revs = lineage.get("revisions") or []
    return bool(revs) and revs[-1].get("rev") == rev.get("rev")


def label_for(die: str, config: str) -> Optional[dict]:
    """The short answer other modules show next to a motor's name: ``{"rev", "label", "lineage", "latest", "frozen"}`` or ``None``."""
    hit = find(die, config)
    if hit is None:
        return None
    lin, rev = hit
    return {"rev": rev.get("rev"), "label": label(rev.get("rev")), "lineage": lin.get("id"),
            "latest": is_latest(lin, rev), "frozen": bool(rev.get("frozen"))}


# ── guards (called from the catalogue) ───────────────────────────────────────

_ALLOW_WRITE: contextvars.ContextVar[bool] = contextvars.ContextVar("motor_revisions_allow_write", default=False)


@contextlib.contextmanager
def writes_allowed() -> Iterator[None]:
    """Inside this block the frozen-revision guard stands aside (this module's own lock / snapshot step)."""
    token = _ALLOW_WRITE.set(True)
    try:
        yield
    finally:
        _ALLOW_WRITE.reset(token)


def _has_store() -> bool:
    try:
        return _file().is_file()
    except OSError:
        return False


def guard_config_write(die: str, config: str) -> None:
    """409 when ``die/config`` is a FROZEN (superseded) product revision: it is read-only for good.  Called from the catalogue
    write seam (``routes.family._save_yaml``), so every route that would rewrite the configuration is covered."""
    if _ALLOW_WRITE.get() or not _has_store():
        return
    hit = find(die, config)
    if hit is None or not hit[1].get("frozen"):
        return
    lin, rev = hit
    current = (lin.get("revisions") or [{}])[-1]
    raise HTTPException(409, detail=(
        f"'{die} / {config}' is {label(rev.get('rev'))} of {lin.get('title') or lin.get('id')} and is read-only because "
        f"{label(current.get('rev'))} replaced it. Make changes in the current revision ({current.get('die')} / "
        f"{current.get('config')}) or create a new revision."))


def refuse_delete_config(die: str, config: str) -> None:
    """409 when ``die/config`` holds a product revision (a revision is never deleted, the current one included)."""
    if not _has_store():
        return
    hit = find(die, config)
    if hit is None:
        return
    lin, rev = hit
    raise HTTPException(409, detail=(
        f"'{die} / {config}' is {label(rev.get('rev'))} of {lin.get('title') or lin.get('id')}. A product revision is never "
        "deleted; keep it, or create a new revision and work there."))


def refuse_delete_die(die: str) -> None:
    """409 when any product revision lives in ``die`` (deleting or retiring the die would delete it)."""
    if not _has_store():
        return
    held = revisions_of_die(die)
    if not held:
        return
    names = ", ".join(f"{r.get('config')} ({label(r.get('rev'))})" for _l, r in held[:6])
    raise HTTPException(409, detail=(
        f"die '{die}' holds product revision(s): {names}. A product revision is never deleted, so the die stays."))


def refuse_rename_config(die: str, config: str) -> None:
    """409 when the configuration holds a revision: the record names it, so it keeps its name."""
    if not _has_store():
        return
    hit = find(die, config)
    if hit is None:
        return
    lin, rev = hit
    raise HTTPException(409, detail=(
        f"'{die} / {config}' is {label(rev.get('rev'))} of {lin.get('title') or lin.get('id')} and keeps its name; "
        "create a new revision instead of renaming it."))


def refuse_unlock_die(die: str) -> None:
    """409 when the die of a frozen revision would be unlocked (the lamination is part of the frozen product)."""
    if not _has_store():
        return
    for _lin, rev in revisions_of_die(die):
        if rev.get("frozen"):
            raise HTTPException(409, detail=(
                f"die '{die}' is the lamination of {label(rev.get('rev'))} (replaced by a newer revision) and stays locked. "
                "Create a new revision with a new lamination to change it."))


# ── die rename follows (die_rename.step_motor_revisions) ─────────────────────

def rename_die_in(doc: Any, old: str, new: str) -> int:
    """Rewrite, in place, the ``die`` of every revision record of ``doc`` (the parsed ``revisions.json``) from ``old`` to ``new``."""
    n = 0
    lineages = doc.get("lineages") if isinstance(doc, dict) else None
    for lin in (lineages or {}).values():
        for rev in lin.get("revisions") or []:
            if rev.get("die") == old:
                rev["die"] = new
                n += 1
    return n


# ── snapshots ────────────────────────────────────────────────────────────────

def _sha(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _snapshot(lineage_id: str, rev: str, die: str, config: str) -> dict:
    """Copy ``die.yaml`` and the configuration of the revision into the store and return the record's ``snapshot`` block."""
    from motor_ai_sim.routes import family
    die_file, cfg_file = Path(str(family._die_file(die))), Path(str(family._cfg_file(die, config)))
    if not die_file.is_file() or not cfg_file.is_file():
        raise HTTPException(404, detail=f"configuration '{die}/{config}' not found")
    dst = root() / "snapshots" / lineage_id / rev
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(die_file, dst / "die.yaml")
    shutil.copyfile(cfg_file, dst / "config.yaml")
    return {"taken_at": _now(), "die_sha256": _sha(dst / "die.yaml"), "config_sha256": _sha(dst / "config.yaml")}


def _intact(rev: dict) -> Optional[bool]:
    """Does the live configuration still read exactly as at the snapshot?  ``None`` when it cannot be told."""
    snap = rev.get("snapshot") or {}
    if not snap.get("config_sha256"):
        return None
    try:
        from motor_ai_sim.routes import family
        return _sha(Path(str(family._cfg_file(rev["die"], rev["config"])))) == snap["config_sha256"]
    except Exception:                                       # noqa: BLE001 - an unreadable file is "unknown", not an error
        return None


# ── helpers on the project side ──────────────────────────────────────────────

def project_of(die: str, config: str) -> Optional[str]:
    """The development project the motor is linked to (the project's motor index), or ``None``."""
    try:
        from motor_ai_sim.development import motors, store
        row = (store.load_index().get("motors") or {}).get(motors._key(die, config))
        pid = str((row or {}).get("project_id") or "") if isinstance(row, dict) else ""
        return pid or None
    except Exception:                                       # noqa: BLE001
        return None


def _carry_recommended(project_id: str, old: Tuple[str, str], new: Tuple[str, str], actor: str) -> bool:
    """The project's staff mark "recommended" moves from the replaced revision to the new one (only when the replaced one held it)."""
    try:
        from motor_ai_sim.development import motors, store
        from motor_ai_sim.development.models import now as _stamp
        done = {"moved": False}

        def edit(doc):
            rows = [r for r in doc.get("linked_motors", []) if isinstance(r, dict)]
            old_row = next((r for r in rows if motors._key(str(r.get("die") or ""), str(r.get("config") or "")) == motors._key(*old)), None)
            new_row = next((r for r in rows if motors._key(str(r.get("die") or ""), str(r.get("config") or "")) == motors._key(*new)), None)
            if old_row is None or new_row is None or not isinstance(old_row.get("recommended"), dict):
                return
            new_row["recommended"] = {"by": actor, "at": _stamp(), "carried_from": f"{old[0]}/{old[1]}"}
            old_row.pop("recommended", None)
            doc["updated_at"] = _stamp()
            done["moved"] = True

        with motors.transaction():
            store.mutate_project(project_id, edit)
        if done["moved"]:
            store.append_audit(project_id, actor, "motor.recommended", "motor", f"{new[0]}/{new[1]}", {"value": True, "carried": True})
        return done["moved"]
    except Exception:                                       # noqa: BLE001 - the flag is a courtesy, never a failure
        log.warning("motor revisions: the recommended mark could not move to %s/%s", new[0], new[1], exc_info=True)
        return False


# ── operations ───────────────────────────────────────────────────────────────

def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(text or "").casefold()).strip("-")
    return (s or "motor")[:48].strip("-") or "motor"


def _unique_lineage_id(lineages: Dict[str, dict], base: str) -> str:
    cand, i = base, 2
    while cand in lineages or cand in RESERVED_IDS:
        cand = f"{base}-{i}"
        i += 1
    return cand


def _title(die: str, config: str) -> str:
    return f"{die} · {config}"


def assign(die: str, config: str, *, actor: str, project_id: Optional[str] = None,
           agreed_with_client: bool = False) -> dict:
    """Rev A of ``die/config``.  409 when the motor already belongs to a lineage."""
    die, config = str(die).strip(), str(config).strip()
    from motor_ai_sim.routes import family
    if not family._cfg_file(die, config).is_file() or not family._die_file(die).is_file():
        raise HTTPException(404, detail=f"configuration '{die}/{config}' not found")
    if project_id:
        from motor_ai_sim.development import store
        if store.get_project(project_id) is None:
            raise HTTPException(422, detail=f"project '{project_id}' not found")
    project_id = project_id or project_of(die, config)
    with _lock():
        hit = find(die, config)
        if hit is not None:
            raise HTTPException(409, detail=f"'{die} / {config}' already is {label(hit[1].get('rev'))} of {hit[0].get('title')}")

        def add(lineages: Dict[str, dict]) -> dict:
            lid = _unique_lineage_id(lineages, _slug(f"{die} {config}"))
            snap = _snapshot(lid, "A", die, config)
            stamp = _now()
            rev = {"rev": "A", "die": die, "config": config, "created_at": stamp, "created_by": actor, "reason": "",
                   "agreed_with_client": bool(agreed_with_client), "project_id": project_id, "frozen": False,
                   "frozen_at": None, "parent_rev": None, "new_die": False, "snapshot": snap}
            lineages[lid] = {"id": lid, "title": _title(die, config), "created_at": stamp, "created_by": actor, "revisions": [rev]}
            return lineages[lid]

        lin = _mutate(add)
    log.info("motor revisions: %s/%s is Rev A of lineage %s (by %s)", die, config, lin["id"], actor or "?")
    return lin


def _lock_config_and_die(die: str, config: str) -> Dict[str, bool]:
    """Freeze step: the configuration of the replaced revision is locked, and so is its die (the lamination is part of it).
    Returns what was changed (``{"config": True, "die": True}``) so a failed new revision can put it back."""
    from motor_ai_sim.routes import family
    changed = {"config": False, "die": False}
    with writes_allowed():
        c = family._load_yaml(family._cfg_file(die, config), "configuration")
        if c.get("locked") is not True:
            c["locked"] = True
            family._save_yaml(family._cfg_file(die, config), c)
            changed["config"] = True
        d = family._load_yaml(family._die_file(die), "die")
        if d.get("locked") is False:                        # an absent key already reads as locked
            d["locked"] = True
            family._save_yaml(family._die_file(die), d)
            changed["die"] = True
    return changed


def _unlock_again(die: str, config: str, changed: Dict[str, bool]) -> None:
    from motor_ai_sim.routes import family
    try:
        with writes_allowed():
            if changed.get("config"):
                c = family._load_yaml(family._cfg_file(die, config), "configuration")
                c["locked"] = False
                family._save_yaml(family._cfg_file(die, config), c)
            if changed.get("die"):
                d = family._load_yaml(family._die_file(die), "die")
                d["locked"] = False
                family._save_yaml(family._die_file(die), d)
    except Exception:                                       # noqa: BLE001
        log.warning("motor revisions: could not unlock '%s/%s' again", die, config, exc_info=True)


def new_revision(lineage_id: str, *, reason: str, who: dict, actor: str, new_die: bool = False,
                 config_name: Optional[str] = None, die_name: Optional[str] = None,
                 from_rev: Optional[str] = None, agreed_with_client: bool = False) -> dict:
    """Make the next revision of the lineage from its current one.

    ``who`` is the catalogue-write dependency answer (``routes.family.require_catalog_write``): the copy is made by the SAME
    family helpers the Motors catalogue uses (``duplicate_config`` / ``duplicate_die``), so naming, workspace layers and history
    behave exactly as for any copy.  Order: copy first, freeze the replaced revision second, record last; a failure after the
    copy removes the copy again.
    """
    reason = str(reason or "").strip()
    if not reason:
        raise HTTPException(422, detail="say what changed and why - the reason is required for a new revision")
    from motor_ai_sim.routes import family
    with _lock():
        lin = get_lineage(lineage_id)
        if lin is None:
            raise HTTPException(404, detail="motor lineage not found")
        revs = lin.get("revisions") or []
        cur = revs[-1]
        if from_rev and from_rev != cur.get("rev"):
            raise HTTPException(409, detail=f"{label(cur.get('rev'))} is the current revision now ({label(from_rev)} was replaced); reload and try again")
        nxt = next_rev(cur["rev"])
        die, cfg = cur["die"], cur["config"]
        if not family._die_file(die).is_file() or not family._cfg_file(die, cfg).is_file():
            raise HTTPException(404, detail=f"the current revision '{die}/{cfg}' is not in the catalogue any more")
        authorization = who.get("authorization")
        base = revs[0]["config"]
        created_cfg_only: Optional[Tuple[str, str]] = None
        if new_die:
            new_die_name = family._check_name(die_name or family._default_die_name(die, authorization), "die")
            out = family.duplicate_die(die, family.DieDuplicate(name=new_die_name), who)
            new_die_name = str(out.get("die") or new_die_name)
            if cfg not in (out.get("configs") or []):
                _rollback_die(new_die_name, who)
                raise HTTPException(409, detail=f"the copy of die '{die}' did not carry configuration '{cfg}' (it is hidden from this account)")
            new_cfg = cfg
            target = (new_die_name, new_cfg)
        else:
            cfg_name = config_name or family._unique_config_name(die, f"{base}-{nxt}")
            family.duplicate_config(die, cfg, family.ConfigDuplicate(name=family._check_name(cfg_name, "configuration")), who)
            target = (die, family._check_name(cfg_name, "configuration"))
            created_cfg_only = target
        locked_now: Dict[str, bool] = {}
        try:
            # the replaced revision: lock first, snapshot second (the snapshot is the locked, final state)
            locked_now = _lock_config_and_die(die, cfg)
            lin_id = lin["id"]
            if new_die:
                family._autolink_new_config(target[0], target[1], who, copied_from=die)
            stamp = _now()

            def add(lineages: Dict[str, dict]) -> dict:
                live = lineages[lin_id]
                old = live["revisions"][-1]
                old["snapshot"] = _snapshot(lin_id, old["rev"], old["die"], old["config"])
                old["frozen"] = True
                old["frozen_at"] = stamp
                new_snap = _snapshot(lin_id, nxt, target[0], target[1])
                pid = project_of(*target) or old.get("project_id")
                live["revisions"].append({
                    "rev": nxt, "die": target[0], "config": target[1], "created_at": stamp, "created_by": actor,
                    "reason": reason, "agreed_with_client": bool(agreed_with_client), "project_id": pid, "frozen": False,
                    "frozen_at": None, "parent_rev": old["rev"], "new_die": bool(new_die), "snapshot": new_snap})
                return live

            updated = _mutate(add)
        except BaseException:
            _unlock_again(die, cfg, locked_now)
            _rollback_copy(target, created_cfg_only is None, who)
            raise
    newest = updated["revisions"][-1]
    carried = False
    if newest.get("project_id"):
        carried = _carry_recommended(newest["project_id"], (die, cfg), target, actor)
    log.info("motor revisions: lineage %s -> %s (%s/%s) by %s; %s is read-only", lin_id, label(nxt), target[0], target[1], actor or "?", label(cur["rev"]))
    return {"lineage": updated, "revision": newest, "previous": updated["revisions"][-2], "recommended_carried": carried}


def _rollback_die(die: str, who: dict) -> None:
    try:
        from motor_ai_sim.routes import family
        family.delete_die(die, True, who)
    except Exception:                                       # noqa: BLE001
        log.warning("motor revisions: could not remove the unused copy die '%s'", die, exc_info=True)


def _rollback_copy(target: Tuple[str, str], whole_die: bool, who: dict) -> None:
    """A new revision failed after its copy was made: take the copy away again (best effort, logged)."""
    try:
        from motor_ai_sim.routes import family
        if whole_die:
            family.delete_die(target[0], True, who)
        else:
            family.delete_config(target[0], target[1], who)
    except Exception:                                       # noqa: BLE001
        log.warning("motor revisions: could not remove the unused copy '%s/%s'", target[0], target[1], exc_info=True)


def set_agreed(lineage_id: str, rev: str, agreed: bool) -> dict:
    with _lock():
        def edit(lineages: Dict[str, dict]) -> dict:
            lin = lineages.get(lineage_id)
            row = next((r for r in (lin or {}).get("revisions", []) if r.get("rev") == rev), None)
            if row is None:
                raise HTTPException(404, detail="revision not found")
            row["agreed_with_client"] = bool(agreed)
            return lin
        return _mutate(edit)


# ── views ────────────────────────────────────────────────────────────────────

def public_revision(lin: dict, rev: dict, *, staff: bool, with_integrity: bool = False) -> dict:
    """The revision as the API shows it.  The reason ("what changed and why") is the engineers' record: staff only."""
    out = {"rev": rev.get("rev"), "label": label(rev.get("rev")), "die": rev.get("die"), "config": rev.get("config"),
           "created_at": rev.get("created_at"), "agreed_with_client": bool(rev.get("agreed_with_client")),
           "project_id": rev.get("project_id"), "frozen": bool(rev.get("frozen")), "frozen_at": rev.get("frozen_at"),
           "parent_rev": rev.get("parent_rev"), "new_die": bool(rev.get("new_die")), "latest": is_latest(lin, rev),
           "read_only": bool(rev.get("frozen"))}
    if staff:
        out["reason"] = rev.get("reason") or ""
        out["created_by"] = rev.get("created_by") or ""
        snap = rev.get("snapshot") or {}
        out["snapshot"] = {"taken_at": snap.get("taken_at"), "die_sha256": snap.get("die_sha256"), "config_sha256": snap.get("config_sha256")}
        if with_integrity:
            out["snapshot_intact"] = _intact(rev)
    return out
