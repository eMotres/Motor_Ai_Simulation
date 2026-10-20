# Production module — design spec (stage 1)

> Target branch: `feat/production-module` (based on `pre-migration-freeze-2026-09-15`).  
> This doc is the contract between backend, frontend and tests.

## 1. Roles

- Add `oem` to `users.ROLES` (`src/motor_ai_sim/users.py`) and to `auth._ROLE_RANK` (`src/motor_ai_sim/auth.py`) between `user` and `engineer`.
- `_ROLE_RANK = {"anon": -1, "user": 0, "oem": 1, "engineer": 2, "admin": 3}`.
- Admin UI role select (`accessUi.ROLE_VALUES`) becomes `['user', 'oem', 'engineer', 'admin']` with labels:
  - `user` → "Configure"
  - `oem` → "OEM producer"
  - `engineer` → "Simulation"
  - `admin` → "Admin"
- `users._migrate_role` keeps collapsing unknown tiers to `"user"`.

## 2. Access rules

- A production belongs to exactly one die/config.
- Visibility:
  - Members (`owner`, `engineer`, `oem`) of the production may see it.
  - Admins may see all productions and mutate them.
  - Non-members get `404` (not 403) on read/mutation.
- NDA gate: opening a production (reading it) requires the same gate as the full 3D model / STEP download:
  - reuse `model3d.can_download(who, rec)` / `model3d.require_full_view` semantics.
  - If the caller fails the gate, return `403 {code: "nda_required", message: ...}`.
- Mutation rules:
  - `owner` / `engineer` members + admin may edit parts, BOM, process, publish revisions, accept/reject/implement proposals.
  - `oem` members may create proposals and acknowledge revisions.
  - Nothing an OEM types changes design data directly.

## 3. Storage

- Root: `<shared>/production/<die>/<config>/`
- `production.json` — the production record.
- `files/` — uploaded documents/drawings.
- `audit.jsonl` — append-only audit log.
- Use `motor_ai_sim.json_store.atomic_write_json` / `mutate_json` for JSON files.
- Die/config names may contain spaces; store them as-is in the JSON, but build the directory path with safe slugging (replace spaces and slashes).

## 4. Data model

### Bilingual text object

```json
{
  "en": "English text",
  "zh": "Chinese text",
  "src_lang": "zh",
  "status": { "en": "machine|confirmed|pending", "zh": "confirmed" }
}
```

- `src_lang` is the language the author wrote in.
- The other language is machine-translated and shown with a "machine translation, not verified" badge until confirmed/edited.
- On provider failure or missing key: store text with status `pending` and a retry action; never block saving.

### Production

```json
{
  "die": "CIANO14 40 new",
  "config": "L12",
  "title": { "bilingual object" },
  "order_quantity": 100,
  "status": "active|paused|archived",
  "current_revision": "1.1",
  "members": [
    { "email": "a@b.com", "role": "owner|engineer|oem" }
  ],
  "created_at": "ISO",
  "updated_at": "ISO"
}
```

### Part

```json
{
  "id": "pt_<uuid>",
  "name": { "bilingual" },
  "kind": "made|purchased|consumable",
  "qty_per_motor": 1,
  "material": "",
  "spec": "",
  "description": { "bilingual" },
  "drawing_files": [{ "file_id": "...", "kind": "pdf|png", "name": "" }],
  "optional_step": { "file_id": "..." } | null,
  "revision": "1.0"
}
```

### BOM

```json
{
  "rows": [
    { "part_id": "pt_...", "qty_per_motor": 1, "order_total": 100 }
  ]
}
```

### Process

```json
{
  "sections": [
    { "id": "sec_...", "kind": "fits|assembly|winding|tests", "title": { "bilingual" }, "body": { "bilingual" }, "tables": [] }
  ]
}
```

### Document

```json
{
  "id": "doc_...",
  "uploader_email": "...",
  "uploaded_at": "ISO",
  "part_id": "pt_..." | null,
  "file_id": "...",
  "name": "..."
}
```

### Thread / Message

```json
{
  "id": "th_...",
  "part_id": "pt_..." | null,
  "created_at": "ISO",
  "messages": [
    {
      "id": "msg_...",
      "author_email": "...",
      "src_lang": "zh",
      "text": { "bilingual" },
      "attachments": [{"file_id", "name"}],
      "created_at": "ISO"
    }
  ]
}
```

