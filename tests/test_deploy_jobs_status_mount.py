"""The job launcher's status folder reaches the API containers read-only, at the path the application reads."""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_the_status_folder_is_a_read_only_directory_mount_at_the_path_the_application_reads():
    from motor_ai_sim import die_archive
    doc = yaml.safe_load((ROOT / "deploy" / "docker-compose.yml").read_text(encoding="utf-8"))
    vols = doc["x-app-common"]["volumes"]
    hits = [v for v in vols if str(v).endswith(":/var/lib/motres-agent-job:ro")]
    assert len(hits) == 1, vols
    assert hits[0].startswith("${JOBS_STATUS_DIR:-/srv/motres/run/jobs_status}")        # the default is an empty folder
    assert Path(die_archive.JOBS_STATUS).parent == Path("/var/lib/motres-agent-job")    # the mount point is where the code looks
    assert Path(die_archive.JOBS_STATUS).name == "jobs_status.json"


def test_the_variable_and_the_rollout_are_documented():
    assert "JOBS_STATUS_DIR" in (ROOT / ".env.example").read_text(encoding="utf-8")
    readme = (ROOT / "deploy" / "README.md").read_text(encoding="utf-8")
    assert "Job status mount" in readme and "read-only" in readme and "rollback" in readme
