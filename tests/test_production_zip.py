"""Synthetic tests for production document ZIP authorization and contents."""
from __future__ import annotations

import json
import asyncio
import zipfile
from io import BytesIO

from starlette.requests import Request

from motor_ai_sim.production import store
from tests.test_production_access import ADMIN, ENGINEER, OEM_NDA, OEM_NO_NDA, NON_MEMBER, prod_env


DIE = "Synthetic 电机"
CONFIG = "Archive Test"


def _create(env, share_costs=False):
    body = {
        "title": {"en": "Synthetic", "zh": "合成测试", "src_lang": "en"},
        "current_revision": "2.4",
        "share_costs_with_oem": share_costs,
        "members": [
            {"email": ENGINEER, "role": "engineer"},
            {"email": OEM_NDA, "role": "oem"},
            {"email": OEM_NO_NDA, "role": "oem"},
        ],
    }
    response = env["client"].post("/api/admin/production/%s/%s" % (DIE, CONFIG), json=body, headers=env["admin"])
    assert response.status_code in (200, 201), response.text
    store.mutate_file(DIE, CONFIG, "production.json", lambda p: p.update(current_revision="2.4"))


def _zip(response):
    assert response.status_code == 200, response.text
    return zipfile.ZipFile(BytesIO(response.content))


def test_zip_authorization_and_internal_cost_rules(prod_env, monkeypatch):
    from motor_ai_sim import model3d as M, users as U

    monkeypatch.setattr(M, "download_log_path", lambda: prod_env["root"].parent / "downloads.jsonl")
    _create(prod_env, share_costs=False)
    c = prod_env["client"]
    unverified_email = "online-unverified@example.invalid"
    U.create_user(unverified_email, "synthetic-password", role="oem")
    U.set_nda(unverified_email, True, "2026-10-20", by="online", version="test-v1",
              company="Synthetic", record_id="synthetic-nda-record")
    store.mutate_file(DIE, CONFIG, "production.json", lambda p: p.setdefault("members", []).append(
        {"email": unverified_email, "role": "oem"}))
    unverified = {"Authorization": "Bearer %s" % U.issue_token(unverified_email)}
    url = "/api/production/%s/%s/documents.zip" % (DIE, CONFIG)
    c.post("/api/production/%s/%s/documents" % (DIE, CONFIG), headers=prod_env["engineer"],
                     files={"file": ("private quote.pdf", b"synthetic quote", "application/pdf")}).json()
    store.mutate_file(DIE, CONFIG, "documents.json", lambda d: d["documents"][0].update(internal=True))
    store.save_file(DIE, CONFIG, "costs.json", {"rows": [{"unit_price": 999999}], "extras": []})
    internal_id = store.load_file(DIE, CONFIG, "documents.json")["documents"][0]["file_id"]
    store.save_file(DIE, CONFIG, "threads.json", {"threads": [{"messages": [{
        "attachments": [{"file_id": internal_id, "name": "quoted-through-thread.pdf"}],
    }]}]})

    assert c.get(url, headers=prod_env["non_member"]).status_code == 404
    assert c.get(url, headers=prod_env["oem_no_nda"]).status_code == 403
    assert c.get(url, headers=unverified).status_code == 403
    for token in ("admin", "engineer"):
        archive = _zip(c.get(url, headers=prod_env[token]))
        assert "documents/private quote.pdf" in archive.namelist()
        assert b"999999" not in archive.read("MANIFEST.txt")
    archive = _zip(c.get(url, headers=prod_env["oem_nda"]))
    assert "documents/private quote.pdf" not in archive.namelist()
    assert "documents/quoted-through-thread.pdf" not in archive.namelist()
    assert all("costs.json" not in name for name in archive.namelist())

    # Shared-cost visibility only affects the structured BOM/cost endpoints; it does
    # not turn the raw costs store into a downloadable document.
    store.mutate_file(DIE, CONFIG, "production.json", lambda p: p.update(share_costs_with_oem=True))
    archive = _zip(c.get(url, headers=prod_env["oem_nda"]))
    assert "costs.json" not in archive.namelist()
    assert "documents/private quote.pdf" not in archive.namelist()


