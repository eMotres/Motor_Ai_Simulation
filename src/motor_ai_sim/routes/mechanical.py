"""Mechanical (structural) routes — /api/mechanical.

Added 2026-09-05 for the user's Mechanical tab: "начнём с расчёта центробежных
сил ротора ... чтобы оценить какой бандаж нужен для удержания магнитов".

The solve itself lives in ``simulation.mechanical.rotor_stress``; this module
only resolves WHICH machine and WHICH materials the request means, and caches
the answer.  It follows the simulation routes exactly: ``?geo=`` is a
per-request geometry override, ``?mat=`` a per-request material override applied
by a router dependency, and neither ever writes the shared config.
"""
from __future__ import annotations

import logging
import math
import os
import time
from collections import OrderedDict
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from motor_ai_sim import workspace as _WSP
from motor_ai_sim import jobs as _JOBS
from motor_ai_sim import run_history as _RH
from motor_ai_sim.progress import poll as _progress_poll
from motor_ai_sim.progress import route_progress as _route_progress

log = logging.getLogger(__name__)


async def _material_override_dep(mat: Optional[str] = Query(default=None)):
    """Apply this request's ``?mat=`` override, same contract as the simulation
    router: a malformed payload is a 422 from the shared parser, an assignment
    naming a material that does not exist is a 400."""
    from motor_ai_sim.material_context import set_request_materials
    from motor_ai_sim.routes._validation import parse_mat_override

    ov = parse_mat_override(mat)
    if ov and ov.get("assignment"):
        from motor_ai_sim.materials import (UnknownMaterialError,
                                            validate_assignment)
        try:
            validate_assignment(ov["assignment"],
                                known_extra=set(ov.get("materials") or ()))
        except UnknownMaterialError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    set_request_materials(ov)


router = APIRouter(prefix="/api/mechanical", tags=["mechanical"],
                   dependencies=[Depends(_material_override_dep)])


# ---------------------------------------------------------------------------
# Live progress
# ---------------------------------------------------------------------------
# Added 2026-09-07.  Every route in this router is a real FEM solve — a gmsh
# build plus one nonlinear contact solve per case, a sparse eigensolve, a 41-
# point Campbell sweep — and until now they reported NOTHING until they
# returned: a spinner that never moves, which on a minute-long solve is
# indistinguishable from a hung server.  The Simulation tab has had a real
# progress strip for months (`GET /api/simulation/physics/fem_transient/progress`);
# this gives the Mechanical tab the SAME one, from the same arithmetic, so the
# frontend has one strip component and one contract.
#
# ONE tracker for the router, not one per route: only one solve runs per tab at
# a time (the panel's buttons are mutually exclusive while a solve is in
# flight), and a single object is what a poll endpoint can read without knowing
# which button was pressed.  `kind` is what says which one it was.
#
# Migration Stage 4: the NAME is unchanged; what it resolves to is now the
# tracker of the RUN this call is inside (``progress.RouteProgress``), falling
# back to this router's per-workspace default — which is exactly the object this
# global used to be, so a single-user server sees no change at all.
_progress = _route_progress("mechanical")


@router.get("/progress")
def progress(run_id: str = ""):
    """What this router is solving right now — polled at ~500 ms by the panel.

    The payload is byte-for-byte the transient endpoint's shape (see
    ``motor_ai_sim.progress``) plus ``kind``, so the UI can say "Rotor stress —
    case 2/3 — 18 s elapsed, ETA 31 s" instead of "Solving…".  Cheap by
    construction: it reads one dict under a lock and never touches the solvers.

    Deliberately NOT gated (see ``auth._GATED``): it is the same bargain the
    transient's progress route strikes — the SOLVE is gated, and its progress
    counter is a status read with no physics in it.  Gating the poll would make
    a bar that 401s over a solve the user is already paying for.

    ``?run_id=`` answers THAT run (Stage 4); with no argument it answers the
    caller's newest run in this router, which is what today's strip polls.
    """
    return _progress_poll("mechanical", run_id)


# ---------------------------------------------------------------------------
# Request resolution
# ---------------------------------------------------------------------------

def _live_polys(geo: Optional[str]):
    """The rotor cross-section for THIS request, with ``?geo=`` applied.

    Read-only, and it goes through the SAME resolution the mesh route uses
    (``merge_geo_override`` on the live geometry, then ``set_parameters``) —
    a plain dict update would put one machine's primaries under another's
    derived radii, which is exactly the chimera the mesher's comments warn
    about.  A malformed ``geo`` is a 422 from the shared parser, never a silent
    fallback to the shared config.
    """
    from motor_ai_sim.cadquery_geometry import CadQueryMotor
    from motor_ai_sim.routes._validation import parse_geo_override
    from motor_ai_sim.services.geometry_service import get_current_geometry
    from motor_ai_sim.simulation.geometry_2d import merge_geo_override

    ov = parse_geo_override(geo)
    params = merge_geo_override(get_current_geometry().to_dict(), ov)
    motor = CadQueryMotor()
    motor.set_parameters(params)
    return motor.get_2d_polygons(0.0), motor, ov


def _assignments() -> Dict[str, str]:
    """part -> material name: the config's ``materials:`` block, then this
    request's override.  Same precedence the solver uses."""
    out: Dict[str, str] = {}
    try:
        from motor_ai_sim.config import get_material_assignments
        out.update({k: v for k, v in (get_material_assignments() or {}).items() if v})
    except Exception:  # noqa: BLE001 - no config is not a reason to have no answer
        pass
    try:
        from motor_ai_sim.material_context import get_request_materials
        ov = (get_request_materials() or {}).get("assignment") or {}
        out.update({k: v for k, v in ov.items() if v})
    except Exception:  # noqa: BLE001
        pass
    return out


def _override_props() -> Dict[str, dict]:
    try:
        from motor_ai_sim.material_context import get_request_materials
        return dict((get_request_materials() or {}).get("materials") or {})
    except Exception:  # noqa: BLE001
        return {}


def _elapsed(t0: float) -> float:
    """Seconds this request spent working, as the panel's timer reports them.

    User 2026-09-06: "нужно добавить ещё индикатор времени расчёта".  The client
    can time its own fetch, but that number includes the network and the JSON —
    on a 40 MB field payload those are seconds of their own — so the honest
    figure is measured here, around the geometry build and the solve, and it is
    what the panel prints as "solved in 48 s".  Floored at 1 ms so "how long did
    it take" is never answered with a zero that reads as "it did not run".
    """
    return round(max(time.time() - t0, 1e-3), 3)


def _default_rpm() -> float:
    try:
        from motor_ai_sim.config import get_config
        return float(((get_config() or {}).get("simulation") or {}).get("rpm") or 0.0)
    except Exception:  # noqa: BLE001
        return 0.0


def _default_torque_nm() -> tuple:
    """(mean torque of the last Simulation run, where it came from).

    User 2026-09-07: "добавь ещё и момент на ротор, пусть действуют все силы".
    The torque is a RESULT, not a setting, so it is read from the last transient
    the Simulation tab produced — never invented here, and never typed into a
    default in a panel (the standing rule that every physics value comes from
    the Simulation tab).

    Two lookups, in this order, and the source string says which answered:

      * ``presets._last_transient_summary()`` — the persisted last run, guarded
        by the geometry fingerprint, i.e. THIS machine's torque;
      * the in-memory ``_last_transient_ref`` — whatever ran last in this
        process, whichever machine it was.  Reported as `stale` so the panel can
        show it next to an editable field rather than pretend it is this rotor's.

    ``(None, "none")`` when nothing has been run: the caller then solves the
    centrifugal case alone rather than a made-up torque.
    """
    try:
        from motor_ai_sim.routes.presets import _last_transient_summary
        res = _last_transient_summary() or {}
        v = res.get("T_avg_Nm")
        if v is not None and float(v) != 0.0:
            return abs(float(v)), "last run"
    except Exception:  # noqa: BLE001 - a missing run is not an error
        pass
    try:
        from motor_ai_sim.routes.simulation import _last_transient_ref
        res = (_last_transient_ref.get("result") or {})
        v = res.get("T_avg_Nm")
        if v is not None and float(v) != 0.0:
            return abs(float(v)), "last run (another geometry)"
    except Exception:  # noqa: BLE001
        pass
    return None, "none"


def _f_switch(explicit: Optional[float] = None) -> Optional[float]:
    """The inverter carrier, for the excitation table.

    THE RUN BEING SOLVED OWNS THE CARRIER (2026-09-14).  ``explicit`` is the
    carrier the caller is solving at — the coupled orchestrator passes the
    duty's own ``sim.fSwitch`` — and it wins outright, ``0`` included (that is
    "this duty has no PWM line", not "go and look somewhere else").  Only when
    nothing is passed at all does this fall back to the process-global
    ``simulation`` block, which is right for the interactive Mechanical tab (the
    machine on screen IS the machine being solved) and wrong for anything that
    solves another machine's duty: the Ø85's 48 kHz leaked into the Ø200 L155
    rated duty's ring-mode table exactly that way.

    ``None`` when the machine is not being driven by a PWM inverter, in which
    case no carrier line is drawn rather than a made-up 20 kHz.
    """
    if explicit is not None:
        try:
            v = float(explicit)
        except (TypeError, ValueError):
            return None
        return v if v > 0 else None
    try:
        from motor_ai_sim.config import get_config
        v = ((get_config() or {}).get("simulation") or {}).get("f_switch")
        return float(v) if v and float(v) > 0 else None
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
# Keyed by the machine fingerprint + the material assignment + the three inputs.
# `clear_mechanical_caches` is called from routes.simulation.clear_simulation_caches,
# so a geometry PUT / material PATCH / Run drops this too: the answer describes a
# cross-section, and a stale cross-section is a wrong sleeve.

def clear_mechanical_caches(reason: str = "") -> int:
    from motor_ai_sim.simulation.mechanical import modal as mdm
    from motor_ai_sim.simulation.mechanical import rotor_stress as rsm
    from motor_ai_sim.simulation.mechanical import rotordynamics as rdm
    # The modal and rotordynamic answers describe the same cross-section the
    # stress answer does, so a geometry PUT / material PATCH / Run must drop all
    # three together — a stale mode frequency is a stale cross-section.
    n = rsm.clear_cache() + mdm.clear_cache() + rdm.clear_cache()
    n += len(_MESH_CACHE)
    _MESH_CACHE.clear()
    # `_LAST` is deliberately NOT cleared here.  It is not a cache — it is what
    # the panel SHOWS when you come back to the tab, and the user's ask
    # (2026-09-06) is exactly that it survives: "если есть [расчёты] —
    # подгружается последний расчёт; если были изменения текущей геометрии —
    # нужно подсвечивать неактуальность текущего расчёта".  Throwing it away on
    # a geometry edit would replace the badge with a blank page, which is the
    # bug, not the fix.  Every /last response carries the fingerprint it was
    # solved for so the staleness is stated rather than hidden.
    if n:
        log.info("mechanical caches cleared (%s): %d entries", reason or "?", n)
    return n


# ---------------------------------------------------------------------------
# The LAST result, kept across tab switches and backend restarts
# ---------------------------------------------------------------------------
# User 2026-09-06: "когда я захожу и выхожу в Mechanical, графики пропадают.
# Нужно, чтобы по умолчанию: если нет расчётов — рисуется просто геометрия;
# если есть — подгружается последний расчёт".
#
# Same shape as the Simulation tab's last transient
# (`routes.simulation._store_transient_field_snapshot`): one in-memory entry per
# kind, persisted beside the config as a pickle (the payloads are plain lists,
# but a pickle keeps this identical to the transient store and costs nothing),
# written off-thread and atomically, loaded LAZILY at the first /last request so
# importing this module never touches the disk.

_LAST_KINDS = ("rotor_stress", "modes", "critical_speeds")
#: Migration Stage 3: per WORKSPACE.  Three kinds; the cap is a safety valve.
_LAST_MAX = 16
_LAST = _WSP.ws_map("mechanical.last", _LAST_MAX)
_SLOT_LAST_LOADED = "mechanical.last_loaded"
_UNSET = object()


def _last_loaded() -> bool:
    ov = globals().get("_LAST_LOADED", _UNSET)
    if ov is not _UNSET and _WSP.module_override_applies():
        return bool(ov)
    return bool(_WSP.state().flag(_SLOT_LAST_LOADED, False))


def _set_last_loaded(value: bool) -> None:
    if "_LAST_LOADED" in globals() and _WSP.module_override_applies():
        globals()["_LAST_LOADED"] = value
    else:
        _WSP.state().set_flag(_SLOT_LAST_LOADED, value)


def __getattr__(name):
    """``_LAST_LOADED`` survives as a NAME — per workspace, and overridable by
    a plain assignment for the tests that already do that."""
    if name == "_LAST_LOADED":
        return bool(_WSP.state().flag(_SLOT_LAST_LOADED, False))
    raise AttributeError(name)


def _last_store_path() -> str:
    try:
        # Stage 1: the caller's WORKSPACE, which with none set is
        # ``Path(DEFAULT_CONFIG_PATH).parent`` — the old expression exactly.
        from motor_ai_sim.workspace import root as _ws_root
        base = str(_ws_root())
    except Exception:  # noqa: BLE001
        base = os.path.join(os.path.dirname(__file__), "..", "..", "..", "config")
    return os.path.abspath(os.path.join(base, ".last_mechanical.pkl"))


