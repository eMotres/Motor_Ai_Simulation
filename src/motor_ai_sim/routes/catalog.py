"""Motor catalog — the public 'MOTORS' menu.

A curated table of ready-made motors, organised by stator diameter.  Loading a
catalog motor applies its underlying preset (geometry + operating point) via
the presets service.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, Header, HTTPException, Query

import logging
import threading

from motor_ai_sim.auth import caller_identity as _caller_identity
from motor_ai_sim.json_store import mutate_json as _mutate_json, read_json as _read_json
from motor_ai_sim.fscache import read_yaml as _fsc_read_yaml

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/catalog", tags=["catalog"])

# Beside the config THIS PROCESS is pointed at (``MOTOR_AI_SIM_CONFIG``), which
# is how ``routes/family.py`` has always located the same file
# (``Path(DEFAULT_CONFIG_PATH).parent / "motor_catalog.json"``).  It was a
# hardcoded ``Path(__file__)…/config`` here, and `_mutate` WRITES it — so a
# redirected process upserted cards into the user's real catalog while family.py,
# one module over, wrote the sandbox copy.  With no env var set: unchanged.
#
# Migration Stage 1: resolved PER CALL against the caller's workspace (with none
# set: the same expression, the same file).  The NAME survives — several tests
# monkeypatch it, and a value in the module dict wins over the resolver.
def _catalog_path() -> Path:
    _ov = globals().get("_CATALOG_PATH")
    if _ov is not None:
        return Path(str(_ov))
    from motor_ai_sim.workspace import root as _ws_root
    return _ws_root() / "motor_catalog.json"


def __getattr__(name):
    if name == "_CATALOG_PATH":
        return _catalog_path()
    raise AttributeError(name)


def _read_path() -> Path:
    """The file the CALLER READS: his workspace's own ``motor_catalog.json``.

    A workspace that has none yet (a brand-new account is seeded with the machine
    only) reads through to ``<shared>/motor_catalog.json`` when the server has one
    - read-only, never copied: the first write of his own starts a file of his
    own.  Without this a new, granted user opened Configure on an empty list of
    references.  With layering off (this workstation) or a ``_CATALOG_PATH``
    override the single file is read exactly as before."""
    path = _catalog_path()
    if globals().get("_CATALOG_PATH") is None and not path.is_file():
        try:
            from motor_ai_sim.workspace import layering, shared_root
            if layering():
                shared = Path(str(shared_root())) / "motor_catalog.json"
                if shared.is_file():
                    return shared
        except Exception:                                       # noqa: BLE001
            log.warning("catalog: shared read-through failed", exc_info=True)
    return path


def _load() -> dict:
    return _read_json(_read_path(), {"tiers": [], "diameters_mm": [], "motors": []})


def _mutate(fn: Callable[[dict], None]) -> dict:
    """Apply `fn` to the catalog as it stands ON DISK, then write atomically.

    presets.py writes this same file (every motor save upserts a card), so the
    old load-mutate-save could drop a card that appeared while this request was
    running — thumbnail generation and passport extraction both hold the gap
    open for seconds to minutes.  `mutate_json` keys its lock by path, so both
    modules serialise on the same lock without knowing about each other.
    """
    return _mutate_json(_catalog_path(), fn,
                        default={"tiers": [], "diameters_mm": [], "motors": []})


def _mark_active(motors: list) -> None:
    """Flag the card whose motor IS the geometry currently loaded in the editor.

    Identity is the project's geometry stamp (`presets._geo_sig` — the same
    `key:value|…` dialect the browser spells in `geoSig.ts`), never the name: two
    cards can share a name, and the name says nothing about whether loading the
    card would reproduce what is on screen.

    Comparison order per card: the underlying PRESET's geometry (that is what a
    Load applies), falling back to the stamp stored on the card for a card whose
    preset is gone.  No stamp on either side -> `is_active` is False: unprovable
    is not the same as true.  And when the user has edited the geometry since the
    last save, NO card matches — which is the honest answer, not a bug.
    """
    try:
        from motor_ai_sim.routes.presets import (
            _geo_sig, _load_presets, active_geo_sig,
        )
        live = active_geo_sig()
    except Exception:
        for m in motors:
            m["is_active"] = False
        return
    presets = _load_presets() if live else {}
    for m in motors:
        sig = ""
        p = presets.get(m.get("preset") or "") or {}
        if p.get("geometry"):
            sig = _geo_sig(p["geometry"])
        elif m.get("geo_sig"):
            sig = str(m["geo_sig"])
        m["is_active"] = bool(live and sig and sig == live)


def _backing_entry(card: dict) -> dict:
    """The motor a card describes.  The PRESET is the authority on ownership —
    the card is a projection of it, and if the two ever disagree the padlock
    must follow the thing a write would actually land on."""
    try:
        from motor_ai_sim.routes.presets import _load_presets
        pid = card.get("preset")
        if pid:
            p = _load_presets().get(pid)
            if p:
                return p
    except Exception:  # noqa: BLE001
        pass
    return card


def _mark_access(motors: list, authorization: Optional[str]) -> None:
    """Stamp each card with its owner, its lock, and whether THIS caller may
    write it.  Computed per request like `is_active` and never stored: it is a
    fact about who is asking, and a stored copy would be another visitor's."""
    from motor_ai_sim.routes.presets import _can_write, _is_locked, _owner_of
    ident = _caller_identity(authorization)
    for m in motors:
        entry = _backing_entry(m)
        m["owner"] = _owner_of(entry)
        m["locked"] = _is_locked(entry)
        m["can_write"] = _can_write(entry, ident)


@router.get("")
def get_catalog(authorization: Optional[str] = Header(default=None)):
    """Return the full catalog: tiers, the diameter buckets, and every motor."""
    from motor_ai_sim.routes.presets import _backfill_owners
    _backfill_owners()
    cat = _load()
    # group motor ids by diameter for convenience
    by_d: dict = {d: [] for d in cat.get("diameters_mm", [])}
    for m in cat.get("motors", []):
        by_d.setdefault(m.get("diameter_mm"), []).append(m["id"])
    cat["by_diameter"] = by_d
    # Computed drive variants (Configure's PWM menu) ride in from the versioned
    # passport store (motor_ai_sim/passport_store.py) — read-only, per request,
    # and only for cards that have a record; every other card is served as is.
    from motor_ai_sim import passport_store as _ps
    _ps.attach(cat.get("motors", []))
    # `is_active` is COMPUTED per request and never stored: it is a fact about
    # the editor's current state, and a stored copy would be wrong the moment the
    # user nudged a dimension.
    _mark_active(cat.get("motors", []))
    _mark_access(cat.get("motors", []), authorization)
    # ── Private space ────────────────────────────────────────────────────────
    # A client's duplicated motors live in THEIR space: visibility 'private'
    # hides a card from everyone but its owner (and admins) until the owner
    # shares it.  Missing visibility = public — every card that existed before
    # this field keeps its behaviour.
    from motor_ai_sim.auth import caller_identity as _cid
    from motor_ai_sim.routes.presets import _owner_of as _own
    ident = _cid(authorization)
    if not ident.get("is_admin"):
        def _visible(m: dict) -> bool:
            if (m.get("visibility") or "public") != "private":
                return True
            return _own(_backing_entry(m)) == ident.get("id")
        cat["motors"] = _grant_visible_cards(
            [m for m in cat.get("motors", []) if _visible(m)], authorization)
        cat["by_diameter"] = {d: [i for i in ids
                                  if any(m["id"] == i for m in cat["motors"])]
                              for d, ids in cat["by_diameter"].items()}
    return cat


def _machine_names() -> set:
    """Casefolded ``"<die> <configuration>"`` and ``"<die>"`` of every die the caller can see —
    the names ``_family_doc_of_motor`` can match a card by, from directory listings only
    (no yaml is parsed, so it is cheap enough for a request that is meant to be fast)."""
    out: set = set()
    try:
        from motor_ai_sim.workspace import iter_dies as _iter_dies
        for e in _iter_dies():
            die = str(e.get("die") or e.get("name") or "")
            d = Path(str(e["dir"]))
            cfgs = [f.stem for f in d.glob("*.yaml") if f.name != "die.yaml"]
            if cfgs:
                out.add(die.casefold())
            out.update(f"{die} {c}".casefold() for c in cfgs)
    except Exception:                                           # noqa: BLE001
        log.warning("references: machine names could not be listed", exc_info=True)
    return out



