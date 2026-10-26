"""Motor grants follow the project (owner 2026-10-27: no manual linking and unlinking of motors).

A development project that links a motor gives its members (customer / engineer / owner) a per-configuration grant
(``motors._sync_grants``).  Until now the grant stayed when the motor left the project and an admin cleared the
``grant_removal_candidates`` by hand.  Now it leaves with the motor.

Who made a grant (attribution)
    * ``users.json`` ``motors.sources["<die>/<config>"] = ["project:<id>", ...]`` is written by ``_sync_grants`` when the
      project ADDS the grant itself (the account did not hold it) and, when another project's origin is already there,
      also for the second project.  A grant an admin gave carries no origin; an admin un-ticking a grant forgets its
      origin, so a later re-issue by hand is independent.
    * The project's own ledger ``project_motor_grants[<motor>] = {grant_key, emails, sourced}`` lists the accounts the
      project made a grant for; ``sourced`` those whose origin was recorded.  A ledger e-mail outside ``sourced`` is a
      grant stored before origins were recorded: it counts as project-made (``legacy``) when the project has a link
      record for exactly that motor and the account is a project member (or the reason is a membership change / a
      failed link, which the ledger itself proves).  A ``sourced`` e-mail whose account lost the origin was reissued by
      an admin: kept.

A grant is removed only when the project is its only origin, no other project (or the same one) still has the motor
linked with the account as a granting member, and the account is not staff.  Every removal is audited as
``motor.grant_autoremove`` in the project's audit log.  A grant that fails the rule stays listed in
``grant_removal_candidates`` with its reason.
"""
from __future__ import annotations

import copy
import logging
from typing import Iterable

from motor_ai_sim import users
from motor_ai_sim.development import store

log = logging.getLogger(__name__)

AUDIT_ACTION = "motor.grant_autoremove"
#: reasons for which the ledger itself is the proof that the project added the grant
_LEDGER_PROOF_REASONS = ("link_failed", "membership_update_failed")


def source_of(project_id: str) -> str:
    return f"project:{project_id}"


def _motors():
    from motor_ai_sim.development import motors      # late: motors imports this module from its routes
    return motors


def _identity(grant_key: str) -> tuple[str, str]:
    die, _, config = str(grant_key or "").partition("/")
    return die.casefold(), config.casefold()


def _link_identities(project: dict) -> set[tuple[str, str]]:
    found = set()
    for row in list(project.get("linked_motors") or []) + list(project.get("motor_history") or []):
        if isinstance(row, dict) and row.get("die") and row.get("config"):
            found.add((str(row["die"]).casefold(), str(row["config"]).casefold()))
    return found


def _linked_identities(project: dict) -> set[tuple[str, str]]:
    return {(str(row["die"]).casefold(), str(row["config"]).casefold())
            for row in project.get("linked_motors") or []
            if isinstance(row, dict) and row.get("die") and row.get("config")}


def _granting_members(project: dict) -> set[str]:
    roles = _motors().MOTOR_GRANT_ROLES
    return {str(m.get("email") or "").strip().lower() for m in project.get("members") or []
            if isinstance(m, dict) and m.get("role") in roles and str(m.get("email") or "").strip()}


def _warranted_by_other_projects(project: dict, grant_key: str) -> set[str]:
    """Accounts that a project OTHER than ``project`` still gives this configuration to (motor linked + granting member)."""
    identity = _identity(grant_key)
    holders: set[str] = set()
    for other in store.list_projects():
        if other.get("id") == project.get("id"):
            continue
        if identity in _linked_identities(other):
            holders |= _granting_members(other)
    return holders


def _drop_candidate_rows(project: dict, grant_key: str, emails: Iterable[str]) -> None:
    rows = project.get("grant_removal_candidates")
    if not isinstance(rows, list):
        return
    identity = _identity(grant_key)
    drop = {str(e).lower() for e in emails}
    project["grant_removal_candidates"] = [
        row for row in rows
        if not (isinstance(row, dict) and str(row.get("email", "")).lower() in drop
                and (str(row.get("die", "")).casefold(), str(row.get("config", "")).casefold()) == identity)]


