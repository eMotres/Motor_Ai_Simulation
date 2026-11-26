# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Gear geometry and mesh interference checks per ISO 21771:2007.

Sign convention (ISO 21771 cl. 3.2 - 3.3):
- ``GearSpec.z`` is always entered as a positive integer (> 0), with
  ``is_internal: bool`` identifying internal ring gears. Internally, signed tooth
  count ``z_signed = -z`` is used for internal gears.
- Profile shift ``x`` follows ISO 21771: for BOTH external and internal gears,
  positive ``x`` increases the circular tooth thickness on the reference
  cylinder. Consequently, on an internal gear, positive ``x`` shifts the teeth
  radially inward (reducing tip and root diameters), whereas negative ``x``
  expands the internal gear outward. Setting ``x1 + x2 = 0`` for an internal
  pair yields zero centre-distance modification (``a_w = a_d``).
- ``tooth_thickness_allowance_mm >= 0`` is normal tooth thinning on the reference
  cylinder (for backlash); actual normal tooth thickness is reduced by this amount.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .common import DictResultMixin, ValidityState, inv, inv_solve, require_number


@dataclass(frozen=True)
class GearSpec(DictResultMixin):
    """Parametric definition of one spur or helical cylindrical gear."""

    z: int
    m_n_mm: float
    b_mm: float
    x: float = 0.0
    alpha_n_deg: float = 20.0
    beta_deg: float = 0.0
    hand: str = "right"                   # "right" | "left" | "none"
    is_internal: bool = False
    ha_star: float = 1.0
    hf_star: float = 1.25
    c_star: float = 0.25
    rho_fp_star: float = 0.38
    tooth_thickness_allowance_mm: float = 0.0
    tip_relief_um: float = 0.0
    root_relief_um: float = 0.0
    material_name: str = "18CrNiMo7-6_case_carburised_MQ"
    accuracy_grade: int = 6               # ISO 1328-1 grade (4..12)
    Ra_flank_um: float = 0.8
    Rz_flank_um: float = 4.8
    Ra_root_um: float = 1.6
    Rz_root_um: float = 8.0
    rim_thickness_mm: Optional[float] = None   # s_R [mm] from root circle (for Y_B / ring rim)
    bore_diameter_mm: Optional[float] = None   # inner bore (external) or outer housing OD (internal)

    method: str = "GearSpec_ISO21771"
    flags: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if isinstance(self.z, bool) or not isinstance(self.z, int) or self.z <= 0:
            raise ValueError(f"z must be a positive integer, got {self.z!r}")
        for fname in (
            "m_n_mm", "b_mm", "x", "alpha_n_deg", "beta_deg",
            "ha_star", "hf_star", "c_star", "rho_fp_star",
            "tooth_thickness_allowance_mm", "tip_relief_um", "root_relief_um",
            "Ra_flank_um", "Rz_flank_um", "Ra_root_um", "Rz_root_um",
        ):
            require_number(fname, getattr(self, fname))
            if not math.isfinite(float(getattr(self, fname))):
                raise ValueError(f"{fname} must be finite, got {getattr(self, fname)!r}")
        if self.m_n_mm <= 0.0:
            raise ValueError(f"m_n_mm must be > 0, got {self.m_n_mm}")
        if self.b_mm <= 0.0:
            raise ValueError(f"b_mm must be > 0, got {self.b_mm}")
        if self.ha_star <= 0.0 or self.hf_star <= 0.0 or self.c_star < 0.0 or self.rho_fp_star < 0.0:
            raise ValueError("Tooth proportion factors ha_star/hf_star must be > 0 and c_star/rho_fp_star >= 0")
        if self.Ra_flank_um <= 0.0 or self.Rz_flank_um <= 0.0 or self.Ra_root_um <= 0.0 or self.Rz_root_um <= 0.0:
            raise ValueError("Roughness parameters Ra/Rz must be > 0")
        if self.tip_relief_um < 0.0 or self.root_relief_um < 0.0:
            raise ValueError("Relief values must be >= 0")
        require_number("accuracy_grade", self.accuracy_grade, positive=True, integer=True)
        if not isinstance(self.is_internal, bool):
            raise ValueError("is_internal must be boolean")
        if not (1 <= int(self.accuracy_grade) <= 12):
            raise ValueError(f"accuracy_grade must be in [1, 12], got {self.accuracy_grade}")
        if self.rim_thickness_mm is not None and (not math.isfinite(float(self.rim_thickness_mm)) or self.rim_thickness_mm <= 0.0):
            raise ValueError(f"rim_thickness_mm must be finite and > 0, got {self.rim_thickness_mm!r}")
        if self.bore_diameter_mm is not None and (not math.isfinite(float(self.bore_diameter_mm)) or self.bore_diameter_mm <= 0.0):
            raise ValueError(f"bore_diameter_mm must be finite and > 0, got {self.bore_diameter_mm!r}")
        if not (5.0 <= self.alpha_n_deg <= 35.0):
            raise ValueError(f"alpha_n_deg out of plausible range [5, 35], got {self.alpha_n_deg}")
        if not (-45.0 <= self.beta_deg <= 45.0):
            raise ValueError(f"beta_deg out of plausible range [-45, 45], got {self.beta_deg}")
        if self.hand not in ("right", "left", "none"):
            raise ValueError(f"hand must be 'right', 'left', or 'none', got {self.hand!r}")
        if self.tooth_thickness_allowance_mm < 0.0:
            raise ValueError("tooth_thickness_allowance_mm must be >= 0 (tooth thinning)")

    @property
    def z_signed(self) -> int:
        return -self.z if self.is_internal else self.z

    @property
    def effective_hand(self) -> str:
        if abs(self.beta_deg) < 1e-9:
            return "none"
        if self.hand == "none":
            return "right" if self.beta_deg > 0 else "left"
        return self.hand

    def with_x(self, new_x: float) -> "GearSpec":
        """Return a copy of this gear with modified profile shift ``new_x``."""
        return GearSpec(
            z=self.z, m_n_mm=self.m_n_mm, b_mm=self.b_mm, x=float(new_x),
            alpha_n_deg=self.alpha_n_deg, beta_deg=self.beta_deg, hand=self.hand,
            is_internal=self.is_internal, ha_star=self.ha_star, hf_star=self.hf_star,
            c_star=self.c_star, rho_fp_star=self.rho_fp_star,
            tooth_thickness_allowance_mm=self.tooth_thickness_allowance_mm,
            tip_relief_um=self.tip_relief_um, root_relief_um=self.root_relief_um,
            material_name=self.material_name, accuracy_grade=self.accuracy_grade,
            Ra_flank_um=self.Ra_flank_um, Rz_flank_um=self.Rz_flank_um,
            Ra_root_um=self.Ra_root_um, Rz_root_um=self.Rz_root_um,
            rim_thickness_mm=self.rim_thickness_mm, bore_diameter_mm=self.bore_diameter_mm,
        )


