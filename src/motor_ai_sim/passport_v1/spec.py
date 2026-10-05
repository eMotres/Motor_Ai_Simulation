"""The machine spec a passport run is driven by (one YAML file per die).

The pilot scripts (``scripts/passport_pilot.py``, ``scripts/passport_full.py``,
``scripts/passport_card.py``) are machine-agnostic: everything that names a
die, a configuration, an owner input or a controller lives in a spec file
under ``config/passport_specs/``.  The Ø40 pilot (``CIANO14_40_new.yaml``) was
the first; a new machine is a new spec, never a fork of the scripts.

Schema (keys not listed are rejected, so a typo fails closed)::

    die: "CIANO28 85 20SW1200"
    inputs_subdir: die85             # /work/inputs/<this>/{die.yaml,<config>.yaml}
    origin: "<where the inputs were copied from>"
    workspace_user: vadim@motresres.com   # panel settings owner (thermal / mechanical)
    report_stage1: PASSPORT_D85_STAGE1_2026-10-05.md
    title: "Ø85"                     # short family label for the HTML cards
    plan:                            # optional stage parameters
      loss_speed_factors: [0.25, 0.5, 1.0, 1.5, 2.0]   # × rated speed (+ n_max)
      envelope_max_factor: 4.0
    machines:
      L13:
        config: L13
        rated_duty: rated
        peak_duty: peak              # or null + peak_rule
        version: "12S"
        owner_bus_V: [36.0, 44.4, 50.4]
        owner_rated: {rpm: 1000.0, I_arms: 25.88}
        owner_peak: {rpm: 1000.0, I_arms: 45.962}
        peak_rule: {ratio: ..., source: "..."}         # only without a peak duty
        hot_override: {magnet_c: 150, coil_c: 180, source: "...", reason: "..."}
        controller: {...}            # see passport_full / stage3; null = none set
        pwm_variants: [...]          # [] = no controller set
        stage_a_lengths_mm: [13.0]
        mech: {critical_speeds: false, beam: {...}}
        cooling_studies: [...]       # coupled continuous / limits runs
        k3d: {k_T: stage_b_inherited | k_psi}           # how the card's k_T is taken
        propellers: [ids]            # propellers judged against the motor (propeller_air)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Mapping

_TOP = {"die", "inputs_subdir", "origin", "workspace_user", "report_stage1", "title",
        "plan", "machines", "notes"}
_MACHINE = {"config", "rated_duty", "peak_duty", "version", "owner_bus_V", "owner_rated",
            "owner_peak", "peak_rule", "hot_override", "controller", "pwm_variants",
            "not_computed_variants", "stage_a_lengths_mm", "mech", "cooling_studies",
            "audit", "k3d", "propellers", "notes"}
_PLAN = {"loss_speed_factors", "envelope_max_factor", "refine_points", "loss_steps_per_period", "m_margin",
         "loss_floor_levels"}


class SpecError(ValueError):
    """The spec file is missing a required key or names an unknown one."""


def load_spec(path: Path) -> Dict[str, Any]:
    import yaml
    p = Path(path)
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise SpecError(f"{p}: not a mapping")
    bad = set(raw) - _TOP
    if bad:
        raise SpecError(f"{p}: unknown top-level keys {sorted(bad)}")
    for k in ("die", "inputs_subdir", "machines"):
        if not raw.get(k):
            raise SpecError(f"{p}: required key {k!r} missing")
    plan = dict(raw.get("plan") or {})
    if set(plan) - _PLAN:
        raise SpecError(f"{p}: unknown plan keys {sorted(set(plan) - _PLAN)}")
    for tag, m in raw["machines"].items():
        if not isinstance(m, Mapping):
            raise SpecError(f"{p}: machine {tag!r} is not a mapping")
        bad = set(m) - _MACHINE
        if bad:
            raise SpecError(f"{p}: machine {tag!r}: unknown keys {sorted(bad)}")
        for k in ("config", "rated_duty", "version", "owner_bus_V", "owner_rated"):
            if m.get(k) in (None, ""):
                raise SpecError(f"{p}: machine {tag!r}: required key {k!r} missing")
        if not m.get("peak_duty") and not m.get("peak_rule"):
            raise SpecError(f"{p}: machine {tag!r}: a peak duty or a peak rule is required")
        ho = m.get("hot_override")
        if ho is not None:
            for k in ("magnet_c", "coil_c", "source", "reason"):
                if ho.get(k) in (None, ""):
                    raise SpecError(f"{p}: machine {tag!r}: hot_override.{k} missing")
    raw["plan"] = plan
    raw["_path"] = str(p)
    return raw


def default_spec_path(repo: Path, name: str) -> Path:
    """``config/passport_specs/<name>.yaml`` (``name`` may already be a path)."""
    q = Path(name)
    if q.suffix in (".yaml", ".yml") and q.exists():
        return q
    return Path(repo) / "config" / "passport_specs" / f"{name}.yaml"
