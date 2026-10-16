# Work registry — parallel worktrees / PRs

Generated 2026-09-29 by an inventory pass (read-only except this file). Base
branch for all PRs below: `pre-migration-freeze-2026-09-15`. Repo:
`eMotres/Motor_Ai_Simulation`.

## PRs #32–#51 (open work still to land)

| # | Task | Branch | Base commit (HEAD) | PR | Checks | Integration status |
|---|------|--------|---------------------|----|--------|---------------------|
| 32 | DC-orbit Newton sees controller bridge DC damping (L180 delta DC refusal) | `fix/coupled-pwm-dc-settle` | de61359 | [#32](https://github.com/eMotres/Motor_Ai_Simulation/pull/32) OPEN | no checks configured | clean worktree, ready for review |
| 37 | Engineering portal architecture doc | `docs/portal-architecture` | 769fe39 | [#37](https://github.com/eMotres/Motor_Ai_Simulation/pull/37) OPEN | no checks configured | worktree diverged from origin (local +1/-3) — needs owner sync before merge |
| 39 | Project manifesto | `docs/manifesto` | 3d052ba | [#39](https://github.com/eMotres/Motor_Ai_Simulation/pull/39) OPEN | no checks configured | worktree 1 commit behind origin — pull before further edits |
| 40 | Pure AGPL-3.0-or-later + DCO; triangle/pypardiso optional | `chore/license-agpl` | adcb72a | [#40](https://github.com/eMotres/Motor_Ai_Simulation/pull/40) OPEN | `dco` check FAILING | no dedicated worktree; the local worktree `motor_ai_sim_wt_agpl` is on a differently-named local branch `chore/license-agpl-dco` (same commit, no remote) — DCO sign-off needed on the real PR branch |
| 41 | Split ANSYS cross-checks / NDA data into a private repo | `chore/private-split` | 20e2c58 | [#41](https://github.com/eMotres/Motor_Ai_Simulation/pull/41) OPEN | no checks configured | clean, pushed, ready for review |
| 42 | Purge Cyrillic from repo — batch 1/N | `chore/english-only` | be37320 | [#42](https://github.com/eMotres/Motor_Ai_Simulation/pull/42) OPEN | no checks configured | worktree has 74 uncommitted files, 58 with unresolved conflict markers — **do not merge, needs owner** |
| 43 | Translate remaining Cyrillic in docs/ and root Markdown | `chore/english-docs` | 9ad9d13 | [#43](https://github.com/eMotres/Motor_Ai_Simulation/pull/43) OPEN | no checks configured | clean, pushed, ready for review |
| 44 | User-owned compute nodes, Stage 1 | `feat/byo-compute` | fbcd438 | [#44](https://github.com/eMotres/Motor_Ai_Simulation/pull/44) OPEN | no checks configured | clean, pushed, ready for review |
| 45 | Remove Russian from web/src, tests, scripts, config, deploy | `chore/english-web-tests` | a68f16d | [#45](https://github.com/eMotres/Motor_Ai_Simulation/pull/45) OPEN | no checks configured | clean, pushed, ready for review |
| 46 | Purge Cyrillic from src/** (batch 2) | `chore/english-src` | b5733dc | [#46](https://github.com/eMotres/Motor_Ai_Simulation/pull/46) OPEN | no checks configured | clean, pushed, ready for review |
| 47 | Remove commercial features, collapse tiers to user/admin roles | `chore/remove-commerce` | 743f5ea | [#47](https://github.com/eMotres/Motor_Ai_Simulation/pull/47) OPEN | no checks configured | clean, pushed, ready for review |
| 48 | Open/private data sets + admin move-by-PR | `feat/open-private-data` | 1aaf7e1 | [#48](https://github.com/eMotres/Motor_Ai_Simulation/pull/48) OPEN | no checks configured | clean, pushed, ready for review |
| 49 | gmsh CDT backend for the geometry-driven mesher (S2, builds on #40) | `feat/gmsh-parity` | dd8b8f6 | [#49](https://github.com/eMotres/Motor_Ai_Simulation/pull/49) OPEN | `dco` check FAILING | clean, pushed; blocked on #40 landing first, DCO sign-off needed |
| 50 | Data protection fixes (audit 2026-09-29) | `fix/data-protection-now` | 0a24e64 | [#50](https://github.com/eMotres/Motor_Ai_Simulation/pull/50) OPEN | no checks configured | clean, pushed, ready for review |
| 51 | Materials: every value from an independent source, nothing from Ansys | `chore/materials-independent` | 24d4468 | [#51](https://github.com/eMotres/Motor_Ai_Simulation/pull/51) OPEN | no checks configured | worktree 1 local commit ahead of origin — push before merge |

No CI workflow is wired for most of this repo yet — only a `dco` (Developer Certificate of Origin sign-off) check runs, and it is currently failing on #40 and #49 (commits need `git commit -s` / `--signoff`).

## PRs #1–#31 (already merged / closed, for reference)

All merged into `pre-migration-freeze-2026-09-15` except #10, which was closed as superseded by #11.

| # | Title | Branch | State |
|---|---|---|---|
| 1 | test: pin two config-dependent tests to their own machine | `cloud/fix-tests-2026-09-26` | MERGED |
| 2 | Br corner diagnostic; converged sine-voltage settle (Task 2) | `feat/br-corner-and-sine-settle-convergence` | MERGED |
| 3 | Task 3: PWM settle solves the DC orbit | `task3-pwm-dc-solve-and-stop` | MERGED |
| 4 | Task 4: closed-form first PWM command + current-only 3rd pass | `task4-pwm-voltage-guess` | MERGED |
| 5 | Thermal: coupled loop's answer passes (Task 6) | `task6-thermal-coupled-passes` | MERGED |
| 6 | Controller: selectable SVPWM / third-harmonic injection | `feat/controller-svpwm` | MERGED |
| 7 | Task 9: device footprints + board-fit filter | `task9-device-footprints` | MERGED |
| 8 | Task 8: eddy runs default to 72 steps/period | `task8-eddy-72-steps` | MERGED |
| 9 | Task 7 — Robotics: single "Heat path" choice | `task7-robotics-heat-path` | MERGED |
| 10 | Eddy periodic-state accelerator (superseded) | `feat/eddy-periodic-accel` | CLOSED |
| 11 | Accelerator + cap 24 + ripple-grade steps (includes #10) | `feat/accel-cap24-ripple108` | MERGED |
| 12 | Rename honest->linear keys + Configure carrier follows Controller | `feat/ui-honest-linear-carrier` | MERGED |
| 13 | fix: S1 housing gap dimming the coupled Physics Dashboard | `fix/dim-dashboard-s1-housing` | MERGED |
| 14 | Six-coil study (Controller Stage 3) | `feat/six-coil-study` | MERGED |
| 15 | Controller: uniform SPICE loss basis | `controller-spice-uniform-2026-09-27` | MERGED |
| 16 | Follow-ups 2026-09-27 | `fix/followups-2026-09-27` | MERGED |
| 17 | fix(static3d): Stage A method | `fix/stage-a-method-2026-09-28` | MERGED |
| 18 | WCMS900B170E53 card + standalone controller runs | `feat/wcms900b170e53` | MERGED |
| 19 | LTspice backend + SPICE tables for 750 V CoolSiC G2 | `feat/ltspice-750v-tables` | MERGED |
| 20 | Controller: power direction + PWM ripple/THD | `feat/controller-ripple-generator` | MERGED |
| 21 | Six-phase winding | `feat/six-phase-winding` | MERGED |
| 22 | fix(coupled): pass stated R_th(j-c) to controller solve | `fix/coupled-rth-passthrough` | MERGED |
| 23 | fix(family): duty load never silently writes die geometry | `fix/duty-load-no-silent-die-write` | MERGED |
| 24 | fix(mesh): Max element size slider usable on Ø12 motors | `fix/mesh-slider-o12` | MERGED |
| 25 | Add SKF W 638/2-2Z bearing | `feat/bearing-w638-2-2z` | MERGED |
| 26 | feat(auth): e-mail + password auth | `feat/email-password-auth` | MERGED |
| 27 | MCP server Stage 1 | `feat/mcp-stage1` | MERGED |
| 28 | fix(eddy): warm-up stop rule for milliwatt bodies | `fix/eddy-settle-stop-rule` | MERGED |
| 29 | feat(mcp): Stage 2 OAuth 2.1 | `feat/mcp-oauth` | MERGED |
| 30 | feat(catalog): unified card envelope, stage 1 | `feat/catalog-stage1` | MERGED |
| 31 | feat(mcp): stage 3 — agent draft designs via job queue | `feat/mcp-stage3-design` | MERGED |
| 33 | fix(parts): shaft always participates | `fix/shaft-always-included` | MERGED |
| 34 | feat(admin): Servers panel — cluster load monitor | `feat/admin-cluster-monitor` | MERGED |
| 35 | Admin: sections redesign + per-die access | `feat/admin-redesign` | MERGED |
| 36 | feat(newsletter): GDPR double opt-in newsletter | `feat/newsletter` | MERGED |
| 38 | chore: ignore key/state stores, untrack logs, drop vendor PDF | `chore/ignore-secrets-untrack` | MERGED |

## Worktree classification summary (full inventory: `Downloads/worktrees_inventory_2026-09-29.md`)

Counts across the 61 registered worktrees (including the primary checkout):

- **SAFE_TO_REMOVE: 24** — branch merged (or closed/superseded) into `pre-migration-freeze-2026-09-15`, fully pushed, zero uncommitted files.
- **KEEP: 12** — open PR (#32/#41/#43–#51) or an active deploy-tracking branch (`deploy/2026-09-25`), clean working tree.
- **NEEDS_OWNER: 25** — uncommitted changes, unresolved conflict markers, unpushed/diverged commits, detached HEAD of unclear purpose, or a worktree checked out on a differently-named local branch than its PR's head (the two `-dco` duplicates for #39 and #40). Full list and reasons in the Downloads inventory file. Includes the primary `motor_ai_sim` checkout itself (181 uncommitted files from this session's own work — never removable, flagged for the owner to commit or discard).

No `git stash` entries were touched. One pre-existing stash was found:
`stash@{0}: WIP on feature/p2-elements: b563b71 fix(mesh): drop the Windings
and Shaft per-part size fields — measured against the live mesh` (left
untouched).