@dataclass
class GearDimensions(DictResultMixin):
    """Reference and tip/root dimensions of a single gear (ISO 21771 cl. 4)."""

    z: int
    z_signed: int
    is_internal: bool
    m_n_mm: float
    m_t_mm: float
    b_mm: float
    alpha_n_deg: float
    alpha_t_deg: float
    beta_deg: float
    beta_b_deg: float
    x: float
    d_mm: float
    d_b_mm: float
    d_a_mm: float
    d_f_mm: float
    s_n_mm: float                    # normal circular tooth thickness at d
    s_t_mm: float                    # transverse circular tooth thickness at d
    s_an_mm: float                   # normal tooth thickness at tip d_a
    z_min_undercut: float            # ISO 21771 undercut tooth limit
    x_min_undercut: float            # minimum x to avoid undercut
    has_undercut: bool
    pointed_tip: bool
    tip_shortening_k_mm: float       # tip shortening applied (>= 0 means tip radius reduced)
    method: str = "ISO21771_cl4"
    flags: List[str] = field(default_factory=list)


@dataclass
class PairGeometryResult(DictResultMixin):
    """Complete ISO 21771 geometry, contact ratios, and interference checks for a gear pair."""

    gear1: GearDimensions
    gear2: GearDimensions
    is_internal_mesh: bool
    u_ratio: float                   # |z2| / z1
    alpha_wt_deg: float              # working transverse pressure angle [deg]
    a_d_mm: float                    # reference centre distance |a_d| [mm]
    a_w_mm: float                    # working centre distance |a_w| [mm]
    d_w1_mm: float                   # working pitch diameter of gear 1 [mm]
    d_w2_mm: float                   # working pitch diameter of gear 2 [mm]
    epsilon_alpha: float             # transverse contact ratio
    epsilon_beta: float              # overlap contact ratio
    epsilon_gamma: float             # total contact ratio
    epsilon_1: float                 # approach / pinion addendum contact ratio
    epsilon_2: float                 # recess / gear 2 addendum contact ratio
    path_of_contact_mm: float        # g_alpha [mm]
    base_pitch_t_mm: float           # p_bt [mm]
    d_Nf1_mm: float                  # start of active profile diameter, gear 1 [mm]
    d_Nf2_mm: float                  # start of active profile diameter, gear 2 [mm]
    circumferential_backlash_mm: float   # j_t [mm] on working pitch circle
    normal_backlash_mm: float        # j_n [mm]
    angular_backlash_deg: float      # j_theta [deg] at gear 2 (when gear 1 fixed)
    involute_interference: bool
    tip_interference: bool
    internal_tip_margin_deg: Optional[float]   # ISO 21771 Annex A.2 angular margin (>= 0 ok)
    radial_assembly_margin_mm: Optional[float] # internal mesh radial clearance hint
    bottom_clearance_12_mm: float    # tip1 to root2 radial clearance [mm]
    bottom_clearance_21_mm: float    # tip2 to root1 radial clearance [mm]
    valid_mesh: bool
    state: ValidityState = "valid"
    applicability_evidence: Dict[str, Any] = field(default_factory=dict)
    missing_inputs: List[str] = field(default_factory=list)
    method: str = "ISO21771:2007"
    flags: List[str] = field(default_factory=list)