def test_zip_manifest_missing_utf8_names_streaming_and_audit(prod_env, monkeypatch):
    from motor_ai_sim import model3d as M

    log_path = prod_env["root"].parent / "downloads.jsonl"
    monkeypatch.setattr(M, "download_log_path", lambda: log_path)
    _create(prod_env)
    c = prod_env["client"]
    uploaded = c.post("/api/production/%s/%s/documents" % (DIE, CONFIG), headers=prod_env["engineer"],
                      files={"file": ("检验报告.pdf", b"synthetic report", "application/pdf")}).json()
    store.save_uploaded_file(DIE, CONFIG, "drawingfile1234567", b"synthetic drawing")
    missing_id = "missing1234567890"
    store.mutate_file(DIE, CONFIG, "documents.json", lambda d: d["documents"].append({
        "id": "doc_missing", "file_id": missing_id, "name": "缺失文件.txt", "internal": False,
    }))
    store.save_file(DIE, CONFIG, "parts.json", {"parts": [{
        "id": "part_synthetic", "name": {"en": "Bracket", "zh": "支架"},
        "drawing_files": [
            {"file_id": uploaded["file_id"], "name": "重复引用图纸.pdf", "kind": "pdf"},
            {"file_id": "drawingfile1234567", "name": "支架图纸.png", "kind": "png"},
        ],
        "optional_step": {"file_id": "optionalstep123456789"},
    }]})
    store.save_uploaded_file(DIE, CONFIG, "optionalstep123456789", b"synthetic optional step")
    store.save_uploaded_file(DIE, CONFIG, "threadattach123456789", b"synthetic thread attachment")
    store.save_file(DIE, CONFIG, "threads.json", {"threads": [{"messages": [{
        "attachments": [{"file_id": "threadattach123456789", "name": "assembly photo.jpg"}],
    }]}]})
    response = c.get("/api/production/%s/%s/documents.zip?lang=zh" % (DIE, CONFIG), headers=prod_env["engineer"])
    archive = _zip(response)
    names = archive.namelist()
    assert "documents/检验报告.pdf" in names
    assert "drawings/支架图纸.png" in names
    assert "drawings/Bracket optional step.step" in names
    assert "documents/assembly photo.jpg" in names
    assert len([name for name in names if "检验报告.pdf" in name or "重复引用图纸.pdf" in name]) == 1
    assert "MANIFEST.txt" in names
    assert "process/Synthetic 电机_Archive Test_process_zh.pdf" in names
    assert all(zi.flag_bits & 0x800 for zi in archive.infolist())
    manifest = archive.read("MANIFEST.txt").decode("utf-8")
    assert "Revision: 2.4" in manifest and "Generated (UTC):" in manifest
    assert "MISSING\tdocuments/缺失文件.txt" in manifest
    assert "检验报告.pdf" in manifest
    assert "filename*=UTF-8''Synthetic%20%E7%94%B5%E6%9C%BA_Archive%20Test_rev2.4_documents.zip" in response.headers["content-disposition"]
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1 and rows[0]["kind"] == "production_archive"
    assert rows[0]["bytes"] == len(response.content)
    audits = [json.loads(line) for line in store.audit_path(DIE, CONFIG).read_text(encoding="utf-8").splitlines()]
    archive_audits = [row for row in audits if row.get("entity") == "documents_zip"]
    assert len(archive_audits) == 1
    assert archive_audits[0]["snapshot"]["revision"] == "2.4"
    assert archive_audits[0]["snapshot"]["file_count"] == 5


