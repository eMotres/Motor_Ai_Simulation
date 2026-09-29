"""Conformance: any MaterialIR a materials module produces must satisfy these.

A materials resolver from any source (the YAML library today, a supplier BOM
service later) is acceptable iff it passes here — the invariants solvers and the
analytical path rely on when they read material properties through the contract.
"""
from __future__ import annotations

from typing import Any

from .. import MaterialIR


def assert_material_ir(mir: MaterialIR) -> None:
    assert isinstance(mir, MaterialIR), "must return a MaterialIR"
    assert mir.contracts_version, "MaterialIR missing contracts_version"
    MaterialIR.model_validate_json(mir.model_dump_json())          # JSON round-trips
    for name, props in mir.materials.items():
        assert props.name == name, f"materials[{name!r}] must be keyed by its own name"
        assert props.category, f"resolved material {name!r} missing category"
    # the library catalogue must list every resolved material under its category
    for name, props in mir.materials.items():
        assert name in mir.library.get(props.category, []), \
            f"{name!r} ({props.category}) not present in library catalogue"


def assert_materials_provider(provider: Any, *, payload: dict | None = None) -> MaterialIR:
    man = provider.manifest()
    assert man.capability == "materials", f"expected capability 'materials', got {man.capability!r}"
    assert "MaterialIR" in man.outputs, "a materials provider must declare MaterialIR as an output"
    mir = provider.run(payload or {})
    assert_material_ir(mir)
    return mir
