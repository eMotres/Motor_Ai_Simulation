# The Controller module — the vendor's SPICE models (2026-09-23)

Owner, 2026-09-23: model the power switches **the same way the PCB/schematic
tools do** — KiCad runs ngspice, Altium/LTspice/PSpice run the manufacturer's
SPICE `.lib` subcircuits — so that the board design and this simulation agree
later. Downloading the manufacturers' SPICE libraries was authorised.

What this adds is a **second, optional** source of switching energies: a
table built by double-pulse simulations of Infineon's own device models, in
the datasheet's own test circuit, read off on the datasheet's own
integration windows. The datasheet curves stay the default
(`switching_source: datasheet`) until the owner has read §6.

---

## 1 · What is where

| what | where |
|---|---|
| per-part provenance (URL, date, sha256, level, pins, licence) | `config/devices/spice/<PART>/manifest.yaml` (committed) |
| the datasheet double pulse of each runnable part, as a standalone netlist | `config/devices/spice/<PART>/double_pulse_datasheet_T<T>.cir` (committed) |
| vendor libraries (zips, unpacked) | `config/devices/spice/_vendor/` — **git-ignored** (§2, licence) |
| every run (netlist, waveforms `.npz`, metrics `.json`, ngspice log) | `config/devices/spice/_runs/<PART>/` — git-ignored, re-creatable |
| manifest reader, sha256 check, DDT() translation | `src/motor_ai_sim/inverter/spice/models.py` |
| netlists (double pulse, static, AC) | `…/spice/netlist.py` |
| ngspice runner (separate low-priority process) | `…/spice/runner.py`, `…/spice/_worker.py` |
| datasheet-window extraction | `…/spice/extract.py` |
| grid runner, static sweeps | `…/spice/harness.py` |
| `switching_table` block, interpolation | `…/spice/table.py` |
| loss-model hook | `devices.DeviceCard.e_switch(source=…)`, `losses.solve_controller` (`switching_source`, `r_g_off_ext_ohm`, `l_sigma_nH`), route `/api/controller/solve` passes the three through |
| scripts | `scripts/spice_validate_devices.py`, `spice_figure_checks.py`, `spice_build_tables.py`, `spice_compare_losses.py` |
| tests (no ngspice needed) | `tests/test_spice_harness.py` |

---

## 2 · The downloads

All from infineon.com, public assets (design-resources API: confidentiality
"Public", not gated, plain HTTPS GET — no login, no account, no click-through).
The zip and the library file each carry a sha256 in the manifest;
`models.model_for()` re-checks the library's sha256 on every use and refuses
a mismatch, so a table can never silently be reused with another model
revision.

| part(s) | library (zip) | revision | model levels | runs in ngspice? |
|---|---|---|---|---|
| IMCQ120R004/005/007/010/017/034/078M2H | `infineon-spice-coolsic-1200v-gen2-trench-mosfet-simulationmodels-en.zip` → `CoolSiC_1200V_Gen2_Trench_MOSFET_L1_L3.lib` | v02_04, lib dated 2026-06-15, "evaluated with SIMetrix" | L1 (4 pins: D, G, S, Kelvin S; T_j = `.temp`) and L3 (+ Tj, Tcase thermal pins) | **yes**, after an exact DDT() translation (§3) |
| IMDQ75R004M2H, IMDQ75R007M2H | `infineon-coolsic-mosfet-750v-g2-spice-simulationmodels-en.zip` (lib version 1334, 2025-11-06) | 01_00 | L1 / L3 names and pins readable | **no — encrypted** |
| AIMDQ75R016M2H | `infineon-coolsic-750v-g2-automotive-mosfet-simulationmodels-en.zip` (lib version 1325, 2025-10-16) | 01_00 | L1 / L3 names and pins readable | **no — encrypted** |
| IQE050N08NM5SC | `infineon-optimos-powermosfet-pspice-80v-n-channel-simulationmodels-en.zip` → `OptiMOS5/OptiMOS5_80V_Spice.lib` | "Version 280225" | L0, L1 (3 pins), L3 (+ Tj, Ttop, Tbottom) | **yes** (L3, §3) |

**The 750 V "obfuscated" libraries are encrypted, not merely obfuscated.**
Each zip ships three variants and every subcircuit body — the technology core
and the per-part wrappers alike — is inside an encryption block:
`…_PSpice.lib` uses Cadence's `$CDNENCSTART_ADV2` (OrCAD/Cadence PSpice
only), `…_LTSpice.lib` is an "LTspice Encrypted File" (hex `* Begin:` blocks,
LTspice only), `…_Simetrix.lib` uses `SMX_AES` blocks (SIMetrix only). No
line of model code is in plain text. ngspice has no decryption for any of
these three formats and no compatibility mode changes that (compatibility
modes translate syntax, they do not decrypt). **Simulator needed: LTspice
(free, Analog Devices) with the `_LTSpice.lib` variant**, or OrCAD/Cadence
PSpice, or SIMetrix. KiCad cannot run these parts either. The harness writes
the same Fig. F circuit as a `.cir`, so the three parts can be run in
LTspice by hand with the vendor's own LTspice library; nothing in this repo
does that automatically. Their datasheet curves remain the only source.

**Licence.** The CoolSiC 1200 V library carries Infineon's "MODEL TERMS OF
USE" in its header: accepted by downloading/using (no click-through step on
the download); §1 the model describes a *typical* device and the datasheet
remains the only specification; §3 the model and its documentation are
**confidential information**, not to be disclosed to third parties, to be
used for simulation and testing only. The 750 V and OptiMOS libraries carry a
disclaimer (typical device, no warranty) and, for the LTspice variants,
LTspice's "use for simulation, do not reverse engineer" notice; none grants
redistribution. Therefore **no vendor file is committed**:
`config/devices/spice/_vendor/` and the derived ngspice copies are in
`.gitignore`; each manifest names the URL and sha256 to fetch the identical
file. (Owner: the ToU are browse-wrap — using the model is accepting them.)

