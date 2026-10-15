# Shaft material rule (owner 2026-10-14 / 2026-10-15): PREPARED, NOT APPLIED

> 2026-10-14: "only the smallest motors have a stainless shaft" and "all the walls and the shafts are aluminium 6061".
> 2026-10-15: the small-motor shaft grade is **SS420** (AISI 420 martensitic stainless, EN 1.4021 X20Cr13 / 1.4028 X30Cr13,
> Chinese 2Cr13 / 3Cr13) - it replaces the provisional 316. SS420 is **ferromagnetic**, so the change is a physics change; the
> measured size of that change is in "What the SS420 shaft does to the motors" below. Nothing here is applied to any die or
> configuration.

## The rule (by stator outer diameter, not per-motor guesses)

| Stator OD | Shaft | Note |
|---|---|---|
| <= 40 mm (the 30 mm and 40 mm families) | stainless steel **AISI 420** (`Stainless_420` hardened + tempered, or `Stainless_420_annealed`) | ferromagnetic martensite, mu_r 15..430 over the curve (below) |
| 85 mm and larger public dies (85, 100, 150) | aluminium 6061-T6 (`Aluminium_6061`) | non-magnetic |
| 200 mm and larger (private, high-speed) | **not touched** | kept as is |
| between 40 mm and 85 mm | no rule yet | listed as "undecided" |

Every aluminium part of the Full assignment (holders, walls, plates, rotor carrier) already defaults to `Aluminium_6061`
(`config/part_materials.yaml`, `mechanical_parts`); there is no 7075 default.

## The cards (`config/materials_library.yaml`, every value `verify: true`, sources listed on the card)

| card | category | state | density | sigma | E | Rp0.2 / Rm | magnetic |
|---|---|---|---:|---:|---:|---:|---|
| `Stainless_420` | steel (solid) | hardened 1038 C oil quench + tempered 204 C, about 52 HRC | 7720 kg/m3 | 1.82 MS/m | 200 GPa | 1482 / 1724 MPa | B-H curve below (hardened-state PROXY) |
| `Stainless_420_annealed` | steel (solid) | annealed | 7720 kg/m3 | 1.82 MS/m | 200 GPa | 345 / 655 MPa | B-H curve below (measured annealed bar) |
| `Stainless_304` | conductor | annealed austenite | 8000 kg/m3 | 1.39 MS/m | 193 GPa | 205 / 515 MPa | non-magnetic, mu_r 1.005 |
| `Stainless_316` | conductor | annealed austenite (the superseded provisional grade, kept) | 8000 kg/m3 | 1.35 MS/m | 193 GPa | 205 / 515 MPa | non-magnetic, mu_r 1.005 |

Common 420 figures (Carpenter CarTech 420 datasheet, converted from imperial units): k 24.9 W/(m K) at 100 C, cp 460 J/(kg K),
CTE 10.3 ppm/K (0-100 C). **Estimates, flagged `verify`**: Poisson's ratio 0.28 (the datasheet gives none) and the maximum service
temperature (200 C for the hardened card - never above its 204 C temper; 400 C for the annealed one). **Missing**: fatigue limit
(not in the sources read) - take it from the supplier's certificate or a test before sizing the shaft for fatigue.

### The B-H curves the solver uses (and where they come from)

Only the annealed state of 420 is published in magnetic terms. P. Oxley et al., J. Magn. Magn. Mater. 321 (2009) 2107, measured a
0.188 in 420 rod as received: mu_max 450, Hc 820 A/m, Br about 0.83 T, Bs about 1.44 T (the ASM Handbook, vol. 2, lists a FULLY
annealed 420 at mu_max 950, Hc 800 A/m). Tavares et al. (J. Alloys Compd. 312, 2000) report that oil quenching raises Hc and Br and
lowers the saturation magnetisation, but publish no hardened curve. Both cards therefore carry a **Frohlich-Kennelly fit**
`J = Js H / (H + h)`, `B = mu0 H + J` (`h = Js / (mu0 mu_max)`), flagged `derived` + `verify`, *not* a measured curve:

