"""The power schematic — DC link, bridges, coil terminals — drawn from the map.

Owner, 2026-09-22: a picture of *"the controller and its connection to the
motor"* that follows the chosen topology and the winding, with the coils
labelled as the winding builder names them and the parallel device count shown
as a number on each switch.  Later the same day he sent the drawing he wants it
to look like: the classic three-phase two-level bridge — DC source and C_dc on
the left, three vertical legs, **every switch a transistor symbol with its
antiparallel (body) diode**, switches numbered S1…S6 in the standard order
(S1/S4, S3/S6, S5/S2), the phase outputs taken from between the switches.

It is generated SERVER-SIDE, from the very :class:`~motor_ai_sim.inverter.
topology.Topology` the losses were computed on, for one reason: a diagram
drawn from a second description of the same thing can disagree with the
numbers, and this one cannot.  The tab re-requests it whenever the topology or
the mapping changes, which is the same request that re-computes the losses.

Plain line-art SVG, no external fonts, ``currentColor`` for every stroke and
label so it inherits the tab's light/dark theme.  LABELS ONLY inside the
drawing — a schematic with sentences in it is a text wall with a grid.

Crossings follow the usual convention: a junction is a filled dot, and two
lines that cross WITHOUT a dot are not connected.
"""
from __future__ import annotations

import html
from typing import Any, Dict, List, Optional, Sequence

__all__ = ["schematic_svg"]

# ── geometry of one bridge block ───────────────────────────────────────────
LEG_DX = 86.0          # horizontal pitch of the legs
BRIDGE_H = 190.0       # every bridge block is the same height
RAIL_TOP_DY = 30.0     # + rail, from the block's top edge
RAIL_BOT_DY = 26.0     # - rail, from the block's bottom edge
SW_DY = 46.0           # switch centre, above/below the mid line
GAP = 26.0             # vertical gap between stacked bridge blocks
DC_X_POS, DC_X_NEG = 84.0, 44.0
OUT_DY = 20.0          # vertical pitch of the three phase output wires


def _esc(s: Any) -> str:
    return html.escape(str(s), quote=True)


def _line(x1: float, y1: float, x2: float, y2: float, w: float = 1.2,
          op: float = 1.0) -> str:
    return (f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="currentColor" stroke-width="{w}"'
            + (f' opacity="{op}"' if op < 1.0 else "") + '/>')


def _dot(x: float, y: float, r: float = 2.6) -> str:
    return f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="currentColor"/>'


def _text(x: float, y: float, s: Any, size: float = 9.0,
          weight: str = "400", op: float = 1.0, anchor: str = "start") -> str:
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" '
            f'font-weight="{weight}" text-anchor="{anchor}" '
            f'fill="currentColor"'
            + (f' opacity="{op}"' if op < 1.0 else "") + f'>{_esc(s)}</text>')


# ---------------------------------------------------------------------------
# One switch — a MOSFET with its body diode, the way the reference draws it
# ---------------------------------------------------------------------------

def _mosfet(cx: float, cy: float, label: str, n_par: int) -> List[str]:
    """An N-channel MOSFET on the vertical wire through ``cx``.

    Drain up, source down, gate to the left, body diode to the right with its
    cathode at the DRAIN — which is the antiparallel direction, and is what
    carries the current in the third quadrant while the leg is in dead time.
    """
    ch_x = cx - 9        # the channel bar
    g_x = cx - 15        # the gate bar
    d_x = cx + 14        # the body-diode branch
    out: List[str] = [
        # drain and source leads on the leg wire
        _line(cx, cy - 22, cx, cy - 9),
        _line(cx, cy + 9, cx, cy + 22),
        # into the channel
        _line(cx, cy - 9, ch_x, cy - 9),
        _line(cx, cy + 9, ch_x, cy + 9),
        # channel bar (drawn solid, the usual power-device shorthand)
        _line(ch_x, cy - 12, ch_x, cy + 12, 1.8),
        # gate bar and gate lead
        _line(g_x, cy - 9, g_x, cy + 9, 1.4),
        _line(g_x, cy, g_x - 9, cy),
        # source arrow (N-channel: into the channel)
        f'<path d="M {ch_x - 6:.1f} {cy + 5:.1f} L {ch_x:.1f} {cy + 9:.1f} '
        f'L {ch_x - 6:.1f} {cy + 13:.1f} Z" fill="currentColor"/>',
        # body diode, antiparallel: anode at source, cathode at drain
        _line(cx, cy - 22, d_x, cy - 22),
        _line(cx, cy + 22, d_x, cy + 22),
        _line(d_x, cy - 22, d_x, cy - 5),
        _line(d_x, cy + 22, d_x, cy + 5),
        f'<path d="M {d_x - 5:.1f} {cy + 5:.1f} L {d_x + 5:.1f} {cy + 5:.1f} '
        f'L {d_x:.1f} {cy - 5:.1f} Z" fill="none" stroke="currentColor" '
        f'stroke-width="1.2"/>',
        _line(d_x - 5.5, cy - 5, d_x + 5.5, cy - 5, 1.6),
    ]
    tag = label + (f" ×{int(n_par)}" if n_par > 1 else "")
    out.append(_text(g_x - 11, cy + 3.5, tag, 8.5, "600", anchor="end"))
    return out


