"""The catalogue of COMPLETE commercial controllers - ``config/controllers/catalog.yaml``.

``config/devices/`` holds POWER DEVICES (one MOSFET card per part, the loss model's input).  This file is the other
thing: a whole controller product (DTI HV-850, ...), as its manufacturer documents it.  The Development specification
picks its controller from here and the Controller tab lists it.

Three rules, the same ones the device cards keep:

* **Nothing is invented.**  What the source document does not state is ``null`` in the file and ``None`` here.
* **Every value carries its provenance**: ``basis`` (``stated`` / ``derived`` / ``figure`` / ``inferred``) and the
  ``page`` of the source document.  :func:`validate_catalog` refuses a value without them.
* **Read-only.**  Nothing in the application writes this file; a new product is a reviewed change to the YAML.

The file is re-read when its mtime moves (a corrected entry needs no restart).
"""
from __future__ import annotations

import copy
import math
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

__all__ = [
    "CatalogError", "BASES", "catalog_path", "load_catalog", "list_controllers", "get_controller", "summary",
    "match_free_text", "prefill_values", "supply_max_V", "battery_vs_controller", "link_catalog_controller",
    "PREFILL_PATHS", "power_devices_known", "LEGACY_SUPPLIER_CHOICES",
]

BASES = ("stated", "derived", "figure", "inferred")

_REPO_PATH = Path(__file__).resolve().parents[3] / "config" / "controllers" / "catalog.yaml"
#: the API image keeps config/ as a read-only SEED at <app>/config-seed (deploy/Dockerfile.api), never at <app>/config
_SEED_PATH = Path(__file__).resolve().parents[3] / "config-seed" / "controllers" / "catalog.yaml"
#: Overridden by tests (a fixture file); a moved path wins outright (the devices rule).
_PATH: Path = _REPO_PATH

_lock = threading.Lock()
_cache: Dict[str, Any] = {"path": None, "mtime": None, "doc": None}

#: spec field path -> key of ``spec_prefill`` in a catalog entry.  The ORDER is the order the specification fills them.
PREFILL_PATHS = {
    "2_5.device_type": "device_type",
    "2_5.voltage_class_V": "voltage_class_V",
    "2_6.continuous_Arms": "phase_current_continuous_Arms",
    "2_6.peak_Arms": "phase_current_peak_Arms",
    "2_6.peak_duration_s": "phase_current_peak_duration_s",
    "2_7.switching_frequency_Hz": "switching_frequency_Hz",
    "2_8": "position_sensor",
}


class CatalogError(ValueError):
    """The catalogue file is malformed."""


def catalog_path() -> Path:
    """The catalogue file: a moved ``_PATH`` (a test fixture) wins outright; else ``shared/controllers/catalog.yaml``
    when it exists (the machine-wide libraries, as devices_dir), else the repository's ``config/``, else the image seed."""
    if _PATH != _REPO_PATH:
        return _PATH
    candidates = []
    try:
        from motor_ai_sim.workspace import shared_root
        candidates.append(Path(str(shared_root())) / "controllers" / "catalog.yaml")
    except Exception:                                    # noqa: BLE001
        pass
    candidates += [_REPO_PATH, _SEED_PATH]
    for p in candidates:
        if p.is_file():
            return p
    return _REPO_PATH


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v) if math.isfinite(v) else None


def _walk_provenance(node: Any, where: str, problems: List[str]) -> None:
    """Every dict that carries a ``value`` must say ``basis`` and ``page``; a ``basis`` must be a known word."""
    if isinstance(node, dict):
        if "value" in node or "basis" in node:
            if node.get("basis") not in BASES:
                problems.append(f"{where}: basis must be one of {', '.join(BASES)}")
            page = node.get("page")
            if isinstance(page, bool) or not isinstance(page, int) or page < 1:
                problems.append(f"{where}: page (source page number) is required")
        for key, child in node.items():
            _walk_provenance(child, f"{where}.{key}", problems)
    elif isinstance(node, list):
        for i, child in enumerate(node):
            _walk_provenance(child, f"{where}[{i}]", problems)


def validate_catalog(doc: Any) -> List[str]:
    """The problems of a loaded catalogue (an empty list = valid)."""
    problems: List[str] = []
    if not isinstance(doc, dict) or not isinstance(doc.get("controllers"), list):
        return ["catalog.yaml needs a 'controllers' list"]
    seen = set()
    for i, entry in enumerate(doc["controllers"]):
        where = f"controllers[{i}]"
        if not isinstance(entry, dict):
            problems.append(f"{where}: must be a mapping")
            continue
        for key in ("id", "brand", "model", "revision", "display_name", "source", "supply_voltage_V"):
            if entry.get(key) in (None, "", {}):
                problems.append(f"{where}: '{key}' is required")
        cid = str(entry.get("id") or "")
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", cid):
            problems.append(f"{where}: id '{cid}' must be lowercase letters, digits, '.', '_' or '-'")
        if cid in seen:
            problems.append(f"{where}: duplicate id {cid}")
        seen.add(cid)
        src = entry.get("source") if isinstance(entry.get("source"), dict) else {}
        if not src.get("document"):
            problems.append(f"{where}: source.document is required")
        _walk_provenance(entry, f"{cid or where}", problems)
        # a prefill value is a COPY of a documented value: it must exist elsewhere with the same basis is too strict to
        # check mechanically; what is checked is that it names only known targets and carries provenance (walk above)
        pre = entry.get("spec_prefill") or {}
        unknown = set(pre) - set(PREFILL_PATHS.values()) - {"phase_count"}
        if unknown:
            problems.append(f"{cid}: unknown spec_prefill keys {sorted(unknown)}")
    return problems