**A card error the SPICE library exposed.** The OptiMOS library carries a
*separate* `IQE050N08NM5SC` model (R_G 0.62 Ω, a top-side thermal path) next
to the base `IQE050N08NM5` (R_G 0.8 Ω, bottom only), and Infineon publishes a
separate datasheet for the SC part (PG-WHSON-8, dual-side cooled, rev 2.0
2022-05-02: R_G 0.62 Ω, R_thJC top 0.7 K/W, I_D 99 A). The card
`IQE050N08NM5SC.yaml` was transcribed from the BASE part's datasheet on the
assumption that "SC" is a packing suffix — that assumption is wrong. Not
fixed here (a card re-transcription is its own job; suggested as a separate
task); the SPICE runs use the SC model.

---

## 3 · The simulator

**ngspice-46, the shared library KiCad 10.0 ships**
(`C:\Program Files\KiCad\10.0\bin\ngspice.dll`, "Creation Date Apr 14 2026")
— literally the engine KiCad's simulator runs. It is loaded with `ctypes` in a
separate Python child process (`_worker.py`) at BELOW-NORMAL priority, one run
at a time, killed on timeout; `set ngbehavior=psa` (PSpice + "a" compatibility,
what KiCad users set for vendor libraries). The previous pass already used
this binary; no server container was used. `runner.find_backend()` also
accepts the console `ngspice -b` (e.g. `apt-get install ngspice` on the
server) via `$MOTOR_AI_SIM_NGSPICE`.

A run that dies half-way ("Timestep too small … run simulation(s) aborted")
still lets `wrdata` write the points it had; the worker now treats that as a
FAILURE and deletes the partial file, and the double-pulse runner also
refuses a transient that stopped before `t_stop` (the first pass did not —
it would have read a 6-point waveform as data).

**Two things the vendor models needed to run in ngspice — neither changes the model:**

1. **CoolSiC 1200 V: `DDT()`.** The library writes its nonlinear capacitances
   as `G … VALUE = { C(v) * DDT(V(a,b)) }` (SIMetrix's idiom; 4 such sources
   in the technology core). ngspice evaluates `ddt()` in a behavioural source
   explicitly, so the device's own capacitances become an explicit feedback
   loop: the datasheet double pulse of IMCQ120R004M2H with the library **as
   shipped aborts at t = 77 ps** ("timestep too small", node `xh.neoidx`,
   re-checked in this pass). KiCad's simulator is ngspice, so the library as
   shipped cannot run a switching transient in KiCad either. The harness
   `.include`s a translated copy (`_vendor/_ngspice/…<sha12>.ngspice.lib`,
   git-ignored) in which every `DDT(V(x,y))` is replaced by the current of a
   1 pF capacitor driven by a unit VCVS copy of V(x,y), scaled by 1e12 — exact
   algebra, the derivative then carried by a capacitor the integrator treats
   implicitly. No parameter and no other expression is touched; DC is
   bit-identical. The translated file's header names the source file and both
   sha256s.
2. **OptiMOS 5: the L1 wrapper.** The L1 subcircuit ties its junction to
   `TEMP` through a 1 µΩ E-source loop; its DC point does not converge under a
   current drive and its transient aborts at the first turn-off (tried: gear,
   xmu 0.4, looser tolerances, `ps` mode, a 5 ns gate edge). The **L3** model
   (same electrical core) runs: its case pins (Ttop, Tbottom) are held at T_j
   by voltage sources, the junction pin is left to the model's own thermal
   network in a double pulse (holding it by a source does not converge; over
   µs the junction moves by a fraction of a kelvin — the DUT's junction node is
   written with every run) and is held too in the static/AC runs
   (isothermal, as `.temp`). Static sweeps fall back to a drain-VOLTAGE sweep
   when the current-driven DC point fails. The manifest records `level: L3`.

---

## 4 · The circuit and the windows

