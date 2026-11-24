"""The "Contract" section of a project: /api/projects/{id}/contract - who reads it, uploads, the signing a PERSON confirms, versions,
the stale-page guard, downloads, the audit.  Fixture files are real documents made with the libraries the application already has
(ReportLab, python-docx, Pillow)."""
from __future__ import annotations

import io
import json
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from motor_ai_sim import auth
from motor_ai_sim.development import access, store
from motor_ai_sim.development import project_contract as PC

OWNER = "owner@example.test"
MEMBER = "member@example.test"
OUTSIDER = "outsider@example.test"
STAFF = "engineer@example.test"
FACTORY = "factory@example.test"
ROLES = {"t-owner": (OWNER, "user"), "t-member": (MEMBER, "user"), "t-outsider": (OUTSIDER, "user"), "t-staff": (STAFF, "engineer"),
         "t-oem": (FACTORY, "oem")}
URL = "/api/projects/proj1/contract"


# ── real fixture files ──────────────────────────────────────────────────────────────────────────────────────────────

def _jpeg(progressive: bool = True, size=(320, 200)) -> bytes:
    """A photo-style JPEG: progressive, with EXIF."""
    from PIL import Image
    im = Image.new("RGB", size, (235, 232, 225))
    for x in range(0, size[0], 7):
        for y in range(0, size[1], 11):
            im.putpixel((x, y), (20, 20, 30))
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"                                # Make
    exif[0x0110] = "Scanner 1"                                 # Model
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85, progressive=progressive, exif=exif.tobytes())
    return buf.getvalue()


def _png() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("L", (40, 30), 200).save(buf, "PNG")
    return buf.getvalue()


