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
calls: company acceptance, else account acceptance (FREE -> True, PRIVATE -> False), else the ticked Terms of Use box of v0.4 or later
(``legal_texts``) -> True, else False (fail-closed); MOTRES's own data -> True.

:func:`training_eligibility` is the richer answer behind it (owner 2026-10-31, agreement sections 2.3, 2.4, 3.4, 7.9, 7.10): WHEN the data
became eligible, the election for earlier data and the capacity; the allow-list of the dataset export (``dataset_exclusions()["training"]``,
:func:`training_allowlist`) is built from it.  A written PRIVATE request puts a no-training HOLD on the client at once
(``platform_agreement_holds.jsonl``, append-only; admin ``POST /api/admin/agreement/holds``).
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
    """May MOTRES's internal AI train on data this client supplies NOW?  True iff :func:`training_eligibility` has a window that is open
    at this moment (a PRIVATE switch or a hold closes it; data of the closed window stay eligible for the allow-list, see there)."""
    w = training_eligibility(company_or_email)
    return w is not None and (not w.get("eligible_until") or w["eligible_until"] > _iso(datetime.datetime.now(datetime.timezone.utc).timestamp()))


# -- no-training holds (agreement section 3.4, gate R10) ----------------------------------------------------------

def holds_path() -> Path:
    return N.legal_root() / "platform_agreement_holds.jsonl"


def _hold_rows() -> list[dict]:
    rows = []
    try:
        with open(holds_path(), encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict):
                    rows.append(r)
    except OSError:
        return []
    return rows


def hold_key(*, email: Any = "", company_id: Any = "") -> str:
    """The filing key of a hold: ``company:<directory id>`` or ``user:<e-mail>``."""
    if str(company_id or "").strip():
        return "company:%s" % str(company_id).strip()
    e = _norm_email(email)
    if not e or "@" not in e:
        raise ValueError("name an e-mail address or a directory company id")
    return "user:%s" % e


def hold_state() -> tuple[dict[str, dict], dict[str, str]]:
    """(active, released): key -> the ``hold`` line in force, and key -> time of the ``release`` that ended its newest hold.  A key is on
    hold from its newest ``hold`` line until a later ``release`` line.  Data supplied while the key was held are never eligible, so a
    release reopens the window from the release time on (as a FREE - PRIVATE - FREE history keeps the newest FREE run only)."""
    active: dict[str, dict] = {}
    released: dict[str, str] = {}
    for r in _hold_rows():
        k = str(r.get("key") or "")
        if not k:
            continue
        if r.get("kind") == "release":
            if active.pop(k, None) is not None:
                released[k] = _iso_of(r)
        elif r.get("kind") == "hold":
            active[k] = r
    return active, released


def active_holds() -> dict[str, dict]:
    """key -> the ``hold`` line in force (see :func:`hold_state`)."""
    return hold_state()[0]


def record_hold(key: str, *, kind: str, actor: str, note: str = "", reference: str = "") -> dict:
    """Append a ``hold`` (a written PRIVATE request was received: no training on these data until the Order is concluded or declined,
    section 3.4) or a ``release`` line.  Never rewritten.  ``OSError`` when the legal directory is not writable."""
    from motor_ai_sim.private_files import ensure_private_dir, open_private
    if kind not in ("hold", "release"):
        raise ValueError("kind must be hold or release")
    if not re.match(r"^(company|user):.+", key or ""):
        raise ValueError("bad hold key %r" % (key,))
    root = N.legal_root()
    if not root.is_dir():
        raise OSError("legal directory %s is not installed" % root)
    now = datetime.datetime.now(datetime.timezone.utc)
    rec = dict(id="h%s-%s" % (now.strftime("%Y%m%d"), secrets.token_hex(6)), ts=round(now.timestamp(), 3), iso=_iso(now.timestamp()),
               kind=kind, key=key, actor=str(actor or ""), note=_norm_text(note, 500), reference=_norm_text(reference, 200))
    with _LOCK:
        ensure_private_dir(root)
        with open_private(holds_path(), "a") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
    return rec


