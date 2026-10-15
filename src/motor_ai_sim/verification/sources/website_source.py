"""The official website: does it resolve, does it name the company (and show its registration / VAT number), is the applicant
listed on it.  Polite: robots.txt is respected, a short timeout and a size cap per page, at most SITE_MAX_PAGES pages, only
public addresses (net.check_public_url)."""
from __future__ import annotations

import urllib.parse

from motor_ai_sim.verification import config as C
from motor_ai_sim.verification import evidence as EV
from motor_ai_sim.verification import net as NET
from motor_ai_sim.verification import textutil as T
from motor_ai_sim.verification.evidence import Evidence
from motor_ai_sim.verification.sources.base import Context, VerificationSource


def company_in_text(legal_name: str, text: str) -> bool:
    """The legal name (legal form ignored) appears in the page as a phrase."""
    toks = T.company_tokens(legal_name)
    if not toks:
        return False
    hay = " " + T.fold(text) + " "
    return (" " + " ".join(toks) + " ") in hay


def reg_in_text(reg_no: str, text: str) -> bool:
    n = T.norm_reg_no(reg_no)
    if len(n) < 5:
        return False
    return n in T.norm_reg_no(text)


class WebsiteSource(VerificationSource):
    name = "website"

    def applies(self, country: str, data: dict) -> bool:
        return bool(T.normalize_website((data.get("application") or {}).get("website") or ""))

    def run(self, case: dict, ctx: Context) -> list[Evidence]:
        app = case.get("application") or {}
        site = T.normalize_website(app.get("website") or "")
        return ctx.cached("website|%s|%s|%s|%s|%s" % (site, app.get("legal_name"), app.get("reg_no"), app.get("vat_no"), app.get("applicant_name")),
                          lambda: self._run(app, site, ctx))

    def _run(self, app: dict, site: str, ctx: Context) -> list[Evidence]:
        u = urllib.parse.urlsplit(site)
        base = "%s://%s" % (u.scheme, u.netloc)
        legal, person, reg, vat = app.get("legal_name") or "", app.get("applicant_name") or "", app.get("reg_no") or "", app.get("vat_no") or ""
        pages: list[tuple[str, str]] = []
        first_err = ""
        robots: dict = {}
        for i, path in enumerate(C.SITE_PAGES[: C.SITE_MAX_PAGES]):              # a budget of requests, whether they succeed or not
            url = site if path == "/" and u.path not in ("", "/") else base + path
            p = NET.fetch_page(url, ctx.http, robots)
            if p.ok and p.text:
                pages.append((p.url, p.text))
            elif i == 0:
                first_err = p.error or "no content"
                if p.error in ("robots",) or p.error.startswith("blocked:") or p.status == 0:
                    break                                             # nothing to read here
            if pages and any(company_in_text(legal, t) for _u, t in pages) and (not person or any(T.name_in_text(person, t) for _u, t in pages))                     and (not (reg or vat) or any(reg_in_text(reg, t) or reg_in_text(vat, t) for _u, t in pages)):
                break                                                 # everything we look for is found: stop reading
        out: list[Evidence] = []
        if not pages:
            out.append(Evidence("website", EV.CHECK_SITE_RESOLVES, EV.DOMAIN, EV.UNKNOWN if first_err in ("robots",) or first_err.startswith("blocked:") else EV.FAIL,
                                EV.NONE, site, "the website could not be read (%s)" % first_err, source_url=site, detail={"error": first_err}))
            return out
        out.append(Evidence("website", EV.CHECK_SITE_RESOLVES, EV.DOMAIN, EV.PASS, EV.NONE, site, "the website answers (%d page(s) read)" % len(pages),
                            source_url=site, detail={"pages": [p for p, _t in pages]}))
        names = [(pu, t) for pu, t in pages if company_in_text(legal, t)]
        out.append(Evidence("website", EV.CHECK_SITE_NAMES_COMPANY, EV.COMPANY, EV.PASS if names else EV.UNKNOWN, EV.NONE, legal,
                            "the website names the company" if names else "the company name was not found on the pages read",
                            source_url=names[0][0] if names else site, detail={"pages": [pu for pu, _t in names]}))
        shows = [pu for pu, t in pages if (reg and reg_in_text(reg, t)) or (vat and reg_in_text(vat, t))]
        if reg or vat:
            out.append(Evidence("website", EV.CHECK_SITE_SHOWS_REG, EV.COMPANY, EV.PASS if shows else EV.UNKNOWN, EV.NONE, reg or vat,
                                "the registration / VAT number is shown on the website" if shows else "the number was not found on the pages read",
                                source_url=shows[0] if shows else site, detail={"pages": shows}))
        if person:
            hits = [pu for pu, t in pages if T.name_in_text(person, t)]
            if hits:
                out.append(Evidence("website", EV.CHECK_SITE_LISTS_PERSON, EV.PERSON, EV.PASS, EV.STRONG, person,
                                    "the applicant is named on the official website", source_url=hits[0], detail={"pages": hits}))
            else:
                out.append(Evidence("website", EV.CHECK_SITE_LISTS_PERSON, EV.PERSON, EV.UNKNOWN, EV.NONE, person,
                                    "the applicant is not named on the pages read", source_url=site))
        return out
