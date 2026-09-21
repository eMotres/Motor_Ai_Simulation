# The open frame, part two: the rotor is in the wash (2026-09-21)

Owner, 2026-09-21, with his IR photographs of an open drone motor on a propeller
stand: *«по термофотографиям катушки греются всегда значительно больше магнитов;
конструкция полностью открыта, магниты обдуваются со всех сторон, и воздух ещё
продувает зазор — надо это как-то учесть, когда мы задаём no housing».*

The 2026-09-09 open frame (`frame=open`) put the **stator** side in the
propeller stream — the end turns (`cooling.end_windings`) and the ventilated
slot channels (`cooling.slot_channels`) — and left the **rotor** exactly where
the housed model had it: the mechanical clearance and the bore as its only
doors. On the CIANO14 50 edited / L15 record that reads the magnets at 240 °C
against a 251 °C winding, i.e. the rotor all but welded to the stator through
0.2 mm of air, and the S1 rating comes out limited by the magnet.

Two paths were added, both only when `frame == "open"`.

## 1. The rotor's axial end faces, in the wash

`cooling_models.rotor_end_faces_open`, reported in the existing
`cooling.end_faces` block on the `rotor` and `magnet` nodes (each block now
carries `film_kind`).

Areas are measured on this cross-section (`rotor` core annulus, `magnet`
sections) × the model's symmetry × `end_face_sides`, exactly as the robotics
mode's still-air faces are. The film is the **larger** of two mechanisms, never
their sum:

| mechanism | correlation | length |
|---|---|---|
| free disc rotating in air | `Nu = 0.36·Re_ω^½` (Re_ω < 2.4·10⁵, Cobb & Saunders); `Nu = 0.015·Re_ω^0.8` above (Dorfman) | rotor radius `R`, `Re_ω = ω·R²/ν` |
| flat plate in parallel forced flow | `Nu = 0.664·Re_L^½·Pr^⅓` (Re_L < 5·10⁵); `Nu = (0.037·Re_L^0.8 − 871)·Pr^⅓` above | face diameter `2R`, `Re_L = v·2R/ν` |

Floored at `NATURAL_CONVECTION_H` (7 W/m²·K). **No radiation term**: an open
rotor face sees the end plate, the standoff pins and the propeller, not the
room, and a view factor of 1 would over-read it. The disc film is there whenever
the machine turns, so an open rotor is never at the still-air floor.

The **winding's** and the **stator core's** end faces are deliberately *not*
added on an open frame: the end turns already have `cooling.end_windings` in
this same wash and counting them twice would cool the copper twice; the stator
core's end annulus is a real uncounted path and is left out rather than assumed.

## 2. The air gap as a ventilated duct

`cooling_models.gap_axial_flow`, reported in the new `cooling.gap_flow` block.

**The through-velocity is solved, not assumed.** The free stream arrives with a
dynamic head ½ρv_wash² and the gap spends it on an entrance loss, the channel
friction over the stack and the exit:

```
½ρv_wash² = (K_in + K_out + f_D·L/D_h)·½ρv_gap²
v_gap     = v_wash / sqrt(K_in + K_out + f_D·L/D_h)
```

with `D_h = 2δ` (δ = stator bore − rotor OD), `K_in = 0.5`, `K_out = 1.0`,
`f_D = 64/Re` laminar and `0.316·Re^-¼` turbulent, `Re = v_gap·D_h/ν`. The
balance is implicit and is iterated. On the Ø50 (δ 0.25 mm, 15 mm stack) that is
`L/D_h = 30`, and the answer runs from 0.44 m/s at a 10 m/s wash to 14.9 m/s at
40 m/s — strongly non-linear, because the laminar friction term shrinks as the
flow speeds up.

**The model is an enthalpy conductance**, not a film:

```
Q = ṁ·cp·(T_out − T_in),  T_mean = (T_in + T_out)/2  →  G = 2·ṁ·cp
```