def _persist_last() -> None:
    """Write the whole store out, off-thread and atomically."""
    import threading

    # Taken under the store's OWN lock (``workspace.BoundedStore.snapshot``).
    # A plain ``{k: v for k, v in _LAST.items()}`` walks the live OrderedDict,
    # and a concurrent solve — or this store's own cap-eviction — resizing it
    # mid-walk raised "dictionary changed size during iteration", which this
    # function caught, logged and swallowed: the pickle was never written and
    # the tab came back blank after a restart.  A plain dict (what two dozen
    # tests monkeypatch in) has no ``snapshot``; ``dict()`` is the same copy.
    _snap = getattr(_LAST, "snapshot", None)
    snapshot = _snap() if callable(_snap) else dict(_LAST)

    def _write():
        try:
            import pickle as pk
            p = _last_store_path()
            with open(p + ".tmp", "wb") as fh:
                pk.dump(snapshot, fh, protocol=pk.HIGHEST_PROTOCOL)
            os.replace(p + ".tmp", p)
        except Exception as exc:  # noqa: BLE001 - a viewer convenience never breaks a solve
            log.warning("could not persist the last mechanical result: %s", exc)

    # Stage 3: the path is resolved inside the thread — carry the workspace.
    threading.Thread(target=_WSP.bind(_write), daemon=True).start()


def _load_last() -> None:
    if _last_loaded():
        return
    _set_last_loaded(True)
    try:
        import pickle as pk
        p = _last_store_path()
        if not os.path.exists(p):
            return
        with open(p, "rb") as fh:
            blob = pk.load(fh)
        if isinstance(blob, dict):
            for k in _LAST_KINDS:
                e = blob.get(k)
                if isinstance(e, dict) and e.get("result") is not None:
                    _LAST.setdefault(k, e)
            log.info("restored the last mechanical result(s) from %s: %s",
                     p, ", ".join(sorted(_LAST)) or "none")
    except Exception as exc:  # noqa: BLE001
        log.warning("could not restore the last mechanical result: %s", exc)


def _remember_last(kind: str, result: Dict[str, Any], params: Dict[str, Any],
                   fp: Optional[str]) -> None:
    """Park THIS answer as "what the Mechanical tab was last showing".

    …unless the run was marked ``record: false`` (``run_recording``): a solve
    made on another duty's behalf is nobody's last answer — no ``_LAST``, no
    ``config/.last_mech.pkl``, no per-duty row, no per-duty field.
    """
    import datetime as _dt
    from motor_ai_sim import run_recording as _rr
    if _rr.suppressed():
        log.info("mechanical: %s solved for another duty (record: false) — "
                 "not remembered as this machine's last result", kind)
        return
    try:
        _load_last()          # never let a lazy load overwrite what we just stored
        _LAST[kind] = {
            # A PRIVATE shallow copy, and this is the whole bug of 2026-09-15.
            # Every cache-hit branch above publishes its answer here and THEN
            # edits it — ``out["cached"] = True``, ``out.pop("field", None)``
            # (the modal route, the critical-speed route) — while
            # ``_persist_last`` below is already pickling that very dict on a
            # background thread.  ``pickle`` walks a dict it is handed, so the
            # request thread resizing it out from under the writer raised
            #     RuntimeError: dictionary changed size during iteration
            # which ``_persist_last`` caught, logged at WARNING and swallowed:
            # the pickle was never written and the tab came back blank after a
            # restart.  The copy is ~20 keys wide (the big payloads are shared,
            # and nothing mutates those in place), and it also makes the store
            # keep what line 1321 already says it should — the ANSWER, not the
            # ``cached`` flag of the request that happened to re-serve it.
            "result": dict(result) if isinstance(result, dict) else result,
            # The request that produced it, so re-entering the tab restores the
            # INPUT fields too and a Solve press reproduces the picture.
            "params": dict(params),
            "geometry_fingerprint": fp,
            "computed_at": _dt.datetime.now(_dt.timezone.utc)
                              .isoformat(timespec="seconds"),
        }
        _persist_last()
    except Exception as exc:  # noqa: BLE001
        log.warning("could not remember the last mechanical %s: %s", kind, exc)
    # ── and once more, PER DUTY (2026-09-09) ────────────────────────────────
    # ``_LAST`` holds ONE answer per machine — the duty that was solved last —
    # so a report of a configuration with several duties had no honest way to
    # show a mechanical column for the others.  A compact copy (per-part stress
    # rows, safety factors, contacts, seating, fit — no per-element field) is
    # filed under the duty the catalog context names, and a duty with nothing
    # filed prints "not solved" rather than borrowing its neighbour's number.
    try:
        from motor_ai_sim import duty_results as _dr
        _dr.note_mechanical(kind, result, dict(params), fp,
                            (_LAST.get(kind) or {}).get("computed_at"))
    except Exception:  # noqa: BLE001 — bookkeeping never fails a solve
        log.debug("mechanical: per-duty result not recorded", exc_info=True)
    # ── …and the STRESS FIELD itself, per duty (2026-09-09) ─────────────────
    # The row above is the table; the report also draws each duty's own von
    # Mises map (user: *"давай сделаем сохранение всех полей моделирования, как
    # электромагнитных, так и тепловых и механических"*).  ``duty_fields`` keeps
    # the mesh, the primary case's vm / principal / safety factor, the
    # displacement and the contact segments — ~0.31 MB compressed on the 200 mm
    # rotor, against the 4 MB the machine-level pickle costs.  ``modes`` keeps
    # its shapes too since 2026-09-11 (the report draws a gallery of them);
    # ``critical_speeds`` has no map — its answer is a frequency list, which
    # the compact row above already carries whole.
    if kind in ("rotor_stress", "modes"):
        try:
            from motor_ai_sim import duty_fields as _df
            _df.save_active(kind, result, geometry_fingerprint=fp,
                            computed_at=(_LAST.get(kind) or {}).get("computed_at"))
        except Exception:  # noqa: BLE001 — a stored map never fails a solve
            log.debug("mechanical: per-duty field not stored", exc_info=True)


def _live_fingerprint(geo_ov) -> Optional[str]:
    try:
        from motor_ai_sim.routes.simulation import _geometry_fingerprint
        return _geometry_fingerprint(geo_ov)
    except Exception:  # noqa: BLE001
        return None


# The geometry-only mesh, so the tab can draw the rotor before anything is
# solved.  Small (one mesh, no fields), keyed exactly like the solve caches.
_MESH_CACHE_MAX = 4
_MESH_CACHE = _WSP.ws_map("mechanical.mesh_cache", _MESH_CACHE_MAX,
                          lru_on_read=True)


def _num_poles(motor) -> Optional[int]:
    """The machine's pole count, or None — the FALLBACK for a sector's ``n``.

    Only ever used when the polygons carry no magnets to read the periodicity
    from (``symmetry.detect_periodicity``); whenever there are magnets, they
    decide, because they are the features that are actually drawn.  Never
    raises: a geometry without the key simply has no fallback and the sector
    request is refused by name (2026-09-09).
    """
    try:
        n = int(motor.parameters.get("num_poles") or 0)
        return n if n >= 2 else None
    except Exception:  # noqa: BLE001
        return None


def _cache_key(geo_ov, assign, rpm, osf, interf, mesh_mm, order, contacts,
               cases: str = "three", loads: str = "centrifugal",
               torque_nm: float = 0.0,
               rotor_temp_c: float = 20.0, sleeve_temp_c: float = 20.0,
               part_temps_c: Optional[Dict[str, float]] = None,
               symmetry: str = "full") -> tuple:
    from motor_ai_sim.routes.simulation import _geometry_fingerprint
    key = (_geometry_fingerprint(geo_ov),
            tuple(sorted((str(k), str(v)) for k, v in assign.items())),
            tuple(sorted(_override_props().keys())),
            round(float(rpm), 6), round(float(osf), 6), round(float(interf), 6),
            round(float(mesh_mm), 4), int(order),
            # The case table is part of the ANSWER, not a view of it: a
            # single-speed run solves one case and reports no standstill, so it
            # must never be served from a three-case entry (2026-09-06).
            str(cases),
            # WHICH forces acted, and how big the torque was: two answers about
            # the same rotor under different loads are two answers (2026-09-07).
            str(loads), round(float(torque_nm or 0.0), 6),
            # The temperatures are part of the ANSWER too (2026-09-07): a hot
            # rotor is a tighter fit and a more loaded sleeve, so serving a
            # 20 °C entry for a 150 °C request would hand back a different
            # machine's stress.
            round(float(rotor_temp_c), 4), round(float(sleeve_temp_c), 4),
            # The contact settings ARE the model, not a display option: a
            # bonded magnet and a separated one are different machines.
            tuple(sorted((k, v.type, round(float(v.mu), 4))
                         for k, v in contacts.items())))
    # The PER-PART temperatures (2026-09-08) extend the key only when they were
    # actually asked for — the same rule every other optional field on this
    # router follows.  A request that names none is keyed exactly as it was
    # before this parameter existed, so nothing already in the cache is orphaned
    # and no user pays for a re-solve of an answer that has not changed.
    if part_temps_c:
        key = key + (tuple(sorted((str(k), round(float(v), 4))
                                  for k, v in part_temps_c.items())),)
    # The SYMMETRY reduction is the model, not a view of it: a sector answer
    # and a full answer are different solves of the same machine, and serving
    # one for the other would hand back a different mesh's field.  Appended
    # only when it is not the default (2026-09-09), so every key already in the
    # cache keeps the exact tuple it was filed under.
    if str(symmetry or "full").strip().lower() != "full":
        key = key + (str(symmetry).strip().lower(),)
    return key


# ---------------------------------------------------------------------------
# Persistent history — "don't recompute an identical rotor_stress request"
# ---------------------------------------------------------------------------
# Owner, 2026-09-22: a repeat launch of the SAME parameters must load the
# stored answer, not solve again — and survive a backend restart, unlike
# ``rsm.cache_get``/``cache_put`` above (an in-memory, per-process table that
# already makes a same-session Solve press instant but forgets everything on
# restart).  ``run_history`` is the persistent layer; ``_cache_key``'s tuple
# IS the canonical, already-normalised structure the brief asks for (every
# float rounded, every unordered collection sorted — see its own docstring
# for the enumerated field list: geometry, material assignment + overrides,
# rpm, overspeed factor, interference, mesh, order, the case table, which
# loads act and the torque, per-part temperatures, the contact spec, the
# symmetry reduction), so it is hashed AS IS rather than re-specified here.
_ROTOR_STRESS_HISTORY = _RH.history_for("mechanical.rotor_stress")
_RH.register_kind("mechanical.rotor_stress")


def _rotor_stress_history_key(key: tuple) -> str:
    return _RH.make_key("mechanical.rotor_stress", key)


def _load_rotor_stress_history_entry(entry: Dict[str, Any],
                                     payload: Dict[str, Any]) -> Dict[str, Any]:
    """``run_history.register_loader`` hook: make a History-popover click on a
    rotor_stress row behave exactly like the route's own history hit —
    the SAME response shape, and the Mechanical tab's /last restores from it
    too (2026-09-22, "Dashboard stale verdict must treat a loaded entry like
    a fresh result")."""
    out = dict(payload)
    out["cached"] = True
    out["served_from_history"] = True
    out["computed_at"] = entry.get("computed_at")
    out["history_key"] = entry.get("key")
    _remember_last("rotor_stress", out, dict(entry.get("params") or {}),
                   out.get("geo_fingerprint"))
    return out


_RH.register_loader("mechanical.rotor_stress", _load_rotor_stress_history_entry)


def _min_safety_factor(obj, _depth: int = 0) -> Optional[float]:
    """A cheap, best-effort scan for the worst ``safety_factor`` in a result —
    for the history list's one-line summary only, never for a verdict.  Skips
    into plain numeric arrays (the field map) without walking every element."""
    if _depth > 6:
        return None
    best: Optional[float] = None
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "safety_factor" and isinstance(v, (int, float)) and math.isfinite(v):
                if best is None or v < best:
                    best = float(v)
            elif isinstance(v, (dict, list)):
                sub = _min_safety_factor(v, _depth + 1)
                if sub is not None and (best is None or sub < best):
                    best = sub
    elif isinstance(obj, list) and obj and isinstance(obj[0], (dict, list)):
        for v in obj:
            sub = _min_safety_factor(v, _depth + 1)
            if sub is not None and (best is None or sub < best):
                best = sub
    return best


def _rotor_stress_summary(params: Dict[str, Any], out: Dict[str, Any]) -> str:
    try:
        bits = [f"{float(params.get('rpm') or 0):,.0f} rpm"]
        if params.get("cases") == "single":
            bits.append("single case")
        if params.get("symmetry") == "sector":
            bits.append("sector")
        if params.get("loads") and params.get("loads") != "both":
            bits.append(str(params["loads"]))
        sf = _min_safety_factor(out)
        if sf is not None:
            bits.append(f"SF {sf:.2f}")
        return ", ".join(bits)
    except Exception:                                       # noqa: BLE001
        return "rotor stress"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/rotor_stress")