def test_zip_uses_only_referenced_file_paths_and_rejects_bad_language(prod_env):
    _create(prod_env)
    c = prod_env["client"]
    root = store.file_dir(DIE, CONFIG)
    (root / "orphanedfile123456").write_bytes(b"orphan")
    url = "/api/production/%s/%s/documents.zip" % (DIE, CONFIG)
    assert c.get(url + "?lang=fr", headers=prod_env["admin"]).status_code == 422
    archive = _zip(c.get(url, headers=prod_env["admin"]))
    assert all("orphanedfile123456" not in name for name in archive.namelist())


def test_zip_name_sanitizing_and_casefold_collisions():
    from motor_ai_sim.production.routes import _unique_zip_name, _zip_component

    assert _zip_component(r"..\folder\CON") == "_CON"
    assert _zip_component("con.backup.pdf") == "_con.backup.pdf"
    assert _zip_component("CON .pdf") == "_CON .pdf"
    unicode_name = "report" + chr(30005) + chr(26426) + ".pdf"
    assert _zip_component("../" + unicode_name) == unicode_name
    long_chinese = "电" * 86 + ".png"
    truncated = _zip_component(long_chinese)
    assert truncated.endswith(".png")
    assert len(truncated.encode("utf-8")) <= 180
    long_collision = _unique_zip_name("drawings", long_chinese, {"drawings/" + truncated})
    assert long_collision.endswith(" (2).png")
    assert len(long_collision.split("/", 1)[1].encode("utf-8")) <= 180
    assert _zip_component("folder/../report" + chr(127) + ".pdf") == "report.pdf"
    used = set()
    upper_name = "r" + chr(201) + "sum" + chr(201) + ".PDF"
    lower_name = upper_name.casefold()
    assert _unique_zip_name("drawings", upper_name, used) == "drawings/" + upper_name
    assert _unique_zip_name("drawings", lower_name, used) == "drawings/" + lower_name[:-4] + " (2).pdf"


def test_zip_response_streams_in_chunks_and_cleans_up_on_disconnect(prod_env, monkeypatch):
    from motor_ai_sim import model3d as M
    from motor_ai_sim.production import routes

    monkeypatch.setattr(M, "download_log_path", lambda: prod_env["root"].parent / "downloads.jsonl")
    _create(prod_env)
    file_id = "largefile123456789"
    store.save_uploaded_file(DIE, CONFIG, file_id, b"x" * (2 * 1024 * 1024 + 17))
    store.save_file(DIE, CONFIG, "documents.json", {"documents": [{
        "id": "synthetic-large", "file_id": file_id, "name": "large.bin", "internal": False,
    }]})
    headers = prod_env["engineer"]["Authorization"].encode("latin-1")
    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"}, "http_version": "1.1", "method": "GET",
        "scheme": "http", "path": "/api/production/%s/%s/documents.zip" % (DIE, CONFIG),
        "raw_path": b"/", "query_string": b"lang=en", "root_path": "", "headers": [(b"authorization", headers)],
        "client": ("127.0.0.1", 1), "server": ("test", 80),
    }
    response = routes.download_documents_zip(Request(scope), DIE, CONFIG)
    staged = response.archive_path
    assert staged.is_file()
    async def collect_chunks():
        return [len(chunk) async for chunk in response.body_iterator]
    chunks = asyncio.run(collect_chunks())
    assert len(chunks) >= 3 and max(chunks) <= 1024 * 1024
    assert not staged.exists()

    response = routes.download_documents_zip(Request(scope), DIE, CONFIG)
    staged = response.archive_path
    async def fail_on_start():
        async def receive():
            return {"type": "http.disconnect"}
        async def send(message):
            if message["type"] == "http.response.start":
                return
            raise RuntimeError("synthetic client disconnect")
        try:
            await response(scope, receive, send)
        except BaseException:
            pass
    asyncio.run(fail_on_start())
    assert not staged.exists()


