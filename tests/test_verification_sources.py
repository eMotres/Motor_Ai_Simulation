"""Applicant verification: the source adapters with MOCKED network (a fake HTTP client, a fake MX lookup) - GLEIF, UK Companies
House, EU VIES, the website, the e-mail checks, the applicant's links, the SSRF guard, the registry (a new source plugs in
without touching the core)."""
from __future__ import annotations

import struct

import pytest

from motor_ai_sim.verification import config as C
from motor_ai_sim.verification import evidence as EV
from motor_ai_sim.verification import net as NET
from motor_ai_sim.verification import sources as S
from motor_ai_sim.verification.evidence import Evidence
from motor_ai_sim.verification.net import HttpResult


from tests._verification_fakes import FakeHttp  # noqa: E402


def ctx(http, ttl=0.0):
    return S.Context(cache={}, ttl=ttl, http=http)


def case(**app):
    a = dict(legal_name="Siemens Aktiengesellschaft", country="DE", reg_no="HRB 6684", vat_no="", website="", applicant_name="Erika Mustermann",
             title="CTO", work_email="erika@siemens.com", purpose="x")
    a.update(app)
    return {"id": "v20261015-aaaaaaaaaaaa", "user": "erika@siemens.com", "application": a,
            "emails": [{"address": "erika@siemens.com", "verified": True, "method": "account"}], "urls": [], "notes": [], "documents": []}


def one(evs, check):
    return next(e for e in evs if e.check == check)


# -- GLEIF -------------------------------------------------------------------------------------------------------------------

def gleif_record(name="Siemens Aktiengesellschaft", status="ACTIVE", country="DE", reg="HRB 6684", jurisdiction="DE"):
    return {"data": [{"id": "W38RGI023J3WT1HWRP32", "attributes": {"lei": "W38RGI023J3WT1HWRP32", "entity": {
        "legalName": {"name": name}, "legalAddress": {"country": country, "city": "München", "addressLines": ["Werner-von-Siemens-Str. 1"]},
        "registeredAt": {"id": "RA000304"}, "registeredAs": reg, "jurisdiction": jurisdiction, "status": status}}}]}


GLEIF = "https://api.gleif.org/api/v1/lei-records"


def test_gleif_confirms_an_active_company_with_matching_registration_number():
    http = FakeHttp({GLEIF: gleif_record()})
    [e] = S.GleifSource().run(case(), ctx(http))
    assert e.result == EV.PASS and e.registry_grade and e.strength == EV.STRONG and e.detail["reg_no_match"] is True
    assert e.detail["lei"] == "W38RGI023J3WT1HWRP32" and e.raw["entity"]["registeredAs"] == "HRB 6684" and "search.gleif.org" in e.source_url
    assert "filter[entity.legalName]" in http.calls[0][0]


def test_gleif_short_name_matches_the_long_legal_name():
    http = FakeHttp({GLEIF: gleif_record()})
    [e] = S.GleifSource().run(case(legal_name="Siemens AG"), ctx(http))
    assert e.result == EV.PASS


@pytest.mark.parametrize("kw,why", [(dict(country="FR", jurisdiction="FR"), "register says"), (dict(status="INACTIVE"), "INACTIVE"), (dict(reg="HRB 999"), "registration number")])
def test_gleif_conflicts_go_to_a_person(kw, why):
    [e] = S.GleifSource().run(case(), ctx(FakeHttp({GLEIF: gleif_record(**kw)})))
    assert e.result == EV.CONFLICT and why in e.summary


def test_gleif_same_name_abroad_is_a_different_company_not_a_conflict():
    # 2026-10-20: "Orbital" (CA) matched a Belgian ORBITAL and the case went to a person as a "conflict"
    rec = gleif_record(name="ORBITAL", country="BE", jurisdiction="BE", reg="0123.456.789")
    [e] = S.GleifSource().run(case(legal_name="Orbital", country="CA", reg_no="15068525"), ctx(FakeHttp({GLEIF: rec})))
    assert e.result == EV.UNKNOWN and "different company" in e.summary and "BE" in e.summary


