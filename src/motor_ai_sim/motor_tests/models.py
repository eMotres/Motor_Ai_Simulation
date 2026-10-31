"""One test record: validation and normalisation.  Every input is validated loudly (``TestInputError`` -> 422).

Record (stored and returned as is)::

    {"id", "kind": "parameters" | "free_run" | "load", "date": "YYYY-MM-DD", "title", "operator",
     "instrument", "profile", "note",
     "conditions": {"ambient_c", "humidity_pct", "rotor": "in" | "out", "state", "temp_point",
                    "magnet_temp_c", "winding_temp_c"},
     "leads": {"count", "length_mm", "awg" | "area_mm2"},          # the cables between the winding and the meter
     "documents": [production document id, ...],
     # parameters
     "resistance": {"temp_c", "pairs": [{"pair": "1-2", "value_mohm"}]},
     "inductance": {"freq_hz", "level_v", "pairs": [{"pair", "value_uh", "rotor_angle_deg"}]},
     "lcr": [{"pair", "mode": "series" | "parallel", "freq_hz", "level_v", "quantity", "value", "unit"}],
     "insulation": [{"voltage_v", "resistance_mohm", "more_than"}],
     "hipot": [{"voltage_v", "leakage_ma", "time_s", "supply": "ac" | "dc"}],
     "surge": [{"voltage_v", "result": "ok" | "fail", "note"}],
     "weight": [{"what", "mass_g"}],
     "dimensions": [{"what", "value_mm", "nominal_mm"}],
     # free_run: rows of {"rpm_set", "rpm", "shaft_power_w", "v_ll_pp_v", "v_ll_rms_v", "temp_c", "magnet_temp_c"}
     # load:     rows of {"rpm", "torque_nm", "i_phase_a", "v_dc_v", "i_dc_a", "eff_pct",
     #                    "temp_winding_c", "temp_housing_c", "temp_magnet_c", "duration_s"}
     "rows": [...],
     "created_by", "created_at", "updated_by", "updated_at"}
"""
from __future__ import annotations

import datetime
import math
import re
import secrets
from typing import Any, Dict, List, Optional, Tuple

from motor_ai_sim.motor_tests import KINDS

MAX_ROWS = 500
MAX_TEXT = 300
MAX_NOTE = 4000


class TestInputError(ValueError):
    """A record that cannot be stored; the message names the field."""


# (key, min, max) per numeric field; None = unbounded on that side
_RANGES: Dict[str, Tuple[Optional[float], Optional[float]]] = {
    "rpm": (0, 300000), "rpm_set": (0, 300000), "rpm_actual": (0, 300000),
    "shaft_power_w": (-1e7, 1e7), "v_ll_pp_v": (0, 1e5), "v_ll_rms_v": (0, 1e5),
    "temp_c": (-80, 450), "magnet_temp_c": (-80, 450), "winding_temp_c": (-80, 450), "ambient_c": (-80, 120),
    "temp_winding_c": (-80, 450), "temp_housing_c": (-80, 450), "temp_magnet_c": (-80, 450),
    "humidity_pct": (0, 100), "torque_nm": (-1e6, 1e6), "i_phase_a": (0, 1e5), "v_dc_v": (0, 1e5),
    "i_dc_a": (-1e5, 1e5), "eff_pct": (0, 100), "duration_s": (0, 1e7),
    "value_mohm": (0, 1e9), "value_uh": (0, 1e9), "rotor_angle_deg": (-720, 720), "freq_hz": (0, 1e8),
    "level_v": (0, 1e4), "voltage_v": (0, 1e6), "resistance_mohm": (0, 1e12), "leakage_ma": (0, 1e5),
    "time_s": (0, 1e6), "mass_g": (0, 1e8), "value_mm": (0, 1e6), "nominal_mm": (0, 1e6),
    "length_mm": (0, 1e6), "awg": (-3, 46), "area_mm2": (0, 1e4), "count": (0, 100), "value": (None, None),
}

FREE_RUN_KEYS = ("rpm_set", "rpm", "shaft_power_w", "v_ll_pp_v", "v_ll_rms_v", "temp_c", "magnet_temp_c")
LOAD_KEYS = ("rpm", "torque_nm", "i_phase_a", "v_dc_v", "i_dc_a", "eff_pct", "temp_winding_c", "temp_housing_c",
             "temp_magnet_c", "duration_s")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PAIR_RE = re.compile(r"^[A-Za-z0-9]{1,4}\s*-\s*[A-Za-z0-9]{1,4}$")


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_id() -> str:
    return "test_" + secrets.token_urlsafe(9)


def _text(v: Any, where: str, limit: int = MAX_TEXT) -> str:
    if v is None:
        return ""
    if not isinstance(v, (str, int, float)) or isinstance(v, bool):
        raise TestInputError(f"{where}: text expected")
    s = str(v).strip()
    if len(s) > limit:
        raise TestInputError(f"{where}: at most {limit} characters")
    return s


