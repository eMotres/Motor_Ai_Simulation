"""The anonymous tier of the MCP server: public discovery tools and the
machine-readable authentication errors.  docs/MCP_DISCOVERY.md.

An MCP client that has not signed in may learn WHAT AeroStator is, what it
can compute, what each calculation needs and HOW to sign in or sign up — and
nothing else.  Everything here is static service description built from the
code's own constants (scopes, validators, the tool table); no function in this
module reads a user store, a workspace, a job or a catalog entry, and none of
them takes a ``Principal``.

The allow-list that makes this safe is enforced in ``mcp_app.McpGate``
(fail closed): an anonymous request may only use ``ANON_METHODS``, only call
``PUBLIC_TOOLS`` and only read ``PUBLIC_RESOURCES``.

Every URL is built from ``oauth.base_url()`` (env ``PUBLIC_BASE_URL``), never
from the request's Host header and never localhost.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any, Dict, List, Optional

from motor_ai_sim import agent_keys as _keys
from motor_ai_sim import oauth as _oauth

SERVICE_NAME = "AeroStator"
OPERATOR = "MOTRES d.o.o."
LICENSE = "AGPL-3.0-or-later"

#: Tools an unauthenticated client may call (read-only service description).
PUBLIC_TOOLS = frozenset({
    "describe_service", "list_capabilities", "list_calculation_types",
    "get_input_requirements", "how_to_authenticate", "start_sign_up",
})
#: JSON-RPC methods an unauthenticated client may send.  Anything else -> 401.
ANON_METHODS = frozenset({
    "initialize", "ping", "notifications/initialized", "notifications/cancelled",
    "tools/list", "tools/call", "resources/list", "resources/read",
    "resources/templates/list", "prompts/list",
    # 2026-07-28 clients probe with this instead of initialize: server
    # capabilities + instructions only (SDK default handler)
    "server/discover",
})
#: Resources an unauthenticated client may list and read.
PUBLIC_RESOURCES = frozenset({"emotres://guide"})
#: Resource templates / prompts an unauthenticated client may LIST (none
#: today).  The list handlers filter to these, so anything registered later
#: stays invisible anonymously until it is added here on purpose.
PUBLIC_RESOURCE_TEMPLATES: frozenset = frozenset()
PUBLIC_PROMPTS: frozenset = frozenset()
#: Registered but signed-in only.  Every registered resource, template and
#: prompt must be in the public or the private table (tests/test_mcp_discovery.py).
PRIVATE_RESOURCES: frozenset = frozenset()
PRIVATE_RESOURCE_TEMPLATES: frozenset = frozenset()
PRIVATE_PROMPTS: frozenset = frozenset()

#: What a sign-in asks for when the client has no better hint: the read-only
#: pair a new agent key gets by default.  The owner can untick scopes on the
#: consent page; write scopes are asked for by step-up when a tool needs them.
DEFAULT_SIGN_IN_SCOPES = tuple(_keys.DEFAULT_SCOPES)


# ── public links ─────────────────────────────────────────────────────────────

def _env(name: str, default: str) -> str:
    return (os.environ.get(name) or "").strip() or default


def links() -> Dict[str, str]:
    """Every public URL the discovery answers and auth errors quote."""
    b = _oauth.base_url()
    return {
        "website": b + "/",
        "mcp_endpoint": _oauth.resource_url(),
        "sign_in_url": b + "/?signin=1",
        "sign_up_url": b + "/?signup=1",
        "resource_metadata_url": _oauth.resource_metadata_url(),
        "authorization_server_metadata_url": _oauth.authorization_server_metadata_url(),
        "authorize_url": _oauth.authorization_endpoint(),
        "token_url": b + "/oauth/token",
        "registration_url": b + "/oauth/register",
        "docs_url": _env("MCP_DOCS_URL",
                         "https://github.com/eMotres/Motor_Ai_Simulation/blob/"
                         "pre-migration-freeze-2026-09-15/docs/MCP_DISCOVERY.md"),
        "source_code_url": _env("PUBLIC_SOURCE_URL",
                                "https://github.com/eMotres/Motor_Ai_Simulation"),
        "terms_url": _env("PUBLIC_TERMS_URL", "https://emotres.com/terms-conditions"),
        "privacy_url": _env("PUBLIC_PRIVACY_URL", "https://emotres.com/privacy-policy"),
    }


# ── the tool catalogue ───────────────────────────────────────────────────────

#: One line per tool.  ``tests/test_mcp_discovery.py`` checks this covers
#: every registered tool, so a new tool cannot ship undescribed.
TOOL_SUMMARIES: Dict[str, str] = {
    "describe_service": "What AeroStator is, who runs it, licence and links.",
    "list_capabilities": "This catalogue: every tool, its scope and whether it needs sign-in.",
    "list_calculation_types": "The calculations AeroStator supports and which tools run them.",
    "get_input_requirements": "Required and optional inputs (units, valid ranges) of one calculation type.",
    "how_to_authenticate": "Sign-in and sign-up URLs, OAuth metadata, scopes and the API-key alternative.",
    "start_sign_up": "The account sign-up page to open in a browser (no password ever goes through chat).",
    "list_catalog": "Materials, wires, bearings, power devices and dies in the catalog.",
    "get_catalog_entry": "One catalog entry by kind and id.",
    "list_machines": "Machines your account may see, with headline ratings from saved results.",
    "get_machine_performance": "Saved results of one duty: efficiency, loss split, temperatures, ratings.",
    "check_fit": "Rank existing machines against torque / speed / voltage / size limits (no solve).",
    "start_design": "Create a DRAFT design from requirements, scaled from the nearest existing machine.",
    "get_design": "One draft: requirements, starting point, parameters, estimate, runs.",
    "simulate": "Queue a 2-D FEM simulation (em | thermal | coupled) of a draft on your job queue.",
    "get_job": "Status, queue position, progress and ETA of a queued simulation.",
    "get_design_result": "Headline results of a draft's finished runs and the requirement checks.",
    "open_in_configure": "The link that opens a draft in the Configure tab of the web app.",
}


def capabilities() -> Dict[str, Any]:
    from motor_ai_sim import mcp_app as _app
    tools = []
    for name in sorted(PUBLIC_TOOLS) + sorted(_app.TOOL_SCOPES):
        scope = _app.TOOL_SCOPES.get(name)
        tools.append({
            "name": name,
            "purpose": TOOL_SUMMARIES.get(name, ""),
            "required_scope": scope,
            "requires_auth": scope is not None,
            "read_only": name not in _app.WRITE_TOOLS,
        })
    scopes = [{"scope": s, "description": _keys.SCOPE_DESCRIPTIONS.get(s, ""),
               "unlocks": sorted(t for t, need in _app.TOOL_SCOPES.items() if need == s),
               "default_on_new_key": s in _keys.DEFAULT_SCOPES}
              for s in _keys.SCOPES]
    return {"service": SERVICE_NAME, "tools": tools, "scopes": scopes,
            "public_resources": sorted(PUBLIC_RESOURCES),
            "note": ("tools with requires_auth=false work without sign-in and "
                     "return only this public service description; the others "
                     "need an OAuth sign-in (or an agent key) carrying "
                     "required_scope. tools/list shows the public tools until "
                     "you sign in, then every tool your scopes allow.")}


# ── calculation types (what the code really runs) ──────────────────────────

def _calc_types() -> List[Dict[str, Any]]:
    return [
        {"id": "machine_fit_check",
         "title": "Fit check against saved machines",
         "what": ("ranks the saved duties of the machines your account may see "
                  "against torque, speed, DC-bus voltage, outer diameter, active "
                  "length, mass and cooling; returns margins in percent. "
                  "Nothing is simulated."),
         "method": "comparison with saved 2-D FEM results",
         "available_via": "mcp", "tools": ["check_fit"], "required_scope": "machines:read"},
        {"id": "saved_performance",
         "title": "Saved performance of a catalog machine",
         "what": ("operating point, shaft efficiency, loss split, coil / magnet / "
                  "bearing temperatures, continuous rating, KV and torque ripple "
                  "of one saved duty."),
         "method": "saved 2-D FEM results (energy-method torque, coupled thermal)",
         "available_via": "mcp", "tools": ["list_machines", "get_machine_performance"],
         "required_scope": "machines:read"},
        {"id": "design_from_requirements",
         "title": "Draft design from requirements",
         "what": ("validates the requirements (asks for missing or contradictory "
                  "ones), picks the nearest existing machine you may use and "
                  "scales stack length, turns and parallel paths; returns an "
                  "analytical estimate and a draft id. No new laminations."),
         "method": "analytical scaling of a FEM-characterised machine",
         "available_via": "mcp", "tools": ["start_design", "get_design"],
         "required_scope": "designs:write"},
        {"id": "em_fem_transient",
         "title": "2-D FEM electromagnetic transient",
         "what": ("torque (energy method) and its ripple, back-EMF, line voltage, "
                  "phase current, copper / core / magnet / solid-part losses and "
                  "efficiency of a draft at its operating point."),
         "method": "2-D finite-element transient, time-stepped over an electrical period",
         "available_via": "mcp", "tools": ["simulate", "get_job", "get_design_result"],
         "simulate_what": "em", "required_scope": "simulate"},
        {"id": "thermal",
         "title": "Thermal (one EM pass + steady-state thermal)",
         "what": "coil, magnet and bearing temperatures from one EM loss pass.",
         "method": "2-D FEM transient losses into a steady-state thermal solve",
         "available_via": "mcp", "tools": ["simulate", "get_job", "get_design_result"],
         "simulate_what": "thermal", "required_scope": "simulate"},
        {"id": "coupled_em_thermal",
         "title": "Coupled EM <-> thermal to steady temperatures",
         "what": ("iterates the EM transient and the thermal solve until winding "
                  "and magnet temperatures settle; performance at those temperatures."),
         "method": "fixed-point loop of 2-D FEM transient and thermal solves",
         "available_via": "mcp", "tools": ["simulate", "get_job", "get_design_result"],
         "simulate_what": "coupled", "required_scope": "simulate"},
        {"id": "catalog_lookup",
         "title": "Materials and component catalog",
         "what": ("magnets, lamination steels, wires, bearings, power devices and "
                  "dies with their key specs and provenance."),
         "method": "catalog lookup", "available_via": "mcp",
         "tools": ["list_catalog", "get_catalog_entry"], "required_scope": "catalog:read"},
        # In the web app only (not exposed through MCP yet) — listed so an agent
        # can tell the engineer where to find them, never simulated from here.
        {"id": "efficiency_map",
         "title": "Efficiency map (analytical tuner)",
         "what": "efficiency over the torque x speed plane with the battery limit, in the Configure tab.",
         "method": "analytical rescaling of a machine's FEM passport",
         "available_via": "web", "tools": [], "web_tab": "Configure"},
        {"id": "mechanical",
         "title": "Rotor mechanics",
         "what": ("rotor centrifugal stress and sleeve sizing at speed and "
                  "overspeed, safety factors, vibration modes, shaft critical "
                  "speeds, bearing and windage losses."),
         "method": "finite-element and analytical rotor models",
         "available_via": "web", "tools": [], "web_tab": "Mechanical"},
        {"id": "controller_losses",
         "title": "Inverter (controller) losses",
         "what": "power-device conduction and switching losses and junction temperature on a coldplate.",
         "method": "device datasheet / SPICE loss tables",
         "available_via": "web", "tools": [], "web_tab": "Controller"},
        {"id": "end_effect_3d",
         "title": "3-D end-effect static",
         "what": "3-D field of one sector for the end-effect correction of the 2-D torque.",
         "method": "3-D finite-element magnetostatics",
         "available_via": "web", "tools": [], "web_tab": "3D"},
        {"id": "optimization",
         "title": "Optimization and sweeps",
         "what": "parameter sweeps, DOE screening and optimization of a loaded machine.",
         "method": "repeated 2-D FEM runs",
         "available_via": "web", "tools": [], "web_tab": "Optimization"},
    ]


def calculation_types() -> Dict[str, Any]:
    return {"calculation_types": _calc_types(),
            "note": ("available_via 'mcp' = an agent can run it after sign-in "
                     "with required_scope; 'web' = in the AeroStator web app "
                     "only. Call get_input_requirements(calculation_type) for "
                     "the inputs.")}


def _field(name: str, unit: Optional[str], description: str, *,
           type_: str = "number", required: bool = False, **extra) -> Dict[str, Any]:
    """One input; ``unit`` is a UCUM code (N.m, /min, V, mm, kg, Cel, kW)."""
    f: Dict[str, Any] = {"name": name, "type": type_, "required": required,
                         "unit": unit, "description": description}
    f.update({k: v for k, v in extra.items() if v is not None})
    return f


def _design_inputs() -> Dict[str, Any]:
    from motor_ai_sim import agent_designs as _ad
    return {
        "tool": "start_design", "argument": "requirements",
        "rule": ("give torque_nm or power_kw, plus speed_rpm, dc_bus_v, cooling "
                 "and duty; a missing or contradictory essential comes back as "
                 "status 'needs_input' (ask the engineer, never guess)"),
        "inputs": [
            _field("torque_nm", "N.m", "shaft torque at rated speed (or give power_kw)",
                   required=True, range=list(_ad.TORQUE_RANGE_NM), exclusive_minimum=0,
                   alternative="power_kw"),
            _field("power_kw", "kW", "shaft power at rated speed (alternative to torque_nm); "
                   f"must agree with torque x speed within {_ad.POWER_TOLERANCE:.0%}",
                   exclusive_minimum=0),
            _field("speed_rpm", "/min", "rated speed", required=True,
                   range=list(_ad.SPEED_RANGE_RPM), exclusive_minimum=0),
            _field("dc_bus_v", "V", "DC bus voltage of the inverter", required=True,
                   range=list(_ad.DC_BUS_RANGE_V), typical=list(_ad.DC_BUS_OPTIONS_V)),
            _field("cooling", None, "how the heat leaves the machine", type_="string",
                   required=True, options=list(_ad.COOLINGS)),
            _field("duty", None, "S1 continuous, S2 short-time, S3 intermittent, or peak",
                   type_="string", required=True, options=list(_ad.DUTIES)),
            _field("max_speed_rpm", "/min", "maximum speed (default = rated); "
                   "must not be below speed_rpm", exclusive_minimum=0),
            _field("max_outer_diameter_mm", "mm", "max stator outer diameter", exclusive_minimum=0),
            _field("max_length_mm", "mm", "max active (stack) length", exclusive_minimum=0),
            _field("max_mass_kg", "kg", "max active mass", exclusive_minimum=0),
            _field("ambient_c", "Cel", "ambient / coolant temperature",
                   range=list(_ad.AMBIENT_RANGE_C), default=_ad.AMBIENT_DEFAULT_C),
            _field("mode", None, "motor or generator", type_="string",
                   options=list(_ad.MODES), default="motor"),
            _field("application", None, "free-text application notes (max 500 characters)",
                   type_="string"),
        ],
        "optional_arguments": [
            _field("base", None, "force a starting machine: {die, config, duty?} "
                   "(names from list_machines)", type_="object"),
            _field("name", None, "draft name", type_="string"),
        ],
    }


def _simulate_inputs(what: str) -> Dict[str, Any]:
    from motor_ai_sim import agent_designs as _ad
    return {
        "tool": "simulate",
        "rule": ("simulate a DRAFT: create it with start_design first; the "
                 "operating point, materials and cooling come from the draft. "
                 "Returns job_id at once; poll get_job, then get_design_result."),
        "inputs": [
            _field("design_id", None, "draft id d-xxxxxxxxxxxx from start_design",
                   type_="string", required=True, pattern=r"^d-[0-9a-f]{12}$"),
            _field("what", None, "which solve", type_="string", required=True,
                   options=list(_ad.WHATS), value=what),
            _field("steps", "1", "time steps per electrical period (optional)",
                   type_="integer", range=list(_ad.STEPS_RANGE)),
        ],
        "limits": "daily fair-use limit of simulations per account (429 + Retry-After when used up)",
    }


def input_requirements(calculation_type: str) -> Dict[str, Any]:
    """Inputs of one calculation type, from the real tool schemas/validators."""
    ct = (calculation_type or "").strip()
    types = {t["id"]: t for t in _calc_types()}
    if ct not in types:
        raise ValueError(f"unknown calculation_type {ct!r}; one of {sorted(types)}")
    t = types[ct]
    head = {"calculation_type": ct, "title": t["title"],
            "available_via": t["available_via"],
            "required_scope": t.get("required_scope"),
            "units": "UCUM codes (N.m, /min = rpm, V, mm, kg, Cel = degC, kW, 1)"}
    if ct == "machine_fit_check":
        return head | {"tool": "check_fit", "inputs": [
            _field("torque_nm", "N.m", "required shaft torque", required=True, exclusive_minimum=0),
            _field("speed_rpm", "/min", "speed at which that torque is needed",
                   required=True, exclusive_minimum=0),
            _field("voltage_v", "V", "available DC bus voltage", exclusive_minimum=0),
            _field("max_diameter_mm", "mm", "max outer (stator) diameter", exclusive_minimum=0),
            _field("max_length_mm", "mm", "max active (stack) length", exclusive_minimum=0),
            _field("max_mass_kg", "kg", "max active mass", exclusive_minimum=0),
            _field("cooling", None, "required cooling, e.g. air | robotics | liquid",
                   type_="string"),
            _field("limit", "1", "max rows per list", type_="integer", range=[1, 50], default=10),
        ]}
    if ct == "saved_performance":
        return head | {"tool": "get_machine_performance", "inputs": [
            _field("die", None, "die name from list_machines", type_="string", required=True),
            _field("config", None, "configuration name", type_="string", required=True),
            _field("duty", None, "duty (operating point) name", type_="string", required=True),
        ]}
    if ct == "design_from_requirements":
        return head | _design_inputs()
    if ct in ("em_fem_transient", "thermal", "coupled_em_thermal"):
        return head | _simulate_inputs(t["simulate_what"])
    if ct == "catalog_lookup":
        from motor_ai_sim.mcp_tools import CATALOG_KINDS
        return head | {"tool": "list_catalog", "inputs": [
            _field("kind", None, "catalog kind", type_="string", required=True,
                   options=list(CATALOG_KINDS)),
            _field("query", None, "case-insensitive substring filter", type_="string"),
        ]}
    return head | {"inputs": [], "web_tab": t.get("web_tab"),
                   "note": ("run in the AeroStator web app; its inputs are the "
                            f"settings of the {t.get('web_tab')} tab for the "
                            "loaded machine. Not callable through MCP yet.")}


# ── service description, authentication, sign-up ────────────────────────────

def describe_service() -> Dict[str, Any]:
    L = links()
    return {
        "name": SERVICE_NAME,
        "operator": OPERATOR,
        "summary": ("Engineering service for permanent-magnet electric motors "
                    "and generators: a catalog of FEM-characterised machines, "
                    "draft designs from requirements, and 2-D FEM "
                    "electromagnetic, thermal and coupled simulations on the "
                    "user's own job queue."),
        "for_whom": "engineers sizing motors / generators (aerospace, robotics, EV, marine)",
        "license": LICENSE,
        "website": L["website"],
        "mcp_endpoint": L["mcp_endpoint"],
        "source_code_url": L["source_code_url"],
        "terms_url": L["terms_url"],
        "privacy_url": L["privacy_url"],
        "docs_url": L["docs_url"],
        "access": ("the public tools (list_capabilities) work without an "
                   "account; machines, drafts, simulations and your saved "
                   "results need an account and an OAuth sign-in (or an agent key). "
                   "Which catalog machines an account may see is granted per "
                   "account."),
        "data_policy": ("anonymous calls return only this public description; "
                        "they never read accounts, workspaces, machines, drafts, "
                        "jobs or usage. Only public-datasheet data leaves the "
                        "server — never internal dimensions or drawings."),
        "next": "list_calculation_types, then how_to_authenticate to sign in",
    }


def _scope_rows() -> List[Dict[str, Any]]:
    from motor_ai_sim import mcp_app as _app
    return [{"scope": s, "description": _keys.SCOPE_DESCRIPTIONS.get(s, ""),
             "unlocks": sorted(t for t, need in _app.TOOL_SCOPES.items() if need == s)}
            for s in _keys.SCOPES]


def how_to_authenticate() -> Dict[str, Any]:
    L = links()
    return {
        "recommended": "oauth",
        "oauth": {
            "how": ("use your AI app's Connect / Sign in button for this server "
                    "(claude.ai and ChatGPT custom connectors, Claude Code /mcp). "
                    "A browser page opens on the AeroStator site: sign in or "
                    "create an account there, then Allow."),
            "spec": "MCP authorization (OAuth 2.1, PKCE S256, RFC 9728 / 8414 / 7591)",
            "resource": L["mcp_endpoint"],
            "resource_metadata_url": L["resource_metadata_url"],
            "authorization_server_metadata_url": L["authorization_server_metadata_url"],
            "authorize_url": L["authorize_url"],
            "token_url": L["token_url"],
            "registration_url": L["registration_url"],
            "default_scopes": list(DEFAULT_SIGN_IN_SCOPES),
        },
        "api_key": {
            "how": ("sign in on the website, open the avatar menu -> Access for "
                    "agents -> Create key, tick the scopes, copy the emk_ key "
                    "(shown once) and send it as 'Authorization: Bearer emk_...'."),
            "url": L["website"],
        },
        "sign_in_url": L["sign_in_url"],
        "sign_up_url": L["sign_up_url"],
        "scopes": _scope_rows(),
        "never": "never type a password into the chat; sign-in and sign-up happen only on the AeroStator page",
        "docs_url": L["docs_url"],
        "user_message": ("To use your AeroStator machines and simulations, connect "
                         "AeroStator in this app (Connect / Sign in); the page that "
                         "opens lets you sign in or create an account."),
    }


def start_sign_up() -> Dict[str, Any]:
    L = links()
    return {
        "sign_up_url": L["sign_up_url"],
        "preferred": ("click Connect / Sign in for AeroStator in your AI app and "
                      "choose 'Create account' on the page that opens: after the "
                      "e-mail confirmation you continue straight to the consent "
                      "step and come back signed in"),
        "steps": [
            "open the sign-up page (or the Connect / Sign in page of your AI app)",
            "create the account with e-mail + password or with Google, accepting the terms",
            "confirm the e-mail from the link we send (password accounts)",
            "sign in and click Allow; the AI app is then connected",
        ],
        "account_created_by_this_call": False,
        "terms_url": L["terms_url"],
        "privacy_url": L["privacy_url"],
        "never": "do not ask the user for a password in chat; the password is typed only on the AeroStator page",
        "user_message": (f"Create your AeroStator account at {L['sign_up_url']} "
                         "(or via Connect / Sign in in this app), confirm the "
                         "e-mail, then connect AeroStator here."),
    }


# ── "the client said it has no account" hint (per client IP) ────────────────

SIGN_UP_HINT_TTL_S = 1800
_hint_lock = threading.Lock()
_sign_up_hint: Dict[str, float] = {}


def note_sign_up_intent(ip: str, now: Optional[float] = None) -> None:
    """An anonymous start_sign_up call from ``ip``: its next auth errors say
    ``required_action: sign_up`` for a while.  A hint only (never access)."""
    if not ip:
        return
    t = time.time() if now is None else now
    with _hint_lock:
        if len(_sign_up_hint) > 10000:
            for k in [k for k, v in _sign_up_hint.items() if t - v > SIGN_UP_HINT_TTL_S]:
                del _sign_up_hint[k]
            if len(_sign_up_hint) > 10000:
                _sign_up_hint.clear()
        _sign_up_hint[ip] = t


def wants_sign_up(ip: str, now: Optional[float] = None) -> bool:
    t = time.time() if now is None else now
    with _hint_lock:
        v = _sign_up_hint.get(ip or "")
    return v is not None and t - v <= SIGN_UP_HINT_TTL_S


def reset_hints() -> None:
    with _hint_lock:
        _sign_up_hint.clear()


# ── machine-readable authentication errors ──────────────────────────────────

AUTH_ERROR_CODE = -32001


def auth_error(*, error: str, required_action: str, tool: str = "",
               method: str = "", required_scope: str = "", reason: str = "",
               principal_kind: str = "") -> Dict[str, Any]:
    """The ``error`` member of a JSON-RPC error response for an auth refusal.

    ``error``: authentication_required | invalid_token | insufficient_scope |
    account_disabled.  ``required_action``: sign_in | sign_up |
    reauthenticate | grant_scope | contact_support."""
    L = links()
    what = f"'{tool}'" if tool else "this request"
    if required_action == "sign_up":
        msg = (f"Create an AeroStator account to use {what}: click Connect / Sign in "
               "for AeroStator in this app and choose 'Create account', or open "
               f"{L['sign_up_url']}; then confirm the e-mail and connect.")
    elif required_action == "sign_in":
        msg = (f"Sign in to AeroStator to use {what}: click Connect / Sign in for "
               "AeroStator in this app; the page that opens also lets you create "
               "an account.")
    elif required_action == "reauthenticate":
        msg = ("Your AeroStator sign-in has expired or was revoked; reconnect "
               "AeroStator in this app (Connect / Sign in) to continue.")
    elif required_action == "grant_scope":
        if principal_kind == "api_key":
            msg = (f"Your AeroStator key lacks the '{required_scope}' permission needed "
                   f"for {what}; create a key with it under Access for agents.")
        else:
            msg = (f"{what[0].upper() + what[1:]} needs the '{required_scope}' permission; "
                   "reconnect AeroStator in this app and allow it on the consent page.")
    else:
        msg = "This AeroStator account is disabled; contact the service operator."
    titles = {"authentication_required": "Authentication required",
              "invalid_token": "Invalid or expired credentials",
              "invalid_request": "Invalid request",
              "insufficient_scope": "Insufficient scope",
              "account_disabled": "Account disabled"}
    data: Dict[str, Any] = {
        "error": error,
        "required_action": required_action,
        "required_scope": required_scope or None,
        "tool": tool or None,
        "method": method or None,
        "reason": reason or None,
        "sign_in_url": L["sign_in_url"],
        "sign_up_url": L["sign_up_url"],
        "authorize_url": L["authorize_url"],
        "resource_metadata_url": L["resource_metadata_url"],
        "docs_url": L["docs_url"],
        "user_message": msg,
    }
    return {"code": AUTH_ERROR_CODE, "message": titles.get(error, "Not authorized"),
            "data": data}


# ── plain-HTTP discovery: GET /mcp, /.well-known/mcp.json, /llms.txt ────────
#
# What a web-fetch tool or a person sees before speaking MCP.  Built from the
# same tool registry as tools/list (the caller passes the public tools'
# name + description as the SDK lists them), never hand-written.

CARD_MAX_AGE_S = 300


def protocol_versions() -> List[str]:
    try:
        from mcp_types.version import (HANDSHAKE_PROTOCOL_VERSIONS,
                                       MODERN_PROTOCOL_VERSIONS)
        return [*HANDSHAKE_PROTOCOL_VERSIONS, *MODERN_PROTOCOL_VERSIONS]
    except Exception:                                   # noqa: BLE001
        return ["2025-06-18", "2025-11-25"]


def _first_sentence(text: str) -> str:
    t = " ".join((text or "").split())
    for p in ("PUBLIC (no sign-in). ",):
        t = t[len(p):] if t.startswith(p) else t
    cut = t.find(". ")
    return t if cut < 0 else t[:cut + 1]


def public_tool_rows(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """``tools``: [{name, description}] as the SDK lists them."""
    return [{"name": t["name"], "description": _first_sentence(t.get("description") or ""),
             "requires_auth": False}
            for t in sorted(tools, key=lambda t: t["name"]) if t["name"] in PUBLIC_TOOLS]


def service_card(tools: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The JSON answer of an anonymous plain ``GET /mcp``."""
    L = links()
    ep = L["mcp_endpoint"]
    versions = protocol_versions()
    init_version = next((v for v in reversed(versions) if v <= "2025-11-25"), versions[-1])
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream"}
    return {
        "service": SERVICE_NAME,
        "operator": OPERATOR,
        "summary": describe_service()["summary"],
        "this_is": ("an MCP server (Model Context Protocol). Talk to it with an "
                    "MCP client, or POST JSON-RPC 2.0 to this URL. A plain GET "
                    "only returns this card."),
        "mcp": {
            "endpoint": ep,
            "transport": "streamable-http",
            "stateless": True,
            "method": "POST",
            "content_type": "application/json",
            "request_headers": headers,
            "json_rpc": "2.0",
            "protocol_versions": versions,
            "sse_get_stream": False,
        },
        "public_tools": public_tool_rows(tools),
        "public_resources": sorted(PUBLIC_RESOURCES),
        "try_it": {
            "initialize": {"method": "POST", "url": ep, "headers": headers, "body": {
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": init_version, "capabilities": {},
                           "clientInfo": {"name": "my-agent", "version": "1.0"}}}},
            "tools_list": {"method": "POST", "url": ep, "headers": headers, "body": {
                "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}},
            "curl": (f"curl -s {ep} -H 'Content-Type: application/json' "
                     "-H 'Accept: application/json, text/event-stream' "
                     "-d '{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/list\",\"params\":{}}'"),
        },
        "authentication": {
            "anonymous": ("the public tools above work without sign-in; "
                          "machines, drafts and simulations need OAuth 2.1 "
                          "(or an agent key)"),
            "resource_metadata_url": L["resource_metadata_url"],
            "authorization_server_metadata_url": L["authorization_server_metadata_url"],
            "sign_in_url": L["sign_in_url"],
            "sign_up_url": L["sign_up_url"],
            "scopes": list(_keys.SCOPES),
        },
        "connect": {
            "claude_ai": ("Settings -> Connectors -> Add custom connector: name "
                          f"AeroStator, URL {ep}; then Connect"),
            "chatgpt": ("Settings -> Apps & Connectors -> Create (developer mode): "
                        f"MCP server URL {ep}, authentication OAuth"),
            "claude_code": f"claude mcp add --transport http aerostator {ep}",
        },
        "server_card_url": _oauth.base_url() + "/.well-known/mcp.json",
        "llms_txt_url": _oauth.base_url() + "/llms.txt",
        "docs_url": L["docs_url"],
        "license": LICENSE,
        "source_code_url": L["source_code_url"],
    }


def service_card_html(card: Dict[str, Any]) -> str:
    """The same card for a browser (every value HTML-escaped)."""
    import html
    import json as _json
    e = html.escape
    tools = "".join(f"<li><code>{e(t['name'])}</code> — {e(t['description'])}</li>"
                    for t in card["public_tools"])
    a = card["authentication"]
    c = card["connect"]
    body = _json.dumps(card["try_it"]["tools_list"]["body"])
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{e(card['service'])} MCP server</title>"
        "<style>body{font:15px/1.5 system-ui,sans-serif;max-width:46rem;margin:2rem auto;"
        "padding:0 1rem}code,pre{background:#f3f3f3;padding:.1rem .3rem;border-radius:3px}"
        "pre{padding:.6rem;overflow-x:auto}</style></head><body>"
        f"<h1>{e(card['service'])} — MCP server</h1>"
        f"<p>{e(card['summary'])}</p>"
        f"<p>{e(card['this_is'])}</p>"
        f"<p>Endpoint: <code>{e(card['mcp']['endpoint'])}</code> · transport "
        f"<code>streamable-http</code> (stateless, JSON responses) · protocol versions "
        f"{e(', '.join(card['mcp']['protocol_versions']))}</p>"
        f"<h2>Public tools (no sign-in)</h2><ul>{tools}</ul>"
        f"<h2>Try it</h2><pre>{e(card['try_it']['curl'])}</pre>"
        f"<p>Request body for <code>tools/list</code>: <code>{e(body)}</code></p>"
        "<h2>Sign in</h2>"
        f"<p>{e(a['anonymous'])}. OAuth metadata: <a href=\"{e(a['resource_metadata_url'])}\">"
        f"protected resource</a>, <a href=\"{e(a['authorization_server_metadata_url'])}\">"
        f"authorization server</a>. <a href=\"{e(a['sign_up_url'])}\">Create an account</a> · "
        f"<a href=\"{e(a['sign_in_url'])}\">Sign in</a></p>"
        "<h2>Add it to your AI app</h2><ul>"
        f"<li>Claude (claude.ai / Desktop): {e(c['claude_ai'])}</li>"
        f"<li>ChatGPT: {e(c['chatgpt'])}</li>"
        f"<li>Claude Code: <code>{e(c['claude_code'])}</code></li></ul>"
        f"<p><a href=\"{e(card['server_card_url'])}\">Server card (JSON)</a> · "
        f"<a href=\"{e(card['docs_url'])}\">Documentation</a> · "
        f"<a href=\"{e(card['source_code_url'])}\">Source ({e(card['license'])})</a></p>"
        "</body></html>")


