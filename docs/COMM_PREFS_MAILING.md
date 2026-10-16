# Communication preferences and the launch mailing

MOTRES d.o.o. is an EU controller (GDPR + ePrivacy).  An e-mail cannot itself collect valid consent with checkboxes, so:

1. The launch e-mail is a **one-time service announcement** to account holders (they registered for this service).  It carries a
   button to the **Communication preferences** page and a one-click unsubscribe.
2. On that page (and in the sign-up form) the user ticks opt-in boxes.  **Every box starts unticked.**  Marketing mail goes only
   to accounts that ticked the matching topic, and never to one that unsubscribed.
3. Every e-mail carries an unsubscribe link plus the `List-Unsubscribe` / `List-Unsubscribe-Post: List-Unsubscribe=One-Click`
   headers (RFC 8058).

DRAFT: the e-mail texts and the consent wording are for the owner's (and counsel's) review before anything is sent.

## Topics

| id | what the user ticks |
|----|---------------------|
| `product_updates` | release notes and new features |
| `new_motors` | additions to the catalogue and new passports |
| `engineering_content` | articles, webinars and case studies |
| `co_development` | beta programmes and invitations to build modules together |

## How consent is stored (`src/motor_ai_sim/comm_prefs.py`)

Under the identity root (the directory of `MOTOR_AI_SIM_CONFIG`; `/srv/motres/identity` on the server, the persistent mount, next
to `users.json`):

* `comm_prefs.json` - the current state per account: `{topics{4 x bool}, unsubscribed_all, updated, text_version}` (0600).
* `comm_prefs_history.jsonl` - append-only, one line per change: user, event (`set` / `unsubscribe_all`), topic, value, previous
  value, server timestamp (`ts`, `iso`), `text_version` + `text_sha256` of the wording shown (in the language shown, `lang`),
  `source` (`prefs_page`, `email_link`, `unsubscribe`, `signup`, `google`) and the client IP.  The history is written first; if it
  cannot be written nothing changes.  The state can always be rebuilt from it.
* The wording (intro + four lines, EN and zh-CN) lives in `web/src/locales/*/common.json` (`commPrefs`) and as constants in
  `comm_prefs.py`; the SHA-256 is of the canonical JSON of the server constants and `tests/test_comm_prefs.py` fails when the two
  differ, so the hash always names the text on screen.  Bump `TEXT_VERSION` when any wording changes.
* "Unsubscribe from all" switches every topic off and sets `unsubscribed_all` (also stored when nothing was ticked).  Ticking a topic
  later is a fresh consent and clears the flag.
* **The old single Newsletter is retired (2026-10-15).**  There is no Newsletter toggle (account menu > Notifications now lists
  only in-app notices), no subscribe / confirm route, no campaign composer, no send queue.  The sign-up forms and the API still
  accept the old `newsletter` flag but ignore it.  The old `newsletter.json` records stay as READ-ONLY history in the Admin
  "Mailing" card (counts, past campaigns, CSV of the old consent records).  Legacy subscribers are NOT moved into the new topics
  (their consent wording differs); the launch e-mail invites them to choose.  The one-click unsubscribe of OLD mails still works
  (`POST /api/newsletter/unsubscribe`) and also switches every new topic off; an unsubscribe on the new side ends the old record
  too, so the two stores never disagree.  In-app notices (the bell) are unaffected: they are not marketing.
* Account deletion removes the state and pseudonymises the history (address -> `subj_...`, IP hashed): the proof of consent
  stays, the person does not.  The data export contains `communication_preferences.json`.

## Links and tokens

E-mail links carry a signed token, not an address: `<b64url(json{u,p,e})>.<b64url(HMAC-SHA256)>` with `u` = a pseudonymous account
id (an HMAC of the address, so the address never appears in URLs, Referer or proxy logs), `p` = purpose (`prefs`), `e` = expiry
(90 days).  Key: env `COMM_PREFS_SECRET`, else derived from the application's session secret (`AUTH_SECRET` /
`.auth_secret`); nothing is committed.  Rotating the secret invalidates every mailed link.  Whoever holds the link can manage that
one account's four preferences and unsubscribe, nothing else.

| route | who | what |
|-------|-----|------|
| `GET/PUT /api/me/comm-prefs` | signed in | read / change (`{"topics": {...}, "lang"}` or `{"unsubscribe_all": true}`) |
| `GET/PUT /api/comm-prefs/{token}` | link | the same, address masked |
| `POST /api/comm-prefs/{token}/unsubscribe` | link, no session | one click, everything off; the RFC 8058 target |
| `GET /api/admin/comm-prefs/summary` | admin | opted-in counts per topic, unsubscribed count, last campaign |

Bad or expired links are counted per IP (30 per 10 minutes, then 429).  The page is `/preferences` and `/zh-cn/preferences`
(`?t=<token>`; `&action=unsubscribe` unsubscribes on load, in JavaScript only, so a link scanner that merely fetches the page cannot
unsubscribe anybody).  nginx serves it with `X-Robots-Tag: noindex` and `Referrer-Policy: no-referrer`; `robots.txt` disallows it.

The page also shows the NDA state: signed; "Sign the NDA online" when online signing is open (the existing NDA dialog); "Request the
NDA" when it is not (a support ticket of type `account`, the existing path); "Sign in to ..." for a link visitor who is not signed
in.

## The launch mailing (`scripts/send_announcement.py`)

Templates: `config/mail/launch_2026-10-19.en.md` and `.zh-CN.md` (first line `Subject:`, Markdown, `=> [Label](url)` = button, text
after `---` = footer).  Plain text plus table HTML with inline styles; no images, fonts or trackers.

```
# 1. dry run (the default): who would get it, one .eml per user, nothing sent, no log written
PYTHONPATH=src python scripts/send_announcement.py [--out-dir DIR] [--users-file COPY_OF_users.json]

# 2. one test copy to an address (links work when it is an account); [TEST] in the subject; never counts as sent
PYTHONPATH=src python scripts/send_announcement.py --test-to you@example.com

# 3. the real send; --confirm must equal the "would send" number of step 1
PYTHONPATH=src python scripts/send_announcement.py --send --confirm 14
```

On the server run it with the API's environment (`MOTOR_AI_SIM_CONFIG`, `SMTP_*`, `PUBLIC_APP_URL`, `AUTH_SECRET` or
`COMM_PREFS_SECRET`) so it reads the real users and signs the links with the real key.

* Audience: verified, enabled accounts that did not unsubscribe (communication preferences or legacy newsletter).
* Language: the account's stored UI language, else the language of its last preference change, else English (`--default-lang`).
* One message per account per campaign id: `<identity root>/mailing/<campaign>.sendlog.jsonl` (user, time, message-id; an `attempt`
  line is flushed before each SMTP call, `sent` / `failed` after).  An unresolved `attempt` (a crash between SMTP and the log) is
  skipped, never retried blindly; `--retry-failed` retries only definite failures.
* 3 seconds between messages (`--delay`), stops after 5 failures in a row, refuses to start while a `.lock` of the same campaign
  exists, refuses without SMTP settings.
* Admin > Mailing & notices shows the **Mailing** card (opt-ins per topic, unsubscribed count, last campaign from the log, the old newsletter as history) and the in-app notices panel.