def _machine_die_index() -> dict:
    """Casefolded ``"<die> <configuration>"`` and ``"<die>"`` -> the die's real
    name, from directory listings only (same source as ``_machine_names``)."""
    out: dict = {}
    try:
        from motor_ai_sim.workspace import iter_dies as _iter_dies
        for e in _iter_dies():
            die = str(e.get("die") or e.get("name") or "")
            d = Path(str(e["dir"]))
            cfgs = [f.stem for f in d.glob("*.yaml") if f.name != "die.yaml"]
            out.setdefault(die.casefold(), die)
            for c in cfgs:
                out.setdefault(f"{die} {c}".casefold(), die)
    except Exception:                                           # noqa: BLE001
        log.warning("catalog grants: die index could not be built", exc_info=True)
    return out


def _grant_visible_cards(cards: list, authorization: Optional[str]) -> list:
    """Drop the cards of dies an account was NOT granted.

    Only an account on the per-die grant list (``motor_access.MODE_GRANTED``) is
    filtered; admins, ``all`` grants and the anonymous exhibit are untouched.  A
    card is tied to its die by NAME ("<die>" or "<die> <configuration>", the way
    ``_family_doc_of_motor`` matches it).  A card that names no die of the
    catalogue cannot be tied to a grant, so a granted account does not get it —
    except a private card the caller owns (their own duplicate)."""
    from motor_ai_sim import motor_access as _ma
    access = _ma.catalog_access(authorization)
    if access.get("mode") != _ma.MODE_GRANTED:
        # not grant-filtered, but a card of a PRIVATE configuration still needs
        # a config grant (admins keep everything)
        return _ma.drop_private_config_cards(cards, access)
    from motor_ai_sim.routes.presets import _owner_of as _own
    me = _caller_identity(authorization).get("id")
    index = _machine_die_index()
    keep = []
    for m in cards:
        die = index.get(str(m.get("name") or "").strip().casefold())
        if die is not None and _ma.may_see_die(access, die):
            keep.append(m)
        elif die is None and (m.get("visibility") or "public") == "private"                 and _own(_backing_entry(m)) == me:
            keep.append(m)
    return _ma.drop_private_config_cards(keep, access)


#: Source metadata -> the passport-carrying cards across the effective catalogues.
#: Workspace cards and admin-curated shared cards are independent libraries; an
#: unrelated personal card must not hide the shared references.
_REF_CACHE: dict = {"key": None, "cards": []}
_REF_CACHE_LOCK = threading.RLock()


def _reference_catalog_paths() -> list[Path]:
    """Reference sources in precedence order, respecting explicit test overrides."""
    override = globals().get("_CATALOG_PATH")
    if override is not None:
        return [Path(str(override))]

    workspace_path = _catalog_path()
    try:
        from motor_ai_sim.workspace import layering, shared_root
        if layering():
            shared_path = Path(str(shared_root())) / "motor_catalog.json"
            if shared_path != workspace_path:
                return [workspace_path, shared_path]
    except Exception:  # noqa: BLE001 — keep serving the caller catalogue on resolver errors
        log.warning("catalog: shared reference source unavailable", exc_info=True)
    return [workspace_path]


def _reference_source_key(paths: list[Path]) -> tuple:
    """Track metadata for every source, including absent files, for create/remove."""
    key = []
    for path in paths:
        try:
            st = path.stat()
            key.append((str(path), st.st_mtime_ns, st.st_size))
        except OSError:
            key.append((str(path), None, None))
    return tuple(key)


def _passport_cards() -> list:
    # The cache is process-global while the source paths are request-local.
    # Serialize key-check, source reads and list capture so concurrent accounts
    # cannot observe a half-updated cache from another workspace.
    with _REF_CACHE_LOCK:
        paths = _reference_catalog_paths()
        key = _reference_source_key(paths)
        if _REF_CACHE["key"] != key:
            # Read the files separately so one catalogue cannot hide the other.
            # IDs establish precedence; geometry alone deliberately does not,
            # since distinct stack/build cards may share a cross-section.
            ordered: list[dict] = []
            seen_ids: set[str] = set()
            for path in paths:
                catalog = _read_json(path, {"tiers": [], "diameters_mm": [], "motors": []})
                for motor in catalog.get("motors", []):
                    motor_id = motor.get("id")
                    stable_id = str(motor_id) if motor_id is not None else None
                    if stable_id is not None and stable_id in seen_ids:
                        continue
                    if stable_id is not None:
                        # Record even a workspace card without a passport: it still
                        # wins over a shared record with the same stable ID.
                        seen_ids.add(stable_id)
                    if (isinstance(motor.get("passport"), dict)
                            and motor["passport"].get("passport")):
                        ordered.append(motor)
            _REF_CACHE["cards"] = ordered
            _REF_CACHE["key"] = key
        return _REF_CACHE["cards"]


def _l0_mm(card: dict) -> Optional[float]:
    try:
        return float(((card.get("passport") or {}).get("passport") or {}).get("L0_mm"))
    except (TypeError, ValueError):
        return None


def _passport_copper_mass(card: Optional[dict], passport: dict) -> Optional[float]:
    """Copper mass at the passport's own base geometry, or None if unknown.

    The catalogue configuration can have a different stack/wire build from a
    passport copied from another build, so set the mass-model geometry from
    the passport's recorded L/N/h before measuring its copper component.
    """
    if not card or not card.get("die") or not card.get("config"):
        return None
    try:
        import yaml
        from motor_ai_sim.modules import motor_mass as _mm
        from motor_ai_sim.simulation.geometry_2d import merge_geo_override, params_from_config
        from motor_ai_sim.masses import compute_masses
        from motor_ai_sim.material_context import get_request_materials, set_request_materials

        ddir = _mm.find_die_dir(str(card["die"]))
        if ddir is None:
            return None
        from motor_ai_sim import fscache as _fsc
        dy = _fsc.read_yaml(ddir / "die.yaml") or {}
        cy_path = ddir / f"{card['config']}.yaml"
        cy = _fsc.read_yaml(cy_path) or {}
        geo = dict(dy.get("geometry") or {})
        geo.update(cy.get("geometry_overrides") or {})
        for key, val in (("motor_length", passport.get("L0_mm")),
                         ("num_wires_per_slot", passport.get("N0")),
                         ("wire_height", passport.get("wireH0_mm"))):
            if val is None or float(val) <= 0:
                return None
            geo[key] = float(val)
        cfg_doc = _mm._default_config_doc()
        assigned = {k: v for k, v in (cfg_doc.get("materials") or {}).items()
                    if k in ("stator_core", "rotor_core", "magnet", "slot", "shaft") and v}
        assigned.update({k: v for k, v in (cy.get("materials") or {}).items() if v})
        prior = get_request_materials()
        own_materials = dict(cy.get("custom_materials") or {})
        own_parts = dict(cy.get("parts") or {})
        try:
            e_raw = passport.get("endWindFrac")
            if e_raw is None:
                return None
            e = float(e_raw)
            if not (0.0 <= e < 1.0):
                return None
            set_request_materials({"assignment": assigned, "materials": own_materials, "parts": own_parts})
            p = params_from_config(geo_override=geo)
            g = merge_geo_override(dict(cfg_doc.get("geometry") or {}), geo)
            k_end = 1.0 / (1.0 - e)
            mass = compute_masses(p, g, k_end=k_end, materials=assigned)
        finally:
            set_request_materials(prior)
        cu = float(mass.get("cu") or 0.0)
        return cu if cu >= 0.0 else None
    except Exception:                                  # noqa: BLE001 - missing mass provenance must not break references
        return None


def _same_build(passport: dict, build: Optional[Dict[str, float]]) -> bool:
    """The passport's base build (L0, N0, wireH0) equals ``build`` (``motor_mass.config_build``)."""
    if not build:
        return False
    try:
        pairs = ((passport.get("L0_mm"), build["L_mm"]), (passport.get("N0"), build["N"]),
                 (passport.get("wireH0_mm"), build["wireH_mm"]))
        return all(a is not None and abs(float(a) - float(b)) <= 1e-6 * max(1.0, abs(float(b))) for a, b in pairs)
    except (TypeError, ValueError, KeyError):
        return False


