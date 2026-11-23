"""Sheet generation (owner 2026-11-23; ``motor_ai_sim.sheet_generation``): a die whose sheet was replaced (generation > 1) takes
only results of its CURRENT sheet - client saves carry the signed block of their run, in-process records stamp the solved machine,
the mirror and the history restore follow the same rule - while a die at generation 1 behaves exactly as before.  Synthetic die
and values; payload-only."""
from __future__ import annotations

import json
import os
import time

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from motor_ai_sim import duty_results as DR
from motor_ai_sim import sheet_generation as SG
from test_config_archive import DIE, OLDCFG, OTHER, client, env  # noqa: F401 - the shared fixture

SHEET_A = {k: 1.0 + i / 10 for i, k in enumerate(SG.SHEET_KEYS)}
SHEET_B = {k: v * 1.05 for k, v in SHEET_A.items()}


def _die_path(env, die=DIE):
    return env["srv"] / "shared" / "dies" / die / "die.yaml"


def _set_die(env, sheet, generation=None, die=DIE, since=None):
    p = _die_path(env, die)
    d = yaml.safe_load(p.read_text())
    d["geometry"].update(sheet)
    if generation:
        d["sheet_generation"] = generation
        d["sheet_hash"] = SG.sheet_hash(d["geometry"])
        d["sheet_since"] = since or "2000-01-01T00:00:00"
    p.write_text(yaml.safe_dump(d, sort_keys=False))
    return d


@pytest.fixture()
def gen(env, monkeypatch):
    monkeypatch.setattr(DR, "store_path", lambda: env["tmp"] / "duty_results.json")
    cp = env["srv"] / "shared" / "dies" / DIE / f"{OLDCFG}.yaml"
    c = yaml.safe_load(cp.read_text())
    c["duties"] = [{"name": "rated", "rpm": 1000, "result": {"efficiency_pct": 90.0}}]
    cp.write_text(yaml.safe_dump(c, sort_keys=False))
    return env


def test_the_hash_is_the_sheet_content_only():
    g = dict(SHEET_A, motor_length=40, num_wires_per_slot=14)
    assert SG.sheet_hash(g) == SG.sheet_hash(dict(SHEET_A, motor_length=170, num_wires_per_slot=10))   # config-level keys: out
    assert SG.sheet_hash(SHEET_A) == SG.sheet_hash({k: v + 1e-9 for k, v in SHEET_A.items()})           # float noise
    assert SG.sheet_hash(SHEET_A) != SG.sheet_hash(SHEET_B)
    assert SG.canonical({})["tooth_width"] is None


def test_a_block_is_valid_only_as_the_server_issued_it(gen):
    b = SG.issue(SHEET_A, "fp-1")
    assert SG.verified_hash(b) == SG.sheet_hash(SHEET_A)
    for bad in ({**b, "hash": SG.sheet_hash(SHEET_B)}, {**b, "geo_sig": "fp-2"}, {**b, "issued_at": b["issued_at"] + 1},
                {**b, "sig": "0" * 64}, {k: v for k, v in b.items() if k != "sig"}, {**b, "issued_at": True}, "x", None):
        assert SG.verified_hash(bad) is None


def _result(env, block=None, die=DIE):
    body = {"die": die, "config": OLDCFG, "duty": "rated", "result": {"efficiency_pct": 95.0}}
    if block is not None:
        body["result_sheet"] = block
    return client.post("/api/family/duty_result", headers=env["admin"], json=body)


def test_a_die_at_generation_one_asks_nothing(gen):
    _set_die(gen, SHEET_A)
    assert _result(gen).status_code == 200                                   # no block: as before
    assert _result(gen, {"garbage": 1}).status_code == 200                   # nothing is checked at generation 1


def test_a_replaced_sheet_takes_only_a_signed_block_of_its_current_sheet(gen):
    _set_die(gen, SHEET_B, generation=2)
    r = _result(gen)
    assert r.status_code == 409 and "earlier sheet" in r.text and "reload" in r.text   # an old tab: told what to do
    assert _result(gen, SG.issue(SHEET_A)).status_code == 409                # a run of the earlier sheet
    forged = {**SG.issue(SHEET_A), "hash": SG.sheet_hash(SHEET_B)}
    assert _result(gen, forged).status_code == 409                           # altered = no block
    assert _result(gen, SG.issue(SHEET_B)).status_code == 200                # the current sheet


def test_a_result_solved_on_another_die_with_the_same_sheet_is_accepted(gen):
    """The E recompute ran on the owner's copy (die X); the same sheet content is valid for the replaced die Y."""
    _set_die(gen, SHEET_B, generation=3)
    block_from_x = SG.issue(dict(SHEET_B, motor_length=170), "fp-on-another-die")
    assert _result(gen, block_from_x).status_code == 200
    assert _result(gen, SG.issue(dict(SHEET_B, tooth_width=9.9))).status_code == 409


def test_duty_and_duty_run_saves_of_a_computed_state_are_checked(gen):
    _set_die(gen, SHEET_B, generation=2)
    duty = {"name": "rated", "summary": {"T_avg_Nm": 1.0}}
    r = client.post("/api/family/duty", headers=gen["admin"], json={"die": DIE, "config": OLDCFG, "duty": duty})
    assert r.status_code == 409
    duty_ok = {**duty, "result_sheet": SG.issue(SHEET_B)}
    assert client.post("/api/family/duty", headers=gen["admin"], json={"die": DIE, "config": OLDCFG, "duty": duty_ok}).status_code != 409
    settings_only = {"name": "rated", "rpm": 1200}
    assert client.post("/api/family/duty", headers=gen["admin"],
                       json={"die": DIE, "config": OLDCFG, "duty": settings_only}).status_code != 409   # a definition is no result
    run = {"die": DIE, "config": OLDCFG, "duty": "rated", "drive": "current", "summary": {"T_avg_Nm": 1.0}}
    assert client.post("/api/family/duty_run", headers=gen["admin"], json=run).status_code == 409
    assert client.post("/api/family/duty_run", headers=gen["admin"],
                       json={**run, "result_sheet": SG.issue(SHEET_B)}).status_code != 409