def server_card(tools: List[Dict[str, Any]]) -> Dict[str, Any]:
    """``/.well-known/mcp.json``.  The MCP server-card proposal is not
    settled (SEP-1649 superseded by SEP-2127, still in review): this is a
    minimal card in the server.json-derived shape SEP-2127 drafts
    (name / title / description / version / websiteUrl / repository /
    remotes), plus the fields a client needs before connecting (protocol
    versions, auth metadata, the public tools).  docs/MCP_DISCOVERY.md."""
    L = links()
    return {
        "name": "com.aerostator/mcp",
        "title": SERVICE_NAME,
        "description": describe_service()["summary"],
        "version": "stage3-discovery",
        "websiteUrl": L["website"],
        "repository": {"url": L["source_code_url"], "source": "github"},
        "license": LICENSE,
        "remotes": [{"type": "streamable-http", "url": L["mcp_endpoint"]}],
        "protocolVersions": protocol_versions(),
        "capabilities": {"tools": {}, "resources": {}},
        "authentication": {
            "type": "oauth2",
            "required": "for user data and simulations; the public tools work without it",
            "protectedResourceMetadata": L["resource_metadata_url"],
            "authorizationServerMetadata": L["authorization_server_metadata_url"],
            "scopes": list(_keys.SCOPES),
        },
        "tools": public_tool_rows(tools),
        "documentationUrl": L["docs_url"],
        "_meta": {"com.aerostator/card": {
            "format": "minimal server card; the MCP server-card proposal (SEP-2127) is not final",
            "serviceCard": L["mcp_endpoint"],
            "llmsTxt": _oauth.base_url() + "/llms.txt",
            "signUp": L["sign_up_url"]}},
    }


