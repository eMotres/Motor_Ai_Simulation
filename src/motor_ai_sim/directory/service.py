"""Service layer for the Companies & People Directory.

Contract: BRIEF_companies_people_catalog.md
- Company, Person, Affiliation, Commercial Documents lifecycle.
- Matching & Deduplication.
- NDA ingestion & backfill.
- Sign-up & e-mail verification domain matching.
- Production linkage (information only).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from motor_ai_sim import nda, users
from motor_ai_sim.directory import matching, store
from motor_ai_sim.directory.models import (
    AFFILIATION_STATUSES,
    COMPANY_ROLES,
    DOCUMENT_KINDS,
    DOCUMENT_STATUSES,
    check_no_bank_fields,
    new_id,
    now_iso,
)

log = logging.getLogger(__name__)


# -- Helper queries -----------------------------------------------------------

def _enrich_company(c: dict, all_affs: list[dict], all_people: list[dict]) -> dict:
    comp = dict(c)
    cid = comp["id"]
    affs = [a for a in all_affs if a.get("company_id") == cid]
    comp["people_count"] = len({a["person_id"] for a in affs if a.get("status") in ("confirmed", "proposed")})
    comp["proposed_count"] = len([a for a in affs if a.get("status") == "proposed"])

    docs = comp.get("documents") or []
    open_docs = [d for d in docs if d.get("status") == "open"]
    paid_docs = [d for d in docs if d.get("status") == "paid"]
    cancelled_docs = [d for d in docs if d.get("status") == "cancelled"]

    comp["documents_summary"] = {
        "total_count": len(docs),
        "open_count": len(open_docs),
        "paid_count": len(paid_docs),
        "cancelled_count": len(cancelled_docs),
        "open_total": sum(float(d.get("total") or 0.0) for d in open_docs),
        "paid_total": sum(float(d.get("total") or 0.0) for d in paid_docs),
        "currency": docs[0].get("currency", "EUR") if docs else "EUR",
    }
    return comp


def get_company_productions(company_id: str) -> list[dict]:
    """Find productions whose members include people from this company (information only)."""
    people_records = get_company_people(company_id)
    emails = set()
    for p in people_records:
        for em in p.get("emails") or []:
            if em:
                emails.add(em.strip().lower())
        if p.get("user_id"):
            emails.add(p["user_id"].strip().lower())
    if not emails:
        return []

    from motor_ai_sim.production import store as prod_store
    root = prod_store.production_root()
    if not root.is_dir():
        return []

    matched = []
    try:
        for d in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
            for c in sorted(p for p in d.iterdir() if p.is_dir() and not p.name.startswith(".")):
                pj = c / "production.json"
                if not pj.is_file():
                    continue
                prod = prod_store._read_json(pj, default={})
                if not isinstance(prod, dict) or not prod.get("die"):
                    continue
                # Check if any member matches
                members = prod.get("members") or []
                matched_members = [
                    m.get("email") for m in members
                    if isinstance(m, dict) and str(m.get("email") or "").strip().lower() in emails
                ]
                if matched_members:
                    matched.append({
                        "die": prod.get("die"),
                        "config": prod.get("config"),
                        "title": prod.get("title") or f"{prod.get('die')} / {prod.get('config')}",
                        "matched_members": matched_members,
                        "members_count": len(members),
                    })
    except Exception as e:
        log.warning("error scanning productions for company %s: %s", company_id, e)
    return matched


def get_company_people(company_id: str) -> list[dict]:
    """Get all persons affiliated with a company, along with their affiliation data."""
    affs = [a for a in store.load_affiliations() if a.get("company_id") == company_id]
    people_map = {p["id"]: p for p in store.load_people()}
    out = []
    for a in affs:
        p = people_map.get(a["person_id"])
        if not p:
            continue
        p_copy = dict(p)
        p_copy["affiliation"] = {
            "id": a["id"],
            "title": a.get("title", ""),
            "status": a.get("status", "proposed"),
            "from_date": a.get("from_date", ""),
            "to_date": a.get("to_date", ""),
            "confirmed_by": a.get("confirmed_by", ""),
            "source": a.get("source", ""),
            "created": a.get("created", ""),
            "updated": a.get("updated", ""),
        }
        out.append(p_copy)
    return out


# -- Companies CRUD -----------------------------------------------------------

def list_companies() -> list[dict]:
    comps = store.load_companies()
    affs = store.load_affiliations()
    people = store.load_people()
    return [_enrich_company(c, affs, people) for c in comps]


def get_company(company_id: str) -> Optional[dict]:
    comps = store.load_companies()
    comp = next((c for c in comps if c.get("id") == company_id), None)
    if not comp:
        return None
    affs = store.load_affiliations()
    people = store.load_people()
    res = _enrich_company(comp, affs, people)
    res["people"] = get_company_people(company_id)
    res["audit_trail"] = store.read_audit(limit=100, target_id=company_id)
    res["productions"] = get_company_productions(company_id)
    return res


def create_company(data: dict, actor: str = "admin") -> dict:
    check_no_bank_fields(data)

    cid = data.get("id") or new_id("comp")
    now = now_iso()

    # Normalize domains
    domains = []
    for d in data.get("domains") or []:
        cd = matching.clean_domain(d)
        if cd and not matching.is_free_mail(cd) and cd not in domains:
            domains.append(cd)
    if data.get("website"):
        wd = matching.clean_domain(data["website"])
        if wd and not matching.is_free_mail(wd) and wd not in domains:
            domains.append(wd)

    company = {
        "id": cid,
        "legal_name": str(data.get("legal_name") or "").strip(),
        "local_name": str(data.get("local_name") or "").strip(),
        "country": str(data.get("country") or "").strip().upper(),
        "address": str(data.get("address") or "").strip(),
        "phone": str(data.get("phone") or "").strip(),
        "website": str(data.get("website") or "").strip(),
        "reg_no": str(data.get("reg_no") or "").strip(),
        "vat_no": matching.clean_vat_no(data.get("vat_no") or ""),
        "roles": [r for r in (data.get("roles") or []) if isinstance(r, str)],
        "notes": str(data.get("notes") or "").strip(),
        "domains": domains,
        "nda": data.get("nda") or {"status": "none"},
        "verification": data.get("verification") or {"status": "none"},
        "documents": data.get("documents") or [],
        "source": data.get("source") or "manual",
        "created": now,
        "updated": now,
    }

    def _add(comps: list[dict]) -> list[dict]:
        comps.append(company)
        return comps

    store.mutate_companies(_add)
    store.append_audit(actor, "company.created", "company", cid, {"legal_name": company["legal_name"], "source": company["source"]})
    return company


def update_company(company_id: str, patch: dict, actor: str = "admin") -> Optional[dict]:
    check_no_bank_fields(patch)
    updated_rec: list[dict] = []

    def _upd(comps: list[dict]) -> list[dict]:
        target = next((c for c in comps if c.get("id") == company_id), None)
        if not target:
            return comps
        for k, v in patch.items():
            if k in ("id", "created", "documents"):
                continue  # immutable or managed separately
            if k == "domains" and isinstance(v, list):
                doms = []
                for d in v:
                    cd = matching.clean_domain(d)
                    if cd and not matching.is_free_mail(cd) and cd not in doms:
                        doms.append(cd)
                target["domains"] = doms
            elif k == "country":
                target["country"] = str(v or "").strip().upper()
            elif k == "vat_no":
                target["vat_no"] = matching.clean_vat_no(str(v or ""))
            else:
                target[k] = v
        target["updated"] = now_iso()
        updated_rec.append(dict(target))
        return comps

    store.mutate_companies(_upd)
    if not updated_rec:
        return None
    store.append_audit(actor, "company.updated", "company", company_id, {"fields": list(patch.keys())})
    return get_company(company_id)


def delete_company(company_id: str, actor: str = "admin") -> bool:
    deleted = []

    def _del(comps: list[dict]) -> list[dict]:
        orig_len = len(comps)
        res = [c for c in comps if c.get("id") != company_id]
        if len(res) < orig_len:
            deleted.append(True)
        return res

    store.mutate_companies(_del)
    if deleted:
        store.append_audit(actor, "company.deleted", "company", company_id, {})
        # Also end any active affiliations
        end_affiliations_for_company(company_id, actor=actor)
        return True
    return False


# -- Commercial Documents CRUD ------------------------------------------------

def add_document(company_id: str, doc_data: dict, actor: str = "admin") -> dict:
    check_no_bank_fields(doc_data)

    doc_id = doc_data.get("id") or new_id("doc")
    lines = []
    total = 0.0
    for l in (doc_data.get("lines") or []):
        qty = float(l.get("qty") or 0.0)
        unit_price = float(l.get("unit_price") or 0.0)
        amt = float(l.get("amount") or (qty * unit_price))
        lines.append({
            "description": str(l.get("description") or "").strip(),
            "qty": qty,
            "unit_price": unit_price,
            "amount": amt,
        })
        total += amt

    # If explicit total given, allow override if non-negative
    if "total" in doc_data and float(doc_data["total"]) >= 0:
        total = float(doc_data["total"])

    status = doc_data.get("status") or "open"
    if status not in DOCUMENT_STATUSES:
        status = "open"

    paid_on = doc_data.get("paid_on")
    if status == "paid" and not paid_on:
        paid_on = now_iso()[:10]

    doc = {
        "id": doc_id,
        "number": str(doc_data.get("number") or "").strip(),
        "date": str(doc_data.get("date") or now_iso()[:10]).strip(),
        "kind": doc_data.get("kind") if doc_data.get("kind") in DOCUMENT_KINDS else "invoice",
        "currency": str(doc_data.get("currency") or "EUR").strip().upper(),
        "total": round(total, 2),
        "lines": lines,
        "terms": str(doc_data.get("terms") or "").strip(),
        "status": status,
        "paid_on": paid_on if status == "paid" else None,
    }

    check_no_bank_fields(doc)

    def _add_doc(comps: list[dict]) -> list[dict]:
        target = next((c for c in comps if c.get("id") == company_id), None)
        if not target:
            raise ValueError(f"Company {company_id} not found")
        docs = list(target.get("documents") or [])
        docs.append(doc)
        target["documents"] = docs
        target["updated"] = now_iso()
        return comps

    store.mutate_companies(_add_doc)
    store.append_audit(actor, "document.added", "company", company_id, {"doc_id": doc_id, "number": doc["number"], "total": doc["total"]})
    return doc


def update_document(company_id: str, doc_id: str, patch: dict, actor: str = "admin") -> Optional[dict]:
    check_no_bank_fields(patch)
    updated_doc: list[dict] = []

    def _upd_doc(comps: list[dict]) -> list[dict]:
        target = next((c for c in comps if c.get("id") == company_id), None)
        if not target:
            return comps
        docs = list(target.get("documents") or [])
        doc = next((d for d in docs if d.get("id") == doc_id), None)
        if not doc:
            return comps
        for k, v in patch.items():
            if k in ("id",):
                continue
            if k == "lines" and isinstance(v, list):
                new_lines = []
                for l in v:
                    qty = float(l.get("qty") or 0.0)
                    up = float(l.get("unit_price") or 0.0)
                    amt = float(l.get("amount") or (qty * up))
                    new_lines.append({
                        "description": str(l.get("description") or "").strip(),
                        "qty": qty,
                        "unit_price": up,
                        "amount": amt,
                    })
                doc["lines"] = new_lines
                if "total" not in patch:
                    doc["total"] = round(sum(l["amount"] for l in new_lines), 2)
            elif k == "status" and v in DOCUMENT_STATUSES:
                doc["status"] = v
                if v == "paid" and not doc.get("paid_on"):
                    doc["paid_on"] = now_iso()[:10]
                elif v != "paid":
                    doc["paid_on"] = None
            elif k == "total":
                doc["total"] = round(float(v), 2)
            else:
                doc[k] = v
        target["documents"] = docs
        target["updated"] = now_iso()
        updated_doc.append(dict(doc))
        return comps

    store.mutate_companies(_upd_doc)
    if not updated_doc:
        return None
    store.append_audit(actor, "document.updated", "company", company_id, {"doc_id": doc_id, "fields": list(patch.keys())})
    return updated_doc[0]


def delete_document(company_id: str, doc_id: str, actor: str = "admin") -> bool:
    deleted = []

    def _del_doc(comps: list[dict]) -> list[dict]:
        target = next((c for c in comps if c.get("id") == company_id), None)
        if not target:
            return comps
        docs = list(target.get("documents") or [])
        new_docs = [d for d in docs if d.get("id") != doc_id]
        if len(new_docs) < len(docs):
            target["documents"] = new_docs
            target["updated"] = now_iso()
            deleted.append(True)
        return comps

    store.mutate_companies(_del_doc)
    if deleted:
        store.append_audit(actor, "document.deleted", "company", company_id, {"doc_id": doc_id})
        return True
    return False


# -- People CRUD --------------------------------------------------------------

def list_people() -> list[dict]:
    people = store.load_people()
    affs = store.load_affiliations()
    comps = {c["id"]: c for c in store.load_companies()}

    out = []
    for p in people:
        pid = p["id"]
        p_affs = [a for a in affs if a.get("person_id") == pid]
        companies_info = []
        for a in p_affs:
            c = comps.get(a.get("company_id"))
            if c:
                companies_info.append({
                    "affiliation_id": a["id"],
                    "company_id": c["id"],
                    "legal_name": c.get("legal_name", ""),
                    "title": a.get("title", ""),
                    "status": a.get("status", "proposed"),
                    "from_date": a.get("from_date", ""),
                    "to_date": a.get("to_date", ""),
                })
        p_dict = dict(p)
        p_dict["companies"] = companies_info
        out.append(p_dict)
    return out


def get_person(person_id: str) -> Optional[dict]:
    people = list_people()
    return next((p for p in people if p.get("id") == person_id), None)


def find_person_by_email(email: str) -> Optional[dict]:
    norm_em = str(email or "").strip().lower()
    if not norm_em:
        return None
    people = store.load_people()
    for p in people:
        em_list = [str(e).strip().lower() for e in (p.get("emails") or [])]
        if norm_em in em_list or str(p.get("user_id") or "").strip().lower() == norm_em:
            return p
    return None


def create_person(data: dict, actor: str = "admin") -> dict:
    check_no_bank_fields(data)

    pid = data.get("id") or new_id("per")
    now = now_iso()

    emails = []
    for em in data.get("emails") or []:
        norm = str(em or "").strip().lower()
        if norm and norm not in emails:
            emails.append(norm)

    # Check link to user account if one exists in users.py
    user_id = str(data.get("user_id") or "").strip().lower()
    if not user_id and emails:
        for em in emails:
            if users.get_user(em):
                user_id = em
                break

    person = {
        "id": pid,
        "name": str(data.get("name") or "").strip(),
        "emails": emails,
        "user_id": user_id,
        "phone": str(data.get("phone") or "").strip(),
        "notes": str(data.get("notes") or "").strip(),
        "source": data.get("source") or "manual",
        "created": now,
        "updated": now,
    }

    def _add_p(people: list[dict]) -> list[dict]:
        people.append(person)
        return people

    store.mutate_people(_add_p)
    store.append_audit(actor, "person.created", "person", pid, {"name": person["name"], "emails": person["emails"]})
    return person


def update_person(person_id: str, patch: dict, actor: str = "admin") -> Optional[dict]:
    check_no_bank_fields(patch)
    updated: list[dict] = []

    def _upd_p(people: list[dict]) -> list[dict]:
        target = next((p for p in people if p.get("id") == person_id), None)
        if not target:
            return people
        for k, v in patch.items():
            if k in ("id", "created"):
                continue
            if k == "emails" and isinstance(v, list):
                ems = []
                for em in v:
                    norm = str(em or "").strip().lower()
                    if norm and norm not in ems:
                        ems.append(norm)
                target["emails"] = ems
            else:
                target[k] = v
        # Re-verify user_id link if needed
        if not target.get("user_id") and target.get("emails"):
            for em in target["emails"]:
                if users.get_user(em):
                    target["user_id"] = em
                    break
        target["updated"] = now_iso()
        updated.append(dict(target))
        return people

    store.mutate_people(_upd_p)
    if not updated:
        return None
    store.append_audit(actor, "person.updated", "person", person_id, {"fields": list(patch.keys())})
    return get_person(person_id)


# -- Affiliations Lifecycle ----------------------------------------------------

def list_affiliations() -> list[dict]:
    return store.load_affiliations()


def list_proposed_affiliations() -> list[dict]:
    affs = [a for a in store.load_affiliations() if a.get("status") == "proposed"]
    comps = {c["id"]: c for c in store.load_companies()}
    people = {p["id"]: p for p in store.load_people()}

    out = []
    for a in affs:
        c = comps.get(a.get("company_id"))
        p = people.get(a.get("person_id"))
        out.append({
            **a,
            "company_name": c.get("legal_name", "") if c else "",
            "company_country": c.get("country", "") if c else "",
            "person_name": p.get("name", "") if p else "",
            "person_emails": p.get("emails", []) if p else [],
            "person_user_id": p.get("user_id", "") if p else "",
        })
    return out


def create_affiliation(
    company_id: str,
    person_id: str,
    *,
    title: str = "",
    from_date: str = "",
    to_date: str = "",
    status: str = "proposed",
    source: str = "manual",
    confirmed_by: str = "",
    actor: str = "admin",
) -> dict:
    aff_id = new_id("aff")
    now = now_iso()
    status = status if status in AFFILIATION_STATUSES else "proposed"

    aff = {
        "id": aff_id,
        "company_id": company_id,
        "person_id": person_id,
        "title": str(title or "").strip(),
        "from_date": str(from_date or now[:10]).strip(),
        "to_date": str(to_date or "").strip(),
        "status": status,
        "confirmed_by": str(confirmed_by or (actor if status == "confirmed" else "")).strip(),
        "source": str(source or "manual").strip(),
        "created": now,
        "updated": now,
    }

    def _add_aff(affs: list[dict]) -> list[dict]:
        # If an active affiliation already exists, update it instead of duplicate
        existing = next((a for a in affs if a.get("company_id") == company_id and a.get("person_id") == person_id), None)
        if existing:
            existing["title"] = aff["title"] or existing.get("title", "")
            if status == "confirmed":
                existing["status"] = "confirmed"
                existing["confirmed_by"] = actor
            existing["updated"] = now
            return affs
        affs.append(aff)
        return affs

    store.mutate_affiliations(_add_aff)
    store.append_audit(actor, "affiliation.created", "affiliation", aff_id, {
        "company_id": company_id,
        "person_id": person_id,
        "status": status,
        "source": source,
    })
    return aff


def confirm_affiliation(affiliation_id: str, actor: str = "admin") -> Optional[dict]:
    updated = []

    def _conf(affs: list[dict]) -> list[dict]:
        target = next((a for a in affs if a.get("id") == affiliation_id), None)
        if not target:
            return affs
        target["status"] = "confirmed"
        target["confirmed_by"] = actor
        target["updated"] = now_iso()
        updated.append(dict(target))
        return affs

    store.mutate_affiliations(_conf)
    if not updated:
        return None
    aff = updated[0]
    store.append_audit(actor, "affiliation.confirmed", "affiliation", affiliation_id, {
        "company_id": aff["company_id"],
        "person_id": aff["person_id"],
    })
    return aff


def end_affiliation(affiliation_id: str, actor: str = "admin", to_date: str = "") -> Optional[dict]:
    updated = []

    def _end(affs: list[dict]) -> list[dict]:
        target = next((a for a in affs if a.get("id") == affiliation_id), None)
        if not target:
            return affs
        target["status"] = "ended"
        target["to_date"] = to_date or now_iso()[:10]
        target["updated"] = now_iso()
        updated.append(dict(target))
        return affs

    store.mutate_affiliations(_end)
    if not updated:
        return None
    aff = updated[0]
    store.append_audit(actor, "affiliation.ended", "affiliation", affiliation_id, {
        "company_id": aff["company_id"],
        "person_id": aff["person_id"],
    })
    return aff


def delete_affiliation(affiliation_id: str, actor: str = "admin") -> bool:
    deleted = []

    def _del(affs: list[dict]) -> list[dict]:
        orig = len(affs)
        res = [a for a in affs if a.get("id") != affiliation_id]
        if len(res) < orig:
            deleted.append(True)
        return res

    store.mutate_affiliations(_del)
    if deleted:
        store.append_audit(actor, "affiliation.deleted", "affiliation", affiliation_id, {})
        return True
    return False


def end_affiliations_for_company(company_id: str, actor: str = "admin") -> None:
    now = now_iso()

    def _end_all(affs: list[dict]) -> list[dict]:
        for a in affs:
            if a.get("company_id") == company_id and a.get("status") != "ended":
                a["status"] = "ended"
                a["to_date"] = now[:10]
                a["updated"] = now
        return affs

    store.mutate_affiliations(_end_all)


# -- Invitations & Email additions ---------------------------------------------

def add_person_to_company_by_email(company_id: str, email: str, name: str = "", title: str = "", actor: str = "admin") -> dict:
    """Admin adds a person to a company by e-mail (an invitation).

    Contract:
    - If the user account exists and is already verified, status becomes 'confirmed'.
    - If the user has not registered yet (or is unverified), status is 'proposed' with source 'invitation'.
      The affiliation automatically becomes 'confirmed' when that e-mail registers or confirms.
    """
    norm_em = str(email or "").strip().lower()
    if not norm_em:
        raise ValueError("e-mail address is required")

    person = find_person_by_email(norm_em)
    if not person:
        person = create_person({
            "name": name or norm_em.split("@")[0],
            "emails": [norm_em],
            "source": "invitation",
        }, actor=actor)

    # Check if account is verified
    u_rec = users.get_user(norm_em)
    is_ver = bool(u_rec and users.is_verified(u_rec))

    status = "confirmed" if is_ver else "proposed"
    aff = create_affiliation(
        company_id=company_id,
        person_id=person["id"],
        title=title,
        status=status,
        source="invitation",
        confirmed_by=actor if is_ver else "",
        actor=actor,
    )
    return {"person": person, "affiliation": aff}


# -- NDA Ingestion & Backfill --------------------------------------------------

def ingest_nda_record(rec: dict, intake: Optional[dict] = None, actor: str = "system") -> dict:
    """Process one signed NDA record into the Companies and People catalogs.

    - Matches existing company by reg_no / VAT / normalized name / website domain; never duplicates.
    - Creates or updates the Person for the signatory.
    - Creates an affiliation with status 'confirmed' (source 'nda').
    - Reads verification case data (if available) for VAT / website / status.
    """
    intake = intake or {}
    company_name = str(rec.get("company") or "").strip()
    if not company_name:
        return {"ok": False, "reason": "empty company name"}

    user_email = str(rec.get("email") or rec.get("user") or "").strip().lower()
    signatory = str(rec.get("signatory") or "").strip()
    country = str(rec.get("country") or "").strip().upper()
    address = str(rec.get("address") or "").strip()
    reg_no = str(rec.get("reg_no") or "").strip()
    title = str(rec.get("title") or "").strip()
    nda_id = rec.get("id", "")
    nda_iso = rec.get("iso", "")
    nda_version = rec.get("version", "")

    # Look up verification case if one exists
    vat_no = str(intake.get("vat_no") or "").strip()
    website = str(intake.get("website") or "").strip()
    verif_case = None

    try:
        from motor_ai_sim.verification import store as verif_store
        cases = verif_store.cases_of(user_email)
        if cases:
            verif_case = max(cases, key=lambda c: c.get("created", 0))
            if not vat_no:
                vat_no = str(verif_case.get("application", {}).get("vat_no") or "")
            if not website:
                website = str(verif_case.get("application", {}).get("website") or "")
    except Exception as e:
        log.warning("could not read verification case for nda %s: %s", nda_id, e)

    email_dom = matching.clean_domain(user_email) if not matching.is_free_mail(user_email) else ""
    web_dom = matching.clean_domain(website) if not matching.is_free_mail(website) else ""

    # Match existing company
    companies = store.load_companies()
    matched = matching.find_matching_company(
        companies,
        reg_no=reg_no,
        vat_no=vat_no,
        legal_name=company_name,
        domain=web_dom or email_dom,
        website=website,
        country=country,
    )

    nda_block = {
        "status": "signed",
        "date": nda_iso[:10] if nda_iso else now_iso()[:10],
        "record_id": nda_id,
        "version": nda_version,
    }
    verif_block = {
        "status": verif_case.get("status", "none") if verif_case else "none",
        "case_id": verif_case.get("id", "") if verif_case else "",
    }

    if matched:
        company_id = matched["id"]
        patch: dict[str, Any] = {"nda": nda_block}
        if verif_case:
            patch["verification"] = verif_block
        if country and not matched.get("country"):
            patch["country"] = country
        if address and not matched.get("address"):
            patch["address"] = address
        if reg_no and not matched.get("reg_no"):
            patch["reg_no"] = reg_no
        if vat_no and not matched.get("vat_no"):
            patch["vat_no"] = vat_no
        if website and not matched.get("website"):
            patch["website"] = website

        # Merge domains
        cur_domains = list(matched.get("domains") or [])
        for dom in (email_dom, web_dom):
            if dom and not matching.is_free_mail(dom) and dom not in cur_domains:
                cur_domains.append(dom)
        patch["domains"] = cur_domains

        update_company(company_id, patch, actor=actor)
        company = get_company(company_id)
    else:
        domains = []
        for dom in (email_dom, web_dom):
            if dom and not matching.is_free_mail(dom) and dom not in domains:
                domains.append(dom)

        company = create_company({
            "legal_name": company_name,
            "country": country,
            "address": address,
            "reg_no": reg_no,
            "vat_no": vat_no,
            "website": website,
            "roles": ["customer"],
            "domains": domains,
            "nda": nda_block,
            "verification": verif_block,
            "source": "nda",
        }, actor=actor)
        company_id = company["id"]

    # Signatory becomes a Person
    person = find_person_by_email(user_email)
    if not person:
        person = create_person({
            "name": signatory or user_email.split("@")[0],
            "emails": [user_email],
            "source": "nda",
        }, actor=actor)
    elif signatory and person.get("name") != signatory:
        update_person(person["id"], {"name": signatory}, actor=actor)

    # Affiliation (many-to-many, confirmed)
    affs = store.load_affiliations()
    existing_aff = next(
        (a for a in affs if a.get("company_id") == company_id and a.get("person_id") == person["id"]),
        None,
    )
    if existing_aff:
        if existing_aff.get("status") != "confirmed":
            confirm_affiliation(existing_aff["id"], actor=actor)
        aff = existing_aff
    else:
        aff = create_affiliation(
            company_id=company_id,
            person_id=person["id"],
            title=title,
            status="confirmed",
            source="nda",
            confirmed_by="nda",
            actor=actor,
        )

    return {"ok": True, "company_id": company_id, "person_id": person["id"], "affiliation_id": aff["id"]}


def backfill_nda(actor: str = "admin") -> dict:
    """One-shot backfill for all existing NDA records."""
    records = nda.read_records(100000)
    # Records are returned newest first in read_records; reverse to process in chronological order
    chrono_records = list(reversed(records))

    initial_comps = len(store.load_companies())
    initial_people = len(store.load_people())
    initial_affs = len(store.load_affiliations())

    processed = 0
    for r in chrono_records:
        try:
            res = ingest_nda_record(r, actor=actor)
            if res.get("ok"):
                processed += 1
        except Exception as e:
            log.warning("error in nda backfill for record %s: %s", r.get("id"), e)

    final_comps = len(store.load_companies())
    final_people = len(store.load_people())
    final_affs = len(store.load_affiliations())

    summary = {
        "processed_records": processed,
        "companies_created": final_comps - initial_comps,
        "people_created": final_people - initial_people,
        "affiliations_created": final_affs - initial_affs,
        "total_companies": final_comps,
        "total_people": final_people,
        "total_affiliations": final_affs,
    }
    store.append_audit(actor, "backfill.nda", "directory", "all", summary)
    return summary


# -- Sign-up & Verification hooks ----------------------------------------------

def on_user_registered(email: str, name: str = "", source: str = "signup") -> None:
    """Called on new user registration.

    - Free-mail domains (gmail, qq, 163, ...) NEVER auto-match;
    - If a registering user's email domain matches a company's domains, create an affiliation 'proposed';
    - If an admin previously invited this email to a company, the affiliation becomes 'confirmed' when they register.
    """
    norm_em = str(email or "").strip().lower()
    if not norm_em:
        return

    # 1. Find or create Person
    person = find_person_by_email(norm_em)
    if not person:
        person = create_person({
            "name": name or norm_em.split("@")[0],
            "emails": [norm_em],
            "user_id": norm_em,
            "source": source,
        }, actor="system")
    elif not person.get("user_id"):
        update_person(person["id"], {"user_id": norm_em}, actor="system")

    # 2. Check for invitations waiting for this person/email
    affs = store.load_affiliations()
    for a in affs:
        if a.get("person_id") == person["id"] and a.get("status") == "proposed" and a.get("source") == "invitation":
            confirm_affiliation(a["id"], actor="signup")

    # 3. Check domain match (free-mail never auto-matches)
    if matching.is_free_mail(norm_em):
        return

    domain = matching.clean_domain(norm_em)
    if not domain:
        return

    companies = store.load_companies()
    matching_companies = []
    for c in companies:
        c_domains = [matching.clean_domain(d) for d in (c.get("domains") or [])]
        if domain in c_domains:
            matching_companies.append(c)

    for c in matching_companies:
        # Check if already affiliated
        existing = next(
            (a for a in affs if a.get("company_id") == c["id"] and a.get("person_id") == person["id"]),
            None,
        )
        if not existing:
            create_affiliation(
                company_id=c["id"],
                person_id=person["id"],
                status="proposed",
                source="domain_match",
                actor="system",
            )


def on_user_confirmed(email: str) -> None:
    """Called when user confirms email or is verified by admin.

    - Any pending invitation becomes confirmed.
    - Links user_id if not linked.
    """
    norm_em = str(email or "").strip().lower()
    if not norm_em:
        return

    person = find_person_by_email(norm_em)
    if person and not person.get("user_id"):
        update_person(person["id"], {"user_id": norm_em}, actor="system")

    if person:
        affs = store.load_affiliations()
        for a in affs:
            if a.get("person_id") == person["id"] and a.get("status") == "proposed" and a.get("source") == "invitation":
                confirm_affiliation(a["id"], actor="email_confirm")
