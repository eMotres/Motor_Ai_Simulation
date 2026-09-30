# AeroStator MCP: anonymous discovery, auth errors and sign-up (2026-09-29)

Before this change every request to `https://aerostator.com/mcp` without a
token got HTTP 401 before the MCP SDK saw it, so an AI client that had not
signed in learned nothing about the service. Now an unauthenticated client
can find out what AeroStator is, what it computes, what each calculation
needs and how to sign in or sign up. It still cannot see any user data or
start anything. After OAuth sign-in the full tool set allowed by the grant's
scopes is available, as before.

Files: `src/motor_ai_sim/mcp_discovery.py` (public content, error builder),
`src/motor_ai_sim/mcp_app.py` (gate + filtered lists), `src/motor_ai_sim/oauth.py`
(sign-up continuation, `WWW-Authenticate`), `src/motor_ai_sim/routes/auth_local.py`
(`return_to` on register/verify, per-IP sign-up limit),
`web/src/components/auth/OAuthConsent.tsx`, `LoginDialog.tsx`,
`web/src/contexts/AuthContext.tsx`, `web/src/lib/localAuth.ts`;
tests `tests/test_mcp_discovery.py`, `tests/test_mcp_signup.py`.

## Anonymous tier (no `Authorization` header)

`McpGate` enforces a **fail-closed allow-list**. The tool descriptions are
not what protects anything:

| allowed anonymously | anything else |
|---|---|
| methods `initialize`, `ping`, `notifications/initialized`, `notifications/cancelled`, `tools/list`, `tools/call`, `resources/list`, `resources/read`, `resources/templates/list`, `prompts/list` (the three lists are filtered to `PUBLIC_RESOURCES` / `PUBLIC_RESOURCE_TEMPLATES` / `PUBLIC_PROMPTS`; the last two are empty) | 401 `authentication_required` (e.g. `prompts/get`, `completion/complete`) |
| `tools/call` of the public tools below | a protected tool: 401 `authentication_required`; a name that is neither public nor in `TOOL_SCOPES`: 403 `unknown_tool` |
| `resources/read` of `emotres://guide` | 401 |
| JSON-RPC over `POST`; a plain `GET` returns the service card (below) | `GET` with `Accept: text/event-stream`, `DELETE`: 405 `Allow: POST` |

One refused message in a JSON-RPC batch refuses the whole batch. Refusals
happen in the gate, so no tool body is entered (tested). The SDK list
handlers use the same tables: anonymous `tools/list` returns only the public
tools, anonymous `resources/list` only the public resources. A signed-in
`tools/list` returns the public tools plus every tool whose scope the key or
grant holds.

A test (`test_every_registered_tool_is_classified`) fails when a tool is
registered without being put in `PUBLIC_TOOLS` or `TOOL_SCOPES`, and when a
tool has no line in the capability catalogue. The same holds for resources,
resource templates and prompts: each must be in its `PUBLIC_*` or
`PRIVATE_*` table (`test_every_resource_template_and_prompt_is_classified`),
and one registered later without being made public stays out of the
anonymous lists (tested).

**Who is anonymous.** Anonymous means the `Authorization` header is absent.
A header that is present is a credential even when it is empty or only
whitespace: that gets 401 `invalid_token` / `reauthenticate`. Two
`Authorization` headers get 400 `invalid_request`. Neither case falls back to
the anonymous tier.

### Public tools

All are `readOnlyHint: true`, `destructiveHint: false`,
`idempotentHint: true`, `openWorldHint: false`, and declare
`_meta.securitySchemes: [{type: "noauth"}]`. Protected tools declare
`[{type: "oauth2", scopes: [<scope>]}]`, the mirror of the per-tool
declaration ChatGPT's Apps SDK reads.

