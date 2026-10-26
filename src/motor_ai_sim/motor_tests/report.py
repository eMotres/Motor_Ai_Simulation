"""The "Tests vs simulation" section of the motor report (docx and pdf build the same rows from here).

Only when the configuration has tests.  Who gets it: the report route (``routes.report.build_for``) decides once per
build with :func:`viewer_may_see` - admins, global engineers and production owner / engineer members always; anybody
else only when the configuration's ``tests_visible_to_customer`` flag is on.  A build outside a request (scripts, the
owner's CLI) includes it.
"""
from __future__ import annotations

import contextlib
import contextvars
from typing import Any, Dict, List, Optional

HEADING = "Tests vs simulation"
INTRO = "Bench measurements against the model value of the same quantity at the same conditions."
KIND_LABEL = {"parameters": "Parameters", "free_run": "Free-run", "load": "Loads"}

_INCLUDE: "contextvars.ContextVar[bool]" = contextvars.ContextVar("motor_tests_report_include", default=True)


@contextlib.contextmanager
def including(flag: bool):
    tok = _INCLUDE.set(bool(flag))
    try:
        yield
    finally:
        _INCLUDE.reset(tok)


def viewer_may_see(authorization: Optional[str], die: str, cfg: str) -> bool:
    try:
        from motor_ai_sim.motor_tests import store
        if not store.exists(die, cfg):
            return False
        if bool((store.load(die, cfg).get("settings") or {}).get("tests_visible_to_customer")):
            return True
        from motor_ai_sim import auth
        user = auth.resolve_user(authorization) if authorization else None
        if not user:
            return False
        if str(user.get("role") or "").lower() in ("admin", "engineer"):
            return True
        from motor_ai_sim.production import access as PA, models as PM
        prod = PA.load_production(die, cfg)
        return bool(prod) and PM.is_member(str(user.get("email") or ""), prod) in ("owner", "engineer")
    except Exception:                                       # noqa: BLE001
        return False


def _fmt(v: Optional[float], unit: str = "") -> str:
    if v is None:
        return "—"
    a = abs(v)
    s = f"{v:.0f}" if a >= 1000 else f"{v:.1f}" if a >= 100 else f"{v:.2f}" if a >= 1 else f"{v:.3f}"
    return s


def _delta(v: Optional[float]) -> str:
    return "—" if v is None else f"{v:+.1f}"


def _title(rec: Dict[str, Any]) -> str:
    bits = [KIND_LABEL.get(str(rec.get("kind")), str(rec.get("kind"))), str(rec.get("date") or "")]
    for k in ("title", "instrument", "profile"):
        if rec.get(k):
            bits.append(str(rec[k]))
    st = (rec.get("conditions") or {}).get("state")
    if st:
        bits.append(str(st))
    return " · ".join(b for b in bits if b)


def tables(die: str, cfg: str) -> List[Dict[str, Any]]:
    """``[{"title", "rows": [[header...], [label, unit, measured, model, delta], ...]}]``; ``[]`` = no section."""
    if not _INCLUDE.get():
        return []
    try:
        from motor_ai_sim.motor_tests import comparison, model_source, store
        if not store.exists(die, cfg):
            return []
        doc = store.load(die, cfg)
        if not doc.get("tests"):
            return []
        model = model_source.collect(die, cfg)
    except Exception:                                       # noqa: BLE001
        return []
    out = []
    for rec in sorted((t for t in doc["tests"] if isinstance(t, dict)), key=lambda t: (str(t.get("date")), str(t.get("kind")))):
        cmp_ = comparison.compare_record(rec, model, die, cfg)
        rows: List[List[str]] = [["Quantity", "Unit", "Measured", "Model", "Δ %"]]
        for g in cmp_["groups"]:
            if not g.get("rows"):
                continue
            rows.append([g["label"], "", "", "", ""])
            for r in g["rows"]:
                if r["status"] == "info" and r["measured"] is None and r["model"] is None:
                    continue
                model_txt = _fmt(r["model"]) if r["model"] is not None else ("no model point" if r["status"] == "no_model" else "—")
                rows.append(["  " + str(r["label"]), str(r.get("unit") or ""), _fmt(r["measured"]), model_txt,
                             _delta(r["delta_pct"])])
        out.append({"title": _title(rec), "rows": rows, "note": str(rec.get("note") or "")[:200]})
    return out
