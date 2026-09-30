"""Plain-HTTP discovery of the MCP server (docs/MCP_DISCOVERY.md "Plain HTTP").

Public, anonymous, no user data, cacheable 5 min:
  GET /.well-known/mcp.json                 server card (JSON)
  GET /.well-known/mcp                      the same (alias)
  GET /.well-known/mcp/server-card.json     the same (SEP-1649's path)
  GET /llms.txt                             text/plain markdown for LLM crawlers

``GET /mcp`` itself (the service card) is answered by ``mcp_app.McpGate``.
Everything is built from ``mcp_discovery`` and the SDK tool registry.
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse, PlainTextResponse

from motor_ai_sim import mcp_app as _app
from motor_ai_sim import mcp_discovery as _d

router = APIRouter(tags=["mcp-discovery"])

_CACHE = {"Cache-Control": f"public, max-age={_d.CARD_MAX_AGE_S}"}


@router.get("/.well-known/mcp.json")
@router.get("/.well-known/mcp")
@router.get("/.well-known/mcp/server-card.json")
async def server_card():
    return JSONResponse(_d.server_card(await _app.public_tool_descriptions()),
                        headers=_CACHE)


@router.get("/llms.txt")
async def llms_txt():
    return PlainTextResponse(_d.llms_txt(await _app.public_tool_descriptions()),
                             headers=_CACHE, media_type="text/plain; charset=utf-8")
