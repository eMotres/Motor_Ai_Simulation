"""The MCP server at ``/mcp`` (Stage 1 read-only, Stage 2 OAuth, Stage 3 drafts
+ simulations through the job queue).  docs/MCP_2026-09-28.md.

Official MCP Python SDK (``mcp``), streamable-HTTP transport, STATELESS with
plain JSON responses: every POST is one self-contained JSON-RPC exchange, so
nginx needs no SSE/buffering settings and any API worker can answer.

It lives INSIDE the FastAPI process (same stores, same permission code) but in
front of the app's own middleware: ``McpGate`` is the OUTERMOST ASGI layer and
takes ``/mcp`` before the tier gate / workspace resolver / CORS ever see it —
those speak session tokens, and an agent key is not one.  McpGate does, per
request:

1. auth   — ``Bearer emk_…`` key or ``Bearer emo_…`` OAuth token.
            NO Authorization header = the ANONYMOUS tier (docs/MCP_DISCOVERY.md):
            a fail-closed allow-list of methods / public discovery tools /
            public resources; anything else -> 401 + WWW-Authenticate +
            a structured JSON-RPC error.  A token that is presented but
            invalid, expired or revoked -> 401 (reauthenticate), always.
2. scope  — the tool's scope must be on the key            -> 403 insufficient_scope
3. fair use — per key per minute / per day, ``tools/call`` only;
            anonymous: per client IP, every request          -> 429 + Retry-After
4. audit  — one line per tool call (user, key, tool, args summary, status)

then hands the request to the SDK with the ``Principal`` in the ASGI scope
(absent for the anonymous tier).  ``tools/list`` and ``resources/list`` are
filtered by the same tables in the SDK handlers (``_GatedServer``).
"""
import json
from contextlib import asynccontextmanager
from typing import Annotated, Any, Dict, List, Optional

from mcp.server.mcpserver import Context, MCPServer
from mcp_types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from motor_ai_sim import agent_keys as _keys
from motor_ai_sim import mcp_discovery as _d
from motor_ai_sim import mcp_tools as _t
from motor_ai_sim import oauth as _oauth

MCP_PATH = "/mcp"
SCOPE_KEY = "emotres.mcp_principal"

#: Tools callable WITHOUT authentication (read-only service description).
PUBLIC_TOOLS = _d.PUBLIC_TOOLS

#: tool -> the scope it needs.  A tool in neither this table nor PUBLIC_TOOLS
#: is refused for everybody (fail closed).
TOOL_SCOPES: Dict[str, str] = {
    "list_catalog": "catalog:read",
    "get_catalog_entry": "catalog:read",
    "list_machines": "machines:read",
    "get_machine_performance": "machines:read",
    "check_fit": "machines:read",
    # Stage 3 — drafts and simulations (docs/MCP_2026-09-28.md "Stage 3")
    "start_design": "designs:write",
    "get_design": "machines:read",
    "simulate": "simulate",
    "get_job": "simulate",
    "get_design_result": "machines:read",
    "open_in_configure": "machines:read",
}

#: Stage-3 tools that change something (a draft, a queued job) — every other
#: tool carries ``readOnlyHint: true``.
WRITE_TOOLS = frozenset({"start_design", "simulate"})

_server = None
_http_app = None


def _principal_in(request) -> Optional[_keys.Principal]:
    p = (getattr(request, "scope", None) or {}).get(SCOPE_KEY)
    return p if isinstance(p, _keys.Principal) else None


def _principal_from(ctx) -> _keys.Principal:
    p = _principal_in(getattr(ctx.request_context, "request", None))
    if p is None:          # McpGate sets it for every authenticated request
        raise PermissionError("unauthenticated")
    return p


def visible_tools(principal: Optional[_keys.Principal]) -> frozenset:
    """What ``tools/list`` shows: the public tools, plus — once signed in —
    every tool the principal's scopes allow."""
    if principal is None:
        return PUBLIC_TOOLS
    return PUBLIC_TOOLS | {t for t, s in TOOL_SCOPES.items() if principal.has(s)}


def _security_schemes(name: str) -> List[Dict[str, Any]]:
    """Per-tool auth declaration (OpenAI Apps SDK ``securitySchemes``, mirrored
    in ``_meta`` because the SDK's Tool model has no top-level field for it)."""
    if name in PUBLIC_TOOLS:
        return [{"type": "noauth"}]
    return [{"type": "oauth2", "scopes": [TOOL_SCOPES[name]]}]


