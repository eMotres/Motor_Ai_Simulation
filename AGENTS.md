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