def _with_full_mass(outer: dict, card: Optional[dict]) -> dict:
    """THE MOTOR MASS IS THE FULL ASSEMBLED MASS (owner 2026-10-14).  The passport's ``mass0_kg`` is the ACTIVE mass of its
    base solve (it scales with the stack length); Configure adds ``mass_fixed_kg`` - the parts that do not scale with the
    stack (shaft difference, holders, rings, bearings, screws, board, pins, enamel; the CAD bill of materials) - to show
    the full motor mass and every per-kg figure.  No CAD model yet: ``mass_partial = true`` and nothing is added, so
    Configure shows the active mass and says so.  A copy; the cached card is never mutated."""
    inner = outer.get("passport") if isinstance(outer, dict) else None
    if not isinstance(inner, dict):
        return outer
    summ = None
    if card and card.get("die") and card.get("config"):
        try:
            from motor_ai_sim.modules import motor_mass as _mm
            summ = _mm.mass_summary(str(card["die"]), str(card["config"]))
        except Exception:                                  # noqa: BLE001 - never fail the references for a mass
            summ = None
    if summ is None or summ["partial"]:
        extra = {"mass_partial": True}
        if summ is not None:
            extra["mass_partial_reason"] = summ.get("partial_reason") or ""
    else:
        extra = {"mass_partial": False, "mass_fixed_kg": round(summ["full_kg"] - summ["active_kg"], 5),
                 "mass_active_base_kg": round(summ["active_kg"], 5), "mass_rotating_kg": round(summ["rotating_kg"], 5),
                 "mass_scope": summ.get("scope", "full motor")}      # "frameless kit": the fixed part may be negative (no shaft tube)
    # The passport's ``mass0_kg`` is a STORED number of its base solve; some cards carry the full mass there (40 L12 0.1229 /
    # L20 0.1807 kg against the active 0.0945 / 0.1506 - Configure then added the non-active parts twice, owner 2026-10-19).
    # When the passport's base build IS the configuration's build, the base active mass is the server's own active mass.
    if summ is not None and card and _same_build(inner, _mm.config_build(str(card["die"]), str(card["config"]))):
        if inner.get("mass0_kg") is not None:
            extra["mass0_stored_kg"] = inner.get("mass0_kg")
        extra["mass0_kg"] = round(float(summ["active_kg"]), 5)
    cu = _passport_copper_mass(card, inner)
    if cu is not None:
        extra["mass_cu0_kg"] = round(cu, 7)
    return {**outer, "passport": {**inner, **extra}}


@router.get("/references")
def get_references(authorization: Optional[str] = Header(default=None)):
    """Configure's reference motors — ONLY the cards that carry a passport, and
    only what Configure reads of them (id, name, diameter, the passport block with
    its computed ``pwm_variants``).

    ``GET /api/catalog`` also ships every card's ``thumb_svg`` — 12 of its 12.6 MB
    on the live catalogue — and Configure used to download and wait for all of it on
    every open.  Same visibility rule as ``GET /api/catalog`` (a private card is its
    owner's and an admin's only; the owner is computed by the very function the
    back-fill stamps with, so the answer is the same without the write).
    """
    from motor_ai_sim.auth import caller_identity as _cid
    from motor_ai_sim.routes.presets import _owner_of as _own
    cards = _passport_cards()
    ident = _cid(authorization)
    if not ident.get("is_admin"):
        cards = [m for m in cards
                 if (m.get("visibility") or "public") != "private"
                 or _own(_backing_entry(m)) == ident.get("id")]
        cards = _grant_visible_cards(cards, authorization)
    from motor_ai_sim import passport_store as _ps
    names = _machine_names()
    out = [{"id": m.get("id"), "name": m.get("name"), "diameter_mm": m.get("diameter_mm"),
            # True when the card's name is a configuration (or the die) of the catalogue: it
            # then HAS a pack, controller and variants.  Legacy duplicates of the same
            # geometry ("CIANO14 40_12" next to "CIANO14 40 new") have none, and the web must
            # not pick one of those for the loaded machine.
            "has_machine": str(m.get("name") or "").strip().casefold() in names,
            # the FULL passport card behind this machine ({die, config, date}) or None
            "card": _ps.card_of(m.get("name"), _l0_mm(m)),
            "passport": m["passport"]} for m in cards]        # new dicts: the cache is never mutated
    for o in out:
        o["passport"] = _with_full_mass(o["passport"], o.get("card"))
    _ps.attach(out)
    return {"motors": out}


def hidden_preset_view(authorization: Optional[str]) -> tuple:
    """``(preset ids, casefolded names)`` of the presets that describe a
    configuration the caller may not see (``motor_access.config_visible``) -
    found through the catalog cards named ``"<die> <config>"`` and through a
    preset carrying that name itself.  ``(set(), set())`` for admins and when
    nothing is hidden (one cached stat per configuration file)."""
    from motor_ai_sim import motor_access as _ma
    access = _ma.catalog_access(authorization)
    if access.get("is_admin"):
        return set(), set()
    names = _ma.hidden_config_names(access)
    if not names:
        return set(), set()
    ids: set = set()
    paths = list(_reference_catalog_paths())
    try:
        paths.append(_read_path())
    except Exception:                                           # noqa: BLE001
        pass
    for path in paths:
        cat = _read_json(path, {"tiers": [], "diameters_mm": [], "motors": []})
        for m in cat.get("motors", []):
            if str(m.get("name") or "").strip().casefold() in names and m.get("preset"):
                ids.add(str(m["preset"]))
    return ids, names


def _config_hidden_card(motor: dict, authorization: Optional[str]) -> bool:
    """True when the card describes a configuration the caller may not see
    (motor_access.drop_private_config_cards) - such a card is, for them, a card
    that does not exist."""
    from motor_ai_sim import motor_access as _ma
    return not _ma.drop_private_config_cards([motor], _ma.catalog_access(authorization))


@router.post("/{motor_id}/load")
def load_motor(motor_id: str,
               authorization: Optional[str] = Header(default=None)):
    """Load a catalog motor into the app by applying its underlying preset."""
    cat = _load()
    motor = next((m for m in cat.get("motors", []) if m.get("id") == motor_id), None)
    if not motor or _config_hidden_card(motor, authorization):
        raise HTTPException(status_code=404, detail=f"motor '{motor_id}' not found")
    preset_id = motor.get("preset")
    if not preset_id:
        raise HTTPException(status_code=400, detail=f"motor '{motor_id}' has no preset to load")
    from motor_ai_sim.routes.presets import apply_preset
    result = apply_preset(preset_id, authorization)
    result["motor"] = motor_id
    return result


@router.post("/{motor_id}/duplicate")
def duplicate_motor(motor_id: str, body: Optional[dict] = None,
                    authorization: Optional[str] = Header(default=None)):
    """Copy a catalog motor into the CALLER's private space.

    The client workflow (user's spec 2026-08-24): in Motors a client may only
    duplicate existing motors; the copy lives in a space only they see — card
    visibility 'private', preset owned by them — until they share it.  The
    passport (2D + 3D) is copied verbatim, so their Configure works on the
    copy instantly, without any solving."""
    from motor_ai_sim.auth import caller_identity as _cid
    ident = _cid(authorization)
    if not ident.get("id"):
        raise HTTPException(status_code=401,
                            detail="sign in to duplicate a motor into your space")
    cat = _load()
    src = next((m for m in cat.get("motors", []) if m.get("id") == motor_id), None)
    if not src or _config_hidden_card(src, authorization):
        raise HTTPException(status_code=404, detail=f"motor '{motor_id}' not found")
    if (src.get("visibility") or "public") == "private":
        from motor_ai_sim.routes.presets import _owner_of as _own
        if not ident.get("is_admin") and _own(_backing_entry(src)) != ident.get("id"):
            raise HTTPException(status_code=404, detail=f"motor '{motor_id}' not found")
    import copy as _copy
    import re as _re
    import time as _time
    from motor_ai_sim.routes.presets import _load_presets, _PRESETS_PATH
    from motor_ai_sim.json_store import mutate_json as _mj
    presets = _load_presets()
    src_preset = presets.get(src.get("preset") or "")
    if not isinstance(src_preset, dict):
        raise HTTPException(status_code=400,
                            detail=f"motor '{motor_id}' has no preset to copy")
    _stamp = _time.strftime("%Y%m%d_%H%M%S")
    _who = _re.sub(r"[^a-z0-9]+", "_", str(ident["id"]).split("@")[0].lower())[:16]
    new_pid = f"usr_{_who}_{_stamp}"
    new_mid = f"cat_{new_pid}"
    _name = str((body or {}).get("name") or f"{src.get('name')} · copy").strip()[:80]
    new_preset = _copy.deepcopy(src_preset)
    new_preset.update({"id": new_pid, "name": _name, "owner": ident["id"],
                       "template": False, "locked": False})
    def _mp(d: dict) -> None:
        d[new_pid] = new_preset
    _mj(str(_PRESETS_PATH), _mp, {})
    new_card = {k: _copy.deepcopy(v) for k, v in src.items()
                if k not in ("id", "preset", "name", "visibility")}
    new_card.update({"id": new_mid, "preset": new_pid, "name": _name,
                     "visibility": "private", "owner": ident["id"]})
    def _mc(d: dict) -> None:
        d.setdefault("motors", []).append(new_card)
    _mutate(_mc)
    return {"status": "ok", "motor": new_mid, "preset": new_pid, "name": _name,
            "visibility": "private"}


