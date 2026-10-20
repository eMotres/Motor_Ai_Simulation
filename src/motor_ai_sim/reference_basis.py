"""Server-held audited input basis of reviewed Configure reference cards.

WHY HERE AND NOT IN THE WEB BUNDLE (independent review F4, 2026-10-20).  The
audited solve basis of a reviewed (closed) reference card - stack, rpm, current,
the winding / magnet temperatures, the source-record hash - used to be constants
in the JS bundle.  Everything in the JS bundle is public, whatever the API's
visibility rule says, so the facts moved to the server and ride the existing
`GET /api/catalog/references` answer, which is already filtered per caller.

WHY NOT IN THIS MODULE EITHER (re-audit R4, 2026-10-20).  Python source of the
public repository is public too.  This module therefore holds only the LOADER;
the data is closed-motor data and lives with the other closed-motor data
(docs/OPEN_PRIVATE_DATA.md), the same way the passports do::

    config/references/basis/<name>.json         in the PRIVATE repository
    <shared>/references/basis/<name>.json       on the server (wins when the folder exists)

Without a shared folder the private checkout `MOTOR_AI_SIM_PRIVATE_DATA` points
at is the workstation store.  Each file describes one card::

    {"schema": "reference-basis-1",
     "card_id": "<catalog card id>",              # optional: marks the card required even without a pin
     "candidate_sha256": "<64 hex>",              # the pin the card carries in its reference_provenance
     "basis": {windingC, magnetC, steelC, stackLengthMm, rpm, currentA,
               sourceRecord, sourceSha256, coldConstantsAvailable}}

The wire shape, per card, only for the cards that need it::

    "reference_basis": {"required": true,
                        "basis": {...} | null}

`required` makes Configure FAIL CLOSED (no cold constants, no stack
coefficients) when the basis is absent or malformed.  Cards that are not listed
get no key at all, so the answer for every other motor is byte-identical to
before.  If the data folder is missing or holds an unreadable file, no card can
be matched to a record, so every card that carries a candidate pin fails closed
(``required`` with ``basis: null``) rather than open.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

log = logging.getLogger(__name__)

_REPO_DIR = Path(__file__).resolve().parents[2] / "config" / "references" / "basis"

#: Overridden by tests; a moved path wins outright (the passport-store rule).
_DIR: Path = _REPO_DIR


def store_dir() -> Optional[Path]:
    """``<shared>/references/basis`` on the server, else the private checkout's folder, else ``None``."""
    if _DIR != _REPO_DIR:
        return _DIR
    try:
        from motor_ai_sim.workspace import shared_root
        p = Path(str(shared_root())) / "references" / "basis"
        if p.is_dir():
            return p
    except Exception:                                       # noqa: BLE001
        pass
    from motor_ai_sim.private_data import private_path
    q = private_path("config", "references", "basis")
    return q if q is not None and q.is_dir() else None


def _is_sha256(v: Any) -> bool:
    return isinstance(v, str) and len(v) == 64 and all(c in "0123456789abcdefABCDEF" for c in v)


#: ``{path: ((mtime_ns, size), parsed file or None)}`` - a file is parsed again only when it changed.
_CACHE: Dict[str, Tuple[Tuple[int, int], Optional[Dict[str, Any]]]] = {}


def _read(f: Path) -> Optional[Dict[str, Any]]:
    """The validated record of one file, or ``None`` when it is unreadable / malformed."""
    try:
        st = f.stat()
        sig = (st.st_mtime_ns, st.st_size)
    except OSError:
        _CACHE.pop(str(f), None)
        return None
    hit = _CACHE.get(str(f))
    if hit and hit[0] == sig:
        return hit[1]
    rec: Optional[Dict[str, Any]] = None
    try:
        raw = json.loads(f.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            cand = raw.get("candidate_sha256")
            cid = raw.get("card_id")
            basis = raw.get("basis")
            ok = ((cand is None or _is_sha256(cand)) and (cid is None or (isinstance(cid, str) and cid))
                  and (cand is not None or cid is not None) and (basis is None or isinstance(basis, dict)))
            if ok:
                rec = {"candidate": cand.lower() if cand else None, "card_id": cid, "basis": basis}
    except Exception as exc:                                # noqa: BLE001
        log.warning("reference basis: %s is unreadable: %s", f, exc)
    if rec is None:
        log.warning("reference basis: %s is not a valid basis record", f)
    _CACHE[str(f)] = (sig, rec)
    return rec


def _load() -> Tuple[Dict[str, Optional[Dict[str, Any]]], frozenset, bool]:
    """``(basis by candidate pin, required card ids, store usable)``.

    *Usable* = the folder exists and every ``*.json`` in it is a valid record; otherwise no card can be
    matched with confidence and `attach` fails the pinned cards closed.
    """
    root = store_dir()
    if root is None or not root.is_dir():
        return {}, frozenset(), False
    by_cand: Dict[str, Optional[Dict[str, Any]]] = {}
    ids: List[str] = []
    healthy = True
    for f in sorted(root.glob("*.json")):
        rec = _read(f)
        if rec is None:
            healthy = False
            continue
        if rec["candidate"]:
            by_cand[rec["candidate"]] = rec["basis"]
        if rec["card_id"]:
            ids.append(rec["card_id"])
    return by_cand, frozenset(ids), healthy


def _candidate_of(entry: Dict[str, Any]) -> Optional[str]:
    """The candidate pin on the card: top level, passport wrapper or the passport itself."""
    wrapper = entry.get("passport") if isinstance(entry.get("passport"), dict) else {}
    inner = wrapper.get("passport") if isinstance(wrapper.get("passport"), dict) else {}
    for holder in (entry, wrapper, inner):
        prov = holder.get("reference_provenance")
        if isinstance(prov, dict) and isinstance(prov.get("candidate_sha256"), str):
            return prov["candidate_sha256"].lower()
    return None


def attach(entries: Iterable[Dict[str, Any]]) -> None:
    """Add ``reference_basis`` to the (already copied) reference entries that need it."""
    by_cand, required_ids, healthy = _load()
    for entry in entries:
        cand = _candidate_of(entry)
        basis = by_cand.get(cand) if cand else None
        listed = bool(cand) and cand in by_cand
        if not listed and str(entry.get("id") or "") not in required_ids and not (cand and not healthy):
            continue
        entry["reference_basis"] = {"required": True,
                                    "basis": dict(basis) if isinstance(basis, dict) else None}