class _GatedServer(MCPServer):
    """MCPServer whose list handlers follow the gate's tables per request."""

    async def _handle_list_tools(self, ctx, params):
        res = await super()._handle_list_tools(ctx, params)
        allowed = visible_tools(_principal_in(getattr(ctx, "request", None)))
        tools = []
        for t in res.tools:
            if t.name not in allowed:
                continue
            meta = dict(t.meta or {})
            meta["securitySchemes"] = _security_schemes(t.name)
            tools.append(t.model_copy(update={"meta": meta}))
        return res.model_copy(update={"tools": tools})

    async def _handle_list_resources(self, ctx, params):
        res = await super()._handle_list_resources(ctx, params)
        if _principal_in(getattr(ctx, "request", None)) is not None:
            return res
        return res.model_copy(update={"resources": [
            r for r in res.resources if str(r.uri) in _d.PUBLIC_RESOURCES]})

    async def _handle_list_resource_templates(self, ctx, params):
        res = await super()._handle_list_resource_templates(ctx, params)
        if _principal_in(getattr(ctx, "request", None)) is not None:
            return res
        return res.model_copy(update={"resource_templates": [
            t for t in res.resource_templates
            if str(t.uri_template) in _d.PUBLIC_RESOURCE_TEMPLATES]})

    async def _handle_list_prompts(self, ctx, params):
        res = await super()._handle_list_prompts(ctx, params)
        if _principal_in(getattr(ctx, "request", None)) is not None:
            return res
        return res.model_copy(update={"prompts": [
            p for p in res.prompts if p.name in _d.PUBLIC_PROMPTS]})


def _run(fn, ctx, *args):
    """Call a tool body as the key's owner; a caller mistake becomes a tool
    error the agent can read (anything else stays a generic crash)."""
    from mcp.server.mcpserver.exceptions import ToolError as _SdkToolError
    from motor_ai_sim import agent_designs as _ad
    try:
        return fn(_principal_from(ctx), *args)
    except (_t.ToolError, _ad.DesignError) as e:
        raise _SdkToolError(str(e)) from e
    except _ad.QuotaExceeded as e:
        raise _SdkToolError(f"{e}; retry after {e.retry_after} s") from e


INSTRUCTIONS = (
    "AeroStator (aerostator.com): permanent-magnet motor / generator catalog, "
    "saved FEM results, draft designs from requirements and 2-D FEM "
    "simulations on the user's own queue. Without sign-in only the public "
    "tools work: describe_service, list_capabilities, list_calculation_types, "
    "get_input_requirements, how_to_authenticate, start_sign_up. Everything "
    "that touches the user's machines, drafts, jobs or simulations needs an "
    "OAuth sign-in: when an answer says authentication_required, relay its "
    "data.user_message and let the user click Connect / Sign in in this app "
    "(the page also offers Create account). Never ask for a password in chat. "
    "Signed in: start with check_fit, or start_design when nothing fits; if "
    "an answer says needs_input, ask the engineer. Read emotres://guide.")


