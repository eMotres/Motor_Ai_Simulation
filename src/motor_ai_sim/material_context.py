"""Per-request material override (multi-user, Stage 2b).

A signed-in user can assign their OWN materials — personal "mine" or admin-managed
"global" — to motor parts. The frontend resolves the active assignment + the props
of any non-built-in material in use and sends them as `mat=<JSON>` on compute
requests (mirrors `geo=`). `simulation.build_materials` reads this override so the
FEM solve uses the user's materials instead of the shared `config/motor_config.yaml`
— stateless, no server write, isolated per user.

Mechanism: an async router dependency parses `mat` and sets the ContextVar BEFORE
the (sync) handler runs; Starlette copies the request task's context into the
threadpool worker that runs the handler, so `build_materials` — same synchronous
call stack — reads it. Each request is its own asyncio task with a copied context,
so there is no cross-request leak, and the optimizer subprocess starts fresh (None)
and simply falls back to the library. Absent override → build_materials behaves
exactly as before.

Shape: {"assignment": {region_or_part: material_name}, "materials": {name: props}}
where `props` is the GET-library dict (steel/magnet/conductor/... fields).
"""
from __future__ import annotations

import contextvars
from typing import Any, Dict, Optional

_OVERRIDE: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar(
    "material_override", default=None,
)


def set_request_materials(override: Optional[Dict[str, Any]]) -> None:
    """Set (or clear, with None) the current request's material override."""
    _OVERRIDE.set(override or None)


def get_request_materials() -> Optional[Dict[str, Any]]:
    """The current request's material override, or None. Read by build_materials."""
    return _OVERRIDE.get()
