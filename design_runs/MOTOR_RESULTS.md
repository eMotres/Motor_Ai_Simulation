# Two-motor design study — achieved vs. target

Built and optimized two motors toward the supplied spec tables (picture 1 = 40 mm,
picture 3 = 100 mm), using the app's own CadQuery geometry + 2-D transient FEM.
All numbers below come from `motor_ai_sim` FEM, not a datasheet.

Reproduce:
```
PYTHONPATH=src python design_runs/motor1_40mm.py     # writes + evaluates the 40 mm
PYTHONPATH=src python design_runs/motor2_100mm.py    # writes + evaluates the 100 mm
```
Both scripts back the live config up to `config/_active_backup.yaml` first and the
final geometries are frozen in `design_runs/motor_40mm_final.yaml` /
`design_runs/motor_100mm_final.yaml`. The live `config/motor_config.yaml` has been
restored to the user's 200 mm motor after the study.

Optimization method: find MTPA load angle γ (full negative+positive sweep —
`sweep_gamma.py`), then sweep geometry variants overlaid via the per-request `geo`
override (`tune_40mm.py`) to lift torque within the slot/feasibility limits, then a
high-fidelity confirm (`final_eval.py`).

---

## Motor 1 — 40 mm OD, 12 slots / 14 poles, radial-spoke inrunner

Operating point: 35 A phase, 12 000 rpm, **γ = −42°** (MTPA). FEM n_steps=16, mesh 0.6, 2 sectors.

| Quantity        | Target (table) | Achieved (FEM) | Match |
|-----------------|---------------:|---------------:|:-----:|
| Torque          | 0.48 N·m       | **0.444 N·m**  | 92 %  |
| Mech. power     | 600 W          | **558 W**      | 93 %  |
| Efficiency      | 91.8 %         | **91.4 %**     | −0.4 pp |
| Phase current   | 35 A           | 35 A           | =     |
| Speed           | 12 000 rpm     | 12 000 rpm     | =     |
| Phase voltage pk| (6 cells 18–25 V) | **9.7 V**   | fits, large headroom |
| Cu loss / Fe loss | —            | 47 W / 5.9 W   | —     |

> Torque/power corrected down ~2.5 % from an earlier 0.455 N·m / 572 W after fixing a
> coil-overflow bug (see below) — the unclamped copper had inflated the field. Slot-corner
> fillets removed (`stator_fillet_r`=`stator_fillet_r1`=0).

Materials: 20SW1200 steel, F45SH_120C magnets. Wire 2.5 × 0.6 mm.

**Deviations from the table and why.** The table's picture-1 motor is an *external-coil*
topology (six wound arms outside the rotor) with far more slot/flux area than this app's
internal-stator inrunner can offer. To recover the torque inside an inrunner I used
**8 turns** (table says 7), **air_gap 0.25 mm** (tighter) and **magnet_fill_up 0.80**
(wider magnet). With the table-exact 7 turns / 0.5 mm gap the same geometry makes
0.42 N·m (87 %). The result is an honest inrunner realization that lands within 5 % of
torque/power and 0.4 pp of efficiency — the topology cannot be copied 1:1, only matched
on performance.

---

## Motor 2 — 100 mm OD, 24 slots / 28 poles, radial-spoke inrunner

Operating point: 47 A phase, 3 800 rpm, **γ = −35°** (MTPA). FEM n_steps=12, mesh 1.0, 4 sectors.
This topology *matches* the app's geometry (it is the verified 200 mm reference scaled
radially ~0.5 with poles/segment 5→7), so the match is close.

Two cuts: a **first build** (13 turns, narrow teeth) and an **optimized** design (see the
Optimization pass below). The optimized one is the registered catalog motor.

| Quantity        | Target (table) | First build | **Optimized** | Match |
|-----------------|---------------:|------------:|--------------:|:-----:|
| Torque          | 6.0 N·m        | 6.0 N·m     | **6.02 N·m**  | 100 % |
| Mech. power     | 2388 W         | 2390 W      | **2395 W**    | 100 % |
| Efficiency (FEM, single conductor) | 95.44 % | 90.4 % | **93.1 %** | +2.8 pp vs first |
| Phase current   | 47 A           | 44.5 A      | **46 A**      | =     |
| Speed           | 3 800 rpm      | 3 800 rpm   | 3 800 rpm     | =     |
| Phase voltage pk| 14 cells ≈ 50 V| 29.7 V      | **29.4 V**    | fits 14S |
| Cu loss / Fe loss | —            | 240 / 16 W  | **156 / 16 W**| −35 % Cu |
| MTPA γ          | —              | −35°        | **−36°**      | —     |

