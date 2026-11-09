"""Open-only catalog mirror (docs/regions/DESIGN.md): public die, closed die, die with one public configuration,
backup files, unreadable files, per-config result files, passports / cad / revisions, and the report script."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from motor_ai_sim import motor_access as MA
from motor_ai_sim.regions import catalog_manifest as CM


def _w(p: Path, text: str = "x: 1\n") -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.fixture()
def tree(tmp_path, monkeypatch):
    monkeypatch.setattr(MA, "_DIE_ACCESS_FILE", tmp_path / "die_access.json")
    MA._CFG_VIS_CACHE.clear()
    (tmp_path / "die_access.json").write_text(json.dumps({
        "PUB": {"visibility": "public"},
        "SEL": {"visibility": "selected", "clients": ["a@example.com"]},
        "PRIV": {"visibility": "private"},
    }), encoding="utf-8")
    root = tmp_path / "shared"
    # public die: L1 inherits, L2 explicitly private
    _w(root / "dies/PUB/die.yaml", "name: PUB\n")
    _w(root / "dies/PUB/L1.yaml", "name: L1\n")
    _w(root / "dies/PUB/L2.yaml", "visibility: private\nname: L2\n")
    _w(root / "dies/PUB/runs/L1/duty.a.json.gz", "r1")
    _w(root / "dies/PUB/runs/L2/duty.a.json.gz", "r2")
    _w(root / "dies/PUB/runs/GHOST/duty.a.json.gz", "orphan")
    _w(root / "dies/PUB/L1.thumb.png", "png")
    _w(root / "dies/PUB/notes.txt", "who owns this")
    _w(root / "dies/PUB/L1.yaml.bak-20261030-liner", "old")
    _w(root / "dies/PUB/L1.yaml.tmp", "t")
    _w(root / "dies/PUB/L1.yaml~", "t")
    _w(root / "dies/PUB/L1.yaml.orig", "t")
    # closed die
    _w(root / "dies/PRIV/die.yaml", "name: PRIV\n")
    _w(root / "dies/PRIV/C1.yaml", "name: C1\n")
    # selected-clients die with one public configuration
    _w(root / "dies/SEL/die.yaml", "name: SEL\n")
    _w(root / "dies/SEL/S1.yaml", "visibility: public\nname: S1\n")
    _w(root / "dies/SEL/S2.yaml", "name: S2\n")
    _w(root / "dies/SEL/runs/S1/d.json.gz", "s1")
    _w(root / "dies/SEL/runs/S2/d.json.gz", "s2")
    # passports / cad keyed by die and config
    _w(root / "passports/PUB/L1.json", "{}")
    _w(root / "passports/PUB/L2.json", "{}")
    _w(root / "passports/PRIV/C1.json", "{}")
    _w(root / "passports/SEL/S1.json", "{}")
    _w(root / "passports/UNKNOWN/Z.json", "{}")
    _w(root / "cad/PUB/L1/manifest.json", "{}")
    _w(root / "cad/PUB/L2/manifest.json", "{}")
    _w(root / "cad/PRIV/C1/manifest.json", "{}")
    _w(root / "materials_library.yaml", "steel: {}\n")
    _w(root / "devices/d1.yaml", "a: 1\n")
    _w(root / "projects/p1/project.json", "{}")
    return root


def test_open_only_rule(tree):
    m = CM.build(tree)
    f = set(m["files"])
    assert {"materials_library.yaml", "devices/d1.yaml"} <= f
    # public die: die.yaml, inheriting config L1 with its runs and assets
    assert {"dies/PUB/die.yaml", "dies/PUB/L1.yaml", "dies/PUB/runs/L1/duty.a.json.gz", "dies/PUB/L1.thumb.png"} <= f
    # the private configuration and everything of it is out
    assert not any("L2" in x for x in f if x.startswith("dies/PUB/"))
    # unattributable files fail closed and are listed
    assert "dies/PUB/notes.txt" not in f and "dies/PUB/runs/GHOST/" not in f
    why = {e["path"]: e["why"] for e in m["excluded_files"]}
    assert "cannot be attributed" in why["dies/PUB/notes.txt"]
    assert "cannot be attributed" in why["dies/PUB/runs/GHOST/"]
    assert "not public" in why["dies/PUB/L2.yaml"] and "not public" in why["dies/PUB/runs/L2/"]
    # closed die excluded and listed with a reason
    assert not any(x.startswith("dies/PRIV/") for x in f)
    assert {e["die"] for e in m["excluded_dies"]} == {"PRIV"}
    assert "not open" in m["excluded_dies"][0]["why"]
    # selected die with ONE public config: die.yaml + that config + its runs only
    assert {x for x in f if x.startswith("dies/SEL/")} == {"dies/SEL/die.yaml", "dies/SEL/S1.yaml", "dies/SEL/runs/S1/d.json.gz"}
    # keyed stores follow the same rule; unknown die fails closed
    assert {x for x in f if x.startswith("passports/")} == {"passports/PUB/L1.json", "passports/SEL/S1.json"}
    assert {x for x in f if x.startswith("cad/")} == {"cad/PUB/L1/manifest.json"}
    # region-owned data is never in
    assert not any(x.startswith("projects/") for x in f)


def test_backup_and_temp_files_never_listed(tree):
    f = set(CM.build(tree)["files"])
    assert not any(".bak" in x or x.endswith((".tmp", "~", ".orig")) for x in f)
    for name in ("L12.yaml.bak-20261030-liner", "a.tmp", "b~", "c.orig", "x.bak"):
        assert CM._skip(name)
    assert not CM._skip("L12.yaml")


def test_unreadable_file_is_skipped_and_counted(tree, monkeypatch):
    real = CM._sha

    def boom(p):
        if p.name == "L1.thumb.png":
            raise PermissionError("root-owned")
        return real(p)

    monkeypatch.setattr(CM, "_sha", boom)
    m = CM.build(tree)
    assert "dies/PUB/L1.thumb.png" not in m["files"]
    assert m["skipped_unreadable"] == 1 and m["skipped_unreadable_paths"] == ["dies/PUB/L1.thumb.png"]
    assert "dies/PUB/L1.yaml" in m["files"]


def test_unreadable_directory_does_not_crash(tree, monkeypatch):
    real = Path.iterdir

    def it(self):
        if self.name == "devices":
            raise PermissionError("denied")
        return real(self)

    monkeypatch.setattr(Path, "iterdir", it)
    m = CM.build(tree)
    assert m["skipped_unreadable"] >= 1 and "devices/" in m["skipped_unreadable_paths"]


def test_die_without_any_open_part_is_excluded_by_default(tree, tmp_path):
    _w(tree / "dies/NEW/die.yaml", "name: NEW\n")                # no die_access entry -> private
    _w(tree / "dies/NEW/N1.yaml", "name: N1\n")
    m = CM.build(tree)
    assert "NEW" in {e["die"] for e in m["excluded_dies"]}
    assert not any(x.startswith("dies/NEW/") for x in m["files"])


def test_revisions_only_when_every_revision_is_mirrored(tree):
    doc = {"lineages": {"l1": {"id": "l1", "revisions": [{"rev": "A", "die": "PUB", "config": "L1"}]}}}
    _w(tree / "motor_revisions/revisions.json", json.dumps(doc))
    _w(tree / "motor_revisions/snapshots/l1/A/config.yaml", "x: 1\n")
    m = CM.build(tree)
    assert "motor_revisions/revisions.json" in m["files"]
    doc["lineages"]["l2"] = {"id": "l2", "revisions": [{"rev": "A", "die": "PRIV", "config": "C1"}]}
    _w(tree / "motor_revisions/revisions.json", json.dumps(doc))
    m = CM.build(tree)
    assert not any(x.startswith("motor_revisions/") for x in m["files"])
    assert any(e["path"] == "motor_revisions/" for e in m["excluded_files"])


def test_diff_still_works(tree):
    a = CM.build(tree)
    _w(tree / "dies/PUB/L1.yaml", "name: changed\n")
    b = CM.build(tree)
    assert CM.diff(b, a) == {"fetch": ["dies/PUB/L1.yaml"], "delete": []}


def test_report_script(tree, capsys):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import catalog_manifest_report as R
    assert R.main(["--root", str(tree)]) == 0
    out = capsys.readouterr().out
    assert "dies" in out and "excluded dies: 1" in out and "PRIV" in out and "notes.txt" in out
    assert R.main(["--root", str(tree), "--json"]) == 0
    j = json.loads(capsys.readouterr().out)
    assert j["per_top_dir"]["dies"]["files"] >= 1 and j["skipped_unreadable"] == 0
