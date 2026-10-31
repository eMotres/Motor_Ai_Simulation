# AeroStator Platform Services and Data Agreement (click-through)

Owner 2026-10-27: B2B only. A company accepts the agreement before it starts a development, in one of two modes:
**FREE** (closed projects; MOTRES may train its internal AI on the client's engineering data) or **PRIVATE** (paid; no AI training on the
client's data; no payment integration yet - the record says `private_pending_payment: true` and the UI says "our team will contact you
about the fee", no price). Modelled on the online NDA (`docs/NDA_ONLINE.md`). Code: `src/motor_ai_sim/platform_agreement.py`,
`src/motor_ai_sim/routes/platform_agreement.py`, web `web/src/components/agreement/`, `web/src/lib/platformAgreement.ts`.
The public texts, the account acceptance of the Terms of Use and the existing-user dialog: `docs/LEGAL_TEXTS.md`.

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

Two lists come from `python -m motor_ai_sim.dataset_exclusions` and `GET /api/admin/dataset/exclusions` (admin): the **deny-list** keys
(`companies`, `emails`, `workspace_ids`, `project_ids`, `motors`, `productions`, `acceptance_ids`: whatever belongs to a client whose newest
acceptance is PRIVATE; unchanged) and, since owner decision D10 (2026-10-31), the **allow-list** under `"training"`. A record may be trained on
only if it is covered by the allow-list; absent = NOT eligible.

```json
"training": {
  "version": 1,
  "policy": "allow-list",
  "generated": "2026-10-31T09:00:00Z",
  "accounts": {
    "ann@acme-drones.example": {"eligible_from": "2026-10-20T08:00:00Z", "mode": "FREE", "historical_opt_in": false, "capacity": "company"},
    "bob@acme-drones.example": {"eligible_from": "2026-10-20T08:00:00Z", "eligible_until": "2026-10-30T12:00:00Z",
                                "mode": "FREE", "historical_opt_in": false, "capacity": "company"},
    "solo@solo-works.example": {"eligible_from": "2026-03-02T10:15:00Z", "mode": "FREE", "historical_opt_in": true, "capacity": "sole_trader"}
  },
  "companies":  {"c-1a2b3c": {"eligible_from": "2026-10-20T08:00:00Z", "mode": "FREE"}},
  "workspaces": {"<workspace_id(e-mail)>": "ann@acme-drones.example"},
  "projects":   {"acme-1": {"eligible_from": "2026-10-20T08:00:00Z", "eligible_until": "2026-10-30T12:00:00Z"}},
  "own": {"emails": ["vadim@motresres.com"], "domains": ["aerostator.com", "emotres.com", "motresres.com"], "company_ids": []}
}
```

* **Window.** A record of an account, company or project is eligible iff `eligible_from <= record time < eligible_until`; no
  `eligible_until` means open-ended. All times are ISO UTC (`...Z`).
* **PRIVATE and holds close the window, they never reopen the past** (owner 2026-10-31, agreement 11.1): when a company or an account
  switches to PRIVATE, or a PRIVATE request puts a no-training hold on it, the entry STAYS in the list with `eligible_until` = that moment;
  data supplied in FREE mode before it stay eligible. Only an account/company that was never FREE (or whose window is empty) is absent.
  A FREE - PRIVATE - FREE history keeps the newest FREE run only (the PRIVATE interval is never eligible).
* **accounts** - one entry per account that is eligible by its own Terms-of-Use box or account-level agreement (`capacity` `business` or
  `sole_trader`, as ticked at the sign-up/update dialog) or covered by its company's FREE acceptance (`capacity: "company"`). Capacity
  `other` (no authority to bind) and a Terms line without a capacity are NOT listed (fail-closed until a per-data confirmation exists, D4).
  `historical_opt_in: true` (the optional box of agreement 7.9) sets `eligible_from` to the account's creation time instead of the acceptance.
  The company acceptance decides for its confirmed users (agreement 2.4); a company that is PRIVATE from the start leaves only the
  accounts' own earlier windows, closed at the company's switch.
* **companies** - directory company ids with a FREE acceptance (MOTRES's own company is under `own`).
* **workspaces** - `workspace_id(e-mail) -> e-mail` for every listed account and own address, so a workspace directory is mapped to its person.
* **projects** - eligible only if the owning side (the non-staff owners; for a staff-owned project the customer members) is eligible and
  no member who was never FREE is PRIVATE or on hold (most restrictive wins, agreement 2.4). The window is the INTERSECTION of the owners'
  windows (latest `eligible_from`, earliest `eligible_until`), also closed by a member who switched to PRIVATE or was held. The record
  carries who contributed it, so a pipeline can still filter contributors by `accounts`.
* **own** - MOTRES's own addresses (registry accounts on its domains or confirmed in its company), domains and company ids: its own
  data stay allowed as before, but agreement 7.10 gives no exemption for data DERIVED from a client's project: such a record also has to
  pass `projects`/`accounts` of the client.

`training_eligibility(company_or_email) -> dict | None` returns the same window for one account, company id or key
(`{mode, eligible_from, [eligible_until], historical_opt_in, capacity, scope}`; `scope` is `company`, `account` or `own`; None = never
eligible). First match decides: a COMPANY acceptance of this agreement, else an ACCOUNT-level acceptance, else the Terms-of-Use box of v0.4 or
later by capacity (the newest line decides; the unbroken run of qualifying lines sets the start), else None. A no-training **hold** (below) on
the account or its company closes the window at the hold time. `training_allowed(x)` stays: True iff the window is open now.

**Holds (gate R10).** A written PRIVATE request received at support@aerostator.com places the client's data on a no-training hold at once
(agreement 3.4): admin `POST /api/admin/agreement/holds {"email" | "company_id", "action": "hold" | "release", "note", "reference"}`
appends a line to `<shared>/legal/platform_agreement_holds.jsonl` (append-only, `kind` hold/release, `actor`, UTC time; audit lines
`agreement.hold` / `agreement.hold_release`); `GET /api/admin/agreement/holds` lists the active holds and the history. The hold takes
effect on the next export. "Release" (the Order is declined) removes the cap, but never reopens the interval the hold covered:
the window then starts again at the release time (the newest run wins, as for FREE - PRIVATE - FREE).

MOTRES's own accounts/company: `scope: "own"`, `eligible_from` = the epoch. The acceptance of the agreement in force is the newest one of
any version; a PRIVATE choice is never overridden by the Terms line.

## Account acceptance and the public texts

Accepting the Terms of Use when an account is created (or once, after sign-in, for an existing account) accepts this agreement in FREE
mode for the account (agreement section 2.3). The texts, the acceptance record and how a new version is published are in
`docs/LEGAL_TEXTS.md`. The click-through above is unchanged: it still reads the INSTALLED version from `<shared>/legal`; the public copy in
`config/legal/platform_agreement_public/` is only what `/platform-agreement` shows.