def load_catalog() -> Dict[str, Any]:
    """The parsed, validated catalogue (cached on the file's mtime).  Raises :class:`CatalogError` when malformed."""
    path = catalog_path()
    with _lock:
        try:
            mtime = path.stat().st_mtime_ns
        except OSError as exc:
            raise CatalogError(f"controller catalogue not found: {path}") from exc
        if _cache["path"] == str(path) and _cache["mtime"] == mtime and _cache["doc"] is not None:
            return _cache["doc"]
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        problems = validate_catalog(doc)
        if problems:
            raise CatalogError("; ".join(problems[:8]))
        _cache.update({"path": str(path), "mtime": mtime, "doc": doc})
        return doc


def _v(node: Any) -> Any:
    """The ``value`` of a provenance cell, or the cell itself when it is plain."""
    if isinstance(node, dict):
        return node.get("value")
    return node


def power_devices_known(entry: Dict[str, Any]) -> bool:
    pd = entry.get("power_devices") or {}
    return any(pd.get(k) not in (None, "") for k in ("device_type", "voltage_class_V", "part_number"))


def supply_max_V(entry: Dict[str, Any]) -> Optional[float]:
    """The controller's stated maximum supply (operating) voltage - what a battery's maximum is checked against."""
    return _num(_v((entry.get("supply_voltage_V") or {}).get("max")))


