"""Read-only view of a project motor's STORED simulation points (owner 2026-11-04).

The owner wants the computed points of a project's motors visible to everyone who works on the project, in the
Electromagnetic Simulation view, with nothing to run: only the admin runs for now.  This module is the data half:

* :func:`points_for` - the motor's computed points (duties) as the point switcher and the Sine / PWM chips read them,
  in the same shape as a configuration row of ``GET /api/family/tree`` (so the web reuses ``lib/simPoints``);
* :func:`run_for` - ONE stored run of a point (its summary and its waveforms), exactly what the Simulation charts and
  the summary card draw.

Nothing is solved and nothing is written here; the routes (``development.motors``) apply the project-membership gate and
the ordinary motor-grant gate before anything is read.  What leaves the server is the stored RESULT (numbers, waveforms),
never the construction behind it: the build / assignment signatures, the geometry fingerprints, the geometry blocks,
per-element meshes and the CAD-measured keys are stripped (:func:`scrub`), and a bench-test / validation point
(``kind: test``) is not a point at all.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from motor_ai_sim.duty_kind import is_test_duty

#: Keys that identify or describe the CONSTRUCTION rather than a result (lower-case).  Whole-key matches.
_SCRUB_KEYS = frozenset({
    "build_sig", "assignment_sig", "geo_sig", "geo_fingerprint", "geometry_fingerprint", "geo_fingerprint_live",
    "stale_geometry", "stale_reason", "geometry", "geometry_overrides", "geo_overrides", "geo",
    # per-element meshes / fields: the stored run already drops these on the way in; a hand-edited file must not bring them back
    "frames", "field", "demag_field", "demag_coef_per_tri", "vertices", "triangles",
})


def _secret_key(key: str) -> bool:
    k = key.lower()
    return k in _SCRUB_KEYS or "fingerprint" in k or k.endswith("_sig") or (k.startswith("_") and k.endswith("sig"))


def _cad_keys() -> frozenset:
    try:
        from motor_ai_sim.construction_privacy import CAD_KEYS
        return frozenset(CAD_KEYS)
    except Exception:                                        # noqa: BLE001 - the module is a belt, never a dependency
        return frozenset()


def scrub(value: Any, _cad: Optional[frozenset] = None, _depth: int = 0) -> Any:
    """A copy of a stored block without the construction behind it (see the module docstring).  Pure."""
    cad = _cad if _cad is not None else _cad_keys()
    if _depth > 12:
        return None
    if isinstance(value, dict):
        if "vertices" in value and "triangles" in value:      # a mesh, whatever it is called
            return None
        out: Dict[str, Any] = {}
        for k, v in value.items():
            ks = str(k)
            if _secret_key(ks) or ks in cad:
                continue
            out[ks] = scrub(v, cad, _depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        if value and not isinstance(value[0], (dict, list, tuple)):
            return list(value)                                # a waveform: plain numbers, nothing to scrub
        return [scrub(v, cad, _depth + 1) for v in value]
    return value


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def _run_meta(r: dict, drive: str, prim: str, build_sig: str) -> dict:
    st = r.get("settings") if isinstance(r.get("settings"), dict) else {}
    summ = r.get("summary") if isinstance(r.get("summary"), dict) else {}
    res = r.get("result") if isinstance(r.get("result"), dict) else {}
    return {
        "drive": drive, "primary": drive == prim, "recorded_at": r.get("recorded_at"),
        "stale": bool(r.get("build_sig") and build_sig and r.get("build_sig") != build_sig),
        "ripple_pct": _num(res.get("ripple_pct", summ.get("T_ripple_pct"))),
        "f_switch_hz": _num(st.get("sim.fSwitch")), "steps": _num(st.get("sim.stepsPP")),
        "has_payload": bool(r.get("payload_file")),
    }


def _duty_row(d: dict, family, build_sig: str) -> dict:
    prim = family._primary_drive(d)
    res = d.get("result") if isinstance(d.get("result"), dict) else {}
    runs = []
    for drive in family._RUN_DRIVES:
        r = (d.get("runs") or {}).get(drive)
        if isinstance(r, dict):
            runs.append(_run_meta(r, drive, prim, build_sig))
    stale = bool(res.get("build_sig") and build_sig and res.get("build_sig") != build_sig)
    return {
        "name": d.get("name"), "mode": d.get("mode", "motor"),
        "current_arms": _num(d.get("current_arms")), "rpm": _num(d.get("rpm")),
        "gamma_deg": _num(d.get("gamma_deg")), "torque_nm": _num(d.get("torque_nm")),
        "power_kw": _num(d.get("power_kw")),
        "star_delta": (d.get("star_delta") or (d.get("summary") or {}).get("star_delta")),
        # the two numbers a chip's tooltip quotes; the rest of the stored result is the project view's business
        "result": {"efficiency_pct": _num(res.get("efficiency_pct")), "v_ll_peak_v": _num(res.get("v_ll_peak_v"))},
        "stale": stale or any(x["stale"] for x in runs),
        "primary_drive": prim, "runs": runs,
        "duty_cycle": scrub(d.get("duty_cycle")) if isinstance(d.get("duty_cycle"), dict) else None,
    }


def points_for(open_die: str, config: str, cfg: dict, die_doc: dict, stored: Optional[dict] = None) -> dict:
    """The computed points of one configuration, shaped like a ``TreeCfg`` (``lib/simPoints``).

    ``stored`` is ``duty_results.get(die, config)`` (the coupled records the duty class is read from); a store that cannot
    be read only costs the "short-time never stands in for the rating" refinement of the default point.
    """
    from motor_ai_sim.routes import family
    build_sig = family._build_sig(die_doc, cfg)
    try:
        classes = family.config_classes(cfg, stored if isinstance(stored, dict) else {})
    except Exception:                                        # noqa: BLE001 - a class hiccup never hides the points
        classes = {}
    duties = [d for d in (cfg.get("duties") or [])
              if isinstance(d, dict) and d.get("name") and not is_test_duty(d)]
    w = cfg.get("winding") if isinstance(cfg.get("winding"), dict) else {}
    return {
        "die": open_die, "config": config,
        "phases": 6 if str(w.get("phases") or "3").strip() == "6" else 3,
        "default_duty": family.config_rated_duty(cfg, classes),
        "duties": [_duty_row(d, family, build_sig) for d in duties],
    }


def run_for(open_die: str, config: str, cfg: dict, die_doc: dict, duty: str, drive: str) -> dict:
    """ONE stored run of one point: its summary card and its waveforms.  404 for a point or run that is not stored (and
    for a test point - indistinguishable from a missing one)."""
    from motor_ai_sim.routes import family
    drive = family._run_drive(drive)
    entry = next((x for x in (cfg.get("duties") or []) if isinstance(x, dict) and x.get("name") == duty), None)
    if entry is None or is_test_duty(entry):
        raise HTTPException(404, detail="Point not found")
    r = (entry.get("runs") or {}).get(drive)
    if not isinstance(r, dict):
        raise HTTPException(404, detail="This point has no stored run on that source")
    rel = r.get("payload_file")
    payload = family._read_run_payload(open_die, str(rel)) if rel else None
    build_sig = family._build_sig(die_doc, cfg)
    summary = family._summary_with_full_mass(r.get("summary"), open_die, config)
    return {
        "duty": duty, "drive": drive, "recorded_at": r.get("recorded_at"),
        "stale": bool(r.get("build_sig") and build_sig and r.get("build_sig") != build_sig),
        "summary": scrub(summary) if isinstance(summary, dict) else None,
        # None = the description outlived its waveform file: the card still shows, the charts say so
        "payload": scrub(payload) if isinstance(payload, dict) else None,
    }
