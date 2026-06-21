"""ResultIR — the output of any solver module.

Every solver (EM static/transient, thermal, mechanical) returns the SAME shape:
scalars (always), plus optional time-series and on-mesh fields. The orchestrator
merges per-physics ResultIRs into a study result, so adding a new physics never
changes the contract.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from .common import Provenance


class ScalarResults(BaseModel):
    """Operating-point / cycle scalars. Known fields are explicit (unit in the
    name); a module may attach more via extra='allow' without a contract bump."""

    model_config = ConfigDict(extra="allow")

    torque_Nm: Optional[float] = None
    torque_ripple_pct: Optional[float] = None
    p_copper_W: Optional[float] = None
    p_iron_W: Optional[float] = None
    p_magnet_eddy_W: Optional[float] = None
    p_shaft_eddy_W: Optional[float] = None
    p_loss_total_W: Optional[float] = None
    p_mech_W: Optional[float] = None
    efficiency: Optional[float] = None          # 0..1
    v_phase_peak_V: Optional[float] = None
    mass_kg: Optional[float] = None
    t_max_C: Optional[float] = None             # thermal
    stress_max_MPa: Optional[float] = None      # mechanical


class SeriesResults(BaseModel):
    """Time-resolved waveforms over one electrical period (equal-length lists)."""

    model_config = ConfigDict(extra="allow")

    time_s: List[float] = Field(default_factory=list)
    torque_Nm: List[float] = Field(default_factory=list)
    # other named series (p_copper_W, v_a_V, …) so the contract stays small:
    extra: Dict[str, List[float]] = Field(default_factory=dict)


class FieldArray(BaseModel):
    """A named field sampled on the mesh (per node or per cell)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    location: str                               # "node" | "cell"
    values: Any                                 # float ndarray
    unit: Optional[str] = None


class FieldResults(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    mesh: Any                                   # the MeshIR the fields live on
    arrays: List[FieldArray] = Field(default_factory=list)


class ResultIR(BaseModel):
    """One solver's output. ok=False + error carries a graceful failure so a
    crashed/timed-out module degrades the study instead of breaking it."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    physics: str                                # "em_static" | "em_transient" | "thermal" | "mechanical"
    ok: bool = True
    error: Optional[str] = None
    scalars: ScalarResults = Field(default_factory=ScalarResults)
    series: Optional[SeriesResults] = None
    fields: Optional[FieldResults] = None
    provenance: Optional[Provenance] = None

    @classmethod
    def failed(cls, physics: str, error: str, provenance: Optional[Provenance] = None) -> "ResultIR":
        """Construct a graceful-failure result (the fault-isolation payload)."""
        return cls(physics=physics, ok=False, error=error, provenance=provenance)
