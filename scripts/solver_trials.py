"""P2 solver TRIALS across the whole motor range — a measurement campaign.

The P2 sliding-band solver was built and tuned on the 30/40 mm 12s/14p machine.
This harness exercises THE SAME canonical entry point (``em_transient_eval``) on
every machine the repo stores — the 12 presets in ``config/motor_presets.json``
plus the ACTIVE ``config/motor_config.yaml`` (the control, whose numbers are
known good) — deduped by geometry, and records everything needed to say WHERE
the solver degrades.  It changes no physics and writes nothing into ``src/``.

Design notes
------------
* Every motor is evaluated through ``geo_override`` (+ an explicit
  ``set_request_materials``), so the user's global config / Simulation state is
  never mutated.  The only thing the run inherits from the global config is the
  ``winding`` block (n_parallel / n_series) — the solver API has NO per-request
  channel for it; see FINDING-WINDING in the report.
* Each motor runs in its OWN subprocess (``--only KEY``) so a segfault in the
  FEM stack, an exception or a >30 min hang costs one motor, not the campaign.
  Results are appended as JSON lines to ``scripts/_solver_trials_results.jsonl``
  so a crash loses nothing.
* Files are read with ``encoding="utf-8"`` and written with utf-8: the preset
  keys contain unicode subscripts (``ciano14_40_12_fe₁₆n₂``) and Windows cp1252
  would crash on them.

Usage
-----
    python scripts/solver_trials.py --list          # the plan, smallest first
    python scripts/solver_trials.py --only my_motor # one motor, in-process
    python scripts/solver_trials.py                 # the whole campaign
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import math
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "scripts" / "_solver_trials_results.jsonl"

# Where the motor DEFINITIONS are read from.  Default: the live config dir.
# ``SOLVER_TRIALS_INPUT_DIR`` points it at a frozen COPY instead — the live
# files are rewritten by the running backend / open UI while a multi-hour
# campaign is in flight (observed 2026-07-30: max_current 42 -> 44 and a wire
# section change mid-run), which would silently make two trials incomparable.
INPUT_DIR = Path(os.environ.get("SOLVER_TRIALS_INPUT_DIR") or (ROOT / "config"))

# ── trial protocol (identical for every motor) ───────────────────────────────
STEPS_PER_PERIOD = 24
N_PERIODS = 1.0
GAP_LAYERS = 2.0          # >= 2 element layers per HALF air gap (structured belt)
MIN_SIZE_MM = 0.3
OUTER_AIR = 1.2
ELEMENT_ORDER = 2
J_LIMIT_A_MM2 = 35.0      # the house rule used to synthesise a missing current
TIMEOUT_S = 30 * 60       # kill a motor that hangs longer than this
VARIANT_BASE_FE16N2 = "ciano14_40_12_fe₁₆n₂"   # the unicode preset key
# HYPOTHESIS, not stored data: the 200 mm presets carry no winding connection,
# and their stored torque is ~half of what the solver produces when the whole
# phase current is driven through one path.  This runs the same geometry at
# I/2 to TEST whether a 2-parallel-path winding is what those numbers assume.
INFERRED_NPAR = {"m200_20kw_base": 2}


# ─────────────────────────────────────────────────────────────────────────────
# motor set
# ─────────────────────────────────────────────────────────────────────────────
def _load_json(path: Path) -> Any:
    with io.open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _geom_signature(geo: Dict[str, Any]) -> str:
    """Dedupe key: the geometry that actually reaches the mesher, rounded."""
    keys = sorted(k for k, v in geo.items() if isinstance(v, (int, float)))
    return "|".join(f"{k}={round(float(geo[k]), 6)}" for k in keys)


def _topology(geo: Dict[str, Any]) -> Tuple[int, int, int]:
    """(num_slots, num_poles, n_sectors) with the solver's own resolution:
    explicit counts win, else segments x per-segment."""
    seg = int(geo.get("num_seg") or 1)
    slots = int(geo.get("num_slots") or (seg * int(geo.get("num_slots_per_segment") or 0)))
    poles = int(geo.get("num_poles") or (seg * int(geo.get("num_poles_per_segment") or 0)))
    sym = math.gcd(slots, poles) if (slots and poles) else 1
    return slots, poles, max(1, sym)


def _mesh_size_for(diameter_mm: float) -> float:
    """~1.4 mm on the 150 mm machine, scaled with the machine, clamped [0.5, 2.0].

    The solver additionally auto-refines to min(slot_width, tooth_width)/2, so
    this is a CEILING; the air gap is resolved separately by GAP_LAYERS, which
    is why the target may stay well above the gap width.
    """
    return round(min(2.0, max(0.5, 1.4 * float(diameter_mm) / 150.0)), 2)


def _n_parallel_from_connection(conn: Optional[str]) -> Optional[int]:
    import re
    c = (conn or "").strip()
    for pat, grp in ((r"^(\d+)S-(\d+)P$", 2), (r"^(\d+)P(\d+)S$", 1),
                     (r"^(\d+)S$", None), (r"^(\d+)P$", 1)):
        m = re.match(pat, c)
        if m:
            return 1 if grp is None else int(m.group(grp))
    return None


def build_motor_set() -> List[Dict[str, Any]]:
    """Union of presets + the ACTIVE config, deduped by geometry, smallest first.

    Catalog entries (``config/motor_catalog.json``) carry no geometry of their
    own — they reference a preset — so they are attached as REFERENCE numbers
    (their stored torque / ripple / efficiency) to the geometry they name.
    """
    presets = _load_json(INPUT_DIR / "motor_presets.json")
    catalog = _load_json(INPUT_DIR / "motor_catalog.json")

    refs: Dict[str, Dict[str, Any]] = {}
    for m in catalog.get("motors", []):
        pid = m.get("preset")
        if pid:
            refs.setdefault(str(pid), m)

    entries: List[Dict[str, Any]] = []
    for key, p in presets.items():
        geo = dict(p.get("geometry") or {})
        if not geo:
            continue
        entries.append({
            "key": key, "name": p.get("name") or key, "source": "preset",
            "geometry": geo,
            "sim": dict(p.get("simulation") or {}),
            "mesh_stored": dict(p.get("mesh") or {}),
            "metrics": dict(p.get("metrics") or {}),
            "ref": refs.get(key),
            "materials": p.get("materials"),
        })

    # the ACTIVE config — the control
    import yaml
    with io.open(INPUT_DIR / "motor_config.yaml", "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    entries.append({
        "key": "ACTIVE_config", "name": "ACTIVE motor_config.yaml (control)",
        "source": "active_config",
        "geometry": dict(cfg.get("geometry") or {}),
        "sim": dict(cfg.get("simulation") or {}),
        "mesh_stored": dict(cfg.get("mesh") or {}),
        "metrics": {}, "ref": None,
        "materials": dict(cfg.get("materials") or {}),
    })

    # dedupe by geometry — keep the first, remember the aliases.  The ACTIVE
    # config is never dropped (it is the control) but records its twin.
    out: List[Dict[str, Any]] = []
    seen: Dict[str, Dict[str, Any]] = {}
    for e in entries:
        sig = _geom_signature(e["geometry"])
        prev = seen.get(sig)
        if prev is not None and e["source"] != "active_config":
            prev.setdefault("aliases", []).append(e["key"])
            # a duplicate geometry may still carry reference numbers
            if prev.get("ref") is None and e.get("ref"):
                prev["ref"] = e["ref"]
            continue
        e["geom_signature_dup_of"] = prev["key"] if prev is not None else None
        seen.setdefault(sig, e)
        out.append(e)

    # ── material-override variants ───────────────────────────────────────────
    # No preset stores a material assignment, so every trial otherwise runs on
    # the global config's materials (B15AHV950M / F45SH_120C).  The entry NAMED
    # for Fe16N2 therefore cannot reproduce its own stored numbers, and the
    # low-Hc demag path (gate d) never gets exercised.  Add ONE variant that
    # assigns the library's Fe16N2_lab_best magnet through the per-request
    # material channel — same geometry, same operating point, magnet swapped.
    for _base_key, _mat in ((VARIANT_BASE_FE16N2, "Fe16N2_lab_best"),):
        _base = next((x for x in out if x["key"] == _base_key), None)
        if _base is None:
            continue
        _v = dict(_base)
        _v["key"] = f"{_base_key}@{_mat}"
        _v["name"] = f"{_base['name']} + {_mat} magnets (material override)"
        _v["source"] = "variant"
        _v["variant_of"] = _base_key
        _v["aliases"] = []
        _v["mat_override"] = {"assignment": {"magnet": _mat}}
        out.append(_v)

    # ── coil-current-equivalence variants (see FINDING-WINDING) ──────────────
    # The FEM only ever sees I_coil = I_phase / n_parallel, and n_parallel comes
    # from the GLOBAL config (there is no per-request winding channel), so an
    # entry stored as "2S-2P" or "4P" is driven with n_parallel times its
    # intended coil MMF.  For those entries add a second trial at
    # I_phase / n_parallel_entry, which reproduces the entry's INTENDED coil
    # current (and hence its field, torque and copper loss) exactly; only the
    # terminal voltage / KV then read n_parallel times high.
    for _e in list(out):
        _npar = (_n_parallel_from_connection((_e.get("sim") or {}).get("connection"))
                 or INFERRED_NPAR.get(_e["key"]))
        if not _npar or _npar <= 1 or _e["source"] == "variant":
            continue
        _v = dict(_e)
        _v["key"] = f"{_e['key']}@coilI"
        _v["name"] = f"{_e['name']} at I/n_parallel={_npar} (coil-current equivalence)"
        _v["source"] = "variant"
        _v["variant_of"] = _e["key"]
        _v["aliases"] = []
        _v["i_divide_by"] = _npar
        out.append(_v)

    for e in out:
        geo = e["geometry"]
        slots, poles, sym = _topology(geo)
        e["slots"], e["poles"], e["n_sectors"] = slots, poles, sym
        e["diameter_mm"] = float(geo.get("stator_diameter") or 0.0)
        e["length_mm"] = float(geo.get("motor_length") or 0.0)
        e["air_gap_mm"] = float(geo.get("air_gap") or 0.0)
        e["mesh_size_mm"] = _mesh_size_for(e["diameter_mm"])

    out.sort(key=lambda e: (e["diameter_mm"], e["length_mm"], e["key"]))
    return out


def operating_point(e: Dict[str, Any]) -> Dict[str, Any]:
    """Current / rpm / gamma for this entry, and WHICH path produced them."""
    sim = e["sim"]; geo = e["geometry"]
    wire_area = (float(geo.get("wire_width") or 0.0)
                 * float(geo.get("wire_height") or 0.0))     # mm^2, one strand
    cur = sim.get("max_current", sim.get("current_a"))
    rpm = sim.get("rpm")
    gam = sim.get("phase_offset_deg", sim.get("gamma_deg"))
    path = "stored"
    if cur is None or float(cur) <= 0:
        # J_coil <= 35 A/mm^2 house rule from the wire section
        cur = J_LIMIT_A_MM2 * wire_area
        path = "synthesised_J35"
    if rpm is None:
        rpm = 3000.0
        path += "+rpm_default"
    if gam is None:
        gam = 0.0
        path += "+gamma0"
    if e.get("i_divide_by"):
        cur = float(cur) / float(e["i_divide_by"])
        path += f"+coil_current_equivalence(/{e['i_divide_by']})"
    j = (float(cur) / wire_area) if wire_area > 1e-9 else 0.0
    return {
        "I_phase_rms_A": float(cur), "rpm": float(rpm), "gamma_deg": float(gam),
        "path": path, "wire_area_mm2": round(wire_area, 4),
        "J_coil_A_per_mm2": round(j, 1),
        "J_over_house_limit": bool(j > J_LIMIT_A_MM2),
        "coil_temp_c": float(sim.get("coil_temp_c") or 120.0),
        "connection_entry": sim.get("connection"),
        "n_parallel_entry": _n_parallel_from_connection(sim.get("connection")),
        "steps_stored": sim.get("steps_per_period"),
        "demag_stored": sim.get("demag"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# one trial
# ─────────────────────────────────────────────────────────────────────────────
class _WarnCapture(logging.Handler):
    """Collect every WARNING+ record emitted while the solver runs — the
    honesty flags that live only in the log."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.records: List[str] = []

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D102
        try:
            self.records.append(f"{record.levelname} {record.name}: {record.getMessage()}")
        except Exception:
            pass


