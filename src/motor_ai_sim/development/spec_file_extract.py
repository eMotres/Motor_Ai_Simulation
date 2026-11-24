"""Text of an uploaded specification file, for the specification assistant (owner 2026-11-24).

PURE READING.  Nothing in a file is executed, followed or fetched: no macro, no link, no external relationship, no formula
evaluation (a spreadsheet gives its cached values), no OCR and no image reading.  The result is a list of text blocks, each
with a ``ref`` that names where it came from (page, sheet + cells, paragraph / table row, lines), so every proposed value can
show its source.

Types: TXT (CSV and MD arrive through the same text path), DOCX, PDF, XLSX and XLS.

* ``.doc`` (and any old binary Office file renamed to something else) is refused by its OLE magic bytes: "please re-save as
  DOCX".  ``.docm`` / ``.xlsm`` (macro containers, also found by content) are refused.
* ``.xls`` is read with ``xlrd`` 2.x: a pure-Python reader that evaluates nothing (no macro engine, no external fetch, no
  ``open`` on a path: only the bytes we hand it), called with ``on_demand=True`` (one sheet in memory at a time) and
  ``formatting_info=False``.  The size limit is checked before it is called, the sheet / row / cell limits while reading, and the
  route runs the whole extraction in a child process with an address-space and CPU limit (``extract_isolated``).
* The OOXML containers (DOCX / XLSX) are checked as zip files first: member count, total uncompressed size, compression ratio,
  encryption flag, macro / embedded-object / external-link parts.
* A PDF is read with ``pypdf`` (pure Python; JavaScript and actions are never run); an encrypted one is refused, one without a
  usable text layer gets "this PDF has no text; please send the text version".

``pypdf`` and ``xlrd`` are imported only when used; without them the answer is a plain "not available on this server" message
(the tests and the application work before the image is rebuilt).
"""
from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import threading
import zipfile
from dataclasses import dataclass, field
from typing import Any

MAX_BYTES = 5 * 1024 * 1024
MAX_PDF_PAGES = 30
MAX_SHEETS = 10
MAX_CELLS_SHEET = 2000
MAX_CELLS_TOTAL = 5000
MAX_COLS = 60
MAX_EMPTY_TAIL = 200000               # empty rows a sheet may declare after the last read row
MAX_SCAN_ROWS = 20000                 # rows looked at per sheet (a sheet may declare a million empty ones)
MAX_TEXT_CHARS = 100_000              # extracted text of one file (the model gets less, see spec_file_assistant)
ZIP_MAX_MEMBERS = 200
ZIP_MAX_TOTAL = 50 * 1024 * 1024
ZIP_MAX_RATIO = 100
ZIP_RATIO_FLOOR = 64 * 1024           # a member below this size cannot be a bomb
TEXT_LINES_PER_BLOCK = 40
#: a PDF page with fewer text characters than this counts as empty (a scan)
PAGE_EMPTY_CHARS = 25

OLE_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")
EXTENSIONS = (".txt", ".csv", ".md", ".docx", ".pdf", ".xlsx", ".xls")
_MACRO_EXT = (".docm", ".xlsm", ".dotm", ".xltm", ".pptm")
_BAD_MEMBERS = ("vbaproject.bin", "vbadata.xml", "externallinks/", "/embeddings/", "oleobject", "activex/")


@dataclass
class Block:
    ref: str
    text: str


@dataclass
class Extracted:
    kind: str                                   # txt | docx | pdf | xlsx | xls
    blocks: list[Block] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    @property
    def chars(self) -> int:
        return sum(len(b.text) for b in self.blocks)


class ExtractError(Exception):
    """A file that is refused, with a message for the user in both languages."""

    def __init__(self, code: str, en: str, zh: str):
        super().__init__(en)
        self.code, self.en, self.zh = code, en, zh

    def message(self, lang: str) -> str:
        return self.zh if str(lang).lower().startswith("zh") else self.en

    def as_dict(self) -> dict:
        return {"code": self.code, "en": self.en, "zh-CN": self.zh}


