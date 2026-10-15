"""Applicant verification: the PURE decision engine as a table (evidence in, status out), the text helpers, the thresholds.

Every example of the owner's spec is a row: a verified e-mail on a company-owned domain is strong; "works at Siemens" from a gmail
address is only a question; a website alone is not a registry confirmation; LinkedIn alone never verifies.
"""
from __future__ import annotations

import pytest

from motor_ai_sim.verification import config as C
from motor_ai_sim.verification import engine as ENG
from motor_ai_sim.verification import evidence as EV
from motor_ai_sim.verification import textutil as T
from motor_ai_sim.verification.evidence import Evidence

APP = dict(legal_name="Siemens Aktiengesellschaft", country="DE", reg_no="HRB 6684", vat_no="", website="https://www.siemens.com",
           applicant_name="Erika Mustermann", title="R&D Engineer", work_email="erika@siemens.com", purpose="evaluation")
ACCOUNT = "erika@siemens.com"


def ev(check, result=EV.PASS, kind=EV.INFO, strength=EV.NONE, value="", source="x", grade=False, summary="", detail=None):
    return Evidence(source=source, check=check, kind=kind, result=result, strength=strength, value=value, summary=summary or check,
                    registry_grade=grade, detail=detail or {})


def registry(source="gleif", result=EV.PASS):
    return ev(EV.CHECK_REGISTRY_COMPANY, result, EV.COMPANY, EV.STRONG if result == EV.PASS else EV.NONE, "Siemens", source, True, "registry says so")


def email(addr=ACCOUNT, verified=True, corporate=True, match=True):
    out = [ev(EV.CHECK_EMAIL_VERIFIED, EV.PASS if verified else EV.FAIL, EV.EMAIL, value=addr, source="email"),
           ev(EV.CHECK_EMAIL_CORPORATE, EV.PASS if corporate else EV.FAIL, EV.EMAIL, value=addr, source="email")]
    if corporate:
        out.append(ev(EV.CHECK_DOMAIN_MATCH, EV.PASS if match else EV.FAIL, EV.DOMAIN, value=addr, source="domain"))
    return out


def site(resolves=True, names=True, shows_reg=False, lists_person=False):
    out = [ev(EV.CHECK_SITE_RESOLVES, EV.PASS if resolves else EV.FAIL, EV.DOMAIN, source="website")]
    if resolves:
        out.append(ev(EV.CHECK_SITE_NAMES_COMPANY, EV.PASS if names else EV.UNKNOWN, EV.COMPANY, source="website"))
        if shows_reg:
            out.append(ev(EV.CHECK_SITE_SHOWS_REG, EV.PASS, EV.COMPANY, source="website"))
        if lists_person:
            out.append(ev(EV.CHECK_SITE_LISTS_PERSON, EV.PASS, EV.PERSON, EV.STRONG, value="Erika Mustermann", source="website"))
    return out


def linkedin():
    return [ev(EV.CHECK_LINKEDIN, EV.PASS, EV.PERSON, EV.SUPPORTING, value="https://www.linkedin.com/in/erika-mustermann", source="applicant_urls")]


def officer():
    return [ev(EV.CHECK_REGISTRY_OFFICER, EV.PASS, EV.PERSON, EV.STRONG, value="Erika Mustermann", source="companies_house")]


def status(app=None, *evs, prior=None):
    flat = [e for x in evs for e in (x if isinstance(x, list) else [x])]
    return ENG.decide(dict(APP, **(app or {})), flat, prior)


# -- the verified rows --------------------------------------------------------------------------------------------------

def test_registry_company_plus_company_email_plus_linkedin_is_verified():
    d = status(None, registry(), email(), site(), linkedin())
    assert d.status == ENG.VERIFIED
    assert d.company["confirmed"] and d.company["by"] == ["gleif"]
    assert [s["family"] for s in d.signals] == ["email", "linkedin"] and d.signals[0]["strength"] == EV.STRONG
    assert any(r["code"] == "verified" for r in d.reasons)


