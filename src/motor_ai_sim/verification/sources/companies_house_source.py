"""UK Companies House (GB) - the official register of companies in the United Kingdom.

Needs a free API key in the environment: ``COMPANIES_HOUSE_API_KEY`` (https://developer.company-information.service.gov.uk/).
Without it the adapter reports ``not_configured`` and never fails.  The key is the user name of HTTP Basic auth.

* a registration number given: ``GET /company/{number}``; otherwise ``GET /search/companies?q=<legal name>``;
* the name must match, the company must be ``active`` (otherwise a CONFLICT);
* ``GET /company/{number}/officers`` - a current (not resigned) officer with the applicant's name is a STRONG person signal.
"""
from __future__ import annotations

import os
import re
import urllib.parse

from motor_ai_sim.verification import config as C
from motor_ai_sim.verification import evidence as EV
from motor_ai_sim.verification import textutil as T
from motor_ai_sim.verification.evidence import Evidence
from motor_ai_sim.verification.sources.base import Context, VerificationSource

API = "https://api.company-information.service.gov.uk"
PUBLIC = "https://find-and-update.company-information.service.gov.uk/company/%s"


def api_key() -> str:
    return (os.environ.get("COMPANIES_HOUSE_API_KEY") or "").strip()


def pad_number(n: str) -> str:
    """UK company numbers have 8 characters: '445790' -> '00445790'; 'SC123456' stays."""
    t = re.sub(r"\s+", "", str(n or "")).upper()
    return t.zfill(8) if t.isdigit() else t


class CompaniesHouseSource(VerificationSource):
    name = "companies_house"

    def applies(self, country: str, data: dict) -> bool:
        return country == "GB" and bool((data.get("application") or {}).get("legal_name"))

    def run(self, case: dict, ctx: Context) -> list[Evidence]:
        app = case["application"]
        if not api_key():
            return [Evidence("companies_house", EV.CHECK_REGISTRY_COMPANY, EV.INFO, EV.NOT_CONFIGURED, EV.NONE, app.get("legal_name", ""),
                             "COMPANIES_HOUSE_API_KEY is not set: Companies House was not queried", registry_grade=True)]
        key = "ch|%s|%s|%s" % (app.get("legal_name"), app.get("reg_no"), app.get("applicant_name"))
        return ctx.cached(key, lambda: self._run(app, ctx))

    def _get(self, path: str, ctx: Context):
        return ctx.http.get(API + path, auth=(api_key(), ""), headers={"Accept": "application/json"})

    def _run(self, app: dict, ctx: Context) -> list[Evidence]:
        name, reg, person = app["legal_name"], app.get("reg_no") or "", app.get("applicant_name") or ""
        number, company, r = "", None, None
        if reg:
            number = pad_number(reg)
            r = self._get("/company/" + urllib.parse.quote(number, safe=""), ctx)
            if r.ok and isinstance(r.json(), dict):
                company = r.json()
            elif r.status not in (404,):
                return [Evidence("companies_house", EV.CHECK_REGISTRY_COMPANY, EV.COMPANY, EV.UNKNOWN, EV.NONE, name,
                                 "Companies House did not answer (%s)" % (r.error or "HTTP %s" % r.status), source_url=API + "/company/" + number,
                                 registry_grade=True)]
        if company is None:
            s = self._get("/search/companies?q=%s&items_per_page=10" % urllib.parse.quote(name), ctx)
            if not s.ok:
                return [Evidence("companies_house", EV.CHECK_REGISTRY_COMPANY, EV.COMPANY, EV.UNKNOWN, EV.NONE, name,
                                 "Companies House search did not answer (%s)" % (s.error or "HTTP %s" % s.status), registry_grade=True)]
            items = (s.json() or {}).get("items") or []
            scored = sorted(((T.company_similarity(name, i.get("title", "")), i) for i in items), key=lambda x: -x[0])
            if not scored or scored[0][0] < C.COMPANY_NAME_AMBIGUOUS:
                return [Evidence("companies_house", EV.CHECK_REGISTRY_COMPANY, EV.COMPANY, EV.UNKNOWN, EV.NONE, name,
                                 "no company of this name on the register" + (" (and no company with number %s)" % number if number else ""),
                                 registry_grade=True, raw={"items": len(items)})]
            number = scored[0][1].get("company_number", "")
            c = self._get("/company/" + urllib.parse.quote(number, safe=""), ctx)
            if not c.ok or not isinstance(c.json(), dict):
                return [Evidence("companies_house", EV.CHECK_REGISTRY_COMPANY, EV.COMPANY, EV.UNKNOWN, EV.NONE, name,
                                 "Companies House did not return company %s" % number, registry_grade=True)]
            company = c.json()
        legal = company.get("company_name", "")
        status = str(company.get("company_status") or "")
        found = {"company_number": company.get("company_number"), "legal_name": legal, "status": status, "type": company.get("type"),
                 "created": company.get("date_of_creation"), "address": company.get("registered_office_address"),
                 "similarity": round(T.company_similarity(name, legal), 3)}
        link = PUBLIC % company.get("company_number", number)
        raw = {k: company.get(k) for k in ("company_name", "company_number", "company_status", "type", "date_of_creation", "registered_office_address")}
        m = T.company_match(name, legal)
        if m == "different":
            return [Evidence("companies_house", EV.CHECK_REGISTRY_COMPANY, EV.COMPANY, EV.CONFLICT, EV.NONE, name,
                             "company number %s is %s, not the name the applicant gave" % (company.get("company_number"), legal),
                             source_url=link, registry_grade=True, detail=found, raw=raw)]
        if m == "ambiguous":
            return [Evidence("companies_house", EV.CHECK_REGISTRY_COMPANY, EV.COMPANY, EV.UNKNOWN, EV.NONE, name,
                             "a similar but not equal name: %s" % legal, source_url=link, registry_grade=True, detail=found, raw=raw)]
        if status != "active":
            return [Evidence("companies_house", EV.CHECK_REGISTRY_COMPANY, EV.COMPANY, EV.CONFLICT, EV.NONE, name,
                             "the register lists %s as %s" % (legal, status or "not active"), source_url=link, registry_grade=True, detail=found, raw=raw)]
        out = [Evidence("companies_house", EV.CHECK_REGISTRY_COMPANY, EV.COMPANY, EV.PASS, EV.STRONG, name,
                        "Companies House: %s (%s), active" % (legal, company.get("company_number")), source_url=link,
                        registry_grade=True, detail=found, raw=raw)]
        if person:
            o = self._get("/company/%s/officers?items_per_page=100" % urllib.parse.quote(str(company.get("company_number")), safe=""), ctx)
            if o.ok:
                officers = (o.json() or {}).get("items") or []
                hit = [x for x in officers if not x.get("resigned_on") and T.officer_matches(x.get("name", ""), person)]
                out.append(Evidence("companies_house", EV.CHECK_REGISTRY_OFFICER, EV.PERSON, EV.PASS if hit else EV.UNKNOWN,
                                    EV.STRONG if hit else EV.NONE, person,
                                    ("listed as %s on the register" % (hit[0].get("officer_role", "officer"))) if hit else "not among the current officers",
                                    source_url=link + "/officers", detail={"officers_seen": len(officers)},
                                    raw={"matched": [{"name": x.get("name"), "role": x.get("officer_role")} for x in hit]}))
            else:
                out.append(Evidence("companies_house", EV.CHECK_REGISTRY_OFFICER, EV.PERSON, EV.UNKNOWN, EV.NONE, person,
                                    "the officers list could not be read (%s)" % (o.error or "HTTP %s" % o.status), source_url=link + "/officers"))
        return out
