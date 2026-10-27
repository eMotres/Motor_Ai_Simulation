# Owner's daily report (owner 2026-10-27)

«Каждое утро в 6 часов присылать мне письмо обо всех изменениях по всем проектам, производствам и моторам за день, а также полную
информацию о новых пользователях и об активности пользователей.»  Code: `src/motor_ai_sim/owner_report/`.

An INTERNAL e-mail to the owner only. It is NOT a customer digest. Since the owner decision 2026-10-28 the run that precedes the
report also SENDS the customer digests of every group in mode `auto` (the default; `docs/DIGEST.md`); the report only READS the
digest stores to list what went out and what still waits.

## Content (the 24 h before 06:00 Europe/Ljubljana; text + HTML, no images; links open the exact page after sign-in)

Fixed order (also the order of the top summary line; pinned by `tests/test_owner_report.py`):

| # | Section | Sources |
|---|---|---|
| 1 | **New users** (full record) | `users.json` (created in the window); applicant verification case (status, reasons, application: company, country); NDA record (online / pre-approved `nda_preapproval` / set by admin); platform agreement acceptances; data-sharing consent; project membership index; first sign-in (`auth_events.jsonl`), first modules (`activity.sqlite`), first runs (job usage) |
| 2 | **User activity** | `auth_events.jsonl` (sign-ins, refused tokens), `activity.sqlite` (active time, sessions, modules), job usage (runs, failures), `usage_stats` counters (reports, datasheets, Fusion), 3-D download log, design-assistant ledger |
| 3 | **Projects** | each project's `audit.jsonl` (spec versions, variants linked/unlinked/status/notes, recommended and report-reviewed flags, members invited/claimed/removed, proposals), `thread.jsonl` (messages), stored results of its motors (digest collector), agreement acceptances |
| 4 | **Productions** | each production's `audit.jsonl` (revisions with reason, documents, parts/BOM/costs, members, assembly process, schedule, procurement; the digest collector's lines plus every other audit action), motor-test records |
| 5 | **Motors** | admin audit (`die.*`, `config.*`, `cad.*`, `user.motors`), product revisions (`motor_revisions`), finished and failed runs (job usage), new results with headline numbers (shared `.duty_results.json`) |
| 6 | **Digests** | (a) **sent automatically** in this run (drafts closed as sent by the system actor `auto (daily 06:00)`): group, recipients count, items count, a link to the history entry in Admin -> Digests, the recipients skipped at send time and any failure; (b) the drafts that **wait** (groups in `approve` mode, or an automatic send that failed, flagged `AUTOMATIC SEND FAILED (<reason>)`): group, recipients, the full text exactly as the recipients get it (EN, and the Chinese part), a link to Admin -> Digests for that draft |

A section with nothing says "no changes" (digests: "no digests today"); the whole e-mail is still sent every day (heartbeat). A source
that cannot be read is shown as "source unavailable" with the error, never as "no changes". Staff / customer / factory are marked
on every line that has an actor.