@_JOBS.queued("mechanical.rotor_stress", priority=_JOBS.Priority.INTERACTIVE)
def rotor_stress(
    rpm: Optional[float] = Query(default=None,
                                 description="rated speed; default = simulation.rpm"),
    overspeed_factor: float = Query(default=1.2, ge=1.0, le=3.0),
    interference_mm: float = Query(default=0.0, ge=0.0, le=1.0,
                                   description="sleeve RADIAL interference fit"),
    mesh_size_mm: float = Query(default=1.5, gt=0.1, le=10.0),
    order: int = Query(default=2, ge=1, le=2),
    field: bool = Query(default=True, description="include the stress field map"),
    contacts: Optional[str] = Query(
        default=None,
        description='JSON {pair: {"type": separation|bonded|sliding, "mu": 0}}; '
                    'omitted pairs keep the default (separation everywhere '
                    'except shaft_rotor, which is a bonded press-fit hub)'),
    lift_off_solves: int = Query(
        default=6, ge=0, le=8,
        description="extra nonlinear solves the lift-off bisection may spend; "
                    "0 skips the search"),
    cases: str = Query(
        default="three",
        description="three = standstill / rated / overspeed (the default, so "
                    "every existing caller is unchanged); single = ONE case at "
                    "?rpm=, named by its speed, and ~3x faster"),
    loads: str = Query(
        default="both",
        description="which forces act: centrifugal | torque | both. 'torque' "
                    "and 'both' apply the electromagnetic torque as a uniform "
                    "tangential traction on the rotor solids' air-gap surface "
                    "and react it at the shaft bore, held tangentially"),
    torque_nm: Optional[float] = Query(
        default=None, ge=-1e6, le=1e6,
        description="electromagnetic torque, N·m; default = the mean torque of "
                    "the last Simulation run on this machine"),
    rotor_temp_c: float = Query(
        default=20.0, ge=-273.15, le=1000.0,
        description="rotor core / magnet / shaft temperature, °C. 20 = the "
                    "reference, i.e. NO thermal load — the machine as drawn"),
    sleeve_temp_c: float = Query(
        default=20.0, ge=-273.15, le=1000.0,
        description="retaining-sleeve temperature, °C. Separate from the rotor "
                    "because they are not the same number on a real machine, "
                    "and the difference is what changes the fit"),
    magnet_temp_c: Optional[float] = Query(
        default=None, ge=-273.15, le=1000.0,
        description="magnet temperature, °C. Omitted = rotor_temp_c, i.e. the "
                    "rule this route has always used"),
    rotor_core_temp_c: Optional[float] = Query(
        default=None, ge=-273.15, le=1000.0,
        description="rotor-core (iron) temperature, °C. Omitted = rotor_temp_c"),
    shaft_temp_c: Optional[float] = Query(
        default=None, ge=-273.15, le=1000.0,
        description="shaft temperature, °C. Omitted = rotor_temp_c"),
    symmetry: str = Query(
        default="full",
        description="full = the whole 360° cross-section (the default, so "
                    "every existing caller is unchanged); sector = ONE "
                    "periodic sector with cyclic-symmetry ties on its two cut "
                    "faces, so every pole carries an identical load by "
                    "construction and the solve is ~n times cheaper"),
    geo: Optional[str] = Query(default=None),
    fresh: bool = Query(
        default=False,
        description="ignore a stored history answer and solve again, even if "
                    "an identical request was already computed — the panel's "
                    "Recompute button"),
):
    """Centrifugal stress & deformation of the rotor solids, with CONTACT.

    Three cases in one call — standstill (interference only), rated and
    overspeed — because an engineer sizing a sleeve needs all three side by
    side.  Since v2 they are three separate NONLINEAR solves: a separation
    contact opens and closes with the load, so nothing superposes.

    ``cases=single`` solves only the one at ``rpm``.  User 2026-09-06: "давай
    будем рассчитывать только на 23 000 оборотов — всё, что ниже, всяко выдержит,
    и проще будет считать только одну величину".  The response SHAPE is
    identical (``cases`` simply has one entry, keyed by the speed), so the maps,
    the persisted last result and every existing reader keep working.

    ``symmetry=sector`` (2026-09-09) solves ONE periodic sector instead of the
    whole circle.  User: *"нагрузка на все зубы должна быть одинакова … так
    используй периодичность, как я во Fusion"* — the two cut faces are tied by
    ``u_B = R(2*pi/n) u_A``, so every pole is identical by construction and the
    stiffness matrix is ``n`` times smaller.  The response shape does not
    change: extensive numbers (masses, joint capacities, the bore reaction) are
    scaled back to the machine, the field is the sector replicated ``n`` times
    so the map draws the whole rotor, and ``symmetry`` in the answer says what
    was done.  Not in the cache key unless it is asked for, so a request that
    says nothing is keyed and solved exactly as it always was.

    ``magnet_temp_c`` / ``rotor_core_temp_c`` / ``shaft_temp_c`` (2026-09-08)
    give each solid its OWN temperature; ``sleeve_temp_c`` already did, and
    ``rotor_temp_c`` stays the fallback for the three.  User: "в механический
    расчёт тоже нужно делать каплинг, чтобы температуры везде были одинаковы" —
    the Thermal solve reports a temperature per part, and the Mechanical solve
    should apply THOSE rather than two numbers retyped by hand.  Each one is in
    the cache key only when it was passed, so a request that names none is keyed
    and solved exactly as it was before they existed, and
    ``thermal.part_temps_c`` in the response says what every part ended up at.
    """
    from motor_ai_sim.simulation.mechanical import contact as ctc
    from motor_ai_sim.simulation.mechanical import rotor_stress as rsm

    case_mode = str(cases or "three").strip().lower()
    if case_mode not in ("three", "single"):
        # Named, not defaulted: silently solving three cases for a request that
        # asked for one would bill the user 3x the seconds they asked for.
        raise HTTPException(
            status_code=422,
            detail={"error": f"unknown case table {cases!r}",
                    "invalid_parameters": [{
                        "field": "cases", "value": cases, "kind": "bad_value",
                        "message": "cases must be 'three' (standstill / rated / "
                                   "overspeed) or 'single' (one case at ?rpm=)"}]})
    if case_mode == "single":
        # No overspeed case is solved, so the factor is not an input here — pin
        # it before the cache key is taken, or two requests that differ only in
        # a field this mode ignores would each pay for the same solve.
        overspeed_factor = 1.0

    # ── the symmetry reduction (2026-09-09) ─────────────────────────────────
    # Named, never defaulted silently: a typo'd value that quietly solved the
    # full rotor would bill the user n times the seconds they asked for.
    sym_mode = str(symmetry or "full").strip().lower()
    if sym_mode not in ("full", "sector"):
        raise HTTPException(
            status_code=422,
            detail={"error": f"unknown symmetry {symmetry!r}",
                    "invalid_parameters": [{
                        "field": "symmetry", "value": symmetry,
                        "kind": "bad_value",
                        "message": "symmetry must be 'full' (the whole 360° "
                                   "rotor) or 'sector' (one periodic sector "
                                   "with cyclic-symmetry ties)"}]})

    # ── which forces act (2026-09-07) ───────────────────────────────────────
    # User: "сделай меню, чтобы можно было выбрать центробежную, момент и обе".
    load_mode = str(loads or "both").strip().lower()
    if load_mode not in rsm.LOAD_MODES:
        raise HTTPException(
            status_code=422,
            detail={"error": f"unknown load selection {loads!r}",
                    "invalid_parameters": [{
                        "field": "loads", "value": loads, "kind": "bad_value",
                        "message": "loads must be 'centrifugal', 'torque' or "
                                   "'both'"}]})
    loads_requested = load_mode
    if torque_nm is None:
        torque, torque_source = _default_torque_nm()
    else:
        torque, torque_source = float(torque_nm), "given"
    if torque is not None and not math.isfinite(torque):
        raise HTTPException(
            status_code=422,
            detail={"error": "torque_nm is not a finite number",
                    "invalid_parameters": [{
                        "field": "torque_nm", "value": torque_nm,
                        "kind": "bad_value",
                        "message": "pass a finite torque in N·m"}]})
    if load_mode in ("torque", "both") and not torque:
        if load_mode == "torque":
            # Asked for the torque case and there is no torque: say so rather
            # than solving something else and calling it what was asked for.
            raise HTTPException(
                status_code=422,
                detail={"error": "no electromagnetic torque to apply",
                        "invalid_parameters": [{
                            "field": "torque_nm", "value": torque_nm,
                            "kind": "missing",
                            "message": ("pass ?torque_nm=, or run the "
                                        "Simulation tab once — the default is "
                                        "that run's mean torque")}]})
        # loads=both is the DEFAULT, so a machine that has never been run must
        # still get its centrifugal answer.  Downgraded, and the response says
        # so under `loads` vs `loads_requested`.
        load_mode = "centrifugal"
        torque = 0.0

    try:
        cspec = ctc.parse_contacts(contacts)
    except ctc.ContactConfigError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": str(exc),
                    "invalid_parameters": [{
                        "field": exc.field_name, "value": contacts,
                        "kind": "bad_contact_setting", "message": str(exc)}]})

    # ── the per-part temperatures (2026-09-08) ──────────────────────────────
    # Only what was ASKED for goes in: an omitted part keeps `rotor_temp_c`, and
    # an empty map keeps every downstream key (cache, params, solver) byte for
    # byte what it was before this feature.
    part_temps: Dict[str, float] = {}
    for _field, _val in (("rotor_core", rotor_core_temp_c),
                         ("magnet", magnet_temp_c),
                         ("shaft", shaft_temp_c)):
        if _val is not None:
            v = float(_val)
            if not math.isfinite(v):
                raise HTTPException(
                    status_code=422,
                    detail={"error": f"{_field}_temp_c is not a finite number",
                            "invalid_parameters": [{
                                "field": f"{_field}_temp_c", "value": _val,
                                "kind": "bad_value",
                                "message": "pass a temperature in °C"}]})
            part_temps[_field] = v

    speed = _default_rpm() if rpm is None else float(rpm)
    if speed <= 0:
        raise HTTPException(
            status_code=422,
            detail={"error": "no rated speed to solve at",
                    "invalid_parameters": [{
                        "field": "rpm", "value": rpm, "kind": "missing",
                        "message": ("pass ?rpm=, or set simulation.rpm in the "
                                    "config — a centrifugal solve at 0 rpm has "
                                    "nothing to report")}]})

    # The bar opens BEFORE the geometry build, not before the solve: on a big
    # machine the CadQuery cross-section is seconds of its own, and a strip that
    # says "not running" for the first two seconds of a press reads as a click
    # that did not land.  The total is the honest one the solver will confirm —
    # mesh + assembly + (cases + lift-off budget) x the contact iteration cap —
    # so the bar never has to re-scale itself the moment the solve starts.
    _progress.start(
        total=2 + ((1 if case_mode == "single" else 3)
                   + max(int(lift_off_solves), 0)) * rsm.MAX_CONTACT_ITER,
        phase="geometry build", kind="rotor_stress",
        composition="")
    try:
        t0 = time.time()
        try:
            polys, motor, geo_ov = _live_polys(geo)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500,
                                detail=f"geometry build failed: {type(exc).__name__}: {exc}")

        assign = _assignments()
        key = _cache_key(geo_ov, assign, speed, overspeed_factor, interference_mm,
                         mesh_size_mm, order, cspec, case_mode, load_mode,
                         torque or 0.0, rotor_temp_c, sleeve_temp_c,
                         part_temps or None, sym_mode)
        # `cases` rides in the persisted params so re-entering the tab restores the
        # mode as well as the numbers — a single-speed answer under a three-case
        # toggle would read as two missing columns.  `loads` and `torque_nm` ride
        # with it for the same reason (2026-09-07).
        _params = {"rpm": speed, "overspeed_factor": overspeed_factor,
                   "interference_mm": interference_mm,
                   "mesh_size_mm": mesh_size_mm, "order": order,
                   "cases": case_mode,
                   # …and the symmetry reduction, for the same reason: a sector
                   # answer under a "full" toggle would read as a rotor whose
                   # mass had suddenly become 1/28 of itself (2026-09-09).
                   "symmetry": sym_mode,
                   "loads": load_mode, "torque_nm": float(torque or 0.0),
                   # The temperatures ride in the persisted params for the same
                   # reason (2026-09-07): re-entering the tab must restore the two
                   # fields, or the numbers on screen would be a hot rotor's under
                   # a 20 °C toggle.
                   "rotor_temp_c": float(rotor_temp_c),
                   "sleeve_temp_c": float(sleeve_temp_c),
                   "contacts": {k: {"type": v.type, "mu": v.mu}
                                for k, v in cspec.items()}}
        # Only when they were given, so a panel restoring from /last cannot find
        # a per-part override it never set (2026-09-08).
        for _field, _v in part_temps.items():
            _params[f"{_field}_temp_c"] = _v
        requested_history_key = _rotor_stress_history_key(key)
        if not fresh:
            hit = rsm.cache_get(key)
            if hit is not None:
                out = dict(hit)
                out["cached"] = True
                # A cache hit is still "the last thing the tab showed" — remember it, or
                # pressing Solve twice would leave /last pointing at an older answer.
                _remember_last("rotor_stress", hit, _params, hit.get("geo_fingerprint"))
                if not field:
                    out.pop("field", None)
                return out
            # A miss in THIS process's session cache — try the persistent
            # history (survives a restart; ``rsm.cache_get`` above does not).
            h_hit = _ROTOR_STRESS_HISTORY.get(requested_history_key)
            if h_hit is not None:
                out = dict(h_hit["payload"])
                out["cached"] = True
                out["served_from_history"] = True
                out["computed_at"] = h_hit["entry"].get("computed_at")
                out["history_key"] = requested_history_key
                rsm.cache_put(key, out)      # warm the fast layer too
                _remember_last("rotor_stress", out, _params, out.get("geo_fingerprint"))
                if not field:
                    out.pop("field", None)
                return out

        stack_mm = float(motor.parameters.get("motor_length") or 0.0)
        contact_fallback: Optional[Dict[str, Any]] = None
        # The key the REQUEST hashes to.  A fallback below re-keys the solve on
        # the bonded spec; the answer is then filed under both, so the same
        # request pressed again is a cache hit and not a second runaway.
        requested_key = key

        def _solve(spec):
            return rsm.solve_rotor_stress(
                polys, assign, speed, overspeed_factor, interference_mm,
                stack_length_mm=stack_mm, material_overrides=_override_props(),
                mesh_size_mm=mesh_size_mm, order=order, with_field=True,
                contacts=spec, lift_off_solves=lift_off_solves,
                case_mode=case_mode, loads=load_mode, torque_nm=float(torque or 0.0),
                rotor_temp_c=float(rotor_temp_c), sleeve_temp_c=float(sleeve_temp_c),
                part_temps_c=(dict(part_temps) or None),
                symmetry=sym_mode, num_poles=_num_poles(motor),
                progress=_progress.callback())

        try:
            # A SEPARATION joint that does not retain its part is, on a built
            # machine, a GLUED (or pressed) part: 2026-09-09, the G2's and the
            # 40 mm's magnets sit in pockets with a gap above them, and a hub
            # on a fit-less, frictionless shaft opens under its own growth —
            # the contact set the user chose for the Ø200, whose iron lips DO
            # hold the magnets, followed them onto both machines ("я везде
            # сделал separation").  Rather than refuse a machine the user
            # cannot tell apart from a working one, the joint that ran away is
            # solved BONDED and the answer says so, in the result and on the
            # panel (`contact_fallback`).  ONE joint per pass, in the order the
            # solver names them, because bonding the first may be all the
            # second needed; a runaway that names no separation joint — or one
            # already bonded — is the refusal it always was.
            bonded_so_far: list = []
            while True:
                try:
                    out = _solve(cspec)
                    break
                except rsm.RotorRanAway as exc:
                    pair = exc.pair
                    if not (pair and pair in cspec
                            and cspec[pair].type == "separation"
                            and pair not in bonded_so_far):
                        raise
                    fb = dict(cspec)
                    fb[pair] = ctc.ContactSpec("bonded", 0.0)
                    log.warning("rotor stress: %s ran away with %s = separation"
                                " — solving it bonded", exc.case, pair)
                    cspec = fb
                    bonded_so_far.append(pair)
                    entry = {
                        "pair": pair, "from": "separation", "to": "bonded",
                        "open_fraction": exc.open_fraction,
                        "reason": ("the %s joint does not retain the part in "
                                   "the separation model (%s%% of it open at "
                                   "%s rpm) — solved as a glued joint"
                                   % (pair,
                                      ("%.0f" % (100.0 * exc.open_fraction)
                                       if exc.open_fraction is not None else "?"),
                                      f"{exc.rpm:,.0f}")),
                        "refusal": str(exc),
                    }
                    if contact_fallback is None:
                        contact_fallback = {**entry, "pairs": [pair], "entries": [entry]}
                    else:
                        contact_fallback["pairs"].append(pair)
                        contact_fallback["entries"].append(entry)
                        contact_fallback["reason"] += "; " + entry["reason"]
            if bonded_so_far:
                key = _cache_key(geo_ov, assign, speed, overspeed_factor,
                                 interference_mm, mesh_size_mm, order, cspec,
                                 case_mode, load_mode, torque or 0.0,
                                 rotor_temp_c, sleeve_temp_c, part_temps or None,
                                 sym_mode)
                _params["contacts"] = {k: {"type": v.type, "mu": v.mu}
                                       for k, v in cspec.items()}
        except rsm.MissingMechanicalProperty as exc:
            # The whole point of the 422: name the part, the material and the key,
            # so the fix is an edit to one record and not a guess.
            raise HTTPException(
                status_code=422,
                detail={"error": str(exc),
                        "invalid_parameters": [{
                            "field": f"materials.{exc.part}", "value": exc.material,
                            "kind": "missing_mechanical_property",
                            "message": str(exc)}]})
        except rsm.RotorRanAway as exc:
            # A piece of the rotor held by nothing (2026-09-09): the message
            # names the contact pair to change, and the field says where.
            raise HTTPException(
                status_code=422,
                detail={"error": str(exc),
                        "invalid_parameters": [{
                            "field": (f"contacts.{exc.pair}" if exc.pair
                                      else "contacts"),
                            "value": "separation", "kind": "unretained_part",
                            "message": str(exc)}]})
        except ValueError as exc:
            raise HTTPException(status_code=422, detail={"error": str(exc),
                                                         "invalid_parameters": []})
        except Exception as exc:  # noqa: BLE001
            log.exception("rotor stress solve failed")
            raise HTTPException(status_code=500,
                                detail=f"{type(exc).__name__}: {exc}")

        out["solve_time_s"] = round(time.time() - t0, 2)
        out["elapsed_s"] = _elapsed(t0)
        out["cached"] = False
        if contact_fallback is not None:
            out["contact_fallback"] = contact_fallback
        # What was ASKED for beside what was solved: `loads=both` on a machine that
        # has never been run downgrades to centrifugal, and the panel says so rather
        # than showing a torque column that is not there.
        out["loads_requested"] = loads_requested
        out["torque_source"] = torque_source
        out["geo_fingerprint"] = _live_fingerprint(geo_ov)
        rsm.cache_put(key, out)
        if key != requested_key:
            rsm.cache_put(requested_key, out)
        # ── persistent history (2026-09-22) ─────────────────────────────────
        # Filed under the key the SOLVE actually ran under (the bonded
        # fallback may have re-keyed it above) and, when that differs, also
        # under the ORIGINAL requested key — mirroring the two
        # ``rsm.cache_put`` calls just above, so a plain repeat of the request
        # the user actually typed is a history hit even though it silently
        # solved bonded last time.
        final_history_key = _rotor_stress_history_key(key)
        summary = _rotor_stress_summary(_params, out)
        _ROTOR_STRESS_HISTORY.put(final_history_key, params=dict(_params),
                                  summary=summary, payload=out,
                                  extra={"geo_fingerprint": out["geo_fingerprint"]})
        if final_history_key != requested_history_key:
            _ROTOR_STRESS_HISTORY.put(requested_history_key, params=dict(_params),
                                      summary=summary, payload=out,
                                      extra={"geo_fingerprint": out["geo_fingerprint"]})
        out["history_key"] = final_history_key
        _remember_last("rotor_stress", out, _params, out["geo_fingerprint"])
        if not field:
            out = {k: v for k, v in out.items() if k != "field"}
        return out
    finally:
        # Unconditional: an exception on any path must not leave
        # the progress endpoint reporting a live solve.
        _progress.finish()


