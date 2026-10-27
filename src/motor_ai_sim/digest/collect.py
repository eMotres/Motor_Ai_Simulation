"""The activity collector: what happened in ONE development project or ONE production between two instants.

Pure reading (nothing is written).  Every item carries

* ``en`` / ``zh``  one line in each language (the Chinese line is the existing Chinese field when there is one, else the
  English text marked as such; machine-translated text is marked as machine-translated in both languages);
* ``link``         the app path of the exact page (``return_path.project_path`` / ``production_path``) or None;
* ``link_policy``  whether the line may carry that link at all (:data:`LINK_POLICY`): ``new`` = something genuinely new to read or
  open, ``none`` = a notice / warning that never gets a link (owner 2026-10-28).  :attr:`Item.target` is the link the renderer may
  actually use; :func:`motor_ai_sim.digest.build.compose` puts ONE link at the end of each run of lines with the same target and
  never repeats a URL;
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
from motor_ai_sim.digest import readable as RD
from motor_ai_sim.digest import store as DS

#: Capability names (see module docstring).
INTERNAL = "internal"
COSTS = "costs"

EXCERPT = 90

#: Link policy per item kind (owner 2026-10-28): ``"new"`` = the line announces something new to open (results, a variant, a
#: specification version, a report, a message, a document ...), ``"none"`` = a notice or warning, no link.  A kind that is not
#: listed here keeps its link.  A single item may override its kind with ``Item.link_policy`` (the part lines: new / revised parts
#: are new, edited / removed parts are notices).
LINK_POLICY: dict = {
    # development projects
    "results": "new", "motor_added": "new", "motor_note": "new", "recommended": "new", "report_reviewed": "new",
    "spec_version": "new", "message": "new",
    "motor_removed": "none", "motor_status": "none", "member_joined": "none", "notes": "none", "project": "none",
    # productions
    "revision": "new", "document": "new", "proposal": "new", "parts": "new",
    "proposal_decision": "none", "bom": "none", "costs": "none", "procurement": "none", "schedule": "none",
    "process": "none", "members": "none",
}
DEFAULT_LINK_POLICY = "new"


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
    #: ``(en, zh)`` heading the line belongs under (e.g. the variant its duty results are about); lines with the same heading are
    #: shown together under it (:func:`motor_ai_sim.digest.build.compose`)
    group: Optional[tuple] = None
    #: ``"new"`` / ``"none"`` overriding :data:`LINK_POLICY` for this one line (None = the policy of its kind)
    link_policy: Optional[str] = None

    @property
    def policy(self) -> str:
        return self.link_policy or LINK_POLICY.get(self.kind, DEFAULT_LINK_POLICY)

    @property
    def target(self) -> Optional[str]:
        """The app path a rendered line may link to: ``link`` when the policy says ``new``, else None (a notice has no link)."""
        return self.link if self.policy == "new" and self.link else None


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


def _person(email: str) -> str:
    """``Name <email>`` of a member (the account name when there is one), else just the address."""
    email = str(email or "").strip()
    try:
        from motor_ai_sim import users
        name = str((users.get_user(email) or {}).get("name") or "").strip()
    except Exception:                                        # noqa: BLE001
        name = ""
    return f"{name} <{email}>" if name and "@" not in name and email else email


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
            it = Item(it.kind, it.ts, f"{it.en} ({n}x)", f"{it.zh}（{n}次）", it.link, it.requires, it.key, it.motor, it.group, it.link_policy)
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


def headline(kinds: dict) -> str:
    """The few numbers worth a line (the owner report's own lines): torque, power, efficiency, hottest winding / magnet, rotor SF.

    Plain notation (``1 770 N·m``, never ``1.77e+03``); the customer digest writes full sentences with
    :func:`motor_ai_sim.digest.readable.result_line` instead."""
    return RD.headline(kinds)


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
            who = _person(eid)                               # the stored member record carries only e-mail + role: the name is the account's
            items.append(Item("member_joined", ts, f"A new {role or 'member'} joined the project" + (f": {who}" if who else ""),
                              f"新成员加入项目（{role or '成员'}）" + (f"：{who}" if who else ""),
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
            en, zh = RD.result_line(duty, {k: r for k, (r, _) in fresh.items()})
            items.append(Item("results", ts, en, zh, motor_link(die, config), motor_req(die, config),
                              key=f"res:{die}/{config}/{duty}", motor=(die, config),
                              group=(f"New results, {die} / {config}", f"新计算结果，{die} / {config}")))
    return _merge(items)


# -- production: parts and documents in plain words ----------------------------------------------------------------------------------

def _plain_name(bi: Any, n: int = 40) -> tuple:
    """``(en, zh)`` of a part name: the canonical text of each language, one filled from the other when it is missing."""
    if isinstance(bi, dict):
        en, zh = excerpt(bi.get("en"), n), excerpt(bi.get("zh"), n)
    else:
        en = zh = excerpt(bi, n)
    return (en or zh, zh or en)


_FAMILY = {"material": "material", "material_i18n": "material", "spec": "spec", "spec_i18n": "spec"}


def _tokens(v: Any) -> set:
    if v is None:
        return set()
    if isinstance(v, dict) and ("en" in v or "zh" in v):
        return {str(x).strip() for x in (v.get("en"), v.get("zh")) if str(x or "").strip()}
    if isinstance(v, (str, int, float)):
        return {str(v).strip()} if str(v).strip() else set()
    return {json.dumps(v, sort_keys=True, ensure_ascii=False)}


def _same(old: Any, new: Any) -> bool:
    ta, tb = _tokens(old), _tokens(new)
    return ta == tb or bool(ta & tb and isinstance(old, str) != isinstance(new, str))


class _PartTracker:
    """Reads the part events of the audit trail in time order (also the ones before the period, so a revision bump can say what it
    was before) and says what changed in the period: new parts, revisions, edited fields, removed parts."""

    def __init__(self) -> None:
        self.known: dict = {}                    # part id -> {field family: last known value}
        self.new: dict = {}                      # part id -> ts
        self.removed: dict = {}
        self.revised: dict = {}                  # part id -> [old revision, new revision, ts, set(fields)]
        self.edited: dict = {}                   # part id -> [set(fields), ts]

    def event(self, ts: float, inside: bool, action: str, pid: str, snap: dict, pnames: dict) -> None:
        if action == "create":
            part = snap.get("part") if isinstance(snap.get("part"), dict) else {}
            if part.get("name") and pid not in pnames:
                pnames[pid] = _plain_name(part.get("name"))
            k = self.known[pid] = {}
            for f in ("name", "description", "qty_per_motor", "kind", "parent_id", "revision", "drawing_files", "optional_step"):
                if f in part:
                    k[f] = part[f]
            for f in ("material", "spec"):
                if f in part or f + "_i18n" in part:
                    k[f] = part.get(f + "_i18n") or part.get(f)
            if inside:
                self.new[pid] = ts
        elif action == "delete":
            if inside:
                if pid in self.new:
                    del self.new[pid]            # made and removed inside the period: nothing to tell
                else:
                    self.removed[pid] = ts
                self.revised.pop(pid, None)
                self.edited.pop(pid, None)
        else:
            patch = snap.get("patch") if isinstance(snap.get("patch"), dict) else {}
            k = self.known.setdefault(pid, {})
            changed: set = set()
            rev: Optional[list] = None
            for key, val in patch.items():
                fam = _FAMILY.get(key, key)
                if fam not in RD.PART_FIELD_WORDS and fam != "revision":
                    continue
                if key in ("material", "spec") and (key + "_i18n") in patch:
                    continue                      # the structured twin carries the same value
                if fam in k and _same(k[fam], val):
                    continue
                if fam == "revision":
                    rev = [str(k[fam]) if fam in k else None, str(val)]
                else:
                    changed.add(fam)
                k[fam] = val
            if not inside or pid in self.new:
                return
            if rev is not None:
                cur = self.revised.get(pid)
                self.revised[pid] = [cur[0] if cur else rev[0], rev[1], ts, (cur[3] if cur else set()) | changed]
                old_edit = self.edited.pop(pid, None)
                if old_edit:
                    self.revised[pid][3] |= old_edit[0]
            elif changed:
                if pid in self.revised:
                    self.revised[pid][3] |= changed
                    self.revised[pid][2] = max(self.revised[pid][2], ts)
                else:
                    cur = self.edited.get(pid)
                    self.edited[pid] = [(cur[0] if cur else set()) | changed, max(ts, cur[1]) if cur else ts]

    def items(self, pnames: dict, bom_link: Optional[str]) -> list:
        def label(pid: str) -> tuple:
            return pnames.get(pid) or ("a part", "某零件")

        def fields(fs: set) -> tuple:
            order = list(RD.PART_FIELD_WORDS)
            fs = sorted(fs, key=lambda f: order.index(f) if f in order else 99)
            return ", ".join(RD.PART_FIELD_WORDS[f][0] for f in fs), "、".join(RD.PART_FIELD_WORDS[f][1] for f in fs)

        def make(kind: str, head: tuple, rows: list, ts: float) -> Item:
            n = len(rows)
            names_en, names_zh = RD.name_list(rows)
            en = f"{head[0] if n == 1 else head[1] + f' ({n})'}: {names_en}"
            zh = f"{head[2]}{'' if n == 1 else f'（{n}）'}：{names_zh}"
            return Item("parts", ts, en, zh, bom_link, frozenset(), key=f"parts:{kind}",
                        link_policy="new" if kind in ("new", "revised") else "none")

        out: list = []
        if self.new:
            out.append(make("new", ("New part", "New parts", "新增零件"),
                            [label(p) for p in sorted(self.new, key=self.new.get)], max(self.new.values())))
        if self.revised:
            rows = []
            for pid, (old, new, _ts, fs) in sorted(self.revised.items(), key=lambda kv: kv[1][2]):
                en, zh = label(pid)
                f_en, f_zh = fields(fs)
                moved = bool(old and old != new)
                rows.append((f"{en} {f'{old} → {new}' if moved else f'revision {new}'}" + (f" ({f_en})" if f_en else ""),
                             f"{zh} {f'{old} → {new}' if moved else f'版本 {new}'}" + (f"（{f_zh}）" if f_zh else "")))
            out.append(make("revised", ("Part revised", "Parts revised", "零件已改版"), rows, max(v[2] for v in self.revised.values())))
        if self.edited:
            rows = []
            for pid, (fs, _ts) in sorted(self.edited.items(), key=lambda kv: kv[1][1]):
                en, zh = label(pid)
                f_en, f_zh = fields(fs)
                rows.append((f"{en} ({f_en})", f"{zh}（{f_zh}）"))
            out.append(make("edited", ("Part edited", "Parts edited", "零件已修改"), rows, max(v[1] for v in self.edited.values())))
        if self.removed:
            out.append(make("removed", ("Part removed", "Parts removed", "零件已删除"),
                            [label(p) for p in sorted(self.removed, key=self.removed.get)], max(self.removed.values())))
        return out


def _document_item(g: dict, part_id: str, internal: bool, pnames: dict, link: Callable) -> Item:
    """One line for the documents added to ONE part (or to no part): ``Stator assembly: 2 photos, 5 STEP files added``."""
    n = len(g["ids"])
    part = pnames.get(part_id) if part_id else None
    tag_en, tag_zh = (" (internal)", "（内部）") if internal else ("", "")
    ids = [i for i in g["ids"] if i]
    url = None
    for cap in dict.fromkeys((len(ids), 8, 4, 1)):          # the app path is length-limited: fewer ids, then the documents page
        url = link("documents", ids=ids[:cap]) if ids[:cap] else None
        if url:
            break
    url = url or link("documents")
    req = frozenset({INTERNAL}) if internal else frozenset()
    first = ids[0] if ids else ""
    if n == 1 and not part:
        name = g["names"][0]
        return Item("document", g["ts"], f"New document{tag_en}: {name}", f"新文档{tag_zh}：{name}", url, req, key=f"doc:{first}")
    p_en, p_zh = part or ("General documents", "通用文件")
    if n == 1:
        name = g["names"][0]
        return Item("document", g["ts"], f"{p_en}: {name} added{tag_en}", f"{p_zh}：新增 {name}{tag_zh}", url, req, key=f"doc:{first}")
    c_en, c_zh = RD.doc_counts(g["counts"])
    return Item("document", g["ts"], f"{p_en}: {c_en} added{tag_en}", f"{p_zh}：新增 {c_zh}{tag_zh}", url, req,
                key=f"docs:{part_id or '-'}:{int(internal)}")


# -- productions ------------------------------------------------------------------------------------------------------------------

def collect_production(production: dict, start: float, end: float) -> list[Item]:
    from motor_ai_sim.production import store as PR
    die, config = str(production.get("die") or ""), str(production.get("config") or "")

    def link(tab: Optional[str] = None, **kw: Any) -> Optional[str]:
        return RP.production_path(die, config, tab, **kw) or RP.production_path(die, config, tab) or RP.production_path(die, config)

    docs = {d.get("id"): d for d in (PR.load_file(die, config, "documents.json") or {}).get("documents", []) if isinstance(d, dict)}
    threads = {t.get("id"): t for t in (PR.load_file(die, config, "threads.json") or {}).get("threads", []) if isinstance(t, dict)}
    proposals = {p.get("id"): p for p in (PR.load_file(die, config, "proposals.json") or {}).get("proposals", []) if isinstance(p, dict)}
    parts_now = {p.get("id"): p for p in (PR.load_file(die, config, "parts.json") or {}).get("parts", []) if isinstance(p, dict)}
    pnames: dict = {pid: _plain_name(p.get("name")) for pid, p in parts_now.items()}
    tracker = _PartTracker()
    doc_groups: dict = {}
    items: list[Item] = []
    events = [(DS.parse_ts(ev.get("time")), ev) for ev in _read_jsonl(PR.audit_path(die, config))]
    for ts, ev in sorted((e for e in events if e[0] is not None), key=lambda e: e[0]):
        if ts >= end:
            continue
        action, entity, eid = str(ev.get("action") or ""), str(ev.get("entity") or ""), str(ev.get("entity_id") or "")
        snap = ev.get("snapshot") if isinstance(ev.get("snapshot"), dict) else {}
        if entity == "part" and action in ("create", "update", "delete"):
            tracker.event(ts, ts >= start, action, eid, snap, pnames)       # also before the period: what a revision was before
            continue
        if ts < start:
            continue
        if action == "publish_revision" and entity == "revision":
            rev = snap.get("revision") if isinstance(snap.get("revision"), dict) else {}
            s_en, s_zh = bilingual(rev.get("summary"))
            n = len([c for c in rev.get("changes") or [] if c])
            tail_en, tail_zh = (f" ({n} changes)", f"（{n} 项变更）") if n else ("", "")
            items.append(Item("revision", ts, f"Revision {eid} published: {s_en}{tail_en}", f"版本 {eid} 已发布：{s_zh}{tail_zh}",
                              link("changes", item=eid), frozenset(), key=f"rev:{eid}"))
        elif action == "create" and entity == "document":
            d = docs.get(eid) or (snap.get("doc") if isinstance(snap.get("doc"), dict) else {})
            g = doc_groups.setdefault((str(d.get("part_id") or ""), bool(d.get("internal"))),
                                      {"ids": [], "names": [], "counts": {}, "ts": ts})
            g["ids"].append(eid)
            g["names"].append(excerpt(d.get("name") or "file", 70))
            cat = RD.doc_category(d.get("name"))
            g["counts"][cat] = g["counts"].get(cat, 0) + 1
            g["ts"] = max(g["ts"], ts)
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
        elif action == "update" and entity == "procurement":
            items.append(Item("procurement", ts, "Ordering status updated", "订购状态已更新", link("timeline"), frozenset(), key="procurement"))
        elif entity == "schedule" and action in ("update", "freeze_baseline", "create"):
            label = ("Schedule baseline frozen", "进度基线已冻结") if action == "freeze_baseline" else ("Schedule updated", "进度已更新")
            items.append(Item("schedule", ts, label[0], label[1], link("timeline"), frozenset(), key=f"schedule:{action}"))
        elif action == "update" and entity in ("process", "assembly_process", "assembly"):
            items.append(Item("process", ts, "Assembly process updated", "装配工艺已更新", link("assembly"), frozenset(), key="process"))
        elif action == "update" and entity == "members":
            items.append(Item("members", ts, "The team changed", "团队成员已变更", link(), frozenset(), key="members"))
    items += tracker.items(pnames, link("bom"))
    for (part_id, internal), g in doc_groups.items():
        items.append(_document_item(g, part_id, internal, pnames, link))
    return _merge(items)
