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

1. auth   — ``Bearer emk_…`` key or ``Bearer emo_…`` OAuth token
            -> 401 + WWW-Authenticate: Bearer resource_metadata=…
2. scope  — the tool's scope must be on the key            -> 403
3. fair use — per key per minute / per day, ``tools/call`` only -> 429 + Retry-After
4. audit  — one line per tool call (user, key, tool, args summary, status)

then hands the request to the SDK with the ``Principal`` in the ASGI scope.
"""
import json
from contextlib import asynccontextmanager
from typing import Annotated, Any, Dict, Optional

from mcp.server.mcpserver import Context, MCPServer
from mcp_types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from motor_ai_sim import agent_keys as _keys
from motor_ai_sim import mcp_tools as _t
from motor_ai_sim import oauth as _oauth

MCP_PATH = "/mcp"
SCOPE_KEY = "emotres.mcp_principal"

#: tool -> the scope it needs.  A tool missing here is refused (fail closed).
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


def _principal_from(ctx) -> _keys.Principal:
    req = getattr(ctx.request_context, "request", None)
    p = (getattr(req, "scope", None) or {}).get(SCOPE_KEY)
    if not isinstance(p, _keys.Principal):          # McpGate always sets it
        raise PermissionError("unauthenticated")
    return p


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


def build_server():
    """The MCPServer with the five Stage-1 tools and the guide resource."""
    srv = MCPServer(
        name="emotres",
        title="eMotres motor catalog",
        instructions=("eMotres electric-machine catalog, saved simulation results "
                      "and (with designs:write / simulate) draft designs simulated "
                      "on the user's own queue. Start with check_fit, or "
                      "start_design when nothing fits; if an answer says "
                      "needs_input, ask the engineer. Read emotres://guide."),
        version="stage3",
    )
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
    srv = get_server()
    async with srv.session_manager.run():
        yield


# ── the gate ─────────────────────────────────────────────────────────────────

async def _send_json(send, status: int, body: dict, headers=()) -> None:
    raw = json.dumps(body).encode("utf-8")
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(raw)).encode()),
                            *[(k.encode(), v.encode()) for k, v in headers]]})
    await send({"type": "http.response.body", "body": raw})


def _rpc_error(msg_id, code: int, text: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": text}}


def verify_any(authorization):
    """An agent key (``emk_``) or an OAuth access token (``emo_``) -> Principal."""
    if isinstance(authorization, str):
        parts = authorization.strip().split(" ", 1)
        if (len(parts) == 2 and parts[0].lower() == "bearer"
                and parts[1].strip().startswith(_oauth.ACCESS_PREFIX)):
            return _oauth.verify_access(parts[1].strip())
    return _keys.verify(authorization)


class McpGate:
    """Outermost ASGI middleware: ``/mcp`` -> auth/scope/fair-use/audit -> SDK."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("path", "").rstrip("/") != MCP_PATH:
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1")
                   for k, v in scope.get("headers") or ()}
        principal, reason = verify_any(headers.get("authorization"))
        if principal is None:
            _keys.audit(principal=None, method="auth", status=401, note=reason)
            err = "invalid_token" if reason not in ("no_token",) else ""
            await _send_json(send, 401, {"error": "unauthorized", "reason": reason,
                                         "detail": "Sign in with OAuth or send Authorization: "
                                                   "Bearer <eMotres agent key>"},
                             [("www-authenticate", _oauth.www_authenticate(err))])
            return

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
        for m in msgs:
            if not isinstance(m, dict) or m.get("method") != "tools/call":
                continue
            params = m.get("params") or {}
            tool = str(params.get("name") or "")
            args = params.get("arguments")
            need = TOOL_SCOPES.get(tool)
            if need is None or not principal.has(need):
                _keys.audit(principal=principal, method="tools/call", tool=tool,
                            args=args, status=403, note=f"needs {need}")
                await _send_json(send, 403, _rpc_error(
                    m.get("id"), -32001,
                    f"key lacks scope '{need}'" if need else f"unknown tool '{tool}'"),
                    [("www-authenticate", _oauth.www_authenticate("insufficient_scope")
                      + (f', scope="{need}"' if need else ""))])
                return
            ok, retry = _keys.take_quota(principal.credential_id)
            if not ok:
                _keys.audit(principal=principal, method="tools/call", tool=tool,
                            args=args, status=429)
                await _send_json(send, 429, _rpc_error(
                    m.get("id"), -32029, f"rate limit; retry after {retry} s"),
                    [("retry-after", str(retry))])
                return
            if tool == "simulate":
                # The DAILY simulation fair-use limit (Stage 3), counted from the job
                # queue's own records of this account's agent runs.
                from motor_ai_sim import agent_designs as _ad
                ok, retry, used, lim = _ad.check_quota(principal)
                if not ok:
                    _keys.audit(principal=principal, method="tools/call", tool=tool,
                                args=args, status=429, note=f"simulate fair-use limit {used}/{lim}")
                    await _send_json(send, 429, _rpc_error(
                        m.get("id"), -32029,
                        f"daily simulation fair-use limit reached ({used}/{lim}); "
                        f"retry after {retry} s"), [("retry-after", str(retry))])
                    return
            _keys.audit(principal=principal, method="tools/call", tool=tool,
                        args=args, status=200)

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
        inner[SCOPE_KEY] = principal
        await http_app()(inner, _replay, send)


def install(app) -> None:
    """Add the gate as the OUTERMOST middleware (call after every other one)."""
    app.add_middleware(McpGate)