# -- eligibility for AI training (owner 2026-10-31) -------------------------------------------------------------------

EPOCH = "1970-01-01T00:00:00Z"


def _iso(ts: float) -> str:
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso_of(rec: dict) -> str:
    iso = str(rec.get("iso") or "")
    if iso:
        return iso
    try:
        return _iso(float(rec.get("ts") or 0))
    except (TypeError, ValueError, OverflowError, OSError):
        return EPOCH


def _created_iso(email: str) -> str:
    """Creation time of the account (UTC ISO); the registry stores local time.  "" when unknown."""
    try:
        from motor_ai_sim import users as U
        raw = str((U.get_user(email) or {}).get("created") or "")
        if not raw:
            return ""
        local = datetime.datetime.strptime(raw[:19], "%Y-%m-%dT%H:%M:%S")
        return local.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:                                                  # noqa: BLE001
        return ""


def _window(rows: list[dict]) -> tuple[Optional[str], Optional[str]]:
    """(start, end) of the FREE window an ordered run of agreement records (oldest first, ONE filing key) leaves.  ``start`` = the first
    record of the FREE run that is still the newest FREE run (None when the key never had FREE); ``end`` = the first non-FREE (PRIVATE)
    record after it (None while the newest record is FREE).  Owner decision 2026-10-31 (agreement 11.1): a switch to PRIVATE excludes
    only what is supplied from that moment, so the data of the FREE run stay eligible and the window merely closes.  A FREE - PRIVATE -
    FREE history keeps only the newest FREE run (fail-closed: the PRIVATE interval is never eligible)."""
    if not rows:
        return None, None
    modes = [str(r.get("mode") or "").upper() for r in rows]
    last = len(rows) - 1
    if modes[last] == "FREE":
        i = last
        while i > 0 and modes[i - 1] == "FREE":
            i -= 1
        return _iso_of(rows[i]), None
    j = last
    while j >= 0 and modes[j] != "FREE":
        j -= 1
    end = _iso_of(rows[j + 1])
    if j < 0:
        return None, end
    i = j
    while i > 0 and modes[i - 1] == "FREE":
        i -= 1
    return _iso_of(rows[i]), end


