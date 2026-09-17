"""Die -> Configuration -> Duty catalog.

One stamped lamination (the die) is a fixed 2-D cross-section; what a real
product varies on top of it is the stack length, the wire and the winding —
that is a CONFIGURATION — and each configuration is operated at a handful of
named points (current / rpm / mode / gamma) — those are DUTIES.

Storage is one folder per die under config/dies/:

    config/dies/D200-24s28p/
        die.yaml            # the stamped geometry (all 44 keys) + lock
        M1-L200.yaml        # configuration: free geometry keys + winding + duties
        G1-L160.yaml

This router only reads/writes THESE files.  Applying a duty to the live
motor is the frontend's job through the existing endpoints (geometry PUT,
winding PATCH, simulation-config PATCH) — no new write-path into the live
config exists here, so nothing already working can be broken by this layer.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel

from motor_ai_sim import auth as _auth
from motor_ai_sim.auth import caller_identity, require_admin
from motor_ai_sim.config import get_config
from motor_ai_sim import workspace as _WS
from motor_ai_sim.workspace import layering as _ws_layering
from motor_ai_sim.workspace import resolve_die_dir as _ws_resolve_die_dir
from motor_ai_sim.workspace import root as _ws_root_f
from motor_ai_sim.motor_access import (MODE_ANONYMOUS, MODE_GRANTED,
                                       catalog_access, may_see_die)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/family", tags=["family"])

# Migration Stage 1: the die catalog lives in the caller's WORKSPACE, resolved
# per call.  With no workspace set that is ``Path(DEFAULT_CONFIG_PATH).parent /
# "dies"`` — the old expression exactly.  (Stage 2 splits this into a shared
# read-only template layer plus a per-user results overlay.)  The NAME survives:
# eight test modules monkeypatch it, and a value in the module dict wins.
def _dies_dir() -> Path:
    _ov = globals().get("_DIES_DIR")
    if _ov is not None:
        return Path(str(_ov))
    from motor_ai_sim.workspace import root as _ws_root
    return _ws_root() / "dies"

# The keys a CONFIGURATION snapshots on top of a die — everything the stamp
# does not fix: stack length and the whole wire stack.
FREE_GEO_KEYS = (
    "motor_length",
    "wire_width", "wire_height",
    "wire_spacing_x", "wire_spacing_y",
    "num_wires_per_slot", "wire_split", "wire_parallel",
    "insulation_thickness",
)

# What stays EDITABLE while the die is locked — the user's definition, and
# deliberately narrower than FREE_GEO_KEYS: stack length, wire height, turn
# count.  Everything else (wire width, spacings, split, insulation) is part
# of the qualified build and changes only on an unlocked machine.
#: What a DIE lock still lets the engineer change.  These are the winding's
#: electrical knobs: none of them moves a polygon the mesher receives, so a
#: machine edited here is the same DIE — the same laminations, the same slot,
#: the same tools.
#:
#: `wire_parallel` joined them on 2026-09-11 (user: "нужно дать ещё возможность
#: менять wire parallel (strands) для режимов, это никак не затрагивает
#: геометрию").  It is right: the slot holds `num_wires_per_slot` conductors
#: whatever the strands in hand are, and `wire_parallel` only says how they are
#: grouped — it appears nowhere in `cadquery_geometry`, only in `winding`
#: (turns = conductors / strands), in the validator that makes the two divide,
#: and in the cache fingerprints.  Changing it moves the back-EMF, Kt, KV and
#: the phase resistance, and leaves the drawing alone.
#:
#: `wire_split` stays LOCKED on purpose: a split wire is N narrower strips with
#: spacing between them, the slot grows around them, and that is geometry.
EDITABLE_UNDER_DIE_LOCK = ("motor_length", "wire_height", "num_wires_per_slot",
                           "wire_parallel")

#: What a geometry key MEANS when a die/configuration does not carry it (older
#: files predate the knob).  Served by ``/payload`` so a load describes the
#: whole machine and nothing of the previously loaded one survives the PUT.
#: What a MATERIAL part means when a configuration does not name it.  The
#: catalog stores the three parts a build is defined by (magnet, the two core
#: steels); the liner and the enamel are the project's standard on every
#: machine — and a load that says nothing about them leaves the previously
#: loaded machine's picks in the shared config (2026-09-09: the Ø200's Al2O3
#: ceramic liner, tried on 2026-09-07/08, sat on every motor loaded after it —
#: user: "почему у всех моторов поменялся материал изоляции, всегда был Nomex").
_ABSENT_MATERIALS: Dict[str, str] = {
    "slot_insulation": "Nomex",
    "wire_insulation": "polyimide",
}

#: The parts a configuration SAVES its material choice for.  Everything solid
#: the machine is made of — not the three the save used to keep (2026-09-09).
#:
#: User: *"почему изоляция в этой машине опять Nomex, я же менял её на Al2O3"*.
#: The save kept magnet / stator_core / rotor_core only, so a slot liner, a wire
#: enamel, a conductor or a shaft grade chosen in Materials had nowhere to live:
#: on the next activation the absent keys were filled from ``_ABSENT_MATERIALS``
#: (Nomex, polyimide) and the choice was gone.  The two rules are complements —
#: what a configuration names, it keeps; what it does not name falls back to a
#: NAMED default rather than to whatever the previously loaded machine had
#: (which is the leak the absent-materials table was added for that morning).
#:
#: The air placeholders (``air_gap``, ``in_band``, ``out_band``) are not saved:
#: they are always air, and writing them would put three meaningless lines in
#: every configuration file.
_SAVED_MATERIAL_KEYS: tuple = (
    "magnet", "stator_core", "rotor_core", "shaft", "sleeve",
    "slot", "slot_insulation", "wire_insulation",
)

_ABSENT_MEANS: Dict[str, Any] = {
    "sleeve_thickness": 0.0,   # no retaining ring
    "wire_parallel": 1,        # one wire in hand
    "wire_split": 1,           # one strip per wire row
}

# Display names double as file/dir names, so the charset is "safe on every
# filesystem": letters, digits, space and light punctuation.  Real product
# names have spaces ("CILN28 200", "CIANO14 30_10").
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.,()#+°·\- ]{0,63}$")

# Duty names are NOT file names — a duty lives inside its configuration's yaml
# — so they admit Unicode letters.  The user types them off a Russian keyboard,
# where the Cyrillic С is pixel-identical to the Latin C ('rated 30С' was
# refused with a message no one could act on, 2026-09-01), and duties saved
# before the validator already carry Cyrillic letters.
_DUTY_NAME_RE = re.compile(r"^[^\W_][\w.,()#+°·\- ]{0,63}$", re.UNICODE)


def _lookalike_hint(n: str) -> str:
    """Name the offending characters — 'invalid name' with an invisible
    Cyrillic С in it is a message the user cannot act on."""
    bad = sorted({ch for ch in n if not _NAME_RE.match(f"a{ch}")})
    parts = []
    for ch in bad[:5]:
        try:
            import unicodedata
            parts.append(f"'{ch}' ({unicodedata.name(ch).title()})")
        except Exception:                       # noqa: BLE001 — unnamed codepoint
            parts.append(f"'{ch}' (U+{ord(ch):04X})")
    return ("; offending character(s): " + ", ".join(parts)
            + ". A Cyrillic С looks identical to the Latin C — check the "
              "keyboard layout") if parts else ""


def _check_name(name: str, what: str) -> str:
    n = (name or "").strip()
    if what == "duty":
        if not _DUTY_NAME_RE.match(n):
            raise HTTPException(422, detail=(
                f"duty name '{name}' is invalid — use letters (any alphabet), "
                "digits, spaces or '-_.,()#+', up to 64 characters, starting "
                "with a letter or digit"))
        return n
    if not _NAME_RE.match(n) or n != n.strip():
        raise HTTPException(422, detail=(
            f"{what} name '{name}' is not usable as a file name — use Latin "
            "letters, digits, spaces or '-_.,()#+', up to 64 characters, "
            "starting with a letter or digit" + _lookalike_hint(n)))
    return n


# ── the three layers (migration Stage 2) ─────────────────────────────────────
# READS fall through workspace -> published -> shared, first hit wins.  WRITES
# never fall through: they land in the caller's own workspace, and a die that
# lives only in published/ or shared/ has its YAML DOCUMENTS (never its results)
# copied across on the first write — copy-on-write, the same bargain a union
# filesystem makes.  An admin may aim a write at the curated layer explicitly
# with ``?layer=shared``.
#
# With ``WORKSPACES_ROOT`` unset there is exactly one layer and every function
# below is the expression it replaced, character for character.

def _die_dir(die: str) -> Path:
    """Where the named die is READ from.

    Falls back to the workspace path when no layer has it, so a 404 still names
    the folder the user would have created — the pre-Stage-2 answer exactly.
    """
    if _ws_layering():
        d = _ws_resolve_die_dir(str(die))
        if d is not None:
            return d
    return _dies_dir() / die


def _die_file(die: str) -> Path:
    return _die_dir(die) / "die.yaml"


def _cfg_file(die: str, cfg: str) -> Path:
    return _die_dir(die) / f"{cfg}.yaml"


def _die_layer(die: str) -> str:
    """``"workspace"`` | ``"published"`` | ``"shared"`` — where the die is read
    from right now.  A die that exists nowhere reads as ``"workspace"``: that is
    where creating it would put it."""
    if not _ws_layering():
        return _WS.LAYER_WORKSPACE
    d = _ws_resolve_die_dir(str(die))
    return _classify_die_dir(d)[0] if d is not None else _WS.LAYER_WORKSPACE


def _classify_die_dir(d: Optional[Path]) -> tuple:
    """``(layer, addressed-name)`` for a die FOLDER.

    The addressed name is what the API calls this die — the folder name, except
    for another account's published work, which is addressed by its community
    label (``"CIANO28 85 · by Alice"``).  Recomputed from the path rather than
    remembered, so a write started from a read can never disagree with it.
    """
    if d is None:
        return (_WS.LAYER_WORKSPACE, "")
    d = Path(str(d))
    name = d.name
    if not _ws_layering():
        return (_WS.LAYER_WORKSPACE, name)
    try:
        if d.parent == _dies_dir():
            return (_WS.LAYER_WORKSPACE, name)
        if d.parent == Path(str(_WS.shared_root())) / "dies":
            return (_WS.LAYER_SHARED, name)
        pub = Path(str(_WS.published_root()))
        if d.parent.parent == pub:
            ns = d.parent
            if ns.name == _WS.workspace().id:
                return (_WS.LAYER_PUBLISHED, name)
            return (_WS.LAYER_PUBLISHED,
                    _WS.published_label(name, _WS._owner_email(ns)))
    except (OSError, ValueError):            # noqa: BLE001 — a path we cannot compare
        pass
    return (_WS.LAYER_WORKSPACE, name)


def _copy_on_write(src_dir: Path, addressed: str) -> Path:
    """Bring a published/shared die into the workspace so it can be written.

    The YAML DOCUMENTS only — ``die.yaml`` and every configuration.  Not
    ``runs/``: those are the OTHER user's (or the vendor's) solved answers, and
    copying a gigabyte of somebody else's fields because you renamed a duty is
    not a write, it is a fork.  They stay readable through the read-through;
    anything this workspace solves from here lands beside its own copy.

    Idempotent, and it never overwrites a file that is already here: a second
    save of the same die must not undo the first.
    """
    dst = _dies_dir() / addressed
    dst.mkdir(parents=True, exist_ok=True)
    copied = 0
    for p in sorted(src_dir.glob("*.yaml")):
        q = dst / p.name
        if q.exists():
            continue
        import shutil as _sh
        _sh.copy2(p, q)
        copied += 1
    if copied:
        # Rename the die inside its own document when the label became the name.
        try:
            import yaml as _y
            f = dst / "die.yaml"
            doc = _y.safe_load(f.read_text(encoding="utf-8")) or {}
            if isinstance(doc, dict) and doc.get("name") != addressed:
                doc["name"] = addressed
                f.write_text(_y.safe_dump(doc, sort_keys=False,
                                          allow_unicode=True), encoding="utf-8")
        except Exception:                    # noqa: BLE001 — cosmetic, never fatal
            log.debug("family: could not restamp the copied die name",
                      exc_info=True)
        log.info("family: die '%s' copied into the workspace on first write "
                 "(%d document(s) from %s)", addressed, copied, src_dir)
    return dst


def _write_target(p: Path) -> Path:
    """The path a catalog document is ACTUALLY written to.

    One choke point, because ``_save_yaml`` is the one writer of every die and
    configuration file in this router — so "a user's save never touches the
    shared catalog" is a property of four lines rather than of forty call sites.
    """
    if not _ws_layering():
        return p
    src_dir = Path(str(p)).parent
    layer, addressed = _classify_die_dir(src_dir)
    want = _WS.write_layer()
    if want == _WS.LAYER_SHARED:
        if not _WS.is_admin():
            raise HTTPException(403, detail=(
                "only an admin may write the shared catalog — drop "
                "'?layer=shared' and the save lands in your own workspace"))
        plain = _WS.split_published_label(addressed or src_dir.name)[0]
        return Path(str(_WS.shared_root())) / "dies" / plain / p.name
    if layer == _WS.LAYER_WORKSPACE:
        return p
    return _copy_on_write(src_dir, addressed) / p.name


def _require_writable_die(die: str) -> None:
    """Structural edits — rename, delete — of a die this caller does not own.

    Copy-on-write answers "may I change this duty?"; it cannot answer "may I
    RENAME this die?", because renaming a shared die inside a workspace would
    fork the catalog's identity and delete would ask to remove somebody else's
    file.  Both are refused, and the message says which layer holds it.
    """
    if not _ws_layering():
        return
    if _WS.write_layer() == _WS.LAYER_SHARED and _WS.is_admin():
        return
    lay = _die_layer(die)
    if lay == _WS.LAYER_WORKSPACE:
        return
    raise HTTPException(403, detail=(
        f"die '{die}' lives in the {lay} catalog and is read-only here — "
        "duties and configurations you save on it are copied into your own "
        "workspace, but renaming or deleting it is the "
        + ("author's" if lay == _WS.LAYER_PUBLISHED else "vendor's") + " call"))


def _ensure_writable_die(die: str) -> Path:
    """The folder a STRUCTURAL edit of this die must happen in.

    ``_save_yaml`` copies a die across on its own, but deleting or renaming a
    CONFIGURATION unlinks and moves files directly, and those calls must never
    be aimed at another layer's file.  So the copy happens first and the caller
    gets the workspace folder back.
    """
    d = _die_dir(die)
    if not _ws_layering():
        return d
    if _WS.write_layer() == _WS.LAYER_SHARED and _WS.is_admin():
        return d
    layer, addressed = _classify_die_dir(d)
    if layer == _WS.LAYER_WORKSPACE:
        return d
    return _copy_on_write(d, str(die))


def _iter_die_entries() -> list:
    """Every visible die as ``{"name", "die", "dir", "layer", "owner"}``.

    With layering off this is ``_dies_dir()``'s own listing, so ``catalog_dies``
    / ``die_names`` / ``tree`` keep answering exactly what they answered.
    """
    if _ws_layering():
        return _WS.iter_dies()
    out = []
    d = _dies_dir()
    if not d.is_dir():
        return out
    for dd in sorted(d.iterdir()):
        if dd.is_dir() and (dd / "die.yaml").is_file():
            out.append({"name": dd.name, "die": dd.name, "dir": dd,
                        "layer": _WS.LAYER_WORKSPACE, "owner": "",
                        "owner_id": ""})
    return out


def _require_die_access(die: str, authorization) -> dict:
    """404 unless the caller may read this die (motor_access.may_see_die).

    404 and not 403 on purpose: a motor an account was never granted must not
    be distinguishable from one that does not exist.  Hiding a die from the
    tree without this guard would be cosmetic — the payload endpoint would
    still hand over the whole machine to anyone who guessed the name.

    Stage 2: grants gate the SHARED catalog and nothing else.  A die in the
    caller's own workspace is theirs by construction, and a PUBLISHED die is
    visible to every registered account — that is what publishing means.  An
    anonymous visitor still sees only the public exhibit.
    """
    acc = catalog_access(authorization)
    if _ws_layering():
        lay = _die_layer(die)
        if lay == _WS.LAYER_WORKSPACE:
            return acc
        if lay == _WS.LAYER_PUBLISHED:
            if acc["mode"] == MODE_ANONYMOUS:
                raise HTTPException(404, detail=f"die '{die}' not found")
            return acc
    if not may_see_die(acc, die):
        raise HTTPException(404, detail=f"die '{die}' not found")
    return acc


# ── who may WRITE the catalog (migration Stage 5) ────────────────────────────
# The user's rule, 2026-09-15: *"общий каталог правит пока только админ;
# пользователи всё сохраняют только в своём пространстве, но могут и делиться
# со всеми"*.
#
# Stage 2 built the machinery for the second half of that sentence — every write
# in this router funnels through ``_write_target``/``_ensure_writable_die``,
# which copy a published or shared die into the CALLER'S OWN workspace and
# cannot aim a non-admin's save at the curated catalog — but all 21 write routes
# still hung on ``require_admin``, so a registered account was refused before it
# ever reached that seam and the copy-on-write path was unreachable in
# production (found in the Stage 5 rehearsal).
#
# The gate is therefore two rules, not one:
#
#   layering ON  (WORKSPACES_ROOT set) — any REGISTERED account may write, and
#                the write lands in its own workspace.  ``?layer=shared`` and
#                the structural edits of somebody else's die stay the admin's.
#   layering OFF (this workstation, and every test that does not set the env
#                var) — ``require_admin``, character for character, because
#                there is exactly one layer and the only place a write could
#                land IS the shared catalog.

#: The lowest tier that may write its own workspace.  ``free`` is the tier
#: ``auth._GATED`` already spells for the one other route gated on merely being
#: signed in (``POST /api/support/chat``): a registered account, anonymous not.
WRITE_MIN_TIER = "free"


def require_catalog_write(authorization: str = Header(default=None)) -> dict:
    """FastAPI dependency for every family WRITE route.

    Answers ``{"user", "is_admin", "authorization"}`` — the authorization header
    rides along so a route can ask the SECOND question
    (:func:`_require_die_write`: may this caller write THIS die?) without a
    parameter of its own.

    401 for an anonymous caller, 403 for a signed-in one who may not write here:
    the same two codes ``require_admin`` answers, so nothing downstream of an
    HTTP client has to learn a new shape.
    """
    if not _ws_layering():
        # Byte-identical to the pre-Stage-5 gate, exceptions included.  Called
        # through the auth module and not through this module's own name, so
        # that it stays the very function ``Depends(require_admin)`` used to
        # capture at import — a test that rebinds ``family.require_admin`` never
        # reached that dependency either.
        return {"user": _auth.require_admin(authorization), "is_admin": True,
                "authorization": authorization}
    is_admin, user = _auth._is_admin_caller(authorization)
    if is_admin:
        return {"user": user or {"uid": "local-dev", "email": None,
                                 "tier": "admin"},
                "is_admin": True, "authorization": authorization}
    # ``?layer=shared`` (and its ``published`` sibling) is the admin's explicit
    # aim at a curated layer.  ``_write_target`` refuses it too — this is the
    # early, cheap refusal, so a non-admin never gets as far as loading the
    # document it would not have been allowed to save.
    if _WS.write_layer() is not None:
        raise HTTPException(403, detail=(
            "only an admin may write the shared catalog — drop "
            "'?layer=shared' and the save lands in your own workspace"))
    if user is None:
        raise HTTPException(401, detail="Sign in required.")
    tier = str(user.get("tier") or "anon")
    if _auth._TIER_RANK.get(tier, -1) < _auth._TIER_RANK[WRITE_MIN_TIER]:
        raise HTTPException(403, detail=(
            "a registered account is required to save into your workspace."))
    return {"user": user, "is_admin": False, "authorization": authorization}


def _require_die_write(die: str, who: dict) -> None:
    """May this caller write the named die?  The READ question, deliberately.

    A die in the caller's own workspace is theirs by construction; a PUBLISHED
    die is readable by every registered account and a save on it is a
    copy-on-write into the caller's workspace; a SHARED die needs the grant that
    lets the caller see it at all.  Which is exactly
    :func:`_require_die_access`'s answer — so write access is read access plus
    the copy, and there is no second table of rules to drift out of step.

    404 and not 403, for the reason stated there: a motor an account was never
    granted must not be distinguishable from one that does not exist, and a save
    is a perfectly good oracle if it answers differently.
    """
    if not _ws_layering() or who.get("is_admin"):
        return
    _require_die_access(str(die), who.get("authorization"))


def _can_write_catalog(who: dict) -> bool:
    """What the tree / context routes report as ``can_write``.

    The same rule :func:`require_catalog_write` enforces, so the UI never offers
    a button the API refuses (nor hides one it would accept).  ``who`` is either
    a ``caller_identity`` mapping or a ``catalog_access`` one.
    """
    if who.get("is_admin"):
        return True
    if not _ws_layering():
        return False
    if who.get("mode") is not None:                      # catalog_access
        return who.get("mode") != MODE_ANONYMOUS
    ident = str(who.get("id") or "")
    return bool(ident) and ident != _auth.ANON_OWNER


def catalog_dies() -> list[dict]:
    """Every die on disk with its configuration and duty counts — the admin
    motor picker's source (unfiltered; the route behind it is admin-only)."""
    out: list[dict] = []
    for _e in _iter_die_entries():
        dd = Path(str(_e["dir"]))
        try:
            die = _load_yaml(dd / "die.yaml", "die")
        except HTTPException:      # a broken file must not blank the picker
            continue
        geo = die.get("geometry") or {}
        cfgs: list[dict] = []
        for cf in sorted(dd.glob("*.yaml")):
            if cf.name == "die.yaml":
                continue
            try:
                c = _load_yaml(cf, "configuration")
            except HTTPException:
                continue
            cfgs.append({"name": cf.stem, "duties": len(c.get("duties") or [])})
        out.append({
            "name": _e["name"],
            **({"layer": _e["layer"], "owner": _e["owner"] or None}
               if _ws_layering() else {}),
            "stator_diameter": geo.get("stator_diameter"),
            "slots": geo.get("num_slots"), "poles": geo.get("num_poles"),
            "configs": len(cfgs),
            "config_names": [c["name"] for c in cfgs],
            "duties": sum(c["duties"] for c in cfgs),
        })
    return out


def die_names() -> set[str]:
    """The die names a grant may legally reference."""
    return {str(e["name"]) for e in _iter_die_entries()}


def _load_yaml(p: Path, what: str) -> dict:
    try:
        with open(p, encoding="utf-8") as f:
            d = yaml.safe_load(f) or {}
        if not isinstance(d, dict):
            raise ValueError("not a mapping")
        return d
    except FileNotFoundError:
        raise HTTPException(404, detail=f"{what} not found: {p.name}")
    except Exception as e:  # noqa: BLE001 — a broken file must be SAID, not 500'd blind
        raise HTTPException(500, detail=f"{what} file {p.name} is unreadable: {e}")


