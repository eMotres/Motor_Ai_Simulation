"""The real readers behind the owner's daily report: each returns the rows of one 24-hour window ``[start, end)``.

Read-only.  A reader that cannot open ONE store (a broken project, an unreadable ledger) skips it and goes on; a reader that
cannot work at all raises, and ``build.build`` shows "source unavailable" in that section instead of a false "no changes".

Sources per section (docs/OWNER_REPORT.md):

* users        users.json (created in the window) + the applicant verification case, the NDA / pre-approval records, the
               platform agreement acceptances, the data-sharing consent, the project membership index, the first sign-in
               (sessions), the first modules (activity.sqlite) and the first runs (job usage).
* activity     auth_events.jsonl (sign-ins, IPs, rejects), activity.sqlite (time, sessions, modules), job usage (runs, failures),
               the daily counters of usage_stats (reports, datasheets, Fusion), the 3-D download log, the design-assistant ledger.
* projects     every project's ``audit.jsonl`` + ``thread.jsonl`` + the stored results of its linked motors (the digest collector).
* productions  every production's ``audit.jsonl`` (digest collector for the curated lines + the rest of the audit), the motor-test
               records of its configuration.
* motors       admin audit (die / config / CAD / access actions), product revisions, finished runs (job usage), new results in the
               shared duty-results ledger.  There is no event log for "die created / duplicated / deleted" in the app yet.
* digests      the digests the scheduled run SENT by itself in this run (``digest.approval.AUTO_ACTOR``) and the open drafts that
               wait for the owner (or whose automatic send failed), made by ``digest.approval.run_daily`` just before the report.
"""
from __future__ import annotations

import datetime as _dt
import logging
import time
from collections import Counter, defaultdict
from typing import Any, Optional

from motor_ai_sim import return_path as RP
from motor_ai_sim.owner_report import build as B

log = logging.getLogger(__name__)

READISH = ("read", "download", "view", "open", "list")


# -- helpers -----------------------------------------------------------------------------------------------------------------------

