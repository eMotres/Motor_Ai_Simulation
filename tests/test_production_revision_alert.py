"""R1 of the production revision alerts (owner 2026-11-15): publishing a revision SENDS - mail to every member, a message in the
Discussion, an audit line with the recipients - and the preview shows exactly that first.  Mail goes through a fake mailer."""
from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from motor_ai_sim.production import models, revision_alert as RA, routes as R, store

DIE, CFG = "CIANO14 40 new", "L12"
OWNER, ENG, OEM1, OEM2 = "owner@example.com", "eng@example.com", "factory1@example.com", "factory2@example.com"


class FakeMail:
    def __init__(self, fail=()):
        self.sent, self.fail = [], set(fail)

    def __call__(self, to, subject, body):
        if to in self.fail:
            raise RuntimeError("smtp down")
        self.sent.append((to, subject, body))
        return "sent"


@contextlib.contextmanager
def production(members=None, docs=None, revisions=None):
    old_root, old_send = os.environ.get("MOTRES_PRODUCTION_ROOT"), RA.SEND
    root = Path(tempfile.mkdtemp())
    os.environ["MOTRES_PRODUCTION_ROOT"] = str(root)
    mail = FakeMail()
    RA.SEND = mail
    try:
        prod = models.normalize_production({"members": members if members is not None else [
            {"email": OEM1, "role": "oem"}, {"email": ENG, "role": "engineer"}, {"email": OWNER, "role": "owner"},
            {"email": OEM2, "role": "oem"}, {"email": OEM1.upper(), "role": "oem"}]}, DIE, CFG)
        store.save_file(DIE, CFG, "production.json", prod)
        store.save_file(DIE, CFG, "documents.json", {"documents": docs or []})
        store.save_file(DIE, CFG, "revisions.json", {"revisions": revisions or []})
        store.save_file(DIE, CFG, "threads.json", {"threads": []})
        yield mail
    finally:
        RA.SEND = old_send
        if old_root is None:
            os.environ.pop("MOTRES_PRODUCTION_ROOT", None)
        else:
            os.environ["MOTRES_PRODUCTION_ROOT"] = old_root


def doc(i, name, part_id="p1", uploaded="2026-11-01T10:00:00", internal=False):
    return {"id": "doc_%s" % i, "file_id": "f%s" % i, "name": name, "part_id": part_id, "uploaded_at": uploaded, "internal": internal,
            "uploader_email": ENG}


def bil(en, zh=None):
    return models.normalize_bilingual({"en": en, "zh": zh or en + " (zh)", "src_lang": "en", "status": {"en": "confirmed", "zh": "confirmed"}})


def publish(summary="Shaft thread added", changes=None, **kw):
    user = {"email": ENG, "role": "engineer"}
    ch = changes if changes is not None else [dict(bil("M5 thread added to the shaft end", "轴端增加 M5 螺纹"))]
    kw.setdefault("urgent", True)           # these tests are about the mail itself: the urgent path mails at once (the digest has its own tests)
    return R._publish_revision(DIE, CFG, user, bil(summary, "增加轴螺纹"), ch, **kw)


def test_publish_sends_one_mail_to_every_member_once_with_the_required_content():
    with production(docs=[doc(1, "shaft_40_v1.2.pdf"), doc(2, "shaft_40_v1.3.pdf", uploaded="2026-11-15T09:00:00")]) as mail:
        rev = publish(supersedes=[{"old_doc_id": "doc_1", "new_doc_id": "doc_2"}])
        assert [m[0] for m in mail.sent] == [OWNER, ENG, OEM1, OEM2]                       # each member once, owner first
        subjects = {m[1] for m in mail.sent}
        assert len(subjects) == 1
        subj = subjects.pop()
        assert DIE in subj and CFG in subj and "revision 1.0" in subj and "action required: acknowledge" in subj and "需要确认" in subj
        body = mail.sent[0][2]
        for needle in ("Shaft thread added", "M5 thread added to the shaft end", "轴端增加 M5 螺纹", "增加轴螺纹", "What changed:", "变更内容：",
                       "SUPERSEDED - do not use:", "已作废", "shaft_40_v1.2.pdf (v1.2) -> replaced by shaft_40_v1.3.pdf (v1.3)",
                       "Parts must not be made to a superseded drawing.", "不得按已作废的图纸制造零件。", "/production/", "changes", "item=1.0"):
            assert needle in body, needle
        for bad in ("price", "Price", "quote", "€", "$", "USD", "EUR", "margin"):
            assert bad not in body and bad not in subj, bad
        assert body.index("revision 1.0 was published") < body.index("版本 1.0 已发布")        # English first, the Chinese copy under it
        assert rev["alert"]["recipients"] == [{"email": e, "role": r, "mail": "sent"} for e, r in
                                              [(OWNER, "owner"), (ENG, "engineer"), (OEM1, "oem"), (OEM2, "oem")]]