| tool | returns |
|---|---|
| `describe_service()` | name, operator (MOTRES d.o.o.), summary, licence `AGPL-3.0-or-later`, website, MCP endpoint, source / terms / privacy / docs URLs, data policy |
| `list_capabilities()` | every tool: name, one-line purpose, `required_scope` (null = public), `requires_auth`, `read_only`; every scope with what it unlocks |
| `list_calculation_types()` | `machine_fit_check`, `saved_performance`, `design_from_requirements`, `em_fem_transient`, `thermal`, `coupled_em_thermal`, `catalog_lookup` (via MCP), plus `efficiency_map`, `mechanical`, `controller_losses`, `end_effect_3d`, `optimization` (web app only, no MCP tool yet) |
| `get_input_requirements(calculation_type)` | required / optional inputs with UCUM units (`N.m`, `/min`, `V`, `mm`, `kg`, `Cel`, `kW`) and ranges. The ranges come from the validators' own constants (`agent_designs.TORQUE_RANGE_NM`, `DC_BUS_RANGE_V`, `COOLINGS`, `DUTIES`, `STEPS_RANGE`…) and the tool schemas, so they cannot drift. Web-only types return no inputs and name the web tab. |
| `how_to_authenticate()` | OAuth metadata URLs (protected resource, authorization server), authorize / token / registration endpoints, default scopes, the API-key alternative, sign-in and sign-up URLs, each scope and what it unlocks, a `user_message` |
| `start_sign_up()` | the sign-up URL, steps, `account_created_by_this_call: false`, a `user_message`. It takes no input and creates nothing. |

Every URL is built from `PUBLIC_BASE_URL` (the OAuth issuer), never from the
request's `Host` and never localhost. `MCP_DOCS_URL`, `PUBLIC_SOURCE_URL`,
`PUBLIC_TERMS_URL` and `PUBLIC_PRIVACY_URL` override the external links.

No user data. No public function takes a `Principal` or reads a user store,
workspace, die grant, machine, draft, job or usage record. A test calls every
public tool, `initialize`, the lists and the guide anonymously with two users
holding keys and granted dies, and checks that no e-mail, name, key id or die
name appears in any answer.

### Anonymous fair use and audit

Every anonymous HTTP request (not only `tools/call`) counts against a per-IP
bucket `anon:<ip>`: `MCP_ANON_RATE_PER_MIN` (default 20) and
`MCP_ANON_RATE_PER_DAY` (default 300). Over the limit: 429 + `Retry-After`,
`error.data.error = "rate_limited"`. Anonymous tool calls and refusals are
written to `mcp_audit.jsonl` with `email: null` and the `ip`. For anonymous
calls and for every refused call (401, 403, 429) the audit keeps only the
argument **names** (`{"arg_names": [...]}`), never a value. A password
pasted into a public tool therefore never reaches the log (tested with
`{"password": …}`). An accepted signed-in call keeps its value summary as
before. No token is ever logged, only credential ids.

### Client IP (anonymous quota, sign-up and login limits)

`motor_ai_sim/client_ip.py` honours forwarding headers **only when the TCP
peer is a trusted proxy**. Trusted means exact endpoints, never a whole
private range, because another container on any docker bridge (compute
sandboxes on 172.17, the ERP on 172.19) must not be able to forge
`X-Real-IP`. The trusted set is:

- loopback (`127.0.0.0/8`, `::1`);
- the addresses that the hostnames in `TRUSTED_PROXY_HOSTS` (default `web`,
  the compose service of the web container) resolve to. They are resolved
  at API start-up (`client_ip.warm`, at most 2 s), re-resolved every 60 s in
  the background, and re-resolved when a peer carrying forwarding headers
  misses (at most every 10 s), so a recreated network with a new subnet is
  followed without configuration. If the name does not resolve (local dev
  without docker), only loopback is trusted;
- `TRUSTED_PROXIES`: explicit extra addresses / CIDRs, empty by default.

A non-IP peer (unix socket, the in-process test client) counts as trusted.
From a trusted peer the address is taken as follows:

1. `X-Real-IP`, when it is a single valid address that is not itself a
   trusted proxy;