# ---------------------------------------------------------------------------
# Limit speed — the rpm at which SF = 1 (or whatever target is asked for)
# ---------------------------------------------------------------------------
# Owner 2026-09-21: "нужно искать ещё максимальную скорость вращения, на
# всякий случай — она будет, когда достигает SF = 1".  ``rotor_stress`` answers
# "is THIS speed safe"; this route answers the companion question everything
# else about the case held fixed — same torque, contacts, interference,
# temperatures, mesh, order — by bracketing and bisecting single-speed
# ``solve_rotor_stress`` calls (the pure search is
# ``simulation.mechanical.limit_speed.find_limit_speed``).
#
# The geometry and material assignment are built ONCE, like
# ``rotor_stress()``'s own ``_solve`` closure, and every candidate speed reuses
# them — only the FEM solve differs between the analysed case and the search
# steps that follow it.  ``cases`` is pinned to ``single`` (a limit speed is a
# question about ONE speed at a time; a three-case table under a search that
# asks a dozen different speeds would be a different feature): the panel gets
# a genuine ``rotor_stress`` answer, at ``rpm``, with ``limit_speed`` riding on
# it, and THAT becomes the tab's last result — the point of the button is
# exactly to show "here is the case, and here is how far it is from SF = 1".
@router.post("/limit_speed")
@_JOBS.queued("mechanical.limit_speed", priority=_JOBS.Priority.INTERACTIVE)
def limit_speed(
    rpm: Optional[float] = Query(default=None,
                                 description="the analysed speed; default = simulation.rpm"),
    interference_mm: float = Query(default=0.0, ge=0.0, le=1.0,
                                   description="sleeve RADIAL interference fit"),
    mesh_size_mm: float = Query(default=1.5, gt=0.1, le=10.0),
    order: int = Query(default=2, ge=1, le=2),
    contacts: Optional[str] = Query(
        default=None,
        description='JSON {pair: {"type": separation|bonded|sliding, "mu": 0}}; '
                    'same contract as /rotor_stress'),
    loads: str = Query(
        default="both",
        description="which forces act: centrifugal | torque | both — held the "
                    "same at every speed the search tries"),
    torque_nm: Optional[float] = Query(
        default=None, ge=-1e6, le=1e6,
        description="electromagnetic torque, N·m, HELD CONSTANT across the "
                    "whole search (it is the duty's torque, not a function of "
                    "speed); default = the mean torque of the last Simulation "
                    "run on this machine"),
    rotor_temp_c: float = Query(default=20.0, ge=-273.15, le=1000.0),
    sleeve_temp_c: float = Query(default=20.0, ge=-273.15, le=1000.0),
    magnet_temp_c: Optional[float] = Query(default=None, ge=-273.15, le=1000.0),
    rotor_core_temp_c: Optional[float] = Query(default=None, ge=-273.15, le=1000.0),
    shaft_temp_c: Optional[float] = Query(default=None, ge=-273.15, le=1000.0),
    symmetry: str = Query(default="full"),
    target_sf: float = Query(
        default=1.0, gt=0.0, le=10.0,
        description="the safety factor searched for; 1.0 = the rotor's "
                    "structural limit"),
    max_factor: float = Query(
        default=5.0, gt=1.0, le=20.0,
        description="how far from the analysed speed (as a multiple of it) "
                    "the bracket search may range before giving up"),
    max_solves: int = Query(
        default=12, ge=2, le=30,
        description="the whole search's solve budget, bracket + bisection"),
    geo: Optional[str] = Query(default=None),
):
    """The speed at which the minimum averaged safety factor reaches ``target_sf``.

    Everything about the case is held exactly as given — the same torque
    (``loads=both`` keeps the duty's torque applied at every candidate speed,
    not scaled with it), the same contact set, interference, temperatures,
    mesh and element order — and only ``rpm`` is swept, by
    :func:`motor_ai_sim.simulation.mechanical.limit_speed.find_limit_speed`.

    The FIRST solve is at ``rpm`` itself (the analysed case); its answer
    becomes this response — a genuine single-speed ``rotor_stress`` result —
    with the search's block riding on it as ``limit_speed``.  Every candidate
    the search tries after that is a plain ``solve_rotor_stress`` call with
    ``with_field=False`` (the search wants a number, not a map) sharing the
    SAME cross-section and material assignment, so nothing is rebuilt between
    solves.

    A candidate speed at which a separation joint runs away
    (:class:`~motor_ai_sim.simulation.mechanical.rotor_stress.RotorRanAway`) is
    treated as SF 0 at that speed — a part held by nothing is a worse failure
    than SF < target — so the search brackets toward a lower, retained limit
    speed instead of raising mid-search.  The analysed case itself running
    away IS refused, exactly as ``/rotor_stress`` refuses it, because there is
    no case to search from.
    """
    from motor_ai_sim.simulation.mechanical import contact as ctc
    from motor_ai_sim.simulation.mechanical import limit_speed as lsm
    from motor_ai_sim.simulation.mechanical import rotor_stress as rsm

    load_mode = str(loads or "both").strip().lower()
    if load_mode not in rsm.LOAD_MODES:
        raise HTTPException(
            status_code=422,
            detail={"error": f"unknown load selection {loads!r}",
                    "invalid_parameters": [{
                        "field": "loads", "value": loads, "kind": "bad_value",
                        "message": "loads must be 'centrifugal', 'torque' or "
                                   "'both'"}]})

    sym_mode = str(symmetry or "full").strip().lower()
    if sym_mode not in ("full", "sector"):
        raise HTTPException(
            status_code=422,
            detail={"error": f"unknown symmetry {symmetry!r}",
                    "invalid_parameters": [{
                        "field": "symmetry", "value": symmetry,
                        "kind": "bad_value",
                        "message": "symmetry must be 'full' or 'sector'"}]})

    loads_requested = load_mode
    if torque_nm is None:
        torque, torque_source = _default_torque_nm()
    else:
        torque, torque_source = float(torque_nm), "given"
    if torque is not None and not math.isfinite(torque):
        raise HTTPException(
            status_code=422,
            detail={"error": "torque_nm is not a finite number",
                    "invalid_parameters": [{
                        "field": "torque_nm", "value": torque_nm,
                        "kind": "bad_value",
                        "message": "pass a finite torque in N·m"}]})
    if load_mode in ("torque", "both") and not torque:
        if load_mode == "torque":
            raise HTTPException(
                status_code=422,
                detail={"error": "no electromagnetic torque to apply",
                        "invalid_parameters": [{
                            "field": "torque_nm", "value": torque_nm,
                            "kind": "missing",
                            "message": ("pass ?torque_nm=, or run the "
                                        "Simulation tab once — the default is "
                                        "that run's mean torque")}]})
        load_mode = "centrifugal"
        torque = 0.0

    try:
        cspec = ctc.parse_contacts(contacts)
    except ctc.ContactConfigError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": str(exc),
                    "invalid_parameters": [{
                        "field": exc.field_name, "value": contacts,
                        "kind": "bad_contact_setting", "message": str(exc)}]})

    part_temps: Dict[str, float] = {}
    for _field, _val in (("rotor_core", rotor_core_temp_c),
                         ("magnet", magnet_temp_c),
                         ("shaft", shaft_temp_c)):
        if _val is not None:
            v = float(_val)
            if not math.isfinite(v):
                raise HTTPException(
                    status_code=422,
                    detail={"error": f"{_field}_temp_c is not a finite number",
                            "invalid_parameters": [{
                                "field": f"{_field}_temp_c", "value": _val,
                                "kind": "bad_value",
                                "message": "pass a temperature in °C"}]})
            part_temps[_field] = v

    rpm0 = _default_rpm() if rpm is None else float(rpm)
    if rpm0 <= 0:
        raise HTTPException(
            status_code=422,
            detail={"error": "no analysed speed to search from",
                    "invalid_parameters": [{
                        "field": "rpm", "value": rpm, "kind": "missing",
                        "message": ("pass ?rpm=, or set simulation.rpm — a "
                                    "limit-speed search needs a starting "
                                    "point")}]})

    _progress.start(
        total=2 + (int(max_solves) + 1) * rsm.MAX_CONTACT_ITER,
        phase="geometry build", kind="limit_speed", composition="")
    try:
        t0 = time.time()
        try:
            polys, motor, geo_ov = _live_polys(geo)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500,
                                detail=f"geometry build failed: {type(exc).__name__}: {exc}")

        assign = _assignments()
        stack_mm = float(motor.parameters.get("motor_length") or 0.0)
        n_poles = _num_poles(motor)
        override_props = _override_props()
        fp = _live_fingerprint(geo_ov)

        solves_done = 0

        def _solve_rpm(cand_rpm: float):
            nonlocal solves_done
            solves_done += 1
            _progress.update(
                done=min(solves_done, int(max_solves)), total=int(max_solves),
                phase=f"limit speed {solves_done}/{max_solves} — "
                      f"{cand_rpm:,.0f} rpm")
            try:
                out = rsm.solve_rotor_stress(
                    polys, assign, cand_rpm, 1.0, interference_mm,
                    stack_length_mm=stack_mm, material_overrides=override_props,
                    mesh_size_mm=mesh_size_mm, order=order, with_field=False,
                    contacts=cspec, lift_off_solves=0,
                    case_mode="single", loads=load_mode,
                    torque_nm=float(torque or 0.0),
                    rotor_temp_c=float(rotor_temp_c),
                    sleeve_temp_c=float(sleeve_temp_c),
                    part_temps_c=(dict(part_temps) or None),
                    symmetry=sym_mode, num_poles=n_poles)
            except rsm.RotorRanAway as exc:
                # A part held by nothing at this candidate speed IS below the
                # target factor, more so than any number the solve could have
                # printed — fold it in rather than aborting the search over a
                # speed nobody asked to see reported.
                return 0.0, (exc.pair or None), {}, None
            _case_name, case = next(iter(out["cases"].items()))
            return case["sf_min"], case["sf_min_part"], case["sf_min_per_part"], out

        try:
            sf0, part0, per_part0, out0 = _solve_rpm(rpm0)
        except rsm.MissingMechanicalProperty as exc:
            raise HTTPException(
                status_code=422,
                detail={"error": str(exc),
                        "invalid_parameters": [{
                            "field": f"materials.{exc.part}", "value": exc.material,
                            "kind": "missing_mechanical_property",
                            "message": str(exc)}]})
        except ValueError as exc:
            raise HTTPException(status_code=422, detail={"error": str(exc),
                                                         "invalid_parameters": []})
        except Exception as exc:  # noqa: BLE001
            log.exception("limit speed: analysed-case solve failed")
            raise HTTPException(status_code=500,
                                detail=f"{type(exc).__name__}: {exc}")

        if out0 is None:
            # The analysed case ITSELF ran away — there is no safe case to
            # search a limit speed from; refused exactly as /rotor_stress
            # refuses the same solve.
            raise HTTPException(
                status_code=422,
                detail={"error": ("the analysed case does not retain its "
                                  f"parts at {rpm0:,.0f} rpm ({part0} ran "
                                  "away) — fix the contacts or the speed "
                                  "before searching for a limit speed"),
                        "invalid_parameters": [{
                            "field": (f"contacts.{part0}" if part0
                                      else "contacts"),
                            "value": "separation", "kind": "unretained_part",
                            "message": "see /rotor_stress at this rpm for the "
                                       "full refusal"}]})

        def _solve_for_search(cand_rpm: float):
            sf, part, per_part, _out = _solve_rpm(cand_rpm)
            return sf, part, per_part

        try:
            search = lsm.find_limit_speed(
                _solve_for_search, rpm0, sf0, target=float(target_sf),
                max_factor=float(max_factor), max_solves=int(max_solves))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail={"error": str(exc),
                                                         "invalid_parameters": []})

        block = dict(search)
        block["target_sf"] = float(target_sf)
        block["loads"] = load_mode
        block["torque_nm"] = float(torque or 0.0)
        block["interference_mm"] = float(interference_mm)
        block["rotor_temp_c"] = float(rotor_temp_c)
        block["sleeve_temp_c"] = float(sleeve_temp_c)
        block["geo_fingerprint"] = fp
        block["analysed_rpm"] = rpm0

        out0["limit_speed"] = block
        out0["solve_time_s"] = round(time.time() - t0, 2)
        out0["elapsed_s"] = _elapsed(t0)
        out0["cached"] = False
        out0["loads_requested"] = loads_requested
        out0["torque_source"] = torque_source
        out0["geo_fingerprint"] = fp

        _params = {"rpm": rpm0, "overspeed_factor": 1.0,
                   "interference_mm": interference_mm,
                   "mesh_size_mm": mesh_size_mm, "order": order,
                   "cases": "single", "symmetry": sym_mode,
                   "loads": load_mode, "torque_nm": float(torque or 0.0),
                   "rotor_temp_c": float(rotor_temp_c),
                   "sleeve_temp_c": float(sleeve_temp_c),
                   "contacts": {k: {"type": v.type, "mu": v.mu}
                                for k, v in cspec.items()},
                   "target_sf": float(target_sf)}
        for _field, _v in part_temps.items():
            _params[f"{_field}_temp_c"] = _v
        _remember_last("rotor_stress", out0, _params, fp)

        return out0
    finally:
        _progress.finish()