@router.post("/{motor_id}/share")
def share_motor(motor_id: str, unshare: bool = False,
                authorization: Optional[str] = Header(default=None)):
    """Owner (or admin) makes their private motor public — or takes it back.
    A client's copy stays theirs-only until THEY decide to share it."""
    from motor_ai_sim.auth import caller_identity as _cid
    from motor_ai_sim.routes.presets import _require_write_access
    cat = _load()
    motor = next((m for m in cat.get("motors", []) if m.get("id") == motor_id), None)
    if not motor:
        raise HTTPException(status_code=404, detail=f"motor '{motor_id}' not found")
    _require_write_access(_backing_entry(motor), motor_id, _cid(authorization))
    vis = "private" if unshare else "public"
    def _m(d: dict) -> None:
        for m in d.get("motors", []):
            if m.get("id") == motor_id:
                m["visibility"] = vis
                return
    _mutate(_m)
    return {"status": "ok", "motor": motor_id, "visibility": vis}


@router.delete("/{motor_id}")
def delete_motor(motor_id: str, drop_preset: bool = True,
                 authorization: Optional[str] = Header(default=None)):
    """Remove a motor from the catalog.  Drops the card and, by default, its
    underlying preset too — so it is gated by the same ownership rule as
    deleting the preset directly: the owner or an admin, and never a locked
    motor.  No-op-safe: 404 only if the id isn't present."""
    from motor_ai_sim.routes.presets import _backfill_owners, _require_write_access
    _backfill_owners()
    cat = _load()
    motors = cat.get("motors", [])
    target = next((m for m in motors if m.get("id") == motor_id), None)
    if not target:
        raise HTTPException(status_code=404, detail=f"motor '{motor_id}' not found")
    _require_write_access(_backing_entry(target), motor_id,
                          _caller_identity(authorization))

    def _m(d: dict) -> None:
        d["motors"] = [m for m in d.get("motors", []) if m.get("id") != motor_id]
        # prune now-empty diameter buckets so the section header disappears too
        remaining_d = {m.get("diameter_mm") for m in d["motors"]}
        d["diameters_mm"] = [x for x in d.get("diameters_mm", []) if x in remaining_d]
    _mutate(_m)

    preset_id = target.get("preset")
    dropped_preset = False
    if drop_preset and preset_id:
        try:
            from motor_ai_sim.routes.presets import _drop_preset, _load_presets
            if preset_id in _load_presets():
                _drop_preset(preset_id)
                dropped_preset = True
        except Exception:
            pass
    return {"status": "ok", "deleted": motor_id, "dropped_preset": dropped_preset}


# ── Passport: characterise a motor via FEM so the Configurator can scale it ──

def _family_doc_of_motor(motor: dict,
                         geometry: Optional[dict] = None) -> Optional[dict]:
    """The FAMILY configuration document behind a catalog card, matched by the
    card's name ("<die> <configuration>").

    Two passport fields come from it and from nowhere else: the ROLE (only a
    generator gets a charging block) and the BATTERY (the PWM block needs a bus
    to switch against, and the charging block needs the pack as a circuit).
    Both live on the configuration, never on the card, and a card whose family
    document is gone is a card with no pack — not a card with a default one.
    """
    name = str(motor.get("name") or "").casefold()
    if not name:
        return None
    try:
        import yaml as _yaml
        from motor_ai_sim.workspace import iter_dies as _iter_dies
        by_die: dict = {}
        # Stage 2: every layer, workspace first — a card whose configuration
        # lives in the shared catalog must still find its document.
        for f in [q for e in _iter_dies()
                  for q in sorted(Path(str(e["dir"])).glob("*.yaml"))]:
            if f.name == "die.yaml":
                continue
            d = _fsc_read_yaml(f) or {}
            if f"{d.get('die')} {d.get('name')}".casefold() == name:
                return d
            by_die.setdefault(str(d.get("die") or "").casefold(), []).append(d)
        # FALLBACK: older cards carry the DIE name alone ("CIANO14 40 new",
        # whose configuration is "L12") — the exact "<die> <config>" match then
        # fails and the machine silently loses its pack and its role.  One
        # configuration under that die is unambiguous; several are separated by
        # the STACK LENGTH, which is what a configuration of a shared die is
        # (the large reference die series: 40 / 160 / 220 mm on one lamination).  Still
        # ambiguous → None, because guessing which pack a card runs on is
        # exactly the kind of silent substitution a passport must not make.
        got = by_die.get(name) or []
        if len(got) == 1:
            return got[0]
        L = (geometry or {}).get("motor_length")
        if got and L is not None:
            near = [d for d in got
                    if abs(float((d.get("geometry_overrides") or {})
                                 .get("motor_length", -1e9)) - float(L)) < 1e-6]
            if len(near) == 1:
                return near[0]
        return None
    except Exception:      # noqa: BLE001 — a missing family doc is not an error
        return None


def _role_of_motor(motor: dict) -> Optional[str]:
    """"motor" / "generator" for a catalog card, read from the FAMILY
    configuration of the same name ("<die> <configuration>").

    The role lives on the configuration, not on the card, and a passport must
    be measured in the machine's own convention — a generator solved as a motor
    sits at a different operating point (torque, terminal voltage and the loss
    split all move).  Unknown machine → None, and the caller defaults to motor.
    """
    name = str(motor.get("name") or "").casefold()
    if not name:
        return None
    try:
        import yaml as _yaml
        from motor_ai_sim.workspace import iter_dies as _iter_dies
        # Stage 2: every layer, workspace first — a card whose configuration
        # lives in the shared catalog must still find its document.
        for f in [q for e in _iter_dies()
                  for q in sorted(Path(str(e["dir"])).glob("*.yaml"))]:
            if f.name == "die.yaml":
                continue
            d = _fsc_read_yaml(f) or {}
            if f"{d.get('die')} {d.get('name')}".casefold() == name:
                r = str(d.get("role") or "").strip().lower()
                return r if r in ("motor", "generator") else None
    except Exception:      # noqa: BLE001 — a missing role is not an error
        return None
    return None