### Proposal

```json
{
  "id": "pr_...",
  "part_id": "pt_..." | null,
  "author_email": "...",
  "text": { "bilingual" },
  "status": "open|accepted|rejected|implemented",
  "decision_note": { "bilingual" } | null,
  "linked_revision": "1.2" | null,
  "created_at": "ISO",
  "resolved_at": "ISO" | null
}
```

### Revision

```json
{
  "number": "1.1",
  "date": "ISO",
  "author_email": "...",
  "summary": { "bilingual" },
  "changes": [{ "bilingual" }],
  "acknowledgements": [
    { "email": "...", "time": "ISO" }
  ]
}
```

### Audit entry

Append-only:

```json
{
  "time": "ISO",
  "who": "email",
  "action": "create|update|delete|publish_revision|ack_revision|translate_retry",
  "entity": "production|part|bom|process|document|thread|proposal|revision",
  "entity_id": "...",
  "snapshot": { ... }
}
```

## 5. API

Base: `/api/production`
Admin: `/api/admin/production`

All routes require a signed-in caller. Reads fail NDA gate with 403 `nda_required`; non-membership with 404.

### Caller routes

| Method | Path | Who | Description |
|--------|------|-----|-------------|
| GET | `/api/production` | members / admin | List visible productions |
| GET | `/api/production/{die}/{config}` | members / admin | Get one production |
| GET | `/api/production/{die}/{config}/parts` | members / admin | List parts |
| POST | `/api/production/{die}/{config}/parts` | owner/engineer/admin | Create part |
| PATCH | `/api/production/{die}/{config}/parts/{part_id}` | owner/engineer/admin | Edit part |
| DELETE | `/api/production/{die}/{config}/parts/{part_id}` | owner/engineer/admin | Delete part |
| GET | `/api/production/{die}/{config}/bom` | members / admin | BOM rows |
| PUT | `/api/production/{die}/{config}/bom` | owner/engineer/admin | Replace BOM rows |
| GET | `/api/production/{die}/{config}/process` | members / admin | Process sections |
| PUT | `/api/production/{die}/{config}/process` | owner/engineer/admin | Replace process sections |
| POST | `/api/production/{die}/{config}/documents` | owner/engineer/admin | Upload document |
| GET | `/api/production/{die}/{config}/documents` | members / admin | List documents |
| GET | `/api/production/{die}/{config}/files/{file_id}` | members / admin | Download file (traced) |
| GET | `/api/production/{die}/{config}/threads` | members / admin | List threads |
| POST | `/api/production/{die}/{config}/threads` | members / admin | Create general thread |
| POST | `/api/production/{die}/{config}/threads/{thread_id}/messages` | members / admin | Post message |
| GET | `/api/production/{die}/{config}/proposals` | members / admin | List proposals |
| POST | `/api/production/{die}/{config}/proposals` | oem / owner / engineer / admin | Create proposal |
| POST | `/api/production/{die}/{config}/proposals/{proposal_id}/accept` | owner/engineer/admin | Accept proposal |
| POST | `/api/production/{die}/{config}/proposals/{proposal_id}/reject` | owner/engineer/admin | Reject proposal |
| POST | `/api/production/{die}/{config}/proposals/{proposal_id}/implement` | owner/engineer/admin | Implement proposal (publishes revision) |
| GET | `/api/production/{die}/{config}/revisions` | members / admin | List revisions |
| POST | `/api/production/{die}/{config}/revisions` | owner/engineer/admin | Publish revision |
| POST | `/api/production/{die}/{config}/revisions/{rev_number}/ack` | oem / owner / engineer / admin | Acknowledge revision |
| POST | `/api/production/{die}/{config}/translate/retry` | members / admin | Retry pending translation |
| POST | `/api/production/{die}/{config}/translate/confirm` | members / admin | Confirm/edit translation |

### Tooling / fixtures order list (owner 2026-10-19)

