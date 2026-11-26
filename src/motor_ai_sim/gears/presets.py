# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Application acceptance presets (``standard`` and ``humanoid_robot_joint``).

Owner decision (2026-11-25, docs/gear_module/OWNER_DECISIONS.md):
- ``standard`` carries the design note's default screening thresholds
  (S_F >= 1.5, S_H >= 1.1 at duty; static 1.4 / 1.0; 10 000 h).
- ``humanoid_robot_joint`` carries screening thresholds aligned with
  ``docs/gear_module/ASTRA_DESIGN_REVIEW.md`` Q5 (marked PROPOSED because no
  single ISO standard prescribes universal humanoid-joint thresholds).
Every value is overridable per project and named in the evaluation result.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .common import DictResultMixin, ValidityState, worst_state
from .parts import PartsSummaryResult


@dataclass(frozen=True)
class Preset(DictResultMixin):
    """Acceptance criteria and operating assumptions for a gearbox application class."""

    name: str
    description: str
    min_S_F_duty: float
    min_S_H_duty: float
    min_S_F_static: float
    min_S_H_static: float
    min_bearing_S0: float
    K_A: float
    shock_factor: float
    required_life_h: float
    default_lubricant: str
    # (torque_fraction_signed, speed_fraction_signed, time_share) summing to time_share = 1.0
    default_spectrum_shape: Tuple[Tuple[float, float, float], ...]
    max_backlash_arcmin: Optional[float] = None
    require_backdrivable: bool = False
    min_reverse_efficiency: Optional[float] = None
    proposed_fields: List[str] = field(default_factory=list)
    sources: Dict[str, str] = field(default_factory=dict)
    method: str = "ApplicationAcceptancePreset"
    flags: List[str] = field(default_factory=list)


@dataclass
class PresetCheckItem(DictResultMixin):
    """Single acceptance check against a ``Preset``."""

    criterion: str
    actual_value: Optional[float]
    limit_value: Optional[float]
    unit: str
    passed: bool
    state: ValidityState
    is_proposed: bool
    method: str = "PresetThresholdCheck"
    flags: List[str] = field(default_factory=list)


@dataclass
class PresetEvaluationResult(DictResultMixin):
    """Summary of all checks evaluated against a named ``Preset``."""

    preset_name: str
    checks: List[PresetCheckItem]
    all_passed: bool
    state: ValidityState
    applicability_evidence: Dict[str, Any] = field(default_factory=dict)
    missing_inputs: List[str] = field(default_factory=list)
    method: str = "PresetAcceptanceEvaluation"
    flags: List[str] = field(default_factory=list)


