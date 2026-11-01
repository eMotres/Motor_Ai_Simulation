# Legal texts, the sign-up box and the agreement at the start of a simulation (free simulation launch, texts v0.4)

Owner 2026-10-27, flow changed 2026-11-01 («договор нужен только для пользователей, кто хочет сделать моделирование мотора»).
Simulation is free; in FREE mode MOTRES may train its own AI on the engineering data of the user's projects (never personal data); PRIVATE
(paid) excludes AI training. English governs, zh-CN is a full mirror. **Two steps:**

1. **Sign-up** (e-mail form, first Google sign-in, sign-up from an AI app's consent page): ONE unticked, required box accepts the **Terms of
   Use** and the **Privacy Policy**: "I use AeroStator for business and accept the Terms of Use and the Privacy Policy." (zh "我为商业目的使用
   AeroStator，并接受《使用条款》和《隐私政策》。", both names linked). No capacity, country or business name is asked.
2. **The start of a simulation**: the **Platform Agreement** (`docs/PLATFORM_AGREEMENT.md` is its company-level click-through) is accepted at the
   account level, or a company has accepted it. Gate and dialog below. Reading stored results, the catalogue, Configure (no job), the help
   chat and the specification assistant never need it.

Code: `src/motor_ai_sim/legal_texts.py`, `src/motor_ai_sim/routes/legal.py`, `src/motor_ai_sim/platform_agreement.py` (the gate), web
`web/src/lib/{legalLogic,legalApi,legalLabels,platformAgreement}.ts`, `web/src/components/auth/{LegalPage,LegalMarkdown,TermsUpdateLine}.tsx`,
`web/src/components/common/TermsBox.tsx` (the sign-up box), `web/src/components/agreement/{AgreementHost,SimulationAgreementDialog}.tsx`.

## Where the texts live

```
config/legal/terms_of_use/<version>/terms_of_use_{en,zh-CN}.md
config/legal/privacy/<version>/privacy_{en,zh-CN}.md
config/legal/platform_agreement_public/<version>/platform_agreement_{en,zh-CN}.md     the agreement as published, for reading
config/legal/platform_agreement_public/<version>/schedules_{en,zh-CN}.md               Schedules 1-3
```

Copied verbatim (LF; `.gitattributes` pins it) from the approved texts. `{{version}}` is the only placeholder the server fills; the agreement
keeps its click-through placeholders (`{{company}}` ..., `{{#if country_is_cn_hk}}`) and the web page shows them as blanks and shows both
section 13 variants. `GET /api/legal/{terms|privacy|platform-agreement|schedules}?lang=en|zh-CN` (no sign-in) returns
`{doc, version, lang, text, sha256}`; an unknown document or language is 404. Web pages: `/terms`, `/privacy`, `/platform-agreement`
(agreement + Schedules) and the same under `/zh-cn/`; the sign-in dialog, cookie banner and landing footer link to them (build-time overrides
`VITE_TERMS_URL`, `VITE_PRIVACY_URL`, `VITE_AGREEMENT_URL`).

## Publish a new version

1. Put the approved files in a folder as `<terms_of_use|privacy_notice|platform_agreement|schedules>_<en|zh-CN>_<version>.md` and run
   `python scripts/sync_legal_texts.py <folder> --version <version>` (a verbatim copy; run it again if a text changes before release).
2. Bump `TERMS_VERSION` in `src/motor_ai_sim/legal_texts.py` and `TERMS_VERSION` in `web/src/lib/legalLogic.ts` (a node test compares them).
   Privacy and the public agreement share the number. A text that users have already been shown is never edited in place: a change is a new
   version, and every account gets the "updated terms" line once (above); a new public agreement version is asked again at the next simulation start.
3. Re-pin the SHA-256 table in `tests/test_legal_texts.py`. Deploy as usual.

## The acceptance record

`<identity root>/terms_acceptances.jsonl` (next to `users.json` and `comm_prefs.json`; append-only, 0600; ONE file for both steps). Every line:
`id`, `ts`, `iso`, `user` (e-mail), `terms_version`, `privacy_version`, `agreement_public_version`, `lang` (the language shown), `text_sha256`
{`terms`, `privacy`, `platform_agreement`, `schedules`} (of the texts in that language; the agreement hash is of the public rendering with both
section 13 variants), `labels` {`accept`, `election`} (the wording of the boxes as shown, agreement 15.3), `kind`, `source`, `ip`, `user_agent`.

* **`kind: "terms"`**, sources `signup` (e-mail form), `oauth_signup` (the same form opened from an AI app's consent page), `google` (first Google
  sign-in), `existing` (the "updated terms" line below): the Terms of Use + Privacy Policy only. **No agreement fields** (no capacity, country,
  business name, forum variant, earlier-data election or mode).
* **`kind: "agreement"`**, source `simulation_start` (the dialog below): additionally **`mode`** (`FREE` | `PRIVATE`; PRIVATE = requested, the fee
  is agreed with our team), `private_pending_payment`, `ai_training_allowed`, **`capacity`** (agreement 2.3: `business` | `sole_trader` | `other`),
  **`country`** (where the business is established, section 13 forum variant `forum_variant` A | B), **`business_name`** (required for `business`,
  optional for `sole_trader`) and **`historical_opt_in`** (the optional box of 7.9).
* Lines of the first v0.4 release (#525) have a capacity and a country but no `kind` / `mode`; they count as agreement lines in FREE mode (their
  label carried the FREE exchange sentence).

A **new** account is refused without the ticked box and the current version (HTTP 422 `terms_required`) on `POST /api/auth/register` and on the first
`POST /api/auth/google`; an existing Google account is never blocked. A client that still sends `country` / `capacity` / `business_name` at sign-up is
ignored (no 422, nothing recorded). A repeated sign-up of an existing address records nothing. A failure to write the line never blocks the account
(it is logged; the person gets the "updated terms" line at the next sign-in). Account deletion pseudonymises the lines like `comm_prefs_history`
(e-mail -> subject hash, IP -> hash; the proof stays); the data export carries the account's own lines (`terms_acceptances.json`).

`TERMS_ACCEPTANCE_DISABLED=1` switches the requirement off (sign-up box, the line, the simulation gate). Only the test suite sets it
(`tests/conftest.py`); never set it in production.

## Existing accounts: the "updated terms" line

After sign-in, `GET /api/legal/status` says `required: true` when the account has no line for the current `TERMS_VERSION` (any kind). The web then
shows ONE dismissible line (a small snackbar at the bottom, **not a dialog**, nothing is blocked by it): "We updated the Terms of Use and the Privacy
Policy." with both names linked and the buttons **Accept** / **Dismiss** (zh "我们更新了《使用条款》和《隐私政策》。" 「接受」/「关闭」). Accept calls
`POST /api/legal/accept` (`{accept_terms: true, terms_version, lang, accept_label}`) and records a Terms-only line with source `existing`. Dismiss hides
the line for the browser session (sessionStorage, per account). Staff (admin / engineer) and MOTRES's own accounts and domains (the exemption of
`platform_agreement.scope_of`) are never shown it. The status also carries `agreement: {accepted, mode, accepted_at}`.

## The Platform Agreement at the start of a simulation

**The gate** (`platform_agreement.simulation_gate_detail`): while env **`PLATFORM_AGREEMENT_AT_SIMULATION=1`** (default **OFF**: a deploy never
activates it; activation is the owner's command, set it in `/etc/motres/api.env` and restart the API) every route that goes through `jobs.queued`
(coupled, thermal, transient, cogging, mechanical, ...) answers a non-staff, non-MOTRES account that has no current acceptance with HTTP 428
`{"detail": {"code": "agreement_required", "version": <public agreement version>, "at": "simulation", "message"}}` (`at` tells the web to open the
short account dialog and not the company click-through). The MCP `simulate` and `start_design` tools answer a tool error that starts with
`agreement_required`. **Current acceptance** = (a) a company (or account) acceptance of the click-through agreement of `docs/PLATFORM_AGREEMENT.md`
(any version), or (b) an account-level agreement line (`kind: agreement` / first-release shape) of the current public version with capacity, country
and mode (FREE or PRIVATE requested; a PRIVATE request lets the account simulate). A Terms-only line is not an acceptance. Never gated: staff, MOTRES's
own company and domains, an anonymous or process identity, nested runs, `TERMS_ACCEPTANCE_DISABLED=1`; never gated either: reading stored results,
the catalogue, Configure without a job, the help chat, the specification assistant, the legal routes. With the flag off `start_design` keeps its former
gate (the installed company click-through, see `docs/PLATFORM_AGREEMENT.md`). The former `TERMS_REQUIRED_FROM` deadline and its `terms_required` gate
are removed (superseded).

**The dialog** (web `lib/apiAuth.ts` -> `handleAgreementRequired`, one place): on that 428 the web opens `SimulationAgreementDialog` and repeats the
refused call ONCE after the acceptance (concurrent refused calls share one dialog). One heading, five short lines (simulation is free; FREE lets
MOTRES train its AI on the data supplied from now on, no personal data; irreversible; PRIVATE is paid and excludes training, our team will contact
you; links to the three full texts), then the fields: the **mode** (FREE or "Ask for PRIVATE (paid)", nothing preselected), the **capacity** radio
(a) business I may bind / (b) myself as a registered sole trader / (c) neither, the **country** with the section 13 variant that applies, the
**business name** (required for (a)), the **acceptance box** (initially unticked; its sentence carries the FREE exchange and irreversibility, or the
PRIVATE no-training promise) and the optional, unticked **earlier-data box**. `POST /api/legal/accept-agreement` records a `kind: agreement` line
(source `simulation_start`) with the mode, capacity, business name, country, forum variant, `historical_opt_in`, the version and hashes of the shown
rendering, language, IP, user agent and both labels; 422 `agreement_box_required`, `mode_required`, `capacity_required`, `country_required`,
`business_name_required`. **PRIVATE** also appends the existing no-training hold for the account (`platform_agreement.record_hold`, key
`user:<e-mail>`, actor = the account, reference = the record id; `hold: true` in the answer, `false` when the legal directory is not installed -
the line itself still closes the training window): the account may simulate, its data are held out of training, and the dialog says "Our team will
contact you about PRIVATE." Admin releases the hold like any other (`docs/PLATFORM_AGREEMENT.md`).

## Environment

| variable | default | meaning |
|---|---|---|
| `PLATFORM_AGREEMENT_AT_SIMULATION` | unset = OFF | `1` / `true` / `yes` turns the simulation-start gate on (owner's command; no deploy activates it) |
| `TERMS_ACCEPTANCE_DISABLED` | unset | `1` switches the sign-up box, the line and the gate off (test suite only) |
| `TERMS_REQUIRED_FROM` | - | removed (superseded by the simulation gate) |

## Dataset permission

`platform_agreement.training_eligibility(company_or_email)` returns the window in which an account's or company's data may be trained on
(`{mode, eligible_from, [eligible_until], historical_opt_in, capacity, scope}`, None = never eligible); `training_allowed` is "the window is
open now". Company acceptance first, then an account-level click-through acceptance, then the account's agreement line (the simulation-start dialog): FREE with
capacity `business` / `sole_trader` of v0.4 or later is eligible from the acceptance (from the account's creation when `historical_opt_in`),
capacity `other` is not (fail-closed until a per-data confirmation exists), a PRIVATE request closes the window at that line (and places the hold).
A **Terms-only line (the sign-up box, no capacity) is never eligible** and never disturbs an agreement window. A PRIVATE switch or a no-training hold
closes the window and never reopens the past. The export
contract (`dataset_exclusions()["training"]`) and the hold endpoint are in `docs/PLATFORM_AGREEMENT.md` ("For the dataset export").

## The account record as shown (gate R16)

`GET /api/legal/mine` (rows) and `GET /api/legal/mine/pdf` (the newest acceptance as a PDF; admin: `GET /api/admin/legal/{id}/pdf`) show
the agreement mode, the capacity, the business name when one was given, the country, the section 13 forum variant, the earlier-data election,
version, language, time, route and the text hashes. A field without a value is left out (a Terms-only line shows none of the agreement rows); the
company-agreement rows (company, registration no., title) never appear for an account acceptance (`legal_texts.record_view`).

## Other places

* Help chat: when the help assistant runs on Gemini (`/api/me` -> `supportProvider`), the chat shows "Runs on Google Gemini. Google may use
  help-chat messages to improve its services. Do not enter confidential information." above its input (Privacy Policy section 5).
* Mailing: `scripts/send_announcement.py --topic <product_updates|new_motors|engineering_content|co_development>` mails only accounts that
  currently have that topic ticked (others are reported as `topic_not_chosen`); the mail footer's privacy link is the in-app policy.
