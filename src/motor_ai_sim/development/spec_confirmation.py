"""The confirmation mail to the account that submits a specification (owner 2026-11-12).

A submit used to mail only the staff; the submitter got nothing.  Now every NEW specification version sends ONE service message to the
account that pressed Submit (customer or staff), in the language stored for that account (English by default, zh-CN for Chinese
accounts): what was submitted, the status, what happens next, the link.  It is not marketing: it ignores the communication-preference
topics and has no unsubscribe footer.  No prices, quotes, costs or lead times anywhere (hard rule of the product).

ALL wording lives in :data:`TEXTS` below, so it is reviewed in one place.  The summary is built from a whitelist of form fields and uses
the form's own short labels (``schema.json``); commercial fields (``6_3``, ``target_price``) and free-text notes are never read.
:func:`build` is pure (record in, text out); :func:`notify_submitter` sends and never raises.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from motor_ai_sim.development import models

log = logging.getLogger(__name__)

SIGNATURE = "AeroStator support, support@aerostator.com"

TEXTS: dict[str, dict[str, str]] = {
    "en": {
        "colon": ": ", "comma": ", ", "lp": "(", "rp": ")",
        "subject": "AeroStator: specification v{version} submitted - {title}",
        "intro": 'Your specification version {version} for "{title}" was submitted on {date}.',
        "status": "Status: Specification submitted - your motor is being prepared for the calculation.",
        "summary_head": "What you submitted:",
        "continuous": "continuous",
        "at": "at",
        "not_known": "Not known yet: {count} field(s)",
        "next": ('What happens next: your motor is already being prepared for the calculation at your operating points '
                 '(electromagnetic, thermal and mechanical). The results will appear in the project under "Motors & results". '
                 'You can change the specification and submit a new version at any time.'),
        "link": "Project specification: {link}",
        "ambient": "ambient {value} {unit}",
        "dc_source": "DC source {value} V",
        "ac_mains": "AC mains {value} V, {freq} Hz",
        "active": "active",
        "complete": "complete",
        "by": "x",
        "utc": "UTC",
    },
    "zh-CN": {
        "colon": "：", "comma": "，", "lp": "（", "rp": "）",
        "subject": "AeroStator：规格书第 {version} 版已提交 - {title}",
        "intro": "您为“{title}”提交的规格书第 {version} 版已于 {date} 提交。",
        "status": "状态：规格书已提交，您的电机正在准备计算。",
        "summary_head": "您提交的内容：",
        "continuous": "连续",
        "at": "，转速",
        "not_known": "尚未确定：{count} 项",
        "next": ("接下来：您的电机已在按您的工作点（电磁、热和机械）准备计算。结果将显示在项目的“电机与结果”中。"
                 "您可以随时修改规格书并提交新版本。"),
        "link": "项目规格书：{link}",
        "ambient": "环境温度 {value} {unit}",
        "dc_source": "直流电源 {value} V",
        "ac_mains": "交流市电 {value} V，{freq} Hz",
        "active": "有效部分",
        "complete": "整机",
        "by": "x",
        "utc": "UTC",
    },
}

#: short labels the mail uses for the operating points (the form's own label for the field is ``key_points``)
_POINT_TORQUE_UNIT = "N·m"


def lang_for(email: str) -> str:
    """``zh-CN`` for an account whose stored interface language is Chinese, else ``en`` (the same store the mailings read)."""
    try:
        from motor_ai_sim import link_pages as LP, mailing
        return LP.norm_lang(mailing.stored_locales().get(str(email or "").strip().lower()))
    except Exception:                                        # noqa: BLE001 - the language never blocks the mail
        return "en"


def _num(x: Any) -> str:
    """``0.6`` -> ``0.6``, ``2000.0`` -> ``2000``: the number as a person writes it."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return ""
    return f"{x:.6g}"


def _worst(x: Any) -> Any:
    """A stored range is read as its worst value (its maximum)."""
    return x.get("maximum") if isinstance(x, dict) else x


def _pick(label: Any, lang: str) -> str:
    return str((label or {}).get(lang) or (label or {}).get("en") or "") if isinstance(label, dict) else ""


