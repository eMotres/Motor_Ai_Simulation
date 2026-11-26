# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Exact transverse tooth outlines (mm), helix lead, 3-D assembly placement,
and kinematic animation rules (Addition 1 items 7-9).

Geometry conventions:
- Transverse outline coordinates are in **mm**, centered at ``(0, 0)``,
  closed explicitly with ``outline_mm[-1] == outline_mm[0]``.
- Tooth 0 of an external or internal gear has its tooth centerline along ``+X``
  (polar angle ``0 rad``), with tooth ``k`` centered at ``2 * pi * k / z``.
- External gears have exact involute flanks from base circle (or active root
  transition) to tip circle, circular tip arc, exact basic-rack trochoidal root
  fillet (with undercut trimming when ``x < x_min``), and root circular arc.
- Internal gears have exact internal involute flanks (material outside the closed
  inner polyline), circular tip arc on ``d_a``, and a documented circular-tangent
  fillet transition to the root circle ``d_f`` (approximate root fillet, flagged).
- Helical gears carry ``helix_lead_mm`` (signed by hand: ``+`` right, ``-`` left)
  and ``twist_across_face_deg`` so a 3-D CAD/GLB sweeper can extrude with twist.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .common import DictResultMixin, ValidityState, inv, require_number, FieldValidationError
from .geometry import GearSpec, PairGeometryResult, analyze_pair
from .kinematics import ChainKinematicsResult, StageKinematicsResult, pair_kinematics, planetary_kinematics


Point2D = Tuple[float, float]


@dataclass
class GearOutline(DictResultMixin):
    """Closed transverse 2-D tooth outline [mm] and optional helical sweep lead."""

    z: int
    is_internal: bool
    m_n_mm: float
    m_t_mm: float
    d_mm: float
    d_b_mm: float
    d_a_mm: float
    d_f_mm: float
    s_t_pitch_mm: float                   # theoretical transverse circular tooth thickness at d_mm
    b_mm: float
    beta_deg: float
    hand: str
    helix_lead_mm: Optional[float]        # signed lead for 1 full turn (None if spur)
    twist_across_face_deg: float          # total twist angle over face width b_mm
    outer_diameter_mm: Optional[float]    # outer cylinder OD for internal ring blank
    outline_mm: List[Point2D]             # closed polyline (outline_mm[-1] == outline_mm[0])
    points_per_flank: int
    points_per_fillet: int
    root_fillet_model: str                # "exact_rack_trochoid" | "circular_tangent_approx"
    state: ValidityState = "valid"
    applicability_evidence: Dict[str, Any] = field(default_factory=dict)
    missing_inputs: List[str] = field(default_factory=list)
    method: str = "ExactInvoluteTransverseOutline"
    flags: List[str] = field(default_factory=list)


@dataclass
class GearPlacement(DictResultMixin):
    """3-D placement, reference meshing phase, and animation kinematic rule for one gear."""

    name: str
    role: str                             # "gear1" | "gear2" | "sun" | "planet" | "ring"
    stage_index: int
    outline: Optional[GearOutline]
    center_mm: Tuple[float, float, float]
    axis: Tuple[float, float, float]
    face_width_mm: float
    reference_angle_deg: float            # static meshing phase at input_angle_deg = 0
    speed_ratio_to_input: float           # d(theta_gear) / d(theta_input)
    orbital_radius_mm: float = 0.0        # > 0 for planets orbiting carrier origin
    orbital_angle_ref_deg: float = 0.0    # carrier-frame orbital angle at input_angle = 0
    carrier_center_mm: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    carrier_speed_ratio_to_input: float = 0.0
    member_id: str = ""
    outline_id: str = ""
    method: str = "GearAssemblyPlacement"
    flags: List[str] = field(default_factory=list)


@dataclass
class AssemblyPoseMember(DictResultMixin):
    """Evaluated pose (centre and rotation angle) of one gear at a given ``input_angle_deg``."""

    name: str
    role: str
    center_mm: Tuple[float, float, float]
    rotation_deg: float
    axis: Tuple[float, float, float]
    member_id: str = ""
    method: str = "AssemblyPoseMember"
    flags: List[str] = field(default_factory=list)


@dataclass
class AssemblyDescription(DictResultMixin):
    """Complete 3-D assembly layout and animation law for a pair, stage, or stage chain."""

    assembly_kind: str                    # "pair" | "planetary" | "chain"
    members: List[GearPlacement]
    outlines: Dict[str, GearOutline] = field(default_factory=dict)
    carrier_speed_ratio_to_input: float = 0.0
    overall_ratio: float = 1.0
    state: ValidityState = "valid"
    applicability_evidence: Dict[str, Any] = field(default_factory=dict)
    missing_inputs: List[str] = field(default_factory=list)
    method: str = "GearAssembly3DDescription"
    flags: List[str] = field(default_factory=list)

    def with_outline_options(self, *, stride=1, once=False):
        """Viewer-only uniform decimation and content-addressed outline reuse.

        This returns a new description; the full-resolution geometry is retained.
        A placement with outline=None resolves through outlines[outline_id].
        """
        import hashlib
        import json
        require_number('outline_stride', stride, positive=True, integer=True)
        if type(once) is not bool: raise FieldValidationError('/outline_once', 'Expected bool')
        outlines, members = {}, []
        for member in self.members:
            outline = member.outline or self.outlines[member.outline_id]
            if stride > 1:
                points = outline.outline_mm[:-1:stride]
                if len(points) < 3: raise FieldValidationError('/outline_stride', 'Fewer than three points remain')
                outline = replace(outline, outline_mm=points+[points[0]], state='estimated',
                    flags=outline.flags+['viewer_decimation_not_manufacturing_geometry'],
                    applicability_evidence={**outline.applicability_evidence, 'outline_stride': stride})
            identity = ''
            if once:
                encoded = json.dumps(outline.to_dict(), sort_keys=True, separators=(',', ':'), allow_nan=False)
                identity = 'outline_'+hashlib.sha256(encoded.encode('utf8')).hexdigest()
                outlines[identity] = outline
            members.append(replace(member, outline=None if once else outline, outline_id=identity))
        return replace(self, members=members, outlines=outlines,
            flags=self.flags+(['viewer_decimation_not_manufacturing_geometry'] if stride > 1 else []))

    def pose_at(self, input_angle_deg: float) -> Dict[str, AssemblyPoseMember]:
        """Return exact 3-D center and rotation angle of every gear at ``input_angle_deg``."""
        require_number('input_angle_deg', input_angle_deg)
        th_in = float(input_angle_deg)
        out: Dict[str, AssemblyPoseMember] = {}
        for m in self.members:
            if m.orbital_radius_mm > 0.0:
                orb_deg = m.orbital_angle_ref_deg + m.carrier_speed_ratio_to_input * th_in
                orb_rad = math.radians(orb_deg)
                cx = m.carrier_center_mm[0] + m.orbital_radius_mm * math.cos(orb_rad)
                cy = m.carrier_center_mm[1] + m.orbital_radius_mm * math.sin(orb_rad)
                cz = m.carrier_center_mm[2]
                center = (cx, cy, cz)
            else:
                center = m.center_mm
            rot_deg = m.reference_angle_deg + m.speed_ratio_to_input * th_in
            out[m.name] = AssemblyPoseMember(
                name=m.name,
                member_id=m.member_id or m.name,
                role=m.role,
                center_mm=center,
                rotation_deg=rot_deg,
                axis=m.axis,
            )
        return out


