"""Machine translation EN <-> ZH for the production module: DeepSeek through OpenRouter.

``OPENROUTER_API_KEY`` is required (read inside ``_post`` only, never logged, never returned); ``PRODUCTION_TRANSLATE_MODEL`` defaults to
``deepseek/deepseek-chat``.  A failure or a missing key raises ``TranslateError``; the caller stores the text with status ``pending`` and
offers a retry - saving is never blocked.

NO TRAINING (owner decision 2026-10-31).  Every request carries ``PROVIDER_PREFS``: ``data_collection: deny`` (OpenRouter routes
only to hosts of the model that do not store or train on prompts) and a preferred host order; fallbacks stay allowed, so the
translation keeps working while any non-collecting host serves the model (the owner's OpenRouter credit exists for this translation).
:func:`usable_endpoints` answers which hosts of the public endpoint list remain (``scripts/openrouter_endpoints_check.py`` runs it
live).  The provider that actually answered is logged (name only) because the translation metadata has no field for it.  Tests pass ``http_post`` (``(payload, timeout) -> OpenRouter JSON``) or patch ``_post``.
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
#: OpenRouter provider routing sent with every request (see the module docstring).
PROVIDER_PREFS = {"data_collection": "deny", "order": ["DeepInfra"], "allow_fallbacks": True}
_NAMES = {"en": "English", "zh": "Simplified Chinese"}


def usable_endpoints(endpoints: list) -> list:
    """Provider names of the hosts that can serve the model under ``PROVIDER_PREFS``, from OpenRouter's public endpoint list
    (``GET /api/v1/models/<model>/endpoints`` -> ``data.endpoints``): up (``status`` 0) and not ignored.  The data-collection rule is
    applied by OpenRouter itself; this answers the question "does a host remain at all"."""
    ignored = set(PROVIDER_PREFS.get("ignore") or [])
    return sorted({str(e.get("provider_name")) for e in endpoints or []
                   if e.get("status", 0) == 0 and e.get("provider_name") and e.get("provider_name") not in ignored})

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
    payload = {"model": model(), "temperature": 0, "provider": {**PROVIDER_PREFS, "order": list(PROVIDER_PREFS["order"])},
               "messages": [{"role": "system", "content": SYSTEM_PROMPT.format(src=_NAMES[src_lang], tgt=_NAMES[tgt_lang])},
                            {"role": "user", "content": str(text)}]}
    try:
        data = (http_post or _post)(payload, TIMEOUT_S)
        out = str(data["choices"][0]["message"]["content"]).strip()
        served_by = str(data.get("provider") or "").strip()[:60] if isinstance(data, dict) else ""
    except TranslateError:
        raise
    except Exception as e:                                  # noqa: BLE001 - any provider failure degrades to "pending"
        log.warning("production translate failed (%s)", type(e).__name__)
        raise TranslateError("translation failed (%s)" % type(e).__name__) from None
    if not out:
        raise TranslateError("translation came back empty")
    log.info("production translate: %s -> %s served by provider %r", src_lang, tgt_lang, served_by or "unknown")
    return out
