"""The thermal iterations of a coupled ``drive: "inverter"`` run between its
PWM passes — on the SINE losses plus the PWM's own harmonic-loss delta — and
the aim of the one PWM pass that follows them.  Pure functions: no solve, no
I/O.

WHY (Erium L90, 48s56p, 20 kHz, 2026-10-30/31).  One PWM pass is a voltage-fed
march of tens of fine electrical periods — hours on that machine — and the
``final_pass`` algorithm used to answer a temperature move after its first PWM
pass with a SECOND PWM pass at the temperatures that first map produced, then
a third when the current had drifted out of its band.  The second pass sits at
a temperature only one thermal step away from the sine state, not at the
temperature the PWM losses settle to, so its own map still moves the machine
(``residual_K``) and nothing is left to chase it.

What the carrier adds to the machine's losses is, to first order, a property
of the bridge and the machine's inductances — the ripple current, the copper's
AC resistance at the carrier, the iron's high-frequency loop — and moves very
little with a few kelvin of copper or magnet.  What DOES move with the
temperature is the fundamental's loss, which the sine drive measures exactly
and cheaply.  So, after the first PWM pass at the sine state's temperatures
``T_s``:

    ΔP = P_pwm(T_s) − P_sine(T_s)                       per element (or part)
    T_{j+1} = thermal( P_sine(T_j) + ΔP )               until |ΔT| < tol
    one PWM pass at the converged T_c — its own map is the answer, and its
    residual against T_c says whether the frozen ΔP held.

``loss_map_delta`` / ``apply_loss_delta`` build those maps;
``v1_feed_forward`` carries the PWM command from T_s to T_c with the sine
drive's own measured V1 trend, so the one PWM pass at T_c lands where the
first one did relative to the duty's current.
"""
from __future__ import annotations

import hashlib
import math
from typing import Any, Dict, Mapping, Optional, Tuple

#: Scalar totals of the field2d loss-map payload the thermal solve reads, each
#: moved by the delta the same way the per-element density is.
MAP_TOTAL_KEYS = ("P_cu_W", "P_cu_exact_W", "P_fe_W", "P_fe_exact_W",
                  "P_loss_total_W", "P_loss_total_exact_W")

def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def mesh_digest(em_map: Mapping[str, Any]) -> Optional[str]:
    """A short digest of the map's triangulation (vertices to 1 nm, triangle
    indices) — two maps share per-element deltas only on the same mesh."""
    v = em_map.get("vertices")
    t = em_map.get("triangles")
    if not v or not t:
        return None
    h = hashlib.sha1()
    h.update(("%d|%d|" % (len(v), len(t))).encode())
    for p in v:
        h.update(("%d,%d;" % (round(float(p[0]) * 1e9),
                              round(float(p[1]) * 1e9))).encode())
    for tri in t:
        h.update(("%d,%d,%d;" % tuple(int(x) for x in tri)).encode())
    return h.hexdigest()[:16]


def _areas(em_map: Mapping[str, Any]):
    v = em_map.get("vertices") or []
    out = []
    for a, b, c in (em_map.get("triangles") or []):
        (x0, y0), (x1, y1), (x2, y2) = v[a][:2], v[b][:2], v[c][:2]
        out.append(0.5 * abs((x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)))
    return out


def _tag_watts(em_map: Mapping[str, Any]) -> Dict[int, float]:
    """Σ density × area per domain tag (per unit length — only ever compared
    with the same sum of another map, so the stack length cancels)."""
    tags = list(em_map.get("domain_per_tri") or [])
    ld = list(em_map.get("loss_density_per_tri") or [])
    if not ld or len(ld) != len(tags):
        return {}
    out: Dict[int, float] = {}
    for t, d, a in zip(tags, ld, _areas(em_map)):
        out[int(t)] = out.get(int(t), 0.0) + float(d) * a
    return out


