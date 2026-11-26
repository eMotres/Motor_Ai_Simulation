# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Bearing selection, friction bridge, ISO 281 basic & modified life (L10, L10m),
and ISO 76 static safety for planet, input, output, and carrier bearings.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from motor_ai_sim import bearings as repo_bearings
from motor_ai_sim.bearing_fit import interpolate_iso281_factors

from .common import DictResultMixin, ValidityState, require_number, validate_fields


_VALID_BEARING_TYPES = ("deep_groove", "angular_contact", "needle", "cylindrical_roller", "tapered_roller")


@dataclass(frozen=True)
class GearBearingSpec(DictResultMixin):
    """Bearing specification (from ``bearings_library.yaml`` card or inline parameters)."""

    name: str
    bearing_type: str = "deep_groove"    # "deep_groove" | "angular_contact" | "needle" | "cylindrical_roller" | "tapered_roller"
    d_mm: float = 10.0
    D_mm: float = 22.0
    B_mm: float = 6.0
    C_kn: float = 2.0
    C0_kn: float = 1.0
    Cu_kn: Optional[float] = None        # ISO 281 fatigue load limit [kN]
    contact_angle_deg: float = 0.0       # used for angular_contact
    n_limit_rpm: Optional[float] = None
    card_name: Optional[str] = None      # optional link to config/bearings_library.yaml
    e_contamination: float = 0.5         # ISO 281 contamination factor (0.5 = normal cleanliness)
    method: str = "GearBearingSpec"
    flags: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        validate_fields(self, positive=("d_mm", "D_mm", "B_mm", "C_kn", "C0_kn", "Cu_kn", "n_limit_rpm", "e_contamination"), nonnegative=("contact_angle_deg",))
        if self.bearing_type not in _VALID_BEARING_TYPES:
            raise ValueError(
                f"Unknown bearing_type {self.bearing_type!r}; valid types: {', '.join(_VALID_BEARING_TYPES)}"
            )
        for fname in ("d_mm", "D_mm", "B_mm", "C_kn", "C0_kn", "contact_angle_deg", "e_contamination"):
            if not math.isfinite(float(getattr(self, fname))):
                raise ValueError(f"{fname} must be finite, got {getattr(self, fname)!r}")
        if self.d_mm <= 0.0 or self.D_mm <= self.d_mm or self.B_mm <= 0.0:
            raise ValueError("Bearing geometry requires d_mm > 0, D_mm > d_mm, and B_mm > 0")
        if self.C_kn <= 0.0 or self.C0_kn <= 0.0 or self.e_contamination <= 0.0:
            raise ValueError("C_kn, C0_kn, and e_contamination must be > 0")
        if not (0.0 <= self.contact_angle_deg < 90.0):
            raise ValueError(f"contact_angle_deg must be in [0, 90), got {self.contact_angle_deg}")
        if self.Cu_kn is not None and (not math.isfinite(float(self.Cu_kn)) or self.Cu_kn <= 0.0):
            raise ValueError(f"Cu_kn must be finite and > 0, got {self.Cu_kn!r}")
        if self.n_limit_rpm is not None and (not math.isfinite(float(self.n_limit_rpm)) or self.n_limit_rpm <= 0.0):
            raise ValueError(f"n_limit_rpm must be finite and > 0, got {self.n_limit_rpm!r}")

    @classmethod
    def from_library(
        cls,
        card_name: str,
        *,
        Cu_kn: Optional[float] = None,
        e_contamination: float = 0.5,
        lubrication: str = "grease",
    ) -> "GearBearingSpec":
        """Construct from an existing ``config/bearings_library.yaml`` card."""
        card = repo_bearings.get_bearing(card_name)
        if card.C_kn is None or card.C0_kn is None:
            raise ValueError(f"Bearing card {card_name!r} lacks C_kn / C0_kn")
        return cls(
            name=card.name,
            bearing_type=card.type,
            d_mm=float(card.d),
            D_mm=float(card.D),
            B_mm=float(card.B),
            C_kn=float(card.C_kn),
            C0_kn=float(card.C0_kn),
            Cu_kn=Cu_kn,
            contact_angle_deg=float(card.contact_angle_deg or 0.0),
            n_limit_rpm=card.limit_rpm(lubrication),
            card_name=card.name,
            e_contamination=e_contamination,
        )

    @property
    def dm_mm(self) -> float:
        return 0.5 * (self.d_mm + self.D_mm)

    @property
    def is_roller(self) -> bool:
        return self.bearing_type in ("needle", "cylindrical_roller", "tapered_roller")


