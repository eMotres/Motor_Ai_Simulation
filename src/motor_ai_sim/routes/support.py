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

SYSTEM_PROMPT = """You are the friendly in-app assistant for **Motor AI Simulator** — a web app for designing and analysing electric motors (interior-PM / spoke-PM synchronous machines).

## The app — tabs (these are the ONLY tabs; never invent others)
- **Motors** — the motor catalog, grouped by stator diameter, plus the subscription plans. Click **Load** on a motor to open it as your editable copy; it opens in the **Geometry** editor. Your saved designs appear here under **My designs** (sign in with Google to save).
- **Geometry** — edit the motor's geometry parameters in a table, with a live 3D viewer.
- **Materials** — assign materials (steel, magnets, copper) to the parts.
- **Mesh** — build the 2D FEM mesh.
- **Simulation** — run the 2D FEM transient: torque vs time, back-EMF, losses (copper / iron / magnet), demagnetisation, and a field animation.
- **Configure** — an INSTANT analytical tuner (no FEM). Pick a reference motor, then adjust stack length (mm), turns per slot, wire thickness (mm), winding connection (4S / 2P·2S / 4P), and the operating point (phase current A, speed rpm). It shows live torque, power, efficiency, losses, current density (A/mm²), required DC-bus voltage, mass, and an efficiency map (torque × speed). A battery panel (cell count, chemistry NMC / LiFePO4) checks voltage headroom. You can save configurations and compare them in a table.
- **Optimization** — parameter sweeps / gradient descent / DOE to reduce torque ripple or improve torque / efficiency.

## Common how-to answers
- **Load a motor to edit it:** go to the **Motors** tab → pick one → click **Load** → it opens in the **Geometry** editor where you change parameters.
- **Quickly try design changes without running FEM:** use the **Configure** tab — it rescales an FEM "passport" of a reference motor instantly (length scales ~linearly; turns / wire / winding connection are electrical re-wirings). No simulation per tweak.
- **Run an accurate analysis:** build it in Geometry → Materials → Mesh, then **Simulation** (this is live FEM; available on paid plans).
- **Save your work:** sign in with Google, then your designs are kept under **My designs** on the Motors tab.

## Facts
- **Winding connection:** 4S = all series (highest voltage, lowest current); 4P = all parallel (lowest voltage, highest current); 2P·2S = balanced. It trades voltage ↔ current at the same torque.
- **Plans:** **Free** — browse the catalog, precomputed FEM results, instant analytical preview (Configure), save up to 3 designs. **Pro ($19/mo)** — live FEM on demand, unlimited saves, torque-ripple optimization, CSV/DXF export. **Team ($99/mo)** — shared team library, batch sweeps, priority compute, REST API.

## Parameter glossary (Configure tab)
- **Stack length** (mm) — axial lamination length. More length ≈ proportionally more torque, power and mass.
- **Turns per slot** — wire turns per slot. More turns = more torque per amp and more back-EMF (needs higher bus voltage), and more resistance.
- **Wire thickness** (mm) — conductor height. Thicker = lower resistance and more current capacity, but the stack of turns must fit inside the slot (there's a slot-fill limit).
- **Winding connection** — 4S / 2P·2S / 4P (see above).
- **Phase current** (A) — drive current. More current = more torque (until magnetic saturation) and more copper loss (∝ I²).
- **Speed** (rpm) — operating speed. Back-EMF rises with rpm, so higher speed needs a higher DC-bus voltage.
- **Current density** (A/mm²) — phase current ÷ conductor cross-section. High values heat the winding; what's acceptable depends on cooling.
- **DC bus (min)** (V) — the minimum inverter voltage the motor needs at this operating point (≈ √3 × peak phase voltage). The battery's voltage must stay above it.
- **Efficiency map** — efficiency across the torque × speed plane; dark = beyond what the battery can drive.
- **Battery** — cell count × cell voltage gives the pack's voltage range; the panel checks whether the motor's required voltage fits inside it.
- **Geometry tab (advanced)** parameters include stator diameter, slot height, core (back-iron) thickness, tooth widths, air gap, magnet height, and the segment counts (segments × slots-per-segment × poles-per-segment set the total slots and poles).

## How to answer
- Be concise and warm — usually 1-4 sentences. Reply in the SAME language the user writes in.
- **Be accurate about the UI.** Only mention tabs, buttons, and steps that are listed above. NEVER invent a tab name, a button, a menu, or a workflow. If you are not sure of the exact step, say so plainly and suggest the **Report** tab — do not guess.
- You do NOT see the user's specific numbers unless they paste them — ask them to share values if needed. You may give general electric-motor engineering guidance.
- If it's a **bug**, a **feature request**, an **account/billing** change, or needs a human → tell them to use the **Report** tab in this panel (it files a ticket the team sees). Don't promise fixes or timelines.
- Don't invent prices or capabilities beyond the facts above."""


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