def _mean(seq, default=0.0) -> float:
    try:
        vals = [float(x) for x in (seq or [])]
        return sum(vals) / len(vals) if vals else float(default)
    except Exception:
        return float(default)


def _gates(e: Dict[str, Any], op: Dict[str, Any], d: Dict[str, Any],
           n_parallel_used: int) -> Dict[str, Any]:
    """The five per-motor pass/fail gates + the reference-delta check."""
    g: Dict[str, Any] = {}

    # (a) nonlinear convergence on every reported frame
    unconv = list(d.get("picard_unconverged_frames") or [])
    g["a_nonlinear_converged"] = {
        "pass": bool(d.get("picard_converged", False)) and not unconv,
        "unconverged_frames": unconv,
        "resid_max": d.get("picard_resid_max"),
        "tol": d.get("picard_tol"),
        "fallback_frames": list(d.get("picard_fallback_frames") or []),
        "picard_iters_mean": d.get("picard_iters_mean"),
        "picard_iters_max": d.get("picard_iters_max"),
    }

    # (b) energy mean vs Maxwell mean — the same physics measured two ways
    t_en = float(d.get("T_avg_Nm") or 0.0)
    t_mx = float(d.get("T_avg_maxwell_Nm") or 0.0)
    rel = (abs(t_en - t_mx) / abs(t_en)) if abs(t_en) > 1e-9 else float("inf")
    g["b_energy_vs_maxwell"] = {
        "pass": bool(rel <= 0.05),
        "T_energy_Nm": round(t_en, 4), "T_maxwell_Nm": round(t_mx, 4),
        "rel_diff_pct": (round(rel * 100.0, 2) if math.isfinite(rel) else None),
        "torque_method": d.get("torque_method"),
    }

    # (c) copper-DC arithmetic.  Two independent identities:
    #     3*I^2*R_phase  ==  P_cu_dc_W               (R is derived from it)
    #     P_cu_dc_2d_solve_W  ==  P_cu_dc_W / k_end  (active length vs +end turns)
    i_ph = float(op["I_phase_rms_A"])
    r_ph = float(d.get("R_phase_ohm") or 0.0)
    p_dc = float(d.get("P_cu_dc_W") or 0.0)
    p_i2r = 3.0 * i_ph * i_ph * r_ph
    k_end = float(d.get("end_winding_factor") or 0.0)
    p_2d_solve = float(d.get("P_cu_dc_2d_solve_W") or 0.0)
    p_2d_expect = (p_dc / k_end) if k_end > 1e-9 else 0.0
    e1 = (abs(p_i2r - p_dc) / p_dc) if p_dc > 1e-9 else float("inf")
    # The solve-side 2-D DC only exists when the coupled eddy solve ran; with
    # eddy=False the key is 0.0 and comparing against it is meaningless (N/A).
    e2 = ((abs(p_2d_solve - p_2d_expect) / p_2d_expect)
          if (p_2d_expect > 1e-9 and bool(d.get("eddy_coupled")) and p_2d_solve > 0)
          else None)
    g["c_copper_dc_arithmetic"] = {
        "pass": bool(e1 <= 1e-3 and (e2 is None or e2 <= 0.10)),
        "P_cu_dc_W": round(p_dc, 3), "sum_I2R_W": round(p_i2r, 3),
        "rel_err_I2R_pct": (round(e1 * 100.0, 4) if math.isfinite(e1) else None),
        "k_end": round(k_end, 4),
        "P_cu_dc_2d_solve_W": round(p_2d_solve, 3),
        "P_cu_dc_2d_expected_W": round(p_2d_expect, 3),
        "rel_err_2d_pct": (round(e2 * 100.0, 2) if e2 is not None else None),
        "n_parallel_used": n_parallel_used,
    }

    # (d) demag map not saturated to zero
    coef = d.get("demag_coef_per_tri")
    if coef:
        vals = [float(x) for x in coef]
        derated = sorted(v for v in vals if v < 0.999)
        br_min = min(vals) if vals else 1.0
        # The per-magnet report row carries only the MINIMUM factor, and "98 % of
        # elements de-rated" says nothing about HOW MUCH.  The mean/quantiles over
        # the de-rated set are what decides whether a de-rating that deep can
        # leave the torque unchanged, so record them.
        _n = len(derated)
        _mean = (sum(derated) / _n) if _n else 1.0
        g["d_demag_map"] = {
            "pass": bool(br_min > 0.05),
            "n_derated": _n, "n_tris": len(vals),
            "Br_min": round(br_min, 4),
            "Br_mean_derated": round(_mean, 4),
            "Br_p05_derated": round(derated[int(0.05 * (_n - 1))], 4) if _n else None,
            "Br_p50_derated": round(derated[int(0.50 * (_n - 1))], 4) if _n else None,
            "Br_p95_derated": round(derated[int(0.95 * (_n - 1))], 4) if _n else None,
            "frac_derated": round(_n / max(1, len(vals)), 5),
            "report_rows": d.get("demag_report") or [],
        }
    else:
        g["d_demag_map"] = {"pass": None, "note": "no demag map returned"}

    # (e) no exception / mesh failure — set by the caller
    g["e_no_exception"] = {"pass": True}

    # (f) reference performance delta (>10 % = suspect provenance, not
    #     necessarily a solver error: the historical numbers were Maxwell-mean)
    ref_t = None
    for src in (e.get("ref") or {}, e.get("metrics") or {}):
        if ref_t is None and src.get("T_avg_Nm") is not None:
            ref_t = float(src["T_avg_Nm"])
    if ref_t:
        dt = (t_en - ref_t) / ref_t
        dt_mx = (t_mx - ref_t) / ref_t
        g["f_reference_delta"] = {
            "pass": bool(abs(dt) <= 0.10),
            "T_ref_Nm": ref_t,
            "delta_energy_pct": round(dt * 100.0, 1),
            "delta_maxwell_pct": round(dt_mx * 100.0, 1),
        }
    else:
        g["f_reference_delta"] = {"pass": None, "note": "entry stores no reference torque"}
    return g


