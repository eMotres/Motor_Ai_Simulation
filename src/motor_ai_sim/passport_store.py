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
import time
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


#: ``{path: ((mtime_ns, size), slim record)}`` — a file is parsed again only when it
#: changed on disk.  The slim record keeps what Configure reads (the usable
#: ``pwm_variants``, each carrying its build length for the matching rule); the
#: pilot's heavy blocks never sit in memory.
_CACHE: Dict[str, Tuple[Tuple[int, int], Dict[str, Any]]] = {}


def _slim(rec: Dict[str, Any]) -> Dict[str, Any]:
    vs = [v for v in (rec.get("pwm_variants") or []) if _usable(v)]
    return {"pwm_variants": vs,
            "build": rec.get("build") if isinstance(rec.get("build"), dict) else None,
            # a v1 passport record = a FULL passport card (the pilot's schema tag)
            "full": str(rec.get("schema") or "").startswith("passport-v1")}


def _read(f: Path) -> Optional[Dict[str, Any]]:
    try:
        st = f.stat()
        sig = (st.st_mtime_ns, st.st_size)
    except OSError:
        _CACHE.pop(str(f), None)
        return None
    hit = _CACHE.get(str(f))
    if hit and hit[0] == sig:
        return hit[1]
    try:
        rec = json.loads(f.read_text(encoding="utf-8"))
    except Exception as exc:                                # noqa: BLE001
        log.warning("passport store: %s is unreadable: %s", f, exc)
        return None
    slim = _slim(rec) if isinstance(rec, dict) else None
    if slim is not None:
        # the card's date = when the record file was written (store files carry
        # no date of their own); read here so the mtime cache serves it too
        slim["date"] = time.strftime("%Y-%m-%d", time.localtime(st.st_mtime))
        _CACHE[str(f)] = (sig, slim)
    return slim


def _records() -> List[Tuple[str, str, Dict[str, Any]]]:
    """``[(die, config, slim record)]`` for every readable store file."""
    out: List[Tuple[str, str, Dict[str, Any]]] = []
    root = store_dir()
    if not root.is_dir():
        return out
    for die_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for f in sorted(die_dir.glob("*.json")):
            rec = _read(f)
            if rec is not None:
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


def _pick(card_name: Optional[str], length_mm: Optional[float] = None,
          records: Optional[List[Tuple[str, str, Dict[str, Any]]]] = None
          ) -> Optional[Tuple[str, str, Dict[str, Any]]]:
    """The ``(die, config, record)`` a catalogue card stands for, or None.

    The card is matched by its name: ``"<die> <config>"`` exactly, or the die
    name alone (older cards carry it) - and when several configurations sit
    under that die, by the STACK LENGTH, which is what a configuration of a
    shared die is.  Still ambiguous -> None: guessing which machine's record a
    card gets is the silent substitution a passport must not make.
    """
    name = str(card_name or "").strip().casefold()
    if not name:
        return None
    exact: List[Tuple[str, str, Dict[str, Any]]] = []
    by_die: List[Tuple[str, str, Dict[str, Any]]] = []
    for die, cfg, rec in (records if records is not None else _records()):
        if f"{die} {cfg}".casefold() == name:
            exact.append((die, cfg, rec))
        elif die.casefold() == name:
            by_die.append((die, cfg, rec))
    if len(exact) == 1:
        return exact[0]
    if len(by_die) == 1:
        return by_die[0]
    if by_die and length_mm is not None:
        near = [t for t in by_die
                if (_length_mm(t[2]) is not None and abs(_length_mm(t[2]) - float(length_mm)) < 1e-6)]
        if len(near) == 1:
            return near[0]
    return None


def variants_for(card_name: Optional[str], length_mm: Optional[float] = None,
                 records: Optional[List[Tuple[str, str, Dict[str, Any]]]] = None
                 ) -> Optional[List[Dict[str, Any]]]:
    """The computed drive variants of the machine a catalogue card stands for,
    or ``None`` (no record / no usable variant - the card is served unchanged)."""
    hit = _pick(card_name, length_mm, records)
    if hit is None:
        return None
    return list(hit[2].get("pwm_variants") or []) or None


def card_index() -> Dict[str, Dict[str, str]]:
    """``{die: {config: "YYYY-MM-DD"}}`` for every machine that has a FULL
    passport card (a v1 record in the store).  Read-only, served from the mtime
    cache: adding or removing a store file changes the answer on the next call,
    with no list of names anywhere."""
    out: Dict[str, Dict[str, str]] = {}
    for die, cfg, rec in _records():
        if rec.get("full"):
            out.setdefault(die, {})[cfg] = str(rec.get("date") or "")
    return out


def card_date(die: str, config: str) -> Optional[str]:
    """The date of one configuration's full card, or None (it has none)."""
    return card_index().get(str(die), {}).get(str(config))


def card_of(card_name: Optional[str], length_mm: Optional[float] = None
            ) -> Optional[Dict[str, str]]:
    """``{"die", "config", "date"}`` of the full card behind a catalogue card
    (matched like :func:`variants_for`), or None."""
    hit = _pick(card_name, length_mm)
    if hit is None or not hit[2].get("full"):
        return None
    return {"die": hit[0], "config": hit[1], "date": str(hit[2].get("date") or "")}


def attach(motors: List[Dict[str, Any]]) -> None:
    """Add ``pwm_variants`` to the passport block of every card that has a
    record, in place (the cards are request-local copies).  Nothing else of a
    card is touched; a card whose passport already carries variants keeps them."""
    recs = _records()                       # once per call, not once per card
    for m in motors:
        sp = m.get("passport")
        if not isinstance(sp, dict) or sp.get("pwm_variants"):
            continue
        inner = sp.get("passport") if isinstance(sp.get("passport"), dict) else {}
        try:
            L = float(inner.get("L0_mm"))
        except (TypeError, ValueError):
            L = None
        vs = variants_for(m.get("name"), L, recs)
        if vs:
            m["passport"] = {**sp, "pwm_variants": vs}