def release_motor_grants(project: dict, motor_key: str, *, reason: str,
                         emails: set[str] | None = None, actor: str = "") -> list[dict]:
    """Take back the grants ``project`` made for one motor that is no longer (or no longer for them) the project's.

    Works on the in-memory ``project`` document (the caller holds ``motors.transaction()`` and ``users._LOCK`` and
    saves the document afterwards); the account registry is written here.  Returns the removed rows
    ``{"email", "die", "config", "reason"}``.  What cannot be removed under the rule stays as a
    ``grant_removal_candidates`` row with ``reason``.  Never raises for one account: a registry fault leaves the row."""
    entry = (project.get("project_motor_grants") or {}).get(motor_key)
    if not isinstance(entry, dict):
        return []
    ledger = {str(e).strip().lower() for e in entry.get("emails", []) if str(e).strip()}
    wanted = None if emails is None else {str(e).strip().lower() for e in emails}
    selected = ledger if wanted is None else ledger & wanted
    grant_key = str(entry.get("grant_key") or "")
    if not grant_key or not selected:
        return []
    die, _, config = grant_key.partition("/")
    sourced = {str(e).strip().lower() for e in entry.get("sourced", [])}
    source = source_of(str(project.get("id") or ""))
    members_now = _granting_members(project)
    still = members_now if _identity(grant_key) in _linked_identities(project) else set()
    still = still | _warranted_by_other_projects(project, grant_key)
    has_link_record = _identity(grant_key) in _link_identities(project)
    membership_change = reason.startswith("customer_membership") or reason.startswith("membership")
    settled: set[str] = set()
    kept: set[str] = set()
    removed: list[dict] = []
    for email in sorted(selected):
        if email in still:                           # a project still gives this account the motor: the grant stays
            try:
                users.release_config_grant(email, grant_key, source, keep=True)
            except Exception:                                # noqa: BLE001
                log.warning("project %s: origin of %s on %s not dropped", project.get("id"), grant_key, email,
                            exc_info=True)
            settled.add(email)
            continue
        # an e-mail in ``sourced`` had its origin recorded: without it on the account an admin reissued the grant
        legacy = email not in sourced and (reason in _LEDGER_PROOF_REASONS or membership_change
                                           or (has_link_record and email in members_now))
        try:
            outcome = users.release_config_grant(email, grant_key, source, legacy=legacy)
        except Exception:                                    # noqa: BLE001 - never block an unlink on the registry
            log.warning("project %s: grant %s of %s could not be released", project.get("id"), grant_key, email,
                        exc_info=True)
            kept.add(email)
            continue
        if outcome == users.GRANT_KEPT_INDEPENDENT:
            kept.add(email)
            continue
        settled.add(email)
        if outcome == users.GRANT_REMOVED:
            row = {"email": email, "die": die, "config": config, "reason": reason}
            removed.append(row)
            try:
                store.append_audit(str(project.get("id")), actor or "system", AUDIT_ACTION, "motor", grant_key,
                                   {"email": email, "reason": reason})
            except Exception:                                # noqa: BLE001 - the audit line must not undo a removal
                log.warning("project %s: audit of the removed grant %s of %s failed", project.get("id"), grant_key,
                            email, exc_info=True)
    if settled:
        entry["emails"] = sorted(ledger - settled)
        if "sourced" in entry:
            entry["sourced"] = sorted(sourced - settled)
            if not entry["sourced"]:
                entry.pop("sourced")
        _drop_candidate_rows(project, grant_key, settled)
    if kept:
        _motors()._mark_removal_candidates(project, motor_key, reason=reason, emails=kept)
    return removed


def drain_project_candidates(project: dict, *, actor: str = "") -> list[dict]:
    """Apply the removal rule to the candidates already stored on ``project`` (in memory).  Idempotent: a row that
    cannot be removed stays untouched with its reason, a settled row disappears.  Returns the removed grants."""
    rows = [r for r in project.get("grant_removal_candidates") or [] if isinstance(r, dict)]
    if not rows:
        return []
    motors = _motors()
    removed: list[dict] = []
    groups: dict[tuple[str, str], set[str]] = {}
    for row in rows:
        die, config = str(row.get("die") or ""), str(row.get("config") or "")
        email = str(row.get("email") or "").strip().lower()
        if die and config and email:
            groups.setdefault((motors._key(die, config), str(row.get("reason") or "")), set()).add(email)
    for (motor_key, reason), emails in sorted(groups.items()):
        if not isinstance((project.get("project_motor_grants") or {}).get(motor_key), dict):
            continue                                         # no proof the project made it: stays listed
        removed += release_motor_grants(project, motor_key, reason=reason, emails=emails, actor=actor)
    return removed


def _save(project: dict) -> None:
    store.mutate_project(str(project["id"]), lambda doc: doc.update(project))


