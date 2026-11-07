"""Boot-time seeding of NEW device cards from the image into the shared catalogue."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from motor_ai_sim import seed_devices as SD  # noqa: E402


@pytest.fixture
def dirs(tmp_path: Path):
    seed = tmp_path / "config-seed" / "devices"
    shared = tmp_path / "shared" / "devices"
    seed.mkdir(parents=True)
    shared.mkdir(parents=True)
    return seed, shared


def test_new_seed_file_is_copied_and_logged(dirs, caplog):
    seed, shared = dirs
    (seed / "NEW-1.yaml").write_text("part: NEW-1\n", encoding="utf-8")
    (shared / "OLD.yaml").write_text("part: OLD\n", encoding="utf-8")
    with caplog.at_level(logging.INFO, logger=SD.log.name):
        rep = SD.seed_new_cards(seed, shared)
    assert rep.seeded == ["NEW-1.yaml"]
    assert (shared / "NEW-1.yaml").read_text(encoding="utf-8") == "part: NEW-1\n"
    assert "seeded device card NEW-1" in caplog.text
    assert not list(shared.glob(".seed-*")), "temporary files must not be left behind"


def test_existing_file_is_untouched_even_when_it_differs(dirs):
    seed, shared = dirs
    (seed / "A.yaml").write_text("part: A\nseed: 1\n", encoding="utf-8")
    (shared / "A.yaml").write_text("part: A\nowner_edit: 7\n", encoding="utf-8")
    (shared / "ONLY-LIVE.yaml").write_text("part: ONLY-LIVE\n", encoding="utf-8")   # not in the seed: never deleted
    before = (shared / "A.yaml").stat().st_mtime_ns
    rep = SD.seed_new_cards(seed, shared)
    assert rep.seeded == []
    assert (shared / "A.yaml").read_text(encoding="utf-8") == "part: A\nowner_edit: 7\n"
    assert (shared / "A.yaml").stat().st_mtime_ns == before
    assert (shared / "ONLY-LIVE.yaml").is_file()


def test_second_run_is_idempotent(dirs):
    seed, shared = dirs
    (seed / "B.yaml").write_text("part: B\n", encoding="utf-8")
    first = SD.seed_new_cards(seed, shared)
    stamp = (shared / "B.yaml").stat().st_mtime_ns
    second = SD.seed_new_cards(seed, shared)
    assert first.seeded == ["B.yaml"]
    assert second.seeded == [] and second.differs == [] and second.errors == []
    assert (shared / "B.yaml").stat().st_mtime_ns == stamp
    assert sorted(p.name for p in shared.iterdir()) == ["B.yaml"]


def test_warning_logged_when_contents_differ(dirs, caplog):
    seed, shared = dirs
    (seed / "C.yaml").write_text("part: C\nseed: 1\n", encoding="utf-8")
    (shared / "C.yaml").write_text("part: C\nedited: 1\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger=SD.log.name):
        rep = SD.seed_new_cards(seed, shared)
    assert rep.differs == ["C.yaml"]
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "C.yaml" in warnings[0].getMessage() and "differs" in warnings[0].getMessage()


def test_identical_existing_card_is_silent(dirs, caplog):
    seed, shared = dirs
    for d in (seed, shared):
        (d / "D.yaml").write_text("part: D\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger=SD.log.name):
        rep = SD.seed_new_cards(seed, shared)
    assert rep.seeded == [] and rep.differs == []
    assert caplog.text == ""


def test_only_top_level_yaml_cards_are_seeded(dirs):
    seed, shared = dirs
    (seed / "E.yaml").write_text("part: E\n", encoding="utf-8")
    (seed / "notes.txt").write_text("x", encoding="utf-8")
    (seed / "spice").mkdir()
    (seed / "spice" / "F.yaml").write_text("x", encoding="utf-8")
    SD.seed_new_cards(seed, shared)
    assert sorted(p.name for p in shared.iterdir()) == ["E.yaml"]


@pytest.mark.skipif(not hasattr(__import__("os"), "chmod") or sys.platform == "win32",
                    reason="POSIX permission bits")
def test_new_card_takes_the_mode_of_existing_cards(dirs):
    seed, shared = dirs
    ref = shared / "REF.yaml"
    ref.write_text("part: REF\n", encoding="utf-8")
    ref.chmod(0o640)
    (seed / "G.yaml").write_text("part: G\n", encoding="utf-8")
    SD.seed_new_cards(seed, shared)
    assert (shared / "G.yaml").stat().st_mode & 0o777 == 0o640


def test_missing_directories_are_a_noop(tmp_path: Path):
    assert SD.seed_new_cards(tmp_path / "nope", tmp_path / "also-nope") == SD.SeedReport()
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "H.yaml").write_text("part: H\n", encoding="utf-8")
    rep = SD.seed_new_cards(seed, tmp_path / "no-shared")   # a missing shared folder is not created
    assert rep.seeded == [] and not (tmp_path / "no-shared").exists()


def test_boot_hook_targets_shared_root_devices(tmp_path: Path, monkeypatch):
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "I.yaml").write_text("part: I\n", encoding="utf-8")
    shared_root = tmp_path / "shared"
    (shared_root / "devices").mkdir(parents=True)
    from motor_ai_sim import workspace
    monkeypatch.setattr(SD, "seed_dir", lambda: seed)
    monkeypatch.setattr(workspace, "shared_root", lambda: shared_root)
    rep = SD.seed_new_device_cards()
    assert rep.seeded == ["I.yaml"] and (shared_root / "devices" / "I.yaml").is_file()
