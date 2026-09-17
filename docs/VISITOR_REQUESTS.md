# Visitor requests — how what a visitor tells the bot reaches the team

The landing page shows the "Help & feedback" assistant to a **signed-out**
visitor. Until 2026-09-17 that conversation ended in their browser: the provider
answered, the widget printed it, and nobody on this side ever learned that
somebody had stood in the doorway and asked how to get in.

Three mechanisms close that loop. All of them apply to **anonymous callers
only** — a signed-in user's chat is never logged here; they have a name, a
Report tab and a session log already.

```
visitor types  →  POST /api/support/chat  →  provider
                        │
                        ├── every turn → config/support/visitor_chats/<day>.jsonl
                        │
                        └── reply ends with [[ACCESS_REQUEST: …]]?
                                 ├── marker STRIPPED from what the visitor reads
                                 ├── record → config/support/access_requests.json
                                 └── e-mail to vadim@motresres.com (if configured)
                                            ↓
                            Admin tab → "Visitor requests" → Invite
```

## 1. The visitor chat log

`src/motor_ai_sim/support_store.py` appends one JSON line per visitor turn to
`<config>/support/visitor_chats/<YYYY-MM-DD>.jsonl`, beside `users.json` —
never inside a per-user workspace.

| field | meaning |
|---|---|
| `ts` | epoch seconds |
| `conv` | conversation id — `sha1(ip_hash + the first user message)`, so the turns of one visit group together without a cookie |
| `ip_hash` | **salted hash** of the address, 12 hex chars. The address itself is never stored |
| `ua` | user agent, cut to 300 chars |
| `user` / `reply` | the question and the answer the visitor actually saw (marker already removed) |
| `source` / `model` | `gemini` / `claude` / `mock` / `error` / `rate_limited`, and the model |
| `limit` | which cap refused this turn (`ip_burst`, `ip_day`, `global_day`), else `null` |

* one day file stops growing at `DAY_MAX_BYTES` (4 MB) and says so once in the log;
* files older than `RETENTION_DAYS` (90) are deleted on the next append;
* nothing in the module raises — a failed write costs one warning and the
  visitor still gets their answer.

Read it in the Admin tab ("Visitor chats"), or over
`GET /api/admin/support/visitor_chats?day=YYYY-MM-DD`.

## 2. The access-request marker

The provider call is one plain text completion with **no tool API**, so the only
channel the model has back to us is the text itself. `VISITOR_NOTE` (in
`routes/support.py`) tells the assistant: when a visitor wants access, a quote or
to be contacted, collect their details one question at a time — name, company,
work e-mail, what they want to do — and as soon as there is a valid e-mail,
confirm in one sentence and end the message with

```
[[ACCESS_REQUEST: name="…"; company="…"; email="…"; note="…"]]
```

`parse_access_request()` is what enforces it:

* the marker **never reaches the visitor** — parsed, malformed, unterminated or
  written three times, it is cut out along with the blank line it sat on;
* a marker **without a valid e-mail files nothing**. An address nobody can
  answer is the row that teaches an inbox to be ignored;
* several markers → the **last valid one** wins;
* quoting is forgiving (`"…"`, `'…'` or bare), unknown keys are ignored;
* if the marker was the whole message, the visitor gets
  `support.ACCESS_CONFIRMATION` instead of an empty bubble.

The record lands in `<config>/support/access_requests.json`:

```json
{"req_ab12cd34": {
  "id": "req_ab12cd34", "ts": 1758100000.0, "updated": 1758100500.0,
  "name": "Jane Doe", "company": "ACME Robotics", "email": "jane@acme.com",
  "note": "40 mm robot joint, needs a quote", "status": "new",
  "ip_hash": "9f2ac41b7e05", "ua": "Mozilla/5.0 …", "merged": 2,
  "transcript": [{"role": "user", "content": "…"}]}}
```

A second request from the **same e-mail inside 24 h merges** into the existing
record (only non-empty fields overwrite, `merged` counts the messages) — the
visitor who adds their company one turn later is the same person.

## 3. The alert

`src/motor_ai_sim/notify.py`. The old note "the host blocks outbound SMTP" is
half true: **25 and 465 are blocked on the Hetzner box, 587 (submission,
STARTTLS) is open** — verified against `smtp.gmail.com` and
`smtp.office365.com` on 2026-09-17. `motresres.com` is on Google Workspace, so
the notifier is plain `smtplib`.

* **one e-mail per new access request** — subject
  `AeroStator Core: access request from <name> (<company>)`, body = the contact
  fields plus the whole conversation, `Reply-To:` **the visitor's address**, so
  answering the alert answers the person;
* **one daily digest** with the visitor-chat count — only for a day that had
  chats and produced **no** requests (the requests were already mailed; a
  summary of mail the owner has read is the noise that kills an alert);
* sent from a daemon thread with a 10 s socket timeout: a visitor never waits on
  a TLS handshake, and a mail server having a bad minute never costs them an
  answer. The admin inbox is the record either way.

### What the owner has to do, once

1. The Google account that will send (vadim@motresres.com, or a dedicated
   mailbox) needs **2-step verification ON**.
2. Create an app password: <https://myaccount.google.com/apppasswords> → 16
   characters, shown once.
3. Add to `/etc/motres/api.env` and restart the API:

```ini
SMTP_USER=vadim@motresres.com
SMTP_PASSWORD=<the 16-character app password>
# optional — these are the defaults:
# SMTP_HOST=smtp.gmail.com
# SMTP_PORT=587
# NOTIFY_FROM=<SMTP_USER>
# NOTIFY_TO=vadim@motresres.com        (comma-separated for several)
```

With `SMTP_USER` / `SMTP_PASSWORD` unset the notifier logs
`notify: smtp not configured` and does nothing — the Admin tab still fills.

A **Telegram push** is kept as an equally optional second channel
(`TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`, from @BotFather and @userinfobot).
It costs nothing when unset, and it is the one channel that survives the mailbox
the alerts are about.

## 4. The Admin tab

`web/src/components/admin/VisitorRequests.tsx`, between the sessions section and
the support tickets:

* **Visitor requests** — newest first: who, what they want, when, the IP-hash,
  a status (`new` → `contacted` / `invited` / `declined`), the transcript on
  expand, and **Invite**, which opens the existing invite dialog already
  addressed to them. The tab label carries a badge with the number of `new` ones.
* **Visitor chats** — the daily logs, read-only, one block per conversation,
  with a chip on any turn the rate limiter refused.

## Routes (all admin-only)

| route | does |
|---|---|
| `GET /api/admin/support/requests` | `{count, new, requests[]}`, newest first |
| `PATCH /api/admin/support/requests/{id}` | `{"status": "contacted"}` |
| `DELETE /api/admin/support/requests/{id}` | drop one row (a test, a duplicate) |
| `GET /api/admin/support/visitor_chats?day=` | one day, grouped into conversations |

401 for an anonymous caller, 403 for a signed-in non-admin. Deliberately **not**
`require_admin_or_token`: the static `ADMIN_API_TOKEN` exists for the headless
tickets agent, and a visitor's conversation has no business being readable by a
shared static string.

## Tests

`tests/test_support_visitor_requests.py` — the marker (present / absent /
malformed / multiple / e-mail validation / stripping), the log (written for a
visitor, **not** for a signed-in user, capped, pruned, never raising), the
merge window, the four routes with their 401/403, and the notifier with
`smtplib.SMTP` replaced by a recorder (STARTTLS before login, headers, body,
and that the send does not block the reply). Nothing here opens a socket.
