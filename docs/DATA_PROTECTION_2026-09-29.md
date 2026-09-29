# Data protection: code fixes (2026-09-29)

Follow-up to the customer data protection audit of 2026-09-29 (findings #5–#9,
#16, #17). This file covers the code. Server operations (off-site backups,
LUKS, SSH, the `erp` docker group, container hardening, DPAs, breach runbook)
are listed at the end and are not done by this change.

## 1. Fail-closed workspace resolution (#7)

`workspace.workspace_for_request` used to return the process workspace (the
owner's machine) when anything went wrong while resolving the caller. Now:

| caller | workspace |
|---|---|
| `WORKSPACES_ROOT` unset (single-user) | process, always |
| verified account | that account's workspace |
| admin / local-dev admin / `ADMIN_API_TOKEN` | process |
| no credentials | process (the public exhibit; with `PUBLIC_EXHIBIT=0` the tier gate refuses these first) |
| credentials presented but not verified | **401** |
| identity store unreadable | **503** + `Retry-After` |
| any exception | **500**, logged at ERROR |

The sign-in routes (`/api/me`, `/api/auth/*`, `/api/health`, …) still answer
with a bad token, so the client can learn that its token was rejected. They
get an empty quarantine workspace (`<WORKSPACES_ROOT>/_unresolved`, never
created), not the owner's.

Path traversal has three layers:
1. `safe_paths.TraversalGuardMiddleware` returns 400 for any `/api` request
   with a `..` segment, an encoded slash or backslash inside a segment, a
   control character, or an absolute path or drive letter in a file-like
   query parameter. It decodes up to three rounds of percent-encoding.
2. `safe_paths.check_segment` guards the family path builders (`_die_dir`,
   `_cfg_file`) and the device-card readers.
3. `safe_paths.safe_join` / `is_within` resolve and contain paths. A die
   folder that is a symlink out of its layer is ignored by the layered
   resolver and refused by `_die_dir`.

## 2. Account deletion and export (#5, #6)

The logic is in `account_lifecycle.py` and the routes in `routes/account_data.py`.

* Self-service: `POST /api/account/deletion` needs your password, or a Google
  ID token no more than 10 minutes old. It starts a grace period
  (`ACCOUNT_DELETE_GRACE_DAYS`, default 7) that you can cancel with
  `DELETE /api/account/deletion`. The daily retention job purges requests
  whose grace period has ended.
* Admin: `DELETE /api/auth/users/{email}` and `DELETE /api/admin/accounts/{email}`
  purge immediately. `DELETE /api/admin/invites/{email}` still only withdraws
  the invite and keeps the work (owner's decision, `tests/test_invites.py`).
  Use one of the delete routes above to purge.
* Purge removes: the users.json record, the workspace directory, `published/<ws_id>`,
  every session, agent key, OAuth grant/code/request, the newsletter
  subscriber record and notice reads, and support access requests.
  Pseudonymised to `subj_<hmac16>`: MCP audit, auth events (the IP is
  re-hashed and the UA dropped), usage rows in `history.sqlite`, and the admin
  audit. The account is then added to `deleted_subjects.jsonl`.
* Not covered yet: job-queue history (`jobs`), run history and `cluster`
  records that carry an e-mail. Nothing today links them to a person except
  the e-mail, so they belong in a follow-up.
* **Backups**: restic keeps snapshots for 24 h (hourly), 30 d (daily) and 12 mo
  (monthly). A deleted account therefore survives in backups for at most 12
  months. After **any** restore, run
  `docker compose exec -T api python -m motor_ai_sim.account_lifecycle reapply`.
  If the restore brought back an older register, add `--register <newest copy>`.
  The register holds only hashes and ws_ids, no e-mails.
* Export: `POST /api/account/export` returns a signed link that can be used
  once and expires after `ACCOUNT_EXPORT_TTL_S` (default 900 s). The ZIP holds
  the account record (without the password hash), consents, sessions, keys,
  grants, auth events, MCP audit, support requests, usage, the admin-access
  log, and the whole workspace and published folders. Symlinks are not
  followed, and the size cap is `ACCOUNT_EXPORT_MAX_BYTES` (2 GB).

## 3. Admin audit (#8)

`admin_audit.jsonl` sits beside users.json. It is append-only, mode 0600, and
each line records who, what, when, the target and the subject, with
secret-looking fields scrubbed. It is written by every admin route: tier,
disable, invite, revoke, delete, motor grants, die access, session list and
revoke, auth events, tickets, and support requests, chats and config.
Admins read it at `GET /api/admin/audit` (Admin → Logs). Break-glass: an admin
export of another user's data (`POST /api/admin/accounts/{email}/export`) is
logged with `break_glass: true` when the link is minted and again on download.
The person can list these entries at `GET /api/account/admin_access`.
**TODO**: show that list in the user's account menu as a notice (the API exists).

## 4. Retention (#17)

`python -m motor_ai_sim.retention [--dry-run]` runs daily from
`deploy/systemd/motres-retention.timer` (00:50 UTC, after the usage job).

| data | env | default |
|---|---|---|
| sessions after expiry or revocation | `RETENTION_SESSIONS_DAYS` | 30 |
| auth events (raw) | `RETENTION_AUTH_EVENTS_DAYS` | 90 |
| MCP audit (raw) | `RETENTION_MCP_AUDIT_DAYS` | 90 |
| per-job usage rows (raw) | `RETENTION_USAGE_RAW_DAYS` | 90 |
| daily usage aggregates | `RETENTION_USAGE_AGG_DAYS` | 400 |
| rotated `api.log.*` | `RETENTION_LOGS_DAYS` | 30 |
| visitor chats | `RETENTION_VISITOR_CHATS_DAYS` | 90 |
| admin audit | `RETENTION_ADMIN_AUDIT_DAYS` | 730 |

Monthly usage files in `/srv/motres/usage` are written before raw rows age out.

## 5. File permissions (#9)

The API sets `umask 077` at import (skipped under pytest; opt out with
`MOTOR_AI_KEEP_UMASK=1`). Identity stores are also created explicitly as 0600
(`private_files.open_private`), with directories at 0700: users.json,
`.auth_secret`, `.sessions.json`, auth events, newsletter, MCP audit, every
`json_store` file, visitor chats, the admin audit, and workspace directories.
Files that already exist keep their old modes until they are next written.
Run a one-time `chmod` on the server (see below).

## 6. Logging hygiene

`log_redaction.RedactingFilter` is attached to the root and uvicorn handlers.
It removes bearer tokens, JWTs, `emk_`/`emo_`/`emr_` keys, and
`password=`/`token=`/`secret=`/`api_key=`/`code=` values (key=value and JSON
forms). It replaces every non-loopback IPv4 or IPv6 address with
`ip:<HMAC12>`. Sessions and auth events now store the hashed IP too.
Side effect: the "SMTP not configured" fallback no longer prints a usable
reset or verify link to the log. Approve pending accounts in Admin instead.

## Left for server operations

* Off-site restic repository (Storage Box), a restore rehearsal, and running
  `account_lifecycle reapply` as part of it.
* One-time tightening of existing files:
  `chmod 600 /srv/motres/identity/*`, `chmod 750 /srv/motres/logs`,
  `chmod 640 /srv/motres/logs/*`, `chmod 700 /srv/motres/workspaces/*`.
* Install and enable `motres-retention.timer`, then run a `--dry-run` first.
* Existing raw IPs in `.sessions.json` and `auth_events.jsonl` stay until
  retention or a re-login overwrites them. An optional one-off rewrite script
  is not included.
* LUKS, SSH hardening, `erp` out of the docker group, container `cap_drop` and
  read-only root filesystem, pinned image digests, DPAs and the sub-processor
  list, and the breach runbook (audit P1–P4, P10–P16).