class _Eligibility:
    """One read of the stores the eligibility rules need; reused for a whole export (:func:`training_allowlist`)."""

    def __init__(self) -> None:
        from motor_ai_sim import legal_texts as LT
        self.LT = LT
        self.by_key: dict[str, list[dict]] = {}
        for r in sorted(_rows(), key=lambda r: float(r.get("ts") or 0)):          # stable: file order breaks ties
            k = str(r.get("key") or "")
            if k:
                self.by_key.setdefault(k, []).append(r)
        self.newest = {k: v[-1] for k, v in self.by_key.items()}
        self.holds, self.released = hold_state()
        self._terms: Optional[dict[str, list[dict]]] = None

    def terms_lines(self, email: str) -> list[dict]:
        if self._terms is None:
            by: dict[str, list[dict]] = {}
            for r in sorted(self.LT.records(), key=lambda r: float(r.get("ts") or 0)):
                by.setdefault(_norm_email(r.get("user")), []).append(r)
            self._terms = by
        return self._terms.get(_norm_email(email), [])

    def hold_since(self, email: str, company: Optional[dict]) -> Optional[str]:
        """Time of the earliest no-training hold on the account or its company, None = no hold."""
        ks = ["user:%s" % email] + (["company:%s" % company.get("id")] if company else [])
        times = [_iso_of(self.holds[k]) for k in ks if k in self.holds]
        return min(times) if times else None

    def held(self, email: str, company: Optional[dict]) -> bool:
        return self.hold_since(email, company) is not None

    def mode_of(self, email: str, company: Optional[dict]) -> Optional[str]:
        """Mode of the agreement acceptance in force for this address (company record first, then the account's), None = none."""
        for k in (("company:%s" % company["id"]) if company else "", "user:%s" % email):
            r = self.newest.get(k) if k else None
            if r:
                return str(r.get("mode") or "").upper()
        return None

    @staticmethod
    def _out(start: str, end: Optional[str], *, historical: bool, capacity: str, scope: str) -> Optional[dict]:
        if end is not None and end <= start:
            return None                                                # the window closed before it opened
        out = {"mode": "FREE", "eligible_from": start, "historical_opt_in": historical, "capacity": capacity, "scope": scope}
        if end is not None:
            out["eligible_until"] = end
        return out

    def company(self, company_id: str) -> Optional[dict]:
        key = "company:%s" % company_id
        start, end = _window(self.by_key.get(key, []))
        if start is None:
            return None
        start = max(start, self.released.get(key, ""))
        if key in self.holds:
            end = min([end, _iso_of(self.holds[key])] if end else [_iso_of(self.holds[key])])
        return self._out(start, end, historical=False, capacity="company", scope="company")

    def email(self, email: str, company: Optional[dict]) -> Optional[dict]:
        email = _norm_email(email)
        if email.rsplit("@", 1)[-1] in _own_domains() or _is_own_company(company):
            return {"mode": "FREE", "eligible_from": EPOCH, "historical_opt_in": True, "capacity": "own", "scope": "own"}
        caps: list[str] = []
        base: Optional[dict] = None
        ckey = ("company:%s" % company["id"]) if company else ""
        if ckey and self.by_key.get(ckey):                             # (a) the company decides for its confirmed users
            s, e = _window(self.by_key[ckey])
            if s is not None:
                base = {"start": s, "end": e, "historical": False, "capacity": "company", "scope": "company"}
            elif e:
                caps.append(e)                                         # PRIVATE from the start: only earlier account data remain
        if base is None:
            ukey = "user:%s" % email
            s, e = _window(self.by_key.get(ukey, []))                  # (b) an account-level agreement acceptance
            if s is not None:
                base = {"start": s, "end": e, "historical": False, "capacity": "business", "scope": "account"}
            else:
                if e:
                    caps.append(e)
                base = self._terms_window(email)                       # (c) the Terms of Use box by its capacity
        if base is None:
            return None
        h = self.hold_since(email, company)
        if h:
            caps.append(h)
        for k in ("user:%s" % email, ckey):
            if k and k in self.released:
                base["start"] = max(base["start"], self.released[k])   # a released hold: never the interval it covered
        if base["end"]:
            caps.append(base["end"])
        return self._out(base["start"], min(caps) if caps else None, historical=base["historical"], capacity=base["capacity"],
                         scope=base["scope"])

    def _terms_window(self, email: str) -> Optional[dict]:
        """Terms of Use box of v0.4 or later, by the capacity stated (section 2.3).  The newest line decides; the run of qualifying lines
        up to it sets the start.  ``other`` and a line without a capacity stay out (fail-closed until a per-data confirmation exists)."""
        lines = [r for r in self.terms_lines(email) if self.LT.version_ge(r.get("terms_version"), self.LT.MIN_TRAINING_VERSION)]
        run: list[dict] = []
        for r in reversed(lines):
            if str(r.get("capacity") or "") not in self.LT.TRAINING_CAPACITIES:
                break
            run.append(r)
        if not run:
            return None
        start = _iso_of(run[-1])
        historical = any(r.get("historical_opt_in") is True for r in run)
        if historical:
            created = _created_iso(email)
            start = min(start, created) if created else start         # unknown creation time: no extension (fail-closed)
        return {"start": start, "end": None, "historical": historical, "capacity": str(run[0].get("capacity")), "scope": "account"}


