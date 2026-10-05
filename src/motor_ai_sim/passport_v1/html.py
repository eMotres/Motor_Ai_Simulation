"""Self-contained HTML passport cards (tables + inline SVG charts).

No external assets: every chart is an inline SVG built here, so a card opens
offline and prints.  Every number shown carries its basis label in the row or
in the chart caption; prose is one line per block (owner: minimal prose).
"""
from __future__ import annotations

import html as _h
import math
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

PALETTE = ["#1f6feb", "#d1242f", "#1a7f37", "#9a6700", "#8250df", "#bf3989", "#0a7d8c",
           "#6e7781"]

CSS = """
:root{--fg:#1f2328;--muted:#59636e;--bg:#ffffff;--card:#f6f8fa;--line:#d0d7de;--accent:#1f6feb;
--ok:#1a7f37;--bad:#d1242f;--warn:#9a6700}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--fg:#e6edf3;--muted:#9198a1;
--bg:#0d1117;--card:#161b22;--line:#30363d;--accent:#4493f8;--ok:#3fb950;--bad:#f85149;--warn:#d29922}}
:root[data-theme="dark"]{--fg:#e6edf3;--muted:#9198a1;--bg:#0d1117;--card:#161b22;--line:#30363d;
--accent:#4493f8;--ok:#3fb950;--bad:#f85149;--warn:#d29922}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,-apple-system,"Segoe UI",
Roboto,sans-serif}
main{max-width:1180px;margin:0 auto;padding:16px}
h1{font-size:22px;margin:8px 0 2px} h2{font-size:17px;margin:26px 0 6px;border-bottom:1px solid var(--line);
padding-bottom:4px} h3{font-size:14px;margin:14px 0 4px}
.sub{color:var(--muted);margin:0 0 10px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:8px;margin:8px 0}
.tile{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px 10px}
.tile .k{color:var(--muted);font-size:12px} .tile .v{font-size:19px;font-weight:600}
.tile .b{color:var(--muted);font-size:11px}
.tw{overflow-x:auto;margin:6px 0 4px}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{border-bottom:1px solid var(--line);padding:4px 6px;text-align:right;vertical-align:top}
th:first-child,td:first-child{text-align:left} th{color:var(--muted);font-weight:600;white-space:nowrap}
td.l,th.l{text-align:left} .basis{color:var(--muted);font-size:11.5px;text-align:left}
.lab{display:inline-block;font-size:10.5px;border:1px solid var(--line);border-radius:10px;
padding:0 6px;margin:1px 2px 1px 0;color:var(--muted);white-space:nowrap}
.lab.pend{border-color:var(--warn);color:var(--warn)} .ok{color:var(--ok)} .bad{color:var(--bad)}
.charts{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:12px}
figure{margin:0;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px}
figcaption{color:var(--muted);font-size:12px;margin-top:4px}
svg{width:100%;height:auto;display:block} svg text{fill:var(--fg);font-size:11px}
svg .ax{stroke:var(--muted);stroke-width:1} svg .gr{stroke:var(--line);stroke-width:1}
svg .mu{fill:var(--muted)}
.note{color:var(--muted);font-size:12px;margin:4px 0}
footer{color:var(--muted);font-size:11.5px;margin:30px 0 10px;border-top:1px solid var(--line);
padding-top:8px}
"""


def esc(x: Any) -> str:
    return _h.escape(str(x))


def fmt(v: Any, nd: int = 3) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (int, float)):
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return "—"
        a = abs(v)
        if a == 0:
            return "0"
        if a >= 1000:
            return f"{v:,.0f}".replace(",", " ")
        if a >= 100:
            return f"{v:.1f}" if nd >= 3 else f"{v:.0f}"
        if a >= 10:
            return f"{v:.2f}" if nd >= 3 else f"{v:.1f}"
        return f"{v:.{nd}g}"
    return esc(v)


def labels(ls: Iterable[str]) -> str:
    out = []
    for x in ls or []:
        cls = "lab pend" if "pending" in str(x) else "lab"
        out.append(f'<span class="{cls}">{esc(x)}</span>')
    return "".join(out)


def table(head: Sequence[str], rows: Sequence[Sequence[Any]], *, left_cols: int = 1,
          raw: bool = False) -> str:
    h = "".join(f'<th class="{"l" if i < left_cols else ""}">{esc(c)}</th>'
                for i, c in enumerate(head))
    body = []
    for r in rows:
        tds = []
        for i, c in enumerate(r):
            cls = "l" if i < left_cols else ""
            if isinstance(c, tuple) and len(c) == 2 and c[0] == "__html__":
                tds.append(f'<td class="{cls}">{c[1]}</td>')
            else:
                tds.append(f'<td class="{cls}">{fmt(c) if not raw else c}</td>')
        body.append("<tr>" + "".join(tds) + "</tr>")
    return f'<div class="tw"><table><thead><tr>{h}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def H(s: str) -> Tuple[str, str]:
    """Mark a cell as raw HTML."""
    return ("__html__", s)


