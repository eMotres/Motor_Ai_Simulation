"""Estimates stored and quotes shared BEFORE the next unit / rate table (#801) read exactly as before (Astra review 2026-11-26): no new key
in the normalised estimate, the computed result, the diff, the weeks or the shared quote.  The golden file was produced by the code of the
base branch (without #801, commit 1ba6e9edeaffb8ed0e40f6df3e908bb4ef6a86aa) from a stored v2 estimate; neutral ids and made-up numbers.
Provenance: ``tests/data/regenerate_cost_estimate_pre_801.py`` (run against that commit; its docstring has the command).  Astra reproduced all
five sections from the base modules, so the file is reproducible, not just checked in."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from motor_ai_sim.development import cost_estimate as CE
from motor_ai_sim.development import cost_estimate_store as CS
from motor_ai_sim.development import cost_quote as Q

from tests.test_project_quote import _h, _quote, client  # noqa: F401

GOLDEN = json.loads((Path(__file__).parent / "data" / "cost_estimate_pre_801.json").read_text(encoding="utf-8"))
NEW_SETTINGS = ("ship_parcels", "ship_box_cm", "ship_service")


def _dump(value):
    return json.dumps(value, indent=1, sort_keys=False)


@pytest.fixture
def schema():
    return CE.load_schema()


def test_an_estimate_stored_before_the_rate_table_reads_and_computes_exactly_as_before(schema):
    stored = json.loads(json.dumps(GOLDEN["stored"]))
    est = CE.from_stored(stored, schema, "alpha")
    assert _dump(est) == _dump(GOLDEN["normalized"])                     # key order included
    assert not any(k in est["settings"] for k in NEW_SETTINGS) and "next_unit" not in est
    computed = CE.compute(est, schema)
    assert _dump(computed) == _dump(GOLDEN["computed"]) and "next_unit" not in computed
    assert _dump(CE.diff(None, est, schema)) == _dump(GOLDEN["diff"])
    assert _dump([CE.iteration_weeks(i) for i in est["iterations"]]) == _dump(GOLDEN["weeks"])
    assert stored == GOLDEN["stored"]                                    # reading never rewrites the stored document


def test_saving_an_old_estimate_unchanged_does_not_add_the_new_keys(schema):
    est = CE.from_stored(json.loads(json.dumps(GOLDEN["stored"])), schema, "alpha")
    again = CE.normalize_estimate(json.loads(json.dumps(est)), schema, "alpha")
    assert _dump(again) == _dump(est)


def test_a_new_estimate_and_an_explicit_edit_do_carry_the_new_fields(schema):
    fresh = CE.default_estimate(schema, "alpha")
    assert all(k in fresh["settings"] for k in NEW_SETTINGS) and "next_unit" in fresh
    edited = json.loads(json.dumps(GOLDEN["normalized"]))
    edited["settings"]["ship_service"] = "air_freight"
    edited["next_unit"] = {"price": 3900.0}
    got = CE.normalize_estimate(edited, schema, "alpha")
    assert got["settings"]["ship_service"] == "air_freight" and "ship_parcels" not in got["settings"]
    assert got["next_unit"]["price"] == 3900.0
    assert CE.compute(got, schema)["next_unit"]["overridden"] is True


def test_an_old_snapshot_is_quoted_with_the_old_whitelist_and_the_same_numbers(schema):
    est = CE.from_stored(json.loads(json.dumps(GOLDEN["stored"])), schema, "alpha")
    old = Q.build_quote(est, schema, "2026-11-03", terms=1)               # a snapshot without the ``terms`` mark reads as 1
    assert _dump(old) == _dump(GOLDEN["quote"]) and tuple(old) == Q.QUOTE_KEYS_V1
    new = Q.build_quote(est, schema, "2026-11-03")
    assert tuple(new) == Q.QUOTE_KEYS and new["next_unit"] is None
    assert {k: v for k, v in new.items() if k not in ("next_unit", "excluded")} == old    # nothing else moves


def test_a_quote_shared_before_the_change_comes_back_from_the_route_byte_for_byte(client):
    def make(_previous, n):
        return {"n": n, "id": "%04d" % n, "note": "shared", "created_at": "2026-11-03T10:00:00+00:00", "created_by": "admin@example.test",
                "revision": GOLDEN["stored"]["revision"], "estimate": json.loads(json.dumps(GOLDEN["stored"]))}      # no ``terms`` key
    snap = CS.add_snapshot("alpha", make)
    CS.set_shared("alpha", {"snapshot_n": snap["n"], "revision": snap["revision"], "shared_at": "2026-11-03T10:00:00+00:00", "shared_by": "admin@example.test"})
    got = _quote(client, "owner")
    assert got.status_code == 200
    assert _dump(got.json()) == _dump(GOLDEN["quote"])
    assert "next_unit" not in got.json() and "excluded" not in got.json()