@router.post("/{motor_id}/passport")
def generate_motor_passport(motor_id: str, coarse: bool = False,
                            pwm: str = "quick",
                            authorization: Optional[str] = Header(default=None)):
    """Admin: FEM-characterise a catalog motor and store its passport.

    Applies the motor's preset (so its geometry + operating point become the
    active config), runs the passport extraction (3 base solves + an rpm loss
    sweep), and saves the result on the catalog entry.  FEM-heavy (minutes);
    `coarse=true` uses fewer steps/rpms for a quick smoke test.

    Side effect: switches the active motor to the one being characterised.

    Writes the passport onto the card, so it is gated like any other write to
    that motor: the owner or an admin.
    """
    from motor_ai_sim.routes.presets import _backfill_owners, _require_write_access
    _backfill_owners()
    cat = _load()
    motor = next((m for m in cat.get("motors", []) if m.get("id") == motor_id), None)
    if not motor:
        raise HTTPException(status_code=404, detail=f"motor '{motor_id}' not found")
    _require_write_access(_backing_entry(motor), motor_id,
                          _caller_identity(authorization))
    preset_id = motor.get("preset")
    if not preset_id:
        raise HTTPException(status_code=400, detail=f"motor '{motor_id}' has no preset to characterise")
    try:
        # ZERO live-state touch (user 2026-08-25 "so it stays out of my way"):
        # the preset's machine rides every solve as per-request overrides —
        # geometry via `geo`, connection as a parameter, materials through
        # the request material context, rpm as an argument.  No apply_preset,
        # no winding patch, no sim-config mutation: the engineer's live
        # machine is untouched for the whole generation.
        from motor_ai_sim.routes.presets import _load_presets
        _p = _load_presets().get(preset_id) or {}
        if not isinstance(_p.get("geometry"), dict):
            raise HTTPException(status_code=400,
                                detail=f"preset '{preset_id}' has no geometry")
        _psim = _p.get("simulation") or {}
        machine = {
            # the preset's saved Mesh settings — the base machine's (owner
            # 2026-09-30: passports mesh only as the Mesh tab says)
            "mesh": dict(_p.get("mesh") or {}),
            "geometry": dict(_p["geometry"]),
            "connection": str(_psim.get("connection") or "") or None,
            # Terminal connection of THIS machine (preset sim block, mirrored
            # from winding.star_delta on save); star when absent.
            "star_delta": ("delta" if str(_psim.get("star_delta") or "")
                           .lower().startswith("d") else "star"),
            "materials": dict(_p.get("materials") or {}),
            # Measured k_end of the built machine (preset sim block) — the
            # passport's R/copper then describe the real winding.
            "end_winding_factor": float(_psim.get("end_winding_factor") or 0.0),
        }
        from motor_ai_sim.passport import generate_passport
        # coarse = a quick rung: fewer steps and three speeds instead of six,
        # but still spanning the MACHINE's own rated speed (passport.py derives
        # them from rpm0 when the list is left out — a fixed 2/4/6 krpm list
        # missed every high-speed build's operating point entirely).
        # A generator has to be CHARACTERISED as a generator.  The solver falls
        # back to the shared config's mode when the caller stays silent, so a
        # passport used to depend on which way the engineer had left the
        # Simulation tab (measured 2026-08-30: the whole catalog was
        # regenerated as motors while two large reference die machines are generators).
        _fam = _family_doc_of_motor(motor, machine["geometry"]) or {}
        _role = str(_fam.get("role") or "").strip().lower() or None
        if _role not in ("motor", "generator"):
            _role = _role_of_motor(motor)
        _mode = str(_psim.get("mode") or _role or "motor")
        # The pack rides from the family configuration.  coarse=true measures
        # no PWM (it is the smoke-test rung), and an explicit ?pwm=off skips it
        # on a full run too — a passport without a PWM block is a passport
        # whose Configure tab simply has no PWM toggle.
        _batt = dict(_fam.get("battery") or {}) or None
        _pwm = "off" if coarse else str(pwm or "quick").strip().lower()
        _rpm0 = float(_psim["rpm"]) if _psim.get("rpm") else 0.0
        kw = (dict(base_steps=6, sweep_steps=6,
                   rpms=([round(f * _rpm0) for f in (0.5, 1.0, 1.5)]
                         if _rpm0 > 0 else [2000.0, 4000.0, 6000.0]))
              if coarse else {})
        result = generate_passport(
            machine=machine,
            I0=(float(_psim["max_current"])
                if _psim.get("max_current") is not None else None),
            gamma_deg=(float(_psim["phase_offset_deg"])
                       if _psim.get("phase_offset_deg") is not None else None),
            rpm0=(float(_psim["rpm"]) if _psim.get("rpm") else None),
            mode=_mode, battery=_batt, pwm=_pwm, role=_role,
            # The die's calibrated d-axis, when the preset carries it.
            daxis_deg=(float(_psim["daxis_deg"])
                       if _psim.get("daxis_deg") is not None else None),
            **kw)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"passport generation failed: {e}")

    # ── 3D end-effect for the Configure tab ──────────────────────────────────
    # The tuner rescales this passport analytically (no FEM) as the user moves
    # KV (turns/connection) and the lamination length — so the 3D correction
    # must ride WITH the passport, as k_flux over a few STACK LENGTHS for the
    # length slider to interpolate.  Store hit is free; a miss costs three
    # quick Stage A solves (~30-60 min, admin-side, once per catalog motor).
    # coarse=true never solves — it attaches a store hit or an honest null.
    try:
        from motor_ai_sim.routes.simulation import (_geometry_fingerprint,
                                                    _end3d_lookup)
        _fp = _geometry_fingerprint(machine["geometry"])
        # exact=True: an INHERITED coefficient (an earlier geometry of the same
        # machine) must never stand in for the catalog's own measurement.
        _e3 = _end3d_lookup(_fp, machine["geometry"], exact=True)
        if (_e3 is None or not _e3.get("k_flux_vs_L")) and not coarse:
            # SUBPROCESS, not in-process: PARDISO (MKL) took the whole API down
            # with a native access violation when Stage A ran inside the server
            # (measured live 2026-08-24 22:31 — two motors into the passport
            # queue).  A child process contains any native crash; the server
            # just reports the failure and ships the 2D passport.  Mesh knobs
            # scale with the machine (one fixed h_gap was wrong for both the
            # 0.3 mm and the 0.9 mm air gaps).
            import json as _j2
            import subprocess as _sp
            import sys as _sys
            import tempfile as _tf
            from pathlib import Path as _P2
            _src = str(_P2(__file__).resolve().parents[2])
            # The PRESET machine goes to the child in a file — the child must
            # never read the live config (same zero-touch rule as the 2D part).
            with _tf.NamedTemporaryFile("w", suffix=".json", delete=False,
                                        encoding="utf-8") as _tfh:
                _j2.dump({"geometry": machine["geometry"],
                          "materials": machine["materials"]}, _tfh)
                _mfile = _tfh.name
            _child = (
                "import sys, json\n"
                "sys.path.insert(0, sys.argv[1])\n"
                "m = json.load(open(sys.argv[2], encoding='utf-8'))\n"
                "g = dict(m.get('geometry') or {})\n"
                "mats = dict(m.get('materials') or {})\n"
                "if mats:\n"
                "    from motor_ai_sim.material_context import set_request_materials\n"
                "    set_request_materials({'assignment': mats, 'materials': {}})\n"
                "from motor_ai_sim.simulation.static3d.end_effect import run_stage_a\n"
                "# mesh sizes: run_stage_a derives them from the PHYSICAL\n"
                "# scales (air gap, thinnest magnet), never from the OD\n"
                "p = run_stage_a(geo_override=g, n_stack=4, n_cap=6,\n"
                "                order=2, l_factors=(0.75, 1.0, 1.5),\n"
                "                do_bracket=False, do_2d=True,\n"
                "                tol=3e-3, max_iter=45, verbose=False)\n"
                "mm = p.get('machine') or {}\n"
                "md = p.get('model') or {}\n"
                "out = {k: p.get(k) for k in ('k_flux', 'k_flux_self',\n"
                "                             'generated_utc', 'l_stack_curve')}\n"
                "out['materials'] = mm.get('materials')\n"
                "out['mesh'] = {k: md.get(k) for k in ('h_gap_mm', 'h_solid_mm',\n"
                "               'mesh_rule', 'box_z_mm', 'iron_stack_kf')}\n"
                "print('PASSPORT3D:' + json.dumps(out, default=float))\n")
            try:
                # THE CHILD'S MACHINE IS THE CALLER'S.  Geometry and materials
                # travel in the file above, but everything the child does NOT
                # override — the mesh block, the winding, the passport store it
                # reads — resolves from ``MOTOR_AI_SIM_CONFIG``, and a bare
                # inherited environment names the process/starter machine on a
                # layered server (2026-09-20, the sweep incident).  Stamp the
                # caller's own config path, exactly as the optimizer's eval
                # subprocess does.
                import os as _os_c
                _env_c = dict(_os_c.environ)
                try:
                    from motor_ai_sim.config import config_path as _cfgp_c
                    _env_c["MOTOR_AI_SIM_CONFIG"] = str(_cfgp_c())
                except Exception:               # noqa: BLE001
                    pass
                _pr = _sp.run([_sys.executable, "-c", _child, _src, _mfile],
                              capture_output=True, text=True, timeout=7200,
                              env=_env_c)
            finally:
                try:
                    _P2(_mfile).unlink()
                except OSError:
                    pass
            _line = next((ln for ln in (_pr.stdout or "").splitlines()
                          if ln.startswith("PASSPORT3D:")), None)
            if _pr.returncode != 0 or not _line:
                raise RuntimeError(
                    "stage-A subprocess failed (rc=%s): %s"
                    % (_pr.returncode, (_pr.stderr or "")[-400:]))
            _sa = _j2.loads(_line[len("PASSPORT3D:"):])
            _e3 = {
                "k_flux": _sa["k_flux"], "k_flux_self": _sa["k_flux_self"],
                "fidelity": "quick (n_stack=4, 3-point L-sweep)",
                "generated_utc": _sa.get("generated_utc"),
                "machine": f"{result['slots']}s/{result['poles']}p "
                           f"OD {2*result['geo']['statorOR_mm']:.0f}",
                "k_flux_vs_L": {str(e["stack_mm"]): round(e["k_flux"], 5)
                                for e in (_sa.get("l_stack_curve") or [])},
                # provenance: the steel actually solved and the mesh rule, so
                # a wrong-material passport can be found later
                "materials": _sa.get("materials"),
                "mesh": _sa.get("mesh"),
            }
            import json as _j
            from pathlib import Path as _P
            from motor_ai_sim.workspace import root as _ws_root
            _pp = _ws_root() / "end_effect_passports.json"
            _all = _j.loads(_pp.read_text(encoding="utf-8")) if _pp.exists() else {}
            _all[_fp] = _e3
            _pp.write_text(_j.dumps(_all, ensure_ascii=False, indent=1),
                           encoding="utf-8")
        result["passport"]["end3d"] = (
            None if _e3 is None else
            {"k_flux": _e3["k_flux"],
             "k_flux_vs_L": _e3.get("k_flux_vs_L") or None,
             "fidelity": _e3.get("fidelity")})
    except Exception:  # noqa: BLE001 — a 3D failure must not lose the 2D passport
        log.exception("catalog passport: 3D end-effect attach failed — "
                      "the passport ships without it (Configure shows 2D)")
        result["passport"]["end3d"] = None

    # ── Bench Ld/Lq for the Configure tab ────────────────────────────────────
    # Small-signal inductances at the I≈0 iron state (the LCR-meter value) —
    # a MACHINE constant, so the tuner can scale it analytically with turns,
    # stack and connection instead of guessing.  Cached per machine, so a
    # regeneration costs one file read; a miss is three quick solves.
    try:
        from motor_ai_sim.routes.simulation import (_bench_read, _bench_compute,
                                                    _geometry_fingerprint as _gfp)
        _bconn = machine.get("connection") or ""
        _b = _bench_read(_gfp(machine["geometry"]), _bconn)
        if _b is None:
            _b = _bench_compute(machine["geometry"], _bconn)
        result["passport"]["ldq0"] = {
            "Ld_mH": _b["Ld_mH"], "Lq_mH": _b["Lq_mH"],
            "I_probe_arms": _b.get("I_probe_arms"),
            "connection": _b.get("connection"),
        }
    except Exception:  # noqa: BLE001
        log.exception("catalog passport: bench Ld/Lq failed — passport ships "
                      "without inductances")
        result["passport"]["ldq0"] = None

    # Minutes have passed inside generate_passport.  Attach the passport to the
    # card as it exists NOW — writing back the whole document we read before the
    # run would undo every catalog edit made while it was running.
    def _m(d: dict) -> None:
        for m in d.get("motors", []):
            if m.get("id") == motor_id:
                m["passport"] = result
                return
        d.setdefault("motors", []).append({**motor, "passport": result})
    _mutate(_m)
    return {"status": "ok", "motor": motor_id, "coarse": coarse,
            "pwm": (result.get("passport") or {}).get("pwm", None) is not None,
            "passport": result}


