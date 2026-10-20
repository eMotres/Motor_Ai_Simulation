"""Server-held audited input basis of reviewed Configure reference cards.

WHY HERE AND NOT IN THE WEB BUNDLE (independent review F4, 2026-10-20).  The
audited solve basis of the 85 L13 owner-preview card (stack, rpm, current, the
winding / magnet temperatures, the source-record hash) used to be constants in
`web/src/lib/configuratorReferenceTemperature.ts`, and the card id itself was a
literal in two web modules.  Everything in the JS bundle is public, whatever the
API's visibility rule says, so the facts moved here and ride the existing
`GET /api/catalog/references` answer, which is already filtered per caller.

The wire shape, per card, only for the cards that need it::

    "reference_basis": {"required": true,
                        "basis": {windingC, magnetC, steelC, stackLengthMm, rpm,
                                  currentA, sourceRecord, sourceSha256,
                                  coldConstantsAvailable} | null}

`required` makes Configure FAIL CLOSED (no cold constants, no stack
coefficients) when the basis is absent or malformed - the behaviour the web had
for the exact card id.  Cards that are not listed get no key at all, so the
answer for every other motor is byte-identical to before.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

#: candidate_sha256 (lower case) -> the audited basis.
_BASIS_BY_CANDIDATE: Dict[str, Dict[str, Any]] = {
    "7d333cee34b8783a4b581d19f1ca7733659e0fb79e758ca0f2d53be10cf056df": {
        "windingC": 180,
        "magnetC": 150,
        "steelC": None,          # no independently recorded steel temperature in the source
        "stackLengthMm": 13,
        "rpm": 1000,
        "currentA": 25.88,
        "sourceRecord": "lchk_rated_72steps",
        "sourceSha256": "03f600ce90b9462d53cc09bf88f9e55c375c1e63c6d37deb14d2c1b84e87b1b4",
        "coldConstantsAvailable": False,
    },
}

#: Catalog card ids that must fail closed even when their provenance is missing.
_REQUIRED_CARD_IDS = frozenset({"candidate_d85_ciano28_85_20sw1200_l13"})


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
    for entry in entries:
        cand = _candidate_of(entry)
        basis = _BASIS_BY_CANDIDATE.get(cand) if cand else None
        if basis is None and str(entry.get("id") or "") not in _REQUIRED_CARD_IDS:
            continue
        entry["reference_basis"] = {"required": True,
                                    "basis": dict(basis) if basis is not None else None}
