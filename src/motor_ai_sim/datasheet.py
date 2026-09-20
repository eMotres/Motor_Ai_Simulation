"""Motor datasheet export — one .xlsx a stranger can read and understand.

Opens natively in Google Sheets (upload -> Open with Sheets), Excel and
LibreOffice.  The layout follows the motor card the company already uses:
one column per duty point (continuous, peak, ...), then the design block,
then the battery block.

Everything comes from what is already stored — the die, the configuration and
the duties saved from the Simulation tab (their on-screen summary included).
Nothing is solved here, so an export costs no FEM time and can never disagree
with the numbers the user saved.

Sheets:
    Motor card   duty columns, design block, battery block
    Details      mass breakdown, loss terms, solver settings per duty
    Notes        what each number means and how it was obtained
"""
from __future__ import annotations

import io
import logging
import math
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

_TITLE = "1F4E79"     # dark blue   — section bars
_DUTY = "D9E9F7"      # light blue  — first duty column
_PEAK = "FDEADA"      # light beige — further duty columns
_GREY = "F2F2F2"
_NOTE = "808080"

# The supplier's stranded silicone lead range (mirror of web/src/lib/cableTable.ts):
# (copper section mm2, name, insulation O.D. mm)
_CABLES = [
    (0.055, "30 AWG", 0.8), (0.08, "28 AWG", 1.2), (0.15, "26 AWG", 1.5),
    (0.2, "24 AWG", 1.6), (0.3, "22 AWG", 1.7), (0.5, "20 AWG", 1.8),
    (0.75, "18 AWG", 2.3), (1.05, "17 AWG", 2.7), (1.27, "16 AWG", 3.0),
    (1.5, "15 AWG", 3.2), (2.0, "14 AWG", 3.5), (2.5, "13 AWG", 4.0),
    (3.4, "12 AWG", 4.5), (3.8, "11 AWG", 5.0), (5.3, "10 AWG", 5.5),
    (6.0, "9 AWG", 5.8), (8.3, "8 AWG", 6.3), (10.0, "7 AWG", 7.2),
    (16.0, "6 AWG", 8.5), (20.0, "5 AWG", 9.5), (25.0, "4 AWG", 11.5),
    (35.0, "2 AWG", 13.0), (50.0, "1/0 AWG", 14.0), (70.0, "2/0 AWG", 15.5),
    (95.5, "4/0 AWG", 18.0),
]


def _cable_for(area_mm2: float) -> Optional[Dict[str, Any]]:
    """Smallest catalogue lead whose copper section is at or above the phase
    section — the user's rule, never a size below what the phase needs."""
    for area, name, od in _CABLES:
        if area >= float(area_mm2) - 1e-9:
            return {"name": name, "area_mm2": area, "od_mm": od}
    return None


def _num(v: Any, d: int = 3) -> Any:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        f = float(v)
        if f != f or f in (float("inf"), float("-inf")):
            return None
        return round(f, d)
    return v


def _g(duty: Dict[str, Any], *paths: str, default: Any = None) -> Any:
    """First present value along dotted paths like 'summary.T_em_avg_Nm'."""
    for p in paths:
        node: Any = duty
        for part in p.split("."):
            if not isinstance(node, dict):
                node = None
                break
            node = node.get(part)
        if node is not None:
            return node
    return default


def _setting(duty: Dict[str, Any], key: str) -> Any:
    """The panel settings a duty was saved with are a FLAT map with dotted
    keys ('sim.stepsPP', 'mesh.meshSize'), so they need a direct lookup."""
    m = duty.get("mesh")
    return m.get(key) if isinstance(m, dict) else None


def _k_end_geom(geo: Dict[str, Any]) -> Optional[float]:
    """End-winding factor the SOLVER derives from this cross-section:
    k_end = (pi*(wire_width + tooth_width)/2 + L) / L  (masses.end_winding_factor).

    Used when a duty did not record the value it ran with.  A stored override
    is deliberately NOT trusted here: one number was found copied across a
    40 / 160 / 220 mm family, where the true factor runs 1.72 / 1.18 / 1.13."""
    L = float(geo.get("motor_length") or 0)
    if L <= 0:
        return None
    ww = float(geo.get("wire_width") or 0)
    tw = float(geo.get("tooth_width") or 0)
    return (math.pi * (ww / 2 + tw / 2) + L) / L


def _magnet_temp(grade: str) -> Optional[float]:
    m = re.search(r"(\d{2,3})\s*C\b", str(grade or ""))
    return float(m.group(1)) if m else None


# ── drawing ─────────────────────────────────────────────────────────────────
#
# The die stores its cross-section as an SVG of flat paths (M/L/Z with a few
# curves and arcs) coloured by part.  Re-drawing it here — rather than shipping
# a screenshot — keeps the picture in the datasheet the same shape the solver
# actually meshed.

def _svg_subpaths(d: str):
    """SVG path data -> list of point lists.  Curves and arcs are sampled;
    a datasheet picture does not need analytic fidelity, only the outline."""
    toks = re.findall(r"[MmLlHhVvCcQqAaZz]|-?\d*\.?\d+(?:[eE][-+]?\d+)?", d)
    subs: List[List] = []
    cur: List = []
    x = y = sx = sy = 0.0
    i, cmd = 0, ""
    def _f() -> float:
        nonlocal i
        v = float(toks[i]); i += 1
        return v
    while i < len(toks):
        t = toks[i]
        if t.isalpha():
            cmd = t
            i += 1
            if cmd in "Zz":
                if cur:
                    cur.append((sx, sy))
                    subs.append(cur)
                    cur = []
                x, y = sx, sy
                continue
        rel = cmd.islower()
        c = cmd.upper()
        if c == "M":
            x, y = (x + _f(), y + _f()) if rel else (_f(), _f())
            if cur:
                subs.append(cur)
            cur = [(x, y)]
            sx, sy = x, y
            cmd = "l" if rel else "L"          # implicit lineto after moveto
        elif c == "L":
            x, y = (x + _f(), y + _f()) if rel else (_f(), _f())
            cur.append((x, y))
        elif c == "H":
            x = x + _f() if rel else _f()
            cur.append((x, y))
        elif c == "V":
            y = y + _f() if rel else _f()
            cur.append((x, y))
        elif c in ("C", "Q"):
            n = 3 if c == "C" else 2
            pts = []
            for _ in range(n):
                px, py = _f(), _f()
                pts.append((x + px, y + py) if rel else (px, py))
            p0 = (x, y)
            for k in range(1, 13):
                s = k / 12.0
                if c == "C":
                    a, b, cc = pts
                    bx = ((1 - s) ** 3 * p0[0] + 3 * (1 - s) ** 2 * s * a[0]
                          + 3 * (1 - s) * s * s * b[0] + s ** 3 * cc[0])
                    by = ((1 - s) ** 3 * p0[1] + 3 * (1 - s) ** 2 * s * a[1]
                          + 3 * (1 - s) * s * s * b[1] + s ** 3 * cc[1])
                else:
                    a, cc = pts
                    bx = (1 - s) ** 2 * p0[0] + 2 * (1 - s) * s * a[0] + s * s * cc[0]
                    by = (1 - s) ** 2 * p0[1] + 2 * (1 - s) * s * a[1] + s * s * cc[1]
                cur.append((bx, by))
            x, y = pts[-1]
        elif c == "A":
            rx, ry, _rot, _laf, _swp = _f(), _f(), _f(), _f(), _f()
            ex, ey = _f(), _f()
            if rel:
                ex, ey = x + ex, y + ey
            # a datasheet thumbnail can take the chord of a fillet-sized arc
            steps = 8 if (rx or ry) else 1
            for k in range(1, steps + 1):
                cur.append((x + (ex - x) * k / steps, y + (ey - y) * k / steps))
            x, y = ex, ey
        else:                                   # unknown command — skip a number
            i += 1
    if cur:
        subs.append(cur)
    return subs


def _render_cross_section(svg: str, px: int = 900) -> Optional[bytes]:
    """The die's cross-section as a PNG, part colours preserved."""
    if not svg or "<path" not in svg:
        return None
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import PathPatch
        from matplotlib.path import Path as MPath
    except Exception:                                          # noqa: BLE001
        return None
    vb = re.search(r'viewBox="([-\d.\s]+)"', svg)
    x0, y0, w, h = ([float(v) for v in vb.group(1).split()] if vb else [0, 0, 120, 120])
    fig, ax = plt.subplots(figsize=(px / 150, px / 150), dpi=150)
    n = 0
    for tag in re.findall(r"<path\b[^>]*>", svg):
        d = re.search(r'\bd="([^"]+)"', tag)
        if not d:
            continue
        fill = re.search(r'\bfill="([^"]*)"', tag)
        stroke = re.search(r'\bstroke="([^"]*)"', tag)
        colour = fill.group(1) if fill else "#8A94A6"
        if colour in ("none", ""):
            continue
        # one matplotlib path per SVG path so subpaths keep acting as holes
        verts, codes = [], []
        for pts in _svg_subpaths(d.group(1)):
            if len(pts) < 3:
                continue
            verts.append((pts[0][0], -pts[0][1]))
            codes.append(MPath.MOVETO)
            for p in pts[1:]:
                verts.append((p[0], -p[1]))
                codes.append(MPath.LINETO)
            verts.append((pts[0][0], -pts[0][1]))
            codes.append(MPath.CLOSEPOLY)
        if not verts:
            continue
        ax.add_patch(PathPatch(MPath(verts, codes), facecolor=colour,
                               edgecolor=(stroke.group(1) if stroke else "none"),
                               linewidth=0.4 if stroke else 0, zorder=2))
        n += 1
    if not n:
        plt.close(fig)
        return None
    ax.set_xlim(x0, x0 + w)
    ax.set_ylim(-(y0 + h), -y0)
    ax.set_aspect("equal")
    ax.axis("off")
    fig.tight_layout(pad=0.1)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", transparent=True)
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


# NO PICTURES IN THE DATASHEET (2026-09-09).  User, verbatim: *"в datasheet
# ставить только таблицы без картинок; а report всё нужно делать с картинками и
# гораздо подробнее всё расписывать"*.  The two documents were given different
# jobs: the spreadsheet is the machine's NUMBERS — one column per duty, sortable,
# pasteable into a quotation, diffable against the next revision — and the PDF
# report is where the maps, the plots and the reasoning live.
#
# So the cross-section thumbnail and the three matplotlib charts (torque vs
# current, the loss surface, the boost-charge curve) that used to be embedded
# through ``openpyxl.drawing.image.Image`` are gone.  Every one of them sat
# ABOVE a table of the same data, so nothing was lost: the numbers those
# pictures were drawn from are still on the sheet, row by row, and a reader who
# wants the picture opens the report.
#
# ``_render_cross_section`` above stays — ``motor_ai_sim.report`` imports it for
# the report's machine page, where a picture is the point.


# ── live calculator ─────────────────────────────────────────────────────────
#
# The card states the machine as it was solved.  This sheet lets the reader ASK
# a different question — more turns, a longer stack, thicker wire, another
# current or speed — and answers it with real spreadsheet formulas over the
# measured data, exactly the model the Configure tab uses:
#
#   torque   read off the measured T(I) curve in AMPERE-TURNS, × stack × k3D
#   R        (active ∝ stack + fixed end winding) × turns / wire height × paths²
#   EMF      ∝ turns × stack × rpm × series count × k3D
#   losses   bilinear over the measured I × rpm surface, × stack
#   copper   I²R plus the measured proximity watts (field-driven, ∝ h³ × stack)
#
# Nothing here is a curve fit invented for the export: every table below the
# calculator is FEM output, and the formulas only interpolate between them.

def _interp_formula(x: str, xs0: str, ys0: str, n: int) -> str:
    """Linear interpolation of ys at x over the sorted horizontal range xs.
    Beyond the ends FORECAST continues the end segment — the same behaviour as
    the app's own interpolator, so the sheet and the app never disagree."""
    xs_rng = f"{xs0}:{_shift(xs0, n - 1)}"
    idx = (f"MAX(0,MIN({n - 2},IFERROR(MATCH({x},{xs_rng},1),1)-1))")
    return (f"FORECAST({x},OFFSET({ys0},0,{idx},1,2),OFFSET({xs0},0,{idx},1,2))")


def _shift(cell: str, cols: int) -> str:
    """'$B$69' + 3 -> '$E$69' (absolute refs, horizontal move)."""
    m = re.match(r"\$([A-Z]+)\$(\d+)", cell)
    if not m:
        return cell
    from openpyxl.utils import column_index_from_string, get_column_letter
    return f"${get_column_letter(column_index_from_string(m.group(1)) + cols)}${m.group(2)}"


