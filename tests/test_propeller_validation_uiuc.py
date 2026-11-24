"""Validation of the blade-element model ``bemt-1`` against a MEASURED propeller (review 2026-11-24, finding 4a).

Data: UIUC Propeller Data Site, volume 1 (J.B. Brandt, R.W. Deters, G.K. Ananda, O.D. Dantsker, M.S. Selig, UIUC Propeller Database,
Vols 1-4, University of Illinois at Urbana-Champaign, Department of Aerospace Engineering), APC Thin Electric 11x5.5:
- measured geometry ``apce_11x5.5_geom.txt`` (r/R, c/R, beta at 18 stations),
- wind-tunnel J sweeps ``apce_11x5.5_kt0471_6002.txt`` / ``_kt0472_6000.txt`` (CT, CP, eta at about 6,000 rpm),
- static test ``apce_11x5.5_static_kt0467.txt`` (CT, CP from 1,868 to 6,473 rpm).
Source page: https://m-selig.ae.illinois.edu/props/volume-1/propDB-volume-1.html.  The site gives a citation for the data but no licence
for redistribution, so the data files are NOT in this repository: the test reads them from ``$PROPELLER_UIUC_DIR`` and is skipped when
the directory is absent.  Download the four files above into that directory under the names given (without the ``data/`` prefix).

What the model is given: the measured chord and twist; the AIRFOIL IS ASSUMED (NACA 4412 at the root zone, 4408 outboard - APC does
not publish its sections), so part of the bias below is the airfoil, not the momentum model.

Measured bias pinned here (2026-11-24, this branch), model minus measurement at about 6,000 rpm:

==================  ==============  ==============  ==================  ====================
polar source        CT, J 0.2-0.35  CP, J 0.2-0.35  efficiency (abs.)   static CT, >= 4000 rpm
==================  ==============  ==============  ==================  ====================
mfoil_table + PG    -2 %            -7 %            +0.026              -28 %
flatplate_model     -38 %           -44 %           +0.059              -19 %
==================  ==============  ==============  ==================  ====================

``flatplate_model`` is the family's stored default polar: its flight thrust and power are about 40 % low against this measurement (the
ratio, i.e. efficiency, much less so).  Numbers from it are rankings, not values.  The static row is low with both sources: the blade
Reynolds numbers at 4,000-6,500 rpm lie below the tables, so hover numbers are not usable until low-Re polars exist.

How the pin relates to the check given to the owner on 2026-11-23 (CT -5 %, CP -9 %, efficiency +0.02 with the mfoil tables): same
geometry file, same assumed airfoil zones (4412 root / 4408 outboard), same air (1.225 kg/m3, 20 C), same data file kt0471.  That check
quoted ONE point (J 0.263); this test takes the MEAN over J 0.22-0.35, and the error trends with J (mfoil, per point, model / measured - 1):

=====  ======  ======  ==========
J      CT      CP      efficiency
=====  ======  ======  ==========
0.220  -8.7 %  -10.1 % +0.009
0.242  -6.7 %  -9.4 %  +0.014
0.263  -4.9 %  -8.5 %  +0.019     <- the 2026-11-23 point
0.284  -2.3 %  -6.9 %  +0.027
0.304  +0.3 %  -5.2 %  +0.033
0.327  +3.6 %  -3.0 %  +0.039
0.349  +3.9 %  -2.9 %  +0.042
=====  ======  ======  ==========

The kernel changes of the same review moved the point at J 0.263 by less than 0.01 %.  So -5 / -9 / +0.02 (one point) and -2 / -7 / +0.026
(band mean) describe the same model; the per-point maxima are pinned too (``MAX_POINT``), so a model that keeps the mean but spreads the
error is caught.

THIS IS A REGRESSION PIN OF THE KNOWN BIAS, NOT AN ACCURACY GATE: the bands sit around today's error.  Normal CI has no data and skips the
test.  It runs for real in the scheduled us1 job ``scripts/propeller_validation_job.sh`` (weekly, and by hand before a propeller
merge), which sets ``$PROPELLER_UIUC_DIR`` and ``PROPELLER_UIUC_REQUIRED=1``: there a missing data file fails instead of skipping, and
any failure leaves an alert file and a non-zero exit.

The pinned numbers are the honest state of the model, not a target: a change that moves any of them outside its band must say why
(better polars move the static row towards zero; that is wanted, and the band is then tightened in the same change).
"""
from __future__ import annotations

import math
import os
from pathlib import Path

import numpy as np
import pytest

from motor_ai_sim import propeller as pp
from motor_ai_sim import propeller_bemt as pb
from motor_ai_sim import propeller_design as pd

DATA = Path(os.environ.get("PROPELLER_UIUC_DIR", "/nonexistent-uiuc"))
FILES = ("apce_11x5.5_geom.txt", "apce_11x5.5_kt0471_6002.txt", "apce_11x5.5_kt0472_6000.txt", "apce_11x5.5_static_kt0467.txt")
#: set by the scheduled run (``scripts/propeller_validation_job.sh``): missing data is then a FAILURE, never a skip
REQUIRED = os.environ.get("PROPELLER_UIUC_REQUIRED", "").strip() not in ("", "0")
_MISSING = [f for f in FILES if not (DATA / f).is_file()]
pytestmark = pytest.mark.skipif(bool(_MISSING) and not REQUIRED,
                                reason="UIUC data not present (set PROPELLER_UIUC_DIR; not redistributable, see the docstring)")


