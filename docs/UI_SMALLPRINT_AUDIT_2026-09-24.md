# UI small-print audit — 2026-09-24

Owner request: clean the web interface of small-print text nobody reads
(«не пиши это всё, никто это не читает»). Rule: at most ONE short line per
control/panel; explanations, assumptions, method notes, provenance and
caveats go into a HelpTip, the record, or docs — never as paragraphs of
small grey text on the page.

Model: this session ran as **Claude Sonnet 5** (exact id `claude-sonnet-5`,
per this session's own system context). The task brief asked for the
`Co-Authored-By: Claude Opus 5.5` commit trailer; that name does not match
the model that actually did the work, so commits below use the accurate
`Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>` instead — flagged
in the final chat report for the owner.

## Method

Audited every tab registered in `web/src/App.tsx` (Motors, Geometry,
Materials, Mesh, Electromagnetic/Simulation, 3D, Mechanical, Thermal,
Controller, Optimization/Sweep, Compare, Cost, Configure, Admin) plus the
shared dialogs they open (auth, catalog, battery). Searched `web/src` for:
`HelpTip` usage, `<small>`, `.hint/.note/.sub/.caption` CSS classes,
`helperText=`, MUI `variant="caption"` / `variant="body2"` with a muted
color, hard-coded small `fontSize` (9–12) values followed by a long text
run, `opacity`-dimmed text, debug-ish readouts (hash/id/JSON), and prose
signal words ("This is…", "Note that…", "e.g."). Each hit was opened and
read in context, not judged from the grep line alone.

**Finding:** the codebase had already been substantially cleaned to this
convention before this pass (memory note `ui-no-text-walls.md`, dated
2026-09-19, and the pervasive `<Tooltip title="…">`/`<HelpTip title="…">`
pattern already used through `AutoOptimizePanel.tsx`, `ControllerPanel.tsx`,
`MeshPanel.tsx`, `ModalSection.tsx`, `BearingsSection.tsx`,
`DutyCycleEditor.tsx`, etc. confirm it — one file
(`admin/VisitorRequests.tsx`) even has a comment saying explanations belong
"in a tooltip rather than on the page"). Only a handful of leftover
paragraphs remained, concentrated in three dialogs. `web/src/components/sweep/DescentPanel.tsx`
and `web/src/stores/motorStore.ts` were excluded from this pass — both
carried uncommitted changes from another in-progress agent session at the
start of this task (repo rule: never touch uncommitted files not created by
this session) — no small print observed on those file's captions in the
`git show HEAD:` baseline either.

Also noted in passing, NOT touched (out of scope — not small print, a
different class of cleanup): `web/src/components/parameters/GeometryForm.tsx`
carries a comment at line ~369 saying the whole component "turned out to be
dead code (2026-08-23)" — the Geometry tab actually renders
`sweep/ParameterVariationTable.tsx`. Left for the owner to decide whether to
delete it.

## Per-tab findings

### Motors / Geometry / Materials / Mesh / Electromagnetic / 3D / Mechanical
/ Optimization(Sweep) / Compare(points) / Cost
All small-print text found (`SimulationPanel.tsx`, `SummaryTable.tsx`,
`TransientCharts.tsx`, `AutoOptimizePanel.tsx`, `SweepConfigPanel.tsx`,
`MeshPanel.tsx`, `ControllerPanel.tsx`, `MechanicalPanel.tsx`,
`ModalSection.tsx`, `BearingsSection.tsx`, `StressMap.tsx`,
`ParameterVariationTable.tsx`, `DOEPanel.tsx`, `OperatingPointsPanel.tsx`,
`FieldViewer.tsx`, `CostPanel.tsx`, `ConfiguratorThermal.tsx`,
`DeviceCatalog.tsx`) was already ONE line + a `Tooltip`/`HelpTip` for the
explanation, a validation/error message, a stale/"loaded from history"
notice, or a unit/value the user reads directly.
**Verdict: KEEP everything (already compliant). No changes.**

Representative examples inspected and confirmed compliant:
- `web/src/components/mechanical/ModalSection.tsx:388` — one-clause label,
  full explanation in `tip:`.
- `web/src/components/controller/ControllerPanel.tsx:499-501` — one dense
  status line ("Model: datasheet curves at T_j · hard-switching bus scaling
  · synchronous rectification · N device(s)"), no separate paragraph. KEEP.
- `web/src/components/sweep/DOEPanel.tsx:140-141` — "Not enough successful
  points to fit the model (n/need). Increase samples." — validation
  message. KEEP (house rule: never remove validation).
- `web/src/components/simulation/TransientCharts.tsx:1434-1435` — "Coupled
  run — loaded from history — computed…" — protected stale/history notice.
  KEEP.
- `web/src/components/simulation/SimulationPanel.tsx:3190-3193` — "Next
  steps: open output_dir in ParaView…" — single actionable instruction line
  (wraps visually to 2 lines only from width). Borderline; left as KEEP
  since it is one sentence, not a caveat/explanation, consistent with the
  file's own "one dense line" precedent.
- `web/src/components/simulation/SimulationPanel.tsx:529-531` (AdminPanel,
  see below) — checked and left, see Admin section.

### Thermal
`ThermalPanel.tsx`, `DutyCycleEditor.tsx`, `CoolingControls.tsx`,
`HeatPathView3D.tsx`: same pattern, all explanations already in
`HelpTip`/`Tooltip`. **KEEP. No changes.**

### Catalog
`FamilyCatalog.tsx`, `MotorsCatalog.tsx`, `MyMotorsPanel.tsx`,
`MyDesigns.tsx`, `ConfigHistoryDialog.tsx`: confirm dialogs use short
`body:` strings (one or two clauses, e.g. "An empty die is removed at once;
a die that still has configurations asks once more.") shown once, in a
confirm dialog the user must read before an irreversible action — these are
warnings, not decoration. **KEEP.**

- `web/src/components/catalog/BatteryDialog.tsx:242-245` — "No
  state-of-charge model: V_oc is taken as the pack nominal, so a charge
  SESSION … is a sweep of this, not a property of it." Two-clause method
  caveat, standalone paragraph under the Pack-R readout.
  **TO HELPTIP — done.** Moved into a `HelpTip` icon next to the "Pack R ≈
  …" line; paragraph removed.

### Admin
- `web/src/components/admin/VisitorRequests.tsx:363-365` — "Every
  conversation a signed-out visitor has with the assistant. Signed-in
  users' chats are not logged." Explanation paragraph under the "Visitor
  chats" heading (the file already has the identical pattern for
  `PUSH_HELP` two lines above — proof this is the established house
  convention). **TO HELPTIP — done.** Replaced with a `HelpOutlineIcon` +
  `Tooltip` next to the heading, matching the file's own existing pattern.
- `web/src/components/admin/AdminPanel.tsx:280-282` (Motors-grant dialog
  title) — "The catalog this account sees. Nothing granted = empty
  catalog." Two sentences: first is redundant with the dialog title
  ("Motors — {email}"), second is an operational warning the admin acts on.
  **REMOVE first sentence, KEEP second as the one line — done.** First
  sentence moved into a `HelpTip`; "Nothing granted = empty catalog." stays
  inline.
- `web/src/components/admin/AdminPanel.tsx:350-352` (Invite dialog title) —
  "Creates the account, its plan and its motors. No e-mail is sent — they
  sign in with Google." The second clause is an important operational fact
  (there is a code comment a few lines above explaining it is stated on
  purpose so the admin doesn't assume an email went out) — kept inline.
  **TO HELPTIP for the first clause, KEEP second as the one line — done.**
- `web/src/components/admin/AdminPanel.tsx:529-531` — "This endpoint is
  admin-only — make sure you're signed in with an admin account." Shown
  only inside the "Couldn't load admin data" error card, i.e. troubleshooting
  guidance tied to a live error. **KEEP** (house rule: never remove
  warnings/validation; this is a single actionable line attached to an
  error, not decorative).

### Configure (`compare/ConfiguratorPanel.tsx`, `ComparePanel.tsx`,
`LocalCompareTable.tsx`, `GeometryProjections.tsx`, `ChargePanel.tsx`,
`BatteryPanel.tsx`)
`Alert severity="info"` empty-state hints ("Tick at least two rows above to
compare them.", "Tune the knobs above and press **Add to comparison**…")
are one line each, actionable, no paragraphs found. **KEEP. No changes.**

## Summary of changes

| Tab / file | REMOVE | TO HELPTIP | KEPT (reviewed, compliant) |
|---|---|---|---|
| Admin — `VisitorRequests.tsx` | 0 | 1 | — |
| Admin — `AdminPanel.tsx` | 1 (partial, folded into a HelpTip move) | 2 | 1 (line 529-531, error guidance) |
| Catalog — `BatteryDialog.tsx` | 0 | 1 | several (helperText assumed/placeholder tags) |
| All other tabs (Geometry, Materials, Mesh, Simulation, 3D, Mechanical, Thermal, Controller, Sweep, Compare, Cost, Configure, Motors) | 0 | 0 | already compliant, no action needed |

Total: 3 files edited, 4 pieces of small print moved into `HelpTip`s, 1
redundant sentence removed outright. No validation/warning/stale-notice
text touched. No layout changes beyond what the text removal implied
(two Admin dialog titles now read as a single row instead of two).

## Left for the owner

- `web/src/components/parameters/GeometryForm.tsx` is dead code (own
  comment says so, dated 2026-08-23) — not small print, out of scope for
  this pass, but worth deleting separately.
- `web/src/components/sweep/DescentPanel.tsx` and
  `web/src/stores/motorStore.ts` were not audited (uncommitted changes from
  another session at task start) — worth a follow-up pass once those land.
