"""Shared helper: locate an upstream GeometryIR from ANY geometry provider.

The mesh / cost modules used to read payload['upstream']['geometry.2d'] directly.
With the geometry Plugin-SDK a study may instead carry geometry.2d.<plugin> — so
downstream modules look up the geometry by ROLE (any 'geometry.*' key that
produced a GeometryIR), preferring the built-in 'geometry.2d' for back-compat.
"""
from __future__ import annotations

from typing import Any, Optional


def upstream_geometry(payload: Optional[dict]) -> Optional[Any]:
    up = (payload or {}).get("upstream") or {}
    g = up.get("geometry.2d")
    if g is not None and hasattr(g, "regions"):
        return g
    for k, v in up.items():
        if isinstance(k, str) and k.startswith("geometry.") and hasattr(v, "regions"):
            return v
    return None
