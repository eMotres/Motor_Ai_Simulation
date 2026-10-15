"""Links the applicant gave us on the reply page ("a page of your company that lists you", a LinkedIn profile, a press mention).

* a LinkedIn profile URL is NOT fetched (LinkedIn blocks it and its terms forbid scraping): only the address is read, and when its
  slug carries the applicant's name it is a SUPPORTING signal - never enough alone;
* a page on the official website's domain that names the applicant is the same signal as the website check (the engine decides
  its strength from whether the website is tied to the company);
* a page elsewhere (press, conference, a partner) that names both the applicant and the company is SUPPORTING.
"""
from __future__ import annotations

import re
import urllib.parse

from motor_ai_sim.verification import evidence as EV
from motor_ai_sim.verification import net as NET
from motor_ai_sim.verification import textutil as T
from motor_ai_sim.verification.evidence import Evidence
from motor_ai_sim.verification.sources.base import Context, VerificationSource
from motor_ai_sim.verification.sources.website_source import company_in_text


def linkedin_slug(url: str) -> str:
    m = re.match(r"^https?://([a-z]{2,3}\.)?linkedin\.com/in/([^/?#]+)", url.strip(), re.I)
    return urllib.parse.unquote(m.group(2)).lower() if m else ""


class ApplicantUrlsSource(VerificationSource):
    name = "applicant_urls"

    def applies(self, country: str, data: dict) -> bool:
        return bool(data.get("urls"))

    def run(self, case: dict, ctx: Context) -> list[Evidence]:
        app = case.get("application") or {}
        person, legal = app.get("applicant_name") or "", app.get("legal_name") or ""
        site = T.normalize_website(app.get("website") or "")
        site_dom = T.registrable_domain(site) if site else ""
        out: list[Evidence] = []
        for url in case.get("urls") or []:
            slug = linkedin_slug(url)
            if slug:
                toks = T.person_tokens(person)
                slug_toks = set(re.split(r"[^a-z0-9]+", T.fold(slug).replace(" ", "-")))
                hit = len(toks) >= 2 and toks[0] in slug_toks and toks[-1] in slug_toks
                out.append(Evidence("applicant_urls", EV.CHECK_LINKEDIN, EV.PERSON, EV.PASS if hit else EV.UNKNOWN,
                                    EV.SUPPORTING if hit else EV.NONE, url,
                                    "LinkedIn profile address carries the applicant's name (not fetched)" if hit else "LinkedIn address does not carry the applicant's name",
                                    source_url=url, detail={"fetched": False}))
                continue
            if "linkedin.com" in url.lower():
                continue                                              # a company page or other LinkedIn address: no signal
            page = NET.fetch_page(url, ctx.http)
            if not page.ok:
                out.append(Evidence("applicant_urls", EV.CHECK_URL_THIRD_PARTY, EV.PERSON, EV.UNKNOWN, EV.NONE, url,
                                    "the page could not be read (%s)" % page.error, source_url=url, detail={"error": page.error}))
                continue
            named = T.name_in_text(person, page.text)
            same_site = bool(site_dom) and T.registrable_domain(url) == site_dom
            if same_site:
                out.append(Evidence("applicant_urls", EV.CHECK_URL_SITE_PAGE, EV.PERSON, EV.PASS if named else EV.UNKNOWN,
                                    EV.STRONG if named else EV.NONE, url,
                                    "a page on the official website names the applicant" if named else "the page on the official website does not name the applicant",
                                    source_url=url))
            else:
                both = named and company_in_text(legal, page.text)
                out.append(Evidence("applicant_urls", EV.CHECK_URL_THIRD_PARTY, EV.PERSON, EV.PASS if both else EV.UNKNOWN,
                                    EV.SUPPORTING if both else EV.NONE, url,
                                    "a third-party page names the applicant and the company" if both else "the page does not name both the applicant and the company",
                                    source_url=url))
        return out
