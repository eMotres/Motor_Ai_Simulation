# Production revision alerts (owner 2026-11-15)

Why: the 40 mm shaft drawing was replaced after release (v1.1/v1.2 without the M5 thread -> v1.3 with it, same file name, same date) by a bulk
update with no published revision and no notice; the factory built 100 shafts without the thread. Owner: «нужно так предупреждать, чтобы и сообщения и
письма разлетались, это очень важно».

## The three rules
1. **Publishing a revision sends** (`production/revision_alert.py`). One mail to every member (owner, engineers, factory; each once), a message in the
   Discussion (the part's thread when the changes name exactly one part, else General), and a record on the revision and in the audit. English with a
   Chinese copy; subject = motor, revision, "action required: acknowledge"; new files and SUPERSEDED files with versions; the sentence that parts must not be
   made to a superseded drawing; the link to the Changes tab; no prices. `POST .../revisions/preview` returns the exact message and recipients; the
   publish dialog shows them before "Send and publish". Scripts publish through `routes._publish_revision` and therefore send too.
2. **No replacement without a revision** (`production/store.py`). A drawing / STEP file of a part that is in a published revision cannot be replaced,
   renamed, removed, archived or overwritten (documents.json, a part's drawing_files / optional_step, the file bytes) by any caller of the store, except inside
   the publish of a new revision with at least one change line (`supersedes` pairs). The old file is kept as "superseded by ..."; downloads of it warn
   first (409 `file.superseded` until `?confirm=1`; the app asks). File names carry the version (`name_v1.3.pdf`).
   *How to replace a drawing properly:* upload the new file under a new name with its version; publish a revision whose `supersedes` names the old and the
   new file (`{old_doc_id, new_doc_id}` for documents, `{part_id, old_file_id, new_file_id, new_name}` for a part's drawing) and has a change line.
   Writing bytes straight into the files folder bypasses the guard: do not.
3. **Acknowledgements and reminders** (`production/revision_ack.py`). Each factory (oem) member acknowledges; the owner and engineers see who has and who has
   not, with dates (Changes tab). A revision published with an alert and still unacknowledged by a factory member after 24 h and after 72 h sends that member
   one reminder each (the level due now; a failed mail is tried again). The owner's daily report has one line per waiting member (old revisions without an
   alert are listed, not mailed). A banner on every Production tab and a chip on the file ("Changed in revision X - not acknowledged") stay until the
   revision is acknowledged.

## Operations
- Hourly reminders: `deploy/systemd/motres-revision-reminders.{service,timer}` (`python -m motor_ai_sim.production.revision_ack --send`); the 06:00 owner
  report run does the same check once a day as a fallback. Without `--send` the command lists who is waiting and who would be reminded.
- Mail goes through `auth_email` (SMTP/relay from `/etc/motres/api.env`). A server without SMTP (every sandbox) sends nothing and records `not_configured`.
  Tests replace `revision_alert.SEND`.
- Audit actions: `publish_revision`, `revision_alert` (recipients, subject, thread), `revision_reminder` (member, level, mail status).