def prefill_values(entry: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """``{spec field path: {value, basis, page}}`` for the values the entry knows (``None`` ones are left out)."""
    pre = entry.get("spec_prefill") or {}
    out: Dict[str, Dict[str, Any]] = {}
    for path, key in PREFILL_PATHS.items():
        cell = pre.get(key)
        if isinstance(cell, dict) and cell.get("value") not in (None, ""):
            out[path] = {"value": cell["value"], "basis": cell.get("basis"), "page": cell.get("page")}
    return out


def summary(entry: Dict[str, Any]) -> Dict[str, Any]:
    """The flat row the list view and the specification picker use (all numbers keep their ``basis`` / ``page``)."""
    pc = entry.get("phase_current") or {}
    sup = entry.get("supply_voltage_V") or {}
    fsw = entry.get("switching_frequency_Hz") or {}
    return {
        "id": entry["id"], "brand": entry["brand"], "model": entry["model"], "revision": entry.get("revision"),
        "display_name": entry["display_name"], "manufacturer": entry.get("manufacturer"),
        "aliases": list(entry.get("aliases") or []),
        "supply_voltage_V": {k: copy.deepcopy(sup.get(k)) for k in ("min", "nominal", "max", "abs_max")},
        "supply_max_V": supply_max_V(entry),
        "dc_input_current_max_A": copy.deepcopy(entry.get("dc_input_current_max_A")),
        "phase_current": {
            "continuous": copy.deepcopy(pc.get("continuous") or []),
            "continuous_Arms_at_supply_max": copy.deepcopy(pc.get("continuous_Arms_at_supply_max")),
            "peak": copy.deepcopy(pc.get("peak") or []),
            "peak_duration_s": copy.deepcopy(pc.get("peak_duration_s")),
        },
        "switching_frequency_Hz": {k: copy.deepcopy(fsw.get(k)) for k in ("min", "max", "rating_point")},
        "power_devices": copy.deepcopy(entry.get("power_devices") or {}),
        "power_devices_known": power_devices_known(entry),
        "phases": copy.deepcopy(entry.get("phases") or {}),
        "position_sensors": copy.deepcopy(entry.get("position_sensors") or []),
        "communication": copy.deepcopy(entry.get("communication") or []),
        "mass_kg": copy.deepcopy(entry.get("mass_kg")),
        "dimensions_mm": copy.deepcopy(entry.get("dimensions_mm")),
        "protection": copy.deepcopy(entry.get("protection")),
        "cooling": copy.deepcopy(entry.get("cooling")),
        "regeneration": copy.deepcopy(entry.get("regeneration")),
        "typical_efficiency_pct": copy.deepcopy(entry.get("typical_efficiency_pct")),
        "source": copy.deepcopy(entry.get("source")),
        "notes": list(entry.get("notes") or []),
        "prefill": prefill_values(entry),
    }


def list_controllers() -> List[Dict[str, Any]]:
    return [summary(e) for e in load_catalog()["controllers"]]


def get_controller(controller_id: str) -> Optional[Dict[str, Any]]:
    """The WHOLE entry (deep copy) or ``None``."""
    for entry in load_catalog()["controllers"]:
        if entry["id"] == controller_id:
            return copy.deepcopy(entry)
    return None


def _norm(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def match_free_text(text: Any) -> Optional[Dict[str, Any]]:
    """The catalogue entry a free-text controller name means, or ``None``.

    Only an exact name (after dropping case, spaces and punctuation) of the display name, an alias or
    ``brand + model [+ revision]`` counts.  A name that fits two entries is ambiguous and matches neither - a wrong link is
    worse than none.
    """
    wanted = _norm(text)
    if not wanted:
        return None
    hits = []
    for entry in load_catalog()["controllers"]:
        names = {_norm(entry["display_name"]), _norm(f"{entry['brand']} {entry['model']}"),
                 _norm(f"{entry['brand']} {entry['model']} {entry.get('revision') or ''}"),
                 _norm(f"{entry['model']} {entry.get('revision') or ''}")}
        names.update(_norm(a) for a in entry.get("aliases") or [])
        names.discard("")
        if wanted in names:
            hits.append(entry)
    return copy.deepcopy(hits[0]) if len(hits) == 1 else None


def _controller_value(draft: Dict[str, Any]) -> Dict[str, Any]:
    fields = draft.get("fields") if isinstance(draft, dict) else None
    answer = (fields or {}).get("2_4") if isinstance(fields, dict) else None
    value = answer.get("value") if isinstance(answer, dict) else None
    return value if isinstance(value, dict) else {}


#: What the "Supplier" answer used to be: a CHOICE of who provides the controller, not a company name.
LEGACY_SUPPLIER_CHOICES = ("customer", "motres", "third party")


def link_catalog_controller(draft: Dict[str, Any]) -> Dict[str, Any]:
    """Read-time link: a free-text controller name that matches a catalogue entry gets its ``catalog_id``, and the
    supplier shows the catalogue brand.

    Returns a COPY; the stored draft is not changed and the link is saved with the next save.  Nothing is lost or
    overwritten: no other stored value is touched, a ``catalog_id`` already there (even a stale one) is kept, and an old
    supplier CHOICE ("Customer", "MOTRES", "Third party" - the answer meant who provides the controller, not the company)
    is moved to ``supplier_legacy`` while ``supplier`` shows the brand (e.g. DTI).  A supplier the customer typed as a
    company name is never replaced; an empty one is filled with the brand.
    """
    out = copy.deepcopy(draft)
    value = _controller_value(out)
    if not value:
        return out
    cid = value.get("catalog_id")
    entry = get_controller(cid) if cid else match_free_text(value.get("manufacturer_model"))
    if entry is None:
        return out
    if not cid:
        value["catalog_id"] = entry["id"]
    supplier = value.get("supplier")
    brand = entry["brand"]
    if isinstance(supplier, str) and supplier.strip().lower() in LEGACY_SUPPLIER_CHOICES:
        value.setdefault("supplier_legacy", supplier)
        value["supplier"] = brand
    elif supplier in (None, ""):
        value["supplier"] = brand
    return out


def _battery_max_V(draft: Dict[str, Any]) -> Optional[float]:
    fields = draft.get("fields") if isinstance(draft, dict) else None
    answer = (fields or {}).get("2_1") if isinstance(fields, dict) else None
    value = answer.get("value") if isinstance(answer, dict) else None
    if not isinstance(value, dict):
        return None
    if value.get("supply_type") == "dc_source":        # a DC power source states one voltage
        return _num(value.get("dc_source_V"))
    top = _num(value.get("voltage_max_V"))
    if top is not None:
        return top
    cells, cell_max = _num(value.get("series_cells")), _num(value.get("cell_voltage_max_V"))
    return cells * cell_max if cells and cell_max else None


def battery_vs_controller(draft: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A validation error when the battery's maximum voltage exceeds the linked controller's supply maximum.

    ``None`` without a link, without a stated controller maximum or without a stated battery maximum.
    """
    try:
        value = _controller_value(draft)
        cid = value.get("catalog_id")
        # a free-text name that IS a catalogue entry counts too (an old project is checked before its first save)
        entry = get_controller(cid) if cid else match_free_text(value.get("manufacturer_model"))
    except CatalogError:
        return None
    if entry is None:
        return None
    limit, top = supply_max_V(entry), _battery_max_V(draft)
    if limit is None or top is None or top <= limit:
        return None
    return {"field": "2_1.voltage_max_V", "section": "2",
            "message": f"Battery maximum {top:g} V exceeds the maximum supply voltage {limit:g} V of {entry['display_name']}"}
