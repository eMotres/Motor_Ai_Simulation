"""Add-in settings and the sync key.  The key is kept with Windows DPAPI (current user) in
%APPDATA%\\MOTRES\\fusion_sync\\key.dpapi, never in the document and never in plain text.  Stdlib + ctypes only."""
from __future__ import annotations

import ctypes
import json
import os
import sys
from ctypes import wintypes

from .client import DEFAULT_SERVER


def settings_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "MOTRES", "fusion_sync")
    os.makedirs(d, exist_ok=True)
    return d


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> _Blob:
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _dpapi(data: bytes, protect: bool) -> bytes:
    if sys.platform != "win32":
        raise RuntimeError("the sync key store needs Windows (DPAPI)")
    crypt = ctypes.windll.crypt32
    out = _Blob()
    src = _blob(data)
    fn = crypt.CryptProtectData if protect else crypt.CryptUnprotectData
    ok = fn(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out))
    if not ok:
        raise RuntimeError("DPAPI %s failed" % ("protect" if protect else "unprotect"))
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def save_key(key: str) -> None:
    with open(os.path.join(settings_dir(), "key.dpapi"), "wb") as fh:
        fh.write(_dpapi(key.strip().encode("utf-8"), True))


def load_key() -> str:
    p = os.path.join(settings_dir(), "key.dpapi")
    if not os.path.isfile(p):
        return ""
    with open(p, "rb") as fh:
        return _dpapi(fh.read(), False).decode("utf-8")


def load_settings() -> dict:
    p = os.path.join(settings_dir(), "settings.json")
    s = {"server": DEFAULT_SERVER, "mirror_dir": ""}
    if os.path.isfile(p):
        try:
            with open(p, encoding="utf-8") as fh:
                s.update(json.load(fh))
        except (OSError, ValueError):
            pass
    return s


def save_settings(s: dict) -> None:
    with open(os.path.join(settings_dir(), "settings.json"), "w", encoding="utf-8") as fh:
        json.dump({"server": s.get("server") or DEFAULT_SERVER, "mirror_dir": s.get("mirror_dir") or ""}, fh, indent=1)