def test_gleif_prefers_the_record_in_the_applicants_country():
    abroad = gleif_record(country="FR", jurisdiction="FR", reg="RCS 1")["data"][0]
    home = gleif_record()["data"][0]
    home = dict(home, id="HOME0000000000000000")
    [e] = S.GleifSource().run(case(), ctx(FakeHttp({GLEIF: {"data": [abroad, home]}})))
    assert e.result == EV.PASS and e.detail["lei"] == "HOME0000000000000000"


def test_gleif_not_found_is_unknown_never_a_failure_and_errors_are_unknown():
    [e] = S.GleifSource().run(case(), ctx(FakeHttp({GLEIF: {"data": []}})))
    assert e.result == EV.UNKNOWN
    [e2] = S.GleifSource().run(case(), ctx(FakeHttp({GLEIF: HttpResult("u", 503, "")})))
    assert e2.result == EV.UNKNOWN and "did not answer" in e2.summary
    [e3] = S.GleifSource().run(case(), ctx(FakeHttp({GLEIF: HttpResult("u", 0, error="ConnectTimeout")})))
    assert e3.result == EV.UNKNOWN
    [e4] = S.GleifSource().run(case(legal_name="Something Else Corp"), ctx(FakeHttp({GLEIF: gleif_record()})))
    assert e4.result == EV.UNKNOWN


def test_results_are_cached_inside_a_case():
    http = FakeHttp({GLEIF: gleif_record()})
    c = ctx(http, ttl=3600)
    S.GleifSource().run(case(), c)
    n = len(http.calls)
    S.GleifSource().run(case(), c)
    assert len(http.calls) == n


# -- Companies House -----------------------------------------------------------------------------------------------------------

CH = "https://api.company-information.service.gov.uk"


def ch_routes(status="active", name="ACME DRONES LIMITED", officers=None):
    return {CH + "/company/00445790/officers": {"items": officers if officers is not None else [
                {"name": "MUSTERMANN, Erika Maria", "officer_role": "director"}, {"name": "OLD, Person", "officer_role": "director", "resigned_on": "2020-01-01"}]},
            CH + "/company/00445790": {"company_name": name, "company_number": "00445790", "company_status": status, "type": "ltd"},
            CH + "/search/companies": {"items": [{"title": name, "company_number": "00445790"}]}}


def test_companies_house_is_not_configured_without_a_key(monkeypatch):
    monkeypatch.delenv("COMPANIES_HOUSE_API_KEY", raising=False)
    [e] = S.CompaniesHouseSource().run(case(country="GB"), ctx(FakeHttp()))
    assert e.result == EV.NOT_CONFIGURED and "COMPANIES_HOUSE_API_KEY" in e.summary
    assert S.CompaniesHouseSource().applies("GB", case(country="GB")) and not S.CompaniesHouseSource().applies("DE", case())


def test_companies_house_confirms_the_company_and_the_officer(monkeypatch):
    monkeypatch.setenv("COMPANIES_HOUSE_API_KEY", "k")
    http = FakeHttp(ch_routes())
    evs = S.CompaniesHouseSource().run(case(country="GB", legal_name="Acme Drones Ltd", reg_no="445790"), ctx(http))
    assert one(evs, EV.CHECK_REGISTRY_COMPANY).result == EV.PASS
    o = one(evs, EV.CHECK_REGISTRY_OFFICER)
    assert o.result == EV.PASS and o.strength == EV.STRONG and o.raw["matched"][0]["role"] == "director"
    assert http.calls[0][1]["auth"] == ("k", "")          # the key is the Basic-auth user name


