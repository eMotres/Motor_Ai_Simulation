"""Read-only export of the companies catalogue and the production motor prices for MOTRES ERP (owner 2026-10-18:
"the company catalogue must be synchronised with the ERP, and the motor price we compute in production must reach the
ERP").  The ERP pulls; nothing here writes.  Bank details are never stored in the directory, so none can leave here.
"""
from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

SCHEMA = "aerostator-erp-export-1"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ── companies + people ───────────────────────────────────────────────────────

_COMPANY_KEYS = ("legal_name", "local_name", "country", "address", "phone", "website", "reg_no", "vat_no", "roles",
                 "notes", "domains", "source", "created", "updated")
_DOC_KEYS = ("id", "number", "date", "kind", "currency", "total", "status", "paid_on", "terms", "lines")


def directory_export() -> Dict[str, Any]:
    from motor_ai_sim.directory import store
    companies = store.load_companies()
    people = {p["id"]: p for p in store.load_people() if isinstance(p, dict) and p.get("id")}
    affs = [a for a in store.load_affiliations() if isinstance(a, dict)]
    out = []
    for c in companies:
        if not isinstance(c, dict) or not c.get("id"):
            continue
        row: Dict[str, Any] = {"id": c["id"], **{k: c.get(k) for k in _COMPANY_KEYS}}
        row["nda_status"] = (c.get("nda") or {}).get("status")
        row["verification_status"] = (c.get("verification") or {}).get("status")
        row["documents"] = [{k: d.get(k) for k in _DOC_KEYS} for d in c.get("documents") or [] if isinstance(d, dict)]
        row["people"] = []
        for a in affs:
            if a.get("company_id") != c["id"] or a.get("status") == "ended":
                continue
            p = people.get(a.get("person_id"))
            if not p:
                continue
            row["people"].append({"id": p["id"], "name": p.get("name"), "emails": p.get("emails") or [],
                                  "phone": p.get("phone"), "title": a.get("title"), "status": a.get("status"),
                                  "updated": max(str(p.get("updated") or ""), str(a.get("updated") or ""))})
        out.append(row)
    return {"schema": SCHEMA, "generated_at": _now(), "companies": out}


# ── production motor prices ──────────────────────────────────────────────────

def _price_of(die: str, config: str, production: Dict[str, Any]) -> Dict[str, Any]:
    from motor_ai_sim.production import models, store
    costs = store.load_file(die, config, "costs.json") or {}
    bom_rows = (store.load_file(die, config, "bom.json") or {}).get("rows", [])
    cost_map = {r["part_id"]: r for r in costs.get("rows", []) if isinstance(r, dict) and r.get("part_id")}
    rows = []
    for br in bom_rows:
        if not isinstance(br, dict):
            continue
        pid = str(br.get("part_id") or "")
        cr = cost_map.get(pid)
        if cr:
            nr = models.normalize_cost_row(cr)
            nr["part_id"] = pid
            rows.append(nr)
        else:
            rows.append({"part_id": pid, "unit_price": None, "currency": "CNY"})
    qty = max(0, int(production.get("order_quantity") or 0))
    s = models.compute_cost_summary(bom_rows, rows, costs.get("extras", []), costs.get("margin_pct", 0.0), qty)
    currencies = Counter(str(r.get("currency") or "CNY") for r in rows if r.get("unit_price") is not None)
    title = production.get("title") if isinstance(production.get("title"), dict) else {}
    return {"id": f"{die}/{config}", "die": die, "config": config,
            "title": title.get("en") or title.get("zh") or f"{die} {config}",
            "status": production.get("status"), "revision": production.get("current_revision"),
            "currency": currencies.most_common(1)[0][0] if currencies else "CNY",
            "mixed_currencies": len(currencies) > 1,
            "order_quantity": qty,
            "unit_cost_per_motor": s["cost_per_motor"], "factory_price_per_motor": s["price_per_motor"],
            "margin_pct": s["margin_pct"], "parts_subtotal": s["parts_subtotal"],
            "extra_subtotal": s["extra_subtotal"], "missing_prices_count": s["missing_prices_count"],
            "updated_at": production.get("updated_at")}


def motor_prices_export() -> Dict[str, Any]:
    from motor_ai_sim.production import store
    root: Path = store.production_root()
    out: List[Dict[str, Any]] = []
    if root.is_dir():
        for p in sorted(root.glob("*/*/production.json")):
            try:
                prod = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            die, config = str(prod.get("die") or ""), str(prod.get("config") or "")
            if die and config:
                out.append(_price_of(die, config, prod))
    return {"schema": SCHEMA, "generated_at": _now(), "productions": out,
            "note": "unit_cost_per_motor = sum of the BOM line prices per motor + extras; factory_price_per_motor = "
                    "cost / (1 - margin); prices are per motor in the production's currency"}