def _coil(x: float, y: float, r: float = 5.0) -> str:
    """Three arcs on a horizontal wire — one coil."""
    d = " ".join(f"A {r} {r} 0 0 1 {x + (i + 1) * 2 * r:.1f} {y:.1f}"
                 for i in range(3))
    return (f'<path d="M {x:.1f} {y:.1f} {d}" fill="none" '
            f'stroke="currentColor" stroke-width="1.5"/>')


def _coil_seg(x1: float, y1: float, x2: float, y2: float,
              r: float = 5.0) -> List[str]:
    """One winding ON a segment: straight leads and a coil in the middle.

    Rotated into place, so a coil on the side of a delta triangle or on the
    spoke of a star is the same symbol as a coil on a horizontal wire.
    """
    import math
    L = math.hypot(x2 - x1, y2 - y1)
    w = 6 * r
    if L <= w + 6:
        return [_line(x1, y1, x2, y2, 1.5)]
    ang = math.degrees(math.atan2(y2 - y1, x2 - x1))
    t0 = (L - w) / 2.0 / L
    t1 = 1.0 - t0
    ax, ay = x1 + (x2 - x1) * t0, y1 + (y2 - y1) * t0
    bx_, by_ = x1 + (x2 - x1) * t1, y1 + (y2 - y1) * t1
    return [
        _line(x1, y1, ax, ay, 1.5),
        f'<g transform="translate({ax:.1f} {ay:.1f}) rotate({ang:.2f})">'
        + _coil(0.0, 0.0, r) + '</g>',
        _line(bx_, by_, x2, y2, 1.5),
    ]


#: The textbook numbering of a three-phase two-level bridge: the high sides are
#: the odd numbers in leg order, the low sides the even ones starting at the
#: SECOND leg — S1/S4, S3/S6, S5/S2.  Anyone reading a commutation table
#: expects these names.
_3PH_NUMBERS = (("S1", "S4"), ("S3", "S6"), ("S5", "S2"))
#: An H-bridge is numbered down one leg and then the other.
_HB_NUMBERS = (("S1", "S2"), ("S3", "S4"))


def _switch_names(bridge: Any) -> List[tuple]:
    if bridge.kind == "h_bridge":
        table = _HB_NUMBERS
    else:
        table = _3PH_NUMBERS
    out = []
    for i, _ in enumerate(bridge.legs):
        out.append(table[i] if i < len(table) else (f"S{2 * i + 1}", f"S{2 * i + 2}"))
    return out


# ---------------------------------------------------------------------------
# One bridge
# ---------------------------------------------------------------------------

def _bridge_block(bx: float, by: float, bridge: Any) -> Dict[str, Any]:
    legs = list(bridge.legs)
    n = len(legs)
    width = 62.0 + n * LEG_DX
    y_top = by + RAIL_TOP_DY
    y_bot = by + BRIDGE_H - RAIL_BOT_DY
    y_mid = 0.5 * (y_top + y_bot)
    names = _switch_names(bridge)

    parts: List[str] = [
        _text(bx + 6, by + 12, bridge.label or bridge.id, 10, "600"),
        _line(bx + 10, y_top, bx + width - 10, y_top, 1.8),
        _line(bx + 10, y_bot, bx + width - 10, y_bot, 1.8),
    ]
    nodes: List[Dict[str, Any]] = []
    for i, lg in enumerate(legs):
        cx = bx + 46 + i * LEG_DX
        hi, lo = names[i]
        parts += [_line(cx, y_top, cx, y_mid - SW_DY + 22),
                  _line(cx, y_mid + SW_DY - 22, cx, y_bot)]
        parts += _mosfet(cx, y_mid - SW_DY, hi, bridge.devices_parallel)
        parts += _mosfet(cx, y_mid + SW_DY, lo, bridge.devices_parallel)
        parts.append(_line(cx, y_mid - SW_DY + 22, cx, y_mid + SW_DY - 22))
        parts.append(_dot(cx, y_mid))
        nodes.append({"x": cx, "y": y_mid, "leg": lg, "name": lg.name,
                      "coils": list(lg.coils)})
    return {"svg": parts, "nodes": nodes, "width": width,
            "y_top": y_top, "y_bot": y_bot, "y_mid": y_mid, "y": by}