**Double pulse = the datasheet's Fig. F** ("Dynamic test circuit", Infineon
CoolSiC G2 datasheets section 6): half-bridge of two identical devices from
the vendor library; DUT = low side; the high side is held off at V_GS(off)
through its own R_G and freewheels in its **own body diode** ("2nd device own
body diode"); load inductor L across the high side with its parasitic C_σ;
L_σ split ½ in the + rail, ½ in the − rail as drawn. The DUT gate is driven
from one source through separate turn-on / turn-off external resistances,
referenced to the Kelvin source pin where the package has one. Pulse 1 ramps
L to the test current (L = V_DD·t₁/I), E_off is taken on its falling edge,
pulse 2's rising edge gives E_on and the high side's recovery. If the switched
current misses the target by > 1.5 % the first pulse is re-timed once.

| parameter | value | source |
|---|---|---|
| V_DD, I_D, R_G,ext, V_GS, T_vj | the datasheet's Table 4 conditions (validation) / the grid (tables) | card |
| L_σ | 15 nH (CoolSiC, Table 4 "L_σ = 15 nH"); 2 nH for the OptiMOS part | datasheet / **assumption** (the OptiMOS datasheet names none) |
| gate loop L | 2 nH (SiC) / 1 nH (OptiMOS) | **assumption** |
| C_σ across L | 20 pF / 5 pF | **assumption** |
| driver edge | 2 ns / 1 ns (the source itself; the gate edge is set by R_G·C_iss) | **assumption** |
| timeline | 1 µs + 0.5 µs gap + 0.4 µs (SiC); 0.5 + 0.3 + 0.3 µs (OptiMOS) | chosen; §5 |
| max step | 1 ns (SiC) / 0.2 ns (OptiMOS) | measured: 4 ns moved E_on −3 %, Q_fr −9 % |
| T_j | L1: `.temp` = T_vj (isothermal, the datasheet's condition); L3: §3 | |

**Windows — the datasheet's own** (IMCQ120R004M2H rev 1.10, p. 16; identical
in every IMCQ datasheet):

* Fig. C — `E_off = ∫ V_DS·I_D dt` from t1 (V_DS rises through 10 % V_DD) to
  t2 (I_D falls through 10 % I_D); `E_on = ∫ V_DS·I_D dt` from t3 (I_D rises
  through 10 %) to t4 (V_DS falls through 10 % V_DD). V_DS is the DUT's drain
  to power-source pin.
* Fig. A — t_d(on) = V_GS 10 % → V_DS 90 %, t_r = V_DS 90 % → 10 %;
  t_d(off) = V_GS 90 % → V_DS 10 %, t_f = V_DS 10 % → 90 %.
* Fig. B — body-diode recovery of the freewheeling device: t_fr = t_a + t_b
  from the current zero crossing to where the reverse current has decayed to
  10 % I_frm; Q_fr = Q_a + Q_b over t_fr ("Q_fr includes also Q_C", Table 6).
  The datasheet draws no separate E_fr window: E_fr is taken as ∫ V·I of the
  freewheeling device over the same t_fr (stated).
* E_tot = E_on + E_off + E_fr (Table 4, footnote 1 "including E_fr").

---

## 5 · Validation against each model's own datasheet

`scripts/spice_validate_devices.py` at each part's OWN datasheet test
conditions (card fields `switching.v_dd_ref_V / i_d_ref_A / r_g_ext_ref_ohm`,
`gate.l_sigma_nH`, V_GS 0/18 V; OptiMOS: 40 V / 20 A / 1.6 Ω / 10 V).
Datasheet = the card's TABLE anchors (the card numbers were re-checked
against the PDFs for IMCQ120R004/005/007/034M2H in this pass). The rule the
owner set: **more than ~15 % off on a switching energy → "not trusted", with
the reason; a vendor model is never tuned.** The verdict below judges E_on,
E_off and E_tot (= E_on + E_off + E_fr, Table 4 footnote 1); E_fr is shown
but judged only inside E_tot, because the datasheet draws no E_fr window
(§4) and at small dies our t_fr window is mostly the C_oss charge of the
freewheeling device.

### 5.1 · Static (DC sweeps; AC at the datasheet's V_DS)

R_DS(on) is read Kelvin (drain to source-sense), the datasheet's convention.

| part | R_DS(on) 25 °C ds / SPICE mΩ | R_DS(on) 175 °C ds / SPICE | V_SD 25 °C ds / SPICE V | C_iss ds / SPICE pF | C_oss ds / SPICE | C_rss ds / SPICE |
|---|---|---|---|---|---|---|
| IMCQ120R004M2H | 3.70 / 3.60 (−3 %) | 8.90 / 8.75 (−2 %) | 4.20 / 4.24 | 13 000 / 13 204 | 574 / 602 | 50 / 47 |
| IMCQ120R005M2H | 5.00 / 4.76 (−5 %) | 11.90 / 11.65 (−2 %) | 4.20 / 4.23 | 9 760 / 9 853 | 428 / 449 | 37.3 / 35 |
| IMCQ120R007M2H | 7.50 / 7.03 (−6 %) | 17.70 / 17.33 (−2 %) | 4.20 / 4.23 | 8 440 / 6 591 | 287 / 300 | 25 / 24 |
| IMCQ120R010M2H | 10.0 / 9.33 (−7 %) | 23.7 / 23.1 (−2 %) | 4.20 / 4.21 | 6 320 / 4 928 | 214 / 225 | 19 / 18 |
| IMCQ120R017M2H | 17.1 / 15.8 (−8 %) | 40.6 / 39.2 (−3 %) | 4.20 / 4.20 | 3 730 / 2 892 | 126 / 132 | 11 / 10 |
| IMCQ120R034M2H | 34.0 / 31.2 (−8 %) | 80.4 / 77.8 (−3 %) | 4.20 / 4.19 | 1 920 / 1 454 | 64 / 66 | 5.5 / 5 |
| IMCQ120R078M2H | 78.1 / 71.4 (−9 %) | 184.8 / 178.6 (−3 %) | 4.20 / 4.22 | 880 / 633 | 28 / 29 | 2.4 / 2 |
| IQE050N08NM5SC (L3) | 4.30 / 4.31 (0 %) at 10 V; 6.10 / 6.17 at 6 V | — (no table value) | 0.83 / 0.82 (20 A) | 2 200 / 2 228 | 370 / 368 | 21 / 21 |

V_SD at 100 °C and 175 °C (4.1 / 4.0 V on every CoolSiC part) is reproduced
to ±0.03 V; R_DS(on) at 150 °C to −1…−4 %; R_DS(on) at V_GS 15 V to −2…−5 %.
The static model is **good**. Two things to know:

* **C_iss of the 7…78 mΩ parts is 22…28 % low** in the model (the 4 and
  5 mΩ parts match): the gate is lighter than the datasheet's.
