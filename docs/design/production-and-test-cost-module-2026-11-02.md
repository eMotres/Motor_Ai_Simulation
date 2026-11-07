# Production and test cost module: design (2026-11-02)

Status: design only, nothing is implemented by this document.
Goal (owner 2026-11-02): «продумать модуль расчёта цены производства и тестирования». A module that computes what it
costs us to produce a motor (later the 800 V controller, product type `electronics`) and to test it, and from that cost
a price that only the owner releases as a quote.

Process order (owner rule, binding on every part of this design):

```
full simulation -> agreement with the customer -> we compute and assess ALL costs -> quote -> payment -> production
```

No price or quote talk with a customer during design. A price never reaches a customer before the owner releases a
quote, and never through MCP. Costs reach the factory (OEM) only where the production has `share_costs_with_oem` on.
Every number carries a source (supplier quote, measured time or catalog price) with its date and currency, and every
recalculation leaves a trail.

---

## 1. What exists today

### 1.1 The "Cost" main tab (`modules/cost.py`, `contracts/cost_ir.py`, `web/src/components/cost/CostPanel.tsx`)

- Kernel capability `cost` (module `cost-basic` 0.1.0), run through `POST /api/kernel/study` with
  `capabilities: ["geometry.2d", "cost"]`.
- It computes active-material cost only: mass x price per kg for four buckets (copper, magnet, electrical steel, shaft).
  The masses come from `masses.compute_masses`, the same source the Simulation card uses (k_end on copper, lamination
  k_f, the density of the material assigned to each part). Slot insulation is priced by material name (Nomex / Al2O3 /
  AlN), and there is one flat labour line (default 25).
- `CostIR` = `{currency: "USD", total, lines: [{item, mass_kg, unit_price_per_kg, cost}], provenance}`.
- The prices are hard-coded defaults (USD/kg), duplicated in `cost.py` and `CostPanel.tsx`. The tab lets you edit
  them per session, but nothing is saved.
- The tab is admin-only (`TAB_GATES.cost = 'admin'`), but `POST /api/kernel/study` is open to role `user`
  (`auth.py`). Any signed-in account can therefore run the `cost` capability. Today that is harmless because only the
  placeholder prices are used. It stops being harmless as soon as real prices are plugged in.

### 1.2 Production costs (`production/models.py`, `production/routes.py`, `BomTable.tsx`)

- `costs.json` per production: `rows: [{part_id, unit_price, currency, note}]`, `extras: [{id, name, amount, currency,
  note}]` (work lines) and `margin_pct`. The tooling list (`bom_tooling.json` / `costs_tooling.json`) has the same
  shape. Its quantities are totals and are never multiplied by the order quantity.
- `compute_cost_summary`: parts subtotal + extras = `cost_per_motor`; `price_per_motor = cost / (1 - margin)`; order
  totals = x `order_quantity`. `compute_assembly_costs` rolls the cost up the stator / rotor / housing tree.
- Access: `can_view_costs` covers admin, production `owner` / `engineer`, and `oem` only when `share_costs_with_oem`
  is on (the flag is set by an admin, and the change is audited). `can_edit_costs` covers admin, owner and engineer.
  Every save writes a full snapshot to `audit.jsonl`.
- `erp_export.motor_prices_export` lets the ERP pull `unit_cost_per_motor` and `factory_price_per_motor` (read-only).
- Procurement (`procurement.json`) holds supplier, PO, lead time, and receipts with accepted / rejected quantities,
  which is a measured incoming-rejection rate. The schedule phases have `owner_role` us / factory. The assembly
  process (`process_steps.py`) has steps s1..s13 (s3 hipot, s8 balancing, s12 R check, s13 ESC run), but no times
  or rates.

### 1.3 Tests (`motor_tests/`) and Development (`development/`)

- Test records (parameters / free_run / load) store operator, instrument, date, and per load row `duration_s`. Nothing
  records bench hours, staff hours or energy.
- Project statuses: `spec, design, simulation, quote, prototype, production, closed`. The spec's `production` section
  has `quantities` (prototypes, pre-series, series per year), `timeline`, `target_price`, `tests`, `certification`,
  `packaging_delivery` and the `*_supplier` fields.
