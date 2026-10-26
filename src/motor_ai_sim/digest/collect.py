"""The activity collector: what happened in ONE development project or ONE production between two instants.

Pure reading (nothing is written).  Every item carries

* ``en`` / ``zh``  one line in each language (the Chinese line is the existing Chinese field when there is one, else the
  English text marked as such; machine-translated text is marked as machine-translated in both languages);
* ``link``         the app path of the exact page (``return_path.project_path`` / ``production_path``) or None;
* ``requires``     the capabilities a reader needs to be allowed to see it: ``internal`` (our own side: engineers and owners,
  not customers and not the factory) and ``costs`` (the cost sheet).  The digest is ONE text for the whole group, so
  :mod:`motor_ai_sim.digest.build` keeps only the items every recipient may see.

Sources: the project's ``audit.jsonl`` / ``thread.jsonl`` and the stored duty results of its motors (``duty_results``);
the production's ``audit.jsonl`` and its JSON files.  "Read" events (opening a page, downloading) are not activity.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from motor_ai_sim import return_path as RP
from motor_ai_sim.digest import store as DS

#: Capability names (see module docstring).
INTERNAL = "internal"
COSTS = "costs"

EXCERPT = 90


@dataclass
class Item:
    kind: str
    ts: float
    en: str
    zh: str
    link: Optional[str] = None
    requires: frozenset = field(default_factory=frozenset)
    key: str = ""
    #: ``(die, config)`` of the motor this line is about: the line may be shown only when EVERY recipient may open that motor
    motor: Optional[tuple] = None


# -- small helpers ---------------------------------------------------------------------------------------------------------

def excerpt(text: Any, n: int = EXCERPT) -> str:
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict):
                    rows.append(r)
    except OSError:
        pass
    return rows


def _in(ts: Optional[float], start: float, end: float) -> bool:
    return ts is not None and start <= ts < end


def bilingual(bi: Any) -> tuple[str, str]:
    """``(en, zh)`` display text of a production bilingual field, machine text marked, a missing side filled from the other."""
    if not isinstance(bi, dict):
        s = excerpt(bi)
        return s, s
    status = bi.get("status") if isinstance(bi.get("status"), dict) else {}
    en, zh = excerpt(bi.get("en")), excerpt(bi.get("zh"))
    if en and status.get("en") == "machine":
        en += " [machine-translated]"
    if zh and status.get("zh") == "machine":
        zh += " [机器翻译]"
    if not zh and en:
        zh = en + " [英文原文]"
    if not en and zh:
        en = zh + " [Chinese original]"
    return en, zh


def _name(email: str, fallback_en: str, fallback_zh: str) -> tuple[str, str]:
    """Display name of a person (never the address): the account name, else a role word."""
    try:
        from motor_ai_sim import users
        name = str((users.get_user(email) or {}).get("name") or "").strip()
    except Exception:                                        # noqa: BLE001
        name = ""
    if name and "@" not in name:
        return name, name
    return fallback_en, fallback_zh


def _merge(items: Iterable[Item]) -> list[Item]:
    """Items with the same ``key`` become one line with a count (e.g. five BOM saves -> "Bill of materials updated (5x)")."""
    out: dict[str, Item] = {}
    counts: dict[str, int] = {}
    order: list[str] = []
    for it in items:
        k = it.key or f"{it.kind}:{it.ts}:{it.en}"
        if k not in out:
            out[k] = it
            counts[k] = 1
            order.append(k)
        else:
            counts[k] += 1
            if it.ts > out[k].ts:
                it.requires = out[k].requires | it.requires
                out[k] = it
    res = []
    for k in order:
        it = out[k]
        n = counts[k]
        if n > 1 and it.key:
            it = Item(it.kind, it.ts, f"{it.en} ({n}x)", f"{it.zh}（{n}次）", it.link, it.requires, it.key, it.motor)
        res.append(it)
    return res


# -- development projects -----------------------------------------------------------------------------------------------------

#: Result kinds that make a "new results" line.
RESULT_KINDS = ("em_run", "coupled", "coupled_limit", "thermal", "duty_cycle", "rotor_stress", "modes",
                "critical_speeds", "pwm", "controller")


def stored_results(die: str, config: str) -> dict:
    """``{duty: {kind: entry}}`` the way every member's project motor view reads them at its BOTTOM layers.

    The project motor view (``development/motors.py`` -> ``duty_results.index``) merges, deepest first, the die's own ledger, the
    shared catalog's ledger and the published author's ledger, then puts the caller's OWN workspace on top.  A digest is one text
    for the whole group, so it reads only what all of them see: the layers without the caller's workspace - and it reads them with
    the store forced to the shared catalog, so the answer does not depend on who (or which process workspace) runs the digest."""
    from motor_ai_sim import duty_results as DR
    out: dict = {}
    try:
        with DR.using_store(DR.shared_store_path()):
            paths = list(DR._fallback_stores(die)) + [DR.store_path()]       # deepest first, the shared ledger last
    except Exception:                                        # noqa: BLE001
        return out
    seen: set = set()
    for p in paths:
        if str(p) in seen:
            continue
        seen.add(str(p))
        try:
            node = DR._node(DR._read_store(p), die, config)
        except Exception:                                    # noqa: BLE001
            continue
        for duty, kinds in node.items():
            if isinstance(kinds, dict):
                out.setdefault(str(duty), {}).update({k: v for k, v in kinds.items() if isinstance(v, dict)})
    return out


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if f == f and abs(f) != float("inf") else None


def _first_num(sources: Iterable[dict], keys: Iterable[str]) -> Optional[float]:
    keys = tuple(keys)
    for src in sources:
        for k in keys:
            v = _num(src.get(k))
            if v is not None:
                return v
    return None


def headline(kinds: dict) -> str:
    """The few numbers worth a line: torque, power, efficiency, hottest winding / magnet, mechanical safety factor."""
    parts: list[str] = []
    em = kinds.get("em_run") if isinstance(kinds.get("em_run"), dict) else {}
    nums = em.get("numbers") if isinstance(em.get("numbers"), dict) else {}
    coupled = kinds.get("coupled") if isinstance(kinds.get("coupled"), dict) else {}
    cem = coupled.get("em") if isinstance(coupled.get("em"), dict) else {}
    src = (nums, cem)
    t = _first_num(src, ("T_avg_Nm", "T_em_avg_Nm", "torque_nm"))
    if t is not None:
        parts.append(f"T {t:.3g} N·m")
    p = _first_num(src, ("P_mech_W",))
    if p is not None:
        parts.append(f"P {p / 1000:.3g} kW")
    eta = _first_num(src, ("efficiency_pct",))
    if eta is None:
        e2 = _num(coupled.get("efficiency_shaft"))
        eta = e2 * 100 if e2 is not None and e2 <= 1.5 else e2
    if eta is not None:
        parts.append(f"η {eta:.3g} %")
    coil = _num(coupled.get("coil_temp_c"))
    if coil is not None:
        parts.append(f"winding {coil:.0f} °C")
    mag = _num(coupled.get("magnet_temp_c"))
    if mag is not None:
        parts.append(f"magnet {mag:.0f} °C")
    if coil is None:
        th = kinds.get("thermal") if isinstance(kinds.get("thermal"), dict) else {}
        hot = [m for c in (th.get("components") or {}).values() if isinstance(c, dict) for m in [_num(c.get("max"))] if m is not None]
        if hot:
            parts.append(f"hottest part {max(hot):.0f} °C")
    rs = kinds.get("rotor_stress") if isinstance(kinds.get("rotor_stress"), dict) else {}
    sf = _num(rs.get("sf_min"))
    if sf is not None:
        parts.append(f"rotor SF {sf:.2g}")
    return ", ".join(parts)


def _motor_key(die: Any, config: Any) -> str:
    return f"{str(die or '').strip().lower()}|{str(config or '').strip().lower()}"


def collect_project(project: dict, start: float, end: float, *,
                    results: Callable[[str, str], dict] = stored_results) -> list[Item]:
    pid = str(project.get("id") or "")
    links = {_motor_key(m.get("die"), m.get("config")): m for m in project.get("linked_motors") or [] if isinstance(m, dict)}

    def is_trial(die: str, config: str) -> bool:
        m = links.get(_motor_key(die, config))
        return bool(m) and str(m.get("status") or "current") == "trial"

    def motor_req(die: str, config: str) -> frozenset:
        # a trial variant is the engineers' scratch work: not shown to the customer
        return frozenset({INTERNAL}) if is_trial(die, config) else frozenset()

    def motor_link(die: str, config: str, view: str = "motors") -> Optional[str]:
        return RP.project_path(pid, view, die=die, config=config) or RP.project_path(pid, view)

    items: list[Item] = []
    from motor_ai_sim.development import store as PS
    for ev in _read_jsonl(PS.path_for(pid) / "audit.jsonl"):
        ts = DS.parse_ts(ev.get("time"))
        if not _in(ts, start, end):
            continue
        action, entity, eid = str(ev.get("action") or ""), str(ev.get("entity") or ""), str(ev.get("entity_id") or "")
        det = ev.get("details") if isinstance(ev.get("details"), dict) else {}
        die, _, config = eid.partition("/")
        if action == "motor.link":
            items.append(Item("motor_added", ts, f"Design variant added: {die} / {config}", f"新增设计方案：{die} / {config}",
                              motor_link(die, config), frozenset({INTERNAL}), key=f"link:{eid}", motor=(die, config)))
        elif action == "motor.unlink":
            items.append(Item("motor_removed", ts, f"Design variant removed: {die} / {config}", f"移除设计方案：{die} / {config}",
                              RP.project_path(pid, "motors"), frozenset({INTERNAL}), key=f"unlink:{eid}", motor=(die, config)))
        elif action == "motor.status":
            new = str(det.get("new") or "")
            items.append(Item("motor_status", ts, f"Variant {die} / {config} is now {new or 'updated'}",
                              f"方案 {die} / {config} 状态更新为 {new or '已更新'}", motor_link(die, config),
                              frozenset({INTERNAL}), key=f"status:{eid}", motor=(die, config)))
        elif action == "motor.note":
            note = excerpt(det.get("new"))
            if note:
                items.append(Item("motor_note", ts, f"Design step note, {die} / {config}: {note}",
                                  f"设计步骤备注，{die} / {config}：{note}", motor_link(die, config), motor_req(die, config),
                                  key=f"note:{eid}", motor=(die, config)))
        elif action == "motor.recommended" and det.get("value"):
            items.append(Item("recommended", ts, f"Recommended variant: {die} / {config}", f"推荐方案：{die} / {config}",
                              motor_link(die, config), motor_req(die, config), key=f"rec:{eid}", motor=(die, config)))
        elif action == "motor.report_reviewed" and det.get("value"):
            items.append(Item("report_reviewed", ts, f"Report checked for {die} / {config}", f"{die} / {config} 的报告已审核",
                              motor_link(die, config), motor_req(die, config), key=f"rep:{eid}", motor=(die, config)))
        elif action == "submit" and entity == "spec_version":
            items.append(Item("spec_version", ts, f"Specification version {eid} submitted", f"技术规格第 {eid} 版已提交",
                              RP.project_path(pid, "specification"), frozenset(), key=f"spec:{eid}"))
        elif action == "invite_claimed" or (action == "invite" and not det.get("pending")):
            role = str(det.get("role") or "")
            items.append(Item("member_joined", ts, f"A new {role or 'member'} joined the project", f"新成员加入项目（{role or '成员'}）",
                              RP.project_path(pid), frozenset(), key=f"member:{eid}"))
        elif action == "update" and entity == "project":
            fields = [str(f) for f in det.get("fields") or []]
            public = [f for f in fields if f in ("title", "status", "customer_org")]
            if "notes" in fields:
                # the internal notes: only that they changed, and only for the engineering side
                items.append(Item("notes", ts, "Internal project notes updated", "内部项目备注已更新",
                                  RP.project_path(pid, "notes"), frozenset({INTERNAL}), key="notes"))
            if public:
                items.append(Item("project", ts, "Project details updated (" + ", ".join(public) + ")",
                                  "项目信息已更新（" + ", ".join(public) + "）", RP.project_path(pid), frozenset(), key="project"))
    for m in _read_jsonl(PS.path_for(pid) / "thread.jsonl"):
        if m.get("kind") == "resolution":
            continue
        ts = DS.parse_ts(m.get("created_at"))
        if not _in(ts, start, end):
            continue
        who_en, who_zh = _name(str(m.get("author_email") or ""), "A team member", "团队成员")
        text = excerpt(m.get("text"))
        mid = str(m.get("id") or "")
        items.append(Item("message", ts, f"New message from {who_en}: {text}", f"{who_zh} 的新留言：{text}",
                          RP.project_path(pid, "messages", item=mid) or RP.project_path(pid, "messages"), frozenset(), key=f"msg:{mid}"))
    # new computed points of the project's motors
    for link in links.values():
        die, config = str(link.get("die") or ""), str(link.get("config") or "")
        if not die or not config:
            continue
        try:
            by_duty = results(die, config)
        except Exception:                                    # noqa: BLE001 - a broken ledger must not stop the digest
            by_duty = {}
        for duty, kinds in sorted(by_duty.items()):
            fresh = {}
            for k in RESULT_KINDS:
                row = kinds.get(k)
                if isinstance(row, dict):
                    ts = DS.parse_ts(row.get("computed_at") or row.get("recorded_at"))
                    if _in(ts, start, end):
                        fresh[k] = (row, ts)
            if not fresh:
                continue
            ts = max(t for _, t in fresh.values())
            head = headline({k: r for k, (r, _) in fresh.items()})
            tail = f": {head}" if head else f" ({', '.join(sorted(fresh))})"
            items.append(Item("results", ts, f"New results, {die} / {config}, duty {duty}{tail}",
                              f"新计算结果，{die} / {config}，工况 {duty}{tail}", motor_link(die, config),
                              motor_req(die, config), key=f"res:{die}/{config}/{duty}", motor=(die, config)))
    return _merge(items)


# -- productions ------------------------------------------------------------------------------------------------------------------

def collect_production(production: dict, start: float, end: float) -> list[Item]:
    from motor_ai_sim.production import store as PR
    die, config = str(production.get("die") or ""), str(production.get("config") or "")

    def link(tab: Optional[str] = None, **kw: Any) -> Optional[str]:
        return RP.production_path(die, config, tab, **kw) or RP.production_path(die, config, tab) or RP.production_path(die, config)

    docs = {d.get("id"): d for d in (PR.load_file(die, config, "documents.json") or {}).get("documents", []) if isinstance(d, dict)}
    threads = {t.get("id"): t for t in (PR.load_file(die, config, "threads.json") or {}).get("threads", []) if isinstance(t, dict)}
    proposals = {p.get("id"): p for p in (PR.load_file(die, config, "proposals.json") or {}).get("proposals", []) if isinstance(p, dict)}
    items: list[Item] = []
    for ev in _read_jsonl(PR.audit_path(die, config)):
        ts = DS.parse_ts(ev.get("time"))
        if not _in(ts, start, end):
            continue
        action, entity, eid = str(ev.get("action") or ""), str(ev.get("entity") or ""), str(ev.get("entity_id") or "")
        snap = ev.get("snapshot") if isinstance(ev.get("snapshot"), dict) else {}
        if action == "publish_revision" and entity == "revision":
            rev = snap.get("revision") if isinstance(snap.get("revision"), dict) else {}
            s_en, s_zh = bilingual(rev.get("summary"))
            n = len([c for c in rev.get("changes") or [] if c])
            tail_en, tail_zh = (f" ({n} changes)", f"（{n} 项变更）") if n else ("", "")
            items.append(Item("revision", ts, f"Revision {eid} published: {s_en}{tail_en}", f"版本 {eid} 已发布：{s_zh}{tail_zh}",
                              link("changes", item=eid), frozenset(), key=f"rev:{eid}"))
        elif action == "create" and entity == "document":
            d = docs.get(eid) or (snap.get("doc") if isinstance(snap.get("doc"), dict) else {})
            internal = bool(d.get("internal"))
            name = excerpt(d.get("name") or "file", 70)
            items.append(Item("document", ts, f"New document: {name}", f"新文档：{name}",
                              link("documents", ids=[eid]) if eid else link("documents"),
                              frozenset({INTERNAL}) if internal else frozenset(), key=f"doc:{eid}"))
        elif action == "create" and entity == "thread_message":
            tid = str(snap.get("thread_id") or "")
            msg = next((m for m in (threads.get(tid) or {}).get("messages", []) if isinstance(m, dict) and m.get("id") == eid), {})
            t_en, t_zh = bilingual(msg.get("text"))
            who_en, who_zh = _name(str(msg.get("author_email") or ev.get("who") or ""), "A team member", "团队成员")
            items.append(Item("message", ts, f"New message from {who_en}: {t_en}", f"{who_zh} 的新留言：{t_zh}",
                              link("discussion", item=tid) if tid else link("discussion"), frozenset(), key=f"msg:{eid}"))
        elif action == "create" and entity == "proposal":
            p = snap.get("proposal") if isinstance(snap.get("proposal"), dict) else proposals.get(eid) or {}
            t_en, t_zh = bilingual(p.get("text"))
            items.append(Item("proposal", ts, f"New proposal: {t_en}", f"新提案：{t_zh}", link("proposals", item=eid),
                              frozenset(), key=f"prop:{eid}"))
        elif entity == "proposal" and action in ("accepted", "rejected", "implemented"):
            zh = {"accepted": "已接受", "rejected": "已拒绝", "implemented": "已实施"}[action]
            t_en, t_zh = bilingual((proposals.get(eid) or {}).get("text"))
            items.append(Item("proposal_decision", ts, f"Proposal {action}: {t_en}", f"提案{zh}：{t_zh}", link("proposals", item=eid),
                              frozenset(), key=f"propd:{eid}:{action}"))
        elif action == "update" and entity == "bom":
            tooling = eid.endswith(":tooling")
            items.append(Item("bom", ts, "Tooling list updated" if tooling else "Bill of materials updated",
                              "工装清单已更新" if tooling else "物料清单已更新", link("tooling" if tooling else "bom"),
                              frozenset(), key="bom:tooling" if tooling else "bom"))
        elif action == "update" and entity == "costs":
            items.append(Item("costs", ts, "Cost sheet updated", "成本表已更新", link("bom"), frozenset({COSTS}), key="costs"))
        elif entity == "part" and action in ("create", "update", "delete"):
            items.append(Item("parts", ts, "Parts changed", "零件已变更", link("bom"), frozenset(), key="parts"))
        elif action == "update" and entity == "procurement":
            items.append(Item("procurement", ts, "Ordering status updated", "订购状态已更新", link("timeline"), frozenset(), key="procurement"))
        elif entity == "schedule" and action in ("update", "freeze_baseline", "create"):
            label = ("Schedule baseline frozen", "进度基线已冻结") if action == "freeze_baseline" else ("Schedule updated", "进度已更新")
            items.append(Item("schedule", ts, label[0], label[1], link("timeline"), frozenset(), key=f"schedule:{action}"))
        elif action == "update" and entity in ("process", "assembly_process", "assembly"):
            items.append(Item("process", ts, "Assembly process updated", "装配工艺已更新", link("assembly"), frozenset(), key="process"))
        elif action == "update" and entity == "members":
            items.append(Item("members", ts, "The team changed", "团队成员已变更", link(), frozenset(), key="members"))
    return _merge(items)
