"""Machine translation EN <-> ZH for the production module: Anthropic Claude by default, OpenRouter only as an explicit opt-in.

Owner 2026-10-31: «переводи перевод на Claude».  ``PRODUCTION_TRANSLATE_PROVIDER`` = ``anthropic`` (default) or ``openrouter``;
``PRODUCTION_TRANSLATE_MODEL`` defaults to ``claude-sonnet-5-5`` (Anthropic) or ``deepseek/deepseek-chat`` (OpenRouter).  A model id
that does not belong to the chosen provider (for example a stale ``deepseek/...`` with the Anthropic default) is replaced by that
provider's default and logged once.  The key (``ANTHROPIC_API_KEY`` / ``OPENROUTER_API_KEY``) is read inside ``_post`` only, never
logged, never returned.  A failure or a missing key raises ``TranslateError``; the caller stores the text with status ``pending`` and
offers a retry - saving is never blocked.

NO TRAINING.  Anthropic's commercial API does not train on inputs or outputs.  The OpenRouter opt-in carries ``PROVIDER_PREFS``:
``data_collection: deny`` (only hosts that do not store or train on prompts), DeepInfra preferred, fallbacks allowed;
:func:`usable_endpoints` / ``scripts/openrouter_endpoints_check.py`` show which hosts remain.  The provider that answered is logged
(name only).  Tests pass ``http_post`` (``(payload, timeout) -> provider JSON``; both answer shapes are read) or patch ``_post``.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request
from typing import Callable, Optional

log = logging.getLogger(__name__)

PROVIDERS = ("anthropic", "openrouter")
DEFAULT_PROVIDER = "anthropic"
URLS = {"anthropic": "https://api.anthropic.com/v1/messages", "openrouter": "https://openrouter.ai/api/v1/chat/completions"}
URL = URLS["openrouter"]
DEFAULT_MODELS = {"anthropic": "claude-sonnet-5-5", "openrouter": "deepseek/deepseek-chat"}
DEFAULT_MODEL = DEFAULT_MODELS[DEFAULT_PROVIDER]
KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY", "openrouter": "OPENROUTER_API_KEY"}
ANTHROPIC_VERSION = "2023-06-01"
MAX_TOKENS = 8192
TIMEOUT_S = 60.0
#: OpenRouter provider routing sent with every OpenRouter request (see the module docstring).
PROVIDER_PREFS = {"data_collection": "deny", "order": ["DeepInfra"], "allow_fallbacks": True}
_NAMES = {"en": "English", "zh": "Simplified Chinese"}
_warned_model: set = set()


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


def provider() -> str:
    p = (os.environ.get("PRODUCTION_TRANSLATE_PROVIDER") or "").strip().lower()
    return p if p in PROVIDERS else DEFAULT_PROVIDER


def model() -> str:
    p = provider()
    m = (os.environ.get("PRODUCTION_TRANSLATE_MODEL") or "").strip()
    if not m:
        return DEFAULT_MODELS[p]
    if (p == "anthropic") != m.startswith("claude-"):
        if m not in _warned_model:
            _warned_model.add(m)
            log.warning("PRODUCTION_TRANSLATE_MODEL=%s does not belong to provider %s; using %s", m, p, DEFAULT_MODELS[p])
        return DEFAULT_MODELS[p]
    return m


def _payload(text: str, src_lang: str, tgt_lang: str) -> dict:
    system = SYSTEM_PROMPT.format(src=_NAMES[src_lang], tgt=_NAMES[tgt_lang])
    if provider() == "anthropic":
        return {"model": model(), "max_tokens": MAX_TOKENS, "temperature": 0, "system": system,
                "messages": [{"role": "user", "content": str(text)}]}
    return {"model": model(), "temperature": 0, "provider": {**PROVIDER_PREFS, "order": list(PROVIDER_PREFS["order"])},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": str(text)}]}


def _answer(data: dict) -> tuple[str, str]:
    """(text, who served it) from an Anthropic (``content`` blocks) or an OpenRouter (``choices``) answer."""
    if isinstance(data.get("content"), list):
        text = "".join(str(b.get("text") or "") for b in data["content"] if isinstance(b, dict) and b.get("type", "text") == "text")
        return text.strip(), "anthropic:" + str(data.get("model") or "")[:40]
    return str(data["choices"][0]["message"]["content"]).strip(), str(data.get("provider") or "").strip()[:60]


def _post(payload: dict, timeout: float) -> dict:
    """The one place that touches the key and the network."""
    p = provider()
    key = (os.environ.get(KEY_ENV[p]) or "").strip()
    if not key:
        raise TranslateError("translation is not configured (no provider key)")
    if p == "anthropic":
        headers = {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION, "content-type": "application/json"}
    else:
        headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
    req = urllib.request.Request(URLS[p], data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def translate_text(text: str, src_lang: str, tgt_lang: str,
                   http_post: Optional[Callable[[dict, float], dict]] = None) -> str:
    """``text`` translated from ``src_lang`` to ``tgt_lang`` ("en" | "zh"); raises ``TranslateError`` on any failure."""
    if src_lang not in _NAMES or tgt_lang not in _NAMES:
        raise TranslateError("unsupported language pair %s -> %s" % (src_lang, tgt_lang))
    if not str(text or "").strip():
        return ""
    payload = _payload(text, src_lang, tgt_lang)
    try:
        out, served_by = _answer((http_post or _post)(payload, TIMEOUT_S))
    except TranslateError:
        raise
    except Exception as e:                                  # noqa: BLE001 - any provider failure degrades to "pending"
        log.warning("production translate failed (%s)", type(e).__name__)
        raise TranslateError("translation failed (%s)" % type(e).__name__) from None
    if not out:
        raise TranslateError("translation came back empty")
    log.info("production translate: %s -> %s served by provider %r", src_lang, tgt_lang, served_by or "unknown")
    return out
