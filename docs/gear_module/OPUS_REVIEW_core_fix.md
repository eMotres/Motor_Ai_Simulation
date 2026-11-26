# Independent review of the round-3 gear core fix

Reviewer: Claude Opus 5.5 (claude-opus-5-5), local sub-agent, read-only, 2026-11-26. No source or test edited.
Suite: `PYTHONPATH=src python -m pytest tests/test_gears_*.py -q -p no:cacheprovider` -> **80 passed in 15.54 s**.
Scratch scripts (own derivations): `chk1.py` .. `chk8.py` in the session scratchpad (not in the repo).

## VERDICT: ACCEPT WITH FOLLOW-UP (one blocker, a small fix)

The physics changes in the fix hold up when I derive them myself: planetary local equilibrium with planet-bearing
friction, Z_L 2019, Y_DT and the use of the solved shift in YF/YS. One unsafe green pass remains:
`preset_evaluation.all_passed` ignores part strength. Fix it before stage 2 (routes and UI) relies on the flag.

## BLOCKERS

**B1. A failing shaft or part can still give `preset_evaluation.all_passed = True`.** `gearbox.py:2265-2282` calls
`evaluate_against_preset` without `parts_summary` (built at `:2206`), and `presets.py:294` sets
`all_passed = all(item.passed)` over gear/bearing/backlash items only. Example (`chk5.py`): pair 20/40, m 1, b 12,
four 6000-size deep-groove bearings, spans 20 mm, `shaft1_section` d = 2 mm, spectrum (6 Nm, 100 rpm, 2000 h) +
(3 Nm, 200 rpm, 3000 h), static allowables 2000/3000 MPa at 70 C. Result: shaft DIN 743 record `state=failed,
passed=False, SF=0.218`; `parts_summary.state=failed`; gearbox `state=failed`; but **`all_passed=True`** (S_F 3.46,
S_H 1.13, static 7.29/2.31, gear life 565 276 h, bearing life 2.30e6 h, S0 12.1). The same holds for carrier, pin,
rim and spline records. It is the only pass boolean on `GearboxEvaluationResult`. Fix: add a `parts` check (passed =
every part passed, state = `parts_summary.state`) to the preset evaluation, or AND `all_passed` with
`state not in (failed, unsupported)`. Add a regression test with the numbers above.

## Verified, with my own arithmetic

1. **Planetary local equilibrium** (`planetary_equilibrium.py:12-100`). In the carrier frame, vs = ws - wc,
   vr = wr - wc = -vs/u, B = N*M*|wp - wc|. Forward (Ts*vs > 0): -Tr*vr = eta_pr*(eta_sp*Ts*vs - B), so
   Tr = u*eta_sp*eta_pr*Ts + eta_pr*B/vr. Reverse: -Ts*vs = eta_sp*(eta_pr*Tr*vr - B), so
   Tr = u*Ts/(eta_sp*eta_pr) + B/(eta_pr*vr). Tc = -Ts - Tr (friction is internal between planet and carrier).
   This matches the code's A and C terms. The known-carrier branch, ts = -(Tc + C)/(1 + A), is also correct.
   - 40/20/80, N = 3, sun 1 Nm, 1000 rpm, needle planet bearing, mineral VG220. I fed the code's eta_sp/eta_pr/B into my
     own solver and got identical torques to 1e-9 in all six cases (`chk1.py`):

     | inversion | T_known | branch | B (W) | Ts / Tr / Tc (Nm) | Pin-Pout-loss (W) |
     |---|---|---|---|---|---|
     | ring fixed | sun +1 | fwd | 0.22238 | 1 / 1.974704 / -2.974704 | -2e-14 |
     | ring fixed | sun -1 | rev | 0.22551 | -1 / -2.025658 / 3.025658 | 4e-15 |
     | carrier fixed | sun +1 | fwd | 0.33365 | 1 / 1.976173 / -2.976173 | 9e-16 |
     | carrier fixed | sun -1 | rev | 0.33806 | -1 / -2.024146 / 3.024146 | 4e-15 |
     | sun fixed | ring +1 | rev | 0.11135 | 0.494186 / 1 / -1.494186 | 4e-15 |
     | sun fixed | ring -1 | fwd | 0.11253 | -0.505884 / -1 / 1.505884 | 2e-14 |

     The values in ASTRA_CORE_FIX_r3 (0.222378 W, -2.974704 Nm; 0.225509 W, 3.025658 Nm) are reproduced. The sun mesh
     torque equals the port torque (1.0) when there is no input drag. Signs are right: bearing torque on the planets is
     -B/(wp - wc), and the carrier gets the opposite.
   - My own example (speed-up drive: ring fixed, carrier drives with Tc = +1, u = 3.5, wc = 1, ws = 4.5,
     eta_sp = 0.98, eta_pr = 0.99). By hand at B = 0.1 W: A = 3.5/0.9702 = 3.60750, C = -0.10101,
     Ts = -(1 - 0.10101)/4.60750 = -0.195114, Tr = -0.804886. The chain gives 0.804886*0.99 - 0.1 = 0.696837 and
     *0.98 = 0.682900 = 0.195114*3.5, efficiency 0.878014. The code gives the same. At B = 0.9 W the result is valid
     with eta = 0.0888. At B = 1.5 W > Pin both branches are inadmissible, and the code returns `failed` with
     efficiency None, as it should (`chk3.py`).
   - Compound 40/20(30)/90: u = 1.5 matches -vs/vr, and the power-chain residual is 6e-14 at +/-5 Nm (`chk8.py`).
   - When the iteration runs out, `pf.state = failed` and efficiency is None (`gearbox.py:1213`). It reaches point,
     Miner and peaks (`:1759`, `:2365`; tests at r3:136 and :304). This works.