# ---------------------------------------------------------------------------
# The coupled hook — one rotor-stress solve AT a given set of temperatures
# ---------------------------------------------------------------------------
# User 2026-09-08: "в механический расчёт тоже нужно делать каплинг, чтобы
# температуры везде были одинаковы".  An orchestrator that has just solved the
# Thermal map needs to run the Mechanical solve at THOSE temperatures — and it
# must run the same solve the button runs, not a private near-copy that drifts
# the first time the button changes.
#
# So this is a thin wrapper around the route function itself.  Everything the
# route does is therefore inherited for free: the 422s that name their field,
# the cache, the progress strip, and `_remember_last` — the answer lands as
# "what the Mechanical tab is showing", which is the point (the user opens the
# tab after a coupled run and expects to find the coupled result there).
#
# It is deliberately NOT wired to anything here.  Nothing in this module calls
# it; the orchestrator does.

def _mech_panel_settings(authorization: Optional[str] = None) -> Dict[str, Any]:
    """The user's saved Mechanical-panel fields — ``{}`` when there are none.

    Read through ``routes.panel_settings.get_panel_settings`` (the same route
    the panel itself reads on mount), so there is ONE store and one shape.  With
    no ``authorization`` the anonymous ``shared`` entry is read, which is what a
    single-user install writes anyway; pass the caller's header through to get
    that user's own fields.
    """
    try:
        from motor_ai_sim.routes.panel_settings import get_panel_settings
        got = get_panel_settings("mechanical", authorization) or {}
        s = got.get("settings")
        return dict(s) if isinstance(s, dict) else {}
    except Exception as exc:  # noqa: BLE001 - no saved fields is not an error
        log.debug("no mechanical panel settings (%s)", exc)
        return {}


def _panel_float(settings: Dict[str, Any], key: str) -> Optional[float]:
    """One panel field as a number, or ``None`` when it is empty / unusable.

    ``None`` matters: an empty torque box means "let the backend read the last
    Simulation run", and an empty rpm means "use ``simulation.rpm``" — turning
    either into a 0 would solve a machine nobody asked for.
    """
    v = settings.get(key)
    if v is None:
        return None
    try:
        s = str(v).strip()
        if not s:
            return None
        f = float(s)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


# ---------------------------------------------------------------------------
# The COUPLED LOOP'S OWN limit speed — automatic, cached, never manual
# ---------------------------------------------------------------------------
# Owner 2026-09-21: "нужно эту максимальную скорость обязательно добавлять в
# отчёт" — every duty a coupled run saves must carry it, not only the ones a
# human pressed **Limit speed (SF = 1)** for.  Wired into `run_rotor_stress_at`
# ONLY — the hook `routes/coupled.py` calls — so a plain interactive
# `/rotor_stress` GET (the Mechanical tab's own Solve button) never pays this
# automatically; a human who wants it there presses the button, which is the
# `/limit_speed` route above.
#
# `MECH_LIMIT_SPEED_AUTO=0` turns it off for emergencies (a coupled run that
# must not spend the extra solves right now) without touching this file again.

def _limit_speed_auto_enabled() -> bool:
    v = str(os.environ.get("MECH_LIMIT_SPEED_AUTO", "1")).strip().lower()
    return v not in ("0", "false", "off", "no")


#: Cached per (geometry, loads/torque, contacts, temperatures, mesh, order,
#: target) — a second coupled run of the SAME machine at the SAME case is a
#: cache hit and spends nothing; a different geometry or a different case
#: (a different duty's torque, a different rotor temperature) is not.  Small
#: cap: this is a handful of numbers per entry, not a field.
_LIMIT_SPEED_AUTO_MAX = 32
_LIMIT_SPEED_AUTO_CACHE = _WSP.ws_map("mechanical.limit_speed_auto_cache",
                                      _LIMIT_SPEED_AUTO_MAX, lru_on_read=True)


def _limit_speed_auto_cache_key(fp: Optional[str], out: Dict[str, Any]) -> tuple:
    contacts = out.get("contacts") or {}
    c_sig = tuple(sorted(
        (str(k), str(v.get("type")), round(float(v.get("mu") or 0.0), 4))
        for k, v in contacts.items() if isinstance(v, dict)))
    thermal = out.get("thermal") or {}
    part_temps = thermal.get("part_temps_c") or {}

    def _round5(x: Any) -> float:
        return round(float(x) / 5.0) * 5.0

    if part_temps:
        t_sig = tuple(sorted((str(k), _round5(v)) for k, v in part_temps.items()
                             if v is not None))
    else:
        t_sig = (("rotor", _round5(thermal.get("rotor_temp_c", 20.0))),
                 ("sleeve", _round5(thermal.get("sleeve_temp_c", 20.0))))
    mesh = out.get("mesh") or {}
    return (
        fp,
        str(out.get("loads") or "both"),
        round(float(out.get("torque_nm") or 0.0), 1),
        round(float(out.get("interference_mm") or 0.0), 4),
        c_sig, t_sig,
        round(float(mesh.get("mesh_size_mm") or 1.5), 4),
        int(mesh.get("element_order") or 2),
        1.0,  # target_sf — fixed at SF = 1 on the automatic path
    )


