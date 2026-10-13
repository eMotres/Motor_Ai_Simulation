# Online NDA click-through (3D model download)

A user without NDA can view the 3D model but not download the STEP.  In the "3D model" tab such a user sees "Download after NDA" and the
button **Sign NDA online**; signing unlocks the download at once, without the owner.

## Flow

1. The dialog asks for company, registration no., country (list), address, signatory full name and title; the e-mail is the account's and read-only.
2. It shows the CURRENT agreement text with the placeholders filled live (only the variant of the selected country), the acceptance box
   ("I have read and accept the agreement and am authorised to sign for the company") and "type your full name to sign", which must equal the signatory name.
3. `POST /api/nda/sign` validates every field again (loud 422 with a message per field), refuses a draft / missing text (409) and an outdated version (409), then
   - renders the text (same renderer as the dialog), hashes exactly that text,
   - writes `nda_signed/<record_id>.pdf` (filled text + acceptance block: name, title, company, time UTC, IP, version, text hash, record id; reportlab, built-in CJK font for zh-CN),
   - appends one line to `nda_signatures.jsonl` (user, e-mail, company fields, version, language, SHA-256 of the template and of the rendered text, UTC time, IP, user agent),
   - sets the user record `nda = {signed: true, date, by: "online", version, company, record_id}`,
   - mails the PDF to the user and to the admin address (`NDA_NOTIFY_EMAIL`, else the first of `ADMIN_EMAILS`); with SMTP unset it logs "mail not configured" and goes on.
4. The user downloads the PDF ("Your signed NDA", `GET /api/nda/mine/pdf`); the admin any PDF (`GET /api/nda/<record_id>/pdf`).

Admin: Sign-ups tab -> "NDA signed online" (company, user, country, version, PDF; free-mail domains such as gmail / outlook / qq / 163 / yahoo are flagged, never blocked);
the user drawer shows source (online / admin), version, company and the PDF link.  The NDA switch still revokes: the user record keeps `version` / `company` / `record_id`
with `signed: false`, the record line and the PDF stay.  A signature stays valid for its own version; a newer version never invalidates an older signature (a user may sign the new one).

## Text and versions

```
<shared>/legal/model_nda/current.txt                       name of the current version, e.g. v1-2026-10
<shared>/legal/model_nda/<version>/model_nda_en.md
<shared>/legal/model_nda/<version>/model_nda_zh-CN.md
<shared>/legal/nda_signatures.jsonl                        append-only, written by the API
<shared>/legal/nda_signed/<record_id>.pdf                  written by the API
```

Placeholders: `{{company}} {{reg_no}} {{country}} {{address}} {{signatory}} {{title}} {{email}} {{date}} {{version}}`, plus the blocks
`{{#if country_is_cn_hk}}...{{/if}}` and `{{#unless country_is_cn_hk}}...{{/unless}}` (flag = country CN or HK; one level, not nested; hand-written renderer in
`motor_ai_sim/nda.py` and `web/src/lib/nda.ts`, locked to each other by the same SHA-256 values in `tests/test_nda.py` and `web/src/lib/__tests__/nda.test.mjs`).
`GET /api/nda/current?lang=en|zh-CN` returns `{version, lang, text, sha256, signable, countries}`; `sha256` is the hash of the served template.

The code ships a placeholder (`config/legal/model_nda/v0-placeholder/`, "DRAFT - NOT FOR SIGNATURE"); it is used when the shared directory has no `current.txt`, and while it is
current (or the text contains "NOT FOR SIGNATURE") signing is disabled with a clear message.  The API never creates `<shared>/legal`: if it is missing, signing is refused.

## Server setup (owner)

```
sudo mkdir -p /srv/motres/shared/legal/model_nda/<version>
# copy model_nda_en.md and model_nda_zh-CN.md there, then
echo <version> | sudo tee /srv/motres/shared/legal/model_nda/current.txt
sudo chown -R 10001:10001 /srv/motres/shared/legal      # the API writes the records and PDFs as the image's motres user
```

Optional in `/etc/motres/api.env`: `NDA_NOTIFY_EMAIL=<address that gets every signed copy>`.
