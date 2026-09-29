"""Pricing data: per-account usage signals + what each client costs us.

RECORD ONLY -- nothing here bills or limits anyone.  Aggregate counts only,
never file contents.  Everything lives in the cluster store
(``cluster_monitor.data_dir()``, ``history.sqlite``):

* ``usage``    one row per finished job (``job_usage``) -> CPU/wall by kind,
               failed/stopped, queue wait, peak concurrent jobs per day;
* ``activity`` daily counters (day, account, event, key): reports, datasheets,
               Fusion import/export, catalog views, MCP calls by tool, MCP 429s
               -- fed by :func:`note` from the HTTP middleware and the MCP audit;
* ``storage``  daily workspace size per account and category
               (dies / configs / results / reports / other), bytes only;
* logins come from the session event log (``sessions.read_events``).

Cost basis (admin-editable, ``cost_basis.json``): EUR per server-month and
cores per server give EUR per CPU-hour; EUR per GB-month prices storage.

CLI (host timer, see deploy/systemd/motres-usage.*):
  python -m motor_ai_sim.usage_stats daily            # storage sample
  python -m motor_ai_sim.usage_stats monthly [YYYY-MM] [--csv]   # to stdout
"""
from __future__ import annotations

import calendar
import csv
import io
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

HOURS_PER_MONTH = 730.0
#: eu1 = Hetzner AX42-1 (FSN1): EUR 100/month (owner's figure, to confirm from
#: the invoice), 8 cores / 16 threads, 64 GB.  CPU-hour priced per THREAD:
#: EUR/month / (threads x 730 h).  Storage EUR 0 until set.
DEFAULT_BASIS = {"eur_per_server_month": 100.0, "cores_per_server": 16,
                 "eur_per_gb_month": 0.0}
KINDS = ("em", "coupled", "thermal", "sweep", "optimizer", "controller",
         "mechanical", "report", "other")
STORAGE_CATS = ("dies", "configs", "results", "reports", "other")


