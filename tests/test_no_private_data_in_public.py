# SPDX-License-Identifier: Apache-2.0
"""Guard: the public repository holds no 3-D model and no closed-motor data (owner decision 2026-10-19).

The real check is ``scripts/check_no_private_data.py`` (also run in CI).  Closed motors and ALL 3-D models live only in
eMotres/motor-ai-sim-private; the app finds them through MOTOR_AI_SIM_PRIVATE_DATA (docs/OPEN_PRIVATE_DATA.md).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("check_no_private_data", ROOT / "scripts" / "check_no_private_data.py")
G = importlib.util.module_from_spec(_spec)
sys.modules["check_no_private_data"] = G
_spec.loader.exec_module(G)


def test_public_tree_is_clean():
    problems = G.check(str(ROOT))
    assert not problems, "private data in the public repo:\n" + "\n".join(problems)


def test_a_3d_model_is_refused():
    for p in ("cad/reference/Shaft.step", "docs/x/board.STEP", "web/public/m.glb", "a/b/c.FCStd", "kicad/x.kicad_pcb",
              "lamination.dxf"):
        assert G.check_paths([p]), p


def test_closed_motor_data_is_refused():
    for p in ("config/passports/CIANO14 40 new/L12.json",
              "config/propeller_s1/CIANO28 85 20SW1200/L13.json",
              "config/passport_specs/CIANO14_40_new.yaml",
              "tests/fixtures/server_bom/CIANO14_40_new_L12_BOM.csv",
              "tests/fixtures/cad_bom_dump_L12.json",
              "cad/reference/CIANO14_40_12.json",
              "cad/reference/fusion_mechanics/Shaft.json",
              "docs/data/passport_pilot_d40/passport_L12.json",
              "config/dies/CIANO14 40_12/die.yaml",
              "config/dies/Some new die/L1.yaml"):
        assert G.check_paths([p]), p


def test_open_data_and_tooling_are_allowed():
    for p in ("config/dies/CIANO14 30_10/die.yaml",
              "config/dies/100 mm · 24s-28p mid-torque/L15.yaml",
              "data/open/dies/Demo 30 · 12s-14p spindle/die.yaml",
              "cad/reference/make_die_reference.py",
              "cad/fc_scripts/build_model.py",
              "tests/fixtures/catalog_golden/devices_pre_envelope.json"):
        assert not G.check_paths([p]), p


def test_catalogue_and_preset_entries_must_be_open(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "motor_catalog.json").write_text('{"motors": [{"id": "cat_ciano14_30_10"}, {"id": "cat_x_closed"}]}')
    (tmp_path / "config" / "motor_presets.json").write_text('{"ciano14_30_10": {}, "my_closed": {}}')
    problems = G.check_catalog_files(str(tmp_path))
    assert len(problems) == 2 and "cat_x_closed" in problems[0] and "my_closed" in problems[1]
