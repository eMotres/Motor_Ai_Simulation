# Activity digests (owner 2026-10-26)

One short e-mail per working group - a development project or a production - saying what changed since the last digest. Code:
`src/motor_ai_sim/digest/`; web: `web/src/components/digest/`, `web/src/components/admin/sections/DigestsSection.tsx`.

## Rules (they override the first brief)

1. **Automatic daily send (owner decision 2026-10-28, reverses the 2026-10-26 "no automatic mode" rule): "all projects and
   productions get an automatic mailing at 6 a.m. Slovenian time, but only if something changed during the day".** A group's mode is
   `auto` (default), `approve` or `off`.
   * `auto`: the daily scheduled run (`approval.run_daily`: first step of the owner report at 06:00 Europe/Ljubljana, or
     `python -m motor_ai_sim.digest run`) builds the draft and SENDS it right away, through the same private `approval._send` as the
     admin Send (recipients re-checked against today's state - left the group, unsubscribed, disabled, lost access -, nobody added, the
     send log, the unsafe-subject guard, the 14-day max age, partial retry). The approver recorded in the draft and in `log.jsonl` is
     the system actor `auto (daily 06:00)`. A group where nothing changed in the period has no draft and gets nothing.
   * `approve`: the draft waits in Admin -> Digests, where the owner previews it and clicks **Send** (after a confirmation) or
     **Discard**; the send needs the approving admin's address and `POST /api/admin/digests/{id}/send` is its only caller
     (`{"approve": true, "content_hash": "..."}` - the SHA-256 of subject + plain text + HTML + recipient list that the preview showed;
     any other hash is a 409).
   * `off`: no draft.
   * **Only the scheduled run sends automatically.** Page loads, `POST /api/admin/digests/run` ("Make drafts now") and
     `build.run` itself only write drafts and never reach SMTP; `python -m motor_ai_sim.digest run --drafts-only` and `--dry-run` send
     nothing either. `tests/test_digest.py` pins this (static guard: one sending module, the routes never call the automatic send).
   * A draft that was opened or previewed is never rewritten: a refresh makes a new draft (next revision) and marks the old one
     `superseded`; a draft nobody opened is refreshed in place. A send that partly failed leaves the draft `partial`; the next
     scheduled run (auto) or the next approval (approve) retries only the recipients that did not get it. A total failure leaves the
     draft `pending` with `last_error` (`by: auto (daily 06:00)`), the next run tries again, and the owner report names it.
   * Settings shape: `settings.json` is `{scope: {mode, frequency, recipients, chosen, optouts, mode_chosen?, updated_at, updated_by}}`.
     There is no separate global record: the default of a group with no stored mode is `DEFAULT_SETTINGS["mode"]` = `auto`, and a
     per-group `approve` / `off` is the override. **Migration (once, `store.migrate_auto_default`, marker in `migrations.json`):** before
     2026-10-28 a stored `approve` was only the old default (any opt-out or frequency change stored the whole record), so such a record
     becomes `auto`; a record whose settings log shows a change TO `approve` (a deliberate choice) or whose mode is `off` stays and gets
     `mode_chosen: true`. From then on every mode set through the settings route is marked chosen.
2. **One text per group per period.** The text contains only what EVERY recipient of the group may see (lowest common access): no cost
   sheet unless costs are shared with the factory, no internal documents or notes, no trial variants for customers. If the group has
   Chinese-speaking members (the factory) the single e-mail carries English first, then Chinese (the existing bilingual fields;
   machine-translated text is marked `[machine-translated]` / `[机器翻译]`). Only the footer (own unsubscribe and preferences link)
   is personal.
   Motor lines (new results, step notes, recommended variant) are in the text only when EVERY recipient may open that die/config
   (`motor_access`, evaluated per named account); this is re-checked at send time and a change is a 409 ("make new drafts").
   Computed points are read from the layers all members share (the die's and the shared catalog's ledgers, not any one workspace's).
3. **Text and HTML only** - no images, no attachments. One line per item (at most 20, then "and N more"), each with a link to the exact
   page in the app.
4. **A period with no activity makes no digest** (an idle group is never mailed, in any mode). One draft per (group, period start);
   running twice refreshes the same draft; after a send or a discard the next period starts where the last ended.
5. **Links open the exact page after sign-in.** See "Deep links" below.

## Settings (`digest/store.py`)

Per group `{mode: auto|approve|off, frequency: daily|weekly, recipients: all|chosen, chosen: [...], optouts: [...]}`; every change is logged
(who, when, what) in `log.jsonl`. A project's owner or an admin changes a project's settings (project page, "Updates by e-mail");
an admin changes a production's (production page). A member leaves one digest with the link in the e-mail footer (or
`PUT /api/digest/mine`); "unsubscribe from all e-mail" (communication preferences) also stops it. A factory member without a signed
NDA is not a recipient (the production page itself is closed to them).