def compute_gear_dimensions(
    gear: GearSpec,
    *,
    tip_shortening_k: float = 0.0,
) -> GearDimensions:
    """Compute ISO 21771 cl. 4 single-gear reference, base, tip, and root diameters.

    Parameters
    ----------
    tip_shortening_k : float
        Dimensionless tip-shortening coefficient (``>= 0`` reduces tip tooth height
        by ``tip_shortening_k * m_n_mm`` to preserve bottom clearance ``c* * m_n``).
    """
    an = math.radians(gear.alpha_n_deg)
    beta = math.radians(abs(gear.beta_deg))
    cos_b = math.cos(beta)
    # ISO 21771 cl. 4.1: transverse module, pressure angle, base helix angle
    m_t = gear.m_n_mm / cos_b
    alpha_t = math.atan(math.tan(an) / cos_b)
    sin_bb = math.sin(beta) * math.cos(an)
    beta_b = math.asin(min(max(sin_bb, -1.0), 1.0))

    d = gear.z * m_t
    d_b = d * math.cos(alpha_t)

    k_use = max(float(tip_shortening_k), 0.0)
    if not gear.is_internal:
        # External gear (ISO 21771 cl. 4.2)
        d_a = d + 2.0 * gear.m_n_mm * (gear.ha_star + gear.x - k_use)
        d_f = d - 2.0 * gear.m_n_mm * (gear.hf_star - gear.x)
    else:
        # Internal gear (ISO 21771 cl. 4.2, positive x moves teeth inward)
        d_a = d - 2.0 * gear.m_n_mm * (gear.ha_star + gear.x - k_use)
        d_f = d + 2.0 * gear.m_n_mm * (gear.hf_star - gear.x)

    # Reference tooth thickness (ISO 21771 cl. 4.2.4)
    s_n_nom = gear.m_n_mm * (0.5 * math.pi + 2.0 * gear.x * math.tan(an))
    s_n = s_n_nom - gear.tooth_thickness_allowance_mm
    s_t = s_n / cos_b

    # Tip circular thickness s_an
    flags: List[str] = []
    if d_a > d_b:
        alpha_at = math.acos(min(1.0, d_b / d_a))
        if not gear.is_internal:
            s_at = d_a * (s_t / d + inv(alpha_t) - inv(alpha_at))
        else:
            # Internal tooth narrows toward tip (r_a < r)
            s_at = d_a * (s_t / d + inv(alpha_at) - inv(alpha_t))
        beta_a = math.atan(math.tan(beta) * (d_a / d))
        s_an = s_at * math.cos(beta_a)
    else:
        s_an = 0.0
        flags.append("tip_diameter_below_base_circle")

    # Undercut check (ISO 21771 cl. 5.3, virtual / helical rack limit)
    sin2_at = math.sin(alpha_t) ** 2
    if not gear.is_internal:
        z_min = 2.0 * gear.ha_star * cos_b / sin2_at
        x_min = gear.ha_star - (gear.z * sin2_at) / (2.0 * cos_b)
        has_undercut = gear.x < (x_min - 1e-9)
        if has_undercut:
            flags.append("undercut")
    else:
        z_min = 0.0
        x_min = -math.inf
        has_undercut = False

    pointed_tip = s_an <= 0.0
    if pointed_tip:
        flags.append("pointed_tip")
    elif s_an < 0.25 * gear.m_n_mm:
        flags.append("thin_tip_below_0.25mn")

    return GearDimensions(
        z=gear.z,
        z_signed=gear.z_signed,
        is_internal=gear.is_internal,
        m_n_mm=gear.m_n_mm,
        m_t_mm=m_t,
        b_mm=gear.b_mm,
        alpha_n_deg=gear.alpha_n_deg,
        alpha_t_deg=math.degrees(alpha_t),
        beta_deg=gear.beta_deg,
        beta_b_deg=math.degrees(beta_b),
        x=gear.x,
        d_mm=d,
        d_b_mm=d_b,
        d_a_mm=d_a,
        d_f_mm=d_f,
        s_n_mm=s_n,
        s_t_mm=s_t,
        s_an_mm=s_an,
        z_min_undercut=z_min,
        x_min_undercut=x_min,
        has_undercut=has_undercut,
        pointed_tip=pointed_tip,
        tip_shortening_k_mm=k_use * gear.m_n_mm,
        flags=flags,
    )


