"""Stamp the Fusion source on derived files (docs/FUSION_SYNC.md section 4.2).  Stdlib only (runs in the CAD image too).

STEP: one more string in the header's FILE_DESCRIPTION list.  GLB: ``asset.extras.fusion`` in the JSON chunk.  The BOM gets its
stamp from bom.write_csv / write_xlsx (``source``), the manifest from cad.build.
"""
from __future__ import annotations

import json
import re
import struct
import unicodedata
from typing import Any, Dict

_FD = re.compile(rb"FILE_DESCRIPTION\s*\(\s*\(")


def stamp_step(path: str, text: str) -> bool:
    """Add `text` as the first description string of a STEP file's header.  Returns False when the header has no
    FILE_DESCRIPTION (the file is left as it is)."""
    with open(path, "rb") as fh:
        data = fh.read()
    head_end = data.find(b"ENDSEC;")
    m = _FD.search(data, 0, head_end if head_end > 0 else len(data))
    if not m:
        return False
    ascii_ = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode("ascii")     # STEP header strings are ASCII
    ascii_ = re.sub(r"[^ -~]", " ", ascii_)[:200]
    lit = ("'%s'," % ascii_.replace("'", "''")).encode("ascii")
    with open(path, "wb") as fh:
        fh.write(data[:m.end()] + lit + data[m.end():])
    return True


def stamp_glb(path: str, fusion: Dict[str, Any]) -> bool:
    """Put `fusion` into asset.extras.fusion of a binary glTF.  Returns False for a file that is not GLB v2."""
    with open(path, "rb") as fh:
        data = fh.read()
    if len(data) < 20 or data[:4] != b"glTF":
        return False
    version, _total = struct.unpack_from("<II", data, 4)
    clen, ctype = struct.unpack_from("<I4s", data, 12)
    if version != 2 or ctype != b"JSON":
        return False
    doc = json.loads(data[20:20 + clen].decode("utf-8"))
    doc.setdefault("asset", {}).setdefault("extras", {})["fusion"] = fusion
    js = json.dumps(doc, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    js += b" " * ((4 - len(js) % 4) % 4)
    rest = data[20 + clen:]
    out = b"glTF" + struct.pack("<II", 2, 12 + 8 + len(js) + len(rest)) + struct.pack("<I4s", len(js), b"JSON") + js + rest
    with open(path, "wb") as fh:
        fh.write(out)
    return True


def read_glb_fusion(path: str):
    with open(path, "rb") as fh:
        data = fh.read()
    clen = struct.unpack_from("<I", data, 12)[0]
    return ((json.loads(data[20:20 + clen].decode("utf-8")).get("asset") or {}).get("extras") or {}).get("fusion")
