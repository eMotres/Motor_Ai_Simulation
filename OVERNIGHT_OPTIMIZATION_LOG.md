# Overnight optimization campaign — 200 mm / 20p24s motor

Autonomous run requested 2026-06-16 ~23:50. Goal: keep optimizing until morning,
pick the best variant, launch new rounds as needed.

## Operating point (fixed)
- Target torque **77.7 N·m** @ **4000 rpm**, load angle **γ = 28°** (current auto-solved per design).
- Inverter limit: V_peak ≤ **277 V** (480 V bus, SVPWM).
- FEM: full disk, steps/period 60, pole-copy on.

## "Best" criterion (how I pick)
> **maximise  efficiency × (torque / mass)**  among designs with
> **ripple ≤ 7.0 %**, **V_peak ≤ 277 V**, **|torque − 77.7| ≤ 3 N·m**.

Honours the low-ripple priority (the reason we anchored on min-ripple) while
pushing efficiency and torque-density. Recomputed from `config/.opt_dataset.jsonl`
(every FEM eval ever) each round, so it is the true global feasible best.

## Reference points
| design | eff % | Nm/kg | ripple % | mass kg | note |
|---|---|---|---|---|---|
| original baseline | 95.70 | 7.72 | 11.6 | 10.19 | starting design |
| ripple-free winner (round 0) | 95.09 | 8.24 | 13.4 | 9.80 | max eff×td, ripple too high |
| **min-ripple pick** | 95.18 | 7.71 | 5.8 | 10.04 | low-ripple anchor → new center |

## Rounds
- **R1 (done, ~00:15)** — from min-ripple, ripple ≤ 7 %, equal-weight eff×td, auto_expand off.
  Drifted the WRONG way: equal weights traded efficiency for torque-density →
  best reached eff **93.18 %** / 8.28 Nm·kg⁻¹ / ripple 6.5 %. A 93 % motor is a poor
  outcome (baseline 95.7 %). **Decision: protect efficiency.** Stopped early (restart).
