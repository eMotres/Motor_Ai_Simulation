"""The power-device library — one YAML card per part in ``config/devices/``.

Same spirit as ``config/materials_library.yaml`` and ``bearings_library.yaml``:
a catalogue of real parts, transcribed from the manufacturer's datasheet, that
the solver quotes and the UI browses.  Three rules it does not bend:

* **Nothing is invented.**  A parameter the datasheet does not publish is
  ``null`` in the card and ``None`` here, and whatever asks for it is told so
  by name instead of being handed a plausible number.
* **Every value carries its provenance.**  A card block has a ``source`` line
  and each point a ``basis`` — ``table`` (exact) or ``figure`` (read off a
  plotted curve, with ``tolerance_pct``).  :meth:`DeviceCard.provenance` hands
  that to the report so a client-facing number can say what it rests on.
* **One card is one PART, not one design.**  Gate resistor, gate-off voltage,
  devices in parallel, dead time and the bus are the CONTROLLER's choices and
  live in the request, never in the card.

The directory is one flat folder of ``<part>.yaml`` files, read on demand and
re-read when the file's mtime moves (the materials library's rule — a card
corrected on disk must not need a restart).
"""
from __future__ import annotations

import logging
import math
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import yaml

log = logging.getLogger(__name__)

__all__ = [
    "CardError", "DeviceCard", "devices_dir", "library", "list_devices",
    "get_device", "validate_card", "write_card", "REQUIRED_BLOCKS",
]


class CardError(ValueError):
    """A card is missing something the loss model cannot do without."""


# ---------------------------------------------------------------------------
# Where the cards live
# ---------------------------------------------------------------------------

_REPO_DIR = Path(__file__).resolve().parents[3] / "config" / "devices"

#: Overridden by tests; a moved path wins outright (the materials rule).
_DIR: Path = _REPO_DIR
_DEFAULT_DIR = _REPO_DIR


def devices_dir() -> Path:
    """The folder the cards are read from and written to.

    A ``shared/devices`` folder wins when one exists (migration Stage 2 puts
    the machine-wide libraries there); a ``_DIR`` somebody moved wins outright,
    which is a test pointing at a fixture.
    """
    if _DIR != _DEFAULT_DIR:
        return _DIR
    try:
        from motor_ai_sim.workspace import shared_root
        p = Path(str(shared_root())) / "devices"
        if p.is_dir():
            return p
    except Exception:                                    # noqa: BLE001
        pass
    return _DIR


_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_CACHE_LOCK = threading.RLock()

#: Only characters that are safe in a file name AND readable as a part number.
_PART_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{1,63}$")

#: A card without these cannot be used by :mod:`motor_ai_sim.inverter.losses`.
REQUIRED_BLOCKS = ("part", "package", "ratings", "r_ds_on", "switching",
                   "third_quadrant", "thermal")


def _read(path: Path) -> Dict[str, Any]:
    with _CACHE_LOCK:
        try:
            mtime = path.stat().st_mtime
        except OSError as exc:
            raise CardError(f"device card {path.name!r} cannot be read: {exc}")
        hit = _CACHE.get(str(path))
        if hit and hit[0] == mtime:
            return hit[1]
        with path.open("r", encoding="utf-8") as fh:
            doc = yaml.safe_load(fh) or {}
        if not isinstance(doc, dict):
            raise CardError(f"device card {path.name!r} is not a mapping")
        _CACHE[str(path)] = (mtime, doc)
        return doc


def list_devices(*, i_switch_rms_A: Optional[float] = None
                 ) -> List[Dict[str, Any]]:
    """One compact row per card — what the Controller tab's catalogue shows.

    ``i_switch_rms_A`` — the rms current ONE switch position of the chosen
    topology carries at this duty.  Given it, every row also says how many of
    that part a switch would need on current alone (``suggested_parallel``).
    """
    out: List[Dict[str, Any]] = []
    d = devices_dir()
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.yaml")):
        try:
            card = DeviceCard(_read(p), source_file=p)
        except Exception as exc:                          # noqa: BLE001
            log.warning("device card %s is unusable: %s", p.name, exc)
            out.append({"part": p.stem, "error": str(exc)})
            continue
        out.append(card.row(i_switch_rms_A=i_switch_rms_A))
    return out


def library() -> Dict[str, Any]:
    """Every card, whole, keyed by part — the catalogue endpoint's payload."""
    d = devices_dir()
    cards: Dict[str, Any] = {}
    if d.is_dir():
        for p in sorted(d.glob("*.yaml")):
            try:
                cards[p.stem] = _read(p)
            except Exception as exc:                      # noqa: BLE001
                cards[p.stem] = {"error": str(exc)}
    return {"dir": str(d), "devices": cards}


def get_device(part: str) -> "DeviceCard":
    """The card for ``part`` — raises :class:`CardError` when there is none."""
    name = str(part or "").strip()
    if not name:
        raise CardError("no device named: send the part number of a card in "
                        "config/devices/ (GET /api/controller/devices lists them)")
    p = devices_dir() / f"{name}.yaml"
    if not p.is_file():
        known = [row.get("part") for row in list_devices()]
        raise CardError(
            f"no device card {name!r} in {devices_dir()}. Known parts: "
            + (", ".join(str(k) for k in known) if known else "(none)"))
    return DeviceCard(_read(p), source_file=p)


# ---------------------------------------------------------------------------
# Validation — used by the loader AND by the "Add device" route
# ---------------------------------------------------------------------------

