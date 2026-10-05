"""Propeller catalogue routes — /api/propellers (read-only).

    GET /api/propellers                       the catalogue (summary per prop)
    GET /api/propellers/cooling-options       what a die/config may offer for cooling
    GET /api/propellers/{id}                  one prop: published data, fits, curves
    GET /api/propellers/{id}/point            thrust / torque / power / cooling air at an rpm

SOLVER ISOLATION: nothing here solves a field or writes anything.  The catalogue
is read from ``config/propellers/`` (and ``<shared>/propellers/`` on the server)
through ``motor_ai_sim.propeller``; the cooling options from
``config/cooling_options.yaml``.  Ungated, like ``GET /api/wires/stock`` and
``GET /api/materials``: a reference table plus closed-form arithmetic, no compute.

``/point`` is the Configure hook: it turns (propeller, rpm) into the air speed over
the motor and, when the housing diameter is given, the film coefficient the
existing air mode (``cooling_models.outer_air``, Churchill–Bernstein cross-flow)
would use — so the browser's analytical thermal estimate needs no second copy of
the slipstream model.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query

from motor_ai_sim import propeller as pp

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/propellers", tags=["propellers"])


def _rho(temp_c: Optional[float], altitude_m: float, pressure_pa: Optional[float]) -> float:
    try:
        return pp.air_density(temp_c=temp_c, altitude_m=altitude_m, pressure_pa=pressure_pa)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


def _get(prop_id: str) -> pp.Propeller:
    try:
        return pp.get_propeller(prop_id)
    except pp.PropellerNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("")
def list_catalog(selectable_only: bool = Query(
        default=False, description="only props with usable thrust AND power data")) -> Dict[str, Any]:
    """Every catalogue entry, smallest diameter first.

    ``selectable`` is False for reference-only entries (geometry published, no
    test data): the model refuses to compute loads for them.  ``power_data`` says
    how C_P was obtained: ``measured_torque`` | ``estimated`` | ``none``.
    """
    props = [p for p in pp.list_propellers() if p.selectable or not selectable_only]
    return {
        "count": len(props),
        "propellers": [pp.summary(p) for p in props],
        "rho_default_kg_m3": pp.RHO_ISA,
        "slipstream": {"default_position": pp.DEFAULT_POSITION,
                       "factors": pp.POSITION_FACTORS, "notes": pp.POSITION_NOTES},
    }


@router.get("/cooling-options")
def get_cooling_options(die: str = Query(..., description="die name"),
                        config: Optional[str] = Query(default=None, description="configuration, e.g. L12")
                        ) -> Dict[str, Any]:
    """The cooling options the UI may offer for ``die`` / ``config``.

    ``cooling_options`` is ``null`` for an unrestricted die (everything as
    before).  For a restricted die it lists the modes (``propeller_air`` = slipstream
    air from the chosen propeller) and ``propeller_details`` carries the summary of
    each allowed propeller; ids the catalogue does not know are listed in
    ``unknown_propellers`` so a stale config is visible, not silent.
    """
    from motor_ai_sim.cooling_options import cooling_options
    out = cooling_options(die, config)
    ids = out.get("propellers")
    details, unknown = [], []
    if ids:
        cat = pp.load_catalog()
        for i in ids:
            if i in cat:
                details.append(pp.summary(cat[i]))
            else:
                unknown.append(i)
    out["propeller_details"] = details
    out["unknown_propellers"] = unknown
    return out


@router.get("/{prop_id}")
def get_one(prop_id: str,
            temp_c: Optional[float] = Query(default=None, description="air temperature for the curves [°C]"),
            altitude_m: float = Query(default=0.0),
            pressure_pa: Optional[float] = Query(default=None)) -> Dict[str, Any]:
    """The published data, the fitted C_T / C_P (with residuals) and the curves
    (thrust, torque, shaft power, wake speed, cooling air speed vs rpm) at the
    requested air density (default ISA sea level)."""
    prop = _get(prop_id)
    return pp.detail(prop, rho=_rho(temp_c, altitude_m, pressure_pa))


@router.get("/{prop_id}/point")
def get_point(prop_id: str,
              rpm: float = Query(..., ge=0.0, le=60000.0),
              temp_c: Optional[float] = Query(default=None, description="air temperature [°C]; default ISA"),
              altitude_m: float = Query(default=0.0),
              pressure_pa: Optional[float] = Query(default=None),
              position: Optional[str] = Query(default=None,
                                              description="developed_wake | disc_plane | behind_hub (default)"),
              factor: Optional[float] = Query(default=None, gt=0.0, le=1.5,
                                              description="explicit slipstream factor, overrides position"),
              housing_d_mm: Optional[float] = Query(default=None, gt=0.0, le=2000.0,
                                                    description="housing diameter: adds the film coefficient")
              ) -> Dict[str, Any]:
    """Thrust, torque, shaft power and cooling air speed at ``rpm``.

    With ``housing_d_mm`` the answer also carries ``film``: the h [W/m²K] and
    Reynolds number ``cooling_models.outer_air`` gives for that housing in the
    computed air (ambient defaults to 25 °C unless ``temp_c`` is given).
    """
    prop = _get(prop_id)
    rho = _rho(temp_c, altitude_m, pressure_pa)
    try:
        out = pp.operating_point(prop, rpm, rho, position=position, factor=factor)
    except pp.PropellerDataError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if housing_d_mm is not None:
        from motor_ai_sim.simulation import cooling_models as cm
        t_amb = 25.0 if temp_c is None else float(temp_c)
        rep = cm.outer_air(air_speed_mps=out["air_speed_ms"], t_ambient_c=t_amb,
                           d_housing_m=float(housing_d_mm) / 1000.0)
        out["film"] = {"h_conv_W_m2K": rep["h_conv"], "re": rep["re"], "regime": rep.get("regime"),
                       "t_ambient_c": t_amb, "housing_d_mm": float(housing_d_mm),
                       "model": "cooling_models.outer_air (Churchill–Bernstein cross-flow)"}
    return out