def _auto_limit_speed(out: Dict[str, Any],
                      geo: Optional[str]) -> Optional[Dict[str, Any]]:
    """The block `run_rotor_stress_at` attaches to a just-solved answer.

    Reads the analysed point (rpm, SF, part) straight off the PRIMARY case of
    ``out`` — never re-solved, unlike the manual route, which has no such
    answer in hand yet.  Everything else — loads, torque, contacts,
    interference, temperatures, mesh, order — is read off ``out`` too, so the
    search runs at EXACTLY the case that was just solved.

    Never raises: a search that fails must not take a coupled step down with
    it, so a failure is logged and the duty is saved without the block —
    which is exactly what happened before this existed, and the mandatory
    report row says so rather than showing a made-up number.
    """
    if not _limit_speed_auto_enabled() or not isinstance(out, dict):
        return None
    try:
        fp = out.get("geo_fingerprint")
        key = _limit_speed_auto_cache_key(fp, out)
        hit = _LIMIT_SPEED_AUTO_CACHE.get(key)
        if hit is not None:
            return dict(hit)

        cases = out.get("cases") or {}
        primary = out.get("primary_case") or next(iter(cases), None)
        case0 = cases.get(primary) if primary else None
        if not isinstance(case0, dict):
            return None

        def _numf(v: Any) -> Optional[float]:
            try:
                f = float(v)
            except (TypeError, ValueError):
                return None
            return f if math.isfinite(f) else None

        rpm0 = _numf(case0.get("rpm"))
        sf0 = _numf(case0.get("sf_min"))
        if rpm0 is None or rpm0 <= 0 or sf0 is None or sf0 <= 0:
            return None

        from motor_ai_sim.simulation.mechanical import contact as ctc
        from motor_ai_sim.simulation.mechanical import limit_speed as lsm
        from motor_ai_sim.simulation.mechanical import rotor_stress as rsm

        loads = str(out.get("loads") or "both")
        torque_nm = float(out.get("torque_nm") or 0.0)
        interference_mm = float(out.get("interference_mm") or 0.0)
        mesh = out.get("mesh") or {}
        mesh_size_mm = float(mesh.get("mesh_size_mm") or 1.5)
        order = int(mesh.get("element_order") or 2)
        sym = out.get("symmetry") or {}
        sym_mode = str(sym.get("mode") or "full") if isinstance(sym, dict) else "full"
        thermal = out.get("thermal") or {}
        rotor_temp_c = float(thermal.get("rotor_temp_c", 20.0))
        sleeve_temp_c = float(thermal.get("sleeve_temp_c", 20.0))
        part_temps_c = {str(k): float(v) for k, v in
                        (thermal.get("part_temps_c") or {}).items()
                        if str(k) in ("rotor_core", "magnet", "shaft")
                        and v is not None}
        cspec = {str(k): ctc.ContactSpec(str(v.get("type") or "separation"),
                                         float(v.get("mu") or 0.0))
                for k, v in (out.get("contacts") or {}).items()
                if isinstance(v, dict)}

        max_solves = 12
        _progress.start(total=max_solves, phase="geometry build",
                        kind="limit_speed", composition="")
        try:
            polys, motor, geo_ov = _live_polys(geo)
            assign = _assignments()
            stack_mm = float(motor.parameters.get("motor_length") or 0.0)
            n_poles = _num_poles(motor)
            override_props = _override_props()

            solves_done = 0

            def _solve_rpm(cand_rpm: float):
                nonlocal solves_done
                solves_done += 1
                _progress.update(
                    done=min(solves_done, max_solves), total=max_solves,
                    phase=f"limit speed {solves_done}/{max_solves} — "
                          f"{cand_rpm:,.0f} rpm")
                try:
                    sol = rsm.solve_rotor_stress(
                        polys, assign, cand_rpm, 1.0, interference_mm,
                        stack_length_mm=stack_mm,
                        material_overrides=override_props,
                        mesh_size_mm=mesh_size_mm, order=order,
                        with_field=False, contacts=cspec, lift_off_solves=0,
                        case_mode="single", loads=loads,
                        torque_nm=torque_nm, rotor_temp_c=rotor_temp_c,
                        sleeve_temp_c=sleeve_temp_c,
                        part_temps_c=(part_temps_c or None),
                        symmetry=sym_mode, num_poles=n_poles)
                except rsm.RotorRanAway as exc:
                    return 0.0, (exc.pair or None), {}
                _name, sol_case = next(iter(sol["cases"].items()))
                return (sol_case["sf_min"], sol_case["sf_min_part"],
                       sol_case["sf_min_per_part"])

            search = lsm.find_limit_speed(_solve_rpm, rpm0, sf0, target=1.0,
                                          max_factor=5.0, max_solves=max_solves)
        finally:
            _progress.finish()

        block = dict(search)
        block.update({
            "target_sf": 1.0, "loads": loads, "torque_nm": torque_nm,
            "interference_mm": interference_mm, "rotor_temp_c": rotor_temp_c,
            "sleeve_temp_c": sleeve_temp_c, "geo_fingerprint": fp,
            "analysed_rpm": rpm0,
        })
        if len(_LIMIT_SPEED_AUTO_CACHE) >= _LIMIT_SPEED_AUTO_MAX:
            _LIMIT_SPEED_AUTO_CACHE.popitem(last=False)
        _LIMIT_SPEED_AUTO_CACHE[key] = dict(block)
        return block
    except Exception:  # noqa: BLE001 — never take a coupled step down with it
        log.warning("mechanical: automatic limit-speed search failed",
                   exc_info=True)
        return None


def _refile_with_limit_speed(out: Dict[str, Any]) -> None:
    """Re-park `out` (now carrying `limit_speed`) as the tab's last result and
    the duty's compact copy, so a coupled run's saved duty carries the block
    without a second FEM solve.  A bookkeeping miss here must not fail the
    coupled step that got this far."""
    try:
        mesh = out.get("mesh") or {}
        sym = out.get("symmetry") or {}
        thermal = out.get("thermal") or {}
        params = {
            "rpm": out.get("rpm"), "overspeed_factor": out.get("overspeed_factor"),
            "interference_mm": out.get("interference_mm"),
            "mesh_size_mm": mesh.get("mesh_size_mm"),
            "order": mesh.get("element_order"),
            "cases": out.get("case_mode") or "three",
            "symmetry": (sym.get("mode") if isinstance(sym, dict) else None) or "full",
            "loads": out.get("loads"), "torque_nm": out.get("torque_nm"),
            "rotor_temp_c": (thermal.get("rotor_temp_c")),
            "sleeve_temp_c": (thermal.get("sleeve_temp_c")),
            "contacts": {str(k): {"type": v.get("type"), "mu": v.get("mu")}
                        for k, v in (out.get("contacts") or {}).items()
                        if isinstance(v, dict)},
        }
        _remember_last("rotor_stress", out, params, out.get("geo_fingerprint"))
    except Exception:  # noqa: BLE001
        log.warning("mechanical: could not re-file the limit-speed result",
                   exc_info=True)


def run_rotor_stress_at(temps: Dict[str, float],
                        **route_params) -> Dict[str, Any]:
    """Solve the rotor stress with each part at the temperature ``temps`` gives.

    THE HOOK the coupled orchestrator calls; it does exactly what pressing
    **Solve** on the Mechanical tab does, at temperatures somebody else computed.

    ``temps`` is keyed by part: ``rotor_core`` / ``magnet`` / ``shaft`` /
    ``sleeve`` (``rotor`` is accepted as an alias for ``rotor_core``, because
    that is what the Thermal payload's ``components`` calls the iron).  A part
    that is absent falls back exactly as the route does — ``rotor_temp_c`` for
    core / magnet / shaft, ``sleeve_temp_c`` for the band.  Those two fallbacks
    are, in order: the temperature given for ``rotor_core`` / ``sleeve``
    (a shaft nobody measured is far closer to the iron it is pressed into than
    to a field left over on the panel), then the panel's own manual
    ``rotorTempC`` / ``sleeveTempC``, then the 20 °C reference.  An unknown key
    raises rather than being dropped: a temperature that silently does not
    arrive is the whole failure this coupling exists to remove.

    WHERE THE REST COMES FROM, in this order (first that answers wins):

      * ``route_params`` — anything the caller states explicitly, by the route's
        own parameter names (``rpm``, ``overspeed_factor``, ``interference_mm``,
        ``mesh_size_mm``, ``order``, ``cases``, ``loads``, ``torque_nm``,
        ``contacts``, ``lift_off_solves``, ``field``, ``symmetry``, ``geo``);
      * the user's saved Mechanical panel settings
        (``GET /api/panel_settings/mechanical`` — the store this tab has used
        since 2026-09-07, so the coupled run uses the same rpm, overspeed
        factor, interference, mesh size and contact set the user last chose):
        ``rpm1`` when the panel is in single-speed mode and ``rpm`` otherwise,
        ``osf``, ``interf``, ``meshMm``, ``contacts``, ``cases``, ``loads``,
        ``torque``, and ``rotorTempC`` / ``sleeveTempC`` as the two fallbacks;
      * the ROUTE's own defaults — including its two resolutions that are not
        panel fields at all: ``rpm=None`` becomes ``simulation.rpm``, and
        ``torque_nm=None`` becomes the mean torque of the last Simulation run
        (the standing rule that the operating point comes from that tab).

    ``authorization`` may be passed to read that user's panel settings instead
    of the shared entry.  The material / geometry overrides are NOT resolved
    here: they ride the caller's request context exactly as they do for the
    route (``?mat=`` is a router dependency, ``geo`` a plain parameter).

    One difference from the button, stated rather than papered over: the panel
    zeroes ``interference_mm`` itself on a machine with no sleeve, and this hook
    cannot without building the cross-section twice.  A sleeveless machine whose
    saved panel still carries a fit therefore raises the route's own 422 naming
    ``interference_mm``; pass ``interference_mm=0`` to say so deliberately.
    Substituting a zero here would be exactly the silent correction this
    codebase refuses to make.

    Returns the route's payload, and — like the route — leaves it as the tab's
    last mechanical result.
    """
    from motor_ai_sim.simulation.mechanical import rotor_stress as rsm

    authorization = route_params.pop("authorization", None)
    ps = _mech_panel_settings(authorization)

    # ── the temperatures ────────────────────────────────────────────────────
    want = dict(temps or {})
    if "rotor" in want:
        # The Thermal payload calls the iron `rotor`; the materials map and this
        # solver call it `rotor_core`.  One may be given, never two different
        # ones — that is an ambiguity, not a preference to resolve quietly.
        alias = want.pop("rotor")
        if "rotor_core" in want and want["rotor_core"] != alias:
            raise ValueError(
                f"temps gives both rotor={alias!r} and "
                f"rotor_core={want['rotor_core']!r} — they are the same part; "
                "pass one")
        want.setdefault("rotor_core", alias)
    unknown = [k for k in want if k not in rsm.PART_TEMP_KEY]
    if unknown:
        raise ValueError(
            f"temps names unknown part(s) {', '.join(sorted(unknown))} — the "
            f"keys are {', '.join(sorted(rsm.PART_TEMP_KEY))} (or 'rotor' for "
            "the core, as the Thermal result names it)")
    for k, v in list(want.items()):
        try:
            f = float(v)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            f = float("nan")
        if not math.isfinite(f):
            raise ValueError(f"temps[{k!r}] = {v!r} is not a finite "
                             "temperature in °C")
        want[k] = f

    # The two fallbacks: the panel's manual fields, then the reference (= no
    # thermal load), never an invented number.
    _rt = _panel_float(ps, "rotorTempC")
    _st = _panel_float(ps, "sleeveTempC")
    rotor_fallback = rsm.REF_TEMP_C if _rt is None else _rt
    sleeve_fallback = rsm.REF_TEMP_C if _st is None else _st

    # ── the rest of the request ─────────────────────────────────────────────
    cases = str(route_params.get("cases") or ps.get("cases") or "three")
    single = cases == "single"
    rpm = route_params.get("rpm")
    if rpm is None:
        # In single-speed mode the ONE speed solved is the proof rpm, exactly as
        # the panel sends it; `None` here is the route's own `simulation.rpm`.
        rpm = _panel_float(ps, "rpm1" if single else "rpm")
    torque_nm = route_params.get("torque_nm")
    if torque_nm is None:
        t = _panel_float(ps, "torque")
        # An empty or zero torque box is NOT a torque: leaving it None is what
        # makes the route read the last Simulation run instead.
        torque_nm = t if (t is not None and t != 0.0) else None

    contacts = route_params.get("contacts")
    if contacts is None:
        c = ps.get("contacts")
        if isinstance(c, dict) and c:
            import json as _json
            contacts = _json.dumps(c)
    elif isinstance(contacts, dict):
        import json as _json
        contacts = _json.dumps(contacts)

    def _pick(name: str, panel_key: Optional[str], default):
        v = route_params.get(name)
        if v is not None:
            return v
        if panel_key:
            f = _panel_float(ps, panel_key)
            if f is not None:
                return f
        return default

    out = rotor_stress(
        rpm=rpm,
        overspeed_factor=float(_pick("overspeed_factor", "osf", 1.2)),
        interference_mm=float(_pick("interference_mm", "interf", 0.0)),
        mesh_size_mm=float(_pick("mesh_size_mm", "meshMm", 1.5)),
        order=int(_pick("order", None, 2)),
        field=bool(route_params.get("field", True)),
        contacts=contacts,
        lift_off_solves=int(_pick("lift_off_solves", None, 6)),
        cases=cases,
        loads=str(route_params.get("loads") or ps.get("loads") or "both"),
        torque_nm=torque_nm,
        rotor_temp_c=float(want.get("rotor_core", rotor_fallback)),
        sleeve_temp_c=float(want.get("sleeve", sleeve_fallback)),
        magnet_temp_c=(float(want["magnet"]) if "magnet" in want else None),
        rotor_core_temp_c=(float(want["rotor_core"])
                           if "rotor_core" in want else None),
        shaft_temp_c=(float(want["shaft"]) if "shaft" in want else None),
        # The symmetry reduction follows the same three-step resolution as
        # everything else (2026-09-09): what the caller states, then the user's
        # saved panel, then the route's own default of the whole rotor.
        symmetry=str(route_params.get("symmetry")
                     or ps.get("symmetry") or "full"),
        geo=route_params.get("geo"),
    )
    # THE LIMIT SPEED, AUTOMATIC (owner 2026-09-21: "нужно эту максимальную
    # скорость вращения обязательно добавлять в отчёт").  Only here — the
    # coupled loop's own hook — never on a plain interactive Solve, and never
    # for a solve marked `record: false` (nobody will ever read it).
    if isinstance(out, dict):
        try:
            from motor_ai_sim import run_recording as _rr
            recorded = not _rr.suppressed()
        except Exception:  # noqa: BLE001
            recorded = True
        if recorded:
            block = _auto_limit_speed(out, route_params.get("geo"))
            if block is not None:
                out["limit_speed"] = block
                _refile_with_limit_speed(out)
    return out