def _rotate_translate_poly(
    pts: Sequence[Point2D],
    angle_deg: float,
    cx: float = 0.0,
    cy: float = 0.0,
) -> np.ndarray:
    arr = np.asarray(pts, dtype=float)
    th = math.radians(angle_deg)
    c, s = math.cos(th), math.sin(th)
    x = arr[:, 0] * c - arr[:, 1] * s + cx
    y = arr[:, 0] * s + arr[:, 1] * c + cy
    return np.column_stack([x, y])


def generate_gear_outline(
    gear: GearSpec,
    *,
    tip_shortening_k: float = 0.0,
    points_per_flank: int = 24,
    points_per_fillet: int = 12,
    ring_outer_diameter_mm: Optional[float] = None,
) -> GearOutline:
    """Generate the closed transverse tooth outline [mm] for an external or internal gear."""
    if points_per_flank < 4:
        raise ValueError("points_per_flank must be >= 4")
    if points_per_fillet < 3:
        raise ValueError("points_per_fillet must be >= 3")

    an = math.radians(gear.alpha_n_deg)
    beta = math.radians(abs(gear.beta_deg))
    cos_b = math.cos(beta)
    m_t = gear.m_n_mm / cos_b
    alpha_t = math.atan(math.tan(an) / cos_b)

    r = 0.5 * gear.z * m_t
    r_b = r * math.cos(alpha_t)
    k_use = max(float(tip_shortening_k), 0.0)

    s_n = gear.m_n_mm * (0.5 * math.pi + 2.0 * gear.x * math.tan(an)) - gear.tooth_thickness_allowance_mm
    s_t = s_n / cos_b
    psi_ref = s_t / (2.0 * r)
    pitch = 2.0 * math.pi / gear.z

    flags: List[str] = []
    if abs(gear.beta_deg) > 1e-9:
        sign_hand = 1.0 if gear.effective_hand == "right" else -1.0
        helix_lead: Optional[float] = sign_hand * (2.0 * math.pi * r) / math.tan(beta)
        twist_deg = 360.0 * gear.b_mm / helix_lead
    else:
        helix_lead = None
        twist_deg = 0.0

    if not gear.is_internal:
        r_a = r + gear.m_n_mm * (gear.ha_star + gear.x - k_use)
        r_f = r - gear.m_n_mm * (gear.hf_star - gear.x)

        # Exact basic-rack trochoidal fillet in transverse plane (normalized by m_t):
        # Radial rack parameters scale in m_n, tangential pitch scales in m_t:
        hf_t = gear.hf_star * cos_b
        rho_t = gear.rho_fp_star * cos_b
        x_t = gear.x * cos_b
        # Allowance thins gear tooth by widening rack tooth space half-width by E_t / (2*m_t)
        allow_t = (gear.tooth_thickness_allowance_mm / cos_b) / m_t
        g_t = rho_t - hf_t + x_t
        e_t = 0.25 * math.pi - 0.5 * allow_t - hf_t * math.tan(alpha_t) - (1.0 - math.sin(alpha_t)) / math.cos(alpha_t) * rho_t
        xc0 = 0.5 * math.pi - e_t

        def _eval_fillet(ak_val: float) -> Tuple[float, float]:
            phi = (xc0 + g_t * math.tan(ak_val)) / (0.5 * gear.z)
            dx = -g_t * math.tan(ak_val) + rho_t * math.sin(ak_val)
            dy = g_t - rho_t * math.cos(ak_val)
            xg = (0.5 * gear.z) * math.sin(phi) + dx * math.cos(phi) + dy * math.sin(phi)
            yg = (0.5 * gear.z) * math.cos(phi) - dx * math.sin(phi) + dy * math.cos(phi)
            return m_t * math.hypot(xg, yg), math.atan2(xg, yg)

        def _psi_inv_at_r(rv: float) -> float:
            aa = math.acos(min(1.0, r_b / max(rv, r_b)))
            return psi_ref + inv(alpha_t) - inv(aa)

        ak_end_nom = -(0.5 * math.pi - alpha_t)
        r_end_nom, af_end_nom = _eval_fillet(ak_end_nom)
        ak_end_use = ak_end_nom
        if r_end_nom > r_b + 1e-12:
            # Find ak_rb where trochoid crosses base circle r_b, then bisect for exact involute intersection
            lo_r, hi_r = 0.0, ak_end_nom
            if _eval_fillet(lo_r)[0] < r_b:
                for _ in range(60):
                    mid_r = 0.5 * (lo_r + hi_r)
                    if _eval_fillet(mid_r)[0] < r_b:
                        lo_r = mid_r
                    else:
                        hi_r = mid_r
                ak_rb = hi_r
            else:
                ak_rb = 0.0
            r_rb_chk, af_rb_chk = _eval_fillet(ak_rb)
            g_rb = af_rb_chk - _psi_inv_at_r(max(r_rb_chk, r_b))
            g_end = af_end_nom - _psi_inv_at_r(r_end_nom)
            if g_rb * g_end <= 0.0:
                lo_g, hi_g = ak_rb, ak_end_nom
                f_lo = g_rb
                for _ in range(60):
                    mid_g = 0.5 * (lo_g + hi_g)
                    rm, afm = _eval_fillet(mid_g)
                    fm = afm - _psi_inv_at_r(max(rm, r_b))
                    if f_lo * fm <= 0.0:
                        hi_g = mid_g
                    else:
                        lo_g = mid_g
                        f_lo = fm
                ak_end_use = 0.5 * (lo_g + hi_g)
                flags.append("undercut_trochoid_trimmed_at_involute_intersection")

        fillet_r: List[float] = []
        fillet_ang: List[float] = []
        for i in range(points_per_fillet + 1):
            ak = ak_end_use * i / points_per_fillet
            rv, af = _eval_fillet(ak)
            fillet_r.append(rv)
            fillet_ang.append(af)

        r_fil_top = fillet_r[-1]
        r_start = min(max(r_b, r_fil_top), r_a)
        if r_fil_top > r_b + 1e-6 and "undercut_trochoid_trimmed_at_involute_intersection" not in flags:
            flags.append("undercut_trochoid_meets_involute_above_base")

        alpha_start = math.acos(min(1.0, r_b / max(r_start, r_b)))
        alpha_tip = math.acos(min(1.0, r_b / max(r_a, r_b)))
        alphas = [
            alpha_start + (alpha_tip - alpha_start) * i / (points_per_flank - 1)
            for i in range(points_per_flank)
        ]
        if alpha_start < alpha_t < alpha_tip:
            alphas = sorted(set(alphas + [alpha_t]))
        n_tip_seg = max(6, points_per_flank // 4)

        # Check if involute tip self-intersects (pointed tip)
        psi_tip = psi_ref + inv(alpha_t) - inv(alpha_tip)
        if psi_tip < 0.0:
            flags.append("pointed_tip_clamped")
            psi_tip = 0.0

        pts: List[Point2D] = []
        for k in range(gear.z):
            th_c = k * pitch
            # Root arc from previous half-pitch space centerline to fillet start
            a_root_0 = th_c - 0.5 * pitch
            a_root_1 = th_c - fillet_ang[0]
            if a_root_1 > a_root_0:
                for i in range(3):
                    ang = a_root_0 + (a_root_1 - a_root_0) * i / 3.0
                    pts.append((r_f * math.cos(ang), r_f * math.sin(ang)))
            # Left fillet going upward
            for rf_pt, af_pt in zip(fillet_r, fillet_ang):
                ang = th_c - af_pt
                pts.append((rf_pt * math.cos(ang), rf_pt * math.sin(ang)))
            # Left involute flank going up to tip
            for a_i in alphas[1:]:
                ri = r_b / math.cos(a_i)
                psi_i = psi_ref + inv(alpha_t) - inv(a_i)
                ang = th_c - psi_i
                pts.append((ri * math.cos(ang), ri * math.sin(ang)))
            # Tip arc
            for i in range(1, n_tip_seg + 1):
                ang = th_c - psi_tip + (2.0 * psi_tip) * i / n_tip_seg
                pts.append((r_a * math.cos(ang), r_a * math.sin(ang)))
            # Right involute flank going down
            for a_i in reversed(alphas[:-1]):
                ri = r_b / math.cos(a_i)
                psi_i = psi_ref + inv(alpha_t) - inv(a_i)
                ang = th_c + psi_i
                pts.append((ri * math.cos(ang), ri * math.sin(ang)))
            # Right fillet going down to root
            for rf_pt, af_pt in zip(reversed(fillet_r[:-1]), reversed(fillet_ang[:-1])):
                ang = th_c + af_pt
                pts.append((rf_pt * math.cos(ang), rf_pt * math.sin(ang)))
            # Remaining root arc to +0.5*pitch
            a_root_2 = th_c + fillet_ang[0]
            a_root_3 = th_c + 0.5 * pitch
            if a_root_3 > a_root_2:
                for i in range(1, 3):
                    ang = a_root_2 + (a_root_3 - a_root_2) * i / 3.0
                    pts.append((r_f * math.cos(ang), r_f * math.sin(ang)))

        pts.append(pts[0])
        return GearOutline(
            z=gear.z,
            is_internal=False,
            m_n_mm=gear.m_n_mm,
            m_t_mm=m_t,
            d_mm=2.0 * r,
            d_b_mm=2.0 * r_b,
            d_a_mm=2.0 * r_a,
            d_f_mm=2.0 * r_f,
            s_t_pitch_mm=s_t,
            b_mm=gear.b_mm,
            beta_deg=gear.beta_deg,
            hand=gear.effective_hand,
            helix_lead_mm=helix_lead,
            twist_across_face_deg=twist_deg,
            outer_diameter_mm=None,
            outline_mm=pts,
            points_per_flank=points_per_flank,
            points_per_fillet=points_per_fillet,
            root_fillet_model="exact_rack_trochoid",
            state="valid",
            applicability_evidence={"root_fillet": "exact basic-rack rolling trochoid"},
            flags=flags,
        )

    # Internal gear: teeth point INWARD from root circle r_f to tip circle r_a (r_f > r > r_a > r_b)
    r_a = r - gear.m_n_mm * (gear.ha_star + gear.x - k_use)
    r_f = r + gear.m_n_mm * (gear.hf_star - gear.x)
    if r_a <= r_b:
        raise ValueError(f"Internal gear tip radius {r_a:.4f} <= base radius {r_b:.4f}")

    rho_fil = gear.rho_fp_star * gear.m_n_mm
    r_fil_start = min(r_f - 0.1 * rho_fil, max(r_a + 0.05 * gear.m_n_mm, r_f - rho_fil))
    alpha_tip = math.acos(min(1.0, r_b / r_a))
    alpha_fil = math.acos(min(1.0, r_b / r_fil_start))
    alpha_root = math.acos(min(1.0, r_b / r_f))

    # Internal tooth circular half-angle narrows as r decreases:
    # psi(r) = psi_ref + inv(alpha(r)) - inv(alpha_t)
    psi_tip = psi_ref + inv(alpha_tip) - inv(alpha_t)
    psi_fil = psi_ref + inv(alpha_fil) - inv(alpha_t)
    psi_root_flank = psi_ref + inv(alpha_root) - inv(alpha_t)
    # Smooth tangent fillet transition angle at r_f
    psi_root_end = min(0.49 * pitch, psi_root_flank + (rho_fil / r_f))
    flags.append("internal_root_fillet_circular_tangent_approximation")

    alphas_int = [
        alpha_fil + (alpha_tip - alpha_fil) * i / (points_per_flank - 1)
        for i in range(points_per_flank)
    ]
    if alpha_tip < alpha_t < alpha_fil:
        alphas_int = sorted(set(alphas_int + [alpha_t]), reverse=True)
    n_tip_seg_int = max(6, points_per_flank // 4)

    pts = []
    for k in range(gear.z):
        th_c = k * pitch
        # Root arc from space center -0.5*pitch to left fillet start
        a0 = th_c - 0.5 * pitch
        a1 = th_c - psi_root_end
        if a1 > a0:
            for i in range(3):
                ang = a0 + (a1 - a0) * i / 3.0
                pts.append((r_f * math.cos(ang), r_f * math.sin(ang)))
        # Left fillet transition from (r_f, -psi_root_end) to (r_fil_start, -psi_fil)
        for i in range(1, points_per_fillet + 1):
            t_f = i / points_per_fillet
            # Smooth quadratic Bezier-style blend from root circle tangent to involute start
            ri = r_f + (r_fil_start - r_f) * (t_f ** 1.3)
            psi_i = psi_root_end + (psi_fil - psi_root_end) * (t_f ** 0.7)
            ang = th_c - psi_i
            pts.append((ri * math.cos(ang), ri * math.sin(ang)))
        # Left involute flank going inward from r_fil_start down to r_a
        for a_i in alphas_int[1:]:
            ri = r_b / math.cos(a_i)
            psi_i = psi_ref + inv(a_i) - inv(alpha_t)
            ang = th_c - psi_i
            pts.append((ri * math.cos(ang), ri * math.sin(ang)))
        # Tip arc on r_a from -psi_tip to +psi_tip
        for i in range(1, n_tip_seg_int + 1):
            ang = th_c - psi_tip + (2.0 * psi_tip) * i / n_tip_seg_int
            pts.append((r_a * math.cos(ang), r_a * math.sin(ang)))
        # Right involute flank going outward from r_a to r_fil_start
        for a_i in reversed(alphas_int[:-1]):
            ri = r_b / math.cos(a_i)
            psi_i = psi_ref + inv(a_i) - inv(alpha_t)
            ang = th_c + psi_i
            pts.append((ri * math.cos(ang), ri * math.sin(ang)))
        # Right fillet back to r_f
        for i in range(points_per_fillet - 1, -1, -1):
            t_f = i / points_per_fillet
            ri = r_f + (r_fil_start - r_f) * (t_f ** 1.3)
            psi_i = psi_root_end + (psi_fil - psi_root_end) * (t_f ** 0.7)
            ang = th_c + psi_i
            pts.append((ri * math.cos(ang), ri * math.sin(ang)))
        a2 = th_c + psi_root_end
        a3 = th_c + 0.5 * pitch
        if a3 > a2:
            for i in range(1, 3):
                ang = a2 + (a3 - a2) * i / 3.0
                pts.append((r_f * math.cos(ang), r_f * math.sin(ang)))

    pts.append(pts[0])
    rim_default = gear.rim_thickness_mm if gear.rim_thickness_mm is not None else 2.5 * gear.m_n_mm
    od_ring = float(ring_outer_diameter_mm) if ring_outer_diameter_mm is not None else (2.0 * r_f + 2.0 * rim_default)

    return GearOutline(
        z=gear.z,
        is_internal=True,
        m_n_mm=gear.m_n_mm,
        m_t_mm=m_t,
        d_mm=2.0 * r,
        d_b_mm=2.0 * r_b,
        d_a_mm=2.0 * r_a,
        d_f_mm=2.0 * r_f,
        s_t_pitch_mm=s_t,
        b_mm=gear.b_mm,
        beta_deg=gear.beta_deg,
        hand=gear.effective_hand,
        helix_lead_mm=helix_lead,
        twist_across_face_deg=twist_deg,
        outer_diameter_mm=od_ring,
        outline_mm=pts,
        points_per_flank=points_per_flank,
        points_per_fillet=points_per_fillet,
        root_fillet_model="circular_tangent_approx",
        state="estimated",
        applicability_evidence={"root_fillet": "documented smooth circular-tangent approximation for internal ring"},
        flags=flags,
    )


def _points_in_polygon_mask(pts: np.ndarray, poly: np.ndarray) -> np.ndarray:
    """Vectorized ray-casting point-in-polygon check for an array of 2D points (N, 2).

    Pre-filters polygon edges to the Y-band and +X half-plane of ``pts`` (exact,
    zero approximation) for ~50x speedup on dense multi-tooth polylines.
    """
    if len(pts) == 0:
        return np.zeros(0, dtype=bool)
    x_min = float(np.min(pts[:, 0]))
    y_min = float(np.min(pts[:, 1]))
    y_max = float(np.max(pts[:, 1]))
    p_x1 = poly[:-1, 0]
    p_y1 = poly[:-1, 1]
    p_x2 = poly[1:, 0]
    p_y2 = poly[1:, 1]
    keep = (
        (np.maximum(p_x1, p_x2) >= x_min)
        & (np.maximum(p_y1, p_y2) >= y_min)
        & (np.minimum(p_y1, p_y2) <= y_max)
    )
    if not np.any(keep):
        return np.zeros(len(pts), dtype=bool)

    px = pts[:, 0][:, None]
    py = pts[:, 1][:, None]
    x1 = p_x1[keep][None, :]
    y1 = p_y1[keep][None, :]
    x2 = p_x2[keep][None, :]
    y2 = p_y2[keep][None, :]
    cond = (y1 > py) != (y2 > py)
    denom = np.where(cond, y2 - y1, 1.0)
    x_int = x1 + (py - y1) * (x2 - x1) / denom
    crossings = np.sum(cond & (px < x_int), axis=1)
    return (crossings % 2) == 1


def _min_boundary_distance(pts: np.ndarray, seg_a: np.ndarray, seg_b: np.ndarray) -> float:
    """Vectorized minimum Euclidean distance from points (N, 2) to segments [seg_a, seg_b] (M, 2)."""
    if len(pts) == 0 or len(seg_a) == 0:
        return math.inf
    ab = seg_b - seg_a                      # (M, 2)
    ab2 = np.maximum(np.sum(ab * ab, axis=1), 1.0e-18)  # (M,)
    # Chunk pts if large to bound memory
    min_d2 = math.inf
    for i0 in range(0, len(pts), 128):
        p = pts[i0:i0 + 128]                # (K, 2)
        ap = p[:, None, :] - seg_a[None, :, :]  # (K, M, 2)
        t = np.clip(np.sum(ap * ab[None, :, :], axis=2) / ab2[None, :], 0.0, 1.0)
        diff = ap - t[:, :, None] * ab[None, :, :]
        d2 = float(np.min(np.sum(diff * diff, axis=2)))
        if d2 < min_d2:
            min_d2 = d2
    return math.sqrt(min_d2)


def _segments_intersect_any(
    s1_a: np.ndarray,
    s1_b: np.ndarray,
    s2_a: np.ndarray,
    s2_b: np.ndarray,
) -> bool:
    """Return True if any segment in [s1_a, s1_b] properly crosses any segment in [s2_a, s2_b]."""
    if len(s1_a) == 0 or len(s2_a) == 0:
        return False

    a_min_x = np.minimum(s1_a[:, 0], s1_b[:, 0])[:, None]
    a_max_x = np.maximum(s1_a[:, 0], s1_b[:, 0])[:, None]
    a_min_y = np.minimum(s1_a[:, 1], s1_b[:, 1])[:, None]
    a_max_y = np.maximum(s1_a[:, 1], s1_b[:, 1])[:, None]

    b_min_x = np.minimum(s2_a[:, 0], s2_b[:, 0])[None, :]
    b_max_x = np.maximum(s2_a[:, 0], s2_b[:, 0])[None, :]
    b_min_y = np.minimum(s2_a[:, 1], s2_b[:, 1])[None, :]
    b_max_y = np.maximum(s2_a[:, 1], s2_b[:, 1])[None, :]

    overlap = (a_max_x >= b_min_x) & (b_max_x >= a_min_x) & (a_max_y >= b_min_y) & (b_max_y >= a_min_y)
    idx1, idx2 = np.nonzero(overlap)
    if len(idx1) == 0:
        return False

    p1 = s1_a[idx1]
    q1 = s1_b[idx1]
    p2 = s2_a[idx2]
    q2 = s2_b[idx2]

    v1 = q1 - p1
    v2 = q2 - p2
    d1 = v1[:, 0] * (p2[:, 1] - p1[:, 1]) - v1[:, 1] * (p2[:, 0] - p1[:, 0])
    d2 = v1[:, 0] * (q2[:, 1] - p1[:, 1]) - v1[:, 1] * (q2[:, 0] - p1[:, 0])
    d3 = v2[:, 0] * (p1[:, 1] - p2[:, 1]) - v2[:, 1] * (p1[:, 0] - p2[:, 0])
    d4 = v2[:, 0] * (q1[:, 1] - p2[:, 1]) - v2[:, 1] * (q1[:, 0] - p2[:, 0])

    # Proper crossing (straddling segments strictly on opposite sides)
    return bool(np.any((d1 * d2 < -1.0e-18) & (d3 * d4 < -1.0e-18)))


def mesh_signed_distance_mm(
    outline1: GearOutline,
    angle1_deg: float,
    center1_mm: Point2D,
    outline2: GearOutline,
    angle2_deg: float,
    center2_mm: Point2D,
    *,
    window_half_width_mm: Optional[float] = None,
) -> float:
    """Compute signed clearance [mm] between two meshing 2-D gear outlines.

    - For external-external pairs: positive = separation, negative = overlap/penetration
      (detecting both vertex containment and proper edge-edge crossings).
    - For external-internal pairs (``outline2.is_internal=True``): the internal
      outline bounds the hollow cavity (pinion must stay INSIDE ``outline2``),
      so negative = pinion vertex lies OUTSIDE the ring inner void or edges cross.
    """
    p1 = _rotate_translate_poly(outline1.outline_mm, angle1_deg, center1_mm[0], center1_mm[1])
    p2 = _rotate_translate_poly(outline2.outline_mm, angle2_deg, center2_mm[0], center2_mm[1])

    dx = center2_mm[0] - center1_mm[0]
    dy = center2_mm[1] - center1_mm[1]
    dist_c = math.hypot(dx, dy)

    # Full-polygon check for coaxial/generic external polygons (not offset gears)
    use_full = (window_half_width_mm is None) and (dist_c < 1e-6) and (not outline2.is_internal)
    if use_full:
        n1 = p1[:-1]
        n2 = p2[:-1]
        s1_a, s1_b = p1[:-1], p1[1:]
        s2_a, s2_b = p2[:-1], p2[1:]
    else:
        ux, uy = (dx / dist_c, dy / dist_c) if dist_c > 1e-9 else (1.0, 0.0)
        r_win = window_half_width_mm or (4.0 * max(outline1.m_t_mm, 1.0))

        r1 = 0.5 * outline1.d_mm
        if not outline2.is_internal:
            mx = center1_mm[0] + r1 * ux
            my = center1_mm[1] + r1 * uy
        else:
            mx = center1_mm[0] - r1 * ux
            my = center1_mm[1] - r1 * uy

        n1 = p1[:-1][np.hypot(p1[:-1, 0] - mx, p1[:-1, 1] - my) <= r_win]
        n2 = p2[:-1][np.hypot(p2[:-1, 0] - mx, p2[:-1, 1] - my) <= r_win]
        if len(n1) == 0:
            n1 = p1[:-1]
        if len(n2) == 0:
            n2 = p2[:-1]

        # Segment window slightly larger than vertex window to avoid missing edge projections
        r_seg = r_win + 2.0 * max(outline1.m_t_mm, 1.0)
        mid1 = 0.5 * (p1[:-1] + p1[1:])
        mid2 = 0.5 * (p2[:-1] + p2[1:])
        s1_mask = np.hypot(mid1[:, 0] - mx, mid1[:, 1] - my) <= r_seg
        s2_mask = np.hypot(mid2[:, 0] - mx, mid2[:, 1] - my) <= r_seg
        s1_a = p1[:-1][s1_mask] if np.any(s1_mask) else p1[:-1]
        s1_b = p1[1:][s1_mask] if np.any(s1_mask) else p1[1:]
        s2_a = p2[:-1][s2_mask] if np.any(s2_mask) else p2[:-1]
        s2_b = p2[1:][s2_mask] if np.any(s2_mask) else p2[1:]

    seg_cross = _segments_intersect_any(s1_a, s1_b, s2_a, s2_b)
    if not outline2.is_internal:
        pen_vert = bool(
            np.any(_points_in_polygon_mask(n1, p2))
            or np.any(_points_in_polygon_mask(n2, p1))
        )
    else:
        pen_vert = bool(
            np.any(~_points_in_polygon_mask(n1, p2))
            or np.any(_points_in_polygon_mask(n2, p1))
        )

    d = min(_min_boundary_distance(n1, s2_a, s2_b), _min_boundary_distance(n2, s1_a, s1_b))
    if pen_vert or seg_cross:
        return -max(d, 1.0e-6) if seg_cross and d < 1.0e-6 else -d
    return d


def build_pair_assembly(
    gear1: GearSpec,
    gear2: GearSpec,
    *,
    centre_distance_mm: Optional[float] = None,
    z_offset_mm: float = 0.0,
    points_per_flank: int = 24,
    points_per_fillet: int = 12,
    stage_index: int = 0,
    prefix: str = "pair",
) -> AssemblyDescription:
    """Build 3-D assembly placement and meshing phase for an external or internal gear pair."""
    geom = analyze_pair(gear1, gear2, centre_distance_mm=centre_distance_mm)
    g1_use = gear1.with_x(geom.gear1.x)
    g2_use = gear2.with_x(geom.gear2.x)
    kin = pair_kinematics(g1_use, g2_use, input_speed_rpm=1000.0, pair_geom=geom)

    k_short_1 = geom.gear1.tip_shortening_k_mm / g1_use.m_n_mm
    k_short_2 = geom.gear2.tip_shortening_k_mm / g2_use.m_n_mm
    out1 = generate_gear_outline(
        g1_use, tip_shortening_k=k_short_1, points_per_flank=points_per_flank, points_per_fillet=points_per_fillet
    )
    out2 = generate_gear_outline(
        g2_use, tip_shortening_k=k_short_2, points_per_flank=points_per_flank, points_per_fillet=points_per_fillet
    )

    if not gear2.is_internal:
        # gear1 at (0,0), gear2 at (a_w, 0); gear1 tooth 0 at 0 deg meshes into gear2 space at 180 deg
        c1 = (0.0, 0.0, z_offset_mm)
        c2 = (geom.a_w_mm, 0.0, z_offset_mm)
        phi1_deg = 0.0
        phi2_deg = 180.0 + 180.0 / gear2.z
    else:
        # Internal ring at (-a_w, 0), pinion at (0, 0) (or ring at (0,0), pinion at (+a_w, 0))
        # Place ring at (0, 0) and pinion at (+a_w, 0): mesh point is along +X!
        # Pinion has tooth 0 at 0 deg; ring needs a SPACE at 0 deg => ring rotated by 180 / z2!
        c2 = (0.0, 0.0, z_offset_mm)
        c1 = (geom.a_w_mm, 0.0, z_offset_mm)
        phi1_deg = 0.0
        phi2_deg = 180.0 / gear2.z

    m1 = GearPlacement(
        name=f"{prefix}.gear1",
        role="gear1",
        stage_index=stage_index,
        outline=out1,
        center_mm=c1,
        axis=(0.0, 0.0, 1.0),
        face_width_mm=gear1.b_mm,
        reference_angle_deg=phi1_deg,
        speed_ratio_to_input=1.0,
    )
    m2 = GearPlacement(
        name=f"{prefix}.gear2",
        role="gear2",
        stage_index=stage_index,
        outline=out2,
        center_mm=c2,
        axis=(0.0, 0.0, 1.0),
        face_width_mm=gear2.b_mm,
        reference_angle_deg=phi2_deg,
        speed_ratio_to_input=1.0 / kin.ratio,
    )

    return AssemblyDescription(
        assembly_kind="pair",
        members=[m1, m2],
        overall_ratio=kin.ratio,
        state=geom.state,
        applicability_evidence={"a_w_mm": geom.a_w_mm, "valid_mesh": geom.valid_mesh},
        flags=list(geom.flags),
    )


def build_planetary_assembly(
    *,
    sun: GearSpec,
    planet: GearSpec,
    ring: GearSpec,
    n_planets: int = 3,
    planet2: Optional[GearSpec] = None,
    fixed_member: str = "ring",
    input_member: str = "sun",
    output_member: str = "carrier",
    z_offset_mm: float = 0.0,
    points_per_flank: int = 24,
    points_per_fillet: int = 12,
    stage_index: int = 0,
    prefix: str = "stage",
) -> AssemblyDescription:
    """Build 3-D assembly placement, non-interpenetrating phase, and animation for a planetary stage."""
    p_ring = planet2 if planet2 is not None else planet
    sp = analyze_pair(sun, planet)
    pr = analyze_pair(p_ring, ring)
    kin = planetary_kinematics(
        sun=sun,
        planet=planet,
        ring=ring,
        n_planets=n_planets,
        fixed_member=fixed_member,
        input_member=input_member,
        output_member=output_member,
        input_speed_rpm=1000.0,
        planet2=planet2,
        sun_planet_geom=sp,
        planet_ring_geom=pr,
    )

    out_s = generate_gear_outline(
        sun, tip_shortening_k=sp.gear1.tip_shortening_k_mm / sun.m_n_mm,
        points_per_flank=points_per_flank, points_per_fillet=points_per_fillet,
    )
    out_p1 = generate_gear_outline(
        planet, tip_shortening_k=sp.gear2.tip_shortening_k_mm / planet.m_n_mm,
        points_per_flank=points_per_flank, points_per_fillet=points_per_fillet,
    )
    out_r = generate_gear_outline(
        ring, tip_shortening_k=pr.gear2.tip_shortening_k_mm / ring.m_n_mm,
        points_per_flank=points_per_flank, points_per_fillet=points_per_fillet,
    )

    spd = kin.member_speeds_rpm
    w_in = spd[input_member]
    r_sun = spd["sun"] / w_in
    r_ring = spd["ring"] / w_in
    r_carrier = spd["carrier"] / w_in
    r_planet = spd["planet_abs"] / w_in

    # Reference phasing at theta_input = 0:
    # Sun tooth 0 centered along +X (0 deg).
    # Planet k at orbital angle theta_k:
    #   planet angle = 180 + 180/z_p + (1 + z_s/z_p) * theta_k
    # Ring has a tooth/space matching planet 0 at theta_0 = 0 (outward ray 0 deg):
    #   at local planet angle -(180 + 180/z_p), if z_p is even -> planet space at 0 deg -> ring tooth at 0 deg (phi_r = 0);
    #   if z_p is odd -> planet tooth at 0 deg -> ring space at 0 deg (phi_r = 180 / z_r).
    phi_s = 0.0
    if planet2 is None:
        phi_r = 0.0 if (planet.z % 2 == 0) else (180.0 / ring.z)
    else:
        # For stepped planet (even when planet2.z == planet.z with different module), phase ring to planet2 at theta_0 = 0
        phi_r = 0.0 if (planet2.z % 2 == 0) else (180.0 / ring.z)

    is_compound = planet2 is not None
    z_second = z_offset_mm + max(sun.b_mm, planet.b_mm)
    z_ring_mm = z_second if is_compound else z_offset_mm

    members: List[GearPlacement] = [
        GearPlacement(
            name=f"{prefix}.sun",
            role="sun",
            stage_index=stage_index,
            outline=out_s,
            center_mm=(0.0, 0.0, z_offset_mm),
            axis=(0.0, 0.0, 1.0),
            face_width_mm=sun.b_mm,
            reference_angle_deg=phi_s,
            speed_ratio_to_input=r_sun,
        ),
        GearPlacement(
            name=f"{prefix}.ring",
            role="ring",
            stage_index=stage_index,
            outline=out_r,
            center_mm=(0.0, 0.0, z_ring_mm),
            axis=(0.0, 0.0, 1.0),
            face_width_mm=ring.b_mm,
            reference_angle_deg=phi_r,
            speed_ratio_to_input=r_ring,
        ),
    ]

    a_orb = sp.a_w_mm
    for k, th_k in enumerate(kin.planet_angles_deg):
        th_rad = math.radians(th_k)
        cx = a_orb * math.cos(th_rad)
        cy = a_orb * math.sin(th_rad)
        phi_pk = 180.0 + (180.0 / planet.z) + (1.0 + sun.z / planet.z) * th_k
        members.append(
            GearPlacement(
                name=f"{prefix}.planet_{k}",
                role="planet",
                stage_index=stage_index,
                outline=out_p1,
                center_mm=(cx, cy, z_offset_mm),
                axis=(0.0, 0.0, 1.0),
                face_width_mm=planet.b_mm,
                reference_angle_deg=phi_pk,
                speed_ratio_to_input=r_planet,
                orbital_radius_mm=a_orb,
                orbital_angle_ref_deg=th_k,
                carrier_center_mm=(0.0, 0.0, z_offset_mm),
                carrier_speed_ratio_to_input=r_carrier,
            )
        )
        if planet2 is not None:
            out_p2 = generate_gear_outline(
                planet2, tip_shortening_k=pr.gear1.tip_shortening_k_mm / planet2.m_n_mm,
                points_per_flank=points_per_flank, points_per_fillet=points_per_fillet,
            )
            # Clocking offset so planet2 aligns with ring at theta_k:
            # At theta_k, planet2 needs -(phi_r * z_r + th_k * z_r) / z_p2 + 180/z_p2 * ...
            phi_p2k = (180.0 / planet2.z if (planet2.z % 2 != 0) else 0.0) + (
                1.0 - ring.z / planet2.z
            ) * th_k + (0.0 if planet2.z % 2 == 0 else 180.0 / planet2.z)
            # Note: relative to planet1, for k=0 (th_k=0) phi_p2k=0 so planet2 tooth 0 aligns with ring space 0 if even?
            # Check exact ring alignment at th_0 = 0: outward ray is 0 deg; ring tooth at 0 if planet2.z even, space at 0 if odd.
            # If planet2.z even: phi_r = 0 (ring tooth at 0 deg) -> planet2 needs SPACE at 0 deg -> phi_p2k = 180/z_p2!
            # If planet2.z odd: phi_r = 180/z_r (ring space at 0 deg) -> planet2 needs TOOTH at 0 deg -> phi_p2k = 0!
            base_p2 = (180.0 / planet2.z) if (planet2.z % 2 == 0) else 0.0
            phi_p2k = base_p2 + (1.0 - ring.z / planet2.z) * th_k
            members.append(
                GearPlacement(
                    name=f"{prefix}.planet2_{k}",
                    role="planet2",
                    stage_index=stage_index,
                    outline=out_p2,
                    center_mm=(cx, cy, z_second),
                    axis=(0.0, 0.0, 1.0),
                    face_width_mm=planet2.b_mm,
                    reference_angle_deg=phi_p2k,
                    speed_ratio_to_input=r_planet,
                    orbital_radius_mm=a_orb,
                    orbital_angle_ref_deg=th_k,
                    carrier_center_mm=(0.0, 0.0, z_second),
                    carrier_speed_ratio_to_input=r_carrier,
                )
            )

    return AssemblyDescription(
        assembly_kind="planetary",
        members=members,
        carrier_speed_ratio_to_input=r_carrier,
        overall_ratio=kin.ratio,
        state=kin.state,
        applicability_evidence={"n_planets": n_planets, "a_orb_mm": a_orb, "assembly_ok": kin.assembly_ok},
        flags=list(kin.flags),
    )


def build_chain_assembly(
    stages: Sequence[Any],
    *,
    stage_gap_mm: float = 5.0,
    points_per_flank: int = 24,
    points_per_fillet: int = 12,
) -> AssemblyDescription:
    """Stack multiple stages along Z and align each stage's XY input shaft to the previous output axis."""
    from .gearbox import PairStageSpec, PlanetaryStageSpec

    all_members: List[GearPlacement] = []
    all_flags: List[str] = []
    states: List[ValidityState] = []
    z_cur = 0.0
    x_cur = 0.0
    y_cur = 0.0
    cum_ratio = 1.0

    for idx, st in enumerate(stages):
        if isinstance(st, PairStageSpec):
            sub = build_pair_assembly(
                st.gear1,
                st.gear2,
                centre_distance_mm=st.centre_distance_mm,
                z_offset_mm=z_cur,
                points_per_flank=points_per_flank,
                points_per_fillet=points_per_fillet,
                stage_index=idx,
                prefix=f"stage{idx}_{st.name}",
            )
            z_step = max(st.gear1.b_mm, st.gear2.b_mm) + stage_gap_mm
            in_mem = next(m for m in sub.members if m.role == "gear1")
            out_mem = next(m for m in sub.members if m.role == "gear2")
            dx = x_cur - in_mem.center_mm[0]
            dy = y_cur - in_mem.center_mm[1]
            next_xy = (out_mem.center_mm[0] + dx, out_mem.center_mm[1] + dy)
        elif isinstance(st, PlanetaryStageSpec):
            sub = build_planetary_assembly(
                sun=st.sun,
                planet=st.planet,
                ring=st.ring,
                n_planets=st.n_planets,
                planet2=st.planet2,
                fixed_member=st.fixed_member,
                input_member=st.input_member,
                output_member=st.output_member,
                z_offset_mm=z_cur,
                points_per_flank=points_per_flank,
                points_per_fillet=points_per_fillet,
                stage_index=idx,
                prefix=f"stage{idx}_{st.name}",
            )
            is_comp = st.planet2 is not None
            z_step = (
                max(st.sun.b_mm, st.planet.b_mm) + max(st.planet2.b_mm, st.ring.b_mm)
                if is_comp and st.planet2 is not None
                else max(st.sun.b_mm, st.planet.b_mm, st.ring.b_mm)
            ) + stage_gap_mm
            dx = x_cur
            dy = y_cur
            next_xy = (x_cur, y_cur)
        else:
            raise TypeError(f"Unsupported stage spec {type(st).__name__}")

        # Align XY shaft axes, propagate carrier center, and scale cumulative speed ratios
        for m in sub.members:
            m.center_mm = (m.center_mm[0] + dx, m.center_mm[1] + dy, m.center_mm[2])
            m.carrier_center_mm = (
                m.carrier_center_mm[0] + dx,
                m.carrier_center_mm[1] + dy,
                m.carrier_center_mm[2],
            )
            m.speed_ratio_to_input /= cum_ratio
            m.carrier_speed_ratio_to_input /= cum_ratio
            m.member_id = st.stage_id + "." + m.name.rsplit(".", 1)[-1]
            all_members.append(m)

        x_cur, y_cur = next_xy
        cum_ratio *= sub.overall_ratio
        z_cur += z_step
        all_flags.extend(sub.flags)
        states.append(sub.state)

    from .common import worst_state
    from .layout import chain_bounds
    return AssemblyDescription(
        assembly_kind="chain",
        members=all_members,
        overall_ratio=cum_ratio,
        state=worst_state(states),
        applicability_evidence={"n_stages": len(stages), "overall_ratio": cum_ratio, "bounds": chain_bounds(stages, stage_gap_mm=stage_gap_mm)},
        flags=sorted(set(all_flags)),
    )
