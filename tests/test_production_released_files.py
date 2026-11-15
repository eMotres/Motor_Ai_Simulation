"""R2 of the production revision alerts (owner 2026-11-15): a drawing / STEP file of a part that is in a published revision cannot be
replaced, renamed, removed, archived or overwritten by ANY caller (routes, bulk tools, scripts) except inside the publish of a new
revision with at least one change line; the old file is kept as "superseded"; a download of it warns first."""
from __future__ import annotations

import pytest

from motor_ai_sim.production import models, store
from tests.test_production_revision_alert import CFG, DIE, ENG, bil, doc, http_env, production, publish

F1, F2, F3 = "file_id_00000001", "file_id_00000002", "file_id_00000003"


def put_bytes(fid, data=b"%PDF-1.4 original"):
    store.save_uploaded_file(DIE, CFG, fid, data)


def part(pid="p1", files=(F1,), step=None):
    return {"id": pid, "name": {"en": "Shaft", "zh": "轴"}, "drawing_files": [{"file_id": f, "name": "shaft_v1.2.pdf", "kind": "pdf"} for f in files],
            "optional_step": {"file_id": step} if step else None}


def setup_released(extra_docs=()):
    """A production whose revision 1.0 released the shaft drawing (a document + a part drawing) and one internal / one photo document."""
    docs = [dict(doc(1, "shaft_40_v1.2.pdf"), file_id=F1)] + list(extra_docs)
    store.save_file(DIE, CFG, "documents.json", {"documents": docs})
    store.save_file(DIE, CFG, "parts.json", {"parts": [part()]})
    put_bytes(F1)
    return publish(notify=False)


def err_of(fn):
    with pytest.raises(store.ReleasedFileError) as e:
        fn()
    return e.value


def test_a_released_document_cannot_be_replaced_renamed_removed_or_archived_except_in_a_revision():
    with production():
        rev = setup_released()
        assert F1 in rev["files"]["released_files"]

        def edit(**kw):
            def m(d):
                d["documents"][0].update(kw)
            store.mutate_file(DIE, CFG, "documents.json", m)

        for kw, action in (({"file_id": F2}, "replaced"), ({"name": "shaft_40_v1.3.pdf"}, "renamed"), ({"status": "superseded"}, "archived"),
                           ({"archived_at": "2026-11-15T00:00:00"}, "archived")):
            e = err_of(lambda kw=kw: edit(**kw))
            assert e.status_code == 409 and e.code == "production.released_file" and e.action == action and e.revision == "1.0"
            assert "shaft_40_v1.2.pdf" in str(e.detail) and "1.0" in str(e.detail) and "change line" in str(e.detail)
        e = err_of(lambda: store.mutate_file(DIE, CFG, "documents.json", lambda d: d["documents"].clear()))
        assert e.action == "removed"
        e = err_of(lambda: store.save_file(DIE, CFG, "documents.json", {"documents": []}))                    # an import / bulk wipe
        assert e.action == "removed"
        d0 = store.load_file(DIE, CFG, "documents.json")["documents"][0]
        assert d0["file_id"] == F1 and d0["name"] == "shaft_40_v1.2.pdf" and "status" not in d0                 # nothing was written


def test_the_incident_path_replacing_a_part_drawing_is_refused_and_adding_one_is_not():
    with production():
        setup_released()
        e = err_of(lambda: store.mutate_file(DIE, CFG, "parts.json", lambda d: d["parts"][0].update(
            {"drawing_files": [{"file_id": F2, "name": "shaft_v1.3.pdf", "kind": "pdf"}]})))
        assert e.action == "replaced or removed" and "Shaft" in e.file and e.revision == "1.0"
        assert err_of(lambda: store.mutate_file(DIE, CFG, "parts.json", lambda d: d["parts"].clear())).action == "removed"
        store.mutate_file(DIE, CFG, "parts.json", lambda d: d["parts"][0]["drawing_files"].append({"file_id": F2, "name": "extra.pdf", "kind": "pdf"}))
        store.mutate_file(DIE, CFG, "parts.json", lambda d: d["parts"][0].update({"qty_per_motor": 3}))       # unrelated edits stay free
        assert [f["file_id"] for f in store.load_file(DIE, CFG, "parts.json")["parts"][0]["drawing_files"]] == [F1, F2]


def test_the_bytes_of_a_released_file_cannot_be_overwritten_and_a_refusal_leaves_them_intact():
    with production():
        setup_released()
        assert err_of(lambda: store.save_uploaded_file(DIE, CFG, F1, b"replaced")).action == "overwritten"
        assert err_of(lambda: store.save_uploaded_stream(DIE, CFG, F1, iter([b"x" * 10]), 100)).action == "overwritten"
        assert store.read_uploaded_file(DIE, CFG, F1).read_bytes() == b"%PDF-1.4 original"
        put_bytes(F2, b"new file under a new id")                                                          # a new id is always free
        assert store.read_uploaded_file(DIE, CFG, F2).read_bytes() == b"new file under a new id"


