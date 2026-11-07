"""Production uploads: a member has a per-file limit, a per-production quota and a list of file types; staff have larger limits,
no quota and no type list; the file goes to disk in chunks, never whole into memory."""
from __future__ import annotations

import pytest
from starlette.datastructures import UploadFile

from motor_ai_sim.production import routes as R
from tests.test_production_access import CONFIG, DIE, ENGINEER, OEM_NDA, prod_env  # noqa: F401  (fixture)

URL = "/api/production/%s/%s/documents" % (DIE, CONFIG)


@pytest.fixture
def env(prod_env):  # noqa: F811
    payload = {"title": {"en": "P", "zh": "P", "src_lang": "en", "status": {"en": "confirmed", "zh": "confirmed"}},
               "order_quantity": 100,
               "members": [{"email": ENGINEER, "role": "engineer"}, {"email": OEM_NDA, "role": "oem"}]}
    r = prod_env["client"].post("/api/admin/production/%s/%s" % (DIE, CONFIG), json=payload, headers=prod_env["admin"])
    assert r.status_code in (200, 201), r.text
    return prod_env


def _stored(env) -> list:
    from motor_ai_sim.production import store
    return sorted(p for p in store.file_dir(DIE, CONFIG).iterdir())


def _send(env, who: str, name: str, data: bytes = b"content"):
    return env["client"].post(URL, headers=env[who], files={"file": (name, data)})


@pytest.mark.parametrize("name", [
    "bench run 3.mp4", "PXL_20261105.MOV", "rotor.step", "housing.STP", "report.pdf", "losses.xlsx", "points.csv",
    "stator.jpg", "coil.jpeg", "plot.png", "board.kicad_pcb", "gerbers.zip", "检验报告.pdf",
])
def test_a_member_can_send_what_a_production_uses(env, name):
    r = _send(env, "oem_nda", name)
    assert r.status_code == 200, r.text
    assert r.json()["name"] == name


@pytest.mark.parametrize("name", ["page.html", "tool.exe", "script.js", "image.svg", "run.bat", "no_extension", "report.pdf.exe"])
def test_a_member_cannot_send_what_would_run(env, name):
    r = _send(env, "oem_nda", name)
    assert r.status_code == 415, r.text
    assert _stored(env) == []


@pytest.mark.parametrize("who", ["admin", "engineer"])
def test_staff_have_no_type_list(env, who):
    assert _send(env, who, "notes.html").status_code == 200


def test_an_80_mb_bench_video_goes_through_in_chunks(env, monkeypatch):
    async def whole_file_read(self, size=-1):
        raise AssertionError("the upload was read whole into memory")
    monkeypatch.setattr(UploadFile, "read", whole_file_read)
    size = 80 * 1024 * 1024
    r = _send(env, "oem_nda", "bench.mp4", b"\0" * size)
    assert r.status_code == 200, r.text
    assert [p.stat().st_size for p in _stored(env)] == [size]


def test_a_file_over_the_member_limit_leaves_nothing_behind(env, monkeypatch):
    monkeypatch.setattr(R, "UPLOAD_MEMBER_FILE_MAX_BYTES", 2 * 1024 * 1024 + 100)
    # (the declared length passes the early check, which allows for the multipart wrapping: the stream refuses it)
    r = _send(env, "oem_nda", "large.mp4", b"\0" * (3 * 1024 * 1024))
    assert r.status_code == 413, r.text
    assert _stored(env) == []
    assert env["client"].get(URL, headers=env["oem_nda"]).json()["documents"] == []


def test_a_declared_length_over_the_limit_is_refused_before_it_is_parsed(env):
    headers = {**env["oem_nda"], "Content-Length": str(R.UPLOAD_MEMBER_FILE_MAX_BYTES + 3 * 1024 * 1024)}
    assert env["client"].post(URL, headers=headers, content=b"x").status_code == 413


def test_the_quota_stops_a_member_and_a_refusal_keeps_the_stored_files(env, monkeypatch):
    monkeypatch.setattr(R, "UPLOAD_MEMBER_PRODUCTION_MAX_BYTES", 30)
    assert _send(env, "oem_nda", "a.pdf", b"x" * 20).status_code == 200
    assert _send(env, "oem_nda", "b.pdf", b"x" * 20).status_code == 413          # 40 would pass the quota
    assert _send(env, "oem_nda", "c.pdf", b"x" * 10).status_code == 200          # exactly the rest fits
    assert _send(env, "oem_nda", "d.pdf", b"x").status_code == 413               # full
    assert len(_stored(env)) == 2


def test_staff_files_do_not_use_up_the_members_quota(env, monkeypatch):
    monkeypatch.setattr(R, "UPLOAD_MEMBER_PRODUCTION_MAX_BYTES", 30)
    assert _send(env, "admin", "video.mp4", b"x" * 100).status_code == 200
    assert _send(env, "engineer", "video2.mp4", b"x" * 100).status_code == 200   # staff: no quota
    assert _send(env, "oem_nda", "photo.jpg", b"x" * 20).status_code == 200


def test_a_message_attachment_follows_the_same_limits(env):
    c = env["client"]
    thread = c.post("/api/production/%s/%s/threads" % (DIE, CONFIG), headers=env["oem_nda"], json={})
    assert thread.status_code == 200, thread.text
    url = "/api/production/%s/%s/threads/%s/messages" % (DIE, CONFIG, thread.json()["id"])
    text = '{"en": "see attached", "zh": "see attached", "src_lang": "en"}'
    fields = {"text": text, "src_lang": "en"}
    ok = c.post(url, headers=env["oem_nda"], data=fields, files={"files": ("photo.jpg", b"jpeg")})
    assert ok.status_code == 200, ok.text
    assert len(ok.json()["messages"][-1]["attachments"]) == 1
    bad = c.post(url, headers=env["oem_nda"], data=fields, files={"files": ("tool.exe", b"MZ")})
    assert bad.status_code == 415, bad.text
    assert len(_stored(env)) == 1
    empty = c.post(url, headers=env["oem_nda"], data=fields, files={"files": ("photo.jpg", b"")})
    assert empty.status_code == 200 and empty.json()["messages"][-1]["attachments"] == []