def _pdf(text: str = "Agreement") -> bytes:
    """A ReportLab PDF with text and a scanned-page image on it, like a phone-scan app exports."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.drawString(72, 800, text)
    c.drawString(72, 780, "<script> literal text is fine in a real PDF")
    c.drawImage(ImageReader(io.BytesIO(_jpeg(False))), 72, 400, width=300, height=200)
    c.showPage()
    c.save()
    return buf.getvalue()


def _docx() -> bytes:
    import docx
    d = docx.Document()
    d.add_heading("Agreement", 1)
    d.add_paragraph("Standard terms.")
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


PDF = _pdf()


def hdr(who: str) -> dict:
    return {"Authorization": "Bearer " + who}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("CAD_DOWNLOAD_LOG", str(tmp_path / "downloads.jsonl"))
    monkeypatch.delenv("MOTOR_AI_SIM_PRIVATE_DATA", raising=False)
    monkeypatch.delenv("MOTRES_CONTRACT_TEMPLATES_ROOT", raising=False)
    monkeypatch.setenv("MOTRES_CONTRACT_STORE_ROOT", str(tmp_path / "shared" / "contract"))

    def resolve(token):
        if not token:
            return None
        email, role = ROLES[token.split()[-1]]
        return {"email": email, "role": role}

    monkeypatch.setattr(auth, "resolve_user", resolve)
    store.create_project({"id": "proj1", "title": "Project", "customer_org": "Org", "status": "simulation", "created_at": "t",
                          "updated_at": "t", "owner_engineer": OWNER, "linked_motors": [], "notes": "", "pending_invites": [],
                          "members": [{"email": OWNER, "role": "owner"}, {"email": MEMBER, "role": "customer"}]})
    app = FastAPI()
    app.include_router(PC.router)
    return TestClient(app)


def seen(client) -> dict:
    body = client.get(URL, headers=hdr("t-staff")).json()
    return {"revision": body["revision"], "terms_digest": body["terms_digest"]}


def up(client, kind, who="t-staff", name="agreement.pdf", data=PDF, at=None, **fields):
    s = seen(client) if at is None else at
    return client.post(URL + "/files", headers=hdr(who), files={"file": (name, data)},
                       data={"kind": kind, "revision": str(s["revision"]), "terms_digest": s["terms_digest"], **fields})


def status(client, value, who="t-staff", at=None):
    return client.post(URL + "/status", headers=hdr(who), json={"status": value, **(seen(client) if at is None else at)})


def new_revision(client, who="t-staff", at=None):
    return client.post(URL + "/status", headers=hdr(who), json={"new_revision": True, **(seen(client) if at is None else at)})


def confirm(client, who="t-staff", scan=None, at=None):
    body = client.get(URL, headers=hdr("t-staff")).json()
    scan = scan or body["pending_scan"]
    return client.post(URL + "/confirm", headers=hdr(who), json={"file_id": scan["id"], "sha256": scan["sha256"],
                                                                 **(seen(client) if at is None else at)})


def reject(client, reason="unsigned page 2", who="t-staff"):
    scan = client.get(URL, headers=hdr("t-staff")).json()["pending_scan"]
    return client.post(URL + "/reject", headers=hdr(who), json={"file_id": scan["id"], "sha256": scan["sha256"], "reason": reason,
                                                                **seen(client)})


def audit_actions(tmp_path):
    out = []
    for line in (tmp_path / "projects" / "proj1" / "audit.jsonl").read_bytes().decode("utf-8", errors="replace").splitlines():
        try:
            out.append(json.loads(line)["action"])
        except ValueError:
            continue
    return out


def doc(tmp_path):
    return json.loads((tmp_path / "projects" / "proj1" / "contract" / "contract.json").read_text())


# ── who reads it ─────────────────────────────────────────────────────────────────────────────────────────────────────

def test_owner_and_staff_read_it_other_members_and_outsiders_get_404(client):
    assert client.get(URL, headers=hdr("t-owner")).status_code == 200
    assert client.get(URL, headers=hdr("t-staff")).status_code == 200
    assert client.get(URL, headers=hdr("t-member")).status_code == 404
    assert client.get(URL, headers=hdr("t-outsider")).status_code == 404
    assert client.get(URL).status_code == 401


def test_the_sees_prices_switch_decides_for_members(client):
    """The member flag is the central rule's grant (PR #800): on, the member reads; off again, 404; staff always."""
    def flag(p):
        for m in p["members"]:
            if m["email"] == MEMBER:
                m["sees_prices"] = True
    store.mutate_project("proj1", flag)
    assert client.get(URL, headers=hdr("t-member")).status_code == 200
    def unflag(p):
        for m in p["members"]:
            m.pop("sees_prices", None)
    store.mutate_project("proj1", unflag)
    assert client.get(URL, headers=hdr("t-member")).status_code == 404
    assert client.get(URL, headers=hdr("t-staff")).status_code == 200            # staff always


def test_may_read_is_the_central_rule(client):
    project = store.get_project("proj1")
    for who in ("t-owner", "t-member", "t-outsider", "t-staff", "t-oem"):
        user = {"email": ROLES[who][0], "role": ROLES[who][1]}
        assert PC.may_read(user, project) == access.sees_prices(user, project), who


@pytest.mark.parametrize("membership", [None, "customer", "owner"])
def test_a_factory_account_never_sees_the_contract(client, membership):
    fid = up(client, "agreement").json()["current"]["agreement"]
    if membership:
        store.mutate_project("proj1", lambda p: p["members"].append({"email": FACTORY, "role": membership, "sees_prices": True}))
    assert client.get(URL, headers=hdr("t-oem")).status_code == 404         # flag or owner role or not: identity first
    assert client.get(f"{URL}/files/{fid}/download", headers=hdr("t-oem")).status_code == 404
    assert up(client, "signed_customer", who="t-oem").status_code == 404


def test_a_new_contract_is_a_draft_on_the_simulation_step(client):
    body = client.get(URL, headers=hdr("t-owner")).json()
    assert body["status"] == "draft" and body["history"] == [] and body["files"] == [] and body["pending_scan"] is None
    assert body["step"] == "simulation_agreement" and body["steps"][0] == "specification" and body["steps"][-1] == "series"
    assert body["can_manage"] is False and body["upload_kinds"] == [] and body["staff_scheme"] is None
    assert len(body["terms_digest"]) == 16


# ── signing: a person confirms ───────────────────────────────────────────────────────────────────────────────────────