def test_what_is_not_released_is_free():
    photo = dict(doc(2, "photo.jpg"), file_id=F2)
    internal = dict(doc(3, "secret_v1.pdf", internal=True), file_id=F3)
    with production():
        setup_released([photo, internal])
        later = dict(doc(4, "late_v1.0.pdf", uploaded="2999-01-01T00:00:00"), file_id="file_id_00000004")
        store.mutate_file(DIE, CFG, "documents.json", lambda d: d["documents"].append(later))
        store.mutate_file(DIE, CFG, "documents.json", lambda d: d["documents"].__setitem__(1, dict(d["documents"][1], name="renamed.jpg")))   # a photo
        store.mutate_file(DIE, CFG, "documents.json", lambda d: d["documents"].__setitem__(2, dict(d["documents"][2], file_id="file_id_00000009")))  # internal
        def drop_last(d):
            d["documents"].pop()                                                                                                               # unreleased
        store.mutate_file(DIE, CFG, "documents.json", drop_last)
        store.mutate_file(DIE, CFG, "documents.json", lambda d: d["documents"][0].update({"internal": True}))                                 # a flag, not a replacement
        put_bytes(F2, b"photo bytes")
        store.save_uploaded_file(DIE, CFG, F2, b"photo bytes 2")


def test_a_revision_without_a_snapshot_protects_what_existed_at_its_date():
    old = dict(doc(1, "old_v1.0.pdf", uploaded="2026-10-01T00:00:00"), file_id=F1)
    new = dict(doc(2, "new_v1.0.pdf", uploaded="2999-01-01T00:00:00"), file_id=F2)
    with production(docs=[old, new], revisions=[{"number": "1.0", "date": "2026-11-01T00:00:00", "summary": bil("s"), "changes": [], "acknowledgements": []}]):
        put_bytes(F1)
        assert store.released_files(DIE, CFG) == {F1: "1.0"}
        assert err_of(lambda: store.mutate_file(DIE, CFG, "documents.json", lambda d: d["documents"].pop(0))).action == "removed"
        def drop_second(d):
            d["documents"].pop(1)                                                                            # uploaded after the revision: free
        store.mutate_file(DIE, CFG, "documents.json", drop_second)


def test_a_revision_may_supersede_a_document_which_is_then_archived_kept_and_hidden_from_the_part_files():
    new = dict(doc(2, "shaft_40_v1.3.pdf", uploaded="2999-01-01T00:00:00"), file_id=F2)
    with production() as mail:
        setup_released()
        put_bytes(F2, b"v1.3")
        store.mutate_file(DIE, CFG, "documents.json", lambda d: d["documents"].append(new))
        rev = publish(supersedes=[{"old_doc_id": "doc_1", "new_doc_id": "doc_2"}])
        d1, d2 = store.load_file(DIE, CFG, "documents.json")["documents"]
        assert d1["status"] == "superseded" and d1["superseded_by"]["name"] == "shaft_40_v1.3.pdf" and d1["superseded_by"]["revision"] == "1.1"
        assert store.read_uploaded_file(DIE, CFG, F1).read_bytes() == b"%PDF-1.4 original"                   # kept
        assert rev["files"]["superseded"][0]["name"] == "shaft_40_v1.2.pdf" and F2 in rev["files"]["released_files"]
        assert any("SUPERSEDED - do not use" in m[2] and "shaft_40_v1.2.pdf (v1.2) -> replaced by shaft_40_v1.3.pdf (v1.3)" in m[2] for m in mail.sent)
        assert models.is_superseded(d1) and not models.is_superseded(d2)
        # still protected afterwards: the archived file cannot be removed
        def drop_first(d):
            d["documents"].pop(0)
        assert err_of(lambda: store.mutate_file(DIE, CFG, "documents.json", drop_first)).action == "removed"
        # and archiving without the revision stays refused
        assert err_of(lambda: store.mutate_file(DIE, CFG, "documents.json", lambda d: d["documents"][1].update({"status": "superseded"}))).action == "archived"


def test_a_revision_may_replace_the_drawing_of_a_part_and_the_old_one_stays_as_a_superseded_document():
    with production():
        setup_released()
        put_bytes(F2, b"v1.3")
        rev = publish(supersedes=[{"part_id": "p1", "old_file_id": F1, "new_file_id": F2, "new_name": "shaft_40_v1.3.pdf"}])
        p = store.load_file(DIE, CFG, "parts.json")["parts"][0]
        assert p["drawing_files"] == [{"file_id": F2, "name": "shaft_40_v1.3.pdf", "kind": "pdf"}]
        old = [d for d in store.load_file(DIE, CFG, "documents.json")["documents"] if d["file_id"] == F1 and d.get("status") == "superseded"]
        assert len(old) == 1 and old[0]["superseded_by"]["name"] == "shaft_40_v1.3.pdf" and old[0]["part_id"] == "p1"
        assert store.read_uploaded_file(DIE, CFG, F1).read_bytes() == b"%PDF-1.4 original"
        assert rev["files"]["superseded"][0]["replaced_by"]["file_id"] == F2 and F2 in rev["files"]["released_files"]
        assert err_of(lambda: store.mutate_file(DIE, CFG, "parts.json", lambda d: d["parts"][0].update({"drawing_files": []}))).action == "replaced or removed"