def _save_yaml(p: Path, d: dict) -> None:
    # Stage 2: THE write seam.  A document resolved out of published/ or shared/
    # is redirected into the caller's own workspace (copying the die's yaml
    # documents across on the way), and an admin's ``?layer=shared`` write is
    # redirected the other way.  With layering off this is a no-op.
    p = _write_target(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.safe_dump(d, f, sort_keys=False, allow_unicode=True)
    # ── Catalog history ──────────────────────────────────────────────────────
    # config/dies is NOT under git, and the ripple-optimized 150 was only
    # recoverable through an optimizer side-effect (incident 2026-08-24).
    # Every overwrite that CHANGES a file first snapshots the old version into
    # .history/<die>/<file>.<stamp>.yaml (30 newest kept).  Best-effort: a
    # history hiccup must never fail the save itself.
    try:
        if p.exists():
            old = p.read_bytes()
            if old != tmp.read_bytes():
                from datetime import datetime as _dt
                hdir = _dies_dir() / ".history" / p.parent.name
                hdir.mkdir(parents=True, exist_ok=True)
                stamp = _dt.now().strftime("%Y%m%d-%H%M%S")
                (hdir / f"{p.stem}.{stamp}.yaml").write_bytes(old)
                snaps = sorted(hdir.glob(f"{p.stem}.*.yaml"))
                for s in snaps[:-30]:
                    s.unlink()
    except Exception:   # noqa: BLE001
        log.exception("catalog history snapshot failed (save proceeds)")
    _replace_with_retry(tmp, p)


def _replace_with_retry(tmp: Path, p: Path) -> None:
    """``tmp -> p``, retried — the catalog's twin of ``json_store``'s replace.

    ON WINDOWS a rename fails with ``PermissionError`` while ANY other handle is
    open on the DESTINATION, and these files are read on request paths: the
    catalog tree the browser polls every few seconds opens every configuration
    yaml.  ``json_store`` learned this on 2026-09-03 and retries; ``_save_yaml``
    did not, so a duty save that landed while the catalog was being read died
    with a bare 500 and left an orphan ``.tmp`` beside the file (lived
    2026-09-14: a coupled run's 7-minute answer refused to save three times in a
    row while the Thermal tab was open).

    Same budget as ``json_store``, and the same last resort: rather than lose a
    save the document is written IN PLACE, under that module's per-path lock, and
    the fallback is logged as a WARNING naming the file.  A reader that catches
    the in-place write mid-parse sees an unreadable yaml and says so on the next
    read; a silently dropped save is the outcome that cannot be recovered from.
    """
    import os as _os
    import time as _time
    from motor_ai_sim.json_store import (_REPLACE_BACKOFF_MAX_S,
                                         _REPLACE_BACKOFF_S, _REPLACE_TRIES,
                                         lock_for)
    last: Optional[BaseException] = None
    delay = _REPLACE_BACKOFF_S
    for attempt in range(1, _REPLACE_TRIES + 1):
        try:
            _os.replace(tmp, p)
            if attempt > 1:
                log.info("%s: os.replace succeeded on attempt %d (the file was "
                         "locked by another reader)", p.name, attempt)
            return
        except PermissionError as e:      # Windows: destination handle open
            last = e
            if attempt < _REPLACE_TRIES:
                _time.sleep(delay)
                delay = min(_REPLACE_BACKOFF_MAX_S, delay * 2)
    with lock_for(p):
        p.write_bytes(tmp.read_bytes())
    log.warning("%s: os.replace stayed locked after %d tries (%s) — wrote the "
                "configuration IN PLACE under the store lock instead of losing "
                "the save", p, _REPLACE_TRIES, last)
    try:
        tmp.unlink()
    except OSError:
        pass


def _plain(v):
    """OmegaConf/DictConfig → plain python (yaml-safe)."""
    try:
        from omegaconf import OmegaConf
        if OmegaConf.is_config(v):
            return OmegaConf.to_container(v, resolve=True)
    except Exception:
        pass
    return v


def _live_cfg() -> dict:
    c = _plain(get_config(reload=True))
    return c if isinstance(c, dict) else dict(c)


def _sim_of(c: dict) -> dict:
    return dict(c.get("simulation") or {})


def _live_parts() -> dict:
    """The live machine's per-part accounting states, or {} when every part is
    included.  Same shape the configuration file stores under ``parts:`` and
    the ``?mat=`` payload carries under ``parts``."""
    try:
        from motor_ai_sim.part_states import config_part_states
        return dict(config_part_states())
    except Exception:      # noqa: BLE001
        return {}


def _config_role(c: dict) -> tuple:
    """WHAT THIS CONFIGURATION IS, read off its duties.

    User 2026-09-10: *"почему здесь motor, хотя это генератор"* — the chip said
    `motor` on a configuration named "L180 gen" whose every duty is a generator
    duty.  `role` was captured ONCE, at creation, from whatever the Simulation
    panel's mode toggle happened to be, written into the yaml and never looked
    at again; nothing kept it in step with the duties that were then saved
    under it.

    The duties ARE the answer: a configuration whose duties all generate is a
    generator whatever the toggle said the day it was made.  Mixed duties are
    said as "mixed" rather than resolved by majority — a machine that both
    drives and generates is a real thing and picking one would hide it.  The
    stored value survives only as the fallback for a configuration that has no
    duties yet, which is the one case where the creation-time toggle is all
    there is.

    Returns (role, source) — `source` says which of the two answered, so the
    catalog can explain itself in a tooltip instead of just asserting.
    """
    stored = str(c.get("role") or "motor").strip().lower()
    modes = set()
    for d in (c.get("duties") or []):
        if not isinstance(d, dict):
            continue
        m = str(d.get("mode") or "").strip().lower()
        if m in ("motor", "generator"):
            modes.add(m)
    if not modes:
        return (stored if stored in ("motor", "generator") else "motor",
                "stored")
    if len(modes) == 1:
        return (next(iter(modes)), "duties")
    return ("mixed", "duties")


def _build_sig(die_doc: dict, cfg_doc: dict) -> str:
    """Fingerprint of the BUILD a result was computed on: the die's stamped
    geometry + the configuration's free-key overrides + winding + materials.
    Duty RESULTS carry the sig they were recorded under; a mismatch against
    the configuration's current sig marks them 'computed on an older build'."""
    import hashlib
    import json as _json
    src = {
        "geo": die_doc.get("geometry") or {},
        "ov": cfg_doc.get("geometry_overrides") or {},
        "winding": cfg_doc.get("winding") or {},
        "materials": cfg_doc.get("materials") or {},
        # Per-part accounting states (included / reference / excluded).  A
        # frameless build that stops billing its shaft — or solves it as air —
        # is not the build its earlier duties were computed on: `excluded`
        # changes the FIELD and `reference` changes every N·m/kg, so a flip
        # must flag stored results as computed on an older build, exactly as
        # re-assigning the core steel does.  Absent (every part included) the
        # key is a missing dict, so pre-existing signatures are unchanged only
        # when the configuration carries no `parts:` block — which is every
        # configuration written before this feature.
        **({"parts": cfg_doc["parts"]} if cfg_doc.get("parts") else {}),
    }
    return hashlib.sha1(
        _json.dumps(src, sort_keys=True, default=str).encode()).hexdigest()[:12]


# ── stored RUNS: one per excitation source, beside the duty ──────────────────
# A duty's PRIMARY result is the sinusoidal-CURRENT run: that is the number the
# catalog row shows and what every comparison is made against.  The same duty
# run on the PWM inverter (or a 120° block) answers a different question about
# the same operating point, and it costs twelve minutes — but saving it used to
# OVERWRITE the sine snapshot, and re-loading the duty then restored the PWM
# settings, so every later Run became a PWM solve (user 2026-09-02).
#
# So every excitation keeps its own entry under `runs:`, keyed by drive, and
# the heavy waveform payload goes into a gzip sidecar under
# ``config/dies/<die>/runs/<config>/<duty>.<drive>.json.gz`` — out of the yaml,
# which stays a file an engineer can read.  The top-level mesh/summary/result
# remain the sine story; a duty with no `runs:` block behaves exactly as it did
# before this existed, and its file is not rewritten until the user saves it.
_RUN_DRIVES = ("current", "voltage", "pwm_voltage", "bldc_current",
               "custom_current")

# The per-element arrays no chart reads: one full mesh + A_z per animation
# frame, the last frame's field, and the per-triangle demagnetisation map.
# They turn a 0.7 MB run into hundreds of megabytes (the .last_transient
# incident, 2026-09-01) and are dropped on the way in — re-run to get them.
_RUN_HEAVY_KEYS = ("frames", "field", "demag_field", "demag_coef_per_tri")

# What a single stored run may weigh before the request is refused outright.
_RUN_MAX_BYTES = 8 * 1024 * 1024


def _run_drive(name) -> str:
    """Validate an excitation NAMED BY THE CALLER (endpoint parameter)."""
    d = str(name or "").strip()
    if d not in _RUN_DRIVES:
        raise HTTPException(422, detail=(
            f"unknown excitation '{name}' — expected one of: "
            + ", ".join(_RUN_DRIVES)))
    return d


def _drive_of(explicit=None, settings=None, summary=None) -> str:
    """Which excitation a saved run was solved with, from whichever of the
    three witnesses can answer: the caller's own field, the run summary's
    `drive`, or the panel settings' `sim.drive`.  Anything unrecognised (a
    duty saved before this existed) reads as `current` — the behaviour every
    stored duty has always had."""
    for cand in (
            explicit,
            (summary or {}).get("drive") if isinstance(summary, dict) else None,
            (settings or {}).get("sim.drive") if isinstance(settings, dict) else None):
        d = str(cand or "").strip()
        if d in _RUN_DRIVES:
            return d
    return "current"


def _with_mech_loss(d: Dict[str, Any]) -> Any:
    """The duty's ``result``, with ``loss_mech_w`` DERIVED when it is absent.

    Bearings and windage began to be saved with a duty on 2026-09-11, so every
    duty written before that has an efficiency that carries them (it is the
    shaft efficiency) and no watts to show for it — the catalog would print the
    electromagnetic loss beside a shaft efficiency, and mark it as unknown.
    Nobody should have to press save on a machine they did not change to get a
    field the code learned to store afterwards (user: "я же ничего не менял,
    зачем мне ещё раз всё пересохранять?"), so it is read back here from the
    run's own summary, which has carried ``P_bearings_W`` / ``P_windage_W`` all
    along.  Derived at SERVE time: nothing is rewritten, and a duty re-saved
    later simply stops needing this.
    """
    r = d.get("result")
    if not isinstance(r, dict) or r.get("loss_mech_w") is not None:
        return r
    for run in (d.get("runs") or {}).values():
        su = (run or {}).get("summary")
        if not isinstance(su, dict):
            continue
        brg, wind = su.get("P_bearings_W"), su.get("P_windage_W")
        if brg is None and wind is None:
            continue
        try:
            out = dict(r)
            out["loss_mech_w"] = round(float(brg or 0.0) + float(wind or 0.0), 1)
            out["loss_mech_derived"] = True
            return out
        except (TypeError, ValueError):
            continue
    return r


def _die_doc_for_sig(die: str) -> dict:
    """The die document, for `_build_sig`.  Best-effort: a signature that cannot
    be computed must never fail a duty save."""
    try:
        return _load_yaml(_die_file(die), "die")
    except Exception:                                        # noqa: BLE001
        return {}


def _restamp_results(c: dict, old_sig: str, new_sig: str) -> int:
    """Move every duty result carrying ``old_sig`` onto ``new_sig``.

    Used for ONE case only — see the caller: the configuration adopted
    materials it had simply never named, so the build did not change, only its
    description did.  Returns how many results were moved.
    """
    if not old_sig or not new_sig or old_sig == new_sig:
        return 0
    n = 0
    for d in (c.get("duties") or []):
        if not isinstance(d, dict):
            continue
        for holder in [d.get("result")] + [
                (r or {}).get("result") for r in (d.get("runs") or {}).values()]:
            if isinstance(holder, dict) and holder.get("build_sig") == old_sig:
                holder["build_sig"] = new_sig
                n += 1
    return n


def _primary_drive(entry: dict) -> str:
    """The excitation the duty's PRIMARY (top-level) snapshot was solved with."""
    return _drive_of(None, entry.get("mesh"), entry.get("summary"))


def _run_stem(duty: str) -> str:
    """A file name for a duty that may be written in any alphabet.

    The Latin/digit run of the name for readability plus 8 hex of its sha1, so
    two duties whose names differ only in a Cyrillic look-alike ('30C' vs
    '30С' — the trap from 2026-09-01) can never land on the same file, and a
    fully Cyrillic name still gets a name at all.  STABLE: the same duty name
    always maps to the same stem, which is what makes rename / duplicate /
    delete bookkeeping possible without a lookup table."""
    import hashlib
    raw = str(duty)
    ascii_part = re.sub(r"[^A-Za-z0-9]+", "_", raw).strip("_")[:40]
    h = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
    return f"{ascii_part}-{h}" if ascii_part else f"duty-{h}"


def _w_die_dir(die: str) -> Path:
    """The WORKSPACE folder of this die, whether or not it is there yet.

    ``die`` is the name the API was addressed with, which for another account's
    published work is its community label — the same name ``_copy_on_write``
    gives the copy, so a save and the sidecars it writes always land together.
    """
    return _dies_dir() / str(die)


def _src_die_dir(die: str) -> Optional[Path]:
    """The published/shared folder this die also lives in, or ``None``.

    Where a read falls THROUGH to.  Kept separate from ``_die_dir`` because
    after a copy-on-write the resolver answers "workspace" and the results the
    other layer still holds would otherwise become unreachable.
    """
    return _WS.source_die_dir(str(die))


def _under_workspace(p: Path) -> bool:
    if not _ws_layering():
        return True
    try:
        Path(str(p)).relative_to(_dies_dir())
        return True
    except ValueError:
        return False


def _runs_dir(die: str, cfg: str) -> Path:
    """Where this die's run sidecars are WRITTEN — always the workspace."""
    return _w_die_dir(die) / "runs" / cfg


def _run_rel(cfg: str, duty: str, drive: str) -> str:
    """The sidecar's path RELATIVE to the die folder — what `payload_file`
    stores, so renaming or duplicating a WHOLE die carries its runs unchanged."""
    return f"runs/{cfg}/{_run_stem(duty)}.{drive}.json.gz"


def _run_path(die: str, rel: str) -> Path:
    """READ path for a stored run: the workspace's own copy, else the layer the
    die was published or curated in.  Writers call :func:`_run_path_w`."""
    w = _w_die_dir(die) / str(rel)
    if w.is_file() or not _ws_layering():
        return w
    src = _src_die_dir(die)
    if src is not None and (src / str(rel)).is_file():
        return src / str(rel)
    return w


def _run_path_w(die: str, rel: str) -> Path:
    return _w_die_dir(die) / str(rel)


def _strip_run_payload(payload: dict) -> dict:
    return {k: v for k, v in dict(payload or {}).items()
            if k not in _RUN_HEAVY_KEYS}


def _write_run_payload(die: str, rel: str, duty: str, drive: str,
                       payload: dict) -> int:
    """Gzip the run's waveforms next to the die.  The duty NAME rides inside
    the file: the stem is hashed, and a folder of hashes nobody can read is
    not a catalog anyone can repair by hand."""
    import gzip
    import json as _json
    p = _run_path_w(die, rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = _json.dumps({"name": duty, "drive": drive,
                        "recorded_at": datetime.now().isoformat(timespec="seconds"),
                        "payload": _strip_run_payload(payload)},
                       default=str).encode("utf-8")
    tmp = p.with_suffix(".tmp")
    with gzip.open(tmp, "wb") as f:
        f.write(body)
    tmp.replace(p)
    return p.stat().st_size


def _read_run_payload(die: str, rel: str) -> Optional[dict]:
    import gzip
    import json as _json
    p = _run_path(die, rel)
    if not p.is_file():
        return None                     # the entry outlived its file — say so
    try:
        with gzip.open(p, "rb") as f:
            d = _json.loads(f.read().decode("utf-8"))
    except Exception as e:              # noqa: BLE001 — a corrupt sidecar must be SAID
        raise HTTPException(500, detail=(
            f"stored run payload '{rel}' is unreadable: {e} — load the duty "
            "and re-run the point to record it again"))
    return d.get("payload") if isinstance(d, dict) else None


def _star_delta_of(c: dict, duty: Optional[str]) -> str:
    """'star' | 'delta' for a duty load: the DUTY's own stored connection
    first (the one its results were solved with — two duties of one
    configuration may run in Y and in Δ), then its saved panel setting
    (`mesh['sim.starDelta']`), then the configuration's winding block, then
    star."""
    v = None
    if duty is not None:
        d = next((x for x in (c.get("duties") or [])
                  if isinstance(x, dict) and x.get("name") == duty), None)
        v = (d or {}).get("star_delta") or ((d or {}).get("summary") or {}).get("star_delta")
        if not v:
            m = (d or {}).get("mesh") if isinstance((d or {}).get("mesh"), dict) else {}
            v = (m or {}).get("sim.starDelta")
    if not v:
        v = (c.get("winding") or {}).get("star_delta")
    return "delta" if str(v or "star").lower().startswith("d") else "star"


def _store_run(die: str, cfg: str, cfg_doc: dict, entry: dict, drive: str, *,
               settings=None, summary=None, result=None,
               assignment_sig=None, payload=None) -> dict:
    """Record (or update) this duty's run for ONE excitation.  Fields not sent
    inherit from the previous record of the same drive — the save arrives in
    three calls (point, result, waveforms) and none of them may erase what the
    other two put there."""
    runs = entry.get("runs")
    if not isinstance(runs, dict):
        runs = {}
    prev = runs.get(drive) if isinstance(runs.get(drive), dict) else {}
    rec: dict = {"recorded_at": datetime.now().isoformat(timespec="seconds")}
    try:
        rec["build_sig"] = _build_sig(_load_yaml(_die_file(die), "die"), cfg_doc)
    except HTTPException:
        pass                            # a die that cannot be read is not fatal here
    sig = assignment_sig if assignment_sig is not None else prev.get("assignment_sig")
    if sig:
        rec["assignment_sig"] = str(sig)
    for k, v in (("settings", settings), ("summary", summary), ("result", result)):
        vv = v if v is not None else prev.get(k)
        if vv:
            rec[k] = dict(vv)
    if payload is not None:
        rel = _run_rel(cfg, str(entry.get("name") or ""), drive)
        _write_run_payload(die, rel, str(entry.get("name") or ""), drive, payload)
        rec["payload_file"] = rel
    elif prev.get("payload_file"):
        # Inherit the previous sidecar ONLY while it is the same run.  A save
        # that brings a new summary and no waveforms used to keep the old
        # file under the new `recorded_at`: on 2026-09-13 both duties of the
        # Ø200 carried today's numbers over waveforms of 2026-09-11 (a STAR
        # run at 672 A) and 2026-09-12 — the report drew "line voltages —
        # peak 674 V" beside a 702 V table.  Same run = same operating point
        # and the same phase-voltage peak; anything else and the pointer is
        # dropped, so the report says "not stored" instead of lying.
        _keep = True
        # Against the SIDECAR's own summary, not the previous record's: the
        # record may already have inherited a foreign file once (that is how
        # the 2026-09-11 waveforms survived two re-saves with today's numbers).
        _side = None
        try:
            _pl = _read_run_payload(die, str(prev["payload_file"]))
            _side = (_pl or {}).get("summary") if isinstance(_pl, dict) else None
        except Exception:      # noqa: BLE001 — unreadable sidecar = not this run
            _side = None
        if summary is not None and not isinstance(_side, dict):
            _keep = False
        if summary is not None and isinstance(_side, dict):
            _ps, _ns = _side, summary
            for _k, _tol in (("rpm", 0.5), ("gamma_deg", 0.05),
                             ("V_phase_peak_V", None), ("I_phase_rms_A", None)):
                _a, _b = _ps.get(_k), _ns.get(_k)
                if _a is None or _b is None:
                    continue
                try:
                    _a, _b = float(_a), float(_b)
                except (TypeError, ValueError):
                    continue
                if _tol is None:
                    _keep &= abs(_a - _b) <= 1e-3 * max(abs(_a), abs(_b), 1.0)
                else:
                    _keep &= abs(_a - _b) <= _tol
        if _keep:
            rec["payload_file"] = prev["payload_file"]
        else:
            log.warning("family: %s run of '%s/%s/%s' re-saved WITHOUT waveforms "
                        "and the stored sidecar %s belongs to a different run — "
                        "pointer dropped; re-run the point to record them",
                        drive, die, cfg, entry.get("name"), prev.get("payload_file"))
    runs[drive] = rec
    entry["runs"] = runs
    return rec


def _run_rows(build_sig: str, entry: dict) -> list[dict]:
    """What the TREE says about a duty's stored runs — never a payload."""
    prim = _primary_drive(entry)
    rows: list[dict] = []
    for drive in _RUN_DRIVES:
        r = (entry.get("runs") or {}).get(drive)
        if not isinstance(r, dict):
            continue
        st = r.get("settings") if isinstance(r.get("settings"), dict) else {}
        summ = r.get("summary") if isinstance(r.get("summary"), dict) else {}
        res = r.get("result") if isinstance(r.get("result"), dict) else {}
        rows.append({
            "drive": drive,
            "primary": drive == prim,
            "recorded_at": r.get("recorded_at"),
            "build_sig": r.get("build_sig"),
            "assignment_sig": r.get("assignment_sig"),
            # Solved on a build the configuration has since moved off — the
            # chip goes amber, same rule as the duty row's own result.
            "stale": bool(r.get("build_sig") and build_sig
                          and r.get("build_sig") != build_sig),
            "ripple_pct": res.get("ripple_pct", summ.get("T_ripple_pct")),
            "f_switch_hz": st.get("sim.fSwitch"),
            "steps": st.get("sim.stepsPP"),
            "has_payload": bool(r.get("payload_file")),
        })
    return rows


def _ttl_num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def _time_to_limit_row(coupled: Any) -> Optional[Dict[str, Any]]:
    """The catalog row's half of a stored ``time_to_limit`` block — or ``None``.

    HOW LONG MAY IT RUN (owner, 2026-09-17).  The coupled loop answers about the
    STEADY state; when that state is past a limit it also integrates the step
    response and says how long the machine has.  The Thermal tab prints it; the
    catalog row is where the reader is ALREADY looking when they ask "can I pull
    this point?", so the answer gets a chip there too.

    ``None`` on three things, and each of them is an answer rather than a gap:
    a duty with no coupled record (never solved), a record written before this
    feature existed, and — the important one — a point that is INSIDE every
    limit it has.  A machine that is not over anything has no time to a limit,
    and a chip quoting one would invite planning around a number that is not a
    constraint.

    FLATTENED ON PURPOSE.  The block carries ``limits_c`` / ``at_point_c`` as a
    dict PER PART; the row carries the limiting part's two scalars under
    deliberately different names, so a reader of either shape cannot mistake one
    for the other.  Everything else stays behind — the tree is fetched on every
    catalog render, and the per-part list, the two start states and the network
    fit are a payload (same rule as ``_run_rows``: descriptions only).
    """
    t = (coupled or {}).get("time_to_limit") if isinstance(coupled, dict) else None
    if not isinstance(t, dict) or t.get("within_limits", True):
        return None
    part = str(t.get("limiting_part") or "")
    starts = t.get("starts") if isinstance(t.get("starts"), dict) else {}

    def _start(name: str) -> Optional[float]:
        blk = starts.get(name)
        return _ttl_num((blk or {}).get("time_to_limit_s")) \
            if isinstance(blk, dict) else None

    row: Dict[str, Any] = {
        "part": part or None,
        "at_point_c": _ttl_num((t.get("at_point_c") or {}).get(part)),
        "limit_c": _ttl_num((t.get("limits_c") or {}).get(part)),
        "over_by_K": _ttl_num((t.get("over_by_K") or {}).get(part)),
        "cold_s": _start("cold"),
        "rated_s": _start("rated"),
        # The block's OWN sentence, which is what the tooltip prints: it already
        # names the part, the limit and both starts, and a second sentence
        # written here could disagree with the one the panel and the report show.
        "note": str(t.get("note") or "") or None,
    }
    return row


def _time_to_limit_kv(coupled: Any) -> Dict[str, Any]:
    """``{"time_to_limit": row}`` or ``{}`` — the duty literal splices this.

    An ABSENT key, never a null one: "this duty has no time-to-limit answer" and
    "this duty's answer is nothing" are the same thing to a reader and both mean
    "draw no chip", so the tree says it once, the way ``duty_cycle`` and the
    layer keys beside it are said.
    """
    row = _time_to_limit_row(coupled)
    return {"time_to_limit": row} if row else {}


def _duty_run_files(entry: dict) -> list[str]:
    return [str(r["payload_file"])
            for r in (entry.get("runs") or {}).values()
            if isinstance(r, dict) and r.get("payload_file")]


def _move_duty_runs(die: str, entry: dict, cfg: str, new_duty: str,
                    *, copy: bool) -> None:
    """Re-file a duty entry's sidecars under `new_duty` and repoint its
    `payload_file`s.  A missing source is not an error — the rename still
    happens and the loader reports the payload as gone, which beats refusing
    to rename a duty because one of its runs was deleted by hand."""
    import shutil
    for drive, r in list((entry.get("runs") or {}).items()):
        if not isinstance(r, dict) or not r.get("payload_file"):
            continue
        src_rel = str(r["payload_file"])
        dst_rel = _run_rel(cfg, new_duty, drive)
        if dst_rel == src_rel:
            continue
        src, dst = _run_path(die, src_rel), _run_path_w(die, dst_rel)
        r["payload_file"] = dst_rel
        if not src.is_file():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        # A sidecar that still lives in published/ or shared/ is COPIED even on
        # a rename: this workspace may re-file its own view of somebody else's
        # duty, it may not move their file out from under them.
        if copy or not _under_workspace(src):
            shutil.copy2(src, dst)
        else:
            src.replace(dst)


def _refile_config_runs(die: str, cfg_doc: dict, old_cfg: str, new_cfg: str,
                        *, copy: bool) -> None:
    """A configuration was renamed or duplicated: its whole runs folder moves
    (or is copied) with it, and every duty's `payload_file` follows."""
    import shutil
    src, dst = _runs_dir(die, old_cfg), _runs_dir(die, new_cfg)
    if not src.is_dir():
        # Renaming a configuration of a die read out of published/ or shared/:
        # the sidecars are still over there, so the workspace takes a COPY under
        # the new name rather than leaving the renamed duties pointing at
        # nothing.  Never a move — the file is not this workspace's to take.
        _s = _src_die_dir(die)
        if _s is not None and (_s / "runs" / old_cfg).is_dir():
            src, copy = _s / "runs" / old_cfg, True
    if src.is_dir():
        dst.parent.mkdir(parents=True, exist_ok=True)
        if copy:
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            if dst.exists():
                shutil.rmtree(dst, ignore_errors=True)
            src.replace(dst)
    for d in (cfg_doc.get("duties") or []):
        for drive, r in list(((d.get("runs") or {}) if isinstance(d, dict) else {}).items()):
            if isinstance(r, dict) and r.get("payload_file"):
                r["payload_file"] = _run_rel(new_cfg, str(d.get("name") or ""), drive)


def _delete_duty_runs(die: str, entry: dict) -> int:
    n = 0
    for rel in _duty_run_files(entry):
        p = _run_path_w(die, rel)          # never another layer's file
        try:
            if p.is_file():
                p.unlink()
                n += 1
        except OSError:                 # noqa: PERF203 — a locked file is not fatal
            log.warning("family: could not delete stored run %s", p)
    return n


def _delete_config_runs(die: str, cfg: str) -> None:
    import shutil
    shutil.rmtree(_runs_dir(die, cfg), ignore_errors=True)


# ── models ───────────────────────────────────────────────────────────────────

class DutySpec(BaseModel):
    name: str
    mode: str = "motor"                  # "motor" | "generator"
    current_arms: Optional[float] = None  # None + from_current → read Simulation
    rpm: Optional[float] = None
    gamma_deg: Optional[float] = None
    torque_nm: Optional[float] = None    # target/rated torque — shown on the card
    power_kw: Optional[float] = None     # target/rated power — shown on the card
    note: Optional[str] = None           # None = keep the previous note on upsert
    from_current: bool = False           # fill the Nones from the live Simulation
    # The COMPLETE computed state of the run being saved (user 2026-08-25:
    # "это всё должно сохраняться"): the mesh settings the numbers were solved
    # on, and the full summary block — so loading the duty restores the exact
    # card (all constants, live 3D/R/KV buttons) and the exact mesh, not
    # whatever happened to linger in the browser.
    mesh: Optional[dict] = None
    summary: Optional[dict] = None
    # THIS DUTY's materials — a PARTIAL assignment {part: material} laid over
    # the configuration's own `materials:` when the duty is loaded (user
    # 2026-09-01: "все материалы, для каждого duty").  A duty here is a full
    # thermal scenario ("peak 200C wire 120C NdFeB"), so the magnet temperature
    # record and the steel it was characterised with belong to it, not to the
    # machine.  A `null` value is the duty SAYING "the configuration's own" —
    # kept, not dropped, because it has to outrank an older stored pick.
    # Absent → the previous save's dict survives, same rule as `mesh`/`result`.
    materials: Optional[dict] = None
    # THIS DUTY's DUTY CYCLE — what the machine actually does with the point:
    # S1 (continuous), S2 (one pull of t_on_s), S3 (ED % of cycle_s, resting at
    # `rest_duty` or unpowered), or an explicit `segments` list.  A robot joint
    # spends seconds at its peak and minutes at nothing, and a steady map of the
    # peak answers a question nobody asked, so the cycle is part of the duty's
    # DEFINITION and lives in the yaml with it.  Shape (all of it optional
    # beyond `kind`):
    #     duty_cycle: {kind, t_on_s, ed_pct, cycle_s, rest_duty, duty,
    #                  segments: [{duty, t_s}], t_start_c, n_cycles_max,
    #                  calibration_duty, saved_at}
    # Absent → the previous save's block survives; sent → it is written on EVERY
    # save, the same rule `materials` follows (it is not excitation-specific: a
    # PWM run of the same point is the same cycle).
    duty_cycle: Optional[dict] = None
    # WHICH EXCITATION the attached run used (`current` = sinusoidal current,
    # `pwm_voltage` = the inverter, …).  It decides whether this save owns the
    # duty's PRIMARY snapshot or only its own `runs[<drive>]` entry.  Absent →
    # read off the summary / the settings block, and `current` when neither
    # says — which is every duty saved before this field existed.
    drive: Optional[str] = None
    # The MATERIAL ASSIGNMENT the run was solved with
    # (lib/dutyMaterials.assignmentSignature), recorded so a stored run can be
    # flagged when the machine is re-assigned under it.
    assignment_sig: Optional[str] = None


def _validate_duty_cycle(block, duties, *, duty_name: Optional[str] = None) -> None:
    """Refuse a `duty_cycle:` block that cannot mean what it says — by name.

    The validator IS ``thermal_duty_cycle.normalise_spec``, the same function
    the thermal solve goes through, asked for its structure pass: one rule in
    one place, so a block the catalog accepts is a block the solver reads the
    same way.  It checks the shape (a known kind, a run time for S2,
    0 < ED ≤ 100 % and a cycle time for S3, a positive duration on every
    segment) and that every duty the cycle NAMES — the running one and the rest
    one — exists in this configuration.  It deliberately does NOT ask whether
    those duties have been solved: a cycle may be written before the points are
    run, and the solve refuses that later, by name.

    A `null` rest duty is not an unknown duty — it is the machine standing
    unpowered, which is a real segment of a real cycle.
    """
    if not isinstance(block, dict) or not block:
        return
    from motor_ai_sim.thermal_duty_cycle import DutyCycleError, normalise_spec
    try:
        normalise_spec(block, duties, default_duty=duty_name,
                       structure_only=True)
    except DutyCycleError as exc:
        raise HTTPException(422, detail="duty_cycle (%s): %s" % (exc.code, exc))
    except (TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(422, detail=(
            "duty_cycle is malformed (%s) — it is a block like "
            "{kind: S3, ed_pct: 25, cycle_s: 60, rest_duty: null}, or "
            "{kind: segments, segments: [{duty: peak, t_s: 2}, "
            "{duty: null, t_s: 8}]}." % exc))


#: The keys of a `duty_cycle:` block that hold a DUTY NAME.  `segments` is
#: handled separately — it is a list of {duty, t_s}.
_DUTY_CYCLE_NAME_KEYS = ("duty", "rest_duty", "calibration_duty")


def _repoint_duty_cycles(duties, old: str, new: str) -> int:
    """Follow a duty RENAME through every stored `duty_cycle:` block.

    Returns how many references moved.  Mutates the entries in place, the way
    `_move_duty_runs` moves the sidecars beside them.
    """
    if old == new:
        return 0
    n = 0
    for d in (duties or []):
        blk = d.get("duty_cycle") if isinstance(d, dict) else None
        if not isinstance(blk, dict):
            continue
        for k in _DUTY_CYCLE_NAME_KEYS:
            if blk.get(k) is not None and str(blk[k]) == old:
                blk[k] = new
                n += 1
        for seg in (blk.get("segments") or []):
            if (isinstance(seg, dict) and seg.get("duty") is not None
                    and str(seg["duty"]) == old):
                seg["duty"] = new
                n += 1
    return n


def config_doc(die: str, cfg: str) -> Optional[dict]:
    """One configuration's whole stored document — no HTTP, no access check.

    The reader ``duty_entry`` is built on, exported because a solve route that
    needs MORE than one duty (the duty-cycle model reads every duty a cycle
    names, plus the configuration's ``materials:`` and ``parts:`` blocks) would
    otherwise reimplement the yaml path, and two paths to one file is how one
    machine's numbers end up under another's name.  ``None`` for anything it
    cannot read: a solver must never fail because the catalog was mid-rename.
    """
    try:
        return _load_yaml(_cfg_file(_check_name(die, "die"),
                                    _check_name(cfg, "configuration")),
                          "configuration")
    except Exception:                      # noqa: BLE001 — a reader, not a gate
        return None


def config_duties(die: str, cfg: str) -> list[dict]:
    """Every duty of one configuration, in the order the yaml holds them."""
    c = config_doc(die, cfg) or {}
    return [d for d in (c.get("duties") or []) if isinstance(d, dict)]


def duty_entry(die: str, cfg: str, duty: str) -> Optional[dict]:
    """One duty's stored entry, straight off the yaml — no HTTP, no access check.

    For the SOLVE routes, which need to know what the catalog says about the
    point they are about to run (``routes.coupled``'s pre-flight reads the duty
    cycle through here).  Returns ``None`` for anything it cannot read: a solver
    must never fail because the catalog was mid-rename.
    """
    for d in config_duties(die, cfg):
        if str(d.get("name") or "") == str(duty):
            return d
    return None


class DieCreate(BaseModel):
    name: str


# Computed numbers a RUN produced at this duty's operating point — never
# invented, only recorded off a finished Simulation run.
_RESULT_KEYS = ("efficiency_pct", "ripple_pct", "v_ll_peak_v", "loss_w", "mass_kg",
                "p_core_w", "p_stranded_w", "p_solid_w",
                "v_phase_peak_v", "j_coil_a_mm2",
                # Bearings + windage, the watts `efficiency_pct` already carries
                # since 2026-09-11 (it is the SHAFT efficiency, like the card).
                # `loss_w` stays the electromagnetic total its three components
                # sum to, so the row can be read either way round.
                "loss_mech_w",
                # When the user saves with the 3D toggle ON, the recorded
                # torque/power/voltages carry the Stage-A end-effect
                # correction and this stamps WHICH k_flux was applied — a
                # catalog row must never hide that its numbers are corrected.
                "end3d_k",
                # KV exactly as DISPLAYED at save time (user 2026-08-25):
                # the value follows the card's KV button; kv_is_noload = 1
                # marks the no-load (bench) convention, 0 the loaded one.
                "kv_rpm_per_v", "kv_is_noload")


class DutyResult(BaseModel):
    die: str
    config: str
    duty: str
    result: dict
    # Same rule as DutySpec.drive: a `current` run (or the first run this duty
    # ever got) owns the headline numbers the catalog shows; every other
    # excitation is recorded under `runs[<drive>]` and leaves them alone.
    drive: Optional[str] = None
    assignment_sig: Optional[str] = None


class DutyRunSave(BaseModel):
    """A finished run's WAVEFORMS, filed under one excitation of one duty."""
    die: str
    config: str
    duty: str
    drive: str
    settings: Optional[dict] = None      # the panel block (= the duty's `mesh`)
    summary: Optional[dict] = None
    result: Optional[dict] = None
    assignment_sig: Optional[str] = None
    payload: Optional[dict] = None       # the transient, heavy arrays stripped


class ConfigCreate(BaseModel):
    die: str
    name: str
    role: str = "motor"                  # informative: "motor" | "generator"


class DutyCreate(BaseModel):
    die: str
    config: str
    duty: DutySpec


# ── tree ─────────────────────────────────────────────────────────────────────

#: The tree, memoised per ACCESS (mode + granted dies + admin) and keyed by a
#: signature of every file it is built from.  It is not an HTTP cache — the
#: response stays `no-store` and per-account — it is the YAML parse that goes:
#: 30 die/configuration files carrying 13 inline SVG thumbnails took ~0.7 s to
#: parse per request (measured 2026-09-13), and the Motors tab asked for the
#: tree once per Ø section at once, so the tab "loaded" for the sum of them.
#: A changed grant is a different key; a changed file is a different signature.
#:
#: Stage 2 put the workspace id INTO the key (`_key` below) and Stage 3 moves
#: the store itself into that workspace: the id stays in the key as the audit
#: (``tests/test_workspace_state.py`` walks every live store and refuses a key
#: that does not start with the workspace it was produced under), and the
#: container is now per workspace and BOUNDED — it was an unbounded dict, and
#: its key carries an access mode plus the grant list, so a server with many
#: accounts grew one parsed catalog per distinct grant set, forever.
#: CAP 8: one entry per (access mode × client filter) a single account can
#: produce, which is four, doubled for head-room.
_TREE_CACHE_MAX = 8
_TREE_CACHE = _WS.ws_map("family.tree_cache", _TREE_CACHE_MAX, lru_on_read=True)


def _tree_signature(with_catalog: bool) -> tuple:
    """Every file `tree()` reads, with its mtime and size — what the memo is
    valid for.  ~30 stats, well under a millisecond.

    THE DUTY-RESULTS STORE IS ONE OF THEM (2026-09-17).  Since the duty row
    carries the coupled loop's "how long may it run" answer, the tree is no
    longer built from the YAML files alone: a coupled run writes
    ``.duty_results.json`` and touches no yaml, so a signature over the catalog
    only would keep serving a row saying the machine is fine hours after the
    loop said it is not.  One stat per store — one file unless layering is on —
    and `duty_results.store_signature` returns `()` on anything unreadable,
    which lands in the same "never serve from the memo" branch as an
    unreadable die.
    """
    sig = []
    try:
        _dies = []
        for _e in _iter_die_entries():
            dd = Path(str(_e["dir"]))
            _dies.append(str(_e["name"]))
            for f in sorted(dd.glob("*.yaml")):
                st = f.stat()
                sig.append((_e["name"], f.name, st.st_mtime_ns, st.st_size))
        from motor_ai_sim import duty_results as _dr
        _dsig = _dr.store_signature(_dies)
        if _dies and not _dsig:
            return ()                # unreadable store → never served from memo
        sig.extend(("\0duty_results", *row) for row in _dsig)
        if with_catalog:
            p = _ws_root_f() / "motor_catalog.json"
            if p.is_file():
                st = p.stat()
                sig.append(("motor_catalog.json", "", st.st_mtime_ns, st.st_size))
    except OSError:
        return ()                    # unreadable right now → never served from memo
    return tuple(sig)


@router.get("/tree")
def tree(response: Response, authorization: str = Header(default=None)):
    # Who may CHANGE the catalog: the vendor alone on a single-layer install,
    # and — since Stage 5 — any registered account once layering is on, because
    # then its saves land in its own workspace (`require_catalog_write`).
    # can_write tells the frontend whether to draw the editing controls; the
    # mutating endpoints enforce the same rule server-side regardless.
    #
    # WHICH motors are listed is per-account (motor_access): admins and
    # `all`-granted accounts see everything, a signed-in account sees its
    # granted dies, an anonymous visitor sees the passported public exhibit.
    # The answer depends on the Authorization header and changes the moment a
    # grant does — it must never be cached (a stale tree is a user reporting
    # "I still don't see my motors" after the vendor granted them).
    _acc = catalog_access(authorization)
    _can_write = _can_write_catalog(_acc)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Vary"] = "Authorization"
    out = []
    _entries = _iter_die_entries()
    if not _entries:
        return {"dies": out, "can_write": _can_write}
    # ── Public exhibit: WORKING configurations only (user 2026-08-25) ────────
    # A motor an ANONYMOUS visitor cannot open in Configure (no FEM passport
    # yet) is a half-baked exhibit — hide it.  A die is exhibit-visible when a
    # characterised catalog card describes the same machine (slots, poles, OD,
    # magnet height).  Signed-in accounts are judged by their grants instead.
    _client_filter = _acc["mode"] == MODE_ANONYMOUS
    _passported: list = []
    if _client_filter:
        try:
            import json as _pj
            _cat = _pj.loads((_ws_root_f() / "motor_catalog.json")
                             .read_text(encoding="utf-8"))
            for _m in _cat.get("motors", []):
                _g = ((_m.get("passport") or {}).get("geo")) or {}
                if _g:
                    _passported.append(_g)
        except Exception:
            _passported = []      # no catalog → hide nothing rather than all
            _client_filter = False

    def _die_is_client_ready(g: dict) -> bool:
        # Recompute the derived radii from the die's own primaries — stored
        # deriveds can be stale (the 85 die carried stator_outer_radius 15
        # beside stator_diameter 85; matching raw fields hid a passported
        # motor from clients).
        try:
            from motor_ai_sim.simulation.geometry_2d import merge_geo_override
            g = merge_geo_override(dict(g), None)
        except Exception:
            pass
        for _g in _passported:
            try:
                if (int(_g.get("numSlots") or 0) == int(g.get("num_slots") or -1)
                        and int(_g.get("numPoles") or 0) == int(g.get("num_poles") or -1)
                        and abs(float(_g.get("statorOR_mm") or 0)
                                - float(g.get("stator_outer_radius") or -1)) <= 0.5
                        and abs(float(_g.get("magnetHeight_mm") or 0)
                                - float(g.get("magnet_height") or -1)) <= 0.25):
                    return True
            except (TypeError, ValueError):
                continue
        return False

    # The WORKSPACE is part of the key since Stage 2: two accounts with the
    # same access mode see two different catalogs, and a memo that could not
    # tell them apart is precisely the cross-user leak this migration exists to
    # prevent.  (The signature would usually catch it; "usually" is not a
    # guarantee worth resting a customer's machine on.)
    _key = (str(_acc["mode"]), tuple(sorted(str(x) for x in (_acc.get("dies") or ()))),
            _can_write, _client_filter, _WS.workspace().id)
    _sig = _tree_signature(_client_filter)
    _hit = _TREE_CACHE.get(_key)
    if _sig and _hit is not None and _hit[0] == _sig:
        res = {"dies": list(_hit[1]), "can_write": _can_write}
        if not _hit[1] and _acc["mode"] == MODE_GRANTED:
            res["note"] = "no motors granted yet — ask the vendor"
        return res

    for _e in _entries:
        dd = Path(str(_e["dir"]))
        _die_name, _layer = str(_e["name"]), str(_e["layer"])
        # A signed-in non-admin sees its granted dies and nothing else — with
        # ALL their configurations and duties, exactly as an admin would.
        # Grants gate the SHARED catalog only: a die in this workspace is the
        # caller's own, and a PUBLISHED one is open to every registered account.
        if (_acc["mode"] == MODE_GRANTED and _die_name not in _acc["dies"]
                and (not _ws_layering() or _layer == _WS.LAYER_SHARED)):
            continue
        # The community layer is for people who signed in.  The anonymous
        # exhibit stays exactly the passport-filtered shared set it always was.
        if _layer == _WS.LAYER_PUBLISHED and _acc["mode"] == MODE_ANONYMOUS:
            continue
        die = _load_yaml(dd / "die.yaml", "die")
        geo = die.get("geometry") or {}
        if _client_filter and not _die_is_client_ready(geo):
            continue
        # Every stored solver answer this die has, read ONCE — never
        # `duty_results.get()` per configuration, which re-reads the whole JSON
        # of every layer each time (a twelve-configuration die would parse the
        # same file twelve times, on a request the Motors tab makes per render).
        # `_tree_signature` stats the same stores, so the memo above falls the
        # moment a coupled run writes one.
        from motor_ai_sim import duty_results as _dr
        _dr_die = _dr.index(_die_name)
        cfgs = []
        for cf in sorted(dd.glob("*.yaml")):
            if cf.name == "die.yaml":
                continue
            c = _load_yaml(cf, "configuration")
            _dr_cfg = _dr_die.get(cf.stem) or {}
            ov = c.get("geometry_overrides") or {}
            w = c.get("winding") or {}
            _bsig = _build_sig(die, c)
            _role, _role_src = _config_role(c)
            cfgs.append({
                "name": cf.stem,
                # Derived from the duties, not from the creation-time toggle —
                # see `_config_role`.  `role_stored` is kept beside it so a
                # stale yaml value is visible rather than silently replaced.
                "role": _role,
                "role_source": _role_src,
                "role_stored": str(c.get("role") or "motor"),
                "locked": bool(c.get("locked", False)),
                "stack_mm": ov.get("motor_length"),
                # Legacy data guard: an L-named configuration whose stored
                # stack contradicts the name (an L15 holding 13 mm).  New
                # writes can no longer mint these; existing ones get flagged
                # so the catalog shows the lie instead of repeating it.
                "name_stack_mismatch": bool(_lname_fix(cf.stem,
                                                       ov.get("motor_length"))),
                "wire_height_mm": ov.get("wire_height"),
                "wire_width_mm": ov.get("wire_width"),
                "turns": ov.get("num_wires_per_slot"),
                "connection": w.get("connection"),
                "star_delta": w.get("star_delta") or "star",
                "magnet": (c.get("materials") or {}).get("magnet"),
                "steel": (c.get("materials") or {}).get("stator_core"),
                # {} on an ordinary build; a frameless one names the parts the
                # customer supplies, so the catalog row can say so.
                "parts": c.get("parts") or {},
                "build_sig": _bsig,
                "battery": c.get("battery"),
                # The bearings, exactly the way the battery rides here: a
                # property of the MACHINE, so the catalog row, the Mechanical
                # tab and the datasheet all read one yaml.
                "bearings": c.get("bearings"),
                "duties": [
                    {"name": d.get("name"), "mode": d.get("mode", "motor"),
                     "saved_at": d.get("saved_at"),
                     "current_arms": d.get("current_arms"), "rpm": d.get("rpm"),
                     "gamma_deg": d.get("gamma_deg"),
                     # per duty since 2026-09-13; older duties: the run's summary
                     "star_delta": (d.get("star_delta")
                                    or (d.get("summary") or {}).get("star_delta")),
                     "torque_nm": d.get("torque_nm"), "power_kw": d.get("power_kw"),
                     "note": d.get("note", ""), "result": _with_mech_loss(d),
                     # What the machine DOES with this point (S1 / S2 / S3 /
                     # segments) — a handful of numbers, so the catalog row can
                     # say "S3 ED 25 % · 60 s" without a second request.  None
                     # on every duty saved before the cycle existed, which reads
                     # as the continuous duty they were always assumed to be.
                     "duty_cycle": d.get("duty_cycle"),
                     # Every excitation this duty has been run and SAVED at —
                     # descriptions only, never a payload (the tree is fetched
                     # on every catalog render).  Empty list = a duty that has
                     # only ever been saved the way duties always were.
                     "primary_drive": _primary_drive(d),
                     "runs": _run_rows(_bsig, d),
                     # HOW LONG MAY IT RUN (2026-09-17).  Absent — never null —
                     # on a duty with no coupled record, on a record older than
                     # the feature, and on a point inside every limit it has.
                     # `_time_to_limit_row` says why each of those is an answer.
                     **_time_to_limit_kv(
                         (_dr_cfg.get(str(d.get("name") or "")) or {})
                         .get("coupled"))}
                    for d in (c.get("duties") or [])
                ],
            })
        out.append({
            "name": _die_name,
            # Which layer answered, and whose work it is — the web tab that
            # draws a "Community" section is a later stage, but the tree must
            # already say so or the frontend has to guess from the name.  Only
            # with multi-user ON: a single-user tree is byte-identical to
            # Stage 1's, keys included.
            **({"layer": _layer, "owner": _e["owner"] or None,
                "read_only": _layer != _WS.LAYER_WORKSPACE}
               if _ws_layering() else {}),
            "locked": bool(die.get("locked", True)),
            "created": die.get("created"),
            "slots": geo.get("num_slots"), "poles": geo.get("num_poles"),
            "stator_diameter": geo.get("stator_diameter"),
            "thumb_svg": die.get("thumb_svg"),
            "configs": cfgs,
        })
    if _sig:
        _TREE_CACHE[_key] = (_sig, out)
    res = {"dies": list(out), "can_write": _can_write}
    if not out and _acc["mode"] == MODE_GRANTED:
        # An empty page tells a new account nothing.  ONE line, no wall of
        # text — the catalog renders it where "no dies yet" would go.
        res["note"] = "no motors granted yet — ask the vendor"
    return res


# ── die ──────────────────────────────────────────────────────────────────────

@router.post("/die")
def create_die(req: DieCreate, _w: dict = Depends(require_catalog_write)):
    name = _check_name(req.name, "die")
    if _die_file(name).exists():
        raise HTTPException(409, detail=f"die '{name}' already exists — delete it "
                                        "first or pick another name")
    live = _live_cfg()
    geo = _plain(live.get("geometry"))
    if not geo:
        raise HTTPException(500, detail="live config has no geometry block")
    sim = _sim_of(live)
    # The card's picture is the REAL stamped cross-section — same generator the
    # motor catalog uses; None (unbuildable) just means the card falls back to
    # a schematic, never a failed die creation.
    try:
        from motor_ai_sim.routes.presets import _gen_thumb_svg
        thumb = _gen_thumb_svg(dict(geo))
    except Exception:
        thumb = None
    _save_yaml(_die_file(name), {
        "name": name,
        "locked": True,
        "created": datetime.now().isoformat(timespec="seconds"),
        # d-axis is a property of the stamped topology — snapshot the pin.
        "daxis_deg": sim.get("daxis_deg"),
        "thumb_svg": thumb,
        "geometry": dict(geo),
    })
    log.info("family: die '%s' created from the live geometry (%d keys)",
             name, len(geo))
    return {"ok": True, "die": name}


class DieRename(BaseModel):
    name: str


@router.patch("/die/{die}")
def rename_die(die: str, req: DieRename, _w: dict = Depends(require_catalog_write)):
    """Rename a die: the folder moves, die.yaml and every configuration's
    back-reference are updated."""
    die = _check_name(die, "die")
    new = _check_name(req.name, "die")
    _require_die_write(die, _w)
    src = _die_dir(die)
    if not (src / "die.yaml").is_file():
        raise HTTPException(404, detail=f"die '{die}' not found")
    if new == die:
        return {"ok": True, "die": die}
    _require_writable_die(die)
    # The rename stays in the layer that holds the die: a workspace die moves
    # inside the workspace, an admin's ``?layer=shared`` rename inside shared.
    dst = src.parent / new
    if dst.exists() or (_ws_layering() and _ws_resolve_die_dir(new) is not None):
        raise HTTPException(409, detail=f"die '{new}' already exists")
    src.rename(dst)
    d = _load_yaml(dst / "die.yaml", "die")
    d["name"] = new
    _save_yaml(dst / "die.yaml", d)
    for cf in dst.glob("*.yaml"):
        if cf.name == "die.yaml":
            continue
        c = _load_yaml(cf, "configuration")
        c["die"] = new
        _save_yaml(cf, c)
    # Keep the active context pointing at the renamed die (same pattern as the
    # upsert_duty auto-rename).  A stale context made geometry_lock_check 404 on
    # the old path and return None — i.e. renaming the ACTIVE die silently
    # unlocked the live machine until the next activate.
    ctx = _read_ctx() or {}
    if ctx.get("die") == die:
        import json as _json
        ctx["die"] = new
        _ctx_file().write_text(_json.dumps(ctx), encoding="utf-8")
    log.info("family: die '%s' renamed to '%s'", die, new)
    return {"ok": True, "die": new}


@router.delete("/die/{die}")
def delete_die(die: str, force: bool = False, _w: dict = Depends(require_catalog_write)):
    die = _check_name(die, "die")
    _require_die_write(die, _w)
    dd = _die_dir(die)
    if not (dd / "die.yaml").is_file():
        raise HTTPException(404, detail=f"die '{die}' not found")
    _require_writable_die(die)
    cfgs = [p.stem for p in dd.glob("*.yaml") if p.name != "die.yaml"]
    if cfgs and not force:
        raise HTTPException(409, detail=(
            f"die '{die}' still has {len(cfgs)} configuration(s): "
            f"{', '.join(cfgs)} — delete them first (or pass force=true)"))
    for p in dd.glob("*.yaml"):
        p.unlink()
    # …and the stored-run sidecars of every duty that went with them.
    import shutil as _sh
    _sh.rmtree(dd / "runs", ignore_errors=True)
    try:
        dd.rmdir()
    except OSError:
        pass                             # non-yaml leftovers — keep the folder
    log.warning("family: die '%s' deleted (%d configuration(s) with it)",
                die, len(cfgs))
    return {"ok": True, "deleted_configs": cfgs}


# ── configuration ────────────────────────────────────────────────────────────

@router.post("/config")
def create_config(req: ConfigCreate, _w: dict = Depends(require_catalog_write)):
    die = _check_name(req.die, "die")
    name = _check_name(req.name, "configuration")
    _require_die_write(die, _w)
    if not _die_file(die).is_file():
        raise HTTPException(404, detail=f"die '{die}' not found")
    if name == "die":
        raise HTTPException(422, detail="'die' is reserved")
    if _cfg_file(die, name).exists():
        raise HTTPException(409, detail=f"configuration '{name}' already exists "
                                        f"under die '{die}'")
    if req.role not in ("motor", "generator"):
        raise HTTPException(422, detail="role must be 'motor' or 'generator'")
    live = _live_cfg()
    geo = _plain(live.get("geometry")) or {}
    sim = _sim_of(live)
    mat = _plain(live.get("materials")) or {}
    # The name must tell the truth about the build it is being given RIGHT NOW:
    # creating "L15" while the live stack is 13 mm mints a lie the catalog then
    # repeats forever.  The L-number follows the actual stack; the response
    # carries the correction so the UI can show what happened.
    _fixed = _lname_fix(name, geo.get("motor_length"))
    if _fixed:
        if _cfg_file(die, _fixed).exists():
            raise HTTPException(409, detail=(
                f"the live stack is {geo.get('motor_length'):g} mm, so this "
                f"configuration would be named '{_fixed}' — which already "
                f"exists under die '{die}'.  Load that configuration instead, "
                "or change the stack first."))
        log.info("family: configuration name '%s' corrected to '%s' — the "
                 "L-number follows the live stack (%s mm)",
                 name, _fixed, geo.get("motor_length"))
        name = _fixed
    _save_yaml(_cfg_file(die, name), {
        "name": name,
        "die": die,
        "role": req.role,
        "created": datetime.now().isoformat(timespec="seconds"),
        "geometry_overrides": {k: geo.get(k) for k in FREE_GEO_KEYS
                               if geo.get(k) is not None},
        "winding": _plain(live.get("winding")) or {},
        # The build's ACTIVE materials — a configuration is a physical product,
        # and its magnet grade / core steel are part of what was built.
        "materials": {k: mat.get(k)
                      for k in ("magnet", "stator_core", "rotor_core")
                      if mat.get(k)},
        # Per-part ACCOUNTING (included / reference / excluded) is a property
        # of the product too: a frameless configuration ships without a shaft,
        # and that is not an operating condition the Simulation tab owns.
        # Absent when every part is included, so an ordinary build's file is
        # byte-identical to before.
        **({"parts": _live_parts()} if _live_parts() else {}),
        # Coil temperature deliberately NOT snapshotted: it is an operating
        # condition the Simulation tab owns, not a property of the build.
        "end_winding_factor": sim.get("end_winding_factor"),
        "duties": [],
    })
    log.info("family: configuration '%s/%s' created from the live machine",
             die, name)
    return {"ok": True, "die": die, "config": name}


# ── L-name ↔ stack consistency ───────────────────────────────────────────────
# A configuration named "L15" / "G1-L160" declares its stack in the name, and
# the name must TRACK the build (user's rule 2026-08-24: "нужно это
# отслеживать и переименовывать") — an L15 holding a 13 mm build misled for a
# whole session before it was caught by hand.
_LNUM_RE = re.compile(r"(^|[-_ ])L(\d+(?:\.\d+)?)$")


def _lname_fix(name: str, stack) -> Optional[str]:
    """The corrected name when ``name`` ends in L<number> contradicting the
    stack; None when the name is free-form or already truthful."""
    try:
        if stack is None:
            return None
        m = _LNUM_RE.search(str(name))
        if m is None or abs(float(m.group(2)) - float(stack)) < 0.05:
            return None
        return str(name)[:m.start(2)] + ("%g" % float(stack))
    except (TypeError, ValueError):
        return None


class ConfigRename(BaseModel):
    name: str


@router.patch("/config/{die}/{cfg}")
def rename_config(die: str, cfg: str, req: ConfigRename, _w: dict = Depends(require_catalog_write)):
    """Rename a configuration (its file moves with it; duties ride along)."""
    die, cfg = _check_name(die, "die"), _check_name(cfg, "configuration")
    new = _check_name(req.name, "configuration")
    _require_die_write(die, _w)
    if new == "die":
        raise HTTPException(422, detail="'die' is reserved")
    src = _cfg_file(die, cfg)
    if not src.is_file():
        raise HTTPException(404, detail=f"configuration '{die}/{cfg}' not found")
    if new == cfg:
        return {"ok": True, "config": cfg}
    # Copy-on-write BEFORE anything is unlinked or moved: a rename inside a
    # published or shared die happens on this workspace's own copy of it.
    _wdir = _ensure_writable_die(die)
    src = _wdir / src.name
    dst = _wdir / f"{new}.yaml"
    if dst.exists():
        raise HTTPException(409, detail=f"configuration '{new}' already exists "
                                        f"under die '{die}'")
    c = _load_yaml(src, "configuration")
    c["name"] = new
    # The stored-run sidecars live under runs/<configuration>/ — move the
    # folder and repoint every duty's `payload_file`, or the copies would
    # dangle and the PWM chips would open nothing.
    _refile_config_runs(die, c, cfg, new, copy=False)
    _save_yaml(dst, c)
    src.unlink()
    # Keep the active context pointing at the renamed configuration (same
    # pattern as the upsert_duty auto-rename) — a stale context 404s inside
    # geometry_lock_check and silently drops the config lock.
    ctx = _read_ctx() or {}
    if ctx.get("die") == die and ctx.get("config") == cfg:
        import json as _json
        ctx["config"] = new
        _ctx_file().write_text(_json.dumps(ctx), encoding="utf-8")
    log.info("family: configuration '%s/%s' renamed to '%s'", die, cfg, new)
    return {"ok": True, "config": new}


class DieDuplicate(BaseModel):
    name: str                       # the copy's name


@router.post("/die/{die}/duplicate")
def duplicate_die(die: str, req: DieDuplicate,
                  _w: dict = Depends(require_catalog_write)):
    """Copy a WHOLE die: the stamped geometry plus every configuration with
    its duties and recorded results.  The copy starts UNLOCKED (it is a new
    stamp-to-be, free to edit), everything else rides along verbatim."""
    die = _check_name(die, "die")
    new = _check_name(req.name, "die")
    _require_die_write(die, _w)
    src_dir = _die_dir(die)
    if not (src_dir / "die.yaml").is_file():
        raise HTTPException(404, detail=f"die '{die}' not found")
    dst_dir = _die_dir(new)
    if (dst_dir / "die.yaml").exists():
        raise HTTPException(409, detail=f"die '{new}' already exists")
    dst_dir.mkdir(parents=True, exist_ok=True)
    d = _load_yaml(src_dir / "die.yaml", "die")
    d["name"] = new
    d["locked"] = False
    d["created"] = datetime.now().isoformat(timespec="seconds")
    _save_yaml(dst_dir / "die.yaml", d)
    cfgs = []
    for p in sorted(src_dir.glob("*.yaml")):
        if p.name == "die.yaml":
            continue
        c = _load_yaml(p, "configuration")
        c["die"] = new
        _save_yaml(dst_dir / p.name, c)
        cfgs.append(p.stem)
    # The stored runs ride along too: `payload_file` is die-RELATIVE, so the
    # whole tree copies across untouched and every duty of the copy opens its
    # PWM/BLDC results exactly as the original's do.
    if (src_dir / "runs").is_dir():
        import shutil as _sh
        _sh.copytree(src_dir / "runs", dst_dir / "runs", dirs_exist_ok=True)
    log.info("family: die '%s' duplicated as '%s' (%d configuration(s))",
             die, new, len(cfgs))
    return {"ok": True, "die": new, "configs": cfgs}


class ConfigDuplicate(BaseModel):
    name: str                       # the copy's name


@router.post("/config/{die}/{cfg}/duplicate")
def duplicate_config(die: str, cfg: str, req: ConfigDuplicate,
                     _w: dict = Depends(require_catalog_write)):
    """Copy a configuration under the SAME die — build, winding, materials,
    battery and every duty (with its recorded results) ride along.  The copy
    is a starting point for a variant: edit its stack/wire/turns and re-save
    its duties; build_sig staleness flags the duties until they are re-run
    on the copy's own build (they carry the ORIGINAL's signature)."""
    die, cfg = _check_name(die, "die"), _check_name(cfg, "configuration")
    new = _check_name(req.name, "configuration")
    _require_die_write(die, _w)
    if new == "die":
        raise HTTPException(422, detail="'die' is reserved")
    src = _cfg_file(die, cfg)
    if not src.is_file():
        raise HTTPException(404, detail=f"configuration '{die}/{cfg}' not found")
    dst = _cfg_file(die, new)
    if dst.exists():
        raise HTTPException(409, detail=f"configuration '{new}' already exists "
                                        f"under die '{die}'")
    c = _load_yaml(src, "configuration")
    c["name"] = new
    c["created"] = datetime.now().isoformat(timespec="seconds")
    # A duplicate is a FULL copy, no empty cells (the house rule) — the stored
    # PWM/BLDC runs and their sidecars are copied like the duties they belong to.
    _refile_config_runs(die, c, cfg, new, copy=True)
    _save_yaml(dst, c)
    log.info("family: configuration '%s/%s' duplicated as '%s' (%d duty(ies))",
             die, cfg, new, len(c.get("duties") or []))
    return {"ok": True, "config": new,
            "duties": [d.get("name") for d in (c.get("duties") or [])]}


@router.delete("/config/{die}/{cfg}")
def delete_config(die: str, cfg: str, _w: dict = Depends(require_catalog_write)):
    die, cfg = _check_name(die, "die"), _check_name(cfg, "configuration")
    _require_die_write(die, _w)
    p = _cfg_file(die, cfg)
    if not p.is_file():
        raise HTTPException(404, detail=f"configuration '{die}/{cfg}' not found")
    n = len((_load_yaml(p, "configuration").get("duties")) or [])
    # Copy-on-write first: deleting a configuration of a published or shared die
    # removes it from THIS workspace's view, never from the layer that holds it.
    p = _ensure_writable_die(die) / p.name
    if not p.is_file():
        raise HTTPException(404, detail=f"configuration '{die}/{cfg}' not found")
    p.unlink()
    _delete_config_runs(die, cfg)          # its duties' stored runs go with it
    log.warning("family: configuration '%s/%s' deleted (%d duty(ies) with it)",
                die, cfg, n)
    return {"ok": True}


# ── duty ─────────────────────────────────────────────────────────────────────

@router.post("/duty")
def upsert_duty(req: DutyCreate, _w: dict = Depends(require_catalog_write)):
    die = _check_name(req.die, "die")
    cfg = _check_name(req.config, "configuration")
    dname = _check_name(req.duty.name, "duty")
    _require_die_write(die, _w)
    p = _cfg_file(die, cfg)
    c = _load_yaml(p, "configuration")
    d = req.duty
    if d.mode not in ("motor", "generator"):
        raise HTTPException(422, detail="duty mode must be 'motor' or 'generator'")
    _defined_build = False     # set only when this save DEFINES a legacy build
    _build_adopted = False     # set when an UNLOCKED config adopts a re-tune
    if d.from_current:
        live = _live_cfg()
        sim = _sim_of(live)
        if d.current_arms is None:
            d.current_arms = sim.get("current_a", sim.get("max_current"))
        if d.rpm is None:
            d.rpm = sim.get("rpm")
        if d.gamma_deg is None:
            d.gamma_deg = sim.get("gamma_deg", sim.get("phase_offset_deg"))
        # A DUTY has no geometry of its own — the build (stack, wire, turns,
        # winding, materials) belongs to the CONFIGURATION, and every duty of
        # a configuration shares it (user's rule, 2026-08-24: "как могут быть
        # режимы с разной геометрией — это невозможно, только конфигурации").
        # This block used to RE-SNAPSHOT the live build into the configuration
        # on every save — which silently turned an L15 config into a 13 mm one
        # the moment a duty was saved from a 13 mm live machine (live incident
        # 2026-08-23).  Now:
        #   • first save into a FRESH configuration defines its build;
        #   • after that, saving from a live machine whose build DIFFERS is
        #     refused loudly, with the differences named — load the
        #     configuration first, or duplicate it for the new build.
        geo = _plain(live.get("geometry")) or {}
        _live_build = {k: geo.get(k) for k in FREE_GEO_KEYS
                       if geo.get(k) is not None}
        _live_wind = _plain(live.get("winding")) or {}
        mat = _plain(live.get("materials")) or {}
        _live_mats = {k: mat.get(k) for k in _SAVED_MATERIAL_KEYS if mat.get(k)}
        _live_pstates = _live_parts()
        _stored = c.get("geometry_overrides") or None
        if not _stored:
            c["geometry_overrides"] = _live_build
            c["winding"] = _live_wind
            c["materials"] = _live_mats
            if _live_pstates:
                c["parts"] = _live_pstates
            else:
                c.pop("parts", None)
            _defined_build = True
        else:
            _diffs = []
            _free_diffs = []
            for k in FREE_GEO_KEYS:
                sv, lv = _stored.get(k), _live_build.get(k)
                try:
                    if sv is not None and lv is not None:
                        if abs(float(sv) - float(lv)) > 1e-9:
                            _free_diffs.append("%s: %s → %s" % (k, sv, lv))
                    elif sv != lv:
                        _free_diffs.append("%s: %s → %s" % (k, sv, lv))
                except (TypeError, ValueError):
                    if sv != lv:
                        _free_diffs.append("%s: %s → %s" % (k, sv, lv))
            # FREE keys (stack, wire, turns, insulation) are the
            # CONFIGURATION'S OWN parameters — it stores them, so an UNLOCKED
            # configuration must accept them being re-tuned: copy a config,
            # change the stack and the coil, save (user 2026-08-25, after the
            # third refusal: "вся остальная геометрия не менялась").  The
            # refusal exists for the DIE-level cross-section below — that one
            # is the stamped lamination, shared by every configuration.
            # Adopting changes build_sig, so older results correctly flag as
            # "computed on an older build".
            if _free_diffs:
                if c.get("locked") is True:
                    _diffs.extend(_free_diffs)
                else:
                    c["geometry_overrides"] = _live_build
                    _build_adopted = True
                    # A stored end-winding factor belongs to the build it was
                    # measured on: after a stack/wire change it is a different
                    # winding, and keeping the old number silently mis-scales
                    # R, copper loss and copper mass (found live 2026-08-31:
                    # the 200 mm parent's 1.144 rode a 40 mm child whose real
                    # factor is 1.72).  Drop it — absent means "derive from
                    # the geometry", which every consumer already handles.
                    if c.pop("end_winding_factor", None) is not None:
                        log.info("family: '%s/%s' stored end_winding_factor "
                                 "dropped — it was measured on the previous "
                                 "build", die, cfg)
                    log.info("family: configuration '%s/%s' adopted the live "
                             "free-key build on duty save (%s)",
                             die, cfg, "; ".join(_free_diffs))
            # Materials / connection are BUILD properties too, but unlike
            # geometry they have a legitimate edit path right here: an
            # UNLOCKED configuration is a work in progress, and the engineer
            # deliberately re-assigning its steel/magnet then saving IS the
            # update (user 2026-08-25: the refusal left no way to save a
            # material change at all).  The configuration adopts the live
            # materials/connection; its build_sig changes, so every earlier
            # result is automatically flagged "computed on an older build".
            # A LOCKED configuration stays canon — refusal as before.
            _mc_diffs = []
            _sig_before = _build_sig(_die_doc_for_sig(die), c)
            _sw = c.get("winding") or {}
            if str(_sw.get("connection") or "") != str(_live_wind.get("connection") or ""):
                _mc_diffs.append("connection: %s → %s"
                                 % (_sw.get("connection"), _live_wind.get("connection")))
            # EVERY saved material, not the three this loop used to watch
            # (fixed 2026-09-11).  `_SAVED_MATERIAL_KEYS` was widened on
            # 2026-09-09 so a liner, an enamel, a conductor or a shaft grade
            # would live in the configuration — but THIS detector was left on
            # magnet/stator/rotor, and it is the detector that decides whether
            # the configuration adopts the live map at all.  So changing the
            # slot liner produced no diff, nothing was adopted, the
            # configuration kept its three-key map, and the next activation
            # filled the unnamed liner from `_ABSENT_MATERIALS` — Nomex.  The
            # user changed it to Al2O3 four times over four days and watched it
            # come back every time ("уже несколько раз я сохранял изоляцию как
            # Al2O3, но она всё равно всегда сбрасывается в Nomex").
            _sm = c.get("materials") or {}
            _mc_contradicted = False       # a NAMED material actually changed
            for k in _SAVED_MATERIAL_KEYS:
                _sv, _lv = (_sm.get(k) or None), (_live_mats.get(k) or None)
                if _sv != _lv:
                    _mc_diffs.append("%s: %s → %s" % (k, _sv, _lv))
                    if _sv is not None:
                        _mc_contradicted = True
            # Per-part accounting is a build property on the same footing: a
            # configuration whose shaft became `reference` (frameless — the
            # customer brings the shaft) is a different product, and every
            # earlier duty's N·m/kg was billed against a mass that included it.
            _sp = c.get("parts") or {}
            for k in sorted(set(_sp) | set(_live_pstates)):
                if (_sp.get(k) or "included") != (_live_pstates.get(k) or "included"):
                    _mc_diffs.append("part %s: %s → %s"
                                     % (k, _sp.get(k) or "included",
                                        _live_pstates.get(k) or "included"))
            if _mc_diffs:
                if c.get("locked") is True:
                    _diffs.extend(_mc_diffs)
                else:
                    c["winding"] = _live_wind
                    c["materials"] = _live_mats
                    if _live_pstates:
                        c["parts"] = _live_pstates
                    else:
                        c.pop("parts", None)
                    log.info("family: configuration '%s/%s' adopted the live "
                             "build properties on duty save (%s)",
                             die, cfg, "; ".join(_mc_diffs))
                    # ABSENT IS NOT CHANGED (2026-09-11).  When the only thing
                    # that moved is that the configuration started NAMING parts
                    # it never named — the case the widened detector above
                    # created on its first run — the machine is the one every
                    # earlier duty was solved on, and flagging those results
                    # "computed on an older build" is a false alarm the user has
                    # no way to clear except by re-saving work they did not
                    # change ("я же не менял ничего, зачем мне ещё раз всё
                    # пересохранять?").  So their stamps are carried forward.
                    # A material that CONTRADICTED the stored one is a real
                    # change and the flag stands, which is the whole point of it.
                    if not _mc_contradicted:
                        _n = _restamp_results(c, _sig_before,
                                              _build_sig(_die_doc_for_sig(die), c))
                        if _n:
                            log.info("family: %d stored result(s) of '%s/%s' "
                                     "re-stamped — the adopted materials only "
                                     "FILLED keys the configuration had never "
                                     "named, so the build they were computed on "
                                     "is the build it now describes", _n, die, cfg)
            # DIE-level geometry too, not only the free keys.  The hole this
            # closes (lived 2026-08-22→24): a ripple-optimized live machine
            # differed from the die in tooth/magnet SHAPE only — free keys
            # matched, the save passed, the results were recorded, and the
            # catalog quietly kept the wrong machine under an honest-looking
            # duty row.  The die snapshot is the build's identity; a duty may
            # never be saved from a machine the catalog does not describe.
            try:
                _die_geo = (_load_yaml(_die_file(die), "die")
                            .get("geometry") or {})
                for k, dv in _die_geo.items():
                    if k in FREE_GEO_KEYS or not isinstance(dv, (int, float)):
                        continue
                    lv = geo.get(k)
                    if isinstance(lv, (int, float)) and abs(float(dv) - float(lv)) > 1e-6:
                        _diffs.append("die %s: %s → %s" % (k, dv, lv))
            except HTTPException:
                pass
            if _diffs:
                raise HTTPException(409, detail=(
                    "the live machine's build differs from configuration "
                    f"'{cfg}' ({'; '.join(_diffs)}) — a duty cannot carry its "
                    "own geometry.  Load this configuration's build first, or "
                    "duplicate the configuration (or create a new one) for the "
                    "build on screen."))
    missing = [k for k in ("current_arms", "rpm", "gamma_deg")
               if getattr(d, k) is None]
    if missing:
        raise HTTPException(422, detail=(
            f"duty '{dname}' is missing {', '.join(missing)} — give the values "
            "or pass from_current=true with the Simulation tab set up"))
    if not (float(d.current_arms) > 0 and float(d.rpm) > 0):
        raise HTTPException(422, detail="current_arms and rpm must be positive")
    duties = [x for x in (c.get("duties") or []) if x.get("name") != dname]
    prev = next((x for x in (c.get("duties") or []) if x.get("name") == dname), None)
    entry = {"name": dname, "mode": d.mode,
             "saved_at": datetime.now().isoformat(timespec="seconds"),
             "current_arms": round(float(d.current_arms), 3),
             "rpm": round(float(d.rpm), 1),
             "gamma_deg": round(float(d.gamma_deg), 3),
             # Fields NOT sent inherit from the previous entry: re-saving the
             # operating point must not silently erase targets or the note.
             "note": (d.note if d.note is not None
                      else (prev or {}).get("note", "") or "")}
    tq = d.torque_nm if d.torque_nm is not None else (prev or {}).get("torque_nm")
    pw = d.power_kw if d.power_kw is not None else (prev or {}).get("power_kw")
    if tq is not None:
        entry["torque_nm"] = round(float(tq), 2)
    if pw is not None:
        entry["power_kw"] = round(float(pw), 2)
    # The terminal connection this duty was solved with, ON THE DUTY (user
    # 2026-09-13: "не забудь сохранять звезда или треугольник"): the run's
    # own summary says it; a save without a run keeps the previous value,
    # then the configuration's winding.  Read back by ▶ before anything else.
    _sd_src = getattr(d, "summary", None)
    _sd = (_sd_src.get("star_delta") if isinstance(_sd_src, dict) else None) \
        or (prev or {}).get("star_delta") or (c.get("winding") or {}).get("star_delta")
    if _sd:
        entry["star_delta"] = "delta" if str(_sd).lower().startswith("d") else "star"
    # An upsert of the operating point KEEPS a previously recorded result —
    # results die only with the duty (or when overwritten by a new record).
    if prev and prev.get("result"):
        entry["result"] = prev["result"]
    # ── which EXCITATION this save is about ──────────────────────────────────
    # The SINE-current run is the duty's primary result and what the catalog
    # shows.  A PWM (or BLDC, or imposed-voltage) run of the SAME point is
    # remembered under `runs[<drive>]` and must NOT touch the top-level
    # snapshot — overwriting it is what made every later Run a 12-minute PWM
    # solve, because loading the duty restored the PWM settings (2026-09-02).
    # The one exception: a duty that has no primary yet takes whatever it is
    # first given, so nothing is lost on a duty that only ever ran on PWM.
    _drive = _drive_of(d.drive, d.mesh, d.summary)
    _has_primary = bool(prev and (prev.get("summary") or prev.get("mesh")))
    _is_primary = (_drive == "current") or not _has_primary
    # THE DUTY CYCLE, before anything is written: a block naming a duty this
    # configuration does not have (a rename away, a typo) would be accepted
    # here and refused only much later, by the thermal run, with nothing on
    # screen to say why.  Validated against the duty list AS IT WILL BE — this
    # entry included, because an S3 cycle normally names its own duty.
    if d.duty_cycle is not None:
        _names = {str(x.get("name") or ""): x for x in (c.get("duties") or [])
                  if isinstance(x, dict)}
        _names[dname] = entry
        _validate_duty_cycle(d.duty_cycle, _names, duty_name=dname)
    # Complete computed state (mesh + full summary), the duty's own material
    # picks and its duty cycle ride the duty; absent in the request → the
    # previous save's copy survives, same rule as `result`.  MATERIALS and the
    # DUTY CYCLE are not excitation-specific — they are what this duty is made
    # of, and what the machine does with it — so they are written either way.
    for k in ("mesh", "summary", "materials", "duty_cycle"):
        v = getattr(d, k, None)
        if v is not None and (_is_primary or k in ("materials", "duty_cycle")):
            entry[k] = dict(v)
        elif prev and prev.get(k):
            entry[k] = prev[k]
    if d.duty_cycle is not None:
        if d.duty_cycle:
            # WHEN the machine's job was last redefined — the entry's own stamp,
            # so one save is one time, and the server's clock, because the
            # caller's is not the record.
            entry["duty_cycle"]["saved_at"] = entry["saved_at"]
        else:
            # An EMPTY block is the only way to say "no cycle any more": the
            # duty goes back to being the continuous point it was before.
            entry.pop("duty_cycle", None)
    # The per-excitation runs go LAST in the entry — the primary snapshot is
    # what an engineer opening this file is looking for, and it must not be
    # buried under three summaries.
    _prev_runs = dict((prev or {}).get("runs") or {})
    if _prev_runs:
        entry["runs"] = _prev_runs
    if d.mesh is not None or d.summary is not None:
        _store_run(die, cfg, c, entry, _drive,
                   settings=d.mesh, summary=d.summary,
                   assignment_sig=d.assignment_sig)
    duties.append(entry)
    c["duties"] = duties
    # A configuration written before `star_delta` existed names no terminal
    # connection; the live winding this duty was just solved with does.  Fill
    # it ONCE (never overwrite a stated one) so the next ▶ restores the same
    # connection the results were computed with (2026-09-13: the Ø200 peak
    # point ran in star twice because this key was missing).
    try:
        _lw_sd = (_plain(_live_cfg().get("winding")) or {}).get("star_delta")
        if _lw_sd and not (c.get("winding") or {}).get("star_delta"):
            c.setdefault("winding", {})["star_delta"] = str(_lw_sd)
            log.info("family: '%s/%s' winding.star_delta filled from the live "
                     "machine: %s", die, cfg, _lw_sd)
    except Exception:      # noqa: BLE001 — a fill-in must never fail a save
        log.debug("star_delta fill skipped", exc_info=True)
    # The L-number follows the stack whenever this save DEFINED or CHANGED the
    # build (an unlocked configuration adopting a re-tuned stack — see above):
    # an "L12" holding 20 mm is exactly the lie this rename prevents.  Renaming
    # is skipped when the target name is taken; the catalog then shows its
    # "⚠ name ≠ stack" chip instead of silently keeping a wrong name.
    if _defined_build or _build_adopted:
        _fixn = _lname_fix(cfg, (c.get("geometry_overrides") or {}).get("motor_length"))
        if _fixn and not _cfg_file(die, _fixn).exists():
            _oldn, cfg = cfg, _fixn
            c["name"] = cfg
            # The runs folder is named after the configuration — it has to
            # follow the rename, exactly as rename_config does it.
            _refile_config_runs(die, c, _oldn, cfg, copy=False)
            _save_yaml(_cfg_file(die, cfg), c)
            p.unlink()
            ctx = _read_ctx() or {}
            if ctx.get("die") == die and ctx.get("config") == _oldn:
                import json as _json
                ctx["config"] = cfg
                _ctx_file().write_text(_json.dumps(ctx), encoding="utf-8")
            log.info("family: configuration '%s/%s' renamed to '%s' — the "
                     "L-number follows the defined stack", die, _oldn, cfg)
            log.info("family: duty '%s/%s/%s' saved (%s, %.1f Arms @ %.0f rpm, γ=%.2f°)",
                     die, cfg, dname, d.mode, d.current_arms, d.rpm, d.gamma_deg)
            return {"ok": True, "config": cfg, "renamed_to": cfg,
                    "drive": _drive, "primary": _is_primary}
    _save_yaml(p, c)
    log.info("family: duty '%s/%s/%s' saved (%s, %.1f Arms @ %.0f rpm, γ=%.2f°, "
             "%s run, %s)", die, cfg, dname, d.mode, d.current_arms, d.rpm,
             d.gamma_deg, _drive,
             "primary" if _is_primary else "stored beside the primary")
    # `drive` / `primary` let the caller say WHERE the save went instead of a
    # bare tick: a PWM save that quietly left the sine numbers standing is
    # correct but indistinguishable from one that did nothing.
    return {"ok": True, "config": cfg, "drive": _drive, "primary": _is_primary}


@router.post("/duty_result")
def record_duty_result(req: DutyResult, _w: dict = Depends(require_catalog_write)):
    """Attach a finished run's numbers to a duty.  The frontend verifies the
    run WAS at this duty's operating point before calling; this endpoint only
    validates shape and stores."""
    die = _check_name(req.die, "die")
    cfg = _check_name(req.config, "configuration")
    _require_die_write(die, _w)
    p = _cfg_file(die, cfg)
    c = _load_yaml(p, "configuration")
    found = next((x for x in (c.get("duties") or [])
                  if x.get("name") == req.duty), None)
    if found is None:
        raise HTTPException(404, detail=f"duty '{req.duty}' not found in {die}/{cfg}")
    res = {}
    for k in _RESULT_KEYS:
        v = req.result.get(k)
        if v is None:
            continue
        try:
            res[k] = round(float(v), 3)
        except (TypeError, ValueError):
            raise HTTPException(422, detail=f"result field '{k}' is not a number: {v!r}")
    if not res:
        raise HTTPException(422, detail=(
            "empty result — expected at least one of: " + ", ".join(_RESULT_KEYS)))
    res["recorded_at"] = datetime.now().isoformat(timespec="seconds")
    try:
        res["build_sig"] = _build_sig(_load_yaml(_die_file(die), "die"), c)
    except HTTPException:
        pass
    # Same split as the duty save: the headline numbers the catalog row shows
    # belong to the SINE run, and a PWM/BLDC result is recorded under its own
    # excitation without disturbing them.  A duty with no result yet takes the
    # first one it is given, whatever ran it.
    _drive = _drive_of(req.drive)
    if _drive == "current" or not found.get("result"):
        found["result"] = res
        _primary = True
    else:
        _primary = False
    _store_run(die, cfg, c, found, _drive, result=res,
               assignment_sig=req.assignment_sig)
    _save_yaml(p, c)
    # The per-duty store keeps a POINTER at the electromagnetic column, never a
    # copy of it (2026-09-09): the numbers above live in the configuration yaml,
    # which is the one place a catalogue answer may live, and a second copy able
    # to drift from it is worth less than nothing.  What is recorded here is
    # that the column exists, when it was saved and on which build — enough for
    # the report's Sources page to date it beside the thermal and mechanical
    # ones.
    if _primary:
        try:
            from motor_ai_sim import duty_results as _dr
            _dr.note_em_pointer(die, cfg, req.duty, res.get("recorded_at"),
                                res.get("build_sig"))
        except Exception:  # noqa: BLE001 — bookkeeping never fails a save
            log.debug("family: EM pointer not recorded for %s/%s/%s",
                      die, cfg, req.duty, exc_info=True)
    log.info("family: %s result recorded on '%s/%s/%s' (%s): %s",
             _drive, die, cfg, req.duty,
             "primary" if _primary else "beside the primary", res)
    return {"ok": True, "drive": _drive, "primary": _primary}


@router.post("/duty_run")
def record_duty_run(req: DutyRunSave, _w: dict = Depends(require_catalog_write)):
    """File a finished run's WAVEFORMS under one excitation of one duty.

    The yaml keeps a description (when, on which build, with which materials,
    its settings and summary); the transient itself — ~0.7 MB of per-step
    series — goes into a gzip sidecar beside the die, because a catalog file
    an engineer cannot open in an editor stops being a catalog."""
    die = _check_name(req.die, "die")
    cfg = _check_name(req.config, "configuration")
    _require_die_write(die, _w)
    drive = _run_drive(req.drive)
    p = _cfg_file(die, cfg)
    c = _load_yaml(p, "configuration")
    entry = next((x for x in (c.get("duties") or [])
                  if x.get("name") == req.duty), None)
    if entry is None:
        raise HTTPException(404, detail=f"duty '{req.duty}' not found in {die}/{cfg}")
    if req.payload is not None:
        import json as _json
        n = len(_json.dumps(req.payload, default=str).encode("utf-8"))
        if n > _RUN_MAX_BYTES:
            raise HTTPException(413, detail=(
                f"the run payload is {n / 1e6:.1f} MB — the limit is "
                f"{_RUN_MAX_BYTES / 1e6:.0f} MB.  Drop the per-element field "
                f"arrays ({', '.join(_RUN_HEAVY_KEYS)}) before sending: no "
                "chart reads them and they are what makes a run enormous."))
    rec = _store_run(die, cfg, c, entry, drive,
                     settings=req.settings, summary=req.summary,
                     result=req.result, assignment_sig=req.assignment_sig,
                     payload=req.payload)
    _save_yaml(p, c)
    log.info("family: %s run stored on '%s/%s/%s' -> %s",
             drive, die, cfg, req.duty, rec.get("payload_file") or "(no payload)")
    return {"ok": True, "drive": drive,
            "payload_file": rec.get("payload_file"),
            "primary": drive == _primary_drive(entry)}


@router.get("/duty_runs/{die}/{cfg}/{duty}")
def get_duty_runs(die: str, cfg: str, duty: str,
                  authorization: str = Header(default=None)):
    """EVERY stored run of one duty, payloads and all — the single call ▶
    makes.

    One request instead of one per excitation: the Simulation panel's run
    selector then switches between sine / PWM / BLDC with no network and no
    solve at all, which is the whole point of remembering them.  Three runs of
    a 48-step transient are ~2 MB gunzipped — the same order as the duty
    payload this sits beside."""
    die, cfg = _check_name(die, "die"), _check_name(cfg, "configuration")
    _require_die_access(die, authorization)
    d = _load_yaml(_die_file(die), "die")
    c = _load_yaml(_cfg_file(die, cfg), "configuration")
    entry = next((x for x in (c.get("duties") or [])
                  if x.get("name") == duty), None)
    if entry is None:
        raise HTTPException(404, detail=f"duty '{duty}' not found in {die}/{cfg}")
    bsig = _build_sig(d, c)
    prim = _primary_drive(entry)
    out = []
    for drive in _RUN_DRIVES:
        r = (entry.get("runs") or {}).get(drive)
        if not isinstance(r, dict):
            continue
        rel = r.get("payload_file")
        try:
            payload = _read_run_payload(die, str(rel)) if rel else None
        except HTTPException:
            # One corrupt sidecar must not cost the user the other runs.
            log.exception("family: stored run %s/%s/%s/%s is unreadable",
                          die, cfg, duty, drive)
            payload = None
        out.append({
            "drive": drive, "primary": drive == prim,
            "recorded_at": r.get("recorded_at"),
            "build_sig": r.get("build_sig"),
            "assignment_sig": r.get("assignment_sig"),
            "stale": bool(r.get("build_sig") and r.get("build_sig") != bsig),
            "settings": r.get("settings") or {},
            "summary": r.get("summary"), "result": r.get("result"),
            "payload": payload,
        })
    return {"die": die, "config": cfg, "duty": duty,
            "primary_drive": prim, "build_sig": bsig, "runs": out}


@router.get("/duty_run/{die}/{cfg}/{duty}/{drive}")
def get_duty_run(die: str, cfg: str, duty: str, drive: str,
                 authorization: str = Header(default=None)):
    """A stored run, whole: its panel settings, its summary, its recorded
    numbers and the gunzipped waveforms.  Loading this is what makes a
    twelve-minute PWM solve a click instead of a wait."""
    die, cfg = _check_name(die, "die"), _check_name(cfg, "configuration")
    _require_die_access(die, authorization)
    drive = _run_drive(drive)
    d = _load_yaml(_die_file(die), "die")
    c = _load_yaml(_cfg_file(die, cfg), "configuration")
    entry = next((x for x in (c.get("duties") or [])
                  if x.get("name") == duty), None)
    if entry is None:
        raise HTTPException(404, detail=f"duty '{duty}' not found in {die}/{cfg}")
    r = (entry.get("runs") or {}).get(drive)
    if not isinstance(r, dict):
        raise HTTPException(404, detail=(
            f"duty '{duty}' has no stored '{drive}' run in {die}/{cfg} — load "
            "it, run the point on that source and save"))
    rel = r.get("payload_file")
    payload = _read_run_payload(die, str(rel)) if rel else None
    bsig = _build_sig(d, c)
    return {
        "die": die, "config": cfg, "duty": duty, "drive": drive,
        "recorded_at": r.get("recorded_at"),
        "build_sig": r.get("build_sig"), "assignment_sig": r.get("assignment_sig"),
        "stale": bool(r.get("build_sig") and r.get("build_sig") != bsig),
        "settings": r.get("settings") or {},
        "summary": r.get("summary"), "result": r.get("result"),
        # None = the description outlived its sidecar (deleted by hand): the
        # settings still restore, the charts stay empty and say why.
        "payload": payload,
    }


class DutyDuplicate(BaseModel):
    name: str


@router.post("/duty/{die}/{cfg}/{duty}/duplicate")
def duplicate_duty(die: str, cfg: str, duty: str, req: DutyDuplicate,
                   _w: dict = Depends(require_catalog_write)):
    """Copy a duty VERBATIM under a new name — operating point, targets, note
    AND the recorded result all ride along (the house rule: a duplicate is a
    full copy, no empty cells).  A duty carries no geometry, so within one
    configuration the copy is exactly as valid as the original; only the
    saved_at stamp is fresh, marking when the copy was made."""
    die, cfg = _check_name(die, "die"), _check_name(cfg, "configuration")
    _require_die_write(die, _w)
    new = str(req.name or "").strip()
    if not new:
        raise HTTPException(422, detail="give the copy a name")
    p = _cfg_file(die, cfg)
    c = _load_yaml(p, "configuration")
    duties = c.get("duties") or []
    src = next((x for x in duties if x.get("name") == duty), None)
    if src is None:
        raise HTTPException(404, detail=f"duty '{duty}' not found in {die}/{cfg}")
    if any(x.get("name") == new for x in duties):
        raise HTTPException(409, detail=f"duty '{new}' already exists in "
                                        f"{die}/{cfg} — pick another name")
    import copy as _copy
    entry = _copy.deepcopy(src)
    entry["name"] = new
    entry["saved_at"] = datetime.now().isoformat(timespec="seconds")
    # A full copy means the STORED RUNS too — their sidecars are copied under
    # the new duty's own file names, so deleting either duty later cannot take
    # the other one's PWM result with it.
    _move_duty_runs(die, entry, cfg, new, copy=True)
    duties.append(entry)
    c["duties"] = duties
    _save_yaml(p, c)
    log.info("family: duty '%s/%s/%s' duplicated as '%s' (result %s, %d stored run(s))",
             die, cfg, duty, new, "copied" if entry.get("result") else "empty",
             len(entry.get("runs") or {}))
    return {"ok": True, "duty": new,
            "result_copied": bool(entry.get("result")),
            "runs_copied": sorted((entry.get("runs") or {}).keys())}


@router.patch("/duty/{die}/{cfg}/{duty}")
def rename_duty(die: str, cfg: str, duty: str, req: DutyDuplicate,
                _w: dict = Depends(require_catalog_write)):
    """Rename a duty in place — everything else (operating point, targets,
    note, recorded result) stays untouched.  Born of a live typo ('peal' for
    'peak'): a name slip must be a two-click fix, not delete-and-redo that
    would throw the recorded result away."""
    die, cfg = _check_name(die, "die"), _check_name(cfg, "configuration")
    _require_die_write(die, _w)
    new = str(req.name or "").strip()
    if not new:
        raise HTTPException(422, detail="give the duty a name")
    p = _cfg_file(die, cfg)
    c = _load_yaml(p, "configuration")
    duties = c.get("duties") or []
    src = next((x for x in duties if x.get("name") == duty), None)
    if src is None:
        raise HTTPException(404, detail=f"duty '{duty}' not found in {die}/{cfg}")
    if new != duty and any(x.get("name") == new for x in duties):
        raise HTTPException(409, detail=f"duty '{new}' already exists in "
                                        f"{die}/{cfg} — pick another name")
    # The sidecar file names are derived from the duty name — move them with it
    # (a rename that orphaned the PWM payload would be a silent data loss).
    _move_duty_runs(die, src, cfg, new, copy=False)
    src["name"] = new
    # Every duty cycle that NAMED this duty follows the rename — its own, and
    # any other duty resting at it.  A cycle left pointing at a name that no
    # longer exists would be refused on the next save with a message about a
    # duty the user never touched, which is the silent-data-loss shape a rename
    # is supposed to be free of (the same reason the run sidecars move above).
    _n = _repoint_duty_cycles(duties, duty, new)
    if _n:
        log.info("family: %d duty-cycle reference(s) re-pointed from '%s' to "
                 "'%s' in %s/%s", _n, duty, new, die, cfg)
    _save_yaml(p, c)
    # the active context may point at this duty by its old name
    ctx = _read_ctx() or {}
    if (ctx.get("die") == die and ctx.get("config") == cfg
            and ctx.get("duty") == duty):
        import json as _json
        ctx["duty"] = new
        _ctx_file().write_text(_json.dumps(ctx), encoding="utf-8")
    # …AND SO DO THE DUTY'S ANSWERS (2026-09-16).  Both stores address a duty
    # by its NAME — `duty_results` by the key itself, `duty_fields` by the
    # folder the name hashes to — and this route moved neither, so a rename
    # silently orphaned every thermal, mechanical, coupled and duty-cycle row
    # and all four stored maps.  Found on CIANO10 200 opt / L180 gen, where
    # 'rated 0.5x9 mm' → 'rated 1x9 mm' carried the two run payloads and left
    # the results keyed under the old name and the em / thermal / rotor_stress
    # / modes npz in the old duty's folder: the next report would have printed
    # "not solved for this duty" over solves that were still on disk.
    n_res = n_fields = 0
    if new != duty:
        try:
            from motor_ai_sim import duty_results as _dr
            n_res = int(bool(_dr.rename(die, cfg, duty, new)))
        except Exception:  # noqa: BLE001 — bookkeeping never fails a rename
            log.debug("family: per-duty results not carried for %s/%s/%s",
                      die, cfg, duty, exc_info=True)
        try:
            from motor_ai_sim import duty_fields as _df
            n_fields = _df.rename(die, cfg, duty, new)
        except Exception:  # noqa: BLE001 — bookkeeping never fails a rename
            log.debug("family: per-duty fields not carried for %s/%s/%s",
                      die, cfg, duty, exc_info=True)
    log.info("family: duty '%s/%s/%s' renamed to '%s' (%d result row(s), "
             "%d stored field file(s) carried)", die, cfg, duty, new,
             n_res, n_fields)
    return {"ok": True, "duty": new, "results_carried": bool(n_res),
            "fields_carried": n_fields}


@router.delete("/duty/{die}/{cfg}/{duty}")
def delete_duty(die: str, cfg: str, duty: str, _w: dict = Depends(require_catalog_write)):
    die, cfg = _check_name(die, "die"), _check_name(cfg, "configuration")
    _require_die_write(die, _w)
    p = _cfg_file(die, cfg)
    c = _load_yaml(p, "configuration")
    before = c.get("duties") or []
    after = [x for x in before if x.get("name") != duty]
    if len(after) == len(before):
        raise HTTPException(404, detail=f"duty '{duty}' not found in {die}/{cfg}")
    # Its stored runs go with it — an orphan sidecar nothing references is
    # dead weight in a folder the user has to be able to reason about.
    n_runs = sum(_delete_duty_runs(die, x) for x in before
                 if x.get("name") == duty)
    c["duties"] = after
    _save_yaml(p, c)
    # …and so do its stored thermal / mechanical / coupled summaries: a duty
    # that no longer exists must not keep a column in the next report, and a
    # duty later re-created under the same name is a NEW operating point, not
    # the old one's results (2026-09-09).
    try:
        from motor_ai_sim import duty_results as _dr
        _dr.forget(die, cfg, duty)
    except Exception:  # noqa: BLE001 — bookkeeping never fails a delete
        log.debug("family: per-duty results not cleared for %s/%s/%s",
                  die, cfg, duty, exc_info=True)
    # …and its stored FIELDS (2026-09-09): a duty's maps are ~1 MB of npz under
    # `runs/<cfg>/<duty>/fields/`, and a folder of maps nothing references any
    # more is exactly the graveyard the size discipline in `duty_fields` exists
    # to prevent.
    n_fields = 0
    try:
        from motor_ai_sim import duty_fields as _df
        n_fields = _df.drop(die, cfg, duty)
    except Exception:  # noqa: BLE001 — bookkeeping never fails a delete
        log.debug("family: per-duty fields not cleared for %s/%s/%s",
                  die, cfg, duty, exc_info=True)
    log.warning("family: duty '%s/%s/%s' deleted (%d stored run file(s), "
                "%d stored field file(s) removed)", die, cfg, duty,
                n_runs, n_fields)
    return {"ok": True, "runs_deleted": n_runs, "fields_deleted": n_fields}


# ── locks & the active family context ────────────────────────────────────────
# WHICH die/configuration the live machine currently is — written when a duty
# is loaded, read by the geometry route to enforce the locks.  A sidecar file,
# not motor_config.yaml, so this layer still never rewrites the live config.
# Per WORKSPACE since Stage 1 (with none set: the folder it always was).  The
# name stays readable for the completeness test and for the two modules that
# monkeypatch it.
def _ctx_file() -> Path:
    _ov = globals().get("_CTX_FILE")
    if _ov is not None:
        return Path(str(_ov))
    from motor_ai_sim.workspace import root as _ws_root
    return _ws_root() / ".family_context.json"


def __getattr__(name):
    _r = {"_DIES_DIR": _dies_dir, "_CTX_FILE": _ctx_file}.get(name)
    if _r is None:
        raise AttributeError(name)
    return _r()


def _read_ctx() -> Optional[dict]:
    try:
        import json
        d = json.loads(_ctx_file().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) and d.get("die") else None
    except Exception:
        return None


def release_context(reason: str) -> Optional[str]:
    """No die is active any more.  ONE writer for the released state — used by
    /deactivate (Compare, My motors), the die identity guard (a foreign machine
    reached the geometry save) and the preset/classic-catalog load — so the
    header strip and every context reader see the same story: which die was
    released and why.  Returns the die that was active, or None."""
    ctx = _read_ctx() or {}
    import json as _json
    _ctx_file().write_text(_json.dumps({
        "die": None, "config": None, "duty": None,
        "released_from": ctx.get("die"),
        "reason": reason,
        "at": datetime.now().isoformat(timespec="seconds")}), encoding="utf-8")
    log.info("family: context released (%s) — was '%s/%s'",
             reason, ctx.get("die"), ctx.get("config"))
    return ctx.get("die")


def sync_active_die_geometry(saved_geo: dict,
                             prev_geo: Optional[dict] = None) -> Optional[str]:
    """Refresh the ACTIVE die's geometry snapshot after a geometry save.

    die.yaml's geometry was written ONCE — at creation (or copied verbatim by
    duplicate_die) — and nothing ever updated it, while an UNLOCKED die is by
    definition a work in progress whose geometry the user is editing live.  The
    snapshot then went stale, and everything derived from it lied at once: the
    catalog grouped the die under the wrong Ø (a reworked 85 mm die stayed
    filed under "Ø 100 mm" — live 2026-08-22), `_build_sig` judged duty
    staleness against the wrong machine, and applying a duty could resurrect
    the pre-rework cross-section.

    Called by the geometry PUT after a SUCCESSFUL save.  Rules:
      • no active context, or the die is LOCKED → do nothing (a locked die's
        geometry is canon by decree; the lock check already refused non-
        canonical edits, so there is nothing to sync).
      • unlocked → overwrite the full geometry snapshot with the saved config's
        and refresh the card thumbnail (the card must show the machine the die
        IS, not the one it was duplicated from).
    Returns the die name when a sync happened (for the caller's log), else None.
    """
    ctx = _read_ctx()
    if not ctx:
        return None
    die = str(ctx.get("die") or "")
    try:
        d = _load_yaml(_die_file(die), "die")
    except HTTPException:
        return None                        # stale context — die is gone
    if bool(d.get("locked", True)):
        return None
    geo = {k: v for k, v in dict(saved_geo or {}).items() if v is not None}
    if not geo:
        return None
    # ── The identity guard (unconditional) ───────────────────────────────────
    # A die's DIAMETER and slot/pole topology never change by editing — they
    # are what makes it this die.  A save that moves them is a foreign machine
    # by definition (a catalog/preset load landing while this die's context
    # was still active), whatever `prev_geo` says or does not say.  Measured
    # 2026-09-01 22:33:59: the stranger guard below did not fire and the 85 mm
    # die '20SW1200' was overwritten with a 200 mm motor — the catalog then
    # filed it under Ø 200 and its three duties "vanished" for the user.
    _die_g0 = d.get("geometry") or {}
    _ident = []
    for k in ("stator_diameter", "num_seg", "num_slots_per_segment",
              "num_poles_per_segment"):
        dv, sv = _die_g0.get(k), geo.get(k)
        if (isinstance(dv, (int, float)) and isinstance(sv, (int, float))
                and abs(float(dv) - float(sv)) > 1e-6):
            _ident.append(f"{k} {dv} → {sv}")
    if _ident:
        log.warning(
            "family: REFUSED to sync die '%s' — the saved machine is not this "
            "die (%s). A die keeps its diameter and topology for life; load "
            "the other motor into its own die. The die snapshot is untouched.",
            die, "; ".join(_ident))
        # The live editor no longer holds this die's machine — a whole-machine
        # load (Compare apply, My motors, a preset, the classic catalog) went
        # through a path that never calls /activate.  Leaving the context
        # pointing at the old die is what made the header strip lie
        # ("CIANO28 85 … / L13 (L40)" over a 200 mm G2-L40 live machine,
        # 2026-09-01 22:33) and sent the overnight charging study to the wrong
        # motor.  Drop it: no die is active until a load says which one.
        try:
            release_context("live machine is not this die: " + "; ".join(_ident))
        except Exception as _ce:   # noqa: BLE001 — never fail the save
            log.warning("family: could not release the context: %s", _ce)
        return None
    # ── The stranger guard ───────────────────────────────────────────────────
    # Sync follows EDITS of this die's own machine — it never adopts a foreign
    # one.  A save whose PRE-save live machine does not match the die's
    # snapshot (die-level keys; free keys legitimately differ per config) is a
    # context race — a stale debounced PUT landing right after a die switch —
    # and adopting it is exactly how the ripple-optimized 150 was destroyed
    # (incident 2026-08-24).  Refuse loudly instead.
    if prev_geo:
        _die_g = d.get("geometry") or {}
        _foreign = []
        for k, dv in _die_g.items():
            if k in FREE_GEO_KEYS or not isinstance(dv, (int, float)):
                continue
            pv = prev_geo.get(k)
            if isinstance(pv, (int, float)) and abs(float(dv) - float(pv)) > 1e-6:
                _foreign.append(k)
        if _foreign:
            log.warning(
                "family: REFUSED to sync die '%s' — the machine being saved "
                "did not originate from this die (pre-save live differs in: "
                "%s). The die snapshot is untouched.",
                die, ", ".join(sorted(_foreign)[:8]))
            return None
    d["geometry"] = geo
    try:
        from motor_ai_sim.routes.presets import _gen_thumb_svg
        thumb = _gen_thumb_svg(dict(geo))
        if thumb:
            d["thumb_svg"] = thumb
    except Exception:                      # thumbnail is cosmetic — never fatal
        pass
    _save_yaml(_die_file(die), d)
    log.info("family: die '%s' geometry snapshot synced from the live save "
             "(%d keys, Ø %s mm)", die, len(geo), geo.get("stator_diameter"))
    return die


class Activate(BaseModel):
    die: str
    config: str
    duty: Optional[str] = None


@router.post("/activate")
def activate(req: Activate, _w: dict = Depends(require_catalog_write)):
    """Mark die/config/duty as the machine now loaded in the editor.  Admin
    only since multi-user deploy: this stamps the SHARED .family_context.json
    (the owner's live editor state).  An ordinary user's ▶ loads the duty as
    a CLIENT-SIDE copy and never calls this."""
    die = _check_name(req.die, "die")
    cfg = _check_name(req.config, "configuration")
    _require_die_write(die, _w)
    d = _load_yaml(_die_file(die), "die")
    c = _load_yaml(_cfg_file(die, cfg), "configuration")
    import json
    _ctx_file().write_text(
        json.dumps({"die": die, "config": cfg, "duty": req.duty,
                    "at": datetime.now().isoformat(timespec="seconds")}),
        encoding="utf-8")
    # The duty's MESH fidelity block goes to the server's `mesh:` config too
    # (2026-09-09).  A browser adopts that block ONCE at boot
    # (web/src/lib/meshConfigSync.ts) — so a duty loaded through the API or
    # picked up by the follower left the server saying "4 sectors, 2.97 mm"
    # from the Ø200 while the 40 mm was live, and the user's next boot solved
    # a 12/14 machine with n_sectors = 4 ("n_sectors=4 is not a symmetry of
    # this machine").  The duty's block is the machine's own statement of how
    # it is run; the Mesh tab's later saves still overwrite it as before.
    mesh_synced = _sync_mesh_config_from_duty(c, req.duty)
    return {"ok": True, "die_locked": bool(d.get("locked", True)),
            "config_locked": bool(c.get("locked", False)),
            "mesh_synced": mesh_synced}


#: The duty block's panel keys → the server mesh config's keys, and their types.
_DUTY_MESH_TO_CONFIG = (("mesh.meshSize", "mesh_size_mm", float),
                        ("mesh.minSize", "min_size_mm", float),
                        ("mesh.outerAir", "outer_air_factor", float),
                        ("mesh.gapLayers", "gap_layers", float),
                        ("mesh.nSectors", "n_sectors", int))


def duty_mesh_patch(duty: Optional[dict]) -> Dict[str, Any]:
    """The server mesh-config patch a duty's `mesh` block amounts to — only the
    five fidelity keys the browser's boot sync reads, only when the block
    carries a finite number for them.  Pure, so it is pinned by a test."""
    m = (duty or {}).get("mesh") if isinstance(duty, dict) else None
    if not isinstance(m, dict):
        return {}
    out: Dict[str, Any] = {}
    for src, dst, typ in _DUTY_MESH_TO_CONFIG:
        v = m.get(src)
        if v is None or v == "" or isinstance(v, bool):
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f != f or f in (float("inf"), float("-inf")):
            continue
        out[dst] = typ(round(f)) if typ is int else float(f)
    return out


def _sync_mesh_config_from_duty(c: dict, duty_name: Optional[str]) -> bool:
    """Write the named duty's mesh fidelity keys into the server mesh config.

    Never fails the activation: a mesh block that cannot be written (a config
    mid-write, a read-only file) is logged and reported as ``False`` — the
    context is stamped either way, exactly as it was before this existed.
    """
    if not duty_name:
        return False
    duty = next((x for x in (c.get("duties") or [])
                 if isinstance(x, dict) and x.get("name") == duty_name), None)
    patch = duty_mesh_patch(duty)
    if not patch:
        return False
    try:
        from motor_ai_sim.api import MeshConfigPatch, update_mesh_config
        update_mesh_config(MeshConfigPatch(**patch))
        return True
    except Exception as exc:  # noqa: BLE001 — the context stamp must not depend on it
        log.warning("family: activate could not sync the duty's mesh block "
                    "into the mesh config: %s", exc)
        return False


class Deactivate(BaseModel):
    reason: Optional[str] = None


@router.post("/deactivate")
def deactivate(req: Deactivate, _w: dict = Depends(require_catalog_write)):
    """No die is active any more — the editor is about to hold a machine that
    is not a catalog duty (a Compare row, a private "my motor" copy, a preset).

    The ONE rule that ends the clobber family for good: only ▶ (activate +
    apply) may make a die active; every other whole-machine load releases the
    context FIRST.  With no active die there is nothing for the geometry save
    to sync into, so a foreign machine can never be written over a die — not
    even one that happens to share its diameter and topology (a modified
    private copy of the same 85 mm, which the identity guard cannot tell
    apart).  The header strip then says "no die active" instead of lying.
    """
    was = release_context(req.reason or "whole-machine load outside the catalog")
    return {"ok": True, "released_from": was}


@router.get("/context")
def context(response: Response, authorization: str = Header(default=None)):
    """WHAT is loaded in the editor right now — die / configuration / duty —
    plus the duty's stored operating point so the header strip can flag when
    the panel has drifted off it."""
    who = caller_identity(authorization)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Vary"] = "Authorization"
    ctx = _read_ctx()
    if not ctx:
        # A RELEASED context says why (identity guard or an explicit
        # deactivate) so the strip can show "no die active" honestly instead
        # of vanishing without a word.
        _rel = {}
        try:
            import json as _json
            _raw = _json.loads(_ctx_file().read_text(encoding="utf-8"))
            if isinstance(_raw, dict) and _raw.get("released_from"):
                _rel = {"released_from": _raw.get("released_from"),
                        "reason": _raw.get("reason"), "at": _raw.get("at")}
        except Exception:   # noqa: BLE001 — no file, no story
            pass
        return {"active": False, "can_write": _can_write_catalog(who), **_rel}
    die, cfg = str(ctx.get("die") or ""), str(ctx.get("config") or "")
    duty = ctx.get("duty")
    # A die the caller was not granted does not exist for them — including in
    # the "what is loaded" strip, which would otherwise name it and hand out
    # its build and operating point.
    if not may_see_die(catalog_access(authorization), die):
        return {"active": False, "can_write": _can_write_catalog(who)}
    try:
        d = _load_yaml(_die_file(die), "die")
        c = _load_yaml(_cfg_file(die, cfg), "configuration")
    except HTTPException:
        return {"active": False, "can_write": _can_write_catalog(who),
                "note": "context points at a deleted die/configuration"}
    point = None
    if duty:
        point = next((x for x in (c.get("duties") or [])
                      if x.get("name") == duty), None)
    return {
        "active": True, "die": die, "config": cfg, "duty": duty,
        "die_locked": bool(d.get("locked", True)),
        "config_locked": bool(c.get("locked", False)),
        "can_write": _can_write_catalog(who),
        # The keys a die-lock leaves editable — ONE source of truth for the
        # Geometry tab's read-only greying (must match the PUT guard).
        "free_keys": list(EDITABLE_UNDER_DIE_LOCK),
        # The supply this machine is designed around.  The Simulation tab's PWM
        # source prefills V_bus from it: a DC-link voltage typed by hand is a
        # number nobody checks against the pack that is actually there, and the
        # ripple a PWM run reports scales directly with it.
        "battery": c.get("battery"),
        # The bearings this machine is built with — the input to the SKF
        # frictional-moment model behind the "Bearings" and "Windage" cells on
        # the Electromagnetic summary and the mechanical rows in the datasheet.
        # Absent = not decided yet, which is NOT the same as zero loss.
        "bearings": c.get("bearings"),
        "duty_point": (None if point is None else {
            "current_arms": point.get("current_arms"), "rpm": point.get("rpm"),
            "gamma_deg": point.get("gamma_deg"), "mode": point.get("mode", "motor"),
        }),
        # The SAVED build — the strip compares the live geometry against it
        # and shows the effective name (M1-L220, amber) while unsaved.
        "build": {
            "stack_mm": (c.get("geometry_overrides") or {}).get("motor_length"),
            "wire_height_mm": (c.get("geometry_overrides") or {}).get("wire_height"),
            "turns": (c.get("geometry_overrides") or {}).get("num_wires_per_slot"),
        },
    }


class LockPatch(BaseModel):
    locked: bool


@router.patch("/die/{die}/lock")
def lock_die(die: str, req: LockPatch, _w: dict = Depends(require_catalog_write)):
    die = _check_name(die, "die")
    _require_die_write(die, _w)
    d = _load_yaml(_die_file(die), "die")
    d["locked"] = bool(req.locked)
    _save_yaml(_die_file(die), d)
    log.info("family: die '%s' %s", die, "locked" if req.locked else "UNLOCKED")
    return {"ok": True, "locked": bool(req.locked)}


@router.patch("/config/{die}/{cfg}/lock")
def lock_config(die: str, cfg: str, req: LockPatch,
                _w: dict = Depends(require_catalog_write)):
    die, cfg = _check_name(die, "die"), _check_name(cfg, "configuration")
    _require_die_write(die, _w)
    c = _load_yaml(_cfg_file(die, cfg), "configuration")
    c["locked"] = bool(req.locked)
    _save_yaml(_cfg_file(die, cfg), c)
    log.info("family: configuration '%s/%s' %s", die, cfg,
             "locked" if req.locked else "UNLOCKED")
    return {"ok": True, "locked": bool(req.locked)}


class BatteryPatch(BaseModel):
    chemistry: Optional[str] = None  # "NMC" | "LiFePO4" | free text
    cells: int                       # series cell count
    v_cell_min: float                # per-cell, discharged [V]
    v_cell_nom: Optional[float] = None
    v_cell_max: float                # per-cell, fully charged [V]
    # ── CHARGE SIDE (2026-09-01) ─────────────────────────────────────────
    # A pack that only knows its voltages answers "does the inverter have
    # headroom?" and nothing else.  Charging THROUGH the bridge asks how many
    # amps go in, at what C-rate, and how far the bus rises while they do —
    # every one of which is a resistance or a capacity.  All optional: a PATCH
    # that omits them keeps whatever the yaml holds, and a pack that has never
    # been told them gets the chemistry placeholder (simulation/battery.py),
    # flagged as a placeholder wherever it is shown.
    n_parallel: Optional[int] = None       # parallel strings (NP), default 1
    r_int_mohm: Optional[float] = None     # PER CELL [mΩ]
    capacity_ah: Optional[float] = None    # per string [Ah]
    i_charge_max_a: Optional[float] = None  # pack charge limit [A]; default 1 C


@router.patch("/config/{die}/{cfg}/battery")
def set_battery(die: str, cfg: str, req: BatteryPatch,
                _w: dict = Depends(require_catalog_write)):
    """The supply the configuration is built to run from — given per CELL
    (chemistry, series count, min/nom/max cell voltage); pack totals are
    derived and stored alongside.  Duties are judged against the pack: the
    inverter needs V_DC >= the duty's line peak.

    The charge-side fields (internal resistance, capacity, charge-current
    limit) are what a GENERATOR duty needs: with the machine feeding this pack
    through the bridge, V_bus = V_oc + I_charge·R_pack, and the C-rate is the
    number that says whether the pack will accept what the machine can make."""
    die, cfg = _check_name(die, "die"), _check_name(cfg, "configuration")
    _require_die_write(die, _w)
    if not (req.cells >= 1):
        raise HTTPException(422, detail=f"cell count must be >= 1, got {req.cells}")
    if not (req.v_cell_max >= req.v_cell_min > 0):
        raise HTTPException(422, detail=(
            f"cell voltage range is nonsense: min={req.v_cell_min}, "
            f"max={req.v_cell_max} — need 0 < min <= max"))
    nom = req.v_cell_nom if req.v_cell_nom is not None else None
    if nom is not None and not (req.v_cell_min <= nom <= req.v_cell_max):
        raise HTTPException(422, detail=(
            f"nominal cell voltage {nom} is outside [{req.v_cell_min}, "
            f"{req.v_cell_max}]"))
    for _f, _v in (("n_parallel", req.n_parallel), ("r_int_mohm", req.r_int_mohm),
                   ("capacity_ah", req.capacity_ah),
                   ("i_charge_max_a", req.i_charge_max_a)):
        if _v is not None and not (float(_v) >= (1 if _f == "n_parallel" else 0)):
            raise HTTPException(422, detail=(
                f"battery.{_f} must be "
                + ("at least 1" if _f == "n_parallel" else "non-negative")
                + f"; got {_v}"))
    c = _load_yaml(_cfg_file(die, cfg), "configuration")
    _prev = dict(c.get("battery") or {})
    n = int(req.cells)

    def _keep(field, val):
        """A PATCH that omits a charge-side field keeps what the yaml holds.
        Sending it as null is the only way to clear it back to the placeholder
        — silently resetting a measured r_int because the dialog did not carry
        the field would be the worst kind of substitution."""
        return _prev.get(field) if val is None else val

    c["battery"] = {
        "chemistry": (req.chemistry or "").strip() or None,
        "cells": n,
        "v_cell_min": round(float(req.v_cell_min), 3),
        "v_cell_nom": (round(float(nom), 3) if nom is not None else None),
        "v_cell_max": round(float(req.v_cell_max), 3),
        # pack totals — what the duty voltage check reads
        "v_min": round(n * float(req.v_cell_min), 1),
        "v_nom": (round(n * float(nom), 1) if nom is not None else None),
        "v_max": round(n * float(req.v_cell_max), 1),
    }
    # Charge side: written ONLY when there is something to write, so a pack
    # nobody has given these to keeps a yaml block byte-identical to the one it
    # has always had — and reads back through the placeholder path.
    for _f, _v in (("n_parallel", _keep("n_parallel", req.n_parallel)),
                   ("r_int_mohm", _keep("r_int_mohm", req.r_int_mohm)),
                   ("capacity_ah", _keep("capacity_ah", req.capacity_ah)),
                   ("i_charge_max_a", _keep("i_charge_max_a", req.i_charge_max_a))):
        if _v is None:
            continue
        c["battery"][_f] = (int(_v) if _f == "n_parallel"
                            else round(float(_v), 4))
    _save_yaml(_cfg_file(die, cfg), c)
    log.info("family: battery on '%s/%s': %s", die, cfg, c["battery"])
    return {"ok": True, "battery": c["battery"]}


class BearingEnd(BaseModel):
    """One end of the shaft line."""
    card: Optional[str] = None       # a name in config/bearings_library.yaml
    seals: Optional[str] = None      # override the card's seal type, if this build differs
    grease: Optional[str] = None     # override the card's default lubricant


class BearingsPatch(BaseModel):
    A: Optional[BearingEnd] = None
    B: Optional[BearingEnd] = None
    lubrication: Optional[str] = None      # 'grease' | 'oil_air'
    preload_n: Optional[float] = None      # axial preload per bearing [N]
    temp_source: Optional[str] = None      # 'manual' | 'thermal'
    temp_c: Optional[float] = None         # bearing temperature when manual


@router.patch("/config/{die}/{cfg}/bearings")
def set_bearings(die: str, cfg: str, req: BearingsPatch,
                 _w: dict = Depends(require_catalog_write)):
    """The BEARINGS this configuration is built with — the mechanical half of
    its loss picture.

    Modelled on ``set_battery`` above and stored the same way, because a bearing
    is the same kind of fact as a battery: a property of the MACHINE, not of a
    panel.  Until this existed the bearing span, the overhangs and the bearing
    stiffness lived in the Mechanical tab as ASSUMED panel values, and the
    datasheet said outright that "bearing and windage losses are not included" —
    which on the real 150 mm free run meant leaving out the single largest loss
    below 3000 rpm.

    The block is the input to the SKF frictional-moment model
    (``motor_ai_sim.bearings``): two cards, how they are lubricated, the axial
    preload, and where the bearing temperature comes from.  Nothing here solves
    anything; ``GET /api/bearings/losses`` reads it back and does the arithmetic.

    A PATCH replaces the whole block — a bearing pair is one decision, and a
    half-updated pair (a new A against a stale B) is a shaft line nobody drew.
    """
    die, cfg = _check_name(die, "die"), _check_name(cfg, "configuration")
    _require_die_write(die, _w)

    lub = (req.lubrication or "grease").strip()
    if lub not in ("grease", "oil_air"):
        raise HTTPException(422, detail=(
            f"lubrication must be 'grease' or 'oil_air'; got '{req.lubrication}'"))
    tsrc = (req.temp_source or "manual").strip()
    if tsrc not in ("manual", "thermal"):
        raise HTTPException(422, detail=(
            f"temp_source must be 'manual' or 'thermal'; got '{req.temp_source}'"))
    if req.preload_n is not None and float(req.preload_n) < 0:
        raise HTTPException(422, detail=(
            f"bearings.preload_n must be non-negative; got {req.preload_n}"))
    if req.temp_c is not None and not (-60.0 <= float(req.temp_c) <= 400.0):
        raise HTTPException(422, detail=(
            f"bearings.temp_c is outside anything a bearing survives: "
            f"{req.temp_c} degC"))

    # A card that is not in the library is refused HERE, at the save, rather
    # than at the first loss request: a die file naming a bearing nobody has is
    # a machine whose losses can never be computed, and it would be discovered
    # weeks later.
    from motor_ai_sim import bearings as _brg
    ends: Dict[str, Any] = {}
    for _end, _spec in (("A", req.A), ("B", req.B)):
        if _spec is None or not (_spec.card or "").strip():
            continue
        name = _spec.card.strip()
        try:
            _brg.get_bearing(name)
        except _brg.UnknownBearingError as exc:
            raise HTTPException(422, detail=str(exc))
        except FileNotFoundError as exc:
            raise HTTPException(500, detail=str(exc))
        e: Dict[str, Any] = {"card": name}
        if (_spec.seals or "").strip():
            e["seals"] = _spec.seals.strip()
        if (_spec.grease or "").strip():
            g = _spec.grease.strip()
            try:
                _brg.get_lubricant(g)
            except _brg.UnknownBearingError as exc:
                raise HTTPException(422, detail=str(exc))
            e["grease"] = g
        ends[_end] = e

    if not ends:
        # Clearing is a real intention (a machine whose bearings are not decided
        # yet), and it must leave NO block rather than an empty one that reads
        # as "assigned, zero loss".
        c = _load_yaml(_cfg_file(die, cfg), "configuration")
        c.pop("bearings", None)
        _save_yaml(_cfg_file(die, cfg), c)
        log.info("family: bearings CLEARED on '%s/%s'", die, cfg)
        return {"ok": True, "bearings": None}

    block: Dict[str, Any] = dict(ends)
    block["lubrication"] = lub
    block["preload_n"] = round(float(req.preload_n or 0.0), 3)
    block["temp_source"] = tsrc
    if req.temp_c is not None:
        block["temp_c"] = round(float(req.temp_c), 1)

    c = _load_yaml(_cfg_file(die, cfg), "configuration")
    c["bearings"] = block
    _save_yaml(_cfg_file(die, cfg), c)
    log.info("family: bearings on '%s/%s': %s", die, cfg, block)
    return {"ok": True, "bearings": block}


def _num_eq(a, b) -> bool:
    try:
        fa, fb = float(a), float(b)
        return abs(fa - fb) <= 1e-9 * max(1.0, abs(fa), abs(fb))
    except (TypeError, ValueError):
        return a == b


def geometry_lock_check(update: dict) -> Optional[dict]:
    """Called by the geometry PUT route.  Returns a refusal detail when the
    ACTIVE die/configuration locks forbid this update, else None.

    Lock levels (user-defined):
      die+config locked -> geometry untouchable (only Simulation params move);
      die locked only   -> only the FREE keys (stack length, wire, turns) move;
      nothing locked    -> everything moves.
    A locked key still ACCEPTS its canonical value — that is how loading the
    configuration itself passes."""
    ctx = _read_ctx()
    if not ctx:
        return None
    die, cfg = str(ctx.get("die") or ""), str(ctx.get("config") or "")
    try:
        d = _load_yaml(_die_file(die), "die")
    except HTTPException:
        return None                       # stale context — die is gone
    try:
        c = _load_yaml(_cfg_file(die, cfg), "configuration")
    except HTTPException:
        c = {}
    die_locked = bool(d.get("locked", True))
    cfg_locked = bool(c.get("locked", False))
    if not die_locked and not cfg_locked:
        return None
    die_geo = d.get("geometry") or {}
    ov = c.get("geometry_overrides") or {}
    bad = []
    for k, v in update.items():
        free = k in EDITABLE_UNDER_DIE_LOCK
        if free:
            if not cfg_locked:
                continue                  # die-lock alone leaves free keys open
            canon = ov.get(k, die_geo.get(k))
            lock_name = f"configuration '{cfg}'"
        else:
            if not die_locked:
                continue
            # THE CONFIGURATION'S OVERRIDE IS CANONICAL TOO (fixed 2026-09-11).
            # A die lock stops the DIE's geometry being edited; it was reading
            # the die's value as the only value a locked key may hold, which
            # made a configuration unloadable the moment it overrode anything
            # outside the three free keys.  On the Ø200 the die still carries
            # the old 4.5 x 1 mm strip winding and every L180 configuration
            # overrides it with 9 x 0.5 mm: loading one was refused key by key
            # (423), the live machine kept the DIE's wire, and the user got a
            # panel showing "wire split 2 / parallel 3" for a machine wound
            # 1 x 4 ("откуда здесь взялось wire split 2?").  An override is a
            # stored property of the configuration, not an edit — loading it is
            # exactly what the lock is meant to keep working.  A THIRD value,
            # typed by hand, is still refused.
            canon = ov.get(k, die_geo.get(k))
            lock_name = f"die '{die}'"
        if canon is None:
            # A key the stamped file never carried has a canonical value all
            # the same — what its absence MEANS (no sleeve, one wire in hand):
            # the value `/payload` serves for it, so loading the configuration
            # itself keeps passing (2026-09-08: a sleeveless locked die refused
            # its own load with "sleeve_thickness locked by die", value null).
            canon = _ABSENT_MEANS.get(k)
        if canon is not None and _num_eq(v, canon):
            continue                      # writing the canonical value is a no-op
        bad.append({"field": k, "reason": f"locked by {lock_name}",
                    "locked_value": canon})
    if not bad:
        return None
    lvl = ("die and configuration are locked — geometry is read-only, only "
           "Simulation parameters may change"
           if (die_locked and cfg_locked) else
           "die is locked — only stack length, wire size and turn count may change")
    return {
        "error": "geometry is locked",
        "die": die, "config": cfg,
        "die_locked": die_locked, "config_locked": cfg_locked,
        "invalid_parameters": bad,
        "hint": lvl + ". Unlock in the Motors catalog (admin) or load another configuration.",
    }


# ── per-duty solver results ──────────────────────────────────────────────────

@router.get("/duty_results/{die}/{cfg}")
def duty_results(die: str, cfg: str, response: Response,
                 authorization: str = Header(default=None)):
    """WHICH simulations each duty of this configuration has an answer for.

    Added 2026-09-09 with the full report.  The electromagnetic side has always
    been per duty — it is saved into the configuration yaml from the Simulation
    tab — but the thermal map, the rotor stress, the critical speeds and the
    coupled loop are stored ONCE PER MACHINE, for whichever duty was solved
    last.  ``motor_ai_sim.duty_results`` now files a compact copy of each of
    those under the duty the catalog context named at the time, and this is the
    read-only view of that store: a duty with no entry for a kind has not been
    solved for it, which is a different statement from "zero".

    READ ONLY, and cheap: a few dozen scalars per duty, no meshes, no fields.
    Nothing is solved and nothing is written by this endpoint.
    """
    from motor_ai_sim import duty_results as dr

    die, cfg = _check_name(die, "die"), _check_name(cfg, "configuration")
    _require_die_access(die, authorization)
    response.headers["Cache-Control"] = "no-store"
    c = _load_yaml(_cfg_file(die, cfg), "configuration")
    stored = dr.get(die, cfg)
    ctx = dr.active_context()
    duties = []
    for d in (c.get("duties") or []):
        if not isinstance(d, dict):
            continue
        name = str(d.get("name") or "")
        e = stored.get(name) or {}
        duties.append({
            "duty": name,
            "mode": d.get("mode"),
            "rpm": d.get("rpm"),
            "torque_nm": d.get("torque_nm"),
            "power_kw": d.get("power_kw"),
            "current_arms": d.get("current_arms"),
            "gamma_deg": d.get("gamma_deg"),
            # The electromagnetic column is the duty's OWN saved block; it is
            # named here, never copied, so the catalogue stays the one answer.
            "em_saved_at": (d.get("result") or {}).get("recorded_at")
                           or d.get("saved_at"),
            "kinds": dr.kinds_present({**e, "em": {"source": "yaml"}
                                       if d.get("summary") or d.get("result")
                                       else None}),
            "results": e,
        })
    return {"die": die, "config": cfg, "duties": duties,
            "store": str(dr.store_path()),
            "active_duty": (ctx[2] if (ctx and ctx[0] == die and ctx[1] == cfg)
                            else None)}


@router.get("/duty_fields/{die}/{cfg}")
def duty_fields(die: str, cfg: str, response: Response,
                authorization: str = Header(default=None)):
    """WHICH duties of this configuration have a stored FIELD, and how big it is.

    The array half of ``/duty_results`` (2026-09-09, user: *"давай сделаем
    сохранение всех полей моделирования, как электромагнитных, так и тепловых и
    механических"*).  ``motor_ai_sim.duty_fields`` keeps each duty's own mesh and
    map arrays under ``<die>/runs/<configuration>/<duty>/fields/<kind>.npz``, so
    a report can draw four duties' temperature maps side by side instead of the
    last-solved one four times.

    A LISTING, never the arrays: kinds, byte sizes, the stamp each field was
    solved at and the geometry fingerprint it was solved on.  The arrays are tens
    of thousands of elements each and belong in the document builder, which reads
    them off the disk itself; shipping them through JSON to answer "what is
    stored?" is the mistake ``/api/thermal/last?field=false`` exists to avoid.
    Nothing is solved and nothing is written here.
    """
    from motor_ai_sim import duty_fields as dfl

    die, cfg = _check_name(die, "die"), _check_name(cfg, "configuration")
    _require_die_access(die, authorization)
    response.headers["Cache-Control"] = "no-store"
    # The configuration must EXIST — same gate as /duty_results.  A listing that
    # answered "nothing stored" for a mistyped name would read as "solve it
    # again", which is the wrong instruction and an expensive one.
    _load_yaml(_cfg_file(die, cfg), "configuration")
    stored = dfl.have(die, cfg)
    ctx = dfl.active_context()
    return {
        "die": die, "config": cfg,
        "duties": [{"duty": duty,
                    "kinds": [r["kind"] for r in rows],
                    "bytes": sum(int(r.get("bytes") or 0) for r in rows),
                    "fields": rows}
                   for duty, rows in sorted(stored.items())],
        "kinds": list(dfl.KINDS),
        "root": str(dfl._dies_dir() / die / "runs" / cfg),
        "active_duty": (ctx[2] if (ctx and ctx[0] == die and ctx[1] == cfg)
                        else None),
    }


# ── datasheet export ─────────────────────────────────────────────────────────

@router.get("/datasheet/{die}/{cfg}")
def datasheet(die: str, cfg: str, authorization: str = Header(default=None)):
    """The configuration as a spreadsheet anyone can read: one column per duty,
    then the design and battery blocks, a detail sheet and a page explaining
    what every number means.

    .xlsx — Google Sheets opens it natively (upload → Open with Sheets), as do
    Excel and LibreOffice.  Built from what is already stored, so it costs no
    solve time and writes nothing."""
    from fastapi.responses import Response

    die, cfg = _check_name(die, "die"), _check_name(cfg, "configuration")
    _require_die_access(die, authorization)
    d = _load_yaml(_die_file(die), "die")
    c = _load_yaml(_cfg_file(die, cfg), "configuration")
    if not (c.get("duties") or []):
        raise HTTPException(400, detail=(
            f"{die} / {cfg} has no saved duty yet — run the point in Simulation "
            "and save it to a duty first, otherwise the datasheet would be empty"))
    # the measured curves live on the catalog card of the same machine, if it
    # has one — the card is named "<die> <configuration>"
    passport = None
    try:
        import json
        cat_path = _ws_root_f() / "motor_catalog.json"
        cards = json.loads(cat_path.read_text(encoding="utf-8")).get("motors", [])
        want = f"{die} {cfg}".casefold()
        hit = next((m for m in cards
                    if str(m.get("name") or "").casefold() == want), None)
        if hit is None:
            same = [m for m in cards
                    if str(m.get("name") or "").casefold().startswith(die.casefold())
                    and (m.get("passport") or {}).get("passport")]
            hit = same[0] if len(same) == 1 else None
        if hit and (hit.get("passport") or {}).get("passport"):
            # a card of the same die is only THIS configuration's card if the
            # build matches — otherwise its curves belong to another machine
            p = hit["passport"]["passport"]
            g = dict((d.get("geometry") or {}))
            g.update(c.get("geometry_overrides") or {})
            same = (abs(float(p.get("L0_mm") or 0) - float(g.get("motor_length") or 0)) < 1e-6
                    and abs(float(p.get("N0") or 0)
                            - float(g.get("num_wires_per_slot") or 0)) < 1e-6)
            if same:
                passport = dict(hit["passport"])
                # the angle the passport was measured at — the datasheet's
                # calculator has to say that it cannot vary it
                gam = hit.get("gamma_deg")
                if gam is None:                    # family cards carry it on the preset
                    try:
                        from motor_ai_sim.routes.presets import _load_presets
                        _p = (_load_presets().get(hit.get("preset") or "") or {})
                        gam = (_p.get("simulation") or {}).get("phase_offset_deg")
                    except Exception:              # noqa: BLE001
                        gam = None
                passport["gamma_deg"] = gam
    except Exception:                                         # noqa: BLE001
        passport = None                      # curves are a bonus, never a blocker

    try:
        from motor_ai_sim.datasheet import build_datasheet
        # wire coating is pure geometry — measured here (cached), no FEM
        try:
            from motor_ai_sim.masses import slot_fill_from_cad
            _geo = dict(d.get("geometry") or {})
            _geo.update(c.get("geometry_overrides") or {})
            _slot = slot_fill_from_cad(_geo)
        except Exception:                                     # noqa: BLE001
            _slot = None
        blob = build_datasheet(die=die, cfg=cfg, die_doc=d, cfg_doc=c,
                               passport=passport, slot=_slot)
    except Exception as e:                                    # noqa: BLE001
        log.exception("datasheet build failed for %s/%s", die, cfg)
        raise HTTPException(500, detail=f"datasheet build failed: {e}")
    fname = f"{die} {cfg} datasheet.xlsx".replace('"', "")
    return Response(
        content=blob,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"',
                 "Content-Length": str(len(blob))})


# ── apply payload ────────────────────────────────────────────────────────────

@router.get("/payload/{die}/{cfg}")
def payload(die: str, cfg: str, duty: Optional[str] = None,
            authorization: str = Header(default=None)):
    """Everything the frontend needs to APPLY a configuration (and optionally a
    duty) through the EXISTING endpoints: merged geometry, winding, sim values.
    Nothing is written here."""
    die, cfg = _check_name(die, "die"), _check_name(cfg, "configuration")
    _require_die_access(die, authorization)
    d = _load_yaml(_die_file(die), "die")
    c = _load_yaml(_cfg_file(die, cfg), "configuration")
    geo = dict(d.get("geometry") or {})
    # A null override is an override that says nothing — the die's value stands
    # (older saves wrote `wire_parallel: null` for "not set").
    geo.update({k: v for k, v in (c.get("geometry_overrides") or {}).items()
                if v is not None})
    # A key the die never carried is a key the frontend's PUT leaves at the
    # PREVIOUS machine's value — PUT /api/geometry merges over what is loaded.
    # Eleven of thirteen dies predate `sleeve_thickness`, so loading any of
    # them from a sleeved Ø200 kept its 2.5 mm band and the PUT was refused
    # ("not thinner than air_gap (0.65 mm)" — user 2026-09-08: "хочу загрузить
    # G2-L40, а он не грузится"); a stray `wire_parallel: 3` would likewise have
    # turned a 21-turn machine into a 7-turn one without a word.  Say what
    # absence MEANS, explicitly, so the payload describes the whole machine.
    for _k, _v in _ABSENT_MEANS.items():
        if geo.get(_k) is None:
            geo[_k] = _v
    # The same rule for the materials: a part the configuration does not name
    # is the project's standard for it, said explicitly, so the load replaces
    # whatever the previous machine left in the shared assignment.
    mats = {k: v for k, v in (c.get("materials") or {}).items() if v}
    # A part the configuration does not name, but the DUTY being loaded does,
    # is the duty's (2026-09-11).  Every duty saved since 2026-09-09 carries the
    # full material map it was solved with, so this reads the choice back on
    # machines whose configuration block was written before the detector above
    # was fixed — without rewriting a single catalog file.  Only then does the
    # project's standard fill what is still unnamed.
    if duty:
        _d = next((x for x in (c.get("duties") or [])
                   if isinstance(x, dict) and str(x.get("name") or "") == duty),
                  None)
        for _k, _v in ((_d or {}).get("materials") or {}).items():
            if _v and not mats.get(_k):
                mats[_k] = _v
                log.info("family: '%s/%s' does not name %s — taken from duty "
                         "'%s' (%s)", die, cfg, _k, duty, _v)
    for _k, _v in _ABSENT_MATERIALS.items():
        if not mats.get(_k):
            mats[_k] = _v
    poles = int(geo.get("num_poles") or 0)
    out = {
        "die": die, "config": cfg,
        "geometry": geo,
        "winding": c.get("winding") or {},
        "materials": mats,
        # Per-part accounting the configuration was built and characterised
        # under — applied the same way its materials are, so a frameless
        # configuration loads frameless instead of silently regrowing a shaft.
        "parts": c.get("parts") or {},
        "sim": {
            "end_winding_factor": c.get("end_winding_factor"),
            "daxis_deg": d.get("daxis_deg"),
            "connection": (c.get("winding") or {}).get("connection"),
            # The terminal connection: the configuration's winding block, else
            # the DUTY's own saved panel setting, and "star" only when neither
            # says.  A configuration written before `star_delta` existed
            # carried no key, so every ▶ reset the panel to star — the Ø200's
            # peak point ran twice in star on 2026-09-13 (586.9 A in the
            # winding instead of 338.85, copper ×3, 272 °C, loop refused).
            "star_delta": _star_delta_of(c, duty),
        },
    }
    if duty is not None:
        found = next((x for x in (c.get("duties") or [])
                      if x.get("name") == duty), None)
        if found is None:
            raise HTTPException(404, detail=f"duty '{duty}' not found in {die}/{cfg}")
        rpm = float(found.get("rpm") or 0)
        # A VERBATIM copy of the stored entry, which is how the duty's own
        # `materials:` dict (its per-duty material picks — DutySpec above)
        # reaches the frontend: `out["duty"]["materials"]` is the DUTY's partial
        # override, `out["materials"]` above is the CONFIGURATION's assignment.
        # Two different things at two different levels — do not merge them here.
        # `duty_cycle` rides along the same way, unchanged: ▶ restores the cycle
        # the duty was defined with, not whatever the last panel held.
        out["duty"] = dict(found)
        # The stored per-excitation runs are NOT part of the apply payload:
        # they have their own endpoint (/duty_runs) which gunzips the
        # waveforms with them, and duplicating their settings + summaries here
        # would put tens of kilobytes nobody reads on every ▶.
        out["duty"].pop("runs", None)
        # A Stage-A 3D passport measured AFTER this duty was recorded: the
        # stored summary froze end3d=null at solve time.  The last-transient
        # restore already heals that case (_refresh_summary_shape's
        # passport-arrived clause) — stored duties must not lag behind it, so
        # the CURRENT configuration geometry's passport is attached at serve
        # time, same shape as the live summary builder.  Nothing is written;
        # the yaml keeps its honest null.
        try:
            _summ = found.get("summary")
            # v12-era snapshots carry a PM-only saturation reference whose
            # droop can be negative (koef > 100 %); the koef is a saturation
            # measure and is clamped to <= 100 on every serve path.
            if isinstance(_summ, dict):
                _sat = _summ.get("saturation")
                if (isinstance(_sat, dict)
                        and float(_sat.get("droop_pct") or 0) < 0):
                    _summ = dict(_summ)
                    _summ["saturation"] = {**_sat, "droop_pct": 0.0}
                    out["duty"]["summary"] = _summ
            if isinstance(_summ, dict) and _summ.get("end3d") is None:
                from motor_ai_sim.routes.simulation import (_end3d_lookup,
                                                            _geometry_fingerprint)
                _e3 = _end3d_lookup(_geometry_fingerprint(geo), geo)
                if _e3:
                    _s2 = dict(_summ)
                    _T = _s2.get("T_em_avg_Nm")
                    _V = _s2.get("V_line_peak_V")
                    _s2["end3d"] = {
                        **_e3,
                        "T_corrected_Nm": (round(float(_T) * float(_e3["k_flux"]), 3)
                                           if _T is not None else None),
                        "V_line_peak_corrected_V": (round(float(_V) * float(_e3["k_flux"]), 2)
                                                    if _V else None),
                    }
                    out["duty"]["summary"] = _s2
        except Exception:   # noqa: BLE001 — no passport is the normal state
            pass
        out["sim"].update({
            "mode": found.get("mode", "motor"),
            "current_a": found.get("current_arms"),
            "rpm": rpm,
            "gamma_deg": found.get("gamma_deg"),
            "frequency": round(rpm * (poles / 2) / 60.0, 2) if poles else None,
        })
    return out


# ═══════════════════════════════════════════════════════════════════════════
#  CONFIGURATION HISTORY — see it, roll back to it
# ═══════════════════════════════════════════════════════════════════════════
# `_save_yaml` has snapshotted every changed save into
# config/dies/.history/<die>/<file>.<stamp>.yaml since 2026-08-24 (30 newest
# per file); what was missing was a way to LOOK at those snapshots and put one
# back without a file manager (user 2026-09-12: "история сохранённых параметров
# по каждой конфигурации, чтобы в любой момент можно было откатиться —
# геометрия и режимы для моделирования").  A snapshot IS exactly that: the
# configuration file holds geometry_overrides, winding, materials and the
# duties with their operating points and solver settings; the die file holds
# the base geometry.
#
# Restoring goes through `_save_yaml`, so the version being replaced is
# snapshotted first — a rollback is itself undoable.  Results (`runs/`, the
# per-duty store) are NOT touched: a restored configuration whose duties carry
# a result stamped on a different build shows the mismatch like any stale row.

def _hist_dir(die: str) -> Path:
    return _dies_dir() / ".history" / die


def _hist_stem(die: str, cfg: Optional[str]) -> str:
    return "die" if not cfg else str(cfg)


def _hist_parse_stamp(name: str, stem: str) -> Optional[str]:
    """'<stem>.<YYYYMMDD-HHMMSS>[.note].yaml' -> the stamp, or None."""
    if not name.startswith(stem + ".") or not name.endswith(".yaml"):
        return None
    st = name[len(stem) + 1:-5].split(".", 1)[0]
    ok = (len(st) == 15 and st[8] == "-" and st.replace("-", "").isdigit())
    return st if ok else None


def _hist_essentials(d: dict, is_die: bool) -> dict:
    """The part of a snapshot a person compares by eye."""
    if is_die:
        g = dict(d.get("geometry") or {})
        keep = ("stator_diameter", "motor_length", "num_slots", "num_poles",
                "air_gap", "magnet_height", "sleeve_thickness", "core_thickness",
                "tooth_width", "slot_height", "slot_width", "wire_width",
                "wire_height", "num_wires_per_slot", "wire_parallel", "wire_split")
        return {"geometry": {k: g.get(k) for k in keep if g.get(k) is not None},
                "materials": dict(d.get("materials") or {})}
    duties = []
    for du in (d.get("duties") or []):
        if isinstance(du, dict):
            duties.append({"name": du.get("name"),
                           "current_arms": du.get("current_arms"),
                           "rpm": du.get("rpm"), "gamma_deg": du.get("gamma_deg"),
                           "mode": du.get("mode")})
    return {"geometry_overrides": dict(d.get("geometry_overrides") or {}),
            "winding": dict(d.get("winding") or {}),
            "materials": dict(d.get("materials") or {}),
            "duties": duties}


def _hist_diff(a: dict, b: dict) -> List[str]:
    """Flat 'key: now -> snapshot' lines between two essentials dicts."""
    out: List[str] = []

    def walk(x, y, pfx):
        if isinstance(x, dict) or isinstance(y, dict):
            x = x if isinstance(x, dict) else {}
            y = y if isinstance(y, dict) else {}
            for k in sorted(set(x) | set(y)):
                walk(x.get(k), y.get(k), pfx + str(k) + ".")
        elif isinstance(x, list) or isinstance(y, list):
            # duties: pair by NAME so a row reads "duties.peak.current_arms:
            # 634 -> 687", not two whole lists side by side
            x = x if isinstance(x, list) else []
            y = y if isinstance(y, list) else []
            key = lambda e, i: (str(e.get("name")) if isinstance(e, dict)
                                and e.get("name") is not None else "#%d" % i)
            dx = {key(e, i): e for i, e in enumerate(x)}
            dy = {key(e, i): e for i, e in enumerate(y)}
            for k in list(dx) + [k for k in dy if k not in dx]:
                if k not in dy:
                    out.append("%s%s: present -> removed" % (pfx, k))
                elif k not in dx:
                    out.append("%s%s: absent -> added" % (pfx, k))
                else:
                    walk(dx[k], dy[k], pfx + k + ".")
        elif x != y:
            out.append("%s: %s -> %s" % (pfx[:-1], x, y))
    walk(a, b, "")
    return out


@router.get("/history/{die}")
def configuration_history(die: str, config: Optional[str] = None,
                          authorization: Optional[str] = Header(None)):
    """Snapshots of one configuration (or of the die's base geometry when
    `config` is omitted), newest first, each with the lines that differ from
    what is on disk NOW — a row reads "wire_height: 0.5 -> 1.0", not just a
    timestamp."""
    _require_die_access(die, authorization)
    is_die = config is None
    stem = _hist_stem(die, config)
    cur_p = _die_file(die) if is_die else _cfg_file(die, config)
    cur = (_hist_essentials(_load_yaml(cur_p, "die" if is_die else "configuration"),
                            is_die) if cur_p.exists() else {})
    rows = []
    hdir = _hist_dir(die)
    if hdir.is_dir():
        for f in sorted(hdir.iterdir(), reverse=True):
            st = _hist_parse_stamp(f.name, stem)
            if not st:
                continue
            try:
                d = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            except Exception:      # noqa: BLE001
                continue
            ess = _hist_essentials(d, is_die)
            rows.append({
                "stamp": st,
                "taken": "%s-%s-%s %s:%s:%s" % (st[:4], st[4:6], st[6:8],
                                                st[9:11], st[11:13], st[13:15]),
                "note": f.name[len(stem) + 1 + 15:-5].lstrip("."),
                "size": f.stat().st_size,
                "diff_vs_now": _hist_diff(cur, ess)[:40],   # what a restore CHANGES
                "essentials": ess,
            })
    return {"die": die, "config": config, "file": cur_p.name, "snapshots": rows}


class HistoryRestore(BaseModel):
    die: str
    config: Optional[str] = None     # None = the die's base geometry (die.yaml)
    stamp: str                       # YYYYMMDD-HHMMSS of the snapshot


@router.post("/history/restore")
def configuration_history_restore(req: HistoryRestore,
                                  authorization: Optional[str] = Header(None)):
    """Put a snapshot back as the configuration (or die) file.  The version
    being replaced is snapshotted first by `_save_yaml`, so this is undoable
    by restoring the newest entry."""
    _require_die_access(req.die, authorization)
    stem = _hist_stem(req.die, req.config)
    hdir = _hist_dir(req.die)
    cand = (sorted(hdir.glob("%s.%s*.yaml" % (stem, req.stamp)))
            if hdir.is_dir() else [])
    if len(cand) != 1:
        raise HTTPException(404, detail="no such snapshot: %s at %s (%d match)"
                            % (stem, req.stamp, len(cand)))
    d = _load_yaml(cand[0], "snapshot")
    dst = _die_file(req.die) if req.config is None else _cfg_file(req.die, req.config)
    _save_yaml(dst, d)
    try:
        from motor_ai_sim.config import clear_config_cache
        clear_config_cache()
    except Exception:      # noqa: BLE001
        pass
    return {"ok": True, "restored": cand[0].name, "into": dst.name}


# ── the community layer: publishing (migration Stage 2) ──────────────────────
#
# WHY A THIRD LAYER.  ``shared/`` is the vendor's catalog and ``workspaces/`` is
# private; between them sits the thing the user actually asked for — *"a user
# PUBLISHES a die/configuration/duty from their workspace; everyone registered
# sees it read-only with the author's name"*.  It is not the shared catalog
# (nobody vetted it) and it is not private (that is the point), so it is its
# own layer.
#
# NAMESPACED PER OWNER — ``published/<ws_id>/<die>/`` — because two accounts may
# both have a "CIANO28 85" and neither of them is wrong.  Everywhere a published
# die of ANOTHER account is named, it is named ``"<die> · by <name>"``; ``·`` is
# already a legal die-name character, so that label is itself a legal die name
# and every existing route addresses it without a second scheme.
#
# WHAT TRAVELS: the yaml documents AND the results — the run sidecars, the field
# ``.npz`` files and the per-duty result rows.  A published duty with no numbers
# in it would be a drawing, not an answer, and the report is the reason anyone
# would publish at all.

class PublishNote(BaseModel):
    note: Optional[str] = None


def _pub_ns(ws_id: Optional[str] = None) -> Path:
    return Path(str(_WS.published_root())) / str(ws_id or _WS.workspace().id)


def _pub_identity(authorization) -> dict:
    """The caller, refused unless they are a NAMED account.

    Publishing stamps an author onto a document every other account will read;
    ``anonymous`` is not an author, it is the absence of one.
    """
    if not _ws_layering():
        raise HTTPException(409, detail=(
            "publishing needs the multi-user layout (WORKSPACES_ROOT) — this "
            "installation has one workspace and nothing to publish to"))
    from motor_ai_sim.auth import ADMIN_OWNER, ANON_OWNER
    who = caller_identity(authorization)
    ident = str(who.get("id") or "")
    if not ident or ident in (ANON_OWNER, ADMIN_OWNER):
        raise HTTPException(401, detail="sign in to publish")
    return who


def _pub_stamp(doc: dict, email: str, note: Optional[str] = None) -> dict:
    doc = dict(doc)
    doc["published_by"] = email
    doc["published_at"] = datetime.now().isoformat(timespec="seconds")
    if note:
        doc["published_note"] = str(note)[:400]
    return doc


def _pub_copy_results(die: str, cfg: str, duties: list, dst_die: Path) -> dict:
    """The run sidecars and field files of the named duties, copied across.

    Best-effort per file and deliberately so: a publish that half-copied is
    repaired by publishing again, while a publish that 500s on one locked
    ``.npz`` teaches the user not to publish at all.
    """
    import shutil as _sh
    n_runs = n_fields = 0
    for entry in duties:
        if not isinstance(entry, dict):
            continue
        for rel in _duty_run_files(entry):
            src = _run_path(die, rel)
            if not src.is_file():
                continue
            dst = dst_die / str(rel)
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                _sh.copy2(src, dst)
                n_runs += 1
            except OSError:
                log.warning("publish: could not copy the run sidecar %s", src)
        stem = _run_stem(str(entry.get("name") or ""))
        fsrc = _w_die_dir(die) / "runs" / str(cfg) / stem / "fields"
        if fsrc.is_dir():
            fdst = dst_die / "runs" / str(cfg) / stem / "fields"
            fdst.mkdir(parents=True, exist_ok=True)
            for f in sorted(fsrc.glob("*.npz")):
                try:
                    _sh.copy2(f, fdst / f.name)
                    n_fields += 1
                except OSError:
                    log.warning("publish: could not copy the field file %s", f)
    return {"runs": n_runs, "fields": n_fields}


def _pub_results_store(dst_die: Path, die: str, cfg: Optional[str],
                       duties: Optional[list]) -> int:
    """Mirror this die's rows of ``.duty_results.json`` into the published die.

    A per-die file rather than a slice of the workspace store, in the SAME
    schema, so the read-through in ``duty_results`` is one extra path and not a
    second format.
    """
    try:
        import json as _json
        from motor_ai_sim import duty_results as _dr
        src = (_dr.read_all().get("results") or {}).get(str(die)) or {}
        keep: Dict[str, Any] = {}
        for c, node in src.items():
            if cfg is not None and c != str(cfg):
                continue
            if duties is None:
                keep[c] = node
            else:
                names = {str(d.get("name")) for d in duties
                         if isinstance(d, dict)}
                sel = {k: v for k, v in (node or {}).items() if k in names}
                if sel:
                    keep[c] = sel
        p = dst_die / ".duty_results.json"
        prev: Dict[str, Any] = {}
        if p.is_file():
            try:
                prev = ((_json.loads(p.read_text(encoding="utf-8")) or {})
                        .get("results") or {}).get(str(die)) or {}
            except Exception:                    # noqa: BLE001
                prev = {}
        for c, node in keep.items():
            prev.setdefault(c, {}).update(node)
        if not prev:
            return 0
        p.write_text(_json.dumps(
            {"version": _dr.VERSION,
             "updated_at": datetime.now().isoformat(timespec="seconds"),
             "results": {str(die): prev}}, ensure_ascii=False, default=str),
            encoding="utf-8")
        return sum(len(v) for v in prev.values())
    except Exception as exc:                     # noqa: BLE001
        log.warning("publish: duty results not mirrored for %s (%s)", die, exc)
        return 0


def _pub_put(p: Path, doc: dict) -> None:
    """Write a document INTO the published layer.

    Not through ``_save_yaml``: that function's whole job is to keep writes out
    of the other layers, and this is the one call that is allowed in.
    """
    import yaml as _y
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(_y.safe_dump(doc, sort_keys=False, allow_unicode=True),
                   encoding="utf-8")
    tmp.replace(p)


def _publish(die: str, cfg: Optional[str], duty: Optional[str],
             authorization, note: Optional[str] = None) -> dict:
    who = _pub_identity(authorization)
    email = str(who["id"])
    die = _check_name(die, "die")
    src_die = _w_die_dir(die)
    if not (src_die / "die.yaml").is_file():
        raise HTTPException(404, detail=(
            f"die '{die}' is not in your workspace — you publish your own "
            "work, not somebody else's"))
    ns = _pub_ns()
    ns.mkdir(parents=True, exist_ok=True)
    import json as _json
    (ns / _WS.OWNER_FILE).write_text(
        _json.dumps({"email": email, "name": _WS.owner_display_name(email)}),
        encoding="utf-8")
    dst_die = ns / die
    dst_die.mkdir(parents=True, exist_ok=True)
    _pub_put(dst_die / "die.yaml",
             _pub_stamp(_load_yaml(src_die / "die.yaml", "die"), email, note))

    cfg_names = ([_check_name(cfg, "configuration")] if cfg else
                 [p.stem for p in sorted(src_die.glob("*.yaml"))
                  if p.name != "die.yaml"])
    published: Dict[str, Any] = {}
    for cname in cfg_names:
        p = src_die / f"{cname}.yaml"
        if not p.is_file():
            raise HTTPException(404,
                                detail=f"configuration '{die}/{cname}' not found")
        c = _load_yaml(p, "configuration")
        duties = [d for d in (c.get("duties") or []) if isinstance(d, dict)]
        if duty is not None:
            duties = [d for d in duties if str(d.get("name")) == str(duty)]
            if not duties:
                raise HTTPException(
                    404, detail=f"duty '{die}/{cname}/{duty}' not found")
            # Publishing ONE duty must not retract the others already out
            # there: the previously published document's duties are kept and
            # only the named one is replaced.
            old = dst_die / f"{cname}.yaml"
            if old.is_file():
                prev = _load_yaml(old, "configuration")
                keep = [d for d in (prev.get("duties") or [])
                        if isinstance(d, dict)
                        and str(d.get("name")) != str(duty)]
                duties = keep + duties
        c["duties"] = duties
        _pub_put(dst_die / f"{cname}.yaml", _pub_stamp(c, email, note))
        published[cname] = _pub_copy_results(die, cname, duties, dst_die)
        published[cname]["duties"] = [str(d.get("name")) for d in duties]
        _pub_results_store(dst_die, die, cname, duties)

    log.info("family: '%s' published by %s (%d configuration(s))",
             die, email, len(published))
    return {"ok": True, "die": die, "owner": email,
            "label": _WS.published_label(die, email),
            "configs": published, "root": str(dst_die)}


@router.post("/publish/{die}")
def publish_die(die: str, req: Optional[PublishNote] = None,
                authorization: str = Header(default=None)):
    """Publish a whole die — every configuration, duty and stored answer."""
    return _publish(die, None, None, authorization, (req.note if req else None))


@router.post("/publish/{die}/{cfg}")
def publish_config(die: str, cfg: str, req: Optional[PublishNote] = None,
                   authorization: str = Header(default=None)):
    """Publish one configuration of a die."""
    return _publish(die, cfg, None, authorization, (req.note if req else None))


@router.post("/publish/{die}/{cfg}/{duty}")
def publish_duty(die: str, cfg: str, duty: str,
                 req: Optional[PublishNote] = None,
                 authorization: str = Header(default=None)):
    """Publish ONE duty — the others of the configuration stay as they were."""
    return _publish(die, cfg, duty, authorization, (req.note if req else None))


def _pub_find_ns(die: str, who: dict) -> tuple:
    """``(namespace dir, die name on disk)`` for something already published."""
    base, by = _WS.split_published_label(str(die))
    if not by:
        return (_pub_ns(), str(die))
    for lay in _WS.layers():
        if (lay.name == _WS.LAYER_PUBLISHED
                and _WS.owner_display_name(lay.owner) == by
                and (lay.dies_dir / base / "die.yaml").is_file()):
            return (lay.dies_dir, base)
    raise HTTPException(404, detail=f"'{die}' is not published")


def _unpublish(die: str, cfg: Optional[str], duty: Optional[str],
               authorization) -> dict:
    """Take it down.  The AUTHOR or an admin, and nobody else."""
    who = _pub_identity(authorization)
    email, is_admin = str(who["id"]), bool(who.get("is_admin"))
    ns, die = _pub_find_ns(die, who)
    owner = _WS._owner_email(ns)
    if not is_admin and ns.name != _WS.workspace().id:
        raise HTTPException(403, detail=(
            f"'{die}' was published by {owner or 'another account'} — only its "
            "author or an admin can change or withdraw it"))
    dd = ns / str(die)
    if not (dd / "die.yaml").is_file():
        raise HTTPException(404, detail=f"'{die}' is not published")
    import shutil as _sh
    if cfg is None:
        _sh.rmtree(dd, ignore_errors=True)
        log.warning("family: published die '%s' of %s withdrawn by %s",
                    die, owner or ns.name, email)
        return {"ok": True, "withdrawn": "die", "die": die}
    p = dd / f"{_check_name(cfg, 'configuration')}.yaml"
    if not p.is_file():
        raise HTTPException(404, detail=f"'{die}/{cfg}' is not published")
    if duty is None:
        p.unlink()
        _sh.rmtree(dd / "runs" / str(cfg), ignore_errors=True)
        log.warning("family: published '%s/%s' withdrawn by %s", die, cfg, email)
        return {"ok": True, "withdrawn": "config", "die": die, "config": cfg}
    c = _load_yaml(p, "configuration")
    was = len(c.get("duties") or [])
    rest = [d for d in (c.get("duties") or [])
            if isinstance(d, dict) and str(d.get("name")) != str(duty)]
    if len(rest) == was:
        raise HTTPException(404, detail=f"'{die}/{cfg}/{duty}' is not published")
    c["duties"] = rest
    _pub_put(p, c)
    _sh.rmtree(dd / "runs" / str(cfg) / _run_stem(str(duty)), ignore_errors=True)
    log.warning("family: published duty '%s/%s/%s' withdrawn by %s",
                die, cfg, duty, email)
    return {"ok": True, "withdrawn": "duty", "die": die, "config": cfg,
            "duty": str(duty)}


@router.delete("/publish/{die}")
def unpublish_die(die: str, authorization: str = Header(default=None)):
    return _unpublish(die, None, None, authorization)


@router.delete("/publish/{die}/{cfg}")
def unpublish_config(die: str, cfg: str,
                     authorization: str = Header(default=None)):
    return _unpublish(die, cfg, None, authorization)


@router.delete("/publish/{die}/{cfg}/{duty}")
def unpublish_duty(die: str, cfg: str, duty: str,
                   authorization: str = Header(default=None)):
    return _unpublish(die, cfg, duty, authorization)


@router.get("/community")
def community(response: Response, authorization: str = Header(default=None)):
    """What other accounts have published — the Community listing.

    Every REGISTERED account sees every published item; an anonymous visitor
    sees none.  Read-only by construction: nothing here is addressable for
    writing except through its own author's publish routes.
    """
    response.headers["Cache-Control"] = "no-store"
    response.headers["Vary"] = "Authorization"
    acc = catalog_access(authorization)
    if acc["mode"] == MODE_ANONYMOUS or not _ws_layering():
        return {"items": [], "can_publish": False}
    me = _WS.workspace().id
    items = []
    for lay in _WS.layers():
        if lay.name != _WS.LAYER_PUBLISHED:
            continue
        who = _WS.owner_display_name(lay.owner)
        try:
            entries = sorted(lay.dies_dir.iterdir())
        except OSError:
            continue
        for dd in entries:
            if not dd.is_dir() or not (dd / "die.yaml").is_file():
                continue
            try:
                doc = _load_yaml(dd / "die.yaml", "die")
            except HTTPException:
                continue
            cfgs = []
            for cf in sorted(dd.glob("*.yaml")):
                if cf.name == "die.yaml":
                    continue
                try:
                    c = _load_yaml(cf, "configuration")
                except HTTPException:
                    continue
                cfgs.append({"name": cf.stem,
                             "duties": [str(d.get("name")) for d in
                                        (c.get("duties") or [])
                                        if isinstance(d, dict)],
                             "published_at": c.get("published_at")})
            geo = doc.get("geometry") or {}
            items.append({
                "die": dd.name,
                # What every other route must be called with.
                "name": (dd.name if lay.owner_id == me
                         else _WS.published_label(dd.name, lay.owner)),
                "label": f"{dd.name} · by {who}",
                "owner": lay.owner, "owner_name": who,
                "mine": lay.owner_id == me,
                "published_by": doc.get("published_by"),
                "published_at": doc.get("published_at"),
                "note": doc.get("published_note"),
                "slots": geo.get("num_slots"), "poles": geo.get("num_poles"),
                "stator_diameter": geo.get("stator_diameter"),
                "configs": cfgs,
                "duties": sum(len(c["duties"]) for c in cfgs),
            })
    items.sort(key=lambda r: (not r["mine"], r["owner_name"], r["die"]))
    return {"items": items, "can_publish": True}
