# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Materials and parts on the component card: the material cards (sources for every value), the motor mass from the
app's own mass model, the privacy gate for motor parts, the propeller material defaults, the CAD-BOM extras and the
routes.  Closed-form, no solver, no FEM."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import ValidationError

from motor_ai_sim import part_materials as pm
from motor_ai_sim import propeller as pp
from motor_ai_sim import propeller_s1 as s1
from motor_ai_sim.api import app
from motor_ai_sim.contracts import ComponentCard, ComponentPart, Provenance, RigidBodyIR
from motor_ai_sim.contracts.component_ir import Frame, MassProperties, Port, box_inertia
from motor_ai_sim.contracts.conformance import ConformanceError, assert_component_card
from motor_ai_sim.modules import component_adapters as A
from motor_ai_sim.modules import component_library as L
from motor_ai_sim.modules import motor_mass as MM

client = TestClient(app)
REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module", autouse=True)
def stand_in_dies(tmp_path_factory):
    from tests._component_dies import make_dies
    mp = pytest.MonkeyPatch()
    mp.setenv("MOTRES_DIES_DIR", str(make_dies(tmp_path_factory.mktemp("catalog"))))
    L.get_library(refresh=True)
    yield
    mp.undo()
    L._LIB = None                                                         # noqa: SLF001


# -- the material cards ------------------------------------------------------------------------------

NEW_CARDS = ["Stainless_A2_70", "Bearing_steel_100Cr6", "FR4_glass_epoxy", "CFRP_QI_T800_epoxy", "PA66_GF30",
             "PA66_CF30", "Li_ion_pouch_cell_effective"]


def test_every_new_card_value_has_a_source_a_type_and_a_unit():
    doc = yaml.safe_load((REPO / "config" / "part_materials.yaml").read_text(encoding="utf-8"))
    assert set(NEW_CARDS) == set(doc["materials"])
    for mid in NEW_CARDS:
        card = pm.resolve(mid)
        assert card.origin == "part_materials" and card.get("density_kg_m3") > 0
        for name, pv in card.props.items():
            assert pv.source in card.sources and card.sources[pv.source].strip(), (mid, name)
            assert pv.unit == pm.PROPERTIES[name]
            assert pv.type in ("standard", "datasheet", "handbook", "derived", "estimate")
        assert set(card.missing) == set(pm.PROPERTIES) - set(card.props)
    # repo rule: nothing comes from a commercial FEM package's material library
    text = (REPO / "config" / "part_materials.yaml").read_text(encoding="utf-8").lower()
    assert "ansys" not in text


def test_a_cell_has_no_invented_mechanical_properties():
    cell = pm.resolve("Li_ion_pouch_cell_effective")
    assert cell.get("youngs_modulus_GPa") is None and "youngs_modulus_GPa" in cell.missing


def test_library_cards_are_reused_with_their_own_sources():
    for mid, cat in [("20SW1200", "steel"), ("F52SH_120C", "magnet"), ("copper", "conductor"),
                     ("Steel_42CrMo4_QT", "steel"), ("Nomex", "insulator"),
                     ("Aluminium_6061", "conductor"), ("Aluminium_7075", "conductor")]:
        card = pm.resolve(mid)
        assert card.origin == "materials_library" and card.library_category == cat, mid
        assert card.get("density_kg_m3") > 0 and card.sources
    # the density IS the library's density (one card per material), not a copy
    from motor_ai_sim import materials as mat
    assert pm.resolve("20SW1200").get("density_kg_m3") == mat.get_material("steel", "20SW1200").density
    # an extension fills a gap but never overrides the library
    al = pm.resolve("Aluminium_6061")
    assert al.get("yield_MPa") == 276.0 and al.get("ultimate_MPa") == 310.0 and al.get("fatigue_limit_MPa") == 96.5
    assert al.props["ultimate_MPa"].source == "asm_v2"
    assert pm.resolve("Steel_42CrMo4_QT").get("yield_MPa") == 750.0          # the library value stays