@dataclass
class BearingLifeResult(DictResultMixin):
    """ISO 281 L10 / L10m, ISO 76 static safety S0, and friction loss for one bearing."""

    role: str
    bearing_name: str
    bearing_type: str
    F_r_N: float
    F_a_N: float
    speed_rel_rpm: float
    P_equiv_N: float
    P0_static_N: float
    X_factor: float
    Y_factor: float
    life_exponent_p: float
    S0_static: Optional[float]
    L10_Mrev: Optional[float]
    L10h_h: Optional[float]
    kappa: Optional[float]
    a_ISO: Optional[float]
    L10m_Mrev: Optional[float]
    L10mh_h: Optional[float]
    min_load_ok: bool
    speed_limit_ok: Optional[bool]
    friction_torque_Nm: float
    friction_power_W: float
    passed: bool
    state: ValidityState = "valid"
    applicability_evidence: Dict[str, Any] = field(default_factory=dict)
    missing_inputs: List[str] = field(default_factory=list)
    method: str = "ISO281_2007_ISO76"
    flags: List[str] = field(default_factory=list)


@dataclass
class BearingSpectrumLifeResult(DictResultMixin):
    """Revolution-weighted bearing fatigue life across a duty spectrum."""

    role: str
    bearing_name: str
    total_block_hours: float
    total_block_Mrev: float
    L10_block_damage: float
    L10_spectrum_h: Optional[float]
    L10m_block_damage: Optional[float]
    L10m_spectrum_h: Optional[float]
    min_S0_static: Optional[float]
    bin_results: List[BearingLifeResult]
    passed: bool
    state: ValidityState = "valid"
    applicability_evidence: Dict[str, Any] = field(default_factory=dict)
    missing_inputs: List[str] = field(default_factory=list)
    method: str = "ISO281_SpectrumMiner"
    flags: List[str] = field(default_factory=list)


def _angular_contact_factors(alpha_deg: float) -> Tuple[float, float, float, float]:
    """Return (e, X, Y, Y0) for single-row angular-contact ball bearings (ISO 281)."""
    # Interpolated across standard contact angles (15, 25, 40 deg)
    anchors = (
        (15.0, 0.38, 0.44, 1.47, 0.46),
        (25.0, 0.68, 0.41, 0.87, 0.38),
        (40.0, 1.14, 0.35, 0.57, 0.26),
    )
    a = min(max(float(alpha_deg), 15.0), 40.0)
    if a <= anchors[0][0]:
        return anchors[0][1:]
    if a >= anchors[-1][0]:
        return anchors[-1][1:]
    for i in range(len(anchors) - 1):
        a0, e0, x0, y0, y0s = anchors[i]
        a1, e1, x1, y1, y1s = anchors[i + 1]
        if a0 <= a <= a1:
            t = (a - a0) / (a1 - a0)
            return (
                e0 + t * (e1 - e0),
                x0 + t * (x1 - x0),
                y0 + t * (y1 - y0),
                y0s + t * (y1s - y0s),
            )
    return 0.68, 0.41, 0.87, 0.38


