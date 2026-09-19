"""A duty remembers one run PER EXCITATION, and the sine run stays primary.

Until 2026-09-02 a duty held exactly ONE snapshot of the last matching run.
Saving after a PWM solve overwrote the sine-current snapshot, and re-loading the
duty then restored ``sim.drive = pwm_voltage`` — so every later Run became a
twelve-minute PWM solve, and the sine result the catalog shows was gone.

What is under test:

* saving a PWM run leaves the PRIMARY (top-level mesh / summary / result)
  untouched and records ``runs.pwm_voltage`` with a gzip sidecar on disk;
* saving a sine run updates BOTH the primary and ``runs.current``;
* a duty that has never had a primary adopts whatever it is first given, so a
  PWM-only duty is not left blank;
* ``GET /duty_run/...`` round-trips the payload with the heavy per-element
  arrays stripped, and 404s on an excitation this duty never ran;
* access control is the duty payload's: an ungranted account gets 404;
* duplicate / rename / delete of a duty (and of a configuration) carry — or
  remove — the sidecar files;
* a duty's DUTY CYCLE (S1/S2/S3/segments) is stored on the duty, survives a
  re-save that does not mention it, is refused by name when it cannot mean what
  it says, and follows a rename of the duties it points at;
* a Cyrillic duty name gets a safe, stable file name;
* the tree describes the stored runs without shipping a payload;
* an oversized payload is refused with 413, not silently written.

Everything runs against a COPY of config/dies in the pytest tmp area; the
module asserts the real catalog was never touched.
"""
from __future__ import annotations

import gzip
import json
import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from motor_ai_sim.api import app

_ROOT = Path(__file__).resolve().parents[1]
_REAL_DIES = _ROOT / "config" / "dies"
_REAL_USERS = _ROOT / "config" / "users.json"

DIE = "TESTDIE 40"
CFG = "L40"
DUTY = "peak"

ADMIN = "admin@example.com"
CLIENT = "client@example.com"

client = TestClient(app)


# ── isolation ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module", autouse=True)
def _real_catalog_untouched():
    before = {p: p.stat().st_mtime_ns
              for p in sorted(_REAL_DIES.rglob("*")) if p.is_file()}
    yield
    after = {p: p.stat().st_mtime_ns
             for p in sorted(_REAL_DIES.rglob("*")) if p.is_file()}
    assert before == after, "config/dies was modified by a test"