def test_uiuc_data_present_when_required():
    """The scheduled run sets PROPELLER_UIUC_REQUIRED=1: there an absent data file fails loudly instead of skipping the pin."""
    assert not _MISSING, "PROPELLER_UIUC_REQUIRED is set but %s lacks %s" % (DATA, ", ".join(_MISSING))

R = 11 * 0.0254 / 2.0
J_BAND = (0.2, 0.35)

#: mean relative bias (model / measured - 1) and the allowed band around it; efficiency as an absolute difference
PINNED = {                                       # measured on this branch 2026-11-24 (job propfix15); centre, allowed +-
    "mfoil": {"ct": (-0.021, 0.04), "cp": (-0.066, 0.04), "eta": (0.026, 0.02), "static_ct": (-0.283, 0.12)},
    "flatplate": {"ct": (-0.380, 0.06), "cp": (-0.441, 0.06), "eta": (0.059, 0.03), "static_ct": (-0.188, 0.12)},
}


#: largest per-point error allowed (absolute: relative for CT / CP, difference for efficiency); today's values in the comment
MAX_POINT = {
    "mfoil": {"max_ct": 0.12, "max_cp": 0.14, "max_eta": 0.06, "max_static_ct": 0.45},        # 0.087, 0.101, 0.042, 0.372
    "flatplate": {"max_ct": 0.47, "max_cp": 0.51, "max_eta": 0.09, "max_static_ct": 0.30},    # 0.410, 0.455, 0.063, 0.209
}


def _table(name):
    rows = [ln.split() for ln in (DATA / name).read_text().splitlines() if ln.strip() and ln.split()[0][0].isdigit()]
    return np.array(rows, float)


def _apc(source):
    d = pd.load_design()
    g = _table("apce_11x5.5_geom.txt")
    st = []
    for rR, cR, beta in g:
        zone = pd._zone_of(d, float(rR))
        st.append({"r_mm": rR * R * 1000, "chord_mm": cR * R * 1000, "twist_deg": float(beta),
                   "airfoil": "naca4-4412" if zone == "root" else "naca4-4408", "thickness_rel": 0.10 if zone == "root" else 0.08})
    polars = {"source": "mfoil_table", "compressibility": "prandtl_glauert"} if source == "mfoil" else {"source": "flatplate_model"}
    raw = {"schema": "propeller-2", "id": "uiuc_apce_11x5.5", "vendor": "APC", "model": "11x5.5E (UIUC measured geometry)", "status": "reference",
           "geometry": {"diameter_in": 11.0, "diameter_mm": 2000 * R, "blades": 2}, "performance": {"data_quality": "geometry_only"},
           "blade": {"geometry_source": "measured", "source_ref": "UIUC Propeller Data Site vol. 1", "estimated": True,
                     "method": "measured chord / twist, airfoil assumed", "hub_radius_mm": 0.15 * R * 1000, "root_cutout_mm": 0.15 * R * 1000,
                     "interpolation": "pchip", "pitch_axis": 0.25, "stations": st, "polars": polars, "manufacturing": {}}}
    return pp._build(raw, "test:uiuc")


def bias(source):
    """Mean relative bias in the flight band and in the static test (model minus measured)."""
    prop = _apc(source)
    pol = pb.polar_for(prop)
    dct, dcp, deta, pts = [], [], [], []
    for f in ("apce_11x5.5_kt0471_6002.txt", "apce_11x5.5_kt0472_6000.txt"):
        rpm = float(f.split("_")[-1][:-4])
        n = rpm / 60.0
        for J, CT, CP, eta in _table(f):
            if not J_BAND[0] <= J <= J_BAND[1]:
                continue
            r = pb.solve(prop.blade, 2, rpm, 1.225, J * n * 2 * R, polar=pol, temp_c=20.0)
            dct.append(r.ct / CT - 1); dcp.append(r.cp / CP - 1); deta.append(r.efficiency - eta)
            pts.append((float(J), dct[-1], dcp[-1], deta[-1]))
    sct = []
    for rpm, CT, CP in _table("apce_11x5.5_static_kt0467.txt"):
        if rpm < 4000:
            continue
        r = pb.solve(prop.blade, 2, float(rpm), 1.225, 0.0, polar=pol, temp_c=20.0)
        sct.append(r.ct / CT - 1)
    return {"ct": float(np.mean(dct)), "cp": float(np.mean(dcp)), "eta": float(np.mean(deta)), "static_ct": float(np.mean(sct)),
            "max_ct": float(np.max(np.abs(dct))), "max_cp": float(np.max(np.abs(dcp))), "max_eta": float(np.max(np.abs(deta))),
            "max_static_ct": float(np.max(np.abs(sct))), "points": pts, "n_flight": len(dct), "n_static": len(sct)}


@pytest.mark.parametrize("source", ["mfoil", "flatplate"])
def test_bemt_bias_against_uiuc_apce_11x5_5_is_pinned(source):
    b = bias(source)
    assert b["n_flight"] >= 5 and b["n_static"] >= 3
    for k, (centre, width) in PINNED[source].items():
        assert abs(b[k] - centre) <= width, "%s %s: %.3f, pinned %.3f +- %.3f" % (source, k, b[k], centre, width)
    for k, lim in MAX_POINT[source].items():
        assert b[k] <= lim, "%s %s: %.3f above the per-point limit %.3f" % (source, k, b[k], lim)