def drain_all(*, actor: str = "system") -> dict:
    """One-shot, idempotent: apply the rule to every project's stored candidates.  Safe to run at every start and on
    every admin read - a project without candidates is not even rewritten."""
    summary = {"projects": 0, "removed": 0, "left": 0}
    try:
        pending = [p["id"] for p in store.list_projects() if p.get("grant_removal_candidates")]
    except Exception:                                        # noqa: BLE001 - an unreadable store drains nothing
        return summary
    if not pending:
        return summary
    motors = _motors()
    for project_id in pending:
        try:
            with motors.transaction(), users._LOCK:
                project = store.get_project(project_id)
                if not project or not project.get("grant_removal_candidates"):
                    continue
                before = copy.deepcopy(project)
                removed = drain_project_candidates(project, actor=actor)
                if project != before:
                    _save(project)
                summary["projects"] += 1
                summary["removed"] += len(removed)
                summary["left"] += len(project.get("grant_removal_candidates") or [])
        except Exception:                                    # noqa: BLE001 - one project never stops the others
            log.warning("grant cleanup of project %s failed", project_id, exc_info=True)
    if summary["removed"]:
        log.info("project grants: %d stale grant(s) removed automatically, %d left for review",
                 summary["removed"], summary["left"])
    return summary


def release_for_catalog_removal(die: str, configs: Iterable[str], *, reason: str, actor: str = "") -> list[dict]:
    """A configuration (or a whole die) left the catalog: take back the grants every project made for it.  The project
    keeps the link (its history and snapshots stay).  Names are matched like ``autolink`` does (a published label
    counts as its base die)."""
    from motor_ai_sim.development.autolink import _names
    wanted_dies = _names(die)
    wanted_configs = {str(c).casefold() for c in configs}
    if not wanted_configs:
        return []
    motors = _motors()
    removed: list[dict] = []
    try:
        candidates = [p["id"] for p in store.list_projects()
                      if any(isinstance(m, dict) and str(m.get("config", "")).casefold() in wanted_configs
                             and _names(str(m.get("die", ""))) & wanted_dies
                             for m in list(p.get("linked_motors") or []))]
    except Exception:                                        # noqa: BLE001
        return []
    for project_id in candidates:
        with motors.transaction(), users._LOCK:
            project = store.get_project(project_id)
            if not project:
                continue
            before = copy.deepcopy(project)
            for m in list(project.get("linked_motors") or []):
                if (isinstance(m, dict) and str(m.get("config", "")).casefold() in wanted_configs
                        and _names(str(m.get("die", ""))) & wanted_dies):
                    key = motors._key(m["die"], m["config"])
                    # nobody is warranted by a motor that is gone: judge as unlinked
                    shadow = copy.deepcopy(project)
                    shadow["linked_motors"] = [x for x in shadow.get("linked_motors", [])
                                               if not (isinstance(x, dict) and motors._key(x.get("die", ""), x.get("config", "")) == key)]
                    got = release_motor_grants(shadow, key, reason=reason, actor=actor)
                    project["project_motor_grants"] = shadow.get("project_motor_grants", {})
                    if "grant_removal_candidates" in shadow:
                        project["grant_removal_candidates"] = shadow["grant_removal_candidates"]
                    removed += got
            if project != before:
                _save(project)
    return removed


def regrant_for_restored_die(die: str, configs: Iterable[str]) -> int:
    """A retired die came back: give its linked motors to the project members again (the retire took the grants away).
    Returns how many project/motor pairs were synced."""
    from motor_ai_sim.development.autolink import _names
    wanted_dies = _names(die)
    wanted_configs = {str(c).casefold() for c in configs}
    motors = _motors()
    synced = 0
    try:
        candidates = [p["id"] for p in store.list_projects()
                      if any(isinstance(m, dict) and str(m.get("config", "")).casefold() in wanted_configs
                             and _names(str(m.get("die", ""))) & wanted_dies
                             for m in list(p.get("linked_motors") or []))]
    except Exception:                                        # noqa: BLE001
        return 0
    for project_id in candidates:
        try:
            with motors.transaction(), users._LOCK:
                project = store.get_project(project_id)
                if not project:
                    continue
                for m in list(project.get("linked_motors") or []):
                    if (isinstance(m, dict) and str(m.get("config", "")).casefold() in wanted_configs
                            and _names(str(m.get("die", ""))) & wanted_dies):
                        motors._sync_grants(project, motors._key(m["die"], m["config"]), f"{m['die']}/{m['config']}")
                        synced += 1
                _save(project)
        except Exception:                                    # noqa: BLE001
            log.warning("restoring the grants of project %s failed", project_id, exc_info=True)
    return synced
