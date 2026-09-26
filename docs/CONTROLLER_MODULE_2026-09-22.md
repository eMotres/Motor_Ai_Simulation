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

### The datasheet limits — all of them, on every solve

Owner: *«не забудь про паспортные лимиты MOSFET»*. The junction temperature
and the current rating were already refusals; the solve now carries the WHOLE
list, so a design is not "fine" merely because nobody printed the line that
would have failed. Every row has the number reached, the published limit,
where that limit comes from, and a verdict — `pass`, `fail`, `warn` or
`not_judged`. Any `fail` makes the solve `feasible: false`.

| row | judged against | note |
|---|---|---|
| Continuous current per device | I_DDC at the SOLVED case temperature | see the derating rule below |
| Peak current per device | I_DM (1433 A) | the datasheet states I_DM as limited by T_vj(max), not by a fixed pulse width; here it recurs every fundamental period, so the binding judge is the T_j row |
| Reverse peak current per device | I_SM (860 A) | through the body diode during the dead-time windows |
| Junction temperature | T_vj max (175 °C) | the hottest device of the controller |
| DC link vs V_DSS | V_DSS (1200 V) | `fail` above it; `warn` above 80 % of it — a convention of THIS module, printed as one, because nothing is then left for the commutation overshoot |
| Gate voltage | the static V_GS window (−7…23 V) | the drive actually used |
| Avalanche energy | E_AS/E_AR published | **not judged** — this model computes no avalanche event |
| dv/dt | — | **not judged** — the datasheet states a characterisation figure, not a limit |

`not_judged` is never a silent pass: the row says why.

**The current derating.** Inside the card's tabulated span (25…100 °C) the
published points answer, interpolated. Outside it a straight line is nonsense
— extrapolated it would still promise 171 A at the junction limit itself — so
the datasheet's own limiting mechanism is used instead. Table 2 states that
I_DDC is *limited by T_vj(max)* through R_th(j-c,max):

```
I_D(T_c) = sqrt( (T_j,max − T_c) / (R_th(j-c,max) · R_DS(on)@T_j,max) )
```

and it is not a guess: on this card it gives **410 A at 25 °C** against a
published 403 and **290 A at 100 °C** against a published 287 — both within
2 %. Below the coldest published point it is capped there, because the bond
wire limits and not the junction (the flat top of the I_D = f(T_c) figure).
Which branch answered is reported in the row's `source`.

**The connection is the motor's.** Star or delta comes from the duty's own
coupled record and never from this module (owner: *«соединение звезда/
треугольник у нас определяется на моторе»*). The response's `sources`
block names where it came from, the point block repeats it, and the schematic
draws it.

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

### Sized on the WHOLE limit table instead (T_j ≤ 175 °C, the datasheet)

The table above sizes to a 150 °C DESIGN TARGET. Asked instead for the
smallest N that passes every published limit — which lets T_j run to the
datasheet's own 175 °C — the answer is smaller silicon and thinner margin.
Connection delta, taken from the duty. Same device, same coldplate.

| duty | topology | N | devices | I rms/device vs I_D | I peak/device vs I_DM | T_j (margin) | V utilisation | losses | η_inv | η wall-to-shaft | limits |
|---|---|---|---|---|---|---|---|---|---|---|---|
| rated | One 3-phase inverter | 3 | 6×3 = 18 | 128.3 / 285.9 A | 257 / 1433 A | 132 °C (+43 K) | 62 % | 4 151 W | 98.50 % | 96.24 % | PASS |
| rated | H-bridge per coil | 3 | 24×3 = 72 | 74.1 / 197.1 A | 148 / 1433 A | 156 °C (+19 K) | 62 % | 8 413 W | 97.00 % | 94.78 % | PASS |
| peak | One 3-phase inverter | 4 | 6×4 = 24 | 134.6 / 233.6 A | 269 / 1433 A | 164 °C (+11 K) | 62 % | 6 852 W | 98.47 % | 95.96 % | PASS |
| peak | H-bridge per coil | 7 | 24×7 = 168 | 44.4 / 127.0 A | 89 / 1433 A | 169 °C (+6 K) | 62 % | 10 665 W | 97.63 % | 95.15 % | PASS |

Read the two tables together: **the current rating never binds** — at every
feasible point the devices run at 45…55 % of their continuous rating and at
under 20 % of I_DM. What binds is the JUNCTION TEMPERATURE, and through it the
coldplate. Sizing to the datasheet's 175 °C leaves 6 K of margin at peak on
the H-bridge, which is not a machine anyone should build; the 150 °C target in
the first table is the number to design to, and the difference between the two
is one device per switch on the standard inverter and four on the H-bridge.

The bus sits at 62 % of V_DSS on all of them — comfortable, and the reason a
1200 V part is the right class for a 750 V link.

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

## 6b · Compare — the same stacked table the other tabs have

Owner: *«не забудь Compare сделать для анализа разных вариантов, так же как на
всех других меню»*. The tab hosts `common/LocalCompareTable`, the component
the Thermal and Mechanical tabs use, with the same skin and the same row
contract (`compare/resultRows.LocalRow`): press **+ Add to comparison** and the
solve on screen becomes a column — device, topology, connection, duty, N,
switches, devices, carrier, bus, dead time, coldplate on the input side; the
loss split, T_j and its margin, the case, both efficiencies, the DC-link
ripple, the datasheet verdict, the number of failed limits and a device cost
proxy on the result side. Inputs identical in every column collapse into one
line; every result cell carries its Δ against the first column.

The row builders are pure and live in `controller/compareRows.ts` rather than
in `compare/resultRows.ts`: that module is shared by three tabs and a new tab's
builder has no business widening it, while the TYPES and the store conventions
come from it, so there is still one definition of what a stacked row is. They
are pinned by `controller/__tests__/compareRows.test.mjs`.

The **cost proxy** is devices × the card's own `price.amount`, and only where a
card carries one (it is a quotation with a source and a date, never a datasheet
value — `price` is optional and null by default). It is not a bill of
materials: no gate drivers, no busbars, no coldplate, no assembly.

## 7 · Stage 2 — coupling with the electromagnetic solver

