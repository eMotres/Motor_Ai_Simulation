"""The legal texts of the free simulation launch and the record of who accepted them (owner 2026-10-27, texts v0.4).

Three documents are accepted by ONE unticked box when an account is created: the Terms of Use, the Platform Agreement (the click-through
of ``platform_agreement.py`` is the company-level version of the same agreement) and the Privacy Policy.  The texts live in the repo,
EN and zh-CN, one directory per version, and are shown read-only in the app (``/terms``, ``/privacy``, ``/platform-agreement``):

    config/legal/terms_of_use/<version>/terms_of_use_<lang>.md
    config/legal/privacy/<version>/privacy_<lang>.md
    config/legal/platform_agreement_public/<version>/platform_agreement_<lang>.md   the agreement as published, for reading
    config/legal/platform_agreement_public/<version>/schedules_<lang>.md             Schedules 1-3 of the agreement

``<lang>`` is ``en`` or ``zh-CN``; English governs.  The files are copied verbatim from the approved texts; the only placeholder the server
fills is ``{{version}}``.  (The agreement carries the company placeholders of its click-through; they stay in the public copy and the
web page shows them as blanks.  What a COMPANY accepts is still read from ``<shared>/legal`` by ``platform_agreement.py``.)

The PUBLIC rendering of the agreement shows both section 13 variants: the click-through's ``{{#if country_is_cn_hk}}`` /
``{{#unless ...}}`` markers are stripped (:func:`public_rendering`), and the hash recorded with an account acceptance is of that rendering.

Acceptance record, append-only, one JSON line per acceptance, next to ``users.json`` / ``comm_prefs.json`` (0600):

    <identity root>/terms_acceptances.jsonl     id, ts, iso, user, terms_version, privacy_version, agreement_public_version, lang,
                                                text_sha256 {terms, privacy, platform_agreement, schedules} (of the text in ``lang``),
                                                country (where the business is established, required), capacity (agreement section 2.3:
                                                business | sole_trader | other, required), business_name (required for ``business``,
                                                optional otherwise), historical_opt_in (section 7.9: the optional box for the data the
                                                account entered before it accepted; only an existing account sees it), labels {accept,
                                                election} (the wording shown), forum_variant (A | B: B for CN / HK, agreement section 13),
                                                source (signup | google | oauth_signup | existing), ip, user_agent

Account deletion pseudonymises it like the consent history of ``comm_prefs`` (the proof stays, the person goes); the data export
carries the account's own lines.  A new version is published by adding its directories and bumping :data:`TERMS_VERSION`
(``docs/LEGAL_TEXTS.md``); an account that has no line for the current version is asked once, after it signs in.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import logging
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any, Optional

from motor_ai_sim import comm_prefs as CP
from motor_ai_sim import nda as N
from motor_ai_sim.file_lock import store_lock

log = logging.getLogger(__name__)

#: The version of the Terms of Use, the Privacy Policy and the public Platform Agreement a sign-up accepts.  One number for the set: a
#: change of any of them is a new version of all (the texts reference each other).
TERMS_VERSION = "v0.4"
PRIVACY_VERSION = TERMS_VERSION
AGREEMENT_PUBLIC_VERSION = TERMS_VERSION
#: First terms version that carries the FREE-mode data use: an account that accepted it may be trained on (platform_agreement.training_allowed).
MIN_TRAINING_VERSION = "v0.4"

LANGS = ("en", "zh-CN")
SOURCES = ("signup", "google", "oauth_signup", "existing")
#: Agreement section 2.3: (a) a named business the person may bind, (b) the person as a registered sole trader / self-employed
#: professional, (c) neither (an employee or contractor without authority to bind its employer or customer).
CAPACITIES = ("business", "sole_trader", "other")
#: Capacities whose acceptance can make the account's data eligible for training; ``other`` stays excluded until a per-data
#: confirmation exists (owner D4: fail-closed).
TRAINING_CAPACITIES = ("business", "sole_trader")
MAX_LABEL = 2000

#: public name of a document -> (directory under config/legal, file stem, version).  The order is the order of the acceptance hashes.
DOCS = {
    "terms": ("terms_of_use", "terms_of_use", TERMS_VERSION),
    "privacy": ("privacy", "privacy", PRIVACY_VERSION),
    "platform-agreement": ("platform_agreement_public", "platform_agreement", AGREEMENT_PUBLIC_VERSION),
    "schedules": ("platform_agreement_public", "schedules", AGREEMENT_PUBLIC_VERSION),
}
#: the key a document has in the acceptance record's ``text_sha256``
HASH_KEYS = {"terms": "terms", "privacy": "privacy", "platform-agreement": "platform_agreement", "schedules": "schedules"}

_LOCK = store_lock(lambda: acceptances_path())
_VERSION_RE = re.compile(r"^v\d+(?:\.\d+)*$")


class UnknownText(KeyError):
    """No such document / language / version in the repo."""


class CompanyError(ValueError):
    """The country / capacity / business name of an acceptance is not usable; ``code`` is the stable API code (``country_required`` |
    ``capacity_required`` | ``business_name_required`` | ``business_name_too_long``)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


