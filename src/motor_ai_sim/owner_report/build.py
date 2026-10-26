"""The owner's daily report - the PURE builder (no file, network or clock access of its own).

``build(date, sources, base_url=...)`` asks each injected source for the rows of one 24-hour window and renders ONE plain-text
and ONE HTML body.  The window is the 24 hours before 06:00 Europe/Ljubljana of ``date``.

Section order is fixed (owner 2026-10-27) and pinned by ``tests/test_owner_report.py``::

    1 New users (full information)   2 User activity   3 Projects   4 Productions   5 Motors   6 Digests to approve

The top summary line follows the same order.  A section with nothing says "no changes" (digests: "no digests today"); the whole
e-mail is still produced (a daily heartbeat).  One line per item, no images, staff / customer / factory clearly marked.
"""
from __future__ import annotations

import datetime as _dt
import html
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Ljubljana")
SEND_HOUR = 6                      # local hour of the daily report
MAX_LINES_PER_BLOCK = 60

#: THE order of the sections (and of the summary line).
SECTION_ORDER = ("users", "activity", "projects", "productions", "motors", "digests")
TITLES = {
    "users": "New users",
    "activity": "User activity",
    "projects": "Projects",
    "productions": "Productions",
    "motors": "Motors",
    "digests": "Digests to approve",
}
SHORT = {"digests": "Digests"}
NO_CHANGES = "no changes"
NO_DIGESTS = "no digests today"
ACTIVITY_NOTE = ("Active time, sessions and the report / datasheet counters are kept per UTC day: they cover the UTC days that "
                 "overlap the window.")
SIDE_LABEL = {"staff": "staff", "customer": "customer", "factory": "factory", "system": "system"}


# -- the window --------------------------------------------------------------------------------------------------------------------

def window_for(date: str) -> tuple[float, float]:
    """``(start, end)`` epoch seconds of the report of ``date`` (YYYY-MM-DD): from 06:00 local of the day before to 06:00 local of
    ``date``.  Calendar arithmetic in the local zone, so a daylight-saving change gives a 23 h or 25 h window, never a wrong hour."""
    day = _dt.date.fromisoformat(str(date))
    end = _dt.datetime.combine(day, _dt.time(SEND_HOUR), TZ)
    start = _dt.datetime.combine(day - _dt.timedelta(days=1), _dt.time(SEND_HOUR), TZ)
    return start.timestamp(), end.timestamp()


def report_date_for(now: float) -> str:
    """The report that is due at ``now``: today's date from 06:00 local on, yesterday's before that (an early manual run)."""
    local = _dt.datetime.fromtimestamp(now, TZ)
    day = local.date() if local.hour >= SEND_HOUR else local.date() - _dt.timedelta(days=1)
    return day.isoformat()


def local_stamp(ts: float) -> str:
    return _dt.datetime.fromtimestamp(float(ts), TZ).strftime("%d %H:%M")


def admin_path(section: str, **query: str) -> str:
    """The app path that opens an Admin section after sign-in (``web/src/lib/adminLink.ts`` reads it)."""
    from urllib.parse import urlencode
    q = {"tab": "admin", "section": section, **{k: v for k, v in query.items() if v}}
    return "/?" + urlencode(q)


# -- the data ----------------------------------------------------------------------------------------------------------------------

@dataclass
class Line:
    """One item: ``side`` marks who did it (staff | customer | factory | system | "")."""
    ts: float
    text: str
    path: str = ""
    who: str = ""
    side: str = ""


@dataclass
class Block:
    """A group of lines under one heading (a project, a production, "Catalogue")."""
    title: str
    lines: list = field(default_factory=list)
    path: str = ""


@dataclass
class Sources:
    """Where the rows come from.  Every callable takes ``(start, end)`` epoch seconds, except ``digests`` (no arguments needed, it
    gets them anyway).  Tests inject fixtures; ``sources.default_sources`` reads the real stores."""
    users: Callable[[float, float], list]
    activity: Callable[[float, float], list]
    projects: Callable[[float, float], list]
    productions: Callable[[float, float], list]
    motors: Callable[[float, float], list]
    digests: Callable[[float, float], list]


@dataclass
class Section:
    id: str
    title: str
    count: int
    rows: list
    empty_text: str = NO_CHANGES
    error: str = ""


@dataclass
class Report:
    date: str
    start: float
    end: float
    subject: str
    summary: str
    sections: list
    text: str
    html: str
    errors: list = field(default_factory=list)

    @property
    def counts(self) -> dict:
        return {s.id: s.count for s in self.sections}


# -- building ----------------------------------------------------------------------------------------------------------------------