def validate_card(doc: Any) -> List[str]:
    """Everything wrong with ``doc``, as a list of sentences (empty = fine)."""
    bad: List[str] = []
    if not isinstance(doc, dict):
        return ["the card must be a YAML mapping"]
    for key in REQUIRED_BLOCKS:
        if doc.get(key) in (None, "", {}):
            bad.append(f"{key} is missing — it is required")
    part = str(doc.get("part") or "")
    if part and not _PART_RE.match(part):
        bad.append(f"part {part!r} must be letters, digits, dot, dash, plus or "
                   "underscore (it is the file name)")
    rat = doc.get("ratings") or {}
    if isinstance(rat, dict):
        if not _num(rat.get("v_dss_V")):
            bad.append("ratings.v_dss_V is required (blocking voltage)")
        if not _num(rat.get("t_j_max_c")):
            bad.append("ratings.t_j_max_c is required (the refusal limit)")
        if not _points_of(rat.get("i_d_continuous"), "t_case_c", "i_a"):
            bad.append("ratings.i_d_continuous needs at least one "
                       "{t_case_c, i_a} point")
    th = doc.get("thermal") or {}
    if isinstance(th, dict):
        r = th.get("r_th_jc_k_w")
        if not (isinstance(r, dict) and (_num(r.get("typ")) or _num(r.get("max")))):
            bad.append("thermal.r_th_jc_k_w needs typ or max [K/W]")
    rds = doc.get("r_ds_on") or {}
    if isinstance(rds, dict):
        curves = rds.get("curves")
        if not (isinstance(curves, list) and curves):
            bad.append("r_ds_on.curves needs at least one V_GS(on) curve")
        else:
            for c in curves:
                if not _points_of((c or {}).get("points"), "t_j_c", "r_mohm"):
                    bad.append("every r_ds_on curve needs {t_j_c, r_mohm} points")
                    break
    sw = doc.get("switching") or {}
    if isinstance(sw, dict):
        if not _num(sw.get("v_dd_ref_V")):
            bad.append("switching.v_dd_ref_V is required (the bus the energies "
                       "were measured at)")
        has_curves = isinstance(sw.get("curves"), list) and bool(sw.get("curves"))
        if not has_curves and not _has_times_and_charges(doc):
            bad.append(
                "switching needs EITHER .curves (an E_on/E_off energy table) OR "
                "enough of .times_ns + gate charges for the times-and-charges "
                "fallback: a reference r_g_ext_ref_ohm, and either "
                "gate.q_gd_nC (with gate.q_sw_nC to recover Qgs2) or "
                "switching.times_ns.t_r/t_f")
    tq = doc.get("third_quadrant") or {}
    if isinstance(tq, dict) and not (isinstance(tq.get("curves"), list)
                                     and tq.get("curves")):
        bad.append("third_quadrant.curves needs at least one I_SD(V_SD) curve "
                   "(the dead-time drop)")
    return bad


