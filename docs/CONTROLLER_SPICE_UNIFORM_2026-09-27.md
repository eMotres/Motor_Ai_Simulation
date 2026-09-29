# The Controller on the vendor SPICE models, uniformly (2026-09-27)

Owner, 2026-09-27: «Перевести L155 на SPICE — все моторы должны работать на
SPICE одинаково.» Every device whose vendor model runs in ngspice is now
solved on that model, the same way for every motor: switching energies,
conduction and the dead-time (third-quadrant) drop all come from the same
vendor model. The datasheet path is only an explicit, labelled fallback for
devices whose model cannot run here. Background and the harness:
`CONTROLLER_SPICE_2026-09-23.md` (§ numbers below refer to it) and
`CONTROLLER_MODULE_2026-09-22.md`.

Simulator: ngspice-46, the shared library shipped with KiCad 10
(`C:\Program Files\KiCad\10.0\bin\ngspice.dll`), `ngbehavior=psa`. Nothing
was installed.

---

## 1 · What was generated

`scripts/spice_build_all_tables.py` runs one grid per part (the datasheet's
Fig. F double pulse, the datasheet's own integration windows, §4 of the
09-23 doc), at most 8 ngspice processes at a time (below-normal priority),
and writes two blocks at the end of each card, between the generator's
marker comments:

* `switching_table`: rows `[V_dc, T_j, I_off, E_off, I_on, E_on, E_fr]` per
  driver/layout set, plus `l_sigma_default_nH` (the datasheet test circuit's
  loop), `failed_points` and `validation` (the model-vs-datasheet verdict,
  §4 below);
* `static_table`: DC sweeps, forward V_DS(I, T_j) at V_GS(on) and
  third-quadrant V_SD(I, T_j) at V_GS(off), each read at the Kelvin pin and
  at the power source pin;
* `switching_source: spice`.

Provenance in each block: library file, sha256 (checked on every use),
subcircuit, simulator path, circuit, windows, date.

| part | V_dc [V] | T_j [°C] | I [A] | driver sets (R_G,on = R_G,off, L_σ, V_GS) | rows | failed points | static |
|---|---|---|---|---|---|---|---|
| IMCQ120R004M2H | 375 / 750.4 | 25 / 125 / 175 | 20 / 60 / 120 / 185.2 / 270 | 2.3 & 10 Ω, 15 nH; + 4.7 Ω 10 / 20 nH (750.4 V) | 90 | 0 | 80 fwd + 160 3Q |
| IMCQ120R005M2H | 375 / 750.4 | 25 / 125 / 175 | 0.12…1.5 × 138.2 A | 2.3 & 10 Ω, 15 nH, 0/18 V | 48 | 0 | 80 + 160 |
| IMCQ120R007M2H | 375 / 750.4 | 25 / 125 / 175 | 0.12…1.5 × 93 A | 2.3 & 10 Ω, 15 nH, 0/18 V | 48 | 0 | 80 + 160 |
| IMCQ120R010M2H | 375 / 750.4 | 25 / 125 / 175 | 0.12…1.5 × 69 A | 2.3 & 10 Ω, 15 nH, 0/18 V | 48 | 0 | 80 + 160 |
| IMCQ120R017M2H | 375 / 750.4 | 25 / 125 / 175 | 0.12…1.5 × 40 A | 2.3 & 10 Ω, 15 nH, 0/18 V | 48 | 0 | 80 + 160 |
| IMCQ120R034M2H | 375 / 750.4 | 25 / 125 / 175 | 0.12…1.5 × 20.4 A | 2.3 & 10 Ω, 15 nH, 0/18 V | 47 | 1 | 80 + 160 |
| IMCQ120R078M2H | 375 / 750.4 | 25 / 125 / 175 | 0.12…1.5 × 8.9 A | 2.3 & 10 Ω, 15 nH, 0/18 V | 48 | 0 | 80 + 160 |
| IQE050N08NM5SC | 22.2 / 44.4 | 25 / 75 / 125 / 175 | 5…60 | 1.6 & 4.7 Ω, 2 nH; + 1.6 Ω 5 nH (22.2 V) | 98 | 8 | 80 + 80 |

