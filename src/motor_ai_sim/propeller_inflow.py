"""One interface for "what air does this propeller push at the motor".

WHY
===
``propeller.py`` answers with ONE number: the momentum-theory wake speed times a
position factor.  The blade-element model that follows (``propeller_bemt``) gives a
radial profile with swirl and handles forward flight.  Both must be reachable
through the same call, so the thermal side and the API can ask for "the air
field" without knowing which model made it, and so the model that produced a
number is always named.

``momentum-1`` is the model every stored result so far was made with.  It is a
thin wrapper: every number is computed by the unchanged functions of
``propeller.py``, so it is bit-identical to calling them directly
(``tests/test_propeller_inflow.py`` pins exact values).  This module changes no
existing function, no response and no stored number.

WHAT A MODEL RETURNS (``AirField``)
===================================
* ``thrust_N`` / ``torque_Nm`` / ``shaft_power_W`` of the propeller at the point;
* ``bands``: the axial and swirl velocity [m/s] by radius.  ``momentum-1`` has no
  radial profile and no swirl: it returns ONE band covering the requested radius
  range with ``swirl_ms = None`` and says so in ``flags`` (``uniform_profile``,
  ``swirl_not_modelled``) — a missing quantity is ``None``, never 0;
* ``air_speed_ms``: the single number the thermal model uses today;
* ``model_id`` and ``assumptions``: the model and what it assumed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Protocol, Tuple

from motor_ai_sim import propeller as _pp

MODEL_MOMENTUM = "momentum-1"


class InflowModelError(_pp.PropellerError, ValueError):
    """The model cannot answer this request (unknown model, unsupported condition)."""


@dataclass(frozen=True)
class InflowRequest:
    """One operating point.

    ``r_min_m`` / ``r_max_m``: the radius band [m] the caller needs the air in
    (the motor body); ``None`` = the model's default band.  ``v_inf_ms``: free-stream
    speed along the axis (climb / forward flight), 0 = static.
    """

    propeller: _pp.Propeller
    rpm: float
    rho: float = _pp.RHO_ISA
    v_inf_ms: float = 0.0
    r_min_m: Optional[float] = None
    r_max_m: Optional[float] = None
    air_temp_c: Optional[float] = None  # blade-element model: air viscosity and sound speed (default 15 C)
    position: Optional[str] = None      # momentum-1 only
    factor: Optional[float] = None      # momentum-1 only (overrides position)


@dataclass(frozen=True)
class Band:
    """Air velocity over a radius band; ``None`` = not modelled."""

    r_min_m: Optional[float]
    r_max_m: Optional[float]
    axial_ms: Optional[float]
    swirl_ms: Optional[float]


@dataclass(frozen=True)
class AirField:
    model_id: str
    propeller_id: str
    rpm: float
    rho_kg_m3: float
    thrust_N: float
    torque_Nm: float
    shaft_power_W: float
    wake_velocity_ms: float
    air_speed_ms: Optional[float]      # None = the model has no value for the requested band
    bands: Tuple[Band, ...]
    flags: Tuple[str, ...] = ()
    assumptions: Tuple[str, ...] = ()
    extra: Dict[str, object] = field(default_factory=dict)


class InflowModel(Protocol):
    model_id: str

    def evaluate(self, req: InflowRequest) -> AirField: ...


class MomentumInflow:
    """Static momentum theory x position factor — the model of all stored results."""

    model_id = MODEL_MOMENTUM

    def evaluate(self, req: InflowRequest) -> AirField:
        if float(req.v_inf_ms) != 0.0:
            raise InflowModelError(
                "%s is a static (hover) model: v_inf_ms must be 0, got %g" % (MODEL_MOMENTUM, req.v_inf_ms))
        op = _pp.operating_point(req.propeller, req.rpm, req.rho,
                                 position=req.position, factor=req.factor)
        band = Band(req.r_min_m, req.r_max_m, op["air_speed_ms"], None)
        return AirField(
            model_id=self.model_id, propeller_id=op["propeller_id"], rpm=op["rpm"],
            rho_kg_m3=op["rho_kg_m3"], thrust_N=op["thrust_N"], torque_Nm=op["torque_Nm"],
            shaft_power_W=op["shaft_power_W"], wake_velocity_ms=op["wake_velocity_ms"],
            air_speed_ms=op["air_speed_ms"], bands=(band,),
            flags=("uniform_profile", "swirl_not_modelled")
                  + (("extrapolated",) if op["extrapolated"] else ())
                  + (("power_estimated",) if op["power_estimated"] else ()),
            assumptions=tuple(op["assumptions"]),
            extra={"slipstream_position": op["slipstream_position"],
                   "slipstream_factor": op["slipstream_factor"]})


_MODELS: Dict[str, InflowModel] = {MODEL_MOMENTUM: MomentumInflow()}


def register_model(model: InflowModel) -> None:
    """Add a model under its ``model_id`` (a later stage registers the blade-element one)."""
    _MODELS[str(model.model_id)] = model


def available_models() -> List[str]:
    _load_optional_models()
    return sorted(_MODELS)


def _load_optional_models() -> None:
    """Import the models that register themselves on import (kept out of the module top: no import cycle)."""
    try:
        from motor_ai_sim import propeller_bemt  # noqa: F401
    except ImportError:                                    # numpy/scipy missing: the model is simply not offered
        pass


def get_model(model_id: str = MODEL_MOMENTUM) -> InflowModel:
    _load_optional_models()
    m = _MODELS.get(str(model_id))
    if m is None:
        raise InflowModelError("unknown inflow model %r; available: %s" % (model_id, ", ".join(available_models())))
    return m


def air_field(propeller_id: str, rpm: float, rho: float = _pp.RHO_ISA, *,
              model_id: str = MODEL_MOMENTUM, **kw) -> AirField:
    """Convenience: look the propeller up in the catalogue and evaluate ``model_id``."""
    return get_model(model_id).evaluate(
        InflowRequest(propeller=_pp.get_propeller(propeller_id), rpm=float(rpm), rho=float(rho), **kw))
