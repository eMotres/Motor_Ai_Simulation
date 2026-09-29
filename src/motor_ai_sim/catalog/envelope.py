"""The common card ENVELOPE every reference catalogue shares (stage 1, 2026-09-28).

Owner-approved proposal ``catalogs_unified_proposal_2026-09-28.md`` §2: one
shape around each kind's own body, so the UI, the tests and a reviewer read
provenance the same way for a bearing, a lubricant and a power device::

    {id, kind, manufacturer, part_number, description, status,
     sources: [{id, doc, url, file, page, rev, read}],
     units: {field: unit},
     body: {...the kind's own fields, exactly as its loader reads them...},
     prov: {field: {type, src, verify, note}},
     validation: [{what, ref, model, delta_pct, doc, ...}],
     revision: {n, date, by, why},
     flags: {n_verify, n_estimate, n_null}}

The body is NEVER rewritten: the adapters hand the kind's own loader the same
mapping it read before the envelope existed, so the numbers cannot move
(``tests/test_catalog_envelope.py`` pins that bit for bit against the
pre-envelope files).

Rule (proposal §2): a field with no ``prov`` entry is ``datasheet`` from
``sources[0]``; ``null`` means "not published".
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

#: Card life cycle.  ``draft`` cards are imported but not reviewed.
STATUSES = ("draft", "active", "validated", "deprecated")

#: What a number rests on.  Badge letters are the UI's (D / M / E / ∂).
PROV_TYPES = ("datasheet", "measured", "estimate", "derived")

KINDS = ("bearing", "lubricant", "device")


def _clean(d: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in d.items() if v not in (None, "", [], {})}


def make_envelope(*, id: str, kind: str, body: Dict[str, Any],
                  manufacturer: Optional[str] = None,
                  part_number: Optional[str] = None,
                  description: Optional[str] = None,
                  status: str = "active",
                  sources: Optional[List[Dict[str, Any]]] = None,
                  units: Optional[Dict[str, str]] = None,
                  prov: Optional[Dict[str, Dict[str, Any]]] = None,
                  validation: Optional[List[Dict[str, Any]]] = None,
                  revision: Optional[Dict[str, Any]] = None,
                  file: Optional[str] = None) -> Dict[str, Any]:
    prov = {k: _clean(dict(v or {})) for k, v in (prov or {}).items()}
    env = {
        "id": str(id), "kind": kind,
        "manufacturer": manufacturer, "part_number": part_number or str(id),
        "description": description or "",
        "status": status if status in STATUSES else "active",
        "sources": [_clean(dict(s)) for s in (sources or [])],
        "units": dict(units or {}),
        "body": body,
        "prov": prov,
        "validation": list(validation or []),
        "revision": dict(revision or {}),
        "file": file,
    }
    env["flags"] = {
        "n_verify": sum(1 for v in prov.values() if v.get("verify")),
        "n_estimate": sum(1 for v in prov.values() if v.get("type") == "estimate"),
        "n_measured": sum(1 for v in prov.values() if v.get("type") == "measured"),
    }
    return env


def field_prov(env: Dict[str, Any], field: str) -> Dict[str, Any]:
    """The provenance of one (dotted) field, the default rule applied."""
    p = (env.get("prov") or {}).get(field)
    if p:
        return dict(p)
    srcs = env.get("sources") or []
    return {"type": "datasheet", "src": srcs[0].get("id") if srcs else None,
            "default": True}


def validate_envelope(env: Dict[str, Any]) -> List[str]:
    """Everything structurally wrong with an envelope (empty = fine)."""
    bad: List[str] = []
    if not str(env.get("id") or "").strip():
        bad.append("id is required")
    if env.get("kind") not in KINDS:
        bad.append(f"kind must be one of {KINDS}")
    if env.get("status") not in STATUSES:
        bad.append(f"status must be one of {STATUSES}")
    ids = {s.get("id") for s in env.get("sources") or []}
    for f, p in (env.get("prov") or {}).items():
        t = p.get("type")
        if t not in PROV_TYPES:
            bad.append(f"prov.{f}.type {t!r} must be one of {PROV_TYPES}")
        if p.get("src") and p["src"] not in ids:
            bad.append(f"prov.{f}.src {p['src']!r} is not one of the card's sources")
        if t == "estimate" and not str(p.get("note") or "").strip():
            bad.append(f"prov.{f}: an estimate needs a note saying what it rests on")
    return bad


def flat_fields(body: Dict[str, Any], prefix: str = "",
                depth: int = 1) -> Iterable[str]:
    """Dotted names of the body's scalar fields, one level of nesting deep —
    the granularity ``prov`` is written at."""
    for k, v in body.items():
        name = f"{prefix}{k}"
        if isinstance(v, dict) and depth > 0:
            yield from flat_fields(v, name + ".", depth - 1)
        else:
            yield name
