# The Controller module — Stage 1 (2026-09-22)

Owner, 2026-09-22 10:20: *«давай начнём делать модуль инвертора и его
моделирование и начнём с IMCQ120R004M2H для мотора L155 motor, чтобы была
возможность его подключить к мотору и выдавать уже реальный сигнал, ну и
конечно считать потери в инверторе с учётом системы охлаждения MOSFET; пусть
это всё будет в отдельном меню Controller, и чтобы была возможность делать
каплинг с электромагнитным решателем»*, and, 15 minutes later, the requirement
that shaped the whole design: *«чтобы была возможность комбинировать мосты так,
как нам надо: один контроллер на один мотор, два контроллера на один мотор и
т.д., один мост на каждую катушку отдельно»*.

So the module is not "a three-phase inverter". It is a **map from the motor's
coils onto bridges**, plus the arithmetic that map implies.

---

## 1 · What is where

| what | where |
|---|---|
| device cards | `config/devices/<part>.yaml` — one YAML per real part |
| card loader / validator | `src/motor_ai_sim/inverter/devices.py` |
| package outlines (generated thumbnails) | `src/motor_ai_sim/inverter/packages.py` |
| coil → bridge map | `src/motor_ai_sim/inverter/topology.py` |
| losses, T_j, efficiencies | `src/motor_ai_sim/inverter/losses.py` |
| PWM waveform, dead time, DC link | `src/motor_ai_sim/inverter/waveforms.py` |
| power schematic (SVG) | `src/motor_ai_sim/inverter/schematic.py` |
| routes | `src/motor_ai_sim/routes/controller.py` (`/api/controller/*`) |
| duty record | `duty_results` kind `controller` (`compact_controller`) |
| tab | `web/src/components/controller/` |
| report | `report.py` section `controller`, `report_docx.py::_controller` |
| datasheet | `datasheet.py` — "Inverter efficiency", "Wall-to-shaft efficiency" |
| tests | `tests/test_controller.py`, `web/.../__tests__/controllerApi.test.mjs` |

---

## 2 · The device card

One card per PART. Gate resistor, gate-off voltage, parallel count, dead time
and the bus are the CONTROLLER's choices and live in the request, never in the
card.

Two kinds of number, and they are tagged, because they do not carry the same
authority:

* `basis: table` — read from a numbered table of the datasheet. Exact.
* `basis: figure` — read off a plotted curve by eye. `tolerance_pct` says how
  far it may be out. Where a figure point coincides with a table point the
  TABLE value is written and the point is marked `anchor: true`.

A value the datasheet does not publish is `null`, and whatever asks for it is
told so by name. Nothing is invented.

### IMCQ120R004M2H — what the card carries

Infineon CoolSiC™ 1200 V SiC MOSFET G2, Q-DPAK (PG-HDSOP-22-U03), top-side
cooled. Datasheet revision 1.10 (2025-10-13);
<https://www.infineon.com/assets/row/public/documents/60/49/infineon-imcq120r004m2h-datasheet-en.pdf>

| block | source |
|---|---|
| V_DSS 1200 V, I_DDC 403 A @ 25 °C / 287 A @ 100 °C, I_DM 1433 A, T_vj 175 °C (200 °C overload ≤ 100 h) | Table 2, Table 4 |
| V_GS(on) 15…18 V, V_GS(off) −5…0 V, V_GS(th) 4.2 V typ, Q_G 348 nC, R_G,int 2.25 Ω | Table 3, Table 4 |
| R_DS(on) 3.7 / 7.6 / 8.9 mΩ at 25 / 150 / 175 °C (V_GS 18 V, I_D 185.2 A); 4.7 mΩ at V_GS 15 V, 25 °C | Table 4 (anchors) + the R_DS(on) = f(T_vj) figure, p. 10 |
| C_iss 13 nF, C_oss 574 pF, C_rss 50 pF, C_o(er) 756 pF, C_o(tr) 1123 pF, E_oss 242 µJ, Q_oss 898 nC (all at 800 V) | Table 4 |
| E_on / E_off / E_fr at 185.2 A, 800 V, R_G,ext 2.3 Ω, at 25 and 175 °C, for V_GS(off) 0 V and −5 V | Table 4, Table 6 (anchors) + the E = f(I_D) figures, p. 12 |
| V_SD 4.2 / 4.1 / 4.0 V at 25 / 100 / 175 °C (I_SD 185.2 A, V_GS 0 V); I_SD = f(V_SD) curves | Table 6 (anchor) + the figures, p. 11 |
| R_th(j-c) 0.07 typ / 0.1 max K/W, R_th(j-a) 62 K/W max | Table 1 |
| package 15.10 × 21.11 × 2.35 mm (D × E × A, max), body 15.50 mm, 22 leads on 1.14 mm pitch | Figure 1 (section 5, Package outlines) |
| weight | **not present in this revision** → `null`, printed "—" |
| Z_th(j-c) transient curve | **not present in this revision** → `null` |