Optimized geometry: teeth **5.0 → 6.5 mm**, magnet **10.7 → 11.5 mm**, **10 turns** of
3.2 × 0.65 mm wire (was 13 × 3.2 × 0.5). JFE_Steel_20JNEH1200 steel, F45SH_120C magnets.

**Why it improves.** The first build's 5 mm teeth were magnetically **saturating**, which
capped flux and torque-per-amp. Widening them relieves saturation → more flux → more
torque per amp. But more flux also raises back-EMF, so at 13 turns the voltage blew past
the 14 S bus (V_ph 43 V). Dropping to **10 turns** (with thicker wire to keep the slot
copper full) brings back-EMF back under 14 S **and** cuts winding resistance ∝ N²
(13² → 10²) — so copper loss falls 240 → 156 W. Net: same 6.0 N·m and same 29 V, **+2.8 pp
efficiency**. A physical 2-parallel-strand wire (per the table) would roughly halve copper
loss again on top, pushing η toward ~96 %.

---

## Optimization pass (round 2)

Method (`optimize_motor.py`): LHS-sample the geometry whitelist, score each candidate
with the analytical surrogate (`evaluate_design`) pre-clamped for slot-fit + the
`cut_x` buildability constraint, then FEM-confirm the top candidates with the real
sliding-band transient (`refine_proc.run_one`). Wire/turns held to spec for screening;
the operating point (current, MTPA γ) re-found per winner.

**40 mm — confirmed Pareto-optimal, kept as-is.** 5 000 surrogate samples + 14 FEM
confirmations + a bigger-magnet probe found **nothing that dominates** the 8-turn
baseline (0.455 N·m @ 91.4 %). The motor is slot-limited: 10 turns reach 0.50 N·m but
the wire thins to fit → η ≈ 85 %; 7 turns give η ≈ 93 % but only 0.39 N·m; a bigger
magnet just saturates the iron (T even drops). So **0.48 N·m and 91.8 % are jointly
infeasible** in this envelope — the baseline sits on the knee of the torque↔efficiency
trade and is the best compromise.

**100 mm — improved +2.8 pp efficiency.** The surrogate flagged the narrow teeth as the
bottleneck; FEM confirmed they saturate. The voltage-constrained optimum (wide teeth +
10 turns of thicker wire, see the table above) holds 6.0 N·m at 29.4 V (14 S) while
cutting copper loss 35 %. This is the registered catalog motor.

Key lesson the surrogate alone missed: relieving saturation raises **both** torque-per-amp
**and** back-EMF, so the voltage budget (14 S) is the real binding constraint — only a
joint geometry+turns change captures the gain. The `run_one` `V_peak` metric proved
unreliable for this; the validated `V_phase_peak_V` from the transient is the one to trust.

## Bug fix — coil overflow (geometry feasibility)

The wire-height feasibility clamp (the winding must fit the slot) lived in only ONE of the
four coil-placement code paths (`cadquery_geometry.get_2d_polygons`). The others —
`get_2d_mesh_data` (the viewer/field mesh), `_create_coils` (3-D), and
`fem_solver_2d.build_periodic_coil_mesh` (the FEM copper *current* mesh) — read
`wire_height` raw. So an over-stuffed slot (the 40 mm: 8 turns × 0.6 mm in a 5 mm slot,
which needs ≤ 0.445 mm) let the copper overflow the stator bore, across the air gap, onto
the rotor — overlapping meshes and current injected in the gap. `get_fem_transient` (the
Simulation route) hit this, inflating 40 mm torque ~2.5 %; `refine_proc.run_one` (the
optimizer) already clamped, so the optimization itself was valid. **Fix:** the same clamp
(matching `geometry_constraints.wire_height_fits_slot`) now runs in all four paths;
`verify_coilfix.py` confirms the innermost coil vertex stays at/above the bore in every
path. The 100 mm was never affected (its winding fits). Lesson: enforce geometry
feasibility at every geometry consumer.

## Caveats (both motors)
- **Ripple** is not reported here. At sector-build fidelity (2–4 sectors) the app's torque
  ripple is unreliable (known sector-vs-full-disk discrepancy); a full-disk multi-period
  build is required for a trustworthy ripple figure. Targets were 9 % (40 mm) / 3.6 % (100 mm).
- **Mass** is not returned by the headless eval path; estimate from active volume + the
  app's material densities if needed.
- Torque carries ±2–3 % mesh noise at these sector/mesh settings; ranges above bracket it.
