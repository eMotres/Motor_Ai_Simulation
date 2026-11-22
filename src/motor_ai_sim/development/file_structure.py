"""Is an uploaded contract file what its name says (owner 2026-11-22; coordinator decision after the Astra reviews of #819)?

Simple and permissive for real files, because a person confirms every signature (``project_contract``): these checks only keep
out what is plainly not a document of the named type, and never try to parse a PDF.

* PDF   starts with ``%PDF-``, ends with ``%%EOF`` (within the last 2 KiB), and does not start with HTML / SVG / XML markup;
* PNG / JPEG   the magic bytes, then a bounded decode with Pillow (``verify`` and a full ``load`` under a pixel cap);
* DOCX  (staff agreement / annex files only, never a signed scan) a ZIP PREFLIGHT before any member is read: member count, each
        member's and the total expanded size, the compression ratio and method; then the package parts a Word document has, the
        WordprocessingML content type and ``word/document.xml`` that parses (no DTD, bounded read).
"""
from __future__ import annotations

import zipfile
from pathlib import Path
from xml.etree import ElementTree

_MARKUP = (b"<", b"\xef\xbb\xbf<")
_PNG_SIG = b"\x89PNG\r\n\x1a\n"
_JPEG_SIG = b"\xff\xd8\xff"
MAX_PIXELS = 80_000_000
ZIP_MAX_MEMBERS = 2000
ZIP_MAX_MEMBER = 50 * 1024 * 1024
ZIP_MAX_TOTAL = 200 * 1024 * 1024
ZIP_MAX_RATIO = 200
_XML_MAX = 8 * 1024 * 1024
_WORD_MAIN = b"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"

SCAN_EXT = (".pdf", ".png", ".jpg", ".jpeg")
DOC_EXT = (".pdf", ".docx", ".png", ".jpg", ".jpeg")


def valid_pdf(path: Path) -> bool:
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            head = fh.read(1024)
            fh.seek(max(0, size - 2048))
            tail = fh.read()
    except OSError:
        return False
    if head.lstrip().startswith(_MARKUP):
        return False
    return head.startswith(b"%PDF-") and b"%%EOF" in tail


def valid_image(path: Path, ext: str) -> bool:
    try:
        with path.open("rb") as fh:
            head = fh.read(8)
    except OSError:
        return False
    if ext == ".png" and not head.startswith(_PNG_SIG):
        return False
    if ext in (".jpg", ".jpeg") and not head.startswith(_JPEG_SIG):
        return False
    try:
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = MAX_PIXELS                     # Pillow refuses a decompression bomb above this
        with Image.open(path) as im:
            im.verify()
        with Image.open(path) as im:
            if im.width * im.height > MAX_PIXELS:
                return False
            im.load()
    except Exception:                                       # noqa: BLE001 - anything Pillow cannot read is not an image
        return False
    return True


def valid_docx(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as z:
            infos = z.infolist()
            if not infos or len(infos) > ZIP_MAX_MEMBERS:
                return False
            total = 0
            for i in infos:                                 # PREFLIGHT: nothing is decompressed before every member passes
                if i.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED) or i.flag_bits & 0x1:
                    return False
                if i.file_size > ZIP_MAX_MEMBER:
                    return False
                if i.file_size and i.file_size > max(1, i.compress_size) * ZIP_MAX_RATIO:
                    return False
                total += i.file_size
            if total > ZIP_MAX_TOTAL:
                return False
            names = {i.filename: i for i in infos}
            for member in ("[Content_Types].xml", "_rels/.rels", "word/document.xml"):
                if member not in names or names[member].file_size > _XML_MAX:
                    return False
            types = z.read("[Content_Types].xml")
            doc = z.read("word/document.xml")
    except (zipfile.BadZipFile, OSError, RuntimeError, ValueError, NotImplementedError):
        return False
    if _WORD_MAIN not in types:
        return False
    for xml in (types, doc):
        if b"<!DOCTYPE" in xml or b"<!ENTITY" in xml:
            return False
        try:
            ElementTree.fromstring(xml)
        except ElementTree.ParseError:
            return False
    return True


def content_matches(path: Path, name: str) -> bool:
    """Does the file at ``path`` look like the type its name's extension says (PDF, PNG, JPEG, DOCX)?"""
    ext = Path(name.lower()).suffix
    if ext == ".pdf":
        return valid_pdf(path)
    if ext in (".png", ".jpg", ".jpeg"):
        return valid_image(path, ext)
    if ext == ".docx":
        return valid_docx(path)
    return False