def test_one_failing_address_does_not_stop_the_others_and_is_recorded():
    with production() as mail:
        mail.fail = {OEM1}
        rev = publish()
        assert sorted(m[0] for m in mail.sent) == sorted([OWNER, ENG, OEM2])
        status = {r["email"]: r["mail"] for r in rev["alert"]["recipients"]}
        assert status[OEM1] == "failed" and status[OEM2] == "sent" and status[OWNER] == "sent"


def test_a_server_without_smtp_sends_nothing_and_says_so():
    saved = {k: os.environ.pop(k, None) for k in ("SMTP_USER", "SMTP_PASS", "SMTP_PASSWORD")}
    try:
        with production() as _mail:
            RA.SEND = RA._send_default                                                    # the real path, no SMTP in the environment
            rev = publish()
            assert {r["mail"] for r in rev["alert"]["recipients"]} == {"not_configured"}
            assert rev["alert"]["message_id"]                                           # the Discussion message is still posted
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


def test_the_discussion_message_goes_to_general_or_to_the_one_part_named():
    with production() as mail:
        publish(changes=[dict(bil("generic change"))])
        th = store.load_file(DIE, CFG, "threads.json")["threads"]
        assert len(th) == 1 and th[0]["part_id"] is None and len(th[0]["messages"]) == 1
        m = th[0]["messages"][0]
        assert "Revision 1.0 was published" in m["text"]["en"] and "版本 1.0 已发布" in m["text"]["zh"] and m["author_email"] == ENG
        publish(changes=[dict(bil("a"), part_id="p7"), dict(bil("b"), part_id="p7")])
        th = store.load_file(DIE, CFG, "threads.json")["threads"]
        assert sorted((t["part_id"] or "-") for t in th) == ["-", "p7"]
        publish(changes=[dict(bil("a"), part_id="p7"), dict(bil("b"), part_id="p8")])          # two parts: General again
        general = [t for t in store.load_file(DIE, CFG, "threads.json")["threads"] if t["part_id"] is None][0]
        assert len(general["messages"]) == 2


def test_the_record_is_on_the_revision_and_in_the_audit_with_the_recipients():
    with production():
        rev = publish()
        stored = store.load_file(DIE, CFG, "revisions.json")["revisions"][0]
        assert stored["alert"]["recipients"] == rev["alert"]["recipients"] and stored["alert"]["thread_id"]
        audit = [__import__("json").loads(x) for x in store.audit_path(DIE, CFG).read_text().splitlines()]
        al = [a for a in audit if a["action"] == "revision_alert"]
        assert len(al) == 1 and [r["email"] for r in al[0]["snapshot"]["recipients"]] == [OWNER, ENG, OEM1, OEM2]
        assert any(a["action"] == "publish_revision" for a in audit)


def test_the_preview_is_exactly_what_is_sent_and_writes_nothing():
    with production(docs=[doc(1, "a_v1.0.pdf")]) as mail:
        client_user = {"email": ENG, "role": "engineer"}
        existing = store.load_file(DIE, CFG, "revisions.json")["revisions"]
        files = R._revision_files(DIE, CFG, existing, [], number="1.0")
        rev = models.normalize_revision({"number": "1.0", "author_email": ENG, "summary": bil("S"), "changes": [bil("C")], "files": files})
        pv = RA.preview(DIE, CFG, store.load_file(DIE, CFG, "production.json"), rev)
        assert store.load_file(DIE, CFG, "revisions.json")["revisions"] == [] and store.load_file(DIE, CFG, "threads.json")["threads"] == []
        assert mail.sent == []
        R._publish_revision(DIE, CFG, client_user, bil("S"), [bil("C")], urgent=True)
        assert len(mail.sent) == 4 and all(m[1] == pv["subject"] and m[2] == pv["body"] for m in mail.sent)
        assert [r["email"] for r in pv["recipients"]] == [m[0] for m in mail.sent]


def test_new_files_are_those_uploaded_since_the_last_revision_and_internal_ones_stay_out():
    docs = [doc(1, "old_v1.0.pdf", uploaded="2026-11-01T00:00:00"), doc(2, "fresh_v1.1.pdf", uploaded="2026-11-02T00:00:00"),
            doc(3, "secret_v1.0.pdf", uploaded="2026-11-02T00:00:00", internal=True)]
    with production(docs=docs):
        first = publish()
        assert [f["name"] for f in first["files"]["new"]] == ["old_v1.0.pdf", "fresh_v1.1.pdf"]          # first revision: all non-internal
        assert first["files"]["released"] == ["doc_1", "doc_2"]
        assert publish()["files"]["new"] == []                                                          # nothing uploaded since
        store.mutate_file(DIE, CFG, "documents.json", lambda d: d["documents"].append(doc(4, "later_v1.0.pdf", uploaded="2999-01-01T00:00:00")))
        third = publish()
        assert [f["name"] for f in third["files"]["new"]] == ["later_v1.0.pdf"] and third["files"]["released"] == ["doc_1", "doc_2", "doc_4"]