@pytest.fixture()
def dies(tmp_path, monkeypatch):
    """A throwaway catalog holding ONE die with one configuration and one
    duty — small enough that every assertion below is about this feature and
    not about whatever the real catalog happens to contain."""
    from motor_ai_sim.routes import family as fam

    root = tmp_path / "dies"
    (root / DIE).mkdir(parents=True)
    (root / DIE / "die.yaml").write_text(yaml.safe_dump({
        "name": DIE, "locked": False, "created": "2026-09-02T10:00:00",
        "geometry": {"num_slots": 12, "num_poles": 14, "stator_diameter": 40.0,
                     "magnet_height": 3.0, "motor_length": 40.0},
    }, sort_keys=False, allow_unicode=True), encoding="utf-8")
    (root / DIE / f"{CFG}.yaml").write_text(yaml.safe_dump({
        "name": CFG, "die": DIE, "role": "motor",
        "geometry_overrides": {"motor_length": 40.0, "wire_height": 1.0},
        "winding": {"connection": "star"},
        "materials": {"magnet": "N42SH", "stator_core": "20SW1200"},
        "duties": [{"name": DUTY, "mode": "motor",
                    "saved_at": "2026-09-02T10:00:00",
                    "current_arms": 85.0, "rpm": 6000.0, "gamma_deg": 12.0,
                    "note": ""}],
    }, sort_keys=False, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(fam, "_DIES_DIR", root)
    return root


@pytest.fixture()
def granted(tmp_path, monkeypatch, dies):
    """The same catalog with a real admin and a real (ungranted) client."""
    from motor_ai_sim import auth
    from motor_ai_sim import users as U

    users_file = tmp_path / "users.json"
    shutil.copy2(_REAL_USERS, users_file)
    monkeypatch.setattr(U, "_USERS_FILE", users_file)
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.delenv("CATALOG_GRANT_ALL_REGISTERED", raising=False)
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {ADMIN})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", False)
    U.create_user(ADMIN, "password-admin", tier="admin", name="Admin")
    U.create_user(CLIENT, "password-client", tier="free", name="Client")
    return {"admin": {"Authorization": f"Bearer {U.issue_token(ADMIN)}"},
            "client": {"Authorization": f"Bearer {U.issue_token(CLIENT)}"}}


# ── helpers ──────────────────────────────────────────────────────────────────

def _cfg_doc(dies, cfg=CFG):
    return yaml.safe_load((dies / DIE / f"{cfg}.yaml").read_text(encoding="utf-8"))


def _duty_doc(dies, duty=DUTY, cfg=CFG):
    return next(d for d in _cfg_doc(dies, cfg)["duties"] if d["name"] == duty)


def _settings(drive: str, **extra) -> dict:
    s = {"sim.drive": drive, "sim.current": 85.0, "sim.rpm": 6000.0,
         "sim.gamma": 12.0, "sim.stepsPP": 48, "mesh.maxSize": 2.0}
    s.update(extra)
    return s


def _summary(drive: str, **extra) -> dict:
    s = {"drive": drive, "rpm": 6000.0, "I_phase_rms_A": 85.0,
         "gamma_deg": 12.0, "T_em_avg_Nm": 4.2, "T_ripple_pct": 3.1}
    s.update(extra)
    return s


def _payload(drive: str, n=8) -> dict:
    """A transient shaped like the real one — per-step series the charts read,
    plus the heavy per-element blocks they never do."""
    return {
        "drive": drive, "n_steps": n, "n_steps_per_period": n,
        "time_s": [i * 1e-4 for i in range(n)],
        "T_em_Nm": [4.0 + 0.1 * i for i in range(n)],
        "I_A": [0.0] * n, "V_A": [0.0] * n,
        "P_loss_total_W": [10.0] * n,
        "summary": _summary(drive),
        "pwm": {"v_bus_V": 750.0, "f_switch_eff_Hz": 24000.0},
        "excitation": {"kind": drive, "series": "V", "quantity": "V_phase"},
        "computed_at": "2026-09-02T11:00:00",
        # …and the heavy ones, which must NOT come back
        "frames": [{"mesh": [0.0] * 200} for _ in range(3)],
        "field": {"A_z": [0.0] * 500},
        "demag_field": {"demag_coef_per_tri": [1.0] * 500},
        "demag_coef_per_tri": [1.0] * 500,
    }


def _save_duty(drive: str, duty=DUTY, cfg=CFG, headers=None, **kw):
    body = {"name": duty, "mode": "motor", "from_current": False,
            "current_arms": 85.0, "rpm": 6000.0, "gamma_deg": 12.0,
            "drive": drive, "mesh": _settings(drive),
            "summary": _summary(drive)}
    body.update(kw)
    return client.post("/api/family/duty", headers=headers,
                       json={"die": DIE, "config": cfg, "duty": body})


def _save_result(drive: str, eff: float, duty=DUTY, cfg=CFG, headers=None):
    return client.post("/api/family/duty_result", headers=headers, json={
        "die": DIE, "config": cfg, "duty": duty, "drive": drive,
        "result": {"efficiency_pct": eff, "ripple_pct": 3.1, "mass_kg": 1.0}})


def _save_run(drive: str, duty=DUTY, cfg=CFG, payload=None, sig=None,
              headers=None):
    return client.post("/api/family/duty_run", headers=headers, json={
        "die": DIE, "config": cfg, "duty": duty, "drive": drive,
        "settings": _settings(drive), "summary": _summary(drive),
        "assignment_sig": sig,
        "payload": _payload(drive) if payload is None else payload})


def _save_everything(drive: str, eff: float, duty=DUTY, cfg=CFG, sig=None,
                     headers=None):
    """The three calls the Save button makes, in the order it makes them."""
    for r in (_save_duty(drive, duty, cfg, headers=headers),
              _save_result(drive, eff, duty, cfg, headers=headers),
              _save_run(drive, duty, cfg, sig=sig, headers=headers)):
        assert r.status_code == 200, r.text
    return r


# ── the primary / runs split ─────────────────────────────────────────────────

def test_sine_run_is_the_primary_and_also_a_stored_run(dies):
    _save_everything("current", 95.5)
    d = _duty_doc(dies)
    assert d["mesh"]["sim.drive"] == "current"
    assert d["summary"]["drive"] == "current"
    assert d["result"]["efficiency_pct"] == 95.5
    assert set(d["runs"]) == {"current"}
    assert d["runs"]["current"]["result"]["efficiency_pct"] == 95.5
    assert (dies / DIE / d["runs"]["current"]["payload_file"]).is_file()


def test_pwm_run_leaves_the_primary_sine_result_alone(dies):
    _save_everything("current", 95.5)
    before = _duty_doc(dies)
    _save_everything("pwm_voltage", 91.0)
    d = _duty_doc(dies)
    # the primary is byte-identical to the sine save
    assert d["mesh"] == before["mesh"]
    assert d["summary"] == before["summary"]
    assert d["result"] == before["result"]
    assert d["result"]["efficiency_pct"] == 95.5
    # …and the PWM run is remembered beside it
    assert set(d["runs"]) == {"current", "pwm_voltage"}
    pwm = d["runs"]["pwm_voltage"]
    assert pwm["settings"]["sim.drive"] == "pwm_voltage"
    assert pwm["result"]["efficiency_pct"] == 91.0
    assert pwm["build_sig"] and pwm["recorded_at"]
    assert (dies / DIE / pwm["payload_file"]).is_file()
    # the sine sidecar is a DIFFERENT file — one excitation cannot clobber another
    assert pwm["payload_file"] != d["runs"]["current"]["payload_file"]


def test_a_duty_with_no_primary_adopts_the_first_run_it_gets(dies):
    """A point that has only ever been solved on PWM must not read as blank."""
    _save_everything("pwm_voltage", 91.0)
    d = _duty_doc(dies)
    assert d["mesh"]["sim.drive"] == "pwm_voltage"
    assert d["result"]["efficiency_pct"] == 91.0
    assert set(d["runs"]) == {"pwm_voltage"}
    # …and the sine run that comes later TAKES the primary over
    _save_everything("current", 95.5)
    d = _duty_doc(dies)
    assert d["mesh"]["sim.drive"] == "current"
    assert d["result"]["efficiency_pct"] == 95.5
    assert d["runs"]["pwm_voltage"]["result"]["efficiency_pct"] == 91.0


def test_re_saving_the_operating_point_alone_keeps_every_stored_run(dies):
    _save_everything("current", 95.5)
    _save_everything("pwm_voltage", 91.0)
    r = client.post("/api/family/duty", json={
        "die": DIE, "config": CFG,
        "duty": {"name": DUTY, "mode": "motor", "current_arms": 90.0,
                 "rpm": 6000.0, "gamma_deg": 12.0}})
    assert r.status_code == 200, r.text
    d = _duty_doc(dies)
    assert d["current_arms"] == 90.0
    assert set(d["runs"]) == {"current", "pwm_voltage"}
    assert d["runs"]["pwm_voltage"]["payload_file"]


def test_a_duty_saved_the_old_way_gets_no_runs_block(dies):
    """Backwards compatibility: a save with no run attached must not invent a
    `runs:` key, so files that predate the feature stay as they are."""
    r = client.post("/api/family/duty", json={
        "die": DIE, "config": CFG,
        "duty": {"name": DUTY, "mode": "motor", "current_arms": 85.0,
                 "rpm": 6000.0, "gamma_deg": 12.0}})
    assert r.status_code == 200, r.text
    assert "runs" not in _duty_doc(dies)


# ── loading a stored run back ────────────────────────────────────────────────

def test_duty_run_round_trips_the_payload_without_the_heavy_arrays(dies):
    _save_everything("pwm_voltage", 91.0)
    r = client.get(f"/api/family/duty_run/{DIE}/{CFG}/{DUTY}/pwm_voltage")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["drive"] == "pwm_voltage"
    assert j["settings"]["sim.drive"] == "pwm_voltage"
    assert j["summary"]["drive"] == "pwm_voltage"
    assert j["result"]["efficiency_pct"] == 91.0
    assert j["stale"] is False
    p = j["payload"]
    # every per-step series and the description blocks survive…
    assert len(p["time_s"]) == 8 and len(p["T_em_Nm"]) == 8
    assert p["pwm"]["v_bus_V"] == 750.0
    assert p["excitation"]["kind"] == "pwm_voltage"
    assert p["summary"]["T_ripple_pct"] == 3.1
    assert p["computed_at"] == "2026-09-02T11:00:00"
    # …and the per-element arrays no chart reads are gone
    for k in ("frames", "field", "demag_field", "demag_coef_per_tri"):
        assert k not in p, f"{k} was stored — that is what made a run enormous"


def test_the_sidecar_is_gzip_and_names_its_duty(dies):
    _save_everything("pwm_voltage", 91.0)
    rel = _duty_doc(dies)["runs"]["pwm_voltage"]["payload_file"]
    with gzip.open(dies / DIE / rel, "rb") as f:
        blob = json.loads(f.read().decode("utf-8"))
    assert blob["name"] == DUTY and blob["drive"] == "pwm_voltage"
    assert blob["payload"]["time_s"]


def test_duty_runs_returns_every_stored_run_in_one_call(dies):
    """What ▶ fetches: all of the duty's runs, payloads gunzipped, so the
    panel's selector switches between them with no network and no solve."""
    _save_everything("current", 95.5)
    _save_everything("pwm_voltage", 91.0)
    r = client.get(f"/api/family/duty_runs/{DIE}/{CFG}/{DUTY}")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["primary_drive"] == "current"
    runs = {x["drive"]: x for x in j["runs"]}
    assert set(runs) == {"current", "pwm_voltage"}
    assert runs["current"]["primary"] is True
    assert runs["pwm_voltage"]["primary"] is False
    for x in runs.values():
        assert x["payload"]["time_s"] and x["settings"] and x["summary"]
        assert "frames" not in x["payload"] and "field" not in x["payload"]
    assert runs["pwm_voltage"]["settings"]["sim.drive"] == "pwm_voltage"
    assert runs["pwm_voltage"]["result"]["efficiency_pct"] == 91.0


def test_the_apply_payload_does_not_carry_the_runs_block(dies):
    """▶ fetches the runs separately — the apply payload must not repeat their
    settings and summaries on every load."""
    _save_everything("current", 95.5)
    _save_everything("pwm_voltage", 91.0)
    j = client.get(f"/api/family/payload/{DIE}/{CFG}?duty={DUTY}").json()
    assert "runs" not in j["duty"]
    assert j["duty"]["summary"]["drive"] == "current"     # the primary is there


def test_duty_runs_is_empty_for_a_duty_that_has_none(dies):
    j = client.get(f"/api/family/duty_runs/{DIE}/{CFG}/{DUTY}").json()
    assert j["runs"] == [] and j["primary_drive"] == "current"


def test_duty_runs_is_404_for_an_ungranted_account(dies, granted):
    _save_everything("pwm_voltage", 91.0, headers=granted["admin"])
    r = client.get(f"/api/family/duty_runs/{DIE}/{CFG}/{DUTY}",
                   headers=granted["client"])
    assert r.status_code == 404 and "not found" in r.json()["detail"]


def test_an_excitation_that_was_never_run_is_404(dies):
    _save_everything("current", 95.5)
    r = client.get(f"/api/family/duty_run/{DIE}/{CFG}/{DUTY}/bldc_current")
    assert r.status_code == 404
    assert "no stored 'bldc_current' run" in r.json()["detail"]


def test_an_unknown_excitation_is_refused_and_named(dies):
    _save_everything("current", 95.5)
    r = client.get(f"/api/family/duty_run/{DIE}/{CFG}/{DUTY}/space_vector")
    assert r.status_code == 422
    assert "space_vector" in r.json()["detail"]
    assert "pwm_voltage" in r.json()["detail"]


def test_a_stored_run_flags_itself_stale_after_the_build_moves(dies):
    _save_everything("pwm_voltage", 91.0)
    doc = _cfg_doc(dies)
    doc["geometry_overrides"]["motor_length"] = 45.0     # a different machine
    (dies / DIE / f"{CFG}.yaml").write_text(
        yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
    j = client.get(f"/api/family/duty_run/{DIE}/{CFG}/{DUTY}/pwm_voltage").json()
    assert j["stale"] is True
    row = next(x for x in _tree_duty(DIE, CFG, DUTY)["runs"]
               if x["drive"] == "pwm_voltage")
    assert row["stale"] is True


def test_the_assignment_signature_rides_the_run(dies):
    _save_everything("pwm_voltage", 91.0, sig="magnet=N42SH_120C|stator_core=20SW1200")
    j = client.get(f"/api/family/duty_run/{DIE}/{CFG}/{DUTY}/pwm_voltage").json()
    assert j["assignment_sig"] == "magnet=N42SH_120C|stator_core=20SW1200"


# ── the tree ─────────────────────────────────────────────────────────────────

def _tree_duty(die=DIE, cfg=CFG, duty=DUTY):
    t = client.get("/api/family/tree").json()
    d = next(x for x in t["dies"] if x["name"] == die)
    c = next(x for x in d["configs"] if x["name"] == cfg)
    return next(x for x in c["duties"] if x["name"] == duty)


def test_the_tree_describes_the_runs_and_ships_no_payload(dies):
    _save_everything("current", 95.5)
    _save_everything("pwm_voltage", 91.0, sig="magnet=N42SH")
    d = _tree_duty()
    assert d["primary_drive"] == "current"
    rows = {r["drive"]: r for r in d["runs"]}
    assert set(rows) == {"current", "pwm_voltage"}
    assert rows["current"]["primary"] is True
    assert rows["pwm_voltage"]["primary"] is False
    pwm = rows["pwm_voltage"]
    assert pwm["recorded_at"] and pwm["has_payload"] is True
    assert pwm["ripple_pct"] == 3.1 and pwm["steps"] == 48
    assert pwm["stale"] is False
    assert pwm["assignment_sig"] == "magnet=N42SH"
    assert "payload" not in pwm and "settings" not in pwm


def test_a_duty_with_no_stored_runs_reports_an_empty_list(dies):
    assert _tree_duty()["runs"] == []
    assert _tree_duty()["primary_drive"] == "current"


# ── the duty cycle ───────────────────────────────────────────────────────────
# A robot joint spends two seconds at its peak and a minute at nothing; the
# steady map of the peak answers a question nobody asked.  What the machine DOES
# with the point is part of the duty's definition, so it lives in the yaml with
# it — written on every save like `materials`, kept when a save does not mention
# it, and refused BY NAME when it names a duty this configuration does not have.

_S3 = {"kind": "S3", "ed_pct": 25, "cycle_s": 60.0, "rest_duty": None,
       "t_start_c": 40.0, "n_cycles_max": 200}


def _save_second_duty(name="rated"):
    return client.post("/api/family/duty", json={
        "die": DIE, "config": CFG,
        "duty": {"name": name, "mode": "motor", "current_arms": 40.0,
                 "rpm": 6000.0, "gamma_deg": 12.0}})


def test_the_duty_cycle_is_stored_on_the_duty(dies):
    assert _save_duty("current", duty_cycle=_S3).status_code == 200
    blk = _duty_doc(dies)["duty_cycle"]
    assert blk["kind"] == "S3" and blk["ed_pct"] == 25
    assert blk["cycle_s"] == 60.0 and blk["rest_duty"] is None
    assert blk["t_start_c"] == 40.0
    assert blk["saved_at"] == _duty_doc(dies)["saved_at"]   # one save, one stamp
    # …and it reaches both readers: the catalog row and the apply payload
    assert _tree_duty()["duty_cycle"]["ed_pct"] == 25
    j = client.get(f"/api/family/payload/{DIE}/{CFG}?duty={DUTY}").json()
    assert j["duty"]["duty_cycle"]["kind"] == "S3"


def test_re_saving_the_point_without_a_cycle_keeps_the_stored_one(dies):
    """The `materials` rule: a Save that does not mention the cycle is a Save of
    the operating point, not a deletion of what the duty is FOR."""
    assert _save_duty("current", duty_cycle=_S3).status_code == 200
    r = client.post("/api/family/duty", json={
        "die": DIE, "config": CFG,
        "duty": {"name": DUTY, "mode": "motor", "current_arms": 90.0,
                 "rpm": 6000.0, "gamma_deg": 12.0}})
    assert r.status_code == 200, r.text
    d = _duty_doc(dies)
    assert d["current_arms"] == 90.0
    assert d["duty_cycle"]["ed_pct"] == 25


def test_an_empty_block_clears_the_cycle(dies):
    """The only way back to a plain continuous point — and the one case where
    "sent" does not mean "stored"."""
    assert _save_duty("current", duty_cycle=_S3).status_code == 200
    assert _save_duty("current", duty_cycle={}).status_code == 200
    assert "duty_cycle" not in _duty_doc(dies)


def test_a_pwm_save_still_writes_the_cycle(dies):
    """A cycle is not excitation-specific — the same point on PWM is the same
    cycle — so it is written even when the save is not the primary snapshot."""
    _save_everything("current", 95.5)
    assert _save_duty("pwm_voltage", duty_cycle=_S3).status_code == 200
    d = _duty_doc(dies)
    assert d["summary"]["drive"] == "current"          # primary untouched
    assert d["duty_cycle"]["kind"] == "S3"


def test_a_zero_duty_ratio_is_refused_by_name(dies):
    assert _save_duty("current", duty_cycle=_S3).status_code == 200
    r = _save_duty("current", duty_cycle={"kind": "S3", "ed_pct": 0,
                                          "cycle_s": 60.0})
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert "duty_cycle_bad_ed" in detail
    assert "ED" in detail
    # the refused save wrote NOTHING — the previous cycle is still on the duty
    assert _duty_doc(dies)["duty_cycle"]["ed_pct"] == 25


def test_a_rest_duty_this_configuration_does_not_have_is_refused(dies):
    r = _save_duty("current", duty_cycle={**_S3, "rest_duty": "idle"})
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert "duty_cycle_unknown_duty" in detail
    assert "idle" in detail and DUTY in detail     # what is wrong, what exists
    assert "duty_cycle" not in _duty_doc(dies)
    # …and once that duty exists, the same block saves
    assert _save_second_duty("idle").status_code == 200
    assert _save_duty("current", duty_cycle={**_S3,
                                             "rest_duty": "idle"}).status_code == 200
    assert _duty_doc(dies)["duty_cycle"]["rest_duty"] == "idle"


@pytest.mark.parametrize("block,needle", [
    ({"kind": "S4"}, "duty_cycle_bad_kind"),
    ({"kind": "S2"}, "duty_cycle_bad_s2"),
    ({"kind": "S2", "t_on_s": 0}, "duty_cycle_bad_s2"),
    ({"kind": "S3", "ed_pct": 25, "cycle_s": 0}, "duty_cycle_bad_cycle"),
    ({"kind": "S3", "ed_pct": 120, "cycle_s": 60}, "duty_cycle_bad_ed"),
    ({"kind": "segments", "segments": []}, "duty_cycle_bad_segments"),
    ({"kind": "segments", "segments": [{"duty": None, "t_s": 0}]},
     "duty_cycle_bad_segment"),
    ({"kind": "segments", "segments": [{"duty": "ghost", "t_s": 2}]},
     "duty_cycle_unknown_duty"),
    ({"kind": "S3", "ed_pct": 25, "cycle_s": 60, "n_cycles_max": 0},
     "duty_cycle_bad_n_cycles"),
])
def test_every_malformed_cycle_is_refused_with_its_own_code(dies, block, needle):
    r = _save_duty("current", duty_cycle=block)
    assert r.status_code == 422, r.text
    assert needle in r.json()["detail"]
    assert "duty_cycle" not in _duty_doc(dies)


def test_a_cycle_that_names_an_unrun_duty_is_accepted(dies):
    """The catalog checks NAMES, not physics: a cycle is normally written before
    the points it names have been solved, and refusing to save it then would
    make the editor unusable.  The thermal run refuses that later, by name."""
    assert _save_second_duty("idle").status_code == 200      # no run attached
    r = _save_duty("current", duty_cycle={**_S3, "rest_duty": "idle"})
    assert r.status_code == 200, r.text


def test_a_cycle_follows_a_rename_of_the_duty_it_names(dies):
    assert _save_second_duty("idle").status_code == 200
    assert _save_duty("current", duty_cycle={**_S3,
                                             "rest_duty": "idle"}).status_code == 200
    r = client.patch(f"/api/family/duty/{DIE}/{CFG}/idle", json={"name": "pause"})
    assert r.status_code == 200, r.text
    assert _duty_doc(dies)["duty_cycle"]["rest_duty"] == "pause"
    # …so the duty can still be re-saved, which a dangling name would refuse
    assert _save_duty("current").status_code == 200


def test_duplicating_a_duty_copies_its_cycle(dies):
    assert _save_duty("current", duty_cycle=_S3).status_code == 200
    r = client.post(f"/api/family/duty/{DIE}/{CFG}/{DUTY}/duplicate",
                    json={"name": "peak copy"})
    assert r.status_code == 200, r.text
    assert _duty_doc(dies, "peak copy")["duty_cycle"] == _duty_doc(dies)["duty_cycle"]
    # deleting the original leaves the copy's block alone
    assert client.delete(f"/api/family/duty/{DIE}/{CFG}/{DUTY}").status_code == 200
    assert _duty_doc(dies, "peak copy")["duty_cycle"]["ed_pct"] == 25


def test_storing_a_cycle_does_not_disturb_the_stored_runs(dies):
    """The sidecar store is untouched by a block that has nothing to do with
    it — the same guard the materials save carries."""
    _save_everything("current", 95.5)
    _save_everything("pwm_voltage", 91.0)
    before = {k: v["payload_file"] for k, v in _duty_doc(dies)["runs"].items()}
    assert _save_duty("current", duty_cycle=_S3).status_code == 200
    d = _duty_doc(dies)
    assert {k: v["payload_file"] for k, v in d["runs"].items()} == before
    for rel in before.values():
        assert (dies / DIE / rel).is_file()


# ── access control ───────────────────────────────────────────────────────────

def test_an_ungranted_account_gets_404_on_a_stored_run(dies, granted):
    _save_everything("pwm_voltage", 91.0, headers=granted["admin"])
    url = f"/api/family/duty_run/{DIE}/{CFG}/{DUTY}/pwm_voltage"
    r = client.get(url, headers=granted["client"])
    assert r.status_code == 404
    assert "not found" in r.json()["detail"]
    # once granted, the same URL serves the run
    assert client.put(f"/api/admin/users/{CLIENT}/motors",
                      json={"all": False, "dies": [DIE]},
                      headers=granted["admin"]).status_code == 200
    r = client.get(url, headers=granted["client"])
    assert r.status_code == 200 and r.json()["payload"]["time_s"]


def test_storing_a_run_is_admin_only(dies, granted):
    r = client.post("/api/family/duty_run", json={
        "die": DIE, "config": CFG, "duty": DUTY, "drive": "pwm_voltage",
        "payload": _payload("pwm_voltage")}, headers=granted["client"])
    assert r.status_code in (401, 403)


# ── size guard ───────────────────────────────────────────────────────────────

def test_an_oversized_payload_is_refused_with_413(dies):
    big = _payload("pwm_voltage")
    big["time_s"] = [1.234567890123] * 900_000          # ~13 MB of JSON
    r = _save_run("pwm_voltage", payload=big)
    assert r.status_code == 413, r.status_code
    assert "MB" in r.json()["detail"] and "frames" in r.json()["detail"]
    assert "runs" not in _duty_doc(dies)                # nothing was written


# ── names in any alphabet ────────────────────────────────────────────────────

def test_a_cyrillic_duty_name_gets_a_safe_stable_file_name(dies):
    from motor_ai_sim.routes import family as fam
    name = "пик 30С"
    r = client.post("/api/family/duty", json={
        "die": DIE, "config": CFG,
        "duty": {"name": name, "mode": "motor", "current_arms": 85.0,
                 "rpm": 6000.0, "gamma_deg": 12.0}})
    assert r.status_code == 200, r.text
    _save_everything("pwm_voltage", 91.0, duty=name)
    rel = _duty_doc(dies, name)["runs"]["pwm_voltage"]["payload_file"]
    assert rel.isascii(), rel
    assert (dies / DIE / rel).is_file()
    # stable: the same name always maps to the same stem
    assert fam._run_stem(name) == fam._run_stem(name)
    # …and a Cyrillic С does not collide with the Latin C that looks identical
    assert fam._run_stem("пик 30С") != fam._run_stem("пик 30C")
    j = client.get(f"/api/family/duty_run/{DIE}/{CFG}/{name}/pwm_voltage").json()
    assert j["payload"]["time_s"]


def test_a_stray_cyrillic_lookalike_in_an_otherwise_latin_duty_name_is_normalised(dies):
    """The OPPOSITE of the test above: a name typed as plain English that
    picked up one Cyrillic letter pixel-identical to its Latin neighbour
    ('...120С...' — Cyrillic С) is silently corrected to the Latin spelling
    on save — this is exactly how the server workspace's 'rated' duty
    (die 'CIANO28 85 20SW1200' / config 'L13') ended up with its thermal map
    filed under a name the configuration no longer used, 2026-09-19.  A name
    that is mostly ANOTHER alphabet (the test above) is never touched."""
    from motor_ai_sim.routes import family as fam

    # unit-level: the helper itself, both directions
    assert fam._delookalike_duty_name("rated 120С wire 80C NdFeB") \
        == "rated 120C wire 80C NdFeB"
    assert fam._delookalike_duty_name("пик 30С") \
        == "пик 30С"          # mostly Cyrillic — untouched
    assert fam._delookalike_duty_name("rated") == "rated"

    # integration: POST /api/family/duty stores the normalised name
    name_in = "rated 120С wire 80C NdFeB"
    r = client.post("/api/family/duty", json={
        "die": DIE, "config": CFG,
        "duty": {"name": name_in, "mode": "motor", "current_arms": 85.0,
                 "rpm": 6000.0, "gamma_deg": 12.0}})
    assert r.status_code == 200, r.text
    assert _duty_doc(dies, "rated 120C wire 80C NdFeB")

    # …and PATCH /duty/.../rename normalises the new name too
    r = client.patch(f"/api/family/duty/{DIE}/{CFG}/{DUTY}",
                     json={"name": "peak 200С wire 120C NdFeB"})
    assert r.status_code == 200, r.text
    assert r.json()["duty"] == "peak 200C wire 120C NdFeB"


# ── duplicate / rename / delete carry the files ──────────────────────────────

def test_duplicating_a_duty_copies_its_runs_and_their_files(dies):
    _save_everything("current", 95.5)
    _save_everything("pwm_voltage", 91.0)
    r = client.post(f"/api/family/duty/{DIE}/{CFG}/{DUTY}/duplicate",
                    json={"name": "peak copy"})
    assert r.status_code == 200, r.text
    assert r.json()["runs_copied"] == ["current", "pwm_voltage"]
    copy = _duty_doc(dies, "peak copy")
    orig = _duty_doc(dies, DUTY)
    for drive in ("current", "pwm_voltage"):
        a = copy["runs"][drive]["payload_file"]
        b = orig["runs"][drive]["payload_file"]
        assert a != b, "the copy must own its own file, not share the original's"
        assert (dies / DIE / a).is_file() and (dies / DIE / b).is_file()
    j = client.get(f"/api/family/duty_run/{DIE}/{CFG}/peak copy/pwm_voltage").json()
    assert j["payload"]["time_s"]
    # deleting the ORIGINAL leaves the copy's payload intact
    assert client.delete(f"/api/family/duty/{DIE}/{CFG}/{DUTY}").status_code == 200
    assert (dies / DIE / copy["runs"]["pwm_voltage"]["payload_file"]).is_file()


def test_renaming_a_duty_moves_its_run_files(dies):
    _save_everything("pwm_voltage", 91.0)
    old_rel = _duty_doc(dies)["runs"]["pwm_voltage"]["payload_file"]
    r = client.patch(f"/api/family/duty/{DIE}/{CFG}/{DUTY}", json={"name": "peak2"})
    assert r.status_code == 200, r.text
    new_rel = _duty_doc(dies, "peak2")["runs"]["pwm_voltage"]["payload_file"]
    assert new_rel != old_rel
    assert (dies / DIE / new_rel).is_file()
    assert not (dies / DIE / old_rel).exists()
    j = client.get(f"/api/family/duty_run/{DIE}/{CFG}/peak2/pwm_voltage").json()
    assert j["payload"]["time_s"]


def _fake_field(dies, duty: str, kind: str = "thermal", cfg: str = CFG) -> Path:
    """One stored map, written where ``duty_fields.save`` would write it."""
    import numpy as np
    from motor_ai_sim import duty_fields as df

    p = df.fields_dir(DIE, cfg, duty) / f"{kind}.npz"
    p.parent.mkdir(parents=True, exist_ok=True)
    meta = {"version": 1, "kind": kind, "die": DIE, "config": cfg,
            "duty": duty, "saved_at": "2026-09-16T20:00:00"}
    np.savez_compressed(p, meta=np.array(json.dumps(meta)),
                        T_C=np.arange(6, dtype=np.float32))
    return p


def test_renaming_a_duty_carries_its_results_and_its_stored_fields(dies):
    """A rename moved the run payloads and left the duty's ANSWERS behind
    (2026-09-16, CIANO10 200 opt / L180 gen): the results stayed keyed under
    the old name and the maps stayed in the folder the old name hashes to, so
    the next report said "not solved for this duty" over solves still on
    disk."""
    import numpy as np
    from motor_ai_sim import duty_fields as df
    from motor_ai_sim import duty_results as dr

    _save_everything("pwm_voltage", 91.0)
    assert dr.record(DIE, CFG, DUTY, "thermal", {"coil_temp_c": 123.4})
    old_map = _fake_field(dies, DUTY)
    assert old_map.is_file()

    r = client.patch(f"/api/family/duty/{DIE}/{CFG}/{DUTY}", json={"name": "peak2"})
    assert r.status_code == 200, r.text
    assert r.json()["results_carried"] is True
    assert r.json()["fields_carried"] == 1

    rows = dr.get(DIE, CFG)
    assert DUTY not in rows and "peak2" in rows
    assert rows["peak2"]["thermal"]["coil_temp_c"] == 123.4

    assert not old_map.exists()
    new_map = df.fields_dir(DIE, CFG, "peak2") / "thermal.npz"
    assert new_map.is_file()
    # the arrays survive the move, and the name INSIDE the file follows it —
    # `have` keys its listing off that name and cannot invert the hash
    got = df.load(DIE, CFG, "peak2", "thermal")
    assert got is not None
    assert np.allclose(got["T_C"], np.arange(6))
    assert got["meta"]["duty"] == "peak2"
    assert list(df.kinds_present(DIE, CFG)) == ["peak2"]


def test_renaming_a_duty_to_its_own_name_moves_nothing(dies):
    from motor_ai_sim import duty_fields as df
    from motor_ai_sim import duty_results as dr

    _save_everything("pwm_voltage", 91.0)
    assert dr.record(DIE, CFG, DUTY, "thermal", {"coil_temp_c": 123.4})
    p = _fake_field(dies, DUTY)
    before = p.read_bytes()
    r = client.patch(f"/api/family/duty/{DIE}/{CFG}/{DUTY}", json={"name": DUTY})
    assert r.status_code == 200, r.text
    assert r.json()["fields_carried"] == 0
    assert p.read_bytes() == before
    assert dr.get(DIE, CFG)[DUTY]["thermal"]["coil_temp_c"] == 123.4


def test_a_duty_with_no_results_or_fields_still_renames(dies):
    """A duty that was never solved has nothing to carry, and the rename is
    the plain one it always was — bookkeeping never fails a rename."""
    assert _save_second_duty("idle").status_code == 200
    r = client.patch(f"/api/family/duty/{DIE}/{CFG}/idle", json={"name": "pause"})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "duty": "pause",
                        "results_carried": False, "fields_carried": 0}
    assert _duty_doc(dies, "pause")["name"] == "pause"


def test_deleting_a_duty_removes_its_run_files(dies):
    _save_everything("current", 95.5)
    _save_everything("pwm_voltage", 91.0)
    rels = [r["payload_file"] for r in _duty_doc(dies)["runs"].values()]
    r = client.delete(f"/api/family/duty/{DIE}/{CFG}/{DUTY}")
    assert r.status_code == 200 and r.json()["runs_deleted"] == 2
    for rel in rels:
        assert not (dies / DIE / rel).exists()


def test_duplicating_a_configuration_copies_the_run_files(dies):
    _save_everything("pwm_voltage", 91.0)
    r = client.post(f"/api/family/config/{DIE}/{CFG}/duplicate", json={"name": "L41"})
    assert r.status_code == 200, r.text
    rel = _duty_doc(dies, DUTY, cfg="L41")["runs"]["pwm_voltage"]["payload_file"]
    assert rel.startswith("runs/L41/")
    assert (dies / DIE / rel).is_file()
    j = client.get(f"/api/family/duty_run/{DIE}/L41/{DUTY}/pwm_voltage").json()
    assert j["payload"]["time_s"]
    # the original still has its own
    assert (dies / DIE / _duty_doc(dies)["runs"]["pwm_voltage"]["payload_file"]).is_file()


def test_renaming_a_configuration_moves_the_run_files(dies):
    _save_everything("pwm_voltage", 91.0)
    old = _duty_doc(dies)["runs"]["pwm_voltage"]["payload_file"]
    r = client.patch(f"/api/family/config/{DIE}/{CFG}", json={"name": "L42"})
    assert r.status_code == 200, r.text
    new = _duty_doc(dies, DUTY, cfg="L42")["runs"]["pwm_voltage"]["payload_file"]
    assert new.startswith("runs/L42/") and not (dies / DIE / old).exists()
    assert (dies / DIE / new).is_file()


def test_deleting_a_configuration_removes_its_run_folder(dies):
    _save_everything("pwm_voltage", 91.0)
    assert (dies / DIE / "runs" / CFG).is_dir()
    assert client.delete(f"/api/family/config/{DIE}/{CFG}").status_code == 200
    assert not (dies / DIE / "runs" / CFG).exists()


def test_duplicating_a_die_carries_the_run_files(dies):
    _save_everything("pwm_voltage", 91.0)
    r = client.post(f"/api/family/die/{DIE}/duplicate", json={"name": "TESTDIE 41"})
    assert r.status_code == 200, r.text
    rel = _duty_doc(dies)["runs"]["pwm_voltage"]["payload_file"]
    assert (dies / "TESTDIE 41" / rel).is_file()
    j = client.get(f"/api/family/duty_run/TESTDIE 41/{CFG}/{DUTY}/pwm_voltage").json()
    assert j["payload"]["time_s"]


def test_deleting_a_die_takes_its_run_folder_with_it(dies):
    _save_everything("pwm_voltage", 91.0)
    r = client.delete(f"/api/family/die/{DIE}?force=true")
    assert r.status_code == 200, r.text
    assert not (dies / DIE / "runs").exists()