Owner, 2026-09-22: *«как отладим каплинг с контроллером, нам не нужен будет PWM
в электромагнитном моделировании — всё будет задаваться в меню Controller»*.
So Stage 2 is not only a new drive; it is where the Controller becomes the ONE
place a PWM excitation is described, and the Simulation tab stops owning one.

1. `routes/coupled.py` gains `drive: "inverter"` beside `"current"` and
   `"pwm"`. The excitation is no longer synthesised from a handful of PWM
   fields: it comes from a CONTROLLER CONFIG — topology, device, N per switch,
   carrier, dead time, V_dc, modulation — which the module turns into per-coil
   waveforms with the device drops already in them.
   `_pwm_snap_excitation` takes that series instead of building an ideal one.
2. The loop becomes: controller waveform → EM transient → solved coil currents
   → controller again (the device currents changed, so the drops and the
   dead-time error changed) → iterate to a fixed point on the fundamental
   current, with the same tolerance machinery the temperature loop uses.
3. **The Simulation tab keeps only "Sine current" (the ideal reference) and
   "Target T/P".** Its PWM controls are retired and redirect to the Controller
   tab; a machine's carrier, bus and dead time are a property of its
   controller, and having two places to type them is how two answers for one
   duty happen.
4. **Reports name the Controller as the PWM source.** The "PWM influence"
   section keeps its numbers and its meaning; what changes is the sentence that
   says where the carrier came from.
5. **Old records stay readable.** A stored `drive: "pwm"` record keeps its
   `inverter` block and its answer, and a duty re-solved with `drive: "pwm"`
   must still give byte-identical numbers — `drive: "inverter"` is a THIRD
   drive, not a change to the second. Nothing in Stage 1 blocks any of this:
   the per-coil waveform interface already exists, the record already carries
   the full controller configuration (`controller.settings`,
   `controller.topology`, `controller.point`), and the duty store already
   keeps `pwm` and `controller` as separate kinds.
6. Expected size of the effect: the dead-time error at L155 rated is
   ±9 V on a 750 V link (1.2 % of the fundamental) plus ~2.2 V of device drop
   — small on the fundamental, but it is a SQUARE wave in the current sign, so
   it injects 5th and 7th harmonics that the ideal modulator does not, and
   those land in the rotor losses.

## 7a · Stage 2 — WHAT WAS BUILT (2026-09-22)

Owner, 13:45: *«как закончишь лимиты, запускай каплинг — сначала стандартный
инвертор на L155 motor»*.  Below is what the plan above turned into, and where
it differs from the plan it says so.

### The drive

`drive: "inverter"` is a THIRD drive of the coupled loop, beside `"current"`
and `"pwm"` (`routes/coupled.py::_DRIVE_ALIASES`, `_BRIDGE_DRIVES`).  It is not
a change to the second: a stored `drive: "pwm"` record keeps its answer, a duty
re-solved on `"pwm"` gives the same numbers, and `tests/test_coupled_pwm.py`
and `tests/test_excitation_source.py` still pin that path.  What changed is
that `"inverter"` used to be an ALIAS of `"pwm"` and now means the Controller.

### Where the non-ideality lives

| what | where |
|---|---|
| the source the solver marches | `inverter/coupling.py::InverterVoltageSource` |
| the card, reduced for a time loop | `inverter/coupling.py::fit_device_drop` |
| the per-step pole-voltage error | `inverter/coupling.py::pole_error_volts` |
| the factory | `simulation/excitation.py::make_source("inverter", …)` |
| the route's four scalars | `routes/simulation.py` — `inv_r_ds_ohm`, `inv_v_sd_v0_V`, `inv_v_sd_rd_ohm`, `inv_dead_time_us` (+ provenance) |
| the fixed point | `routes/coupled.py::_ControllerLoop` |
| tests | `tests/test_inverter_coupling.py`, `web/.../__tests__/pwmInController.test.mjs` |

`InverterVoltageSource` SUBCLASSES the ideal PWM source and overrides exactly
one method, `mean_over`.  Everything else — the compensated reference, the
exact per-step volt-second means, the settle policy, the DC-link switch
function — is the one implementation it always was.

### The two loops, and why only one of them is an iteration

**The inner one is not.** `excitation.Feedback` already carries the PREVIOUS
converged step's phase currents — the sampling delay real hardware has — so the
dead-time polarity and the device drops are decided by the current the solver
has just measured, at every FEM step.  The controller↔machine loop is therefore
closed INSIDE the transient and needs no relaxation.

**The outer one is.**  The devices' losses set a junction temperature, that
temperature moves `R_DS(on)` (≈ 0.6 %/K) and `V_SD`, and those move the
waveform.  So each coupled pass solves the machine, then solves the controller
on the current that pass drew, and the next pass reads the card at the new
`T_j`.  `T_j` is a residual of the loop exactly as the winding, the magnets and
the bearing seat are, and it is tested with them — band `CONTROLLER_TJ_TOL_K`
= 2 K, which is ~1.2 % of the channel resistance and inside the figure-read
tolerance of the curve it comes from.  It costs no extra electromagnetic run:
the controller solve is arithmetic over a card.

### What the bridge really applies

Added to the ideal pole voltage, per leg, over each step:

* **the channel** `−i·R_DS(on)/N` — independent of which switch is on, so a
  plain constant over a step;
* **the dead time** — per commanded edge, clipped to the step:

  | edge | i > 0 (leaving) | i < 0 (entering) |
  |---|---|---|
  | rising (LS→HS) | `−(V_dc + V_SD)` | `+V_SD` |
  | falling (HS→LS) | `−V_SD` | `+(V_dc + V_SD)` |

  whose sum over a carrier period is the textbook
  `ΔV = −sign(i)·t_d·f_sw·(V_dc + 2·V_SD)`.  That identity is the test, and it
  holds at 200 sub-steps per carrier AND at one step per carrier — the loss
  integral and the picture are not two opinions about one clamp.  On L155
  rated the solved run measures **8.99 V per leg** — 1.2 % of the 750.4 V
  link, and 3.8 % of that leg's own fundamental pole voltage (m·V_dc/2 =
  237.6 V) — a SQUARE wave in the sign of the leg current, so its own
  fundamental is 4/π of it.

**The delta mapping is exact, not a scaling.**  A delta machine is solved on the
star equivalent, so the model's phase voltage IS the real line-to-line voltage:

```
star    e_A = err(i_leg_A)
delta   e_A = err(i_leg_A) − err(i_leg_B)
```

with the LEG (device) current reconstructed from the solved branch currents —
`i_leg_A = n_parallel·(I_A − I_C)`, the √3, 30°-lagging line current the bridge
outside the delta really carries.  The circulating triplen stays inside the
delta and never reaches a device, which is the point of comparing with the
H-bridge later.  The drops and the dead-time clamp are computed on the REAL
`V_dc`, never on the √3 model bus (`v_bus_real` is a separate solver argument).

### The body diode, and the one thing that is a fit

`R_DS(on)` is a single card look-up.  `V_SD(i)` is a least-squares straight line
through the card's own curve over `0.1·i_peak … i_peak` of device current, and
the worst deviation over that span is a record field
(`device_drop.v_sd_fit_max_err_V`, **< 0.1 V** on this card).  The TOE is
deliberately outside the fit — the characteristic is logarithmic below the knee
(0.5 V at 1 A, 3.0 V at 25 A) and a line asked to cover zero is wrong
everywhere in exchange for being right where nothing happens.  What it costs is
an over-read of up to `v0` while the current crosses zero, i.e. `2·v0` of
`V_dc + 2·V_SD` — under one per cent of an error that is itself ~1 % of the
fundamental.  A per-sample card look-up inside a FEM time loop was the
alternative: thousands of interpolations per frame for a number whose own
tolerance is ±10 %.

### The record

The coupled record's `drive` becomes `"inverter"`; the `inverter` block keeps
its meaning unchanged (carrier, link, modulation, the settled DC, the regulator)
and a new `controller` block says which power stage applied them.  It is the
Stage 1 solve's own shape — `losses`, `thermal`, `efficiency`, `limits`,
`bridges`, `point`, `device_row`, `provenance`, `settings` — minus the waveform
arrays, plus `coupled: true`, `stage: 2`, `settings_resolved`, `sources`,
`t_j_c` / `t_j_residual_K` / `t_j_tol_K`, `passes` (one row per coupled pass:
`T_j`, its step, the inverter watts, both efficiencies, `R_DS(on)`, the
verdict), `excitation` (what the source really applied, with its
`dead_time_error_V`) and `device_drop`.  The thermal loss map carries
`drive: "inverter"` and names the device.

### Report and datasheet

`PWM_DRIVE_WORDS` already contained `"inverter"`, so the whole "PWM influence"
machinery — the sine → bridge loss table, the ripple gates, the carrier rows —
reads a Stage-2 record without a change.  What was added:

* `report.controller_record` prefers the COUPLED block over the tab's
  standalone solve where both exist: same question, and only one of them
  measured it.  `datasheet._ctrl` does the same.
* a new coupled table in the Controller section (PDF and Word):
  copper / iron / magnets / sleeve+shaft / total, torque and current, **on the
  controller's waveform against the duty's own sine reference**, then the
  inverter's watts and both efficiencies.
* two one-clause notes under it: how many passes and the junction temperature's
  last step, and what the excitation was (dead time, `R_DS(on)` at the solved
  `T_j`, the dead-time error in volts).

### The Simulation tab

Pressing "PWM inverter" no longer switches the drive: it shows ONE line — *PWM
is defined in Controller* — an "Open" button and a HelpTip.  A stored
`pwm_voltage` run still restores into the panel with its controls, so old
records stay readable and re-runnable, which is why the drive itself was left
alone.  Pinned by `web/.../__tests__/pwmInController.test.mjs`.

**Superseded 2026-09-24** (`PWM_IN_CONTROLLER_2026-09-24.md`): the PWM button,
carrier and V_bus are gone from the Simulation tab altogether; the carrier is
the Controller's saved field and every consumer resolves it through
`inverter/drive_source.py`.

**Open item:** the coupled panel has no drive selector of its own today (PWM
coupled runs have always been driven from a script), so "the coupled loop's
drive selector gets *inverter (Controller)*" has nothing to extend yet; the
drive is available on `POST /api/coupled/run` as `drive: "inverter"` with a
`controller` block.

### What is exact on the device side, and what is not

The loss model is handed the run's own **solved rms** — the switching ripple is
in it — so the CONDUCTION term is exact: it depends on `⟨i²⟩` and on nothing
else.  The third-quadrant and switching integrals still run on a SINUSOID of
that rms, so the instantaneous current each commutation switches is the
fundamental's rather than the rippled one.  On this machine the current THD is a
few per cent and the effect averages toward zero over a period, but it does not
vanish, and the record says so (`controller.model_note`).  The DC-link series
beside it is still the COMMANDED switching function's: the dead-time notch is
not in the bus current (`excitation.dc_link_note`).

### Dead time

The IMCQ120R004M2H datasheet publishes no RECOMMENDED dead time — it publishes
switching TIMES.  `_dead_time_floor_ns` builds the device's own floor from them
(AN2025-10 §7: `max t_d_off + max t_f − min t_d_on`) and reports it beside the
value used; the gate driver's propagation mismatch and the layout add to it and
neither is on the card.  The runs use **0.5 µs**, Stage 1's design value, and
the record says it is one.

---

## 7b · Stage 2, MEASURED — L155 motor / rated 1×9 mm (2026-09-22)

Owner, 13:45: *«запускай каплинг — сначала стандартный инвертор на L155 motor»*.
One three-phase bridge of **IMCQ120R004M2H**, **N = 3** per switch, **24 kHz**,
**750.4 V**, **0.5 µs** dead time, V_GS 18/0 V, micro-channel coldplate
water-glycol 50/50 at **65 °C, 8 L/min**; motor cooling the duty's own (water
60 °C, 10 L/min, bore air 30 m/s).  `drive: "inverter"`, 400 FEM steps per
electrical period = **20 per carrier**, two coupled passes, **11 397 s**
(3 h 10) on one BelowNormal process in a sandbox.  Nothing was saved into the
catalogue: a Stage-2 record is a new kind of answer about this duty and the
owner decides whether it replaces the stored one.

### The dead time is the one the closed form predicts

| | |
|---|---|
| dead-time error, solved run | **8.991 V** per leg |
| `t_d·f_sw,eff·(V_dc + 2·V_SD)` by hand | 0.5 µs × 23 666.67 Hz × (750.4 + 2×4.75) = **8.992 V** |