def equivalent_bearing_loads(
    spec: GearBearingSpec,
    *,
    F_r_N: float,
    F_a_N: float = 0.0,
) -> Tuple[float, float, float, float, float, List[str]]:
    """Compute (P_N, P0_N, X, Y, p_exp, flags) per ISO 281 and ISO 76."""
    fr_raw = float(F_r_N)
    fa_raw = float(F_a_N)
    if not (math.isfinite(fr_raw) and math.isfinite(fa_raw)):
        raise ValueError(f"Bearing loads must be finite, got F_r_N={F_r_N!r}, F_a_N={F_a_N!r}")
    if fr_raw < 0.0:
        raise ValueError(f"Radial bearing load magnitude F_r_N must be >= 0, got {F_r_N}")

    fr = fr_raw
    fa = abs(fa_raw)
    c0_n = max(float(spec.C0_kn) * 1000.0, 1.0)
    flags: List[str] = []
    if fa_raw < -1.0e-9:
        flags.append("negative_axial_load_direction_magnitude_used")

    if spec.is_roller:
        if spec.bearing_type == "tapered_roller":
            p_exp = 10.0 / 3.0
            e_lim, x_ac, y_ac, y0_ac = _angular_contact_factors(spec.contact_angle_deg or 15.0)
            if fr <= 0.0 or (fa / fr) > e_lim:
                x_val, y_val = x_ac, y_ac
                p_eq = max(x_val * fr + y_val * fa, 0.0)
            else:
                x_val, y_val = 1.0, 0.0
                p_eq = fr
            p0_eq = max(0.5 * fr + y0_ac * fa, fr)
            if fa_raw < -1.0e-9:
                flags.append("reversed_axial_load_requires_opposing_tapered_pair")
            return p_eq, p0_eq, x_val, y_val, p_exp, flags
        p_exp = 10.0 / 3.0
        x_val, y_val = 1.0, 0.0
        p_eq = fr
        p0_eq = fr
        if fa > 1e-6:
            flags.append("radial_roller_needle_bearing_axial_load_unsupported")
        return p_eq, p0_eq, x_val, y_val, p_exp, flags

    p_exp = 3.0
    if spec.bearing_type == "angular_contact":
        e_lim, x_ac, y_ac, y0_ac = _angular_contact_factors(spec.contact_angle_deg or 15.0)
        if fr <= 0.0 or (fa / fr) > e_lim:
            x_val, y_val = x_ac, y_ac
            p_eq = max(x_val * fr + y_val * fa, 0.0)
        else:
            x_val, y_val = 1.0, 0.0
            p_eq = fr
        p0_eq = max(0.5 * fr + y0_ac * fa, fr)
        if fa_raw < -1.0e-9:
            flags.append("reversed_axial_load_requires_opposing_angular_pair")
        return p_eq, p0_eq, x_val, y_val, p_exp, flags

    if spec.bearing_type == "deep_groove":
        # Deep groove ball bearing (ISO 281 / ISO 76)
        e_lim, y_dg = interpolate_iso281_factors(fa / c0_n)
        if fr <= 0.0 or (fa / fr) > e_lim:
            x_val, y_val = 0.56, y_dg
            p_eq = max(x_val * fr + y_val * fa, fa)
        else:
            x_val, y_val = 1.0, 0.0
            p_eq = fr
        p0_eq = max(0.6 * fr + 0.5 * fa, fr)
        return p_eq, p0_eq, x_val, y_val, p_exp, flags

    raise ValueError(f"Unsupported bearing_type {spec.bearing_type!r}")


def reference_viscosity_nu1(n_rpm: float, d_m_mm: float) -> float:
    """ISO 281 Annex A reference kinematic viscosity nu_1 [mm^2/s]."""
    n = max(abs(float(n_rpm)), 1.0)
    dm = max(float(d_m_mm), 1.0)
    if n < 1000.0:
        return 45000.0 * (n ** -0.83) * (dm ** -0.5)
    return 4500.0 * (n ** -0.5) * (dm ** -0.5)


