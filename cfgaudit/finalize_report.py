r"""Fill the summary / addenda of the Downloads audit report (NOT committed)."""
p = r"C:\Users\vadim\Downloads\configure_audit_2026-09-30.md"
s = open(p, encoding="utf-8").read()
summ = r"""## Summary

**Where Configure stands.** The tuner is a fast, well-documented scaling engine
around one FEM operating point, and it gets torque right: within 1.2 % at every
tested knob corner on both machines. It is not yet a client product, for four
reasons:

1. **Passports are missing or stale.**
   - Only 14 catalog cards have a passport, and 13 of them have no PWM block.
   - The newer die/configuration machines (L155, L180, the 85 mm 20SW1200 / L13, CIANO 150_40) have none.
   - Nothing fingerprints a passport, so a passport can describe a superseded lamination without anyone noticing.
2. **Three silent loss bugs.**
   - The end-winding split is 0 on 7 of 14 passports, so copper is off by ±35 % when the stack length changes.
   - On delta machines the AC copper is stored 3× too low and then dropped to zero, which makes the L155 total loss −24 % at base.
   - The EMF is taken as the peak of the sampled waveform: −14 % on a coarse passport, +2 % on the stored Ø40 one.
3. **No γ axis.** There is no MTPA, no field weakening and no voltage-limit refusal. The speed charts are fixed at 0–8000 rpm, and the Ø40's rated point is already at the pack's v_min.
4. **The N·h³ proximity law breaks near the top of the slot.**
   - AC copper −70…−74 %, total loss −13…−34 % against the coupled FEM on the Ø40.
   - The two AC copper routes still disagree by 62 % on L155.

**Accuracy (law error vs FEM, today's tuner).**

| Quantity | Error |
|---|---|
| Torque | ≤ 1.2 % everywhere |
| EMF scaling | ≤ 0.1 % |
| Loaded line voltage | up to +16 % |
| Total loss, speed 0.5–1.5× and current 0.5–1.3× | ≤ 3 % on the Ø40 |
| Total loss, stack corners | ±30 % |
| Total loss, turns/wire near the slot top | −13…−34 % |
| Total loss, anywhere on L155 (delta bug) | ✘ |

**Proposed next steps.** Fix the three loss bugs, add passport signatures, and give die configurations their own passports. Then decide the computation baseline and pilot the ψ-map passport v1 on the Ø40. The specification is `docs/PASSPORT_ALGORITHM.md`, draft PR https://github.com/eMotres/Motor_Ai_Simulation/pull/80.

Compute used: 2.45 h on the server sandbox (8810 s over 7 containers, never more than one at a time). The sandbox was deleted afterwards."""
tail = r"""## 8. Owner addenda answered here and in the specification

- Passport grid: the (id, iq) static grid with an MTPA triple and a field-weakening arm, and the MTPA-only comparison row, are in spec §3.1–3.5.
- The loss grid of 5 speeds × 3 settled transients at the operating angle, with its validation plan: spec §4.
- Turns and wire thickness: the hybrid AC method with its failure modes, rotor-side independence of N at the same NI (FEM pair, section 5 above), and the validation matrix: spec §6.
- The workflow for users without FEM rights, with what can be reused (job queue, quotas, MCP scopes, Admin inbox, notify) and what is new (FEM-rights flag, request store, result cache, MCP tool): spec §10.
- Temperature: the hot grid, the mandatory cold 20 °C set, cold motor constants and the thermal loop: spec §8. Mechanical losses (the SKF bearing model and Couette + end-face windage already in `bearings.py` / `mech_losses.py`): spec §5.6.
- Computation baseline v1 with decided / needs-evidence status for each item: spec §3.0. This covers the mesher, the mesh rule, the settle rule (L155 614 frames → estimated 150–220), the one-window demag method, materials PR #51, reproducibility, and TDM as a later optimisation.

Files: the audit harness and raw results (not committed) are in
`C:\Users\vadim\Projects\motor_ai_sim_wt_cfgaudit\cfgaudit\`: `build_jobs.py`
(machines, corners, γ sweeps, AC matrix), `cfgaudit.py` (sandbox driver),
`tuner_eval.mjs` (runs the unmodified `motorScaling.ts`), `analyze.py`,
`mtpa.py`, `acm.py`, `res_*.jsonl` (every FEM result with its settings)."""
s = s.replace("<!-- SUMMARY_PLACEHOLDER -->", summ).replace("<!-- ACM_PLACEHOLDER2 -->", tail)
assert "PLACEHOLDER" not in s
open(p, "w", encoding="utf-8").write(s)
print("ok", len(s))
