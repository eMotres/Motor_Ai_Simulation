# Owner queue

One queue in the app (Overview -> Decisions) where agent sessions post what waits for the owner. He answers card by card; the asking
session reads the answer back. **The app only records answers. It never executes anything on an answer.**

## Kinds

| kind | options | needs |
|---|---|---|
| `decision` | 2-5, one may be recommended | |
| `question` | none, answered with a text | |
| `message` | none; the owner answers "Read" or replies | |
| `catalog_write` | fixed by the server: Approve / Reject | `detail`: the full old/new table |
| `outside_message` | fixed by the server: Approve / Reject | `detail`: the final text, and `recipient` (To / CC, or "project thread <id>") |

Limits: question one sentence up to 300 characters, detail up to 4000 (one report = one item, never split), blocks up to 200. A repeat
of an identical open item of the same token and session returns the same id; at most 200 open items per token.

Order: project priority first (`urgent`, `normal`, `low`; a project without an entry, and an item without a project, is `normal`), then
items that block something (decision, question, catalog_write, outside_message) before messages, oldest first in each. Every item the
CLI or the API returns carries its effective `priority`. Only the owner sets a project's priority (on the card, or in the catalog's project
menu); a session may read it (`GET /api/owner-queue/agent/priority?project=<id>`) but cannot change it. The priority orders the queue and
the daily report line; it is not mapped to compute job classes.

## Token and token file

The OWNER creates a token on the Decisions page (it is shown once) and places the file. No agent creates, reads, copies or prints it.

- us1 (Linux): `~/.config/motres/owner_queue.token`
- notebook (Windows): `%APPDATA%\motres\owner_queue.token`

The CLI also accepts `--token-file` and `$MOTRES_OWNER_QUEUE_TOKEN_FILE`. The token is never a command-line argument and is never printed.

## CLI (`scripts/ask_owner.py`, standard library only)

Session name: `--session "US · pump"` or `$MOTRES_SESSION_NAME`. API: `--api` or `$MOTRES_API` (default `https://aerostator.com`).

```
ask_owner.py ask --kind decision --question "Which winding?" --option "A*" --option "B" --project p1 --blocks "next run"   # prints the id
ask_owner.py say --title "Report ready" --detail-file report.md --project p1                                              # a message
ask_owner.py ask --kind outside_message --question "Send the letter?" --detail-file letter.md --recipient "a@example.test"
ask_owner.py wait oq-0123456789ab --timeout 3600 --interval 60      # prints {state, option, text, by, at[, approved_hash]}; exit 3 withdrawn, 4 timeout
ask_owner.py list --state open
ask_owner.py withdraw oq-0123456789ab
ask_owner.py hash --kind outside_message --detail-file letter.md --recipient "a@example.test"
```

A trailing `*` marks the recommended option. `wait` polls with no model in the loop (not faster than every 30 s).

## The rule

Post a question or a report here instead of asking in chat. One report = one item, no splitting.

## Approvals (owner decision 2026-11-25, «3 да»)

For `catalog_write` and `outside_message` the owner's answer on the card counts as his approval, only for the exact content and recipient shown
on the card. On answering, the server stores `answer.approved_hash` = sha256 (hex) of the canonical JSON of `{kind, detail, recipient or null}`
(sorted keys, no spaces, UTF-8), for Approve and for Reject. A session acts ONLY when

1. `state` is `answered`, and
2. `option` is `Approve`, and
3. the hash of the exact content it is about to write or send (`ask_owner.py hash ...`) equals `approved_hash`.

If anything differs it refuses and asks again. The admin's free text, if any, is stored beside the option and is read as an instruction to
re-ask or adjust, never as part of the approval.

## What the app does not do

It executes nothing on an answer: the session reads it and acts under its own rules. Which other kinds may be approved through the queue is
the owner's decision, not set by this feature.