BUILTIN_PRESETS: Dict[str, Preset] = {
    "standard": Preset(
        name="standard",
        description="Default industrial metallic gear screening rule from DESIGN.md",
        min_S_F_duty=1.5,            # DESIGN.md §5 table
        min_S_H_duty=1.1,            # DESIGN.md §5 table
        min_S_F_static=1.4,          # DESIGN.md §5 table
        min_S_H_static=1.0,          # DESIGN.md §5 table
        min_bearing_S0=1.0,          # ISO 76 minimal static safety baseline
        K_A=1.0,                     # Smooth uniform drive baseline (ISO 6336-1 cl. 5)
        shock_factor=1.5,            # Moderate peak/start shock factor
        required_life_h=10000.0,     # DESIGN.md §5 / OWNER_DECISIONS.md default
        default_lubricant="ISO_VG_220_mineral",
        default_spectrum_shape=((1.0, 1.0, 1.0),),
        max_backlash_arcmin=None,
        require_backdrivable=False,
        min_reverse_efficiency=None,
        proposed_fields=["K_A", "shock_factor"],
        sources={
            "min_S_F_duty": "DESIGN.md §5 / OWNER_DECISIONS.md (1.5)",
            "min_S_H_duty": "DESIGN.md §5 / OWNER_DECISIONS.md (1.1)",
            "min_S_F_static": "DESIGN.md §5 / OWNER_DECISIONS.md (1.4)",
            "min_S_H_static": "DESIGN.md §5 / OWNER_DECISIONS.md (1.0)",
            "required_life_h": "DESIGN.md §5 / OWNER_DECISIONS.md (10 000 h)",
        },
        flags=["screening_defaults_pending_project_confirmation"],
    ),
    "humanoid_robot_joint": Preset(
        name="humanoid_robot_joint",
        description=(
            "Compact sealed grease-lubricated planetary / compound-planetary joint "
            "with reversing shock duty, low backlash, and back-drivability"
        ),
        # PROPOSED values per ASTRA_DESIGN_REVIEW.md Q5 (reversing robot joint screening)
        # and published actuator/robotics engineering practice (grease-filled planetary joints):
        min_S_F_duty=1.75,           # PROPOSED: ASTRA_DESIGN_REVIEW.md Q5 (reversing joint S_F >= 1.75)
        min_S_H_duty=1.20,           # PROPOSED: ASTRA_DESIGN_REVIEW.md Q5 (S_H >= 1.20 => ~1.44 load margin)
        min_S_F_static=2.0,          # PROPOSED: ASTRA_DESIGN_REVIEW.md Q5 (peak root proof margin >= 2.0)
        min_S_H_static=1.20,         # PROPOSED: ASTRA_DESIGN_REVIEW.md Q5 (peak contact proof >= 1.20)
        min_bearing_S0=1.5,          # PROPOSED: SKF/ISO 76 guidance for shock/oscillating joints
        K_A=1.25,                    # PROPOSED: ISO 6336-1 light-to-moderate shock driven joint
        shock_factor=2.5,            # PROPOSED: humanoid footstrike / emergency-stop peak torque multiplier
        required_life_h=20000.0,     # PROPOSED: ASTRA_DESIGN_REVIEW.md Q5 initial study horizon (20 000 h)
        default_lubricant="ROBOT_GREASE_NLGI1_PAO",  # PROPOSED: low-temp synthetic PAO lithium-complex grease
        # PROPOSED reversing joint duty cycle: forward/reverse swing + loaded stance + peak hold/turnaround
        default_spectrum_shape=(
            (0.35, 0.85, 0.40),
            (-0.35, -0.85, 0.25),
            (0.75, 0.45, 0.20),
            (-0.75, -0.45, 0.10),
            (1.00, 0.15, 0.05),
        ),
        max_backlash_arcmin=8.0,     # PROPOSED: precision planetary robot joint target (<= 8 arcmin)
        require_backdrivable=True,   # PROPOSED: OWNER_DECISIONS.md (back-drivable joint)
        min_reverse_efficiency=0.80, # PROPOSED: back-driving efficiency target for low-ratio planetary joint
        proposed_fields=[
            "min_S_F_duty", "min_S_H_duty", "min_S_F_static", "min_S_H_static",
            "min_bearing_S0", "K_A", "shock_factor", "required_life_h",
            "default_lubricant", "default_spectrum_shape", "max_backlash_arcmin",
            "require_backdrivable", "min_reverse_efficiency",
        ],
        sources={
            "min_S_F_duty": "PROPOSED — ASTRA_DESIGN_REVIEW.md Q5 reversing robot joint screening",
            "min_S_H_duty": "PROPOSED — ASTRA_DESIGN_REVIEW.md Q5 reversing robot joint screening",
            "min_S_F_static": "PROPOSED — ASTRA_DESIGN_REVIEW.md Q5 peak root proof margin",
            "min_S_H_static": "PROPOSED — ASTRA_DESIGN_REVIEW.md Q5 peak contact proof margin",
            "required_life_h": "PROPOSED — ASTRA_DESIGN_REVIEW.md Q5 initial horizon (20 000 h)",
            "min_bearing_S0": "PROPOSED — ISO 76 / SKF oscillating impact joint guidance",
            "K_A": "PROPOSED — ISO 6336-1 moderate shock application factor",
            "shock_factor": "PROPOSED — dynamic impact / E-stop peak-to-nominal multiplier",
            "default_lubricant": "PROPOSED — synthetic PAO NLGI 1 grease for wide-temperature actuators",
            "max_backlash_arcmin": "PROPOSED — low-backlash planetary actuator specification target",
            "min_reverse_efficiency": "PROPOSED — transparent back-drivability target (OWNER_DECISIONS.md)",
        },
        flags=["PROPOSED_values_require_application_qualification"],
    ),
}


def get_preset(name: str) -> Preset:
    """Retrieve a named preset or raise ``KeyError`` listing available presets."""
    if name not in BUILTIN_PRESETS:
        raise KeyError(f"Unknown preset {name!r}; available: {', '.join(sorted(BUILTIN_PRESETS))}")
    return BUILTIN_PRESETS[name]


