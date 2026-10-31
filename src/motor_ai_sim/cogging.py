"""Cogging torque as a first-class parameter of a CONFIGURATION (owner 2026-10-26).

Owner, verbatim: «это важный параметр во все моторы — амплитуда или полуамплитуда зубцового момента».
Decision (orchestrator): the headline is the PEAK-TO-PEAK cogging torque in N·m; the half-amplitude and the share of
the configuration's continuous (rated) torque go to the tooltip.

WHAT IT IS.  Cogging is a property of the MACHINE (slots x poles x magnet card x geometry), not of a duty: it is the
torque the magnets make on the slotted stator with NO current at all.  A duty cannot carry it (the app refuses a duty
with 0 A, ``op.current_rpm_positive``), so it is a configuration-level measurement, saved as the ``cogging:`` block of
the configuration yaml (beside ``battery`` / ``bearings``) and shown wherever a motor's parameters are shown.

HOW IT IS COMPUTED (one rule, no second method):

* the sliding-band P2 transient, current drive at EXACTLY I = 0 (no armature field, nothing to rotate the d-axis for:
  ``daxis_deg`` is given, so no calibration solve is paid for), eddy and rotor eddy OFF, demagnetisation OFF;
* the iron is RE-CONVERGED at every rotor position (the project's Newton saturation solve, ``frozen_nu=False``).
  FROZEN PERMEABILITY (``frozen_nu=True``: saturation converged once at the first position and held) was tried first
  (the orchestrator's brief) and REJECTED as the default on evidence (2026-10-26, 40 mm 12s/14p, 2 mm mesh): it
  gave 0.699 N·m pk-pk, dominated by electrical harmonics 1, 2 and 4 that a symmetric machine cannot produce (the
  saturation pattern of frame 0 is frozen into the stator teeth and breaks the slot symmetry), against 0.064 N·m with
  the iron re-converged, whose spectrum has none of them (dominant order 42 = LCM/2, then 84 and 126).  The option stays for comparison and the stored
  block records which one made the number;
* ``sampling_purpose="cogging_quality"``: at least 6 raw samples per cogging cycle (this module asks for 12), over ONE
  electrical period, which always holds a whole number of cogging cycles (the cogging period is 360 deg / LCM(slots,
  poles) mechanical);
* the torque is the project's energy-consistent one: the Coulomb VIRTUAL-WORK torque (``torque_method="coulomb"``,
  owner 2026-09-30).  The flux-linkage (Clarke) torque cannot be used here at all: at I = 0 it is identically zero,
  and the Maxwell band over-reads (docs / memory: torque methodology), so virtual work is the energy method that
  exists at no load;
* the magnet card is the configuration's, at the temperature stated on the result (default: the card as the library
  quotes it; ``magnet_temp_c`` corrects Br / Hc along the card's own coefficients).

STALE.  The block carries the ``build_sig`` of the build it was computed on (die geometry + configuration overrides +
winding + materials + part states, ``routes.family._build_sig``) and the geometry hash; a build that moved since marks
it superseded (``stale``) exactly as a duty result on an older build is.  A stale value is still shown (amber, "computed
on an older build"), never silently dropped.

Pure functions first (``summarize``, ``cogging_cycles``, ``view``) so the unit tests need no solver.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

log = logging.getLogger(__name__)

BLOCK_KEY = "cogging"
SCHEMA = 1

#: Raw samples per cogging cycle this module asks for (the solver's quality gate is 6; the passport uses 12).
SAMPLES_PER_CYCLE = 12
#: What the label of the stored number says (one place; the datasheet / report / tooltip read it).
def method_label(frozen_nu: bool = False, samples_per_cycle: float = SAMPLES_PER_CYCLE) -> str:
    """The one-line method statement stored with the number (tooltip / datasheet / report read it).  The sample count
    is the DELIVERED one (the solver may snap the frame count), never the requested one."""
    return ("energy torque at no load: Coulomb virtual work, I = 0, "
            + ("frozen permeability" if frozen_nu else "iron re-converged at every rotor position")
            + ", eddy off, %g samples per cogging cycle over one electrical period" % round(float(samples_per_cycle), 2))


#: Most frames a cogging run may ask for (a 36-slot / 34-pole machine at 12 samples per cycle is 432).
MAX_FRAMES = 720
#: Lowest mesh size [mm] a request may ask for (the solver's own floor ``min_size_mm`` is checked per machine).
MIN_MESH_MM = 0.1
MAX_MESH_MM = 20.0


def overrides_of(*, frozen_nu: bool = False, mesh_size_mm: Optional[float] = None,
                 magnet_temp_c: Optional[float] = None, samples_per_cycle: int = SAMPLES_PER_CYCLE) -> List[str]:
    """What a request changed from the standard recipe (empty = the standard run).  A standard run is the only kind a
    non-admin may SAVE; an admin's saved override is named in the tooltip, the datasheet and the report."""
    out: List[str] = []
    if frozen_nu:
        out.append("frozen permeability")
    if mesh_size_mm is not None:
        out.append("mesh %g mm" % float(mesh_size_mm))
    if magnet_temp_c is not None:
        out.append("magnets at %g °C" % float(magnet_temp_c))
    if int(samples_per_cycle) != SAMPLES_PER_CYCLE:
        out.append("%d samples per cycle" % int(samples_per_cycle))
    return out