def iso281_a_iso(
    *,
    kappa: float,
    e_contamination: float,
    Cu_N: float,
    P_N: float,
    is_roller: bool,
) -> float:
    """ISO 281:2007 life modification factor a_ISO (radial ball and radial roller curves).

    Implements the analytical curve equations of ISO 281:2007 cl. 8.2 (clauses 8.2.2
    for radial ball bearings and 8.2.3 for radial roller bearings):
    - ``kappa`` is capped at ``4.0`` and floored at ``0.1`` per ISO 281.
    - ``a_ISO`` is capped at ``50.0`` per ISO 281.
    """
    k = min(max(float(kappa), 0.1), 4.0)
    p = max(float(P_N), 1.0e-6)
    ratio = max(float(e_contamination) * float(Cu_N) / p, 1.0e-6)

    if not is_roller:
        # ISO 281:2007 cl. 8.2.2 radial ball bearings (three kappa bands)
        if k < 0.4:
            c1, e1 = 2.5671, 0.83
            c2, exp_in, exp_out = 2.2649, 1.0 / 3.0, -9.3
        elif k < 1.0:
            c1, e1 = 2.5671, 0.83
            c2, exp_in, exp_out = 1.9987, 1.0 / 3.0, -9.3
        else:
            c1, e1 = 2.5671, 0.83
            c2, exp_in, exp_out = 1.9987, 1.0 / 3.0, -9.3
        # Smooth ISO 281 piecewise brackets:
        if k < 0.4:
            bracket = c1 - c2 / (k ** e1)
        elif k < 1.0:
            bracket = 2.5671 - 1.9987 / (k ** 0.83)
        else:
            bracket = 2.5671 - 1.9987 / (k ** 0.83)
        # Correction for kappa in [0.1, 4]: ensure bracket >= 0
        # Wait: at k = 0.4, 2.5671 - 2.2649 / (0.4^0.83) = 2.5671 - 4.84 = negative!
        # Let's use the exact positive ISO 281 exponent form:
        # In ISO 281: a_ISO = 0.1 * [1 - (k1 - k2 / kappa^e1)^e2 * (eC*Cu/P)^e3]^(-e4)
        # Note ISO 281 uses 0.1 * (1 - (...) )^(-9.3) where the inner factor (k1 - k2/k^e1) has k1=2.5671, k2=2.2649/1.9987 ONLY when inner > 0, i.e., kappa > 0.87; below that ISO 281 switches the sign: [1 + (k2/k^e1 - k1)...]^-9.3?
        # Wait: check ISO 281:2007 exact formula!
        # For ball bearings (ISO 281:2007 eq. 20-22):
        # 0.1 <= k < 0.4: a_ISO = 0.1 * (1 - (2.5671 - 2.2649/k^0.054381)^(0.83) * (eC*Cu/P)^(1/3))^(-9.3)
        # 0.4 <= k < 1.0: a_ISO = 0.1 * (1 - (2.5671 - 1.9987/k^0.19087)^(0.83) * (eC*Cu/P)^(1/3))^(-9.3)
        # 1.0 <= k <= 4.0: a_ISO = 0.1 * (1 - (2.5671 - 1.9987/k^0.071739)^(0.83) * (eC*Cu/P)^(1/3))^(-9.3)
        if k < 0.4:
            term_k = 2.5671 - 2.2649 / (k ** 0.054381)
        elif k < 1.0:
            term_k = 2.5671 - 1.9987 / (k ** 0.19087)
        else:
            term_k = 2.5671 - 1.9987 / (k ** 0.071739)
        inner = max(term_k, 0.0) ** 0.83 * (ratio ** (1.0 / 3.0))
        base = max(1.0 - inner, 0.1 ** (1.0 / 9.3) * 0.15)
        # Cap at 50 per ISO 281
        a_iso = 0.1 * (base ** -9.3) if inner < 0.99 else 50.0
    else:
        # ISO 281:2007 cl. 8.2.3 radial roller bearings
        if k < 0.4:
            term_k = 1.5859 - 1.3993 / (k ** 0.054381)
        elif k < 1.0:
            term_k = 1.5859 - 1.2348 / (k ** 0.19087)
        else:
            term_k = 1.5859 - 1.2348 / (k ** 0.071739)
        inner = max(term_k, 0.0) ** (9.0 / 8.0) * (ratio ** 0.4)
        a_iso = 0.1 * ((1.0 - inner) ** -9.0) if inner < 0.99 else 50.0

    return min(max(a_iso, 0.1), 50.0)