* **The package source path.** Between the Kelvin pin and the power source
  pin the CoolSiC model has ~0.95 mΩ at 185 A (V at the power pin 4.55 mΩ
  vs 3.60 mΩ Kelvin on R004, 25 °C; the same ~0.95 mΩ on every Q-DPAK). The
  datasheet R_DS(on) — and this project's conduction model — is the Kelvin
  number. If the model's source path is real, the load current's conduction
  loss in a bridge is higher than the datasheet R_DS(on) says: on L155 rated
  (R004 at ~130 °C, R_DS(on) ≈ 7.2 mΩ) about **+13 %, ≈ +250 W** on the
  bridge. Infineon publishes no power-pin resistance to check it against —
  an open decision (§10), not applied.

### 5.2 · Dynamic (double pulse at the datasheet point)

| part | T_vj | E_on ds / SPICE µJ | E_off ds / SPICE | E_fr ds / SPICE | Q_fr ds / SPICE µC | t_r / t_f ds vs SPICE ns | verdict |
|---|---|---|---|---|---|---|---|
| **IMCQ120R004M2H** 0/18 V | 25 °C | 3790 / 3199 (−16 %) | 3970 / 4427 (+12 %) | 860 / 425 (−51 %) | 0.95 / 1.14 | 26.8/35.5 vs 41.4/28.7 | **not trusted**: E_on −16 % |
| | 175 °C | 4920 / 4523 (−8 %) | 4780 / 4985 (+4 %) | 2990 / 1964 (−34 %) | 3.36 / 3.97 | 23.8/41.8 vs 49.8/31.6 | trusted (E_tot −10 %) |
| IMCQ120R004M2H −5/18 V | 25 °C | 3770 / 3128 (−17 %) | 2310 / 2477 (+7 %) | 870 / 420 (−52 %) | 0.95 / 1.14 | 26.8/35.5 vs 39.8/18.7 | **not trusted**: E_on −17 % |
| | 175 °C | 4890 / 5539 (+13 %) | 2510 / 2679 (+7 %) | 3100 / 3150 (+2 %) | 3.36 / 6.09 | 23.8/41.8 vs 53.5/19.9 | trusted (E_tot +8 %) |
| IMCQ120R005M2H | 25 °C | 2380 / 2270 (−5 %) | 2530 / 2308 (−9 %) | 600 / 281 (−53 %) | 0.74 / 0.81 | 21.3/28.2 vs 35.7/21.7 | trusted (E_tot −12 %) |
| | 175 °C | 3300 / 3208 (−3 %) | 3040 / 2571 (−15.4 %) | 2100 / 1308 (−38 %) | 2.56 / 2.81 | 19.0/33.3 vs 43.0/24.1 | **not trusted**: E_off −15 %, E_tot −16 % |
| IMCQ120R007M2H | 25 °C | 1113 / 1355 (+22 %) | 886 / 1077 (+22 %) | 354 / 166 (−53 %) | 0.75 / 0.52 | 12.1/16.0 vs 28.5/17.6 | **not trusted**: E_on, E_off +22 % |
| | 175 °C | 1838 / 2021 (+10 %) | 1073 / 1236 (+15.2 %) | 1195 / 867 (−27 %) | 2.00 / 1.92 | 10.7/18.9 vs 35.9/19.3 | **not trusted** (marginal): E_off +15 % |
| IMCQ120R010M2H | 25 °C | 648 / 721 (+11 %) | 451 / 491 (+9 %) | 313 / 153 (−51 %) | 0.60 / 0.42 | 9.0/12.0 vs 20.3/12.9 | trusted |
| | 175 °C | 1153 / 1162 (+1 %) | 550 / 571 (+4 %) | 1055 / 741 (−30 %) | 1.60 / 1.50 | 8.0/14.1 vs 25.7/14.3 | trusted |
| IMCQ120R017M2H | 25 °C | 330 / 373 (+13 %) | 110 / 98 (−10 %) | 140 / 81 (−42 %) | 0.33 / 0.24 | 6.0/8.9 vs 13.9/8.1 | trusted |
| | 175 °C | 590 / 603 (+2 %) | 180 / 113 (−37 %) | 400 / 406 (+1 %) | 0.99 / 0.84 | 5.3/10.5 vs 19.1/8.5 | **not trusted**: E_off −37 % |
| IMCQ120R034M2H | 25 °C | 193 / 114 (−41 %) | 50 / 31 (−37 %) | 20 / 54 (+170 %) | 0.20 / 0.13 | 4.9/7.3 vs 8.2/7.1 | **not trusted** |
| | 175 °C | 379 / 221 (−42 %) | 78 / 37 (−53 %) | 50 / 240 (+381 %) | 0.62 / 0.45 | 4.4/8.6 vs 10.7/7.3 | **not trusted** |
| IMCQ120R078M2H | 25 °C | 75 / 69 (−8 %) | 21 / 17 (−17 %) | 6 / 12 (+92 %) | 0.13 / 0.04 | 3.2/4.8 vs 8.1/9.2 | **not trusted**: E_off −17 % |
| | 175 °C | 159 / 125 (−22 %) | 29 / 20 (−31 %) | 16 / 73 (+356 %) | 0.44 / 0.18 | 2.8/5.6 vs 10.8/9.6 | **not trusted** |
| IQE050N08NM5SC (40 V, 20 A, 1.6 Ω, L_σ 2 nH assumed) | 25 °C | — / 1.4 | — / 1.5 | — / 7.1 | 0.03 (at 100 A/µs) / 0.18 (at ~9 A/ns) | 4.6/4.0 vs 5.7/4.4 | energies **unvalidated** — the datasheet publishes none |
| IMDQ75R004M2H, IMDQ75R007M2H, AIMDQ75R016M2H | — | — | — | — | — | — | **not run**: library encrypted (§2) |