2. otherwise the **rightmost** `X-Forwarded-For` hop that is not a trusted
   proxy. Every hop right of it was appended by our own proxies, and
   anything left of it, which the client controls, is never selected. A
   malformed hop stops the walk;
3. otherwise the peer itself.

A direct (untrusted) peer is counted by its own address, whatever headers
it sends. The MCP anonymous quota and all `/api/auth` limits (login lockout,
mail, sign-up) use this helper; the support chat keeps its own rule.

Production chain: the host nginx sets `X-Real-IP $remote_addr` (it
overwrites) and appends to `X-Forwarded-For`. It proxies to
`127.0.0.1:8080`, the web container's only published port, so the
connection reaches the container from its own network's **gateway**
(docker-proxy). The container nginx (`deploy/nginx.conf`) uses the realip
module with `real_ip_header X-Real-IP` and includes
`/etc/nginx/realip/trusted.conf`. That file is written at every container
start by `/docker-entrypoint.d/15-realip-gateway.sh`
(`deploy/nginx-realip-gateway.sh`, copied and made executable in
`deploy/Dockerfile.web`). The script reads the default gateway from
`/proc/net/route` and writes `set_real_ip_from 127.0.0.1;` plus
`set_real_ip_from <gateway>;`. It fails closed: with no gateway found only
127.0.0.1 is trusted, the file baked into the image is that same
loopback-only default, and the API then counts every visitor as the
gateway, which is one shared bucket and never a spoofable one. The
container's `$remote_addr`, and so the `X-Real-IP` it passes to the API,
is therefore the visitor, and only for requests that came in through the
host. No host nginx change is required.
Optional hardening on the host is `proxy_set_header X-Forwarded-For
$remote_addr;`, which overwrites instead of appending.

## Plain HTTP: `GET /mcp`, `/.well-known/mcp.json`, `/llms.txt`

An external checker or a web-fetch tool starts with a plain
`GET https://aerostator.com/mcp`, not with MCP. Before this change that
returned 401 with no explanation, `/.well-known/mcp.json` returned 404, and
`/llms.txt` returned the SPA's `index.html`.

| request (no `Authorization` header) | answer |
|---|---|
| `GET` / `HEAD /mcp`, `Accept` without `text/event-stream` | **200 service card**. JSON by default (`*/*`, `application/json`, no Accept); HTML when `text/html` ranks above JSON (a browser). It covers what AeroStator is, that this is a stateless streamable-HTTP MCP endpoint (POST JSON-RPC 2.0, supported protocol versions from the SDK), the public tools (name + first sentence, taken from the SDK tool registry like `tools/list`), sample `initialize` / `tools/list` requests and a curl line, OAuth metadata URLs, sign-in / sign-up URLs, how to add the server in claude.ai / ChatGPT / Claude Code (`claude mcp add --transport http aerostator https://aerostator.com/mcp`), and the docs, card and source URLs. Headers: `Cache-Control: public, max-age=300`, `Vary: Accept, Authorization`, `Link: </.well-known/mcp.json>; rel="describedby"`. It counts against the anonymous per-IP quota. |
| `GET /mcp` with `Accept: text/event-stream` (an MCP client opening the standalone SSE stream) | **405**, `Allow: POST`. The streamable-HTTP spec says a server with no GET stream "MUST … return HTTP 405". The TypeScript SDK treats a 405 there as benign (no error, no OAuth). Before, the 401 at this point made it start OAuth right after an anonymous `initialize`. The Python SDK client opens that stream only when the server issued a session id, which this stateless server never does. |
| `DELETE /mcp` (end session), any other non-POST | **405**, `Allow: POST`. The spec allows 405 for "no session termination", and both SDKs accept it. |
| any method **with** an `Authorization` header | Unchanged. A bad token gets 401 `invalid_token`, even on GET; a valid one goes to the SDK. |

`server/discover` (the 2026-07-28 revision's first call instead of
`initialize`) is on the anonymous allow-list. It returns the SDK's
capabilities and the server instructions.

