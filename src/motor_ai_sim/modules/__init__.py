"""modules — autonomous portal modules + the registry that wires them by capability.

Each module declares a ModuleManifest (capability, depends_on, ui) and runs
against contract IR. They never import each other; the registry resolves them by
capability so the physical layout (in-process today, isolated workers / separate
services later) can change without touching module code.
"""
from __future__ import annotations

from .base import Module, ModuleManifest, UIContribution
from .registry import DependencyError, ModuleRegistry

__all__ = [
    "Module", "ModuleManifest", "UIContribution",
    "ModuleRegistry", "DependencyError",
]