def training_eligibility(company_or_email: str) -> Optional[dict]:
    """May MOTRES's internal AI train on this client's engineering data, and in which window?  -> ``{mode, eligible_from (ISO UTC),
    [eligible_until (ISO UTC)], historical_opt_in, capacity, scope}`` or None (never eligible).  A record is eligible iff
    ``eligible_from <= its time < eligible_until`` (``eligible_until`` absent = open-ended).  ``company_or_email`` is an account e-mail,
    a directory company id or a stored key (``company:<id>`` / ``user:<email>``).  Rules, first match decides (agreement sections 2.3,
    2.4, 3.4, 7.9, 7.10, 11.1):

    (a) a COMPANY acceptance of this agreement decides for the company's confirmed users: FREE -> window from the acceptance;
    (b) else an ACCOUNT-level acceptance of this agreement decides the same way;
    (c) else the Terms of Use box of ``legal_texts.MIN_TRAINING_VERSION`` (v0.4) or later (``terms_acceptances.jsonl``) by its capacity:
        ``business`` / ``sole_trader`` -> window from the acceptance, or from the account's creation when the earlier-data box
        (7.9) was ticked; ``other`` (no authority to bind) and a line without a capacity -> None until a per-data confirmation exists;
    (d) else None (fail-closed): nothing accepted, a bare company id without an acceptance, an unknown key.

    PRIVATE and holds close the window, they never reopen the past (owner 2026-10-31, agreement 11.1): a switch to PRIVATE by the company
    or the account, or a no-training HOLD (a written PRIVATE request, section 3.4), sets ``eligible_until`` to that moment; data supplied
    in FREE mode before it stay eligible.  An entity that was never FREE (or whose window is empty) -> None.

    MOTRES's own company and domains -> eligible from the epoch with scope ``own`` (its own data; data derived from a client's project
    still pass through the project rules of :func:`training_allowlist`).  The acceptance in force is the newest one of any version of this
    agreement."""
    s = str(company_or_email or "").strip()
    if not s:
        return None
    el = _Eligibility()
    if s.startswith("company:") or ("@" not in s and not s.startswith("user:")):
        cid = s[8:] if s.startswith("company:") else s
        try:
            from motor_ai_sim.directory import store as DS
            c = next((c for c in DS.load_companies() if c.get("id") == cid), None)
        except Exception:                                              # noqa: BLE001
            c = None
        if c and _is_own_company(dict(id=c.get("id"), name=c.get("legal_name"), reg_no=c.get("reg_no"))):
            return {"mode": "FREE", "eligible_from": EPOCH, "historical_opt_in": True, "capacity": "own", "scope": "own"}
        return el.company(cid)
    email = _norm_email(s[5:] if s.startswith("user:") else s)
    if "@" not in email:
        return None
    return el.email(email, company_of(email))


def _staff_emails() -> set[str]:
    try:
        from motor_ai_sim import users as U
        return {_norm_email(u.get("email")) for u in U.list_users() if is_staff(u.get("role"))}
    except Exception:                                                  # noqa: BLE001
        log.exception("platform_agreement: user registry unreadable for the training allow-list")
        return set()