def _field_schema() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for section in models.schema()["sections"]:
        for field in section.get("fields", []):
            out[field["id"]] = field
    return out


def _choice_label(prop: dict, value: Any, lang: str) -> str:
    """The form's label of a stored choice (an option or a suggestion), matched without case; else the stored text."""
    text = str(value or "").strip()
    for choice in list(prop.get("options") or []) + list(prop.get("suggestions") or []):
        if isinstance(choice, dict) and str(choice.get("value", "")).strip().lower() == text.lower():
            return _pick(choice.get("label"), lang) or text
    return text


def _prop(field: dict, pid: str) -> dict:
    return next((p for p in field.get("properties", []) if p.get("id") == pid), {})


def _active(fields: dict, fid: str) -> Any:
    return models._active_value(fields, fid)


def _points(value: Any, T: dict, lang: str) -> list[str]:
    rows = []
    for row in value if isinstance(value, list) else []:
        if not isinstance(row, dict):
            continue
        name = " ".join(str(row.get("name") or "").split())[:60]
        torque, speed, duration = _num(_worst(row.get("torque_Nm"))), _num(_worst(row.get("speed_rpm"))), row.get("duration_s")
        text = f"{torque} {_POINT_TORQUE_UNIT}" if torque else ""
        if speed:
            text += f" {T['at']} {speed} rpm" if lang == "en" else f"{T['at']} {speed} rpm"
        if isinstance(duration, str) and duration.strip().lower() == "continuous":
            text += f"{T['comma']}{T['continuous']}"
        elif _num(_worst(duration)):
            text += f"{T['comma']}{_num(_worst(duration))} s"
        text = text.strip(" ,")
        if name or text:
            rows.append(f"    {name}{T['colon']}{text}" if name and text else f"    {name or text}")
    return rows


def _supply(value: Any, T: dict) -> str:
    if not isinstance(value, dict):
        return ""
    kind = value.get("supply_type")
    if kind == "dc_source" and _num(value.get("dc_source_V")):
        return T["dc_source"].format(value=_num(value["dc_source_V"]))
    if kind == "ac_mains" and _num(value.get("ac_line_voltage_V")):
        return T["ac_mains"].format(value=_num(value["ac_line_voltage_V"]), freq=_num(value.get("ac_frequency_Hz")) or "-")
    pack = " ".join(x for x in (f"{_num(value.get('series_cells'))}S" if _num(value.get("series_cells")) else "",
                                str(value.get("chemistry") or "").strip()) if x)
    volts = [_num(value.get(k)) for k in ("voltage_min_V", "voltage_nominal_V", "voltage_max_V")]
    volts = " / ".join(v for v in volts if v)
    return T['comma'].join(x for x in (pack, f"{volts} V" if volts else "") if x)


def _cooling(value: Any, ambient: Any, field: dict, T: dict, lang: str) -> str:
    if not isinstance(value, dict):
        return ""
    medium = models.cooling_medium(value)[0]
    parts = []
    if medium:
        parts.append(_choice_label(_prop(field, "cooling_medium"), medium, lang))
    detail = []
    air = value.get("airflow") if isinstance(value.get("airflow"), dict) else {}
    if air.get("mode"):
        detail.append(_choice_label(_prop(_prop(field, "airflow"), "mode"), air["mode"], lang))
    elif value.get("cooling_type"):
        detail.append(" ".join(str(value["cooling_type"]).split())[:60])
    if value.get("coolant"):
        detail.append(" ".join(str(value["coolant"]).split())[:40])
    if _num(value.get("inlet_temperature_C")):
        detail.append(f"{_num(value['inlet_temperature_C'])} °C")
    if detail:
        d = T['comma'].join(detail)
        parts.append(f"{T['lp']}{d.lower() if lang == 'en' else d}{T['rp']}")
    text = ("" if lang != "en" else " ").join(parts)
    temp = _num(_worst((ambient or {}).get("temperature_max_C"))) if medium == "air" and isinstance(ambient, dict) else ""
    if temp:
        text = T['comma'].join(x for x in (text, T["ambient"].format(value=temp, unit="°C")) if x)
    return text