def test_replacing_a_file_needs_a_change_line_and_a_bad_pair_changes_nothing():
    with production() as mail:
        setup_released()
        put_bytes(F2, b"v1.3")
        sent_before = len(mail.sent)
        pair = [{"part_id": "p1", "old_file_id": F1, "new_file_id": F2, "new_name": "shaft_40_v1.3.pdf"}]
        for changes in ([], [{"en": "", "zh": ""}]):
            with pytest.raises(ValueError, match="change line"):
                publish(changes=changes, supersedes=pair)
        for bad in ([{"part_id": "p9", "old_file_id": F1, "new_file_id": F2, "new_name": "x_v1.pdf"}],
                    [{"part_id": "p1", "old_file_id": F3, "new_file_id": F2, "new_name": "x_v1.pdf"}],
                    [{"part_id": "p1", "old_file_id": F1, "new_file_id": F1, "new_name": "x_v1.pdf"}],
                    [{"part_id": "p1", "old_file_id": F1, "new_file_id": "file_id_0000ffff", "new_name": "x_v1.pdf"}],
                    [{"part_id": "p1", "old_file_id": F1, "new_file_id": F2, "new_name": " "}]):
            with pytest.raises(ValueError):
                publish(supersedes=bad)
        assert len(store.load_file(DIE, CFG, "revisions.json")["revisions"]) == 1 and len(mail.sent) == sent_before
        assert store.load_file(DIE, CFG, "parts.json")["parts"][0]["drawing_files"][0]["file_id"] == F1


def test_http_a_part_drawing_cannot_be_replaced_through_the_api_and_a_superseded_download_warns_first():
    with http_env() as (client, h, mail):
        from tests.test_production_revision_alert import OEM1, OWNER
        base = "/api/production/%s/%s" % (DIE, CFG)
        prod = models.normalize_production({"members": [{"email": OEM1, "role": "oem"}, {"email": ENG, "role": "engineer"}, {"email": OWNER, "role": "owner"}]}, DIE, CFG)
        store.save_file(DIE, CFG, "production.json", prod)
        store.save_file(DIE, CFG, "documents.json", {"documents": [dict(doc(1, "shaft_40_v1.2.pdf"), file_id=F1)]})
        store.save_file(DIE, CFG, "parts.json", {"parts": [part()]})
        for k in ("revisions.json", "threads.json"):
            store.save_file(DIE, CFG, k, {"revisions": []} if k.startswith("rev") else {"threads": []})
        put_bytes(F1)
        put_bytes(F2, b"v1.3 bytes")
        store.mutate_file(DIE, CFG, "documents.json", lambda d: d["documents"].append(dict(doc(2, "shaft_40_v1.3.pdf", uploaded="2999-01-01T00:00:00"), file_id=F2)))
        first = client.post(base + "/revisions", json={"summary": bil("First"), "changes": [bil("x")]}, headers=h["eng"])
        assert first.status_code == 200, first.text
        # the incident: an editor swaps the drawing of the part for another file
        r = client.patch(base + "/parts/p1", json={"drawing_files": [{"file_id": F2, "name": "shaft_v1.3.pdf", "kind": "pdf"}]}, headers=h["eng"])
        assert r.status_code == 409 and r.json()["code"] == "production.released_file" and "revision 1.0" in r.json()["detail"]
        # a revision without a change line cannot supersede
        r = client.post(base + "/revisions", json={"summary": bil("Second"), "changes": [], "supersedes": [{"old_doc_id": "doc_1", "new_doc_id": "doc_2"}]}, headers=h["eng"])
        assert r.status_code == 422 and "change line" in r.json()["detail"]
        ok = client.post(base + "/revisions", json={"summary": bil("Second"), "changes": [bil("M5 thread added")], "supersedes": [{"old_doc_id": "doc_1", "new_doc_id": "doc_2"}]},
                         headers=h["eng"])
        assert ok.status_code == 200, ok.text
        # downloads: the current file is served, the superseded one warns first
        assert client.get(base + "/files/" + F2, headers=h["oem"]).content == b"v1.3 bytes"
        w = client.get(base + "/files/" + F1, headers=h["oem"])
        assert w.status_code == 409 and w.json()["code"] == "file.superseded" and "shaft_40_v1.3.pdf" in w.json()["detail"] and "revision 1.1" in w.json()["detail"]
        page = client.get(base + "/files/" + F1, headers={**h["oem"], "Accept": "text/html"})
        assert page.status_code == 409 and "SUPERSEDED / 已作废" in page.text and "confirm=1" in page.text and "不得按已作废的图纸制造零件" in page.text
        assert client.get(base + "/files/" + F1 + "?confirm=1", headers=h["oem"]).content == b"%PDF-1.4 original"
        # the document bundle and the part files do not hand the old drawing out
        zipped = client.get(base + "/documents.zip", headers=h["oem"])
        assert zipped.status_code == 200 and b"shaft_40_v1.2.pdf" not in zipped.content
