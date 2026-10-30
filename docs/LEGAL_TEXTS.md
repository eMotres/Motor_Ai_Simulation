# Legal texts and the sign-up acceptance (free simulation launch, texts v0.3)

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
`platform_agreement`, `schedules`} (of the texts in that language), `source`, `ip`, `user_agent`.

`source`: `signup` (e-mail form), `oauth_signup` (the same form opened from an AI app's consent page), `google` (first Google sign-in),
`existing` (the dialog below). A **new** account is refused without the ticked box and the current version (HTTP 422 `terms_required`) on
`POST /api/auth/register` and on the first `POST /api/auth/google`; an existing Google account is never blocked. A repeated sign-up of an
existing address records nothing. A failure to write the line never blocks the account (it is logged; the person is asked at the next
sign-in). Account deletion pseudonymises the lines like `comm_prefs_history` (e-mail -> subject hash, IP -> hash; the proof stays); the data
export carries the account's own lines (`terms_acceptances.json`).

`TERMS_ACCEPTANCE_DISABLED=1` switches the requirement off. Only the test suite sets it (`tests/conftest.py`); never set it in production.

## Existing accounts

After sign-in, `GET /api/legal/status` says `required: true` when the account has no line for the current `TERMS_VERSION`. The web then shows ONE
dialog, "Updated terms: free simulation" (the "In short" paragraph of the Terms of Use, links to the three texts, the same box, **Accept** /
**Later**). Accept calls `POST /api/legal/accept` (`{accept_terms: true, terms_version, lang}`) and records source `existing`. "Later" closes it
until the next sign-in (remembered per session id); nothing else is blocked. Staff (admin / engineer) and MOTRES's own accounts and domains
(the exemption of `platform_agreement.scope_of`) are never asked.

## Dataset permission

`platform_agreement.training_allowed` falls back to this record when no agreement acceptance exists: an account that accepted v0.3 or later
may be trained on; see `docs/PLATFORM_AGREEMENT.md` for the precedence.

## Other places

* Help chat: when the help assistant runs on Gemini (`/api/me` -> `supportProvider`), the chat shows "Runs on Google Gemini. Google may use
  help-chat messages to improve its services. Do not enter confidential information." above its input (Privacy Policy section 5).
* Mailing: `scripts/send_announcement.py --topic <product_updates|new_motors|engineering_content|co_development>` mails only accounts that
  currently have that topic ticked (others are reported as `topic_not_chosen`); the mail footer's privacy link is the in-app policy.
