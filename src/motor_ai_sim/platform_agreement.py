"""AeroStator Platform Services and Data Agreement: a click-through a COMPANY accepts before it starts a development.

Owner 2026-10-27: B2B only; two modes - FREE (closed projects, MOTRES may train its internal AI on the client's engineering data)
and PRIVATE (paid; no AI training on the client's data).  The text is a DRAFT under legal review, so the feature ships DISABLED
for users, exactly like the online NDA (``nda.py``): it is enforced only when an approved version is installed.

Where things live (``<shared>/legal`` = :func:`motor_ai_sim.nda.legal_root`, ``MOTRES_LEGAL_ROOT`` overrides, tests):

    <shared>/legal/platform_agreement/current.txt                           the current version name
    <shared>/legal/platform_agreement/<version>/platform_agreement_<lang>.md   EN and zh-CN text, placeholders {{company}} {{reg_no}}
                                                                            {{country}} {{address}} {{signatory}} {{title}} {{email}}
                                                                            {{date}} {{version}} {{mode}}
    <shared>/legal/platform_agreement_acceptances.jsonl                     append-only acceptance records (never rewritten)
    <shared>/legal/platform_agreement_signed/<record_id>.pdf                the accepted text + record, next to it

The repo ships the draft (``config/legal/platform_agreement/v0-draft``).  A version whose name starts with ``v0-`` or whose text
says "NOT FOR SIGNATURE" is a placeholder: while it is the current one NOTHING is enforced and users are told nothing (admins see
"agreement text not installed").  ``PLATFORM_AGREEMENT_DISABLED=1`` switches an installed version off again (emergency).

Acceptance is per COMPANY (the directory company the account is confirmed in; one acceptance covers all its users) and falls back
to the account when the account has no company.  A newer version needs a new acceptance; the old one stays valid for its own
version (and keeps deciding the mode until the new one is given).

The gate: :func:`require_accepted` (HTTP 428 ``agreement_required``) is called by the actions that START a development; staff
(admin / engineer accounts) and MOTRES's own company are never gated.  :func:`training_allowed` is the helper the dataset export
calls: FREE -> True, PRIVATE -> False, no acceptance -> False (fail-closed), MOTRES's own data -> True.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import re
import secrets
import threading
from pathlib import Path
from typing import Any, Optional

from motor_ai_sim import nda as N

log = logging.getLogger(__name__)

NAME = "platform_agreement"
LANGS = N.LANGS
MODES = ("FREE", "PRIVATE")
FIELDS = ("company", "reg_no", "country", "address")           # the client's data in the text
PLACEHOLDER_VERSION_PREFIX = "v0-"
PLACEHOLDER_MARKS = ("NOT FOR SIGNATURE", "非签署版本")
STAFF_ROLES = frozenset({"admin", "engineer"})
#: MOTRES d.o.o. (the other party): its own accounts / company never need to accept.
OWN_REG_NO = "7255691000"
OWN_DOMAINS = frozenset({"motresres.com", "emotres.com", "aerostator.com"})
CODE = "agreement_required"
_LOCK = threading.RLock()
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_ID_RE = re.compile(r"^a[0-9]{8}-[0-9a-f]{12}$")
_CACHE: dict[str, Any] = {"sig": None, "rows": []}


# -- the text -----------------------------------------------------------------

def disabled() -> bool:
    return (os.environ.get("PLATFORM_AGREEMENT_DISABLED") or "").strip().lower() in ("1", "true", "yes")


def _is_placeholder(version: str, texts: dict) -> bool:
    return version.startswith(PLACEHOLDER_VERSION_PREFIX) or any(m in t for t in texts.values() for m in PLACEHOLDER_MARKS)


def current_version() -> Optional[tuple[str, Path]]:
    """(version, directory ``platform_agreement/``) of the installed current version, else the shipped draft, else None."""
    for root in (N.legal_root(), N.repo_legal_root()):
        v = (N._read(root / NAME / "current.txt") or "").strip()
        if v and _VERSION_RE.match(v) and (root / NAME / v).is_dir():
            return v, root / NAME
    base = N.repo_legal_root() / NAME
    drafts = sorted(p.name for p in base.iterdir() if p.is_dir() and p.name.startswith(PLACEHOLDER_VERSION_PREFIX)) if base.is_dir() else []
    return (drafts[-1], base) if drafts else None


def current(lang: str = "en") -> Optional[dict]:
    """Current EN and zh-CN texts (None when either is missing: both are presented and accepted together)."""
    lang = lang if lang in LANGS else "en"
    cv = current_version()
    if cv is None:
        return None
    version, base = cv
    texts = {code: N._read(base / version / ("%s_%s.md" % (NAME, code))) for code in LANGS}
    if any(t is None for t in texts.values()):
        return None
    hashes = {code: N.sha256_text(texts[code]) for code in LANGS}
    return dict(version=version, lang=lang, text=texts[lang], sha256=hashes[lang], texts=texts, sha256s=hashes,
                placeholder=_is_placeholder(version, texts),
                installed=not _is_placeholder(version, texts) and N.legal_root().is_dir(), modes=list(MODES),
                countries=list(N.COUNTRIES))


def enforced() -> bool:
    """True only when an APPROVED version is installed (and the switch is not off)."""
    if disabled():
        return False
    cur = current("en")
    return bool(cur and cur["installed"])


# -- who is asking ------------------------------------------------------------

def _norm_email(e: Any) -> str:
    return str(e or "").strip().lower()


def is_staff(role: Any) -> bool:
    return str(role or "").strip().lower() in STAFF_ROLES


def _own_domains() -> set[str]:
    extra = {d.strip().lower() for d in (os.environ.get("MOTRES_OWN_DOMAINS") or "").split(",") if d.strip()}
    return set(OWN_DOMAINS) | extra


def _is_own_company(company: Optional[dict]) -> bool:
    if not company:
        return False
    ids = {c.strip() for c in (os.environ.get("MOTRES_OWN_COMPANY_IDS") or "").split(",") if c.strip()}
    name = N._norm(company.get("name")).lower()
    return (company.get("id") in ids or N._norm(company.get("reg_no")).replace(" ", "") == OWN_REG_NO
            or name.startswith("motres d.o.o"))


def company_of(email: str) -> Optional[dict]:
    """The directory company the account is CONFIRMED in -> {id, name, reg_no, country, address}; None when it has none (or the
    directory cannot be read).  A proposed affiliation does not count: nobody has confirmed that person works there."""
    email = _norm_email(email)
    if not email:
        return None
    try:
        from motor_ai_sim.directory import service as D, store as DS
        person = D.find_person_by_email(email)
        if not person:
            return None
        affs = [a for a in DS.load_affiliations() if a.get("person_id") == person.get("id") and a.get("status") == "confirmed"]
        if not affs:
            return None
        comps = {c.get("id"): c for c in DS.load_companies()}
        for a in sorted(affs, key=lambda a: str(a.get("updated") or ""), reverse=True):
            c = comps.get(a.get("company_id"))
            if c:
                return dict(id=c["id"], name=str(c.get("legal_name") or ""), reg_no=str(c.get("reg_no") or ""),
                            country=str(c.get("country") or "").upper(), address=str(c.get("address") or ""))
    except Exception:                                                  # noqa: BLE001 - a directory problem never blocks a caller
        log.exception("platform_agreement: company lookup of %s failed", email)
    return None


def _nda_facts(email: str) -> dict:
    """Company facts from the account's own signed NDA record (fills what the directory does not know)."""
    try:
        from motor_ai_sim import users as U
        rid = U.get_nda(email).get("record_id") or ""
        rec = N.get_record(rid) if rid else None
        if rec:
            return {k: N._norm(rec.get(k)) for k in FIELDS} | {"country": N._norm(rec.get("country")).upper()}
    except Exception:                                                  # noqa: BLE001
        pass
    return {}


