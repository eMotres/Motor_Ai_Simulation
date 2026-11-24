"""Text of an uploaded specification file (``development/spec_file_extract.py``): every type, every refusal, every limit.
Files are generated in the test (synthetic data only); no model, no network.  Tests that need ``pypdf`` / ``xlrd`` are skipped with a
reason when the library is absent, and a stub covers the PDF rules either way."""
from __future__ import annotations

import io
import os
import socket
import struct
import subprocess
import sys
import types
import zipfile

import pytest

from motor_ai_sim.development import spec_file_extract as X


def _err(name, data):
    with pytest.raises(X.ExtractError) as info:
        X.extract(name, data)
    return info.value


# ── generators ───────────────────────────────────────────────────────────────

def make_docx(paragraphs=("Motor requirements",), table=(("Parameter", "Value"), ("Rated power", "2.2 kW")), bomb_member=None) -> bytes:
    docx = pytest.importorskip("docx")
    d = docx.Document()
    for p in paragraphs:
        d.add_paragraph(p)
    if table:
        t = d.add_table(rows=len(table), cols=len(table[0]))
        for r, row in enumerate(table):
            for c, text in enumerate(row):
                t.cell(r, c).text = text
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def make_xlsx(sheets=None, hidden=()) -> bytes:
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in (sheets or {"Spec": [("Parameter", "Value", "Unit"), ("Rated power", 2.2, "kW"), ("Max speed", 6000, "rpm")]}).items():
        ws = wb.create_sheet(name)
        for row in rows:
            ws.append(list(row))
        if name in hidden:
            ws.sheet_state = "hidden"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def make_pdf(pages) -> bytes:
    """One page per entry; a list of lines of text, or None for a page with only a drawn rectangle (a scan)."""
    canvas = pytest.importorskip("reportlab.pdfgen.canvas")
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for lines in pages:
        if lines is None:
            c.rect(50, 50, 300, 300, fill=1)
        else:
            y = 800
            for line in lines:
                c.drawString(50, y, line)
                y -= 16
        c.showPage()
    c.save()
    return buf.getvalue()


def make_xls(rows, hidden=False, extra_dir=None, sheet_type=0) -> bytes:
    """A minimal BIFF8 workbook inside an OLE2 compound file (one sheet; text and numbers), no third-party writer needed."""
    def rec(rid, body=b""):
        return struct.pack("<HH", rid, len(body)) + body

    def unistr(s):
        return struct.pack("<HB", len(s), 1) + s.encode("utf-16-le")

    sheet = rec(0x0809, struct.pack("<HHHHII", 0x0600, 0x0010, 0x0DBB, 0x07CC, 0, 6))
    nrows = len(rows)
    ncols = max(len(r) for r in rows)
    sheet += rec(0x0200, struct.pack("<IIHHH", 0, nrows, 0, ncols, 0))
    for r, row in enumerate(rows):
        for c, v in enumerate(row):
            if v is None:
                continue
            if isinstance(v, str):
                sheet += rec(0x0204, struct.pack("<HHH", r, c, 0) + unistr(v))
            else:
                sheet += rec(0x0203, struct.pack("<HHHd", r, c, 0, float(v)))
    sheet += rec(0x000A)
    name = b"Spec"
    glob_head = rec(0x0809, struct.pack("<HHHHII", 0x0600, 0x0005, 0x0DBB, 0x07CC, 0, 6)) + rec(0x0042, struct.pack("<H", 1200)) \
        + rec(0x0022, struct.pack("<H", 0))

    def boundsheet(offset):
        return rec(0x0085, struct.pack("<IBBB", offset, 1 if hidden else 0, sheet_type, len(name)) + b"\x00" + name)

    offset = len(glob_head) + len(boundsheet(0)) + 4              # the sheet starts after the globals' closing EOF (4 bytes)
    book = glob_head + boundsheet(offset) + rec(0x000A) + sheet
    book = book.ljust(4096, b"\x00")
    n_sec = len(book) // 512
    end, free = 0xFFFFFFFE, 0xFFFFFFFF
    fat = [0xFFFFFFFD, end] + [2 + i + 1 for i in range(n_sec - 1)] + [end]
    fat[1 + n_sec] = end
    fat = (fat + [free] * 128)[:128]

    def dirent(nm, typ, child, start, size):
        raw = nm.encode("utf-16-le") + b"\x00\x00"
        return (raw.ljust(64, b"\x00") + struct.pack("<HBB", len(raw), typ, 1) + struct.pack("<III", free, free, child)
                + b"\x00" * 16 + b"\x00" * 4 + b"\x00" * 16 + struct.pack("<IQ", start, size)).ljust(128, b"\x00")[:128]

    directory = (dirent("Root Entry", 5, 1, end, 0) + dirent("Workbook", 2, free, 2, len(book))
                 + (dirent(extra_dir, 1, free, end, 0) if extra_dir else b"\x00" * 128) + b"\x00" * 128)
    header = (bytes.fromhex("D0CF11E0A1B11AE1") + b"\x00" * 16 + struct.pack("<HHHHH", 0x3E, 3, 0xFFFE, 9, 6) + b"\x00" * 6
              + struct.pack("<IIIIIIIII", 0, 1, 1, 0, 4096, end, 0, end, 0) + struct.pack("<I", 0)
              + struct.pack("<108I", *([free] * 108)))
    return header.ljust(512, b"\x00") + struct.pack("<128I", *fat) + directory + book