- Inverter device cards already carry `price: {amount, currency, quantity, source, dated}`. That is the pattern this
  design generalises.

### 1.4 Gaps (what blocks a real quote)

| # | Gap | Where |
|---|---|---|
| G1 | No source, date or validity on any price. `note` is free text. | `normalize_cost_row`, `CostPanel` |
| G2 | **Currencies are summed without conversion.** A CNY row plus a EUR row gives one number. `erp_export` only flags `mixed_currencies`. | `compute_cost_summary` |
| G3 | `normalize_cost_row` does not validate the currency. The extras do (CNY/USD/EUR). | `production/models.py` |
| G4 | `unit_price` on the motor list means "cost of the line PER MOTOR", but on the tooling list it means "price of ONE piece". Same field, two meanings. | `routes._price_rows` |
| G5 | **The OEM with the flag on also receives `margin_pct` and `price_per_motor`**: the summary is returned whole. Sharing costs currently also shares our margin and our price. | `GET .../bom`, `GET .../costs` |
| G6 | No process cost: no times, rates, setup, batch operations (acid etch, impregnation) or learning curve. | `process_steps.py` |
| G7 | Tooling is never amortised. It is a separate total per production, although one die serves several configurations (one CILN28 die for M1/G1/G2). | `bom_tooling.json` |
| G8 | No test cost: no bench hours, staff hours, energy, consumables, fixtures or calibration. | `motor_tests/` |
| G9 | Only one quantity (`order_quantity`). No quantity breaks, no scrap, no NRE vs per-unit split. | `production.json` |
| G10 | No freight, duty, VAT or incoterm. No FX rates anywhere in the code. | — |
| G11 | No quote object, no workflow, no customer view. The project status `quote` is only a label. | `development/` |
| G12 | Project status PATCH is allowed for project role `owner`. In a self-service project that owner is the CUSTOMER (`self_service.py`), so a customer can set `quote` or `production` himself. | `development/routes.py:532` |
| G13 | MCP has a geometry denylist (`assert_no_geometry`) but no cost or price denylist. | `mcp_tools.py` |
| G14 | The Cost tab and production costs are two unrelated cost models. The tab's masses never reach the BOM, and the BOM prices never reach the tab. | — |

---

## 2. Data model

New package `src/motor_ai_sim/costing/`. It sits next to `production/` and reuses its store helpers (`json_store`
atomic writes, `audit.jsonl`). Storage:

```
<shared>/costing/
  price_book.json        catalog + quoted prices (append-only versions)
  rates.json             factory, machine, bench and staff rates
  fx_rates.json          dated currency rates
  tooling/<die>.json     tooling assets of one die, shared by its configurations
  audit.jsonl
<shared>/production/<die>/<config>/costing/
  cost_model.json        how this motor is built and tested, for costing (links to BOM / steps / tests)
  estimates/<id>.json    frozen estimate snapshots (never edited after freeze)
  quotes/<id>.json       quote drafts and released quotes
```

The die folder name follows `die_rename` (a new step `step_costing`), so a rename carries the costing data with it.

### 2.1 Money and source (used by every number)

```json
"money":  {"amount": 12.40, "currency": "CNY", "vat_included": false, "incoterm": "EXW", "place": "factory"},
"source": {"kind": "supplier_quote | invoice | catalog | measured | estimate | model",
           "ref": "production document id / directory document id / test id / price_book id@version",
           "company_id": "directory company id or null",
           "date": "2026-10-28", "valid_until": "2026-12-31",
           "by": "email", "note": "short"}
```

- The currency must be one of `CNY`, `EUR` or `USD`. This fixes G3, and new currencies are added only on purpose.
- `vat_included` is mandatory for CNY prices, because Chinese factory prices are quoted with or without the VAT
  invoice. A missing value counts as an error, not a default.
- `kind: estimate` is allowed, but it raises the risk allowance (2.7) and is listed in the estimate's "open
  sources".