applied to the gap **air's own elements** against ambient. The wall→air film is
deliberately not folded in: in a 0.25 mm clearance the meshed gap air already
carries that resistance by conduction, and adding a film would count it twice.
What the walls can actually hand the stream therefore falls out of the coupled
solve; a channel whose enthalpy capacity exceeds what the walls can transfer
simply leaves cooler. `k_eff_axial` is reported so the axial-flow Nusselt can be
compared with the Taylor–Couette value (on these machines it is a near no-op:
the gap is conduction-dominated at Ta ≈ 600).

**Two streams, split at the slip radius** (`volume_sinks` gained an
`r_range_m` filter for it): the rotor half of the clearance and the stator half,
each with its own share of the annular cross-section and its own mean air
temperature. One sink could not be attributed, and then neither
`rotor_heat_split` nor `stator_heat_split` would close.

**The open magnet recess is part of the duct.** Both pocket families of this
geometry generator open to the rotor OD (`rotor_hole ≥ 1` = straight sides run
to the OD; `rotor_hole < 1` = a narrower rectangular opening from the OD — see
`cadquery_geometry._extended_pocket` / `_pocket_cut_depth`), so when
`magnet_up_gap > 0` the air above the magnet is continuous with the gap and the
magnet's **top face is washed by it**. The recess's free area is *measured* on
the mesh (elements above `r = rotor_OD − magnet_up_gap`), the same rule the slot
channels follow. With `magnet_up_gap = 0` the magnet top **is** the rotor OD and
already sees the gap film; with pockets this section shows closed, the pocket air
stays enclosed and conduction-only. The block says which of the three it found.

## 3. Downstream

* **Heat budget**: `gap_flow_W`, `gap_flow_rotor_W`, `gap_flow_stator_W` (0 on a
  housed machine, and 0 there is a statement). `rotor_heat_split.gap_flow_W` and
  `stator_heat_split.gap_flow_W` join their own closures.
* **Lumped network** (`thermal_duty_cycle.network_from_steady`, `surface_fit`):
  two fitted conductances `r_gap_flow` / `s_gap_flow` (rotor→ambient,
  stator→ambient) and two new flows `rotor_gap_flow` / `stator_gap_flow` on the
  right sides of the split. The end-face film kind is now **read off the map**
  (`film_kind`) instead of being hardcoded `natural`, so the rotor's 143 W/m²·K
  forced film is held rather than walked down a Rayleigh number to 8.
* **UI**: one clause added to the Frame HelpTip — on Open, the rotor end faces
  and the air gap are in the same wash. No new inputs (the owner removed the
  separate wash field in bde8fae; the wash is the housing air speed).

## 4. Calibration status — NOT calibrated

`cooling_models.OPEN_GAP_FLOW_CALIBRATION` and
`OPEN_ROTOR_FACE_H_CALIBRATION` are both **1.0**, i.e. the correlations as
published. They are multipliers with docstrings saying what they are waiting
for. Two effects the pressure balance does not carry pull in opposite
directions and are stated rather than tuned into it:

* the **full** free-stream head is taken as available at the gap mouth (the
  inlet treated as a stagnation region discharging to static ambient) —
  optimistic;
* the rotor's own **disc pumping**, which drags air through its own clearance
  and adds to this flow, is not counted at all — pessimistic.

## 5. Validation

### 5a. Ø40 propeller stand — the first real calibration point

