"""Tests for Companies & People Directory module (src/motor_ai_sim/directory/).

Contract & Verification: BRIEF_companies_people_catalog.md
- dedup matching
- the NDA backfill
- domain auto-proposal (and free-mail never matching)
- invitation confirmation
- admin-only access
- no bank fields accepted
"""
from __future__ import annotations

import json
import pytest
from fastapi.testclient import TestClient

from motor_ai_sim import auth
from motor_ai_sim import nda as N
from motor_ai_sim import users as U
from motor_ai_sim.api import app
from motor_ai_sim.directory import matching, service, store
from motor_ai_sim.directory.models import check_no_bank_fields

ADMIN = "admin@example.com"
USER = "engineer@example.com"
NON_ADMIN = "regular@example.com"

client = TestClient(app)


@pytest.fixture()
def dir_env(tmp_path, monkeypatch):
    """Set up sandboxed directory, legal, users, and auth stores."""
    dir_root = tmp_path / "directory"
    dir_root.mkdir(parents=True, exist_ok=True)
    legal_root = tmp_path / "legal"
    legal_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("MOTRES_DIRECTORY_ROOT", str(dir_root))
    monkeypatch.setenv("MOTRES_LEGAL_ROOT", str(legal_root))
    monkeypatch.setattr(U, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setenv("AUTH_SECRET", "test-secret-key-1234567890")
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {ADMIN})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", True)

    U.create_user(ADMIN, "Password123!", role="admin", name="Admin User")
    U.create_user(USER, "Password123!", role="user", name="Engineer One")
    U.create_user(NON_ADMIN, "Password123!", role="user", name="Regular User")

    def _headers(email: str):
        token = U.issue_token(email)
        return {"Authorization": f"Bearer {token}"}

    return {
        "dir_root": dir_root,
        "legal_root": legal_root,
        "admin_h": _headers(ADMIN),
        "user_h": _headers(USER),
        "non_admin_h": _headers(NON_ADMIN),
    }


# ==============================================================================
# 1. Deduplication Matching Tests
# ==============================================================================

def test_dedup_matching_by_reg_no(dir_env):
    """Companies with matching registration numbers (cleaned, ignoring whitespace/casing/dots) must match."""
    c1 = service.create_company({
        "legal_name": "Stator Dynamics GmbH",
        "reg_no": "HRB 12345 B",
        "country": "DE",
    })

    companies = store.load_companies()
    # Same reg_no, different spacing / punctuation
    matched = matching.find_matching_company(
        companies,
        reg_no="hrb-12345-b",
        country="DE",
    )
    assert matched is not None
    assert matched["id"] == c1["id"]

    # Different reg_no should not match
    assert matching.find_matching_company(companies, reg_no="HRB 99999", country="DE") is None


def test_dedup_matching_by_vat_no(dir_env):
    """Companies with matching VAT numbers (cleaned alphanumeric) must match."""
    c1 = service.create_company({
        "legal_name": "Volt Components S.L.",
        "vat_no": "ES-B12345678",
        "country": "ES",
    })

    companies = store.load_companies()
    matched = matching.find_matching_company(
        companies,
        vat_no="es b12345678",
        country="ES",
    )
    assert matched is not None
    assert matched["id"] == c1["id"]


def test_dedup_matching_by_normalized_name(dir_env):
    """Companies with equivalent normalized names (ignoring legal suffixes like Ltd, GmbH, LLC) must match."""
    c1 = service.create_company({
        "legal_name": "Apex Drivetrain Technologies Limited",
        "country": "GB",
    })

    companies = store.load_companies()
    matched = matching.find_matching_company(
        companies,
        legal_name="Apex Drivetrain Technologies Ltd",
        country="GB",
    )
    assert matched is not None
    assert matched["id"] == c1["id"]


def test_dedup_matching_by_website_domain(dir_env):
    """Corporate website domain or explicit domain matches existing company (never free-mail)."""
    c1 = service.create_company({
        "legal_name": "Quantum Stator Corp",
        "website": "https://www.quantumstator.com/en/about",
        "domains": ["quantumstator.com"],
    })

    companies = store.load_companies()
    # Query with subpage URL
    matched = matching.find_matching_company(
        companies,
        domain="quantumstator.com",
    )
    assert matched is not None
    assert matched["id"] == c1["id"]

    # Free-mail never matches by domain
    matched_free = matching.find_matching_company(
        companies,
        domain="gmail.com",
    )
    assert matched_free is None


