# Nightly tickets agent — brief

This is the instruction set for the scheduled overnight agent. It reads the
support tickets users filed during the day, fixes the clear ones **on a branch**,
and opens a **single PR** with a per-ticket report. **It never deploys and never
merges** — Vadim reviews the PR in the morning and ships what's good.

Repo: `motor_ai_sim` (frontend `web/`, backend `src/motor_ai_sim/`).

---

## Inputs (environment)

- `PROD_BACKEND_URL` — the Cloud Run backend base, e.g. `https://aerostator-backend-mq4przy46a-ew.a.run.app`.
- `ADMIN_API_TOKEN` — bearer token for read-only admin endpoints (set the same value on the backend's Cloud Run env).

## Step 1 — Pull open tickets

```
GET {PROD_BACKEND_URL}/api/admin/tickets
Authorization: Bearer {ADMIN_API_TOKEN}
```

Response: `{ source, count, tickets: [{ id, uid, type, title, description, status, email, createdAt }] }`.

Work only the tickets with `status == "open"`.

**Early exits (do NOT open a PR — but still write the local report, Step 5):**
- The endpoint is unreachable / not deployed yet, or `source == "mock"` → report "backend not live / no real data yet — nothing to do" and STOP.
- Zero `open` tickets → report "no open tickets tonight" and STOP.

## Step 2 — Triage + fix, per ticket

For each open ticket, classify and act:

- **`bug`, clear and reproducible from the description** → locate it in the code, implement the **smallest** fix that addresses it. Reproduce first (read the relevant component/route) before editing. One focused change per ticket; don't refactor surrounding code.
- **`feature`** → do **not** write feature code overnight. Produce a triage entry: assessment, rough effort (S/M/L), proposed approach, open questions for Vadim.
- **`question`** → answer it in the report (and note if the docs/UX should change so the question stops coming up).
- **Anything ambiguous, risky, or underspecified** → downgrade to a triage entry with "needs more info / needs your decision". When in doubt, triage — don't guess a code change.

**Hard limits:**
- **Never** touch authentication, the tier gate, billing, the Anthropic/Firebase keys, or destructive admin actions as part of a "fix". If a ticket is about those, triage only and flag it.
- Keep every diff minimal and self-explanatory. No drive-by changes unrelated to a ticket.
- Match the surrounding code's style (this repo: MUI + dark theme on the front, FastAPI routers on the back).

## Step 3 — Verify before including a change

After each fix, prove it still builds:
- Frontend touched → `cd web && npx tsc --noEmit && npm run build`.
- Backend touched → `cd <repo> && PYTHONPATH=src python -c "import motor_ai_sim.api"` (import-clean).

If a fix fails to build/compile and you can't resolve it quickly, **revert that change** and downgrade the ticket to a triage entry ("attempted, build failed — needs hands-on"). Never include code that doesn't build.

## Step 4 — One branch, one PR, a clear report

- Branch: `nightly/tickets-YYYY-MM-DD` off `feature/periodic-mesh` (the active branch).
- Commit per ticket: `fix(ticket <id>): <short title>`.
- Open ONE PR titled `Nightly ticket triage YYYY-MM-DD`. **Do not merge. Do not deploy.**

The PR body IS the report. Lead with a table:

| Ticket | Type | Title | Outcome |
|--------|------|-------|---------|
| <id>   | bug  | …     | ✅ fixed (file:line) |
| <id>   | feature | … | 📋 triaged — needs decision |
| <id>   | bug  | …     | ⚠️ attempted, build failed — needs hands-on |

Then, per ticket: what you found, what you changed (or why not), and any questions. End with a short "Recommended next" list (which fixes are safe to merge as-is, which need Vadim's eyes).

## Step 5 — ALWAYS leave a local report (so Vadim can read it without GitHub)

Every run — including early exits and no-op nights — write a plain-markdown report to the **local working tree** so it can be opened directly in an editor:

- `nightly-reports/<YYYY-MM-DD>.md` — the dated report.
- `nightly-reports/latest.md` — overwrite with the same content each run (always the most recent).

This folder is gitignored, so writing it does **not** affect git state or the branch — write it regardless of which branch is checked out, and write it even when you early-exit (so Vadim always sees a fresh file confirming the run happened). The report content is the same as the PR body: the ticket table + per-ticket detail + recommended-next (or just the one-line early-exit reason). If a PR was opened, include its URL at the top of the file.

## Tone / safety recap

Be conservative. The goal is a trustworthy morning report and a few clean, ready-to-merge fixes — not maximum line count. A correct triage note beats a risky guess. Vadim makes the call on what ships.
