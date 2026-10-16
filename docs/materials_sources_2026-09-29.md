# Materials library from independent sources - audit 2026-09-29

Owner decision: remove everything taken from Ansys (built-in library, examples,
values copied through Maxwell) from `config/materials_library.yaml` and base
every value on an independent, citable source. Branch
`chore/materials-independent` off `pre-migration-freeze-2026-09-15`.
No re-solves were run; the orchestrator schedules recalculations.

## What was Ansys-origin

The library's first commit (2026-05-28) was "extracted from Ansys Maxwell
PersonalLib (My_lib.amat)". Traced through git history, these entries came in
that way: Somaloy_700HR_5P (Bertotti coefficients), both VACODUR 49 cards
(curves, coefficients, k 32.83), JFE_10JNEX900, JFE_Steel_20JNEH1200 and
20SW1200 (curve point sets, Maxwell-fitted kh/kc/ke), the N52UH 100/150 C pair
(Br 1.30/1.22, knees -1155/-730 kA/m) from which N52UH_20C was later derived,
F45SH_120C (Br 1.19, from which the F52SH trio's alpha/beta were derived), and
copper's density 8933 kg/m3 (the Maxwell built-in copper). B15AHV950M and
20RSW175 also started as "MagWeb / Granta Design (ANSYS)" data but had already
been replaced with the suppliers' own workbooks on 2026-08-04.

## Counts (44 materials)

| Class | n | Materials |
|---|---|---|
| Rebuilt / values replaced from an independent source | 23 | Somaloy_700HR_5P, VACODUR_49 x2, Steel_42CrMo4_QT, Steel_34CrNiMo6_QT, N52UH_20C/100C/150C, F52SH_30C/80C/120C, F45SH_120C, copper, Aluminium_6061, Aluminium_7075, Stainless_316L, polyimide, air, ethylene_glycol, 20SW1200 (description + refit), JFE_10JNEX900 and JFE_Steel_20JNEH1200 (refit only) |
| Matches the source - citation added, no value change | 7 | B15AHV950M, B10AHV900M, 20RSW175 (supplier workbooks), Aluminium_1060, Titanium_Ti6Al4V, Nomex, water |
| No independent source found / not re-read - `verify: true` | 14 | Aluminium (generic), Al2O3, AlN, oil, water_glycol_50, water_glycol_50_65c, rp3_kerosene, T800/HM63/M40X/M55J_UD_60, N45EH_150C/180C, Fe16N2 x2 (literature by design) |

129 field-level value changes (table below). Every card now carries `sources:`
and per-field `prov:` in the catalog-envelope format
(`src/motor_ai_sim/catalog/envelope.py`: a field without a prov entry is
`datasheet` from `sources[0]`; `derived` and `estimate` entries carry a note;
`verify: true` marks open items). The loader ignores the new keys, so nothing
else moves.

## Findings worth a look before the recalculation

1. **VACODUR_49_0p20mm_390MPa iron loss was ~60x too low.** Its curves were
   stored as W/m3 but held numbers that only make sense as a different unit;
   the solver fits kh/kc/ke from the curves, so 1.5 T / 400 Hz billed
   0.46 W/kg against VAC's 28 W/kg. Now the six VAC datasheet points (0.20 mm,
   mechanically optimised). No saved die uses this card.
2. **N52UH (4 live dies)**: rebuilt from the Arnold G52UH sheet
   (Br20 1.43 T, HcJ20 >= 1990 kA/m, alpha -0.12, beta -0.51 %/K). At 150 C
   Br 1.22 -> 1.207 T (-1.1 %), knee -730 -> -670.6 kA/m (8 % less demag
   margin), sigma +20 % (150 vs 180 uOhm cm), density 7500 -> 7600.
3. **F52SH (9 live dies)**: rebuilt from Arnold G52SH (Br 1.44, HcJ >= 1592,
   beta -0.55). At 120 C Br -0.2 %, knee -649 -> -716 kA/m (10 % more margin),
   sigma +6.7 %.
4. **Copper (all dies)**: thermal_alpha 0.0043 -> 0.00393 1/K (IEC 60028 value
   at the 20 C reference the code uses; 0.0043 is the 0 C-referenced value).
   Hot winding resistance and copper loss -3.1 % at 150 C. Density 8933 -> 8890.
5. **42CrMo4 shaft (5 live dies)**: sigma 4.4 -> 5.26 MS/m (DEW 0.19 Ohm mm2/m):
   solid-shaft eddy loss up to +20 %.
6. **20SW1200 is a Shougang grade**, not JFE (description fixed). Its curves
   reproduce Shougang's typical anchors (P1.0/400 11.21 vs 11.0 W/kg, B50 1.639
   vs 1.63 T) but the point set entered through the PersonalLib - ask Shougang
   for the curve workbook, as for 20RSW175.
7. Somaloy: TRS 120 -> 60 MPa (Hoganas p.5; 120 was not in any sheet).

## Open items for the owner (verify: true)

- Supplier sheets for the magnets actually bought: Ningbo Permanent Magnetics
  N52UH and 45SH-F (the cards now use Arnold's same-grade sheets as the
  independent reference). NPM's sheet may differ in HcJ and resistivity.
