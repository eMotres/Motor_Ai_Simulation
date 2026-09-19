"""Tests for sweep persistence and resumption."""
import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from motor_ai_sim import sweep_journal as journal_module


class TestSweepJournal:
    """Tests for sweep journal creation, update, and persistence."""

    @pytest.fixture
    def temp_config_dir(self):
        """Create a temporary config directory for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    def test_create_sweep_journal(self, temp_config_dir):
        """Test creating a new sweep journal."""
        sweep_id = "test-sweep-001"
        req_body = {
            "variables": [{"name": "slot_height", "min": 5, "max": 10, "mode": "sweep", "step": 1}],
            "operating_points": [{"current_a": 85.0, "gamma_deg": 0.0, "rpm": 3950.0}],
            "steps_per_period": 6,
        }
        machine_fp = "abc123def456"
        owner = "test_ws"

        record = journal_module.create_sweep_journal(
            temp_config_dir,
            sweep_id=sweep_id,
            request_body=req_body,
            total_points=23,
            owner=owner,
            machine_fp=machine_fp,
        )

        assert record.sweep_id == sweep_id
        assert record.state == "running"
        assert record.total_points == 23
        assert record.done_points == []
        assert record.owner == owner
        assert record.machine_fp == machine_fp

        # Check file was written
        path = journal_module._journal_path(temp_config_dir)
        assert os.path.exists(path)

        # Verify file contents
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        assert data["sweep_id"] == sweep_id
        assert data["state"] == "running"

    def test_update_sweep_journal(self, temp_config_dir):
        """Test updating a journal with completed points."""
        sweep_id = "test-sweep-002"
        req_body = {"variables": [], "operating_points": [], "steps_per_period": 6}

        # Create journal
        journal_module.create_sweep_journal(
            temp_config_dir,
            sweep_id=sweep_id,
            request_body=req_body,
            total_points=5,
            owner="test",
            machine_fp="fp123",
        )

        # Update with completed points
        journal_module.update_sweep_journal(temp_config_dir, sweep_id, 0)
        journal_module.update_sweep_journal(temp_config_dir, sweep_id, 2)
        journal_module.update_sweep_journal(temp_config_dir, sweep_id, 4)

        # Load and verify
        loaded = journal_module.load_sweep_journal(temp_config_dir)
        assert loaded is not None
        assert set(loaded.done_points) == {0, 2, 4}

    def test_finish_sweep_journal(self, temp_config_dir):
        """Test marking a sweep as finished."""
        sweep_id = "test-sweep-003"
        req_body = {}

        journal_module.create_sweep_journal(
            temp_config_dir,
            sweep_id=sweep_id,
            request_body=req_body,
            total_points=1,
            owner="test",
            machine_fp="fp",
        )

        journal_module.finish_sweep_journal(temp_config_dir, sweep_id)

        loaded = journal_module.load_sweep_journal(temp_config_dir)
        assert loaded is not None
        assert loaded.state == "finished"

    def test_cancel_sweep_journal(self, temp_config_dir):
        """Test marking a sweep as cancelled."""
        sweep_id = "test-sweep-004"
        req_body = {}

        journal_module.create_sweep_journal(
            temp_config_dir,
            sweep_id=sweep_id,
            request_body=req_body,
            total_points=1,
            owner="test",
            machine_fp="fp",
        )

        journal_module.cancel_sweep_journal(temp_config_dir, sweep_id)

        loaded = journal_module.load_sweep_journal(temp_config_dir)
        assert loaded is not None
        assert loaded.state == "cancelled"

    def test_load_nonexistent_journal(self, temp_config_dir):
        """Test loading from a directory with no journal returns None."""
        loaded = journal_module.load_sweep_journal(temp_config_dir)
        assert loaded is None

    def test_should_resume_sweep_running_and_valid(self, temp_config_dir):
        """Test that a valid running sweep should be resumed."""
        fp = "current_fingerprint"
        record = journal_module.SweepJournalRecord(
            sweep_id="test",
            started_at=datetime.utcnow().isoformat() + "Z",
            owner="ws",
            machine_fp=fp,
            request_body={"variables": []},
            total_points=10,
            state="running",
        )

        should_resume, reason = journal_module.should_resume_sweep(record, fp)
        assert should_resume is True
        assert reason == ""

    def test_should_not_resume_finished_sweep(self):
        """Test that a finished sweep should not be resumed."""
        fp = "current_fingerprint"
        record = journal_module.SweepJournalRecord(
            sweep_id="test",
            started_at=datetime.utcnow().isoformat() + "Z",
            owner="ws",
            machine_fp=fp,
            request_body={"variables": []},
            total_points=10,
            state="finished",
        )

        should_resume, reason = journal_module.should_resume_sweep(record, fp)
        assert should_resume is False
        assert "finished" in reason.lower()

    def test_should_not_resume_cancelled_sweep(self):
        """Test that a cancelled sweep should not be resumed."""
        fp = "current_fingerprint"
        record = journal_module.SweepJournalRecord(
            sweep_id="test",
            started_at=datetime.utcnow().isoformat() + "Z",
            owner="ws",
            machine_fp=fp,
            request_body={"variables": []},
            total_points=10,
            state="cancelled",
        )

        should_resume, reason = journal_module.should_resume_sweep(record, fp)
        assert should_resume is False
        assert "cancelled" in reason.lower()

    def test_should_not_resume_on_fp_mismatch(self):
        """Test that a sweep with mismatched fingerprint should not be resumed."""
        old_fp = "old_fingerprint"
        new_fp = "new_fingerprint"
        record = journal_module.SweepJournalRecord(
            sweep_id="test",
            started_at=datetime.utcnow().isoformat() + "Z",
            owner="ws",
            machine_fp=old_fp,
            request_body={"variables": []},
            total_points=10,
            state="running",
        )

        should_resume, reason = journal_module.should_resume_sweep(record, new_fp)
        assert should_resume is False
        assert "fingerprint" in reason.lower()

    def test_should_not_resume_old_sweep(self):
        """Test that very old sweeps should not be resumed."""
        fp = "fp"
        old_date = datetime.utcnow() - timedelta(days=8)
        record = journal_module.SweepJournalRecord(
            sweep_id="test",
            started_at=old_date.isoformat() + "Z",
            owner="ws",
            machine_fp=fp,
            request_body={"variables": []},
            total_points=10,
            state="running",
        )

        should_resume, reason = journal_module.should_resume_sweep(record, fp, max_age_days=7)
        assert should_resume is False
        assert "days old" in reason.lower()

    def test_sweep_resume_info(self):
        """Test SweepResumeInfo creation and serialization."""
        resume_info = journal_module.SweepResumeInfo(
            at="2026-09-19T14:23:45Z",
            done_before=5,
            total=23,
        )

        record = journal_module.SweepJournalRecord(
            sweep_id="test",
            started_at="2026-09-19T14:20:00Z",
            owner="ws",
            machine_fp="fp",
            request_body={},
            total_points=23,
            resumed_from_restart=resume_info,
        )

        # Test serialization
        d = record.to_dict()
        assert d["resumed_from_restart"]["at"] == "2026-09-19T14:23:45Z"
        assert d["resumed_from_restart"]["done_before"] == 5

        # Test deserialization
        loaded = journal_module.SweepJournalRecord.from_dict(d)
        assert loaded.resumed_from_restart is not None
        assert loaded.resumed_from_restart.done_before == 5

    def test_journal_wrapper(self, temp_config_dir):
        """Test the SweepJournal test-friendly wrapper."""
        jw = journal_module.SweepJournal(temp_config_dir)
        assert not jw.exists()

        # Create a journal
        journal_module.create_sweep_journal(
            temp_config_dir,
            sweep_id="test",
            request_body={},
            total_points=5,
            owner="ws",
            machine_fp="fp",
        )

        assert jw.exists()

        loaded = jw.read()
        assert loaded is not None
        assert loaded.sweep_id == "test"

        jw.delete()
        assert not jw.exists()