def test_companies_house_resigned_officer_does_not_count_and_by_name_search_works(monkeypatch):
    monkeypatch.setenv("COMPANIES_HOUSE_API_KEY", "k")
    evs = S.CompaniesHouseSource().run(case(country="GB", legal_name="Acme Drones Ltd", reg_no="", applicant_name="Person Old"),
                                       ctx(FakeHttp(ch_routes())))
    assert one(evs, EV.CHECK_REGISTRY_COMPANY).result == EV.PASS
    assert one(evs, EV.CHECK_REGISTRY_OFFICER).result == EV.UNKNOWN


@pytest.mark.parametrize("kw,res", [(dict(status="dissolved"), EV.CONFLICT), (dict(name="TOTALLY OTHER LIMITED"), EV.CONFLICT)])
def test_companies_house_conflicts(monkeypatch, kw, res):
    monkeypatch.setenv("COMPANIES_HOUSE_API_KEY", "k")
    evs = S.CompaniesHouseSource().run(case(country="GB", legal_name="Acme Drones Ltd", reg_no="445790"), ctx(FakeHttp(ch_routes(**kw))))
    assert one(evs, EV.CHECK_REGISTRY_COMPANY).result == res


# -- VIES ------------------------------------------------------------------------------------------------------------------------

def test_vies_valid_number_without_a_name_is_unknown(monkeypatch):
    http = FakeHttp({"https://ec.europa.eu/taxation_customs/vies/rest-api/ms/DE/vat/811115368": {"isValid": True, "name": "---", "address": "---", "userError": "VALID"}})
    [e] = S.ViesSource().run(case(vat_no="DE 811 115 368"), ctx(http))
    assert e.result == EV.UNKNOWN and e.detail["name_disclosed"] is False and e.registry_grade


def test_vies_name_must_match():
    url = "https://ec.europa.eu/taxation_customs/vies/rest-api/ms/NL/vat/123456789B01"
    [ok] = S.ViesSource().run(case(country="NL", legal_name="Acme B.V.", vat_no="NL123456789B01"), ctx(FakeHttp({url: {"isValid": True, "name": "ACME BV"}})))
    assert ok.result == EV.PASS and ok.strength == EV.STRONG
    [bad] = S.ViesSource().run(case(country="NL", legal_name="Acme B.V.", vat_no="NL123456789B01"), ctx(FakeHttp({url: {"isValid": True, "name": "ZZZ HOLDING"}})))
    assert bad.result == EV.CONFLICT
    [inv] = S.ViesSource().run(case(country="NL", legal_name="Acme B.V.", vat_no="NL123456789B01"), ctx(FakeHttp({url: {"isValid": False, "userError": "INVALID"}})))
    assert inv.result == EV.FAIL
    [wrong] = S.ViesSource().run(case(country="FR", legal_name="Acme B.V.", vat_no="NL123456789B01"), ctx(FakeHttp({url: {"isValid": True, "name": "ACME BV"}})))
    assert wrong.result == EV.CONFLICT                      # a Dutch VAT number for a French company


def test_vies_applies_only_with_a_vat_number_of_a_member_state():
    assert not S.ViesSource().applies("DE", case())
    assert S.ViesSource().applies("DE", case(vat_no="811115368")) and S.ViesSource().applies("GR", case(country="GR", vat_no="EL123456789"))
    assert not S.ViesSource().applies("US", case(country="US", vat_no="12-3456789"))


# -- website ---------------------------------------------------------------------------------------------------------------------

PAGES = {
    "https://www.siemens.com/": "<html><title>Siemens</title><body>Siemens Aktiengesellschaft - Werner-von-Siemens-Str. 1</body></html>",
    "https://www.siemens.com/about": "<html><body><h2>Team</h2><p>Dr. Erika Mustermann, Head of Drives</p></body></html>",
    "https://www.siemens.com/impressum": "<html><body>Siemens Aktiengesellschaft, Handelsregister HRB 6684 Muenchen</body></html>",
}


