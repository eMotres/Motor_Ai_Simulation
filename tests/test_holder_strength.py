"""Tests for holder strength (thrust, torque, press-fit preload) and CalculiX deck generation.

Covers:
- Front holder benchmark on CIANO14 40 prototype:
  * 49 N (5 kg) thrust -> ~28 MPa averaged, ~62 MPa peak local
  * Averaged safety factor SF = Sy / sigma_avg ~ 9.86
- Back holder benchmark on CIANO14 40 prototype:
  * 1.0 N*m torque + 49 N thrust -> ~77 MPa averaged
  * Averaged safety factor SF = Sy / sigma_avg ~ 3.58
- Project convention: every stress AVERAGED and SF = strength / averaged stress
- Combined load case with bearing press fit
- CalculiX INP deck generation & DAT output parser
- Fast CCX smoke test (if ccx.exe is installed locally)
- API routes: GET /api/mechanical/holder_strength and POST /api/mechanical/holder_fem_job
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import pytest
from fastapi.testclient import TestClient

from motor_ai_sim.api import app
from motor_ai_sim.holder_strength import (
    HolderStressResult,
    assess_holder_strength,
    calculate_back_holder_stress,
    calculate_front_holder_stress,
    generate_calculix_inp_text,
    parse_calculix_dat,
)

CCX_PATH = r"C:\Program Files\FreeCAD 1.0\bin\ccx.exe"


# ---------------------------------------------------------------------------
# 1. Front Holder Benchmarks
# ---------------------------------------------------------------------------

def test_front_holder_thrust_benchmark():
    """Front holder under 49.05 N thrust: ~28 MPa averaged, ~62 MPa peak."""
    res = calculate_front_holder_stress(
        thrust_n=49.05,
        motor_dia_mm=40.0,
        bearing_od_mm=16.0,
        bearing_bore_mm=8.0,
        wall_thickness_mm=1.2,
    )
    th = res["thrust_only"]
    assert th.sigma_avg_mpa == pytest.approx(28.0, abs=1.0)
    assert th.sigma_peak_mpa == pytest.approx(62.0, abs=2.0)
    # SF = Sy / sigma_avg (our project convention: every stress averaged)
    assert th.safety_factor == pytest.approx(276.0 / 28.0, rel=0.05)
    assert th.safety_factor > 9.0


# ---------------------------------------------------------------------------
# 2. Back Holder Benchmarks
# ---------------------------------------------------------------------------

def test_back_holder_thrust_and_torque_benchmark():
    """Back holder under 1.0 N*m torque + 49.05 N thrust: ~77 MPa averaged."""
    res = calculate_back_holder_stress(
        thrust_n=49.05,
        torque_nm=1.0,
        motor_dia_mm=40.0,
        bearing_od_mm=16.0,
        bearing_bore_mm=8.0,
        wall_thickness_mm=1.2,
    )
    op = res["thrust_torque"]
    assert op.sigma_avg_mpa == pytest.approx(77.0, abs=2.0)
    # SF = Sy / sigma_avg = 276 / 77 ~ 3.58
    assert op.safety_factor == pytest.approx(276.0 / 77.0, rel=0.05)
    assert 3.3 <= op.safety_factor <= 3.9


# ---------------------------------------------------------------------------
# 3. Project Convention: Every Stress Averaged
# ---------------------------------------------------------------------------

def test_averaged_stress_safety_factor_convention():
    """Convention: SF is strictly computed from AVERAGED stress, peak is singularity gauge."""
    res = calculate_front_holder_stress(thrust_n=49.05)
    th = res["thrust_only"]
    assert th.safety_factor == pytest.approx(th.yield_strength_mpa / th.sigma_avg_mpa, rel=1e-5)
    assert th.sf_peak == pytest.approx(th.yield_strength_mpa / th.sigma_peak_mpa, rel=1e-5)
    assert th.safety_factor > th.sf_peak


# ---------------------------------------------------------------------------
# 4. Combined Load Case with Press-Fit Preload
# ---------------------------------------------------------------------------

def test_combined_operational_and_press_fit_load_case():
    """Combined load case adds bearing press-fit contact pressure."""
    p_fit = 45.0  # MPa contact pressure from bearing fit
    res = calculate_front_holder_stress(thrust_n=49.05, press_fit_p_mpa=p_fit)
    comb = res["combined"]
    th = res["thrust_only"]
    # Combined stress is higher than thrust alone
    assert comb.sigma_avg_mpa > th.sigma_avg_mpa
    assert comb.press_fit_p_mpa == p_fit
    assert comb.safety_factor < th.safety_factor


# ---------------------------------------------------------------------------
# 5. CalculiX INP Deck Generation & DAT Parser
# ---------------------------------------------------------------------------

def test_calculix_deck_generation():
    """Verify CalculiX .inp file text generation."""
    nodes = {
        1: (0.0, 0.0, 0.0),
        2: (1.0, 0.0, 0.0),
        3: (0.0, 1.0, 0.0),
        4: (0.0, 0.0, 1.0),
        5: (0.5, 0.0, 0.0),
        6: (0.5, 0.5, 0.0),
        7: (0.0, 0.5, 0.0),
        8: (0.0, 0.0, 0.5),
        9: (0.5, 0.0, 0.5),
        10: (0.0, 0.5, 0.5),
    }
    tets = [[1, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]]
    fixed = [1, 2, 3]
    loads = {4: (0.0, 0.0, -100.0)}

    inp = generate_calculix_inp_text(
        name="test_deck",
        nodes=nodes,
        tets=tets,
        fixed_nodes=fixed,
        loads=loads,
        material_name="AL6061",
        youngs_modulus_mpa=68900.0,
        poisson_ratio=0.33,
    )
    assert "*ELEMENT, TYPE=C3D10" in inp
    assert "*MATERIAL, NAME=AL6061" in inp
    assert "*BOUNDARY" in inp
    assert "*CLOAD" in inp
    assert "*EL PRINT" in inp
    assert "4,3,-1.00000000e+02" in inp


def test_calculix_dat_parser():
    """Verify parser for CalculiX .dat output file."""
    sample_dat = """
    stresses (elem, integ.pnt., sxx, syy, szz, sxy, sxz, syz)
         1   1  1.000000e+01  2.000000e+01  3.000000e+01  0.000000e+00  0.000000e+00  0.000000e+00
         1   2  1.200000e+01  2.200000e+01  3.200000e+01  0.000000e+00  0.000000e+00  0.000000e+00

    displacements (node, u1, u2, u3)
         1  0.000000e+00  0.000000e+00  0.000000e+00
         4  1.200000e-03 -5.000000e-04  2.500000e-03
    """
    stresses, disps = parse_calculix_dat(sample_dat)
    assert 1 in stresses
    assert len(stresses[1]) == 2
    assert 4 in disps
    assert disps[4] == (0.0012, -0.0005, 0.0025)


# ---------------------------------------------------------------------------
# 6. Fast CalculiX Smoke Test (if CCX installed)
# ---------------------------------------------------------------------------

def test_calculix_smoke_run_if_available():
    """Run a tiny 1-element C3D10 solve (<1 s) if ccx.exe is installed."""
    if not os.path.isfile(CCX_PATH):
        pytest.skip("CalculiX ccx.exe not found at standard FreeCAD path")

    nodes = {
        1: (0.0, 0.0, 0.0),
        2: (2.0, 0.0, 0.0),
        3: (0.0, 2.0, 0.0),
        4: (0.0, 0.0, 2.0),
        5: (1.0, 0.0, 0.0),
        6: (1.0, 1.0, 0.0),
        7: (0.0, 1.0, 0.0),
        8: (0.0, 0.0, 1.0),
        9: (1.0, 0.0, 1.0),
        10: (0.0, 1.0, 1.0),
    }
    tets = [[1, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]]
    fixed = [1, 2, 3, 5, 6, 7]
    loads = {4: (0.0, 0.0, 50.0)}

    inp_text = generate_calculix_inp_text(
        name="smoke_test",
        nodes=nodes,
        tets=tets,
        fixed_nodes=fixed,
        loads=loads,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        inp_path = os.path.join(tmpdir, "smoke.inp")
        with open(inp_path, "w") as f:
            f.write(inp_text)

        # Run CCX in tmpdir
        proc = subprocess.run(
            [CCX_PATH, "-i", "smoke"],
            cwd=tmpdir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )
        assert proc.returncode == 0
        dat_path = os.path.join(tmpdir, "smoke.dat")
        assert os.path.isfile(dat_path)

        with open(dat_path) as f:
            dat_content = f.read()
        stresses, disps = parse_calculix_dat(dat_content)
        assert 1 in stresses
        assert len(stresses[1]) > 0
        assert any(v > 0 for v in stresses[1])


# ---------------------------------------------------------------------------
# 7. Routes: GET /api/mechanical/holder_strength & POST /api/mechanical/holder_fem_job
# ---------------------------------------------------------------------------

def test_api_holder_strength_endpoint():
    """Verify GET /api/mechanical/holder_strength route."""
    client = TestClient(app)
    resp = client.get("/api/mechanical/holder_strength?motor_dia=40&thrust_n=49.05&torque_nm=1.0")
    assert resp.status_code == 200
    data = resp.json()
    assert "front_holder" in data
    assert "back_holder" in data
    assert "summary" in data

    front_th = data["front_holder"]["thrust_only"]
    assert front_th["sigma_avg_mpa"] == pytest.approx(28.0, abs=1.5)
    assert front_th["safety_factor"] > 9.0

    back_op = data["back_holder"]["thrust_torque"]
    assert back_op["sigma_avg_mpa"] == pytest.approx(77.0, abs=3.0)
    assert 3.2 <= back_op["safety_factor"] <= 4.0


def test_api_holder_fem_job_endpoint():
    """Verify POST /api/mechanical/holder_fem_job route."""
    client = TestClient(app)
    resp = client.post("/api/mechanical/holder_fem_job?holder_type=front&thrust_n=49.05")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "queued"
    assert data["holder_type"] == "front"
