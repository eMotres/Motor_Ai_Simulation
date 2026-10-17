"""Machine translation EN <-> ZH for the production module: DeepSeek through OpenRouter.

``OPENROUTER_API_KEY`` is required (read inside ``_post`` only, never logged, never returned); ``PRODUCTION_TRANSLATE_MODEL`` defaults to
``deepseek/deepseek-chat``.  A failure or a missing key raises ``TranslateError``; the caller stores the text with status ``pending`` and
offers a retry - saving is never blocked.  Tests pass ``http_post`` (``(payload, timeout) -> OpenRouter JSON``) or patch ``_post``.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request
from typing import Callable, Optional

log = logging.getLogger(__name__)

URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "deepseek/deepseek-chat"
TIMEOUT_S = 60.0
_NAMES = {"en": "English", "zh": "Simplified Chinese"}

SYSTEM_PROMPT = (
    "You are a technical translator for electric-motor manufacturing (stators, rotors, magnets, windings, shafts, bearings, fits, "
    "assembly and test procedures). Translate the user's text from {src} to {tgt}. Keep numbers, units, tolerances (for example "
    "H7/k6, +0.02/0), part codes, material grades and file names exactly as written. Do not add, explain or omit anything; keep "
    "line breaks and list markers. Reply with the translation only.")


class TranslateError(RuntimeError):
    """The provider could not translate (no key, network, bad answer).  The message never contains the key."""


def model() -> str:
    return (os.environ.get("PRODUCTION_TRANSLATE_MODEL") or "").strip() or DEFAULT_MODEL


def _post(payload: dict, timeout: float) -> dict:
    """The one place that touches the key and the network."""
    key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
    if not key:
        raise TranslateError("translation is not configured (no provider key)")
    req = urllib.request.Request(
        URL, data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def translate_text(text: str, src_lang: str, tgt_lang: str,
                   http_post: Optional[Callable[[dict, float], dict]] = None) -> str:
    """``text`` translated from ``src_lang`` to ``tgt_lang`` ("en" | "zh"); raises ``TranslateError`` on any failure."""
    if src_lang not in _NAMES or tgt_lang not in _NAMES:
        raise TranslateError("unsupported language pair %s -> %s" % (src_lang, tgt_lang))
    if not str(text or "").strip():
        return ""
    payload = {"model": model(), "temperature": 0,
               "messages": [{"role": "system", "content": SYSTEM_PROMPT.format(src=_NAMES[src_lang], tgt=_NAMES[tgt_lang])},
                            {"role": "user", "content": str(text)}]}
    try:
        data = (http_post or _post)(payload, TIMEOUT_S)
        out = str(data["choices"][0]["message"]["content"]).strip()
    except TranslateError:
        raise
    except Exception as e:                                  # noqa: BLE001 - any provider failure degrades to "pending"
        log.warning("production translate failed (%s)", type(e).__name__)
        raise TranslateError("translation failed (%s)" % type(e).__name__) from None
    if not out:
        raise TranslateError("translation came back empty")
    return out