…and the leg current the module reconstructs from the solved BRANCH currents
(`i_leg = n_parallel·(I_A − I_C)`) is **546.60 A rms** against the solver's own
independently-computed `I_line_rms_A` of **546.5 A** — 0.02 % apart.  The delta
mapping is not argued, it is checked against the solver.

### Both fixed points close

| pass | coil °C | magnet °C | bearing °C | T_j °C | ΔT_j | V₁ branch | I branch | P_inv |
|---|---|---|---|---|---|---|---|---|
| start | 97.8 | 104.2 | 70.0 | 120.0 (seed) | — | 411.437 | — | — |
| 1 | 97.8 → 114.1 | 104.2 → 128.9 | 70.0 → 119.5 | **132.8** | +12.8 K | 411.437 | 315.50 A | 4 187.3 W |
| 2 | 114.1 → 113.6 | 128.9 → 129.1 | 119.5 → 119.8 | **130.0** | −2.8 K | 414.963 | 309.26 A | 4 014.4 W |

Temperatures settled (coil −0.5 K, magnet +0.2 K, seat +0.3 K); the junction
temperature settled to −2.8 K against a 2 K band, i.e. one more pass short.
`R_DS(on)` per device moved 6.706 → 6.560 mΩ with it, which is the loop working.

### The devices, on the machine's own run

| | |
|---|---|
| conduction / third quadrant / switching | 1 838.1 / 141.7 / 2 034.6 W |
| **total inverter loss** | **4 014.4 W** |
| junction temperature | **130.0 °C**, limit 175, margin **45 K** |
| datasheet limit table | **PASS**, every row |
| inverter efficiency | **98.48 %** |
| shaft efficiency (the machine's ONE efficiency) | **97.71 %** |
| **wall-to-shaft efficiency** | **96.22 %** |

**Against Stage 1's arithmetic on the stored point** (§6: 4 151 W, T_j 132 °C,
η_inv 98.50 %, η_wall-to-shaft 96.24 %) the coupled answer moves by **3.3 % of
the inverter loss and 0.02 pp of either efficiency**.  That is the headline
result of Stage 2 and it is worth stating plainly: **solving the devices on the
real rippled excitation changes the INVERTER very little and the MOTOR a great
deal.**  The device losses are dominated by the fundamental, which Stage 1
already had.

### The motor, on the controller's waveform against the sine

Pass 1 was solved at **coil 97.8 °C / magnet 104.2 °C — exactly the duty's sine
reference's own converged temperatures**, so this column pair is a
same-temperature measurement of what the carrier and the device cost the
machine.

| | sine reference | controller's bridge | Δ |
|---|---|---|---|
| copper | 2 259.1 W | 3 164.3 W | **+905 W (+40 %)** |
| iron | 1 447.6 W | 2 291.8 W | **+844 W (+58 %)** |
| magnets | 84.9 W | 114.1 W | +29 W |
| rotor solid | 123.6 W | 144.4 W | +21 W |
| sleeve | 9.7 W | 21.8 W | +12 W |
| shaft | 29.0 W | 8.5 W | −21 W |
| **motor loss, total** | **3 830.3 W** | **5 600.5 W** | **+1 770 W (+46 %)** |
| torque (2-D) | 187.86 N·m | 174.06 N·m | −13.8 |
| torque ripple | 1.4 % | 30.8 % | |
| current THD | 0 % | 4.97 % | |
| shaft efficiency | 98.49 % | 97.70 % | −0.79 pp |

**NOT resolution-matched**, and it matters: the sine ran at 36 steps per
electrical period against 400, and the carrier study's own rule puts about
100 W of any such difference on the step count alone.

### What the device costs at a FIXED fundamental

The cleanest single number this run produces.  Held at the duty's own seed
V₁ = 411.437 V, the machine drew **315.50 A** where it is billed at 324.51 A —
**−2.78 %**.  The dead time's own fundamental is 4/π × 8.99 = 11.4 V per leg,
which on the branch (line-to-line) is √3 × that ≈ 19.8 V; only ~3.5 V of it
shows up as a magnitude the regulator has to make back, because the error sits
along the CURRENT and the current is nearly in quadrature with the applied
voltage.  The rest moves the LOAD ANGLE — which is why the torque falls further
than the current alone explains.

### What did NOT converge, and why

The operating point: **−4.70 %** after two passes.  Between pass 1 and pass 2
the winding moved 16 K and the magnets 25 K, so the secant `_regulate_v1` fits
is dominated by the TEMPERATURE and not by the machine's dI/dV — raising V₁ by
3.5 V while the copper heated 16 K made the current FALL, and the regulator
then extrapolated the wrong way (next aim 406.3 V).  This is not something
Stage 2 introduced: the duty's stored IDEAL-PWM record is **−3.16 % off point**
for the same reason.  The fix is passes, not code — the stored PWM campaign
used 3–4.

### Record-to-record, with the caveat stated

| | ideal PWM (stored) | controller's bridge |
|---|---|---|
| coil / magnet °C | 117.6 / 133.0 | 114.1 / 128.9 |
| V₁ branch | 411.437 V | 414.963 V |
| I branch solved | 314.25 A | 309.26 A |
| point error | −3.16 % | −4.70 % |
| torque (2-D) | 179.22 N·m | 171.59 N·m |
| copper / iron | 3 257.0 / 2 414.5 W | 3 118.9 / 2 303.5 W |
| motor loss total | 5 824.1 W | 5 568.6 W |
| torque ripple / THD_I | 28.7 % / 5.23 % | 29.8 % / 5.1 % |
| DC residual | 0.067 A | 0.348 A (settled) |

The two sit at different currents AND different temperatures, so the loss
difference here is mostly those two and not the device.  **Only the fixed-V₁
current drop above is a clean measurement of what the power stage costs.**

### Not done

**Peak.**  The rated duty alone took 3 h 10 for two passes at 20 samples per
carrier, so the peak duty did not fit the session.  It needs the same run with
`--duty "peak 1x9 mm"` and, for the point to land, `--max-iter 4`.

---

## 7c · The loop on the SINE, the controller's PWM once (2026-09-25)