def summary_lines(draft: dict, lang: str) -> list[str]:
    """The lines of the summary block: only the whitelisted fields that have a value, with the form's own short labels."""
    T, S = TEXTS[lang], _field_schema()
    fields = draft.get("fields") or {}
    label = lambda fid: _pick(S[fid].get("short_label") or S[fid].get("label"), lang)   # noqa: E731
    lines: list[str] = []

    app = _active(fields, "1_1")
    if isinstance(app, str) and app.strip():
        lines.append(f"- {label('1_1')}{T['colon']}{_choice_label(S['1_1'], app, lang)[:80]}")
    pts = _points(_active(fields, "key_points"), T, lang)
    if pts:
        lines.append(f"- {label('key_points')}{T['colon'].rstrip()}")
        lines.extend(pts)
    speed = _active(fields, "1_6")
    if isinstance(speed, dict) and _num(_worst(speed.get("maximum_rpm"))):
        lines.append(f"- {label('1_6')}{T['colon']}{_num(_worst(speed['maximum_rpm']))} rpm")
    supply = _supply(_active(fields, "2_1"), T)
    if supply:
        lines.append(f"- {label('2_1')}{T['colon']}{supply}")
    cooling = _cooling(_active(fields, "3_1"), _active(fields, "3_2"), S["3_1"], T, lang)
    if cooling:
        lines.append(f"- {label('3_1')}{T['colon']}{cooling}")
    size = _active(fields, "4_1")
    if isinstance(size, dict):
        d, n = _num(_worst(size.get("outer_diameter_mm"))), _num(_worst(size.get("length_mm")))
        if d or n:
            lines.append(f"- {label('4_1')}{T['colon']}{d or '-'} {T['by']} {n or '-'} mm")
    mass = _active(fields, "4_2")
    if isinstance(mass, dict):
        a, c = _num(_worst(mass.get("active_mass_kg"))), _num(_worst(mass.get("complete_mass_kg")))
        bits = [f"{a} kg {T['active']}" if a else "", f"{c} kg {T['complete']}" if c else ""]
        bits = [b for b in bits if b]
        if bits:
            lines.append(f"- {label('4_2')}{T['colon']}{' / '.join(bits)}")
    try:
        unknown = int(models.completeness(draft).get("unknown") or 0)
    except Exception:                                        # noqa: BLE001 - a count never blocks the mail
        unknown = 0
    if unknown:
        lines.append(f"- {T['not_known'].format(count=unknown)}")
    return lines


def _date_utc(stamp: Any, T: dict) -> str:
    try:
        t = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        t = t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)
    except (TypeError, ValueError):
        t = datetime.now(timezone.utc)
    return f"{t:%Y-%m-%d %H:%M} {T['utc']}"


def build(project: dict, project_id: str, version: dict, lang: str, base: str) -> tuple[str, str]:
    """(subject, plain-text body) of the confirmation of one submitted version record (``{version, created_at, draft}``)."""
    lang = lang if lang in TEXTS else "en"
    T = TEXTS[lang]
    title = " ".join(str(project.get("title") or project_id).split())[:120]
    n = int(version.get("version") or 0)
    summary = summary_lines(version.get("draft") or {}, lang)
    parts = [T["intro"].format(version=n, title=title, date=_date_utc(version.get("created_at"), T)), "", T["status"], ""]
    if summary:
        parts += [T["summary_head"], *summary, ""]
    parts += [T["next"], "", T["link"].format(link=f"{base}/projects/{project_id}/specification"), "", SIGNATURE, ""]
    return T["subject"].format(version=n, title=title), "\n".join(parts)


def notify_submitter(user: dict, project_id: str, version: dict, project: Optional[dict] = None) -> bool:
    """Send the confirmation to the account that submitted.  Never raises: a mail failure only logs a warning."""
    try:
        from motor_ai_sim import auth_email as E
        to = str(user.get("email") or "").strip().lower()
        if not to or "@" not in to:
            return False
        subject, body = build(project or {}, project_id, version, lang_for(to), E.app_url())
        return bool(E.send(to, subject, body, kind="spec-submitted"))
    except Exception:                                        # noqa: BLE001 - never fail the submit
        log.warning("specification confirmation mail failed for %s", project_id, exc_info=True)
        return False
