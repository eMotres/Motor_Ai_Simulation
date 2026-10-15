"""The decision engine: PURE (evidence in, decision out; no I/O, no clock, no randomness), so every rule is a table row in a test.

Statuses
    VERIFIED                  company confirmed by a registry-grade source AND the person linked to it by >= MIN_PERSON_SIGNALS
                              independent signals, >= MIN_STRONG_PERSON_SIGNALS of them strong (config.py)
    MORE_INFORMATION_REQUIRED the company is confirmed but the person link is weak, or a fixable gap exists (free mailbox, domain
                              mismatch, no website, no registration / VAT number); the applicant is asked for the SPECIFIC proof
    MANUAL_REVIEW             conflict, unidentifiable company, no registry exists (China), repeated failure, an adapter error,
                              an unclear role: a person decides
    (PENDING and DENIED are set elsewhere: PENDING while checks run, DENIED only by an administrator)

When uncertain: never automatic access, never an automatic rejection.

Signals (a person-to-company link), counted ONCE per family (a family repeated by two sources is one signal):
    strong       registry officer | applicant named on the official website (a website tied to the company) | a verified e-mail on the
                 official website's domain (a website tied to the company)
    supporting   a LinkedIn profile address carrying the name | a third-party page naming person and company | the same
                 website / e-mail facts when the website is NOT tied to the company
A website is "tied to the company" when it resolves, names the company, and (the domain resembles the company name OR the website
shows the registration / VAT number) - config.REQUIRE_DOMAIN_COMPANY_LINK.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Iterable, Optional

from motor_ai_sim.verification import config as C
from motor_ai_sim.verification import evidence as EV
from motor_ai_sim.verification import textutil as T
from motor_ai_sim.verification.evidence import Evidence

PENDING = "PENDING"
VERIFIED = "VERIFIED"
MORE_INFORMATION_REQUIRED = "MORE_INFORMATION_REQUIRED"
MANUAL_REVIEW = "MANUAL_REVIEW"
DENIED = "DENIED"
STATUSES = (PENDING, VERIFIED, MORE_INFORMATION_REQUIRED, MANUAL_REVIEW, DENIED)

#: request codes the applicant can be asked for (texts in verification/texts.py)
REQ_CORPORATE_EMAIL = "corporate_email"
REQ_PERSON_PAGE = "person_page"
REQ_WEBSITE = "website"
REQ_REG_NO = "registration_number"
REQ_VAT = "vat_number"
REQ_DOMAIN_MISMATCH = "domain_mismatch"
REQ_ROLE = "role_clarification"
REQ_LICENCE = "business_licence"
REQ_ACCOUNT_EMAIL = "verify_account_email"
REQ_FREE_TEXT = "free_text"                      # an administrator's own wording


@dataclass
class Decision:
    status: str
    reasons: list = field(default_factory=list)        # [{code, text}]
    requests: list = field(default_factory=list)       # [{code, params}]
    signals: list = field(default_factory=list)        # the person signals counted
    company: dict = field(default_factory=dict)        # {confirmed, by, conflicts}
    facts: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _reason(code: str, text: str) -> dict:
    return {"code": code, "text": text}


def _req(code: str, **params) -> dict:
    return {"code": code, "params": params}


def _coerce(evidence: Iterable) -> list[Evidence]:
    return [e if isinstance(e, Evidence) else EV.from_dict(e) for e in evidence]


_RANK = {EV.STRONG: 2, EV.SUPPORTING: 1, EV.NONE: 0}


def derive(application: dict, ev: list[Evidence]) -> dict:
    """Facts and person signals from the evidence (the only place where evidence is interpreted)."""
    legal = application.get("legal_name") or ""
    site = T.normalize_website(application.get("website") or "")
    site_dom = T.registrable_domain(site) if site else ""
    passed = lambda check: [e for e in ev if e.check == check and e.result == EV.PASS]
    names_company = bool(passed(EV.CHECK_SITE_NAMES_COMPANY))
    shows_reg = bool(passed(EV.CHECK_SITE_SHOWS_REG))
    resolves = bool(passed(EV.CHECK_SITE_RESOLVES))
    resembles = bool(site_dom) and T.domain_resembles_company(site_dom, legal)
    tied = names_company and (not C.REQUIRE_DOMAIN_COMPANY_LINK or resembles or shows_reg)
    site_confirmed = resolves and tied

    verified = {e.value for e in ev if e.check == EV.CHECK_EMAIL_VERIFIED and e.result == EV.PASS}
    corporate = {e.value for e in ev if e.check == EV.CHECK_EMAIL_CORPORATE and e.result == EV.PASS}
    matching = {e.value for e in ev if e.check == EV.CHECK_DOMAIN_MATCH and e.result == EV.PASS}
    mismatching = {e.value for e in ev if e.check == EV.CHECK_DOMAIN_MATCH and e.result == EV.FAIL}

    cands: list[dict] = []

    def add(family: str, strength: str, e: Optional[Evidence], summary: str, value: str = "") -> None:
        cands.append({"family": family, "strength": strength, "check": e.check if e else "", "value": value or (e.value if e else ""),
                      "source_url": e.source_url if e else "", "summary": summary})

    for a in sorted(verified & corporate & matching):
        add("email", EV.STRONG if site_confirmed else EV.SUPPORTING, None,
            "verified e-mail %s is on the official website's domain%s" % (a, "" if site_confirmed else " (the website is not tied to the company)"), a)
    for e in passed(EV.CHECK_REGISTRY_OFFICER):
        add("registry_officer", EV.STRONG, e, e.summary)
    for e in passed(EV.CHECK_SITE_LISTS_PERSON) + passed(EV.CHECK_URL_SITE_PAGE):
        add("site", EV.STRONG if site_confirmed else EV.SUPPORTING, e,
            e.summary + ("" if site_confirmed else " (the website is not tied to the company)"))
    for e in passed(EV.CHECK_URL_THIRD_PARTY):
        add("third_party", EV.SUPPORTING, e, e.summary)
    for e in passed(EV.CHECK_LINKEDIN):
        add("linkedin", EV.SUPPORTING, e, e.summary)
    known = {EV.CHECK_REGISTRY_OFFICER, EV.CHECK_SITE_LISTS_PERSON, EV.CHECK_URL_SITE_PAGE, EV.CHECK_URL_THIRD_PARTY, EV.CHECK_LINKEDIN}
    for e in ev:                                          # a signal from a source added later: its own family, its own strength
        if e.kind == EV.PERSON and e.result == EV.PASS and e.check not in known and e.strength in (EV.STRONG, EV.SUPPORTING):
            add(e.source + ":" + e.check, e.strength, e, e.summary)

    best: dict[str, dict] = {}
    for c in cands:
        if c["family"] not in best or _RANK[c["strength"]] > _RANK[best[c["family"]]["strength"]]:
            best[c["family"]] = c
    signals = sorted(best.values(), key=lambda c: -_RANK[c["strength"]])
    return dict(site=site, site_domain=site_dom, site_resolves=resolves, site_names_company=names_company, site_shows_registration=shows_reg,
                domain_resembles_company=resembles, site_confirmed=site_confirmed, verified_addresses=sorted(verified),
                corporate_verified=sorted(verified & corporate), domain_mismatch=sorted((verified & corporate) & mismatching),
                free_only=bool(verified) and not (verified & corporate), signals=signals,
                strong=sum(1 for s in signals if s["strength"] == EV.STRONG), total=len(signals))


def decide(application: dict, evidence: Iterable, prior: Optional[dict] = None) -> Decision:
    """``prior`` = {"open_replies": n, "requested": [codes asked before], "reply_since_request": bool}."""
    prior = prior or {}
    asked = set(prior.get("requested") or [])
    ev = _coerce(evidence)
    f = derive(application, ev)
    country = str(application.get("country") or "").upper()

    errors = [e for e in ev if e.check == EV.CHECK_SOURCE_ERROR]
    conflicts = [e for e in ev if e.result == EV.CONFLICT]
    confirmed = [e for e in ev if e.kind == EV.COMPANY and e.check == EV.CHECK_REGISTRY_COMPANY and e.result == EV.PASS and e.registry_grade
                 and e.source in C.REGISTRY_GRADE_SOURCES]
    manual = [e for e in ev if e.result == EV.MANUAL]
    company = {"confirmed": bool(confirmed), "by": sorted({e.source for e in confirmed}),
               "conflicts": [e.summary for e in conflicts]}
    facts = {k: v for k, v in f.items() if k != "signals"}
    facts.update(role_plausible=T.role_plausible(application.get("title") or ""))

    def done(status, reasons, requests=()):
        return Decision(status, list(reasons), list(requests), f["signals"], company, facts)

    # 1. anything that makes the machine unsure goes to a person --------------------------------------------------------
    if errors:
        return done(MANUAL_REVIEW, [_reason("exception", "a check failed with an error: " + "; ".join("%s (%s)" % (e.source, e.summary) for e in errors))])
    if conflicts:
        return done(MANUAL_REVIEW, [_reason("conflict", "a source contradicts the application: " + "; ".join(e.summary for e in conflicts))])
    if int(prior.get("open_replies") or 0) >= C.MAX_OPEN_REPLIES:
        return done(MANUAL_REVIEW, [_reason("repeated_failure", "the applicant replied %d times and the case is still open" % int(prior["open_replies"]))])

    # 2. gaps the applicant can fix ----------------------------------------------------------------------------------
    reqs: list[dict] = []
    reasons: list[dict] = []
    if not f["site"]:
        reqs.append(_req(REQ_WEBSITE))
        reasons.append(_reason("no_website", "no official website was given"))
    elif not f["site_resolves"]:
        reqs.append(_req(REQ_WEBSITE, unreachable=True))
        reasons.append(_reason("website_unreadable", "the official website could not be read"))
    if f["site_resolves"] and not f["site_confirmed"]:
        reasons.append(_reason("website_not_tied", "the website does not name the company, so it is not treated as the company's own"
                               if not f["site_names_company"] else
                               "the website names the company, but neither the domain resembles the company name nor the registration number is shown"))
    if not f["corporate_verified"]:
        if f["free_only"]:
            reasons.append(_reason("free_mail", "the verified e-mail address is a free or disposable mailbox"))
        elif not f["verified_addresses"]:
            reqs.append(_req(REQ_ACCOUNT_EMAIL))
            reasons.append(_reason("email_unverified", "no e-mail address of the applicant is verified"))
        reqs.append(_req(REQ_CORPORATE_EMAIL, domain=f["site_domain"]))
    elif f["domain_mismatch"] and not any(s["family"] == "email" for s in f["signals"]):
        reqs.append(_req(REQ_DOMAIN_MISMATCH, addresses=f["domain_mismatch"], domain=f["site_domain"]))
        reasons.append(_reason("domain_mismatch", "the verified corporate e-mail is not on the official website's domain"))

    role_ok = facts["role_plausible"]
    role_block = False
    if not role_ok:
        if REQ_ROLE in asked and prior.get("reply_since_request"):
            return done(MANUAL_REVIEW, [_reason("role_unclear", "the job title %r is not a usual role for this request and the applicant's reply did not settle it"
                                                % application.get("title"))])
        role_block = True
        if REQ_ROLE not in asked:
            reqs.append(_req(REQ_ROLE, title=application.get("title") or ""))
        reasons.append(_reason("role_implausible", "the job title %r needs a short explanation" % application.get("title")))

    # 3. the company -------------------------------------------------------------------------------------------------
    if not confirmed:
        if manual:
            lic = [] if REQ_LICENCE in asked else [_req(REQ_LICENCE)]
            return done(MANUAL_REVIEW, [_reason("no_registry", "no automatic registry exists for this country: " + "; ".join(e.summary for e in manual))], lic)
        company_reqs = []
        if not f["site"]:
            company_reqs.append(_req(REQ_WEBSITE))
        if not application.get("reg_no") and REQ_REG_NO not in asked:
            company_reqs.append(_req(REQ_REG_NO))
        if not application.get("vat_no") and country in _EU and REQ_VAT not in asked:
            company_reqs.append(_req(REQ_VAT))
        if company_reqs and not prior.get("reply_since_request"):
            codes = {r["code"] for r in company_reqs}
            return done(MORE_INFORMATION_REQUIRED,
                        [_reason("company_not_confirmed", "no registry-grade source confirmed the company yet; more details can settle it")] + reasons,
                        company_reqs + [r for r in reqs if r["code"] not in codes])
        return done(MANUAL_REVIEW, [_reason("company_unidentifiable", "no registry-grade source (official register, GLEIF, VIES) confirmed the company; "
                                           "the website alone is not proof")])

    # 4. company confirmed: is the person linked to it? --------------------------------------------------------------
    enough = f["total"] >= C.MIN_PERSON_SIGNALS and f["strong"] >= C.MIN_STRONG_PERSON_SIGNALS
    if enough and not role_block:
        return done(VERIFIED, [_reason("verified", "company confirmed by %s; person linked by %d independent signals (%d strong)"
                                       % (", ".join(company["by"]), f["total"], f["strong"]))])
    if enough:                                             # only the role explanation is outstanding
        return done(MORE_INFORMATION_REQUIRED, reasons, reqs)
    if not enough:
        if f["total"] == 0:
            reasons.append(_reason("person_unlinked", "the applicant is not linked to the company by any signal yet"))
        elif f["strong"] < C.MIN_STRONG_PERSON_SIGNALS:
            reasons.append(_reason("person_weak", "the person link rests on supporting signals only (%d)" % f["total"]))
        else:
            reasons.append(_reason("person_one_signal", "only %d independent signal(s); %d are needed" % (f["total"], C.MIN_PERSON_SIGNALS)))
        if not any(r["code"] in (REQ_PERSON_PAGE,) for r in reqs):
            reqs.append(_req(REQ_PERSON_PAGE))
    if not reqs:                                           # confirmed, nothing askable, still not enough: a person decides
        return done(MANUAL_REVIEW, reasons + [_reason("nothing_to_ask", "the checks cannot be improved automatically")])
    return done(MORE_INFORMATION_REQUIRED, reasons, reqs)


#: EU member states (ISO) - where a VAT number can be asked for
_EU = frozenset("AT BE BG CY CZ DE DK EE ES FI FR GR HR HU IE IT LT LU LV MT NL PL PT RO SE SI SK".split())
