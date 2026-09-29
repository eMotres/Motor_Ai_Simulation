"""The machine's own battery, as a CIRCUIT rather than a nameplate.

Everywhere else in this project the battery is three numbers on a card —
chemistry, series count, v_min/v_nom/v_max — because that is all a duty check
needs: the inverter must have V_DC above the duty's line peak.  Charging asks a
different question.  When the machine is a GENERATOR feeding its own pack
through the same MOSFET bridge, the pack is a source with an INTERNAL
RESISTANCE, and every quantity the engineer wants — how many amps go in, how
many watts, at what C-rate, how far the bus rises while it happens — is that
resistance times a current.

    V_bus = V_oc + I_charge · R_pack          (charging: the bus rises)
    R_pack = NS · r_int_cell / NP

That is the whole model, and it is deliberately the whole model:

* NO state of charge.  V_oc is taken as the pack's nominal (or whatever the
  caller passes).  A real charge curve walks V_oc from v_min to v_max as the
  cells fill, which moves the modulation index and therefore the operating
  point — so a charge SESSION is a sweep of this model, not a property of it.
  ``v_oc_at_soc`` is here as the linear placeholder that sweep would use, and
  it says in its own docstring that it is a placeholder.
* NO temperature, no ageing, no charge acceptance limit beyond a flat current
  cap.  r_int on a real cell doubles between 25 °C and 0 °C and grows with
  cycle count; the number here is one figure the user is expected to replace.

DEFAULTS ARE PLACEHOLDERS AND SAY SO.  The user has not measured this pack yet
(2026-09-01: "introduce sensible defaults, clearly editable, they will correct
later"), so every default below carries its provenance in ``sources`` and the
UI prints it next to the field.  A number nobody measured must never reach a
card looking like a number somebody did.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

# ── PLACEHOLDER cell internal resistances [mΩ per cell] ──────────────────────
# Order-of-magnitude figures for a healthy ~10 Ah pouch/cylindrical cell at
# room temperature, from datasheet DC-IR ranges — NOT a measurement of this
# user's pack.  NMC 21700-class cells sit at 10-20 mΩ; LFP prismatics run
# lower per cell but need more of them in series for the same pack voltage.
_R_INT_DEFAULT_MOHM: Dict[str, float] = {
    "nmc": 12.0,
    "nca": 12.0,
    "lco": 15.0,
    "lifepo4": 8.0,
    "lfp": 8.0,
    "lto": 6.0,
}
_R_INT_FALLBACK_MOHM = 12.0

# PLACEHOLDER pack capacity [Ah].  There is no honest guess from a voltage
# spec — capacity is an independent choice — so this exists only so the C-rate
# cell has something to divide by on a pack the user has not filled in yet.
_CAPACITY_DEFAULT_AH = 10.0


def default_r_int_mohm(chemistry: Optional[str]) -> float:
    """Placeholder per-cell internal resistance for a chemistry label."""
    return _R_INT_DEFAULT_MOHM.get(
        str(chemistry or "").strip().lower(), _R_INT_FALLBACK_MOHM)


@dataclass(frozen=True)
class BatteryPack:
    """A pack as the DC link sees it: an EMF behind a resistance, with a
    capacity so a current can be quoted as a C-rate.

    ``cells`` is the SERIES count (NS) — the same field the family yaml has
    always held — and ``n_parallel`` (NP) the parallel strings, which is new
    and defaults to 1 so every stored pack reads exactly as it did.
    """

    v_oc: float                     # open-circuit pack voltage [V]
    cells: int = 1                  # NS, series cells
    n_parallel: int = 1             # NP, parallel strings
    r_int_mohm: float = _R_INT_FALLBACK_MOHM   # PER CELL [mΩ]
    capacity_ah: float = 0.0        # per string [Ah]; 0 = unknown
    i_charge_max_a: float = 0.0     # 0 = no cap declared
    chemistry: Optional[str] = None
    # Which of the numbers above nobody measured.  Rides every payload so a
    # card can grey the placeholder cells instead of quoting them straight.
    sources: Dict[str, str] = field(default_factory=dict)

    # ── pack arithmetic ──────────────────────────────────────────────────
    @property
    def r_pack_ohm(self) -> float:
        """NS·r_int/NP [Ω] — the series string's resistance, strings in
        parallel.  Wiring, fuse, connector and shunt resistance are NOT in it;
        on a 12S pack those add up to a real fraction of the cells' own, and
        the honest place to put them is this same number once measured."""
        return (max(1, int(self.cells)) * max(0.0, float(self.r_int_mohm))
                * 1e-3 / max(1, int(self.n_parallel)))

    @property
    def capacity_pack_ah(self) -> float:
        return max(0.0, float(self.capacity_ah)) * max(1, int(self.n_parallel))

    def bus_under_charge(self, i_charge_a: float) -> float:
        """Terminal voltage while ``i_charge_a`` amperes flow INTO the pack.

        Charging pushes current through R_pack the other way from a discharge,
        so the terminal sits ABOVE the open circuit — which is why a boost-mode
        run has to iterate: raising V_bus lowers the modulation index at a
        fixed applied fundamental, which changes the current, which changes
        V_bus.  Negative ``i_charge_a`` (discharging) sags it, same formula.
        """
        return float(self.v_oc) + float(i_charge_a) * self.r_pack_ohm

    def c_rate(self, i_a: float) -> Optional[float]:
        cap = self.capacity_pack_ah
        return (abs(float(i_a)) / cap) if cap > 1e-9 else None

    def r_loss_w(self, i_charge_a: float) -> float:
        """I²·R_pack — the watts the pack's own resistance eats out of the
        charge power.  Reported separately because it is NOT a machine loss and
        must not land in the motor's efficiency chain."""
        return float(i_charge_a) ** 2 * self.r_pack_ohm

    def as_dict(self) -> Dict[str, object]:
        return {
            "v_oc_V": round(float(self.v_oc), 3),
            "cells_series": int(self.cells),
            "cells_parallel": int(self.n_parallel),
            "r_int_mohm_per_cell": round(float(self.r_int_mohm), 4),
            "R_pack_ohm": round(self.r_pack_ohm, 6),
            "capacity_ah": round(self.capacity_pack_ah, 3),
            "i_charge_max_A": (round(float(self.i_charge_max_a), 3)
                               if self.i_charge_max_a > 0 else None),
            "chemistry": self.chemistry,
            "sources": dict(self.sources),
        }