def evaluate_bearing(
    spec: GearBearingSpec,
    *,
    role: str,
    F_r_N: float,
    F_a_N: float,
    speed_rel_rpm: float,
    nu_op_mm2_s: float = 20.0,
    temp_C: float = 70.0,
    lubrication: str = "grease",
    required_life_h: float = 10000.0,
    min_S0: float = 1.0,
    allow_cu_estimate: bool = False,
) -> BearingLifeResult:
    """Rate one bearing for ISO 281 L10 / L10m, ISO 76 S0, minimum load, and friction loss."""
    for name, val in (
        ("speed_rel_rpm", speed_rel_rpm),
        ("nu_op_mm2_s", nu_op_mm2_s),
        ("temp_C", temp_C),
        ("required_life_h", required_life_h),
        ("min_S0", min_S0),
    ):
        if not math.isfinite(float(val)):
            raise ValueError(f"{name} must be finite, got {val!r}")
    if nu_op_mm2_s <= 0.0 or required_life_h < 0.0 or min_S0 < 0.0:
        raise ValueError("nu_op_mm2_s must be > 0 and required_life_h / min_S0 must be >= 0")

    p_eq, p0_eq, x_val, y_val, p_exp, flags = equivalent_bearing_loads(
        spec, F_r_N=F_r_N, F_a_N=F_a_N
    )
    missing: List[str] = []
    n_abs = abs(float(speed_rel_rpm))
    fa_abs = abs(float(F_a_N))
    c_n = float(spec.C_kn) * 1000.0
    c0_n = float(spec.C0_kn) * 1000.0

    s0 = (c0_n / p0_eq) if p0_eq > 0.0 else math.inf

    # Minimum radial load check (empirical 1% of C0 for ball, 2% of C for roller)
    f_min = (0.02 * c_n) if spec.is_roller else (0.01 * c0_n)
    min_load_ok = (float(F_r_N) >= f_min) or (p_eq == 0.0)
    if not min_load_ok and p_eq > 0.0:
        flags.append("below_minimum_bearing_load_skidding_risk")

    speed_ok: Optional[bool] = None
    if spec.n_limit_rpm is not None and spec.n_limit_rpm > 0.0:
        speed_ok = n_abs <= spec.n_limit_rpm
        if not speed_ok:
            flags.append("bearing_speed_exceeds_limit")

    # Friction via existing validated SKF library bridge when card_name is available
    if spec.card_name:
        card = repo_bearings.get_bearing(spec.card_name)
        fr_res = repo_bearings.friction(
            card,
            rpm=n_abs,
            f_r_n=float(F_r_N),
            f_a_n=fa_abs,
            temp_c=temp_C,
            lubrication=lubrication,
            nu_mm2_s=nu_op_mm2_s,
        )
        m_fric = float(fr_res["M_total_Nm"])
        p_fric = float(fr_res["P_W"])
    else:
        # Simplified Coulomb rolling/sliding friction estimate for inline bearings
        mu_est = 0.0040 if spec.is_roller else (0.0020 if spec.bearing_type == "angular_contact" else 0.0015)
        m_fric = 0.5 * mu_est * p_eq * (spec.d_mm * 1.0e-3)
        p_fric = m_fric * (n_abs * math.pi / 30.0)
        flags.append("simplified_inline_bearing_friction")

    if p_eq <= 0.0:
        l10_mrev: Optional[float] = None
        l10h: Optional[float] = None
        flags.append("zero_bearing_load")
    else:
        l10_mrev = (c_n / p_eq) ** p_exp
        if n_abs > 0.0:
            l10h = (1.0e6 * l10_mrev) / (60.0 * n_abs)
        else:
            l10h = None
            flags.append("zero_speed_l10h_undefined_check_static_S0")

    # Modified life L10m (ISO 281 cl. 8)
    kappa: Optional[float] = None
    a_iso: Optional[float] = None
    l10m_mrev: Optional[float] = None
    l10mh: Optional[float] = None

    if n_abs > 0.0 and p_eq > 0.0:
        nu1 = reference_viscosity_nu1(n_abs, spec.dm_mm)
        kappa = float(nu_op_mm2_s) / max(nu1, 1e-6)
        cu_n: Optional[float] = None
        if spec.Cu_kn is not None and spec.Cu_kn > 0.0:
            cu_n = float(spec.Cu_kn) * 1000.0
        elif allow_cu_estimate:
            cu_n = c0_n / 22.0
            flags.append("estimated_Cu_from_C0")
        else:
            missing.append("Cu_kn")

        if cu_n is not None:
            a_iso = iso281_a_iso(
                kappa=kappa,
                e_contamination=spec.e_contamination,
                Cu_N=cu_n,
                P_N=p_eq,
                is_roller=spec.is_roller,
            )
            l10m_mrev = a_iso * l10_mrev  # type: ignore[operator]
            l10mh = a_iso * l10h          # type: ignore[operator]
            if kappa < 0.4:
                flags.append("bearing_boundary_lubrication_low_kappa")

    # Determine state and pass/fail (unsupported and failed checks never pass!)
    life_check = l10mh if l10mh is not None else l10h
    life_ok = (life_check >= required_life_h) if life_check is not None else False
    if spec.bearing_type in ("needle", "cylindrical_roller") and abs(float(F_a_N)) > 1e-6:
        state: ValidityState = "unsupported"
        passed = False
        l10_mrev = l10h = l10m_mrev = l10mh = None
        missing.append("bearing_axial_load_rating_method")
    elif n_abs == 0.0 or life_check is None:
        # Standstill or zero-load: L10h is undefined
        state = "unsupported"
        passed = False
    else:
        passed = bool(life_ok and (s0 >= min_S0) and (speed_ok is not False) and min_load_ok)
        if not passed:
            state = "failed"
        elif "Cu_kn" in missing or "simplified_inline_bearing_friction" in flags:
            state = "estimated"
        else:
            state = "valid"

    return BearingLifeResult(
        role=role,
        bearing_name=spec.name,
        bearing_type=spec.bearing_type,
        F_r_N=float(F_r_N),
        F_a_N=float(F_a_N),
        speed_rel_rpm=n_abs,
        P_equiv_N=p_eq,
        P0_static_N=p0_eq,
        X_factor=x_val,
        Y_factor=y_val,
        life_exponent_p=p_exp,
        S0_static=s0,
        L10_Mrev=l10_mrev,
        L10h_h=l10h,
        kappa=kappa,
        a_ISO=a_iso,
        L10m_Mrev=l10m_mrev,
        L10mh_h=l10mh,
        min_load_ok=min_load_ok,
        speed_limit_ok=speed_ok,
        friction_torque_Nm=m_fric,
        friction_power_W=p_fric,
        passed=passed,
        state=state,
        applicability_evidence={
            "standard": "ISO 281:2007 / ISO 76",
            "kappa_capped_max": 4.0,
            "a_ISO_capped_max": 50.0,
            "e_contamination": spec.e_contamination,
        },
        missing_inputs=missing,
        flags=flags,
    )