2. **Peaks / Miner.** The S-N inversion (`materials.py:168-202`) is the exact inverse of `y_nt`/`z_nt`:
   N = N_ref / y^(log(N_ref/N_st)/log Y_st). For y = 1290/860 = 1.5 that is 3e6/1.5^8.737 = about 8.68e4 cycles, the
   same as the code. Peak cycles are
   events * per-tooth engagements. Duty cycles counted in the carrier frame (`:1952-1954`: sun N*|vs|, planet |vp|,
   ring N*|vr|) are correct. Miner rejects capacity <= 0 or NaN; +inf counts as zero damage; None makes damage and life
   unavailable. A planetary with full peak data (static limits and engagements for every tooth) still has ring root
   `unsupported`, min gear life None and `all_passed=False` (`chk4.py` H). Wrong-temperature limits give static
   unsupported (G).
3. **ISO 6336.** Z_L = C_ZL + 4(1 - C_ZL)/(1.2 + 134/nu40)^2: for PAO 220 with C_ZL = 0.91 I get
   0.91 + 0.36/1.80909^2 = 1.019997, which matches. Z_v and Z_R (2019, Rz10) also match the standard. Y_DT gate is
   grade <= 4 and eps_an > 2.05, with 2.366 - 0.666*eps_an up to 2.5 and 0.7 above. I checked the algebra of h_Fe
   against the ISO form: r_en*cos(alpha_en)/cos(alpha_Fen) = r_bn/cos(alpha_Fen). At aw = 31 the solved x1 = x2 =
   0.556693, k = 0.113385 mm and eps_a = 1.341592 (my own value from the tip circles). My separate Method B code gives
   pinion YF/YS 1.332659/2.186191 and wheel 1.372894/2.200649, the same as the code to 1e-12 (`chk6.py`).
4. **Adversarial inputs that correctly do not pass** (`chk4.py`):
   - alpha_n 30 deg: unsupported.
   - beta 35 deg: failed.
   - Zero backlash allowance: failed.
   - 250 C and -60 C: lubrication unsupported.
   - 500 Nm overload: failed.
   - Peak of 8.7 GPa: capacity clamped to 1 cycle, life 0.5 h, failed.
   - Internal ring root: unsupported.
   - Coaxiality error and non-convergence: failed (tests).
   - NaN, bool, string and negative inputs are rejected at construction (tests).

## FOLLOW-UPS

- F1. Duty S_H/S_F use a fixed `cycles_default = 5e7` (`gearbox.py:693`) that is not tied to required life. For the
  planet in the sun mesh, `:1228` uses 5e7/N. The real planet/sun tooth-cycle ratio is (zs/zp)/N = 0.667 for 40/20/80,
  and the code uses 0.333. Z_NT becomes 1.087 instead of 1.031, so the planet's S_H is 5 % high. The same planet in the
  ring mesh uses 5e7 (`:1244`). The life gate still protects the pass, so this is a follow-up, but the S_H label must
  say "reference 5e7 cycles", or the code should compute cycles from life and speed.
- F2. When every member is below endurance (zero damage), `min_gear_life_h=None` and the gear-life check reads
  `unsupported`, so the preset fails. In `chk5.py`, 2 Nm fails while 6 Nm passes. This is conservative but not
  monotonic, and the UI would show the best design as "unsupported". Report it as endurance (infinite) and pass.