# ── helpers ──────────────────────────────────────────────────────────────────
def day_of(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def month_bounds(month: str) -> Tuple[float, float]:
    y, m = (int(x) for x in month.split("-"))
    s = datetime(y, m, 1, tzinfo=timezone.utc).timestamp()
    e = datetime(y + (m == 12), m % 12 + 1, 1, tzinfo=timezone.utc).timestamp()
    return s, e


def kind_category(kind: str) -> str:
    k = (kind or "").lower()
    for pat, cat in (("coupled", "coupled"), ("duty_cycle", "coupled"),
                     ("thermal", "thermal"), ("sweep", "sweep"),
                     ("optim", "optimizer"), ("pipeline", "optimizer"),
                     ("controller", "controller"), ("pwm", "controller"),
                     ("inverter", "controller"),
                     ("mech", "mechanical"), ("stress", "mechanical"),
                     ("modes", "mechanical"), ("critical", "mechanical"),
                     ("report", "report"), ("datasheet", "report"), ("pdf", "report"),
                     ("em", "em"), ("transient", "em"), ("field", "em"),
                     ("static", "em"), ("mesh", "em"), ("magnet", "em"),
                     ("fem", "em")):
        if pat in k:
            return cat
    return "other"


def _db():
    from motor_ai_sim import job_usage as U
    con = U._db()
    cols = [r[1] for r in con.execute("PRAGMA table_info(usage)")]
    if "wait_s" not in cols:
        con.execute("ALTER TABLE usage ADD COLUMN wait_s REAL DEFAULT 0")
    con.execute("CREATE TABLE IF NOT EXISTS activity (day TEXT, user TEXT, event TEXT, "
                "key TEXT, n INTEGER, PRIMARY KEY(day, user, event, key))")
    con.execute("CREATE TABLE IF NOT EXISTS storage (day TEXT, user TEXT, category TEXT, "
                "bytes INTEGER, PRIMARY KEY(day, user, category))")
    return con


# ── activity counters ────────────────────────────────────────────────────────
def note(user: Optional[str], event: str, key: str = "", n: int = 1,
         ts: Optional[float] = None) -> None:
    """+n on (day, account, event, key).  Never raises."""
    try:
        d = day_of(time.time() if ts is None else ts)
        con = _db()
        try:
            con.execute("INSERT INTO activity VALUES (?,?,?,?,?) ON CONFLICT(day,user,event,key) "
                        "DO UPDATE SET n = n + excluded.n",
                        (d, str(user or "anon").lower(), event, str(key or "")[:80], int(n)))
            con.commit()
        finally:
            con.close()
    except Exception:                                   # noqa: BLE001
        pass


#: (method, path regex) -> (event, key group or None).  2xx responses only.
_ROUTE_EVENTS = [
    ("GET", re.compile(r"^/api/family/report/(?!progress$)[^/]+/[^/]+$"), "report_pdf"),
    ("GET", re.compile(r"^/api/family/datasheet/[^/]+/[^/]+$"), "datasheet_export"),
    ("GET", re.compile(r"^/api/fusion/params\.(csv|json)$"), "fusion_export"),
    ("POST", re.compile(r"^/api/fusion/import$"), "fusion_import"),
    ("GET", re.compile(r"^/api/catalog$"), "catalog_view"),
    ("POST", re.compile(r"^/api/catalog/([^/]+)/load$"), "catalog_view"),
]


def event_for(method: str, path: str) -> Optional[Tuple[str, str]]:
    p = path.rstrip("/") or "/"
    for m, rx, ev in _ROUTE_EVENTS:
        if m == method:
            mt = rx.match(p)
            if mt:
                return ev, (mt.group(1) if (mt.groups() and ev == "catalog_view") else "")
    return None


def note_request(method: str, path: str, status: int, authorization: Optional[str]) -> None:
    if not (200 <= int(status) < 300):
        return
    hit = event_for(method, path)
    if hit is None:
        return
    try:
        from motor_ai_sim.auth import resolve_user
        u = resolve_user(authorization)
        who = (u or {}).get("email") or (u or {}).get("uid") or "anon"
    except Exception:                                   # noqa: BLE001
        who = "anon"
    note(who, hit[0], hit[1])


# ── storage sampling ─────────────────────────────────────────────────────────
def _category(rel: Path) -> str:
    parts = rel.parts
    name = rel.name.lower()
    if any(p in ("runs", "results") for p in parts) or name.startswith((".last_", ".duty_results")):
        return "results"
    if any(p in ("reports",) for p in parts) or name.endswith((".pdf", ".html")):
        return "reports"
    if parts and parts[0] == "dies":
        return "dies"
    if name.endswith((".yaml", ".yml", ".json")):
        return "configs"
    return "other"


def workspace_sizes(root: Path) -> Dict[str, Dict[str, int]]:
    """{ws_id: {category: bytes}} -- sizes only, contents never read."""
    out: Dict[str, Dict[str, int]] = {}
    if not root.is_dir():
        return out
    for ws in root.iterdir():
        if not ws.is_dir():
            continue
        acc = {c: 0 for c in STORAGE_CATS}
        for dirpath, _dirs, files in os.walk(ws):
            for f in files:
                p = Path(dirpath) / f
                try:
                    acc[_category(p.relative_to(ws))] += p.stat().st_size
                except OSError:
                    continue
        out[ws.name] = acc
    return out


def _ws_owner_map() -> Dict[str, str]:
    """ws_id -> account e-mail, via the workspace module when available."""
    m: Dict[str, str] = {}
    try:
        from motor_ai_sim import users as _u
        from motor_ai_sim import workspace as W
        for u in _u.list_users():
            try:
                m[W.workspace_for_identity(u["email"]).ws_id] = u["email"]
            except Exception:                           # noqa: BLE001
                continue
    except Exception:                                   # noqa: BLE001
        pass
    return m


def sample_storage(root: Optional[Path] = None, ts: Optional[float] = None,
                   owners: Optional[Dict[str, str]] = None) -> int:
    root = root or Path(os.environ.get("WORKSPACES_ROOT", "/srv/motres/workspaces"))
    sizes = workspace_sizes(root)
    owners = _ws_owner_map() if owners is None else owners
    d = day_of(time.time() if ts is None else ts)
    con = _db()
    try:
        for ws, cats in sizes.items():
            who = owners.get(ws, ws)
            for c, b in cats.items():
                con.execute("INSERT OR REPLACE INTO storage VALUES (?,?,?,?)", (d, who, c, int(b)))
        con.commit()
    finally:
        con.close()
    return len(sizes)


# ── cost basis ───────────────────────────────────────────────────────────────
def _basis_file() -> Path:
    from motor_ai_sim import cluster_monitor as CM
    return CM.data_dir() / "cost_basis.json"


def get_basis() -> Dict[str, Any]:
    b = dict(DEFAULT_BASIS)
    try:
        b.update(json.loads(_basis_file().read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return with_rates(b)


def with_rates(b: Dict[str, Any]) -> Dict[str, Any]:
    cores = max(1.0, float(b["cores_per_server"]))
    out = dict(b)
    out["eur_per_cpu_hour"] = round(float(b["eur_per_server_month"]) / (cores * HOURS_PER_MONTH), 6)
    return out


def set_basis(body: Dict[str, Any]) -> Dict[str, Any]:
    b = {k: DEFAULT_BASIS[k] for k in DEFAULT_BASIS}
    b.update({k: v for k, v in get_basis().items() if k in DEFAULT_BASIS})
    for k in DEFAULT_BASIS:
        if k in body:
            v = float(body[k])
            if not (v >= 0) or (k == "cores_per_server" and v < 1):
                raise ValueError(f"{k} must be a non-negative number (cores >= 1)")
            b[k] = int(v) if k == "cores_per_server" else v
    _basis_file().write_text(json.dumps(b, indent=1), encoding="utf-8")
    return with_rates(b)


# ── account attributes ───────────────────────────────────────────────────────
def account_attrs(email: str, registry: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    e = (email or "").lower()
    reg = registry if registry is not None else _registry()
    u = reg.get(e) or {}
    try:
        from motor_ai_sim.auth import _ADMIN_EMAILS, ADMIN_OWNER
        internal = e in _ADMIN_EMAILS or e == (ADMIN_OWNER or "").lower() or u.get("tier") == "admin"
    except Exception:                                   # noqa: BLE001
        internal = u.get("tier") == "admin"
    return {"plan": u.get("plan") or ("internal" if internal else "free"),
            "tier": u.get("tier", ""),
            "domain": e.split("@", 1)[1] if "@" in e else "",
            "created": u.get("created")}


def _registry() -> Dict[str, Dict[str, Any]]:
    try:
        from motor_ai_sim import users as _u
        return {u["email"]: u for u in _u.list_users()}
    except Exception:                                   # noqa: BLE001
        return {}


# ── aggregation ──────────────────────────────────────────────────────────────
def _jobs(start: float, end: float) -> List[Dict[str, Any]]:
    con = _db()
    try:
        con.row_factory = __import__("sqlite3").Row
        rows = con.execute("SELECT * FROM usage WHERE ts_end >= ? AND ts_end < ?",
                           (start, end)).fetchall()
    finally:
        con.close()
    return [dict(r) for r in rows]


def peak_concurrent(intervals: Iterable[Tuple[float, float]]) -> int:
    ev = []
    for s, e in intervals:
        ev.append((s, 1))
        ev.append((e, -1))
    ev.sort(key=lambda x: (x[0], x[1]))           # ends before starts at a tie
    cur = best = 0
    for _, d in ev:
        cur += d
        best = max(best, cur)
    return best


def daily(start: float, end: float) -> List[Dict[str, Any]]:
    """Per account per day: CPU/wall by kind, jobs, failed/stopped, wait, peak."""
    by: Dict[Tuple[str, str], Dict[str, Any]] = {}
    ivs: Dict[Tuple[str, str], List[Tuple[float, float]]] = {}
    for r in _jobs(start, end):
        k = (str(r["user"]).lower(), day_of(r["ts_end"]))
        a = by.setdefault(k, {"user": k[0], "day": k[1], "jobs": 0, "failed": 0,
                              "stopped": 0, "agent_runs": 0, "wait_s": 0.0,
                              "cpu_s": {c: 0.0 for c in KINDS},
                              "wall_s": {c: 0.0 for c in KINDS}})
        cat = kind_category(r["kind"])
        a["jobs"] += 1
        a["failed"] += r["status"] == "failed"
        a["stopped"] += r["status"] == "stopped"
        a["agent_runs"] += (r.get("client") or "web") != "web"
        a["wait_s"] += float(r.get("wait_s") or 0.0)
        a["cpu_s"][cat] += float(r["cpu_s"] or 0.0)
        a["wall_s"][cat] += float(r["wall_s"] or 0.0)
        ivs.setdefault(k, []).append((float(r["ts_start"]), float(r["ts_end"])))
    for k, a in by.items():
        a["peak_concurrent"] = peak_concurrent(ivs[k])
    return sorted(by.values(), key=lambda a: (a["day"], a["user"]))


def _activity(d0: str, d1: str) -> List[Tuple[str, str, str, str, int]]:
    con = _db()
    try:
        return con.execute("SELECT day, user, event, key, n FROM activity WHERE day >= ? AND day < ?",
                           (d0, d1)).fetchall()
    finally:
        con.close()


def _storage(d0: str, d1: str) -> Dict[str, Dict[str, Any]]:
    """Per account: mean GB over sampled days + last sample per category."""
    con = _db()
    try:
        rows = con.execute("SELECT day, user, category, bytes FROM storage WHERE day >= ? AND day < ?",
                           (d0, d1)).fetchall()
    finally:
        con.close()
    per: Dict[str, Dict[str, Dict[str, int]]] = {}
    for day, user, cat, b in rows:
        per.setdefault(user.lower(), {}).setdefault(day, {})[cat] = b
    out = {}
    for user, days in per.items():
        totals = [sum(c.values()) for c in days.values()]
        last = days[max(days)]
        out[user] = {"avg_gb": sum(totals) / len(totals) / 1e9,
                     "last": {c: last.get(c, 0) for c in STORAGE_CATS}}
    return out


def _logins(start: float, end: float) -> Dict[str, int]:
    out: Dict[str, int] = {}
    try:
        from motor_ai_sim import sessions as S
        for ev in S.read_events(limit=100_000):
            if ev.get("event") != "login":
                continue
            ts = ev.get("ts")
            t = ts if isinstance(ts, (int, float)) else datetime.fromisoformat(
                str(ts).replace("Z", "+00:00")).timestamp()
            if start <= t < end:
                e = str(ev.get("email") or "").lower()
                out[e] = out.get(e, 0) + 1
    except Exception:                                   # noqa: BLE001
        pass
    return out


def monthly(month: str, basis: Optional[Dict[str, Any]] = None,
            registry: Optional[Dict[str, Dict[str, Any]]] = None,
            logins: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    start, end = month_bounds(month)
    d0, d1 = day_of(start), day_of(end)
    b = with_rates(basis) if basis else get_basis()
    reg = registry if registry is not None else _registry()
    lg = logins if logins is not None else _logins(start, end)
    days_in = calendar.monthrange(*(int(x) for x in month.split("-")))[1]
    acc: Dict[str, Dict[str, Any]] = {}

    def row(u: str) -> Dict[str, Any]:
        return acc.setdefault(u, {
            "account": u, "jobs": 0, "failed": 0, "stopped": 0, "agent_runs": 0,
            "cpu_h": 0.0, "wall_h": 0.0, **{f"cpu_h_{c}": 0.0 for c in KINDS},
            "wait_h": 0.0, "peak_concurrent": 0, "active_days": set(),
            "logins": 0, "report_pdf": 0, "datasheet_export": 0, "fusion_import": 0,
            "fusion_export": 0, "catalog_view": 0, "mcp_calls": 0, "mcp_429": 0,
            "mcp_by_tool": {}, "storage_gb_avg": 0.0,
            **{f"storage_{c}_gb": 0.0 for c in STORAGE_CATS}})

    for d in daily(start, end):
        a = row(d["user"])
        a["jobs"] += d["jobs"]
        a["failed"] += d["failed"]
        a["stopped"] += d["stopped"]
        a["agent_runs"] += d["agent_runs"]
        a["wait_h"] += d["wait_s"] / 3600
        a["peak_concurrent"] = max(a["peak_concurrent"], d["peak_concurrent"])
        for c in KINDS:
            a[f"cpu_h_{c}"] += d["cpu_s"][c] / 3600
            a["cpu_h"] += d["cpu_s"][c] / 3600
            a["wall_h"] += d["wall_s"][c] / 3600
        a["active_days"].add(d["day"])
    for day, user, ev, key, n in _activity(d0, d1):
        a = row(user.lower())
        a["active_days"].add(day)
        if ev == "mcp_call":
            a["mcp_calls"] += n
            a["mcp_by_tool"][key or "?"] = a["mcp_by_tool"].get(key or "?", 0) + n
        elif ev in a:
            a[ev] += n
    for user, n in lg.items():
        row(user)["logins"] += n
    for user, s in _storage(d0, d1).items():
        a = row(user)
        a["storage_gb_avg"] = s["avg_gb"]
        for c in STORAGE_CATS:
            a[f"storage_{c}_gb"] = s["last"][c] / 1e9

    rate_cpu = float(b["eur_per_cpu_hour"])
    rate_gb = float(b["eur_per_gb_month"])
    rows = []
    for u, a in acc.items():
        a["active_days"] = len(a["active_days"])
        a.update(account_attrs(u, reg))
        a["cost_cpu_eur"] = round(a["cpu_h"] * rate_cpu, 4)
        a["cost_storage_eur"] = round(a["storage_gb_avg"] * rate_gb, 4)
        a["cost_eur"] = round(a["cost_cpu_eur"] + a["cost_storage_eur"], 4)
        for k, v in list(a.items()):
            if isinstance(v, float):
                a[k] = round(v, 4)
        rows.append(a)
    rows.sort(key=lambda r: -r["cost_eur"])
    return {"month": month, "days": days_in, "basis": b, "accounts": rows,
            "totals": {"cpu_h": round(sum(r["cpu_h"] for r in rows), 4),
                       "cost_eur": round(sum(r["cost_eur"] for r in rows), 4),
                       "jobs": sum(r["jobs"] for r in rows)}}


def monthly_csv(report: Dict[str, Any]) -> str:
    rows = []
    for r in report["accounts"]:
        flat = {k: v for k, v in r.items() if k != "mcp_by_tool"}
        flat["mcp_by_tool"] = ";".join(f"{k}={v}" for k, v in sorted(r["mcp_by_tool"].items()))
        rows.append(flat)
    from motor_ai_sim.job_usage import to_csv
    return to_csv(rows)


# ── CLI ──────────────────────────────────────────────────────────────────────
def _main(argv: List[str]) -> int:
    if not argv or argv[0] not in ("daily", "monthly"):
        print(__doc__, file=sys.stderr)
        return 2
    if argv[0] == "daily":
        n = sample_storage()
        print(json.dumps({"storage_sampled_workspaces": n}))
        return 0
    month = next((a for a in argv[1:] if re.match(r"^\d{4}-\d{2}$", a)), None)
    if month is None:
        now = datetime.now(timezone.utc)
        y, m = (now.year, now.month - 1) if now.month > 1 else (now.year - 1, 12)
        month = f"{y:04d}-{m:02d}"
    rep = monthly(month)
    sys.stdout.write(monthly_csv(rep) if "--csv" in argv else json.dumps(rep, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