# ==============================================================================
# 2. NDA Backfill Tests
# ==============================================================================

def test_nda_backfill_and_ingestion(dir_env):
    """Verify that NDA signatures create/deduplicate companies and signatories become confirmed people."""
    legal_root = dir_env["legal_root"]
    sig_file = legal_root / "nda_signatures.jsonl"

    rec1 = {
        "id": "n20261017-000000000001",
        "iso": "2026-10-17T10:00:00Z",
        "company": "Bavaria Motor Works GmbH",
        "reg_no": "HRB 7777",
        "country": "DE",
        "address": "München, Germany",
        "signatory": "Hans Schmidt",
        "title": "Head of Engineering",
        "email": "h.schmidt@bmw-test.de",
        "user": "h.schmidt@bmw-test.de",
        "version": "v1-2026-10",
    }
    # Second signature for SAME company by another signatory
    rec2 = {
        "id": "n20261017-000000000002",
        "iso": "2026-10-17T11:00:00Z",
        "company": "Bavaria Motor Works",
        "reg_no": "HRB 7777",
        "country": "DE",
        "address": "München, Germany",
        "signatory": "Greta Weber",
        "title": "Procurement Lead",
        "email": "g.weber@bmw-test.de",
        "user": "g.weber@bmw-test.de",
        "version": "v1-2026-10",
    }
    # Different company
    rec3 = {
        "id": "n20261017-000000000003",
        "iso": "2026-10-17T12:00:00Z",
        "company": "Shenzhen Rotor Tech Co., Ltd",
        "reg_no": "91440300MA5XXXXXX",
        "country": "CN",
        "address": "Shenzhen, Guangdong",
        "signatory": "Li Wei",
        "title": "General Manager",
        "email": "li.wei@sz-rotor-test.cn",
        "user": "li.wei@sz-rotor-test.cn",
        "version": "v1-2026-10",
    }

    sig_file.write_text("\n".join(json.dumps(r) for r in (rec1, rec2, rec3)) + "\n", encoding="utf-8")

    summary = service.backfill_nda(actor="admin")
    assert summary["processed_records"] == 3
    assert summary["total_companies"] == 2  # rec1 and rec2 deduplicated into 1 company!
    assert summary["total_people"] == 3     # 3 distinct signatories
    assert summary["total_affiliations"] == 3

    # Check the deduplicated company
    comps = store.load_companies()
    bmw = next(c for c in comps if "Bavaria" in c["legal_name"])
    assert bmw["nda"]["status"] == "signed"
    assert "bmw-test.de" in bmw["domains"]

    # Check affiliations for BMW company
    affs = [a for a in store.load_affiliations() if a["company_id"] == bmw["id"]]
    assert len(affs) == 2
    assert all(a["status"] == "confirmed" for a in affs)
    assert all(a["source"] == "nda" for a in affs)


# ==============================================================================
# 3. Domain Auto-proposal & Free-mail Isolation Tests
# ==============================================================================

def test_domain_auto_proposal_and_freemail_never_matching(dir_env):
    """When a registering user's email domain matches a company's domains, create a 'proposed' affiliation.

    Free-mail domains (gmail, qq, 163, etc.) NEVER auto-match.
    """
    comp = service.create_company({
        "legal_name": "AeroStator Partner Corp",
        "domains": ["aeropartner.com"],
        "country": "US",
    })

    # 1. Register corporate email matching domain
    service.on_user_registered("alice@aeropartner.com", name="Alice Smith")

    affs = store.load_affiliations()
    alice_aff = next((a for a in affs if a["company_id"] == comp["id"]), None)
    assert alice_aff is not None
    assert alice_aff["status"] == "proposed"
    assert alice_aff["source"] == "domain_match"

    # 2. Register free-mail email
    for free_email in ("bob@gmail.com", "charlie@qq.com", "david@163.com", "eva@proton.me"):
        service.on_user_registered(free_email, name="Free Mail User")
        free_person = service.find_person_by_email(free_email)
        assert free_person is not None
        # Must have NO auto-created affiliation
        free_affs = [a for a in store.load_affiliations() if a["person_id"] == free_person["id"]]
        assert len(free_affs) == 0