METHOD = method_label(False)
LABEL = "Cogging torque (pk-pk)"


# ─────────────────────────────────────────────────────────────────────────────
#  Pure numbers
# ─────────────────────────────────────────────────────────────────────────────

def slots_poles(geo: Mapping[str, Any]) -> Tuple[int, int]:
    """``(slots, poles)`` of the WHOLE machine from a geometry dict (the totals, else segments x per-segment)."""
    def _i(v: Any) -> int:
        try:
            return int(round(float(v)))
        except (TypeError, ValueError):
            return 0
    s, p = _i(geo.get("num_slots")), _i(geo.get("num_poles"))
    if s <= 0:
        s = _i(geo.get("num_seg") or 1) * _i(geo.get("num_slots_per_segment"))
    if p <= 0:
        p = _i(geo.get("num_seg") or 1) * _i(geo.get("num_poles_per_segment"))
    if s <= 0 or p <= 0 or p % 2:
        raise ValueError("cogging: the machine has no valid slot / pole count (slots=%s, poles=%s)" % (s, p))
    return s, p


def cogging_cycles(num_slots: int, num_poles: int) -> Dict[str, int]:
    """The cogging order of a slot / pole pair.

    ``order`` = LCM(slots, poles) cogging cycles per MECHANICAL revolution (the fundamental cogging order);
    ``cycles_per_electrical_period`` = ``order / pole_pairs`` - how many fit in one electrical period, which is the
    window every no-load run reports, so the window always holds a whole number of cycles."""
    s, p = int(num_slots), int(num_poles)
    if s <= 0 or p <= 0 or p % 2:
        raise ValueError("cogging_cycles: slots and poles must be positive and poles even")
    pairs = p // 2
    order = math.lcm(s, p)
    if order % pairs:
        raise ValueError("cogging order must tile an electrical period")
    return {"order": order, "pole_pairs": pairs, "cycles_per_electrical_period": order // pairs,
            "period_mech_deg": 360.0 / order}


def steps_for(num_slots: int, num_poles: int, per_cycle: int = SAMPLES_PER_CYCLE) -> int:
    """Frames per electrical period for ``per_cycle`` raw samples per cogging cycle."""
    return int(per_cycle) * cogging_cycles(num_slots, num_poles)["cycles_per_electrical_period"]


