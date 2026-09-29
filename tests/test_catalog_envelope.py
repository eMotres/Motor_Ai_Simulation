"""Unified catalogues, stage 1 — the envelope must not move a single number.

Owner-approved gate (catalogs_unified_proposal_2026-09-28.md §4): for EVERY
card, the old loader and the loader-through-the-adapter give bit-identical
numbers, and both equal what the old loader gave on the file BEFORE the
envelope was added (``tests/fixtures/catalog_golden/*_pre_envelope.json``,
frozen from ``origin/pre-migration-freeze-2026-09-15`` + the W 638/2-2Z card,
before any edit).  Floats are compared through ``repr`` — exact, not approx.

Also pinned here:
* every "catalogue value to verify" / "APPROXIMATE" comment of the old file is
  now a structured ``prov`` flag (verify: true / type: estimate) — none lost;
* 61811-2RS1 stays the VALIDATED card: 0.387 measured vs 0.400 model;
* every envelope passes ``validate_envelope``.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from motor_ai_sim import bearings as brg
from motor_ai_sim.catalog import bearings as cb
from motor_ai_sim.catalog import devices as cd
from motor_ai_sim.catalog.envelope import field_prov, validate_envelope
from motor_ai_sim.inverter import devices as dv

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "tests" / "fixtures" / "catalog_golden"
LIB = ROOT / "config" / "bearings_library.yaml"
DEV_DIR = ROOT / "config" / "devices"

B_GOLD = json.loads((GOLD / "bearings_pre_envelope.json").read_text(encoding="utf-8"))
D_GOLD = json.loads((GOLD / "devices_pre_envelope.json").read_text(encoding="utf-8"))


def _canon(o) -> str:
    return json.dumps(o, sort_keys=True, ensure_ascii=False, default=str)


def _frozen(dc) -> dict:
    """A dataclass as the golden file spells it: floats by repr."""
    d = {k: (repr(v) if isinstance(v, float) else v)
         for k, v in dataclasses.asdict(dc).items()}
    if "friction" in d:
        d["friction"] = {k: repr(v) for k, v in dc.friction.items()}
    return d


@pytest.fixture(autouse=True)
def _repo_library(monkeypatch):
    """Read the REPO files — never a shared/sandbox copy that may predate the
    envelope."""
    monkeypatch.setattr(brg, "_LIB_PATH", LIB)
    monkeypatch.setattr(brg, "_library", None)
    brg._CARD_CACHE.clear()
    brg._LUBE_CACHE.clear()
    monkeypatch.setattr(dv, "devices_dir", lambda: DEV_DIR)
    yield
    brg._CARD_CACHE.clear()
    brg._LUBE_CACHE.clear()


# ---------------------------------------------------------------------------
# Bearings + lubricants
# ---------------------------------------------------------------------------

def test_the_same_cards_exist_as_before():
    assert sorted(brg.list_bearings()) == sorted(B_GOLD["bearings"])
    assert sorted(brg.list_lubricants()) == sorted(B_GOLD["lubricants"])


@pytest.mark.parametrize("name", sorted(B_GOLD["bearings"]))
def test_bearing_old_loader_and_adapter_bit_identical(name):
    old = brg.get_bearing(name)
    env = cb.bearing_envelope(name)
    new = cb.to_bearing_card(env)
    assert new == old                                   # dataclass equality
    assert _frozen(old) == B_GOLD["bearings"][name]     # == before the envelope
    assert _frozen(new) == B_GOLD["bearings"][name]
    # and the body is byte-for-byte the pre-envelope entry (no other key moved)
    h = hashlib.sha256(_canon(env["body"]).encode()).hexdigest()
    assert h == B_GOLD["raw_sha256"]["bearings/" + name]


@pytest.mark.parametrize("name", sorted(B_GOLD["lubricants"]))
def test_lubricant_old_loader_and_adapter_bit_identical(name):
    old = brg.get_lubricant(name)
    env = cb.lubricant_envelope(name)
    new = cb.to_lubricant(env)
    assert new == old
    assert _frozen(old) == B_GOLD["lubricants"][name]
    h = hashlib.sha256(_canon(env["body"]).encode()).hexdigest()
    assert h == B_GOLD["raw_sha256"]["lubricants/" + name]


@pytest.mark.parametrize("flag", B_GOLD["comment_flags"],
                         ids=lambda f: f"{f['card']}.{f['field']}")
def test_every_old_verify_or_approx_comment_is_now_a_prov_flag(flag):
    env = (cb.bearing_envelope if flag["section"] == "bearings"
           else cb.lubricant_envelope)(flag["card"])
    field = flag["field"]
    if field in ("R1", "R2", "R3", "S1", "S2", "K_z", "mu_sl"):
        field = "friction." + field
    p = env["prov"].get(field)
    assert p, f"{flag['card']}.{field}: the comment's flag was lost"
    if flag["flag"] == "verify":
        assert p.get("verify") is True
    else:
        assert p.get("type") == "estimate" and p.get("note")


def test_no_flag_comment_is_left_in_the_yaml():
    """Moved, not copied: the loaders never showed a comment to anybody."""
    for line in LIB.read_text(encoding="utf-8").splitlines():
        code, _, comment = line.partition("#")
        if comment and code.strip() and "note:" not in code:
            assert "to verify" not in comment and "APPROXIMATE" not in comment, line


@pytest.mark.parametrize("kind", ["bearing", "lubricant"])
def test_every_bearing_envelope_is_well_formed(kind):
    envs = cb.bearing_envelopes() if kind == "bearing" else cb.lubricant_envelopes()
    for env in envs:
        assert validate_envelope(env) == [], env["id"]
        assert env["sources"], env["id"]
        assert env["revision"].get("n") == 1


def test_61811_2rs1_is_the_validated_card_and_its_figure_stays_pinned():
    env = cb.bearing_envelope("61811-2RS1")
    assert env["status"] == "validated"
    v = env["validation"][0]
    assert v["ref"] == 0.387 and v["model"] == 0.400 and v["ref_tol"] == 0.025
    assert env["prov"]["seal_ds"]["type"] == "measured"
    # the number itself, through the ADAPTER's card, equals the old loader's
    card_new = cb.to_bearing_card(env)
    card_old = brg.get_bearing("61811-2RS1")
    kw = dict(rpm=2000.0, f_r_n=6.51, nu_mm2_s=30.0)
    m_new = 2.0 * brg.friction(card_new, **kw)["M_total_Nm"]
    m_old = 2.0 * brg.friction(card_old, **kw)["M_total_Nm"]
    assert m_new == m_old
    assert m_new == pytest.approx(0.400, abs=0.01)
    assert abs(m_new - v["ref"]) <= v["ref_tol"]


def test_default_prov_rule_is_datasheet_from_the_first_source():
    env = cb.bearing_envelope("61811-2RS1")
    p = field_prov(env, "d")
    assert p["type"] == "datasheet" and p["src"] == "skf_catalogue" and p["default"]


# ---------------------------------------------------------------------------
# Power devices
# ---------------------------------------------------------------------------

def test_the_same_devices_exist_as_before():
    assert sorted(p.stem for p in DEV_DIR.glob("*.yaml")) == sorted(D_GOLD)


def _numbers(card) -> dict:
    nums = {}
    for t in (25.0, 100.0, 175.0):
        nums[f"r_ds_on_ohm@{t}"] = repr(card.r_ds_on_ohm(t, 18.0))
    e = card.e_switch(i_d_A=100.0, t_j_c=125.0, v_dc_V=600.0, source="datasheet")
    nums["e_switch_datasheet"] = {k: repr(e[k]) for k in ("e_on_J", "e_off_J", "e_fr_J")}
    nums["r_th_jc_k_w"] = repr(card.r_th_jc_k_w)
    nums["i_d_rating_100"] = repr(card.i_d_rating(100.0).get("i_a"))
    return nums


@pytest.mark.parametrize("part", sorted(D_GOLD))
def test_device_old_loader_and_adapter_bit_identical(part):
    old = dv.get_device(part)
    env = cd.device_envelope(part)
    new = cd.to_device_card(env)
    # the card data, minus the envelope block, is the pre-envelope card exactly
    body = {k: v for k, v in old.doc.items() if k != "catalog"}
    assert new.doc == body
    assert hashlib.sha256(_canon(body).encode()).hexdigest() == D_GOLD[part]["sha256"]
    # and the loss model's numbers agree, old == adapter == before
    assert _numbers(old) == D_GOLD[part]["numbers"]
    assert _numbers(new) == D_GOLD[part]["numbers"]


@pytest.mark.parametrize("part", sorted(D_GOLD))
def test_device_envelope_is_well_formed_and_carries_provenance(part):
    env = cd.device_envelope(part)
    assert validate_envelope(env) == []
    assert env["sources"][0]["id"] == "datasheet"
    assert env["sources"][0].get("url") or env["sources"][0].get("file")
    for blk in ("ratings", "r_ds_on", "thermal"):
        assert env["prov"][blk]["type"] in ("datasheet", "derived")
    assert env["status"] == "active"


def test_unpublished_device_values_are_flagged_not_hidden():
    from motor_ai_sim.private_data import ENV_VAR, private_path
    part = "WCMS900B170E53"
    if private_path("config", "devices", f"{part}.yaml") is None:
        pytest.skip(f"the card of {part} is private data; set {ENV_VAR} to "
                    "the private data checkout to run this test")
    env = cd.device_envelope(part)
    assert env["prov"]["thermal.r_th_jc_k_w.typ"]["note"] == "not published (null)"
    assert env["prov"]["ratings.i_d_pulsed_A"]["note"] == "not published (null)"
    assert "figure" in env["prov"]["r_ds_on"]["note"]
