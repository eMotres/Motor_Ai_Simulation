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


# --- Python literals of closed-motor data (re-audit R4, 2026-10-20) --------------------------------------------------
# All values below are SYNTHETIC; the shapes are the ones the real leak had (a card id, a pin, a source-record hash and
# a basis dict in a public module).

_SYN_HASH = "ab" * 32
_LEAK = f'''
_BASIS = {{
    "{_SYN_HASH}": {{"windingC": 1, "magnetC": 2, "stackLengthMm": 3, "rpm": 4, "currentA": 5,
                     "sourceRecord": "rec", "sourceSha256": "{'cd' * 32}"}},
}}
_REQUIRED = frozenset({{"candidate_x9_synthetic_card"}})
'''


def test_basis_literals_in_public_source_are_refused():
    problems = G.check_python_literals("src/motor_ai_sim/some_module.py", _LEAK)
    text = "\n".join(problems)
    assert "64-hex" in text and "audited reference basis" in text and "candidate_<die>" in text


def test_the_same_literals_in_a_test_are_not_a_source_leak():
    assert not G.check_python_literals("tests/test_x.py", _LEAK)       # tests may build synthetic basis dicts


def test_digest_list_covers_tests_too(monkeypatch):
    import hashlib
    monkeypatch.setattr(G, "PRIVATE_LITERAL_DIGESTS",
                        frozenset({hashlib.sha256(b"some_private_record").hexdigest()}))
    for path in ("src/motor_ai_sim/a.py", "tests/test_a.py"):
        assert G.check_python_literals(path, 'X = "Some_Private_Record"\n'), path
    assert not G.check_python_literals("src/motor_ai_sim/a.py", 'X = "something_else"\n')


def test_a_public_literal_can_be_allowed():
    ok = f'H = "{_SYN_HASH}"  # private-data-ok: published test vector\n'
    assert not G.check_python_literals("src/motor_ai_sim/a.py", ok)
    assert G.check_python_literals("src/motor_ai_sim/a.py", f'H = "{_SYN_HASH}"\n')
    path = "src/motor_ai_sim/a.py"
    G.ALLOWED_LITERALS = frozenset({(path, _SYN_HASH)})
    try:
        assert not G.check_python_literals(path, f'H = "{_SYN_HASH}"\n')
    finally:
        G.ALLOWED_LITERALS = frozenset()


def test_non_python_and_other_folders_are_skipped():
    assert not G.check_python_literals("scripts/x.py", _LEAK)
    assert not G.check_python_literals("src/motor_ai_sim/data.json", _LEAK)
    assert not G.check_python_literals("src/motor_ai_sim/broken.py", "def (:\n")


def test_the_real_reference_basis_module_is_a_loader_only():
    problems = G.check_literals(str(ROOT), ["src/motor_ai_sim/reference_basis.py", "tests/test_reference_basis.py"])
    assert not problems, problems
