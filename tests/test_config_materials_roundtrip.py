"""A configuration keeps EVERY material it is made of, and a load applies them (owner 2026-10-30).

``POST /api/family/config`` created "CIANO56 400_90 vadim 11 / L90-2x3ph-Al2O3-15t"
from a live machine lined with Al2O3, and the saved yaml named only the magnet and
the two steels.  Loading it filled the liner with Nomex, the next duty save adopted
that Nomex, and every L90 winding temperature was solved with k 0.14 instead of
Al2O3's 24.  Pinned here:

* create, save-as-new-die (one writer), the duty save that defines a fresh build
  and duplicate store the full assignment — liner, enamel, conductor, shaft too;
* a load serves what the file names: Al2O3 survives a load onto a workspace that
  held Nomex;
* a file that does not name a part with a project standard gets that standard and
  the load says so in ONE line; a part with no standard is named in the same line;
* the dry-run lister finds the files lacking a liner / an enamel, read-only.

Throwaway catalog under the pytest tmp area; nothing reads or writes ``config/``.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import yaml

from motor_ai_sim.routes import family as fam

DIE = "CIANO56 400_90 test"
CFG = "L90-2x3ph-Al2O3-15t"
GEO = {"stator_diameter": 400.0, "num_seg": 8, "num_slots_per_segment": 6,
       "num_poles_per_segment": 7, "num_slots": 48, "num_poles": 56,
       "air_gap": 1.0, "magnet_height": 18.8, "tooth_width": 9.0,
       "wire_width": 7.0, "wire_height": 1.0, "num_wires_per_slot": 15,
       "motor_length": 90}
FULL = {"magnet": "N52UH", "stator_core": "B15AHV950M", "rotor_core": "B15AHV950M",
        "shaft": "Steel_42CrMo4_QT", "slot": "copper",
        "slot_insulation": "Al2O3", "wire_insulation": "polyimide"}


def _write(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


def _yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture
def live(monkeypatch):
    """The live machine — its ``materials`` dict is the workspace assignment."""
    cfg = {"geometry": dict(GEO),
           "simulation": {"current_a": 300.0, "rpm": 3000.0, "gamma_deg": 20.0,
                          "mode": "motor"},
           "winding": {"connection": "8S"},
           "materials": dict(FULL, air_gap="air", in_band="air", out_band="air")}
    monkeypatch.setattr(fam, "_live_cfg", lambda: cfg)
    return cfg


@pytest.fixture
def dies(tmp_path, monkeypatch, live):
    root = tmp_path / "dies"
    _write(root / DIE / "die.yaml", {"name": DIE, "locked": False, "geometry": dict(GEO)})
    monkeypatch.setattr(fam, "_DIES_DIR", root)
    monkeypatch.setattr(fam, "_CTX_FILE", tmp_path / ".family_context.json")
    monkeypatch.setattr(fam, "_acting_account", lambda *_a, **_k: ("Vadim", "vadim@example.com"))
    monkeypatch.setattr(fam, "_autolink_new_config", lambda *_a, **_k: None)
    monkeypatch.setattr(fam, "_live_parts", lambda: {})
    monkeypatch.setattr(fam, "may_see_die", lambda *_a, **_k: True)
    monkeypatch.setattr(fam, "_require_die_access", lambda *_a, **_k: None)
    from motor_ai_sim.routes import presets as _presets
    monkeypatch.setattr(_presets, "_gen_thumb_svg", lambda *_a, **_k: None)
    return root


def _create(name: str = CFG) -> dict:
    out = fam.create_config(fam.ConfigCreate(die=DIE, name=name), _w={})
    return _yaml(fam._cfg_file(DIE, out["config"]))


def _workspace_load(workspace: dict, payload: dict) -> dict:
    """What the owner's load does with the payload's materials (lib/dutyApply):
    one PATCH per part the payload names, nothing for a part it does not."""
    out = dict(workspace)
    out.update({k: v for k, v in (payload.get("materials") or {}).items() if v})
    return out


# ── create / save-as-new-die (one writer) ────────────────────────────────────

def test_create_keeps_the_full_assignment(dies):
    c = _create()
    assert c["materials"] == FULL           # the air placeholders are not saved


def test_save_as_new_die_writer_is_the_same_snapshot(dies, live):
    name, doc = fam._config_doc_from_live(DIE, "L90-copy", "motor", live)
    assert doc["materials"] == FULL


# ── load ─────────────────────────────────────────────────────────────────────

def test_al2o3_survives_a_load_after_the_workspace_had_nomex(dies, live):
    _create()
    # the previous machine left Nomex in the shared assignment
    live["materials"]["slot_insulation"] = "Nomex"
    live["materials"]["shaft"] = "Aluminium_6061"
    p = fam.payload(DIE, CFG)
    assert p["materials"]["slot_insulation"] == "Al2O3"
    assert "material_note" not in p and "material_defaults" not in p
    ws = _workspace_load(live["materials"], p)
    assert ws["slot_insulation"] == "Al2O3"
    assert ws["shaft"] == "Steel_42CrMo4_QT"


def test_an_old_file_takes_the_stated_default_and_says_so_once(dies, live):
    """The pre-fix shape: magnet + two steels only."""
    _write(fam._cfg_file(DIE, CFG), {
        "name": CFG, "die": DIE, "role": "motor",
        "geometry_overrides": {"motor_length": 90},
        "winding": {"connection": "8S"},
        "materials": {k: FULL[k] for k in ("magnet", "stator_core", "rotor_core")},
        "duties": []})
    live["materials"]["slot_insulation"] = "Al2O3"      # the workspace's leftover
    p = fam.payload(DIE, CFG)
    assert p["materials"]["slot_insulation"] == "Nomex"      # never the leftover
    assert p["materials"]["wire_insulation"] == "polyimide"
    assert p["materials"]["slot"] == "copper"
    assert p["material_defaults"] == {"slot_insulation": "Nomex",
                                      "wire_insulation": "polyimide", "slot": "copper"}
    note = p["material_note"]
    assert "\n" not in note and len(note) < 200
    assert "liner Nomex" in note and "project default" in note
    # the shaft has no project standard: named, not invented
    assert "shaft" not in p["materials"]
    assert "shaft not named" in note
    # no sleeve band, so the sleeve is not worth a word
    assert "sleeve" not in note


def test_a_duty_that_names_the_liner_outranks_the_default(dies):
    _write(fam._cfg_file(DIE, CFG), {
        "name": CFG, "die": DIE, "role": "motor",
        "geometry_overrides": {"motor_length": 90},
        "winding": {"connection": "8S"},
        "materials": {k: FULL[k] for k in ("magnet", "stator_core", "rotor_core")},
        "duties": [{"name": "S1", "mode": "motor", "current_arms": 300.0,
                    "rpm": 3000.0, "gamma_deg": 20.0,
                    "materials": {"slot_insulation": "Al2O3"}}]})
    p = fam.payload(DIE, CFG, duty="S1")
    assert p["materials"]["slot_insulation"] == "Al2O3"
    assert "slot_insulation" not in p["material_defaults"]
    assert "liner" not in p["material_note"]


# ── duty save / duplicate ────────────────────────────────────────────────────

def test_the_duty_save_that_defines_a_fresh_build_keeps_the_full_assignment(dies, live):
    _write(fam._cfg_file(DIE, CFG), {"name": CFG, "die": DIE, "role": "motor",
                                     "duties": []})
    (dies.parent / ".family_context.json").write_text(
        json.dumps({"die": DIE, "config": CFG, "duty": "S1"}), encoding="utf-8")
    spec = fam.DutySpec(name="S1", mode="motor", from_current=True)
    fam.upsert_duty(fam.DutyCreate(die=DIE, config=CFG, duty=spec), _w={})
    c = _yaml(fam._cfg_file(DIE, CFG))
    assert c["materials"] == FULL


def test_duplicate_keeps_every_part_and_loads_it(dies, live):
    _create()
    fam.duplicate_config(DIE, CFG, fam.ConfigDuplicate(name="L90-variant"), _w={})
    c = _yaml(fam._cfg_file(DIE, "L90-variant"))
    assert c["materials"] == FULL
    live["materials"]["slot_insulation"] = "Nomex"
    p = fam.payload(DIE, "L90-variant")
    assert _workspace_load(live["materials"], p)["slot_insulation"] == "Al2O3"


# ── the dry-run lister ───────────────────────────────────────────────────────

def _lister():
    path = Path(__file__).resolve().parent.parent / "scripts" / "list_configs_missing_materials.py"
    spec = importlib.util.spec_from_file_location("list_configs_missing_materials", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_lister_uses_the_saved_key_list():
    assert tuple(_lister().SAVED_KEYS) == tuple(fam._SAVED_MATERIAL_KEYS)


def test_the_lister_finds_files_without_a_liner_and_writes_nothing(tmp_path, capsys):
    mod = _lister()
    root = tmp_path / "srv"
    _write(root / "shared" / "dies" / DIE / "die.yaml", {"name": DIE})
    _write(root / "shared" / "dies" / DIE / "old.yaml",
           {"name": "old", "materials": {"magnet": "N52UH"},
            "duties": [{"name": "S1", "materials": {"slot_insulation": "Al2O3"}}]})
    _write(root / "shared" / "dies" / DIE / "full.yaml",
           {"name": "full", "materials": dict(FULL)})
    _write(root / "workspaces" / "w1" / "dies" / DIE / "ws.yaml",
           {"name": "ws", "materials": dict(FULL, wire_insulation=None)})
    before = {p: p.read_bytes() for p in root.rglob("*.yaml")}
    out_json = tmp_path / "rows.json"
    assert mod.main(["--root", str(root), "--json", str(out_json)]) == 0
    assert {p: p.read_bytes() for p in root.rglob("*.yaml")} == before
    rows = json.loads(out_json.read_text(encoding="utf-8"))
    assert {(r["layer"], r["config"]) for r in rows} == {("shared", "old"),
                                                         ("workspace/w1", "ws")}
    old = next(r for r in rows if r["config"] == "old")
    assert old["lacks_insulation"] == ["slot_insulation", "wire_insulation"]
    assert old["duty_insulation"] == {"S1": {"slot_insulation": "Al2O3"}}
    assert "2 of 3 configuration(s)" in capsys.readouterr().out
