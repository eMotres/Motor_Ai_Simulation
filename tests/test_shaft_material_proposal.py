# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""The shaft-material rule of 2026-10-14 is PREPARED, never applied: it classifies dies by stator OD, lists what each change
would mean, and writes nothing to the catalog.  Closed-form, no solver."""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("shaft_material_proposal", ROOT / "scripts" / "shaft_material_proposal.py")
SP = importlib.util.module_from_spec(spec)
spec.loader.exec_module(SP)


@pytest.mark.parametrize("od,cls,prop", [
    (30, "smallest", "Stainless_420"), (40, "smallest", "Stainless_420"),
    (50, "undecided", None), (84.9, "undecided", None),
    (85, "85 mm and larger", "Aluminium_6061"), (100, "85 mm and larger", "Aluminium_6061"),
    (150, "85 mm and larger", "Aluminium_6061"),
    (200, "untouched", None), (250, "untouched", None)])
def test_the_rule_is_by_stator_diameter(od, cls, prop):
    r = SP.classify(od)
    assert r["class"].startswith(cls) and r["proposed"] == prop


def _dies(tmp_path):
    root = tmp_path / "dies"
    spec_ = {"S40": (40.0, {"shaft": "Aluminium_6061"}), "M85": (85.0, {"shaft": "Steel_42CrMo4_QT"}),
             "M85d": (85.0, {}), "BIG200": (200.0, {"shaft": "Steel_42CrMo4_QT"}), "MID60": (60.0, {})}
    for name, (od, mats) in spec_.items():
        d = root / name
        d.mkdir(parents=True)
        (d / "die.yaml").write_text(yaml.safe_dump({"geometry": {"stator_diameter": od}}), encoding="utf-8")
        (d / "L1.yaml").write_text(yaml.safe_dump({"materials": dict(mats)}), encoding="utf-8")
    return root


def test_the_proposal_lists_what_each_change_means_and_writes_nothing(tmp_path):
    root = _dies(tmp_path)
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*.yaml")}
    rows = {r["die"]: r for r in SP.proposal(root, with_mass=False)}
    # Ø40: aluminium (non-magnetic) -> SS420 (FERROMAGNETIC): a physics change
    assert rows["S40"]["proposed"] == "Stainless_420" and rows["S40"]["meaning"].startswith("NON-MAGNETIC -> MAGNETIC")
    # 85 with the magnetic 42CrMo4 shaft today -> aluminium: this one matters
    assert rows["M85"]["proposed"] == "Aluminium_6061" and rows["M85"]["meaning"].startswith("MAGNETIC -> NON-MAGNETIC")
    # a configuration that names no shaft takes the shipped default (aluminium 6061): already the proposed material
    assert rows["M85d"]["current_source"] == "the shipped default" and rows["M85d"]["meaning"] == "already that material"
    # the private high-speed die and the undecided size are NOT touched
    assert rows["BIG200"]["proposed"] is None and rows["BIG200"]["meaning"] == "not changed by the rule"
    assert rows["MID60"]["proposed"] is None and rows["MID60"]["class"].startswith("undecided")
    patch = yaml.safe_load(SP.to_patch(list(rows.values())))
    assert {Path(c["file"]).parent.name for c in patch["changes"]} == {"S40", "M85"}
    assert "PROPOSAL ONLY" in SP.to_markdown(list(rows.values()))
    assert before == {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*.yaml")}


def test_main_never_writes_the_catalog_and_the_card_is_not_assigned(tmp_path):
    root = _dies(tmp_path)
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*.yaml")}
    assert SP.main(["--dies-dir", str(root), "--no-mass", "--out", str(tmp_path / "r.md"), "--patch", str(tmp_path / "p.yaml")]) == 0
    assert (tmp_path / "p.yaml").read_text(encoding="utf-8").startswith("# PROPOSAL ONLY")
    assert before == {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*.yaml")}
    # the real catalog of the repository: nothing names the provisional card
    for f in (ROOT / "config" / "dies").glob("*/*.yaml"):
        assert "Stainless_316\n" not in f.read_text(encoding="utf-8") + "\n", f