def _count(section_id: str, rows: list) -> int:
    if section_id in ("projects", "productions", "motors"):
        return sum(len(getattr(b, "lines", []) or []) for b in rows)
    return len(rows)


def build(date: str, sources: Sources, *, base_url: str = "", extra_errors: Optional[list] = None) -> Report:
    start, end = window_for(date)
    errors: list = list(extra_errors or [])
    sections: list[Section] = []
    for sid in SECTION_ORDER:
        fn = getattr(sources, sid)
        err = ""
        try:
            rows = list(fn(start, end) or [])
        except Exception as exc:                                   # noqa: BLE001 - a broken source must not hide the others
            rows, err = [], f"{type(exc).__name__}: {str(exc)[:160]}"
            errors.append(f"{TITLES[sid]}: {err}")
        sections.append(Section(sid, TITLES[sid], _count(sid, rows), rows, NO_DIGESTS if sid == "digests" else NO_CHANGES, err))
    summary = summary_line(sections)
    subject = f"AeroStator owner report {date}"
    text = render_text(date, start, end, summary, sections, base_url, errors)
    body = render_html(date, start, end, summary, sections, base_url, errors, subject)
    return Report(date, start, end, subject, summary, sections, text, body, errors)


def summary_line(sections: list) -> str:
    parts = []
    for s in sections:
        name = SHORT.get(s.id, s.title)
        if s.error:
            parts.append(f"{name} unavailable")
        elif s.count == 0:
            parts.append(f"{name}: {s.empty_text}")
        else:
            parts.append(f"{name} {s.count}")
    return " | ".join(parts)


def _url(base: str, path: str) -> str:
    return (base.rstrip("/") + path) if path else ""


def _side(side: str) -> str:
    return f"[{SIDE_LABEL[side]}] " if side in SIDE_LABEL else ""


def _clip(rows: list) -> tuple[list, int]:
    return rows[:MAX_LINES_PER_BLOCK], max(0, len(rows) - MAX_LINES_PER_BLOCK)


# -- text ----------------------------------------------------------------------------------------------------------------------------

USER_FIELDS = (("company", "Company"), ("country", "Country"), ("signup", "Sign-up"), ("verified", "E-mail"), ("nda", "NDA"),
               ("verification", "Verification"), ("agreement", "Agreement"), ("data_sharing", "Data sharing"),
               ("projects", "Projects"), ("first_actions", "First actions"), ("created", "Created"))


def _user_value(u: dict, key: str) -> str:
    v = u.get(key)
    if isinstance(v, (list, tuple)):
        v = "; ".join(str(x) for x in v)
    return str(v).strip() if v not in (None, "") else "-"


def _tabs_text(tabs: Any) -> str:
    out = []
    for t in tabs or []:
        name, sec = (t.get("tab"), t.get("seconds")) if isinstance(t, dict) else (t[0], t[1])
        out.append(f"{name} {int(round(float(sec) / 60.0))} min")
    return ", ".join(out)


def _kv_text(d: Any) -> str:
    return ", ".join(f"{k} {v}" for k, v in (d or {}).items())


def activity_line(a: dict) -> str:
    """One line per active user."""
    parts = []
    if a.get("logins"):
        ips = ", ".join(a.get("ips") or [])
        parts.append(f"{a['logins']} sign-in{'s' if a['logins'] != 1 else ''}" + (f" (client {ips})" if ips else ""))
    if a.get("country"):
        parts.append(f"country {a['country']}")
    if a.get("active_s") or a.get("sessions"):
        parts.append(f"{int(round(float(a.get('active_s') or 0) / 60.0))} min active, {int(a.get('sessions') or 0)} session(s)")
    if a.get("tabs"):
        parts.append("modules: " + _tabs_text(a["tabs"]))
    if a.get("runs"):
        failed = f", {a['failed']} failed" if a.get("failed") else ""
        kinds = f" ({_kv_text(a.get('run_kinds'))})" if a.get("run_kinds") else ""
        parts.append(f"runs {a['runs']}{failed}{kinds}")
    if a.get("downloads"):
        parts.append("downloads: " + _kv_text(a["downloads"]))
    if a.get("assistant"):
        parts.append(f"assistant {a['assistant']}")
    if a.get("errors"):
        parts.append("errors: " + "; ".join(str(e) for e in a["errors"][:5]))
    return f"{a.get('email', '?')}" + (f" ({a['name']})" if a.get("name") else "") + f" {_side(a.get('side', '')).strip()} - " \
        + ("; ".join(parts) if parts else "active")


def _line_text(ln: Line, base: str) -> str:
    who = f" - {ln.who.strip()}" if ln.who else ""
    url = _url(base, ln.path)
    return f"- {local_stamp(ln.ts)} {_side(ln.side)}{ln.text}{who}" + (f" -> {url}" if url else "")