def test_registry_company_plus_two_strong_signals_is_verified():
    d = status(None, registry("companies_house"), email(), site(lists_person=True), officer())
    assert d.status == ENG.VERIFIED and d.company["by"] == ["companies_house"]
    assert len(d.signals) == 3


def test_a_verified_company_email_alone_is_strong_but_one_signal_so_more_info():
    """siemens.com + verified = a STRONG signal, but two independent signals are needed."""
    d = status(None, registry(), email(), site())
    assert d.status == ENG.MORE_INFORMATION_REQUIRED
    assert [s["strength"] for s in d.signals] == [EV.STRONG]
    assert [r["code"] for r in d.requests] == [ENG.REQ_PERSON_PAGE]
    assert any(r["code"] == "person_one_signal" for r in d.reasons)


def test_gmail_and_works_at_siemens_is_only_a_question():
    d = status(dict(work_email="someone@gmail.com"), registry(), email("someone@gmail.com", corporate=False), site())
    assert d.status == ENG.MORE_INFORMATION_REQUIRED
    assert d.signals == []
    codes = [r["code"] for r in d.requests]
    assert ENG.REQ_CORPORATE_EMAIL in codes and ENG.REQ_PERSON_PAGE in codes
    assert any(r["code"] == "free_mail" for r in d.reasons)
    assert d.requests[codes.index(ENG.REQ_CORPORATE_EMAIL)]["params"]["domain"] == "siemens.com"


def test_linkedin_alone_never_verifies():
    d = status(None, registry(), email("e@gmail.com", corporate=False), site(), linkedin())
    assert d.status == ENG.MORE_INFORMATION_REQUIRED
    assert [s["family"] for s in d.signals] == ["linkedin"] and d.signals[0]["strength"] == EV.SUPPORTING


def test_two_supporting_signals_are_still_not_enough():
    third = ev(EV.CHECK_URL_THIRD_PARTY, EV.PASS, EV.PERSON, EV.SUPPORTING, value="https://news.example/x", source="applicant_urls")
    d = status(None, registry(), email("e@gmail.com", corporate=False), site(), linkedin(), third)
    assert d.status == ENG.MORE_INFORMATION_REQUIRED and len(d.signals) == 2 and not any(s["strength"] == EV.STRONG for s in d.signals)


def test_website_only_company_is_not_registry_confirmed():
    d = status(dict(vat_no="", reg_no=""), site(), email(), linkedin())
    assert d.company["confirmed"] is False
    assert d.status == ENG.MORE_INFORMATION_REQUIRED          # first round: ask for the registration number / VAT number
    assert {r["code"] for r in d.requests} >= {ENG.REQ_REG_NO, ENG.REQ_VAT}


def test_website_only_company_with_all_details_given_goes_to_a_person():
    d = status(dict(vat_no="DE811115368"), site(), email(), linkedin(), registry(result=EV.UNKNOWN))
    assert d.status == ENG.MANUAL_REVIEW and d.reasons[0]["code"] == "company_unidentifiable"


def test_an_advisory_company_pass_cannot_confirm():
    e = ev(EV.CHECK_REGISTRY_COMPANY, EV.PASS, EV.COMPANY, EV.STRONG, source="llm_research", grade=True)
    d = status(None, e, email(), site(), linkedin())
    assert d.company["confirmed"] is False                    # only sources in REGISTRY_GRADE_SOURCES count


# -- the manual rows -------------------------------------------------------------------------------------------------------

def test_conflict_goes_to_manual_review_even_with_all_the_signals():
    d = status(None, registry(), registry(result=EV.CONFLICT), email(), site(lists_person=True), linkedin())
    assert d.status == ENG.MANUAL_REVIEW and d.reasons[0]["code"] == "conflict"


def test_an_adapter_error_goes_to_manual_review():
    err = ev(EV.CHECK_SOURCE_ERROR, EV.UNKNOWN, summary="KeyError: 'x'", source="gleif")
    d = status(None, registry(), email(), site(), linkedin(), err)
    assert d.status == ENG.MANUAL_REVIEW and d.reasons[0]["code"] == "exception"