def test_the_cfrp_laminate_follows_from_the_library_lamina_by_lamination_theory():
    from motor_ai_sim import materials as mat
    t800 = mat.get_material("insulator", "T800_UD_60")
    e1, e2, g12, n12 = t800.youngs_modulus_gpa, t800.youngs_modulus_transverse_gpa, t800.shear_modulus_gpa, t800.poisson_ratio
    n21 = n12 * e2 / e1
    d = 1 - n12 * n21
    q11, q22, q12, q66 = e1 / d, e2 / d, n12 * e2 / d, g12
    u1 = (3 * q11 + 3 * q22 + 2 * q12 + 4 * q66) / 8
    u4 = (q11 + q22 + 6 * q12 - 4 * q66) / 8
    qi = pm.resolve("CFRP_QI_T800_epoxy")
    assert qi.get("youngs_modulus_GPa") == pytest.approx((u1 * u1 - u4 * u4) / u1, abs=0.5)
    assert qi.get("poisson_ratio") == pytest.approx(u4 / u1, abs=0.01)
    assert qi.get("density_kg_m3") == t800.density


def test_unknown_material_is_refused_not_defaulted():
    with pytest.raises(pm.UnknownPartMaterial):
        pm.resolve("unobtainium")
    assert not pm.exists("unobtainium")


# -- parts on the card ---------------------------------------------------------------------------------

def _prov():
    return Provenance(module="t", contracts_version="0.1.0")


def _frame(parts, mass=1.0):
    mp = MassProperties(mass_kg=mass, inertia_kg_m2=box_inertia(mass, 0.1, 0.1, 0.1), basis="test")
    return ComponentCard(component_id="test_frame", kind="frame", name="t", licence="Apache-2.0", provenance=_prov(),
                         mass_properties=mp, parts=parts, frames=[Frame(name="a")],
                         ports=[Port(name="m1", type="mount", direction="bidirectional", frame="a")],
                         model=RigidBodyIR(id="x", source={"kind": "analytical", "reference": "test"}))


def test_parts_must_add_up_to_the_card_mass_and_resolve():
    ok = [ComponentPart(name="a", material_id="Aluminium_6061", mass_kg=0.6, role="structural"),
          ComponentPart(name="b", material_id="Stainless_A2_70", mass_kg=0.4, role="fastener")]
    assert_component_card(_frame(ok))
    with pytest.raises(ValidationError, match="add up"):
        _frame([ok[0]])
    with pytest.raises(ValidationError, match="unique"):
        _frame([ok[0], ok[0].model_copy(update={"mass_kg": 0.4})])
    # a part outside the card mass (counted=false) does not enter the sum
    extra = ComponentPart(name="c", material_id="copper", mass_kg=5.0, role="conductor", counted=False)
    assert_component_card(_frame(ok + [extra]))
    # an unknown material is a gate failure
    bad = [ComponentPart(name="a", material_id="unobtainium", mass_kg=1.0, role="structural")]
    with pytest.raises(ConformanceError, match="unobtainium"):
        assert_component_card(_frame(bad))
    # volume and density must be of the same order
    off = [ComponentPart(name="a", material_id="Aluminium_6061", mass_kg=1.0, volume_cm3=1.0, role="structural")]
    with pytest.raises(ConformanceError, match="density"):
        assert_component_card(_frame(off))
    with pytest.raises(ValidationError):                                  # extra fields are refused
        ComponentPart(name="a", material_id="copper", mass_kg=1.0, role="conductor", diameter_mm=3)
    with pytest.raises(ValidationError):
        ComponentPart(name="a", material_id="copper", mass_kg=1.0, role="decor")


# -- the motor mass is the app's ------------------------------------------------------------------------

def _compute_masses_directly(die: str, cfg: str):
    from motor_ai_sim.masses import compute_masses
    from motor_ai_sim.simulation.geometry_2d import _cfg_path, merge_geo_override, params_from_config
    d = Path(os.environ["MOTRES_DIES_DIR"]) / die
    geo = dict(yaml.safe_load((d / "die.yaml").read_text())["geometry"])
    cy = yaml.safe_load((d / f"{cfg}.yaml").read_text())
    geo.update(cy["geometry_overrides"])
    base = yaml.safe_load(Path(_cfg_path()).read_text())
    mats = {k: v for k, v in base["materials"].items() if k in ("stator_core", "rotor_core", "magnet", "slot", "shaft")}
    mats.update(cy["materials"])
    return compute_masses(params_from_config(geo_override=geo), merge_geo_override(dict(base["geometry"]), geo),
                          k_end=0.0, materials=mats)