- **R2 (~00:18)** — from min-ripple, **w_eff = 2.5**, w_td = 1, ripple ≤ 7 %,
  auto_expand on (box-walking, ≤6 rounds). Gen 0: eff 94.7 % / 7.87 Nm·kg⁻¹ / ripple 6.9 %
  → finding the eff↔td knee (more compactness costs ~0.5 pp eff).
  **Stalled ~00:46** — 35 python procs (orphaned FEM workers from the night's restarts)
  thrashing the CPU → evals crawled, auto-solve stuck. **Cleaned ~00:55**: killed all
  python → relaunched ONE backend (13 procs = uvicorn + normal pool). R2 **restarted**
  on the clean backend, dataset baseline line 1233.
  NB ripple is noisy ±1.5 pp @ steps=60 → final winner must be re-verified @ higher steps.
- **R2 CONVERGED ~02:18** — best stable at eff **94.74 %** / **7.90 Nm·kg⁻¹** / ripple **5.71 %**
  across 2 box-walk rounds (~1.2 h, w_eff=2.5). The eff↔td frontier is shallow and
  fully mapped (≈0.5 pp eff buys ≈2 % torque-density). Stopped box-walking — further
  rounds only re-confirm.
- **Verification ~02:25** — re-evaluating the top finalists @ **steps=120** (full disk,
  2× resolution) for trustworthy ripple → `config/verify_steps120.json`. Candidates:
  balanced (max eff×td), eff-priority (eff≥95), high-eff (round-0 all-round), max-td.
  Then I apply the recommended design + save the operating point.

## Best criterion (REFINED after R1)
> max **torque / mass** among designs with **eff ≥ 95 %**, ripple ≤ 7 %, V_peak ≤ 277 V,
> |T − 77.7| ≤ 3.  (R1 proved raw eff×td under-values the high baseline efficiency —
> a good motor must keep ~95 %+ efficiency.)

## ⚠️ steps=60 was BIASED (found by steps=120 verification, ~02:30)
Re-evaluating finalists @ **steps=120** (full disk, 2× res): steps=60 **over-estimates
efficiency ~1 pp** and **under-estimates ripple ~1–3 pp** (slip-ring/step undersampling —
the known issue). So R0–R2's steps=60 numbers were optimistic and the ripple ≤ 7 %
"gate" was unreachable in reality.

**Trustworthy (steps=120) frontier:**
| design | eff % | Nm/kg | ripple % | mass kg |
|---|---|---|---|---|
| **high-eff (round-0)** | **95.02** | 7.72 | 9.1 | 10.03 |
| eff-priority (anchor) | 94.29 | 7.78 | 7.8 | 10.04 |
| balanced / max-td | 92.28 | 8.31 | 9.5 | 9.60 |

## R3 (running, ~02:32) — re-optimise at TRUSTWORTHY steps=120
From the **high-eff** design, w_eff=2.5, ripple ≤ 9 %, auto_expand, target 77.7.
Finds the true optimum at proper resolution (R1/R2 chased steps=60 artifacts).
Dataset baseline line 1368.

- **R3 CONVERGED ~03:30** — at steps=120 the optimum settles at eff 94.34 % / 7.87 Nm·kg⁻¹
  / ripple 8.02 % (same shallow eff↔td/ripple knee, now at honest resolution).
  Stopped — R1/R2/R3 all confirm the frontier; more rounds add nothing.

# ✅ FINAL RESULT (campaign complete ~03:40)

Two non-dominated finalists at **trustworthy steps=120** (geom in `config/FINAL_candidates.json`):

| design | eff % | Nm/kg | ripple % | mass kg | I (≈77.7 N·m) | V_peak |
|---|---|---|---|---|---|---|
| original baseline | ~94.7* | 7.72 | ~13* | 10.19 | ~88 | 225 |
| **① HIGH-EFF (applied to config now)** | **95.02** | 7.72 | 9.1 | 10.03 | 97 | 213 |
| ② R3-optimum (low-ripple / compact) | 94.34 | **7.87** | **8.0** | **9.87** | 93 | 244 |
\* baseline @ steps=120 estimated (steps=60 gave 95.70/11.6, biased ~+1 pp eff / −2 pp ripple).

**Both beat the baseline** (much lower ripple at similar/better eff). They trade off:
- ① **HIGH-EFF** — best efficiency (95.02 %), ripple 9.1 %. *Recommended default* (efficiency
  is the headline spec; ripple already far below baseline). **Currently applied to config.**
- ② **R3-optimum** — −0.68 pp eff buys −1.1 pp ripple + 1.6 % lighter + more compact. Pick
  this if low ripple / compactness matter more. To apply: `PUT /api/geometry` with its geom
  from `config/FINAL_candidates.json`.

**Operating point:** γ = 28°, 4000 rpm, current auto-solves to 77.7 N·m (~97 A for ①).

**Why I stopped (didn't burn until 07:00):** the eff↔(ripple,td) frontier is genuinely shallow
and fully mapped at both resolutions — R1/R2/R3 all land on it. The 200/20p24s spec + fixed γ
leave no untapped lever, so more FEM hours would only re-confirm. Responsible to stop.

**Key lesson:** steps=60 (the run resolution) is **optimistic** — re-verify finalists at
steps≥120. The 3D objective view is now in the app (Optimize → 2D/3D toggle).

_State left for you: config = design ①; backend clean; full dataset in config/.opt_dataset.jsonl._

# Follow-up — push ripple LOWER (R4, ~03:45)

Correction to "no untapped lever": the **cogging-shaping knobs were barely used**. R0–R3
optimized structural/winding params (eff×td drivers); these were fixed or never in a run:
`magnet_fill_radius`, `magnet_up_gap`, `rotor_fill_r`, `cut_width`, `tooth2_width` (optimizable,
never run) and `magnet_fill_down`, `slot_hs` (fixed). They shape the **air-gap flux / cogging
harmonics** with little eff/torque cost (current auto-solves torque).

- **R4 (running)** — MINIMISE ripple: 9 cogging-shape variables (magnet_fill_up/radius,
  magnet_down_height, rotor_fill_r, stator_fillet_r/r1, cut_width, tooth_width/2), ripple
  gate **4 %**, penalty λ=2, w_eff=1.5, **steps=120** (trustworthy — steps=60 under-reports
  ripple so optimizing there is meaningless), from design ①. Goal: ripple below 8 %. Dataset
  baseline line 1435. **Plateaued at ripple 8.84 %** (only −0.26 pp for −0.9 pp eff — these
  knobs lack cogging authority). Stopped.
- **R5 (running, morning)** — same goal, **strong levers added**: `magnet_fill_down`
  (bottom pole arc) + `slot_hs` (slot-opening width) — the classic anti-cogging pair, plus
  magnet_fill_up/radius, fillets, cut_width, rotor_fill_r, magnet_down_height, tooth_width.
  Minimise ripple (gate 4, λ=2, w_eff=1.5), steps=120, from design ①. Dataset baseline 1464.

### Table feature (Sweep study)
Added an Excel-like sortable table under the sweep chart: leading columns = swept variables,
then T / P / η / V_peak / ripple / mass / Nm·kg⁻¹ / kW·kg⁻¹ / P_loss / core / stranded / solid.
Click a header to sort; click a row to pick+apply+save it; row ↔ chart-point highlight both
ways. Removed the chart legend + the zoom caption.

### Applied for manual tuning (morning)
R4/R5 (dedicated ripple runs) both plateaued (8.84 % / 9.02 %) — confirming **~8 % is the
geometry floor** at 77.7 N·m on 20p24s (load ripple ∝ MMF×permeance harmonics of the fixed
slot/pole set; the real <5 % lever is rotor SKEW, not modelable in 2D FEM). Per Vadim's
request, **applied the minimum-ripple design to config** for hand-tuning:
`slot 19.8 / core 6.8 / tooth 11.4 / wires 12 / mag_h 21.4 / fill_up 0.467 / fillet 4.6,1.8`
→ steps=120: **eff 94.29 % · 7.78 Nm/kg · ripple 7.78 % · T 78.1** · op-point 107 A (pk), γ 28°, 4000 rpm.
NB always re-check ripple at steps ≥ 120 when tuning (steps=60 under-reports ~1.5–3 pp).
