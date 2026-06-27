"""2-D motor sub-domain definitions (radii, winding layout, magnet polarity).

Domain map
----------
  stator_core          — outer steel ring (back-iron + teeth), σ=0 (laminated)
  air_gap              — thin air ring between stator and rotor
  rotor_core           — rotor back-iron ring, σ=0 (laminated)
  shaft                — innermost cylinder (Al)
  slot_{i}             — 24 individual slot (conductor) sub-domains, σ=σ_cu
  magnet_{i}           — 28 individual permanent magnet sectors, σ=σ_mag
  full                 — full computational disk (for boundary conditions)

Material conductivities:
  σ_cu       = 5.8e7  S/m   (copper windings — skin + proximity effects)
  σ_fe_lam   = 0.0    S/m   (laminated steel — Bertotti model instead)
  σ_mag      = 6.25e5 S/m   (NdFeB F45SH — eddy current losses in magnets)

Winding layout (24 slots / 28 poles = 14 pole-pairs, 3-phase):
  Computed via star-of-slots method.  Each slot is assigned one phase
  (A/B/C) and direction (+1/−1).  Used by the solver to set J_z per slot.

All coordinates in metres (SI).  Config uses mm → divide by 1000.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

# ── Geometry primitives ───────────────────────────────────────────────────────
# The active scikit-fem solver builds polygons via cadquery/shapely and never
# samples PINN CSG; the NVIDIA Modulus CSG path has been removed.  The numpy
# domain samplers below provide everything the live code needs.
HAS_MODULUS = False  # NVIDIA Modulus path removed


# ── Material conductivities ───────────────────────────────────────────────────
SIGMA_CU    = 5.8e7    # copper [S/m]
SIGMA_FE_LAM = 0.0     # laminated steel [S/m]  — eddy via Bertotti
SIGMA_MAG   = 6.25e5   # NdFeB (F45SH) [S/m]
SIGMA_AIR   = 0.0


# ─────────────────────────────────────────────────────────────────────────────
# 1.  Parameter dataclass
# ─────────────────────────────────────────────────────────────────────────────
_CFG_PATH = Path(__file__).parent.parent.parent.parent / "config" / "motor_config.yaml"


@dataclass
class MotorDomainParams:
    """Precomputed radii, angles, and material properties for all motor sub-domains."""

    # ── Radii [m] ─────────────────────────────────────────────────────────────
    r_stator_out: float = 0.075
    r_stator_in:  float = 0.057
    r_rotor_out:  float = 0.0565
    r_rotor_in:   float = 0.042
    r_shaft_in:   float = 0.039
    r_air_out:    float = 0.057
    r_air_in:     float = 0.0565

    # ── Topology ──────────────────────────────────────────────────────────────
    num_poles: int = 28
    num_slots: int = 24

    # ── Stack / fill ──────────────────────────────────────────────────────────
    stack_length:       float = 0.030
    magnet_fill_fraction: float = 0.9
    fill_factor:        float = 0.6   # conductor fill in slot

    # ── Slot / wire geometry [m] ──────────────────────────────────────────────
    slot_width_m:       float = 5.5e-3   # tangential slot width
    slot_height_m:      float = 14e-3    # radial slot height
    wire_width_m:       float = 5.0e-3   # wire bundle tangential width
    wire_height_m:      float = 0.6e-3   # single wire height
    num_wires_per_slot: int   = 14

    # ── Material electrical properties [S/m] ──────────────────────────────────
    sigma_cu:     float = SIGMA_CU
    sigma_fe_lam: float = SIGMA_FE_LAM
    sigma_mag:    float = SIGMA_MAG
    sigma_shaft:  float = 2.5e7    # Al6061 conductivity [S/m]


def params_from_config(cfg_path: Path = _CFG_PATH, geo_override=None) -> MotorDomainParams:
    """Load MotorDomainParams from motor_config.yaml.

    geo_override (multi-user): a partial/full geometry dict that takes precedence
    over the file's geometry, so a request can derive params from a signed-in
    user's ACTIVE design without mutating the shared global config.
    """
    import yaml

    with cfg_path.open() as f:
        cfg = yaml.safe_load(f)

    g   = {**cfg["geometry"], **geo_override} if geo_override else cfg["geometry"]
    mm  = 1e-3

    r_so = g["stator_diameter"] / 2 * mm
    r_si = r_so - g["core_thickness"] * mm - g["slot_height"] * mm
    r_ro = r_si - g["air_gap"] * mm
    r_ri = r_ro - g["magnet_height"] * mm - g["rotor_house_height"] * mm
    r_sh = r_ri - g["shaft_height"] * mm

    # Counts MUST be ints — the config can carry them as floats (the web UI
    # writes every number as a JS float, YAML round-trips, preset saves), and
    # range()/modulo on a float raises TypeError and 500s the whole solve.
    # num_poles/num_slots (the geometry's magnets/slots) are authoritative; the
    # segment product is a fallback only (stale num_seg must not override the count).
    num_slots = int(g.get("num_slots") or int(round(g["num_seg"])) * int(round(g["num_slots_per_segment"])))
    num_poles = int(g.get("num_poles") or int(round(g["num_seg"])) * int(round(g["num_poles_per_segment"])))

    slot_width_m = (
        g["wire_width"] + 2 * g["wire_spacing_x"] + 2 * g["insulation_thickness"]
    ) * mm

    return MotorDomainParams(
        r_stator_out=r_so,
        r_stator_in=r_si,
        r_rotor_out=r_ro,
        r_rotor_in=r_ri,
        r_shaft_in=r_sh,
        r_air_out=r_si,
        r_air_in=r_ro,
        num_poles=num_poles,
        num_slots=num_slots,
        stack_length=g.get("motor_length", 30) * mm,
        magnet_fill_fraction=g.get("magnet_fill_down", 0.9),
        slot_width_m=slot_width_m,
        slot_height_m=g["slot_height"] * mm,
        wire_width_m=g["wire_width"] * mm,
        wire_height_m=g["wire_height"] * mm,
        num_wires_per_slot=int(round(g["num_wires_per_slot"])),
    )


# ─────────────────────────────────────────────────────────────────────────────
# 2.  Numpy-based domain samplers
# ─────────────────────────────────────────────────────────────────────────────

class _NumpyDomain:
    """Minimal numpy sampling interface for the motor sub-domains."""

    def sample_interior(self, n: int, **kw) -> Dict[str, np.ndarray]:
        raise NotImplementedError

    def sample_boundary(self, n: int, **kw) -> Dict[str, np.ndarray]:
        return {"x": np.zeros((n, 1)), "y": np.zeros((n, 1))}


class AnnulusDomain(_NumpyDomain):
    """Full annular ring [r_in, r_out].  Uniform area sampling."""

    def __init__(self, r_out: float, r_in: float):
        self.r_out = r_out
        self.r_in  = r_in

    def sample_interior(self, n: int, **kw) -> Dict[str, np.ndarray]:
        r   = np.sqrt(np.random.uniform(self.r_in**2, self.r_out**2, n))
        phi = np.random.uniform(0, 2 * math.pi, n)
        return {"x": (r * np.cos(phi)).reshape(-1, 1),
                "y": (r * np.sin(phi)).reshape(-1, 1)}


class AnnularSectorDomain(_NumpyDomain):
    """Annular sector [r_in, r_out] × [phi_start, phi_end].

    Used for individual magnet poles.
    phi values are in radians, measured counter-clockwise from +x axis.
    """

    def __init__(self, r_out: float, r_in: float,
                 phi_start: float, phi_end: float):
        self.r_out     = r_out
        self.r_in      = r_in
        self.phi_start = phi_start
        self.phi_end   = phi_end

    def sample_interior(self, n: int, **kw) -> Dict[str, np.ndarray]:
        r   = np.sqrt(np.random.uniform(self.r_in**2, self.r_out**2, n))
        phi = np.random.uniform(self.phi_start, self.phi_end, n)
        return {"x": (r * np.cos(phi)).reshape(-1, 1),
                "y": (r * np.sin(phi)).reshape(-1, 1)}


class SlotDomain(_NumpyDomain):
    """Rectangular slot sub-domain.

    The slot is oriented radially at angle phi_center.
    Local coordinate frame:
      e_r = radial   (toward stator outer)
      e_t = tangential (perpendicular)

    Sampling: uniform in (r_offset, u_tangential) → rotated to lab frame.

    Parameters
    ----------
    r_in, r_out   : radial extent of the slot [m]
    phi_center    : angular center of the slot [rad]
    width         : tangential width of the slot [m]
    """

    def __init__(self, r_in: float, r_out: float,
                 phi_center: float, width: float):
        self.r_in       = r_in
        self.r_out      = r_out
        self.phi_center = phi_center
        self.width      = width

    def sample_interior(self, n: int, **kw) -> Dict[str, np.ndarray]:
        r_local = np.random.uniform(self.r_in, self.r_out, n)
        u_local = np.random.uniform(-self.width / 2, self.width / 2, n)
        cos_phi = math.cos(self.phi_center)
        sin_phi = math.sin(self.phi_center)
        x = r_local * cos_phi - u_local * sin_phi
        y = r_local * sin_phi + u_local * cos_phi
        return {"x": x.reshape(-1, 1), "y": y.reshape(-1, 1)}


# ─────────────────────────────────────────────────────────────────────────────
# 3.  Winding layout — star-of-slots method (24 slots / 28 poles)
# ─────────────────────────────────────────────────────────────────────────────

def _slot_phase_sector(angle: float) -> Tuple[str, int]:
    """Star-of-slots 60° belt → (phase, sign) for an electrical angle [deg]."""
    sectors = [
        ('A', +1, 330, 30),    # wraps around 0°
        ('C', -1,  30, 90),
        ('B', +1,  90, 150),
        ('A', -1, 150, 210),
        ('C', +1, 210, 270),
        ('B', -1, 270, 330),
    ]
    a = angle % 360.0
    for ph, sgn, start, end in sectors:
        if start < end:
            if start <= a < end:
                return ph, sgn
        else:                  # wraps 330→30
            if a >= start or a < end:
                return ph, sgn
    return 'A', +1             # boundary fallback


def parse_winding_layout(layout_str: str) -> List[Tuple[str, int]]:
    """Parse an explicit per-slot layout string like 'A|a|c|C|B|b|…' (one token
    per slot, UPPER=+1, lower=−1) — lets you paste a winding straight from a
    winding tool (emetor etc.).  Separators: '|', ',' or whitespace."""
    out: List[Tuple[str, int]] = []
    for tok in str(layout_str).replace(',', '|').replace(' ', '|').split('|'):
        t = tok.strip()
        if not t:
            continue
        out.append((t.upper(), +1 if t.isupper() else -1))
    return out


def build_winding_layout(num_slots: int, num_pole_pairs: int,
                         single_layer: bool = True,
                         layout_str: str = None) -> List[Tuple[str, int]]:
    """Return [(phase, direction), ...] for each slot.

    layout_str : if given AND it has exactly num_slots tokens, it is used VERBATIM
        (paste-from-winding-tool path).  Otherwise the layout is generated:

      single_layer=True  → SINGLE-LAYER concentrated winding: one coil per TWO
        slots (coils on alternate teeth).  A coil's two sides sit in adjacent
        slots as (phase,+1),(phase,−1); the phase of coil j comes from the
        slot-2j star-of-slots phasor.  Phases come in pairs (A A C C B B …) and
        each coil's sides alternate sign — matches the single-layer winding
        tools (verified vs emetor for 24s/28p: A a c C B b a A C c b B …).

      single_layer=False → DOUBLE-LAYER: one star-of-slots phasor per slot
        (++ −− sign groups).
    """
    if layout_str:
        parsed = parse_winding_layout(layout_str)
        if len(parsed) == num_slots:
            return parsed
        # wrong length → ignore and auto-generate

    alpha_e = num_pole_pairs * 360.0 / num_slots   # electrical angle step [deg]

    if single_layer:
        layout: List[Tuple[str, int]] = [('A', 1)] * num_slots
        for j in range(num_slots // 2):
            ph, sgn = _slot_phase_sector((2 * j) * alpha_e)
            layout[2 * j]     = (ph, +sgn)
            layout[2 * j + 1] = (ph, -sgn)
        if num_slots % 2:                                  # odd leftover slot
            layout[-1] = _slot_phase_sector((num_slots - 1) * alpha_e)
        return layout

    # double-layer (one phasor per slot)
    return [_slot_phase_sector((k * alpha_e) % 360.0) for k in range(num_slots)]


# Cached layout for 24s/28p (14 pole-pairs) — single-layer
_WINDING_24S_28P: List[Tuple[str, int]] = build_winding_layout(24, 14)


# ─────────────────────────────────────────────────────────────────────────────
# 4.  Main domain builder
# ─────────────────────────────────────────────────────────────────────────────

class MotorDomains2D:
    """All 2-D PINN sub-domains for a motor cross-section.

    After construction:
      self.domains        — dict[name → domain object]
      self.winding_layout — list[(phase, direction)] for slot_0 .. slot_{N-1}
      self.magnet_polarity — list[+1|−1] for magnet_0 .. magnet_{M-1}
      self.material_props — dict[domain_name → {sigma, mu_r, ...}]

    Domain names
    ------------
      stator_core, air_gap, rotor_core, shaft, full
      slot_0 .. slot_{num_slots-1}        (conductor sub-domains)
      magnet_0 .. magnet_{num_poles-1}    (PM sub-domains)
    """

    def __init__(self, p: MotorDomainParams):
        self.p = p
        self.domains:        Dict[str, object]           = {}
        self.winding_layout: List[Tuple[str, int]]       = []
        self.magnet_polarity: List[int]                  = []
        self.material_props: Dict[str, Dict]             = {}
        self._build()

    @classmethod
    def from_config(cls, cfg_path: Path = _CFG_PATH) -> "MotorDomains2D":
        return cls(params_from_config(cfg_path))

    # ── build ─────────────────────────────────────────────────────────────────
    def _build(self) -> None:
        p = self.p

        def annulus(r_out: float, r_in: float) -> object:
            return AnnulusDomain(r_out, r_in)

        # ── Bulk domains ──────────────────────────────────────────────────────
        self.domains["stator_core"] = annulus(p.r_stator_out, p.r_stator_in)
        self.domains["air_gap"]     = annulus(p.r_air_out,    p.r_air_in)
        self.domains["rotor_core"]  = annulus(p.r_rotor_in,   p.r_shaft_in)
        self.domains["shaft"]       = AnnulusDomain(p.r_shaft_in, 0.0)
        self.domains["full"]        = AnnulusDomain(p.r_stator_out, 0.0)

        self._set_props("stator_core", sigma=p.sigma_fe_lam, mu_r=5000.0)
        self._set_props("air_gap",     sigma=SIGMA_AIR,      mu_r=1.0)
        self._set_props("rotor_core",  sigma=p.sigma_fe_lam, mu_r=5000.0)
        self._set_props("shaft",       sigma=SIGMA_AIR,      mu_r=1.0)

        # ── Individual slot (conductor) sub-domains ───────────────────────────
        # Winding layout from config: layers=1 → single-layer (default), and an
        # optional explicit `layout` string (paste from a winding tool) wins.
        try:
            from motor_ai_sim.config import get_config as _gc
            _wcfg = (_gc().get("winding", {}) or {})
        except Exception:
            _wcfg = {}
        self.winding_layout = build_winding_layout(
            p.num_slots, p.num_poles // 2,
            single_layer=(int(_wcfg.get("layers", 1)) == 1),
            layout_str=(_wcfg.get("layout") or None))
        slot_pitch = 2 * math.pi / p.num_slots
        # Slot radial extent: from stator inner outward by slot_height
        r_slot_in  = p.r_stator_in
        r_slot_out = p.r_stator_in + p.slot_height_m

        for i in range(p.num_slots):
            phi_center = i * slot_pitch
            name = f"slot_{i}"
            self.domains[name] = SlotDomain(
                r_in=r_slot_in,
                r_out=r_slot_out,
                phi_center=phi_center,
                width=p.slot_width_m,
            )
            phase, direction = self.winding_layout[i]
            self._set_props(name,
                            sigma=p.sigma_cu,
                            mu_r=1.0,
                            phase=phase,
                            direction=direction)

        # ── Individual magnet sub-domains ─────────────────────────────────────
        pole_pitch   = 2 * math.pi / p.num_poles
        magnet_arc   = pole_pitch * p.magnet_fill_fraction
        half_gap     = (pole_pitch - magnet_arc) / 2

        self.magnet_polarity = []
        for i in range(p.num_poles):
            phi_start = i * pole_pitch + half_gap
            phi_end   = phi_start + magnet_arc
            polarity  = +1 if i % 2 == 0 else -1
            name = f"magnet_{i}"
            self.domains[name] = AnnularSectorDomain(
                r_out=p.r_rotor_out,
                r_in=p.r_rotor_in,
                phi_start=phi_start,
                phi_end=phi_end,
            )
            self.magnet_polarity.append(polarity)
            self._set_props(name,
                            sigma=p.sigma_mag,
                            mu_r=1.05,      # NdFeB recoil permeability
                            polarity=polarity)

        # ── Legacy "slot" / "magnet" keys (backward compat) ──────────────────
        self.domains["slot"]   = self.domains["slot_0"]
        self.domains["magnet"] = self.domains["magnet_0"]

    # ── helpers ───────────────────────────────────────────────────────────────
    def _set_props(self, name: str, **kw) -> None:
        self.material_props[name] = kw

    def __getitem__(self, key: str) -> object:
        return self.domains[key]

    def keys(self) -> List[str]:
        return list(self.domains.keys())

    def slot_domains(self) -> List[Tuple[str, object, Tuple[str, int]]]:
        """Return [(name, domain, (phase, direction)), ...] for all slots."""
        return [
            (f"slot_{i}", self.domains[f"slot_{i}"], self.winding_layout[i])
            for i in range(self.p.num_slots)
        ]

    def magnet_domains(self) -> List[Tuple[str, object, int]]:
        """Return [(name, domain, polarity), ...] for all magnets."""
        return [
            (f"magnet_{i}", self.domains[f"magnet_{i}"], self.magnet_polarity[i])
            for i in range(self.p.num_poles)
        ]

    def summary(self) -> str:
        p = self.p
        # Winding phase counts
        phase_counts: Dict[str, int] = {}
        for ph, sgn in self.winding_layout:
            key = f"+{ph}" if sgn > 0 else f"-{ph}"
            phase_counts[key] = phase_counts.get(key, 0) + 1

        lines = [
            "Motor 2D Domains",
            f"  Stator outer radius : {p.r_stator_out*1e3:.2f} mm",
            f"  Stator inner radius : {p.r_stator_in*1e3:.2f} mm",
            f"  Air-gap thickness   : {(p.r_air_out - p.r_air_in)*1e3:.2f} mm",
            f"  Rotor outer radius  : {p.r_rotor_out*1e3:.2f} mm",
            f"  Rotor inner radius  : {p.r_rotor_in*1e3:.2f} mm",
            f"  Shaft radius        : {p.r_shaft_in*1e3:.2f} mm",
            f"  Num poles           : {p.num_poles}",
            f"  Num slots           : {p.num_slots}",
            f"  Stack length        : {p.stack_length*1e3:.1f} mm",
            "",
            f"  Slot geometry       : {p.slot_width_m*1e3:.2f} mm wide × "
            f"{p.slot_height_m*1e3:.1f} mm deep, "
            f"{p.num_wires_per_slot} wires/slot",
            "",
            "  Material sigma [S/m]:",
            f"    copper (slots)    : {p.sigma_cu:.2e}",
            f"    steel  (lam.)     : {p.sigma_fe_lam:.2e}  (Bertotti model)",
            f"    NdFeB  (magnets)  : {p.sigma_mag:.2e}",
            "",
            "  Winding layout (24s/28p star-of-slots):",
        ]
        for k, v in sorted(phase_counts.items()):
            lines.append(f"    {k}: {v} slots")

        lines += [
            "",
            f"  Total domains: {len(self.domains)}",
            "  " + ", ".join(list(self.domains.keys())[:8]) + " ...",
        ]
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 5.  Current-density helper
# ─────────────────────────────────────────────────────────────────────────────

def winding_current_density(
    I_peak: float,
    n_turns: int,
    slot_area_m2: float,
    fill_factor: float = 0.6,
    phase_angle_rad: float = 0.0,
) -> Tuple[float, float]:
    """Return (J_pos, J_neg) current densities for a coil pair [A/m²]."""
    J_peak = I_peak * n_turns / (slot_area_m2 * fill_factor)
    J = J_peak * math.sin(phase_angle_rad)
    return J, -J


# ─────────────────────────────────────────────────────────────────────────────
# 6.  Quick sanity check
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    domains = MotorDomains2D.from_config()
    print(domains.summary())
    print()
    print("Winding layout (slot → phase·dir):")
    for i, (ph, sgn) in enumerate(domains.winding_layout):
        sign_str = "+" if sgn > 0 else "−"
        print(f"  slot {i:2d}: {sign_str}{ph}", end="   ")
        if (i + 1) % 6 == 0:
            print()
    print()
    print("Magnet polarity (first 6):")
    for i in range(6):
        print(f"  magnet {i}: {'N (+)' if domains.magnet_polarity[i] > 0 else 'S (−)'}")
