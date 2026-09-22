"""The Controller tab's own SETTINGS persist WITH the configuration.

Owner, 2026-09-22: *"при сохранении мотора текущий контроллер тоже должен
сохраняться со всеми настройками"* — until now only the Controller tab's
SOLVE RESULT (losses, junction temperatures, the limit table — the
``duty_results`` ``controller`` kind ``routes.controller.post_solve``
writes) survived a reload; the tab's own FORM (topology, device, mapping, N
parallel, R_g, dead time, carrier, DC link, cooling, the "couple with EM"
flag) did not.

What is under test:

* ``PATCH /api/family/config/{die}/{cfg}/controller`` writes a ``controller:``
  block into the configuration yaml, on the same footing as ``battery``;
* ``GET /api/controller/settings`` reads it back, resolving die/config from
  the active context when neither is given (the same convention
  ``_duty_defaults`` already uses);
* a configuration that has never saved one answers ``{}`` — never a 404,
  never a validation error;
* an OLD configuration yaml (written before this field existed) loads fine
  through every reader that touches a configuration document — migration
  safety without any block-specific backfill, the same ``c.get(...) or {}``
  idiom every other block in this file already follows;
* the ``/tree`` catalog row carries the block too, so a future duty-card
  chip has something to read.

Everything runs against a COPY of config/dies in the pytest tmp area,
mirroring ``tests/test_family_duty_runs.py``'s isolation fixture.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from motor_ai_sim.api import app

_ROOT = Path(__file__).resolve().parents[1]
_REAL_DIES = _ROOT / "config" / "dies"
_REAL_USERS = _ROOT / "config" / "users.json"

DIE = "TESTDIE CTRL"
CFG = "L40"
DUTY = "rated"

ADMIN = "admin@example.com"

client = TestClient(app)


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
    """One die, one configuration, one duty — an OLD-shaped yaml: no
    ``controller`` key at all, the migration-safety case."""
    from motor_ai_sim.routes import family as fam

    root = tmp_path / "dies"
    (root / DIE).mkdir(parents=True)
    (root / DIE / "die.yaml").write_text(yaml.safe_dump({
        "name": DIE, "locked": False, "created": "2026-09-22T10:00:00",
        "geometry": {"num_slots": 12, "num_poles": 10, "stator_diameter": 40.0,
                     "magnet_height": 3.0, "motor_length": 40.0},
    }, sort_keys=False, allow_unicode=True), encoding="utf-8")
    (root / DIE / f"{CFG}.yaml").write_text(yaml.safe_dump({
        "name": CFG, "die": DIE, "role": "motor",
        "geometry_overrides": {"motor_length": 40.0, "wire_height": 1.0},
        "winding": {"connection": "star"},
        "materials": {"magnet": "N42SH", "stator_core": "20SW1200"},
        "duties": [{"name": DUTY, "mode": "motor",
                    "saved_at": "2026-09-22T10:00:00",
                    "current_arms": 85.0, "rpm": 6000.0, "gamma_deg": 12.0,
                    "note": ""}],
    }, sort_keys=False, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(fam, "_DIES_DIR", root)
    return root


@pytest.fixture()
def granted(tmp_path, monkeypatch, dies):
    from motor_ai_sim import auth
    from motor_ai_sim import users as U
    import shutil

    users_file = tmp_path / "users.json"
    shutil.copy2(_REAL_USERS, users_file)
    monkeypatch.setattr(U, "_USERS_FILE", users_file)
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.delenv("CATALOG_GRANT_ALL_REGISTERED", raising=False)
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {ADMIN})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", False)
    U.create_user(ADMIN, "password-admin", tier="admin", name="Admin")
    return {"Authorization": f"Bearer {U.issue_token(ADMIN)}"}


def _cfg_doc(dies, cfg=CFG):
    return yaml.safe_load((dies / DIE / f"{cfg}.yaml").read_text(encoding="utf-8"))


_BLOCK = {
    "device": "IMCQ120R004M2H",
    "topology": "one_3ph",
    "set_split": "series_split",
    "h_bridge_modulation": "unipolar",
    "devices_parallel": 3,
    "devices_parallel_by_bridge": {"INV2": 6},
    "r_g_ext_ohm": 2.3,
    "v_gs_off_V": 0.0,
    "dead_time_us": 0.5,
    "f_carrier_hz": 24000.0,
    "v_dc_V": 750.4,
    "cooling": {"coolant": "water_glycol_50", "flow_lpm": 8.0, "t_in_c": 65.0,
               "r_tim_k_w": 0.03},
    "mapping": [{"coil": 1, "bridge": "INV1", "leg": "A"}],
    "couple_with_em": True,
}


def _patch_controller(die=DIE, cfg=CFG, headers=None, **over):
    body = dict(_BLOCK)
    body.update(over)
    return client.patch(f"/api/family/config/{die}/{cfg}/controller",
                        headers=headers, json=body)


# ---------------------------------------------------------------------------
# save -> yaml has the block
# ---------------------------------------------------------------------------

def test_save_writes_the_block_into_the_configuration_yaml(dies, granted):
    r = _patch_controller(headers=granted)
    assert r.status_code == 200, r.text
    out = r.json()["controller"]
    assert out["device"] == "IMCQ120R004M2H"
    assert out["devices_parallel"] == 3
    assert out["devices_parallel_by_bridge"] == {"INV2": 6}
    assert out["cooling"]["coolant"] == "water_glycol_50"
    assert out["mapping"] == [{"coil": 1, "bridge": "INV1", "leg": "A"}]
    assert out["couple_with_em"] is True
    assert out["saved_at"]

    c = _cfg_doc(dies)
    assert c["controller"]["device"] == "IMCQ120R004M2H"
    assert c["controller"]["couple_with_em"] is True
    # the rest of the document is untouched — a controller save is not a
    # config rewrite
    assert c["winding"] == {"connection": "star"}
    assert len(c["duties"]) == 1


def test_save_is_a_whole_replace_not_a_merge(dies, granted):
    """The tab sends its complete state every time (owner: "со всеми
    настройками") — a second save with a different shape REPLACES the first,
    it does not merge into it."""
    _patch_controller(headers=granted, devices_parallel=3, device="A")
    r = _patch_controller(headers=granted, devices_parallel=1, device="B",
                          devices_parallel_by_bridge={})
    assert r.status_code == 200, r.text
    c = _cfg_doc(dies)
    assert c["controller"]["device"] == "B"
    assert c["controller"]["devices_parallel"] == 1
    assert c["controller"]["devices_parallel_by_bridge"] == {}


def test_blank_numeric_fields_mean_the_dutys_own(dies, granted):
    """``None`` for carrier/DC link is a real, saved state — "from duty PWM"
    — not an omission."""
    r = _patch_controller(headers=granted, f_carrier_hz=None, v_dc_V=None)
    assert r.status_code == 200, r.text
    c = _cfg_doc(dies)
    assert c["controller"]["f_carrier_hz"] is None
    assert c["controller"]["v_dc_V"] is None


def test_devices_parallel_below_one_is_refused(dies, granted):
    r = _patch_controller(headers=granted, devices_parallel=0)
    assert r.status_code == 422
    assert "devices_parallel" in r.json()["detail"]


# ---------------------------------------------------------------------------
# load -> GET /api/controller/settings
# ---------------------------------------------------------------------------

def test_get_settings_round_trips_the_saved_block(dies, granted):
    _patch_controller(headers=granted)
    r = client.get(f"/api/controller/settings?die={DIE}&config={CFG}")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["device"] == "IMCQ120R004M2H"
    assert out["devices_parallel"] == 3
    assert out["mapping"] == [{"coil": 1, "bridge": "INV1", "leg": "A"}]


def test_missing_block_is_an_empty_object_never_an_error(dies, granted):
    """No save yet — never a 404, never a validation error (owner's own
    words: "missing block = the tab's defaults, no error")."""
    r = client.get(f"/api/controller/settings?die={DIE}&config={CFG}")
    assert r.status_code == 200, r.text
    assert r.json() == {}


def test_get_settings_falls_back_to_the_active_context(dies, granted, monkeypatch):
    """No die/config in the query — the same active-context convention
    ``_duty_defaults`` already uses."""
    from motor_ai_sim import duty_results as dr
    _patch_controller(headers=granted)
    monkeypatch.setattr(dr, "active_context", lambda: (DIE, CFG, DUTY))
    r = client.get("/api/controller/settings")
    assert r.status_code == 200, r.text
    assert r.json()["device"] == "IMCQ120R004M2H"


def test_no_active_context_and_no_query_is_an_empty_object(dies, monkeypatch):
    from motor_ai_sim import duty_results as dr
    monkeypatch.setattr(dr, "active_context", lambda: None)
    r = client.get("/api/controller/settings")
    assert r.status_code == 200, r.text
    assert r.json() == {}


# ---------------------------------------------------------------------------
# migration safety — an old yaml with no `controller` key at all
# ---------------------------------------------------------------------------

def test_an_old_configuration_with_no_controller_key_loads_everywhere(dies, granted):
    """The fixture ITSELF is the old-shaped yaml (no `controller:` key) —
    every reader that touches this configuration must tolerate that."""
    c = _cfg_doc(dies)
    assert "controller" not in c

    r = client.get(f"/api/controller/settings?die={DIE}&config={CFG}")
    assert r.status_code == 200 and r.json() == {}

    tree = client.get("/api/family/tree", headers=granted)
    assert tree.status_code == 200, tree.text
    row = next(cf for d in tree.json()["dies"] if d["name"] == DIE
              for cf in d["configs"] if cf["name"] == CFG)
    assert row["controller"] is None

    payload = client.get(f"/api/family/payload/{DIE}/{CFG}", headers=granted,
                         params={"duty": DUTY})
    assert payload.status_code == 200, payload.text
