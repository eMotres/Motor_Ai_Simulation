"""Loss models that turn a captured B(t) history into watts.

One implementation per model, shared by every element order. The iron loss used
to be written twice — once in the P1 branch, once in P2 — identical line for
line except for how dB/dt was estimated. Duplicated physics is how a fix reaches
one path and misses the other, which is exactly what happened here: P2 spent a
while reporting ZERO core loss because its copy sat behind a flag the P1 copy
did not have. Passing the derivative in as a callable keeps the one genuine
difference and removes the copy.
"""
from __future__ import annotations

import math
from typing import Any, Callable, Optional, Sequence, Tuple

import numpy as np

# 2*pi^2 — the classical-eddy denominator in the Bertotti form below.
TWO_PI_SQ = 2.0 * math.pi ** 2

# Fill factor used when a steel declares none. Every steel in the shipped library
# declares its own (B15AHV950M is 0.925), so this is a guard, not a knob — and it
# is ONE value: the same constant appeared as 0.95 in two places and 0.97 in a
# third, which is the sort of split that quietly moves numbers.
DEFAULT_STACKING_FACTOR = 0.97


def iron_loss_series(
    hist_x: Sequence,
    hist_y: Sequence,
    idx: np.ndarray,
    areas: np.ndarray,
    material: Any,
    stack_length_m: float,
    f_elec_hz: float,
    n_frames: int,
    ddt: Callable[[np.ndarray], np.ndarray],
    bertotti: Callable[[Any], Tuple[float, float, float]],
) -> Tuple[np.ndarray, float]:
    """Bertotti iron loss from a per-element B(t) history.

    Returns ``(P_classical(t), P_hysteresis_and_excess)`` — the first ripples
    with the teeth passing, the second is a per-cycle quantity and therefore flat.

        P/V = k_h*f*B^2  +  k_c/(2*pi^2) * <(dB/dt)^2>  +  k_e*f^1.5*B^1.5

    The coefficients come from the material's MEASURED loss curves when it has
    them (relative-error-weighted NNLS over every (f, B) point), falling back to
    the YAML k_h/k_c/k_e. Real curves give real loss.

    ``ddt`` is the caller's time-derivative operator, and it is the ONLY thing
    that differs between element orders: P1 needs a smoothed angle-derivative
    because the sliding band's node re-pairing adds a frame-to-frame jitter that
    a raw difference amplifies (the loss tripled going from 24 to 72 steps),
    while the P2 field is smooth enough for a plain central difference.

    ``bertotti`` is injected rather than imported so this module stays free of
    the materials library and can be tested with hand-written coefficients.
    """
    if material is None or idx.size == 0 or len(hist_x) == 0:
        return np.zeros(n_frames), 0.0
    X = np.asarray(hist_x)
    Y = np.asarray(hist_y)
    if X.size == 0 or np.asarray(hist_x[0]).size == 0:
        return np.zeros(n_frames), 0.0

    kh, kc, ke = bertotti(material)
    sf = float(getattr(material, "stacking_factor", DEFAULT_STACKING_FACTOR))
    # Only the steel carries loss; the inter-laminate insulation is dead volume.
    vol = areas[idx] * stack_length_m * sf

    dX = ddt(X)
    dY = ddt(Y)
    classical = (kc / TWO_PI_SQ) * np.sum((dX ** 2 + dY ** 2) * vol[None, :], axis=1)

    # Peak-to-peak / 2 per element over the captured window — the AC amplitude
    # the per-cycle terms are defined on.
    Bac2 = (((X.max(0) - X.min(0)) * 0.5) ** 2
            + ((Y.max(0) - Y.min(0)) * 0.5) ** 2)
    per_cycle = float(np.sum(
        (kh * f_elec_hz * Bac2
         + ke * f_elec_hz ** 1.5 * np.power(np.maximum(Bac2, 0.0), 0.75)) * vol))
    return classical, per_cycle


def central_difference(dt_s: float) -> Callable[[np.ndarray], np.ndarray]:
    """Periodic central difference — for a field already smooth in time (P2)."""
    def _ddt(X: np.ndarray) -> np.ndarray:
        return (np.roll(X, -1, 0) - np.roll(X, 1, 0)) / (2.0 * dt_s)
    return _ddt


