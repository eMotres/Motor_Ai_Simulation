"""Bearing shrink/press fit and holder thermal mechanics.

Calculates:
1. Diametral interference vs temperature curve up to max working temperature.
2. Contact pressure p(T) using Lamé thick-walled cylinder theory (elastic hub on elastic ring).
3. Hub hoop stress, von Mises equivalent stress, and safety factor against yield.
4. Bearing outer-ring compression and internal radial clearance loss.
5. Temperature where fit is lost: dT_loss = delta_0 / (D * (alpha_hub - alpha_ring)).
6. ISO 281 bearing L10 rating life for 1 vs 2 bearings carrying axial thrust.

Conventions & Provenance:
- `offset_bearing`: diametral interference at reference assembly temperature (default 0.04 mm / 40 um).
  Can be configured as 'diametral' (default) or 'radial'.
- Aluminium 6061-T6 holder: alpha = 23.6e-6 1/K, E = 69.0 GPa, nu = 0.33, Sy = 276 MPa.
- Bearing steel (100Cr6 / 52100): alpha = 12.5e-6 1/K, E = 206.0 GPa, nu = 0.30.
- Resulting delta_alpha = 11.1e-6 1/K yields dT_loss ~ 225 K (16 mm), ~82 K (44 mm), ~50 K (72 mm).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from motor_ai_sim import bearings as brg


# ---------------------------------------------------------------------------
# Material Defaults
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HubMaterial:
    name: str = "AL6061-T6"
    alpha: float = 23.6e-6       # 1/K thermal expansion
    E_mpa: float = 69000.0       # MPa Young's modulus (69 GPa)
    nu: float = 0.33             # Poisson's ratio
    yield_mpa: float = 276.0     # MPa yield strength


@dataclass(frozen=True)
class BearingMaterial:
    name: str = "100Cr6"
    alpha: float = 12.5e-6       # 1/K thermal expansion (bearing steel)
    E_mpa: float = 206000.0      # MPa Young's modulus (206 GPa)
    nu: float = 0.30             # Poisson's ratio


# Standard ISO 281 (e, Y) interpolation table for deep-groove ball bearings
# (Fa / C0 -> e, Y)
_ISO281_FACTORS: Tuple[Tuple[float, float, float], ...] = (
    (0.014, 0.19, 2.30),
    (0.028, 0.22, 1.99),
    (0.056, 0.26, 1.71),
    (0.084, 0.28, 1.55),
    (0.110, 0.30, 1.45),
    (0.170, 0.34, 1.31),
    (0.280, 0.38, 1.15),
    (0.420, 0.42, 1.04),
    (0.560, 0.44, 1.00),
)


def interpolate_iso281_factors(fa_over_c0: float) -> Tuple[float, float]:
    """Return (e, Y) for deep groove ball bearing per ISO 281."""
    f = max(fa_over_c0, 0.0)
    if f <= _ISO281_FACTORS[0][0]:
        return _ISO281_FACTORS[0][1], _ISO281_FACTORS[0][2]
    if f >= _ISO281_FACTORS[-1][0]:
        return _ISO281_FACTORS[-1][1], _ISO281_FACTORS[-1][2]
    for i in range(len(_ISO281_FACTORS) - 1):
        f0, e0, y0 = _ISO281_FACTORS[i]
        f1, e1, y1 = _ISO281_FACTORS[i + 1]
        if f0 <= f <= f1:
            ratio = (f - f0) / (f1 - f0)
            e = e0 + ratio * (e1 - e0)
            y = y0 + ratio * (y1 - y0)
            return e, y
    return 0.30, 1.45


# ---------------------------------------------------------------------------
# Core Analytical Fit Calculations
# ---------------------------------------------------------------------------

@dataclass
class FitPoint:
    temp_c: float
    dT_k: float
    interference_mm: float
    interference_um: float
    p_contact_mpa: float
    sigma_hoop_mpa: float
    sigma_vm_mpa: float
    sf_yield: Optional[float]
    ring_compression_um: float
    clearance_loss_um: float
    is_tight: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "temp_c": round(self.temp_c, 2),
            "dT_k": round(self.dT_k, 2),
            "interference_mm": round(self.interference_mm, 5),
            "interference_um": round(self.interference_um, 2),
            "p_contact_mpa": round(self.p_contact_mpa, 2),
            "sigma_hoop_mpa": round(self.sigma_hoop_mpa, 2),
            "sigma_vm_mpa": round(self.sigma_vm_mpa, 2),
            "sf_yield": round(self.sf_yield, 2) if self.sf_yield is not None else None,
            "ring_compression_um": round(self.ring_compression_um, 2),
            "clearance_loss_um": round(self.clearance_loss_um, 2),
            "is_tight": self.is_tight,
        }


def compute_fit_loss_dT(
    bearing_od_mm: float,
    offset_bearing_mm: float = 0.04,
    offset_mode: str = "diametral",
    alpha_hub: float = 23.6e-6,
    alpha_ring: float = 12.5e-6,
) -> float:
    """Calculate the delta T [K] where the fit is lost.

    dT_loss = delta_0 / (D * (alpha_hub - alpha_ring))
    """
    d_od = float(bearing_od_mm)
    if d_od <= 0:
        raise ValueError(f"bearing_od_mm must be positive, got {bearing_od_mm}")
    delta_alpha = float(alpha_hub - alpha_ring)
    if delta_alpha <= 0:
        raise ValueError("alpha_hub must be strictly greater than alpha_ring for thermal fit loss")
    delta_0 = float(offset_bearing_mm) if offset_mode == "diametral" else 2.0 * float(offset_bearing_mm)
    return delta_0 / (d_od * delta_alpha)


def compute_contact_pressure_and_stresses(
    d_od_mm: float,
    d_bore_mm: float,
    interference_mm: float,
    hub_od_mm: Optional[float] = None,
    ring_id_mm: Optional[float] = None,
    hub_mat: HubMaterial = HubMaterial(),
    bearing_mat: BearingMaterial = BearingMaterial(),
) -> Tuple[float, float, float, float, float]:
    """Lamé thick-walled cylinder solution for contact pressure and stresses.

    Returns:
        (p_contact_mpa, sigma_hoop_hub_mpa, sigma_vm_hub_mpa, ring_compression_um, clearance_loss_um)
    """
    if interference_mm <= 0:
        return 0.0, 0.0, 0.0, 0.0, 0.0

    R = 0.5 * d_od_mm
    # Hub outer radius: default 1.5 * R or R + 3 mm
    r_o = 0.5 * float(hub_od_mm) if (hub_od_mm and hub_od_mm > d_od_mm) else max(R + 3.0, 1.5 * R)

    # Bearing outer ring inner radius: raceway shoulder / bore of outer ring
    # Typically ~ 0.5 * (d_m + D) / 2 or D/2 - 0.25*(D - d)
    d_span = d_od_mm - d_bore_mm
    r_i = 0.5 * float(ring_id_mm) if (ring_id_mm and 0 < ring_id_mm < d_od_mm) else max(R - 0.25 * d_span, 0.5 * d_bore_mm + 0.1)

    # Elastic compliance of hub (outer cylinder under internal pressure p)
    # u_hub(R) = (p * R / E_hub) * ((r_o^2 + R^2) / (r_o^2 - R^2) + nu_hub)
    hub_geo_factor = (r_o**2 + R**2) / (r_o**2 - R**2)
    c_hub = (hub_geo_factor + hub_mat.nu) / hub_mat.E_mpa

    # Elastic compliance of outer ring (inner cylinder under external pressure p)
    # u_ring(R) = (p * R / E_ring) * ((R^2 + r_i^2) / (R^2 - r_i^2) - nu_ring)
    ring_geo_factor = (R**2 + r_i**2) / (R**2 - r_i**2)
    c_ring = (ring_geo_factor - bearing_mat.nu) / bearing_mat.E_mpa

    # Total diametral interference delta = 2 * (u_hub(R) + u_ring(R)) = 2 * R * p * (c_hub + c_ring)
    # p = delta / (D * (c_hub + c_ring))
    p = interference_mm / (d_od_mm * (c_hub + c_ring))

    # Hub stresses at contact boundary (r = R):
    # sigma_hoop = p * (r_o^2 + R^2) / (r_o^2 - R^2)
    sigma_hoop = p * hub_geo_factor
    sigma_r = -p
    # von Mises equivalent stress (plane stress, sigma_z ~ 0)
    sigma_vm = math.sqrt(sigma_hoop**2 + sigma_r**2 - sigma_hoop * sigma_r)

    # Bearing outer ring outer surface radial compression: u_ring(R)
    u_ring_mm = p * R * c_ring
    ring_compression_um = 2.0 * u_ring_mm * 1000.0  # diametral

    # Bearing outer ring raceway contraction (r = r_i):
    # u_ring(r_i) = 2 * p * R^2 * r_i / (E_ring * (R^2 - r_i^2))
    u_raceway_mm = 2.0 * p * (R**2) * r_i / (bearing_mat.E_mpa * (R**2 - r_i**2))
    clearance_loss_um = 2.0 * u_raceway_mm * 1000.0  # diametral clearance reduction

    return p, sigma_hoop, sigma_vm, ring_compression_um, clearance_loss_um


def bearing_fit_curve(
    bearing_od_mm: float,
    bearing_bore_mm: float,
    offset_bearing_mm: float = 0.04,
    offset_mode: str = "diametral",
    t_assembly_c: float = 20.0,
    t_max_c: float = 150.0,
    step_c: float = 5.0,
    hub_od_mm: Optional[float] = None,
    ring_id_mm: Optional[float] = None,
    hub_mat: HubMaterial = HubMaterial(),
    bearing_mat: BearingMaterial = BearingMaterial(),
) -> Dict[str, Any]:
    """Generate the full interference, contact pressure, and stress vs temperature curve."""
    delta_0 = float(offset_bearing_mm) if offset_mode == "diametral" else 2.0 * float(offset_bearing_mm)
    d_od = float(bearing_od_mm)
    d_bore = float(bearing_bore_mm)
    delta_alpha = hub_mat.alpha - bearing_mat.alpha

    dT_loss = compute_fit_loss_dT(
        d_od, offset_bearing_mm, offset_mode, hub_mat.alpha, bearing_mat.alpha
    )
    t_loss_c = t_assembly_c + dT_loss

    # Points along temperature sweep
    points: List[FitPoint] = []
    t_curr = float(t_assembly_c)
    t_end = max(float(t_max_c), t_loss_c + 10.0)

    while t_curr <= t_end + 1e-6:
        dT = t_curr - t_assembly_c
        raw_delta = delta_0 - d_od * delta_alpha * dT
        interference = max(raw_delta, 0.0)
        is_tight = raw_delta > 0.0

        p, hoop, vm, comp_um, clear_um = compute_contact_pressure_and_stresses(
            d_od_mm=d_od,
            d_bore_mm=d_bore,
            interference_mm=interference,
            hub_od_mm=hub_od_mm,
            ring_id_mm=ring_id_mm,
            hub_mat=hub_mat,
            bearing_mat=bearing_mat,
        )

        sf = (hub_mat.yield_mpa / vm) if (vm > 1e-6 and is_tight) else None

        points.append(
            FitPoint(
                temp_c=t_curr,
                dT_k=dT,
                interference_mm=interference,
                interference_um=interference * 1000.0,
                p_contact_mpa=p,
                sigma_hoop_mpa=hoop,
                sigma_vm_mpa=vm,
                sf_yield=sf,
                ring_compression_um=comp_um,
                clearance_loss_um=clear_um,
                is_tight=is_tight,
            )
        )
        t_curr += step_c

    # Metrics at assembly (20 C)
    p_assembly, hoop_assembly, vm_assembly, comp_assembly, clear_assembly = compute_contact_pressure_and_stresses(
        d_od_mm=d_od,
        d_bore_mm=d_bore,
        interference_mm=delta_0,
        hub_od_mm=hub_od_mm,
        ring_id_mm=ring_id_mm,
        hub_mat=hub_mat,
        bearing_mat=bearing_mat,
    )
    sf_assembly = (hub_mat.yield_mpa / vm_assembly) if vm_assembly > 0 else None

    # Status at t_max_c
    dT_max = t_max_c - t_assembly_c
    raw_delta_max = delta_0 - d_od * delta_alpha * dT_max
    is_fit_tight_at_t_max = raw_delta_max > 0.0

    return {
        "bearing_od_mm": d_od,
        "bearing_bore_mm": d_bore,
        "offset_bearing_mm": offset_bearing_mm,
        "offset_mode": offset_mode,
        "delta_0_mm": delta_0,
        "t_assembly_c": t_assembly_c,
        "t_max_c": t_max_c,
        "dT_loss_k": round(dT_loss, 2),
        "t_loss_c": round(t_loss_c, 2),
        "is_fit_tight_at_t_max": is_fit_tight_at_t_max,
        "assembly": {
            "interference_um": round(delta_0 * 1000.0, 2),
            "p_contact_mpa": round(p_assembly, 2),
            "sigma_hoop_mpa": round(hoop_assembly, 2),
            "sigma_vm_mpa": round(vm_assembly, 2),
            "sf_yield": round(sf_assembly, 2) if sf_assembly is not None else None,
            "ring_compression_um": round(comp_assembly, 2),
            "clearance_loss_um": round(clear_assembly, 2),
        },
        "materials": {
            "hub": {
                "name": hub_mat.name,
                "alpha_per_k": hub_mat.alpha,
                "E_mpa": hub_mat.E_mpa,
                "yield_mpa": hub_mat.yield_mpa,
            },
            "bearing": {
                "name": bearing_mat.name,
                "alpha_per_k": bearing_mat.alpha,
                "E_mpa": bearing_mat.E_mpa,
            },
            "delta_alpha_per_k": round(delta_alpha, 8),
        },
        "curve": [pt.to_dict() for pt in points],
        "convention_note": (
            f"offset_bearing = {offset_bearing_mm} mm ({offset_mode}). "
            f"Hub alpha = {hub_mat.alpha*1e6:.1f}e-6/K ({hub_mat.name}), "
            f"Bearing steel alpha = {bearing_mat.alpha*1e6:.1f}e-6/K ({bearing_mat.name}). "
            f"Fit lost when differential thermal expansion equals initial interference."
        ),
    }


# ---------------------------------------------------------------------------
# ISO 281 Bearing Life (1 vs 2 Bearings Carrying Thrust)
# ---------------------------------------------------------------------------

def compute_l10_bearing_life(
    c_kn: float,
    c0_kn: float,
    rpm: float,
    thrust_load_n: float = 49.05,
    radial_load_n: float = 0.0,
    fit_is_tight: bool = True,
) -> Dict[str, Any]:
    """Calculate ISO 281 basic rating life (L10h) with 1 vs 2 bearings carrying thrust.

    When both bearings maintain tight fit, axial thrust is shared across 2 bearings,
    giving roughly ~8x longer life in pure thrust regimes.
    """
    n = max(abs(float(rpm)), 1.0)
    c_n = max(float(c_kn) * 1000.0, 1.0)
    c0_n = max(float(c0_kn) * 1000.0, 1.0)
    f_thrust = max(float(thrust_load_n), 0.0)
    f_r = max(float(radial_load_n), 0.0)

    # 1. Single bearing carrying the whole thrust (front bearing only)
    fa_single = f_thrust
    e_1, y_1 = interpolate_iso281_factors(fa_single / c0_n)
    if f_r > 0 and (fa_single / f_r <= e_1):
        p_single = f_r
    else:
        p_single = max(0.56 * f_r + y_1 * fa_single, fa_single, 0.1)

    l10_revs_single = (c_n / p_single) ** 3.0
    l10h_single = (1e6 / (60.0 * n)) * l10_revs_single

    # 2. Shared between 2 bearings carrying thrust
    fa_shared = 0.5 * f_thrust
    e_2, y_2 = interpolate_iso281_factors(fa_shared / c0_n)
    if f_r > 0 and (fa_shared / f_r <= e_2):
        p_shared = f_r
    else:
        p_shared = max(0.56 * f_r + y_2 * fa_shared, fa_shared, 0.1)

    l10_revs_shared = (c_n / p_shared) ** 3.0
    l10h_shared = (1e6 / (60.0 * n)) * l10_revs_shared

    ratio = l10h_shared / max(l10h_single, 1e-9)

    return {
        "C_kn": float(c_kn),
        "C0_kn": float(c0_kn),
        "rpm": n,
        "thrust_load_n": f_thrust,
        "radial_load_n": f_r,
        "fit_is_tight": fit_is_tight,
        "single_bearing": {
            "fa_n": round(fa_single, 2),
            "p_equiv_n": round(p_single, 2),
            "e": round(e_1, 3),
            "Y": round(y_1, 2),
            "l10_millions_revs": round(l10_revs_single, 2),
            "l10h_hours": round(l10h_single, 1),
        },
        "two_bearings_shared": {
            "fa_n": round(fa_shared, 2),
            "p_equiv_n": round(p_shared, 2),
            "e": round(e_2, 3),
            "Y": round(y_2, 2),
            "l10_millions_revs": round(l10_revs_shared, 2),
            "l10h_hours": round(l10h_shared, 1),
        },
        "life_ratio_shared_to_single": round(ratio, 2),
        "note": (
            f"ISO 281 basic rating life L10h at {n:,.0f} rpm with thrust {f_thrust:.1f} N. "
            f"Sharing thrust between 2 tight bearings increases life by {ratio:.1f}x."
        ),
    }


def bearing_fit_analysis(
    bearing_name: Optional[str] = None,
    bearing_od_mm: Optional[float] = None,
    bearing_bore_mm: Optional[float] = None,
    c_kn: Optional[float] = None,
    c0_kn: Optional[float] = None,
    offset_bearing_mm: float = 0.04,
    offset_mode: str = "diametral",
    t_assembly_c: float = 20.0,
    t_max_c: float = 150.0,
    rpm: float = 5000.0,
    thrust_load_n: float = 49.05,
    radial_load_n: float = 0.0,
    hub_od_mm: Optional[float] = None,
) -> Dict[str, Any]:
    """Complete analysis combining fit curve and ISO 281 L10 life."""
    card: Optional[brg.BearingCard] = None
    if bearing_name:
        try:
            card = brg.get_bearing(bearing_name)
        except Exception:
            pass

    od = float(bearing_od_mm if bearing_od_mm is not None else (card.D if card else 16.0))
    bore = float(bearing_bore_mm if bearing_bore_mm is not None else (card.d if card else 8.0))
    c_val = float(c_kn if c_kn is not None else (card.C_kn if card and card.C_kn else 1.33))
    c0_val = float(c0_kn if c0_kn is not None else (card.C0_kn if card and card.C0_kn else 0.585))

    fit = bearing_fit_curve(
        bearing_od_mm=od,
        bearing_bore_mm=bore,
        offset_bearing_mm=offset_bearing_mm,
        offset_mode=offset_mode,
        t_assembly_c=t_assembly_c,
        t_max_c=t_max_c,
        hub_od_mm=hub_od_mm,
    )

    l10 = compute_l10_bearing_life(
        c_kn=c_val,
        c0_kn=c0_val,
        rpm=rpm,
        thrust_load_n=thrust_load_n,
        radial_load_n=radial_load_n,
        fit_is_tight=fit["is_fit_tight_at_t_max"],
    )

    return {
        "bearing_name": bearing_name or (card.name if card else f"Bearing Ø{od:.0f}/Ø{bore:.0f}"),
        "bearing_card": card.to_dict() if card else None,
        "fit": fit,
        "l10_life": l10,
    }