def zip_with(members: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in members.items():
            z.writestr(name, data)
    return buf.getvalue()


# ── TXT / CSV / MD ───────────────────────────────────────────────────────────

def test_txt_utf8_and_gb18030_and_csv_md_come_through_the_text_path():
    out = X.extract("req.txt", "Rated power: 2.2 kW\nMax speed: 6000 rpm\n".encode("utf-8"))
    assert out.kind == "txt" and out.blocks[0].ref == "lines 1-3" and "6000 rpm" in out.blocks[0].text
    zh = X.extract("规格.txt", "额定功率：2.2 kW\n最高转速：6000 rpm\n".encode("gb18030"))
    assert "最高转速" in zh.blocks[0].text
    assert X.extract("t.csv", b"a,b\n1,2\n").kind == "txt" and X.extract("n.md", b"# Title\nvalue 3\n").kind == "txt"


def test_txt_blocks_carry_line_ranges_and_control_characters_are_dropped():
    text = "\n".join(f"line {i}" for i in range(1, 101)) + "\x01\x02"
    out = X.extract("a.txt", text.encode())
    assert [b.ref for b in out.blocks] == ["lines 1-40", "lines 41-80", "lines 81-100"]
    assert "\x01" not in out.blocks[-1].text


def test_binary_content_in_a_txt_is_refused():
    assert _err("a.txt", b"\x00" * 500 + b"text").code == "mismatch"
    assert _err("a.txt", b"%PDF-1.4 something").code == "mismatch"
    assert _err("a.txt", b"PK\x03\x04rest").code == "mismatch"


def test_empty_unsupported_and_oversize():
    assert _err("a.txt", b"").code == "empty"
    assert _err("a.png", b"\x89PNG....").code == "unsupported"
    assert _err("a.exe", b"MZ\x90\x00").code == "unsupported"
    big = _err("a.txt", b"x" * (X.MAX_BYTES + 1))
    assert big.code == "too_big" and "5 MB" in big.en and "5 MB" in big.zh
    assert _err("a.txt", b"  \n \n").code == "no_text"


def test_text_over_the_character_cap_is_refused():
    assert _err("a.txt", (("x" * 99 + "\n") * 1100).encode()).code == "text_big"


# ── DOC / DOCM / XLSM / OLE ──────────────────────────────────────────────────

def test_doc_is_refused_by_name_and_by_ole_magic_with_the_resave_message():
    e = _err("old.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 600)
    assert e.code == "ole_doc" and "re-save as DOCX" in e.en and "DOCX" in e.zh
    renamed = _err("report.docx", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 600)    # an old file under a new name
    assert renamed.code == "ole_other"
    assert _err("x.txt", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 600).code == "ole_other"


@pytest.mark.parametrize("name", ["a.docm", "b.xlsm", "c.dotm"])
def test_macro_containers_are_refused_by_name(name):
    assert _err(name, b"PK\x03\x04abc").code == "macro"


def test_a_macro_container_renamed_to_docx_is_refused_by_content():
    base = make_docx()
    z = zipfile.ZipFile(io.BytesIO(base))
    members = {n: z.read(n) for n in z.namelist()}
    with_vba = dict(members, **{"word/vbaProject.bin": b"\x00" * 100})
    assert _err("a.docx", zip_with(with_vba)).code == "embedded"
    ct = members["[Content_Types].xml"].replace(b"document.main+xml", b"document.macroEnabled.main+xml")
    assert _err("a.docx", zip_with(dict(members, **{"[Content_Types].xml": ct}))).code == "macro"


def test_external_links_and_embedded_objects_are_refused():
    members = {"[Content_Types].xml": b"<Types/>", "xl/workbook.xml": b"<w/>", "xl/externalLinks/externalLink1.xml": b"<x/>"}
    assert _err("a.xlsx", zip_with(members)).code == "embedded"
    members = {"[Content_Types].xml": b"<Types/>", "word/document.xml": b"<w/>", "word/embeddings/oleObject1.bin": b"x"}
    assert _err("a.docx", zip_with(members)).code == "embedded"


# ── DOCX ─────────────────────────────────────────────────────────────────────

def test_docx_paragraphs_tables_and_headers_are_read_with_refs():
    out = X.extract("req.docx", make_docx(("Motor requirements", "Voltage 48 V"), (("Parameter", "Value"), ("Rated power", "2.2 kW"))))
    refs = {b.ref: b.text for b in out.blocks}
    assert refs["paragraph 1"] == "Motor requirements" and refs["paragraph 2"] == "Voltage 48 V"
    assert refs["table 1 row 2"] == "Rated power | 2.2 kW"
    assert out.kind == "docx"


def test_docx_that_is_not_a_zip_or_is_broken():
    assert _err("a.docx", b"not a zip at all").code == "mismatch"
    assert _err("a.docx", b"PK\x03\x04" + b"junk" * 50).code == "unreadable"


# ── zip bombs ────────────────────────────────────────────────────────────────

def test_zip_bomb_by_ratio_is_refused_before_any_parser_runs():
    members = {"[Content_Types].xml": b"<Types/>", "word/document.xml": b"0" * (30 * 1024 * 1024)}
    data = zip_with(members)
    assert len(data) < 200_000                                   # 30 MB of zeros packs to a few KB
    assert _err("a.docx", data).code == "bomb"
    assert _err("a.xlsx", data).code == "bomb"


def test_zip_bomb_by_total_size_and_member_count():
    import random
    rng = random.Random(7)
    # 60 members of 1 MB each, a coin flip between two words: packs about 10:1 (under the per-member ratio limit), 60 MB in total
    members = {f"xl/s{i}.xml": "".join(rng.choice(("abcdefgh ", "ijklmnop ")) for _ in range(110_000)).encode() for i in range(60)}
    data = zip_with(members)
    assert len(data) < X.MAX_BYTES and sum(len(v) for v in members.values()) > X.ZIP_MAX_TOTAL
    assert _err("a.xlsx", data).code == "bomb"
    many = {f"xl/m{i}.xml": b"<a/>" for i in range(X.ZIP_MAX_MEMBERS + 1)}
    assert _err("a.xlsx", zip_with(many)).code == "bomb"


def test_encrypted_zip_member_is_refused():
    data = bytearray(zip_with({"[Content_Types].xml": b"<Types/>", "xl/workbook.xml": b"<w/>"}))
    # set the "encrypted" bit (0x1) in the general purpose flags of both headers
    for sig, off in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        i = 0
        while True:
            i = data.find(sig, i)
            if i < 0:
                break
            data[i + off] |= 1
            i += 4
    assert _err("a.xlsx", bytes(data)).code == "encrypted"


# ── XLSX ─────────────────────────────────────────────────────────────────────

def test_xlsx_cached_values_with_sheet_and_cell_refs():
    out = X.extract("spec.xlsx", make_xlsx())
    refs = {b.ref: b.text for b in out.blocks}
    assert refs["Spec!A2:C2"] == "A2=Rated power; B2=2.2; C2=kW"
    assert refs["Spec!A3:C3"] == "A3=Max speed; B3=6000; C3=rpm"          # 6000.0 reads as 6000
    assert out.kind == "xlsx" and out.stats["cells"] == 9


def test_xlsx_formula_without_a_cached_value_is_not_evaluated():
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    wb.active["A1"] = "power"
    wb.active["B1"] = "=1+1"                                              # written without a cached result
    buf = io.BytesIO()
    wb.save(buf)
    out = X.extract("f.xlsx", buf.getvalue())
    assert out.blocks[0].text == "A1=power"                               # nothing was calculated


def test_xlsx_hidden_sheet_is_flagged():
    out = X.extract("h.xlsx", make_xlsx({"Spec": [("a", 1)], "Notes": [("b", 2)]}, hidden=("Notes",)))
    assert any(b.ref.startswith("Notes (hidden)!") for b in out.blocks) and out.warnings == ["hidden sheet: Notes"]


def test_xlsx_sheet_and_cell_limits():
    many_sheets = {f"S{i}": [("a", i)] for i in range(X.MAX_SHEETS + 1)}
    assert _err("a.xlsx", make_xlsx(many_sheets)).code == "sheets"
    assert X.extract("a.xlsx", make_xlsx({f"S{i}": [("a", i)] for i in range(X.MAX_SHEETS)})).stats["sheets"] == X.MAX_SHEETS
    one_big = {"S": [tuple(range(40)) for _ in range(60)]}                 # 2400 cells on one sheet > 2000
    assert _err("a.xlsx", make_xlsx(one_big)).code == "cells"
    spread = {f"S{i}": [tuple(range(1, 41)) for _ in range(30)] for i in range(5)}      # 1200 per sheet, 6000 in total > 5000
    assert _err("a.xlsx", make_xlsx(spread)).code == "cells"


# ── PDF (stub always; real pypdf when importable) ────────────────────────────

class _Page(dict):
    """A page of the stub reader: an (empty) PDF page dictionary with a text."""

    def __init__(self, text):
        super().__init__()
        self._t = text

    def extract_text(self):
        if isinstance(self._t, Exception):
            raise self._t
        return self._t


def _stub_pypdf(monkeypatch, pages, encrypted=False):
    mod = types.ModuleType("pypdf")

    class PdfReader:
        def __init__(self, stream, strict=False):
            self.is_encrypted = encrypted
            self.pages = [_Page(t) for t in pages]
            self.trailer = {"/Root": {}}

    mod.PdfReader = PdfReader
    monkeypatch.setitem(sys.modules, "pypdf", mod)


GOOD = "Rated power 2.2 kW at 4800 rpm. Maximum speed 6000 rpm. DC supply 48 V nominal. " * 3
PDF = b"%PDF-1.4\n%stub\n"


def test_pdf_text_with_page_refs_stub(monkeypatch):
    _stub_pypdf(monkeypatch, [GOOD, GOOD + " more"])
    out = X.extract("s.pdf", PDF)
    assert [b.ref for b in out.blocks] == ["page 1", "page 2"] and out.kind == "pdf"


def test_scanned_pdf_gets_the_text_version_message_stub(monkeypatch):
    _stub_pypdf(monkeypatch, ["", "  ", ""])
    e = _err("scan.pdf", PDF)
    assert e.code == "scanned" and e.en == "This PDF has no text; please send the text version."
    _stub_pypdf(monkeypatch, [GOOD, "", "", ""])                           # half or more pages empty
    assert _err("half.pdf", PDF).code == "scanned"
    _stub_pypdf(monkeypatch, ["�" * 400])                             # unreadable embedded font
    assert _err("font.pdf", PDF).code == "scanned"


def test_pdf_page_limit_encryption_and_bad_page_stub(monkeypatch):
    _stub_pypdf(monkeypatch, [GOOD] * (X.MAX_PDF_PAGES + 1))
    assert _err("big.pdf", PDF).code == "pages"
    _stub_pypdf(monkeypatch, [GOOD] * X.MAX_PDF_PAGES)
    assert X.extract("ok.pdf", PDF).stats["pages"] == X.MAX_PDF_PAGES
    _stub_pypdf(monkeypatch, [GOOD], encrypted=True)
    assert _err("lock.pdf", PDF).code == "encrypted"
    _stub_pypdf(monkeypatch, [GOOD, RuntimeError("bad page"), GOOD])
    assert _err("one-bad.pdf", PDF).code == "unreadable"                   # never read half-way and silently


def test_pdf_not_a_pdf_and_missing_library(monkeypatch):
    assert _err("x.pdf", b"hello").code == "mismatch"
    monkeypatch.setitem(sys.modules, "pypdf", None)                        # an import of it now fails
    e = _err("x.pdf", PDF)
    assert e.code == "pdf_unavailable" and e.en == "PDF reading is not available on this server."


def test_real_pdf_with_text_and_a_scan():
    pytest.importorskip("pypdf", reason="pypdf is not installed here (it is added to requirements.txt by this change)")
    out = X.extract("s.pdf", make_pdf([["Rated power 2.2 kW at 4800 rpm.", "Maximum speed 6000 rpm.", "DC supply 48 V nominal.",
                                        "Ambient temperature up to 40 C.", "Outer diameter 120 mm, length 80 mm.", "Air cooled."] * 2]))
    assert out.blocks[0].ref == "page 1" and "6000 rpm" in out.blocks[0].text
    assert _err("scan.pdf", make_pdf([None, None])).code == "scanned"


# ── XLS ──────────────────────────────────────────────────────────────────────

def test_xls_values_through_xlrd():
    pytest.importorskip("xlrd", reason="xlrd is not installed here (it is added to requirements.txt by this change)")
    out = X.extract("old.xls", make_xls([("Parameter", "Value", "Unit"), ("Rated power", 2.2, "kW"), ("Max speed", 6000, "rpm")]))
    refs = {b.ref: b.text for b in out.blocks}
    assert out.kind == "xls" and refs["Spec!A2:C2"] == "A2=Rated power; B2=2.2; C2=kW"
    assert refs["Spec!A3:C3"] == "A3=Max speed; B3=6000; C3=rpm"


def test_xls_without_the_library_says_resave(monkeypatch):
    monkeypatch.setitem(sys.modules, "xlrd", None)
    e = _err("old.xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 600)
    assert e.code == "xls_unavailable" and "re-save as XLSX" in e.en


def test_xls_name_with_other_content_and_macro_xls():
    assert _err("old.xls", b"hello world").code == "mismatch"
    pytest.importorskip("xlrd")
    base = make_xls([("a", 1)])
    macro = base[:-200] + "_VBA_PROJECT_CUR".encode("utf-16-le") + base[-200 + 32:]
    assert _err("m.xls", macro).code == "macro"


# ── extraction is pure reading ───────────────────────────────────────────────

def test_extraction_never_touches_the_network_or_starts_a_process(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("extraction must not use the network or a subprocess")

    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(subprocess, "run", boom)
    X.extract("a.txt", b"value 1\n")
    X.extract("a.docx", make_docx())
    X.extract("a.xlsx", make_xlsx())


def test_isolated_extraction_in_a_child_process_and_its_refusals():
    ok = X.extract_isolated("a.txt", b"Rated power 2.2 kW\n")
    assert ok.blocks[0].text == "Rated power 2.2 kW"
    with pytest.raises(X.ExtractError) as info:
        X.extract_isolated("old.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 600)
    assert info.value.code == "ole_doc"
    with pytest.raises(X.ExtractError) as info:
        X.extract_isolated("a.txt", b"x" * (X.MAX_BYTES + 1))
    assert info.value.code == "too_big"


def test_the_child_environment_carries_no_keys(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-should-not-travel")
    seen = {}
    real = subprocess.Popen

    def spy(cmd, **kw):
        seen.update(env=kw.get("env"), session=kw.get("start_new_session"), pre=kw.get("preexec_fn"))
        return real(cmd, **kw)

    monkeypatch.setattr(subprocess, "Popen", spy)
    X.extract_isolated("a.txt", b"x 1\n")
    assert "ANTHROPIC_API_KEY" not in seen["env"] and set(seen["env"]) <= set(X._CHILD_ENV)
    assert seen["session"] is True and seen["pre"] is None             # own process group; nothing runs between fork and exec


# ── active content is refused BEFORE storage (the accepted original is handed to people) ──────────────────────────────────

def _rewrite(data: bytes, edits: dict) -> bytes:
    """A copy of a zip container with some members replaced (name -> bytes) or added."""
    z = zipfile.ZipFile(io.BytesIO(data))
    members = {n: z.read(n) for n in z.namelist()}
    members.update(edits)
    return zip_with(members)


REL_NS = b'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'


def test_remote_template_and_external_links_in_a_docx_are_refused_but_a_hyperlink_passes():
    base = make_docx()
    remote = REL_NS + (b'<Relationship Id="rId9" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/attachedTemplate" '
                       b'Target="https://evil.example/t.dotm" TargetMode="External"/></Relationships>')
    assert _err("a.docx", _rewrite(base, {"word/_rels/settings.xml.rels": remote})).code == "active"
    image = REL_NS + (b'<Relationship Id="rId9" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
                      b'Target="http://evil.example/p.png" TargetMode="External"/></Relationships>')
    assert _err("a.docx", _rewrite(base, {"word/_rels/document.xml.rels": image})).code == "active"
    ole = REL_NS + (b'<Relationship Id="rId9" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/oleObject" '
                    b'Target="embeddings/o.bin"/></Relationships>')
    assert _err("a.docx", _rewrite(base, {"word/_rels/x.xml.rels": ole})).code == "active"
    pkg = REL_NS + (b'<Relationship Id="rId9" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/package" '
                    b'Target="embeddings/p.docx"/></Relationships>')
    assert _err("a.docx", _rewrite(base, {"word/_rels/x.xml.rels": pkg})).code == "active"
    link = REL_NS + (b'<Relationship Id="rId9" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
                     b'Target="https://example.test/datasheet" TargetMode="External"/></Relationships>')
    assert X.extract("a.docx", _rewrite(base, {"word/_rels/x.xml.rels": link})).kind == "docx"   # inert text, never followed
    sheet = _rewrite(make_xlsx(), {"xl/worksheets/_rels/sheet1.xml.rels": remote})
    assert _err("a.xlsx", sheet).code == "active"


def test_a_dtd_or_entity_in_any_xml_part_is_refused_for_docx_and_xlsx():
    bomb = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]><x>&b;</x>'
    assert _err("a.xlsx", _rewrite(make_xlsx(), {"xl/sharedStrings.xml": bomb})).code == "dtd"
    assert _err("a.docx", _rewrite(make_docx(), {"word/document.xml": bomb})).code == "dtd"
    assert _err("a.docx", _rewrite(make_docx(), {"customXml/item1.xml": b'<!doctype html><x/>'})).code == "dtd"
    utf16 = "<?xml version='1.0'?><!ENTITY a 'b'><x/>".encode("utf-16-le")
    assert _err("a.xlsx", _rewrite(make_xlsx(), {"xl/styles.xml": utf16})).code == "dtd"


def test_xlsx_beyond_the_columns_and_rows_we_read_is_refused_not_dropped():
    wide = {"S": [("a",) + (None,) * (X.MAX_COLS + 3) + ("far",)]}
    assert _err("a.xlsx", make_xlsx(wide)).code == "cells"
    rows = [("x",)] * 3 + [(None,)] * (X.MAX_SCAN_ROWS + 5) + [("late",)]
    assert _err("a.xlsx", make_xlsx({"S": rows})).code == "cells"
    assert X.extract("ok.xlsx", make_xlsx({"S": [("a",) + (None,) * (X.MAX_COLS - 2) + ("last",)]})).stats["cells"] == 2


def _pdf(extra=None, js=False, attach=False, open_js=False):
    pypdf = pytest.importorskip("pypdf", reason="pypdf is not installed here")
    w = pypdf.PdfWriter()
    w.add_blank_page(width=300, height=300)
    if js:
        w.add_js("app.alert('x')")
    if attach:
        w.add_attachment("a.txt", b"hello")
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


@pytest.mark.parametrize("kw", [{"js": True}, {"attach": True}])
def test_pdf_with_a_script_or_an_embedded_file_is_refused(kw):
    pytest.importorskip("pypdf", reason="pypdf is not installed here")
    assert _err("a.pdf", _pdf(**kw)).code == "active"


def test_pdf_active_names_are_found_even_when_spelled_with_hex_escapes():
    raw = b"%PDF-1.4\n1 0 obj<< /Type /Catalog /OpenAction << /S /#4Aava#53cript /JS (x) >> >>endobj\ntrailer<< /Root 1 0 R >>\n%%EOF"
    pytest.importorskip("pypdf", reason="pypdf is not installed here")
    assert _err("a.pdf", raw).code == "active"
    assert _err("a.pdf", b"%PDF-1.4\n1 0 obj<< /S /Launch /F (cmd.exe) >>endobj").code == "active"


def test_pdf_objects_inside_the_parsed_file_actions_stub(monkeypatch):
    """The parsed-object scan (compressed object streams hide names from the raw scan): catalog actions, name trees, annotations."""
    def with_reader(root, page_extra=None):
        _stub_pypdf(monkeypatch, [GOOD])
        mod = sys.modules["pypdf"]
        base = mod.PdfReader

        class R(base):
            def __init__(self, *a, **k):
                super().__init__(*a, **k)
                self.trailer = {"/Root": root}
                if page_extra:
                    self.pages[0].update(page_extra)
        mod.PdfReader = R

    with_reader({"/OpenAction": {"/S": "/JavaScript"}})
    assert _err("a.pdf", PDF).code == "active"
    with_reader({"/OpenAction": [0, "/Fit"]})                                    # an ordinary destination is fine
    assert X.extract("a.pdf", PDF).kind == "pdf"
    with_reader({"/Names": {"/EmbeddedFiles": {}}})
    assert _err("a.pdf", PDF).code == "active"
    with_reader({"/AcroForm": {"/XFA": []}})
    assert _err("a.pdf", PDF).code == "active"
    with_reader({}, {"/Annots": [{"/Subtype": "/FileAttachment"}]})
    assert _err("a.pdf", PDF).code == "active"
    with_reader({}, {"/Annots": [{"/Subtype": "/Link", "/A": {"/S": "/Launch"}}]})
    assert _err("a.pdf", PDF).code == "active"
    with_reader({}, {"/Annots": [{"/Subtype": "/Link", "/A": {"/S": "/URI"}}]})  # a plain link is inert
    assert X.extract("a.pdf", PDF).kind == "pdf"


def test_xls_macro_forms_beyond_the_vba_project_stream_are_refused():
    pytest.importorskip("xlrd", reason="xlrd is not installed here")
    base = make_xls([("a", 1)])
    assert X.extract("ok.xls", base).kind == "xls"
    for name in ("VBA", "Macros", "_VBA_PROJECT_CUR", "ObjectPool", "MBD0001", "\x01Ole10Native"):
        assert _err("m.xls", make_xls([("a", 1)], extra_dir=name)).code == "macro", name
    assert _err("m.xls", make_xls([("a", 1)], sheet_type=1)).code == "macro"          # an Excel 4.0 macro sheet


# ── names, limits of the worker ──────────────────────────────────────────────

@pytest.mark.parametrize("name", ["", "   ", "a" * 201 + ".txt", "a\x00b.txt", "a\nb.txt", "a\x7f.txt", "dir/a.txt", "dir\\a.txt", "../a.txt"])
def test_bad_file_names_are_refused_before_anything_else(name):
    assert _err(name, b"x 1\n").code == "badname"
    with pytest.raises(X.ExtractError):
        X.extract_isolated(name, b"x 1\n")


def test_a_limit_that_cannot_be_set_or_verified_fails_closed(monkeypatch):
    import resource
    def no(*a, **k):
        raise OSError("not permitted")
    monkeypatch.setattr(resource, "setrlimit", no)
    assert X._set_limits() is False
    monkeypatch.setattr(resource, "setrlimit", lambda *a, **k: None)         # accepted but not applied: the read-back differs
    monkeypatch.setattr(resource, "getrlimit", lambda which: (resource.RLIM_INFINITY, resource.RLIM_INFINITY))
    assert X._set_limits() is False
    assert X._main(["--name", "a.txt"]) == X._WORKER_BAD_LIMITS


def test_the_worker_is_killed_as_a_group_on_a_timeout_and_its_answer_is_checked(monkeypatch):
    monkeypatch.setattr(X, "CHILD_WALL_SECONDS", 0.001)
    with pytest.raises(X.ExtractError) as info:
        X.extract_isolated("a.txt", b"x 1\n" * 1000)
    assert info.value.code == "timeout"
    monkeypatch.setattr(X, "CHILD_WALL_SECONDS", 25)
    monkeypatch.setattr(X, "CHILD_OUT_BYTES", 10)                              # an answer over the cap is refused, never read in full
    with pytest.raises(X.ExtractError) as info:
        X.extract_isolated("a.txt", b"x 1\n")
    assert info.value.code == "unreadable"


@pytest.mark.parametrize("bad", [None, [], {}, {"kind": "exe", "blocks": []}, {"kind": "txt", "blocks": "x"},
                                  {"kind": "txt", "blocks": [{"ref": 1, "text": "a"}]}, {"kind": "txt", "blocks": [{"ref": "a"}]}])
def test_the_answer_of_the_worker_must_have_the_schema(bad):
    with pytest.raises(X.ExtractError):
        X.from_dict(bad)


# ── relationships are PARSED, not matched ────────────────────────────────────────────────────────────────────────────────

def _rels(inner: str, prefix: str = "", ns: str = "http://schemas.openxmlformats.org/package/2006/relationships", enc: str = "utf-8", bom: bool = False) -> bytes:
    tag = f"{prefix}:Relationship" if prefix else "Relationship"
    root = f"{prefix}:Relationships" if prefix else "Relationships"
    xmlns = f'xmlns:{prefix}="{ns}"' if prefix else f'xmlns="{ns}"'
    body = f'<?xml version="1.0" encoding="{enc}"?><{root} {xmlns}>' + inner.replace("<R ", f"<{tag} ") + f"</{root}>"
    raw = body.encode(enc)
    return (b"\xff\xfe" + raw if enc == "utf-16" and False else raw)


ATT = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/attachedTemplate"
IMG = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
LNK = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"


@pytest.mark.parametrize("name,raw", [
    ("prefixed element", _rels(f'<R Id="a" Type="{ATT}" Target="http://e.test/t.dotm" TargetMode="External"/>', prefix="pr")),
    ("character reference in the mode", _rels(f'<R Id="a" Type="{IMG}" Target="http://e.test/p.png" TargetMode="&#69;xternal"/>')),
    ("character reference in the type", _rels(f'<R Id="a" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/attached&#84;emplate" Target="x"/>')),
    ("hex character reference", _rels(f'<R Id="a" Type="{IMG}" Target="http://e.test/p.png" TargetMode="&#x45;xternal"/>')),
    ("mixed case", _rels(f'<R Id="a" Type="{IMG}" Target="http://e.test/p.png" TargetMode="eXTernal"/>')),
    ("single quotes and spaces", _rels(f"<R   Id='a'   Type='{IMG}'\n Target='http://e.test/p.png'\n TargetMode = ' External '  />")),
    ("utf-16 file", _rels(f'<R Id="a" Type="{IMG}" Target="http://e.test/p.png" TargetMode="External"/>', enc="utf-16")),
    ("other namespace prefix on the attribute", _rels(f'<R Id="a" Type="{IMG}" Target="http://e.test/p.png" xmlns:q="urn:q" q:TargetMode="External"/>')),
    ("ole object", _rels('<R Id="a" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/oleObject" Target="embeddings/o.bin"/>')),
    ("embedded package", _rels('<R Id="a" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/package" Target="embeddings/p.xlsx"/>')),
])
def test_relationship_bypasses_are_refused(name, raw):
    with pytest.raises(X.ExtractError) as info:
        X._check_relationships(raw)
    assert info.value.code == "active", name
    base = make_docx()
    assert _err("a.docx", _rewrite(base, {"word/_rels/x.xml.rels": raw})).code in ("active", "dtd"), name


def test_a_utf16_rels_without_a_bom_is_parsed_or_refused_never_stored():
    raw = f'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="a" Type="{ATT}" Target="x"/></Relationships>'.encode("utf-16-le")
    with pytest.raises(X.ExtractError):
        X._check_relationships(raw)


def test_ordinary_relationships_pass_and_a_hyperlink_is_inert():
    ok = _rels(f'<R Id="a" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
               f'<R Id="b" Type="{LNK}" Target="https://example.test/x" TargetMode="External"/>')
    X._check_relationships(ok)
    prefixed = _rels(f'<R Id="b" Type="{LNK}" Target="https://example.test/x" TargetMode="External"/>', prefix="pr")
    X._check_relationships(prefixed)
    assert X.extract("a.docx", _rewrite(make_docx(), {"word/_rels/x.xml.rels": ok})).kind == "docx"


def test_a_rels_that_does_not_parse_or_declares_entities_is_refused():
    with pytest.raises(X.ExtractError) as info:
        X._check_relationships(b"<Relationships><Relationship")
    assert info.value.code == "unreadable"
    with pytest.raises(X.ExtractError) as info:
        X._check_relationships(b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "b">]><Relationships/>')
    assert info.value.code == "dtd"


def test_macro_content_types_are_read_by_the_parser_too():
    ct = ('<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Override PartName="/word/document.xml" ContentType="application/vnd.ms-word.document.macroEnabled.main+xml"/></Types>')
    with pytest.raises(X.ExtractError) as info:
        X._check_relationships(ct.encode("utf-16"))
    assert info.value.code == "macro"


# ── the worker's surroundings ────────────────────────────────────────────────

def test_on_a_platform_without_process_groups_extraction_refuses_cleanly(monkeypatch):
    monkeypatch.delattr(os, "killpg", raising=False)
    with pytest.raises(X.ExtractError) as info:
        X.extract_isolated("a.txt", b"x 1\n")
    assert info.value.code == "no_reader" and "not available" in info.value.en
    monkeypatch.undo()
    monkeypatch.setattr(os, "name", "nt")
    with pytest.raises(X.ExtractError) as info:
        X.extract_isolated("a.txt", b"x 1\n")
    assert info.value.code == "no_reader"


def test_a_module_planted_in_the_working_directory_is_not_imported_by_the_worker(tmp_path, monkeypatch):
    (tmp_path / "json.py").write_text("raise SystemExit(99)\n")                    # would break `import json` if the cwd were on sys.path
    monkeypatch.chdir(tmp_path)
    assert X.extract_isolated("a.txt", b"x 1\n").kind == "txt"
    seen = {}
    real = subprocess.Popen

    def spy(cmd, **kw):
        seen.update(cmd=cmd, cwd=kw.get("cwd"))
        return real(cmd, **kw)

    monkeypatch.setattr(subprocess, "Popen", spy)
    X.extract_isolated("a.txt", b"x 1\n")
    assert seen["cwd"] and seen["cwd"] != str(tmp_path) and ("-P" in seen["cmd"] or sys.version_info < (3, 11))
