"""Support assistant — proxies the in-app chat to an AI provider (Claude or Gemini).

POST /api/support/chat takes the running conversation and returns the assistant's
reply. API keys live ONLY on the backend and are NEVER shipped to the browser.

Config resolution (admin override > env > auto), per field:
  - provider:  Firestore config/ai.provider  >  SUPPORT_PROVIDER env  >  auto
  - keys/models: Firestore config/ai.*  >  env  >  built-in default

Admins set the override from the Admin UI (POST /api/admin/support -> set_overrides),
which writes config/ai in Firestore via the Admin SDK. Keys are write-only: the
status endpoint returns only a masked hint, never the key. IMPORTANT: lock your
Firestore rules so clients cannot read the `config` collection (see firestore.rules).

When no key is configured anywhere, the endpoint returns a flagged mock reply.
"""
from __future__ import annotations

import json
import os
import time
from fastapi import APIRouter, Body

router = APIRouter(prefix="/api/support", tags=["support"])

_MAX_TURNS = 20

# Env defaults (used when there's no admin override in Firestore).
ENV_PROVIDER = os.environ.get("SUPPORT_PROVIDER", "").strip().lower()
ENV_GEMINI_KEY = (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()
ENV_GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash").strip()
ENV_ANTHROPIC_KEY = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
ENV_ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_SUPPORT_MODEL", "claude-opus-4-8").strip()

SYSTEM_PROMPT = """You are the friendly in-app assistant for **Motor AI Simulator**, a web app for designing and analysing electric motors (interior-PM / spoke-PM synchronous machines).

Two experiences:
- **Configure** tab (every user): an instant analytical "Configurator". The user picks a reference motor and adjusts stack length (mm), turns per slot, wire thickness (mm), winding connection (4S / 2P·2S / 4P) and an operating point (phase current A, speed rpm). It shows live torque, power, efficiency, losses (copper / iron / magnet), current density (A/mm2), the required DC-bus voltage, mass, and an efficiency map over torque x speed. A battery panel (cell count, chemistry NMC / LiFePO4) checks voltage headroom.
- **Engineering** tabs (paid / admin): full 2D FEM — Geometry, Materials, Mesh, Simulation (transient torque, back-EMF, losses, demagnetisation) and Optimization (parameter sweeps, descent, DOE).

How the Configurator is instant: it analytically rescales ONE FEM-extracted "passport" of a reference motor — no new simulation per tweak. Length scales roughly linearly; turns / wire / connection are electrical re-wirings.

Winding connection: 4S = all series (highest voltage, lowest current); 4P = all parallel (lowest voltage, highest current); 2P·2S = balanced. It trades voltage <-> current at the same torque.

Plans: **Free** — browse the catalog, precomputed FEM results, instant analytical preview, save up to 3 designs. **Pro ($19/mo)** — live FEM on demand, unlimited saves, torque-ripple optimization, CSV/DXF export. **Team ($99/mo)** — shared team library, batch sweeps, priority compute, REST API.

How to answer:
- Be concise and warm — usually a few sentences. Reply in the SAME language the user writes in.
- Help with using the app, what parameters mean, and interpreting results. You may give general electric-motor engineering guidance, but you do NOT see the user's specific numbers unless they paste them — ask them to share the values if needed.
- If something is a **bug**, a **feature request**, an **account/billing change**, or needs a human, tell the user to use the **"Report"** tab in this panel — it files a ticket the team sees. Don't promise fixes or timelines.
- Don't invent exact specs, prices beyond the plans above, or capabilities you're unsure of. If you don't know, say so and point them to "Report"."""


# ── Firestore-backed admin overrides (config/ai) ─────────────────────────────
_fb_done = False
_fb_db = None


def _firestore():
    """Firestore client via the Admin SDK, or None if unavailable (local dev)."""
    global _fb_done, _fb_db
    if _fb_done:
        return _fb_db
    _fb_done = True
    try:
        import firebase_admin
        from firebase_admin import firestore
        if not firebase_admin._apps:
            firebase_admin.initialize_app()
        _fb_db = firestore.client()
    except Exception:
        _fb_db = None
    return _fb_db


_ov_cache: dict = {}
_ov_exp: float = 0.0


def _load_overrides() -> dict:
    """Admin overrides from Firestore config/ai, cached ~20s. {} if unavailable."""
    global _ov_cache, _ov_exp
    now = time.time()
    if now < _ov_exp:
        return _ov_cache
    data: dict = {}
    db = _firestore()
    if db is not None:
        try:
            snap = db.collection("config").document("ai").get()
            if getattr(snap, "exists", False):
                data = snap.to_dict() or {}
        except Exception:
            data = {}
    _ov_cache, _ov_exp = data, now + 20.0
    return data


def _str(v) -> str:
    return v.strip() if isinstance(v, str) else ""


def _effective() -> dict:
    """Merge admin overrides over env defaults. Keys resolve admin > env."""
    ov = _load_overrides()
    gem_key = _str(ov.get("gemini_key")) or ENV_GEMINI_KEY
    gem_src = "admin" if _str(ov.get("gemini_key")) else ("env" if ENV_GEMINI_KEY else "none")
    ant_key = _str(ov.get("anthropic_key")) or ENV_ANTHROPIC_KEY
    ant_src = "admin" if _str(ov.get("anthropic_key")) else ("env" if ENV_ANTHROPIC_KEY else "none")
    gem_model = _str(ov.get("gemini_model")) or ENV_GEMINI_MODEL
    ant_model = _str(ov.get("anthropic_model")) or ENV_ANTHROPIC_MODEL
    prov = (_str(ov.get("provider")) or ENV_PROVIDER).lower()
    if prov not in ("anthropic", "gemini"):
        prov = "gemini" if gem_key else ("anthropic" if ant_key else "none")
    return {
        "provider": prov,
        "gemini": {"key": gem_key, "model": gem_model, "key_source": gem_src},
        "anthropic": {"key": ant_key, "model": ant_model, "key_source": ant_src},
    }


def _mask(k: str):
    if not k:
        return None
    return (k[:4] + "…" + k[-4:]) if len(k) > 9 else ("…" + k[-2:])


def provider_status() -> dict:
    """Non-secret status for the admin UI — never returns a key, only a masked hint."""
    eff = _effective()
    g, a = eff["gemini"], eff["anthropic"]
    active = eff["provider"]
    model = g["model"] if active == "gemini" else a["model"] if active == "anthropic" else None
    configured = bool(g["key"]) if active == "gemini" else bool(a["key"]) if active == "anthropic" else False
    return {
        "provider": active, "model": model, "configured": configured,
        "providerOverride": _str(_load_overrides().get("provider")),  # "" = auto
        "store": "firestore" if _firestore() is not None else "env-only",
        "gemini": {"model": g["model"], "configured": bool(g["key"]), "hint": _mask(g["key"]), "keySource": g["key_source"]},
        "anthropic": {"model": a["model"], "configured": bool(a["key"]), "hint": _mask(a["key"]), "keySource": a["key_source"]},
    }


def set_overrides(data: dict, who: str = "admin") -> dict:
    """Persist admin overrides to Firestore config/ai. Keys are write-only.
    Returns {ok: False, error} when the store is unavailable (local dev)."""
    db = _firestore()
    if db is None:
        return {"ok": False, "error": "Settings store unavailable (Firebase Admin SDK not configured on this server). Configure via env vars in local dev."}
    patch: dict = {}
    prov = _str(data.get("provider")).lower()
    if prov in ("", "auto"):
        patch["provider"] = ""
    elif prov in ("anthropic", "gemini"):
        patch["provider"] = prov
    for f in ("gemini_model", "anthropic_model"):
        if isinstance(data.get(f), str):
            patch[f] = _str(data.get(f))
    for name in ("gemini", "anthropic"):
        kf = f"{name}_key"
        if data.get(f"{name}_key_clear"):
            patch[kf] = ""               # explicit clear
        elif _str(data.get(kf)):
            patch[kf] = _str(data.get(kf))  # set only when a non-empty value is provided
    patch["updatedBy"] = who
    try:
        from firebase_admin import firestore as _fs
        patch["updatedAt"] = _fs.SERVER_TIMESTAMP
    except Exception:
        pass
    db.collection("config").document("ai").set(patch, merge=True)
    global _ov_exp
    _ov_exp = 0.0  # invalidate cache so the change takes effect immediately
    return {"ok": True}


# ── Provider clients ─────────────────────────────────────────────────────────
_ant_clients: dict = {}


def _anthropic_client(key: str):
    if not key:
        return None
    if key in _ant_clients:
        return _ant_clients[key]
    try:
        import anthropic
        c = anthropic.Anthropic(api_key=key)
    except Exception:
        c = None
    _ant_clients[key] = c
    return c


def _gemini_reply(messages: list[dict], key: str, model: str) -> str:
    """Call the Google Gemini REST API (no SDK dependency). Raises on failure."""
    import urllib.request
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    contents = [
        {"role": "user" if m["role"] == "user" else "model", "parts": [{"text": m["content"]}]}
        for m in messages
    ]
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": contents,
        "generationConfig": {"maxOutputTokens": 1024},
    }
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        out = json.loads(resp.read().decode("utf-8"))
    cands = out.get("candidates") or []
    parts = (cands[0].get("content", {}).get("parts") if cands else None) or []
    return "".join(p.get("text", "") for p in parts).strip()


