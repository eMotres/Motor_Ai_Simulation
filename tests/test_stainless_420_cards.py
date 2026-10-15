# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""The SS420 shaft cards (owner 2026-10-15): a FERROMAGNETIC martensitic stainless, two conditions, every value sourced, the
B-H curve flagged as a derived fit that still needs a measurement.  Closed-form, no solver."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from motor_ai_sim import materials as mat
from motor_ai_sim import part_materials as pm
from motor_ai_sim.catalog.envelope import PROV_TYPES

LIB = yaml.safe_load((Path(__file__).resolve().parents[1] / "config" / "materials_library.yaml").read_text(encoding="utf-8"))
CARDS = ("Stainless_420", "Stainless_420_annealed")


def _bh(card):
    return [(float(h), float(b)) for h, b in card["bh_curve"]]


@pytest.mark.parametrize("name", CARDS)
def test_420_is_a_solid_ferromagnetic_steel_with_a_monotone_bh_curve(name):
    c = LIB["steel"][name]
    assert c["form"] == "solid" and c["density"] == 7720 and c["sigma"] == pytest.approx(1.817e6, rel=1e-3)
    bh = _bh(c)
    assert bh[0] == (0.0, 0.0)
    assert all(b1 > b0 and h1 > h0 for (h0, b0), (h1, b1) in zip(bh, bh[1:]))
    mu_r_max = max(b / (4e-7 * 3.141592653589793 * h) for h, b in bh if h > 0)
    assert 100 < mu_r_max < 1000                    # a stainless martensite is far softer than an electrical steel
    s = mat.get_material("steel", name)
    assert s.density == 7720.0                                          # the solver loader accepts it
    h_end, b_end = s.bh_curve[-1]
    assert b_end - 4e-7 * 3.141592653589793 * h_end < 1.5               # polarisation J = B - mu0 H (Bs of a 420 is ~1.2-1.5 T)
    assert pm.resolve(name).get("density_kg_m3") == 7720


def test_hardened_is_magnetically_harder_and_mechanically_stronger_than_annealed():
    hard, ann = LIB["steel"]["Stainless_420"], LIB["steel"]["Stainless_420_annealed"]
    b_at = lambda c, h: dict(_bh(c))[h]            # noqa: E731
    assert b_at(hard, 1000) < b_at(ann, 1000) and b_at(hard, 400) < b_at(ann, 400)
    assert hard["yield_strength_mpa"] > 3 * ann["yield_strength_mpa"] and hard["tensile_strength_mpa"] > ann["tensile_strength_mpa"]
    assert hard["max_service_temp_c"] < ann["max_service_temp_c"]      # a tempered part must not exceed its tempering


@pytest.mark.parametrize("name", CARDS)
def test_every_420_value_names_its_source_and_the_bh_curve_is_flagged(name):
    c = LIB["steel"][name]
    ids = {s["id"] for s in c["sources"]}
    assert {"cartech_420", "oxley_2009"} <= ids
    for field, p in c["prov"].items():
        assert p["type"] in PROV_TYPES, (name, field)
        if p.get("src"):
            assert p["src"] in ids, (name, field)
    bh = c["prov"]["bh_curve"]
    assert bh["type"] == "derived" and bh["verify"] is True and "NOT a measured curve" in bh["note"]
    for field in ("density", "sigma", "youngs_modulus_gpa", "yield_strength_mpa", "tensile_strength_mpa",
                  "thermal_conductivity", "specific_heat", "cte_ppm_k"):
        assert c["prov"][field]["src"] == "cartech_420"


def test_the_nonmagnetic_options_stay_nonmagnetic_conductors():
    for name in ("Stainless_304", "Stainless_316"):
        c = LIB["conductor"][name]
        assert c["permeability"] == pytest.approx(1.005) and "bh_curve" not in c and c["density"] == 8000
    assert LIB["conductor"]["Stainless_304"]["yield_strength_mpa"] == 205