def render_text(date: str, start: float, end: float, summary: str, sections: list, base: str, errors: list) -> str:
    out = [f"AeroStator owner report {date}",
           f"Window: {local_stamp(start)} .. {local_stamp(end)} Europe/Ljubljana (last 24 h before 06:00). Internal: for the owner only.",
           "", summary, ""]
    for n, s in enumerate(sections, 1):
        out += ["=" * 60, f"{n}. {s.title}" + (f" ({s.count})" if s.count else ""), ""]
        if s.error:
            out += [f"source unavailable: {s.error}", ""]
            continue
        if s.count == 0:
            out += [s.empty_text, ""]
            continue
        if s.id == "users":
            for u in s.rows:
                out.append(f"- {u.get('email', '?')} - {u.get('name') or '-'} {_side(u.get('side', '')).strip()}")
                out += [f"    {label}: {_user_value(u, key)}" for key, label in USER_FIELDS]
                if u.get("path"):
                    out.append(f"    Open: {_url(base, u['path'])}")
        elif s.id == "activity":
            out += [f"- {activity_line(a)}" for a in s.rows]
            out += ["", ACTIVITY_NOTE]
        elif s.id == "digests":
            for d in s.rows:
                out += _digest_text(d, base)
        else:
            for b in s.rows:
                if not b.lines:
                    continue
                out.append(b.title + (f"  {_url(base, b.path)}" if b.path else ""))
                shown, more = _clip(sorted(b.lines, key=lambda x: x.ts))
                out += [_line_text(ln, base) for ln in shown]
                if more:
                    out.append(f"- and {more} more")
                out.append("")
        out.append("")
    if errors:
        out += ["=" * 60, "Source problems (the sections above may be incomplete):"] + [f"- {e}" for e in errors] + [""]
    return "\n".join(out).rstrip() + "\n"


def _digest_text(d: dict, base: str) -> list:
    rec = d.get("recipients") or []
    who = ", ".join(f"{r.get('email')} ({r.get('role') or 'member'})" for r in rec) or "no recipients"
    flag = "NEW" if d.get("new") else "waiting"
    out = [f"{d.get('kind', 'group').capitalize()}: {d.get('title', '')} - {flag}, status {d.get('status', 'pending')}"
           f", period {d.get('period_start', '')[:16]} .. {d.get('period_end', '')[:16]}",
           f"  Recipients ({len(rec)}): {who}",
           f"  Preview and Send (Admin -> Digests): {_url(base, d.get('path', ''))}",
           "  --- the text the recipients get (the footer with their own unsubscribe link is added per person) ---"]
    out += ["  " + ln if ln else "" for ln in str(d.get("text") or "").splitlines()]
    out += ["  --- end of text ---", ""]
    return out


# -- HTML ----------------------------------------------------------------------------------------------------------------------------

_BADGE_COLOR = {"staff": "#6b7280", "customer": "#2563eb", "factory": "#b45309", "system": "#9ca3af"}


def _badge(side: str) -> str:
    if side not in SIDE_LABEL:
        return ""
    return (f'<span style="font-size:11px;color:#fff;background:{_BADGE_COLOR[side]};border-radius:3px;padding:1px 5px;'
            f'margin-right:4px">{SIDE_LABEL[side]}</span>')


def _a(base: str, path: str, label: str = "open") -> str:
    url = _url(base, path)
    return f' <a href="{html.escape(url, quote=True)}" style="color:#2563eb">{html.escape(label)}</a>' if url else ""