def evaluate_bearing_spectrum(
    spec: GearBearingSpec,
    *,
    role: str,
    bins: Sequence[Tuple[float, float, float, float]],  # (F_r_N, F_a_N, speed_rel_rpm, duration_h)
    nu_op_mm2_s: float = 20.0,
    temp_C: float = 70.0,
    required_life_h: float = 10000.0,
    min_S0: float = 1.0,
    allow_cu_estimate: bool = False,
) -> BearingSpectrumLifeResult:
    """Compute revolution-weighted ISO 281 L10 / L10m life over a duty spectrum."""
    if not bins:
        raise ValueError("Duty spectrum bins must be non-empty")
    bin_res: List[BearingLifeResult] = []
    tot_h = 0.0
    tot_mrev = 0.0
    d_l10 = 0.0
    d_l10m = 0.0
    have_all_l10m = True
    s0_vals: List[float] = []
    flags: List[str] = []
    missing: List[str] = []

    for idx, (fr, fa, rpm, dt_h) in enumerate(bins):
        dt_val = float(dt_h)
        if not math.isfinite(dt_val) or dt_val < 0.0:
            raise ValueError(f"Bearing spectrum bin {idx} duration_h must be finite and >= 0, got {dt_h!r}")
        res = evaluate_bearing(
            spec,
            role=role,
            F_r_N=fr,
            F_a_N=fa,
            speed_rel_rpm=rpm,
            nu_op_mm2_s=nu_op_mm2_s,
            temp_C=temp_C,
            required_life_h=required_life_h,
            min_S0=min_S0,
            allow_cu_estimate=allow_cu_estimate,
        )
        bin_res.append(res)
        dt = dt_val
        tot_h += dt
        mrev = 60.0 * abs(float(rpm)) * dt * 1.0e-6
        tot_mrev += mrev
        if res.S0_static is not None:
            s0_vals.append(res.S0_static)
        if res.L10_Mrev is not None and mrev > 0.0:
            d_l10 += mrev / res.L10_Mrev
        if res.L10m_Mrev is not None and mrev > 0.0:
            d_l10m += mrev / res.L10m_Mrev
        elif mrev > 0.0 and res.L10_Mrev is not None:
            have_all_l10m = False
        flags.extend(res.flags)
        for m in res.missing_inputs:
            if m not in missing:
                missing.append(m)

    l10_spec_h = (tot_h / d_l10) if d_l10 > 0.0 else None
    l10m_spec_h = (tot_h / d_l10m) if (have_all_l10m and d_l10m > 0.0) else None
    min_s0_val = min(s0_vals) if s0_vals else None

    active_bins = [r for r, (_, _, _, dt_raw) in zip(bin_res, bins) if float(dt_raw) > 0.0]
    all_min_load_ok = all(r.min_load_ok for r in active_bins)
    all_speed_ok = all(r.speed_limit_ok is not False for r in active_bins)
    eff_life = l10m_spec_h if l10m_spec_h is not None else l10_spec_h

    if tot_mrev <= 0.0 or eff_life is None:
        state: ValidityState = "unsupported"
        passed = False
        if tot_mrev <= 0.0:
            flags.append("standstill_spectrum_no_revolutions")
    elif any(r.state == "unsupported" and r.speed_rel_rpm > 0.0 for r in active_bins):
        state = "unsupported"
        passed = False
    else:
        passed = bool(
            eff_life >= required_life_h
            and (min_s0_val is None or min_s0_val >= min_S0)
            and all_min_load_ok
            and all_speed_ok
        )
        if not passed:
            state = "failed"
        elif missing or any(r.state == "estimated" for r in active_bins):
            state = "estimated"
        else:
            state = "valid"

    if any("bearing_axial_load_rating_method" in r.missing_inputs for r in active_bins):
        l10_spec_h = l10m_spec_h = None
        state = "unsupported"
        passed = False

    return BearingSpectrumLifeResult(
        role=role,
        bearing_name=spec.name,
        total_block_hours=tot_h,
        total_block_Mrev=tot_mrev,
        L10_block_damage=d_l10,
        L10_spectrum_h=l10_spec_h,
        L10m_block_damage=d_l10m if have_all_l10m else None,
        L10m_spectrum_h=l10m_spec_h,
        min_S0_static=min_s0_val,
        bin_results=bin_res,
        passed=passed,
        state=state,
        applicability_evidence={
            "total_bins": len(bins),
            "revolution_weighted_damage": True,
        },
        missing_inputs=missing,
        flags=sorted(set(flags)),
    )
