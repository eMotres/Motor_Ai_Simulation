"""Versioned store of full (v1) passport records — what Configure reads its
COMPUTED DRIVE VARIANTS from.

The passport pilot (``passport_v1``, branch feat/passport-pilot-d40) produces one
record per machine, with a ``pwm_variants`` block: each entry a (device, carrier)
pair solved for THAT machine, with computed points over speed and current
(``motor_pwm_loss_W``, ``inverter_loss_W{cond,sw,dead}``, ``tj_C``,
``eta_drive_pct``, ``eta_shaft_pct``, ``p_cont_max_W`` …).  Configure's PWM menu
lists exactly those and nothing else (owner 2026-10-05).

Layout, the same way the device cards are installed::

    config/passports/<die>/<config>.json        in the repository
    <shared>/passports/<die>/<config>.json      on the server (wins when it exists)

``scripts/export_passport_store.py`` writes a store file from a pilot record
(identity + ``pwm_variants`` only — the heavy raw blocks stay in
``docs/data/``).  This module only READS, and only ever hands out
``pwm_variants``: the catalogue's own passport (the FEM-scaled analytical model)
is never replaced or edited by a record, so a machine without one is exactly what
it was.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

_REPO_DIR = Path(__file__).resolve().parents[2] / "config" / "passports"

#: Overridden by tests; a moved path wins outright (the device-card rule).
_DIR: Path = _REPO_DIR


def store_dir() -> Path:
    """``<shared>/passports`` when the server has one, else the repo folder."""
    if _DIR != _REPO_DIR:
        return _DIR
    try:
        from motor_ai_sim.workspace import shared_root
        p = Path(str(shared_root())) / "passports"
        if p.is_dir():
            return p
    except Exception:                                       # noqa: BLE001
        pass
    return _DIR


def _usable(v: Any) -> bool:
    """A variant Configure can read: id, device, a carrier and computed points."""
    return (isinstance(v, dict) and bool(v.get("id")) and bool(v.get("device"))
            and float(v.get("carrier_hz") or 0) > 0
            and isinstance(v.get("points"), dict) and bool(v["points"]))


def _records() -> List[Tuple[str, str, Dict[str, Any]]]:
    """``[(die, config, record)]`` for every readable store file."""
    out: List[Tuple[str, str, Dict[str, Any]]] = []
    root = store_dir()
    if not root.is_dir():
        return out
    for die_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for f in sorted(die_dir.glob("*.json")):
            try:
                rec = json.loads(f.read_text(encoding="utf-8"))
            except Exception as exc:                        # noqa: BLE001
                log.warning("passport store: %s is unreadable: %s", f, exc)
                continue
            if isinstance(rec, dict):
                out.append((die_dir.name, f.stem, rec))
    return out


def _length_mm(rec: Dict[str, Any]) -> Optional[float]:
    b = rec.get("build")
    if not isinstance(b, dict):
        for v in rec.get("pwm_variants") or []:
            if isinstance(v, dict) and isinstance(v.get("build"), dict):
                b = v["build"]
                break
    try:
        return float((b or {}).get("length_mm"))
    except (TypeError, ValueError):
        return None


def variants_for(card_name: Optional[str], length_mm: Optional[float] = None
                 ) -> Optional[List[Dict[str, Any]]]:
    """The computed drive variants of the machine a catalogue card stands for,
    or ``None`` (no record / no usable variant — the card is served unchanged).

    The card is matched by its name: ``"<die> <config>"`` exactly, or the die
    name alone (older cards carry it) — and when several configurations sit
    under that die, by the STACK LENGTH, which is what a configuration of a
    shared die is.  Still ambiguous -> ``None``: guessing which machine's
    variants a card gets is the silent substitution a passport must not make.
    """
    name = str(card_name or "").strip().casefold()
    if not name:
        return None
    exact: List[Dict[str, Any]] = []
    by_die: List[Tuple[str, Dict[str, Any]]] = []
    for die, cfg, rec in _records():
        if f"{die} {cfg}".casefold() == name:
            exact.append(rec)
        elif die.casefold() == name:
            by_die.append((cfg, rec))
    pick: Optional[Dict[str, Any]] = None
    if len(exact) == 1:
        pick = exact[0]
    elif len(by_die) == 1:
        pick = by_die[0][1]
    elif by_die and length_mm is not None:
        near = [r for _, r in by_die
                if (_length_mm(r) is not None and abs(_length_mm(r) - float(length_mm)) < 1e-6)]
        if len(near) == 1:
            pick = near[0]
    if pick is None:
        return None
    vs = [v for v in (pick.get("pwm_variants") or []) if _usable(v)]
    return vs or None


def attach(motors: List[Dict[str, Any]]) -> None:
    """Add ``pwm_variants`` to the passport block of every card that has a
    record, in place (the cards are request-local copies).  Nothing else of a
    card is touched; a card whose passport already carries variants keeps them."""
    for m in motors:
        sp = m.get("passport")
        if not isinstance(sp, dict) or sp.get("pwm_variants"):
            continue
        inner = sp.get("passport") if isinstance(sp.get("passport"), dict) else {}
        try:
            L = float(inner.get("L0_mm"))
        except (TypeError, ValueError):
            L = None
        vs = variants_for(m.get("name"), L)
        if vs:
            m["passport"] = {**sp, "pwm_variants": vs}
