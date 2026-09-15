"""Motor catalog — the public 'MOTORS' menu.

A curated table of ready-made motors, organised by stator diameter, plus the
subscription tiers.  Loading a catalog motor applies its underlying preset
(geometry + operating point) via the presets service.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from fastapi import APIRouter, Header, HTTPException

import logging

from motor_ai_sim.auth import caller_identity as _caller_identity
from motor_ai_sim.json_store import mutate_json as _mutate_json, read_json as _read_json

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


def _load() -> dict:
    return _read_json(_catalog_path(), {"tiers": [], "diameters_mm": [], "motors": []})


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
        cat["motors"] = [m for m in cat.get("motors", []) if _visible(m)]
        cat["by_diameter"] = {d: [i for i in ids
                                  if any(m["id"] == i for m in cat["motors"])]
                              for d, ids in cat["by_diameter"].items()}
    return cat


@router.post("/{motor_id}/load")
def load_motor(motor_id: str,
               authorization: Optional[str] = Header(default=None)):
    """Load a catalog motor into the app by applying its underlying preset."""
    cat = _load()
    motor = next((m for m in cat.get("motors", []) if m.get("id") == motor_id), None)
    if not motor:
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
    if not src:
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
        from motor_ai_sim.workspace import root as _ws_root
        dies = _ws_root() / "dies"
        by_die: dict = {}
        for f in dies.glob("*/*.yaml"):
            if f.name == "die.yaml":
                continue
            d = _yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            if f"{d.get('die')} {d.get('name')}".casefold() == name:
                return d
            by_die.setdefault(str(d.get("die") or "").casefold(), []).append(d)
        # FALLBACK: older cards carry the DIE name alone ("CIANO14 40 new",
        # whose configuration is "L12") — the exact "<die> <config>" match then
        # fails and the machine silently loses its pack and its role.  One
        # configuration under that die is unambiguous; several are separated by
        # the STACK LENGTH, which is what a configuration of a shared die is
        # (the CILN28 series: 40 / 160 / 220 mm on one lamination).  Still
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
        from motor_ai_sim.workspace import root as _ws_root
        dies = _ws_root() / "dies"
        for f in dies.glob("*/*.yaml"):
            if f.name == "die.yaml":
                continue
            d = _yaml.safe_load(f.read_text(encoding="utf-8")) or {}
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
        # ZERO live-state touch (user 2026-08-25 "чтобы он мне не мешал"):
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
        # regenerated as motors while two CILN28 machines are generators).
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
                "gap = float(g.get('air_gap') or 0.5)\n"
                "od = float(g.get('stator_diameter') or 100.0)\n"
                "from motor_ai_sim.simulation.static3d.end_effect import run_stage_a\n"
                "p = run_stage_a(geo_override=g, n_stack=4, n_cap=6,\n"
                "                h_gap=max(0.28, min(0.6, gap)),\n"
                "                h_solid=max(0.8, min(1.6, od / 100.0)),\n"
                "                order=2, l_factors=(0.75, 1.0, 1.5),\n"
                "                do_bracket=False, do_2d=True,\n"
                "                tol=3e-3, max_iter=45, verbose=False)\n"
                "out = {k: p.get(k) for k in ('k_flux', 'k_flux_self',\n"
                "                             'generated_utc', 'l_stack_curve')}\n"
                "print('PASSPORT3D:' + json.dumps(out, default=float))\n")
            try:
                _pr = _sp.run([_sys.executable, "-c", _child, _src, _mfile],
                              capture_output=True, text=True, timeout=7200)
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
def get_motor_passport(motor_id: str):
    """Return a motor's stored passport (404 if it hasn't been generated yet)."""
    cat = _load()
    motor = next((m for m in cat.get("motors", []) if m.get("id") == motor_id), None)
    if not motor:
        raise HTTPException(status_code=404, detail=f"motor '{motor_id}' not found")
    passport = motor.get("passport")
    if not passport:
        raise HTTPException(status_code=404,
                            detail=f"motor '{motor_id}' has no passport — generate it first")
    return passport