def summarize(torque_nm: List[float], *, num_slots: int, num_poles: int,
              continuous_torque_nm: Optional[float] = None) -> Dict[str, Any]:
    """Peak-to-peak, half-amplitude and the spectrum line of one no-load torque series.

    ``torque_nm`` is one electrical period, uniformly sampled (the transient's ``T_em_Nm``).  The DFT is taken over
    that window, where the cogging fundamental is harmonic ``cycles_per_electrical_period``."""
    vals = [float(v) for v in torque_nm]
    if len(vals) < 4 or not all(math.isfinite(v) for v in vals):
        raise ValueError("cogging: the torque series has %d samples or a non-finite value" % len(vals))
    cyc = cogging_cycles(num_slots, num_poles)
    n = len(vals)
    pkpk = max(vals) - min(vals)
    mean = sum(vals) / n
    # DFT of the window: amplitude of harmonic k = 2|X_k|/N
    amps: Dict[int, float] = {}
    for k in range(1, n // 2 + 1):
        re = sum(v * math.cos(2.0 * math.pi * k * i / n) for i, v in enumerate(vals))
        im = sum(v * math.sin(2.0 * math.pi * k * i / n) for i, v in enumerate(vals))
        amps[k] = (1.0 if k == n / 2 else 2.0) * math.hypot(re, im) / n
    k1 = cyc["cycles_per_electrical_period"]
    fund = amps.get(k1)
    dominant_k = max(amps, key=lambda k: amps[k]) if amps else None
    out: Dict[str, Any] = {
        "pkpk_Nm": pkpk,
        "half_amp_Nm": pkpk / 2.0,
        "mean_Nm": mean,
        "fundamental_order": cyc["order"],
        "cycles_per_electrical_period": k1,
        "fundamental_amp_Nm": fund,
        "dominant_order": (dominant_k * cyc["pole_pairs"] if dominant_k else None),
        "samples": n,
        "samples_per_cycle": n / k1,
    }
    ct = _pos(continuous_torque_nm)
    if ct is not None:
        out["continuous_torque_Nm"] = ct
        out["pct_of_continuous"] = 100.0 * pkpk / ct
    return out


def _pos(v: Any) -> Optional[float]:
    try:
        f = abs(float(v))
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) and f > 0.0 else None


def _r(v: Any, nd: int) -> Any:
    return None if v is None else round(float(v), nd)


# ─────────────────────────────────────────────────────────────────────────────
#  The stored block (what the configuration yaml carries)
# ─────────────────────────────────────────────────────────────────────────────

def geo_hash(geo: Mapping[str, Any]) -> str:
    """Hash of the RESOLVED geometry the run saw (so a save can refuse a run of another machine)."""
    from motor_ai_sim.passport_v1.snapshot import sha256_of
    return sha256_of(dict(geo))[:16]


def build_block(run: Mapping[str, Any], *, build_sig: Optional[str]) -> Dict[str, Any]:
    """The ``cogging:`` block of a configuration yaml, from a finished run (``run()``'s answer).

    Numbers are rounded for a file an engineer can read; the waveform is kept (it is one electrical period,
    ~100-300 points) so the Simulation tab / report can draw it without a re-run."""
    s = run["summary"]
    wf = run.get("waveform") or {}
    blk: Dict[str, Any] = {
        "schema": SCHEMA,
        "pkpk_Nm": _r(s["pkpk_Nm"], 5),
        "half_amp_Nm": _r(s["half_amp_Nm"], 5),
        "mean_Nm": _r(s.get("mean_Nm"), 5),
        "fundamental_order": s["fundamental_order"],
        "fundamental_amp_Nm": _r(s.get("fundamental_amp_Nm"), 5),
        "dominant_order": s.get("dominant_order"),
        "cycles_per_electrical_period": s["cycles_per_electrical_period"],
        "samples_per_cycle": _r(s.get("samples_per_cycle"), 2),
        "method": run.get("method") or METHOD,
        "torque_method": run.get("torque_method"),
        "frozen_nu": bool(run.get("frozen_nu", False)),
        "converged": run.get("converged"),
        "overrides": list(run.get("overrides") or []) or None,
        "magnet_card": run.get("magnet_card"),
        "magnet_temp_c": run.get("magnet_temp_c"),
        "magnet_temp_source": run.get("magnet_temp_source"),
        "mesh": run.get("mesh"),
        "geo_hash": run.get("geo_hash"),
        "build_sig": build_sig,
        "computed_at": run.get("computed_at"),
        "runtime_s": _r(run.get("runtime_s"), 1),
        "solver": run.get("solver"),
        "waveform": {"angle_deg": [round(float(a), 4) for a in wf.get("angle_deg") or []],
                     "torque_Nm": [round(float(t), 6) for t in wf.get("torque_Nm") or []]},
    }
    return {k: v for k, v in blk.items() if v is not None}


def current_build_sig(die_doc: Mapping[str, Any], cfg_doc: Mapping[str, Any]) -> str:
    """``routes.family._build_sig`` - the ONE fingerprint of the build every per-configuration result is judged by."""
    from motor_ai_sim.routes import family
    return family._build_sig(dict(die_doc or {}), dict(cfg_doc or {}))