def test_motor_mass_is_compute_masses_with_a_per_part_breakdown():
    mm = MM.app_motor_mass("CIANO14 40 new", "L12")
    m = _compute_masses_directly("CIANO14 40 new", "L12")
    assert mm.total_kg == pytest.approx(m["total"], rel=1e-12)
    by = {p.name: p for p in mm.parts}
    assert by["Stator core"].mass_kg == pytest.approx(m["stator"]) and by["Stator core"].material_id == "20SW1200"
    assert by["Magnets"].mass_kg == pytest.approx(m["mag"]) and by["Magnets"].material_id == "F52SH_120C"
    assert by["Winding copper"].mass_kg == pytest.approx(m["cu"]) and by["Shaft"].mass_kg == pytest.approx(m["shaft"])
    assert sum(p.mass_kg for p in mm.parts if p.counted) == pytest.approx(mm.total_kg, rel=1e-12)
    # what the configuration does not name falls back to the shipped default, and the part says so
    assert "shipped default" in by["Shaft"].basis and "the configuration" in by["Stator core"].basis


def test_motor_card_has_the_app_mass_and_material_plus_mass_only():
    model = s1.load_model("CIANO14 40 new", "L12")
    card = A.motor_card(model)
    assert_component_card(card)
    mm = MM.app_motor_mass("CIANO14 40 new", "L12")
    assert card.mass_properties.mass_kg == pytest.approx(mm.total_kg)
    assert "masses.compute_masses" in card.mass_properties.basis and "not in it" in card.mass_properties.basis
    assert sum(p.mass_kg for p in card.parts if p.counted) == pytest.approx(card.mass_properties.mass_kg)
    assert {p.role for p in card.parts} <= {"magnetic", "conductor", "structural"}
    # privacy: no volume, no dimension, no geometry key anywhere in the dumped card
    assert all(p.volume_cm3 is None for p in card.parts)
    from motor_ai_sim.mcp_tools import assert_no_geometry
    assert_no_geometry(card.model_dump(mode="json"))
    parts_text = str([p.model_dump() for p in card.parts])
    for forbidden in ("slot_height", "air_gap", "tooth_width", "magnet_height", "thickness", "diameter", "_mm"):
        assert forbidden not in parts_text, forbidden
    # the gate refuses a motor part that carries a volume
    leaky = card.model_copy(update={"parts": [card.parts[0].model_copy(update={"volume_cm3": 4.0})] + card.parts[1:]})
    with pytest.raises(ConformanceError, match="material and mass only"):
        assert_component_card(leaky)


def test_motor_without_its_die_has_an_unknown_mass_never_a_placeholder():
    model = dict(s1.load_model("CIANO14 40 new", "L12"), die="NO SUCH DIE")
    with pytest.raises(MM.MotorMassError, match="not found"):
        A.motor_card(model)


def test_cad_only_clearances_are_not_in_the_motor_mass(tmp_path):
    """The magnet mass is the nominal magnet: an offset_rotor among the geometry keys changes nothing the app weighs."""
    from tests._component_dies import make_dies
    dies = make_dies(tmp_path)
    base = MM.app_motor_mass("CIANO14 40 new", "L12", dies_dir=dies)
    f = dies / "CIANO14 40 new" / "L12.yaml"
    cy = yaml.safe_load(f.read_text())
    cy["geometry_overrides"]["offset_rotor"] = 0.5
    f.write_text(yaml.safe_dump(cy), encoding="utf-8")
    again = MM.app_motor_mass("CIANO14 40 new", "L12", dies_dir=dies)
    assert again.total_kg == pytest.approx(base.total_kg, rel=1e-12)
    assert again.components["mag"] == pytest.approx(base.components["mag"], rel=1e-12)


# -- the CAD bill of materials extras -----------------------------------------------------------------

def test_bom_extras_are_listed_but_not_counted(tmp_path):
    from motor_ai_sim.cad import bom
    from tests.test_cad_bom import make
    b, _ = make("L12")
    out = tmp_path / "CIANO14 40 new" / "L12"
    out.mkdir(parents=True)
    bom.write_csv(b, str(out / "CIANO14_40_new_L12_BOM.csv"))
    extra = MM.bom_extra_parts("CIANO14 40 new", "L12", cad_dir=tmp_path)
    names = {p.name for p in extra}
    assert {"Bearings", "Screws", "Glass-fibre rings", "Phase board"} <= names
    assert not any("lamination" in n.lower() or n == "Shaft" for n in names)       # the app mass covers those
    assert all(not p.counted and pm.exists(p.material_id) for p in extra)
    card = A.motor_card(s1.load_model("CIANO14 40 new", "L12"), cad_dir=tmp_path)
    assert_component_card(card)
    unc = [p for p in card.parts if not p.counted]
    assert {p.name for p in unc} == names
    counted = sum(p.mass_kg for p in card.parts if p.counted)
    assert counted == pytest.approx(card.mass_properties.mass_kg)                  # the card mass is still the app's
    assert "CNY" not in str([p.model_dump() for p in card.parts])                 # no price column is ever read
    assert MM.bom_extra_parts("CIANO14 40 new", "L12", cad_dir=tmp_path / "empty") == []