def run_one(key: str) -> Dict[str, Any]:
    """Evaluate ONE motor and append the record to the results jsonl."""
    motors = {m["key"]: m for m in build_motor_set()}
    if key not in motors:
        raise SystemExit(f"unknown motor key {key!r}; try --list")
    e = motors[key]
    op = operating_point(e)

    # Diagnostic override: force a different symmetry sector for THIS run
    # (SOLVER_TRIALS_NS=1 -> full ring).  Used to test whether a gate-(c) /
    # gate-(b) discrepancy is a property of the anti-periodic wedge or of the
    # machine: same geometry, same operating point, only the sector changes.
    _ns_env = os.environ.get("SOLVER_TRIALS_NS")
    if _ns_env:
        e = dict(e)
        e["n_sectors_auto"] = e["n_sectors"]
        e["n_sectors"] = int(_ns_env)
        key = f"{key}@NS{int(_ns_env)}"
    # Same idea for the demagnetisation pre-pass: SOLVER_TRIALS_DEMAG=0 turns it
    # off so the TORQUE cost of the de-rating can be measured as a difference
    # instead of inferred from the map.
    _demag_on = os.environ.get("SOLVER_TRIALS_DEMAG", "1") != "0"
    if not _demag_on:
        key = f"{key}@demagOFF"
    # ...and for the coupled sigma*dA/dt eddy solve, so a demag on/off pair can
    # be repeated with and without it (the demag re-solve inside the frame loop
    # assembles `f = f_mag2 if eddy else ...` — a different right-hand side per
    # branch, so whether the de-rating reaches the reported field may depend on
    # this flag).
    _eddy_on = os.environ.get("SOLVER_TRIALS_EDDY", "1") != "0"
    if not _eddy_on:
        key = f"{key}@eddyOFF"

    # The config files are read at CHILD-SPAWN time, and they are not
    # necessarily static (a running backend / an open UI rewrites presets and
    # motor_config.yaml, and motor_ai_sim.config's cache follows the file's
    # mtime).  So every record carries the exact geometry + operating point it
    # ran, plus a digest, and the campaign can be audited for input drift after
    # the fact instead of assuming the inputs held still for hours.
    import hashlib as _hl
    _geom_json = json.dumps(e["geometry"], sort_keys=True, ensure_ascii=False)

    rec: Dict[str, Any] = {
        "key": key, "name": e["name"], "source": e["source"],
        "variant_of": e.get("variant_of"),
        "aliases": e.get("aliases", []),
        "geometry_used": dict(e["geometry"]),
        "geometry_sha256": _hl.sha256(_geom_json.encode("utf-8")).hexdigest()[:16],
        "diameter_mm": e["diameter_mm"], "length_mm": e["length_mm"],
        "slots": e["slots"], "poles": e["poles"], "n_sectors": e["n_sectors"],
        "air_gap_mm": e["air_gap_mm"],
        "operating_point": op,
        "protocol": {
            "n_steps_per_period": STEPS_PER_PERIOD, "n_periods": N_PERIODS,
            "mesh_size_mm": e["mesh_size_mm"], "min_size_mm": MIN_SIZE_MM,
            "gap_layers": GAP_LAYERS, "outer_air_factor": OUTER_AIR,
            "element_order": ELEMENT_ORDER, "structured_gap": True,
            "iron_template": True, "geo_mesh": True,
            "demag": _demag_on, "eddy": _eddy_on, "rotor_eddy": True,
            "mesh_size_rule": "1.4 mm * D/150, clamped [0.5, 2.0]; solver "
                              "auto-refines to min(slot,tooth)/2; gap resolved "
                              "by gap_layers=2 per half-gap",
            "mesh_size_stored_in_entry": e["mesh_stored"].get("mesh_size_mm"),
        },
        "ts_start": time.time(),
    }

    from motor_ai_sim.config import get_config
    from motor_ai_sim.material_context import set_request_materials
    from motor_ai_sim.simulation.fem_solver_2d import em_transient_eval

    cfg = get_config()

    # ── SPEED (F2, now FIXED — the argument exists) ──────────────────────────
    # ``em_transient_eval`` gained an explicit ``rpm`` parameter, so the entry's
    # OWN speed is passed as an argument instead of being smuggled in through
    # the shared config.  Nothing is patched and nothing is written: the YAML's
    # sha256 is checked below and stays unchanged.
    import hashlib
    _cfg_path = ROOT / "config" / "motor_config.yaml"
    _sha_before = hashlib.sha256(_cfg_path.read_bytes()).hexdigest()
    rpm_global = float(cfg.get("simulation", {}).get("rpm", 0.0) or 0.0)
    rec["speed_handling"] = {
        "rpm_entry": float(op["rpm"]), "rpm_global_config": rpm_global,
        "in_memory_patch_applied": False,
        "channel": "em_transient_eval(rpm=...) argument (F2 fixed)",
        "f_elec_expected_Hz": round(float(op["rpm"]) * (e["poles"] // 2) / 60.0, 3),
    }

    wind = dict(cfg.get("winding") or {})
    n_par_used = max(1, int(wind.get("n_parallel", 1) or 1))
    rec["winding_used"] = {
        "n_parallel": n_par_used, "n_series": wind.get("n_series"),
        "connection": wind.get("connection"), "layers": wind.get("layers"),
        "source": "global config (no per-request winding override exists)",
        "matches_entry": (op["n_parallel_entry"] is None
                          or op["n_parallel_entry"] == n_par_used),
    }
    # Per-request materials.  No preset/catalog entry stores a material
    # assignment, so the plain trials pass the explicit "no override" and run on
    # the global config's materials; only the explicit variants send one.
    _mat_ov = e.get("mat_override")
    set_request_materials(_mat_ov)
    rec["materials_used"] = {
        "override_passed": bool(_mat_ov), "override": _mat_ov,
        "fallback": dict(cfg.get("materials") or {}),
        "entry_stores_assignment": bool(e.get("materials")) and e["source"] == "preset",
    }

    cap = _WarnCapture()
    root = logging.getLogger()
    root.addHandler(cap)
    prev_level = root.level
    if prev_level > logging.WARNING or prev_level == 0:
        root.setLevel(logging.WARNING)

    t0 = time.perf_counter()
    try:
        d = em_transient_eval(
            n_steps_per_period=STEPS_PER_PERIOD, n_periods=N_PERIODS,
            gamma_deg=op["gamma_deg"], I_phase_rms=op["I_phase_rms_A"],
            rpm=op["rpm"],
            mesh_size_mm=e["mesh_size_mm"], min_size_mm=MIN_SIZE_MM,
            outer_air_factor=OUTER_AIR, gap_layers=GAP_LAYERS,
            n_sectors=e["n_sectors"], stator_fillet_mm=0.0,
            coil_temp_c=op["coil_temp_c"], end_winding_factor=0.0,
            rotor_eddy=True, demag=_demag_on, eddy=_eddy_on,
            torque_filter=False, geo_override=dict(e["geometry"]),
            structured_gap=True, iron_template=True, geo_mesh=True,
            element_order=ELEMENT_ORDER, drive="current",
        )
        rec["wall_s"] = round(time.perf_counter() - t0, 1)
        rec["ok"] = True
    except BaseException as exc:  # noqa: BLE001 - a trial records every failure
        rec["wall_s"] = round(time.perf_counter() - t0, 1)
        rec["ok"] = False
        rec["error"] = f"{type(exc).__name__}: {exc}"
        rec["traceback"] = traceback.format_exc()[-4000:]
        rec["warnings"] = cap.records[-40:]
        root.removeHandler(cap); root.setLevel(prev_level)
        set_request_materials(None)
        _append(rec)
        return rec
    finally:
        pass
    root.removeHandler(cap)
    root.setLevel(prev_level)
    set_request_materials(None)

    # ── metrics ────────────────────────────────────────────────────────────
    p_cu = _mean(d.get("P_cu_W")); p_fe = _mean(d.get("P_fe_W"))
    p_mag = _mean(d.get("P_mag_eddy_W")); p_shaft = _mean(d.get("P_shaft_eddy_W"))
    p_elec = float(d.get("P_elec_in_W") or 0.0)
    p_mech = float(d.get("P_mech_avg_W") or 0.0)
    t_avg = float(d.get("T_avg_Nm") or 0.0)
    omega = 2.0 * math.pi * float(d.get("rpm") or 0.0) / 60.0
    p_loss = p_cu + p_fe + p_mag + p_shaft
    eff = (p_mech / p_elec) if (p_elec > 0 and p_mech > 0) else (
        (t_avg * omega) / (t_avg * omega + p_loss) if t_avg * omega > 0 else 0.0)

    rec["metrics"] = {
        "T_avg_Nm": round(t_avg, 4),
        "T_avg_maxwell_Nm": d.get("T_avg_maxwell_Nm"),
        "T_ripple_raw_pct": d.get("T_ripple_raw_pct"),
        "T_ripple_filt_pct": d.get("T_ripple_filt_pct"),
        "T_noise_floor_pct": d.get("T_noise_floor_pct"),
        "T_harm_top": sorted(zip(d.get("T_harm_order") or [],
                                 d.get("T_harm_amp") or []),
                             key=lambda kv: -kv[1])[:5],
        "P_cu_W": round(p_cu, 2), "P_cu_dc_W": d.get("P_cu_dc_W"),
        "P_cu_ac_W": d.get("P_cu_ac_W"),
        "P_cu_total_solve_W": d.get("P_cu_total_solve_W"),
        "P_cu_dc_2d_solve_W": d.get("P_cu_dc_2d_solve_W"),
        "P_cu_ac_solve_W": d.get("P_cu_ac_solve_W"),
        "P_cu_ac_prox_W": d.get("P_cu_ac_prox_W"),
        "P_fe_W": round(p_fe, 2), "P_mag_eddy_W": round(p_mag, 3),
        "P_shaft_eddy_W": round(p_shaft, 3),
        "P_mag_solve_W": d.get("P_mag_solve_W"), "P_shaft_solve_W": d.get("P_shaft_solve_W"),
        "P_mag_honest_W": d.get("P_mag_honest_W"), "P_shaft_honest_W": d.get("P_shaft_honest_W"),
        "P_loss_total_W": round(p_loss, 2),
        "P_airgap_W": d.get("P_airgap_W"), "P_mech_avg_W": p_mech,
        "P_elec_in_W": p_elec, "efficiency": round(eff, 5),
        "V_peak_V": d.get("V_peak"), "R_phase_ohm": d.get("R_phase_ohm"),
        "end_winding_factor": d.get("end_winding_factor"),
        "loss_model": d.get("loss_model"), "eddy_coupled": d.get("eddy_coupled"),
        "f_elec_Hz": d.get("f_elec_Hz"), "rpm": d.get("rpm"),
    }
    rec["solver"] = {
        "method": d.get("method"), "element_order": d.get("element_order"),
        "n_steps": d.get("n_steps"),
        "n_steps_per_period": d.get("n_steps_per_period"),
        "n_steps_per_period_requested": d.get("n_steps_per_period_requested"),
        "steps_snapped": d.get("steps_snapped"),
        "slip_nodes_per_period": d.get("slip_nodes_per_period"),
        "n_slip_nodes": d.get("n_slip_nodes"),
        "n_parallel": d.get("n_parallel"),
        "drive": d.get("drive"),
        "v_dc_residual_A": d.get("v_dc_residual_A"),
        "v_drive_diag": d.get("v_drive_diag"),
    }
    rec["gates"] = _gates(e, op, d, n_par_used)
    rec["warnings"] = cap.records[-40:]
    rec["n_warnings"] = len(cap.records)
    # proof that the trial never wrote the user's config
    rec["speed_handling"]["config_yaml_sha256_unchanged"] = (
        hashlib.sha256(_cfg_path.read_bytes()).hexdigest() == _sha_before)
    _append(rec)
    return rec


def _append(rec: Dict[str, Any]) -> None:
    rec["ts_end"] = time.time()
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    with io.open(RESULTS, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")


# ─────────────────────────────────────────────────────────────────────────────
# driver
# ─────────────────────────────────────────────────────────────────────────────
def drive(keys: Optional[List[str]] = None, timeout_s: int = TIMEOUT_S) -> None:
    motors = build_motor_set()
    if keys:
        motors = [m for m in motors if m["key"] in set(keys)]
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    for i, m in enumerate(motors, 1):
        print(f"[{i}/{len(motors)}] {m['key']} — {m['diameter_mm']:.0f} mm "
              f"{m['slots']}s/{m['poles']}p NS={m['n_sectors']} "
              f"mesh={m['mesh_size_mm']} mm", flush=True)
        t0 = time.perf_counter()
        cmd = [sys.executable, str(Path(__file__).resolve()), "--only", m["key"]]
        try:
            pr = subprocess.run(cmd, cwd=str(ROOT), env=env, timeout=timeout_s,
                                capture_output=True, text=True,
                                encoding="utf-8", errors="replace")
            dt = time.perf_counter() - t0
            if pr.returncode != 0:
                print(f"    !! exit {pr.returncode} after {dt:.0f} s", flush=True)
                _append({"key": m["key"], "name": m["name"], "ok": False,
                         "diameter_mm": m["diameter_mm"], "slots": m["slots"],
                         "poles": m["poles"], "wall_s": round(dt, 1),
                         "failure_mode": "subprocess_exit",
                         "returncode": pr.returncode,
                         "stderr_tail": (pr.stderr or "")[-3000:]})
            else:
                print(f"    ok in {dt:.0f} s", flush=True)
        except subprocess.TimeoutExpired:
            dt = time.perf_counter() - t0
            print(f"    !! TIMEOUT after {dt:.0f} s — killed", flush=True)
            _append({"key": m["key"], "name": m["name"], "ok": False,
                     "diameter_mm": m["diameter_mm"], "slots": m["slots"],
                     "poles": m["poles"], "wall_s": round(dt, 1),
                     "failure_mode": "timeout", "timeout_s": timeout_s})


def coil_audit() -> None:
    """Read-only diagnostic for gate (c): MESHED copper section vs the analytic one.

    ``copper_loss_W`` computes the reported DC copper from the NOMINAL wire
    rectangle (num_wires_per_slot x wire_width x wire_height), while the coupled
    eddy solve computes its own active-length I^2R from S_b = sigma * (MESHED
    coil area).  If the CAD clips the wire rectangles against the slot walls the
    two disagree, which is exactly the shape of the gate (c) failures.  No FEM
    is run here — only the 2-D polygons are built.
    """
    from motor_ai_sim.cadquery_geometry import CadQueryMotor
    print(f"{'motor':30s} {'n_poly':>6s} {'n_nom':>6s} {'A_mesh':>9s} "
          f"{'A_nom':>9s} {'A_mesh/A_nom':>12s} {'1/ratio':>8s}")
    for m in build_motor_set():
        geo = m["geometry"]
        try:
            mot = CadQueryMotor()
            mot.set_parameters(dict(geo))
            polys = mot.get_2d_polygons(rotor_angle_deg=0.0)
            coils = polys.get("coils") or []

            def _area(poly) -> float:
                a = getattr(poly, "area", None)      # shapely Polygon
                if a is not None:
                    return float(a)
                pts = [(float(x), float(y)) for x, y in poly]
                s = 0.0
                for i in range(len(pts)):
                    x1, y1 = pts[i]; x2, y2 = pts[(i + 1) % len(pts)]
                    s += x1 * y2 - x2 * y1
                return abs(s) / 2.0

            a_mesh = sum(_area(c) for c in coils)
            n_nom = int(geo.get("num_slots") or m["slots"]) * int(geo.get("num_wires_per_slot") or 0)
            a_nom = n_nom * float(geo.get("wire_width") or 0) * float(geo.get("wire_height") or 0)
            r = (a_mesh / a_nom) if a_nom > 0 else 0.0
            print(f"{m['key']:30s} {len(coils):6d} {n_nom:6d} {a_mesh:9.3f} "
                  f"{a_nom:9.3f} {r:12.4f} {(1 / r if r else 0):8.3f}")
        except Exception as exc:  # noqa: BLE001
            print(f"{m['key']:30s} ERROR {type(exc).__name__}: {exc}")


def report() -> None:
    """Print the results table + the gate/severity summary from the jsonl."""
    if not RESULTS.exists():
        raise SystemExit("no results yet")
    recs = [json.loads(ln) for ln in io.open(RESULTS, encoding="utf-8") if ln.strip()]
    recs.sort(key=lambda r: (float(r.get("diameter_mm") or 0), r.get("key") or ""))

    hdr = ("| motor | size | slots/poles | operating point | T_avg N·m | ripple % | "
           "eta % | gates a/b/c/d/e | wall s |")
    print(hdr)
    print("|---|---|---|---|---|---|---|---|---|")
    for r in recs:
        op = r.get("operating_point") or {}
        m = r.get("metrics") or {}
        g = r.get("gates") or {}
        if not r.get("ok"):
            print(f"| `{r['key']}` | {r.get('diameter_mm')} mm | "
                  f"{r.get('slots')}s/{r.get('poles')}p | — | **{r.get('failure_mode') or 'ERROR'}** "
                  f"| — | — | — | {r.get('wall_s')} |")
            continue

        def _mk(k: str) -> str:
            v = (g.get(k) or {}).get("pass")
            return "—" if v is None else ("PASS" if v else "**FAIL**")
        gates = "/".join(_mk(k) for k in ("a_nonlinear_converged", "b_energy_vs_maxwell",
                                          "c_copper_dc_arithmetic", "d_demag_map",
                                          "e_no_exception"))
        print(f"| `{r['key']}` | {r.get('diameter_mm'):.0f}×{r.get('length_mm'):.0f} mm | "
              f"{r.get('slots')}s/{r.get('poles')}p (NS={r.get('n_sectors')}) | "
              f"{op.get('I_phase_rms_A'):.0f} A, {op.get('rpm'):.0f} rpm, "
              f"γ={op.get('gamma_deg'):+.0f}° | {m.get('T_avg_Nm')} | "
              f"{(m.get('T_ripple_raw_pct') or 0):.2f} | "
              f"{(m.get('efficiency') or 0) * 100:.2f} | {gates} | {r.get('wall_s')} |")

    print("\n### severity feed\n")
    for r in recs:
        if not r.get("ok"):
            print(f"HARD  {r['key']}: {r.get('failure_mode')} {r.get('error', '')}")
    for r in recs:
        g = r.get("gates") or {}
        for k, v in g.items():
            if isinstance(v, dict) and v.get("pass") is False:
                print(f"GATE  {r['key']}: {k} -> "
                      f"{json.dumps({kk: vv for kk, vv in v.items() if kk != 'report_rows'}, ensure_ascii=False)}")
    for r in recs:
        if r.get("ok"):
            print(f"TIME  {r['key']}: {r.get('wall_s')} s "
                  f"({r.get('diameter_mm')} mm, NS={r.get('n_sectors')}, "
                  f"mesh={(r.get('protocol') or {}).get('mesh_size_mm')})")
    for r in recs:
        for w in (r.get("warnings") or []):
            print(f"WARN  {r['key']}: {w}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--list", action="store_true", help="print the plan and exit")
    ap.add_argument("--report", action="store_true", help="summarise the jsonl")
    ap.add_argument("--coil-audit", action="store_true",
                    help="meshed vs nominal copper section (gate-c diagnostic)")
    ap.add_argument("--only", metavar="KEY", help="run one motor in-process")
    ap.add_argument("--keys", nargs="*", help="restrict the campaign to these keys")
    ap.add_argument("--timeout", type=int, default=TIMEOUT_S)
    a = ap.parse_args()

    if a.list:
        for m in build_motor_set():
            op = operating_point(m)
            print(f"{m['key']:26s} {m['diameter_mm']:6.1f} mm  L={m['length_mm']:5.1f}  "
                  f"{m['slots']:2d}s/{m['poles']:2d}p NS={m['n_sectors']}  "
                  f"I={op['I_phase_rms_A']:6.1f} A  {op['rpm']:7.0f} rpm  "
                  f"g={op['gamma_deg']:+5.1f}  J={op['J_coil_A_per_mm2']:5.1f}  "
                  f"mesh={m['mesh_size_mm']:.2f}  conn={op['connection_entry']}  "
                  f"path={op['path']}  dup_of={m.get('geom_signature_dup_of')}  "
                  f"aliases={m.get('aliases', [])}")
        return
    if a.report:
        report()
        return
    if a.coil_audit:
        coil_audit()
        return
    if a.only:
        r = run_one(a.only)
        print(json.dumps({k: r.get(k) for k in ("key", "ok", "wall_s", "error")},
                         ensure_ascii=False))
        return
    drive(a.keys, a.timeout)


if __name__ == "__main__":
    main()
