# Continuous (S1) rating under different cooling conditions — 2026-09-20

Owner, 2026-09-20: *«давай ещё сделаем расчёт continuous power для разных условий
охлаждения»*.

`coupled_time_to_limit` answers "the current is given, how long may it pull?".
This is the complement: **the largest current the machine may hold for ever at
the reference speed with every part inside its own limit**, answered once per
cooling condition.

## What was built

| what | where |
|---|---|
| the physics (pure, no FastAPI, no I/O) | `src/motor_ai_sim/coupled_continuous_rating.py` |
| the route | `POST /api/coupled/continuous_rating` in `src/motor_ai_sim/routes/coupled.py` |
| the CLI | `scripts/continuous_rating.py` |
| tests | `tests/test_continuous_rating.py` (16) |

One electromagnetic run pays for a whole table. Per condition: 2-3 solves of the
2-D steady thermal map and **no** electromagnetic solve.

## The method

1. Fit the four-node network (`thermal_duty_cycle.network_from_steady`) to the
   2-D steady map of the reference run's loss field under this cooling; read the
   limits off the machine's own cards (`coupled_time_to_limit.part_limits` — the
   winding HOT SPOT against the insulation class, the HOTTEST magnet element
   against its card, the bearing SEAT against the lubricant).
2. With `s = I / I_ref`: copper ∝ `s²·ρ_Cu(T_w)/ρ_Cu(coil_ref)`; iron,
   magnet-eddy and mechanical watts HELD at the reference values. Settle the
   network (`steady_state`, nonlinear through ρ(T)) and **bisect `s`** until the
   first part sits on its limit within 0.5 K.
3. **Make it real**: rebuild the loss map with the copper scaled by
   `s*²·ρ(T_w)/ρ(coil_ref)`, re-solve the 2-D thermal FEM under the same cooling,
   re-fit the network, re-bisect — until `s*` moves by less than 1 %.
4. Torque `T_em(s*) = T_em_ref·s*`, **linear in current** and labelled so; the
   power balance is `report.shaft_view` (one efficiency, at the shaft).

### Stated approximations

* the torque is linear in the current — optimistic above the reference point
  (saturation), conservative below it;
* iron and magnet-eddy losses are held: at fixed rpm they move with the field and
  only weakly with the current; Br(T) is not fed back;
* the copper is scaled by ONE law (the network bills one copper number), so the
  proximity share's own 1/ρ dependence is not separated — ~1 % at 250 °C here;
* each part's temperature FIELD SHAPE is frozen at the calibration map's; only
  its level moves (the same approximation `coupled_time_to_limit` states).

## Three defects found and fixed in the lumped-network fitter

All three were silently wrong for every machine that is **not** a still-air
housed one, i.e. they also affected `coupled_time_to_limit` and
`coupled_duty_cycle`. Measured on CIANO14 50 edited / L15 / rated edited (Ø50,
15 mm, 12s/14p, 40 m/s forced air + 10 m/s bore air, open frame):

1. **The open frame's two winding-side paths were missing.** The map cools the
   end turns in the airflow (169.8 W) and the ventilated slot channels (37.8 W)
   straight to the room — 207 of the 262 W the machine makes. The network had no
   key for them, so it could reject 55 W and every transient ran away. Fixed:
   `Network.G["w_open"]`, a winding→ambient conductance fitted to those watts,
   plus `_flows["winding_open"]`.
2. **The winding→stator link was fitted to the whole copper loss.** Only what
   actually crosses into the iron may drive that fit; here `241.4 W` instead of
   `33.7 W` made `w_s` seven times too stiff. Fixed: every watt a node sends
   straight to the room (open frame + axial end face) is taken off the drive.
3. **Surface films were re-computed instead of being taken from the map.**
   `still_air_G` re-evaluated a NATURAL-convection correlation whenever a housing
   diameter was known — so a 40 m/s film of 132 W/m²K became 8, and a water
   jacket's 1e5 became 8 — and the end faces used the flat
   `END_FACE_H_PROVISIONAL = 12 W/m²K` while the 2-D solve computes a real
   per-node Rayleigh/Nusselt film with radiation. Fixed: `Network.G_fit` /
   `t_fit_c` / `film_kind` — **the map's own conductance is the level**, and the
   correlation only supplies the SHAPE of a natural film
   (`G(T) = G_map · h(T)/h(T_map)`); a forced film is held.

After the fix the network reproduces the map it was fitted to: the worst
residual on the rated condition falls from 298 W (of the 262 W the machine
makes) to 9 %.

**All three ship OFF** behind `network_from_steady(..., surface_fit=True)`.
Switching them on moves every number this network has produced on a machine
that is not a still-air housed one — the L13's time to its insulation class
among them, which is printed in reports already delivered — and the project's
rule is that live answers do not move without the owner's word. Measured while
gating it: with the correction on by default the L13 fixtures' time-to-limit
suite went from 60 s to over 14 minutes of CPU without finishing, because the
housing conductance it had been using was 23× too small and the machine it was
integrating no longer ran away. **`coupled_continuous_rating` asks for it;
`coupled_time_to_limit` and `coupled_duty_cycle` do not, yet.** Turning it on
for them is a one-word change once the owner has seen these numbers.

