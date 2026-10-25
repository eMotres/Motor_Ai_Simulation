# Agents working in motor_ai_sim — read before the first edit

1. Working rules (shared worktree, path-limited commits, the live API on 8001
   is read-only, deploy lock, model choice, conventions):
   `C:\Users\vadim\Projects\AGENT_RULES_motor_ai_sim.md`
2. Dataset rules (log every finished task, physics provenance, knowledge
   notes): `C:\Users\vadim\Projects\AGENT_DATASET_RULES.md`
3. Project guide (architecture, quick start, conventions): `CLAUDE.md` here.

Both rule files are mandatory for every agent and every tool (Claude Code,
Codex, others). Read them at the start of every session.

4. Private design rules (one set for all motors and all design aspects): motor-ai-sim-private/docs/DESIGN_RULES.md -- read before editing any motor, die, CAD model or board.
5. NEVER commit a 3-D model (STEP, FCStd, GLB, STL, BREP, IGES, KiCad board, DXF) or the data of a closed motor (die.yaml, passport, S1 model, BOM, Fusion parameter dump, reference profile) to this PUBLIC repository (owner 2026-10-19): they live in the private repository eMotres/motor-ai-sim-private under the same relative path (see docs/OPEN_PRIVATE_DATA.md, "3-D models and closed-motor data"). `scripts/check_no_private_data.py` runs in CI and in the test suite and fails the pull request.

## UI text rules (owner 2026-10-26: repeated "a thousand times" - follow them on every screen the customer sees)

- **No caption or helper lines.** No small grey line under a field, tile, panel or table that only explains something
  (no `Typography variant="caption"`, no `helperText=`, no 9-11 px grey sentence, no "·"-joined summary line). One short
  label + a tooltip (`Tooltip` / `HelpTip`): whatever the line said goes into the tooltip of the field or tile label.
- **No min-max ranges on screen.** Show ONE value, the worst one for the calculation (the maximum allowed / worst case);
  the other bounds may live in the tooltip. A stored range is read as its worst value, never rewritten.
- **Keep** real values, units, table headers and validation errors. A validation error is loud (red, next to the field);
  it is the one small text that stays.
- New strings go into EN and the full zh-CN mirror; delete the locale keys of a line you remove.
- `web/src/modules/development/__tests__/noCaptions.test.mjs` fails on `variant="caption"`, `helperText=` and
  `FormHelperText` in the Development module and Configure (`web/src/components/compare`). An exception needs an entry
  in its allow-list with a comment saying why it is not a caption.
- **Every page remembers where the user was after a reload** (open project, tab, card, step): use `web/src/lib/viewMemory.ts`
  / `useRememberedView` (localStorage, every read/write in try/catch, validated, a vanished target falls back to the
  default view silently). Never store secrets or tokens there.
- **Specification parameters are changed only by the project owner and by global admins.** Project engineers, customers and
  global engineers who are not admins view the specification, test in Configure and write recommendations in the project
  thread (`can_edit_spec` in `src/motor_ai_sim/development/access.py`).
