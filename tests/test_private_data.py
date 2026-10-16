"""The optional private data overlay (MOTOR_AI_SIM_PRIVATE_DATA)."""
from __future__ import annotations

import shutil
from pathlib import Path

from motor_ai_sim import private_data as pd
from motor_ai_sim.inverter import devices as dv

REPO_DEVICES = Path(__file__).resolve().parents[1] / "config" / "devices"


def test_unset_means_no_private_data(monkeypatch):
    monkeypatch.delenv(pd.ENV_VAR, raising=False)
    assert pd.private_root() is None
    assert pd.private_path("config", "devices") is None


def test_missing_folder_is_ignored(monkeypatch, tmp_path):
    monkeypatch.setenv(pd.ENV_VAR, str(tmp_path / "nope"))
    assert pd.private_root() is None


def test_private_device_cards_are_listed_after_public_ones(monkeypatch, tmp_path):
    priv = tmp_path / "config" / "devices"
    priv.mkdir(parents=True)
    shutil.copy2(REPO_DEVICES / "IMCQ120R004M2H.yaml", priv / "PRIVPART-1.yaml")
    monkeypatch.setenv(pd.ENV_VAR, str(tmp_path))
    paths = dv.card_paths()
    assert paths["PRIVPART-1"].parent == priv
    # the public card wins on a name clash
    assert paths["IMCQ120R004M2H"].parent == dv.devices_dir()
    assert dv.card_path("PRIVPART-1") == priv / "PRIVPART-1.yaml"


def test_a_moved_fixture_dir_sees_no_overlay(monkeypatch, tmp_path):
    priv = tmp_path / "priv" / "config" / "devices"
    priv.mkdir(parents=True)
    shutil.copy2(REPO_DEVICES / "IMCQ120R004M2H.yaml", priv / "PRIVPART-1.yaml")
    monkeypatch.setenv(pd.ENV_VAR, str(tmp_path / "priv"))
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    monkeypatch.setattr(dv, "_DIR", fixture)
    assert dv.card_paths() == {}
