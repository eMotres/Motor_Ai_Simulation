"""GLEIF (Global Legal Entity Identifier Foundation) public API - free, global, no key.

https://api.gleif.org/api/v1/lei-records?filter[entity.legalName]=...   (exact legal name; a fulltext query is the fallback)

A company with a Legal Entity Identifier is in a regulated register: name, legal address, jurisdiction, registration authority and
registration number were validated by the issuing organisation.  Many small companies have no LEI: "not found" is UNKNOWN, never
a failure.  A found, ACTIVE entity with the claimed name and country is a registry-grade confirmation; a name that matches while
the country or the registration number differs is a CONFLICT (a human looks); an entity that is not ACTIVE is a CONFLICT too.
"""
from __future__ import annotations

import urllib.parse

from motor_ai_sim.verification import config as C
from motor_ai_sim.verification import evidence as EV
from motor_ai_sim.verification import textutil as T
from motor_ai_sim.verification.evidence import Evidence
from motor_ai_sim.verification.sources.base import Context, VerificationSource

API = "https://api.gleif.org/api/v1/lei-records"


def _q(params: dict) -> str:
    return API + "?" + urllib.parse.urlencode(params, safe="[]")


class GleifSource(VerificationSource):
    name = "gleif"

    def applies(self, country: str, data: dict) -> bool:
        return bool((data.get("application") or {}).get("legal_name"))

    def run(self, case: dict, ctx: Context) -> list[Evidence]:
        app = case["application"]
        key = "gleif|%s|%s|%s" % (app.get("legal_name"), app.get("country"), app.get("reg_no"))
        return ctx.cached(key, lambda: self._run(app, ctx))

    def _fetch(self, params: dict, ctx: Context):
        url = _q(params)
        r = ctx.http.get(url, headers={"Accept": "application/vnd.api+json"})
        if r.error or not r.ok:
            return url, None, r
        j = r.json()
        return url, (j.get("data") if isinstance(j, dict) else None) or [], r

    def _run(self, app: dict, ctx: Context) -> list[Evidence]:
        name, cc, reg = app["legal_name"], str(app.get("country") or "").upper(), app.get("reg_no") or ""
        attempts = [{"filter[entity.legalName]": name, "page[size]": "10"},
                    {"filter[fulltext]": name, "filter[entity.legalAddress.country]": cc, "page[size]": "10"}]
        url, records, last = "", None, None
        for params in attempts:
            url, records, last = self._fetch(params, ctx)
            if records is None:
                return [Evidence("gleif", EV.CHECK_REGISTRY_COMPANY, EV.COMPANY, EV.UNKNOWN, EV.NONE, name,
                                 "GLEIF did not answer (%s)" % (last.error or "HTTP %s" % last.status), source_url=url,
                                 registry_grade=True)]
            scored = sorted(((T.company_similarity(name, (r.get("attributes", {}).get("entity", {}).get("legalName") or {}).get("name", "")), r)
                             for r in records), key=lambda x: -x[0])
            if scored and scored[0][0] >= C.COMPANY_NAME_AMBIGUOUS:
                break
        else:
            scored = []
        if not scored or scored[0][0] < C.COMPANY_NAME_AMBIGUOUS:
            return [Evidence("gleif", EV.CHECK_REGISTRY_COMPANY, EV.COMPANY, EV.UNKNOWN, EV.NONE, name,
                             "no LEI record with this name (many companies have none)", source_url=url, registry_grade=True,
                             raw={"records": len(records or [])})]
        score, rec = scored[0]
        ent = rec.get("attributes", {}).get("entity", {})
        legal = (ent.get("legalName") or {}).get("name", "")
        addr = ent.get("legalAddress") or {}
        found = {"lei": rec.get("id"), "legal_name": legal, "status": ent.get("status"), "country": addr.get("country"),
                 "jurisdiction": ent.get("jurisdiction"), "city": addr.get("city"), "address": addr.get("addressLines"),
                 "registered_at": (ent.get("registeredAt") or {}).get("id"), "registered_as": ent.get("registeredAs"), "similarity": round(score, 3)}
        raw = {"entity": {k: ent.get(k) for k in ("legalName", "legalAddress", "registeredAt", "registeredAs", "jurisdiction", "status", "category")},
               "lei": rec.get("id")}
        link = "https://search.gleif.org/#/record/%s" % rec.get("id")
        if score < C.COMPANY_NAME_MATCH:
            return [Evidence("gleif", EV.CHECK_REGISTRY_COMPANY, EV.COMPANY, EV.UNKNOWN, EV.NONE, name,
                             "an LEI record with a similar but not equal name: %s" % legal, source_url=link, registry_grade=True, detail=found, raw=raw)]
        countries = {str(addr.get("country") or "").upper(), str(ent.get("jurisdiction") or "").upper()[:2]} - {""}
        if cc and countries and cc not in countries:
            return [Evidence("gleif", EV.CHECK_REGISTRY_COMPANY, EV.COMPANY, EV.CONFLICT, EV.NONE, name,
                             "the name matches %s but the register says %s, the applicant said %s" % (legal, "/".join(sorted(countries)), cc),
                             source_url=link, registry_grade=True, detail=found, raw=raw)]
        if str(ent.get("status") or "").upper() != "ACTIVE":
            return [Evidence("gleif", EV.CHECK_REGISTRY_COMPANY, EV.COMPANY, EV.CONFLICT, EV.NONE, name,
                             "the register lists %s as %s" % (legal, ent.get("status")), source_url=link, registry_grade=True, detail=found, raw=raw)]
        have = ent.get("registeredAs") or ""
        if reg and have and not T.reg_no_equal(reg, have):
            return [Evidence("gleif", EV.CHECK_REGISTRY_COMPANY, EV.COMPANY, EV.CONFLICT, EV.NONE, name,
                             "the name matches but the registration number differs (register %s, applicant %s)" % (have, reg),
                             source_url=link, registry_grade=True, detail=found, raw=raw)]
        found["reg_no_match"] = bool(reg and have)
        return [Evidence("gleif", EV.CHECK_REGISTRY_COMPANY, EV.COMPANY, EV.PASS, EV.STRONG, name,
                         "GLEIF: %s, %s, %s%s" % (legal, ent.get("status"), addr.get("country"), ", registration no. matches" if found["reg_no_match"] else ""),
                         source_url=link, registry_grade=True, detail=found, raw=raw)]

