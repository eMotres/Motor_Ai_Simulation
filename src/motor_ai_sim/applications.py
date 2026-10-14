"""What a motor configuration is FOR: its ``applications`` list (owner 2026-10-13).

Every configuration yaml may carry ``applications: [...]``.  The first entry is the default
application.  Today each configuration has one entry; a second one is added later by a data
edit alone (Configure then shows a switch).

=========== =====================================================================
value       what it controls in Configure
=========== =====================================================================
propeller   drones: the propeller selection, prop-wash cooling, propeller S1,
            thrust and rpm-at-prop (today's Ø40 behaviour)
robot       NO propeller selection, prop-wash cooling or propeller S1; cooling is
            the "robotics" mode (still air, radiation, mount, end faces).  Stored
            propeller S1 data is hidden, never deleted.
aviation    liquid-cooled; no propeller, no robotics block
marine      liquid-cooled; no propeller, no robotics block
=========== =====================================================================

A configuration WITHOUT the field behaves exactly as before and is flagged "application not
set" in Admin.  An unknown or malformed value is an error, never silently dropped.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

APPLICATIONS: Tuple[str, ...] = ("propeller", "robot", "aviation", "marine")

PROPELLER_AIR = "propeller_air"
ROBOTICS = "robotics"


class ApplicationsError(ValueError):
    """The ``applications`` value of a configuration is not valid."""


def validate_applications(value: Any) -> List[str]:
    """The cleaned list, or ``ApplicationsError`` saying exactly what is wrong.

    ``None`` (the key is absent) is "not set" and returns ``[]``.  Anything else must be a
    non-empty list of distinct known values; a bare string is refused (``applications: robot``
    is the typo this guards against)."""
    if value is None:
        return []
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise ApplicationsError(
            f"applications must be a list such as [robot], got {value!r}")
    if not value:
        raise ApplicationsError(
            "applications is empty; list at least one of " + ", ".join(APPLICATIONS)
            + " or remove the field")
    out: List[str] = []
    for v in value:
        if not isinstance(v, str) or v not in APPLICATIONS:
            raise ApplicationsError(
                f"unknown application {v!r}; allowed: " + ", ".join(APPLICATIONS))
        if v in out:
            raise ApplicationsError(f"application {v!r} is listed twice")
        out.append(v)
    return out


def config_applications(c: Optional[dict]) -> Tuple[List[str], Optional[str]]:
    """``(applications, error)`` for a configuration document.

    Used by READERS (the tree, Configure) that must not take the whole catalog down for one
    bad file: an invalid value yields ``([], <message>)`` so the problem is shown, and the
    configuration behaves as "not set" until the file is fixed.  Writers use
    ``validate_applications`` and refuse."""
    if not isinstance(c, dict):
        return [], None
    try:
        return validate_applications(c.get("applications")), None
    except ApplicationsError as exc:
        return [], str(exc)


def default_application(applications: List[str]) -> Optional[str]:
    return applications[0] if applications else None


def propeller_allowed(applications: Optional[List[str]]) -> bool:
    """False only when the configuration names applications and none of them is propeller."""
    return not applications or "propeller" in applications


def restrict_cooling(cooling: Dict[str, Any], applications: Optional[List[str]]
                     ) -> Dict[str, Any]:
    """Apply the application to a ``cooling_options()`` answer (returns a new dict).

    A configuration that is not a propeller application loses the propeller from its cooling
    and its propeller list.  A robot keeps (or gets) the robotics mode; aviation and marine
    fall back to the unrestricted state they have for a die with no cooling entry.  Nothing
    is deleted from the registry file: removing the application's restriction restores it."""
    out = dict(cooling)
    out["applications"] = list(applications or [])
    out["application"] = default_application(list(applications or []))
    if propeller_allowed(applications):
        return out
    opts = out.get("cooling_options")
    had_propeller = isinstance(opts, list) and PROPELLER_AIR in opts
    if isinstance(opts, list):
        kept = [o for o in opts if o != PROPELLER_AIR]
        if "robot" in (applications or []) and ROBOTICS not in kept:
            kept.append(ROBOTICS)
        out["cooling_options"] = kept or None
        out["restricted"] = bool(kept)
    if out.get("propellers"):
        out["propellers"] = []
    out["defaults"] = {}
    out["default_propeller"] = None
    out["propeller_hidden"] = had_propeller or bool(cooling.get("propellers"))
    return out