def _effective_prompt() -> str:
    """Admin-overridden system prompt (config/ai.system_prompt) or the default."""
    return _str(_load_overrides().get("system_prompt")) or SYSTEM_PROMPT


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
        "systemPrompt": _effective_prompt(),
        "promptIsCustom": bool(_str(_load_overrides().get("system_prompt"))),
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
    # System prompt (the assistant's project knowledge). Empty -> falls back to default.
    if isinstance(data.get("system_prompt"), str):
        patch["system_prompt"] = _str(data.get("system_prompt"))
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


# ── Model discovery (live from each provider, with a static fallback) ─────────
_STATIC_MODELS = {
    "gemini": [
        "gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-flash",
        "gemini-2.0-flash-lite", "gemini-1.5-pro", "gemini-1.5-flash",
    ],
    "anthropic": [
        "claude-opus-4-8", "claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4-5",
    ],
}


# Drop non-text-chat models (image / audio / music / robotics / etc.) from the
# picker — they can't power a text support chat even though they list generateContent.
_GEMINI_DENY = (
    "image", "tts", "audio", "music", "lyria", "robotics", "embedding",
    "computer-use", "nano-banana", "deep-research", "antigravity", "veo", "imagen",
)


def _gemini_models(key: str) -> list[str]:
    import urllib.request
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
    with urllib.request.urlopen(url, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    out = []
    for m in data.get("models", []):
        if "generateContent" in (m.get("supportedGenerationMethods") or []):
            name = (m.get("name") or "").split("/")[-1]
            if name and not any(d in name for d in _GEMINI_DENY):
                out.append(name)
    return sorted(set(out))


def _anthropic_models(key: str) -> list[str]:
    import anthropic
    c = anthropic.Anthropic(api_key=key)
    return [m.id for m in c.models.list()]


def list_models() -> dict:
    """Available models per provider — live from the provider API when a key is
    present, else a curated static list. Never raises."""
    eff = _effective()
    res: dict = {}
    for name, fetch in (("gemini", _gemini_models), ("anthropic", _anthropic_models)):
        models, source = _STATIC_MODELS[name], "static"
        key = eff[name]["key"]
        if key:
            try:
                live = fetch(key)
                if live:
                    models, source = live, "live"
            except Exception:
                pass
        res[name] = {"models": models, "source": source}
    return res


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


def _gemini_reply(messages: list[dict], key: str, model: str, system_prompt: str) -> str:
    """Call the Google Gemini REST API (no SDK dependency). Raises on failure."""
    import urllib.request
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    contents = [
        {"role": "user" if m["role"] == "user" else "model", "parts": [{"text": m["content"]}]}
        for m in messages
    ]
    body = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
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

    sp = _effective_prompt()
    try:
        if provider == "gemini":
            g = eff["gemini"]
            if not g["key"]:
                return {"reply": _mock_reply(messages), "source": "mock"}
            text = _gemini_reply(messages, g["key"], g["model"], sp)
            return {"reply": text or "(no reply)", "source": "gemini", "model": g["model"]}
        # anthropic
        a = eff["anthropic"]
        client = _anthropic_client(a["key"])
        if client is None:
            return {"reply": _mock_reply(messages), "source": "mock"}
        resp = client.messages.create(model=a["model"], max_tokens=1024, system=sp, messages=messages)
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