# ---------------------------------------------------------------------------
# The motor side — a star with its neutral, or a closed delta triangle
# ---------------------------------------------------------------------------

#: Radius of the three-phase motor symbol [px].
MOTOR_R = 48.0


def _three_phase_motor(blk: Dict[str, Any], x_bridge_end: float,
                       label_of: Dict[int, str]) -> List[str]:
    """Wire this bridge's three legs to a drawn star or delta — NO CROSSINGS.

    Owner, 2026-09-22, on the first version (one vertex at the top, the other
    two below it): *«я бы повернул и треугольник, и звезду на 60 градусов,
    тогда линии фаз не пересекались бы»*.  He is right, and the rule behind it
    is the one this function now keeps: **the terminals must appear in the same
    top-to-bottom order as the legs that feed them.**  The legs leave the
    bridge stacked L1 / L2 / L3, so:

    Both symbols are the TEXTBOOK ones and they share one terminal geometry —
    three points 120° apart at 120° / 240° / 0°, i.e. upper-left, lower-left
    and right (owner, on the first star: *«нарисуй нормальную звезду»* — three
    identical windings at 120°, equal arms, meeting at N in the centre).

    ``star``   three equal arms from the neutral N at the centre to those
               three points, a winding on each.
    ``delta``  the equilateral triangle on the same three points, a winding on
               each side, terminals at the vertices.

    The feeders then need no crossing: L1 goes straight to the upper-left
    terminal, L2 straight to the lower-left one, and L3 — already the lowest
    wire — is taken UNDER the symbol and up into the right terminal, so it
    meets neither another feeder nor a winding.
    """
    import math
    nodes = blk["nodes"]
    b = blk["bridge"]
    y_mid = blk["y_mid"]
    x_gather = x_bridge_end + 34
    cx = x_gather + MOTOR_R + 52
    cy = y_mid
    R = MOTOR_R
    out: List[str] = []

    #: The three terminals, 120° apart: upper-left, lower-left, right.
    term = [(cx + R * math.cos(math.radians(a)),
             cy - R * math.sin(math.radians(a))) for a in (120.0, 240.0, 0.0)]
    y_under = cy + 1.5 * R               # clear of the symbol and of L2's label

    for i, nd in enumerate(nodes[:3]):
        y_out = y_mid - OUT_DY + i * OUT_DY
        if abs(y_out - nd["y"]) > 0.5:
            out.append(_line(nd["x"], nd["y"], nd["x"], y_out))
        out.append(_line(nd["x"], y_out, x_gather, y_out))
        tx, ty = term[i]
        if i < 2:
            out.append(_line(x_gather, y_out, tx, ty))
        else:
            out += [_line(x_gather, y_out, x_gather, y_under),
                    _line(x_gather, y_under, tx, y_under),
                    _line(tx, y_under, tx, ty)]
        out.append(_dot(tx, ty, 2.4))
        dx, dy, anchor = ((-7.0, -9.0, "end") if i == 0 else
                          (-7.0, 16.0, "end") if i == 1 else
                          (10.0, 3.5, "start"))
        out.append(_text(tx + dx, ty + dy,
                         ", ".join(label_of.get(c, str(c)) for c in nd["coils"]),
                         8.5, "500", anchor=anchor))

    if b.connection == "delta":
        for a, bb in ((0, 1), (1, 2), (2, 0)):
            out += _coil_seg(term[a][0], term[a][1], term[bb][0], term[bb][1])
        out.append(_text(cx - 0.12 * R, cy + 4.5, "Δ", 13, "700", 0.85,
                         anchor="middle"))
    else:
        for tx, ty in term:
            out += _coil_seg(cx, cy, tx, ty)
        out += [_dot(cx, cy, 3.2), _text(cx + 7, cy + 12, "N", 9, "600")]
    return out


# ---------------------------------------------------------------------------
# The whole picture
# ---------------------------------------------------------------------------

