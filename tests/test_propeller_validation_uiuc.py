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
pytestmark = pytest.mark.skipif(not all((DATA / f).is_file() for f in FILES),
                                reason="UIUC data not present (set PROPELLER_UIUC_DIR; not redistributable, see the docstring)")

R = 11 * 0.0254 / 2.0
J_BAND = (0.2, 0.35)

#: mean relative bias (model / measured - 1) and the allowed band around it; efficiency as an absolute difference
PINNED = {                                       # measured on this branch 2026-11-24 (job propfix15); centre, allowed +-
    "mfoil": {"ct": (-0.021, 0.04), "cp": (-0.066, 0.04), "eta": (0.026, 0.02), "static_ct": (-0.283, 0.12)},
    "flatplate": {"ct": (-0.380, 0.06), "cp": (-0.441, 0.06), "eta": (0.059, 0.03), "static_ct": (-0.188, 0.12)},
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
    dct, dcp, deta = [], [], []
    for f in ("apce_11x5.5_kt0471_6002.txt", "apce_11x5.5_kt0472_6000.txt"):
        rpm = float(f.split("_")[-1][:-4])
        n = rpm / 60.0
        for J, CT, CP, eta in _table(f):
            if not J_BAND[0] <= J <= J_BAND[1]:
                continue
            r = pb.solve(prop.blade, 2, rpm, 1.225, J * n * 2 * R, polar=pol, temp_c=20.0)
            dct.append(r.ct / CT - 1); dcp.append(r.cp / CP - 1); deta.append(r.efficiency - eta)
    sct = []
    for rpm, CT, CP in _table("apce_11x5.5_static_kt0467.txt"):
        if rpm < 4000:
            continue
        r = pb.solve(prop.blade, 2, float(rpm), 1.225, 0.0, polar=pol, temp_c=20.0)
        sct.append(r.ct / CT - 1)
    return {"ct": float(np.mean(dct)), "cp": float(np.mean(dcp)), "eta": float(np.mean(deta)), "static_ct": float(np.mean(sct)),
            "n_flight": len(dct), "n_static": len(sct)}


@pytest.mark.parametrize("source", ["mfoil", "flatplate"])
def test_bemt_bias_against_uiuc_apce_11x5_5_is_pinned(source):
    b = bias(source)
    assert b["n_flight"] >= 5 and b["n_static"] >= 3
    for k, (centre, width) in PINNED[source].items():
        assert abs(b[k] - centre) <= width, "%s %s: %.3f, pinned %.3f +- %.3f" % (source, k, b[k], centre, width)
