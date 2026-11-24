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
import zipfile
from dataclasses import dataclass, field
from typing import Any

MAX_BYTES = 5 * 1024 * 1024
MAX_PDF_PAGES = 30
MAX_SHEETS = 10
MAX_CELLS_SHEET = 2000
MAX_CELLS_TOTAL = 5000
MAX_COLS = 60
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

def precheck(name: str, data: bytes) -> str:
    """The cheap refusals (size, type, macro container, old binary Office file); returns the lower-case extension.  Nothing is parsed
    here, so the route runs it BEFORE the per-user file limit is charged."""
    if not data:
        raise _err("empty")
    if len(data) > MAX_BYTES:
        raise _err("too_big")
    ext = os.path.splitext(str(name or "").replace("\\", "/").rsplit("/", 1)[-1])[1].lower()
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
        if "[Content_Types].xml" in zf.namelist() and b"macroenabled" in zf.read("[Content_Types].xml").lower():
            raise _err("macro")
    except (KeyError, zipfile.BadZipFile, RuntimeError):
        raise _err("unreadable") from None
    return zf


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
            for r, row in enumerate(ws.iter_rows(max_col=MAX_COLS, values_only=True), start=1):
                if r > MAX_SCAN_ROWS:
                    break
                cells = [(c, _cell_text(v)) for c, v in enumerate(row, start=1) if _cell_text(v)]
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

def _read_xls(data: bytes) -> Extracted:
    try:
        import xlrd
    except ImportError:
        raise _err("xls_unavailable") from None
    if "_VBA_PROJECT".encode("utf-16-le") in data or b"_VBA_PROJECT" in data:
        raise _err("macro")
    try:
        book = xlrd.open_workbook(file_contents=data, on_demand=True, formatting_info=False, ragged_rows=True)
    except xlrd.XLRDError as exc:
        raise _err("encrypted" if "encrypted" in str(exc).lower() else "unreadable") from None
    except Exception:                                  # noqa: BLE001
        raise _err("unreadable") from None
    try:
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
            for r in range(min(sh.nrows, MAX_SCAN_ROWS)):
                cells = []
                for c in range(min(sh.row_len(r), MAX_COLS)):
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

def _read_pdf(data: bytes) -> Extracted:
    try:
        import pypdf
    except ImportError:
        raise _err("pdf_unavailable") from None
    try:
        reader = pypdf.PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            raise _err("encrypted")
        pages = reader.pages
        n = len(pages)
    except ExtractError:
        raise
    except Exception:                                  # noqa: BLE001
        raise _err("unreadable") from None
    if n > MAX_PDF_PAGES:
        raise _err("pages")
    blocks: list[Block] = []
    empty = 0
    total = 0
    bad = 0
    for i in range(n):
        try:
            text = pages[i].extract_text() or ""
        except Exception:                              # noqa: BLE001 - one bad page is an empty page
            text = ""
        text = _CTRL.sub("", text).strip()
        if len(re.sub(r"\s+", "", text)) < PAGE_EMPTY_CHARS:
            empty += 1
        total += len(text)
        bad += sum(1 for ch in text if ch == "�" or "" <= ch <= "")
        if text:
            blocks.append(Block(f"page {i + 1}", text))
        if total > MAX_TEXT_CHARS:
            raise _err("text_big")
    if n == 0 or empty * 2 >= n or total < 200 or (total and bad / total > 0.30):
        raise _err("scanned")
    return Extracted("pdf", blocks, stats={"pages": n, "empty_pages": empty})


# ── isolated run (the route's path) ──────────────────────────────────────────

_CHILD_ENV = ("PATH", "PYTHONPATH", "LANG", "LC_ALL", "VIRTUAL_ENV", "SYSTEMROOT")
CHILD_MEMORY = 1536 * 1024 * 1024
CHILD_CPU_SECONDS = 20
CHILD_WALL_SECONDS = 25


def _limits() -> None:                                 # pragma: no cover - runs in the child before exec
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (CHILD_MEMORY, CHILD_MEMORY))
        resource.setrlimit(resource.RLIMIT_CPU, (CHILD_CPU_SECONDS, CHILD_CPU_SECONDS))
        resource.setrlimit(resource.RLIMIT_FSIZE, (1 << 20, 1 << 20))
        os.nice(10)
    except Exception:                                  # noqa: BLE001
        pass


def to_dict(out: Extracted) -> dict:
    return {"kind": out.kind, "blocks": [{"ref": b.ref, "text": b.text} for b in out.blocks], "warnings": out.warnings, "stats": out.stats}


def from_dict(d: dict) -> Extracted:
    return Extracted(str(d["kind"]), [Block(str(b["ref"]), str(b["text"])) for b in d["blocks"]], list(d.get("warnings") or []),
                     dict(d.get("stats") or {}))


def extract_isolated(name: str, data: bytes) -> Extracted:
    """:func:`extract` in a child Python process with no API keys in its environment, an address-space limit, a CPU limit and a
    wall-clock limit.  A crash, a kill or a timeout is a refusal, never a failure of the server."""
    if not data:
        raise _err("empty")
    if len(data) > MAX_BYTES:
        raise _err("too_big")
    env = {k: os.environ[k] for k in _CHILD_ENV if k in os.environ}
    try:
        proc = subprocess.run([sys.executable, "-m", "motor_ai_sim.development.spec_file_extract", "--name", str(name)],
                              input=data, capture_output=True, timeout=CHILD_WALL_SECONDS, env=env,
                              preexec_fn=_limits if os.name == "posix" else None, check=False)
    except subprocess.TimeoutExpired:
        raise _err("timeout") from None
    except OSError:
        raise _err("unreadable") from None
    try:
        reply = json.loads(proc.stdout.decode("utf-8"))
    except ValueError:
        raise _err("unreadable") from None                # killed by a limit, or crashed
    if "error" in reply:
        e = reply["error"]
        raise ExtractError(str(e.get("code")), str(e.get("en")), str(e.get("zh-CN")))
    return from_dict(reply["ok"])


def _main(argv: list[str]) -> int:                    # pragma: no cover - the child process
    name = argv[argv.index("--name") + 1] if "--name" in argv else ""
    data = sys.stdin.buffer.read(MAX_BYTES + 1)
    try:
        payload: dict = {"ok": to_dict(extract(name, data))}
    except ExtractError as exc:
        payload = {"error": exc.as_dict()}
    except MemoryError:
        payload = {"error": _err("unreadable").as_dict()}
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":                             # pragma: no cover
    raise SystemExit(_main(sys.argv[1:]))
