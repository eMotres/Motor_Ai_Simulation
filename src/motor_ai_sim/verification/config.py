"""Every threshold and list of the applicant verification, in ONE file (docs/APPLICANT_VERIFICATION.md).

Change a number here, never inside the engine.  Nothing in this file reads the environment except the optional list file
override at the bottom.
"""
from __future__ import annotations

import os

# -- decision thresholds ---------------------------------------------------------------------------------------------
#: independent person-to-company signals needed for VERIFIED, and how many of them must be strong
MIN_PERSON_SIGNALS = 2
MIN_STRONG_PERSON_SIGNALS = 1
#: sources whose "company exists" answer counts as registry-grade (the official register or a regulated public registry)
REGISTRY_GRADE_SOURCES = frozenset({"gleif", "companies_house", "vies"})
#: a verified e-mail on the official website's domain is a STRONG signal only when the website is tied to the company:
#: it names the company AND (the domain resembles the company name OR the website shows the registration / VAT number).
#: False = the name on the website is enough (weaker: anybody can write a company name on a page).
REQUIRE_DOMAIN_COMPANY_LINK = True
#: applicant replies (a reply that still leaves the case open) before the case goes to a human
MAX_OPEN_REPLIES = 3
#: a company name matches a registry name at or above this similarity (after removing legal forms, accents, punctuation)
COMPANY_NAME_MATCH = 0.92
#: below COMPANY_NAME_MATCH but at or above this = "ambiguous" (recorded, never confirmed)
COMPANY_NAME_AMBIGUOUS = 0.75

#: job titles that make the authority plausible (substring, case-insensitive); anything else is asked about once
PLAUSIBLE_ROLE_WORDS = (
    "engineer", "r&d", "research", "develop", "design", "cto", "ceo", "coo", "cfo", "founder", "director", "manager", "head",
    "chief", "president", "owner", "partner", "procure", "purchas", "buyer", "sourcing", "supply", "business development",
    "bd ", "sales", "technical", "technician", "scientist", "professor", "lecturer", "student", "phd", "architect", "lead",
    "product", "project", "operations", "managing", "principal", "specialist", "analyst", "consultant", "integrator",
    "工程师", "研发", "总监", "经理", "总经理", "创始人", "董事", "采购", "技术", "负责人", "主管", "教授", "研究",
)

# -- network ---------------------------------------------------------------------------------------------------------
USER_AGENT = "AeroStatorVerification/1.0 (+https://aerostator.com; respects robots.txt; contact support@aerostator.com)"
HTTP_TIMEOUT_S = 6.0
FETCH_MAX_BYTES = 400_000
FETCH_MAX_REDIRECTS = 3
#: pages of the official website searched for the company name and the applicant's name (homepage first); the budget counts requests, answered or not
SITE_PAGES = ("/", "/about", "/team", "/impressum", "/imprint", "/contact", "/company", "/management", "/about-us", "/en/about")
SITE_MAX_PAGES = 7
#: seconds a result of one (source, input) is reused inside a case (re-running after a reply does not hammer the registries)
CACHE_S = 6 * 3600

# -- e-mail one-time codes and the reply page -------------------------------------------------------------------------
OTP_TTL_S = 30 * 60
OTP_MAX_ATTEMPTS = 5
OTP_MAX_SENDS_PER_CASE_DAY = 6
OTP_RESEND_COOLDOWN_S = 120
REPLY_TOKEN_TTL_S = 30 * 24 * 3600
UPLOAD_MAX_BYTES = 5 * 1024 * 1024
UPLOAD_EXTENSIONS = {"pdf": b"%PDF", "png": b"\x89PNG\r\n\x1a\n", "jpg": b"\xff\xd8\xff", "jpeg": b"\xff\xd8\xff"}
MAX_URLS_PER_REPLY = 5
MAX_NOTE_CHARS = 2000
MAX_DOCUMENTS_PER_CASE = 5

# -- reminders and escalation (sweep) --------------------------------------------------------------------------------
REMINDER_AFTER_DAYS = 3
ESCALATE_AFTER_DAYS = 7

# -- lists -----------------------------------------------------------------------------------------------------------
#: throw-away mailbox providers (never a company); extend with a file, one domain per line: VERIFY_DISPOSABLE_FILE
DISPOSABLE_DOMAINS = frozenset("""mailinator.com guerrillamail.com guerrillamail.net guerrillamail.org sharklasers.com grr.la
10minutemail.com 10minutemail.net tempmail.com temp-mail.org temp-mail.io tempmailo.com tempr.email yopmail.com yopmail.fr
trashmail.com trashmail.net getnada.com nada.email throwawaymail.com maildrop.cc dispostable.com fakeinbox.com mohmal.com
emailondeck.com moakt.com mintemail.com spamgourmet.com mailnesia.com discard.email burnermail.io mailcatch.com
mytemp.email tmpmail.org tmpmail.net fakemail.net mail-temporary.com 1secmail.com 1secmail.net 1secmail.org
linshiyouxiang.net 027168.com zzrgg.com""".split())


def _extra_lines(env: str) -> frozenset:
    p = (os.environ.get(env) or "").strip()
    if not p:
        return frozenset()
    try:
        with open(p, encoding="utf-8") as fh:
            return frozenset(ln.strip().lower() for ln in fh if ln.strip() and not ln.startswith("#"))
    except OSError:
        return frozenset()


def disposable_domains() -> frozenset:
    return DISPOSABLE_DOMAINS | _extra_lines("VERIFY_DISPOSABLE_FILE")