def write_card(doc: Dict[str, Any], *, overwrite: bool = False) -> Path:
    """Validate ``doc`` and write it as ``config/devices/<part>.yaml``.

    The "Add device" form's only writer.  It refuses an invalid card outright —
    a half-filled card that loads and then returns ``None`` deep inside a loss
    sum is exactly the silent-wrong-answer this project does not ship.
    """
    bad = validate_card(doc)
    if bad:
        raise CardError("; ".join(bad))
    part = str(doc["part"]).strip()
    d = devices_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{part}.yaml"
    if path.exists() and not overwrite:
        raise CardError(f"a card for {part!r} already exists "
                        f"({path}); send overwrite=true to replace it")
    tmp = path.with_suffix(".yaml.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(doc, fh, allow_unicode=True, sort_keys=False)
    tmp.replace(path)
    with _CACHE_LOCK:
        _CACHE.pop(str(path), None)
    return path


# ---------------------------------------------------------------------------
# Small numeric helpers — every one of them returns None rather than guessing
# ---------------------------------------------------------------------------

def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def _has_times_and_charges(doc: Dict[str, Any]) -> bool:
    """Whether ``doc`` carries enough for the TIMES-AND-CHARGES switching
    fallback (:meth:`DeviceCard._e_switch_from_times_charges`) — most
    low-voltage Si/SiC MOSFET datasheets publish switching TIMES
    (``t_d/t_r/t_d(off)/t_f``) and gate CHARGES (``Q_gd``, ``Q_sw``) rather
    than an E_on/E_off energy table, and a card built from one of those has
    no ``switching.curves`` at all.
    """
    sw = doc.get("switching") or {}
    if not isinstance(sw, dict) or _num(sw.get("r_g_ext_ref_ohm")) is None:
        return False
    gate = doc.get("gate") or {}
    if _num(gate.get("q_gd_nC")) is not None:
        return True
    times = sw.get("times_ns") or {}
    def _has_pts(name: str) -> bool:
        blk = times.get(name) or {}
        return isinstance(blk, dict) and any(
            k.startswith("t_j_") and _num(v) is not None for k, v in blk.items())
    return _has_pts("t_r") and _has_pts("t_f")


def _points_of(raw: Any, xk: str, yk: str) -> List[Tuple[float, float]]:
    """``[{xk: .., yk: ..}, ...]`` -> sorted ``[(x, y)]``; [] when unusable."""
    out: List[Tuple[float, float]] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        x, y = _num(item.get(xk)), _num(item.get(yk))
        if x is not None and y is not None:
            out.append((x, y))
    return sorted(out)


def _pairs(raw: Any) -> List[Tuple[float, float]]:
    """``[[x, y], ...]`` -> sorted ``[(x, y)]``."""
    out: List[Tuple[float, float]] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            x, y = _num(item[0]), _num(item[1])
            if x is not None and y is not None:
                out.append((x, y))
    return sorted(out)


def interp(points: Sequence[Tuple[float, float]], x: float) -> Optional[float]:
    """Piecewise-linear through ``points``, linearly EXTRAPOLATED outside.

    Extrapolation is deliberate and it is the honest option here: a phase
    current above the last tabulated point is a real operating condition, and
    clamping to the last value would UNDER-report the loss — the one direction
    a loss model must never err in.  The caller reports whether it extrapolated
    (:meth:`DeviceCard.e_switch`), and the route says so in the response.
    """
    pts = list(points)
    if not pts:
        return None
    if len(pts) == 1:
        return pts[0][1]
    if x <= pts[0][0]:
        (x0, y0), (x1, y1) = pts[0], pts[1]
    elif x >= pts[-1][0]:
        (x0, y0), (x1, y1) = pts[-2], pts[-1]
    else:
        for i in range(len(pts) - 1):
            if pts[i][0] <= x <= pts[i + 1][0]:
                (x0, y0), (x1, y1) = pts[i], pts[i + 1]
                break
    if x1 == x0:
        return y0
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


# ---------------------------------------------------------------------------
# The card
# ---------------------------------------------------------------------------

@dataclass
class DeviceCard:
    """One part, with the accessors the loss model needs and nothing else."""

    doc: Dict[str, Any]
    source_file: Optional[Path] = None

    def __post_init__(self) -> None:
        bad = validate_card(self.doc)
        if bad:
            raise CardError(
                f"device card {self.doc.get('part') or self.source_file}: "
                + "; ".join(bad))

    # ── identity ────────────────────────────────────────────────────────────
    @property
    def part(self) -> str:
        return str(self.doc.get("part"))

    @property
    def v_dss_V(self) -> float:
        return float((self.doc["ratings"] or {})["v_dss_V"])

    @property
    def t_j_max_c(self) -> float:
        return float((self.doc["ratings"] or {})["t_j_max_c"])

    @property
    def r_th_jc_k_w(self) -> float:
        """The junction-case resistance the model derates on.

        ``max`` when the card has one — a thermal margin quoted on the TYPICAL
        package would be a margin no production unit is guaranteed to have.
        """
        r = (self.doc["thermal"] or {}).get("r_th_jc_k_w") or {}
        return float(_num(r.get("max")) or _num(r.get("typ")))

    @property
    def r_th_jc_basis(self) -> str:
        r = (self.doc["thermal"] or {}).get("r_th_jc_k_w") or {}
        return "max" if _num(r.get("max")) is not None else "typ"

    @property
    def r_th_ja_k_w(self) -> Optional[float]:
        """Junction-to-ambient resistance, when the card publishes one —
        almost always a BOARD number (a stated PCB copper area/layer count,
        vertical-in-still-air test condition), not a package property, so
        the loss model never puts it inside its own R_th(j-c)+R_TIM+
        R_spread+R_film chain.  It exists only as a CITED still-air
        cross-check for the controller's ``air_forced``/``air_still``
        cooling modes (``inverter.losses.solve_controller``) — reported
        alongside the model's own number and named as the datasheet's, never
        silently substituted for it.  ``None`` when the card does not
        publish one (most higher-current SiC modules, which are always
        cooled through a case, not free air).
        """
        r = (self.doc["thermal"] or {}).get("r_th_ja_k_w") or {}
        if not isinstance(r, dict):
            return None
        return _num(r.get("max")) if _num(r.get("max")) is not None else _num(r.get("typ"))

    @property
    def r_th_ja_note(self) -> Optional[str]:
        return (self.doc["thermal"] or {}).get("r_th_ja_note")

    def t_j_curve_max_c(self) -> float:
        """The hottest junction temperature the card actually TABULATES.

        Every characteristic here is a piecewise-linear table, and past the
        last point :func:`interp` extrapolates — which is right for a current
        a few per cent outside the curve and nonsense for a temperature a
        thousand degrees outside it.  The solver clamps its look-ups to this
        value and says so; the T_j it REPORTS is the unclamped one, so an
        impossible design is still visibly impossible.
        """
        hottest = self.t_j_max_c
        for c in (self.doc["r_ds_on"] or {}).get("curves") or []:
            pts = _points_of((c or {}).get("points"), "t_j_c", "r_mohm")
            if pts:
                hottest = max(hottest, pts[-1][0])
        return hottest

    def i_d_continuous(self, t_case_c: float) -> Optional[float]:
        """I_DDC at this case temperature, interpolated between the card's."""
        pts = _points_of((self.doc["ratings"] or {}).get("i_d_continuous"),
                         "t_case_c", "i_a")
        return interp(pts, float(t_case_c)) if pts else None

    def i_d_rating(self, t_case_c: float) -> Dict[str, Any]:
        """``{i_a, basis, source}`` — the continuous rating to judge against.

        INSIDE the card's tabulated span the published points answer, linearly
        interpolated; that is the datasheet, and nothing else is allowed to
        override it.  OUTSIDE it the straight line is nonsense — extrapolated
        it would still promise 171 A at the junction limit itself — so the
        datasheet's OWN limiting mechanism is used instead.  Table 2 states
        that I_DDC is "limited by T_vj(max)" through R_th(j-c,max), which is

            I_D = sqrt( (T_j,max - T_c) / (R_th(j-c,max) * R_DS(on)@T_j,max) )

        and it is not a guess: on this card it gives 410 A at 25 degC against a
        published 403, and 290 A at 100 degC against a published 287 — both
        within 2 %.  It is capped at the coldest tabulated value, because below
        that the bond wire limits the current and not the junction (the flat
        top of the I_D = f(T_c) figure).  Which branch answered is reported.
        """
        pts = _points_of((self.doc["ratings"] or {}).get("i_d_continuous"),
                         "t_case_c", "i_a")
        if not pts:
            return {"i_a": None, "basis": "the card publishes no I_D rating",
                    "source": None}
        t = float(t_case_c)
        src = ((self.doc["ratings"] or {}).get("source") or "").strip()
        if pts[0][0] <= t <= pts[-1][0]:
            return {"i_a": interp(pts, t),
                    "basis": (f"the card's published I_D(T_c) points, "
                              f"interpolated at {t:.0f} degC"),
                    "source": src}
        r_hot = self.r_ds_on_ohm(self.t_j_max_c, 18.0)
        r_th = self.r_th_jc_k_w
        head = self.t_j_max_c - t
        if head <= 0 or r_hot <= 0 or r_th <= 0:
            return {"i_a": 0.0,
                    "basis": (f"the case is at or above T_j,max "
                              f"({self.t_j_max_c:.0f} degC) — no current at all"),
                    "source": src}
        i = math.sqrt(head / (r_th * r_hot))
        cap = pts[0][1] if t < pts[0][0] else None
        if cap is not None and i > cap:
            return {"i_a": cap,
                    "basis": (f"below the card's coldest published point the "
                              f"bond wire limits, not the junction: held at "
                              f"{cap:.0f} A"),
                    "source": src}
        return {"i_a": i,
                "basis": (f"outside the card's published span: "
                          f"sqrt((T_j,max - T_c)/(R_th(j-c,max) * "
                          f"R_DS(on)@T_j,max)) at T_c = {t:.0f} degC"),
                "source": src}

    def unit_price(self) -> Dict[str, Any]:
        """``{amount, currency, quantity, source, dated}`` — all optional.

        A price is a QUOTATION and never a datasheet value, so it is null
        unless somebody put it on the card with where and when it came from.
        The Compare table multiplies it by the device count as a cost PROXY,
        which is what it is called there.
        """
        p = self.doc.get("price")
        p = p if isinstance(p, dict) else {}
        return {"amount": _num(p.get("amount")),
                "currency": p.get("currency") or "EUR",
                "quantity": _num(p.get("quantity")),
                "source": p.get("source"), "dated": p.get("dated")}

    @property
    def i_d_pulsed_A(self) -> Optional[float]:
        return _num((self.doc["ratings"] or {}).get("i_d_pulsed_A"))

    @property
    def i_sm_A(self) -> Optional[float]:
        return _num((self.doc["third_quadrant"] or {}).get("i_sm_A"))

    def v_gs_static_window(self) -> Tuple[Optional[float], Optional[float]]:
        """The static V_GS window, from wherever the card keeps it.

        Infineon publishes it in the MAXIMUM RATED VALUES table, so the card
        carries it under ``ratings``; another manufacturer's card may put it
        with the rest of the gate data.  Both are read — a limit that exists
        and is not found would be reported as "not judged", which is the one
        outcome a limit table must not produce by accident.
        """
        for blk in ("ratings", "gate"):
            w = (self.doc.get(blk) or {}).get("v_gs_static_V")
            if isinstance(w, dict):
                lo, hi = _num(w.get("min")), _num(w.get("max"))
                if lo is not None and hi is not None:
                    return lo, hi
        return None, None

    def gate_window(self) -> Dict[str, Any]:
        return dict((self.doc.get("gate") or {}))

    # ── R_DS(on) ────────────────────────────────────────────────────────────
    def r_ds_on_ohm(self, t_j_c: float, v_gs_on_V: float) -> float:
        """On-state resistance [ohm] at this junction temperature.

        The card carries one curve per gate-on voltage; the CLOSEST published
        V_GS(on) is used and the choice is reported (:meth:`r_ds_on_note`) —
        interpolating between two gate voltages would invent a curve the
        datasheet does not publish.
        """
        curve = self._r_curve(v_gs_on_V)
        pts = _points_of(curve.get("points"), "t_j_c", "r_mohm")
        val = interp(pts, float(t_j_c))
        if val is None:
            raise CardError(f"{self.part}: r_ds_on has no usable points")
        return max(float(val), 0.0) * 1e-3

    def _r_curve(self, v_gs_on_V: float) -> Dict[str, Any]:
        curves = [c for c in (self.doc["r_ds_on"] or {}).get("curves") or []
                  if isinstance(c, dict)]
        if not curves:
            raise CardError(f"{self.part}: r_ds_on.curves is empty")
        want = _num(v_gs_on_V)
        if want is None:
            return curves[0]
        return min(curves, key=lambda c: abs((_num(c.get("v_gs_on_V")) or 0.0) - want))

    def r_ds_on_note(self, v_gs_on_V: float) -> str:
        c = self._r_curve(v_gs_on_V)
        have = _num(c.get("v_gs_on_V"))
        if have is not None and abs(have - float(v_gs_on_V)) > 1e-9:
            return (f"R_DS(on) taken from the V_GS(on) = {have:g} V curve "
                    f"(the card publishes no {float(v_gs_on_V):g} V curve)")
        return f"R_DS(on) from the V_GS(on) = {float(v_gs_on_V):g} V curve"

    # ── switching energies ──────────────────────────────────────────────────
    def _sw_sets(self, v_gs_off_V: float) -> List[Dict[str, Any]]:
        sets = [c for c in (self.doc["switching"] or {}).get("curves") or []
                if isinstance(c, dict)]
        if not sets:
            raise CardError(f"{self.part}: switching.curves is empty")
        want = _num(v_gs_off_V) or 0.0
        best = min(sets, key=lambda c: abs((_num(c.get("v_gs_off_V")) or 0.0) - want))
        keep = _num(best.get("v_gs_off_V")) or 0.0
        return [c for c in sets
                if abs((_num(c.get("v_gs_off_V")) or 0.0) - keep) < 1e-9]

    def _energy_points(self, block: Any,
                       name: str) -> List[Tuple[float, float]]:
        """The E(I_D) points of one energy block, its ``shape_from`` resolved.

        ``name`` is which energy this is (``e_on_uJ`` / ``e_off_uJ`` /
        ``e_fr_uJ``).  A 25 degC block usually holds ONE table point; the card
        says whose SHAPE to borrow, and the borrowed curve is scaled so it
        passes through that one exact point.  That keeps the temperature
        dependence exact where the datasheet publishes it and takes the current
        dependence, openly, from the curve that does exist.
        """
        if not isinstance(block, dict):
            return []
        pts = _pairs(block.get("points"))
        shape = block.get("shape_from")
        if not (isinstance(shape, dict) and pts):
            return pts
        anchor_i = _num(block.get("anchor_i_d_A")) or pts[0][0]
        anchor_e = interp(pts, anchor_i)
        donor = None
        for s in (self.doc["switching"] or {}).get("curves") or []:
            if not isinstance(s, dict):
                continue
            if (_num(s.get("v_gs_off_V")) == _num(shape.get("v_gs_off_V"))
                    and _num(s.get("t_j_c")) == _num(shape.get("t_j_c"))):
                donor = s
                break
        if donor is None:
            return pts
        donor_pts = _pairs((self._block_of(donor, name) or {}).get("points"))
        donor_at = interp(donor_pts, anchor_i) if donor_pts else None
        if not donor_pts or not donor_at or donor_at <= 0 or anchor_e is None:
            return pts
        k = anchor_e / donor_at
        return [(x, y * k) for x, y in donor_pts]

    @staticmethod
    def _block_of(s: Dict[str, Any], name: str) -> Any:
        b = s.get(name)
        return b if isinstance(b, dict) else None

    def switching_energy_source(self) -> str:
        """``"curves"`` or ``"times_and_charges"`` — which method
        :meth:`e_switch` uses for THIS card.  ``"curves"`` when the datasheet
        publishes an E_on/E_off energy table (the SiC power modules this
        project started with); ``"times_and_charges"`` when it does not — most
        low-voltage Si/SiC MOSFET datasheets publish switching TIMES and gate
        CHARGES instead, and owner 2026-09-22: *"конечно, нужен честный
        пересчёт для любых MOSFET"* — a card without an energy table gets an
        honest ESTIMATE from what it DOES publish, not a null loss.
        """
        sw = self.doc.get("switching") or {}
        if isinstance(sw.get("curves"), list) and sw.get("curves"):
            return "curves"
        return "times_and_charges"

    def e_switch(self, *, i_d_A: float, t_j_c: float, v_dc_V: float,
                 v_gs_off_V: float = 0.0,
                 r_g_ext_ohm: Optional[float] = None,
                 v_gs_on_V: float = 18.0) -> Dict[str, Any]:
        """``{e_on_J, e_off_J, e_fr_J, extrapolated, notes}`` at one point.

        Dispatches on :meth:`switching_energy_source`.  ``v_gs_on_V`` is only
        used by the times-and-charges path (the gate-drive current needs
        BOTH rail voltages); the curves path ignores it, as before.
        """
        if self.switching_energy_source() == "curves":
            return self._e_switch_from_curves(
                i_d_A=i_d_A, t_j_c=t_j_c, v_dc_V=v_dc_V,
                v_gs_off_V=v_gs_off_V, r_g_ext_ohm=r_g_ext_ohm)
        return self._e_switch_from_times_charges(
            i_d_A=i_d_A, t_j_c=t_j_c, v_dc_V=v_dc_V, v_gs_off_V=v_gs_off_V,
            v_gs_on_V=v_gs_on_V, r_g_ext_ohm=r_g_ext_ohm)

    def _e_switch_from_curves(self, *, i_d_A: float, t_j_c: float, v_dc_V: float,
                              v_gs_off_V: float = 0.0,
                              r_g_ext_ohm: Optional[float] = None) -> Dict[str, Any]:
        """``{e_on_J, e_off_J, e_fr_J, extrapolated, notes}`` at one point.

        Order of operations, and every step is a stated rule of the card:
        current interpolation on each temperature's own curve, then a linear
        blend between the two temperatures, then the voltage law, then the
        gate-resistance law.
        """
        sets = self._sw_sets(v_gs_off_V)
        sw = self.doc["switching"] or {}
        scaling = sw.get("scaling") or {}
        out: Dict[str, float] = {}
        above = below = False
        i = max(float(i_d_A), 0.0)
        for name, key in (("e_on_uJ", "e_on_J"), ("e_off_uJ", "e_off_J"),
                          ("e_fr_uJ", "e_fr_J")):
            by_t: List[Tuple[float, float]] = []
            for s in sets:
                blk = self._block_of(s, name)
                if blk is None:
                    continue
                pts = self._energy_points(blk, name)
                if not pts:
                    continue
                above = above or i > pts[-1][0]
                below = below or i < pts[0][0]
                e = interp(pts, i)
                t = _num(s.get("t_j_c"))
                if e is not None and t is not None:
                    by_t.append((t, max(e, 0.0)))
            if not by_t:
                out[key] = 0.0
                continue
            e_uj = interp(sorted(by_t), float(t_j_c)) or 0.0
            out[key] = max(float(e_uj), 0.0) * 1e-6
        # Voltage
        v_ref = float(sw.get("v_dd_ref_V"))
        n = float(_num(scaling.get("voltage_exponent")) or 1.0)
        kv = (max(float(v_dc_V), 0.0) / v_ref) ** n if v_ref > 0 else 1.0
        for key in ("e_on_J", "e_off_J", "e_fr_J"):
            out[key] *= kv
        # Gate resistance — E_on/E_off only, per the card's `r_g_rule`.
        r_ref = _num(sw.get("r_g_ext_ref_ohm"))
        kr = 1.0
        if r_g_ext_ohm is not None and r_ref and r_ref > 0:
            kr = max(float(r_g_ext_ohm), 0.0) / r_ref
            out["e_on_J"] *= kr
            out["e_off_J"] *= kr
        notes = [
            f"E(I_D) from the card's {float(t_j_c):.0f} degC-interpolated curves "
            f"at V_GS(off) = {float(v_gs_off_V):g} V",
            f"bus scaling (V_dc/{v_ref:g} V)^{n:g} = {kv:.4f} "
            f"[{scaling.get('voltage_rule') or 'card rule'}]",
        ]
        if kr != 1.0:
            notes.append(f"gate-resistance scaling R_G,ext/{r_ref:g} ohm = {kr:.3f} "
                         "(E_on and E_off only; E_fr held at the datasheet R_G)")
        if above:
            notes.append(f"{i:.0f} A is ABOVE the card's tabulated current range "
                         "— linearly extrapolated")
        # Extrapolating DOWN to zero current is a different thing and not worth
        # a warning: every curve here stops at the datasheet's lowest measured
        # point (25 A for this family) and the straight line through the two
        # lowest points is what a sinusoidal current spends its zero crossings
        # on.  It is stated, once, as a model note.
        out["extrapolated"] = above                 # type: ignore[assignment]
        out["extrapolated_below"] = below           # type: ignore[assignment]
        out["notes"] = notes                        # type: ignore[assignment]
        out["switching_energy_source"] = "curves"    # type: ignore[assignment]
        return out

    def _e_switch_from_times_charges(self, *, i_d_A: float, t_j_c: float,
                                     v_dc_V: float, v_gs_off_V: float,
                                     v_gs_on_V: float,
                                     r_g_ext_ohm: Optional[float]) -> Dict[str, Any]:
        """First-order OVERLAP-MODEL switching energy, for a card whose
        datasheet publishes switching TIMES and gate CHARGES instead of an
        E_on/E_off energy table (most low-voltage Si/SiC MOSFET datasheets —
        only the higher-current SiC modules this project started with publish
        the energy curves directly).

        The classic hard-switching overlap estimate, as stated in every major
        vendor's own MOSFET switching-loss application note::

            E_on  = 0.5 * V_dc * I_d * (t_ri + t_fv)
            E_off = 0.5 * V_dc * I_d * (t_rv + t_fi)

        with the voltage transition set by the MILLER (gate-drain) charge and
        the current transition by the charge ABOVE THRESHOLD to the plateau
        (``Qgs2``), both driven by a CONSTANT gate current — the textbook
        simplification, and it is the reason this model under-reads (see
        below)::

            t_fv = Q_gd  / I_g(on)     t_ri = Qgs2 / I_g(on)
            t_rv = Q_gd  / I_g(off)    t_fi = Qgs2 / I_g(off)
            I_g  = (V_gs,on - V_gs,off) / (R_g,int + R_g,ext)

        ``Qgs2`` is not a field this project's cards carry directly.  Where
        the card publishes the industry's own SWITCHING charge ``Q_sw``
        (``Qgs2 + Qgd``, e.g. this part's datasheet Table 6) it is recovered
        as ``Q_sw - Q_gd``.  Where the card has neither a usable ``Q_gd`` nor
        ``Q_sw``, the datasheet's OWN measured ``t_r``/``t_f`` are used
        instead, closest-temperature and LINEARLY SCALED by the gate
        resistance ratio — the same physical reasoning the curves path's
        ``r_g_rule`` states for E_on/E_off: a resistive gate charge/discharge
        time scales with R_g to first order.

        Body-diode reverse recovery (``e_fr_J``) is estimated as
        ``0.5 * Q_rr * V_dc`` when the card publishes ``Q_rr`` — the standard
        first-order recovery-energy estimate — and reported as an explicit
        zero (which UNDER-reads) when it does not.

        WHAT THIS IS NOT: a SPICE switching-loss simulation.  It has no stray
        inductance, no parasitic ringing, and a Miller charge treated as
        constant rather than the real V_DS-dependent nonlinear C_rss.  On the
        one card this project can check BOTH ways — :data:`IMCQ120R004M2H`,
        whose datasheet publishes an E_on/E_off table AND the times/charges
        this method needs — the two branches read very differently at the
        L155 operating point (185.2 A, 800 V, 175 degC; see
        ``tests/test_controller.py::test_times_charges_fallback_against_the_known_curves``
        for the numbers, on the owner's instruction NOT tuned to 1):

        * the CHARGE branch (Q_gd and a Qgs2 proxy) under-reads the measured
          E_on/E_off by roughly a THIRD — the honest number for the branch
          this card (IQE050N08NM5SC) actually uses, since it publishes real
          Q_gd/Q_sw;
        * the TIMES branch (no usable charge pair) reproduces the measured
          curve within ~2 % AT THE DATASHEET'S OWN R_g — expected, since it
          is just recombining the manufacturer's own measured t_r/t_f rather
          than re-deriving them; its accuracy at a DIFFERENT R_g rests on the
          stated linear-scaling assumption alone and is unverified here.

        Treat every number from this method as a LOWER BOUND, never a
        measurement — the notes say so on every call.
        """
        sw = self.doc.get("switching") or {}
        gate = self.doc.get("gate") or {}
        r_g_ref = _num(sw.get("r_g_ext_ref_ohm"))
        r_g_int = _num(gate.get("r_g_internal_ohm")) or 0.0
        r_g_ext = _num(r_g_ext_ohm) if r_g_ext_ohm is not None else r_g_ref
        if r_g_ext is None:
            raise CardError(
                f"{self.part}: no R_g,ext given and the card publishes no "
                "reference gate resistance (switching.r_g_ext_ref_ohm) for "
                "the times-and-charges fallback")
        r_g_total = max(r_g_int + float(r_g_ext), 1e-9)

        d_v = max(float(v_gs_on_V) - float(v_gs_off_V), 1e-9)
        i_g = d_v / r_g_total                # constant gate-drive current, stated

        q_gd = _num(gate.get("q_gd_nC"))
        q_sw = _num(gate.get("q_sw_nC"))
        q_gs2 = (q_sw - q_gd) if (q_sw is not None and q_gd is not None
                                  and q_sw > q_gd) else None

        notes: List[str] = []
        if q_gd is not None and q_gs2 is not None:
            t_fv = t_rv = (q_gd * 1e-9) / i_g
            t_ri = t_fi = (q_gs2 * 1e-9) / i_g
            notes.append(
                f"switching times from gate charge: Q_gd={q_gd:g} nC, "
                f"Qgs2=Q_sw-Q_gd={q_gs2:g} nC, I_g=(V_gs,on-V_gs,off)/"
                f"(R_g,int+R_g,ext)={i_g * 1e3:.1f} mA at R_g,total="
                f"{r_g_total:.2f} ohm (V_gs,on={float(v_gs_on_V):g} V, "
                f"V_gs,off={float(v_gs_off_V):g} V)")
        else:
            times = sw.get("times_ns") or {}

            def _pt(name: str) -> Optional[float]:
                blk = times.get(name) or {}
                vals = [(k, _num(v)) for k, v in blk.items() if k.startswith("t_j_")]
                vals = [(k, v) for k, v in vals if v is not None]
                if not vals:
                    return None
                def _tk(k: str) -> float:
                    try:
                        return float(k.split("t_j_")[1])
                    except (IndexError, ValueError):
                        return 1e9
                vals.sort(key=lambda kv: abs(_tk(kv[0]) - float(t_j_c)))
                return vals[0][1]

            t_r0, t_f0 = _pt("t_r"), _pt("t_f")
            if t_r0 is None or t_f0 is None or r_g_ref is None:
                raise CardError(
                    f"{self.part}: the card publishes neither a usable "
                    "gate-charge pair (gate.q_gd_nC with gate.q_sw_nC) nor "
                    "switching times (switching.times_ns.t_r/t_f) with a "
                    "reference R_g — no honest switching-energy estimate is "
                    "possible")
            # The published t_r/t_f were measured with R_g,ext = r_g_ref AND
            # the SAME internal R_g this card states — so the total the
            # datasheet's own numbers correspond to is (r_g_int + r_g_ref),
            # not r_g_ref alone.  Scaling by r_g_total / (r_g_int + r_g_ref)
            # is what makes k = 1 when the request matches the datasheet's
            # own test condition exactly, instead of over-reading by
            # (r_g_int + r_g_ref) / r_g_ref every time.
            k = r_g_total / max(r_g_int + float(r_g_ref), 1e-9)
            t_fv = t_rv = t_r0 * 1e-9 * k
            t_ri = t_fi = t_f0 * 1e-9 * k
            notes.append(
                f"no usable gate charge on this card — switching times taken "
                f"from the datasheet's own t_r={t_r0:g} ns / t_f={t_f0:g} ns "
                f"(closest tabulated T_j to {float(t_j_c):.0f} degC) at "
                f"R_g,ext={r_g_ref:g} ohm (R_g,total,datasheet="
                f"{r_g_int + float(r_g_ref):.2f} ohm), linearly scaled by "
                f"R_g,total/{r_g_int + float(r_g_ref):.2f} ohm = {k:.3f}")

        v = max(float(v_dc_V), 0.0)
        i = max(float(i_d_A), 0.0)
        e_on = 0.5 * v * i * (t_ri + t_fv)
        e_off = 0.5 * v * i * (t_rv + t_fi)

        tq = self.doc.get("third_quadrant") or {}
        rr = tq.get("reverse_recovery") or {}
        q_rr = _num(rr.get("q_rr_nC_typ")) if isinstance(rr, dict) else None
        v_rr_ref = _num(rr.get("v_r_ref_V")) if isinstance(rr, dict) else None
        e_fr = 0.0
        if q_rr is not None:
            v_scale = v if not v_rr_ref else v          # V_dc IS the reverse bias here
            e_fr = 0.5 * (q_rr * 1e-9) * v_scale
            notes.append(
                f"E_fr (body-diode reverse recovery) = 0.5 * Q_rr * V_dc, "
                f"Q_rr={q_rr:g} nC typ"
                + (f" (V_R={v_rr_ref:g} V test condition on the card, NOT "
                   f"this working di/dt — a further first-order approximation)"
                   if v_rr_ref else ""))
        else:
            notes.append(
                "no Q_rr published on this card — E_fr (body-diode reverse "
                "recovery) is reported as zero, which UNDER-reads")

        notes.insert(0,
            f"switching energy source: TIMES-AND-CHARGES fallback for "
            f"{self.part} — this card's datasheet publishes no E_on/E_off "
            "table (owner 2026-09-22: every MOSFET gets an honest recalc, "
            "never a silent zero)")
        notes.append(
            "first-order overlap-model ESTIMATE, not a measured curve — "
            "treat as a LOWER BOUND (constant gate current, linear Miller "
            "charge, no stray inductance or ringing)")

        return {
            "e_on_J": max(e_on, 0.0), "e_off_J": max(e_off, 0.0),
            "e_fr_J": max(e_fr, 0.0),
            "extrapolated": False, "extrapolated_below": False,
            "notes": notes, "switching_energy_source": "times_and_charges",
        }

    def e_oss_J(self, v_dc_V: float) -> Optional[float]:
        """E_oss at this bus from C_o(er) — AN2025-10 eq. (11), rearranged."""
        cap = self.doc.get("capacitance") or {}
        c_er = _num(cap.get("c_o_er_pF"))
        if c_er is None:
            e = _num(cap.get("e_oss_uJ"))
            v_at = _num(cap.get("at_v_ds_V"))
            if e is None or not v_at:
                return None
            return e * 1e-6 * (float(v_dc_V) / v_at) ** 2
        return 0.5 * c_er * 1e-12 * float(v_dc_V) ** 2

    # ── third quadrant ──────────────────────────────────────────────────────
    def v_sd_V(self, i_sd_A: float, t_j_c: float,
               v_gs_off_V: float = 0.0) -> float:
        """Body-diode drop at this reverse current — the dead-time cost.

        The card publishes I_SD = f(V_SD); this inverts it.  At 175 degC and
        185 A the card's anchor is the exact Table 6 value.

        Curve choice: CLOSEST published ``v_gs_off_V`` first (as before — a
        different gate-off voltage is a genuinely different device curve, not
        an interpolation), and among ties on that, closest ``t_j_c`` — a card
        with more than one third-quadrant temperature (e.g. IQE050N08NM5SC's
        25/175 degC pair) must not silently answer with whichever curve
        happens to sort first.
        """
        curves = [c for c in (self.doc["third_quadrant"] or {}).get("curves") or []
                  if isinstance(c, dict)]
        if not curves:
            raise CardError(f"{self.part}: third_quadrant.curves is empty")
        want_v = _num(v_gs_off_V) or 0.0
        d_v = {id(c): abs((_num(c.get("v_gs_off_V")) or 0.0) - want_v) for c in curves}
        best_dv = min(d_v.values())
        tied = [c for c in curves if d_v[id(c)] <= best_dv + 1e-9]
        want_t = _num(t_j_c)
        if want_t is None or len(tied) == 1:
            c = tied[0]
        else:
            c = min(tied, key=lambda x: abs(
                (_num(x.get("t_j_c")) if _num(x.get("t_j_c")) is not None else 1e9)
                - want_t))
        pts = _pairs(c.get("points"))               # [(V_SD, I_SD)]
        if not pts:
            raise CardError(f"{self.part}: third_quadrant curve has no points")
        inverse = sorted((i, v) for v, i in pts)
        v = interp(inverse, max(float(i_sd_A), 0.0))
        return max(float(v or 0.0), 0.0)

    # ── what the report has to be able to say ───────────────────────────────
    def provenance(self) -> Dict[str, Any]:
        """Which block came from where — straight out of the card."""
        out: Dict[str, Any] = {
            "datasheet": self.doc.get("datasheet_url"),
            "datasheet_revision": self.doc.get("datasheet_revision"),
            "application_note": self.doc.get("application_note_url"),
        }
        for blk in ("ratings", "r_ds_on", "switching", "third_quadrant",
                    "thermal", "capacitance", "gate"):
            src = (self.doc.get(blk) or {})
            if isinstance(src, dict) and src.get("source"):
                out[blk] = str(src["source"]).strip()
        return out

    def package_size(self) -> Dict[str, Optional[float]]:
        """``{length_mm, width_mm, height_mm}`` — ``None`` where unpublished."""
        s = self.doc.get("package_size_mm")
        s = s if isinstance(s, dict) else {}
        return {k: _num(s.get(k)) for k in
                ("length_mm", "width_mm", "height_mm")}

    def package_outline_svg(self, box: int = 64) -> str:
        """A generated line drawing of this package — never a vendor image."""
        from motor_ai_sim.inverter.packages import outline_svg
        return outline_svg(self.doc, box=box)

    def suggested_parallel(self, i_switch_rms_A: float,
                           t_case_c: float = 100.0) -> Optional[int]:
        """How many of THIS part one switch position needs, by current alone.

        ``ceil(I_switch_rms / I_DDC(T_case))`` — the datasheet's own continuous
        rating and nothing else.  It is a FIRST CUT and the response says so:
        the binding limit is almost always the junction temperature, which only
        the solve knows, and on a 24 kHz carrier the switching loss usually
        asks for more devices than the current rating does.
        """
        rating = self.i_d_continuous(float(t_case_c))
        if not rating or rating <= 0 or i_switch_rms_A <= 0:
            return None
        return int(math.ceil(float(i_switch_rms_A) / float(rating)))

    def row(self, *, i_switch_rms_A: Optional[float] = None) -> Dict[str, Any]:
        """The catalogue row the Controller tab's table renders."""
        rat = self.doc.get("ratings") or {}
        th = self.doc.get("thermal") or {}
        r25 = r175 = None
        try:
            r25 = round(self.r_ds_on_ohm(25.0, 18.0) * 1e3, 2)
            r175 = round(self.r_ds_on_ohm(175.0, 18.0) * 1e3, 2)
        except Exception:                                 # noqa: BLE001
            pass
        i_hot = self.i_d_continuous(100.0)
        return {
            "part": self.part,
            "manufacturer": self.doc.get("manufacturer"),
            "family": self.doc.get("family"),
            "technology": self.doc.get("technology"),
            "switching_energy_source": self.switching_energy_source(),
            "package": self.doc.get("package"),
            "package_common_name": self.doc.get("package_common_name"),
            "cooling": self.doc.get("cooling"),
            "v_dss_V": _num(rat.get("v_dss_V")),
            "i_d_100c_A": None if i_hot is None else round(i_hot, 1),
            "i_d_25c_A": (lambda v: None if v is None else round(v, 1))(
                self.i_d_continuous(25.0)),
            "t_j_max_c": _num(rat.get("t_j_max_c")),
            "r_ds_on_25c_mohm": r25,
            "r_ds_on_175c_mohm": r175,
            "r_th_jc_k_w": _num((th.get("r_th_jc_k_w") or {}).get("typ")),
            "r_th_jc_max_k_w": _num((th.get("r_th_jc_k_w") or {}).get("max")),
            # WHAT THE PART PHYSICALLY IS (owner 2026-09-22): the outline
            # dimensions, the mass where the datasheet gives one, and a
            # GENERATED drawing of the package — no vendor artwork is fetched.
            "package_size_mm": self.package_size(),
            "weight_g": _num(self.doc.get("weight_g")),
            # OPTIONAL and null by default: a price is a quotation, not a
            # datasheet value, so it is only ever what somebody typed on the
            # card, with the date and the source they typed it from.
            "price": self.unit_price(),
            "image": self.doc.get("image"),
            "package_svg": self.package_outline_svg(),
            "suggested_parallel": (None if i_switch_rms_A is None else
                                   self.suggested_parallel(i_switch_rms_A)),
            "datasheet_url": self.doc.get("datasheet_url"),
            "datasheet_revision": self.doc.get("datasheet_revision"),
        }
