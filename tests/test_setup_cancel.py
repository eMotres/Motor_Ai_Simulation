"""Stop during the solver's SET-UP, before the first time step.

The Stop button is honoured by the progress callback raising (the route's
``_RunCancelled``, a BaseException).  ``fem_transient_sliding_band`` used to call
that callback for the first time at frame 0, so everything before it — the CAD
polygons and the mesh, the per-tag assembly, the sliding-band projections, the
phasor initialiser's Picard, the static start field — ran deaf to it: up to
~30 s on a large machine.  Every set-up stage now starts with a checkpoint that
calls the callback with ``done = total = None`` ("keep what the bar shows").

These run the real solver on the 30 mm regression fixture and stop it at named
stages; the stage name is read off the checkpoint's own DEBUG line.
"""
from __future__ import annotations

import logging
import time

import pytest

from motor_ai_sim.material_context import set_request_materials
from motor_ai_sim.simulation.fem_solver_2d import fem_transient_sliding_band

from tests.test_physics_regression import (COMMON, CONNECTION, GEO_30MM,
                                           OVERRIDE, RPM)

_LOGGER = "motor_ai_sim.simulation.fem_solver_2d"
_PREFIX = "set-up checkpoint: "


class _Stop(BaseException):
    """What the route raises: a BaseException, like KeyboardInterrupt."""


class _Recorder(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.stages = []

    def emit(self, record):
        msg = record.getMessage()
        if msg.startswith(_PREFIX):
            self.stages.append(msg[len(_PREFIX):].rsplit(" (", 1)[0])


def _run(stop_at, **over):
    """Solve the fixture, raising _Stop at the checkpoint named ``stop_at``
    (None: never).  Returns (raised, stages seen, frame calls, seconds)."""
    log = logging.getLogger(_LOGGER)
    rec = _Recorder()
    old = log.level
    log.addHandler(rec)
    log.setLevel(logging.DEBUG)
    frames = []

    def cb(done=None, total=None, phase=None, composition=None):
        if done is None and total is None:
            # the d-axis calibration is a nested solve with checkpoints of its
            # own; past "start", aim at the run's OWN stages
            own = (stop_at == "start"
                   or "d-axis reference resolved" in rec.stages)
            if (stop_at is not None and own and rec.stages
                    and rec.stages[-1] == stop_at):
                raise _Stop()
            return
        # a SOLVED frame of the run itself (the d-axis calibration's nested
        # solve counts its own frames under its own label)
        if not str(phase or "").startswith("calibrating"):
            frames.append((done, total, phase))

    kw = dict(COMMON, element_order=2, I_phase_rms=60.0, gamma_deg=0.0,
              demag=False)
    kw.update(over)
    set_request_materials(OVERRIDE)
    t0 = time.time()
    raised = False
    try:
        fem_transient_sliding_band(geo_override=dict(GEO_30MM), rpm=RPM,
                                   connection=CONNECTION, progress_cb=cb, **kw)
    except _Stop:
        raised = True
    finally:
        set_request_materials(None)
        log.removeHandler(rec)
        log.setLevel(old)
    return raised, rec.stages, frames, time.time() - t0


def test_a_stop_pressed_before_anything_is_built_is_honoured_at_once():
    raised, stages, frames, secs = _run("start")
    assert raised
    assert stages == ["start"]            # nothing after it ran
    assert frames == []
    assert secs < 5.0


# The stages a voltage run passes before frame 0, in order.  The phasor
# initialiser checks on EVERY sweep (one factorisation + solve each).
_VOLTAGE_STAGES = [
    "d-axis reference resolved", "CAD polygons", "mesh",
    "materials and per-tag assembly", "eddy conductor data",
    "warm-start and demag seeds",
    "P2 sliding band: stitching and projections",
    "P2 dof maps and sources", "P2 flux linkage and circuit",
    "phasor initialiser, sweep 0", "first time step",
]


@pytest.mark.parametrize("stop_at", ["mesh",
                                     "P2 sliding band: stitching and projections",
                                     "phasor initialiser, sweep 0",
                                     "first time step"])
def test_a_stop_during_set_up_is_honoured_before_frame_0(stop_at):
    """Voltage drive: the settle is long, so a set-up that ignored Stop was
    followed by a whole march that did not."""
    raised, stages, frames, _ = _run(stop_at, drive="voltage",
                                     v_phase_peak=7.0, v_delta_deg=10.0)
    assert raised, "the Stop at %r was not honoured" % stop_at
    assert stages[-1] == stop_at
    assert frames == [], "a frame was solved after Stop: %r" % frames[:3]


def test_every_set_up_stage_is_a_checkpoint_in_order():
    raised, stages, frames, _ = _run("first time step", drive="voltage",
                                     v_phase_peak=7.0, v_delta_deg=10.0)
    assert raised and frames == []
    main = stages[stages.index("d-axis reference resolved"):]
    got = [s for s in main if s in _VOLTAGE_STAGES]
    assert got == _VOLTAGE_STAGES, got
    # every phasor sweep is its own checkpoint
    assert sum(s.startswith("phasor initialiser, sweep") for s in main) >= 2


def test_the_static_start_field_is_a_checkpoint_on_the_eddy_path():
    """Current drive with the coupled eddy solve: the cold start solves the
    ∂A/∂t = 0 field before frame 0 — a Stop there must not wait for it."""
    raised, stages, frames, _ = _run("static start field", eddy=True,
                                     rotor_eddy=True)
    assert raised
    assert stages[-1] == "static start field"
    assert frames == []


def test_without_a_stop_the_checkpoints_change_nothing():
    """The checkpoints keep the bar (done = total = None) and a callback that
    never raises sees the ordinary per-frame calls after them."""
    raised, stages, frames, _ = _run(None, n_steps_per_period=4)
    assert not raised
    assert "first time step" in stages
    assert frames and all(isinstance(f[0], int) for f in frames)
