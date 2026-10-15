from __future__ import annotations

from motor_ai_sim.verification import domain_link as DL
from motor_ai_sim.verification import net as NET
from motor_ai_sim.verification.sources.base import Context
from motor_ai_sim.verification.sources.email_source import EmailSource
from motor_ai_sim.verification.sources.website_source import WebsiteSource
from tests._verification_fakes import FakeHttp
from motor_ai_sim.verification.net import HttpResult


SITE = "https://emotres.com"
MAIL = "motresres.com"
MX = [(10, "aspmx.l.google.com"), (20, "alt1.aspmx.l.google.com")]


def _routes(home="<html>Emotres GmbH HRB 12345</html>", mail=None, extra=None):
    out = {SITE + "/": home}
    if mail is not None:
        out["https://" + MAIL + "/"] = mail
    out.update(extra or {})
    return out


def test_linked_domain_by_redirect_and_matching_mx(monkeypatch):
    monkeypatch.setattr(NET, "_mx_impl", lambda domain: list(MX))
    redirect = HttpResult("https://emotres.com/", 200, "<html>Emotres</html>", redirect_chain=[
        "https://motresres.com/", "https://emotres.com/"])
    ctx = Context(http=FakeHttp(_routes(mail=redirect)))

    linked, how, evidence = DL.email_domain_linked(MAIL, "emotres.com", context=ctx)

    assert linked and "redirect" in how
    assert any(p["type"] == "matching_mx_hosts" for p in evidence["proofs"])
    proof = next(p for p in evidence["proofs"] if p["type"] == "http_redirect")
    assert proof["chain"] == ["https://motresres.com/", "https://emotres.com/"]


def test_linked_domain_by_visible_website_mention_with_exact_boundaries(monkeypatch):
    monkeypatch.setattr(NET, "_mx_impl", lambda domain: list(MX))
    home = "<html><body>Contact us at team@motresres.com. Another domain is notmotresres.com.</body></html>"
    linked, how, evidence = DL.email_domain_linked(MAIL, "emotres.com", context=Context(http=FakeHttp(_routes(home=home))))

    assert linked and how == "matching_mx_plus_website_mention"
    mention = next(p for p in evidence["proofs"] if p["type"] == "visible_domain_text")
    assert mention["matched"] == MAIL and mention["url"] == SITE + "/"


def test_hidden_script_domain_mention_does_not_count(monkeypatch):
    monkeypatch.setattr(NET, "_mx_impl", lambda domain: list(MX))
    home = "<html><script>contact team@motresres.com</script><body>Emotres GmbH</body></html>"
    linked, how, evidence = DL.email_domain_linked(MAIL, "emotres.com", context=Context(http=FakeHttp(_routes(home=home))))

    assert not linked and how == "matching_mx_without_ownership_proof"
    assert evidence["mx_only"] is True


def test_matching_mx_alone_does_not_link(monkeypatch):
    monkeypatch.setattr(NET, "_mx_impl", lambda domain: list(MX))
    linked, how, evidence = DL.email_domain_linked(MAIL, "emotres.com", context=Context(http=FakeHttp(_routes())))

    assert not linked and how == "matching_mx_without_ownership_proof"
    assert evidence["mx_sets_match"] is True and evidence["proofs"] == []


def test_mentions_require_a_full_domain_token_and_accept_sentence_punctuation():
    text = "See motresres.com. Also support.motresres.com, notmotresres.com and motresres.com.evil.example."
    assert DL._visible_domain_mention(MAIL, [{"url": SITE + "/contact", "text": text}])["matched"] == MAIL
    adversarial = "support.motresres.com notmotresres.com motresres.com.evil.example"
    assert DL._visible_domain_mention(MAIL, [{"url": SITE + "/contact", "text": adversarial}]) is None


def test_identical_canonical_url_links_only_when_canonical_is_on_company_site(monkeypatch):
    monkeypatch.setattr(NET, "_mx_impl", lambda domain: list(MX))
    official = '<html><link rel="canonical" href="https://emotres.com/products"><body>Emotres GmbH</body></html>'
    candidate = HttpResult("https://motresres.com/", 200,
                           '<html><link rel="canonical" href="https://emotres.com/products"></html>')
    linked, how, evidence = DL.email_domain_linked(MAIL, "emotres.com", context=Context(http=FakeHttp(_routes(home=official, mail=candidate))))
    assert linked and how == "matching_mx_plus_identical_canonical_url"
    assert next(p for p in evidence["proofs"] if p["type"] == "identical_canonical_url")["url"] == "https://emotres.com/products"

    third_party = '<html><link rel="canonical" href="https://common.example/landing"><body>Emotres GmbH</body></html>'
    candidate = HttpResult("https://motresres.com/", 200,
                           '<html><link rel="canonical" href="https://common.example/landing"></html>')
    linked, how, _ = DL.email_domain_linked(MAIL, "emotres.com", context=Context(http=FakeHttp(_routes(home=third_party, mail=candidate))))
    assert not linked and how == "matching_mx_without_ownership_proof"