Each dynamic run is at the switched load current within ±1.5 % of the
datasheet I_D (the first pulse is re-timed once when it misses).

What the validation says:

* **IMCQ120R004M2H (the L155 part): usable at the temperatures the inverter
  runs at.** At 175 °C E_on −8 %, E_off +4 %, E_tot −10 % (0 V off) and
  +13 / +7 / +8 % (−5 V off). At 25 °C E_on is 16…17 % low — just outside the
  15 % line, so formally "not trusted" at 25 °C. E_fr (−34…−52 %) is the
  model's weakest quantity at 0 V off; at −5 V/175 °C it matches (+2 %).
* The 5 / 10 / 17 mΩ parts are within 15 % at most points; 7 mΩ reads
  +22 % at 25 °C; **the small dies (34, 78 mΩ) are not trusted** — at 20 A
  and 9 A the datasheet energies are dominated by capacitive charge and
  test-set parasitics, and the model's E_on/E_off are 8…53 % low (their
  E_fr in our window is mostly the other device's C_oss charge).
* **Switching times are not a match and are not used:** the model's t_r
  (V_DS fall at turn-on) is 1.5…3.6× the datasheet's, t_f −28…+10 %, the
  delays shorter (our driver is an ideal source with a 2 ns edge; the
  datasheet's is a real driver IC). The energies are integrals over those
  edges and are what the loss model reads.
* Q_fr: within −31…+20 % on the 4…17 mΩ parts at 0 V off; at −5 V/175 °C
  the model gives 6.1 µC against the datasheet's 3.36 µC for 0 V (the
  datasheet gives no −5 V Q_fr).

---

## 6 · The existing algorithm against the SPICE table

Solver-direct (`scripts/spice_compare_losses.py`, `losses.solve_controller`
in-process — no API, no config write). The only thing that changes between
rows is where E_on/E_off/E_fr come from; conduction, dead time, the coldplate
and the T_j iteration are the same code, so the conduction column moves only
through T_j.

### 6.1 · L155 motor, rated (IMCQ120R004M2H × 3 per switch, one 3-phase bridge)

The §6 point of `CONTROLLER_MODULE_2026-09-22.md`: CIANO10 200 opt / L155
motor rated 1×9 mm, delta 314.3 A per phase (544 A per leg), 272.2 kW AC,
m 0.6333, 750.4 V, 24 kHz, 0.5 µs dead time, V_GS 18/0 V, micro-channel
coldplate water-glycol 50/50 8 L/min 65 °C, R_TIM 0.03 K/W, η_shaft 97.71 %.
The first row reproduces that document's 4 151 W / 132 °C / 98.50 %.

| case | conduction W | dead time W | switching W (on/off/fr) | total W | T_j °C | η_inv | η wall-to-shaft | feasible |
|---|---|---|---|---|---|---|---|---|
| datasheet curves, R_G 2.3 Ω (existing) | 1930 | 145 | 2078 (842/795/440) | **4152** | 132.2 | 98.50 % | 96.24 % | yes |
| SPICE table, datasheet driver R_G 2.3/2.3 Ω, L_σ 15 nH | 1864 | 145 | 1873 (748/859/267) | **3882** | 127.9 | 98.59 % | 96.34 % | yes |
| datasheet curves × existing R_G rule at 4.7 Ω (existing) | 2584 | 145 | 4108 (1844/1709/554) | **6837** | 175.7 | 97.55 % | 95.32 % | **no** (T_j) |
| SPICE table, R_G 4.7/4.7 Ω, L_σ 10 nH | 2122 | 145 | 2674 (1084/1355/234) | **4940** | 145.0 | 98.22 % | 95.97 % | yes |
| SPICE table, R_G 4.7/4.7 Ω, L_σ 20 nH | 2138 | 145 | 2726 (992/1443/292) | **5009** | 146.1 | 98.19 % | 95.94 % | yes |

Per switching event at 750.4 V, 125 °C, R_G 2.3 Ω (what the two sources
feed the integral):

| I_D A | E_on curves / SPICE µJ | E_off curves / SPICE | E_fr curves / SPICE | E_tot SPICE ÷ curves |
|---|---|---|---|---|
| 50 | 1299 / 1222 | 1018 / 770 | 572 / 644 | 0.91 |
| 100 | 2295 / 2076 | 2124 / 1914 | 1216 / 913 | 0.87 |
| 185.2 | 4262 / 3744 | 4230 / 4369 | 2139 / 1316 | 0.89 |
| 257 | 6060 / 5470 | 5712 / 6935 | 3094 / 1664 | 0.95 |