def tile(k: str, v: str, b: str = "") -> str:
    return f'<div class="tile"><div class="k">{esc(k)}</div><div class="v">{v}</div><div class="b">{b}</div></div>'


# ─────────────────────────────────────────────────────────────────────────────
#  SVG charts
# ─────────────────────────────────────────────────────────────────────────────

def _nice(lo: float, hi: float, n: int = 5) -> List[float]:
    if hi <= lo:
        hi = lo + 1.0
    span = hi - lo
    step = 10 ** math.floor(math.log10(span / n))
    for m in (1, 2, 2.5, 5, 10):
        if span / (m * step) <= n:
            step *= m
            break
    a = math.floor(lo / step) * step
    out = []
    x = a
    while x <= hi + 1e-9 * span:
        out.append(round(x, 10))
        x += step
    return out


def line_chart(series: Sequence[Mapping[str, Any]], *, xlab: str, ylab: str,
               w: int = 520, h: int = 300, ymin: Optional[float] = None,
               ymax: Optional[float] = None, xmin: Optional[float] = None,
               xmax: Optional[float] = None, hlines: Sequence[Tuple[float, str]] = (),
               title: str = "") -> str:
    """series: [{name, x:[], y:[], dash?, color?, marker?}]"""
    xs = [x for s in series for x in s["x"] if x is not None]
    ys = [y for s in series for y in s["y"] if y is not None]
    if not xs or not ys:
        return '<svg viewBox="0 0 10 10"></svg>'
    x0 = min(xs) if xmin is None else xmin
    x1 = max(xs) if xmax is None else xmax
    y0 = (min(ys) if ymin is None else ymin)
    y1 = (max(ys) if ymax is None else ymax)
    for v, _ in hlines:
        y0, y1 = min(y0, v), max(y1, v)
    if y1 == y0:
        y1 = y0 + 1
    pad = 0.05 * (y1 - y0)
    if ymin is None:
        y0 -= pad
    if ymax is None:
        y1 += pad
    L, R, T, B = 58, 12, 22, 42
    pw, ph = w - L - R, h - T - B

    def X(x):
        return L + (x - x0) / (x1 - x0 or 1) * pw

    def Y(y):
        return T + (1 - (y - y0) / (y1 - y0 or 1)) * ph
    o = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{esc(title or ylab)}">']
    for t in _nice(x0, x1):
        if x0 - 1e-9 <= t <= x1 + 1e-9:
            o.append(f'<line class="gr" x1="{X(t):.1f}" y1="{T}" x2="{X(t):.1f}" y2="{T + ph}"/>'
                     f'<text x="{X(t):.1f}" y="{T + ph + 14}" text-anchor="middle" class="mu">{fmt(t, 3)}</text>')
    for t in _nice(y0, y1):
        if y0 - 1e-9 <= t <= y1 + 1e-9:
            o.append(f'<line class="gr" x1="{L}" y1="{Y(t):.1f}" x2="{L + pw}" y2="{Y(t):.1f}"/>'
                     f'<text x="{L - 5}" y="{Y(t) + 4:.1f}" text-anchor="end" class="mu">{fmt(t, 3)}</text>')
    o.append(f'<line class="ax" x1="{L}" y1="{T + ph}" x2="{L + pw}" y2="{T + ph}"/>'
             f'<line class="ax" x1="{L}" y1="{T}" x2="{L}" y2="{T + ph}"/>')
    o.append(f'<text x="{L + pw / 2}" y="{h - 6}" text-anchor="middle">{esc(xlab)}</text>'
             f'<text x="14" y="{T + ph / 2}" text-anchor="middle" transform="rotate(-90 14 {T + ph / 2})">{esc(ylab)}</text>')
    for v, name in hlines:
        o.append(f'<line x1="{L}" y1="{Y(v):.1f}" x2="{L + pw}" y2="{Y(v):.1f}" stroke="#9a6700" '
                 f'stroke-dasharray="4 3"/><text x="{L + pw - 2}" y="{Y(v) - 3:.1f}" text-anchor="end" '
                 f'class="mu">{esc(name)}</text>')
    lx = L + 6
    for i, s in enumerate(series):
        c = s.get("color") or PALETTE[i % len(PALETTE)]
        pts = [(X(x), Y(y)) for x, y in zip(s["x"], s["y"]) if x is not None and y is not None]
        if not pts:
            continue
        dash = ' stroke-dasharray="5 4"' if s.get("dash") else ""
        if s.get("marker_only"):
            for px, py in pts:
                o.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="3.5" fill="{c}"/>')
        else:
            d = " ".join(f"{'M' if k == 0 else 'L'}{px:.1f},{py:.1f}" for k, (px, py) in enumerate(pts))
            o.append(f'<path d="{d}" fill="none" stroke="{c}" stroke-width="2"{dash}/>')
            if s.get("marker"):
                for px, py in pts:
                    o.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="2.6" fill="{c}"/>')
        ly = T + 12 + 14 * i
        o.append(f'<rect x="{lx + pw - 190}" y="{ly - 8}" width="10" height="3" fill="{c}"/>'
                 f'<text x="{lx + pw - 176}" y="{ly - 4}">{esc(s["name"])}</text>')
    o.append("</svg>")
    return "".join(o)