def test_website_names_company_registration_number_and_person():
    evs = S.WebsiteSource().run(case(website="https://www.siemens.com"), ctx(FakeHttp(PAGES)))
    assert one(evs, EV.CHECK_SITE_RESOLVES).result == EV.PASS
    assert one(evs, EV.CHECK_SITE_NAMES_COMPANY).result == EV.PASS
    assert one(evs, EV.CHECK_SITE_SHOWS_REG).result == EV.PASS
    p = one(evs, EV.CHECK_SITE_LISTS_PERSON)
    assert p.result == EV.PASS and p.kind == EV.PERSON and p.source_url.endswith("/about")


def test_website_that_does_not_name_the_company_or_person_is_unknown():
    evs = S.WebsiteSource().run(case(website="https://other.example"), ctx(FakeHttp({"https://other.example/": "<html>A shop for shoes</html>"})))
    assert one(evs, EV.CHECK_SITE_NAMES_COMPANY).result == EV.UNKNOWN and one(evs, EV.CHECK_SITE_LISTS_PERSON).result == EV.UNKNOWN


def test_website_unreachable_and_robots_blocked():
    gone = S.WebsiteSource().run(case(website="https://gone.example"), ctx(FakeHttp({"https://gone.example/": HttpResult("u", 0, error="ConnectError")})))
    assert [e.check for e in gone] == [EV.CHECK_SITE_RESOLVES] and gone[0].result == EV.FAIL
    robots = FakeHttp({"https://closed.example/robots.txt": "User-agent: *\nDisallow: /\n", "https://closed.example/": "<html>Siemens Aktiengesellschaft</html>"})
    evs = S.WebsiteSource().run(case(website="https://closed.example"), ctx(robots))
    assert evs[0].result == EV.UNKNOWN and "robots" in evs[0].summary
    assert not any(url == "https://closed.example/" for url, _kw in robots.calls)          # the page itself was never requested


def test_website_requests_are_public_only_with_a_short_page_budget():
    http = FakeHttp(PAGES)
    S.WebsiteSource().run(case(website="https://www.siemens.com"), ctx(http))
    assert all(kw.get("public_only") for _u, kw in http.calls)
    assert len([1 for u, _ in http.calls if not u.endswith("robots.txt")]) <= C.SITE_MAX_PAGES


# -- e-mail ----------------------------------------------------------------------------------------------------------------------

@pytest.fixture()
def mx(monkeypatch):
    from motor_ai_sim import email_domain as ED
    monkeypatch.setattr(ED, "_exists", lambda d, t: "yes")
    ED.clear_cache()
    NET.set_mx(lambda d: [(10, "mail." + d)])
    yield
    NET.set_mx(None)
    ED.clear_cache()


def test_email_checks_corporate_domain_mx_and_website_match(mx):
    c = case(website="https://www.siemens.com")
    c["emails"] = [{"address": "erika@siemens.com", "verified": True, "method": "account"},
                   {"address": "someone@gmail.com", "verified": True, "method": "otp"},
                   {"address": "x@mailinator.com", "verified": False, "method": "claimed"},
                   {"address": "e@other.org", "verified": True, "method": "otp"}]
    evs = S.EmailSource().run(c, ctx(FakeHttp()))
    pick = lambda check, addr: next(e for e in evs if e.check == check and e.value == addr)
    assert pick(EV.CHECK_EMAIL_VERIFIED, "erika@siemens.com").result == EV.PASS and pick(EV.CHECK_EMAIL_VERIFIED, "x@mailinator.com").result == EV.FAIL
    assert pick(EV.CHECK_EMAIL_CORPORATE, "erika@siemens.com").result == EV.PASS
    assert pick(EV.CHECK_EMAIL_CORPORATE, "someone@gmail.com").result == EV.FAIL and pick(EV.CHECK_EMAIL_CORPORATE, "someone@gmail.com").detail["free"]
    assert pick(EV.CHECK_EMAIL_CORPORATE, "x@mailinator.com").detail["disposable"]
    assert pick(EV.CHECK_EMAIL_MX, "erika@siemens.com").result == EV.PASS and pick(EV.CHECK_EMAIL_DOMAIN, "erika@siemens.com").result == EV.PASS
    assert pick(EV.CHECK_DOMAIN_MATCH, "erika@siemens.com").result == EV.PASS and pick(EV.CHECK_DOMAIN_MATCH, "e@other.org").result == EV.FAIL
    assert not any(e.check == EV.CHECK_DOMAIN_MATCH and e.value == "someone@gmail.com" for e in evs)       # a free mailbox is never compared