| card | Js | mu_max | anchor | B at 200 / 800 / 2000 / 5000 / 12000 / 60000 A/m |
|---|---:|---:|---|---|
| `Stainless_420_annealed` | 1.44 T | 450 | measured 420 bar (Oxley 2009) | 0.105 / 0.345 / 0.636 / 0.960 / 1.203 / 1.457 T (mu_r 418 / 343 / 253 / 153 / 80 / 19) |
| `Stainless_420` (hardened) | 1.17 T | 170 | **proxy**: the measured hard martensite 440A of the same paper (mu_max 170, Hc 1430 A/m, Bs 1.17 T); a 52 HRC 420 is probably no softer | 0.042 / 0.150 / 0.316 / 0.565 / 0.818 / 1.147 T (mu_r 165 / 149 / 125 / 90 / 54 / 15) |

For comparison `Steel_42CrMo4_QT` (the shaft the Ø85 and Ø40-L20 dies name today, a generic QT-steel estimate): mu_r about 1000 at
400 A/m, 1.5 T at 5 kA/m. The FEM below also runs an **ideal-soft bound** (Js 1.95 T, mu_initial 3000, job-local card, never in
the library) so that no real 420 condition can be worse than it. Replace both fits by a ring-test measurement of the supplier's
bar (IEC 60404-4) when the shaft is bought.

## What is prepared

* The cards above. They are the solver's one card per material, so the same card serves the EM assignment, the masses and the
  stress/thermal studies. Not assigned anywhere.
* `scripts/shaft_material_proposal.py`: applies the rule to a die catalog and prints, per die and configuration, the shaft
  material the electromagnetic simulation has today, the proposed one, whether the change is magnetic or not, and the mass
  effect. It **never writes** a die; `--patch FILE` produces a `.patch.yaml` proposal for the orchestrator.
* `scripts/shaft_material_fem_compare.py`: the FEM A/B below (passport solver-direct jobs on a read-only copy of the inputs;
  runs on the server sandbox only).

## Why it is a PHYSICS change (a separate step)

The shaft is an electromagnetic part. Its material is the configuration's `materials.shaft`; a part the configuration does not name
takes the workspace's `motor_config.yaml` value (the shipped default is aluminium 6061; **the Ø40 passports of 2026-10-13 were
solved with `Steel_42CrMo4_QT` from the owner's workspace for both L12 and L20**). Assigning a new grade moves the shaft's eddy
loss and, for a ferromagnetic shaft, the rotor flux path. The component card's Full assignment follows the same material (the CAD
shaft is rescaled by the density ratio).

## What the SS420 shaft does to the motors (server FEM, 2026-10-15)

Method: CIANO14 40 new (gap 0.25, the passport inputs of 2026-10-13: die, configuration, libraries, hot temperatures copied
read-only) and CIANO28 85 20SW1200 (decisions inputs of 2026-10-14), every number from the passport's own solver-direct jobs
(`passport_v1.jobs`, one `em_transient_eval` each, the same mesh for every shaft - only the shaft card changes):
cold no-load (psi_PM, back-EMF, cogging; one full period, 144 steps), magnetostatic rated and peak points (torque, Kt = T/I,
ripple; duty current and angle, hot magnet/winding temperatures), and the settled coupled-eddy transient with demagnetisation at the
rated speed and at 1.5 x the rated speed (operating torque, ripple, shaft / magnet / stator-iron / rotor-iron / copper loss).
Efficiency here is `T w / (T w + P_loss)` on the air-gap torque (bearings and windage are shaft-independent). Duty angle 10 deg
(not the MTPA angle of the passport card), so absolute values differ from the passport card; the deltas are the point. Same-mesh
comparison, so differences are physics, not mesh noise; the noise floor is about 0.1 % (316 vs Al ripple differs by 0.02-0.15 %).
Raw numbers: `docs/data/shaft_ss420_fem_2026-10-15/`. Percentages are against **Al 6061** for the Ø40 tables and against **today's 42CrMo4** for the Ø85 tables.

### CIANO14 40 L12 (6S, rated 42.78 A / 13000 rpm, hot magnet 53.5 C)