- `model` is used for quantities, not prices: a mass from the simulation, a lamination count from the geometry.

### 2.2 Price book entry (catalog and quoted prices)

```json
{"id": "pb_magnet_N52UH_seg", "version": 3, "category": "magnet | wire | lamination_steel | liner | varnish |
  adhesive | fastener | bearing | housing_part | machining | cutting | packing | freight | other",
 "key": {"grade": "N52UH", "coating": "NiCuNi", "segmented": true, "dims_mm": [20, 6, 5]},
 "unit": "kg | pc | m | m2 | ml | h | batch | lot | shipment",
 "breaks": [{"min_qty": 1, "money": {...}}, {"min_qty": 500, "money": {...}}],
 "source": {...}, "status": "active | expired | superseded"}
```

A new version is a new record. Old versions are never overwritten, so an estimate that used `@2` can be recomputed
exactly. Examples of the owner's items and how each one is keyed:

| Item | Key fields | Unit |
|---|---|---|
| Magnets N52UH, N48SH ... | grade, coating, segmented, piece dims | pc (preferred: one segment is one priced piece), kg as fallback |
| Wire | flat / round, size (w x h or d), enamel grade, strands | kg |
| Lamination steel | grade, thickness (e.g. 0.2 / 0.35 mm), coating | kg (bought sheet, see 2.3) |
| Lamination cutting | method `laser / wire_edm / stamping_die`, steel thickness | per lamination or per m of cut; stamping per stroke |
| Acid etch | batch time >= 24 h (owner rule), stator and rotor in separate batches | batch |
| Ceramic liner | Al2O3, 0.25 mm plates (standard), impregnated | pc or m2 |
| Impregnation | varnish or epoxy, oven cycle | kg of varnish + batch |
| Loctite 641 | on every bearing / shaft / hub fit | ml (qty from the number of fits) |
| Screws | zinc-plated (blue-white), ISO 4762 size | pc |
| Bearings | catalog id (the bearing card) | pc |
| Housing, holders, shaft | made parts: the drawing revision is part of the key | pc (factory or machine-shop quote) |
| Packing, shipping | box type; DHL / FedEx service, weight band, destination zone | pc / shipment |

### 2.3 BOM-to-cost link (`cost_model.json` -> `items`)

Each production BOM row (`part_id`) gets one costing item. It does not duplicate the BOM; it says how the quantity of
bought material follows from the part:

```json
{"part_id": "pt_magnet", "price_ref": "pb_magnet_N52UH_seg",
 "qty_basis": "bom | model_mass | model_count | manual",
 "qty_per_motor": 40, "unit": "pc",
 "buy_factor": 1.0, "scrap": {"rate": 0.02, "source": {...}},
 "supplied_by": "factory | us | customer"}
```

- `model_mass` takes the mass from `masses.compute_masses` (active parts) or `cad/bom.py` (full assembled mass) of the
  agreed configuration, and records which one it used and from which result.
- `buy_factor` turns used mass into bought mass. For laminations that is the sheet utilisation (an annulus cut from a
  square pitch: bought = used / u). For copper it covers lead and trimming allowance.
- `scrap.rate` is the material lost in production. A measured value comes from the procurement receipts
  (rejected / qty) or from failed in-process checks (s3 hipot). Line cost = qty x buy_factor x price / (1 - scrap).
- `supplied_by: customer` keeps the line in the BOM at zero cost, with a quote note "free-issue by customer". This
  covers the spec's `magnets_supplier` / `controller_supplier` / `housing_supplier`.
- The existing `costs.json` rows stay readable (backward compatible). Stage 1 adds `source`, `price_basis:
  "per_motor_line" | "per_piece"` (this fixes G4: the field says what it means) and `vat_included`.

### 2.4 Process operations with times and rates (`cost_model.json` -> `operations`)

```json
{"id": "op_s8_balance", "step_id": "s8", "name": "Balance the rotor to G2.5",
 "kind": "per_unit | per_batch | per_lot | per_order",
 "setup_min": 20, "cycle_min": 6, "operators": 1, "batch_size": null,
 "rate_ref": "rate_factory_labour_A", "machine_rate_ref": "rate_balancer",
 "performed_by": "factory | us", "learning_rate": 0.9,
 "source": {"kind": "measured", "ref": "time study doc id", "date": "...", "units_observed": 6}}
```