def heat_chart(xs: Sequence[float], ys: Sequence[float], z: Sequence[Sequence[Optional[float]]], *,
               xlab: str, ylab: str, zlab: str, zmin: float, zmax: float,
               overlay: Sequence[Mapping[str, Any]] = (), w: int = 520, h: int = 320) -> str:
    """Cell map z[j][i] at (xs[i], ys[j]) — cells, not contours (no smoothing)."""
    L, R, T, B = 58, 70, 18, 42
    pw, ph = w - L - R, h - T - B
    x0, x1 = min(xs), max(xs)
    y0, y1 = 0.0, max(ys) * 1.05

    def X(x):
        return L + (x - x0) / (x1 - x0 or 1) * pw

    def Y(y):
        return T + (1 - (y - y0) / (y1 - y0 or 1)) * ph

    def col(v):
        t = min(max((v - zmin) / (zmax - zmin or 1), 0.0), 1.0)
        # perceptual-ish ramp: dark blue -> teal -> yellow
        stops = [(0.0, (49, 54, 149)), (0.35, (69, 117, 180)), (0.6, (116, 196, 118)),
                 (0.85, (254, 224, 139)), (1.0, (215, 48, 39))]
        for (a, ca), (b, cb) in zip(stops, stops[1:]):
            if t <= b:
                u = (t - a) / (b - a)
                return "rgb(%d,%d,%d)" % tuple(int(ca[k] + u * (cb[k] - ca[k])) for k in range(3))
        return "rgb(215,48,39)"
    o = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{esc(zlab)}">']
    nx, ny = len(xs), len(ys)
    for j in range(ny):
        for i in range(nx):
            v = z[j][i]
            if v is None:
                continue
            xl = X((xs[i] + xs[i - 1]) / 2) if i else X(xs[0])
            xr = X((xs[i] + xs[i + 1]) / 2) if i < nx - 1 else X(xs[-1])
            yt = Y((ys[j] + ys[j + 1]) / 2) if j < ny - 1 else Y(ys[-1])
            yb = Y((ys[j] + ys[j - 1]) / 2) if j else Y(ys[0])
            o.append(f'<rect x="{xl:.1f}" y="{yt:.1f}" width="{max(xr - xl, 0.5):.1f}" '
                     f'height="{max(yb - yt, 0.5):.1f}" fill="{col(v)}"><title>{fmt(v)}</title></rect>')
    for t in _nice(x0, x1):
        if x0 <= t <= x1:
            o.append(f'<text x="{X(t):.1f}" y="{T + ph + 14}" text-anchor="middle" class="mu">{fmt(t, 3)}</text>')
    for t in _nice(y0, y1):
        if y0 <= t <= y1:
            o.append(f'<line class="gr" x1="{L - 3}" y1="{Y(t):.1f}" x2="{L}" y2="{Y(t):.1f}"/>'
                     f'<text x="{L - 5}" y="{Y(t) + 4:.1f}" text-anchor="end" class="mu">{fmt(t, 3)}</text>')
    o.append(f'<line class="ax" x1="{L}" y1="{T + ph}" x2="{L + pw}" y2="{T + ph}"/>'
             f'<line class="ax" x1="{L}" y1="{T}" x2="{L}" y2="{T + ph}"/>')
    o.append(f'<text x="{L + pw / 2}" y="{h - 6}" text-anchor="middle">{esc(xlab)}</text>'
             f'<text x="14" y="{T + ph / 2}" text-anchor="middle" transform="rotate(-90 14 {T + ph / 2})">{esc(ylab)}</text>')
    for k, s in enumerate(overlay):
        pts = [(X(x), Y(y)) for x, y in zip(s["x"], s["y"]) if y is not None]
        if pts:
            d = " ".join(f"{'M' if q == 0 else 'L'}{px:.1f},{py:.1f}" for q, (px, py) in enumerate(pts))
            dash = ' stroke-dasharray="5 4"' if s.get("dash") else ""
            o.append(f'<path d="{d}" fill="none" stroke="{s.get("color", "#000")}" '
                     f'stroke-width="2"{dash}/>')
            o.append(f'<text x="{pts[-1][0] - 4:.1f}" y="{pts[-1][1] - 5:.1f}" text-anchor="end" '
                     f'font-size="10">{esc(s["name"])}</text>')
    # colour bar
    for q in range(40):
        v = zmin + (zmax - zmin) * q / 39
        yb = T + ph - (q + 1) * ph / 40
        o.append(f'<rect x="{L + pw + 14}" y="{yb:.1f}" width="12" height="{ph / 40 + 0.5:.1f}" fill="{col(v)}"/>')
    o.append(f'<text x="{L + pw + 30}" y="{T + 8}" class="mu">{fmt(zmax)}</text>'
             f'<text x="{L + pw + 30}" y="{T + ph}" class="mu">{fmt(zmin)}</text>'
             f'<text x="{L + pw + 30}" y="{T + ph / 2}" class="mu">{esc(zlab)}</text>')
    o.append("</svg>")
    return "".join(o)