def _calc_mech_terms(bearings: Optional[Dict[str, Any]], geo: Dict[str, Any],
                     rpm0: float) -> Optional[Dict[str, Any]]:
    """Turn this machine's bearings into two LIVE Excel formulas of the speed
    cell, or None when it has none.

    The bearing side is nearly exact: ``M_seal`` and ``M_sl`` do not depend on
    speed and ``M_rr`` goes as ``(n.nu)^0.6``, so only the two SKF correction
    factors (Phi_ish, Phi_rs) are frozen — at the base speed, and they fall with
    speed, so the sheet reads slightly HIGH efficiency far above it, never low.
    The windage side has a regime change in it, so it is written as the local
    power law measured on the model at 0.7x and 1.4x the base speed, with the
    exponent printed in the note. Both notes say what they are.
    """
    if not bearings:
        return None
    try:
        from motor_ai_sim import bearings as brg
        if not brg.has_bearings(bearings):
            return None
        res = brg.resolve_assignment(bearings)
        n0 = max(float(rpm0), 1.0)
        t_c = res["temp_c"] if res["temp_c"] is not None else 70.0
        consts: List[tuple] = []
        names: List[str] = []
        for end in ("A", "B"):
            spec = res[end]
            if not spec.get("card"):
                continue
            card = brg.get_bearing(str(spec["card"]))
            f = brg.friction(card, rpm=n0, f_r_n=0.0, f_a_n=res["preload_n"],
                             temp_c=t_c, lubrication=res["lubrication"],
                             lubricant=spec.get("grease"))
            nu = float(f["nu_mm2_s"])
            # G_eff = Phi_ish.Phi_rs.G_rr, backed out at the base speed.
            g_eff = (float(f["M_rr_Nm"]) * 1e3) / ((n0 * nu) ** 0.6)
            m_const = (float(f["M_seal_Nm"]) + float(f["M_sl_Nm"])) * 1e3
            consts.append((m_const, g_eff, nu))
            names.append(card.name)
        if not consts:
            return None

        def bearing_formula(rpm_cell: str) -> str:
            terms = " + ".join(
                f"({m:.6g}+{g:.6g}*({rpm_cell}*{nu:.6g})^0.6)"
                for m, g, nu in consts)
            return f"({terms})/1000*2*PI()*{rpm_cell}/60"

        w0 = brg.windage_from_geometry(geo, n0, temp_c=t_c)
        if w0 and float(w0["P_W"]) > 0:
            lo = brg.windage_from_geometry(geo, 0.7 * n0, temp_c=t_c)
            hi = brg.windage_from_geometry(geo, 1.4 * n0, temp_c=t_c)
            p_lo, p_hi = float(lo["P_W"]), float(hi["P_W"])
            k = (math.log(p_hi / p_lo) / math.log(2.0)) if p_lo > 0 else 2.0
            p0 = float(w0["P_W"])
            regime = str(w0.get("gap_regime") or "")

            def windage_formula(rpm_cell: str) -> str:
                return f"{p0:.6g}*({rpm_cell}/{n0:g})^{k:.4f}"

            w_note = (f"air drag on the rotor (gap Couette + two end faces), "
                      f"{p0:.2f} W at {n0:,.0f} rpm rising as n^{k:.2f} — the "
                      f"local power law of the model around the base speed "
                      f"({regime}). ANALYTIC, not FEM")
        else:
            def windage_formula(rpm_cell: str) -> str:  # noqa: ARG001
                return "0"
            w_note = ("the geometry does not carry the rotor OD, air gap and "
                      "stack length windage needs")

        return {
            "bearing_formula": bearing_formula,
            "bearing_note": (
                f"{' + '.join(names)} on "
                f"{res['lubrication'].replace('_', '-')}, SKF frictional-moment "
                f"model: seal + sliding (speed-independent) + rolling (n^0.6). "
                f"The two SKF correction factors are frozen at {n0:,.0f} rpm and "
                f"they FALL with speed, so this reads slightly optimistic far "
                f"above it. ANALYTIC, not FEM"),
            "windage_formula": windage_formula,
            "windage_note": w_note,
        }
    except Exception:                            # noqa: BLE001 — never break an export
        return None