def is_stale(block: Mapping[str, Any], current_sig: Optional[str]) -> bool:
    """True when the build moved since the block was computed.  A block with no stamp is UNKNOWN, which is stale; so
    is one judged against no signature at all (a build that cannot be fingerprinted cannot vouch for the number)."""
    sig = block.get("build_sig")
    return not sig or not current_sig or str(sig) != str(current_sig)


def _s1_ok(cr: Any) -> bool:
    """The datasheet's own S1 rule: feasible and VERIFIED by an electromagnetic pass (an unverified estimate is never a
    base for a percentage)."""
    if not isinstance(cr, Mapping) or not cr:
        return False
    try:
        from motor_ai_sim.report import continuous_rating_feasible
        feasible = continuous_rating_feasible(dict(cr))
    except Exception:                                           # noqa: BLE001 - same rule, written out
        feasible = bool(cr.get("verified") is not False and cr.get("ok", True) and cr.get("feasible", True)
                        and cr.get("trustworthy", True))
    return feasible and cr.get("verified") is True


def continuous_torque(cfg_doc: Mapping[str, Any], stored_by_duty: Optional[Mapping[str, Any]] = None
                      ) -> Tuple[Optional[float], Optional[str], Optional[str]]:
    """``(N·m, source, basis)`` of the torque the cogging percentage is taken against.

    ``basis`` is ``"continuous"`` for the S1 search's own VERIFIED answer (a stored ``coupled.continuous_rating``
    torque - the machine's real continuous rating, the datasheet's checks), else ``"rated duty"`` for the rated duty's
    solved torque (labelled as such wherever it is shown - it is not a continuous rating), else ``(None, None, None)``:
    no percentage is then claimed."""
    duties = [d for d in (cfg_doc.get("duties") or []) if isinstance(d, dict) and d.get("name")]
    names = [str(d["name"]) for d in duties]
    order = list(names)
    try:
        from motor_ai_sim.routes import family
        rated = family.config_rated_duty(dict(cfg_doc))
        if rated in names:
            order = [rated] + [n for n in names if n != rated]
    except Exception:                                           # noqa: BLE001
        pass
    stored = stored_by_duty or {}
    for n in order:
        cp = (stored.get(n) or {}).get("coupled")
        cr = cp.get("continuous_rating") if isinstance(cp, Mapping) else None
        if _s1_ok(cr):
            t = _pos((cr.get("power") or {}).get("T_em_Nm")) if isinstance(cr.get("power"), Mapping) else None
            if t is not None:
                return t, "verified continuous rating (S1 search) of duty '%s'" % n, "continuous"
    for n in order:
        d = next((x for x in duties if str(x.get("name")) == n), {})
        for holder in (d.get("summary"), d.get("result")):
            if isinstance(holder, Mapping):
                for key in ("T_em_avg_Nm", "t_em_avg_nm", "T_avg_Nm", "torque_nm"):
                    t = _pos(holder.get(key))
                    if t is not None:
                        return t, "solved torque of duty '%s'" % n, "rated duty"
    return None, None, None


