"""One torque factor for the coupled hold and the tiles (report of 2026-11-25): the hold / mechanical step / propeller load line used
``end3d.k_flux`` alone while the tiles, the S1 blocks and the report use the measured k_T else k_flux.  And the S1 / continuous
``power`` blocks read ``P_mech_W`` from ``report.shaft_view``, which never returns it."""
from __future__ import annotations

import math
import re
from pathlib import Path

import pytest

from motor_ai_sim import report
from motor_ai_sim.routes import coupled


def _old_shaft_torque_nm(summary):
    """The function as it was (k_flux alone, any finite k > 0): the reference of the bit-identity check."""
    t = summary.get("T_em_avg_Nm")
    try:
        t = None if t is None else float(t)
    except (TypeError, ValueError):
        t = None
    if t is None or not math.isfinite(t):
        return None, None
    e3 = summary.get("end3d")
    k = (e3 or {}).get("k_flux") if isinstance(e3, dict) else None
    try:
        k = None if k is None else float(k)
    except (TypeError, ValueError):
        k = None
    if k is not None and math.isfinite(k) and k > 0.0 and abs(k - 1.0) > 1e-9:
        return t * k, ("2-D mean %.4g N·m × 3-D end-effect %.4g" % (t, k))
    return t, None


def test_without_a_measured_k_t_the_hold_is_bit_identical_to_before():
    for t in (0.0, 0.623, 5.618, 12.345678, -3.2):
        for e3 in (None, {}, {"k_flux": 0.9226}, {"k_flux": 0.947, "source": "passport"}, {"k_flux": 1.0}, {"k_flux": 0.9637, "k_torque": 0.9637, "torque_basis": "flux"},
                   {"k_flux": 1.012}):
            s = {"T_em_avg_Nm": t, "end3d": e3}
            assert coupled._shaft_torque_nm(s) == _old_shaft_torque_nm(s), (t, e3)      # == on floats and notes: bit for bit
    for s in ({}, {"T_em_avg_Nm": None}, {"T_em_avg_Nm": "nan"}, {"T_em_avg_Nm": 0.6, "end3d": "x"}):
        assert coupled._shaft_torque_nm(s) == _old_shaft_torque_nm(s)


def test_a_measured_k_t_is_the_hold_s_factor_too_and_the_tiles_agree():
    s = {"T_em_avg_Nm": 5.618, "end3d": {"k_flux": 0.9226, "k_T": 0.95}}
    t, note = coupled._shaft_torque_nm(s)
    assert t == pytest.approx(5.618 * 0.95) and "0.95" in note
    assert coupled._torque_factor_k(s) == report.torque_factor_3d(s) == pytest.approx(0.95)
    assert report.shaft_view({"P_mech_W": 1000.0, "P_loss_total_W": 100.0, **s}, None, "motor")["k"] == coupled._torque_factor_k(s)
    # the passport enrichment spells the measured factor k_torque + torque_basis
    s2 = {"T_em_avg_Nm": 2.0, "end3d": {"k_flux": 0.9, "k_torque": 0.97, "torque_basis": "3-D"}}
    assert coupled._torque_factor_k(s2) == pytest.approx(0.97) == report.torque_factor_3d(s2)


def test_a_factor_outside_the_accepted_band_is_refused_like_the_tiles_do():
    s = {"T_em_avg_Nm": 1.0, "end3d": {"k_flux": 0.3}}
    assert coupled._shaft_torque_nm(s) == (1.0, None) and coupled._torque_factor_k(s) is None and report.torque_factor_3d(s) is None


def test_the_propeller_load_line_reads_the_same_factor():
    src = Path(coupled.__file__).read_bytes().decode("utf-8")
    assert "k3d = _torque_factor_k(summary) or 1.0" in src.replace("\r\n", "\n")
    assert '.get("k_flux")) or 1.0' not in src


def test_shaft_view_has_no_p_mech_key_and_the_power_blocks_read_p_rotor():
    sv = report.shaft_view({"P_mech_W": 1000.0, "P_loss_total_W": 100.0, "end3d": {"k_flux": 0.9}}, None, "motor")
    assert "P_mech_W" not in sv and sv["P_rotor_W"] == pytest.approx(900.0)
    src = Path(coupled.__file__).read_bytes().decode("utf-8").replace("\r\n", "\n")
    assert '"P_mech_W": _sv_v.get("P_rotor_W")' in src and '"P_mech_W": sv.get("P_rotor_W")' in src
    assert not re.search(r'(_sv_v|\bsv)\.get\("P_mech_W"\)', src)