**Server card** at `/.well-known/mcp.json`, also `/.well-known/mcp` and
`/.well-known/mcp/server-card.json` (the path used by the older SEP-1649).
The MCP server-card proposal is not settled: SEP-1649 was superseded by
SEP-2127, which is still in review, and whether `tools` belongs in the card
is still debated. So this is a documented **minimal card** in the
server.json-derived shape SEP-2127 drafts: `name` (`com.aerostator/mcp`),
`title`, `description`, `version`, `websiteUrl`, `repository`, `license`,
`remotes: [{type: "streamable-http", url}]`, `protocolVersions`,
`capabilities`, `authentication` (protected-resource and
authorization-server metadata URLs, scopes), `tools` (public ones only),
`documentationUrl`, and `_meta["com.aerostator/card"]` pointing at the
service card, `llms.txt` and the sign-up page. It is cached for 5 min. The
existing `location /.well-known/` in `deploy/nginx.conf` already sends it to
the API.

**`/llms.txt`** (`text/plain`, markdown in the llmstxt.org layout: H1,
summary quote, link lists) covers the service, the MCP endpoint, the server
card, OAuth metadata, the Claude Code command, the public tools and the docs.
`deploy/nginx.conf` has `location = /llms.txt` proxied to the API, so it is
answered ahead of the SPA fallback. Files: `routes/mcp_discovery.py`,
`mcp_discovery.service_card / server_card / llms_txt`,
`tests/test_mcp_plain_http.py`.

## Error contract

All auth refusals are JSON-RPC error responses with a structured `data`
member. For compatibility, `reason` stays a top-level member of the 401 body.

```json
HTTP/1.1 401 Unauthorized
WWW-Authenticate: Bearer realm="emotres-mcp",
  resource_metadata="https://aerostator.com/.well-known/oauth-protected-resource/mcp",
  scope="catalog:read machines:read"

{"jsonrpc": "2.0", "id": 1, "reason": "no_token",
 "error": {"code": -32001, "message": "Authentication required",
  "data": {"error": "authentication_required",
           "required_action": "sign_in",
           "required_scope": "machines:read",
           "tool": "list_machines", "method": "tools/call", "reason": "no_token",
           "sign_in_url": "https://aerostator.com/?signin=1",
           "sign_up_url": "https://aerostator.com/?signup=1",
           "authorize_url": "https://aerostator.com/oauth/authorize",
           "resource_metadata_url": "https://aerostator.com/.well-known/oauth-protected-resource/mcp",
           "docs_url": "https://github.com/eMotres/Motor_Ai_Simulation/blob/pre-migration-freeze-2026-09-15/docs/MCP_DISCOVERY.md",
           "user_message": "Sign in to AeroStator to use 'list_machines': click Connect / Sign in for AeroStator in this app; the page that opens also lets you create an account."}}}
```

| case | HTTP | `WWW-Authenticate` | `data.error` | `required_action` |
|---|---|---|---|---|
| no token, protected tool / method / resource | 401 | `resource_metadata`, `scope` = default read scopes + the tool's scope (no `error`, RFC 6750 §3.1) | `authentication_required` | `sign_in`, or `sign_up` when the client said it has no account (below) |
| token presented but malformed / unknown / expired / revoked (any method, public tools included) | 401 | `error="invalid_token"`, `resource_metadata`, `scope`, `error_description` | `invalid_token` | `reauthenticate` |
| `Authorization` present but empty / whitespace | 401 | as above | `invalid_token` (`reason: empty_authorization`) | `reauthenticate` |
| two `Authorization` headers | 400 | `error="invalid_request"` | `invalid_request` | `reauthenticate` |
| token of a disabled account | 401 | as above | `account_disabled` | `contact_support` |
| valid token without the tool's scope | 403 | `error="insufficient_scope"`, `scope` = held scopes + the missing one, `resource_metadata`, `error_description` | `insufficient_scope` (+ `granted_scopes`) | `grant_scope` |
| unknown tool | 403 | none | `unknown_tool` | null |
| rate limit (anonymous per IP, key per minute/day, daily simulations) | 429 + `Retry-After` | none | `rate_limited` (+ `retry_after_s`) | `wait_or_sign_in` (anonymous) |