# ==============================================================================
# 4. Invitation Confirmation Tests
# ==============================================================================

def test_invitation_confirmation_lifecycle(dir_env):
    """Admin adds an unregistered person by e-mail (an invitation): affiliation is proposed.

    When that email registers or confirms, the affiliation becomes 'confirmed'.
    """
    comp = service.create_company({
        "legal_name": "Supplier Dynamics",
        "country": "IT",
    })

    invite_email = "contractor@supplier-dyn.it"

    # Admin invites person
    res = service.add_person_to_company_by_email(
        comp["id"],
        email=invite_email,
        name="Contractor Name",
        title="Senior Electrical Engineer",
        actor="admin@example.com",
    )
    aff = res["affiliation"]
    person = res["person"]
    assert aff["status"] == "proposed"
    assert aff["source"] == "invitation"

    # Later, person registers
    service.on_user_registered(invite_email, name="Contractor Registered")

    # Affiliation should now be confirmed!
    updated_affs = store.load_affiliations()
    confirmed_aff = next(a for a in updated_affs if a["id"] == aff["id"])
    assert confirmed_aff["status"] == "confirmed"


def test_invitation_confirmation_on_email_confirm(dir_env):
    """If user was unverified when invited, email confirmation transitions affiliation to 'confirmed'."""
    comp = service.create_company({
        "legal_name": "Electro Rotor Inc",
        "country": "FR",
    })

    email = "engineer@rotor-france.fr"
    # Unverified user in users.py (registered via register_self)
    U.register_self(email, "Password123!", name="French Engineer")

    res = service.add_person_to_company_by_email(
        comp["id"],
        email=email,
        actor="admin@example.com",
    )
    assert res["affiliation"]["status"] == "proposed"

    # Confirm email
    U.mark_verified(email, by="link")

    updated_affs = store.load_affiliations()
    aff = next(a for a in updated_affs if a["id"] == res["affiliation"]["id"])
    assert aff["status"] == "confirmed"


# ==============================================================================
# 5. Admin-only Access Tests
# ==============================================================================

def test_admin_only_access(dir_env):
    """All directory management endpoints must require role == admin."""
    endpoints = [
        ("GET", "/api/admin/directory/companies"),
        ("POST", "/api/admin/directory/companies"),
        ("GET", "/api/admin/directory/people"),
        ("POST", "/api/admin/directory/people"),
        ("GET", "/api/admin/directory/affiliations/proposed"),
        ("POST", "/api/admin/directory/backfill/nda"),
    ]

    for method, url in endpoints:
        # Anonymous -> 401
        if method == "GET":
            r_anon = client.get(url)
        else:
            r_anon = client.post(url, json={})
        assert r_anon.status_code == 401, f"{method} {url} allowed anonymous"

        # Regular user (non-admin) -> 403
        if method == "GET":
            r_user = client.get(url, headers=dir_env["non_admin_h"])
        else:
            r_user = client.post(url, json={}, headers=dir_env["non_admin_h"])
        assert r_user.status_code == 403, f"{method} {url} allowed non-admin"

    # Admin access -> allowed (200)
    r_admin_get = client.get("/api/admin/directory/companies", headers=dir_env["admin_h"])
    assert r_admin_get.status_code == 200


# ==============================================================================
# 6. No Bank Fields Accepted Tests
# ==============================================================================

