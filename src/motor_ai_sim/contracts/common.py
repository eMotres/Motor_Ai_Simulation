"""Shared contract primitives used across all module IRs.

This module imports NOTHING from the rest of motor_ai_sim — the contracts
package is the dependency leaf, so every module (geometry, mesh, solvers,
surrogate, cost, …) can depend on it without creating cycles.
"""
from __future__ import annotations

from typing import Optional, Dict

from pydantic import BaseModel, Field


class Provenance(BaseModel):
    """Who produced an IR payload, from what, and how long it took.

    Stamped by the producing module so a downstream consumer (or a cache) can
    tell exactly which module/version/inputs a payload came from.
    """

    module: str                              # e.g. "geometry-aerostator"
    module_version: str = "0.0.0"
    contracts_version: str                   # the CONTRACTS_VERSION it was built against
    input_hash: Optional[str] = None         # hash of the upstream IR / parameter set
    elapsed_s: Optional[float] = None         # wall-clock the producer spent
    created_unix: Optional[float] = None      # producer-stamped timestamp (epoch s)
    notes: Dict[str, str] = Field(default_factory=dict)


class MaterialRef(BaseModel):
    """A reference to a material in the materials library.

    Geometry/mesh carry only the NAME (+ optional category); a module resolves it
    to real properties (B-H curve, k, ρ, …) so the library can evolve independently.
    """

    name: str
    category: Optional[str] = None           # steel | magnet | conductor | air | ...