def view(block: Optional[Mapping[str, Any]], *, current_sig: Optional[str] = None,
         cfg_doc: Optional[Mapping[str, Any]] = None,
         stored_by_duty: Optional[Mapping[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """What a screen / datasheet / report shows of a saved block, or ``None`` when there is nothing to show.

    FLAT, small and waveform-free.  ``current_sig`` is the configuration's ``build_sig`` NOW (``stale`` follows it).
    ``pct_of_continuous`` is computed here against the configuration's continuous torque at READ time (it follows a
    re-rated duty without a re-run).  No value, or a value of exactly 0 or not finite, is "nothing" (owner rule: no
    empty / zero rows)."""
    if not isinstance(block, Mapping):
        return None
    try:
        pk = float(block.get("pkpk_Nm"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(pk) or pk <= 0.0:
        return None
    out: Dict[str, Any] = {
        "pkpk_Nm": pk,
        "half_amp_Nm": float(block.get("half_amp_Nm") or pk / 2.0),
        "fundamental_order": block.get("fundamental_order"),
        "fundamental_amp_Nm": block.get("fundamental_amp_Nm"),
        "magnet_temp_c": block.get("magnet_temp_c"),
        "magnet_card": block.get("magnet_card"),
        "method": block.get("method") or METHOD,
        "computed_at": block.get("computed_at"),
        "stale": is_stale(block, current_sig),
    }
    if block.get("overrides"):
        out["overrides"] = [str(o) for o in block["overrides"]]
    if block.get("converged") is False:
        out["converged"] = False
    if cfg_doc is not None:
        ct, src, basis = continuous_torque(cfg_doc, stored_by_duty)
        if ct is not None:
            out["continuous_torque_Nm"] = ct
            out["continuous_torque_source"] = src
            if basis == "continuous":
                out["pct_of_continuous"] = 100.0 * pk / ct
            else:
                out["pct_of_rated_duty"] = 100.0 * pk / ct
    return out


def flat_keys(v: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """The ``view`` as the flat ``cogging_*`` keys a project / Configure comparison row carries (empty when none)."""
    if not v:
        return {}
    out: Dict[str, Any] = {"cogging_pkpk_nm": v["pkpk_Nm"], "cogging_half_nm": v["half_amp_Nm"],
                           "cogging_stale": bool(v.get("stale"))}
    for src, dst in (("pct_of_continuous", "cogging_pct_of_continuous"),
                     ("pct_of_rated_duty", "cogging_pct_of_rated_duty"), ("magnet_temp_c", "cogging_magnet_temp_c"),
                     ("fundamental_order", "cogging_order"), ("method", "cogging_method")):
        if v.get(src) is not None:
            out[dst] = v[src]
    if v.get("overrides"):
        out["cogging_overrides"] = "; ".join(v["overrides"])
    if v.get("pct_of_rated_duty") is not None and v.get("continuous_torque_source"):
        out["cogging_torque_source"] = v["continuous_torque_source"]
    return out


def block_of(cfg_doc: Optional[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    b = (cfg_doc or {}).get(BLOCK_KEY)
    return dict(b) if isinstance(b, Mapping) else None


def for_config(die: str, cfg_doc: Mapping[str, Any], stored_by_duty: Optional[Mapping[str, Any]] = None,
               current_sig: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """``view`` of a configuration document read from the catalog (the die document is loaded for the stale check
    unless the caller already has the configuration's current ``build_sig``).  Never raises."""
    blk = block_of(cfg_doc)
    if blk is None:
        return None
    try:
        if current_sig is None:
            from motor_ai_sim.routes import family
            current_sig = current_build_sig(family._die_doc_for_sig(die), cfg_doc)
        return view(blk, current_sig=current_sig, cfg_doc=cfg_doc, stored_by_duty=stored_by_duty)
    except Exception:                                           # noqa: BLE001 - a display must never fail a read
        log.exception("cogging view failed for %s", die)
        return None


# ─────────────────────────────────────────────────────────────────────────────
#  The run
# ─────────────────────────────────────────────────────────────────────────────

_ABSENT_MATERIALS = {"slot_insulation": "Nomex", "wire_insulation": "polyimide"}


def _default_duty(cfg_doc: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        from motor_ai_sim.routes import family
        name = family.config_default_duty(dict(cfg_doc))
    except Exception:                                           # noqa: BLE001
        name = None
    for d in cfg_doc.get("duties") or []:
        if isinstance(d, dict) and (name is None or d.get("name") == name):
            return d
    return None


def machine_from_config(die_doc: Mapping[str, Any], cfg_doc: Mapping[str, Any],
                        overrides: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Everything a no-load run needs about ONE catalogued machine, from its two yaml documents.

    Mesh settings come from the configuration's default duty's Mesh-tab block (the machine's own setting; owner
    2026-09-30: no hidden defaults); when it has none the machine's saved Mesh config applies, and the result says
    which.  ``overrides`` (``mesh_size_mm`` ...) win, for a deliberately coarse check."""
    from motor_ai_sim.passport_v1 import snapshot as S
    geo = S.resolve_geometry(die_doc, cfg_doc)
    slots, poles = slots_poles(geo)
    winding = dict(cfg_doc.get("winding") or {})
    duty = _default_duty(cfg_doc)
    mesh: Dict[str, Any]
    source: str
    try:
        if duty is None:
            raise S.SnapshotError("no duty")
        mesh = S.duty_mesh_settings(duty)
        source = "duty '%s' Mesh block" % duty.get("name")
    except S.SnapshotError:
        from motor_ai_sim.mesh_settings import resolve_mesh_settings
        vals, _src = resolve_mesh_settings({})
        mesh = dict(vals)
        mesh.update(component_mesh_mm={}, iron_template=True, geo_mesh=True, pole_copy=False)
        source = "machine Mesh settings"
    m_duty = (duty or {}).get("mesh") or {}
    try:
        ewf = float(m_duty.get("sim.endWinding")) if m_duty.get("sim.endWinding") not in (None, "") else 0.0
    except (TypeError, ValueError):
        ewf = 0.0
    try:
        rpm = float(m_duty.get("sim.rpm") or (duty or {}).get("rpm") or 1000.0)
    except (TypeError, ValueError):
        rpm = 1000.0
    # The d-axis the machine is calibrated to (its duty's saved summary, else the Simulation-tab pin).  At I = 0 the
    # d-axis has no physical role, but the solver refuses a pin more than 15 deg from the topology's calibration on
    # file (fem_solver_2d d-axis guard) - so the run pins THIS machine's own value, not 0 (G2-L40 24s/28p: 60 deg).
    daxis = None
    for v in (((duty or {}).get("summary") or {}).get("daxis_deg"), m_duty.get("sim.daxisDeg")):
        try:
            if v not in (None, "") and math.isfinite(float(v)):
                daxis = float(v)
                break
        except (TypeError, ValueError):
            continue
    assignment = {str(k): str(v) for k, v in (cfg_doc.get("materials") or {}).items() if v}
    for k, v in _ABSENT_MATERIALS.items():
        assignment.setdefault(k, v)
    mach = {
        "die": die_doc.get("name"), "config": cfg_doc.get("name"),
        "geo_override": geo, "num_slots": slots, "num_poles": poles,
        "connection": (winding.get("connection") or None),
        "star_delta": str(winding.get("star_delta") or (duty or {}).get("star_delta")
                          or m_duty.get("sim.starDelta") or "star"),
        "mesh": mesh, "mesh_source": source, "end_winding_factor": ewf, "rpm": rpm, "daxis_deg": daxis,
        "assignment": assignment, "magnet_card": assignment.get("magnet"),
        "live": False,
    }
    for k, v in (overrides or {}).items():
        if v is not None and k in ("mesh_size_mm", "min_size_mm", "outer_air_factor", "gap_layers", "n_sectors"):
            mach["mesh"] = dict(mach["mesh"], **{k: v})
            mach["mesh_source"] += " (+ request override %s)" % k
    return mach


def machine_live(geo_override: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """The LIVE machine (the workspace's loaded config, optionally with a per-request ``geo`` override)."""
    from motor_ai_sim.config import get_config
    from motor_ai_sim.mesh_settings import resolve_mesh_settings
    from motor_ai_sim.simulation.geometry_2d import merge_geo_override
    cfg = get_config() or {}
    geo = dict(cfg.get("geometry") or {})
    if geo_override:
        try:
            geo = dict(merge_geo_override(geo, dict(geo_override)))
        except Exception:                                       # noqa: BLE001 - the override alone is the machine
            geo = dict(geo_override)
    slots, poles = slots_poles(geo)
    vals, _src = resolve_mesh_settings({}, cfg)
    mesh = dict(vals)
    mesh.update(component_mesh_mm={}, iron_template=True, geo_mesh=True, pole_copy=False)
    wind = cfg.get("winding") or {}
    return {
        "die": None, "config": None, "geo_override": (dict(geo_override) if geo_override else None),
        "geo_resolved": geo, "num_slots": slots, "num_poles": poles,
        "connection": wind.get("connection") or None, "star_delta": str(wind.get("star_delta") or "star"),
        "mesh": mesh, "mesh_source": "machine Mesh settings", "end_winding_factor": 0.0, "rpm": None,
        "assignment": None, "magnet_card": (cfg.get("materials") or {}).get("magnet"), "live": True,
    }


def _clamp_sectors(requested: int, slots: int, poles: int) -> int:
    """Largest divisor of gcd(slots, poles) not above ``requested`` that is >= 2, else -1 (full machine)."""
    g = math.gcd(slots, poles)
    best = -1
    for d in range(2, g + 1):
        if g % d == 0 and d <= max(int(requested), 1):
            best = d
    return best


def _magnet_reference(card: Optional[str], magnet_temp_c: Optional[float]) -> Tuple[Optional[float], str]:
    """``(°C, source)`` of the magnet state a run is at."""
    if magnet_temp_c is not None:
        return float(magnet_temp_c), "requested"
    try:
        from motor_ai_sim.materials import get_material
        t = getattr(get_material("magnet", str(card)), "temperature_c", None)
        if t is not None:
            return float(t), "magnet card reference temperature"
    except Exception:                                           # noqa: BLE001
        pass
    return None, "magnet card as the library quotes it"


def _solver_stamp() -> Optional[Dict[str, Any]]:
    try:
        from motor_ai_sim import duty_results
        return duty_results.solver_stamp()
    except Exception:                                           # noqa: BLE001
        return None


def run(machine: Mapping[str, Any], *, magnet_temp_c: Optional[float] = None, frozen_nu: bool = False,
        samples_per_cycle: int = SAMPLES_PER_CYCLE, continuous_torque_nm: Optional[float] = None,
        em_transient_eval: Optional[Callable[..., Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Solve the no-load cogging of ``machine`` (``machine_from_config`` / ``machine_live``).

    Nothing is cached, recorded or written: this is a measurement, saving is a separate, explicit step
    (``build_block`` + the route).  ``em_transient_eval`` is injectable for tests."""
    if em_transient_eval is None:
        from motor_ai_sim.solve_pool import em_transient_eval as _eval
        em_transient_eval = _eval
    per = int(samples_per_cycle)
    if not 6 <= per <= 48:
        raise ValueError("samples_per_cycle must be 6..48 (the solver's quality gate is 6)")
    slots, poles = int(machine["num_slots"]), int(machine["num_poles"])
    steps = steps_for(slots, poles, per)
    if steps > MAX_FRAMES:
        raise ValueError("cogging: %d samples per cycle x %d cycles = %d frames, above the %d-frame limit - lower "
                         "samples_per_cycle" % (per, cogging_cycles(slots, poles)["cycles_per_electrical_period"],
                                                steps, MAX_FRAMES))
    mesh = dict(machine["mesh"])
    msz, mmin = float(mesh["mesh_size_mm"]), float(mesh["min_size_mm"])
    if not (math.isfinite(msz) and MIN_MESH_MM <= msz <= MAX_MESH_MM) or msz < mmin:
        raise ValueError("cogging: mesh_size_mm %g is outside %g..%g mm or below the machine's smallest element %g mm"
                         % (msz, MIN_MESH_MM, MAX_MESH_MM, mmin))
    sectors = _clamp_sectors(int(mesh.get("n_sectors", -1)), slots, poles)
    kw: Dict[str, Any] = dict(
        n_steps_per_period=steps, sampling_purpose="cogging_quality", n_periods=1.0,
        gamma_deg=0.0, I_phase_rms=0.0, daxis_deg=float(machine.get("daxis_deg") or 0.0),
        connection=machine.get("connection"), star_delta=machine.get("star_delta") or "star",
        mesh_size_mm=float(mesh["mesh_size_mm"]), min_size_mm=float(mesh["min_size_mm"]),
        outer_air_factor=float(mesh["outer_air_factor"]), gap_layers=float(mesh["gap_layers"]),
        n_sectors=sectors, stator_fillet_mm=0.0, coil_temp_c=20.0,
        magnet_temp_c=(None if magnet_temp_c is None else float(magnet_temp_c)),
        end_winding_factor=float(machine.get("end_winding_factor") or 0.0),
        rotor_eddy=False, eddy=False, demag=False, frozen_nu=bool(frozen_nu),
        component_mesh_mm=dict(mesh.get("component_mesh_mm") or {}) or None,
        geo_override=machine.get("geo_override"),
        iron_template=bool(mesh.get("iron_template", True)), geo_mesh=bool(mesh.get("geo_mesh", True)),
        pole_copy=bool(mesh.get("pole_copy", False)),
        structured_gap=True, airgap_macro=False, hi_fidelity=False, element_order=2,
        torque_method="coulomb", inc_ldq=False, drive="current",
    )
    if machine.get("rpm"):
        kw["rpm"] = float(machine["rpm"])
    # Materials: a catalogued machine brings its own assignment; the live machine uses the request's context.
    token = None
    if machine.get("assignment"):
        from motor_ai_sim import material_context as _mc
        prev = _mc.get_request_materials()
        merged = {"assignment": dict(machine["assignment"]), "materials": dict((prev or {}).get("materials") or {})}
        token = _mc._OVERRIDE.set(merged)
    t0 = time.time()
    try:
        res = em_transient_eval(**kw)
    finally:
        if token is not None:
            from motor_ai_sim import material_context as _mc
            _mc._OVERRIDE.reset(token)
    runtime = time.time() - t0
    series = [float(v) for v in (res.get("T_em_Nm") or [])]
    angle = [float(a) for a in (res.get("rotor_angle_deg") or [])]
    if not series:
        raise RuntimeError("cogging: the solver returned no torque series")
    # THE TORQUE IS THE ONE THE LABEL SAYS.  If the Coulomb set-up fails the solver only warns and hands back the
    # Maxwell / hybrid series (fem_solver_2d, virtual_work_torque): never store that as "virtual work".
    if res.get("torque_method") != "coulomb_virtual_work":
        raise RuntimeError("cogging: the solver reported torque method %r, not 'coulomb_virtual_work' (the Coulomb "
                           "set-up failed and a different torque was returned) - nothing is stored"
                           % (res.get("torque_method"),))
    # A run that did not converge is not a measurement.
    conv = res.get("picard_converged")
    if conv is False:
        raise RuntimeError("cogging: the saturation solve did not converge on frame(s) %s - nothing is stored"
                           % (list(res.get("picard_unconverged_frames") or [])[:10],))
    # THE SAMPLES THE SOLVER DELIVERED, not the ones asked for: it snaps the frame count to a slip-ring divisor.
    cyc = cogging_cycles(slots, poles)["cycles_per_electrical_period"]
    delivered_spc = len(series) / cyc
    if delivered_spc < per - 1e-9:
        raise RuntimeError("cogging: the solver delivered %d frames = %.2f samples per cogging cycle, below the %d "
                           "asked for (it snapped the frame count) - nothing is stored"
                           % (len(series), delivered_spc, per))
    summ = summarize(series, num_slots=slots, num_poles=poles, continuous_torque_nm=continuous_torque_nm)
    card = machine.get("magnet_card") or _card_from_result(res)
    mtemp, msrc = _magnet_reference(card, magnet_temp_c)
    geo = machine.get("geo_resolved") or machine.get("geo_override") or {}
    return {
        "summary": summ,
        "waveform": {"angle_deg": angle if len(angle) == len(series) else list(range(len(series))),
                     "torque_Nm": series},
        "method": method_label(bool(res.get("frozen_nu", frozen_nu)), delivered_spc),
        "torque_method": res.get("torque_method"),
        "frozen_nu": bool(res.get("frozen_nu", frozen_nu)),
        "magnet_card": card, "magnet_temp_c": mtemp, "magnet_temp_source": msrc,
        "mesh": {"mesh_size_mm": kw["mesh_size_mm"], "min_size_mm": kw["min_size_mm"],
                 "outer_air_factor": kw["outer_air_factor"], "gap_layers": kw["gap_layers"],
                 "gap_layers_effective": res.get("gap_layers_effective"), "n_sectors": sectors,
                 "steps_per_period": len(series), "steps_requested": steps, "source": machine.get("mesh_source")},
        "geo_hash": geo_hash(geo) if geo else None,
        "die": machine.get("die"), "config": machine.get("config"),
        "computed_at": datetime.now().replace(microsecond=0).isoformat(),
        "runtime_s": runtime, "solver": _solver_stamp(),
        "converged": (None if conv is None else bool(conv)), "sampling": {
            k: res.get(k) for k in ("cogging_sampling_purpose", "cogging_raw_samples_per_cycle",
                                    "cogging_sampling_sufficient")},
    }


def _card_from_result(res: Mapping[str, Any]) -> Optional[str]:
    for key in ("magnet_material", "magnet_name", "magnet_card"):
        if res.get(key):
            return str(res[key])
    return None


def cogging_id_of(r: Mapping[str, Any]) -> str:
    h = hashlib.sha1(json.dumps([r.get("die"), r.get("config"), r.get("computed_at"), r["summary"]["pkpk_Nm"]],
                                default=str).encode()).hexdigest()
    return "cog_" + h[:12]
