"""ModuleRegistry — discovery + capability routing + dependency resolution.

The kernel builds a registry, registers modules, and resolves a study DAG by
capability. `check_dependencies()` enforces that every module's `depends_on`
capabilities actually have a provider (so geometry-3d won't load unless a
geometry.2d provider is present).
"""
from __future__ import annotations

from typing import Dict, List

from .base import Module, ModuleManifest


class DependencyError(RuntimeError):
    """A module declares a depends_on capability that no registered module provides."""


class ModuleRegistry:
    def __init__(self) -> None:
        self._by_name: Dict[str, Module] = {}
        self._providers: Dict[str, List[Module]] = {}

    def register(self, module: Module) -> "ModuleRegistry":
        man = module.manifest()
        if man.name in self._by_name:
            raise ValueError(f"module {man.name!r} already registered")
        self._by_name[man.name] = module
        self._providers.setdefault(man.capability, []).append(module)
        return self

    def get(self, name: str) -> Module:
        return self._by_name[name]

    def providers(self, capability: str) -> List[Module]:
        """All modules that provide a capability (e.g. several geometry.2d implementations)."""
        return list(self._providers.get(capability, []))

    def provider(self, capability: str) -> Module:
        """The single provider of a capability (raises if 0 or >1 — for unambiguous wiring)."""
        ps = self.providers(capability)
        if len(ps) != 1:
            raise DependencyError(f"expected exactly 1 provider of {capability!r}, found {len(ps)}")
        return ps[0]

    def manifests(self) -> List[ModuleManifest]:
        return [m.manifest() for m in self._by_name.values()]

    def capabilities(self) -> List[str]:
        return sorted(self._providers)

    def check_dependencies(self) -> None:
        have = set(self._providers)
        for module in self._by_name.values():
            man = module.manifest()
            missing = [c for c in man.depends_on if c not in have]
            if missing:
                raise DependencyError(f"{man.name}: unmet depends_on {missing}")

    def pipeline_compatible(self, capabilities: List[str]) -> List[str]:
        """Check a capability pipeline by I/O ports: each stage's outputs should
        cover the next stage's declared inputs. Returns a list of mismatch
        warnings (empty == every interface lines up)."""
        warnings: List[str] = []
        chain = []
        for cap in capabilities:
            ps = self.providers(cap)
            if not ps:
                warnings.append(f"{cap}: no provider")
            else:
                chain.append((cap, ps[0].manifest()))
        for (c1, m1), (c2, m2) in zip(chain, chain[1:]):
            if m2.inputs and not (set(m1.outputs) & set(m2.inputs)):
                warnings.append(
                    f"{c1} outputs {m1.outputs} but {c2} needs {m2.inputs} — no shared type")
        return warnings
