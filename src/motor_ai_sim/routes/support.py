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

ANONYMOUS CALLERS (2026-09-17).  The landing page shows this widget to a
signed-out visitor, so the route is on ``auth._ANON_OK_PATHS`` and no longer
carries a tier — otherwise the product's own "how can I get access?" answer was
a 401 that the widget printed as "Sorry — I couldn't answer just now".  It is
the only open route that costs money per call, so the bill is held down here
instead of at the door:

  * per IP: ``ANON_BURST_MAX`` messages per ``ANON_BURST_WINDOW_S`` and
    ``ANON_DAY_MAX`` per day (the address nginx hands us — ``client_ip``);
  * for the anonymous audience as a whole: ``ANON_GLOBAL_DAY_MAX`` per day;
  * the history is cut to ``ANON_MAX_TURNS`` and each message to
    ``ANON_MAX_CHARS`` before it is spent on a provider call.

Over a cap the caller gets a polite canned reply with 429 and NO provider call,
and one line reaches the log.  A signed-in caller is never counted or capped —
the counters are keyed on "no credentials presented" and nothing else.  They are
in-memory (one API process) and thread-safe; a restart forgives everyone, which
is the right failure for a limit whose job is to bound a bill, not to punish.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import threading
import time
from typing import Optional

from fastapi import APIRouter, Body, Header, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/support", tags=["support"])

_MAX_TURNS = 20