def run_modes_at(**route_params) -> Dict[str, Any]:
    """Solve the ring modes exactly as **Solve modes** on the Mechanical tab
    does — THE HOOK the coupled orchestrator calls (2026-09-13, user: "при
    каплинге чтобы всё решалось — и модальный, и частоты, чтобы к отчёту было
    всё готово").

    No temperature enters: the modal model is bonded, unprestressed and reads
    none (its own ``assumptions`` string says so), so this hook is only about
    WHICH solve.  Resolved in the stress hook's three steps: ``route_params``
    (``body``, ``n``, ``support``, ``mesh_size_mm``, ``order``,
    ``winding_mass``, ``shapes``, ``rpm``, ``geo``), then the user's saved
    panel (``body``, ``support``, ``nModes``, ``meshMm`` — the tab's ONE mesh
    size, which is what the button sends too), then the route's defaults.  A
    rotor is solved free-free whatever the saved support says, as the panel
    itself enforces before it presses.  ``rpm`` is the speed the excitation
    table is built at and ``f_switch_hz`` the inverter carrier — the caller
    passes the RUN's own of both (2026-09-14: without the second, the ring-mode
    table was built on whatever machine the server happened to have loaded).
    Leaves the answer as the tab's last modal result and files the duty's copy,
    like the button.
    """
    authorization = route_params.pop("authorization", None)
    ps = _mech_panel_settings(authorization)
    body = str(route_params.get("body") or ps.get("body") or "rotor").strip().lower()
    support = str(route_params.get("support") or ps.get("support")
                  or "free").strip().lower()
    if body == "rotor":
        support = "free"
    n = route_params.get("n")
    if n is None:
        n = _panel_float(ps, "nModes")
    n = int(n) if n else 12
    n = max(1, min(40, n))
    mesh = route_params.get("mesh_size_mm")
    if mesh is None:
        mesh = _panel_float(ps, "meshMm")
    mesh = float(mesh) if mesh else 1.5           # the button's own fallback
    return modes(
        body=body, n=n, support=support, mesh_size_mm=mesh,
        order=int(route_params.get("order") or 2),
        winding_mass=bool(route_params.get("winding_mass", True)),
        shapes=bool(route_params.get("shapes", True)),
        rpm=route_params.get("rpm"),
        f_switch_hz=route_params.get("f_switch_hz"),
        geo=route_params.get("geo"),
    )


#: The shaft line the ``/critical_speeds`` route assumes when nothing says
#: otherwise — one copy, so the hook and the route cannot drift apart.
_SHAFT_LINE_DEFAULTS: Dict[str, float] = {
    "bearing_span_mm": 250.0, "stack_length_mm": 0.0, "stack_offset_mm": 0.0,
    "overhang_a_mm": 30.0, "overhang_b_mm": 30.0, "shaft_od_mm": 0.0,
    "shaft_id_mm": -1.0, "bearing_k_n_per_m": 2e8,
    "stack_stiffness_fraction": 0.0,
}


def run_critical_speeds_at(**route_params) -> Dict[str, Any]:
    """Solve the shaft's critical speeds as **Critical speeds** on the
    Mechanical tab does — the second HOOK of the coupled orchestrator.

    The shaft LINE is the drawing, not the electromagnetics: it comes from the
    user's saved panel ``beam`` (bearing span, overhangs, stack offset, shaft
    OD / ID, bearing stiffness — the catalogue card's once a bearing is named,
    stack EI fraction), each field falling back to the route's flagged default
    when it is empty or unparseable, exactly as the panel does before it
    presses.  ``n_modes`` 6 and the 3.0 mm beam mesh are what the button
    sends.  ``rpm`` is the rated speed the Campbell 1× line is placed against
    and ``f_switch_hz`` the inverter carrier of the excitation table beside the
    branches — the caller passes the run's own of both.  Leaves the answer as
    the tab's last rotordynamics result and files the duty's copy, like the
    button.
    """
    authorization = route_params.pop("authorization", None)
    ps = _mech_panel_settings(authorization)
    beam = ps.get("beam") if isinstance(ps.get("beam"), dict) else {}

    def _num(name: str) -> float:
        v = route_params.get(name)
        if v is None:
            v = beam.get(name)
        try:
            s = "" if v is None else str(v).strip()
            f = float(s) if s else float("nan")
        except (TypeError, ValueError):
            f = float("nan")
        return _SHAFT_LINE_DEFAULTS[name] if not math.isfinite(f) else f

    line = {k: _num(k) for k in _SHAFT_LINE_DEFAULTS}
    mesh = route_params.get("mesh_size_mm")
    return critical_speeds(
        **line,
        rpm=route_params.get("rpm"),
        n_modes=int(route_params.get("n_modes") or 6),
        rpm_max_factor=float(route_params.get("rpm_max_factor") or 1.3),
        mesh_size_mm=(float(mesh) if mesh else 3.0),
        # The run's own carrier, like `run_modes_at` — the excitation table
        # beside the whirl branches is built on it.
        f_switch_hz=route_params.get("f_switch_hz"),
        geo=route_params.get("geo"),
    )


# ---------------------------------------------------------------------------
# Modal — 2-D in-plane modes, and the shaft's critical speeds
# ---------------------------------------------------------------------------
# Added 2026-09-05 for the user's request: "нам нужно сделать ещё модальный
# анализ, чтобы понять все частоты — это очень важно для 20000 rpm".  Two
# endpoints because they are two different models of two different things: the
# ring modes of the iron (``/modes``) and the bending criticals of the shaft
# line (``/critical_speeds``).  Neither runs on its own — both are a button.

@router.get("/modes")
@_JOBS.queued("mechanical.modes", priority=_JOBS.Priority.INTERACTIVE)
def modes(
    body: str = Query(default="rotor", description="rotor | stator"),
    n: int = Query(default=12, ge=1, le=40, description="elastic modes wanted"),
    support: str = Query(default="free",
                         description="free | pinned (stator only: the outer "
                                     "surface held radially by a housing)"),
    mesh_size_mm: float = Query(default=2.5, gt=0.1, le=10.0),
    order: int = Query(default=2, ge=1, le=2),
    winding_mass: bool = Query(default=True,
                               description="stator only: add the winding copper "
                                           "as non-structural mass on the slot "
                                           "walls"),
    shapes: bool = Query(default=True, description="include the mode shapes"),
    rpm: Optional[float] = Query(default=None,
                                 description="speed the excitation table is "
                                             "built at; default = simulation.rpm"),
    f_switch_hz: Optional[float] = Query(default=None, ge=0.0,
                                         description="inverter carrier the "
                                                     "excitation table is built "
                                                     "on; 0 = no PWM line; "
                                                     "default = simulation."
                                                     "f_switch"),
    geo: Optional[str] = Query(default=None),
):
    """In-plane (per unit length) natural modes of the rotor or stator core.

    Every interface is BONDED and there is no centrifugal prestress — read the
    ``assumptions`` string before quoting a frequency.  The answer is a list of
    (f, circumferential order, nearest excitation, separation margin), plus the
    mode shapes as normalised per-vertex displacement fields.
    """
    from motor_ai_sim.simulation.mechanical import modal as mdm
    from motor_ai_sim.simulation.mechanical import rotor_stress as rsm

    speed = _default_rpm() if rpm is None else float(rpm)
    # The carrier of the RUN being solved, resolved once and carried into the
    # cache key, the stored record and the answer — an excitation table built
    # on 48 kHz and one built on 24 kHz are two different tables, and before
    # 2026-09-14 they shared a cache entry and no field said which was which.
    fsw = _f_switch(f_switch_hz)
    _progress.start(total=3 + int(n), phase="geometry build", kind="modes",
                    composition="")
    try:
        t0 = time.time()
        try:
            polys, motor, geo_ov = _live_polys(geo)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500,
                                detail=f"geometry build failed: {type(exc).__name__}: {exc}")

        assign = _assignments()
        fp = _live_fingerprint(geo_ov)
        key = ("modes", fp,
               tuple(sorted((str(k), str(v)) for k, v in assign.items())),
               tuple(sorted(_override_props().keys())),
               str(body), int(n), str(support), round(float(mesh_size_mm), 4),
               int(order), bool(winding_mass), round(float(speed), 6),
               (round(float(fsw), 3) if fsw else None))
        _params = {"body": body, "n": n, "support": support,
                   "mesh_size_mm": mesh_size_mm, "order": order,
                   "winding_mass": winding_mass, "rpm": speed,
                   "f_switch_hz": fsw}
        hit = mdm.cache_get(key)
        if hit is not None:
            out = dict(hit)
            # The carrier is part of `key`, so a hit was built on exactly this
            # one — stamp it even on entries that predate the field.  Stamped
            # BEFORE `cached`, so what is remembered is the answer, not the
            # fact that this request did not have to solve it.
            out["f_switch_hz"] = fsw
            _remember_last("modes", out, _params, out.get("geo_fingerprint"))
            out["cached"] = True
            if not shapes:
                out.pop("field", None)
            return out

        try:
            out = mdm.solve_modes(
                polys, dict(motor.parameters), assign, body=body, n_modes=n,
                support=support, mesh_size_mm=mesh_size_mm, order=order,
                material_overrides=_override_props(), winding_mass=winding_mass,
                with_shapes=True, rpm=speed, f_switch=fsw,
                progress=_progress.callback())
        except rsm.MissingMechanicalProperty as exc:
            raise HTTPException(
                status_code=422,
                detail={"error": str(exc),
                        "invalid_parameters": [{
                            "field": f"materials.{exc.part}", "value": exc.material,
                            "kind": "missing_mechanical_property",
                            "message": str(exc)}]})
        except ValueError as exc:
            raise HTTPException(status_code=422,
                                detail={"error": str(exc), "invalid_parameters": []})
        except Exception as exc:  # noqa: BLE001
            log.exception("modal solve failed")
            raise HTTPException(status_code=500,
                                detail=f"{type(exc).__name__}: {exc}")

        out["solve_time_s"] = round(time.time() - t0, 2)
        out["elapsed_s"] = _elapsed(t0)
        out["cached"] = False
        out["geo_fingerprint"] = fp
        # WHICH CARRIER this table was built on, in the record itself, so the
        # report can check it against the duty's own `sim.fSwitch` instead of
        # trusting whatever machine the server had loaded (A1, 2026-09-14).
        out["f_switch_hz"] = fsw
        mdm.cache_put(key, out)
        _remember_last("modes", out, _params, fp)
        if not shapes:
            out = {k: v for k, v in out.items() if k != "field"}
        return out
    finally:
        # Unconditional: an exception on any path must not leave
        # the progress endpoint reporting a live solve.
        _progress.finish()


@router.get("/critical_speeds")
@_JOBS.queued("mechanical.critical_speeds", priority=_JOBS.Priority.INTERACTIVE)
def critical_speeds(
    bearing_span_mm: float = Query(default=250.0, gt=0.0, le=5000.0),
    stack_length_mm: float = Query(default=0.0, ge=0.0, le=5000.0,
                                   description="0 = the config's motor_length"),
    stack_offset_mm: float = Query(default=0.0, ge=-2500.0, le=2500.0,
                                   description="+ moves the stack toward "
                                               "bearing B; 0 = centred"),
    overhang_a_mm: float = Query(default=30.0, ge=0.0, le=2000.0),
    overhang_b_mm: float = Query(default=30.0, ge=0.0, le=2000.0),
    shaft_od_mm: float = Query(default=0.0, ge=0.0, le=2000.0,
                               description="0 = 2 × rotor_inner_radius"),
    shaft_id_mm: float = Query(default=-1.0, ge=-1.0, le=2000.0,
                               description="-1 = 2 × shaft_inner_radius"),
    bearing_k_n_per_m: float = Query(default=2e8, gt=0.0, le=1e12),
    stack_stiffness_fraction: float = Query(default=0.0, ge=0.0, le=1.0),
    rpm: Optional[float] = Query(default=None,
                                 description="rated speed; default = simulation.rpm"),
    n_modes: int = Query(default=6, ge=2, le=12),
    rpm_max_factor: float = Query(default=1.3, ge=1.0, le=3.0,
                                  description="the shaded band on the Campbell "
                                              "plot; the sweep itself always "
                                              "runs to 3 × rated"),
    mesh_size_mm: float = Query(default=3.0, gt=0.1, le=10.0),
    f_switch_hz: Optional[float] = Query(default=None, ge=0.0,
                                         description="inverter carrier the "
                                                     "excitation table is built "
                                                     "on; 0 = no PWM line; "
                                                     "default = simulation."
                                                     "f_switch"),
    geo: Optional[str] = Query(default=None),
):
    """Forward / backward whirl vs speed, and the shaft's critical speeds.

    NOTHING here except the cross-section comes from the motor config: the
    bearing span, the overhangs, the shaft outside the stack and the bearing
    stiffness are the DRAWING, and they arrive as inputs with flagged defaults.
    The response repeats them under ``inputs.assumed`` so a number can never be
    quoted without the shaft line it was computed on.
    """
    from motor_ai_sim.simulation.mechanical import rotor_stress as rsm
    from motor_ai_sim.simulation.mechanical import rotordynamics as rdm

    speed = _default_rpm() if rpm is None else float(rpm)
    if speed <= 0:
        raise HTTPException(
            status_code=422,
            detail={"error": "no rated speed to place the criticals against",
                    "invalid_parameters": [{
                        "field": "rpm", "value": rpm, "kind": "missing",
                        "message": ("pass ?rpm=, or set simulation.rpm — a "
                                    "Campbell diagram of a machine with no "
                                    "rated speed has no 1× line to cross")}]})

    # Same rule as `/modes`: the carrier belongs to the run being solved.
    fsw = _f_switch(f_switch_hz)
    _progress.start(total=3 + rdm.N_CAMPBELL_POINTS, phase="geometry build",
                    kind="critical_speeds", composition="")
    try:
        t0 = time.time()
        try:
            polys, motor, geo_ov = _live_polys(geo)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500,
                                detail=f"geometry build failed: {type(exc).__name__}: {exc}")

        inp = rdm.ShaftInputs(
            bearing_span_mm=bearing_span_mm, stack_length_mm=stack_length_mm,
            stack_offset_mm=stack_offset_mm, overhang_a_mm=overhang_a_mm,
            overhang_b_mm=overhang_b_mm, shaft_od_mm=shaft_od_mm,
            shaft_id_mm=shaft_id_mm, bearing_k_n_per_m=bearing_k_n_per_m,
            stack_stiffness_fraction=stack_stiffness_fraction)

        assign = _assignments()
        fp = _live_fingerprint(geo_ov)
        key = ("crit", fp,
               tuple(sorted((str(k), str(v)) for k, v in assign.items())),
               tuple(sorted(_override_props().keys())),
               tuple(round(float(v), 6) for v in
                     (bearing_span_mm, stack_length_mm, stack_offset_mm,
                      overhang_a_mm, overhang_b_mm, shaft_od_mm, shaft_id_mm,
                      bearing_k_n_per_m, stack_stiffness_fraction, speed,
                      rpm_max_factor, mesh_size_mm)),
               int(n_modes), (round(float(fsw), 3) if fsw else None))
        _params = {"bearing_span_mm": bearing_span_mm,
                   "stack_length_mm": stack_length_mm,
                   "stack_offset_mm": stack_offset_mm,
                   "overhang_a_mm": overhang_a_mm, "overhang_b_mm": overhang_b_mm,
                   "shaft_od_mm": shaft_od_mm, "shaft_id_mm": shaft_id_mm,
                   "bearing_k_n_per_m": bearing_k_n_per_m,
                   "stack_stiffness_fraction": stack_stiffness_fraction,
                   "rpm": speed, "n_modes": n_modes,
                   "rpm_max_factor": rpm_max_factor, "mesh_size_mm": mesh_size_mm,
                   "f_switch_hz": fsw}
        hit = rdm.cache_get(key)
        if hit is not None:
            out = dict(hit)
            out["f_switch_hz"] = fsw
            _remember_last("critical_speeds", out, _params,
                           out.get("geo_fingerprint"))
            out["cached"] = True
            return out

        try:
            out = rdm.solve_rotordynamics(
                polys, dict(motor.parameters), assign, inp, speed,
                material_overrides=_override_props(), n_modes=n_modes,
                rpm_max_factor=rpm_max_factor, mesh_size_mm=mesh_size_mm,
                f_switch=fsw, progress=_progress.callback())
        except rsm.MissingMechanicalProperty as exc:
            raise HTTPException(
                status_code=422,
                detail={"error": str(exc),
                        "invalid_parameters": [{
                            "field": f"materials.{exc.part}", "value": exc.material,
                            "kind": "missing_mechanical_property",
                            "message": str(exc)}]})
        except ValueError as exc:
            # A shaft line that cannot exist names the number that is impossible —
            # the client-facing validation rule: never solve a machine nobody built.
            raise HTTPException(
                status_code=422,
                detail={"error": str(exc),
                        "invalid_parameters": [{
                            "field": "bearing_span_mm", "value": bearing_span_mm,
                            "kind": "impossible_shaft_line",
                            "message": str(exc)}]})
        except Exception as exc:  # noqa: BLE001
            log.exception("rotordynamics solve failed")
            raise HTTPException(status_code=500,
                                detail=f"{type(exc).__name__}: {exc}")

        out["solve_time_s"] = round(time.time() - t0, 2)
        out["elapsed_s"] = _elapsed(t0)
        out["cached"] = False
        out["geo_fingerprint"] = fp
        out["f_switch_hz"] = fsw
        rdm.cache_put(key, out)
        _remember_last("critical_speeds", out, _params, fp)
        return out
    finally:
        # Unconditional: an exception on any path must not leave
        # the progress endpoint reporting a live solve.
        _progress.finish()


