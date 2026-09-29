"""Kernel — the orchestrator that runs portal modules via the registry.

This is the path the app migrates onto: instead of calling a solver function
directly, a request goes through the kernel, which resolves a capability to a
module and runs it with FAULT ISOLATION. Any error — a roadmap stub's
NotImplementedError, a solver crash, a missing provider — is caught and returned
as {ok: False, error}, so one failing module degrades a study instead of taking
down the system.

run_study(steps) chains capabilities into a pipeline, threading each step's
result to the next under payload['upstream'].

Hard timeouts + true process isolation come with the worker split (Phase 3);
today fault isolation is in-process try/except.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .bootstrap import default_registry
from .registry import DependencyError, ModuleRegistry


class Kernel:
    def __init__(self, registry: Optional[ModuleRegistry] = None) -> None:
        self.registry = registry or default_registry()

    def capabilities(self) -> List[str]:
        return self.registry.capabilities()

    def run(self, capability: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Run a single capability, fault-isolated. Never raises."""
        try:
            module = self.registry.provider(capability)
        except (DependencyError, KeyError) as e:
            return {"ok": False, "capability": capability, "error": f"no provider: {e}"}
        name = module.manifest().name
        try:
            result = module.run(payload or {})
            return {"ok": True, "capability": capability, "module": name, "result": result}
        except NotImplementedError as e:
            return {"ok": False, "capability": capability, "module": name,
                    "error": f"not implemented: {e}"}
        except Exception as e:  # noqa: BLE001 — fault isolation: never propagate
            return {"ok": False, "capability": capability, "module": name,
                    "error": f"{type(e).__name__}: {e}"}

    def run_study(self, steps: List[Tuple[str, Optional[Dict[str, Any]]]]) -> Dict[str, Any]:
        """Run a sequence of (capability, payload). Each successful result is made
        available to later steps under payload['upstream'][capability]. Failures
        are recorded, not raised (graceful degradation)."""
        results: Dict[str, Any] = {}
        upstream: Dict[str, Any] = {}
        for capability, payload in steps:
            pl = dict(payload or {})
            pl["upstream"] = dict(upstream)
            r = self.run(capability, pl)
            results[capability] = r
            if r.get("ok"):
                upstream[capability] = r.get("result")
        return {"steps": results, "ok": all(s.get("ok") for s in results.values())}