def bar_chart(cats: Sequence[str], stacks: Sequence[Mapping[str, Any]], *, ylab: str,
              w: int = 520, h: int = 300) -> str:
    """Stacked bars: stacks = [{name, values:[per cat]}]."""
    L, R, T, B = 58, 12, 22, 70
    pw, ph = w - L - R, h - T - B
    tot = [sum((s["values"][i] or 0.0) for s in stacks) for i in range(len(cats))]
    y1 = max(tot + [1e-9]) * 1.1

    def Y(y):
        return T + (1 - y / y1) * ph
    bw = pw / max(len(cats), 1)
    o = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{esc(ylab)}">']
    for t in _nice(0.0, y1):
        if t <= y1:
            o.append(f'<line class="gr" x1="{L}" y1="{Y(t):.1f}" x2="{L + pw}" y2="{Y(t):.1f}"/>'
                     f'<text x="{L - 5}" y="{Y(t) + 4:.1f}" text-anchor="end" class="mu">{fmt(t, 3)}</text>')
    for i, c in enumerate(cats):
        acc = 0.0
        x = L + i * bw + 0.18 * bw
        for k, s in enumerate(stacks):
            v = s["values"][i] or 0.0
            if v <= 0:
                continue
            o.append(f'<rect x="{x:.1f}" y="{Y(acc + v):.1f}" width="{0.64 * bw:.1f}" '
                     f'height="{Y(acc) - Y(acc + v):.1f}" fill="{PALETTE[k % len(PALETTE)]}">'
                     f'<title>{esc(s["name"])}: {fmt(v)}</title></rect>')
            acc += v
        o.append(f'<text x="{x + 0.32 * bw:.1f}" y="{Y(acc) - 3:.1f}" text-anchor="middle" '
                 f'font-size="10">{fmt(acc, 3)}</text>')
        o.append(f'<text x="{x + 0.32 * bw:.1f}" y="{T + ph + 12}" text-anchor="end" font-size="10" '
                 f'transform="rotate(-30 {x + 0.32 * bw:.1f} {T + ph + 12})">{esc(c)}</text>')
    o.append(f'<line class="ax" x1="{L}" y1="{T + ph}" x2="{L + pw}" y2="{T + ph}"/>')
    o.append(f'<text x="14" y="{T + ph / 2}" text-anchor="middle" transform="rotate(-90 14 {T + ph / 2})">{esc(ylab)}</text>')
    for k, s in enumerate(stacks):
        o.append(f'<rect x="{L + 8 + 104 * (k % 4)}" y="{4 + 12 * (k // 4)}" width="9" height="9" '
                 f'fill="{PALETTE[k % len(PALETTE)]}"/><text x="{L + 20 + 104 * (k % 4)}" '
                 f'y="{12 + 12 * (k // 4)}" font-size="10">{esc(s["name"])}</text>')
    o.append("</svg>")
    return "".join(o)


def figure(svg: str, cap: str) -> str:
    return f"<figure>{svg}<figcaption>{esc(cap)}</figcaption></figure>"


def page(title: str, body: str, desc: str = "") -> str:
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<title>{esc(title)}</title><meta name=\"description\" content=\"{esc(desc)}\">"
            f"<style>{CSS}</style></head><body><main>{body}</main></body></html>")
