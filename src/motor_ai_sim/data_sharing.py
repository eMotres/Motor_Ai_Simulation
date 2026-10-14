"""Optional consent to share a customer's own data with the AeroStator engineering dataset, and the filter that enforces it.

The consent is SEPARATE from the NDA: unticked by default, never a condition of signing, changeable at any time by the account
itself (NDA dialog, account menu).  Text version: ``data_sharing_consent_v1`` (web/src/locales/*/common.json ``dataSharing`` and the
draft ``Data_Sharing_Consent_v1_DRAFT.md`` carry the same wording).

Where things live (``<shared>/legal`` = :func:`motor_ai_sim.nda.legal_root`):

    users.json  ``data_sharing: {consent, version, time}``     the CURRENT state of the account (users.set_data_sharing)
    <legal>/data_sharing_audit.jsonl                           append-only, one line per decision or change (this module)
    <legal>/nda_signatures.jsonl                               the NDA record also carries ``data_sharing`` as it was ticked

Enforcement (:func:`filter_for_dataset`, the single gate in front of any dataset export of workspace data):

* a record of a customer (it has an ``owner`` account, or a customer / nda / confidential / restricted marker) is exported ONLY when
  the owner's CURRENT consent is true AND the consent was true when the record was created (the audit history decides; a record
  without a readable creation time cannot be proven and stays out);
* Restricted Material that MOTRES supplied (``restricted`` / ``restricted_material`` true, tag ``restricted`` / ``nda`` /
  ``confidential``, ``origin: motres_restricted``) is never exported, consent or not;
* the ``customer`` marker alone is lifted by a valid consent; without consent it keeps blocking, as in
  :mod:`motor_ai_sim.data_sources` (``CONFIDENTIAL_TAGS``);
* every exported record is anonymised: personal and company identifiers are dropped, e-mail addresses and the owner's own name /
  company / e-mail are scrubbed from every string.

A withdrawal stops future inclusion; a dataset release already published cannot be recalled (said so in the consent text).
"""
from __future__ import annotations

import datetime
import json
import logging
import re
import secrets
import threading
from pathlib import Path
from typing import Any, Iterable, Optional

log = logging.getLogger(__name__)

CONSENT_VERSION = "data_sharing_consent_v1"
_LOCK = threading.RLock()

#: keys removed from every exported record, at any depth (personal data, company names, network and account identifiers)
IDENTIFIER_KEYS = frozenset("""owner owner_org owner_id user user_id uid account email e_mail mail display_name full_name
first_name last_name signatory company company_name organisation organization org customer customer_name reg_no address
street city postcode zip phone tel ip ip_address user_agent workspace workspace_id ws_id record_id session sid token author
created_by updated_by""".split())

#: markers that mean Restricted Material or NDA-covered data; consent never lifts them
HARD_TAGS = frozenset({"restricted", "nda", "confidential"})
REMOVED = "[removed]"
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")


