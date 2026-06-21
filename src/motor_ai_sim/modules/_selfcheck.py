"""Self-check for the full module catalog, on REAL data where light.

Run:  PYTHONPATH=src python -m motor_ai_sim.modules._selfcheck

Validates: the whole registry builds + dependency graph resolves; geometry-2d
passes the geometry conformance gate; the mesh module produces a real (coarse)
MeshIR; the cost module produces a real CostIR; the geometry.3d -> geometry.2d
dependency resolves and an unmet dependency raises. Heavy FEM solves are NOT run
here (the solver modules delegate to proven route functions).
"""
from __future__ import annotations

import sys
import traceback

PASS, FAIL = "PASS", "FAIL"
_results: list[tuple[str, str, str]] = []


def _check(name: str, fn) -> None:
    try:
        _results.append((PASS, name, str(fn() or "")))
    except Exception as e:  # noqa: BLE001
        _results.append((FAIL, name, f"{type(e).__name__}: {e}"))
        traceback.print_exc()


def _catalog() -> str:
    from motor_ai_sim.modules.bootstrap import default_registry
    reg = default_registry()                      # builds + check_dependencies()
    mans = reg.manifests()
    assert len(mans) >= 11, f"expected the full catalog, got {len(mans)}"
    for m in mans:
        assert m.contracts_version, f"{m.name} missing contracts_version"
        assert m.capability, f"{m.name} missing capability"
    caps = reg.capabilities()
    for need in ("geometry.2d", "geometry.3d", "mesh", "solver.em_static",
                 "solver.em_transient", "solver.thermal", "solver.mechanical",
                 "surrogate", "optimization", "cost", "users"):
        assert need in caps, f"missing capability {need}"
    return f"{len(mans)} modules; caps={len(caps)}; graph resolves"


def _geometry_conformance() -> str:
    from motor_ai_sim.contracts.conformance import assert_geometry_provider
    from motor_ai_sim.modules.bootstrap import default_registry
    gir = assert_geometry_provider(default_registry().provider("geometry.2d"), dim=2)
    return f"geometry.2d -> {gir.n_regions} regions, dim={gir.dim}"


def _mesh_real() -> str:
    from motor_ai_sim.modules.bootstrap import default_registry
    mesh_mod = default_registry().provider("mesh")
    mir = mesh_mod.run({"mesh_size_mm": 3.0})     # coarse + fast
    mir.check_consistent()
    assert mir.n_nodes > 10 and mir.n_cells > 10, "mesh too small"
    tags = set(int(t) for t in mir.cell_tags.tolist())
    return f"MeshIR {mir.n_nodes} nodes, {mir.n_cells} cells, {len(tags)} domain tags"


def _cost_real() -> str:
    from motor_ai_sim.contracts import CostIR
    from motor_ai_sim.modules.bootstrap import default_registry
    cost = default_registry().provider("cost")
    cir = cost.run({"masses_kg": {"copper": 0.061, "magnet": 0.030, "steel": 0.050, "shaft": 0.007}})
    assert isinstance(cir, CostIR) and cir.total > 0 and len(cir.lines) >= 4
    CostIR.model_validate_json(cir.model_dump_json())
    return f"CostIR total=${cir.total} over {len(cir.lines)} lines"


def _dependency_graph() -> str:
    from motor_ai_sim.contracts import CONTRACTS_VERSION
    from motor_ai_sim.modules.base import ModuleManifest
    from motor_ai_sim.modules.bootstrap import default_registry
    from motor_ai_sim.modules.registry import DependencyError
    reg = default_registry()
    g3 = reg.provider("geometry.3d").manifest()
    assert g3.depends_on == ["geometry.2d"], "geometry.3d must depend on geometry.2d"
    # negative control: unmet dependency must raise
    reg2 = default_registry()

    class _Orphan:
        def manifest(self):
            return ModuleManifest(name="orphan", capability="x.orphan",
                                  depends_on=["does.not.exist"], contracts_version=CONTRACTS_VERSION)
        def run(self, payload):
            raise NotImplementedError
    reg2.register(_Orphan())
    try:
        reg2.check_dependencies()
        raise AssertionError("expected DependencyError")
    except DependencyError:
        pass
    return "geometry.3d->geometry.2d resolves; unmet dep raises (fault-safe)"


def _ui_map() -> str:
    from motor_ai_sim.modules.bootstrap import default_registry
    with_ui = [m for m in default_registry().manifests() if m.ui]
    return f"{len(with_ui)} modules declare a UI panel (web-as-module)"


def main() -> int:
    from motor_ai_sim.contracts import CONTRACTS_VERSION
    print(f"modules + contracts v{CONTRACTS_VERSION} - full-catalog self-check\n")
    _check("registry builds (full catalog + graph)", _catalog)
    _check("geometry.2d conformance (real)", _geometry_conformance)
    _check("mesh -> MeshIR (real, coarse)", _mesh_real)
    _check("cost -> CostIR (real)", _cost_real)
    _check("dependency graph (2D->3D + unmet)", _dependency_graph)
    _check("UI map (web-as-module)", _ui_map)

    print()
    ok = True
    for status, name, detail in _results:
        print(f"  [{status}] {name:38s} {detail}")
        ok = ok and status == PASS
    print("\n" + ("ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
