"""The shipping rate table of the cost estimate (owner 2026-11-22: "automatic calculation of delivery from Shanghai to the customer
place").  Pure: a table kept in ``shipping_rate_table.json`` (zones by country, weight breaks, volumetric weight, fuel surcharge,
service factor), no network, no paid API, no credentials.

The table is NOT a carrier tariff.  Zone ``SA`` is calibrated on the one real quote we have (Shanghai -> Buenos Aires, 2 x 75 kg,
air freight CNY 20 169.14 excl. VAT, 16 days; ``test_cost_shipping_rates`` pins that it is reproduced); the other zones are the same
curve times an ESTIMATED zone factor and say ``basis: "estimate"`` in every answer until a real quote for that country is entered
as a lane (a lane always wins over the table).  The admin can overwrite any shipping amount; the line keeps ``auto`` / ``manual``
and the table date.  Duties and import VAT are never included (DAP)."""
from __future__ import annotations

import copy
import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

TABLE_PATH = Path(__file__).with_name("shipping_rate_table.json")


@lru_cache(maxsize=2)
def _cached(path: str, mtime: float) -> dict:
    with open(path, encoding="utf-8") as fh:
        table = json.load(fh)
    seen: Dict[str, str] = {}
    for zid, zone in (table.get("zones") or {}).items():
        for c in zone.get("countries") or []:
            if c in seen:
                raise ValueError("shipping rate table: %s is in zones %s and %s" % (c, seen[c], zid))
            seen[c] = zid
    brk = table.get("breaks") or []
    if not brk or [b["from_kg"] for b in brk] != sorted(b["from_kg"] for b in brk) or brk[0]["from_kg"] != 0:
        raise ValueError("shipping rate table: breaks must start at 0 kg and ascend")
    return table


def load_table(path: Optional[Path] = None) -> dict:
    p = Path(path or TABLE_PATH)
    return copy.deepcopy(_cached(str(p), p.stat().st_mtime))


def zone_of(country: str, table: dict) -> Optional[Tuple[str, dict]]:
    country = str(country or "").upper()
    for zid, zone in (table.get("zones") or {}).items():
        if country in (zone.get("countries") or []):
            return zid, zone
    return None


def default_box_cm(parcel_kg: float, table: dict) -> Tuple[float, float, float]:
    """A parcel's box from its weight when none is given: the volume at the table's crate density, as a 3 : 2 : 2 crate (the real
    2 x 75 kg shipment was 60 x 60 x 40 cm = 520 kg/m3)."""
    vol_cm3 = float(parcel_kg) / float(table["default_density_kg_m3"]) * 1e6
    k = (vol_cm3 / 12.0) ** (1.0 / 3.0)
    return round(3 * k, 1), round(2 * k, 1), round(2 * k, 1)


def chargeable_kg(parcels: List[dict], table: dict) -> Tuple[float, float, float]:
    """``(chargeable, actual, volumetric)`` kg of a shipment: per parcel the larger of its gross weight and L x W x H / divisor,
    summed."""
    div = float(table["volumetric_divisor"])
    actual = vol = charge = 0.0
    for p in parcels:
        w = float(p["weight_kg"])
        l, wd, h = p["box_cm"]
        v = float(l) * float(wd) * float(h) / div
        actual += w
        vol += v
        charge += max(w, v)
    return charge, actual, vol


def _zone_basis(zid: str, zone: dict, country: str, charge: float, table: dict, service: Optional[str] = None) -> str:
    """``calibrated`` only AT the one real quote (its country and its chargeable weight within the tolerance); the rest of a
    calibrated zone is an ``extrapolated`` use of that curve; every other zone is an ``estimate``."""
    if (zone.get("basis") or "estimate") != "calibrated":
        return zone.get("basis") or "estimate"
    cal = table.get("calibration") or {}
    if cal.get("service") and service != cal["service"]:
        return "extrapolated"          # the one real quote is for ONE service: the other service has the same factor, not its own quote
    if (cal.get("zone") == zid and str(country or "").upper() == str(cal.get("country") or "").upper() and cal.get("chargeable_kg")
            and abs(charge - float(cal["chargeable_kg"])) <= float(cal["chargeable_kg"]) * float(cal.get("tolerance_pct") or 0) / 100.0):
        return "calibrated"
    return "extrapolated"


def quote(country: str, parcels: List[dict], table: dict, service: Optional[str] = None) -> Tuple[Optional[dict], str]:
    """The table price of a shipment from the table's origin to ``country``: ``({amount (table currency), transit_days, zone, ...},
    "")`` or ``(None, reason)``.  amount = chargeable kg x base rate x zone factor x weight-break multiplier x service factor,
    at least the minimum charge, + the fuel surcharge."""
    found = zone_of(country, table)
    if found is None:
        return None, "the rate table has no zone for %s: add a DHL quote for it" % (str(country or "?").upper())
    zid, zone = found
    service = service or table.get("default_service") or "express"
    svc = (table.get("services") or {}).get(service)
    if svc is None:
        return None, "unknown shipping service %r" % service
    if not parcels:
        return None, "no parcels"
    charge, actual, vol = chargeable_kg(parcels, table)
    mult = [b for b in table["breaks"] if charge >= float(b["from_kg"])][-1]["multiplier"]
    base = charge * float(table["base_cny_per_kg"]) * float(zone["factor"]) * float(mult) * float(svc["factor"])
    base = max(base, float(table["min_charge_cny"]))
    fuel = float(table["fuel_surcharge_pct"])
    amount = round(base * (1 + fuel / 100.0), 2)
    return {"amount": amount, "currency": table["currency"], "transit_days": int(zone["transit_days"]), "zone": zid,
            "zone_basis": _zone_basis(zid, zone, country, charge, table, service), "chargeable_kg": round(charge, 2), "actual_kg": round(actual, 2),
            "volumetric_kg": round(vol, 2), "parcels": len(parcels), "service": service, "fuel_pct": fuel,
            "table_date": table["date"]}, ""