A production has a second, separate order list next to the motor BOM: the TOOLING / FIXTURES the factory or our own shop needs to make the
motor (e.g. the coil press).  Same rows, columns, prices, currencies and procurement statuses as the motor BOM, same cost-access rule (prices
never reach the factory roles).  `?list=tooling` on `GET/PUT .../bom`, `GET/PUT .../costs`, `GET .../procurement` and `POST .../parts`
selects it; no parameter = the motor BOM.  Files: `bom_tooling.json` (same shape as `bom.json`) and `costs_tooling.json` (same shape as
`costs.json`); procurement lines stay in `procurement.json` (keyed by part id).  Quantities are TOTALS (the "per motor" column is the quantity,
the order quantity is 1); `unit_price` is the price of one piece and a line costs price x quantity, so nothing is multiplied by the number of
motors.  The motor BOM, its costs and its readiness summary never see the tooling parts.

### Admin routes

| Method | Path | Who | Description |
|--------|------|-----|-------------|
| GET | `/api/admin/production` | admin | List all productions with member counts |
| POST | `/api/admin/production/{die}/{config}` | admin | Create production |
| DELETE | `/api/admin/production/{die}/{config}` | admin | Delete production |
| PUT | `/api/admin/production/{die}/{config}/members` | admin | Replace members list |
| POST | `/api/admin/production/{die}/{config}/members` | admin | Add member |
| DELETE | `/api/admin/production/{die}/{config}/members/{email}` | admin | Remove member |

## 6. Translation provider

- Module: `src/motor_ai_sim/production/translate.py`
- Provider: DeepSeek via OpenRouter.
- Env: `OPENROUTER_API_KEY` (required), `PRODUCTION_TRANSLATE_MODEL` default `deepseek/deepseek-chat`.
- Timeout ~60 s.
- System prompt: technical translation for motor manufacturing; keep numbers, units, tolerances, part codes verbatim.
- Never log or return the key.
- On failure / missing key: store text with status `pending` and provide `retry` action.
- Unit tests mock the provider; no network.

## 7. Import CLI

```bash
python -m motor_ai_sim.production.import_package <package.json> --die "CIANO14 40 new" --config L12 --assets <dir>
```

- Reads existing factory-document `package.json`:
  - `{title, subtitle, file_name, sections:[...]}`
  - section types: `{"h": text}`, `{"h3": text}`, `{"p": text}`, `{"bullets": [text]}`, `{"table": [[...]]}`, `{"img": "assets/x.png", "w": cm}`, `{"drawings": [[caption, qty, "assets/x.png"], ...]}`.
- Text is Chinese → set `src_lang=zh`, machine-translate en to pending if no key.
- Map config's BOM table to parts + BOM rows. Header: `序号, 零件, 类型, 每台, 订单合计（N 台）, 材料 / 表面, 规格, 图纸 / 要求`.
- Map drawings to part drawing files by file name (`shaft`, `magnet`, `front_holder`, `back_holder`, `ring`).
- Other sections become process sections.
- Creates production if missing, seeds parts/BOM/process, publishes revision `1.1`.

## 8. Web module

- Location: `web/src/modules/production/`
- Public export: `index.ts`
- Components:
  - `ProductionListPage` — list of visible productions.
  - `ProductionPage` — tabs: Parts, BOM, Process, Documents, Discussion, Proposals, Changes.
  - `PartCard` — drawing preview/download, link to 3-D viewer, bilingual description, revision.
  - `BomTable` — editable for owner/engineer.
  - `ProcessView` — ordered sections.
  - `DocumentsPanel` — upload/download.
  - `DiscussionPanel` — general + per-part threads, attachments, original/translation toggle.
  - `ProposalsPanel` — create, accept/reject/implement.
  - `ChangesPanel` — revisions + acknowledge banner for OEM.
- Machine-translation badge + "confirm translation" for members.
- Admin: `ProductionAccessSection` in AdminPanel, one row per die/config, drawer to create production and add/remove members.
- Top-level menu: "Production" / "生产" for admin, engineer, oem (non-members see nothing).
- i18n namespace: `production` (add to `core.ts` `NAMESPACES`).

## 9. Tests

- `tests/test_production.py`:
  - access: oem sees only member productions, NDA gate, non-members 404, admin all
  - proposal → implement → revision → acknowledge workflow
  - translation provider mocked (success, failure → pending, retry)
  - import of synthetic package.json
  - audit entries
- `web/src/modules/production/tests/*.test.mjs` or `.test.ts`: pure helpers ( bilingual text helpers, revision number parsing).
- `tsc --noEmit` filtered to touched files.