- `step_id` links to an assembly-graph step when there is one (s1..s13). Operations outside the graph (lamination
  cutting, acid etch, impregnation, machining, incoming inspection, packing) have no `step_id`.
- `per_batch`: cost per unit = (setup + batch time) x rate / units per batch. This covers the acid etch (>= 24 h is
  lead time, not labour: only loading, unloading and checking are labour) and the impregnation oven.
- `per_lot`: incoming inspection per received lot, with the quantity taken from the procurement receipts.
- Learning curve (Wright): the time of unit n is t1 x n^b, with b = log2(LR). The average over a lot of Q is used.
  It applies to labour only, never to material.
- Rates (`rates.json`): `{id, kind: labour | machine | bench | staff | energy, company_id, money per h or per kWh,
  source}`. A factory rate belongs to the factory company in the directory.

### 2.5 Tooling amortisation over the motors that share a die (`tooling/<die>.json`)

```json
{"id": "tl_stamp_die_stator", "die": "<die name>", "kind": "stamping_die | mold | coil_press | fixture |
  balancing_arbor | test_fixture", "money": {...}, "source": {...},
 "scope": "die | production",
 "method": "per_stroke | per_unit | written_off | customer_paid",
 "life": {"strokes": 2000000, "units": null},
 "plan_volume": {"units": 5000, "set_by": "owner", "date": "..."},
 "consumed": [{"production": "<die>/<config>", "units": 30, "strokes": 7800, "date": "..."}]}
```

- One asset serves every configuration on the die (M1/G1/G2 on one CILN28 die). `scope: die` lives at die level.
  Tooling for one motor only (a coil press for one coil length) stays `scope: production`, and the existing tooling
  list maps onto it.
- `per_stroke` is used for a lamination stamping die. Its wear follows the laminations punched, so a longer stack pays
  more: strokes per motor = laminations per motor (stack length x k_f / sheet thickness, stator and rotor, or one
  stroke for both with a compound die).
- `per_unit` is used for fixtures. Amortisation per motor = (cost - already recovered) / remaining planned volume. The
  `consumed` ledger shows what each production has paid, so the die is never paid twice.
- `customer_paid` means the customer pays the tooling once as NRE in the quote. The asset stays ours or becomes his
  (open question 6).
- The cutting route depends on the quantity tier: prototypes use laser or wire EDM (no die, plus the >= 24 h acid
  etch against the cut-edge shorts), and the series uses the stamping die. The tier (2.8) picks the route.

### 2.6 Test plan cost (`cost_model.json` -> `test_plan`)

```json
{"id": "tp_load_dyno", "test": "parameters_RLEMF | free_run | load_propeller | load_dyno | thermal_run | hipot |
  surge | insulation | balancing | first_article | type_test_other",
 "scope": "every_unit | sample | first_article | type_test",
 "sample": {"n": 2, "per_lot": 50},
 "performed_by": "factory | us",
 "bench_ref": "rate_bench_dyno", "bench_h": 3.0, "staff_ref": "rate_staff_test", "staff_h": 4.0,
 "energy_kwh": 6.0, "consumables": [{"price_ref": "pb_thermocouple", "qty": 4}],
 "fixture_ref": "tl_dyno_adapter", "source": {...}}
```

- `every_unit` and `sample` are per-unit costs. `type_test` and `first_article` are NRE, charged once per design or
  per order.
- The bench rate includes calibration: the annual calibration and maintenance cost divided by the bench hours booked
  per year. Its source is the calibration invoice.
- Energy: electrical input = shaft power / (eta_motor x eta_drive) x hours, less the regenerated share when the dyno
  feeds back (stated per bench).
- Measured times come from `motor_tests` once data exists: the record dates, the `duration_s` sum and the operator.
  Stage 2 adds the optional fields `bench_h` and `staff_h` to a test record. Until then the time is `estimate`.