**Assumed "realistic driver"** (the owner's to change): one external gate
resistor per device, **4.7 Ω for both edges**, V_GS 18/0 V (the §6 setting),
power-loop stray **10 nH and 20 nH** per commutation cell. 2.3 Ω is
Infineon's lab value; with three Q-DPAKs in parallel per switch each device
normally gets its own 3.3…5 Ω to damp paralleled-gate ringing and hold dv/dt,
and the loss model's single `r_g_ext_ohm` could not express different on/off
resistors (the SPICE path can: `r_g_off_ext_ohm`).

What the table says:

1. **At the datasheet driver the two sources agree to 10 % on switching and
   6.5 % on the total** (SPICE lower: 1 873 vs 2 078 W; 3 882 vs 4 152 W;
   T_j 4 K lower). The split differs: SPICE has less E_fr (−40 %) and E_on
   (−12 %), more E_off (+3…+21 % above 185 A). That is the model's own
   validation offset at this part, not a physics difference: at the
   datasheet point the model reads E_off
   +4…+12 %, E_on −8…−16 %, E_fr −34…−51 % (§5.2).
2. **At a realistic 4.7 Ω the existing model is wrong by +54 % on
   switching** — 4 108 W against 2 674 W — and pushes T_j to 175.7 °C
   (infeasible) where the vendor model gives 145 °C. The cause is the
   card's R_G rule as IMPLEMENTED: `E(R) = E(2.3 Ω) · R/2.3` for E_on and
   E_off, a line through the ORIGIN. The datasheet's own figure
   "E = f(R_G,ext)" (p. 12) is a straight line with a large intercept
   (E_on ≈ 3.2 mJ at R → 0): at 4.7 Ω it reads E_on ≈ 6.7 mJ, E_off ≈ 7.2
   mJ, against the rule's 10.1 / 9.8 mJ (+50 % / +36 %); at 10 Ω the rule
   gives 21.4 / 20.8 against 10.5 / 12.8 mJ (×2.0 / ×1.6). The document
   says "linear in R_G,ext through the datasheet point"; the code is
   proportional — see §6.3 for the SPICE R_G sweep against the same figure.
3. **L_σ 10 → 20 nH costs +2 % switching** at 4.7 Ω (+52 W on the bridge):
   more stray trades a little E_on (the stray carries part of V_DS during
   the current rise) for more E_off (overshoot). The datasheet model cannot
   see L_σ at all.

### 6.2 · Ø40 L12 (IQE050N08NM5SC × 2 per switch, one 3-phase bridge)

The test suite's L12 point moved to the 6S bus: 22.2 V, star 43.8 A,
f_el 1 516.7 Hz, 48 kHz, 0.5 µs, V_GS 10/0 V, R_G 1.6 Ω, forced air 10 m/s
35 °C, R_TIM 0.03, R_spread 0.02 K/W, m 0.9 and a STATED power factor 0.9
(p_ac 835 W follows). The stored L12 duty record is thermal only — there is
no electrical point at 22.2 V to read, so this is a device comparison at a
stated point, not a re-solve of the machine.

| case | conduction W | dead time W | switching W (on/off/fr) | total W | T_j °C | η_inv |
|---|---|---|---|---|---|---|
| times-and-charges fallback (existing), R_G 1.6 Ω | 13.1 | 5.7 | 0.4 (0.3/0.3/0.0) | **19.3** | 45.5 | 97.74 % |
| SPICE table, R_G 1.6/1.6 Ω, L_σ 2 nH | 13.2 | 5.7 | 1.5 (0.3/0.3/0.9) | **20.4** | 46.1 | 97.61 % |
| SPICE table, R_G 1.6/1.6 Ω, L_σ 5 nH | 13.2 | 5.7 | 1.6 (0.0/0.6/0.9) | **20.5** | 46.2 | 97.60 % |

Per event, 22.2 V, 25 °C, R_G 1.6 Ω, L_σ 2 nH:

| I_D A | E_on fallback / SPICE µJ | E_off fallback / SPICE | E_fr fallback / SPICE | E_tot SPICE ÷ fallback |
|---|---|---|---|---|
| 5 | 0.1 / 0.2 | 0.1 / 0.4 | 0.3 / 1.6 | 3.5 |
| 12 | 0.4 / 0.4 | 0.4 / 0.5 | 0.3 / 2.5 | 3.2 |
| 20 | 0.6 / 0.6 | 0.6 / 1.2 | 0.3 / 3.4 | 3.4 |
| 31 | 0.9 / 1.1 | 0.9 / 2.4 | 0.3 / 4.5 | 3.7 |

What it says: **the fallback under-reads the switching loss ×3.4…3.7**,
and almost all of it is the body diode. The fallback's E_fr = ½·Q_rr·V uses
the datasheet Q_rr (30 nC) measured at di/dt = 100 A/µs; in the bridge the
diode is commutated at ~9 A/ns and the vendor model gives Q_fr ≈ 0.18 µC at
40 V/20 A (6×), and E_fr grows with current where the fallback holds it
constant. The overlap formula's E_on is close from 12 A up (0.4…1.1 µJ both
ways); its E_off under-reads ×1.3 at 12 A, ×2 at 20 A and ×2.7 at 31 A
(tenths of a µJ read off rounded table rows — the ratios, not the
decimals, are the finding). On THIS machine it hardly
matters — switching is 2…8 % of a 20 W inverter loss dominated by
conduction and dead time — but at 44 V (12S) or a higher carrier the
fallback's "lower bound" label is an understatement by a factor of ~3.

### 6.3 · The two scaling rules against the vendor model and the datasheet figures

`scripts/spice_figure_checks.py` — IMCQ120R004M2H, T_vj 175 °C, I_D 185.2 A
(each run re-timed onto the target current), V_GS 0/18 V, L_σ 15 nH. "fig"
= the datasheet's own figure, read by eye (±5 %); "rule" = what the card's
datasheet path computes.

**Bus voltage** — figure "E = f(V_DD)", rev 1.10 p. 13 (R_G,ext 2.3 Ω):

| V_DD V | E_on fig / SPICE / rule µJ | E_off fig / SPICE / rule | E_fr fig / SPICE / rule |
|---|---|---|---|
| 600 | 2950 / 2698 / 3690 | 2850 / 3331 / 3585 | 2250 / 1575 / 2242 |
| 700 | 3900 / 3587 / 4305 | 3750 / 4102 / 4182 | 2600 / 1730 / 2616 |
| 800 | 4920 / 4523 / 4920 | 4780 / 4985 / 4780 | 2990 / 1964 / 2990 |
| 900 | 5950 / 5477 / 5535 | 6000 / 5982 / 5377 | 3400 / 2135 / 3364 |
| 1000 | 7050 / 6414 / 6150 | 7500 / 7107 / 5975 | 3800 / 2233 / 3738 |