@router.get("/{motor_id}/passport")
def get_motor_passport(motor_id: str,
                       authorization: Optional[str] = Header(default=None)):
    """Return a motor's stored passport (404 if it hasn't been generated yet)."""
    cat = _load()
    motor = next((m for m in cat.get("motors", []) if m.get("id") == motor_id), None)
    if not motor or _config_hidden_card(motor, authorization):
        raise HTTPException(status_code=404, detail=f"motor '{motor_id}' not found")
    passport = motor.get("passport")
    if not passport:
        raise HTTPException(status_code=404,
                            detail=f"motor '{motor_id}' has no passport — generate it first")
    from motor_ai_sim import passport_store as _ps
    _tmp = [{"name": motor.get("name"), "passport": passport}]
    _ps.attach(_tmp)
    return _tmp[0]["passport"]


# ---------------------------------------------------------------------------
# Configure's physical limits (owner 2026-10-05) — see motor_ai_sim.configure_limits
# ---------------------------------------------------------------------------

def _die_docs_of(fam: Optional[dict]) -> tuple:
    """``(configuration docs, die geometry)`` of the die a family document belongs to —
    every configuration the die has, found the way ``_family_doc_of_motor`` finds one."""
    die = str((fam or {}).get("die") or "").strip()
    if not die:
        return [], None
    try:
        import yaml as _yaml
        from motor_ai_sim.workspace import iter_dies as _iter_dies
        for e in _iter_dies():
            if str(e.get("die") or e.get("name") or "").casefold() != die.casefold():
                continue
            d = Path(str(e["dir"]))
            try:
                dy = _fsc_read_yaml(d / "die.yaml") or {}
            except Exception:                                   # noqa: BLE001
                dy = {}
            docs = []
            from motor_ai_sim import motor_access as _ma
            _acc = _ma.catalog_access(None)
            for f in sorted(d.glob("*.yaml")):
                if f.name == "die.yaml":
                    continue
                if not _ma.may_see_config_file(_acc, str(e.get("name") or die), f.stem, f,
                                               layer=e.get("layer")):
                    continue         # a hidden sibling configuration is not part of this die for the caller
                c = _fsc_read_yaml(f) or {}
                if isinstance(c, dict) and c.get("name"):
                    c.setdefault("die", die)
                    docs.append(c)
            return docs, (dy.get("geometry") if isinstance(dy.get("geometry"), dict) else None)
    except Exception:                                           # noqa: BLE001
        log.warning("configure presets: the die's configurations could not be read", exc_info=True)
    return [], None


@router.get("/{motor_id}/configure_context")
def get_configure_context(motor_id: str,
                          authorization: Optional[str] = Header(default=None)):
    """What Configure's sliders need to know about ONE machine: the hand-set
    stack-length maximum, the inverter's phase-current ceiling, the pack and the
    modulation index.  Read-only; open to every caller who can read the card
    (the numbers are limits, not data of the owner's).

    STORED (``answer_store``): built once per version of its inputs (``_configure_stamp``) and read back after
    that - the caller check (``_configure_motor``) runs on every request."""
    from motor_ai_sim import answer_store as _as
    return _as.json_response(configure_context_answer(motor_id, authorization))


def configure_context_answer(motor_id: str, authorization: Optional[str] = None) -> Dict[str, Any]:
    """The stored context as a dict (the route sends it to the wire; the warm-up and tests read it)."""
    motor, src = _configure_motor(motor_id, authorization)
    from motor_ai_sim import answer_store as _as
    return _as.get_or_compute("configure_context", {"motor": motor_id, "src": str(src)}, _configure_stamp(src),
                              lambda: _configure_context_of(motor_id, motor), persist=_shared_source(src))


def _configure_motor(motor_id: str, authorization: Optional[str]) -> tuple:
    """``(card, catalogue file it came from)`` of a motor the caller may read; 404 otherwise."""
    # The Configure picker may have received this card from the shared catalog
    # while the caller's workspace has its own unrelated catalog. Resolve in
    # the same workspace-first source order as references, keeping local cards
    # authoritative when IDs collide.
    loaded_path = _read_path()
    current = _load()
    motor = next((m for m in current.get("motors", []) if m.get("id") == motor_id), None)
    src = loaded_path
    if motor is None:
        for path in _reference_catalog_paths():
            if path == loaded_path:
                continue
            other = _read_json(path, {"tiers": [], "diameters_mm": [], "motors": []})
            motor = next((m for m in other.get("motors", [])
                          if m.get("id") == motor_id), None)
            if motor is not None:
                src = path
                break

    from motor_ai_sim.auth import caller_identity as _cid
    from motor_ai_sim.routes.presets import _owner_of as _own
    ident = _cid(authorization)
    if (not motor
            or (not ident.get("is_admin")
                and (motor.get("visibility") or "public") == "private"
                and _own(_backing_entry(motor)) != ident.get("id"))
            or not _grant_visible_cards([motor], authorization)):
        # 404 for a motor the account was not granted: the same answer as one
        # that does not exist, so the id is not an oracle.
        raise HTTPException(status_code=404, detail=f"motor '{motor_id}' not found")
    return motor, src


def _shared_source(src: Any) -> bool:
    """A card read from the SHARED catalogue may have its answers stored in the shared layer; a card of the
    caller's own workspace (private data) is never written there - its answers stay in this process."""
    try:
        from motor_ai_sim.workspace import shared_root
        return Path(str(src)).resolve() == (Path(str(shared_root())) / "motor_catalog.json").resolve()
    except Exception:                                           # noqa: BLE001
        return False


#: the code a stored Configure answer is computed by: a deploy that changes it invalidates every stored answer
_CONFIGURE_CODE = ("routes/catalog.py", "configure_limits.py", "configure_load.py", "configure_anchor.py",
                   "propeller_s1.py", "propeller.py", "cooling_options.py", "passport_store.py", "duty_class.py",
                   "duty_results.py", "applications.py", "routes/propellers.py", "modules/motor_mass.py")


