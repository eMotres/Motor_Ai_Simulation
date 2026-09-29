"""The geometry audit follows the machine it describes (2026-09-20).

``logs/geometry_audit.jsonl`` was pinned to the repo, so every process that was
NOT the owner's API — pytest's TestClient, a sandbox uvicorn on another port
with ``MOTOR_AI_SIM_CONFIG`` redirected — wrote its PUTs into the owner's
forensic trail (pid 48328 on localhost:5199 and test pids sat beside the
owner's 12:47 entry).  The trail exists to answer "who changed MY machine?".

Pinned here, in order of precedence:

* ``MOTOR_AI_SIM_LOG_DIR`` wins outright;
* a process config outside the repo's ``config/`` (a sandbox) writes
  ``<config dir>/logs/geometry_audit.jsonl``;
* the workstation default (repo config) keeps ``<repo>/logs/…`` — unchanged;
* ``record_write`` really writes there, and a monkeypatched ``_AUDIT_PATH``
  still wins (the module's override convention).
"""
from __future__ import annotations

import json
from pathlib import Path

from motor_ai_sim import audit
from motor_ai_sim import config as config_mod

_ROOT = Path(audit.__file__).resolve().parents[2]


def test_the_suite_itself_audits_into_its_sandbox_not_the_repo(monkeypatch):
    """conftest redirects MOTOR_AI_SIM_CONFIG to a sandbox copy, so the whole
    suite's geometry PUTs must land beside that copy — never in the owner's
    logs/."""
    monkeypatch.delenv("MOTOR_AI_SIM_LOG_DIR", raising=False)
    p = audit.audit_path()
    assert p.name == "geometry_audit.jsonl"
    assert p.parent.name == "logs"
    assert p.parent.parent == Path(str(config_mod.DEFAULT_CONFIG_PATH)).resolve().parent
    assert p != _ROOT / "logs" / "geometry_audit.jsonl"


def test_a_redirected_config_moves_the_audit_beside_it(monkeypatch, tmp_path):
    monkeypatch.delenv("MOTOR_AI_SIM_LOG_DIR", raising=False)
    sandbox = tmp_path / "sb"
    sandbox.mkdir()
    monkeypatch.setattr(config_mod, "DEFAULT_CONFIG_PATH", sandbox / "motor_config.yaml")
    assert audit.audit_path() == sandbox / "logs" / "geometry_audit.jsonl"


def test_the_repo_config_keeps_the_repo_logs_dir(monkeypatch):
    """The owner's API (no env, repo config) writes exactly where it always did."""
    monkeypatch.delenv("MOTOR_AI_SIM_LOG_DIR", raising=False)
    monkeypatch.setattr(config_mod, "DEFAULT_CONFIG_PATH",
                        _ROOT / "config" / "motor_config.yaml")
    assert audit.audit_path() == _ROOT / "logs" / "geometry_audit.jsonl"


def test_log_dir_env_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("MOTOR_AI_SIM_LOG_DIR", str(tmp_path / "mylogs"))
    monkeypatch.setattr(config_mod, "DEFAULT_CONFIG_PATH",
                        _ROOT / "config" / "motor_config.yaml")
    assert audit.audit_path() == tmp_path / "mylogs" / "geometry_audit.jsonl"


def test_record_write_lands_on_the_resolved_path_and_the_override_wins(monkeypatch, tmp_path):
    monkeypatch.delenv("MOTOR_AI_SIM_LOG_DIR", raising=False)
    sandbox = tmp_path / "sb2"
    sandbox.mkdir()
    monkeypatch.setattr(config_mod, "DEFAULT_CONFIG_PATH", sandbox / "motor_config.yaml")
    audit.record_write("live_geometry", {"num_poles": 14}, {"num_poles": 16},
                       note="test", client="127.0.0.1:1")
    p = sandbox / "logs" / "geometry_audit.jsonl"
    assert p.is_file()
    line = json.loads(p.read_text(encoding="utf-8").splitlines()[-1])
    assert line["changed"] == ["num_poles"] and line["after"] == {"num_poles": 16}
    assert line["note"] == "test"
    # the module-dict override (the repo's monkeypatch convention) still wins
    monkeypatch.setattr(audit, "_AUDIT_PATH", tmp_path / "pinned.jsonl", raising=False)
    assert audit.audit_path() == tmp_path / "pinned.jsonl"
    audit.record_write("live_geometry", {}, {"num_poles": 12})
    assert (tmp_path / "pinned.jsonl").is_file()
