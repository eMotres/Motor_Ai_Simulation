"""Phase-1 self-check: the geometry-2d module + registry + conformance, on REAL data.

Run:  PYTHONPATH=src python -m motor_ai_sim.modules._selfcheck

Proves: the registry wires modules by capability; the real geometry-2d provider
passes the geometry conformance gate; and a (stub) geometry-3d that
depends_on ["geometry.2d"] resolves — i.e. the 2D->3D dependency design holds.
Nothing in the existing pipeline is touched.
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


def _registry_and_conformance() -> str:
    from motor_ai_sim.contracts.conformance import assert_geometry_provider
    from motor_ai_sim.modules.bootstrap import default_registry

    reg = default_registry()
    assert "geometry.2d" in reg.capabilities(), "geometry.2d not registered"
    geo = reg.provider("geometry.2d")
    gir = assert_geometry_provider(geo, dim=2)   # real build + full conformance gate
    return (f"{len(reg.manifests())} module(s); geometry.2d -> {gir.n_regions} regions, "
            f"dim={gir.dim}, contracts {geo.manifest().contracts_version}")


def _dependency_resolution() -> str:
    from motor_ai_sim.contracts import CONTRACTS_VERSION
    from motor_ai_sim.modules.base import ModuleManifest
    from motor_ai_sim.modules.bootstrap import default_registry
    from motor_ai_sim.modules.registry import DependencyError

    reg = default_registry()

    class _Geometry3DStub:
        def manifest(self) -> ModuleManifest:
            return ModuleManifest(
                name="geometry-3d-aerostator", capability="geometry.3d",
                depends_on=["geometry.2d"], contracts_version=CONTRACTS_VERSION,
                summary="future: extrude/revolve geometry.2d regions -> dim=3")

        def run(self, payload):  # not built yet
            raise NotImplementedError

    reg.register(_Geometry3DStub())
    reg.check_dependencies()             # geometry.3d depends_on geometry.2d -> resolves

    # negative control: an unmet dependency must raise
    reg2 = default_registry()

    class _Orphan:
        def manifest(self) -> ModuleManifest:
            return ModuleManifest(name="needs-nothing-real", capability="mech",
                                  depends_on=["does.not.exist"], contracts_version=CONTRACTS_VERSION)

        def run(self, payload):
            raise NotImplementedError

    reg2.register(_Orphan())
    try:
        reg2.check_dependencies()
        raise AssertionError("expected DependencyError for unmet depends_on")
    except DependencyError:
        pass
    return "geometry.3d depends_on geometry.2d resolves; unmet dep raises (fault-safe)"


def _ui_manifest() -> str:
    from motor_ai_sim.modules.bootstrap import default_registry
    reg = default_registry()
    ui = reg.provider("geometry.2d").manifest().ui
    assert ui and ui.panel_id == "geometry" and ui.frontend_module, "geometry module must declare a UI panel"
    return f"web-as-module: panel '{ui.panel_id}' -> {ui.frontend_module}"


def main() -> int:
    from motor_ai_sim.contracts import CONTRACTS_VERSION
    print(f"modules + contracts v{CONTRACTS_VERSION} - Phase-1 self-check\n")
    _check("registry + geometry.2d conformance (real)", _registry_and_conformance)
    _check("2D->3D dependency resolution", _dependency_resolution)
    _check("UI contribution (web-as-module)", _ui_manifest)

    print()
    ok = True
    for status, name, detail in _results:
        print(f"  [{status}] {name:42s} {detail}")
        ok = ok and status == PASS
    print("\n" + ("ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
