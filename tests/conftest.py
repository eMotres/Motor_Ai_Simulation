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
import re
import shutil
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_REAL_CONFIG = _ROOT / "config" / "motor_config.yaml"
_REAL_PRESETS = _ROOT / "config" / "motor_presets.json"

_SANDBOX = Path(tempfile.mkdtemp(prefix="motor_ai_sim_tests_"))


def _zero_the_sleeve(path: Path) -> None:
    """Sandbox only: force ``geometry.sleeve_thickness`` to 0.

    Fixtures across the suite pin their machine field by field but do NOT list
    every geometry key; ``CadQueryMotor._map_api_to_cadquery`` fills the gaps
    from the LOADED config, so an unpinned key is silently the user's.  That is
    the F2 leak the physics-regression docstring tells at length — and on
    2026-09-04 it bit through a key no fixture had ever heard of: the live
    machine grew ``sleeve_thickness: 1`` (in a 1.6 mm gap, legal there), and
    every fixture running a 0.2 / 0.65 mm air gap inherited a retaining ring
    thicker than its own gap.  ~19 tests went red at once — geometry validation
    refusing designs that are fine, and the d-axis calibration finding no psi
    maximum in a machine whose rotor reaches the stator.

    Pinning the key in each fixture would fix today's break and buy nothing:
    the NEXT geometry key the product grows would leak exactly the same way.
    Zeroing it once, here, makes "no sleeve" the sandbox default for the whole
    suite, so a fixture that wants a ring has to ASK for one — tests/test_sleeve.py
    passes ``sleeve_thickness=`` explicitly on every sleeved case (and asserts
    that omitting it builds no ring), which is exactly that opt-in.

    WIRE_PARALLEL, the same leak one key over (2026-09-09).  The live machine
    grew ``wire_parallel: 4`` beside ``num_wires_per_slot: 24``, which is a legal
    winding — four strands in hand, six turns.  Every fixture that pins its turn
    count but not its strand count then inherited the 4: the 30 mm fixture winds
    6 wires per slot, 6 is not divisible by 4, and ``wire_parallel_from_geo``
    refuses by design ("1.5 turns per coil is not a winding").  The whole thermal
    suite, the report suite and everything downstream of ``store_em_run`` errored
    out before solving anything.  ``tests/test_physics_regression.py`` pinned the
    key in ITS OWN dict after the same thing happened on 2026-09-03; pinning it
    fixture by fixture fixes today's break and buys nothing, so — exactly like
    the sleeve above — "one wire in hand" becomes the sandbox default and a
    fixture that wants strands in hand asks for them (tests/test_wire_parallel.py
    passes ``wire_parallel=`` on every case, and asserts that ``GEO_30MM`` alone
    reads as 1).

    Line-level on purpose: a yaml round-trip would rewrite the sandbox copy and
    throw away the comments and ordering that some tests read the file for.
    """
    try:
        txt = path.read_text(encoding="utf-8")
    except Exception:
        return
    # Only a scalar `<key>: <number>` — never the geometry_schema block, where
    # the same names introduce nested min/max mappings.
    n_tot = 0
    for key, value in (("sleeve_thickness", "0"), ("wire_parallel", "1")):
        txt, n = re.subn(r"(?m)^(\s*%s:[ \t]*)[-+0-9.eE]+[ \t]*$" % key,
                         r"\g<1>" + value, txt, count=1)
        n_tot += n
    if n_tot:
        path.write_text(txt, encoding="utf-8")


for _env, _real, _name in (("MOTOR_AI_SIM_CONFIG", _REAL_CONFIG, "motor_config.yaml"),
                           ("MOTOR_AI_SIM_PRESETS", _REAL_PRESETS, "motor_presets.json")):
    if os.environ.get(_env):
        continue                      # an explicit override wins (CI, debugging)
    _copy = _SANDBOX / _name
    if _real.exists():
        shutil.copy2(_real, _copy)
        if _env == "MOTOR_AI_SIM_CONFIG":
            _zero_the_sleeve(_copy)
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
