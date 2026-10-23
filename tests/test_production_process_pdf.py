"""Synthetic integration coverage for generated production process PDFs."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from motor_ai_sim.production import store
from tests.test_production_access import ADMIN, ENGINEER, OEM_NDA, OEM_NO_NDA, NON_MEMBER, prod_env


DIE = "Synthetic PDF"
CONFIG = "Process Test"


def _create(env, share_costs=False):
    body = {
        "title": {"en": "Synthetic process", "zh": "合成工艺", "src_lang": "en"},
        "members": [{"email": ENGINEER, "role": "engineer"}, {"email": OEM_NDA, "role": "oem"},
                    {"email": OEM_NO_NDA, "role": "oem"}],
        "share_costs_with_oem": share_costs,
    }
    response = env["client"].post("/api/admin/production/%s/%s" % (DIE, CONFIG),
                                  json=body, headers=env["admin"])
    assert response.status_code in (200, 201), response.text
    store.mutate_file(DIE, CONFIG, "production.json", lambda p: p.update(current_revision="3.1"))


def _png(label: str, width=320, height=180) -> bytes:
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    draw.rectangle((8, 8, width - 8, height - 8), outline="#1f4e79", width=4)
    draw.text((24, max(12, height // 2)), label, fill="black")
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def _configure_synthetic_process(internal=False):
    step_file_id = "stepimage12345678"
    portrait_file_id = "portraitimage123"
    part_file_id = "partimage123456789"
    internal_file_id = "secretimage1234567"
    step_doc = {"id": "step_photo", "file_id": step_file_id, "name": "step-photo.png",
                "step_id": "s1", "internal": False}
    portrait_doc = {"id": "portrait_photo", "file_id": portrait_file_id, "name": "portrait-photo.png",
                    "step_id": "s1", "internal": False}
    internal_doc = {"id": "internal_photo", "file_id": internal_file_id, "name": "internal-photo.png",
                    "step_id": "s1", "internal": internal}
    store.save_uploaded_file(DIE, CONFIG, step_file_id, _png("step image"))
    store.save_uploaded_file(DIE, CONFIG, portrait_file_id, _png("portrait image", width=120, height=620))
    store.save_uploaded_file(DIE, CONFIG, part_file_id, _png("part drawing"))
    store.save_uploaded_file(DIE, CONFIG, internal_file_id, _png("internal image"))
    store.save_file(DIE, CONFIG, "documents.json", {"documents": [step_doc, portrait_doc, internal_doc]})
    store.save_file(DIE, CONFIG, "parts.json", {"parts": [{
        "id": "synthetic_part", "name": {"en": "Bracket", "zh": "支架"}, "kind": "made",
        "drawing_files": [
            {"file_id": part_file_id, "kind": "png", "name": "bracket.png"},
            {"file_id": internal_file_id, "kind": "png", "name": "secret-private.png"},
        ],
        "optional_step": None,
    }]})
    long_text = "这是合成中文文本，用于PDF字体和分页检查。 " * 36
    sections = [{
        "id": "inspection", "title": {"en": "Inspection", "zh": "工序检查"},
        "body": {"en": "Synthetic PDF validation text. " * 25, "zh": (long_text + "\n\n") * 12},
        "tables": [[["Check", "Value"], ["Turns", "33"]]],
        "tables_i18n": {"zh": [[
            ["检查项目", "验收值"], ["匝数", "33"], ["长文本单元格" * 28, "合格"],
        ]]},
    }]
    store.save_file(DIE, CONFIG, "process.json", {"sections": sections})
    store.save_file(DIE, CONFIG, "assembly_process.json", {"steps": [{
        "id": "s1", "n": 1, "title": {"en": "Press coils", "zh": "压制线圈"},
        "kind": "operation", "parts": ["synthetic_part"], "after": [], "sections": ["inspection"],
        "documents": ["step_photo", "portrait_photo", "internal_photo"],
    }]})


def test_download_process_pdf_embeds_cjk_images_table_and_page_numbers(prod_env):
    fitz = pytest.importorskip("fitz")
    _create(prod_env)
    _configure_synthetic_process()
    url = "/api/production/%s/%s/process_document" % (DIE, CONFIG)
    view = prod_env["client"].get(url + "?lang=zh", headers=prod_env["engineer"])
    assert view.status_code == 200 and view.headers["content-type"].startswith("text/html")
    response = prod_env["client"].get(url + "?lang=zh-CN&download=true", headers=prod_env["engineer"])
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/pdf")
    assert "filename=\"Synthetic_PDF_Process_Test_process_zh.pdf\"" in response.headers["content-disposition"]
    assert "filename*=UTF-8''Synthetic%20PDF_Process%20Test_process_zh.pdf" in response.headers["content-disposition"]

    scratch = Path(__file__).parents[1] / "_scratch" / "part2"
    scratch.mkdir(parents=True, exist_ok=True)
    artifact = scratch / "synthetic_process_zh.pdf"
    artifact.write_bytes(response.content)
    pdf = fitz.open(stream=response.content, filetype="pdf")
    assert len(pdf) >= 2
    text = "\n".join(page.get_text() for page in pdf)
    assert "工序检查" in text and "检查项目" in text and "匝数" in text and "长文本单元格" in text
    assert "第 1 页" in text and "第 2 页" in text
    for page in pdf:
        assert abs(page.rect.width - 595.28) < 1 and abs(page.rect.height - 841.89) < 1
    assert sum(len(page.get_images(full=True)) for page in pdf) == 4
    image_rects = [rect for page in pdf for image in page.get_images(full=True)
                   for rect in page.get_image_rects(image[0])]
    assert image_rects
    for rect in image_rects:
        assert rect.x0 >= 56 and rect.x1 <= 539
        assert rect.height <= 623.7
    for page in pdf:
        for word in page.get_text("words"):
            assert word[0] >= 50 and word[2] <= 545
    fonts = {font[0] for page in pdf for font in page.get_fonts(full=True)}
    assert fonts
    assert any(fitz_doc.extract_font(font_id)[3] for font_id in fonts for fitz_doc in [pdf])
    pdf.close()


def test_process_pdf_download_keeps_member_nda_and_admin_access_gates(prod_env, monkeypatch):
    from motor_ai_sim.production import process_pdf as PDF

    _create(prod_env)
    _configure_synthetic_process()

    def synthetic_pdf(_html, output_path, **_kwargs):
        Path(output_path).write_bytes(b"%PDF-1.4 synthetic test document")

    monkeypatch.setattr(PDF, "render_process_pdf", synthetic_pdf)
    url = "/api/production/%s/%s/process_document?download=true" % (DIE, CONFIG)
    assert prod_env["client"].get(url, headers=prod_env["non_member"]).status_code == 404
    assert prod_env["client"].get(url, headers=prod_env["oem_no_nda"]).status_code == 403
    admin = prod_env["client"].get(url, headers=prod_env["admin"])
    assert admin.status_code == 200 and admin.content.startswith(b"%PDF-")


def test_internal_step_and_part_images_stay_hidden_from_oem_even_with_shared_costs(prod_env):
    fitz = pytest.importorskip("fitz")
    _create(prod_env, share_costs=True)
    _configure_synthetic_process(internal=True)
    url = "/api/production/%s/%s/process_document?lang=zh&download=true" % (DIE, CONFIG)
    response = prod_env["client"].get(url, headers=prod_env["oem_nda"])
    assert response.status_code == 200, response.text
    pdf = fitz.open(stream=response.content, filetype="pdf")
    assert sum(len(page.get_images(full=True)) for page in pdf) == 3
    text = "\n".join(page.get_text() for page in pdf)
    assert "internal-photo.png" not in text and "secret-private.png" not in text


def test_zip_uses_process_pdf_and_only_renderer_unavailability_falls_back(prod_env, monkeypatch):
    from motor_ai_sim.production import process_pdf as PDF

    _create(prod_env)
    _configure_synthetic_process()
    url = "/api/production/%s/%s/documents.zip?lang=zh" % (DIE, CONFIG)
    response = prod_env["client"].get(url, headers=prod_env["engineer"])
    assert response.status_code == 200, response.text
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    assert "process/Synthetic PDF_Process Test_process_zh.pdf" in archive.namelist()
    assert not any(name.endswith("process_zh.html") for name in archive.namelist())

    monkeypatch.setattr(PDF, "_register_cjk_font", lambda: (_ for _ in ()).throw(PDF.ProcessPdfUnavailable("synthetic unavailable")))
    direct = prod_env["client"].get("/api/production/%s/%s/process_document?lang=zh&download=true" % (DIE, CONFIG),
                                    headers=prod_env["engineer"])
    assert direct.status_code == 503
    fallback = prod_env["client"].get(url, headers=prod_env["engineer"])
    assert fallback.status_code == 200, fallback.text
    fallback_zip = zipfile.ZipFile(io.BytesIO(fallback.content))
    assert "process/Synthetic PDF_Process Test_process_zh.html" in fallback_zip.namelist()
    assert "NOTE\tProcess PDF renderer or CJK font unavailable; HTML included instead." in fallback_zip.read("MANIFEST.txt").decode("utf-8")

    monkeypatch.setattr(PDF, "render_process_pdf", lambda *args, **kwargs: (_ for _ in ()).throw(PDF.ProcessPdfRenderError("synthetic render failure")))
    direct_error = prod_env["client"].get("/api/production/%s/%s/process_document?download=true" % (DIE, CONFIG),
                                          headers=prod_env["engineer"])
    zip_error = prod_env["client"].get(url, headers=prod_env["engineer"])
    assert direct_error.status_code == 500 and zip_error.status_code == 500