**Grid rules, the same for every part.** CoolSiC 1200 V: V_dc 375 and
750.4 V. 800 V and anything above 750.4 V comes from the power law through
those two buses and is flagged; 800 V was dropped from the grid to keep the
run inside the owner's absence. I = 0.12 / 0.5 / 1.0 / 1.5 × the datasheet
test current (R004: the 09-23 currents 20…270 A). R_G,ext = 2.3 Ω (the
datasheet's) and 10 Ω, L_σ 15 nH (Table 4), V_GS 0/18 V. R004 also keeps the
09-23 sets at 4.7 Ω / 10 and 20 nH (750.4 V). OptiMOS IQE050N08NM5SC:
22.2 and 44.4 V, T_j 25/75/125/175, I 5…60 A, R_G 1.6 and 4.7 Ω, L_σ 2 nH
(an assumption; the datasheet names none), and the 09-23 5 nH set.
Static tables: T_j 25/75/125/150/175, V_GS(on) 18 V (10 V OptiMOS),
V_GS(off) 0 and −5 V (0 V OptiMOS), up to 1.6 × the test current (80 A
OptiMOS).

**Failed points.** A failed point is logged and left out. The table
interpolates on the currents that did finish. CoolSiC: 1 of 385 (IMCQ120R034M2H). OptiMOS: 8 of 106, all 'timestep too small' at a turn-off (e.g. 22.2 V / 25 °C / 44 and 60 A).

**How the table is read** (`spice/table.py`):

* **Current:** piecewise-linear in the current each run actually switched.
* **T_j:** linear, clamped to the tabulated span.
* **V_dc:** a power law through the two nearest buses. A straight line
  would over-read between 375 and 750 V, because the model's energies go
  as ≈V^1.5…1.9.
* **R_G:** linear between the simulated resistances. E_on and E_fr follow
  R_G,on; E_off follows R_G,off. This matches the datasheet figure
  "E = f(R_G,ext)", which is a straight line. Outside the simulated span the
  line is extrapolated and flagged.
* **L_σ / V_GS:** the nearest simulated set, named in the notes.

---

## 2 · Defaults: SPICE wherever a table exists

`DeviceCard.switching_source_default()` returns `spice` for every card
with a `switching_table`. A card may still pin `switching_source: datasheet`.
A card whose `switching_source: spice` has no table falls back to datasheet
and says so.

`losses._leg_losses` on the SPICE basis:

* conduction = (1 − f_dt) · N · ⟨i_dev · V_DS,SPICE(i_dev, T_j)⟩ at the
  power pin;
* third quadrant = V_SD,SPICE at the power pin, gate at V_GS(off);
* switching = the table.

The response carries:

* `losses.conduction_source` (`spice_static` | `datasheet`);
* `losses.basis_label`, e.g. "SPICE (IMCQ120R004M2H vendor model):
  switching, conduction, dead time", or "datasheet FALLBACK — no runnable
  vendor model (LTspice … is required)";
* `losses.spice_deviation_line` and `losses.spice_validation`;
* a warning line when the model deviates by more than 15 % (§4).

`solve_controller`, the coupled loop and the report all read the same
`card.switching_source_default()`, so every motor gets the same basis. The
route's history key now carries `loss_basis_rev`, so no result from before
this change can be served for a new solve.

| device | default basis | why |
|---|---|---|
| IMCQ120R004/005/007/010/017/034/078M2H | **SPICE** (switching + conduction + dead time) | vendor model runs (DDT translation, §3 of 09-23) |
| IQE050N08NM5SC | **SPICE** (switching + conduction + dead time) | vendor L3 model runs |
| IMDQ75R004M2H, IMDQ75R007M2H, AIMDQ75R016M2H | **datasheet — labelled fallback** | vendor libraries are **encrypted** (LTspice / PSpice / SIMetrix only). **LTspice (free, Analog Devices) with the vendor's `_LTSpice.lib` is required** to tabulate them. It was not installed. The harness `.cir` would run there by hand. |

---

## 3 · The datasheet fallback, corrected

`devices._e_switch_from_curves` (used only by the three 750 V parts, and by
anyone who picks "datasheet" on purpose):

* **R_G:** the rule is now a line through the datasheet point with the
  datasheet figure's slope,
  `E(R) = E(R_ref) · (1 + s · (R − R_ref))`. The card fields are
  `scaling.r_g_slope_rel_on_per_ohm` / `_off_per_ohm`. For IMCQ120R004M2H
  they come from its own figure (p. 12): s_on 0.148/Ω, s_off 0.215/Ω. The
  other IMCQ parts use the same relative slope, stated on each card as an
  assumption because their figures are not digitised. A card without a
  slope (the 750 V parts publish no R_G figure) is **not rescaled**, and
  the note says so. The old proportional rule (a line through the origin)
  was ×1.5 at 4.7 Ω and ×2.4 at 20 Ω.
* **Bus voltage:** `voltage_exponent` 1.8 for E_on/E_off and
  `voltage_exponent_fr` 1.0 for E_fr, from the E = f(V_DD) figure
  (p. 13, 600…1000 V). IMCQ120R004M2H's own figure is used for it and for
  the rest of the family. The 750 V parts use it as a stated assumption.
  The old ^1.0 read +25 % at 600 V and roughly ×1.8 at 375 V.

Check against IMCQ120R004M2H's own figures (175 °C, 185.2 A;
`tests/test_spice_harness.py::test_datasheet_fallback_rg_line_and_bus_exponent`):