def test_email_mx_unknown_and_null_mx(monkeypatch):
    from motor_ai_sim import email_domain as ED
    monkeypatch.setattr(ED, "_exists", lambda d, t: "unknown")
    ED.clear_cache()
    NET.set_mx(lambda d: None)
    try:
        evs = S.EmailSource().run(case(website=""), ctx(FakeHttp()))
        assert one(evs, EV.CHECK_EMAIL_MX).result == EV.UNKNOWN and one(evs, EV.CHECK_EMAIL_DOMAIN).result == EV.UNKNOWN
        NET.set_mx(lambda d: [(0, ".")])
        assert one(S.EmailSource().run(case(website=""), ctx(FakeHttp())), EV.CHECK_EMAIL_MX).result == EV.FAIL
    finally:
        NET.set_mx(None)
        ED.clear_cache()


def test_mx_response_parser():
    q = b"\x00\x01\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00\x03foo\x03com\x00\x00\x0f\x00\x01"
    rdata = struct.pack(">H", 10) + b"\x04mail\xc0\x0c"            # preference 10, "mail" + pointer to foo.com
    ans = b"\xc0\x0c\x00\x0f\x00\x01\x00\x00\x00\x3c" + struct.pack(">H", len(rdata)) + rdata
    resp = b"\x00\x01\x81\x80\x00\x01\x00\x01\x00\x00\x00\x00" + q[12:] + ans
    assert NET.parse_mx(resp) == [(10, "mail.foo.com")]
    nx = bytearray(resp)
    nx[3] = 0x83
    assert NET.parse_mx(bytes(nx)) == [] and NET.parse_mx(b"\x00") is None


# -- applicant links ---------------------------------------------------------------------------------------------------------------

def test_linkedin_slug_is_supporting_and_never_fetched():
    http = FakeHttp()
    c = case(website="https://www.siemens.com")
    c["urls"] = ["https://www.linkedin.com/in/erika-mustermann-1a2b3c", "https://www.linkedin.com/in/someone-else", "https://www.linkedin.com/company/siemens"]
    evs = S.ApplicantUrlsSource().run(c, ctx(http))
    assert [e.result for e in evs] == [EV.PASS, EV.UNKNOWN] and evs[0].strength == EV.SUPPORTING and evs[0].detail["fetched"] is False
    assert http.calls == []


def test_applicant_pages_same_site_third_party_and_unreadable():
    http = FakeHttp({"https://www.siemens.com/people": "<html>Erika Mustermann, Drives</html>",
                     "https://press.example/a": "<html>Erika Mustermann of Siemens Aktiengesellschaft spoke at the conference</html>",
                     "https://press.example/b": "<html>Erika Mustermann spoke at the conference</html>"})
    c = case(website="https://www.siemens.com")
    c["urls"] = ["https://www.siemens.com/people", "https://press.example/a", "https://press.example/b", "https://nowhere.example/x"]
    evs = S.ApplicantUrlsSource().run(c, ctx(http))
    assert (evs[0].check, evs[0].result, evs[0].strength) == (EV.CHECK_URL_SITE_PAGE, EV.PASS, EV.STRONG)
    assert (evs[1].check, evs[1].result, evs[1].strength) == (EV.CHECK_URL_THIRD_PARTY, EV.PASS, EV.SUPPORTING)
    assert evs[2].result == EV.UNKNOWN and evs[3].result == EV.UNKNOWN


