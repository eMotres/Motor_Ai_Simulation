"""A configuration whose wire stack derives another slot width than its die WARNS (owner 2026-11-13: "add a
warning").  Since the no-implicit-die rule (tests/test_no_implicit_die.py) such a duty save lands in the
configuration instead of forking a die; without a word that is the trap of 2026-11-12 - a round-wire proxy
with a 3.667 mm pocket on the 3.24 mm die, every cogging number computed on a slot the lamination lacks.

Pinned: the pure verdict (with its micron tolerance), the duty save's ``warnings``, the strip context's
``slot_mismatch``; a matching configuration says nothing.  Throwaway catalog under the pytest tmp area.
"""
from __future__ import annotations

import json

import pytest
from fastapi import Response

from motor_ai_sim.routes import family as fam
from tests.test_no_implicit_die import CFG, DIE, GEO, _live, _names, _save, _yaml, cat  # noqa: F401 - fixture


def test_the_same_slot_is_no_mismatch():
    assert fam.config_slot_mismatch(DIE, CFG, GEO, {}) is None
    assert fam.config_slot_mismatch(DIE, CFG, GEO, {"wire_spacing_x": GEO["wire_spacing_x"]}) is None


def test_a_micron_of_rounding_is_no_mismatch():
    # a round-wire proxy stack is rounded to the micron: 3.23998 on a 3.24 slot must not warn
    sx = GEO["wire_spacing_x"] + 0.5 * 0.8 * fam.SLOT_MISMATCH_WARN_MM
    base = fam._derived_slot_width(GEO)
    near = fam._derived_slot_width(dict(GEO, wire_spacing_x=sx))
    assert 0 < abs(near - base) <= fam.SLOT_MISMATCH_WARN_MM
    assert fam.config_slot_mismatch(DIE, CFG, GEO, {"wire_spacing_x": sx}) is None


def test_another_slot_is_reported_with_both_widths():
    w = fam.config_slot_mismatch(DIE, CFG, GEO, {"wire_spacing_x": 0.2})
    assert w["code"] == fam.SLOT_DIFFERS_FROM_DIE == "slot_width_differs_from_die"
    assert w["die_slot_mm"] == round(fam._derived_slot_width(GEO), 3)
    assert w["config_slot_mm"] == round(fam._derived_slot_width(dict(GEO, wire_spacing_x=0.2)), 3)
    assert w["die_slot_mm"] != w["config_slot_mm"]
    assert str(w["config_slot_mm"]) in w["message"] and str(w["die_slot_mm"]) in w["message"]
    assert DIE in w["message"] and CFG in w["message"]


def test_a_duty_save_on_another_slot_is_saved_and_warns(cat, monkeypatch):  # noqa: F811
    live = dict(GEO, wire_spacing_x=0.2, slot_width=fam._derived_slot_width(dict(GEO, wire_spacing_x=0.2)))
    _live(monkeypatch, live)
    out = _save()
    assert out["ok"] and _names(cat) == {DIE}                        # saved, no die created
    assert _yaml(cat / DIE / f"{CFG}.yaml")["geometry_overrides"]["wire_spacing_x"] == 0.2
    assert [w["code"] for w in out["warnings"]] == ["slot_width_differs_from_die"]
    assert out["warnings"][0]["config"] == CFG


def test_a_duty_save_on_the_die_slot_does_not_warn(cat, monkeypatch):  # noqa: F811
    _live(monkeypatch, dict(GEO))
    out = _save()
    assert out["ok"] and "warnings" not in out


def test_the_strip_context_carries_the_warning(cat, monkeypatch):  # noqa: F811
    monkeypatch.setattr(fam, "caller_identity", lambda *_a, **_k: None)
    monkeypatch.setattr(fam, "catalog_access", lambda *_a, **_k: None)
    monkeypatch.setattr(fam, "may_see_config", lambda *_a, **_k: True)
    monkeypatch.setattr(fam, "_can_write_catalog", lambda *_a, **_k: True)
    assert fam.context(Response(), authorization=None)["slot_mismatch"] is None
    c = _yaml(cat / DIE / f"{CFG}.yaml")
    c["geometry_overrides"]["wire_spacing_x"] = 0.2
    (cat / DIE / f"{CFG}.yaml").write_text(json.dumps(c), encoding="utf-8")     # JSON is YAML
    ctx = fam.context(Response(), authorization=None)
    assert ctx["active"] and ctx["slot_mismatch"]["code"] == "slot_width_differs_from_die"