def evaluate_against_preset(
    preset: Preset,
    *,
    min_S_F_duty: Optional[float] = None,
    min_S_H_duty: Optional[float] = None,
    S_F_duty_state: ValidityState = "valid",
    S_H_duty_state: ValidityState = "valid",
    min_S_F_static: Optional[float] = None,
    min_S_H_static: Optional[float] = None,
    S_F_static_state: ValidityState = "unsupported",
    S_H_static_state: ValidityState = "unsupported",
    gear_life_h: Optional[float] = None,
    gear_life_state: ValidityState = "valid",
    bearing_life_h: Optional[float] = None,
    bearing_life_state: ValidityState = "valid",
    min_bearing_S0: Optional[float] = None,
    backlash_arcmin: Optional[float] = None,
    reverse_efficiency: Optional[float] = None,
    backdrivable: Optional[bool] = None,
    parts_summary: Optional[PartsSummaryResult] = None,
    required_checks_state: ValidityState = "valid",
) -> PresetEvaluationResult:
    """Evaluate computed gearbox metrics against a ``Preset``.

    An ``"unsupported"`` or ``"failed"`` metric never evaluates to ``passed=True``.
    """
    items: List[PresetCheckItem] = []

    def _add_min(
        criterion: str,
        val: Optional[float],
        limit: Optional[float],
        unit: str,
        st: ValidityState,
        field_key: str,
    ) -> None:
        if limit is None:
            return
        prop = field_key in preset.proposed_fields
        if val is None or st in ("unsupported", "failed"):
            items.append(
                PresetCheckItem(
                    criterion=criterion,
                    actual_value=val,
                    limit_value=limit,
                    unit=unit,
                    passed=False,
                    state=("failed" if st == "failed" else "unsupported"),
                    is_proposed=prop,
                    flags=["unsupported_or_missing_metric_cannot_pass"] if val is None or st == "unsupported" else [],
                )
            )
            return
        ok = val >= limit
        items.append(
            PresetCheckItem(
                criterion=criterion,
                actual_value=val,
                limit_value=limit,
                unit=unit,
                passed=ok,
                state=(st if ok else "failed"),
                is_proposed=prop,
            )
        )

    _add_min("S_F_duty", min_S_F_duty, preset.min_S_F_duty, "-", S_F_duty_state, "min_S_F_duty")
    _add_min("S_H_duty", min_S_H_duty, preset.min_S_H_duty, "-", S_H_duty_state, "min_S_H_duty")
    _add_min("S_F_static", min_S_F_static, preset.min_S_F_static, "-", S_F_static_state, "min_S_F_static")
    _add_min("S_H_static", min_S_H_static, preset.min_S_H_static, "-", S_H_static_state, "min_S_H_static")
    _add_min("gear_life_h", gear_life_h, preset.required_life_h, "h", gear_life_state, "required_life_h")
    _add_min("bearing_life_h", bearing_life_h, preset.required_life_h, "h", bearing_life_state, "required_life_h")
    _add_min("bearing_S0", min_bearing_S0, preset.min_bearing_S0, "-", "valid" if min_bearing_S0 is not None else "unsupported", "min_bearing_S0")

    if preset.max_backlash_arcmin is not None:
        prop = "max_backlash_arcmin" in preset.proposed_fields
        if backlash_arcmin is None:
            items.append(PresetCheckItem("backlash_arcmin", None, preset.max_backlash_arcmin, "arcmin", False, "unsupported", prop))
        else:
            ok = backlash_arcmin <= preset.max_backlash_arcmin
            items.append(PresetCheckItem("backlash_arcmin", backlash_arcmin, preset.max_backlash_arcmin, "arcmin", ok, "valid" if ok else "failed", prop))

    if preset.require_backdrivable:
        prop_bd = "require_backdrivable" in preset.proposed_fields
        if backdrivable is False:
            items.append(
                PresetCheckItem(
                    "backdrivable",
                    0.0,
                    1.0,
                    "bool",
                    False,
                    "failed",
                    prop_bd,
                    flags=["non_backdrivable_fails_preset_requirement"],
                )
            )
        elif backdrivable is True:
            items.append(PresetCheckItem("backdrivable", 1.0, 1.0, "bool", True, "valid", prop_bd))
        else:
            items.append(
                PresetCheckItem(
                    "backdrivable",
                    None,
                    1.0,
                    "bool",
                    False,
                    "unsupported",
                    prop_bd,
                    flags=["missing_backdrivability_data"],
                )
            )

        lim = preset.min_reverse_efficiency or 0.5
        prop = "min_reverse_efficiency" in preset.proposed_fields
        if backdrivable is False:
            items.append(
                PresetCheckItem(
                    "reverse_efficiency",
                    reverse_efficiency,
                    lim,
                    "-",
                    False,
                    "failed",
                    prop,
                    flags=["non_backdrivable_fails_preset_requirement"],
                )
            )
        elif reverse_efficiency is None:
            items.append(PresetCheckItem("reverse_efficiency", None, lim, "-", False, "unsupported", prop))
        else:
            ok = reverse_efficiency >= lim
            items.append(PresetCheckItem("reverse_efficiency", reverse_efficiency, lim, "-", ok, "valid" if ok else "failed", prop))

    parts_ok = parts_summary is not None and parts_summary.all_passed and all(
        p.passed and p.state not in ("failed", "unsupported") for p in parts_summary.parts)
    parts_state = parts_summary.state if parts_summary is not None else "unsupported"
    items.append(PresetCheckItem("parts", float(parts_ok), 1., "bool",
        parts_ok and parts_state not in ("failed", "unsupported"), parts_state, False,
        flags=[p.part_name for p in parts_summary.parts if not p.passed or p.state in ("failed", "unsupported")]
              if parts_summary is not None else ["parts_not_evaluated"]))
    items.append(PresetCheckItem("required_checks", None, None, "state",
        required_checks_state not in ("failed", "unsupported"), required_checks_state, False))
    all_ok = all(it.passed for it in items)
    overall = worst_state([it.state for it in items])
    missing = [it.criterion for it in items if it.state == "unsupported"]

    return PresetEvaluationResult(
        preset_name=preset.name,
        checks=items,
        all_passed=all_ok,
        state=overall,
        applicability_evidence={"n_checks": len(items), "proposed_fields": list(preset.proposed_fields)},
        missing_inputs=missing,
        flags=list(preset.flags),
    )