`error.code` is `-32001` for the auth cases (the existing Stage 1 code) and
`-32029` for rate limits. `user_message` is always one plain sentence an AI
can relay as it is. For `grant_scope` it differs by credential: an agent key
is told to create a key with the scope under Access for agents, and an OAuth
grant is told to reconnect and allow it.

**`sign_up` vs `sign_in`.** The client "indicates no account" in either of
two ways. It can call `start_sign_up` (the client IP is remembered for
30 min). It can also send `params._meta["aerostator/account"] = "none"` on
the request. Both only change the advice, never access. Both `sign_in_url`
and `sign_up_url` open a page offering both options.

### Why HTTP 401 mid-session (spec basis)

MCP authorization spec 2025-11-25 (the same rules as 2025-06-18, plus scope
guidance):

- "MCP clients **MUST** be able to parse `WWW-Authenticate` headers and
  respond appropriately to `HTTP 401 Unauthorized` responses". This is not
  limited to `initialize`. The TypeScript SDK's
  `StreamableHTTPClientTransport` (checked on its `main` branch) handles a
  401 on any POST by running the auth provider and retrying once, and a 403
  `insufficient_scope` by running step-up authorization and retrying.
- "Invalid or expired tokens **MUST** receive a HTTP 401 response." This is
  why a bad token is 401 even on public methods: the client must learn that
  its credential is dead.
- Servers **SHOULD** include `scope` in the 401 challenge, and clients use
  it first when choosing scopes ("Scope Selection Strategy").
- Runtime insufficient scope: 403 with `error="insufficient_scope"`,
  `scope`, `resource_metadata`. Clients acting for a user **SHOULD** run the
  step-up authorization flow. We send held + missing scopes (the spec's
  "recommended approach"), so a step-up does not drop permissions.
- "Authorization is OPTIONAL": a server may serve some requests without a
  token. The anonymous tier relies on this.

Known client difference: ChatGPT's Apps SDK documents a different trigger
for its account-linking UI in mixed-auth servers. That trigger is a tool
result with `isError: true` and `_meta["mcp/www_authenticate"]`, sent with
HTTP 200. We follow the owner's requirement and the MCP spec (HTTP 401 +
header) instead. The same challenge string is not duplicated in the body.
Whether ChatGPT reacts to a mid-session 401 must be checked with a real
ChatGPT connector after deploy. Clients that are set up with OAuth from the
start (the connector dialogs in claude.ai and ChatGPT) are not affected.

**Risk to check after deploy:** a client that adds the server now gets a
successful anonymous `initialize` and may treat the server as "no auth
needed" until the first protected call returns 401. Spec-compliant clients
then run OAuth. The instructions and every `user_message` tell the model to
point the user at Connect / Sign in.

## Sign-up (no password ever passes through the chat)

- Sign-up happens only in the browser. That is the OAuth window the AI app
  opens (`/oauth/authorize` then `/agent-consent?request=<id>`), or
  `/?signup=1`. The consent page shows **Sign in** and **Create account**.
  The dialog is the existing one (`LoginDialog`): e-mail + password with
  e-mail verification, or Google. It reuses `POST /api/auth/register` and
  `POST /api/auth/verify`; there is no second sign-up implementation.
  Creating an account shows the Terms / Privacy links and the AGPL notice
  with the source link.
- **Continuation, bound to the registrant.** Create account on the consent
  page sends `return_to=/agent-consent?request=<id>`. The server accepts only
  exactly that shape, for a request that is still pending (a relative path to
  our consent page, never a URL). When this registration **creates** the
  account, the server mints a single-use continuation secret
  (`secrets.token_urlsafe(32)`). Only its SHA-256 is stored, with
  `{rid, normalized e-mail}` (`oauth.create_continuation`). The confirmation
  link is `<PUBLIC_APP_URL>/agent-consent?request=<id>&continue=<secret>&verify=<token>`.
  The mail names the host the app will return to and says not to allow it
  unless the reader started it.