- F3. Stresses above the static plateau extrapolate the S-N curve down to 1 cycle. Flag this as "outside S-N domain".
- F4. Y_DT is gated per member grade. ISO intends the pair: use the worse grade of the two.
- F5. For helical gears, YF puts the load point between OPSC and tip, interpolated by eps_beta (`rating:433-435`).
  I did not verify this against ISO 6336-3:2019. Spur gears are not affected.
- F6. When a mesh efficiency is unavailable, `eta=1.0` is substituted (`gearbox.py:1115`). Only the stage state shows
  this. Flag it explicitly.
- F7. `min_bearing_life_h` keeps finite values from bearings whose state is `failed` (needle skidding, chk2: state
  failed, L10h 2.26e7 h). The state blocks the pass, but the number is shown. Per-bearing availability should be the
  source the UI uses.
- F8. Internal-root bending, signed reversal history, thin rims, unequal branch vectors and churning allocation are
  unsupported or estimated, as documented. They are reported as such in the results I inspected.

## API GAPS (for HTTP routes, storage and a 3-D viewer)

The point, spectrum and assembly request/response pairs round trip exactly through JSON (allow_nan=False) for a
planetary + pair chain (`chk7.py`). Stage IDs survive rename and reorder. The strict decoder rejects unknown or missing
fields, wrong types, version and kind. Gaps:

- G1. **Results have no integrity check.** Changing `preset_evaluation.all_passed` to true, or editing
  `overall_efficiency`, is accepted by `GearboxResponse.from_dict`, because canonical form only re-derives
  availability. Storage needs a content hash, a core version and an input hash, or it must recompute.
- G2. Responses carry no core/code version, solver settings hash or request hash (needed for cache keys and
  provenance).
- G3. No JSON Schema or OpenAPI export. Routes must hand-write validation docs. Errors are bare `ValueError` strings,
  with no field path or code.
- G4. The decoder needs complete canonical records, with no defaults filled in. An HTTP layer must build requests
  through constructors. Without that, the server could fill a random `stage_id` itself (`gearbox.py:212`), and that
  would break ID stability.
- G5. No catalog endpoints in the core API to list material, lubricant, bearing and preset names that the request
  references by string.
- G6. Viewer: `pose_at()` is a method and is not serialized. Members carry the speed and orbit ratios, so poses can be
  rebuilt on the client, but no formula or contract is documented. The assembly payload is 4.4 MB for two small
  stages, with no level of detail or decimation option. Planet histories use the aggregate `planet` while placements
  use `planet_0..n`, and the mapping table is not in the payload.
- G7. There is one pass boolean, and per B1 it is not complete. Add a single top-level `qualified`/`passed` that
  combines state, preset and parts.

## Tests: independent oracle or self-comparison

Independent: `test_all_local_planetary_frames_hand_oracle` and `test_planet_bearing_local_equilibrium_independent`
(the power-chain equations are written in a different form from the solver), `test_deep_tooth_factor_precision_condition`
(eps_a from tip circles), x inversion in `test_resolved_shift...`, `test_planetary_external_load_and_support_moment`
(FL/4 over pi d^3/32), `test_translated_envelope...`, `test_compound_unequal_width_planes`, and the trivial Miner
arithmetic in `test_miner_missing...`. `test_peak_static_ratio...` re-derives the same log-log curve definition.

Self-comparison only: YF/YS equality in `test_resolved_shift...` (solved vs explicit `with_x`; my chk6 supplies the
missing ISO oracle), `test_coupled_planet_bearings...` (power balance of the code's own stored values),
`test_peak_events_change_life...` (damage delta against the code's own capacity), and all round-trip/decoder tests.
Missing: a test that preset pass is false when a part fails (B1), an endurance-only spectrum (F2), and any
literature or measured efficiency or strength benchmark.

## NOT VERIFIED

- Mesh-loss model (eta_sp/eta_pr values).
- Bearing friction torque M and ISO 281 a_ISO / spectrum life.
- K_v, K_Hbeta, K_Halpha.
- Y_beta 2019 form.
- Internal-gear geometry and helical YF.
- Thermal balance.
- DIN 743 shaft factors beyond the bending formula.
- Carrier/pin/rim parts.
- Stiffness, mass, inertia.
- Transmission IR.
- Outline correctness for CAD.
- Bin-by-bin coverage of `bin_evaluations` for more than two bins.
- Fix-report evidence JSON hashes.
