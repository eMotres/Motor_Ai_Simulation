# Br corner diagnostic and converged sine-voltage settle (2026-09-26)

Branch `feat/br-corner-and-sine-settle-convergence` from `pre-migration-freeze-2026-09-15`.
Closes open items 3 and 4 of `NO_FILTERS_2026-09-24.md`.

## 1. The worst demag element is a flagged corner diagnostic

`NO_FILTERS` §3: the worst single element never converges with the magnet mesh. It stays at 11.7 %
on Ø40/L13 at every mesh size, and goes 98.0 → 93.3 → 78.6 % on L155. It is the field singularity at a
sharp magnet corner, the demag version of the mechanical module's unaveraged peak stress.

- **Solver.** `demag_summary.br_worst_pct` has been removed. `demag_summary.br_corner` holds `{flag:
  "corner", br_pct, element, magnet_tag, x_mm, y_mm, r_mm, theta_deg, element_area_pct, note}`. The
  coordinates are the rotor mesh's own (rotor at 0°, the modelled sector). `br_kept_vol_pct` is the
  magnet's figure.
- **Report.** The red/amber `demag_worst_element` rule (80 / 90 %, B6) is gone. It is replaced by an
  info-level `demag_corner` row that has its location and no limit. The following all name the value
  as a corner diagnostic that is not the magnet's figure: the demag paragraph, the map captions (single
  map and pair), the PWM and duty-comparison tables (value printed as `"x (corner)"`), and the limits
  table ("none — a flag"). A stored result that only has the legacy `br_worst_pct` is read as the same
  diagnostic without a location (`br_corner_of`). A map-recomputed block (`demag_from_field`) takes its
  location from the stored map.
- **Datasheet.** The row is labelled "⚑ Br corner diagnostic" and shows the per-duty locations.
- **Web summary.** A separate "⚑ Br corner" cell with a one-line tooltip. The worst element is no longer
  in the Demag koef tooltip.

Real output (pinned `p2_demag`, 30 mm): Br kept 97.102 % (unchanged). The corner is 12.3 % at r 8.83
mm, 163.4°, magnet 106, on an element that covers 0.0025 % of the magnet area. Every pinned number is
unchanged to round-off.

## 2. Sine-voltage settle: a convergence criterion replaces the fixed 4 periods

This applies to the plain sinusoidal voltage drive (no eddy, no demag, no explicit
`SB_V_SETTLE_PERIODS`). The settle schedules `SB_V_SETTLE_CAP` periods (default 40). At the end of each
settling period it measures three quantities over that period:

- the phase-current amplitude (√2 × the Δt-weighted phase rms),
- the torque mean,
- the torque ripple (`torque_metrics`, the function the reported ripple uses).

It compares them with the previous period. It stops when all three move by less than
`SB_V_SETTLE_TOL` (default 1e-3, relative). Both settings are validated and raise loudly.

- A pair of periods counts only when both periods ran after the last Aitken anchor.
- The settle never stops on a boundary where an anchor fired.
- On a stop, the schedule is rebuilt for the periods actually marched. It is a prefix of the capped
  schedule, checked frame by frame, so the run is exactly the fixed-count run with that count.

The result carries `voltage_settle` with these fields: `converged`, `periods_used`, `cap_periods`,
`tol_rel`, `stop_residual`, one history row per period (the three quantities, whether an anchor fired
after it, and its residual), and `reported_vs_last_settle`. The last one is what the reported period
moved relative to the last settling period, measured the same way.

### Before / after

"before" = base code (fixed 4). "after" = this branch. "ref" = `SB_V_SETTLE_PERIODS=40`.

**30 mm pinned `p2_voltage`** (7 V, +10°, 15 000 rpm, 12 steps). After: converged at 5 periods, stop
residual 2.6e-4 (ripple), no anchor fired, 72 frames against 492 for the reference.