def _num(v: Any, key: str, where: str, *, required: bool = False) -> Optional[float]:
    if v is None or (isinstance(v, str) and v.strip() in ("", "-", "—")):
        if required:
            raise TestInputError(f"{where}.{key}: a number is required")
        return None
    if isinstance(v, bool):
        raise TestInputError(f"{where}.{key}: a number is expected")
    try:
        f = float(str(v).replace(",", ".")) if isinstance(v, str) else float(v)
    except (TypeError, ValueError):
        raise TestInputError(f"{where}.{key}: '{v}' is not a number")
    if not math.isfinite(f):
        raise TestInputError(f"{where}.{key}: not a finite number")
    lo, hi = _RANGES.get(key, (None, None))
    if (lo is not None and f < lo) or (hi is not None and f > hi):
        raise TestInputError(f"{where}.{key}: {f:g} is outside {lo:g} .. {hi:g}")
    return f


def _nums(src: Any, keys, where: str, required=()) -> Dict[str, float]:
    src = src if isinstance(src, dict) else {}
    out = {}
    for k in keys:
        v = _num(src.get(k), k, where, required=k in required)
        if v is not None:
            out[k] = v
    return out


def _list(v: Any, where: str) -> List[Any]:
    if v is None:
        return []
    if not isinstance(v, list):
        raise TestInputError(f"{where}: a list is expected")
    if len(v) > MAX_ROWS:
        raise TestInputError(f"{where}: at most {MAX_ROWS} rows")
    return v


def _pair(v: Any, where: str) -> str:
    s = _text(v, where, 12)
    if s and not _PAIR_RE.match(s):
        raise TestInputError(f"{where}: a terminal pair like '1-2' is expected")
    return re.sub(r"\s+", "", s)


def _choice(v: Any, allowed, where: str, default: Optional[str] = None) -> Optional[str]:
    if v in (None, ""):
        return default
    s = str(v).strip().lower()
    if s not in allowed:
        raise TestInputError(f"{where}: one of {', '.join(allowed)}")
    return s


def _conditions(c: Any) -> Dict[str, Any]:
    c = c if isinstance(c, dict) else {}
    out: Dict[str, Any] = _nums(c, ("ambient_c", "humidity_pct", "magnet_temp_c", "winding_temp_c"), "conditions")
    rotor = _choice(c.get("rotor"), ("in", "out"), "conditions.rotor")
    if rotor:
        out["rotor"] = rotor
    for k in ("state", "temp_point"):
        t = _text(c.get(k), f"conditions.{k}")
        if t:
            out[k] = t
    return out