- The customer's required tests (spec `production.tests`, `certification`) map onto plan items. A required test that
  has no plan item blocks the estimate.

### 2.7 Overhead, risk and margin (`cost_model.json` -> `commercial`, owner-only)

- `overhead_pct` on our own labour and bench (factory overhead is already inside the factory rate), and `ga_pct` on the
  total cost.
- Risk allowance per line by source kind (defaults are open question 3): quote and measured 0 %, catalog 5 %,
  estimate 15 %, missing = **blocks** the quote (it is never silently zero). Plus `fx_buffer_pct` on lines in a
  currency other than the quote's, and `warranty_pct`.
- Margin keeps today's meaning: price = cost / (1 - margin). A different margin may be set per tier.

### 2.8 Currency and quantity breaks

- `fx_rates.json`: `[{date, base: "EUR", rates: {CNY, USD}, source: "ECB reference | bank | manual", by}]`. Base
  currency EUR (MOTRES d.o.o.). Every amount is stored in its original currency. An estimate converts at the rate of
  its `fx_date` and freezes that rate inside the snapshot. A rate older than 7 days gives a warning. This fixes G2:
  a sum over mixed currencies without a rate is refused.
- Quantity tiers come from spec `production.quantities` (prototype 1-3, pre-series, series per year). The owner can
  add or remove tiers. For each tier the estimate picks the price break, the cutting route, setups / lot, the learning
  curve average, test sampling and tooling amortisation. Output: one column per tier plus an NRE column.
- Landed cost per tier: + freight per unit (shipment quote / units per shipment) + import duty % x customs value (HS
  code and rate entered with source and date) according to the quote's incoterm. VAT is shown separately and is never
  part of the cost.

### 2.9 Estimate snapshot (the recalculation trail)

```json
{"id": "est_...", "production": "<die>/<config>", "project_id": "...", "parent": "est_prev or null",
 "inputs": {"spec_revision": "...", "production_revision": "1.4", "config_hash": "...",
            "mass_source": {"kind": "masses.compute_masses | cad_bom", "result_ref": "..."},
            "price_refs": ["pb_...@3", ...], "rate_refs": [...], "tooling_refs": [...], "fx": {...}},
 "tiers": [1, 3, 50, 500],
 "lines": [{"category": "material | purchased | process | tooling | test | packing_freight | duty | overhead | risk",
            "ref": "part_id | op id | tooling id | test id", "qty": ..., "unit": ..., "unit_money": {...},
            "amount_base": {"1": ..., "50": ...}, "basis": "0.412 kg x 1/0.46 sheet x 1/(1-0.02)",
            "source": {...}, "confidence": "quoted | measured | catalog | estimate"}],
 "totals": {...}, "nre": {...}, "open_sources": [...], "warnings": [...],
 "diff_vs_parent": [{"line": "...", "was": ..., "now": ..., "why": "price pb_magnet@3 -> @4"}],
 "state": "draft | ready | reviewed | frozen", "by": "...", "at": "..."}
```

- Estimates are pure functions of their inputs (`costing/compute.py`, no I/O), so a snapshot can be recomputed and
  must give the same totals. A test pins this.
- A change of the spec, the production revision, a price or a rate never edits a frozen estimate. The next estimate
  has `parent` and `diff_vs_parent`. Every production-side change is already in `audit.jsonl`, and the costing audit
  adds who changed which price, rate or estimate.

---

## 3. Where each number comes from

