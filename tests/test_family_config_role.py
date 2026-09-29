"""A configuration's role is read off its DUTIES, not off a stale toggle.

User 2026-09-10: *"почему здесь motor, хотя это генератор"* — the catalog chip
said `motor` on a configuration named "L180 gen" whose every duty is a
generator duty.  `role` was captured once, at creation, from whatever the
Simulation panel's mode toggle happened to be, written into the yaml and never
looked at again.

The duties are the answer.  What is pinned here is that rule and its two edges:
mixed duties are SAID to be mixed rather than resolved by majority, and a
configuration with no duties yet falls back to the stored word, because there
the creation-time toggle is genuinely all there is.
"""
from __future__ import annotations

import pytest

from motor_ai_sim.routes.family import _config_role


def test_a_all_generator_duties_make_a_generator():
    c = {"role": "motor",
         "duties": [{"name": "rated", "mode": "generator"},
                    {"name": "peak", "mode": "generator"}]}
    assert _config_role(c) == ("generator", "duties")


def test_b_all_motor_duties_make_a_motor():
    c = {"role": "generator", "duties": [{"name": "rated", "mode": "motor"}]}
    assert _config_role(c) == ("motor", "duties")


def test_c_mixed_duties_are_said_to_be_mixed():
    """Not resolved by majority: a machine that both drives and generates is a
    real thing, and picking one of the two would hide it."""
    c = {"role": "motor",
         "duties": [{"name": "drive", "mode": "motor"},
                    {"name": "regen", "mode": "generator"},
                    {"name": "regen2", "mode": "generator"}]}
    assert _config_role(c) == ("mixed", "duties")


def test_d_with_no_duties_the_stored_word_stands():
    assert _config_role({"role": "generator"}) == ("generator", "stored")
    assert _config_role({"role": "generator", "duties": []}) == ("generator", "stored")


@pytest.mark.parametrize("stored", [None, "", "   ", "MOTOR", "nonsense"])
def test_e_a_missing_or_junk_stored_role_reads_as_motor(stored):
    """The old default, kept: a configuration with neither duties nor a usable
    stored word is a motor, which is what every caller assumed before."""
    c = {} if stored is None else {"role": stored}
    role, src = _config_role(c)
    assert src == "stored"
    assert role == "motor"


def test_f_a_duty_with_no_mode_does_not_vote():
    """A duty saved before `mode` existed says nothing about the role, and it
    must not turn a generator into a 'mixed' one by being blank."""
    c = {"role": "motor",
         "duties": [{"name": "old"},
                    {"name": "rated", "mode": "generator"},
                    {"name": "junk", "mode": "  "}]}
    assert _config_role(c) == ("generator", "duties")


def test_g_the_live_generator_configuration_reads_as_one():
    """The case that started it: the yaml still says `motor`, the duties do not."""
    import io
    import pathlib

    import yaml

    p = pathlib.Path("config/dies/CIANO10 200 opt/L180 gen.yaml")
    if not p.exists():
        pytest.skip("the live generator configuration is not in this checkout")
    c = yaml.safe_load(io.open(p, encoding="utf-8"))
    role, src = _config_role(c)
    assert (role, src) == ("generator", "duties"), (role, src, c.get("role"))