def _register_public(srv) -> None:
    """The anonymous tier: service description only (mcp_discovery)."""
    from mcp.server.mcpserver.exceptions import ToolError as _SdkToolError
    pub = ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                          idempotentHint=True, openWorldHint=False)

    @srv.tool(annotations=pub, description=(
        "PUBLIC (no sign-in). What AeroStator is: summary, operator, licence "
        "(AGPL-3.0-or-later), website, MCP endpoint, terms, privacy and docs links."))
    def describe_service() -> Dict[str, Any]:
        return _d.describe_service()

    @srv.tool(annotations=pub, description=(
        "PUBLIC (no sign-in). The full tool catalogue: name, one-line purpose, "
        "required scope, whether it needs sign-in, read-only or not; and what "
        "each scope unlocks."))
    def list_capabilities() -> Dict[str, Any]:
        return _d.capabilities()

    @srv.tool(annotations=pub, description=(
        "PUBLIC (no sign-in). The calculation types AeroStator supports (fit "
        "check, saved performance, design from requirements, 2-D FEM EM "
        "transient, thermal, coupled EM-thermal, catalog; web-only: efficiency "
        "map, mechanics, inverter losses, 3-D end effect, optimization) and "
        "the tools that run them."))
    def list_calculation_types() -> Dict[str, Any]:
        return _d.calculation_types()

    @srv.tool(annotations=pub, description=(
        "PUBLIC (no sign-in). Required and optional inputs of one calculation "
        "type with UCUM units and valid ranges, taken from the validators. "
        "calculation_type: an id from list_calculation_types."))
    def get_input_requirements(
        calculation_type: Annotated[str, Field(description="id from list_calculation_types, e.g. design_from_requirements")],
    ) -> Dict[str, Any]:
        try:
            return _d.input_requirements(calculation_type)
        except ValueError as e:
            raise _SdkToolError(str(e)) from e

    @srv.tool(annotations=pub, description=(
        "PUBLIC (no sign-in). How to sign in: OAuth metadata and endpoint URLs, "
        "scopes and what each unlocks, the sign-up URL and the API-key "
        "alternative, plus a user_message to relay."))
    def how_to_authenticate() -> Dict[str, Any]:
        return _d.how_to_authenticate()

    @srv.tool(annotations=pub, description=(
        "PUBLIC (no sign-in). For a user with no AeroStator account: the "
        "sign-up page to open in a browser and a user_message to relay. It "
        "creates nothing; the password is typed only on the AeroStator page, "
        "never in chat. Easiest path: Connect / Sign in in the AI app, then "
        "'Create account' on the page that opens."))
    def start_sign_up() -> Dict[str, Any]:
        return _d.start_sign_up()


def build_server():
    """The MCPServer: public discovery tools, Stage-1..3 tools, the guide."""
    srv = _GatedServer(
        name="emotres",
        title="AeroStator",
        instructions=INSTRUCTIONS,
        version="stage3-discovery",
    )
    _register_public(srv)
    ro = ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                         idempotentHint=True, openWorldHint=False)

    @srv.tool(annotations=ro, description=(
        "List catalog entries of one kind with key specs and provenance. "
        "kind: magnets | steels | wires | bearings | devices | dies. "
        "Optional free-text query filters by id/description/grade/maker."))
    def list_catalog(
        kind: Annotated[str, Field(description="magnets | steels | wires | bearings | devices | dies")],
        ctx: Context,
        query: Annotated[Optional[str], Field(description="case-insensitive substring filter")] = None,
    ) -> Dict[str, Any]:
        return _run(_t.list_catalog, ctx, kind, query)

    @srv.tool(annotations=ro, description=(
        "One catalog entry by kind and id (the id field from list_catalog)."))
    def get_catalog_entry(
        kind: Annotated[str, Field(description="magnets | steels | wires | bearings | devices | dies")],
        id: Annotated[str, Field(description="entry id exactly as list_catalog returns it")],
        ctx: Context,
    ) -> Dict[str, Any]:
        return _run(_t.get_catalog_entry, ctx, kind, id)

    @srv.tool(annotations=ro, description=(
        "Machines this key's owner may see (own, published and granted catalog "
        "machines): die / config / duty names with headline ratings — torque_nm, "
        "power_kw, speed_rpm, efficiency_pct, line_voltage_peak_v, mass_kg, "
        "outer_diameter_mm, active_length_mm — taken from saved duty results."))
    def list_machines(ctx: Context) -> Dict[str, Any]:
        return _run(_t.list_machines, ctx)

    @srv.tool(annotations=ro, description=(
        "Saved results of one duty of one machine: operating point, efficiency, "
        "loss split (W), temperatures (degC), continuous rating, KV, ripple. "
        "Public-datasheet data only. Names come from list_machines."))
    def get_machine_performance(
        die: Annotated[str, Field(description="die name from list_machines")],
        config: Annotated[str, Field(description="configuration name")],
        duty: Annotated[str, Field(description="duty (operating point) name")],
        ctx: Context,
    ) -> Dict[str, Any]:
        return _run(_t.get_machine_performance, ctx, die, config, duty)

    @srv.tool(annotations=ro, description=(
        "Rank existing machines against requirements. A saved duty fits when it "
        "gives >= torque_nm at >= speed_rpm, its peak line voltage <= voltage_v "
        "(DC bus), and outer diameter / active length / mass are within limits. "
        "Returns fits with margins (percent) and rejected machines with reasons. "
        "Nothing is simulated."))
    def check_fit(
        torque_nm: Annotated[float, Field(gt=0, description="required shaft torque, N*m")],
        speed_rpm: Annotated[float, Field(gt=0, description="speed at which that torque is needed, rpm")],
        ctx: Context,
        voltage_v: Annotated[Optional[float], Field(gt=0, description="available DC bus voltage, V")] = None,
        max_diameter_mm: Annotated[Optional[float], Field(gt=0, description="max outer (stator) diameter, mm")] = None,
        max_length_mm: Annotated[Optional[float], Field(gt=0, description="max active (stack) length, mm")] = None,
        max_mass_kg: Annotated[Optional[float], Field(gt=0, description="max active mass, kg")] = None,
        cooling: Annotated[Optional[str], Field(description="required cooling, e.g. air | robotics | liquid")] = None,
        limit: Annotated[int, Field(ge=1, le=50, description="max rows per list")] = 10,
    ) -> Dict[str, Any]:
        return _run(_t.check_fit, ctx, torque_nm, speed_rpm,
                            voltage_v, max_diameter_mm, max_length_mm,
                            max_mass_kg, cooling, limit)

    _register_stage3(srv)

    @srv.resource("emotres://guide", name="guide", title="How to use eMotres MCP",
                  mime_type="text/markdown")
    def guide() -> str:
        return _t.GUIDE

    return srv