def test_superseding_marks_the_old_file_keeps_it_and_refuses_bad_pairs_before_writing():
    with production(docs=[doc(1, "s_v1.1.pdf"), doc(2, "s_v1.3.pdf")]) as mail:
        for bad in ([{"old_doc_id": "doc_1", "new_doc_id": "nope"}], [{"old_doc_id": "doc_1", "new_doc_id": "doc_1"}],
                    [{"old_doc_id": "doc_1", "new_doc_id": "doc_2"}, {"old_doc_id": "doc_1", "new_doc_id": "doc_2"}]):
            with pytest.raises(ValueError):
                publish(supersedes=bad)
        assert store.load_file(DIE, CFG, "revisions.json")["revisions"] == [] and mail.sent == []
        publish(supersedes=[{"old_doc_id": "doc_1", "new_doc_id": "doc_2"}])
        d1, d2 = store.load_file(DIE, CFG, "documents.json")["documents"]
        assert d1["status"] == "superseded" and d1["superseded_by"] == {"doc_id": "doc_2", "file_id": "f2", "name": "s_v1.3.pdf", "version": "1.3", "revision": "1.0"}
        assert d1["file_id"] == "f1" and d1["archived_at"] and "status" not in d2                 # kept, not deleted
        with pytest.raises(ValueError):
            publish(supersedes=[{"old_doc_id": "doc_1", "new_doc_id": "doc_2"}])                   # already superseded


def test_file_versions_come_from_the_name_and_revisions_keep_their_new_fields():
    for name, v in (("shaft_40_v1.3.pdf", "1.3"), ("a-V2.pdf", "2"), ("x_v1.2.3.step", "1.2.3"), ("plain.pdf", None), ("rev_v.pdf", None), ("v1.pdf", None),
                    ("m_v1.3", "1.3"), ("", None)):
        assert models.file_version(name) == v, name
    r = models.normalize_revision({"number": "1.1", "summary": bil("s"), "changes": [dict(bil("c"), part_id="p1")], "files": {"new": [{"name": "n"}], "released": ["d1"]},
                                   "alert": {"sent_at": "t"}})
    assert r["changes"][0]["part_id"] == "p1" and r["files"] == {"new": [{"name": "n"}], "superseded": [], "released": ["d1"]} and r["alert"] == {"sent_at": "t"}
    assert "files" not in models.normalize_revision({"number": "1.0", "summary": bil("s")})


def test_notify_false_publishes_without_any_message():
    with production() as mail:
        rev = publish(notify=False)
        assert mail.sent == [] and "alert" not in rev and store.load_file(DIE, CFG, "threads.json")["threads"] == []


def test_every_new_alert_string_has_its_chinese_copy():
    with production():
        rev = models.normalize_revision({"number": "2.0", "summary": bil("S", "摘要S"), "changes": [bil("C", "变更C")],
                                         "files": {"new": [{"name": "n_v1.0.pdf"}], "superseded": [{"name": "o_v0.9.pdf", "replaced_by": {"name": "n_v1.0.pdf"}}]}})
        a = RA.build(DIE, CFG, store.load_file(DIE, CFG, "production.json"), rev)
        en, zh = a["body"].split("-" * 40)
        for en_s, zh_s in (("was published", "已发布"), ("Summary:", "摘要："), ("What changed:", "变更内容："), ("New files:", "新文件："),
                           ("SUPERSEDED - do not use:", "已作废"), (RA.SUPERSEDED_SENTENCE_EN, RA.SUPERSEDED_SENTENCE_ZH)):
            assert en_s in en and zh_s in zh, (en_s, zh_s)
        assert "摘要S" in zh and "变更C" in zh and "摘要S" not in en


def test_a_sandbox_never_calls_the_mail_module_at_all():
    from motor_ai_sim import auth_email
    saved = {k: os.environ.pop(k, None) for k in ("SMTP_USER", "SMTP_PASS", "SMTP_PASSWORD")}
    real = auth_email._send
    calls = []
    auth_email._send = lambda *a, **k: calls.append(a) or True
    try:
        with production() as _mail:
            RA.SEND = RA._send_default
            publish()
            assert calls == [] and not auth_email.smtp_configured()
    finally:
        auth_email._send = real
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