_MSG: dict[str, tuple[str, str]] = {
    "unsupported": ("This file type is not read. Send a TXT, DOCX, PDF, XLSX or XLS file.",
                    "不支持此文件类型。请发送 TXT、DOCX、PDF、XLSX 或 XLS 文件。"),
    "ole_doc": ("This is an old Word file (.doc). Please re-save as DOCX and send it again.",
                "这是旧版 Word 文件（.doc）。请另存为 DOCX 后重新发送。"),
    "ole_other": ("This is an old binary Office file that is not read. Please re-save as DOCX or XLSX and send it again.",
                  "这是不被读取的旧版二进制 Office 文件。请另存为 DOCX 或 XLSX 后重新发送。"),
    "macro": ("Files that can contain macros (.docm, .xlsm) are not read. Please save a copy as DOCX or XLSX without macros.",
              "不读取可含宏的文件（.docm、.xlsm）。请另存为不含宏的 DOCX 或 XLSX。"),
    "mismatch": ("The content of the file does not match its type.", "文件内容与其类型不符。"),
    "too_big": ("The file is larger than 5 MB. Send only the pages that hold the specification.",
                "文件超过 5 MB。请只发送包含规格的页面。"),
    "empty": ("The file is empty.", "文件为空。"),
    "scanned": ("This PDF has no text; please send the text version.", "此 PDF 没有文字；请发送文字版本。"),
    "encrypted": ("The file is encrypted or password protected; it is not read.", "文件已加密或有密码保护，无法读取。"),
    "bomb": ("The file is refused: it unpacks to an unsafe size.", "文件被拒绝：解压后的大小不安全。"),
    "embedded": ("The file contains macros, embedded objects or external links; it is not read. "
                 "Please send a plain copy.", "文件含有宏、嵌入对象或外部链接，无法读取。请发送纯净副本。"),
    "pages": ("The PDF has more than 30 pages. Send only the pages that hold the specification.",
              "PDF 超过 30 页。请只发送包含规格的页面。"),
    "sheets": ("The workbook has more than 10 sheets. Send only the sheets that hold the specification.",
               "工作簿超过 10 个工作表。请只发送包含规格的工作表。"),
    "cells": ("The workbook has more than 5000 filled cells (2000 on one sheet). Send only the part that holds the specification.",
              "工作簿的非空单元格超过 5000 个（单个工作表 2000 个）。请只发送包含规格的部分。"),
    "text_big": ("The file holds too much text. Send only the pages that hold the specification.",
                 "文件文字过多。请只发送包含规格的页面。"),
    "no_text": ("The file has no readable text.", "文件中没有可读文字。"),
    "badname": ("The file name is not acceptable (too long, or it holds control characters or path separators).",
                "文件名不可接受（过长，或含控制字符或路径分隔符）。"),
    "active": ("The file contains active content (macros, embedded files or objects, scripts, or links to remote content); it is not read. "
               "Please send a plain copy.", "文件含有活动内容（宏、嵌入文件或对象、脚本或远程内容链接），无法读取。请发送纯净副本。"),
    "dtd": ("The file contains an XML document type declaration or entities; it is not read.", "文件含有 XML 文档类型声明或实体，无法读取。"),
    "busy": ("The server is busy reading other files. Please try again in a minute.", "服务器正忙于读取其他文件，请一分钟后再试。"),
    "no_reader": ("Reading files is not available on this server.", "此服务器暂不支持读取文件。"),
    "unreadable": ("The file could not be read. Please check it or send another copy.", "无法读取该文件。请检查或发送另一份。"),
    "timeout": ("The file took too long to read; it is refused.", "读取文件耗时过长，已拒绝。"),
    "pdf_unavailable": ("PDF reading is not available on this server.", "此服务器暂不支持读取 PDF。"),
    "xls_unavailable": ("Old Excel (.xls) reading is not available on this server. Please re-save as XLSX.",
                        "此服务器暂不支持读取旧版 Excel（.xls）。请另存为 XLSX。"),
}


def _err(code: str) -> ExtractError:
    en, zh = _MSG[code]
    return ExtractError(code, en, zh)


# ── entry point ──────────────────────────────────────────────────────────────

MAX_NAME = 200


def check_name(name: str) -> str:
    """The upload's file name, or ``ExtractError``: not empty, at most 200 characters, no NUL or other control character, no path
    separator.  Checked BEFORE extraction and storage (the name is stored and shown)."""
    n = str(name or "")
    if not n.strip() or len(n) > MAX_NAME or any(ord(c) < 32 or ord(c) == 127 for c in n) or "/" in n or "\\" in n:
        raise _err("badname")
    return n


