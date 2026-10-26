# AeroStator Platform Services and Data Agreement (click-through)

Owner 2026-10-27: B2B only. A company accepts the agreement before it starts a development, in one of two modes:
**FREE** (closed projects; MOTRES may train its internal AI on the client's engineering data) or **PRIVATE** (paid; no AI training on the
client's data; no payment integration yet - the record says `private_pending_payment: true` and the UI says "our team will contact you
about the fee", no price). Modelled on the online NDA (`docs/NDA_ONLINE.md`). Code: `src/motor_ai_sim/platform_agreement.py`,
`src/motor_ai_sim/routes/platform_agreement.py`, web `web/src/components/agreement/`, `web/src/lib/platformAgreement.ts`.

## Off until an approved text is installed

The shipped text is a DRAFT under legal review (`config/legal/platform_agreement/v0-draft/`, first line "DRAFT - NOT FOR SIGNATURE").
A version is a placeholder when its name starts with `v0-` or a text contains "NOT FOR SIGNATURE" / "非签署版本". While the current
version is a placeholder (or `<shared>/legal` is missing, or `PLATFORM_AGREEMENT_DISABLED=1`) nothing is enforced, `GET
/api/agreement/status` tells a user only `enforced: false`, the text is 404 for users and the account menu shows nothing. Admins see
"agreement text not installed" (account menu -> Platform agreement, Admin -> Companies & people).

## Install an approved version (owner / orchestrator)

```
sudo mkdir -p /srv/motres/shared/legal/platform_agreement/<version>          # e.g. v1-2026-11 (must not start with v0-)
# copy platform_agreement_en.md and platform_agreement_zh-CN.md there (no "NOT FOR SIGNATURE" line), then
echo <version> | sudo tee /srv/motres/shared/legal/platform_agreement/current.txt
sudo chown -R 10001:10001 /srv/motres/shared/legal                           # the API writes the records and PDFs
```

No restart: the API reads `current.txt` on every call, so the gate turns on at once. Placeholders in the text:
`{{company}} {{reg_no}} {{country}} {{address}} {{signatory}} {{title}} {{email}} {{date}} {{version}} {{mode}}`; any other
`{{name}}` (the draft has `{{subprocessor_url}}`, `{{free_cap}}`, `{{motres_signatory}}`) stays visible as written, so fill those in the
approved text. A newer version: install it the same way; companies are asked again, their old acceptance stays on record for its own version.
To switch it off again set `PLATFORM_AGREEMENT_DISABLED=1` in `/etc/motres/api.env` and restart the API (acceptances stay on record).

## Data

```
<shared>/legal/platform_agreement_acceptances.jsonl     append-only (never rewritten): id, iso, key, scope, company_id, company, reg_no,
                                                        country, address, user, signatory, title, mode, business, authority,
                                                        ai_training_allowed, private_pending_payment, version, lang, template/text sha256
                                                        (per language), ip, user_agent
<shared>/legal/platform_agreement_signed/<id>.pdf       EN + zh-CN rendered text and the record
```

`key` is `company:<directory company id>` (the company the account is CONFIRMED in; one acceptance covers all its users) or
`user:<email>` when the account has no company. Nothing is e-mailed (owner rule: no outside message without his OK); the admin notice is
the audit line `agreement.accept` (live activity feed) and Admin -> Companies & people shows `MODE · version` per company.

## Routes

`GET /api/agreement/status`, `GET /api/agreement/text?lang=`, `POST /api/agreement/accept {mode, signer, title, business: true,
authority: true, version, lang[, company, reg_no, country, address where the directory/NDA does not know them]}`,
`GET /api/agreement/mine/pdf`, admin `GET /api/admin/agreement/acceptances`, `GET /api/admin/agreement/{id}/pdf`.

## The gate (HTTP 428 `{"code": "agreement_required", "version": ...}`)

Only when an approved version is installed: `POST /api/projects` (create a development project), `POST /api/projects/{id}/spec/submit`
(submit the specification), `POST /api/design-agent/conversations` (start a design-assistant run) and the MCP `start_design` (a tool error
starting with `agreement_required`). Never gated: staff (role admin / engineer), MOTRES's own company (directory reg. no. 7255691000,
name "MOTRES d.o.o.", ids in `MOTRES_OWN_COMPANY_IDS`) and accounts on `motresres.com`, `emotres.com`, `aerostator.com`
(`MOTRES_OWN_DOMAINS` adds more); every read of an existing project. The web (`lib/apiAuth.ts`) opens the dialog on a 428 and repeats the call.

## For the dataset export

`platform_agreement.training_allowed(company_or_email)` -> bool: FREE True, PRIVATE False, no acceptance False (fail-closed), MOTRES's
own accounts/company True. It accepts an e-mail, a directory company id or a stored key. The acceptance in force is the newest one of any
version. The dataset code does not call it yet.