def _configure_stamp(src: Any = None) -> str:
    """The cheap input hash of every stored Configure answer: the catalogue files, every die's configuration
    files, the passport store, the duty results, the cooling registry, the propeller cards, the S1 models, the
    material cards, the tombstones and the code - file versions only (stat calls, no parsing)."""
    from motor_ai_sim import fscache as _fsc
    paths: list = [src] if src else []
    dirs: list = []
    try:
        from motor_ai_sim.workspace import iter_dies as _iter_dies, shared_root
        from motor_ai_sim import duty_results as _dres
        for e in _iter_dies():
            d = Path(str(e["dir"]))
            paths += sorted(d.glob("*.yaml"))
            paths += _dres.store_paths(str(e.get("die") or e.get("name") or ""))
        sr = Path(str(shared_root()))
        paths += [sr / "cooling_options.yaml", sr / "materials_library.yaml"]
    except Exception:                                           # noqa: BLE001
        log.debug("configure stamp: dies unavailable", exc_info=True)
    try:
        from motor_ai_sim import cooling_options as _co, passport_store as _ps, propeller as _pp
        from motor_ai_sim import propeller_s1 as _s1
        from motor_ai_sim.workspace import _tombstones_file
        paths += [_co._FILE, _tombstones_file()]
        dirs += [_ps.store_dir(), *_pp.catalog_dirs(), *_s1.model_dirs()]
    except Exception:                                           # noqa: BLE001
        log.debug("configure stamp: stores unavailable", exc_info=True)
    try:                                                        # the CAD bills of materials: every preset's full mass
        from motor_ai_sim.modules import motor_mass as _mm
        for root in _mm._cad_dirs():
            if root.is_dir():
                paths += sorted(root.glob("*/*/*_BOM.csv"))
    except Exception:                                           # noqa: BLE001
        log.debug("configure stamp: CAD BOMs unavailable", exc_info=True)
    src = Path(__file__).resolve().parents[1]
    paths += [src / f for f in _CONFIGURE_CODE]
    return _fsc.stamp_of(sorted({str(x) for x in paths}), dirs, salt="configure-1")


def _configure_context_of(motor_id: str, motor: dict) -> Dict[str, Any]:
    """The context of one machine (no caller check - ``get_configure_context`` does it)."""
    from motor_ai_sim import configure_limits as _cl
    # Several configurations can share one die name ("CIANO14 40 new": L12, L20), and a
    # card carries no geometry of its own — the passport's base stack length is what
    # tells them apart.
    geo = motor.get("geometry")
    if not geo:
        L0 = ((motor.get("passport") or {}).get("passport") or {}).get("L0_mm")
        geo = {"motor_length": L0} if L0 is not None else None
    fam = _family_doc_of_motor(motor, geo)
    from motor_ai_sim import passport_store as _ps
    docs, die_geo = _die_docs_of(fam)
    presets = _cl.presets_for_die(
        docs, die_geo, lambda die, cfg: _ps.variants_for(f"{die} {cfg}", None))
    ctx = {"motor_id": motor_id,
           **_cl.context(motor, fam),
           # one preset per configuration of the die, read from the machines themselves
           "presets": presets}
    s1_die = str(((ctx.get("cooling") or {}) if isinstance(ctx.get("cooling"), dict) else {}).get("die") or "")
    for pr in presets:
        # does this configuration have a FULL passport card (v1 record)?
        pr["card_date"] = _ps.card_date(str(pr.get("die") or ""), str(pr.get("config") or ""))
        _mark_duty_classes(pr, docs)
        _drive_the_s1_model_carries(pr, s1_die)
        pr["mass"] = _preset_mass(str(pr.get("die") or ""), str(pr.get("config") or ""))
    return ctx


def _preset_mass(die: str, config: str) -> Optional[Dict[str, Any]]:
    """THE masses of one configuration as the server computes them (``modules.motor_mass.mass_summary``): Configure shows
    exactly these for the loaded build - "Mass" = ``full_kg``, "Active mass" = ``active_kg`` - and scales from them only when
    the user moves the build (owner 2026-10-19: 40 L20 showed 0.211 / 0.181 kg against the server's 0.181 / 0.151, because
    the page scaled from a passport ``mass0_kg`` that held the full mass).  ``None`` = the die cannot be weighed here."""
    if not die or not config:
        return None
    try:
        from motor_ai_sim.modules import motor_mass as _mm
        s = _mm.mass_summary(die, config)
    except Exception:                                           # noqa: BLE001 - a mass hiccup never breaks the context
        return None
    if not s:
        return None
    return {"full_kg": round(float(s["full_kg"]), 5), "active_kg": round(float(s["active_kg"]), 5),
            "partial": bool(s["partial"]), "partial_reason": s.get("partial_reason") or "",
            "scope": s.get("scope") or "full motor"}


def _drive_the_s1_model_carries(pr: dict, s1_die: str) -> None:
    """A default load never opens on a drive the configuration's S1 model does not carry (owner 2026-10-19: 50 L15
    opened on si_48k, whose S1 answer is a 422 - an empty load chart).  The preset then opens on Sine and says
    which drive it set aside (``drive_variant_unmodelled``); the drive stays selectable by hand."""
    dv = pr.get("drive_variant")
    if not dv or not s1_die:
        return
    try:
        from motor_ai_sim import propeller_s1 as _s1
        model = _s1.load_model(s1_die, str(pr.get("config") or ""))
    except Exception:                                           # noqa: BLE001 - no S1 model: nothing to agree with
        return
    if dv not in (model.get("drives") or {}):
        pr["drive_variant_unmodelled"] = dv
        pr["drive_variant"] = None


@router.get("/{motor_id}/configure_load")
def get_configure_load(motor_id: str,
                       config: str = Query(..., min_length=1, max_length=100),
                       duty: Optional[str] = Query(default=None, max_length=100,
                                                   description="the duty to open on; default rated"),
                       ambient_c: float = Query(default=25.0, ge=-60.0, le=80.0),
                       propeller: Optional[str] = Query(default=None, max_length=200,
                                                        description="the viewer's propeller; default the configuration's"),
                       housing_d_mm: Optional[float] = Query(default=None, gt=0.0, le=2000.0),
                       drive: Optional[str] = Query(default=None, max_length=100,
                                                    description="S1 drive; default the preset's own (its first PWM variant, else sine)"),
                       pack_v_min: Optional[float] = Query(default=None, gt=0.0, le=2000.0),
                       pack_v_max: Optional[float] = Query(default=None, gt=0.0, le=2000.0),
                       series_rpm_max: float = Query(default=40000.0, gt=0.0, le=60000.0),
                       series_n: int = Query(default=321, ge=3, le=400),
                       authorization: Optional[str] = Header(default=None)):
    """ONE Configure load (``motor_ai_sim.configure_load``): the context, the target
    configuration + duty, and - when the machine is propeller-cooled - the propeller it opens
    on, its rpm series with the housing film h and its motor S1 limit for the preset's drive
    and pack, in one answer, so the page renders the loaded point once.  The parts are the
    existing routes' own answers; a part that cannot be computed is ``null`` with its reason.

    STORED (owner 2026-10-19: "it must already be ready"): the whole answer is built once per version of its
    inputs (``_configure_stamp``) and kept (``answer_store``; the warm-up process builds the default loads of the
    public machines ahead of any request).  The caller check runs on every request, and the signed-in-only parts
    (the S1 limit and model, the duty anchor) are stripped for a caller who may not see them."""
    from motor_ai_sim import answer_store as _as
    return _as.json_response(configure_load_answer(
        motor_id, authorization, config=config, duty=duty, ambient_c=ambient_c, propeller=propeller,
        housing_d_mm=housing_d_mm, drive=drive, pack_v_min=pack_v_min, pack_v_max=pack_v_max,
        series_rpm_max=series_rpm_max, series_n=series_n))


def configure_load_answer(motor_id: str, authorization: Optional[str] = None, **params: Any) -> Dict[str, Any]:
    """The stored load as THIS caller may see it, as a dict (the route sends it to the wire; the warm-up reads it)."""
    motor, src = _configure_motor(motor_id, authorization)
    from motor_ai_sim import answer_store as _as
    keyp = {k: (round(float(v), 6) if isinstance(v, float) else v) for k, v in params.items()}
    full = _as.get_or_compute("configure_load", {"motor": motor_id, "src": str(src), **keyp}, _configure_stamp(src),
                              lambda: _configure_load_full(motor_id, motor, src, **params), persist=_shared_source(src))
    return _gate_configure_load(full, authorization)