class Requirements(BaseModel):
    """What the engineer asked for.  Leave out what he did not say — the tool
    answers ``needs_input`` and you ask him; never invent a value."""
    model_config = ConfigDict(extra="forbid")
    torque_nm: Optional[float] = Field(None, gt=0, description="shaft torque at rated speed, N*m")
    power_kw: Optional[float] = Field(None, gt=0, description="shaft power at rated speed, kW (alternative to torque)")
    speed_rpm: Optional[float] = Field(None, gt=0, description="rated speed, rpm")
    max_speed_rpm: Optional[float] = Field(None, gt=0, description="maximum speed, rpm (default = rated)")
    dc_bus_v: Optional[float] = Field(None, gt=0, description="DC bus voltage of the inverter, V")
    max_outer_diameter_mm: Optional[float] = Field(None, gt=0, description="max stator outer diameter, mm")
    max_length_mm: Optional[float] = Field(None, gt=0, description="max active (stack) length, mm")
    max_mass_kg: Optional[float] = Field(None, gt=0, description="max active mass, kg")
    cooling: Optional[str] = Field(None, description="air | liquid | robotics")
    duty: Optional[str] = Field(None, description="S1 (continuous) | S2 | S3 | peak")
    ambient_c: Optional[float] = Field(None, description="ambient / coolant temperature, degC (default 40)")
    mode: Optional[str] = Field(None, description="motor | generator (default motor)")
    application: Optional[str] = Field(None, description="free-text application notes")


class BaseMachine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    die: str = Field(description="die name from list_machines")
    config: str = Field(description="configuration name")
    duty: Optional[str] = Field(None, description="duty name (optional)")


_CONVERSATION = (
    " CONVERSATIONAL PATTERN: when the answer has status 'needs_input', do NOT "
    "guess — ask the engineer each listed field (the 'why' and the 'options' / "
    "'range' are written to be read to him), then call again with the complete "
    "requirements.  status 'no_fit' lists what blocked it: ask which limit may move.")


