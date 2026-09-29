"""deploy/node-agent/motres_node_agent.py: the "created" timestamp parsing
that feeds the Admin -> Overview live-load Now table's outside-app uptime
column. Stdlib-only script (no package, no docker calls here) -- imported
directly from its file path, same trick as loading a script for a dry run."""
from __future__ import annotations

import calendar
import importlib.util
import sys
import time
from pathlib import Path

import pytest

# The agent targets the Linux hosts it's deployed to (os.sysconf, pwd) --
# same convention as test_data_protection.py's POSIX-only mode tests.
if sys.platform.startswith("win"):
    pytest.skip("deploy/node-agent targets Linux hosts only (os.sysconf, pwd)",
               allow_module_level=True)

_PATH = Path(__file__).resolve().parents[1] / "deploy" / "node-agent" / "motres_node_agent.py"
_spec = importlib.util.spec_from_file_location("motres_node_agent", _PATH)
assert _spec and _spec.loader
agent = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(agent)


def test_parses_docker_ps_created_at():
    # docker ps --format {{.CreatedAt}}: "2026-09-15 08:00:00 +0000 UTC"
    ts = agent._parse_docker_time("2026-09-15 08:00:00 +0000 UTC")
    expect = calendar.timegm(time.strptime("2026-09-15 08:00:00", "%Y-%m-%d %H:%M:%S"))
    assert ts == float(expect)


def test_parses_docker_ps_created_at_with_t_separator_and_fraction():
    ts = agent._parse_docker_time("2026-09-15T08:00:00.123456789Z")
    expect = calendar.timegm(time.strptime("2026-09-15 08:00:00", "%Y-%m-%d %H:%M:%S"))
    assert ts == float(expect)


def test_unparseable_time_is_zero_not_a_crash():
    assert agent._parse_docker_time("") == 0.0
    assert agent._parse_docker_time("garbage") == 0.0


def test_container_meta_keeps_name_and_created_per_container_id():
    calls = []

    def fake_docker(args):
        calls.append(args)
        if args[0] == "ps":
            return ("abc123\tdeploy-api-1\t2026-09-15 08:00:00 +0000 UTC\n"
                   "def456\tmesher_1\t2026-09-20 12:30:00 +0000 UTC\n")
        return ""
    agent._docker = fake_docker
    meta = agent.container_meta()
    assert meta["abc123"][0] == "deploy-api-1"
    assert meta["def456"][0] == "mesher_1"
    assert meta["def456"][1] > meta["abc123"][1]              # later created ts
    assert agent.container_names(meta) == {"abc123": "deploy-api-1", "def456": "mesher_1"}


def test_containers_attaches_created_from_meta_by_name():
    import json

    def fake_docker(args):
        if args[0] == "stats":
            return json.dumps({"Name": "mesher_1", "CPUPerc": "210.5%",
                               "MemUsage": "2GiB / 8GiB"}) + "\n"
        return ""
    agent._docker = fake_docker
    meta = {"def456": ("mesher_1", 12345.0)}
    out = agent.containers(meta)
    assert out == [{"name": "mesher_1", "cpu": 210.5, "mem": 2 * 1024 ** 3, "created": 12345.0}]