def render_html(date: str, start: float, end: float, summary: str, sections: list, base: str, errors: list, subject: str) -> str:
    h: list[str] = []
    h.append(f'<h1 style="margin:0 0 4px;font-size:19px;color:#0f172a">AeroStator owner report {html.escape(date)}</h1>'
             f'<p style="margin:0 0 12px;font-size:12px;color:#6b7280">{html.escape(local_stamp(start))} .. {html.escape(local_stamp(end))} '
             'Europe/Ljubljana (last 24 h before 06:00). Internal, for the owner only.</p>'
             f'<p style="margin:0 0 16px;font-size:14px;font-weight:bold;color:#0f172a">{html.escape(summary)}</p>')
    for n, s in enumerate(sections, 1):
        h.append(f'<h2 style="margin:20px 0 6px;font-size:16px;color:#0f172a;border-top:1px solid #e5e7eb;padding-top:12px">'
                 f'{n}. {html.escape(s.title)}{f" ({s.count})" if s.count else ""}</h2>')
        if s.error:
            h.append(f'<p style="color:#b91c1c;font-size:13px">source unavailable: {html.escape(s.error)}</p>')
            continue
        if s.count == 0:
            h.append(f'<p style="margin:0;font-size:14px;color:#6b7280">{html.escape(s.empty_text)}</p>')
            continue
        if s.id == "users":
            for u in s.rows:
                h.append(f'<p style="margin:10px 0 2px;font-size:14px">{_badge(u.get("side", ""))}<b>{html.escape(str(u.get("email", "?")))}</b>'
                         f' - {html.escape(str(u.get("name") or "-"))}{_a(base, u.get("path", ""), "open")}</p>'
                         '<table style="border-collapse:collapse;font-size:13px;margin-left:8px">')
                for key, label in USER_FIELDS:
                    h.append(f'<tr><td style="color:#6b7280;padding:1px 10px 1px 0;vertical-align:top">{label}</td>'
                             f'<td>{html.escape(_user_value(u, key))}</td></tr>')
                h.append("</table>")
        elif s.id == "activity":
            h.append('<ul style="margin:0;padding-left:20px;font-size:13.5px;line-height:1.5">')
            for a in s.rows:
                h.append(f'<li style="margin:0 0 4px">{_badge(a.get("side", ""))}{html.escape(activity_line(a))}</li>')
            h.append("</ul>" f'<p style="margin:4px 0 0;font-size:11.5px;color:#9ca3af">{html.escape(ACTIVITY_NOTE)}</p>')
        elif s.id == "digests":
            for d in s.rows:
                h += _digest_html(d, base)
        else:
            for b in s.rows:
                if not b.lines:
                    continue
                h.append(f'<p style="margin:12px 0 2px;font-size:14px"><b>{html.escape(b.title)}</b>{_a(base, b.path, "open")}</p>'
                         '<ul style="margin:0;padding-left:20px;font-size:13.5px;line-height:1.5">')
                shown, more = _clip(sorted(b.lines, key=lambda x: x.ts))
                for ln in shown:
                    who = f' <span style="color:#6b7280">- {html.escape(ln.who)}</span>' if ln.who else ""
                    h.append(f'<li style="margin:0 0 3px"><span style="color:#6b7280">{html.escape(local_stamp(ln.ts))}</span> '
                             f'{_badge(ln.side)}{html.escape(ln.text)}{who}{_a(base, ln.path)}</li>')
                if more:
                    h.append(f'<li>and {more} more</li>')
                h.append("</ul>")
    if errors:
        h.append('<h2 style="margin:20px 0 6px;font-size:15px;color:#b91c1c">Source problems</h2><ul style="font-size:13px">'
                 + "".join(f"<li>{html.escape(str(e))}</li>" for e in errors) + "</ul>")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><title>{html.escape(subject)}</title></head>
<body style="margin:0;padding:0;background:#f4f5f7;color:#1f2937;font-family:Arial,Helvetica,sans-serif">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="#f4f5f7"><tr><td align="center" style="padding:20px 10px">
<table role="presentation" width="720" cellpadding="0" cellspacing="0" bgcolor="#ffffff" style="max-width:720px;width:100%;background:#ffffff;border-radius:8px">
<tr><td style="padding:22px 24px">
{chr(10).join(h)}
</td></tr></table></td></tr></table></body></html>
"""


def _digest_html(d: dict, base: str) -> list:
    rec = d.get("recipients") or []
    who = ", ".join(f"{r.get('email')} ({r.get('role') or 'member'})" for r in rec) or "no recipients"
    flag = "NEW" if d.get("new") else "waiting"
    h = [f'<p style="margin:14px 0 2px;font-size:14px"><b>{html.escape(str(d.get("kind", "group")).capitalize())}: '
         f'{html.escape(str(d.get("title", "")))}</b> - {flag}, status {html.escape(str(d.get("status", "pending")))}, period '
         f'{html.escape(str(d.get("period_start", ""))[:16])} .. {html.escape(str(d.get("period_end", ""))[:16])}'
         f'{_a(base, d.get("path", ""), "Preview and Send")}</p>',
         f'<p style="margin:0 0 4px;font-size:12.5px;color:#475569">Recipients ({len(rec)}): {html.escape(who)}</p>',
         '<div style="border-left:3px solid #cbd5e1;padding:2px 10px;margin:4px 0 8px;font-size:13px;white-space:pre-wrap;color:#1f2937">'
         f'{html.escape(str(d.get("text") or ""))}</div>',
         '<p style="margin:0;font-size:11.5px;color:#9ca3af">The text the recipients get; the footer with their own unsubscribe link '
         'is added per person.</p>']
    return h