| quantity | Al 6061 | SS420 annealed | SS420 hardened | 42CrMo4 (passport) | 316 | ideal-soft bound |
|---|---:|---:|---:|---:|---:|---:|
| PM flux linkage, cold (20 C), no load | 1.0045 | 1.0047 (+0.02 %) | 1.0047 (+0.02 %) | 1.0047 (+0.02 %) | 1.0045 (+0.00 %) | 1.0047 (+0.02 %) |
| Back-EMF at 1000 rpm, V peak | 10.269 | 10.267 (-0.01 %) | 10.267 (-0.01 %) | 10.267 (-0.02 %) | 10.269 (+0.00 %) | 10.267 (-0.02 %) |
| Cogging torque p-p, cold, mN.m | 63.44 | 63.76 (+0.51 %) | 63.74 (+0.47 %) | 63.77 (+0.53 %) | 63.44 (+0.00 %) | 63.78 (+0.53 %) |
| Torque at the rated current (static), N.m | 0.6200 | 0.6199 (-0.01 %) | 0.6199 (-0.01 %) | 0.6199 (-0.01 %) | 0.6200 (+0.00 %) | 0.6199 (-0.01 %) |
| Kt at the rated current, N.m/A rms | 0.01449 | 0.01449 (-0.01 %) | 0.01449 (-0.01 %) | 0.01449 (-0.01 %) | 0.01449 (+0.00 %) | 0.01449 (-0.01 %) |
| Torque ripple p-p at rated (static), mN.m | 33.77 | 34.01 (+0.71 %) | 33.98 (+0.64 %) | 34.02 (+0.74 %) | 33.77 (+0.00 %) | 34.02 (+0.75 %) |
| Peak-current torque (static), N.m | 0.7039 | 0.7038 (-0.01 %) | 0.7038 (-0.01 %) | 0.7038 (-0.01 %) | 0.7039 (+0.00 %) | 0.7038 (-0.01 %) |
| Operating torque at rated speed (eddy + demag solve), N.m | 0.6133 | 0.6133 (-0.01 %) | 0.6133 (-0.00 %) | 0.6133 (-0.01 %) | 0.6133 (-0.00 %) | 0.6132 (-0.01 %) |
| Operating torque ripple p-p at rated speed, mN.m | 33.00 | 33.39 (+1.16 %) | 33.35 (+1.04 %) | 33.40 (+1.21 %) | 33.01 (+0.02 %) | 33.42 (+1.27 %) |
| Shaft eddy loss at rated speed, W | 0.0141 | 0.0321 (+127.35 %) | 0.0557 (+294.64 %) | 0.0123 (-12.84 %) | 0.0048 (-66.11 %) | 0.0063 (-55.23 %) |
| Magnet eddy loss at rated speed, W | 2.988 | 2.977 (-0.35 %) | 2.978 (-0.32 %) | 2.977 (-0.37 %) | 2.988 (+0.00 %) | 2.976 (-0.38 %) |
| Rotor iron loss at rated speed, W | 0.6469 | 0.6449 (-0.31 %) | 0.6430 (-0.61 %) | 0.6480 (+0.16 %) | 0.6460 (-0.15 %) | 0.6479 (+0.15 %) |
| Stator iron loss at rated speed, W | 8.307 | 8.305 (-0.02 %) | 8.306 (-0.01 %) | 8.305 (-0.02 %) | 8.307 (-0.01 %) | 8.305 (-0.03 %) |
| Total loss at rated speed, W | 67.377 | 67.388 (+0.02 %) | 67.411 (+0.05 %) | 67.372 (-0.01 %) | 67.367 (-0.02 %) | 67.365 (-0.02 %) |
| Efficiency at rated (air-gap), % | 92.533 | 92.531 (-0.00 %) | 92.529 (-0.00 %) | 92.533 (+0.00 %) | 92.534 (+0.00 %) | 92.533 (+0.00 %) |
| Shaft eddy loss at 1.5x rated speed, W | 0.0168 | 0.0448 (+165.90 %) | 0.0759 (+350.70 %) | 0.0167 (-0.89 %) | 0.0097 (-42.18 %) | 0.0083 (-50.43 %) |
| Rotor iron loss at 1.5x rated speed, W | 1.2980 | 1.2950 (-0.23 %) | 1.2920 (-0.46 %) | 1.3000 (+0.16 %) | 1.2959 (-0.16 %) | 1.3000 (+0.16 %) |
| Total loss at 1.5x rated speed, W | 84.877 | 84.892 (+0.02 %) | 84.922 (+0.05 %) | 84.869 (-0.01 %) | 84.867 (-0.01 %) | 84.860 (-0.02 %) |
| Efficiency at 1.5x rated speed, % | 93.629 | 93.628 (-0.00 %) | 93.626 (-0.00 %) | 93.629 (+0.00 %) | 93.630 (+0.00 %) | 93.630 (+0.00 %) |

