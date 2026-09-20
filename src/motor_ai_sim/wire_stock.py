"""Enamelled flat copper wire physically in stock — ``config/wire_stock.yaml``.

WHY
===
The owner: *"давай сделаем справочную таблицу по доступным на складе
проводам; мы потом будем брать данные отсюда, чтобы пользователи могли менять
толщину провода из тех, что есть реально"* — a reference table of the flat
strip actually on the shelf, meant to become the source the winding editors
restrict the wire-size choice to.  This module only loads and validates the
table and offers it read-only; nothing here restricts a wire selector yet —
that is a deliberate later step (the owner decides the hard restriction).

Same loader shape as ``materials.py`` / ``bearings.py``: a module cache that
follows the file's mtime, a shared-root override for the multi-user layout,
and a half-written edit keeps serving the last good copy rather than taking
the app down mid-request.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Path + module cache
# ---------------------------------------------------------------------------
_LIB_PATH = Path(__file__).parent.parent.parent / "config" / "wire_stock.yaml"

#: The repo copy, and the value ``_LIB_PATH`` starts at — kept separately so
#: :func:`_lib_path` can tell "nobody moved it" (a test pointing at a fixture)
#: from "no shared copy exists yet".
_DEFAULT_LIB_PATH = _LIB_PATH


def _lib_path() -> Path:
    """Where the table is read from — mirrors ``materials._lib_path``.

    A ``_LIB_PATH`` a test moved wins outright; otherwise the shared-root copy
    (``<shared_root>/wire_stock.yaml``) wins when one exists on disk, and the
    repo copy answers everywhere else, including the pytest sandbox.
    """
    if _LIB_PATH != _DEFAULT_LIB_PATH:
        return _LIB_PATH
    try:
        from motor_ai_sim.workspace import shared_root
        p = Path(str(shared_root())) / _DEFAULT_LIB_PATH.name
        if p.is_file():
            return p
    except Exception:                           # noqa: BLE001
        pass
    return _LIB_PATH


_raw: Optional[dict] = None
_raw_mtime: float = 0.0
_raw_from: str = ""
_raw_checked: float = 0.0
#: Same bargain as materials._load — the cache follows the file, probed at
#: most once a second, and a half-written edit keeps the previously loaded
#: (validated) copy rather than taking the app down.
_MTIME_PROBE_S = 1.0

_wires_cache: Optional[List["WireStock"]] = None
_wires_cache_key: Tuple[str, float] = ("", 0.0)


class WireStockError(ValueError):
    """The wire-stock table failed validation."""


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WireStock:
    """One row of the warehouse table: one flat-strip item physically in stock."""

    code: str
    spec: str
    thickness_mm: float          # wire_height in this project's geometry
    width_mm: float               # wire_width
    insulation: str
    self_bonding: bool
    stock_kg: float
    warehouse: str
    thermal_class_c: Optional[float] = None
    #: The label on the warehouse screenshot was ambiguous (a typo, a missing
    #: prefix) — the owner has not confirmed it yet.  Surfaced in the UI as a
    #: warning, never silently dropped.
    check: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "spec": self.spec,
            "thickness_mm": self.thickness_mm,
            "width_mm": self.width_mm,
            "insulation": self.insulation,
            "self_bonding": self.self_bonding,
            "thermal_class_c": self.thermal_class_c,
            "stock_kg": self.stock_kg,
            "warehouse": self.warehouse,
            "check": self.check,
        }


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def _load_raw() -> dict:
    """Load ``wire_stock.yaml``, re-reading it when the file changes."""
    global _raw, _raw_mtime, _raw_checked, _raw_from
    now = time.monotonic()
    if _raw is not None and (now - _raw_checked) < _MTIME_PROBE_S:
        return _raw
    p = _lib_path()
    if not p.exists():
        if _raw is not None:
            return _raw              # file vanished mid-session: keep serving it
        raise FileNotFoundError(f"Wire stock table not found: {p}")
    _raw_checked = now
    mtime = p.stat().st_mtime
    if _raw is not None and mtime == _raw_mtime and _raw_from == str(p):
        return _raw
    try:
        with p.open("r", encoding="utf-8") as f:
            parsed = yaml.safe_load(f)
    except Exception as exc:                     # noqa: BLE001
        if _raw is not None:
            _log.warning("wire_stock.yaml unreadable (%s) — keeping the "
                         "previously loaded copy", exc)
            return _raw
        raise
    if not isinstance(parsed, dict):
        raise WireStockError(f"{p} does not parse to a mapping")
    _raw = parsed
    _raw_mtime = mtime
    _raw_from = str(p)
    return _raw


def _parse_entry(raw: dict, idx: int) -> WireStock:
    def _req(key: str):
        if key not in raw or raw[key] in (None, ""):
            raise WireStockError(f"wire_stock.yaml entry #{idx}: missing '{key}'")
        return raw[key]

    code = str(_req("code"))
    spec = str(_req("spec"))
    try:
        thickness_mm = float(_req("thickness_mm"))
        width_mm = float(_req("width_mm"))
    except (TypeError, ValueError) as exc:
        raise WireStockError(
            f"wire_stock.yaml entry #{idx} ({code!r}): thickness_mm/width_mm "
            f"must be numbers") from exc
    if not (thickness_mm > 0):
        raise WireStockError(
            f"wire_stock.yaml entry #{idx} ({code!r}): thickness_mm must be "
            f"positive; got {thickness_mm!r}")
    if not (width_mm > 0):
        raise WireStockError(
            f"wire_stock.yaml entry #{idx} ({code!r}): width_mm must be "
            f"positive; got {width_mm!r}")
    try:
        stock_kg = float(raw.get("stock_kg") if raw.get("stock_kg") is not None else 0.0)
    except (TypeError, ValueError) as exc:
        raise WireStockError(
            f"wire_stock.yaml entry #{idx} ({code!r}): stock_kg must be a "
            f"number") from exc
    if stock_kg < 0:
        raise WireStockError(
            f"wire_stock.yaml entry #{idx} ({code!r}): stock_kg must not be "
            f"negative; got {stock_kg!r}")
    thermal_class_c = raw.get("thermal_class_c")
    return WireStock(
        code=code,
        spec=spec,
        thickness_mm=thickness_mm,
        width_mm=width_mm,
        insulation=str(raw.get("insulation") or ""),
        self_bonding=bool(raw.get("self_bonding", False)),
        stock_kg=stock_kg,
        warehouse=str(raw.get("warehouse") or ""),
        thermal_class_c=(float(thermal_class_c) if thermal_class_c is not None else None),
        check=bool(raw.get("check", False)),
    )


def _validate_unique(wires: List[WireStock]) -> None:
    seen: Dict[Tuple[str, str], int] = {}
    for i, w in enumerate(wires):
        key = (w.code, w.spec)
        if key in seen:
            raise WireStockError(
                f"wire_stock.yaml: duplicate code+spec {key!r} at entries "
                f"#{seen[key]} and #{i}")
        seen[key] = i


def list_wires() -> List[WireStock]:
    """Every wire in stock, validated, sorted by thickness then width then code.

    Cached against the file's mtime like the raw loader; re-validates on every
    file change so a malformed edit is caught here rather than in the route.
    """
    global _wires_cache, _wires_cache_key
    raw = _load_raw()
    key = (_raw_from, _raw_mtime)
    if _wires_cache is not None and _wires_cache_key == key:
        return _wires_cache
    entries = raw.get("wires")
    if not isinstance(entries, list):
        raise WireStockError("wire_stock.yaml: 'wires' must be a list")
    wires = [_parse_entry(e, i) for i, e in enumerate(entries)]
    _validate_unique(wires)
    wires.sort(key=lambda w: (w.thickness_mm, w.width_mm, w.code))
    _wires_cache = wires
    _wires_cache_key = key
    return wires


def available_sizes() -> List[Dict[str, Any]]:
    """Sorted unique ``(thickness_mm, width_mm)`` pairs with summed stock_kg.

    ``codes`` lists every item code stocked at that size, so a caller can trace
    a size back to the warehouse rows that make it up.
    """
    by_size: Dict[Tuple[float, float], Dict[str, Any]] = {}
    for w in list_wires():
        key = (w.thickness_mm, w.width_mm)
        slot = by_size.setdefault(key, {
            "thickness_mm": w.thickness_mm,
            "width_mm": w.width_mm,
            "stock_kg": 0.0,
            "codes": [],
        })
        slot["stock_kg"] += w.stock_kg
        slot["codes"].append(w.code)
    return [by_size[k] for k in sorted(by_size.keys())]


def meta() -> Dict[str, Any]:
    """``{version, updated, unit}`` — the table's own header fields."""
    raw = _load_raw()
    return {
        "version": raw.get("version"),
        "updated": raw.get("updated"),
        "unit": raw.get("unit", "kg"),
    }


def nearest_sizes(thickness_mm: float, width_mm: float, n: int = 3) -> List[Dict[str, Any]]:
    """The ``n`` stocked sizes closest to ``(thickness_mm, width_mm)``.

    Euclidean distance in mm — good enough for "nearest" over a handful of
    discrete sizes; not used to restrict anything, only to hint.
    """
    sizes = available_sizes()
    if not sizes:
        return []
    def _dist(s: Dict[str, Any]) -> float:
        dt = s["thickness_mm"] - thickness_mm
        dw = s["width_mm"] - width_mm
        return (dt * dt + dw * dw) ** 0.5
    return sorted(sizes, key=_dist)[:max(0, int(n))]


def reload() -> None:
    """Force a reload of the table from disk, bypassing the mtime probe."""
    global _raw, _raw_mtime, _raw_checked, _wires_cache, _wires_cache_key
    _raw = None
    _raw_mtime = 0.0
    _raw_checked = 0.0
    _wires_cache = None
    _wires_cache_key = ("", 0.0)
    _load_raw()
    list_wires()
