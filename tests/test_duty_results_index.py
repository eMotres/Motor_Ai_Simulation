"""ONE READ PER LAYER — ``duty_results.index`` and what a memo over it costs.

WHY THIS EXISTS (2026-09-17).  The catalog duty row now carries the coupled
loop's "how long may it run" answer, and that answer lives in
``.duty_results.json`` — not in the configuration yaml the tree is built from.
Two things had to be true before the row could show it, and both are the kind of
thing that is right on the day and quietly wrong a month later:

  * the tree may not ask ``duty_results.get()`` PER CONFIGURATION.  ``get`` is
    shaped for a report of one machine: it re-reads every layer's whole JSON to
    answer for one configuration, so a twelve-configuration die would parse the
    same file twelve times, on a request the Motors tab makes on every render.
    :func:`duty_results.index` reads each layer ONCE and hands back the die;
  * the tree's memo (``routes.family._TREE_CACHE``, keyed on
    ``_tree_signature``) covered the YAML files alone.  A coupled run writes the
    duty-results store and touches no yaml, so a row read from that store would
    have gone stale the moment it became interesting — a duty saying the machine
    is fine hours after the loop said it is not.

Everything here runs against a redirected config directory in the pytest tmp
area; the module asserts the real store was never read.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from motor_ai_sim import duty_results as dr

DIE = "CIANO10 200 opt"
CFG = "L13"
DUTY = "peak"


# ── isolation ────────────────────────────────────────────────────────────────

@pytest.fixture()
def store(tmp_path, monkeypatch):
    """A throwaway workspace whose ``.duty_results.json`` is the only one."""
    from motor_ai_sim import config as cfgmod
    from motor_ai_sim import workspace as ws

    monkeypatch.setattr(cfgmod, "DEFAULT_CONFIG_PATH",
                        str(tmp_path / "motor_config.yaml"), raising=True)
    for var in ("WORKSPACES_ROOT", "SHARED_ROOT", "PUBLISHED_ROOT"):
        monkeypatch.delenv(var, raising=False)
    ws._PROC_CACHE = None                    # the process workspace is memoised
    assert dr.store_path() == tmp_path / ".duty_results.json"
    yield tmp_path
    ws._PROC_CACHE = None


def _write(root: Path, results: dict, name: str = ".duty_results.json") -> Path:
    p = root / name
    p.write_text(json.dumps({"version": 1, "results": results}),
                 encoding="utf-8")
    return p


def _ttl(part: str = "winding", cold: float = 160.2) -> dict:
    """A ``time_to_limit`` block, as ``compact_coupled`` keeps it."""
    return {"within_limits": False, "limiting_part": part,
            "time_to_limit_s": cold,
            "limits_c": {part: 200.0}, "at_point_c": {part: 212.4},
            "over_by_K": {part: 12.4},
            "starts": {"cold": {"time_to_limit_s": cold},
                       "rated": {"time_to_limit_s": 65.4}},
            "note": "over the winding limit by 12 K — reaches 200 °C after "
                    "2 m 40 s from cold, 1 m 05 s from rated"}


def _doc(n_cfgs: int = 3) -> dict:
    return {DIE: {f"L{100 + i}": {DUTY: {"coupled": {"kind": "coupled",
                                                     "time_to_limit": _ttl()}},
                                  "rated": {"thermal": {"kind": "thermal"}}}
                  for i in range(n_cfgs)}}


# ── what it answers ──────────────────────────────────────────────────────────

def test_the_index_is_the_whole_die_configuration_by_configuration(store):
    _write(store, _doc(3))
    idx = dr.index(DIE)
    assert sorted(idx) == ["L100", "L101", "L102"]
    assert sorted(idx["L101"]) == [DUTY, "rated"]
    assert idx["L101"][DUTY]["coupled"]["time_to_limit"]["limiting_part"] \
        == "winding"


def test_the_index_and_get_never_disagree(store):
    _write(store, _doc(2))
    for cfg in ("L100", "L101"):
        assert dr.index(DIE)[cfg] == dr.get(DIE, cfg), (
            "one store, one answer — a caller must not be able to read one "
            "thing through the index and another through get()")


def test_an_unknown_die_or_configuration_is_an_empty_answer(store):
    _write(store, _doc(1))
    assert dr.index("no such die") == {}
    assert dr.index(DIE).get("L999") is None


def test_a_missing_store_is_not_an_error(store):
    assert dr.index(DIE) == {}


def test_a_corrupt_store_reads_as_empty_rather_than_failing_the_catalog(store):
    (store / ".duty_results.json").write_text("{not json", encoding="utf-8")
    assert dr.index(DIE) == {}


# ── the read count, which is the whole point ─────────────────────────────────

def test_each_layer_is_read_exactly_once_however_many_configurations(
        store, monkeypatch):
    _write(store, _doc(12))
    reads: list = []
    real = dr._read_store
    monkeypatch.setattr(dr, "_read_store",
                        lambda p: (reads.append(str(p)), real(p))[1])

    idx = dr.index(DIE)
    assert len(idx) == 12
    assert len(reads) == len(dr.store_paths(DIE)) == 1, (
        "one layer, one read — this is why the tree calls index() and not "
        "get() per configuration")


def test_get_per_configuration_is_what_this_replaces(store, monkeypatch):
    """The cost the index removes, measured rather than asserted in prose."""
    _write(store, _doc(12))
    reads: list = []
    monkeypatch.setattr(dr, "read_all",
                        lambda: (reads.append(1),
                                 {"version": 1, "results": _doc(12)})[1])
    for cfg in dr.index(DIE):
        dr.get(DIE, cfg)
    assert len(reads) == 12


# ── the layers ───────────────────────────────────────────────────────────────

def test_a_deeper_layer_answers_and_the_workspace_lands_on_top(store,
                                                               monkeypatch):
    """Merged exactly the way ``get`` merges: deepest first, per DUTY.

    ``_fallback_stores`` is stubbed rather than a whole three-layer tree stood
    up — ``tests/test_workspace_layers`` owns the layering itself; what is under
    test here is that :func:`index` applies whatever it is handed in the same
    order and at the same granularity ``get`` does.
    """
    shared = _write(store, {DIE: {CFG: {
        DUTY: {"coupled": {"kind": "coupled", "time_to_limit": _ttl(cold=99.0)},
               "thermal": {"kind": "thermal", "T_max": 214.0}},
        "rated": {"coupled": {"kind": "coupled", "from": "the vendor"}}}}},
        name=".shared_duty_results.json")
    _write(store, {DIE: {CFG: {
        DUTY: {"coupled": {"kind": "coupled",
                           "time_to_limit": _ttl(cold=160.2)}}}}})
    monkeypatch.setattr(dr, "_fallback_stores", lambda die: [shared])

    assert dr.store_paths(DIE) == [shared, dr.store_path()]
    node = dr.index(DIE)[CFG]
    assert node[DUTY]["coupled"]["time_to_limit"]["starts"]["cold"][
        "time_to_limit_s"] == 160.2, "my own re-solve wins"
    assert "thermal" not in node[DUTY], (
        "first hit wins PER DUTY, not per kind — same granularity as get()")
    assert node["rated"]["coupled"]["from"] == "the vendor", (
        "a duty I have not re-solved still shows the author's answer")


def test_a_published_die_is_found_under_its_plain_name(store, monkeypatch):
    """A published die is addressed ``"<die> · by <name>"``; its own store,
    written by its author, knows it as ``<die>``."""
    published = _write(store, {DIE: {CFG: {DUTY: {"coupled": {"kind": "coupled"}}}}},
                       name=".published_duty_results.json")
    monkeypatch.setattr(dr, "_fallback_stores", lambda die: [published])
    monkeypatch.setattr(dr, "_plain_die", lambda die: str(die).split(" · by ")[0])

    idx = dr.index(f"{DIE} · by Vadim")
    assert idx[CFG][DUTY]["coupled"]["kind"] == "coupled"


def test_the_callers_own_spelling_wins_over_the_plain_one(store, monkeypatch):
    label = f"{DIE} · by Vadim"
    _write(store, {DIE: {CFG: {DUTY: {"coupled": {"whose": "the author's"}}}},
                   label: {CFG: {DUTY: {"coupled": {"whose": "mine"}}}}})
    monkeypatch.setattr(dr, "_plain_die", lambda die: str(die).split(" · by ")[0])
    assert dr.index(label)[CFG][DUTY]["coupled"]["whose"] == "mine"


# ── the signature a memo rests on ────────────────────────────────────────────

def test_the_signature_names_every_store_once(store, monkeypatch):
    other = _write(store, {}, name=".other_duty_results.json")
    monkeypatch.setattr(dr, "_fallback_stores", lambda die: [other])
    _write(store, _doc(1))

    sig = dr.store_signature([DIE, "another die", DIE])
    assert len(sig) == 2, "the workspace store is stat-ed ONCE, not per die"
    assert {row[0] for row in sig} == {str(other), str(dr.store_path())}


def test_writing_a_coupled_answer_changes_the_signature(store):
    """The regression this exists for: a coupled run writes THIS file and no
    yaml, so a signature that did not cover it would keep serving the row from
    before the run."""
    before = dr.store_signature([DIE])
    assert before and before[0][1] == -1, "an absent store is part of the answer"

    dr.record(DIE, CFG, DUTY, "coupled", {"time_to_limit": _ttl()})
    after = dr.store_signature([DIE])
    assert after != before

    dr.record(DIE, CFG, DUTY, "coupled", {"time_to_limit": _ttl(cold=12.0)})
    assert dr.store_signature([DIE]) != after, (
        "a re-run at the same size must still move the signature")


def test_an_empty_die_list_is_an_empty_signature(store):
    assert dr.store_signature([]) == ()


# ── and the tree's own memo, end to end ──────────────────────────────────────

def test_the_duty_row_carries_the_limiting_parts_own_scalars():
    """``routes.family._time_to_limit_row`` — the chip's half of the block.

    FLATTENED, and named differently from the block on purpose: the block
    carries ``limits_c`` / ``at_point_c`` per PART, the row carries the limiting
    part's two scalars, and neither shape may be mistaken for the other.
    """
    from motor_ai_sim.routes import family as fam

    row = fam._time_to_limit_row({"kind": "coupled", "time_to_limit": _ttl()})
    assert row == {"part": "winding", "at_point_c": 212.4, "limit_c": 200.0,
                   "over_by_K": 12.4, "cold_s": 160.2, "rated_s": 65.4,
                   "note": _ttl()["note"]}
    assert "starts" not in row and "parts" not in row and "network" not in row, (
        "the tree is fetched on every catalog render — descriptions only")


def test_a_point_inside_every_limit_grows_no_row_at_all():
    from motor_ai_sim.routes import family as fam

    for coupled in (None, {}, {"kind": "coupled"},
                    {"time_to_limit": {"within_limits": True,
                                       "time_to_limit_s": None}}):
        assert fam._time_to_limit_row(coupled) is None
        assert fam._time_to_limit_kv(coupled) == {}, (
            "an ABSENT key, never a null one: a machine that is not over "
            "anything has no time to a limit")
    assert fam._time_to_limit_kv({"time_to_limit": _ttl()}).keys() == \
        {"time_to_limit"}


def test_an_over_limit_point_the_network_never_reaches_keeps_its_sentence():
    """No time, but the row still travels: the chip draws nothing (that is the
    web side's rule) while the block's own explanation stays readable."""
    from motor_ai_sim.routes import family as fam

    block = dict(_ttl(), starts={"cold": {"time_to_limit_s": None}},
                 note="over the winding limit by 12 K — the step response of "
                      "this network never reaches 200 °C")
    row = fam._time_to_limit_row({"time_to_limit": block})
    assert row["cold_s"] is None and row["rated_s"] is None
    assert "never reaches" in row["note"]


@pytest.fixture()
def catalog(store, monkeypatch):
    """One die, one configuration, two duties — and an admin looking at it."""
    import yaml
    from motor_ai_sim.routes import family as fam
    from motor_ai_sim import motor_access as ma

    dies = store / "dies"
    (dies / DIE).mkdir(parents=True)
    (dies / DIE / "die.yaml").write_text(yaml.safe_dump(
        {"name": DIE, "locked": False,
         "geometry": {"num_slots": 24, "num_poles": 28,
                      "stator_diameter": 200.0}}), encoding="utf-8")
    (dies / DIE / f"{CFG}.yaml").write_text(yaml.safe_dump(
        {"name": CFG, "die": DIE,
         "duties": [{"name": DUTY, "mode": "motor"},
                    {"name": "rated", "mode": "motor"}]}), encoding="utf-8")
    monkeypatch.setattr(fam, "_DIES_DIR", dies)
    monkeypatch.setattr(fam, "catalog_access", lambda _a=None: {
        "mode": ma.MODE_ALL, "dies": frozenset(), "email": None,
        "is_admin": True})
    return fam


def _duties(fam) -> dict:
    from fastapi import Response
    out = fam.tree(Response())
    cfgs = out["dies"][0]["configs"][0]
    return {d["name"]: d for d in cfgs["duties"]}


def test_the_tree_puts_the_chips_row_on_the_duty_that_is_over_a_limit(catalog):
    """End to end: a coupled record in the store becomes ONE key on ONE duty."""
    dr.record(DIE, CFG, DUTY, "coupled", {"time_to_limit": _ttl()})
    dr.record(DIE, CFG, "rated", "coupled",
              {"time_to_limit": {"within_limits": True,
                                 "time_to_limit_s": None,
                                 "note": "every part is inside its limit"}})

    duties = _duties(catalog)
    assert duties[DUTY]["time_to_limit"]["part"] == "winding"
    assert duties[DUTY]["time_to_limit"]["cold_s"] == 160.2
    assert "time_to_limit" not in duties["rated"], (
        "a point inside every limit grows no key — the row draws nothing")


def test_a_duty_with_no_coupled_record_is_untouched(catalog):
    duties = _duties(catalog)
    assert "time_to_limit" not in duties[DUTY]
    assert duties[DUTY]["name"] == DUTY, "the rest of the row is what it was"


def test_the_memoised_tree_still_answers_after_a_coupled_run(catalog):
    """THE SECOND HALF of the feature.  The tree is memoised on
    ``_tree_signature``; a coupled run writes the duty-results store and no
    yaml, so without the store in that signature this assertion fails with the
    duty row from BEFORE the run — which is the stale-chip bug itself."""
    assert "time_to_limit" not in _duties(catalog)[DUTY], "warm the memo"

    dr.record(DIE, CFG, DUTY, "coupled", {"time_to_limit": _ttl()})
    assert _duties(catalog)[DUTY]["time_to_limit"]["cold_s"] == 160.2

    dr.record(DIE, CFG, DUTY, "coupled", {"time_to_limit": _ttl(cold=12.0)})
    assert _duties(catalog)[DUTY]["time_to_limit"]["cold_s"] == 12.0, (
        "a re-run at the same file size must still be seen")


def test_the_tree_signature_falls_when_the_duty_results_store_moves(catalog):
    """``routes.family._tree_signature`` must cover the store, or the chip on
    the duty row goes stale exactly when it starts to matter."""
    fam = catalog
    before = fam._tree_signature(False)
    assert before, "a readable catalog has a signature"

    dr.record(DIE, CFG, DUTY, "coupled", {"time_to_limit": _ttl()})
    assert fam._tree_signature(False) != before, (
        "a coupled run touches no yaml — without the store in the signature "
        "the memoised tree would keep serving the row from before it")