def copper_ac_dims(geo: dict, coil_temp_c: float, f_elec_hz: float,
                   rho_cu_20: float, alpha_cu: float, mu0: float
                   ) -> Tuple[float, float, float]:
    """Conductor dimensions the proximity loss sees, and the conductivity.

    Returns ``(sigma, d_radial, d_tangential)`` in SI.

    Two caps, whichever bites first:
      * ``wire_split`` — the wide flat bar is wound as N insulated, transposed
        strips across its WIDTH, so the width-direction loops see w/N and that
        loss term falls as N^2. Assumes ideal transposition (no circulating
        current between strips).
      * two skin depths — beyond that the field does not reach the middle of the
        conductor and a larger dimension buys no extra loss.
    """
    rho = rho_cu_20 * (1.0 + alpha_cu * (float(coil_temp_c) - 20.0))
    omega = 2.0 * math.pi * max(1e-6, float(f_elec_hz))
    delta = math.sqrt(2.0 * rho / (omega * mu0))
    n_split = max(1, int(round(float(geo.get("wire_split", 1) or 1))))
    d_r = min(float(geo.get("wire_width", 5.0)) * 1e-3 / n_split, 2.0 * delta)
    d_t = min(float(geo.get("wire_height", 0.8)) * 1e-3, 2.0 * delta)
    return 1.0 / rho, d_r, d_t


def proximity_loss_series(
    hist_x: Sequence,
    hist_y: Sequence,
    idx: np.ndarray,
    centroids: np.ndarray,
    areas: np.ndarray,
    sigma: float,
    d_for_Br: float,
    d_for_Bt: float,
    stack_length_m: float,
    n_frames: int,
    ddt: Callable[[np.ndarray], np.ndarray],
    scale: float = 1.0,
    post: Optional[Callable[[np.ndarray], np.ndarray]] = None,
) -> Tuple[list, float]:
    """Proximity/skin loss in a SOLID conductor, field split by direction.

        P = sigma/12 * sum( d_r^2 * (dB_r/dt)^2 + d_t^2 * (dB_t/dt)^2 ) * V

    The split matters. Pairing each field component with the conductor dimension
    PERPENDICULAR to it — B_r with the tangential width, B_theta (slot leakage)
    with the radial height — avoids the single-d slab over-count: a tall thin bar
    barely sees the tangential leakage field, and treating it as a cube says
    otherwise.

    ``scale`` multiplies up from the modelled sector to the whole machine.
    ``post`` is the caller's outlier treatment (P1 clips to median +- 5 MAD to
    catch a single bad frame; P2's field is smooth and only needs a floor at 0).
    """
    if sigma <= 0.0 or idx.size == 0 or len(hist_x) == 0:
        return [0.0] * n_frames, 0.0
    X = np.asarray(hist_x)
    Y = np.asarray(hist_y)
    if X.size == 0 or np.asarray(hist_x[0]).size == 0:
        return [0.0] * n_frames, 0.0

    r = np.hypot(centroids[0], centroids[1])
    r = np.where(r < 1e-9, 1e-9, r)
    ux = (centroids[0] / r)[None, :]
    uy = (centroids[1] / r)[None, :]
    Br = X * ux + Y * uy                       # radial
    Bt = -X * uy + Y * ux                      # tangential
    vol = areas[idx] * stack_length_m
    Pt = (sigma / 12.0) * np.sum(
        (d_for_Br ** 2 * ddt(Br) ** 2 + d_for_Bt ** 2 * ddt(Bt) ** 2)
        * vol[None, :], axis=1) * scale
    Pt = (post or (lambda a: np.maximum(a, 0.0)))(Pt)
    return Pt.tolist(), float(np.mean(Pt))


def declip(a: np.ndarray) -> np.ndarray:
    """Safety net: clip any residual single-frame outlier to median±5·MAD.

    The P1 path's outlier treatment, passed to ``proximity_loss_series`` as
    ``post`` and applied by hand to every other P1 loss series. It is here
    rather than inline in the solver so that "which series are de-clipped" is
    answerable by grep instead of by reading a 3000-line function.
    """
    a = np.asarray(a, float)
    if a.size < 5:
        return a
    med = float(np.median(a)); mad = float(np.median(np.abs(a - med)))
    if mad <= 0:
        return a
    return np.clip(a, max(0.0, med - 5 * mad), med + 5 * mad)