- **`/api/auth/verify`** takes `{token, continuation}` and **never** a
  caller-supplied path. After the token proves the address, the continuation
  is spent (any attempt burns it). It resumes the request only when it was
  issued for that same address and the request is still pending. The
  request is then **bound** to that address and extended, and the answer
  carries `return_to`, derived from the stored record. A foreign, replayed,
  expired or mismatched secret resumes nothing (`authorization_pending:
  false`), and the web moves the tab off the consent page.
- **Consent.** A bound request can be seen and approved only by the bound
  account: `GET` / `POST /api/oauth/requests/{id}` answer 403 for any other
  account, and the request stays pending for the right one. The page always
  shows the client name and the return host. A request resumed from a
  confirmation mail also shows a warning to allow only if you started it
  yourself.
- **Residual risk (documented).** Someone could start an authorization for
  their own client and register a victim's address from that consent page.
  The victim's confirmation mail would then lead to that consent. The victim
  would still have to sign in to an account whose password they never set
  (or via Google) and click Allow on a page that names the client and the
  return host and warns them. Registration of an address one does not own
  is the existing sign-up model (the address is proven by the link).
- Opening the link in the same tab or another one (even another browser)
  shows the same consent page. The dialog confirms the address and asks for
  the password once. Then Allow, the code goes back to the AI app's
  `redirect_uri`, and the app is connected without starting over.
- **Pending-request TTL.** A plain pending request lives 15 min
  (`REQUEST_TTL_S`). A sign-up step (minting or spending a continuation)
  extends it to 30 min from that step (`SIGNUP_REQUEST_TTL_S`), never
  beyond 1 h after `/authorize` (`SIGNUP_MAX_AGE_S`). An expired request is
  never revived: `register` answers 410 ("start connecting again"). The AI app's own callback may time out earlier; then the user
  clicks Connect again and signs in with the new account.
- **Google.** Google sign-in on the same dialog creates the account on first
  use (the mailbox is proven by Google) and continues straight to consent.
- **Abuse limits.** Well-formed sign-ups are limited per IP
  (`auth_email.SIGNUP_IP`: 5 per hour) on top of the existing mail limits
  (`MAIL_IP` 10/h, `MAIL_ACCOUNT` 3/h). No account can sign in before its
  e-mail is confirmed. No tool creates accounts: `start_sign_up` only returns
  a URL and has no input.
- **URL-mode elicitation.** It is not used, deliberately. (1) The server
  runs stateless JSON-response streamable HTTP, so a server-to-client
  `elicitation/create` has no stream to travel on. The server also cannot
  know per request whether the client declared `elicitation.url` (that is
  only in `initialize`, a separate HTTP exchange with no session), and the
  spec says "Servers **MUST NOT** send elicitation requests with modes that
  are not supported by the client". (2) The `URLElicitationRequiredError`
  (`-32042`) means "retry the original request after the elicitation", which
  does not fit a sign-up. (3) The spec also says URL mode "is *not* for
  authorizing the MCP client's access to the MCP server". Sign-in stays with
  MCP authorization. `start_sign_up` returns the URL and a `user_message`
  instead.

## Manual check

```bash
B=http://127.0.0.1:8765/mcp; H='-H Content-Type:application/json -H Accept:application/json,text/event-stream'
curl -s $B $H -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'            # 6 public tools
curl -s $B $H -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"describe_service","arguments":{}}}'
curl -si $B $H -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"list_machines","arguments":{}}}'  # 401
curl -si $B $H -H 'Authorization: Bearer emk_bad_bad' -d '{"jsonrpc":"2.0","id":4,"method":"tools/list","params":{}}'  # 401 invalid_token
```