def _sanitize(raw) -> list[dict]:
    """Coerce the client payload into a clean alternating-friendly message list."""
    out: list[dict] = []
    for m in (raw or [])[-_MAX_TURNS:]:
        if not isinstance(m, dict):
            continue
        role, content = m.get("role"), m.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            out.append({"role": role, "content": content[:4000]})
    while out and out[0]["role"] != "user":  # the API requires a leading user turn
        out.pop(0)
    return out


def _mock_reply(messages: list[dict]) -> str:
    last = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    return (
        "⚠️ Demo mode — the AI assistant isn't switched on for this server yet "
        "(no API key configured). Once it's enabled I'll answer questions about the "
        "Configurator, motor parameters, plans and more.\n\n"
        + (f'You asked: "{last[:200]}".\n\n' if last else "")
        + "In the meantime, use the **Report** tab to send a bug or feature request "
        "straight to the team."
    )


@router.post("/chat")
def chat(body: dict = Body(default={})):
    messages = _sanitize(body.get("messages"))
    if not messages:
        return {"reply": "Hi! How can I help you with the motor simulator?", "source": "mock"}

    eff = _effective()
    provider = eff["provider"]
    if provider == "none":
        return {"reply": _mock_reply(messages), "source": "mock"}

    try:
        if provider == "gemini":
            g = eff["gemini"]
            if not g["key"]:
                return {"reply": _mock_reply(messages), "source": "mock"}
            text = _gemini_reply(messages, g["key"], g["model"])
            return {"reply": text or "(no reply)", "source": "gemini", "model": g["model"]}
        # anthropic
        a = eff["anthropic"]
        client = _anthropic_client(a["key"])
        if client is None:
            return {"reply": _mock_reply(messages), "source": "mock"}
        resp = client.messages.create(model=a["model"], max_tokens=1024, system=SYSTEM_PROMPT, messages=messages)
        text = next((b.text for b in resp.content if b.type == "text"), "")
        return {"reply": text or "(no reply)", "source": "claude", "model": a["model"]}
    except Exception as e:
        msg = str(e)
        rate_limited = "429" in msg or "quota" in msg.lower() or "RESOURCE_EXHAUSTED" in msg
        return {
            "reply": (
                "The assistant is busy right now (usage limit reached). Please try "
                "again in a minute — or use the **Report** tab to reach the team."
                if rate_limited else
                "Sorry — I couldn't answer just now. Please try again, or use the "
                "**Report** tab to reach the team."
            ),
            "source": "error",
            "detail": msg[:200],
        }