def scope_of(email: str, role: Any = "") -> dict:
    """-> {key, scope: company|account, company: dict|None, exempt: None|'staff'|'motres'}.  ``key`` is what an acceptance is filed under."""
    email = _norm_email(email)
    company = company_of(email)
    exempt = None
    if is_staff(role):
        exempt = "staff"
    elif _is_own_company(company) or email.rsplit("@", 1)[-1] in _own_domains():
        exempt = "motres"
    return dict(key=("company:%s" % company["id"]) if company else ("user:%s" % email), scope="company" if company else "account",
                company=company, account_key="user:%s" % email, exempt=exempt)


def company_facts(email: str, company: Optional[dict] = None) -> dict:
    """What we already know of the client's data for the text: directory first, then the signed NDA; blank = the dialog asks."""
    company = company if company is not None else company_of(email)
    facts = {k: str((company or {}).get({"company": "name"}.get(k, k)) or "") for k in FIELDS}
    if not all(facts.values()):
        nda = _nda_facts(email)
        facts = {k: facts[k] or nda.get(k, "") for k in FIELDS}
    return facts


# -- records ------------------------------------------------------------------

def acceptances_path() -> Path:
    return N.legal_root() / "platform_agreement_acceptances.jsonl"


def pdf_path(record_id: str) -> Optional[Path]:
    if not _ID_RE.match(record_id or ""):
        return None
    p = N.legal_root() / "platform_agreement_signed" / (record_id + ".pdf")
    return p if p.is_file() else None


