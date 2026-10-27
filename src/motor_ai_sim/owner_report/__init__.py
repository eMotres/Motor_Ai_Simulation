"""The owner's daily report: one INTERNAL e-mail at 06:00 Europe/Ljubljana (owner 2026-10-27).

Everything that happened in the last 24 hours across all projects, productions and motors, the full record of new users, what
the users did, and the activity-digest drafts that wait for the owner's approval.  It goes to the owner's own address only
(``send.recipients``); it is NOT a customer digest and shares nothing with the ``digest`` package except read-only helpers.

* ``build``    pure: windows, sections, text + HTML from injected sources (no I/O).
* ``sources``  the real readers of the app's stores (users, sessions, audit logs, job usage, revisions, digest drafts).
* ``send``     the ONLY module that reaches SMTP; owner-locked recipients, the once-a-day marker and lock.
* ``routes``   ``GET /api/admin/owner-report/preview``, ``POST /api/admin/owner-report/send`` (admin).
* ``__main__`` ``python -m motor_ai_sim.owner_report [--date D] [--dry-run] [--send]`` (the host timer, docs/OWNER_REPORT.md).
"""