def ts_of(value: Any) -> Optional[float]:
    """Epoch seconds of a number or an ISO string; a string without a zone is the SERVER's local time (``datetime.now()``)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value or "").strip()
    if not s:
        return None
    try:
        dt = _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.timestamp() if dt.tzinfo is not None else dt.astimezone().timestamp()


def _in(ts: Optional[float], start: float, end: float) -> bool:
    return ts is not None and start <= ts < end


def side_of(email: str) -> str:
    """staff (admin / engineer / the configured admin addresses) | factory (OEM role) | customer."""
    email = str(email or "").strip().lower()
    if not email:
        return ""
    try:
        from motor_ai_sim import auth as A
        if email in A._ADMIN_EMAILS:
            return "staff"
    except Exception:                                        # noqa: BLE001
        pass
    try:
        from motor_ai_sim import users as U
        rec = U.get_user(email)
    except Exception:                                        # noqa: BLE001
        rec = None
    if not rec:
        return "customer" if "@" in email else "system"
    role = str(rec.get("role") or "user")
    if role in ("admin", "engineer"):
        return "staff"
    return "factory" if role == "oem" else "customer"


def _who(email: str) -> str:
    return str(email or "").strip().lower()


def _read_jsonl(path) -> list:
    from motor_ai_sim.digest import collect as C
    return C._read_jsonl(path)


def _label_count(rows: list) -> list:
    """Merge equal texts of the same kind into one line with a count ("... (x3)"): the newest line carries it."""
    out: dict = {}
    for ln in rows:
        k = (ln.text, ln.who)
        if k in out:
            out[k][0] += 1
            if ln.ts > out[k][1].ts:
                out[k][1] = ln
        else:
            out[k] = [1, ln]
    res = []
    for n, ln in out.values():
        if n > 1:
            ln = B.Line(ln.ts, f"{ln.text} (x{n})", ln.path, ln.who, ln.side)
        res.append(ln)
    return res


# -- 1. new users --------------------------------------------------------------------------------------------------------------------

def _signup_method(rec: dict) -> str:
    if rec.get("signup"):
        return str(rec["signup"])
    inv = rec.get("invite")
    if isinstance(inv, dict):
        return f"invited by {inv.get('by') or 'admin'} ({inv.get('role') or 'user'})"
    if rec.get("verified_by") == "google" or rec.get("has_password") is False:
        return "google"
    return "created by an administrator"


def _nda_text(email: str, rec: dict) -> str:
    nda = rec.get("nda") if isinstance(rec.get("nda"), dict) else {}
    by = str(nda.get("by") or "")
    if nda.get("signed"):
        if by == "online":
            return f"online, signed {nda.get('date') or '?'}" + (f", version {nda['version']}" if nda.get("version") else "")
        if by.startswith("preapproved"):
            return f"pre-approved (signed outside the platform), {nda.get('date') or '?'} - {by.partition(':')[2].strip() or by}"
        return f"set by {by or 'an administrator'}, {nda.get('date') or '?'}"
    try:
        from motor_ai_sim import nda_preapproval as NP
        pre = NP.load().get(email)
        if pre:
            return f"pre-approved by {pre.get('by') or 'admin'} ({pre.get('reference') or '-'}), account not flagged yet"
    except Exception:                                        # noqa: BLE001
        pass
    return "none"


def _verification_text(email: str) -> tuple[str, dict]:
    try:
        from motor_ai_sim.verification import store as VS
        case = VS.latest_of(email)
    except Exception:                                        # noqa: BLE001
        case = None
    if not case:
        return "no case", {}
    reasons = []
    for r in case.get("reasons") or []:
        reasons.append(str(r.get("code") or r.get("text") or r) if isinstance(r, dict) else str(r))
    txt = f"{case.get('status')}" + (f" - {case['status_reason']}" if case.get("status_reason") else "")
    if reasons:
        txt += " [" + ", ".join(reasons[:6]) + "]"
    return txt, case.get("application") if isinstance(case.get("application"), dict) else {}


def _agreement_text(email: str) -> str:
    try:
        from motor_ai_sim import platform_agreement as PA
        rows = [r for r in PA.read_records(10_000) if _who(r.get("email") or r.get("user")) == email]
        if rows:
            r = rows[-1]
            return f"accepted {str(r.get('iso') or '')[:10]} mode {r.get('mode')} (v{r.get('version')}, {r.get('company') or '-'})"
    except Exception:                                        # noqa: BLE001
        pass
    return "none"


def _first_actions(email: str, created: float, end: float) -> list:
    out = []
    try:
        from motor_ai_sim import sessions as S
        logins = sorted((e for e in S.read_events(100_000, email=email) if e.get("event") == "login"), key=lambda e: e.get("t") or 0)
        if logins:
            first = logins[0]
            out.append(f"first sign-in {B.local_stamp(first.get('t') or 0)} ({first.get('reason') or first.get('login_method') or 'login'})")
    except Exception:                                        # noqa: BLE001
        pass
    try:
        from motor_ai_sim import activity as ACT
        con = ACT._db()
        try:
            rows = con.execute("SELECT tabs FROM activity_day WHERE user=? AND last_ts>=?", (email, created)).fetchall()
        finally:
            con.close()
        tabs: Counter = Counter()
        for (raw,) in rows:
            for k, v in ACT._tabs(raw).items():
                tabs[k] += v
        if tabs:
            out.append("modules " + ", ".join(k for k, _ in tabs.most_common(4)))
    except Exception:                                        # noqa: BLE001
        pass
    try:
        from motor_ai_sim import job_usage as JU
        kinds = Counter(str(r.get("kind")) for r in JU.jobs(created, end + 1, user=email, limit=10_000))
        if kinds:
            out.append("runs " + ", ".join(f"{k} {n}" for k, n in kinds.most_common(4)))
    except Exception:                                        # noqa: BLE001
        pass
    return out


def new_users(start: float, end: float) -> list:
    from motor_ai_sim import users as U
    rows = []
    for email, rec in sorted(U._load_soft().items()):
        created = ts_of(rec.get("created"))
        if not _in(created, start, end):
            continue
        email = _who(email)
        ver_text, app = _verification_text(email)
        nda = rec.get("nda") if isinstance(rec.get("nda"), dict) else {}
        company = str(nda.get("company") or app.get("company") or app.get("organisation") or app.get("organization") or "")
        try:
            from motor_ai_sim.routes.admin_analytics import resolve_user_country
            country = resolve_user_country(email, rec)
        except Exception:                                    # noqa: BLE001
            country = str(rec.get("country") or app.get("country") or "").upper()
        projects = []
        try:
            from motor_ai_sim.development import store as PS
            for pid in PS.membership_projects(email):
                proj = PS.get_project(pid) or {}
                role = next((m.get("role") for m in proj.get("members") or [] if _who(m.get("email")) == email), "")
                projects.append(f"{proj.get('title') or pid} ({role or 'member'})")
        except Exception:                                    # noqa: BLE001
            pass
        verified = U.is_verified(rec)
        ds = rec.get("data_sharing") if isinstance(rec.get("data_sharing"), dict) else {}
        rows.append({
            "email": email, "name": str(rec.get("name") or ""), "side": side_of(email), "role": rec.get("role") or "user",
            "company": company, "country": country, "signup": _signup_method(rec),
            "verified": (f"verified {str(rec.get('verified_at') or '')[:16]} by {rec.get('verified_by') or '?'}" if verified
                         else "NOT verified" + (f" ({rec['pending_approval']})" if rec.get("pending_approval") else "")),
            "nda": _nda_text(email, rec), "verification": ver_text, "agreement": _agreement_text(email),
            "data_sharing": ("consent given " + str(ds.get("time") or "")[:10]) if ds.get("consent") else "no consent",
            "projects": projects or "none", "first_actions": _first_actions(email, created or start, end) or "none yet",
            "created": B.local_stamp(created or 0) + " (Europe/Ljubljana)", "ts": created,
            "path": "", "admin_path": B.admin_path("users"),
        })
    for r in rows:
        r["path"] = r.pop("admin_path")
    rows.sort(key=lambda r: r["ts"] or 0)
    return rows


# -- 2. user activity ----------------------------------------------------------------------------------------------------------------

STEP_KINDS = {"report_pdf": "reports", "datasheet_export": "datasheets", "fusion_export": "fusion export", "fusion_import": "fusion import"}


def _utc_days(start: float, end: float) -> list:
    d0 = _dt.datetime.fromtimestamp(start, _dt.timezone.utc).date()
    d1 = _dt.datetime.fromtimestamp(end, _dt.timezone.utc).date()
    out, d = [], d0
    while d <= d1:
        out.append(d.isoformat())
        d += _dt.timedelta(days=1)
    return out


def user_activity(start: float, end: float) -> list:
    acc: dict = defaultdict(lambda: {"logins": 0, "ips": [], "active_s": 0.0, "sessions": 0, "tabs": Counter(), "runs": 0,
                                     "failed": 0, "run_kinds": Counter(), "downloads": Counter(), "assistant": 0, "errors": []})
    # sign-ins and refused tokens
    try:
        from motor_ai_sim import sessions as S
        for ev in S.read_events(limit=200_000):
            t = ev.get("t")
            if not _in(float(t) if isinstance(t, (int, float)) else ts_of(ev.get("ts")), start, end):
                continue
            who = _who(ev.get("email"))
            if not who:
                continue
            if ev.get("event") == "login":
                a = acc[who]
                a["logins"] += 1
                if ev.get("ip") and ev["ip"] not in a["ips"]:
                    a["ips"].append(ev["ip"])
            elif ev.get("event") == "reject":
                acc[who]["errors"].append(f"sign-in refused ({ev.get('reason') or '?'})")
    except Exception as exc:                                 # noqa: BLE001
        log.warning("owner_report: auth events unreadable (%s)", exc)
    # working time, sessions and modules (per UTC day: the days that overlap the window)
    try:
        from motor_ai_sim import activity as ACT
        days = _utc_days(start, end)
        con = ACT._db()
        try:
            rows = con.execute("SELECT user, day, active_s, sessions, last_ts, tabs FROM activity_day WHERE day IN (%s)"
                               % ",".join("?" * len(days)), days).fetchall()
        finally:
            con.close()
        for user, _day, active, sessions, last_ts, tabs in rows:
            if float(last_ts or 0) < start:
                continue
            a = acc[_who(user)]
            a["active_s"] += float(active or 0)
            a["sessions"] += int(sessions or 0)
            a["tabs"].update({k: v for k, v in ACT._tabs(tabs).items()})
    except Exception as exc:                                 # noqa: BLE001
        log.warning("owner_report: activity.sqlite unreadable (%s)", exc)
    # runs
    try:
        from motor_ai_sim import job_usage as JU
        from motor_ai_sim import usage_stats as US
        for r in JU.jobs(start, end, limit=10 ** 6):
            who = _who(r.get("user"))
            if not who:
                continue
            a = acc[who]
            a["runs"] += 1
            a["run_kinds"][US.kind_category(str(r.get("kind")))] += 1
            if r.get("status") == "failed":
                a["failed"] += 1
                a["errors"].append(f"run failed: {r.get('kind')} {r.get('machine') or ''}".strip())
    except Exception as exc:                                 # noqa: BLE001
        log.warning("owner_report: job usage unreadable (%s)", exc)
    # report / datasheet / Fusion counters (per UTC day)
    try:
        from motor_ai_sim import usage_stats as US
        days = _utc_days(start, end)
        after = (_dt.date.fromisoformat(days[-1]) + _dt.timedelta(days=1)).isoformat()
        for day, user, event, _key, n in US._activity(days[0], after):
            if day in days and event in STEP_KINDS and user != "anon":
                acc[_who(user)]["downloads"][STEP_KINDS[event]] += int(n)
    except Exception as exc:                                 # noqa: BLE001
        log.warning("owner_report: usage counters unreadable (%s)", exc)
    # 3-D / STEP downloads
    try:
        from motor_ai_sim import model3d as M3
        for r in M3._with_account(M3._all_rows()):
            who = _who(r.get("email") or r.get("user"))
            if who and _in(ts_of(r.get("ts")), start, end):
                acc[who]["downloads"]["3D/STEP"] += 1
    except Exception as exc:                                 # noqa: BLE001
        log.warning("owner_report: download log unreadable (%s)", exc)
    # the design assistant
    try:
        from motor_ai_sim.design_agent import runner as DA
        for day in _utc_days(start, end):
            for r in DA.ledger_rows(day):
                if _in(ts_of(r.get("ts")), start, end) and r.get("owner"):
                    acc[_who(r["owner"])]["assistant"] += 1
    except Exception as exc:                                 # noqa: BLE001
        log.warning("owner_report: design assistant ledger unreadable (%s)", exc)

    out = []
    for email, a in acc.items():
        if not (a["logins"] or a["active_s"] or a["runs"] or a["downloads"] or a["assistant"] or a["errors"]):
            continue
        name, country = "", ""
        try:
            from motor_ai_sim import users as U
            rec = U.get_user(email) or {}
            name = str(rec.get("name") or "")
            try:
                from motor_ai_sim.routes.admin_analytics import resolve_user_country
                country = resolve_user_country(email, rec)
            except Exception:                                # noqa: BLE001
                country = str(rec.get("country") or "").upper()
        except Exception:                                    # noqa: BLE001
            pass
        out.append({
            "email": email, "name": name, "side": side_of(email), "country": country, "logins": a["logins"], "ips": a["ips"][:4],
            "active_s": a["active_s"], "sessions": a["sessions"],
            "tabs": [{"tab": k, "seconds": v} for k, v in a["tabs"].most_common(4) if v > 0],
            "runs": a["runs"], "failed": a["failed"], "run_kinds": dict(a["run_kinds"].most_common()),
            "downloads": dict(a["downloads"]), "assistant": a["assistant"],
            "errors": [f"{e} x{n}" if n > 1 else e for e, n in Counter(a["errors"]).most_common(5)],
        })
    out.sort(key=lambda r: (-r["active_s"], r["email"]))
    return out


# -- 3. projects ---------------------------------------------------------------------------------------------------------------------

def _project_audit_text(ev: dict) -> Optional[tuple]:
    """``(text, view)`` of one project audit event, None for reads."""
    action, entity, eid = str(ev.get("action") or ""), str(ev.get("entity") or ""), str(ev.get("entity_id") or "")
    det = ev.get("details") if isinstance(ev.get("details"), dict) else {}
    if action == "read" or action.endswith(".read") or "view" in action.split(".")[-1:]:
        return None
    if action == "create" and entity == "project":
        return "project created", None
    if action == "update" and entity == "project":
        return "project updated (" + ", ".join(str(f) for f in det.get("fields") or []) + ")", None
    if action == "motor.link":
        return f"variant linked: {eid}", "motors"
    if action == "motor.unlink":
        return f"variant unlinked: {eid}", "motors"
    if action == "motor.autolink":
        return f"variant auto-linked: {eid}", "motors"
    if action == "motor.status":
        return f"variant {eid} is now {det.get('new') or 'updated'}", "motors"
    if action == "motor.note":
        return f"design step note on {eid}", "motors"
    if action == "motor.recommended":
        return f"recommended flag {'set' if det.get('value') else 'cleared'} on {eid}", "motors"
    if action == "motor.report_reviewed":
        return f"report-reviewed flag {'set' if det.get('value') else 'cleared'} on {eid}", "motors"
    if action == "motor.report.download":
        return f"report downloaded: {eid}", "motors"
    if action == "submit" and entity == "spec_version":
        return f"specification version {eid} submitted", "specification"
    if entity == "spec_draft":
        return {"autosave": "specification draft saved", "migrate": "specification draft migrated",
                "submit_attempt": "specification submit attempted", "submit_rejected": "specification submit rejected"}.get(
                    action, f"specification draft {action}"), "specification"
    if action == "invite" and entity == "member":
        return f"member invited: {eid} ({det.get('role') or 'member'})", None
    if action == "invite_claimed":
        return f"invite claimed: {eid} ({det.get('role') or 'member'})", None
    if action == "remove" and entity == "member":
        return f"member removed: {eid}", None
    if action in ("post", "project_thread_reopened") and entity == "thread_message":
        return None if action == "post" else "message thread reopened", "messages"      # posted messages come from thread.jsonl
    if action.startswith("spec.proposal."):
        return f"specification proposal {action.rsplit('.', 1)[-1]}: {eid}", "specification"
    return f"{action} {entity} {eid}".strip(), None


def _fmt_projects(start: float, end: float) -> list:
    from motor_ai_sim.development import store as PS
    from motor_ai_sim.digest import collect as C
    blocks = []
    for project in PS.list_projects():
        pid = str(project.get("id") or "")
        try:
            lines = []
            for ev in _read_jsonl(PS.path_for(pid) / "audit.jsonl"):
                ts = ts_of(ev.get("time"))
                if not _in(ts, start, end):
                    continue
                got = _project_audit_text(ev)
                if got is None or got[0] is None:            # a posted message comes from thread.jsonl, not its audit row
                    continue
                text, view = got
                who = _who(ev.get("actor_email"))
                eid = str(ev.get("entity_id") or "")
                die, _, cfg = eid.partition("/")
                path = (RP.project_path(pid, view, die=die, config=cfg) if view == "motors" and cfg else None) or RP.project_path(pid, view) \
                    or RP.project_path(pid) or ""
                lines.append(B.Line(ts, text, path, who, side_of(who)))
            lines = _label_count(lines)
            # messages (thread) and new computed results: the digest collector's own lines
            for it in C.collect_project(project, start, end):
                if it.kind in ("message", "results"):
                    lines.append(B.Line(it.ts, it.en, it.link or "", "", "customer" if it.kind == "message" else "system"))
            if lines:
                blocks.append(B.Block(f"Project: {project.get('title') or pid} [{project.get('status') or 'spec'}]", lines,
                                      RP.project_path(pid) or ""))
        except Exception as exc:                             # noqa: BLE001 - one broken project must not hide the others
            log.warning("owner_report: project %s unreadable (%s)", pid, exc)
    try:
        from motor_ai_sim import platform_agreement as PA
        agree = []
        for r in PA.read_records(10_000):
            ts = ts_of(r.get("ts") or r.get("iso"))
            if _in(ts, start, end):
                who = _who(r.get("email") or r.get("user"))
                agree.append(B.Line(ts, f"platform agreement accepted: {r.get('company') or '-'}, mode {r.get('mode')}, v{r.get('version')}",
                                    B.admin_path("users"), who, side_of(who)))
        if agree:
            blocks.append(B.Block("Agreement acceptances", agree))
    except Exception as exc:                                 # noqa: BLE001
        log.warning("owner_report: agreement records unreadable (%s)", exc)
    return blocks


# -- 4. productions ------------------------------------------------------------------------------------------------------------------

#: (action, entity) pairs the digest collector already turns into a line
def _prod_covered(action: str, entity: str) -> bool:
    return ((action == "publish_revision" and entity == "revision") or (action == "create" and entity in ("document", "thread_message", "proposal"))
            or (entity == "proposal" and action in ("accepted", "rejected", "implemented"))
            or (action == "update" and entity in ("bom", "costs", "procurement", "process", "assembly_process", "assembly", "members"))
            or (entity == "part" and action in ("create", "update", "delete"))
            or (entity == "schedule" and action in ("update", "freeze_baseline", "create")))


def _fmt_productions(start: float, end: float) -> list:
    from motor_ai_sim.digest import build as DB
    from motor_ai_sim.digest import collect as C
    from motor_ai_sim.production import store as PR
    blocks = []
    for group in (g for g in DB.list_groups() if g.kind == "production"):
        rec = group.record
        die, config = str(rec.get("die") or ""), str(rec.get("config") or "")
        try:
            lines = [B.Line(it.ts, it.en, it.link or "", "", "system") for it in C.collect_production(rec, start, end)]
            extra = []
            for ev in _read_jsonl(PR.audit_path(die, config)):
                ts = ts_of(ev.get("time"))
                action, entity = str(ev.get("action") or ""), str(ev.get("entity") or "")
                if not _in(ts, start, end) or _prod_covered(action, entity) or any(action.startswith(r) for r in READISH):
                    continue
                who = _who(ev.get("who"))
                extra.append(B.Line(ts, f"{action} {entity} {ev.get('entity_id') or ''}".strip(), group.path or "", who, side_of(who)))
            lines += _label_count(extra)
            try:
                from motor_ai_sim.motor_tests import store as MT
                for ev in MT.read_audit(die, config):
                    ts = ts_of(ev.get("time"))
                    if _in(ts, start, end):
                        who = _who(ev.get("who"))
                        lines.append(B.Line(ts, f"motor test record {ev.get('action')}: {ev.get('entity_id') or ''}".strip(),
                                            RP.production_path(die, config, "tests") or group.path or "", who, side_of(who)))
            except Exception as exc:                         # noqa: BLE001
                log.warning("owner_report: motor tests of %s/%s unreadable (%s)", die, config, exc)
            if lines:
                blocks.append(B.Block(f"Production: {group.title_en} [{die} / {config}]", lines, group.path or ""))
        except Exception as exc:                             # noqa: BLE001
            log.warning("owner_report: production %s/%s unreadable (%s)", die, config, exc)
    return blocks


# -- 5. motors -----------------------------------------------------------------------------------------------------------------------

def _fmt_motors(start: float, end: float) -> list:
    from motor_ai_sim import admin_audit as AA
    blocks = []
    cat = []
    for r in AA._iter_lines():
        act = str(r.get("action") or "")
        ts = ts_of(r.get("ts"))
        if _in(ts, start, end) and (act.startswith(("die.", "config.", "cad.")) or act == "user.motors"):
            det = r.get("details") if isinstance(r.get("details"), dict) else {}
            extra = ", ".join(f"{k}={v}" for k, v in list(det.items())[:4])
            cat.append(B.Line(ts, f"{act} {r.get('target') or ''}" + (f" ({extra})" if extra else ""),
                              B.admin_path("motorsAccess"), _who(r.get("actor")), "staff"))
    if cat:
        blocks.append(B.Block("Catalogue and access", _label_count(cat)))
    try:
        from motor_ai_sim import motor_revisions as MR
        revs = []
        for lin in MR._load().values():
            for rev in lin.get("revisions") or []:
                ts = ts_of(rev.get("created_at"))
                if _in(ts, start, end):
                    who = _who(rev.get("created_by"))
                    revs.append(B.Line(ts, f"{MR.label(rev.get('rev'))} of {rev.get('die')} / {rev.get('config')}"
                                           + (f": {rev['reason']}" if rev.get("reason") else "")
                                           + (" (agreed with client)" if rev.get("agreed_with_client") else ""),
                                       "", who, side_of(who)))
        if revs:
            blocks.append(B.Block("Product revisions", revs))
    except Exception as exc:                                 # noqa: BLE001
        log.warning("owner_report: motor revisions unreadable (%s)", exc)
    try:
        from motor_ai_sim import job_usage as JU
        from motor_ai_sim import usage_stats as US
        groups: dict = {}
        for r in JU.jobs(start, end, limit=10 ** 6):
            who = _who(r.get("user"))
            key = (str(r.get("machine") or "-"), US.kind_category(str(r.get("kind"))), str(r.get("status") or ""), who)
            g = groups.setdefault(key, [0, 0.0])
            g[0] += 1
            g[1] = max(g[1], float(r.get("ts_end") or 0))
        runs = []
        for (machine, kind, status, who), (n, ts) in groups.items():
            word = "FAILED" if status == "failed" else status or "finished"
            runs.append(B.Line(ts, f"{kind} run {word} x{n}: {machine}" if n > 1 else f"{kind} run {word}: {machine}", "", who, side_of(who)))
        if runs:
            blocks.append(B.Block("Runs finished", runs))
    except Exception as exc:                                 # noqa: BLE001
        log.warning("owner_report: job usage unreadable (%s)", exc)
    try:
        from motor_ai_sim import duty_results as DR
        from motor_ai_sim.digest import collect as C
        results = DR._read_store(DR.shared_store_path())
        res = []
        for die, cfgs in results.items():
            for cfg, duties in (cfgs.items() if isinstance(cfgs, dict) else []):
                for duty, kinds in (duties.items() if isinstance(duties, dict) else []):
                    fresh = {}
                    for k in C.RESULT_KINDS:
                        row = kinds.get(k) if isinstance(kinds, dict) else None
                        if isinstance(row, dict):
                            ts = ts_of(row.get("computed_at") or row.get("recorded_at"))
                            if _in(ts, start, end):
                                fresh[k] = (row, ts)
                    if fresh:
                        head = C.headline({k: r for k, (r, _) in fresh.items()})
                        res.append(B.Line(max(t for _, t in fresh.values()),
                                          f"results {die} / {cfg}, duty {duty}: " + (head or ", ".join(sorted(fresh))), "", "", "system"))
        if res:
            blocks.append(B.Block("New results (shared ledger)", res))
    except Exception as exc:                                 # noqa: BLE001
        log.warning("owner_report: duty results unreadable (%s)", exc)
    return blocks


# -- 6. digests ----------------------------------------------------------------------------------------------------------------------

def _digest_row(d: dict, state: str) -> dict:
    period = d.get("period") or {}
    return {
        "id": d.get("id"), "scope": d.get("scope"), "kind": d.get("kind"), "title": d.get("title"), "status": d.get("status"),
        "state": state, "revision": d.get("revision") or 0,
        "items": len(d.get("items") or []) + int(d.get("more") or 0),
        "period_start": period.get("start", ""), "period_end": period.get("end", ""),
        "recipients": [{"email": r.get("email"), "role": r.get("role"), "lang": r.get("lang")} for r in d.get("recipients") or []],
        "text": d.get("text_body") or "", "bilingual": bool(d.get("bilingual")),
        "path": B.admin_path("digests", draft=str(d.get("id") or "")),
    }


def _auto_problem(d: dict) -> str:
    """Why the scheduled run did not (fully) send this open draft, "" when it did not try or nothing went wrong."""
    from motor_ai_sim.digest import approval as AP
    err, part = d.get("last_error") or {}, d.get("partial") or {}
    if err.get("by") == AP.AUTO_ACTOR:
        return str(err.get("message") or err.get("code") or ("send failed for %s recipients" % err.get("failed", "all")))
    if part.get("by") == AP.AUTO_ACTOR:
        return "sent to %s, failed for %s (%s)" % (part.get("sent", 0), part.get("failed", 0), ", ".join(part.get("outstanding") or []))
    return ""


def open_digests(start: float, end: float) -> list:
    """Section 6.  First the digests the scheduled run SENT by itself in this run (owner 2026-10-28): drafts closed as ``sent`` by
    ``AUTO_ACTOR`` between this report's ``end`` (06:00 local) and the next one, with who was skipped; then the drafts that still
    wait for the owner (``approve`` mode, or an automatic send that failed - ``problem`` says why), newest first.  Reading never
    marks a draft as opened."""
    from motor_ai_sim.digest import approval as AP
    from motor_ai_sim.digest import store as DS
    next_end = B.window_for((_dt.date.fromisoformat(B.report_date_for(end)) + _dt.timedelta(days=1)).isoformat())[1]
    sent, waiting = [], []
    for d in DS.list_drafts():
        if d.get("status") == "sent" and (d.get("sent") or {}).get("by") == AP.AUTO_ACTOR:
            at = ts_of((d.get("sent") or {}).get("at"))
            if at is not None and end <= at < next_end:
                info = d["sent"]
                row = _digest_row(d, "sent_auto")
                row.update(sent=int(info.get("sent") or 0), failed=int(info.get("failed") or 0), skipped=list(info.get("skipped") or []),
                           sent_at=info.get("at", ""))
                sent.append(row)
        elif d.get("status") in DS.OPEN_STATUSES:
            row = _digest_row(d, "waiting")
            row.update(new=_in(ts_of(d.get("created_at")), start, end), problem=_auto_problem(d))
            waiting.append(row)
    return sorted(sent, key=lambda r: r["sent_at"]) + waiting


def default_sources() -> B.Sources:
    return B.Sources(users=new_users, activity=user_activity, projects=_fmt_projects, productions=_fmt_productions,
                     motors=_fmt_motors, digests=open_digests)