def test_signing_is_confirmed_by_a_person_for_that_exact_scan(client, tmp_path):
    assert status(client, "sent").status_code == 409                          # no agreement yet
    agreement = up(client, "agreement").json()["current"]["agreement"]
    assert up(client, "signed_customer", who="t-owner").status_code == 409    # nothing sent yet: no scan
    r = status(client, "sent")
    assert r.status_code == 200 and r.json()["status"] == "sent"
    assert [f["id"] for f in r.json()["history"][-1]["files"]] == [agreement]
    r = up(client, "signed_customer", who="t-owner", name="signed.pdf")
    body = r.json()
    assert r.status_code == 200 and body["status"] == "sent"                   # a scan is NOT a signature
    assert body["pending_scan"]["name"] == "signed.pdf" and body["history"][-1]["status"] == "scan_uploaded"
    assert up(client, "signed_customer", who="t-owner", name="again.pdf").status_code == 409   # one awaiting at a time
    assert confirm(client, who="t-owner").status_code == 403                  # the customer side never sets a signed state
    other = {"id": agreement, "sha256": body["pending_scan"]["sha256"]}
    assert confirm(client, scan=other).status_code == 409                     # exactly the awaiting file
    r = confirm(client)
    assert r.status_code == 200 and r.json()["status"] == "signed_customer" and r.json()["pending_scan"] is None
    assert r.json()["history"][-1]["by"] == STAFF and r.json()["step"] == "contract_payment"
    assert up(client, "signed_motres", name="countersigned.pdf").json()["status"] == "signed_customer"
    r = confirm(client)
    assert r.json()["status"] == "signed_both"
    assert [h["status"] for h in r.json()["history"]] == ["sent", "scan_uploaded", "signed_customer", "scan_uploaded", "signed_both"]
    actions = audit_actions(tmp_path)
    assert actions.count("contract.status") == 3 and actions.count("contract.upload") == 3


def test_a_rejected_scan_stays_in_the_history_and_the_status_stays(client):
    up(client, "agreement")
    status(client, "sent")
    up(client, "signed_customer", who="t-owner", name="blurred.jpg", data=_jpeg())
    assert reject(client, who="t-owner").status_code == 403
    r = reject(client)
    body = r.json()
    assert r.status_code == 200 and body["status"] == "sent" and body["pending_scan"] is None
    assert body["history"][-1]["status"] == "scan_rejected" and body["history"][-1]["reason"] == "unsigned page 2"
    assert any(f["name"] == "blurred.jpg" for f in body["files"])
    r = up(client, "signed_customer", who="t-owner", name="clear.pdf")
    assert r.status_code == 200 and r.json()["pending_scan"]["name"] == "clear.pdf"
    bad = client.post(URL + "/reject", headers=hdr("t-staff"), json={**seen(client), "file_id": "x", "sha256": "y", "reason": ""})
    assert bad.status_code == 422


def test_after_signing_nothing_signed_is_replaced_or_archived(client):
    agreement = up(client, "agreement").json()["current"]["agreement"]
    status(client, "sent")
    scan_c = up(client, "signed_customer", who="t-owner").json()["pending_scan"]["id"]
    assert client.patch(f"{URL}/files/{scan_c}", headers=hdr("t-staff"), json={"archived": True}).status_code == 409   # awaiting
    confirm(client)
    up(client, "signed_motres")
    scan_m = confirm(client).json()["current"]["signed_motres"]
    assert up(client, "signed_customer", who="t-owner", name="another.pdf").status_code == 409
    assert up(client, "signed_motres", name="another.pdf").status_code == 409
    assert up(client, "agreement", name="agreement_v2.pdf").status_code == 409
    for fid in (agreement, scan_c, scan_m):
        assert client.patch(f"{URL}/files/{fid}", headers=hdr("t-staff"), json={"archived": True}).status_code == 409
    body = client.get(URL, headers=hdr("t-owner")).json()
    assert body["status"] == "signed_both" and body["current"]["signed_customer"] == scan_c
    assert {f["id"] for f in body["files"] if f["frozen"]} == {agreement, scan_c, scan_m}
    assert up(client, "other", name="cover_letter.pdf").status_code == 200