def _rows() -> list[dict]:
    """All records, oldest first; re-read only when the file changed."""
    p = acceptances_path()
    try:
        st = p.stat()
        sig = (str(p), st.st_mtime_ns, st.st_size)
    except OSError:
        _CACHE.update(sig=None, rows=[])
        return []
    if _CACHE["sig"] == sig:
        return _CACHE["rows"]
    rows = []
    with open(p, encoding="utf-8") as fh:
        for line in fh:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    _CACHE.update(sig=sig, rows=rows)
    return rows


def read_records(limit: int = 200) -> list[dict]:
    """Newest first."""
    return _rows()[-limit:][::-1]


def get_record(record_id: str) -> Optional[dict]:
    return next((r for r in _rows() if r.get("id") == record_id), None)


def _latest(keys: tuple[str, ...], version: Optional[str] = None) -> Optional[dict]:
    """Newest record filed under the first key that has one (keys in priority order), optionally only of one version."""
    rows = _rows()
    for k in keys:
        for r in reversed(rows):
            if r.get("key") == k and (version is None or r.get("version") == version):
                return r
    return None


def _view(r: Optional[dict]) -> Optional[dict]:
    if not r:
        return None
    return {k: r.get(k) for k in ("id", "iso", "mode", "version", "company", "signatory", "title", "scope", "user", "ai_training_allowed",
                                  "private_pending_payment")}


def company_summary(company_id: str) -> Optional[dict]:
    """The agreement of one directory company for the admin lists: {mode, version, iso, ...} or None."""
    return _view(_latest(("company:%s" % company_id,)))


def training_allowed(company_or_email: str) -> bool:
    """May MOTRES's internal AI train on this client's engineering data?  ``company_or_email`` is an account e-mail, a directory
    company id or a stored key (``company:<id>`` / ``user:<email>``).  FREE -> True; PRIVATE -> False; no acceptance -> False
    (fail-closed); MOTRES's own company and domains -> True (its own data).  The acceptance in force is the newest one of any
    version (a later version replaces it when it is given)."""
    s = str(company_or_email or "").strip()
    if not s:
        return False
    if "@" in s and not s.startswith(("company:", "user:")):
        sc = scope_of(s)
        if sc["exempt"] == "motres":
            return True
        r = _latest((sc["key"], sc["account_key"]))
    else:
        key = s if s.startswith(("company:", "user:")) else "company:" + s
        if key.startswith("user:"):
            if key[5:].rsplit("@", 1)[-1].lower() in _own_domains():
                return True
        else:
            try:
                from motor_ai_sim.directory import store as DS
                c = next((c for c in DS.load_companies() if c.get("id") == key[8:]), None)
            except Exception:                                          # noqa: BLE001
                c = None
            if c and _is_own_company(dict(id=c.get("id"), name=c.get("legal_name"), reg_no=c.get("reg_no"))):
                return True
        r = _latest((key,))
    return bool(r and r.get("mode") == "FREE")