def test_an_in_process_record_of_an_earlier_sheet_is_refused_and_said_so(gen, monkeypatch):
    import motor_ai_sim.config as C
    _set_die(gen, SHEET_B, generation=2)
    app = FastAPI()
    SG.install(app)

    @app.get("/solve")
    def solve(sheet: str = "old"):
        geo = SHEET_A if sheet == "old" else SHEET_B
        monkeypatch.setattr(C, "get_config", lambda: {"geometry": dict(geo)})
        ok = DR.record(DIE, OLDCFG, "rated", "thermal", {"computed_at": "t", "coil_c": 90.0})
        return {"solved": True, "recorded": ok}
    c = TestClient(app)
    r = c.get("/solve?sheet=old")
    assert r.json()["recorded"] is False and r.json()["not_saved"] == [SG.REFUSED_TEXT]
    assert "earlier sheet" in r.headers["X-Result-Not-Saved"]
    r = c.get("/solve?sheet=new")
    assert r.json() == {"solved": True, "recorded": True}                    # nothing refused: untouched
    row = json.loads((gen["tmp"] / "duty_results.json").read_text())["results"][DIE][OLDCFG]["rated"]["thermal"]
    assert row["sheet_hash"] == SG.sheet_hash(SHEET_B)


def test_a_record_at_generation_one_is_written_exactly_as_before(gen, monkeypatch):
    import motor_ai_sim.config as C
    _set_die(gen, SHEET_A)
    monkeypatch.setattr(C, "get_config", lambda: {"geometry": dict(SHEET_B)})
    assert DR.record(DIE, OLDCFG, "rated", "thermal", {"computed_at": "t", "coil_c": 90.0})
    row = json.loads((gen["tmp"] / "duty_results.json").read_text())["results"][DIE][OLDCFG]["rated"]["thermal"]
    assert "sheet_hash" not in row and SG.refusals() == []


def test_the_mirror_merge_keeps_only_rows_of_the_current_sheet(gen):
    from motor_ai_sim.regions import catalog_mirror as CM
    root = gen["srv"] / "shared"
    rows = {OLDCFG: {"rated": {"thermal": {"coil_c": 1.0}, "coupled": {"coil_temp_c": 2.0, "sheet_hash": SG.sheet_hash(SHEET_B)},
                               "modes": {"f": 3.0, "sheet_hash": SG.sheet_hash(SHEET_A)}}}}
    _set_die(gen, SHEET_A)
    assert CM._current_sheet_rows(root, DIE, rows) == rows                    # generation 1: as they come
    _set_die(gen, SHEET_B, generation=2)
    assert CM._current_sheet_rows(root, DIE, rows) == {OLDCFG: {"rated": {"coupled": rows[OLDCFG]["rated"]["coupled"]}}}


def test_the_history_restore_cannot_bring_an_earlier_sheet_back(gen):
    from motor_ai_sim.routes import family as F
    hdir = F._hist_dir(DIE)
    hdir.mkdir(parents=True, exist_ok=True)
    old = hdir / f"{OLDCFG}.20200101T000000.yaml"
    old.write_text((gen["srv"] / "shared" / "dies" / DIE / f"{OLDCFG}.yaml").read_text())
    os.utime(old, (time.time() - 86400 * 400, time.time() - 86400 * 400))
    die_snap = hdir / "die.20200101T000000.yaml"
    die_snap.write_text(_die_path(gen).read_text())
    _set_die(gen, SHEET_B, generation=2, since="2026-01-01T00:00:00")
    r = client.post("/api/family/history/restore", headers=gen["admin"], json={"die": DIE, "config": OLDCFG, "stamp": "20200101T000000"})
    assert r.status_code == 409 and "before the sheet" in r.text
    r = client.post("/api/family/history/restore", headers=gen["admin"], json={"die": DIE, "config": None, "stamp": "20200101T000000"})
    assert r.status_code in (404, 409)                                       # never the earlier sheet through the history


def test_the_transient_answer_carries_the_signed_block_of_its_geometry(gen, monkeypatch):
    import motor_ai_sim.config as C
    from motor_ai_sim.routes import simulation as S
    monkeypatch.setattr(C, "get_config", lambda: {"geometry": dict(SHEET_B)})
    out = S._with_result_sheet(lambda **kw: {"summary": {"T": 1.0}, "geo_fingerprint": "fp"})(geo=None)
    assert SG.verified_hash(out["summary"]["result_sheet"]) == SG.sheet_hash(SHEET_B)
    assert S._with_result_sheet(lambda **kw: {"no_summary": 1})(geo=None) == {"no_summary": 1}


def test_the_coupled_warm_start_skips_a_state_of_an_earlier_sheet(gen):
    d = _set_die(gen, SHEET_B, generation=2)
    assert SG.row_allowed(d, {"sheet_hash": SG.sheet_hash(SHEET_B)})
    assert not SG.row_allowed(d, {"sheet_hash": SG.sheet_hash(SHEET_A)}) and not SG.row_allowed(d, {})
    assert SG.row_allowed(_set_die(gen, SHEET_A, die=OTHER), {})               # generation 1: any row