Owner: *«очень долго идёт каплинг с контроллером, нужно сменить алгоритм:
каплинг делается только с синусоидой, а последний прогон — с PWM из
контроллера»* and *«если уже есть каплинг с синусом — просто запускается расчёт
с PWM из контроллера»*.

`inverter_coupling: "final_pass"` is now the default for `drive: "inverter"`
(`"full"` keeps §7a's loop — every pass on the PWM — for validation):

1. the whole EM ↔ thermal (↔ mechanical) loop, the pass at the limit and the
   S1 search/verification run on the ideal SINE current;
2. ONE PWM pass on the controller's bridge at the sine state's temperatures
   and current, the fundamental seeded from the sine run's own terminal
   voltage, `T_j` seeded by a controller solve on the sine state (no EM run);
3. that pass's own loss map → one thermal re-solve; if a temperature moved by
   more than `tol_K` (or the current is outside `i_tol_pct`) ONE more PWM pass
   at the new temperatures, re-aimed — never more than two, never the loop;
4. the record is the final PWM state; `pwm_final` holds the passes, ΔT vs the
   sine state and the residual; `sine_comparison` gets the sine state as its
   reference column (same-T = PWM pass 1, own-T = the final pass).
   At the limit only the current is re-aimed (the instant's temperatures are
   the answer) and the PWM map re-reads the time to the limit (`limited.pwm`);
   at the S1 point a part the PWM losses push over its limit is reported with
   its margin and a first-order corrected current (an estimate, not re-searched).

The converged sine state is filed under a DRIVE-INDEPENDENT key
(`run_history` kind `coupled.sine_state`: machine fingerprint, point, mesh,
cooling, solve_to, max_iter, code version — everything but the drive); a later
inverter run of the same inputs skips step 1 and says so
(`pwm_final.sine_state_reused`), a near miss names the input that differed
(`sine_state_not_reused`).  The duty's saved coupled record is NOT used as a
source: it does not carry the inputs needed to prove it is the same state.

### Measured — Ø40 L12, IQE050N08NM5SC ×1, 24 kHz, 22.2 V, 0.2 µs, 96 steps/period

Solver-direct in a sandbox, mesh 2 mm, 2–3 iterations; wall times are under
heavy CPU contention (other campaigns on the same workstation) and are
indicative only — the pass counts are the robust measure.

| steady, water 40 °C 2 L/min | old (6317c41) | `full` | `final_pass` |
|---|---|---|---|
| EM passes (sine + PWM) | 0 + 3 | 0 + 3 (+1 sine ref.) | 3 + 2 |
| wall | 1 813 s | 3 860 s | 2 292 s (PWM part 1 328 s) |
| current vs 43.84 A | +5.47 % (overshoot) | +0.2 % | −3.4 % (2-pass cap) |
| coil / magnet °C | 77.1 / 110.5 | 78.0 / 108.7 | 76.9 / 108.6 |
| T_j, P_inv, η_inv | never solved (bug) | 48.2 °C, 30.3 W, 96.71 % | 47.6 °C, 28.2 W, 96.78 % |

With the sine state already in the history (a sine run of the same inputs
first), the `final_pass` run skipped the loop and made only its 2 PWM passes:
1 488 s, and the answer was identical to the run that solved its own sine loop
(42.367 A, 68.8 W, 76.9 / 108.6 °C).

| limits, air 20 m/s (winding 200 °C after 63 s) | old | `full` | `final_pass` |
|---|---|---|---|
| pass at the limit on | the IDEAL bridge | the controller | sine, then 2 PWM (current re-aimed) |
| current / torque / loss | 45.02 A / 0.624 N·m / 93.4 W | 42.23 A / 0.565 / 83.7 W | 42.53 A / 0.582 / 85.2 W |
| controller | never solved | T_j 47.6 °C, 28.0 W, residual 0.4 K | T_j 47.7 °C, 28.4 W, residual 0.4 K |

The S1 rating could not be tested on this machine: at air 20 m/s the cooling
cannot hold even the current-independent losses (sine loop's own verdict); on
the old / `full` loops the rating refuses earlier still — its steady-map lookup
is keyed on a sine current and never finds a voltage-fed run (open item).

At equal temperatures and fundamental current (`full`'s comparison pass) the
controller's waveform costs the machine +14.6 % loss (copper AC +57 %, stator
iron +47 %, magnets +38 %), torque ripple 6.3 → 34.8 %, THD_I 13.1 %, shaft
η −0.97 pp.  The sine state is +15.8 K cooler in the magnets than the
PWM-corrected state.

**Bug found on the way:** on a machine with no bearings the summary has no
`efficiency_shaft`, `_ControllerLoop._solve_request` returned `None` and the
devices were silently never solved (T_j frozen at the 120 °C start).  The
electromagnetic efficiency is used now, with a warning.

**L155 (estimate, not run):** §7b's 2 PWM passes took 3 h 10; `final_pass` is
the sine loop (36 steps/period, minutes per pass) + 1–2 PWM passes, i.e.
≈ 1 h 40 – 3 h 20 — the saving is the PWM passes the old loop spent walking
from the start temperatures (it was one pass short of T_j convergence), and all
of the sine loop when a sine state of the duty already exists.

### 7d · The first PWM command carries the bridge's drops (2026-09-26)

§7c's −3.4 % was the SEED, not the thermal: pass 1 commanded the sine state's
TERMINAL fundamental, the bridge lost its channel drop and dead time between
command and terminals, and the damped first regulator step (gain 0.3) could
not recover it inside the 2-pass cap.

* **First command, closed form** (`coupled._pwm_v1_first_guess`, placed after
  the `T_j` seed so `R_DS(on)` and `V_SD` are read at the seeded junction).
  The leg's pole error `e(i) = −i·R_DS − sign(i)·t_d·f_sw·(V_dc + 2·(v0 + r_d·|i|))`
  has, on a sinusoidal leg current of peak `Î`, the fundamental
  `E1 = Î·R_DS + (4/π)·t_d·f_sw·(V_dc + 2·v0) + 2·t_d·f_sw·r_d·Î`, in phase
  with the current and opposing it (in delta `√3·E1` at `Î_leg = √3·Î_branch`,
  along the branch current).  The load angle is held, so the command is the
  projection `V_cmd = V1 + E1·cos φ`, `φ = δ_V − γ_I` (below `V1` on a
  generator).  The ceiling clamp applies; the record carries every term
  (`pwm_final.v1_first_guess`).  Left out, and said: dead-time windows clipped
  near the rails, the channel drop absent during the dead time, and the
  harmonic currents the drops drive — the regulator takes that residual.
* **Third pass for the current only**: after the 2-pass cap, ONE more PWM
  pass when `|point_error_pct| > i_tol_pct`; a temperature residual alone
  never buys it (`pwm_final.current_extra_pass`, `final_point_error_pct`).

Mocked affine machine (`tests/test_coupled_pwm_first_guess.py`; Ø40 L12-like
bridge, the true drop 10 % above the closed form, `dI/I = 1.5·dV/V`, +5 K
from the PWM map on pass 1):

| seed / cap | PWM passes | current error per pass |
|---|---|---|
| terminal V1, 2-pass cap (before) | 2 | −7.19 %, −3.95 % |
| terminal V1, + current pass | 3 | −7.19 %, −3.95 %, +0.29 % |
| closed form (now) | 2 | −0.65 %, −0.90 % (pass 2 for the +5 K) |

Not yet re-measured on the FEM machine.

---

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

---

## 10 · MOTRES SiC concept doc — the whole 1200 V/750 V Q-DPAK lineup (2026-09-23)

Owner, 2026-09-23 08:10: *«внеси все MOSFET, которые есть в документе, в
нашу базу и проверь, как они работают»*, source document: MOTRES's
"800 V / 400 V SiC Inverter Platform — Technical Concept". Nine new device
cards were added to `config/devices/`, each transcribed from its own
Infineon datasheet (PDF fetched live, not from the concept doc's summary
numbers) with the same `basis: table`/`basis: figure` provenance convention
as `IMCQ120R004M2H.yaml`:

| part | V_DSS | R_DS(on) 25 C | package | notes |
|---|---|---|---|---|
| IMCQ120R005M2H | 1200 V | 5 mOhm | Q-DPAK (PG-HDSOP-22-U03) | rev 1.10, 2025-10-13 |
| IMCQ120R007M2H | 1200 V | 7.5 mOhm | Q-DPAK (PG-HDSOP-22-U03) | rev 1.20, 2026-07-27 |
| IMCQ120R010M2H | 1200 V | 10 mOhm | Q-DPAK (PG-HDSOP-22-U03) | rev 1.20 |
| IMCQ120R017M2H | 1200 V | 17.1 mOhm | Q-DPAK (PG-HDSOP-22-U03) | rev 1.10; the ONE part where -5 V turn-off is NOT the default (E_tot higher at -5 V than at 0 V — confirmed: 1170 uJ vs 1373 uJ at T_j=175 C, I_D=40 A) |
| IMCQ120R034M2H | 1200 V | 34 mOhm | Q-DPAK (PG-HDSOP-22-U03) | rev 1.10; DigiKey price $6.30@100 / $5.15@750 carried in the card |
| IMCQ120R078M2H | 1200 V | 78.1 mOhm | Q-DPAK (PG-HDSOP-22-U03) | rev 1.10; too small a die for this platform's currents — see below |
| IMDQ75R004M2H | 750 V | 3.5 mOhm | Q-DPAK (PG-HDSOP-22-U01) | rev 2.1, 2025-06-05; **DUAL-CHIP, datasheet's own cover page says "not recommended for high frequency (kHz) switching applications"** |
| IMDQ75R007M2H | 750 V | 6.8 mOhm | Q-DPAK (PG-HDSOP-22-U01) | rev 2.1; datasheet carries NO such warning (see the card's `notes` — the concept doc's claim that it is "positioned the same way" as R004M2H is flagged, not inherited) |
| AIMDQ75R016M2H | 750 V | 16 mOhm | Q-DPAK (PG-HDSOP-22-U01) | rev 2.1; AEC-Q101 automotive-qualified; the EVAL-QDPAK-FB V2.1 reference-design device (4x, full bridge) |

Also added: a DigiKey `price` block on the pre-existing `IMCQ120R004M2H.yaml`
($43.25 @750 pcs, the concept doc's other hard price anchor).

**Package.** All nine share one footprint family (`packages.py`'s `q-dpak`
alias already matches both `PG-HDSOP-22-U03` and `-U01` via the
`hdsop[-_ ]*22` regex) — confirmed against each part's own Figure 1, not
assumed: the 1200 V family's outline is bit-identical to IMCQ120R004M2H's
card (D 14.90-15.10 / E 20.81-21.11 / A 2.25-2.35 mm); the 750 V family's
U01 outline differs only in the overall envelope (H 20.86-21.06 vs
21.11 mm) and was verified on all three cards' own Figure 1 pages.

**Switching-energy curves.** The 1200 V family's datasheets publish an
E=f(I_D) figure at T_j=175 C (both V_GS(off)=0 and -5 V) the same way
IMCQ120R004M2H's does; for time reasons this batch of cards digitizes it
SPARSELY (3 points: low current, the table's own test-current anchor,
figure's right edge) with a wider `tolerance_pct` (15-18 % vs R004M2H's
8-12 %) — stated once here and in every new card's `switching.source`, not
silently narrowed. The three 750 V-class parts publish only ONE test point
each (no current- or temperature-dependence figure at all) — their cards'
`switching.curves` hold that single anchor and do NOT extrapolate across
current or temperature; this is stated in each card, not defaulted
silently. Every `third_quadrant.curves` on the new cards is likewise a
single Table-6/Table-8 anchor plus the physical origin (no I_SD=f(V_SD)
figure digitized) — a scope decision for this batch, named in each card.

### 10.1 · How they perform — Controller solve, L155 rated + peak

Sandbox run (`solve_controller()` called directly, in-process — no live
API, no config write), topology `one_3ph`, cooling `liquid` (water-glycol,
65 C inlet, 8 L/min, module-default R_TIM=0.03 K/W, R_spread=0), dead time
0.5 us, V_GS(on)=18 V; V_GS(off) = -5 V for the 4/5/7/10/34/78 mOhm parts,
0 V for the 17 mOhm part and all three 750 V-class parts (the doc's own
rule, confirmed against each datasheet's own `switching`/`gate` block).
Operating points from `config/.duty_results.json`,
`CIANO10 200 opt / L155 motor`, delta, 12 slots / 10 poles:

| duty | I_phase rms | rpm | m (@750.4 V) | power factor | f_elec |
|---|---|---|---|---|---|
| rated | 314.25 A | 14 200 | 0.6333 | 0.9926 | 1183.3 Hz |
| peak | 439.61 A | 20 000 | 0.9427 | 0.7692 | 1666.7 Hz |

`N` = devices in parallel per switch position, chosen as the smallest N
(scanned 1..24) for which the model's own T_j stays <= 150 C (the doc's
design limit) at BOTH points; 24 kHz carrier throughout (the stored L155
records' own carrier). Owner's follow-up (2026-09-23 08:30): evaluate every
card on BOTH lines — the 800 V line at the real 750.4 V bus AND the 400 V
line at the doc's own 375 V bus (not 400) — same L155 load current on both
(a device comparison, not a claim that L155 runs at 375 V).

**800 V line (750.4 V bus):**

| part | R_ds(on) 25/175 C mOhm | N | devices | rated: I/device (util. vs I_D@100C), T_j, cond/sw, eta_inv, verdict | peak: same |
|---|---|---|---|---|---|
| IMCQ120R004M2H | 3.7/8.9 | 5 | 30 | 108 A (37%) / T_j=100 C / cond=942 W / sw=1608 W / eta=99.02% / pass | 152 A (53%) / T_j=130 C / cond=2219 W / sw=2413 W / eta=98.91% / pass |
| IMCQ120R005M2H | 5.0/11.9 | 6 | 36 | 90 A (37%) / T_j=106 C / cond=1093 W / sw=1959 W / eta=98.85% / pass | 126 A (52%) / T_j=141 C / cond=2713 W / sw=3005 W / eta=98.67% / pass |
| IMCQ120R007M2H | 7.5/17.7 | 7 | 42 | 77 A (42%) / T_j=98 C / cond=1373 W / sw=1074 W / eta=99.06% / pass | 108 A (60%) / T_j=133 C / cond=3332 W / sw=1743 W / eta=98.80% / pass |
| IMCQ120R010M2H | 10.0/23.7 | 8 | 48 | 68 A (49%) / T_j=103 C / cond=1790 W / sw=973 W / eta=98.94% / pass | 95 A (68%) / T_j=146 C / cond=4257 W / sw=1609 W / eta=98.62% / pass |
| IMCQ120R017M2H | 17.1/40.6 | 13 | 78 | 41 A (49%) / T_j=100 C / cond=1861 W / sw=768 W / eta=98.98% / pass | 58 A (69%) / T_j=141 C / cond=4396 W / sw=1258 W / eta=98.66% / pass |
| IMCQ120R034M2H | 34.0/80.4 | 23 | 138 | 23 A (52%) / T_j=103 C / cond=2107 W / sw=708 W / eta=98.91% / pass | 33 A (73%) / T_j=150 C / cond=5110 W / sw=1315 W / eta=98.48% / pass |
| IMCQ120R078M2H | 78.1/184.8 | 24* | 144 | 22 A (103%) / T_j=182 C / cond=6679 W / sw=676 W / eta=97.23% / **fail** | 31 A (144%) / T_j=288 C / cond=13071 W / sw=936 W / eta=96.74% / **fail** |

`*` R078M2H: no N up to 24 reaches T_j <= 150 C — table shows the N=24 cap, both points FAIL.

**400 V line (375 V bus, same L155 load -- device comparison only, per the owner's 2026-09-23 08:30 clarification):**

| part | R_ds(on) 25/175 C mOhm | N | devices | rated: I/device (util.), T_j, cond/sw, eta_inv, verdict | peak: same |
|---|---|---|---|---|---|
| IMCQ120R004M2H | 3.7/8.9 | 4 | 24 | 136 A (47%) / T_j=95 C / cond=1139 W / sw=783 W / eta=99.24% / pass | 190 A (66%) / T_j=124 C / cond=2659 W / sw=1214 W / eta=99.07% / pass |
| IMCQ120R005M2H | 5.0/11.9 | 5 | 30 | 108 A (44%) / T_j=96 C / cond=1240 W / sw=897 W / eta=99.18% / pass | 152 A (62%) / T_j=126 C / cond=2908 W / sw=1326 W / eta=98.99% / pass |
| IMCQ120R007M2H | 7.5/17.7 | 6 | 36 | 90 A (50%) / T_j=95 C / cond=1581 W / sw=533 W / eta=99.17% / pass | 126 A (70%) / T_j=133 C / cond=3883 W / sw=883 W / eta=98.86% / pass |
| IMCQ120R010M2H | 10.0/23.7 | 7 | 42 | 77 A (56%) / T_j=102 C / cond=2031 W / sw=480 W / eta=99.02% / pass | 108 A (78%) / T_j=148 C / cond=4911 W / sw=810 W / eta=98.64% / pass |
| IMCQ120R017M2H | 17.1/40.6 | 12 | 72 | 45 A (53%) / T_j=98 C / cond=1992 W / sw=378 W / eta=99.07% / pass | 63 A (75%) / T_j=139 C / cond=4728 W / sw=624 W / eta=98.72% / pass |
| IMCQ120R034M2H | 34.0/80.4 | 22 | 132 | 24 A (54%) / T_j=99 C / cond=2167 W / sw=351 W / eta=99.02% / pass | 34 A (76%) / T_j=144 C / cond=5216 W / sw=645 W / eta=98.60% / pass |
| IMCQ120R078M2H | 78.1/184.8 | 24* | 144 | 22 A (103%) / T_j=177 C / cond=6679 W / sw=338 W / eta=97.35% / **fail** | 31 A (144%) / T_j=281 C / cond=13071 W / sw=467 W / eta=96.84% / **fail** |
| IMDQ75R004M2H | 3.5/6.5 | 3 | 18 | 181 A (64%) / T_j=96 C / cond=1374 W / sw=398 W / eta=99.30% / pass | 253 A (89%) / T_j=124 C / cond=2970 W / sw=398 W / eta=99.17% / pass |
| IMDQ75R007M2H | 6.8/12.2 | 5 | 30 | 108 A (68%) / T_j=96 C / cond=1574 W / sw=208 W / eta=99.30% / pass | 152 A (95%) / T_j=129 C / cond=3433 W / sw=208 W / eta=99.12% / pass |
| AIMDQ75R016M2H | 16.0/29.0 | 10 | 60 | 54 A (74%) / T_j=98 C / cond=1844 W / sw=99 W / eta=99.23% / pass | 76 A (104%) / T_j=136 C / cond=4068 W / sw=99 W / eta=98.99% / pass |

Wall-to-shaft efficiency was not computed (`efficiency.shaft` is `null` for
this sandbox run — no motor-side efficiency was piped in, per
`solve_controller`'s own "no shaft efficiency is known for this point"
note); `eta_inv` (inverter-only) is reported instead in every cell above.
`limits` column = the card's own datasheet-derived continuous-current limit
verdict (`pass`/`fail`) at the solved case temperature, not a separate
judgement.

`IMCQ120R078M2H` cannot reach T_j <= 150 C at 24 devices on either bus at
this load (Tj = 182/288 C on the 800 V line) — the 78 mOhm die is simply
too small for the L155 platform's current; flagged `fail`, not forced.
Silicon-cost column is populated only where the concept doc (or DigiKey)
gives a price — `IMCQ120R004M2H` ($43.25@750) and `IMCQ120R034M2H`
($6.30@100/$5.15@750) — every other part's price is `null` in its card, per
the "price is a quotation, never invented" rule.

### 10.2 · Cross-checking the document's own claims

**(a) 179 kW continuous, 6 x IMCQ120R004M2H, N=1, 800 V line.** The doc's
own formula (`T_j = T_coolant + P_cond+P_sw*(R_ch)`) was reproduced directly
— `r_tim_k_w` set to the doc's R_ch (0.2 / 0.15 K/W), coldplate flow cranked
high so the model's own (separate, geometry-derived) shared channel term is
negligible, `cos phi = 0.9`, `m = 0.95` (this model's own linear-modulation
ceiling minus a 5 % margin — see the caveat below), star-connected so the
model's `i_phase_rms_A` input IS the leg/device current (matching the doc's
own "Phase current, A RMS" column, rather than L155's own delta winding):

| R_ch | f_sw | I_phase (model) | I_phase (doc) | P_continuous (model) | P (doc) | ratio |
|---|---|---|---|---|---|---|
| 0.20 K/W | 16 kHz | 218.8 A | 227 A | 148.8 kW | 179 kW | 0.83 |
| 0.20 K/W | 24 kHz | 198.9 A | 208 A | 135.3 kW | 163 kW | 0.83 |
| 0.15 K/W | 16 kHz | 242.3 A | -- | 164.8 kW | 199 kW | 0.83 |
| 0.15 K/W | 24 kHz | 221.8 A | -- | 150.8 kW | 183 kW | 0.82 |

**Current agrees to within 4 %** at both R_ch values — the thermal/loss
model itself (conduction + switching, R_th(j-c) + R_ch chain) checks out
against the doc closely. **Power under-reads by a consistent ~17-20 %**,
and multiplying by 2/sqrt(3) = 1.1547 (the classic SVPWM third-harmonic
injection boost over plain sine-PWM) closes MOST of the gap (179 -> 171.8,
163 -> 156.2, within ~4-5 %): this Controller module's `m` appears to
implement a plain sine-triangle modulator capped at `m<=1`
(`solve_controller` flags `m>1` as "OVERMODULATED"), not the extended-range
SVPWM the doc assumes ("SVPWM, 5 % margin to the linear limit" implies the
doc's own m allows ~15 % more fundamental voltage at the same DC bus). This
is a MODEL LIMITATION worth a follow-up (the module has no SVPWM/third-
harmonic-injection modulator yet), not a device-card error — the current
figure, which depends only on the loss/thermal chain and not on the
modulation scheme, matches the doc closely.

**(b) ~100 kW continuous, 400-100 line (same die, 375 V bus).** Same method,
375 V bus:

| R_ch | f_sw | I_phase (model) | P_continuous (model) | P (doc) |
|---|---|---|---|---|
| 0.20 K/W | 16 kHz | 240.0 A | 81.6 kW | 100 kW |
| 0.20 K/W | 24 kHz | 227.9 A | 77.5 kW | 97 kW |
| 0.15 K/W | 16 kHz | 264.0 A | 89.8 kW | 110 kW |
| 0.15 K/W | 24 kHz | 251.6 A | 85.6 kW | -- |

Same pattern: model power under-reads by the same ~15-20 %, closing to
within ~5 % once the SVPWM factor above is applied (81.6*1.1547=94.2 vs
100 kW). Consistent with (a) — one modulation-scheme gap, not two separate
disagreements.

**(c) IMDQ75R004M2H E_off = 2044 uJ at 500 V / 262 A.** Transcribed
DIRECTLY from this card's own datasheet, Table 6 (Dynamic characteristics):
"Turn-OFF switching losses, E_off, typ = 2044 uJ, V_DD=500V, V_GS=0/18V,
I_D=262.4A, R_G,ext=1.8 ohm, L_stray=15nH" — **exact match** to the concept
doc's citation. E_on at the same point is 416 uJ (doc: "416 uJ"), also an
exact match. The datasheet's cover page independently confirms the "dual
chip product, not recommended for high frequency (kHz) switching
applications" note the doc paraphrases (see `IMDQ75R004M2H.yaml`'s
`notes:` field for the verbatim text).

### 10.3 · What was NOT done

* No current- or temperature-dependence figure was digitized for the three
  750 V-class parts (single-point cards, stated above and in each card).
* The 1200 V family's switching curves are a sparser 3-point read than
  IMCQ120R004M2H's own dense digitization — a scope decision for nine cards
  in one sitting, not a claim of equal precision.
* No I_SD=f(V_SD) body-diode figure was digitized for any of the nine new
  cards (Table-6/8 anchor only).
* The model's plain sine-PWM modulator (no SVPWM/third-harmonic injection)
  is the standing gap behind the ~17-20 % power under-read in section 10.2;
  fixing it is a Controller-module change, out of this batch's scope
  (device cards + read-only cross-checks only, "не подстраивай ничего под
  документ").