def test_website_mention_with_different_mx_does_not_link(monkeypatch):
    monkeypatch.setattr(NET, "_mx_impl", lambda domain: [(10, "mx." + domain)])
    home = "<html>Contact: team@motresres.com</html>"

    linked, how, evidence = DL.email_domain_linked(MAIL, "emotres.com", context=Context(http=FakeHttp(_routes(home=home))))

    assert not linked and how == "mx_records_do_not_match" and evidence["mx_sets_match"] is False


def test_linked_by_public_registrant_org_and_rejects_redacted_or_person_only(monkeypatch):
    monkeypatch.setattr(NET, "_mx_impl", lambda domain: list(MX))
    record_mail = {"entities": [{"roles": ["registrant"], "vcardArray": ["vcard", [["org", {}, "text", ["Emotres", "GmbH"]]]]}]}
    record_site = {"entities": [{"roles": ["registrant"], "vcardArray": ["vcard", [["org", {}, "text", "Emotres GmbH"]]]}]}
    routes = _routes(extra={"https://rdap.org/domain/motresres.com": record_mail, "https://rdap.org/domain/emotres.com": record_site})
    ctx = Context(http=FakeHttp(routes))
    linked, how, evidence = DL.email_domain_linked(MAIL, "emotres.com", context=ctx)
    assert linked and how.endswith("same_public_registrant_org")
    proof = next(p for p in evidence["proofs"] if p["type"] == "same_public_registrant_org")
    assert len(proof["source_urls"]) == 2 and all(url.startswith("https://rdap.org/") for url in proof["source_urls"])
    assert proof["organization_values"] == {"motresres.com": "Emotres GmbH", "emotres.com": "Emotres GmbH"}

    invalid_orgs = ("REDACTED FOR PRIVACY", "-", "---", 12345, "12345", {"name": "Emotres GmbH"}, [])
    for value in invalid_orgs:
        bad_record = {"entities": [{"roles": ["registrant"], "vcardArray": ["vcard", [["org", {}, "text", value]]]}]}
        bad = _routes(extra={"https://rdap.org/domain/motresres.com": bad_record,
                             "https://rdap.org/domain/emotres.com": bad_record})
        linked, how, _ = DL.email_domain_linked(MAIL, "emotres.com", context=Context(http=FakeHttp(bad)))
        assert not linked and how == "matching_mx_without_ownership_proof", value

    person_only = {"entities": [{"roles": ["registrant"], "vcardArray": ["vcard", [["fn", {}, "text", "Emotres GmbH"]]]}]}
    bad = _routes(extra={"https://rdap.org/domain/motresres.com": person_only, "https://rdap.org/domain/emotres.com": person_only})
    linked, how, _ = DL.email_domain_linked(MAIL, "emotres.com", context=Context(http=FakeHttp(bad)))
    assert not linked and how == "matching_mx_without_ownership_proof"


def test_same_or_subdomain_links_without_external_evidence():
    linked, how, evidence = DL.email_domain_linked("staff.emotres.com", "emotres.com")
    assert linked and how == "same_or_subdomain" and evidence["proofs"][0]["type"] == "domain_hierarchy"
    linked, how, _ = DL.email_domain_linked("company.com", "en.company.com")
    assert linked and how == "same_or_subdomain"
    # A parent/suffix domain such as a public suffix must not inherit an unrelated company subdomain.
    linked, how, _ = DL.email_domain_linked("org.uk", "company.org.uk", context=Context(http=FakeHttp()))
    assert not linked and how == "mx_records_do_not_match"


def test_unrelated_domains_are_unlinked_without_network(monkeypatch):
    monkeypatch.setattr(NET, "_mx_impl", lambda domain: None)
    linked, how, _ = DL.email_domain_linked("unrelated.test", "emotres.com", context=Context(http=FakeHttp()))
    assert not linked and how == "mx_records_do_not_match"


def test_email_and_website_sources_share_the_actual_www_http_path_pages(monkeypatch):
    site = "http://www.emotres.com/about"
    page = "<html>Contact us at help@motresres.com. Acme Drones Ltd HRB 12345.</html>"
    monkeypatch.setattr(NET, "_mx_impl", lambda domain: list(MX))
    monkeypatch.setattr("motor_ai_sim.email_domain._exists", lambda domain, timeout: "yes")
    for order in ("email_first", "website_first"):
        http = FakeHttp({site: page, "http://www.emotres.com/": page})
        cache = {}
        ctx = Context(cache=cache, ttl=3600, http=http)
        app = {"website": site, "legal_name": "Acme Drones Ltd", "reg_no": "HRB 12345", "applicant_name": ""}
        case = {"application": app, "emails": [{"address": "staff@motresres.com", "verified": True}]}
        if order == "email_first":
            EmailSource().run(case, ctx)
            WebsiteSource().run(case, ctx)
        else:
            WebsiteSource().run(case, ctx)
            EmailSource().run(case, ctx)
        assert sum(url == site for url, _kw in http.calls) == 1
        # A later source pass shares successful fetched pages across Context instances in the case TTL.
        ctx2 = Context(cache=cache, ttl=3600, http=http)
        EmailSource().run(case, ctx2)
        assert sum(url == site for url, _kw in http.calls) == 1