def test_a_new_revision_needs_fresh_scans_and_old_ones_never_sign_it(client):
    up(client, "agreement")
    status(client, "sent")
    up(client, "signed_customer", who="t-owner")
    confirm(client)
    assert new_revision(client, who="t-owner").status_code == 403
    r = new_revision(client)
    assert r.status_code == 200 and r.json()["status"] == "draft" and r.json()["revision"] == 2
    a2 = up(client, "agreement", name="agreement_v2.pdf", note="payment clause").json()["current"]["agreement"]
    r = status(client, "sent")
    assert [f["id"] for f in r.json()["history"][-1]["files"]] == [a2] and r.json()["history"][-1]["revision"] == 2
    up(client, "signed_customer", who="t-owner", name="signed_v2.pdf")
    assert confirm(client).json()["history"][-1]["files"][0]["name"] == "signed_v2.pdf"


# ── the stale-page guard: revision AND terms ─────────────────────────────────────────────────────────────────────────

def test_a_stale_page_never_signs_newer_terms(client):
    up(client, "agreement")
    status(client, "sent")
    old = seen(client)                                                         # the customer's dialog opened on revision 1
    new_revision(client)
    up(client, "agreement", name="agreement_v2.pdf")
    status(client, "sent")
    r = up(client, "signed_customer", who="t-owner", name="signed_for_v1.pdf", at=old)
    assert r.status_code == 409 and "revision 2" in r.json()["detail"]
    assert client.get(URL, headers=hdr("t-owner")).json()["pending_scan"] is None
    assert status(client, "sent", at=old).status_code == 409
    assert new_revision(client, at=old).status_code == 409
    assert client.post(URL + "/status", headers=hdr("t-staff"), json={"status": "sent", "revision": 2}).status_code == 422
    assert client.post(URL + "/files", headers=hdr("t-owner"), files={"file": ("s.pdf", PDF)},
                       data={"kind": "signed_customer", "revision": "2"}).status_code == 422
    wrong_terms = {**seen(client), "terms_digest": "0" * 16}
    assert up(client, "signed_customer", who="t-owner", at=wrong_terms).status_code == 409


def test_confirm_with_terms_that_changed_meanwhile_is_refused(client):
    up(client, "agreement")
    status(client, "sent")
    up(client, "signed_customer", who="t-owner")
    at = seen(client)
    scan = client.get(URL, headers=hdr("t-staff")).json()["pending_scan"]
    new_revision(client)                                                       # meanwhile: a new revision
    assert confirm(client, scan=scan, at=at).status_code == 409
    assert client.get(URL, headers=hdr("t-staff")).json()["status"] == "draft"


def test_a_race_between_upload_and_a_new_revision_is_refused_under_the_lock(client, monkeypatch):
    up(client, "agreement")
    status(client, "sent")
    real = PC.content_matches

    def slow(path, name):                                   # while the scan is checked, staff start revision 2
        ok = real(path, name)
        PC._mutate("proj1", lambda d: d.update(revision=2, status="draft", sent_files=[], signatures={}))
        return ok

    monkeypatch.setattr(PC, "content_matches", slow)
    assert up(client, "signed_customer", who="t-owner").status_code == 409
    monkeypatch.setattr(PC, "content_matches", real)
    body = client.get(URL, headers=hdr("t-staff")).json()
    assert body["revision"] == 2 and body["pending_scan"] is None and not any(f["kind"] == "signed_customer" for f in body["files"])


# ── files ────────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_real_documents_are_accepted(client):
    assert up(client, "agreement", name="agreement.docx", data=_docx()).status_code == 200
    assert up(client, "annex_b", name="annex_b.pdf", data=_pdf("Annex B")).status_code == 200
    assert up(client, "other", name="photo.jpg", data=_jpeg()).status_code == 200
    assert up(client, "other", name="photo.png", data=_png()).status_code == 200
    status(client, "sent")
    assert up(client, "signed_customer", who="t-owner", name="phone_scan.jpg", data=_jpeg()).status_code == 200