### CIANO14 40 L20 (12S, rated 52.55 A / 13000 rpm, hot magnet 113.6 C; 59.93 A assumed peak)

| quantity | Al 6061 | SS420 annealed | SS420 hardened | 42CrMo4 (passport) | 316 | ideal-soft bound |
|---|---:|---:|---:|---:|---:|---:|
| PM flux linkage, cold (20 C), no load | 1.6741 | 1.6745 (+0.02 %) | 1.6745 (+0.02 %) | 1.6745 (+0.02 %) | 1.6741 (+0.00 %) | 1.6745 (+0.02 %) |
| Back-EMF at 1000 rpm, V peak | 17.115 | 17.112 (-0.01 %) | 17.112 (-0.01 %) | 17.112 (-0.02 %) | 17.115 (+0.00 %) | 17.112 (-0.02 %) |
| Cogging torque p-p, cold, mN.m | 105.73 | 106.27 (+0.51 %) | 106.23 (+0.47 %) | 106.29 (+0.53 %) | 105.73 (+0.00 %) | 106.30 (+0.53 %) |
| Torque at the rated current (static), N.m | 1.2158 | 1.2154 (-0.03 %) | 1.2154 (-0.03 %) | 1.2154 (-0.03 %) | 1.2158 (+0.00 %) | 1.2154 (-0.03 %) |
| Kt at the rated current, N.m/A rms | 0.02314 | 0.02313 (-0.03 %) | 0.02313 (-0.03 %) | 0.02313 (-0.03 %) | 0.02314 (+0.00 %) | 0.02313 (-0.03 %) |
| Torque ripple p-p at rated (static), mN.m | 25.19 | 25.39 (+0.78 %) | 25.37 (+0.70 %) | 25.40 (+0.82 %) | 25.19 (+0.00 %) | 25.40 (+0.83 %) |
| Peak-current torque (static), N.m | 1.3711 | 1.3705 (-0.04 %) | 1.3706 (-0.04 %) | 1.3705 (-0.04 %) | 1.3711 (+0.00 %) | 1.3704 (-0.05 %) |
| Operating torque at rated speed (eddy + demag solve), N.m | 1.1885 | 1.1881 (-0.04 %) | 1.1882 (-0.03 %) | 1.1880 (-0.05 %) | 1.1885 (-0.00 %) | 1.1879 (-0.05 %) |
| Operating torque ripple p-p at rated speed, mN.m | 26.61 | 27.17 (+2.09 %) | 27.10 (+1.83 %) | 27.21 (+2.24 %) | 26.64 (+0.12 %) | 27.24 (+2.37 %) |
| Shaft eddy loss at rated speed, W | 0.0322 | 0.0805 (+150.07 %) | 0.1338 (+315.38 %) | 0.0344 (+6.77 %) | 0.0096 (-70.23 %) | 0.0199 (-38.12 %) |
| Magnet eddy loss at rated speed, W | 5.551 | 5.536 (-0.27 %) | 5.537 (-0.25 %) | 5.536 (-0.28 %) | 5.551 (+0.00 %) | 5.535 (-0.29 %) |
| Rotor iron loss at rated speed, W | 1.1080 | 1.1020 (-0.54 %) | 1.0999 (-0.73 %) | 1.1109 (+0.26 %) | 1.1050 (-0.28 %) | 1.1080 (-0.00 %) |
| Stator iron loss at rated speed, W | 13.126 | 13.125 (-0.01 %) | 13.125 (-0.01 %) | 13.124 (-0.02 %) | 13.126 (-0.00 %) | 13.124 (-0.02 %) |
| Total loss at rated speed, W | 158.408 | 158.449 (+0.03 %) | 158.499 (+0.06 %) | 158.411 (+0.00 %) | 158.383 (-0.02 %) | 158.395 (-0.01 %) |
| Efficiency at rated (air-gap), % | 91.083 | 91.077 (-0.01 %) | 91.076 (-0.01 %) | 91.079 (-0.00 %) | 91.084 (+0.00 %) | 91.079 (-0.00 %) |
| Shaft eddy loss at 1.5x rated speed, W | 0.0387 | 0.1132 (+192.72 %) | 0.1840 (+375.83 %) | 0.0474 (+22.69 %) | 0.0200 (-48.30 %) | 0.0268 (-30.70 %) |
| Rotor iron loss at 1.5x rated speed, W | 2.2020 | 2.1960 (-0.27 %) | 2.1900 (-0.54 %) | 2.2091 (+0.32 %) | 2.1951 (-0.31 %) | 2.2069 (+0.22 %) |
| Total loss at 1.5x rated speed, W | 185.672 | 185.736 (+0.03 %) | 185.801 (+0.07 %) | 185.685 (+0.01 %) | 185.647 (-0.01 %) | 185.662 (-0.01 %) |
| Efficiency at 1.5x rated speed, % | 92.876 | 92.871 (-0.01 %) | 92.869 (-0.01 %) | 92.872 (-0.00 %) | 92.876 (+0.00 %) | 92.873 (-0.00 %) |

