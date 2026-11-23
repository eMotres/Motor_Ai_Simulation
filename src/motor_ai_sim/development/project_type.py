"""The type of a project: Motor (every project made before 2026-11-23) or Controller (owner 2026-11-22: «спецификация сделана
только для мотора, для контроллера она должна быть другой ... нужно чтобы был выбор [типа] проекта»).

Stored as ``project["project_type"] = "controller"`` ONLY for a controller project: the key absent is a motor project, so
every existing project and every new motor project stays byte-identical.  Staff choose the type when they create a project
(a customer's project is always a motor project: controllers are internal for now); afterwards only an admin changes it,
because it swaps the whole specification form."""
from __future__ import annotations

from typing import Any, Optional

MOTOR = "motor"
CONTROLLER = "controller"
TYPES = (MOTOR, CONTROLLER)
KEY = "project_type"


def of(project: Any) -> str:
    """The type of a stored project record (``motor`` when the key is absent or unknown)."""
    value = project.get(KEY) if isinstance(project, dict) else None
    return CONTROLLER if value == CONTROLLER else MOTOR


def parse(value: Any) -> Optional[str]:
    """A requested type: ``None`` when not given, else ``motor`` / ``controller``; ``ValueError`` for anything else."""
    if value is None or value == "":
        return None
    text = str(value).strip().lower()
    if text not in TYPES:
        raise ValueError(f"project_type must be one of {', '.join(TYPES)}")
    return text


def apply(project: dict, value: str) -> None:
    """Set the type on a project record in place: a controller carries the key, a motor carries none."""
    if value == CONTROLLER:
        project[KEY] = CONTROLLER
    else:
        project.pop(KEY, None)


def is_admin(user: dict) -> bool:
    """The type is changed by a global admin only (the same rule as ``access.can_edit_spec`` reads an admin)."""
    return str(user.get("role") or "").lower() == "admin"