`package_size_mm` and `weight_g` are card fields; the catalogue draws a
**generated** outline from `packages.py` (a family table keyed by the package
name, scaled by the card's own dimensions) rather than any vendor image. A card
may name a local file the owner placed in `config/devices/img/` (`image:`), and
`GET /api/controller/devices/{part}/image` serves it — strictly from inside that
folder, because a card is data and data does not get to name a path.

### How the card's numbers are moved to the working point

These are MODEL rules and the card names them as such — the datasheet publishes
no scaling law:

* **current** — piecewise-linear between the tabulated points; linear
  extrapolation outside them. Extrapolating UP is flagged as a warning (that is
  the direction a loss model must not under-read). Extrapolating DOWN to zero
  is stated once as a model note — every curve stops at 25 A and that is where
  a sinusoidal current's zero crossings live.
* **temperature** — linear between the 25 °C and 175 °C curve sets. A 25 °C
  block usually holds one table point; the card names whose SHAPE to borrow
  (`shape_from`) and the borrowed curve is scaled through that exact point.
* **bus voltage** — `E(V) = E(800 V) · (V/800)^1`, the first-order
  hard-switching rule. ASSUMPTION; the exponent is a card field.
* **gate resistance** — linear in R_G,ext through the datasheet's 2.3 Ω, for
  E_on and E_off only (the `E = f(R_G,ext)` figure on p. 12 is a straight line
  over 2.3…50 Ω). E_fr *falls* with R_G and is held at its 2.3 Ω value, which
  over-reads slightly at large R_G.
* **E_oss** — `E_oss(V) = ½·C_o(er)·V²`, AN2025-10 eq. (11) rearranged. It
  reproduces the 242 µJ at 800 V exactly, so this one is not an assumption.
* **R_DS(on) vs gate voltage** — the CLOSEST published V_GS(on) curve is used
  and the choice is reported. Interpolating between two gate voltages would
  invent a curve the datasheet does not publish.

---

## 3 · The coil → bridge map

The motor's coils come from the winding builder
(`geometry_2d.build_winding_layout`), so the controller and the FEM agree about
which coil is which. Single layer = one coil per TWO slots on the tooth
between them; L155 (12 slots, 10 poles) gives six coils:

```
coil 1 · A+ · tooth 1  · slots 1/2      coil 4 · A- · tooth 7  · slots 7/8
coil 2 · B- · tooth 3  · slots 3/4      coil 5 · B+ · tooth 9  · slots 9/10
coil 3 · C+ · tooth 5  · slots 5/6      coil 6 · C- · tooth 11 · slots 11/12
```

| preset | bridges | switches | what the leg carries |
|---|---|---|---|
| `one_3ph` | 1 × three-phase two-level | 6 | phase current (star) or **line** current (delta) |
| `two_3ph` | 2 × three-phase two-level, coils 1-3-5 and 2-4-6 | 12 | see `set_split` below |
| `h_bridge` | one full H-bridge per coil | 4 × n_coils | the coil current, through both legs |
| `custom` | an explicit `[{coil, bridge, leg}]` list | validated | as mapped |

**The delta trap, and it is worth saying out loud.** The bridge sits OUTSIDE
the delta, so each leg carries √3 × the phase current. The duty records store
`point.I_phase_rms` as the LINE value on a delta duty (562 A on L155 rated)
while `inverter.I_phase_rms_solved_A` is the winding's own phase current
(314 A). The route takes the second and says so in `sources`.

**`set_split`** — what a SECOND inverter does depends on how the coils are
reconnected, and the two answers differ by a factor of two in device current:

* `series_split` (default) — the winding is untouched. Each set keeps the
  per-coil current and supplies half the volts, so each inverter handles half
  the power at the SAME leg current. Twice the switches at the same current is
  twice the conduction loss: on this winding a second inverter costs, it does
  not save.
* `power_split` — the sets are reconnected so each inverter delivers half the
  power at the full bus, halving the device current.

**H-bridge modulation** — `unipolar` (default) switches the two legs against
opposite references; the coil sees twice the ripple frequency and half the
volt-second step. `bipolar` switches the diagonals together. The device-level
event count per leg per carrier period is 1 in both; the difference is ripple,
not count, and the module says so rather than pretending otherwise.

**Validation.** Every coil must be driven by exactly one BRIDGE. Inside a
bridge a three-phase leg owns a coil once and an H-bridge owns it twice (its
two legs are the coil's two ends). Anything else is refused by coil number.

**Devices in parallel per switch** (owner: *«надо добавить number of
parallel»*) is `devices_parallel` for the whole controller and
`devices_parallel_by_bridge: {INV2: 7}` where one bridge differs. It is edited
in two places because those are the two places it is read: beside the device in
the catalogue, and on each bridge's row in the results table. The schematic
prints it on every switch as `×N`. The catalogue's **Parallel** column shows,
for the parts that are not selected, `ceil(I_switch_rms / I_DDC@100 °C)` for
the loaded duty and the chosen topology — a first cut on the current rating
alone; the junction temperature usually asks for more, and only Solve knows it.

**What one switch carries.** A switch conducts the leg current for about half
the period, so its own full-period rms is `I_leg/√2`, and a device's is
`I_leg/(N·√2)`. That is the number held against a continuous DC rating. The
conduction loss is unaffected — the two switches of a leg together dissipate
`I_leg²·R`.

---

## 4 · The loss model

Integrated over ONE electrical period on the modulator's own grid, per leg,
with `N` devices in parallel per switch:

| term | formula | why |
|---|---|---|
| conduction | `(1 − f_dt)·⟨i²⟩·R_DS(on)(T_j)/N` | with synchronous rectification the leg current is ALWAYS in a channel — high side or low side — so the leg's conduction loss does not depend on the duty at all; the duty only decides which of the two switches carries it |
| third quadrant | `f_dt·⟨|i|·V_SD(|i|/N)⟩`, `f_dt = 2·t_d·f_sw` | the dead-time windows: both channels off, the current in a SiC body diode at ~4 V. The same fraction is taken out of the conduction term |
| switching | `f_sw·N·⟨E_on + E_off + E_fr⟩` at `|i|/N` | one hard turn-on, one hard turn-off and one body-diode recovery per carrier period per leg (AN2025-10 §6.1: *the reverse recovery loss of the body diode is added to the turn-on energy of the opposite switch*; §5.2.4: E_fr is included in E_tot) |
| E_oss | reported, **not added** by default | the datasheet E_on is a hard-switching half-bridge measurement and already contains the channel discharge of C_oss (AN2025-10 §4.3.8.3 describes that exact mechanism). `e_oss_policy: "added"` gives the pessimistic bound |

The two switches of a leg are taken to share its loss equally — sinusoidal
current, symmetric modulation, one full fundamental period. Devices in one
switch position share the current equally, which is a layout-dependent
assumption and the usual reason a real stack is derated.

**DC-link ripple** is not a formula: `i_dc = Σ s_leg · i_leg` over the
modulator's switching functions, so `I_cap,rms = √(I_dc,rms² − I_dc,mean²)`.
Exact for an ideal two-level bridge, and it works unchanged for one bridge, for
two shifted bridges and for six H-bridges — which is why the whole model is
numerical rather than closed-form.

### Junction temperature

```
T_j = T_coolant_in + P_total/(2·ṁ·cp)          (coolant mean)
    + P_total · R_coldplate
    + P_device · (R_th(j-c) + R_TIM + R_spread)
```

iterated to 0.1 K with 0.6 damping. `R_th(j-c)` is the card's **max**, not its
typ — a margin quoted on the typical package is a margin no production unit is
guaranteed to have. `R_coldplate` comes from `simulation/cooling_models.py`,
the SAME laminar-3.66 / Gnielinski / Dittus–Boelter ladder the motor's water
jacket uses, so a coldplate and a jacket in one report are not two different
opinions about pipe flow.

The default plate is a MICRO-CHANNEL plate (40 × 1 × 5 mm channels, 300 mm) and
that is deliberate: with 50/50 glycol at 8 L/min the flow is laminar whatever
you do, so `h = 3.66·k/D_h` and the only lever left is the hydraulic diameter.
A plain 6 × 4 mm-channel plate gives ~0.11 K/W and cooks a several-kW stack.

Device characteristics are CLAMPED to the hottest temperature the card
tabulates (200 °C here); the reported T_j is the unclamped one, so an
impossible design stays visibly impossible. If the iteration diverges — R
rising faster with T_j than the plate removes heat — it stops and says
THERMAL RUNAWAY rather than printing a number.

### The two efficiencies

One efficiency at the shaft stays one efficiency at the shaft:

```
inverter efficiency      = P_ac_out / (P_ac_out + P_inverter)
wall-to-shaft efficiency = inverter efficiency × shaft efficiency
```

and the shaft efficiency is READ from the duty's coupled record, never
recomputed here.

---

## 5 · The waveform (and the Stage 2 hook)

`waveforms.leg_terminal_voltage` is the non-ideal modulator the coupled loop's
`inverter` block is not: the leg terminal follows the CURRENT during dead time
and every conducting device drops its own volts.

```
HS on,  i > 0   +V/2 − i·R_DS(on)        dead, i > 0   −V/2 − V_SD(i)
HS on,  i < 0   +V/2 + |i|·R_DS(on)      dead, i < 0   +V/2 + V_SD(|i|)
LS on,  i > 0   −V/2 − i·R_DS(on)
LS on,  i < 0   −V/2 + |i|·R_DS(on)
```

So the dead time subtracts volts while the current leaves the leg and adds them
while it enters: `ΔV = −sign(i)·t_d·f_sw·(V_dc + 2·V_SD)`, a step at every
current zero crossing. That is the shape test in `tests/test_controller.py`.

`waveforms.coil_waveforms` exports **per coil**, not per phase:

```json
{"f_elec_hz": .., "t_s": [..],
 "coils": {"1": {"bridge": "HB1", "v_V": [..], "i_A": [..],
                 "v_mean_V": .., "v_rms_V": .., "i_rms_A": ..}, ...}}
```

Per coil deliberately: the H-bridge topology drives every coil independently
and a phase-level interface could not express it; a three-phase topology simply
gives all coils of a phase the same series current. **Stage 2 adds a consumer,
not a format.**

The picture gets its own, finer grid (four samples per dead-time window, capped
at 400 per carrier). The loss integral does not need the dead time resolved —
it is integrated analytically from `t_d` — but the chart is read for exactly
one thing, and at the loss grid's resolution a 0.5 µs window falls between two
samples.

---

## 6 · L155 motor, CIANO10 200 opt — the three presets

Device **IMCQ120R004M2H**, DC link **750.4 V** (GF Myriad 200S3P), carrier
**24 kHz**, dead time **0.5 µs**, V_GS 18/0 V, R_G,ext 2.3 Ω,
coldplate **water-glycol 50/50, 8 L/min, 65 °C inlet**, R_TIM 0.03 K/W.
`N` is the smallest parallel device count that keeps **T_j ≤ 150 °C**.
Operating point, shaft efficiency and modulation index read from the duty's
stored coupled record (2026-09-15/16); nothing was re-solved.

### rated 1×9 mm — 14 200 rpm, 266.0 kW shaft, 272.2 kW AC, η_shaft 97.71 %, 314.3 A per delta phase (544.3 A per leg), m 0.633

| topology | N | devices | conduction | 3rd quadrant | switching | total | T_j | η_inv | η_wall-to-shaft | DC ripple |
|---|---|---|---|---|---|---|---|---|---|---|
| One 3-phase inverter | 3 | 6 × 3 = 18 | 1 929 W | 145 W | 2 078 W | **4 151 W** | 132 °C | **98.50 %** | **96.24 %** | 351 A rms |
| Two 3-phase inverters (series split) | 5 | 12 × 5 = 60 | 2 518 W | 254 W | 4 276 W | 7 048 W | 144 °C | 97.48 % | 95.24 % | 583 A rms |
| H-bridge per coil (unipolar) | 4 | 24 × 4 = 96 | 2 045 W | 276 W | 4 936 W | 7 256 W | 140 °C | 97.40 % | 95.17 % | 644 A rms |

### peak 1×9 mm — 20 000 rpm, 428.3 kW shaft, 439.5 kW AC, η_shaft 97.46 %, 439.6 A per delta phase (761.4 A per leg), m 0.943

| topology | N | devices | conduction | 3rd quadrant | switching | total | T_j | η_inv | η_wall-to-shaft | DC ripple |
|---|---|---|---|---|---|---|---|---|---|---|
| One 3-phase inverter | 5 | 6 × 5 = 30 | 2 372 W | 192 W | 2 945 W | **5 510 W** | 138 °C | **98.76 %** | **96.25 %** | 406 A rms |
| Two 3-phase inverters (series split) | 13 | 12 × 13 = 156 | 1 970 W | 320 W | 6 281 W | 8 571 W | 149 °C | 98.09 % | 95.60 % | 785 A rms |
| H-bridge per coil (unipolar) | — | — | — | — | — | no solution on this coldplate | ≥ 160 °C | — | — | — |

### What the table says

1. **One three-phase inverter wins on every count** for this machine: fewest
   devices, lowest loss, lowest DC-link ripple, highest efficiency.
2. **A second inverter costs.** On an unchanged winding (`series_split`) each
   set keeps the per-coil current, so twice the switches carry the same amps
   and the conduction loss doubles; the switching loss more than doubles
   because each device still pays the current-independent part of E_on. The
   reason to build two is redundancy, not efficiency. `power_split` — the
   winding reconnected so each inverter takes half the power at the full bus —
   is the case where the split pays, and it is a knob.
3. **H-bridge per coil is the expensive topology here.** Both legs of a bridge
   carry the full coil current, so there are 12 current-carrying legs instead
   of 3: 1.33× the conduction of the delta three-phase and ~2.4× the switching.
   At peak the default coldplate saturates — the plate's own resistance does
   not fall when you add devices — and T_case sits at 155 °C for any N. With a
   doubled plate (80 channels, 16 L/min) it solves: N = 6, 144 devices,
   9.12 kW, T_j 114 °C, η_inv 97.97 %, η_wall-to-shaft 95.48 %. Its virtue is
   not efficiency; it is six independent coils, which is what Stage 3 is for.
4. **Switching dominates at 24 kHz.** It is half the loss at rated on one
   inverter and more than half on every split topology. The PWM study already
   found +2.2 kW in the MOTOR at that carrier
   (`docs`/memory `pwm-study-l155-2026-09-14`); the inverter adds ~2.1 kW of
   its own. A carrier study is the obvious next question and this module can
   answer it in seconds.
5. **The DC link is not a detail.** 351 A rms of capacitor ripple at rated and
   406 A at peak on one inverter; the split topologies are worse because their
   legs are shifted, not cancelled.

### What is datasheet, what is correlation, what is assumption

| | |
|---|---|
| **datasheet (exact)** | V_DSS, I_DDC, T_vj max, R_DS(on) at 25/150/175 °C, E_on/E_off/E_fr at 185.2 A, V_SD at 185.2 A, C_o(er)/E_oss, R_th(j-c) |
| **datasheet (figure-read, ±8…12 %)** | the current dependence of E_on/E_off/E_fr, the temperature dependence of R_DS(on) between the table points, the I_SD(V_SD) curves |
| **application-note rule** | E_fr belongs to the commutation (§6.1), E_oss is inside a hard-switching E_on (§4.3.8.3), `E_oss = ½·C_o(er)·V²` (eq. 11) |
| **correlation** | the coldplate film — `cooling_models.pipe_nusselt`, the motor jacket's own ladder |
| **assumption** | the bus-voltage scaling exponent (1.0); linear R_G scaling; equal current sharing between parallel devices; equal loss sharing between the two switches of a leg; R_TIM 0.03 K/W; the coldplate geometry; `series_split` for two inverters; the operating point is the stored coupled record's, not re-solved |

---

## 6a · The schematic, and where the words live

The picture is generated server-side from the same `Topology` the losses were
computed on, so it cannot disagree with them. It follows the drawing the owner
asked for: DC source and `C_dc` on the left, vertical legs, **every switch a
transistor symbol with its antiparallel body diode**, standard numbering
(S1/S4, S3/S6, S5/S2 on a three-phase bridge; S1/S2, S3/S4 per H-bridge),
`×N` on each switch, and the MOTOR drawn as what it is — a closed delta
triangle with a coil on each side, or a star with its neutral and a coil on
each spoke, whichever the duty's `star_delta` says. Two 3-phase inverters get
two windings; an H-bridge gets its coil between its own two legs, with no star
and no delta. Junction = filled dot; lines crossing without a dot are not
connected. **Labels only inside the drawing.**

The tab itself carries no paragraphs (owner 2026-09-22: *«не пиши это всё,
никто это не читает»*). The model's assumptions exist in exactly three places:
this note, the duty record's `controller.assumptions` list, and the tooltip
behind the one-line "Model: …" under the results header. Every other
explanatory sentence in the tab is a label plus a HelpTip.

## 7 · Stage 2 — coupling with the electromagnetic solver

The hook exists; the consumer does not yet.

1. `routes/coupled.py` gains `drive: "inverter"` beside `"current"` and
   `"pwm"`. `_pwm_snap_excitation` takes a per-coil voltage series instead of
   synthesising an ideal one.
2. The loop becomes: controller waveform → EM transient → solved coil currents
   → controller again (the device currents changed, so the drops and the
   dead-time error changed) → iterate to a fixed point on the fundamental
   current, with the same tolerance machinery the temperature loop uses.
3. The record keeps both: what the bridge ASKED for and what the machine drew,
   exactly as the present `inverter` block does.
4. Expected size of the effect: the dead-time error at L155 rated is
   ±9 V on a 750 V link (1.2 % of the fundamental) plus ~2.2 V of device drop
   — small on the fundamental, but it is a SQUARE wave in the current sign, so
   it injects 5th and 7th harmonics that the ideal modulator does not, and
   those land in the rotor losses.
5. What must not regress: a duty solved with `drive: "pwm"` must keep giving
   byte-identical answers. `drive: "inverter"` is a third drive, not a change
   to the second.

## 8 · Stage 3 — the six-coil H-bridge study

The topology is already available and already costed (§6). What Stage 3 adds
is the reason to use it: per-coil control. With six independent bridges the
coil currents need not be a three-phase set at all — harmonic injection per
coil, fault tolerance with a coil open, and torque-ripple cancellation that a
three-wire machine cannot reach. That study needs Stage 2's per-coil interface
and nothing else from this module.

## 9 · Open items

* `Z_th(j-c)` is not published in this datasheet revision, so the model is
  steady-state only; a pulsed duty (S2/S3) cannot be judged on it yet.
* The figure-read curves should be replaced by a proper digitisation (or by
  Infineon's own simulation data) before any number from here goes to a client.
* Gate-driver losses (`Q_G · V_GS · f_sw` per device ≈ 7.5 W per device at
  24 kHz and 18/0 V) are NOT in the totals — they are the driver's supply, not
  the device's junction, and mixing them in would corrupt T_j.
* Bus-bar and capacitor losses are not modelled.
* `power_split` for two inverters is implemented but has not been checked
  against a rewound machine.