@contextlib.contextmanager
def http_env():
    """The app with throwaway users and a throwaway production root; yields (client, headers-by-role)."""
    from motor_ai_sim import auth, users as U
    from motor_ai_sim.api import app
    saved_env = {k: os.environ.get(k) for k in ("MOTRES_PRODUCTION_ROOT", "AUTH_SECRET", "ADMIN_AUDIT_FILE", "CATALOG_GRANT_ALL_REGISTERED")}
    tmp = Path(tempfile.mkdtemp())
    old = (U._USERS_FILE, auth._ADMIN_EMAILS, auth.AUTH_ENFORCE, RA.SEND)
    os.environ.update({"MOTRES_PRODUCTION_ROOT": str(tmp / "prod"), "AUTH_SECRET": "test-secret-revision-alert-1234",
                       "ADMIN_AUDIT_FILE": str(tmp / "audit.jsonl")})
    os.environ.pop("CATALOG_GRANT_ALL_REGISTERED", None)
    U._USERS_FILE = tmp / "users.json"
    auth._ADMIN_EMAILS = {OWNER}
    auth.AUTH_ENFORCE = False
    mail = FakeMail()
    RA.SEND = mail
    try:
        U.create_user(OWNER, "password-owner-1", role="admin", name="Owner")
        U.create_user(ENG, "password-eng-1", role="engineer", name="Eng")
        U.create_user(OEM1, "password-oem1-1", role="oem", name="Factory")
        U.set_nda(OEM1, True, "2026-10-01", by=OWNER)
        hdr = lambda e: {"Authorization": "Bearer " + U.issue_token(e)}                      # noqa: E731
        (tmp / "prod").mkdir()
        yield TestClient(app), {"owner": hdr(OWNER), "eng": hdr(ENG), "oem": hdr(OEM1)}, mail
    finally:
        U._USERS_FILE, auth._ADMIN_EMAILS, auth.AUTH_ENFORCE, RA.SEND = old
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_http_preview_then_publish_sends_the_previewed_message_and_the_factory_cannot_publish():
    with http_env() as (client, h, mail):
        base = "/api/production/%s/%s" % (DIE, CFG)
        prod = models.normalize_production({"members": [{"email": OEM1, "role": "oem"}, {"email": ENG, "role": "engineer"}, {"email": OWNER, "role": "owner"}]}, DIE, CFG)
        store.save_file(DIE, CFG, "production.json", prod)
        body = {"summary": bil("Shaft thread added"), "changes": [bil("M5 thread")], "urgent": True}
        assert client.post(base + "/revisions/preview", json=body, headers=h["oem"]).status_code in (401, 403, 404)
        assert client.post(base + "/revisions", json=body, headers=h["oem"]).status_code in (401, 403, 404)
        assert mail.sent == []
        pv = client.post(base + "/revisions/preview", json=body, headers=h["eng"])
        assert pv.status_code == 200, pv.text
        pj = pv.json()
        assert mail.sent == [] and client.get(base + "/revisions", headers=h["eng"]).json()["revisions"] == []        # asking for it sends and writes nothing
        assert [r["email"] for r in pj["recipients"]] == [OWNER, ENG, OEM1]
        pub = client.post(base + "/revisions", json=body, headers=h["eng"])
        assert pub.status_code == 200, pub.text
        assert len(mail.sent) == 3 and all(m[1] == pj["subject"] and m[2] == pj["body"] for m in mail.sent)
        assert pub.json()["alert"]["recipients"][0]["mail"] == "sent"
        bad = client.post(base + "/revisions", json={**body, "supersedes": [{"old_doc_id": "x", "new_doc_id": "y"}]}, headers=h["eng"])
        assert bad.status_code == 422 and len(mail.sent) == 3                                                          # refused before anything is sent
        assert client.post(base + "/revisions/preview", json={"summary": "x"}, headers=h["eng"]).status_code == 400


def test_publishing_without_urgent_mails_nobody_and_the_record_says_the_digest_carries_it():
    """Owner 2026-11-19: every production notification waits for the daily digest; only "urgent: mail now" mails at once."""
    with production() as mail:
        rev = publish(urgent=False)
        assert mail.sent == []
        assert rev["alert"]["mode"] == "digest" and {r["mail"] for r in rev["alert"]["recipients"]} == {"digest"}
        assert rev["alert"]["thread_id"]                                   # the Discussion message is still posted at once
        pv = RA.preview(DIE, CFG, store.load_file(DIE, CFG, "production.json"), rev)
        assert pv["mode"] == "digest" and pv["will_send"] is False
    with production() as mail:
        rev = publish(urgent=True)
        assert rev["alert"]["mode"] == "urgent" and len(mail.sent) == 4
