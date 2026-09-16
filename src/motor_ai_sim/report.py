"""Motor report — every solver's last answer about one machine, as one PDF.

The datasheet (``motor_ai_sim.datasheet``) states the machine as the CATALOGUE
holds it: the duties saved from the Simulation tab, the design block, the
battery.  It is a spreadsheet, it never looks at a solver, and it has no
pictures of a field.

This is the other document the user asked for (2026-09-08): *"у нас уже есть
datasheet, нам нужно его расширить до полного отчёта по всем результатам
моделирования, с картинками, с подшипниками, со всеми потерями — полный отчёт
по мотору, но только самое важное, не нужно сильно перегружать"*.  Six A4 pages:

    1  Cover              what machine, when, four headline numbers, SOURCES
    2  Machine            cross-section, geometry, materials, bearings
    3  Electromagnetic    operating point, torque/power, the whole loss table,
                          |B| and loss-density maps
    4  Thermal            boundary conditions, per-part temperatures, the heat
                          budget, the temperature map
    5  Mechanical         the case, per-part stress and safety factors,
                          contacts, the von Mises map, critical speeds
    6  Assumptions        what the numbers are and are not, per-solver stamps

NOTHING IS SOLVED HERE.  Every number is read out of a store some solver already
wrote: the last electromagnetic transient (``routes.simulation``), the last
thermal map and coupled loop (``routes.thermal``), the last rotor-stress /
modal / critical-speed answers (``routes.mechanical``), the last coupled run
(``routes.coupled``), the SKF bearing model (``motor_ai_sim.bearings``) and the
configuration's own yaml.  A store that is empty becomes one short line saying
so — a report is not the place to discover that the thermal tab was never run,
and it is certainly not the place to start a six-minute solve.

WHOSE ANSWER IS IT.  Every "last" store belongs to the machine that was LOADED
when it was written, which is not necessarily the configuration this report is
about.  So each source's geometry fingerprint is compared against the loaded
machine and a foreign answer is DROPPED, never quietly mixed in.  None of that
comparison reaches the page: since 2026-09-14 (client review) the document says
nothing about what the server has loaded — it is built from the stored duties of
the configuration it names, and the mismatch goes to the log instead.
"""
from __future__ import annotations

import io
import logging
import math
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

# ── palette (the datasheet's, so the two documents look like one family) ─────
NAVY = "#1F4E79"
GREY = "#F2F2F2"
NOTE = "#6E6E6E"
WARN = "#B3261E"
OK = "#1B7A3D"

#: Marker for a result that does not describe the live machine.  Deliberately
#: ASCII: the base-14 PDF fonts have no U+26A0, and a report that prints a black
#: box where the warning should be is worse than one that prints "(!)".
FLAG = "(!)"

# ── text that is not Latin-1 ────────────────────────────────────────────────
# The document is set in Helvetica, which is one of the fourteen fonts every PDF
# reader already has — no embedding, no 300 kB of font, and the text stream stays
# readable.  Its encoding is WinAnsi, which has °, ², ³, µ, · and the dashes but
# NOT the Greek letters a motor engineer writes, the arrows, or Cyrillic — and
# duty names ARE allowed to be Cyrillic (routes/family._DUTY_NAME_RE: the user
# types them off a Russian keyboard).
#
# So DejaVu Sans — which matplotlib already ships, and this module already
# depends on matplotlib for the field maps — is registered once and used ONLY
# for the runs that need it.  Everything else stays Helvetica.  When the font
# cannot be found the symbols below are spelled out instead and anything left
# becomes '?', because a wrong glyph is worse than a named one.

_SYMBOLS = {
    "α": "alpha", "β": "beta", "γ": "gamma", "Δ": "delta",
    "δ": "delta", "ε": "eps", "θ": "theta", "λ": "lambda",
    "μ": "u", "π": "pi", "ρ": "rho", "σ": "sigma",
    "τ": "tau", "φ": "phi", "ψ": "psi", "ω": "omega",
    "Ω": "Ohm", "∂": "d", "→": "->", "←": "<-",
    "↔": "<->", "≤": "<=", "≥": ">=", "≈": "~",
    "√": "sqrt", "∞": "inf", "∑": "sum", "∫": "int",
    "…": "...", "ₘ": "m", "₀": "0", "₁": "1", "₂": "2",
    "⚠": "(!)",
}

_UNICODE_FONT = ""


def _register_unicode_font() -> str:
    """Register DejaVu Sans for the runs Helvetica cannot set.  '' when absent."""
    global _UNICODE_FONT
    if _UNICODE_FONT:
        return _UNICODE_FONT
    try:
        import os
        import matplotlib
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        base = os.path.join(matplotlib.get_data_path(), "fonts", "ttf")
        pdfmetrics.registerFont(TTFont("ReportUni",
                                       os.path.join(base, "DejaVuSans.ttf")))
        pdfmetrics.registerFont(TTFont("ReportUni-Bold",
                                       os.path.join(base, "DejaVuSans-Bold.ttf")))
        pdfmetrics.registerFontFamily("ReportUni", normal="ReportUni",
                                      bold="ReportUni-Bold")
        _UNICODE_FONT = "ReportUni"
    except Exception as exc:                                # noqa: BLE001
        log.debug("report: no unicode font available (%s)", exc)
        _UNICODE_FONT = "-"
    return _UNICODE_FONT


def _wa(s: str) -> bool:
    try:
        s.encode("cp1252")
        return True
    except Exception:                                       # noqa: BLE001
        return False


def _txt(s: Any) -> str:
    """Paragraph markup for `s`, with the non-WinAnsi runs set in DejaVu.

    Existing markup in the caller's string is passed through — every literal in
    this module writes its own <b>/<font>, and escaping them here would print
    the tags.
    """
    s = "" if s is None else str(s)
    if _wa(s):
        return s
    font = _register_unicode_font()
    out: List[str] = []
    buf: List[str] = []
    cur_ok: Optional[bool] = None
    for ch in s:
        ok = _wa(ch)
        if cur_ok is None:
            cur_ok = ok
        elif ok != cur_ok:
            out.append(_wrap("".join(buf), not cur_ok, font))
            buf, cur_ok = [], ok
        buf.append(ch)
    if buf:
        out.append(_wrap("".join(buf), not cur_ok, font))
    return "".join(out)


def _wrap(chunk: str, exotic: bool, font: str) -> str:
    if not exotic:
        return chunk
    if font and font != "-":
        return f'<font name="{font}">{chunk}</font>'
    return _plain(chunk)


def _plain(s: Any) -> str:
    """`s` with every non-WinAnsi character spelled out — for TABLE CELLS.

    A plain string in a reportlab cell is drawn in the table's own font with no
    markup parsing, so the font switch above cannot reach it; a bearing card or
    a part name that somehow carries a Greek letter gets the word instead of a
    black box.
    """
    s = "" if s is None else str(s)
    if _wa(s):
        return s
    return "".join(c if _wa(c) else _SYMBOLS.get(c, "?") for c in s)

PAGE_W, PAGE_H = 595.28, 841.89          # A4 in points
MARGIN = 34.0
CONTENT_W = PAGE_W - 2 * MARGIN


# ---------------------------------------------------------------------------
# Reading what the solvers left behind
# ---------------------------------------------------------------------------
# Every one of these returns `None` (or an empty dict) instead of raising: a
# report of a machine whose thermal tab was never opened is a normal report with
# one line in it, and an import error in a solver module must not 500 an export.


def _num(v: Any, d: int = 3) -> Any:
    """Round for display; ``None`` for anything that is not a finite number.

    ANY number, not only a Python one (reviewer 2026-09-14, CS-2 / D1).
    ``np.float64`` subclasses ``float`` and went down the first branch;
    ``np.float32`` does NOT, so it fell through unrounded and was printed with
    ``str()`` — which is how the loss pie's slice labels came out as
    "5301.989 W" beside a legend that said "5,301.9 W".  matplotlib hands its
    ``autopct`` callback a float32, and that one path was enough to put raw
    floats on a client's chart.
    """
    if isinstance(v, bool) or v is None:
        return None
    if not isinstance(v, (int, float)):
        if isinstance(v, (str, bytes)) or not hasattr(v, "__float__"):
            return v
        try:
            v = float(v)
        except (TypeError, ValueError):                     # noqa: PERF203
            return v
    f = float(v)
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return round(f, d)


def _fmt(v: Any, d: int = 2, unit: str = "") -> str:
    """One cell of a table: the number with its unit, or an em dash."""
    n = _num(v, d)
    if n is None:
        return "—"
    if isinstance(n, float):
        if n == 0.0:
            n = 0.0                      # never print a "-0" closure residual
        s = f"{n:,.{d}f}".rstrip("0").rstrip(".") if d else f"{n:,.0f}"
        s = s if s not in ("", "-") else "0"
    else:
        s = str(n)
    return f"{s} {unit}".strip()


def _g(node: Any, *paths: str, default: Any = None) -> Any:
    """First present value along dotted paths ('summary.T_em_avg_Nm')."""
    for p in paths:
        cur: Any = node
        for part in p.split("."):
            if not isinstance(cur, dict):
                cur = None
                break
            cur = cur.get(part)
        if cur is not None:
            return cur
    return default


def winding_words(geo: Dict[str, Any]) -> str:
    """'6 turns x 4 parallel strands' — how one slot is actually wound.

    `num_wires_per_slot` is the TURN count; `wire_parallel` is how many strands
    are wound in hand for each of those turns (they share the turn's current);
    `wire_split` cuts each strand into that many narrower strips in SERIES,
    which lowers the eddy loss and leaves the current where it was.  A reader
    given only the turn count cannot tell 6 fat conductors from 6 x 4 thin ones,
    and the two are different machines (2026-09-10).
    """
    n = _numf(geo.get("num_wires_per_slot"))
    par = _numf(geo.get("wire_parallel"))
    spl = _numf(geo.get("wire_split"))
    if n is None:
        return "—"
    # `num_wires_per_slot` counts CONDUCTORS, not turns: 24 of them wound 4 in
    # hand is 6 turns, which is what the user reads off the drawing ("витков 6
    # по 4 параллельных провода в каждом") and what the run journal calls
    # `turns_per_coil`.  Dividing here rather than printing 24 is the whole
    # point of the row.
    if par and par > 1 and abs(n / par - round(n / par)) < 1e-9:
        out = "%s turns × %s parallel strands (%s conductors)" % (
            _fmt(n / par, 0), _fmt(par, 0), _fmt(n, 0))
    elif par and par > 1:
        out = "%s conductors, %s in hand" % (_fmt(n, 0), _fmt(par, 0))
    else:
        out = "%s turns" % _fmt(n, 0)
    if spl and spl > 1:
        out += ", each strand split into %s strips in series" % _fmt(spl, 0)
    return out


def _slots_poles(geo: Dict[str, Any]) -> Tuple[int, int]:
    """(slots, poles) of the whole machine.

    A die stores both, but a geometry written per SEGMENT (the fixtures, and
    anything applied through a `?geo=` override) carries only the per-segment
    counts and ``num_seg`` — and "None slots / None poles" on the cover of a
    report is not a thing to ship.  Multiplied out, never guessed.
    """
    def _i(*keys: str) -> int:
        for k in keys:
            v = geo.get(k)
            try:
                if v is not None and int(float(v)) > 0:
                    return int(float(v))
            except (TypeError, ValueError):
                pass
        return 0
    seg = _i("num_seg") or 1
    slots = _i("num_slots") or seg * _i("num_slots_per_segment")
    poles = _i("num_poles") or seg * _i("num_poles_per_segment")
    return slots, poles


def _magnet_card_temp(grade: Any) -> Optional[float]:
    """The temperature a magnet card was measured at, off its own name.

    'F52SH_120C' is a measurement at 120 °C — the same parse the datasheet makes
    (``datasheet._magnet_temp``).  It is the LAST resort for the report's magnet
    temperature line and is labelled as the card's own, never as a solve input.
    """
    try:
        import re
        m = re.search(r"(\d{2,3})\s*C\b", str(grade or ""))
        return float(m.group(1)) if m else None
    except Exception:                                       # noqa: BLE001
        return None


def _live_geometry() -> Dict[str, Any]:
    try:
        from motor_ai_sim.config import get_config
        return dict((get_config() or {}).get("geometry") or {})
    except Exception:                                       # noqa: BLE001
        return {}


def _live_fingerprint() -> Optional[str]:
    try:
        from motor_ai_sim.routes.simulation import _geometry_fingerprint
        return _geometry_fingerprint(None)
    except Exception:                                       # noqa: BLE001
        return None


def _last_transient() -> Optional[Dict[str, Any]]:
    """The last electromagnetic transient — the run the Simulation tab restores.

    Read straight off ``routes.simulation._last_transient_ref`` rather than
    through ``GET /physics/fem_transient/last``: the route takes the whole
    request apparatus (and a ``restore=false`` on it would SOLVE), while the ref
    is the same object the route hands back.
    """
    try:
        from motor_ai_sim.routes import simulation as sim
        res = sim._last_transient_ref.get("result")
        if res is None:
            # An empty ref is not proof nothing was run: a fresh process loads
            # it at import, but a test sandbox may have cleared it.
            try:
                sim._load_last_transient_into_cache()
            except Exception:                               # noqa: BLE001
                pass
            res = sim._last_transient_ref.get("result")
        return res if isinstance(res, dict) else None
    except Exception as exc:                                # noqa: BLE001
        log.debug("report: no last transient (%s)", exc)
        return None


def _last_field_snapshot() -> Optional[Dict[str, Any]]:
    """The last run's FIELD — the per-element |B| and loss-density arrays.

    The newest entry of ``routes.simulation._transient_field_snap``, preferring
    one whose ``meta.computed_at`` is the last transient's: the store keeps a
    handful of runs and the report must picture the run it is quoting.
    """
    try:
        from motor_ai_sim.routes import simulation as sim
        store = sim._transient_field_snap
        if not store:
            try:
                sim._load_last_transient_field_snapshot()
            except Exception:                               # noqa: BLE001
                pass
            store = sim._transient_field_snap
        if not store:
            return None
        items = list(store.values())
        run = _last_transient() or {}
        want = str(run.get("computed_at") or "")
        if want:
            for e in reversed(items):
                if str((e.get("meta") or {}).get("computed_at") or "") == want:
                    return e
        return items[-1]
    except Exception as exc:                                # noqa: BLE001
        log.debug("report: no field snapshot (%s)", exc)
        return None


def _last_thermal() -> Dict[str, Any]:
    """``{'field': entry|None, 'coupled': entry|None}`` — the Thermal tab's own
    store, loaded from its pickle the same way ``GET /api/thermal/last`` does."""
    out: Dict[str, Any] = {"field": None, "coupled": None}
    try:
        from motor_ai_sim.routes import thermal as th
        th._load_last()
        for k in ("field", "coupled"):
            e = th._LAST.get(k)
            if isinstance(e, dict) and e.get("result") is not None:
                out[k] = e
    except Exception as exc:                                # noqa: BLE001
        log.debug("report: no thermal result (%s)", exc)
    return out


def _last_mechanical() -> Dict[str, Any]:
    """``{'rotor_stress', 'modes', 'critical_speeds'}`` off the Mechanical tab's
    store — the same three kinds ``GET /api/mechanical/last`` serves."""
    out: Dict[str, Any] = {"rotor_stress": None, "modes": None,
                           "critical_speeds": None}
    try:
        from motor_ai_sim.routes import mechanical as me
        me._load_last()
        for k in out:
            e = me._LAST.get(k)
            if isinstance(e, dict) and e.get("result") is not None:
                out[k] = e
    except Exception as exc:                                # noqa: BLE001
        log.debug("report: no mechanical result (%s)", exc)
    return out


def _last_coupled() -> Optional[Dict[str, Any]]:
    try:
        from motor_ai_sim.routes import coupled as co
        co._load_last()
        return dict(co._LAST) if co._LAST else None
    except Exception as exc:                                # noqa: BLE001
        log.debug("report: no coupled run (%s)", exc)
        return None


def _bearing_losses(assign: Dict[str, Any], geo: Dict[str, Any],
                    rpm: float, rotor_mass_kg: float,
                    temp_c: Optional[float],
                    summary: Optional[Dict[str, Any]] = None
                    ) -> Optional[Dict[str, Any]]:
    """The SKF frictional-moment model plus windage, at this duty's speed.

    THE STORED RUN FIRST (2026-09-08).  Since every run of a machine with
    bearings carries its own mechanical block, the report reads THAT: it is the
    number the efficiency printed two rows above was derived from, and it carries
    the bearing temperature the run was actually billed at — a coupled run's
    converged shaft temperature, not whatever the assignment says today.
    Recomputing here would produce a second, slightly different answer on the
    same page.

    The recomputation is still the fallback, for the two cases where there is no
    stored block: a run solved before this existed, and a duty whose speed
    differs from the run's (the report then says so through ``from_stored_run``).

    ``None`` when the configuration names no bearing: an unknown mechanical loss
    is printed as unknown, never as a zero that flatters the efficiency.
    """
    try:
        from motor_ai_sim import bearings as brg
        from motor_ai_sim import mech_losses as ml
        if not brg.has_bearings(assign):
            return None
        if not (rpm and rpm > 0):
            return None
        stored = ml.from_summary(summary)
        # Only when it is the SAME operating point: the friction is a function of
        # speed, and pasting a 3 000 rpm bearing loss onto a 23 000 rpm duty
        # would be the wrong number with a provenance stamp on it.
        if stored and abs(float(stored.get("rpm") or 0.0) - float(rpm)) <= 0.5:
            return stored
        return brg.machine_bearing_losses(
            assign, rpm=float(rpm),
            temp_c=float(temp_c) if temp_c is not None else 70.0,
            rotor_mass_kg=float(rotor_mass_kg or 0.0), geometry=geo)
    except Exception as exc:                                # noqa: BLE001
        log.debug("report: bearing losses unavailable (%s)", exc)
        return None


def _scale_bearing_ends(brg: Dict[str, Any]) -> None:
    """Make the per-end rows add up to the bearing watts the rest of the
    document prints (MJ-11, audit v6).

    Section 1's bearing table is the only place a reader adds the two ends up,
    and it summed to 348.8 W beside the 322.2 W section 3 and section 4 print —
    the same SKF model at two grease temperatures.  With the block now carried
    at the coupled seat temperature the two agree; where the recomputation
    cannot be done, the ends are scaled onto the total instead, and the whole
    change goes on M_rr, which is the term the viscosity moves (ν^0.6).
    """
    ends = [b for b in (brg.get("bearings") or []) if isinstance(b, dict)]
    tot = _numf(brg.get("P_bearings_W"))
    have = sum(_numf(b.get("P_W")) or 0.0 for b in ends)
    if not ends or tot is None or have <= 0.0 or abs(have - tot) <= 0.05:
        return
    r = float(tot) / have
    for b in ends:
        for k in ("P_W", "M_total_Nm"):
            v = _numf(b.get(k))
            if v is not None:
                b[k] = v * r
        mt = _numf(b.get("M_total_Nm"))
        if mt is not None:
            other = sum(_numf(b.get(k)) or 0.0
                        for k in ("M_sl_Nm", "M_seal_Nm", "M_drag_Nm"))
            b["M_rr_Nm"] = mt - other


def _with_coupled_bearings(brg: Optional[Dict[str, Any]],
                           coupled: Optional[Dict[str, Any]],
                           rpm: Optional[float],
                           recompute: Optional[Any] = None
                           ) -> Optional[Dict[str, Any]]:
    """The coupled loop's CONVERGED bearing and windage watts, when the duty
    has them, over the run's own block.

    Two numbers for one bearing were on one page (reviewer 2026-09-11: 1129.6 W
    on the cover and in the loss table, 1352.5 W in the coupled table).  Both
    are the SKF model; they differ by the grease temperature it was run at —
    the assigned one against the one the coupled loop converged to.  The
    converged one is the machine's, so it wins wherever the duty carries it,
    and the block says where its temperature came from.
    """
    if not isinstance(coupled, dict) or not isinstance(brg, dict):
        return brg
    pb, pw = _numf(coupled.get("P_bearings_W")), _numf(coupled.get("P_windage_W"))
    if pb is None and pw is None:
        return brg
    r = _numf(coupled.get("rpm"))
    if r is not None and rpm and abs(r - float(rpm)) > 0.5:
        return brg
    out = dict(brg)
    # THE SEAT TEMPERATURE COMES WITH THE WATTS (BL-3 / MJ-11, audit v6).  The
    # totals were lifted to the coupled loop's and the temperature was left at
    # the assigned one, so section 1's grease row judged the L155 rated duty on
    # 97.3 °C and passed it green while the loop beside it says 122.9 °C, past
    # the LGLT_2 range.  `recompute(temp_c)` re-runs the same SKF model at the
    # converged seat, so the per-end moments belong to the watts above them.
    t = _numf(coupled.get("bearing_temp_c"))
    if t is not None and recompute is not None:
        try:
            rb = recompute(float(t))
        except Exception as exc:                            # noqa: BLE001
            log.debug("report: bearings not recomputed at the coupled seat (%s)",
                      exc)
            rb = None
        if isinstance(rb, dict) and rb.get("bearings"):
            out = dict(rb)
    out["bearings"] = [dict(b) for b in (out.get("bearings") or [])
                       if isinstance(b, dict)]
    out["P_bearings_W"] = pb if pb is not None else out.get("P_bearings_W")
    out["P_windage_W"] = pw if pw is not None else out.get("P_windage_W")
    out["P_mech_extra_W"] = float(out.get("P_bearings_W") or 0.0) + \
        float(out.get("P_windage_W") or 0.0)
    if t is not None:
        out["temp_c"] = t
        out["bearing_temp_c"] = t
        out["bearing_temp_source"] = "coupled"
        out["bearing_temp_note"] = (
            "converged by the coupled loop — the seat temperature the loop "
            "solved for and billed the friction at")
    out["temp_source"] = ("bearing temperature converged by the coupled run (%s)"
                          % _fmt(coupled.get("bearing_temp_c"), 0, "°C"))
    out["from_coupled"] = True
    _scale_bearing_ends(out)
    return out


def _rotating_mass_kg(summary: Dict[str, Any]) -> float:
    """Rotor iron + magnets + shaft + sleeve off the run's own mass rows.

    A REFERENCE part (a customer-supplied shaft) carries mass 0 in the card but
    it still spins and still loads the bearings, so its modelled mass is used —
    the same rule ``datasheet._rotating_mass_kg`` follows.
    """
    tot = 0.0
    for c in (_g(summary, "mass_components") or []):
        if not isinstance(c, dict):
            continue
        if not str(c.get("name") or "").startswith(
                ("Rotor back-iron", "Magnets", "Shaft", "Sleeve")):
            continue
        v = c.get("mass_kg") or c.get("mass_modelled_kg")
        try:
            tot += float(v or 0.0)
        except (TypeError, ValueError):
            pass
    return tot


# ---------------------------------------------------------------------------
# Pictures
# ---------------------------------------------------------------------------
# Server-side matplotlib over the mesh the solver stored: the browser draws
# these with WebGL, and a report cannot ask a browser for anything.  Every
# payload here carries its own vertices and triangles, so no mesh is rebuilt and
# no CAD is touched.


def _as_xy(v: Any) -> Optional["Any"]:
    """(n, 2) float array in MILLIMETRES from any of the stores' conventions.

    The electromagnetic snapshot keeps (2, n) in mm, the thermal and mechanical
    payloads keep (n, 2) — in metres and millimetres respectively — so both the
    orientation and the unit are detected rather than assumed.
    """
    try:
        import numpy as np
        a = np.asarray(v, dtype=float)
        if a.ndim != 2:
            return None
        if a.shape[1] != 2 and a.shape[0] == 2:
            a = a.T
        if a.shape[1] != 2 or a.shape[0] < 3:
            return None
        # A motor cross-section is tens of millimetres across; the same numbers
        # in metres are below one.  Nothing real sits between the two.
        if float(np.abs(a).max()) < 1.0:
            a = a * 1e3
        return a
    except Exception:                                       # noqa: BLE001
        return None


def _as_tris(v: Any) -> Optional["Any"]:
    try:
        import numpy as np
        a = np.asarray(v, dtype=np.int64)
        if a.ndim != 2:
            return None
        if a.shape[1] != 3 and a.shape[0] == 3:
            a = a.T
        return a if a.shape[1] == 3 and a.shape[0] >= 1 else None
    except Exception:                                       # noqa: BLE001
        return None


#: The web viewer's own ramp and band count (2026-09-09).  User: *"формат
#: вывода графиков должен быть совершенно одинаковый с нашим веб-интерфейсом"*.
#: The app paints every field the same way — a classic Ansys rainbow quantised
#: into ONE band count for all views (`web/src/components/simulation/fieldView
#: .ts`: `jet01`, `N_BANDS`, and its comment "все графики одинаково") — so the
#: report's maps are that, to the same arithmetic, rather than a different
#: matplotlib colormap per quantity.  A picture in the document and the picture
#: on the tab it came from must be the same picture.
_JET_BANDS = 11


def _jet01(t: float) -> tuple:
    """`jet01` of fieldView.ts, term for term: blue → cyan → green → yellow →
    red, in 0..1 RGB (matplotlib's unit, the web's is 0..255)."""
    x = min(max(float(t), 0.0), 1.0)
    return (min(max(1.5 - abs(4 * x - 3), 0.0), 1.0),
            min(max(1.5 - abs(4 * x - 2), 0.0), 1.0),
            min(max(1.5 - abs(4 * x - 1), 0.0), 1.0))


def _jet_cmap(bands: int = _JET_BANDS):
    """The band CENTRES as a listed colormap — `bandColor(k, n)` of the web's
    `fieldOutput.ts`, which is what its shader paints inside band k."""
    from matplotlib.colors import ListedColormap

    return ListedColormap([_jet01((k + 0.5) / bands) for k in range(bands)])


#: NOTHING IN A FIGURE OF THIS REPORT MAY PRINT SMALLER THAN THIS (BL-3,
#: audit v5 2026-09-14).  A map is drawn for a figure nearly 10 inches wide and
#: then placed at 8.1 cm, so every point size inside it reaches the page at
#: about a third of itself: the colour bars — the scale the captions make
#: load-bearing — printed at 1.3–1.8 pt, a quarter of the body text.  The bar's
#: type is now sized from the width it will be PLACED at, so 7 pt is 7 pt on
#: paper whatever the figure was drawn at.
FIG_MIN_PT = 7.0

#: …and the colour bar's own axis label, a touch above it.
MAP_LABEL_PT = 7.5

#: The pixel width every map is drawn at, and the share of the figure the
#: colour bar adds beside the axes — the two numbers `map_font_pt` needs to
#: know how much the picture shrinks between `savefig` and the page.
MAP_PX = 1400
MAP_BAR_K = 1.14

#: …plus the room `savefig(bbox_inches="tight")` adds AROUND the figure for the
#: bigger labels.  Measured at 8 % on the Ø200's maps; 10 % is the margin that
#: keeps the printed size on the right side of `FIG_MIN_PT`.
MAP_BBOX_K = 1.10

#: The full content width a map is placed at when it is not half of a pair.
MAP_FULL_CM = round(CONTENT_W / 28.35, 2)

#: Tick labels and in-figure annotations on the CHARTS.  Those are drawn at the
#: width they are placed at, so the number is the printed point size — named
#: here rather than repeated as a literal at twenty call sites, and never under
#: `FIG_MIN_PT` (BL-3 / MJ-2 / MJ-3 / MJ-4, audit v5).
CHART_TICK_PT = 8.5
CHART_LABEL_PT = 8.0


def map_font_pt(base_pt: float, width_cm: Optional[float] = None,
                px: int = MAP_PX) -> float:
    """The size to DRAW a map label at so that it PRINTS at ``base_pt``.

    :func:`_map_png` builds a figure ``px / 160 * 1.14`` inches wide whatever
    the document then does with it; ``width_cm`` is the width it is placed at.
    The ratio of the two is how much the type shrinks on the way to the page,
    and multiplying by it is what makes the printed size the size asked for.
    Never below ``base_pt``: a map placed WIDER than it was drawn does not get
    its labels shrunk to compensate.
    """
    if not width_cm:
        return float(base_pt)
    fig_in = float(px) / 160.0 * MAP_BAR_K * MAP_BBOX_K
    place_in = max(float(width_cm), 1.0) / 2.54
    return float(base_pt) * max(1.0, fig_in / place_in)


def map_tick_label(v: float) -> str:
    """One colour-bar tick, with no shared ``1e8`` multiplier above the bar.

    matplotlib's default formatter factors a common power of ten out of the
    ticks and prints it as a bare ``1e8`` over the bar — at the sizes above it
    was unreadable, and unreadable is the same as absent for a scale whose unit
    it carries (CS-1).  Every tick now carries its own magnitude, in the SI
    suffixes an engineer reads a loss density in.
    """
    try:
        x = float(v)
    except (TypeError, ValueError):
        return ""
    if x == 0:
        return "0"
    a = abs(x)
    for lim, suf in ((1e12, "T"), (1e9, "G"), (1e6, "M"), (1e3, "k")):
        if a >= lim:
            return "%.3g %s" % (x / lim, suf)
    if a < 1e-4:
        return "%.2e" % x
    return "%.3f" % x if a < 10 else "%.4g" % x


def _map_png(verts: Any, tris: Any, values: Any, *, label: str,
             log_scale: bool = False, grp: Any = None, mesh: bool = True,
             per_node: bool = False, mask_nonpositive: bool = False,
             mask_nonfinite: bool = False,
             vmax_pct: Optional[float] = None,
             vmax_fixed: Optional[float] = None,
             range_from: str = "elements",
             reverse: bool = False,
             context: Any = None,
             iso_lines: int = 0,
             only: Any = None,
             outline: Any = None,
             pair_range: Optional[Tuple[float, float]] = None,
             range_only: bool = False,
             width_cm: Optional[float] = None,
             px: int = MAP_PX) -> Any:
    """One field map as a PNG: tripcolor on the stored mesh, with a colour bar.

    ``mask_nonpositive`` leaves the elements no model wrote into BLANK rather
    than painting them at the bottom of the scale — on a loss map the bottom of
    the scale is what air looks like, and colouring an unmodelled part there
    says "no loss here", which is not what "not modelled" means.

    The picture is framed on what is DRAWN, not on the whole mesh: the far-field
    air ring around a machine is most of the mesh's extent, and framing on it
    left the motor a small shape in the middle of a square of white (the user's
    "ничего не видно", 2026-09-08).  The figure's aspect follows that frame, so
    a half-machine wedge comes out twice as wide as tall and fills the page
    width without a page of white under it.

    ``context`` (per-element bool) paints elements in flat light grey UNDER the
    field — the rest of the machine behind a magnets-only map, so twelve small
    coloured blocks are read in their place and not as a scatter.
    ``vmax_fixed`` pins the top of the scale (100 % on a "Br remaining" map).

    ``range_from='nodal'`` takes the colour range from the field that is DRAWN
    — the per-class nodal average — instead of from the element values it is
    averaged from.  That is what the viewer does (2026-09-10), and the two
    differ by more than rounding: on the Ø200 the band's element peak is
    1770 MPa and its nodal one 1558, so a document scaled to the first and a
    screen scaled to the second put the same stress in different colours.  With
    it, ``vmax_pct`` still applies as a CAP (2026-09-14) — averaging tames a
    singular corner but does not remove it, and the |B| bar still ended at
    3.6 T on a 2.3 T machine; the cap never raises the range, and the bar grows
    an upper arrow so nothing is hidden.

    ``width_cm`` is the width the picture will be PLACED at in the document,
    and the colour bar's type is sized from it so that it reaches the page at
    :data:`FIG_MIN_PT` rather than at a third of it (BL-3).  Nothing else about
    the figure changes — same geometry, same line weights, same framing.

    ``range_only=True`` returns ``(vmin, vmax)`` — the scale this map WOULD be
    drawn on — and draws nothing; ``pair_range`` pins the scale to a range the
    caller computed instead.  Those two are how the halves of a side-by-side
    figure end up on ONE colour scale (user 2026-09-14: *"слева картинка из
    rated, справа из peak"*), which is the only way the two pictures can be read
    against each other at a glance.
    """
    p = _as_xy(verts)
    t = _as_tris(tris)
    if p is None or t is None:
        return None
    try:
        import numpy as np
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.tri as mtri
        from matplotlib.colors import BoundaryNorm

        vals = np.asarray(values, dtype=float).ravel()
        n_need = p.shape[0] if per_node else t.shape[0]
        if vals.size != n_need or not np.isfinite(vals).any():
            return None
        if int(t.max()) >= p.shape[0]:
            return None

        tri = mtri.Triangulation(p[:, 0], p[:, 1], t)
        finite = vals[np.isfinite(vals)]
        if mask_nonpositive:
            vals = np.ma.masked_where(~(vals > 0), vals)
            finite = finite[finite > 0]
        elif mask_nonfinite:
            # The caller blanked whole domains by writing NaN over them (the |B|
            # map does this to the air); the colour scale must not see them and
            # the triangles must come out white, not "lowest value".
            vals = np.ma.masked_invalid(vals)
        if finite.size == 0:
            return None
        vmax = float(np.percentile(finite, vmax_pct)) if vmax_pct else float(finite.max())
        if vmax_fixed is not None:
            vmax = float(vmax_fixed)
        vmin = float(finite.min())
        _nodal_range: Optional[Tuple[float, float]] = None
        # BANDED, like the viewer: 11 equal steps of the scale, each one flat
        # colour, so a value read off the document lands in the same band as the
        # same value read off the tab.  The floor is the field's own minimum
        # (already computed above) for the same reason the web floors there.
        # ── WHAT IS DRAWN, and the field that is drawn FROM ────────────────
        # Lifted above the colour scale (2026-09-10) so the range can be taken
        # from the picture instead of from the numbers behind it: the viewer
        # colours the per-class NODAL average, and a document scaled to the
        # element values it was averaged from puts the same stress in a
        # different band.
        ctx = None
        if context is not None:
            c_arr = np.asarray(context, dtype=bool).ravel()
            if c_arr.size == t.shape[0] and c_arr.any():
                ctx = c_arr
        if per_node:
            drawn = np.ones(t.shape[0], dtype=bool)
        else:
            drawn = ~np.ma.getmaskarray(vals)
        # `only` cuts elements out of a NODAL field, which cannot blank itself
        # the way an element field does by carrying NaN: one node belongs to
        # several elements.  Used to drop the far-field air from the A_z map.
        if only is not None:
            o_arr = np.asarray(only, dtype=bool).ravel()
            if o_arr.size == t.shape[0] and o_arr.any():
                drawn = drawn & o_arr
        if ctx is not None:
            drawn = drawn | ctx
        if not drawn.any():
            drawn = np.ones(t.shape[0], dtype=bool)

        v_elem = (np.ma.mean(np.ma.masked_invalid(vals)[t], axis=1)
                  if per_node else vals)
        drawn_f = drawn & ~np.ma.getmaskarray(np.ma.masked_invalid(v_elem))
        if grp is None:
            grp_arr = np.zeros(t.shape[0], dtype=int)
        else:
            grp_arr = np.asarray(grp, dtype=int).ravel()
            if grp_arr.size != t.shape[0]:
                grp_arr = np.zeros(t.shape[0], dtype=int)
        pa, pb, pc = p[t[:, 0]], p[t[:, 1]], p[t[:, 2]]
        area = 0.5 * np.abs((pb[:, 0] - pa[:, 0]) * (pc[:, 1] - pa[:, 1])
                            - (pc[:, 0] - pa[:, 0]) * (pb[:, 1] - pa[:, 1]))
        area = np.where(area > 0, area, 1e-12)
        v_raw = np.ma.filled(np.ma.masked_invalid(v_elem), np.nan)

        # One area-weighted nodal average per material class — iron and air must
        # not bleed across their boundary (`buildFieldView`, the viewer).
        groups = []
        for g in np.unique(grp_arr[drawn_f]):
            sel = drawn_f & (grp_arr == g)
            if sel.sum() < 1:
                continue
            num = np.zeros(p.shape[0])
            den = np.zeros(p.shape[0])
            w = area[sel]
            vv = np.nan_to_num(v_raw[sel], nan=0.0)
            for c in range(3):
                np.add.at(num, t[sel, c], vv * w)
                np.add.at(den, t[sel, c], w)
            nodal = np.where(den > 0, num / np.maximum(den, 1e-30),
                             float(np.nanmean(vv)) if vv.size else 0.0)
            groups.append((sel, nodal, den > 0))
        if str(range_from).lower() == "nodal" and groups:
            _seen = np.concatenate([nd[hit] for _s, nd, hit in groups
                                    if hit.any()]) if any(
                hit.any() for _s, _n, hit in groups) else None
            if _seen is not None and _seen.size:
                _nodal_range = (float(np.min(_seen)), float(np.max(_seen)))

        cmap_obj = _jet_cmap()
        if _nodal_range is not None and vmax_fixed is None and not log_scale:
            vmin, vmax = _nodal_range
            # …but a PERCENTILE CAP still applies (2026-09-14).  The nodal
            # average tames a corner singularity, it does not remove it: the
            # |B| map's bar still ended at 3.6 T on a 2.3 T machine.  The cap
            # never RAISES the range, and the caption names the raw maximum.
            if vmax_pct:
                vmax = min(vmax, float(np.percentile(finite, vmax_pct)))
        # ── ONE SCALE ACROSS THE PAIR ────────────────────────────────────
        # Everything above this line is the range this map would be drawn on
        # alone; `range_only` hands exactly that back so the caller can union
        # two of them, and `pair_range` is that union coming back.  Nothing
        # else about the picture changes — same bands, same framing, same bar.
        if range_only:
            return (float(vmin), float(vmax))
        if pair_range is not None:
            vmin, vmax = float(pair_range[0]), float(pair_range[1])
            if not (vmax > vmin):
                vmax = vmin + 1e-9
        if log_scale:
            vmin = max(vmin, vmax / 1e5) if vmax > 0 else 1e-12
            lo = max(vmin, 1e-12)
            hi = max(vmax, lo * 10)
            edges = np.logspace(np.log10(lo), np.log10(hi), _JET_BANDS + 1)
        else:
            hi = vmax if vmax > vmin else vmin + 1e-9
            edges = np.linspace(vmin, hi, _JET_BANDS + 1)
        norm = BoundaryNorm(edges, _JET_BANDS)

        # The frame: the vertices of every element that is drawn (field or
        # context), with a 2 % margin.
        fv = p[np.unique(t[drawn].ravel())]
        x0, x1 = float(fv[:, 0].min()), float(fv[:, 0].max())
        y0, y1 = float(fv[:, 1].min()), float(fv[:, 1].max())
        mx, my = 0.02 * max(x1 - x0, 1e-9), 0.02 * max(y1 - y0, 1e-9)
        x0, x1, y0, y1 = x0 - mx, x1 + mx, y0 - my, y1 + my
        aspect = (x1 - x0) / max(y1 - y0, 1e-9)          # width / height
        aspect = min(max(aspect, 0.6), 3.0)
        w_in = px / 160.0
        h_in = w_in / aspect
        # room for the colour bar at the right
        fig, ax = plt.subplots(figsize=(w_in * 1.14, h_in), dpi=160)
        if ctx is not None:
            # Greys runs white → black; 0.45 at low alpha is a light grey.
            grey = np.ma.masked_where(~ctx, np.full(t.shape[0], 0.45))
            ax.tripcolor(tri, facecolors=grey, cmap="Greys", vmin=0.0, vmax=1.0,
                         alpha=0.35, edgecolors="none")
        # THE VIEWER'S OWN RECIPE, term for term (2026-09-09; user: "у нас же
        # в вебе всё сглажено и красиво, нужно сделать точно так же"):
        #
        #   1. an element field is averaged onto the VERTICES, weighted by
        #      triangle area and kept SEPARATE per material class — iron and
        #      air must not bleed across their boundary (`buildFieldView` in
        #      web/src/components/simulation/fieldView.ts);
        #   2. that nodal field is interpolated INSIDE the triangle and banded
        #      per pixel (`BAND_FRAG`), which is what makes a band edge a
        #      smooth curve instead of the triangle facets a flat fill draws —
        #      the "trash" the first banded version of this function produced;
        #   3. every band edge carries an iso-line at 30 % of the band colour.
        #
        # `tricontourf` interpolates and fills between levels, `tricontour`
        # draws the edges: the same two things the shader does per pixel.
        # `reverse` flips the palette, not the numbers.  A SAFETY FACTOR is the
        # one field on which jet reads backwards: low is the dangerous end, and
        # painting it blue while a shaft that carries nothing goes red says the
        # opposite of what the picture means (2026-09-10).  The viewer solves
        # the same problem with red-below / green-in-range / blue-above.
        band_cols = [cmap_obj(k / max(_JET_BANDS - 1, 1)) for k in range(_JET_BANDS)]
        if reverse:
            band_cols = list(reversed(band_cols))
        iso_cols = [(c[0] * 0.30, c[1] * 0.30, c[2] * 0.30) for c in band_cols[1:]]
        tpc = None
        # The bar's ends are FLAT when the range is the picture's own — there is
        # nothing past either end to point at (the viewer's bar has no arrows).
        _extend = "neither" if _nodal_range is not None else "both"
        if _nodal_range is not None and vmax < _nodal_range[1] - 1e-12:
            # Capped: there IS something above the top of the bar, so the bar
            # says so with an arrow instead of pretending the range is complete.
            _extend = "max"
        for sel, nodal, _hit in groups:
            tri_g = mtri.Triangulation(p[:, 0], p[:, 1], t)
            tri_g.set_mask(~sel)
            try:
                tpc = ax.tricontourf(tri_g, nodal, levels=edges, colors=band_cols,
                                     extend=_extend)
                if len(edges) > 2:
                    ax.tricontour(tri_g, nodal, levels=edges[1:-1], colors=iso_cols,
                                  linewidths=0.25, alpha=0.55)
            except (ValueError, RuntimeError):
                # a group of one or two triangles has nothing to contour
                ax.tripcolor(tri_g, facecolors=np.ma.masked_where(~sel, v_raw),
                             cmap=cmap_obj, norm=norm)
        # ── WHERE ONE PART ENDS AND THE NEXT BEGINS ────────────────────────
        # User 2026-09-10: "можешь сделать белые линии разделов магнитов в
        # механике и температуре в отчёте".  The viewer draws the parts'
        # outlines over the field; this report drew only the mesh, so a magnet
        # and the iron around it at the same stress were one shape and the
        # picture could not be read as a machine.
        #
        # Taken from the FIELD's own class tags rather than from a separate
        # outline payload: an edge shared by two triangles of different
        # materials is a boundary, an edge with only one drawn triangle behind
        # it is the outside of the section, and both are drawn.  Nothing to keep
        # in step, and it works on every map this function draws.
        # WHICH classes the outlines are taken from is a separate question from
        # which classes the FIELD is averaged per.  A_z is continuous across
        # every material boundary — folding it per part would be wrong — but the
        # machine still has to be recognisable under it, so `outline` supplies
        # the parts on their own (2026-09-10).
        _ol = outline if outline is not None else grp
        _ol_arr = grp_arr
        if outline is not None:
            _o = np.asarray(outline, dtype=int).ravel()
            if _o.size == t.shape[0]:
                _ol_arr = _o
            else:
                _ol = None
        if _ol is not None and drawn.any():
            _edge_owner: Dict[tuple, list] = {}
            _tk = t[drawn]
            _gk = _ol_arr[drawn]
            for _e in range(3):
                _a = _tk[:, _e]
                _b = _tk[:, (_e + 1) % 3]
                for _i in range(_tk.shape[0]):
                    _key = (int(_a[_i]), int(_b[_i]))
                    if _key[0] > _key[1]:
                        _key = (_key[1], _key[0])
                    _edge_owner.setdefault(_key, []).append(int(_gk[_i]))
            _seg = [(k, v) for k, v in _edge_owner.items()
                    if len(v) == 1 or len(set(v)) > 1]
            if _seg:
                from matplotlib.collections import LineCollection
                _lines = [[(p[k[0], 0], p[k[0], 1]), (p[k[1], 0], p[k[1], 1])]
                          for k, _v in _seg]
                ax.add_collection(LineCollection(
                    _lines, colors="white", linewidths=0.7, zorder=3.5))

        # ── FLUX LINES ────────────────────────────────────────────────────
        # A contour of the vector potential IS a line of flux: B = curl(A ẑ) is
        # everywhere parallel to a line of constant A_z, and the flux between
        # two such lines is the difference of their A_z.  So `iso_lines` equally
        # spaced contours draw the field's own picture — dense where the flux
        # crowds, sparse where it does not — which is what an engineer looks at
        # first and what every FE post-processor calls "flux lines" (user
        # 2026-09-10: "в отчёт добавь ещё график A_z").
        #
        # Drawn over the WHOLE drawn mesh in one pass, not per material class:
        # A_z is continuous across every boundary — that is the point of solving
        # for it — so folding it per part would break every line at the iron.
        if iso_lines and per_node:
            try:
                tri_i = mtri.Triangulation(p[:, 0], p[:, 1], t)
                tri_i.set_mask(~drawn)
                _fv = np.ma.filled(vals, np.nan)
                _in = np.unique(t[drawn].ravel())
                _sub = _fv[_in]
                _sub = _sub[np.isfinite(_sub)]
                _lv = np.linspace(float(_sub.min()), float(_sub.max()),
                                  int(iso_lines) + 1)
                # SOLID throughout: matplotlib dashes negative levels by
                # default, and on a vector potential half the lines are
                # negative only because of where the zero happens to sit —
                # dashing them says a difference that is not there.
                ax.tricontour(tri_i, _fv, levels=_lv, colors="#101010",
                              linewidths=0.45, alpha=0.85, linestyles="solid")
            except (ValueError, RuntimeError):
                pass

        # …and the MESH over it (user 2026-09-09: "выводи картинки вместе с
        # сеткой") — the same hairline the viewer's Mesh toggle draws, light
        # enough that it reads as texture over the field rather than as ink.
        if mesh:
            tri_m = mtri.Triangulation(p[:, 0], p[:, 1], t)
            tri_m.set_mask(~drawn)
            ax.triplot(tri_m, color="#2b2b2b", linewidth=0.12, alpha=0.35)
        ax.set_xlim(x0, x1)
        ax.set_ylim(y0, y1)
        from matplotlib.cm import ScalarMappable
        from matplotlib.colors import ListedColormap
        sm = ScalarMappable(norm=norm,
                            cmap=(ListedColormap(band_cols) if reverse
                                  else cmap_obj))
        sm.set_array([])
        # THE BAR IS ALWAYS THE NORM'S, never the contour set's (user
        # 2026-09-10: "делай одинаковый шкалу без стрелок везде").  A colorbar
        # built from a `tricontourf` inherits its `extend` and grows arrow ends
        # on the maps that clip — so a log loss map got pointed ends and a
        # stress map flat ones, and two bars in one document meant two things.
        # The FILL still extends where it must; only the bar is uniform.
        cb = fig.colorbar(sm, ax=ax, fraction=0.035, pad=0.02,
                          shrink=0.92 if aspect < 1.6 else 0.98)
        # BOTH ENDS ARE LABELLED.  matplotlib picks its own ticks and drops the
        # last one whenever it does not land on a round number, so the top of
        # every bar in this report was blank — and the top of a bar is the
        # number the reader is looking for (user 2026-09-10: "нигде не стоит
        # отметки верхней границы шкалы, а это важно").  The ticks are the band
        # EDGES, thinned to keep them legible, with the first and the last
        # always in.
        try:
            from matplotlib.ticker import FuncFormatter
            _ed = [float(e) for e in edges]
            _step = 1 if len(_ed) <= 7 else 2
            _ticks = _ed[::_step]
            if _ed[0] not in _ticks:
                _ticks = [_ed[0]] + _ticks
            if _ed[-1] not in _ticks:
                _ticks.append(_ed[-1])
            cb.set_ticks(_ticks)
            # EVERY TICK CARRIES ITS OWN MAGNITUDE (CS-1): the default
            # formatter hangs a bare `1e8` over the bar instead.
            cb.ax.yaxis.set_major_formatter(
                FuncFormatter(lambda v, _p: map_tick_label(v)))
            cb.ax.yaxis.get_offset_text().set_visible(False)
        except Exception:                                   # noqa: BLE001
            pass
        # …AT THE SIZE THEY WILL BE READ AT, not at the size they are drawn at
        # (BL-3) — see `map_font_pt`.
        cb.ax.tick_params(labelsize=map_font_pt(FIG_MIN_PT, width_cm, px))
        cb.set_label(label, fontsize=map_font_pt(MAP_LABEL_PT, width_cm, px))
        ax.set_aspect("equal")
        ax.axis("off")
        fig.tight_layout(pad=0.15)
        buf = io.BytesIO()
        # bbox "tight": a sector or a half-machine is wide and short, and a
        # square figure around it was two thirds white — that white was what
        # pushed the field maps onto a page of their own (2026-09-08).
        fig.savefig(buf, format="png", transparent=False, facecolor="white",
                    bbox_inches="tight", pad_inches=0.05)
        plt.close(fig)
        return buf.getvalue()
    except Exception as exc:                                # noqa: BLE001
        log.debug("report: map render failed (%s)", exc)
        try:
            import matplotlib.pyplot as plt
            plt.close("all")
        except Exception:                                   # noqa: BLE001
            pass
        return None


#: How many flux lines the A_z map draws.  32 is the density that reads as a
#: field on a page this size — fewer looks sparse in the gap, more turns the
#: yoke solid black.
_AZ_LINES = 32

TORQUE_CAPTION = (
    "Torque through one electrical period and its ripple spectrum; the mean is "
    "the number the tables quote.")

CURRENTS_CAPTION = (
    "The three winding currents the run was driven with — the winding current "
    "the tables quote.")

VOLTAGE_CAPTION = (
    "Line voltages and the spectrum of U_AB — the THD the warnings section "
    "checks.")

#: The same figure on a PWM duty, where the main panel is the BRIDGE and the
#: field's winding voltage keeps a small panel of its own (2026-09-15).
#: TWO SENTENCES, like every other caption in the document (CS-12, audit v7):
#: this one ran to three and 501 characters, against 323 for the next longest.
VOLTAGE_CAPTION_PWM = (
    "The bridge line voltage as the inverter switches it — a three-level pulse "
    "train of +V_dc / 0 / −V_dc, its fundamental dashed over it, and its own "
    "spectrum and THD; the winding voltage the field gives back is the small "
    "panel below.")

#: Which of the two the pulse train came from — said in the caption, because a
#: reconstruction and a stored waveform are not the same claim.
PWM_WAVE_SOURCE = {
    "stored": " The train is the run's own PWM sidecar.",
    "regenerated": (" The train was regenerated from the record's modulator "
                    "parameters."),
}


def voltage_caption(*wfs: Any) -> str:
    """The voltage figure's caption — the PWM one as soon as one side is PWM."""
    for wf in wfs:
        br = pwm_bridge_ll(wf or {})
        if br and br.get("t_s"):
            return VOLTAGE_CAPTION_PWM + PWM_WAVE_SOURCE.get(
                str(br.get("source") or ""), "")
    return VOLTAGE_CAPTION

SF_CHART_CAPTION = (
    "Safety factor per part; a bar short of the dashed line does not pass.")

#: The waterfall's caption WITHOUT the hatched-bar sentence.  The bar is drawn
#: only when there are mechanical watts outside the section worth more than
#: 1 % of the map, so the sentence is added by `heat_chart_caption` at the same
#: threshold — a caption that describes a bar nobody drew is worse than no
#: caption (reviewer 2026-09-14, B2).
HEAT_CHART_CAPTION_PLAIN = (
    "Heat budget as steps: what the loss map carries, what each cooled surface "
    "takes out, and the closure error.")

#: Kept for callers that want the full sentence unconditionally.
HEAT_CHART_CAPTION = HEAT_CHART_CAPTION_PLAIN + (
    " Hatched = mechanical heat outside this 2-D section, in neither column.")

#: The three captions the two renderers now SHARE (MJ-5, reviewer 2026-09-14).
#: The loss pie, the temperature bars and the heat waterfall were drawn only in
#: the .docx, so a client sent the PDF and a client sent the Word document had
#: different evidence for the same machine.  Held here so the two documents
#: cannot drift apart again.
#: A wedge narrower than this cannot hold a two-line label without half of it
#: printing over the pie's edge (MJ-9, audit v5): 8.2 % of a circle is 30° and
#: the label is wider than the wedge at every radius it could sit at.  Those
#: terms are read off the legend under the pie, which carries the same watts
#: and the same per cent.
PIE_LABEL_MIN_PCT = 12.0

LOSS_PIE_CAPTION = (
    "Where the watts go — the slices are the rows beside them, and terms under "
    "%g %% are named in the legend under the pie only." % PIE_LABEL_MIN_PCT)

MASS_PIE_CAPTION = (
    "Where the kilograms are; the slices are the rows beside them.")

TEMP_BARS_CAPTION = (
    "Bar = the part's maximum, tick = its average; dashed lines are the "
    "insulation class and the magnet grade, a blue bar has no limit of its own.")

MASS_J_CAPTION = (
    "Where the spinning inertia is — a part weighed by the SQUARE of its "
    "radius, same colours as the mass pie.")


def mass_j_caption(em: Dict[str, Any]) -> str:
    """…and the magnet's two shares, ROUNDED TO WHAT THE TABLES PRINT.

    The caption used to say "a quarter of the mass and two thirds of the
    inertia" beside tables reading 28 % and 61 % (reviewer 2026-09-14, D12).
    The numbers are in the same two blocks the pies are drawn from, so they are
    read rather than described.
    """
    m_tot = 0.0
    m_mag = 0.0
    for c in (_g(em, "mass_components") or []):
        if not isinstance(c, dict):
            continue
        v = _numf(c.get("mass_kg") or c.get("mass_modelled_kg")) or 0.0
        m_tot += v
        if "magnet" in str(c.get("name") or "").lower():
            m_mag += v
    J = _g(em, "rotor_inertia") if isinstance(_g(em, "rotor_inertia"), dict) else {}
    j_tot = sum(_numf(v) or 0.0 for k, v in (J or {}).items()
                if k in ("rotor_iron", "magnet", "shaft", "sleeve"))
    j_mag = _numf((J or {}).get("magnet")) or 0.0
    if not (m_tot > 0 and j_tot > 0 and m_mag > 0):
        return MASS_J_CAPTION
    return (MASS_J_CAPTION[:-len(", same colours as the mass pie.")]
            + ": the magnets are %s of the mass and %s of the inertia."
            % (_fmt(100.0 * m_mag / m_tot, 0, "%"),
               _fmt(100.0 * j_mag / j_tot, 0, "%")))

AZ_CAPTION = (
    "Magnetic vector potential A_z with its contours: each contour is a flux "
    "line, so crowded lines mean dense flux."
)


def thumb_svg_for(die_doc: Dict[str, Any]) -> str:
    """The die's cross-section drawing, redrawn if the stored one is stale.

    A die keeps its `thumb_svg` as a string and refreshes it only when the
    geometry is synced from a live save.  That made the drawing immune to its
    own generator: the band was added and the palette was taken from the app,
    and the report kept printing the old picture because the die still held the
    old string (user 2026-09-10: "опять Machine без бандажа и цвета не те").

    So the stored string is used only while it carries the CURRENT version
    stamp; otherwise it is regenerated here, from the die's own geometry, and
    nothing is written back — the report does not mutate the catalog.  A
    generator that cannot run (no CadQuery, a geometry it refuses) falls back to
    whatever was stored, which is what the reader had before.
    """
    stored = str((die_doc or {}).get("thumb_svg") or "")
    try:
        from motor_ai_sim.routes.presets import (THUMB_SVG_VERSION,
                                                 _gen_thumb_svg)
        if ('data-thumb-v="%d"' % THUMB_SVG_VERSION) in stored:
            return stored
        geo = (die_doc or {}).get("geometry") or {}
        if not geo:
            return stored
        fresh = _gen_thumb_svg(dict(geo))
        if fresh:
            log.info("report: die drawing was stale (v<%d) - redrawn for this "
                     "document", THUMB_SVG_VERSION)
            return str(fresh)
    except Exception as exc:                                # noqa: BLE001
        log.debug("report: could not redraw the die thumbnail (%s)", exc)
    return stored


#: The thermal mesh's part ids that together make up ONE winding: the copper,
#: its enamel, the slot insulation, the coating.  They are separate materials to
#: the solver and must stay separate there — the enamel is most of the thermal
#: resistance out of a slot — but outlining each of them drew a white line
#: around every strand and every film, and the slot came out hatched instead of
#: looking like a coil (2026-09-10).  Ids from the thermal result's own
#: `part_names` map.
_TH_WINDING_IDS = (2, 9, 10, 61, 62, 63)


def _winding_as_one(dom: Any) -> Any:
    """`domain_per_tri` with the winding's layers folded into one class."""
    try:
        import numpy as np
        a = np.asarray(dom, dtype=int).ravel()
        return np.where(np.isin(a, _TH_WINDING_IDS), _TH_WINDING_IDS[0], a)
    except Exception:                                       # noqa: BLE001
        return dom


def _az_outline(tags: Any) -> Any:
    """The domain tags, with every CONDUCTOR folded into one class.

    The mesh tags each strand of each coil separately, and this winding is
    STRIPS with insulation between them — so outlining on the raw tags drew a
    white line around every strip and every air gap between two of them, and
    each slot came out as a hatched block.  Folding the conductors in with the
    air leaves the slot outlined by the IRON around it, which is the shape a
    reader is looking for; the magnets stay separate because they are separate
    parts.
    """
    try:
        import numpy as np
        from motor_ai_sim.simulation.sb_domains import DOM_AIR, DOM_COIL_BASE
        a = np.asarray(tags, dtype=int).ravel()
        return np.where(a >= int(DOM_COIL_BASE), int(DOM_AIR), a)
    except Exception:                                       # noqa: BLE001
        return tags


#: The percentile the |B| colour scale stops at.  The same one the mechanical
#: pages have read stresses at since 2026-09-10, and for the same reason: a P2
#: field at a re-entrant corner has no maximum, only a mesh.
B_MAP_PCTILE = 99.5


def _em_maps(snap: Optional[Dict[str, Any]],
             ranges: Optional[Dict[str, Any]] = None,
             range_only: bool = False,
             width_cm: Optional[float] = None) -> Dict[str, Any]:
    """|B| and cycle-averaged loss density off the stored run field.

    Both come from the SAME arrays the field view draws (``P_mm``, ``T``,
    ``Bx``/``By``, ``loss_dens``), so the pictures in the report are the run's
    own, not a re-solve of it.  A key that is missing yields ``None`` and the
    page says the run did not store it.

    ``range_only``/``ranges`` are the pair contract of :func:`_map_png`: the
    first pass asks each side what scale it wants, the caller unions the two,
    the second pass draws both on it — see :func:`em_maps_pair`.
    """
    out: Dict[str, Any] = {"b": None, "loss": None, "az": None,
                           "label": None}
    if not snap:
        return out

    def _mp(key: str, *a: Any, **kw: Any) -> Any:
        """One map of this set, on the pair's scale when there is one."""
        if ranges and ranges.get(key) is not None:
            kw["pair_range"] = ranges[key]
        if range_only:
            kw["range_only"] = True
        kw.setdefault("width_cm", width_cm)
        return _map_png(*a, **kw)
    # A run SNAPSHOT wraps its arrays in `field`; the per-duty field store hands
    # them over flat (2026-09-10).  Accept both rather than make the caller know
    # which shape it holds.
    fld = snap.get("field") or (snap if snap.get("P_mm") is not None else {})
    try:
        import numpy as np
        P, T = fld.get("P_mm"), fld.get("T")
        bx, by = fld.get("Bx"), fld.get("By")
        # The AIR the model needs around the machine — the outer far-field
        # ring, the gap and the motion band — is most of the mesh's area and
        # carries almost no flux, so drawing it turns the picture into a small
        # coloured motor inside a large black disc.  Blanked, exactly as the
        # loss map blanks what no model wrote into.
        air = None
        try:
            from motor_ai_sim.simulation.sb_domains import (DOM_AIR, DOM_AIRGAP,
                                                            DOM_BAND, DOM_OUTER)
            tags = np.asarray(fld.get("tags"), int)
            air = np.isin(tags, (DOM_AIR, DOM_AIRGAP, DOM_BAND, DOM_OUTER))
        except Exception:                                   # noqa: BLE001
            air = None
        # Bx/By from a live run snapshot; `b_mag` from the per-duty field store,
        # which keeps the magnitude it drew rather than the two components.
        bmag = None
        if bx is not None and by is not None:
            bmag = np.hypot(np.asarray(bx, float), np.asarray(by, float))
        elif fld.get("b_mag") is not None:
            bmag = np.asarray(fld.get("b_mag"), float)
        if bmag is not None:
            if air is not None and air.shape == bmag.shape and (~air).any():
                bmag = np.where(air, np.nan, bmag)
            # THE SCALE IS CAPPED AT p99.5 (reviewer 2026-09-14): the top of the
            # bar was 3.599 T, which is no material's flux density — it is the
            # P2 field at a bridge or tooth-tip corner, a geometric singularity
            # that refines without bound.  The stress maps have been read at
            # p99.5 for the same reason since 2026-09-10; the raw maximum is
            # not hidden, it goes in the caption.
            _fin = bmag[np.isfinite(bmag)]
            if _fin.size:
                out["b_max_T"] = float(_fin.max())
                out["b_cap_T"] = float(np.percentile(_fin, B_MAP_PCTILE))
            out["b"] = _mp("b", P, T, bmag, label="|B|  [T]",
                           mask_nonfinite=True, grp=fld.get("tags"),
                           range_from="nodal", vmax_pct=B_MAP_PCTILE)
        # THE VECTOR POTENTIAL, with its own contours over it.  Air INCLUDED,
        # unlike |B|: the flux lines close through the gap and the air outside
        # the stator, and blanking that leaves the lines cut off at the iron —
        # the one field on this mesh whose picture needs the air.  No `grp`
        # either, for the same reason: A_z does not jump at a material boundary.
        az = fld.get("a_z_per_node")
        if az is None:
            az = fld.get("A") if fld.get("A") is not None else fld.get("A_z_per_node")
        if az is not None:
            a = np.asarray(az, float).ravel()
            # The mesh arrives either way round — (N, 2) from a live snapshot,
            # (2, N) out of the npz store — and `_as_xy` sorts that out; here
            # only the node COUNT matters, which is the long axis either way.
            _pn = int(max(np.asarray(P).shape)) if P is not None else 0
            if a.size == _pn and _pn:
                # …except the FAR-FIELD RING, which is dropped.  It is the air
                # the model needs out to the A_z = 0 boundary, it is bigger than
                # the machine, and nothing happens in it — drawn, it framed the
                # picture on a green disc with a small motor inside it.  The gap
                # and the slot air stay: that is where the flux actually closes.
                #
                # Cut on RADIUS rather than on the air tag: the model's outer
                # air is more than one domain and the machine's own radius is
                # the honest edge of the picture.  Everything that is not air is
                # kept, plus the air inside the largest radius any material
                # reaches — which keeps the slot air and the gap and drops the
                # ring around the outside.
                _keep = None
                try:
                    from motor_ai_sim.simulation.sb_domains import (
                        DOM_AIR, DOM_AIRGAP, DOM_BAND, DOM_OUTER)
                    _tg = np.asarray(fld.get("tags"), int).ravel()
                    _tt = np.asarray(T)
                    _tt = _tt.T if _tt.shape[0] == 3 and _tt.shape[1] != 3 else _tt
                    _pp = np.asarray(P, float)
                    _pp = _pp.T if _pp.shape[0] == 2 and _pp.shape[1] != 2 else _pp
                    if _tg.size == _tt.shape[0]:
                        _mat = ~np.isin(_tg, (DOM_AIR, DOM_AIRGAP, DOM_BAND,
                                              DOM_OUTER))
                        # OUTSIDE air is dropped, INSIDE air is kept — and
                        # "outside" is decided by connectivity, not by radius
                        # (user 2026-09-11: "зачем ты рисуешь эти вставки
                        # только на этом рисунке, убери их, чтобы было всё
                        # одинаково").  A radius cut kept the air in the
                        # scallops between the yoke humps, so this map alone
                        # grew green lobes the |B| map does not have.  The air
                        # that matters — gap, slots, bore — is walled in by iron
                        # or shaft and cannot be reached from the far field
                        # through air; a flood fill from the mesh's outer rim
                        # over air-to-air edges finds exactly the rest.
                        _keep = _mat.copy()
                        if _mat.any():
                            # Adjacency through shared NODES, not edges: on
                            # this machine the slots open OUTWARD, so slot air
                            # is genuinely outside air and must go with it —
                            # which is exactly what the |B| map's blanking does.
                            _air = ~_mat
                            import scipy.sparse as _sp
                            from scipy.sparse.csgraph import connected_components
                            _n, _nn = _tt.shape[0], _pp.shape[0]
                            _ai = np.where(_air)[0]
                            _A = _sp.coo_matrix(
                                (np.ones(_ai.size * 3),
                                 (np.repeat(_ai, 3), _tt[_ai].ravel())),
                                shape=(_n, _nn)).tocsr()
                            _ncomp, _lab = connected_components(
                                _A @ _A.T, directed=False)
                            _r = np.hypot(_pp[:, 0], _pp[:, 1])
                            _seed = _ai[_r[_tt[_ai]].max(axis=1)
                                        >= 0.98 * float(_r.max())]
                            _outside = np.isin(_lab, np.unique(_lab[_seed]))
                            _keep = _mat | (_air & ~_outside)
                except Exception:                           # noqa: BLE001
                    _keep = None
                out["az"] = _mp("az", P, T, a, label="A$_z$  [Wb/m]",
                                per_node=True, range_from="nodal",
                                iso_lines=_AZ_LINES, mesh=False,
                                only=_keep, outline=_az_outline(
                                    fld.get("tags")))

        ld = fld.get("loss_dens")
        if ld is not None and np.asarray(ld, float).size:
            # WHITE PART LINES here too (user 2026-09-10: "здесь нет белых линий
            # между магнитами и не видно бандажа").  `outline` only, never
            # `grp`: the log scale and the per-class averaging must not change,
            # the picture only has to say where one part ends.
            #
            # …and `context`, so a part that loses NOTHING is still on the
            # drawing.  The loss map blanks what no model wrote into — correct,
            # "not modelled" is not "no loss" — but blanking is also how the
            # retaining band disappeared: it is drawn in grey now, outlined like
            # everything else, and the reader can see it carries no loss instead
            # of not knowing whether it is there.
            _body = None
            if air is not None and air.shape == np.asarray(ld, float).shape:
                _body = ~air
            out["loss"] = _mp(
                "loss", P, T, ld, label="loss density  [W/m³]",
                log_scale=True, mask_nonpositive=True,
                outline=_az_outline(fld.get("tags")), context=_body)
            out["label"] = str(fld.get("loss_dens_label") or "")
        # The irreversible-demagnetisation map — per cent of Br the run left in
        # each magnet element (the Simulation tab's Demag view reads the same
        # array).  User 2026-09-08: "подписи под... демагнитизации обязательно
        # рисовать".  Magnets only: everything else is blanked.
        dc = (snap.get("scalars") or {}).get("demag_coef_per_tri")
        if dc is None:
            dc = fld.get("demag_coef_per_tri")
        if dc is not None and T is not None:
            coef = np.asarray(dc, float)
            # The triangle array arrives (3, N) from the npz store and (N, 3)
            # from a live snapshot; the element count is whichever axis is not
            # the corner index.  Reading `shape[1]` blind made this map vanish
            # silently on the other layout (same trap as the A_z map, 2026-09-11).
            _tsh = np.asarray(T).shape
            n_tri = (int(_tsh[0] if _tsh[1] == 3 and _tsh[0] != 3 else _tsh[1])
                     if len(_tsh) == 2 else 0)
            if coef.size == n_tri and n_tri > 0:
                mag = None
                body = None
                try:
                    from motor_ai_sim.simulation.sb_domains import (
                        DOM_COIL_BASE, DOM_MAG_BASE)
                    tags = np.asarray(fld.get("tags"), int)
                    if tags.shape == coef.shape:
                        # MAGNETS ONLY — bounded ABOVE as well (fixed
                        # 2026-09-11).  Magnet tags start at DOM_MAG_BASE (100)
                        # and conductor tags at DOM_COIL_BASE (200), so a bare
                        # `>= DOM_MAG_BASE` swept every coil into the magnet
                        # mask: the windings carry a demagnetisation
                        # coefficient of 1.0 (nothing lost, because there is
                        # nothing to lose) and came out painted at the top of
                        # the Br scale, the same dark red as a healthy magnet
                        # (user: "зачем ты здесь красным нарисовал катушки").
                        # Every other reader of this field in the app already
                        # bounds it — `routes/simulation.py` does it three
                        # times; this one did not.
                        mag = ((tags >= int(DOM_MAG_BASE))
                               & (tags < int(DOM_COIL_BASE)))
                        # the rest of the machine, in grey, behind the magnets
                        body = (~mag) & (~air if air is not None and
                                         air.shape == mag.shape else True)
                except Exception:                           # noqa: BLE001
                    mag = None
                # The solver stores a FACTOR (1.0 = nothing lost); the page
                # speaks per cent, like the Demag view and the summary line.
                shown = 100.0 * (np.where(mag, coef, np.nan)
                                 if mag is not None else coef)
                if np.isfinite(shown).any():
                    out["demag"] = _mp(
                        "demag", P, T, shown, label="Br remaining  [%]",
                        mask_nonfinite=True, vmax_fixed=100.0, context=body)
                    fin = shown[np.isfinite(shown)]
                    out["demag_min_pct"] = float(fin.min()) if fin.size else None
    except Exception as exc:                                # noqa: BLE001
        log.debug("report: EM maps failed (%s)", exc)
    return out


def _thermal_map(res: Optional[Dict[str, Any]],
                 pair_range: Optional[Tuple[float, float]] = None,
                 range_only: bool = False,
                 width_cm: Optional[float] = None) -> Any:
    if not isinstance(res, dict):
        return None
    inner = res.get("field") if isinstance(res.get("field"), dict) else res
    # Temperature is already NODAL and continuous across a material boundary —
    # it is one field, not one per part — so there is no class to split on; the
    # range is still the drawn field's own, like the tab's (2026-09-10).
    # The FIELD stays one continuous thing — temperature does not jump at a
    # material boundary, so it is neither grouped nor folded — but the machine
    # under it has to be recognisable, and on a thermal map the magnets are all
    # one warm red shape without it (user 2026-09-10: "здесь нет белых линий
    # между магнитами").  `domain_per_tri` is the thermal mesh's own part array:
    # magnet_N and magnet_S are different ids, so alternating poles separate.
    return _map_png(inner.get("vertices"), inner.get("triangles"),
                    inner.get("temperature_per_node"),
                    label="temperature  [°C]", per_node=True,
                    range_from="nodal", pair_range=pair_range,
                    range_only=range_only, width_cm=width_cm,
                    outline=_winding_as_one(inner.get("domain_per_tri")))


def _mech_map(res: Optional[Dict[str, Any]],
              pair_range: Optional[Tuple[float, float]] = None,
              range_only: bool = False,
              width_cm: Optional[float] = None) -> Tuple[Any, str]:
    """Von Mises over the primary case — the picture the Mechanical tab draws.

    Term for term, since 2026-09-10 (user, on the first docx: "с картинками
    полная жопа, они совершенно не похожи на то, что у нас в вебе"):

      * ``grp`` is the material class, so the average stops at every material
        boundary instead of smearing the band into the magnets under it — that
        one omission was most of the difference;
      * the colour range is the DRAWN field's own, the per-class nodal average,
        with no percentile clip.  Averaging is what tames a singular corner; the
        clip on top of it ended the bar at a value the picture never reaches,
        and it disagreed with the tab, which stopped clipping the same day.
    """
    if not isinstance(res, dict):
        return None, ""
    fld = res.get("field") or {}
    cases = fld.get("cases") or {}
    case = res.get("primary_case") or next(iter(cases), None)
    if not case or case not in cases:
        return None, ""
    vm = (cases[case] or {}).get("vm_per_tri")
    if vm is None:
        return None, ""
    png = _map_png(fld.get("vertices"), fld.get("triangles"), vm,
                   label="von Mises  [MPa]", grp=fld.get("domain_per_tri"),
                   range_from="nodal", pair_range=pair_range,
                   range_only=range_only, width_cm=width_cm)
    return png, str(case)


#: Where a rotor is judged.  Below this the part is not accepted; the safety-
#: factor map's bar stops at twice it, because "safer than 4" is one answer.
SF_ACCEPT = 2.0


def _mech_extra_maps(res: Optional[Dict[str, Any]],
                     ranges: Optional[Dict[str, Any]] = None,
                     range_only: bool = False,
                     width_cm: Optional[float] = None) -> Dict[str, Any]:
    """The DISPLACEMENT and the SAFETY FACTOR, beside the stress.

    User 2026-09-10: *"по механике нужно ещё выводить график деформаций и SF"*.
    Both were solved and shown on the tab and neither reached the document, so a
    reader could see where the metal is loaded but not how far it moves or how
    close it is to its own limit — and the limit is the answer the section is
    written to give.

    The safety factor's bar stops at 2x the acceptance line: it runs to 4,000 in
    a shaft that carries nothing, and a scale reaching that puts every part
    under test into one band.  Everything above the top band is safe by a
    factor of two over the line, which is the same statement.
    """
    out: Dict[str, Any] = {"disp": None, "sf": None, "disp_exagg": None}
    if not isinstance(res, dict):
        return out

    def _mp(key: str, *a: Any, **kw: Any) -> Any:
        if ranges and ranges.get(key) is not None:
            kw["pair_range"] = ranges[key]
        if range_only:
            kw["range_only"] = True
        kw.setdefault("width_cm", width_cm)
        return _map_png(*a, **kw)

    fld = res.get("field") or {}
    cases = fld.get("cases") or {}
    case = res.get("primary_case") or next(iter(cases), None)
    if not case or case not in cases:
        return out
    c = cases[case] or {}
    grp = fld.get("domain_per_tri")
    u = c.get("u_mag_per_node")
    if u is not None:
        # DRAWN DEFORMED, like the tab (user 2026-09-10: "этот график сделай как
        # в вэбе, с деформацией").  Microns on a 100 mm part are invisible at
        # true scale, so the shape is exaggerated by the same rule the viewer
        # uses: the largest displacement reads as ~5 % of the rotor radius.  The
        # COLOUR is still the true |u| in µm; only the geometry is stretched,
        # and the factor is printed in the caption so nobody reads the picture
        # as a shape.
        _v = fld.get("vertices")
        _uv = c.get("u_per_node")
        _k = 0.0
        try:
            import numpy as _np
            _V = _np.asarray(_v, float)
            _U = _np.asarray(_uv, float) * 1e-3          # µm -> mm
            if _U.shape == _V.shape and _U.size:
                _umax = float(_np.hypot(_U[:, 0], _U[:, 1]).max())
                _rout = float(_np.hypot(_V[:, 0], _V[:, 1]).max())
                if _umax > 0 and _rout > 0:
                    _k = 0.05 * _rout / _umax
                    _v = _V + _k * _U
        except Exception:                                # noqa: BLE001
            _v, _k = fld.get("vertices"), 0.0
        out["disp_exagg"] = round(_k, 1) if _k else None
        out["disp"] = _mp("disp", _v, fld.get("triangles"), u,
                          label="displacement |u|  [µm]", per_node=True,
                          grp=grp, range_from="nodal")
    sf = c.get("sf_per_tri")
    if sf is not None:
        out["sf"] = _mp("sf", fld.get("vertices"), fld.get("triangles"), sf,
                        label="safety factor  [-]", grp=grp,
                        vmax_fixed=2.0 * SF_ACCEPT, reverse=True)
    return out


# ── ONE FIGURE, TWO DUTIES ──────────────────────────────────────────────────
#
# User 2026-09-14: *"добавим ещё картинки из peak — слева картинка из rated,
# справа из peak"*.  Every picture in this document used to be ONE duty's — the
# `pictures` choice — and the reader who wanted to know what the peak does to
# the magnets, the temperatures or the stress had to build a second report and
# put the two on a desk.  So every per-duty figure is drawn twice, rated on the
# left and the other duty on the right, on ONE colour scale per quantity and in
# one frame, with one caption naming both sides.
#
# WHAT IS NOT PAIRED: the die's cross-section, the mass pie, the inertia pie,
# the mode gallery and the Campbell diagram.  There is one rotor and one
# machine, and drawing the same picture twice says there are two.


# ── EVERY SIDE ON ITS OWN SCALE ─────────────────────────────────────────────
#
# User 2026-09-14, after seeing the first paired build: *"не надо общей шкалы,
# шкалы как и рисунки должны быть отдельные; но магниты на первом рисунке
# должны быть красными"*.  The shared bar had been introduced so 132 °C and
# 249 °C could be read against each other; what it actually did was flatten the
# quieter of the two pictures — the rated temperature map spans 23 K against
# the peak's 83, so on the union it came out as one teal shape with two band
# edges crossing it and its hottest part, the magnets, was not red.
#
# So each half is drawn EXACTLY as it would be drawn alone: its own vmin/vmax,
# its own colour bar, its own bands.  What stays identical is the FORMAT — the
# same mesh, the same framing, the same figure geometry, the same bar, the same
# line weights and the same point sizes on both sides — and that is what makes
# the two read as one figure.  The per-side numbers in the caption are how the
# two are compared.


def em_maps_pair(left: Optional[Dict[str, Any]],
                 right: Optional[Dict[str, Any]],
                 width_cm: Optional[float] = None
                 ) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """The electromagnetic maps of both sides, each on its own scale."""
    return (_em_maps(left, width_cm=width_cm) if left else {},
            _em_maps(right, width_cm=width_cm) if right else None)


def thermal_map_pair(left: Optional[Dict[str, Any]],
                     right: Optional[Dict[str, Any]],
                     width_cm: Optional[float] = None
                     ) -> Tuple[Optional[bytes], Optional[bytes]]:
    """Both temperature maps, each on its own scale."""
    return (_thermal_map(left, width_cm=width_cm) if left else None,
            _thermal_map(right, width_cm=width_cm) if right else None)


def mech_map_pair(left: Optional[Dict[str, Any]],
                  right: Optional[Dict[str, Any]],
                  width_cm: Optional[float] = None
                  ) -> Tuple[Tuple[Any, str], Tuple[Any, str]]:
    """Both von Mises maps, each on its own scale."""
    return (_mech_map(left, width_cm=width_cm) if left else (None, ""),
            _mech_map(right, width_cm=width_cm) if right else (None, ""))


def mech_extra_pair(left: Optional[Dict[str, Any]],
                    right: Optional[Dict[str, Any]],
                    width_cm: Optional[float] = None
                    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Displacement and safety factor, both sides, each on its own scale."""
    return (_mech_extra_maps(left, width_cm=width_cm) if left else {},
            _mech_extra_maps(right, width_cm=width_cm) if right else {})


def pair_duties(duties: List[Dict[str, Any]], die: str, cfg: str,
                active: Optional[str], pictures: Optional[str],
                fallback: Optional[str] = None
                ) -> Tuple[Optional[str], Optional[str], str]:
    """``(left, right, note)`` — WHICH two duties the pictures are of.

    Left is the rated duty, always: it is the point the machine is sold at and
    the one the document is written around.  Right is the other one — with
    exactly two duties there is no choice to make, and with more than two the
    user's own ``pictures`` pick names it (the peak when that pick IS the rated
    duty).  One duty means no pair at all and the pages draw the single figure
    they always drew.
    """
    names = [str(d.get("name") or "") for d in duties if d.get("name")]
    if len(names) < 2:
        return (fallback or (names[0] if names else None)), None, ""

    # THE RATED DUTY IS ON THE LEFT, and it is the one whose NAME says rated —
    # `_rated_duty` falls back to any duty that has a stored field, which on a
    # configuration where only the peak was ever solved is the peak, and "left
    # is rated" then quietly stopped being true.  A rated duty that HAS fields
    # wins over one that has none; with no rated duty at all the stored-field
    # rule decides, exactly as it does for the single-figure pages.
    rated = [n for n in names if n.strip().lower().startswith("rated")]
    pick = _rated_duty(duties, die, cfg, active)
    left = (next((n for n in rated if n == pick), None)
            or (rated[0] if rated else None)
            or pick
            or (fallback if fallback in names else None)
            or names[0])
    rest = [n for n in names if n != left]
    if not rest:
        return left, None, ""
    if len(names) == 2:
        right = rest[0]
    elif pictures and str(pictures) in rest:
        right = str(pictures)
    else:
        right = next((n for n in rest if "peak" in n.lower()), rest[0])
    note = ("" if len(names) == 2 else
            "2 of the %d duties of this configuration" % len(names))
    return left, right, note


def _side_thermal(die: str, cfg: str, duty: Optional[str],
                  src: Dict[str, Any], th: Dict[str, Any],
                  owner: Optional[str]) -> Optional[Dict[str, Any]]:
    """The thermal RESULT one side of a pair is drawn from.

    The page's own entry when this side is the duty that entry belongs to — so
    the left-hand picture and the table beside it stay one answer — and the
    duty's own stored record otherwise.  ``None`` when that duty was never
    solved thermally, and the caption then says so.
    """
    entry = (th or {}).get("field") or (th or {}).get("coupled")
    if entry and duty and owner and str(duty) == str(owner):
        return entry.get("result") or {}
    rec = _duty_detail_sources(die, cfg, duty, src or {})
    e = (rec.get("th") or {}).get("field")
    return (e or {}).get("result") if e else None


def _side_mech(die: str, cfg: str, duty: Optional[str],
               src: Dict[str, Any], me: Dict[str, Any],
               owner: Optional[str]) -> Optional[Dict[str, Any]]:
    """…and the rotor-stress result, by the same rule."""
    entry = (me or {}).get("rotor_stress")
    if entry and duty and owner and str(duty) == str(owner):
        return entry.get("result") or {}
    rec = _duty_detail_sources(die, cfg, duty, src or {})
    e = (rec.get("me") or {}).get("rotor_stress")
    return (e or {}).get("result") if e else None


def _side_case(res: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The primary case of a rotor-stress result, or ``{}``."""
    if not isinstance(res, dict):
        return {}
    name = res.get("primary_case") or next(iter(res.get("cases") or {}), None)
    return ((res.get("cases") or {}).get(name) or {}) if name else {}


def _worst_sf(case: Optional[Dict[str, Any]]) -> Optional[float]:
    """The lowest safety factor of a rotor-stress case — the one number a
    mechanical picture is read for."""
    if _numf((case or {}).get("sf_min")) is not None:
        return _numf(case["sf_min"])
    vals = [_numf((p or {}).get("safety_factor"))
            for p in ((case or {}).get("parts") or {}).values()]
    vals = [v for v in vals if v is not None]
    return min(vals) if vals else None


def _peak_vm(case: Optional[Dict[str, Any]]) -> Optional[float]:
    """The highest AVERAGED von Mises of a case — the number a stress map is
    read for, and the one the table beside it prints."""
    vals = [_numf((p or {}).get("von_mises_max_mpa"))
            for p in ((case or {}).get("parts") or {}).values()]
    vals = [v for v in vals if v is not None]
    return max(vals) if vals else None


def mech_case_words(left: Any, right: Any) -> str:
    """``'14,200 rpm' / '20,000 rpm'`` — the two cases a paired stress figure
    is drawn over, or the one they share."""
    a, b = str(left or "").strip(), str(right or "").strip()
    if a and b and a != b:
        return "the '%s' / '%s' cases" % (a, b)
    return "the '%s' case" % (a or b or "—")


def map_kind_clause(kind: str, left: Optional[Dict[str, Any]],
                    right: Optional[Dict[str, Any]]) -> str:
    """The provenance sentence a two-up MAP caption carries, or ``""``.

    One sentence when both sides tell the same story (they were refiled by the
    same run, so they usually do), one per side when they do not.
    """
    if not kind:
        return ""
    l = str(((left or {}).get("prov") or {}).get(kind) or "").strip()
    r = str(((right or {}).get("prov") or {}).get(kind) or "").strip()
    if not l and not r:
        return ""
    if l and r and l == r:
        return l + " on both sides"
    bits = []
    if l:
        bits.append("left: %s" % l)
    if r:
        bits.append("right: %s" % r)
    return "; ".join(bits)


def caption_with_provenance(caption: str, side: Optional[Dict[str, Any]],
                            kind: str) -> str:
    """A SINGLE figure's caption with the provenance sentence appended, or the
    caption unchanged when the map and its table are one solve."""
    note = str(((side or {}).get("prov") or {}).get(kind) or "").strip()
    if not note:
        return caption
    txt = (caption or "").rstrip()
    if txt.endswith("."):
        txt = txt[:-1]
    return "%s; %s." % (txt, note)


def pair_caption(what: str, left: Optional[Dict[str, Any]],
                 right: Optional[Dict[str, Any]], *,
                 have: Tuple[bool, bool] = (True, True),
                 numbers: str = "", note: str = "",
                 map_kind: str = "") -> str:
    """One caption for a two-up figure.

    ``<what it shows>. Left: rated, 562.1 A rms at 14,200 rpm; right: peak,
    770.5 A rms at 20,000 rpm; worst element 98.4 % / 18.4 %`` — one sentence
    for the figure, then who is on which side and the one number per side that
    matters.  A side with nothing stored is NAMED rather than drawn empty.
    """
    txt = (what or "").strip()
    if txt and not txt.endswith("."):
        txt += "."

    def _side(s: Optional[Dict[str, Any]]) -> str:
        if not s:
            return "—"
        pt = str(s.get("point") or "").strip()
        # THE SUPPLY OF EACH HALF (MJ-6, audit v6).  Fig. 4's left trace carries
        # 28.7 % carrier ripple and its right one 1 %, Fig. 6's left voltages
        # are switched and its right are sine, and no caption said so.
        sup = str(s.get("supply_short") or "").strip()
        return ", ".join(b for b in (str(s.get("duty") or "—"),
                                     pt or "operating point not recorded",
                                     sup) if b)

    l_ok, r_ok = have
    if l_ok and r_ok:
        clause = "Left: %s; right: %s" % (_side(left), _side(right))
    elif l_ok or r_ok:
        one, other = (left, right) if l_ok else (right, left)
        clause = ("Drawn from %s alone — the duty '%s' has nothing stored for "
                  "this figure" % (_side(one), (other or {}).get("duty") or "—"))
    else:
        clause = "Neither duty has anything stored for this figure"
    for extra in (numbers, note, map_kind_clause(map_kind, left, right)):
        if str(extra or "").strip():
            clause += "; " + str(extra).strip()
    return ((txt + " ") if txt else "") + clause + "."


def fig_ahead(counter: Optional[List[int]], n: int = 1) -> Optional[int]:
    """The number the NEXT figure will get, without taking it.

    CS-8 (audit v6): the document numbers twenty figures and no sentence in it
    ever cites one, so the numbering existed only for a reader to quote back.
    Both renderers count in the same order, so a lead-in that reads the counter
    before its section draws anything names the same figure in both.
    """
    if counter is None:
        return None
    return int(counter[0] or 0) + int(n)


def pair_owner_text(what: str, pair: Optional[Dict[str, Any]],
                    tail: str = "", first_fig: Optional[int] = None) -> str:
    """The section's opening line when its pictures are a PAIR.

    The single-figure wording — "the stored field of the duty 'rated'" — is a
    false statement about a page carrying both duties, and it is the first line
    of the section (2026-09-14).  ``""`` when there is no pair, and the caller
    keeps the sentence it has always printed.
    """
    left, right = (pair or {}).get("left"), (pair or {}).get("right")
    if not (left and right):
        return ""

    def _one(s: Dict[str, Any]) -> str:
        pt = str(s.get("point") or "").strip()
        return "'%s'%s" % (s.get("duty") or "—", (" at %s" % pt) if pt else "")

    # "IN THIS SECTION", not "on this page" (MJ-6, audit v5): the figures the
    # sentence introduces are spread over two and three pages, and in section 4
    # the sentence itself used to arrive after the last of them.
    # …and each side on its OWN scale since 2026-09-14 — see the pair helpers.
    return ("%s in this section (from Fig. %d) are drawn for BOTH duties, each "
            "on its own scale: the stored fields of %s on the left and %s on "
            "the right.%s"
            % (what, int(first_fig), _one(left), _one(right),
               (" " + tail) if tail else "")
            if first_fig else
            "%s in this section are drawn for BOTH duties, each on its own "
            "scale: the stored fields of %s on the left and %s on the "
            "right.%s"
            % (what, _one(left), _one(right), (" " + tail) if tail else ""))


def pair_owner_tail(kind: str, map_duty: Optional[str], from_duty: bool,
                    extra: str = "") -> str:
    """…and whatever else the section's lead-in has to add.

    IT NO LONGER NAMES THE SERVER (client review 2026-09-14).  The clause this
    used to add — "The machine's last <kind> answer is the duty 'X' — the one
    loaded on this server." — is a remark about the engineer's session, not
    about the machine: the document is built from the stored duties of one
    configuration, and the duty a side belongs to is already named in the
    sentence this tail hangs off.  ``kind``, ``map_duty`` and ``from_duty`` are
    kept in the signature so both renderers keep their call sites.
    """
    return extra


def fig_no(counter: Optional[List[int]]) -> int:
    """The next figure number of this document (the two renderers each keep
    their own count, and both count in the same order)."""
    if counter is None:
        return 0
    counter[0] = int(counter[0] or 0) + 1
    return counter[0]


#: The one caption the machine's own drawing carries, in both renderers.
DIE_SECTION_CAPTION = (
    "The die's own cross-section — the shape the solver meshed, not a "
    "screenshot.")


def fig_label(counter: Optional[List[int]], what: str, tag: str = "") -> str:
    """``Fig. 7 — <what> Duty 'rated', 562.1 A rms at 14,200 rpm.``

    ONE CAPTION CONVENTION PER DOCUMENT (CS-5, audit v5).  A document that
    numbers its figures — one that draws pairs — numbered the fifteen paired
    ones and left the four single ones reading ``Fig. [duty '…'] — …``, so two
    conventions sat side by side and neither could be cited.  ``counter=None``
    is the un-numbered document (one duty, no pairs) and keeps the bracketed
    form, which is internally consistent there.
    """
    txt = (what or "").strip()
    if counter is None:
        return "Fig. [%s] — %s" % (tag, txt)
    if tag:
        if txt and not txt.endswith("."):
            txt += "."
        loc = str(tag).strip()
        loc = loc[0].upper() + loc[1:]
        txt = ((txt + " ") if txt else "") + loc.rstrip(".") + "."
    return "Fig. %d — %s" % (fig_no(counter), txt)


def pair_number_clause(label: str, left: Any, right: Any,
                       d: int = 1, unit: str = "") -> str:
    """``worst element 98.4 % / 18.4 %`` — the one number per side."""
    if _numf(left) is None and _numf(right) is None:
        return ""
    return "%s %s / %s" % (label, _fmt(left, d, unit), _fmt(right, d, unit))


#: The two charts that sit BESIDE a table rather than under a picture.  Drawn at
#: print size, like the mode gallery, so their labels are the document's own
#: point size (see `MODES_GALLERY_CM`).
SIDE_CHART_CM = 11.0
SIDE_CHART_PX = 1500

#: One colour per loss term, held still across every chart in the document so a
#: slice, a bar and a row are the same thing in three places.  Copper is the
#: app's own winding orange, the irons its steel blues, the rotating parts its
#: magnet red — the palette a reader already associates with those parts.
#: …and one colour per PART, for the pies that show the same machine twice.
#: Keyed by a keyword of the name, because the mass rows call a magnet
#: "Magnets" and the inertia block calls it "magnet".  Colours are the app's own
#: part palette (`web/src/lib/partColors.ts`), so a slice is the colour that
#: part has in the 3-D view and on the cross-section.
PART_COLOURS = {
    # BRIGHT AND APART (user 2026-09-11: "цвета сделай поярче, чтобы хорошо
    # было видно разницу").  The viewer's own palette is three near-identical
    # navies plus two near-blacks — right for a cross-section, where the shapes
    # separate the parts and the colours only have to be quiet, and wrong for a
    # pie, where the colour IS the only thing telling two wedges apart.  Copper
    # keeps its orange and the magnets their red, because those two carry
    # meaning; the irons and the small parts get hues far enough from each
    # other to read at a glance and at a slice a millimetre wide.
    "stator": "#3d6fb4",      # steel blue
    "rotor": "#14b8a6",       # teal — the other iron, unmistakably not the first
    "copper": "#e0821a", "winding": "#e0821a",
    "magnet": "#e02718",
    "shaft": "#8b5cf6",       # violet
    "sleeve": "#1E7A3C",      # green
}


def part_colour(name: Any, default: str = "#9aa4b2") -> str:
    """The palette colour of a part, matched on a keyword of its name."""
    n = str(name or "").lower()
    for key, col in PART_COLOURS.items():
        if key in n:
            return col
    return default


LOSS_COLOURS = {
    "Copper": "#e0821a", "Iron (stator)": "#42526b", "Iron (rotor)": "#5c7091",
    "Magnets": "#e02718", "Shaft": "#2b3648", "Sleeve": "#1f2937",
    "Bearings": "#8b5cf6", "Windage": "#2e86ff",
}


def loss_breakdown(em: Dict[str, Any],
                   brg: Optional[Dict[str, Any]]) -> List[Tuple[str, float]]:
    """``[(term, watts)]`` for the pie — the SAME numbers as `em_loss_rows`.

    The iron split is used when the run measured one, and the solid parts are
    listed apart when the run carries them apart; otherwise the sums stand
    under their honest label.  Zero and missing terms are dropped: a slice of
    nothing is a label with a line pointing at nothing.
    """
    out: List[Tuple[str, float]] = []

    def add(label, v):
        f = _numf(v)
        if f is not None and abs(f) > 1e-9:
            out.append((label, abs(f)))

    add("Copper", _g(em, "P_stranded_W"))
    if _g(em, "P_core_stator_W") is not None:
        add("Iron (stator)", _g(em, "P_core_stator_W"))
        add("Iron (rotor)", _g(em, "P_core_rotor_W"))
    else:
        add("Iron (stator)", _g(em, "P_core_W"))
    if _g(em, "P_mag_W") is not None or _g(em, "P_shaft_W") is not None:
        add("Magnets", _g(em, "P_mag_W"))
        add("Shaft", _g(em, "P_shaft_W"))
        add("Sleeve", _g(em, "P_sleeve_W"))
    else:
        add("Magnets", _g(em, "P_solid_W"))
    if brg and brg.get("has_bearings"):
        add("Bearings", brg.get("P_bearings_W"))
        add("Windage", brg.get("P_windage_W"))
    return out


def _loss_pie_png(em: Dict[str, Any], brg: Optional[Dict[str, Any]],
                  width_cm: float = SIDE_CHART_CM,
                  px: int = SIDE_CHART_PX) -> Optional[bytes]:
    """Where the watts go, as a pie (user 2026-09-11: "круговую диаграмму всех
    потерь справа от таблицы — будет гораздо наглядней").

    Every slice is labelled with its watts AND its share, because a pie alone
    answers "which is biggest" and never "how much" — and the table beside it
    would then be the only place with the number, which defeats putting them
    side by side.  Slices under 2 % get their label on a leader outside the
    circle rather than inside a wedge too thin to hold it.
    """
    terms = loss_breakdown(em, brg)
    if not terms:
        return None
    try:
        import numpy as np
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        labels = [t for t, _v in terms]
        vals = np.array([v for _t, v in terms], float)
        tot = float(vals.sum())
        if tot <= 0:
            return None
        cols = [LOSS_COLOURS.get(l, "#9aa4b2") for l in labels]
        fig_w = max(float(width_cm), 4.0) / 2.54
        # WHERE THE LEGEND GOES depends on how wide the figure is placed (user
        # 2026-09-14: *"раздвигай на всю ширину страницы"*).  As half of a pair
        # the legend beside the pie ate half the column and left the circle the
        # size of a coin, so it goes UNDERNEATH and the pie fills the width; at
        # full page width there is room beside it, and a full-width pie with
        # the legend under it would be taller than the page.
        _half = _narrow(width_cm)
        fig, ax = plt.subplots(figsize=(fig_w, fig_w * (0.75 if _half else 0.42)),
                               dpi=max(160.0, float(px) / fig_w))

        # ONE DECIMAL, the legend's and the table's (reviewer 2026-09-14, D1),
        # and only on a wedge wide enough to hold two lines of it (MJ-9).
        def _auto(pct):
            # matplotlib hands this a float32; make it a real float before it
            # reaches the formatter (CS-2).
            pct = float(pct)
            w = pct / 100.0 * tot
            return (("%s\n%.1f %%" % (_fmt(w, 1, "W"), pct))
                    if pct >= PIE_LABEL_MIN_PCT else "")

        wedges, _texts, autotexts = ax.pie(
            vals, colors=cols, startangle=90, counterclock=False,
            autopct=_auto, pctdistance=0.58, radius=1.0,
            wedgeprops={"linewidth": 0.8, "edgecolor": "white"},
            textprops={"fontsize": 8.5, "color": "white"})
        for t in autotexts:
            t.set_fontweight("bold")
        # the legend carries every term, including the ones too thin to label
        _lg = ["%s — %s (%.1f %%)" % (l, _fmt(v, 1, "W"), 100.0 * v / tot)
               for l, v in zip(labels, vals)]
        if _half:
            ax.legend(wedges, _lg, loc="upper center",
                      bbox_to_anchor=(0.5, -0.02), frameon=False,
                      fontsize=CHART_LABEL_PT, ncol=2,
                      columnspacing=1.2, handlelength=1.2)
        else:
            ax.legend(wedges, _lg, loc="center left",
                      bbox_to_anchor=(0.98, 0.5), frameon=False,
                      fontsize=CHART_TICK_PT, handlelength=1.2)
        ax.set_title("Total %s" % _fmt(tot, 0, "W"), fontsize=10.5, pad=4)
        ax.set_aspect("equal")
        buf = io.BytesIO()
        fig.savefig(buf, format="png", facecolor="white", bbox_inches="tight",
                    pad_inches=0.04)
        plt.close(fig)
        return buf.getvalue()
    except Exception as exc:                                # noqa: BLE001
        log.debug("report: loss pie failed (%s)", exc)
        try:
            import matplotlib.pyplot as plt
            plt.close("all")
        except Exception:                                   # noqa: BLE001
            pass
        return None


def _temp_bars_png(res: Dict[str, Any], inner: Dict[str, Any],
                   limits: Optional[Dict[str, float]] = None,
                   width_cm: float = SIDE_CHART_CM,
                   px: int = SIDE_CHART_PX) -> Optional[bytes]:
    """Per-part temperature as bars (user 2026-09-11: "график температур в виде
    гистограммы справа от таблицы температур").

    Each part gets its maximum as the bar and its average as a tick inside it —
    the two numbers the table carries, so nothing new is asserted.  The limits
    that apply are drawn as vertical lines: the winding's insulation class and
    the magnets' grade, which are what the reader is measuring the bars against.
    """
    comps = (res or {}).get("components") or (inner or {}).get("components") or {}
    order = [("winding", "Winding"), ("enamel", "Wire enamel"),
             ("liner", "Slot insulation"), ("slot_fill", "Slot fill"),
             ("magnet", "Magnets"), ("rotor", "Rotor core"),
             ("sleeve", "Sleeve"), ("shaft", "Shaft"),
             ("stator", "Stator core"), ("gap_air", "Air gap")]
    rows = [(lbl, _numf((comps.get(k) or {}).get("max")),
             _numf((comps.get(k) or {}).get("avg")))
            for k, lbl in order if isinstance(comps.get(k), dict)]
    rows = [r for r in rows if r[1] is not None]
    if not rows:
        return None
    try:
        import numpy as np
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        labels = [r[0] for r in rows][::-1]          # first row on top
        mx = np.array([r[1] for r in rows], float)[::-1]
        av = np.array([(r[2] if r[2] is not None else np.nan)
                       for r in rows], float)[::-1]
        fig_w = max(float(width_cm), 4.0) / 2.54
        fig, ax = plt.subplots(figsize=(fig_w, fig_w * 0.80),
                               dpi=max(160.0, float(px) / fig_w))
        hot = float(np.nanmax(mx))
        lim_vals = [v for v in (limits or {}).values() if _numf(v)]
        span = max([hot] + [float(v) for v in lim_vals]) * 1.18
        # COLOUR BY THE PART'S OWN LIMIT, not by how it ranks against the
        # hottest part: a sleeve at 155 °C is not "nearly failing" because the
        # air gap happens to be the hottest thing in the machine.  A part with
        # no limit of its own stays neutral — the bar is a measurement, and
        # pretending otherwise is the mistake the first draft made.
        _PART_LIMIT = {"Winding": "insulation", "Wire enamel": "insulation",
                       "Slot insulation": "insulation", "Slot fill": "insulation",
                       "Magnets": "magnets"}
        cols = []
        for lbl, v in zip(labels, mx):
            lim = _numf((limits or {}).get(_PART_LIMIT.get(lbl, "")))
            if lim is None:
                cols.append("#2e86ff")
            elif v >= lim:
                cols.append("#e02718")
            elif v >= lim * (1.0 - NEAR_PCT / 100.0):
                cols.append("#e0821a")
            else:
                cols.append("#1E7A3C")
        ax.barh(labels, mx, color=cols, height=0.62, zorder=2)
        for i, (m, a) in enumerate(zip(mx, av)):
            if np.isfinite(a):
                ax.plot([a, a], [i - 0.31, i + 0.31], color="#1f2937",
                        lw=1.4, zorder=3)
            # PAST THE AVERAGE TICK TOO (MJ-3): on a part whose average is its
            # maximum the tick was drawn straight through the value label.
            _x = max(float(m), float(a) if np.isfinite(a) else float(m))
            # …on its own white ground, so a limit line that happens to fall
            # where the number is does not print through it (MJ-3).
            ax.text(_x + span * 0.02, i, _fmt(m, 1), va="center",
                    fontsize=CHART_TICK_PT, zorder=5,
                    bbox={"facecolor": "white", "edgecolor": "none",
                          "pad": 0.8})
        # THE LIMIT LABELS GO ABOVE THE PLOT, not inside it (MJ-3, audit v5).
        # Inside, "magnets 180 °C" and "insulation 200 °C" printed over each
        # other at the top of the axes and over the value label of whichever
        # bar reached them.  Above the top bar they have a row each and nothing
        # to collide with; the dashed lines still span the whole axis.
        _lims = sorted(((k, _numf(x)) for k, x in (limits or {}).items()
                        if _numf(x) is not None), key=lambda kv: kv[1])
        _lo, _hi = -0.6, len(labels) - 0.5 + 0.62 * len(_lims) + 0.35
        # the dashed line stops where the bars stop, so it cannot be drawn
        # through the label that names it
        _top = (len(labels) - 0.5 - _lo) / (_hi - _lo)
        for _i, (name, v) in enumerate(_lims):
            ax.axvline(v, ymin=0.0, ymax=_top, color="#B3261E", lw=1.2,
                       ls="--", zorder=4)
            ax.text(v, len(labels) - 0.45 + 0.62 * (len(_lims) - _i),
                    " %s %s" % (name, _fmt(v, 0, "°C")),
                    color="#B3261E", fontsize=CHART_LABEL_PT, va="center",
                    ha="center" if v > 0.75 * span else "left", zorder=4)
        ax.set_ylim(_lo, _hi)
        ax.set_xlim(0, span)
        ax.set_xlabel("°C   (bar = maximum, tick = average)",
                      fontsize=CHART_TICK_PT)
        ax.tick_params(labelsize=CHART_TICK_PT)
        ax.grid(axis="x", color="#e6e6e6", lw=0.6, zorder=0)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
        buf = io.BytesIO()
        fig.savefig(buf, format="png", facecolor="white", bbox_inches="tight",
                    pad_inches=0.04)
        plt.close(fig)
        return buf.getvalue()
    except Exception as exc:                                # noqa: BLE001
        log.debug("report: temperature bars failed (%s)", exc)
        try:
            import matplotlib.pyplot as plt
            plt.close("all")
        except Exception:                                   # noqa: BLE001
            pass
        return None


def temp_chart_limits(mats: Dict[str, Any],
                      ctxs: Dict[str, Any]) -> Dict[str, float]:
    """The two temperature limits worth drawing on the bar chart: the winding's
    insulation class and the magnet grade's.  Both come from the same places the
    warnings engine reads, so a bar past a line and a red row are one fact."""
    out: Dict[str, float] = {}
    lim, _n = _insulation_limit(mats)
    if lim:
        out["insulation"] = float(lim)
    mag = next((_numf((c or {}).get("magnet_limit_c"))
                for c in (ctxs or {}).values()
                if (c or {}).get("magnet_limit_c") is not None), None)
    if mag is None:
        mag, _mn = _magnet_limit(mats.get("magnet"))
    if mag:
        out["magnets"] = float(mag)
    return out


def duty_waveforms(die: str, cfg: str, duty: Optional[str],
                   cfg_doc: Dict[str, Any],
                   drive: Optional[str] = None) -> Dict[str, Any]:
    """The WAVEFORMS of one duty's saved run — torque, currents, voltages.

    Every duty files its transient in a gzip sidecar beside the catalog (see
    `family._store_run`); the summary next to it keeps only the scalars.  The
    charts the user asked for on 2026-09-11 — torque with its harmonics, the
    phase currents, the line voltages — are all in that sidecar, so none of
    them costs a solve.  ``{}`` when the duty has no stored run, which is the
    normal state of a duty saved before the waveforms were kept.

    ``drive`` asks for ONE excitation's run and refuses to substitute another:
    a PWM duty's charts must be the PWM run's or be labelled as the sinusoid's
    (2026-09-14), and silently handing back the sine waveforms under a PWM
    caption is the one answer this function may not give.
    """
    if not duty:
        return {}
    try:
        from motor_ai_sim.routes import family as _fam
        d = next((x for x in (cfg_doc.get("duties") or [])
                  if isinstance(x, dict) and str(x.get("name") or "") == duty),
                 None)
        runs = (d or {}).get("runs") or {}
        if drive:
            run = runs.get(drive)
            if not isinstance(run, dict):
                return {}
        else:
            run = runs.get(_fam._primary_drive(d) or "current") or \
                next((r for r in runs.values() if isinstance(r, dict)), None)
        rel = (run or {}).get("payload_file")
        if not rel:
            return {}
        return _fam._read_run_payload(die, str(rel)) or {}
    except Exception as exc:                                # noqa: BLE001
        log.debug("report: no stored waveforms for %r (%s)", duty, exc)
        return {}


def _dft_pct(series: Any, n_orders: int = 20) -> Optional[Tuple[Any, Any, float]]:
    """``(orders, amplitude as % of the fundamental, THD %)`` of one period.

    The series is ONE electrical period as the run swept it, so a plain DFT is
    the spectrum — no windowing, no interpolation, nothing that could invent a
    harmonic the solve did not produce.
    """
    try:
        import numpy as np
        y = np.asarray(series, float)
        y = y[np.isfinite(y)]
        if y.size < 8:
            return None
        sp = np.fft.rfft(y - y.mean()) * 2.0 / y.size
        amp = np.abs(sp)
        if amp.size < 2 or amp[1] <= 0:
            return None
        k = min(int(n_orders), amp.size - 1)
        pct = 100.0 * amp[1:k + 1] / amp[1]
        thd = 100.0 * float(np.sqrt((amp[2:] ** 2).sum())) / float(amp[1])
        return np.arange(1, k + 1), pct, thd
    except Exception:                                       # noqa: BLE001
        return None


#: Under this width a chart is half of a PAIR, and the three waveform charts
#: are stretched by `PAIR_CHART_TALLER`: at full width a strip of aspect 0.30 is
#: 24 cm by 7 cm and reads; at 8 cm it is 1.6 cm tall, which is all axis and no
#: picture.  The point sizes are left alone — a chart drawn at the width it is
#: placed at renders its 9 pt labels as 9 pt on the page either way.
#:
#: 14 cm, not 12 (user 2026-09-14: *"рисунки делай побольше, раздвигай на всю
#: ширину страницы, для всех, чтобы одинаково было"*).  A pair now spans the
#: whole text width, so a half is 13.2 cm in Word and 9.2 cm in the PDF — and
#: with the threshold at 12 the two renderers would have laid the same figure
#: out two different ways.  Half of a pair is half of a pair in both.
PAIR_CHART_CM = 14.0
PAIR_CHART_TALLER = 1.85


def _chart_aspect(aspect: float, width_cm: float) -> float:
    """The height/width a chart is drawn at, taller when it is half of a pair."""
    return float(aspect) * (PAIR_CHART_TALLER
                            if float(width_cm) < PAIR_CHART_CM else 1.0)


def _narrow(width_cm: float) -> bool:
    """Is this chart one HALF of a pair?"""
    return float(width_cm) < PAIR_CHART_CM


def _chart_title(text: str, width_cm: float, cols: int = 46) -> str:
    """A long title WRAPS on a half-width chart rather than stretching it.

    `savefig(bbox_inches="tight")` grows the figure around a title wider than
    its axes, and the whole picture is then scaled down to the column width —
    so one long title shrinks the curves and every label under them.  Wrapped,
    the figure keeps its width and the title takes a second line.
    """
    if not _narrow(width_cm):
        return text
    import textwrap
    return "\n".join(textwrap.wrap(str(text), cols)) or str(text)


def _chart_fs(base: float, width_cm: float) -> float:
    """Point size for a chart label — a touch smaller on half of a pair, so a
    title and a tick label do not run into the picture beside them."""
    return float(base) if not _narrow(width_cm) else max(7.0, float(base) - 1.5)


def _fig(width_cm: float, px: int, aspect: float = 0.62):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    w = max(float(width_cm), 4.0) / 2.54
    return plt.subplots(figsize=(w, w * aspect),
                        dpi=max(160.0, float(px) / w))


def _finish(fig) -> bytes:
    import matplotlib.pyplot as plt
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor="white", bbox_inches="tight",
                pad_inches=0.04)
    plt.close(fig)
    return buf.getvalue()


def _torque_png(wf: Dict[str, Any], width_cm: float = 22.0,
                px: int = 2400) -> Optional[bytes]:
    """Torque over one electrical period, and its harmonic spectrum beside it.

    The ripple figure in the tables is a number with nothing to check it
    against; this is the waveform it was measured on (user 2026-09-11).  Mean,
    peak-to-peak and the two extremes are drawn, and the spectrum says WHICH
    order the ripple is — which is what decides whether to change the slot
    opening, the magnet arc or the current angle.
    """
    t = wf.get("T_em_Nm") or wf.get("T_em_filt_Nm")
    if not t:
        return None
    try:
        import numpy as np
        import matplotlib.pyplot as plt
        y = np.abs(np.asarray(t, float))
        ang = wf.get("rotor_angle_deg")
        x = (np.asarray(ang, float) if ang and len(ang) == len(y)
             else np.arange(y.size, dtype=float))
        mean = float(y.mean())
        pp = float(y.max() - y.min())
        w = max(float(width_cm), 8.0) / 2.54
        # STACKED ON HALF A PAGE (2026-09-14).  Side by side, each panel of a
        # paired chart is 4 cm wide: the two titles collide over the gap and
        # the spectrum's y-label lands on the waveform's x-ticks.  One above
        # the other, both panels keep the full column width.
        if _narrow(width_cm):
            fig, (ax, bx) = plt.subplots(
                2, 1, figsize=(w, w * 0.92), dpi=max(160.0, float(px) / w),
                gridspec_kw={"height_ratios": [1.45, 1.0]})
        else:
            fig, (ax, bx) = plt.subplots(
                1, 2, figsize=(w, w * _chart_aspect(0.30, width_cm)),
                dpi=max(160.0, float(px) / w),
                gridspec_kw={"width_ratios": [1.7, 1.0]})
        ax.plot(x, y, color="#2e86ff", lw=1.8, zorder=3)
        ax.axhline(mean, color="#1f2937", lw=1.0, ls="--", zorder=2)
        ax.fill_between(x, y.min(), y.max(), color="#2e86ff", alpha=0.06,
                        zorder=0)
        # THE MEAN, WITH THE 3-D FACTOR (reviewer 2026-09-14, B10).  The chart
        # quoted the 2-D mean against a headline that says 179.9 N·m, the way
        # the voltage chart beside it already does not.  Four decimals on k,
        # like every other place in the document that prints it.
        _sm = wf.get("summary") if isinstance(wf.get("summary"), dict) else {}
        _kt3 = _numf(_g(_sm, "end3d.k_flux"))
        _lbl_m = "mean %s" % _fmt(mean, 1, "N·m")
        if _kt3 and 0.5 < _kt3 < 1.5 and abs(_kt3 - 1.0) > 1e-3:
            _lbl_m = "mean %s (2-D; ×%.4f 3-D → %s)" % (
                _fmt(mean, 1, "N·m"), _kt3, _fmt(mean * _kt3, 1, "N·m"))
        # THE ANNOTATION SITS IN A BOX IN A FREE CORNER (MJ-4, audit v5).
        # Anchored on the mean line at the left edge it was crossed by that
        # line AND by the blue trace, and on the peak side it wrapped across
        # both.  The axis is opened at the top to give the box a corner of its
        # own, and the box's own ground keeps the trace out of the text.
        _rng = float(y.max() - y.min()) or max(abs(mean), 1.0)
        ax.set_ylim(y.min() - 0.10 * _rng, y.max() + 0.42 * _rng)
        ax.text(0.015, 0.975, _chart_title(_lbl_m, width_cm, cols=30),
                transform=ax.transAxes, va="top", ha="left", zorder=5,
                fontsize=_chart_fs(8.5, width_cm), color="#1f2937",
                bbox={"boxstyle": "round,pad=0.28", "facecolor": "white",
                      "edgecolor": "#d9d9d9", "linewidth": 0.6, "alpha": 0.92})
        ax.set_xlabel("rotor angle [deg]" if ang else "frame", fontsize=9)
        ax.set_ylabel("torque [N·m]", fontsize=9)
        # ONE RIPPLE, ONE ROUNDING (CS-10): the tables print the run's own
        # `T_ripple_pct`; this title used to print its own peak-to-peak over
        # the mean at two decimals, so §5 said 0.9 % and the figure 0.96 %.
        _rip = _numf(_sm.get("T_ripple_pct"))
        if _rip is None and mean:
            _rip = 100.0 * pp / mean
        ax.set_title(_chart_title(
            "peak-to-peak %s  =  %s of the mean"
            % (_fmt(pp, 2, "N·m"), _fmt(_rip, 1, "%")), width_cm),
            fontsize=_chart_fs(9.5, width_cm), pad=4)
        ax.tick_params(labelsize=CHART_TICK_PT)
        ax.grid(color="#ececec", lw=0.6, zorder=0)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
        # the spectrum: the solver's own orders when it stored them, else a DFT
        ords, amps = wf.get("T_harm_order"), wf.get("T_harm_amp")
        if ords and amps and len(ords) == len(amps):
            o = np.asarray(ords, float)
            a = 100.0 * np.asarray(amps, float) / (mean or 1.0)
            lbl = "% of mean torque"
        else:
            d = _dft_pct(y)
            if d is None:
                o, a, lbl = np.zeros(0), np.zeros(0), ""
            else:
                o, a, _thd = d
                lbl = "% of the fundamental"
        if o.size:
            bx.bar(o, a, color="#e0821a", width=0.72, zorder=3)
            # ROOM FOR THE "order N" LABEL over the tallest bar — on half a
            # page it landed on the panel's own title.
            if _narrow(width_cm) and float(a.max()) > 0:
                bx.set_ylim(0.0, float(a.max()) * 1.30)
            _top = int(np.argmax(a))
            bx.annotate("order %d" % int(o[_top]), (o[_top], a[_top]),
                        textcoords="offset points", xytext=(0, 3),
                        ha="center", fontsize=CHART_LABEL_PT, color="#B3261E")
            bx.set_xlabel("harmonic order", fontsize=9)
            bx.set_ylabel(lbl, fontsize=9)
            bx.set_title("torque harmonics",
                         fontsize=_chart_fs(9.5, width_cm), pad=4)
            bx.tick_params(labelsize=CHART_TICK_PT)
            bx.grid(axis="y", color="#ececec", lw=0.6, zorder=0)
            for sp in ("top", "right"):
                bx.spines[sp].set_visible(False)
        else:
            bx.axis("off")
        fig.tight_layout(pad=0.4)
        return _finish(fig)
    except Exception as exc:                                # noqa: BLE001
        log.debug("report: torque chart failed (%s)", exc)
        return None


def _n_parallel_eff(summary: Optional[Dict[str, Any]]) -> float:
    """Parallel paths (× strands in hand) the winding current is split between.

    The same rule as ``passport_pwm._n_parallel_eff``, kept here so a chart does
    not import the PWM module: ``n_parallel_eff`` first, ``n_parallel`` for a
    summary written before the strands existed, never below 1.
    """
    for key in ("n_parallel_eff", "n_parallel"):
        v = _numf((summary or {}).get(key))
        if v is not None and v >= 1.0:
            return float(v)
    return 1.0


def _currents_png(wf: Dict[str, Any], width_cm: float = 22.0,
                  px: int = 2400) -> Optional[bytes]:
    """The three phase currents over one period — the excitation the rest of
    the document is an answer to."""
    ia, ib, ic = wf.get("I_A"), wf.get("I_B"), wf.get("I_C")
    if not (ia and ib and ic):
        return None
    try:
        import numpy as np
        # THE WINDING'S CURRENT, not one parallel path's.  The stored series is
        # the branch current the solver excites a coil side with, so a machine
        # with parallel paths (or strands in hand) drew an axis at ±200 A under
        # tables quoting 324.5 A rms (reviewer 2026-09-14).  Scaled by
        # `n_parallel_eff`, the picture and the tables are the same winding.
        _npar = _n_parallel_eff(wf.get("summary")
                                if isinstance(wf.get("summary"), dict) else {})
        y = [np.asarray(v, float) * _npar for v in (ia, ib, ic)]
        ang = wf.get("rotor_angle_deg")
        x = (np.asarray(ang, float) if ang and len(ang) == y[0].size
             else np.arange(y[0].size, dtype=float))
        fig, ax = _fig(width_cm, px,
                       aspect=_chart_aspect(0.28, width_cm))
        for v, name, col in zip(y, ("A", "B", "C"),
                                ("#e02718", "#1E7A3C", "#2e86ff")):
            ax.plot(x, v, color=col, lw=1.7, label="phase %s" % name, zorder=3)
        rms = float(np.sqrt(np.mean(np.concatenate(y) ** 2)))
        ax.axhline(0.0, color="#9aa4b2", lw=0.8, zorder=1)
        ax.set_xlabel("rotor angle [deg]" if ang else "frame", fontsize=9)
        ax.set_ylabel("current [A]", fontsize=9)
        # What is DRAWN is the winding (phase) current; the branch value the
        # series was stored as stays in the title, because that is the number a
        # reader will find in the solver's own arrays (2026-09-13 / 2026-09-14).
        _su = wf.get("summary") if isinstance(wf.get("summary"), dict) else {}
        # THE CURRENT THE RUN SOLVED, not the one it was asked for (2026-09-16).
        # A voltage-fed run keeps its SETPOINT in `I_winding_rms_A` /
        # `I_terminal_rms_A` and its answer in `I_phase_rms_solved_A` /
        # `I_line_rms_A`, and this title took the setpoint: the L180 gen 'rated'
        # panel said "346.6 A rms (line 600.4 A)" over a trace whose own rms is
        # 335.9 A, under a caption, a comparison table and a warnings section
        # that all say 581.8 A line.  The picture and its title are one run.
        _solved = _numf(_su.get("I_phase_rms_solved_A"))
        if _solved is not None:
            _iw, _il = _solved, _numf(_su.get("I_line_rms_A"))
        else:
            _iw = _numf(_su.get("I_winding_rms_A"))
            _il = _numf(_su.get("I_terminal_rms_A") or _su.get("I_phase_rms_A"))
        _lab = "phase (winding) currents — %s rms" % _fmt(_iw or rms, 1, "A")
        _bits = []
        if _il and abs(_il - (_iw or rms)) > 0.02 * _il:
            _bits.append("line %s" % _fmt(_il, 1, "A"))
        if _npar > 1.0:
            _bits.append("%s per parallel path" % _fmt(rms / _npar, 1, "A"))
        if _bits:
            _lab += " (%s)" % "; ".join(_bits)
        ax.set_title(_chart_title(_lab, width_cm),
                     fontsize=_chart_fs(9.5, width_cm), pad=4)
        # THE LEGEND GOES UNDER THE AXES (user 2026-09-14: the plot area must
        # not be spent on it).  Opening the axis to 1.75x the peak to make room
        # inside left the waveforms in the bottom half of the picture; below
        # the x-label the three entries cost one line and the traces get the
        # whole frame.
        _hi = max(float(np.abs(v).max()) for v in y) or 1.0
        ax.set_ylim(-_hi * 1.12, _hi * 1.12)
        ax.legend(frameon=False, fontsize=_chart_fs(8.5, width_cm), ncol=3,
                  loc="upper center", bbox_to_anchor=(0.5, -0.16),
                  columnspacing=1.6, handlelength=1.4)
        ax.tick_params(labelsize=CHART_TICK_PT)
        ax.grid(color="#ececec", lw=0.6, zorder=0)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
        return _finish(fig)
    except Exception as exc:                                # noqa: BLE001
        log.debug("report: current chart failed (%s)", exc)
        return None


# ── THE BRIDGE'S OWN VOLTAGE, NOT THE WINDING'S (2026-09-15) ────────────────
# User: *"он же не реальное напряжение показывает — там же должны быть сплошные
# импульсы с разной скважностью"*.  The line-voltage chart drew V_A − V_B
# reconstructed from the FIELD — a smooth fundamental carrying whatever ripple
# the FEM's 20 steps per carrier could resolve.  That is a real quantity (the
# volt-seconds the winding integrated) but it is NOT what the inverter puts on
# the terminals: a two-level bridge's line voltage is a THREE-level pulse train
# of +V_dc / 0 / −V_dc whose pulse WIDTHS carry the modulation, and a reader
# looking at a sinusoid under a "PWM 24 kHz" headline is being shown the wrong
# machine.  On a PWM duty the chart now draws the bridge, and keeps the field's
# winding voltage beside it, in a smaller panel, under its own name.


def _khz_words(f: Any) -> str:
    """A carrier with its unit, for a chart title: ``24 kHz``."""
    k = _khz(f)
    return ("%s kHz" % k) if k else "—"


def pwm_bridge_ll(wf: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The BRIDGE line voltage over one electrical period — ``None`` on a sine.

    Two sources, in this order:

    * the run's own PWM sidecar (``payload["pwm"]["wave_AB"]``, written by
      :meth:`simulation.pwm.PwmVoltageSource.edge_waveform_ll`) — the exact
      edges whose per-step volt-second means the solve integrated.  On a delta
      machine solved through the star equivalent those edges are the MODEL
      bridge's, on a bus of √3·V_dc; they are scaled back onto the real DC link
      here, which leaves the pattern alone and puts the levels at ±V_dc where a
      two-level bridge really switches;
    * failing that, a regeneration from the coupled record's ``inverter`` block
      through the same modulator class — same carrier count, same modulation
      index, same reference angle.

    ``t_s[i]`` is the instant the bridge switches TO ``v[i]`` (step-after), so
    the series is the pulse train exactly, not a sampling of it.
    """
    if not isinstance(wf, dict):
        return None
    pw = wf.get("pwm") if isinstance(wf.get("pwm"), dict) else {}
    inv = wf.get("inverter") if isinstance(wf.get("inverter"), dict) else {}
    if not (pw or inv):
        return None
    v_dc = (_numf(pw.get("v_bus_real_V")) or _numf(inv.get("v_dc_V"))
            or _numf(pw.get("v_bus_V")))
    f_el = (_numf(wf.get("f_elec_Hz")) or _numf(inv.get("f_elec_Hz")))
    carr = (_numf(pw.get("carriers_per_period"))
            or _numf(inv.get("carriers_per_period")))
    m = _numf(pw.get("modulation_index")) or _numf(inv.get("m"))
    f_sw = (_numf(pw.get("f_switch_requested_Hz"))
            or _numf(inv.get("f_carrier_hz")))
    f_sw_eff = (_numf(pw.get("f_switch_eff_Hz"))
                or _numf(inv.get("f_carrier_eff_hz"))
                or ((carr * f_el) if (carr and f_el) else None))
    out: Dict[str, Any] = {
        "v_dc_V": v_dc, "f_elec_Hz": f_el, "carriers": int(carr or 0),
        "m": m, "f_carrier_hz": f_sw, "f_carrier_eff_hz": f_sw_eff,
        "modulator": str(pw.get("modulator") or inv.get("modulator") or ""),
        "equivalent_star": bool(pw.get("equivalent_star")
                                or inv.get("equivalent_star")),
    }
    w = pw.get("wave_AB") if isinstance(pw.get("wave_AB"), dict) else None
    if w and w.get("t_s") and w.get("v"):
        # The stored edges are the MODEL bridge's when the delta was solved on
        # the star equivalent: same pattern, bus ×√3.  One scale factor puts
        # them back on the real link — nothing about the widths changes.
        _mb = (_numf(w.get("v_bus_V")) or _numf(pw.get("v_bus_model_V"))
               or v_dc)
        k = (float(v_dc) / float(_mb)) if (v_dc and _mb) else 1.0
        out.update({
            "t_s": [float(x) for x in w["t_s"]],
            "v": [float(x) * k for x in w["v"]],
            "t_end_s": _numf(w.get("t_end_s")),
            "v1_peak_V": (_numf(w.get("v1_ll_peak_V")) or 0.0) * k,
            "v1_phase_deg": _numf(w.get("v1_ll_phase_deg")) or 0.0,
            "source": "stored",
            "source_note": ("the run's own PWM sidecar — the exact edges the "
                            "solve integrated"),
        })
        if out["f_elec_Hz"] is None:
            out["f_elec_Hz"] = _numf(w.get("f_elec_Hz"))
        if not out["carriers"]:
            out["carriers"] = int(_numf(w.get("carriers_per_period")) or 0)
        if out["t_end_s"] and out["f_elec_Hz"]:
            return out
        if out["f_elec_Hz"]:
            out["t_end_s"] = 1.0 / float(out["f_elec_Hz"])
            return out
    # ── regenerate, through the solver's own modulator ────────────────────
    if not (v_dc and f_el and m):
        return None
    try:
        from motor_ai_sim.simulation.pwm import (PwmVoltageSource,
                                                 carriers_per_period)
        nc = int(carr or 0) or carriers_per_period(float(f_sw or 0.0),
                                                   float(f_el))
        ref = (_numf(pw.get("reference_delta_deg"))
               or _numf(inv.get("v_delta_deg")) or 0.0)
        src = PwmVoltageSource(pole_pairs=1, daxis_deg=0.0,
                               v_delta_deg=float(ref), v_bus=float(v_dc),
                               carriers=int(nc), m=float(m))
        e = src.edge_waveform_ll(float(f_el), 1.0)
    except Exception as exc:                                # noqa: BLE001
        log.debug("report: no PWM bridge waveform (%s)", exc)
        return None
    out.update({
        "t_s": [float(x) for x in e["t_s"]], "v": [float(x) for x in e["v"]],
        "t_end_s": _numf(e.get("t_end_s")) or 1.0 / float(f_el),
        "v1_peak_V": _numf(e.get("v1_ll_peak_V")) or 0.0,
        "v1_phase_deg": _numf(e.get("v1_ll_phase_deg")) or 0.0,
        "carriers": int(nc), "source": "regenerated",
        "source_note": ("regenerated from the record's modulator parameters — "
                        "the run kept no voltage sidecar"),
    })
    return out


def pwm_bridge_spectrum(edges: Dict[str, Any], n_orders: Optional[int] = None
                        ) -> Optional[Tuple[Any, Any, float]]:
    """``(orders, amplitude as % of the fundamental, THD %)`` of a pulse train.

    The coefficients are integrated ANALYTICALLY across the edges — a pulse
    train resampled onto a grid and FFT'd carries the grid's own aliasing into
    the carrier band, which is the one part of this spectrum the chart exists
    to show.  The THD is Parseval's, off the exact rms, so it is the whole
    waveform's and not a truncated sum: the bridge's harmonic content does not
    stop at the order the bars run out at.
    """
    try:
        import numpy as np
        t = np.asarray((edges or {}).get("t_s") or [], float)
        v = np.asarray((edges or {}).get("v") or [], float)
        T = float(_numf((edges or {}).get("t_end_s")) or 0.0)
        if t.size < 2 or t.size != v.size or T <= 0.0:
            return None
        t1 = np.append(t[1:], T)
        dt = t1 - t
        keep = dt > 0.0
        t, t1, v, dt = t[keep], t1[keep], v[keep], dt[keep]
        if t.size < 2:
            return None
        n = int(n_orders or 0) or max(
            40, int(2.6 * max(int(_numf(edges.get("carriers")) or 0), 8)))
        k = np.arange(1, n + 1, dtype=float)
        w = 2j * np.pi * k[:, None] / T
        c = ((v[None, :] * (np.exp(-w * t[None, :])
                            - np.exp(-w * t1[None, :]))).sum(axis=1)
             / (w[:, 0] * T))
        amp = 2.0 * np.abs(c)
        if amp.size < 1 or amp[0] <= 0.0:
            return None
        dc = float((v * dt).sum() / T)
        rms2 = float((v ** 2 * dt).sum() / T) - dc * dc
        thd = 100.0 * math.sqrt(max(0.0, 2.0 * rms2 - amp[0] ** 2)) / amp[0]
        return np.arange(1, n + 1), 100.0 * amp / amp[0], thd
    except Exception as exc:                                # noqa: BLE001
        log.debug("report: no PWM bridge spectrum (%s)", exc)
        return None


def bridge_pulse_thd_pct(wf: Any, col: Optional[Dict[str, Any]] = None
                         ) -> Optional[float]:
    """The THD of the BRIDGE's pulse train — ``None`` when there is no bridge.

    MJ-1 (audit v7).  The row and the §8 finding named "Line voltage THD at the
    bridge (pulse train)" printed ``THD_LL_pct``, which is the FEM's
    carrier-averaged WINDING line voltage: 32.28 % on the L155 rated duty,
    43.35 % on the peak one, under the bridge's name and beside a figure whose
    own spectrum panel says 115 % and 74.8 %.  A three-level pulse train on a
    24 kHz carrier really does distort by that much, and the two numbers were
    never the same quantity.

    So there is ONE function for the row and the chart: both reach the train
    through :func:`pwm_bridge_ll` and integrate it with
    :func:`pwm_bridge_spectrum`, and the number in the table is by construction
    the number printed on the picture.
    """
    br = pwm_bridge_ll(_with_inverter(wf or {}, col or {}))
    if not br:
        return None
    d = pwm_bridge_spectrum(br)
    return None if d is None else float(d[2])


#: `(die, cfg, duty) -> bridge THD or None`, so one build integrates each
#: duty's pulse train at most once.  Cleared with the process.
_BRIDGE_THD_CACHE: Dict[Tuple[str, str, str], Optional[float]] = {}


def duty_bridge_thd_pct(die: str, cfg: str, col: Dict[str, Any],
                        cfg_doc: Dict[str, Any]) -> Optional[float]:
    """:func:`bridge_pulse_thd_pct` for one stored duty — the run's own PWM
    sidecar when it kept one, the regenerated train otherwise (the same order
    :func:`pwm_bridge_ll` uses for the chart)."""
    if duty_drive(col) != "pwm":
        return None
    duty = str(col.get("duty") or "")
    key = (str(die), str(cfg), duty)
    if key in _BRIDGE_THD_CACHE:
        return _BRIDGE_THD_CACHE[key]
    wf = duty_waveforms(die, cfg, duty, cfg_doc or {}, PWM_RUN_DRIVE)
    out = bridge_pulse_thd_pct(wf, col)
    _BRIDGE_THD_CACHE[key] = out
    return out


def _pwm_step(edges: Dict[str, Any]) -> Optional[Tuple[Any, Any]]:
    """The pulse train as a drawable step: (t, v) with the closing edge."""
    try:
        import numpy as np
        t = np.asarray(edges.get("t_s") or [], float)
        v = np.asarray(edges.get("v") or [], float)
        T = float(_numf(edges.get("t_end_s")) or 0.0)
        if t.size < 2 or t.size != v.size or T <= 0.0:
            return None
        return np.append(t, T), np.append(v, v[-1])
    except Exception:                                       # noqa: BLE001
        return None


def _voltage_png(wf: Dict[str, Any], width_cm: float = 22.0,
                 px: int = 2400) -> Optional[bytes]:
    """LINE voltages over one period, with their harmonic spectrum.

    Line, not phase: the triplen harmonics cancel between two terminals, so the
    line waveform is what the inverter and the insulation actually see — and it
    is the THD the warnings section quotes.

    On a PWM duty the main panel is the BRIDGE's three-level pulse train
    (:func:`pwm_bridge_ll`) with its fundamental over it, the spectrum is that
    train's, and the field-reconstructed winding voltage keeps a smaller panel
    of its own — see the comment above :func:`pwm_bridge_ll`.
    """
    va, vb, vc = wf.get("V_A"), wf.get("V_B"), wf.get("V_C")
    if not (va and vb and vc):
        return None
    try:
        import numpy as np
        import matplotlib.pyplot as plt
        a, b, c = (np.asarray(v, float) for v in (va, vb, vc))
        # LINE per the terminal connection, the same rule as
        # `postproc._line_harmonics`: star — the difference of two windings;
        # delta — the winding IS the line, minus its zero-sequence part (the
        # triplen EMF circulates inside the closed loop and never reaches the
        # terminals).  The star formula on a delta run drew √3× the terminal
        # voltage (2026-09-13).
        _su = wf.get("summary") if isinstance(wf.get("summary"), dict) else {}
        _sd = str(wf.get("star_delta") or _su.get("star_delta") or "star").lower()
        if _sd.startswith("d"):
            v0 = (a + b + c) / 3.0
            lines = {"AB": a - v0, "BC": b - v0, "CA": c - v0}
        else:
            lines = {"AB": a - b, "BC": b - c, "CA": c - a}
        ang = wf.get("rotor_angle_deg")
        x = (np.asarray(ang, float) if ang and len(ang) == a.size
             else np.arange(a.size, dtype=float))
        w = max(float(width_cm), 8.0) / 2.54
        # ── the bridge, when there is one ────────────────────────────────
        _br = pwm_bridge_ll(wf)
        _step = _pwm_step(_br) if _br else None
        _pwm = _br if _step is not None else None
        # STACKED ON HALF A PAGE (2026-09-14).  Side by side, each panel of a
        # paired chart is 4 cm wide: the two titles collide over the gap and
        # the spectrum's y-label lands on the waveform's x-ticks.  One above
        # the other, both panels keep the full column width.
        if _pwm is not None:
            # THREE PANELS (2026-09-15): the bridge's pulse train, its
            # spectrum, and — smaller, under its own name — the winding
            # voltage the field gives back.
            if _narrow(width_cm):
                fig = plt.figure(figsize=(w, w * 1.48),
                                 dpi=max(160.0, float(px) / w))
                gs = fig.add_gridspec(3, 1, height_ratios=[1.35, 1.05, 1.0])
                ax = fig.add_subplot(gs[0, 0])
                bx = fig.add_subplot(gs[1, 0])
                cx = fig.add_subplot(gs[2, 0])
            else:
                fig = plt.figure(figsize=(w, w * 0.46),
                                 dpi=max(160.0, float(px) / w))
                gs = fig.add_gridspec(2, 2, width_ratios=[1.75, 1.0],
                                      height_ratios=[1.5, 1.0])
                ax = fig.add_subplot(gs[0, 0])
                cx = fig.add_subplot(gs[1, 0])
                bx = fig.add_subplot(gs[:, 1])
        elif _narrow(width_cm):
            fig, (ax, bx) = plt.subplots(
                2, 1, figsize=(w, w * 0.92), dpi=max(160.0, float(px) / w),
                gridspec_kw={"height_ratios": [1.45, 1.0]})
            cx = None
        else:
            fig, (ax, bx) = plt.subplots(
                1, 2, figsize=(w, w * _chart_aspect(0.30, width_cm)),
                dpi=max(160.0, float(px) / w),
                gridspec_kw={"width_ratios": [1.7, 1.0]})
            cx = None
        if cx is not None:
            # The field's winding voltage moves down into the small panel; the
            # main axes belong to the bridge.
            ax, cx = cx, ax
        for (name, v), col in zip(lines.items(),
                                  ("#e02718", "#1E7A3C", "#2e86ff")):
            ax.plot(x, v, color=col, lw=1.7, label="U%s" % name, zorder=3)
        pk = max(float(np.abs(v).max()) for v in lines.values())
        ax.axhline(0.0, color="#9aa4b2", lw=0.8, zorder=1)
        ax.set_xlabel("rotor angle [deg]" if ang else "frame", fontsize=9)
        ax.set_ylabel("line voltage [V]", fontsize=9)
        # The waveform is the 2-D solve; the catalog's V L-L carries the 3-D
        # end-effect factor when the run has one — say both, or the two
        # numbers a reader sees on facing pages do not agree (702 vs 722 V).
        _k = None
        try:
            _k = float(((_su.get("end3d") or {}).get("k_flux")))
        except (TypeError, ValueError, AttributeError):
            _k = None
        _ttl = ("winding voltage from the field (carrier-averaged) — peak %s"
                % _fmt(pk, 1, "V") if _pwm is not None
                else "line voltages — peak %s" % _fmt(pk, 1, "V"))
        # FOUR DECIMALS on k, the same as every table that prints it (D10).
        if _k and 0.5 < _k < 1.5 and abs(_k - 1.0) > 1e-3:
            _ttl += " (2-D; ×%.4f 3-D → %s)" % (_k, _fmt(pk * _k, 1, "V"))
        if _sd.startswith("d"):
            _ttl += " · delta"
        # The small panel is half the width of the page: its title WRAPS, or it
        # runs across the spectrum's y-label beside it.
        if _pwm is not None:
            import textwrap as _tw
            _ttl = "\n".join(_tw.wrap(_ttl, 52)) or _ttl
        ax.set_title(_ttl if _pwm is not None else _chart_title(_ttl, width_cm),
                     fontsize=_chart_fs(9.0 if _pwm is not None else 9.5,
                                        width_cm), pad=4)
        # UNDER THE AXES, not over the crests (CS-7, audit v5): the three
        # entries at "upper right" sat on the tops of the three waveforms.
        _vpk = max(float(np.abs(v).max()) for v in lines.values()) or 1.0
        ax.set_ylim(-_vpk * 1.10, _vpk * 1.10)
        ax.legend(frameon=False, fontsize=_chart_fs(8.5, width_cm), ncol=3,
                  loc="upper center",
                  bbox_to_anchor=(0.5, -0.34 if _pwm is not None else -0.16),
                  columnspacing=1.6, handlelength=1.4)
        ax.tick_params(labelsize=CHART_TICK_PT)
        ax.grid(color="#ececec", lw=0.6, zorder=0)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
        # ── the bridge panel and ITS spectrum ─────────────────────────────
        if _pwm is not None:
            _T = float(_numf(_pwm.get("t_end_s")) or 0.0)
            _bt, _bv = _step
            _deg = _bt / _T * 360.0 if _T > 0 else _bt
            _vdc = _numf(_pwm.get("v_dc_V")) or float(np.abs(_bv).max()) or 1.0
            cx.step(_deg, _bv, where="post", color="#2e86ff", lw=1.0,
                    zorder=3, label="U_AB bridge")
            _v1 = _numf(_pwm.get("v1_peak_V")) or 0.0
            if _v1 > 0.0:
                _tt = np.linspace(0.0, 360.0, 1440)
                cx.plot(_tt, _v1 * np.cos(np.radians(
                    _tt + (_numf(_pwm.get("v1_phase_deg")) or 0.0))),
                    color="#B3261E", lw=1.6, ls="--", zorder=4,
                    label="fundamental %s" % _fmt(_v1, 0, "V"))
            for _lv in (_vdc, 0.0, -_vdc):
                cx.axhline(_lv, color="#9aa4b2", lw=0.7, ls=":", zorder=1)
            cx.set_xlim(0.0, 360.0)
            cx.set_ylim(-_vdc * 1.18, _vdc * 1.18)
            cx.set_xlabel("electrical angle [deg] — one period (%s)"
                          % _fmt(_T * 1e6, 0, "µs"), fontsize=9)
            cx.set_ylabel("bridge line voltage [V]", fontsize=9)
            cx.set_title(_chart_title(
                "bridge line voltage ±%s, fundamental %s (m %s), %s"
                % (_fmt(_vdc, 0, "V"), _fmt(_v1, 0, "V"),
                   _fmt(_pwm.get("m"), 2),
                   _khz_words(_pwm.get("f_carrier_hz")
                              or _pwm.get("f_carrier_eff_hz"))),
                width_cm, cols=64), fontsize=_chart_fs(9.5, width_cm), pad=4)
            cx.legend(frameon=False, fontsize=_chart_fs(8.0, width_cm), ncol=2,
                      loc="upper center", bbox_to_anchor=(0.5, -0.30),
                      columnspacing=1.6, handlelength=1.8)
            cx.tick_params(labelsize=CHART_TICK_PT)
            cx.grid(color="#ececec", lw=0.6, zorder=0)
            for sp in ("top", "right"):
                cx.spines[sp].set_visible(False)
        d = (pwm_bridge_spectrum(_pwm) if _pwm is not None
             else _dft_pct(lines["AB"]))
        if d is not None:
            o, amp, thd = d
            if _pwm is not None:
                # THE CARRIER BAND IS THE POINT.  The bars run past it and the
                # cluster is named in kHz, because "order 20" means nothing to
                # a reader sizing an EMC filter and "23.7 kHz" means
                # everything.
                _nc = int(_numf(_pwm.get("carriers")) or 0)
                _f1 = _numf(_pwm.get("f_elec_Hz")) or 0.0
                bx.bar(o, amp, color=["#B3261E" if (_nc and k % _nc == 0)
                                      else "#e0821a" for k in o],
                       width=0.9, zorder=3)
                bx.set_ylabel("% of the fundamental", fontsize=9)
                if _nc and _f1:
                    _ticks = [1] + [k * _nc for k in (1, 2, 3)
                                    if k * _nc <= int(o[-1])]
                    bx.set_xticks(_ticks)
                    bx.set_xticklabels(
                        [_khz(t * _f1) or str(t) for t in _ticks])
                    bx.set_xlabel("harmonic order, as frequency [kHz]",
                                  fontsize=9)
                    # THE CARRIER IS NAMED AS THE DOCUMENT NAMES IT (CS-7,
                    # audit v7).  The cluster sits on the nearest harmonic
                    # ORDER of a 1,181 Hz fundamental, so the bin reads
                    # 23.7 kHz against the 24 kHz every other page prints.  The
                    # annotation says the bridge's own carrier, and the bin
                    # beside it only when the two round differently.
                    _fc = _numf(_pwm.get("f_carrier_hz")) or (_nc * _f1)
                    _bin = _nc * _f1
                    bx.annotate("carrier %s%s"
                                % (_khz_words(_fc),
                                   ("" if _khz(_fc) == _khz(_bin)
                                    else " (nearest order %s)"
                                         % _khz_words(_bin))),
                                (_nc, float(np.max(amp))),
                                textcoords="offset points", xytext=(4, -6),
                                ha="left", color="#B3261E",
                                fontsize=_chart_fs(8.0, width_cm))
                else:
                    bx.set_xlabel("harmonic order", fontsize=9)
                bx.set_title("U_AB spectrum — THD of the bridge waveform %s"
                             % _fmt(thd, 1, "%"),
                             fontsize=_chart_fs(9.5, width_cm), pad=4)
            else:
                cols = ["#B3261E" if k % 2 == 0 else "#e0821a" for k in o]
                bx.bar(o, amp, color=cols, width=0.72, zorder=3)
                bx.set_xlabel("harmonic order", fontsize=9)
                bx.set_ylabel("% of the fundamental", fontsize=9)
                # ONE THD on the page: the table quotes the solver's own line
                # THD (THD_LL_pct); this spectrum is a DFT of the stored
                # samples and rounded a little differently (1.2 vs 1.15 —
                # reviewer 2026-09-13).
                _thd_tab = _numf(_su.get("THD_LL_pct")) if _su else None
                bx.set_title("U_AB spectrum — THD %s" % _fmt(
                    _thd_tab if _thd_tab is not None else thd, 2, "%"),
                    fontsize=_chart_fs(9.5, width_cm), pad=4)
            bx.tick_params(labelsize=CHART_TICK_PT)
            bx.grid(axis="y", color="#ececec", lw=0.6, zorder=0)
            for sp in ("top", "right"):
                bx.spines[sp].set_visible(False)
        else:
            bx.axis("off")
        fig.tight_layout(pad=0.4,
                         **({"w_pad": 2.2, "h_pad": 1.8} if _pwm is not None
                            else {}))
        return _finish(fig)
    except Exception as exc:                                # noqa: BLE001
        log.debug("report: voltage chart failed (%s)", exc)
        return None


def _sf_bars_png(case: Dict[str, Any], width_cm: float = SIDE_CHART_CM,
                 px: int = SIDE_CHART_PX) -> Optional[bytes]:
    """Safety factor per part, against the acceptance level.

    The number that decides whether a rotor is buildable, as a picture: a bar
    short of the line is a part that does not pass, and no reading of a table
    row makes that as plain (user 2026-09-11).
    """
    parts = (case or {}).get("parts") or {}
    rows = [(str(n).capitalize(), _numf((p or {}).get("safety_factor")))
            for n, p in sorted(parts.items())]
    rows = [(n, v) for n, v in rows if v is not None]
    if not rows:
        return None
    try:
        import numpy as np
        labels = [r[0] for r in rows][::-1]
        vals = np.array([r[1] for r in rows], float)[::-1]
        lim = float(SF_ACCEPT) if _numf(SF_ACCEPT) else 2.0
        fig, ax = _fig(width_cm, px, aspect=0.72)
        cols = ["#e02718" if v < 1.0 else "#e0821a" if v < lim else "#1E7A3C"
                for v in vals]
        ax.barh(labels, vals, color=cols, height=0.6, zorder=2)
        span = max(float(vals.max()), lim) * 1.25
        for i, v in enumerate(vals):
            ax.text(v + span * 0.02, i, _fmt(v, 2), va="center",
                    fontsize=CHART_TICK_PT, zorder=5,
                    bbox={"facecolor": "white", "edgecolor": "none",
                          "pad": 0.8})
        # BOTH LINE LABELS ABOVE THE PLOT, on their own rows (reviewer
        # 2026-09-14, D9; audit v5): under the axis "yields 1.0" printed across
        # the "0" tick, and inside it across the topmost bar.
        _lo, _hi = -0.6, len(labels) - 0.5 + 1.5
        _top = (len(labels) - 0.5 - _lo) / (_hi - _lo)
        ax.axvline(lim, ymin=0.0, ymax=_top, color="#B3261E", lw=1.2, ls="--",
                   zorder=4)
        ax.text(lim, len(labels) - 0.5 + 1.05, " accept %s" % _fmt(lim, 1),
                color="#B3261E", fontsize=CHART_LABEL_PT, va="center")
        ax.axvline(1.0, ymin=0.0, ymax=_top, color="#9aa4b2", lw=1.0, ls=":",
                   zorder=4)
        ax.text(1.0, len(labels) - 0.5 + 0.45, " yields 1.0", color="#6E6E6E",
                fontsize=CHART_LABEL_PT, va="center", ha="left")
        ax.set_ylim(_lo, _hi)
        ax.set_xlim(0, span)
        ax.set_xlabel("safety factor", fontsize=9)
        ax.tick_params(labelsize=CHART_TICK_PT)
        ax.grid(axis="x", color="#ececec", lw=0.6, zorder=0)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
        return _finish(fig)
    except Exception as exc:                                # noqa: BLE001
        log.debug("report: safety-factor chart failed (%s)", exc)
        return None


def heat_not_in_map_w(res: Dict[str, Any],
                      inner: Dict[str, Any]) -> Optional[float]:
    """The mechanical watts this 2-D cross-section has NO PLACE for, or None.

    The explicit keys first (``bearing_friction_not_modelled_W`` /
    ``windage_not_modelled_W``); a record that carries neither — which is every
    record this project has written so far — is asked the same question the
    other way round: the whole mechanical loss minus the share that IS inside
    the section.  On the L155 rated duty that is 437.7 − 61 = 376.7 W, 9.7 % of
    the map, and the waterfall drew none of it under a caption describing a
    hatched bar that was not there (reviewer 2026-09-14, B2).

    ``None`` when it cannot be formed or is under 1 % of what the map carries —
    the same threshold the chart draws at, so the caption and the picture are
    one decision made in one place.
    """
    cooling = (res or {}).get("cooling") or (inner or {}).get("cooling") or {}
    budget = cooling.get("heat_budget") or {}
    nm = (abs(_numf(budget.get("bearing_friction_not_modelled_W")) or 0.0)
          + abs(_numf(budget.get("windage_not_modelled_W")) or 0.0))
    if nm <= 0.0:
        mech = cooling.get("mech_losses") or {}
        tot = (_numf(budget.get("mech_loss_total_W"))
               or _numf(mech.get("P_mech_total_W")))
        inside = (_numf(budget.get("mech_loss_in_map_W"))
                  or _numf(mech.get("P_mech_into_map_W")) or 0.0)
        if tot is None:
            return None
        nm = max(0.0, float(tot) - float(inside))
    p_in = _numf(budget.get("losses_W")) or _numf(budget.get("P_in_W"))
    if p_in is None:
        p_in = _numf((res or {}).get("P_loss_total_W")
                     or (inner or {}).get("P_loss_total_W"))
    if not p_in or nm <= 0.01 * float(p_in):
        return None
    return nm


def heat_chart_caption(res: Dict[str, Any], inner: Dict[str, Any]) -> str:
    """The waterfall's caption, with the hatched-bar sentence only when the
    bar is actually drawn — the caption and the drawing read one number."""
    nm = heat_not_in_map_w(res, inner)
    if nm is None:
        return HEAT_CHART_CAPTION_PLAIN
    return HEAT_CHART_CAPTION_PLAIN.rstrip(".") + (
        "; hatched = %s of mechanical heat outside this 2-D section, in "
        "neither column." % _fmt(nm, 0, "W"))


def heat_chart_caption_pair(l_res: Optional[Dict[str, Any]],
                            l_inner: Optional[Dict[str, Any]],
                            r_res: Optional[Dict[str, Any]],
                            r_inner: Optional[Dict[str, Any]]) -> str:
    """…and the same sentence for a PAIR, with a number per side.

    The compaction dropped it from the paired caption (CS-2, audit v5): the
    hatched bar was still drawn and still labelled inside the figure, but the
    caption no longer said what it was and the watts it carries appeared
    nowhere else in the document.
    """
    a = heat_not_in_map_w(l_res or {}, l_inner or {})
    b = heat_not_in_map_w(r_res or {}, r_inner or {})
    if a is None and b is None:
        return HEAT_CHART_CAPTION_PLAIN
    # ONE SIDE ONLY IS SAID IN WORDS (CS-1, audit v6).  The "— / 963 W" form
    # left the caption opening on a bare em dash and a reader had no way to tell
    # a missing value from a zero.
    if a is None or b is None:
        v, side = (b, "right") if a is None else (a, "left")
        tail = ("; hatched on the %s = %s of mechanical heat outside this 2-D "
                "section, in neither column — the other side has none."
                % (side, _fmt(v, 0, "W")))
    else:
        tail = ("; hatched = %s / %s of mechanical heat outside this 2-D "
                "section, in neither column." % (_fmt(a, 0, "W"),
                                                 _fmt(b, 0, "W")))
    return HEAT_CHART_CAPTION_PLAIN.rstrip(".") + tail


def _heat_waterfall_png(res: Dict[str, Any], inner: Dict[str, Any],
                        width_cm: float = SIDE_CHART_CM,
                        px: int = SIDE_CHART_PX) -> Optional[bytes]:
    """The heat budget as a waterfall: what went in, what each surface took
    out, and what did not close.

    The table says the same thing in numbers; the waterfall is what makes a
    residual visible as a step that does not reach the floor.
    """
    cooling = (res or {}).get("cooling") or (inner or {}).get("cooling") or {}
    budget = cooling.get("heat_budget") or {}
    # WHAT WENT IN is `losses_W` — the integral of the loss density over the
    # solved sub-mesh, which is the heat this map actually carries.  Reading
    # `P_loss_total_W` instead (the electromagnetic total) made the bars miss by
    # the mechanical watts that DO sit in the map, and the chart then showed
    # more heat leaving than entering.
    p_in = _numf(budget.get("losses_W")) or _numf(budget.get("P_in_W"))
    if p_in is None:
        p_in = _numf((res or {}).get("P_loss_total_W")
                     or (inner or {}).get("P_loss_total_W"))
    if not p_in:
        return None
    # …and OUT through every door the record has.  The mount and the axial end
    # faces joined the list on 2026-09-14: on a joint in still air they carry
    # most of the heat, and a waterfall that knew only three doors drew the
    # other 55 W as a residual — a red bar under a solve that closed to a
    # milliwatt.  Both are 0 (and so drop out) on every machine before them.
    outs = [("Housing", _numf((cooling.get("outer") or {}).get("heat_removed_W"))),
            ("Bore", _numf((cooling.get("inner") or {}).get("heat_removed_W"))),
            ("Shaft ends",
             _numf((cooling.get("shaft_ends") or {}).get("heat_removed_W"))),
            ("Mount", _numf((cooling.get("mount") or {}).get("heat_removed_W"))),
            ("End faces",
             _numf((cooling.get("end_faces") or {}).get("heat_removed_W")))]
    outs = [(n, v) for n, v in outs if v]
    if not outs:
        return None
    try:
        import numpy as np
        names = ["Losses in"] + [n for n, _v in outs] + ["Residual"]
        left = float(p_in)
        bottoms, heights, cols = [0.0], [left], ["#e0821a"]
        for _n, v in outs:
            left -= abs(float(v))
            bottoms.append(left)
            heights.append(abs(float(v)))
            cols.append("#2e86ff")
        resid = _numf(budget.get("residual_W"))
        resid = left if resid is None else float(resid)
        bottoms.append(0.0)
        heights.append(abs(resid))
        cols.append("#B3261E" if abs(resid) > 0.02 * float(p_in) else "#1E7A3C")
        fig, ax = _fig(width_cm, px, aspect=0.72)
        ax.bar(names, heights, bottom=bottoms, color=cols, width=0.62, zorder=3)
        for i, (b, h) in enumerate(zip(bottoms, heights)):
            ax.text(i, b + h + float(p_in) * 0.02, _fmt(h, 1),
                    ha="center", fontsize=CHART_TICK_PT)
        ax.set_ylabel("W", fontsize=9)
        ax.set_ylim(0, float(p_in) * 1.16)
        ax.tick_params(labelsize=CHART_TICK_PT)
        # THE CATEGORY NAMES DO NOT RUN INTO EACH OTHER (MJ-2, audit v5): at
        # half width "Losses in" and "Housing" printed as `Losses inHousing`
        # and four of the five columns could not be told apart.  Laid over at
        # an angle and anchored on their right end, each label ends under its
        # own bar whatever the column is called.
        for _lbl in ax.get_xticklabels():
            _lbl.set_rotation(28)
            _lbl.set_ha("right")
            _lbl.set_rotation_mode("anchor")
        ax.grid(axis="y", color="#ececec", lw=0.6, zorder=0)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
        ax.set_title("in %s · out %s · residual %s"
                     % (_fmt(p_in, 0, "W"),
                        _fmt(sum(abs(float(v)) for _n, v in outs), 0, "W"),
                        _fmt(resid, 1, "W")),
                     fontsize=_chart_fs(9.5, width_cm), pad=4)
        # …and the mechanical watts this cross-section has NO PLACE for, as a
        # detached hatched bar: they are not part of the balance and must not
        # look like one, but leaving them off the picture is how a reader comes
        # away thinking the machine dissipates less than it does.
        _nm = heat_not_in_map_w(res, inner) or 0.0
        if _nm > 0.01 * float(p_in):
            # NAMED, not just hatched (user 2026-09-11: "что это значит?").  A
            # bar standing outside the balance has to say what it is on the
            # chart, not only in the caption three lines below it.
            ax.bar(["Mechanical,\nnot in map"], [_nm], color="#fff4e5",
                   width=0.62, edgecolor="#B7791F", hatch="//", lw=1.2,
                   zorder=3)
            ax.text(len(names), _nm + float(p_in) * 0.02,
                    "%s\nbearings + windage\noutside the section"
                    % _fmt(_nm, 0, "W"),
                    ha="center", va="bottom", fontsize=CHART_LABEL_PT,
                    color="#B7791F")
            for _lbl in ax.get_xticklabels():
                _lbl.set_rotation(28)
                _lbl.set_ha("right")
                _lbl.set_rotation_mode("anchor")
        return _finish(fig)
    except Exception as exc:                                # noqa: BLE001
        log.debug("report: heat waterfall failed (%s)", exc)
        return None


def _mass_inertia_png(em: Dict[str, Any], width_cm: float = 20.0,
                      px: int = 2200, which: str = "both") -> Optional[bytes]:
    """Two pies side by side: where the kilograms are, and where the spinning
    inertia is.

    They are different questions with different answers — on the Ø200 the
    magnets are a fifth of the mass and two thirds of the inertia — and putting
    them next to each other is what makes that visible (user 2026-09-11).
    """
    comps = _g(em, "mass_components") or []
    J = _g(em, "rotor_inertia") if isinstance(_g(em, "rotor_inertia"), dict) else {}
    import numpy as np
    m_rows = []
    for c in comps:
        if not isinstance(c, dict):
            continue
        v = _numf(c.get("mass_kg") or c.get("mass_modelled_kg"))
        n = str(c.get("name") or "").split(" (")[0]
        if v and v > 0:
            m_rows.append((n, v))
    j_rows = [(k.replace("_", " ").capitalize(), _numf(v) * 1e4)
              for k, v in (J or {}).items()
              if k in ("rotor_iron", "magnet", "shaft", "sleeve") and _numf(v)]
    if not m_rows and not j_rows:
        return None
    try:
        import matplotlib.pyplot as plt
        # `which` picks one pie or both: the mass table on the machine page
        # carries the mass pie beside it (user 2026-09-11), and repeating it
        # under the inertia table would be the same picture twice.
        panes = [p for p in ("mass", "inertia")
                 if which in ("both", p)] or ["mass", "inertia"]
        w = max(float(width_cm), 8.0) / 2.54
        # …and the same legend rule as the loss pie: beside the pie at full
        # page width, underneath it when the figure is half of a pair.
        _half = _narrow(width_cm)
        fig, axes = plt.subplots(
            1, len(panes),
            figsize=(w, w * (0.46 if len(panes) > 1
                             else (0.75 if _half else 0.32))),
            dpi=max(160.0, float(px) / w))
        axes = np.atleast_1d(axes)
        # ONE colour per part across BOTH pies — the whole point of putting them
        # side by side is that the eye carries a part from one to the other, and
        # a positional palette made the magnets red on the left and orange on
        # the right (2026-09-11).
        _panes = {"mass": (m_rows, "Mass", "kg"),
                  "inertia": (j_rows, "Rotor inertia", "kg·cm²")}
        for ax, key in zip(axes, panes):
            rows, title, unit = _panes[key]
            if not rows:
                ax.axis("off")
                continue
            tot = sum(v for _n, v in rows)
            ax.pie([v for _n, v in rows],
                   colors=[part_colour(n) for n, _v in rows],
                   startangle=90, counterclock=False,
                   autopct=lambda p: "%.0f %%" % p if p >= 7 else "",
                   pctdistance=0.7,
                   wedgeprops={"linewidth": 0.8, "edgecolor": "white"},
                   textprops={"fontsize": 8.5, "color": "white",
                              "fontweight": "bold"})
            # UNDER the pie, not over its bottom wedges (user 2026-09-14).
            _lg = [("%s — %s" % (n, _fmt(v, 2 if unit == "kg" else 1, unit)))
                   for n, v in rows]
            if _half or len(panes) > 1:
                ax.legend(_lg, loc="upper center",
                          bbox_to_anchor=(0.5, -0.01), frameon=False,
                          fontsize=CHART_LABEL_PT, ncol=2,
                          columnspacing=1.2, handlelength=1.2)
            else:
                ax.legend(_lg, loc="center left", bbox_to_anchor=(0.98, 0.5),
                          frameon=False, fontsize=CHART_TICK_PT,
                          handlelength=1.2)
            ax.set_title("%s — %s total" % (title, _fmt(tot, 2, unit)),
                         fontsize=10, pad=4)
            ax.set_aspect("equal")
        fig.tight_layout(pad=0.5)
        return _finish(fig)
    except Exception as exc:                                # noqa: BLE001
        log.debug("report: mass/inertia pies failed (%s)", exc)
        return None


def _campbell_png(crit: Optional[Dict[str, Any]]) -> Optional[bytes]:
    """The Campbell diagram the rotordynamics solve already computed.

    ``campbell`` carries the sweep it drew the plot from — ``rpm`` and one
    forward and one backward branch per mode, in Hz — plus the crossings with
    the 1× line in ``critical_speeds``.  Drawn from those arrays, so the picture
    in the report is the picture the Mechanical tab showed, and not a second
    idea of where the criticals are.
    """
    if not isinstance(crit, dict):
        return None
    cam = crit.get("campbell") or {}
    rpms = cam.get("rpm") or []
    crits = [c for c in (crit.get("critical_speeds") or []) if isinstance(c, dict)]
    # NO BRANCHES, NO DIAGRAM (reviewer 2026-09-14, A4).  The caption promises
    # forward and backward whirl curves crossing the 1× line; with the sweep
    # arrays missing the figure was an empty axes with one grey ray and two
    # dots under exactly that sentence.  A record that did not keep the sweep
    # gets a line of text instead — see `CAMPBELL_NOT_STORED`.
    if not has_campbell(crit):
        return None
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        rated = float(crit.get("rated_rpm") or 0.0)
        top = float(max(list(rpms) + [rated] + [c.get("rpm") or 0 for c in crits]
                        or [1.0]))
        top = max(top, 1.0)
        # …with room under the axes for the legend that used to sit on top of
        # the whirl branches (CS-6).
        fig, ax = plt.subplots(figsize=(5.6, 2.6), dpi=160)
        # 1× synchronous, in Hz against rpm — a critical speed is where a
        # forward branch crosses it.
        ax.plot([0, top], [0, top / 60.0], color="#888888", lw=1.1,
                label="1x synchronous")
        # `campbell.forward` / `.backward` are indexed BY SPEED POINT — one
        # per-mode frequency vector per rpm — so a branch is a column, not a row.
        for branches, colour, style, name in (
                (cam.get("forward") or [], "#1F4E79", "-", "forward whirl"),
                (cam.get("backward") or [], "#9AA7B5", ":", "backward whirl")):
            n_mode = min((len(c) for c in branches), default=0)
            for k in range(n_mode):
                ax.plot(rpms, [float(c[k]) for c in branches], color=colour,
                        lw=0.9 if style == "-" else 0.7, ls=style,
                        label=name if k == 0 else None)
        for c in crits:
            try:
                r = float(c.get("rpm"))
            except (TypeError, ValueError):
                continue
            ax.plot([r], [r / 60.0], "o", ms=3.5,
                    color=(WARN if c.get("whirl") == "forward" else "#9AA7B5"))
        if rated > 0:
            ax.axvline(rated, color=OK, lw=1.1, ls="--")
            # HORIZONTAL, AT THE FOOT OF THE LINE (CS-6, audit v6).  The label
            # was rotated 90° and ran up through the whole plot, crossing every
            # whirl branch it was meant to sit beside.
            ax.annotate("rated %s rpm" % f"{rated:,.0f}",
                        xy=(rated, 0.0), xycoords=("data", "axes fraction"),
                        xytext=(3, 3), textcoords="offset points",
                        fontsize=6.5, color=OK, ha="left", va="bottom",
                        bbox={"facecolor": "white", "edgecolor": "none",
                              "alpha": 0.75, "pad": 1.0})
        # PAD THE TOP so a critical marker at the end of the sweep is inside
        # the axes rather than half off the right-hand edge (A4).
        ax.set_xlim(0, top * 1.06)
        ax.set_ylim(bottom=0)
        ax.set_xlabel("shaft speed [rpm]", fontsize=7)
        ax.set_ylabel("frequency [Hz]", fontsize=7)
        ax.tick_params(labelsize=6.5)
        ax.grid(True, color="#DDDDDD", lw=0.6)
        # UNDER THE AXES, not on the topmost whirl line (CS-6): the legend box
        # sat inside the plot and covered the very branch a reader looks for.
        ax.legend(fontsize=6, frameon=False, loc="upper center",
                  bbox_to_anchor=(0.5, -0.24), ncol=3, columnspacing=1.6,
                  handlelength=1.6)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        fig.tight_layout(pad=0.3)
        buf = io.BytesIO()
        fig.savefig(buf, format="png", facecolor="white")
        plt.close(fig)
        return buf.getvalue()
    except Exception as exc:                                # noqa: BLE001
        log.debug("report: campbell render failed (%s)", exc)
        try:
            import matplotlib.pyplot as plt
            plt.close("all")
        except Exception:                                   # noqa: BLE001
            pass
        return None


# ---------------------------------------------------------------------------
# Platypus helpers
# ---------------------------------------------------------------------------


def _styles():
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet

    ss = getSampleStyleSheet()
    base = ss["BodyText"]
    return {
        "title": ParagraphStyle("rtitle", parent=base, fontName="Helvetica-Bold",
                                fontSize=21, leading=25, textColor=NAVY,
                                spaceAfter=2),
        "sub": ParagraphStyle("rsub", parent=base, fontName="Helvetica",
                              fontSize=9, leading=12, textColor=NOTE),
        "h1": ParagraphStyle("rh1", parent=base, fontName="Helvetica-Bold",
                             fontSize=13, leading=16, textColor=NAVY,
                             spaceBefore=2, spaceAfter=5),
        "h2": ParagraphStyle("rh2", parent=base, fontName="Helvetica-Bold",
                             fontSize=9.5, leading=12, textColor="#333333",
                             spaceBefore=7, spaceAfter=2),
        "body": ParagraphStyle("rbody", parent=base, fontName="Helvetica",
                               fontSize=8.5, leading=11, alignment=TA_LEFT),
        "note": ParagraphStyle("rnote", parent=base, fontName="Helvetica-Oblique",
                               fontSize=7.5, leading=10, textColor=NOTE),
        "warn": ParagraphStyle("rwarn", parent=base, fontName="Helvetica-Bold",
                               fontSize=8, leading=11, textColor=WARN),
        "cell": ParagraphStyle("rcell", parent=base, fontName="Helvetica",
                               fontSize=8, leading=10),
        # A header cell that may carry a DUTY NAME.  Duty names are allowed to
        # be Cyrillic (routes/family._DUTY_NAME_RE: the user types them off a
        # Russian keyboard), and a plain string in a reportlab cell is drawn
        # with no markup parsing — so `_plain` would turn 'номинал' into seven
        # question marks at the top of every comparison column.  Set as a
        # Paragraph instead, which `_txt` can switch to the Unicode font, in
        # white so it still reads on the navy header band.
        "hcell": ParagraphStyle("rhcell", parent=base,
                                fontName="Helvetica-Bold", fontSize=7.2,
                                leading=9, textColor="#FFFFFF"),
        "dcell": ParagraphStyle("rdcell", parent=base, fontName="Helvetica",
                                fontSize=7.2, leading=9),
    }


def _para(text: Any, style):
    """A paragraph whose exotic characters are set in a font that has them."""
    from reportlab.platypus import Paragraph
    return Paragraph(_txt(text), style)


def _cell_style(size: float, *, head: bool, first: bool):
    """The paragraph style a PLAIN cell is re-set in when it carries a glyph
    Helvetica does not have (γ, Δ, √, Cyrillic …).

    A string in a reportlab cell is drawn with no markup parsing, so the font
    switch `_txt` builds cannot reach it and `_plain` used to spell the letter
    out — which printed "gamma (gamma)" and "delta delta" in the PDF while the
    .docx rendered both correctly (reviewer 2026-09-14, D3).  Set as a
    Paragraph instead, the glyph survives; the alignment and the header's white
    ink are reproduced here because a Paragraph ignores the table's own ALIGN
    and TEXTCOLOR commands.
    """
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT, TA_RIGHT
    from reportlab.lib.styles import ParagraphStyle

    return ParagraphStyle(
        "tcell-%s-%s-%s" % (size, head, first),
        fontName="Helvetica-Bold" if head else "Helvetica",
        fontSize=size, leading=size + 2.2,
        alignment=TA_LEFT if (first or head) else TA_RIGHT,
        textColor=colors.white if head else colors.black)


def _cell_paragraph(s: str, style):
    """One table cell as a WRAPPING paragraph.

    BL-1 (reviewer 2026-09-14).  This used to be skipped for any string that
    encodes to cp1252 — that is, for every ordinary ASCII cell — and a plain
    string in a reportlab ``Table`` cell is drawn on ONE line with no wrapping
    and no clipping: it runs straight out of its column and over the text
    beside it.  Six tables printed unreadable headers and collided values that
    way (the PWM headers, the bearings' M total / Loss, the mass table's Part
    over Material, the rules table's quantity over its limit, the heat-budget
    labels, the glossary's symbol column).  Every string is a Paragraph now;
    the markup fallback keeps a stray ``&`` or ``<`` in a material name from
    failing the whole build.
    """
    from reportlab.platypus import Paragraph
    try:
        return Paragraph(_txt(s), style)
    except Exception:                                       # noqa: BLE001
        from xml.sax.saxutils import escape
        return Paragraph(_txt(escape(s)), style)


def _table(rows: List[List[Any]], widths: List[float], *,
           header: bool = False, zebra: bool = True, size: float = 8.0):
    """A restrained table: one header band, hairline grid, alternating rows."""
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle

    def _cell(c: Any, i: int, j: int) -> Any:
        if not isinstance(c, str):
            return c
        return _cell_paragraph(c, _cell_style(size, head=(header and i == 0),
                                              first=(j == 0)))

    rows = [[_cell(c, i, j) for j, c in enumerate(row)]
            for i, row in enumerate(rows)]
    t = Table(rows, colWidths=widths, hAlign="LEFT", repeatRows=1 if header else 0)
    cmds = [
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), size),
        ("LEADING", (0, 0), (-1, -1), size + 2.2),
        ("TOPPADDING", (0, 0), (-1, -1), 2.0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.0),
        ("LEFTPADDING", (0, 0), (-1, -1), 4.0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4.0),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D8D8D8")),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
    ]
    if header:
        cmds += [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(NAVY)),
                 ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                 ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                 ("ALIGN", (0, 0), (-1, 0), "LEFT")]
    if zebra:
        start = 1 if header else 0
        for i in range(start, len(rows)):
            if (i - start) % 2 == 1:
                cmds.append(("BACKGROUND", (0, i), (-1, i),
                             colors.HexColor(GREY)))
    t.setStyle(TableStyle(cmds))
    return t


def _image(blob: Optional[bytes], width: float,
           max_height: Optional[float] = None):
    """A platypus Image drawn ``width`` wide; ``max_height`` (points) shrinks a
    tall picture — a full-disc rotor map at page width would be a page tall —
    so the whole picture and its caption still share a page with the table
    they belong to."""
    from reportlab.platypus import Image
    if not blob:
        return None
    try:
        img = Image(io.BytesIO(blob))
        scale = width / float(img.imageWidth or width)
        h = float(img.imageHeight) * scale
        if max_height is not None and h > max_height > 0:
            scale *= max_height / h
            h = max_height
        img.drawWidth = float(img.imageWidth) * scale
        img.drawHeight = h
        img.hAlign = "LEFT"
        return img
    except Exception:                                       # noqa: BLE001
        return None


#: The gap between the two halves of a paired figure, in points.
PAIR_GAP = 8.0

#: ONE HEIGHT CAP FOR EVERY PAIR (user 2026-09-14: *"рисунки делай побольше,
#: раздвигай на всю ширину страницы, для всех, чтобы одинаково было"*).  Each
#: figure used to name its own cap — 0.30, 0.32, 0.42 of the page — so three
#: figures on one page were three different sizes.  One number, and it is loose
#: enough that no pair is ever narrowed by it: a pair ALWAYS spans the whole
#: content width.
PAIR_MAX_H = PAGE_H * 0.46

#: …and the width of ONE half, in centimetres — what a chart of a pair is drawn
#: at.  A chart drawn at the width it is placed at renders its point sizes 1:1
#: on the page, so a half-width chart keeps the same 9 pt labels as a full-width
#: one; only the plot area shrinks, and matplotlib thins its own ticks for it.
PAIR_CM = round((CONTENT_W - PAIR_GAP) / 2.0 / 28.35, 2)


def _image_pair(left: Optional[bytes], right: Optional[bytes], *,
                width: float = CONTENT_W,
                max_height: Optional[float] = None):
    """The two sides of one figure in a single borderless table row.

    Each half is drawn at the same width, so the pair is framed identically and
    a feature at the same place on both pictures sits at the same place on the
    page.  With only one side stored the caller falls back to the single
    full-width figure and the caption names the duty that is missing — an empty
    cell says nothing and costs half the page.
    """
    from reportlab.platypus import Table as _T
    from reportlab.platypus import TableStyle as _TS

    if not (left and right):
        return None
    w = (float(width) - PAIR_GAP) / 2.0
    a = _image(left, w, max_height=max_height)
    b = _image(right, w, max_height=max_height)
    if a is None or b is None:
        return None
    # ONE FRAME FOR BOTH HALVES (CS-6, audit v5).  The two canvases differ by a
    # few pixels when one side's tick labels are wider than the other's, and
    # placed independently the pair came out 1.2 pt apart — a visible step
    # between two pictures that are supposed to be one figure.  The taller
    # aspect wins, so neither half is cropped and both are framed alike.
    _asp = max(a.drawHeight / max(a.drawWidth, 1e-9),
               b.drawHeight / max(b.drawWidth, 1e-9))
    _dw = w
    if max_height is not None and _dw * _asp > max_height > 0:
        _dw = max_height / _asp
    for _im in (a, b):
        _im.drawWidth, _im.drawHeight = _dw, _dw * _asp
    t = _T([[a, b]], colWidths=[w + PAIR_GAP / 2.0, w + PAIR_GAP / 2.0],
           hAlign="LEFT")
    t.setStyle(_TS([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("LEFTPADDING", (0, 0), (0, 0), 0),
        ("LEFTPADDING", (1, 0), (1, 0), PAIR_GAP),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
    ]))
    return t


def _fig_pair(st, left: Optional[bytes], right: Optional[bytes], caption: str,
              *, width: float = CONTENT_W,
              max_height: Optional[float] = None,
              lead: Optional[List[Any]] = None,
              figs: Optional[List[int]] = None) -> Optional[Any]:
    """A paired figure and its caption, as one KeepTogether block.

    Falls back to the single full-width picture when only one side has anything
    stored — never to an empty axes (user 2026-09-14).  ``None`` when neither
    side has a picture, and the caller prints the missing-map sentence instead.

    THE NUMBER IS TAKEN HERE, and only once the picture really exists (MJ-7,
    audit v5).  The callers used to format ``"Fig. %d — …" % fig_no(figs)``
    into the caption before asking whether there was anything to caption, so a
    figure that could not be drawn still burnt a number in the PDF and not in
    Word — and from there on the two documents numbered differently.
    """
    from reportlab.platypus import KeepTogether

    body = _image_pair(left, right, width=width, max_height=max_height)
    if body is None:
        one = left or right
        body = _image(one, width, max_height=max_height)
    if body is None:
        return None
    cap = ("Fig. %d — %s" % (fig_no(figs), caption)
           if figs is not None else caption)
    return KeepTogether(list(lead or []) + [body, _para(cap, st["note"])])


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


def _pick_duty(cfg_doc: Dict[str, Any],
               duty: Optional[str]) -> Optional[Dict[str, Any]]:
    duties = [d for d in (cfg_doc.get("duties") or []) if isinstance(d, dict)]
    if not duties:
        return None
    if duty:
        hit = next((d for d in duties if str(d.get("name") or "") == duty), None)
        if hit is not None:
            return hit
    # continuous-looking first, peak last — the order of the paper card
    duties.sort(key=lambda d: ("peak" in str(d.get("name", "")).lower(),
                               str(d.get("name", ""))))
    return duties[0]


def _geo_delta(cfg_geo: Dict[str, Any], live: Dict[str, Any]) -> List[str]:
    """Geometry keys on which the LIVE machine differs from this configuration.

    A fingerprint answers "same or not" and nothing else; this answers "how",
    which is the only form in which the difference is actionable.  Compared on
    the configuration's own keys, at the resolution a geometry field carries.
    """
    out: List[str] = []
    for k, v in (cfg_geo or {}).items():
        if k not in (live or {}):
            continue
        a, b = v, live.get(k)
        try:
            if abs(float(a) - float(b)) > 1e-6 * max(1.0, abs(float(a))):
                out.append(f"{k} {float(a):g} -> {float(b):g}")
        except (TypeError, ValueError):
            if str(a) != str(b):
                out.append(f"{k} {a} -> {b}")
    return out


def _report_fingerprint(die: str, cfg: str,
                        cfg_doc: Optional[Dict[str, Any]] = None
                        ) -> Optional[str]:
    """The geometry fingerprint of the CONFIGURATION THIS REPORT IS ABOUT.

    BL-1 (audit v6).  Every staleness check in this module used to compare a
    stored answer against ``_live_fingerprint()`` — the machine the SERVER has
    loaded.  With the Ø85 robot motor loaded, the Ø85's own thermal and
    mechanical tab stores matched "live" exactly, nothing was dropped, and
    sections 6 and 7 of a Ø200 client report printed another motor's 408.8 °C
    winding and SF 32.46 rotor beside this machine's 124.8 °C and 0.55.

    A report is about a configuration, so the fingerprint it judges against is
    that configuration's.  Every record this die/cfg's duties filed carries the
    fingerprint of the machine it was solved on (``duty_results`` writes it on
    thermal, rotor_stress, modes, critical_speeds and coupled alike), and they
    agree with each other because they are one build: the value the duty records
    carry IS this configuration's print.  The most frequent one wins, so a
    single record left behind by an older build cannot move the answer.

    ``None`` when this configuration has no stored record at all — the caller
    then falls back to the geometry delta, which is the only other evidence
    there is.
    """
    counts: Dict[str, int] = {}
    try:
        from motor_ai_sim import duty_results as _dr
        stored = _dr.get(die, cfg) or {}
    except Exception as exc:                                    # noqa: BLE001
        log.debug("report: per-duty store unavailable for the fingerprint (%s)",
                  exc)
        return None
    for _duty, rec in (stored or {}).items():
        for _kind, entry in (rec or {}).items():
            fp = (entry or {}).get("geometry_fingerprint") \
                if isinstance(entry, dict) else None
            if fp:
                counts[str(fp)] = counts.get(str(fp), 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0]


class _Source:
    """One solver answer the report leans on, and whether it is this machine.

    "This machine" means THE REPORT'S CONFIGURATION, never the one the server
    happens to have loaded — see :func:`_report_fingerprint` (BL-1, audit v6).
    """

    def __init__(self, name: str, stamp: Any, fp: Any, live_fp: Optional[str],
                 report_fp: Optional[str] = None,
                 delta: Optional[List[str]] = None):
        self.name = name
        # The stamp is KEPT but no longer printed (user 2026-09-10: "я думаю,
        # что метки времени можно вообще убрать").  A reader compared two of
        # them and concluded the sections disagreed, when what they disagreed
        # about was which store answered first; the machine and the operating
        # point are what identify a result, and both are stated already.
        self.stamp = _local_stamp(stamp)
        self.fp = str(fp) if fp else None
        self.report_fp = str(report_fp) if report_fp else None
        # THE CONFIGURATION'S OWN PRINT DECIDES, when it is known: a store whose
        # fingerprint is not this configuration's is another machine's, whatever
        # is loaded.  With no print to judge against, a geometry delta against
        # the loaded machine is proof enough that the machine-level stores are
        # not this report's.  Only when there is neither does the old live
        # comparison stand, and None = UNKNOWN, never "fine": a staleness check
        # that cannot prove a mismatch must not claim one.
        if self.report_fp and self.fp:
            self.stale: Optional[bool] = bool(self.fp != self.report_fp)
        elif delta:
            self.stale = True
        elif not self.fp or not live_fp or live_fp == "nofp":
            self.stale = None
        else:
            self.stale = bool(self.fp != live_fp)

    def line(self) -> str:
        if self.stale is True:
            tail = f' <font color="{WARN}"><b>{FLAG} solved on a DIFFERENT machine</b></font>'
        elif self.stale is None:
            tail = f' <font color="{NOTE}">(machine unverified)</font>'
        else:
            tail = f' <font color="{OK}">this machine</font>'
        return f"<b>{self.name}</b>{tail}"


# ---------------------------------------------------------------------------
# LIMITS AND WARNINGS
# ---------------------------------------------------------------------------
# User, 2026-09-09: *"нужно делать предупреждения, если что-то близко к пределам,
# и предложения, как этого избежать"*.
#
# The engine below is a PURE FUNCTION of one flat dict per duty — no stores, no
# imports, no solver — for two reasons.  The first is that it is the only part of
# this document that makes a JUDGEMENT rather than quoting one, so it is the part
# that has to be testable at the threshold, from both sides, without a six-minute
# FEM run behind it.  The second is that a rule which cannot be read in one place
# is a rule nobody checks: every limit, its source, and the remedy that follows
# from it sit in the same twenty lines.
#
# TWO LEVELS.  ``red`` = past the limit.  ``amber`` = inside 10 % of it — close
# enough that a manufacturing tolerance, a hot day or the next duty point puts
# the machine over, which is the case the user asked to be told about.
#
# WHERE THE LIMIT COMES FROM.  Wherever the machine's own cards carry it — the
# magnet's tensile strength, the sleeve's strength, the bearing's speed rating,
# the pack's minimum voltage — the card is the limit and nothing is hard-coded.
# Where no card carries it (an insulation class, a current density for a given
# cooling, a ripple the user finds acceptable) the number below is the report's
# OWN assumption and every warning built on one says so in its `note`.

#: Maximum working temperature by NdFeB coercivity class, °C.  These are the
#: industry's class definitions, not a property of any one supplier's grade, and
#: they are used only when the card does not carry ``max_working_temp_c``.
MAGNET_CLASS_MAX_C: Dict[str, float] = {
    "": 80.0, "N": 80.0, "M": 100.0, "H": 120.0,
    "SH": 150.0, "UH": 180.0, "EH": 200.0, "AH": 230.0,
}

#: WHAT THE ROTOR BRIDGES ARE FOR, on this die (user 2026-09-15).  A surface
#: magnet rotor whose poles are joined by thin bridges reads as a failed part
#: in every centrifugal solve — the bridges yield at speed and the safety
#: factor on them is well under 1 — and on this machine that is BY DESIGN: the
#: bridges hold the laminations together for assembly, the sleeve carries the
#: magnets, and nothing asks the bridge to carry a load once the band is on.
#: One sentence, printed wherever the rotor's safety factor is.
ROTOR_BRIDGE_POLICY = (
    "the bridges between the poles are assembly-only and carry no load in "
    "operation; the magnets are retained by the sleeve")

#: The parts whose safety factor :data:`ROTOR_BRIDGE_POLICY` is about.
ROTOR_BRIDGE_PARTS = ("rotor", "rotor_core", "rotor core")

#: Insulation classes, °C — IEC 60085.  The materials library's enamel and liner
#: cards are thermal-property cards and carry no rating, so the report assumes
#: the project's own build (200 °C = class N) and says that it did.
INSULATION_CLASS_C: Dict[str, float] = {
    "B": 130.0, "F": 155.0, "H": 180.0, "N": 200.0, "R": 220.0, "S": 240.0,
}
DEFAULT_INSULATION_CLASS = "N"

#: WHAT THIS PROJECT BUILDS TO (user 2026-09-11: "обмотки везде класс 200С").
#: The letters above are the IEC ladder, and 200 °C on it is class N — H is
#: 180 °C.  The document used to call this build "class H" and print 200 °C
#: beside it, which is a contradiction on the page (reviewer 2026-09-14); the
#: NUMBER was never in doubt, only the letter, so the label is the IEC one and
#: the temperature is unchanged.
PROJECT_INSULATION_C = 200.0
PROJECT_INSULATION_LABEL = "N (200 °C)"
#: The same statement as a sentence, for the places that spell the assumption
#: out rather than putting a letter in a cell.
PROJECT_INSULATION_TEXT = ("class N per IEC 60085 (200 °C) — this project's "
                           "build")

#: Current density a winding is designed to, A/mm² rms — THIS PROJECT'S limits,
#: given by the user on 2026-09-10 ("current density in the copper limit 20
#: A/mm²" for the jacketed machine, then "лимиты по воздушному от 10 для
#: закрытых конструкций до 15 для открытых конструкций").
#:
#: Air depends on the FRAME, which is why there are three numbers and not two: a
#: closed machine hands its winding heat to the housing and only then to the
#: air, an open one has the end turns and the slot ducts in the wash and can
#: carry half again as much.  The report already knows which it is — the thermal
#: solve reports `cooling.frame`, and `frame: housed` is a statement there — so
#: the band is chosen rather than assumed.
#:
#: A machine with no thermal answer reads as closed air, the tightest of the
#: three, and the warning says so in its own note.
#:
#: THE JACKET SPLITS BY INSULATION SYSTEM (user 2026-09-14: "исправим лимиты для
#: плотности тока с жидкостным охлаждением: до 20 A/mm² с органической изоляцией
#: и до 25 A/mm² с керамической").  Under a jacket the heat path out of the slot
#: is short and it is the GROUND WALL that sets how hard it may be pushed: an
#: organic liner (aramid paper, polymer film) is a 0.14 W/(m·K) blanket with an
#: organic temperature ceiling, while an alumina liner conducts ~24 W/(m·K) and
#: does not age thermally — the same jacket then carries a quarter more copper
#: loss for the same winding temperature.  Air is UNCHANGED: there the bottleneck
#: is the housing-to-air film, not the liner, so a ceramic liner buys nothing.
J_LIMIT_A_MM2: Dict[str, float] = {
    "air_closed": 10.0,       # totally enclosed: the housing carries the heat
    "air_open": 15.0,         # end turns and slot ducts in the airflow
    "liquid": 20.0,           # water jacket, ORGANIC insulation system
    "liquid_ceramic": 25.0,   # water jacket + a CERAMIC slot liner (Al2O3 …)
}
J_BAND_TEXT = ("this project's limits: air-cooled 10 A/mm² closed and 15 open, "
               "liquid-jacketed 20 A/mm² with an organic insulation system and "
               "25 A/mm² with a ceramic one")

#: What makes a slot liner CERAMIC.  No card in config/materials_library.yaml
#: carries a class / family / ceramic flag — the insulator entries are pure
#: thermal-property cards (sigma, density, k, cp) — so the system is read from
#: the card's NAME and its description, and every note this module writes says
#: that it was read that way.  Matching is substring, case-folded.
CERAMIC_INSULATION_WORDS: Tuple[str, ...] = (
    "al2o3", "al₂o₃", "alumina", "aluminium oxide", "aluminum oxide",
    "aln", "aluminium nitride", "aluminum nitride",
    "ceramic", "glass", "mica", "sio2", "silica", "zro2", "zirconia",
)

#: The liner the report assumes when nothing is assigned — the project's
#: standard organic build (see `INSULATION_ASSUMED` further down, which is the
#: same build spelled for the materials table).
DEFAULT_SLOT_INSULATION = "Nomex"


def _insulator_description(card: str) -> str:
    """The library's own words for an insulator card, '' if there is no card."""
    try:
        from motor_ai_sim.materials import get_insulator
        return str(getattr(get_insulator(card), "description", "") or "")
    except Exception:                                       # noqa: BLE001
        return ""


def insulation_system(mats: Any) -> Tuple[str, str]:
    """``('ceramic' | 'organic', why)`` for the winding's insulation system.

    THE SLOT INSULATION IS THE DECIDING CARD (user 2026-09-14: "с керамической
    изоляцией" is the Al2O3 liner he assigns).  The ground wall is the whole
    series heat path out of the slot and the part that ages; the wire enamel is
    a 30 µm film on the strand, and a polyimide enamel inside an alumina liner
    is still a ceramic-insulated slot as far as the current density goes.  A
    ceramic ENAMEL with an organic liner would not be: the liner would still be
    the blanket.  The enamel is named in the note either way, never voted.
    """
    m = mats if isinstance(mats, dict) else {}
    liner = str(m.get("slot_insulation") or "").strip()
    enamel = str(m.get("wire_insulation") or "").strip()
    assumed = not liner
    if assumed:
        liner = DEFAULT_SLOT_INSULATION
    hay = (liner + " " + _insulator_description(liner)).lower()
    hit = next((w for w in CERAMIC_INSULATION_WORDS if w in hay), "")
    kind = "ceramic" if hit else "organic"
    why = ("the slot liner '%s'%s reads as %s (%s) and is the card that "
           "decides"
           % (liner,
              " (ASSUMED - none assigned)" if assumed else "",
              "CERAMIC" if hit else "ORGANIC",
              ("matched on '%s'" % hit) if hit
              else "no ceramic word in the card's name or description"))
    return kind, why


def current_density_limit(cooling_kind: Any, mats: Any = None,
                          frame_open: Any = None) -> Tuple[Optional[float], str]:
    """``(limit A/mm², why)`` — the ONE place the current-density band lives.

    Air splits by FRAME (open end turns carry half again as much), the jacket
    splits by INSULATION SYSTEM (ceramic ground wall, 25 instead of 20).
    `frame_open` is only consulted for air and `None` (unknown) reads as
    closed, the tighter of the two; `mats` is only consulted for liquid.
    """
    kind = str(cooling_kind or "").strip().lower()
    # THE ROBOT JOINT (2026-09-14): no fan, no jacket, no slipstream — still air
    # on the housing and the bolts into the arm.  It is not a cooling class of
    # its own as far as the copper goes: nothing blows on the end turns, so the
    # band is the air-cooled CLOSED one, the tightest of the four, and the note
    # says which two mechanisms are actually carrying the heat.
    if kind.startswith("robot"):
        lim = J_LIMIT_A_MM2["air_closed"]
        return lim, ("natural convection + conduction to the mount, so the "
                     "limit is the air-cooled CLOSED band, %g A/mm²: nothing is "
                     "blown over the end turns, and the mount's conductance "
                     "moves the TEMPERATURE, not the band" % lim)
    if kind.startswith("liq"):
        ins, why = insulation_system(mats)
        key = "liquid_ceramic" if ins == "ceramic" else "liquid"
        lim = J_LIMIT_A_MM2[key]
        return lim, ("liquid-jacketed, so the limit is %g A/mm²: %s"
                     % (lim, why))
    if kind.startswith("air"):
        lim = J_LIMIT_A_MM2["air_open" if bool(frame_open) else "air_closed"]
        return lim, ("air-cooled, %s frame, so the limit is %g A/mm²; the "
                     "insulation system does not move the air band - there the "
                     "bottleneck is the housing-to-air film, not the liner"
                     % ("open" if bool(frame_open) else "closed", lim))
    return None, ""


def j_limit(cooling_kind: Any, frame_open: Any = None,
            mats: Any = None) -> Optional[float]:
    """Just the number of :func:`current_density_limit` (back-compatible)."""
    return current_density_limit(cooling_kind, mats, frame_open)[0]

#: Torque ripple the user works to (memory: the ripple-gated optimisations run
#: at <= 5 %), and the terminal distortion above which the waveform is worth a
#: look.  Both are this report's own reference numbers.
RIPPLE_LIMIT_PCT = 5.0
THD_LIMIT_PCT = 10.0

#: How close a rotor ring mode may sit to an excitation line — the PWM carrier
#: above all (reviewer 2026-09-14: mode 3 at 24,028 Hz against a 24,000 Hz
#: carrier).  10 % is the modal solver's own flag band
#: (`simulation.mechanical.modal.FLAG_MARGIN`); inside 2 % the mode is ON the
#: line and no tolerance will move it off.
RING_MODE_AMBER_PCT = 10.0
RING_MODE_RED_PCT = 2.0

#: THE LINEAR-MODULATION CEILING OF A TWO-LEVEL INVERTER, in the per-phase
#: convention m = 2·V1_phase,peak / V_dc (reviewer 2026-09-14 / PWM study §1.7).
#:
#: Plain sine-triangle modulation stops at m = 1; with the zero-sequence
#: (SVPWM / third-harmonic) injection every real drive uses, the bridge holds a
#: sinusoidal fundamental up to 2/sqrt(3) = 1.1547, and 1.15 is what drives are
#: specified to.  The LINE-to-line ceiling that follows is
#: V1_LL,peak <= 1.15·(sqrt(3)/2)·V_dc = 0.9959·V_dc — i.e. very nearly the DC
#: link itself, and NOT the peak of the whole solved waveform, which is what
#: this report used to check.  The fundamental is the only thing the bridge has
#: to synthesise; the machine's harmonics it does not.
MOD_INDEX_LIMIT = 1.15
MOD_CEILING_OF_VDC = MOD_INDEX_LIMIT * math.sqrt(3.0) / 2.0

#: WHAT THE BRIDGE'S OWN THD IS, and why it has no limit (reviewer 2026-09-15).
#: The 10 % gate is a statement about the machine's back-EMF — a low-order,
#: sinusoidal-run quantity that a pole arc and a winding factor can change.  A
#: two-level bridge's line voltage is a pulse train: its THD is tens of per
#: cent by construction at ANY design, all of it in the carrier band, and the
#: winding inductance filters it — which is why the number beside it, the
#: CURRENT THD, is the one that says what reaches the machine.
BRIDGE_THD_NOTE = (
    "the THD of the bridge's own pulse train, not of the machine: carrier-band "
    "content, filtered by the winding inductance — it is the current THD "
    "printed beside it that says what reaches the copper and the iron. No "
    "limit: a two-level bridge distorts its line voltage by tens of per cent "
    "whatever the machine is")

#: The same thing in one clause, for the 10 % row's own note.
SINE_THD_NOTE = (
    "10 % is this report's own reference; judged on the SINUSOIDAL run — the "
    "machine's own back-EMF distortion, which is what a pole arc and a winding "
    "factor can change. The bridge's pulse-train THD is the informational row "
    "beside it")

#: A PWM duty is solved on ONE chosen DC link, and that link — not the pack
#: floor — is the voltage everything about it is judged against (reviewer
#: 2026-09-15).  Where the chosen link is the pack's MAXIMUM, the duty exists
#: only while the pack is full, which is a statement a client must not have to
#: derive from two numbers in two tables.
DC_LINK_AMBER_NOTE = "reachable only on a fully charged pack"

#: How close ``inverter.v_dc_V`` must sit to a pack level to read as that level.
DC_LINK_MATCH_V = 1.0

def modulation_index(v1_ll_peak_v: Any, v_dc_v: Any) -> Optional[float]:
    """The two-level bridge's modulation index m = 2·V1_phase,peak / V_dc.

    ``v1_ll_peak_v`` is the FUNDAMENTAL line-to-line amplitude (``V1_LL_V`` of
    the stored summary, times k_3d where the run has a 3-D passport).  The
    per-phase fundamental the modulator works in is V1_LL/sqrt(3) in BOTH star
    and delta — in delta the winding IS the line, but the bridge still swings
    each terminal against the DC link, so the convention does not change with
    the connection.  ``None`` when either number is missing: a gate with no
    input raises no verdict.
    """
    v, vdc = _numf(v1_ll_peak_v), _numf(v_dc_v)
    if v is None or vdc is None or vdc <= 0:
        return None
    return 2.0 * (v / math.sqrt(3.0)) / vdc


def pack_level_words(v_dc: Any, ctx: Dict[str, Any]) -> str:
    """Where a chosen DC link sits in the pack's range, in words.

    ``"the pack maximum (fully charged)"``, ``"the pack nominal"``, ``"the pack
    minimum (fully discharged)"``, or a plain "between" sentence.  The words a
    client reads to learn whether the duty exists on a half-empty pack.
    """
    v = _numf(v_dc)
    lo, nom, hi = (_numf(ctx.get("v_pack_min_v")), _numf(ctx.get("v_pack_nom_v")),
                   _numf(ctx.get("v_pack_max_v")))
    if v is None:
        return ""
    for lvl, words in ((hi, "the pack maximum (fully charged)"),
                       (nom, "the pack nominal"),
                       (lo, "the pack minimum (fully discharged)")):
        if lvl is not None and abs(v - float(lvl)) <= DC_LINK_MATCH_V:
            return words
    if lo is not None and v < float(lo):
        return "BELOW the pack minimum"
    if hi is not None and v > float(hi):
        return "ABOVE the pack maximum"
    if nom is not None and v > float(nom):
        return "between the pack nominal and its maximum"
    if nom is not None:
        return "between the pack minimum and its nominal"
    return "inside the pack's range"


def dc_link_vs_pack_rule(ctx: Dict[str, Any], duty: Any) -> Dict[str, Any]:
    """The PWM duty's own voltage verdict: the link it was SOLVED on, against
    the pack that has to supply it (reviewer 2026-09-15).

    Green while the link is at or under the pack's nominal — a pack at any
    state of charge above half can hold it.  AMBER above nominal, because the
    duty then exists only while the pack is full: the L180 'peak' duty was
    solved on 1,049.8 V, which is the pack MAXIMUM, and no page of the document
    said that its 500 kW is a fully-charged-pack number.  Red outside the pack
    altogether — the duty was solved on a supply this machine does not have.
    """
    v = _numf(ctx.get("v_dc_run_v"))
    lo, nom, hi = (_numf(ctx.get("v_pack_min_v")), _numf(ctx.get("v_pack_nom_v")),
                   _numf(ctx.get("v_pack_max_v")))
    words = pack_level_words(v, ctx)
    level = "green"
    if v is None or (lo is None and hi is None):
        level = "info"
    elif (lo is not None and v < float(lo) - DC_LINK_MATCH_V) or \
         (hi is not None and v > float(hi) + DC_LINK_MATCH_V):
        level = "red"
    elif nom is not None and v > float(nom) + DC_LINK_MATCH_V:
        level = "amber"
    note = ("the DC link the coupled run was actually solved on, against this "
            "pack's range %s … %s (nominal %s)"
            % (_fmt(lo, 1, "V"), _fmt(hi, 1, "V"), _fmt(nom, 1, "V"))
            + (("; this link is %s" % words) if words else ""))
    if level == "amber":
        note += " — %s" % DC_LINK_AMBER_NOTE
    # The margin a reader wants here is how much LINK is left: the distance to
    # the pack's fully-charged top, in per cent of it.  A duty solved at the
    # maximum reads 0.0 % and says so in the same column as every other rule.
    margin = (None if (v is None or hi is None or not float(hi))
              else round(100.0 * (float(hi) - v) / float(hi), 1))
    return {
        "rule": "dc_link_vs_pack", "duty": duty, "level": level,
        "quantity": "DC link this duty was solved on",
        "value": v, "limit": hi, "unit": "V", "kind": "range",
        "margin_pct": margin,
        "remedy": ("Every number of this duty belongs to that link: at a lower "
                   "state of charge the bridge builds less voltage and the "
                   "point is not reachable. Bill the duty at the nominal link, "
                   "or state it as a fully-charged-pack rating."
                   if level in ("amber", "red") else ""),
        "note": note}


#: A separation joint that has lost this much of its contact is reported even
#: when the solve did not call it lift-off.
OPEN_FRACTION_LIMIT_PCT = 50.0

#: WHICH JOINT RETAINS THE MAGNETS, in order of preference (2026-09-10).
#:
#: The rotor's contact solve reports every separation pair it meshed, and until
#: now the retention verdict took the WORST of them.  On a banded rotor that is
#: the wrong pair almost every time.  Measured on the live Ø200:
#:
#:   sleeve-magnet   open 0.0 %,  gap 0 um,   pressure 13.2 MPa at the weakest
#:                   facet                                    -> held, everywhere
#:   sleeve-rotor    open 57.3 %, gap 91 um,  "lift-off" at 16,523 rpm
#:   magnet-rotor    open 45.0 %, gap 287 um
#:
#: and the report cried that the band was coming off.  It is not: the band is
#: pressed onto the MAGNETS over their whole arc.  The other two pairs open
#: because that is what the machine does — the magnet is thrown outward against
#: the band, so it leaves the rotor iron under it, and the band bridges the
#: inter-pole gaps rather than lying on the iron there.  Neither is a load path,
#: and neither has anything to let go of.  The user, who built these rotors:
#: *"нет никакого отслоения бандажа"*.
#:
#: So retention is judged on ONE pair: the band against the magnets when there
#: is a band, the magnets against the rotor when there is not (then the glue or
#: the pocket is all there is).  The rest stay in the detail table as
#: measurements, without a verdict attached.
RETENTION_PAIRS = ("sleeve_magnet", "magnet_rotor")


def retention_interface(case: Dict[str, Any]) -> Tuple[Optional[str],
                                                       Optional[Dict[str, Any]]]:
    """``(label, interface)`` of the joint that holds the magnets on, or
    ``(None, None)`` when the solve meshed no separation pair at all."""
    ifs = (case or {}).get("interfaces") or {}
    for lbl in RETENTION_PAIRS:
        i = ifs.get(lbl)
        if isinstance(i, dict) and str(i.get("type") or "").startswith("separ"):
            return lbl, i
    # An unfamiliar naming still gets a verdict rather than silence: the first
    # separation pair is better than none, and the label says which it was.
    for lbl, i in sorted(ifs.items()):
        if isinstance(i, dict) and str(i.get("type") or "").startswith("separ"):
            return lbl, i
    return None, None

#: Irreversible demagnetisation the design is allowed: the user's criterion is
#: "Br kept >= 99 %", written here as the LOSS so it reads like every other
#: maximum in the table.
DEMAG_LOSS_LIMIT_PCT = 1.0

#: …and the WORST SINGLE ELEMENT of the map, which is a different question
#: (reviewer 2026-09-14, B6).  The volume average is a design criterion; one
#: element down to a fifth of its Br is a pole corner that has stopped being a
#: magnet, and it happens at a corner facing a slot opening long before the
#: average moves.  Both bands are this report's own — no standard fixes them.
DEMAG_WORST_RED_PCT = 80.0
DEMAG_WORST_AMBER_PCT = 90.0

#: How close to a limit is "close" — per cent of the limit.
NEAR_PCT = 10.0


def _numf(v: Any) -> Optional[float]:
    try:
        if v is None or isinstance(v, bool):
            return None
        f = float(v)
        return None if (f != f or f in (float("inf"), float("-inf"))) else f
    except (TypeError, ValueError):
        return None


def _warn(rule: str, duty: str, quantity: str, value: Optional[float],
          limit: Optional[float], unit: str, remedy: str, *,
          kind: str = "max", near: float = NEAR_PCT, note: str = ""
          ) -> Optional[Dict[str, Any]]:
    """One threshold check, or ``None`` when the machine is comfortably inside.

    ``kind='max'`` — the value must stay BELOW the limit (a temperature, a
    stress, a current density).  ``kind='min'`` — it must stay ABOVE it (a
    safety factor, a runaway speed).  ``margin_pct`` is signed and always reads
    the same way: positive is headroom, negative is how far past the limit the
    machine already is.
    """
    v, L = _numf(value), _numf(limit)
    if v is None or L is None or L <= 0:
        return None
    margin = ((L - v) / L * 100.0) if kind == "max" else ((v - L) / L * 100.0)
    over = (v > L) if kind == "max" else (v < L)
    if over:
        level = "red"
    elif margin <= near:
        level = "amber"
    else:
        # GREEN, and it is printed (user 2026-09-10: "помечай шрифты цветом
        # красным превышения предела, зелёным норма; на зелёные не надо писать
        # советов").  A check that passes used to return nothing at all, so the
        # table listed only trouble and the reader could not tell a quantity
        # that was measured and passed from one nobody looked at.  Green rows
        # carry no remedy: there is nothing to do about them.
        level = "green"
    return {"rule": rule, "level": level, "duty": duty, "quantity": quantity,
            "value": v, "limit": L, "unit": unit, "kind": kind,
            "margin_pct": round(margin, 1), "remedy": remedy, "note": note}


#: What the rotor-stress route calls the case a retaining band is sized on
#: (``?cases=three`` = standstill / rated / overspeed).
OVERSPEED_CASE = "overspeed"


def overspeed_remedy_clause(factor: Any, case: Any = None) -> str:
    """The half-sentence about the overspeed case, conditional on there being
    one (reviewer 2026-09-14, B7).

    The remedy used to advise "take the overspeed case down" on machines whose
    stored solve ran at ``overspeed_factor = 1`` — a case that was never
    solved.  With a factor of 1 the advice is the other way round: there is no
    overspeed case, and a retaining band has not been sized the way a band is
    sized until there is one.

    ``case`` CLOSES THE OTHER HALF OF IT (BT-8, 2026-09-16).  A factor above 1
    says the case exists; it does not say this document reports it.  The L180
    gen record carries ``overspeed_factor 1.2`` and ``case "rated"`` — the
    solve ran three cases and filed the rated one — so two red remedies pointed
    a client at an overspeed case that appears on no page of the report.  The
    clause now names the case the document actually carries.
    """
    f = _numf(factor)
    if f is None:
        return ""
    if f > 1.0 + 1e-9:
        _c = str(case or "").strip()
        return (", or take the overspeed case (this duty's speed × %s) down%s"
                % (_fmt(f, 2),
                   "" if not _c or _c.lower() == OVERSPEED_CASE else
                   "; this report carries the '%s' case, not that one" % _c))
    return (". This solve ran at the duty's own speed only (overspeed factor "
            "1) — re-run the rotor stress at 1.2, the case a band is sized "
            "against")


def is_rotor_bridge_part(part: Any) -> bool:
    """True for the part :data:`ROTOR_BRIDGE_POLICY` is about."""
    return str(part or "").strip().lower() in ROTOR_BRIDGE_PARTS


def sleeve_sf_clause(ctx: Dict[str, Any]) -> str:
    """``"sleeve SF 2.06 carries the retention"`` — the number that matters
    where the rotor's own safety factor does not (user 2026-09-15)."""
    sf = _numf((ctx.get("part_safety_factors") or {}).get("sleeve"))
    if sf is None and str(ctx.get("sf_min_part") or "").lower() == "sleeve":
        sf = _numf(ctx.get("sf_min"))
    if sf is None:
        return ""
    return "sleeve SF %s carries the retention" % _fmt(sf, 2)


def rotor_bridge_note(part: Any, ctx: Dict[str, Any]) -> str:
    """The standing policy clause for a rotor safety factor, or ``""``.

    The number and the red flag stay exactly as they are — the bridges really
    do yield at this speed and the report says so — but a client reading
    "SF 0.24" with no further word would read a rotor that flies apart.  The
    sentence says what the bridges are for, and the sleeve's own safety factor
    beside it says what actually holds the poles on.
    """
    if not is_rotor_bridge_part(part):
        return ""
    bits = [ROTOR_BRIDGE_POLICY]
    _sl = sleeve_sf_clause(ctx)
    if _sl:
        bits.append(_sl)
    _f = _numf(ctx.get("overspeed_factor"))
    if _f is not None and _f <= 1.0 + 1e-9:
        bits.append("overspeed 1.2 not solved")
    return ". Standing policy for this rotor (2026-09-15): " + "; ".join(bits)


def rotor_bridge_remedy(part: Any, ctx: Dict[str, Any]) -> str:
    """The same policy, as the remedy's last sentence."""
    if not is_rotor_bridge_part(part):
        return ""
    _sl = sleeve_sf_clause(ctx)
    _f = _numf(ctx.get("overspeed_factor"))
    return (" On this rotor, though, %s%s%s."
            % (ROTOR_BRIDGE_POLICY,
               ("" if not _sl else " — %s" % _sl),
               ("" if _f is None or _f > 1.0 + 1e-9
                else "; overspeed 1.2 not solved")))


def overspeed_glossary_text(factor: Any) -> str:
    """What "Overspeed factor" means for THIS report — the same conditional.

    With a factor above 1 the glossary describes the three cases; with a factor
    of 1 it says outright that only the duty's own speed was solved, rather
    than promising an overspeed case the mechanical section does not contain.
    """
    f = _numf(factor)
    base = ("The rotor stress may be solved at more than one speed: standstill "
            "(the band's fit alone), the duty's own speed, and an OVERSPEED "
            "case — the duty's speed times this factor. The overspeed case is "
            "the one a retaining band is sized against. ")
    if f is not None and f > 1.0 + 1e-9:
        return (base + "On this machine the factor is %s, so each duty is also "
                "checked at %s times its own speed; the safety factors and "
                "verdicts in the comparison tables and the warnings are the "
                "duty's own speed, and the overspeed case is in the mechanical "
                "detail." % (_fmt(f, 2), _fmt(f, 2)))
    if f is not None:
        return (base + "On this machine the factor is 1: every stress in this "
                "report was solved at the duty's own speed and there is NO "
                "overspeed case — re-run the rotor stress with a factor of 1.2 "
                "before the band is signed off.")
    return (base + "No rotor-stress answer on this machine states a factor, so "
            "nothing here is an overspeed case.")


def _ed_limit_clause(ctx: Dict[str, Any]) -> str:
    """The half-sentence the ED rule carries when the cycle was judged against
    a winding limit that is not this report's own insulation limit."""
    a, b = (_numf(ctx.get("duty_cycle_winding_limit_c")),
            _numf(ctx.get("winding_limit_c")))
    if a is None or b is None or abs(a - b) <= 0.5:
        return ""
    return ("; the cycle was judged against %s, not this report's insulation "
            "limit of %s, so the allowable share above belongs to that number"
            % (_fmt(a, 0, "°C"), _fmt(b, 0, "°C")))


def duty_warnings(ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every limit this duty is at or past, worst first.  Pure: dict in, list out.

    ``ctx`` is the flat per-duty view the report assembles from the stored
    results (:func:`_warning_context`); a key that is absent simply switches its
    rule off — a machine with no thermal answer raises no temperature warning,
    which is different from raising a green one.
    """
    duty = str(ctx.get("duty") or "-")
    out: List[Optional[Dict[str, Any]]] = []

    # ── temperatures ────────────────────────────────────────────────────────
    out.append(_warn(
        "magnet_temperature", duty, "Magnet temperature",
        ctx.get("magnet_temp_c"), ctx.get("magnet_limit_c"), "°C",
        "Cool the rotor (bore air or a shaft path), move to a higher-coercivity "
        "grade (SH -> UH -> EH), or cut the rotor loss that is heating it: "
        "segment the magnets axially and check the slot-opening harmonics.",
        note=str(ctx.get("magnet_limit_note") or "")))
    out.append(_warn(
        "winding_temperature", duty, "Winding temperature",
        ctx.get("winding_temp_c"), ctx.get("winding_limit_c"), "°C",
        "Lower the current density (more copper or fewer turns), improve the "
        "housing cooling, or specify a higher insulation class - class R (220 "
        "°C) or S (240 °C) enamel and slot insulation.",
        note=str(ctx.get("winding_limit_note") or "")))
    out.append(_warn(
        "hot_spot", duty, "Hot spot in the machine",
        ctx.get("hot_spot_c"), ctx.get("winding_limit_c"), "°C",
        "The hottest point is not always the winding average: check where it "
        "sits on the temperature map, and open a heat path there (slot-liner "
        "conductivity, potting, an end-winding spray).",
        note=str(ctx.get("winding_limit_note") or "")))

    # ── electromagnetic ─────────────────────────────────────────────────────
    kept = _numf(ctx.get("br_kept_pct"))
    out.append(_warn(
        "demag_br_loss", duty, "Irreversible demagnetisation (Br lost)",
        (None if kept is None else 100.0 - kept), DEMAG_LOSS_LIMIT_PCT, "%",
        "Higher-coercivity grade, cooler magnets, or the load line off the "
        "knee: less current, less negative gamma, a thicker magnet.",
        note="the design criterion is Br kept >= 99 % of the card value"))
    # …and the WORST SINGLE ELEMENT beside it (reviewer 2026-09-14, B6).  A
    # volume average of 97.6 % and one pole corner at 18.4 % of Br are two
    # different machines, and only the first had a rule.
    _worst = _numf(ctx.get("br_worst_pct"))
    _corner = demag_corner_clause(ctx.get("demag_corner"))
    if _worst is not None:
        _lvl = ("red" if _worst < DEMAG_WORST_RED_PCT else
                ("amber" if _worst < DEMAG_WORST_AMBER_PCT else "green"))
        out.append({
            "rule": "demag_worst_element", "level": _lvl, "duty": duty,
            "quantity": "Worst magnet element, Br retained",
            "value": _worst, "limit": DEMAG_WORST_RED_PCT, "unit": "%",
            "kind": "min",
            "margin_pct": round(100.0 * (_worst - DEMAG_WORST_RED_PCT)
                                / DEMAG_WORST_RED_PCT, 1),
            "remedy": ("ONE element, not the average, and it does not come "
                       "back: chamfer or shorten the pole arc at that corner, "
                       "or take the load line off the knee."),
            "note": ("the worst SINGLE element of the demagnetisation map, not "
                     "the volume average — that is the row above. Below %s %% "
                     "of Br that corner has stopped being a magnet; %s to %s %% "
                     "is amber. This report's own band, no standard fixes it"
                     % (_fmt(DEMAG_WORST_RED_PCT, 0),
                        _fmt(DEMAG_WORST_RED_PCT, 0),
                        _fmt(DEMAG_WORST_AMBER_PCT, 0)))
            + ((". " + _corner) if _corner else "")})
    out.append(_warn(
        "torque_ripple", duty, "Torque ripple, low-order (cogging + slotting)",
        ctx.get("ripple_pct"), RIPPLE_LIMIT_PCT, "%",
        "Skew or step the magnets, re-cut the pole arc, revisit the slot "
        "opening, or add a rotor-surface notch; a ripple gate belongs in the "
        "optimisation, not in the acceptance test.",
        note="5 % is this project's own gate, not a standard; judged on the "
             "SINUSOIDAL run — what the geometry makes, which is what skew and "
             "a pole arc can change"))
    # …and the carrier's own, which is NOT a limit (2026-09-14).  A bridge adds
    # tens of per cent of ripple at tens of kilohertz; the rotor inertia is a
    # low-pass filter and none of it reaches the shaft.  It is printed because
    # it is a real number the solve produced and a reader who found it only in
    # section 5 would wonder why nothing checked it.
    _cr = _numf(ctx.get("carrier_ripple_pct"))
    if _cr is not None:
        _thd = _numf(ctx.get("carrier_thd_i_pct"))
        out.append({
            "rule": "carrier_ripple", "level": "info", "duty": duty,
            # …AND THE RUN'S OWN CAVEAT IS IN THE COLUMN THE READER SEES
            # (2026-09-16).  The warnings table prints no note, so a figure the
            # record itself marks as not quotable — the L180 gen 'peak' pass
            # ended with −1 A of DC in the phase current against a ± 0.5 A band
            # — has to say so in its own name or not at all.
            "quantity": ("Torque ripple at the carrier"
                         + ("" if ctx.get("carrier_ripple_quotable") is not False
                            else " (this pass ended with a DC offset)")),
            "value": _cr, "limit": None, "unit": "%", "kind": "info",
            "margin_pct": None, "remedy": "",
            "note": ((CARRIER_RIPPLE_NOTE
                      + ("; carrier %s, current THD %s"
                         % (_fmt((_numf(ctx.get("carrier_hz")) or 0) / 1000.0,
                                 1, "kHz"), _fmt(_thd, 2, "%")))
                      if _thd is not None else CARRIER_RIPPLE_NOTE)
                     # THE RUN'S OWN CAVEAT TRAVELS WITH THE NUMBER
                     # (2026-09-16).  A voltage-fed pass that ends with a DC
                     # offset in the phase current reports a ripple that partly
                     # belongs to the offset; section 5 said so and this row,
                     # which is where the figure is quoted as a finding, did
                     # not (L180 gen 'peak', −1 A against a ± 0.5 A band).
                     + ("" if ctx.get("carrier_ripple_quotable") is not False
                        else (" — this run ended with a DC offset of %s in the "
                              "phase current%s, so part of this figure belongs "
                              "to the offset and not to the machine"
                              % (_fmt(ctx.get("carrier_dc_residual_a"), 2, "A"),
                                 ("" if ctx.get("carrier_dc_tol_a") is None
                                  else " against a ± %s band"
                                  % _fmt(ctx.get("carrier_dc_tol_a"), 2, "A")))
                              )))})
    # …and what the BRIDGE's ripple is once the carrier is filtered out (BT-6,
    # 2026-09-16).  The gate above is judged on the sinusoidal run and stays
    # there — skew and a pole arc change that ripple and nothing else — but the
    # PWM run's own filtered figure is a different number and the L180 gen peak
    # duty's is 6.1 %, past the 5 % gate, printed on no page while the gate's
    # own row read 1.6 % green with 68 % of margin.  Informational, because the
    # rule's basis is the sinusoid and because a pass that ended with a DC
    # offset reports a filtered ripple that partly belongs to the offset — but
    # the number is in the document, beside the two it belongs with, and its
    # own name says when it is past the gate (the warnings table prints no
    # note column).
    _crf = _numf(ctx.get("carrier_ripple_filt_pct"))
    if _crf is not None:
        out.append({
            "rule": "carrier_ripple_filtered", "level": "info", "duty": duty,
            "quantity": ("Torque ripple on the bridge, carrier filtered out"
                         + (", PAST the %s %% gate" % _fmt(RIPPLE_LIMIT_PCT, 0)
                            if _crf > RIPPLE_LIMIT_PCT else "")
                         + ("" if ctx.get("carrier_ripple_quotable") is not False
                            else " (this pass ended with a DC offset)")),
            "value": _crf, "limit": None, "unit": "%", "kind": "info",
            "margin_pct": None, "remedy": "",
            "note": ("what is left of the bridge's ripple once the carrier is "
                     "filtered out — the PWM run's own figure, beside the %s %% "
                     "gate that is judged on the sinusoidal run above"
                     % _fmt(RIPPLE_LIMIT_PCT, 0))})
    # …and the duty CYCLE's own rule: how much of the cycle this point may be
    # on for.  Requested against allowable, both off the record — the one
    # number a robot integrator asks for and the one the steady-state map
    # cannot answer.
    _ed_req, _ed_all = (_numf(ctx.get("ed_requested_pct")),
                        _numf(ctx.get("ed_allowable_pct")))
    if _ed_req is not None and _ed_all is not None:
        out.append(_warn(
            "duty_cycle_ed", duty, "Duty cycle ED (on-time share)",
            _ed_req, _ed_all, "%",
            "Lower the ED, shorten the on-time t_on, or raise the mount "
            "conductance — a bigger flange, a thermal pad, a colder arm; the "
            "cycle time itself buys nothing at a fixed ED.",
            note=("the allowable ED is the share at which the %s peak sits "
                  "exactly on its limit, from this cycle's own ED curve"
                  % str(ctx.get("ed_limiting_part") or "hottest part")
                  + _ed_limit_clause(ctx))))
    elif _ed_all is not None:
        # NOTHING WAS REQUESTED, so there is nothing to fail (2026-09-15): a
        # cycle solved to FIND the allowable regime carries no requested ED,
        # and comparing the allowable with itself would print a green row that
        # checked nothing.  The number is still the section's answer, so it is
        # carried here as what it is — a finding, not a verdict.
        out.append({
            "rule": "duty_cycle_ed", "duty": duty, "level": "info",
            "quantity": "Duty cycle ED (on-time share)",
            "value": _ed_all, "limit": None, "unit": "%", "kind": "info",
            "margin_pct": None, "remedy": "",
            "note": ("no ED was requested — this is the allowable one, the "
                     "share at which the %s peak sits exactly on its limit"
                     % str(ctx.get("ed_limiting_part") or "hottest part")
                     + _ed_limit_clause(ctx))})
    # ── TWO THDs, ONE LIMIT (reviewer 2026-09-15) ───────────────────────────
    # Split exactly as the torque ripple was: the gate is on the MACHINE's own
    # line-voltage distortion, which is the sinusoidal run's, and the bridge's
    # pulse-train THD is an informational row with no limit beside it.
    _is_pwm = str(ctx.get("drive") or "sine") == "pwm"
    out.append(_warn(
        "line_voltage_thd", duty,
        "Line voltage THD" + (" (low-order, sinusoidal run)" if _is_pwm else ""),
        ctx.get("thd_pct"), THD_LIMIT_PCT, "%",
        "A distorted back-EMF costs the current controller headroom: shorten "
        "the magnet arc, check the winding factor for the low harmonics, or "
        "accept it and size the inverter for the harmonic current.",
        note=(SINE_THD_NOTE if _is_pwm else
              "10 % is this report's own reference; no standard fixes it")))
    _bthd = _numf(ctx.get("bridge_thd_pct"))
    if _is_pwm and _bthd is not None:
        _ithd = _numf(ctx.get("carrier_thd_i_pct"))
        out.append({
            "rule": "bridge_thd", "level": "info", "duty": duty,
            "quantity": "Line voltage THD at the bridge (pulse train)",
            "value": _bthd, "limit": None, "unit": "%", "kind": "info",
            "margin_pct": None, "remedy": "",
            "note": (BRIDGE_THD_NOTE
                     + ("" if _ithd is None
                        else ("; current THD %s on this duty"
                              % _fmt(_ithd, 2, "%"))))})
    _cool = str(ctx.get("cooling_kind") or "")
    _open = ctx.get("frame_open")
    _jlim, _jwhy = current_density_limit(_cool, ctx.get("insulation_mats"), _open)
    out.append(_warn(
        "current_density", duty, "Current density in the copper",
        ctx.get("j_coil_a_mm2"), _jlim, "A/mm²",
        "More copper section (thicker wire, more strips in hand, a fuller slot), "
        "a better cooling class, or a ceramic ground wall under a jacket; at this "
        "density the copper loss is what sets the winding temperature.",
        note=("%s - this machine reads as %s" % (J_BAND_TEXT, _jwhy)
              if _jwhy else J_BAND_TEXT)))

    # ── the pack ────────────────────────────────────────────────────────────
    # WHICH VOLTAGE RULES A DUTY GETS DEPENDS ON WHAT FED IT (reviewer
    # 2026-09-15).
    #
    # On a SINUSOIDAL duty nothing is chosen for the machine: the terminal
    # waveform is what the field makes, and the peak of it against the pack
    # floor is the insulation and device-rating statement it always was.
    #
    # On a PWM duty that rule is wrong twice over.  The "waveform peak" of a
    # voltage-fed run is the STAR-EQUIVALENT MODEL's peak — 1.155 × V_dc by
    # construction, a number of the circuit the solver substitutes and not of
    # anything a probe could touch — and the pack FLOOR is not what the duty
    # was solved on: it was solved on one chosen DC link.  So the rule set
    # becomes three questions about that link, and the model peak is never
    # quoted: where the link sits in the pack (below), whether the bridge can
    # build the fundamental on it, and what the insulation sees, which is the
    # link itself and not 1.155 times it.
    if not _is_pwm:
        out.append(_warn(
            "voltage_headroom", duty, "Line voltage, waveform peak",
            ctx.get("v_line_peak_v"), ctx.get("v_pack_min_v"), "V",
            "The peak of the terminal waveform is what the winding insulation "
            "and the bridge's devices see: fewer turns, more field weakening at "
            "this point, or a pack with a higher minimum voltage. Whether the "
            "DRIVE can hold the current is the modulation row below, not this "
            "one.",
            note="limit = the pack's minimum (fully discharged) voltage; the "
                 "value is the peak of the whole solved line-voltage waveform, "
                 "2-D peak × k_3d"
                 + ("" if not _numf(ctx.get("k_3d")) else
                    (" (%s × %s)" % (_fmt(ctx.get("v_line_peak_2d_v"), 1, "V"),
                                     _fmt(ctx.get("k_3d"), 4))))
                 + " — the same factor the modulation row below uses"))
    else:
        out.append(dc_link_vs_pack_rule(ctx, duty))
    # …and THIS is the gate the inverter actually has: the bridge does not have
    # to synthesise the machine's harmonics, it has to synthesise the
    # FUNDAMENTAL, and with zero-sequence injection it reaches
    # V1_LL,peak = 0.9959·V_dc before it leaves linear modulation.
    _m = _numf(ctx.get("mod_index"))
    _mod_against = ("the DC link this duty was solved on"
                    if _is_pwm and ctx.get("v_dc_run_v") is not None
                    else "the pack minimum")
    out.append(_warn(
        "fundamental_vs_modulation", duty,
        "Line voltage, fundamental vs linear modulation",
        ctx.get("v_line_fund_v"), ctx.get("v_mod_ceiling_v"), "V",
        "Past the ceiling a two-level bridge cannot make this fundamental at "
        "all: the current controller saturates and the torque collapses. Fewer "
        "turns, a pack with a higher minimum voltage, more field weakening at "
        "this point, or a modulation with a higher ceiling (overmodulation up "
        "to six-step, paid for in low-order harmonic current and losses).",
        note=("limit = %s x %s (%s), i.e. modulation index "
              "m = 2*V1_phase,peak/V_dc at or under %s with "
              "V1_phase = V1_LL/sqrt(3) in BOTH star and delta%s"
              % (_fmt(MOD_CEILING_OF_VDC, 4), _mod_against,
                 _fmt(ctx.get("v_dc_run_v") if _is_pwm
                      else ctx.get("v_pack_min_v"), 1, "V"),
                 _fmt(MOD_INDEX_LIMIT, 2),
                 ("; this duty sits at m = %s%s"
                  % (_fmt(_m, 3),
                     (" — " + str(ctx["mod_index_note"]))
                     if ctx.get("mod_index_note") else ""))
                 if _m is not None else "")
              # WHERE THIS VALUE COMES FROM (CS-5, audit v7; revised
              # 2026-09-16).  The run's own clamp is the first choice — the
              # fundamental it applied against the largest one the bridge could
              # build on that link — so this row and section 4's V1 are one
              # number.  Only a record without the clamp falls back to the
              # BRIDGE convention V1_LL,peak = m·V_dc·√3/2, which sits a little
              # off section 4 and well off section 3's ×k_3d row.
              + ("" if not (_is_pwm and _m is not None)
                 or ctx.get("mod_ceiling_from_run") else
                 ". The value is the bridge's own V1_LL,peak = m·V_dc·√3/2, so "
                 "it differs slightly from the field's V1 in section 4 and from "
                 "the ×k_3d row in section 3, which are machine-side numbers")
              # …AND WHEN THE RUN SAT ON THE CLAMP, THE MARGIN IS NOMINAL
              # (2026-09-16).  m is the reference's index and the clamp is on
              # the fundamental the modulator APPLIES, which is smaller by the
              # sampled-reference gain: a row reading "1 % left" belongs beside
              # the sentence that says the point could not use it.
              + ("" if not ctx.get("mod_at_ceiling") else
                 ". This run ended ON that ceiling: the loop clamped the "
                 "fundamental at %s%s — what this link can actually build once "
                 "the modulator's sampled-reference gain is compensated — so "
                 "the operating point is inverter-limited and the margin in "
                 "this row is nominal, not usable"
                 % (_fmt(ctx.get("mod_ceiling_v1_v"), 2, "V"),
                    ("" if ctx.get("mod_carriers") is None else
                     " at %d carriers per electrical period"
                     % int(ctx["mod_carriers"])))))))
    # …and what the INSULATION sees on a bridge: the line-to-line pulse
    # amplitude, which is the DC link itself.  A two-level inverter swings each
    # terminal between the rails, so between two terminals the winding sees
    # ± V_dc — never 1.155 × V_dc, which is an artefact of the star-equivalent
    # circuit the voltage-fed solve runs in.  No card on this project states a
    # winding or device voltage rating, so the row is a finding, not a verdict.
    if _is_pwm and _numf(ctx.get("v_dc_run_v")) is not None:
        out.append({
            "rule": "insulation_peak", "duty": duty, "level": "info",
            "quantity": "Bridge line voltage amplitude (what the insulation sees)",
            "value": _numf(ctx.get("v_dc_run_v")), "limit": None, "unit": "V",
            "kind": "info", "margin_pct": None, "remedy": "",
            "note": ("the two-level bridge's line-to-line pulse amplitude = the "
                     "DC link; the winding sees ±%s between two terminals, plus "
                     "whatever the cable reflection adds at the switching edge. "
                     "No insulation or device voltage rating is stated on this "
                     "project, so nothing is checked against it"
                     % _fmt(ctx.get("v_dc_run_v"), 1, "V"))})
    out.append(_warn(
        "runaway_speed", duty, "Runaway speed on cold magnets",
        ctx.get("runaway_rpm"), ctx.get("max_speed_rpm"), "rpm",
        "Above it an uncontrolled machine drives its back-EMF into the pack: "
        "fewer turns, or an active short-circuit armed below this speed.",
        kind="min",
        note="V_line_peak scaled to 20 °C magnets, against the pack minimum"))

    # ── mechanical ──────────────────────────────────────────────────────────
    # ONE STRESS BEHIND EVERY SAFETY FACTOR (reviewer 2026-09-14, B4): the
    # AVERAGED peak of each part's own criterion — the "Criterion" column of
    # the mechanical table — with p99.5 quoted beside it as the singularity
    # gauge, never as the number a part is sized on.
    _sf_note = ("SF 2 on every part is this project's acceptance level; the "
                "stress behind it is the AVERAGED peak of the part's own "
                "criterion, with p99.5 printed beside it as the singularity "
                "gauge")
    out.append(_warn(
        "safety_factor", duty,
        "Mechanical safety factor%s" % (
            " (%s)" % ctx["sf_min_part"] if ctx.get("sf_min_part") else ""),
        ctx.get("sf_min"), ctx.get("sf_limit", 2.0), "",
        "Thicken the sleeve or raise its interference, shorten the magnet "
        "overhang, add material at the bridge root%s.%s"
        % (overspeed_remedy_clause(ctx.get("overspeed_factor"),
                                   ctx.get("overspeed_case")),
           rotor_bridge_remedy(ctx.get("sf_min_part"), ctx)),
        kind="min",
        note=_sf_note + rotor_bridge_note(ctx.get("sf_min_part"), ctx)))
    # …and every OTHER part beside the worst one.  On the L155 peak duty the
    # magnet's own SF is 2.05 against an acceptance level of 2 — the
    # second-tightest number in the document — and nothing fired on it,
    # because only the minimum was ever checked (B5).
    for _part, _sf in sorted((ctx.get("part_safety_factors") or {}).items()):
        if str(_part) == str(ctx.get("sf_min_part") or ""):
            continue                      # the row above already names it
        out.append(_warn(
            "part_safety_factor", duty, "Safety factor (%s)" % _part,
            _sf, ctx.get("sf_limit", 2.0), "",
            "Same levers as the row above: more section, more interference, a "
            "stronger card, or a lower speed case."
            + rotor_bridge_remedy(_part, ctx),
            kind="min", note=_sf_note + rotor_bridge_note(_part, ctx)))
    out.append(_warn(
        "sleeve_hoop", duty, "Sleeve hoop stress",
        ctx.get("sleeve_hoop_mpa"), ctx.get("sleeve_strength_mpa"), "MPa",
        "A hoop-wound sleeve carries the magnets in tension: thicken it, wind a "
        "higher-modulus fibre, or reduce the mass it retains (thinner magnets, "
        "a smaller rotor radius).",
        note="limit = the sleeve card's own strength"))
    out.append(_warn(
        "magnet_tensile", duty, "Magnet tensile stress",
        ctx.get("magnet_stress_mpa"), ctx.get("magnet_tensile_mpa"), "MPa",
        "Sintered NdFeB is brittle and is checked in TENSION: keep the magnet in "
        "compression under the sleeve (more interference), split the pole into "
        "more blocks, or seat it in a deeper pocket.",
        note="limit = the magnet card's tensile strength; the value is the "
             "AVERAGED peak of the magnet's own criterion — the same stress "
             "the safety factor in the mechanical table divides into"
             + ("" if _numf(ctx.get("magnet_stress_p995_mpa")) is None
                else (", with p99.5 at %s as the singularity gauge"
                      % _fmt(ctx.get("magnet_stress_p995_mpa"), 1, "MPa")))))
    out.append(_warn(
        "contact_open", duty, "Separation joint open fraction%s" % (
            " (%s)" % _interface_words(ctx["open_interface"])
            if ctx.get("open_interface") else ""),
        ctx.get("open_fraction_pct"), OPEN_FRACTION_LIMIT_PCT, "%",
        "More interference on the fit, a thicker sleeve, or a form lock (a "
        "dovetail or a deeper pocket) so the joint does not have to be held by "
        "pressure alone.",
        note="a separation joint may open; past half its length it is not a "
             "joint any more"))
    if ctx.get("lift_off"):
        out.append({
            "rule": "lift_off", "level": "red", "duty": duty,
            "quantity": "Lift-off%s" % (
                " (%s)" % _interface_words(ctx["open_interface"])
            if ctx.get("open_interface") else ""),
            "value": None, "limit": None, "unit": "", "kind": "verdict",
            "margin_pct": None,
            "remedy": "The joint has let go at this speed. Raise the sleeve "
                      "interference or thickness, lower the speed case, or "
                      "carry the load by form rather than by friction.",
            "note": "the contact solve reported this pair as lifted off"})
    # A TIE IN TENSION IS A JOINT THAT IS NOT THERE (user 2026-09-14).  The
    # mechanical model may bond a pair the real machine only presses together;
    # when that bond carries TENSION the solve is holding the rotor onto its
    # shaft with glue that does not exist.  Red at this duty's speed, amber when
    # the crossing is above it.  Separation pairs never reach here — the
    # sleeve/rotor and magnet/rotor gaps are open by design.
    for _t in (ctx.get("tie_tension") or []):
        _at = _numf(_t.get("rpm"))
        _lbl = _interface_words(_t.get("interface"))
        _red = bool(_t.get("in_tension"))
        out.append({
            "rule": "tie_in_tension", "level": "red" if _red else "amber",
            "duty": duty,
            "quantity": "Bonded joint in tension (%s)%s" % (
                _lbl, (" at %s" % _fmt(_at, 0, "rpm")) if _at is not None else ""),
            "value": None, "limit": None, "unit": "rpm",
            "kind": "verdict", "margin_pct": None,
            "remedy": ("Nothing in the real machine pulls this joint together: "
                       "give it a shrink fit that stays closed at this speed, "
                       "or carry the load by FORM (key, spline, shoulder)."),
            "note": ("the contact solve reports this %s pair as going into "
                     "TENSION%s — a bonded tie can pull, a press fit cannot, so "
                     "this is the speed at which the real joint would have let "
                     "go. It is NOT an open separation joint: the sleeve/rotor "
                     "and magnet/rotor gaps open by design and raise nothing"
                     % (str(_t.get("type") or "bonded"),
                        (" at %s" % _fmt(_at, 0, "rpm")) if _at is not None
                        else ""))})
    _tp = str(ctx.get("torque_path_verdict") or "").lower()
    if _tp:
        if "held: no" in _tp or "not held" in _tp or "no torque path" in _tp:
            lvl, rem = "red", (
                "Nothing carries the torque but friction, and the solve says "
                "friction is not enough: add a key, a dovetail or a bonded "
                "joint, or raise the interference until the pair stays closed.")
        elif _tp.startswith("no verdict"):
            lvl, rem = "amber", (
                "The torque path could not be judged on this solve - re-run the "
                "rotor stress with loads='both' so the reaction is computed.")
        else:
            lvl, rem = "", ""
        if lvl:
            out.append({"rule": "torque_path", "level": lvl, "duty": duty,
                        "quantity": "Torque path", "value": None, "limit": None,
                        "unit": "", "kind": "verdict", "margin_pct": None,
                        "remedy": rem,
                        "note": str(ctx.get("torque_path_verdict") or "")})

    # ── bearings ────────────────────────────────────────────────────────────
    out.append(_warn(
        "bearing_speed", duty, "Bearing speed%s" % (
            " (%s)" % ctx["bearing_name"] if ctx.get("bearing_name") else ""),
        ctx.get("bearing_rpm"), ctx.get("bearing_limit_rpm"), "rpm",
        "The n·dm limit is a lubrication limit before it is a mechanical one: "
        "an oil-air or grease-for-high-speed fill, a hybrid-ceramic or angular "
        "contact bearing, or a smaller mean diameter.",
        note="limit = the bearing card's own speed rating for this lubrication"))
    out.append(_warn(
        "bearing_temperature", duty, "Bearing temperature%s" % (
            " (%s)" % ctx["bearing_lubricant"]
            if ctx.get("bearing_lubricant") else ""),
        ctx.get("bearing_temp_c"), ctx.get("bearing_temp_limit_c"), "°C",
        "Past it the fill oxidises and the catalogue life no longer holds: "
        "cool the seat, or move to a high-temperature grease or oil-air.",
        note=("limit = the top of the lubricant card's stated temperature "
              "range%s; %s. The SKF rolling term M_rr goes as the base-oil "
              "viscosity to the 0.6, and that viscosity is extrapolated to the "
              "seat temperature by the Walther line whether or not the grease "
              "could survive there - past this limit the friction watts are a "
              "modelled number, not a qualified one"
              % (("" if not ctx.get("bearing_lubricant_range_c") else
                  (" (%s: %s to %s °C)"
                   % (ctx.get("bearing_lubricant") or "the grease",
                      _fmt((ctx["bearing_lubricant_range_c"] or [None, None])[0], 0),
                      _fmt((ctx["bearing_lubricant_range_c"] or [None, None])[1], 0)))),
                 str(ctx.get("bearing_lubricant_source") or "")))))

    # ── a ring mode sitting on an excitation line ───────────────────────────
    # Reviewer 2026-09-14: the L155 peak's mode 3 is 24,028 Hz against a
    # 24,000 Hz carrier — 0.1 % — and the warnings section did not mention it.
    # A mode ON the carrier is an acoustic and a fatigue problem, so it is a
    # rule of its own: amber inside 10 % (the modal solver's own flag band),
    # red inside 2 %, and a margin that is comfortably wider is a green row
    # like any other check that was made and passed.
    _m = _numf(ctx.get("ring_mode_margin_pct"))
    if _m is not None:
        _m = abs(_m)
        _lvl = ("red" if _m <= RING_MODE_RED_PCT else
                ("amber" if _m <= RING_MODE_AMBER_PCT else "green"))
        out.append({
            "rule": "ring_mode_vs_carrier", "level": _lvl, "duty": duty,
            "quantity": "Ring mode vs %s" % (ctx.get("ring_mode_excitation")
                                             or "the nearest excitation"),
            "value": _m, "limit": RING_MODE_AMBER_PCT, "unit": "%",
            # The VALUE is a per cent and the MARGIN is the distance between
            # two per cents — percentage POINTS (CS-11).  Printed as "%" the
            # row read "0.12 % − 10 % = −9.9 %", which is not arithmetic.
            "margin_unit": "pp",
            "kind": "min", "margin_pct": round(_m - RING_MODE_AMBER_PCT, 1),
            "remedy": ("Shift the carrier a few kHz, or stiffen/soften the "
                       "rotor ring — a mode on the carrier is an acoustic and "
                       "fatigue problem."),
            "note": str(ctx.get("ring_mode_note") or "")})

    got = [w for w in out if w]
    got.sort(key=lambda w: (w["level"] != "red",
                            (w.get("margin_pct") if w.get("margin_pct")
                             is not None else 0.0)))
    return got


# ── reading a limit off the machine's own cards ─────────────────────────────


def _magnet_limit(grade: Any) -> Tuple[Optional[float], str]:
    """Maximum working temperature of the assigned magnet, and where it is from.

    The card first (``max_working_temp_c`` when a card ever carries one), then
    the coercivity class read off the grade name — 'N52UH_150C' is a UH grade
    whatever temperature the card was measured at, and UH is a 180 °C class.
    The card's OWN measurement temperature is deliberately NOT used as a limit:
    F52SH_120C is a 150 °C-class magnet measured at 120 °C, and reading 120 as
    the limit would condemn a machine that is fine.
    """
    name = str(grade or "").strip()
    if not name:
        return None, ""
    try:
        from motor_ai_sim.materials import get_material
        card = get_material("magnet", name)
        v = _numf(getattr(card, "max_working_temp_c", None))
        if v:
            return v, f"the {name} card's own maximum working temperature"
    except Exception:                                       # noqa: BLE001
        pass
    try:
        import re
        m = re.match(r"^[A-Za-z]{0,2}\d{2,3}([A-Za-z]{0,2})", name)
        cls = (m.group(1) or "").upper() if m else ""
    except Exception:                                       # noqa: BLE001
        cls = ""
    lim = MAGNET_CLASS_MAX_C.get(cls)
    if lim is None:
        return None, ""
    return lim, (f"ASSUMED: '{name}' reads as a{'n' if cls in ('EH', 'AH') else ''} "
                 f"{cls or 'N'} class magnet, whose maximum working temperature "
                 f"is {lim:g} °C; no card on this machine states one")


def _insulation_limit(mats: Dict[str, Any]) -> Tuple[float, str]:
    """The winding's temperature limit.  Always an ASSUMPTION, always said so.

    The materials library's enamel and liner entries are thermal-property cards
    — conductivity, density, specific heat — and carry no temperature rating, so
    there is nothing on this machine to read.  The project's standard build
    (polyimide enamel, Nomex liner) is qualified to 200 °C, which on the IEC
    60085 ladder is class N — not class H, which is 180 °C (reviewer
    2026-09-14).
    """
    lim = PROJECT_INSULATION_C
    en = str((mats or {}).get("wire_insulation") or "polyimide")
    li = str((mats or {}).get("slot_insulation") or "Nomex")
    return lim, (f"class N per IEC 60085 ({lim:g} °C) for {en} enamel with "
                 f"{li} slot insulation - ASSUMED, no card carries a rating")


def _cold_br_card(grade: Any) -> Optional[Tuple[str, float, float]]:
    """``(card name, Br, Br of the assigned card)`` of the library's cold card of
    the same grade as the assigned magnet (N52UH_20C for N52UH_150C), or None.
    One lookup for the runaway check and for the materials table, so the card
    the check borrows is the card the table lists (reviewer 2026-09-13, item 6).
    """
    name = str(grade or "").strip()
    if not name:
        return None
    try:
        from motor_ai_sim.materials import get_material
        card = get_material("magnet", name)
        br_ref = _numf(getattr(card, "Br", None))
        if not br_ref:
            return None
        base = str(getattr(card, "name", name)).split("_")[0]
        for cold in (f"{base}_20C", f"{base}_25C", f"{base}_30C"):
            try:
                c2 = get_material("magnet", cold)
            except Exception:                               # noqa: BLE001
                continue
            br2 = _numf(getattr(c2, "Br", None))
            if br2:
                return str(getattr(c2, "name", cold)), float(br2), float(br_ref)
    except Exception:                                       # noqa: BLE001
        pass
    return None


def _cold_br_factor(grade: Any) -> Tuple[float, str]:
    """Br(20 °C) / Br(card) for the assigned magnet — the cold-magnet EMF factor.

    A runaway speed computed at the running temperature is the wrong side of the
    question: the back-EMF is highest when the magnets are COLD, which is the
    state an uncontrolled machine is most likely to be spun in.  The 20 °C card
    of the same grade is used when the library has one (N52UH_20C, F52SH_30C);
    otherwise the card's own reversible coefficient walks it there.  ``(1.0, "")``
    when neither is available — the report then says the factor was not applied.
    """
    name = str(grade or "").strip()
    if not name:
        return 1.0, ""
    try:
        _cc = _cold_br_card(name)
        if _cc is not None:
            cold, br2, br_ref = _cc
            return (br2 / br_ref,
                    f"Br from the {cold} card ({br2:g} T) against "
                    f"{name} ({br_ref:g} T)")
        from motor_ai_sim.materials import get_material
        card = get_material("magnet", name)
        br_ref = _numf(getattr(card, "Br", None))
        t_ref = _numf(getattr(card, "temperature_c", None))
        if not br_ref:
            return 1.0, ""
        a = _numf(getattr(card, "alpha_br_pct_per_k", None))
        if a is not None and t_ref is not None:
            k = 1.0 + a / 100.0 * (20.0 - t_ref)
            if k > 0:
                return k, (f"Br walked from the {name} card's {t_ref:g} °C to "
                           f"20 °C at {a:g} %/K")
    except Exception:                                       # noqa: BLE001
        pass
    return 1.0, ""


def _magnet_alpha_br(grade: Any) -> Tuple[Optional[float], Optional[float]]:
    """``(alpha_br %/K, the card's own temperature °C)`` of the assigned magnet."""
    name = str(grade or "").strip()
    if not name:
        return None, None
    try:
        from motor_ai_sim.materials import get_material
        card = get_material("magnet", name)
        return (_numf(getattr(card, "alpha_br_pct_per_k", None)),
                _numf(getattr(card, "temperature_c", None))
                or _magnet_card_temp(name))
    except Exception:                                       # noqa: BLE001
        return None, _magnet_card_temp(name)


def kv_at_magnet_temp(kv_card: Any, grade: Any, t_c: Any
                      ) -> Tuple[Optional[float], str]:
    """The no-load KV walked from the magnet CARD's temperature to ``t_c``.

    The solver's ``KV_noload_rpm_per_V_line`` is a ψ_PM probe taken at the
    card's temperature (``routes/simulation.noload_psi_pm`` takes no run
    temperature), so it reads the same under every duty of a machine — which is
    exactly what the reviewer tripped over on 2026-09-14.  KV goes as 1/Br, so
    the duty's own figure is ``KV_card × Br(card) / Br(T)`` with Br walked
    LINEARLY on the card's reversible coefficient.  ``(None, reason)`` when the
    card carries no dBr/dT: a KV at a temperature nothing was measured at would
    be an invention.
    """
    kv = _numf(kv_card)
    t = _numf(t_c)
    a, t_ref = _magnet_alpha_br(grade)
    if kv is None or t is None:
        return None, "no magnet temperature for this duty"
    if a is None or t_ref is None:
        return None, "card carries no dBr/dT"
    k = 1.0 + a / 100.0 * (t - t_ref)
    if k <= 0:
        return None, "card carries no dBr/dT"
    return kv / k, ("Br walked linearly from the card's %s to %s at %g %%/K"
                    % (_fmt(t_ref, 0, "°C"), _fmt(t, 1, "°C"), a))


def _duty_magnet_temp(em: Dict[str, Any]) -> Optional[float]:
    """The magnet temperature THIS duty's run settled at."""
    return (_numf(_g(em, "coupling.magnet_temp_c"))
            or _numf(_g(em, "magnet_temp_C"))
            or _numf(_g(em, "magnet_temp_c")))


# ---------------------------------------------------------------------------
# ONE COLUMN PER DUTY
# ---------------------------------------------------------------------------
# User, 2026-09-09: *"если в конфигурации несколько режимов, их нужно сравнивать
# в таблицах по всем моделированиям"*.
#
# The electromagnetic side has always been per duty — the Simulation tab saves a
# summary into the configuration's yaml.  Everything else was stored ONCE PER
# MACHINE, for whichever duty was solved last, which is why
# ``motor_ai_sim.duty_results`` exists: each completed thermal / mechanical /
# coupled solve now files a compact copy of itself under the duty the catalog
# context named.
#
# THE RULE THIS SECTION EXISTS TO ENFORCE: a duty with nothing stored prints
# "not solved" — never a number belonging to another duty.  There is exactly one
# exception, and it is not a guess: the machine-level LAST result of a kind
# belongs to the duty the context says is loaded, so for THAT duty it is used,
# and the Sources page says so.

NOT_SOLVED = "not solved"


def _machine_last_entries(th: Dict[str, Any], me: Dict[str, Any],
                          cp: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The three machine-level ``_LAST`` stores, compacted into the same shape
    the per-duty store holds — so a table reads one shape whichever it came
    from, and cannot accidentally print a raw store's key."""
    out: Dict[str, Any] = {}
    try:
        from motor_ai_sim import duty_results as dr
    except Exception:                                       # noqa: BLE001
        return out
    e = (th or {}).get("field") or (th or {}).get("coupled")
    if isinstance(e, dict):
        out["thermal"] = dr.compact_thermal(
            e.get("result") or {}, e.get("params") or {},
            e.get("geometry_fingerprint"), e.get("computed_at"))
    for k in ("rotor_stress", "modes", "critical_speeds"):
        e = (me or {}).get(k)
        if isinstance(e, dict):
            out[k] = dr.compact_mechanical(
                k, e.get("result") or {}, e.get("params") or {},
                e.get("geometry_fingerprint"), e.get("computed_at"))
    if isinstance(cp, dict) and cp:
        out["coupled"] = dr.compact_coupled(cp)
    return out


def _rated_duty(duties: List[Dict[str, Any]], die: str, cfg: str,
                active: Optional[str]) -> Optional[str]:
    """WHICH duty the pictures are of.

    User 2026-09-10: *"картинки моделирования должны быть из rated"*.  A report
    that took its maps from whatever the editor last had open showed the peak
    duty's fields under a document about the rated one, and the two look
    different for a reason.  So: the duty whose name says rated, when it has
    stored fields; then the one the editor has open; then the first that has
    any.  ``None`` when no duty has a single stored field, and the pages then
    fall back to the machine's last solve exactly as before.
    """
    try:
        from motor_ai_sim import duty_fields as _df
    except Exception:                                        # noqa: BLE001
        return None
    names = [str(d.get("name") or "") for d in duties if d.get("name")]

    def _has(n: str) -> bool:
        try:
            return any(_df.load(die, cfg, n, k) is not None
                       for k in ("em", "thermal", "rotor_stress"))
        except Exception:                                    # noqa: BLE001
            return False

    rated = [n for n in names if n.strip().lower().startswith("rated")]
    for n in rated + ([active] if active else []) + names:
        if n and _has(n):
            return n
    return None


def _duty_detail_sources(die: str, cfg: str, duty: Optional[str],
                         fields: Dict[str, Any]) -> Dict[str, Any]:
    """The DUTY's own thermal and mechanical answers, shaped like the
    machine-level stores the detail pages read.

    Sections 5 and 6 read `_last_thermal()` / `_last_mechanical()`, which hold
    ONE answer per server — whatever was solved last.  When that answer belongs
    to another machine it is dropped (rightly), and until now the two sections
    then said "not solved" even though this duty had its own thermal map and
    its own stress solve filed under this die and this configuration.  On
    2026-09-11 the user opened the next motor and the report of the finished one
    lost half its pages.

    Nothing here is invented: the numbers come from `duty_results` (the compact
    record each solve files) and the arrays from `duty_fields` (the map each
    solve stored).  The shape is the one the pages already consume, so there is
    no second rendering path to keep in step.
    """
    out: Dict[str, Any] = {"th": {}, "me": {}}
    if not duty:
        return out
    try:
        from motor_ai_sim import duty_results as _dr
    except Exception:                                        # noqa: BLE001
        return out
    rec = (_dr.get(die, cfg) or {}).get(duty) or {}

    th = rec.get("thermal")
    if isinstance(th, dict):
        res = dict(th)
        fld = (fields.get("thermal") or {}).get("field")
        if isinstance(fld, dict):
            res["field"] = fld
        out["th"]["field"] = {"result": res,
                              "computed_at": th.get("computed_at"),
                              "params": th.get("cooling") or {},
                              "geometry_fingerprint": th.get("geometry_fingerprint")}
    ms = rec.get("rotor_stress")
    if isinstance(ms, dict):
        case_name = str(ms.get("case") or "rated")
        res = {"primary_case": case_name, "cases": {case_name: dict(ms)},
               "rpm": ms.get("rpm"),
               "interference_mm": ms.get("interference_mm"),
               "interference_effective_mm": ms.get("interference_effective_mm"),
               "overspeed_factor": ms.get("overspeed_factor"),
               "lift_off_rpm": ms.get("lift_off_rpm") or {},
               "mesh": ms.get("mesh") or {},
               "part_temps_c": ms.get("part_temps_c") or {}}
        out["me"]["rotor_stress"] = {
            "result": res, "computed_at": ms.get("computed_at"),
            "params": {}, "geometry_fingerprint": ms.get("geometry_fingerprint")}
    for _k in ("critical_speeds", "modes"):
        v = rec.get(_k)
        if isinstance(v, dict):
            out["me"][_k] = {"result": dict(v),
                             "computed_at": v.get("computed_at"),
                             "params": {},
                             "geometry_fingerprint": v.get("geometry_fingerprint")}
    return out


#: `(die, cfg, duty) -> stats or None`, so one build reads each duty's
#: demagnetisation field at most once.  Cleared with the process; the arrays are
#: read-only and a re-solve writes a new file, never mutates this.
_DEMAG_CORNER_CACHE: Dict[Tuple[str, str, str], Optional[Dict[str, Any]]] = {}


def demag_corner_stats(die: str, cfg: str,
                       duty: Optional[str]) -> Optional[Dict[str, Any]]:
    """HOW MUCH of the magnet is actually below the band, from the duty's own
    stored demagnetisation field.

    WHY (user 2026-09-14).  The summary card carries one number — the worst
    single element, ``demag.br_worst_pct``, 18.4 % on the L155 peak duty — and a
    reader cannot tell a pole that is gone from a corner that is.  Verified on
    the stored field: it is the KEPT fraction (``fem_solver_2d`` writes
    ``100 × min(Br factor)``), the minimum really is 18.4 %, and it is a corner —
    160 of 1380 magnet elements sit under 80 % of Br and they are 2.05 % of the
    magnet area.  So the count and the AREA SHARE travel with the number.

    Reads only, and the answer is ``None`` whenever the duty has no stored field
    or it carries no demagnetisation array — the paragraph then prints exactly
    what it printed before.
    """
    if not (die and cfg and duty):
        return None
    key = (str(die), str(cfg), str(duty))
    if key in _DEMAG_CORNER_CACHE:
        return _DEMAG_CORNER_CACHE[key]
    out: Optional[Dict[str, Any]] = None
    try:
        import numpy as np
        from motor_ai_sim import duty_fields as _df
        from motor_ai_sim.simulation.sb_domains import (DOM_COIL_BASE,
                                                        DOM_MAG_BASE)
        em = _df.load(die, cfg, duty, "em")
        coef = None if em is None else em.get("demag_coef_per_tri")
        if coef is not None and em.get("tags") is not None:
            coef = np.asarray(coef, float)
            tags = np.asarray(em.get("tags"), int)
            # MAGNETS ONLY, bounded above as well — the coils carry a
            # coefficient of 1.0 and would dilute every share below.
            mag = (tags >= int(DOM_MAG_BASE)) & (tags < int(DOM_COIL_BASE))
            if mag.shape == coef.shape and bool(mag.any()):
                P = np.asarray(em.get("vertices"), float)
                T = np.asarray(em.get("triangles"))
                if P.shape[0] == 2 and P.shape[1] != 2:
                    P = P.T
                if T.shape[0] == 3 and T.shape[1] != 3:
                    T = T.T
                tri = T[mag]
                x, y = P[:, 0], P[:, 1]
                ar = 0.5 * np.abs(
                    (x[tri[:, 1]] - x[tri[:, 0]]) * (y[tri[:, 2]] - y[tri[:, 0]])
                    - (x[tri[:, 2]] - x[tri[:, 0]]) * (y[tri[:, 1]] - y[tri[:, 0]]))
                m = coef[mag]
                tot = float(ar.sum()) or 1.0
                out = {
                    "n_elements": int(m.size),
                    "min_pct": round(100.0 * float(m.min()), 2),
                    "p1_pct": round(100.0 * float(np.percentile(m, 1.0)), 2),
                }
                for thr, tag in ((0.5, "50"), (0.8, "80"), (0.9, "90")):
                    sel = m < thr
                    out["n_below_%s" % tag] = int(sel.sum())
                    out["area_below_%s_pct" % tag] = round(
                        100.0 * float(ar[sel].sum()) / tot, 3)
    except Exception as exc:                                 # noqa: BLE001
        log.debug("report: demag corner stats unavailable (%s)", exc)
        out = None
    _DEMAG_CORNER_CACHE[key] = out
    return out


def demag_corner_clause(st: Optional[Dict[str, Any]]) -> str:
    """The corner sentence both the paragraph and the rule note hang off, or
    ``""`` when the field could not be read."""
    if not st or not st.get("n_elements"):
        return ""
    n, tot = int(st.get("n_below_80") or 0), int(st["n_elements"])
    # THE COUNT IS A MESH'S, NOT A ROTOR'S (CS-8, audit v7).  Two duties of one
    # machine are meshed separately — 1,774 magnet elements on the rated duty
    # against 1,525 on the peak — and quoting both as bare facts invites the
    # reader to take the difference for a geometry change.  The AREA share
    # beside it is the number that does not depend on the mesh.
    if not n:
        return ("No magnet element is below 80 %% of Br: the worst of the %d "
                "magnet elements of this duty's mesh keeps %s."
                % (tot, _fmt(st.get("min_pct"), 1, "%")))
    return ("It is a CORNER, not a pole: %d of the %d magnet elements of this "
            "duty's mesh are below "
            "80 %% of Br and they are %s of the magnet area (%d below 50 %%, %s "
            "of the area); the 1st percentile of the map is %s."
            % (n, tot, _fmt(st.get("area_below_80_pct"), 2, "%"),
               int(st.get("n_below_50") or 0),
               _fmt(st.get("area_below_50_pct"), 2, "%"),
               _fmt(st.get("p1_pct"), 1, "%")))


def _duty_map_sources(die: str, cfg: str, duty: Optional[str]) -> Dict[str, Any]:
    """The stored fields of ONE duty, shaped the way the map builders read.

    The arrays come back from ``duty_fields`` exactly as the solver wrote them,
    so a map drawn from here is the map that duty was solved with — not the
    machine's last solve, which belongs to whichever tab was pressed last.
    """
    out: Dict[str, Any] = {}
    if not duty:
        return out
    try:
        from motor_ai_sim import duty_fields as _df
    except Exception:                                        # noqa: BLE001
        return out

    # WHEN EACH MAP WAS SOLVED, kept beside the arrays (reviewer 2026-09-15).
    # A field and the record the tables beside it are built from are two
    # different answers, and on the L180 gen report they were two different
    # SOLVES — a sinusoidal map of 2026-09-13 under a PWM table of 2026-09-16,
    # with nothing on the page saying so.  The captions compare these.
    meta: Dict[str, Any] = {}
    out["_meta"] = meta

    em = _df.load(die, cfg, duty, "em")
    if em is not None:
        meta["em"] = dict(em.get("meta") or {})
    if em is not None and em.get("vertices") is not None:
        out["em"] = {
            "P_mm": em.get("vertices"), "T": em.get("triangles"),
            "tags": em.get("tags"),
            "b_mag": em.get("b_mag_per_tri"),
            "a_z_per_node": em.get("a_z_per_node"),
            "loss_dens": em.get("loss_dens_per_tri"),
            "demag_coef_per_tri": em.get("demag_coef_per_tri"),
            "loss_dens_label": (em.get("meta") or {}).get("loss_dens_label") or "",
        }
    th = _df.load(die, cfg, duty, "thermal")
    if th is not None:
        meta["thermal"] = dict(th.get("meta") or {})
    if th is not None and th.get("temperature_per_node") is not None:
        out["thermal"] = {"field": {
            "vertices": th.get("vertices"), "triangles": th.get("triangles"),
            "temperature_per_node": th.get("temperature_per_node"),
            "domain_per_tri": th.get("domain_per_tri"),
        }}
    md = _df.load(die, cfg, duty, "modes")
    if md is not None:
        meta["modes"] = dict(md.get("meta") or {})
    if md is not None and md.get("u_modes") is not None:
        _mm = md.get("meta") or {}
        out["modes"] = {
            "body": _mm.get("body"), "support": _mm.get("support"),
            "rpm": _mm.get("rpm"), "modes": _mm.get("modes") or [],
            "field": {
                "vertices": md.get("vertices"), "triangles": md.get("triangles"),
                "domain_per_tri": md.get("domain_per_tri"),
                "extent": _mm.get("extent"), "modes": md.get("u_modes"),
            },
        }
    ms = _df.load(die, cfg, duty, "rotor_stress")
    if ms is not None:
        meta["rotor_stress"] = dict(ms.get("meta") or {})
    if ms is not None and ms.get("vm_per_tri") is not None:
        case = str((ms.get("meta") or {}).get("case") or "rated")
        out["rotor_stress"] = {
            "primary_case": case,
            "field": {
                "vertices": ms.get("vertices"), "triangles": ms.get("triangles"),
                "domain_per_tri": ms.get("domain_per_tri"),
                "cases": {case: {
                "vm_per_tri": ms.get("vm_per_tri"),
                "u_mag_per_node": ms.get("u_mag_per_node"),
                "u_per_node": ms.get("u_per_node"),
                "sf_per_tri": ms.get("sf_per_tri"),
            }},
            },
        }
    return out


# ---------------------------------------------------------------------------
# WHERE A PICTURE CAME FROM, AGAINST WHERE THE TABLE BESIDE IT CAME FROM
# (reviewer 2026-09-15)
# ---------------------------------------------------------------------------
# A map is an npz the solve filed; the table beside it is the compact record
# the same tab filed.  They are normally one answer stored twice — and they are
# NOT when a later run refreshed the record and not the field.  On the L180 gen
# report the thermal maps were the sinusoidal solve of 2026-09-13 (hottest
# solid 153.9 / 168.4 °C) and every table on the page was the PWM run of
# 2026-09-16 (175.2 / 219.0 °C).  Both are honest numbers of two different
# machines, and the document mixed them silently.
#
# Never again: every map caption compares the two provenances, and where they
# differ it says which solve the picture is and which run the table is, and
# labels the caption's own numbers as the MAP's.

#: How far apart two stamps of one solve may sit and still be that solve.
MAP_PROVENANCE_TOL_S = 3600.0

#: Which stored record each FIELD kind's tables are built from.  ``em`` is the
#: exception with two answers: on a duty whose coupled run was the inverter's,
#: the electromagnetic tables are that coupled record's, not the `em` one's.
_FIELD_RECORD_KIND = {"thermal": "thermal", "rotor_stress": "rotor_stress",
                      "modes": "modes", "em": "em"}


def _when_of(x: Any) -> Optional[Tuple[bool, Any]]:
    """``(aware, datetime)`` of a stored stamp, or ``None``.

    The stores write both shapes — a UTC-offset ``computed_at`` from the solve
    routes and a naive local one from the older paths — so the kind travels
    with the value and only like is compared with like.
    """
    s = str(x or "").strip()
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return (d.tzinfo is not None, d)


def same_solve(a: Any, b: Any) -> Optional[bool]:
    """Do these two stamps describe one solve?  ``None`` when it cannot be told.

    Two stamps of the same kind are compared with an hour of slack; a naive
    stamp against an offset one can differ by the machine's time zone and no
    more than the DATE is compared, which is what the question is really about.
    """
    wa, wb = _when_of(a), _when_of(b)
    if wa is None or wb is None:
        return None
    if wa[0] == wb[0]:
        return abs((wa[1] - wb[1]).total_seconds()) <= MAP_PROVENANCE_TOL_S
    return wa[1].date() == wb[1].date()


def _solve_words(drive: Any) -> str:
    """"PWM run" | "sinusoidal solve" — a record with no ``drive`` predates the
    inverter loop and IS a sinusoid (the same reading as :func:`record_drive`)."""
    return ("PWM run" if record_drive({"drive": drive}) == "pwm"
            else "sinusoidal solve")


def _day_of(x: Any) -> str:
    w = _when_of(x)
    return w[1].date().isoformat() if w else "an unrecorded date"


def map_provenance_note(field_meta: Optional[Dict[str, Any]],
                        record: Optional[Dict[str, Any]]) -> str:
    """``""`` when the map and the table beside it are one solve; otherwise the
    sentence that says they are not.

    Nothing is guessed: a field or a record with no stamp cannot be compared
    and says nothing, which is the same silence the document had before — the
    sentence appears only where the two stamps really disagree.
    """
    if not isinstance(field_meta, dict) or not isinstance(record, dict):
        return ""
    fw, rw = field_meta.get("computed_at"), record.get("computed_at")
    same = same_solve(fw, rw)
    if same is None or same:
        return ""
    return ("map from the %s of %s; the table beside it is the %s of %s — the "
            "numbers in this caption are the MAP's"
            % (_solve_words(field_meta.get("drive")), _day_of(fw),
               _solve_words(record.get("drive")), _day_of(rw)))


def duty_map_provenance(rec: Optional[Dict[str, Any]],
                        metas: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """``{field kind: the caption's provenance sentence}`` for one duty.

    Only kinds whose two stamps actually disagree get an entry, so a caller can
    ask for any kind and get ``""`` when there is nothing to say.
    """
    out: Dict[str, str] = {}
    if not isinstance(rec, dict) or not isinstance(metas, dict):
        return out
    for kind, rkey in _FIELD_RECORD_KIND.items():
        meta = metas.get(kind)
        if not isinstance(meta, dict):
            continue
        # The electromagnetic tables of a duty solved on the inverter are the
        # COUPLED record's, so that is the record its field is compared with.
        r = rec.get(rkey)
        if kind == "em" and record_drive(rec.get("coupled")) == "pwm":
            r = rec.get("coupled")
        note = map_provenance_note(meta, r if isinstance(r, dict) else None)
        if note:
            out[kind] = note
    # The Campbell diagram is drawn from the modal field against the critical
    # speeds record; where the modal map is already flagged, so is it.
    if "modes" in out:
        out.setdefault("critical_speeds", out["modes"])
    return out


def _duty_record(die: str, cfg: str, duty: Optional[str]) -> Dict[str, Any]:
    """The compact per-duty record every table on these pages is built from."""
    if not duty:
        return {}
    try:
        from motor_ai_sim import duty_results as _dr
        rec = (_dr.get(die, cfg) or {}).get(duty)
    except Exception:                                        # noqa: BLE001
        return {}
    return rec if isinstance(rec, dict) else {}


def _duty_at_point(duties: List[Dict[str, Any]], rpm: Any, cur: Any) -> Optional[str]:
    """The one duty of this configuration solved at this speed and current.

    None when nothing matches, and none when more than one does — a report that
    guesses between two duties is worse than one that says it does not know.
    The current is compared only when BOTH sides carry it: a rotor-stress solve
    knows a speed and a torque, not a winding current.
    """
    rpm, cur = _numf(rpm), _numf(cur)
    if not rpm:
        return None
    hits = []
    for d in duties:
        d_rpm, d_cur = _numf(d.get("rpm")), _numf(d.get("current_arms"))
        if not d_rpm or abs(d_rpm - rpm) > max(1.0, 0.01 * d_rpm):
            continue
        if cur and d_cur and abs(d_cur - cur) > max(0.5, 0.02 * d_cur):
            continue
        hits.append(str(d.get("name") or ""))
    return hits[0] if len(hits) == 1 else None


def _match_duty_by_point(duties: List[Dict[str, Any]],
                         last: Dict[str, Any]) -> Dict[str, str]:
    """Which duty each machine-level LAST answer was solved at, PER KIND.

    2026-09-09.  User: *"почему написано not solved везде?"* — the catalog
    context names the duty the EDITOR has open, so a report of any machine the
    user is not editing at that minute attributed nothing: every comparison cell
    said "not solved" and every map was captioned "no duty identified", while
    the answers themselves sat in the stores with the speed and the current they
    were solved at printed right there in the caption.

    A stored answer belongs to a duty of THIS configuration when its speed and
    its phase current are that duty's, and no other duty's.  PER KIND, because
    the three stores are filled at different moments and need not agree: on the
    G2-L40 the last thermal map was solved at 2 200 rpm and the last rotor
    stress at 3 000, which is two duties, not an ambiguity — one owns the
    temperatures, the other the stresses, and each column says so.
    """
    out: Dict[str, str] = {}
    for kind, entry in (last or {}).items():
        p = (entry or {}).get("point") or {}
        name = _duty_at_point(
            duties, p.get("rpm"),
            p.get("I_phase_rms") or p.get("current_arms"))
        if name:
            out[kind] = name
    return out


def _duty_columns(die: str, cfg: str, cfg_doc: Dict[str, Any],
                  th: Dict[str, Any], me: Dict[str, Any],
                  cp: Optional[Dict[str, Any]],
                  same_machine: bool = True) -> Tuple[List[Dict[str, Any]],
                                                         Optional[str]]:
    """One column per saved duty, and the duty the editor currently has loaded.

    Order is the paper card's: continuous-looking points first, peak last —
    the same sort ``datasheet.build_datasheet`` uses, so the two documents read
    left to right the same way.
    """
    duties = [d for d in (cfg_doc.get("duties") or []) if isinstance(d, dict)]
    duties.sort(key=lambda d: ("peak" in str(d.get("name", "")).lower(),
                               str(d.get("name", ""))))
    stored: Dict[str, Any] = {}
    active: Optional[str] = None
    try:
        from motor_ai_sim import duty_results as dr
        stored = dr.get(die, cfg)
        ctx = dr.active_context()
        if ctx and ctx[0] == die and ctx[1] == cfg:
            active = ctx[2]
    except Exception as exc:                                # noqa: BLE001
        log.debug("report: per-duty store unavailable (%s)", exc)
    last = _machine_last_entries(th, me, cp)
    # WHO OWNS each machine-level answer.  The context's duty owns all of them
    # while the editor has this configuration open; otherwise each is matched to
    # the duty it was solved at (2026-09-09) — see `_match_duty_by_point`.
    if active is not None:
        owners = {k: active for k in last}
        why = "this duty is the one loaded"
    else:
        # …and only when those answers are THIS machine's at all.  The three
        # stores hold the LIVE machine's last solve; a report of a configuration
        # whose geometry differs from what is loaded must attribute none of them
        # to it, however well a speed happens to line up (2026-09-09: the G2's
        # report was written while the editor had the Ø200 open, and the Ø200's
        # 20,900 rpm map is not any G2 duty's).
        owners = (_match_duty_by_point(duties, last) if same_machine else {})
        why = ("matched by the operating point it was solved at — the editor "
               "has another machine open")

    cols: List[Dict[str, Any]] = []
    for d in duties:
        name = str(d.get("name") or "-")
        res = dict(stored.get(name) or {})
        origin = {k: "the per-duty store" for k in res}
        for k, v in last.items():
            if owners.get(k) != name:
                continue
            res[k] = v
            origin[k] = ("the machine's last %s solve (%s)"
                         % (k.replace("_", " "), why))
        cols.append(apply_pwm_view({
            "duty": name,
            "d": d,
            "em": (d.get("summary") if isinstance(d.get("summary"), dict) else {}),
            "result": (d.get("result") if isinstance(d.get("result"), dict) else {}),
            "res": res,
            "origin": origin,
            "active": (name == active),
        }, cfg_doc))
    return cols, owners


# ---------------------------------------------------------------------------
# WHICH SUPPLY A DUTY RUNS ON  (2026-09-14)
# ---------------------------------------------------------------------------
# Until tonight every number in this document was the SINUSOID and section 5 was
# the one place that said what a real bridge costs.  The coupled loop can now be
# run on the inverter (`routes.coupled`, drive="pwm"), and when a duty's coupled
# answer was reached that way the inverter IS that duty's operating condition:
# its temperatures, its losses, its efficiency and its warnings are the PWM
# run's, because that is the machine the client will build.
#
# The switch is made in ONE place — here — by overlaying the PWM run's own
# summary onto the duty's saved sinusoidal one.  Everything downstream keeps
# reading `col["em"]` and needs to know nothing about carriers; a quantity the
# PWM run does not measure (the no-load KV, Kt, the inductances, the 3-D
# passport) falls through to the sinusoidal summary underneath, which is where
# it was always solved.

#: What a stored record's ``drive`` says when it means the inverter — the same
#: three spellings :func:`duty_results._entry_drive` accepts.
PWM_DRIVE_WORDS = ("pwm", "pwm_voltage", "inverter")

#: The key a duty's PWM run is filed under in its ``runs`` sidecar map
#: (``routes.family._RUN_DRIVES``).
PWM_RUN_DRIVE = "pwm_voltage"

#: Quantities that stay the SINUSOIDAL run's even on a PWM duty.  Not a
#: convenience: the machine constants are properties of the geometry and the
#: winding measured on a clean sinusoid, and the connection is the duty's own
#: (the inverter path solves a star-EQUIVALENT circuit and would report "star"
#: for a delta machine).
#:
#: ``demag`` IS NOT ONE OF THEM (BL-1, audit v7).  It was kept here while the
#: maps of section 4 were drawn from the sinusoidal field; they are the PWM
#: run's since 2026-09-16, and keeping the scalars behind made the document
#: certify the peak duty at 2.383 % of Br lost with its own map beside it
#: showing a worst element at 6.9 % and a fifth of the magnet area under 80 %.
#: Demagnetisation is PERMANENT DAMAGE done by the duty's own condition — the
#: carrier's current ripple included — so it is the PWM run's, like every other
#: quantity that run measured, and the rules in section 8 fire on those
#: numbers.
#:
#: ``R_`` IS NOT ONE OF THEM EITHER (MJ-4, audit v7).  A resistance is quoted
#: at a temperature, and the two runs settle at different ones: the sinusoid's
#: 5.253 mOhm at 97.8 °C was printed under an operating-point box stating
#: 117.6 °C, where this run's own resistance is 5.566 mOhm.  The row carries
#: the temperature it belongs to — see :func:`em_constant_rows`.
#:
#: ``end3d`` IS NOT ONE OF THEM (BL-4, audit v6).  The block carries k_flux — a
#: property of the geometry, the same under either supply — but also
#: ``T_corrected_Nm`` and ``V_line_peak_corrected_V``, which are that RUN's own
#: 2-D values times k.  Keeping the sinusoid's put 179.898 N·m (the sine's
#: 187.855 × k) in the row directly under the PWM run's 2-D 179.22, and 386.4 V
#: (the sine's 403.5 × k) under the PWM run's 593.2 V peak, on one table of one
#: duty.  The block is merged and the two corrected values are rebuilt from the
#: run the rest of the row belongs to — see :func:`_pwm_end3d`.
#: THE KEEP-LIST HAS TO NAME THE KEYS THE SUMMARY ACTUALLY USES (BT-2,
#: 2026-09-16).  Two of these guards matched nothing and were dead: ``"L_"``
#: against inductances filed as ``Ld_mH`` / ``Lq_mH`` / ``Ld_eq_star_mH`` /
#: ``Lq_eq_star_mH`` / ``L0_mH``, and ``"saliency_ratio"`` against
#: ``saliency_Lq_over_Ld``.  So section 4 printed the SINUSOID's Ld beside the
#: PWM run's Lq in one table — the rated L180 gen duty read Ld 0.0976 mH,
#: Lq 0.0718 mH and "Saliency Lq/Ld 0.71", three numbers that do not close
#: (0.0718/0.0976 = 0.736) — under a closing sentence stating that every
#: constant in the table is the sinusoidal run's.  Ld survived only because the
#: voltage-fed run recorded none; where it records one, both halves flip.
_PWM_KEEP_SINE = ("star_delta",)
_PWM_KEEP_SINE_PREFIX = ("KV", "Kt", "Km", "Ld", "Lq", "L0", "psi",
                         "saliency", "V1_seed")

#: The coupled record's ``em`` block, in the keys a saved summary uses.
_PWM_EM_KEYS = ("T_em_avg_Nm", "T_ripple_pct", "P_stranded_W", "P_core_W",
                "P_solid_W", "P_mag_W", "P_shaft_W", "P_sleeve_W",
                "P_loss_total_W", "efficiency", "THD_I_pct", "THD_LL_pct",
                "V_line_peak_V", "n_steps_per_period")


def record_drive(entry: Any) -> str:
    """``"pwm"`` | ``"sine"`` — what excitation a stored record describes.

    A record with no ``drive`` at all predates the inverter loop and is a
    sinusoid; reading the absence that way is a statement of fact, not a
    default (same rule as :func:`duty_results._entry_drive`).
    """
    d = (str((entry or {}).get("drive") or "").strip().lower()
         if isinstance(entry, dict) else "")
    return "pwm" if d in PWM_DRIVE_WORDS else "sine"


def duty_drive(col: Dict[str, Any]) -> str:
    """``"pwm"`` when THIS duty's coupled answer was reached on the inverter."""
    return record_drive((col.get("res") or {}).get("coupled"))


def duty_inverter(col: Dict[str, Any]) -> Dict[str, Any]:
    """The bridge that fed this duty's coupled run — ``{}`` on a sinusoid."""
    rec = (col.get("res") or {}).get("coupled")
    inv = rec.get("inverter") if isinstance(rec, dict) else None
    return dict(inv) if isinstance(inv, dict) else {}


def sine_line_thd(col: Dict[str, Any]) -> Optional[float]:
    """The LINE-voltage THD of this duty's SINUSOIDAL run, or ``None``.

    The 10 % gate is a statement about the machine's own back-EMF, so on a PWM
    duty it is read here and not from the bridge's pulse train (reviewer
    2026-09-15).  The coupled record's ``reference_sine`` first — it is the
    sine solved at the SAME temperatures as the PWM pass — then the duty's own
    saved sinusoidal summary.
    """
    rec = (col.get("res") or {}).get("coupled")
    ref = (rec.get("reference_sine") if isinstance(rec, dict) else None)
    em = (ref.get("em") if isinstance(ref, dict)
          and isinstance(ref.get("em"), dict) else None)
    v = _numf((em or {}).get("THD_LL_pct") or (em or {}).get("THD_pct"))
    if v is not None:
        return v
    s = col.get("em_sine") if isinstance(col.get("em_sine"), dict) else {}
    return _numf(s.get("THD_LL_pct") or s.get("THD_pct"))


def _with_inverter(wf: Any, col: Any) -> Dict[str, Any]:
    """A PWM run's waveforms with the bridge that produced them attached.

    The payload's own ``pwm`` block is the primary source and is left alone;
    this only carries the coupled record's ``inverter`` description alongside,
    so the voltage chart can regenerate the pulse train on a run that was saved
    without one.  A sinusoidal duty gets nothing back but what it had.
    """
    if not isinstance(wf, dict) or not wf:
        return wf if isinstance(wf, dict) else {}
    inv = duty_inverter(col or {})
    if not inv or isinstance(wf.get("inverter"), dict):
        return wf
    out = dict(wf)
    out["inverter"] = inv
    return out


def pwm_run_sidecar(cfg_doc: Dict[str, Any], duty: Any) -> Dict[str, Any]:
    """The duty's stored PWM RUN record — ``runs["pwm_voltage"]`` of the yaml.

    That is where the inverter run's full summary and its waveform pointer live
    (``routes.family._store_run``); the duty's top-level ``summary`` stays the
    sinusoidal one, which is exactly why the overlay below is needed.
    """
    d = next((x for x in ((cfg_doc or {}).get("duties") or [])
              if isinstance(x, dict)
              and str(x.get("name") or "") == str(duty or "")), None)
    r = ((d or {}).get("runs") or {}).get(PWM_RUN_DRIVE)
    return dict(r) if isinstance(r, dict) else {}


def _pwm_summary_from_record(rec: Dict[str, Any]) -> Dict[str, Any]:
    """The coupled record's own blocks as a summary — the fallback source.

    Used when the duty kept no ``pwm_voltage`` run beside its catalogue entry:
    the coupled record carries the last electromagnetic run's numbers and the
    bridge that produced them, which is everything the tables below need except
    the waveforms.
    """
    em = rec.get("em") if isinstance(rec.get("em"), dict) else {}
    inv = rec.get("inverter") if isinstance(rec.get("inverter"), dict) else {}
    out: Dict[str, Any] = {k: em.get(k) for k in _PWM_EM_KEYS
                           if em.get(k) is not None}
    # The solved phase current is the POINT of a voltage-fed run: the sinusoid
    # was told its current, the inverter was told its volts and found one.  It
    # is a WINDING quantity (the bridge solves the star-equivalent circuit), so
    # it goes in the solved slot and never in the line one — see
    # `_pwm_solved_current` (BL-2, audit v6).
    for src, dst in (("I1_phase_rms_A", "I_phase_rms_solved_A"),
                     ("I_phase_rms_A", "I_phase_rms_solved_A")):
        if _numf(em.get(src)) is not None:
            out[dst] = _numf(em.get(src))
    # …and what it was AIMED at, which is the setpoint it may have missed.
    if _numf(inv.get("target_I_phase_rms_A")) is not None:
        out["I_winding_commanded_A"] = _numf(inv.get("target_I_phase_rms_A"))
    # …under its OWN name, so `_pwm_solved_current` can tell the solved current
    # from the commanded one instead of finding one number in both slots (BL-2).
    for src, dst in (("I_phase_rms_solved_A", "I_phase_rms_solved_A"),
                     ("v_phase_peak_V", "V_phase_peak_V"),
                     ("ripple_pct", "T_ripple_pct"),
                     ("thd_i_pct", "THD_I_pct"),
                     ("thd_ll_pct", "THD_LL_pct")):
        if out.get(dst) is None and _numf(inv.get(src)) is not None:
            out[dst] = _numf(inv.get(src))
    for src, dst in (("coil_temp_c", "coil_temp_C"),
                     ("magnet_temp_c", "magnet_temp_C"),
                     ("efficiency_shaft", "efficiency_shaft"),
                     ("P_bearings_W", "P_bearings_W"),
                     ("P_windage_W", "P_windage_W"),
                     ("P_mech_extra_W", "P_mech_extra_W")):
        if _numf(rec.get(src)) is not None:
            out[dst] = _numf(rec.get(src))
    return out


def _pwm_solved_current(out: Dict[str, Any], sine: Dict[str, Any],
                        pwm: Dict[str, Any]) -> None:
    """THE CURRENT A VOLTAGE-FED RUN FOUND, not the one it was asked for.

    BL-2 (audit v6).  A PWM run is told its volts and solves for a current; the
    summary it is saved with carries the COMMANDED value in ``I_phase_rms_A``
    and ``I_winding_rms_A`` (they are copied from the request) and the solved
    one in ``I_line_rms_A`` / ``I_phase_rms_solved_A``.  The report read the
    commanded key, so 562.1 A appeared twenty-four times in the L155 rated
    document — cover, overview, comparison, operating point, every pair caption
    and the current-density rule — while the run it describes settled at
    544.3 A line / 314.25 A in the winding, 3.16 % short of its setpoint.  Every
    constant divided by it was wrong by the same 3.16 %.

    The commanded value is kept beside it (``I_commanded_line_A``) with the miss
    (``I_point_error_pct``), because the miss is the operating point's own
    accuracy and belongs in the document — not because a reader should ever have
    to choose between the two.
    """
    i_line = _numf(pwm.get("I_line_rms_A"))
    i_wind = _numf(pwm.get("I_phase_rms_solved_A"))
    if i_line is None and i_wind is None:
        return                                  # not a voltage-fed run's summary
    delta = str(out.get("star_delta") or "star").lower().startswith("d")
    root3 = math.sqrt(3.0)
    if i_line is None:
        i_line = i_wind * root3 if delta else i_wind
    if i_wind is None:
        i_wind = i_line / root3 if delta else i_line
    _wc = _numf(pwm.get("I_winding_commanded_A"))
    cmd = next((v for v in (_numf(pwm.get("I_commanded_line_A")),
                            (_wc * root3 if (_wc and delta) else _wc),
                            _numf(pwm.get("I_terminal_rms_A")),
                            _numf(pwm.get("I_phase_rms_A")),
                            _numf((sine or {}).get("I_line_rms_A")),
                            _numf((sine or {}).get("I_phase_rms_A")))
                if v), None)
    out["I_line_rms_A"] = i_line
    out["I_phase_rms_A"] = i_line
    out["I_terminal_rms_A"] = i_line
    out["I_winding_rms_A"] = i_wind
    # …and the current density with it: J is the WINDING current over the phase
    # copper section, and the stored one was the commanded current's.
    a = _numf(out.get("A_phase_mm2"))
    if a:
        out["J_coil_A_per_mm2"] = i_wind / float(a)
    if cmd and abs(cmd - i_line) > 1e-9:
        out["I_commanded_line_A"] = cmd
        out["I_point_error_pct"] = 100.0 * (i_line - cmd) / cmd


def _pwm_end3d(out: Dict[str, Any], sine: Dict[str, Any],
               pwm: Dict[str, Any]) -> None:
    """The 3-D passport of THIS run: k from the geometry, the corrected torque
    and line-voltage peak from the run's own 2-D values (BL-4)."""
    base = (sine or {}).get("end3d")
    base = dict(base) if isinstance(base, dict) else {}
    over = (pwm or {}).get("end3d")
    if isinstance(over, dict):
        base.update({k: v for k, v in over.items() if v is not None})
    k = _numf(base.get("k_flux"))
    if not base or not k:
        return
    t2 = _numf(out.get("T_em_avg_Nm"))
    if t2 is not None:
        base["T_corrected_Nm"] = abs(t2) * float(k)
    v2 = _numf(out.get("V_line_peak_V"))
    if v2 is not None:
        base["V_line_peak_corrected_V"] = v2 * float(k)
    out["end3d"] = base


def pwm_em_overlay(sine: Dict[str, Any],
                   pwm: Dict[str, Any]) -> Dict[str, Any]:
    """The duty's electromagnetic view on the inverter: the PWM run's numbers
    over the sinusoidal summary, with the machine's own constants left alone."""
    out = dict(sine or {})
    for k, v in (pwm or {}).items():
        if v is None or k in _PWM_KEEP_SINE:
            continue
        if any(str(k).startswith(p) for p in _PWM_KEEP_SINE_PREFIX):
            continue
        out[k] = v
    # ROTOR POWER FOLLOWS THE TORQUE IT IS MADE OF.  The record-only path has a
    # torque and no power, and leaving the sinusoid's watts under a PWM torque
    # would put two different machines in one efficiency.
    if pwm.get("T_em_avg_Nm") is not None and pwm.get("P_mech_W") is None:
        _t, _n = _numf(out.get("T_em_avg_Nm")), _numf(out.get("rpm"))
        if _t is not None and _n:
            out["P_mech_W"] = abs(_t) * 2.0 * math.pi * float(_n) / 60.0
    _pwm_solved_current(out, sine or {}, pwm or {})
    _pwm_end3d(out, sine or {}, pwm or {})
    return out


def apply_pwm_view(col: Dict[str, Any],
                   cfg_doc: Optional[Dict[str, Any]] = None
                   ) -> Dict[str, Any]:
    """Make one duty column say what supply it runs on, and read that supply.

    A sinusoidal duty is returned untouched but for ``drive``.  A PWM duty keeps
    its sinusoidal summary under ``em_sine`` — section 5 is the comparison of
    the two — and its ``em`` becomes the inverter's answer.
    """
    col["drive"] = duty_drive(col)
    if col["drive"] != "pwm":
        return col
    rec = (col.get("res") or {}).get("coupled") or {}
    col["inverter"] = duty_inverter(col)
    side = pwm_run_sidecar(cfg_doc or {}, col.get("duty"))
    summ = side.get("summary") if isinstance(side.get("summary"), dict) else None
    if summ:
        # NO STORE KEY IN A CLIENT DOCUMENT (CS-10, audit v7): the cover and
        # section 4 printed "(runs['pwm_voltage'])" after this sentence, which
        # names a field of the catalogue file and tells a reader nothing.
        col["pwm_summary_source"] = "the PWM run saved for this duty"
    else:
        summ = _pwm_summary_from_record(rec)
        col["pwm_summary_source"] = "the coupled run's own electromagnetic block"
    col["em_sine"] = dict(col.get("em") or {})
    col["pwm_summary"] = dict(summ)
    col["em"] = pwm_em_overlay(col.get("em") or {}, summ)
    # Which stored run the waveform charts may be drawn from.  The sidecar's
    # pointer is dropped by `family._store_run` when the file belongs to another
    # run, so its presence here really does mean "this run's waveforms".
    col["wf_drive"] = PWM_RUN_DRIVE if side.get("payload_file") else None
    return col


def _khz(f: Any) -> Optional[str]:
    """A carrier in kHz, without a decimal it does not need: 24, 23.3."""
    v = _numf(f)
    if v is None or v <= 0:
        return None
    k = v / 1000.0
    return _fmt(k, 0 if abs(k - round(k)) < 0.05 else 1)


#: What the headline, the overview and the comparison table print for a duty
#: fed by a clean sinusoid.
SUPPLY_SINE = "sinusoid"


def supply_words(col: Dict[str, Any]) -> str:
    """``"PWM 24 kHz, DC link 750 V, m 0.88"`` — one duty's supply, in one cell.

    Every part is read from the stored inverter block; a bridge that recorded
    only its carrier says only its carrier.
    """
    if duty_drive(col) != "pwm":
        return SUPPLY_SINE
    inv = duty_inverter(col)
    f = _khz(inv.get("f_carrier_hz"))
    bits = ["PWM %s kHz" % f if f else "PWM"]
    v = _numf(inv.get("v_dc_V"))
    if v is not None:
        # …TO THE DECIMAL THE REST OF THE DOCUMENT PRINTS (CS-2, audit v7):
        # the cover and the overview said 750 V where section 3 said 750.4 V.
        bits.append("DC link %s V" % _fmt(v, 1))
    m = _numf(inv.get("m"))
    if m is not None:
        bits.append("m %s" % _fmt(m, 2))
    return ", ".join(bits)


def any_pwm(cols: Optional[List[Dict[str, Any]]]) -> bool:
    """True when at least one duty of this configuration runs on the inverter."""
    return any(duty_drive(c) == "pwm" for c in (cols or []))


def supply_short(col: Dict[str, Any]) -> str:
    """``"PWM 24 kHz"`` / ``"sinusoid"`` — the supply in a CAPTION (MJ-6).

    The full :func:`supply_words` carries the DC link and the modulation index
    as well, which is right for a table cell and three clauses too long for a
    figure caption that already names two duties and two operating points.
    """
    if duty_drive(col) != "pwm":
        return SUPPLY_SINE
    f = _khz(duty_inverter(col).get("f_carrier_hz"))
    return ("PWM %s kHz" % f) if f else "PWM"


#: A voltage-fed run inside this band of its current setpoint IS on its point,
#: and the document says nothing about it.  Well under the inverter loop's own
#: 1 % tolerance: this is the threshold for printing, not for accepting.
PWM_POINT_TOL_PCT = 0.05


def duty_point_error(col: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """By how much a voltage-fed duty's SOLVED current missed its setpoint.

    ``None`` on a current-fed duty and on a voltage-fed one that landed on its
    point.  The L155 rated duty settled at 544.3 A line against a commanded
    562.1 A — 3.16 % low — and the document said so nowhere (BL-2, audit v6).
    """
    if duty_drive(col) != "pwm":
        return None
    em = col.get("em") if isinstance(col.get("em"), dict) else {}
    inv = duty_inverter(col)
    pct = _numf(em.get("I_point_error_pct"))
    if pct is None:
        pct = _numf(inv.get("point_error_pct"))
    if pct is None or abs(pct) < PWM_POINT_TOL_PCT:
        return None
    return {
        "pct": pct,
        "solved_A": _numf(em.get("I_line_rms_A")),
        "commanded_A": _numf(em.get("I_commanded_line_A")),
        "solved_winding_A": _numf(em.get("I_winding_rms_A")),
        "on_point": bool(inv.get("on_point")),
    }


#: WHY a voltage-fed run misses its current setpoint — the one clause that goes
#: with every printing of the miss.
#:
#: IT DESCRIBES THE REGULATOR, not a fixed voltage (2026-09-16).  The clause
#: used to say the first pass's fundamental "was held for the passes after it",
#: which is not what the loop does and not what these records show: the L180 gen
#: 'rated' duty was re-aimed 729.6 → 743.2 → 747.18 V and the 'peak' duty
#: 789.7 → 825.0 → 863.7 V.  The loop stops when the current meets the setpoint,
#: when the passes run out, or when the bridge reaches its modulation ceiling.
POINT_ERROR_WHY = ("a voltage-fed run is given volts, not amps: the loop "
                   "re-aims the fundamental after every pass — a damped step, "
                   "then a secant on the solved current — until the current "
                   "meets the setpoint, the passes run out, or the bridge "
                   "reaches its modulation ceiling, and this is where it "
                   "stopped")


def point_error_cell(col: Dict[str, Any]) -> str:
    """``"−3.16 % (solved 544.3 A against 562.1 A)"`` — the comparison cell."""
    pe = duty_point_error(col)
    if pe is None:
        return ""
    return "%s %% (solved %s against %s)" % (
        _signed(pe["pct"], 2), _fmt(pe["solved_A"], 1, "A"),
        _fmt(pe["commanded_A"], 1, "A"))


def point_error_text(cols: Optional[List[Dict[str, Any]]]) -> str:
    """The sentence the duties overview and section 3 carry when a duty of this
    configuration did not land on its setpoint.  ``""`` when they all did."""
    bits = []
    for c in (cols or []):
        cell = point_error_cell(c)
        if cell:
            bits.append("'%s' %s" % (c.get("duty") or "—", cell))
    if not bits:
        return ""
    return ("Operating point vs the duty's setpoint — %s; %s."
            % ("; ".join(bits), POINT_ERROR_WHY))


#: The clause every waveform caption of a PWM duty carries when the inverter
#: run was saved without its transient — the charts are then the sinusoid's and
#: the reader is told so rather than shown a carrier-free trace under a carrier.
WF_SINE_ON_PWM = "(sinusoidal run — PWM waveforms not stored)"

#: What a row carries on a PWM document when the quantity in it was measured on
#: the sinusoid and cannot be measured on the bridge.
SINE_ROW_TAIL = " (sinusoidal run)"


# ---------------------------------------------------------------------------
# WHICH DUTY IS A CYCLE  (2026-09-14)
# ---------------------------------------------------------------------------
# A robot joint's peak point is not a steady state and never reaches one: it is
# an S2 pull or an S3 cycle, and the temperature that matters is the peak of the
# settled cycle, not the steady state the 2-D map would run to.  A duty that has
# been through the duty-cycle solver carries that answer as its own record, and
# from here on the document reads it — in the overview, in its own section, and
# in the warnings, which are judged on the cycle's peak.


def duty_cycle_record(col: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """This duty's stored duty-cycle answer, or ``None``."""
    rec = (col.get("res") or {}).get("duty_cycle")
    return rec if isinstance(rec, dict) and rec.get("cycle") else None


def duty_cycle_kind(rec: Optional[Dict[str, Any]]) -> str:
    """``"S1"`` / ``"S2"`` / ``"S3"`` — what the cycle was asked for."""
    k = str(((rec or {}).get("spec") or {}).get("kind") or "").strip().upper()
    return k or "S3"


def duty_class_mark(col: Dict[str, Any]) -> str:
    """``" · S3 ED 25 %"`` for the overview's Mode cell; ``""`` with no cycle.

    Nothing is marked on a duty without a record: a point nobody ran a cycle for
    is not thereby continuous, and calling it S1 would be this report inventing
    a duty class for the reader.
    """
    rec = duty_cycle_record(col)
    if rec is None:
        return ""
    spec = rec.get("spec") or {}
    bits = [duty_cycle_kind(rec)]
    ed = _numf(spec.get("ed_pct"))
    if ed is not None:
        bits.append("ED %s %%" % _fmt(ed, 0))
    cyc = _numf(spec.get("cycle_s"))
    if cyc is not None:
        bits.append(_fmt(cyc, 0, "s"))
    return " · " + " ".join(bits)


def _cmp_table(header: str, cols: List[Dict[str, Any]],
               rows: List[List[Any]], *, st=None, size: float = 7.2):
    """A comparison table: the quantity on the left, one column per duty.

    The duty names in the header row go in as Paragraphs, not strings — see the
    'hcell' style: a Cyrillic duty name in a plain cell comes out as question
    marks, and the column heading is the one cell in the table that must be
    readable before anything else.
    """
    n = max(1, len(cols))
    lw = max(96.0, min(190.0, CONTENT_W - n * 58.0))
    cw = (CONTENT_W - lw) / n
    head = [header] + [(_para(c["duty"], st["hcell"]) if st else c["duty"])
                       for c in cols]
    # A verdict or a list of temperatures is longer than a duty column is
    # wide: wrap it instead of clipping it (the rows used to be cut at 44
    # characters — "…runs below its fir" on the page, reviewer 2026-09-13).
    def _cell(v):
        if st and isinstance(v, str) and len(v) > 40:
            return _para(v, st["cell"])
        return v
    data = [head] + [[r[0]] + [_cell(v) for v in r[1:]] for r in rows]
    return _table(data, [lw] + [cw] * n, header=True, size=size)


def _pair(a: Any, b: Any, d: int = 1, sep: str = " / ") -> str:
    """``"143 / 139"`` — two numbers of the same kind in one cell.

    Used wherever a table carried a maximum and an average as two rows with the
    same label (user 2026-09-11).  A missing half is an em dash, never a blank,
    so the cell still reads as a pair.
    """
    fa, fb = _numf(a), _numf(b)
    if fa is None and fb is None:
        return "—"
    return "%s%s%s" % (_fmt(fa, d) if fa is not None else "—", sep,
                       _fmt(fb, d) if fb is not None else "—")


def _col_vals(cols, fn) -> List[Any]:
    out = []
    for c in cols:
        try:
            out.append(fn(c))
        except Exception:                                   # noqa: BLE001
            out.append("—")
    return out


def _drop_empty(rows: List[List[Any]]) -> List[List[Any]]:
    """Comparison rows with nothing in them at all, taken out.

    User 2026-09-11: *"убери это Warning, они пустые"* — the coupled table's
    "Warning" row printed an em-dash under every duty, which is what a healthy
    run looks like, so the row was a line of nothing on every report of a
    machine that converged.  A row goes only when EVERY cell is the "no value"
    dash: a cell reading "not solved" is an answer and keeps its row, and a row
    with a value under one duty keeps it for all of them.
    """
    return [r for r in rows
            if any(str(v).strip() not in ("—", "", "None") for v in r[1:])]


def _local_stamp(s: Any, missing: str = "unstamped") -> str:
    """``2026-09-13 15:20:02`` — a stored timestamp in the machine's LOCAL time.

    The thermal and mechanical stores stamp their entries in UTC with an offset
    (``…T13:20:02+00:00``) while the electromagnetic run stamps local wall time;
    printing the first 19 characters of both put two clocks two hours apart on
    one page (reviewer 2026-09-13: a 15:20 coupled run reported as "solved
    13:20").  An offset-aware stamp is converted; a naive one is trusted as is.
    """
    txt = str(s or "").strip()
    if not txt:
        return missing
    try:
        import datetime as _dt
        t = _dt.datetime.fromisoformat(txt.replace("Z", "+00:00"))
        if t.tzinfo is not None:
            t = t.astimezone()
        return t.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:                                       # noqa: BLE001
        return txt[:19].replace("T", " ")


def _stamp(entry: Any) -> str:
    if not isinstance(entry, dict):
        return NOT_SOLVED
    return _local_stamp(entry.get("computed_at"))


def _cooling_kind(t: Optional[Dict[str, Any]]) -> Tuple[Optional[str], str]:
    """'air' / 'liquid' / 'robotics' — how the heat leaves, off the solved
    boundary report.

    A thermal solve set to a fixed film coefficient ('manual') says nothing
    about the cooling class, and a machine with no thermal answer says nothing
    at all; both read as air, which is the tighter current-density band, and the
    warning that follows carries the assumption in its own note.
    """
    if isinstance(t, dict):
        # THE ROBOTICS MODE FIRST (2026-09-14): its housing reports `robotics`
        # and its bore `still`, and neither is a forced-air boundary.  Read in
        # the old order the bore's `still` fell through to the generic branch
        # and the machine came back "air-cooled" — the right NUMBER (both bands
        # are 10 A/mm2) under a sentence that was not true of it.
        for where in ("outer", "inner"):
            mode = str(((t.get("cooling") or {}).get(where) or {})
                       .get("mode") or "").lower()
            if mode.startswith("robot") or mode == "still":
                return "robotics", ("the solved boundary is still air — natural "
                                    "convection + radiation on the housing, and "
                                    "conduction into the mount")
        for where in ("outer", "inner"):
            mode = str(((t.get("cooling") or {}).get(where) or {})
                       .get("mode") or "").lower()
            if mode.startswith("liq"):
                return "liquid", "the solved housing/bore boundary is a liquid jacket"
            if mode.startswith("air"):
                return "air", "the solved housing/bore boundary is forced air"
    return "air", ("ASSUMED air-cooled: no thermal solve on this duty says how "
                   "the heat leaves, and air is the tighter band")


def _warning_context(col: Dict[str, Any], *, mats: Dict[str, Any],
                     batt: Dict[str, Any], brg: Optional[Dict[str, Any]],
                     max_speed_rpm: Optional[float],
                     mag_lim: Optional[float], mag_note: str,
                     ins_lim: float, ins_note: str,
                     cold_k: float, cold_note: str,
                     slots: Optional[int] = None) -> Dict[str, Any]:
    """The flat per-duty view :func:`duty_warnings` judges.

    Every value is taken from the duty's OWN stored answers; where a quantity
    exists in more than one store the more specific one wins (a solved
    temperature over a card's quoted temperature, a coupled loop's settled
    magnet temperature over the panel's input).
    """
    em = col.get("em") or {}
    rr = col.get("result") or {}
    res = col.get("res") or {}
    t = res.get("thermal") if isinstance(res.get("thermal"), dict) else None
    ms = res.get("rotor_stress") if isinstance(res.get("rotor_stress"), dict) else None
    cp = res.get("coupled") if isinstance(res.get("coupled"), dict) else None
    comps = (t or {}).get("components") or {}

    def _comp(key: str, which: str = "max") -> Optional[float]:
        c = comps.get(key)
        return _numf(c.get(which)) if isinstance(c, dict) else None

    dc = duty_cycle_record(col)
    ctx: Dict[str, Any] = {"duty": col["duty"], "drive": duty_drive(col)}

    # ── temperatures: solved first, settled second, quoted last ─────────────
    ctx["magnet_temp_c"] = (_comp("magnet")
                            or _numf((cp or {}).get("magnet_temp_max_c"))
                            or _numf((cp or {}).get("magnet_temp_c"))
                            or _numf(_setting_of(col, "sim.magnetTempC"))
                            or _magnet_card_temp(mats.get("magnet")))
    ctx["magnet_limit_c"], ctx["magnet_limit_note"] = mag_lim, mag_note
    ctx["winding_temp_c"] = (_comp("winding")
                             or _numf((cp or {}).get("coil_temp_c"))
                             or _numf(em.get("coil_temp_C")))
    ctx["winding_limit_c"], ctx["winding_limit_note"] = ins_lim, ins_note
    ctx["hot_spot_c"] = _numf((t or {}).get("T_max"))
    # ── …AND A CYCLE BEATS EVERY STEADY STATE (2026-09-14) ──────────────────
    # An S2 or S3 duty never reaches the steady state the 2-D map ran to: it is
    # switched off first, and it is switched back on before it has cooled.  The
    # temperature its insulation and its magnets actually see is the PEAK of the
    # settled cycle, and a report that judged this duty on a steady state would
    # be judging a machine that does not exist — too hot on a short pull, too
    # cold on a long one.  The basis is printed in the limit's own note.
    if dc is not None:
        cyc = dc.get("cycle") or {}
        peak = cyc.get("peak_c") if isinstance(cyc.get("peak_c"), dict) else {}
        _w = (_numf(cyc.get("winding_hot_peak_c"))
              if cyc.get("winding_hot_peak_c") is not None
              else _numf(peak.get("winding")))
        _m = _numf(peak.get("magnet"))
        ctx["duty_cycle_kind"] = duty_cycle_kind(dc)
        ctx["temp_basis"] = DUTY_CYCLE_TEMP_BASIS % duty_cycle_kind(dc)
        if _w is not None:
            ctx["winding_temp_c"] = _w
            ctx["hot_spot_c"] = _w
            ctx["winding_limit_note"] = ((ins_note + "; " if ins_note else "")
                                         + ctx["temp_basis"])
        if _m is not None:
            ctx["magnet_temp_c"] = _m
            ctx["magnet_limit_note"] = ((mag_note + "; " if mag_note else "")
                                        + ctx["temp_basis"])
        lim = dc.get("limits") or {}
        _dsp = dc.get("spec") or {}
        # A RATIO THE TOOL FOUND IS NOT A RATIO SOMEBODY ASKED FOR (2026-09-15).
        # `ed_found` says which this record is; with it true nothing was
        # requested, so there is nothing for the ED rule to fail against and
        # the allowable travels as a finding instead (see `duty_warnings`).
        _req = _numf(lim.get("ed_requested_pct"))
        if (_req is None and lim.get("ed_found") is not True
                and _dsp.get("ed_given") is not False):
            _req = _numf(_dsp.get("ed_pct"))
        if lim.get("ed_found") is True:
            _req = None
        ctx["ed_requested_pct"] = _req
        ctx["ed_allowable_pct"] = _numf(lim.get("ed_allowable_pct"))
        ctx["ed_found"] = lim.get("ed_found")
        ctx["ed_cycle_s"] = _numf(lim.get("ed_cycle_s"))
        ctx["ed_limiting_part"] = (lim.get("ed_limiting_part")
                                   or lim.get("limiting_part"))
        # THE CYCLE WAS JUDGED AGAINST ITS OWN LIMIT, which is the one the
        # solver was told and need not be this report's insulation card.  When
        # the two differ the allowable ED below belongs to the cycle's number,
        # and the row says so rather than letting the reader pair 21.7 % with a
        # limit it was not computed at.
        ctx["duty_cycle_winding_limit_c"] = _numf(lim.get("winding_limit_c"))
        ctx["s2_time_to_limit_s"] = _numf(lim.get("s2_time_to_limit_s"))
        ctx["s2_limiting_part"] = lim.get("s2_limiting_part")
        ctx["s2_from_rated_s"] = _numf(lim.get("s2_from_rated_s"))
        ctx["s2_from_cycle_mean_s"] = _numf(lim.get("s2_from_cycle_mean_s"))
        ctx["duty_cycle_regime"] = duty_cycle_regime_text(dc)

    # ── electromagnetic ─────────────────────────────────────────────────────
    ctx["br_kept_pct"] = _numf(_g(em, "demag.br_kept_vol_pct"))
    # …and the WORST single element beside the volume average (reviewer
    # 2026-09-14, B6): on the L155 peak duty one pole corner keeps 18.4 % of
    # its Br — a corner that is gone — and the document stated it three times
    # in passing and never as a finding.
    ctx["br_worst_pct"] = _numf(_g(em, "demag.br_worst_pct"))
    # ── TWO RIPPLES, ONE LIMIT (2026-09-14) ─────────────────────────────────
    # The 5 % gate is about the LOW-ORDER ripple — cogging, slotting, the pole
    # arc — which is what skew and a re-cut arc can change and what a joint or a
    # spindle feels.  A two-level bridge adds ripple at the carrier, tens of per
    # cent of it on this machine, and the rotor inertia filters all of it: on
    # the L155 peak duty a 44 % carrier figure would have failed a design whose
    # low-order ripple is under 1 %.  So the rule reads the SINUSOIDAL run, and
    # the carrier's own figure gets an informational row with no limit.
    _sine_em = col.get("em_sine") if isinstance(col.get("em_sine"), dict) else em
    _sine_cp = ((cp or {}).get("reference_sine")
                if isinstance((cp or {}).get("reference_sine"), dict) else None)
    ctx["ripple_pct"] = _numf(
        ((_sine_cp or {}).get("em") or {}).get("T_ripple_pct")
        if _sine_cp else None) or _numf(
        _sine_em.get("T_ripple_pct") or rr.get("ripple_pct"))
    if ctx["drive"] == "pwm":
        _inv = duty_inverter(col)
        ctx["carrier_ripple_pct"] = _numf(em.get("T_ripple_pct")
                                          or _inv.get("ripple_pct"))
        # …and what is LEFT of it once the carrier is filtered out (BT-6,
        # 2026-09-16).  The 5 % gate is judged on the sinusoidal run, which is
        # right — it is the geometry's ripple — but the PWM run measures its
        # own carrier-filtered figure and it is not the sinusoid's: 6.1 % on
        # the L180 gen peak duty against the 1.6 % the gate saw, past the gate,
        # and it appeared nowhere in the document.
        ctx["carrier_ripple_filt_pct"] = _numf(em.get("T_ripple_filt_pct"))
        ctx["carrier_thd_i_pct"] = _numf(em.get("THD_I_pct")
                                         or _inv.get("thd_i_pct"))
        ctx["carrier_hz"] = _numf(_inv.get("f_carrier_hz"))
        if _inv.get("ripple_quotable") is not None:
            ctx["carrier_ripple_quotable"] = bool(_inv.get("ripple_quotable"))
        ctx["carrier_dc_residual_a"] = _numf(_inv.get("dc_residual_A"))
        ctx["carrier_dc_tol_a"] = _numf(_inv.get("dc_tol_A"))
    # The LINE (terminal) voltage's THD — what the inverter and the insulation
    # see, and what the U_AB spectrum chart measures.  `THD_pct` is the
    # winding's: in delta it carries the zero-sequence triplen EMF (6.3 %)
    # that never reaches the terminals (1.2 %) — reviewer 2026-09-13.
    #
    # …AND IT SPLITS EXACTLY AS THE RIPPLE DOES (reviewer 2026-09-15).  The
    # 10 % gate is about the MACHINE's distortion; on a PWM duty `THD_LL_pct`
    # is the BRIDGE's pulse train — 44.7 % on the L180 rated duty, which failed
    # a machine whose own back-EMF distorts by 1.17 %.  So the rule reads the
    # sinusoidal run, and the bridge's figure gets an informational row, with
    # the current THD beside it because that is what the winding lets through.
    ctx["thd_pct"] = _numf(em.get("THD_LL_pct") or em.get("THD_pct"))
    if ctx["drive"] == "pwm":
        # …AND THE BRIDGE'S ROW IS THE BRIDGE'S NUMBER (MJ-1, audit v7).  It is
        # the pulse train's own THD, integrated from the edges by
        # `bridge_pulse_thd_pct` — the same two functions the U_AB spectrum
        # panel uses — and never `THD_LL_pct`, which is the winding voltage the
        # field gives back, carrier-averaged.  Absent when the train can be
        # neither read nor regenerated, and then the row is not printed at all
        # rather than printed with somebody else's number.
        ctx["bridge_thd_pct"] = _numf(col.get("bridge_thd_pct"))
        ctx["winding_thd_pct"] = ctx["thd_pct"]
        ctx["sine_thd_pct"] = sine_line_thd(col)
        ctx["thd_pct"] = ctx["sine_thd_pct"]
    ctx["j_coil_a_mm2"] = _numf(em.get("J_coil_A_per_mm2")
                                or rr.get("j_coil_a_mm2"))
    kind, kind_note = _cooling_kind(t)
    ctx["cooling_kind"], ctx["cooling_note"] = kind, kind_note
    # OPEN or CLOSED, for the air band.  The thermal solve says so outright:
    # `cooling.frame.mode` is 'housed' on a machine with a housing and names the
    # open model otherwise.  Unknown reads as closed — the tighter limit.
    _fr = str((((t or {}).get("cooling") or {}).get("frame") or {})
              .get("mode") or "").strip().lower()
    ctx["frame_open"] = bool(_fr) and not _fr.startswith("hous")
    # The two insulation cards travel with the context, because under a JACKET
    # they are what sets the current-density band (20 organic / 25 ceramic,
    # user 2026-09-14).  The cards are the machine's, not the duty's, so any
    # column answers for the limit-rules table too.
    ctx["insulation_mats"] = {k: (mats or {}).get(k)
                              for k in ("slot_insulation", "wire_insulation")}
    ctx["insulation_system"] = insulation_system(ctx["insulation_mats"])[0]

    # ── the pack, and the speed an uncontrolled machine runs away at ────────
    # THE 3-D END-EFFECT FACTOR, read before the first voltage that needs it.
    # Every flux-proportional number in this document carries k_3d, and the
    # waveform-peak rule did not: the L155 peak duty was failed on its 2-D
    # 563.4 V while the detail table cleared it at 539.5 V and the modulation
    # row beside it was already ×k (reviewer 2026-09-14, A2).  One k, both
    # voltage rules.
    _k3 = _numf(_g(em, "end3d.k_flux"))
    _k3 = _k3 if (_k3 and 0.5 < _k3 <= 1.0) else None
    v_peak = _numf(em.get("V_line_peak_V") or rr.get("v_ll_peak_v"))
    v_peak_2d = v_peak
    if v_peak is not None and _k3:
        v_peak = v_peak * float(_k3)
    v_min = _numf((batt or {}).get("v_min"))
    rpm = _numf(em.get("rpm") or col["d"].get("rpm"))
    ctx["v_line_peak_v"], ctx["v_pack_min_v"] = v_peak, v_min
    ctx["v_line_peak_2d_v"], ctx["k_3d"] = v_peak_2d, _k3
    # The WHOLE pack, not just its floor: a PWM duty is solved on a chosen DC
    # link and the question about it is where in the pack's range that link
    # sits (reviewer 2026-09-15).
    ctx["v_pack_nom_v"] = _numf((batt or {}).get("v_nom"))
    ctx["v_pack_max_v"] = _numf((batt or {}).get("v_max"))
    # THE RUNAWAY SPEED IS A PROPERTY OF THE MACHINE, not of the duty
    # (reviewer 2026-09-13, item 8: 18 537 vs 18 705 rpm for one machine on
    # the same cold magnets).  An uncontrolled machine carries no load
    # current, so it is the NO-LOAD line EMF that meets the pack floor: from
    # the run's no-load KV (rpm per peak line volt) with its magnets walked
    # from the run's own temperature to 20 °C, which cancels the run-to-run
    # difference the loaded, field-weakened terminal voltage carried.
    # The run's no-load KV is a ψ_PM probe at the magnet CARD's temperature
    # (routes/simulation: `noload_psi_pm` takes no run temperature), so the
    # cold factor is the card's own Br(card)/Br(20 °C) = 1/cold_k — the same
    # number for every duty of the machine.
    kv_nl = _numf(em.get("KV_noload_rpm_per_V_line"))
    t_card = _magnet_card_temp(mats.get("magnet")) or 150.0
    # The probe is 2-D; the EMF a real stack makes is the 2-D one × k_3d, so
    # the speed per volt is ÷ k_3d — the same correction every other
    # flux-proportional number of this document carries (2026-09-13).
    if kv_nl and _k3 and 0.5 < _k3 <= 1.0:
        kv_nl = float(kv_nl) / float(_k3)
    if kv_nl and v_min and cold_k and cold_k > 1.0:
        ctx["runaway_rpm"] = float(kv_nl) * float(v_min) / float(cold_k)
        ctx["runaway_note"] = (cold_note + ", from the no-load KV at the card's "
                               "%s °C%s scaled to 20 °C"
                               % (_fmt(t_card, 0),
                                  (" ÷ k_3d %.4f" % _k3)
                                  if _k3 and 0.5 < _k3 <= 1.0 else ""))
    elif v_peak and v_min and rpm and v_peak > 0:
        # V_line_peak is proportional to speed; on COLD magnets it is higher by
        # the Br ratio, so the speed at which it reaches the pack floor is lower.
        ctx["runaway_rpm"] = float(rpm) * float(v_min) / (float(v_peak) * cold_k)
        ctx["runaway_note"] = cold_note + " (from this duty's loaded line voltage)"
    ctx["max_speed_rpm"] = max_speed_rpm
    # THE MODULATION GATE (reviewer 2026-09-14 / PWM study §1.7).  The peak
    # above is the waveform's; the quantity a two-level inverter is limited on
    # is the FUNDAMENTAL line voltage, `V1_LL_V` of the stored summary — the
    # exact fundamental amplitude of the terminal waveform, triplen-free in
    # delta.  It is flux-proportional, so it carries k_3d like every other
    # voltage on these pages, and it is compared against 0.9959 x the pack
    # floor.  Measured on the L155 peak duty: waveform peak passes, m = 1.15+.
    v1_ll = _numf(em.get("V1_LL_V"))
    if v1_ll is not None and _k3 and 0.5 < _k3 <= 1.0:
        v1_ll = v1_ll * float(_k3)
    ctx["v_line_fund_v"] = v1_ll
    if v_min:
        ctx["v_mod_ceiling_v"] = MOD_CEILING_OF_VDC * float(v_min)
    ctx["mod_index"] = modulation_index(v1_ll, v_min)
    # …AND ON A PWM DUTY THE BRIDGE'S OWN NUMBERS WIN (2026-09-14).  The gate
    # above is a prediction made from a current-driven run's fundamental; an
    # inverter run MEASURES both halves of it — the modulation index it ran at
    # and the link it ran on — and a measured m is not to be re-derived from a
    # pack floor the run may not have been given.
    if ctx["drive"] == "pwm":
        _inv = duty_inverter(col)
        _m = _numf(_inv.get("m"))
        _vdc = _numf(_inv.get("v_dc_V"))
        if _vdc is not None:
            ctx["v_dc_run_v"] = _vdc
            # THE CEILING IS THE RUN'S LINK, NOT THE PACK FLOOR (reviewer
            # 2026-09-15).  A PWM duty was solved on one chosen link and the
            # only ceiling it can be judged against is that link's; comparing
            # it with the fully-discharged pack failed duties the bridge held
            # comfortably.
            ctx["v_mod_ceiling_v"] = MOD_CEILING_OF_VDC * float(_vdc)
        if _m is not None:
            ctx["mod_index"] = _m
            ctx["mod_index_note"] = (
                "the modulation index the inverter run itself ran at%s"
                % ((", on a %s V link" % _fmt(_vdc, 1))
                   if _vdc is not None else ""))
            # …and the fundamental that goes with it, in the SAME convention.
            # The stored `V1_LL_V` of a voltage-fed run is the star-equivalent
            # circuit's, which does not divide into the link the bridge really
            # ran on; the measured m does, so the rule's two halves are both
            # the bridge's own: V1_LL,peak = m · V_dc · sqrt(3)/2.
            if _vdc is not None:
                ctx["v_line_fund_v"] = (float(_m) * float(_vdc)
                                        * math.sqrt(3.0) / 2.0)
        # …AND THE CEILING THE RUN ACTUALLY HAD (2026-09-16).  m is the index of
        # the modulator's REFERENCE and the clamp is on the fundamental it
        # APPLIES, which is smaller by the sampled-reference gain: the L180 gen
        # 'rated' duty sat exactly on its 747.18 V ceiling and this row, rebuilt
        # from m alone, read 787.71 V against 795.95 V — "1 % of margin left",
        # in a document whose coupled block says the point is inverter-limited
        # and whose section 4 prints the fundamental as 747.2 V.  Both halves
        # now come off the record, in the LINE convention the row's name uses
        # (in delta the branch IS the line; in star it is V_LL/√3).
        _cap = _numf(_inv.get("v_phase_peak_max_V"))
        _v1r = _numf(_inv.get("v_phase_peak_V"))
        _sd_inv = str(_inv.get("star_delta") or "").strip().lower()
        if _cap and _v1r and _sd_inv:
            _ll = 1.0 if _sd_inv.startswith("d") else math.sqrt(3.0)
            ctx["v_line_fund_v"] = _v1r * _ll
            ctx["v_mod_ceiling_v"] = _cap * _ll
            ctx["mod_ceiling_from_run"] = True
            if (bool(_inv.get("at_modulation_ceiling"))
                    or _v1r >= _cap - 1e-6):
                ctx["mod_at_ceiling"] = True
                ctx["mod_ceiling_v1_v"] = _cap * _ll
                ctx["mod_carriers"] = _numf(_inv.get("carriers_per_period"))

    # ── mechanical ──────────────────────────────────────────────────────────
    if ms:
        parts = ms.get("parts") or {}
        ctx["sf_min"] = _numf(ms.get("sf_min"))
        ctx["sf_min_part"] = ms.get("sf_min_part")
        sl = parts.get("sleeve") or {}
        ctx["sleeve_hoop_mpa"] = _numf(sl.get("hoop_max_mpa"))
        ctx["sleeve_strength_mpa"] = _numf(sl.get("strength_mpa"))
        ctx["overspeed_factor"] = _numf(ms.get("overspeed_factor"))
        # …and WHICH case the stored answer is (BT-8): a factor above 1
        # says an overspeed case exists, not that this document carries it.
        ctx["overspeed_case"] = ms.get("case")
        mg = parts.get("magnet") or {}
        # THE CRITERION THE SAFETY FACTOR IS ON (reviewer 2026-09-14, B4/B5).
        # The table divides the strength by `governing_stress_mpa` — the
        # AVERAGED peak of the part's own criterion, which for a magnet is the
        # principal stress — and the warning used to check the p99.5 principal
        # instead, so the same magnet read "62.9 % of margin left" in section 7
        # and "SF 2.05, at the acceptance line" in section 6.  One stress.
        ctx["magnet_stress_mpa"] = (_numf(mg.get("governing_stress_mpa"))
                                    or _numf(mg.get("principal_max_p995_mpa")))
        ctx["magnet_stress_p995_mpa"] = _numf(mg.get("principal_max_p995_mpa"))
        ctx["magnet_tensile_mpa"] = _numf(mg.get("strength_mpa"))
        # …and every part's own safety factor, so the second-tightest number in
        # the document raises a row of its own: the magnet at SF 2.05 against
        # an acceptance level of 2 fired nothing at all (B5).
        ctx["part_safety_factors"] = {
            str(k): _numf((p or {}).get("safety_factor"))
            for k, p in sorted(parts.items())
            if isinstance(p, dict) and _numf(p.get("safety_factor")) is not None}
        # THE RETENTION JOINT ONLY — see `RETENTION_PAIRS`.  Taking the worst
        # of every separation pair reported a band that is pressed solidly onto
        # its magnets as lifting off, because the same band does not touch the
        # rotor iron between the poles.
        _r_lbl, _r_if = retention_interface(ms)
        if _r_if is not None:
            _f = _numf(_r_if.get("open_fraction"))
            if _f is not None:
                ctx["open_fraction_pct"] = 100.0 * _f
            ctx["open_interface"] = _r_lbl
            ctx["lift_off"] = bool(_r_if.get("lift_off"))
        else:
            ctx["lift_off"] = False
        ctx["torque_path_verdict"] = (ms.get("torque_path") or {}).get("verdict")
        # A BONDED / TIED joint that goes into TENSION (user 2026-09-14).  The
        # interfaces table said "shaft/rotor — tie goes into tension at 14,200
        # rpm" and section 7 fired nothing, because the only contact rule is the
        # retention joint's open fraction.  A tie can carry tension in the model
        # and a press fit cannot, so this is the speed at which the REAL joint
        # would have let go.  SEPARATION pairs are excluded by construction: the
        # sleeve/rotor and magnet/rotor gaps open by design (see
        # `RETENTION_PAIRS`) and must never raise this.
        _ties = []
        _lo = ms.get("lift_off_rpm") or {}
        for _lbl, _if in sorted((ms.get("interfaces") or {}).items()):
            if not isinstance(_if, dict):
                continue
            if str(_if.get("type") or "").startswith("separ"):
                continue
            _in_tension = bool(_if.get("lift_off"))
            _at = _numf(_lo.get(_lbl))
            if _in_tension or _at is not None:
                _ties.append({"interface": str(_lbl), "rpm": _at,
                              "in_tension": _in_tension,
                              "duty_rpm": rpm,
                              "type": str(_if.get("type") or "bonded")})
        if _ties:
            ctx["tie_tension"] = _ties

    # ── the ring modes against the excitation lines ─────────────────────────
    # RE-JUDGED HERE, against THIS duty's own carrier (reviewer 2026-09-14, A1).
    # The stored `tightest` carries the carrier the server happened to hold
    # when the modal step ran — for the L155's rated duty a Ø85 machine's
    # 48 kHz — and the frequencies are the rotor's, so the two duties of one
    # machine came out with two different verdicts about one rotor.  The
    # frequencies are read back from whatever the record kept; the lines are
    # built from `mesh['sim.fSwitch']`, the duty's speed and the slot count.
    _fs = _numf(_setting_of(col, "sim.fSwitch"))
    _modes_rec = res.get("modes") if isinstance(res.get("modes"), dict) else None
    _tight = tightest_ring_mode(_modes_rec, rpm=rpm, slots=slots,
                                f_switch_hz=_fs)
    _rebuilt = _tight is not None
    if _tight is None:
        # Nothing stored for this duty — the coupled run's own block, which is
        # the same arithmetic against the carrier IT was solved with.
        _tight = (_g(cp or {}, "mechanical.modes.tightest")
                  or _g(em, "coupling.mechanical.modes.tightest"))
    if isinstance(_tight, dict) and _tight.get("margin_pct") is not None:
        ctx["ring_mode_margin_pct"] = _numf(_tight.get("margin_pct"))
        ctx["ring_mode_excitation"] = _tight.get("excitation")
        ctx["ring_mode_hz"] = _numf(_tight.get("f_hz"))
        ctx["ring_mode_note"] = (
            "mode at %s against %s at %s%s" % (
                _fmt(_tight.get("f_hz"), 1, "Hz"),
                _tight.get("excitation") or "the nearest excitation",
                _fmt(_tight.get("excitation_hz"), 1, "Hz"),
                (", re-judged against this duty's own PWM carrier (%s) and "
                 "speed (%s)" % (_fmt(_fs, 0, "Hz"), _fmt(rpm, 0, "rpm")))
                if _rebuilt else
                ", as the coupled run's modal step filed it"))

    # ── bearings ────────────────────────────────────────────────────────────
    if brg and brg.get("has_bearings"):
        worst = None
        for b in (brg.get("bearings") or []):
            sp = (b or {}).get("speed") or {}
            lim = _numf(sp.get("limit_rpm"))
            if lim and (worst is None or lim < worst[0]):
                worst = (lim, str(b.get("bearing") or ""))
        if worst:
            ctx["bearing_limit_rpm"] = worst[0]
            ctx["bearing_name"] = worst[1]
            ctx["bearing_rpm"] = rpm
        # THE GREASE HAS A TEMPERATURE LIMIT TOO (user 2026-09-14).  The seat
        # reaches 155.6 °C on the L155 peak duty and the only bearing rule was
        # the speed one; the lubricant cards carry `temp_range_c` and nothing
        # read it.  The base-oil viscosity behind M_rr is extrapolated to the
        # seat temperature by Walther whether or not the grease could survive
        # there, so a number past the range is a modelled number, not a
        # qualified one.
        _lube = bearing_lubricant(brg)
        if _lube and _lube.get("limit_c") is not None:
            # THE DUTY'S OWN SEAT, FIRST (BL-3, audit v6).  `brg` is a bearing
            # model, and on a duty whose coupled loop converged elsewhere it may
            # still carry the assigned 97.3 °C while this duty's loop settled at
            # 122.9 °C — past the LGLT_2 range, and the rule passed it green.
            # The record this duty's own coupled table prints decides.
            ctx["bearing_temp_c"] = (_numf((cp or {}).get("bearing_temp_c"))
                                     or _numf(brg.get("temp_c"))
                                     or _numf(brg.get("bearing_temp_c")))
            ctx["bearing_temp_limit_c"] = _lube["limit_c"]
            ctx["bearing_lubricant"] = _lube["name"]
            ctx["bearing_lubricant_source"] = _lube["source"]
            ctx["bearing_lubricant_range_c"] = _lube.get("range_c")
    return ctx


def _setting_of(col: Dict[str, Any], key: str) -> Any:
    """A panel setting the duty was saved with — a FLAT map with dotted keys."""
    m = (col.get("d") or {}).get("mesh")
    return m.get(key) if isinstance(m, dict) else None


def duty_setting(cfg_doc: Dict[str, Any], duty: Optional[str], key: str) -> Any:
    """The same panel setting, by duty NAME — for the sections that print one
    duty's answer without holding its column (the modal table, above all: its
    carrier is ``mesh['sim.fSwitch']``, which ``routes.family`` exposes as
    ``f_switch_hz``)."""
    dd = next((d for d in (cfg_doc.get("duties") or [])
               if isinstance(d, dict)
               and str(d.get("name") or "") == str(duty or "")), None)
    m = (dd or {}).get("mesh")
    return m.get(key) if isinstance(m, dict) else None


def gather_report_data(*, die: str, cfg: str, die_doc: Dict[str, Any],
                       cfg_doc: Dict[str, Any], duty: Optional[str] = None,
                       slot: Optional[Dict[str, float]] = None,
                       pictures: Optional[str] = None) -> Dict[str, Any]:
    """Everything the report is ABOUT, with no renderer anywhere near it.

    Split out of :func:`build_motor_report` on 2026-09-09, when the user asked
    for the document in Word as well: *"репорт лучше выдавать в формате doc"* —
    he edits it and forwards it to clients, and Word makes its own PDF.  Two
    renderers reading two different gathering passes is how a .docx and a .pdf
    of the same machine end up quoting two different torques, so there is one
    pass and both documents are built from its result.

    Reads only.  Nothing here starts a solve or writes a store — the same
    contract the module has always had, now stated in one function.
    """
    geo = dict(die_doc.get("geometry") or {})
    geo.update(cfg_doc.get("geometry_overrides") or {})
    wind = cfg_doc.get("winding") or {}
    mats = dict(cfg_doc.get("materials") or {})
    # A part the CONFIGURATION does not name, but a duty of it does, is the
    # duty's (2026-09-11).  Until the fix in `routes.family` the configuration
    # block only adopted magnet/stator/rotor, so a slot liner chosen in
    # Materials lived on in the duties and nowhere else — and this report, which
    # reads the configuration, printed the project's default instead of the
    # user's Al2O3.  The cover duty wins; another duty fills what it leaves.
    _md = _pick_duty(cfg_doc, duty) or {}
    for _src in ([_md] + [x for x in (cfg_doc.get("duties") or [])
                          if isinstance(x, dict) and x is not _md]):
        for _k, _v in ((_src or {}).get("materials") or {}).items():
            if _v and not mats.get(_k):
                mats[_k] = _v
    role = str(cfg_doc.get("role") or "motor")
    d_duty = _pick_duty(cfg_doc, duty) or {}

    live_geo = _live_geometry()
    live_fp = _live_fingerprint()
    delta = _geo_delta(geo, live_geo)
    # THE MISMATCH IS A SERVER FACT, NOT A CLIENT SENTENCE (client review
    # 2026-09-14).  The delta still drives everything it always drove — which
    # electromagnetic answer the cover quotes, and which foreign solver results
    # are dropped below — but it is no longer written into the document: a
    # client reading "the machine loaded on this server is a different
    # configuration" learns nothing about the machine they bought a report on,
    # and the geometry diff hands them the dimensions of an unrelated design.
    # It goes in the log, where the engineer who builds the report can see it.
    if delta:
        log.info("report: %s/%s differs from the loaded machine on %d "
                 "geometry key(s): %s", die, cfg, len(delta),
                 ", ".join(delta[:12]) + (" …" if len(delta) > 12 else ""))

    em_run = _last_transient()
    snap = _last_field_snapshot()
    th = _last_thermal()
    me = _last_mechanical()
    cp = _last_coupled()

    # ── which electromagnetic answer this report is about ────────────────────
    # The live run when the live machine still IS this configuration, otherwise
    # the duty's own saved summary — mixing a foreign machine's torque into a
    # configuration's report is exactly the failure this document exists to
    # prevent.  Whichever is used, the page names it.
    run_summary = (em_run or {}).get("summary") if isinstance(em_run, dict) else None
    run_stamp = _local_stamp((em_run or {}).get("computed_at"))
    duty_summary = d_duty.get("summary") if isinstance(d_duty.get("summary"), dict) else None
    # THE DUTY'S OWN SUMMARY FIRST (2026-09-11).  The cover used to quote the
    # machine's LAST run whenever the live machine was this configuration —
    # and the last run was the peak duty while the cover was about the rated
    # one, so the headline torque was one duty's, the bearing watts beside it
    # another's, and the comparison tables a third reading of both.  The saved
    # summary is what the columns are built from; the cover reads the same
    # record now, and the live run is the fallback for a duty never saved.
    em_from_run = False
    if duty_summary:
        em, em_src = dict(duty_summary), (
            "the duty '%s' as saved from the Simulation tab" % (d_duty.get("name") or "—"))
    elif run_summary:
        # ONE WORDING, WHETHER OR NOT THE LOADED MACHINE MATCHES (client review
        # 2026-09-14).  The second branch used to append "— on a machine that
        # DIFFERS from this configuration", which is the same internal remark
        # the cover note carried; the mismatch is logged above instead.
        em, em_src = dict(run_summary), (
            "the last Electromagnetic run, solved %s" % run_stamp)
        em_from_run = True
    else:
        em, em_src = {}, ""

    rpm = float(_g(d_duty, "rpm") or _g(em, "rpm") or 0.0)
    brg_assign = cfg_doc.get("bearings") or {}
    brg = _bearing_losses(brg_assign, geo, rpm, _rotating_mass_kg(em),
                          (brg_assign or {}).get("temp_c"), em)
    # …taking the coupled run's converged bearings when the cover duty has one
    # (filled in below, once the per-duty records are read).

    def _brg_at(rpm_: Any, em_: Optional[Dict[str, Any]] = None):
        """The SKF model of this machine at one speed and seat temperature —
        the callable `_with_coupled_bearings` re-runs when a duty's coupled loop
        converged to a seat other than the assigned one (BL-3 / MJ-11)."""
        def _f(temp_c: float) -> Optional[Dict[str, Any]]:
            try:
                from motor_ai_sim import bearings as _b
                if not _b.has_bearings(brg_assign) or not rpm_:
                    return None
                return _b.machine_bearing_losses(
                    brg_assign, rpm=float(rpm_), temp_c=float(temp_c),
                    rotor_mass_kg=float(_rotating_mass_kg(em_ or {}) or 0.0),
                    geometry=geo)
            except Exception as exc:                        # noqa: BLE001
                log.debug("report: bearing recompute unavailable (%s)", exc)
                return None
        return _f

    # THE PRINT THIS REPORT JUDGES AGAINST (BL-1, audit v6) — the
    # configuration's own, never the server's.
    report_fp = _report_fingerprint(die, cfg, cfg_doc)
    if report_fp and live_fp and live_fp not in ("nofp", report_fp):
        log.info("report: %s/%s fingerprints as %s; the loaded machine is %s — "
                 "the machine-level tab stores are not this report's",
                 die, cfg, report_fp, live_fp)

    def _src(name: str, entry: Optional[Dict[str, Any]],
             key: str = "geometry_fingerprint") -> _Source:
        e = entry or {}
        return _Source(name, e.get("computed_at"), e.get(key), live_fp,
                       report_fp=report_fp, delta=delta)

    sources: List[_Source] = []
    if em_run:
        sources.append(_src("Electromagnetic transient", em_run,
                            "geo_fingerprint"))
    if th.get("field"):
        sources.append(_src("Thermal map", th["field"]))
    if th.get("coupled"):
        sources.append(_src("Thermal coupled loop", th["coupled"]))
    if me.get("rotor_stress"):
        sources.append(_src("Mechanical rotor stress", me["rotor_stress"]))
    if me.get("critical_speeds"):
        sources.append(_src("Rotordynamics critical speeds",
                            me["critical_speeds"]))
    if cp:
        sources.append(_src("Coupled EM/thermal run", cp))

    # ── ANOTHER MACHINE'S ANSWER IS NOT IN THIS REPORT ──────────────────────
    #
    # User 2026-09-10, reading the first docx: *"машина должна быть одна и та
    # же; если нет для неё решения, вообще этот раздел не вносится в отчёт"*.
    # Until now a stale answer was printed with a red flag beside it, and the
    # numbers were read anyway — the rotordynamics section quoted a critical
    # speed of 15,534 rpm belonging to a different motor.  A flag is not a
    # defence; the entry is removed, and the section that needed it says it was
    # not solved, which is the truth for THIS machine.
    _foreign = {s.name for s in sources if s.stale is True}
    if _foreign:
        if "Thermal map" in _foreign:
            th.pop("field", None)
        if "Thermal coupled loop" in _foreign:
            th.pop("coupled", None)
        if "Mechanical rotor stress" in _foreign:
            me.pop("rotor_stress", None)
            me.pop("modes", None)          # the modal answer rides the same solve
        if "Rotordynamics critical speeds" in _foreign:
            me.pop("critical_speeds", None)
        if "Coupled EM/thermal run" in _foreign:
            cp = {}
        if "Electromagnetic transient" in _foreign:
            em_run = None
            # …but only throw the NUMBERS away if they came from that run.
            # A duty carries its own saved summary, filed under this die and
            # this configuration, and nothing about the editor having another
            # machine open makes it foreign.  Wiping it emptied the whole
            # document — no operating point, no losses, no maps — the moment
            # the user moved on to the next motor (2026-09-11).
            if em_from_run:
                if duty_summary:
                    em, em_src = dict(duty_summary), (
                        "the duty '%s' as saved from the Simulation tab"
                        % (d_duty.get("name") or "—"))
                    em_from_run = False
                else:
                    em, em_src = {}, ""
        sources = [s for s in sources if s.stale is not True]
        log.info("report: dropped %d foreign-machine source(s): %s",
                 len(_foreign), ", ".join(sorted(_foreign)))

    # ── one column per duty (2026-09-09) ─────────────────────────────────────
    # Everything from here to the warnings section is per DUTY, not per machine:
    # the user asked for the configuration's operating points to be compared
    # across every simulation, and the per-duty store is what makes that
    # possible without a column ever borrowing its neighbour's number.
    cols, map_owner = _duty_columns(die, cfg, cfg_doc, th, me, cp,
                                    same_machine=not delta)
    # The cover's bearings: the cover duty's coupled record, when it has one.
    _cov = next((c for c in cols if c.get("duty") == (d_duty.get("name") or "")), None)
    if _cov is not None:
        brg = _with_coupled_bearings(brg, (_cov.get("res") or {}).get("coupled"),
                                     rpm, _brg_at(rpm, em))
        _cov["brg"] = brg
    # ── THE COVER DUTY'S OWN SUPPLY (2026-09-14) ────────────────────────────
    # When that duty's coupled answer was reached on the inverter, the inverter
    # is what this report is about: the cover, section 4 and every chart read
    # the PWM run's summary, which `_duty_columns` has already overlaid onto the
    # column.  `em` was formed above from the duty's SINUSOIDAL summary, so it
    # is re-pointed here — one place, before anything has used it for a number.
    supply = supply_words(_cov) if _cov is not None else SUPPLY_SINE
    if _cov is not None and _cov.get("drive") == "pwm" and _cov.get("em"):
        em = dict(_cov["em"])
        em_src = ((em_src + " — on the inverter, from %s"
                   % _cov.get("pwm_summary_source"))
                  if em_src else
                  "the PWM run of the duty '%s'" % (d_duty.get("name") or "—"))
    for _c in cols:
        if _c is not _cov and _c.get("brg") is None:
            _rpm_c = float(_c["d"].get("rpm") or 0.0)
            _c["brg"] = _with_coupled_bearings(
                _bearing_losses(brg_assign, geo, _rpm_c,
                                _rotating_mass_kg(_c.get("em") or {}),
                                (brg_assign or {}).get("temp_c"), _c.get("em") or {}),
                (_c.get("res") or {}).get("coupled"), _numf(_c["d"].get("rpm")),
                _brg_at(_rpm_c, _c.get("em") or {}))
    # The duty the EDITOR has loaded, for the overview's "loaded" mark —
    # never a matched one: a machine nobody has open has no loaded duty.
    active_duty = next((c["duty"] for c in cols if c.get("active")), None)
    # …and the duty each PICTURE belongs to, which is the duty the answer
    # behind it was solved at (2026-09-09).  The electromagnetic maps come
    # from the run snapshot, so they are matched on its own point.
    em_duty = (active_duty or _duty_at_point(
        [d for d in (cfg_doc.get('duties') or []) if isinstance(d, dict)],
        _g(em, 'rpm'), _g(em, 'I_phase_rms_A') or _g(em, 'I_phase_rms')))
    batt = cfg_doc.get("battery") or {}
    mag_lim, mag_note = _magnet_limit(mats.get("magnet"))
    ins_lim, ins_note = _insulation_limit(mats)
    cold_k, cold_note = _cold_br_factor(mats.get("magnet"))
    # The speed a runaway must stay above is the fastest point this
    # configuration is designed for — its own duties, not a number typed here.
    _speeds = [_numf(c["d"].get("rpm")) for c in cols]
    max_speed = max([s for s in _speeds if s] or [0.0]) or None
    # THE BRIDGE'S OWN THD, before anything judges or prints it (MJ-1, audit
    # v7).  It is integrated from the pulse train, which lives in the duty's
    # stored run and not in any summary, so it is read here — where the die and
    # the configuration are known — and travels on the column for the
    # comparison row and the findings row alike.
    for _c in cols:
        _bt = duty_bridge_thd_pct(die, cfg, _c, cfg_doc)
        if _bt is not None:
            _c["bridge_thd_pct"] = _bt
    ctxs = {c["duty"]: _warning_context(
        c, mats=mats, batt=batt,
        brg=c.get("brg") or _bearing_losses(
            brg_assign, geo, float(c["d"].get("rpm") or 0.0),
            _rotating_mass_kg(c.get("em") or {}),
            (brg_assign or {}).get("temp_c"), c.get("em") or {}),
        max_speed_rpm=max_speed, mag_lim=mag_lim, mag_note=mag_note,
        ins_lim=ins_lim, ins_note=ins_note, cold_k=cold_k,
        cold_note=cold_note, slots=_slots_poles(geo)[0]) for c in cols}
    # …and HOW MUCH of the magnet is under the band, read from each duty's own
    # stored demagnetisation field (user 2026-09-14): a worst element of 18.4 %
    # is a corner or a pole, and only the field says which.  Read-only, cached,
    # and absent when a duty kept no field — the rule then reads as before.
    for _d, _c in ctxs.items():
        _c["demag_corner"] = demag_corner_stats(die, cfg, _d)

    # ── THE PICTURES ARE ONE DUTY'S, and that duty is the rated one ─────────
    # Read from the per-duty FIELD store, so a map is the map that duty was
    # solved with rather than whatever the last press of a Solve button left in
    # the machine's store (user 2026-09-10: "картинки моделирования должны быть
    # из rated").  Empty when nothing is stored, and then every page falls back
    # to the machine's last solve exactly as it did before.
    _duties_l = [d for d in (cfg_doc.get("duties") or []) if isinstance(d, dict)]
    # THE USER'S CHOICE first (2026-09-11: "нужно ещё сделать выбор, из какого
    # режима мы публикуем картинки в отчёте"): `pictures` names a duty of this
    # configuration, and its stored fields are the maps.  A duty with nothing
    # stored cannot be drawn from, so the rule below takes over and the log
    # says so — the document never comes out with blank map pages because of a
    # menu choice.
    pic_duty = None
    if pictures:
        _src = _duty_map_sources(die, cfg, pictures)
        if _src:
            pic_duty = pictures
        else:
            log.info("report: pictures requested from duty %r but it has no "
                     "stored field — falling back to the rated rule", pictures)
    if pic_duty is None:
        pic_duty = _rated_duty(_duties_l, die, cfg, active_duty)
    pic_src = _duty_map_sources(die, cfg, pic_duty)
    if pic_src:
        log.info("report: pictures from duty %r (%s)", pic_duty,
                 ", ".join(sorted(pic_src)))
    # ── SECTIONS 6 AND 7 FOLLOW `duty=`, NEVER `pictures=` (BL-1, audit v5) ──
    #
    # The boundary conditions, the per-part temperatures, the heat budget and
    # its closure sentence, the stress and fit tables, the interfaces, the
    # modal excitation column, the critical speeds and the mode gallery used to
    # be read from the PICTURE duty's stored records, while the cover and
    # sections 1–5 were read from the report duty's.  `duty=rated&pictures=peak`
    # therefore produced a document whose section 6 was the peak duty's — and
    # whose closure line subtracted the RATED duty's electromagnetic loss from
    # the PEAK duty's map integral and printed the 4 kW difference as "the loss
    # map's own integration error".  With the figures paired, `pictures=` has
    # no business driving a section at all: it survives only as the right-hand
    # pick of the pair on a configuration with more than two duties.
    detail_duty = str(d_duty.get("name") or "") or None
    detail_src = (_duty_map_sources(die, cfg, detail_duty)
                  if detail_duty else {}) or {}
    # WHEN THE MACHINE-LEVEL ANSWER IS GONE, the duty's own stands in — see
    # `_duty_detail_sources`.  Asked UNCONDITIONALLY since BL-1 (audit v6): the
    # guard used to skip the lookup whenever every tab store was still present,
    # and with a foreign machine loaded "present" meant "the other motor's".
    # Only what is actually missing is filled, so a server still holding this
    # machine keeps quoting its live solve.
    _fb = _duty_detail_sources(die, cfg, detail_duty or active_duty, detail_src)
    th_detail_from_duty = me_detail_from_duty = False
    if _fb["th"] and not (th.get("field") or th.get("coupled")):
        th.update(_fb["th"])
        th_detail_from_duty = True
        log.info("report: thermal detail from duty %r (no machine-level answer "
                 "of this configuration)", detail_duty)
    for _k, _v in (_fb["me"] or {}).items():
        if not me.get(_k):
            me[_k] = _v
            me_detail_from_duty = True
            log.info("report: %s detail from duty %r", _k, detail_duty)
    # …and the coupled loop the thermal page's closing block reads (MJ-10).  It
    # used to be the machine-level `_last_coupled()` alone, so a dropped store
    # left section 6 saying "NOT converged, coil residual 192.3 K" while the
    # same duty's own record — the one section 3 prints — says converged at
    # 1.6 K.
    if not cp:
        _cpd = ((next((c for c in cols if c.get("duty") == detail_duty), None)
                 or {}).get("res") or {}).get("coupled")
        if isinstance(_cpd, dict) and _cpd:
            # …in the shape the page reads: the machine-level store nests its
            # loop under "coupling" and the per-duty record is flat.
            cp = {"coupling": dict(_cpd)}
            log.info("report: coupled detail from duty %r", detail_duty)

    # The picture duty's stored waveforms — torque, currents, voltages — for the
    # charts on the electromagnetic page (2026-09-11).  Read once here, like
    # everything else this function gathers.
    wf_duty = pic_duty or active_duty
    # …AND FROM THE RUN THE REST OF THE DOCUMENT IS ABOUT (2026-09-14).  On a
    # PWM duty the charts must be the PWM run's; when that run was saved
    # without its waveforms the sinusoidal ones are drawn instead and the
    # caption says so, because a torque trace with no carrier ripple under a
    # "PWM 24 kHz" headline is the one picture this report may not print
    # unlabelled.
    _wf_col = next((c for c in cols if c.get("duty") == wf_duty), None)
    _wf_pwm = (_wf_col or {}).get("drive") == "pwm"
    wf = (duty_waveforms(die, cfg, wf_duty, cfg_doc, PWM_RUN_DRIVE)
          if _wf_pwm else {})
    # …AND THE BRIDGE THAT PRODUCED THEM (2026-09-15).  The payload carries its
    # own `pwm` block on every run the inverter path wrote; the coupled
    # record's `inverter` block is the fallback the voltage chart regenerates
    # the pulse train from when it does not (see `pwm_bridge_ll`).
    wf = _with_inverter(wf, _wf_col)
    wf_sine_on_pwm = bool(_wf_pwm and not wf)
    if not wf:
        wf = duty_waveforms(die, cfg, wf_duty, cfg_doc)
    # A picture duty saved without its waveforms (the browser's copy of the
    # run was behind the server's — 2026-09-13) must not cost the report its
    # three charts: the report duty's own run, then any duty of this
    # configuration that kept one, and the caption says whose they are.
    if not wf:
        for _cand in ([str(d_duty.get("name") or "")]
                      + [str(x.get("name") or "") for x in _duties_l]):
            if not _cand or _cand == wf_duty:
                continue
            _w = duty_waveforms(die, cfg, _cand, cfg_doc)
            if _w:
                wf, wf_duty = _w, _cand
                log.info("report: waveforms from duty %r (the picture duty %r "
                         "has no stored run)", _cand, pic_duty or active_duty)
                break
    wf_note = ("" if (not wf or wf_duty == (pic_duty or active_duty)) else
               " These are the waveforms of the duty '%s' — the duty '%s' was "
               "saved without its run." % (wf_duty, pic_duty or active_duty))
    if wf and wf_sine_on_pwm:
        wf_note += " " + WF_SINE_ON_PWM
    # The caption every electromagnetic map carries: the duty and the point
    # the PICTURES are of (not the report duty's point — 2026-09-13).
    _mp_duty, _mp_rpm, _mp_cur = em_map_point(pic_duty, pic_src, cfg_doc, em,
                                              d_duty, em_duty, em_run, cols)
    em_map_tag = _map_tag(_mp_duty, _mp_rpm, _mp_cur)
    em_map_point_text = " at ".join(
        b for b in (_fmt(_mp_cur, 1, "A rms") if _numf(_mp_cur) else "",
                    _fmt(_mp_rpm, 0, "rpm") if _numf(_mp_rpm) else "") if b)
    # …AND THE SAME FOR THE THERMAL AND MECHANICAL PICTURES (2026-09-14).  They
    # were captioned "[no duty identified]" while being drawn from the picture
    # duty's own stored fields, because the caption read the machine-level
    # owner map — which is empty when another machine is loaded on the server.
    # A map taken from `pic_src[kind]` belongs to `pic_duty`, full stop; only a
    # map that really is the machine's last unattributed solve keeps the "no
    # duty identified" wording.
    # …and they name the DETAIL duty, which is the report duty (BL-1): the
    # tables, the closure sentence and the source lines under them are that
    # duty's, so the sentence that says whose answer this section is must be
    # the same duty or the two contradict each other on one page.
    th_from_duty = bool(detail_src.get("thermal"))
    me_from_duty = bool(detail_src.get("rotor_stress")
                        or detail_src.get("modes"))
    th_map_duty = detail_duty if th_from_duty else map_owner.get("thermal")
    me_map_duty = detail_duty if me_from_duty else map_owner.get("rotor_stress")
    _pic_rpm, _pic_cur = duty_point(cfg_doc, detail_duty, cols)
    th_map_rpm = _pic_rpm if th_from_duty else None
    me_map_rpm = _pic_rpm if me_from_duty else None
    th_map_cur = _pic_cur if th_from_duty else None
    me_map_cur = _pic_cur if me_from_duty else None

    # ── AND THE SAME PICTURE FOR THE OTHER DUTY, BESIDE IT (2026-09-14) ─────
    # User: *"добавим ещё картинки из peak — слева картинка из rated, справа из
    # peak"*.  Both sides are gathered here, once, exactly like everything else
    # this function reads: the stored fields, the waveforms, and the thermal and
    # mechanical records each side's charts are drawn from.  The renderers only
    # place them.
    _pair_l, _pair_r, _pair_note = pair_duties(
        _duties_l, die, cfg, active_duty, pictures, fallback=pic_duty)

    def _side(name: Optional[str], src: Optional[Dict[str, Any]] = None
              ) -> Optional[Dict[str, Any]]:
        if not name:
            return None
        s = _duty_map_sources(die, cfg, name) if src is None else src
        _rpm, _cur = duty_point(cfg_doc, name, cols)
        _col = next((c for c in cols if c.get("duty") == name), None)
        _th = _side_thermal(die, cfg, name, s, th, th_map_duty)
        _me = _side_mech(die, cfg, name, s, me, me_map_duty)
        # The same rule as the report duty's charts: a PWM duty's own run
        # first, the sinusoid's only as a labelled substitute.
        _wf = (duty_waveforms(die, cfg, name, cfg_doc, PWM_RUN_DRIVE)
               if (_col or {}).get("drive") == "pwm" else {})
        _wf = _with_inverter(_wf, _col)
        _wf_sub = bool((_col or {}).get("drive") == "pwm" and not _wf)
        if not _wf:
            _wf = duty_waveforms(die, cfg, name, cfg_doc)
        return {
            "duty": name, "src": s, "rpm": _rpm, "cur": _cur,
            "point": point_words(_rpm, _cur),
            "wf": _wf, "wf_sine_on_pwm": _wf_sub,
            "supply": supply_words(_col or {}),
            # …and the short form the CAPTION carries (MJ-6): every pair caption
            # named two duties and two operating points and never said that the
            # left half was switched and the right half a clean sinusoid.
            "supply_short": supply_short(_col or {}),
            "em": ((_col or {}).get("em") or {}),
            "brg": ((_col or {}).get("brg") or {}),
            "th": _th,
            "th_inner": ((_th.get("field") if isinstance((_th or {}).get("field"),
                                                         dict) else _th)
                         if _th else None),
            "mech": _me, "case": _side_case(_me),
            "demag": demag_corner_stats(die, cfg, name),
            # WHERE EACH MAP CAME FROM, against the record its table came from
            # (reviewer 2026-09-15) — empty when they are one solve, which is
            # every well-behaved duty.
            "prov": duty_map_provenance(_duty_record(die, cfg, name),
                                        (s or {}).get("_meta")),
        }

    pair = {
        "left": _side(_pair_l, pic_src if _pair_l == pic_duty else None),
        "right": _side(_pair_r),
        "note": _pair_note,
        "n_duties": len(_duties_l),
    }

    # ── HOW MANY SECTIONS THIS DOCUMENT HAS (2026-09-14) ────────────────────
    # The duty-cycle section is printed only when a duty has a cycle record —
    # a machine nobody ran a duty cycle on gets no empty chapter — so the
    # numbering after it is not a constant and both renderers read it here.
    has_duty_cycle = any(duty_cycle_record(c) is not None for c in cols)
    sec = section_numbers(has_duty_cycle)

    return {
        "pair": pair,
        # One running figure number per document; both renderers count in the
        # same order, so "Fig. 7" is the same picture in the .docx and the PDF.
        "fig_n": [0],
        "die": die, "cfg": cfg, "die_doc": die_doc, "cfg_doc": cfg_doc, "wf": wf,
        "role": role, "geo": geo, "wind": wind, "mats": mats, "slot": slot,
        "live_geo": live_geo, "live_fp": live_fp, "report_fp": report_fp,
        "d_duty": d_duty, "em": em, "em_src": em_src, "delta": delta,
        "brg_assign": brg_assign, "brg": brg, "sources": sources,
        "em_run": em_run, "snap": snap, "th": th, "me": me, "cp": cp,
        "cols": cols, "map_owner": map_owner, "active_duty": active_duty,
        "em_duty": em_duty, "batt": batt, "ctxs": ctxs,
        "pic_duty": pic_duty, "pic_src": pic_src,
        # The REPORT duty's own stored fields — what sections 6 and 7 read
        # when they are not drawing a pair (BL-1).
        "detail_src": detail_src,
        "em_map_tag": em_map_tag, "em_map_duty": _mp_duty,
        "em_map_point": em_map_point_text,
        "em_map_from_duty": bool((pic_src or {}).get("em")),
        # THE MODAL TABLE'S OWN EXCITATION INPUTS (2026-09-14, A1): the duty
        # the modes belong to, ITS carrier and ITS speed, and the machine's
        # slot count.  Never the process-global carrier, which belongs to
        # whatever machine the server has loaded.
        # …the DETAIL duty's, like the rest of section 7 (BL-1).
        "slots": _slots_poles(geo)[0],
        "detail_duty": detail_duty,
        "modes_duty": (me_map_duty or detail_duty or None),
        "modes_rpm": duty_point(cfg_doc, me_map_duty or detail_duty or "",
                                cols)[0],
        "modes_f_switch_hz": duty_setting(
            cfg_doc, me_map_duty or detail_duty or "", "sim.fSwitch"),
        "em_from_run": bool(em_from_run),
        # The COVER duty's demagnetisation-field statistics, for the section-4
        # paragraph: the worst element's company — how many elements and how
        # much area are under the band with it (user 2026-09-14).
        "demag_corner": (ctxs.get(str(d_duty.get("name") or "")) or {}
                         ).get("demag_corner"),
        "wf_duty": wf_duty, "wf_note": wf_note,
        # WHAT FEEDS THE MACHINE, and how the document is numbered because of
        # it (2026-09-14).  `supply` is the cover duty's; `sec` is the section
        # map both renderers head their pages from — the duty-cycle section
        # exists only when a duty has a cycle record, and everything after it
        # shifts by one.
        "supply": supply, "any_pwm": any_pwm(cols),
        "wf_sine_on_pwm": bool(wf_sine_on_pwm),
        "has_duty_cycle": has_duty_cycle,
        "sec": sec,
        # WHERE SECTIONS 6 AND 7 GOT THEIR TABLES (BL-1) — the duty's own
        # record, or the machine-level tab store when it really is this
        # configuration's.  The source line under each heading says which.
        "th_detail_from_duty": th_detail_from_duty,
        "me_detail_from_duty": me_detail_from_duty,
        "th_map_duty": th_map_duty, "th_map_from_duty": th_from_duty,
        "th_map_rpm": th_map_rpm, "th_map_cur": th_map_cur,
        "me_map_duty": me_map_duty, "me_map_from_duty": me_from_duty,
        "me_map_rpm": me_map_rpm, "me_map_cur": me_map_cur,
    }


def build_motor_report(*, die: str, cfg: str, die_doc: Dict[str, Any],
                       cfg_doc: Dict[str, Any], duty: Optional[str] = None,
                       slot: Optional[Dict[str, float]] = None,
                       compress: bool = True,
                       pictures: Optional[str] = None) -> bytes:
    """The whole report as PDF bytes.  Reads only; solves nothing.

    ``duty`` names the operating point the cover is about (the first saved duty
    when omitted).  ``compress=False`` writes uncompressed page streams, which
    is how the tests read the text back without a PDF library.
    """
    from reportlab.lib.colors import HexColor
    from reportlab.platypus import PageBreak, SimpleDocTemplate

    st = _styles()
    D = gather_report_data(die=die, cfg=cfg, die_doc=die_doc, cfg_doc=cfg_doc,
                           duty=duty, slot=slot, pictures=pictures)
    geo, wind, mats, role = D["geo"], D["wind"], D["mats"], D["role"]
    d_duty, em, em_src, delta = D["d_duty"], D["em"], D["em_src"], D["delta"]
    brg_assign, brg, sources = D["brg_assign"], D["brg"], D["sources"]
    em_run, snap, th, me, cp = D["em_run"], D["snap"], D["th"], D["me"], D["cp"]
    # `map_owner` is no longer unpacked here: since 2026-09-14 the thermal and
    # mechanical pages take the picture duty and its point out of `D` directly,
    # and the machine-level owner is only the fallback `_gather` already applied.
    cols = D["cols"]
    active_duty, em_duty = D["active_duty"], D["em_duty"]
    batt, ctxs, live_fp = D["batt"], D["ctxs"], D["live_fp"]
    sec = D.get("sec")

    story: List[Any] = []
    story += _cover(st, die, cfg, role, geo, mats, d_duty, em, em_src,
                    brg, sources, delta, batt,
                    used_live=bool(D.get("em_from_run")), cols=cols, ctxs=ctxs,
                    supply=D.get("supply"), sec=sec)
    story.append(PageBreak())
    # The stamp every chart drawn from the REPORT duty's own summary carries —
    # the same one `report_docx._report_tag` builds (B9 / MJ-5).
    _rtag = _map_tag(str(d_duty.get("name") or "") or None,
                     _g(em, "rpm") or d_duty.get("rpm"),
                     _g(em, "I_phase_rms_A") or d_duty.get("current_arms"))
    # The figure counter is only used by a document that HAS pairs — see
    # `fig_label` (CS-5).
    _figs = D.get("fig_n") if ((D.get("pair") or {}).get("right")) else None
    story += _machine_page(st, die_doc, geo, wind, mats, slot, brg_assign, brg,
                           em, D.get("ctxs"), report_tag=_rtag, figs=_figs,
                           sec=sec)
    from reportlab.platypus import CondPageBreak
    story.append(CondPageBreak(PAGE_H * 0.5))
    story += _duty_overview(st, cols, active_duty)
    story.append(PageBreak())
    story.append(_para(section_heading(sec, "compare"), st["h1"]))
    story.append(_para(COMPARE_INTRO, st["body"]))
    story += _em_compare(st, cols, batt, brg)
    story.append(CondPageBreak(PAGE_H * 0.45))
    story += _thermal_compare(st, cols)
    story.append(CondPageBreak(PAGE_H * 0.45))
    story += _mech_compare(st, cols)
    story.append(CondPageBreak(PAGE_H * 0.4))
    story += _coupled_compare(st, cols)
    story.append(PageBreak())
    story += _em_page(st, em, em_src, d_duty, brg, snap, em_run, geo, mats,
                      D.get("em_map_duty") or em_duty, D.get("wf"),
                      map_tag=D.get("em_map_tag"),
                      map_point=D.get("em_map_point"),
                      wf_note=D.get("wf_note") or "",
                      batt=batt, map_from_duty=bool(D.get("em_map_from_duty")),
                      demag_corner=D.get("demag_corner"), report_tag=_rtag,
                      pair=D.get("pair"), figs=_figs,
                      drive=("pwm" if (D.get("supply")
                                       or SUPPLY_SINE) != SUPPLY_SINE
                             else "sine"),
                      em_sine=(_col_of(cols, str(d_duty.get("name") or ""))
                               or {}).get("em_sine"),
                      inverter=duty_inverter(
                          _col_of(cols, str(d_duty.get("name") or "")) or {}))
    # From here on a section opens a NEW page only when less than half of the
    # current one is left: with full-width field maps a hard break after each
    # section left a map alone on a page with three quarters of white under it
    # (user 2026-09-08: "убери эти здоровенные пропуски").
    story.append(CondPageBreak(PAGE_H * 0.5))
    # 5 · PWM influence — the one section that is not the sinusoidal supply
    # (user decision 2026-09-14).  It sits between the electromagnetics it
    # qualifies and the thermal section it warns about.
    story += _pwm_page(st, cols, brg, sec,
                       duty=str(d_duty.get('name') or '') or None)
    story.append(CondPageBreak(PAGE_H * 0.5))
    story += _thermal_page(st, th, cp, D.get("th_map_duty"),
                           field_src=(D.get("detail_src") or {}).get("thermal"),
                           run_note="", em=D.get("em"),
                           map_from_duty=bool(D.get("th_map_from_duty")),
                           map_rpm=D.get("th_map_rpm"),
                           map_cur=D.get("th_map_cur"),
                           mats=mats, ctxs=ctxs,
                           pair=D.get("pair"), figs=_figs,
                           em_duty=D.get("detail_duty"), sec=sec,
                           detail_from_duty=bool(D.get("th_detail_from_duty")),
                           col=_col_of(cols, str(d_duty.get("name") or "")))
    # The duty-cycle section, when a duty of this configuration has one: it
    # follows the steady states it replaces and precedes the mechanics, and on
    # every other machine it is not printed at all (2026-09-14).
    if D.get("has_duty_cycle"):
        story.append(CondPageBreak(PAGE_H * 0.5))
        story += _duty_cycle_page(st, cols, ctxs, sec, _figs)
    story.append(CondPageBreak(PAGE_H * 0.5))
    story += _mech_page(st, me, D.get("me_map_duty"),
                        field_src=(D.get("detail_src") or {}).get("rotor_stress"),
                        modes_src=(D.get("detail_src") or {}).get("modes"),
                        em=em,
                        map_from_duty=bool(D.get("me_map_from_duty")),
                        map_rpm=D.get("me_map_rpm"),
                        map_cur=D.get("me_map_cur"),
                        modes_rpm=D.get("modes_rpm"), slots=D.get("slots"),
                        f_switch_hz=D.get("modes_f_switch_hz"),
                        pair=D.get("pair"), figs=_figs, sec=sec,
                        detail_from_duty=bool(D.get("me_detail_from_duty")))
    story.append(PageBreak())
    story += _warnings_page(st, cols, ctxs, sec)
    story.append(CondPageBreak(PAGE_H * 0.45))
    story += _notes_page(st, sources, brg, em, th, me, cp, sec)
    story.append(CondPageBreak(PAGE_H * 0.35))
    # No per-duty source table (user 2026-09-10: "это тоже выкинь") — it listed
    # which store each column was read from and when, and since a foreign answer
    # is dropped from this report rather than flagged in it, the Machine column
    # said "this machine" on every row.

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=(PAGE_W, PAGE_H),
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN, bottomMargin=MARGIN + 10,
        title=f"{die} {cfg} — motor report", author="motor_ai_sim",
        subject="Full report of every solver's last result for this configuration",
        pageCompression=1 if compress else 0)

    # NO TIMESTAMP (user 2026-09-10: "метки времени можно вообще убрать"; the
    # cover and the footer carried two of them a minute apart — reviewer
    # 2026-09-14, D4).  The footer says which machine the sheet belongs to and
    # nothing else.
    stamp = f"{die} · {cfg}"

    def _decorate(canv, _doc):
        canv.saveState()
        canv.setFont("Helvetica", 7)
        canv.setFillColor(HexColor(NOTE))
        canv.drawString(MARGIN, MARGIN * 0.62, stamp)
        canv.drawRightString(PAGE_W - MARGIN, MARGIN * 0.62,
                             "page %d" % canv.getPageNumber())
        canv.setStrokeColor(HexColor("#DDDDDD"))
        canv.setLineWidth(0.4)
        canv.line(MARGIN, MARGIN * 0.62 + 9, PAGE_W - MARGIN, MARGIN * 0.62 + 9)
        canv.restoreState()

    doc.build(story, onFirstPage=_decorate, onLaterPages=_decorate)
    return buf.getvalue()


# ── page 1 · cover ──────────────────────────────────────────────────────────


NO_SOURCES_YET = (
    "No solver result is stored on this server yet — run the point on the "
    "Simulation tab, then Thermal and Mechanical, and export again.")

#: The cover's ONE remaining sentence about where the electromagnetic numbers
#: came from, printed only when they were NOT read from a saved duty summary.
#: Neutral by design (client review 2026-09-14): it says which store answered,
#: and nothing about what machine the server happens to have loaded.
EM_FROM_LAST_RUN_NOTE = (
    "The electromagnetic numbers in this document were read from the last "
    "stored electromagnetic run rather than from a saved duty summary.")

# ── HOW THE DOCUMENT IS NUMBERED ───────────────────────────────────────────
# One section list, read by both renderers, because the DUTY-CYCLE section
# (2026-09-14) exists only on a machine somebody ran a duty cycle for.  A
# document that printed an empty "Duty cycle" chapter on every other machine
# would be worse than one whose mechanical section is sometimes 7 and sometimes
# 8 — so the numbering moves, and nothing in either renderer types a number.

#: The sections, in the order they are printed.  ``duty_cycle`` drops out when
#: no duty of the configuration carries a cycle record.
SECTION_ORDER: Tuple[str, ...] = (
    "machine", "duties", "compare", "em", "pwm", "thermal", "duty_cycle",
    "mech", "warnings", "notes")

SECTION_TITLES: Dict[str, str] = {
    "machine": "Machine",
    "duties": "Duties and what has been solved for them",
    "compare": "Every duty, every simulation",
    "em": "Electromagnetic in detail",
    "pwm": "PWM influence",
    "thermal": "Thermal in detail",
    "duty_cycle": "Duty cycle",
    "mech": "Mechanical in detail",
    "warnings": "Warnings and limits",
    "notes": "Assumptions and notes",
}

#: What each section is called in the contents line — the same words, in the
#: running case that line is written in.
SECTION_CONTENTS_WORDS: Dict[str, str] = {
    "machine": "machine",
    "duties": "duties and what has been solved for them",
    "compare": "every duty, every simulation (the comparison tables)",
    "em": "electromagnetic in detail",
    "pwm": "PWM influence",
    "thermal": "thermal in detail",
    "duty_cycle": "duty cycle",
    "mech": "mechanical in detail",
    "warnings": "warnings and limits",
    "notes": "assumptions, notes and sources",
}


def section_numbers(has_duty_cycle: bool = False) -> Dict[str, int]:
    """``{"thermal": 6, "mech": 7, …}`` — this document's section numbers."""
    out: Dict[str, int] = {}
    n = 1
    for key in SECTION_ORDER:
        if key == "duty_cycle" and not has_duty_cycle:
            continue
        out[key] = n
        n += 1
    return out


def _sec_no(sec: Optional[Dict[str, int]], key: str) -> int:
    """This document's number for one section.

    A caller with no map at all gets the default numbering; a caller whose map
    does not carry the key (a duty-cycle heading built from a plain map) gets
    the position the key WOULD have, never a KeyError — a report that raised
    while numbering its own heading would be a defect of the worst kind.
    """
    s = sec if isinstance(sec, dict) else {}
    if key in s:
        return int(s[key])
    full = section_numbers(True)
    return int(section_numbers().get(key, full.get(key, len(SECTION_ORDER))))


def section_heading(sec: Optional[Dict[str, int]], key: str) -> str:
    """``"6 · Thermal in detail"`` — a heading, numbered for THIS document."""
    return "%d · %s" % (_sec_no(sec, key), SECTION_TITLES[key])


def sec_ref(sec: Optional[Dict[str, int]], key: str) -> str:
    """``"section 8"`` — a cross-reference that survives the renumbering."""
    return "section %d" % _sec_no(sec, key)


def compare_ref(sec: Optional[Dict[str, int]] = None) -> str:
    """``"the comparison table in section 3"`` — NEVER a page number (BT-5,
    2026-09-16).

    Three cross-references said "on page 3" and were hard-coded.  In the Word
    render section 3 starts on page 7 and page 3 is "1 · Machine"; in the
    in-process PDF it is page 6.  A page number is a property of the RENDERER
    and of how the figures happen to fall, and this document is built by two of
    them — so the pointer is to the section, which is numbered by
    :func:`section_numbers` and survives a duty-cycle section appearing or not.
    """
    return "the comparison table in %s" % sec_ref(sec, "compare")


def contents_line(sec: Optional[Dict[str, int]] = None) -> str:
    s = sec or section_numbers()
    return ("Contents — " + " · ".join(
        "%d %s" % (s[k], SECTION_CONTENTS_WORDS[k])
        for k in SECTION_ORDER if k in s) + ".")


#: The contents line of a document with no duty-cycle section — the shape the
#: line has had since 2026-09-14 and the one most machines get.
CONTENTS_LINE = contents_line()


def machine_subtitle(role: str, geo: Dict[str, Any],
                     mats: Dict[str, Any]) -> str:
    return (f"{role.capitalize()} · "
            f"{float(geo.get('stator_diameter') or 0):g} mm outer diameter, "
            f"{float(geo.get('motor_length') or 0):g} mm stack, "
            f"{_slots_poles(geo)[0]} slots / {_slots_poles(geo)[1]} poles, "
            f"{mats.get('magnet') or 'PM'} magnets")


def cover_source_note(used_live: bool = False) -> str:
    """The cover's provenance line — ``""`` in the normal case.

    REPLACES ``geometry_delta_text`` (client review 2026-09-14).  That function
    printed, on page 1 of a client deliverable, either "the machine loaded on
    this server is a different configuration (40 geometry key(s) differ:
    stator_diameter 200 -> 85 …) and nothing here was read from it" or its
    flagged twin.  Both are remarks about the engineer's own session: the report
    is built from the stored duties of the configuration it names, and what some
    server has open while it is built is not a fact about the machine — it is,
    in the "40 keys differ" form, a leak of an unrelated design's dimensions.
    The mismatch is still detected; it goes to the log in
    :func:`gather_report_data` and it still drops foreign solver results.

    ``used_live`` — the electromagnetic numbers came from the last stored run
    instead of a saved duty summary.  That IS a fact about this document, so one
    neutral sentence stays; the geometry diff does not.
    """
    return EM_FROM_LAST_RUN_NOTE if used_live else ""


def shaft_view(em: Dict[str, Any], brg: Optional[Dict[str, Any]],
               mode: Optional[str] = None) -> Dict[str, Optional[float]]:
    """The power balance exactly as the Simulation card and the catalog row
    state it (SummaryTable, 2026-09-13: "почему цифры не бьют") — ONE set of
    formulas for every table of this document:

      P_rotor  = |P_mech| × k_3d           (the 3-D end-effect factor, if any)
      generator: P_shaft = P_rotor + P_mech_extra   (the prime mover supplies
                 the friction too);  P_elec = P_rotor − P_loss_em;
                 η_em = P_elec / P_rotor;  η_shaft = P_elec / P_shaft
      motor:     P_shaft = P_rotor − P_mech_extra;  P_elec = P_rotor + P_loss_em;
                 η_em = P_rotor / P_elec;  η_shaft = P_shaft / P_elec

    Losses stay 2-D (conservative — the card's rule).  This document used to
    take the friction OFF a generator's shaft power (534.8 kW beside the
    catalog's 538.2) and scale the efficiency from the 2-D value, so three
    pages carried three efficiencies for one run.  `P_shaft` / `eta_shaft`
    are None without a bearing model — an unknown friction is not a zero.
    """
    P = _numf(_g(em, "P_mech_W"))
    k = _numf(_g(em, "end3d.k_flux"))
    loss = _numf(_g(em, "P_loss_total_W"))
    x = _numf((brg or {}).get("P_mech_extra_W")) if (brg and brg.get("has_bearings", True)) else None
    gen = str(mode or _g(em, "op_mode") or "").lower().startswith("gen")
    out: Dict[str, Optional[float]] = {"k": k, "P_rotor_W": None, "P_shaft_W": None,
                                       "P_elec_W": None, "eta_em": None,
                                       "eta_shaft": None, "gen": gen}
    if P is None:
        return out
    pr = abs(P) * (k if k else 1.0)
    out["P_rotor_W"] = pr
    if loss is not None:
        pe = max(0.0, pr - loss) if gen else pr + loss
        out["P_elec_W"] = pe
        out["eta_em"] = ((pe / pr) if gen else (pr / pe)) if pr > 0 and pe > 0 else None
    if x is not None:
        ps = (pr + x) if gen else max(0.0, pr - x)
        out["P_shaft_W"] = ps
        pe = out["P_elec_W"]
        if pe is not None:
            out["eta_shaft"] = ((pe / ps) if gen else (ps / pe)) if ps > 0 and pe > 0 else None
    return out


def _decap(s: str) -> str:
    """Sentence-case a quantity for mid-sentence use, KEEPING its symbols.

    CS-5 (reviewer 2026-09-14): ``.lower()`` on the whole string printed
    "irreversible demagnetisation (br lost)" on page 1 — Br is a symbol, not a
    word.  Only the leading capital is dropped, and only when the word it opens
    is not an acronym.
    """
    s = str(s or "")
    if len(s) > 1 and s[0].isupper() and not s[1].isupper():
        return s[0].lower() + s[1:]
    return s


#: THE VERDICT ROW IS GONE (user 2026-09-14, restated 2026-09-15).  Page 1
#: carries the machine's numbers — torque, speed, shaft power, shaft
#: efficiency, mass, and the supply they belong to — and the limits are read in
#: the warnings section, which is untouched.  `headline_verdict_row` used to sit
#: here unused "for whoever wants it back", and a test that pinned its wording
#: kept it alive; both are removed, so the row cannot come back by accident.
#: :func:`warnings_headline` is the one place that counts reds and ambers.
HEADLINE_HAS_NO_VERDICT_ROW = True


def headline_rows(role: str, d_duty: Dict[str, Any], em: Dict[str, Any],
                  brg: Optional[Dict[str, Any]],
                  cols: Optional[List[Dict[str, Any]]] = None,
                  ctxs: Optional[Dict[str, Any]] = None,
                  supply: Optional[str] = None) -> List[List[str]]:
    """The four numbers a reader looks at first.  Header row included.

    Shaft power is the 2-D torque times the 3-D end-effect factor with the
    mechanical losses taken off, and the efficiency row RENAMES itself when this
    configuration names no bearings — an "efficiency" that quietly excludes
    friction must never wear the same label as one that includes it.
    """
    T2d = _g(em, "T_em_avg_Nm") or _g(d_duty, "torque_nm")
    k3d = _g(em, "end3d.k_flux")
    P_mech = _g(em, "P_mech_W")
    if P_mech is None and T2d and _g(d_duty, "rpm"):
        P_mech = abs(float(T2d)) * 2 * math.pi * float(_g(d_duty, "rpm")) / 60.0
    gen = str(d_duty.get("mode") or role).lower().startswith("gen")
    # ONE power balance for the whole document (`shaft_view`): the card's and
    # the catalog's numbers, generator-aware.
    sv = shaft_view({**em, "P_mech_W": P_mech} if P_mech is not None else em,
                    brg, "generator" if gen else "motor")
    P_shaft = sv["P_shaft_W"] if sv["P_shaft_W"] is not None else sv["P_rotor_W"]

    eta_em = _g(em, "efficiency")
    eta_em = float(eta_em) * 100.0 if (eta_em is not None and float(eta_em) <= 1.5) \
        else (float(eta_em) if eta_em is not None else None)
    if eta_em is None:
        eta_em = _g(d_duty, "result.efficiency_pct")
    if sv["eta_em"] is not None:
        eta_em = 100.0 * sv["eta_em"]
    eta_row = ("Shaft efficiency", None, "")
    if brg and sv["eta_shaft"] is not None:
        eta_row = ("Shaft efficiency", 100.0 * sv["eta_shaft"],
                   ("electrical output over rotor power plus bearings and "
                    "windage (SKF model)" if gen else
                    "bearings + windage taken off the shaft (SKF model)"))
    else:
        eta_row = ("Electromagnetic efficiency", eta_em,
                   "bearing and windage losses are NOT included — this "
                   "configuration names no bearings")

    # ONE MASS (user 2026-09-10: "масса у нас только одна").
    #
    # It is the TOTAL, and not by preference: `mass_total_kg` is already the
    # divisor of every N·m/kg and kW/kg in this project (Compare's TD tile, the
    # optimizer's Pareto, the datasheet's mass row), so printing anything else
    # beside those numbers would not divide into them.  It is the whole machine
    # the mass model knows: stator and rotor iron, copper, magnets, the band and
    # the shaft.  `mass_active_kg` stays in the payload for whoever needs the
    # electromagnetic subset; it is no longer a headline, because two masses
    # three per cent apart taught the reader nothing and cost them a decision
    # about which one the torque density used.
    m_tot = _g(em, "mass_total_kg") or _g(d_duty, "result.mass_kg")

    headline = [
        ["", "", ""],
        ["Torque at the rotor",
         _fmt(float(T2d) * float(k3d) if (T2d and k3d) else T2d, 2, "N·m"),
         ("2-D average %s × the 3-D end-effect factor %s"
          % (_fmt(T2d, 2, "N·m"), _fmt(k3d, 4)) if (T2d and k3d) else
          "2-D electromagnetic average over one electrical period; no 3-D "
          "passport, so no end-effect correction")],
        # THE SPEED THE THREE NUMBERS BELOW BELONG TO (user 2026-09-14).  The
        # headline table has ONE value column, so this is the pictures duty's
        # speed and the note names it — a torque read at an unstated rpm is a
        # torque the reader can pair with the wrong power.
        ["Speed", _fmt(_g(em, "rpm") or _g(d_duty, "rpm"), 0, "rpm"),
         "the speed every number in this table belongs to"
         + (" (duty '%s')" % d_duty.get("name") if d_duty.get("name") else "")],
        ["Shaft power", _fmt((P_shaft or 0) / 1000.0 if P_shaft else None, 2, "kW"),
         ("2-D × the 3-D end-effect factor k = %.4f" % float(k3d)
          if k3d else "2-D; no 3-D passport for this machine, k = 1 assumed")
         + ((", plus bearings and windage — what the prime mover must supply"
             if gen else ", minus bearings and windage")
            if (brg and sv["P_shaft_W"] is not None) else "")],
        [eta_row[0], _fmt(eta_row[1], 2, "%"), eta_row[2]],
        ["Mass", _fmt(m_tot, 3, "kg"),
         "iron, copper, magnets, band and shaft — what every N·m/kg divides by"],
    ]
    # WHAT FEEDS IT (2026-09-14).  Every number above belongs to a supply, and
    # until tonight the document only said so in section 5.  A duty whose
    # coupled answer was reached on the inverter is a PWM machine on every page,
    # and page 1 is where that has to be said first.
    if supply:
        headline.append([
            "Supply", supply,
            ("the two-level bridge this duty was solved on — its losses, "
             "temperatures and efficiency are this document's; section 5 is "
             "what the carrier costs against the sinusoid"
             if supply != SUPPLY_SINE else
             "an ideal sinusoidal current supply — no carrier, no switching "
             "harmonics")])
    headline[0] = ["Headline", "Value", "What it is"]
    # …and when the OTHER duties of this build disagree about that mass, the
    # cover says so rather than letting page 3 be the first hint (2026-09-14).
    _mc = mass_consistency(cols or [])
    if _mc:
        headline.append(["%s Mass across the duties" % FLAG,
                         " / ".join(_fmt(m, 3) for m in _mc["masses"]) + " kg",
                         _mc["text"]])
    # NO VERDICT ROW HERE (user 2026-09-14, again 2026-09-15) — see
    # `HEADLINE_HAS_NO_VERDICT_ROW`.
    return headline


def _cover(st, die, cfg, role, geo, mats, d_duty, em, em_src, brg,
           sources, delta, batt: Optional[Dict[str, Any]] = None,
           used_live: bool = False, supply: Optional[str] = None,
           sec: Optional[Dict[str, int]] = None,
           cols: Optional[List[Dict[str, Any]]] = None,
           ctxs: Optional[Dict[str, Any]] = None) -> List[Any]:
    from reportlab.platypus import Spacer

    out: List[Any] = [
        _para(f"{die} · {cfg}", st["title"]),
        _para(machine_subtitle(role, geo, mats), st["sub"]),
        _para(
            f"Duty <b>{d_duty.get('name') or '—'}</b> · every number below was "
            f"read from a stored solver result; nothing was solved to produce "
            f"this document.",
            st["sub"]),
        Spacer(1, 6),
        # WHO OWNS THIS, before anything else on the page (2026-09-10).
        _para(f'<font color="{WARN}"><b>{CONFIDENTIAL_NOTICE}</b></font>',
              st["body"]),
        Spacer(1, 12),
    ]
    out.append(_table(
        [[r[0], r[1], _para(str(r[2]), st["cell"])]
         for r in headline_rows(role, d_duty, em, brg, cols, ctxs, supply)],
        [140, 95, CONTENT_W - 235], header=True, size=9.4))
    out.append(_para(f"Operating point taken from {em_src or '— nothing solved yet'}.",
                         st["note"]))
    out.append(Spacer(1, 12))

    # ── the pack, and the voltage limit that comes off it ───────────────────
    out.append(_para("Battery and the voltage limit", st["h1"]))
    _brows = battery_rows(batt)
    if len(_brows) > 1:
        # THE "what it is" COLUMN WRAPS (reviewer 2026-09-14, A5).  A plain
        # string in a reportlab cell is not wrapped, and the two longest
        # descriptions — "Pack, minimum" and "Pack, maximum" — ran back across
        # the value and label columns, so the pack minimum, which is the limit
        # half this report is judged against, was unreadable in the PDF.
        out.append(_table([[r[0], r[1], _para(str(r[2]), st["cell"])]
                           for r in _brows],
                          [140, 95, CONTENT_W - 235], header=True, size=8.8))
        out.append(_para(battery_note(cols), st["note"]))
        _above = dc_link_above_nominal_text(cols, batt)
        if _above:
            out.append(_para(_above, st["note"]))
    else:
        out.append(_para(BATTERY_NONE, st["note"]))
    out.append(Spacer(1, 10))

    # ── what the symbols mean ───────────────────────────────────────────────
    out.append(_para("What the symbols mean", st["h1"]))
    # "k_3d (this machine: 0.9576)" needs its own column back: at 110 pt it ran
    # into the description with no gap (reviewer 2026-09-14, BL-1).
    out.append(_table([[r[0], _para(r[1], st["cell"])] for r in
                       glossary_rows(em, cols)],
                      [132, CONTENT_W - 132], header=True, size=8.8))
    out.append(Spacer(1, 6))
    _src_note = cover_source_note(used_live)
    if _src_note:
        out.append(_para(_src_note, st["note"]))
    out.append(Spacer(1, 10))
    out.append(_para(contents_line(sec), st["note"]))
    return out


# ── page 2 · machine ────────────────────────────────────────────────────────


def _machine_page(st, die_doc, geo, wind, mats, slot, brg_assign, brg,
                  em, ctxs: Optional[Dict[str, Any]] = None,
                  report_tag: str = "",
                  figs: Optional[List[int]] = None,
                  sec: Optional[Dict[str, int]] = None) -> List[Any]:
    from reportlab.platypus import Spacer

    out: List[Any] = [_para(section_heading(sec, "machine"), st["h1"])]

    img = None
    try:
        from motor_ai_sim.datasheet import _render_cross_section
        # FULL CONTENT WIDTH (user 2026-09-14: every figure the same size, the
        # whole width of the text).  At 200 pt the coils were a smudge.
        img = _image(_render_cross_section(thumb_svg_for(die_doc), px=1600),
                     CONTENT_W, max_height=PAIR_MAX_H)
    except Exception:                                       # noqa: BLE001
        img = None
    if img is not None:
        # The picture and its caption travel together, like every other figure
        # in this document (reviewer 2026-09-14, B11).
        from reportlab.platypus import KeepTogether as _KTc
        img.hAlign = "LEFT"
        out.append(_KTc([img, _para(
            fig_label(figs, DIE_SECTION_CAPTION), st["note"])]))
        out.append(Spacer(1, 6))

    out.append(_para("Geometry", st["h2"]))
    rows = geometry_rows(geo, wind, slot, em)
    w = CONTENT_W / 2.0
    out.append(_table(rows, [w * 0.52, w * 0.30, w * 0.18,
                             w * 0.52, w * 0.30, w * 0.18], size=8.8))

    out.append(_para("Materials", st["h2"]))
    mrows = material_rows(mats, em, geo, sec)
    out.append(_table([[r[0], r[1], _para(r[2], st["cell"])] for r in mrows],
                      [110, 150, CONTENT_W - 260], header=True, size=8.8))
    _mrows = mass_rows(em)
    if len(_mrows) > 2:
        out.append(_para("Masses", st["h2"]))
        # THE TABLE FULL WIDTH AND THE PIE UNDER IT, AT FULL WIDTH TOO (user
        # 2026-09-14).  Beside the table the pie was 40 % of the column and its
        # legend took most of that.
        _mpie = _image(_mass_inertia_png(em, width_cm=MAP_FULL_CM,
                                         which="mass"),
                       CONTENT_W, max_height=PAIR_MAX_H)
        _mtab = _table([[r[0], r[1], r[2], r[3], r[4]] for r in _mrows],
                       [CONTENT_W * 0.30, CONTENT_W * 0.18, CONTENT_W * 0.18,
                        CONTENT_W * 0.18, CONTENT_W * 0.16],
                       header=True, size=8.4)
        out.append(_mtab)
        if _mpie is not None:
            from reportlab.platypus import KeepTogether as _KTm
            out.append(_KTm([_mpie, _para(
                fig_label(figs, MASS_PIE_CAPTION, report_tag or ""),
                st["note"])]))
        _mnote = mass_total_note(em)
        if _mnote:
            out.append(_para(_mnote, st["note"]))
        out.append(_para(MASS_TABLE_NOTE, st["note"]))

    out.append(_para("Lamination and segmentation", st["h2"]))
    out.append(_table(lamination_rows(mats, em),
                      [150, (CONTENT_W - 150) / 2, (CONTENT_W - 150) / 2],
                      header=True, size=8.8))
    out.append(_para(lamination_text(mats, em), st["body"]))
    out.append(_para(magnet_text(mats, em, ctxs or {}), st["body"]))
    out.append(_para(insulation_text(mats, ctxs), st["body"]))

    out.append(_para("Bearings", st["h2"]))
    if brg and brg.get("has_bearings"):
        # TEN WIDTHS THAT FIT THE PAGE (reviewer 2026-09-14, BL-1): the last
        # one used to be CONTENT_W − 556, i.e. NEGATIVE, so the Loss column —
        # the 174.4 W a bearing costs — was drawn off the edge of the table.
        out.append(_table(bearing_rows(brg),
                          [40, 89, 62, 48, 76, 40, 40, 40, 52, 40],
                          header=True, size=7.6))
        out.append(_para(bearing_note_text(brg), st["note"]))
    else:
        out.append(_para(NO_BEARINGS_TEXT_PDF, st["body"]))
    return out


#: The "no bearings" sentence.  The PDF version escapes the arrow and the
#: ampersand for reportlab's mini-markup; the plain one is what Word gets.
NO_BEARINGS_TEXT = (
    "No bearings assigned to this configuration — the mechanical loss is "
    "UNKNOWN, not zero. Assign a pair in Mechanical -> Shaft & bearings and the "
    "report will carry the SKF friction model and the shaft efficiency.")
NO_BEARINGS_TEXT_PDF = NO_BEARINGS_TEXT.replace("&", "&amp;").replace("->", "-&gt;")


def _shaft_words(geo: Dict[str, Any]) -> str:
    r_in = _numf(geo.get("rotor_inner_radius"))
    if r_in is None:
        d = _numf(geo.get("shaft_diameter"))
        return _fmt(d, 2) if d is not None else "—"
    wall = _numf(geo.get("shaft_height"))
    seat = 2.0 * r_in
    if wall is None or wall <= 0 or wall >= r_in:
        return "%s / solid" % _fmt(seat, 1)
    return "%s / %s" % (_fmt(seat, 1), _fmt(2.0 * (r_in - wall), 1))


def _sd_words(wind: Optional[Dict[str, Any]], em: Optional[Dict[str, Any]] = None,
              duty: Optional[Dict[str, Any]] = None) -> str:
    """'Δ delta' | 'Y star' | '—': the duty's own connection, then the run's,
    then the configuration's winding."""
    v = ((duty or {}).get("star_delta") or _g(em or {}, "star_delta")
         or (wind or {}).get("star_delta"))
    if not v:
        return "—"
    return "Δ delta" if str(v).lower().startswith("d") else "Y star"


def _layers_words(wind: Dict[str, Any]) -> str:
    """`layers` is the winding's layer count, and the number alone is read as
    anything from strand layers to lamination stacks — say what it selects."""
    n = _numf(wind.get("layers"))
    if n is None:
        return "—"
    # The number and the word only.  The old gloss ("coils on alternate
    # teeth" / "every tooth wound") described a topology this field does not
    # select — the Ø200 winds every one of its 12 teeth and printed
    # "alternate teeth" (user 2026-09-13: "выкинь это из отчёта").
    if int(n) <= 1:
        return "1 — single-layer"
    return "%s — double-layer" % _fmt(n, 0)


def geometry_rows(geo: Dict[str, Any], wind: Dict[str, Any],
                  slot: Optional[Dict[str, float]],
                  em: Dict[str, Any]) -> List[List[str]]:
    """The machine's dimensions and its winding, two label/value pairs a row.

    The slot fill is the CAD's measured one when the cross-section could be
    built, and the run's own figure otherwise — never a nominal.
    """
    def _f(k, d=2):
        return _fmt(geo.get(k), d)

    return [
        ["Stator outer diameter", _f("stator_diameter"), "mm",
         "Slots / poles", "%d / %d" % _slots_poles(geo), ""],
        # HOW THE SLOT IS ACTUALLY WOUND (user 2026-09-10: "не нашёл нигде, что
        # витков 6 по 4 параллельных провода в каждом").  The turn count alone
        # does not describe the winding: each turn is `wire_parallel` strands in
        # hand, and `wire_split` cuts each strand into that many narrower strips
        # in SERIES.  All three are drawn and meshed, so they belong on the
        # geometry page rather than in a note somewhere.
        ["Stack length", _f("motor_length"), "mm",
         "Turns per slot", winding_words(geo), ""],
        ["Air gap", _f("air_gap", 3), "mm",
         "Wire (one strand)", f"{_f('wire_width', 2)} × {_f('wire_height', 2)}",
         "mm"],
        ["Magnet height", _f("magnet_height", 2), "mm",
         "Parallel paths", _fmt(wind.get("n_parallel"), 0), ""],
        ["Sleeve thickness", _f("sleeve_thickness", 2) if geo.get("sleeve_thickness")
         else "none", "mm" if geo.get("sleeve_thickness") else "",
         "Series coils", _fmt(wind.get("n_series"), 0), ""],
        ["Rotor outer radius", _f("rotor_outer_radius", 2), "mm",
         # coil grouping AND the terminal connection (user 2026-09-13: "нужно
         # добавить соединение в отчёт") — "2P · Δ delta"
         "Connection", "%s · %s" % (str(wind.get("connection") or "—"),
                                    _sd_words(wind, em)), ""],
        # THE SHAFT, from the keys this geometry actually has (2026-09-11).
        # `shaft_diameter` does not exist here, and `rotor_hole` is the magnet
        # POCKET's shape switch (>= 1: straight sides to the rotor OD) — the row
        # printed it as a 1 mm bore.  The shaft is `rotor_inner_radius` (its seat
        # under the rotor iron) and `shaft_height` (the wall of a hollow shaft),
        # so seat Ø = 2 r_in and bore Ø = 2 (r_in − wall); a solid shaft has no
        # bore and says so.
        ["Shaft seat Ø / bore Ø", _shaft_words(geo), "mm",
         "Winding layers", _layers_words(wind), ""],
        ["Stator yoke (core thickness)", _f("core_thickness", 2), "mm",
         "Slot fill", (_fmt(100.0 * float(slot["fill"]), 1, "%")
                       if slot and slot.get("fill") else
                       _fmt(_g(em, "slot_fill_pct"), 1, "%")), ""],
    ]


def _magnet_lamination_words(em: Optional[Dict[str, Any]]) -> str:
    """How the magnet is sliced, as a clause for the materials row.

    Empty when the run stored no segmentation block — an older run, or a solve
    that genuinely modelled a solid magnet, and the two are told apart by the
    presence of the block rather than by guessing.
    """
    seg = _g(em or {}, "magnet_segmentation")
    if not isinstance(seg, dict) or seg.get("factor") is None:
        return ("; SOLID — no lamination modelled, so the magnet loss is the "
                "whole-block figure" if em else "")
    f = _numf(seg.get("factor"))
    # `n_bodies` is the number of MAGNET BODIES in the modelled section (the
    # poles), not the slice count — printing it as "5 slices of 5 mm along
    # the 180 mm stack" was the reviewer's item 4 (2026-09-13).  The slices
    # are stack / slice.
    n_sl = _n_slices(seg)
    return ("; LAMINATED axially into %s slices of %s along the %s stack, "
            "leaving %s of a solid magnet's loss"
            % (_fmt(n_sl, 0) if n_sl else "—",
               _fmt(seg.get("slice_mm"), 1, "mm"),
               _fmt(seg.get("stack_mm"), 0, "mm"),
               _fmt(100.0 * f, 1, "%") if f is not None else "—"))


def _n_slices(seg: Dict[str, Any]) -> Optional[int]:
    """stack / slice, rounded — the number of axial pieces one magnet is cut into."""
    try:
        s, L = float(seg.get("slice_mm") or 0), float(seg.get("stack_mm") or 0)
        return int(round(L / s)) if s > 0 and L > 0 else None
    except (TypeError, ValueError):
        return None


def material_rows(mats: Dict[str, Any],
                  em: Optional[Dict[str, Any]] = None,
                  geo: Optional[Dict[str, Any]] = None,
                  sec: Optional[Dict[str, int]] = None) -> List[List[str]]:
    """Which card every part is made of.  Header row included.

    The magnet row also says how the magnet is CUT (user 2026-09-11).  Its
    segmentation is a factor of ~50 on the magnet loss on this machine, so a
    materials table that names the grade and says nothing about the slicing
    describes half the part.  The slot-insulation row carries the THICKNESS
    the geometry draws it at (``insulation_thickness``), not the word
    "liner" (user 2026-09-13).
    """
    _ins_t = _numf((geo or {}).get("insulation_thickness")) if geo else None
    _ins_note = ("%s thick, as drawn in the slot" % _fmt(_ins_t, 2, "mm")
                 if _ins_t is not None else "")
    mag = str(mats.get("magnet") or "—")
    mag_t = None
    try:
        import re as _re
        m = _re.search(r"(\d{2,3})\s*C\b", mag)
        mag_t = float(m.group(1)) if m else None
    except Exception:                                       # noqa: BLE001
        mag_t = None
    mrows = [["Part", "Card", "Note"]]
    for label, key, note in (
            ("Stator core", "stator_core", "laminated, P(B,f) surface from the card"),
            ("Rotor core", "rotor_core", ""),
            ("Magnet", "magnet",
             ((f"card measured at {mag_t:g} °C" if mag_t else
               "temperature as the library quotes it")
              + _magnet_lamination_words(em))),
            ("Slot insulation", "slot_insulation", _ins_note),
            ("Wire insulation", "wire_insulation", "enamel"),
            ("Insulation class", "__class__", ""),
            ("Retaining sleeve", "sleeve",
             "hoop-wound carbon fibre" if mats.get("sleeve") else ""),
            ("Shaft", "shaft", ""),
    ):
        # THE INSULATION IS ALWAYS ON THE PAGE (user 2026-09-11: "не нашёл ни
        # одного слова по поводу изоляции — нужно это обязательно написать и в
        # материалах отметить").  It used to be dropped whenever it was not
        # assigned, which is every machine: the library's enamel and liner
        # entries are thermal-property cards and nobody picks one, so the two
        # rows never appeared — while the winding's whole temperature limit
        # rests on them.  Unassigned now prints the build the limit ASSUMES,
        # marked as an assumption rather than passed off as a choice.
        if key == "__class__":
            mrows.append([label, PROJECT_INSULATION_LABEL,
                "ASSUMED: no card carries a rating. Every winding and hot-spot "
                "check is made against it"])
            continue
        v = mats.get(key)
        if key in ("slot_insulation", "wire_insulation") and not v:
            mrows.append([label,
                          "%s %s" % (INSULATION_ASSUMED[key], FLAG),
                          "ASSUMED — %s; nothing is assigned on this machine"
                          % note])
            continue
        # "Al2O3" read as "AI203" on a printed page (reviewer 2026-09-13): the
        # card's name stays, the note says what it is.
        if key == "slot_insulation" and str(v or "").strip().lower() == "al2o3":
            note = ("alumina ceramic — " + note) if note else "alumina ceramic"
        if v or key in ("stator_core", "rotor_core", "magnet"):
            mrows.append([label, str(v or "— not assigned"), note])
    # The cold card the runaway check borrows, LISTED (reviewer 2026-09-13,
    # item 6: "N52UH_20C is not in the materials table") — as a reference, not
    # a part of the machine.
    _cc = _cold_br_card(mag)
    if _cc is not None:
        for i, r in enumerate(mrows):
            if r[0] == "Magnet":
                mrows.insert(i + 1, [
                    "Magnet, cold reference", _cc[0],
                    "room-temperature card of the same grade (Br %g T against "
                    "%g T), used only for the runaway check in %s"
                    % (_cc[1], _cc[2], sec_ref(sec, "warnings"))])
                break
    return mrows


#: What the report assumes the winding is built with when nothing is assigned —
#: the project's standard build, and the build `_insulation_limit` rates.
INSULATION_ASSUMED = {"wire_insulation": "polyimide enamel",
                      "slot_insulation": "Nomex liner"}


def magnet_text(mats: Dict[str, Any], em: Dict[str, Any],
                ctxs: Dict[str, Any]) -> str:
    """At what temperature the magnets were taken, and where that came from.

    User 2026-09-11: *"надо также упомянуть, что для расчётов использовались
    магниты при температуре 150 °C, если этого ещё нет"*.  The operating-point
    table carries the number, but nothing said that the CARD is a 150 °C card
    and that the solve walks it to the run's own magnet temperature — two
    different temperatures, and the difference is a real Br.
    """
    grade = str((mats or {}).get("magnet") or "").strip()
    if not grade:
        return ("No magnet is assigned to this machine, so nothing below is a "
                "magnet calculation.")
    card_t = _magnet_card_temp(grade)
    # The RUN's own magnet temperature, wherever it was filed: a coupled run
    # keeps it under `coupling.magnet_temp_c` and the paragraph used to read
    # only `magnet_temp_C`, so on every coupled duty it said nothing about the
    # walk at all (reviewer 2026-09-14, C8).
    run_t = _duty_magnet_temp(em)
    out = "The magnets are %s" % grade
    out += (", a card measured at %s" % _fmt(card_t, 0, "°C") if card_t is not None
            else ", whose card does not state the temperature it was measured at")
    # WHICH Br THE SOLVE ACTUALLY USED (reviewer 2026-09-14, C8).  The
    # paragraph named the card and the run's temperature and left the reader to
    # do the walk; the KV row a page later does it out loud, and so does this
    # now — card grade, card temperature, solved temperature, Br used.
    _a, _t_ref = _magnet_alpha_br(grade)
    _br_card = None
    try:
        from motor_ai_sim.materials import get_material
        _br_card = _numf(getattr(get_material("magnet", grade), "Br", None))
    except Exception:                                       # noqa: BLE001
        _br_card = None
    _t_ref = _t_ref if _t_ref is not None else card_t
    _br_used = None
    if _br_card is not None and _a is not None and _t_ref is not None \
            and run_t is not None:
        _br_used = _br_card * (1.0 + float(_a) / 100.0
                               * (float(run_t) - float(_t_ref)))
    if run_t is not None and card_t is not None and abs(run_t - float(card_t)) > 0.5:
        out += (". The card is walked to the run's own magnet temperature "
                "through its reversible coefficients, so the field was "
                "computed with Br and HcJ at %s" % _fmt(run_t, 1, "°C"))
        if _br_used is not None:
            out += (" — Br %s at the card's %s, %g %%/K, %s at the solved %s"
                    % (_fmt(_br_card, 4, "T"), _fmt(_t_ref, 0, "°C"), float(_a),
                       _fmt(_br_used, 4, "T"), _fmt(run_t, 1, "°C")))
    elif run_t is not None:
        out += (". The run's magnets sat at %s, the card's own temperature"
                % _fmt(run_t, 1, "°C"))
        if _br_card is not None:
            out += ", so the field was computed with the card's own Br %s" % \
                _fmt(_br_card, 4, "T")
    out += ". "
    hots = [(_numf((c or {}).get("magnet_temp_c")), d)
            for d, c in (ctxs or {}).items()]
    hots = [(v, d) for v, d in hots if v is not None]
    if hots:
        v, d = max(hots)
        lim = next((_numf(c.get("magnet_limit_c")) for c in (ctxs or {}).values()
                    if (c or {}).get("magnet_limit_c") is not None), None)
        out += ("Across this configuration the magnets run hottest at %s on "
                "'%s'%s. " % (_fmt(v, 1, "°C"), d,
                              "" if lim is None else
                              (", against the grade's %s" % _fmt(lim, 0, "°C"))))
    return out.strip()


def _steel_card(name: Any) -> Any:
    """The assigned steel's card, or ``None`` — never raises."""
    try:
        from motor_ai_sim.materials import get_material
        return get_material("steel", str(name)) if name else None
    except Exception:                                       # noqa: BLE001
        return None


def mass_rows(em: Dict[str, Any]) -> List[List[str]]:
    """Every part of the machine and what it weighs.  Header row included.

    The cover carries one mass; this is what it is made of (user 2026-09-11).
    Straight off the run's own `mass_components`, which the mass model builds
    from the CAD sections and the assigned cards — so the density beside a row
    is the card's, and the total is the number every N·m/kg divides by.
    """
    comps = _g(em, "mass_components") or []
    rows = [["Part", "Material", "Volume", "Mass", "Share"]]
    tot = 0.0
    for c in comps:
        if isinstance(c, dict):
            v = _numf(c.get("mass_kg") or c.get("mass_modelled_kg"))
            if v:
                tot += float(v)
    for c in comps:
        if not isinstance(c, dict):
            continue
        v = _numf(c.get("mass_kg") or c.get("mass_modelled_kg"))
        if not v:
            continue
        rows.append([
            str(c.get("name") or "—"),
            str(c.get("material") or ""),
            _fmt(c.get("volume_cm3"), 1, "cm³"),
            _fmt(v, 3, "kg"),
            _fmt(100.0 * float(v) / tot, 1, "%") if tot else ""])
    if len(rows) > 1:
        # THE SUMMARY'S OWN TOTAL, not the sum of the rounded rows above
        # (reviewer 2026-09-14: 27.458 kg here against 27.457 on the cover and
        # in the comparison table).  `mass_total_kg` is the number every N·m/kg
        # divides by, so it is the one that has to appear wherever a mass is
        # printed; the components are the same mass split up, and a gram of
        # rounding between them is not a second answer.
        rows.append(["TOTAL", "", "", _fmt(mass_table_total(em, tot), 3, "kg"), ""])
    return rows


def mass_table_total(em: Dict[str, Any], parts_sum: Optional[float] = None
                     ) -> Optional[float]:
    """The kilograms the Masses table totals to: the summary's own figure."""
    if parts_sum is None:
        parts_sum = 0.0
        for c in (_g(em, "mass_components") or []):
            if isinstance(c, dict):
                v = _numf(c.get("mass_kg") or c.get("mass_modelled_kg"))
                if v:
                    parts_sum += float(v)
    mt = _numf(_g(em, "mass_total_kg"))
    return mt if mt is not None else (parts_sum or None)


#: A gram.  Below this the component sum and the summary's total are the same
#: number rounded twice and nothing is said about it.
MASS_SUM_TOL_KG = 0.001


def mass_total_note(em: Dict[str, Any]) -> str:
    """"" unless the parts and the total really disagree — then, by how much."""
    parts = 0.0
    for c in (_g(em, "mass_components") or []):
        if isinstance(c, dict):
            v = _numf(c.get("mass_kg") or c.get("mass_modelled_kg"))
            if v:
                parts += float(v)
    mt = _numf(_g(em, "mass_total_kg"))
    if mt is None or not parts or abs(mt - parts) <= MASS_SUM_TOL_KG:
        return ""
    return ("The TOTAL is the run's own mass_total_kg; the parts above add up "
            "to %s — %s apart, which is a part carried as reference or "
            "excluded." % (_fmt(parts, 3, "kg"), _fmt(abs(mt - parts), 3, "kg")))


MASS_TABLE_NOTE = (
    "Volumes are the CAD sections times the stack, densities the assigned "
    "cards'. A part marked reference or excluded is in this table but not in "
    "the total.")


def lamination_rows(mats: Dict[str, Any], em: Dict[str, Any]) -> List[List[str]]:
    """How the iron is laminated and how the magnets are cut.  Header included.

    User 2026-09-11: *"не нашёл в отчёте про ламинацию магнитов и её величину;
    проверь ещё про ламинацию статора и ротора и что они сделаны из одного и
    того же материала"*.  All three facts were in the solve and in none of the
    pages: the stacking factor scales the iron a flux path actually has, the
    sheet thickness is what sets the eddy term of the loss model, and the
    magnet slicing is a factor of ~50 on the magnet loss.
    """
    rows = [["", "Stator", "Rotor"]]
    st_n, rt_n = mats.get("stator_core"), mats.get("rotor_core")
    st, rt = _steel_card(st_n), _steel_card(rt_n)
    rows.append(["Steel", str(st_n or "— not assigned"),
                 str(rt_n or "— not assigned")])
    rows.append(["Stacking factor k_f",
                 _fmt(getattr(st, "stacking_factor", None), 3),
                 _fmt(getattr(rt, "stacking_factor", None), 3)])
    rows.append(["Sheet thickness",
                 _fmt(getattr(st, "thickness_mm", None), 2, "mm"),
                 _fmt(getattr(rt, "thickness_mm", None), 2, "mm")])
    return rows


def lamination_text(mats: Dict[str, Any], em: Dict[str, Any]) -> str:
    """The same three facts as a sentence, with the verdict on the two steels."""
    st_n, rt_n = mats.get("stator_core"), mats.get("rotor_core")
    st = _steel_card(st_n)
    kf = getattr(st, "stacking_factor", None)
    th = getattr(st, "thickness_mm", None)
    same = bool(st_n) and str(st_n) == str(rt_n)
    out = ""
    if same:
        out += ("Stator and rotor are the SAME steel, %s. " % st_n)
    elif st_n and rt_n:
        out += ("Stator and rotor are DIFFERENT steels — %s against %s, each "
                "with its own B(H) and loss surface. " % (st_n, rt_n))
    if kf:
        out += ("Stacking factor k_f = %s — %s %% of the axial length is iron, "
                "applied to the magnetic model%s. "
                % (_fmt(kf, 3), _fmt(100.0 * float(kf), 1),
                   ", sheet %s thick" % _fmt(th, 2, "mm")
                   if th else ""))
    seg = _g(em, "magnet_segmentation")
    if isinstance(seg, dict) and seg.get("factor") is not None:
        f = _numf(seg.get("factor"))
        out += ("The magnets are SEGMENTED axially: %s slices of %s along the "
                "%s stack (%s pole bodies in the modelled section, each %s "
                "wide), leaving %s of a solid magnet's loss (×%s). %s"
                % (_fmt(_n_slices(seg), 0) if _n_slices(seg) else "—",
                   _fmt(seg.get("slice_mm"), 1, "mm"),
                   _fmt(seg.get("stack_mm"), 0, "mm"),
                   _fmt(seg.get("n_bodies"), 0),
                   _fmt(seg.get("width_mm"), 1, "mm"),
                   _fmt(100.0 * f, 1, "%") if f is not None else "—",
                   _fmt(f, 4) if f is not None else "—",
                   str(seg.get("model") or "")))
    else:
        out += ("The magnets are solid — no axial segmentation is modelled, so "
                "the magnet loss is the whole-block figure.")
    return out.strip()


def insulation_text(mats: Dict[str, Any], ctxs: Dict[str, Any]) -> str:
    """The winding insulation in one paragraph: what it is, what it is rated
    for, and how close this machine's duties come to it.

    User 2026-09-11.  The class was already the limit behind two warnings and a
    row in the limits table, but nothing in the document SAID so — a reader
    looking for "what insulation is this wound with" found nothing.
    """
    lim, note = _insulation_limit(mats)
    en = str((mats or {}).get("wire_insulation") or "")
    li = str((mats or {}).get("slot_insulation") or "")
    built = ((en or INSULATION_ASSUMED["wire_insulation"]) + " on the wire, "
             + (li or INSULATION_ASSUMED["slot_insulation"]) + " in the slot")
    # EVERY PART AGAINST ITS OWN LIMIT (user 2026-09-14).  The hottest thing in
    # this machine is the MAGNETS at 169.3 °C, and the sentence measured them
    # against the WINDING's 200 °C insulation class — "15 % of it left" for a
    # part whose own limit is 180 °C and which therefore has 5.9 %.  A margin is
    # only a margin against the limit of the part it belongs to, so the part is
    # named, its own limit is printed, and the winding is quoted beside it.
    # (temperature, named-part priority, duty, what, its own limit).  A NAMED
    # part outranks the bare hot spot at the same temperature — on this machine
    # the 169.3 °C hot spot IS the magnets, and "the hottest point in the
    # machine" would send the reader back to the insulation class it is not
    # measured against.  The hot spot keeps its place for the case where the
    # hottest thing is not a part with a card: it is then quoted against the
    # insulation limit, which is the only limit there is for it.
    hot: List[Tuple[float, int, str, str, float]] = []
    for duty, ctx in (ctxs or {}).items():
        for key, prio, what, lim_key in (
                ("winding_temp_c", 1, "the winding", "winding_limit_c"),
                ("magnet_temp_c", 1, "the magnets", "magnet_limit_c"),
                ("hot_spot_c", 0, "the hottest point in the machine",
                 "winding_limit_c")):
            v = _numf((ctx or {}).get(key))
            L = _numf((ctx or {}).get(lim_key)) or (
                lim if lim_key == "winding_limit_c" else None)
            if v is not None and L:
                hot.append((v, prio, duty, what, L))
    out = ("The winding is taken as %s — wound with %s. %s. "
           % (PROJECT_INSULATION_TEXT, built,
              "That rating is an ASSUMPTION: no card in the library carries a "
              "temperature rating"))
    if hot:
        v, _prio, duty, what, L = max(hot)
        _whose = {"the magnets": "their own", "the winding": "its own",
                  }.get(what, "the insulation's")
        _m = (L - v) / L * 100.0
        out += ("The hottest this configuration runs is %s at %s on '%s', "
                "against %s %s — %s"
                % (what, _fmt(v, 1, "°C"), duty, _whose, _fmt(L, 0, "°C"),
                   ("PAST it by %s" % _fmt(-_m, 1, "%")) if v > L
                   else ("inside it, %s of it left" % _fmt(_m, 1, "%"))))
        # …and the WINDING beside it whenever something else is hotter: the
        # insulation class is what this paragraph is about, and the reader must
        # not have to infer its margin from another part's.
        _wind = [h for h in hot if h[3] == "the winding"]
        w = max(_wind) if _wind else None
        if w is not None and what != "the winding":
            out += ("; the winding reaches %s against %s — %s left"
                    % (_fmt(w[0], 1, "°C"), _fmt(w[4], 0, "°C"),
                       _fmt((w[4] - w[0]) / w[4] * 100.0, 1, "%")))
        out += ". "
    out += ("Class R (220 °C) or S (240 °C) enamel and slot insulation buy "
            "margin without touching the electromagnetics.")
    return out



def bearing_lubricant(brg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The grease (or oil) these bearings run on and the TOP of its stated
    temperature range, or ``None`` when neither can be resolved.

    WHY (user 2026-09-14).  The bearing seat reaches 155.6 °C on the L155 peak
    duty and nothing in the document said what the lubricant is rated for — the
    library carries ``temp_range_c`` on every card and no rule read it.

    The stored run's per-bearing rows are trimmed and carry no lubricant name
    (only ``lubrication: grease`` and the viscosity it was evaluated at), so the
    fallback is the BEARING CARD's ``default_lubricant`` — and the answer says
    which of the two it is, because a default is an assumption.
    """
    ends = [b for b in (brg or {}).get("bearings") or [] if isinstance(b, dict)]
    name = next((str(b.get("lubricant")) for b in ends if b.get("lubricant")),
                None) or (str(brg.get("lubricant")) if (brg or {}).get("lubricant")
                          else None)
    from_record = name is not None
    card_name = next((str(b.get("bearing")) for b in ends if b.get("bearing")), "")
    try:
        from motor_ai_sim import bearings as _brg
        if name is None and card_name:
            name = getattr(_brg.get_bearing(card_name), "default_lubricant", None)
        if not name:
            return None
        lube = _brg.get_lubricant(str(name))
    except Exception as exc:                                 # noqa: BLE001
        log.debug("report: lubricant card unavailable (%s)", exc)
        return None
    rng = list(getattr(lube, "temp_range_c", None) or [])
    return {
        "name": str(getattr(lube, "name", name)),
        "kind": str(getattr(lube, "kind", "") or ""),
        "from_record": bool(from_record),
        "card": card_name,
        "range_c": rng if len(rng) == 2 else None,
        "limit_c": _numf(rng[1]) if len(rng) == 2 else None,
        "source": ("named by the stored run" if from_record else
                   "the %s card's default_lubricant — ASSUMED, the stored run "
                   "does not name one" % (card_name or "bearing")),
    }


def bearing_rows(brg: Dict[str, Any]) -> List[List[str]]:
    """Each end's card, its speed rating, the card's own verdict on that speed,
    the SKF moment term by term, and what it costs.  Header included.

    The VERDICT is printed whatever it says (reviewer 2026-09-14, C3): the peak
    duty's bearings are stored as "at the limit" at 20,000 rpm and the table
    showed only the two numbers it is the conclusion of, while section 7 turned
    the same fact into a 4.8 % amber margin.

    THE MOMENT IS BROKEN OUT (user 2026-09-14).  855 W at the peak duty is 10 %
    of every loss in the machine, and one M_total said nothing about where it
    comes from: M_rr is the rolling term (it goes as ν^0.6, so it is the one the
    grease temperature moves), M_sl the sliding term the preload sets, M_seal
    zero on an open bearing and M_drag zero on grease — there is no oil bath to
    churn.  And the GREASE's own temperature limit closes the table, because the
    seat temperature is judged against it in section 7.
    """
    # THE HEADERS ARE THE ROW'S OWN QUANTITY, not the bearing's (reviewer
    # 2026-09-14, CS-4): the closing Grease row prints 97.3 °C and 110 °C, and
    # under headings that read "n·dm" and "limit" a temperature looks like a
    # data error.  Every cell carries its unit, so the two columns are named
    # for what they hold on whichever row.
    brows = [["End", "Card", "Speed n·dm / temperature", "Limit", "Verdict",
              "M_rr", "M_sl", "M_seal", "M total", "Loss"]]
    for b in brg.get("bearings") or []:
        sp = b.get("speed") or {}
        _v = str(sp.get("verdict") or "").strip()
        brows.append([
            str(b.get("end") or ""), str(b.get("bearing") or ""),
            _fmt(sp.get("n_dm"), 0, "mm/min"),
            _fmt(sp.get("limit_rpm"), 0, "rpm"),
            ((FLAG + " " + _v) if (sp.get("ok") is False and _v) else
             (_v or ("—" if sp.get("ok") is None else
                     ("past the limit " + FLAG) if sp.get("ok") is False
                     else "inside the card's rating"))),
            _fmt(b.get("M_rr_Nm"), 4), _fmt(b.get("M_sl_Nm"), 4),
            _fmt(b.get("M_seal_Nm"), 4),
            _fmt(b.get("M_total_Nm"), 4, "N·m"), _fmt(b.get("P_W"), 1, "W")])
    wnd = brg.get("windage") or {}
    brows.append(["Windage", "rotor surface + gap shear", "", "", "", "", "", "",
                  _fmt(wnd.get("M_total_Nm"), 4, "N·m"),
                  _fmt(brg.get("P_windage_W"), 1, "W")])
    lube = bearing_lubricant(brg)
    if lube and lube.get("limit_c") is not None:
        t = _numf(brg.get("temp_c"))
        lim = float(lube["limit_c"])
        verdict = "—"
        if t is not None:
            verdict = ((FLAG + " PAST its range") if t > lim else
                       ("within 10 % of it" if (lim - t) / lim * 100.0 <= NEAR_PCT
                        else "inside its range"))
        brows.append(["Grease",
                      lube["name"] + ("" if lube.get("from_record")
                                      else " (card default)"),
                      _fmt(t, 1, "°C"), _fmt(lim, 0, "°C"), verdict,
                      "", "", "", "", ""])
    return brows


def bearing_note_text(brg: Dict[str, Any]) -> str:
    """WHERE the bearing temperature came from.  M_rr goes as ν^0.6, so a grease
    quoted at 40 °C running at 90 °C is a factor of two on the rolling term — a
    number that important may not appear unattributed."""
    _tsrc = {"thermal": " (thermal map)",
             "coupled": " (converged by the coupled run)",
             "assigned": " (set on this machine)",
             "default": " (ASSUMED)"}.get(
                 str(brg.get("bearing_temp_source") or ""), "")
    if brg.get("bearing_temp_note"):
        # …its LEAD CLAUSE only: the note spells the feedback path out in a
        # sentence and a half, and the coupled table already names the source.
        _tsrc = " (%s)" % re.split(
            r"\s+[—–]\s+", str(brg["bearing_temp_note"]), maxsplit=1)[0].strip()
    lube = bearing_lubricant(brg)
    _lube_txt = ""
    if lube:
        _lube_txt = (" (%s, rated %s to %s)"
                     % (lube["name"],
                        _fmt((lube.get("range_c") or [None, None])[0], 0, "°C"),
                        _fmt((lube.get("range_c") or [None, None])[1], 0, "°C"))
                     if lube.get("range_c") else " (%s)" % lube["name"])
    return (
        f"Lubrication {brg.get('lubrication', '—')}{_lube_txt}, preload "
        f"{_fmt(brg.get('preload_n'), 0, 'N')} per bearing, bearing "
        f"temperature {_fmt(brg.get('temp_c'), 0, '°C')}{_tsrc}; radial load "
        f"= the rotating mass {_fmt(brg.get('rotor_mass_kg'), 3, 'kg')} "
        f"({_fmt(brg.get('F_r_total_N'), 1, 'N')}) split between the two ends. "
        f"{brg.get('model', '')}")


# ── page 3 · electromagnetic ────────────────────────────────────────────────


EM_PAGE_UNSOLVED = (
    "Not solved yet — no electromagnetic run is stored on this server and this "
    "configuration has no saved duty. Run the point on the Simulation tab and "
    "save it to a duty.")

EM_NO_PASSPORT = (
    "No 3-D passport for this geometry — no end-effect correction is applied "
    "and the 2-D column is the whole answer.")

EM_NO_SNAPSHOT = (
    "The field snapshot was not kept (or was solved by a different process). "
    "Re-run the point on the Electromagnetic tab to store one.")


def em_k3d_note(k3d: Any) -> str:
    """The one line under the TORQUE table that says what the 3-D column is.

    It belongs to that table and to no other: the machine-constants table below
    has one value column, and this sentence standing under it told the reader
    that two were printed (reviewer 2026-09-14, B1).
    """
    if not k3d:
        return EM_NO_PASSPORT
    return ("k_3d = %.4f, the Stage-A 3-D passport of this geometry; both "
            "columns are printed above." % float(k3d))


def em_constants_note(k3d: Any, drive: str = "sine") -> str:
    """…and the one under the MACHINE CONSTANTS table, which has one column.

    Kt, Km, Km per mass and the two densities carry k_3d — the same correction
    section 3 applies, so the two sections quote one number per quantity.  The
    inductances, the resistances and the flux linkage are the winding's own
    2-D values and are printed as solved.
    """
    # …AND THE TWO EXCEPTIONS ARE NAMED (MJ-3 / MJ-4, audit v7).  The sentence
    # said "every constant here is the sinusoidal run's" over a droop formed on
    # the PWM torque and a resistance quoted at the sinusoid's temperature under
    # an operating point stating another.  The droop is the sinusoid's and says
    # so in its own label; the resistances are this run's, at the temperature
    # the row prints.
    tail = ("" if str(drive or "sine") != "pwm" else
            " The constants here are the SINUSOIDAL run's — a voltage-fed "
            "inverter run does not probe the no-load flux linkage and measures "
            "no KV — except the resistances, which belong to this run and to "
            "the winding temperature printed beside them.")
    if not k3d:
        return ("No 3-D passport for this geometry, so every constant above is "
                "the 2-D value as solved." + tail)
    return ("Kt, Km, Km per mass and the two densities above carry k_3d = "
            "%.4f; the inductances, resistances, flux linkage and mean |B| are "
            "the 2-D values as solved." % float(k3d) + tail)


def em_operating_rows(em: Dict[str, Any], d_duty: Dict[str, Any],
                      geo: Dict[str, Any], em_run: Optional[Dict[str, Any]],
                      mats: Dict[str, Any]) -> List[List[str]]:
    """The operating point, as the four label/value pairs both documents print.

    The electrical frequency is not on every stored summary (older duties carry
    none), and it is speed × pole pairs — derived rather than left blank.  Nor
    is the magnet temperature: it rides on the RUN's key fields, so the run is
    asked first and the assigned card's own quoted temperature is the fallback,
    said as such and never as a solve input.
    """
    I = _g(em, "I_phase_rms_A") or _g(d_duty, "current_arms")
    rpm = _g(em, "rpm") or _g(d_duty, "rpm")
    poles = _slots_poles(geo)[1]
    f_el = _g(em, "f_elec_Hz")
    if f_el is None and rpm and poles:
        f_el = float(rpm) / 60.0 * poles / 2.0
    t_mag, t_mag_src = _g(em, "magnet_temp_C"), ""
    if t_mag is None:
        # A coupled run solved its final pass AT the loop's magnet temperature
        # — that is the run's own temperature, not the card's (2026-09-13).
        t_mag = _g(em, "coupling.magnet_temp_c")
        t_mag_src = " (coupled loop)" if t_mag is not None else ""
    if t_mag is None:
        t_mag = _g(em_run or {}, "key_fields.magnet_temp_c",
                   "sb_key_fields.magnet_temp_c")
    if t_mag is None:
        t_mag = _magnet_card_temp(mats.get("magnet"))
        t_mag_src = " (card as quoted)" if t_mag is not None else ""
    _dl_op = str(_g(em, "star_delta") or "star").lower().startswith("d")
    return [
        ["Line current" if _dl_op else "Phase current",
         f"{_fmt(I, 1, 'A rms')} / "
                          f"{_fmt(float(I) * math.sqrt(2) if I else None, 1, 'A peak')}",
         "Load angle gamma", _fmt(_g(em, "gamma_deg") or _g(d_duty, "gamma_deg"), 1, "°")],
        ["Speed", _fmt(rpm, 0, "rpm"),
         "Electrical frequency", _fmt(f_el, 1, "Hz")],
        ["Winding temperature", _fmt(_g(em, "coil_temp_C"), 1, "°C"),
         "Magnet temperature", _fmt(t_mag, 1, "°C") + t_mag_src],
        # TWO DECIMALS, the same as section 3 and the rule in section 8 (CS-4,
        # audit v7): one place read 17.5 / 24.4 against their 17.46 / 24.42.
        ["Steps per period", _fmt(_g(em, "n_steps_per_period"), 0),
         "Current density (winding current / copper)",
         _fmt(_g(em, "J_coil_A_per_mm2"), 2, "A/mm²")],
        # The connection the point was solved in, beside the current it
        # implies in the winding (line ÷ √3 in delta) — user 2026-09-13.
        ["Terminal connection", _sd_words(None, em, d_duty),
         "Winding current", _fmt(_g(em, "I_winding_rms_A") or I, 1, "A rms")],
    ]


#: The sub-row under the fundamental: what V1 is, in the words the reviewer
#: needed on 2026-09-14 to stop reading "V1 411.4 V" above "peak 403.5 V" as a
#: mistake.  The third column of this table is the k_3d-corrected value, so the
#: sentence cannot ride in the V1 row itself and gets its own.
V1_NOTE_LABEL = "…what V1 is"
V1_NOTE_TEXT = ("an amplitude, not a waveform peak: a flat-topped wave peaks "
                "below its fundamental, and the inverter synthesises this one")

#: …and on a PWM duty the row above it is not a terminal waveform at all, so
#: the sentence says what V1 is against THE BRIDGE rather than against a peak
#: that belongs to the star-equivalent circuit (reviewer 2026-09-15).
V1_NOTE_PWM_TEXT = ("an amplitude, and the only part of the voltage the bridge "
                    "has to synthesise: it is judged against the DC link this "
                    "duty was solved on, not against the carrier-averaged peak "
                    "above")


#: What the "waveform peak" row is on a PWM duty (reviewer 2026-09-15).  The
#: voltage-fed solve runs a star-EQUIVALENT circuit and its "line voltage" is
#: the winding voltage the field gives back, averaged over the carrier — not
#: the pulse train the bridge puts on the terminals, whose amplitude is the DC
#: link and which has a row of its own below.
PWM_VPK_LABEL = "Winding voltage from the field, carrier-averaged peak"
PWM_VDC_LABEL = "Bridge line voltage amplitude [V] = DC link"
PWM_VDC_NOTE = ("what the terminals really carry: a two-level bridge swings "
                "each one between the rails, so the line-to-line pulse "
                "amplitude IS the DC link — never 1.155 × V_dc, which belongs "
                "to the star-equivalent circuit the solve runs in")

#: MJ-6 (audit v7).  Section 3 printed a 912.9 V carrier-averaged peak two rows
#: above "Bridge line voltage amplitude = DC link 750.4 V" and left the reader
#: to reconcile them.  The reason is in `simulation.pwm.star_equivalent_bus`:
#: the voltage-fed solve runs the star-EQUIVALENT circuit on a bus of √3·V_dc,
#: whose branch waveform peaks at 1.1547 × V_dc where the real bridge's line
#: voltage peaks at V_dc, and what the field gives back on top of that is the
#: winding's own ring between pulses, carrier-averaged.  Neither is a terminal
#: voltage; the terminals see the pulse train of the row above.
PWM_VPK_VS_VDC_LABEL = "…why the two rows above exceed the bridge amplitude"
PWM_VPK_VS_VDC_NOTE = (
    "they are not terminal voltages: a voltage-fed run solves the "
    "star-EQUIVALENT circuit, whose branch peaks at 1.1547 × V_dc where the "
    "real bridge's line voltage peaks at V_dc, and the field adds the "
    "winding's own ring between pulses on top of it. The terminals see the "
    "pulse train — ± the DC link of the row above, and no more.")


def em_torque_rows(em: Dict[str, Any],
                   batt: Optional[Dict[str, Any]] = None,
                   drive: str = "sine",
                   em_sine: Optional[Dict[str, Any]] = None,
                   inverter: Optional[Dict[str, Any]] = None) -> List[List[str]]:
    """Torque, power and voltage, 2-D beside the 3-D-corrected column.  Header
    row included.

    ``batt`` is optional and only buys one row: the modulation index at the
    pack minimum, which cannot be formed without a pack (reviewer 2026-09-14 /
    PWM study §1.7).
    """
    k3d = _g(em, "end3d.k_flux")
    T2d = _g(em, "T_em_avg_Nm")
    P2d = _g(em, "P_mech_W")
    _pwm_drive = str(drive or "sine") == "pwm"
    tp = [["", "2-D FEM", "with the 3-D factor"]]
    tp.append(["Torque [N·m]", _fmt(T2d, 3),
               _fmt(_g(em, "end3d.T_corrected_Nm"), 3) if k3d else "no 3-D passport"])
    # ROTOR power (T·ω) — "shaft power" is the coupling's number with the
    # bearings and windage in it, and it has its own rows (headline, duty
    # table); one label per quantity (2026-09-13).
    tp.append(["Rotor power T·ω [kW]",
               _fmt(abs(float(P2d)) / 1000.0 if P2d else None, 3),
               _fmt(abs(float(P2d)) * float(k3d) / 1000.0 if (P2d and k3d) else None, 3)
               if k3d else "—"])
    # EVERY flux-proportional row carries BOTH columns (reviewer 2026-09-11:
    # a k_3d-corrected peak beside an uncorrected rms read as a crest factor
    # nobody could reproduce).  The voltages scale with the flux exactly as
    # the torque does; the current does not, and says so below.
    def _k(v):
        f = _numf(v)
        return (f * float(k3d)) if (f is not None and k3d) else None
    tp.append([("%s [V]" % PWM_VPK_LABEL) if _pwm_drive
               else "Line voltage, waveform peak [V]",
               _fmt(_g(em, "V_line_peak_V"), 1),
               _fmt(_g(em, "end3d.V_line_peak_corrected_V")
                    or _k(_g(em, "V_line_peak_V")), 1) if k3d else "—"])
    # …and the number the terminals and the insulation actually see, which is
    # the link and not the model peak above it (reviewer 2026-09-15).
    _vdc = _numf((inverter or {}).get("v_dc_V"))
    if _pwm_drive and _vdc is not None:
        tp.append([PWM_VDC_LABEL, _fmt(_vdc, 1), PWM_VDC_NOTE])
    tp.append(["Line voltage, rms [V]", _fmt(_g(em, "V_line_rms_V"), 1),
               _fmt(_k(_g(em, "V_line_rms_V")), 1) if k3d else "—"])
    # THE FUNDAMENTAL, beside the waveform peak (reviewer 2026-09-14 / PWM
    # study §1.7): the peak above is what the insulation sees, the fundamental
    # is the only part of it a two-level bridge has to synthesise — and the
    # two disagree on this machine, the peak being the LOWER of the two.  The
    # rows are NAMED for that (reviewer 2026-09-14, second pass: 411.4 V above
    # 403.5 V read as a contradiction), and the note says why it is not one.
    tp.append(["Line voltage, fundamental amplitude V1 [V]",
               _fmt(_g(em, "V1_LL_V"), 1),
               _fmt(_k(_g(em, "V1_LL_V")), 1) if k3d else "—"])
    tp.append([V1_NOTE_LABEL, "",
               V1_NOTE_PWM_TEXT if _pwm_drive else V1_NOTE_TEXT])
    tp.append([("Winding phase voltage from the field, carrier-averaged peak [V]"
                if _pwm_drive else "Phase voltage, waveform peak [V]"),
               _fmt(_g(em, "V_phase_peak_V"), 1),
               _fmt(_k(_g(em, "V_phase_peak_V")), 1) if k3d else "—"])
    tp.append(["Phase voltage, rms [V]", _fmt(_g(em, "V_phase_rms_V"), 1),
               _fmt(_k(_g(em, "V_phase_rms_V")), 1) if k3d else "—"])
    # …and what that fundamental costs in inverter headroom, in the number a
    # drive engineer reads: the modulation index at the pack's worst moment.
    _v1 = _k(_g(em, "V1_LL_V")) if k3d else _numf(_g(em, "V1_LL_V"))
    _vmin = _numf((batt or {}).get("v_min"))
    # ON A PWM DUTY THE BRIDGE MEASURED ITS OWN m (reviewer 2026-09-15), on the
    # link it really ran on; re-deriving one from the pack floor describes a
    # run that never happened.
    _m_run = _numf((inverter or {}).get("m")) if _pwm_drive else None
    if _m_run is not None and _vdc is not None:
        tp.append(["Modulation index m the bridge ran at", _fmt(_m_run, 3),
                   "m = 2·V1_phase,peak/V_dc, V_dc = the %s V link this duty "
                   "was solved on; a two-level bridge stays linear up to "
                   "m = %s." % (_fmt(_vdc, 1), _fmt(MOD_INDEX_LIMIT, 2))])
    else:
        _m = modulation_index(_v1, _vmin)
        if _m is not None:
            tp.append(["Modulation index m at the pack minimum", _fmt(_m, 3),
                       "m = 2·V1_phase,peak/V_dc, V_dc = the pack minimum %s; a "
                       "two-level bridge stays linear up to m = %s."
                       % (_fmt(_vmin, 1, "V"), _fmt(MOD_INDEX_LIMIT, 2))])
    _vp, _vr = _numf(_g(em, "V_line_peak_V")), _numf(_g(em, "V_line_rms_V"))
    if _vp and _vr:
        tp.append(["Crest factor, line", _fmt(_vp / _vr, 3),
                   "peak / rms; %.3f for a pure sine" % math.sqrt(2)])
    _dl0 = str(_g(em, "star_delta") or "star").lower().startswith("d")
    if _dl0:
        # Reviewer 2026-09-13: "for Δ line and phase must be equal" — they are
        # equal in the FUNDAMENTAL; the winding (phase) voltage also carries
        # the zero-sequence triplen EMF that circulates inside the closed
        # delta and cannot appear between two terminals.  Say so on the page.
        tp.append(["Why line ≠ phase in Δ", "",
                   "the winding voltage carries the 3rd-harmonic EMF that "
                   "circulates inside the closed delta and never reaches the "
                   "terminals"])
    tp.append(["Line current [A rms]" if _dl0 else "Phase current [A rms]",
               _fmt(_g(em, "I_phase_rms_A"), 1),
               "the drive's input — no end-effect correction applies"])
    if _dl0 and _g(em, "I_winding_rms_A") is not None:
        tp.append(["Winding current [A rms]", _fmt(_g(em, "I_winding_rms_A"), 1),
                   "line ÷ √3 — the current in the copper"])
    # The ELECTRICAL side of the same operating point (2026-09-11): the shaft
    # row above is mechanical, and a reader checking the efficiency needs both
    # ends of it.  The solved terminal power is signed by convention — negative
    # is power leaving a generator — and the magnitude is what belongs here.
    # From the BALANCE, not from the FEM's own terminal integral: that
    # integral sees only the losses inside the 2-D solve (no post-processed
    # iron, no end-winding copper) and printed a number that matched nothing
    # else on the page (reviewer 2026-09-11: "569.333 − 7.179 = 562.15, equal
    # to neither 553.946 nor 552.49").  Rotor power × k_3d, then the whole
    # electromagnetic loss taken off (generator) or put on (motor).
    _pr = abs(float(P2d)) * float(k3d) if (P2d and k3d) else (abs(float(P2d)) if P2d else None)
    _pl = _numf(_g(em, "P_loss_total_W"))
    _gen = str(_g(em, "op_mode") or "").lower().startswith("gen")
    if _pr is not None and _pl is not None:
        _pe = (_pr - _pl) if _gen else (_pr + _pl)
        tp.append(["Electrical power at the terminals [kW]",
                   _fmt(_pe / 1000.0, 3),
                   ("rotor power × k_3d minus the electromagnetic losses "
                    "(generating)" if _gen else
                    "rotor power × k_3d plus the electromagnetic losses (motoring)")])
    # KV and Kt follow the flux like the voltages: KV = rpm/V RISES by 1/k,
    # Kt falls by k.  Both columns, same as every row above — the old single
    # row put "loaded" in the 2-D column and "no load" in the 3-D one, so the
    # catalog's 27.8 rpm/V (no load, ÷k) matched nothing on this page.
    def _kd(v):
        f = _numf(v)
        return (f / float(k3d)) if (f is not None and k3d) else None
    # The no-load KV is a ψ_PM probe at the magnet CARD's temperature, so it is
    # a machine figure and reads the same under every duty — said in the label,
    # with the duty's own KV walked underneath it (reviewer 2026-09-14).
    _grade = _g(em, "demag.magnet_name")
    _tcard = _magnet_card_temp(_grade) or 150.0
    # …LABELLED AS THE SINUSOID'S on a PWM document (BL-2): a voltage-fed run
    # probes no flux linkage and measures no KV, and section 3 already says so
    # on the same four quantities.
    _sine_tail = SINE_ROW_TAIL if _pwm_drive else ""
    tp.append(["KV, no load (magnets at the card's %s) [rpm/V]%s"
               % (_fmt(_tcard, 0, "°C"), _sine_tail),
               _fmt(_g(em, "KV_noload_rpm_per_V_line"), 2),
               _fmt(_kd(_g(em, "KV_noload_rpm_per_V_line")), 2) if k3d else "—"])
    _t_mag = _duty_magnet_temp(em)
    _kv2, _kv_why = kv_at_magnet_temp(_g(em, "KV_noload_rpm_per_V_line"),
                                      _grade, _t_mag)
    _kv3 = kv_at_magnet_temp(_kd(_g(em, "KV_noload_rpm_per_V_line")),
                             _grade, _t_mag)[0] if k3d else None
    tp.append(["KV, no load at this duty's magnet temperature [rpm/V]"
               + _sine_tail,
               _fmt(_kv2, 2) if _kv2 is not None else "—",
               (_fmt(_kv3, 2) if _kv3 is not None else "—") if k3d else "—"])
    tp.append(["…how", "",
               (("KV goes as 1/Br: %s" % _kv_why) if _kv2 is not None
                else _kv_why)])
    tp.append(["KV, loaded [rpm/V]" + _sine_tail,
               _fmt(_g(em, "KV_rpm_per_V_line"), 2),
               _fmt(_kd(_g(em, "KV_rpm_per_V_line")), 2) if k3d else "—"])
    # THE RIPPLE THIS RUN MADE, and what kind it is (2026-09-14).  On a PWM
    # duty the figure is dominated by the carrier and is not the low-order
    # ripple the 5 % gate is about — the note says which, because the two
    # differ by a factor of thirty on this machine.
    # ONE QUANTITY, ONE LABEL, AND BOTH RIPPLES (CS-9, audit v6).  Section 3
    # prints "Torque ripple" with a "…of which low-order" row under it; this
    # table printed only the first and explained the second in prose, so a
    # reader comparing the two pages met 28.7 % under one label and 1.4 % under
    # another and could not tell they were two different quantities.
    tp.append(["Torque ripple [%]", _fmt(_g(em, "T_ripple_pct"), 1),
               ("peak-to-peak over mean; on this supply it is dominated by the "
                "CARRIER — the low-order ripple the 5 % limit is on is the row "
                "below" if _pwm_drive else
                "peak-to-peak over mean, cogging included")])
    if _pwm_drive and _g(em_sine or {}, "T_ripple_pct") is not None:
        tp.append(["…of which low-order (cogging + slotting), sinusoidal "
                   "run [%]", _fmt(_g(em_sine or {}, "T_ripple_pct"), 1),
                   "the same machine on a clean sinusoid — what section 5 "
                   "compares and what the warnings section judges"])
    # Kt against the LINE current — the setpoint and the inverter's rating
    # (user 2026-09-12: the three leads to the inverter).  In delta that is
    # the winding current × √3, so the per-winding figure is not the one the
    # card shows; the constants table below prints both, named.
    _dl = str(_g(em, "star_delta") or "star").lower().startswith("d")
    _kt = _g(em, "Kt_Nm_per_A_line") if (_dl and _g(em, "Kt_Nm_per_A_line") is not None) \
        else _g(em, "Kt_Nm_per_Arms")
    tp.append(["Torque constant Kt [N·m/A rms" + (", line" if _dl else "")
               + "]" + _sine_tail,
               _fmt(_kt, 4), _fmt(_k(_kt), 4) if k3d else "—"])
    return tp


#: Either side of 1.0 the saliency is called "≈ 1" — inside this band Lq and Ld
#: are the same inductance as far as a control engineer is concerned.
SALIENCY_FLAT_BAND = (0.98, 1.02)


def saliency_note(ratio: Any) -> str:
    """What Lq/Ld MEANS on this machine — read off the number, not assumed.

    The note used to say "below 1 here" on every report, including the ones
    printing 1.228 (reviewer 2026-09-14, the L155 rated duty): the same
    configuration is inverse-salient at one operating point and salient at
    another, because the saturation moves with the current.  So the sentence is
    chosen by the value it sits beside.
    """
    v = _numf(ratio)
    lo, hi = SALIENCY_FLAT_BAND
    if v is None:
        return ("Lq over Ld at this operating point — above 1 there is "
                "reluctance torque to take with a negative gamma, below 1 "
                "there is none")
    if v > hi:
        return ("above 1: Lq > Ld, so a negative gamma (current advanced into "
                "the d-axis) adds reluctance torque on top of the magnet torque")
    if v < lo:
        return ("below 1 here: the q-axis saturates first, so there is no "
                "reluctance torque to harvest by advancing the current")
    return "≈ 1: no usable reluctance torque either way"


#: The tail a row of this table carries when its number is the SINUSOIDAL run's
#: on a PWM duty — the same label the comparison table uses (``SINE_ROW_TAIL``).
SINE_CONSTANT_TAIL = " (sinusoidal run)"


def em_constant_rows(em: Dict[str, Any],
                     em_sine: Optional[Dict[str, Any]] = None,
                     drive: str = "sine") -> List[List[str]]:
    """The machine constants a control engineer asks for.  Header included.

    User 2026-09-11: *"проверь все эти параметры, они обязательно должны быть
    отображены, каждый в своём разделе"*.  Ld, Lq, the saliency, the magnet
    flux linkage, the winding resistances and the saturation droop were on the
    summary card and in no section of this document — and they are exactly what
    a drive is tuned from.

    ``em_sine`` is the duty's sinusoidal summary on a PWM duty, and TWO rows
    need it (audit v7):

    * **the saturation droop** (MJ-3) is a property of the iron at this point,
      measured against an unsaturated dq twin of the SAME run.  Formed on the
      PWM torque it read 19.87 % on the L155 peak duty — 204.935 N·m against a
      255.739 N·m reference — where the sinusoidal torque at that point is
      239.972 N·m, so about 13.7 points of what the row called saturation were
      carrier loss and the −1.18 % point miss.  It is taken on the sinusoidal
      run, like every other constant of this table, and its label says so;
    * **the resistances** (MJ-4) are the opposite case: they belong to the RUN
      and to the temperature it settled at, so they are this run's and the row
      prints that temperature rather than leaving it to be read off an
      operating-point box that states another one.
    """
    _pwm = str(drive or "sine") == "pwm" and bool(em_sine)
    _sine = (em_sine or {}) if _pwm else em
    def R(label, v, d, unit, note=""):
        if v is not None:
            rows.append([label, _fmt(v, d, unit), note])

    rows = [["Constant", "Value", "What it is"]]
    # A delta machine's Ld / Lq / R are quoted for the WINDING — the numbers the
    # field was solved with.  A datasheet reader expects the equivalent star's
    # per-phase values (one third), so both are printed and named.
    _delta = str(_g(em, "star_delta") or "star").lower().startswith("d")
    _w = " (winding)" if _delta else ""
    # ×k_3d ON THE FLUX-PROPORTIONAL CONSTANTS (reviewer 2026-09-14, B1).
    # Kt and Km were printed here as the raw 2-D numbers while section 3
    # printed the same quantities × k_3d, so one document carried Km 4.611 and
    # Km 4.416 under identical labels.  The torque constant follows the flux,
    # and Km = Kt / sqrt(R) follows it too.
    _k3c = _numf(_g(em, "end3d.k_flux"))

    def _km(v: Any) -> Optional[float]:
        f = _numf(v)
        return (f * float(_k3c)) if (f is not None and _k3c) else f

    _kt_tail = ((" × k_3d = %s" % _fmt(_k3c, 4)) if _k3c else " (2-D, no 3-D passport)")
    # The load angle, for the inductance caveat below (reviewer 2026-09-14, C6).
    _gam = _numf(_g(em, "gamma_deg"))
    # ONE CAVEAT, TWO AXES (MJ-7, reviewer 2026-09-14).  The shared suffix was
    # appended to both rows, so the q-axis row carried a sentence about the
    # d-axis.  The common half stays common; each axis says what its own chord
    # means.
    _chord_head = ("" if (_gam is None or abs(_gam) < 0.5) else
                   (" — a CHORD extraction at gamma = %s, not a small-signal "
                    "slope" % _fmt(_gam, 1, "°")))
    _chord_d = _chord_head
    _chord_q = _chord_head + ("" if not _chord_head else
                              "; it already carries this current's saturation")
    rows.append(["Terminal connection", "DELTA (Δ)" if _delta else "STAR (Y)",
                 ("windings between the lines: I_line = √3·I_winding" if _delta
                  else "isolated neutral: V_line = √3·V_phase, "
                       "I_line = I_phase")])
    R("Magnet flux linkage Psi_PM", _g(em, "psi_pm_Wb"), 4, "Wb",
      "the back-EMF per rad/s")
    R("Ld" + _w, _g(em, "Ld_mH"), 4, "mH",
      "direct-axis inductance at this point" + _chord_d)
    R("Lq" + _w, _g(em, "Lq_mH"), 4, "mH",
      "quadrature-axis inductance at this point" + _chord_q)
    if _delta:
        R("Ld, star-equivalent", _g(em, "Ld_eq_star_mH"), 4, "mH",
          "the per-phase value of the equivalent star — one third of the winding's")
        R("Lq, star-equivalent", _g(em, "Lq_eq_star_mH"), 4, "mH", "")
    R("Saliency Lq/Ld", _g(em, "saliency_Lq_over_Ld"), 3, "",
      saliency_note(_g(em, "saliency_Lq_over_Ld")))
    R("Torque constant Kt" + (" per winding A" if _delta else ""),
      _km(_g(em, "Kt_Nm_per_Arms")), 4, "N·m/A rms", "2-D" + _kt_tail)
    if _delta:
        R("Torque constant Kt per line A", _km(_g(em, "Kt_Nm_per_A_line")), 4,
          "N·m/A rms", "what the inverter is rated against; 2-D" + _kt_tail)
    R("Motor constant Km", _km(_g(em, "Km_Nm_sqrtW")), 3, "N·m/√W",
      "torque per root watt of copper; 2-D" + _kt_tail)
    R("Km per mass", _km(_g(em, "Km_per_mass_Nm_sqrtW_kg")), 4,
      "N·m/(√W·kg)", "2-D" + _kt_tail)
    # …and the two densities every comparison is made in, from THIS report's
    # own numbers (reviewer 2026-09-14, D15): torque × k_3d over the total mass
    # the cover prints, never the stored 2-D `torque_per_mass_Nm_kg`.
    _mtot = _numf(_g(em, "mass_total_kg"))
    _t3d = _km(_g(em, "T_em_avg_Nm"))
    _p3d = _km(_g(em, "P_mech_W"))
    if _mtot and _t3d:
        R("Torque per mass", abs(_t3d) / _mtot, 3, "N·m/kg",
          "torque × k_3d over the total mass %s" % _fmt(_mtot, 3, "kg"))
    if _mtot and _p3d:
        R("Rotor power per mass", abs(_p3d) / 1000.0 / _mtot, 3, "kW/kg",
          "rotor power × k_3d over the same mass")
    # THE TEMPERATURE THE RESISTANCE BELONGS TO, printed (MJ-4, audit v7).
    _rt = _numf(_g(em, "coil_temp_C"))
    _r_note = ("at this run's winding temperature%s, end windings included"
               % ("" if _rt is None else " of %s" % _fmt(_rt, 1, "°C")))
    R("Phase resistance" + _w, (lambda v: v * 1000.0 if v is not None else None)(
        _numf(_g(em, "R_phase_ohm"))), 3, "mOhm", _r_note)
    if _delta:
        R("Phase resistance, star-equivalent",
          (lambda v: v * 1000.0 if v is not None else None)(
              _numf(_g(em, "R_phase_eq_star_ohm"))), 3, "mOhm",
          "one third of the winding's")
    R("Line-to-line resistance",
      (lambda v: v * 1000.0 if v is not None else None)(
          _numf(_g(em, "R_line_line_ohm"))), 3, "mOhm",
      "what a meter across two terminals reads")
    R("Phase copper section", _g(em, "A_phase_mm2"), 2, "mm²",
      "strand × parallel paths × strands in hand; the current density divides "
      "the winding current by it")
    # SATURATION DROOP — and WHAT LINE it is measured from (user 2026-09-14).
    # It is NOT a straight line through a load sweep's small-current points:
    # the reference is the UNSATURATED LINEAR dq twin of this same operating
    # point, T_lin = 1.5·p·[ψ_PM·i_q + (Ld − Lq)·i_d·i_q], with ψ_PM the run's
    # own PM flux linkage and Ld/Lq the small-signal BENCH probe at the I ≈ 0
    # iron state (the `bench_ldq` block, a 2 A rms probe).  The stored number is
    # floored at zero on two paths, so a printed "0 %" can mean either "no
    # droop" or "the measured torque came out ABOVE its own unsaturated
    # reference" — the L155 rated duty is the second, 187.855 N·m against a
    # 184.946 N·m reference.  That is the reference's resolution, not a finding,
    # and it is printed as "not measured" rather than as a zero.
    # …AND IT IS THE SINUSOIDAL RUN'S, like every other constant of this table
    # (MJ-3, audit v7): a carrier's torque loss and a missed operating point are
    # not saturation, and the row sat inside a table whose closing sentence says
    # every number in it is the sinusoid's.
    _sat = _numf(_g(_sine, "saturation.droop_pct"))
    _t_lin = _numf(_g(_sine, "saturation.T_linear_Nm"))
    _t_avg = _numf(_g(_sine, "T_em_avg_Nm"))
    _bench = _g(_sine, "bench_ldq") or _g(em, "bench_ldq") or {}
    _sat_label = "Saturation droop" + (SINE_CONSTANT_TAIL if _pwm else "")
    _sat_note = (
        "how far the torque has fallen below the UNSATURATED linear reference "
        "T = 1.5·p·[ψ_PM·i_q + (Ld − Lq)·i_d·i_q] at this same point, with "
        "Ld/Lq from the small-signal bench probe%s%s"
        % ("" if not _numf(_bench.get("I_probe_arms")) else
           " at %s" % _fmt(_bench.get("I_probe_arms"), 1, "A rms"),
           "; measured on the sinusoidal run, so no carrier loss is counted "
           "as saturation" if _pwm else ""))
    if (_sat is not None and _sat <= 0.0 and _t_lin is not None
            and _t_avg is not None and abs(_t_avg) >= abs(_t_lin)):
        rows.append([
            _sat_label, "not measured",
            _sat_note + ". The solved torque (%s) came out at or above the "
            "reference (%s), so the reference cannot resolve a droop here"
            % (_fmt(_t_avg, 3, "N·m"), _fmt(_t_lin, 3, "N·m"))])
    else:
        R(_sat_label, _sat, 2, "%",
          _sat_note + ("" if _t_lin is None else
                       "; the reference is %s against a solved %s"
                       % (_fmt(_t_lin, 3, "N·m"), _fmt(_t_avg, 3, "N·m"))))
    R("Air-gap flux density, mean |B|", _g(em, "B_gap_mean_T"), 3, "T", "")
    return rows


def em_loss_rows(em: Dict[str, Any],
                 brg: Optional[Dict[str, Any]]) -> List[List[str]]:
    """The whole loss table, term by term, with where each watt goes.  Header
    row included."""
    P2d = _g(em, "P_mech_W")
    P_cu = _g(em, "P_stranded_W")
    P_fe = _g(em, "P_core_W")
    tot_em = _g(em, "P_loss_total_W")
    lrows = [["Term", "W", "Where it goes"]]

    def _lr(label, v, note, d=1):
        lrows.append([label, _fmt(v, d), note])

    _lr("Copper (I²R + proximity)", P_cu,
        "at the winding temperature above; the AC share is solved")
    # The per-half iron split and the per-part solid split only exist on runs
    # solved after they were added.  An older summary carries the SUMS, and the
    # report prints the sums under their honest label rather than four em dashes
    # beside a total that is plainly not zero.
    if _g(em, "P_core_stator_W") is not None:
        _lr("Iron — stator", _g(em, "P_core_stator_W"),
            "Bertotti over the steel's measured P(B,f) surface")
        _lr("Iron — rotor", _g(em, "P_core_rotor_W"),
            "" if _g(em, "P_loss_split_measured") else
            "split NOT measured on this run — the whole core loss is billed "
            "to the stator")
    else:
        _lr("Iron (stator + rotor)", P_fe,
            "Bertotti over the steel's measured P(B,f) surface; this run "
            "carries no per-half split")
    # WHETHER THE SOLID LOSS IS SPLIT decides how the sleeve is listed, and it
    # has to: on a run WITHOUT the split the combined row already contains the
    # sleeve, so printing a sleeve row beside it invites the reader to add the
    # column up and land 20 W above the total — reported 2026-09-11 ("двойной
    # учёт потерь в гильзе... прямое суммирование даёт 6,462.6 Вт, тогда как
    # итоговая строка правильно указывает 6,442.9 Вт").  The total was right
    # both times; the table was ambiguous.  Split: three siblings that add up.
    # Not split: one row, and the sleeve indented under it as a part OF it.
    _split = (_g(em, "P_mag_W") is not None or _g(em, "P_shaft_W") is not None)
    if _split:
        _lr("Magnets (eddy)", _g(em, "P_mag_W"),
            "solved eddy currents in the magnet blocks")
        _lr("Shaft (eddy)", _g(em, "P_shaft_W"), "solid-conductor eddy loss")
    else:
        _lr("Magnets and solid parts (eddy)", _g(em, "P_solid_W"),
            "magnets, rotor housing, shaft and sleeve together; this run "
            "carries no per-part split")
    if _g(em, "P_sleeve_W") is not None:
        _lr("Sleeve (eddy)" if _split else "  …of which the sleeve",
            _g(em, "P_sleeve_W"),
            "not in the thermal loss map (no loss model for the band)"
            # ONE DECIMAL, like every neighbour in this table and like the
            # comparison table in section 3 (reviewer 2026-09-14, D2).
            + ("" if _split else "; ALREADY counted in the row above"), 1)
    _lr("Total electromagnetic", tot_em, "copper + iron + all solid conductors")
    if brg and brg.get("has_bearings"):
        _lr("Bearings", brg.get("P_bearings_W"), "SKF friction model — analytic")
        _lr("Windage", brg.get("P_windage_W"), "rotor surface + gap shear — analytic")
        # ONE SUM, NOT TWO (CS-3, audit v7).  Section 3 prints the coupled
        # loop's own `P_loss_total_incl_mech_W` and this row re-added the
        # terms, so the two pages read 6,234.5 and 6,234.4 W for one machine.
        # The loop's figure when the run has one; the sum only when it does not.
        _incl = _numf(_g(em, "coupling.P_loss_total_incl_mech_W"))
        _lr("Total including mechanical",
            (_incl if _incl is not None else
             (float(tot_em or 0.0) + float(brg.get("P_mech_extra_W") or 0.0))),
            "what a calorimeter around the machine would read")
    else:
        lrows.append(["Bearings + windage", "—",
                      "no bearings assigned — UNKNOWN, deliberately not zero"])

    eta = _g(em, "efficiency")
    eta = float(eta) * 100.0 if (eta is not None and float(eta) <= 1.5) else eta
    sv = shaft_view(em, brg)
    if sv["eta_em"] is not None:
        eta = 100.0 * sv["eta_em"]
    lrows.append(["Electromagnetic efficiency", _fmt(eta, 2, "%"),
                  ("(rotor power × k_3d − the terms above) / rotor power × k_3d"
                   if sv["gen"] else
                   "rotor power × k_3d / (rotor power × k_3d + the terms above)")])
    if brg and brg.get("has_bearings") and sv["eta_shaft"] is not None:
        lrows.append(["Shaft efficiency", _fmt(100.0 * sv["eta_shaft"], 2, "%"),
                      "the same balance with bearings and windage in it"])
    return lrows


def em_demag_text(em: Dict[str, Any],
                  corner: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """The demagnetisation sentence, or ``None`` when the run did not model it.

    The headline the app quotes is the volume-weighted ENERGY-product deficit
    ((BH)max goes as Br², which is the user's criterion): nominal grade × kept
    energy is the grade the magnet has effectively become.  ``br_worst_pct`` is
    the worst single element, and the per-magnet report carries the knee field
    of the magnets close enough to it to be worth naming.
    """
    dem = _g(em, "demag")
    if not (isinstance(dem, dict) and dem):
        return None
    knee = None
    for r in (_g(em, "demag_report") or dem.get("report") or []):
        if isinstance(r, dict) and r.get("H_knee_kA_per_m") is not None:
            knee = r["H_knee_kA_per_m"]
            break
    grade = ""
    if dem.get("grade_nominal") and dem.get("grade_effective"):
        grade = (" Grade %g nominal, %g effective (%s)."
                 % (float(dem["grade_nominal"]), float(dem["grade_effective"]),
                    dem.get("magnet_name") or "assigned magnet"))
    # THE WORST ELEMENT IS A FRACTION KEPT, and the reader is told how much of
    # the magnet is with it (user 2026-09-14).  `br_worst_pct` is
    # 100 × min(Br factor) — what the element KEPT, not what it lost — and the
    # stored field says the 18.4 % of the L155 peak duty is 160 of 1380 elements
    # under 80 %, 2.05 % of the magnet area.
    _corner = demag_corner_clause(corner)
    return ("Br kept %s of the magnet volume, energy ((BH)max) lost %s, worst "
            "single element KEPT %s of its Br%s.%s%s" % (
                _fmt(dem.get("br_kept_vol_pct"), 3, "%"),
                _fmt(dem.get("bh_loss_pct"), 3, "%"),
                _fmt(dem.get("br_worst_pct"), 1, "%"),
                (", knee field %s kA/m" % _fmt(knee, 1)) if knee is not None else "",
                grade, (" " + _corner) if _corner else ""))


def em_map_numbers(key: str, maps: Dict[str, Any],
                   other: Optional[Dict[str, Any]]) -> str:
    """The ONE number per side that belongs in a paired map's caption.

    The worst magnet element on the demagnetisation map and the raw peak on the
    |B| map — both are read off the same arrays the two pictures are drawn
    from, so the caption cannot disagree with either half of its own figure.
    """
    if not other:
        return ""
    if key == "demag":
        return pair_number_clause("worst element keeps",
                                  maps.get("demag_min_pct"),
                                  other.get("demag_min_pct"), 1, "%")
    if key == "b":
        # …AND WHAT THAT MAXIMUM IS (CS-3).  The caveat went missing when the
        # captions were compacted, and no table anywhere carries a |B| peak to
        # check the printed number against.
        clause = pair_number_clause("raw element maximum", maps.get("b_max_T"),
                                    other.get("b_max_T"), 2, "T")
        return (clause + " at a corner (a singularity, not a material's flux "
                         "density)") if clause else ""
    return ""


def em_map_figures(maps: Dict[str, Any],
                   other: Optional[Dict[str, Any]] = None
                   ) -> List[Tuple[str, str, str]]:
    """``(key, caption, what-to-say-when-missing)`` for the three field maps.

    WHOSE MAPS THESE ARE (2026-09-09).  A field snapshot is per-MACHINE and
    per-run: exactly one is kept, for the point that was solved last.  The
    comparison tables have a column per duty because the per-duty store holds
    SCALARS; a per-element field for every duty would be a mesh store, and this
    document does not invent one.  So the maps belong to one operating point and
    the caption says which.

    The solver writes its own label for the loss map, component by component,
    and it already opens with "cycle-averaged loss density" — printed verbatim,
    not paraphrased and not said twice.
    """
    # ONE SENTENCE (user 2026-09-14).  The solver's label spells its loss model
    # out component by component — "iron: Bertotti shape normalised to …;
    # magnets: solved σE² (coupled σ·∂A/∂t), unrenormalised; …" — which is a
    # method note, not a caption.  Only its lead clause is printed; the models
    # are named once, in §9.
    lbl = str(maps.get("label") or "").strip()
    lbl = re.split(r"\s+[—–-]\s+", lbl, maxsplit=1)[0].strip()
    loss_cap = (lbl or "The run's cycle-averaged loss density")
    loss_cap = loss_cap[0].upper() + loss_cap[1:]
    if not loss_cap.endswith("."):
        loss_cap += "."
    demag_cap = ("Irreversible demagnetisation, Br kept per magnet element, "
                 "per cent of the card value.")
    # The worst element is read off the SAME array the picture draws, so the
    # caption cannot disagree with its own map.  On a PAIR it moves to the
    # per-side clause at the end of the caption — one number each, not one
    # number that could be read as belonging to both pictures.
    if maps.get("demag_min_pct") is not None and not other:
        demag_cap += " Worst element keeps %s of Br." % _fmt(
            maps["demag_min_pct"], 1, "%")
    # The |B| scale is capped, and a capped scale says so beside the picture
    # (reviewer 2026-09-14: a bar ending at 3.599 T on a 2.3 T machine).  A pair
    # shares ONE cap — the wider of the two, so neither side is clipped harder
    # than it would be alone — and the two raw maxima ride in the per-side
    # clause.
    # ONE SENTENCE, NOT TWO (CS-4): a caption is one statement about the
    # figure plus the locator that says which side is which.
    b_cap = ""
    _caps = [_numf(m.get("b_cap_T")) for m in (maps, other or {})
             if _numf(m.get("b_cap_T")) is not None]
    if _caps and (maps.get("b_max_T") is not None
                  or (other or {}).get("b_max_T") is not None):
        if other:
            # Each side on its own scale since 2026-09-14, so the cap is named
            # as a rule and the two raw maxima ride in the per-side clause.
            b_cap = ("; each scale capped at its own %sth percentile"
                     % _fmt(B_MAP_PCTILE, 1))
        else:
            b_cap = ("; scale capped at the %sth percentile %s, raw element "
                     "maximum %s at a corner (a singularity, not a material's "
                     "flux density)"
                     % (_fmt(B_MAP_PCTILE, 1), _fmt(maps["b_cap_T"], 2, "T"),
                        _fmt(maps["b_max_T"], 2, "T")))
    return [
        ("b", "Flux density |B| of the run's final frame, T; air left blank"
              + b_cap + ".", "|B| map not stored on this run"),
        # …and the field the |B| map was differentiated from, which is the one
        # that can be read as flux lines (user 2026-09-10).
        ("az", AZ_CAPTION, "A_z was not kept on this run"),
        ("loss", loss_cap.rstrip(".") + "; unmodelled elements are left blank.",
         "loss-density map not stored on this run"),
        # A run solved WITHOUT the demagnetisation model has no map to show —
        # that is a normal state, worded differently from a stripped |B| map.
        ("demag", demag_cap, "no demagnetisation map: the run did not model "
                             "demagnetisation, or its per-element array was "
                             "not kept"),
    ]


def _em_page(st, em, em_src, d_duty, brg, snap, em_run, geo, mats,
             map_duty: Optional[str] = None,
             wf: Optional[Dict[str, Any]] = None,
             map_tag: Optional[str] = None,
             map_point: Optional[str] = None,
             wf_note: str = "",
             batt: Optional[Dict[str, Any]] = None,
             map_from_duty: bool = False,
             demag_corner: Optional[Dict[str, Any]] = None,
             report_tag: str = "",
             pair: Optional[Dict[str, Any]] = None,
             figs: Optional[List[int]] = None,
             drive: str = "sine",
             em_sine: Optional[Dict[str, Any]] = None,
             inverter: Optional[Dict[str, Any]] = None) -> List[Any]:
    out: List[Any] = [_para(section_heading(None, "em"), st["h1"])]
    if not em:
        out.append(_para(EM_PAGE_UNSOLVED, st["warn"]))
        return out
    out.append(_para(f"Source: {em_src}.", st["note"]))

    out.append(_para("Operating point", st["h2"]))
    w = CONTENT_W / 2.0
    out.append(_table(em_operating_rows(em, d_duty, geo, em_run, mats),
                      [w * 0.46, w * 0.54, w * 0.46, w * 0.54], size=8.8))

    out.append(_para("Torque, power and voltage", st["h2"]))
    # The note column WRAPS (a plain string is clipped at the cell edge in
    # reportlab — "…I_line =" on the page, reviewer 2026-09-13).
    out.append(_table([[r[0], r[1], _para(r[2], st["cell"])]
                       for r in em_torque_rows(em, batt, drive, em_sine,
                                               inverter)],
                      [175, 95, CONTENT_W - 270], header=True, size=8.8))
    # The k_3d footnote belongs to the TORQUE table above, which really has
    # two columns (reviewer 2026-09-14, B1).
    out.append(_para(em_k3d_note(_g(em, "end3d.k_flux")), st["note"]))
    _crows = em_constant_rows(em, em_sine, drive)
    if len(_crows) > 1:
        out.append(_para("Machine constants", st["h2"]))
        out.append(_table([[r[0], r[1], _para(r[2], st["cell"])] for r in _crows],
                          [175, 95, CONTENT_W - 270], header=True, size=8.8))
        out.append(_para(em_constants_note(_g(em, "end3d.k_flux"), drive),
                         st["note"]))

    out.append(_para("Losses", st["h2"]))
    out.append(_table([[r[0], r[1], _para(r[2], st["cell"])]
                       for r in em_loss_rows(em, brg)],
                      [150, 60, CONTENT_W - 210], header=True, size=7.6))
    # THE LOSS PIE, WHICH ONLY THE .DOCX USED TO DRAW (MJ-5, reviewer
    # 2026-09-14).  Same chart, same caption, same tag as Word's.
    # …and since 2026-09-14 it is drawn for BOTH duties, side by side.
    _L, _R = (pair or {}).get("left"), (pair or {}).get("right")
    _note = (pair or {}).get("note") or ""
    from reportlab.platypus import KeepTogether as _KTp
    if _R:
        _pl = _loss_pie_png((_L or {}).get("em") or em, (_L or {}).get("brg"),
                            width_cm=PAIR_CM)
        _pr = _loss_pie_png(_R.get("em") or {}, _R.get("brg"), width_cm=PAIR_CM)
        _blk = _fig_pair(st, _pl, _pr, pair_caption(LOSS_PIE_CAPTION, _L, _R,
                                       have=(bool(_pl), bool(_pr)),
                                       note=_note),
            max_height=PAIR_MAX_H, figs=figs)
        if _blk is not None:
            out.append(_blk)
    else:
        _pie = _image(_loss_pie_png(em, brg, width_cm=MAP_FULL_CM),
                      CONTENT_W, max_height=PAIR_MAX_H)
        if _pie is not None:
            _pie.hAlign = "LEFT"
            out.append(_KTp([_pie, _para("Fig. [%s] — %s"
                                         % (report_tag or "", LOSS_PIE_CAPTION),
                                         st["note"])]))

    # ── the waveforms the losses were measured on (2026-09-11) ───────────
    # Every caption carries the duty and the point it belongs to, like the map
    # captions below (reviewer 2026-09-14, B9): only one duty is DRAWN and the
    # tables carry two, so an untagged chart is ambiguous by construction.
    from reportlab.platypus import KeepTogether as _KTw
    _wtag = map_tag or _map_tag(map_duty, _g(em, "rpm") or _g(d_duty, "rpm"),
                                _g(em, "I_phase_rms_A")
                                or _g(d_duty, "current_arms"))
    if _R:
        for _fn, _cap in ((_torque_png, TORQUE_CAPTION),
                          (_currents_png, CURRENTS_CAPTION),
                          (_voltage_png, voltage_caption(
                              (_L or {}).get("wf"), _R.get("wf")))):
            _a = _fn((_L or {}).get("wf") or {}, width_cm=PAIR_CM)
            _b = _fn(_R.get("wf") or {}, width_cm=PAIR_CM)
            if not (_a or _b):
                continue
            _blk = _fig_pair(st, _a, _b, pair_caption(_cap, _L, _R,
                                           have=(bool(_a), bool(_b)),
                                           note=_note),
                max_height=PAIR_MAX_H, figs=figs)
            if _blk is not None:
                out.append(_blk)
    else:
        for _png, _cap in ((_torque_png(wf or {}, width_cm=MAP_FULL_CM),
                            TORQUE_CAPTION),
                           (_currents_png(wf or {}, width_cm=MAP_FULL_CM),
                            CURRENTS_CAPTION),
                           (_voltage_png(wf or {}, width_cm=MAP_FULL_CM),
                            voltage_caption(wf))):
            _im = _image(_png, CONTENT_W, max_height=PAIR_MAX_H)
            if _im is not None:
                out.append(_KTw([_im, _para(
                    "Fig. [%s] — %s" % (_wtag, _cap + (wf_note or "")),
                    st["note"])]))

    dem_text = em_demag_text(em, demag_corner)
    if dem_text:
        out.append(_para("Demagnetisation", st["h2"]))
        out.append(_para(dem_text, st["body"]))

    # ── the pictures ────────────────────────────────────────────────────────
    # User 2026-09-08: "рисунки надо делать на всю ширину страницы, а то ничего
    # не видно, и подписи под ними и демагнитизации обязательно рисовать" —
    # every map takes the full content width on a row of its own, its caption
    # travels with it (image + caption in one KeepTogether, nothing more: the
    # earlier whole-block grouping is what left a half-empty page behind).
    maps, maps_r = em_maps_pair(
        ((_L or {}).get("src") or {}).get("em") or snap if _R else snap,
        (_R.get("src") or {}).get("em") if _R else None,
        width_cm=PAIR_CM if _R else MAP_FULL_CM)
    from reportlab.platypus import KeepTogether
    heading = _para("Field maps from the stored run", st["h2"])
    # THE LEAD-IN LEADS (MJ-6, audit v5): the sentence that says which side is
    # which used to be appended after the last figure of the section, two pages
    # below the first pair it introduces.
    _owner = _para(pair_owner_text("The field maps", pair,
                                   first_fig=fig_ahead(figs))
                   or map_owner_text(map_duty, em, d_duty, point=map_point,
                                     from_duty=map_from_duty), st["note"])
    figures = em_map_figures(maps, maps_r)
    # Every caption carries the duty and the operating point it belongs to
    # (user 2026-09-09: the maps must say which mode they are of).
    tag = map_tag or _map_tag(map_duty, _g(em, "rpm") or _g(d_duty, "rpm"),
                              _g(em, "I_phase_rms_A") or _g(d_duty, "current_arms"))
    n_fig = 0
    pending_heading: Optional[List[Any]] = [heading, _owner]
    for key, cap, missing in figures:
        _a, _b = maps.get(key), (maps_r or {}).get(key)
        if not (_a or _b):
            if pending_heading is not None:
                out += pending_heading
                pending_heading = None
            out.append(_para("Fig. — " + missing + ".", st["note"]))
            continue
        n_fig += 1
        lead: List[Any] = []
        if pending_heading is not None:          # heading travels with fig. 1
            lead += pending_heading
            pending_heading = None
        if _R:
            _blk = _fig_pair(st, _a, _b, pair_caption(
                    cap, _L, _R, have=(bool(_a), bool(_b)),
                    numbers=em_map_numbers(key, maps, maps_r), note=_note,
                    map_kind="em"),
                max_height=PAIR_MAX_H, lead=lead, figs=figs)
            if _blk is not None:
                out.append(_blk)
            continue
        # Page width, but never taller than about half a page: a wedge is
        # wide and short and fills the width; a square map is capped so it and
        # its caption still fit under the tables instead of opening a page.
        img = _image(_a or _b, CONTENT_W, max_height=PAIR_MAX_H)
        if img is None:
            n_fig -= 1
            out += lead
            out.append(_para("Fig. — " + missing + ".", st["note"]))
            continue
        out.append(KeepTogether(lead + [
            img, _para("Fig. %d [%s] — %s"
                       % (n_fig, tag, caption_with_provenance(cap, _L, "em")),
                       st["note"])]))
    if pending_heading is not None:
        out += pending_heading
    if n_fig == 0:
        out.append(_para(EM_NO_SNAPSHOT, st["body"]))
    return out


def em_map_point(pic_duty: Optional[str], pic_src: Optional[Dict[str, Any]],
                 cfg_doc: Dict[str, Any], em: Dict[str, Any],
                 d_duty: Dict[str, Any], em_duty: Optional[str],
                 em_run: Optional[Dict[str, Any]] = None,
                 cols: Optional[List[Dict[str, Any]]] = None
                 ) -> Tuple[Optional[str], Any, Any]:
    """``(duty, rpm, current)`` the electromagnetic MAPS were solved at.

    The maps are the picture duty's stored field when it has one, else the
    machine's last snapshot — and their caption must carry THAT run's point.
    A report about the peak duty drawn with the rated duty's maps used to
    caption them "duty 'rated', 788.6 A at 20,000 rpm": the name from the
    picture duty, the point from the report duty (reviewer 2026-09-13).  The
    stored field names its own speed; the current is the picture duty's saved
    operating point, and a duty whose saved point cannot be found leaves the
    current out rather than borrowing one.
    """
    src = (pic_src or {}).get("em") if isinstance(pic_src, dict) else None
    if pic_duty and isinstance(src, dict):
        rpm = _g(src, "meta.point.rpm")
        dd = next((d for d in (cfg_doc.get("duties") or [])
                   if isinstance(d, dict) and str(d.get("name") or "") == str(pic_duty)),
                  None)
        if dd is not None:
            # The prepared column's current when there is one — the SOLVED one
            # on a voltage-fed duty (BL-2).
            _cur = (_col_of(cols, pic_duty) or {}).get("em", {}).get(
                "I_phase_rms_A")
            return (str(pic_duty), rpm if rpm is not None else dd.get("rpm"),
                    _cur or _g(dd, "summary.I_phase_rms_A")
                    or dd.get("current_arms"))
        return (str(pic_duty), rpm, None)
    # The machine's own last snapshot: the run the Simulation tab restores.
    rs = (em_run or {}).get("summary") if isinstance(em_run, dict) else None
    if isinstance(rs, dict) and rs.get("rpm") is not None:
        return (em_duty, rs.get("rpm"), rs.get("I_phase_rms_A") or rs.get("I_phase_rms"))
    return (em_duty, _g(em, "rpm") or _g(d_duty, "rpm"),
            _g(em, "I_phase_rms_A") or _g(d_duty, "current_arms"))


def point_words(rpm: Any = None, cur: Any = None) -> str:
    """``"562.1 A rms at 14,200 rpm"`` — an operating point in a sentence."""
    return " at ".join(b for b in (
        _fmt(cur, 1, "A rms") if _numf(cur) else "",
        _fmt(rpm, 0, "rpm") if _numf(rpm) else "") if b)


def _col_of(cols: Optional[List[Dict[str, Any]]],
            duty: Optional[str]) -> Optional[Dict[str, Any]]:
    """The prepared column of one duty — the place its SOLVED view lives."""
    return next((c for c in (cols or [])
                 if str(c.get("duty") or "") == str(duty or "")), None)


def duty_point(cfg_doc: Dict[str, Any], duty: Optional[str],
               cols: Optional[List[Dict[str, Any]]] = None
               ) -> Tuple[Any, Any]:
    """``(rpm, current)`` a duty of this configuration is saved at.

    The operating point a caption quotes when the picture came out of that
    duty's own stored field: the duty's prepared column first — on a PWM duty
    that is the SOLVED current, not the commanded one it missed by 3.16 %
    (BL-2) — then the saved summary, then the duty's own setpoint.
    ``(None, None)`` for a duty this configuration does not have.
    """
    col = _col_of(cols, duty)
    if col is not None and (col.get("em") or {}):
        em = col["em"]
        if _numf(em.get("I_phase_rms_A")) is not None:
            return (em.get("rpm") or (col.get("d") or {}).get("rpm"),
                    em.get("I_phase_rms_A"))
    dd = next((d for d in (cfg_doc.get("duties") or [])
               if isinstance(d, dict)
               and str(d.get("name") or "") == str(duty or "")), None)
    if dd is None:
        return (None, None)
    return (_g(dd, "summary.rpm") or dd.get("rpm"),
            _g(dd, "summary.I_phase_rms_A") or dd.get("current_arms"))


def _map_tag(map_duty: Optional[str], rpm: Any = None, cur: Any = None) -> str:
    """The short "whose picture is this" stamp that rides in every map caption.

    User 2026-09-09 wants the maps captioned per duty.  A field is stored per
    MACHINE — one at a time — so the honest caption names the duty that owns it
    and the point it was solved at, and says outright when no duty can be named.
    """
    bits = []
    if _numf(cur):
        bits.append(_fmt(cur, 1, "A rms"))
    if _numf(rpm):
        bits.append(_fmt(rpm, 0, "rpm"))
    point = " at ".join(bits) if bits else "operating point not recorded"
    return ("duty '%s', %s" % (map_duty, point) if map_duty
            else "no duty identified, %s" % point)


def map_owner_text(map_duty: Optional[str], em: Dict[str, Any],
                   d_duty: Dict[str, Any], point: Optional[str] = None,
                   from_duty: bool = False) -> str:
    """Whose operating point the maps on this page belong to.

    Field maps come out of the per-MACHINE snapshot stores, which keep exactly
    one answer — the last one solved.  The per-duty store this report's tables
    are built from holds SCALARS only; giving every duty its own |B| and
    temperature field would mean storing a mesh and a per-element array per
    duty, which is a different feature and is deliberately not built here.  So
    the maps are one duty's, and this line says which one and at what point.
    """
    if not point:
        # No picture point handed in: the report duty's own (the maps ARE its
        # run when nothing else was stored).
        rpm = _g(em, "rpm") or _g(d_duty, "rpm")
        cur = _g(em, "I_phase_rms_A") or _g(d_duty, "current_arms")
        point = "%s at %s" % (_fmt(cur, 1, "A rms"), _fmt(rpm, 0, "rpm"))
    if map_duty and from_duty:
        # THE DUTY'S OWN SAVED FIELD (reviewer 2026-09-14, B12).  The maps are
        # read back from the duty's own stored field, exactly as the thermal
        # and mechanical equivalents already said.
        return (
            "These maps are the stored FIELD of the duty '%s' at %s. A duty "
            "saved without its field has no map of its own."
            % (map_duty, point))
    if map_duty:
        return (
            "These maps are the stored FIELD of the duty '%s' at %s. Only the "
            "last-solved duty has a stored field; the other duties above have "
            "no map of their own." % (map_duty, point))
    # …and with no duty to attribute them to, they are simply the last stored
    # field, at the point they were solved at.  The old wording named the
    # server and the duty it had loaded (client review 2026-09-14).
    return (
        "These maps are the last stored field, at %s; no duty of this "
        "configuration owns them." % point)


# ── page 4 · thermal ────────────────────────────────────────────────────────


def _cooling_words(cooling: Dict[str, Any]) -> List[str]:
    """The boundary conditions in words — what the solve was actually told.

    Read off the surface reports ``simulation.cooling_models`` builds (``mode``,
    ``h_conv``, ``t_sink_c``, ``t_in_c`` / ``t_out_c``, ``flow_lpm``,
    ``air_speed_mps``), so the sentence here and the panel's own line can only
    disagree if the payload does.
    """
    out: List[str] = []

    def _surface(rep: Dict[str, Any], where: str, off: str) -> str:
        mode = str(rep.get("mode") or "none").lower()
        h = _fmt(rep.get("h_conv"), 0, "W/m²K")
        if mode.startswith("non"):
            return f"{where}: {off}"
        # STILL AIR (2026-09-14, the robotics mode): the housing reports
        # `robotics`, the unventilated bore `still`, and both carry TWO films.
        # The line names both, because on a small housing radiation is more
        # than half of the cooling and a single "h = 12" hides that the ε the
        # user typed is doing most of the work.
        if mode.startswith("robot") or mode == "still":
            try:
                eps = "%.2f" % float(rep.get("emissivity") or 0.0)
            except (TypeError, ValueError):
                eps = "—"
            return ("%s: still air at %s, h %s convection + %s radiation "
                    "(ε %s) = %s" % (
                        where, _fmt(rep.get("t_sink_c"), 0, "°C"),
                        _fmt(rep.get("h_conv"), 1), _fmt(rep.get("h_rad"), 1),
                        eps, _fmt(rep.get("h_total"), 1, "W/m²K")))
        if mode.startswith("liq"):
            # No h on a jacket line (user 2026-09-11: "а зачем он нужен?"):
            # the wall is PINNED at the outlet temperature, and the 1e5 that
            # imposes that is a device, not a property of the cooling.
            return ("%s: %s jacket, in %s -> out %s at %s; wall held at the "
                    "outlet temperature" % (
                where, rep.get("fluid") or "coolant",
                _fmt(rep.get("t_in_c"), 1, "°C"), _fmt(rep.get("t_out_c"), 1, "°C"),
                _fmt(rep.get("flow_lpm"), 2, "L/min")))
        if mode.startswith("air"):
            return ("%s: air at %s, sink %s (h = %s)" % (
                where, _fmt(rep.get("air_speed_mps"), 2, "m/s"),
                _fmt(rep.get("t_sink_c"), 1, "°C"), h))
        return ("%s: fixed film h = %s at %s" % (
            where, h, _fmt(rep.get("t_sink_c"), 1, "°C")))

    out.append(_surface(cooling.get("outer") or {}, "Housing",
                        "adiabatic — no heat leaves the outer surface"))
    out.append(_surface(cooling.get("inner") or {}, "Rotor bore",
                        "closed — no bore cooling"))
    se = cooling.get("shaft_ends") or {}
    if se and float(se.get("heat_removed_W") or 0.0):
        out.append("Shaft ends: %s exposed each side, %s removed" % (
            _fmt(se.get("length_each_side_mm"), 1, "mm"),
            _fmt(se.get("heat_removed_W"), 1, "W")))
    else:
        out.append("Shaft ends: not a heat path in this solve")
    # ── THE MOUNT (2026-09-14) ──────────────────────────────────────────────
    # An INPUT, and on a joint in still air the one that decides the
    # temperature: the housing hands the room a few watts and the bolts take
    # the rest.  The line says the conductance that was given, the structure
    # temperature it was given against and what it removed, so the identity
    # (G · ΔT) is checkable from the sentence itself.  Absent on a record
    # written before the mode existed — no line then, not a blank one.
    mt = cooling.get("mount")
    if isinstance(mt, dict) and mt:
        if str(mt.get("mode") or "off").lower().startswith("cond") and _numf(
                mt.get("G_W_per_K")):
            out.append("Mount: %s to %s structure, %s removed" % (
                _fmt(mt.get("G_W_per_K"), 2, "W/K"),
                _fmt(mt.get("t_sink_c"), 0, "°C"),
                _fmt(mt.get("heat_removed_W"), 1, "W")))
        else:
            out.append("Mount: not a heat path in this solve")
    # ── THE AXIAL END FACES (2026-09-14) ────────────────────────────────────
    # Four paths out of the two ends of an open machine — the end turns above
    # all, which stand proud of the core and see the room directly.  One line,
    # part by part, because "end faces: 6.4 W" says nothing about which face.
    ef = cooling.get("end_faces")
    if isinstance(ef, dict) and ef:
        if str(ef.get("mode") or "off").lower() == "off":
            out.append("End faces: not a heat path in this solve")
        else:
            _sides = _numf(ef.get("sides"))
            bits = []
            for _k, _lbl in (("winding", "winding"), ("stator", "stator"),
                             ("rotor", "rotor"), ("magnet", "magnets")):
                _b = ef.get(_k)
                if isinstance(_b, dict) and _b.get("heat_removed_W") is not None:
                    bits.append("%s %s" % (_lbl, _fmt(_b.get("heat_removed_W"),
                                                      1, "W" if not bits else "")))
            out.append("End faces (%s): %s" % (
                "both sides" if _sides == 2 else
                ("one side" if _sides == 1 else _fmt(_sides, 0, "sides")),
                ", ".join(bits) if bits else _fmt(ef.get("heat_removed_W"), 1, "W")))
    g = cooling.get("gap") or {}
    if g:
        out.append("Air gap: k_eff = %s W/m·K, Taylor %s, Nu %s — derived from the "
                   "clearance and the speed, never typed in" % (
                       _fmt(g.get("k_eff"), 4), _fmt(g.get("Ta"), 1),
                       _fmt(g.get("Nu"), 2)))
    return out


THERMAL_PAGE_UNSOLVED = (
    "Not solved yet — no temperature map is stored. Open the Thermal tab, set "
    "the cooling and press Solve; the map is then part of every later report.")

THERMAL_MAP_MISSING = (
    "The temperature map image is not stored on this result (the per-node array "
    "was stripped).")

THERMAL_MAP_CAPTION = (
    "Steady-state temperature over the solved cross-section, °C, from the "
    "cycle-averaged loss map of the electromagnetic run named above. The "
    # CS-6 (audit v7): the bar topped at 135.2 °C where the caption and every
    # table said 135.3, and 206.4 against 206.7 on the peak duty.  The picture
    # is drawn from the area-weighted nodal average of each material class —
    # that is what keeps a boundary from bleeding — so its bar ends a few
    # tenths under the part maximum the tables quote.  Said, not chased.
    "bar is the drawn field's own area-averaged range, a few tenths under the "
    "part maxima in the tables.")


def thermal_map_owner_text(map_duty: Optional[str], from_duty: bool = False,
                           point: str = "",
                           sec: Optional[Dict[str, int]] = None) -> str:
    """Whose temperature field the picture on the thermal page is.

    ``from_duty`` — the map was read back from THAT duty's own stored field
    (``pic_src['thermal']``), which is the statement the document makes; the
    branches that named the server instead went on 2026-09-14 (client review).
    ``sec`` — this document's section numbering, for the cross-reference
    (BT-5: it used to say "page 3", which is "1 · Machine").
    """
    at = (" at %s" % point) if point else ""
    if map_duty and from_duty:
        return ("The temperature map below is the stored field of the duty "
                "'%s'%s. Per-duty temperatures are in %s."
                % (map_duty, at, compare_ref(sec)))
    if map_duty:
        return ("The temperature map below belongs to the duty '%s'%s. "
                "Per-duty temperatures are in %s."
                % (map_duty, at, compare_ref(sec)))
    return ("The temperature map below is the last stored temperature field; "
            "no duty of this configuration owns it, so it is not attributed.")


def thermal_source_text(th: Dict[str, Any], entry: Dict[str, Any],
                        duty: Optional[str] = None,
                        from_duty: bool = False) -> str:
    # …and no solve timestamp here either (CS-7) — see `mech_source_text`.
    #
    # WHOSE ANSWER IT IS (BL-1, audit v6).  With a foreign machine loaded the
    # tab store is dropped and this section reads the duty's OWN record, so the
    # line that says "the Thermal tab's last map" would name a store this page
    # is not built from.
    what = "coupled run" if th.get("field") is None else "map"
    if from_duty and duty:
        return ("Source: the thermal %s stored for the duty '%s'. Steady state."
                % (what, duty))
    return "Source: the Thermal tab's last %s. Steady state." % what


def thermal_temp_rows(res: Dict[str, Any],
                      inner: Dict[str, Any]) -> List[List[str]]:
    """Per-part max/avg temperatures.  Header row included; one row means the
    solve carried no per-component breakdown."""
    comps = res.get("components") or inner.get("components") or {}
    order = [("winding", "Winding (copper)"), ("enamel", "Wire enamel"),
             ("liner", "Slot insulation"), ("slot_fill", "Slot fill / impregnation"),
             ("magnet", "Magnets"), ("rotor", "Rotor core"), ("shaft", "Shaft"),
             ("sleeve", "Retaining sleeve"), ("stator", "Stator core"),
             ("gap_air", "Air gap")]
    trows = [["Part", "max / avg [°C]"]]
    for key, label in order:
        c = comps.get(key)
        if not isinstance(c, dict):
            continue
        trows.append([label, _pair(_numf(c.get("max")), _numf(c.get("avg")))])
    return trows


#: The thermal mesh's AIR and coolant domain ids — everything that is not metal
#: or insulation.  From the thermal result's own `part_names` map (0 air,
#: 3 airgap, 8 outer_air, 64 air gap, 65 pocket air, 66 bore air,
#: 67 unclassified air, 68 outer cut/coolant).
_TH_AIR_IDS = (0, 3, 8, 64, 65, 66, 67, 68)


def _thermal_solid_extremes(inner: Dict[str, Any]
                            ) -> Tuple[Optional[float], Optional[float]]:
    """``(min, max)`` over the nodes of SOLID elements, or ``(None, None)``."""
    try:
        import numpy as np
        T = np.asarray(inner.get("temperature_per_node"), float).ravel()
        tri = np.asarray(inner.get("triangles"), dtype=np.int64)
        dom = np.asarray(inner.get("domain_per_tri")).ravel()
        if tri.ndim != 2 or tri.shape[1] != 3:
            tri = tri.T
        if T.size == 0 or tri.shape[0] != dom.size:
            return None, None
        keep = tri[~np.isin(dom.astype(np.int64), _TH_AIR_IDS)]
        if keep.size == 0:
            return None, None
        return float(np.nanmin(T[keep])), float(np.nanmax(T[keep]))
    except Exception:                                       # noqa: BLE001
        return None, None


def thermal_solid_max_c(inner: Dict[str, Any]) -> Optional[float]:
    """The hottest node of a SOLID element, or ``None`` without the field.

    CS-9 (reviewer 2026-09-14): "Hottest point in the machine 108.8 °C" is the
    air in the gap on the rated duty, judged in section 8 against a WINDING
    insulation limit, while its sibling clause carefully says "coolest SOLID
    point".  The hottest is qualified the same way now.
    """
    return _thermal_solid_extremes(inner or {})[1]


def thermal_solid_min_c(inner: Dict[str, Any]) -> Optional[float]:
    """The coolest node of a SOLID element, or ``None`` without the field.

    ``T_min`` as the solver stores it is the minimum over the whole solved
    mesh, air and coolant cut included, and a report that quotes it beside a
    jacket wall temperature invites "no interior node can be below the wall"
    (reviewer 2026-09-14, C2).  On the L155 the two answers turn out to be the
    same node — the coolest point IS solid, the outermost stator node under the
    jacket — but the document should be able to say so rather than assume it.
    """
    try:
        import numpy as np
        T = np.asarray(inner.get("temperature_per_node"), float).ravel()
        tri = np.asarray(inner.get("triangles"), dtype=np.int64)
        dom = np.asarray(inner.get("domain_per_tri")).ravel()
        if tri.ndim != 2 or tri.shape[1] != 3:
            tri = tri.T
        if T.size == 0 or tri.shape[0] != dom.size:
            return None
        keep = tri[~np.isin(dom.astype(np.int64), _TH_AIR_IDS)]
        if keep.size == 0:
            return None
        return float(np.nanmin(T[keep]))
    except Exception:                                       # noqa: BLE001
        return None


def thermal_extremes_text(res: Dict[str, Any], inner: Dict[str, Any]) -> str:
    t_max = res.get("T_max", inner.get("T_max"))
    t_min = res.get("T_min", inner.get("T_min"))
    solid, solid_hot = _thermal_solid_extremes(inner or {})
    if solid is None:
        return ("Hottest point in the machine %s, coolest %s (over the whole "
                "solved mesh, the gap and bore air included)."
                % (_fmt(t_max, 1, "°C"), _fmt(t_min, 1, "°C")))
    # BOTH ENDS QUALIFIED THE SAME WAY (CS-9): the hottest node of the mesh is
    # often the air in the gap, and section 8 judges METAL against the
    # insulation class, so the solid maximum is named beside it.
    hot = "Hottest point in the machine %s" % _fmt(t_max, 1, "°C")
    if (solid_hot is not None and _numf(t_max) is not None
            and float(_numf(t_max)) - solid_hot > 0.05):
        hot += (" (gap or bore AIR; hottest SOLID point %s)"
                % _fmt(solid_hot, 1, "°C"))
    tail = ""
    if _numf(t_min) is not None and abs(solid - float(_numf(t_min))) > 0.05:
        tail = (" — the whole-mesh minimum is %s, in air" % _fmt(t_min, 1, "°C"))
    tail += thermal_wall_note(res, inner, solid)
    return "%s, coolest SOLID point %s%s." % (hot, _fmt(solid, 1, "°C"), tail)


def thermal_wall_note(res: Dict[str, Any], inner: Dict[str, Any],
                      solid_min: Optional[float]) -> str:
    """Why the coolest node may read a few tenths BELOW the jacket wall (CS-1).

    Reviewer 2026-09-14 (C2, again): "coolest point 65.1 °C against a jacket
    wall held at the outlet 65.3 °C — in a conduction solve with the jacket as
    the sink no interior node can sit below the wall."  Correct, and the
    explanation is the boundary condition itself: the wall is not a Dirichlet
    temperature, it is a film with h = 1e5 W/m²K that PINS it, and the finite
    element solution of a Robin condition that stiff lands within a few tenths
    of a kelvin either side of the pinned value.  The clause is printed only
    when the gap is real and small; anything larger is not a rounding of the
    boundary condition and must not be explained away as one.
    """
    if solid_min is None:
        return ""
    cool = (res.get("cooling") or inner.get("cooling") or {})
    rep = cool.get("outer") or {}
    if not str(rep.get("mode") or "").lower().startswith("liq"):
        return ""
    sink = _numf(rep.get("t_sink_c"))
    h = _numf(rep.get("h_conv")) or 0.0
    if sink is None or h < 1e4:
        return ""
    gap = sink - solid_min
    if not (0.005 < gap <= 1.0):
        return ""
    return (" — %s under the %s the jacket wall is pinned at, which is the "
            "film (h = %s) resolved on the mesh, not heat from the coolant"
            % (_fmt(gap, 2, "K"), _fmt(sink, 1, "°C"),
               _fmt(h, 0, "W/m²K")))


def thermal_budget_rows(res: Dict[str, Any],
                        inner: Dict[str, Any],
                        em: Optional[Dict[str, Any]] = None) -> List[List[str]]:
    """What went into the map and what left it, surface by surface.  Header row
    included; the residual is the closure error of the whole solve.  ``em`` is
    the duty's electromagnetic summary, for the one loss the map does NOT
    carry (the band's eddy loss), named so the two totals reconcile."""
    cooling = res.get("cooling") or inner.get("cooling") or {}
    budget = cooling.get("heat_budget") or {}
    brows = [["Heat budget", "W"]]
    # `losses_W` is the integral of the loss density over the solved mesh — the
    # heat this map actually carries, mechanical share included.  Reading the
    # electromagnetic total here made the rows not add up (reviewer
    # 2026-09-11: "7357.9 − 7120.4 = 237.5 W, the residual should be −237.5").
    # It was never a residual: it was the bearings and windage in the map.
    brows.append(["Losses put in (the loss map's integral)", _fmt(
        budget.get("losses_W") or budget.get("P_in_W")
        or res.get("P_loss_total_W", inner.get("P_loss_total_W")), 1)])
    if budget.get("mech_loss_in_map_W"):
        brows.append(["  of which mechanical (bearing seats, gap windage)",
                      _fmt(budget.get("mech_loss_in_map_W"), 1)])
    brows.append(["  of which copper", _fmt(
        res.get("P_cu_W", inner.get("P_cu_W")), 1)])
    brows.append(["  of which iron", _fmt(
        res.get("P_fe_W", inner.get("P_fe_W")), 1)])
    # …and the rest of the map's integral — the solid-conductor eddy terms
    # (magnets and shaft) the loss map carries by element and the record does
    # not name.  Without this row the "of which" list came ~300 W short of
    # the total (reviewer 2026-09-13, item 6).
    try:
        _tot = float(budget.get("losses_W") or budget.get("P_in_W")
                     or res.get("P_loss_total_W", inner.get("P_loss_total_W")) or 0.0)
        _rest = (_tot - float(res.get("P_cu_W", inner.get("P_cu_W")) or 0.0)
                 - float(res.get("P_fe_W", inner.get("P_fe_W")) or 0.0)
                 - float(budget.get("mech_loss_in_map_W") or 0.0))
        if _rest > 0.5:
            brows.append(["  of which solid conductors (magnet + shaft eddy, in the map)",
                          _fmt(_rest, 1)])
    except (TypeError, ValueError):
        pass
    # The band's eddy loss is NOT in the map (no loss model for the sleeve in
    # the thermal solve) — the gap between the electromagnetic total and the
    # map's total, named (reviewer 2026-09-13, item 9).
    _slv = _numf(_g(em or {}, "P_sleeve_W"))
    if _slv is not None and _slv > 0.05:
        brows.append(["  not in the map: sleeve eddy (no loss model for the band)",
                      _fmt(_slv, 1)])
    brows.append(["Removed through the housing",
                  _fmt((cooling.get("outer") or {}).get("heat_removed_W"), 1)])
    # WHICH HALF LEFT AS LIGHT (2026-09-14).  Only the still-air film has two
    # mechanisms; every other mode reports its whole heat as convection, so the
    # pair is printed only when radiation actually carried something — on a Ø85
    # housing at ΔT 60 K it is more than half, which is the point.
    _rad_w = _numf(budget.get("housing_radiation_W"))
    _cnv_w = _numf(budget.get("housing_convection_W"))
    if _rad_w is not None and _cnv_w is not None and _rad_w > 0.005:
        brows.append(["  of which convection", _fmt(_cnv_w, 1)])
        brows.append(["  of which radiation", _fmt(_rad_w, 1)])
    brows.append(["Removed through the bore",
                  _fmt((cooling.get("inner") or {}).get("heat_removed_W"), 1)])
    brows.append(["Removed through the shaft ends",
                  _fmt((cooling.get("shaft_ends") or {}).get("heat_removed_W"), 1)])
    # ── THE MOUNT (2026-09-14) ──────────────────────────────────────────────
    # The bolted flange.  Absent from a record written before the mode existed,
    # and skipped on a machine bolted to nothing — 0 W under a row nobody asked
    # for is the blank row this table has been pruned of twice.
    _mt = cooling.get("mount")
    _mt_w = _numf((_mt or {}).get("heat_removed_W"))
    if isinstance(_mt, dict) and _mt and (
            _mt_w or str(_mt.get("mode") or "off").lower() != "off"):
        brows.append(["Removed into the mount", _fmt(_mt_w, 1)])
    # ── THE AXIAL END FACES (2026-09-14) ────────────────────────────────────
    _ef = cooling.get("end_faces")
    _ef_w = _numf((_ef or {}).get("heat_removed_W"))
    if isinstance(_ef, dict) and _ef and (
            _ef_w or str(_ef.get("mode") or "off").lower() != "off"):
        brows.append(["Removed off the end faces", _fmt(_ef_w, 1)])
        for _k, _lbl in (("winding", "  of which the end windings"),
                         ("stator", "  of which the stator core's ends"),
                         ("rotor", "  of which the rotor core's ends"),
                         ("magnet", "  of which the magnet ends")):
            _b = _ef.get(_k)
            if isinstance(_b, dict) and _b.get("heat_removed_W") is not None:
                brows.append([_lbl, _fmt(_b.get("heat_removed_W"), 1)])
    for k, label in (("residual_W", "Residual (closure error)"),
                     ("P_gap_W", "Across the air gap")):
        if budget.get(k) is not None:
            brows.append([label, _fmt(budget.get(k), 2)])
    # THE ROTOR's own balance: the two ways its heat leaves this cross-section
    # (user 2026-09-10 — "через зазор и через вал").  Every watt made inside the
    # slip radius goes one way or the other, so the shares are the cooling
    # design: a rotor that has to be cooled through the shaft wants the second
    # number to be the big one.
    sp = budget.get("rotor_heat_split")
    if isinstance(sp, dict) and sp.get("rotor_W") is not None:
        def _pw(w, p):
            return ("%s  (%s)" % (_fmt(w, 1), _fmt(p, 0, "%"))
                    if p is not None else _fmt(w, 1))
        # NAMED FOR WHAT IT CONTAINS (reviewer 2026-09-14, C1): 201.4 W against
        # an electromagnetic rotor loss of 180.5 W is not a discrepancy, it is
        # the gap-windage share the map credits to the rotor surface — and
        # nothing on the page said so.
        brows.append(["Made in the rotor (rotor loss + the gap windage "
                      "credited to it)", _fmt(sp.get("rotor_W"), 1)])
        brows.append(["  out across the air gap",
                      _pw(sp.get("gap_W"), sp.get("gap_pct"))])
        brows.append(["  out off the bore surface",
                      _pw(sp.get("bore_W"), sp.get("bore_pct"))])
        if sp.get("axial_shaft_ends_W"):
            brows.append(["  axially, down the shaft ends",
                          _pw(sp.get("axial_shaft_ends_W"),
                              sp.get("axial_shaft_ends_pct"))])
        if sp.get("axial_end_faces_W"):
            brows.append(["  axially, off the rotor and magnet end faces",
                          _pw(sp.get("axial_end_faces_W"),
                              sp.get("axial_end_faces_pct"))])
        # No "rotor balance closes to" row: it is the residual of the three
        # rows above (a fraction of a watt when the split is sound) and the
        # reader does not audit it — user 2026-09-11, "выкинь".
    # ── …AND THE STATOR's (2026-09-14) ──────────────────────────────────────
    # The mirror of the block above, and on a joint in still air it is THE
    # question: the housing hands the room ~3 W of 64 and the BOLTS take the
    # rest, so "how much leaves through the mount" is the number the flange is
    # designed from.  Absent from a record written before the mode existed.
    ss = budget.get("stator_heat_split")
    if isinstance(ss, dict) and ss.get("total_in_W") is not None:
        def _pw2(w, p):
            return ("%s  (%s)" % (_fmt(w, 1), _fmt(p, 0, "%"))
                    if p is not None else _fmt(w, 1))
        brows.append(["Made in the stator side", _fmt(ss.get("stator_W"), 1)])
        brows.append(["  in across the air gap", _fmt(ss.get("gap_in_W"), 1)])
        brows.append(["  = into the stator side, in total",
                      _fmt(ss.get("total_in_W"), 1)])
        brows.append(["  out through the housing",
                      _pw2(ss.get("housing_W"), ss.get("housing_pct"))])
        if ss.get("mount_W"):
            brows.append(["  out into the mount",
                          _pw2(ss.get("mount_W"), ss.get("mount_pct"))])
        if ss.get("end_faces_W"):
            brows.append(["  out off the end faces",
                          _pw2(ss.get("end_faces_W"), ss.get("end_faces_pct"))])
        if ss.get("end_windings_W"):
            brows.append(["  out off the end windings (open frame)",
                          _fmt(ss.get("end_windings_W"), 1)])
        if ss.get("slot_channels_W"):
            brows.append(["  out down the slot ducts (open frame)",
                          _fmt(ss.get("slot_channels_W"), 1)])
    # ── THE HEADLINE: which SIDE of the machine the heat leaves from ────────
    # The user's own question (2026-09-14).  Every watt that leaves this model
    # leaves through a stator-side door (the housing film, the bolted mount,
    # the stator-side end faces and the open frame's two paths) or a rotor-side
    # one (the bore, the shaft stubs, the rotor and magnet end faces); the gap
    # is INTERNAL and is on neither list.  The shares are of the two together,
    # so they are a split of what actually left and not of what was made.
    _st_side, _ro_side = _heat_sides(cooling)
    if _st_side is not None and _ro_side is not None:
        _tot_out = _st_side + _ro_side
        _pct = (lambda w: (100.0 * w / _tot_out) if abs(_tot_out) > 1e-9 else None)
        brows.append(["Stator side (housing + mount + end faces) [W]",
                      _fmt(_st_side, 1)])
        brows.append(["…as a share of everything that left [%]",
                      _fmt(_pct(_st_side), 0)])
        brows.append(["Rotor side (bore + shaft + end faces) [W]",
                      _fmt(_ro_side, 1)])
        brows.append(["…as a share of everything that left [%]",
                      _fmt(_pct(_ro_side), 0)])
    return brows


def _heat_sides(cooling: Dict[str, Any]
                ) -> Tuple[Optional[float], Optional[float]]:
    """``(stator-side W, rotor-side W)`` — which SIDE of the machine the heat
    that left this cross-section left from.

    The user's headline question on the robot joint (2026-09-14): the stator
    side is the housing film, the bolted mount, the stator-side axial end faces
    (the end turns and the core's own end annulus) and — on an open frame — the
    end windings and the slot ducts; the rotor side is the bore surface, the
    exposed shaft stubs and the rotor / magnet end faces.  The air gap is an
    INTERNAL transfer and is on neither list, which is why this is not simply
    ``rotor_heat_split`` read upside down.

    ``(None, None)`` on a record that carries no ``stator_heat_split`` — the
    split is a statement about paths half of which that record never had.
    """
    budget = (cooling or {}).get("heat_budget") or {}
    ss = budget.get("stator_heat_split")
    rs = budget.get("rotor_heat_split")
    if not isinstance(ss, dict) or ss.get("total_in_W") is None:
        return None, None
    st = sum(_numf(ss.get(k)) or 0.0 for k in
             ("housing_W", "mount_W", "end_faces_W", "end_windings_W",
              "slot_channels_W"))
    ro = sum(_numf((rs or {}).get(k)) or 0.0 for k in
             ("bore_W", "axial_shaft_ends_W", "axial_end_faces_W"))
    return st, ro


def thermal_budget_gap_w(budget: Optional[Dict[str, Any]],
                         em: Optional[Dict[str, Any]]) -> Optional[float]:
    """The loss map's own integration error, as ONE comparison.

    ``integral − (electromagnetic total − the sleeve's eddy loss + the
    mechanical loss inside the section)`` — exactly the arithmetic
    :func:`thermal_budget_reconcile_text` spells out, so the comparison row in
    section 3 and the paragraph under the heat budget cannot print two
    different answers to one question.  They did: 29.1 W against 0.1 W, and the
    29.1 was the shaft's eddy loss, which the budget's own
    ``em_loss_total_W`` leaves out while the map's integral contains it
    (reviewer 2026-09-14, B3).  ``None`` when the record cannot form the sum.
    """
    b = budget if isinstance(budget, dict) else {}
    integral = _numf(b.get("losses_W") or b.get("P_in_W"))
    em_tot = _numf(_g(em or {}, "P_loss_total_W"))
    if integral is None or em_tot is None:
        return None
    slv = _numf(_g(em or {}, "P_sleeve_W")) or 0.0
    mech = _numf(b.get("mech_loss_in_map_W")) or 0.0
    return integral - (em_tot - slv + mech)


#: How far the loss map's integral may sit from the summed terms before the
#: closure sentence stops calling the difference an integration error (MJ-7).
#: This project's own records close to tenths of a watt; 5 % (or 25 W, whichever
#: is larger) is far outside anything a quadrature does and inside nothing a
#: mixed pair of solves produces.
RECONCILE_MAX_GAP_PCT = 5.0
RECONCILE_MAX_GAP_W = 25.0


def thermal_budget_reconcile_text(res: Dict[str, Any], inner: Dict[str, Any],
                                  em: Optional[Dict[str, Any]] = None,
                                  duty: Optional[str] = None,
                                  em_duty: Optional[str] = None) -> str:
    """The one line that makes the heat budget add up, with the duty named.

    Reviewer 2026-09-14: the page said "Losses put in 3 794.3 W, mechanical
    61 W" beside an electromagnetic total of 3 742.9 W and did not say which
    duty it was about or how the three numbers relate.  They relate by
    arithmetic, and every term of it is in the record — the electromagnetic
    total, the sleeve's eddy loss (which has no model in the thermal solve and
    is therefore NOT in the map), and the mechanical loss that IS inside the
    cross-section.  ``""`` when the record cannot form the sum.

    ONE DUTY ON BOTH SIDES OF THE SUBTRACTION (BL-1, audit v5).  ``duty`` is
    the duty the MAP belongs to and ``em_duty`` the duty the electromagnetic
    total belongs to; with ``pictures=`` still driving this section they could
    differ, and the sentence then printed *"duty 'peak': electromagnetic
    3,830.3 W … integration of the map 7,963.5 W — 4,001.9 W apart, which is
    the loss map's own integration error"*: a 4 kW discrepancy manufactured by
    mixing two duties and then blamed on the integrator, in a client document.
    Both names are passed in now and the mismatch is an assertion, so it cannot
    be printed at all.
    """
    assert duty is None or em_duty is None or str(duty) == str(em_duty), (
        "heat-budget closure would subtract duty %r's electromagnetic loss "
        "from duty %r's map integral" % (em_duty, duty))
    cooling = res.get("cooling") or inner.get("cooling") or {}
    budget = cooling.get("heat_budget") or {}
    integral = _numf(budget.get("losses_W") or budget.get("P_in_W")
                     or res.get("P_loss_total_W", inner.get("P_loss_total_W")))
    em_tot = _numf(_g(em or {}, "P_loss_total_W"))
    if integral is None or em_tot is None:
        return ""
    slv = _numf(_g(em or {}, "P_sleeve_W")) or 0.0
    mech = _numf(budget.get("mech_loss_in_map_W")) or 0.0
    expect = em_tot - slv + mech
    bits = ["electromagnetic %s" % _fmt(em_tot, 1, "W")]
    if slv:
        bits.append("minus the sleeve's %s (not in the map)" % _fmt(slv, 1, "W"))
    if mech:
        bits.append("plus %s of mechanical loss inside the section"
                    % _fmt(mech, 1, "W"))
    # …and WHAT "made in the rotor" contains, when the split is on the page
    # (reviewer 2026-09-14, C1): the rotor's own electromagnetic loss plus the
    # share of the gap windage the map credits to the rotor surface, which is
    # why that row reads above `P_loss_rotor_W`.
    # …AND IT DIFFERENCES WHAT IS IN THE MAP AGAINST WHAT IS IN THE MAP (BT-3,
    # 2026-09-16).  `P_loss_rotor_W` contains the SLEEVE, which the clause two
    # sentences above states is NOT in the map, so the sentence reconciled
    # 630.9 W against 581.6 W and called the 49.3 W difference gap windage
    # while the map's own rotor loss is 539.2 W and the windage credited to the
    # rotor surface is 91.7 W — under-stated 1.9× and contradicting an adjacent
    # row of the same table (L180 gen 'rated').  The band is subtracted here
    # and both halves are named.
    tail = ""
    sp = budget.get("rotor_heat_split")
    ro_em = _numf(_g(em or {}, "P_loss_rotor_W"))
    ro_map = None if ro_em is None else ro_em - slv
    if isinstance(sp, dict) and _numf(sp.get("rotor_W")) is not None \
            and ro_map is not None and abs(_numf(sp["rotor_W"]) - ro_map) > 0.5:
        tail = (" \"Made in the rotor\" below is %s against the rotor loss the "
                "map carries, %s%s; the %s difference is gap windage credited "
                "to the rotor surface."
                % (_fmt(sp["rotor_W"], 1, "W"), _fmt(ro_map, 1, "W"),
                   ("" if not slv else
                    " (the electromagnetic %s less the sleeve's %s)"
                    % (_fmt(ro_em, 1, "W"), _fmt(slv, 1, "W"))),
                   _fmt(abs(_numf(sp["rotor_W"]) - ro_map), 1, "W")))
    # A GAP THIS SIZE IS NOT AN INTEGRATOR (MJ-7, audit v6).  The closure line
    # blamed a 5,117.9 W difference on "the loss map's own integration error"
    # in a client document: the watts were this machine's and the map integral
    # another machine's, and no quadrature is wrong by seven eighths of the
    # quantity it integrates.  Past this band the sentence REFUSES to difference
    # the two and says what it cannot do — a genuine integration error on this
    # project's records is tenths of a watt.
    gap = abs(integral - expect)
    if expect and gap > max(RECONCILE_MAX_GAP_PCT / 100.0 * abs(expect),
                            RECONCILE_MAX_GAP_W):
        return ("%s%s = %s, and the map integrates to %s — %s apart. The two "
                "are not the same solve, so no closure is stated here; the "
                "comparison row in the table above is the one to read."
                % ("duty '%s': " % duty if duty else "", ", ".join(bits),
                   _fmt(expect, 1, "W"), _fmt(integral, 1, "W"),
                   _fmt(gap, 1, "W")))
    return ("%s%s = %s; integration of the map %s%s.%s"
            % ("duty '%s': " % duty if duty else "",
               ", ".join(bits), _fmt(expect, 1, "W"), _fmt(integral, 1, "W"),
               (" — %s apart, which is the loss map's own integration error"
                % _fmt(gap, 1, "W"))
               if gap > 0.5 else "", tail))


def coupled_loop_text(cp: Dict[str, Any]) -> str:
    """The one-sentence state of the coupled loop, for the thermal page.

    THREE VERDICTS, NOT TWO (2026-09-16).  ``converged`` is an AND of the
    temperatures and the operating point, and this sentence printed it as
    "NOT converged" in front of the very residuals — 0.15 K, 0.26 K, 0.1 K
    against ± 2 K and ± 5 K — that say the temperatures converged (L180 gen
    'rated').  What did not converge there is the point, and the flagged
    sentence under this one says so; this one answers for the temperatures it
    is about.
    """
    c = (cp or {}).get("coupling") or {}
    if c.get("converged"):
        _verdict = "converged"
    elif coupled_temps_settled(c) is True:
        _verdict = "the TEMPERATURES converged; the operating point did not"
    else:
        _verdict = "NOT converged"
    txt = ("Winding %s, magnets %s after %s electromagnetic run(s) — %s "
           "(tolerance ± %s K, coil residual %s K, magnet residual %s K" % (
               _fmt(c.get("coil_temp_c"), 1, "°C"),
               _fmt(c.get("magnet_temp_c"), 1, "°C"),
               _fmt(c.get("iterations"), 0), _verdict,
               _fmt(c.get("tol_K"), 1), _fmt(c.get("residual_coil_K"), 2),
               _fmt(c.get("residual_magnet_K"), 2)))
    if c.get("residual_bearing_K") is not None:
        txt += ", bearing seat residual %s K against ± %s K" % (
            _fmt(c.get("residual_bearing_K"), 1), _fmt(c.get("tol_bearing_K"), 1))
    return txt + ")."


# ---------------------------------------------------------------------------
# 5 · PWM influence
# ---------------------------------------------------------------------------
# USER DECISION, 2026-09-14: the report is NOT restructured.  Every other page
# of it is the sinusoidal supply, and it stays that way — this one section says,
# in one place, how much worse the machine gets when a real two-level inverter
# feeds it instead.
#
# It reads exactly one thing: the "pwm" record of each duty
# (:func:`motor_ai_sim.duty_results.compact_pwm`).  A duty with no such record
# gets ONE line — the same refusal every other section makes, for the same
# reason: a carrier's cost measured on one duty is not another duty's.

PWM_HEADING = "5 · PWM influence"

PWM_INTRO = (
    "Every other section of this report is the machine on a SINUSOIDAL supply; "
    "this section is the measured difference, one FEM run per carrier against "
    "a sinusoidal run at the same time step, mesh and temperatures.")

PWM_NOT_MEASURED = (
    "PWM influence not measured on this duty — the numbers elsewhere in this "
    "report are the sinusoidal supply.")

PWM_NO_RECORD_AT_ALL = (
    "No duty of this configuration has a PWM measurement, so every number in "
    "this report is the sinusoidal supply.")

PWM_RIPPLE_NOT_QUOTABLE = "not quotable"

PWM_RIPPLE_FOOTNOTE = (
    "Torque ripple reads \"" + PWM_RIPPLE_NOT_QUOTABLE + "\" when the run's "
    "phase current ended with a DC offset.")


def _pwm_record(col: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """One duty's stored PWM measurement, or ``None``."""
    v = (col.get("res") or {}).get("pwm")
    return v if isinstance(v, dict) and (v.get("cases") or v.get("baseline")) else None


# ── THE STUDY'S POINT IS NOT ALWAYS THE REPORT'S POINT ──────────────────────
# Client review, 2026-09-14.  The carrier study of the L155 was measured at the
# duty's THEN-saved point — 750.9 A line, 134.7 °C winding, 7,689.3 W of
# sinusoidal loss — and the duty has since been re-saved 2.6 % higher, at
# 770.5 A / 139.2 °C, where sections 3 and 4 print 7,851.8 W.  A reader who took
# the section-5 sinusoid column for "this report's machine on a sine wave" found
# two different baselines in one document and no sentence saying why.
#
# THE RULE: the measured watts stay exactly as measured — a carrier's cost is a
# property of the point it was measured at and is never rescaled into a table
# cell.  What DOES transfer is dimensionless: the share of the loss the carrier
# adds, and the percentage points of efficiency it costs.  So the section prints
# the report's own sinusoidal loss beside the study's, one clearly-labelled
# estimate of the added watts at this report's point, and the sentence that says
# which of the two kinds of number the reader may carry forward.
#
# The point is READ FROM THE RECORD, never hard-coded: a structured ``point``
# block when a future writer stores one, otherwise the provenance line the study
# already writes ("… I_line 750.947 A, 20 000 rpm, γ 15° el, winding 134.7 °C,
# magnets 158.8 °C; …").

#: Relative agreement on current, and absolute on temperature, below which the
#: study point and the report point are the SAME point and section 5 says
#: nothing extra — the normal case, and the one the rows below must not clutter.
PWM_POINT_I_TOL = 1e-3
PWM_POINT_T_TOL_K = 0.5

PWM_REPORT_BASELINE_LABEL = "Sinusoid at this report's point, §3 [W]"
PWM_TRANSFERRED_LABEL = (
    "Added loss transferred to this report's point, estimate [W]")


def _pwm_grab(text: str, pattern: str) -> Optional[float]:
    m = re.search(pattern, text, re.I)
    if not m:
        return None
    return _numf(re.sub(r"[\s,  ]", "", m.group(1)))


def pwm_study_point(rec: Dict[str, Any]) -> Dict[str, float]:
    """The operating point the carrier study was MEASURED at.

    ``{"I_line_A": …, "winding_C": …, "magnets_C": …}``, whatever of it can be
    established.  A record that carries a structured ``point`` wins; otherwise
    the numbers are read off the provenance line the study writes.
    """
    p = (rec or {}).get("point")
    if isinstance(p, dict):
        out = {
            "I_line_A": _numf(p.get("I_line_A") if p.get("I_line_A") is not None
                              else p.get("I_line_rms_A")),
            "winding_C": _numf(p.get("winding_C") if p.get("winding_C") is not None
                               else p.get("coil_temp_C")),
            "magnets_C": _numf(p.get("magnets_C") if p.get("magnets_C") is not None
                               else p.get("magnet_temp_c")),
        }
        return {k: v for k, v in out.items() if v is not None}
    s = str(p if isinstance(p, str) and p else ((rec or {}).get("source") or ""))
    out = {
        "I_line_A": _pwm_grab(s, r"I_line\s+([\d.,\s  ]*\d)\s*A"),
        "winding_C": _pwm_grab(s, r"winding\s+([\d.,]+)\s*°?\s*C"),
        "magnets_C": _pwm_grab(s, r"magnets?\s+([\d.,]+)\s*°?\s*C"),
    }
    return {k: v for k, v in out.items() if v is not None}


def pwm_report_point(col: Dict[str, Any]) -> Dict[str, float]:
    """The point THIS report is about for that duty — the saved summary sections
    3 and 4 are built from, read with the same keys those sections use."""
    em = col.get("em") if isinstance(col.get("em"), dict) else {}
    i = _numf(_g(em, "I_line_rms_A"))
    if i is None:
        i = _numf(_g(em, "I_phase_rms_A"))
    out = {"I_line_A": i, "winding_C": _numf(_g(em, "coil_temp_C"))}
    return {k: v for k, v in out.items() if v is not None}


def pwm_point_differs(study: Dict[str, float], rep: Dict[str, float]) -> bool:
    """True when the study was measured somewhere else than this report's point.

    Unknown is NOT a difference: a record whose point cannot be established says
    nothing, rather than accusing the document of an inconsistency it cannot
    demonstrate — the same rule the staleness check follows.
    """
    a, b = study.get("I_line_A"), rep.get("I_line_A")
    if a is not None and b is not None and \
            abs(a - b) > PWM_POINT_I_TOL * max(abs(a), abs(b), 1.0):
        return True
    a, b = study.get("winding_C"), rep.get("winding_C")
    return bool(a is not None and b is not None
                and abs(a - b) > PWM_POINT_T_TOL_K)


def pwm_report_sine_loss(col: Dict[str, Any]) -> Optional[float]:
    """This duty's sinusoidal electromagnetic loss as sections 3 and 4 print it
    — the same key the comparison table's "Total electromagnetic loss" reads."""
    em = col.get("em") if isinstance(col.get("em"), dict) else {}
    return _numf(_g(em, "P_loss_total_W"))


def _pwm_transferred(case: Dict[str, Any], b_total: Optional[float],
                     rep_loss: Optional[float]) -> str:
    """The added watts this carrier would cost AT THIS REPORT'S POINT.

    The measured SHARE — the case's own delta over the sinusoid it was
    differenced against — applied to the report's own sinusoidal loss.  The
    unrounded share is used, not the rounded per cent printed two rows above:
    28.6 % of 7,851.8 W is 2,246 W and 28.5648 % of it is 2,243 W, and the
    second is the one the measurement supports.  Labelled an estimate because
    it is one: it assumes the carrier's cost scales with the loss it rides on.
    """
    d = _numf((case.get("delta_W") or {}).get("total"))
    base = _pwm_case_base(case, b_total)
    if d is None or not base or rep_loss is None:
        return "—"
    return _signed(rep_loss * d / base, 0)


def pwm_point_note(rec: Dict[str, Any], col: Dict[str, Any],
                   sine_shaft_pct: Optional[float] = None) -> str:
    """The one line under the table that says where the study was measured and
    which of its numbers transfer.  ``""`` when the two points agree."""
    study, rep = pwm_study_point(rec), pwm_report_point(col)
    if not pwm_point_differs(study, rep):
        return ""
    i_s, i_r = study.get("I_line_A"), rep.get("I_line_A")
    head = "Study point: the carrier study was measured at %s" % _fmt(i_s, 1, "A")
    if study.get("winding_C") is not None:
        head += " line / %s winding" % _fmt(study["winding_C"], 1, "°C")
    if i_s and i_r:
        head += (" — this report's point is %s, %s %% %s"
                 % (_fmt(i_r, 1, "A"), _fmt(abs(100.0 * (i_r - i_s) / i_s), 1),
                    "higher" if i_r > i_s else "lower"))
    loss = pwm_report_sine_loss(col)
    if loss is not None:
        head += (", where the sinusoidal electromagnetic loss is %s W (§3)"
                 % _fmt(loss, 1))
    tail = ("The added watts in the table are as measured at the study's point; "
            "what transfers is dimensionless — the percentages and the "
            "efficiency drops")
    if sine_shaft_pct is not None:
        tail += (", so the estimated shaft efficiency below is this report's own "
                 "sinusoidal %s %% less the points the carrier costs"
                 % _fmt(sine_shaft_pct, 3))
    return head + ". " + tail + "."


def pwm_sine_shaft_eta(col: Dict[str, Any],
                       brg: Optional[Dict[str, Any]] = None) -> Optional[float]:
    """This duty's SINUSOIDAL shaft efficiency, as the rest of the report states
    it — :func:`shaft_view` on the duty's own saved run, with the bearing model.

    The PWM record carries an ``eta_shaft_est`` of its own, computed at the
    point the study was measured at.  The report prefers this one so that the
    estimated PWM shaft efficiency in the table below and the headline shaft
    efficiency on page 1 cannot disagree about where the machine starts from;
    the record's own number is the fallback when there is no bearing model.
    """
    em = col.get("em") if isinstance(col.get("em"), dict) else None
    if not em:
        return None
    # THE COLUMN'S OWN FRICTION, not the report duty's.  ``brg`` is the machine
    # level bearing model, solved at whatever point the document is about — feed
    # it to another duty's run and a 14,200 rpm report prints a 20,000 rpm
    # column's shaft efficiency with the slower duty's windage in it.  Same
    # order `coupled_compare_rows` uses: the duty's own coupled record, then its
    # own saved summary, then the machine-level model.
    x = _numf((((col.get("res") or {}).get("coupled")) or {}).get("P_mech_extra_W"))
    if x is None:
        x = _numf(em.get("P_mech_extra_W"))
    b = ({"has_bearings": True, "P_mech_extra_W": x} if x is not None else brg)
    sv = shaft_view(em, b, (col.get("d") or {}).get("mode"))
    return sv.get("eta_shaft")


def pwm_sine_em_eta(col: Dict[str, Any]) -> Optional[float]:
    """This duty's SINUSOIDAL electromagnetic efficiency as sections 3 and 4
    state it — :func:`shaft_view` on the duty's own saved run (MJ-2).

    The carrier study's baseline ``eta_em`` was measured at the study's own
    point, which on the L155 peak duty is 750.9 A against this report's 770.5 A;
    printing it in a column headed "sinusoid" put 98.406 % under a label section
    3 answers with 98.39 %.  The study's baseline still forms every DELTA in the
    table — what transfers between two points is the drop, which is the rule the
    point note under the table already states.
    """
    em = col.get("em") if isinstance(col.get("em"), dict) else None
    if not em:
        return None
    sv = shaft_view(em, col.get("brg"), (col.get("d") or {}).get("mode"))
    return None if sv.get("eta_em") is None else 100.0 * float(sv["eta_em"])


def _pwm_em_est(case: Dict[str, Any], base_eta_pct: Optional[float],
                rep_eta_pct: Optional[float]) -> Optional[float]:
    """The electromagnetic efficiency this carrier would leave at THIS report's
    point — the report's own sinusoidal figure less the drop the study measured
    (the same transfer :func:`_pwm_shaft_est` makes for the shaft)."""
    eta = _pwm_pct(case.get("eta_em"))
    if rep_eta_pct is not None and base_eta_pct is not None and eta is not None:
        return rep_eta_pct - (base_eta_pct - eta)
    return eta


def _pwm_pct(v: Optional[float]) -> Optional[float]:
    """A stored efficiency (0…1, or already in per cent) as per cent."""
    f = _numf(v)
    if f is None:
        return None
    return f * 100.0 if f <= 1.5 else f


def pwm_rows(cols: List[Dict[str, Any]],
             brg: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """One block per duty: ``{duty, measured, header, rows, source, notes, line}``.

    ``measured`` False means the duty has no PWM record, and then ``line`` is
    the single sentence both renderers print in its place — the ``NOT_SOLVED``
    rule of every other comparison in this document, spelled for this section.

    The table is one column per supply: the sinusoid first, then each measured
    carrier.  Every number comes out of the record; nothing here is fitted,
    scaled or carried over from another duty.
    """
    blocks: List[Dict[str, Any]] = []
    for c in cols:
        rec = _pwm_record(c)
        if rec is None:
            blocks.append({"duty": c.get("duty"), "measured": False,
                           "header": [], "rows": [], "source": None,
                           "point_note": "",
                           "notes": [], "line": PWM_NOT_MEASURED})
            continue
        base = rec.get("baseline") or {}
        bl = base.get("losses") or {}
        b_total = _numf(bl.get("total_W"))
        b_eta = _pwm_pct(base.get("eta_em"))
        cases = [x for x in (rec.get("cases") or []) if isinstance(x, dict)]
        sine_shaft = _pwm_pct(pwm_sine_shaft_eta(c, brg))

        header = ["Supply", "sinusoid"] + [_pwm_label(x) for x in cases]
        rows: List[List[Any]] = []

        def _row(label: str, sine: Any, fn) -> None:
            rows.append([label, sine] + [fn(x) for x in cases])

        # THIS REPORT'S OWN SINUSOID, FIRST (client review 2026-09-14).  When
        # the study was measured at another point, the first thing the table
        # says is what the rest of the document calls the sinusoidal loss, so
        # the 7,689.3 W two rows down cannot be mistaken for it.
        off_point = pwm_point_differs(pwm_study_point(rec), pwm_report_point(c))
        rep_loss = pwm_report_sine_loss(c) if off_point else None
        if rep_loss is not None:
            rows.append([PWM_REPORT_BASELINE_LABEL, _fmt(rep_loss, 1)]
                        + ["—"] * len(cases))

        _row("Carrier [kHz]", "—",
             lambda x: _fmt((_numf(x.get("f_carrier_hz")) or 0) / 1000.0, 1)
             if x.get("f_carrier_hz") is not None else "—")
        _row("DC link [V]", "—", lambda x: _fmt(x.get("v_bus_V"), 1))
        _row("Modulation index [-]", "—", lambda x: _fmt(x.get("m"), 3))
        _row("Total electromagnetic loss [W]", _fmt(b_total, 1),
             lambda x: _fmt((x.get("losses") or {}).get("total_W"), 1))
        # THE BASELINE EACH COLUMN IS DIFFERENCED AGAINST (MJ-2, reviewer
        # 2026-09-14).  The 48 kHz case is measured against a 600-step
        # sinusoid, the 24 kHz ones against a 280-step one, and with only the
        # first printed the 48 kHz column did not close: 9,225.5 − 7,689.3 is
        # 1,536.2 W beside a printed +1,533.2 W.  Every case now prints the
        # sinusoid it was actually differenced against, so each column is
        # arithmetic the reader can do on the page.
        _row("…the sinusoid it is differenced against [W]", "—",
             lambda x: _fmt(_pwm_case_base(x, b_total), 1))
        _row("Added loss [W]", "—",
             lambda x: _signed((x.get("delta_W") or {}).get("total"), 1))
        _row("Added loss [% of the sinusoidal loss]", "—",
             lambda x: _pwm_share((x.get("delta_W") or {}).get("total"),
                                  _pwm_case_base(x, b_total)))
        # …and that share carried over to the point THIS report is about.
        if rep_loss is not None:
            _row(PWM_TRANSFERRED_LABEL, "—",
                 lambda x: _pwm_transferred(x, b_total, rep_loss))
        for lbl, key in (("…of which copper [W]", "copper"),
                         ("…of which iron [W]", "iron"),
                         ("…of which magnets [W]", "magnets")):
            _row(lbl, "—",
                 lambda x, _k=key: _signed((x.get("delta_W") or {}).get(_k), 1))
        # …AND THE REST OF THE SOLID CONDUCTORS (MJ-1).  The record carries
        # `solid` = magnets + sleeve + shaft and `magnets` on its own, so the
        # three printed components used to sum to 46.7 W MORE than the total
        # they were "of which" of.  The missing term is the sleeve and the
        # shaft, and on this machine it is NEGATIVE — the carrier's field is
        # screened before it reaches them — which is exactly why it may not be
        # left out.
        _row("…of which sleeve + shaft (solid conductors) [W]", "—",
             lambda x: _signed(_pwm_sleeve_shaft(x), 1))
        # ONE EM EFFICIENCY PER DUTY (MJ-2): this report's own sinusoidal
        # figure, with each carrier's measured drop transferred onto it — the
        # rule the shaft row below has always followed.
        rep_eta = pwm_sine_em_eta(c)
        _row("Electromagnetic efficiency%s [%%]"
             % (", estimated" if rep_eta is not None else ""),
             _fmt(rep_eta if rep_eta is not None else b_eta, 3),
             lambda x: _fmt(_pwm_em_est(x, b_eta, rep_eta), 3))
        _row("Shaft efficiency, estimated [%]",
             _fmt(sine_shaft, 3) if sine_shaft is not None else "—",
             lambda x: _fmt(_pwm_shaft_est(x, b_eta, sine_shaft), 3))
        _row("Torque ripple [%]", _fmt(base.get("ripple_pct"), 1),
             lambda x: (_fmt(x.get("ripple_pct"), 1) if x.get("dc_converged")
                        else PWM_RIPPLE_NOT_QUOTABLE))
        # The sinusoid's THD is not a measurement (CS-14): the baseline is a
        # current-driven source, so it is zero BY CONSTRUCTION and the cell
        # says so rather than passing for a solved number like its neighbours.
        _row("Current THD [%]", PWM_THD_BY_CONSTRUCTION,
             lambda x: _fmt(x.get("thd_i_pct"), 2))

        notes = [str(n) for n in (rec.get("notes") or [])]
        notes += [("%s — %s" % (_pwm_label(x), x.get("note")))
                  for x in cases if x.get("note")]
        blocks.append({"duty": c.get("duty"), "measured": True,
                       "header": header, "rows": rows,
                       "source": pwm_source_text(rec.get("source")),
                       "point_note": pwm_point_note(rec, c, sine_shaft),
                       "notes": notes, "line": ""})
    return blocks


#: The sinusoid column's current THD — true, and true by construction.
PWM_THD_BY_CONSTRUCTION = "0 (by construction)"


def _pwm_label(case: Dict[str, Any]) -> str:
    """The column heading for one carrier, with the kHz the CARRIER ROW says.

    CS-13 (reviewer 2026-09-14): the stored labels are the round numbers the
    study was named after ("PWM 24 kHz") while the synchronous carrier the run
    actually used is 23,333 Hz — printed as 23.3 in the row directly under the
    heading and quoted as 23.3 in every sentence of the narrative.  The heading
    was the only place the machine ran at a frequency it never saw.
    """
    label = str(case.get("label") or "PWM")
    f = _numf(case.get("f_carrier_hz"))
    if f is None or f <= 0:
        return label
    khz = _fmt(f / 1000.0, 1)
    return re.sub(r"(?<![\d.])\d+(?:[.,]\d+)?(\s*kHz)", khz + r"\1", label,
                  count=1)


def _pwm_case_base(case: Dict[str, Any], b_total: Optional[float]
                   ) -> Optional[float]:
    """The sinusoidal loss THIS case was differenced against, in watts.

    The record stores the case's own total and its delta; their difference is
    the resolution-matched baseline the case was measured against, which is not
    always the one printed in the sinusoid column (MJ-2).
    """
    tot = _numf((case.get("losses") or {}).get("total_W"))
    d = _numf((case.get("delta_W") or {}).get("total"))
    if tot is None or d is None:
        return b_total
    return tot - d


def _pwm_sleeve_shaft(case: Dict[str, Any]) -> Optional[float]:
    """``solid − magnets`` — the sleeve and the shaft between them (MJ-1)."""
    d = case.get("delta_W") or {}
    sol, mag = _numf(d.get("solid")), _numf(d.get("magnets"))
    if sol is None:
        return None
    return sol - (mag or 0.0)


#: What replaces the stored `pwm.source` head when it is PRINTED (MJ-8).  The
#: record's own line names the engineer's working file and the wall-clock time
#: the point was taken at; a client cannot open the first and the second is an
#: identifier this project does not put in documents.  The physics tail —
#: current, speed, angle, temperatures, mesh, k_end, flags — is kept verbatim.
PWM_SOURCE_HEAD = ("MOTRES PWM study on this duty, resolution-matched FEM runs")

_PWM_SOURCE_PATH = re.compile(r"\s*\([^()]*[\\/][^()]*\)")
_PWM_SOURCE_CLOCK = re.compile(r"\s*\b\d{1,2}:\d{2}(?::\d{2})?\b")


def pwm_source_text(source: Any) -> str:
    """The stored provenance line as the CLIENT document prints it."""
    s = str(source or "").strip()
    if not s:
        return ""
    tail = s.split(" — ", 1)[1] if " — " in s else ""
    tail = _PWM_SOURCE_PATH.sub("", tail)
    tail = _PWM_SOURCE_CLOCK.sub("", tail)
    tail = re.sub(r"\s{2,}", " ", tail).strip(" ;,")
    return PWM_SOURCE_HEAD + ((" — " + tail) if tail else "")


def _signed(v: Any, d: int = 1) -> str:
    """``"+2,196.4"`` — an ADDED quantity, so the sign is part of the number."""
    f = _numf(v)
    if f is None:
        return "—"
    return ("+" if f >= 0 else "−") + _fmt(abs(f), d)


def _pwm_share(delta: Any, base: Any) -> str:
    d, b = _numf(delta), _numf(base)
    if d is None or not b:
        return "—"
    return _signed(100.0 * d / b, 1) + " %"


def _pwm_shaft_est(case: Dict[str, Any], base_eta_pct: Optional[float],
                   sine_shaft_pct: Optional[float]) -> Optional[float]:
    """The shaft efficiency this carrier would leave, in per cent.

    The report's own sinusoidal shaft efficiency LESS the electromagnetic
    efficiency the carrier costs — the bearings and the windage do not change
    when the supply does, so the drop is the same at the shaft as it is in the
    electromagnetics.  The record's stored estimate is the fallback.
    """
    eta = _pwm_pct(case.get("eta_em"))
    if sine_shaft_pct is not None and base_eta_pct is not None and eta is not None:
        return sine_shaft_pct - (base_eta_pct - eta)
    return _pwm_pct(case.get("eta_shaft_est"))


def pwm_influence_text(cols: List[Dict[str, Any]],
                       brg: Optional[Dict[str, Any]] = None) -> List[str]:
    """The paragraphs under the table — every number read from the records.

    Empty when no duty carries a PWM measurement; the caller then prints
    :data:`PWM_NO_RECORD_AT_ALL` and nothing else.
    """
    out: List[str] = []
    for c in cols:
        rec = _pwm_record(c)
        # A duty with a PWM COUPLED record is answered by `pwm_coupled_text`
        # instead: its carrier study is an older, colder measurement of the same
        # question and printing both would put two costs of one carrier on one
        # page (2026-09-14).
        if rec is None or pwm_coupled_record(c) is not None:
            continue
        base = rec.get("baseline") or {}
        bl = base.get("losses") or {}
        b_total = _numf(bl.get("total_W"))
        b_eta = _pwm_pct(base.get("eta_em"))
        cases = [x for x in (rec.get("cases") or []) if isinstance(x, dict)]
        if not cases:
            continue
        duty = c.get("duty")

        # ── where the watts land ────────────────────────────────────────────
        first = cases[0]
        dw = first.get("delta_W") or {}
        tot = _numf(dw.get("total"))
        cu, fe, mag = (_numf(dw.get("copper")), _numf(dw.get("iron")),
                       _numf(dw.get("magnets")))
        shs = _pwm_sleeve_shaft(first)
        base1 = _pwm_case_base(first, b_total)
        # …quoted on THIS report's own sinusoid, exactly as the table above
        # prints it (MJ-2): the drop is what the study measured, the level is
        # this duty's.
        _rep_eta = pwm_sine_em_eta(c)
        eta1 = _pwm_em_est(first, b_eta, _rep_eta)
        b_eta_print = _rep_eta if _rep_eta is not None else b_eta
        # FOUR SENTENCES, no more (user 2026-09-14): what the carrier costs and
        # where it lands, what doubling it buys, what the bus does, and that the
        # thermal section is on the sinusoidal losses.  The run notes, the
        # resolution-matching explanation and the footnotes are gone — the
        # numbers are in the table above and the source line names the study.
        # ONE CLAUSE FOR THE POINT (client review 2026-09-14): when the study
        # was measured somewhere else than this report's point, the sentence
        # that quotes its watts says so before it quotes them.
        _study, _rep = pwm_study_point(rec), pwm_report_point(c)
        _at = ""
        if pwm_point_differs(_study, _rep) and _study.get("I_line_A") \
                and _rep.get("I_line_A"):
            _at = ("at the study's point — %s against this report's %s — "
                   % (_fmt(_study["I_line_A"], 1, "A"),
                      _fmt(_rep["I_line_A"], 1, "A")))
        if tot:
            out.append(
                "Duty '%s', %s: %sthe carrier costs %s W on the sinusoid's %s W "
                "(%s of the electromagnetic loss) and it lands in the STATOR — "
                "%s W copper, %s W iron, against %s W in the magnets and %s W "
                "in the sleeve and shaft — so electromagnetic efficiency falls "
                "from %s %% to %s %%, %s pp."
                % (duty, _pwm_label(first) if first.get("label")
                   else "the measured carrier", _at,
                   _signed(tot, 0).lstrip("+"), _fmt(base1, 0),
                   _pwm_share(tot, base1).lstrip("+"),
                   _signed(cu, 0), _signed(fe, 0),
                   _signed(mag, 1), _signed(shs, 1),
                   _fmt(b_eta_print, 3), _fmt(eta1, 3),
                   _signed((eta1 - b_eta_print)
                           if (eta1 is not None and b_eta_print is not None)
                           else None, 3)))

        # ── doubling the carrier ────────────────────────────────────────────
        pair = _pwm_carrier_pair(cases)
        if pair:
            lo, hi = pair
            d_lo = (b_eta - _pwm_pct(lo.get("eta_em"))) if b_eta else None
            d_hi = (b_eta - _pwm_pct(hi.get("eta_em"))) if b_eta else None
            if d_lo is not None and d_hi is not None:
                out.append(
                    "Doubling the carrier from %s kHz to %s kHz takes the added "
                    "loss from %s W to %s W and gives back only %s pp of the %s "
                    "the lower carrier costs, while the bridge's own switching "
                    "loss — not in this model — roughly doubles."
                    % (_fmt((_numf(lo.get("f_carrier_hz")) or 0) / 1000.0, 1),
                       _fmt((_numf(hi.get("f_carrier_hz")) or 0) / 1000.0, 1),
                       _fmt((lo.get("delta_W") or {}).get("total"), 0),
                       _fmt((hi.get("delta_W") or {}).get("total"), 0),
                       _fmt(d_lo - d_hi, 3), _fmt(d_lo, 3) + " pp"))

        # ── the bus ─────────────────────────────────────────────────────────
        bus = _pwm_bus_pair(cases)
        if bus:
            lo, hi = bus
            d_lo = _numf((lo.get("delta_W") or {}).get("total"))
            d_hi = _numf((hi.get("delta_W") or {}).get("total"))
            v_lo, v_hi = _numf(lo.get("v_bus_V")), _numf(hi.get("v_bus_V"))
            n = None
            if d_lo and d_hi and v_lo and v_hi and v_hi > v_lo:
                try:
                    n = math.log(d_hi / d_lo) / math.log(v_hi / v_lo)
                except (ValueError, ZeroDivisionError):
                    n = None
            e_lo, e_hi = _pwm_pct(lo.get("eta_em")), _pwm_pct(hi.get("eta_em"))
            out.append(
                "Charging the pack from %s V to %s V at the same carrier raises "
                "the cost from %s W to %s W%s — another %s pp, more than "
                "doubling the carrier gives back."
                % (_fmt(v_lo, 1), _fmt(v_hi, 1), _fmt(d_lo, 0), _fmt(d_hi, 0),
                   (" (measured exponent %s on the bus voltage)" % _fmt(n, 1))
                   if n else "",
                   _fmt((e_lo - e_hi), 3) if (e_lo is not None
                                              and e_hi is not None) else "—"))

        # ── what the thermal section was fed ────────────────────────────────
        # THE SENTENCE NAMES THE DUTY, not the report (CS-3, audit v6).  It is
        # true of a duty solved on the sinusoid and false of a PWM duty whose
        # thermal map the loop itself produced, and it sat under a section that
        # prints a block for each.
        if tot and b_total:
            out.append(
                "The thermal and coupled answers of the duty '%s' were solved "
                "on its SINUSOIDAL losses, so on a %s kHz bridge its stator "
                "carries about %s more loss than the temperature field they "
                "print was given."
                % (duty,
                   _fmt((_numf(first.get("f_carrier_hz")) or 0) / 1000.0, 1),
                   _pwm_share(tot, base1).lstrip("+")))
    return out


def _pwm_carrier_pair(cases: List[Dict[str, Any]]):
    """The lowest and highest carrier measured on the SAME bus — the pair the
    "what does doubling the carrier buy" sentence may be made of.  ``None``
    when the record holds only one carrier."""
    by_bus: Dict[Any, List[Dict[str, Any]]] = {}
    for x in cases:
        if _numf(x.get("f_carrier_hz")) is None:
            continue
        by_bus.setdefault(_numf(x.get("v_bus_V")), []).append(x)
    for _bus, group in sorted(by_bus.items(),
                              key=lambda kv: (kv[0] is None, kv[0] or 0.0)):
        g = sorted(group, key=lambda x: _numf(x.get("f_carrier_hz")) or 0.0)
        if len(g) >= 2 and g[0] is not g[-1]:
            return g[0], g[-1]
    return None


def _pwm_bus_pair(cases: List[Dict[str, Any]]):
    """The lowest and highest DC link measured at the SAME carrier."""
    by_f: Dict[Any, List[Dict[str, Any]]] = {}
    for x in cases:
        if _numf(x.get("v_bus_V")) is None:
            continue
        by_f.setdefault(round(_numf(x.get("f_carrier_hz")) or 0.0, 1), []).append(x)
    for _f, group in sorted(by_f.items()):
        g = sorted(group, key=lambda x: _numf(x.get("v_bus_V")) or 0.0)
        if len(g) >= 2 and g[0] is not g[-1]:
            return g[0], g[-1]
    return None


# ---------------------------------------------------------------------------
# 5 · PWM influence, from the COUPLED records  (2026-09-14, overnight)
# ---------------------------------------------------------------------------
# The study record above is a measurement at ONE point with the temperatures
# held fixed — it could say what the carrier costs in watts and nothing about
# what those watts do.  The coupled loop can now be run on the inverter, so a
# duty carries a whole PWM answer: its own settled winding, magnet and bearing
# temperatures, its own shaft efficiency, and — in `reference_sine` — the
# sinusoidal record it replaced, solved on the same machine at the same point.
#
# That is what this section became: two (or three) complete answers side by
# side, not a delta table.  The study path survives underneath for a duty that
# has a carrier study and no PWM coupled run.

#: The opening sentence when THE DUTY THIS REPORT IS ABOUT runs on the bridge.
#: It NAMES that duty (MJ-1 / CS-2, audit v6): the sentence was printed verbatim
#: on the peak document, whose duty is fed by a clean sinusoid and where no
#: number anywhere is a PWM run, and its singular "This duty's" opened a section
#: that prints a block for each duty of the configuration.
#:
#: …AND IT PROMISES A SETPOINT, NOT A SOLVED POINT (BT-4, 2026-09-16).  It said
#: "the same point": the two columns are aimed at one current setpoint, which
#: is true, but a voltage-fed run reaches it or does not, and on the L180 gen
#: peak duty it did not — 176.679 N·m against the sinusoid's 238.467.  Where
#: the two columns really are not one point, the block below says so and names
#: the cause (:func:`pwm_same_point_note`).
PWM_INTRO_COUPLED = (
    "The duty '%s' this report is about runs on the inverter: every number "
    "elsewhere in this report is its PWM run, and this section is what the "
    "carrier costs against the sinusoidal run it replaced — the same machine "
    "at the same setpoint, both converged to their own temperatures.")

#: …and when the report duty is sinusoidal while another duty of the same
#: configuration was solved on a bridge.
PWM_INTRO_SINE_REPORT = (
    "The duty '%s' this report is about runs on an ideal SINUSOIDAL supply, "
    "and every number elsewhere in this report is that run; this section is "
    "what a real two-level bridge costs, duty by duty.")

#: The informational row's own words, wherever it is printed.
CARRIER_RIPPLE_NOTE = ("carrier-frequency ripple, filtered by the rotor "
                       "inertia; not a limit")

#: How far section 5's two columns' TORQUES may sit apart before the block
#: stops letting "the same point" stand (BT-4, 2026-09-16).  The inverter
#: loop's own current tolerance (``inverter.i_tol_pct``) when the record
#: carries one; 1 % is what this project's loop is configured with.
PWM_SAME_POINT_TOL_PCT = 1.0


def pwm_same_point_note(t_sine: Any, t_pwm: Any, inv: Any,
                        dem_sine: Any = None, dem_pwm: Any = None,
                        point_pct: Any = None) -> str:
    """One clause when section 5's two columns are NOT the same point — ``""``
    when they are.

    The section's intro promises "the same machine, the same setpoint" and every
    delta in the table is billed to the carrier.  On the L180 gen PEAK duty the
    sinusoid solves 238.467 N·m and the bridge 176.679 N·m — 26 % apart — with
    19.17 % of Br lost on the PWM run against 2.07 % on the sinusoid, so
    "+3,456 W (+41.1 %)" and "shaft efficiency falls 98.17 → 96.70 %" compared a
    553 kW machine with a 410 kW one.  Neither torque nor demagnetisation was
    printed anywhere in the section.  Both have rows now, and this sentence
    names the cause in ONE clause (minimal-prose rule): whichever of the two —
    the current the bridge actually solved, or magnets the duty demagnetised —
    accounts for more of the torque gap.
    """
    ts, tp = _numf(_g(t_sine, "T_em_avg_Nm") if isinstance(t_sine, dict)
                   else t_sine), _numf(_g(t_pwm, "T_em_avg_Nm")
                                       if isinstance(t_pwm, dict) else t_pwm)
    if ts is None or tp is None or not ts:
        return ""
    tol = _numf((inv or {}).get("i_tol_pct")) or PWM_SAME_POINT_TOL_PCT
    d_t = 100.0 * (abs(tp) - abs(ts)) / abs(ts)
    if abs(d_t) <= abs(tol):
        return ""
    # ONE FIGURE FOR THE MISS.  ``point_pct`` is the document's own — the LINE
    # current's, which sections 2, 3 and 5 print — so this clause cannot be the
    # only place in the report that spells it differently.
    d_i = _numf(point_pct)
    if d_i is None:
        d_i = _numf((inv or {}).get("point_error_pct")) or 0.0
    ls = _numf(_g(dem_sine or {}, "loss_pct"))
    lp = _numf(_g(dem_pwm or {}, "loss_pct"))
    # The torque a current miss alone explains is the miss itself; what is left
    # over is the magnets'.
    if (ls is not None and lp is not None and lp > ls
            and abs(d_t - d_i) > abs(d_i)):
        why = ("the PWM run's magnets are demagnetised, %s of Br lost against "
               "%s on the sinusoid" % (_fmt(lp, 2, "%"), _fmt(ls, 2, "%")))
    else:
        why = ("the bridge did not solve the sinusoid's current, %s %% off the "
               "setpoint" % _signed(d_i, 2))
    return ("The two columns are NOT one operating point — %s on the bridge "
            "against %s on the sinusoid, %s %% — so the added loss above is "
            "not the carrier's alone: %s."
            % (_fmt(tp, 3, "N·m"), _fmt(ts, 3, "N·m"),
               _signed(d_t, 1), why))


def pwm_coupled_record(col: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """This duty's PWM coupled record, or ``None`` on a sinusoidal duty."""
    rec = (col.get("res") or {}).get("coupled")
    if isinstance(rec, dict) and record_drive(rec) == "pwm":
        return rec
    return None


def _cpl_em(rec: Any) -> Dict[str, Any]:
    e = (rec or {}).get("em") if isinstance(rec, dict) else None
    return e if isinstance(e, dict) else {}


def _cpl_inv(rec: Any) -> Dict[str, Any]:
    i = (rec or {}).get("inverter") if isinstance(rec, dict) else None
    return i if isinstance(i, dict) else {}


#: What the sine column may borrow from the duty's own saved summary.
_PWM_VIEW_KEYS: Tuple[str, ...] = (
    "T_em_avg_Nm", "T_ripple_pct", "P_stranded_W", "P_core_W", "P_solid_W",
    "P_mag_W", "P_shaft_W", "P_sleeve_W", "P_loss_total_W", "efficiency",
    "THD_I_pct", "THD_LL_pct", "V_line_peak_V", "n_steps_per_period")


def _cpl_view(rec: Any, sine_em: Optional[Dict[str, Any]] = None
              ) -> Dict[str, Any]:
    """One record's electromagnetic numbers, as this table reads them.

    THE SINE COLUMN MAY BORROW ITS WATTS (2026-09-14).  A coupled record filed
    before the loop learned to keep its own ``em`` block carries temperatures
    and no losses, and that is exactly the record a first PWM run displaces and
    keeps as its ``reference_sine`` — so the one table the whole exercise is
    about would have printed a column of em dashes.  The duty's own saved
    sinusoidal summary is the same run at the same point (it is what sections 3
    and 4 printed before the bridge existed), so it fills what the record did
    not keep, and never overrides what it did.
    """
    em = dict(_cpl_em(rec))
    if isinstance(sine_em, dict):
        em = {**{k: v for k, v in sine_em.items()
                 if k in _PWM_VIEW_KEYS and v is not None},
              **em}
    return em


def _cpl_sum(em: Any, keys: Tuple[str, ...]) -> Optional[float]:
    """The watts of one loss GROUP — ``None`` when the record kept none of it,
    which is different from a group that really is zero."""
    e = em if isinstance(em, dict) else {}
    vals = [_numf(e.get(k)) for k in keys]
    vals = [v for v in vals if v is not None]
    return sum(vals) if vals else None


#: The loss groups the sine → PWM table differences, in the order it prints.
_PWM_GROUPS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("…of which copper [W]", ("P_stranded_W",)),
    ("…of which iron [W]", ("P_core_W",)),
    ("…of which magnets [W]", ("P_mag_W",)),
    ("…of which sleeve + shaft [W]", ("P_sleeve_W", "P_shaft_W")),
)


def _cpl_label(rec: Any, fallback: str = "PWM") -> str:
    f = _khz(_cpl_inv(rec).get("f_carrier_hz"))
    return ("PWM %s kHz" % f) if f else fallback


def pwm_coupled_columns(rec: Dict[str, Any]
                        ) -> List[Tuple[str, Optional[Dict[str, Any]]]]:
    """``[(heading, record), …]`` — the sinusoid, this carrier, then the others.

    The alternative carriers are sorted by frequency, so a 48 kHz column always
    stands to the right of the 24 kHz one it is compared with.
    """
    sine = rec.get("reference_sine") if isinstance(rec.get("reference_sine"),
                                                   dict) else None
    cols: List[Tuple[str, Optional[Dict[str, Any]]]] = [("sinusoid", sine)]
    cols.append((_cpl_label(rec), rec))
    alts = [a for a in (rec.get("alt_carriers") or []) if isinstance(a, dict)]
    alts.sort(key=lambda a: _numf(_cpl_inv(a).get("f_carrier_hz")) or 0.0)
    for a in alts:
        cols.append((_cpl_label(a, "another carrier"), a))
    return cols


#: What the sine column's note says when its watts came from the duty's own
#: saved summary rather than from the coupled record it is stored in.
PWM_SINE_WATTS_BORROWED = (
    "The sinusoid column's losses are the duty's own saved sinusoidal summary "
    "— the same run at the same point — because the coupled record it "
    "replaced predates the loop keeping a loss block of its own; the "
    "temperatures beside them are the record's.")


def _cpl_shaft_view(col: Dict[str, Any], rec: Any,
                    view: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """:func:`shaft_view` for ONE column of the section-5 table (MJ-2).

    The block used to print each record's own stored ``efficiency`` /
    ``efficiency_shaft`` while every other section of the document states the
    same two quantities through ``shaft_view`` — so the L155 rated duty carried
    97.86 % / 97.71 % in section 5 and 97.77 % / 97.61 % on page 1, in section 3
    and in section 4, under identical labels.  One balance, everywhere.
    """
    em = dict(view or {})
    base = col.get("em") if isinstance(col.get("em"), dict) else {}
    if em.get("end3d") is None and base.get("end3d") is not None:
        em["end3d"] = base["end3d"]
    if em.get("rpm") is None:
        em["rpm"] = base.get("rpm") or (col.get("d") or {}).get("rpm")
    if em.get("P_mech_W") is None:
        _t, _n = _numf(em.get("T_em_avg_Nm")), _numf(em.get("rpm"))
        if _t is not None and _n:
            em["P_mech_W"] = abs(_t) * 2.0 * math.pi * float(_n) / 60.0
    x = _numf((rec or {}).get("P_mech_extra_W"))
    brg = {"has_bearings": True, "P_mech_extra_W": x} if x is not None else None
    return shaft_view(em, brg, (col.get("d") or {}).get("mode"))


#: WHAT THE TWO COLUMNS WERE SOLVED AT (MJ-4, audit v6).  The sinusoid runs at
#: 36 steps per electrical period and the inverter run at 400 — a carrier needs
#: them — so the difference the table prints is the carrier's cost AND the
#: resolution's.  The study's own rule puts about 100 W on the step count alone.
PWM_RESOLUTION_NOTE = (
    "The two columns are NOT resolution-matched: the sinusoid was solved at "
    "%s steps per electrical period against the inverter run's %s, and the "
    "carrier study's own rule puts about 100 W of any such difference on the "
    "step count alone.")


def pwm_resolution_note(sine_steps: Any, pwm_steps: Any) -> str:
    """The sentence above, or ``""`` when the two columns match."""
    a, b = _numf(sine_steps), _numf(pwm_steps)
    if a is None or b is None or abs(a - b) < 0.5:
        return ""
    return PWM_RESOLUTION_NOTE % (_fmt(a, 0), _fmt(b, 0))


def pwm_coupled_rows(col: Dict[str, Any]) -> Dict[str, Any]:
    """One PWM duty's section-5 block, built entirely from its stored records."""
    rec = pwm_coupled_record(col) or {}
    cc = pwm_coupled_columns(rec)
    sine = cc[0][1]
    recs = [r for _h, r in cc]
    sine_em = (col.get("em_sine")
               if isinstance(col.get("em_sine"), dict) else None)
    # The SINE column may borrow its watts from the duty's saved summary; every
    # other column is its own record's and borrows nothing.
    views = {id(r): _cpl_view(r, sine_em if r is sine else None)
             for r in recs if r is not None}
    borrowed = bool(sine is not None and not _cpl_em(sine)
                    and _cpl_sum(views.get(id(sine)), ("P_loss_total_W",))
                    is not None)
    header = ["Supply"] + [h for h, _r in cc]
    rows: List[List[Any]] = []

    def _em(r: Any) -> Dict[str, Any]:
        return views.get(id(r)) or {}

    def _row(label: str, fn) -> None:
        rows.append([label] + [fn(r) for r in recs])

    def _d(r: Any, keys: Tuple[str, ...]) -> Optional[float]:
        """This column's watts minus the sinusoid's, for one loss group."""
        if r is sine or sine is None:
            return None
        a, b = _cpl_sum(_em(r), keys), _cpl_sum(_em(sine), keys)
        return None if (a is None or b is None) else a - b

    # THE TWO QUANTITIES THAT SAY WHETHER THIS IS ONE POINT (BT-4, 2026-09-16).
    # The block billed every delta to the carrier and printed neither the
    # torque nor the demagnetisation that separate the columns.
    _k3 = _numf(_g(col.get("em") or {}, "end3d.k_flux"))
    _dem_sine = ((sine_em or {}).get("demag")
                 if isinstance((sine_em or {}).get("demag"), dict) else None)
    _dem_pwm = ((col.get("em") or {}).get("demag")
                if isinstance((col.get("em") or {}).get("demag"), dict) else None)

    def _dem(r: Any) -> Optional[Dict[str, Any]]:
        """The demagnetisation of the run THIS column is.  It lives on the run
        summaries, not on the coupled record, so the sine column reads the
        duty's saved sinusoidal summary and the carrier column the PWM run's;
        an alternative carrier has none and prints an em dash."""
        d = _em(r).get("demag")
        if isinstance(d, dict) and d:
            return d
        if r is sine:
            return _dem_sine
        if r is rec:
            return _dem_pwm
        return None

    _row("Carrier [kHz]",
         lambda r: _khz(_cpl_inv(r).get("f_carrier_hz")) or "—")
    _row("Torque%s [N·m]" % (" × k_3d" if _k3 else ", 2-D"),
         lambda r: _fmt((lambda t: None if t is None else
                         abs(t) * (float(_k3) if _k3 else 1.0))(
                            _numf(_em(r).get("T_em_avg_Nm"))), 3))
    _row("Br kept in the magnets [%]",
         lambda r: _fmt(_numf((_dem(r) or {}).get("br_kept_vol_pct")), 3))
    _row("Worst magnet element, Br [%]",
         lambda r: _fmt(_numf((_dem(r) or {}).get("br_worst_pct")), 1))
    # ONE DC LINK, ONE FIGURE (CS-2, audit v7): 750 V here against the 750.4 V
    # sections 3, 4 and 8 print is the same link, rounded twice.
    _row("DC link [V]", lambda r: _fmt(_cpl_inv(r).get("v_dc_V"), 1))
    _row("Modulation index [-]", lambda r: _fmt(_cpl_inv(r).get("m"), 3))
    _row("Total electromagnetic loss [W]",
         lambda r: _fmt(_cpl_sum(_em(r), ("P_loss_total_W",)), 1))
    _row("Added loss [W]",
         lambda r: _signed(_d(r, ("P_loss_total_W",)), 1)
         if _d(r, ("P_loss_total_W",)) is not None else "—")
    _row("Added loss [% of the sinusoidal loss]",
         lambda r: _pwm_share(_d(r, ("P_loss_total_W",)),
                              _cpl_sum(_em(sine), ("P_loss_total_W",)))
         if r is not sine else "—")
    for _lbl, _keys in _PWM_GROUPS:
        _row(_lbl, lambda r, _k=_keys: (_signed(_d(r, _k), 1)
                                        if _d(r, _k) is not None else "—"))
    # ONE BALANCE, EVERY SECTION (MJ-2) — and two decimals, the same as the
    # comparison table, so the two cannot read as different numbers.
    def _eta(r: Any, key: str, stored: Any) -> str:
        v = _cpl_shaft_view(col, r, _em(r)).get(key)
        if v is not None:
            return _fmt(100.0 * float(v), 2)
        return _fmt(_pwm_pct(stored), 2)

    _row("Electromagnetic efficiency [%]",
         lambda r: _eta(r, "eta_em", _em(r).get("efficiency")))
    _row("Shaft efficiency [%]",
         lambda r: _eta(r, "eta_shaft", (r or {}).get("efficiency_shaft")))
    # …and at what RESOLUTION each column was solved (MJ-4).
    _row("Steps per electrical period",
         lambda r: _fmt(_em(r).get("n_steps_per_period"), 0))
    # THE POINT OF RUNNING THE LOOP ON THE BRIDGE: these three rows.  A study
    # record holds the temperatures fixed and can only say what the carrier
    # costs in watts; a coupled record says what those watts DO.
    _row("Winding temperature [°C]",
         lambda r: _fmt((r or {}).get("coil_temp_c"), 1))
    # THE ROW AND THE SENTENCE UNDER IT ARE ONE NUMBER (MJ-3, audit v6).  The
    # row preferred `magnet_temp_max_c` and the narrative two lines below used
    # `magnet_temp_c`, under one label: 135.3 / 107.8 in the table against
    # 133 / 104.2 in the sentence.  The loop's own magnet temperature — the one
    # section 3 prints as "Magnet temperature" — decides; the hottest element
    # has its own row in that table and is not this one.
    _row("Magnet temperature [°C]",
         lambda r: _fmt((r or {}).get("magnet_temp_c")
                        if (r or {}).get("magnet_temp_c") is not None
                        else (r or {}).get("magnet_temp_max_c"), 1))
    _row("Bearing seat temperature [°C]",
         lambda r: _fmt((r or {}).get("bearing_temp_c"), 1))
    _row("Torque ripple [%]",
         lambda r: _fmt(_em(r).get("T_ripple_pct")
                        if _em(r).get("T_ripple_pct") is not None
                        else _cpl_inv(r).get("ripple_pct"), 1))
    _row("Current THD [%]",
         lambda r: (PWM_THD_BY_CONSTRUCTION if r is sine and r is not None
                    else _fmt(_em(r).get("THD_I_pct")
                              if _em(r).get("THD_I_pct") is not None
                              else _cpl_inv(r).get("thd_i_pct"), 2)))
    _row("DC residual in the phase current [A]",
         lambda r: _fmt(_cpl_inv(r).get("dc_residual_A"), 2))

    notes: List[str] = []
    if sine is None:
        notes.append("This duty carries no sinusoidal record to be measured "
                     "against — the PWM column stands alone and the deltas "
                     "are not formed.")
    # …and whether the two columns really are one point (BT-4).
    _mismatch = pwm_same_point_note(
        _em(sine).get("T_em_avg_Nm") if sine is not None else None,
        _em(rec).get("T_em_avg_Nm"), _cpl_inv(rec), _dem_sine, _dem_pwm,
        (duty_point_error(col) or {}).get("pct"))
    if _mismatch:
        notes.append(_mismatch)
    if borrowed:
        notes.append(PWM_SINE_WATTS_BORROWED)
    if any(bool(_cpl_inv(r).get("dc_unconverged")) for r in recs if r):
        notes.append("A column whose run ended with a DC offset in the phase "
                     "current reports a ripple that belongs to the offset, "
                     "not to the machine.")
    _res_note = pwm_resolution_note(
        _em(sine).get("n_steps_per_period") if sine is not None else None,
        _em(rec).get("n_steps_per_period"))
    if _res_note:
        notes.append(_res_note)
    # …and what the inverter run's own current did against its setpoint (BL-2).
    _pe = point_error_cell(col)
    if _pe:
        notes.append("Operating point vs the duty's setpoint: %s; %s."
                     % (_pe, POINT_ERROR_WHY))
    return {"duty": col.get("duty"), "measured": True, "coupled": True,
            "header": header, "rows": rows,
            # NO WALL-CLOCK STAMP (MJ-5, audit v6): it was the only timestamp
            # left in the document, and the rest of it stopped printing them on
            # 2026-09-10.
            "source": "the coupled EM/thermal loop on the inverter",
            "point_note": "", "notes": notes, "line": ""}


def pwm_coupled_text(cols: List[Dict[str, Any]],
                     sec: Optional[Dict[str, int]] = None) -> List[str]:
    """Four sentences at most, per PWM duty — every number from the records."""
    out: List[str] = []
    for c in cols:
        rec = pwm_coupled_record(c)
        if rec is None:
            continue
        cc = pwm_coupled_columns(rec)
        sine = cc[0][1]
        if sine is None:
            continue
        duty, lbl = c.get("duty"), cc[1][0]
        _sine_em = (c.get("em_sine")
                    if isinstance(c.get("em_sine"), dict) else None)
        _vs, _vp = _cpl_view(sine, _sine_em), _cpl_view(rec)

        def _dg(keys: Tuple[str, ...], _p=_vp, _s=_vs) -> Optional[float]:
            a, b = _cpl_sum(_p, keys), _cpl_sum(_s, keys)
            return None if (a is None or b is None) else a - b

        tot_s = _cpl_sum(_vs, ("P_loss_total_W",))
        tot_p = _cpl_sum(_vp, ("P_loss_total_W",))
        if tot_s is not None and tot_p is not None:
            d = tot_p - tot_s
            out.append(
                "Duty '%s', %s: the carrier costs %s W on the sinusoid's %s W "
                "(%s of the electromagnetic loss) and it lands in the STATOR — "
                "%s W copper, %s W iron, against %s W in the magnets and %s W "
                "in the sleeve and shaft."
                % (duty, lbl, _fmt(d, 0), _fmt(tot_s, 0),
                   _pwm_share(d, tot_s).lstrip("+"),
                   _signed(_dg(("P_stranded_W",)), 0),
                   _signed(_dg(("P_core_W",)), 0),
                   _signed(_dg(("P_mag_W",)), 1),
                   _signed(_dg(("P_sleeve_W", "P_shaft_W")), 1)))
        # …and what those watts did, which is why the loop was run at all.
        _w_s, _w_p = _numf(sine.get("coil_temp_c")), _numf(rec.get("coil_temp_c"))
        # …through `shaft_view`, like the table above and every other section
        # (MJ-2): the sentence used to quote the records' own stored numbers and
        # read "falls from 98.49 % to 97.71 %" under a table saying 98.43 and
        # 97.61.
        def _sh(r: Any, v: Dict[str, Any], stored: Any) -> Optional[float]:
            sv = _cpl_shaft_view(c, r, v).get("eta_shaft")
            return 100.0 * float(sv) if sv is not None else _pwm_pct(stored)

        _e_s = _sh(sine, _vs, sine.get("efficiency_shaft"))
        _e_p = _sh(rec, _vp, rec.get("efficiency_shaft"))
        if _w_s is not None and _w_p is not None:
            out.append(
                "The loop settled the winding at %s against %s on the "
                "sinusoid, the magnets at %s against %s%s%s."
                % (_fmt(_w_p, 1, "°C"), _fmt(_w_s, 1, "°C"),
                   _fmt(rec.get("magnet_temp_c"), 1, "°C"),
                   _fmt(sine.get("magnet_temp_c"), 1, "°C"),
                   (", and the bearing seat at %s against %s"
                    % (_fmt(rec.get("bearing_temp_c"), 1, "°C"),
                       _fmt(sine.get("bearing_temp_c"), 1, "°C")))
                   if rec.get("bearing_temp_c") is not None else "",
                   (", so shaft efficiency falls from %s %% to %s %%, %s pp"
                    % (_fmt(_e_s, 2), _fmt(_e_p, 2), _signed(_e_p - _e_s, 2)))
                   if (_e_s is not None and _e_p is not None) else ""))
        # …the second carrier, when one was measured.
        alts = [r for _h, r in cc[2:] if r]
        if alts and tot_s is not None:
            a = alts[-1]
            _da = _cpl_sum(_cpl_view(a), ("P_loss_total_W",))
            if _da is not None:
                out.append(
                    "At %s the added loss is %s W instead of %s W — %s pp of "
                    "electromagnetic efficiency given back, while the bridge's "
                    "own switching loss, which is not in this model, roughly "
                    "doubles."
                    % (_cpl_label(a), _fmt(_da - tot_s, 0),
                       _fmt((tot_p - tot_s) if tot_p is not None else None, 0),
                       _fmt((_pwm_pct(_cpl_view(a).get("efficiency")) or 0.0)
                            - (_pwm_pct(_vp.get("efficiency")) or 0.0), 3)))
        # …and the ripple, which is two different quantities on one machine.
        _r_p = _numf(_vp.get("T_ripple_pct")
                     or _cpl_inv(rec).get("ripple_pct"))
        _r_s = _numf(_vs.get("T_ripple_pct"))
        if _r_p is not None and _r_s is not None:
            out.append(
                "Torque ripple reads %s %% on the bridge against %s %% on the "
                "sinusoid: the difference is at the carrier and the rotor "
                "inertia filters it, so the %s %% gate in %s is judged "
                "on the sinusoidal figure."
                % (_fmt(_r_p, 1), _fmt(_r_s, 1), _fmt(RIPPLE_LIMIT_PCT, 0),
                   sec_ref(sec, "warnings")))
    return out


def pwm_blocks(cols: List[Dict[str, Any]],
               brg: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """One block per duty: the coupled comparison where there is one, the
    carrier study where there is only that, and the refusal line otherwise."""
    out: List[Dict[str, Any]] = []
    for c in cols:
        if pwm_coupled_record(c) is not None:
            out.append(pwm_coupled_rows(c))
        else:
            out.append(pwm_rows([c], brg)[0])
    return out


def pwm_intro(cols: List[Dict[str, Any]],
              duty: Optional[str] = None) -> str:
    """Section 5's opening line — read off THE REPORT DUTY's own supply (MJ-1).

    ``duty`` is the duty the document is about.  Without it the old rule stands
    (any PWM duty in the configuration), which is what printed "every number
    elsewhere in this report is the PWM run" on a document whose every number
    is a sinusoid.
    """
    if not any_pwm(cols):
        return PWM_INTRO
    rep = _col_of(cols, duty) if duty else None
    if rep is None:
        # No duty named: say what is true of the configuration, not of a page.
        return ("At least one duty of this configuration was solved on the "
                "inverter; this section is what the carrier costs it, against "
                "the sinusoidal run it replaced.")
    if duty_drive(rep) == "pwm":
        return PWM_INTRO_COUPLED % (duty,)
    return PWM_INTRO_SINE_REPORT % (duty,)


def _pwm_page(st, cols: List[Dict[str, Any]],
              brg: Optional[Dict[str, Any]] = None,
              sec: Optional[Dict[str, int]] = None,
              duty: Optional[str] = None) -> List[Any]:
    from reportlab.platypus import Spacer

    out: List[Any] = [_para(PWM_HEADING, st["h1"])]
    out.append(_para(pwm_intro(cols, duty), st["body"]))
    blocks = pwm_blocks(cols, brg)
    if not any(b["measured"] for b in blocks):
        out.append(_para(PWM_NO_RECORD_AT_ALL, st["body"]))
        return out
    for b in blocks:
        out.append(_para("Duty '%s'" % b["duty"], st["h2"]))
        if not b["measured"]:
            out.append(_para(b["line"], st["note"]))
            continue
        n = max(1, len(b["header"]) - 1)
        lw = max(120.0, min(220.0, CONTENT_W - n * 72.0))
        cw = (CONTENT_W - lw) / n
        out.append(_table([b["header"]] + b["rows"], [lw] + [cw] * n,
                          header=True, size=7.6))
        out.append(Spacer(1, 2))
        if b.get("point_note"):
            out.append(_para(b["point_note"], st["note"]))
        if b["source"]:
            out.append(_para("Source: %s." % b["source"], st["note"]))
        for n in (b.get("notes") or []) if b.get("coupled") else []:
            out.append(_para(n, st["note"]))
    for par in pwm_coupled_text(cols, sec) + pwm_influence_text(cols, brg):
        out.append(_para(par, st["body"]))
        out.append(Spacer(1, 3))
    return out


def _thermal_page(st, th, cp, map_duty: Optional[str] = None,
                  field_src: Optional[Dict[str, Any]] = None,
                  run_note: str = "",
                  em: Optional[Dict[str, Any]] = None,
                  map_from_duty: bool = False,
                  map_rpm: Any = None, map_cur: Any = None,
                  mats: Optional[Dict[str, Any]] = None,
                  ctxs: Optional[Dict[str, Any]] = None,
                  pair: Optional[Dict[str, Any]] = None,
                  figs: Optional[List[int]] = None,
                  em_duty: Optional[str] = None,
                  sec: Optional[Dict[str, int]] = None,
                  detail_from_duty: bool = False,
                  col: Optional[Dict[str, Any]] = None) -> List[Any]:
    from reportlab.platypus import Spacer

    out: List[Any] = [_para(section_heading(sec, "thermal"), st["h1"])]
    out.append(_para(
        pair_owner_text("The temperature maps and the charts", pair,
                        pair_owner_tail(
                            "thermal", map_duty, map_from_duty,
                            "Per-duty temperatures are in %s."
                            % compare_ref(sec)),
                        first_fig=fig_ahead(figs))
        or thermal_map_owner_text(map_duty, map_from_duty,
                                  point_words(map_rpm, map_cur), sec),
        st["note"]))
    if run_note:
        out.append(_para(f'<font color="{WARN}"><b>{FLAG}</b></font> ' + run_note,
                         st["body"]))
    # NOTE (2026-09-10): a check lived here that compared the map's copper with
    # the summary's and cried "two different runs".  That WAS the wrong cause —
    # both numbers came from one run — so it was removed.  The investigation the
    # user then asked for found the real one, and it was not a reporting
    # question at all: the field-view payload the thermal solve reads took
    # `P_cu_total_solve_W`, the coupled solve's 2-D integral, which is the
    # ACTIVE LENGTH only.  The end windings — 542.7 W of the Ø200's 3,443.1 W,
    # 18.7 % — never reached the temperature field.  Fixed at source in
    # `routes/simulation.py` (the payload now takes the `P_cu_W` series, which
    # carries k_end); a thermal result solved before that fix still shows the
    # gap, and re-running thermal closes it.  Nothing to check for here any
    # more: the two numbers now measure the same winding.
    entry = th.get("field") or th.get("coupled")
    if not entry:
        out.append(_para(THERMAL_PAGE_UNSOLVED, st["warn"]))
        return out
    res = entry.get("result") or {}
    inner = res.get("field") if isinstance(res.get("field"), dict) else res
    out.append(_para(thermal_source_text(th, entry, em_duty, detail_from_duty),
                     st["note"]))

    out.append(_para("Boundary conditions", st["h2"]))
    for line in _cooling_words(res.get("cooling") or inner.get("cooling") or {}):
        out.append(_para("• " + line, st["body"]))

    # The stamp every chart on this page carries, as in Word (B9).
    _tag_t = _map_tag(map_duty, res.get("rpm", inner.get("rpm")) or map_rpm,
                      map_cur)

    out.append(_para("Temperatures", st["h2"]))
    trows = thermal_temp_rows(res, inner)
    if len(trows) > 1:
        out.append(_table(trows, [190, 110], header=True, size=7.6))
    out.append(_para(thermal_extremes_text(res, inner), st["note"]))
    # THE TEMPERATURE BARS (MJ-5): drawn only in the .docx until 2026-09-14.
    # …and for both duties since the same day — the bars are the one picture on
    # this page where rated beside peak IS the answer.
    _L, _R = (pair or {}).get("left"), (pair or {}).get("right")
    _note = (pair or {}).get("note") or ""
    _lims = temp_chart_limits(mats or {}, ctxs or {})
    from reportlab.platypus import KeepTogether as _KTt
    if _R:
        _bl = _temp_bars_png((_L or {}).get("th") or res,
                             (_L or {}).get("th_inner") or inner, _lims,
                             width_cm=PAIR_CM)
        _br = _temp_bars_png(_R.get("th") or {}, _R.get("th_inner") or {},
                             _lims, width_cm=PAIR_CM)
        _blk = _fig_pair(st, _bl, _br, pair_caption(
                TEMP_BARS_CAPTION, _L, _R, have=(bool(_bl), bool(_br)),
                numbers=pair_number_clause(
                    "hottest solid",
                    thermal_solid_max_c((_L or {}).get("th_inner") or inner),
                    thermal_solid_max_c(_R.get("th_inner") or {}), 1, "°C"),
                note=_note, map_kind="thermal"),
            max_height=PAIR_MAX_H, figs=figs)
        if _blk is not None:
            out.append(_blk)
    else:
        _bars = _image(_temp_bars_png(res, inner, _lims,
                                      width_cm=MAP_FULL_CM),
                       CONTENT_W, max_height=PAIR_MAX_H)
        if _bars is not None:
            _bars.hAlign = "LEFT"
            out.append(_KTt([_bars, _para(
                "Fig. [%s] — %s"
                % (_tag_t, caption_with_provenance(TEMP_BARS_CAPTION, _L,
                                                   "thermal")),
                st["note"])]))

    # ── the budget and the picture, side by side ────────────────────────────
    # One page, not two.  The temperature map under a full-width heat budget
    # spilled onto a seventh page, and a report the user asked to keep short
    # does not get an extra page for whitespace.
    budget_t = _table(thermal_budget_rows(res, inner, em), [186, 60],
                      header=True, size=7.6)
    # THE WATERFALL (MJ-5) — and with it the hatched "mechanical heat outside
    # the section" bar the .docx has carried since B2, which the PDF reader
    # never saw.
    _wf_block: List[Any] = []
    if _R:
        _hl = _heat_waterfall_png((_L or {}).get("th") or res,
                                  (_L or {}).get("th_inner") or inner,
                                  width_cm=PAIR_CM)
        _hr = _heat_waterfall_png(_R.get("th") or {}, _R.get("th_inner") or {},
                                  width_cm=PAIR_CM)
        _blk = _fig_pair(st, _hl, _hr, pair_caption(
                heat_chart_caption_pair(
                    (_L or {}).get("th") or res,
                    (_L or {}).get("th_inner") or inner,
                    _R.get("th") or {}, _R.get("th_inner") or {}),
                _L, _R, have=(bool(_hl), bool(_hr)), note=_note),
            max_height=PAIR_MAX_H, figs=figs)
        if _blk is not None:
            _wf_block = [_blk]
    else:
        _wf_img = _image(_heat_waterfall_png(res, inner,
                                             width_cm=MAP_FULL_CM),
                         CONTENT_W, max_height=PAIR_MAX_H)
        if _wf_img is not None:
            from reportlab.platypus import KeepTogether as _KTh
            _wf_img.hAlign = "LEFT"
            _wf_block = [_KTh([_wf_img, _para(
                "Fig. [%s] — %s" % (_tag_t, heat_chart_caption(res, inner)),
                st["note"])])]

    # THE TEMPERATURE MAP, each duty on ITS OWN colour bar (2026-09-14, the
    # user's second look: on the union the rated map was one flat teal shape).
    _pair_blk = None
    if _R:
        _ml, _mr = thermal_map_pair(
            ((_L or {}).get("src") or {}).get("thermal")
            or (_L or {}).get("th") or field_src or res,
            (_R.get("src") or {}).get("thermal") or _R.get("th"),
            width_cm=PAIR_CM)
        _pair_blk = _fig_pair(st, _ml, _mr, pair_caption(
                THERMAL_MAP_CAPTION, _L, _R, have=(bool(_ml), bool(_mr)),
                numbers=pair_number_clause(
                    "hottest solid",
                    thermal_solid_max_c((_L or {}).get("th_inner") or inner),
                    thermal_solid_max_c(_R.get("th_inner") or {}), 1, "°C"),
                note=_note, map_kind="thermal"),
            max_height=PAIR_MAX_H, figs=figs)
    img = None if _R else _image(
        _thermal_map(field_src or res, width_cm=MAP_FULL_CM), CONTENT_W,
        max_height=PAIR_MAX_H)
    if _pair_blk is not None:
        out.append(Spacer(1, 4))
        out.append(budget_t)
        _rec = thermal_budget_reconcile_text(res, inner, em, map_duty,
                                             em_duty=em_duty)
        if _rec:
            out.append(_para(_rec, st["note"]))
        out += _wf_block
        out.append(Spacer(1, 4))
        out.append(_pair_blk)
    elif img is not None:
        # Full width, caption under it (user 2026-09-08: "рисунки на всю
        # ширину страницы, а то ничего не видно").
        from reportlab.platypus import KeepTogether
        out.append(Spacer(1, 4))
        out.append(budget_t)
        _rec = thermal_budget_reconcile_text(res, inner, em, map_duty,
                                             em_duty=em_duty)
        if _rec:
            out.append(_para(_rec, st["note"]))
        out += _wf_block
        out.append(Spacer(1, 4))
        out.append(KeepTogether([img, _para(
            "Fig. [%s] — %s" % (
                _map_tag(map_duty,
                         res.get("rpm", inner.get("rpm")) or map_rpm, map_cur),
                caption_with_provenance(THERMAL_MAP_CAPTION, _L, "thermal")),
            st["note"])]))
    else:
        out.append(Spacer(1, 4))
        out.append(budget_t)
        _rec = thermal_budget_reconcile_text(res, inner, em, map_duty,
                                             em_duty=em_duty)
        if _rec:
            out.append(_para(_rec, st["note"]))
        out += _wf_block
        out.append(_para(THERMAL_MAP_MISSING, st["note"]))

    if cp:
        c = cp.get("coupling") or {}
        out.append(_para("Coupled loop", st["h2"]))
        out.append(_para(coupled_loop_text(cp), st["body"]))
        _cw = coupled_warning_words(c, (duty_point_error(col or {}) or {})
                                    .get("pct"))
        if _cw:
            out.append(_para(f"{FLAG} {_cw}", st["warn"]))
    return out


# ---------------------------------------------------------------------------
# Duty cycle  (2026-09-14)
# ---------------------------------------------------------------------------
# A robot joint does not run to a steady state.  Its peak point is a two-second
# pull repeated every minute, and the number that decides whether the winding
# survives is the peak of the SETTLED cycle — the fixed point the machine
# reaches after it has been through the cycle enough times to repeat itself.
# The steady-state map two sections above answers a different question and
# answers it badly for this duty: it is far too hot for a short pull and too
# cold for a long one.
#
# The section is printed only when a duty carries a cycle record.  Everything in
# it is read from that record; nothing here integrates anything.

DUTY_CYCLE_INTRO = (
    "An intermittent duty is judged on the SETTLED cycle — the machine is put "
    "through the cycle until it repeats itself, and the peak of that cycle is "
    "what the insulation and the magnets see. The temperatures in the warnings "
    "section are these, not the steady states in the thermal section.")

DUTY_CYCLE_NOT_RUN = (
    "No duty cycle has been run for this duty — the temperatures for it "
    "elsewhere in this report are steady states.")

#: What the warnings section says its temperatures are, on a cycle duty.
DUTY_CYCLE_TEMP_BASIS = "peak of the settled %s cycle"

DUTY_CYCLE_PROFILE_CAPTION = (
    "The cycle as it was integrated: the shaft torque of the duty running in "
    "each segment, over one period, with the time-weighted mean.")
DUTY_CYCLE_TEMPS_CAPTION = (
    "One settled cycle: the four lumped nodes and the winding hot spot, "
    "against the limits they are judged on.")
DUTY_CYCLE_ED_CAPTION = (
    "The winding peak against the on-time share — where that curve crosses the "
    "limit is the allowable ED.")
DUTY_CYCLE_ED_CYCLE_CAPTION = (
    "The allowable ED against the length of the cycle — a short cycle rides "
    "the winding's own heat capacity, a long one settles toward the steady "
    "state and buys less.")

DUTY_CYCLE_NOT_CONVERGED = (
    "This cycle had not settled when the solver stopped: the peak below is the "
    "last cycle's, not a fixed point, and the real machine runs hotter.")


def _dc_seg_words(rec: Dict[str, Any]) -> str:
    """The segments of one cycle in one cell: what runs, for how long, at what
    loss."""
    segs = ((rec or {}).get("spec") or {}).get("segments") or []
    bits = []
    for s in segs:
        if not isinstance(s, dict):
            continue
        bits.append("%s — %s at %s"
                    % (str(s.get("duty") or "unpowered"),
                       _fmt(s.get("t_s"), 1, "s"),
                       _fmt(s.get("total_W"), 1, "W")))
    return " · ".join(bits) or "—"


# ── THE FOUND REGIME, FIRST (2026-09-15) ────────────────────────────────────
# The one line an integrator reads this section for: what this machine may
# actually be run at.  Everything under it — the table, the three curves — is
# the evidence; the answer used to be the ninth row of a twenty-row table.
# Every clause is optional and every one of them is read field by field, so a
# record written before these fields existed prints exactly the part of the
# sentence it can support, and a record with none of them prints nothing.


def _dc_part_limit(lim: Dict[str, Any], part: str) -> Optional[float]:
    """The limit the named part was judged against, whichever key holds it."""
    lc = lim.get("limits_c") if isinstance(lim.get("limits_c"), dict) else {}
    v = _numf(lc.get(part))
    if v is None and str(part).startswith("wind"):
        v = _numf(lim.get("winding_limit_c"))
    if v is None and str(part).startswith("mag"):
        v = _numf(lim.get("magnet_limit_c"))
    return v


def _dc_at_allowable_words(lim: Dict[str, Any]) -> str:
    """``limits.at_allowable`` in one cell — the hot spot, its mean, and the
    nodes, in the order a reader asks for them."""
    at = (lim or {}).get("at_allowable")
    if not isinstance(at, dict) or not at:
        return ""
    bits: List[str] = []
    for k, label in (("winding_hot_peak_c", "winding hot spot"),
                     ("winding_hot_mean_c", "winding mean"),
                     ("magnet_peak_c", "magnets")):
        if _numf(at.get(k)) is not None:
            bits.append("%s %s" % (label, _fmt(at.get(k), 1, "°C")))
    pk = at.get("peak_c") if isinstance(at.get("peak_c"), dict) else {}
    for node in ("winding", "stator", "rotor", "magnet"):
        if node == "magnet" and _numf(at.get("magnet_peak_c")) is not None:
            continue
        if _numf(pk.get(node)) is not None:
            bits.append("%s %s" % (node, _fmt(pk.get(node), 1, "°C")))
    return " · ".join(bits)


def duty_cycle_ed_vs_cycle(rec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """``limits.ed_vs_cycle`` as a list of dicts, sorted by cycle length.

    The solver writes ``{cycle_s, ed_allowable_pct, t_on_s, limiting_part,
    winding_hot_peak_c, magnet_peak_c, note}``; a bare ``[cycle_s, ed_pct]``
    pair is accepted too, so nothing in the chart or the sentence depends on
    which shape a record was written in.
    """
    out: List[Dict[str, Any]] = []
    for p in ((rec or {}).get("limits") or {}).get("ed_vs_cycle") or []:
        if isinstance(p, dict):
            c, e = _numf(p.get("cycle_s")), _numf(p.get("ed_allowable_pct"))
            d = dict(p)
        elif isinstance(p, (list, tuple)) and len(p) >= 2:
            c, e = _numf(p[0]), _numf(p[1])
            d = {"cycle_s": c, "ed_allowable_pct": e}
        else:
            continue
        if c is None or e is None or c <= 0.0:
            continue
        d["cycle_s"], d["ed_allowable_pct"] = float(c), float(e)
        out.append(d)
    out.sort(key=lambda d: d["cycle_s"])
    return out


def _dc_t_on(rec: Dict[str, Any], ed: Optional[float],
             cyc: Optional[float]) -> Optional[float]:
    """The ON-time behind an ED ratio: the solver's own ``t_on_s`` for that
    cycle length, or the product when the record carries only the two."""
    if cyc is None:
        return None
    for p in duty_cycle_ed_vs_cycle(rec):
        # …and only for the ratio that row IS: the stored t_on belongs to the
        # allowable ED, and printing it beside a requested one would be a
        # third number that is neither.
        if (abs(p["cycle_s"] - float(cyc)) <= 1e-6 * max(1.0, float(cyc))
                and ed is not None
                and abs(p["ed_allowable_pct"] - float(ed)) <= 0.05
                and _numf(p.get("t_on_s")) is not None):
            return _numf(p.get("t_on_s"))
    return (float(ed) / 100.0 * float(cyc)) if ed is not None else None


def duty_cycle_regime_text(rec: Dict[str, Any]) -> str:
    """The regime this machine may be run at, in one sentence — ``""`` when
    the record says nothing that could fill one.

    ``"Allowable regime: S3, 60 s cycle — ED 21.6 % (12.9 s on), limited by
    the winding at 200 °C; one pull S2: 26.6 s from cold, 20.7 s from the
    rated state; magnets 111 °C at that point."``

    ``limits.ed_found`` is the gate: true means the tool SEARCHED for the ratio
    and this is the allowable one, false means the ED was handed to it and the
    ratio is the requested one — two different claims, and printing the second
    under the first word would be the one sentence this report may not print.
    Every clause is read field by field, so a record written before any of
    these fields existed prints the part it can support and nothing else.
    """
    lim = (rec or {}).get("limits") or {}
    spec = (rec or {}).get("spec") or {}
    # ── which ratio is this, and what may it be called ────────────────────
    found = lim.get("ed_found")
    if found is None and spec.get("found") is not None:
        found = bool(spec.get("found"))
    ed_all = _numf(lim.get("ed_allowable_pct"))
    ed_req = _numf(lim.get("ed_requested_pct"))
    if ed_req is None and spec.get("ed_given") is not False:
        ed_req = _numf(spec.get("ed_pct"))
    ed = ed_all if found is not False else (ed_req if ed_req is not None
                                            else ed_all)
    cyc = _numf(lim.get("ed_cycle_s")) or _numf(spec.get("cycle_s"))
    part = str(lim.get("ed_limiting_part") or lim.get("limiting_part") or "")
    # ── the single pull beside the repeated one ───────────────────────────
    # An S2 answer is a different question from an S3 one — how long may I
    # hold it ONCE — and it depends on where the machine starts, so each
    # start is named rather than all of them being called "cold".
    _t0 = _numf(spec.get("t_start_c"))
    s2 = [(lim.get("s2_time_to_limit_s"),
           "from cold" if _t0 is None or _t0 <= 40.0
           else "from %s" % _fmt(_t0, 0, "°C")),
          (lim.get("s2_from_rated_s"), "from the rated state"),
          (lim.get("s2_from_cycle_mean_s"), "from the cycle mean")]
    s2w = ["%s %s" % (_fmt(v, 1, "s"), w) for v, w in s2
           if _numf(v) is not None]
    head = duty_cycle_kind(rec)
    if cyc is not None:
        head += ", %s cycle" % _fmt(cyc, 1, "s")
    bits: List[str] = []
    if ed is not None:
        _ed = "ED %s" % _fmt(ed, 1, "%")
        _on = _dc_t_on(rec, ed, cyc)
        if _on is not None:
            _ed += " (%s on)" % _fmt(_on, 1, "s")
        _pl = _dc_part_limit(lim, part) if part else None
        if part and _pl is not None:
            _ed += ", limited by the %s at %s" % (part, _fmt(_pl, 0, "°C"))
        elif part:
            _ed += ", limited by the %s" % part
        bits.append("%s — %s" % (head, _ed))
        if found is False and ed_all is not None and abs(ed_all - ed) > 0.05:
            bits.append("allowable ED %s" % _fmt(ed_all, 1, "%"))
    elif s2w and head.strip():
        # A class with no ED at all still names the regime the single pull
        # belongs to; a class with neither says nothing at all.
        bits.append(head)
    if s2w:
        bits.append("one pull S2: " + ", ".join(s2w))
    # …and what the magnets reach there, because the winding limit is the one
    # the ED was solved on and the magnets are the part nobody can re-wind.
    _at = lim.get("at_allowable") if isinstance(lim.get("at_allowable"),
                                                dict) else {}
    _mag = _numf(_at.get("magnet_peak_c"))
    if _mag is None:
        _mag = _numf((_at.get("peak_c") or {}).get("magnet")
                     if isinstance(_at.get("peak_c"), dict) else None)
    if _mag is not None and bits:
        bits.append("magnets %s at that point" % _fmt(_mag, 0, "°C"))
    if not bits:
        return ""
    lead = "Allowable regime" if found is not False else "Requested regime"
    return "%s: %s." % (lead, "; ".join(bits))


def duty_cycle_profile_text(rec: Dict[str, Any]) -> str:
    """The one sentence that says what cycle this is."""
    spec = (rec or {}).get("spec") or {}
    cyc = (rec or {}).get("cycle") or {}
    bits = ["%s duty" % duty_cycle_kind(rec)]
    if _numf(spec.get("ed_pct")) is not None:
        bits.append("ED %s requested" % _fmt(spec.get("ed_pct"), 1, "%"))
    if _numf(spec.get("cycle_s")) is not None:
        bits.append("a %s cycle" % _fmt(spec.get("cycle_s"), 1, "s"))
    if spec.get("rest_duty"):
        bits.append("resting on '%s'" % spec.get("rest_duty"))
    if spec.get("calibration_duty"):
        bits.append("the lumped network calibrated on '%s'"
                    % spec.get("calibration_duty"))
    tail = (" It settled after %s cycles (residual %s)."
            % (_fmt(cyc.get("n_cycles"), 0),
               _fmt(cyc.get("residual_K"), 3, "K"))
            if cyc.get("converged") else "")
    return ", ".join(bits) + "." + tail


def duty_cycle_rows(col: Dict[str, Any],
                    ctx: Optional[Dict[str, Any]] = None) -> List[List[str]]:
    """One duty's cycle as the table both renderers print.  Header included."""
    rec = duty_cycle_record(col) or {}
    spec = rec.get("spec") or {}
    cyc = rec.get("cycle") or {}
    lim = rec.get("limits") or {}
    split = rec.get("split") or {}
    net = rec.get("network") or {}
    peak = cyc.get("peak_c") if isinstance(cyc.get("peak_c"), dict) else {}
    mean = cyc.get("mean_c") if isinstance(cyc.get("mean_c"), dict) else {}
    cap = net.get("C_J_per_K") if isinstance(net.get("C_J_per_K"), dict) else {}
    rows: List[List[str]] = [["The cycle", "Value", "What it is"]]

    def R(label: str, value: str, what: str) -> None:
        if str(value).strip() not in ("", "—", "— / —"):
            rows.append([label, value, what])

    R("Duty class", duty_cycle_kind(rec)
      + ("" if _numf(spec.get("ed_pct")) is None
         else ", ED %s" % _fmt(spec.get("ed_pct"), 1, "%")),
      "the class the cycle was asked for; ED is the share of the cycle this "
      "point is on for")
    R("Cycle time", _fmt(spec.get("cycle_s"), 1, "s"),
      "one period of the repeated cycle")
    R("Segments", _dc_seg_words(rec),
      "what runs in each part of the cycle and the loss it puts in")
    R("Rest duty", str(spec.get("rest_duty") or "—"),
      "the point the machine falls back to between pulls")
    R("Calibration duty", str(spec.get("calibration_duty") or "—"),
      "the steady map the lumped conductances were fitted to")
    # THE ANSWER, in the order a reader asks for it.
    R("Winding hot spot, peak / mean",
      _pair(cyc.get("winding_hot_peak_c"), cyc.get("winding_hot_mean_c"), 1)
      + " °C",
      "the hot spot through the settled cycle — the number the insulation "
      "limit is judged on")
    for part, what in (("winding", "the winding node's own mean temperature"),
                       ("stator", "the stator core"),
                       ("rotor", "the rotor body"),
                       ("magnet", "the magnets")):
        R("%s, peak / mean" % part.capitalize(),
          _pair(peak.get(part), mean.get(part), 1) + " °C", what)
    R("Winding limit", _fmt(lim.get("winding_limit_c"), 0, "°C"),
      str(lim.get("winding_limit_note") or "the insulation class"))
    R("S2 time to the limit", _fmt(lim.get("s2_time_to_limit_s"), 1, "s"),
      str(lim.get("s2_note") or
          ("from %s: how long this point may be held ONCE before %s reaches "
           "its limit"
           % (_fmt(spec.get("t_start_c"), 0, "°C")
              if _numf(spec.get("t_start_c")) is not None else "cold",
              str(lim.get("s2_limiting_part") or "the hottest part")))))
    # …FROM A MACHINE THAT IS ALREADY WARM (2026-09-15).  A robot arm is never
    # cold when the peak is asked for: it has been running its rated point, or
    # its own cycle, and the single pull it has left is shorter than the
    # brochure number by exactly that head start.
    R("…from the rated state", _fmt(lim.get("s2_from_rated_s"), 1, "s"),
      str(lim.get("s2_from_rated_note") or
          ("the same single pull, started from the steady state of the rest "
           "duty" + ("" if _numf(lim.get("s2_from_rated_start_c")) is None
                     else " (%s)"
                     % _fmt(lim.get("s2_from_rated_start_c"), 1, "°C")))))
    R("…from the cycle mean", _fmt(lim.get("s2_from_cycle_mean_s"), 1, "s"),
      str(lim.get("s2_from_cycle_mean_note")
          or "…and started from the mean temperature of the settled cycle"))
    R("ED allowable / requested",
      _pair(lim.get("ed_allowable_pct"), lim.get("ed_requested_pct"), 1) + " %",
      str(lim.get("ed_note") or
          ("the share at which %s sits exactly on its limit, against the one "
           "asked for" % str(lim.get("ed_limiting_part")
                             or lim.get("limiting_part")
                             or "the hottest part")))
      + ("" if _numf(lim.get("ed_cycle_s")) is None
         else "; on a %s cycle" % _fmt(lim.get("ed_cycle_s"), 1, "s")))
    R("At the allowable ED", _dc_at_allowable_words(lim),
      "what the parts reach when the cycle runs at the allowable ED rather "
      "than the requested one")
    R("Heat out, stator side / rotor side",
      _pair(split.get("stator_side_W"), split.get("rotor_side_W"), 1) + " W",
      "where the cycle-averaged loss leaves — the split a mount and a bore "
      "cannot change the sum of")
    R("…as a share",
      _pair(split.get("stator_pct"), split.get("rotor_pct"), 1) + " %",
      "the same split in per cent of the generated loss")
    R("Settled", ("yes, after %s cycles" % _fmt(cyc.get("n_cycles"), 0)
                  if cyc.get("converged") else
                  "NO — stopped after %s cycles" % _fmt(cyc.get("n_cycles"), 0)),
      "the cycle repeats itself to within %s"
      % _fmt(cyc.get("residual_K"), 3, "K"))
    R("Thermal capacities",
      " · ".join("%s %s" % (k, _fmt(cap.get(k), 1, "J/K"))
                 for k in ("winding", "stator", "rotor", "magnet")
                 if _numf(cap.get(k)) is not None),
      "mass × specific heat per node — what makes a short pull survivable")
    return rows


def duty_cycle_torques(cols: Optional[List[Dict[str, Any]]]
                       ) -> Dict[str, float]:
    """Shaft torque [N·m] per duty NAME, for the cycle's profile figure.

    The duty entry's own ``torque_nm`` first — it is what the configuration
    says this point is for, and what the catalog and the datasheet print — and
    the run's own 2-D mean times the 3-D end-effect factor when the entry
    carries none, which is the same arithmetic page 1 uses for shaft power.
    A duty whose torque is unknown is simply absent, so the figure can say so
    rather than draw a zero.
    """
    out: Dict[str, float] = {}
    for c in (cols or []):
        name = str(c.get("duty") or "")
        if not name:
            continue
        t = _numf((c.get("d") or {}).get("torque_nm"))
        if t is None:
            em = c.get("em") or {}
            t2d = _numf(_g(em, "T_em_avg_Nm"))
            if t2d is not None:
                t = t2d * (_numf(_g(em, "end3d.k_flux")) or 1.0)
        if t is not None:
            out[name] = float(t)
    return out


def duty_cycle_torque_profile(rec: Dict[str, Any],
                              torques: Optional[Dict[str, float]] = None
                              ) -> Optional[Dict[str, Any]]:
    """The cycle's torque step: ``{'steps': [(t0, t1, T, name)], 'mean_nm'}``.

    ``None`` when the cycle names a duty whose torque nobody stored — a torque
    profile with a guessed zero in it is worse than no figure at all.  An
    unpowered segment is a real 0 N·m and is not a guess.
    """
    segs = [s for s in (((rec or {}).get("spec") or {}).get("segments") or [])
            if isinstance(s, dict) and _numf(s.get("t_s")) is not None]
    if not segs:
        return None
    tq = torques or {}
    steps: List[Tuple[float, float, float, str]] = []
    t = 0.0
    for s in segs:
        name = str(s.get("duty") or "")
        if name:
            if name not in tq:
                return None
            val = float(tq[name])
        else:
            val = 0.0
        d = float(_numf(s.get("t_s")) or 0.0)
        steps.append((t, t + d, val, name or "unpowered"))
        t += d
    span = t
    if span <= 0:
        return None
    mean = sum(v * (b - a) for a, b, v, _n in steps) / span
    return {"steps": steps, "mean_nm": mean, "span_s": span}


def _dc_profile_png(rec: Dict[str, Any],
                    torques: Optional[Dict[str, float]] = None,
                    width_cm: float = 22.0,
                    px: int = 2200) -> Optional[bytes]:
    """The SHAFT TORQUE of one cycle, as a step (2026-09-15).

    It used to be the loss each segment puts in; the losses are in the table
    and in the split, and what a robot integrator reads a cycle for is the
    torque it delivers and the mean torque that comes out of it.
    """
    prof = duty_cycle_torque_profile(rec, torques)
    if prof is None:
        return None
    try:
        import numpy as np
        steps = prof["steps"]
        xs = [steps[0][0]] + [b for _a, b, _v, _n in steps]
        ys = [v for _a, _b, v, _n in steps]
        fig, ax = _fig(width_cm, px, aspect=_chart_aspect(0.34, width_cm))
        ax.step(np.asarray(xs), np.asarray(ys + [ys[-1]]), where="post",
                color="#2e86ff", lw=1.8, zorder=3)
        ax.fill_between(np.asarray(xs), 0.0, np.asarray(ys + [ys[-1]]),
                        step="post", color="#2e86ff", alpha=0.10, zorder=1)
        lo, hi = min(ys + [0.0]), max(ys + [0.0])
        _mean = float(prof["mean_nm"])
        ax.axhline(_mean, color="#1E7A3C", lw=1.0, ls="--", zorder=2)
        ax.annotate("mean %s" % _fmt(_mean, 1, "N·m"), (xs[0], _mean),
                    textcoords="offset points", xytext=(2, 3), ha="left",
                    fontsize=_chart_fs(8.0, width_cm), color="#1E7A3C")
        for a, b, v, name in steps:
            ax.annotate(name, ((a + b) / 2.0, v), textcoords="offset points",
                        xytext=(0, 6), ha="center",
                        fontsize=_chart_fs(8.0, width_cm), color="#1f2937")
        _pad = (hi - lo) or (abs(hi) or 1.0)
        ax.set_ylim(lo - 0.06 * _pad, hi + 0.28 * _pad)
        ax.set_xlabel("time in the cycle [s]", fontsize=9)
        ax.set_ylabel("shaft torque [N·m]", fontsize=9)
        ax.set_title(_chart_title("Cycle torque profile", width_cm),
                     fontsize=10)
        ax.grid(True, color="#EEEEEE", lw=0.6)
        return _finish(fig)
    except Exception as exc:                                # noqa: BLE001
        log.debug("report: no duty-cycle torque chart (%s)", exc)
        return None


#: The colour each lumped node is drawn in, on both cycle charts.
_DC_NODE_INK = {"winding": "#C0392B", "stator": "#2e86ff",
                "rotor": "#7B61FF", "magnet": "#1E7A3C"}


def _dc_temps_png(rec: Dict[str, Any], winding_limit: Any = None,
                  magnet_limit: Any = None, width_cm: float = 22.0,
                  px: int = 2200) -> Optional[bytes]:
    """The four nodes and the hot spot through one settled cycle."""
    cyc = (rec or {}).get("cycle") or {}
    ts = cyc.get("t_s")
    if not isinstance(ts, list) or len(ts) < 2:
        return None
    try:
        import numpy as np
        x = np.asarray(ts, float)
        fig, ax = _fig(width_cm, px, aspect=_chart_aspect(0.36, width_cm))
        blk = cyc.get("T_c") if isinstance(cyc.get("T_c"), dict) else {}
        for name in ("winding", "stator", "rotor", "magnet"):
            y = blk.get(name)
            if not isinstance(y, list) or len(y) != len(ts):
                continue
            ax.plot(x, np.asarray(y, float), lw=1.4,
                    color=_DC_NODE_INK.get(name, "#9aa4b2"), label=name,
                    zorder=3)
        hot = cyc.get("winding_hot_c")
        if isinstance(hot, list) and len(hot) == len(ts):
            ax.plot(x, np.asarray(hot, float), lw=1.9, ls="--",
                    color="#C0392B", label="winding hot spot", zorder=4)
        for lim, lbl, ink in ((_numf(winding_limit), "winding limit", "#C0392B"),
                              (_numf(magnet_limit), "magnet limit", "#1E7A3C")):
            if lim is None:
                continue
            ax.axhline(lim, color=ink, lw=1.0, ls=":", zorder=2)
            ax.annotate("%s %s" % (lbl, _fmt(lim, 0, "°C")),
                        (x[0], lim), textcoords="offset points",
                        xytext=(2, 3), ha="left",
                        fontsize=_chart_fs(8.0, width_cm), color=ink)
        ax.set_xlabel("time in the cycle [s]", fontsize=9)
        ax.set_ylabel("temperature [°C]", fontsize=9)
        ax.set_title(_chart_title("One settled cycle", width_cm), fontsize=10)
        ax.grid(True, color="#EEEEEE", lw=0.6)
        ax.legend(fontsize=_chart_fs(8.0, width_cm), frameon=False, ncol=3,
                  loc="lower right")
        return _finish(fig)
    except Exception as exc:                                # noqa: BLE001
        log.debug("report: no duty-cycle temperature chart (%s)", exc)
        return None


def _dc_ed_png(rec: Dict[str, Any], width_cm: float = 22.0,
               px: int = 2200) -> Optional[bytes]:
    """The winding peak against the on-time share, with the two EDs marked."""
    lim = (rec or {}).get("limits") or {}
    curve = [p for p in (lim.get("ed_curve") or [])
             if isinstance(p, (list, tuple)) and len(p) >= 2
             and _numf(p[0]) is not None and _numf(p[1]) is not None]
    if len(curve) < 2:
        return None
    try:
        import numpy as np
        x = np.asarray([float(p[0]) for p in curve])
        y = np.asarray([float(p[1]) for p in curve])
        fig, ax = _fig(width_cm, px, aspect=_chart_aspect(0.34, width_cm))
        ax.plot(x, y, color="#2e86ff", lw=1.8, zorder=3)
        wl = _numf(lim.get("winding_limit_c"))
        if wl is not None:
            ax.axhline(wl, color="#C0392B", lw=1.0, ls=":", zorder=2)
            ax.annotate("limit %s" % _fmt(wl, 0, "°C"), (x[0], wl),
                        textcoords="offset points", xytext=(2, 3), ha="left",
                        fontsize=_chart_fs(8.0, width_cm), color="#C0392B")
        for v, lbl, ink in ((_numf(lim.get("ed_allowable_pct")), "allowable",
                             "#1E7A3C"),
                            (_numf(lim.get("ed_requested_pct")), "requested",
                             "#B7791F")):
            if v is None:
                continue
            ax.axvline(v, color=ink, lw=1.0, ls="--", zorder=2)
            ax.annotate("%s %s" % (lbl, _fmt(v, 1, "%")), (v, float(y.max())),
                        textcoords="offset points", xytext=(3, -8), ha="left",
                        fontsize=_chart_fs(8.0, width_cm), color=ink)
        ax.set_xlabel("on-time share ED [%]", fontsize=9)
        ax.set_ylabel("winding peak in the settled cycle [°C]", fontsize=9)
        ax.set_title(_chart_title("What the ED buys", width_cm), fontsize=10)
        ax.grid(True, color="#EEEEEE", lw=0.6)
        return _finish(fig)
    except Exception as exc:                                # noqa: BLE001
        log.debug("report: no duty-cycle ED chart (%s)", exc)
        return None


def _dc_ed_cycle_png(rec: Dict[str, Any], width_cm: float = 22.0,
                     px: int = 2200) -> Optional[bytes]:
    """The allowable ED against the length of the cycle, on a log time axis.

    The ED curve above answers "how much of a 60 s cycle"; this one answers
    "and what if the cycle were 10 s, or 600" — the question a machine builder
    asks next, and the one the cycle length is chosen by.  Step, not a smooth
    line: the record holds the cycle lengths that were actually solved, and
    drawing a curve between them would claim points nobody computed.
    """
    lim = (rec or {}).get("limits") or {}
    pts = duty_cycle_ed_vs_cycle(rec)
    if len(pts) < 2:
        return None
    try:
        import numpy as np
        x = np.asarray([p["cycle_s"] for p in pts], float)
        y = np.asarray([p["ed_allowable_pct"] for p in pts], float)
        fig, ax = _fig(width_cm, px, aspect=_chart_aspect(0.34, width_cm))
        ax.step(x, y, where="post", color="#1E7A3C", lw=1.6, zorder=3)
        ax.plot(x, y, "o", color="#1E7A3C", ms=3.2, zorder=4)
        ax.set_xscale("log")
        _cs = (_numf(lim.get("ed_cycle_s"))
               or _numf(((rec or {}).get("spec") or {}).get("cycle_s")))
        _ed = _numf(lim.get("ed_allowable_pct"))
        if _cs and _cs > 0.0:
            ax.axvline(_cs, color="#B7791F", lw=1.0, ls="--", zorder=2)
            ax.annotate("this cycle %s" % _fmt(_cs, 1, "s"),
                        (_cs, float(y.max())), textcoords="offset points",
                        xytext=(4, -8), ha="left", color="#B7791F",
                        fontsize=_chart_fs(8.0, width_cm))
        if _ed is not None:
            ax.axhline(_ed, color="#2e86ff", lw=1.0, ls=":", zorder=2)
            ax.annotate("allowable %s" % _fmt(_ed, 1, "%"),
                        (float(x[0]), _ed), textcoords="offset points",
                        xytext=(2, 3), ha="left", color="#2e86ff",
                        fontsize=_chart_fs(8.0, width_cm))
        ax.set_xlabel("cycle length [s], log scale", fontsize=9)
        ax.set_ylabel("allowable ED [%]", fontsize=9)
        ax.set_title(_chart_title("What the cycle length buys", width_cm),
                     fontsize=10)
        ax.grid(True, which="both", color="#EEEEEE", lw=0.6)
        return _finish(fig)
    except Exception as exc:                                # noqa: BLE001
        log.debug("report: no duty-cycle ED-vs-cycle chart (%s)", exc)
        return None


def duty_cycle_figures(rec: Dict[str, Any], winding_limit: Any = None,
                       magnet_limit: Any = None,
                       width_cm: float = 22.0,
                       torques: Optional[Dict[str, float]] = None
                       ) -> List[Tuple[Optional[bytes], str]]:
    """The pictures of one cycle, each with its one-sentence caption.

    ``torques`` is the configuration's shaft torque per duty name
    (:func:`duty_cycle_torques`); without it the profile figure has nothing to
    draw and drops out.  The fourth figure — the allowable ED against the
    cycle length — appears only on a record that carries ``ed_vs_cycle``."""
    return [
        (_dc_profile_png(rec, torques, width_cm), DUTY_CYCLE_PROFILE_CAPTION),
        (_dc_temps_png(rec, winding_limit, magnet_limit, width_cm),
         DUTY_CYCLE_TEMPS_CAPTION),
        (_dc_ed_png(rec, width_cm), DUTY_CYCLE_ED_CAPTION),
        (_dc_ed_cycle_png(rec, width_cm), DUTY_CYCLE_ED_CYCLE_CAPTION),
    ]


def duty_cycle_limits(col: Dict[str, Any],
                      ctx: Optional[Dict[str, Any]] = None
                      ) -> Tuple[Any, Any]:
    """``(winding limit, magnet limit)`` for this duty's charts.

    The cycle's own limits first — they are what it was judged against — and
    the report's own magnet card second, because the cycle solver is told no
    magnet limit by default and the line is still worth drawing."""
    lim = (duty_cycle_record(col) or {}).get("limits") or {}
    w = _numf(lim.get("winding_limit_c"))
    m = _numf(lim.get("magnet_limit_c"))
    if w is None:
        w = _numf((ctx or {}).get("winding_limit_c"))
    if m is None:
        m = _numf((ctx or {}).get("magnet_limit_c"))
    return w, m


def _duty_cycle_page(st, cols: List[Dict[str, Any]],
                     ctxs: Optional[Dict[str, Any]] = None,
                     sec: Optional[Dict[str, int]] = None,
                     figs: Optional[List[int]] = None) -> List[Any]:
    from reportlab.platypus import KeepTogether, Spacer

    out: List[Any] = [_para(section_heading(sec, "duty_cycle"), st["h1"])]
    out.append(_para(DUTY_CYCLE_INTRO, st["body"]))
    # One torque per duty of this configuration — the profile figure draws the
    # torque of whichever duty runs in each segment, so it needs them all.
    _tq = duty_cycle_torques(cols)
    for c in cols:
        rec = duty_cycle_record(c)
        out.append(_para("Duty '%s'" % c.get("duty"), st["h2"]))
        if rec is None:
            out.append(_para(DUTY_CYCLE_NOT_RUN, st["note"]))
            continue
        ctx = (ctxs or {}).get(c.get("duty")) or {}
        # THE ANSWER FIRST (2026-09-15) — see `duty_cycle_regime_text`.
        _reg = duty_cycle_regime_text(rec)
        if _reg:
            out.append(_para("<b>%s</b>" % _reg, st["body"]))
        out.append(_para(duty_cycle_profile_text(rec), st["body"]))
        if not (rec.get("cycle") or {}).get("converged"):
            out.append(_para(FLAG + " " + DUTY_CYCLE_NOT_CONVERGED, st["warn"]))
        rows = duty_cycle_rows(c, ctx)
        out.append(_table([[r[0], r[1], _para(str(r[2]), st["cell"])]
                           for r in rows],
                          [140, 150, CONTENT_W - 290], header=True, size=7.6))
        out.append(Spacer(1, 4))
        _wl, _ml = duty_cycle_limits(c, ctx)
        for blob, caption in duty_cycle_figures(rec, _wl, _ml, torques=_tq):
            if not blob:
                continue
            img = _image(blob, CONTENT_W)
            if img is None:
                continue
            out.append(KeepTogether([img, _para(
                fig_label(figs, caption, "duty '%s'" % c.get("duty")),
                st["note"])]))
            out.append(Spacer(1, 4))
    return out


# ── page 5 · mechanical ─────────────────────────────────────────────────────


MECH_PAGE_UNSOLVED = (
    "Not solved yet — no rotor-stress answer is stored. Open the Mechanical "
    "tab, set the speed and the contacts and press Solve.")

MECH_MAP_MISSING = (
    "The stress map is not stored on this result (the per-element field was "
    "stripped).")

CRIT_PAGE_UNSOLVED = (
    "Not solved — no rotordynamics answer is stored for this machine.")

#: The notice on the cover (user 2026-09-10: "надо как бы написать, что это всё
#: конфиденциально и принадлежит Motres d.o.o., распространять только с
#: разрешения — кратко и понятно").  Short on purpose: a paragraph of legal
#: boilerplate on an engineering report is read by nobody.
CONFIDENTIAL_NOTICE = (
    "CONFIDENTIAL — property of Motres d.o.o. This document and the design it "
    "describes may not be copied, shared or passed on, in whole or in part, "
    "without written permission from Motres d.o.o."
)

CRIT_NO_CROSSING = (
    "No crossing of the 1x line was found inside the search range.")

#: What the page says INSTEAD of the Campbell figure when the rotordynamics
#: record kept the crossings but not the sweep it found them on (A4).
CAMPBELL_NOT_STORED = (
    "Campbell sweep not stored for this duty — the crossings of the 1× line "
    "are in the table above, but the whirl branches were not kept.")

CAMPBELL_CAPTION = (
    "Campbell diagram: whirl frequencies against speed with the 1× unbalance "
    "line; a crossing of that line is a critical speed.")

#: The two modal answers, named by the PART they belong to.  See the note at
#: `_mech_page`: "modes" alone did not say which half of the machine it meant.
MODES_HEADING = "Stator ring modes"
CRIT_HEADING = "Rotor critical speeds (rotordynamics)"

#: The gallery is 3 rows × 4 columns = 12 modes, the default solve count and
#: the number the user asked for (2026-09-11).
MODES_GALLERY_ROWS, MODES_GALLERY_COLS = 3, 4

MODES_PAGE_UNSOLVED = (
    "No modal solve stored for this machine — press Solve modes on the "
    "Mechanical tab and the ring modes appear here.")
MODES_GALLERY_MISSING = (
    "The mode shapes were not stored with this solve (solved before shapes "
    "were kept) — re-run Solve modes and the gallery appears here.")
MODES_CAPTION = (
    # TWO SENTENCES (CS-12, audit v7): the bonded-interface caveat is a clause
    # of the first, not a third sentence of its own.
    "The first %d in-plane modes of the %s cross-section, each at its natural "
    "frequency with the circumferential order n, every interface bonded — an "
    "upper bound on the frequency. Deformation exaggerated (%s of the radius).")


def modes_heading(res: Optional[Dict[str, Any]]) -> str:
    """Which body the modes belong to — the fixed 'stator' heading was wrong
    for a rotor solve (the live Ø200's modal result IS the rotor)."""
    body = str((res or {}).get("body") or "").lower()
    return {"rotor": "Rotor ring modes", "stator": MODES_HEADING}.get(
        body, "Ring modes")


def modes_excitation_note(rpm: Any, slots: Any, f_switch_hz: Any,
                          duty: Optional[str] = None) -> str:
    """The one line under the modal table that names the lines it was judged
    against, and whose carrier they are.

    Written because the stored answer's carrier belongs to whatever machine was
    loaded when the modal step ran, and the reader has no way to tell a 48 kHz
    column that came from the duty from one that came from another motor
    (reviewer 2026-09-14, A1).
    """
    lines = excitation_lines(rpm, slots, f_switch_hz)
    if not lines:
        return ("No excitation line could be formed for this duty — it carries "
                "no PWM carrier and no speed, so the modes above are judged "
                "against nothing.")
    return ("The 'nearest excitation' column is rebuilt from the duty%s own "
            "settings: PWM carrier %s and its 2×, slot passing %s (%s slots × "
            "%s) and its 2×."
            % ((" '%s's" % duty) if duty else "'s",
               _fmt(f_switch_hz, 0, "Hz"),
               _fmt(next((l["hz"] for l in lines
                          if l["name"] == "slot passing"), None), 0, "Hz"),
               _fmt(slots, 0), _fmt(rpm, 0, "rpm")))


def excitation_lines(rpm: Any, slots: Any,
                     f_switch_hz: Any) -> List[Dict[str, Any]]:
    """The excitation lines a rotor ring mode is judged against, at THIS duty.

    The inverter carrier and its second harmonic (fixed, they do not scale with
    speed) and the slot-passing force with its second harmonic (slots × the
    mechanical rotation).  Same names and same arithmetic as
    ``simulation.mechanical.modal.excitation_orders``, rebuilt here because the
    stored answer's carrier is the one the SERVER happened to hold when the
    modal step ran, not the duty's own: the L155's rated duty was solved while
    a Ø85 machine was loaded and every one of its twelve modes came out judged
    against that machine's 48 kHz carrier (reviewer 2026-09-14, A1).  A mode is
    a property of the rotor, the carrier is a property of the duty, and the
    report owns the second half of that sentence.
    """
    out: List[Dict[str, Any]] = []
    f_rot = (_numf(rpm) or 0.0) / 60.0
    n_sl = _numf(slots) or 0.0
    if f_rot > 0 and n_sl > 0:
        out.append({"name": "slot passing", "hz": n_sl * f_rot})
        out.append({"name": "slot passing 2×", "hz": 2.0 * n_sl * f_rot})
    fs = _numf(f_switch_hz) or 0.0
    if fs > 0:
        out.append({"name": "PWM carrier", "hz": fs})
        out.append({"name": "PWM carrier 2×", "hz": 2.0 * fs})
    return out


def nearest_excitation(f_hz: Any,
                       lines: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """``{'name','hz','margin_pct','flag'}`` of the line this mode sits nearest.

    The margin is relative to the EXCITATION, signed, exactly as the modal
    solver reports it (``nearest_excitation`` there): positive means the mode is
    above the line.  ``None`` when there is no line to judge against.
    """
    f = _numf(f_hz)
    if f is None:
        return None
    best: Optional[Dict[str, Any]] = None
    for e in lines:
        hz = _numf(e.get("hz"))
        if not hz or hz <= 0:
            continue
        margin = (f - hz) / hz
        if best is None or abs(margin) < abs(best["_m"]):
            best = {"name": e.get("name"), "hz": hz, "_m": margin}
    if best is None:
        return None
    return {"name": best["name"], "hz": best["hz"],
            "margin_pct": 100.0 * best["_m"],
            "flag": bool(abs(best["_m"]) < RING_MODE_AMBER_PCT / 100.0)}


def mode_frequencies(res: Optional[Dict[str, Any]]) -> List[float]:
    """Every stored ring-mode frequency, whichever shape the record holds."""
    out: List[float] = []
    r = res if isinstance(res, dict) else {}
    for m in (r.get("modes") or []):
        v = _numf(m.get("f_hz")) if isinstance(m, dict) else _numf(m)
        if v:
            out.append(float(v))
    if not out:
        for v in (r.get("frequencies_hz") or []):
            f = _numf(v)
            if f:
                out.append(float(f))
    return out


def tightest_ring_mode(res: Optional[Dict[str, Any]], *, rpm: Any = None,
                       slots: Any = None, f_switch_hz: Any = None
                       ) -> Optional[Dict[str, Any]]:
    """The stored mode that sits closest to one of THIS duty's excitation lines.

    ``{'f_hz', 'excitation', 'excitation_hz', 'margin_pct'}`` — the same shape
    the coupled run files under ``mechanical.modes.tightest``, recomputed from
    the duty's own carrier and speed so the two duties of one machine cannot
    disagree about a frequency that belongs to the rotor.
    """
    lines = excitation_lines(rpm, slots, f_switch_hz)
    if not lines:
        return None
    best: Optional[Dict[str, Any]] = None
    for f in mode_frequencies(res):
        near = nearest_excitation(f, lines)
        if near is None:
            continue
        if best is None or abs(near["margin_pct"]) < abs(best["margin_pct"]):
            best = {"f_hz": f, "excitation": near["name"],
                    "excitation_hz": near["hz"],
                    "margin_pct": near["margin_pct"]}
    return best


def mode_rows(res: Optional[Dict[str, Any]], *, rpm: Any = None,
              slots: Any = None, f_switch_hz: Any = None) -> List[List[str]]:
    """One row per mode: number, frequency, order, the nearest excitation line
    and its margin.  Header included.

    The excitation column is REBUILT from the duty's own carrier and speed when
    those are handed in (see :func:`excitation_lines`); the stored ``nearest``
    is the fallback for a caller that cannot say which duty it is printing.
    """
    rows = [["#", "f [Hz]", "n", "nearest excitation", "margin"]]
    lines = excitation_lines(rpm, slots, f_switch_hz)
    for m in ((res or {}).get("modes") or []):
        if not isinstance(m, dict):
            continue
        near = m.get("nearest") if isinstance(m.get("nearest"), dict) else {}
        if lines:
            near = nearest_excitation(m.get("f_hz"), lines) or {}
        rows.append([
            _fmt(m.get("index"), 0), _fmt(m.get("f_hz"), 0),
            _fmt(m.get("order"), 0) if m.get("order") is not None else "—",
            ("%s (%s Hz)" % (near.get("name"), _fmt(near.get("hz"), 0))
             if near else "—"),
            # SIGNED margins read as arithmetic errors ("−12 %" beside a line
            # the mode sits 12 % under — reviewer 2026-09-11).  Say the
            # direction — and keep two decimals under one per cent (CS-11):
            # rounded to a whole number a mode 0.12 % off the carrier printed
            # "0 % above" here and "0.12 %" in the warning, which is one
            # quantity with two values.
            (("%s %s" % (_fmt(abs(_numf(near.get("margin_pct")) or 0.0),
                              2 if abs(_numf(near.get("margin_pct")) or 0.0) < 1.0
                              else 0, "%"),
                         "below" if (_numf(near.get("margin_pct")) or 0.0) < 0
                         else "above"))
             + (" " + FLAG if near.get("flag") else "")) if near else "—",
        ])
    return rows


#: How wide the gallery is DRAWN, in centimetres, and how many pixels that is.
#:
#: The figure is built at the size it will be printed at (2026-09-11, user:
#: "увеличь разрешение во всю ширину страницы пропорционально, и шрифты внутри
#: тоже увеличь").  That is the whole trick: matplotlib sizes text in POINTS of
#: the figure, so a 28 cm figure shrunk into a 16.5 cm frame took its 8.5 pt
#: titles down to 5 pt on paper.  Draw it at the frame's own width and a point
#: in the figure is a point on the page — the titles below are then the same
#: size as the tables around them.  Resolution rides on the dpi, not on the
#: figure size, so the picture stays sharp.
MODES_GALLERY_CM = 24.0
MODES_GALLERY_PX = 2800


def _mode_gallery_png(res: Optional[Dict[str, Any]],
                      nrows: int = MODES_GALLERY_ROWS,
                      ncols: int = MODES_GALLERY_COLS,
                      px: int = MODES_GALLERY_PX,
                      width_cm: float = MODES_GALLERY_CM) -> Optional[bytes]:
    """The mode shapes as one picture: a grid of small deformed cross-sections.

    Reads either the live result (``field.modes`` as nested lists) or the
    per-duty store (``u_modes`` float16 array) — both arrive under
    ``res["field"]["modes"]``.  Each cell: the undeformed outline in grey, the
    deformed mesh coloured by |u| (jet, the app's palette), the frequency and
    the order as the title.  Empty cells stay white when fewer modes were
    solved than the grid holds.
    """
    if not isinstance(res, dict):
        return None
    fld = res.get("field") if isinstance(res.get("field"), dict) else None
    if not fld:
        return None
    p = _as_xy(fld.get("vertices"))
    t = _as_tris(fld.get("triangles"))
    if p is None or t is None or fld.get("modes") is None:
        return None
    try:
        import numpy as np
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.tri as mtri
        from matplotlib.collections import LineCollection

        U = np.asarray(fld.get("modes"), dtype=float)
        if U.ndim != 3 or U.shape[1] != p.shape[0] or U.shape[2] != 2:
            return None
        rows = [m for m in (res.get("modes") or []) if isinstance(m, dict)]
        n = min(U.shape[0], nrows * ncols)
        if n == 0:
            return None
        ext = float(fld.get("extent") or np.abs(p).max() or 1.0)
        # 6 % of the radius at the peak: visibly bent, still recognisable.
        exagg = 0.06 * ext
        # …and the cell is drawn tight around the deformed shape, so the disc
        # fills it: the old 12 % margin was a ring of white around every mode.
        lim_k = 1.02 + 0.06
        # the outline: every edge that belongs to exactly one triangle
        e = np.concatenate([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]])
        e.sort(axis=1)
        _, idx, cnt = np.unique(e, axis=0, return_index=True, return_counts=True)
        bnd = e[idx[cnt == 1]]

        # FIGURE AT PRINT SIZE, resolution from the dpi (see MODES_GALLERY_CM).
        fig_w_in = max(float(width_cm), 4.0) / 2.54
        cell_in = fig_w_in / ncols
        dpi = max(160.0, float(px) / fig_w_in)
        fig, axes = plt.subplots(nrows, ncols,
                                 figsize=(fig_w_in, cell_in * nrows * 1.08),
                                 dpi=dpi)
        axes = np.asarray(axes).reshape(-1)
        cmap = _jet_cmap()
        lim = ext * lim_k
        for k, ax in enumerate(axes):
            ax.set_aspect("equal")
            ax.set_xlim(-lim, lim)
            ax.set_ylim(-lim, lim)
            ax.axis("off")
            if k >= n:
                continue
            u = U[k]
            q = p + exagg * u
            mag = np.hypot(u[:, 0], u[:, 1])
            tri = mtri.Triangulation(q[:, 0], q[:, 1], t)
            ax.tripcolor(tri, mag, shading="gouraud", cmap=cmap, vmin=0.0,
                         vmax=max(float(mag.max()), 1e-9))
            ax.add_collection(LineCollection(
                [[(p[a, 0], p[a, 1]), (p[b, 0], p[b, 1])] for a, b in bnd],
                colors="#9a9a9a", linewidths=0.6, linestyles="dashed"))
            ax.add_collection(LineCollection(
                [[(q[a, 0], q[a, 1]), (q[b, 0], q[b, 1])] for a, b in bnd],
                colors="#202020", linewidths=0.5))
            m = rows[k] if k < len(rows) else {}
            f = m.get("f_hz")
            order = m.get("order")
            # 10.5 pt — the document's own table size, and a point here is a
            # point on the page now that the figure is drawn at print width.
            ax.set_title("#%d · %s Hz%s" % (
                int(m.get("index") or k + 1), _fmt(f, 0),
                ("  n = %d" % int(order)) if order is not None else ""),
                fontsize=10.5, pad=3)
        fig.tight_layout(pad=0.3)
        buf = io.BytesIO()
        fig.savefig(buf, format="png", facecolor="white",
                    bbox_inches="tight", pad_inches=0.05)
        plt.close(fig)
        return buf.getvalue()
    except Exception as exc:                                # noqa: BLE001
        log.debug("report: mode gallery failed (%s)", exc)
        try:
            import matplotlib.pyplot as plt
            plt.close("all")
        except Exception:                                   # noqa: BLE001
            pass
        return None


def modes_source(me: Dict[str, Any],
                 modes_src: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The modal result to print: the duty's stored one (it has the shapes),
    else the machine's last (which may carry shapes only while the server that
    solved it is still up)."""
    if isinstance(modes_src, dict) and modes_src.get("modes"):
        return modes_src
    e = (me or {}).get("modes")
    if isinstance(e, dict) and isinstance(e.get("result"), dict):
        return e["result"]
    return None


def has_campbell(crit: Optional[Dict[str, Any]]) -> bool:
    """Whether a Campbell diagram will actually be DRAWN for this record.

    The same test :func:`_campbell_png` makes, without building the figure —
    so the sentence that opens section 7 and the figure at the end of it
    cannot disagree (MJ-3, reviewer 2026-09-14: the opening line promised
    "the Campbell diagram below" on a record whose last line then said no
    diagram is drawn).
    """
    if not isinstance(crit, dict):
        return False
    cam = crit.get("campbell") or {}
    return bool((cam.get("forward") or cam.get("backward"))
                and (cam.get("rpm") or []))


def mech_map_owner_text(map_duty: Optional[str], from_duty: bool = False,
                        point: str = "", campbell: bool = True,
                        sec: Optional[Dict[str, int]] = None) -> str:
    """Whose stress field and mode shapes the pictures on this page are.

    ``from_duty`` — they were read back from that duty's own stored fields,
    which is the statement the document makes; the branches that named the
    server instead went on 2026-09-14 (client review).  ``campbell`` — whether
    the Campbell figure is drawn at all; the clause naming it is dropped when
    it is not (MJ-3).
    """
    at = (" at %s" % point) if point else ""
    _c = " and the Campbell diagram" if campbell else ""
    if map_duty and from_duty:
        return ("The stress map, the mode shapes%s below are the stored fields "
                "of the duty '%s'%s; per-duty stress numbers are in %s."
                % (_c, map_duty, at, compare_ref(sec)))
    if map_duty:
        return ("The stress map%s below belong%s to the duty '%s'%s; "
                "per-duty stress numbers are in %s."
                % (_c, "" if campbell else "s", map_duty, at,
                   compare_ref(sec)))
    return ("The stress map%s below %s the last stored one%s and %s not "
            "attributed to a duty column."
            % (_c, "are" if campbell else "is", "s" if campbell else "",
               "are" if campbell else "is"))


def mech_pair_tail_text(campbell: bool,
                        sec: Optional[Dict[str, int]] = None) -> str:
    """What section 7's opening line says about the pictures that are NOT a
    pair, when the pictures that are one have taken over the sentence.

    The Campbell diagram is named only when the record has the sweep to draw
    one (MJ-5, audit v5: *"The mode shapes and the Campbell diagram are the one
    rotor's, drawn once"* stood three paragraphs above *"Campbell sweep not
    stored for this duty"*).  :func:`has_campbell` is the one test both this
    sentence and the figure at the end of the section make.
    """
    return (("The mode shapes and the Campbell diagram are the one rotor's, "
             "drawn once. " if campbell else
             "The mode shapes are the one rotor's, drawn once. ")
            + "Per-duty stress numbers are in %s." % compare_ref(sec))


def mech_source_text(entry: Dict[str, Any], res: Dict[str, Any],
                     case_name: Optional[str], case: Dict[str, Any],
                     duty: Optional[str] = None,
                     from_duty: bool = False) -> str:
    # NO SOLVE TIMESTAMP (CS-7 / D4, reviewer 2026-09-14).  A result is
    # identified in this document by its MACHINE and its OPERATING POINT, both
    # of which this sentence states; a wall-clock stamp beside them is read as
    # a version and invites "these two sections disagree" when what differs is
    # which store answered first.  `_Source` dropped its stamp for exactly this
    # reason; the thermal and mechanical provenance lines had kept theirs.
    head = ("the rotor-stress solve stored for the duty '%s'" % duty
            if (from_duty and duty) else
            "the Mechanical tab's last rotor-stress solve")
    return ("Source: %s. Case "
            "'%s' at %s (overspeed factor %s). Plane stress, %s elements, "
            "order %s." % (
                head,
                case_name or "—", _fmt(case.get("rpm", res.get("rpm")), 0, "rpm"),
                _fmt(res.get("overspeed_factor"), 2),
                _fmt(_g(res, "mesh.n_triangles"), 0),
                _fmt(_g(res, "mesh.element_order"), 0)))


def mech_temps_text(res: Dict[str, Any]) -> Optional[str]:
    """Which part temperatures the solve ran at — the ONLY way temperature
    enters mechanics (rule of 2026-09-09: it is a band's fit change)."""
    th_blk = res.get("thermal") or {}
    pt = th_blk.get("part_temps_c") or {}
    if not pt:
        return None
    return ("Temperatures used: " + ", ".join(
        f"{k} {float(v):g} °C" for k, v in sorted(pt.items()) if v is not None)
        + (" (reference %s °C)" % _fmt(th_blk.get("ref_temp_c"), 0)))


def mech_part_rows(case: Dict[str, Any]) -> List[List[str]]:
    """Per-part stress and safety factor.  Header row included.

    AVERAGED since 2026-09-10 — element values area-averaged onto the nodes of
    their own part, which is what ANSYS and Fusion plot and report, so the
    number here is the number on the map beside it.  The unaveraged element
    peak is carried in its own column: it is the other half of the same toggle
    those tools offer, and the gap between the two is the corner singularity.
    """
    prows = [["Part", "Material", "VM p99.5", "VM peak",
              "VM peak, unaveraged", "Criterion", "Strength", "SF"]]
    for name, p in sorted((case.get("parts") or {}).items()):
        if not isinstance(p, dict):
            continue
        prows.append([
            name, str(p.get("material") or "—"),
            _fmt(p.get("von_mises_p995_mpa"), 1, "MPa"),
            _fmt(p.get("von_mises_max_mpa"), 1, "MPa"),
            _fmt(p.get("von_mises_max_unaveraged_mpa"), 1, "MPa"),
            _fmt(p.get("governing_stress_mpa"), 1, "MPa"),
            _fmt(p.get("strength_mpa"), 0, "MPa"),
            _fmt(p.get("safety_factor"), 2)])
    return prows


def mech_exagg_text(exagg: Any) -> str:
    """The sentence that says the deformation is drawn exaggerated, and by how
    much.  Empty when the map was drawn at true scale (nothing moved)."""
    try:
        k = float(exagg)
    except (TypeError, ValueError):
        return ""
    return (" Deformation exaggerated x%s; the colours are the true microns."
            % _fmt(k, 0)) if k > 0 else ""


def mech_extra_captions() -> List[Tuple[str, str, str]]:
    """``(key, caption, what-to-say-when-missing)`` for the two extra maps."""
    return [
        ("disp", "Displacement magnitude |u| over the same case, µm, drawn on "
                 "the deformed shape.",
         "the displacement field was not stored with this result"),
        ("sf", "Safety factor over the same case, each element against its own "
               "material's criterion; the bar stops at 4.",
         "the safety-factor field was not stored with this result"),
    ]


def mech_percentile_text(case: Dict[str, Any]) -> str:
    # …and where the lowest factor is the ROTOR's, the standing policy sentence
    # rides with it (user 2026-09-15), with the sleeve's own factor beside it:
    # the bridges are assembly features, the sleeve is the retention.
    tail = ""
    if is_rotor_bridge_part(case.get("sf_min_part")):
        _sl = _numf((((case.get("parts") or {}).get("sleeve")) or {})
                    .get("safety_factor"))
        tail = (" %s%s."
                % (ROTOR_BRIDGE_POLICY[0].upper() + ROTOR_BRIDGE_POLICY[1:],
                   ("" if _sl is None
                    else " — sleeve SF %s carries the retention" % _fmt(_sl, 2))))
    return ("Stresses are AVERAGED onto the nodes of each part. SF = strength "
            "over the averaged peak of the 'Criterion' column; the unaveraged "
            "column and p99.5 are singularity gauges, not sizing numbers. "
            "Lowest safety factor %s on %s (raw element minimum %s).%s" % (
                _fmt(case.get("sf_min"), 2), case.get("sf_min_part") or "—",
                _fmt(case.get("sf_min_unaveraged")
                     if case.get("sf_min_unaveraged") is not None
                     else case.get("sf_min_p05"), 2), tail))


def rotor_inertia_rows(em: Dict[str, Any]) -> List[List[str]]:
    """The rotor's moment of inertia about the shaft, and what makes it up.

    User 2026-09-11: *"нигде не нашёл величины инерции ротора — её нужно
    обязательно добавить в механику"*.  It is computed on every run (from the
    CAD polygons, not a cylinder approximation) and lives in the summary; no
    section of this document carried it.  It belongs to MECHANICS: it is what
    sets the acceleration a drive can ask for and the energy stored in the
    spinning mass, and it is the term a critical-speed model needs beside the
    bearing stiffness.  Header included; empty when the run stored none.
    """
    J = _g(em, "rotor_inertia")
    if not isinstance(J, dict):
        return []
    tot = _numf(J.get("J_kg_cm2"))
    if tot is None:
        _si = _numf(J.get("J_kg_m2"))
        tot = _si * 1e4 if _si is not None else None
    if tot is None:
        return []
    rows = [["Rotating part", "J [kg·cm²]", "Share"]]
    parts = [(k, _numf(v)) for k, v in J.items()
             if k in ("rotor_iron", "magnet", "shaft", "sleeve")]
    for name, v in parts:
        if v is None:
            continue
        v_cm2 = v * 1e4                      # the parts are stored in kg·m²
        rows.append([name.replace("_", " ").capitalize(), _fmt(v_cm2, 2),
                     _fmt(100.0 * v_cm2 / tot, 0, "%") if tot else ""])
    rows.append(["TOTAL about the shaft axis", _fmt(tot, 2),
                 str(J.get("source") or "")])
    return rows


ROTOR_INERTIA_NOTE = (
    "From the CAD polygons of every rotating part; a part marked reference or "
    "excluded is out of this total, as it is out of the mass.")


def mech_fit_rows(case: Dict[str, Any], res: Dict[str, Any]) -> List[List[str]]:
    """How far the rotor grew and what interference is left.  Header included."""
    # `od_growth` is the outermost ROTATING surface — the top of the band when
    # the rotor has one — and its maximum is what closes the mechanical
    # clearance (air gap minus the band).  User 2026-09-10 asked for that
    # maximum as its own number; the mean beside it separates the whole ring
    # growing from the lobing the magnets push in between the poles.
    od = case.get("od_growth") if isinstance(case.get("od_growth"), dict) else {}
    what = str(od.get("part") or "rotor").capitalize()
    rows = [
        ["Quantity", "Value"],
        ["Rotor OD growth", _fmt(case.get("rotor_od_growth_um"), 1, "µm")],
    ]
    if od:
        rows += [
            [f"{what} OD radial travel, maximum",
             _fmt(od.get("max_um"), 1, "µm")],
            [f"{what} OD radial travel, mean", _fmt(od.get("mean_um"), 1, "µm")],
            [f"{what} OD radial travel, least", _fmt(od.get("min_um"), 1, "µm")],
        ]
    # THE AIR GAP, as a budget (user 2026-09-10: "не забудь добавить в отчёт,
    # как меняется зазор").  The clearance is measured off the drawn section —
    # the smallest radius anywhere on the stator against the rotor's largest —
    # so a chamfer or a stepped pole counts and no geometry field has to be
    # kept in step with it.  What is NOT in it: manufacturing tolerance,
    # bearing clearance and shaft whirl, which is why the last row says so.
    ag = case.get("air_gap") if isinstance(case.get("air_gap"), dict) else {}
    if ag:
        rows += [
            ["Air-gap clearance, cold (bore − rotor)",
             _fmt(ag.get("clearance_um"), 1, "µm")],
            ["…closed by the rotor at this speed",
             _fmt(ag.get("closed_um"), 1, "µm")],
            ["…left running",
             "%s  (%s of the clearance used)"
             % (_fmt(ag.get("remaining_um"), 1, "µm"),
                _fmt(ag.get("closed_pct"), 0, "%"))],
        ]
    return rows + [
        ["Maximum displacement", _fmt(case.get("max_displacement_um"), 1, "µm")],
        ["Sleeve interference, geometric",
         _fmt(res.get("interference_mm"), 4, "mm")],
        ["Sleeve interference, effective at temperature",
         _fmt(res.get("interference_effective_mm"), 4, "mm")],
    ]


def _interface_words(label: Any) -> str:
    """``magnet_rotor`` -> ``magnet/rotor``.

    User 2026-09-11: *"интерфейсы лучше писать так: magnet/rotor"* — a contact
    pair is two parts touching, and a slash says that where an underscore reads
    like one identifier.  The stored key is untouched: this is spelling for the
    page only.
    """
    t = str(label or "").strip()
    return t.replace("_", "/") if t else "—"


def mech_contact_rows(case: Dict[str, Any],
                      res: Dict[str, Any]) -> List[List[str]]:
    """Every interface and its verdict.  Header included; one row means the
    solve carried no contact at all.

    A separation pair can OPEN; a bonded or sliding tie cannot, and its verdict
    is a different question — is the tie carrying tension (a joint a real press
    fit would have let go) or compression.  One word for both would report a
    healthy bonded interface as lift-off.
    """
    crows = [["Interface", "type", "open", "verdict"]]
    _ret_lbl, _ = retention_interface(case)
    for label, i in sorted((case.get("interfaces") or {}).items()):
        if not isinstance(i, dict):
            continue
        bad = bool(i.get("lift_off"))
        unilateral = str(i.get("type") or "").startswith("separ")
        if unilateral and label != _ret_lbl:
            # A pair that retains nothing cannot lift off ANYTHING: it is
            # reported as what it is, an open fraction, and the reader is told
            # which pair the verdict actually rests on (2026-09-10).
            verdict = "open — not the retention joint"
        elif unilateral:
            # The retention joint's verdict carries the number that PROVES it:
            # the pressure at its weakest facet.  "Held" with 13 MPa still
            # squeezing the magnet at the worst point on the arc is an answer;
            # "held" on its own is an assertion.
            _pmin = _numf(i.get("pressure_min_mpa"))
            verdict = ("LIFT-OFF " + FLAG) if bad else (
                "held — RETENTION JOINT" + (
                    ", %s still pressing at its weakest facet"
                    % _fmt(_pmin, 1, "MPa") if _pmin else ""))
        else:
            verdict = ("tie in TENSION " + FLAG) if bad else "in compression"
        crows.append([
            _interface_words(label), str(i.get("type") or ""),
            _fmt(100.0 * float(i.get("open_fraction") or 0.0), 1, "%")
            if unilateral else "n/a", verdict])
    for label, v in sorted((res.get("lift_off_rpm") or {}).items()):
        if not v:
            continue
        _i = (case.get("interfaces") or {}).get(label) or {}
        if not str(_i.get("type") or "").startswith("separ"):
            # A BONDED tie does not open — it goes into tension, which is the
            # speed at which a real press fit (which cannot pull) would have let
            # go.  Calling that "lift-off" was the same word for two different
            # things (2026-09-10).
            crows.append([f"{_interface_words(label)} — tie goes into tension "
                          f"at", "", "", _fmt(v, 0, "rpm")])
        else:
            crows.append([f"{_interface_words(label)} — opens at", "", "",
                          _fmt(v, 0, "rpm")
                          + ("" if label == _ret_lbl else "  (not the "
                             "retention joint)")])
    ret = case.get("magnet_retention") or {}
    if ret.get("verdict"):
        crows.append(["Magnet retention", "", "", str(ret["verdict"])[:60]])
    return crows


def crit_detail_rows(crit: Dict[str, Any]) -> List[List[str]]:
    """Every crossing of the 1× line.  Header included."""
    rows = [["Mode", "Whirl", "Critical speed", "Margin vs rated",
             "Excited by unbalance"]]
    for c in (crit.get("critical_speeds") or []):
        if not isinstance(c, dict):
            continue
        try:
            v = float(c.get("rpm"))
        except (TypeError, ValueError):
            continue
        rows.append([
            _fmt(c.get("mode"), 0), str(c.get("whirl") or ""),
            _fmt(v, 0, "rpm"), _fmt(c.get("margin_vs_rated_pct"), 1, "%"),
            "yes" if c.get("excited_by_unbalance") else "no"])
    return rows


def crit_verdict_text(crit: Dict[str, Any]) -> Optional[str]:
    if not crit.get("verdict"):
        return None
    fwd = [c.get("rpm") for c in (crit.get("critical_speeds") or [])
           if c.get("rpm") is not None and str(c.get("whirl") or "") == "forward"]
    bwd = [c.get("rpm") for c in (crit.get("critical_speeds") or [])
           if c.get("rpm") is not None and str(c.get("whirl") or "") == "backward"]
    rated = _numf(crit.get("rated_rpm"))
    extra = ""
    if fwd and rated:
        f1 = min(fwd)
        # RELATIVE TO RATED, the same margin the table beside this text prints
        # (reviewer 2026-09-13: "17.5 % above rated" was the gap as a share of
        # the critical, the table's 21.2 % the share of rated — one definition).
        m = 100.0 * (f1 - rated) / rated
        extra = (" The first FORWARD critical is %s, %s %s rated" % (
            _fmt(f1, 0, "rpm"), _fmt(abs(m), 1, "%"),
            "above" if m >= 0 else "BELOW"))
        if 0 <= m < NEAR_PCT:
            extra += (" — a thin margin: a stiffer or softer bearing than the "
                      "assumed one moves it through the running speed")
        extra += "."
    if bwd and rated and min(bwd) < rated:
        extra += (" A backward crossing sits at %s, below rated: unbalance does "
                  "not excite it on isotropic bearings, but an anisotropic mount "
                  "or a bent shaft will." % _fmt(min(bwd), 0, "rpm"))
    return ("%s. Rated %s.%s The shaft line is an ASSUMPTION — no bearing "
            "span, overhang or stiffness is carried by the motor config."
            % (str(crit["verdict"]).capitalize(),
                            _fmt(crit.get("rated_rpm"), 0, "rpm"), extra))


def _mech_page(st, me, map_duty: Optional[str] = None,
               field_src: Optional[Dict[str, Any]] = None,
               modes_src: Optional[Dict[str, Any]] = None,
               em: Optional[Dict[str, Any]] = None,
               map_from_duty: bool = False,
               map_rpm: Any = None, map_cur: Any = None,
               modes_rpm: Any = None, slots: Any = None,
               f_switch_hz: Any = None,
               pair: Optional[Dict[str, Any]] = None,
               figs: Optional[List[int]] = None,
               sec: Optional[Dict[str, int]] = None,
               detail_from_duty: bool = False) -> List[Any]:
    _L, _R = (pair or {}).get("left"), (pair or {}).get("right")
    _note = (pair or {}).get("note") or ""
    out: List[Any] = [_para(section_heading(sec, "mech"), st["h1"])]
    out.append(_para(
        pair_owner_text("The stress, displacement and safety-factor maps",
                        pair, pair_owner_tail(
                            "rotor-stress", map_duty, map_from_duty,
                            mech_pair_tail_text(has_campbell(
                                (me.get("critical_speeds")
                                 or {}).get("result") or {}), sec)),
                        first_fig=fig_ahead(figs))
        or mech_map_owner_text(
            map_duty, map_from_duty, point_words(map_rpm, map_cur),
            campbell=has_campbell((me.get("critical_speeds")
                                   or {}).get("result") or {}),
            sec=sec), st["note"]))
    entry = me.get("rotor_stress")
    if not entry:
        out.append(_para(MECH_PAGE_UNSOLVED, st["warn"]))
    else:
        from reportlab.platypus import KeepTogether as _KT
        res = entry.get("result") or {}
        case_name = res.get("primary_case") or next(iter(res.get("cases") or {}), None)
        case = (res.get("cases") or {}).get(case_name) or {}
        out.append(_para(mech_source_text(entry, res, case_name, case,
                                         map_duty, detail_from_duty),
                         st["note"]))
        temps = mech_temps_text(res)
        if temps:
            out.append(_para(temps, st["note"]))

        out.append(_para("Stress and safety factors", st["h2"]))
        prows = mech_part_rows(case)
        if len(prows) > 1:
            # EIGHT COLUMNS, EIGHT WIDTHS (reviewer 2026-09-14, A3): the table
            # carries Part, Material, p99.5, peak, unaveraged, Criterion,
            # Strength and SF, and only six widths were given.  The one long
            # heading goes in as a Paragraph so it WRAPS instead of running
            # into the column beside it.
            prows = [list(r) for r in prows]
            prows[0][4] = _para(prows[0][4], st["hcell"])
            out.append(_table(
                prows, [58, 100, 60, 58, 76, 62, 58, CONTENT_W - 472],
                header=True, size=7.8))
        # THE SAFETY-FACTOR BARS BELONG TO THE TABLE ABOVE THEM, and they are
        # drawn here in Word: the PDF used to put them after the three §7 maps,
        # so "Fig. 12" was the bar chart in one deliverable and the von Mises
        # map in the other (MJ-7, audit v5).  One order, both renderers.
        if _R:
            _fl = _sf_bars_png((_L or {}).get("case") or case, width_cm=PAIR_CM)
            _fr = _sf_bars_png(_R.get("case") or {}, width_cm=PAIR_CM)
            _blk = _fig_pair(st, _fl, _fr, pair_caption(
                    SF_CHART_CAPTION, _L, _R, have=(bool(_fl), bool(_fr)),
                    numbers=pair_number_clause(
                        "lowest", _worst_sf((_L or {}).get("case") or case),
                        _worst_sf(_R.get("case")), 2),
                    note=_note), max_height=PAIR_MAX_H, figs=figs)
            if _blk is not None:
                out.append(_blk)
        else:
            _sf = _image(_sf_bars_png(case, width_cm=MAP_FULL_CM), CONTENT_W,
                         max_height=PAIR_MAX_H)
            if _sf is not None:
                _sf.hAlign = "LEFT"
                out.append(_KT([_sf, _para(
                    "Fig. [%s] — %s" % (_map_tag(
                        map_duty, case.get('rpm', res.get('rpm')) or map_rpm,
                        map_cur), SF_CHART_CAPTION), st["note"])]))
        out.append(_para(mech_percentile_text(case), st["note"]))

        out.append(_para("Fit and contacts", st["h2"]))
        # A value cell that carries a clause ("583.3 µm (16 % of the clearance
        # used)") does not fit 62 pt as a plain string — wrap it.
        fit_t = _table([[r[0], (_para(r[1], st["cell"])
                                if isinstance(r[1], str) and len(r[1]) > 16
                                else r[1])]
                        for r in mech_fit_rows(case, res)],
                       [190, 92], header=True, size=8.6)

        # Full-width stress map under the fit table, caption attached (user
        # 2026-09-08: full-width pictures with captions, no half-empty pages —
        # so only the image and its caption are grouped, not the tables).
        # The PICTURE is the rated duty's own field when one is stored; the
        # numbers on this page stay the machine's last solve (2026-09-10).
        # The two sides' own stress fields, on ONE scale (2026-09-14).
        _sl = ((_L or {}).get("src") or {}).get("rotor_stress") \
            or (_L or {}).get("mech") or field_src or res
        _sr = ((_R or {}).get("src") or {}).get("rotor_stress") \
            or (_R or {}).get("mech")
        (png, case_used), (png_r, case_r) = mech_map_pair(
            _sl, _sr if _R else None,
            width_cm=PAIR_CM if _R else MAP_FULL_CM)
        if _R:
            out.append(fit_t)
            _blk = _fig_pair(st, png, png_r, pair_caption(
                    "Von Mises stress over %s, MPa, averaged onto each part's "
                    "own nodes." % mech_case_words(case_used, case_r),
                    _L, _R, have=(bool(png), bool(png_r)),
                    numbers=pair_number_clause(
                        "highest averaged", _peak_vm((_L or {}).get("case")
                                                     or case),
                        _peak_vm(_R.get("case")), 1, "MPa"),
                    note=_note, map_kind="rotor_stress"),
                max_height=PAIR_MAX_H, figs=figs)
            if _blk is not None:
                out.append(_blk)
            else:
                out.append(_para(MECH_MAP_MISSING, st["note"]))
        else:
            img = _image(png, CONTENT_W, max_height=PAIR_MAX_H)
            if img is not None:
                from reportlab.platypus import KeepTogether
                out.append(fit_t)
                out.append(KeepTogether([img, _para(
                    "Fig. [%s] — %s"
                    % (_map_tag(map_duty,
                                case.get('rpm', res.get('rpm')) or map_rpm,
                                map_cur),
                       caption_with_provenance(
                           "Von Mises stress over the '%s' case, MPa, averaged "
                           "onto each part's own nodes." % case_used,
                           _L, "rotor_stress")),
                    st["note"])]))
            else:
                out.append(fit_t)
                out.append(_para(MECH_MAP_MISSING, st["note"]))

        # …and the two maps the tab shows beside the stress (2026-09-10).
        _extra, _extra_r = mech_extra_pair(
            _sl, _sr if _R else None,
            width_cm=PAIR_CM if _R else MAP_FULL_CM)
        for _k, _cap, _missing in mech_extra_captions():
            _a, _b = _extra.get(_k), (_extra_r or {}).get(_k)
            if _R and (_a or _b):
                _blk = _fig_pair(st, _a, _b, pair_caption(
                        _cap, _L, _R, have=(bool(_a), bool(_b)), note=_note,
                        map_kind="rotor_stress"),
                    max_height=PAIR_MAX_H, figs=figs)
                if _blk is not None:
                    out.append(_blk)
                continue
            _im = _image(_a, CONTENT_W, max_height=PAIR_MAX_H)
            if _im is not None:
                from reportlab.platypus import KeepTogether as _KT
                out.append(_KT([_im, _para(
                    "Fig. [%s] — %s"
                    % (_map_tag(map_duty,
                                case.get('rpm', res.get('rpm')) or map_rpm,
                                map_cur),
                       caption_with_provenance(_cap, _L, "rotor_stress")),
                    st["note"])]))
            else:
                out.append(_para("%s." % _missing, st["note"]))

        crows = mech_contact_rows(case, res)
        if len(crows) > 1:
            out.append(_table(crows, [150, 80, 60, CONTENT_W - 290],
                              header=True, size=8.6))

        _jrows = rotor_inertia_rows(em or {})
        if len(_jrows) > 1:
            out.append(_para("Rotor inertia", st["h2"]))
            out.append(_table(_jrows, [150, 90, CONTENT_W - 240],
                              header=True, size=8.6))
            out.append(_para(ROTOR_INERTIA_NOTE, st["note"]))
        # FULL CONTENT WIDTH, like every other figure (user 2026-09-14).
        _mj = _image(_mass_inertia_png(em or {}, width_cm=MAP_FULL_CM,
                                       which="inertia"),
                     CONTENT_W, max_height=PAIR_MAX_H)
        if _mj is not None:
            out.append(_KT([_mj, _para(
                fig_label(figs, mass_j_caption(em or {}), _map_tag(
                    map_duty, case.get('rpm', res.get('rpm')) or map_rpm,
                    map_cur)), st["note"])]))

    # ── ring modes: the table and the 3 × 4 gallery (2026-09-11) ───────────
    from reportlab.platypus import KeepTogether as _KT
    mres = modes_source(me, modes_src)
    out.append(_para(modes_heading(mres), st["h2"]))
    if not mres:
        out.append(_para(MODES_PAGE_UNSOLVED, st["body"]))
    else:
        mrows = mode_rows(mres, rpm=(modes_rpm or mres.get("rpm") or map_rpm),
                          slots=slots, f_switch_hz=f_switch_hz)
        if len(mrows) > 1:
            out.append(_table(mrows, [30, 60, 30, 170, CONTENT_W - 290],
                              header=True, size=8.6))
            out.append(_para(modes_excitation_note(
                (modes_rpm or mres.get("rpm") or map_rpm), slots, f_switch_hz,
                map_duty), st["note"]))
        gal = _image(_mode_gallery_png(mres, width_cm=MAP_FULL_CM),
                     CONTENT_W, max_height=PAGE_H * 0.74)
        if gal is not None:
            _n = min(len(mrows) - 1, MODES_GALLERY_ROWS * MODES_GALLERY_COLS)
            out.append(_KT([gal, _para(
                fig_label(figs,
                          caption_with_provenance(
                              MODES_CAPTION % (_n, mres.get("body") or "iron",
                                               "6 %"), _L, "modes"),
                          _map_tag(map_duty, mres.get("rpm") or map_rpm)),
                st["note"])]))
        else:
            out.append(_para(MODES_GALLERY_MISSING, st["note"]))

    # ── rotordynamics ───────────────────────────────────────────────────────
    # The heading, the table, the verdict and the Campbell diagram travel as
    # ONE block: on the live Ø200 the diagram alone was pushed onto an
    # otherwise empty page 7 (2026-09-08).
    blk: list = [_para("Critical speeds", st["h2"])]
    crit_entry = me.get("critical_speeds")
    if not crit_entry:
        blk.append(_para(CRIT_PAGE_UNSOLVED, st["body"]))
        out.append(_KT(blk))
    else:
        crit = crit_entry.get("result") or {}
        rows = crit_detail_rows(crit)
        if len(rows) > 1:
            blk.append(_table(rows, [42, 70, 90, 90, CONTENT_W - 292],
                               header=True, size=8.6))
        else:
            blk.append(_para(CRIT_NO_CROSSING, st["body"]))
        verdict = crit_verdict_text(crit)
        if verdict:
            blk.append(_para(verdict, st["note"]))
        out.append(_KT(blk))
        img = _image(_campbell_png(crit), CONTENT_W, max_height=PAIR_MAX_H)
        if img is not None:
            img.hAlign = "LEFT"
            out.append(_KT([img, _para(
                fig_label(figs, caption_with_provenance(
                              CAMPBELL_CAPTION, _L, "critical_speeds"),
                          _map_tag(map_duty,
                                   crit.get("rated_rpm") or map_rpm)),
                st["note"])]))
        else:
            out.append(_para(CAMPBELL_NOT_STORED, st["note"]))
    return out


# ── page 6 · assumptions ────────────────────────────────────────────────────


NOTHING_SOLVED_YET = "Nothing has been solved on this server yet."

READ_ONLY_CLOSING = (
    "Every figure in this report was read from a stored result. No solve was "
    "started to produce it, and no stored state was changed by it.")


def assumption_bullets(sec: Optional[Dict[str, int]] = None) -> List[str]:
    """What each solver actually did, in the five sentences that qualify every
    number above.  The ``<b>`` runs are the PDF's markup; the .docx renderer
    strips them into a real bold run — see ``report_docx._rich``.

    ``sec`` numbers the one cross-reference in the list (BT-5): the Thermal
    bullet said "the electromagnetic run named on page 3", and page 3 is
    "1 · Machine" in the Word render and "2 · Duties" in the PDF."""
    return [
        "<b>Electromagnetic</b> — 2-D transient finite elements on the sliding "
        "band, torque by the energy / flux-linkage method. The 3-D end effect "
        "is a separate Stage-A static passport applied as one scalar k to "
        "torque and voltage.",
        "<b>Losses</b> — iron from the steel's measured P(B,f) surface "
        "(Bertotti), copper as I²R at the stated winding temperature plus the "
        "solved proximity term, magnets / shaft / sleeve as solved eddy "
        "currents. A class the solver did not model is reported as unmodelled, "
        "never as no loss.",
        "<b>Thermal</b> — steady state, on the cycle-averaged loss map of the "
        "electromagnetic run named in %s; the air-gap conductivity is "
        "derived from clearance, speed and gap temperature, not typed in."
        % sec_ref(sec, "compare"),
        "<b>Mechanical</b> — plane-stress 2-D with contact; a separation "
        "contact may open, a bonded one may not. Peak stresses at re-entrant "
        "corners are mesh-dependent singularities, hence the p99.5 column.",
        # ONE BALANCE, AND WHY IT IS NOT THE STORED NUMBER (reviewer
        # 2026-09-14, D14): the catalog row and Compare quote the solver's own
        # `efficiency`, this document recomputes it, and the two differ by a
        # few hundredths of a point on the same run.
        "<b>Efficiency</b> — one balance everywhere: rotor power × k_3d "
        "against the 2-D losses, bearings and windage where they sit. The "
        "solver's own solve-time figure, which the catalog quotes, is a "
        "different balance a few hundredths of a point apart.",
        "<b>Bearings and windage</b> — the SKF frictional-moment model plus an "
        "analytic windage term, not FEM. The radial load is the rotor's own "
        "weight only: magnetic pull, coupling, belt and gear side loads are "
        "NOT in it.",
    ]


def not_included_bullets(brg: Optional[Dict[str, Any]],
                         th: Dict[str, Any],
                         sec: Optional[Dict[str, int]] = None) -> List[str]:
    """What this report does NOT model — read off the map that was solved."""
    # WHAT THE THERMAL MAP DID WITH THE MECHANICAL WATTS.  Until 2026-09-08 this
    # page stated flatly that bearing and windage heat is not a source in the
    # thermal solve.  It is now — friction at the shaft seats when the shaft-ends
    # path is open, gap shear in the air-gap air — so the bullet is read off the
    # map that was actually solved rather than asserted.  Absent map, absent
    # block (a solve from before this) and the old sentence still stands, because
    # for that map it is still true.
    _tm = ((th.get("field") or {}).get("result") or {}) if isinstance(th, dict) else {}
    _tmech = ((_tm.get("cooling") or {}).get("mech_losses") or {})
    if _tmech.get("P_mech_into_map_W"):
        _mech_bullet = (
            "the thermal map in %s CARRIES %.1f W of mechanical heat: "
            "bearing friction at the shaft seats and gap windage in the air-gap "
            "air.  %.1f W of it could not be placed in a cross-section (see the "
            "heat budget) and is excluded there"
            % (sec_ref(sec, "thermal"),
               float(_tmech["P_mech_into_map_W"]),
               float(_tmech.get("P_mech_total_W") or 0.0)
               - float(_tmech["P_mech_into_map_W"])))
    else:
        _mech_bullet = (
            "bearing and windage heat is not a source in the thermal solve of "
            "%s — the bearings sit on the shaft stubs outside this "
            "cross-section, and its shaft-ends heat path was closed"
            % sec_ref(sec, "thermal"))
    not_incl = [
        _mech_bullet,
        "transient duty cycles: every temperature here is a steady state",
        "end-winding copper is a lumped length factor, not a meshed 3-D volume",
        "manufacturing effects — EDM recast, interlaminar shorts, magnet "
        "tolerance — are not modelled",
    ]
    if not (brg and brg.get("has_bearings")):
        not_incl.insert(0, "bearing and windage losses: this configuration "
                           "names no bearings, so the mechanical loss is unknown")
    return not_incl


def solved_at_rows(sources: List[Any]) -> List[List[str]]:
    """Every solver answer this report leans on, and whether it is this machine.

    No "computed at" column since 2026-09-10 (user: "метки времени можно вообще
    убрать").  Two stamps a few minutes apart were read as two sections
    disagreeing about the machine, when what they recorded was which store
    answered first; a result is identified by its machine and its operating
    point, and this report states both.  Header row included.
    """
    rows = [["Solver result", "machine"]]
    for s in sources:
        rows.append([s.name,
                     "another machine " + FLAG if s.stale is True
                     else ("this machine" if s.stale is False else "unverified")])
    return rows


def _notes_page(st, sources, brg, em, th, me, cp,
                sec: Optional[Dict[str, int]] = None) -> List[Any]:
    from reportlab.platypus import Spacer

    out: List[Any] = [_para(section_heading(sec, "notes"), st["h1"])]
    for b in assumption_bullets(sec):
        out.append(_para("• " + b, st["body"]))
        out.append(Spacer(1, 2))
    # NOTHING AFTER THE ASSUMPTIONS (user 2026-09-11: "я думаю это не надо").
    # Three blocks went together, and they had one thing in common — they were
    # about the REPORT rather than about the machine:
    #   • "Not included", a list of what the models leave out.  The caveats that
    #     bear on a number are already beside that number (the thermal budget
    #     says what it could not place, the mechanics says temperature enters
    #     only as the band's fit), and repeated here they read as boilerplate.
    #   • the read-only closing — a promise about how the document was built.
    #   • the "ANOTHER machine" paragraph, which explained a column of a table
    #     that was removed on 2026-09-10 and had been standing alone since.
    # `not_included_bullets` is left in place: it is still the honest list, and
    # a caller that wants it has it.
    return out


# ---------------------------------------------------------------------------
# The comparison pages — one column per duty, one table per simulation
# ---------------------------------------------------------------------------
# User, 2026-09-09: *"а report всё нужно делать с картинками и гораздо подробнее
# всё расписывать ... если в конфигурации несколько режимов, их нужно сравнивать
# в таблицах по всем моделированиям"*.
#
# The UI's one-line rule (memory: "no text walls") is a rule about a PANEL, where
# the reader is mid-task and wants the number.  A report is read once, by someone
# deciding whether to build the machine, and every table below is followed by the
# paragraph that says which solver produced it and what it assumed.  Detail is
# the deliverable here, not the noise.


# ── THE WORDS BOTH RENDERERS SAY ────────────────────────────────────────────
# Every narrative paragraph in the comparison sections lives here rather than
# inside the reportlab call that used to hold it.  The .docx of 2026-09-09 is
# the same document as the PDF, and "the same document" is a promise about the
# sentences as much as about the numbers: a caveat edited in one renderer and
# not the other is a report that says two things about one machine.

DUTY_OVERVIEW_EMPTY = (
    "This configuration has no saved duty. Run the point on the Simulation tab "
    "and save it to a duty; the comparison tables below are one column per duty "
    "and there is nothing yet to compare.")

DUTY_STAMPS_LOADED = (
    "When each stored answer was computed. The duty marked * is the one loaded "
    "on this server right now, so the machine's own last thermal / mechanical / "
    "coupled result counts as ITS result and is used in the columns below.")

DUTY_STAMPS_UNLOADED = (
    "When each stored answer was computed. No duty of this configuration is "
    "loaded on this server, so the machine-level last results (the ones the "
    "detail sections further down draw the maps from) are not attributed to any "
    "column — they belong to whatever was loaded when they were solved.")

NOT_SOLVED_RULE = (
    "A cell reading \"" + NOT_SOLVED + "\" means exactly that: nothing has been "
    "solved for that duty and that simulation. It is never filled in from a "
    "neighbouring column — an operating point's temperature, stress and losses "
    "are properties of that point, and the whole reason this report keeps a "
    "per-duty store is so that they cannot be swapped.")

COMPARE_INTRO = (
    "One column per operating point, one table per simulation. A duty with no "
    "stored answer for a simulation reads \"" + NOT_SOLVED + "\" in that table "
    "— never a number from another column.")

EM_COMPARE_NOTE = (
    "Each column is that duty's own saved 2-D transient, same mesh and solver. "
    "Torque is the energy/flux-linkage average over one electrical period; the "
    "3-D column applies the end-effect factor to torque and voltage. A blank "
    "cell is a quantity that run did not record; \"" + NOT_SOLVED + "\" is a "
    "duty with no electromagnetic answer.")

EM_BEARING_NOTE = (
    "Bearing and windage watts are at each duty's own speed and temperature.")

THERMAL_COMPARE_NOTE = (
    "Steady-state conduction on the cross-section, driven by that duty's "
    "cycle-averaged loss density. Air-gap conductance follows from clearance, "
    "speed and gap temperature (Taylor-Couette); the budget residual is what "
    "went in minus what left, and a large one means a missing heat path.")

THERMAL_COMPARE_EMPTY = (
    "No duty of this configuration has a stored temperature map. Open the "
    "Thermal tab with a duty loaded, set the cooling and press Solve; the answer "
    "is then filed under that duty and appears in this column from the next "
    "report on.")

MECH_COMPARE_NOTE = (
    "Plane-stress 2-D with contact, at the speed in the first row. Safety "
    "factors are on the AVERAGED PEAK of each part's own criterion, p99.5 "
    "beside it as the singularity gauge. Temperature enters only as the band's "
    "fit change: strengths are NOT de-rated, so a hot machine's real margin is "
    "smaller than shown.")

CRIT_COMPARE_NOTE = (
    "A critical speed is where a forward whirl branch crosses the 1× unbalance "
    "line. The shaft line is an ASSUMPTION, so these locate the problem and do "
    "not certify the rotor; each column's sweep range is that duty's own, and "
    # CS-9 (audit v7): 27,413 rpm in one column against 27,414 in the other for
    # one shaft line.  Two sweeps bracket the same crossing on different grids;
    # the last digit is the bisection's, not the rotor's, and saying so is
    # honest where chasing it would not be.
    "two sweeps bracket one crossing on different grids — the last digit is "
    "the bisection's, not the rotor's.")

COUPLED_COMPARE_NOTE = (
    "The loop alternates the electromagnetic transient and the thermal map "
    "until the temperature it solved at and the one it solved for agree within "
    "the tolerance shown; the residual rows are the gap left when it stopped. "
    "Shaft efficiency includes bearings and windage, the electromagnetic one "
    "above does not.")

COUPLED_COMPARE_EMPTY = (
    "No duty has a stored coupled run. The temperatures elsewhere in this report "
    "are then the ones each solve was TOLD to use, not ones the machine settled "
    "at.")

WARNINGS_NONE = (
    "No quantity this report can check is at or within 10 % of its limit on any "
    "duty. That is not a clean bill of health: it is a statement about the rules "
    "listed at the end of this section and about the results that were actually "
    "stored — a limit with nothing solved against it raises nothing.")

WARNINGS_RULES_NOTE = (
    "A rule fires only when a stored result carries the quantity it checks; a "
    "limit with nothing solved against it does not appear.")

SOURCES_PER_DUTY_NOTE = (
    "\"ANOTHER machine\" means the geometry fingerprint stored with that answer "
    "is not this configuration's. The number is still printed — it "
    "was a real solve of a real machine — but it is not a number about THIS "
    "configuration, and it is flagged rather than quietly mixed in. "
    "\"Unverified\" means one of the two fingerprints is missing, which is "
    "unknown, not fine.")

SOURCES_PER_DUTY_EMPTY = (
    "No duty of this configuration has a stored solver result yet.")

#: Column headings of the two duty-overview tables.
DUTY_OVERVIEW_HEAD = ["Duty", "Mode", "Speed", "Torque × k_3d", "Shaft power",
                      "Current", "gamma", "Supply", "Solved for"]
DUTY_STAMPS_HEAD = ["Duty", "Electromagnetic", "Thermal", "Coupled",
                    "Rotor stress", "Critical speeds"]


def duty_overview_rows(cols: List[Dict[str, Any]],
                       active: Optional[str] = None
                       ) -> Tuple[List[List[str]], List[List[str]]]:
    """The two overview tables as PLAIN rows: what each duty is, and when each
    stored answer for it was computed.

    Returns ``(what, when)``, both without their header rows — the renderer
    pairs them with :data:`DUTY_OVERVIEW_HEAD` / :data:`DUTY_STAMPS_HEAD`.  The
    duty name is the raw name plus ``" *"`` on the loaded one; a renderer that
    needs it in a font with Cyrillic does that itself.
    """
    label = {"em": "electromagnetic", "thermal": "thermal",
             "coupled": "coupled", "rotor_stress": "rotor stress",
             "critical_speeds": "critical speeds", "modes": "modes",
             "duty_cycle": "duty cycle"}
    what: List[List[str]] = []
    when: List[List[str]] = []
    for c in cols:
        d = c["d"]
        have: List[str] = []
        if c.get("em"):
            have.append("electromagnetic")
        for k in ("thermal", "coupled", "duty_cycle", "rotor_stress",
                  "critical_speeds", "modes"):
            if isinstance((c.get("res") or {}).get(k), dict):
                have.append(label[k])
        # THE RUN'S OWN NUMBERS, not the duty's setpoint (BL-4, audit v6).  The
        # three cells below used to print `torque_nm` / `power_kw` /
        # `current_arms` — what the duty was ASKED for — so page 2 said
        # 179.9 N·m, 267.07 kW and 562.1 A where page 1 said 171.63 N·m,
        # 254.81 kW and the run solved 544.3 A.  One duty, one torque: the
        # cover's own, which is the 2-D average times k_3d.
        _em_c = c.get("em") or {}
        _k_c = _numf(_g(_em_c, "end3d.k_flux"))
        _t_c = _numf(_g(_em_c, "end3d.T_corrected_Nm"))
        if _t_c is None:
            _t2 = _numf(_g(_em_c, "T_em_avg_Nm")) or _numf(d.get("torque_nm"))
            _t_c = (_t2 * _k_c) if (_t2 is not None and _k_c) else _t2
        _sv_c = shaft_view(_em_c, c.get("brg"), d.get("mode"))
        _p_c = _sv_c["P_shaft_W"] if _sv_c["P_shaft_W"] is not None \
            else _sv_c["P_rotor_W"]
        what.append([
            c["duty"] + (" *" if c.get("active") else ""),
            # THE DUTY CLASS BESIDE THE MODE (2026-09-14): a duty with a cycle
            # record is an S2 or an S3 point, and every temperature this report
            # prints for it is the settled cycle's rather than a steady state.
            str(d.get("mode") or "motor") + duty_class_mark(c),
            _fmt(_g(_em_c, "rpm") or d.get("rpm"), 0, "rpm"),
            _fmt(_t_c, 2, "N·m"),
            _fmt(_p_c / 1000.0 if _p_c else _numf(d.get("power_kw")), 2, "kW"),
            _fmt(_g(_em_c, "I_phase_rms_A") or d.get("current_arms"), 1, "A"),
            _fmt(d.get("gamma_deg"), 1, "°"),
            supply_words(c),
            ", ".join(have) or "nothing yet",
        ])
        res = c.get("res") or {}
        when.append([
            c["duty"],
            (_local_stamp((c.get("result") or {}).get("recorded_at")
                          or c["d"].get("saved_at"), "saved")
             if c.get("em") else NOT_SOLVED),
            _stamp(res.get("thermal")), _stamp(res.get("coupled")),
            _stamp(res.get("rotor_stress")), _stamp(res.get("critical_speeds")),
        ])
    return what, when


def _duty_overview(st, cols: List[Dict[str, Any]],
                   active: Optional[str]) -> List[Any]:
    """Which operating points this configuration has, and what has been solved
    for each of them."""
    from reportlab.platypus import Spacer

    out: List[Any] = [_para(section_heading(None, "duties"), st["h1"])]
    if not cols:
        out.append(_para(DUTY_OVERVIEW_EMPTY, st["warn"]))
        return out

    what, when = duty_overview_rows(cols, active)
    # a duty name may be Cyrillic — see the 'hcell'/'dcell' styles
    rows = [list(DUTY_OVERVIEW_HEAD)] + [
        [_para(r[0], st["dcell"])] + list(r[1:]) for r in what]
    out.append(_table(rows, [70, 62, 48, 52, 46, 46, 32, 96,
                             CONTENT_W - 452], header=True, size=8.4))
    # …and where a voltage-fed duty landed against the current it was aimed at
    # (BL-2): the cells above are the SOLVED point, and the miss is a fact about
    # this document's operating point.
    _pe = point_error_text(cols)
    if _pe:
        out.append(_para(_pe, st["note"]))
    # The "when computed" table and the two paragraphs under it are gone
    # (user 2026-09-11: "выкинь это"; the timestamps themselves were already
    # ruled out on 2026-09-10).  `duty_overview_rows` still returns the stamps
    # for anyone who wants them; the document does not print them.
    return out


def battery_rows(batt: Dict[str, Any]) -> List[List[str]]:
    """The PACK the voltage limit comes from.  Header row included; empty when
    the configuration names no battery.

    User 2026-09-10: *"нигде не нашёл информацию про батарейку и лимиты
    напряжения"*.  The report warned against a 749.5 V limit and never said
    where that number came from — it is this pack at its minimum cell voltage,
    which is the worst case for a machine that has to keep making torque.
    """
    b = batt or {}
    if not b:
        return []
    rows = [["The pack", "Value", "What it is"]]

    def R(label, key, unit, what, d=1):
        if b.get(key) is not None:
            rows.append([label, _fmt(b.get(key), d, unit), what])

    if b.get("chemistry"):
        rows.append(["Chemistry", str(b["chemistry"]), "cell type"])
    R("Cells in series", "cells", "", "one string", 0)
    # Three decimals: "3.5 V × 216 = 756" did not reproduce the 749.5 V pack
    # minimum the limits use (reviewer 2026-09-13) — the cell is 2.721 / 3.47 V.
    R("Cell, minimum", "v_cell_min", "V", "the end of discharge", 3)
    R("Cell, nominal", "v_cell_nom", "V", "the plateau", 3)
    R("Cell, maximum", "v_cell_max", "V", "fully charged", 3)
    R("Pack, minimum", "v_min", "V", "cells x minimum cell — THE LIMIT the "
      "line voltage is checked against")
    R("Pack, nominal", "v_nom", "V", "cells x nominal cell")
    R("Pack, maximum", "v_max", "V", "cells x maximum cell — what the "
      "insulation sees on a full charge")
    return rows


BATTERY_NOTE = (
    # TWO CHECKS, and they are not the same check (reviewer 2026-09-14 / PWM
    # study §1.7): the old single sentence described the waveform peak and
    # claimed the control consequence of the fundamental for it.
    "The warnings section checks two limits against this pack: the peak LINE "
    "voltage against the pack minimum (insulation and device rating), and the "
    "FUNDAMENTAL line voltage × k_3d against 0.996 × the pack minimum — "
    "modulation index m ≤ 1.15 — past which the drive runs out of voltage and "
    "the torque collapses."
)

#: …AND A PWM DOCUMENT IS JUDGED AGAINST A LINK, NOT A FLOOR (reviewer
#: 2026-09-15).  A duty solved on the inverter was given one DC link; the
#: questions about it are where that link sits in this pack and whether the
#: bridge can build the fundamental on it.  The waveform-peak-vs-floor rule is
#: not applied to such a duty at all — its "waveform peak" is the
#: star-equivalent model's, not a terminal voltage.
BATTERY_NOTE_PWM = (
    "A duty solved on the inverter is judged against the DC LINK it was solved "
    "on, not against the pack floor: the warnings section checks where that "
    "link sits in this pack's range (a link at the pack maximum is reachable "
    "only on a fully charged pack), the FUNDAMENTAL line voltage against "
    "0.996 × that link — modulation index m ≤ 1.15 — and prints the bridge's "
    "line-to-line pulse amplitude, which is the link itself, as what the "
    "insulation sees. A sinusoidal duty keeps the peak-against-the-pack-minimum "
    "rule."
)


def dc_link_above_nominal_text(cols: Optional[List[Dict[str, Any]]] = None,
                               batt: Optional[Dict[str, Any]] = None) -> str:
    """WHY a duty was solved above the pack's nominal link — ``""`` when none
    was (2026-09-16).

    The L180 gen 'peak' duty is billed on the pack's TOP OF CHARGE, 1,049.8 V,
    while its sister duty runs on the 799.2 V nominal, and no page said why the
    two differ.  The reason is arithmetic the record carries: at the fundamental
    this duty needs, the nominal link would have to be modulated past the
    bridge's linear limit.  The sentence states that and nothing it cannot
    measure — never why an engineer chose a link, only what the nominal one
    could not have done.
    """
    nom = _numf((batt or {}).get("v_nom"))
    if nom is None or nom <= 0:
        return ""
    bits = []
    for c in (cols or []):
        if duty_drive(c) != "pwm":
            continue
        inv = duty_inverter(c)
        vdc, m = _numf(inv.get("v_dc_V")), _numf(inv.get("m"))
        v1 = _numf(inv.get("v_phase_peak_V"))
        if vdc is None or m is None or v1 is None or vdc <= nom + 0.5:
            continue
        # m scales with 1/V_dc at a fixed fundamental: what the same voltage
        # would have cost on the nominal link.
        m_nom = m * vdc / nom
        if m_nom <= MOD_INDEX_LIMIT:
            continue
        bits.append(
            "'%s' is solved on %s, the pack's top of charge: its fundamental "
            "of %s would need a modulation index of %s on the %s nominal link, "
            "past the %s a two-level bridge stays linear to"
            % (c.get("duty") or "—", _fmt(vdc, 1, "V"), _fmt(v1, 1, "V"),
               _fmt(m_nom, 2), _fmt(nom, 1, "V"), _fmt(MOD_INDEX_LIMIT, 2)))
    if not bits:
        return ""
    return ("; ".join(bits)
            + ". Every number of that duty therefore belongs to a fully "
              "charged pack.")


def battery_note(cols: Optional[List[Dict[str, Any]]] = None) -> str:
    """Which voltage rules this document's duties are judged by."""
    return BATTERY_NOTE_PWM if any_pwm(cols) else BATTERY_NOTE

BATTERY_NONE = (
    "No battery is named on this configuration, so no voltage limit is checked."
)


def overspeed_factor_of(cols: Optional[List[Dict[str, Any]]]) -> Any:
    """The overspeed factor the stored rotor-stress solves actually used.

    The lowest one across the duties: a glossary sentence that promises an
    overspeed case has to be true of every column under it.
    """
    got = []
    for c in (cols or []):
        v = _numf(((c.get("res") or {}).get("rotor_stress") or {})
                  .get("overspeed_factor"))
        if v is not None:
            got.append(v)
    return min(got) if got else None


def glossary_rows(em: Dict[str, Any],
                  cols: Optional[List[Dict[str, Any]]] = None) -> List[List[str]]:
    """The symbols this report uses that are not self-explaining.

    User 2026-09-10: *"нигде не нашёл, что такое k_3d — тоже нужно, пользователь,
    который будет читать отчёт, объяснить, что это, так же как и gamma. Может,
    что ещё нужно объяснить, сам реши"*.  So: every symbol a reader meets in a
    headline number or a table header and cannot look up in the document.
    Header row included.
    """
    k3d = _g(em, "end3d.k_flux")
    return [
        ["Symbol", "What it is"],
        ["gamma (γ)",
         "The current angle in ELECTRICAL degrees between the current vector "
         "and the rotor's q-axis. An input, not a result: the report solves at "
         "the angle the duty was saved with."],
        ["k_3d" + (" (this machine: %s)" % _fmt(k3d, 4) if k3d else ""),
         "The 3-D end-effect factor: what the 2-D slice is multiplied by to "
         "match a full 3-D solve of the same machine. Torque, EMF and shaft "
         "power carry it; the losses do not."],
        ["p99.5 / p05",
         "A percentile of a field instead of its extreme, printed beside the "
         "peak as a GAUGE of how singular that peak is. Not the number a part "
         "is sized on."],
        ["Safety factor (SF)",
         "The part's strength over the AVERAGED PEAK stress of its own "
         "criterion — von Mises on yield for steel, the principal stresses for "
         "a magnet, the hoop fibres for the band. That peak is the 'Criterion' "
         "column of the mechanical table."],
        ["Kt / Km",
         "Kt is torque per amp (per LINE amp where the table says so); Km is "
         "torque per root watt of copper, the size-independent figure of merit. "
         "Both carry k_3d."],
        ["THD_LL",
         "Total harmonic distortion of the LINE (terminal) voltage: everything "
         "in the line-to-line waveform that is not the fundamental, per cent of "
         "the fundamental. In delta the circulating 3rd-harmonic EMF never "
         "reaches the terminals and is not in this figure."],
        ["Duty",
         "One operating point this configuration is designed for — current, "
         "speed, angle and cooling, saved together. One column per duty in "
         "every comparison table."],
    ]


def _mag_grade(cols: List[Dict[str, Any]]) -> Optional[str]:
    """The magnet card these runs were solved with, off the first run that
    names one (the assignment is the machine's, not the duty's)."""
    for c in cols:
        n = _g(c.get("em") or {}, "demag.magnet_name")
        if n:
            return str(n)
    return None


def _kv_duty_cell(c: Dict[str, Any], kv_card: Any, grade: Optional[str]) -> str:
    """One cell of the "KV at this duty's magnet temperature" row."""
    v, why = kv_at_magnet_temp(kv_card, grade, _duty_magnet_temp(c.get("em") or {}))
    return _fmt(v, 2) if v is not None else "— %s" % why


#: Two duties of ONE build may not disagree about its mass by more than this.
#: Asked for as half a per cent (2026-09-14) — but the defect that prompted the
#: rule is 0.37 % (27.457 against 27.559 kg on the L155), which half a per cent
#: would have let through.  A tenth is what the gate is, and it is still 25×
#: the gram of rounding two mass tables can legitimately differ by.
MASS_DUTY_SPREAD_PCT = 0.1


def _k_end_of(em: Dict[str, Any]) -> Optional[float]:
    """The end-winding factor this run's copper mass was built with.

    ``end_winding_factor`` in the summary is rounded to two decimals; the
    copper component's own note carries the factor the mass model actually
    multiplied by ("× k_end 1.287"), which is the number that explains the
    difference between two duties.  The note first, the rounded key second.
    """
    for c in (_g(em, "mass_components") or []):
        if not isinstance(c, dict):
            continue
        if "copper" not in str(c.get("material") or "").lower():
            continue
        m = re.search(r"k_end\s*([0-9]*\.?[0-9]+)", str(c.get("note") or ""))
        if m:
            return _numf(m.group(1))
    return _numf(_g(em, "end_winding_factor"))


def mass_consistency(cols: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The duties of one configuration that do not agree on the machine's mass.

    ``None`` when they agree (or when fewer than two carry a mass).  One build
    has one mass; two columns printing 27.457 and 27.559 kg are not two designs
    but one design solved twice with different end-winding factors — 1.287 and
    1.355 on the L155 (user's own data defect, 2026-09-14).  The report must
    not print both and say nothing, so this is what the flagged row, the cover
    line and the amber warning all read from.
    """
    got = []
    for c in cols:
        m = _numf(_g(c.get("em") or {}, "mass_total_kg")) or _numf(
            (c.get("result") or {}).get("mass_kg"))
        if m:
            got.append((str(c.get("duty") or "—"), m,
                        _k_end_of(c.get("em") or {}), c))
    if len(got) < 2:
        return None
    masses = [m for _d, m, _k, _c in got]
    lo, hi = min(masses), max(masses)
    if not lo or 100.0 * (hi - lo) / lo <= MASS_DUTY_SPREAD_PCT:
        return None
    k_txt = " / ".join(_fmt(k, 3) if k is not None else "—"
                       for _d, _m, k, _c in got)
    ks = [k for _d, _m, k, _c in got if k is not None]
    k_differs = len(ks) > 1 and (max(ks) - min(ks) > 1e-4)
    return {
        "duties": [d for d, _m, _k, _c in got],
        "masses": masses,
        "k_end": [k for _d, _m, k, _c in got],
        "spread_pct": 100.0 * (hi - lo) / lo,
        # …and the CELL names the attribute that actually differs (MJ-12): it
        # used to print "k_end 1.355" in both columns, which is one number said
        # twice under a flag that claims the two disagree.
        "cells": [("%s kg, k_end %s" % (_fmt(m, 3), _fmt(k, 3))
                   if (k_differs and k is not None) else
                   "%s kg, %s" % (_fmt(m, 3), _mass_state_cell(c)))
                  for _d, m, k, c in got],
        "text": "Mass differs between duties: " + _mass_cause_text(got, k_txt),
    }


def _mass_state_cell(col: Dict[str, Any]) -> str:
    """``"shaft as reference"`` — which parts this run left out of its total."""
    out = [n for n, (state, _m) in _mass_part_states(col.get("em") or {}).items()
           if state != "included"]
    if not out:
        return "every part counted"
    return ", ".join("%s as %s" % (n, _mass_part_states(
        col.get("em") or {})[n][0]) for n in out)


def _mass_part_states(em: Dict[str, Any]) -> Dict[str, Tuple[str, float]]:
    """``{part name: (state, mass)}`` off a run's own mass components."""
    out: Dict[str, Tuple[str, float]] = {}
    for c in (_g(em or {}, "mass_components") or []):
        if not isinstance(c, dict):
            continue
        # The name carries the material in brackets and, on a reference part,
        # a trailing clause; the leading word is what identifies the part.
        name = str(c.get("name") or "").split("(")[0].strip().lower()
        if name:
            out[name] = (str(c.get("state") or "included"),
                         _numf(c.get("mass_kg")) or 0.0)
    return out


def _mass_cause_text(got: List[Tuple[str, float, Any, Dict[str, Any]]],
                     k_txt: str) -> str:
    """WHY two duties of one build print two masses (MJ-12, audit v6).

    The sentence used to blame the end-winding factor unconditionally — "k_end
    1.355 / 1.355 — the duties were solved with different end-winding factors"
    — while printing two factors that are the same number, and sent the reader
    to re-run for a cause that was not there.  On the L155 the real cause is in
    the mass table two pages earlier: the 0.883 kg shaft is carried as a
    REFERENCE part (customer-supplied) in the run one duty was saved from and
    counted in the other's.  The factors are compared first, the part states
    second, and only a difference neither explains gets the generic sentence.
    """
    ks = [k for _d, _m, k, _c in got if k is not None]
    if len(ks) > 1 and max(ks) - min(ks) > 1e-4:
        return ("k_end %s — the duties were solved with different end-winding "
                "factors; re-run so the build is one" % k_txt)
    # …the parts, then: a name whose STATE differs between two runs, or whose
    # mass does.
    states = [(d, _mass_part_states(c.get("em") or {})) for d, _m, _k, c in got]
    names = sorted({n for _d, s in states for n in s})
    for n in names:
        seen = [(d, s[n]) for d, s in states if n in s]
        if len(seen) < 2 or len({v[0] for _d, v in seen}) < 2:
            continue
        inc = [(d, v) for d, v in seen if v[0] == "included"]
        exc = [(d, v) for d, v in seen if v[0] != "included"]
        if not (inc and exc):
            continue
        return ("the %s (%s) is carried as %s in '%s' and counted in '%s' — "
                "one build, two part states; set the part's state once and "
                "re-save"
                % (n, _fmt(inc[0][1][1], 3, "kg"), exc[0][1][0], exc[0][0],
                   inc[0][0]))
    for n in names:
        vals = [(d, s[n][1]) for d, s in states if n in s]
        if len(vals) > 1 and max(v for _d, v in vals) - min(
                v for _d, v in vals) > 1e-4:
            return ("the %s weighs %s — one build, two masses for one part; "
                    "re-run so the build is one"
                    % (n, " / ".join("%s kg" % _fmt(v, 3) for _d, v in vals)))
    return ("k_end %s and the same parts — the duties were saved from two "
            "different builds; re-run so the build is one" % k_txt)


def em_compare_rows(cols: List[Dict[str, Any]], batt: Dict[str, Any]
                    ) -> Tuple[str, List[List[Any]]]:
    """The electromagnetic comparison as ``(row header, rows)`` — plain data.

    The rows are strings already formatted for a cell, because the formatting IS
    part of the content here: how many decimals a torque is quoted to is an
    engineering statement about the run, not a typographic choice, and it must
    read the same in the .docx and the .pdf.
    """
    def _e(c, *paths, default=None):
        return _g(c["em"], *paths, default=default)

    def _shaft_kw(c) -> Optional[float]:
        """Rotor power × k_3d, minus bearings and windage — the coupling's
        number.  None when the machine has no bearings: an unknown mechanical
        loss is not a zero."""
        p, k = _pk(c), _e(c, "end3d.k_flux")
        x = _numf((c.get("brg") or {}).get("P_mech_extra_W"))
        if p is None or x is None:
            return None
        gen = str(c["d"].get("mode") or "").lower().startswith("gen")
        pk = p * float(k) if k else p
        return pk + x / 1000.0 if gen else max(0.0, pk - x / 1000.0)

    def _pk(c) -> Optional[float]:
        p = _e(c, "P_mech_W")
        if p is None:
            t, n = c["d"].get("torque_nm"), c["d"].get("rpm")
            p = (abs(float(t)) * 2 * math.pi * float(n) / 60.0
                 if (t and n) else None)
        return abs(float(p)) / 1000.0 if p is not None else None

    def _eta(c) -> Optional[float]:
        sv = shaft_view(c["em"] or {}, c.get("brg"), c["d"].get("mode"))
        if sv["eta_em"] is not None:
            return 100.0 * sv["eta_em"]
        v = _e(c, "efficiency")
        if v is not None:
            v = float(v)
            return v * 100.0 if v <= 1.5 else v
        return (c.get("result") or {}).get("efficiency_pct")

    def _eta_sh(c) -> Optional[float]:
        # The card's balance (`shaft_view`) first — the number the catalog row
        # shows; the solve-time `efficiency_shaft` only when the balance
        # cannot be formed (no rotor power or no loss on this entry).
        sv = shaft_view(c["em"] or {}, c.get("brg"), c["d"].get("mode"))
        if sv["eta_shaft"] is not None:
            return 100.0 * sv["eta_shaft"]
        v = _e(c, "efficiency_shaft")
        return (float(v) * 100.0 if (v is not None and float(v) <= 1.5)
                else (float(v) if v is not None else None))

    rows: List[List[Any]] = []

    def R(label, fn, d=2, unit=""):
        rows.append([label] + _col_vals(
            cols, lambda c: (NOT_SOLVED if (not c["em"] and fn(c) is None)
                             else _fmt(fn(c), d, unit))))

    R("Speed [rpm]", lambda c: _e(c, "rpm") or c["d"].get("rpm"), 0)
    # WHAT FEEDS EACH COLUMN (2026-09-14).  A duty whose coupled answer was
    # reached on the inverter is read from its PWM run everywhere in this
    # document, and this row is where the reader learns which columns those
    # are — two duties of one machine may well be one of each.
    rows.append(["Supply"] + _col_vals(cols, supply_words))
    # Per duty, because two duties of one configuration may be solved in Y
    # and in Δ (user 2026-09-13: "нужно добавить соединение в отчёт").
    rows.append(["Terminal connection"] + _col_vals(
        cols, lambda c: _sd_words(None, c["em"] or {}, c["d"])))
    R("Torque, 2-D [N·m]",
      lambda c: _e(c, "T_em_avg_Nm") or c["d"].get("torque_nm"), 3)
    R("Torque × k_3d [N·m]", lambda c: _e(c, "end3d.T_corrected_Nm"), 3)
    R("End-effect factor k_3d", lambda c: _e(c, "end3d.k_flux"), 4)
    # TWO POWERS, named (reviewer 2026-09-11: "Shaft power 569.661 kW on one
    # page, 553.946 on another, 552.49 on a third").  The 2-D rotor power, the
    # same times the end-effect factor, and what leaves the coupling after the
    # bearings and windage — three numbers, three rows, each saying which.
    R("Rotor power, 2-D [kW]", _pk, 3)
    R("Rotor power × k_3d [kW]",
      lambda c: (_pk(c) * float(_e(c, "end3d.k_flux"))
                 if (_pk(c) is not None and _e(c, "end3d.k_flux")) else None), 3)
    R("Shaft power [kW]",
      lambda c: (_shaft_kw(c) if _shaft_kw(c) is not None else None), 3)
    # In a delta machine the current the drive delivers is the LINE current
    # and the copper carries line ÷ √3 — the row says which one it holds.
    _dl_cols = [str(_e(c, "star_delta") or c["d"].get("star_delta") or "")
                .lower().startswith("d") for c in cols]
    R("Line current [A rms]" if all(_dl_cols)
      else ("Phase current [A rms]" if not any(_dl_cols)
            else "Phase (Y) / line (Δ) current [A rms]"),
      lambda c: _e(c, "I_phase_rms_A") or c["d"].get("current_arms"), 1)
    # …AND HOW CLOSE THAT CURRENT IS TO THE ONE THE DUTY ASKED FOR (BL-2).  A
    # voltage-fed run solves for a current; the rated duty of the L155 settled
    # 3.16 % under its setpoint and no page of the document said so.
    if any(duty_point_error(c) for c in cols):
        rows.append(["Operating point vs the duty's setpoint"]
                    + _col_vals(cols, lambda c: point_error_cell(c) or "on point"))
    R("Load angle gamma [°]",
      lambda c: _e(c, "gamma_deg", default=c["d"].get("gamma_deg")), 1)
    R("Current density [A/mm²]",
      lambda c: _e(c, "J_coil_A_per_mm2")
      or (c.get("result") or {}).get("j_coil_a_mm2"), 2)
    # THREE VOLTAGES, each named for what it is (reviewer 2026-09-14): the
    # waveform's own peak, its rms, and the amplitude of its FUNDAMENTAL — a
    # flat-topped line wave peaks BELOW its fundamental amplitude, so V1 above
    # the peak is physics, not a contradiction.
    # …AND ON A PWM DOCUMENT THE FIRST OF THEM IS NOT THE TERMINAL VOLTAGE
    # (reviewer 2026-09-15).  A voltage-fed run solves a star-EQUIVALENT
    # circuit: its "line voltage" is the winding voltage the field gives back,
    # averaged over the carrier, and its peak is 1.155 × V_dc by construction.
    # The row is named for what it is, and the pulse train's own amplitude —
    # the DC link — gets the row under it.
    _pwm_v = any_pwm(cols)
    R(("%s, 2-D [V]" % PWM_VPK_LABEL) if _pwm_v
      else "Line voltage, waveform peak, 2-D [V]",
      lambda c: _e(c, "V_line_peak_V")
      or (c.get("result") or {}).get("v_ll_peak_v"), 1)

    def _vpk3(c) -> Optional[float]:
        """The waveform peak WITH the end-effect factor — the number the pack
        rule in section 7 is judged on (user 2026-09-14).  The comparison table
        printed only the 2-D peak, so a reader checking the 563.4 V against a
        549.6 V pack floor saw a failure the rule does not report: the rule uses
        539.5 V.  The solve's own corrected value first, the product second."""
        v = _numf(_e(c, "end3d.V_line_peak_corrected_V"))
        if v is not None:
            return v
        raw = _numf(_e(c, "V_line_peak_V")
                    or (c.get("result") or {}).get("v_ll_peak_v"))
        k = _numf(_e(c, "end3d.k_flux"))
        return (raw * k) if (raw is not None and k) else None

    R(("%s × k_3d [V]" % PWM_VPK_LABEL) if _pwm_v
      else "Line voltage, waveform peak × k_3d [V]", _vpk3, 1)
    if _pwm_v:
        R(PWM_VDC_LABEL,
          lambda c: (_numf(duty_inverter(c).get("v_dc_V"))
                     if duty_drive(c) == "pwm" else None), 1)
        # …AND WHY THE TWO ROWS ABOVE ARE LARGER THAN IT (MJ-6, audit v7).  A
        # client reading 912.9 V two rows over "= DC link 750.4 V" has every
        # reason to think one of them is wrong, and section 3 is the table read
        # first.  One clause, in the row under the pair it reconciles.
        rows.append([PWM_VPK_VS_VDC_LABEL]
                    + _col_vals(cols, lambda c: (
                        PWM_VPK_VS_VDC_NOTE if duty_drive(c) == "pwm"
                        else "—")))
    R("Line voltage, rms [V]", lambda c: _e(c, "V_line_rms_V"), 1)

    def _v1k(c) -> Optional[float]:
        """The FUNDAMENTAL line-to-line amplitude with k_3d on it — the only
        voltage the inverter's modulation limit is about (reviewer 2026-09-14 /
        PWM study §1.7).  The peak row above is the waveform's, harmonics
        included, and on a delta machine it can read LOWER than this."""
        v, k = _numf(_e(c, "V1_LL_V")), _numf(_e(c, "end3d.k_flux"))
        return (v * k) if (v is not None and k) else v

    R("Line voltage, fundamental amplitude V1 × k_3d [V]", _v1k, 1)
    if batt.get("v_min"):
        rows.append(["Pack minimum voltage [V]"]
                    + [_fmt(batt["v_min"], 1)] * len(cols))
        # The gate itself, printed rather than left to be derived in someone's
        # head: m at the pack's worst moment, against the 1.15 a two-level
        # bridge with zero-sequence injection holds.
        R("Modulation index m at the pack minimum (limit %s)"
          % _fmt(MOD_INDEX_LIMIT, 2),
          lambda c: (None if duty_drive(c) == "pwm"
                     else modulation_index(_v1k(c), batt.get("v_min"))), 3)
    if _pwm_v:
        # …and on a PWM column the bridge MEASURED its m, on the link it ran
        # on; the pack-floor derivation above describes a different run.
        R("Modulation index m the bridge ran at (limit %s)"
          % _fmt(MOD_INDEX_LIMIT, 2),
          lambda c: (_numf(duty_inverter(c).get("m"))
                     if duty_drive(c) == "pwm" else None), 3)
    R("Torque ripple [%]",
      lambda c: _e(c, "T_ripple_pct")
      or (c.get("result") or {}).get("ripple_pct"), 2)
    # TWO RIPPLES ON A PWM DOCUMENT (2026-09-14).  The row above is the supply's
    # own — on a bridge it is the CARRIER's, tens of per cent — and the 5 % gate
    # in the warnings section is on the low-order one, which is the sinusoidal
    # run's.  Both are printed, so the reader never has to guess which number
    # the limit is about.
    if any_pwm(cols):
        # …ON THE PWM COLUMNS ONLY (CS-5, audit v6).  A sinusoidal column's
        # ripple IS the low-order one, so the split printed the same number
        # twice under two labels and taught the reader nothing.
        R("…of which low-order (cogging + slotting), sinusoidal run [%]",
          lambda c: (_g(c.get("em_sine") or {}, "T_ripple_pct")
                     if duty_drive(c) == "pwm" else None), 2)
    # LINE voltage THD (terminal, triplen-free in delta) — the same number the
    # U_AB spectrum chart prints; the winding's own THD_pct is not a terminal
    # quantity (reviewer 2026-09-13: 6.32 % in the table vs 1.2 % on the chart).
    # TWO THDs ON A PWM DOCUMENT, exactly as with the ripple (reviewer
    # 2026-09-15): the bridge's pulse train is tens of per cent by
    # construction, the 10 % gate is on the machine's own distortion, and the
    # rows say which is which rather than leaving one number to be read as both.
    # …AND THE BRIDGE ROW IS THE BRIDGE'S OWN NUMBER (MJ-1, audit v7): the
    # pulse train's THD, integrated from its edges by `bridge_pulse_thd_pct` —
    # the very function the U_AB spectrum panel prints from — and not the
    # field's carrier-averaged winding THD, which was 32.28 % under the bridge's
    # name beside a figure saying 115 %.
    R(("Line voltage THD at the bridge (pulse train) [%]" if _pwm_v
       else "Line voltage THD [%]"),
      lambda c: (_numf(c.get("bridge_thd_pct")) if duty_drive(c) == "pwm"
                 else (_e(c, "THD_LL_pct") or _e(c, "THD_pct"))), 2)
    if _pwm_v:
        R("…of which low-order, sinusoidal run — the 10 % limit's row [%]",
          lambda c: (sine_line_thd(c) if duty_drive(c) == "pwm" else None), 2)
    # Mean |B| over the air-gap clearance, area-weighted and averaged over the
    # electrical period.  Under LOAD — magnet flux and armature reaction
    # together — so it is not the no-load fundamental a sizing formula asks for.
    R("Air-gap mean |B| [T]", lambda c: _e(c, "B_gap_mean_T"), 3)
    # KV and Kt with the 3-D factor where the run has one — the catalog row's
    # convention (KV rises by 1/k, Kt falls by k); Kt against the LINE current
    # in delta, the card's figure.
    def _kdiv(c, key):
        v, k = _numf(_e(c, key)), _numf(_e(c, "end3d.k_flux"))
        return (v / k) if (v is not None and k) else v
    def _kmul(c, v):
        k = _numf(_e(c, "end3d.k_flux"))
        return (v * k) if (v is not None and k) else v
    def _kt(c):
        dl = str(_e(c, "star_delta") or "star").lower().startswith("d")
        v = _e(c, "Kt_Nm_per_A_line") if dl else None
        return _numf(v if v is not None else _e(c, "Kt_Nm_per_Arms"))
    # THE NO-LOAD KV IS A MACHINE FIGURE, not a duty's (reviewer 2026-09-14):
    # the solver probes ψ_PM at the magnet CARD's temperature, so the row reads
    # the same under every column and a reader comparing duties needs to know
    # why.  The second row walks it to each duty's own magnet temperature.
    # THE MACHINE CONSTANTS ARE THE SINUSOID'S, always (2026-09-14).  A
    # voltage-fed inverter run does not probe ψ_PM and does not measure a KV;
    # these four rows therefore keep reading the duty's sinusoidal summary, and
    # on a PWM document the label says so rather than letting the reader take
    # them for numbers the bridge produced.
    _sine_tail = SINE_ROW_TAIL if any_pwm(cols) else ""
    R("KV, no load (magnets at the card's %s) [rpm/V]%s"
      % (_fmt(_magnet_card_temp(_mag_grade(cols)) or 150.0, 0, "°C"), _sine_tail),
      lambda c: _kdiv(c, "KV_noload_rpm_per_V_line"), 2)
    rows.append(["KV, no load at this duty's magnet temperature [rpm/V]"
                 + _sine_tail]
                + _col_vals(cols, lambda c: _kv_duty_cell(c, _kdiv(
                    c, "KV_noload_rpm_per_V_line"), _mag_grade(cols))))
    R("KV, loaded [rpm/V]" + _sine_tail,
      lambda c: _kdiv(c, "KV_rpm_per_V_line"), 2)
    R("Kt, line current [N·m/A rms]" + _sine_tail,
      lambda c: _kmul(c, _kt(c)), 4)
    R("Km [N·m/sqrt(W)]" + _sine_tail,
      lambda c: _kmul(c, _numf(_e(c, "Km_Nm_sqrtW"))), 3)
    R("Copper loss [W]",
      lambda c: _e(c, "P_stranded_W")
      or (c.get("result") or {}).get("p_stranded_w"), 1)
    R("Iron loss [W]",
      lambda c: _e(c, "P_core_W") or (c.get("result") or {}).get("p_core_w"), 1)
    R("Magnet + solid loss [W]",
      lambda c: _e(c, "P_solid_W") or (c.get("result") or {}).get("p_solid_w"), 1)
    # CONTAINED, not additional.  `P_solid_W` is magnet + shaft + sleeve, so a
    # reader adding this column up counted the band twice — 6,462.6 W against
    # the 6,442.9 W the total says (user 2026-09-10, reading the first docx).
    # The three parts are now named as parts OF the row above, with the leading
    # ellipsis this report already uses for a sub-row.
    R("…of which the magnets [W]", lambda c: _e(c, "P_mag_W"), 1)
    R("…of which the shaft [W]", lambda c: _e(c, "P_shaft_W"), 1)
    R("…of which the sleeve [W]", lambda c: _e(c, "P_sleeve_W"), 1)
    R("Total electromagnetic loss [W]",
      lambda c: _e(c, "P_loss_total_W") or (c.get("result") or {}).get("loss_w"), 1)
    R("Bearing loss [W]", lambda c: _e(c, "P_bearings_W"), 1)
    R("Windage [W]", lambda c: _e(c, "P_windage_W"), 2)
    R("Electromagnetic efficiency [%]", _eta, 2)
    R("Shaft efficiency [%]", _eta_sh, 2)
    R("Br kept in the magnets [%]",
      lambda c: _g(c["em"], "demag.br_kept_vol_pct"), 3)
    R("Worst magnet element, Br [%]",
      lambda c: _g(c["em"], "demag.br_worst_pct"), 1)
    R("Mass [kg]",
      lambda c: _e(c, "mass_total_kg") or (c.get("result") or {}).get("mass_kg"), 3)
    # ONE BUILD, ONE MASS.  Two columns quoting two masses is a data defect, not
    # a comparison, and the document must not print it silently (2026-09-14).
    _mc = mass_consistency(cols)
    if _mc:
        rows.append(["%s Mass differs between duties" % FLAG] + list(_mc["cells"]))
    return "Electromagnetic", _drop_empty(rows)


def _em_compare(st, cols: List[Dict[str, Any]], batt: Dict[str, Any],
                brg: Optional[Dict[str, Any]]) -> List[Any]:
    """The electromagnetic table: every duty side by side."""
    out: List[Any] = [_para("Electromagnetic", st["h2"])]
    if not cols:
        return out
    header, rows = em_compare_rows(cols, batt)
    out.append(_cmp_table(header, cols, rows, st=st))
    _mc = mass_consistency(cols)
    if _mc:
        out.append(_para("%s %s." % (FLAG, _mc["text"]), st["warn"]))
    out.append(_para(EM_COMPARE_NOTE, st["body"]))
    if brg and brg.get("has_bearings"):
        out.append(_para(EM_BEARING_NOTE, st["note"]))
    return out


def thermal_compare_rows(cols: List[Dict[str, Any]]
                         ) -> Tuple[str, List[List[Any]]]:
    """Per-component temperatures, the boundary conditions and the heat budget,
    duty by duty — plain rows, no renderer."""
    def _t(c):
        v = (c.get("res") or {}).get("thermal")
        return v if isinstance(v, dict) else None

    def _comp(c, key, which):
        t = _t(c)
        if t is None:
            return None
        e = (t.get("components") or {}).get(key)
        return e.get(which) if isinstance(e, dict) else None

    # No "Solved at" row: the stamps are gone from this report
    # (user 2026-09-10) — a result is identified by its machine
    # and its operating point, both of which are stated.
    rows: List[List[Any]] = []

    def R(label, fn, d=1, unit=""):
        rows.append([label] + _col_vals(
            cols, lambda c: (NOT_SOLVED if _t(c) is None else _fmt(fn(c), d, unit))))

    def S(label, fn):
        rows.append([label] + _col_vals(
            cols, lambda c: (NOT_SOLVED if _t(c) is None else fn(c))))

    # `slot_fill` joined the list on 2026-09-14 (reviewer, D11): it is in the
    # detail table and on the bar chart and was the only part missing here.
    for key, label in (("winding", "Winding (copper)"), ("enamel", "Wire enamel"),
                       ("liner", "Slot insulation"),
                       ("slot_fill", "Slot fill / impregnation"),
                       ("magnet", "Magnets"),
                       ("rotor", "Rotor core"), ("stator", "Stator core"),
                       ("shaft", "Shaft"), ("sleeve", "Retaining sleeve"),
                       ("gap_air", "Air-gap air")):
        if not any(isinstance(((_t(c) or {}).get("components") or {}).get(key), dict)
                   for c in cols):
            continue
        # ONE ROW PER PART (user 2026-09-11: "пиши эти все через чёрточку —
        # меньше будет строк").  Two rows per part filled a page with the same
        # nine labels written twice; "143 / 139" says the same in one line, and
        # the header says which is which.
        S(f"{label}, max / avg [°C]",
          lambda c, k=key: _pair(_comp(c, k, "max"), _comp(c, k, "avg")))
    R("Hot spot in the machine [°C]", lambda c: (_t(c) or {}).get("T_max"))
    R("Coolest point [°C]", lambda c: (_t(c) or {}).get("T_min"))
    # WHAT THE MAP CARRIES is `losses_W`, the integral of the loss density over
    # the solved mesh — the same key the detail budget was moved to on
    # 2026-09-11.  This row still read a `P_in_W` that no budget writes and
    # fell back to the ELECTROMAGNETIC total, so the column showed 7,120 W in
    # and 7,358 W out and the reviewer, rightly, called it a balance that does
    # not close.  It closes: the 238 W were the mechanical heat inside the map
    # plus the loss map's own integration against the summed terms, and both
    # are now rows of their own.
    def _budget(c, k):
        return _g(_t(c) or {}, "cooling.heat_budget." + k)

    R("Losses put into the map [W]",
      lambda c: (_budget(c, "losses_W") or _budget(c, "P_in_W")
                 or (_t(c) or {}).get("P_loss_total_W")))
    R("  of which copper [W]", lambda c: (_t(c) or {}).get("P_cu_W"))
    R("  of which iron [W]", lambda c: (_t(c) or {}).get("P_fe_W"))
    # …and the solid-conductor eddy terms, so the sub-rows ADD UP to the row
    # above them: the thermal record keeps copper and iron by name and the
    # rest only inside its electromagnetic total.
    # The rest of the MAP's own integral: the solid-conductor eddy terms the
    # loss map carries by element (magnets and shaft; the band's milliwatts
    # are in it too).  Derived from `losses_W`, not from `em_loss_total_W`,
    # which counted the magnets alone and made this row read 232 W under a
    # "magnets, shaft and sleeve" label (reviewer 2026-09-13, item 5).
    R("  of which solid conductors — magnet + shaft eddy, in the map [W]",
      lambda c: ((_numf(_budget(c, "losses_W") or _budget(c, "P_in_W")
                        or (_t(c) or {}).get("P_loss_total_W")) or 0.0)
                 - (_numf((_t(c) or {}).get("P_cu_W")) or 0.0)
                 - (_numf((_t(c) or {}).get("P_fe_W")) or 0.0)
                 - (_numf(_budget(c, "mech_loss_in_map_W")) or 0.0)
                 if (_budget(c, "losses_W") or _budget(c, "P_in_W")) is not None else None))
    R("  of which mechanical, inside the section [W]",
      lambda c: _budget(c, "mech_loss_in_map_W"))
    # The band's own eddy loss is NOT in the map — the thermal solve carries
    # no loss model for the sleeve — which is exactly the gap between the
    # electromagnetic total and the map's total (reviewer 2026-09-13, item 9).
    R("  not in the map: sleeve eddy (no loss model for the band) [W]",
      lambda c: _g(c.get("em") or {}, "P_sleeve_W"))
    # ONE comparison, the same one the reconcile paragraph under the heat
    # budget makes — see `thermal_budget_gap_w` (reviewer 2026-09-14, B3).
    R("  loss-map integration vs the summed terms [W]",
      lambda c: thermal_budget_gap_w(
          _g(_t(c) or {}, "cooling.heat_budget") or {}, c.get("em") or {}), 2)
    R("Removed through the housing [W]",
      lambda c: _g(_t(c) or {}, "cooling.outer.heat_removed_W"))
    # The two mechanisms of the still-air film, per duty — the same pair the
    # detail page prints, and `_drop_empty` takes both rows out again on a
    # report whose columns are all forced-air or jacketed (2026-09-14).
    R("  of which convection [W]",
      lambda c: (_g(_t(c) or {}, "cooling.heat_budget.housing_convection_W")
                 if (_numf(_g(_t(c) or {},
                              "cooling.heat_budget.housing_radiation_W")) or 0.0)
                 > 0.005 else None))
    R("  of which radiation [W]",
      lambda c: (_g(_t(c) or {}, "cooling.heat_budget.housing_radiation_W")
                 if (_numf(_g(_t(c) or {},
                              "cooling.heat_budget.housing_radiation_W")) or 0.0)
                 > 0.005 else None))
    R("Removed through the bore [W]",
      lambda c: _g(_t(c) or {}, "cooling.inner.heat_removed_W"))
    R("Removed through the shaft ends [W]",
      lambda c: _g(_t(c) or {}, "cooling.shaft_ends.heat_removed_W"))
    # THE MOUNT and THE END FACES (2026-09-14) — the robot joint's two paths.
    # Both rows carry a 0 on a machine that has neither, which is an answer,
    # and both disappear entirely (`_drop_empty`) on a report of records
    # written before the mode existed.
    R("Mount conductance [W/K]",
      lambda c: _g(_t(c) or {}, "cooling.mount.G_W_per_K"), 2)
    R("Removed into the mount [W]",
      lambda c: _g(_t(c) or {}, "cooling.mount.heat_removed_W"))
    R("Removed off the end faces [W]",
      lambda c: _g(_t(c) or {}, "cooling.end_faces.heat_removed_W"))
    for _k, _lbl in (("winding", "  of which the end windings [W]"),
                     ("stator", "  of which the stator core's ends [W]"),
                     ("rotor", "  of which the rotor core's ends [W]"),
                     ("magnet", "  of which the magnet ends [W]")):
        R(_lbl, lambda c, k=_k: _g(_t(c) or {},
                                   "cooling.end_faces.%s.heat_removed_W" % k))
    R("Budget residual [W]",
      lambda c: _g(_t(c) or {}, "cooling.heat_budget.residual_W"), 2)
    R("Rotor heat out across the gap [W]",
      lambda c: _g(_t(c) or {}, "cooling.heat_budget.rotor_heat_split.gap_W"), 1)
    R("…as a share of the rotor's loss [%]",
      lambda c: _g(_t(c) or {}, "cooling.heat_budget.rotor_heat_split.gap_pct"), 0)
    R("Rotor heat out off the bore [W]",
      lambda c: _g(_t(c) or {},
                   "cooling.heat_budget.rotor_heat_split.bore_W"), 1)
    R("…as a share of the rotor's loss [%]",
      lambda c: _g(_t(c) or {},
                   "cooling.heat_budget.rotor_heat_split.bore_pct"), 0)
    # ZERO, NOT A BLANK, wherever the split itself exists (reviewer 2026-09-14,
    # C4).  A record written before the axial keys existed and one that solved
    # a model with no end faces are the same physics — a 2-D cross-section has
    # no end faces — and printing "0" under one duty and "—" under the next
    # made the peak run look as though it had lost data it never had.
    def _rsplit(c, key):
        sp = _g(_t(c) or {}, "cooling.heat_budget.rotor_heat_split")
        if not isinstance(sp, dict):
            return None
        v = _numf(sp.get(key))
        return v if v is not None else 0.0

    R("Rotor heat out off the rotor / magnet end faces [W]",
      lambda c: _rsplit(c, "axial_end_faces_W"), 1)
    R("…as a share of the rotor's loss [%]",
      lambda c: _rsplit(c, "axial_end_faces_pct"), 0)
    # ── THE STATOR's own balance, and then the headline split (2026-09-14) ──
    # The same rows as the per-duty page, because the question the user asks of
    # one duty ("how much goes out through the mount?") is the question he asks
    # of all of them side by side.
    for _k, _lbl, _d in (
            ("stator_W", "Made in the stator side [W]", 1),
            ("gap_in_W", "  in across the air gap [W]", 1),
            ("total_in_W", "  = into the stator side, in total [W]", 1),
            ("housing_W", "Stator heat out through the housing [W]", 1),
            ("housing_pct", "…as a share of what enters the stator side [%]", 0),
            ("mount_W", "Stator heat out into the mount [W]", 1),
            ("mount_pct", "…as a share of what enters the stator side [%]", 0),
            ("end_faces_W", "Stator heat out off the end faces [W]", 1),
            ("end_faces_pct", "…as a share of what enters the stator side [%]", 0)):
        R(_lbl, lambda c, k=_k: _g(
            _t(c) or {}, "cooling.heat_budget.stator_heat_split." + k), _d)

    # THE HEADLINE (user 2026-09-14): stator side against rotor side, as watts
    # and as shares of everything that left.  Derived, not stored, so the two
    # rows say the same thing under every duty — see :func:`_heat_sides`.
    def _sides(c):
        return _heat_sides((_t(c) or {}).get("cooling") or {})

    def _side_pct(c, which):
        st, ro = _sides(c)
        if st is None or ro is None or abs(st + ro) < 1e-9:
            return None
        return 100.0 * (st if which == "stator" else ro) / (st + ro)

    R("Stator side (housing + mount + end faces) [W]",
      lambda c: _sides(c)[0], 1)
    R("…as a share of everything that left [%]",
      lambda c: _side_pct(c, "stator"), 0)
    R("Rotor side (bore + shaft + end faces) [W]",
      lambda c: _sides(c)[1], 1)
    R("…as a share of everything that left [%]",
      lambda c: _side_pct(c, "rotor"), 0)
    R("Air-gap k_eff [W/m·K]", lambda c: _g(_t(c) or {}, "cooling.gap.k_eff"), 4)
    rows.append(["Housing boundary"] + _col_vals(
        cols, lambda c: (NOT_SOLVED if _t(c) is None
                         else _bc_words(_g(_t(c), "cooling.outer") or {}))))
    rows.append(["Bore boundary"] + _col_vals(
        cols, lambda c: (NOT_SOLVED if _t(c) is None
                         else _bc_words(_g(_t(c), "cooling.inner") or {}))))
    # The open frame's own two paths — only when some column actually has them;
    # a housed machine reports `mode: housed` here, which is an answer, not a gap.
    if any(_g(_t(c) or {}, "cooling.frame") for c in cols):
        rows.append(["Frame"] + _col_vals(
            cols, lambda c: (NOT_SOLVED if _t(c) is None else str(
                (_g(_t(c), "cooling.frame") or {}).get("mode") or "—"))))
        R("Removed off the end windings [W]",
          lambda c: _g(_t(c) or {}, "cooling.end_windings.heat_removed_W"))
        R("Removed down the slot ducts [W]",
          lambda c: _g(_t(c) or {}, "cooling.slot_channels.heat_removed_W"))
    return "Thermal", _drop_empty(rows)


def _thermal_compare(st, cols: List[Dict[str, Any]]) -> List[Any]:
    """Per-component temperatures, the boundary conditions and the heat budget,
    duty by duty."""
    from reportlab.platypus import Spacer

    out: List[Any] = [_para("Thermal", st["h2"])]
    if not cols:
        return out
    header, rows = thermal_compare_rows(cols)
    out.append(_cmp_table(header, cols, rows, st=st))
    out.append(Spacer(1, 2))
    out.append(_para(THERMAL_COMPARE_NOTE, st["body"]))
    if not any(isinstance((c.get("res") or {}).get("thermal"), dict)
               for c in cols):
        out.append(_para(THERMAL_COMPARE_EMPTY, st["warn"]))
    return out


def _bc_words(rep: Dict[str, Any]) -> str:
    """One cell's worth of boundary condition."""
    mode = str((rep or {}).get("mode") or "none").lower()
    if mode.startswith("non") or mode == "none":
        return "adiabatic"
    # STILL AIR (2026-09-14): the housing of the robotics mode reports
    # `robotics`, its unventilated bore `still`.  Read by the old ladder both
    # fell through to "fixed h", which is exactly what this boundary is not —
    # its coefficient is derived from the wall temperature and half of it is
    # radiation, so the cell names both films and the ε they were taken at.
    if mode.startswith("robot") or mode == "still":
        try:
            eps = "%.2f" % float(rep.get("emissivity") or 0.0)
        except (TypeError, ValueError):
            eps = "—"
        return "still air %s (h %s conv + %s rad, ε %s)" % (
            _fmt(rep.get("t_sink_c"), 0, "°C"), _fmt(rep.get("h_conv"), 1),
            _fmt(rep.get("h_rad"), 1), eps)
    if mode.startswith("liq"):
        return "%s jacket, in %s, %s, wall at outlet %s" % (
            (rep.get("fluid") or "coolant"), _fmt(rep.get("t_in_c"), 1, "°C"),
            _fmt(rep.get("flow_lpm"), 2, "L/min"),
            _fmt(rep.get("t_out_c", rep.get("t_sink_c")), 1, "°C"))
    # ONE ROUNDING for a sink temperature (reviewer 2026-09-14, D5): the
    # boundary-conditions bullet prints it to a decimal, so this cell does too
    # — 31.7 °C in one place and 32 °C in the other read as two numbers.
    if mode.startswith("air"):
        return "air %s, sink %s (h %s)" % (
            _fmt(rep.get("air_speed_mps"), 1, "m/s"),
            _fmt(rep.get("t_sink_c"), 1, "°C"), _fmt(rep.get("h_conv"), 0))
    return "fixed h %s at %s" % (_fmt(rep.get("h_conv"), 0),
                                 _fmt(rep.get("t_sink_c"), 1, "°C"))


def _worst_open(m: Optional[Dict[str, Any]]) -> Tuple[Optional[str], Optional[float]]:
    """The RETENTION joint and how much of it has opened [%].

    Named for what it used to do — take the worst of every separation pair —
    and kept under that name only so the callers do not have to move; see
    `RETENTION_PAIRS` for why the worst pair was the wrong one to quote.
    """
    if not isinstance(m, dict):
        return None, None
    lbl, i = retention_interface(m)
    if i is None:
        return None, None
    f = _numf(i.get("open_fraction"))
    label = (f"{_interface_words(lbl)} {FLAG} LIFT-OFF" if i.get("lift_off")
             else _interface_words(lbl))
    return label, (None if f is None else 100.0 * f)


def _seated_words(m: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(m, dict):
        return None
    seated = ((m.get("contact") or {}).get("seated") or [])
    mags = [s for s in seated if str(s.get("part") or "") == "magnet"]
    if not mags:
        return "none — nothing had to travel"
    return "%d, worst %s µm" % (
        len(mags), _fmt(max((s.get("travel_rel_um") or 0.0) for s in mags), 1))


def _liftoff_words(m: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(m, dict):
        return None
    lo = {k: v for k, v in (m.get("lift_off_rpm") or {}).items() if v}
    if not lo:
        return "not searched"
    # The RETENTION joint's speed if it has one; the lowest of the rest is a
    # measurement of a pair that holds nothing, and quoting it as "the lift-off
    # speed" is what made a sound rotor look like a failing one.
    _r, _ = retention_interface(m)
    if _r in lo:
        return "%s at %s rpm" % (_interface_words(_r), _fmt(lo[_r], 0))
    k = min(lo, key=lambda x: lo[x])
    return "retention joint holds; %s (bridging) opens at %s rpm" % (
        _interface_words(k), _fmt(lo[k], 0))


def _criterion_words(cols: List[Dict[str, Any]], part: str) -> str:
    """Which stress a part's safety factor is taken on, in words.

    Read off the part's own ``strength_kind``: a tensile strength is applied to
    the max principal stress (the magnets, the sleeve's hoop), a yield to the
    von Mises equivalent.  Used to LABEL the governing-stress row (MJ-6).
    """
    for c in cols:
        m = (c.get("res") or {}).get("rotor_stress")
        e = ((m or {}).get("parts") or {}).get(part)
        if isinstance(e, dict) and e.get("strength_kind"):
            k = str(e.get("strength_kind")).lower()
            if k.startswith("tens"):
                return "max principal"
            if k.startswith("yield") or k.startswith("proof"):
                return "von Mises"
            return k
    return "the part's own criterion"


def mech_compare_rows(cols: List[Dict[str, Any]]
                      ) -> Tuple[str, List[List[Any]]]:
    """Rotor stress and contacts, duty by duty — plain rows, no renderer."""
    def _m(c):
        v = (c.get("res") or {}).get("rotor_stress")
        return v if isinstance(v, dict) else None

    def _p(c, part, key):
        m = _m(c)
        if m is None:
            return None
        e = (m.get("parts") or {}).get(part)
        return e.get(key) if isinstance(e, dict) else None

    # No "Solved at" row: the stamps are gone from this report
    # (user 2026-09-10) — a result is identified by its machine
    # and its operating point, both of which are stated.
    rows: List[List[Any]] = []

    def R(label, fn, d=2, unit=""):
        rows.append([label] + _col_vals(
            cols, lambda c: (NOT_SOLVED if _m(c) is None else _fmt(fn(c), d, unit))))

    def S(label, fn):
        rows.append([label] + _col_vals(
            cols, lambda c: (NOT_SOLVED if _m(c) is None
                             else (str(fn(c)) if fn(c) is not None else "—"))))

    R("Speed solved [rpm]", lambda c: (_m(c) or {}).get("rpm"), 0)
    R("Overspeed factor", lambda c: (_m(c) or {}).get("overspeed_factor"), 2)
    S("Case", lambda c: (_m(c) or {}).get("case"))
    R("Lowest safety factor", lambda c: (_m(c) or {}).get("sf_min"), 2)
    S("…on part", lambda c: (_m(c) or {}).get("sf_min_part"))
    # THE ROTOR'S OWN ROW NEEDS ITS SENTENCE (user 2026-09-15).  The bridges
    # between the poles are assembly features; the factor on them stays, and so
    # does the flag, but a client must not read "SF 0.22" without being told
    # what those bridges are for and what holds the magnets.
    _bridge_said = any(is_rotor_bridge_part((_m(c) or {}).get("sf_min_part"))
                       for c in cols)
    if _bridge_said:
        rows.append(["…what the rotor bridges carry"] + _col_vals(
            cols, lambda c: (ROTOR_BRIDGE_POLICY
                             if is_rotor_bridge_part(
                                 (_m(c) or {}).get("sf_min_part")) else "—")))
    R("Lowest SF on the p05 field", lambda c: (_m(c) or {}).get("sf_min_p05"), 2)
    # THE ROTOR'S OWN KEY IS "rotor" (BT-7, 2026-09-16).  This loop asked for
    # "rotor_core", which the rotor-stress record does not use — `parts` are
    # rotor / magnet / sleeve / shaft — so section 3 printed the stress set of
    # the magnet, the sleeve and the shaft and NONE of the rotor's, on a
    # machine whose rotor is the part that fails (SF 0.22 / 0.19 in the very
    # first rows of the same table).  Both spellings are accepted; everything
    # else about the loop is unchanged.
    def _has(part: str) -> bool:
        return any(isinstance(((_m(c) or {}).get("parts") or {}).get(part), dict)
                   for c in cols)

    _rotor = next((k for k in ROTOR_BRIDGE_PARTS if _has(k)), "rotor_core")
    for part, label in ((_rotor, "Rotor core"), ("magnet", "Magnets"),
                        ("sleeve", "Sleeve"), ("shaft", "Shaft")):
        if not _has(part):
            continue
        R(f"{label} von Mises p99.5 [MPa]",
          lambda c, p=part: _p(c, p, "von_mises_p995_mpa"), 1)
        R(f"…{label} von Mises peak, unaveraged [MPa]",
          lambda c, p=part: _p(c, p, "von_mises_max_unaveraged_mpa"), 1)
        # THE NUMBER THE SAFETY FACTOR IS TAKEN ON (MJ-6, reviewer
        # 2026-09-14).  Strength and SF sat under a von Mises p99.5 that does
        # not divide into them — 80 / 263.1 = 0.30 beside a printed 2.05 —
        # because the criterion of a magnet is its max PRINCIPAL stress, and
        # the averaged peak of it appeared nowhere in this table.
        R(f"{label}, governing stress (averaged peak, {_criterion_words(cols, part)}) [MPa]",
          lambda c, p=part: _p(c, p, "governing_stress_mpa"), 1)
        R(f"{label} strength [MPa]",
          lambda c, p=part: _p(c, p, "strength_mpa"), 0)
        R(f"{label} safety factor",
          lambda c, p=part: _p(c, p, "safety_factor"), 2)
        # THE ROTOR'S SAFETY FACTOR NEEDS ITS SENTENCE (user 2026-09-15).  The
        # bridges between the poles are assembly features; the number stays and
        # so does the flag on it, but a client must not read "SF 0.24" without
        # being told what those bridges are for and what holds the magnets.
        # …ONCE (BT-7, 2026-09-16).  The row above already carries it whenever
        # the rotor is the part the lowest safety factor is on, which is every
        # machine whose rotor rows this branch prints.
        if is_rotor_bridge_part(part) and not _bridge_said:
            rows.append(["…what the rotor bridges carry"]
                        + _col_vals(cols, lambda c: ROTOR_BRIDGE_POLICY))
    if any(isinstance(((_m(c) or {}).get("parts") or {}).get("sleeve"), dict)
           for c in cols):
        R("Sleeve hoop stress [MPa]",
          lambda c: _p(c, "sleeve", "hoop_max_mpa"), 1)
    if any(isinstance(((_m(c) or {}).get("parts") or {}).get("magnet"), dict)
           for c in cols):
        R("Magnet max principal p99.5 [MPa]",
          lambda c: _p(c, "magnet", "principal_max_p995_mpa"), 1)
    S("Worst separation joint", lambda c: _worst_open(_m(c))[0])
    R("…open fraction [%]", lambda c: _worst_open(_m(c))[1], 1)
    S("Torque path", lambda c: _g(_m(c) or {}, "torque_path.verdict"))
    S("Magnet retention", lambda c: _g(_m(c) or {}, "magnet_retention.verdict"))
    S("Magnets seated during the solve", lambda c: _seated_words(_m(c)))
    S("Lift-off speed", lambda c: _liftoff_words(_m(c)))
    R("Rotor OD growth [µm]",
      lambda c: (_m(c) or {}).get("rotor_od_growth_um"), 1)
    R("…outer surface travel, maximum [µm]",
      lambda c: ((_m(c) or {}).get("od_growth") or {}).get("max_um"), 1)
    R("Air-gap clearance, cold [µm]",
      lambda c: ((_m(c) or {}).get("air_gap") or {}).get("clearance_um"), 1)
    R("…left running [µm]",
      lambda c: ((_m(c) or {}).get("air_gap") or {}).get("remaining_um"), 1)
    R("…of the clearance used [%]",
      lambda c: ((_m(c) or {}).get("air_gap") or {}).get("closed_pct"), 0)
    R("…outer surface travel, mean [µm]",
      lambda c: ((_m(c) or {}).get("od_growth") or {}).get("mean_um"), 1)
    R("Maximum displacement [µm]",
      lambda c: (_m(c) or {}).get("max_displacement_um"), 1)
    R("Sleeve interference, geometric [mm]",
      lambda c: (_m(c) or {}).get("interference_mm"), 4)
    R("…effective at temperature [mm]",
      lambda c: (_m(c) or {}).get("interference_effective_mm"), 4)
    S("Temperatures the solve ran at",
      lambda c: ", ".join(f"{k} {float(v):g}" for k, v in
                          sorted(((_m(c) or {}).get("part_temps_c") or {}).items())
                          if v is not None) or None)
    return "Mechanical", _drop_empty(rows)


def crit_compare_rows(cols: List[Dict[str, Any]]
                      ) -> Tuple[str, List[List[Any]]]:
    """The rotordynamics comparison — empty rows when no column has an answer,
    which is the renderer's cue to leave the whole sub-section out."""
    def _cs(c):
        v = (c.get("res") or {}).get("critical_speeds")
        return v if isinstance(v, dict) else None

    if not any(_cs(c) for c in cols):
        return "Critical speeds", []
    # No "Solved at" row: the stamps are gone from this report
    # (user 2026-09-10) — a result is identified by its machine
    # and its operating point, both of which are stated.
    crows: List[List[Any]] = []
    crows.append(["Rated speed [rpm]"] + _col_vals(
        cols, lambda c: (NOT_SOLVED if _cs(c) is None
                         else _fmt((_cs(c) or {}).get("rated_rpm"), 0))))
    crows.append(["Verdict"] + _col_vals(
        cols, lambda c: (NOT_SOLVED if _cs(c) is None
                         else str((_cs(c) or {}).get("verdict") or "—"))))
    # FORWARD and BACKWARD apart (reviewer 2026-09-11: "rated 22,900, first
    # critical 22,585, verdict subcritical — wrong").  The 22,585 crossing is a
    # BACKWARD whirl: on isotropic bearings unbalance does not excite it, and
    # the verdict is judged on the forward branch, whose first crossing is
    # 23,987.  Printing the lowest of BOTH under "lowest critical" beside a
    # forward-only verdict is what made the page contradict itself.
    def _lowest(c, whirl):
        cs = (_cs(c) or {}).get("critical_speeds") or []
        v = [x.get("rpm") for x in cs
             if x.get("rpm") is not None and str(x.get("whirl") or "") == whirl]
        return min(v) if v else None
    crows.append(["Lowest FORWARD critical (unbalance-excited) [rpm]"] + _col_vals(
        cols, lambda c: (NOT_SOLVED if _cs(c) is None
                         else _fmt(_lowest(c, "forward"), 0))))
    # ONE definition of the margin on the whole page: (critical − rated) / RATED,
    # the same number the rotordynamics table and the verdict text print
    # (reviewer 2026-09-13: 17.5 % here against 21.2 % there for one crossing).
    def _margin_vs_rated(c):
        f1 = _lowest(c, "forward")
        r0 = float((_cs(c) or {}).get("rated_rpm") or 0)
        return (100.0 * (f1 - r0) / r0) if (f1 and r0) else None
    crows.append(["  margin above rated"] + _col_vals(
        cols, lambda c: (NOT_SOLVED if _cs(c) is None else (
            _fmt(_margin_vs_rated(c), 1, "%")
            + ("  " + FLAG + " thin" if _margin_vs_rated(c) < NEAR_PCT else "")
            if _margin_vs_rated(c) is not None else "—"))))
    crows.append(["Lowest BACKWARD crossing (not excited on isotropic bearings) [rpm]"]
                 + _col_vals(cols, lambda c: (NOT_SOLVED if _cs(c) is None
                                              else _fmt(_lowest(c, "backward"), 0))))
    crows.append(["Crossings of the 1x line"] + _col_vals(
        cols, lambda c: (NOT_SOLVED if _cs(c) is None else str(
            len((_cs(c) or {}).get("critical_speeds") or [])))))
    return "Critical speeds", _drop_empty(crows)


def _mech_compare(st, cols: List[Dict[str, Any]]) -> List[Any]:
    """Rotor stress, contacts and critical speeds, duty by duty."""
    from reportlab.platypus import Spacer

    out: List[Any] = [_para("Mechanical", st["h2"])]
    if not cols:
        return out
    header, rows = mech_compare_rows(cols)
    out.append(_cmp_table(header, cols, rows, st=st))
    out.append(Spacer(1, 2))
    out.append(_para(MECH_COMPARE_NOTE, st["body"]))

    cheader, crows = crit_compare_rows(cols)
    if crows:
        # A HEADING NEVER ENDS A PAGE (BT-10, 2026-09-16) — the docx renderer's
        # `keepNext` on this heading and on the table's header band, in the
        # only form this renderer has.  The table is four rows; if it ever
        # cannot fit, `KeepTogether` splits rather than refusing to lay out.
        from reportlab.platypus import KeepTogether as _KT
        out.append(_KT([_para(CRIT_HEADING, st["h2"]),
                        _cmp_table(cheader, cols, crows, st=st)]))
        out.append(_para(CRIT_COMPARE_NOTE, st["body"]))
    return out


#: What each of the coupled loop's own refusal codes means, in the four words
#: a "Converged" cell has room for (`routes.coupled`).
COUPLED_REFUSAL_WORDS = {
    "point_limited_by_modulation": "modulation ceiling",
    "last_pass_dc_unconverged": "unsettled DC in the bridge",
    "point_not_converged": "the operating point did not settle",
}


def coupled_temps_settled(rec: Optional[Dict[str, Any]]) -> Optional[bool]:
    """True when EVERY temperature residual the coupled record carries is
    inside its own tolerance; ``None`` when it carries none.

    The loop's ``converged`` flag is an AND of the temperatures and the
    operating point, so a run that settled its temperatures perfectly and then
    stopped because the bridge ran out of voltage stores ``converged: false``.
    Printing that as "NOT converged" beside residuals of 0.15 K against ± 2 K
    is a contradiction the reader has to resolve for himself (L180 gen 'rated',
    2026-09-16), so every sentence about the loop asks this first.
    """
    if not isinstance(rec, dict):
        return None
    tol, tol_b = _numf(rec.get("tol_K")), _numf(rec.get("tol_bearing_K"))
    pairs = [(_numf(rec.get("residual_coil_K")), tol),
             (_numf(rec.get("residual_magnet_K")), tol),
             (_numf(rec.get("residual_bearing_K")), tol_b)]
    pairs = [(r, t) for r, t in pairs
             if r is not None and t is not None and t > 0]
    if not pairs:
        return None
    return all(abs(r) <= t + 1e-9 for r, t in pairs)


def coupled_modulation_limit(rec: Optional[Dict[str, Any]]
                             ) -> Optional[Dict[str, Any]]:
    """The inverter ceiling a coupled run ended ON, or ``None``.

    A voltage-fed loop that cannot reach its current setpoint because the
    largest fundamental the bridge can synthesise is smaller than the one the
    regulator wants did not fail and did not run out of passes: every pass
    solved, the last ones at the clamp.  This reads that state off the record —
    the machine-readable ``warning_code`` first, the route's own sentence on
    records written before it existed.
    """
    if not isinstance(rec, dict):
        return None
    inv = rec.get("inverter") if isinstance(rec.get("inverter"), dict) else {}
    code = str(rec.get("warning_code") or "").strip()
    msg = str(rec.get("warning") or "")
    hit = (code == "point_limited_by_modulation" or "out of INVERTER" in msg
           or bool(inv.get("at_modulation_ceiling")))
    if not hit:
        return None
    return {
        "v_dc_V": _numf(inv.get("v_dc_V")),
        "carriers": _numf(inv.get("carriers_per_period")),
        "v1_ceiling_V": _numf(inv.get("v_phase_peak_max_V")),
        "v1_uncompensated_V": _numf(inv.get("v_phase_peak_max_uncompensated_V")),
        "solved_A": _numf(inv.get("I_phase_rms_solved_A")),
        "target_A": _numf(inv.get("target_I_phase_rms_A")),
        "point_error_pct": _numf(inv.get("point_error_pct")),
    }


def coupled_refusal_reason(rec: Optional[Dict[str, Any]]) -> Tuple[Any, str]:
    """``(pass number, reason)`` of a coupled run whose PASS was refused.

    The record's ``warning_code`` first; records written before it existed
    (2026-09-15 and earlier — the L180 gen duties among them) are read from the
    refusal sentence itself, which is the route's own wording and names both.
    ``(None, "")`` when the loop simply ran out of iterations.

    A pass number is returned ONLY when a pass really was refused, i.e. the
    route says so by name.  A point held at the modulation ceiling is NOT a
    refusal — every pass of it solved — and it used to be reported as
    "pass 5 refused" because the reason was inferred from the run count
    (L180 gen 'rated', 2026-09-16): :func:`coupled_modulation_limit` is what
    answers for that state now.
    """
    if not isinstance(rec, dict):
        return None, ""
    code = str(rec.get("warning_code") or "").strip()
    msg = str(rec.get("warning") or "")
    if not code and not msg:
        return None, ""
    n = None
    m = re.search(r"electromagnetic run (\d+) refused", msg)
    if m:
        n = int(m.group(1))
    reason = COUPLED_REFUSAL_WORDS.get(code, "")
    if not reason:
        low = msg.lower()
        if ("linear limit" in low or "out of inverter" in low
                or "modulation" in low or "modulator" in low):
            reason = "modulation ceiling"
        elif "unsettled dc" in low:
            reason = "unsettled DC in the bridge"
        elif "refused" in low:
            reason = "the electromagnetic solve refused"
        elif "operating point did not" in low:
            reason = "the operating point did not settle"
    # A code that names a modulation limit beats the prose every time.
    if code == "last_pass_em_refused" and reason == "":
        reason = "the electromagnetic solve refused"
    return n, reason


#: The configuration keys the coupled route's own refusal sentences name for
#: the OPERATOR, and the words a client document says instead (MJ-5, audit v7).
#: Longest first, so `inverter.i_tol_pct` is not eaten by a prefix.
_OPERATOR_KEYS = (
    ("inverter.i_tol_pct", "the point tolerance"),
    ("inverter.v_dc_V", "the DC link"),
    ("inverter.", "the inverter's "),
    ("max_iter", "the iteration budget"),
)


def _client_words(msg: str) -> str:
    """One stored refusal sentence with the operator's half taken off.

    The route writes for whoever can re-run the loop: it ends in an imperative
    naming the keys to change.  A client document says what happened and stops;
    the remedy lives in the engineer's log.
    """
    out = str(msg or "").strip()
    # The remedy is always the last clause, introduced by an em dash.
    cut = out.rfind(" — raise ")
    if cut > 0:
        out = out[:cut]
    for key, word in _OPERATOR_KEYS:
        out = out.replace(key, word)
    out = out.strip().rstrip(";,")
    if out and not out.endswith("."):
        out += "."
    return (out[:1].upper() + out[1:]) if out else ""


def coupled_warning_words(rec: Optional[Dict[str, Any]],
                          point_pct: Optional[float] = None) -> str:
    """The coupled loop's warning as a CLIENT reads it — ``""`` when there is
    none.

    MJ-5 (audit v7).  Section 6 of the peak document printed the route's own
    sentence verbatim: two configuration keys (``max_iter``,
    ``inverter.i_tol_pct``) and a millivolt-resolution voltage (617.110 V), in
    a document written for the buyer of the machine.  What the buyer needs is
    the fact — how many passes, how far off the point, against what tolerance,
    and where the next pass would have aimed — and this says exactly that.

    ``point_pct`` is the miss as the rest of the document prints it (CS-1): the
    route's own figure is the winding current's and rounds to −1.17 % where
    every other page says −1.18 %, with an ASCII hyphen against their Unicode
    minus.  One number, one sign.
    """
    if not isinstance(rec, dict):
        return ""
    msg = str(rec.get("warning") or "").strip()
    if not msg:
        return ""
    code = str(rec.get("warning_code") or "").strip()
    settled = coupled_temps_settled(rec)
    # ── THE BRIDGE RAN OUT OF VOLTAGE (2026-09-16) ─────────────────────────
    # Built from the record, not passed through from the route's sentence: the
    # stored prose is written for whoever can re-run the loop ("raise
    # inverter.v_dc_V or the carrier"), says "out of INVERTER" in capitals, and
    # carries its own rounding of the miss (−3.10 % against the document's
    # −3.09 %).  Every number below is the inverter block's own.
    lim = coupled_modulation_limit(rec)
    if lim and lim.get("v1_ceiling_V") is not None:
        pct = point_pct if point_pct is not None else lim.get("point_error_pct")
        bits = ["The bridge ran out of voltage, not out of passes: the largest "
                "fundamental it can build%s%s is %s" % (
                    ("" if lim.get("v_dc_V") is None else
                     " on the %s link" % _fmt(lim["v_dc_V"], 1, "V")),
                    ("" if lim.get("carriers") is None else
                     " at %d carriers per electrical period"
                     % int(lim["carriers"])),
                    _fmt(lim["v1_ceiling_V"], 2, "V"))]
        if lim.get("v1_uncompensated_V") is not None:
            bits.append(" (modulation index %s once the modulator's "
                        "sampled-reference gain is compensated, %s before it)"
                        % (_fmt(MOD_INDEX_LIMIT, 2),
                           _fmt(lim["v1_uncompensated_V"], 1, "V")))
        bits.append(", the last pass ran there")
        if lim.get("solved_A") is not None and lim.get("target_A") is not None:
            bits.append(" and drew %s against the %s this duty is billed at%s"
                        % (_fmt(lim["solved_A"], 1, "A"),
                           _fmt(lim["target_A"], 1, "A"),
                           ("" if pct is None
                            else " (%s %%)" % _signed(pct, 2))))
        tail = ("" if settled is not True else
                " The temperatures settled inside their tolerance; it is the "
                "operating point that did not.")
        return "".join(bits) + "." + tail
    if code == "point_not_converged" or "operating point did not" in msg.lower():
        runs = _numf(rec.get("em_runs") or rec.get("iterations"))
        if runs is None:
            m = re.search(r"after (\d+) electromagnetic run", msg)
            runs = float(m.group(1)) if m else None
        pct = point_pct
        if pct is None:
            m = re.search(r"draws\s*([-+−]?[\d.]+)\s*%", msg)
            pct = _numf(str(m.group(1)).replace("−", "-")) if m else None
        m = re.search(r"off the ([\d.]+) A", msg)
        billed = _numf(m.group(1)) if m else None
        m = re.search(r"tolerance\s*±\s*([\d.]+)", msg)
        tol = _numf(m.group(1)) if m else None
        m = re.search(r"aimed at ([\d.]+) V", msg)
        aim = _numf(m.group(1)) if m else None
        if runs and pct is not None:
            # …AND ONLY WHEN THEY DID (2026-09-16).  The route writes "the
            # temperatures settled but the operating point did not" whenever
            # the point is the thing it was regulating, and on the L180 gen
            # 'peak' duty it wrote that over residuals of 8.92 K, 15.08 K and
            # 12.9 K against ± 2 K and ± 5 K — three rows above, in the same
            # table.  The residuals decide which sentence this is.
            head = ("The temperatures settled, the operating point did not: "
                    if coupled_temps_settled(rec) is not False else
                    "Neither the operating point nor the temperatures had "
                    "settled when the loop ran out of passes: ")
            return (head
                    + "%d passes, and the machine draws %s %% off the %s this "
                      "duty is billed at%s.%s The temperatures above are the "
                      "last pass that solved."
                    % (int(runs), _signed(pct, 2),
                       _fmt(billed, 2, "A") if billed is not None
                       else "current",
                       ("" if tol is None
                        else " (tolerance ± %s %%)" % _fmt(tol, 0)),
                       ("" if aim is None else
                        " The next pass would have aimed at %s."
                        % _fmt(aim, 0, "V"))))
    return _client_words(msg)


def converged_words(rec: Optional[Dict[str, Any]],
                    point_pct: Optional[float] = None) -> str:
    """The "Converged" cell — ``"yes"``, a runaway, or WHY it is not yes.

    A bare "no" under a table of temperatures says nothing a client can act on
    (reviewer 2026-09-15): the L180 'rated' duty stopped because the bridge
    could not build a bigger fundamental on its link, and that is the sentence,
    not "no".

    …AND IT SAYS WHAT THE RECORD SAYS (2026-09-16).  That duty's five passes
    ALL SOLVED — the last two at the clamp — and its temperatures settled to
    0.15 K of a ± 2 K tolerance, so the cell used to print "pass 5 refused:
    modulation ceiling" about a pass that was never refused and a loop whose
    temperatures had converged.  What is unconverged here is the operating
    POINT, and the cell now separates the two.

    ``point_pct`` is the miss as the rest of the document prints it — line
    current, Unicode minus — so the cell and section 3's own row agree.
    """
    if not isinstance(rec, dict):
        return "—"
    if rec.get("converged"):
        return "yes"
    if rec.get("runaway"):
        return "RUNAWAY " + FLAG
    settled = coupled_temps_settled(rec)
    runs = _numf(rec.get("em_runs") or rec.get("iterations"))
    n, reason = coupled_refusal_reason(rec)
    if n is not None and reason:
        return ("pass %d refused: %s — temperatures are the last solved pass"
                % (n, reason))
    lim = coupled_modulation_limit(rec)
    if lim:
        # The temperatures are one verdict, the point another; the point's is
        # the inverter's ceiling and it is stated with the number it is.
        head = ("temperatures converged in %d passes" % int(runs)
                if settled and runs else
                ("temperatures converged" if settled else
                 ("temperatures not settled in %d passes" % int(runs)
                  if runs else "temperatures not settled")))
        pct = point_pct if point_pct is not None else lim.get("point_error_pct")
        tail = ""
        if lim.get("v1_ceiling_V") is not None:
            tail = " (the fundamental sits at the %s ceiling%s)" % (
                _fmt(lim["v1_ceiling_V"], 2, "V"),
                ("" if pct is None else
                 ", %s %% off the setpoint" % _signed(pct, 2)))
        return head + "; the operating point is limited by the inverter" + tail
    if reason:
        if settled is False and runs:
            return ("no — %s, and the temperatures had not settled in %d passes"
                    % (reason, int(runs)))
        return "no — %s" % reason
    return "no"


def coupled_compare_rows(cols: List[Dict[str, Any]]
                         ) -> Tuple[str, List[List[Any]]]:
    """The coupled electromagnetic/thermal loop, duty by duty — plain rows."""
    def _c(c):
        v = (c.get("res") or {}).get("coupled")
        return v if isinstance(v, dict) else None

    # No "Solved at" row: the stamps are gone from this report
    # (user 2026-09-10) — a result is identified by its machine
    # and its operating point, both of which are stated.
    rows: List[List[Any]] = []

    def R(label, fn, d=2, unit=""):
        rows.append([label] + _col_vals(
            cols, lambda c: (NOT_SOLVED if _c(c) is None else _fmt(fn(c), d, unit))))

    # NO CLIP (MJ-2, audit v7).  These cells were cut at exactly 160 characters
    # — mid-word, with no ellipsis: the peak duty's convergence sentence stopped
    # inside "(tolerance", taking its remedy with it.  A comparison cell wraps
    # in the PDF (`_cmp_table._cell`) and in Word (the row is kept whole), so
    # there was nothing for the clip to protect.
    def S(label, fn):
        rows.append([label] + _col_vals(
            cols, lambda c: (NOT_SOLVED if _c(c) is None
                             else (str(fn(c)) if fn(c) is not None else "—"))))

    def _sh(c):
        # The card's balance where it can be formed (the same `shaft_view` as
        # the headline and the electromagnetic table — one number per run);
        # the loop's own solve-time figure otherwise.
        cb = _c(c) or {}
        x = _numf(cb.get("P_mech_extra_W"))
        if isinstance(c.get("em"), dict) and c["em"] and x is not None:
            sv = shaft_view(c["em"], {"has_bearings": True, "P_mech_extra_W": x},
                            (c.get("d") or {}).get("mode"))
            if sv["eta_shaft"] is not None:
                return 100.0 * sv["eta_shaft"]
        v = cb.get("efficiency_shaft")
        if v is None:
            return None
        v = float(v)
        return v * 100.0 if v <= 1.5 else v

    R("Electromagnetic runs", lambda c: (_c(c) or {}).get("em_runs")
      or (_c(c) or {}).get("iterations"), 0)
    S("Converged", lambda c: converged_words(
        _c(c), (duty_point_error(c) or {}).get("pct")))
    R("Winding temperature [°C]", lambda c: (_c(c) or {}).get("coil_temp_c"), 1)
    R("Magnet temperature [°C]", lambda c: (_c(c) or {}).get("magnet_temp_c"), 1)
    R("Magnet temperature, hottest [°C]",
      lambda c: (_c(c) or {}).get("magnet_temp_max_c"), 1)
    R("Bearing temperature [°C]", lambda c: (_c(c) or {}).get("bearing_temp_c"), 1)
    # …AND WHY THE PROVENANCE DIFFERS between two duties of one machine
    # (reviewer 2026-09-14, C5).  A loop that needed a second electromagnetic
    # run fed the seat temperature back and converged on it; a loop that
    # converged on the first pass bills the last thermal map, which is the
    # same map, read one iteration earlier.  Both are defensible; a client
    # reading two words for one quantity is not, unless the row says why.
    def _bts(c):
        src = str((_c(c) or {}).get("bearing_temp_source") or "") or None
        if src is None:
            return None
        runs = _numf((_c(c) or {}).get("em_runs")
                     or (_c(c) or {}).get("iterations"))
        if runs is None:
            return src
        # …AND "converged" ONLY WHEN IT CONVERGED (2026-09-16).  A second pass
        # feeds the seat back; it does not make the feedback settle.  The L180
        # gen 'peak' loop ran four passes and left the seat 12.9 K out of its
        # ± 5 K tolerance — the row two above this one — while this cell said
        # the seat "was converged and fed back".
        resid = _numf((_c(c) or {}).get("residual_bearing_K"))
        tol = _numf((_c(c) or {}).get("tol_bearing_K"))
        if runs < 2:
            how = "read off the one thermal map"
        elif resid is not None and tol is not None and tol > 0 \
                and abs(resid) > tol:
            how = ("fed back but still %s K out of its ± %s K tolerance"
                   % (_fmt(abs(resid), 1), _fmt(tol, 1)))
        else:
            how = "converged and fed back"
        return ("%s — %s run(s), so the seat temperature was %s"
                % (src, _fmt(runs, 0), how))

    S("…where it came from", _bts)
    R("Bearing loss [W]", lambda c: (_c(c) or {}).get("P_bearings_W"), 1)
    R("Windage [W]", lambda c: (_c(c) or {}).get("P_windage_W"), 2)
    R("Mechanical loss total [W]", lambda c: (_c(c) or {}).get("P_mech_extra_W"), 1)
    R("Loss incl. mechanical [W]",
      lambda c: (_c(c) or {}).get("P_loss_total_incl_mech_W"), 1)
    R("Shaft efficiency [%]", _sh, 2)
    # "± 2.0", never a bare "2": two adjacent cells reading "2" were taken for
    # "22 K" against the 2 K of the text (reviewer 2026-09-13, item 2).
    S("Tolerance, winding and magnets [K]",
      lambda c: (None if (_c(c) or {}).get("tol_K") is None
                 else "± %.1f" % float((_c(c) or {}).get("tol_K"))))
    R("Residual, winding [K]", lambda c: (_c(c) or {}).get("residual_coil_K"), 2)
    R("Residual, magnets [K]", lambda c: (_c(c) or {}).get("residual_magnet_K"), 2)
    # The bearing-seat pair rides the run's own coupling block as well as the
    # per-duty record (records written before 2026-09-13 evening lack it).
    def _cb(c, key):
        v = (_c(c) or {}).get(key)
        return v if v is not None else _g(c.get("em") or {}, "coupling." + key)
    S("Tolerance, bearing seat [K]",
      lambda c: (None if _cb(c, "tol_bearing_K") is None
                 else "± %.1f" % float(_cb(c, "tol_bearing_K"))))
    R("Residual, bearing seat [K]", lambda c: _cb(c, "residual_bearing_K"), 2)
    # THE CLIENT'S HALF OF THE ROUTE'S SENTENCE (MJ-5 / CS-1, audit v7), and
    # the miss in the same figure and the same sign as every other page.
    S("Warning", lambda c: coupled_warning_words(
        _c(c), (duty_point_error(c) or {}).get("pct")) or None)
    return "Coupled loop", _drop_empty(rows)


def _coupled_compare(st, cols: List[Dict[str, Any]]) -> List[Any]:
    """The coupled electromagnetic/thermal loop, duty by duty."""
    out: List[Any] = [_para("Coupled electromagnetic / thermal loop", st["h2"])]
    if not cols:
        return out
    header, rows = coupled_compare_rows(cols)
    out.append(_cmp_table(header, cols, rows, st=st))
    out.append(_para(COUPLED_COMPARE_NOTE, st["body"]))
    if not any(isinstance((c.get("res") or {}).get("coupled"), dict)
               for c in cols):
        out.append(_para(COUPLED_COMPARE_EMPTY, st["note"]))
    return out


#: Column headings of the warnings table.  The .docx carries the remedy as a
#: sixth column (Word wraps a paragraph inside a cell happily); the PDF spells
#: the remedies out underneath instead, where reportlab can keep each one with
#: its own heading.
WARNINGS_HEAD = ["", "Duty", "Quantity", "Value", "Limit", "Margin"]
WARNINGS_HEAD_DOCX = ["Duty", "Quantity", "Value", "Limit", "Margin",
                      "What to do"]


def cross_duty_warnings(cols: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Warnings about the SET of duties — one build, one answer per question.

    ``kind='data'``: not a physical limit but a consistency check on the stored
    results, which is why it carries no unit and its remedy is "re-run", not
    "cool it".  Amber, always: nothing about the machine is over a limit, but
    the document cannot be trusted to be about one build until it is fixed.
    """
    out: List[Dict[str, Any]] = []
    mc = mass_consistency(cols)
    if mc:
        out.append({
            "rule": "mass_consistency", "level": "amber",
            "duty": ", ".join(mc["duties"]),
            "quantity": "Mass differs between duties",
            "value": mc["spread_pct"], "limit": MASS_DUTY_SPREAD_PCT,
            "unit": "%", "kind": "data",
            "margin_pct": round(MASS_DUTY_SPREAD_PCT - mc["spread_pct"], 1),
            "remedy": mc["text"],
            "note": "%s kg on one build" % " / ".join(
                _fmt(m, 3) for m in mc["masses"])})
    return out


#: Rules that answer for the CONFIGURATION and not for one duty (BT-9,
#: 2026-09-16).  The runaway speed is built from the machine's no-load KV and
#: the pack minimum — neither is a duty's — and it is checked against the
#: FASTEST duty of the configuration, so the identical row
#: (17,985.2 rpm · 22,900 rpm · −21.5 %) printed under both L180 gen duties,
#: each duty-tagged.  A reader comparing the two could only conclude that the
#: 20,900 rpm duty had been judged at its own speed, which it had not.
CONFIG_LEVEL_RULES: Tuple[str, ...] = ("runaway_speed",)

#: What the duty column says on a row that is about the configuration.
CONFIG_LEVEL_DUTY = "all duties"

#: …and what its own name then has to say, because the limit is one duty's.
CONFIG_LEVEL_TAIL = " (against this configuration's fastest duty)"


def collapse_config_level(ws: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Print a configuration-level rule ONCE, tagged with what it is about.

    Only when every duty's copy of it carries the same value and the same
    limit: a runaway speed read off each duty's own loaded line voltage really
    is per-duty, and collapsing those would hide the one that matters.
    """
    same: Dict[str, bool] = {}
    for rule in CONFIG_LEVEL_RULES:
        rows = [w for w in ws if w.get("rule") == rule]
        same[rule] = (len(rows) > 1
                      and len({(w.get("value"), w.get("limit")) for w in rows}) == 1)
    out: List[Dict[str, Any]] = []
    done: set = set()
    for w in ws:
        rule = w.get("rule")
        if not same.get(rule):
            out.append(w)
            continue
        if rule in done:
            continue
        done.add(rule)
        w = dict(w)
        w["duty"] = CONFIG_LEVEL_DUTY
        if not str(w.get("quantity") or "").endswith(CONFIG_LEVEL_TAIL):
            w["quantity"] = str(w.get("quantity") or "") + CONFIG_LEVEL_TAIL
        out.append(w)
    return out


def all_duty_warnings(cols: List[Dict[str, Any]],
                      ctxs: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Every duty's warnings, red first — the order both renderers print."""
    all_w: List[Dict[str, Any]] = []
    for c in cols:
        all_w += duty_warnings(ctxs.get(c["duty"]) or {"duty": c["duty"]})
    # …with the rules that are about the CONFIGURATION printed once (BT-9).
    all_w = collapse_config_level(all_w)
    # …plus the checks that are ABOUT the set of duties rather than about one
    # of them.  A build whose duties disagree on its own mass is a data defect,
    # and the warnings section is where the reader looks for those (2026-09-14).
    all_w += cross_duty_warnings(cols)
    # INFO rows come last and are counted as nothing: they carry no limit, so
    # they are neither passed nor failed — the carrier's torque ripple is the
    # first of them (2026-09-14).
    return ([w for w in all_w if w["level"] == "red"]
            + [w for w in all_w if w["level"] == "amber"]
            + [w for w in all_w if w["level"] == "green"]
            + [w for w in all_w if w["level"] == "info"])


#: Ink per verdict, shared by both renderers so a colour means one thing.
WARN_INK = {"red": "#C0392B", "amber": "#B7791F", "green": "#1E7A3C",
            "info": "#5A6472"}


def warning_row(w: Dict[str, Any]) -> List[str]:
    """One warning as the cells of the warnings table (without the remedy)."""
    return [
        {"red": "OVER", "amber": "near", "info": "info"}.get(w["level"], "ok"),
        str(w["duty"]), str(w["quantity"]),
        (_fmt(w["value"], 2, w["unit"]) if w["value"] is not None else "flagged"),
        (_fmt(w["limit"], 2, w["unit"]) if w["limit"] is not None else "—"),
        (_fmt(w["margin_pct"], 1, w.get("margin_unit") or "%")
         if w["margin_pct"] is not None else "—"),
    ]


def warnings_headline(reds: int, ambers: int, greens: int = 0) -> str:
    return ("%d past a limit (red), %d within 10 %% of one (amber), %d checked "
            "and inside (green)." % (reds, ambers, greens))


def warning_sentence(w: Dict[str, Any]) -> str:
    """The "over by how much" clause the remedy hangs off."""
    if w["value"] is not None and w["limit"] is not None:
        return ("%s against a limit of %s, %s margin"
                % (_fmt(w["value"], 2, w["unit"]),
                   _fmt(w["limit"], 2, w["unit"]),
                   _fmt(w["margin_pct"], 1, w.get("margin_unit") or "%")))
    return "flagged by the solve"


def limit_rules_context(ctxs: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Which duty's context the limits table is built from.

    Almost every limit in it is the MACHINE's, so any column answers — but the
    duty-cycle row exists only on a duty that has a cycle, and taking the first
    column blindly dropped that row whenever the cycle belonged to the second
    (2026-09-14).  A column with a cycle wins; otherwise the first, as before.
    """
    vals = list((ctxs or {}).values())
    return next((c for c in vals
                 if _numf((c or {}).get("ed_allowable_pct")) is not None),
                next(iter(vals), {}) if vals else {})


def limit_rules_rows(ex: Dict[str, Any]) -> List[List[str]]:
    """The "where every limit comes from" table, header row included.

    ``ex`` is any one duty's warning context — the limits it carries are the
    machine's, not that duty's, so any column answers for all of them.
    """
    _j_rule = current_density_limit(ex.get("cooling_kind") or "air",
                                    ex.get("insulation_mats"),
                                    ex.get("frame_open"))
    return [
        ["Quantity", "Limit", "Source of the limit"],
        ["Magnet temperature", _fmt(ex.get("magnet_limit_c"), 0, "°C"),
         str(ex.get("magnet_limit_note")
             or "the magnet grade's coercivity class")],
        ["Winding temperature", _fmt(ex.get("winding_limit_c"), 0, "°C"),
         str(ex.get("winding_limit_note") or "")],
        ["Hot spot in the machine", _fmt(ex.get("winding_limit_c"), 0, "°C"),
         "the same insulation limit, applied to the hottest point anywhere"],
        ["Irreversible demagnetisation",
         _fmt(DEMAG_LOSS_LIMIT_PCT, 1, "% of Br"),
         "the project's criterion, volume-averaged over the magnets"],
        ["Worst magnet element, Br retained",
         "%s (red), %s (amber)" % (_fmt(DEMAG_WORST_RED_PCT, 0, "%"),
                                   _fmt(DEMAG_WORST_AMBER_PCT, 0, "%")),
         "the worst SINGLE element, not the volume average; this report's own "
         "bands"],
        ["Mechanical safety factor", _fmt(2.0, 1),
         "the project's acceptance level, on the AVERAGED PEAK of each part's "
         "criterion"],
        ["Sleeve hoop stress", "the sleeve card",
         "the assigned sleeve material's own strength"],
        ["Magnet tensile stress", "the magnet card",
         "the assigned magnet's tensile strength (sintered NdFeB ~80 MPa)"],
        ["Separation joint open fraction",
         _fmt(OPEN_FRACTION_LIMIT_PCT, 0, "%"),
         "this report's own, judged on the RETENTION joint only; the other "
         "pairs open by design"],
        ["Torque path", "must be held",
         "the rotor-stress solve's own verdict on what carries the torque"],
        ["Bonded joint in tension", "must stay in compression",
         "a press fit cannot pull, so a tie in tension is a joint that is not "
         "there"],
        ["Bearing speed", "the bearing card",
         "the card's n·dm rating for the assigned lubrication"],
        ["Bearing temperature",
         _fmt(ex.get("bearing_temp_limit_c"), 0, "°C") if
         ex.get("bearing_temp_limit_c") is not None else "the lubricant card",
         "the top of the lubricant's stated range"
         + ("" if not ex.get("bearing_lubricant") else
            " (%s)" % ex["bearing_lubricant"])
         + "; past it the bearing watts are modelled, not qualified"],
        # TWO ROWS, one question each (reviewer 2026-09-14 / PWM study §1.7) —
        # and on a PWM duty the first of them is about the LINK the duty was
        # solved on, not the pack floor (reviewer 2026-09-15).
        (["DC link this duty was solved on",
          "%s … %s" % (_fmt(ex.get("v_pack_min_v"), 1, "V"),
                       _fmt(ex.get("v_pack_max_v"), 1, "V")),
          "the pack's own range; a link above the nominal %s is amber — %s"
          % (_fmt(ex.get("v_pack_nom_v"), 1, "V"), DC_LINK_AMBER_NOTE)]
         if str(ex.get("drive") or "") == "pwm" else
         ["Line voltage, waveform peak", _fmt(ex.get("v_pack_min_v"), 1, "V"),
          "the pack minimum against the solved waveform peak × k_3d — the "
          "INSULATION and device-rating question"]),
        ["Line voltage, fundamental vs linear modulation",
         _fmt(ex.get("v_mod_ceiling_v"), 1, "V"),
         # THE RUN'S OWN CLAMP, where it has one (2026-09-16): the largest
         # fundamental the modulator can APPLY on this link, which is the
         # linear-modulation value less the sampled-reference gain the factory
         # costs at a low pulse ratio.  Without it, the linear limit alone.
         ("the largest fundamental the bridge can build on the DC link this "
          "duty was solved on: %s × the link at modulation index %s, less the "
          "sampled-reference gain the modulator costs at this pulse ratio — "
          "the CONTROL question"
          % (_fmt(MOD_CEILING_OF_VDC, 4), _fmt(MOD_INDEX_LIMIT, 2)))
         if ex.get("mod_ceiling_from_run") else
         ("%s × %s — the CONTROL question: modulation index "
          "m = 2·V1_phase,peak/V_dc must stay at or under %s"
          % (_fmt(MOD_CEILING_OF_VDC, 4),
             ("the DC link this duty was solved on"
              if str(ex.get("drive") or "") == "pwm" else "the pack minimum"),
             _fmt(MOD_INDEX_LIMIT, 2)))],
        ["Bridge line voltage amplitude", "no limit",
         "the line-to-line pulse amplitude = the DC link — what the insulation "
         "sees; no insulation or device voltage rating is stated on this "
         "project, so nothing is checked against it"],
        ["Runaway speed, cold magnets", _fmt(ex.get("max_speed_rpm"), 0, "rpm"),
         "the fastest duty in this configuration; " +
         str(ex.get("runaway_note") or "no cold-magnet card was available, so a "
             "factor of 1.0 was used and the true runaway speed is LOWER")],
        ["Current density", _fmt(_j_rule[0], 1, "A/mm²"),
         J_BAND_TEXT + ("; " + _j_rule[1] if _j_rule[1] else "")],
        ["Torque ripple, low-order", _fmt(RIPPLE_LIMIT_PCT, 1, "%"),
         "this project's own gate — no standard fixes it; judged on the "
         "SINUSOIDAL run, which is what the geometry makes"],
        ["Torque ripple at the carrier", "no limit",
         CARRIER_RIPPLE_NOTE + " — printed on a PWM duty because the solve "
         "produced it, not because anything is checked against it"],
        ["Line voltage THD, low-order", _fmt(THD_LIMIT_PCT, 1, "%"),
         "this report's own reference, judged on the SINUSOIDAL run — the "
         "machine's own back-EMF distortion"],
        ["Line voltage THD at the bridge", "no limit",
         "a two-level bridge's pulse train distorts by tens of per cent "
         "whatever the machine is; the winding inductance filters it, so it is "
         "the CURRENT THD beside it that says what reaches the machine"],
        # Added 2026-09-14 (reviewer): a rotor ring mode on the PWM carrier was
        # in the modal table and in no warning.
        ["Ring mode vs an excitation line",
         "%s (amber), %s (red)" % (_fmt(RING_MODE_AMBER_PCT, 0, "%"),
                                   _fmt(RING_MODE_RED_PCT, 0, "%")),
         "separation from the nearest excitation line, the PWM carrier above "
         "all; inside 2 % the mode sits ON the line"],
        ["Mass across the duties", _fmt(MASS_DUTY_SPREAD_PCT, 1, "%"),
         "a consistency check: one build has one mass, two are a data defect"],
    ] + ([["Duty cycle ED (on-time share)",
           _fmt(ex.get("ed_allowable_pct"), 1, "%"),
           "the share at which the %s peak of the settled cycle sits exactly "
           "on its limit — this cycle's own answer, not a standard"
           % str(ex.get("ed_limiting_part") or "hottest part")
           + _ed_limit_clause(ex)]]
          if _numf(ex.get("ed_allowable_pct")) is not None else [])


def _warnings_page(st, cols: List[Dict[str, Any]],
                   ctxs: Dict[str, Dict[str, Any]],
                   sec: Optional[Dict[str, int]] = None) -> List[Any]:
    """Every duty against every limit, with what to do about it.

    User, 2026-09-09: *"нужно делать предупреждения, если что-то близко к
    пределам, и предложения, как этого избежать"*.
    """
    from reportlab.platypus import KeepTogether, Spacer

    out: List[Any] = [_para(section_heading(sec, "warnings"), st["h1"])]
    ordered = all_duty_warnings(cols, ctxs)
    reds = [w for w in ordered if w["level"] == "red"]
    ambers = [w for w in ordered if w["level"] == "amber"]
    greens = [w for w in ordered if w["level"] == "green"]

    if not ordered:
        out.append(_para(WARNINGS_NONE, st["body"]))
    else:
        out.append(_para(warnings_headline(len(reds), len(ambers), len(greens)),
                         st["body"]))
        rows = [list(WARNINGS_HEAD)]
        for w in ordered:
            # Every cell of the row in the verdict's ink, so the table can be
            # read by colour alone (2026-09-10).
            _ink = WARN_INK.get(w["level"], "#000000")
            cells = [_para('<font color="%s">%s</font>' % (_ink, str(x)),
                           st["cell"]) for x in warning_row(w)]
            cells[1] = _para('<font color="%s">%s</font>' % (_ink, w["duty"]),
                             st["dcell"])
            rows.append(cells)
        out.append(_table(rows, [34, 62, 150, 82, 74, CONTENT_W - 402],
                          header=True, size=8.4))
        out.append(Spacer(1, 6))
        out.append(_para("What to do about each of them", st["h2"]))
        # …about each of the ones that are OVER a limit.  User 2026-09-10:
        # "What to do about each of them — писать тоже только для красных".
        # An amber row already says its own margin in the table; a paragraph of
        # advice for something that is still inside its limit buries the rows
        # that are not.
        for w in [x for x in ordered if x["level"] == "red"]:
            head = ("<font color=\"%s\"><b>%s — %s: %s</b></font>" % (
                WARN_INK.get(w["level"], WARN),
                w["duty"], w["quantity"], warning_sentence(w)))
            # ONE LINE + ONE REMEDY (user 2026-09-14).  The rule's own basis
            # used to follow as a third paragraph; it is in the limits table at
            # the foot of this section and does not need saying twice.
            blk = [_para("• " + head, st["body"]),
                   _para("&nbsp;&nbsp;&nbsp;" + w["remedy"], st["body"])]
            blk.append(Spacer(1, 3))
            out.append(KeepTogether(blk))

    out.append(Spacer(1, 6))
    out.append(_para("The rules, and where each limit comes from", st["h2"]))
    ex: Dict[str, Any] = limit_rules_context(ctxs)
    lim_rows = limit_rules_rows(ex)
    # The Limit column carries "80 % (red), 90 % (amber)" and the Quantity
    # column "Worst magnet element, Br retained": both need room to wrap
    # (reviewer 2026-09-14, BL-1).
    out.append(_table([[r[0], r[1], _para(str(r[2]), st["cell"])]
                       for r in lim_rows],
                      [152, 96, CONTENT_W - 248], header=True, size=8.4))
    out.append(_para(WARNINGS_RULES_NOTE, st["note"]))
    return out


def sources_per_duty_rows(cols: List[Dict[str, Any]],
                          live_fp: Optional[str],
                          report_fp: Optional[str] = None) -> List[List[str]]:
    """Which stored result each column came from — plain rows, header included.

    A one-row answer (the header alone) means nothing has been solved for any
    duty of this configuration; both renderers print the sentence instead.

    ``report_fp`` is THIS CONFIGURATION's own geometry fingerprint and is what
    the "Machine" column judges against (BL-1, audit v6); ``live_fp`` — the
    machine the server happens to have loaded — is only the fallback for a
    configuration with no stored print of its own.
    """
    ref = report_fp or live_fp
    rows = [["Duty", "Simulation", "Computed at", "Store", "Machine"]]
    label = {"thermal": "Thermal map", "coupled": "Coupled loop",
             "rotor_stress": "Rotor stress",
             "critical_speeds": "Rotor critical speeds",
             "modes": "Stator ring modes"}
    for c in cols:
        res = c.get("res") or {}
        if c.get("em"):
            rows.append([c["duty"], "Electromagnetic",
                         _local_stamp((c.get("result") or {}).get("recorded_at")
                                      or c["d"].get("saved_at"), "—"),
                         "the duty's own saved summary", "this configuration"])
        for k in ("thermal", "coupled", "rotor_stress", "critical_speeds", "modes"):
            e = res.get(k)
            if not isinstance(e, dict):
                continue
            fp = e.get("geometry_fingerprint")
            if fp and ref and ref != "nofp":
                machine = ("this machine" if str(fp) == str(ref)
                           else "ANOTHER machine " + FLAG)
            else:
                machine = "unverified"
            rows.append([c["duty"], label.get(k, k), _stamp(e),
                         str((c.get("origin") or {}).get(k) or "the per-duty store"),
                         machine])
    return rows