**Evidence** (owner, 2026-09-21, one IR photograph + the test-stand chat):
CIANO14 40_12 / L12 on a propeller test stand, *steady* ("50 A continuous, I
would say"), **DC 50 A at 24.6 V ≈ 1.23 kW** at the ESC input. IR camera scale
30.9–201.0 °C: **coil end turns ≈ 200 °C (MAX 201.0)**, **rotor/magnet zone
≈ 115.5 °C** (spot cursor), ambient/min 30.9 °C. Speed, propeller and wash not
recorded.

**Assumptions, every one of them** — this is a qualitative target, not a fitted
point:

1. **The magnet reading is a LOWER bound.** Machined steel and a plated magnet
   radiate far worse than enamelled copper; at the camera's assumed emissivity
   the rotor's true temperature is somewhat above 115.5 °C. So the *rise ratio*
   `(T_magnet − T_amb)/(T_winding − T_amb) ≈ (115.5 − 30.9)/(200 − 30.9) = 0.50`
   is a **lower bound** too.
2. **Operating point**: reconstructed as the machine's own saved rated duty,
   **13 000 rpm, 43.84 A rms, γ = 10°**, coil reference 180 °C. Cross-check
   against the photo's electrical input: 1.23 kW at the ESC, less 3–5 % ESC loss
   ≈ 1.17–1.19 kW into the motor, ≈ 1.10 kW at the shaft at the duty's 94 %.
   The speed follows the bus ceiling — the duty's `V_ll_peak` is 16.9 V at
   13 000 rpm, so at 24.6 V the ceiling is **16 400 rpm** (sine PWM,
   `V_ll_peak = 0.866·V_dc`) to **20 800 rpm** (six-step,
   `V_ll_peak = (2√3/π)·V_dc`). At 1.10 kW over that range the torque is
   0.50–0.64 N·m, i.e. **42–47 A rms** on the duty's own 0.0137 N·m/A — within a
   few per cent of the 43.84 A used. The **iron and magnet-eddy loss is
   therefore under-read** (the map is solved at 13 000 rpm, the machine was
   probably at 16–21 krpm); on this machine copper dominates, so the winding
   number moves little and the magnet number is the conservative direction.
3. **EM map**: one sandbox transient, 8 frames/period, eddy + rotor eddy, no
   demag, mesh 1.0/0.3 mm, 2 sectors. 8 frames is a cycle average good enough
   for copper; the iron terms carry the coarser discretisation.
4. **Materials**: L12.yaml carries no material assignment, so the sandbox
   config's own assignment was used.
5. **Wash**: swept, not assumed. Momentum theory on a 6–7″ propeller at ~1.1 kW
   shaft (FM ≈ 0.7) gives a far-field slipstream of ~45–50 m/s, but the motor
   sits at the **hub**, inside the blade-root region where the local axial
   velocity is a fraction of that — hence the 10–25 m/s range. The owner's own
   figure for these 40 mm machines (2026-09-09) is **10–12 m/s**.
6. Cooling otherwise: `cooling_mode=air` on the tooth backs at the same wash,
   `bore_mode=none`, ambient 30.9 °C (the photo's own minimum), both ends
   exposed.

**The table** — winding mean/max, magnet mean, and the rise ratio, before and
after this change, at the same EM map and the same cooling:

| wash m/s | BEFORE winding mean/max | BEFORE magnet | BEFORE ratio | AFTER winding mean/max | AFTER magnet | AFTER ratio |
|---:|---:|---:|---:|---:|---:|---:|
| 10 | 243.9 / 245.8 | 251.9 | **1.038** | 217.6 / 220.1 | 133.4 | **0.549** |
| 12 | — | — | — | 201.1 / 203.5 | 120.3 | **0.525** |
| 13 | — | — | — | 193.8 / 196.1 | 112.9 | **0.503** |
| 15 | 199.2 / 201.3 | 205.5 | **1.037** | 177.0 / 179.6 | 96.7 | **0.450** |
| 20 | 167.2 / 169.8 | 169.1 | **1.014** | 148.7 / 151.7 | 70.6 | **0.337** |
| 25 | 150.4 / 153.0 | 151.6 | **1.010** | 133.1 / 136.3 | 57.3 | **0.258** |
| **photo** | | | | **≈ 200 (max 201.0)** | **≥ 115.5** | **≥ 0.50** |

The old model says the magnets are **hotter than the coils** (ratio ≈ 1.01–1.04)
at every wash speed — the photograph flatly contradicts that. The new model at a
**12 m/s** wash gives winding 201.1 mean / 203.5 max and magnet 120.3 °C, ratio
0.525 — the photograph's own numbers, at a wash inside the owner's own 10–12 m/s
figure and **with both calibration constants left at their physics-derived
1.0**. Nothing was tuned to produce this.

Read the other way: the photo's winding max (201 °C) puts the wash at ≈ 12 m/s,
and at that wash the model's magnet (120.3 °C) sits 5 K above the IR reading —
which is the right side of it, because the IR reading is a lower bound.

### 5b. Ø50 (CIANO14 50 edited / L15), the record that started this

The duty's saved cooling: `cooling_mode=air`, outer **40 m/s**, bore air 10 m/s,
frame `open`, ambient 30 °C, at 10 000 rpm / 63.64 A / γ 10° / coil 120 °C. Same
EM map (the server workspace's own transient snapshot) both times; only the code
differs. Ambient 30 °C.

| | BEFORE | AFTER |
|---|---:|---:|
| winding mean / max °C | 251.4 / 257.7 | **219.1 / 226.4** |
| magnet mean / max °C | 239.4 / 240.3 | **60.7 / 61.3** |
| rotor mean °C | 239.3 | 60.7 |
| stator mean / max °C | 235.6 / 240.5 | 194.4 / 202.0 |
| **rise ratio (magnet/winding)** | **0.946** | **0.162** |
| housing W | 49.17 | 40.39 |
| bore W | 5.88 | 0.87 |
| gap (slip tie) W | 3.46 | −10.61 |
| end windings W | 169.84 | 145.04 |
| slot channels W | 37.81 | 26.61 |
| rotor + magnet end faces W | 0 | 4.88 |
| gap through-flow W (rotor / stator) | 0 | 44.91 (14.19 / 30.72) |
| budget residual | 0.0 % | 0.0 % |
| network fit residual (`merged` links) | none | none |
| **S1 rating at this cooling** | **42.4 A rms, limited by magnet 150 °C** | **53.4 A rms, limited by winding 200 °C** |

The `gap_W` sign flip is the physics, not an error: with the clearance
ventilated the rotor runs *below* the stator and the slip tie now carries
10.6 W **into** the rotor side, which is then blown away with the rest. Both
splits close: `rotor_heat_split.closure_W = 0.00`,
`stator_heat_split.closure_W = 0.00`.

Network fit (`surface_fit=True`), before → after:
`w_s` 2.136 → 2.824 W/K, `r_s` 0.935 → 0.079 W/K, `r_bore` 0.0281 → 0.0283,
new `r_gap_flow` 0.462 and `s_gap_flow` 0.187 W/K, new surface fits
`rotor_ends` 0.0739 and `magnet_ends` 0.0851 W/K with `film_kind = forced`
(held, not re-evaluated). No link merged in either case.

**The Ø50's 0.162 is not a different physics from the Ø40's 0.525 — it is a
different wash.** The same sweep on the Ø50:

| wash m/s | winding mean/max | magnet mean | ratio | v_gap m/s | gap-flow W | end-face W |
|---:|---:|---:|---:|---:|---:|---:|
| 10 | 495.2 / 500.8 | 285.0 | 0.548 | 0.44 | 4.3 | 21.4 |
| 12 | 455.5 / 460.8 | 258.9 | 0.538 | 0.69 | 6.4 | 19.6 |
| 15 | 392.7 / 399.0 | 206.2 | 0.486 | 1.30 | 10.7 | 16.9 |
| 20 | 327.6 / 334.5 | 148.3 | 0.398 | 2.88 | 19.2 | 13.2 |
| 30 | 257.8 / 265.1 | 86.4 | 0.248 | 8.06 | 35.4 | 7.7 |
| 40 (the duty's own) | 219.1 / 226.4 | 60.7 | 0.162 | 14.95 | 44.9 | 4.9 |

At the 10–15 m/s the photograph implies, the Ø50 model gives a rise ratio of
0.49–0.55 — the same answer as the Ø40. The 40 m/s in the saved duty is a design
assumption the owner typed, not a measurement, and it is what drives the ratio
down to 0.16. (Those low-wash rows are not design points — the winding is at
400–500 °C — they are here only to show that the ratio is a function of the
wash, not of the machine.)

**What would have to move to hit 0.5 at 40 m/s**: the gap through-flow would
have to fall by roughly an order of magnitude (`OPEN_GAP_FLOW_CALIBRATION` ≈
0.1, i.e. v_gap ≈ 1.5 m/s instead of 14.9) and the end-face film by about half.
That is well outside the assumption range of the pressure balance, and the Ø40
photograph does **not** ask for it — it is matched at 1.0. The constants are
therefore left at 1.0, and the open question is the **wash speed the Ø50 duty
was saved with**, not the correlations. That is a question for the owner.

### 5c. Housed is bit-identical

Same Ø50 loss map, `frame=housed`, under HEAD (d90c001) and under this change,
in three cooling modes — forced air, a water jacket, and robotics (still air +
radiation + mount + the four axial end faces):

| case | winding mean/max °C | magnet mean °C |
|---|---:|---:|
| housed + forced air | 1102.7 / 1120.3 | 922.4 |
| housed + water jacket | 127.5 / 145.3 | 165.4 |
| housed + robotics | 416.0 / 425.7 | 318.1 |

Every number identical between the two trees: a full recursive comparison of
the temperatures, the heat budgets, both heat splits, the surface blocks, the
end-face block and the gap block found **zero numeric differences**. The only
new keys are `gap_flow_W` / `gap_flow_rotor_W` / `gap_flow_stator_W` in the
budget and `gap_flow_W` / `gap_flow_pct` in the two splits, all **0.0**.
(The housed-with-forced-air case is absurd on this machine by design — a 50 mm
machine with no bore cooling and no end paths cannot shed 262 W — it is a
regression fixture, not a design.)

## 6. Tests

`tests/test_thermal_open_rotor.py` (17 tests) pins the contract: housed reports
both blocks as `mode: off` with zero watts and zero budget lines; the rotor faces
are forced, at the wash speed, with `h_rad = 0` and above the natural floor, and
`P = G·ΔT` holds on them; the gap's through-velocity is a fraction of the wash
with the losses that produced it in the payload; the stream's energy balance
closes twice over (`Q = G·(T_air − T_∞)` per side and `ΔT_air = Q/(ṁ·cp)`); the
open magnet recess is found and measured; no wash leaves the gap the closed
conductor it was; the whole budget and both heat splits close; the open rotor
runs colder than the housed one *and* its rise ratio falls relative to the
winding's; the network carries `r_gap_flow` / `s_gap_flow` and holds the forced
faces; and the two correlations behave (larger-of-two, never the sum; a longer
or narrower gap passes less air; `G = 2·ṁ·cp`; both calibration constants are
1.0 and the module says they await calibration).

`tests/test_thermal_open_frame.py::test_the_budget_still_closes_with_both_new_sinks`
gained the two new outflow lines in its identity.

Run: `tests/test_thermal_open_frame.py tests/test_thermal_robotics.py
tests/test_thermal_duty_cycle.py tests/test_continuous_rating.py
tests/test_thermal_open_rotor.py` — 107 passed.

## 7. Open items

* **The wash speed of the Ø50 duty** (40 m/s) is an assumption, and it is what
  the magnet temperature now hangs on. Worth asking the owner where it came
  from before the L15 record is re-generated.
* The **stator core's end annulus** is a real uncounted path on an open machine.
  Left out deliberately here; it would lower the stator and the winding further.
* The Ø40 point would be worth re-running at the reconstructed **16–21 krpm**
  with a 48-frame map once the owner recalls the throttle setting: that raises
  the iron and magnet-eddy loss and is the direction that moves the magnet
  number up toward the IR reading.
* Splitting the gap stream in two assumes the two halves do not fully mix over
  the stack. On the Ø50 the transit time (~1 ms) and the cross-gap diffusion
  time (~2 ms) are comparable, so the truth is between the two-stream and the
  one-stream lumping; a single mixed stream would put the rotor a few kelvin
  warmer.
