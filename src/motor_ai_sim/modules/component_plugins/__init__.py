# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Component Plugin-SDK - drop-in drive-train components (transmissions, loads).

Any .py file in this package that defines a class with a string CAPABILITY
starting with ``transmission.`` or ``load.`` plus manifest()/run() is imported by
``default_registry()``.  Unlike geometry plugins, a component is registered ONLY
if it passes its conformance gate (``assert_component_provider``): a module that
fails is skipped and listed in ``registry.rejected`` (and in ``GET /api/modules``
as ``rejected_components``) with the reason, so an author sees why it is missing.

Trust levels (``manifest.status``) are set by the maintainers, never by the
module's own claim alone: see docs/MODULE_CONTRIBUTION_RULES.md.  Components load
in-process, so only code that has been READ and merged by a maintainer can sit in
this folder; running unreviewed third-party code needs the isolated runner (see
docs/COMPONENT_SDK.md, "Where modules live").  Specification: docs/COMPONENT_SDK.md.
"""
from __future__ import annotations

from typing import Dict, List, Tuple


def discover_component_plugins() -> Tuple[List[object], List[Dict[str, str]]]:
    """Import every module in this package, instantiate each component class and
    run its conformance gate.  Returns ``(accepted, rejected)``; one broken
    plugin never breaks the registry."""
    import importlib
    import inspect
    import pkgutil

    from ...contracts.conformance import COMPONENT_PREFIXES, assert_component_provider

    accepted: List[object] = []
    rejected: List[Dict[str, str]] = []
    for info in pkgutil.iter_modules(__path__):
        if info.name.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f"{__name__}.{info.name}")
        except Exception as exc:                                    # noqa: BLE001
            rejected.append({"module": info.name, "capability": "?",
                             "reason": f"import failed: {type(exc).__name__}: {exc}"})
            continue
        for _, obj in inspect.getmembers(mod, inspect.isclass):
            if obj.__module__ != mod.__name__:
                continue
            cap = getattr(obj, "CAPABILITY", None)
            if not (isinstance(cap, str) and cap.startswith(COMPONENT_PREFIXES)
                    and hasattr(obj, "manifest") and hasattr(obj, "run")):
                continue
            try:
                inst = obj()
                assert_component_provider(inst)
                accepted.append(inst)
            except Exception as exc:                                # noqa: BLE001
                rejected.append({"module": f"{info.name}.{obj.__name__}", "capability": cap,
                                 "reason": f"{type(exc).__name__}: {exc}"})
    return accepted, rejected