def llms_txt(tools: List[Dict[str, Any]]) -> str:
    """``/llms.txt`` (llmstxt.org shape: H1, summary blockquote, link lists)."""
    L = links()
    ep = L["mcp_endpoint"]
    rows = "\n".join(f"- `{t['name']}`: {t['description']}" for t in public_tool_rows(tools))
    return (
        f"# {SERVICE_NAME}\n\n"
        f"> {describe_service()['summary']}\n\n"
        f"{SERVICE_NAME} is run by {OPERATOR}; the code is open source ({LICENSE}). "
        "AI agents use it through an MCP server (Model Context Protocol, "
        "streamable HTTP, stateless JSON). Without sign-in an agent can call the "
        "public discovery tools below; machines, draft designs and FEM "
        "simulations need an OAuth sign-in by the user.\n\n"
        "## MCP\n\n"
        f"- [MCP endpoint]({ep}): POST JSON-RPC 2.0 (`initialize`, `tools/list`, "
        "`tools/call`); a plain GET returns the service card\n"
        f"- [Server card]({_oauth.base_url()}/.well-known/mcp.json): machine-readable description\n"
        f"- [OAuth protected-resource metadata]({L['resource_metadata_url']})\n"
        f"- Add to Claude Code: `claude mcp add --transport http aerostator {ep}`\n\n"
        "## Public tools (no sign-in)\n\n"
        f"{rows}\n\n"
        "## Docs\n\n"
        f"- [MCP discovery and auth]({L['docs_url']})\n"
        f"- [Source code]({L['source_code_url']})\n"
        f"- [Create an account]({L['sign_up_url']})\n")