def _build_calculator(wb, *, pp: Dict[str, Any], geo: Dict[str, Any],
                      wind: Dict[str, Any], duties: List[Dict[str, Any]],
                      gamma0: Optional[float] = None,
                      slot: Optional[Dict[str, float]] = None,
                      bearings: Optional[Dict[str, Any]] = None) -> None:
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    ws = wb.create_sheet("Calculator")
    thin = Side(style="thin", color="D0D0D0")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    right = Alignment(horizontal="right")
    ws.column_dimensions["A"].width = 36
    ws.column_dimensions["B"].width = 14
    ws.column_dimensions["C"].width = 11
    ws.column_dimensions["D"].width = 62
    for i in range(5, 16):
        ws.column_dimensions[get_column_letter(i)].width = 11

    IN = "FFF2CC"      # editable cells — the only ones a reader should touch
    OUT = "E2EFDA"

    def sec(r: int, text: str, span: int = 4) -> int:
        c = ws.cell(row=r, column=1, value=text)
        c.font = Font(bold=True, size=11, color="FFFFFF")
        for i in range(1, span + 1):
            ws.cell(row=r, column=i).fill = PatternFill("solid", fgColor=_TITLE)
        return r + 1

    def line(r: int, label: str, value: Any, unit: str = "", note: str = "",
             fill: Optional[str] = None, fmt: Optional[str] = None) -> int:
        ws.cell(row=r, column=1, value=label).border = border
        c = ws.cell(row=r, column=2, value=value)
        c.alignment = right
        c.border = border
        if fill:
            c.fill = PatternFill("solid", fgColor=fill)
            c.font = Font(bold=True)
        if fmt:
            c.number_format = fmt
        ws.cell(row=r, column=3, value=unit).border = border
        n = ws.cell(row=r, column=4, value=note)
        n.font = Font(size=9, color=_NOTE, italic=True)
        n.alignment = Alignment(wrap_text=True, vertical="center")
        return r + 1

    # base machine ----------------------------------------------------------
    N0 = float(pp.get("N0") or 1)
    L0 = float(pp.get("L0_mm") or 1)
    h0 = float(pp.get("wireH0_mm") or 1)
    nP0 = float(pp.get("nP0") or 1)
    I0 = float(pp.get("I0_A") or 1)
    rpm0 = float(pp.get("rpm0") or 1)
    T0 = abs(float(pp.get("T0_Nm") or 0))
    Vemf0 = float(pp.get("Vemf0_peak_V") or 0)
    Vload0 = float(pp.get("Vload0_peak_V") or 0)
    R0 = float(pp.get("R0_ohm") or 0)
    ewf = float(pp.get("endWindFrac") or 0)
    mass0 = float(pp.get("mass0_kg") or 0)
    poles = int(geo.get("num_poles") or 0)
    w_wire = float(geo.get("wire_width") or 0)
    # Conductors carrying the phase current in parallel: the connection's paths
    # TIMES the strands in hand (k wires wound together per turn each carry
    # I_coil/k).  The current-density formula below divides by exactly this.
    # `wire_split` is NOT in it: a row's strips are consecutive SERIES turns and
    # each carries the branch current whole.
    wp0 = max(1.0, float(pp.get("wire_parallel0") or 1))
    strands = float(wind.get("n_parallel") or 1) * wp0
    # Strips per wire row the passport was measured at (absent = 1: it predates
    # the split).  N0 and the "Turns per slot" input below are wire ROWS, so the
    # ELECTRICAL turn count is (N/wp0)*ws0 — the same ratio N/N0 the factors use,
    # because wp0 and ws0 are fixed by the build and cancel; recorded here so the
    # base section can state the turns the tables were actually measured on.
    ws0 = max(1.0, float(pp.get("wire_split0") or 1))
    turns0 = (N0 / wp0) * ws0
    ldq = pp.get("ldq0") or {}
    cur = pp.get("current") or {}
    lg = pp.get("loss_grid") or {}
    kvL = (pp.get("end3d") or {}).get("k_flux_vs_L") or {}
    T_ref = float(_g(duties[0], "summary.coil_temp_C") or 120) if duties else 120.0

    ws.cell(row=1, column=1, value="Recalculate this motor").font = \
        Font(bold=True, size=13, color=_TITLE)
    ws.cell(row=2, column=1, value=(
        "Change the yellow cells and every green result updates. The formulas "
        "read the FEM tables at the bottom of this sheet — the same model the "
        "web tuner uses — so this is interpolation of measured points, not a "
        "curve fit. Far from the measured range, run a real solve instead."
    )).font = Font(size=9, color=_NOTE, italic=True)

    r = sec(4, "INPUTS — edit the yellow cells")
    R_I, R_RPM, R_N, R_H, R_NP, R_L, R_T = r, r + 1, r + 2, r + 3, r + 4, r + 5, r + 6
    R_K3ON = r + 7
    # default: whatever the saved duties used, so the calculator reproduces the
    # card before anything is touched
    k3_default = 1 if any(_g(d, "result.end3d_k") for d in duties) else 0
    r = line(R_I, "Phase current", I0, "A rms",
             "rms current per phase", IN, "0.0")
    r = line(R_RPM, "Speed", rpm0, "rpm", "", IN, "0")
    r = line(R_N, "Wire rows per slot", N0, "",
             "wire width is fixed by the slot — only the row count moves. "
             + (f"This build winds {wp0:g} in hand and splits each row into "
                f"{ws0:g} series strips, so the coil is {turns0:g} turns; both "
                f"are fixed by the build, so the factors below scale on the row "
                f"ratio."
                if (wp0 > 1 or ws0 > 1) else
                "One wire in hand, one strip per row, so rows = turns."), IN, "0")
    r = line(R_H, "Wire height", h0, "mm",
             "conductor thickness: area ∝ height, so resistance ∝ 1/height", IN, "0.000")
    r = line(R_NP, "Parallel paths", nP0, "",
             "how the coil groups are wired: 4S = 1, 2P·2S = 2, 4P = 4. "
             "Pure voltage↔current trade, the magnetics do not change", IN, "0")
    r = line(R_L, "Stack length", L0, "mm", "active lamination length", IN, "0.0")
    r = line(R_T, "Coil temperature", T_ref, "°C",
             "copper resistance is corrected from the solved value at "
             f"{T_ref:g} °C (0.393 %/K)", IN, "0")
    r = line(R_K3ON, "Apply 3-D correction (1 = yes, 0 = no)", k3_default, "",
             "1 multiplies torque, back-EMF and KV by the measured end-effect "
             "factor. The default matches how the duties on the motor card were "
             f"recorded ({'with' if k3_default else 'without'} it), so this sheet "
             "starts out agreeing with the card", IN, "0")
    r += 1

    # The results are written first because that is what a reader wants at the
    # top, but their formulas point at the factor and data blocks below them —
    # so those blocks get FIXED rows reserved here, before anything is written.
    res_start = r
    R_FN, R_FL, R_FH, R_FI, R_FC, R_IEQ, R_K3, R_FE, R_COV = (
        res_start + 32, res_start + 33, res_start + 34, res_start + 35,
        res_start + 36, res_start + 37, res_start + 38, res_start + 39,
        res_start + 40)
    B = lambda row: f"$B${row}"                                   # noqa: E731

    # data block rows (filled further down; referenced by the formulas above it)
    n_cur = len(cur.get("I_A") or [])
    n_rpm = len(lg.get("rpm") or [])
    n_gI = len(lg.get("I_A") or [])
    n_kl = len(kvL)
    # between the factors and the data block sit the base point and the solved
    # duties; their height is known here, so the data block can be placed exactly
    _mid = (5 + (1 if gamma0 is not None else 0) + (1 if pp.get("mode0") else 0)
            + (4 + len(duties) if duties else 0))
    D0 = R_COV + 4 + _mid                      # first row of the data block
    ROW_CI, ROW_CT, ROW_CV, ROW_CD = D0 + 2, D0 + 3, D0 + 4, D0 + 5
    ROW_GR = D0 + 8                            # rpm header of the loss surface
    ROW_FE = ROW_GR + 1                        # iron rows
    ROW_MG = ROW_FE + n_gI
    ROW_AC = ROW_MG + n_gI
    ROW_PX = ROW_AC + n_gI                     # proximity watts (derived)
    ROW_HI = ROW_PX + n_gI + 1                 # per-current values at the input rpm
    ROW_GI = ROW_HI + 4                        # the grid's current axis
    ROW_KL = ROW_GI + 3                        # k_flux vs L
    ROW_KK = ROW_KL + 1

    # ── results ────────────────────────────────────────────────────────────
    r = sec(r, "RESULT")
    T_f = (f"={_interp_formula(B(R_IEQ), f'$B${ROW_CI}', f'$B${ROW_CT}', n_cur)}"
           f"*{B(R_FL)}*{B(R_FE)}") if n_cur >= 3 else \
         f"={T0}*{B(R_FN)}*{B(R_FL)}*{B(R_FI)}*{B(R_FC)}*{B(R_FE)}"
    R_T_ROW = r
    r = line(r, "Torque", T_f, "N·m",
             "measured T(I) read at the same ampere-turns, then scaled by stack "
             "length and the 3-D factor", OUT, "0.00")
    R_P = r
    r = line(r, "Mechanical power", f"={B(R_T_ROW)}*2*PI()*{B(R_RPM)}/60/1000", "kW",
             "torque × angular speed", OUT, "0.000")
    R_R = r
    r = line(r, "Phase resistance",
             f"=((1-{ewf})*{B(R_FL)}+{ewf})*{R0}*{B(R_FN)}/{B(R_FH)}"
             f"*{B(R_FC)}^2*(1+0.00393*({B(R_T)}-20))/(1+0.00393*({T_ref}-20))",
             "Ω", "active copper follows the stack, the end winding does not", OUT, "0.0000")
    R_CUDC = r
    r = line(r, "Copper loss, I²R", f"=3*{B(R_I)}^2*{B(R_R)}", "W", "", OUT, "0.0")
    R_PX_OUT = r
    if n_gI and lg.get("cuAC"):
        prox = _interp_formula(B(R_IEQ), f"$B${ROW_GI}", f"$B${ROW_HI + 2}", n_gI)
        r = line(r, "Copper loss, proximity",
                 f"=MAX(0,{prox})*{B(R_FN)}*{B(R_FH)}^3*{B(R_FL)}", "W",
                 "driven by the rotating magnet field, not by the phase current: "
                 "∝ conductors × height³ × stack", OUT, "0.0")
    else:
        r = line(r, "Copper loss, proximity", 0, "W", "not measured for this build", OUT)
    R_FEW = r
    fe_f = (f"=MAX(0,{_interp_formula(B(R_IEQ), f'$B${ROW_GI}', f'$B${ROW_HI}', n_gI)})"
            f"*{B(R_FL)}") if n_gI else f"={pp.get('Pfe0_W') or 0}*{B(R_FL)}"
    r = line(r, "Iron loss", fe_f, "W",
             "bilinear over the measured current × speed surface", OUT, "0.0")
    R_MGW = r
    mg_f = (f"=MAX(0,{_interp_formula(B(R_IEQ), f'$B${ROW_GI}', f'$B${ROW_HI + 1}', n_gI)})"
            f"*{B(R_FL)}") if n_gI else f"={pp.get('Pmag0_W') or 0}*{B(R_FL)}"
    r = line(r, "Magnet and solid loss", mg_f, "W", "", OUT, "0.0")
    R_LOSS = r
    r = line(r, "Total loss",
             f"={B(R_CUDC)}+{B(R_PX_OUT)}+{B(R_FEW)}+{B(R_MGW)}", "W", "", OUT, "0.0")
    R_EFF = r
    _mech = _calc_mech_terms(bearings, geo, rpm0)
    r = line(r, "Efficiency",
             f"=IF({B(R_P)}<=0,0,100*{B(R_P)}*1000/({B(R_P)}*1000+{B(R_LOSS)}))", "%",
             ("electromagnetic — the mechanical losses are in the three rows below"
              if _mech else "bearings and windage are not included"), OUT, "0.00")
    if _mech:
        # The SKF model as a LIVE formula of the speed cell.  M_seal and M_sl do
        # not depend on speed at all, and M_rr goes exactly as (n.nu)^0.6, so
        # the only thing frozen here is the pair of correction factors
        # Phi_ish.Phi_rs, folded into G at the base speed — they move slowly and
        # in the SAFE direction (both fall with speed, so a faster reading is
        # slightly conservative).  Far from the base speed, ask the app.
        R_BRG = r
        r = line(r, "Bearing loss", "=" + _mech["bearing_formula"](B(R_RPM)), "W",
                 _mech["bearing_note"], OUT, "0.0")
        R_WND = r
        r = line(r, "Windage", "=" + _mech["windage_formula"](B(R_RPM)), "W",
                 _mech["windage_note"], OUT, "0.00")
        R_LOSSM = r
        r = line(r, "Total loss incl. mechanical",
                 f"={B(R_LOSS)}+{B(R_BRG)}+{B(R_WND)}", "W",
                 "copper, iron, magnets — plus the bearings and the air", OUT, "0.0")
        r = line(r, "Shaft efficiency",
                 f"=IF({B(R_P)}<=0,0,100*MAX(0,{B(R_P)}*1000-{B(R_BRG)}-{B(R_WND)})"
                 f"/({B(R_P)}*1000+{B(R_LOSS)}))", "%",
                 "what a dynamometer on the shaft reads: the row above with the "
                 "bearing and windage losses taken off the output", OUT, "0.00")
    R_EMF = r
    r = line(r, "Back-EMF, line peak",
             f"={Vemf0}*SQRT(3)*{B(R_FN)}*{B(R_FL)}*{B(R_RPM)}/{rpm0}*{B(R_FC)}*{B(R_FE)}",
             "V", "no load — what the machine generates when spun", OUT, "0.0")
    R_VLL = r
    drop = (f"{max(0.0, Vload0 - Vemf0)}*{B(R_FI)}*{B(R_RPM)}/{rpm0}*{B(R_FN)}^2"
            f"*{B(R_FL)}*{B(R_FC)}^2") if Vload0 > Vemf0 > 0 else \
           f"{B(R_R)}*{B(R_I)}*SQRT(2)"
    _vnote = ("back-EMF plus the load drop — the DC bus must exceed it. The drop "
              "is added as a magnitude, so at a leading current angle (generators, "
              "field weakening) the real terminal voltage comes out lower: treat "
              "this as an upper estimate and confirm the point in the simulator")
    r = line(r, "Terminal voltage, line peak",
             f"=({Vemf0}*{B(R_FN)}*{B(R_FL)}*{B(R_RPM)}/{rpm0}*{B(R_FC)}*{B(R_FE)}"
             f"+{drop})*SQRT(3)", "V",
             _vnote, OUT, "0.0")
    R_KV = r
    r = line(r, "KV (no load)",
             f"=IF({B(R_EMF)}<=0,0,{B(R_RPM)}/{B(R_EMF)})", "rpm/V",
             "rpm per volt of line-peak back-EMF", OUT, "0.0")
    r = line(r, "Torque constant Kt",
             f"=IF({B(R_I)}<=0,0,{B(R_T_ROW)}/{B(R_I)})", "N·m/A rms", "", OUT, "0.0000")
    r = line(r, "Motor constant Km",
             f"=IF({B(R_CUDC)}<=0,0,{B(R_T_ROW)}/SQRT({B(R_CUDC)}))", "N·m/√W",
             "torque per square root of copper loss — quoted on the DC part, "
             "so it stays a property of the design", OUT, "0.000")
    R_MASS = r
    r = line(r, "Active mass", f"={mass0}*{B(R_FL)}", "kg", "", OUT, "0.000")
    r = line(r, "Power per mass",
             f"=IF({B(R_MASS)}<=0,0,{B(R_P)}/{B(R_MASS)})", "kW/kg", "", OUT, "0.00")
    r = line(r, "Torque per mass",
             f"=IF({B(R_MASS)}<=0,0,{B(R_T_ROW)}/{B(R_MASS)})", "N·m/kg", "", OUT, "0.00")
    if w_wire > 0:
        r = line(r, "Current density",
                 f"={I0}/({w_wire}*{h0}*{strands})*{B(R_FI)}*{B(R_FC)}/{B(R_FH)}",
                 "A/mm²", "rms current per mm² of conductor in one path", OUT, "0.00")
    _slot_src = slot or {"A_slot_mm2": pp.get("A_slot_mm2"),
                         "A_cu_mm2": pp.get("A_cu0_mm2")}
    if _slot_src.get("A_slot_mm2") and _slot_src.get("A_cu_mm2"):
        r = line(r, "Wire coating",
                 f"=100*{float(_slot_src['A_cu_mm2'])}*{B(R_FN)}*{B(R_FH)}"
                 f"/{float(_slot_src['A_slot_mm2'])}", "%",
                 "copper in the slot grows with turns and wire height, the "
                 "window does not — above ~75 % the winding stops being "
                 "buildable whatever the electromagnetics say", OUT, "0.0")
    if ldq.get("Ld_mH"):
        r = line(r, "Ld",
                 f"={ldq['Ld_mH']}*{B(R_FN)}^2*{B(R_FL)}*{B(R_FC)}^2", "mH",
                 "small-signal, I≈0 — scales with turns², stack and series count²",
                 OUT, "0.0000")
    if ldq.get("Lq_mH"):
        r = line(r, "Lq", f"={ldq['Lq_mH']}*{B(R_FN)}^2*{B(R_FL)}*{B(R_FC)}^2", "mH",
                 "", OUT, "0.0000")
    if poles:
        r = line(r, "Electrical frequency",
                 f"={B(R_RPM)}/60*{poles / 2}", "Hz", "", OUT, "0.0")
        r = line(r, "Magnet flux linkage ψ_PM",
                 f"=IF({B(R_RPM)}<=0,0,{B(R_EMF)}/SQRT(3)/(2*PI()*{B(R_RPM)}/60*{poles / 2})*1000)",
                 "mWb", "from the back-EMF fundamental", OUT, "0.000")
    if n_cur >= 3 and (cur.get("demag_keep_pct") or []):
        r = line(r, "Magnet retention",
                 f"=MIN(100,{_interp_formula(B(R_IEQ), f'$B${ROW_CI}', f'$B${ROW_CD}', n_cur)})",
                 "%", "measured magnet flux that survives this current — below "
                 "~99.5 % the loss is permanent", OUT, "0.00")
        slope = ((abs(float(cur["T_Nm"][0])) / float(cur["I_A"][0]))
                 if float(cur["I_A"][0]) else 0)
        if slope:
            r = line(r, "Saturation",
                     f"=MIN(100,100*{_interp_formula(B(R_IEQ), f'$B${ROW_CI}', f'$B${ROW_CT}', n_cur)}"
                     f"/({slope}*{B(R_IEQ)}))", "%",
                     "measured torque over what a non-saturating machine would "
                     "give — 100 % means the iron is still linear", OUT, "0.0")
    r += 1

    # ── the factors, written where the results already point ───────────────
    sec(R_FN - 1, "SCALING FACTORS — how the inputs map onto the measured machine")
    line(R_FN, "Turns factor", f"={B(R_N)}/{N0}", "",
         "electrical turns over the measured winding's — the strands in hand "
         "and the strips per row are fixed by the build, so they cancel and "
         "this is the row ratio", None, "0.0000")
    line(R_FL, "Stack factor", f"={B(R_L)}/{L0}", "", "", None, "0.0000")
    line(R_FH, "Wire-height factor", f"={B(R_H)}/{h0}", "", "", None, "0.0000")
    line(R_FI, "Current factor", f"={B(R_I)}/{I0}", "", "", None, "0.0000")
    line(R_FC, "Series-count factor", f"={nP0}/{B(R_NP)}", "",
         "series groups per phase relative to the measured winding", None, "0.0000")
    line(R_IEQ, "Ampere-turn equivalent current",
         f"={B(R_FN)}*{B(R_FI)}*{B(R_FC)}*{I0}", "A",
         "the current that would put the MEASURED winding in the same iron "
         "state — this is what the torque and loss tables are read at",
         None, "0.00")
    if n_kl >= 2:
        kl_lo, kl_hi = min(kvL.values()), max(kvL.values())
        line(R_K3, "3-D factor k(L)",
             f"=MIN({kl_hi},MAX({kl_lo},"
             f"{_interp_formula(B(R_L), f'$B${ROW_KL}', f'$B${ROW_KK}', n_kl)}))", "",
             "measured flux that survives the ends of the stack; clamped to the "
             "measured range — beyond it the end effect is unknown", None, "0.0000")
        line(R_FE, "Flux factor applied",
             f"=IF({B(R_K3ON)}=1,{B(R_K3)},1)", "",
             "torque, back-EMF and KV above are multiplied by it — 1 when the "
             "3-D switch in the inputs is off", None, "0.0000")
    else:
        k1 = float((pp.get("end3d") or {}).get("k_flux") or 1.0)
        line(R_K3, "3-D factor k(L)", k1, "", "single measured point", None, "0.0000")
        line(R_FE, "Flux factor applied", f"=IF({B(R_K3ON)}=1,{B(R_K3)},1)", "",
             "", None, "0.0000")

    # Interpolation inside the measured tables is trustworthy; outside them the
    # formulas continue the last segment in a straight line, and iron and magnet
    # losses do not grow in a straight line.  Say so, on the row itself.
    if n_rpm and n_gI:
        rpm_lo, rpm_hi = min(lg["rpm"]), max(lg["rpm"])
        I_lo, I_hi = min(lg["I_A"]), max(lg["I_A"])
        line(R_COV, "Inside the measured range?",
             f'=IF(AND({B(R_RPM)}>={rpm_lo},{B(R_RPM)}<={rpm_hi},'
             f'{B(R_IEQ)}>={I_lo},{B(R_IEQ)}<={I_hi}),"yes",'
             f'"NO — losses are extrapolated beyond the measured surface '
             f'({rpm_lo:g}-{rpm_hi:g} rpm, {I_lo:g}-{I_hi:g} A); they grow '
             f'faster than the straight line used here, so treat them as a '
             f'lower bound")', "",
             "the loss numbers are only measured inside this box", None)

    # ── what this calculator is anchored to, and what it cannot move ───────
    r = sec(R_COV + 2, "MEASURED BASE — the point every formula is anchored to")
    r = line(r, "Base current", I0, "A rms", "", None, "0.00")
    r = line(r, "Base speed", rpm0, "rpm", "", None, "0")
    r = line(r, "Base torque", T0, "N·m",
             "raw 2-D value of the base solve, before the 3-D factor", None, "0.000")
    if gamma0 is not None:
        r = line(r, "Base current angle γ", gamma0, "°",
                 "FIXED. γ is not an input here: the measured tables were taken "
                 "at this angle, and a different one changes torque and voltage "
                 "in a way this sheet cannot see", None, "0.0")
    r = line(r, "Base rows / stack / wire height",
             f"{N0:g} / {L0:g} mm / {h0:g} mm", "",
             "the winding the tables belong to"
             + (f" — {turns0:g} series turns per coil at {wp0:g} in hand, "
                f"{ws0:g} strips per row" if (wp0 > 1 or ws0 > 1) else ""))
    if pp.get("mode0"):
        r = line(r, "Measured as", str(pp["mode0"]), "",
                 "the convention the tables were solved in — a generator and a "
                 "motor at the same current sit at different operating points, "
                 "so the two are not interchangeable")

    # The saved duties, so the reader can drop them into the inputs and see for
    # themselves how far the rescaling sits from a full solve.
    if duties:
        r += 1
        r = sec(r, "SOLVED DUTIES — put these inputs in above to compare", span=8)
        hdr = ["Duty", "Current, A", "Speed, rpm", "γ, °", "Torque, N·m",
               "Power, kW", "Efficiency, %", "Loss, W"]
        for i, h in enumerate(hdr):
            c = ws.cell(row=r, column=1 + i, value=h)
            c.font = Font(bold=True, size=10)
            c.fill = PatternFill("solid", fgColor=_DUTY)
            c.border = border
        r += 1
        for d_ in duties:
            vals = [str(d_.get("name") or ""),
                    _num(_g(d_, "current_arms"), 2), _num(_g(d_, "rpm"), 0),
                    _num(_g(d_, "gamma_deg"), 1),
                    _num(abs(float(_g(d_, "torque_nm") or 0)) or None, 3),
                    _num(_g(d_, "power_kw"), 3),
                    _num(_g(d_, "result.efficiency_pct"), 2),
                    _num(_g(d_, "result.loss_w"), 1)]
            for i, v in enumerate(vals):
                c = ws.cell(row=r, column=1 + i, value=v)
                c.border = border
                if i:
                    c.alignment = right
            r += 1
        c = ws.cell(row=r, column=1, value=(
            "These rows are full FEM solves. Set the inputs to one of them and the "
            "calculator should land within a few percent — the gap is the price of "
            "rescaling instead of solving, and it grows with the distance from the "
            "base point above. A duty at a different γ will disagree more, because "
            "γ is fixed here."))
        c.font = Font(size=9, color=_NOTE, italic=True)

    # ── measured data block ────────────────────────────────────────────────
    def hrow(row: int, label: str, values, d: int = 3, bold: bool = False) -> None:
        c = ws.cell(row=row, column=1, value=label)
        c.font = Font(bold=bold, size=10)
        c.border = border
        for i, v in enumerate(values):
            cell = ws.cell(row=row, column=2 + i, value=_num(abs(float(v)), d))
            cell.alignment = right
            cell.border = border
            if bold:
                cell.font = Font(bold=True)
                cell.fill = PatternFill("solid", fgColor=_DUTY)

    sec(D0, "MEASURED FEM DATA — the formulas above read these tables", span=10)
    ws.cell(row=D0 + 1, column=1,
            value="Torque and demagnetisation vs current (at the measured winding)")\
        .font = Font(bold=True, size=10)
    if n_cur:
        hrow(ROW_CI, "Phase current, A rms", cur["I_A"], 2, bold=True)
        hrow(ROW_CT, "Torque, N·m", cur["T_Nm"], 3)
        hrow(ROW_CV, "Line voltage peak, V", cur.get("V_peak_V") or [], 2)
        hrow(ROW_CD, "Magnet retention, %", cur.get("demag_keep_pct") or [], 3)

    if n_gI and n_rpm:
        ws.cell(row=ROW_GR - 1, column=1, value="Loss surface: current × speed")\
            .font = Font(bold=True, size=10)
        hrow(ROW_GR, "rpm →", lg["rpm"], 0, bold=True)
        for i, I in enumerate(lg["I_A"]):
            hrow(ROW_FE + i, f"iron loss @ {I:g} A, W", lg["Pfe_W"][i], 2)
            hrow(ROW_MG + i, f"magnet loss @ {I:g} A, W", lg["Pmag_W"][i], 2)
            if lg.get("cuAC"):
                hrow(ROW_AC + i, f"AC/DC copper @ {I:g} A", lg["cuAC"][i], 4)
                # proximity watts = DC I²R × (factor − 1), reconstructed here so
                # the reader can see where the number comes from
                for j in range(n_rpm):
                    src = f"{get_column_letter(2 + j)}{ROW_AC + i}"
                    cell = ws.cell(row=ROW_PX + i, column=2 + j,
                                   value=f"=MAX(0,3*{I}^2*{R0}*({src}-1))")
                    cell.alignment = right
                    cell.border = border
                    cell.number_format = "0.0"
                c = ws.cell(row=ROW_PX + i, column=1,
                            value=f"proximity watts @ {I:g} A")
                c.font = Font(size=10)
                c.border = border

        ws.cell(row=ROW_HI - 1, column=1,
                value="Interpolated at the input speed (helper)").font = \
            Font(bold=True, size=10)
        for j, (lbl, base) in enumerate((("iron, W", ROW_FE), ("magnets, W", ROW_MG),
                                         ("proximity, W", ROW_PX))):
            c = ws.cell(row=ROW_HI + j, column=1, value=lbl)
            c.font = Font(size=10)
            c.border = border
            for i in range(n_gI):
                f = _interp_formula(B(R_RPM), f"$B${ROW_GR}",
                                    f"${get_column_letter(2)}${base + i}", n_rpm)
                cell = ws.cell(row=ROW_HI + j, column=2 + i, value=f"={f}")
                cell.alignment = right
                cell.border = border
                cell.number_format = "0.0"
        hrow(ROW_GI, "current axis of the surface, A", lg["I_A"], 2, bold=True)

    if n_kl:
        ws.cell(row=ROW_KL - 1, column=1, value="3-D end effect vs stack length")\
            .font = Font(bold=True, size=10)
        pts = sorted((float(a), float(b)) for a, b in kvL.items())
        hrow(ROW_KL, "stack length, mm", [p[0] for p in pts], 2, bold=True)
        hrow(ROW_KK, "k_flux", [p[1] for p in pts], 4)

    ws.cell(row=ROW_KK + 2, column=1, value=(
        "Validity: this is a first-order rescaling around the measured point. "
        "Turns, stack, wire and connection are exact to first order; the "
        "saturation and demagnetisation curves are measured, so moderate "
        "current changes are covered too. What it cannot see: a changed "
        "cross-section, a different steel or magnet, and thermal limits. "
        "Once a variant looks right here, solve it properly in the simulator."
    )).font = Font(size=9, color=_NOTE, italic=True)


