"""Company matching and deduplication logic.

Contract: BRIEF_companies_people_catalog.md
- Match existing companies by reg_no / VAT / normalized name / website domain; never duplicate.
- Free-mail domains (gmail, qq, 163, ...) never auto-match; reuse the verification free-mail list.
"""
from __future__ import annotations

import re
from typing import Optional

from motor_ai_sim import nda
from motor_ai_sim.verification import textutil as TU


def is_free_mail(domain_or_email: str) -> bool:
    """Return True if the domain or email belongs to a consumer/free webmail provider."""
    val = str(domain_or_email or "").strip().lower()
    if "@" in val:
        return nda.free_mail(val)
    # Check domain directly with nda's free mail list
    return nda.free_mail("user@" + val)


def clean_domain(url_or_domain: str) -> str:
    """Extract clean registrable domain (eTLD+1) e.g. 'https://www.siemens.com/path' -> 'siemens.com'."""
    val = str(url_or_domain or "").strip().lower()
    if not val:
        return ""
    if "@" in val:
        val = val.rsplit("@", 1)[-1]
    return TU.registrable_domain(TU.host_of(val))


def clean_vat_no(vat: str) -> str:
    """Clean VAT number: uppercase alphanumeric only."""
    return re.sub(r"[^A-Za-z0-9]", "", str(vat or "")).upper()


def clean_reg_no(reg: str) -> str:
    return TU.norm_reg_no(reg)


def same_country(c1: str, c2: str) -> bool:
    s1, s2 = str(c1 or "").strip().upper(), str(c2 or "").strip().upper()
    return not s1 or not s2 or s1 == s2


def find_matching_company(
    companies: list[dict],
    *,
    reg_no: str = "",
    vat_no: str = "",
    legal_name: str = "",
    local_name: str = "",
    domain: str = "",
    website: str = "",
    country: str = "",
) -> Optional[dict]:
    """Find an existing company that matches any of the identifiers, in priority order:

    1. reg_no (non-empty & equal per reg_no_equal, respecting country)
    2. vat_no (non-empty & equal, respecting country)
    3. normalized legal_name / local_name (company_match == "match" or similarity >= 0.90)
    4. website domain or explicit domain (non-empty & not free-mail)
    """
    cleaned_reg = clean_reg_no(reg_no)
    cleaned_vat = clean_vat_no(vat_no)
    norm_dom = clean_domain(domain or website)
    if is_free_mail(norm_dom):
        norm_dom = ""

    # 1. Match by registration number
    if cleaned_reg:
        for c in companies:
            c_reg = clean_reg_no(c.get("reg_no") or "")
            if c_reg and TU.reg_no_equal(c_reg, cleaned_reg):
                if same_country(c.get("country"), country):
                    return c

    # 2. Match by VAT number
    if cleaned_vat:
        for c in companies:
            c_vat = clean_vat_no(c.get("vat_no") or "")
            if c_vat and c_vat == cleaned_vat:
                if same_country(c.get("country"), country):
                    return c

    # 3. Match by normalized name
    if legal_name or local_name:
        for c in companies:
            c_legal = c.get("legal_name") or ""
            c_local = c.get("local_name") or ""
            # Check local_name exact or high similarity if CJK
            if local_name and c_local and local_name.strip() == c_local.strip():
                return c
            # Check legal_name
            if legal_name and c_legal:
                if same_country(c.get("country"), country):
                    if TU.company_similarity(c_legal, legal_name) >= 0.90:
                        return c
                    if TU.company_match(c_legal, legal_name) == "match":
                        return c

    # 4. Match by corporate website domain (never free-mail)
    if norm_dom:
        for c in companies:
            # Check company domains list
            c_domains = [clean_domain(d) for d in (c.get("domains") or [])]
            # Also check company website
            if c.get("website"):
                c_web_dom = clean_domain(c["website"])
                if c_web_dom and not is_free_mail(c_web_dom):
                    c_domains.append(c_web_dom)
            if any(d and d == norm_dom for d in c_domains):
                return c

    return None