# -- the SSRF guard ------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("url,why", [
    ("http://127.0.0.1/", "not_public"), ("http://10.1.2.3/x", "not_public"), ("http://169.254.169.254/latest/meta-data", "not_public"),
    ("http://[::1]/", "not_public"), ("ftp://example.com/", "scheme"), ("https://example.com:8080/", "port"), ("https://u:p@example.com/", "credentials"),
    ("file:///etc/passwd", "scheme")])
def test_ssrf_guard_refuses_private_and_odd_targets(url, why):
    assert NET.check_public_url(url, resolver=lambda h: ["93.184.216.34"]) == why


def test_ssrf_guard_checks_every_resolved_address():
    assert NET.check_public_url("https://example.com/", resolver=lambda h: ["93.184.216.34"]) is None
    assert NET.check_public_url("https://rebind.example/", resolver=lambda h: ["93.184.216.34", "10.0.0.5"]) == "not_public"
    assert NET.check_public_url("https://nx.example/", resolver=lambda h: []) == "unresolvable"


def test_http_client_blocks_a_private_target_before_connecting():
    r = NET.Http(resolver=lambda h: ["127.0.0.1"]).get("http://evil.example/", public_only=True)
    assert r.status == 0 and r.error == "blocked:not_public"


# -- the registry: a new source plugs in without touching the core ---------------------------------------------------------------------

def test_a_new_registry_plugs_in_and_a_crashing_source_becomes_an_error_row():
    class OfficialRegistryXY(S.VerificationSource):
        name = "xy_registry"

        def applies(self, country, data):
            return country == "XY"

        def run(self, c, x):
            return [Evidence("xy_registry", EV.CHECK_REGISTRY_COMPANY, EV.COMPANY, EV.PASS, EV.STRONG, "n", registry_grade=True)]

    class Boom(S.VerificationSource):
        name = "boom"

        def run(self, c, x):
            raise RuntimeError("bug")

    try:
        S.register(OfficialRegistryXY())
        S.register(Boom())
        out = S.run_all(case(country="XY"), ctx(FakeHttp()), sources=[s for s in S.registered() if s.name in ("xy_registry", "boom")])
        assert [(e.source, e.check) for e in out] == [("xy_registry", EV.CHECK_REGISTRY_COMPANY), ("boom", EV.CHECK_SOURCE_ERROR)]
        assert "RuntimeError" in out[1].summary
        assert S.run_all(case(country="DE"), ctx(FakeHttp()), sources=[OfficialRegistryXY()]) == []
    finally:
        S.unregister("xy_registry")
        S.unregister("boom")


def test_the_advisory_llm_stub_gathers_citations_only_and_cannot_confirm():
    class Fake(S.LlmResearchSource):
        def applies(self, country, data):
            return True

        def gather(self, c, x):
            return [{"url": "https://news.example/1", "quote": "Erika Mustermann, CTO of Siemens", "claim": "named as CTO", "relevance": "person"},
                    {"url": "", "quote": "no source"}]

    out = S.run_all(case(), ctx(FakeHttp()), sources=[Fake()])
    assert len(out) == 1 and out[0].strength == EV.SUPPORTING and out[0].detail["quote"].startswith("Erika")
    assert not S.LlmResearchSource().applies("DE", case())          # off until a backend exists (phase 2)

    class Greedy(S.VerificationSource):
        name = "greedy"
        advisory = True

        def run(self, c, x):
            return [Evidence("greedy", EV.CHECK_REGISTRY_COMPANY, EV.COMPANY, EV.PASS, EV.STRONG, "n", registry_grade=True)]

    [e] = S.run_all(case(), ctx(FakeHttp()), sources=[Greedy()])
    assert e.result == EV.UNKNOWN and not e.registry_grade


def test_china_stub_asks_for_manual_review():
    assert S.ChinaRegistrySource().applies("CN", case(country="CN")) and not S.ChinaRegistrySource().applies("DE", case())
    [e] = S.ChinaRegistrySource().run(case(country="CN"), ctx(FakeHttp()))
    assert e.result == EV.MANUAL and e.detail["request"] == "business_licence"