def training_allowlist() -> dict:
    """The allow-list of the dataset export (owner D10; ``dataset_exclusions()["training"]``): ONLY entries that are eligible appear, and
    whatever is absent is NOT eligible.  Built from :func:`training_eligibility`; see ``docs/PLATFORM_AGREEMENT.md`` ("For the dataset
    export") for the shape.  An entity that was FREE and then went PRIVATE or was held STAYS in the list with ``eligible_until`` set to that
    moment (owner 2026-10-31, agreement 11.1: only what is supplied from then on is excluded).  A project is eligible only if its owning side
    (the non-staff owners; for a staff-owned project the customer members) is eligible and no member who was never FREE is PRIVATE or on hold
    (agreement 2.4, the most restrictive permission wins); its window is the intersection of its owners' windows (latest ``eligible_from``,
    earliest ``eligible_until``), also closed by a member who switched to PRIVATE.  A store that cannot be read raises (the caller must not read that as "nothing")."""
    from motor_ai_sim.directory import store as DS
    from motor_ai_sim.workspace import workspace_id
    el = _Eligibility()
    people, comps, affs = DS.load_people(), {c.get("id"): c for c in DS.load_companies()}, DS.load_affiliations()
    comp_of_person: dict[Any, str] = {}
    for a in sorted((a for a in affs if a.get("status") == "confirmed"), key=lambda a: str(a.get("updated") or "")):
        if a.get("company_id") in comps:
            comp_of_person[a.get("person_id")] = a["company_id"]
    comp_of_email: dict[str, str] = {}
    for p in people:
        cid = comp_of_person.get(p.get("id"))
        if cid:
            for e in list(p.get("emails") or []) + [p.get("user_id")]:
                if _norm_email(e):
                    comp_of_email[_norm_email(e)] = cid

    def company_dict(e: str) -> Optional[dict]:
        c = comps.get(comp_of_email.get(e))
        return dict(id=c["id"], name=str(c.get("legal_name") or ""), reg_no=str(c.get("reg_no") or "")) if c else None

    known: set[str] = set(comp_of_email)
    staff = _staff_emails()
    try:
        from motor_ai_sim import users as U
        known |= {_norm_email(u.get("email")) for u in U.list_users()}
    except Exception:                                                  # noqa: BLE001
        pass
    for r in el.LT.records():
        known.add(_norm_email(r.get("user")))
    known |= {k[5:] for k in el.newest if k.startswith("user:")}
    from motor_ai_sim.development import store as PS
    projects = PS.list_projects()
    for p in projects:
        for m in p.get("members") or []:
            if isinstance(m, dict):
                known.add(_norm_email(m.get("email")))
    known = {e for e in known if e and "@" in e}

    accounts: dict[str, dict] = {}
    own_emails: list[str] = []
    verdict: dict[str, Optional[dict]] = {}
    for e in sorted(known):
        v = el.email(e, company_dict(e))
        verdict[e] = v
        if v is None:
            continue
        if v["scope"] == "own":
            own_emails.append(e)
        else:
            accounts[e] = {"eligible_from": v["eligible_from"], "mode": v["mode"], "historical_opt_in": v["historical_opt_in"],
                           "capacity": v["capacity"], **({"eligible_until": v["eligible_until"]} if v.get("eligible_until") else {})}

    companies: dict[str, dict] = {}
    own_company_ids = sorted(cid for cid, c in comps.items()
                             if _is_own_company(dict(id=cid, name=c.get("legal_name"), reg_no=c.get("reg_no"))))
    for cid in sorted(comps):
        if cid in own_company_ids:
            continue
        v = el.company(cid)
        if v:
            companies[cid] = {"eligible_from": v["eligible_from"], "mode": v["mode"],
                              **({"eligible_until": v["eligible_until"]} if v.get("eligible_until") else {})}

    eligible_projects: dict[str, dict] = {}
    for p in projects:
        members = [(_norm_email(m.get("email")), m.get("role")) for m in p.get("members") or []
                   if isinstance(m, dict) and "@" in _norm_email(m.get("email"))]
        customers = [(e, r) for e, r in members if e not in staff]
        blocked = False
        member_ends: list[str] = []
        for e, _r in customers:
            if e in own_emails or e.rsplit("@", 1)[-1] in _own_domains():
                continue
            v = verdict.get(e)
            if v is None:
                if el.mode_of(e, company_dict(e)) == "PRIVATE" or el.held(e, company_dict(e)):
                    blocked = True                                     # never FREE and PRIVATE / held: the most restrictive wins
                    break
            elif v.get("eligible_until"):
                member_ends.append(v["eligible_until"])               # a member who switched to PRIVATE / was held closes the window
        if blocked:
            continue
        owners = [e for e, r in customers if r == "owner"] or [e for e, _r in customers]
        if not owners or any(verdict.get(e) is None for e in owners):
            continue
        start = max(verdict[e]["eligible_from"] for e in owners)
        ends = member_ends + [verdict[e]["eligible_until"] for e in owners if verdict[e].get("eligible_until")]
        if ends and min(ends) <= start:
            continue
        eligible_projects[str(p.get("id"))] = {"eligible_from": start, **({"eligible_until": min(ends)} if ends else {})}

    wanted = sorted(set(accounts) | set(own_emails))
    return {"version": 1, "policy": "allow-list", "generated": _iso(datetime.datetime.now(datetime.timezone.utc).timestamp()),
            "accounts": accounts, "companies": companies, "workspaces": {workspace_id(e): e for e in wanted},
            "projects": dict(sorted(eligible_projects.items())),
            "own": {"emails": own_emails, "domains": sorted(_own_domains()), "company_ids": own_company_ids}}


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