| quantity | before | after | ref | before vs ref | after vs ref |
|---|---:|---:|---:|---:|---:|
| T_avg [N·m] | 0.2437813 | 0.2437808 | 0.2437808 | +0.0002 % | 0.0000 % |
| T_ripple [%] | 1.50492 | 1.50501 | 1.50500 | −0.005 % | +0.0003 % |
| I_A fund [A] | 72.78623 | 72.78662 | 72.78663 | −0.0005 % | 0.0000 % |
| P_cu [W] | 81.48028 | 81.47996 | 81.47996 | +0.0004 % | 0.0000 % |
| V_peak [V] | 8.562575 | 8.562577 | 8.562577 | 0.0000 % | 0.0000 % |

**Ø40 L12 (CIANO14 40_12 die geometry), 10 V, +10°, 13 000 rpm, 12 steps.**

| quantity | before (4) | after | ref (40) | before vs ref |
|---|---:|---:|---:|---:|
| T_avg [N·m] | 0.36972 | 0.36669 | 0.36669 | +0.83 % |
| T_ripple [%] | 11.034 | 2.329 | 2.329 | +374 % |
| I_A fund [A] | 50.551 | 50.212 | 50.212 | +0.67 % |
| I_A THD [%] | 4.399 | 3.655 | 3.655 | +20 % |
| P_cu [W] | 40.859 | 39.390 | 39.390 | +3.7 % |
| P_fe [W] | 8.9007 | 8.8697 | 8.8697 | +0.35 % |
| V_peak [V] | 12.390 | 12.242 | 12.242 | +1.2 % |
| DC residual [A] | −2.677 | −0.003 | −0.003 | |
| frames solved | 60 | 492 | 492 | |

The after run is identical to the reference to round-off. It hit the cap: `converged: false`, stop
residual 0.17 (ripple). Both of the following are measured in its own `voltage_settle.history`:

1. **The Δ² Aitken anchor is destructive on this machine.** It fired at periods 3, 6, 9, 12 and 15. Each
   time, the next period jumped to 64 A with 76 % ripple, against 50 A and about 2 % on the orbit. The
   fixed-4 default reported exactly that disturbed period (−2.68 A DC, 11 % ripple). The converged
   settle cannot stop across an anchor, so it rode through all five. `NO_FILTERS` §5 found the anchor
   inert on the doc's Ø40 point (it fired 0 times), so this is operating-point dependent. The fixed
   10-period sine settle kept for eddy/demag runs is exposed to the same thing: anchors at 3, 6 and 9,
   with the reported period right after the last one. The anchor is not changed here. This is a
   separate fix.
2. **From period 26 on, the per-period values repeat exactly every 7 electrical periods.** Ripple cycles
   through 1.30, 1.56, 2.26, 2.34, 1.61, 1.95, 2.34 %, while I and T stay within 1e-4. At 12 steps per
   period on 12s/14p, the slip-snapped rotor positions only repeat every p/gcd(Q, p) = 7 electrical
   periods. A single period's ripple is therefore a sampling-phase quantity, and the log already warns
   that it is aliased at 12 steps. The circuit is settled, but no single-period value is stationary.
   The criterion reports this honestly as capped. Comparing each period with the one 7 periods earlier
   would declare convergence at about period 29. That is left to the owner, because it changes what
   "period-to-period" means.

The 1 % on the doc's own Ø40 sine point (7.126 → 7.202 % at 40) could not be re-run as-is. Its harness
lived in a scratchpad and did not record the applied voltage. The Ø40 row above uses the same geometry
at its rated speed.

## Tests

- `tests/test_voltage_settle_default.py`: the selection, the criterion's defaults and loud validation,
  the per-period metrics, the residual (including a no-load torque), and source guards for the stop /
  anchor guard / prefix splice / result keys.
- `tests/test_br_corner.py`: value, location, magnet, flag.
- `tests/test_report.py`: the B6 tests are rewritten for the info-level corner row. Text, captions,
  tables and the map-recomputed block are covered.
- `tests/test_solver_guards.py`: `_cs_hist` is registered as a per-period (not per-frame) list.
