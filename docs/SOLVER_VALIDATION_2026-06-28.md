# Solver validation vs ANSYS — 2026-06-28 (autonomous session)

Reference split (per Vadim): **ANSYS = KV/torque oracle** (validated on real motors); **losses = ours** (ANSYS StrandedLossAC is overstated). So we validate our *torque/KV* against ANSYS and trust *our* losses.

Motor under test: 40 mm OD, **12 slots / 14 poles**, 12 mm stack. Operating point from config: **I = 38 A rms, γ = −32°, 12000 rpm**.

---

## TL;DR

Your sweep showing "wide tooth (3.5) best" and the **jumping curves** were **two solver bugs**, not physics:

1. **The sweep built an invalid symmetry.** "Full" (n_sectors=1) was turned into a 90° wedge (NS=4) inside the solver — and a 90° wedge cannot hold a whole number of 14 poles (3.5/sector) → corrupt field → spurious tooth-width torque slope + scattered ripple. (The Simulation tab did *not* have this — it maps 1→−1=full ring. The sweep silently differed despite a comment claiming they matched.)
2. **The mesh was far too coarse for a small motor.** Mesh size is absolute (UI default 4 mm; sweep floor 1 mm) with **no scaling to motor size**. The 40 mm motor has 2.8 mm slots → ~1 element per slot → torque/back-EMF/ripple grossly under-resolved and **mesh-dependent**.

Both are now fixed (real fixes, no fudge factors). After the fixes the solver is **converged and mesh-independent**, and the torque/KV land where 2D physics says they should vs ANSYS.

---

## What the convergence study proved

Same design (tw 3.1, I=38, γ=−32), consistent build, only the mesh refined:

| mesh (mm) | T_avg (N·m) | KV (rpm/V) | ripple_filt |
|---|---|---|---|
| 1.5 | 0.520 | 484 | 175 % ← garbage |
| 1.0 | 0.571 | 583 | 10.5 % |
| 0.7 | 0.567 | 578 | 11.1 % |
| 0.5 | 0.564 | 588 | 10.7 % |

→ Results only **plateau at mesh ≲ slot_width/3**. At the default 4 mm (and even 1.5 mm) they are meaningless. **Converged truth: T ≈ 0.565 N·m, KV ≈ 585 rpm/V, ripple ≈ 11 %.**

Earlier I briefly saw a "24 % torque drop" refining 2.0→1.3 — that was *confounded* by the mesh-copy build flipping to a double-stitch at finer mesh. With a consistent build the convergence above is clean.

## The fixes (real element sizing + correct symmetry — no band-aids)

1. **`routes/optimization.py`** — sweep now maps `n_sectors ≤ 1 → −1` (true full ring), exactly like the Simulation transient route. No more invalid NS=4 wedge.
2. **`simulation/fem_solver_2d.py`** — `n_sectors == 1` now means the full ring everywhere (was falling through to NS=4).
3. **`simulation/fem_solver_2d.py`** — **mesh auto-refine**: the target is clamped to resolve the smallest in-plane feature (slot/tooth) with ≥4 elements: `mesh = min(user, min(slot_w, tooth_w)/4)`. Only ever *refines* (min) — large-feature motors (200 mm) keep their mesh. Single choke point (`fem_transient_sliding_band`) → covers Simulation **and** sweep/optimizer (both funnel through it).

**Validated mesh-independence:** same design at user mesh 4.0 vs 2.0 → **bit-identical** (T=0.57594, KV=590.1, ripple=10.66 % both). The user's mesh slider can no longer change the answer for a small motor.

## KV / torque vs ANSYS — the 2D short-stack offset

Our converged **KV ≈ 585 vs ANSYS 647** (~10 % apart, our back-EMF ~8 % *stronger* per rpm). This is the **expected 2D-vs-3D direction**: 2D ignores 3D end-region flux leakage, so it overstates flux linkage / back-EMF / torque. The effect is large here because the stack is only **12 mm** (end-region is a big fraction of a short motor). So:
- ANSYS KV/torque = the absolute reference (3D).
- Our 2D runs ~8–10 % "stronger" for this short stack — physics, not a bug. Our 2D is internally **converged**.

## Tooth-width — the real answer (converged mesh, our losses)

Converged mesh (auto-refine ≈0.7 mm), full ring, full losses, I=38, γ=−32, 144 steps:

| tw (mm) | T (N·m) | η % | Pcu (W) | Pfe (W) | Pmag (W) | ripple % | KV |
|---|---|---|---|---|---|---|---|
| 3.1 | 0.5736 | 89.57 | 64.4 | 17.5 | 1.96 | 14.4 | 588 |
| 3.2 | 0.5769 | 89.72 | 63.4 | 17.7 | 1.94 | 12.9 | 575 |
| 3.3 | 0.5841 | 89.82 | 63.3 | 17.9 | 2.05 | 10.1 | 576 |
| 3.4 | 0.5894 | 89.98 | 62.6 | 17.9 | 2.06 | 10.9 | 576 |
| 3.5 | 0.5898 | 90.02 | 62.5 | 17.6 | 2.09 | 10.8 | 589 |