# -- the dataset exclusion list -------------------------------------------------

def _newest_by_key() -> dict[str, dict]:
    """Newest acceptance of ANY version per filing key (``company:<id>`` / ``user:<email>``): the one that decides the mode."""
    out: dict[str, dict] = {}
    for r in sorted(_rows(), key=lambda r: float(r.get("ts") or 0)):          # stable: file order breaks ties
        k = str(r.get("key") or "")
        if k:
            out[k] = r
    return out


def dataset_exclusions() -> dict:
    """Everything that must NOT be written to the engineering dataset (owner 2026-10-27: a PRIVATE client's data is not recorded at all).

    Unlike :func:`training_allowed` this is NOT fail-closed: an account or company with no acceptance, or with a FREE one, is not
    listed, so before anybody accepts anything every list is empty and the dataset behaves exactly as before.  Only the NEWEST
    acceptance counts (a later FREE one removes a company again).  Attribution:

    * ``companies``   directory company ids whose newest acceptance (key ``company:<id>``) is PRIVATE.
    * ``emails``      every address of a person CONFIRMED in such a company (their newest confirmed affiliation, as :func:`company_of`
                      files it), and every account whose own ``user:<email>`` acceptance is PRIVATE.  Where a company has a record
                      it decides, else the account's own record (the same priority as :func:`scope_of`).
    * ``workspace_ids`` ``workspace.workspace_id(e-mail)`` of each listed address.
    * ``project_ids`` a development project belongs to the customer side: if any non-staff OWNER member has a recorded acceptance,
                      the project is listed iff one of those owners is PRIVATE; else (staff-owned project, or an owner who accepted
                      nothing) it is listed iff ANY non-staff member is PRIVATE.  A FREE owner's project with a PRIVATE guest
                      member is therefore NOT listed (the owning company decided).
    * ``motors``      the die/config pairs linked to a listed project (current links and the link history), and the motor of a
                      production whose non-staff owner member is listed.  Compare die/config case-insensitively.
    * ``productions`` the productions of those motors, plus any production whose non-staff owner member is listed.
    * ``acceptance_ids`` ids of the agreement records (every version) filed under a listed company / account: their signed PDFs
                      and register lines name the client and must not reach the dataset either.
    * ``training``    the ALLOW-list (owner D10, :func:`training_allowlist`): the opposite polarity - only what is listed there may be
                      trained on; an account, company or project that is absent is not eligible.  The keys above are unchanged.

    Staff (admin / engineer accounts), MOTRES's own company and own domains are never listed.  A store that cannot be read raises
    (the caller must not mistake that for an empty list)."""
    newest = _newest_by_key()
    private_keys = {k for k, r in newest.items() if str(r.get("mode") or "").upper() == "PRIVATE"}
    companies = sorted(k[8:] for k in private_keys if k.startswith("company:"))
    out: dict[str, Any] = {"generated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                           "companies": companies, "emails": [], "workspace_ids": [], "project_ids": [], "motors": [], "productions": [],
                           "acceptance_ids": [], "training": training_allowlist()}
    if not private_keys:
        return out

    from motor_ai_sim.directory import store as DS
    people, comps, affs = DS.load_people(), {c.get("id"): c for c in DS.load_companies()}, DS.load_affiliations()

    # person -> company id (newest CONFIRMED affiliation whose company exists, the way company_of files an account)
    comp_of_person: dict[Any, str] = {}
    for a in sorted((a for a in affs if a.get("status") == "confirmed"), key=lambda a: str(a.get("updated") or "")):
        if a.get("company_id") in comps:
            comp_of_person[a.get("person_id")] = a["company_id"]
    comp_of_email: dict[str, str] = {}
    for p in people:
        cid = comp_of_person.get(p.get("id"))
        if cid:
            for e in list(p.get("emails") or []) + [p.get("user_id")]:
                if _norm_email(e):
                    comp_of_email[_norm_email(e)] = cid

    staff: set[str] = set()
    known: set[str] = set(comp_of_email)
    try:
        from motor_ai_sim import users as U
        for u in U.list_users():
            e = _norm_email(u.get("email"))
            known.add(e)
            if is_staff(u.get("role")):
                staff.add(e)
    except Exception:                                                  # noqa: BLE001 - no registry: nobody is known to be staff
        log.exception("platform_agreement: user registry unreadable for the dataset exclusions")
    known |= {k[5:] for k in private_keys if k.startswith("user:")}

    def exempt(e: str) -> bool:
        if e in staff or e.rsplit("@", 1)[-1] in _own_domains():
            return True
        c = comps.get(comp_of_email.get(e))
        return bool(c and _is_own_company(dict(id=c.get("id"), name=c.get("legal_name"), reg_no=c.get("reg_no"))))

    def mode_of(e: str) -> Optional[str]:
        """Mode of the acceptance in force for this address (company record first, then the account's), None = none."""
        for k in (("company:" + comp_of_email[e]) if e in comp_of_email else "", "user:" + e):
            r = newest.get(k) if k else None
            if r:
                return str(r.get("mode") or "").upper()
        return None

    from motor_ai_sim.development import store as PS
    projects = PS.list_projects()
    for p in projects:
        for m in p.get("members") or []:
            if isinstance(m, dict) and _norm_email(m.get("email")):
                known.add(_norm_email(m["email"]))

    emails = sorted(e for e in known if e and not exempt(e) and mode_of(e) == "PRIVATE")
    private_emails = set(emails)

    def member_emails(doc: dict, roles: Optional[tuple] = None) -> list[str]:
        return [_norm_email(m.get("email")) for m in doc.get("members") or []
                if isinstance(m, dict) and _norm_email(m.get("email")) and (roles is None or m.get("role") in roles)]

    def side_private(doc: dict) -> bool:
        owners = [e for e in member_emails(doc, ("owner",)) if not exempt(e)]
        if any(mode_of(e) for e in owners):
            return any(e in private_emails for e in owners)
        return any(e in private_emails for e in member_emails(doc) if not exempt(e))

    motors: dict[tuple[str, str], dict] = {}
    project_ids = []
    for p in projects:
        if not side_private(p):
            continue
        project_ids.append(str(p.get("id")))
        for row in list(p.get("linked_motors") or []) + list(p.get("motor_history") or []):
            if isinstance(row, dict) and row.get("die") and row.get("config"):
                motors.setdefault((str(row["die"]).casefold(), str(row["config"]).casefold()),
                                  {"die": str(row["die"]), "config": str(row["config"])})

    prods: dict[tuple[str, str], dict] = {}
    from motor_ai_sim.production import store as PRS
    root = PRS.production_root()
    for f in (sorted(root.glob("*/*/production.json")) if root.is_dir() else []):
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(doc, dict) or not doc.get("die") or not doc.get("config"):
            continue
        key = (str(doc["die"]).casefold(), str(doc["config"]).casefold())
        owners = [e for e in member_emails(doc, ("owner",)) if not exempt(e)]
        if key in motors or any(e in private_emails for e in owners):
            row = {"die": str(doc["die"]), "config": str(doc["config"])}
            prods[key] = row
            motors.setdefault(key, row)

    acceptance_ids = sorted(str(r.get("id")) for r in _rows() if r.get("id")
                            and (str(r.get("key") or "")[8:] in companies if str(r.get("key") or "").startswith("company:")
                                 else str(r.get("key") or "")[5:] in private_emails))

    from motor_ai_sim.workspace import workspace_id
    sort_key = lambda r: (r["die"].casefold(), r["config"].casefold())
    out.update(emails=emails, workspace_ids=sorted({workspace_id(e) for e in emails}), project_ids=sorted(project_ids),
               acceptance_ids=acceptance_ids, motors=sorted(motors.values(), key=sort_key), productions=sorted(prods.values(), key=sort_key))
    return out
