"""CostIR — output of the cost module: a material + manufacturing cost breakdown."""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from .common import Provenance


class CostLine(BaseModel):
    item: str                                   # "copper" | "magnet" | "steel" | "shaft" | "labor" | ...
    mass_kg: Optional[float] = None
    unit_price_per_kg: Optional[float] = None
    cost: float = 0.0


class CostIR(BaseModel):
    model_config = ConfigDict(extra="forbid")

    currency: str = "USD"
    total: float = 0.0
    lines: List[CostLine] = Field(default_factory=list)
    provenance: Optional[Provenance] = None
