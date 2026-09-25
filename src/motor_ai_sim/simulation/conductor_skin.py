"""Skin-depth-driven mesh rule for the solid conductive shaft.

The shaft's eddy current lives in a skin layer δ = sqrt(2 / (ω μ σ)) under
the surface that faces the rotor iron.  On a 42CrMo4 shaft (σ 4.4 MS/m, μ_r up
to ~1000) that is 0.05-0.3 mm at the rotor-frame slot frequency — far below the
2-3 mm cells the CDT puts into a shaft wall.  An unresolved layer reads the
loss LOW (1-D P2 check, `docs/CONDUCTIVE_BODY_MESH_CONVERGENCE_2026-09-24.md`:
cells 17 δ thick return 0.48 of the exact loss; cells one δ thick +0.3 %).

This module turns the physics the solver knows (σ, the B-H curve, the rotor-
frame frequencies of the duty) into the spec the geometry-driven mesher builds
(`geo_mesh._shaft_skin_plan` / `_skin_patch`): first layer h1, growth, the
circumferential chord and the layer cap.  Pure functions; no mesh, no solve.
"""
from __future__ import annotations

import math
import os
from typing import Dict, Iterable, Optional, Sequence, Tuple

MU0 = 4e-7 * math.pi

#: first layer / δ at the reference frequency.  1-D P2: h = δ → +0.3 %,
#: h = 2δ → +5 %; graded h1 = δ with growth 1.5 → +0.3 % (docs, §rule).
SKIN_H1_FRAC = 1.0
#: geometric growth of the layers away from the surface.
SKIN_GROWTH = 1.5
#: circumferential cells per shortest spatial wavelength of the field at the
#: shaft surface, λ = 2π·r / (N_slots + p) — a PHYSICAL length (the field's
#: own period there), so a Ø40 and a Ø200 get the same cells per wavelength.
#: Measured (docs, §convergence): 8/λ reads the Ø40 shaft 20 % and the L13
#: shaft 10 % off their 32/λ values; 16/λ is within 4 % / 1 %, the L155 is
#: within 1 % at any of them.
SKIN_CELLS_PER_WAVELENGTH = 16.0
#: the layers stop growing at this many chords.  Past a few δ the current has
#: decayed; 4-chord bulk cells (aspect 4) read the Ø40 shaft within 0.3 % of
#: square ones at 3/4 of the extra unknowns.
SKIN_HMAX_CHORDS = 4.0


def skin_depth_m(f_hz: float, sigma: float, mu_r: float) -> float:
    """δ = sqrt(2 / (ω μ0 μ_r σ)) [m]; inf when any factor is not positive."""
    w = 2.0 * math.pi * float(f_hz)
    d = w * MU0 * float(mu_r) * float(sigma)
    return math.sqrt(2.0 / d) if d > 0.0 else math.inf


def bh_mu_r_max(bh_curve: Optional[Iterable[Sequence[float]]]) -> float:
    """Largest secant permeability B/(μ0 H) on a B-H curve ([H, B] pairs);
    1.0 for a missing curve.  The largest μ gives the THINNEST skin, so a
    mesh sized on it resolves every operating point of the steel."""
    best = 1.0
    for pt in (bh_curve or []):
        try:
            h, b = float(pt[0]), float(pt[1])
        except (TypeError, ValueError, IndexError):
            continue
        if h > 0.0 and b > 0.0:
            best = max(best, b / (MU0 * h))
    return best


def rotor_frame_ref_hz(num_slots: int, rpm: float,
                       f_switch_hz: Optional[float] = None) -> float:
    """Highest rotor-frame frequency the skin layer must resolve: the slot-
    passing frequency N_s·n/60 (the rotor slides past N_s slot openings per
    revolution — the permeance harmonics every rotor conductor sees), or the
    PWM carrier when the drive chops (its current ripple reaches the rotor at
    ~f_sw)."""
    f = abs(float(num_slots)) * abs(float(rpm)) / 60.0
    if f_switch_hz:
        f = max(f, abs(float(f_switch_hz)))
    return f


def _env_float(name: str, default: float) -> float:
    try:
        v = os.environ.get(name)
        return float(v) if v not in (None, "") else float(default)
    except ValueError:
        return float(default)


def skin_layer_enabled() -> bool:
    """SB_SKIN_LAYER=0 turns the rule off (diagnostic A/B); default ON."""
    return os.environ.get("SB_SKIN_LAYER", "1").strip().lower() not in (
        "0", "false", "no", "off")


def shaft_skin_spec(sigma: float, mu_r_max: float, f_ref_hz: float,
                    r_shaft_mm: float, num_slots: int, pole_pairs: int,
                    h1_frac: Optional[float] = None,
                    growth: Optional[float] = None,
                    cells_per_wavelength: Optional[float] = None,
                    ) -> Optional[Dict]:
    """The mesher's shaft skin spec, or None when there is nothing to resolve
    (non-conductive shaft, no speed).

    h1     = h1_frac · δ(f_ref, μ_r,max)            first layer at the surface
    growth = geometric growth away from it
    chord  = 2π r / (N_s + p) / cells_per_wavelength circumferential cell
    h_max  = SKIN_HMAX_CHORDS · chord                 layers stop growing there

    Every length here is a PHYSICAL one — the skin depth and the field's
    wavelength at the shaft surface — never the machine size or the global
    element size, so the same rule resolves a Ø40 and a Ø200 alike.
    depth_solid = 10 δ (at least 4 chords)            a SOLID shaft's layered skin

    Diagnostic overrides (study only): SB_SKIN_H1_FRAC, SB_SKIN_GROWTH,
    SB_SKIN_CELLS_PER_WL, SB_SKIN_CHORD_MM (absolute chord)."""
    if not (sigma > 0.0 and f_ref_hz > 0.0 and r_shaft_mm > 0.0):
        return None
    h1f = _env_float("SB_SKIN_H1_FRAC", SKIN_H1_FRAC if h1_frac is None else h1_frac)
    g = _env_float("SB_SKIN_GROWTH", SKIN_GROWTH if growth is None else growth)
    cpw = _env_float("SB_SKIN_CELLS_PER_WL", SKIN_CELLS_PER_WAVELENGTH
                     if cells_per_wavelength is None else cells_per_wavelength)
    delta_mm = skin_depth_m(f_ref_hz, sigma, max(1.0, mu_r_max)) * 1e3
    nu_max = max(1, int(num_slots) + int(pole_pairs))
    lam_mm = 2.0 * math.pi * float(r_shaft_mm) / nu_max
    chord = _env_float("SB_SKIN_CHORD_MM", lam_mm / max(cpw, 1.0))
    h1 = max(1e-3, h1f * delta_mm)
    h1 = min(h1, chord)
    h_max = chord * max(1.0, _env_float("SB_SKIN_HMAX_CHORDS", SKIN_HMAX_CHORDS))
    return {"h1_mm": h1, "growth": max(1.0, g), "chord_mm": chord,
            "h_max_mm": h_max,
            "depth_solid_mm": max(10.0 * delta_mm, 4.0 * chord),
            "delta_mm": delta_mm, "f_ref_hz": float(f_ref_hz),
            "mu_r_max": float(mu_r_max), "sigma": float(sigma)}


def sleeve_layers() -> Optional[float]:
    """SB_SLEEVE_LAYERS (study only): element layers across the retaining
    sleeve; None keeps the mesher's 2."""
    v = os.environ.get("SB_SLEEVE_LAYERS")
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None