## Two defects found and NOT fixed — they belong to `routes/thermal.py`

Reported rather than touched: they change every robotics map already computed,
and that needs the owner's word.

1. **The robotics end-face block is internally inconsistent.** For the Ø50 joint
   in still air the heat budget closes on `end_faces_W = 192.3 W`, while the
   block's own conductances (`cooling.end_faces.<node>.G_W_per_K`, summing to
   0.108 W/K over ~100 K ≈ 11 W) account for a seventeenth of it. The network is
   therefore fitted to the WATTS (what the temperature field actually came from)
   and says so in its notes.
2. **The robotics map is NOT MONOTONE in the copper loss.** Same machine, same
   cooling, the loss map handed in at four scales:

   | copper handed in | winding mean |
   |---|---|
   | 60.4 W | 319.8 °C |
   | 241.4 W | 147.6 °C |
   | 362.1 W | 91.3 °C |
   | 482.8 W | 66.2 °C |

   Colder with more heat in it, over the whole range. The same sweep under
   forced air (condition D) is correctly monotone (96.2 → 251.5 → 354.9 →
   458.4 °C). `coupled_continuous_rating` detects this and refuses to iterate:
   the row is flagged `trustworthy: false` and is reported as **not a rating**.

## The table — CIANO14 50 edited / L15 / "rated edited", 10 000 rpm

Reference: the electromagnetic run of 2026-09-20T18:34:40, `geo_fingerprint
cb4d42f435952a1f`, 63.64 A rms, 10 000 rpm, γ 10°, coil 120 °C, 11 turns,
star/2S, P_cu 241.4 W, P_fe 13.2 W, P_solid 8.2 W, T_em 2.176 N·m (no 3-D
passport, so `k_flux = 1`). Ambient 30 °C in every row. Magnet limit 150 °C
(F52SH_120C card), winding limit 200 °C (the project's assumed class).

Note the speed: the duty's SAVED point is 42.78 A at 13 000 rpm; the
electromagnetic run that exists is 63.64 A at 10 000 rpm, and this table is at
the run's speed.

| condition (ambient 30 °C) | I_cont A rms | s* | limited by | winding °C | magnet °C | T N·m | P_mech W | η_em | fit % | FEM |
|---|---|---|---|---|---|---|---|---|---|---|
| A robotics, still air + radiation ε 0.9, bore still, no mount | — ! | — | magnet | 199.7 | 72.0 | (2.465) | (2581) | (0.867) | 6.7 | 1 |
| B forced air 10 m/s, bore none | — | — | magnet | 74.3 | 157.0 | — | — | — | 35.2 | 2 |
| C forced air 20 m/s, bore none | 13.37 | 0.210 | magnet | 68.8 | 149.9 | 0.457 | 479 | 0.940 | 25.2 | 4 |
| D air 40 m/s + bore air 10 m/s (the duty's own setup) | 34.36 | 0.540 | magnet | 103.0 | 149.7 | 1.175 | 1230 | 0.933 | 9.2 | 4 |
| E liquid jacket, water 25 °C, 2 L/min, bore none | 59.66 | 0.938 | magnet | 127.6 | 149.9 | 2.040 | 2224 | 0.943 | 3.9 | 4 |
| F robotics + mount 1.0 W/K at 30 °C | 31.60 | 0.497 | magnet | 114.5 | 150.4 | 1.080 | 1131 | 0.935 | 8.1 | 3 |

`!` A is **not a rating**: the 2-D robotics map is non-monotone in the copper
loss (see above), so the iteration is refused and the row is the first pass
only — 72.1 A, winding-limited, is what that single pass said and it is not to
be quoted. B has no continuous rating at all: with the bore uncooled the
magnet's own 8 W of eddy loss puts it at 157.0 °C with essentially zero current
in the machine, 7 K past the card.

Shaft power and shaft efficiency are absent in every row on purpose: this
configuration names no bearings, so `P_mech_extra_W` is unknown and an unknown
friction is not a zero. The η column is the electromagnetic one (`P_rotor /
P_elec`), labelled as such.

They are regenerated with
`python scripts/continuous_rating.py --conditions <file> --current 63.6396
--rpm 10000 --gamma 10 --coil-temp 120 --steps 48 --mesh 1.0 --min-size 0.3
--outer-air 1.2 --sectors 2 --component-mesh '{"coil_rel": 0.5}'`.

## The engineering conclusion

**This machine's continuous rating is set by the MAGNETS, not by the
insulation**, under every cooling that was tried: the magnet eddy loss (8 W) has
only the air gap to leave through whenever the bore is not cooled, so the
F52SH_120C card's 150 °C binds long before the winding reaches 200 °C. Cooling
the BORE is worth more than cooling the housing — 10 m/s through the bore moves
the rating more than doubling the housing air speed.