def _gate_configure_load(full: Dict[str, Any], authorization: Optional[str]) -> Dict[str, Any]:
    """The stored answer as THIS caller may see it: the S1 limit / model and the duty anchor only for a signed-in
    caller who may see the configuration (the /s1 rule)."""
    from motor_ai_sim import motor_access as _ma
    gate = full.get("_gate") or {}
    out = {k: v for k, v in full.items() if k != "_gate"}
    acc = _ma.catalog_access(authorization)
    signed = acc.get("mode") != _ma.MODE_ANONYMOUS
    cfg = str(out.get("config") or "")
    if not (signed and gate.get("s1_die") and _ma.may_see_config(acc, str(gate["s1_die"]), cfg)):
        out["thermal_model"] = None
        if isinstance(out.get("propeller"), dict):
            pr = dict(out["propeller"])
            if pr.get("s1") is not None or pr.get("s1_error"):
                pr["s1"], pr["s1_error"] = None, "Sign in required."
            out["propeller"] = pr
    if not (signed and gate.get("anchor_die") is not None and _ma.may_see_config(acc, str(gate["anchor_die"]), cfg)):
        out["thermal_anchor"] = None
    return out


def _configure_load_full(motor_id: str, motor: dict, src: Any = None, *, config: str, duty: Optional[str], ambient_c: float,
                         propeller: Optional[str], housing_d_mm: Optional[float], drive: Optional[str],
                         pack_v_min: Optional[float], pack_v_max: Optional[float], series_rpm_max: float,
                         series_n: int) -> Dict[str, Any]:
    """The whole answer, every part computed (no caller gate: ``_gate_configure_load`` applies it on serving)."""
    from motor_ai_sim import answer_store as _as
    from motor_ai_sim import configure_load as _ld
    from motor_ai_sim import propeller as _pp
    from motor_ai_sim.routes import propellers as _prop_routes
    ctx = _as.get_or_compute("configure_context", {"motor": motor_id, "src": str(src)}, _configure_stamp(src),
                             lambda: _configure_context_of(motor_id, motor), persist=_shared_source(src))
    preset = next((p for p in ctx.get("presets") or [] if p.get("config") == config), None)
    if preset is None:
        raise HTTPException(status_code=404, detail=f"motor '{motor_id}' has no configuration '{config}'")
    mode = _ld.pick_mode(preset, duty)
    die0 = str(preset.get("die") or "")
    out: Dict[str, Any] = {"motor_id": motor_id, "context": ctx, "config": config,
                           "duty": (mode or {}).get("name") or preset.get("duty"),
                           "propeller": None, "thermal_model": None, "thermal_anchor": None,
                           "_gate": {"s1_die": None, "anchor_die": die0}}
    # The duty's coupled FEM record anchors the page's lumped network wherever no calibrated S1 network
    # applies (still air / robotics, typed air): motor_ai_sim.configure_anchor.  Gated on serving.
    try:
        from motor_ai_sim import configure_anchor as _anc
        from motor_ai_sim import duty_results as _dres
        if out["duty"]:
            out["thermal_anchor"] = _anc.anchor_from_record(
                str(out["duty"]), (_dres.get(die0, config) or {}).get(str(out["duty"])))
    except Exception:                                           # noqa: BLE001
        log.debug("configure_load: no thermal anchor", exc_info=True)
    cooling = ctx.get("cooling") if isinstance(ctx.get("cooling"), dict) else {}
    try:
        catalog = _pp.load_catalog()
    except Exception:                                           # noqa: BLE001
        catalog = {}
    pid = _ld.pick_propeller(cooling, config, propeller, catalog)
    if not pid:
        return out
    housing = housing_d_mm          # the page's housing diameter (its film h uses the same)
    pack = ({"min": pack_v_min, "max": pack_v_max} if pack_v_min and pack_v_max
            else _ld.preset_pack(preset))
    # THE thermal model of this configuration (its S1 model: passport losses + the calibrated network,
    # owner 2026-10-19 "one model only"): the page evaluates it for every knob position itself, and the
    # air over the housing is the model's own (its fitted slipstream factor), so the series below, the S1
    # limit and the tiles all see the same air.  Gated on serving like /s1.
    thermal_model = None
    available = ["sine"]
    if cooling.get("die"):
        from motor_ai_sim import propeller_s1 as _s1
        out["_gate"]["s1_die"] = str(cooling["die"])
        try:
            model = _s1.load_model(str(cooling["die"]), config)
            thermal_model = _s1.configure_thermal_model(model)
            available += sorted((model.get("drives") or {}).keys())
        except (_s1.S1ModelNotFound, _s1.S1Error):
            thermal_model = None
    out["thermal_model"] = thermal_model
    # the S1 drive: the one asked for, else the preset's own - only one the S1 model carries, else Sine
    wanted = drive or preset.get("drive_variant") or preset.get("drive_variant_unmodelled") or "sine"
    s1_drive = wanted if wanted in available else "sine"
    factor = (thermal_model or {}).get("slipstream_factor")
    prop_out: Dict[str, Any] = {"id": pid, "ambient_c": ambient_c, "housing_d_mm": housing,
                                "series_rpm_max": series_rpm_max, "series_n": series_n,
                                "slipstream_factor": factor,
                                "die": cooling.get("die"), "config": config,
                                "drive": s1_drive, "drive_wanted": wanted, "drives_available": available,
                                "pack": pack,
                                "series": None, "series_error": None, "s1": None, "s1_error": None}
    try:
        prop_out["series"] = _prop_routes.get_series(
            pid, rpm_max=series_rpm_max, n=series_n, temp_c=ambient_c, altitude_m=0.0,
            pressure_pa=None, position=None, factor=factor, housing_d_mm=housing)
    except HTTPException as exc:
        prop_out["series_error"] = str(exc.detail)
    if cooling.get("die"):
        try:
            prop_out["s1"] = _prop_routes.s1_answer(
                pid, str(cooling["die"]), config, ambient_c=ambient_c, altitude_m=0.0,
                position=None, factor=None, n=41, drive=s1_drive,
                pack_v_min=pack["min"] if pack else None, pack_v_max=pack["max"] if pack else None)
        except HTTPException as exc:
            prop_out["s1_error"] = str(exc.detail)
    out["propeller"] = prop_out
    return out


def _mark_duty_classes(pr: dict, docs: list) -> None:
    """SHORT-TIME OR CONTINUOUS on every mode of a Configure preset (owner
    2026-10-19): ``duty_class`` and ``duty_label`` on each ``duty_points``
    entry, read off the duty's stored coupled record.  Never fails the
    context — a mode with nothing stored simply carries no class."""
    try:
        from motor_ai_sim import duty_class as _dcl
        from motor_ai_sim import duty_results as _dres
        cfg = str(pr.get("config") or "")
        doc = next((d for d in docs if isinstance(d, dict)
                    and str(d.get("name") or "") == cfg), {}) or {}
        rec = _dres.get(str(pr.get("die") or ""), cfg) or {}
        for dp in pr.get("duty_points") or []:
            n = str(dp.get("name") or "")
            entry = next((x for x in (doc.get("duties") or [])
                          if isinstance(x, dict) and x.get("name") == n), None)
            k = _dcl.classify(entry, (rec.get(n) or {}).get("coupled"))
            if k:
                dp["duty_class"] = k["duty_class"]
                dp["duty_label"] = _dcl.label(n, k)
    except Exception:                                       # noqa: BLE001
        log.debug("duty classes for the presets could not be read",
                  exc_info=True)


@router.patch("/{motor_id}/configure_limits")
def set_configure_limits(motor_id: str, body: dict,
                         authorization: Optional[str] = Header(default=None)):
    """Admin: set (or clear with ``null``) the stack-length maximum of ONE
    machine.  Stored on its catalogue card beside the passport; Configure reads
    it as the physical maximum of its length slider."""
    from datetime import datetime
    from motor_ai_sim import configure_limits as _cl
    ident = _caller_identity(authorization)
    if not ident.get("is_admin"):
        raise HTTPException(status_code=403,
                            detail="only an admin can set a machine's Configure limits")
    if "L_max_mm" not in (body or {}):
        raise HTTPException(status_code=422, detail="send {\"L_max_mm\": number | null}")
    try:
        v = _cl.validate_l_max(body.get("L_max_mm"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    found = {"ok": False}

    def _m(d: dict) -> None:
        for m in d.get("motors", []):
            if m.get("id") == motor_id:
                found["ok"] = True
                if v is None:
                    m.pop("configure_limits", None)
                else:
                    m["configure_limits"] = {
                        "L_max_mm": v, "set_by": ident.get("id"),
                        "set_at": datetime.now().isoformat(timespec="seconds")}
                return
    _mutate(_m)
    if not found["ok"]:
        raise HTTPException(status_code=404, detail=f"motor '{motor_id}' not found")
    return {"ok": True, "motor_id": motor_id, "L_max_mm": v}