| point | E_on figure / fallback now / old rule µJ | E_off figure / now / old |
|---|---|---|
| 4.7 Ω, 800 V | 6700 / 6668 / 10054 | 7200 / 7246 / 9768 |
| 10 Ω, 800 V | 10500 / 10527 / 21391 | 12800 / 12693 / 20783 |
| 20 Ω, 800 V | 17800 / 17808 / 42783 | 23000 / 22970 / 41565 |
| 2.3 Ω, 600 V | 2950 / 2931 / 3690 | 2850 / 2848 / 3585 |
| 2.3 Ω, 1000 V | 7050 / 7352 / 6150 | 7500 / 7143 / 5975 |

---

## 4 · Models that deviate from their datasheet — used, and said

The owner's rule: the vendor model is **used as is** (never tuned), and a
one-line "model deviates from datasheet by X %" is shown on the device card
(Controller tab, under the basis selector), on the results card, and in the
report's controller source line. X is the worst signed deviation over E_on,
E_off and E_tot at the datasheet's own test points
(`spice_validate_devices.py`, 09-23 §5.2). The limit is 15 %.

| part | worst deviation | where | shown? |
|---|---|---|---|
| IMCQ120R004M2H | −17 % | E_on, 25 °C (175 °C: within 13 %) | yes |
| IMCQ120R005M2H | −16 % | E_tot, 175 °C | yes |
| IMCQ120R007M2H | +22 % | E_on, 25 °C | yes |
| IMCQ120R010M2H | +11 % | E_on, 25 °C | no (within 15 %) |
| IMCQ120R017M2H | −37 % | E_off, 175 °C | yes |
| IMCQ120R034M2H | −53 % | E_off, 175 °C | yes |
| IMCQ120R078M2H | −31 % | E_off, 175 °C | yes |
| IQE050N08NM5SC | — | datasheet publishes no energies | yes ("unvalidated") |

---

## 5 · The 0.95 mΩ Kelvin-to-power-source path

On the SPICE basis conduction is read at the **power source pin**, so the
vendor model's Kelvin-to-power-source path is included automatically. It
was neither added nor removed by hand.

* **Where it sits in the model:** every IMCQ part's L1/L3 wrapper instances
  the same package subcircuit. The path is therefore a **package**
  property: the same ≈0.95 mΩ (±0.01) on all seven Q-DPAK parts,
  independent of the die (R017: 16.71 mΩ at the pin vs 15.76 mΩ Kelvin).
* **Is it physical?** The datasheet specifies R_DS(on) Kelvin
  (drain to source-sense) and publishes **no** power-pin or package source
  resistance. It cannot be confirmed or refuted from the datasheet. A
  Q-DPAK's source leads and bond path are physically in series with the
  load current and not with the Kelvin sense, so a sub-milliohm path is
  plausible for this package. The number itself exists only in Infineon's
  model.
* **Effect on L155:** SPICE uniform 4 308 W vs the same solve with conduction read at the Kelvin pin 3 928 W: **+380 W on the bridge (+9.7 %), T_j +6 K** (conduction 2 257 vs 1 906 W). On the datasheet basis it is absent.

---

## 6 · L155 and Ø40 L12, datasheet vs SPICE (solver-direct)

`scripts/spice_uniform_compare.py` (in-process `losses.solve_controller`;
no API, no config write). Points as in 09-23 §6:

* L155: IMCQ120R004M2H × 3 per switch, one_3ph, delta 314.3 A, 272.2 kW,
  m 0.6333, 750.4 V, 24 kHz, 0.5 µs, V_GS 18/0 V, water-glycol coldplate
  8 L/min 65 °C, η_shaft 97.71 %.
* Ø40 L12: IQE050N08NM5SC × 2, 22.2 V, 43.8 A star, 48 kHz, 10/0 V,
  forced air 10 m/s 35 °C, stated pf 0.9.

"Reference" rows isolate one ingredient each and are not a basis.

