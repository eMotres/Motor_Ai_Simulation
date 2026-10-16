"""EU VIES (VAT Information Exchange System) - the European Commission's free REST service, no key.

``GET https://ec.europa.eu/taxation_customs/vies/rest-api/ms/{CC}/vat/{number}``

A valid VAT number proves that a registered taxable person exists under that number.  Many member states (Germany, Spain,
...) return no name ("---"); then the number is valid but the NAME cannot be matched, and the result is UNKNOWN (a VAT number
of somebody else would otherwise confirm any name).  A returned name must match the claimed legal name to be a registry-grade
confirmation; a different name is a CONFLICT.  Needs the applicant to give a VAT number (EU companies).
"""
from __future__ import annotations

import re

from motor_ai_sim.verification import evidence as EV
from motor_ai_sim.verification import textutil as T
from motor_ai_sim.verification.evidence import Evidence
from motor_ai_sim.verification.sources.base import Context, VerificationSource

API = "https://ec.europa.eu/taxation_customs/vies/rest-api/ms/%s/vat/%s"

#: ISO country -> VIES member-state code (Greece is EL; Northern Ireland XI is given by the number's own prefix)
EU = {"AT": "AT", "BE": "BE", "BG": "BG", "CY": "CY", "CZ": "CZ", "DE": "DE", "DK": "DK", "EE": "EE", "ES": "ES", "FI": "FI", "FR": "FR",
      "GR": "EL", "HR": "HR", "HU": "HU", "IE": "IE", "IT": "IT", "LT": "LT", "LU": "LU", "LV": "LV", "MT": "MT", "NL": "NL", "PL": "PL",
      "PT": "PT", "RO": "RO", "SE": "SE", "SI": "SI", "SK": "SK"}
_VIES_CODES = set(EU.values()) | {"XI"}


def split_vat(vat: str, country: str) -> tuple[str, str]:
    """('DE', '811115368') from 'DE 811.115.368' or from '811115368' + country DE; ('', '') when it cannot be told."""
    t = re.sub(r"[\s.\-/]", "", str(vat or "")).upper()
    m = re.match(r"^([A-Z]{2})(.+)$", t)
    if m and m.group(1) in _VIES_CODES:
        return m.group(1), m.group(2)
    cc = EU.get(str(country or "").upper(), "")
    return (cc, t) if cc and t else ("", "")


class ViesSource(VerificationSource):
    name = "vies"

    def applies(self, country: str, data: dict) -> bool:
        app = data.get("application") or {}
        return bool(app.get("vat_no")) and bool(split_vat(app.get("vat_no"), country)[0])

    def run(self, case: dict, ctx: Context) -> list[Evidence]:
        app = case["application"]
        cc, num = split_vat(app.get("vat_no"), app.get("country"))
        name = app.get("legal_name") or ""
        claimed = EU.get(str(app.get("country") or "").upper(), "")
        url = API % (cc, num)

        def go() -> list[Evidence]:
            r = ctx.http.get(url, headers={"Accept": "application/json"})
            j = r.json() if r.ok else None
            if not isinstance(j, dict):
                return [Evidence("vies", EV.CHECK_REGISTRY_COMPANY, EV.COMPANY, EV.UNKNOWN, EV.NONE, num,
                                 "VIES did not answer (%s)" % (r.error or "HTTP %s" % r.status), source_url=url, registry_grade=True)]
            raw = {k: j.get(k) for k in ("isValid", "userError", "name", "address", "requestDate", "vatNumber")}
            if claimed and cc not in (claimed, "XI") and not (claimed == "GB"):
                return [Evidence("vies", EV.CHECK_REGISTRY_COMPANY, EV.COMPANY, EV.CONFLICT, EV.NONE, num,
                                 "the VAT number belongs to %s, the applicant's company country is %s" % (cc, claimed), source_url=url,
                                 registry_grade=True, raw=raw)]
            if j.get("isValid") is not True:
                if str(j.get("userError") or "").upper() in ("INVALID", "INVALID_INPUT"):
                    return [Evidence("vies", EV.CHECK_REGISTRY_COMPANY, EV.COMPANY, EV.FAIL, EV.NONE, num, "VIES: the VAT number is not valid",
                                     source_url=url, registry_grade=True, raw=raw)]
                return [Evidence("vies", EV.CHECK_REGISTRY_COMPANY, EV.COMPANY, EV.UNKNOWN, EV.NONE, num,
                                 "VIES could not validate (%s)" % j.get("userError"), source_url=url, registry_grade=True, raw=raw)]
            vname = str(j.get("name") or "").strip()
            if not vname or set(vname) <= {"-"}:
                return [Evidence("vies", EV.CHECK_REGISTRY_COMPANY, EV.COMPANY, EV.UNKNOWN, EV.NONE, num,
                                 "the VAT number is valid, but this member state does not disclose the company name, so it is not matched",
                                 source_url=url, registry_grade=True, detail={"valid": True, "name_disclosed": False}, raw=raw)]
            m = T.company_match(name, vname)
            if m == "match":
                return [Evidence("vies", EV.CHECK_REGISTRY_COMPANY, EV.COMPANY, EV.PASS, EV.STRONG, num, "VIES: %s, VAT number valid" % vname,
                                 source_url=url, registry_grade=True, detail={"valid": True, "legal_name": vname}, raw=raw)]
            return [Evidence("vies", EV.CHECK_REGISTRY_COMPANY, EV.COMPANY, EV.CONFLICT if m == "different" else EV.UNKNOWN, EV.NONE, num,
                             "the VAT number is registered to %s" % vname, source_url=url, registry_grade=True,
                             detail={"valid": True, "legal_name": vname}, raw=raw)]

        return ctx.cached("vies|%s|%s|%s" % (cc, num, name), go)