# -- status and the gate ------------------------------------------------------

def status_for(email: str, role: Any = "", *, is_admin: bool = False) -> dict:
    """What the web needs.  While nothing is enforced a normal user gets only ``enforced: false`` (the UI says nothing); an
    admin also learns that the approved text is not installed."""
    cur = current("en")
    on = enforced()
    out: dict[str, Any] = {"enforced": on, "required": False, "accepted": False, "exempt": None}
    if is_admin:
        out["admin"] = {"installed": bool(cur and cur["installed"]), "version": cur["version"] if cur else None,
                        "placeholder": bool(cur and cur["placeholder"]), "disabled": disabled()}
    if not on:
        return out
    sc = scope_of(email, role)
    version = cur["version"]
    now_rec = _latest((sc["key"], sc["account_key"]), version)
    latest = now_rec or _latest((sc["key"], sc["account_key"]))
    facts = company_facts(email, sc["company"])
    out.update(version=version, modes=list(MODES), exempt=sc["exempt"], scope=sc["scope"], accepted=bool(now_rec),
               required=not sc["exempt"] and not now_rec, acceptance=_view(latest),
               company=dict(facts, known=bool(sc["company"]), complete=all(facts.values())))
    return out


def required_detail(email: str, role: Any = "") -> Optional[dict]:
    """None when the caller may start a development; else the body of the 428 (``code`` ``agreement_required``)."""
    if not enforced():
        return None
    email = _norm_email(email)
    if not role and email:                      # a caller that knows only the e-mail (an agent key): read the role from the registry
        try:
            from motor_ai_sim import users as U
            role = (U.get_user(email) or {}).get("role") or ""
        except Exception:                                              # noqa: BLE001
            role = ""
    if not email or is_staff(role):
        return None
    sc = scope_of(email, role)
    if sc["exempt"]:
        return None
    version = current("en")["version"]
    if _latest((sc["key"], sc["account_key"]), version):
        return None
    return {"code": CODE, "version": version,
            "message": "Accept the AeroStator Platform Services and Data Agreement (version %s) to start a development." % version}


def require_accepted(user: Any) -> None:
    """Route hook: HTTP 428 ``agreement_required`` unless the caller's company accepted the current version (or nothing is enforced
    or the caller is staff / MOTRES).  ``user`` is the dict the auth layer resolves (``email`` + ``role``) or a caller identity."""
    from fastapi import HTTPException
    u = user if isinstance(user, dict) else {}
    detail = required_detail(u.get("email") or u.get("id"), u.get("role"))
    if detail:
        raise HTTPException(status_code=428, detail=detail)


# -- accepting ----------------------------------------------------------------

def _norm_text(v: Any, limit: int = 300) -> str:
    return N._norm(v)[:limit + 1]


def validate(body: dict, known: dict) -> tuple[dict, dict]:
    """-> (clean fields, errors {field: message}).  Mode FREE|PRIVATE, signer and title typed, both boxes ticked (business use,
    authority); the client's company data comes from the directory / NDA and is asked for only where we do not know it."""
    err: dict[str, str] = {}
    mode = str(body.get("mode") or "").strip().upper()
    if mode not in MODES:
        err["mode"] = "choose FREE or PRIVATE"
    signer, title = _norm_text(body.get("signer")), _norm_text(body.get("title"))
    for k, v in (("signer", signer), ("title", title)):
        if not v:
            err[k] = "required"
        elif len(v) > 300:
            err[k] = "too long"
    if body.get("business") is not True:
        err["business"] = "confirm that the platform is used for business purposes only"
    if body.get("authority") is not True:
        err["authority"] = "confirm that you are authorised to bind the company"
    f = {k: known.get(k) or _norm_text(body.get(k)) for k in FIELDS}
    for k in FIELDS:
        if not f[k]:
            err[k] = "required"
        elif len(f[k]) > 300:
            err[k] = "too long"
    f["country"] = f["country"].upper()
    if f["country"] and f["country"] not in N.COUNTRIES:
        err["country"] = "choose a country from the list"
    return dict(f, mode=mode, signatory=signer, title=title), err