def _zip(members: dict, method=zipfile.ZIP_DEFLATED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=method) as z:
        for k, v in members.items():
            z.writestr(k, v)
    return buf.getvalue()


def _png_without_idat() -> bytes:
    raw = _png()
    i = raw.index(b"IDAT") - 4
    length = int.from_bytes(raw[i:i + 4], "big")
    return raw[:i] + raw[i + 12 + length:]


@pytest.mark.parametrize("name,data", [
    ("scan.pdf", b"<html><script>alert(1)</script></html>"),
    ("scan.pdf", b"  <svg onload=x>%PDF-1.7 %%EOF"),
    ("scan.png", b"<svg onload=x>"),
    ("short.png", b"\x89PNG\r\n\x1a\n"),
    ("no_idat.png", None),
    ("three.jpg", b"\xff\xd8\xff"),
    ("cut.pdf", b"%PDF-1.7 no end marker"),
    ("signed.docx", "docx"),                                    # a signed scan is never a DOCX
])
def test_what_is_not_a_readable_scan_is_refused_and_moves_nothing(client, tmp_path, name, data):
    if data is None:
        data = _png_without_idat()
    if data == "docx":
        data = _docx()
    up(client, "agreement")
    status(client, "sent")
    r = up(client, "signed_customer", who="t-owner", name=name, data=data)
    assert r.status_code == 422
    body = client.get(URL, headers=hdr("t-owner")).json()
    assert body["status"] == "sent" and body["pending_scan"] is None
    assert len(list((tmp_path / "projects" / "proj1" / "contract" / "files").iterdir())) == 1      # only the agreement


@pytest.mark.parametrize("members", [
    {"[Content_Types].xml": "not xml", "_rels/.rels": "<r/>", "word/document.xml": "not xml either"},
    {"[Content_Types].xml": "<Types/>", "word/document.xml": "<d/>"},                                  # no package relationships
    {"[Content_Types].xml": "<T>application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml</T>",
     "_rels/.rels": "<r/>", "word/document.xml": "<d/>", "word/media/bomb.bin": b"\0" * (4 * 1024 * 1024)},  # ratio far above 200
])
def test_a_docx_is_preflighted_before_anything_is_read(client, members):
    assert up(client, "agreement", name="agreement.docx", data=_zip(members)).status_code == 422


def test_the_docx_preflight_reads_nothing_of_a_bomb(client, monkeypatch):
    calls = []
    real = zipfile.ZipFile.read
    monkeypatch.setattr(zipfile.ZipFile, "read", lambda self, name, *a, **k: calls.append(name) or real(self, name, *a, **k))
    bomb = _zip({"[Content_Types].xml": "<T/>", "_rels/.rels": "<r/>", "word/document.xml": "<d/>", "x.bin": b"\0" * (4 * 1024 * 1024)})
    assert up(client, "agreement", name="agreement.docx", data=bomb).status_code == 422
    assert calls == []


def test_every_upload_is_a_version_and_archive_restore_keep_everything(client):
    a1 = up(client, "agreement", name="agreement_v1.pdf").json()["current"]["agreement"]
    a2 = up(client, "agreement", name="agreement_v2.pdf", data=_pdf("v2"), note="payment clause").json()["current"]["agreement"]
    assert a1 != a2
    body = client.get(URL, headers=hdr("t-owner")).json()
    assert [f["name"] for f in body["files"]] == ["agreement_v2.pdf", "agreement_v1.pdf"]
    r = client.patch(f"{URL}/files/{a2}", headers=hdr("t-staff"), json={"archived": True})
    assert r.status_code == 200 and r.json()["current"]["agreement"] == a1
    assert len(client.get(URL + "?archived=1", headers=hdr("t-staff")).json()["files"]) == 2
    assert client.patch(f"{URL}/files/{a2}", headers=hdr("t-owner"), json={"archived": False}).status_code == 403
    assert client.patch(f"{URL}/files/{a2}", headers=hdr("t-staff"), json={"archived": False}).json()["current"]["agreement"] == a2
    r = status(client, "sent")
    assert [f["id"] for f in r.json()["history"][-1]["files"]] == [a2]