- Curve files from JFE (10JNEX900: Cat. F2E-001; 20JNEH1200) and Shougang
  (20SW1200) to replace the PersonalLib-origin point sets (they match the
  makers' anchors within 2.4 %).
- VACODUR 49: specific heat 410, stacking factors 0.95/0.94 - not in the VAC
  documents read.
- Magnet demag-curve squareness (knee at 0.97 HcJ) is an estimate; HcJ is the
  datasheet minimum.
- QT shaft steels: B-H curve has no published source (measure the bar).
- Lamination mechanical data (E, nu, CTE, yield) for all electrical steels:
  not in the supplier documents.
- N45EH: no maker sheet found (Arnold stops at N42EH); values checked against
  class ranges only.
- Al2O3 / AlN liners, CFRP sleeves (laminate values derived from fibre data),
  generic oil, 50 % glycol at 20 C, ASHRAE / CRC citations not re-read,
  Nomex specific heat, generic "Aluminium" card (mixes pure-Al conductivity
  with alloy density/k/yield), NdFeB Poisson ratio.

## Tests

Updated pins, each because a material value changed:
`test_magnet_temperature` (N52UH 100->150 C: Br 1.2200 -> 1.2069 T, knee
-730 -> -670.6 kA/m), `test_masses` (copper 8933 -> 8890 kg/m3),
`test_mechanical_thermal` and `test_mechanical_part_temps` (magnet CTE
5/-1.5 -> 7/-1 ppm/K), `test_mechanical_seating` (FROZEN_20C pressure
29.205 -> 29.576 MPa and G2_COLD re-measured: magnet density and CTE),
`test_thermal_robotics` (a 1 mW rounding tolerance the new numbers exposed).
The catalog envelope golden files cover bearings / lubricants / devices only -
no golden change was needed. `test_mechanical_part_temps::test_d_...` fails
identically on the base materials (pre-existing, unrelated).
Not run (solver-level, will move with the new numbers, orchestrator's
recalculation): `test_physics_regression`, `test_report`, static3d, coupled.

## Sources (read 2026-09-29)

- Hoganas, Somaloy 5P Material data 2274HOG-3 (2025), p.5; Hoganas curve-fitting workbook (owner's file)
- VACUUMSCHMELZE, VACODUR 49 datasheet, July 2022, p.2; VAC CoFe brochure (2021), p.8
- JFE Super Core catalog F1E-002 (2014), p.5/7/10; JFE Technical Report No.31 (2024), p.13 Table 1
- Beijing Shougang NGO product handbook (2024-12), p.8
- Baowu B15AHV950M / B10AHV900M product info 2025 + curve workbooks; Shougang 20RSW175 workbook (owner's files)
- Deutsche Edelstahlwerke 34CrNiMo6 (2018-12) and 42CrMo4 data sheets; EN 10083-3; EN 1993-1-1
- Arnold G52UH and G52SH sheets Rev. 210607; Arnold Neo catalog 151021 (N45SH p.40)
- HGT Advanced Magnets, Sintered NdFeB specifications (2020); Courage Magnet N45EH page (2024)
- IEC 60028; CDA C11000; ASM Handbook Vol. 2 (Al alloys); ASTM A240 / ASM Vol. 1 (316L); ASM Titanium Alloys handbook
- DuPont Kapton HN TDS (2006); DuPont Nomex 410 TDS (2000) Table IV
- Incropera et al., 7th ed., Tables A.4 / A.5; NIST WebBook (water, IAPWS-95)

URLs, pages and revisions are on each card's `sources:` list.

## Change table

| Material | Field | Old | New | Source | Impact (estimate, no re-solve) | Saved dies using it |
|---|---|---|---|---|---|---|
| Somaloy_700HR_5P | density | 7650 | 7500 | hoganas_5p: p.5: 7.50 g/cm3 (was 7650, Ansys PersonalLib) |  | not used by a live die |
| Somaloy_700HR_5P | youngs_modulus_gpa | 160 | 150 | hoganas_5p: p.5: 150 GPa (ASTM E1876) | mechanical / thermal input | not used by a live die |
| Somaloy_700HR_5P | poisson_ratio | 0.28 | 0.23 | hoganas_5p: p.5: 0.23 | mechanical / thermal input | not used by a live die |
| Somaloy_700HR_5P | cte_ppm_k | 12.0 | 11.0 | hoganas_5p: p.5: 11e-6 /K | mechanical / thermal input | not used by a live die |
| Somaloy_700HR_5P | yield_strength_mpa | 120 | 60 | hoganas_5p: p.5: TRS 60 MPa (ambient and 150 C); tensile 20, compressive yield 110 | mechanical / thermal input | not used by a live die |
| Somaloy_700HR_5P | thermal_conductivity | null | 21 | hoganas_5p: p.5: 21 W/mK (ISO 22007-2) | mechanical / thermal input | not used by a live die |
| Somaloy_700HR_5P | bh_curve | 21 points (previous card) | 22 points = Hoganas p.5 table (the old 21 points were the sa | hoganas_5p: p.5 'Magnetising curve - data adjusted for use in FE modelling', full 22-point table |  | not used by a live die |
| VACODUR_49_0p20mm_390MPa | youngs_modulus_gpa | 210 | 250 | vac_flyer: p.2 final annealed, mechanically optimised (750 C / 3 h): E 250 GPa | mechanical / thermal input | not used by a live die |
| VACODUR_49_0p20mm_390MPa | cte_ppm_k | 12.0 | 8.9 | vac_flyer: p.2: 8.9e-6/K (20-100 C); the old 12.0 was the generic steel figure | mechanical / thermal input | not used by a live die |
| VACODUR_49_0p20mm_390MPa | thermal_conductivity | 32.83 | 32 | vac_flyer: p.2: 32 W/mK at 25 C (was 32.83, Ansys PersonalLib) | mechanical / thermal input | not used by a live die |
| VACODUR_49_0p20mm_390MPa | core_loss_curve_unit | w_per_cubic_meter | w_per_kg | vac_flyer: flyer losses are W/kg |  | not used by a live die |
| VACODUR_49_0p20mm_390MPa | bh_curve | 36 points (previous card) | 6 datasheet points + origin + 3 saturation points (mu0 slope | vac_cofe: brochure p.8 typical B at 300/800/1600/4000/8000/16000 A/m, mechanically optimised (750 C / 3 h); above 16 kA/m B = 2.30 T + mu0 (H - 16 kA/m) (saturated, Js 2.30 T) |  | not used by a live die |
| VACODUR_49_0p20mm_390MPa | core_loss_curves | 16 points (previous card) | 6 datasheet points: 1.5 T and 2.0 T at 50 / 400 / 1000 Hz, W | vac_flyer: p.2 specific iron losses, 0.20 mm strip, mechanically optimised (750 C / 3 h) | iron loss was ~60x TOO LOW (curves stored in the wrong unit): 1.5 T/400 Hz 0.46 -> 27.6 W/kg; no saved die uses it | not used by a live die |
| VACODUR_49_0p20mm_210MPa | youngs_modulus_gpa | 210 | 200 | vac_flyer: p.2 final annealed, magnetically optimised (880 C / 6 h): E 200 GPa | mechanical / thermal input | not used by a live die |
| VACODUR_49_0p20mm_210MPa | cte_ppm_k | 12.0 | 8.9 | vac_flyer: p.2: 8.9e-6/K (20-100 C); the old 12.0 was the generic steel figure | mechanical / thermal input | not used by a live die |
| VACODUR_49_0p20mm_210MPa | thermal_conductivity | 32.83 | 32 | vac_flyer: p.2: 32 W/mK at 25 C (was 32.83, Ansys PersonalLib) | mechanical / thermal input | not used by a live die |
| VACODUR_49_0p20mm_210MPa | bh_curve | 36 points (previous card) | 6 datasheet points + origin + 3 saturation points (mu0 slope | vac_cofe: brochure p.8 typical B at 300/800/1600/4000/8000/16000 A/m, magnetically optimised (880 C / 6 h); above 16 kA/m B = 2.30 T + mu0 (H - 16 kA/m) (saturated, Js 2.30 T) |  | not used by a live die |
| VACODUR_49_0p20mm_210MPa | core_loss_curves | 20 points (previous card) | 6 datasheet points: 1.5 T and 2.0 T at 50 / 400 / 1000 Hz, W | vac_flyer: p.2 specific iron losses, 0.20 mm strip, magnetically optimised (880 C / 6 h) | iron loss +11 % (400 Hz) to +16 % (1 kHz) at 1.5 T; no saved die uses it | not used by a live die |
| 20SW1200 | description | "JFE Steel 20SW1200 — non-oriented electrical steel, 0.20 mm | Shougang 20SW1200 ... | shougang_2024: 20SW1200 is a Shougang grade (handbook p.8), not JFE |  | CIANO14 40 new L12, L20; CIANO14 50 edited L15; CIANO28 150_35 new L35; CIANO28 85 20SW1200 L13 |
| Steel_42CrMo4_QT | density | 7850 | 7720 | dew_42crmo4: DEW data sheet: 7.72 kg/dm3 (was 7850, generic) | shaft mass -1.7 % | CIANO10 200 opt L155 motor, L180 gen; CIANO14 50 edited L15; CIANO28 85 20SW1200 L13; CILN28 G2-L40 |
| Steel_42CrMo4_QT | sigma | 4.4e+06 | 5263158.0 | dew_42crmo4: DEW: 0.19 Ohm mm2/m at 20 C -> 5.263e6 S/m | solid-shaft eddy loss up to +20 % (resistance-limited) | CIANO10 200 opt L155 motor, L180 gen; CIANO14 50 edited L15; CIANO28 85 20SW1200 L13; CILN28 G2-L40 |
| Steel_42CrMo4_QT | thermal_conductivity | 42 | 42.6 | dew_42crmo4: DEW: 42.6 W/mK at 20 C | mechanical / thermal input | CIANO10 200 opt L155 motor, L180 gen; CIANO14 50 edited L15; CIANO28 85 20SW1200 L13; CILN28 G2-L40 |
| Steel_42CrMo4_QT | cte_ppm_k | 12.3 | 11.1 | dew_42crmo4: DEW: 11.1e-6/K (20-100 C) | shaft thermal growth -10 % (fits) | CIANO10 200 opt L155 motor, L180 gen; CIANO14 50 edited L15; CIANO28 85 20SW1200 L13; CILN28 G2-L40 |
| Steel_34CrNiMo6_QT | density | 7850 | 7730 | dew_34crnimo6: DEW data sheet: 7.73 kg/dm3 (was 7850, generic) |  | not used by a live die |
| Steel_34CrNiMo6_QT | sigma | 4e+06 | 5263158.0 | dew_34crnimo6: DEW: 0.19 Ohm mm2/m at 20 C -> 5.263e6 S/m |  | not used by a live die |
| Steel_34CrNiMo6_QT | thermal_conductivity | 40 | 42.6 | dew_34crnimo6: DEW: 42.6 W/mK at 20 C | mechanical / thermal input | not used by a live die |
| Steel_34CrNiMo6_QT | cte_ppm_k | 12.0 | 11.1 | dew_34crnimo6: DEW: 11.1e-6/K (20-100 C) | mechanical / thermal input | not used by a live die |
| N52UH_150C | Br | 1.22 | 1.2069 | arnold_g52uh: Br20 1.43 T x (1 -0.12 %/K x 130 K) | Br -1.1 % -> torque / EMF ~ -1 % | CIANO10 200 L160; CIANO10 200 opt L155 motor, L180 gen; CILN28 G2-L40 (+ saved_simulations, end_effect_passports) |
| N52UH_150C | Hc | 924614 | 914701 | arnold_g52uh: Br / (mu0 mu_rec), mu_rec 1.05 |  | CIANO10 200 L160; CIANO10 200 opt L155 motor, L180 gen; CILN28 G2-L40 (+ saved_simulations, end_effect_passports) |
| N52UH_150C | alpha_br_pct_per_k | -0.1311 | -0.1422 | arnold_g52uh: datasheet alpha -0.12 %/K re-referenced to 150 C |  | CIANO10 200 L160; CIANO10 200 opt L155 motor, L180 gen; CILN28 G2-L40 (+ saved_simulations, end_effect_passports) |
| N52UH_150C | beta_hcj_pct_per_k | -1.1644 | -1.5134 | arnold_g52uh: datasheet beta -0.51 %/K re-referenced to 150 C |  | CIANO10 200 L160; CIANO10 200 opt L155 motor, L180 gen; CILN28 G2-L40 (+ saved_simulations, end_effect_passports) |
| N52UH_150C | sigma | 555555.6 | 666666.7 | arnold_g52uh: rho 150 uOhm cm parallel to C -> 6.667e5 S/m (was 5.556e5 = 180 uOhm cm) | magnet eddy loss +20 % | CIANO10 200 L160; CIANO10 200 opt L155 motor, L180 gen; CILN28 G2-L40 (+ saved_simulations, end_effect_passports) |
| N52UH_150C | density | 7500 | 7600 | arnold_g52uh: 7.6 g/cm3 (was 7500) | magnet mass / centrifugal load +1.3 % | CIANO10 200 L160; CIANO10 200 opt L155 motor, L180 gen; CILN28 G2-L40 (+ saved_simulations, end_effect_passports) |
| N52UH_150C | thermal_conductivity | 7.6 | 6.16 | arnold_g52uh: 5.3 kcal/(m h C) parallel = 6.16 W/mK (was 7.6) | magnet k -19 % (slightly hotter magnets) | CIANO10 200 L160; CIANO10 200 opt L155 motor, L180 gen; CILN28 G2-L40 (+ saved_simulations, end_effect_passports) |
| N52UH_150C | cte_ppm_k_1 | 5.0 | 7.0 | arnold_g52uh: CTE parallel to magnetisation (20-200 C) | mechanical / thermal input | CIANO10 200 L160; CIANO10 200 opt L155 motor, L180 gen; CILN28 G2-L40 (+ saved_simulations, end_effect_passports) |
| N52UH_150C | cte_ppm_k_2 | -1.5 | -1.0 | arnold_g52uh: CTE perpendicular (20-200 C) | mechanical / thermal input | CIANO10 200 L160; CIANO10 200 opt L155 motor, L180 gen; CILN28 G2-L40 (+ saved_simulations, end_effect_passports) |
| N52UH_150C | bh_curve | 30 points (previous card) | 17 points, HcJ(150 C) = 670.6 kA/m, solver knee -670.6 kA/m | arnold_g52uh: generated from datasheet Br20/HcJ20/alpha/beta at 150 C: Br 1.2069 T, HcJ 670.6 kA/m, recoil mu_rec 1.05, knee at 0.97 HcJ | demag knee -730 -> -670.6 kA/m (8 % less margin) | CIANO10 200 L160; CIANO10 200 opt L155 motor, L180 gen; CILN28 G2-L40 (+ saved_simulations, end_effect_passports) |
| N52UH_20C | Br | 1.4279 | 1.43 | arnold_g52uh: Br20 1.43 T x (1 -0.12 %/K x 0 K) |  | not used by a live die |
| N52UH_20C | Hc | 1082196 | 1083769 | arnold_g52uh: Br / (mu0 mu_rec), mu_rec 1.05 |  | not used by a live die |
| N52UH_20C | alpha_br_pct_per_k | -0.112 | -0.12 | arnold_g52uh: datasheet alpha -0.12 %/K re-referenced to 20 C |  | not used by a live die |
| N52UH_20C | beta_hcj_pct_per_k | -0.463 | -0.51 | arnold_g52uh: datasheet beta -0.51 %/K re-referenced to 20 C |  | not used by a live die |
| N52UH_20C | sigma | 555555.6 | 666666.7 | arnold_g52uh: rho 150 uOhm cm parallel to C -> 6.667e5 S/m (was 5.556e5 = 180 uOhm cm) |  | not used by a live die |
| N52UH_20C | density | 7500 | 7600 | arnold_g52uh: 7.6 g/cm3 (was 7500) |  | not used by a live die |
| N52UH_20C | thermal_conductivity | 7.6 | 6.16 | arnold_g52uh: 5.3 kcal/(m h C) parallel = 6.16 W/mK (was 7.6) | mechanical / thermal input | not used by a live die |
| N52UH_20C | cte_ppm_k_1 | 5.0 | 7.0 | arnold_g52uh: CTE parallel to magnetisation (20-200 C) | mechanical / thermal input | not used by a live die |
| N52UH_20C | cte_ppm_k_2 | -1.5 | -1.0 | arnold_g52uh: CTE perpendicular (20-200 C) | mechanical / thermal input | not used by a live die |
| N52UH_20C | bh_curve | 26 points (previous card) | 17 points, HcJ(20 C) = 1990.0 kA/m, solver knee -1990.0 kA/m | arnold_g52uh: generated from datasheet Br20/HcJ20/alpha/beta at 20 C: Br 1.4300 T, HcJ 1990.0 kA/m, recoil mu_rec 1.05, knee at 0.97 HcJ |  | not used by a live die |
| N52UH_100C | Br | 1.30 | 1.2927 | arnold_g52uh: Br20 1.43 T x (1 -0.12 %/K x 80 K) |  | not used by a live die |
| N52UH_100C | Hc | 983000 | 979728 | arnold_g52uh: Br / (mu0 mu_rec), mu_rec 1.05 |  | not used by a live die |
| N52UH_100C | mu_rec | 1.052 | 1.05 | hgt_ndfeb: HGT Table IV: 1.05 |  | not used by a live die |
| N52UH_100C | alpha_br_pct_per_k | -0.1231 | -0.1327 | arnold_g52uh: datasheet alpha -0.12 %/K re-referenced to 100 C |  | not used by a live die |
| N52UH_100C | beta_hcj_pct_per_k | -0.7359 | -0.8615 | arnold_g52uh: datasheet beta -0.51 %/K re-referenced to 100 C |  | not used by a live die |
| N52UH_100C | sigma | 555555.6 | 666666.7 | arnold_g52uh: rho 150 uOhm cm parallel to C -> 6.667e5 S/m (was 5.556e5 = 180 uOhm cm) |  | not used by a live die |
| N52UH_100C | density | 7500 | 7600 | arnold_g52uh: 7.6 g/cm3 (was 7500) |  | not used by a live die |
| N52UH_100C | thermal_conductivity | 7.6 | 6.16 | arnold_g52uh: 5.3 kcal/(m h C) parallel = 6.16 W/mK (was 7.6) | mechanical / thermal input | not used by a live die |
| N52UH_100C | cte_ppm_k_1 | 5.0 | 7.0 | arnold_g52uh: CTE parallel to magnetisation (20-200 C) | mechanical / thermal input | not used by a live die |
| N52UH_100C | cte_ppm_k_2 | -1.5 | -1.0 | arnold_g52uh: CTE perpendicular (20-200 C) | mechanical / thermal input | not used by a live die |
| N52UH_100C | bh_curve | 29 points (previous card) | 17 points, HcJ(100 C) = 1178.1 kA/m, solver knee -1178.1 kA/ | arnold_g52uh: generated from datasheet Br20/HcJ20/alpha/beta at 100 C: Br 1.2927 T, HcJ 1178.1 kA/m, recoil mu_rec 1.05, knee at 0.97 HcJ |  | not used by a live die |
| F52SH_120C | Br | 1.27 | 1.2672 | arnold_g52sh: Br20 1.44 T x (1 -0.12 %/K x 100 K) | Br -0.2 % | CIANO14 40 new L12, L20; CIANO14 50 edited L15; CIANO28 150_35 L35 (+new); CIANO28 85 20RSW175 L13; CIANO28 85 20SW1200 L13; CILN28 G1-L160, M1-L220 |
| F52SH_120C | Hc | 962525 | 960386 | arnold_g52sh: Br / (mu0 mu_rec), mu_rec 1.05 |  | CIANO14 40 new L12, L20; CIANO14 50 edited L15; CIANO28 150_35 L35 (+new); CIANO28 85 20RSW175 L13; CIANO28 85 20SW1200 L13; CILN28 G1-L160, M1-L220 |
| F52SH_120C | alpha_br_pct_per_k | -0.1344 | -0.1364 | arnold_g52sh: datasheet alpha -0.12 %/K re-referenced to 120 C |  | CIANO14 40 new L12, L20; CIANO14 50 edited L15; CIANO28 150_35 L35 (+new); CIANO28 85 20RSW175 L13; CIANO28 85 20SW1200 L13; CILN28 G1-L160, M1-L220 |
| F52SH_120C | beta_hcj_pct_per_k | -1.0000 | -1.2222 | arnold_g52sh: datasheet beta -0.55 %/K re-referenced to 120 C |  | CIANO14 40 new L12, L20; CIANO14 50 edited L15; CIANO28 150_35 L35 (+new); CIANO28 85 20RSW175 L13; CIANO28 85 20SW1200 L13; CILN28 G1-L160, M1-L220 |
| F52SH_120C | sigma | 6.25e5 | 666666.7 | arnold_g52sh: rho 150 uOhm cm parallel -> 6.667e5 S/m (was 6.25e5) | magnet eddy loss +6.7 % | CIANO14 40 new L12, L20; CIANO14 50 edited L15; CIANO28 150_35 L35 (+new); CIANO28 85 20RSW175 L13; CIANO28 85 20SW1200 L13; CILN28 G1-L160, M1-L220 |
| F52SH_120C | density | 7500 | 7600 | arnold_g52sh: 7.6 g/cm3 (was 7500) | magnet mass +1.3 % | CIANO14 40 new L12, L20; CIANO14 50 edited L15; CIANO28 150_35 L35 (+new); CIANO28 85 20RSW175 L13; CIANO28 85 20SW1200 L13; CILN28 G1-L160, M1-L220 |
| F52SH_120C | thermal_conductivity | 7.6 | 6.16 | arnold_g52sh: 5.3 kcal/(m h C) parallel = 6.16 W/mK (was 7.6) | magnet k -19 % | CIANO14 40 new L12, L20; CIANO14 50 edited L15; CIANO28 150_35 L35 (+new); CIANO28 85 20RSW175 L13; CIANO28 85 20SW1200 L13; CILN28 G1-L160, M1-L220 |
| F52SH_120C | cte_ppm_k_1 | 5.0 | 7.0 | arnold_g52sh: CTE parallel to magnetisation (20-200 C) | mechanical / thermal input | CIANO14 40 new L12, L20; CIANO14 50 edited L15; CIANO28 150_35 L35 (+new); CIANO28 85 20RSW175 L13; CIANO28 85 20SW1200 L13; CILN28 G1-L160, M1-L220 |
| F52SH_120C | cte_ppm_k_2 | -1.5 | -1.0 | arnold_g52sh: CTE perpendicular (20-200 C) | mechanical / thermal input | CIANO14 40 new L12, L20; CIANO14 50 edited L15; CIANO28 150_35 L35 (+new); CIANO28 85 20RSW175 L13; CIANO28 85 20SW1200 L13; CILN28 G1-L160, M1-L220 |
| F52SH_120C | bh_curve | (absent) | 17 points, HcJ(120 C) = 716.4 kA/m, solver knee -716.4 kA/m | arnold_g52sh: generated from datasheet Br20/HcJ20/alpha/beta at 120 C: Br 1.2672 T, HcJ 716.4 kA/m, recoil mu_rec 1.05, knee at 0.97 HcJ | demag knee -649 -> -716 kA/m (10 % MORE margin) | CIANO14 40 new L12, L20; CIANO14 50 edited L15; CIANO28 150_35 L35 (+new); CIANO28 85 20RSW175 L13; CIANO28 85 20SW1200 L13; CILN28 G1-L160, M1-L220 |
| F52SH_30C | Br | 1.428 | 1.4227 | arnold_g52sh: Br20 1.44 T x (1 -0.12 %/K x 10 K) |  | not used by a live die |
| F52SH_30C | Hc | 1082200 | 1078252 | arnold_g52sh: Br / (mu0 mu_rec), mu_rec 1.05 |  | not used by a live die |
| F52SH_30C | alpha_br_pct_per_k | -0.1199 | -0.1215 | arnold_g52sh: datasheet alpha -0.12 %/K re-referenced to 30 C |  | not used by a live die |
| F52SH_30C | beta_hcj_pct_per_k | -0.5263 | -0.582 | arnold_g52sh: datasheet beta -0.55 %/K re-referenced to 30 C |  | not used by a live die |
| F52SH_30C | sigma | 625000.0 | 666666.7 | arnold_g52sh: rho 150 uOhm cm parallel -> 6.667e5 S/m (was 6.25e5) |  | not used by a live die |
| F52SH_30C | density | 7500 | 7600 | arnold_g52sh: 7.6 g/cm3 (was 7500) |  | not used by a live die |
| F52SH_30C | thermal_conductivity | 7.6 | 6.16 | arnold_g52sh: 5.3 kcal/(m h C) parallel = 6.16 W/mK (was 7.6) | mechanical / thermal input | not used by a live die |
| F52SH_30C | cte_ppm_k_1 | 5.0 | 7.0 | arnold_g52sh: CTE parallel to magnetisation (20-200 C) | mechanical / thermal input | not used by a live die |
| F52SH_30C | cte_ppm_k_2 | -1.5 | -1.0 | arnold_g52sh: CTE perpendicular (20-200 C) | mechanical / thermal input | not used by a live die |
| F52SH_30C | bh_curve | (absent) | 17 points, HcJ(30 C) = 1504.4 kA/m, solver knee -1504.4 kA/m | arnold_g52sh: generated from datasheet Br20/HcJ20/alpha/beta at 30 C: Br 1.4227 T, HcJ 1504.4 kA/m, recoil mu_rec 1.05, knee at 0.97 HcJ |  | not used by a live die |
| F52SH_80C | Br | 1.342 | 1.3363 | arnold_g52sh: Br20 1.44 T x (1 -0.12 %/K x 60 K) |  | not used by a live die |
| F52SH_80C | Hc | 1017300 | 1012771 | arnold_g52sh: Br / (mu0 mu_rec), mu_rec 1.05 |  | not used by a live die |
| F52SH_80C | alpha_br_pct_per_k | -0.1276 | -0.1293 | arnold_g52sh: datasheet alpha -0.12 %/K re-referenced to 80 C |  | not used by a live die |
| F52SH_80C | beta_hcj_pct_per_k | -0.7143 | -0.8209 | arnold_g52sh: datasheet beta -0.55 %/K re-referenced to 80 C |  | not used by a live die |
| F52SH_80C | sigma | 625000.0 | 666666.7 | arnold_g52sh: rho 150 uOhm cm parallel -> 6.667e5 S/m (was 6.25e5) |  | not used by a live die |
| F52SH_80C | density | 7500 | 7600 | arnold_g52sh: 7.6 g/cm3 (was 7500) |  | not used by a live die |
| F52SH_80C | thermal_conductivity | 7.6 | 6.16 | arnold_g52sh: 5.3 kcal/(m h C) parallel = 6.16 W/mK (was 7.6) | mechanical / thermal input | not used by a live die |
| F52SH_80C | cte_ppm_k_1 | 5.0 | 7.0 | arnold_g52sh: CTE parallel to magnetisation (20-200 C) | mechanical / thermal input | not used by a live die |
| F52SH_80C | cte_ppm_k_2 | -1.5 | -1.0 | arnold_g52sh: CTE perpendicular (20-200 C) | mechanical / thermal input | not used by a live die |
| F52SH_80C | bh_curve | (absent) | 17 points, HcJ(80 C) = 1066.6 kA/m, solver knee -1066.6 kA/m | arnold_g52sh: generated from datasheet Br20/HcJ20/alpha/beta at 80 C: Br 1.3363 T, HcJ 1066.6 kA/m, recoil mu_rec 1.05, knee at 0.97 HcJ |  | not used by a live die |
| F45SH_120C | Br | 1.190 | 1.188 | arnold_n45sh: Br20 1.35 T x (1 -0.12 %/K x 100 K) |  | no live die (motor_catalog.json, end_effect_3d.json) |
| F45SH_120C | Hc | 901878 | 900362 | arnold_n45sh: Br / (mu0 mu_rec), mu_rec 1.05 |  | no live die (motor_catalog.json, end_effect_3d.json) |
| F45SH_120C | alpha_br_pct_per_k | -0.1344 | -0.1364 | arnold_n45sh: datasheet alpha -0.12 %/K re-referenced to 120 C |  | no live die (motor_catalog.json, end_effect_3d.json) |
| F45SH_120C | beta_hcj_pct_per_k | -1.0000 | -1.1505 | arnold_n45sh: datasheet beta -0.535 %/K re-referenced to 120 C |  | no live die (motor_catalog.json, end_effect_3d.json) |
| F45SH_120C | sigma | 6.25e5 | 555555.6 | arnold_n45sh: rho 180 uOhm cm -> 5.556e5 S/m (was 6.25e5) |  | no live die (motor_catalog.json, end_effect_3d.json) |
| F45SH_120C | cte_ppm_k_1 | 5.0 | 7.5 | arnold_n45sh: CTE parallel to magnetisation (20-200 C) | mechanical / thermal input | no live die (motor_catalog.json, end_effect_3d.json) |
| F45SH_120C | cte_ppm_k_2 | -1.5 | -0.1 | arnold_n45sh: CTE perpendicular (20-200 C) | mechanical / thermal input | no live die (motor_catalog.json, end_effect_3d.json) |
| F45SH_120C | bh_curve | 7 points (previous card) | 17 points, HcJ(120 C) = 740.3 kA/m, solver knee -740.3 kA/m | arnold_n45sh: generated from datasheet Br20/HcJ20/alpha/beta at 120 C: Br 1.1880 T, HcJ 740.3 kA/m, recoil mu_rec 1.05, knee at 0.97 HcJ |  | no live die (motor_catalog.json, end_effect_3d.json) |
| copper | density | 8933 | 8890 | iec60028: IEC 60028: 8.89 g/cm3 (was 8933 = the Ansys Maxwell built-in copper) | copper mass -0.5 % | all 11 live dies (CIANO10 200 L160; CIANO10 200 opt L155 motor, L180 gen; CIANO14 40 new L12, L20; CIANO14 50 edited L15; CIANO28 150_35 L35 (+new); CIANO28 85 20RSW175 L13; CIANO28 85 20SW1200 L13; CILN28 G2-L40) |
| copper | thermal_conductivity | 400 | 391 | cda_c11000: C11000: 226 Btu ft/(h ft2 F) = 391 W/mK (was 400) | winding k -2 % (negligible) | all 11 live dies (CIANO10 200 L160; CIANO10 200 opt L155 motor, L180 gen; CIANO14 40 new L12, L20; CIANO14 50 edited L15; CIANO28 150_35 L35 (+new); CIANO28 85 20RSW175 L13; CIANO28 85 20SW1200 L13; CILN28 G2-L40) |
| copper | thermal_alpha | 0.0043 | 0.00393 | iec60028: IEC 60028: 0.00393 1/K at 20 C; the old 0.0043 is the 0 C-referenced value used with a 20 C reference | hot winding R: -2.6 % at 120 C, -3.1 % at 150 C, -3.4 % at 180 C -> copper loss down by the same | all 11 live dies (CIANO10 200 L160; CIANO10 200 opt L155 motor, L180 gen; CIANO14 40 new L12, L20; CIANO14 50 edited L15; CIANO28 150_35 L35 (+new); CIANO28 85 20RSW175 L13; CIANO28 85 20SW1200 L13; CILN28 G2-L40) |
| Aluminium_6061 | sigma | 25800000 | 24940000 | asm_v2: 6061-T6 43 % IACS = 24.94 MS/m (was 25.8, 44.5 % IACS) | eddy loss in 6061 parts -3.3 % | CIANO10 200 L160; CIANO14 40 new L12, L20; CIANO28 150_35 L35 (+new); CIANO28 85 20RSW175 L13 (+ end_effect json) |
| Aluminium_7075 | specific_heat | 870 | 960 | asm_v2: 7075-T6: 960 J/kgK (was 870) | mechanical / thermal input | not used by a live die |
| Aluminium_7075 | cte_ppm_k | 23.4 | 23.6 | asm_v2: 7075-T6: 23.6e-6/K (was 23.4) | mechanical / thermal input | not used by a live die |
| Stainless_316L | yield_strength_mpa | 240 | 170 | astm_a240: ASTM A240 316L minimum Rp0.2 170 MPa (was 240, unsourced) | mechanical / thermal input | not used by a live die |
| polyimide | specific_heat | 1100 | 1090 | kapton_hn: Kapton HN: 1.09 J/gK (was 1100) | mechanical / thermal input | CIANO10 200 opt L155 motor, L180 gen; CIANO14 50 edited L15; CIANO28 85 20SW1200 L13 |
| air | density | 1.16 | 1.1614 | incropera: Table A.4 300 K: 1.1614 (was 1.16) |  | every thermal run with air cooling / air gap |
| air | kinematic_viscosity | 1.56e-5 | 1.6e-05 | incropera: Table A.4 300 K: 15.89e-6 (was 1.56e-5) | air-side h about -1 % (Re down 1.9 %) | every thermal run with air cooling / air gap |
| ethylene_glycol | density | 1110 | 1114.4 | incropera: Table A.5 300 K: 1114.4 (was 1110) |  | not used by a live die |
| ethylene_glycol | specific_heat | 2400 | 2415 | incropera: Table A.5 300 K: 2415 (was 2400) | mechanical / thermal input | not used by a live die |
| ethylene_glycol | thermal_conductivity | 0.25 | 0.252 | incropera: Table A.5 300 K: 0.252 (was 0.25) | mechanical / thermal input | not used by a live die |
| ethylene_glycol | kinematic_viscosity | 1.5e-5 | 1.4e-05 | incropera: Table A.5 300 K: 14.1e-6 (was 1.5e-5) |  | not used by a live die |
| ethylene_glycol | prandtl | 150.0 | 151.0 | incropera: Table A.5 300 K: 151 (was 150) |  | not used by a live die |
| Somaloy_700HR_5P | core_loss_kh | 376.596523799905 | 479.606487 | hoganas_fit: refit of the card's curves (n=57, mean rel. err 8.9 %); old value was a Maxwell fit | none (the solver fits kh/kc/ke from the curves; YAML coefficients are the fallback only) | not used by a live die |
| Somaloy_700HR_5P | core_loss_kc | 0.151462503872851 | 0.168030874 | hoganas_fit: refit of the card's curves (n=57, mean rel. err 8.9 %); old value was a Maxwell fit | none (the solver fits kh/kc/ke from the curves; YAML coefficients are the fallback only) | not used by a live die |
| Somaloy_700HR_5P | core_loss_ke | 5.39623112602906 | 1.5068649 | hoganas_fit: refit of the card's curves (n=57, mean rel. err 8.9 %); old value was a Maxwell fit | none (the solver fits kh/kc/ke from the curves; YAML coefficients are the fallback only) | not used by a live die |
| VACODUR_49_0p20mm_390MPa | core_loss_kh | 2.48807199028208 | 154.062513 | vac_flyer: refit of the card's curves (n=6, mean rel. err 3.3 %); old value was a Maxwell fit | none (the solver fits kh/kc/ke from the curves; YAML coefficients are the fallback only) | not used by a live die |
| VACODUR_49_0p20mm_390MPa | core_loss_kc | 0.00284058417289946 | 0.126537089 | vac_flyer: refit of the card's curves (n=6, mean rel. err 3.3 %); old value was a Maxwell fit | none (the solver fits kh/kc/ke from the curves; YAML coefficients are the fallback only) | not used by a live die |
| VACODUR_49_0p20mm_390MPa | core_loss_ke | 0.0 | 2.68915966 | vac_flyer: refit of the card's curves (n=6, mean rel. err 3.3 %); old value was a Maxwell fit | none (the solver fits kh/kc/ke from the curves; YAML coefficients are the fallback only) | not used by a live die |
| VACODUR_49_0p20mm_210MPa | core_loss_kh | 135.779373853219 | 82.8654995 | vac_flyer: refit of the card's curves (n=6, mean rel. err 3.9 %); old value was a Maxwell fit | none (the solver fits kh/kc/ke from the curves; YAML coefficients are the fallback only) | not used by a live die |
| VACODUR_49_0p20mm_210MPa | core_loss_kc | 0.141517405090033 | 0.147416351 | vac_flyer: refit of the card's curves (n=6, mean rel. err 3.9 %); old value was a Maxwell fit | none (the solver fits kh/kc/ke from the curves; YAML coefficients are the fallback only) | not used by a live die |
| VACODUR_49_0p20mm_210MPa | core_loss_ke | 0.0 | 1.72434236 | vac_flyer: refit of the card's curves (n=6, mean rel. err 3.9 %); old value was a Maxwell fit | none (the solver fits kh/kc/ke from the curves; YAML coefficients are the fallback only) | not used by a live die |
| JFE_10JNEX900 | core_loss_kh | 133.474786410586 | 72.0772494 | jfe_f1e002: refit of the card's curves (n=43, mean rel. err 8.5 %); old value was a Maxwell fit | none (the solver fits kh/kc/ke from the curves; YAML coefficients are the fallback only) | not used by a live die |
| JFE_10JNEX900 | core_loss_kc | 0.0134650388489389 | 0.0128812215 | jfe_f1e002: refit of the card's curves (n=43, mean rel. err 8.5 %); old value was a Maxwell fit | none (the solver fits kh/kc/ke from the curves; YAML coefficients are the fallback only) | not used by a live die |
| JFE_10JNEX900 | core_loss_ke | 1.26413752880456 | 1.69723504 | jfe_f1e002: refit of the card's curves (n=43, mean rel. err 8.5 %); old value was a Maxwell fit | none (the solver fits kh/kc/ke from the curves; YAML coefficients are the fallback only) | not used by a live die |
| JFE_Steel_20JNEH1200 | core_loss_kh | 173.295800803324 | 125.435641 | jfe_tr31: refit of the card's curves (n=132, mean rel. err 6.8 %); old value was a Maxwell fit | none (the solver fits kh/kc/ke from the curves; YAML coefficients are the fallback only) | not used by a live die |
| JFE_Steel_20JNEH1200 | core_loss_kc | 0.0859561716280974 | 0.0959462164 | jfe_tr31: refit of the card's curves (n=132, mean rel. err 6.8 %); old value was a Maxwell fit | none (the solver fits kh/kc/ke from the curves; YAML coefficients are the fallback only) | not used by a live die |
| JFE_Steel_20JNEH1200 | core_loss_ke | 2.06787925813399 | 2.44765379 | jfe_tr31: refit of the card's curves (n=132, mean rel. err 6.8 %); old value was a Maxwell fit | none (the solver fits kh/kc/ke from the curves; YAML coefficients are the fallback only) | not used by a live die |
| 20SW1200 | core_loss_kh | 139.921979119876 | 115.340729 | shougang_2024: refit of the card's curves (n=269, mean rel. err 6.4 %); old value was a Maxwell fit | none (the solver fits kh/kc/ke from the curves; YAML coefficients are the fallback only) | CIANO14 40 new L12, L20; CIANO14 50 edited L15; CIANO28 150_35 new L35; CIANO28 85 20SW1200 L13 |
| 20SW1200 | core_loss_kc | 0.201729356570559 | 0.0799395817 | shougang_2024: refit of the card's curves (n=269, mean rel. err 6.4 %); old value was a Maxwell fit | none (the solver fits kh/kc/ke from the curves; YAML coefficients are the fallback only) | CIANO14 40 new L12, L20; CIANO14 50 edited L15; CIANO28 150_35 new L35; CIANO28 85 20SW1200 L13 |
| 20SW1200 | core_loss_ke | 0.0 | 3.84225901 | shougang_2024: refit of the card's curves (n=269, mean rel. err 6.4 %); old value was a Maxwell fit | none (the solver fits kh/kc/ke from the curves; YAML coefficients are the fallback only) | CIANO14 40 new L12, L20; CIANO14 50 edited L15; CIANO28 150_35 new L35; CIANO28 85 20SW1200 L13 |