def test_china_is_manual_review_with_a_licence_request():
    manual = ev(EV.CHECK_REGISTRY_COMPANY, EV.MANUAL, EV.INFO, source="china_registry", summary="no API")
    d = status(dict(country="CN"), manual, email())
    assert d.status == ENG.MANUAL_REVIEW and d.reasons[0]["code"] == "no_registry"
    assert [r["code"] for r in d.requests] == [ENG.REQ_LICENCE]
    again = status(dict(country="CN"), manual, email(), prior={"requested": [ENG.REQ_LICENCE]})
    assert again.requests == []                               # asked once


def test_a_china_company_confirmed_by_gleif_is_not_forced_to_manual():
    manual = ev(EV.CHECK_REGISTRY_COMPANY, EV.MANUAL, EV.INFO, source="china_registry")
    d = status(dict(country="CN"), manual, registry(), email(), site(), linkedin())
    assert d.status == ENG.VERIFIED


def test_repeated_open_replies_go_to_manual_review():
    d = status(None, registry(), email("e@gmail.com", corporate=False), site(), prior={"open_replies": C.MAX_OPEN_REPLIES})
    assert d.status == ENG.MANUAL_REVIEW and d.reasons[0]["code"] == "repeated_failure"
    assert status(None, registry(), email("e@gmail.com", corporate=False), site(), prior={"open_replies": C.MAX_OPEN_REPLIES - 1}).status \
        == ENG.MORE_INFORMATION_REQUIRED


# -- fixable gaps ----------------------------------------------------------------------------------------------------------

def test_missing_website_is_asked_for():
    d = status(dict(website=""), registry(), email())
    assert d.status == ENG.MORE_INFORMATION_REQUIRED and ENG.REQ_WEBSITE in [r["code"] for r in d.requests]


def test_domain_mismatch_is_asked_about():
    d = status(None, registry(), email("e@other-domain.com", match=False), site())
    assert d.status == ENG.MORE_INFORMATION_REQUIRED
    req = next(r for r in d.requests if r["code"] == ENG.REQ_DOMAIN_MISMATCH)
    assert req["params"]["addresses"] == ["e@other-domain.com"] and req["params"]["domain"] == "siemens.com"


def test_an_unverified_account_email_is_asked_to_be_confirmed():
    d = status(None, registry(), email(verified=False), site())
    assert d.status == ENG.MORE_INFORMATION_REQUIRED and ENG.REQ_ACCOUNT_EMAIL in [r["code"] for r in d.requests]


def test_a_website_that_does_not_name_the_company_downgrades_the_email_to_supporting():
    d = status(None, registry(), email(), site(names=False), linkedin())
    assert d.status == ENG.MORE_INFORMATION_REQUIRED          # one supporting + one supporting, no strong
    assert {s["strength"] for s in d.signals} == {EV.SUPPORTING}
    assert any(r["code"] == "website_not_tied" for r in d.reasons)


def test_a_fake_look_alike_domain_is_not_tied_to_the_company():
    """Anybody can write a company name on a page: the domain must resemble the name, or the page must show the registration number."""
    fake = dict(website="https://fast-parts-shop.com")
    d = status(fake, registry(), email("e@fast-parts-shop.com"), site(), linkedin())
    assert d.status == ENG.MORE_INFORMATION_REQUIRED and d.facts["site_confirmed"] is False
    shown = status(fake, registry(), email("e@fast-parts-shop.com"), site(shows_reg=True), linkedin())
    assert shown.status == ENG.VERIFIED and shown.facts["site_confirmed"] is True


def test_the_person_listed_on_an_untied_site_is_only_supporting():
    d = status(dict(website="https://fast-parts-shop.com"), registry(), email("e@fast-parts-shop.com", verified=False), site(lists_person=True))
    assert d.status == ENG.MORE_INFORMATION_REQUIRED and [s["strength"] for s in d.signals] == [EV.SUPPORTING]