@router.get("/materials")
def materials(geo: Optional[str] = Query(default=None)):
    """The mechanical card of every rotor part, with its source note.

    Serves the same resolution the solve uses, so "why is my sleeve stress that
    number" is answerable without running anything.  A part whose material has
    no mechanical data comes back with ``error`` set rather than a 422: this is
    the diagnostic view, and hiding the other three parts because one is
    incomplete would be the opposite of useful.
    """
    from motor_ai_sim.simulation.mechanical import rotor_stress as rsm

    try:
        polys, _motor, _ov = _live_polys(geo)
        has_sleeve = polys.get("sleeve") is not None
    except Exception:  # noqa: BLE001 - still answer for the parts we can
        has_sleeve = False

    assign = _assignments()
    ovp = _override_props()
    out: Dict[str, Any] = {"has_sleeve": has_sleeve, "parts": {}}
    from motor_ai_sim.materials import DEFAULT_PART_MATERIAL

    for part, key in rsm.PART_ASSIGNMENT_KEY.items():
        if part == "sleeve" and not has_sleeve:
            continue
        name = assign.get(key) or DEFAULT_PART_MATERIAL.get(part)
        row: Dict[str, Any] = {"assignment_key": key, "material": name}
        if not name:
            row["error"] = f"no materials.{key} in the config"
            out["parts"][part] = row
            continue
        try:
            pm = rsm.part_mech(part, str(name), ovp)
        except rsm.MissingMechanicalProperty as exc:
            row["error"] = str(exc)
            row["missing_key"] = exc.key
            out["parts"][part] = row
            continue
        row.update(
            density=pm.density,
            youngs_modulus_gpa=pm.E / 1e9,
            youngs_modulus_transverse_gpa=(pm.E_transverse / 1e9
                                           if pm.E_transverse else None),
            shear_modulus_gpa=(pm.G / 1e9 if pm.G else None),
            poisson_ratio=pm.nu,
            # Thermal expansion (2026-09-07), ppm/K.  None means the card has
            # none — the part then does not expand and the solve says so under
            # `thermal_notes` rather than refusing.
            cte_ppm_k_1=(pm.cte_1 * 1e6 if pm.cte_source == "card" else None),
            cte_ppm_k_2=(pm.cte_pair()[1] * 1e6
                         if pm.cte_source == "card" else None),
            cte_anisotropic=bool(pm.cte_anisotropic),
            strength_mpa=pm.strength / 1e6,
            strength_kind=pm.strength_kind,
            compressive_strength_mpa=(pm.compressive_strength / 1e6
                                      if pm.compressive_strength else None),
            tensile_strength_transverse_mpa=(pm.strength_transverse / 1e6
                                             if pm.strength_transverse else None),
            sf_criterion=rsm.sf_criterion_text(pm, part),
            orthotropic=pm.orthotropic,
            note=pm.source_note,
        )
        out["parts"][part] = row
    return out


# ---------------------------------------------------------------------------
# What the tab was last showing, and the bare cross-section
# ---------------------------------------------------------------------------
# User 2026-09-06: "когда я захожу и выхожу в Mechanical, графики пропадают.
# Нужно, чтобы по умолчанию: если нет расчётов — рисуется просто геометрия; если
# есть — подгружается последний расчёт; если были изменения текущей геометрии —
# нужно подсвечивать неактуальность текущего расчёта."
#
# Neither route SOLVES anything: /last is a lookup, /mesh is a mesher.  Solve
# stays the only way to compute a stress, a mode or a critical speed.

@router.get("/last")
def last(
    field: bool = Query(default=True,
                        description="include the heavy field / shape payloads"),
    geo: Optional[str] = Query(default=None),
):
    """The most recent rotor-stress / modal / critical-speed answers.

    Each one comes back with the geometry fingerprint it was SOLVED for and the
    live fingerprint now, so the panel can say "this result is for a previous
    geometry" instead of quietly drawing yesterday's rotor.  `stale_geometry` is
    ``None`` — UNKNOWN, never "fine" — when either fingerprint is missing: a
    staleness check that cannot prove a mismatch must not claim one.
    """
    from motor_ai_sim.routes._validation import parse_geo_override

    _load_last()
    geo_ov = parse_geo_override(geo)
    live = _live_fingerprint(geo_ov)

    def _entry(kind: str) -> Optional[Dict[str, Any]]:
        e = _LAST.get(kind)
        if not e or e.get("result") is None:
            return None
        res = e["result"]
        if not field and isinstance(res, dict) and "field" in res:
            res = {k: v for k, v in res.items() if k != "field"}
        fp = e.get("geometry_fingerprint")
        stale = (None if (not fp or not live or live == "nofp")
                 else bool(fp != live))
        return {"result": res, "params": e.get("params") or {},
                "geometry_fingerprint": fp, "computed_at": e.get("computed_at"),
                "stale_geometry": stale}

    out: Dict[str, Any] = {"live_geometry_fingerprint": live}
    for kind in _LAST_KINDS:
        out[kind] = _entry(kind)
    # 200 with `has_result: false` rather than a 404: "nothing solved yet" is the
    # NORMAL first state of the tab (it then draws the bare geometry), and a
    # console full of red 404s on every fresh mount is not an error report.
    out["has_result"] = any(out[k] is not None for k in _LAST_KINDS)
    return out


@router.get("/mesh")
def mesh(
    mesh_size_mm: float = Query(default=1.5, gt=0.1, le=10.0),
    order: int = Query(default=2, ge=1, le=2,
                       description="element order the SOLVE will use on this "
                                   "mesh; the triangles are the same either "
                                   "way, P2 only adds midside nodes"),
    geo: Optional[str] = Query(default=None),
):
    """The rotor cross-section as triangles — no solve, no fields.

    What the tab draws before anything has been computed, and since 2026-09-06
    also what the panel's **Build mesh** button calls: user, on the mechanical
    mesh, "она строится отдельно, и ей тоже нужно как-то управлять".  The SAME
    mesher ``rotor_stress`` runs (``build_rotor_mesh``), so the picture on an
    empty tab is the picture the solve will colour in, down to the element edges
    — the Mesh and Part toggles therefore work before the first Solve — and the
    solve that follows a Build mesh reuses the built mesh through
    ``rotor_stress``'s memo rather than meshing the same rotor twice.
    """
    import numpy as np

    from motor_ai_sim.simulation.mechanical import rotor_stress as rsm

    # One step, and it is the gmsh build.  A cache hit still starts and finishes
    # the bar (below): a poll that arrives after an instant answer must see
    # running=False, not the stale True of whatever ran before it.
    _progress.start(total=1, phase="mesh build", kind="mesh",
                    composition="one gmsh mesh build")
    try:
        t0 = time.time()
        try:
            polys, _motor, geo_ov = _live_polys(geo)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500,
                                detail=f"geometry build failed: {type(exc).__name__}: {exc}")

        fp = _live_fingerprint(geo_ov)
        key = (fp, round(float(mesh_size_mm), 4), int(order))
        hit = _MESH_CACHE.get(key)
        if hit is not None:
            out = dict(hit)
            out["cached"] = True
            # `mesh_s` stays the seconds the mesh actually COST when it was built —
            # a cache hit did not make the mesher faster, and the panel says
            # "cached" beside it rather than claiming a 0.0 s build.
            out["elapsed_s"] = _elapsed(t0)
            return out

        try:
            rm = rsm.build_rotor_mesh(polys, mesh_size_mm=mesh_size_mm,
                                      progress=_progress.callback())
        except ValueError as exc:
            # A rotor that meshes in disconnected pieces is named, not defaulted —
            # the same client-facing rule the solve routes follow.
            raise HTTPException(status_code=422,
                                detail={"error": str(exc), "invalid_parameters": []})
        except Exception as exc:  # noqa: BLE001
            log.exception("mechanical mesh build failed")
            raise HTTPException(status_code=500,
                                detail=f"{type(exc).__name__}: {exc}")

        pts = np.asarray(rm.mesh.p).T * 1e3          # m -> mm, the display unit
        tri = np.asarray(rm.mesh.t).T
        # The edge extremes: what the mesh-size field actually BOUGHT.  gmsh clamps
        # between MeshSizeMin and the requested max and refines on curvature, so the
        # number typed in the box is a target and these two are the outcome — the
        # only cheap way to see that "0.4 mm" was mostly ignored.  Three edges per
        # triangle on a few thousand elements is a numpy line, not a cost.
        e0 = pts[tri[:, 1]] - pts[tri[:, 0]]
        e1 = pts[tri[:, 2]] - pts[tri[:, 1]]
        e2 = pts[tri[:, 0]] - pts[tri[:, 2]]
        ed = np.hypot(*np.concatenate([e0, e1, e2]).T) if tri.size else np.zeros(0)
        out = {
            "vertices": np.round(pts, 5).astype(np.float32).tolist(),
            "triangles": tri.astype(np.int32).tolist(),
            "domain_per_tri": np.asarray(rm.part_tri).astype(np.int8).tolist(),
            "part_names": rsm.PART_NAMES,
            "outlines": rm.outlines,
            "extent": float(np.abs(pts).max()) if pts.size else 1.0,
            "n_nodes": int(pts.shape[0]),
            # `n_vertices` is the same count under the name the panel's mesh line
            # uses; `n_nodes` stays for every existing reader.
            "n_vertices": int(pts.shape[0]),
            "n_triangles": int(tri.shape[0]),
            "mesh_size_mm": float(mesh_size_mm),
            "element_order": int(order),
            # Seconds of gmsh this mesh cost to build, reported separately from the
            # request's own elapsed.  It stays the build cost when the mesh is handed
            # out again — "built in 3.2 s" is a property of the mesh, not of the
            # press — and `mesh_reused` is what says this call did not pay it.
            "mesh_s": float(rm.build_s),
            "mesh_reused": bool(rm.from_memo),
            "min_edge_mm": float(ed.min()) if ed.size else 0.0,
            "max_edge_mm": float(ed.max()) if ed.size else 0.0,
            "geo_fingerprint": fp,
            "cached": False,
        }
        out["elapsed_s"] = _elapsed(t0)
        if len(_MESH_CACHE) >= _MESH_CACHE_MAX:
            _MESH_CACHE.popitem(last=False)
        _MESH_CACHE[key] = out
        return out
    finally:
        # Unconditional: an exception on any path must not leave
        # the progress endpoint reporting a live solve.
        _progress.finish()