Known limits (said in the e-mail where it matters):
* Raw IP addresses are not stored by design (`sessions.py`, audit 2026-09-29 #16): the report shows the keyed hash of the client
  address ("same client as before?") and the country from the profile / NDA / verification case.
* Active time, sessions and the report / datasheet counters are kept per UTC day: they cover the UTC days that overlap the window.
* There is no event log for "die / configuration created, duplicated, deleted" yet; the Motors section shows what exists (audit
  actions, revisions, runs, results).

## Sending and the schedule

* `owner_report/send.py` is the ONLY module that reaches SMTP (`auth_email.deliver`, the transport the digest uses: the notify helper
  is text-only to `NOTIFY_TO`). **Recipients are locked to the owner:** `OWNER_REPORT_TO` (default: `NDA_NOTIFY_EMAIL` / first
  `ADMIN_EMAILS`, else the notice address) is filtered to administrator addresses (`ADMIN_EMAILS`, enabled admin accounts, the
  notice / NDA admin address). `send_report` has no recipient argument; the routes accept none from the body.
* The mechanism the server already uses for periodic work is a **host systemd timer** that runs a command in the api container
  (`motres-retention`, `motres-usage`; there is no in-process scheduler). So:
  `deploy/systemd/motres-owner-report.{service,timer}` -> `bluegreen.sh exec api python -m motor_ai_sim.owner_report --send` at
  **06:00 Europe/Ljubljana** (retries 07:00 and 08:00, `Persistent=true`).
* **Exactly once per date:** `<shared>/owner_report/state.json` holds `last_sent_date`; the run holds `run.lock` (exclusive file,
  stale after 30 min) while it works. A restart, the retry triggers, a second process or `Persistent` catch-up find the date sent and
  do nothing; a failed send leaves the date unmarked so the next trigger retries. `history.jsonl` keeps one line per sent report.
* **The digests are made (and the automatic ones sent) first.** At the run, before the report is built,
  `digest.approval.run_daily` (the digest package's scheduler function) writes the due DRAFTS for every project and production and
  sends those of groups in mode `auto` through the admin send path (owner decision 2026-10-28); groups in `approve` mode keep their
  draft for Admin -> Digests. Because of this the separate `motres-digest` timer of `docs/DIGEST.md` is no longer needed: install the
  owner-report timer INSTEAD of it (running both is harmless, the run is idempotent). The admin route below makes the drafts but
  sends none (`send_digests=False`).

### Install (orchestrator, on the server; the PR installs nothing)

```bash
sudo cp /opt/motres/app/deploy/systemd/motres-owner-report.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now motres-owner-report.timer
systemctl list-timers motres-owner-report.timer
# dry run: prints the text, sends nothing, makes no drafts
bash /opt/motres/app/deploy/bluegreen.sh exec api python -m motor_ai_sim.owner_report --dry-run
```

SMTP must be configured in `/etc/motres/api.env` (`SMTP_USER`, `SMTP_PASSWORD`; the same as every other mail). Optional:
`OWNER_REPORT_TO=<owner address>`. Check the first morning with `journalctl -u motres-owner-report`.

### "N digests ready to check" notice (owner 2026-10-27)

* When the 06:00 run **creates** digest drafts (a fresh draft, or a new revision that replaces an opened one; a draft only refreshed in
  place is not new), the owner gets a second, short mail right after the report: subject `N digests ready to check`, one line per
  draft (`Project|Production: <title> - <items> items, <recipients> recipients`) and a deep link to
  `/?tab=admin&section=digests&draft=<id>` (opens after sign-in), plus the link to the whole Digests section. It says that nothing was
  sent. It goes through `send.send_notice` - the same module, the same recipient lock as the report, no recipient argument - and
  never to a customer or factory address.
* No new draft = no notice. The new ids are queued in `state.json` (`notice_pending`) before the report is sent and moved to `notified`
  once the notice is out, so a failed notice is retried by the next run and never sent twice; a draft the owner already sent or
  discarded is dropped from the queue unannounced. Digests the run sent by itself (or tried to and failed: the report names those with
  the reason) are never announced by this notice, which is for the drafts that wait for approval.
* The report's own subject carries the counts: `AeroStator owner report <date> · 2 digests sent automatically · 1 digest to approve ·
  1 auto-send problem` (each part only when non-zero).

## CLI and routes

* `python -m motor_ai_sim.owner_report [--date YYYY-MM-DD] [--dry-run] [--html]` prints the report (default); `--send [--force]`
  is the daily run (digests incl. the automatic send, report, mail once per date).
* `GET /api/admin/owner-report/preview?date=` (admin): text + HTML + counts, nothing sent, no drafts made, drafts not marked opened.
* `POST /api/admin/owner-report/send` `{"date": "YYYY-MM-DD"?}` (admin): the daily run NOW (drafts but NO automatic digest send, report, mail to the owner address
  only; it marks the date as sent, so the 06:00 timer will not send that date again).
* Deep links: `/?tab=admin&section=digests&draft=<id>` opens Admin -> Digests with that draft expanded
  (`web/src/lib/adminLink.ts`); project / production lines use the digest deep-link paths (`return_path.py`).
