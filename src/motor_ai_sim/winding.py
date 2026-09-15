"""The winding CONNECTION label, and the one place it is parsed.

A three-phase winding of C coils per phase can be wired as any factor pair
``n_series x n_parallel = C``.  The label the UI, the presets and the catalog all
store is a string — ``"4S"`` (all series), ``"4P"`` (all parallel), ``"2S-2P"``
(series-parallel), plus the legacy ``"2P2S"`` spelling.

Only ``n_parallel`` reaches the physics, and it reaches it as a DIVIDER: the FEM
sees ``I_coil = I_phase / n_parallel``.  Getting it wrong is a factor-n_parallel
error in the coil MMF and therefore in the torque — measured at +95.7 % on
``ciano20_150_35`` (its stored ``2S-2P`` unapplied), and -1.1 % against its
stored torque once applied (docs/SOLVER_TRIALS_2026-07-30.md F3).

The parser lived in ``api.py`` only, so the solver had no way to honour a
connection that was not already written into the shared config.  It lives here
now: one definition, importable without dragging FastAPI in.

``wire_parallel`` (STRANDS IN HAND) is the second, independent divider and it
lives in the GEOMETRY block, not here: winding k wires in hand per turn leaves
the slot holding the same ``num_wires_per_slot`` physical conductors (same
copper, same fill, same CAD) but wires them as ``num_wires_per_slot / k`` SERIES
turns of k strands each.  Electrically that is exactly an extra factor on the
parallel-path count — turns/k and k strands in parallel — so every physics
consumer uses ``n_parallel x wire_parallel`` and nothing else has to know:
psi and EMF /k, Kt and T /k, KV xk, R_phase and Ld/Lq /k^2, J per strand /k.

``wire_split`` (STRIPS PER WIRE ROW) is the third, and it is GEOMETRY FIRST.  N
strips of ``wire_width`` each — ``wire_width`` IS one strip, the user sets it
himself (2026-09-08: *"сделай ширину провода 4,5 мм, слот станет чуть больше, я
бы гап между проводами сделал 2·Wire Spacing X"*) — are laid SIDE BY SIDE across
the slot width with ``2 x wire_spacing_x`` of insulation between them.  So the
wire footprint, and with it the slot the CAD cuts, GROWS::

    footprint = N * wire_width + (N - 1) * 2 * wire_spacing_x

That is ``winding_footprint_mm``.  The strips are drawn, meshed and solved as
separate conductors, one imposed net current each, so the slot holds
``num_wires_per_slot x N`` CONDUCTORS (``conductors_per_slot``).

The N strips of a row are CONSECUTIVE SERIES TURNS — always.  There was a
``wire_split_series`` flag for a few hours on 2026-09-08 offering the parallel
reading too; the user removed it the same day (*"wire_split_series можно убрать —
нам всегда будет нужно только последовательное подключение этих двух катушек;
при параллельном подключении возникнут компенсационные токи между ними"*):
strips lying side by side in a slot see different leakage flux, so wiring them in
parallel drives circulating current between them, which this model does not
solve and which the machine does not want.  So::

    turns/coil xN     psi_PM, back-EMF, V xN     Kt, torque xN     KV /N
    R_phase, Ld, Lq xN^2      P_cu(DC) xN^2      copper area, mass xN
    I per strip = I_phase / n_parallel / wire_parallel  (the FULL branch current)

The split never divides current: every strip carries the whole branch current,
which is why ``n_parallel_effective`` does NOT carry N.  The same MMF (and
therefore the same torque) comes back at ``I_phase / N``.  The current is the
user's to set on the Electromagnetic tab — nothing here rescales it silently;
``turns_per_coil``, ``R_phase_ohm`` and ``KV`` are reported so the multiplied
voltage has a visible cause.

Only the SOLVED AC copper is measured on the STRIP rather than on the bar: each
conductor is ``wire_width`` wide instead of ``N x wire_width``, so the
width-direction proximity term falls as N^2 — that is what splitting a bar buys.

``N = 1`` is the unsplit machine, bit for bit: one strip per row, no gap, no
extra turn, no extra conductor.

Until 2026-09-08 ``wire_split`` was ELECTRICAL ONLY: strips of
``wire_width / N`` with no CAD behind them, modelled as ideally transposed and
invisible to the mesh.  That reading is retired — ``wire_width`` is now the
strip, not the bar.
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Optional, Tuple

__all__ = ["parse_connection", "n_parallel_from_connection", "connection_label",
           "wire_parallel_from_geo", "wire_split_from_geo", "turns_per_coil",
           "n_parallel_effective", "conductors_per_slot", "strip_width_mm",
           "strip_gap_mm", "winding_footprint_mm", "STRIP_GAP_FACTOR"]

#: How many ``wire_spacing_x`` layers sit between two strips of the SAME row.
#: TWO, not one (user 2026-09-08: *"я бы гап между проводами сделал 2·Wire
#: Spacing X"*): ``wire_spacing_x`` is the half-gap the CAD already leaves on
#: each side of a wire, so two strips facing each other bring one each.  It
#: lives here as a named constant because the polygon builder, the slot cutter,
#: the validator's bound and the thermal template all have to use the SAME gap
#: or the pocket and the copper in it stop agreeing.
STRIP_GAP_FACTOR = 2.0

_PATTERNS = (
    (re.compile(r"^(\d+)S-(\d+)P$"), lambda m: (int(m.group(2)), int(m.group(1)))),
    (re.compile(r"^(\d+)P(\d+)S$"), lambda m: (int(m.group(1)), int(m.group(2)))),
    (re.compile(r"^(\d+)S$"),       lambda m: (1, int(m.group(1)))),
    (re.compile(r"^(\d+)P$"),       lambda m: (int(m.group(1)), 1)),
)


def parse_connection(conn: str) -> Tuple[int, int]:
    """``"2S-2P"`` -> ``(n_parallel, n_series)``.

    Raises ``ValueError`` on anything it cannot read.  It RAISES rather than
    falling back to 1 on purpose: a connection nobody can parse silently driving
    the whole phase current through one path is exactly the failure this
    function exists to make impossible.
    """
    c = (conn or "").strip()
    for pat, take in _PATTERNS:
        m = pat.match(c)
        if m:
            n_par, n_ser = take(m)
            if n_par < 1 or n_ser < 1:
                raise ValueError(f"connection {conn!r}: counts must be >= 1")
            return n_par, n_ser
    raise ValueError(
        f"unknown winding connection {conn!r}; expected forms like "
        f"'4S' (all series), '4P' (all parallel), '2S-2P' (series-parallel)")


def n_parallel_from_connection(conn: str) -> int:
    """Parallel paths in ``conn``.  Raises ``ValueError`` on an unreadable label."""
    return parse_connection(conn)[0]


def wire_parallel_from_geo(geo: Optional[Mapping[str, Any]]) -> int:
    """``geometry.wire_parallel`` — strands in hand per turn.  Absent = 1.

    RAISES ``ValueError`` when the value is not a positive integer, or when it
    does not divide ``num_wires_per_slot`` exactly.  A silent round would build
    a machine nobody asked for: 7 wires wound 2-in-hand is 3.5 turns, and the
    only honest answers are "3 turns and a spare wire" or "refuse".  It refuses,
    naming BOTH numbers, because the fix is always to change one of them.
    """
    g = dict(geo or {})
    raw = g.get("wire_parallel", 1)
    if raw is None or raw == "":
        return 1
    try:
        k_f = float(raw)
    except (TypeError, ValueError):
        raise ValueError(
            f"wire_parallel must be a positive integer (strands in hand); "
            f"got {raw!r}")
    k = int(round(k_f))
    if k < 1 or abs(k_f - k) > 1e-9:
        raise ValueError(
            f"wire_parallel must be a positive integer (strands in hand); "
            f"got {raw!r}")
    if k == 1:
        return 1
    nw_raw = g.get("num_wires_per_slot")
    if nw_raw is None:
        return k
    try:
        nw = int(round(float(nw_raw)))
    except (TypeError, ValueError):
        raise ValueError(
            f"num_wires_per_slot must be a positive integer to wind "
            f"wire_parallel={k} strands in hand; got {nw_raw!r}")
    if nw % k:
        raise ValueError(
            f"wire_parallel={k} does not divide num_wires_per_slot={nw}: "
            f"{nw} wires wound {k}-in-hand is {nw / k:.4g} turns per coil, "
            f"which is not a winding.  Use a wire_parallel that divides {nw} "
            f"(e.g. {', '.join(str(d) for d in sorted(_divisors(nw))[:8])}) or "
            f"change num_wires_per_slot to a multiple of {k}.")
    return k


def _divisors(n: int):
    return {d for d in range(1, max(1, n) + 1) if n % d == 0}


def wire_split_from_geo(geo: Optional[Mapping[str, Any]]) -> int:
    """``geometry.wire_split`` — N strips per wire row, side by side.  Absent = 1.

    RAISES ``ValueError`` on anything that is not a positive integer.  It does
    NOT have to divide ``num_wires_per_slot`` (unlike ``wire_parallel``): the
    strips are laid across ONE wire's row, so N of them per row is a winding
    whatever the row count is.
    """
    g = dict(geo or {})
    raw = g.get("wire_split", 1)
    if raw is None or raw == "":
        return 1
    try:
        n_f = float(raw)
    except (TypeError, ValueError):
        raise ValueError(
            f"wire_split must be a positive integer (strips per wire row); "
            f"got {raw!r}")
    n = int(round(n_f))
    if n < 1 or abs(n_f - n) > 1e-9:
        raise ValueError(
            f"wire_split must be a positive integer (strips per wire row); "
            f"got {raw!r}")
    return n


def conductors_per_slot(geo: Optional[Mapping[str, Any]]) -> int:
    """PHYSICAL conductors the CAD lays in one slot side.

    ``num_wires_per_slot x wire_split``.  The strips are drawn, meshed and
    current-constrained one by one, so this is the polygon count per slot side
    AND the ampere-turn multiplier the winding source is normalised by.  Since
    the strips are SERIES turns each carrying the full branch current, this is
    also exactly ``turns_per_coil x wire_parallel``.
    """
    g = dict(geo or {})
    nw = int(round(float(g.get("num_wires_per_slot", 0) or 0)))
    return nw * wire_split_from_geo(g)


def strip_width_mm(geo: Optional[Mapping[str, Any]]) -> float:
    """Width [mm] of ONE drawn conductor — which is ``wire_width`` itself.

    The split does NOT subdivide ``wire_width`` any more (it did until
    2026-09-08).  ``wire_width`` IS the strip: the user halves it himself when
    he splits a bar in two, and the slot then grows to hold both halves plus the
    insulation between them (``winding_footprint_mm``).  Kept as a named
    function because "the conductor J and R are measured on" is asked from four
    places and must be one answer.
    """
    g = dict(geo or {})
    return float(g.get("wire_width", 0.0) or 0.0)


def strip_gap_mm(geo: Optional[Mapping[str, Any]]) -> float:
    """Insulation [mm] between two strips of the SAME row = ``2 x wire_spacing_x``.

    0.0 when there is no split, so an unsplit machine never inserts a gap.
    """
    g = dict(geo or {})
    if wire_split_from_geo(g) <= 1:
        return 0.0
    return STRIP_GAP_FACTOR * float(g.get("wire_spacing_x", 0.0) or 0.0)


def winding_footprint_mm(geo: Optional[Mapping[str, Any]]) -> float:
    """Tangential space one turn's strips OCCUPY [mm], insulation included.

    ``N x wire_width + (N - 1) x 2 x wire_spacing_x``.  Everything that places
    something against the far edge of a wire — the slot cutter, the enamel
    envelope, the −x column's origin, the thermal template's coil column — has
    to use THIS, or the outermost strip is drawn inside the tooth.  Equal to
    ``wire_width`` at ``wire_split = 1``, which is what keeps an unsplit machine
    bit-identical.
    """
    g = dict(geo or {})
    n = wire_split_from_geo(g)
    w = float(g.get("wire_width", 0.0) or 0.0)
    if n <= 1:
        return w
    return n * w + (n - 1) * strip_gap_mm(g)


def turns_per_coil(geo: Optional[Mapping[str, Any]]) -> int:
    """SERIES turns of one coil.

    ``(num_wires_per_slot / wire_parallel) x wire_split``.  The slot holds
    ``num_wires_per_slot`` wire ROWS — that is geometry, and the coating and the
    copper mass are built on it — each row carrying ``wire_split`` strips, and
    those strips are consecutive SERIES turns.  This is the ELECTRICAL turn
    count the MMF, psi, Kt and R are built on.
    """
    g = dict(geo or {})
    k = wire_parallel_from_geo(g)
    nw = int(round(float(g.get("num_wires_per_slot", 0) or 0)))
    rows = (nw // k if k else nw)
    return rows * wire_split_from_geo(g)


def n_parallel_effective(n_parallel: Any,
                         geo: Optional[Mapping[str, Any]]) -> int:
    """The divider every physics path uses: paths x strands in hand.

    ``n_parallel x wire_parallel``.

    ``n_parallel`` stays what the CONNECTION label says (it is validated against
    the coil count and must not be moved); the strands in hand multiply it at
    the point of use.  I_strand = I_phase / this; psi and the coil MMF carry the
    same divider, which is what makes EMF/Kt scale as 1/k and R/Ld/Lq as 1/k^2
    for the strands.

    ``wire_split`` is deliberately NOT here.  The strips of a row are SERIES
    turns: they add no parallel path and divide no current, every one carries
    this same branch current, and the split multiplies ``turns_per_coil``
    instead.  (Wiring them in parallel — the reading this function carried for a
    few hours on 2026-09-08 — would put circulating current between strips that
    link different leakage flux, which is why the user removed it.)
    """
    npar = max(1, int(n_parallel or 1))
    return npar * wire_parallel_from_geo(geo)


def connection_label(n_series: int, n_parallel: int) -> str:
    """Canonical label: all-series ``'{C}S'``, all-parallel ``'{C}P'``, else
    ``'{nS}S-{nP}P'``."""
    if n_parallel <= 1:
        return f"{int(n_series)}S"
    if n_series <= 1:
        return f"{int(n_parallel)}P"
    return f"{int(n_series)}S-{int(n_parallel)}P"