**Ripple is now smooth/monotonic (14→10 %), no more random jumping** — the fixes removed the scatter.

**3.1 → 3.5: torque +2.8 %, efficiency +0.45 pp.** Decomposition of the +0.45 pp:
- ~+0.27 pp from the torque rise (Pmech 721→741 W), and
- ~+0.18 pp from copper loss falling (Pcu 64.4→62.5 W; iron + magnet ~flat).

The copper-loss fall is the **AC proximity** term (DC I²R is fixed — copper area is wire-defined and constant). Our proximity model is **radial-field-dominated** for this flat 2.5×0.5 mm wire, so a narrower slot opening (wider tooth) → less radial penetration → less AC loss → favours wide. **ANSYS's StrandedLossAC does the opposite (rises with wider tooth).** That sign difference is the one genuine physics fork, and it depends on the exact conductor geometry/orientation — unresolved, but small.

### Verdict on tooth width
- **It is a shallow, near-neutral optimum.** Our converged result favours **wide (3.5): η +0.45 pp, T +2.8 %**. This happens to match the *direction* of your old sweep — but the old sweep was an NS=4+coarse artifact; this one is trustworthy.
- ANSYS torque is **flat** (±0.4 %, peak 3.3–3.4); ours rises +2.8 %. Ours over-favours wide vs ANSYS — partly residual build noise (KV wobbles ±1–2 % = the un-welded seam) and possibly 2D over-stating slot-opening leakage at this short stack. So treat our +2.8 % as "+1–3 %, peak in the 3.3–3.5 range".
- ANSYS efficiency (narrow best) is built **entirely on its StrandedLossAC** (136–161 W, ~14× iron) — the loss you distrust. By your own rule (trust our losses), wide-ish wins; but only by ~0.2–0.5 pp.
- **Recommendation: don't optimise on tooth width — it moves η by <0.5 pp. Pick ~3.3–3.4 (both tools' torque peak, good saturation/mechanical margin).**

## Still open (characterized, deliberately NOT band-aided)

1. **Raw-torque seam ripple (orders 1–4).** The full ring is a *stitched* build (`_stitch_full_half`: two 180° halves mirrored + welded); the 180° half's two radial cuts get different OCC segment counts ("5 vs 4 curves", slot-copy "11 vs 11, 3 aligned") so the seam doesn't fully weld → low-order parasitic ripple in the **raw** torque (raw 25–30 %; the 6·k band-filter removes it, so it does **not** affect mean torque, KV, or filtered ripple). Real fix = node-match the cut edges or mesh the annulus directly; it is real mesh-generator surgery that touches every motor, so I left it for your review rather than risk a blind change.
2. **Filtered ripple ≈ 11 % vs ANSYS ≈ 4 %.** Partly an operating-point artifact (you compare our **low-current** point, I=38, to ANSYS's **high-current** I=650/700 — cogging is a bigger fraction of a smaller mean torque at low current), partly a slip-band 6·k residual. Re-compare at matched current before judging.

## Files changed
- `src/motor_ai_sim/routes/optimization.py` (n_sectors ≤1 → full ring)
- `src/motor_ai_sim/simulation/fem_solver_2d.py` (n_sectors=1 → full ring; mesh auto-refine to feature/4)

Both compile; not committed (left on the working tree for your review). Backend restart needed to serve them.

## ⚠️ The 100 / 200 mm motors were under-resolved too — re-validate them
The bigger motors have **slot_width ≈ 5.5 mm**. At the default 4 mm mesh that is only **1.4 elements per slot** — the *same* garbage regime (mesh > slot/2.3) the 40 mm was in. So their previously "validated"/optimized numbers were likely off as well. After the auto-refine they will mesh at slot/4 ≈ **1.4 mm** (≈4 elements/slot) → **more accurate but markedly slower** (~8× elements ⇒ a big-motor transient/sweep is much heavier).

Action items for you:
- **Re-run the 100 / 200 mm motors** — their torque/η/ripple will shift toward correct.
- If big-motor sweeps become too slow, the refinement strength is **one line** in `fem_solver_2d.py` (`_feat_mm / 4.0`): raise the divisor toward 3 (gentler, ~5× elements, ~1 % less accurate) or add an explicit "draft/coarse" toggle. The convergence cliff for these motors is mesh ≈ slot/2.3, so slot/3 is the safe minimum.
- The factor 4 (≥4 elements across the smallest feature) is the standard accuracy choice and what every number in this report uses.