# -- propellers ------------------------------------------------------------------------------------------------

def test_propeller_materials_and_masses_follow_the_catalogue():
    p = pp.get_propeller("tmotor_fpv_10x5")                    # catalogue: glass fiber nylon, 26.5 g
    c = A.propeller_card(p)
    assert_component_card(c)
    (part,) = c.parts
    assert part.material_id == "PA66_GF30" and part.status == "assumed" and part.mass_kg == pytest.approx(0.0265)
    assert "glass fiber nylon" in part.basis
    g = A.propeller_card(pp.get_propeller("tmotor_g28x9_2"))      # single blade 85 g, two blades
    assert g.mass_properties.mass_kg == pytest.approx(0.170) and g.parts[0].material_id == "CFRP_QI_T800_epoxy"
    assert "single blade" in g.mass_properties.basis
    ms = [x for x in pp.list_propellers() if str(x.raw.get("material", "")).lower().startswith("polymer")]
    assert ms and all(A.propeller_material(x)[0] == "PA66_CF30" for x in ms)
    # without any statement a default by vendor is used and says so
    mat, status, why = A.propeller_material(pp.get_propeller("motres_bench_hq1050_3"))
    assert status == "assumed" and "DEFAULT" in why
    # no mass in the catalogue: no mass, no parts (unknown, not 0)
    cf = A.propeller_card(pp.get_propeller("tmotor_cf10x3_3"))
    assert cf.mass_properties is None and cf.parts == []


# -- the placeholders ----------------------------------------------------------------------------------------

def test_placeholder_components_flag_their_part_splits():
    lib = L.get_library()
    for cid in ("example_quad_frame_450", "gearbox_single_stage", "pack_ciano14_40_new_l12_example",
                "ctrl_ciano14_40_new_l12_si_48k"):
        card = lib.get(cid)
        assert card.parts and all(p.status == "placeholder" for p in card.parts), cid
        assert sum(p.mass_kg for p in card.parts) == pytest.approx(card.mass_properties.mass_kg, rel=1e-9)
        assert "placeholder" in lib.entries[cid].flags
        assert all(pm.exists(p.material_id) for p in card.parts)
    assert {p.role for p in lib.get("pack_ciano14_40_new_l12_example").parts} == {"cell", "conductor", "structural"}


# -- the summary and the routes -------------------------------------------------------------------------------

def test_summary_lists_parts():
    row = L.summary(L.get_library().entries["tmotor_fpv_10x5"])
    assert row["parts"][0]["material_id"] == "PA66_GF30" and row["parts"][0]["counted"] is True


@pytest.fixture()
def accounts(tmp_path, monkeypatch):
    from motor_ai_sim import auth
    from motor_ai_sim import users as U
    monkeypatch.setattr(U, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {"owner@example.com"})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", True)
    out = {}
    for who, role in (("owner@example.com", "admin"), ("eng@example.com", "engineer"), ("usr@example.com", "user")):
        U.create_user(who, "password-" + role, role=role, name=role)
        out[role] = {"Authorization": f"Bearer {U.issue_token(who)}"}
    return out


def test_material_routes(accounts):
    eng = accounts["engineer"]
    r = client.get("/api/components/materials", headers=eng)
    assert r.status_code == 200 and "CFRP_QI_T800_epoxy" in r.json()["materials"]["part_materials"]
    assert "20SW1200" in r.json()["materials"]["materials_library"]
    one = client.get("/api/components/materials/Aluminium_6061", headers=eng)
    assert one.status_code == 200 and one.json()["props"]["yield_MPa"]["value"] == 276.0
    assert one.json()["props"]["yield_MPa"]["source"] == "asm_v2" and one.json()["sources"]["asm_v2"]
    assert client.get("/api/components/materials/unobtainium", headers=eng).status_code == 404
    assert client.get("/api/components/materials/copper", headers=accounts["user"]).status_code == 403
    assert client.get("/api/components/materials").status_code == 401
    card = client.get("/api/components/motor_ciano14_40_new_l12", headers=accounts["admin"]).json()["card"]
    assert card["parts"] and ComponentCard.model_validate(card).parts[0].material_id
