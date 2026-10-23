"""Focused memory and admission-bound regressions for process PDF rendering."""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from motor_ai_sim.production import process_pdf as PDF


def test_prepare_downsamples_before_exif_transpose_and_never_copies(tmp_path, monkeypatch):
    from PIL import Image, ImageOps

    source = tmp_path / "large.jpg"
    Image.new("RGB", (2400, 1800), "white").save(source)
    original_copy = Image.Image.copy

    def forbidden_copy(self):
        raise AssertionError("full resolution image copy is prohibited")

    seen = []
    def check_transpose(image):
        seen.append(image.size)
        assert max(image.size) <= 1600
        return original_copy(image)

    monkeypatch.setattr(Image.Image, "copy", forbidden_copy)
    monkeypatch.setattr(ImageOps, "exif_transpose", check_transpose)
    prepared = PDF._prepare_images({1: [{"path": source, "name": "large.jpg"}]}, str(tmp_path))
    assert seen and max(seen[0]) <= 1600 and seen[0] != (2400, 1800)
    assert Path(prepared[1][0]["path"]).stat().st_size > 0


def test_prepare_handles_exif_rotation_and_flattens_rgba_after_downsample(tmp_path):
    from PIL import Image

    rotated = tmp_path / "rotated.jpg"
    exif = Image.Exif()
    exif[274] = 6
    Image.new("RGB", (2400, 1800), "blue").save(rotated, exif=exif)
    rgba = tmp_path / "transparent.png"
    Image.new("RGBA", (24, 18), (255, 0, 0, 0)).save(rgba)

    prepared = PDF._prepare_images({1: [{"path": rotated}, {"path": rgba}]}, str(tmp_path))[1]
    with Image.open(prepared[0]["path"]) as result:
        assert result.size[0] <= 1600 and result.size[1] <= 1200
    with Image.open(prepared[1]["path"]) as result:
        assert result.mode == "RGB"
        assert result.getpixel((0, 0)) == (255, 255, 255)


def test_rejects_source_pixel_cap_before_decode(tmp_path, monkeypatch):
    class StubImage:
        width, height = 4001, 4000
        def __enter__(self): return self
        def __exit__(self, *_args): pass

    class StubPIL:
        DecompressionBombWarning = Warning
        @staticmethod
        def open(_path): return StubImage()

    monkeypatch.setattr("PIL.Image.open", StubPIL.open)
    source = tmp_path / "pixel-limit.png"
    source.write_bytes(b"x")
    with pytest.raises(PDF.ProcessPdfLimitError, match="16 megapixel"):
        PDF._prepare_images({1: [{"path": source}]}, str(tmp_path))


def test_rejects_combined_thumbnail_workload(tmp_path, monkeypatch):
    from PIL import Image

    monkeypatch.setattr(PDF, "MAX_PREPARED_IMAGE_PIXELS", 150)
    sources = []
    for index in range(2):
        path = tmp_path / ("small-%d.png" % index)
        Image.new("RGB", (10, 10), "white").save(path)
        sources.append({"path": path})
    with pytest.raises(PDF.ProcessPdfLimitError, match="20 megapixel workload"):
        PDF._prepare_images({1: sources}, str(tmp_path))


def test_html_workload_limit_is_checked_before_font_or_render(tmp_path, monkeypatch):
    monkeypatch.setattr(PDF, "MAX_HTML_BYTES", 3)
    monkeypatch.setattr(PDF, "_register_cjk_font", lambda: (_ for _ in ()).throw(AssertionError("font work began")))
    with pytest.raises(PDF.ProcessPdfLimitError, match="1 MiB PDF input"):
        PDF.render_process_pdf("\u00e9\u00e9", tmp_path / "out.pdf")


@pytest.mark.parametrize("fail", [False, True])
def test_render_semaphore_bounds_concurrency_and_releases_after_completion(monkeypatch, tmp_path, fail):
    monkeypatch.setattr(PDF, "MAX_HTML_BYTES", 100)
    monkeypatch.setattr(PDF, "_RENDER_SEMAPHORE", threading.BoundedSemaphore(2))
    active = peak = 0
    lock = threading.Lock()

    def render(_html, output_path, **_kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.025)
        with lock:
            active -= 1
        if fail and Path(output_path).name == "error.pdf":
            raise RuntimeError("synthetic renderer error")

    monkeypatch.setattr(PDF, "_render_process_pdf_locked", render)
    errors = []
    def run(name):
        try:
            PDF.render_process_pdf("ok", tmp_path / name)
        except RuntimeError as exc:
            errors.append(str(exc))

    threads = [threading.Thread(target=run, args=(name,)) for name in
               ("one.pdf", "two.pdf", "error.pdf", "four.pdf")]
    for thread in threads: thread.start()
    for thread in threads: thread.join(timeout=2)
    assert all(not thread.is_alive() for thread in threads)
    assert peak <= 2
    assert errors == (["synthetic renderer error"] if fail else [])
    # A fresh call proves the semaphore was released on both success and error.
    PDF.render_process_pdf("ok", tmp_path / "after.pdf")
