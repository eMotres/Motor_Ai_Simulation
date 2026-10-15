import pytest
import sys
import os
from pathlib import Path

# Add scripts directory to path to import loadtest
scripts_dir = Path(__file__).parent.parent / "scripts" / "loadtest"
sys.path.insert(0, str(scripts_dir))

from loadtest import compute_stats, user_scenario, LoadTestMetrics

def test_dry_run_plan(capsys):
    metrics = LoadTestMetrics()
    import asyncio
    asyncio.run(user_scenario(0, "http://test", None, 1, False, metrics, True))
    
    out, err = capsys.readouterr()
    # Check that dry run prints only GET requests
    lines = out.strip().split("\n")
    for line in lines:
        assert "Plan: GET" in line
        assert "Plan: POST" not in line
        assert "Plan: PUT" not in line
        assert "Plan: DELETE" not in line
        assert "Plan: PATCH" not in line
        
    # Check specific paths
    assert "/api/me" in out
    assert "/api/catalog" in out
    assert "/api/catalog/MOTOR_ID/configure_context" in out
    assert "/api/model3d/models" in out
    assert "/api/propellers/APC_10x4.5MR/point?rpm=2000&temp_c=25" in out

def test_stats_computation():
    latencies = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
    stats = compute_stats(latencies)
    
    assert stats["count"] == 10
    assert stats["p50"] == 0.6
    assert stats["p95"] == 1.0
    
    # Test empty list
    empty_stats = compute_stats([])
    assert empty_stats["count"] == 0
    assert empty_stats["p50"] == 0.0

def test_arg_parsing(monkeypatch):
    import argparse
    from loadtest import main
    # test parsing works for a basic command
    test_args = ["loadtest.py", "--base-url", "http://foo", "--users", "10", "--dry-run"]
    monkeypatch.setattr(sys, 'argv', test_args)
    # the main function should run the loadtest. Because --dry-run is set, it will just plan.
    try:
        main()
    except Exception as e:
        pytest.fail(f"main() raised {e}")