def precheck(name: str, data: bytes) -> str:
    """The cheap refusals (size, type, macro container, old binary Office file); returns the lower-case extension.  Nothing is parsed
    here, so the route runs it BEFORE the per-user file limit is charged."""
    check_name(name)
    if not data:
        raise _err("empty")
    if len(data) > MAX_BYTES:
        raise _err("too_big")
    ext = os.path.splitext(str(name or ""))[1].lower()
    if ext in _MACRO_EXT:
        raise _err("macro")
    if ext == ".doc":
        raise _err("ole_doc")
    if ext not in EXTENSIONS:
        raise _err("unsupported")
    if data[:8] == OLE_MAGIC and ext != ".xls":    # also catches an old binary file renamed to .docx / .txt / ...
        raise _err("ole_other")
    return ext


def extract(name: str, data: bytes) -> Extracted:
    """The text blocks of one file, or ``ExtractError``.  In-process; the route calls :func:`extract_isolated`."""
    ext = precheck(name, data)
    ole = data[:8] == OLE_MAGIC
    if ext == ".xls":
        if not ole:
            raise _err("mismatch")
        out = _read_xls(data)
    elif ext in (".docx", ".xlsx"):
        if data[:4] != b"PK\x03\x04":
            raise _err("mismatch")
        out = _read_docx(data) if ext == ".docx" else _read_xlsx(data)
    elif ext == ".pdf":
        if b"%PDF-" not in data[:1024]:
            raise _err("mismatch")
        out = _read_pdf(data)
    else:
        if data[:4] in (b"PK\x03\x04", b"MZ\x90\x00") or data[:5] == b"%PDF-":
            raise _err("mismatch")
        out = _read_text(data)
    if not out.blocks or not any(b.text.strip() for b in out.blocks):
        raise _err("no_text")
    if out.chars > MAX_TEXT_CHARS:
        raise _err("text_big")
    out.stats.setdefault("chars", out.chars)
    return out


# ── zip containers ───────────────────────────────────────────────────────────

