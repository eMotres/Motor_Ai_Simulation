"""Owner rule 2026-09-29: «in every motor the shaft must be included» — the
shaft part-state must always resolve to ``included``, no matter what a
config file, a ``?mat=`` request or a stale saved config says.

Covers:
  * the repo's seed template (config/motor_config.yaml) carries no
    ``parts: shaft: reference`` block;
  * ``part_states.resolve``/``part_state`` force the shaft to ``included``
    even when the config/request explicitly says otherwise, while leaving
    other parts' states alone;
  * ``forced_override_note`` reports the override, silently for everyone
    else;
  * ``routes.family._live_parts`` never emits a shaft entry, so neither
    configuration creation nor a duty save can persist ``shaft: reference``.
"""
from __future__ import annotations

import io
import pathlib

import yaml

from motor_ai_sim import part_states as ps


# ── template ──────────────────────────────────────────────────────────────

def test_a_seed_template_has_no_shaft_reference_block():
    p = pathlib.Path("config/motor_config.yaml")
    doc = yaml.safe_load(io.open(p, encoding="utf-8")) or {}
    parts = doc.get("parts") or {}
    assert parts.get("shaft") != "reference"
    assert parts.get("shaft") != "excluded"


# ── part_states.resolve / part_state ─────────────────────────────────────

def test_b_resolve_drops_a_forced_shaft_override():
    out = ps.resolve({"shaft": "reference", "rotor_core": "reference"})
    assert "shaft" not in out
    assert out.get("rotor_core") == "reference"


def test_c_part_state_reads_shaft_as_included_even_when_reference():
    assert ps.part_state("shaft", {"shaft": "reference"}) == ps.INCLUDED
    assert ps.part_state("shaft", {"shaft": "excluded"}) == ps.INCLUDED
    assert ps.is_reference("shaft", {"shaft": "reference"}) is False
    assert ps.counts_in_mass("shaft", {"shaft": "reference"}) is True


def test_d_other_parts_are_unaffected():
    states = {"shaft": "reference", "magnet": "excluded", "slot": "reference"}
    out = ps.resolve(states)
    assert out == {"magnet": "excluded", "slot": "reference"}
    assert ps.part_state("magnet", states) == "excluded"
    assert ps.part_state("slot", states) == "reference"
    assert ps.part_state("shaft", states) == ps.INCLUDED


def test_e_no_override_no_shaft_entry_is_a_silent_no_op():
    assert ps.resolve({"magnet": "excluded"}) == {"magnet": "excluded"}
    assert ps.forced_override_note({"magnet": "excluded"}) == ""


# ── forced_override_note ─────────────────────────────────────────────────

def test_f_forced_override_note_names_the_overridden_part():
    note = ps.forced_override_note({"shaft": "reference"})
    assert "shaft" in note
    assert note != ""


def test_g_forced_override_note_empty_when_shaft_is_included():
    assert ps.forced_override_note({"shaft": "included"}) == ""
    assert ps.forced_override_note({}) == ""


def test_h_state_note_for_shaft_is_empty_since_it_never_leaves_included():
    # state_note() reflects part_state(), which now always reads shaft as
    # included — so the honesty label a datasheet row carries is empty, the
    # same as for any other included part.
    assert ps.state_note("shaft", state=ps.part_state("shaft", {"shaft": "reference"})) == ""


# ── normalize_states still accepts shaft (STATEFUL_PARTS unchanged) ──────

def test_i_normalize_states_still_recognises_shaft_as_a_valid_part():
    # forced_override_note relies on this: it must see the raw "reference"
    # before resolve() drops it, so shaft must not be removed from
    # STATEFUL_PARTS.
    assert "shaft" in ps.STATEFUL_PARTS
    assert ps.normalize_states({"shaft": "reference"}) == {"shaft": "reference"}


# ── routes.family helpers ────────────────────────────────────────────────

def test_j_live_parts_never_emits_a_shaft_entry(monkeypatch):
    from motor_ai_sim.routes import family

    monkeypatch.setattr(
        "motor_ai_sim.part_states.config_part_states",
        lambda: {"shaft": "reference", "magnet": "excluded"})
    out = family._live_parts()
    assert "shaft" not in out
    assert out.get("magnet") == "excluded"


def test_k_live_shaft_note_reports_the_stale_live_state(monkeypatch):
    from motor_ai_sim.routes import family

    monkeypatch.setattr(
        "motor_ai_sim.part_states.config_part_states",
        lambda: {"shaft": "reference"})
    note = family._live_shaft_note()
    assert "shaft" in note


def test_l_live_shaft_note_empty_when_shaft_is_included(monkeypatch):
    from motor_ai_sim.routes import family

    monkeypatch.setattr(
        "motor_ai_sim.part_states.config_part_states",
        lambda: {"magnet": "excluded"})
    assert family._live_shaft_note() == ""