def loss_density_map(
    *,
    n_stator_elems: int,
    n_elems: int,
    hist_sx, hist_sy, hist_rx, hist_ry, hist_mx, hist_my, hist_cx, hist_cy,
    iron_s_idx: np.ndarray, iron_r_idx: np.ndarray,
    mag_idx: np.ndarray, coil_idx: np.ndarray,
    areas_s: np.ndarray, areas_r: np.ndarray, coil_centroids: np.ndarray,
    steel_s: Any, steel_r: Any, bertotti: Callable[[Any], Tuple[float, float, float]],
    f_elec_hz: float, stack_length_m: float, sector_scale: float,
    P_fe_avg: float, P_mag_avg: float, P_cu_dc: float, P_cu_ac_avg: float,
    sigma_cu: float, d_cu_r: float, d_cu_t: float,
    ddt: Callable,
    solved_dens: Optional[np.ndarray] = None,
    solved_groups: Sequence[str] = (),
    solved_elems: Optional[dict] = None,
    P_cu_end_winding_W: float = 0.0,
    log_line: Optional[Callable[[str], None]] = None,
) -> Tuple[np.ndarray, str, list]:
    """Per-element loss DENSITY (W/m³) for the Ansys-style spatial map.

    Returns ``(density, label, unmodelled)`` — the label says, in the picture's
    own words, which component came from where, and ``unmodelled`` lists the
    material classes no model produced a value for, so the view can leave them
    BLANK instead of painting them the bottom of the scale (which on a loss map
    is what air looks like, i.e. "no loss here").  ``"air"`` is ALWAYS in that
    list — there is no air-loss model in a 2-D magnetic solve (σ=0, no
    hysteresis, no windage), so the air gap must be blank, never band 0.

    TWO sources, never mixed inside one component:

    * ``solved_dens`` — the cycle-averaged per-element σE² of the COUPLED
      σ·∂A/∂t solve, for whichever of ``solved_groups`` ⊆ {cu, mag, shaft} the
      run actually solved.  This is the density itself, not a shape: it is
      taken UNRENORMALISED, because renormalising a measured quantity to a
      number derived from it can only add error.  It is also the only way the
      map can show the corner/edge crowding an Ansys Total-Loss plot shows —
      the eddy current concentrates where the conductor faces the changing
      field, and no per-body average knows that.
    * everything else — the modelled shapes (Bertotti for iron always; the slab
      |dB/dt|² magnet term and the proximity copper term when the coupled solve
      did not run), each NORMALISED so its volume-integral equals the reported
      component loss.  A shape normalised to a total is smooth by construction;
      the label says so.

    ``P_cu_end_winding_W`` is the DC copper loss OUTSIDE the modelled plane
    (the end turns).  It is real, the sidebar reports it, and the 2-D map has
    nowhere honest to put it — so it is spread uniformly over the copper and
    called out in the label, rather than being folded into the solved σE²
    shape as if the end turns crowded like the active length does.

    Element order matches the field snapshot: [stator-half | rotor-half].
    """
    _nst_e = int(n_stator_elems)
    _dens = np.zeros(int(n_elems))
    _solved = {str(g) for g in (solved_groups or ())}
    _sel = solved_elems or {}
    _parts = []                       # label fragments, in draw order

    def _log(msg: str) -> None:
        if log_line is not None:
            log_line(msg)

    # Element areas in the SNAPSHOT's global order, so a cross-check can be
    # written once against global element ids instead of once per half.
    _areas_all = np.concatenate([np.asarray(areas_s, float),
                                 np.asarray(areas_r, float)])

    def _integ_glob(glob_idx) -> float:
        """Volume integral [W, machine] of the map over global element ids."""
        if glob_idx is None or np.size(glob_idx) == 0:
            return 0.0
        gi = np.asarray(glob_idx, int)
        return float(np.sum(_dens[gi] * _areas_all[gi])) \
            * stack_length_m * sector_scale

    def _mean_sq_ddt(hx, hy, qp=None):              # time-avg |dB/dt|² per elem
        dX = ddt(np.asarray(hx), qp)
        dY = ddt(np.asarray(hy), qp)
        return np.mean(dX ** 2 + dY ** 2, axis=0)

    def _bac2(hx, hy):                              # (½ peak-peak)² per elem
        X = np.asarray(hx); Y = np.asarray(hy)
        return (((X.max(0) - X.min(0)) * 0.5) ** 2
                + ((Y.max(0) - Y.min(0)) * 0.5) ** 2)

    def _norm_into(local_idx, shape_e, areas_half, base, P_target_W):
        if local_idx.size == 0 or shape_e.size == 0 or P_target_W <= 0:
            return
        integ = float(np.sum(shape_e * areas_half[local_idx])) * stack_length_m * sector_scale
        if integ > 1e-30:
            _dens[base + local_idx] += shape_e * (P_target_W / integ)

    # Iron — stator + rotor share one Bertotti total (P_fe_avg).
    def _iron_shape(hx, hy, idx, mat, qp=None):
        if (mat is None or idx.size == 0 or not np.size(hx)
                or np.asarray(hx[0]).size == 0):
            return np.zeros(idx.size)
        kh, kc, ke = bertotti(mat)
        b2 = _bac2(hx, hy)
        return (kh * f_elec_hz * b2
                + ke * f_elec_hz ** 1.5 * np.power(np.maximum(b2, 0.0), 0.75)
                + (kc / TWO_PI_SQ) * _mean_sq_ddt(hx, hy, qp))
    _sh_is = _iron_shape(hist_sx, hist_sy, iron_s_idx, steel_s)
    _sh_ir = _iron_shape(hist_rx, hist_ry, iron_r_idx, steel_r)
    _integ_fe = ((float(np.sum(_sh_is * areas_s[iron_s_idx])) if iron_s_idx.size else 0.0)
                 + (float(np.sum(_sh_ir * areas_r[iron_r_idx])) if iron_r_idx.size else 0.0)
                 ) * stack_length_m * sector_scale
    if _integ_fe > 1e-30 and P_fe_avg > 0:
        _kfe = P_fe_avg / _integ_fe
        if iron_s_idx.size: _dens[iron_s_idx] += _sh_is * _kfe
        if iron_r_idx.size: _dens[_nst_e + iron_r_idx] += _sh_ir * _kfe
        _parts.append("iron: Bertotti shape normalised to %.3g W" % P_fe_avg)
        _fe_glob = np.concatenate([np.asarray(iron_s_idx, int),
                                   _nst_e + np.asarray(iron_r_idx, int)])
        _fe_int = _integ_glob(_fe_glob)
        _log("loss map | iron   : ∫ = %.4g W vs reported %.4g W (%+.3f %%) "
             "[Bertotti shape, normalised]"
             % (_fe_int, P_fe_avg,
                100.0 * (_fe_int / max(P_fe_avg, 1e-30) - 1.0)))

    # ── Magnets ───────────────────────────────────────────────────────────
    _mag_glob = ((_nst_e + np.asarray(mag_idx, int)) if np.size(mag_idx)
                 else np.array([], int))
    if "mag" in _solved and solved_dens is not None:
        _gi = np.asarray(_sel.get("mag", _mag_glob), int)
        _dens[_gi] += np.asarray(solved_dens, float)[_gi]
        _parts.append("magnets: solved σE² (coupled σ·∂A/∂t), unrenormalised")
        _log("loss map | magnet : ∫ = %.4g W vs reported %.4g W (%+.3f %%) "
             "[per-element σE², NOT renormalised]"
             % (_integ_glob(_gi), P_mag_avg,
                100.0 * (_integ_glob(_gi) / max(P_mag_avg, 1e-30) - 1.0)))
    elif np.size(mag_idx) and np.size(hist_mx) and np.asarray(hist_mx[0]).size:
        _norm_into(np.asarray(mag_idx, int), _mean_sq_ddt(hist_mx, hist_my),
                   areas_r, _nst_e, P_mag_avg)
        _parts.append("magnets: slab |dB/dt|² shape normalised to %.3g W" % P_mag_avg)
        _log("loss map | magnet : ∫ = %.4g W vs reported %.4g W (%+.3f %%) "
             "[slab |dB/dt|² shape, normalised — smooth by construction]"
             % (_integ_glob(_mag_glob), P_mag_avg,
                100.0 * (_integ_glob(_mag_glob) / max(P_mag_avg, 1e-30) - 1.0)))

    # ── Shaft ─────────────────────────────────────────────────────────────
    # Only the coupled solve produces a shaft map at all: the frequency-domain
    # rotor solve reports a shaft WATT with no per-element field behind it, so
    # before this there was simply no shaft component in the picture.
    if "shaft" in _solved and solved_dens is not None and _sel.get("shaft") is not None:
        _gi = np.asarray(_sel["shaft"], int)
        if _gi.size:
            _dens[_gi] += np.asarray(solved_dens, float)[_gi]
            _parts.append("shaft: solved σE²")
            _log("loss map | shaft  : ∫ = %.4g W [per-element σE², "
                 "NOT renormalised]" % _integ_glob(_gi))

    # ── Copper ────────────────────────────────────────────────────────────
    _cu_glob = np.asarray(coil_idx, int)
    if _cu_glob.size:
        _vol_cu = float(np.sum(areas_s[_cu_glob])) * stack_length_m * sector_scale
        if "cu" in _solved and solved_dens is not None:
            _gi = np.asarray(_sel.get("cu", _cu_glob), int)
            _dens[_gi] += np.asarray(solved_dens, float)[_gi]
            _p_act = _integ_glob(_gi)
            # End turns: real watts, outside the modelled plane.  Uniform over
            # the copper and SAID so — not smeared into the solved shape.
            if _vol_cu > 1e-30 and P_cu_end_winding_W > 0:
                _dens[_cu_glob] += P_cu_end_winding_W / _vol_cu
            _parts.append(
                "copper: solved σE² (active length)"
                + (" + uniform end-winding DC %.3g W"
                   % P_cu_end_winding_W if P_cu_end_winding_W > 0 else ""))
            _log("loss map | copper : ∫ = %.4g W = solved active-length σE² "
                 "%.4g W + uniform end-winding %.4g W; reported copper %.4g W "
                 "(%+.3f %%)"
                 % (_integ_glob(_cu_glob), _p_act, P_cu_end_winding_W,
                    P_cu_dc + P_cu_ac_avg,
                    100.0 * (_integ_glob(_cu_glob)
                             / max(P_cu_dc + P_cu_ac_avg, 1e-30) - 1.0)))
        else:
            # Uniform DC ohmic + crowded AC proximity (radial/tangential).
            if _vol_cu > 1e-30 and P_cu_dc > 0:
                _dens[_cu_glob] += P_cu_dc / _vol_cu
            if (np.size(hist_cx) and np.asarray(hist_cx[0]).size
                    and P_cu_ac_avg > 0):
                _Xc = np.asarray(hist_cx); _Yc = np.asarray(hist_cy)
                _rc = np.hypot(coil_centroids[0], coil_centroids[1])
                _rc = np.where(_rc < 1e-9, 1e-9, _rc)
                _uxc = (coil_centroids[0] / _rc)[None, :]; _uyc = (coil_centroids[1] / _rc)[None, :]
                _dBrc = ddt(_Xc * _uxc + _Yc * _uyc)
                _dBtc = ddt(-_Xc * _uyc + _Yc * _uxc)
                _sh_cu = (sigma_cu / 12.0) * np.mean(
                    d_cu_r ** 2 * _dBrc ** 2 + d_cu_t ** 2 * _dBtc ** 2, axis=0)
                _norm_into(_cu_glob, _sh_cu, areas_s, 0, P_cu_ac_avg)
            _parts.append("copper: uniform DC I²R + proximity AC shape "
                          "normalised to %.3g W" % P_cu_ac_avg)
            _log("loss map | copper : ∫ = %.4g W vs reported %.4g W (%+.3f %%) "
                 "[uniform DC + normalised proximity AC]"
                 % (_integ_glob(_cu_glob), P_cu_dc + P_cu_ac_avg,
                    100.0 * (_integ_glob(_cu_glob)
                             / max(P_cu_dc + P_cu_ac_avg, 1e-30) - 1.0)))

    # ── components that are NOT IN THIS MAP ──────────────────────────────
    # A component whose model produced nothing — the magnet term when the run
    # had neither the coupled eddy solve nor the frequency-domain rotor solve,
    # so P_mag_avg came through as 0 — used to be normalised to zero watts and
    # painted at the bottom of the scale.  On a loss map the bottom of the scale
    # is what AIR looks like, so "we did not model this" rendered as "there is
    # no loss here", three times in a row, in the one component the user was
    # looking for.  Naming it here lets the view grey the material out and say
    # what is missing instead of quietly showing a zero.
    # ── invariant: nothing outside a modelled MATERIAL carries loss ───────
    # Every write above is indexed by a material's element ids, so this can
    # only fire on an index bug — the [stator-half | rotor-half] offset being
    # applied twice, or a solved-σE² id set from a different mesh.  It is
    # checked rather than assumed because the failure is silent and PRETTY:
    # loss landing in air paints a gradient across the air gap that looks like
    # physics.  Zeroed AND logged, so the picture stays honest even when the
    # indexing is not — and zeroed BEFORE the "what is missing" pass below, so
    # a stray write cannot make an unmodelled material look modelled.
    #
    # The mask is built from the MATERIAL index arrays, deliberately NOT from
    # ``solved_elems``: a solved-σE² id set that has drifted off the material it
    # claims to be is precisely the bug this is here to catch, so it cannot also
    # be the thing that declares itself legitimate.  (The shaft is the one
    # exception — the coupled solve is its only source, so there is nothing else
    # to check it against.)
    _shaft_glob = np.asarray(_sel.get("shaft") if _sel.get("shaft") is not None
                             else [], int)
    _mod_mask = np.zeros(int(n_elems), bool)
    for _ids in (np.asarray(iron_s_idx, int),
                 _nst_e + np.asarray(iron_r_idx, int),
                 _mag_glob, _cu_glob, _shaft_glob):
        if _ids.size:
            _mod_mask[_ids] = True
    _stray = np.flatnonzero((~_mod_mask) & (_dens != 0.0))
    if _stray.size:
        _log("loss map | ERROR : %d element(s) OUTSIDE every modelled material "
             "(air / gap / band) carried loss, max %.4g W/m³ — zeroed.  This is "
             "an element-index bug in the map, not physics."
             % (int(_stray.size), float(np.max(np.abs(_dens[_stray])))))
        _dens[_stray] = 0.0

    _unmodelled = []
    if np.size(mag_idx) and not float(np.sum(_dens[_mag_glob])) > 0.0:
        _unmodelled.append("magnets")
    if _cu_glob.size and not float(np.sum(_dens[_cu_glob])) > 0.0:
        _unmodelled.append("copper")
    if (np.size(iron_s_idx) or np.size(iron_r_idx)) and not (
            P_fe_avg > 0 and _integ_fe > 1e-30):
        _unmodelled.append("iron")
    # The SHAFT has exactly one source — the coupled σ·∂A/∂t solve.  Without it
    # there is no shaft term at all, and a shaft drawn at the bottom of the
    # scale says "no loss in the shaft", which is a claim this run cannot make.
    if not (_shaft_glob.size and float(np.sum(_dens[_shaft_glob])) > 0.0):
        _unmodelled.append("shaft")
    # AIR is never modelled, in any run.  σ = 0 → no eddy current, no
    # hysteresis, and windage is not part of a 2-D magnetic solve — so there is
    # no air loss to draw, ever.  It is listed here (rather than left to be
    # inferred from a zero) because on a LOG map the bottom of the scale is a
    # COLOUR: the air gap came out painted as a smooth blue-to-cyan ring, which
    # reads as a real, small, measured loss.  Blank is the only honest fill.
    _unmodelled.append("air")
    for _u in _unmodelled:
        if _u == "air":
            _parts.append("air: no loss model exists (σ=0, no hysteresis, "
                          "windage not modelled) — left BLANK, not coloured")
            continue
        _parts.append("%s: NOT MODELLED in this run — nothing is drawn there"
                      % _u)
        _log("loss map | %-6s : NOT MODELLED (no loss model produced a value; "
             "the map leaves it blank rather than showing zero)" % _u)

    _label = ("cycle-averaged loss density — " + "; ".join(_parts)) if _parts \
        else "loss density unavailable (no loss component could be built)"
    _log("loss map | TOTAL  : ∫ = %.4g W over the whole map"
         % _integ_glob(np.arange(int(n_elems))))
    return _dens, _label, _unmodelled


