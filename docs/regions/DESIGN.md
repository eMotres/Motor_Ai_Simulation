# Two regions: EU + US (design, stage 1)

Owner 2026-11-08: synchronise the two servers, put everything into the admin panel, filter user data — **US accounts in the
US, everyone else in Europe for now — and US users are answered by the US server.** Same day: computations may run on
either server for any account; **a computation on a server is not data** (only engineering inputs cross, never who they are
for).

Today: one server in the EU (Germany), Docker compose (API blue/green + worker + web/nginx), host nginx in front, state
under `/srv/motres/{shared,published,workspaces,identity,backups}`, code `/opt/motres/app`. A second server in the US
(Dallas, hostname `motres-us1.emotres.com`) is being provisioned.

This document fixes the decisions; stage 1 (this change) ships the code that is safe on one server and the protocol for
the rest. Everything is **off in single-region mode**: deploying stage 1 to today's EU server changes nothing a user sees;
an admin sees one new column and one new card.

## 1. Decisions at a glance

| Topic | Decision |
|---|---|
| Home region | Every account is `eu` or `us`, default `eu`. The company / stated country decides (`US` -> `us`); else the sign-up IP country if recorded; else `eu`. Admin override, audited. |
| Where data at rest lives | Workspaces, projects, production files, verification cases, acceptance and audit records, backups: only in the account's (or company's) home region. |
| Our library | Catalog dies, materials, bearings, wire, controllers, devices, propellers, passports, revisions, catalog 3-D models: authored on EU, mirrored read-only on US by hash manifest. Closed customer motors are customer data (home region). |
| Account directory | Both regions hold `email -> {id, region, tier, created}` of the other region's accounts; nothing else. E-mails stay unique, either login knows where to send the person. |
| Serving a signed-in account | Wrong server -> `421 Misdirected Request` `{code: wrong_region, region, base_url}`; the web moves the tab to `base_url` + same path. Page loads are redirected by the web (they carry no bearer). |
| Anonymous visitors | Edge geo, like the China pattern: host nginx 302 of page loads (never `/api/`) from US IPs to `us.aerostator.com`, with loop protection. Free. Paid alternatives listed in section 6. |
| Server-to-server | HMAC-SHA256 signed requests (`/api/peer/v1/*`), over WireGuard. Pull only; no endpoint writes the caller's user records into the receiver. |
| Admin | Region column + filter on Users, Projects, Verification; override in the user drawer; Servers -> Regions card (both servers' state). The other region's rows are fetched live and never stored. |
| Compute | A job of any account may run on either server (the less loaded one). Only the engineering input crosses, under an opaque token; the result returns to the home server, which alone knows whose it is. |

## 2. Home region

**Rule** (`src/motor_ai_sim/regions/classifier.py`):

1. An admin override stays.
2. Otherwise the first country found decides — `US` -> `us`, any other country -> `eu`. Sources in this order (company
   first, then what the person stated):
   a. the directory company the account is confirmed in (`platform_agreement.company_of`),
   b. the applicant-verification case (`application.country`),
   c. the Platform Agreement acceptance line (country where the business is established),
   d. the account's signed NDA record,
   e. a `country` on the account record (profile).
3. No country anywhere -> the sign-up IP country, **only if the account record carries one** (`signup_country`). The app
   stores the sign-up IP (`terms_acceptances.jsonl`) but no country, and stage 1 adds **no GeoIP database**. Stage 4 adds
   the country as a header set by the host nginx geo map (the same free list mechanism as the China map, here the ARIN
   delegated file or GeoLite2 — GeoLite2 needs a free MaxMind account and its licence terms; owner's choice); sign-up then
   stores `signup_country`.
4. Nothing -> `eu`.

Owner 2026-11-08: **North, Central and South America and the Caribbean go to the US server, every other country to the EU
server** (`AMERICAS_COUNTRIES` in `regions/classifier.py`, territories in the Americas included; US Pacific territories
GU, AS, MP, UM are not in the Americas and go to EU). Stage 4 edge routing uses the same list.

**Storage**: `users.json` record fields `home_region` (`eu` | `us`) and `home_region_set` `{by: admin|classifier, actor,
reason, iso}`. Absent = `eu`. The registry answer (`public_user`) carries `home_region` and `home_region_source`
(`admin` | `classifier` | `default`).

**Override**: `POST /api/admin/users/{email}/region {region, reason}` (admin only), audit action `user.region` with the
previous and new value. Setting the field **moves no data**; until the stage-3 migration of that account has run, the
account keeps being served where its data are (the guard stays off until then, see section 5).

**Classifier report**: `python scripts/classify_regions.py` (dry run, default) or `GET /api/admin/regions/classify` prints
every account with current region, proposed region, reason code and country. `--apply` stores the changed proposals and
never overrides an admin's choice.

## 3. Data map

| Path (`/srv/motres/...`) | Owner | Region | Notes |
|---|---|---|---|
| `identity/users.json`, `.sessions.json`, `.auth_secret` | accounts | per server | Each server has its own registry and its own signing secret. After stage 3 EU holds EU accounts, US holds US accounts. |
| `identity/terms_acceptances.jsonl`, `comm_prefs*`, `admin_audit.jsonl`, `auth_events.jsonl`, account deletion register | accounts | home | Records about a person live with the person. Audit lines of an admin action live where the action ran. |
| `identity/region_directory.json` | both | both | The other region's directory entries only (section 7). |
| `identity/region_jobs_outbound.json` | home server | home | Token -> run / owner / workspace map of jobs computed elsewhere (section 9). |
| `identity/cluster/` | per server | per server | Node registry and load history of that server. |
| `workspaces/<ws_id>/` | account | home | The account's machines, results, saved simulations. |
| `published/<ws_id>/` | account | home | Community publishes. Cross-region reading of another account's publish is not offered in stage 1-4 (open item if asked). |
| `shared/{dies,devices,propellers,basis,passports,motor_revisions,cad}`, `shared/{materials_library,bearings_library,wire_stock,fusion_param_map}.yaml`, `shared/catalog.yaml`, `shared/ecb_eurofxref.json` | MOTRES | EU authors, US mirrors read-only | `regions/catalog_manifest.py` allow-list. A die whose `die.yaml` names a customer or carries `home_region` != `eu` is left out. |
| `shared/projects/` | company / project owner | home of the project owner | A project's region = its owner account's region. |
| `shared/production/`, `shared/motor_tests/` | project | project's region | Production packages and bench tests belong to the project. |
| `shared/directory/` (companies, people, affiliations) | companies | home of the company (by company country) | Personal data. |
| `shared/legal/` (NDA and agreement signature records, PDFs) | accounts / companies | home | The texts themselves are in the code (`config/legal`). |
| `shared/digests/`, `shared/owner_report/`, `shared/runs/` | per server | per server | Operational; not replicated. |
| `private_assets/` | MOTRES | EU authors, US mirrors read-only | Same rule as the library. |
| `logs/`, `run/`, `web/` | per server | per server | |
| backups | per region | per region | Section 12. |

Anything not in the allow-list of the manifest is **not replicated** (fail closed): a new store is region-owned until it
is added to the list on purpose.

## 4. Configuration of a server

| Env | Default | Meaning |
|---|---|---|
| `MOTRES_REGION` | `eu` | region this server serves |
| `MOTRES_PEER_URL` | unset = single-region | the peer's API base URL (WireGuard address, stage 2) |
| `MOTRES_PEER_REGION` | the other region | |
| `MOTRES_PEER_HMAC_SECRET` or `MOTRES_PEER_HMAC_SECRET_FILE` | unset | shared secret, >= 32 characters; shorter = not configured |
| `MOTRES_REGION_BASE_URL_EU` / `_US` | `https://aerostator.com` / `https://us.aerostator.com` | public base URLs a 421 points at |
| `MOTRES_REGION_GUARD` | off | `1` turns the wrong-region answer on (also needs a configured peer) |
| `MOTRES_CROSS_REGION_COMPUTE` | off | `1` lets the scheduler send jobs to the peer (stage 2 wiring) |
| `MOTRES_RELAY_VIA_PEER` | off | `1` sends mail and LLM calls through the peer (US; needs a configured peer, section 7) |
| `MOTRES_REGION_DIRECTORY_FILE`, `MOTRES_REGION_JOBS_FILE` | beside `users.json` | test overrides |

Single-region mode = no peer URL or no usable secret. Then every `/api/peer/v1/*` route answers 404, the peer client sends
nothing, the guard passes everything, the admin card shows this server alone. That is the state after deploying stage 1.

## 5. Serving and routing

**Signed-in accounts** (`regions/guard.py`, web `lib/regionRedirect.ts`): an `/api/` request with a valid bearer of a
non-staff account whose home region is not this server's gets

```
421 Misdirected Request
X-Motres-Region: eu
{"detail": {"code": "wrong_region", "region": "us", "base_url": "https://us.aerostator.com", "message": "..."}}
```

421, not 409: the request is valid, it reached the wrong server, nothing changed. The web moves the tab to `base_url` +
the current path (https and `*.aerostator.com` only; never onto the host that refused). This is the page redirect: page
loads are served by nginx and carry no bearer, so the server cannot redirect them for a signed-in person; the first API
call of the page does. Never guarded: staff (admin / engineer work on both servers), anonymous callers and invalid tokens
(the role gate answers them), health, version, `/api/region`, the peer channel, sign-in / sign-out / account mail links,
legal texts, node agents.

Consistent with the China pattern (host nginx 302 of page loads only, never `/api/`, loop protection by the edge IP): the
edge handles anonymous visitors by IP, the app handles signed-in accounts by home region.

**Sign-in across regions** (stage 3 code): bearer tokens are per server (own `.auth_secret`, own sessions), and the
browser keeps them per origin. A person sent to the other server signs in there. Stage 3 adds (a) the login route
answering 421 for an e-mail the directory places on the peer, before any password check, and (b) a hand-off ticket: the
refusing server mints a 60 s single-use HMAC ticket (peer secret) for `{email, target region}`; `GET /region-handoff#t=...`
on the target consumes it and opens a local session, so the person is not asked for the password again.

**Turning the guard on**: only for an account whose data have been migrated (stage 3). Until then the field `home_region`
is advisory and `MOTRES_REGION_GUARD` stays off on both servers.

## 6. Edge routing of anonymous visitors (stage 4) — options

| Option | How | Cost | Remarks |
|---|---|---|---|
| **A. Host-nginx geo redirect (recommended)** | `us.aerostator.com` A/AAAA -> US server (Cloudflare DNS-only, as `cn`). EU host nginx: `geo $is_us` from a weekly-updated US list (`/etc/nginx/geo/us.conf`), 302 of GET/HEAD page loads (not `/api/`, `/mcp`, `/.well-known/`, `/forms/`, `/stats/`) to `https://us.aerostator.com$request_uri`; not for requests that came from the US server. US host nginx: the mirror rule for non-US visitors who land on `us.` only if the owner wants it (default: no, US server serves everyone who asks it). | free | Same as the live China redirect. `us.` has its own cookies / storage: sign-in is per host (section 5). Google sign-in must list `https://us.aerostator.com` as an authorised origin. |
| B. Cloudflare Load Balancing, geo steering | One hostname `aerostator.com`, Cloudflare proxies and steers by visitor region | **paid**: from about 5 USD / month plus per-origin and health-check charges | Owner declined |
| C. GeoDNS at another DNS provider (e.g. AWS Route 53 geolocation records) | `aerostator.com` resolves to the US server for US resolvers | **paid**: hosted zone plus per-million geo queries (small) | Moves DNS off Cloudflare; both servers then need the `aerostator.com` certificate (the HK edge already has the certificate-push channel pattern). Resolver location != visitor location. |

Recommendation: A now (free, proven by `cn.aerostator.com`), revisit B when one hostname matters more than 5 USD / month.

## 7. Server-to-server API

All under `/api/peer/v1`, HMAC-SHA256 signed (`regions/peer.py`):

```
X-Motres-Peer-Region  sender region          X-Motres-Peer-Ts     unix seconds
X-Motres-Peer-Nonce   16-64 random chars     X-Motres-Peer-Sig    hex HMAC(secret, "v1\n<METHOD>\n<path?query>\n<ts>\n<nonce>\n<region>\n<sha256(body)>")
```

Accepted only in multi-region mode, from the configured peer region, within 300 s, with a new nonce and a matching
signature (constant time); anything else is the same 404 an unknown path gets. Transport: a WireGuard tunnel between the
two hosts (`MOTRES_PEER_URL` = the tunnel address of the peer's API port); the API port is not published on the public
interface for this. The secret is generated once (`openssl rand -hex 32`), stored 0600 on both hosts, never in git.

| Endpoint | Returns | Used by |
|---|---|---|
| `GET /health` | region, version, deployed commit, load (cores, 1-min load, % per core), queue (running, queued, oldest wait), disk (total, free, used %) | Servers -> Regions card, scheduler |
| `GET /directory` | `{region, entries: {email: {id, region, tier, created}}}` of the accounts this server hosts | directory sync (pull, every 5 min in stage 2 + on demand from the admin) |
| `GET /catalog/manifest` | `{root_id, files: {path: {sha256, bytes}}, excluded_dies}` — EU only | US mirror job (stage 2) |
| `GET /admin/{users,projects,verification,jobs}` | this server's rows, tagged with its region | the other region's admin views, live |
| `POST /relay/mail` | `{ok}` or `{ok: false, code}` — EU sends one prepared message through its own SMTP | US (no SMTP credentials) |
| `POST /relay/llm` | `{ok, text, model}` or `{ok: false, code}` — EU runs one help-chat / spec-assistant (`purpose: support`) or production-translation (`purpose: translate`) request with its own provider and key | US (no provider keys) |

**Relay exception (owner 2026-11-09)**: the two `relay/*` endpoints are the documented exception to "pull only": they
perform an action for the peer. They store nothing of the request on EU. The only trace is one audit line
(`relay.mail` / `relay.llm`, actor `peer:<region>`): the sha256 of each recipient (never a plain address), message size,
model and token counts — no subject, body, prompt or reply. Same signature check as every peer route (404 when not
multi-region or unsigned). Limits: mail <= 2 MiB serialized and <= 100 recipients; LLM request <= 200 000 characters;
EU-side caps for the whole peer (`MOTRES_RELAY_MAIL_HOUR_MAX` 600 / h, `MOTRES_RELAY_LLM_MIN_MAX` 120 / min,
`MOTRES_RELAY_LLM_DAY_MAX` 20 000 / day); the per-account and per-IP help-chat limiters keep running on the US route
before the request is relayed. A server in relay mode never executes a relayed request.

**Enabling relay on US**: `MOTRES_RELAY_VIA_PEER=1` (valid only with a configured peer; ignored in single-region mode).
Then `auth_email.deliver`, `notify` (e-mail channel), the help chat, the specification assistant and the production
translation send to the peer instead of SMTP / the provider; no SMTP or provider key is needed on US. An unreachable or
refusing peer is a clear failure to the caller (mail: logged and raised like an SMTP failure; chat: the usual "could not
answer" reply; translation: `TranslateError`, text stays `pending`). `design_agent/runner.py` is never relayed. Without
the variable nothing changes.

Pull only: no peer endpoint writes anything. The directory file accepts the peer region's entries only, with the four
fields only (extra fields are dropped), so the peer can never place personal data or a local account into it. Admin
views of the other region fetch rows live through `GET /api/admin/regions/remote/{kind}` and never store them; a live read
of users or verification rows is audited (`regions.remote_read`).

**Open-only mirror (owner 2026-11-09)**: US mirrors only what is open in the public catalog; closed customer motors stay
on EU. A die is mirrored when its die-level visibility is `public` OR at least one configuration is `public`
(`motor_access.get_die_access` / `file_visibility`). For such a die: `die.yaml`, every `public` configuration (or `inherit`
while the die is `public`) with its `runs/<cfg>/` results and `<cfg>.*` assets; `private` configurations are left out with
everything that belongs to them. Files that cannot be attributed to a configuration are left out and listed (fail
closed). `passports/` and `cad/` (keyed die/config) follow the same rule; `motor_revisions/` only while every revision in
it names a mirrored configuration. Backup / temp files (`*.bak*`, `*.tmp`, `*~`, `*.orig`) are never listed; unreadable
files are skipped and counted (`skipped_unreadable`). `python scripts/catalog_manifest_report.py` prints the dry run.

**Catalog replication**: EU publishes the manifest; the US job (stage 2, systemd timer, every 15 min) compares it with
its own `build()` of the mirror, fetches the `fetch` list by rsync over WireGuard from the EU shared tree (read-only rsync
module limited to the allow-listed paths), deletes the `delete` list, and refuses to run if the US copy has local edits
(US never authors the library). `root_id` equal on both = in sync; the Regions card can show it in stage 2.

## 8. Global account directory

Local half: derived from `users.json` on every call. Remote half: `identity/region_directory.json`, replaced by each sync.
Uses: e-mail uniqueness (`store.email_taken_elsewhere`, wired into sign-up in stage 3: a sign-up for an e-mail the peer
hosts is answered like "already registered" with the right base URL), the login hand-off (section 5), the admin region
map of the Projects / Verification tables. Race: two sign-ups of one new e-mail on both servers within one sync interval
-> stage 3 sign-up asks the peer synchronously (`GET /directory?email=`, 2 s timeout); if the peer is down the sign-up
proceeds locally and the next sync flags the duplicate in the admin (never auto-merged).

## 9. Compute pool across regions

Owner 2026-11-08: jobs of any account may run on either server; a computation is not personal data.
`regions/compute.py`:

* **What crosses**: `{v, token, kind, priority, inputs}`. `inputs` = the job body with every identifying key removed at
  every depth (e-mail, owner, user, name, company, account / workspace / run / project ids, title, die / configuration /
  duty names, file names and paths, agent / client names, notes, labels, IP, tokens). After that, any string that still
  looks like an e-mail, a path or a file name makes the payload **refused** (the job then runs at home). `token` is 144
  random bits.
* **What stays home**: `identity/region_jobs_outbound.json` maps token -> run id, owner, workspace, target. Only the home
  server can file the result under an account.
* **On the computing server**: inputs and result are kept only while the job runs and until the home server confirms it
  received the result; unconfirmed, the hand-back is retried and everything is deleted **24 h** after the job ended
  (`RETENTION_S`). Foreign jobs are never in that server's backups (stage 2 puts them under `run/foreign_jobs/`, excluded).
* **Scheduling**: `pick_region` sends a job to the peer only with `MOTRES_CROSS_REGION_COMPUTE=1`, a configured and
  reachable peer, and clearly lower pressure there ((running + queued) / cores lower by more than 0.25), so equal load
  does not ping-pong. Otherwise home.
* Stage 1 ships the contract and its tests; nothing calls it from the queue yet (stage 2 wires `jobs.queued` once both
  servers run the same build). Consequence for the privacy text: computing a job elsewhere moves no personal data and is
  therefore not described as data storage (owner's correction of the v0.7 draft).

## 10. Admin panel

* **Users**: region column (chip EU / US, tooltip: who set it), region filter, the other region's accounts appended
  read-only (live, tooltip "lives on the US server"), override in the drawer ("Home region").
* **Projects**, **Verification**: region column (the owner's / applicant's region) and region filter.
* **Servers -> Regions** card: one row per region — state, version + commit, load, jobs running / queued, free disk. One
  row ("Single region") when no peer is configured; the peer row shows "unreachable" with dashes when it does not answer.
* New strings EN + zh-CN (`locales/*/admin.json` `regions.*`); one label + tooltip, no caption lines.

## 11. Failure modes

| Failure | Behaviour |
|---|---|
| Peer down / tunnel down | Card shows the peer "unreachable"; remote rows empty with `reachable: false`; directory keeps the last sync; scheduler keeps jobs home; each region serves its own accounts normally. |
| Secret missing / too short / mismatched | Single-region behaviour on that side (404 on peer routes); the other side sees the peer unreachable. Never weakly open. |
| Clock skew > 300 s | Signed requests refused as stale (NTP on both hosts is a stage 2 item). |
| Replay of a captured request | Refused (nonce cache, 300 s window). |
| Wrong `MOTRES_REGION` on a host | The card shows two servers of one region; `peer_configured()` is false when both claim the same region. |
| Account classified US but not migrated | Guard stays off for it (stage 3 enables per account after migration); it keeps working on EU. |
| A job input that still identifies someone | Payload refused, job runs at home. |
| Computing server loses a foreign job | Home server times out the token (24 h) and re-queues at home. |
| Directory duplicate e-mail | Flagged in admin after sync; never auto-merged. |

## 12. Backups per region

* EU: unchanged — restic to the Hetzner Storage Box (EU) of `deploy/backup/restic-include.txt`.
* US: its own restic repository on US storage (an InterServer storage product or another US provider; owner chooses),
  its own key, the same include list. US backups never go to the EU box and EU backups never to the US.
* The mirrored library on US is excluded from US backups (it is restorable from EU); `run/foreign_jobs/` is excluded on
  both.
* Restore rehearsal per region after stage 2.

## 13. Migration of existing US accounts (stage 3)

1. Dry run of the classifier on EU; owner reviews the list (`scripts/classify_regions.py`).
2. `--apply` sets `home_region=us` on the approved accounts (moves nothing).
3. Per account, in a quiet window: freeze its writes (disable for minutes, mail only after the owner's OK on the text),
   copy `workspaces/<ws_id>/`, `published/<ws_id>/`, its projects (owner = this account) with production and tests, its
   verification case and directory company / people, its acceptance / NDA / audit lines, to US; verify hashes; create the
   account on US with the same password hash and grants; switch the guard on for it; delete the EU copy after the US
   backup of it has run once (EU backups age out by rotation).
4. Projects with members in both regions stay with the owner's region; members from the other region open them on that
   server (staff anyway; customers of one company are normally in one region).

## 14. Stages

| Stage | Content | Who |
|---|---|---|
| 1 (this PR) | Design; region field + classifier (dry run); region config; guard (off); signed peer client / server (inactive); admin column, filter, override, Servers -> Regions card; compute-pool contract; legal texts v0.7 | code |
| 2 | Provision the US server (same compose, `MOTRES_REGION=us`), WireGuard between the hosts, peer secret on both, `MOTRES_PEER_URL` on both, catalog mirror timer, directory sync timer, US backups, `us.aerostator.com` DNS + certificate, Google sign-in origin; deploy; wire cross-region compute behind its switch | orchestrator |
| 3 | Login 421 + hand-off ticket, sign-up uniqueness, migration of US accounts, guard on per migrated account | code + orchestrator |
| 4 | Edge routing of anonymous visitors (option A), `signup_country` from the edge header | orchestrator |

## 15. Open owner decisions

* (a) ~~May EU customers' jobs run on the US server when EU is busy?~~ **Decided 2026-11-08**: jobs of any account may run on
  either server; only engineering inputs cross (section 9).
* (b) ~~Privacy wording for US storage~~ **Decided 2026-11-08**: texts v0.7 (privacy sections 5 and 6, Schedule 2 row);
  computation elsewhere is not described as storage.
* (c) Which edge option (A free / B paid) and whether `us.aerostator.com` should also send non-US visitors back to EU.
* (d) US territories as US (section 2)?
* (e) The US backup storage provider and its retention.
* (f) Whether published community work of one region should be readable from the other.
* (g) The Hetzner rows of Schedule 2 still say "All Platform data"; with a second host the owner may want "Platform data of
  accounts hosted in the EU" in a later version (v0.7 changed only what was approved).