def _register_stage3(srv) -> None:
    from motor_ai_sim import agent_designs as _ad
    ro = ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                         idempotentHint=True, openWorldHint=False)
    wr = ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                         idempotentHint=False, openWorldHint=False)

    @srv.tool(annotations=wr, description=(
        "Start a DRAFT motor design from requirements (scope designs:write). "
        "Validates loudly: missing or contradictory essentials (torque or power, "
        "rated speed, DC bus voltage, cooling, duty) come back as "
        "status='needs_input' with needs_input=[{field, why, options|range}]." +
        _CONVERSATION + " Otherwise picks the nearest EXISTING machine you may "
        "use (no new laminations), scales stack length / turns / parallel paths "
        "within valid ranges, explains why, and creates a draft in the user's "
        "workspace marked as yours. Nothing saved or open is changed. Returns "
        "design_id; next: simulate(design_id, 'em')."))
    def start_design(
        requirements: Annotated[Requirements, Field(description="the engineer's requirements")],
        ctx: Context,
        base: Annotated[Optional[BaseMachine], Field(description="force this starting machine (explicit addressing)")] = None,
        name: Annotated[Optional[str], Field(description="draft name")] = None,
    ) -> Dict[str, Any]:
        return _run(_ad.start_design, ctx, requirements.model_dump(exclude_none=True),
                    base.model_dump(exclude_none=True) if base else None, name)

    @srv.tool(annotations=ro, description=(
        "One draft by design_id: requirements, starting point and why, scaled "
        "parameters, the analytical estimate, runs and warnings."))
    def get_design(design_id: Annotated[str, Field(description="d-… from start_design")],
                   ctx: Context) -> Dict[str, Any]:
        return _run(_ad.get_design, ctx, design_id)

    @srv.tool(annotations=wr, description=(
        "Queue a FEM simulation of a draft on the user's own job queue (scope "
        "simulate; daily fair-use limit, 429 when used up). what: em (electromagnetic "
        "transient) | thermal (one EM pass + one thermal solve) | coupled (EM <-> "
        "thermal loop to steady temperatures). steps: time steps per electrical "
        "period (optional). Returns job_id at once; the solve takes minutes — "
        "poll get_job. Runs in the draft's own sandbox: the user's open machine "
        "is never touched and his own runs keep their place in the queue."))
    def simulate(design_id: Annotated[str, Field(description="d-… from start_design")],
                 what: Annotated[str, Field(description="em | thermal | coupled")],
                 ctx: Context,
                 steps: Annotated[Optional[int], Field(ge=12, le=360, description="steps per electrical period")] = None,
                 ) -> Dict[str, Any]:
        return _run(_ad.simulate, ctx, design_id, what, steps)

    @srv.tool(annotations=ro, description=(
        "Status of a queued job: state (queued | running | done | failed | "
        "cancelled | interrupted), queue position, progress %, ETA, elapsed "
        "time and the error text — what the web's progress bar shows."))
    def get_job(job_id: Annotated[str, Field(description="job_id from simulate")],
                ctx: Context) -> Dict[str, Any]:
        return _run(_ad.get_job, ctx, job_id)

    @srv.tool(annotations=ro, description=(
        "Headline results of a draft's finished runs: torque, power, efficiency "
        "at the shaft, loss split (W), temperatures (degC), limits, mass, and "
        "whether the requirements are met. Public-datasheet fields only."))
    def get_design_result(design_id: Annotated[str, Field(description="d-… from start_design")],
                          ctx: Context) -> Dict[str, Any]:
        return _run(_ad.get_design_result, ctx, design_id)

    @srv.tool(annotations=ro, description=(
        "The aerostator.com link that opens the draft in Configure, where the "
        "engineer tunes length / turns / current / speed interactively. Give it "
        "to him; his open machine is replaced only if he clicks Open there."))
    def open_in_configure(design_id: Annotated[str, Field(description="d-… from start_design")],
                          ctx: Context) -> Dict[str, Any]:
        return _run(_ad.open_in_configure, ctx, design_id)


def get_server():
    global _server, _http_app
    if _server is None:
        from mcp.server.transport_security import TransportSecuritySettings
        _server = build_server()
        # DNS-rebinding protection guards credential-less localhost servers
        # from browsers; ours sits behind nginx and demands a bearer key.
        _http_app = _server.streamable_http_app(
            streamable_http_path=MCP_PATH, stateless_http=True,
            json_response=True,
            transport_security=TransportSecuritySettings(
                enable_dns_rebinding_protection=False))
    return _server


def http_app():
    get_server()
    return _http_app


@asynccontextmanager
async def lifespan():
    """Run the SDK's session manager for the life of the API process.

    A session manager runs once per instance, so every lifespan builds a
    fresh server (a second app start in one process — tests — needs it)."""
    global _server, _http_app
    _server = _http_app = None
    # resolve the trusted proxy hostnames (compose service ``web``) once at
    # startup, so the first request does not wait on DNS (client_ip.py)
    import asyncio
    from motor_ai_sim import client_ip as _cip
    await asyncio.to_thread(_cip.warm)
    srv = get_server()
    async with srv.session_manager.run():
        yield


# ── the gate ─────────────────────────────────────────────────────────────────

async def _send_json(send, status: int, body: dict, headers=()) -> None:
    raw = json.dumps(body).encode("utf-8")
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(raw)).encode()),
                            (b"cache-control", b"no-store"),
                            *[(k.encode(), v.encode()) for k, v in headers]]})
    await send({"type": "http.response.body", "body": raw})


