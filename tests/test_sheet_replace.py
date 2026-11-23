"""Replace the sheet of ONE die (owner 2026-11-23; ``motor_ai_sim.sheet_replace`` + ``/api/die-sheet``).  Payload-only, a synthetic
die in temporary layers: the dry run, the 12 allowed keys only, refusals (other key, no change, running job, frozen revision,
non-admin, no confirm), the backup, the marks on every duty and stored row (unstamped included), clearing only by a save on the new
sheet, and the outward readers (MCP, public card, help catalogue) dropping marked results while an unmarked one is served exactly
as before."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from motor_ai_sim import sheet_replace as DS
from motor_ai_sim import duty_results as DR
from test_config_archive import ADMIN, A, DIE, NEWCFG, OLDCFG, OTHER, client, env  # noqa: F401 - the shared fixture

# synthetic sheets for the fixture's small test die (no catalogue values in a public repository)
OLD = {"tooth_width": 1.0, "tooth2_width": 0.5, "cut_width": 0.4, "slot_height": 2.0, "stator_inner_radius": 4.0,
       "rotor_outer_radius": 3.8, "rotor_inner_radius": 2.5, "magnet_height": 0.9, "magnet_fill_up": 0.3,
       "magnet_up_gap": 0.1, "rotor_hole": 0.2, "rotor_house_height": 0.3}
NEW = {k: round(v * 1.1, 4) for k, v in OLD.items()}
URL = f"/api/die-sheet/{DIE}"


def _dies(env) -> Path:
    return env["srv"] / "shared" / "dies"


@pytest.fixture()
def die(env, monkeypatch):
    """The fixture's die with the OLD sheet, two configurations with duties (one stamped on the old sheet, one unstamped), and
    stored rows in the results store."""
    from motor_ai_sim import config_archive as CA
    monkeypatch.setattr(CA, "_live_jobs_strict", lambda d: [])
    monkeypatch.setattr(DR, "store_path", lambda: env["tmp"] / "duty_results.json")
    p = _dies(env) / DIE / "die.yaml"
    d = yaml.safe_load(p.read_text())
    d["geometry"].update(OLD, motor_length=40.0, num_wires_per_slot=14)
    p.write_text(yaml.safe_dump(d, sort_keys=False))
    for cfg, stamped in ((OLDCFG, True), (NEWCFG, False)):
        cp = _dies(env) / DIE / f"{cfg}.yaml"
        c = yaml.safe_load(cp.read_text())
        duty = {"name": "rated", "rpm": 1000, "result": {"efficiency_pct": 95.0, "mass_kg": 1.0}, "torque_nm": 2.0, "power_kw": 0.2}
        if stamped:
            duty["geometry"] = dict(d["geometry"])
        c["duties"] = [duty]
        c["card_date"] = "2026-11-20"
        cp.write_text(yaml.safe_dump(c, sort_keys=False))
    (env["tmp"] / "duty_results.json").write_text(json.dumps({"results": {DIE: {
        OLDCFG: {"rated": {"thermal": {"computed_at": "t", "coil_c": 90.0}, "coupled": {"coil_temp_c": 91.0}}},
        NEWCFG: {"rated": {"thermal": {"computed_at": "t", "coil_c": 80.0}}}}}}))
    return env


def _plan(env, sheet=NEW, who="admin"):
    return client.post(f"{URL}/plan", headers=env[who], json={"sheet": sheet})


def _tree_bytes(env, who="admin") -> bytes:
    from motor_ai_sim.routes import family as F
    F._TREE_CACHE.clear()
    return client.get("/api/family/tree", headers=env[who]).content


def test_the_dry_run_lists_every_key_configuration_and_duty_and_changes_nothing(die):
    before = {p.name: p.read_bytes() for p in (_dies(die) / DIE).glob("*.yaml")}
    r = _plan(die)
    assert r.status_code == 200, r.text
    p = r.json()
    assert [c["key"] for c in p["changes"]] == list(NEW) and p["refusals"] == []
    assert {c["config"]: (c["duties"], c["stored_results"]) for c in p["configurations"]} == {
        NEWCFG: (["rated"], 1), OLDCFG: (["rated"], 2)}
    assert f"tooth_width: 1.0 -> {NEW['tooth_width']}" in p["text"] and "Can be written." in p["text"]
    assert {p.name: p.read_bytes() for p in (_dies(die) / DIE).glob("*.yaml")} == before


@pytest.mark.parametrize("sheet, words", [({"motor_length": 170}, "not a sheet key"), ({"num_wires_per_slot": 10}, "not a sheet key"),
                                          ({"tooth_width": -1}, "finite number"), ({}, "give the sheet")])
def test_only_the_twelve_sheet_keys_are_accepted(die, sheet, words):
    r = _plan(die, sheet)
    assert r.status_code == 422 and words in r.text


def test_the_write_needs_an_admin_and_the_die_name(die):
    assert client.post(f"{URL}/apply", headers=die["a"], json={"sheet": NEW, "confirm": DIE}).status_code == 403
    assert client.post(f"{URL}/apply", headers=die["admin"], json={"sheet": NEW}).status_code == 422
    assert _plan(die, who="a").status_code == 403


def test_the_write_changes_the_sheet_only_backs_up_and_marks_every_result(die):
    r = client.post(f"{URL}/apply", headers=die["admin"], json={"sheet": NEW, "confirm": DIE})
    assert r.status_code == 200, r.text
    out = r.json()
    d = yaml.safe_load((_dies(die) / DIE / "die.yaml").read_text())
    assert {k: d["geometry"][k] for k in NEW} == NEW
    assert d["geometry"]["motor_length"] == 40.0 and d["geometry"]["num_wires_per_slot"] == 14   # config-level keys untouched
    assert d["sheet_history"][-1]["previous"] == OLD and d["sheet_history"][-1]["by"] == ADMIN
    backup = Path(out["backup"])
    assert sorted(p.name for p in backup.iterdir()) == sorted(["die.yaml", f"{OLDCFG}.yaml", f"{NEWCFG}.yaml", "duty_results.json"])
    assert yaml.safe_load((backup / "die.yaml").read_text())["geometry"]["tooth_width"] == OLD["tooth_width"]
    for cfg in (OLDCFG, NEWCFG):                                        # the unstamped duty is marked too
        duty = yaml.safe_load((_dies(die) / DIE / f"{cfg}.yaml").read_text())["duties"][0]
        assert duty["sheet_stale"]["previous_sheet"] == out["previous_hash"]
    rows = json.loads((die["tmp"] / "duty_results.json").read_text())["results"][DIE]
    assert all(row["sheet_stale"]["new_sheet"] == out["new_hash"] for duties in rows.values() for kinds in duties.values()
               for row in kinds.values())
    assert out["marked_rows"] == 3 and out["marked_duties"] == {OLDCFG: 1, NEWCFG: 1}
    audit = [json.loads(line) for line in (die["tmp"] / "audit.jsonl").read_text().splitlines()]
    assert any(a["action"] == "die.sheet_replace" for a in audit)
    again = _plan(die)
    assert "the die already has this sheet" in again.json()["refusals"]


def test_a_running_job_or_a_frozen_revision_refuses_the_whole_write(die, monkeypatch):
    from motor_ai_sim import config_archive as CA
    from motor_ai_sim import motor_revisions as MR
    before = {p.name: p.read_bytes() for p in (_dies(die) / DIE).glob("*.yaml")}
    monkeypatch.setattr(CA, "_live_jobs_strict", lambda d: [{"die": DIE, "config": OLDCFG, "where": "us: job-1"}])
    r = client.post(f"{URL}/apply", headers=die["admin"], json={"sheet": NEW, "confirm": DIE})
    assert r.status_code == 409 and "job is running" in r.text
    monkeypatch.setattr(CA, "_live_jobs_strict", lambda d: [])
    real = MR.guard_config_write

    def frozen(d, c):
        if c == NEWCFG:
            raise RuntimeError("frozen")
        return real(d, c)
    monkeypatch.setattr(MR, "guard_config_write", frozen)
    r = client.post(f"{URL}/apply", headers=die["admin"], json={"sheet": NEW, "confirm": DIE})
    assert r.status_code == 409 and "frozen product revision" in r.text
    assert {p.name: p.read_bytes() for p in (_dies(die) / DIE).glob("*.yaml")} == before


def test_outward_readers_drop_marked_results_and_serve_unmarked_ones_exactly_as_before(die, monkeypatch):
    from motor_ai_sim import mcp_tools as M
    from motor_ai_sim import public_motors as PM
    from motor_ai_sim import support_catalog as SC
    from motor_ai_sim.routes import family as F
    real_tree = F.tree

    def carded(*a, **k):                    # the catalogue cards store is not part of this fixture: every config has a card
        t = real_tree(*a, **k)
        for d in t.get("dies") or []:
            for c in d.get("configs") or []:
                c["card_date"] = "2026-11-20"
        return t
    monkeypatch.setattr(F, "tree", carded)
    tree_before = _tree_bytes(die)
    mcp_before = {c["name"]: c for d in M._visible_tree() if d["name"] == DIE for c in d["configs"]}
    # mark ONE configuration only (as apply does for each), the other stays unmarked
    cp = _dies(die) / DIE / f"{OLDCFG}.yaml"
    c = yaml.safe_load(cp.read_text())
    c["duties"][0]["sheet_stale"] = {"previous_sheet": "x", "new_sheet": "y", "at": "t"}
    cp.write_text(yaml.safe_dump(c, sort_keys=False))
    F._TREE_CACHE.clear()
    tree = json.loads(_tree_bytes(die))
    old = json.loads(tree_before)
    by = lambda t, cfg: next(c for d in t["dies"] if d["name"] == DIE for c in d["configs"] if c["name"] == cfg)  # noqa: E731
    assert json.dumps(by(tree, NEWCFG), sort_keys=True) == json.dumps(by(old, NEWCFG), sort_keys=True)   # unmarked: identical
    assert by(tree, OLDCFG)["duties"][0]["sheet_stale"]["previous_sheet"] == "x"                      # staff still see it, marked
    assert "sheet_stale" not in json.dumps(by(tree, NEWCFG))
    mcp = {c["name"]: c for d in M._visible_tree() if d["name"] == DIE for c in d["configs"]}
    assert mcp[OLDCFG]["duties"] == [] and len(mcp[NEWCFG]["duties"]) == 1                            # MCP: never as current
    assert json.dumps(mcp[NEWCFG], sort_keys=True) == json.dumps(mcp_before[NEWCFG], sort_keys=True)    # unmarked: identical
    assert SC._pick_rated(by(tree, OLDCFG)) is None and SC._pick_rated(by(tree, NEWCFG))["name"] == "rated"
    die_row = next(d for d in tree["dies"] if d["name"] == DIE)
    assert PM.build_row(die_row, by(tree, OLDCFG)) is None                                             # public card: left out
    assert PM.build_row(die_row, by(tree, NEWCFG)) == PM.build_row(next(d for d in old["dies"] if d["name"] == DIE), by(old, NEWCFG))


def _stamp(env, cfg, geometry):
    cp = _dies(env) / DIE / f"{cfg}.yaml"
    c = yaml.safe_load(cp.read_text())
    c["duties"][0]["geometry"] = dict(geometry)
    c["duties"][0].pop("solved_on", None)
    cp.write_text(yaml.safe_dump(c, sort_keys=False))


def _marked(env, cfg) -> bool:
    from motor_ai_sim.routes import family as F                     # read where the app reads (and refresh_mark writes)
    return "sheet_stale" in F._load_yaml(F._cfg_file(DIE, cfg), "configuration")["duties"][0]


def test_only_a_save_on_the_new_sheet_clears_the_mark_of_a_duty(die):
    assert client.post(f"{URL}/apply", headers=die["admin"], json={"sheet": NEW, "confirm": DIE}).status_code == 200
    geo = yaml.safe_load((_dies(die) / DIE / "die.yaml").read_text())["geometry"]
    assert DS.refresh_mark(DIE, NEWCFG, "rated") is False and _marked(die, NEWCFG)                      # unstamped: stays
    _stamp(die, OLDCFG, dict(geo, **OLD))
    assert DS.refresh_mark(DIE, OLDCFG, "rated") is False and _marked(die, OLDCFG)                      # old sheet: stays
    _stamp(die, OLDCFG, {k: v for k, v in geo.items() if k != "rotor_hole"})
    assert DS.refresh_mark(DIE, OLDCFG, "rated") is False and _marked(die, OLDCFG)                      # silent on a key: stays
    _stamp(die, OLDCFG, geo)
    assert DS.refresh_mark(DIE, OLDCFG, "rated") is True and not _marked(die, OLDCFG)                   # new sheet: cleared


def test_the_duty_result_save_refreshes_the_mark_and_a_failed_save_does_not(die, monkeypatch):
    from motor_ai_sim.routes import family as F
    calls = []
    monkeypatch.setattr(DS, "refresh_mark", lambda *a: calls.append(a) or False)
    body = {"die": DIE, "config": OLDCFG, "duty": "rated", "result": {"efficiency_pct": 95.1}}
    r = client.post("/api/family/duty_result", headers=die["admin"], json=body)
    assert r.status_code == 200, r.text
    assert calls == [(DIE, OLDCFG, "rated")]
    monkeypatch.setattr(F, "_save_yaml", lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError):
        client.post("/api/family/duty_result", headers=die["admin"], json=body)
    assert calls == [(DIE, OLDCFG, "rated")]                                                            # not called again


def test_a_stored_row_keeps_its_mark_unless_the_result_was_computed_on_the_new_sheet(die, monkeypatch):
    assert client.post(f"{URL}/apply", headers=die["admin"], json={"sheet": NEW, "confirm": DIE}).status_code == 200
    geo = yaml.safe_load((_dies(die) / DIE / "die.yaml").read_text())["geometry"]
    import motor_ai_sim.config as C
    monkeypatch.setattr(C, "get_config", lambda: {"geometry": dict(geo, **OLD)})                       # live on the OLD sheet
    assert DR.record(DIE, OLDCFG, "rated", "thermal", {"computed_at": "t2", "coil_c": 92.0})
    row = json.loads((die["tmp"] / "duty_results.json").read_text())["results"][DIE][OLDCFG]["rated"]["thermal"]
    assert row["coil_c"] == 92.0 and "sheet_stale" in row and not DR.is_current_row(row)
    monkeypatch.setattr(C, "get_config", lambda: {"geometry": dict(geo)})                               # live on the NEW sheet
    assert DR.record(DIE, OLDCFG, "rated", "thermal", {"computed_at": "t3", "coil_c": 93.0})
    row = json.loads((die["tmp"] / "duty_results.json").read_text())["results"][DIE][OLDCFG]["rated"]["thermal"]
    assert row["coil_c"] == 93.0 and "sheet_stale" not in row and DR.is_current_row(row)


def test_an_unmarked_store_row_is_written_exactly_as_before(die, monkeypatch):
    import motor_ai_sim.config as C
    monkeypatch.setattr(C, "get_config", lambda: {"geometry": {}})
    assert DR.record(DIE, NEWCFG, "rated", "thermal", {"computed_at": "t9", "coil_c": 70.0})
    row = json.loads((die["tmp"] / "duty_results.json").read_text())["results"][DIE][NEWCFG]["rated"]["thermal"]
    assert "sheet_stale" not in row and row["coil_c"] == 70.0