| machine | case | conduction W | dead time W | switching W (on/off/fr) | total W | T_j °C | η_inv | η wall-to-shaft | R_DS eff mΩ | feasible |
|---|---|---|---|---|---|---|---|---|---|---|
| L155 | datasheet (corrected fallback), R_G 2.3 ohm | 1900.4 | 144.7 | 1986.9 (797.7/753.9/435.3) | **4032.1** | 130.3 | 98.54 % | 96.28 % | 6.57 | yes |
| L155 | SPICE uniform, R_G 2.3/2.3 ohm, L_sigma 15 nH | 2256.5 | 152.3 | 1899.4 (755.4/863.7/280.2) | **4308.2** | 134.7 | 98.44 % | 96.19 % | 7.80 | yes |
| L155 | SPICE switching + datasheet conduction (reference) | 1864.3 | 144.7 | 1873.4 (747.6/859.2/266.7) | **3882.5** | 127.9 | 98.59 % | 96.34 % | 6.45 | yes |
| L155 | SPICE uniform, conduction at the Kelvin pin (reference) | 1905.6 | 145.9 | 1876.1 (748.2/859.8/268.2) | **3927.6** | 128.6 | 98.58 % | 96.32 % | 6.59 | yes |
| L155 | datasheet (corrected fallback), R_G 4.7 ohm | 2147.2 | 144.7 | 2753.9 (1110.3/1165.2/478.2) | **5045.8** | 146.7 | 98.18 % | 95.93 % | 7.42 | yes |
| L155 | SPICE uniform, R_G 4.7/4.7 ohm, L_sigma 15 nH | 2457.3 | 151.6 | 2686.0 (1026.3/1374.3/285.3) | **5295.0** | 150.7 | 98.09 % | 95.85 % | 8.49 | yes |
| L12 | datasheet (times-and-charges fallback), R_G 1.6 ohm | 13.1 | 5.7 | 0.4 (0.3/0.3/0.0) | **19.3** | 45.5 | 97.74 % | — | 4.79 | yes |
| L12 | SPICE uniform, R_G 1.6/1.6 ohm, L_sigma 2 nH | 13.1 | 4.6 | 1.5 (0.3/0.3/0.9) | **19.2** | 45.4 | 97.76 % | — | 4.76 | yes |
| L12 | SPICE switching + datasheet conduction (reference) | 13.2 | 5.7 | 1.5 (0.3/0.3/0.9) | **20.4** | 46.1 | 97.61 % | — | 4.81 | yes |

What it says:

* **L155 at the datasheet driver (2.3 Ω):**
  * SPICE uniform gives 4 308 W / 134.7 °C / η_inv 98.44 %.
  * The corrected datasheet path gives 4 032 W / 130.3 °C / 98.54 %.
  * On switching, SPICE is 4 % lower (1 899 vs 1 987 W).
  * SPICE's higher total is conduction. The model's Kelvin R_DS(on) is
    about 2 % below the datasheet's at temperature. The package source path
    adds about 0.95 mΩ per device, which is +380 W over the bridge (§5).
* **At 4.7 Ω:**
  * The corrected fallback gives 5 046 W against SPICE's 5 295 W.
  * The old proportional rule gave 6 837 W and an infeasible T_j (09-23 §6.1).
  * Feasible on both bases, T_j 147…151 °C.
* **Ø40 L12:** 19.2 W on SPICE vs 19.3 W on the times-and-charges fallback.
  * SPICE switching is ×3.7 (1.5 vs 0.4 W), almost all body-diode recovery.
  * The SPICE dead-time drop is lower (4.6 vs 5.7 W): the model's V_SD
    is below the card curve.
  * Negligible on this machine.

---

## 7 · Web (cloud task 10, done here)

Controller tab, driver block:

* **Switching losses: SPICE | datasheet.** The default is the device's own
  (`basis_default` in the catalogue row: SPICE wherever a table exists).
  "SPICE" is disabled with "(no model)" for the 750 V parts. The HelpTip
  names LTspice as what those parts would need.
* **R_G,on / R_G,off / V_GS on / V_GS off / Loop L_σ.** Blank means the
  datasheet test circuit (R_G 2.3 Ω CoolSiC / 1.6 Ω OptiMOS, V_GS 18 / 10 V,
  L_σ 15 / 2 nH), each with a HelpTip. They are saved with the
  configuration (`PATCH …/controller`: `switching_source`,
  `r_g_off_ext_ohm`, `v_gs_on_V`, `l_sigma_nH`) and sent with every solve.
* Under the selector, in amber, the device's deviation line when it has one.
* Results card: "Basis: …" names the basis, plus the deviation line (ⓘ).

---

## 8 · Open items for the owner

1. **The three 750 V parts** stay on the datasheet fallback until LTspice
   (free) is installed and their `_LTSpice.lib` double pulse is run.
   Nothing here installs it.
2. **800 V and above** is read from the 375/750.4 V power law (flagged).
   Add an 800 V node overnight if a ≥ 800 V bus becomes a design point.
3. **IQE050N08NM5SC energies are unvalidated.** The datasheet publishes no
   E_on/E_off. The card also still carries the base part's datasheet
   (09-23 §2).
4. The R_G slopes of the IMCQ parts other than R004, and the V exponent of
   the 750 V parts, are family assumptions, stated on each card. Digitise
   each part's own figure to replace them.