def _norm(email) -> str:
    return str(email or "").strip().lower()


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _iso(dt: datetime.datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# -- audit log ------------------------------------------------------------------

def audit_path() -> Path:
    from motor_ai_sim.nda import legal_root
    return legal_root() / "data_sharing_audit.jsonl"


def _audit_append(row: dict) -> None:
    """Append one line; creates the legal directory (consent is recorded even before the NDA text is installed).  Raises OSError."""
    from motor_ai_sim.private_files import ensure_private_dir, open_private
    p = audit_path()
    ensure_private_dir(p.parent)
    with open_private(p, "a") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def read_audit(email: Optional[str] = None) -> list[dict]:
    """The audit lines (oldest first), optionally only those of one account."""
    rows: list[dict] = []
    try:
        with open(audit_path(), encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict) and (email is None or _norm(r.get("user")) == _norm(email)):
                    rows.append(r)
    except OSError:
        return []
    rows.sort(key=lambda r: float(r.get("ts") or 0))
    return rows


def _events(email: str) -> list[tuple[float, bool]]:
    return [(float(r.get("ts") or 0), r.get("consent") is True) for r in read_audit(email)]


# -- state ------------------------------------------------------------------------

def state(email: str) -> dict:
    """``{consent, version, time}`` of the account now (default: no consent)."""
    from motor_ai_sim import users as U
    return U.get_data_sharing(email)


def set_consent(email: str, consent: bool, *, source: str, ip: str = "", record_id: str = "", log_unchanged: bool = False) -> dict:
    """Set the consent of an account and append the audit line.  -> the new ``{consent, version, time}``.

    The audit line is written FIRST: if it cannot be written (OSError) nothing changes.  Unchanged value -> no new line, unless
    ``log_unchanged`` (the NDA dialog records the decision it was given even when it equals the default).  KeyError for an
    unknown account."""
    from motor_ai_sim import users as U
    email = _norm(email)
    consent = consent is True
    with _LOCK:
        if U.get_user(email) is None:
            raise KeyError(email)
        old = U.get_data_sharing(email)
        if old["consent"] == consent and old["version"] == CONSENT_VERSION and not log_unchanged:
            return old
        now = _now()
        _audit_append(dict(id="d%s-%s" % (now.strftime("%Y%m%d"), secrets.token_hex(6)), ts=round(now.timestamp(), 3), iso=_iso(now),
                           user=email, consent=consent, previous=old["consent"], version=CONSENT_VERSION, source=source,
                           record_id=record_id, ip=ip or ""))
        return U.set_data_sharing(email, consent, CONSENT_VERSION, _iso(now))


def consent_at(email: str, ts: float) -> bool:
    """Was the consent true at the instant ``ts`` (epoch seconds)?  The last audit line at or before ``ts`` decides; no line = False."""
    return _state_at(_events(email), ts)


def _state_at(ev: list[tuple[float, bool]], ts: float) -> bool:
    last = False
    for t, c in ev:
        if t <= ts:
            last = c
        else:
            break
    return last


def consenting_users() -> list[str]:
    """Accounts whose current consent is true (user record AND the last audit line agree)."""
    from motor_ai_sim import users as U
    out = []
    for row in U.list_users():
        if row.get("data_sharing", {}).get("consent") is True and _currently_true(row["email"]):
            out.append(row["email"])
    return out


def _currently_true(email: str) -> bool:
    ev = _events(email)
    return bool(ev) and ev[-1][1]


# -- the export filter ------------------------------------------------------------------

def _created_ts(rec: dict) -> Optional[float]:
    """Creation time of a record in epoch seconds from ``created`` / ``created_at`` / ``ts`` / ``time`` (number or ISO string)."""
    for k in ("created", "created_at", "ts", "time"):
        v = rec.get(k)
        if v is None or v == "" or isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            return float(v)
        try:
            d = datetime.datetime.fromisoformat(str(v).strip().replace("Z", "+00:00"))
        except ValueError:
            continue
        if d.tzinfo is None:
            d = d.replace(tzinfo=datetime.timezone.utc)
        return d.timestamp()
    return None


def _tags(rec: dict) -> set:
    tags = rec.get("tags") or []
    return {str(t).strip().lower() for t in tags} if isinstance(tags, (list, tuple, set)) else set()


def _is_restricted(rec: dict) -> bool:
    return (rec.get("restricted") is True or rec.get("restricted_material") is True
            or str(rec.get("origin") or "").strip().lower() == "motres_restricted"
            or bool(HARD_TAGS & _tags(rec))
            or str(rec.get("confidential") or "").strip().lower() not in ("", "0", "false", "no", "none", "customer")
            or rec.get("nda") is True)


def _is_customer(rec: dict) -> bool:
    return (bool(_norm(rec.get("owner"))) or bool(str(rec.get("customer") or "").strip())
            or "customer" in _tags(rec) or str(rec.get("confidential") or "").strip().lower() == "customer")


def decide(rec: dict, *, _events_cache: Optional[dict] = None) -> tuple[bool, str]:
    """May this record enter the dataset?  -> (allowed, reason).  Fail-closed: anything unproven is refused."""
    if not isinstance(rec, dict):
        return False, "not_a_record"
    if _is_restricted(rec):
        return False, "restricted_material"
    if not _is_customer(rec):
        return True, "motres_data"                          # no customer marker: outside this gate (data_sources still applies)
    owner = _norm(rec.get("owner"))
    if not owner:
        return False, "customer_without_owner"
    if state(owner)["consent"] is not True:
        return False, "no_consent"
    ts = _created_ts(rec)
    if ts is None:
        return False, "creation_time_unknown"
    ev = (_events_cache or {}).get(owner)
    if ev is None:
        ev = _events(owner)
        if _events_cache is not None:
            _events_cache[owner] = ev
    if not ev or not ev[-1][1]:
        return False, "no_consent"                          # user record says yes but the audit trail does not: refuse
    return (True, "consent") if _state_at(ev, ts) else (False, "created_before_consent")


def _scrub_terms(owner: str) -> list[str]:
    from motor_ai_sim import users as U
    terms = []
    u = U.get_user(owner) or {}
    nda = U.normalize_nda(u.get("nda"))
    for v in (owner, u.get("name"), nda.get("company")):
        v = str(v or "").strip()
        if len(v) >= 3:
            terms.append(v)
    return sorted(set(terms), key=len, reverse=True)


def _scrub_str(s: str, terms: list[str]) -> str:
    s = _EMAIL_RE.sub(REMOVED, s)
    for t in terms:
        s = re.sub(re.escape(t), REMOVED, s, flags=re.I)
    return s


def anonymise(value: Any, terms: Optional[list[str]] = None) -> Any:
    """A copy without identifier keys (any depth) and with e-mails and the owner's name / company / e-mail scrubbed from strings."""
    terms = terms or []
    if isinstance(value, dict):
        return {k: anonymise(v, terms) for k, v in value.items() if str(k).strip().lower() not in IDENTIFIER_KEYS}
    if isinstance(value, (list, tuple)):
        return [anonymise(v, terms) for v in value]
    if isinstance(value, str):
        return _scrub_str(value, terms)
    return value


def filter_for_dataset(rows: Iterable[dict]) -> tuple[list[dict], dict]:
    """The gate for a dataset export.  -> (anonymised included records, ``{reason: count}`` of everything decided).

    Every record is anonymised, MOTRES-owned ones as well (identifier keys never leave)."""
    included: list[dict] = []
    counts: dict[str, int] = {}
    cache: dict = {}
    for rec in rows:
        ok, why = decide(rec, _events_cache=cache)
        counts[why] = counts.get(why, 0) + 1
        if ok:
            owner = _norm(rec.get("owner")) if isinstance(rec, dict) else ""
            included.append(anonymise(rec, _scrub_terms(owner) if owner else []))
    return included, counts


def export_jsonl(src: Path, dst: Path) -> dict:
    """Filter a JSON-lines file of workspace records into ``dst``.  -> ``{read, written, reasons}``.  Unreadable lines are skipped
    (counted as ``unreadable``), never copied."""
    rows, bad = [], 0
    with open(src, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                bad += 1
    inc, counts = filter_for_dataset(rows)
    if bad:
        counts["unreadable"] = bad
    Path(dst).write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in inc), encoding="utf-8")
    return dict(read=len(rows) + bad, written=len(inc), reasons=counts)