def test_zip_size_guard_returns_clear_error(prod_env, monkeypatch):
    from motor_ai_sim.production import routes

    _create(prod_env)
    monkeypatch.setattr(routes, "MAX_PRODUCTION_ZIP_BYTES", 1)
    response = prod_env["client"].get("/api/production/%s/%s/documents.zip" % (DIE, CONFIG), headers=prod_env["admin"])
    assert response.status_code == 413
    assert "256 MiB" in response.json()["detail"]


def test_zip_entry_guard_counts_manifest(prod_env, monkeypatch):
    from motor_ai_sim.production import routes

    _create(prod_env)
    file_id = "entrylimit12345678"
    store.save_uploaded_file(DIE, CONFIG, file_id, b"synthetic")
    store.save_file(DIE, CONFIG, "documents.json", {"documents": [{
        "id": "entry-limit", "file_id": file_id, "name": "entry.txt", "internal": False,
    }]})
    monkeypatch.setattr(routes, "MAX_PRODUCTION_ZIP_FILES", 2)
    response = prod_env["client"].get("/api/production/%s/%s/documents.zip" % (DIE, CONFIG), headers=prod_env["admin"])
    assert response.status_code == 413
    assert "2 entry limit" in response.json()["detail"]


def test_zip_copy_guard_enforces_actual_growth_beyond_stat(prod_env, monkeypatch, tmp_path):
    from pathlib import Path
    from motor_ai_sim.production import routes
    from motor_ai_sim.production import process_pdf as PDF

    _create(prod_env)
    file_id = "growingfile12345678"
    store.save_uploaded_file(DIE, CONFIG, file_id, b"g" * 8192)
    store.save_file(DIE, CONFIG, "documents.json", {"documents": [{
        "id": "growing-file", "file_id": file_id, "name": "growing.bin", "internal": False,
    }]})
    source_path = store.read_uploaded_file(DIE, CONFIG, file_id)
    stub_pdf = b"%PDF-1.4 small synthetic PDF"
    monkeypatch.setattr(PDF, "render_process_pdf", lambda _html, output_path, **_kwargs: Path(output_path).write_bytes(stub_pdf))
    monkeypatch.setattr(routes, "MAX_PRODUCTION_ZIP_BYTES", 4096)
    original_stat = Path.stat

    class StatProxy:
        def __init__(self, original):
            self.original = original
        @property
        def st_size(self):
            return 1
        def __getattr__(self, name):
            return getattr(self.original, name)

    def underreport_source(path, *args, **kwargs):
        actual = original_stat(path, *args, **kwargs)
        return StatProxy(actual) if path == source_path else actual

    monkeypatch.setattr(Path, "stat", underreport_source)
    import tempfile
    original_mkstemp = tempfile.mkstemp
    monkeypatch.setattr(routes.tempfile, "mkstemp", lambda **kwargs: original_mkstemp(dir=tmp_path, **kwargs))
    original_open = Path.open
    source_read_bytes = []
    def track_source_open(path, mode="r", *args, **kwargs):
        source = original_open(path, mode, *args, **kwargs)
        if path != source_path or "r" not in mode or "b" not in mode:
            return source
        class ReadTracker:
            def __enter__(self):
                source.__enter__()
                return self
            def __exit__(self, *exc):
                return source.__exit__(*exc)
            def read(self, *args, **kwargs):
                data = source.read(*args, **kwargs)
                source_read_bytes.append(len(data))
                return data
            def __getattr__(self, name):
                return getattr(source, name)
        return ReadTracker()
    monkeypatch.setattr(Path, "open", track_source_open)
    response = prod_env["client"].get("/api/production/%s/%s/documents.zip" % (DIE, CONFIG), headers=prod_env["admin"])
    assert response.status_code == 413
    assert sum(source_read_bytes) == 8192, "the actual 8 KiB source must be read before growth is detected"
    assert not list(tmp_path.glob("production-documents-*.zip"))