def pack_from_config(batt: Optional[dict], *,
                     v_oc: Optional[float] = None) -> Optional[BatteryPack]:
    """Build a :class:`BatteryPack` from a family-yaml ``battery:`` block.

    Fills the CHARGE-side fields (r_int, capacity, current cap) with the
    placeholders above when the block predates them — which every stored pack
    does — and records that it did in ``sources``.  Returns None when there is
    no battery at all: "no battery" is a real answer and must not be faked as
    a 0 V pack.
    """
    if not batt:
        return None
    src: Dict[str, str] = {}
    chem = batt.get("chemistry")
    ns = int(batt.get("cells") or 1)
    np_ = int(batt.get("n_parallel") or 1)
    if batt.get("n_parallel") is None:
        src["n_parallel"] = "assumed 1 string"
    r_int = batt.get("r_int_mohm")
    if r_int is None:
        r_int = default_r_int_mohm(chem)
        src["r_int_mohm"] = ("placeholder for %s — not measured on this pack"
                             % (str(chem or "unknown"),))
    cap = batt.get("capacity_ah")
    if cap is None:
        cap = _CAPACITY_DEFAULT_AH
        src["capacity_ah"] = "placeholder — capacity is not derivable from a voltage spec"
    i_max = batt.get("i_charge_max_a")
    if i_max is None:
        i_max = float(cap) * max(1, np_)          # 1 C
        src["i_charge_max_a"] = "placeholder — 1 C of the (placeholder) capacity"
    if v_oc is None:
        v_oc = batt.get("v_nom")
        if v_oc is None:
            # A pack with no nominal still has a usable midpoint; say so.
            lo, hi = batt.get("v_min"), batt.get("v_max")
            if lo is not None and hi is not None:
                v_oc = 0.5 * (float(lo) + float(hi))
                src["v_oc"] = "midpoint of v_min/v_max — the pack carries no nominal"
        if v_oc is None:
            return None
        else:
            src.setdefault("v_oc", "pack nominal (no state-of-charge model)")
    return BatteryPack(
        v_oc=float(v_oc), cells=ns, n_parallel=np_, r_int_mohm=float(r_int),
        capacity_ah=float(cap), i_charge_max_a=float(i_max),
        chemistry=(str(chem) if chem else None), sources=src)


def v_oc_at_soc(batt: dict, soc: float) -> float:
    """PLACEHOLDER open-circuit voltage at a state of charge in [0, 1].

    Straight line from v_min to v_max.  A real NMC curve is flat across the
    middle 60 % and knees hard at both ends, so this is wrong exactly where a
    charge session spends most of its time — it exists so the SoC sweep has a
    shape to walk, and it must be replaced with the pack's own OCV table before
    any number it produces is quoted.  Nothing in the solve path calls it.
    """
    lo = float(batt.get("v_min") or 0.0)
    hi = float(batt.get("v_max") or 0.0)
    s = min(1.0, max(0.0, float(soc)))
    return lo + (hi - lo) * s
