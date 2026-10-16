"""Geometry Plugin-SDK — drop-in external geometry providers.

Any .py file in this package that defines a class with a string CAPABILITY
starting with "geometry." plus manifest()/run() is auto-discovered and registered
by the kernel's default_registry(). No core edit needed to add a new topology —
that is the whole point of the SDK (see docs/PLUGIN_SDK.md).

A plugin MUST pass motor_ai_sim.contracts.conformance.assert_geometry_provider:
its run() returns a GeometryIR with the required 2D roles (stator/rotor/magnet/
coil) that JSON-round-trips. Downstream modules (mesh, cost, solvers) then accept
it unchanged because they speak only the GeometryIR contract.
"""
from __future__ import annotations

from typing import List


def discover_geometry_plugins() -> List[object]:
    """Import every module in this package and instantiate each geometry-provider
    class found (CAPABILITY startswith 'geometry.'). Failures in one plugin are
    isolated — a broken plugin is skipped, not fatal to the registry."""
    import importlib
    import inspect
    import pkgutil

    out: List[object] = []
    for info in pkgutil.iter_modules(__path__):
        if info.name.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f"{__name__}.{info.name}")
        except Exception:  # noqa: BLE001 — one bad plugin must not break the catalog
            continue
        for _, obj in inspect.getmembers(mod, inspect.isclass):
            if obj.__module__ != mod.__name__:
                continue  # only classes defined in this plugin file
            cap = getattr(obj, "CAPABILITY", None)
            if isinstance(cap, str) and cap.startswith("geometry.") \
                    and hasattr(obj, "manifest") and hasattr(obj, "run"):
                try:
                    out.append(obj())
                except Exception:  # noqa: BLE001
                    continue
    return out
