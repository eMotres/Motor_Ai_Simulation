"""Which machines name a catalogue card — the "used by" list.

A walk over the caller's die catalog (``routes.family._dies_dir``, i.e. the
workspace the rest of the Family API reads), looking at the two places a
configuration yaml names a card:

* ``bearings: {A: {card, grease}, B: {card, grease}}`` — bearings and their
  grease override (lubricant);
* ``controller: {device: <part>}`` — the power device.

Read-only, and a yaml that does not parse is skipped, never fatal: the list is
a navigation aid, not a gate.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import yaml


def _dies_dir() -> Path:
    from motor_ai_sim.routes import family as fam
    return fam._dies_dir()


def _refs(cfg: Dict[str, Any]) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {"bearing": [], "lubricant": [], "device": []}
    b = cfg.get("bearings")
    if isinstance(b, dict):
        for end in ("A", "B"):
            e = b.get(end)
            if isinstance(e, dict):
                if e.get("card"):
                    out["bearing"].append(str(e["card"]))
                if e.get("grease"):
                    out["lubricant"].append(str(e["grease"]))
    c = cfg.get("controller")
    if isinstance(c, dict) and c.get("device"):
        out["device"].append(str(c["device"]))
    return out


def used_by(kind: str, card_id: str) -> List[Dict[str, Any]]:
    root = _dies_dir()
    hits: List[Dict[str, Any]] = []
    if not root.is_dir():
        return hits
    for die_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for f in sorted(die_dir.glob("*.yaml")):
            if f.name == "die.yaml":
                continue
            try:
                cfg = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            except Exception:                                 # noqa: BLE001
                continue
            if not isinstance(cfg, dict):
                continue
            refs = _refs(cfg).get(kind) or []
            n = refs.count(card_id)
            if n:
                hits.append({"die": die_dir.name,
                             "config": str(cfg.get("name") or f.stem),
                             "count": n,
                             "where": ("bearings" if kind != "device"
                                       else "controller")})
    return hits