def test_no_bank_fields_accepted(dir_env):
    """Never store bank account numbers / SWIFT details. Any attempt is rejected with 422."""
    admin_h = dir_env["admin_h"]

    forbidden_payloads = [
        {"legal_name": "Comp A", "bank": "Deutsche Bank"},
        {"legal_name": "Comp B", "iban": "DE89370400440532013000"},
        {"legal_name": "Comp C", "swift": "DEUTDEDB"},
        {"legal_name": "Comp D", "bank_account": "123456789"},
        {"legal_name": "Comp E", "account_number": "987654321"},
        {"legal_name": "Comp F", "routing_number": "021000021"},
        {"legal_name": "Comp G", "notes": "Please transfer to IBAN: DE12345678901234567890"},
    ]

    for payload in forbidden_payloads:
        r = client.post("/api/admin/directory/companies", json=payload, headers=admin_h)
        assert r.status_code == 422, f"Payload {payload} should have been rejected with 422, got {r.status_code}"

    # Also test commercial document bank rejection
    comp = service.create_company({"legal_name": "Safe Company", "country": "DE"})
    doc_with_bank = {
        "number": "INV-2026-001",
        "date": "2026-10-17",
        "total": 5000.0,
        "iban": "DE89370400440532013000",
        "lines": [{"description": "Motor prototype assembly", "qty": 1, "unit_price": 5000.0, "amount": 5000.0}],
    }
    r_doc = client.post(f"/api/admin/directory/companies/{comp['id']}/documents", json=doc_with_bank, headers=admin_h)
    assert r_doc.status_code == 422

    # Direct model validator test
    with pytest.raises(ValueError):
        check_no_bank_fields({"swift_bic": "CHASUS33"})


# ==============================================================================
# 7. Commercial Documents Lifecycle & Totals
# ==============================================================================

def test_commercial_documents_lifecycle(dir_env):
    """Commercial documents: add, compute total, update status, paid_on tracking."""
    admin_h = dir_env["admin_h"]

    comp = service.create_company({"legal_name": "Motor Assembler Ltd", "country": "DE"})
    cid = comp["id"]

    doc_data = {
        "number": "INV-1001",
        "date": "2026-10-15",
        "kind": "invoice",
        "currency": "EUR",
        "lines": [
            {"description": "Stator core lamination stack", "qty": 10, "unit_price": 150.0, "amount": 1500.0},
            {"description": "Rotor assembly labor", "qty": 5, "unit_price": 200.0, "amount": 1000.0},
        ],
        "terms": "Net 30 days",
        "status": "open",
    }

    r_create = client.post(f"/api/admin/directory/companies/{cid}/documents", json=doc_data, headers=admin_h)
    assert r_create.status_code == 200
    doc = r_create.json()
    assert doc["total"] == 2500.0
    assert doc["status"] == "open"
    assert doc["paid_on"] is None

    # Mark as paid
    r_patch = client.patch(
        f"/api/admin/directory/companies/{cid}/documents/{doc['id']}",
        json={"status": "paid"},
        headers=admin_h,
    )
    assert r_patch.status_code == 200
    updated_doc = r_patch.json()
    assert updated_doc["status"] == "paid"
    assert updated_doc["paid_on"] is not None

    # Check enriched company list summary
    r_list = client.get("/api/admin/directory/companies", headers=admin_h)
    c_summary = next(c for c in r_list.json()["companies"] if c["id"] == cid)
    assert c_summary["documents_summary"]["paid_count"] == 1
    assert c_summary["documents_summary"]["paid_total"] == 2500.0


def test_company_verification_shows_the_case_status_now(monkeypatch):
    """The block is written at NDA time; the admin's later decision lands on the case only (owner 2026-10-27:
    companies showed PENDING with their case long VERIFIED)."""
    from motor_ai_sim.directory import service as S
    from motor_ai_sim.verification import store as VS
    cases = {"v20261025-aaaaaaaaaaaa": {"status": "VERIFIED"}, "v20261025-bbbbbbbbbbbb": {"status": "DENIED"}}
    monkeypatch.setattr(VS, "get", lambda cid: cases.get(cid))
    comp = {"id": "c1", "verification": {"status": "PENDING", "case_id": "v20261025-aaaaaaaaaaaa"}}
    assert S._enrich_company(comp, [], [])["verification"]["status"] == "VERIFIED"
    comp["verification"]["case_id"] = "v20261025-bbbbbbbbbbbb"
    assert S._enrich_company(comp, [], [])["verification"]["status"] == "DENIED"
    comp["verification"]["case_id"] = "v20261025-cccccccccccc"          # case missing: the stored status stays
    assert S._enrich_company(comp, [], [])["verification"]["status"] == "PENDING"
    assert S._enrich_company({"id": "c2", "verification": {"status": "none"}}, [], [])["verification"]["status"] == "none"
