"""The ONE definition of Kv and Kt (owner 2026-11-05), used by every Python reader on the site.

* ``Kv`` = rpm per volt of the line-line PEAK of the FUNDAMENTAL back-EMF at no load.
* ``Kt`` = N*m per ampere rms of PHASE current, sine current on the q axis.  At no load it follows from Kv exactly:
  ``Kt = (3 / sqrt(2)) * 30 / (sqrt(3) * pi * Kv)`` = ``KT_TIMES_KV / Kv`` (about 11.695 / Kv), so the two always agree.
* ``T / I`` at a load point is a different quantity (saturation and the load angle are inside it): call it "Kt at load",
  never "Kt".
* The waveform peak of a voltage is a voltage (bus margin), never Kv.

The web has its mirror in ``web/src/lib/kvKt.ts``; neither side keeps a second copy of the number.
"""
from __future__ import annotations

import math
from typing import Optional

#: Kt * Kv, in (N*m / A rms) * (rpm / V line-line peak): 3/sqrt(2) * 30 / (sqrt(3) * pi).
KT_TIMES_KV = (3.0 / math.sqrt(2.0)) * 30.0 / (math.sqrt(3.0) * math.pi)


def kt_from_kv(kv_rpm_per_v: Optional[float], *, delta: bool = False) -> Optional[float]:
    """Kt per A rms of PHASE (winding) current from Kv.  In star the phase current is the line current; in delta the winding
    carries 1/sqrt(3) of the line current, so the per-winding Kt is sqrt(3) larger (see :func:`kt_per_line_amp`)."""
    if kv_rpm_per_v is None or not math.isfinite(kv_rpm_per_v) or kv_rpm_per_v <= 0:
        return None
    return KT_TIMES_KV / float(kv_rpm_per_v) * (math.sqrt(3.0) if delta else 1.0)


def kt_per_line_amp(kv_rpm_per_v: Optional[float]) -> Optional[float]:
    """Kt per A rms of LINE current: the star formula in both connections."""
    return kt_from_kv(kv_rpm_per_v, delta=False)


def kv_from_kt(kt_nm_per_a: Optional[float], *, delta: bool = False) -> Optional[float]:
    """The inverse of :func:`kt_from_kv`."""
    if kt_nm_per_a is None or not math.isfinite(kt_nm_per_a) or kt_nm_per_a <= 0:
        return None
    return KT_TIMES_KV / float(kt_nm_per_a) * (math.sqrt(3.0) if delta else 1.0)


def noload_constants(summary: Optional[dict], k_flux: Optional[float] = None) -> dict:
    """Kv and Kt of one stored EM summary, on the one definition above.

    ``kv`` = the stored no-load Kv (``KV_noload_rpm_per_V_line``, the fundamental); an older run that carries only the
    loaded waveform-peak Kv (``KV_rpm_per_V_line``) still works and is marked ``source = "waveform peak"``.  ``k_flux`` (the
    end-effect correction, applied inside ``end3d_factors.FACTOR_BAND`` - either side of 1, a refused one is logged) divides
    Kv like every voltage, and Kt follows it, so the two stay consistent.  ``kt`` is per A rms of the winding (phase) current, ``kt_line`` per A rms of line current.
    Nothing is invented: no Kv at all gives ``None`` values."""
    s = summary if isinstance(summary, dict) else {}

    def _f(key):
        try:
            v = float(s.get(key))
        except (TypeError, ValueError):
            return None
        return v if math.isfinite(v) and v > 0 else None

    kv, source = _f("KV_noload_rpm_per_V_line"), "fundamental"
    if kv is None:
        kv, source = _f("KV_rpm_per_V_line"), "waveform peak"
    if kv is None:
        return {"kv": None, "kt": None, "kt_line": None, "source": None}
    from motor_ai_sim.end3d_factors import accepted_factor
    kf = accepted_factor(k_flux, "k_flux")
    if kf is not None:
        kv = kv / kf
    delta = str(s.get("star_delta") or "star").lower().startswith("d")
    return {"kv": kv, "kt": kt_from_kv(kv, delta=delta), "kt_line": kt_per_line_amp(kv), "source": source}