def _leads(v: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(v, dict) or not v:
        return None
    out = _nums(v, ("count", "length_mm", "awg", "area_mm2"), "leads")
    if not out.get("length_mm"):
        raise TestInputError("leads.length_mm: the cable length is required")
    if out.get("awg") is None and not out.get("area_mm2"):
        raise TestInputError("leads: give the AWG or the cross-section in mm2")
    return out


def _parameters(raw: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    r = raw.get("resistance")
    if isinstance(r, dict) and r.get("pairs"):
        pairs = []
        for i, p in enumerate(_list(r.get("pairs"), "resistance.pairs")):
            w = f"resistance.pairs[{i}]"
            pairs.append({"pair": _pair((p or {}).get("pair"), w + ".pair"),
                          **_nums(p, ("value_mohm",), w, required=("value_mohm",))})
        out["resistance"] = {"pairs": pairs, **_nums(r, ("temp_c",), "resistance")}
    ind = raw.get("inductance")
    if isinstance(ind, dict) and ind.get("pairs"):
        pairs = []
        for i, p in enumerate(_list(ind.get("pairs"), "inductance.pairs")):
            w = f"inductance.pairs[{i}]"
            pairs.append({"pair": _pair((p or {}).get("pair"), w + ".pair"),
                          **_nums(p, ("value_uh", "rotor_angle_deg"), w, required=("value_uh",))})
        out["inductance"] = {"pairs": pairs, **_nums(ind, ("freq_hz", "level_v"), "inductance")}
    rows = []
    for i, p in enumerate(_list(raw.get("lcr"), "lcr")):
        w = f"lcr[{i}]"
        p = p if isinstance(p, dict) else {}
        rows.append({"pair": _pair(p.get("pair"), w + ".pair"),
                     "mode": _choice(p.get("mode"), ("series", "parallel"), w + ".mode", "series"),
                     "quantity": _text(p.get("quantity"), w + ".quantity", 8) or "R",
                     "unit": _text(p.get("unit"), w + ".unit", 8),
                     **_nums(p, ("freq_hz", "level_v", "value"), w, required=("value",))})
    if rows:
        out["lcr"] = rows
    rows = []
    for i, p in enumerate(_list(raw.get("insulation"), "insulation")):
        w = f"insulation[{i}]"
        rows.append({**_nums(p, ("voltage_v", "resistance_mohm"), w, required=("voltage_v", "resistance_mohm")),
                     "more_than": bool((p or {}).get("more_than"))})
    if rows:
        out["insulation"] = rows
    rows = []
    for i, p in enumerate(_list(raw.get("hipot"), "hipot")):
        w = f"hipot[{i}]"
        row = _nums(p, ("voltage_v", "leakage_ma", "time_s"), w, required=("voltage_v",))
        supply = _choice((p or {}).get("supply"), ("ac", "dc"), w + ".supply")
        rows.append({**row, **({"supply": supply} if supply else {})})
    if rows:
        out["hipot"] = rows
    rows = []
    for i, p in enumerate(_list(raw.get("surge"), "surge")):
        w = f"surge[{i}]"
        rows.append({**_nums(p, ("voltage_v",), w, required=("voltage_v",)),
                     "result": _choice((p or {}).get("result"), ("ok", "fail"), w + ".result", "ok"),
                     "note": _text((p or {}).get("note"), w + ".note")})
    if rows:
        out["surge"] = rows
    rows = []
    for i, p in enumerate(_list(raw.get("weight"), "weight")):
        w = f"weight[{i}]"
        rows.append({"what": _text((p or {}).get("what"), w + ".what") or "motor",
                     **_nums(p, ("mass_g",), w, required=("mass_g",))})
    if rows:
        out["weight"] = rows
    rows = []
    for i, p in enumerate(_list(raw.get("dimensions"), "dimensions")):
        w = f"dimensions[{i}]"
        what = _text((p or {}).get("what"), w + ".what")
        if not what:
            raise TestInputError(f"{w}.what: name the dimension")
        rows.append({"what": what, **_nums(p, ("value_mm", "nominal_mm"), w, required=("value_mm",))})
    if rows:
        out["dimensions"] = rows
    # no-load cogging (owner 2026-10-31: «он должен быть в тестах»): the bench peak-to-peak, or an entry that names the
    # cogging check with the bench value still to come (pending) so the model value is on the page before the bench
    cg = raw.get("cogging")
    if isinstance(cg, dict) and cg:
        c = _nums(cg, ("pkpk_nm", "magnet_temp_c"), "cogging")
        pending = bool(cg.get("pending")) and c.get("pkpk_nm") is None
        if c.get("pkpk_nm") is None and not pending:
            raise TestInputError("cogging.pkpk_nm: enter the peak-to-peak cogging torque (or mark it pending)")
        out["cogging"] = {**c, **({"pending": True} if pending else {})}
    if not out:
        raise TestInputError("parameters: enter at least one measurement")
    return out


def _rows(raw: Dict[str, Any], keys, required: Tuple[str, ...]) -> List[Dict[str, float]]:
    rows = []
    for i, p in enumerate(_list(raw.get("rows"), "rows")):
        row = _nums(p, keys, f"rows[{i}]", required=required)
        if row:
            rows.append(row)
    if not rows:
        raise TestInputError("rows: enter at least one row")
    return rows


def normalize(raw: Any, *, who: str, previous: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """A clean record from a request body; ``previous`` keeps id / created_* on an edit."""
    if not isinstance(raw, dict):
        raise TestInputError("a JSON object is expected")
    kind = _choice(raw.get("kind") if previous is None else (raw.get("kind") or previous.get("kind")), KINDS, "kind")
    if not kind:
        raise TestInputError(f"kind: one of {', '.join(KINDS)}")
    date = _text(raw.get("date"), "date", 10)
    if not _DATE_RE.match(date):
        raise TestInputError("date: YYYY-MM-DD is expected")
    try:
        datetime.date.fromisoformat(date)
    except ValueError:
        raise TestInputError("date: not a calendar date")
    docs = []
    for i, d in enumerate(_list(raw.get("documents"), "documents")):
        s = _text(d, f"documents[{i}]", 80)
        if not re.match(r"^[A-Za-z0-9_\-]{4,80}$", s):
            raise TestInputError(f"documents[{i}]: a production document id is expected")
        docs.append(s)
    rec: Dict[str, Any] = {
        "id": previous["id"] if previous else new_id(),
        "kind": kind, "date": date,
        "title": _text(raw.get("title"), "title"),
        "operator": _text(raw.get("operator"), "operator"),
        "instrument": _text(raw.get("instrument"), "instrument"),
        "profile": _text(raw.get("profile"), "profile"),
        "note": _text(raw.get("note"), "note", MAX_NOTE),
        "conditions": _conditions(raw.get("conditions")),
        "documents": docs,
    }
    leads = _leads(raw.get("leads"))
    if leads:
        rec["leads"] = leads
    if kind == "parameters":
        rec.update(_parameters(raw))
    elif kind == "free_run":
        rec["rows"] = _rows(raw, FREE_RUN_KEYS, ("rpm",))
    else:
        rec["rows"] = _rows(raw, LOAD_KEYS, ("rpm",))
    if previous:
        rec["created_by"], rec["created_at"] = previous.get("created_by"), previous.get("created_at")
        rec["updated_by"], rec["updated_at"] = who, now()
    else:
        rec["created_by"], rec["created_at"] = who, now()
    return rec