### Reading the Ø40 tables

* **The SS420 shaft, in either condition, changes the motor by well under 0.1 %** in psi_PM, EMF, Kt, torque (rated, peak,
  operating, at 1.5 x speed), total loss and efficiency. The largest effects are the cogging torque (+0.5 %, absolute +0.3 mN.m
  on L12 and +0.5 mN.m on L20) and the operating torque ripple (+1.0 to +1.2 % on L12, +1.8 to +2.2 % on L20, a few tenths of
  a mN.m).
* **Annealed and hardened are indistinguishable** for these motors (torque, EMF, Kt and total loss within 0.03 %; only the 0.65 W rotor iron loss differs by 0.3 %), and so are
  the 42CrMo4 shaft and the **ideal-soft bound**: the shaft carries so little flux that even a perfect soft-iron shaft moves
  nothing more. The unknown hardened-state B-H curve therefore does not matter here.
* **The shaft eddy loss is tiny in absolute terms**: 0.014 W (Al) -> 0.032 W (annealed) / 0.056 W (hardened) on L12 at the
  rated speed; 0.032 -> 0.081 / 0.134 W on L20; at most +0.07 % of the total loss. The hardened shaft loses more because its
  lower permeability lets the slot-harmonic field penetrate deeper.
* **Austenitic 316 / 304 versus Al**: identical to the digit on every static quantity (non-magnetic is non-magnetic); only the shaft eddy loss falls (-65 % on
  L12, -70 % on L20) because sigma drops from 25 to 1.4 MS/m.
* Versus the **passport baseline (42CrMo4)**, which is what the Ø40 first-product passports were solved with: SS420 hardened
  moves cogging -0.06 %, ripple -0.1 %, psi_PM/Kt/torque 0.00 %, shaft eddy +0.04 W (0.06 % of the total loss).

### CIANO28 85 L13 and L15 (rated duty 25.88 A / 1000 rpm, hot override 69.2 / 75.1 C): 42CrMo4 (today) -> Al 6061 (rule)

L13 (13 mm stack):

| quantity | 42CrMo4 (passport) | Al 6061 |
|---|---:|---:|
| PM flux linkage, cold (20 C), no load | 7.5581 | 7.6413 (+1.10 %) |
| Back-EMF at 1000 rpm, V peak | 151.434 | 152.701 (+0.84 %) |
| Cogging torque p-p, cold, mN.m | 302.07 | 339.59 (+12.42 %) |
| Torque at the rated current (static), N.m | 5.5703 | 5.6617 (+1.64 %) |
| Kt at the rated current, N.m/A rms | 0.21524 | 0.21877 (+1.64 %) |
| Torque ripple p-p at rated (static), mN.m | 413.47 | 402.66 (-2.61 %) |
| Peak-current torque (static), N.m | 9.2462 | 9.5087 (+2.84 %) |
| Operating torque at rated speed (eddy + demag solve), N.m | 5.5209 | 5.6119 (+1.65 %) |
| Operating torque ripple p-p at rated speed, mN.m | 402.10 | 388.85 (-3.30 %) |
| Shaft eddy loss at rated speed, W | 0.4147 | 2.0076 (+384.09 %) |
| Magnet eddy loss at rated speed, W | 0.433 | 0.498 (+14.99 %) |
| Rotor iron loss at rated speed, W | 0.2139 | 0.1849 (-13.53 %) |
| Stator iron loss at rated speed, W | 2.404 | 2.466 (+2.57 %) |
| Total loss at rated speed, W | 159.380 | 161.357 (+1.24 %) |
| Efficiency at rated (air-gap), % | 78.390 | 78.458 (+0.09 %) |
| Shaft eddy loss at 1.5x rated speed, W | - | - |
| Rotor iron loss at 1.5x rated speed, W | - | - |
| Total loss at 1.5x rated speed, W | - | - |
| Efficiency at 1.5x rated speed, % | - | - |