def loss_map_delta(pwm_map: Mapping[str, Any],
                   sine_map: Mapping[str, Any]) -> Dict[str, Any]:
    """ΔP = the PWM pass's map − the sine map, both at the sine state's
    temperatures.  Per ELEMENT when the two maps are one mesh (the usual case:
    the same machine, mesh settings and slip grid), else per DOMAIN tag; the
    scalar totals of :data:`MAP_TOTAL_KEYS` always."""
    totals = {}
    for k in MAP_TOTAL_KEYS:
        a, b = _num(pwm_map.get(k)), _num(sine_map.get(k))
        if a is not None and b is not None:
            totals[k] = a - b
    dg_p, dg_s = mesh_digest(pwm_map), mesh_digest(sine_map)
    ld_p = list(pwm_map.get("loss_density_per_tri") or [])
    ld_s = list(sine_map.get("loss_density_per_tri") or [])
    blk: Dict[str, Any] = {"totals": totals, "mesh_digest": dg_s}
    if dg_p is not None and dg_p == dg_s and len(ld_p) == len(ld_s) and ld_p:
        blk["mode"] = "element"
        blk["d_density"] = [float(a) - float(b) for a, b in zip(ld_p, ld_s)]
    else:
        wp, ws = _tag_watts(pwm_map), _tag_watts(sine_map)
        blk["mode"] = "domain"
        blk["d_tag_watts"] = {t: wp.get(t, 0.0) - ws.get(t, 0.0)
                              for t in sorted(set(wp) | set(ws))}
        blk["note"] = ("the PWM and sine maps are not one mesh (%s vs %s): the "
                       "delta is carried per domain, distributed like the "
                       "sine map inside each domain" % (dg_p, dg_s))
    return blk


def apply_loss_delta(sine_map: Mapping[str, Any],
                     delta: Mapping[str, Any]) -> Tuple[Dict[str, Any],
                                                        Dict[str, Any]]:
    """``(map, info)``: a shallow copy of the sine map at the iteration's own
    temperatures with the PWM delta added — the map the thermal solve reads.

    An element whose density would go below zero (a place the carrier screens
    more than it heats, or a rotor-iron selection) is clipped at zero and the
    clipped watts are reported, never hidden.  The sine map is never touched.
    """
    out = dict(sine_map or {})
    info: Dict[str, Any] = {"mode": delta.get("mode")}
    ld = [float(x) for x in (out.get("loss_density_per_tri") or [])]
    tags = list(out.get("domain_per_tri") or [])
    clipped = 0.0
    if delta.get("mode") == "element":
        dd = delta.get("d_density") or []
        if len(dd) != len(ld) or mesh_digest(out) != delta.get("mesh_digest"):
            raise ValueError("the per-element PWM delta belongs to another "
                             "mesh than this sine map")
        area = _areas(out)
        new = []
        for x, d, a in zip(ld, dd, area):
            y = x + float(d)
            if y < 0.0:
                clipped += -y * a
                y = 0.0
            new.append(y)
        out["loss_density_per_tri"] = new
    elif delta.get("mode") == "domain" and ld and len(tags) == len(ld):
        ws = _tag_watts(out)
        dw = {int(k): float(v) for k, v in
              (delta.get("d_tag_watts") or {}).items()}
        area = _areas(out)
        a_tag: Dict[int, float] = {}
        for t, a in zip(tags, area):
            a_tag[int(t)] = a_tag.get(int(t), 0.0) + a
        new = []
        for x, t, a in zip(ld, tags, area):
            t = int(t)
            d = dw.get(t, 0.0)
            if ws.get(t, 0.0) > 0.0:
                y = x * (1.0 + d / ws[t])
            else:
                y = x + (d / a_tag[t] if a_tag.get(t) else 0.0)
            if y < 0.0:
                clipped += -y * a
                y = 0.0
            new.append(y)
        out["loss_density_per_tri"] = new
    for k, d in (delta.get("totals") or {}).items():
        v = _num(out.get(k))
        if v is not None:
            out[k] = max(v + float(d), 0.0)
    info["clipped_density_area"] = clipped
    out["pwm_delta_applied"] = dict(info)
    return out, info


def v1_feed_forward(s_from: Mapping[str, Any],
                    s_to: Mapping[str, Any]) -> Dict[str, Any]:
    """How the sine drive's own terminal fundamental moved between two sine
    states of the same current (the sine state at ``T_s`` and the one at the
    converged ``T_c``): the shift the PWM command must make for the same
    current at the new temperatures — the magnets' back-EMF and the copper's
    resistance, measured instead of modelled."""
    v0, v1 = _num(s_from.get("V1_seed_peak_V")), _num(s_to.get("V1_seed_peak_V"))
    d0, d1 = (_num(s_from.get("V1_seed_delta_deg")),
              _num(s_to.get("V1_seed_delta_deg")))
    out: Dict[str, Any] = {"applied": False}
    if v0 is None or v1 is None or v0 <= 0.0 or v1 <= 0.0:
        out["reason"] = "a sine state carries no terminal fundamental"
        return out
    out.update(applied=True, dV_peak_V=v1 - v0, ratio=v1 / v0,
               d_delta_deg=(None if (d0 is None or d1 is None) else d1 - d0),
               v_sine_from_V=v0, v_sine_to_V=v1)
    return out