Store: `<shared root>/digests/` (`MOTRES_DIGEST_ROOT` overrides): `settings.json`, `state.json`, `drafts/<id>.json`, `log.jsonl`
(append-only), `sendlog/<id>.sendlog.jsonl` (the mailing send log: a person with a `sent` line is never mailed again for that draft).
At send time the recipients are re-checked against today's state (left the group, unsubscribed, lost access -> skipped and named).

## Readable lines (owner 2026-10-27)

The item lines are written for a customer or a factory reader (`digest/readable.py`, one line per item, English source with a Chinese
mirror where the digest is bilingual): numbers in plain notation with thousands separators (`1 770 N·m`, never `1.77e+03`);
results say what the run answered - `holds continuously: winding 137 °C, magnets 82 °C`, `reaches the winding limit (200 °C) after
2 min 17 s from cold, 33 s after cruise`, the torque target held or not reached - and the duties of one variant stand together under one
heading with one link; production parts are named with what changed (new part, revision `1.0 → 1.1`, material / specification edited,
removed; capped with `+N more`) and documents are counted per part (`Stator assembly: 2 photos, 5 STEP files added`). Internal
documents keep their own `internal` line, so the lowest-common-access filter drops them for customers and the factory.

## Scheduler

> Since 2026-10-27 the digests are run by the owner's daily report run at 06:00 Europe/Ljubljana (`docs/OWNER_REPORT.md`,
> `deploy/systemd/motres-owner-report.*`); since 2026-10-28 that run also SENDS the groups in `auto` mode and lists what went out and
> what still waits in the e-mail to the owner. If that timer is installed the `motres-digest` timer below is not needed.

`python -m motor_ai_sim.digest run [--dry-run] [--drafts-only] [--scope project:<id>]` makes the due drafts, sends those of groups in
`auto` mode and prints what it did (`--drafts-only` and `--dry-run` send nothing). Install on the server (NOT installed by the PR) as
a systemd timer, only if the owner-report timer is not used:

```ini
# /etc/systemd/system/motres-digest.service
[Unit]
Description=AeroStator activity digests: make the drafts and send the automatic ones
After=network-online.target

[Service]
Type=oneshot
User=motres
EnvironmentFile=/etc/motres/api.env
WorkingDirectory=/opt/motres/app
ExecStart=/usr/bin/docker compose -f deploy/docker-compose.yml exec -T api python -m motor_ai_sim.digest run
Nice=10

# /etc/systemd/system/motres-digest.timer
[Unit]
Description=Daily activity digests

[Timer]
OnCalendar=*-*-* 06:00:00 UTC
Persistent=true
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
```

(The `ExecStart` line must match how the API container is run on the server; the command inside the container is the same.)
Quiet groups are skipped silently; `--dry-run` shows the plan and writes nothing. Admin -> Digests has a "Make drafts now" button
that only writes drafts (it never sends, whatever the mode).

## Deep links (`return_path.py`, `web/src/lib/appLink.ts`)

A link in a digest opens the exact page: `/projects/<id>[/<view>][?die=&config=&item=]` (views: specification, motors, messages,
notes) and `/production/<die>/<config>[/<tab>][?item=]` (tabs: bom, tooling, assembly, timeline, documents, discussion, proposals,
changes).

How the target survives a visit by someone who is not signed in:

* the web app has no router; the address bar IS the link. A signed-out visitor sees the landing with the sign-in dialog opened on that
  URL (it was already so for the documents link; now for every app link). Google and e-mail+password sign-in happen in the same page,
  so the bar is untouched and the workspace reads the link when it mounts (Production tab / Development page open the named tab,
  project, motor step, revision or proposal).
* registration: the canonical path travels as `next` through the confirmation mail to the "e-mail confirmed" page, whose buttons lead to
  it (`return_path.safe_app_path`, server-side re-validated on every use; **this was the gap**: only the production documents link
  was accepted, everything else was dropped).
* a remembered copy of the canonical target is kept in the tab's sessionStorage and put back after sign-in if the bar lost the link.
* the NDA step of a production is inside the page on the same URL, so it does not lose the target.
* only same-origin paths of these exact shapes are accepted (no URL, no `//host`, unknown query keys dropped): no open redirect.
