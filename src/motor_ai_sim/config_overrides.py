"""The configuration's geometry overrides as every READER may use them (owner 2026-11-18: one die = one rotor).

The magnet fill / radius decide the rotor pocket, so a configuration cannot override them (``PATCH .../motor_keys`` refuses
a value).  A configuration saved before that still carries one in ``geometry_overrides``; it is QUARANTINED, not dropped:
no reader applies it (the machine is the die's rotor), ``shape_overrides`` names it so a payload / row can flag it, and
only the explicit null-clear of the motor_keys route removes it from the file."""
from typing import Dict, Optional

#: shape keys that decide the rotor pocket: never applied from a configuration
ROTOR_SHAPE_KEYS = ("magnet_fill_up", "magnet_fill_down", "magnet_fill_radius")


def shape_overrides(cfg_doc: Optional[dict]) -> Dict[str, float]:
    """The rotor-shape overrides a configuration still carries (quarantined: not applied; clear them)."""
    ov = (cfg_doc or {}).get("geometry_overrides") or {}
    return {k: ov[k] for k in ROTOR_SHAPE_KEYS if ov.get(k) is not None}


def geo_overrides(cfg_doc: Optional[dict]) -> dict:
    """``geometry_overrides`` of the configuration document without the quarantined rotor-shape keys (nulls kept as stored)."""
    ov = (cfg_doc or {}).get("geometry_overrides") or {}
    return {k: v for k, v in ov.items() if k not in ROTOR_SHAPE_KEYS}