def _rpc_error(msg_id, code: int, text: str, data: Optional[dict] = None) -> dict:
    err: Dict[str, Any] = {"code": code, "message": text}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": msg_id, "error": err}


def verify_any(authorization):
    """An agent key (``emk_``) or an OAuth access token (``emo_``) -> Principal."""
    if isinstance(authorization, str):
        parts = authorization.strip().split(" ", 1)
        if (len(parts) == 2 and parts[0].lower() == "bearer"
                and parts[1].strip().startswith(_oauth.ACCESS_PREFIX)):
            return _oauth.verify_access(parts[1].strip())
    return _keys.verify(authorization)


def _client_ip(scope) -> str:
    """The visitor's IP; forwarding headers only from a trusted proxy
    (``motor_ai_sim.client_ip``, env TRUSTED_PROXIES)."""
    from motor_ai_sim import client_ip as _cip
    return _cip.from_scope(scope) or "?"


def _scope_set(*scopes) -> str:
    """Scopes in the canonical SCOPES order (a stable WWW-Authenticate)."""
    want = {s for s in scopes if s}
    return " ".join(s for s in _keys.SCOPES if s in want)


def _account_hint(m: dict) -> str:
    """``params._meta["aerostator/account"]`` — a client MAY say the user has
    no account yet ("none"); it only changes the advice, never access."""
    meta = ((m.get("params") or {}) if isinstance(m.get("params"), dict) else {}).get("_meta")
    v = meta.get("aerostator/account") if isinstance(meta, dict) else None
    return str(v or "").strip().lower()


def _arg_names(args: Any) -> Dict[str, Any]:
    """What the audit keeps of an anonymous or REFUSED call's arguments: the
    argument names only, never a value (a password pasted into a public tool
    must not land in mcp_audit.jsonl)."""
    if isinstance(args, dict):
        return {"arg_names": sorted(str(k)[:64] for k in args)[:50]}
    return {"arg_type": type(args).__name__} if args is not None else {}


def _auth_body(msg_id, err: dict, reason: str) -> dict:
    # ``reason`` stays top-level for existing callers (Stage 1 contract)
    return {**_rpc_error(msg_id, err["code"], err["message"], err["data"]),
            "reason": reason}


