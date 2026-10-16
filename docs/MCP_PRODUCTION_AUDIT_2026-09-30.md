# AeroStator production MCP audit — 2026-09-30

Endpoint: `https://aerostator.com/mcp`. Tests originated from an external Windows client except the official Python MCP SDK probe, which ran in the production API container against the public HTTPS URL. [Exact response bodies and selected response headers](MCP_PRODUCTION_RAW_2026-09-30.json) are preserved without bearer tokens.

| Check | Result | Evidence / limit |
|---|---|---|
| A. Discovery | PASS | `/mcp?v=test`, `/.well-known/mcp.json`, `/llms.txt`, protected-resource metadata: HTTP 200, expected JSON/text content types, no cookie or authentication required. Unique query reached host nginx access log. |
| B. GET `/mcp` card | PASS | HTTP 200 JSON; endpoint, transport, OAuth URLs, public tools and scopes are described. |
| C. `initialize` | PASS | JSON-RPC 2.0, protocol `2025-11-25`, server info and capabilities. Initialized notification returned 202. Stateless server did not issue `Mcp-Session-Id`. |
| D. Anonymous `tools/list` | PASS | Six public tools only; each has input/output schema, description, noauth scheme and read-only annotation. |
| E. Public tools | PASS | `describe_service`, `list_capabilities`, `list_calculation_types`, `get_input_requirements`, `how_to_authenticate`, `start_sign_up` returned structured content. `get_input_requirements` needs `calculation_type`; `design_from_requirements` and `em_fem_transient` worked. Repeated rapid anonymous calls returned a machine-readable 429 with `retry_after_s=29`. |
| F. OAuth discovery | PASS for metadata and DCR | Resource and authorization-server metadata: HTTP 200; scopes and S256 PKCE advertised. Dynamic registration: 201. Correct authorize request: 302 to consent; wrong redirect URI: 400. Interactive login, consent, token exchange and refresh were **not verified**. |
| G. Registration | PARTIAL | `start_sign_up` returns a sign-up URL and clear user message. Actual account creation, e-mail verification and return to OAuth were not performed. |
| H. Authenticated `tools/list` | NOT VERIFIED | No normally authenticated test user/grant was available. Do not infer from the public catalogue. |
| I. Test motor calculation | NOT VERIFIED | No authenticated calculation was launched. From the public requirements, 24 V / 0.5 kW / 6000 rpm needs `cooling` and `duty` from the engineer. |
| J. Structured results | NOT VERIFIED | No real result retrieved. Public tools return `structuredContent`; calculation results require an authenticated run. |
| K. Project URL | NOT VERIFIED | `open_in_configure` is advertised, but no draft and link were created. |
| L. Security | PARTIAL | Anonymous `list_machines`, `start_design` and `simulate` returned 401 JSON-RPC `authentication_required`, required scope and OAuth URLs; wrong OAuth redirect was rejected. Cross-user IDs, scope restrictions after login and ownership of calculations were not tested. |
| M. `/mcp/` | PASS after fix | Before: GET served the HTML SPA; POST returned 405. After `cc0d250`, GET returns 308 to `https://aerostator.com/mcp`; POST preserves body through 308 and returns `tools/list` HTTP 200. Both hops appear in nginx access log. |

An agent starting only with the public URL can identify the service, supported MCP versus web-only calculations, required data and authentication. For “Design me a 24 V PMSM motor, approximately 500 W” with the additional 6000 rpm test speed, it should ask the user for `cooling` and `duty`. It may derive torque as ~0.796 N·m from power and speed, but should not silently choose cooling or duty. The public tool has no topology field; the service currently starts from a permitted existing machine and scales a draft, so it cannot promise an arbitrary new PMSM topology.

Interoperability: a raw Node HTTP client completed `initialize → tools/list → tools/call`; the official Python MCP SDK 2.2 client completed the same sequence against the production HTTPS endpoint (six tools; `describe_service.is_error=False`). This is two independent client implementations, although the SDK probe originated inside the production container rather than on a separate external network.

The confirmed routing defect was fixed in `deploy/nginx.conf` on top of production commit `970e1228781088938a7293a148a8bcefce948b6a`. Commits: `5664c36` adds the 308 route; `cc0d250` corrects its scheme to HTTPS. `cc0d250b21e080da8bbb13ac34898671eece51f8` is deployed. Only the web container was rebuilt; nginx syntax, API health and unchanged API container ID were checked. Deploy lock was released.

No result should be described as a successful end-to-end motor design until a user completes ordinary OAuth consent and a draft/solve/result URL is tested. A proposed shortcut that minted a session token over root SSH was rejected by automatic approval review and was not run.
