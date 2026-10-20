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
before.

Which cards REQUIRE a basis does not depend on their record file being there
(re-audit #2, R7): the built-in REQUIRED_*_DIGESTS name them by SHA-256 digest of
the card id / candidate pin, and a record names its own card as well.  A required
card whose record is missing, null or ambiguous gets ``required: true, basis: null``.
If the data folder is missing, empty, holds an invalid file (also an unsupported
``schema``) or claims one pin / card id twice, no pinned card can be matched with
confidence, so every card that carries a candidate pin fails closed too.
`store_problems()` lists what is wrong with the real store (empty list = complete).
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

log = logging.getLogger(__name__)

SCHEMA = "reference-basis-1"

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


def _sha(x: str) -> str:
    return hashlib.sha256(x.encode("utf-8")).hexdigest()


def _norm_id(v: Any) -> str:
    """Card id as compared: trimmed, lower case, without the web's ``cat:`` prefix."""
    t = str(v or "").strip().lower()
    return t[4:] if t.startswith("cat:") else t


#: Cards that REQUIRE an audited basis, as SHA-256 digests (lower-case card id / candidate pin): not data, and not
#: a secret either (the pin is a 256-bit hash).  The requirement therefore survives a missing record file: the loader
#: answers ``required: true, basis: null`` and Configure fails closed.  A new audited card is added here AND as a record.
REQUIRED_ID_DIGESTS: frozenset = frozenset({
    "4d79db9394d5c6d51b6ba800d0523a2936cc9477b662a15bde9f4d8bc38d6acd",  # private-data-ok: digest of a card id
})
REQUIRED_PIN_DIGESTS: frozenset = frozenset({
    "be7d7e9afada0c082b2ae453786b94688b0e8f51b5642d3707af6b0ae0e49ab8",  # private-data-ok: digest of a candidate pin
})


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
        if isinstance(raw, dict) and raw.get("schema") == SCHEMA:
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


class _Store:
    """One snapshot of the data folder."""

    def __init__(self) -> None:
        self.by_cand: Dict[str, Optional[Dict[str, Any]]] = {}
        self.ids: set = set()
        self.dup_cands: set = set()          # a pin that two files claim
        self.dup_ids: set = set()            # a card id that two files claim
        self.present = False                 # the folder exists
        self.invalid: List[str] = []         # files that are not valid records
        self.n_records = 0

    @property
    def healthy(self) -> bool:
        """Usable = the folder exists, holds a record, every file is valid and no pin / id is claimed twice."""
        return (self.present and self.n_records > 0 and not self.invalid
                and not self.dup_cands and not self.dup_ids)


def _load() -> _Store:
    """Read the data folder.  An unusable store (`healthy` False) fails every pinned card closed in `attach`."""
    st = _Store()
    root = store_dir()
    if root is None or not root.is_dir():
        return st
    st.present = True
    seen_c: Dict[str, str] = {}
    seen_i: Dict[str, str] = {}
    for f in sorted(root.glob("*.json")):
        rec = _read(f)
        if rec is None:
            st.invalid.append(f.name)
            continue
        st.n_records += 1
        if rec["candidate"]:
            if rec["candidate"] in seen_c:
                st.dup_cands.add(rec["candidate"])
                log.warning("reference basis: pin claimed by both %s and %s - ambiguous, failing closed", seen_c[rec["candidate"]], f.name)
            seen_c.setdefault(rec["candidate"], f.name)
            st.by_cand[rec["candidate"]] = rec["basis"]
        if rec["card_id"]:
            key = _norm_id(rec["card_id"])
            if key in seen_i:
                st.dup_ids.add(key)
                log.warning("reference basis: card id claimed by both %s and %s - ambiguous, failing closed", seen_i[key], f.name)
            seen_i.setdefault(key, f.name)
            st.ids.add(key)
    return st


def _candidate_of(entry: Dict[str, Any]) -> Optional[str]:
    """The candidate pin on the card: top level, passport wrapper or the passport itself."""
    wrapper = entry.get("passport") if isinstance(entry.get("passport"), dict) else {}
    inner = wrapper.get("passport") if isinstance(wrapper.get("passport"), dict) else {}
    for holder in (entry, wrapper, inner):
        prov = holder.get("reference_provenance")
        if isinstance(prov, dict) and isinstance(prov.get("candidate_sha256"), str):
            return prov["candidate_sha256"].lower()
    return None


def _required(entry: Dict[str, Any], cand: Optional[str], st: _Store) -> bool:
    """True when the card needs a basis.  The decision never depends on its record file being present."""
    cid = _norm_id(entry.get("id"))
    return bool(
        (cid and _sha(cid) in REQUIRED_ID_DIGESTS)
        or (cand and _sha(cand) in REQUIRED_PIN_DIGESTS)
        or (cid and cid in st.ids)
        or (cand and cand in st.by_cand)
        or (cand and not st.healthy)                    # unusable store: no pinned card can be matched with confidence
    )


def attach(entries: Iterable[Dict[str, Any]]) -> None:
    """Add ``reference_basis`` to the (already copied) reference entries that need it.

    A card that REQUIRES a basis (its id / pin digest is on the built-in list, or a record names it) gets
    ``required: true`` whether or not its record is present; with no usable record the basis is ``null`` and Configure
    fails closed.  An unusable store (invalid file, duplicate pin / id) gives ``null`` for every card.
    """
    st = _load()
    for entry in entries:
        cand = _candidate_of(entry)
        if not _required(entry, cand, st):
            continue
        cid = _norm_id(entry.get("id"))
        basis = (st.by_cand.get(cand) if cand else None) if st.healthy else None      # an unusable store gives no basis at all
        entry["reference_basis"] = {"required": True,
                                    "basis": dict(basis) if isinstance(basis, dict) else None}


def store_problems() -> List[str]:
    """Completeness check of the real store (run it after every copy, see docs/OPEN_PRIVATE_DATA.md).

    Empty list = the folder exists, every file is a valid record, nothing is claimed twice and every card on the
    built-in required list has its record.
    """
    st = _load()
    out: List[str] = []
    if not st.present:
        return ["no references/basis folder (shared or private checkout)"]
    out += [f"{n}: not a valid basis record" for n in st.invalid]
    out += [f"pin claimed by two files: {c[:8]}.." for c in sorted(st.dup_cands)]
    out += [f"card id claimed by two files: {_sha(c)[:8]}.." for c in sorted(st.dup_ids)]
    if st.n_records == 0:
        out.append("the folder holds no record")
    have_pins = {_sha(c) for c in st.by_cand}
    have_ids = {_sha(i) for i in st.ids}
    out += [f"required pin (digest {d[:8]}..) has no record" for d in sorted(REQUIRED_PIN_DIGESTS - have_pins)]
    out += [f"required card id (digest {d[:8]}..) has no record" for d in sorted(REQUIRED_ID_DIGESTS - have_ids)]
    return out