def accept(*, user: str, role: Any, fields: dict, cur: dict, ip: str, user_agent: str) -> dict:
    """Render, write the PDF, append the record -> the record (with ``_pdf``).  Raises ``FileExistsError`` when the company already
    accepted this version, ``OSError`` when the legal directory is not writable."""
    from motor_ai_sim.private_files import ensure_private_dir, open_private
    root = N.legal_root()
    if not root.is_dir():
        raise OSError("legal directory %s is not installed" % root)
    sc = scope_of(user, role)
    now = datetime.datetime.now(datetime.timezone.utc)
    clean = {k: fields[k] for k in ("company", "reg_no", "country", "address", "signatory", "title")}
    values = N.template_values(clean, user, now.strftime("%Y-%m-%d"), cur["version"]) | {"mode": fields["mode"]}
    texts = cur.get("texts") or {}
    if set(texts) != set(LANGS):
        raise ValueError("Both English and zh-CN agreement texts are required.")
    rendered = {code: N.render(texts[code], values) for code in LANGS}
    rid = "a%s-%s" % (now.strftime("%Y%m%d"), secrets.token_hex(6))
    mode = fields["mode"]
    rec = dict(id=rid, ts=round(now.timestamp(), 3), iso=now.strftime("%Y-%m-%dT%H:%M:%SZ"), key=sc["key"], scope=sc["scope"],
               company_id=(sc["company"] or {}).get("id", ""), user=user, email=user, **clean, mode=mode, business=True, authority=True,
               ai_training_allowed=mode == "FREE", private_pending_payment=mode == "PRIVATE",
               version=cur["version"], lang=cur["lang"], template_sha256=cur["sha256"], text_sha256=N.sha256_text(rendered[cur["lang"]]),
               template_sha256s={c: cur.get("sha256s", {}).get(c) or N.sha256_text(texts[c]) for c in LANGS},
               text_sha256s={c: N.sha256_text(rendered[c]) for c in LANGS},
               ip=ip, user_agent=(user_agent or "")[:300], pdf=rid + ".pdf", free_mail=N.free_mail(user))
    rows = (("Mode", mode),
            ("AI training on the Client's engineering data", "allowed (FREE)" if mode == "FREE" else "not allowed (PRIVATE)"),
            ("Business use only / authority to bind the company", "confirmed / confirmed"))
    pdf = N.build_pdf(rendered, rec, label="Platform Agreement", extra_rows=rows,
                      note="Accepted online: mode chosen, business-use and authority boxes ticked, signer and title typed. The "
                           "selected-language SHA-256 above is the hash of that exact rendered text; both language hashes are recorded.")
    with _LOCK:
        if _latest((sc["key"], sc["account_key"]), cur["version"]):
            raise FileExistsError(cur["version"])
        d = ensure_private_dir(root / "platform_agreement_signed")
        tmp = d / (rid + ".pdf.tmp")
        tmp.write_bytes(pdf)
        os.replace(tmp, d / (rid + ".pdf"))
        with open_private(acceptances_path(), "a") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
        _CACHE["sig"] = None
    rec["_pdf"] = pdf
    return rec


def public_record(r: dict) -> dict:
    """Admin overview row (no user agent, no hashes of the agent)."""
    return {k: r.get(k) for k in ("id", "iso", "user", "email", "key", "scope", "company_id", "company", "reg_no", "country", "signatory",
                                  "title", "mode", "version", "lang", "ai_training_allowed", "private_pending_payment", "free_mail",
                                  "ip", "text_sha256")}
