"""The MCP server at ``/mcp`` (Stage 1: read-only).  docs/MCP_2026-09-28.md.

Official MCP Python SDK (``mcp``), streamable-HTTP transport, STATELESS with
plain JSON responses: every POST is one self-contained JSON-RPC exchange, so
nginx needs no SSE/buffering settings and any API worker can answer.

It lives INSIDE the FastAPI process (same stores, same permission code) but in
front of the app's own middleware: ``McpGate`` is the OUTERMOST ASGI layer and
takes ``/mcp`` before the tier gate / workspace resolver / CORS ever see it —
those speak session tokens, and an agent key is not one.  McpGate does, per
request:

1. auth   — ``Bearer emk_…`` (agent_keys.verify) -> 401 + WWW-Authenticate
2. scope  — the tool's scope must be on the key            -> 403
3. quota  — per key per minute / per day, ``tools/call`` only -> 429 + Retry-After
4. audit  — one line per tool call (user, key, tool, args summary, status)

then hands the request to the SDK with the ``Principal`` in the ASGI scope.
"""
import json
from contextlib import asynccontextmanager
from typing import Annotated, Any, Dict, Optional

from mcp.server.mcpserver import Context, MCPServer
from mcp_types import ToolAnnotations
from pydantic import Field

from motor_ai_sim import agent_keys as _keys
from motor_ai_sim import mcp_tools as _t

MCP_PATH = "/mcp"
SCOPE_KEY = "emotres.mcp_principal"

#: tool -> the scope it needs.  A tool missing here is refused (fail closed).
TOOL_SCOPES: Dict[str, str] = {
    "list_catalog": "catalog:read",
    "get_catalog_entry": "catalog:read",
    "list_machines": "machines:read",
    "get_machine_performance": "machines:read",
    "check_fit": "machines:read",
}

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
    try:
        return fn(_principal_from(ctx), *args)
    except _t.ToolError as e:
        raise _SdkToolError(str(e)) from e


def build_server():
    """The MCPServer with the five Stage-1 tools and the guide resource."""
    srv = MCPServer(
        name="emotres",
        title="eMotres motor catalog",
        instructions=("Read-only access to eMotres electric-machine catalog and "
                      "saved simulation results. Start with list_machines or "
                      "check_fit; read resource emotres://guide for conventions."),
        version="stage1",
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

    @srv.resource("emotres://guide", name="guide", title="How to use eMotres MCP",
                  mime_type="text/markdown")
    def guide() -> str:
        return _t.GUIDE

    return srv


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


class McpGate:
    """Outermost ASGI middleware: ``/mcp`` -> auth/scope/quota/audit -> SDK."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("path", "").rstrip("/") != MCP_PATH:
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1")
                   for k, v in scope.get("headers") or ()}
        principal, reason = _keys.verify(headers.get("authorization"))
        if principal is None:
            _keys.audit(principal=None, method="auth", status=401, note=reason)
            await _send_json(send, 401, {"error": "unauthorized", "reason": reason,
                                         "detail": "Send Authorization: Bearer <eMotres agent key>"},
                             [("www-authenticate", 'Bearer realm="emotres-mcp"')])
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
                    f"key lacks scope '{need}'" if need else f"unknown tool '{tool}'"))
                return
            ok, retry = _keys.take_quota(principal.credential_id)
            if not ok:
                _keys.audit(principal=principal, method="tools/call", tool=tool,
                            args=args, status=429)
                await _send_json(send, 429, _rpc_error(
                    m.get("id"), -32029, f"rate limit; retry after {retry} s"),
                    [("retry-after", str(retry))])
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