def test_an_unusual_role_is_asked_once_then_a_person_decides():
    base = (registry(), email(), site(lists_person=True), linkedin())
    first = status(dict(title="Intern for coffee"), *base)
    assert first.status == ENG.MORE_INFORMATION_REQUIRED and [r["code"] for r in first.requests] == [ENG.REQ_ROLE]
    waiting = status(dict(title="Intern for coffee"), *base, prior={"requested": [ENG.REQ_ROLE], "reply_since_request": False})
    assert waiting.status == ENG.MORE_INFORMATION_REQUIRED and waiting.requests == []
    answered = status(dict(title="Intern for coffee"), *base, prior={"requested": [ENG.REQ_ROLE], "reply_since_request": True})
    assert answered.status == ENG.MANUAL_REVIEW and answered.reasons[0]["code"] == "role_unclear"
    assert status(dict(title="Procurement manager"), *base).status == ENG.VERIFIED


def test_every_decision_carries_its_reasons():
    for d in (status(None, registry(), email(), site(), linkedin()), status(None, registry(), email("e@gmail.com", corporate=False)),
              status(None, registry(result=EV.CONFLICT))):
        assert d.reasons and all(r["code"] and r["text"] for r in d.reasons)
        assert d.to_dict()["status"] == d.status


def test_thresholds_live_in_the_config_file(monkeypatch):
    monkeypatch.setattr(C, "MIN_PERSON_SIGNALS", 1)
    assert status(None, registry(), email(), site()).status == ENG.VERIFIED


# -- text helpers ----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("a,b,want", [
    ("Siemens AG", "Siemens Aktiengesellschaft", "match"), ("Acme Drones Ltd", "ACME DRONES LIMITED", "match"),
    ("Müller & Söhne GmbH", "Muller und Sohne", "match"), ("Acme Drones", "Acme Robots", "different"),
    ("深圳市某某科技有限公司", "深圳市某某科技股份有限公司", "match")])
def test_company_names(a, b, want):
    assert T.company_match(a, b) == want


@pytest.mark.parametrize("url,want", [
    ("https://mail.siemens.co.uk/x", "siemens.co.uk"), ("www.siemens.com", "siemens.com"), ("a.b.example.com.cn", "example.com.cn"),
    ("erika@energy.siemens.com".split("@")[1], "siemens.com"), ("https://foo.co.jp/", "foo.co.jp"), ("http://localhost", "localhost")])
def test_registrable_domain(url, want):
    assert T.registrable_domain(url) == want


def test_domain_resemblance_and_names():
    assert T.domain_resembles_company("siemens.com", "Siemens Aktiengesellschaft")
    assert T.domain_resembles_company("bmw.com", "Bayerische Motoren Werke AG")
    assert not T.domain_resembles_company("fast-parts-shop.com", "Siemens Aktiengesellschaft")
    assert T.name_in_text("Erika Mustermann", "Our team: Mustermann, Erika - CTO") and T.name_in_text("Erika Mustermann", "contact Erika Maria Mustermann")
    assert not T.name_in_text("Erika Mustermann", "Erika works elsewhere. Another page mentions the Mustermann family business in a far away place of text")
    assert T.officer_matches("MUSTERMANN, Erika Maria", "Erika Mustermann") and not T.officer_matches("MUSTERMANN, Hans", "Erika Mustermann")
    assert T.reg_no_equal("HRB 6684", "hrb6684") and T.reg_no_equal("00445790", "445790") and not T.reg_no_equal("HRB 6684", "HRB 6685")
    assert T.normalize_website("siemens.com/about") == "https://siemens.com/about"
    assert T.normalize_website("http://10.0.0.1/") is None and T.normalize_website("intranet") is None and T.normalize_website("javascript:alert(1)") is None
    assert T.role_plausible("Head of R&D") and T.role_plausible("采购经理") and not T.role_plausible("Plumber")
