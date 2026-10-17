# Applicant verification before confidential 3-D / STEP access

Owner spec 2026-10-15. **Signing the NDA online no longer opens the confidential 3-D files by itself.**  Access needs the NDA **and**
an applicant verification that ends VERIFIED (or an administrator's approval).  Phase 1 (this document): the gate, the case store,
deterministic checks, the decision engine, the applicant correspondence, the admin queue.  Phase 2 (hooks only): an LLM research
step that gathers cited evidence, a China registry.

Code: `src/motor_ai_sim/verification/`, routes `src/motor_ai_sim/routes/verification.py`, web `web/src/lib/verification.ts`,
`components/model3d/VerificationStatus.tsx`, `components/admin/VerificationPanel.tsx`.

## What is gated

One rule, in one place: `model3d.can_download(identity, user_rec)` -> `verification.access_ok`.

| Caller | Access to tools, parts list, BOM / parameter files, STEP |
|---|---|
| owner / admin | yes |
| NDA flag set **by an administrator** (Admin > Users) | yes (the administrator vouched for the account; grandfathered) |
| NDA signed **online**, case VERIFIED | yes |
| NDA signed online, any other state (PENDING, MORE_INFORMATION_REQUIRED, MANUAL_REVIEW, DENIED, no case, store unreadable) | **no** (403 with a text that names the missing verification) |
| no NDA | no (as before) |

Viewing the model (rotate, zoom) stays open for everybody who may see the motor.  The web follows the server flag: `GET /api/model3d/models`
returns `can_download` and `access` (`state` = `ok | nda | in_progress | need_info | denied`).  The 3-D tab replaces "Sign NDA" with
"Verification in progress" / "We need one more thing from you" + a link to the personal reply page; Geometry > Files shows the same
status instead of reopening the NDA form.  EN + zh-CN.

A case that was decided earlier keeps deciding: the newest VERIFIED / DENIED case governs; a newer case still open (a re-signature of a
newer NDA version) does not take access away.  An account that signed online before this system existed gets its case opened silently the
first time it looks (no mail from a GET) and the status "we need one more thing" until it is verified.

## Flow

1. **NDA form** (`NdaDialog`) asks, besides the signed fields, for the **official website**, the **purpose**, an optional **VAT number**
   and a **work e-mail** (default: the account e-mail).  The server accepts them as optional (a gap is a question, never a refusal) but
   refuses malformed values (422 per field).  `POST /api/nda/sign` stores the signature as before, then `verification.on_nda_signed` opens a
   case (the signature is never undone by a verification failure) and runs the checks.
2. **Checks** run in a background thread (registries are slow): every applicable source returns `Evidence`; all of it is stored with
   source URL / API, the raw (clipped) answer and the time.  Network failures are `unknown`, never `pass`; results are cached per case
   for 6 h so a re-run does not hammer the registries.
3. **Decision** (`engine.decide`, pure) -> status + reasons + what to ask.
4. **Correspondence**: the applicant gets a mail (sender `MAIL_FROM`, support@aerostator.com) that asks for the SPECIFIC missing proof and
   links to the **reply page**.  English, with the zh-CN text below it for Chinese readers.  Each reply re-runs the checks.
5. **Admin > Verification**: the queue; the case with all evidence; approve / ask for more / deny / re-run.

## Statuses

| Status | Meaning | Access |
|---|---|---|
| `PENDING` | checks are running | no |
| `VERIFIED` | company confirmed AND person linked (rules below), or approved by an administrator | **yes** |
| `MORE_INFORMATION_REQUIRED` | the company is confirmed but the person link is weak, or a fixable gap exists; the applicant was asked | no |
| `MANUAL_REVIEW` | goes to the admin queue (an e-mail to `NDA_NOTIFY_EMAIL` / first `ADMIN_EMAILS`) | no |
| `DENIED` | an administrator closed the request (the system never denies) | no |

**When uncertain: no automatic access and no automatic rejection.**

## Signals and scoring

Company (all of it must come from a registry-grade source, `config.REGISTRY_GRADE_SOURCES` = GLEIF, UK Companies House, EU VIES):
a website alone is **not** a company confirmation.

Person-to-company link: independent signals, counted **once per family**:

| Signal | Strength | Family |
|---|---|---|
| applicant is a current officer in the register (Companies House) | strong | registry_officer |
| applicant is named on the official website *(a website tied to the company)* | strong | site |
| a verified e-mail on the official website's domain *(a website tied to the company)* | strong | email |
| the same facts when the website is **not** tied to the company | supporting | site / email |
| a third-party page (press, conference) naming person and company | supporting | third_party |
| a LinkedIn profile address whose slug carries the name (not fetched) | supporting | linkedin |

A website is **tied to the company** when it resolves, names the company, and (the domain resembles the company name, e.g. `siemens.com` /
"Siemens AG", or the page shows the registration / VAT number).  This stops `fast-parts-shop.com` with "Siemens AG" written on a page
from turning a verified e-mail into a strong signal (`config.REQUIRE_DOMAIN_COMPANY_LINK`).

**VERIFIED** = company confirmed by a registry-grade source **and** `>= MIN_PERSON_SIGNALS` (2) independent signals, `>= MIN_STRONG_PERSON_SIGNALS` (1) strong,
**and** the role is plausible (engineer, R&D, CTO, procurement, BD, CEO ...; `config.PLAUSIBLE_ROLE_WORDS`).  LinkedIn / press are never enough alone.

**MORE_INFORMATION_REQUIRED** - the company is confirmed but the person link is weak, or a gap: free or disposable mailbox, e-mail domain differs
from the website, no or unreadable website, unverified account e-mail, company not confirmed yet while a registration number / VAT number /
website was never asked for (asked once), an unusual job title (asked once).

**MANUAL_REVIEW** - any source contradicts the application (a conflict: other country, registration number differs, entity not active, VAT
number of another country); the company is unidentifiable after the applicant gave everything; no registry exists (China); the applicant
replied `MAX_OPEN_REPLIES` (3) times and it is still open; the role explanation did not settle it; an adapter raised an exception.

Spec examples as tests (`tests/test_verification_engine.py`): `siemens.com` verified = a strong signal (but one signal -> more information);
gmail + "works at Siemens" = more information; website-only company = not registry-confirmed; LinkedIn-only = not verified.
Every decision stores its reasons (`code` + text); the thresholds are in `verification/config.py` only.

## Sources (adapters)

| Source | Applies | What it does | Needs |
|---|---|---|---|
| `email` | always | account e-mail verified, free / disposable mailbox (free list + disposable list + `VERIFY_DISPOSABLE_FILE`), domain exists (`email_domain`), MX (stdlib DNS), e-mail domain == website's registrable domain | - |
| `website` | a website was given | homepage + `/about /team /impressum /imprint /contact ...` (7 requests max, robots.txt respected, 6 s, 400 kB each, public addresses only): names the company, shows the registration / VAT number, names the applicant | - |
| `gleif` | always | `lei-records?filter[entity.legalName]=` (fulltext fallback): name, status ACTIVE, country, registration number | - (public API) |
| `companies_house` | country GB | company by number / name, `active`, officers list -> officer match | `COMPANIES_HOUSE_API_KEY` (else `not_configured`, never fails) |
| `vies` | VAT number given, EU | valid number; the company name is compared when the member state discloses it ("---" -> `unknown`) | - (public API) |
| `applicant_urls` | links on the reply page | LinkedIn address (not fetched); page on the official domain naming the applicant (strong); third-party page naming both (supporting) | - |
| `china_registry` | country CN | no free registry API: evidence `manual`; the case goes to MANUAL_REVIEW and a business-licence document is requested | - |
| `llm_research` | **off** (phase 2) | contract only: gathers cited evidence (URL + quotation), `advisory = True`: capped at "supporting", can never confirm a company, never decides | - |

Matching: company names compare after accents, punctuation and legal forms are removed (`Siemens AG` = `Siemens Aktiengesellschaft`, `B.V.` = `BV`);
>= 0.92 similar = match, 0.75-0.92 = ambiguous (recorded, never confirmed).  Person names: first and last token within five words of each other.
Registrable domain (eTLD+1) uses a small vendored suffix subset plus a conservative `xx.co/com/org/...` rule (no dependency).

**Every user-typed URL** goes through the SSRF guard (`net.check_public_url`): http(s) only, port 80 / 443, no credentials, and every address
the host resolves to (also after each redirect) must be global - no loopback, private, link-local or reserved range.  Fetched text is never
shown to anybody, only turned into yes / no findings.  Residual risk: DNS rebinding between the check and the connection.

### How to add a registry or API

```python
# src/motor_ai_sim/verification/sources/my_registry.py
from motor_ai_sim.verification import evidence as EV
from motor_ai_sim.verification.evidence import Evidence
from motor_ai_sim.verification.sources import Context, VerificationSource, register

class MyRegistry(VerificationSource):
    name = "my_registry"                        # shown in the admin view; add it to config.REGISTRY_GRADE_SOURCES if it is an official register

    def applies(self, country: str, data: dict) -> bool:        # cheap, no I/O; data = the case view (application, emails, urls ...)
        return country == "XY"

    def run(self, case: dict, ctx: Context) -> list[Evidence]:  # never decide, never raise on a network problem (return UNKNOWN)
        r = ctx.http.get("https://registry.example/api?name=...")
        if r.error or not r.ok:
            return [Evidence("my_registry", EV.CHECK_REGISTRY_COMPANY, EV.COMPANY, EV.UNKNOWN, summary="no answer", registry_grade=True)]
        ...
        return [Evidence("my_registry", EV.CHECK_REGISTRY_COMPANY, EV.COMPANY, EV.PASS, EV.STRONG, value=name, summary="...",
                         source_url=url, registry_grade=True, raw=r.json())]

register(MyRegistry())                           # and import the module from sources/__init__.py's register_defaults()
```

The engine reads the **check ids** in `evidence.py` (`registry.company`, `registry.officer`, `person.*`, ...); a new source that emits one of
them takes part in the decision with no change to the core, and a person signal with its own check id counts as its own family at its own
strength.  A registry that only gives a company confirmation emits `registry.company` with `registry_grade=True` (and its name in
`REGISTRY_GRADE_SOURCES`).  A source that raises is recorded as `source.error` and sends the case to a person.  Tests: fake the HTTP
client (`net.set_http(...)`, see `tests/_verification_fakes.py`) - no test touches the network.

## Applicant correspondence

Templates in `verification/texts.py` (one line per request code, EN + zh-CN mirror): `corporate_email` ("please verify a corporate e-mail on
`<domain>`"), `person_page` ("please send a link to a page of your company listing you"), `website`, `registration_number`, `vat_number`,
`domain_mismatch`, `role_clarification`, `business_licence`, `verify_account_email`, `free_text` (the administrator's own wording).  **No passport or ID is
ever requested.**  No LLM is involved in phase 1.

The mail's link is `<PUBLIC_APP_URL>/api/verification/reply/<token>`: a plain server-rendered page (no JavaScript, inline CSS, a few kB, EN / zh-CN,
`?lang=`), like the sign-in links.  The token is `HMAC-SHA256(case id, expiry)` (30 days; key `VERIFICATION_SECRET`, else derived from the session
secret); whoever holds the link answers for THIS case only.  On the page:

* add a **corporate e-mail**: a six-digit code is sent to it (30 min, 5 tries, 6 codes per case per day; free / disposable mailboxes and non-existent
  domains are refused); a confirmed address is a verified-e-mail signal.  The code is stored only as an HMAC, never in the log, and the code mail is not stored;
* **links** (page listing you, professional profile, press), a **note**, corrections of website / registration number / VAT number;
* **ONE document** (pdf / png / jpg; type checked by extension *and* magic bytes, 5 MB, 5 per case): stored privately (0600) under
  `<root>/documents/<case>/`, never executed or parsed, served only to administrators as an attachment (`nosniff`, `default-src 'none'`).

Each reply re-runs the checks.  Bad tokens are rate-limited (429 after 30 per 10 min per IP).  A DENIED case ignores replies.

## Audit trail

`<shared>/legal/verification/` (`MOTRES_VERIFICATION_ROOT` overrides) - next to the NDA records, in the part of the shared tree the hourly restic backup covers.
**Nothing is ever deleted.**

| File | Content |
|---|---|
| `events.jsonl` | every event of every case, append-only: `{ts, iso, case, type, actor, data}`; `actor` = `system | applicant | admin:<e-mail>` |
| `cases/<id>.json` | the current state = `reduce(events)`; `store.rebuild(id)` recreates it exactly (tested) |
| `index.json` | `{account: [case ids]}`, rebuilt when missing |
| `documents/<case>/` | uploaded documents |

Events: `case_created` (the submitted data, NDA version + record id + text hash), `application_updated`, `check_run` (**every check with source, source URL,
raw result and time**, the decision with its reasons, the signals counted), `status_changed` (status, reason, actor), `mail_out` / `reply_in` (the
correspondence), `email_otp_sent` / `email_otp_failed` / `email_verified`, `document_uploaded` (name, size, sha256), `admin_action`,
`access_granted` / `access_revoked`, `download` (download id, file, sha256).  The download log `cad_downloads.jsonl` stays the master record of deliveries
(its `nda_basis.verification` names the case and status behind each download); the same delivery is also written into the case timeline.  Admin
actions are also in the admin audit log (`verification.read|approve|deny|request|rerun|document`).

## Admin > Verification

Queue by status (manual review first), case detail: application, decision + reasons + what was asked, access, every check (source, result, strength,
finding, link to the source, the raw answer), the correspondence, documents (download), the downloads, the timeline.  Actions (a reason / text is
required, every action audited and written to the case):

* **Approve** -> VERIFIED, access granted by `admin:<you>`, the applicant is mailed.  Automatic re-runs never take an administrator's approval away.
* **Ask for more** -> your text goes to the applicant as a templated mail with the reply link; status MORE_INFORMATION_REQUIRED.
* **Deny** -> DENIED, access revoked, a neutral mail; replies are ignored.
* **Re-run checks** -> clears the case cache and runs every source again.

## Follow-ups and background automation

### Periodic sweep (`motor_ai_sim.verification.sweep`)

Run via cron or systemd timer (e.g. hourly or daily):
```bash
python -m motor_ai_sim.verification.sweep [--dry-run]
```
The sweep performs:
1. **Silent applicant reminders (Day 3)**: cases in `MORE_INFORMATION_REQUIRED` where the applicant has been silent for `>= REMINDER_AFTER_DAYS` (3 days) receive a reminder email (`reminder_sent` event recorded, sent at most once).
2. **Escalation to manual review (Day 7)**: cases silent for `>= ESCALATE_AFTER_DAYS` (7 days) transition to `MANUAL_REVIEW` (`reason = "applicant_silent"`). Both the admin (`NDA_NOTIFY_EMAIL`) and applicant are notified.
3. **Daily digest**: if any cases are waiting (`MANUAL_REVIEW` or `PENDING > 1h`), sends a summary email to `support@aerostator.com` (at most once per calendar day, tracked in `<root>/digest.json`).

Any applicant action (opening reply page, sending links/notes, requesting OTP) resets the silence clock.

### Admin badge for waiting decisions

- `GET /api/admin/verification/waiting` returns `{"count": N}`, counting cases in `MANUAL_REVIEW` plus cases stuck in `PENDING` for over 1 hour.
- `AdminPanel.tsx` polls this endpoint every 60 seconds on mount.
- `AdminNav.tsx` displays a red pill badge (`#ef4444`) with white text next to the "Verification" tab whenever `count > 0`.

### Reply page contact & OTP resend

- The reply page includes a "Did not get the code?" section with a spam folder notice and a direct mailto link to `support@aerostator.com`.
- Applicants can request a new OTP code via the "Send a new code" button (`action=resend`), subject to a 120-second cooldown (`OTP_RESEND_COOLDOWN_S`) and the per-case daily code limit.
- All outbound applicant verification emails include a direct support address below the signature.

## What the owner configures

| Setting | Where | Effect |
|---|---|---|
| `COMPANIES_HOUSE_API_KEY` | `/etc/motres/api.env` | enables UK Companies House (free key at developer.company-information.service.gov.uk); without it GB companies rely on GLEIF / manual review |
| `MAIL_FROM=support@aerostator.com`, SMTP vars | already set for the auth mails | mails to applicants and the admin notice |
| `NDA_NOTIFY_EMAIL` (else first `ADMIN_EMAILS`) | optional | where MANUAL_REVIEW notices go |
| `VERIFICATION_SECRET` | optional | key of the reply links (else derived from the session secret; rotating it invalidates open links) |
| `VERIFY_DISPOSABLE_FILE` | optional | extra disposable domains, one per line |
| `MOTRES_VERIFICATION_ROOT` | optional | move the store (default `<shared>/legal/verification`) |
| `REMINDER_AFTER_DAYS` | `config.py` (default 3) | Days of silence before reminder email is sent |
| `ESCALATE_AFTER_DAYS` | `config.py` (default 7) | Days of silence before escalating to MANUAL_REVIEW |
| `OTP_RESEND_COOLDOWN_S` | `config.py` (default 120) | Seconds before a new OTP can be requested |

No new Python dependency (standard library + `httpx`, already in `requirements.txt`; a test fails if the package imports anything else).
Accounts that signed the NDA online **before** this change lose the STEP / tools until verified (their case opens at first look): approve the
known ones in Admin > Verification, or set the NDA flag in Admin > Users (that is an administrator's vouching and keeps access open).

## Tests

`tests/test_verification_engine.py` (decision table, text helpers), `tests/test_verification_sources.py` (adapters with a mocked network, SSRF guard, the
registry), `tests/test_verification_flow.py` (gate, intake, automatic decision, reply page with one-time code / links / document, admin actions, audit
completeness, rebuild), `tests/test_verification_misc.py` (no new dependency, failure isolation, background run, mails, limits),
`tests/test_verification_sweep.py` (periodic sweep, reminders, escalations, waiting decisions count, daily digest, OTP cooldown and resend); web:
`web/src/lib/__tests__/verification.test.mjs`, `viewerToolbar.test.mjs`.  `tests/conftest.py` replaces the HTTP client and the MX lookup with offline
doubles and runs the checks inline for the whole suite.

## Phase 2 (hooks, not implemented)

* `LlmResearchSource` (`sources/stubs.py`): gathers `{url, quote, claim, relevance}` citations only; advisory, capped at "supporting", cannot confirm a company.
* `ChinaRegistrySource`: manual review plus the business-licence upload today; a registry integration can replace it behind the same interface.