def schematic_svg(topology: Any, *, v_dc_V: Optional[float] = None,
                  device: Optional[str] = None,
                  title: Optional[str] = None) -> str:
    """The whole drawing as one ``<svg>`` string."""
    bridges = list(topology.bridges)
    label_of = {c.index: f"L{c.index} {c.phase}"
                f"{'+' if c.polarity > 0 else '-'}" for c in topology.coils}

    bx = 118.0
    y = 44.0
    blocks: List[Dict[str, Any]] = []
    parts: List[str] = []
    for b in bridges:
        blk = _bridge_block(bx, y, b)
        blk["bridge"] = b
        blocks.append(blk)
        parts += blk["svg"]
        y += BRIDGE_H + GAP
    width_max = max(b["width"] for b in blocks)
    total_h = y + 22
    total_w = bx + width_max + 2 * MOTOR_R + 190

    # ── the DC link: source, capacitor, rails to every bridge ──────────────
    top = blocks[0]["y_top"]
    bot = blocks[-1]["y_bot"]
    mid = 0.5 * (top + bot)
    dc: List[str] = [
        _line(DC_X_POS, top - 12, DC_X_POS, bot + 12, 2.0),
        _line(DC_X_NEG, top - 12, DC_X_NEG, bot + 12, 2.0),
        # C_dc across the rails
        _line(DC_X_NEG, mid - 34, DC_X_POS, mid - 34),
        _line(DC_X_NEG, mid + 34, DC_X_POS, mid + 34),
        _line(0.5 * (DC_X_NEG + DC_X_POS), mid - 34,
              0.5 * (DC_X_NEG + DC_X_POS), mid - 8),
        _line(0.5 * (DC_X_NEG + DC_X_POS), mid + 34,
              0.5 * (DC_X_NEG + DC_X_POS), mid + 8),
        _line(DC_X_NEG + 2, mid - 8, DC_X_POS - 2, mid - 8, 2.4),
        _line(DC_X_NEG + 2, mid + 8, DC_X_POS - 2, mid + 8, 2.4),
        _text(DC_X_POS + 6, mid - 12, "C_dc", 9, "400", 0.85),
        # the source, on the far left
        _line(16, top - 12, 16, bot + 12, 1.6),
        _line(16, top - 12, DC_X_NEG, top - 12, 1.6),
        _line(16, bot + 12, DC_X_NEG, bot + 12, 1.6),
        _line(8, mid - 10, 24, mid - 10, 2.4),
        _line(12, mid - 3, 20, mid - 3, 1.4),
        _line(8, mid + 4, 24, mid + 4, 2.4),
        _line(12, mid + 11, 20, mid + 11, 1.4),
        _text(16, mid - 18, "DC link", 9, "600", 1.0, anchor="middle"),
        _text(DC_X_POS - 4, top - 16, "+", 11, "700", anchor="end"),
        _text(DC_X_NEG - 4, bot + 22, "−", 11, "700", anchor="end"),
    ]
    if v_dc_V:
        dc.append(_text(16, mid + 26, f"{float(v_dc_V):.0f} V", 9, "400",
                        0.85, anchor="middle"))
    for blk in blocks:
        dc += [_line(DC_X_POS, blk["y_top"], bx + 10, blk["y_top"], 1.6),
               _line(DC_X_NEG, blk["y_bot"], bx + 10, blk["y_bot"], 1.6),
               _dot(DC_X_POS, blk["y_top"]), _dot(DC_X_NEG, blk["y_bot"])]
    parts = dc + parts

    # ── the motor side ─────────────────────────────────────────────────────
    for blk in blocks:
        b = blk["bridge"]
        nodes = blk["nodes"]
        if b.kind == "h_bridge" and len(nodes) == 2:
            # The load sits BETWEEN the two legs — which is what an H-bridge is.
            x0, x1 = nodes[0]["x"], nodes[1]["x"]
            ym = blk["y_mid"]
            cxm = 0.5 * (x0 + x1) - 15
            parts += [_line(x0, ym, cxm, ym), _coil(cxm, ym),
                      _line(cxm + 30, ym, x1, ym)]
            txt = ", ".join(label_of.get(c, str(c)) for c in nodes[0]["coils"])
            parts.append(_text(0.5 * (x0 + x1), ym - 12, txt, 9, "500",
                               anchor="middle"))
            parts.append(_text(bx + blk["width"] + 16, ym + 3.5,
                               b.modulation, 9, "400", 0.75))
            continue
        # three-phase: the outputs leave the midpoints, run right, and end on a
        # REAL motor symbol — a star with its neutral, or a closed delta
        # triangle, whichever this duty is wound as (owner 2026-09-22:
        # «дельту и звезду тоже надо рисовать на картинке»).
        parts += _three_phase_motor(blk, bx + width_max, label_of)

    head = title or (f"{topology.as_dict()['preset_label']} · "
                     f"{len(bridges)} bridge(s) · {topology.n_switches} switches"
                     + (f" · {device}" if device else ""))
    # No legend line under the drawing: every coil is already NAMED at the
    # terminal it is wired to, and a second listing of the same six names is
    # the text wall the tab rule forbids.
    parts.insert(0, _text(14, 20, head, 12, "600"))

    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 '
            f'{total_w:.0f} {total_h:.0f}" width="100%" '
            f'style="max-width:{total_w:.0f}px" role="img" '
            f'aria-label="{_esc(head)}">' + "".join(parts) + "</svg>")