def test_the_customer_side_uploads_only_its_signed_scan(client):
    assert up(client, "agreement", who="t-owner").status_code == 403
    assert up(client, "signed_motres", who="t-owner").status_code == 403
    assert status(client, "sent", who="t-owner").status_code == 403


def test_download_is_traced_and_an_archived_file_is_staff_only(client, tmp_path):
    fid = up(client, "agreement").json()["current"]["agreement"]
    r = client.get(f"{URL}/files/{fid}/download", headers=hdr("t-owner"))
    assert r.status_code == 200 and r.content == PDF and r.headers["content-type"] == "application/octet-stream"
    log = [json.loads(x) for x in (tmp_path / "downloads.jsonl").read_text().splitlines()]
    assert log[-1]["kind"] == "project_contract" and log[-1]["project"] == "proj1"
    client.patch(f"{URL}/files/{fid}", headers=hdr("t-staff"), json={"archived": True})
    assert client.get(f"{URL}/files/{fid}/download", headers=hdr("t-owner")).status_code == 404
    assert client.get(f"{URL}/files/{fid}/download", headers=hdr("t-staff")).status_code == 200


def test_uploads_are_validated_and_leave_no_bytes(client, tmp_path):
    assert up(client, "secret").status_code == 422
    assert up(client, "agreement", name="script.exe").status_code == 422
    assert up(client, "agreement", data=b"").status_code == 400
    files = tmp_path / "projects" / "proj1" / "contract" / "files"
    assert not files.exists() or list(files.iterdir()) == []


def test_the_sample_iteration(client):
    store.mutate_project("proj1", lambda p: p.update(status="prototype"))
    assert client.get(URL, headers=hdr("t-staff")).json()["step"] == "samples_1"
    r = client.post(URL + "/status", headers=hdr("t-staff"), json={"sample_iteration": 2})
    assert r.status_code == 200 and r.json()["step"] == "samples_2"
    assert client.post(URL + "/status", headers=hdr("t-staff"), json={"sample_iteration": 3}).status_code == 422


@pytest.mark.parametrize("status_,contract,step", [("spec", {}, "specification"), ("design", {}, "simulation_agreement"),
                                                   ("quote", {}, "contract_payment"), ("production", {}, "series"),
                                                   ("closed", {}, None), ("simulation", {"status": "signed_both"}, "contract_payment")])
def test_current_step(status_, contract, step):
    assert PC.current_step({"status": status_}, contract) == step


def test_the_staff_scheme_falls_back_to_the_private_checkout(client, tmp_path, monkeypatch):
    assert client.get(URL, headers=hdr("t-staff")).json()["staff_scheme"] is None
    private = tmp_path / "private"
    (private / "config" / "contract").mkdir(parents=True)
    scheme = {"nodes": [{"id": "a", "label": "A", "x": 1, "y": 1}], "edges": [], "notes": []}
    (private / "config" / "contract" / "supply_scheme_staff.json").write_text(json.dumps(scheme))
    monkeypatch.setenv("MOTOR_AI_SIM_PRIVATE_DATA", str(private))
    assert client.get(URL, headers=hdr("t-staff")).json()["staff_scheme"] == scheme
    assert client.get(URL, headers=hdr("t-owner")).json()["staff_scheme"] is None


def test_company_state_is_unknown_until_the_form_exists(client):
    body = client.get(URL, headers=hdr("t-owner")).json()
    assert set(body["company"]) == {"available", "filled", "confirmed_on", "confirmed_by"}


# ── the audit ────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_a_failed_audit_append_keeps_the_change_with_its_event_and_is_written_once_later(client, tmp_path, monkeypatch):
    real = store.append_jsonl

    def broken(project_id, name, entry):
        if name == "audit.jsonl":
            raise OSError("disk full")
        return real(project_id, name, entry)

    up(client, "agreement")
    at = seen(client)
    monkeypatch.setattr(store, "append_jsonl", broken)
    r = status(client, "sent", at=at)
    assert r.status_code == 200 and doc(tmp_path)["status"] == "sent"
    assert [e["action"] for e in doc(tmp_path)["pending_audit"]] == ["contract.status"]
    assert up(client, "signed_customer", who="t-owner").status_code == 503
    monkeypatch.setattr(store, "append_jsonl", real)
    client.get(URL, headers=hdr("t-staff"))
    client.get(URL, headers=hdr("t-staff"))
    assert audit_actions(tmp_path).count("contract.status") == 1
    assert "pending_audit" not in doc(tmp_path)


