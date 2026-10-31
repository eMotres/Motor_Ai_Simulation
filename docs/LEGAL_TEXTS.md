# Legal texts and the sign-up acceptance (free simulation launch, texts v0.4)

Owner 2026-10-27. Simulation is free; in FREE mode MOTRES may train its own AI on the engineering data of the user's projects (never personal
data); PRIVATE (paid) excludes AI training. One unticked box accepts three texts: the **Terms of Use**, the **Platform Agreement**
(`docs/PLATFORM_AGREEMENT.md` is its company-level click-through) and the **Privacy Policy**. English governs, zh-CN is a full mirror.
Code: `src/motor_ai_sim/legal_texts.py`, `src/motor_ai_sim/routes/legal.py`, web `web/src/lib/legalLogic.ts`,
`web/src/components/auth/{LegalPage,LegalMarkdown,TermsUpdateDialog,TermsUpdateHost}.tsx`, `web/src/components/common/TermsBox.tsx`.

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
   version, and every account is asked again once (below).
3. Re-pin the SHA-256 table in `tests/test_legal_texts.py`. Deploy as usual.

## The acceptance record

`<identity root>/terms_acceptances.jsonl` (next to `users.json` and `comm_prefs.json`; append-only, 0600): `id`, `ts`, `iso`, `user` (e-mail),
`terms_version`, `privacy_version`, `agreement_public_version`, `lang` (the language shown), `text_sha256` {`terms`, `privacy`,
`platform_agreement`, `schedules`} (of the texts in that language), `country`, `forum_variant` (A | B), **`capacity`** (`business` |
`sole_trader` | `other`, agreement 2.3), `business_name` (required for `business`, optional for `sole_trader`), **`historical_opt_in`** (bool,
the optional box of 7.9; only the existing-user dialog can set it), **`labels`** {`accept`, `election`} (the wording of the boxes as shown,
agreement 15.3), `source`, `ip`, `user_agent`.

`source`: `signup` (e-mail form), `oauth_signup` (the same form opened from an AI app's consent page), `google` (first Google sign-in),
`existing` (the dialog below). A **new** account is refused without the ticked box and the current version (HTTP 422 `terms_required`), a
country from the list (`country_required`), a capacity (`capacity_required`) and, for capacity `business`, the business name
(`business_name_required`) on `POST /api/auth/register` and on the first `POST /api/auth/google`; an existing Google account is never blocked.
The block (web `components/common/TermsBox.tsx`): the capacity radio, the country with the section 13 variant that applies, the business name,
the box whose sentence follows the capacity (recommended R5 wording, zh-CN fully Chinese), and in the update dialog only the second, optional,
initially unticked box "Also use the engineering data my account entered before today". A line of v0.4 or later without a capacity is not an
acceptance (the person is asked again). A repeated sign-up of an
existing address records nothing. A failure to write the line never blocks the account (it is logged; the person is asked at the next
sign-in). Account deletion pseudonymises the lines like `comm_prefs_history` (e-mail -> subject hash, IP -> hash; the proof stays); the data
export carries the account's own lines (`terms_acceptances.json`).

`TERMS_ACCEPTANCE_DISABLED=1` switches the requirement off. Only the test suite sets it (`tests/conftest.py`); never set it in production.

## Existing accounts

After sign-in, `GET /api/legal/status` says `required: true` when the account has no line for the current `TERMS_VERSION`. The web then shows ONE
dialog, "Updated terms: free simulation" (the "In short" paragraph of the Terms of Use, links to the three texts, the same box, **Accept** /
**Later**). Accept calls `POST /api/legal/accept` (`{accept_terms: true, terms_version, country, capacity, business_name, historical_opt_in,
lang, accept_label, election_label}`) and records source `existing`. "Later" closes it
until the next sign-in (remembered per session id); nothing else is blocked. Staff (admin / engineer) and MOTRES's own accounts and domains
(the exemption of `platform_agreement.scope_of`) are never asked.

## The "Later" deadline (owner D7)

`TERMS_REQUIRED_FROM` (an ISO date, UTC; unset = no gate; set it in `/etc/motres/api.env`) is the day after which "Later" stops working for
compute: from that date a non-staff, non-MOTRES account without an acceptance of the current `TERMS_VERSION` cannot START a job. Every route
that goes through `jobs.queued` (coupled, thermal, transient, cogging, mechanical) answers HTTP 428 `{"detail": {"code": "terms_required",
"version", "from"}}`; the MCP `simulate` tool answers a tool error that starts with `terms_required`. The web (`lib/platformAgreement.ts`,
the same retry as `agreement_required`) opens the "Updated terms" dialog and repeats the call once after acceptance. `GET /api/legal/status`
carries `required_from`; the dialog then shows "From {date} new simulations need these terms." / zh "自 {date} 起，启动新的仿真须先接受这些条款。".
Campaign starts (optimizer, 3-D) are staff-only routes, so staff exemption makes the gate unnecessary there.

## Dataset permission

`platform_agreement.training_eligibility(company_or_email)` returns the window in which an account's or company's data may be trained on
(`{mode, eligible_from, [eligible_until], historical_opt_in, capacity, scope}`, None = never eligible); `training_allowed` is "the window is
open now". Company acceptance first, then an account-level agreement acceptance, then this record: capacity `business` / `sole_trader` of
v0.4 or later is eligible from the acceptance (from the account's creation when `historical_opt_in`), capacity `other` is not (fail-closed
until a per-data confirmation exists). A PRIVATE switch or a no-training hold closes the window and never reopens the past. The export
contract (`dataset_exclusions()["training"]`) and the hold endpoint are in `docs/PLATFORM_AGREEMENT.md` ("For the dataset export").

## The account record as shown (gate R16)

`GET /api/legal/mine` (rows) and `GET /api/legal/mine/pdf` (the newest acceptance as a PDF; admin: `GET /api/admin/legal/{id}/pdf`) show
the capacity, the business name when one was given, the country, the section 13 forum variant, the earlier-data election, version,
language, time, route and the text hashes. A field without a value is left out; the company-agreement rows (company, registration no.,
title, mode) never appear for an account acceptance (`legal_texts.record_view`).

## Other places

* Help chat: when the help assistant runs on Gemini (`/api/me` -> `supportProvider`), the chat shows "Runs on Google Gemini. Google may use
  help-chat messages to improve its services. Do not enter confidential information." above its input (Privacy Policy section 5).
* Mailing: `scripts/send_announcement.py --topic <product_updates|new_motors|engineering_content|co_development>` mails only accounts that
  currently have that topic ticked (others are reported as `topic_not_chosen`); the mail footer's privacy link is the in-app policy.
