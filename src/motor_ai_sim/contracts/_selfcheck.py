"""Phase-0 self-check: prove the contracts + adapters work on REAL data, end to end.

Run:  PYTHONPATH=src python -m motor_ai_sim.contracts._selfcheck

It exercises the existing geometry -> (mesh) -> result chain and validates that
each stage maps cleanly onto GeometryIR / MeshIR / ResultIR. Nothing in the
existing pipeline is modified — this only READS from it. Exits non-zero on any
failure so it can gate CI later.
"""
from __future__ import annotations

import sys
import traceback

PASS, FAIL = "PASS", "FAIL"
_results: list[tuple[str, str, str]] = []


def _check(name: str, fn) -> None:
    try:
        detail = fn() or ""
        _results.append((PASS, name, str(detail)))
    except Exception as e:  # noqa: BLE001 — self-check reports, never raises
        _results.append((FAIL, name, f"{type(e).__name__}: {e}"))
        traceback.print_exc()


def _geometry() -> str:
    from motor_ai_sim.config import get_config
    from motor_ai_sim.cadquery_geometry import CadQueryMotor
    from motor_ai_sim.contracts import GeometryIR, RegionRole
    from motor_ai_sim.contracts.adapters import geometry_ir_from_polys, stamp

    cfg = get_config()
    geo = dict(cfg["geometry"])
    mats_cfg = cfg.get("materials", {}) or {}
    motor = CadQueryMotor()
    motor.set_parameters(geo)
    polys = motor.get_2d_polygons(0.0)

    passport: dict = {}
    try:
        from motor_ai_sim.services.geometry_service import get_current_geometry
        p = get_current_geometry(reload=True)
        for k in ("stator_outer_radius", "stator_inner_radius", "rotor_outer_radius",
                  "rotor_inner_radius", "shaft_radius", "num_slots", "num_poles"):
            if hasattr(p, k):
                passport[k] = getattr(p, k)
    except Exception:
        pass

    gir: GeometryIR = geometry_ir_from_polys(
        polys, passport=passport, parameters=geo,
        materials={"stator": mats_cfg.get("stator_core"),
                   "rotor": mats_cfg.get("rotor_core"),
                   "magnet": mats_cfg.get("magnet")},
        provenance=stamp("geometry-aerostator"),
    )
    assert gir.n_regions > 0, "no regions produced"
    roles = {r.role for r in gir.regions}
    for need in (RegionRole.STATOR, RegionRole.ROTOR, RegionRole.MAGNET, RegionRole.COIL):
        assert need in roles, f"missing role {need}"
    # full JSON round-trip (geometry is all small polygons → safe to serialise)
    js = gir.model_dump_json()
    GeometryIR.model_validate_json(js)
    n_mag = len(gir.regions_with_role(RegionRole.MAGNET))
    n_coil = len(gir.regions_with_role(RegionRole.COIL))
    return (f"{gir.n_regions} regions ({n_mag} magnets, {n_coil} coils), "
            f"slip_r={gir.symmetry.slip_radius_mm}, JSON {len(js)//1024} kB ok")


def _mesh() -> str:
    import numpy as np
    import skfem
    from motor_ai_sim.contracts import MeshIR
    from motor_ai_sim.contracts.adapters import mesh_ir_from_skfem, stamp

    m = skfem.MeshTri()  # unit-square reference mesh: real skfem MeshTri
    tags = np.ones(m.t.shape[1], dtype=int)
    mir: MeshIR = mesh_ir_from_skfem(m, tags, tag_names={1: "stator"},
                                     slip_radius_mm=12.5, provenance=stamp("mesh-skfem"))
    mir.check_consistent()
    assert mir.n_nodes == m.p.shape[1] and mir.n_cells == m.t.shape[1]
    assert mir.vertices.shape[1] == 2 and mir.triangles.shape[1] == 3
    return f"{mir.n_nodes} nodes, {mir.n_cells} cells, tags ok"


def _result() -> str:
    from motor_ai_sim.contracts import ResultIR
    from motor_ai_sim.contracts.adapters import result_ir_from_transient, stamp

    # Representative shape of the sliding-band transient dict (subset of real keys).
    sbres = {
        "time_s": [0.0, 1e-4, 2e-4],
        "T_em_Nm": [6.1, 6.3, 6.0],
        "T_em_filt_Nm": [6.13, 6.13, 6.13],
        "P_cu_W": [240.0, 240.0, 240.0],
        "T_avg_Nm": 6.13,
        "summary": {"P_stranded_W": 240.0, "P_core_W": 16.5, "efficiency": 0.943,
                    "V_phase_peak_V": 29.7, "mass_total_kg": 0.75, "T_ripple_pct": 3.6},
    }
    r: ResultIR = result_ir_from_transient(sbres, provenance=stamp("solver-em-transient"))
    assert r.ok and r.physics == "em_transient"
    assert abs(r.scalars.torque_Nm - 6.13) < 1e-9
    assert abs(r.scalars.efficiency - 0.943) < 1e-9
    assert r.series and len(r.series.time_s) == 3
    # graceful-failure payload (fault isolation)
    bad = ResultIR.failed("thermal", "solver crashed (SIGSEGV)")
    assert not bad.ok and bad.error
    return (f"torque={r.scalars.torque_Nm} Nm, eff={r.scalars.efficiency}, "
            f"series={len(r.series.time_s)} pts; failure-payload ok")


def main() -> int:
    from motor_ai_sim.contracts import CONTRACTS_VERSION
    print(f"contracts v{CONTRACTS_VERSION} - Phase-0 self-check\n")
    _check("import contracts", lambda: CONTRACTS_VERSION)
    _check("geometry -> GeometryIR (real config)", _geometry)
    _check("skfem mesh -> MeshIR", _mesh)
    _check("transient dict -> ResultIR", _result)

    print()
    ok = True
    for status, name, detail in _results:
        print(f"  [{status}] {name:42s} {detail}")
        ok = ok and status == PASS
    print("\n" + ("ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