L15 (15 mm stack):

| quantity | 42CrMo4 (passport) | Al 6061 |
|---|---:|---:|
| PM flux linkage, cold (20 C), no load | 8.7209 | 8.8168 (+1.10 %) |
| Back-EMF at 1000 rpm, V peak | 174.731 | 176.193 (+0.84 %) |
| Cogging torque p-p, cold, mN.m | 348.54 | 391.83 (+12.42 %) |
| Torque at the rated current (static), N.m | 6.4273 | 6.5327 (+1.64 %) |
| Kt at the rated current, N.m/A rms | 0.24835 | 0.25242 (+1.64 %) |
| Torque ripple p-p at rated (static), mN.m | 477.09 | 464.61 (-2.61 %) |
| Peak-current torque (static), N.m | 10.6687 | 10.9716 (+2.84 %) |
| Operating torque at rated speed (eddy + demag solve), N.m | 6.3702 | 6.4753 (+1.65 %) |
| Operating torque ripple p-p at rated speed, mN.m | 463.96 | 448.67 (-3.30 %) |
| Shaft eddy loss at rated speed, W | 0.4785 | 2.3165 (+384.09 %) |
| Magnet eddy loss at rated speed, W | 0.500 | 0.574 (+14.99 %) |
| Rotor iron loss at rated speed, W | 0.2469 | 0.2121 (-14.12 %) |
| Stator iron loss at rated speed, W | 2.774 | 2.847 (+2.62 %) |
| Total loss at rated speed, W | 172.023 | 174.305 (+1.33 %) |
| Efficiency at rated (air-gap), % | 79.499 | 79.551 (+0.07 %) |
| Shaft eddy loss at 1.5x rated speed, W | - | - |
| Rotor iron loss at 1.5x rated speed, W | - | - |
| Total loss at 1.5x rated speed, W | - | - |
| Efficiency at 1.5x rated speed, % | - | - |

(The L13 / L15 duty current is far above the continuous rating; the table is a like-for-like A/B at the passport's rated duty,
not an operating prediction.)

### Reading the Ø85 tables

* **Here the shaft steel matters**: going from the magnetic 42CrMo4 shaft to aluminium raises psi_PM by 1.1 % (EMF +0.84 %), the
  rated and operating torque and Kt by 1.65 %, the peak-current torque by 2.8 % and the cogging torque by 12.4 %, while the
  operating ripple falls by 3.3 %. The 42CrMo4 shaft shunts part of the magnet flux inside the rotor; aluminium does not.
* The shaft eddy loss rises about 5 times (0.41 -> 2.0 W on L13, 0.48 -> 2.3 W on L15) because aluminium carries the
  slot-harmonic currents the steel shaft screens; the total loss at this duty rises 1.2-1.3 %, the magnet eddy loss 15 %, the
  rotor iron loss falls 14 %; the efficiency moves +0.1 %.
* L13 and L15 give the same percentages (the shaft effect scales with the stack length).
* **The Ø85 passports (solved 2026-10-14 with `Steel_42CrMo4_QT`) are therefore stale for the rule "Ø85 = Al 6061"**: the
  torque, EMF, Kt, cogging and shaft-loss figures need a re-solve once the dies are changed (about 1.1-2.8 % on the torque
  family, 12 % on cogging).

## Mass (nothing applied)

Density 7720 kg/m3 (SS420) against 2700 (Al): the shaft gets 2.86 times heavier - on the Ø40 simulation shaft tube 4.3 -> 12.3 g
(L12) and 5.3 -> 15.2 g (L20), i.e. about +8.0 g and +9.9 g of full motor mass **against an aluminium shaft**. Against the
`Steel_42CrMo4_QT` the server's L20 and Ø85 dies name today (7720 kg/m3) the mass does not change. The Full and Active masses pick
the card's density as soon as a die names it (the CAD shaft is rescaled by the density ratio); the Ø85 shaft tube falls from
29.8 g to 10.4 g when it goes to aluminium. 100 mm and 150 mm public dies already use aluminium 6061.

Re-run `python scripts/shaft_material_proposal.py --dies-dir <catalog>` for the current table of a catalog.