The datasheet's own curve is **not** linear in V: E_on and E_off go as
≈ V^1.8…1.9 between 600 and 1000 V (E_fr ≈ V^1.0); the vendor model gives
V^1.7 (E_on) and V^1.5 (E_off). The card's `(V/800)^1` rule therefore
over-reads below 800 V and under-reads above it: +25 % / +26 % on E_on / E_off
at 600 V, −13 % / −20 % at 1000 V. **At the L155 bus (750.4 V) the error is
small — about +5 % on E_on and E_off** (the figure at 750 V reads ≈ 4.4 /
4.25 mJ against the rule's 4.62 / 4.48) — because 750 V is close to the
800 V test point. At a 375 V bus (the concept doc's 400 V line, §10 of the
module doc) the same rule would over-read by roughly ×1.8…1.9 if the figure's
exponent held there (not published below 600 V; not simulated here). The
exponent is a card field (`switching.scaling.voltage_exponent`); ≈ 1.8 is
what this datasheet's own figure says for E_on/E_off, 1.0 for E_fr. Not
changed in this job.

**Gate resistance** — figure "E = f(R_G,ext)", rev 1.10 p. 12 (800 V):

| R_G,ext Ω | E_on fig / SPICE / rule µJ | E_off fig / SPICE / rule | E_fr fig / SPICE / rule |
|---|---|---|---|
| 2.3 | 4920 / 4523 / 4920 | 4780 / 4985 / 4780 | 2990 / 1964 / 2990 |
| 4.7 | 6700 / 6117 / 10054 | 7200 / 8179 / 9768 | 1900 / 1692 / 2990 |
| 10 | 10500 / 9425 / 21391 | 12800 / 14857 / 20783 | 1200 / 1658 / 2990 |
| 20 | 17800 / 15784 / 42783 | 23000 / 27169 / 41565 | 800 / 2028 / 2990 |

The vendor model reproduces the datasheet's R_G dependence (a straight line
with an intercept) within −9…−11 % on E_on and +14…+18 % on E_off over
2.3…20 Ω — the same offsets it has at 2.3 Ω. The card's rule (proportional
to R_G,ext, through the origin) is **+50 % / +36 % at 4.7 Ω, ×2.0 / ×1.6 at
10 Ω and ×2.4 / ×1.8 at 20 Ω** against the datasheet's own figure. That is
the rule behind the +54 % of §6.1. A line through the 2.3 Ω point with the
figure's slope (E_on ≈ 3.25 mJ + 0.73 mJ/Ω·R, E_off ≈ 2.41 mJ + 1.03 mJ/Ω·R
at 175 °C / 800 V / 185 A) would match the figure; that is a card/rule change
for the owner to decide (§10). E_fr: the figure falls monotonically with
R_G; the model's E_fr (over the t_fr window) falls to ~5 Ω and then rises
again (slower, longer recovery tail) — E_fr at large R_G is not trusted.
The existing rule holds E_fr at its 2.3 Ω value, which over-reads the figure
×1.6…×3.7 at 4.7…20 Ω.

(Each point needed its own timeline: at 20 Ω t_d(off) + t_f ≈ 0.65 µs, longer
than the default 0.5 µs gap; `timing_for(v_dss, r_g)` stretches the gap and
the second pulse above 5 Ω. A first attempt without it measured a load
current of a few amperes and asked for a 48 µs first pulse — the harness now
refuses to re-time by more than 30 % and falls back to the peak current,
flagged, when the turn-off has not finished before the averaging window.)


---

## 7 · The table in the card

`scripts/spice_build_tables.py` runs a grid (V_dc × T_j × I per driver/layout
set) and appends a `switching_table` block at the END of the card between
two marker comments — the hand-written card is not touched, and
`switching_source` is **not** set (default stays `datasheet`):

```yaml
switching_table:
  basis: spice:<lib file>@<sha256[:12]>:<subckt>
  lib_sha256: <full sha256>
  simulator: ngspice (dll) C:\Program Files\KiCad\10.0\bin\ngspice.dll, ngbehavior=psa, …
  circuit / windows / generated / note
  row_columns: [V_dc, T_j, I_off, E_off_uJ, I_on, E_on_uJ, E_fr_uJ]
  sets:
  - v_gs_on_V / v_gs_off_V / r_g_on_ohm / r_g_off_ohm (EXTERNAL) / l_sigma_nH / l_gate_nH / c_sigma_pF
    rows: [[750.4, 125, 185.4, …], …]
```

Interpolation (`table.table_energy`): per (V_dc, T_j) node each energy is
piecewise-linear in ITS OWN measured current (E_off against the current
actually switched off, E_on/E_fr against the current commutated), linear
extrapolation from the last two points (above: flagged as a warning; below:
a note), clamped at zero; then linear in T_j (clamped to the tabulated span,
said) and in V_dc between nodes. A set simulated at ONE bus is **held** at
that bus for any other V_dc and says so (no scaling law is invented). The
driver picks the set (V_GS off/on, R_G,on, R_G,off, L_σ); a near miss uses the
nearest set and names it. No scaling law is assumed anywhere.