def check_zip(data: bytes) -> zipfile.ZipFile:
    """Open an OOXML container after the safety checks (bomb, encryption, macros, embedded objects, external links)."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        infos = zf.infolist()
    except (zipfile.BadZipFile, ValueError, OSError):
        raise _err("unreadable") from None
    if len(infos) > ZIP_MAX_MEMBERS:
        raise _err("bomb")
    total = 0
    for info in infos:
        if info.flag_bits & 0x1:
            raise _err("encrypted")
        total += info.file_size
        if total > ZIP_MAX_TOTAL:
            raise _err("bomb")
        if info.file_size > ZIP_RATIO_FLOOR and info.file_size > ZIP_MAX_RATIO * max(info.compress_size, 1):
            raise _err("bomb")
        low = info.filename.lower()
        if any(bad in low for bad in _BAD_MEMBERS):
            raise _err("embedded")
    if total > ZIP_RATIO_FLOOR and total > ZIP_MAX_RATIO * max(len(data), 1):
        raise _err("bomb")
    try:
        if "[Content_Types].xml" in zf.namelist() and any(m in zf.read("[Content_Types].xml").lower().replace(b"\x00", b"")
                                                          for m in (b"macroenabled", b"vbaproject")):
            raise _err("macro")
        for info in infos:                                  # every XML part: no DTD / entities; every relationship: nothing remote or active
            low = info.filename.lower()
            if not low.endswith((".xml", ".rels", ".vml")):
                continue
            raw = zf.read(info)
            body = raw.lower()
            if any(tag in body for tag in _DTD_MARKS):
                raise _err("dtd")
            if low.endswith(".rels") or info.filename == "[Content_Types].xml":
                _check_relationships(raw)
    except (KeyError, zipfile.BadZipFile, RuntimeError, ValueError):
        raise _err("unreadable") from None
    return zf


_DTD_MARKS = tuple(m for t in ("<!doctype", "<!entity") for m in (t.encode(), t.encode("utf-16-le"), t.encode("utf-16-be")))
_ACTIVE_TYPES = frozenset({"oleobject", "package", "activexcontrol", "vbaproject", "vbadata", "externallink", "attachedtemplate",
                           "oleobjectbin", "macrosheet", "dialogsheet"})


def _check_relationships(raw: bytes) -> None:
    """Parse one ``.rels`` (or ``[Content_Types].xml``) with a namespace-aware XML parser (expat; any DOCTYPE or entity declaration
    raises; the encoding comes from the BOM / declaration, so UTF-16 is read as UTF-16; prefixes, character references and any case
    are resolved by the parser, never by a regex).  A relationship that points outside the file (remote template, external link, linked
    image) or at an active / embedded part is refused; a plain external HYPERLINK is inert text and passes (it is never followed).  A
    content type that is macro-enabled is refused.  A part that does not parse is refused."""
    import xml.parsers.expat as expat

    def local(name: str) -> str:
        return name.rsplit(" ", 1)[-1].lower()

    def forbidden(*_a, **_k):
        raise _err("dtd")

    def start(name, attrs):
        tag = local(name)
        a = {local(k): " ".join(str(v).split()).lower() for k, v in attrs.items()}
        if tag == "relationship":
            kind = a.get("type", "")
            if kind.rsplit("/", 1)[-1] in _ACTIVE_TYPES:
                raise _err("active")
            if a.get("targetmode") == "external" and not kind.endswith("/hyperlink"):
                raise _err("active")
        elif tag in ("default", "override"):
            ct = a.get("contenttype", "")
            if "macroenabled" in ct or "vbaproject" in ct:
                raise _err("macro")

    parser = expat.ParserCreate(namespace_separator=" ")
    parser.StartDoctypeDeclHandler = forbidden
    parser.EntityDeclHandler = forbidden
    parser.NotationDeclHandler = forbidden
    parser.ExternalEntityRefHandler = lambda *a: forbidden()
    parser.StartElementHandler = start
    try:
        parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
        parser.Parse(raw, True)
    except expat.ExpatError:
        raise _err("unreadable") from None


# ── TXT / CSV / MD ───────────────────────────────────────────────────────────

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _decode(data: bytes) -> str:
    sample = data[:65536]
    if sample.count(b"\x00") > len(sample) // 100:
        raise _err("mismatch")                       # binary, not text
    for enc in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise _err("unreadable")


def _read_text(data: bytes) -> Extracted:
    text = _CTRL.sub("", _decode(data).replace("\r\n", "\n").replace("\r", "\n"))
    if len(text) > MAX_TEXT_CHARS:
        raise _err("text_big")
    lines = text.split("\n")
    blocks: list[Block] = []
    for i in range(0, len(lines), TEXT_LINES_PER_BLOCK):
        chunk = lines[i:i + TEXT_LINES_PER_BLOCK]
        body = "\n".join(chunk).strip()
        if body:
            blocks.append(Block(f"lines {i + 1}-{i + len(chunk)}", body))
    return Extracted("txt", blocks, stats={"lines": len(lines)})


# ── DOCX ─────────────────────────────────────────────────────────────────────

def _read_docx(data: bytes) -> Extracted:
    check_zip(data).close()
    try:
        import docx                                    # python-docx
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError:                                # pragma: no cover - python-docx is in the image
        raise _err("unreadable") from None
    try:
        document = docx.Document(io.BytesIO(data))
    except Exception:                                  # noqa: BLE001 - any parser failure is "unreadable"
        raise _err("unreadable") from None
    blocks: list[Block] = []
    para = table = 0
    total = 0
    for child in document.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            para += 1
            text = Paragraph(child, document).text.strip()
            if text:
                blocks.append(Block(f"paragraph {para}", text))
                total += len(text)
        elif tag == "tbl":
            table += 1
            for r, row in enumerate(Table(child, document).rows, start=1):
                cells: list[str] = []
                seen: set[int] = set()
                for cell in row.cells:                 # merged cells repeat: read each once
                    if id(cell._tc) in seen:
                        continue
                    seen.add(id(cell._tc))
                    cells.append(" ".join(cell.text.split()))
                line = " | ".join(cells).strip(" |")
                if line:
                    blocks.append(Block(f"table {table} row {r}", line))
                    total += len(line)
        if total > MAX_TEXT_CHARS:
            raise _err("text_big")
    for s, section in enumerate(document.sections, start=1):          # headers and footers read as text
        for kind, part in (("header", section.header), ("footer", section.footer)):
            text = " ".join(p.text.strip() for p in part.paragraphs if p.text.strip())
            if text:
                blocks.append(Block(f"section {s} {kind}", text))
    return Extracted("docx", blocks, stats={"paragraphs": para, "tables": table})


# ── XLSX ─────────────────────────────────────────────────────────────────────

def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return ""
        return str(int(value)) if value == int(value) and abs(value) < 1e15 else repr(round(value, 9))
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return " ".join(str(value).split())


def _col_letters(n: int) -> str:
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _read_xlsx(data: bytes) -> Extracted:
    check_zip(data).close()
    try:
        import openpyxl
    except ImportError:                                # pragma: no cover
        raise _err("unreadable") from None
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True, keep_links=False)
    except Exception:                                  # noqa: BLE001
        raise _err("unreadable") from None
    try:
        sheets = list(wb.worksheets)
        if len(sheets) > MAX_SHEETS:
            raise _err("sheets")
        blocks: list[Block] = []
        total = 0
        hidden: list[str] = []
        for ws in sheets:
            state = getattr(ws, "sheet_state", "visible")
            label = ws.title if state == "visible" else f"{ws.title} (hidden)"
            if state != "visible":
                hidden.append(ws.title)
            in_sheet = 0
            for r, row in enumerate(ws.iter_rows(values_only=True), start=1):
                if r > MAX_SCAN_ROWS + MAX_EMPTY_TAIL:
                    raise _err("cells")
                cells = [(c, _cell_text(v)) for c, v in enumerate(row, start=1) if _cell_text(v)]
                if cells and (r > MAX_SCAN_ROWS or cells[-1][0] > MAX_COLS):
                    raise _err("cells")                    # data beyond the rows / columns we read is refused, never silently dropped
                if not cells:
                    continue
                in_sheet += len(cells)
                total += len(cells)
                if in_sheet > MAX_CELLS_SHEET or total > MAX_CELLS_TOTAL:
                    raise _err("cells")
                first, last = cells[0][0], cells[-1][0]
                blocks.append(Block(f"{label}!{_col_letters(first)}{r}:{_col_letters(last)}{r}",
                                    "; ".join(f"{_col_letters(c)}{r}={t}" for c, t in cells)))
        return Extracted("xlsx", blocks, warnings=[f"hidden sheet: {n}" for n in hidden], stats={"sheets": len(sheets), "cells": total})
    finally:
        try:
            wb.close()
        except Exception:                              # noqa: BLE001
            pass


# ── XLS (xlrd) ───────────────────────────────────────────────────────────────

_OLE_ACTIVE = frozenset({"_vba_project_cur", "_vba_project", "vba", "macros", "projectwm", "project", "objectpool", "\x01ole10native",
                         "ole10native"})


def _ole_names(data: bytes) -> list[str]:
    """Names of the 128-byte directory entries of an OLE2 file (read from the bytes; a sector is 512 bytes, so entries are aligned)."""
    names = []
    for off in range(512, len(data) - 127, 128):
        n = int.from_bytes(data[off + 64:off + 66], "little")
        typ = data[off + 66]
        if 2 <= n <= 64 and n % 2 == 0 and typ in (1, 2, 5):
            try:
                names.append(data[off:off + n - 2].decode("utf-16-le"))
            except UnicodeDecodeError:
                continue
    return names


def _check_ole(data: bytes) -> None:
    """An old Excel file with a macro project, an embedded object / OLE package or an object pool is refused (reading it would not run
    anything, but the original is stored and handed to people)."""
    for name in _ole_names(data):
        low = name.lower()
        if low in _OLE_ACTIVE or low.startswith(("mbd", "_vba", "\x01ole")):
            raise _err("macro")
    if "_VBA_PROJECT".encode("utf-16-le") in data:
        raise _err("macro")


def _read_xls(data: bytes) -> Extracted:
    try:
        import xlrd
    except ImportError:
        raise _err("xls_unavailable") from None
    _check_ole(data)
    try:
        book = xlrd.open_workbook(file_contents=data, on_demand=True, formatting_info=False, ragged_rows=True)
    except xlrd.XLRDError as exc:
        raise _err("encrypted" if "encrypted" in str(exc).lower() else "unreadable") from None
    except Exception:                                  # noqa: BLE001
        raise _err("unreadable") from None
    try:
        if getattr(book, "_all_sheets_count", book.nsheets) != book.nsheets:
            raise _err("macro")                        # a macro sheet (Excel 4.0), a Visual Basic module or a chart sheet
        if book.nsheets > MAX_SHEETS:
            raise _err("sheets")
        blocks: list[Block] = []
        total = 0
        hidden: list[str] = []
        for i in range(book.nsheets):
            sh = book.sheet_by_index(i)
            vis = getattr(sh, "visibility", 0)
            label = sh.name if vis == 0 else f"{sh.name} (hidden)"
            if vis != 0:
                hidden.append(sh.name)
            in_sheet = 0
            if sh.nrows > MAX_SCAN_ROWS + MAX_EMPTY_TAIL:
                raise _err("cells")
            for r in range(sh.nrows):
                cells = []
                for c in range(sh.row_len(r)):
                    ctype = sh.cell_type(r, c)
                    value = sh.cell_value(r, c)
                    if ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK, xlrd.XL_CELL_ERROR):
                        continue
                    if ctype == xlrd.XL_CELL_DATE:
                        try:
                            value = xlrd.xldate_as_datetime(value, book.datemode)
                        except Exception:              # noqa: BLE001
                            pass
                    elif ctype == xlrd.XL_CELL_BOOLEAN:
                        value = bool(value)
                    text = _cell_text(value)
                    if text:
                        cells.append((c + 1, text))
                if not cells:
                    continue
                if r >= MAX_SCAN_ROWS or cells[-1][0] > MAX_COLS:
                    raise _err("cells")
                in_sheet += len(cells)
                total += len(cells)
                if in_sheet > MAX_CELLS_SHEET or total > MAX_CELLS_TOTAL:
                    raise _err("cells")
                blocks.append(Block(f"{label}!{_col_letters(cells[0][0])}{r + 1}:{_col_letters(cells[-1][0])}{r + 1}",
                                    "; ".join(f"{_col_letters(c)}{r + 1}={t}" for c, t in cells)))
            book.unload_sheet(i)
        return Extracted("xls", blocks, warnings=[f"hidden sheet: {n}" for n in hidden], stats={"sheets": book.nsheets, "cells": total})
    finally:
        try:
            book.release_resources()
        except Exception:                              # noqa: BLE001
            pass


# ── PDF ──────────────────────────────────────────────────────────────────────

_PDF_RAW = re.compile(rb"/(JavaScript|JS|Launch|EmbeddedFiles?|RichMedia|XFA|GoToR|GoToE|ImportData|SubmitForm|Rendition)(?![A-Za-z0-9])")
_PDF_HEX = re.compile(rb"#([0-9A-Fa-f]{2})")
_PDF_ACTIONS = frozenset({"/JavaScript", "/Launch", "/GoToR", "/GoToE", "/ImportData", "/SubmitForm", "/Rendition", "/Movie", "/Sound"})
_PDF_ANNOTS = frozenset({"/FileAttachment", "/RichMedia", "/Movie", "/Sound", "/Screen", "/3D"})


def _pdf_raw_scan(data: bytes) -> None:
    """Names written in the file text (a name may hide letters as #XX) that mean a script, a launch, an embedded file or a remote action."""
    plain = _PDF_HEX.sub(lambda m: bytes([int(m.group(1), 16)]), data)
    if _PDF_RAW.search(plain):
        raise _err("active")


def _pdf_object_scan(reader, pages, n: int) -> None:
    """The same, on the parsed objects (compressed object streams hide names from the raw scan): the catalog's actions, name trees,
    XFA, and every page's annotations.  A plain link (URI) or an ordinary go-to destination is inert and passes."""
    def obj(x):
        return x.get_object() if hasattr(x, "get_object") else x

    def action_bad(a) -> bool:
        a = obj(a)
        if isinstance(a, dict):
            return str(obj(a.get("/S"))) in _PDF_ACTIONS or ("/Next" in a and action_bad(a["/Next"]))
        return False

    root = obj(reader.trailer["/Root"])
    names = obj(root.get("/Names")) or {}
    acro = obj(root.get("/AcroForm")) or {}
    if "/AA" in root or "/JavaScript" in names or "/EmbeddedFiles" in names or "/XFA" in acro or action_bad(root.get("/OpenAction")):
        raise _err("active")
    for i in range(n):
        page = obj(pages[i])
        if "/AA" in page:
            raise _err("active")
        for annot in obj(page.get("/Annots")) or []:
            a = obj(annot)
            if str(obj(a.get("/Subtype"))) in _PDF_ANNOTS or "/AA" in a or action_bad(a.get("/A")):
                raise _err("active")


def _read_pdf(data: bytes) -> Extracted:
    try:
        import pypdf
    except ImportError:
        raise _err("pdf_unavailable") from None
    _pdf_raw_scan(data)
    try:
        reader = pypdf.PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            raise _err("encrypted")
        pages = reader.pages
        n = len(pages)
        if n > MAX_PDF_PAGES:
            raise _err("pages")
        _pdf_object_scan(reader, pages, n)
    except ExtractError:
        raise
    except Exception:                                  # noqa: BLE001
        raise _err("unreadable") from None
    blocks: list[Block] = []
    empty = 0
    total = 0
    bad = 0
    for i in range(n):
        try:
            text = pages[i].extract_text() or ""
        except Exception:                              # noqa: BLE001 - a page that cannot be read: the file is not read half-way
            raise _err("unreadable") from None
        text = _CTRL.sub("", text).strip()
        if len(re.sub(r"\s+", "", text)) < PAGE_EMPTY_CHARS:
            empty += 1
        total += len(text)
        bad += sum(1 for ch in text if ch == "\ufffd" or "\ue000" <= ch <= "\uf8ff")
        if text:
            blocks.append(Block(f"page {i + 1}", text))
        if total > MAX_TEXT_CHARS:
            raise _err("text_big")
    if n == 0 or empty * 2 >= n or total < 200 or (total and bad / total > 0.30):
        raise _err("scanned")
    return Extracted("pdf", blocks, stats={"pages": n, "empty_pages": empty})


# ── isolated run (the route's path) ──────────────────────────────────────────
#
# A small worker ENTRY POINT (``python -m motor_ai_sim.development.spec_file_extract``) that is started fresh (no ``preexec_fn``, so
# nothing runs between fork and exec in a threaded server).  The worker sets its OWN limits and READS THEM BACK before it touches the
# file; if a limit cannot be set or verified it refuses and exits.  It is the leader of its own session / process group, which the
# parent kills as a group on a timeout.  Its stdout is a size-limited temporary file, its stderr is discarded, and the parent checks
# the return code and the shape of the answer.

_CHILD_ENV = ("PATH", "PYTHONPATH", "LANG", "LC_ALL", "VIRTUAL_ENV", "SYSTEMROOT")
CHILD_MEMORY = 1536 * 1024 * 1024
CHILD_CPU_SECONDS = 20
CHILD_WALL_SECONDS = 25
CHILD_OUT_BYTES = 4 * 1024 * 1024
MAX_WORKERS = 2
BUSY_WAIT_SECONDS = 5
_SLOTS = threading.BoundedSemaphore(MAX_WORKERS)
_WORKER_BAD_LIMITS = 3


def to_dict(out: Extracted) -> dict:
    return {"kind": out.kind, "blocks": [{"ref": b.ref, "text": b.text} for b in out.blocks], "warnings": out.warnings, "stats": out.stats}


def from_dict(d: dict) -> Extracted:
    """The worker's answer as an ``Extracted``; any other shape is ``ExtractError`` (the parent trusts nothing it reads back)."""
    try:
        if not isinstance(d, dict) or not isinstance(d["kind"], str) or d["kind"] not in ("txt", "docx", "pdf", "xlsx", "xls"):
            raise TypeError
        blocks = [Block(b["ref"], b["text"]) for b in d["blocks"]]
        if not all(isinstance(b.ref, str) and isinstance(b.text, str) for b in blocks) or not isinstance(d.get("warnings", []), list) \
                or not isinstance(d.get("stats", {}), dict) or len(blocks) > 200_000:
            raise TypeError
        return Extracted(d["kind"], blocks, [str(w)[:200] for w in d.get("warnings") or []], dict(d.get("stats") or {}))
    except (KeyError, TypeError, AttributeError):
        raise _err("unreadable") from None


def _set_limits() -> bool:
    """Set and verify the worker's resource limits; ``False`` (refuse) when this platform cannot or any limit is not what we asked."""
    try:
        import resource
        wanted = {resource.RLIMIT_AS: CHILD_MEMORY, resource.RLIMIT_CPU: CHILD_CPU_SECONDS, resource.RLIMIT_FSIZE: CHILD_OUT_BYTES,
                  resource.RLIMIT_CORE: 0}
        for which, value in wanted.items():
            resource.setrlimit(which, (value, value))
        if any(resource.getrlimit(which) != (value, value) for which, value in wanted.items()):
            return False
        os.nice(10)
        return True
    except Exception:                                  # noqa: BLE001 - no resource module (Windows), no permission, anything: fail closed
        return False


def extract_isolated(name: str, data: bytes) -> Extracted:
    """:func:`extract` in a fresh worker process: limits set and verified by the worker itself, no API keys in its environment, its own
    process group killed as a group on a timeout, stdout size-capped, return code and answer schema checked.  A crash, a kill, a bad
    answer or a timeout is a refusal, never a failure of the server."""
    check_name(name)
    if not data:
        raise _err("empty")
    if len(data) > MAX_BYTES:
        raise _err("too_big")
    if os.name != "posix" or not hasattr(os, "killpg"):          # no resource limits, no process groups: no reading (fail closed)
        raise _err("no_reader")
    if not _SLOTS.acquire(timeout=BUSY_WAIT_SECONDS):             # at most two workers at once (1.5 GB each)
        raise _err("busy")
    try:
        return _run_worker(name, data)
    finally:
        _SLOTS.release()


def _run_worker(name: str, data: bytes) -> Extracted:
    import signal
    import tempfile
    env = {k: os.environ[k] for k in _CHILD_ENV if k in os.environ}
    flags = ["-P"] if sys.version_info >= (3, 11) else []          # never put the working directory on sys.path
    with tempfile.TemporaryFile() as out, tempfile.TemporaryDirectory() as empty:
        try:
            proc = subprocess.Popen([sys.executable, *flags, "-m", "motor_ai_sim.development.spec_file_extract", "--name", name],
                                    stdin=subprocess.PIPE, stdout=out, stderr=subprocess.DEVNULL, env=env, start_new_session=True,
                                    close_fds=True, cwd=empty)
        except OSError:
            raise _err("unreadable") from None

        def kill_group() -> None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass

        try:
            proc.communicate(input=data, timeout=CHILD_WALL_SECONDS)
        except subprocess.TimeoutExpired:
            kill_group()
            proc.wait()
            raise _err("timeout") from None
        except BaseException:
            kill_group()
            raise
        finally:
            kill_group()                               # nothing the worker started outlives it
        if proc.returncode != 0:
            raise _err("unreadable")
        out.seek(0)
        raw = out.read(CHILD_OUT_BYTES + 1)
    if len(raw) > CHILD_OUT_BYTES:
        raise _err("unreadable")
    try:
        reply = json.loads(raw.decode("utf-8"))
    except ValueError:
        raise _err("unreadable") from None
    if not isinstance(reply, dict) or ("ok" in reply) == ("error" in reply):
        raise _err("unreadable")
    if "error" in reply:
        e = reply["error"]
        if not isinstance(e, dict) or e.get("code") not in {k for k in _MSG} | {"unreadable"}:
            raise _err("unreadable")
        raise _err(e["code"])                          # the text is ours, never the worker's
    return from_dict(reply["ok"])


def _main(argv: list[str]) -> int:                    # pragma: no cover - the worker process
    if not _set_limits():
        return _WORKER_BAD_LIMITS
    name = argv[argv.index("--name") + 1] if "--name" in argv else ""
    data = sys.stdin.buffer.read(MAX_BYTES + 1)
    try:
        payload: dict = {"ok": to_dict(extract(name, data))}
    except ExtractError as exc:
        payload = {"error": {"code": exc.code}}
    except MemoryError:
        payload = {"error": {"code": "unreadable"}}
    sys.stdout.write(json.dumps(payload, ensure_ascii=True))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":                             # pragma: no cover
    raise SystemExit(_main(sys.argv[1:]))