def build_datasheet(*, die: str, cfg: str, die_doc: Dict[str, Any],
                    cfg_doc: Dict[str, Any],
                    passport: Optional[Dict[str, Any]] = None,
                    slot: Optional[Dict[str, float]] = None,
                    coupled: Optional[Dict[str, Any]] = None) -> bytes:
    """``coupled`` is ``{duty name: its stored coupled record}`` — the only
    thing in this card that does not come out of the two yaml documents.

    It is here for ONE block: the catalogue constants at 20 °C (owner
    2026-09-18).  KV, Kt, Km and Km/kg are what a buyer compares two machines
    on, and every other number on this card is at the duty's own temperatures —
    right, and not comparable.  ``None`` (or a duty whose loop never made the
    cold pass) simply leaves those four lines out: this card states measured
    numbers, and a room-temperature Kt extrapolated from a hot one is not one.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    geo = dict(die_doc.get("geometry") or {})
    geo.update(cfg_doc.get("geometry_overrides") or {})
    wind = cfg_doc.get("winding") or {}
    mats = cfg_doc.get("materials") or {}
    batt = cfg_doc.get("battery") or {}
    role = str(cfg_doc.get("role") or "motor")
    duties: List[Dict[str, Any]] = [d for d in (cfg_doc.get("duties") or [])
                                    if isinstance(d, dict)]
    # continuous-looking duties first, peak last — the order of the paper card
    duties.sort(key=lambda d: ("peak" in str(d.get("name", "")).lower(),
                               str(d.get("name", ""))))
    ncol = max(1, len(duties))

    wb = Workbook()
    ws = wb.active
    ws.title = "Motor card"
    thin = Side(style="thin", color="D0D0D0")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    right = Alignment(horizontal="right")

    ws.column_dimensions["A"].width = 38
    for i in range(2, 2 + ncol):
        ws.column_dimensions[get_column_letter(i)].width = 17
    ws.column_dimensions[get_column_letter(2 + ncol)].width = 54

    state = {"row": 4}

    def section(text: str) -> None:
        r = state["row"]
        c = ws.cell(row=r, column=1, value=text)
        c.font = Font(bold=True, size=11, color="FFFFFF")
        for col in range(1, 2 + ncol + 1):
            ws.cell(row=r, column=col).fill = PatternFill("solid", fgColor=_TITLE)
        state["row"] = r + 1

    def row(label: str, values: List[Any], note: str = "", d: int = 2,
            bold: bool = False, flat: bool = False) -> None:
        """One line of the card.  `flat` = a property of the machine itself,
        identical for every duty, so it is written once in a grey band."""
        r = state["row"]
        lc = ws.cell(row=r, column=1, value=label)
        lc.font = Font(bold=bold)
        lc.border = border
        for i in range(ncol):
            v = values[0] if flat else (values[i] if i < len(values) else None)
            cell = ws.cell(row=r, column=2 + i, value=_num(v, d) if (i == 0 or not flat) else None)
            cell.alignment = right
            cell.border = border
            cell.font = Font(bold=bold)
            cell.fill = PatternFill(
                "solid", fgColor=_GREY if flat else (_DUTY if i == 0 else _PEAK))
        n = ws.cell(row=r, column=2 + ncol, value=note)
        n.font = Font(size=9, color=_NOTE, italic=True)
        n.alignment = Alignment(wrap_text=True, vertical="center")
        state["row"] = r + 1

    def one(label: str, value: Any, note: str = "", d: int = 2) -> None:
        row(label, [value], note, d, flat=True)

    def blank() -> None:
        state["row"] += 1

    # ── THE MECHANICAL HALF OF THE LOSS PICTURE ─────────────────────────────
    # Until 2026-09-08 this sheet said, in three places, that "bearing and
    # windage losses belong to the assembled machine and are not included".  On
    # the real 150 mm free run those two were 43-170 W out of 83-319 W measured
    # — the largest single term below 3000 rpm.  When the machine names its
    # bearings (``cfg_doc['bearings']``, written by PATCH
    # /api/family/config/{die}/{cfg}/bearings) they are computed here from the
    # SKF frictional-moment model and reported; when it does not, the old note
    # stands, because an unknown loss must not be printed as zero.
    _brg_assign = cfg_doc.get("bearings") or {}
    try:
        from motor_ai_sim import bearings as _brg_mod
        _has_brg = bool(_brg_mod.has_bearings(_brg_assign))
    except Exception:                            # noqa: BLE001 — no library, no rows
        _brg_mod, _has_brg = None, False

    _mech_cache: Dict[int, Optional[Dict[str, Any]]] = {}

    def _rotating_mass_kg(d: Dict[str, Any]) -> float:
        """Rotor iron + magnets + shaft + sleeve, off the duty's own mass rows.

        A REFERENCE part (a customer-supplied shaft) carries mass 0 in the card
        but it still spins and still loads the bearings, so its modelled mass is
        used.  0.0 when the duty carries no mass rows — the bearing torque then
        rests on the preload and the seals, and M_rr goes as F_r^0.54, so the
        error is small and it is stated rather than guessed.
        """
        tot = 0.0
        for c in (_g(d, "summary.mass_components") or []):
            if not isinstance(c, dict):
                continue
            nm = str(c.get("name") or "")
            if not nm.startswith(("Rotor back-iron", "Magnets", "Shaft", "Sleeve")):
                continue
            v = c.get("mass_kg")
            if not v and c.get("mass_modelled_kg"):
                v = c.get("mass_modelled_kg")
            try:
                tot += float(v or 0.0)
            except (TypeError, ValueError):
                pass
        return tot

    def _mech(d: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Bearing + windage loss at THIS duty's speed, or None.

        THE DUTY'S OWN STORED RUN FIRST (2026-09-08): since every run of a
        machine with bearings carries its mechanical block, a duty saved from
        such a run already HAS the answer — at the bearing temperature that run
        was billed at, which a recomputation from the assignment cannot
        reproduce once a coupled run has converged on a shaft temperature.  The
        recomputation stays the fallback for duties saved before this, and for a
        duty whose speed is not the stored run's.
        """
        if not _has_brg:
            return None
        key = id(d)
        if key in _mech_cache:
            return _mech_cache[key]
        out = None
        try:
            rpm = float(_g(d, "rpm") or 0.0)
            if rpm > 0:
                from motor_ai_sim import mech_losses as _ml
                stored = _ml.from_summary(d.get("summary"))
                if stored and abs(float(stored.get("rpm") or 0.0) - rpm) <= 0.5:
                    out = stored
                else:
                    t_c = _brg_assign.get("temp_c")
                    out = _brg_mod.machine_bearing_losses(
                        _brg_assign, rpm=rpm,
                        temp_c=float(t_c) if t_c is not None else 70.0,
                        rotor_mass_kg=_rotating_mass_kg(d), geometry=geo)
        except Exception:                        # noqa: BLE001 — a bad card is not a broken export
            out = None
        _mech_cache[key] = out
        return out

    def _p_bearings(d: Dict[str, Any]) -> Optional[float]:
        m = _mech(d)
        return None if not m else float(m.get("P_bearings_W") or 0.0)

    def _p_windage(d: Dict[str, Any]) -> Optional[float]:
        m = _mech(d)
        return None if not m else float(m.get("P_windage_W") or 0.0)

    def _p_extra(d: Dict[str, Any]) -> float:
        m = _mech(d)
        return 0.0 if not m else float(m.get("P_mech_extra_W") or 0.0)

    def _eta_shaft(d: Dict[str, Any]) -> Optional[float]:
        """Efficiency AT THE SHAFT: the electromagnetic one with the mechanical
        losses put where they physically sit — between the rotor and the coupling.

        Motoring they come off the output, generating they go onto the input,
        so the same two numbers give ``eta·(1 - x)`` and ``eta/(1 + x)`` with
        ``x = P_mech_extra / P_mech``.  Deliberately DERIVED from the row above
        rather than recomputed from a loss sum: the two must never disagree.
        """
        if not _has_brg:
            return None
        eta = _g(d, "result.efficiency_pct")
        p_mech = _g(d, "summary.P_mech_W")
        if p_mech is None:
            t, n = _g(d, "torque_nm", "summary.T_em_avg_Nm"), _g(d, "rpm")
            p_mech = (abs(float(t)) * 2 * math.pi * float(n) / 60.0
                      if (t and n) else None)
        if eta is None or not p_mech:
            return None
        x = _p_extra(d) / abs(float(p_mech))
        gen = str(d.get("mode") or role).lower().startswith("gen")
        k = (1.0 / (1.0 + x)) if gen else max(0.0, 1.0 - x)
        return float(eta) * k

    # ── title ───────────────────────────────────────────────────────────────
    t = ws.cell(row=1, column=1, value=f"{die} · {cfg}")
    t.font = Font(bold=True, size=16, color=_TITLE)
    ws.cell(row=2, column=1, value=(
        f"{role.capitalize()} · {float(geo.get('stator_diameter') or 0):g} mm outer "
        f"diameter, {float(geo.get('motor_length') or 0):g} mm stack, "
        f"{geo.get('num_slots')} slots / {geo.get('num_poles')} poles, "
        f"{mats.get('magnet') or 'PM'} magnets · datasheet generated "
        f"{datetime.now():%Y-%m-%d} from a finite-element characterisation of this "
        f"exact cross-section, winding and material set"
    )).font = Font(size=9, color=_NOTE, italic=True)
    ws.cell(row=3, column=1, value=(
        "Tables only — the cross-section, the field maps and the measured "
        "curves are in the PDF report of the same configuration."
    )).font = Font(size=9, color=_NOTE, italic=True)

    # ── operating points ────────────────────────────────────────────────────
    section("OPERATING POINTS")
    row("Duty", [str(d.get("name") or "") for d in duties],
        "the named points this machine was characterised at", 0, bold=True)
    row("Mode", [str(d.get("mode") or role) for d in duties],
        "motor = drives the shaft, generator = driven by it", 0)

    def _pkw(d: Dict[str, Any]) -> Optional[float]:
        v = _g(d, "power_kw")
        if v is not None:
            return float(v)
        w = _g(d, "summary.P_mech_W")
        return abs(float(w)) / 1000.0 if w is not None else None

    P_kw = [_pkw(d) for d in duties]
    T_nm = [_g(d, "torque_nm", "summary.T_em_avg_Nm") for d in duties]
    mass = [_g(d, "result.mass_kg", "summary.mass_total_kg") for d in duties]

    row("Power (kW)", P_kw, "shaft power at this point", 2, bold=True)
    row("Torque (N·m)", T_nm,
        "average electromagnetic torque over one electrical period", 2, bold=True)
    row("Speed (rpm)", [_g(d, "rpm") for d in duties], "", 0, bold=True)
    poles = int(geo.get("num_poles") or 0)
    row("Electrical frequency (Hz)",
        [(float(_g(d, "rpm") or 0) / 60.0 * poles / 2) if poles else None for d in duties],
        "speed × pole pairs — what the controller and the iron see", 1)
    row("Efficiency (%)", [_g(d, "result.efficiency_pct") for d in duties],
        "electromagnetic: rotor power / (rotor power + the copper, iron and "
        "magnet losses below)"
        + (" — the mechanical losses are in the row beneath" if _has_brg else
           "; bearing and windage losses are NOT included"), 2, bold=True)
    if _has_brg:
        row("Shaft efficiency (%)", [_eta_shaft(d) for d in duties],
            "the same number with the BEARING and WINDAGE losses put where they "
            "sit — between the rotor and the coupling. This is what a dynamometer "
            "on the shaft reads. Analytic (SKF frictional-moment model + windage), "
            "not FEM", 2, bold=True)
    row("Minimum bus voltage (V)",
        [_g(d, "result.v_ll_peak_v", "summary.V_line_peak_V") for d in duties],
        "line-to-line peak of the solved waveform — the DC bus must stay above it", 1)
    row("Phase current (A rms)",
        [_g(d, "current_arms", "summary.I_phase_rms_A") for d in duties], "", 1, bold=True)
    row("Phase current (A peak)",
        [(float(_g(d, "current_arms", "summary.I_phase_rms_A") or 0) * math.sqrt(2)) or None
         for d in duties], "what the controller has to switch", 1)
    row("Current density (A/mm²)",
        [_g(d, "result.j_coil_a_mm2", "summary.J_coil_A_per_mm2") for d in duties],
        "rms current per mm² of copper — sets the coil temperature rise for a "
        "given cooling", 1)
    row("Load angle γ (°)", [_g(d, "gamma_deg") for d in duties],
        "current vector relative to the q-axis", 1)
    row("Torque ripple (%)",
        [_g(d, "result.ripple_pct", "summary.T_ripple_pct") for d in duties],
        "peak-to-peak over mean, cogging included", 1)
    # ── three rows that were only ever visible in a picture (2026-09-09) ─────
    # With the charts gone, the numbers they carried become rows of their own:
    # the terminal distortion, how much remanence the run left in the magnets,
    # and the temperature the magnets were solved at.  All three are already on
    # the stored summary — no new physics, no new solve.
    if any(_g(d, "summary.THD_pct", "summary.THD_LL_pct") for d in duties):
        row("Voltage THD (%)",
            [_g(d, "summary.THD_pct", "summary.THD_LL_pct") for d in duties],
            "total harmonic distortion of the solved line-to-line waveform — "
            "what the inverter's current controller has to track", 2)
    if any(_g(d, "summary.demag.br_kept_vol_pct") for d in duties):
        row("Magnet Br kept (%)",
            [_g(d, "summary.demag.br_kept_vol_pct") for d in duties],
            "volume-weighted remanence left in the magnets after this point — "
            "below 100 % the loss is IRREVERSIBLE and the machine does not "
            "recover it by cooling down", 3)
        row("Worst magnet element Br (%)",
            [_g(d, "summary.demag.br_worst_pct") for d in duties],
            "the single most demagnetised element — where a knee crossing "
            "starts, usually a pole corner facing the slot opening", 1)
    if any(_setting(d, "sim.magnetTempC") is not None for d in duties):
        row("Magnet temperature (°C)",
            [_setting(d, "sim.magnetTempC") for d in duties],
            "the temperature the magnets were SOLVED at — Br and the knee field "
            "both ride on it, so a duty solved cold is not the same machine as "
            "the same duty solved hot", 0)
    row("Power per mass (kW/kg)",
        [(P_kw[i] / float(mass[i])) if (P_kw[i] and mass[i]) else None
         for i in range(len(duties))], "active material only", 2)
    row("Torque per mass (N·m/kg)",
        [(abs(float(T_nm[i])) / float(mass[i])) if (T_nm[i] and mass[i]) else None
         for i in range(len(duties))], "active material only", 2)
    def _kt(i: int) -> Optional[float]:
        v = _g(duties[i], "summary.Kt_Nm_per_Arms")
        if v is not None:
            return float(v)
        cur = _g(duties[i], "current_arms")
        return abs(float(T_nm[i])) / float(cur) if (T_nm[i] and cur) else None

    row("Torque constant Kt (N·m/A rms)", [_kt(i) for i in range(len(duties))],
        "torque per ampere at this point — it falls as the iron saturates", 4)

    def _S_kva(d: Dict[str, Any]) -> Optional[float]:
        v = _g(d, "summary.V_phase_rms_V")
        i = _g(d, "current_arms", "summary.I_phase_rms_A")
        return 3 * float(v) * float(i) / 1000.0 if (v and i) else None

    row("Apparent power (kVA)", [_S_kva(d) for d in duties],
        "3 × phase volts × phase amps — what the inverter must be rated for", 2)
    row("Power factor",
        [(abs(float(_g(d, "summary.P_elec_in_solved_W") or 0)) / (_S_kva(d) * 1000.0))
         if _S_kva(d) else None for d in duties],
        "electrical power over apparent power; a low value means the inverter "
        "carries current that does no work", 3)
    if batt.get("v_nom"):
        row(f"DC current at {batt['v_nom']:g} V pack (A)",
            [(abs(float(_g(d, "summary.P_elec_in_solved_W") or 0)) / float(batt["v_nom"]))
             or None for d in duties],
            "average bus current at the nominal pack voltage — sizes the cabling "
            "and the fuse", 1)
    row("Motor constant Km (N·m/√W)",
        [(abs(float(T_nm[i])) / math.sqrt(float(cu)))
         if (T_nm[i] and (cu := _g(duties[i], "result.p_stranded_w",
                                   "summary.P_stranded_W"))) else None
         for i in range(len(duties))],
        "torque per square root of copper loss — the winding-independent "
        "quality of the magnetic design; higher is better", 3)
    if any(_g(d, "summary.Ld_mH") for d in duties):
        row("Ld at load (mH)", [_g(d, "summary.Ld_mH") for d in duties],
            "chord inductance at THIS duty's iron state — not the same as the "
            "bench value in the design block below", 4)
        row("Lq at load (mH)", [_g(d, "summary.Lq_mH") for d in duties], "", 4)
    blank()

    section("LOSSES")
    row("Total loss (W)",
        [_g(d, "result.loss_w", "summary.P_loss_total_W") for d in duties], "", 1, bold=True)
    row("Copper (W)",
        [_g(d, "result.p_stranded_w", "summary.P_stranded_W") for d in duties],
        "I²R at the coil temperature plus proximity loss from the rotating "
        "magnet field", 1)
    row("Iron (W)", [_g(d, "result.p_core_w", "summary.P_core_W") for d in duties],
        "hysteresis + eddy + excess, from the measured P(B,f) surface of the steel", 1)
    row("Magnets and solid parts (W)",
        [_g(d, "result.p_solid_w", "summary.P_solid_W") for d in duties],
        "solved eddy currents in magnets, rotor housing, shaft and — where one "
        "is fitted — the carbon-fibre retaining sleeve", 1)
    if any(_g(d, "summary.P_sleeve_W") for d in duties):
        row("  of which sleeve (W)",
            [_g(d, "summary.P_sleeve_W") for d in duties],
            "eddy loss solved in the carbon-fibre retaining ring. Carbon fibre "
            "conducts ~80 S/m ACROSS the fibres, which is the direction the "
            "axial induced current has to take in a hoop-wound sleeve — hence "
            "milliwatts, not watts", 4)
    # ── the two numbers a cooling design is sized on ────────────────────────
    # Where the watts above have to LEAVE FROM.  Stator = stator iron + all
    # copper; rotor = rotor iron + magnet, shaft and sleeve eddy.  They sum to
    # the total loss by construction.  A duty solved before the split existed
    # has neither key: the row then falls back to "all the iron on the stator"
    # and the note says the split was assumed, never crashing on the gap.
    def _heat(d: Dict[str, Any], side: str) -> Optional[float]:
        v = _g(d, f"summary.P_loss_{side}_W")
        if v is not None:
            return float(v)
        cu = _g(d, "result.p_stranded_w", "summary.P_stranded_W")
        fe = _g(d, "result.p_core_w", "summary.P_core_W")
        sd = _g(d, "result.p_solid_w", "summary.P_solid_W")
        if side == "stator":
            return (float(cu or 0.0) + float(fe or 0.0)) if (cu or fe) else None
        return float(sd) if sd is not None else None
    _split_assumed = any(_g(d, "summary.P_loss_stator_W") is None
                         or _g(d, "summary.P_loss_split_measured") is False
                         for d in duties)
    _split_note = (" The iron split was NOT measured on this run — the whole "
                   "core loss is billed to the stator; regenerate the duty to "
                   "get the solved split." if _split_assumed else "")
    row("Stator heat to remove (W)", [_heat(d, "stator") for d in duties],
        "stator iron loss plus all the copper loss — what the housing, the "
        "jacket or the fan has to carry away" + _split_note, 1, bold=True)
    row("Rotor heat to remove (W)", [_heat(d, "rotor") for d in duties],
        "rotor back-iron, magnet, shaft and sleeve eddy loss — it can only "
        "leave across the air gap, through the shaft, or by windage"
        + _split_note, 1, bold=True)
    row("Coil temperature (°C)", [_g(d, "summary.coil_temp_C") for d in duties],
        "the temperature the copper resistance is quoted at", 0)
    # ── MECHANICAL, when the machine says which bearings it has ─────────────
    if _has_brg:
        _cards = [str((( _brg_assign.get(e) or {}).get("card") or ""))
                  for e in ("A", "B")]
        _cards = [c for c in _cards if c]
        _lub = str(_brg_assign.get("lubrication") or "grease").replace("_", "-")
        row("Bearing loss (W)", [_p_bearings(d) for d in duties],
            f"both bearings ({' + '.join(_cards) or 'assigned pair'}, {_lub}) by "
            "the SKF frictional-moment model: rolling + sliding + seal drag, at "
            "the rotor's own weight plus the stated preload. ANALYTIC, not FEM. "
            "Unbalanced magnetic pull, coupling side loads and any shaft seal are "
            "NOT included", 1)
        row("Windage (W)", [_p_windage(d) for d in duties],
            "air drag on the rotor: gap Couette friction plus the two end faces "
            "as rotating discs, at the mechanical clearance (stator bore minus "
            "the rotor OD including any sleeve). ANALYTIC, not FEM", 2)
        row("Total losses incl. mechanical (W)",
            [(None if _g(d, "result.loss_w", "summary.P_loss_total_W") is None
              else float(_g(d, "result.loss_w", "summary.P_loss_total_W"))
              + _p_extra(d)) for d in duties],
            "everything above: copper, iron, magnets and solid parts, bearings "
            "and windage — the whole loss picture of the assembled machine",
            1, bold=True)
    blank()

    # ── design ──────────────────────────────────────────────────────────────
    section("DESIGN")
    one("Outer diameter (mm)", geo.get("stator_diameter"),
        "stator outer diameter — the die fixes it for the whole family", 1)
    one("Active length (mm)", geo.get("motor_length"),
        "lamination stack length; the configuration is free to change it", 1)
    one("Number of poles", geo.get("num_poles"), "", 0)
    one("Number of slots", geo.get("num_slots"), "", 0)
    one("Air gap (mm)", geo.get("air_gap"), "radial, single sided", 3)
    if float(geo.get("sleeve_thickness") or 0.0) > 0.0:
        one("Retaining sleeve (mm)", geo.get("sleeve_thickness"),
            "carbon-fibre ring on the rotor OD, inside the air gap. The "
            "MECHANICAL gap is the air gap above minus this thickness; the "
            "MAGNETIC gap is the air gap, because carbon fibre is non-magnetic",
            3)
    one("Magnet height (mm)", geo.get("magnet_height"), "", 2)
    one("Active material mass (kg)", next((m for m in mass if m), None),
        "iron + copper + magnets + shaft over the active length", 3)
    one("Cooling", cfg_doc.get("cooling") or die_doc.get("cooling") or "air",
        "the losses above are electromagnetic — the cooling has to remove them")
    blank()

    from motor_ai_sim.winding import (wire_parallel_from_geo as _wp_geo,
                                      wire_split_from_geo as _ws_geo,
                                      turns_per_coil as _tpc)
    try:
        _wp = _wp_geo(geo)
        _wsp = _ws_geo(geo)
        _turns = _tpc(geo)
    except ValueError:      # a machine whose strands do not divide its wires
        _wp, _wsp, _turns = 1, 1, geo.get("num_wires_per_slot")
    one("Wire rows per slot", geo.get("num_wires_per_slot"),
        "physical wire rows in the slot — the wire coating and the copper mass "
        "are built on this", 0)
    if _wp > 1:
        one("Wires in hand (wire_parallel)", _wp,
            "strands wound together as one turn: the turn count, the EMF and "
            "Kt divide by it, the phase resistance by its square", 0)
    if _wsp > 1:
        one("Strips per row (wire_split)", _wsp,
            "the row is this many strips of wire_width laid side by side and "
            "wired in SERIES, so each strip is a turn: the turn count, the EMF "
            "and Kt multiply by it, the phase resistance by its square", 0)
    one("Series turns per coil", _turns,
        "(wire rows ÷ wires in hand) × strips per row — what the EMF, Kt and R "
        "are built on", 0)
    one("Parallel paths (connection)", wind.get("n_parallel") or 1, "", 0)
    one("Conductors in parallel per phase",
        int(float(wind.get("n_parallel") or 1)) * int(_wp),
        "paths × strands in hand — what the phase current divides over", 0)
    one("Wire height (mm)", geo.get("wire_height"), "", 3)
    one("Wire width (mm)", geo.get("wire_width"), "rectangular magnet wire", 3)
    a_phase = (float(geo.get("wire_width") or 0) * float(geo.get("wire_height") or 0)
               * float(wind.get("n_parallel") or 1) * float(_wp))
    one("Phase cross section (mm²)", a_phase,
        "the copper section the phase current flows through", 2)
    cab = _cable_for(a_phase) if a_phase > 0 else None
    if cab:
        one("Lead cable size", cab["name"],
            "smallest stranded silicone lead whose copper section is at or above "
            "the phase section")
        one("Lead cable copper section (mm²)", cab["area_mm2"],
            "the catalogue value — always ≥ the phase section above", 2)
        one("Lead cable outer diameter (mm)", cab["od_mm"],
            "over the insulation (±0.1 mm) — what the terminal gland has to pass", 1)
    if slot and slot.get("A_slot_mm2"):
        one("Wire coating (%)", 100.0 * float(slot["fill"]),
            f"conductor area {slot['A_cu_mm2']:.0f} mm² over the "
            f"{slot['A_slot_mm2']:.0f} mm² winding window the teeth leave — both "
            "measured on the cross-section, not assumed. Hand-wound rectangular "
            "wire reaches ~45-60 %; the rest is insulation, spacing and the room "
            "the winder needs", 1)
        one("Winding window (mm²)", slot["A_slot_mm2"],
            "free area between the teeth, whole machine", 1)
    one("Connection", wind.get("connection"),
        "series/parallel arrangement of the coil groups")
    # TERMINAL connection — a property of the winding like the label above.
    # A delta machine's R and L below are its WINDING values; the equivalent
    # star's per-phase values (one third) are what most datasheets quote, so
    # both are given and each is named.
    _sd = str(wind.get("star_delta")
              or next((_g(d, "summary.star_delta") for d in duties
                       if _g(d, "summary.star_delta")), "") or "star").lower()
    _delta = _sd.startswith("d")
    one("Terminal connection", "delta (Δ)" if _delta else "star (Y)",
        ("windings between the lines: V_line = V_winding, I_line = √3·I_winding"
         if _delta else
         "isolated neutral: V_line = √3·V_phase, I_line = I_phase"))
    kv = next((_g(d, "result.kv_rpm_per_v", "summary.KV_noload_rpm_per_V_line")
               for d in duties
               if _g(d, "result.kv_rpm_per_v", "summary.KV_noload_rpm_per_V_line")), None)
    one("Motor constant KV (rpm/V)", kv,
        "no-load: rpm per volt of line-peak back-EMF", 1)
    # Kt beside KV — from the first duty carrying both T and I (same source
    # rule as KV above).  T/I_rms at the duty point: saturation included, so a
    # low-current bench measurement reads slightly above it.
    _ktd = next((d for d in duties
                 if _g(d, "summary.T_em_avg_Nm")
                 and float(_g(d, "summary.I_phase_rms_A") or 0) > 0), None)
    if _ktd is not None:
        one("Torque constant Kt (N·m/A rms)",
            float(_g(_ktd, "summary.T_em_avg_Nm"))
            / float(_g(_ktd, "summary.I_phase_rms_A")),
            "measured T/I at the duty point; saturation included — the "
            "low-current (bench) value sits slightly above", 4)
    # ── THE SAME FOUR, AT 20 °C (owner 2026-09-18) ──────────────────────────
    # *«для каждого отчёта делать прогон на холодную 20 °C, чтобы находить все
    # коэффициенты KV, Kt, Km, Km/mass, которые фигурируют во всех каталогах
    # моторов и нужны для сравнения»*.  Beside the hot ones, named, from the
    # coupled loop's own cold pass — and absent entirely when no duty has one,
    # because this card does not extrapolate.
    _c20 = None
    for _d in duties:
        _rec = (coupled or {}).get(str(_d.get("name") or ""))
        _blk = (_rec or {}).get("constants_20c") if isinstance(_rec, dict) else None
        if isinstance(_blk, dict) and _blk:
            _c20 = _blk
            break
    if _c20:
        _tail20 = ("solved with the winding and the magnets at 20 °C — the "
                   "datasheet convention every catalogue uses, for comparison "
                   "between machines; the values above are at this duty's own "
                   "temperatures")
        one("KV at 20 °C (rpm/V)", _c20.get("kv_line_rpm_per_V"),
            "no load, per line volt; " + _tail20, 1)
        one("Kt at 20 °C (N·m/A rms)", _c20.get("kt_line_Nm_per_A"),
            "per line amp; " + _tail20, 4)
        one("Km at 20 °C (N·m/√W)", _c20.get("km_Nm_sqrtW"),
            "torque per root watt of copper; " + _tail20, 3)
        one("Km per mass at 20 °C (N·m/(√W·kg))",
            _c20.get("km_per_mass_Nm_sqrtW_kg"),
            "the figure of merit that survives scaling; " + _tail20, 4)
        # …AND THE INDUCTANCES ON THE SAME BASIS (owner 2026-09-20: *«Ld/Lq
        # нужно указывать тоже для 20 градусов и без тока, как для KV»*).  KV
        # is a no-load constant at a stated temperature; a catalogue that
        # quotes the inductances at 600 A beside it is comparing two different
        # machines.  Incremental (∂ψ/∂i at the no-load iron state), never a
        # loaded chord.
        _ld0, _lq0 = _c20.get("Ld0_mH"), _c20.get("Lq0_mH")
        if _ld0 is not None:
            _tail0 = ("incremental (frozen permeability) at zero current and "
                      "20 °C — the same basis as KV above, which is what makes "
                      "the two comparable between machines")
            one("Ld at 20 °C, no load (mH)" + (" — winding" if _delta else ""),
                _ld0, _tail0, 4)
            one("Lq at 20 °C, no load (mH)" + (" — winding" if _delta else ""),
                _lq0, _tail0, 4)
            if _lq0 and float(_ld0):
                one("Saliency Lq / Ld at 20 °C",
                    float(_lq0) / float(_ld0),
                    "how far the two axes differ at no load — the further from "
                    "1, the more reluctance torque is available", 3)
    # resistance and the temperature it belongs to must come from the SAME duty
    _rd = next((d for d in duties if _g(d, "summary.R_phase_ohm")), None)
    r_phase = _g(_rd, "summary.R_phase_ohm") if _rd else None
    if r_phase:
        t_coil = float(_g(_rd, "summary.coil_temp_C") or 120)
        one("Phase resistance, hot (mΩ)" + (" — winding" if _delta else ""),
            1000 * float(r_phase),
            f"at {t_coil:g} °C, end winding included", 2)
        one("Phase resistance, 25 °C (mΩ)" + (" — winding" if _delta else ""),
            1000 * float(r_phase) * (1 + 0.00393 * (25 - 20)) / (1 + 0.00393 * (t_coil - 20)),
            "what an ohmmeter reads on a cold motor" if not _delta else
            "of ONE winding; an ohmmeter across two terminals of the delta reads ⅔ of it", 2)
        if _delta:
            one("Phase resistance, star-equivalent, hot (mΩ)",
                1000 * float(r_phase) / 3.0,
                "the per-phase R of the equivalent star — what a datasheet "
                "quotes for a delta machine", 2)
    # Two different inductances get quoted for the same machine and mixing them
    # up is the classic datasheet error: the BENCH pair is the small-signal
    # value an LCR meter reads at I≈0, the LOADED pair is the incremental
    # (frozen-permeability) value at the duty's own iron state.  Both are
    # stated, each labelled with its own basis.
    bench = next((_g(d, "summary.bench_ldq") for d in duties
                  if _g(d, "summary.bench_ldq")), None) or \
        (((passport or {}).get("passport") or {}).get("ldq0") if passport else None)
    if bench and bench.get("Ld_mH") and _delta:
        one("Ld, bench, star-equivalent (mH)", float(bench["Ld_mH"]) / 3.0,
            "winding value ÷ 3 — the per-phase inductance of the equivalent "
            "star; the winding's own is below", 4)
        one("Lq, bench, star-equivalent (mH)", float(bench["Lq_mH"]) / 3.0,
            "", 4)
    if bench and bench.get("Ld_mH"):
        one("Ld, bench (mH)" + (" — winding" if _delta else ""), bench.get("Ld_mH"),
            "small-signal at the I≈0 iron state, rotor locked on a magnet — what "
            "an LCR meter measures on the finished motor", 4)
        one("Lq, bench (mH)" + (" — winding" if _delta else ""), bench.get("Lq_mH"),
            "same probe, rotor locked between magnets", 4)
        if bench.get("Ld_mH") and bench.get("Lq_mH"):
            one("Saliency Lq / Ld (bench)",
                float(bench["Lq_mH"]) / float(bench["Ld_mH"]),
                "how far the two axes differ — the further from 1, the more "
                "reluctance torque is available", 3)
    blank()

    one("Magnet grade", mats.get("magnet"),
        "the grade name carries the temperature the magnets were modelled at")
    mt = _magnet_temp(str(mats.get("magnet") or ""))
    if mt:
        one("Magnet temperature (°C)", mt,
            "above it the demagnetisation margin in this sheet no longer holds", 0)
    one("Stator core steel", mats.get("stator_core"))
    one("Rotor core steel", mats.get("rotor_core"))
    k3 = next((_g(d, "result.end3d_k") for d in duties if _g(d, "result.end3d_k")), None)
    if k3:
        one("3-D end-effect factor", k3,
            "flux spilling past the ends of the stack that a 2-D model cannot see "
            "— torque, back-EMF and KV here already include it", 4)
    # the factor each duty was actually solved with — it is a property of the
    # winding, so it is shown flat when the duties agree and per duty when they
    # do not (a disagreement is worth seeing, not worth hiding)
    # k_end is GEOMETRY — one machine, one number.  Per-duty values existed
    # only while a stale stored override could disagree with what a duty was
    # actually solved with; with the override gone, quote the solved value
    # (all duties agree) or the cross-section's own formula.
    _kg = _k_end_geom(geo)
    ew = [(_g(d, "summary.end_winding_factor") or _setting(d, "sim.endWinding"))
          for d in duties]
    ew_vals = {round(float(x), 2) for x in ew if x}
    note_ew = ("conductor length / active length — sets the end-winding share "
               "of the resistance and the copper mass")
    if len(ew_vals) > 1:
        # should not happen any more; if it does, honesty over tidiness
        row("End-winding factor", ew, note_ew + " (differs between duties — "
            "the duties were solved with different end-winding lengths; "
            "re-save them to reconcile)", 3)
    else:
        one("End-winding factor",
            next((x for x in ew if x), _kg), note_ew
            + (f". Derived from the cross-section: {_kg:.3f}" if _kg else ""), 3)

    # ── battery ─────────────────────────────────────────────────────────────
    if batt:
        blank()
        section("BATTERY")
        one("Chemistry", batt.get("chemistry"), "cell type the pack is built from")
        one("Cells in series", batt.get("cells"), "", 0)
        one("Cell voltage, empty (V)", batt.get("v_cell_min"),
            "discharge cut-off of one cell", 2)
        one("Cell voltage, nominal (V)", batt.get("v_cell_nom"),
            "average cell voltage over the discharge", 2)
        one("Cell voltage, full (V)", batt.get("v_cell_max"),
            "fully charged cell", 2)
        one("Pack voltage, empty (V)", batt.get("v_min"),
            "cells × cell cut-off — the worst case every duty must survive", 1)
        one("Pack voltage, nominal (V)", batt.get("v_nom"), "", 1)
        one("Pack voltage, full (V)", batt.get("v_max"),
            "sets the insulation and the inverter's voltage class", 1)
        # the number that decides whether a duty is actually reachable
        vmin = batt.get("v_min")
        if vmin:
            marg = [((float(vmin) - float(v)) if (v := _g(d, "result.v_ll_peak_v",
                                                          "summary.V_line_peak_V")) else None)
                    for d in duties]
            row("Margin at empty pack (V)", marg,
                "empty-pack voltage minus the duty's bus voltage — negative means "
                "that duty drops out before the battery is empty", 1)
            row("Bus voltage used (%)",
                [((100.0 * float(v) / float(vmin))
                  if (v := _g(d, "result.v_ll_peak_v", "summary.V_line_peak_V")) else None)
                 for d in duties],
                "share of the empty-pack voltage the duty needs; above 100 % the "
                "point is not reachable at the end of the discharge", 1)

    # ── BOOST CHARGING (generators only) ────────────────────────────────────
    # What actually reaches the pack when the shaft turns this machine.  The
    # arithmetic is charge_analytic.py — the same model the Configure tab
    # shows, and the analytic twin of the FEM charging run.  Only printed for
    # a GENERATOR with a characterised machine: a motor's card would be
    # quoting the power it takes OUT of the pack as if it put power in.
    _ppass = ((passport or {}).get("passport") or {}) if passport else {}
    if batt and role == "generator" and _ppass.get("current"):
        try:
            from motor_ai_sim.charge_analytic import (charge_at as _cha,
                                                      max_charge as _mxc)
            _pwm_on = bool(_ppass.get("pwm"))
            blank()
            section("BOOST CHARGING")
            # WHY there is no PWM block, when the passport recorded it: an
            # unmeasurable point now leaves a machine-readable record (see
            # passport_pwm's `skipped`), and "no block" reads as a silence
            # unless the reason is printed with it.  One short clause — the
            # detail is in the passport.
            _pwm_why = ""
            if not _pwm_on:
                _sk = [s for s in (_ppass.get("pwm_skipped") or [])
                       if isinstance(s, dict) and s.get("code")]
                if _sk:
                    _s0 = _sk[0]
                    _pwm_why = (" (%s%s)" % (
                        str(_s0.get("code")).replace("_", " "),
                        (", m = %.3f on a %.0f V bus"
                         % (float(_s0["modulation_index"]),
                            float(_s0.get("v_bus_V") or 0.0)))
                        if _s0.get("modulation_index") is not None else ""))
            _dnote = ("ideal bridge — no dead time, no device conduction or "
                      "switching loss; a real charger delivers less, never more"
                      + (". The machine's measured PWM carrier losses ARE "
                         "included." if _pwm_on else
                         ". No PWM block on this passport%s, so the carrier's "
                         "own losses are NOT included." % _pwm_why))
            _rows = []
            for d in duties:
                _I = _g(d, "current_arms", "result.i_phase_rms_a")
                _r = _g(d, "rpm", "result.rpm")
                _rows.append(_cha(_ppass, batt, float(_I or 0.0),
                                  float(_r or 0.0), pwm=_pwm_on)
                             if _I and _r else None)
            row("Charge power (kW)",
                [(c["P_charge_W"] / 1000.0 if c else None) for c in _rows],
                "shaft power minus every loss the card reports; " + _dnote, 2)
            row("Charge current (A)",
                [(c["I_charge_A"] if c else None) for c in _rows],
                "P_charge / V_bus", 1)
            row("C-rate",
                [(c["C_rate"] if c else None) for c in _rows],
                "charge current over the pack's capacity", 3)
            row("Bus under charge (V)",
                [(c["V_bus_V"] if c else None) for c in _rows],
                "V_oc + I·R_pack — charging pushes the terminal ABOVE the open "
                "circuit, which is why a boost run has to iterate the bus", 1)
            row("Bus rise (V)",
                [(c["V_rise_V"] if c else None) for c in _rows],
                "how far the pack's own resistance lifts the link", 2)
            row("Charge efficiency (%)",
                [((c["eta_charge"] or 0) * 100 if c else None) for c in _rows],
                "shaft in → pack in; the pack's own I²R is reported separately "
                "and is NOT in the machine's efficiency", 2)
            row("Pack I²R loss (W)",
                [(c["P_pack_r_loss_W"] if c else None) for c in _rows],
                "burnt in the cells' internal resistance — not a machine loss", 2)
            row("Limited by",
                [(c["limited_by"] if c else None) for c in _rows],
                "current = the pack's charge ceiling · pack = its maximum "
                "terminal voltage · modulation = the bus cannot synthesise the "
                "fundamental the machine needs · none = the shaft power is the "
                "only limit", 0)
            _best = [(_mxc(_ppass, batt, float(_g(d, "rpm", "result.rpm") or 0.0),
                           pwm=_pwm_on) if _g(d, "rpm", "result.rpm") else None)
                     for d in duties]
            row("Max charge power (kW)",
                [(b["P_charge_W"] / 1000.0 if b else None) for b in _best],
                "the best any current in the machine's own measured sweep range "
                "reaches at that speed, with every limit held", 2)
            row("Max charge at current (A)",
                [(b["I_A"] if b else None) for b in _best],
                "the phase current that reaches it", 1)
            one("Pack resistance (mΩ)",
                (_rows[0]["R_pack_ohm"] * 1000.0 if _rows and _rows[0] else None),
                "NS · r_int / NP; wiring, fuse and connector resistance are NOT "
                "in it", 2)
        except Exception:      # noqa: BLE001 — a datasheet must survive this
            log.exception("datasheet: charging block failed — the sheet ships "
                          "without it")

    # The Dimensions sheet is GONE (user 2026-09-09: "убери вкладку
    # Dimensions, они не нужны").  It listed every geometry key of the
    # machine; the numbers a reader of a datasheet needs are on the Motor
    # card, and the drawing lives in the PDF report.  `schema` went with it
    # — it existed only to label those rows.

    # ── Curves (only if the machine has been characterised) ─────────────────
    pp = ((passport or {}).get("passport") or {}) if passport else {}
    if pp:
        wsc = wb.create_sheet("Curves")
        wsc.column_dimensions["A"].width = 26
        for i in range(2, 12):
            wsc.column_dimensions[get_column_letter(i)].width = 13
        wsc.cell(row=1, column=1, value="Measured curves").font = \
            Font(bold=True, size=13, color=_TITLE)
        wsc.cell(row=2, column=1, value=(
            "Each point below is its own finite-element solve of this machine — "
            "this is where the limits live: how far torque keeps following "
            "current, and how the losses grow with speed."
        )).font = Font(size=9, color=_NOTE, italic=True)
        rc = 4
        cur = pp.get("current") or {}
        if cur.get("I_A") and cur.get("T_Nm"):
            Is = [float(v) for v in cur["I_A"]]
            Ts = [abs(float(v)) for v in cur["T_Nm"]]
            wsc.cell(row=rc, column=1, value="Torque and demagnetisation vs current")\
                .font = Font(bold=True)
            rc += 1
            # SATURATION IN A NUMBER, not in a picture (2026-09-09).  The chart
            # that used to sit here drew the measured torque against the
            # straight line through its first point; the interesting part of it
            # was always the GAP, so the gap is now a row — per cent of the
            # linear extrapolation the machine actually delivers.  Below ~100 %
            # the iron is saturating.
            _lin = (Ts[0] / Is[0]) if Is and Is[0] else 0.0
            _sat = [(100.0 * Ts[i] / (_lin * Is[i]))
                    if (_lin and Is[i]) else None for i in range(len(Is))]
            for lbl, vals, d in (("Phase current, A rms", cur.get("I_A"), 1),
                                 ("Torque, N·m", cur.get("T_Nm"), 2),
                                 ("Torque vs linear, %", _sat, 1),
                                 ("Line voltage peak, V", cur.get("V_peak_V"), 1),
                                 ("Magnet retention, %", cur.get("demag_keep_pct"), 2)):
                if not vals:
                    continue
                wsc.cell(row=rc, column=1, value=lbl).border = border
                for i, v in enumerate(vals):
                    cell = wsc.cell(row=rc, column=2 + i,
                                    value=(None if v is None
                                           else _num(abs(float(v)), d)))
                    cell.alignment = right
                    cell.border = border
                rc += 1
            rc += 2

        lgd = pp.get("loss_grid") or {}
        if lgd.get("rpm") and lgd.get("Pfe_W"):
            rpms = [float(v) for v in lgd["rpm"]]
            Ilist = [float(v) for v in (lgd.get("I_A") or [])]

            wsc.cell(row=rc, column=1, value="Speed-dependent losses (W)").font = \
                Font(bold=True)
            rc += 1
            wsc.cell(row=rc, column=1, value="current \\ rpm").font = Font(bold=True, size=9)
            for i, v in enumerate(rpms):
                cell = wsc.cell(row=rc, column=2 + i, value=v)
                cell.font = Font(bold=True)
                cell.fill = PatternFill("solid", fgColor=_DUTY)
                cell.border = border
            rc += 1
            for tag, rows_ in (("iron", lgd.get("Pfe_W")), ("magnets", lgd.get("Pmag_W"))):
                if not rows_:
                    continue
                for r_, I in enumerate(Ilist):
                    wsc.cell(row=rc, column=1, value=f"{I:g} A · {tag}").border = border
                    for i, v in enumerate(rows_[r_]):
                        cell = wsc.cell(row=rc, column=2 + i, value=_num(v, 1))
                        cell.alignment = right
                        cell.border = border
                    rc += 1
            rc += 2

        # ── charge map: P_charge(rpm) at the max-charge current ────────────
        # The boost-mode interpolation — for a generator this IS the machine's
        # output curve, and it belongs on the same sheet as the loss surface it
        # is computed from.
        if batt and role == "generator" and pp.get("current"):
            try:
                from motor_ai_sim.charge_analytic import charge_map as _cmap
                _pwm_on = bool(pp.get("pwm"))
                cm = _cmap(pp, batt, pwm=_pwm_on)
            except Exception:      # noqa: BLE001
                log.exception("datasheet: charge map failed")
                cm = []
            if cm:
                wsc.cell(row=rc, column=1, value=(
                    "Boost charging — power into the pack vs speed"
                    + (" (PWM carrier losses included)" if _pwm_on
                       else " (sine losses only)"))).font = Font(bold=True)
                rc += 1
                for lbl, vals, dg in (
                        ("Speed, rpm", [r["rpm"] for r in cm], 0),
                        ("Charge power, kW",
                         [r["P_charge_W"] / 1000.0 for r in cm], 2),
                        ("At phase current, A rms", [r["I_A"] for r in cm], 1),
                        ("Charge current, A", [r["I_charge_A"] for r in cm], 1),
                        ("Bus under charge, V", [r["V_bus_V"] for r in cm], 1),
                        ("C-rate", [r["C_rate"] for r in cm], 3),
                        ("Charge efficiency, %",
                         [((r["eta_charge"] or 0) * 100) for r in cm], 2)):
                    wsc.cell(row=rc, column=1, value=lbl).border = border
                    for i, v in enumerate(vals):
                        cell = wsc.cell(row=rc, column=2 + i,
                                        value=(_num(v, dg) if v is not None
                                               else None))
                        cell.alignment = right
                        cell.border = border
                    rc += 1
                wsc.cell(row=rc, column=1, value="Limited by").border = border
                for i, r_ in enumerate(cm):
                    cell = wsc.cell(row=rc, column=2 + i, value=r_["limited_by"])
                    cell.alignment = right
                    cell.border = border
                rc += 1
                wsc.cell(row=rc, column=1, value=(
                    "At every speed the phase current is swept over the machine's "
                    "own measured range and the best FEASIBLE point kept; "
                    "\"limited by\" says what stops it going higher. Ideal bridge — "
                    "no dead time, no device loss.")).font = \
                    Font(size=9, color=_NOTE, italic=True)
                rc += 3

        e3d = (pp.get("end3d") or {}).get("k_flux_vs_L") or {}
        if e3d:
            pts = sorted((float(a), float(b)) for a, b in e3d.items())
            wsc.cell(row=rc, column=1, value="3-D end effect vs stack length").font = \
                Font(bold=True)
            rc += 1
            wsc.cell(row=rc, column=1, value="Stack length, mm").border = border
            for i, (L_, _k) in enumerate(pts):
                cell = wsc.cell(row=rc, column=2 + i, value=L_)
                cell.alignment = right
                cell.border = border
            rc += 1
            wsc.cell(row=rc, column=1, value="k_flux").border = border
            for i, (_L, k_) in enumerate(pts):
                cell = wsc.cell(row=rc, column=2 + i, value=_num(k_, 4))
                cell.alignment = right
                cell.border = border
            rc += 1
            wsc.cell(row=rc, column=1, value=(
                "A short stack loses a larger share of its flux past the ends, so "
                "the factor falls as the machine gets shorter — torque and KV are "
                "already multiplied by it.")).font = Font(size=9, color=_NOTE, italic=True)

    # ── Calculator (live formulas over the measured data) ───────────────────
    if pp and (pp.get("current") or pp.get("loss_grid")):
        _build_calculator(wb, pp=pp, geo=geo, wind=wind, duties=duties,
                          gamma0=(passport or {}).get("gamma_deg"), slot=slot,
                          bearings=(_brg_assign if _has_brg else None))

    # ── Details sheet ───────────────────────────────────────────────────────
    ws2 = wb.create_sheet("Details")
    ws2.column_dimensions["A"].width = 40
    for i in range(2, 2 + ncol):
        ws2.column_dimensions[get_column_letter(i)].width = 17
    ws2.cell(row=1, column=1, value="Detail behind the card").font = \
        Font(bold=True, size=13, color=_TITLE)
    ws2.cell(row=2, column=1, value=(
        "Mass breakdown, iron-loss terms and the solver settings each duty was "
        "computed with — the audit trail for the first sheet."
    )).font = Font(size=9, color=_NOTE, italic=True)

    r2 = 4
    ws2.cell(row=r2, column=1, value="Duty").font = Font(bold=True)
    for i, d in enumerate(duties):
        c = ws2.cell(row=r2, column=2 + i, value=str(d.get("name") or ""))
        c.font = Font(bold=True)
        c.alignment = right
    r2 += 1

    def drow(label: str, getter, d: int = 3) -> None:
        nonlocal r2
        ws2.cell(row=r2, column=1, value=label).border = border
        for i, du in enumerate(duties):
            cell = ws2.cell(row=r2, column=2 + i, value=_num(getter(du), d))
            cell.alignment = right
            cell.border = border
        r2 += 1

    names: List[str] = []
    for du in duties:
        for comp in (_g(du, "summary.mass_components") or []):
            nm = str(comp.get("name") or "")
            if nm and nm not in names:
                names.append(nm)
    if names:
        ws2.cell(row=r2, column=1, value="Mass breakdown (kg)").font = Font(bold=True)
        r2 += 1
        for nm in names:
            drow("   " + nm, lambda du, nm=nm: next(
                (c.get("mass_kg") for c in (_g(du, "summary.mass_components") or [])
                 if c.get("name") == nm), None), 3)
        drow("   Total", lambda du: _g(du, "summary.mass_total_kg", "result.mass_kg"), 3)
        # A frameless build is sold without its shaft: that part is in the
        # FIELD (its material, its eddy losses, its heat) and out of the mass.
        # Say so where the mass is quoted — a 0.000 kg row with no explanation
        # reads as a bug, and a total that quietly excludes metal the reader
        # can see in the 3D view reads as a lie.
        _ref_notes: List[str] = []
        for du in duties:
            for comp in (_g(du, "summary.mass_components") or []):
                if str(comp.get("state") or "") != "reference":
                    continue
                _t = (f"{comp.get('name')}: customer-supplied — modelled as "
                      f"{comp.get('material') or 'the assigned material'} for "
                      f"the field ({_num(comp.get('mass_modelled_kg'), 3)} kg), "
                      f"not included in mass, inertia or any per-mass density; "
                      f"its losses ARE in P_loss and the efficiency.")
                if _t not in _ref_notes:
                    _ref_notes.append(_t)
        for _t in _ref_notes:
            ws2.cell(row=r2, column=1, value=_t).font = Font(
                size=9, color=_NOTE, italic=True)
            r2 += 1
        r2 += 1

    ws2.cell(row=r2, column=1, value="Iron loss terms (W)").font = Font(bold=True)
    r2 += 1
    for part in ("stator", "rotor"):
        for term in ("hysteresis_W", "eddy_W", "excess_W"):
            drow(f"   {part} · {term.split('_')[0]}",
                 lambda du, p=part, t=term: _g(du, f"summary.P_core_terms.{p}.{t}"), 2)
    r2 += 1

    ws2.cell(row=r2, column=1, value="Waveform and magnetics").font = Font(bold=True)
    r2 += 1
    drow("   Back-EMF THD, line-line (%)", lambda du: _g(du, "summary.THD_LL_pct"), 2)
    drow("   Fundamental line voltage (V)", lambda du: _g(du, "summary.V1_LL_V"), 1)
    drow("   Magnet flux linkage ψ_PM (mWb)",
         lambda du: (float(_g(du, "summary.psi_pm_Wb") or 0) * 1000) or None, 3)
    drow("   Saliency Lq/Ld", lambda du: _g(du, "summary.saliency_Lq_over_Ld"), 3)
    r2 += 1

    ws2.cell(row=r2, column=1, value="Solver settings").font = Font(bold=True)
    r2 += 1
    drow("   Steps per electrical period",
         lambda du: _g(du, "summary.n_steps_per_period"), 0)
    drow("   Mesh element size (mm)", lambda du: _setting(du, "mesh.meshSize"), 3)
    drow("   Air-gap layers", lambda du: _setting(du, "mesh.gapLayers"), 0)
    drow("   3-D correction applied",
         lambda du: 1 if _setting(du, "sim.apply3d") else 0, 0)
    drow("   Eddy currents coupled",
         lambda du: 1 if _setting(du, "sim.eddyCoupled") else 0, 0)
    drow("   Nonlinear residual",
         lambda du: _g(du, "summary.nonlinear_resid_max"), 9)
    drow("   Computed at", lambda du: str(_g(du, "saved_at") or "")[:19], 0)

    # ── Notes sheet ─────────────────────────────────────────────────────────
    ws3 = wb.create_sheet("Notes")
    ws3.column_dimensions["A"].width = 30
    ws3.column_dimensions["B"].width = 100
    ws3.cell(row=1, column=1, value="How to read this datasheet").font = \
        Font(bold=True, size=13, color=_TITLE)
    notes = [
        ("Where the numbers come from",
         "A finite-element solve of this exact cross-section, winding and material "
         "set — not a scaling formula and not a similar motor. Each duty column is "
         "one transient run over an electrical period."),
        ("Torque",
         "Average electromagnetic torque, energy (flux-linkage) method, with the "
         "3-D end-effect factor applied. It is the torque at the rotor, before "
         "bearing friction."),
        ("Efficiency",
         "Rotor power divided by rotor power plus the copper, iron and magnet "
         "losses listed."
         + (" SHAFT EFFICIENCY beside it is the same number with the bearing "
            "and windage losses taken off, which is what a dynamometer on the "
            "shaft reads. Those two come from the SKF frictional-moment model "
            "and an analytic windage estimate for the bearings this machine is "
            "built with — analytic, not FEM. Unbalanced magnetic pull, coupling "
            "side loads and any seal on the shaft itself are not modelled."
            if _has_brg else
            " Bearing and windage losses belong to the assembled machine and "
            "are not included — this machine has no bearings assigned. Set them "
            "in Mechanical -> Shaft & bearings and they will be computed and "
            "shown here.")),
        ("Copper loss",
         "I²R at the stated coil temperature plus proximity loss driven by the "
         "rotating magnet field. The second part barely depends on current and "
         "grows with the square of speed, so even a lightly loaded fast motor "
         "heats its copper."),
        ("Bus voltage",
         "Line-to-line peak of the solved voltage. A duty is reachable only while "
         "the DC bus stays above it, so compare it with the minimum pack voltage, "
         "not the nominal one."),
        ("KV",
         "No-load constant: rpm per volt of line-peak back-EMF, from the magnet "
         "flux linkage at zero current. Under load the effective value is lower."),
        ("Ld / Lq",
         "Two different things share these names. The BENCH pair in the design "
         "block is the small-signal value at the I≈0 iron state — what an LCR "
         "meter reads with the rotor locked, on a magnet (Ld) and between magnets "
         "(Lq). The AT LOAD pair in each duty column is the chord value at that "
         "duty's own saturation, which is what the controller actually sees."),
        ("The Calculator sheet",
         "It rescales this machine to different turns, stack length, wire height, "
         "connection, current and speed with live formulas over the FEM tables "
         "printed underneath it — the same model the web tuner uses. It cannot "
         "see a changed cross-section, another steel or magnet, or thermal "
         "limits; for those, solve the variant properly."),
        ("Torque ripple",
         "Peak-to-peak over mean of the torque waveform, at the step count shown "
         "on the Details sheet. Cogging is included."),
        ("Temperature",
         "Copper resistance and losses are quoted at the coil temperature on the "
         "card; the magnet grade name carries the temperature the magnets were "
         "modelled at. Above it the demagnetisation margin no longer holds."),
        ("Accuracy",
         "Torque and back-EMF land within a few percent of bench measurement on "
         "the machines validated so far. Losses depend on build quality — "
         "lamination, gluing, impregnation, machining of the stack — and are the "
         "least certain figures here."),
    ]
    for i, (k_, v_) in enumerate(notes):
        rr = 3 + i
        c1 = ws3.cell(row=rr, column=1, value=k_)
        c1.font = Font(bold=True)
        c1.alignment = Alignment(vertical="top")
        c2 = ws3.cell(row=rr, column=2, value=v_)
        c2.alignment = Alignment(wrap_text=True, vertical="top")
        ws3.row_dimensions[rr].height = 34

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
