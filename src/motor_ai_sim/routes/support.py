"""Support assistant — proxies the in-app chat to Claude.

POST /api/support/chat takes the running conversation and returns the assistant's
reply. The Anthropic API key lives ONLY on the backend (ANTHROPIC_API_KEY env);
it is never shipped to the browser. When the key is absent (local dev) a canned
mock reply is returned (flagged source:"mock") so the widget works without
credentials.

Bugs / feature requests are stored as tickets in Firestore by the frontend
(users/{uid}/tickets); admins read them via /api/admin/tickets.
"""
from __future__ import annotations

import os
from fastapi import APIRouter, Body

router = APIRouter(prefix="/api/support", tags=["support"])

# Default to the most capable model; set ANTHROPIC_SUPPORT_MODEL=claude-haiku-4-5
# for ~5x lower cost on this high-volume, simple-Q&A surface.
SUPPORT_MODEL = os.environ.get("ANTHROPIC_SUPPORT_MODEL", "claude-opus-4-8")
_MAX_TURNS = 20

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

_client = None
_client_ready = False


def _get_client():
    """Lazily build the Anthropic client. Returns None if the SDK isn't installed
    or no API key is configured (then the endpoint serves a mock reply)."""
    global _client, _client_ready
    if _client_ready:
        return _client
    _client_ready = True
    if not os.environ.get("ANTHROPIC_API_KEY"):
        _client = None
        return None
    try:
        import anthropic
        _client = anthropic.Anthropic()
    except Exception:
        _client = None
    return _client


def _sanitize(raw) -> list[dict]:
    """Coerce the client payload into a clean alternating-friendly message list."""
    out: list[dict] = []
    for m in (raw or [])[-_MAX_TURNS:]:
        if not isinstance(m, dict):
            continue
        role, content = m.get("role"), m.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            out.append({"role": role, "content": content[:4000]})
    # the Messages API requires the first message to be from the user
    while out and out[0]["role"] != "user":
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

    client = _get_client()
    if client is None:
        return {"reply": _mock_reply(messages), "source": "mock"}

    try:
        # Minimal, model-agnostic params so the same call works on Opus or Haiku.
        resp = client.messages.create(
            model=SUPPORT_MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=messages,
        )
        text = next((b.text for b in resp.content if b.type == "text"), "")
        return {"reply": text or "(no reply)", "source": "claude", "model": SUPPORT_MODEL}
    except Exception as e:
        return {
            "reply": "Sorry — I couldn't answer just now. Please try again, or use the "
                     "**Report** tab to reach the team.",
            "source": "error",
            "detail": str(e)[:200],
        }
