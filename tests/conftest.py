"""Test session isolation — the suite must never touch the loaded machine.

Most of these tests drive the REAL API (``client.put("/api/geometry", ...)``,
``POST /api/presets``), and those endpoints write ``config/motor_config.yaml``
and ``config/motor_presets.json`` — the two files that decide which motor the
user has open.  On 2026-08-06 a test run replaced a live 150 mm CIANO28 with the
30 mm fixture mid-session, and the next save stored the fixture under the user's
motor name; the day's optimization was recovered from a stale copy.

So before ``motor_ai_sim`` is imported at all, both stores are redirected to
throwaway copies inside the pytest tmp area.  The copies start as the real
files, so tests that expect a sane starting machine still get one — they just
cannot write back.  ``MOTOR_AI_SIM_CONFIG`` / ``MOTOR_AI_SIM_PRESETS`` are read
once at import time by ``config.py`` and ``routes/presets.py``, which is why
this happens here and not in a fixture.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_REAL_CONFIG = _ROOT / "config" / "motor_config.yaml"
_REAL_PRESETS = _ROOT / "config" / "motor_presets.json"

_SANDBOX = Path(tempfile.mkdtemp(prefix="motor_ai_sim_tests_"))

for _env, _real, _name in (("MOTOR_AI_SIM_CONFIG", _REAL_CONFIG, "motor_config.yaml"),
                           ("MOTOR_AI_SIM_PRESETS", _REAL_PRESETS, "motor_presets.json")):
    if os.environ.get(_env):
        continue                      # an explicit override wins (CI, debugging)
    _copy = _SANDBOX / _name
    if _real.exists():
        shutil.copy2(_real, _copy)
    os.environ[_env] = str(_copy)

import pytest                                             # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _assert_the_live_config_is_untouched():
    """Fail the session if a test wrote the user's config anyway — a redirect
    that quietly stops working is worse than none."""
    before = _REAL_CONFIG.read_bytes() if _REAL_CONFIG.exists() else None
    yield
    after = _REAL_CONFIG.read_bytes() if _REAL_CONFIG.exists() else None
    assert before == after, (
        "config/motor_config.yaml changed during the test session — either a "
        "test bypassed MOTOR_AI_SIM_CONFIG and wrote the machine the user has "
        "loaded, or something OUTSIDE the suite (the running backend, a PATCH "
        "from the UI) edited it while the run was in flight.  Both are worth "
        "knowing; check the audit trail in logs/geometry_audit.jsonl and rerun "
        "with the app idle before treating it as a test bug.")