def rotor_eddy_tags(cells: dict, n_tri: int, dom_mag_base: int
                    ) -> Tuple[np.ndarray, list]:
    """Per-element domain tags for the rotor half, and which of them are magnets.

    Both element orders built this by hand, identically. The coupled rotor-eddy
    solve needs a dense tag array (it assembles per-region), and the magnet list
    decides which regions get their loss reported separately from the shaft.
    """
    tags = np.zeros(int(n_tri), int)
    for tag, elems in cells.items():
        tags[np.asarray(elems, int)] = int(tag)
    mag_tags = [int(t) for t in np.unique(tags) if int(t) >= dom_mag_base]
    return tags, mag_tags


def rotor_mu_lookup(mu_back_iron: float, dom_mag_base: int, dom_rotor: int,
                    mu_magnet: float = 1.05) -> Callable[[int], float]:
    """tag -> relative permeability for the rotor-eddy solve.

    Magnets recoil at ~1.05, the back iron uses the CONVERGED nu from the run
    that just finished (a linear 1000 would put the eddy solve on a different
    machine than the transient), and everything else — shaft aluminium, air — is
    non-magnetic.
    """
    def _mu(tag: int) -> float:
        tag = int(tag)
        if tag >= dom_mag_base:
            return mu_magnet
        if tag == dom_rotor:
            return float(mu_back_iron)
        return 1.0
    return _mu