# Env defaults (used when there's no admin override in Firestore).
ENV_PROVIDER = os.environ.get("SUPPORT_PROVIDER", "").strip().lower()
ENV_GEMINI_KEY = (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()
ENV_GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash").strip()
ENV_ANTHROPIC_KEY = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
ENV_ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_SUPPORT_MODEL", "claude-opus-4-8").strip()

SYSTEM_PROMPT = """You are the friendly in-app assistant for **AeroStator Core** — the engineering portal where an invited user opens a proven electric-motor design (permanent-magnet synchronous machines for aerospace, robotics, EV and marine drivetrains), tunes it to a spec, and runs the analyses that prove it: electromagnetic FEM, thermal, mechanical, cost.

## Access — by invitation only
- There is **no self-sign-up** and **no public pricing**. Accounts are created by the team, and each account is granted the specific motors it may open.
- A visitor asks for access with the **Request access** link on the landing page (it writes to vadim@motresres.com), or by writing to vadim@motresres.com directly.
- **Plans and pricing are agreed individually — write to vadim@motresres.com.** NEVER name a price, a plan, a tier or a trial: there is no price list to quote.
- Signing in is the **Sign in** button on the landing page (Google), for an account that already exists.

## The app — tabs (these are the ONLY tabs; never invent others; which ones a user sees depends on their account)
- **Motors** — the catalog: sections by stator diameter Ø → a **die** (a stamped lamination: frozen geometry) → a **configuration** (stack length, wire, turns, winding connection, Y/Δ, steel, magnet, battery) → its **duties** (operating points, with kW, N·m, rpm, A, V L-L, efficiency, ripple, losses, mass, KV). The green **▶** on a duty row loads that machine — geometry, winding, materials, operating point — into every other tab. A configuration row also carries **⭳ datasheet**, **⭳ report** and **pdf**. Private copies live under **My motors** above the catalog.
- **Geometry** — the parameter table of the loaded machine beside a live 3D view; **Save as new motor** keeps a modified one.
- **Materials** — the materials library (lamination steel, magnets, metals, insulators, coolants) with B-H and loss curves, and which material each part is made of.
- **Mesh** — the 2D FEM mesh: element size per component, the sector solved (full, 1/2, 1/4 …) and periodic pole/slot meshing. It rebuilds itself as a setting changes.
- **Electromagnetic** — the FEM transient: winding connection and Y/Δ, the operating point (motor or generator; sine current, target torque/power, PWM inverter, BLDC or a custom waveform; current, speed, current angle, temperatures), then **Run Simulation**. Out come torque and its ripple, back-EMF, copper / iron / magnet losses, R, L, KV/Kt/Km, the field animation and the transient curves.
- **3D** — the 3D end-effect model of one sector: geometry, mesh and the |B| / demagnetisation fields, at a chosen fidelity.
- **Mechanical** — rotor centrifugal stress and retaining-sleeve sizing at speed and overspeed, contacts and safety factors, plus vibration modes, shaft critical speeds and bearing / windage losses.
- **Thermal** — the steady-state temperature map: cooling mode (air, liquid, manual h, none, or **Robotics — still air + mount**, which bundles a still-air housing with its emissivity, the mount W/K, an open bore and the exposed end faces), bore and frame options, coolant and ambient. Below it the read-only result of the last **coupled EM ↔ thermal** loop.
- **Optimization** — one-click optimization (e.g. minimum torque ripple: explore, then refine), parameter sweeps, and a DOE screening of which variables matter; a result can be applied back to the design.
- **Compare** — saved runs side by side, showing only the inputs that differ next to the key results.
- **Cost** — the material cost of the loaded machine: an editable price per kg for copper, magnet, electrical steel and shaft steel plus labour, with the mass and cost of each item.
- **Configure** — the instant analytical tuner, no FEM: stack length, turns, wire thickness, winding connection, phase current and speed (and PWM carrier / DC bus where the machine's passport carries them), with live torque, power, efficiency, losses, voltages, current density, slot fill, an efficiency map, a battery panel, and saved configurations to compare.
- **Admin** — the team's own tab: accounts, per-account motor grants, sessions, tickets and this assistant's settings.

## Common how-to answers
- **Load a motor:** **Motors** tab → open the Ø section → the die → the configuration → click **▶** on the duty you want. Every other tab then describes that machine.
- **Run the coupled EM ↔ thermal loop:** set the cooling on the **Thermal** tab, then on the **Electromagnetic** tab switch on **Coupled thermal — solve for the temperatures** and press **Run Simulation**; it iterates until the winding and magnet temperatures settle, and the **Thermal** tab shows the converged result.
- **Generate a report or a datasheet:** **Motors** tab, on the configuration row — **⭳ report** (Word: every duty with its tables, field maps and warnings), **pdf** for the same, **⭳ datasheet** (Excel: a column per duty).
- **Try a change without a FEM run:** the **Configure** tab rescales the machine's measured passport instantly.
- **Save your work:** **💾 Save to <duty>** in the strip under the tab bar writes the current point back, or **＋ duty** on a configuration snapshots it as a new duty.

## Helping a user pick a machine
Ask what matters: the target **torque** and **speed** (or the mechanical load), the **diameter** budget, the **cooling** it will have, and the **supply** (battery cell count / DC-bus voltage). Then point at the nearest die in the catalog they have been granted, and tell them to load a duty with **▶** and open **Configure** to trim stack length / turns / wire / current onto their target while the battery panel confirms the voltage fits. Levers: more torque → a bigger diameter, a longer stack, more turns or more current; higher speed → fewer turns, to keep the bus voltage in range. These are starting points to confirm with a real run — don't overstate precision. What the catalog contains for a given account is decided by that account's grants; never promise a machine you cannot see.

## Facts
- **Winding connection** trades voltage ↔ current at the same torque: all-series = the highest voltage and the lowest current, all-parallel = the opposite, and the mixed layouts sit between. **Y (star)** vs **Δ (delta)** does the same at the machine's terminals (Δ ≈ √3 more current at √3 less line voltage).
- **Duty** = one operating point of a configuration (power, torque, speed, current, connection, temperatures, cooling). A configuration usually carries several — continuous, peak, generator …
- There is **no free tier and no published price list**: plans and pricing are agreed individually — write to vadim@motresres.com.

## Parameter glossary (Configure tab)
- **Stack length** (mm) — axial lamination length. More length ≈ proportionally more torque, power and mass.
- **Turns per slot** — wire turns per slot. More turns = more torque per amp and more back-EMF (needs higher bus voltage), and more resistance.
- **Wire thickness** (mm) — conductor height. Thicker = lower resistance and more current capacity, but the stack of turns must fit inside the slot (there's a slot-fill limit).
- **Winding connection** — series / parallel groups, plus Y or Δ (see above).
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
- If it's a **bug**, a **feature request**, an **account** question, or needs a human → the **Report** tab in this panel files a ticket the team sees, or they can write to vadim@motresres.com. Don't promise fixes or timelines.
- **Never invent a price, a plan, a tier, a discount or a delivery date** — access and commercial terms are agreed individually with vadim@motresres.com.
- Never discuss how this assistant itself is built, which model or vendor answers, or anything about the servers."""


#: Appended to the system prompt for a caller with NO account (the landing page
#: widget).  A visitor is not a user: they cannot open a tab, they have no
#: granted motors, and the only answer they are actually after is how to get in.
VISITOR_NOTE = """

## Visitor mode — this person is NOT signed in
They are on the public landing page and have no account yet. Answer product
questions briefly and plainly — what the portal is for, what it analyses, what a
motor design involves — in two or three sentences. When they ask about access,
say plainly that access is **by invitation**: use the **Request access** link on
the page, or write to vadim@motresres.com, and the team creates the account and
grants the motors it may open. Plans and pricing are agreed individually; never
name a price or a plan, and never say there is a free tier or a trial. Do NOT
describe internal data, the catalog's contents, any specific customer machine or
numbers from any design, and do not walk them step by step through tabs they
cannot open yet."""


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


# ── one retry, for the provider's own bad minute ─────────────────────────────
#: Statuses that mean "not now", not "no": Gemini answers 503 "the model is
#: overloaded" often enough that a visitor's FIRST and only question lands on
#: one (seen live on 2026-09-17, on the old prompt as well as the new one — the
#: same call succeeded two seconds later).  A visitor does not press send twice;
#: they read "Sorry — I couldn't answer just now" and leave.
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
_RETRY_PAUSE_S = 1.2


def _is_transient(e: Exception) -> bool:
    code = getattr(e, "code", None) or getattr(e, "status_code", None)
    if code in _RETRY_STATUS:
        return True
    m = str(e).lower()
    return any(w in m for w in ("unavailable", "overloaded", "timed out",
                                "timeout", "resource_exhausted"))


def _call_provider(fn, *, attempts: int = 2, pause: Optional[float] = None):
    """Run one provider call, retrying ONCE on a transient failure.

    Deliberately not a backoff ladder: the caller is a person watching a
    "thinking…" dot, and the rate limiter counts the request, not the attempts.
    """
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:                              # noqa: BLE001
            if i + 1 >= attempts or not _is_transient(e):
                raise
            logger.warning("support: provider call failed (%s: %s) — retrying once",
                           type(e).__name__, str(e)[:120])
            time.sleep(_RETRY_PAUSE_S if pause is None else pause)


def _sanitize(raw, *, max_turns: int = _MAX_TURNS, max_chars: int = 4000) -> list[dict]:
    """Coerce the client payload into a clean alternating-friendly message list.

    An anonymous caller gets the SHORTER caps (``ANON_MAX_TURNS`` /
    ``ANON_MAX_CHARS``): the history is what a chat costs per call, and a
    visitor's question fits in a paragraph.
    """
    out: list[dict] = []
    for m in (raw or [])[-max_turns:]:
        if not isinstance(m, dict):
            continue
        role, content = m.get("role"), m.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            out.append({"role": role, "content": content[:max_chars]})
    while out and out[0]["role"] != "user":  # the API requires a leading user turn
        out.pop(0)
    return out


# ── The anonymous audience: how much it may ask ──────────────────────────────
#: Per IP, per ``ANON_BURST_WINDOW_S`` — a real visitor asks a handful of
#: questions; eight in ten minutes is more than the owner's own two test runs
#: and far less than a script's.
ANON_BURST_MAX = 8
ANON_BURST_WINDOW_S = 600.0
#: Per IP, per day.
ANON_DAY_MAX = 40
#: And the whole anonymous audience per day — the ceiling on the bill even if
#: the traffic arrives from a thousand addresses.
ANON_GLOBAL_DAY_MAX = 500
#: What an anonymous conversation may carry into a provider call.
ANON_MAX_TURNS = 6
ANON_MAX_CHARS = 1000

_DAY_S = 86400.0
#: Env overrides, read per call so a server can retune without a code change.
_CAP_ENV = {
    "burst": "SUPPORT_ANON_BURST_MAX",
    "day": "SUPPORT_ANON_DAY_MAX",
    "global": "SUPPORT_ANON_GLOBAL_DAY_MAX",
}


def _cap(name: str) -> int:
    default = {"burst": ANON_BURST_MAX, "day": ANON_DAY_MAX,
               "global": ANON_GLOBAL_DAY_MAX}[name]
    raw = os.environ.get(_CAP_ENV[name], "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


#: The address families that can only be a hop of OUR OWN plumbing — the docker
#: bridge (172.16/12), a host-local proxy (127/8), a LAN (10/8, 192.168/16) —
#: and never a visitor arriving from the internet.  Spelled out rather than
#: taken from ``ip.is_private``, which in Python also answers True for the
#: documentation ranges (192.0.2/24, 198.51.100/24, 203.0.113/24) that tests and
#: examples are written in: those must behave like the visitors they stand for.
_INTERNAL_NETS = tuple(ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8",
    "169.254.0.0/16", "0.0.0.0/32", "::1/128", "fc00::/7", "fe80::/10",
))


def _is_internal(ip: str) -> bool:
    """Is this hop our own plumbing rather than a visitor?"""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(a in n for n in _INTERNAL_NETS if n.version == a.version)


def client_ip(request: Optional[Request]) -> str:
    """The VISITOR's address, read out of the proxy chain in front of us.

    On the server a request crosses TWO nginxes: the host one (TLS, port 443)
    and the container one (``deploy/nginx.conf``, in front of the API).  Both
    use ``$proxy_add_x_forwarded_for``, i.e. each APPENDS the peer it saw, so
    what arrives is

        <whatever the client itself sent>, <the real client>, 172.18.0.x

    — the last entry is the docker bridge and is the same for every visitor on
    earth (seen in the live log on 2026-09-17: ``ip=172.18.0.1`` for a per-IP
    limit, which would have made ONE bucket for the whole internet).  The last
    entry a client can forge is never the last PUBLIC one either, because every
    hop appends after it: dropping the internal tail and taking what remains is
    both correct and unforgeable.  ``X-Real-IP`` is rewritten by the inner nginx
    to that same bridge address, so it is only a fallback for a one-hop
    deployment; last comes the socket peer, which is what a local run without a
    proxy has.
    """
    if request is None:
        return ""
    try:
        headers = request.headers
    except Exception:                                        # pragma: no cover
        return ""
    xff = (headers.get("x-forwarded-for") or "").strip()
    if xff:
        hops = [h.strip() for h in xff.split(",") if h.strip()]
        while hops and _is_internal(hops[-1]):
            hops.pop()
        if hops:
            return hops[-1]
        last = [h.strip() for h in xff.split(",") if h.strip()]
        if last:                       # an all-internal chain: a LAN/dev call
            return last[-1]
    real = (headers.get("x-real-ip") or "").strip()
    if real:
        return real
    client = getattr(request, "client", None)
    return getattr(client, "host", "") or ""


_rl_lock = threading.Lock()
_ip_hits: dict[str, list[float]] = {}
_global_hits: list[float] = []

#: What the visitor reads instead of an answer.  It is still an ANSWER to the
#: question they came with — access is by invitation — so it names the way in.
_LIMIT_REPLIES = {
    "ip_burst": (
        "I've answered as many questions as I can from this connection for the "
        "moment — please try again in a few minutes.\n\n"
        "If you're after access: the portal is invitation-only. Use the "
        "**Request access** link on this page, or write to vadim@motresres.com, "
        "and we'll set you up."
    ),
    "ip_day": (
        "That's my daily limit for questions from this connection. Please write "
        "to vadim@motresres.com — access to the portal is by invitation and we "
        "answer every request personally."
    ),
    "global_day": (
        "The assistant has reached today's limit for visitors. Please write to "
        "vadim@motresres.com — access to the portal is by invitation, and we "
        "answer every request personally."
    ),
}
_RETRY_AFTER = {"ip_burst": int(ANON_BURST_WINDOW_S), "ip_day": 3600,
                "global_day": 3600}


def reset_limits() -> None:
    """Forget every counter (tests; a fresh process starts here anyway)."""
    with _rl_lock:
        _ip_hits.clear()
        _global_hits.clear()


def _charge_anonymous(ip: str, now: Optional[float] = None) -> Optional[str]:
    """Check AND record one anonymous message, atomically.

    Returns ``None`` when the call may go to the provider, else the key of the
    cap it hit (``ip_burst`` | ``ip_day`` | ``global_day``).  A refused call is
    NOT recorded: the counters measure what was spent, and a refusal spends
    nothing.
    """
    now = time.time() if now is None else now
    key = ip or "?"
    with _rl_lock:
        hits = [t for t in _ip_hits.get(key, ()) if now - t < _DAY_S]
        recent = sum(1 for t in hits if now - t < ANON_BURST_WINDOW_S)
        glob = [t for t in _global_hits if now - t < _DAY_S]
        _global_hits[:] = glob
        if recent >= _cap("burst"):
            _ip_hits[key] = hits
            return "ip_burst"
        if len(hits) >= _cap("day"):
            _ip_hits[key] = hits
            return "ip_day"
        if len(glob) >= _cap("global"):
            _ip_hits[key] = hits
            return "global_day"
        hits.append(now)
        _ip_hits[key] = hits
        _global_hits.append(now)
        if len(_ip_hits) > 5000:                             # pragma: no cover
            for k, v in list(_ip_hits.items()):
                if not v or now - v[-1] > _DAY_S:
                    _ip_hits.pop(k, None)
        return None


def _is_anonymous(authorization: Optional[str]) -> bool:
    """No credentials at all?  `caller_identity` is the ONE definition of who is
    calling in this backend, tier 'anon' its answer for "nobody presented any" —
    which also keeps the local/unconfigured workstation (where the developer IS
    the admin) out of the limiter, exactly as it is out of every other gate."""
    try:
        from motor_ai_sim.auth import caller_identity
        return caller_identity(authorization).get("tier") == "anon"
    except Exception:                                        # pragma: no cover
        return True


def _mock_reply(messages: list[dict]) -> str:
    last = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    return (
        "⚠️ Demo mode — the AI assistant isn't switched on for this server yet "
        "(no API key configured). Once it's enabled I'll answer questions about the "
        "Configurator, motor parameters, the catalog and how the app works.\n\n"
        + (f'You asked: "{last[:200]}".\n\n' if last else "")
        + "In the meantime, use the **Report** tab to send a bug or feature request "
        "straight to the team."
    )


@router.post("/chat")
def chat(request: Request, body: dict = Body(default={}),
         authorization: Optional[str] = Header(default=None)):
    anon = _is_anonymous(authorization)
    messages = _sanitize(
        body.get("messages"),
        max_turns=ANON_MAX_TURNS if anon else _MAX_TURNS,
        max_chars=ANON_MAX_CHARS if anon else 4000,
    )
    if not messages:
        return {"reply": "Hi! How can I help you with the motor simulator?", "source": "mock"}

    # The limiter sits in front of the provider call and nowhere else: it must
    # bound what is SPENT, so a signed-in user never meets it and a refusal
    # never reaches the provider.
    if anon:
        ip = client_ip(request)
        hit = _charge_anonymous(ip)
        if hit is not None:
            logger.warning("support: anonymous chat REFUSED (%s) ip=%s ua=%r",
                           hit, ip or "?",
                           (request.headers.get("user-agent") if request else "")
                           or "")
            return JSONResponse(
                status_code=429,
                content={"reply": _LIMIT_REPLIES[hit], "source": "rate_limited",
                         "limit": hit},
                headers={"Retry-After": str(_RETRY_AFTER[hit])},
            )

    eff = _effective()
    provider = eff["provider"]
    if provider == "none":
        return {"reply": _mock_reply(messages), "source": "mock"}

    sp = _effective_prompt() + (VISITOR_NOTE if anon else "")
    try:
        if provider == "gemini":
            g = eff["gemini"]
            if not g["key"]:
                return {"reply": _mock_reply(messages), "source": "mock"}
            text = _call_provider(
                lambda: _gemini_reply(messages, g["key"], g["model"], sp))
            return {"reply": text or "(no reply)", "source": "gemini", "model": g["model"]}
        # anthropic
        a = eff["anthropic"]
        client = _anthropic_client(a["key"])
        if client is None:
            return {"reply": _mock_reply(messages), "source": "mock"}
        resp = _call_provider(lambda: client.messages.create(
            model=a["model"], max_tokens=1024, system=sp, messages=messages))
        text = next((b.text for b in resp.content if b.type == "text"), "")
        return {"reply": text or "(no reply)", "source": "claude", "model": a["model"]}
    except Exception as e:
        msg = str(e)
        rate_limited = "429" in msg or "quota" in msg.lower() or "RESOURCE_EXHAUSTED" in msg
        logger.warning("support: provider call failed (%s: %s)%s",
                       type(e).__name__, msg[:160],
                       " [anonymous]" if anon else "")
        # A VISITOR has no Report tab (it needs an account), so sending them
        # there is sending them nowhere — they get the address instead, which
        # is the answer they came for anyway.
        where = ("write to vadim@motresres.com — access is by invitation and we "
                 "answer every request personally"
                 if anon else
                 "use the **Report** tab to reach the team")
        return {
            "reply": (
                f"The assistant is busy right now (usage limit reached). Please try "
                f"again in a minute — or {where}."
                if rate_limited else
                f"Sorry — I couldn't answer just now. Please try again, or {where}."
            ),
            "source": "error",
            "detail": msg[:200],
        }
