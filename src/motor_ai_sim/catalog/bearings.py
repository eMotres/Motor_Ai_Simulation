"""Adapter: ``config/bearings_library.yaml`` <-> the common card envelope.

Reads through :func:`motor_ai_sim.bearings._load` — the SAME file, the same
shared-copy precedence and the same mtime cache the friction model uses — so
the browser can never show a different library from the one a loss is
computed on.  Each entry's ``catalog:`` block is the envelope's metadata; the
rest of the entry is the body, handed back to the old loader untouched.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from motor_ai_sim import bearings as _brg
from motor_ai_sim.catalog.envelope import make_envelope

ENVELOPE_KEY = "catalog"


def _sources(lib: Dict[str, Any], ids: List[str]) -> List[Dict[str, Any]]:
    table = lib.get("catalog_sources") or {}
    out = []
    for sid in ids:
        s = dict(table.get(sid) or {})
        s["id"] = sid
        out.append(s)
    return out


def _units(lib: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, str]:
    u = lib.get("catalog_units") or {}
    names = set(body) | {f"friction.{k}" for k in (body.get("friction") or {})}
    return {k: v for k, v in u.items() if k in names}


def _split(raw: Dict[str, Any]):
    raw = dict(raw or {})
    meta = raw.pop(ENVELOPE_KEY, None) or {}
    return raw, meta


def _envelope(kind: str, name: str, raw: Dict[str, Any],
              lib: Dict[str, Any]) -> Dict[str, Any]:
    body, meta = _split(raw)
    return make_envelope(
        id=name, kind=kind, body=body,
        manufacturer=meta.get("manufacturer"),
        part_number=meta.get("part_number") or name,
        description=body.get("description"),
        status=meta.get("status") or "active",
        sources=_sources(lib, list(meta.get("sources") or [])),
        units=_units(lib, body),
        prov=meta.get("prov"),
        validation=meta.get("validation"),
        revision=meta.get("revision"),
        file="config/bearings_library.yaml",
    )


def bearing_envelopes() -> List[Dict[str, Any]]:
    lib = _brg._load()
    return [_envelope("bearing", n, (lib.get("bearings") or {})[n], lib)
            for n in _brg.list_bearings()]


def lubricant_envelopes() -> List[Dict[str, Any]]:
    lib = _brg._load()
    return [_envelope("lubricant", n, (lib.get("lubricants") or {})[n], lib)
            for n in _brg.list_lubricants()]


def bearing_envelope(name: str) -> Optional[Dict[str, Any]]:
    lib = _brg._load()
    raw = (lib.get("bearings") or {}).get(name)
    return None if raw is None else _envelope("bearing", name, raw, lib)


def lubricant_envelope(name: str) -> Optional[Dict[str, Any]]:
    lib = _brg._load()
    raw = (lib.get("lubricants") or {}).get(name)
    return None if raw is None else _envelope("lubricant", name, raw, lib)


# -- envelope -> the OLD dataclasses (the direction the golden test pins) ----

def to_bearing_card(env: Dict[str, Any]) -> "_brg.BearingCard":
    return _brg._card_from(env["id"], env["body"])


def to_lubricant(env: Dict[str, Any]) -> "_brg.Lubricant":
    return _brg._lube_from(env["id"], env["body"])


def bearing_row(env: Dict[str, Any]) -> Dict[str, Any]:
    b = env["body"]
    return {"type": b.get("type"), "d": b.get("d"), "D": b.get("D"),
            "B": b.get("B"), "C_kn": b.get("C_kn"), "seals": b.get("seals"),
            "balls": b.get("balls"),
            "n_limit_grease_rpm": b.get("n_limit_grease_rpm"),
            "n_limit_oil_air_rpm": b.get("n_limit_oil_air_rpm"),
            "stiffness_n_per_m": b.get("stiffness_n_per_m")}


def lubricant_row(env: Dict[str, Any]) -> Dict[str, Any]:
    b = env["body"]
    return {"type": b.get("kind"), "nu40": b.get("nu40"),
            "nu100": b.get("nu100"), "temp_range_c": b.get("temp_range_c")}