class McpGate:
    """Outermost ASGI middleware: ``/mcp`` -> auth/scope/fair-use/audit -> SDK."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("path", "").rstrip("/") != MCP_PATH:
            await self.app(scope, receive, send)
            return
        raw_headers = [(k.decode("latin-1").lower(), v.decode("latin-1"))
                       for k, v in scope.get("headers") or ()]
        authz_values = [v for k, v in raw_headers if k == "authorization"]
        # Anonymous = the header is ABSENT.  A presented header is a credential
        # even when empty (-> 401 invalid_token), and two of them are a
        # malformed request (-> 400), never a fall-back to the anonymous tier.
        anonymous = not authz_values
        if len(authz_values) > 1:
            _keys.audit(principal=None, method="auth", status=400,
                        note="duplicate Authorization headers")
            err = _d.auth_error(error="invalid_request", required_action="reauthenticate",
                                reason="duplicate_authorization")
            err["message"] = "Invalid request: more than one Authorization header"
            err["data"]["user_message"] = ("The AI app sent two Authorization headers; "
                                           "reconnect AeroStator in this app.")
            await _send_json(send, 400, _auth_body(None, err, "duplicate_authorization"),
                             [("www-authenticate", _oauth.www_authenticate(
                                 "invalid_request",
                                 description="send exactly one Authorization header"))])
            return
        authz = authz_values[0] if authz_values else None

        body = b""
        if scope.get("method") == "POST":
            more = True
            while more:
                msg = await receive()
                body += msg.get("body", b"")
                more = msg.get("more_body", False)
                if len(body) > 1_000_000:
                    await _send_json(send, 413, {"error": "request too large"})
                    return
        try:
            parsed = json.loads(body) if body else None
        except ValueError:
            parsed = None
        msgs = parsed if isinstance(parsed, list) else [parsed] if parsed else []

        if anonymous:
            await self._anonymous(scope, receive, send, body, parsed, msgs)
            return

        principal, reason = verify_any(authz)
        if principal is None:
            if not (authz or "").strip():
                reason = "empty_authorization"
            await self._bad_credentials(send, msgs, reason)
            return

        for m in msgs:
            if not isinstance(m, dict) or m.get("method") != "tools/call":
                continue
            params = m.get("params") or {}
            tool = str(params.get("name") or "")
            args = params.get("arguments")
            need = TOOL_SCOPES.get(tool)
            if tool not in PUBLIC_TOOLS and need is None:
                _keys.audit(principal=principal, method="tools/call", tool=tool,
                            args=_arg_names(args), status=403, note="unknown tool")
                await _send_json(send, 403, _rpc_error(
                    m.get("id"), -32001, f"unknown tool '{tool}'",
                    {"error": "unknown_tool", "tool": tool or None,
                     "required_action": None,
                     "user_message": f"AeroStator has no tool named '{tool}'."}))
                return
            if need is not None and not principal.has(need):
                _keys.audit(principal=principal, method="tools/call", tool=tool,
                            args=_arg_names(args), status=403, note=f"needs {need}")
                err = _d.auth_error(error="insufficient_scope",
                                    required_action="grant_scope", tool=tool,
                                    method="tools/call", required_scope=need,
                                    principal_kind=principal.kind)
                err["data"]["granted_scopes"] = list(principal.scopes)
                # Recommended approach (spec "Scope Challenge Handling"): ask
                # for what is held PLUS what is missing, so a step-up does not
                # drop permissions the client already has.
                want = _scope_set(*principal.scopes, need)
                await _send_json(send, 403, _rpc_error(
                    m.get("id"), err["code"],
                    f"Insufficient scope: this credential lacks '{need}'", err["data"]),
                    [("www-authenticate", _oauth.www_authenticate(
                        "insufficient_scope", scope=want,
                        description=f"'{tool}' needs scope {need}"))])
                return
            ok, retry = _keys.take_quota(principal.credential_id)
            if not ok:
                _keys.audit(principal=principal, method="tools/call", tool=tool,
                            args=_arg_names(args), status=429)
                await _send_json(send, 429, _rpc_error(
                    m.get("id"), -32029, f"rate limit; retry after {retry} s",
                    {"error": "rate_limited", "retry_after_s": retry}),
                    [("retry-after", str(retry))])
                return
            if tool == "simulate":
                # The DAILY simulation fair-use limit (Stage 3), counted from the job
                # queue's own records of this account's agent runs.
                from motor_ai_sim import agent_designs as _ad
                ok, retry, used, lim = _ad.check_quota(principal)
                if not ok:
                    _keys.audit(principal=principal, method="tools/call", tool=tool,
                                args=_arg_names(args), status=429, note=f"simulate fair-use limit {used}/{lim}")
                    await _send_json(send, 429, _rpc_error(
                        m.get("id"), -32029,
                        f"daily simulation fair-use limit reached ({used}/{lim}); "
                        f"retry after {retry} s",
                        {"error": "rate_limited", "retry_after_s": retry,
                         "used": used, "limit": lim}), [("retry-after", str(retry))])
                    return
            _keys.audit(principal=principal, method="tools/call", tool=tool,
                        args=args, status=200)

        await self._forward(scope, receive, send, body, principal)

    # -- helpers --------------------------------------------------------------

    async def _forward(self, scope, receive, send, body: bytes,
                       principal: Optional[_keys.Principal]) -> None:
        sent = False

        async def _replay():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        inner = dict(scope)
        inner["path"] = MCP_PATH
        inner["raw_path"] = MCP_PATH.encode()
        inner.pop(SCOPE_KEY, None)
        if principal is not None:
            inner[SCOPE_KEY] = principal
        await http_app()(inner, _replay, send)

    async def _bad_credentials(self, send, msgs, reason: str) -> None:
        """A token WAS presented and is not good: 401 (spec: invalid or
        expired tokens MUST receive 401), whatever the method."""
        _keys.audit(principal=None, method="auth", status=401, note=reason)
        first = next((m for m in msgs if isinstance(m, dict)), {})
        tool = str(((first.get("params") or {}) if isinstance(first.get("params"), dict)
                    else {}).get("name") or "") if first.get("method") == "tools/call" else ""
        if reason == "disabled":
            err = _d.auth_error(error="account_disabled", required_action="contact_support",
                                tool=tool, method=str(first.get("method") or ""),
                                reason=reason)
        else:
            err = _d.auth_error(error="invalid_token", required_action="reauthenticate",
                                tool=tool, method=str(first.get("method") or ""),
                                reason=reason)
        await _send_json(send, 401, _auth_body(first.get("id"), err, reason),
                         [("www-authenticate", _oauth.www_authenticate(
                             "invalid_token", scope=_scope_set(*_d.DEFAULT_SIGN_IN_SCOPES),
                             description="the access token is invalid, expired or revoked"))])

    async def _refuse_anonymous(self, send, m: dict, ip: str, *, tool: str = "",
                                need: str = "", args: Any = None) -> None:
        method = str(m.get("method") or "") if isinstance(m, dict) else ""
        _keys.audit(principal=None, method=method or "anonymous", tool=tool,
                    args=_arg_names(args), status=401, note="anonymous: authentication required",
                    ip=ip)
        sign_up = _d.wants_sign_up(ip) or (isinstance(m, dict) and _account_hint(m) == "none")
        err = _d.auth_error(error="authentication_required",
                            required_action="sign_up" if sign_up else "sign_in",
                            tool=tool, method=method, required_scope=need,
                            reason="no_token")
        await _send_json(send, 401, _auth_body(
            m.get("id") if isinstance(m, dict) else None, err, "no_token"),
            [("www-authenticate", _oauth.www_authenticate(
                scope=_scope_set(*_d.DEFAULT_SIGN_IN_SCOPES, need)))])

    async def _anonymous(self, scope, receive, send, body: bytes, parsed, msgs) -> None:
        """No Authorization header: the public tier, fail closed."""
        ip = _client_ip(scope)
        if scope.get("method") != "POST":
            # GET (SSE stream) / DELETE (session end): nothing public there
            await self._refuse_anonymous(send, {"method": scope.get("method")}, ip)
            return
        ok, retry = _keys.take_quota("anon:" + ip, per_min=_keys.anon_per_minute_limit(),
                                     per_day=_keys.anon_per_day_limit())
        if not ok:
            _keys.audit(principal=None, method="anonymous", status=429, ip=ip)
            first = next((m for m in msgs if isinstance(m, dict)), {})
            await _send_json(send, 429, _rpc_error(
                first.get("id"), -32029, f"anonymous rate limit; retry after {retry} s",
                {"error": "rate_limited", "retry_after_s": retry,
                 "required_action": "wait_or_sign_in",
                 "user_message": ("Too many requests without sign-in; wait a "
                                  "moment or connect AeroStator (Connect / Sign in).")}),
                [("retry-after", str(retry))])
            return
        if not msgs:
            await _send_json(send, 400, _rpc_error(None, -32700, "parse error"))
            return
        for m in msgs:
            if not isinstance(m, dict):
                await _send_json(send, 400, _rpc_error(None, -32600, "invalid request"))
                return
            method = m.get("method")
            if method not in _d.ANON_METHODS:
                await self._refuse_anonymous(send, m, ip)
                return
            params = m.get("params") if isinstance(m.get("params"), dict) else {}
            if method == "tools/call":
                tool = str(params.get("name") or "")
                args = params.get("arguments")
                if tool in PUBLIC_TOOLS:
                    if tool == "start_sign_up":
                        _d.note_sign_up_intent(ip)
                    _keys.audit(principal=None, method="tools/call", tool=tool,
                                args=_arg_names(args), status=200, note="anonymous", ip=ip)
                    continue
                need = TOOL_SCOPES.get(tool)
                if need is None:
                    _keys.audit(principal=None, method="tools/call", tool=tool,
                                args=_arg_names(args), status=403, note="anonymous: unknown tool", ip=ip)
                    await _send_json(send, 403, _rpc_error(
                        m.get("id"), -32001, f"unknown tool '{tool}'",
                        {"error": "unknown_tool", "tool": tool or None,
                         "required_action": None,
                         "user_message": f"AeroStator has no tool named '{tool}'."}))
                    return
                await self._refuse_anonymous(send, m, ip, tool=tool, need=need, args=args)
                return
            if method == "resources/read":
                if str(params.get("uri") or "") not in _d.PUBLIC_RESOURCES:
                    await self._refuse_anonymous(send, m, ip)
                    return
        await self._forward(scope, receive, send, body, None)




def install(app) -> None:
    """Add the gate as the OUTERMOST middleware (call after every other one)."""
    app.add_middleware(McpGate)
