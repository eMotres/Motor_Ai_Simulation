"""A new sheet = a SUCCESSOR die (owner 2026-11-23; ``motor_ai_sim.successor_die`` + ``/api/die-successor``).  Synthetic dies in
temporary layers: the whitelist copy (every key classified), the plan the owner reads, the switch (names, definition copies
without results, links, the working copy moved aside, snapshots stamped, the predecessor retired), the refusals, the freeze and
the die-identity guard on saves, and the restore round trip leaving the stores as they were."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from motor_ai_sim import successor_die as SD
from test_config_archive import A, ADMIN, DIE, NEWCFG, OLDCFG, client, env  # noqa: F401 - the shared fixture

SUCC = "SUCC 12_40"
RETIRED = "NEWDIE 12_40 sheet 1"
#: every top-level / duty key found in the live shared catalogue on 2026-11-23 (84 configurations; key NAMES only)
LIVE_TOP = {"applications", "battery", "bearings", "cad_mass", "cad_variant", "cogging", "coil_overrides", "controller",
            "cooling_note", "copied_from", "created", "default_duty", "die", "duties", "end_winding_factor", "geometry_overrides",
            "limits", "locked", "materials", "name", "note", "parts", "role", "terminals", "thermal_defaults", "visibility",
            "winding"}
LIVE_DUTY = {"current_arms", "duty_class", "duty_cycle", "gamma_deg", "kind", "materials", "mesh", "mode", "name", "note",
             "power_kw", "result", "rpm", "runs", "saved_at", "solved_on", "star_delta", "summary", "torque_nm"}


def _dies(env) -> Path:
    return env["srv"] / "shared" / "dies"


def _cfg(die, name, *, result=True, extra=None):
    duty = {"name": "rated", "mode": "motor", "rpm": 1000, "current_arms": 5.0, "gamma_deg": 0, "torque_nm": 1.2,
            "power_kw": 0.1, "note": "", "mesh": {"mesh_size_mm": 1.0}}
    if result:
        duty.update(result={"efficiency_pct": 91.0}, summary={"T_avg_Nm": 1.2}, runs={"current": {"result": {"x": 1}}},
                    saved_at="2026-11-01T00:00:00", solved_on={"_geoSig": "a:1"})
    doc = {"name": name, "die": die, "role": "motor", "geometry_overrides": {"motor_length": 10.0},
           "winding": {"connection": "star"}, "materials": {"magnet": "N42SH"}, "duties": [duty], "cogging": {"pkpk": 0.1},
           "created": "2026-11-01T00:00:00"}
    doc.update(extra or {})
    return doc


@pytest.fixture()
def pair(env, monkeypatch):
    """The predecessor DIE (old sheet; OLDCFG continues, NEWCFG stays) and the successor SUCC (new sheet; its own OLDCFG is a
    working copy with sweep results); a project links DIE/OLDCFG; a saved snapshot names it."""
    from motor_ai_sim import config_archive as CA
    from motor_ai_sim.development import store
    monkeypatch.setattr(CA, "_live_jobs_strict", lambda d: [])
    for cfg in (OLDCFG, NEWCFG):
        (_dies(env) / DIE / f"{cfg}.yaml").write_text(yaml.safe_dump(_cfg(DIE, cfg), sort_keys=False))
    d = yaml.safe_load((_dies(env) / DIE / "die.yaml").read_text())
    (_dies(env) / SUCC).mkdir()
    s = dict(d, name=SUCC, geometry=dict(d["geometry"], tooth_width=1.1, magnet_height=1.2))
    (_dies(env) / SUCC / "die.yaml").write_text(yaml.safe_dump(s, sort_keys=False))
    working = _cfg(SUCC, OLDCFG, extra={"winding": {"connection": "delta"}})
    (_dies(env) / SUCC / f"{OLDCFG}.yaml").write_text(yaml.safe_dump(working, sort_keys=False))
    store.create_project({"id": "p1", "title": "Pump project", "customer_org": "Org", "status": "spec", "created_at": "t",
                          "updated_at": "t", "owner_engineer": ADMIN, "members": [{"email": A, "role": "owner"}],
                          "linked_motors": [{"die": DIE, "config": OLDCFG, "status": "current"}], "notes": "",
                          "pending_invites": []})
    ws = env["srv"] / "workspaces" / env["ids"][A]
    ws.mkdir(parents=True, exist_ok=True)
    from motor_ai_sim import json_store                        # written as the app writes it
    json_store.atomic_write_json(ws / "saved_simulations.json", {"u": [
        {"id": "s1", "name": "old run", "params": {}, "results": {"T": 1.2}, "source": {"die": DIE, "config": OLDCFG}}]})
    return env


def _plan(env, **kw):
    body = {"successor": SUCC, "retired": RETIRED, "stays": [NEWCFG], **kw}
    return client.post(f"/api/die-successor/{DIE}/plan", headers=env["admin"], json=body)


def _apply(env, peer=None, **kw):
    p = _plan(env, **kw).json()
    body = {"successor": SUCC, "retired": RETIRED, "stays": [NEWCFG], "confirm": DIE,
            "peer_content_hash": p.get("content_hash") if peer is None else peer, **kw}
    return client.post(f"/api/die-successor/{DIE}/apply", headers=env["admin"], json=body)


def test_every_key_of_the_live_catalogue_is_classified():
    assert LIVE_TOP <= SD.COPY_KEYS | SD.NEVER_KEYS, sorted(LIVE_TOP - SD.COPY_KEYS - SD.NEVER_KEYS)
    assert LIVE_DUTY <= SD.DUTY_COPY_KEYS | SD.DUTY_NEVER_KEYS, sorted(LIVE_DUTY - SD.DUTY_COPY_KEYS - SD.DUTY_NEVER_KEYS)
    assert not (SD.COPY_KEYS & SD.NEVER_KEYS) and not (SD.DUTY_COPY_KEYS & SD.DUTY_NEVER_KEYS)


def test_keys_written_by_the_app_routes_are_classified(pair):
    h = pair["admin"]
    client.patch(f"/api/family/config/{DIE}/{OLDCFG}/battery", headers=h, json={"cells_series": 12})
    client.patch(f"/api/family/config/{DIE}/{OLDCFG}/default_duty", headers=h, json={"duty": "rated"})
    client.put(f"/api/family/config/{DIE}/{OLDCFG}/applications", headers=h, json={"applications": []})
    doc = yaml.safe_load((_dies(pair) / DIE / f"{OLDCFG}.yaml").read_text())
    assert SD.unknown_keys(doc) == []


def test_the_definition_copy_carries_no_result():
    doc = _cfg(DIE, OLDCFG)
    out = SD.definition_copy(doc, SUCC, RETIRED)
    assert "cogging" not in out and out["die"] == SUCC and out["copied_from"]["die"] == RETIRED
    duty = out["duties"][0]
    assert not ({"result", "summary", "runs", "saved_at", "solved_on"} & set(duty))
    assert duty["rpm"] == 1000 and duty["mesh"] == {"mesh_size_mm": 1.0}
    with pytest.raises(SD.SwitchRefused):
        SD.definition_copy({**doc, "new_result_field": 1}, SUCC, RETIRED)


def test_the_plan_is_what_the_owner_reads_and_changes_nothing(pair):
    before = _tree(pair)
    r = _plan(pair)
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["refusals"] == [] and [c["config"] for c in p["continuing"]] == [OLDCFG]
    assert p["behind"] == [{"config": NEWCFG, "why": "stays with the retired die"}]
    row = p["continuing"][0]
    assert row["projects"] == ["Pump project"] and row["successor_own_moved_aside"].startswith(f"{OLDCFG} sweep ")
    assert row["working_copy_differs_in"] == ["winding"]
    t = p["text"]
    assert f"'{DIE}' becomes '{RETIRED}'" in t and "tooth_width" in t and "magnet_height" in t and "Can be switched." in t
    assert "differs in: winding" in t and "Saved Compare snapshots" in t and "Content hash" in t
    assert _tree(pair) == before


def test_refusals_unknown_key_running_job_taken_name_other_host(pair, monkeypatch):
    from motor_ai_sim import config_archive as CA
    doc = yaml.safe_load((_dies(pair) / DIE / f"{OLDCFG}.yaml").read_text())
    (_dies(pair) / DIE / f"{OLDCFG}.yaml").write_text(yaml.safe_dump({**doc, "mystery": 1}))
    assert any("neither list" in x for x in _plan(pair).json()["refusals"])
    (_dies(pair) / DIE / f"{OLDCFG}.yaml").write_text(yaml.safe_dump(doc))
    monkeypatch.setattr(CA, "_live_jobs_strict", lambda d: [{"die": d, "where": "us: j1"}] if d == SUCC else [])
    assert any("job is running or queued" in x for x in _plan(pair).json()["refusals"])
    monkeypatch.setattr(CA, "_live_jobs_strict", lambda d: [])
    r = _apply(pair, peer="0" * 20)
    assert r.status_code == 409 and "other host" in r.text and (_dies(pair) / DIE / "die.yaml").is_file()


def test_the_switch(pair):
    r = _apply(pair)
    assert r.status_code == 200, r.text
    out = r.json()
    dies = _dies(pair)
    new = yaml.safe_load((dies / DIE / "die.yaml").read_text())
    assert new["geometry"]["tooth_width"] == 1.1                                   # the name now means the successor
    assert yaml.safe_load((dies / RETIRED / "die.yaml").read_text())["geometry"].get("tooth_width") != 1.1
    copy_doc = yaml.safe_load((dies / DIE / f"{OLDCFG}.yaml").read_text())
    assert copy_doc["copied_from"]["die"] == RETIRED and "result" not in copy_doc["duties"][0] and "cogging" not in copy_doc
    assert copy_doc["winding"] == {"connection": "star"}                           # the catalogue definition, not the working copy
    aside = out["aside"][OLDCFG]
    assert (dies / DIE / f"{aside}.yaml").is_file()                                # the working copy kept, with its results
    from motor_ai_sim import config_archive as CA
    from motor_ai_sim import workspace as WS
    from motor_ai_sim.development import store
    assert CA.state(DIE, aside) == CA.ARCHIVED and WS.is_tombstoned(RETIRED)
    assert store.get_project("p1")["linked_motors"][0] == {"die": DIE, "config": OLDCFG, "status": "current"}
    assert (dies / RETIRED / f"{NEWCFG}.yaml").is_file() and not (dies / DIE / f"{NEWCFG}.yaml").exists()   # stays behind
    snap = json.loads((pair["srv"] / "workspaces" / pair["ids"][A] / "saved_simulations.json").read_text())["u"][0]
    assert snap["source"]["die"] == RETIRED and snap["previous_sheet"]["die"] == RETIRED and snap["results"] == {"T": 1.2}
    assert not (pair["srv"] / "shared" / SD.INTENT_DIR / f"{DIE}.json").exists()


def test_result_saves_during_the_switch_and_for_another_record_are_refused(pair, monkeypatch):
    from motor_ai_sim import duty_results as DR
    (pair["srv"] / "shared" / SD.INTENT_DIR).mkdir(parents=True, exist_ok=True)
    (pair["srv"] / "shared" / SD.INTENT_DIR / f"{DIE}.json").write_text(json.dumps({"pred": DIE, "succ": SUCC, "retired": RETIRED}))
    body = {"die": DIE, "config": OLDCFG, "duty": "rated", "result": {"efficiency_pct": 95.0}}
    for die in (DIE, SUCC):
        r = client.post("/api/family/duty_result", headers=pair["admin"], json={**body, "die": die, "config": OLDCFG})
        assert r.status_code == 409 and "being switched" in r.text
    monkeypatch.setattr(DR, "store_path", lambda: pair["tmp"] / "duty_results.json")
    assert DR.record(DIE, OLDCFG, "rated", "thermal", {"coil_c": 1.0}) is False
    (pair["srv"] / "shared" / SD.INTENT_DIR / f"{DIE}.json").unlink()
    monkeypatch.setattr(SD, "die_id", lambda d: "id-successor")
    r = client.post("/api/family/duty_result", headers=pair["admin"], json={**body, "die_id": "id-predecessor"})
    assert r.status_code == 409 and "another die record" in r.text
    assert client.post("/api/family/duty_result", headers=pair["admin"], json={**body, "die_id": "id-successor"}).status_code == 200
    assert client.post("/api/family/duty_result", headers=pair["admin"], json=body).status_code == 200   # old clients: as today


def _tree(env) -> dict:
    """Every file of the shared catalogue, the projects and the workspaces, minus the audit / history / backup places."""
    out = {}
    skip = (".history", ".config_moves", ".die_renames", SD.INTENT_DIR, "audit")
    for base in (env["srv"] / "shared", env["tmp"] / "projects", env["srv"] / "workspaces"):
        for p in sorted(base.rglob("*")) if base.exists() else []:
            rel = str(p.relative_to(env["tmp"]))
            if p.is_file() and not any(s in rel for s in skip) and not p.name.endswith((".lock", ".jsonl")) \
                    and p.name != "motor_config.yaml":          # a workspace's live config, created by any request
                out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def test_the_restore_round_trip_leaves_the_stores_as_they_were(pair):
    before = _tree(pair)
    out = _apply(pair).json()
    rp = client.post(f"/api/die-successor/{DIE}/restore/plan", headers=pair["admin"],
                     json={"successor_name": SUCC, "retired": RETIRED, "aside": out["aside"]})
    assert rp.status_code == 200 and rp.json()["refusals"] == [], rp.text
    r = client.post(f"/api/die-successor/{DIE}/restore", headers=pair["admin"],
                    json={"successor_name": SUCC, "retired": RETIRED, "aside": out["aside"], "confirm": DIE})
    assert r.status_code == 200, r.text
    after = _tree(pair)
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    assert changed == [], changed