def test_an_ordinary_audit_append_ends_a_cut_line_first(client, tmp_path):
    up(client, "agreement")
    audit = tmp_path / "projects" / "proj1" / "audit.jsonl"
    with audit.open("ab") as fh:
        fh.write(b'{"action": "cut sho')                                    # an append cut short
    fid = client.get(URL, headers=hdr("t-staff")).json()["current"]["agreement"]
    client.get(f"{URL}/files/{fid}/download", headers=hdr("t-owner"))      # an ordinary append (store.append_audit)
    lines = audit.read_bytes().splitlines()
    assert lines[-2] == b'{"action": "cut sho' and json.loads(lines[-1])["action"] == "contract.download"


def test_the_byte_cap_holds_without_content_length(client, monkeypatch):
    monkeypatch.setattr(PC, "MAX_BYTES", 1000)
    s = seen(client)
    boundary = "b0undary"
    fields = "".join(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n"
                     for k, v in (("kind", "agreement"), ("revision", s["revision"]), ("terms_digest", s["terms_digest"])))
    body = (fields + f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.pdf\"\r\n\r\n").encode() \
        + PDF + b"x" * 200_000 + f"\r\n--{boundary}--\r\n".encode()

    def chunked():
        for i in range(0, len(body), 8192):
            yield body[i:i + 8192]

    r = client.post(URL + "/files", headers={**hdr("t-staff"), "Content-Type": f"multipart/form-data; boundary={boundary}"}, content=chunked())
    assert r.status_code == 413
    two = client.post(URL + "/files", headers=hdr("t-staff"), data={"kind": "agreement", **{k: str(v) for k, v in s.items()}},
                      files=[("file", ("a.pdf", PDF)), ("more", ("b.pdf", PDF))])
    assert two.status_code == 400


def test_a_controller_project_has_no_contract_for_anybody(client):
    """Owner 2026-11-24 «убрать Contract у контроллеров пока»: every route answers the unknown-project 404 for every role (the tab
    reads that answer); a motor project is unchanged."""
    fid = up(client, "agreement").json()["current"]["agreement"]
    unknown = client.get("/api/projects/nosuch/contract", headers=hdr("t-staff"))
    store.mutate_project("proj1", lambda p: p.__setitem__("project_type", "controller"))
    for who in ("t-owner", "t-member", "t-staff", "t-oem"):
        r = client.get(URL, headers=hdr(who))
        assert r.status_code == 404 and r.json() == unknown.json(), who
        assert client.get(f"{URL}/files/{fid}/download", headers=hdr(who)).status_code == 404
    assert up(client, "annex_a", at={"revision": 1, "terms_digest": "x"}).status_code == 404
    project = store.get_project("proj1")
    assert not PC.may_read({"email": STAFF, "role": "engineer"}, project)
    def motor_again(p):
        p.pop("project_type", None)                          # return nothing: a returned value would BE the document
    store.mutate_project("proj1", motor_again)
    assert client.get(URL, headers=hdr("t-owner")).status_code == 200



def test_no_contract_line_reaches_the_members_digest_of_a_controller_project(client):
    """The other readers of contract data (Astra #852): contract.json and its files are read only by project_contract (all its
    routes 404 for a controller project); its audit rows reach only staff readers.  The members' digest maps no contract action."""
    from motor_ai_sim.digest import collect
    up(client, "agreement")
    store.mutate_project("proj1", lambda p: p.__setitem__("project_type", "controller"))
    items = collect.collect_project(store.get_project("proj1"), 0, 4e9)
    assert not any("contract" in repr(i).lower() for i in items)