| Number | Primary source | Fallback (flagged) | Stale when |
|---|---|---|---|
| Copper, steel, magnet, shaft mass | `masses.compute_masses` of the agreed configuration (the Simulation card's numbers) | — | config hash changes |
| Full part masses (holders, rings, screws, board) | `cad/bom.py` of the built CAD model | "partial mass, no CAD model" | CAD rebuilt |
| Laminations per motor, strokes | geometry: stack length, k_f, sheet thickness | — | geometry changes |
| Magnet pieces per motor | poles x segments per pole x axial segments, from the config | BOM qty | config changes |
| Part prices | supplier quote / invoice as a production or directory document (`proforma`, `invoice`, `PO`) | price book catalog, then `estimate` | `valid_until` passed, drawing revision changed |
| Material price per kg | price book (supplier quote) | catalog | > 90 days (open question 4) |
| Factory labour and machine rates | factory quote (directory company of role `oem_producer`) | estimate | quote expiry |
| Operation times | measured time study (document), factory routing | estimate | process step changed |
| Scrap rates | procurement receipts (rejected / qty), failed checks | default per category | new receipts |
| Tooling cost | tooling supplier quote / PO | estimate | — |
| Plan volume for amortisation | owner | spec `quantities` | owner change |
| Test times, energy | `motor_tests` records (dates, `duration_s`, operator) + bench log | estimate | — |
| Bench, staff, energy rates | our own rates (calibration invoice, staff cost, energy bill) | — | yearly |
| Freight | DHL / FedEx quote for weight band and zone | carrier tariff | quote date |
| Duty | HS code + rate (entered with source and date) | — | yearly |
| FX | dated rate (ECB reference or bank) | — | > 7 days |

---

## 4. Outputs

1. **Internal cost breakdown** (staff). Per tier plus NRE, by category and rolled up the assembly tree (stator /
   rotor / housing, reusing `build_assembly_tree`). It shows the source and date of every line, confidence, open
   sources, stale lines and `diff_vs_parent`. Stage 3 adds a sensitivity line: magnet price +-20 %, FX +-5 %,
   scrap x2.
2. **Quote draft for the owner** (never sent automatically). It is built from a `reviewed` estimate: price per tier,
   NRE (tooling, type tests, first article), included tests, incoterm and place, validity date, lead time (from the
   production schedule), payment terms, exclusions and free-issue items. The owner edits it, and a PDF / docx is
   generated for him. Sending is a separate act. Any message or e-mail follows the owner rule: draft, show the final
   text and what the recipient will see, and send only after his yes. A quote may hold several options, for example
   ripple variants of one motor (the owner rule on ripple variants), each with its own tiers.
3. **OEM view** (only with `share_costs_with_oem`). It shows only the factory-side lines: the parts the factory buys,
   the operations and tests it performs, its own rates and the tooling it holds. Never our margin, risk, G&A, other
   factories' quotes, the price or the quote. This also fixes G5 for the existing BOM summary.
4. **Customer view** (only after the owner releases the quote). It appears in the Development project as a "Quote"
   section with the released quote document: options x tiers, NRE, inclusions, validity and terms. No breakdown, no
   supplier names, no margin. Before release the section does not exist for the customer; it is not shown as empty or
   locked.
5. **ERP export** (stage 2). `erp_export` additionally publishes the frozen estimate totals and the released quote
   (read-only pull, as today), with the currency stated and no mixed-currency sums.

---

## 5. Roles and access matrix

Cost visibility follows Motres staff identity and production membership, never the project role alone: in a
self-service project the project owner is the customer (see G12).

| Action | Admin (owner) | Motres engineer (global engineer, or production owner / engineer) | Project owner, customer side | Project customer | OEM, flag off | OEM, flag on | MCP agent | Anonymous |
|---|---|---|---|---|---|---|---|---|
| Edit price book, rates, FX | yes | yes (audited) | no | no | no | no | no | no |
| Edit tooling of a die, plan volume | yes | assets yes, plan volume no | no | no | no | no | no | no |
| Edit cost model of a production | yes | yes | no | no | no | proposes times / prices via Proposals | no | no |
| Create / compute estimate | yes | yes | no | no | no | no | no | no |
| Mark estimate `ready` | yes | yes | no | no | no | no | no | no |
| Review / freeze estimate | yes | no (open question 7) | no | no | no | no | no | no |
| Edit commercial (overhead, risk, margin) | yes | no | no | no | no | no | no | no |
| See internal breakdown | yes | yes | no | no | no | factory-side lines only | no | no |
| Create / edit quote draft | yes | prepare only | no | no | no | no | no | no |
| Release quote | yes | no | no | no | no | no | no | no |
| See released quote | yes | yes | yes | yes | no | no | **no** | no |
| Accept quote | records it | records it | yes (in app or by signed PO) | no | no | no | no | no |
| Kernel `cost` capability | yes | yes | no | no | no | no | no | no |

- MCP: no costing route and no cost key in any MCP output. A `COST_DENYLIST` (`unit_price`, `price`, `cost`, `margin`,
  `quote`, `amount`, ...) is enforced the same way as `assert_no_geometry`, with a test over every MCP tool's output.
- Non-members get 404 on costing routes, the same rule the production module uses.

---

## 6. Workflow states and who triggers them

Project status (existing) and costing state (new) move together:

| Project status | Costing state | Trigger | Who |
|---|---|---|---|
| spec / design / simulation | `none` (an internal what-if in the Cost tab is allowed for staff, never stored as an estimate or shown outside; open question 1) | — | — |
| simulation -> **quote** | `estimating` (a costing workspace is created; the agreed design is frozen: spec revision, linked configuration(s), result ids of the coupled key points) | "Design agreed, start costing" | admin / Motres engineer only (fixes G12) |
| quote | estimate `draft` -> `ready` | engineer computes; no missing sources, no expired quote, all required tests planned | engineer |
| quote | `ready` -> `reviewed` | owner checks; for a first quote of a new motor family, an independent review (Astra per the global rules) | admin |
| quote | quote `draft` -> `approved` | owner edits prices, terms and final text | admin |
| quote | `approved` -> `released` | owner clicks Release; the customer view appears; any e-mail only after the owner's OK on the final text | admin |
| quote | `released` -> `accepted` / `rejected` / `expired` | customer accepts (or a signed PO is recorded); validity date passes | customer / admin |
| quote -> prototype / production | `accepted` -> `paid` | payment recorded (proforma / invoice in the directory documents, or the ERP) | admin |
| prototype / production | `baseline` | the accepted estimate becomes the cost baseline; `order_quantity` comes from the accepted tier | automatic on `paid` |

- Any change after release (spec, production revision, price, rate) marks the released quote `stale` for staff. The
  customer keeps seeing the released one until the owner releases a new revision. A spec change still follows the
  owner rule: it shows what changed and which recalculations it triggers (coupled thermal + mechanics of the key
  points) before it is applied.
- Going back from `quote` to `design` closes the costing workspace. Its estimates are kept read-only and never deleted.

---

## 7. Staged plan

### Stage 1: MVP (one honest number with sources, nothing customer-facing)

Backend:
- `src/motor_ai_sim/costing/__init__.py`, `models.py` (money / source / price entry / rate / FX / item validation,
  loud 422), `store.py`, `fx.py`, `compute.py` (pure: production BOM + costs + model masses + one tier -> estimate),
  `access.py`, `routes.py` (`/api/costing/price-book`, `/rates`, `/fx`, `/api/costing/{die}/{config}/model`,
  `/estimates`). Register in `api.py`, and add the auth rows (staff only) in `auth.py`.
- `production/models.py`: `normalize_cost_row` gains `source`, `price_basis` and `vat_included`, and validates the
  currency (G3, G4). `compute_cost_summary` converts with a rate or refuses mixed currencies (G2).
- `production/routes.py`: for `oem` the summary drops `margin_pct`, `price_per_motor` and `order_price_total` even with
  the flag (G5).
- `auth.py` / `routes/kernel.py`: the `cost` capability is staff-only (1.1).
- `mcp_tools.py`: `COST_DENYLIST` and `assert_no_cost` (G13).
- `development/routes.py`: status `quote` and later are set only by admin / global engineer (G12).
- `die_rename.py`: `step_costing`.

Web:
- `web/src/modules/costing/` (`api.ts`, `types.ts`, `components/CostingPanel.tsx`): a production sub-tab "Costing"
  (staff) with lines, sources, warnings and one tier. `BomTable.tsx` gets source / date columns.
- `CostPanel.tsx`: take the prices from the price book (the catalog kind), remove the duplicated defaults, and keep it
  as the admin what-if tab.

Tests:
- `tests/test_costing_models.py`: validation of money, source and currency, and the CNY `vat_included` rule.
- `tests/test_costing_compute.py`: a hand-checked example (mass x buy factor x price / (1 - scrap), FX at a fixed
  rate, sum per category), plus recompute of a frozen snapshot giving the same totals.
- `tests/test_costing_access.py`: the matrix of section 5, including the OEM flag on / off, a customer project owner,
  and anonymous gets 404.
- `tests/test_production_costs.py`: refusal on mixed currencies, OEM summary without margin and price.
- `tests/test_mcp_no_cost.py`: no MCP tool output contains a denylisted key.
- `web/src/modules/costing/__tests__/costingModel.test.mjs`.
- Run only these files (CPU rule), not the whole suite.

### Stage 2: full cost and the quote

- Operations with times and rates, learning curve, batch operations (acid etch, impregnation), incoming inspection
  from receipts. Extend `process_steps.py` steps with an optional `cost_ref`.
- Die-level tooling with `per_stroke` / `per_unit` amortisation and the `consumed` ledger. Map the existing tooling list
  onto it.
- Test plan from spec `production.tests` and `certification`. Optional `bench_h` / `staff_h` on `motor_tests` records.
- Quantity tiers, price breaks, cutting route per tier, NRE column, freight / duty / incoterm.
- Quote object, PDF / docx for the owner, release, customer "Quote" section in Development (`web/src/modules/development/`),
  workflow states of section 6, the OEM factory-side view, ERP export of estimate and quote.
- Tests: `test_costing_tiers.py`, `test_costing_tooling.py` (two configurations on one die share the amortisation, and
  the die is never paid twice), `test_costing_quote_workflow.py` (no customer visibility before release, stale after a
  change), `test_costing_oem_view.py`.

### Stage 3: actuals and the controller

- Actual vs estimate: procurement prices and receipts, measured operation and test times give the variance per line,
  which feeds the next estimate's defaults.
- FX import from the ECB reference rates (admin-triggered, dated, source stored).
- Sensitivity view, and the first real quote audited by an independent reviewer.
- `product_type: electronics`: PCB fabrication, components (from the KiCad BOM), SMT placement, conformal coating,
  hipot / double-pulse / full-power tests (process steps e1..t5 already exist).
- Every frozen estimate and released quote is logged to the engineering dataset (topic `business_erp`), as the dataset
  rules require. Prices are allowed there; secrets are not.

---

## 8. Open questions for the owner

1. During design, may staff run internal what-if costing (e.g. to choose between N52UH and N48SH)? This design keeps
   it inside the admin Cost tab only, never stored as an estimate and never shown to anyone outside.
2. Base currency EUR for estimates, and which currency for quotes: always EUR, or per customer (USD / CNY)?
3. Risk allowance defaults per source kind (quote 0 %, catalog 5 %, estimate 15 %), FX buffer and warranty
   reserve: what values?
4. How old may a catalog price be before it counts as stale (90 days?), and a supplier quote: its own validity only?
5. Learning-curve rate for labour (e.g. 90 %), or no learning curve until we have measured times?
6. Tooling: amortise over a planned volume set by you per die, or charge it to the first customer as NRE? If the
   customer pays the die, who owns it, and does it still serve our other motors on that die?
7. Who may review / freeze an estimate: only you, or also a named Motres engineer?
8. Do we charge engineering or simulation hours in the quote (NRE), or only production, tooling and tests (simulation
   for data is free)?
9. With `share_costs_with_oem` on, should the factory see only its own lines (this design), or the whole cost without
   margin?
10. Who performs which test by default: factory (every-unit hipot, R, balancing, ESC run) and us (load, thermal, type
    tests)? Do our bench hours enter the price at a fixed rate?
11. Should the spec field `target_price` stay? It is customer input during design. The module will never compare a
    cost with it in anything a customer sees.
12. Quote validity (30 days?) and default payment terms (e.g. advance before production)?
13. Incoterm default for quotes: EXW factory, or DAP to the customer with DHL / FedEx included?
14. Can a customer accept in the app, or only by a signed PO / proforma that we record?