Loss-model hook: `solve_controller({..., "switching_source": "spice",
"r_g_ext_ohm": R_G,on, "r_g_off_ext_ohm": R_G,off, "l_sigma_nH": L_σ})`. A
card without a table asked for `spice` is REFUSED by name
(`code: no_spice_table`), never silently answered from the datasheet. The
response's `losses.switching_source` / `switching_basis` and the model note
name the source and the table's provenance. The route passes the three new
fields through and keys its run history on them only when set (every
existing history key is unchanged).

---

## 8 · What the web will need (not built in this job)

The Controller tab's driver block, when the owner switches the source:

| input | unit | why |
|---|---|---|
| switching source | datasheet / SPICE | the selector; SPICE only offered for a card with a `switching_table` |
| R_G,on (external, per device) | Ω | E_on, and the set choice |
| R_G,off (external, per device) | Ω | E_off, and the set choice (the datasheet path has ONE R_G) |
| V_GS(on) / V_GS(off) | V | already there; they also pick the set |
| L_σ (power-loop stray per commutation cell) | nH | the set choice; overshoot/E_off |

and one read-only line: the table's basis (library + sha256) and the set
actually used (nearest-set note when the driver is not tabulated).

---

## 9 · Assumptions and limits, in one place

| | |
|---|---|
| **vendor (not ours)** | the device physics — Infineon's model, a *typical* device; never tuned here |
| **datasheet (exact)** | the test circuit (Fig. F), the windows (Figs. A–C), V_DD / I_D / R_G,ext / V_GS / L_σ = 15 nH of the CoolSiC tests |
| **assumption** | gate-loop L 2 nH (SiC) / 1 nH (Si); C_σ across the load 20 pF / 5 pF; driver source edge 2 ns / 1 ns; an ideal voltage-source driver (no driver output impedance beyond R_G, no Miller clamp); L_σ 2 nH for the OptiMOS datasheet point; the E_fr window (∫V·I over t_fr — the datasheet draws none); the "realistic driver" of §6 (4.7 Ω both edges, 10/20 nH) |
| **numerics** | ngspice-46 (KiCad 10.0), trapezoidal, reltol 1e-3, max step 1 ns (SiC) / 0.2 ns (Si); DDT() carried by an implicit capacitor (§3) |
| **what the table is not** | a board: no second paralleled device per switch (the table is per device; paralleled devices are assumed to share current and energy equally, as in the datasheet path), no bus-bar resonance, no gate-driver supply loss (kept out of T_j as before) |
| **bus** | the R004 table was simulated at 750.4 V only — it is HELD, not scaled, at any other bus, and says so; the scaling question is answered separately in §6.3 |
| **temperature** | L1 isothermal at T_j (the datasheet's T_vj); nodes 25 / 125 / 175 °C, linear between |
| **not done** | the three 750 V parts (encrypted, §2); a SPICE run of the whole inverter over a fundamental period (the table feeds the existing period integral instead); V_GS(off) = −5 V tables (only validated, §5); the L3 self-heating inside a switching period |

**Opening the same circuit in KiCad / LTspice.** Every `.cir` is plain SPICE.
KiCad (ngspice): `set ngbehavior=psa` in the project's `.spiceinit`, and the
CoolSiC library must be the translated copy — created on first use by
`python -c "from motor_ai_sim.inverter.spice.models import model_for; print(model_for('IMCQ120R004M2H').include_path)"`
(it lands in `_vendor/_ngspice/`). LTspice / PSpice / SIMetrix: point the
`.include` at the vendor's own file; the G-element driver with split R_G
(`GRG … VALUE={IF(…)}`) is PSpice syntax LTspice accepts.

---

## 10 · Open decisions for the owner

1. **Switch the L155 controller to `switching_source: spice`?** At the
   datasheet driver the two sources are 10 % apart on switching (SPICE
   lower); at a real driver the datasheet path is off by +54 % because of
   its R_G rule. Recommendation: keep `datasheet` as default, but do not
   use the datasheet path at any R_G other than 2.3 Ω until (2) is fixed.
2. **The R_G rule** (`devices._e_switch_from_curves`): proportional through
   the origin today; the datasheet figure is linear with an intercept
   (§6.3). Replace by a line through the 2.3 Ω point with the figure's slope
   (a card field per part), or read R_G from the SPICE table only.
3. **The bus-voltage exponent** (`scaling.voltage_exponent`, 1.0 today): the
   datasheet's own E(V_DD) figure says ≈ 1.8 for E_on/E_off and 1.0 for E_fr
   (§6.3). Irrelevant at 750 V (≈ 5 %), large at the 375 V line.
4. **The package source path** (~0.95 mΩ between the Kelvin and the power
   source pin in the vendor model, §5.1): add it to conduction or not.
   ≈ +250 W on L155 rated if real.
5. **The realistic driver** for the Controller tab (R_G,on / R_G,off,
   V_GS, L_σ): the 4.7 Ω / 10–20 nH here is an assumption; the web needs the
   inputs of §8 before any of this reaches a user.
6. **The three 750 V parts** need LTspice (free) with the vendor's
   `_LTSpice.lib` to be simulated at all; say if that should be set up
   (it would be a second simulator, not KiCad's).
7. **IQE050N08NM5SC card**: re-transcribe from its own datasheet (§2;
   suggested as a separate task).
8. **IMCQ120R005M2H card** (fixed in this pass): its 175 °C switching blocks
   carried IMCQ120R004M2H's numbers (4.92 / 4.78 / 2.99 mJ …) — re-read from
   its own datasheet rev 1.10 (3.3 / 3.04 / 2.1 mJ at 138.2 A, and the
   −5 V set). The R005M2H rows of `CONTROLLER_MODULE_2026-09-22.md` §10.1
   were computed with the wrong card and over-state its switching loss.