MAX_BUSINESS_NAME = 300
_MARKER_LINE = re.compile(r"^[ \t]*\{\{[#/](?:if|unless)\b[^}]*\}\}[ \t]*\n", re.M)


# -- the texts ----------------------------------------------------------------

def legal_dir() -> Path:
    env = (os.environ.get("MOTRES_LEGAL_TEXTS_DIR") or "").strip()      # tests only
    return Path(env) if env else Path(__file__).resolve().parents[2] / "config" / "legal"


def norm_lang(lang: Any) -> str:
    """``zh-CN`` / ``en``; anything else is :class:`UnknownText` (the route answers 404, the sign-up falls back to English itself)."""
    s = str(lang or "en").strip().replace("_", "-")
    if s.lower() == "en":
        return "en"
    if s.lower() == "zh-cn":
        return "zh-CN"
    raise UnknownText("language %r" % (lang,))


def sha256_text(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


def public_rendering(text: str) -> str:
    """The public form of a text: the click-through's conditional block markers are removed so BOTH section 13 variants are visible
    (each labels itself "Variant A / B"); every other character is kept.  A text without markers is returned unchanged."""
    if "{{#" not in text:
        return text
    return re.sub(r"\n{3,}", "\n\n", _MARKER_LINE.sub("", text))


def countries() -> list[str]:
    """The ISO country codes of the NDA / agreement forms (the same list)."""
    return list(N.COUNTRIES)


def forum_variant(country: Any) -> str:
    """Agreement section 13: ``B`` (Hong Kong law, HKIAC) when the business is established in Mainland China or Hong Kong, else ``A``."""
    return "B" if N.is_cn_hk(country) else "A"


def clean_company(country: Any, business_name: Any = "") -> tuple[str, str]:
    """-> (country code, business name).  The country is required and must be on the list; the business name is optional (empty for a
    sole trader), whitespace-normalised.  Raises :class:`CompanyError`."""
    c = str(country or "").strip().upper()
    if not c or c not in N.COUNTRIES:
        raise CompanyError("country_required", "Choose the country where your business is established.")
    name = N._norm(business_name)
    if len(name) > MAX_BUSINESS_NAME:
        raise CompanyError("business_name_too_long", "The business name is too long (at most %d characters)." % MAX_BUSINESS_NAME)
    return c, name


def clean_acceptance(country: Any, business_name: Any = "", capacity: Any = "") -> tuple[str, str, str]:
    """-> (country code, business name, capacity).  Country as :func:`clean_company`; the capacity (agreement section 2.3) is required and
    one of :data:`CAPACITIES` (``capacity_required``); a ``business`` acceptance must name the business (``business_name_required``).
    Raises :class:`CompanyError`."""
    c, name = clean_company(country, business_name)
    cap = str(capacity or "").strip().lower()
    if cap not in CAPACITIES:
        raise CompanyError("capacity_required", "Choose in which capacity you accept: for a business you may bind, for yourself as a "
                                                "registered sole trader, or neither.")
    if cap == "business" and not name:
        raise CompanyError("business_name_required", "Enter the name of the business you are authorised to bind.")
    return c, name, cap


def clean_labels(accept_label: Any = "", election_label: Any = "") -> dict:
    """The wording of the two boxes as the person saw it (agreement section 15.3 keeps the labels): {accept, election}, whitespace-
    normalised and capped; keys of a label that was not sent are left out."""
    out = {}
    for k, v in (("accept", accept_label), ("election", election_label)):
        t = N._norm(v)[:MAX_LABEL]
        if t:
            out[k] = t
    return out


def read(doc: str, lang: str = "en") -> dict:
    """``{doc, version, lang, text, sha256}`` of one document in one language; ``{{version}}`` is filled, nothing else is touched.
    The hash is that of the text as served.  Raises :class:`UnknownText`."""
    if doc not in DOCS:
        raise UnknownText("document %r" % (doc,))
    lang = norm_lang(lang)
    sub, stem, version = DOCS[doc]
    path = legal_dir() / sub / version / ("%s_%s.md" % (stem, lang))
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        raise UnknownText("%s %s %s" % (doc, version, lang))
    text = public_rendering(raw.replace("\r\n", "\n").replace("{{version}}", version))
    return {"doc": doc, "version": version, "lang": lang, "text": text, "sha256": sha256_text(text)}


def shown_hashes(lang: str = "en") -> dict:
    """The SHA-256 of each text in the language shown at acceptance: ``{terms, privacy, platform_agreement, schedules}``.  A language
    whose file is not (yet) in the repo is recorded as ``""`` for that text rather than blocking an acceptance."""
    out = {}
    for doc, key in HASH_KEYS.items():
        try:
            out[key] = read(doc, lang)["sha256"]
        except UnknownText:
            out[key] = ""
    return out


# -- versions -----------------------------------------------------------------

def _vtuple(v: Any) -> tuple:
    s = str(v or "").strip()
    return tuple(int(x) for x in s[1:].split(".")) if _VERSION_RE.match(s) else ()


def version_ge(version: Any, minimum: str) -> bool:
    """``v0.10 >= v0.4`` by number, not by string; a version that is not ``v<n>[.<n>...]`` is never enough."""
    a, b = _vtuple(version), _vtuple(minimum)
    return bool(a) and bool(b) and a >= b


def acceptance_required() -> bool:
    """False only when ``TERMS_ACCEPTANCE_DISABLED=1`` (the test suite sets it; production never does)."""
    return (os.environ.get("TERMS_ACCEPTANCE_DISABLED") or "").strip().lower() not in ("1", "true", "yes")


def accepted_now(accept_terms: Any, terms_version: Any) -> bool:
    """Did the sign-up request tick the box, for THIS version of the texts?"""
    return accept_terms is True and str(terms_version or "").strip() == TERMS_VERSION


# -- the record ---------------------------------------------------------------

def acceptances_path() -> Path:
    return CP._ROOT / "terms_acceptances.jsonl"


def _norm(email: Any) -> str:
    return str(email or "").strip().lower()


def _iso(ts: float) -> str:
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def records(email: Optional[str] = None) -> list[dict]:
    """The acceptances (oldest first), optionally of one account."""
    rows: list[dict] = []
    try:
        with open(acceptances_path(), encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict) and (email is None or _norm(r.get("user")) == _norm(email)):
                    rows.append(r)
    except OSError:
        return []
    return rows


def record_acceptance(email: str, *, source: str, country: str, business_name: str = "", capacity: str = "", lang: str = "en",
                      historical_opt_in: bool = False, labels: Optional[dict] = None, ip: str = "", user_agent: str = "") -> dict:
    """Append the acceptance of the CURRENT texts by this account and return the record.  ``OSError`` when it cannot be written (the
    caller decides: a sign-up logs it and goes on, the existing-user dialog tells the person to try again).  ``ValueError`` for an
    unknown source, :class:`CompanyError` for a country that is not on the list or a missing capacity / business name.

    ``historical_opt_in`` (agreement section 7.9) is kept only for source ``existing``: a new account has no earlier data."""
    email = _norm(email)
    if not email:
        raise ValueError("no account")
    if source not in SOURCES:
        raise ValueError("unknown source: %r" % (source,))
    try:
        lang = norm_lang(lang)
    except UnknownText:
        lang = "en"
    from motor_ai_sim.private_files import ensure_private_dir, open_private
    country, business_name, capacity = clean_acceptance(country, business_name, capacity)
    now = time.time()
    rec = dict(id="t%s-%s" % (datetime.datetime.fromtimestamp(now, datetime.timezone.utc).strftime("%Y%m%d"), secrets.token_hex(6)),
               ts=round(now, 3), iso=_iso(now), user=email, terms_version=TERMS_VERSION, privacy_version=PRIVACY_VERSION,
               agreement_public_version=AGREEMENT_PUBLIC_VERSION, lang=lang, text_sha256=shown_hashes(lang), country=country,
               capacity=capacity, business_name=business_name, historical_opt_in=bool(historical_opt_in is True and source == "existing"),
               labels=dict(labels or {}), forum_variant=forum_variant(country), source=source,
               ip=str(ip or ""), user_agent=str(user_agent or "")[:300])
    with _LOCK:
        ensure_private_dir(acceptances_path().parent)
        with open_private(acceptances_path(), "a") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
    return rec


def record_signup(email: str, *, source: str, country: str, business_name: str, lang: str, ip: str, user_agent: str,
                  capacity: str = "", labels: Optional[dict] = None) -> Optional[dict]:
    """The acceptance of a new account.  A failure to store it never blocks the account (the person is asked again at the next
    sign-in, see :func:`needs_acceptance`)."""
    try:
        return record_acceptance(email, source=source, country=country, business_name=business_name, capacity=capacity, lang=lang,
                                 labels=labels, ip=ip, user_agent=user_agent)
    except Exception as e:                                   # noqa: BLE001
        log.warning("terms: acceptance of a new account was not stored (%s)", type(e).__name__)
        return None


def latest(email: str) -> Optional[dict]:
    rows = records(email)
    return rows[-1] if rows else None


def accepted_version(email: str, minimum: Optional[str] = None) -> bool:
    """Has this account accepted the texts at ``minimum`` or later (default: the current version)?"""
    minimum = minimum or TERMS_VERSION
    return any(version_ge(r.get("terms_version"), minimum) and _complete(r) for r in records(email))


def _complete(r: dict) -> bool:
    """A line of the FREE-mode texts (:data:`MIN_TRAINING_VERSION` or later) counts only with its capacity (agreement section 2.3);
    older lines predate the choice."""
    return not version_ge(r.get("terms_version"), MIN_TRAINING_VERSION) or str(r.get("capacity") or "") in CAPACITIES


def needs_acceptance(email: str, role: Any = "") -> bool:
    """Should the signed-in account be asked to accept the current texts?  No for staff and MOTRES's own accounts (the exemption of the
    platform agreement) and while the switch is off."""
    if not acceptance_required() or not _norm(email):
        return False
    from motor_ai_sim import platform_agreement as PA
    if PA.scope_of(email, role)["exempt"]:
        return False
    return not accepted_version(email)


def required_from() -> Optional[datetime.date]:
    """The owner's "Later" deadline (``TERMS_REQUIRED_FROM``, an ISO date, UTC): from that day on, starting a compute job needs an
    acceptance of the current version.  Unset or not a date = no gate."""
    raw = (os.environ.get("TERMS_REQUIRED_FROM") or "").strip()
    if not raw:
        return None
    try:
        return datetime.date.fromisoformat(raw[:10])
    except ValueError:
        log.warning("terms: TERMS_REQUIRED_FROM=%r is not an ISO date; no gate", raw)
        return None


def gate_active(now: Optional[datetime.datetime] = None) -> bool:
    d = required_from()
    if d is None:
        return False
    return (now or datetime.datetime.now(datetime.timezone.utc)).date() >= d


def gate_detail(email: str, role: Any = "") -> Optional[dict]:
    """None when this account may start a compute job; else the body of the 428 (``code`` ``terms_required``): the deadline has
    passed and the account has no acceptance of the current version.  Staff, MOTRES's own accounts, anonymous or process identities
    and the switch ``TERMS_ACCEPTANCE_DISABLED`` are never gated."""
    email = _norm(email)
    if not email or "@" not in email or not gate_active():
        return None
    if not role:                                # a caller that knows only the e-mail (an agent key, a job owner)
        try:
            from motor_ai_sim import users as U
            role = (U.get_user(email) or {}).get("role") or ""
        except Exception:                                              # noqa: BLE001
            role = ""
    if not needs_acceptance(email, role):
        return None
    return {"code": "terms_required", "version": TERMS_VERSION, "from": required_from().isoformat(),
            "message": "Accept the updated Terms of Use, Platform Agreement and Privacy Policy (version %s) to start new simulations." % TERMS_VERSION}


def require_current(email: str, role: Any = "") -> None:
    """Gate hook: HTTP 428 ``terms_required`` (see :func:`gate_detail`)."""
    detail = gate_detail(email, role)
    if detail:
        from fastapi import HTTPException
        raise HTTPException(status_code=428, detail=detail)


def status_for(email: str, role: Any = "") -> dict:
    """What the web needs after a sign-in (``GET /api/legal/status``)."""
    last = latest(email)
    d = required_from()
    return {"version": TERMS_VERSION, "privacy_version": PRIVACY_VERSION, "agreement_version": AGREEMENT_PUBLIC_VERSION,
            "required": needs_acceptance(email, role), "accepted_version": (last or {}).get("terms_version"),
            "accepted_at": (last or {}).get("iso"), "required_from": d.isoformat() if d else None}


# -- the record as shown (agreement section 15.3, gate R16) ------------------------------------------------------

CAPACITY_LABELS = {"business": "A business the account holder is authorised to bind",
                   "sole_trader": "Itself, as a registered sole trader or self-employed professional",
                   "other": "Neither (no authority to bind an employer or customer)"}
FORUM_LABELS = {"A": "Variant A", "B": "Variant B (Hong Kong law, HKIAC; Mainland China and Hong Kong SAR)"}


def record_view(rec: dict) -> list[tuple[str, str]]:
    """The rows an ACCOUNT acceptance shows (PDF and record view): capacity, business name (only when one was given), country, forum
    variant, the election of section 7.9, version, language, time, source, account, hashes and the record id.  A field with no value is
    left out; the company-agreement rows (company, registration no., title, mode) never appear here."""
    sha = rec.get("text_sha256") if isinstance(rec.get("text_sha256"), dict) else {}
    labels = rec.get("labels") if isinstance(rec.get("labels"), dict) else {}
    cap = str(rec.get("capacity") or "")
    rows = [("Account", rec.get("user")), ("Accepted (UTC)", rec.get("iso")), ("Capacity", CAPACITY_LABELS.get(cap, cap)),
            ("Business name", rec.get("business_name")), ("Country of establishment", rec.get("country")),
            ("Forum (agreement section 13)", FORUM_LABELS.get(str(rec.get("forum_variant") or ""), "")),
            ("Earlier data (section 7.9)", ("Included from the account's creation" if rec.get("historical_opt_in") else "Not included")
             if "historical_opt_in" in rec else ""),
            ("Terms of Use / Privacy / Agreement version", rec.get("terms_version")), ("Interface language", rec.get("lang")),
            ("Route", rec.get("source")), ("IP address", rec.get("ip")), ("Acceptance label shown", labels.get("accept")),
            ("Election label shown", labels.get("election"))]
    rows += [("%s SHA-256" % k.replace("_", " ").capitalize(), sha.get(k)) for k in HASH_KEYS.values()]
    rows.append(("Record ID", rec.get("id")))
    return [(k, str(v)) for k, v in rows if v not in (None, "")]


def record_pdf(rec: dict) -> bytes:
    """A one-page PDF of the acceptance record built from :func:`record_view` (the texts themselves are published and hashed)."""
    import io
    from xml.sax.saxutils import escape
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    rows = record_view(rec)
    font, bold = N._pdf_font(*[v for _, v in rows])
    wrap = {"wordWrap": "CJK"} if font == "STSong-Light" else {}
    meta = ParagraphStyle("meta", fontName=font, fontSize=8.5, leading=11, **wrap)
    head = ParagraphStyle("head", fontName=bold, fontSize=14, leading=18, spaceAfter=8, **wrap)
    tbl = Table([[Paragraph(escape(k), meta), Paragraph(escape(v), meta)] for k, v in rows], colWidths=[55 * mm, 110 * mm])
    tbl.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.grey), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                             ("BACKGROUND", (0, 0), (0, -1), colors.whitesmoke)]))
    note = ("Accepted online by ticking the initially unticked box. The texts shown are published at /terms, /privacy and "
            "/platform-agreement (and in Chinese under /zh-cn/); each SHA-256 above is the hash of the text in the language shown.")
    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
                      title="Account acceptance %s" % rec.get("id", ""), author="MOTRES d.o.o.").build(
        [Paragraph("AeroStator account acceptance", head), tbl, Spacer(1, 6), Paragraph(escape(note), meta)])
    return buf.getvalue()


# -- account erasure / export -------------------------------------------------

def erase(email: str, subj: str) -> int:
    """Account deletion: the acceptance lines are kept as proof, the person goes (e-mail -> pseudonym, IP -> hash).  -> changed lines."""
    email = _norm(email)
    path = acceptances_path()
    if not path.is_file():
        return 0
    from motor_ai_sim.log_redaction import hash_ip
    from motor_ai_sim.private_files import open_private
    changed = 0
    with _LOCK:
        out = []
        for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                r = json.loads(ln)
            except ValueError:
                out.append(ln)
                continue
            if isinstance(r, dict) and _norm(r.get("user")) == email:
                r["user"] = subj
                if r.get("ip"):
                    r["ip"] = hash_ip(str(r["ip"]))
                changed += 1
                ln = json.dumps(r, ensure_ascii=False, sort_keys=True)
            out.append(ln)
        if changed:
            tmp = path.with_suffix(".jsonl.tmp")
            with open_private(tmp, "w") as f:
                f.write("\n".join(out) + "\n")
            os.replace(tmp, path)
    return changed


def export_for(email: str) -> dict:
    """The account's own acceptance lines for the data-export ZIP."""
    return {"current_version": TERMS_VERSION, "acceptances": records(email)}