def analyze_pair(
    gear1: GearSpec,
    gear2: GearSpec,
    *,
    centre_distance_mm: Optional[float] = None,
    solve_shift_mode: str = "auto",
    shorten_tips: bool = True,
) -> PairGeometryResult:
    """Compute ISO 21771 cylindrical gear pair geometry, contact ratio, and interference checks.

    Parameters
    ----------
    gear1 : GearSpec
        Pinion (always external).
    gear2 : GearSpec
        Wheel (external or internal ring). For an internal pair, ``gear2.z``
        must be strictly larger than ``gear1.z``.
    centre_distance_mm : float, optional
        If provided, solves the required profile-shift sum ``x1 + x2`` for this
        working centre distance and distributes it according to ``solve_shift_mode``
        (``"auto"``, ``"equal"``, ``"gear1"``, or ``"gear2"``).
    shorten_tips : bool
        If True (ISO 21771 cl. 4.3), applies tip shortening ``k`` when
        ``x1 + x2`` exceeds centre-distance modification ``y`` so bottom clearance
        remains at least ``c* * m_n``.
    """
    if gear1.is_internal:
        raise ValueError("gear1 (pinion) must be an external gear; put internal ring in gear2")
    if abs(gear1.m_n_mm - gear2.m_n_mm) > 1e-9:
        raise ValueError(f"Mating gears must share normal module m_n_mm ({gear1.m_n_mm} vs {gear2.m_n_mm})")
    if abs(gear1.alpha_n_deg - gear2.alpha_n_deg) > 1e-9:
        raise ValueError("Mating gears must share normal pressure angle alpha_n_deg")
    if abs(abs(gear1.beta_deg) - abs(gear2.beta_deg)) > 1e-9:
        raise ValueError("Mating gears must share helix angle magnitude |beta_deg|")
    if gear2.is_internal and gear2.z <= gear1.z:
        raise ValueError(f"Internal gear z2 ({gear2.z}) must exceed pinion z1 ({gear1.z})")

    m_n = gear1.m_n_mm
    an = math.radians(gear1.alpha_n_deg)
    beta = math.radians(abs(gear1.beta_deg))
    cos_b = math.cos(beta)
    m_t = m_n / cos_b
    alpha_t = math.atan(math.tan(an) / cos_b)

    z1_s = gear1.z_signed
    z2_s = gear2.z_signed
    z_sum = z1_s + z2_s
    if z_sum == 0:
        raise ValueError("Signed tooth sum z1 + z2 cannot be zero")

    a_d_signed = 0.5 * m_t * z_sum
    a_d = abs(a_d_signed)

    flags: List[str] = []

    hand_ok = True
    if abs(gear1.beta_deg) > 1e-9:
        h1 = gear1.effective_hand
        h2 = gear2.effective_hand
        if not gear2.is_internal and h1 == h2:
            hand_ok = False
            flags.append("incompatible_helical_hands_external_require_opposite")
        elif gear2.is_internal and h1 != h2:
            hand_ok = False
            flags.append("incompatible_helical_hands_internal_require_same")

    if centre_distance_mm is not None:
        a_w = float(centre_distance_mm)
        if a_w <= 0.0:
            raise ValueError(f"centre_distance_mm must be > 0, got {a_w}")
        cos_awt = (a_d / a_w) * math.cos(alpha_t)
        if cos_awt >= 1.0 - 1e-12:
            if cos_awt > 1.0 + 1e-9:
                raise ValueError(
                    f"Target centre distance {a_w} mm is smaller than base-circle limit "
                    f"{a_d * math.cos(alpha_t):.4f} mm"
                )
            cos_awt = 1.0
        alpha_wt = math.acos(cos_awt)
        x_sum = z_sum * (inv(alpha_wt) - inv(alpha_t)) / (2.0 * math.tan(an))
        mode = solve_shift_mode
        if mode == "auto":
            if abs((gear1.x + gear2.x) - x_sum) <= 1e-6:
                g1, g2 = gear1, gear2
                mode = "preserve"
            elif abs(gear1.x) > 1e-12 and abs(gear2.x) <= 1e-12:
                g1 = gear1
                g2 = gear2.with_x(x_sum - gear1.x)
                mode = "gear2"
            elif abs(gear2.x) > 1e-12 and abs(gear1.x) <= 1e-12:
                g1 = gear1.with_x(x_sum - gear2.x)
                g2 = gear2
                mode = "gear1"
            else:
                delta = x_sum - (gear1.x + gear2.x)
                g1 = gear1.with_x(gear1.x + 0.5 * delta)
                g2 = gear2.with_x(gear2.x + 0.5 * delta)
                mode = "equal_delta"
        elif mode == "equal":
            g1 = gear1.with_x(0.5 * x_sum)
            g2 = gear2.with_x(0.5 * x_sum)
        elif mode == "gear2":
            g1 = gear1
            g2 = gear2.with_x(x_sum - gear1.x)
        elif mode == "gear1":
            g1 = gear1.with_x(x_sum - gear2.x)
            g2 = gear2
        else:
            raise ValueError(f"Unknown solve_shift_mode {solve_shift_mode!r}")
        flags.append(f"solved_shifts_for_a_w:{mode}")
    else:
        g1, g2 = gear1, gear2
        x_sum = g1.x + g2.x
        inv_awt = inv(alpha_t) + 2.0 * math.tan(an) * x_sum / z_sum
        if inv_awt < 0.0:
            raise ValueError(
                f"Negative inv(alpha_wt) = {inv_awt:.6f}: profile shift sum x1+x2={x_sum} "
                f"exceeds geometric meshing limit for z_sum={z_sum}"
            )
        alpha_wt = inv_solve(inv_awt)
        a_w = a_d * math.cos(alpha_t) / math.cos(alpha_wt)

    # Tip shortening to keep bottom clearance >= c* * m_n (ISO 21771 cl. 4.3)
    y_mod = (a_w - a_d) / m_n
    if not g2.is_internal:
        k_req = x_sum - y_mod
    else:
        # For internal pair, radial clearance drops by (x1 + x2 + y_mod) * m_n
        k_req = x_sum + y_mod
    k_short = max(k_req, 0.0) if shorten_tips else 0.0
    if k_short > 1e-9:
        flags.append("tip_shortening_applied")

    dim1 = compute_gear_dimensions(g1, tip_shortening_k=k_short)
    dim2 = compute_gear_dimensions(g2, tip_shortening_k=k_short)
    flags.extend(f"gear1:{fl}" for fl in dim1.flags)
    flags.extend(f"gear2:{fl}" for fl in dim2.flags)

    # Working pitch diameters (ISO 21771 cl. 4.3.2)
    u_ratio = g2.z / g1.z
    if not g2.is_internal:
        d_w1 = 2.0 * a_w / (1.0 + u_ratio)
        d_w2 = 2.0 * a_w * u_ratio / (1.0 + u_ratio)
    else:
        d_w1 = 2.0 * a_w / (u_ratio - 1.0)
        d_w2 = 2.0 * a_w * u_ratio / (u_ratio - 1.0)

    r_b1 = 0.5 * dim1.d_b_mm
    r_b2 = 0.5 * dim2.d_b_mm
    r_a1 = 0.5 * dim1.d_a_mm
    r_a2 = 0.5 * dim2.d_a_mm
    r_f1 = 0.5 * dim1.d_f_mm
    r_f2 = 0.5 * dim2.d_f_mm

    # Bottom radial clearances
    if not g2.is_internal:
        c12 = a_w - r_a1 - r_f2
        c21 = a_w - r_a2 - r_f1
    else:
        c12 = r_f2 - r_a1 - a_w
        c21 = r_a2 - r_f1 - a_w

    # Contact ratios and active profile diameters (ISO 21771 cl. 4.4 / 5.5)
    p_bt = math.pi * m_t * math.cos(alpha_t)
    involute_interference = False
    tip_interference = False
    internal_tip_margin_deg: Optional[float] = None
    radial_assembly_margin_mm: Optional[float] = None

    if r_a1 <= r_b1 or r_a2 <= r_b2:
        involute_interference = True
        flags.append("tip_inside_base_circle")

    t1 = math.sqrt(max(r_a1 * r_a1 - r_b1 * r_b1, 0.0))
    t2 = math.sqrt(max(r_a2 * r_a2 - r_b2 * r_b2, 0.0))
    a_sin = a_w * math.sin(alpha_wt)

    if not g2.is_internal:
        g_alpha = t1 + t2 - a_sin
        t_nf1 = a_sin - t2
        t_nf2 = a_sin - t1
        if t_nf1 <= 0.0 or t_nf2 <= 0.0:
            involute_interference = True
            flags.append("involute_base_interference")
        d_Nf1 = 2.0 * math.hypot(r_b1, max(t_nf1, 0.0))
        d_Nf2 = 2.0 * math.hypot(r_b2, max(t_nf2, 0.0))
        eps_1 = (t1 - r_b1 * math.tan(alpha_wt)) / p_bt
        eps_2 = (t2 - r_b2 * math.tan(alpha_wt)) / p_bt
    else:
        # Internal mesh (ISO 21771 cl. 4.4.1 & Annex A)
        g_alpha = t1 - t2 + a_sin
        t_nf1 = t2 - a_sin
        t_nf2 = t1 + a_sin
        if t_nf1 <= 0.0:
            involute_interference = True
            flags.append("involute_base_interference_pinion")
        if r_a2 <= r_b2:
            involute_interference = True
            flags.append("internal_tip_below_base_circle")
        d_Nf1 = 2.0 * math.hypot(r_b1, max(t_nf1, 0.0))
        d_Nf2 = 2.0 * math.hypot(r_b2, max(t_nf2, 0.0))
        if d_Nf2 >= dim2.d_f_mm - 1e-9:
            involute_interference = True
            flags.append("internal_root_involute_interference")
        eps_1 = (t1 - r_b1 * math.tan(alpha_wt)) / p_bt
        eps_2 = (r_b2 * math.tan(alpha_wt) - t2) / p_bt

        # ISO 21771 Annex A.2 tip interference (secondary collision outside line of action)
        if not involute_interference and (a_w + r_a1 > r_a2):
            c2 = max(-1.0, min(1.0, (a_w * a_w + r_a2 * r_a2 - r_a1 * r_a1) / (2.0 * a_w * r_a2)))
            c1 = max(-1.0, min(1.0, (r_a2 * r_a2 - a_w * a_w - r_a1 * r_a1) / (2.0 * a_w * r_a1)))
            psi2 = math.acos(c2)
            psi1 = math.acos(c1)
            aa1 = math.acos(min(1.0, r_b1 / r_a1))
            aa2 = math.acos(min(1.0, r_b2 / r_a2))
            margin_rad = (g1.z / g2.z) * (psi1 + inv(aa1) - inv(alpha_wt)) - (
                psi2 + inv(aa2) - inv(alpha_wt)
            )
            internal_tip_margin_deg = math.degrees(margin_rad)
            if margin_rad < -1e-9:
                tip_interference = True
                flags.append("internal_tip_interference")
        else:
            internal_tip_margin_deg = None if involute_interference else 90.0

        # Radial assembly heuristic: tooth count difference and radial tip clearance
        radial_assembly_margin_mm = (r_a2 - r_a1) - a_w
        if g2.z - g1.z < 7:
            flags.append("small_tooth_difference_radial_assembly_restricted")

    eps_alpha = g_alpha / p_bt
    b_eff = min(g1.b_mm, g2.b_mm)
    eps_beta = b_eff * math.sin(beta) / (math.pi * m_n)
    eps_gamma = eps_alpha + eps_beta

    if eps_alpha < 1.0:
        flags.append("contact_ratio_below_1.0")
    elif eps_alpha < 1.1:
        flags.append("low_transverse_contact_ratio")
    if c12 < 0.0 or c21 < 0.0:
        tip_interference = True
        flags.append("negative_bottom_clearance")

    # Backlash from transverse tooth-thickness allowances projected to working pitch circle
    # On working pitch circle, j_t = (E_sn1 + E_sn2) / cos(beta) * cos(alpha_t) / cos(alpha_wt)
    # Wait: linear allowance on reference pitch arc scales with d_w / d = cos(alpha_t) / cos(alpha_wt).
    j_t = (
        (g1.tooth_thickness_allowance_mm + g2.tooth_thickness_allowance_mm)
        / cos_b
        * (math.cos(alpha_t) / math.cos(alpha_wt))
    )
    j_n = j_t * math.cos(alpha_wt) * math.cos(math.asin(min(1.0, math.sin(beta) * math.cos(an))))
    j_theta_deg = math.degrees(j_t / (0.5 * d_w2)) if d_w2 > 0.0 else 0.0

    valid_mesh = (
        hand_ok
        and (not involute_interference)
        and (not tip_interference)
        and (not dim1.pointed_tip)
        and (not dim2.pointed_tip)
        and (eps_alpha >= 1.0)
        and (c12 >= 0.0)
        and (c21 >= 0.0)
    )
    state: ValidityState = "valid" if valid_mesh else "failed"
    evidence: Dict[str, Any] = {
        "edition": "ISO 21771:2007 (signed internal convention)",
        "alpha_n_deg": gear1.alpha_n_deg,
        "abs_beta_deg": abs(gear1.beta_deg),
        "epsilon_alpha": eps_alpha,
        "involute_interference": involute_interference,
        "tip_interference": tip_interference,
        "bottom_clearance_min_mm": min(c12, c21),
    }

    return PairGeometryResult(
        gear1=dim1,
        gear2=dim2,
        is_internal_mesh=g2.is_internal,
        u_ratio=u_ratio,
        alpha_wt_deg=math.degrees(alpha_wt),
        a_d_mm=a_d,
        a_w_mm=a_w,
        d_w1_mm=d_w1,
        d_w2_mm=d_w2,
        epsilon_alpha=eps_alpha,
        epsilon_beta=eps_beta,
        epsilon_gamma=eps_gamma,
        epsilon_1=eps_1,
        epsilon_2=eps_2,
        path_of_contact_mm=g_alpha,
        base_pitch_t_mm=p_bt,
        d_Nf1_mm=d_Nf1,
        d_Nf2_mm=d_Nf2,
        circumferential_backlash_mm=j_t,
        normal_backlash_mm=j_n,
        angular_backlash_deg=j_theta_deg,
        involute_interference=involute_interference,
        tip_interference=tip_interference,
        internal_tip_margin_deg=internal_tip_margin_deg,
        radial_assembly_margin_mm=radial_assembly_margin_mm,
        bottom_clearance_12_mm=c12,
        bottom_clearance_21_mm=c21,
        valid_mesh=valid_mesh,
        state=state,
        applicability_evidence=evidence,
        missing_inputs=[],
        flags=flags,
    )
