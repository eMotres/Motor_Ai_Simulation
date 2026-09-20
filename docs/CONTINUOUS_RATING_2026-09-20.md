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
| A robotics, still air + radiation ε 0.9, bore still, no mount | — ! | (1.133) | winding | 199.7 | 72.0 | (2.465) | (2581) | (0.867) | 6.7 | 1 |
| B forced air 10 m/s, bore none | — | — | magnet | 74.3 | 157.0 | — | — | — | 35.2 | 2 |
| C forced air 20 m/s, bore none | 13.37 | 0.210 | magnet | 68.8 | 149.9 | 0.457 | 479 | 0.940 | 25.2 | 4 |
| D air 40 m/s + bore air 10 m/s (the duty's own setup) | 34.36 | 0.540 | magnet | 103.0 | 149.7 | 1.175 | 1230 | 0.933 | 9.2 | 4 |
| E liquid jacket, water 25 °C, 2 L/min, bore none | 59.66 | 0.938 | magnet | 127.6 | 149.9 | 2.040 | 2136 | 0.903 | 3.9 | 4 |
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

### …and at 13 000 rpm, the duty's own saved speed

One authorised electromagnetic transient was solved in the sandbox at
BelowNormal priority: 13 000 rpm, 42.78 A rms, γ 10°, 48 steps, eddy + rotor
eddy + demag, n_sectors 2, coil 120 °C — 285 s. It gives T_em 1.543 N·m (2-D),
P_mech 2100.1 W, P_cu 124.1 W, P_fe 18.8 W, P_solid 8.7 W.

**The Stage A passport was applied, by INHERITANCE and not by an exact hit.**
The passport is filed under `cb4d42f435952a1f` (the server run's fingerprint);
the sandbox's own copy of the same machine hashes to `bcc7a7d5dc1022bd`,
because `_geometry_fingerprint` is an md5 of the config DOCUMENT and a YAML
round trip re-spells 50 as 50.0. The lookup therefore fell through to the
machine-desc path, matched `12s/14p OD 50`, and applied **k_flux = 0.95139**
with its own note *"coefficient of an EARLIER geometry of this machine … 
recompute to confirm or replace it"*. The number is this machine's own
measurement; only the exactness flag is wrong, and it is wrong because of a
file-format hash, not a geometry difference.

| condition (ambient 30 °C) | I_cont A rms | s* | limited by | winding °C | magnet °C | T N·m | P_rotor W | η_em | fit % | FEM |
|---|---|---|---|---|---|---|---|---|---|---|
| A robotics, still air + radiation, no mount | 24.96 ! | 0.583 | magnet | 178.1 | 150.4 | 0.900 | 1166 | 0.939 | 26.2 | 1 |
| B forced air 10 m/s, bore none | — | — | magnet | 87.0 | 178.1 | — | — | — | 29.5 | 2 |
| C forced air 20 m/s, bore none | — | — | magnet | 65.5 | 155.4 | — | — | — | 29.5 | 2 |
| D air 40 m/s + bore air 10 m/s (the duty's own setup) | 28.74 | 0.672 | magnet | 94.8 | 149.9 | 1.037 | 1343 | 0.944 | 11.1 | 3 |
| E liquid jacket, water 25 °C, 2 L/min, bore none | 45.45 | 1.063 | magnet | 95.2 | 149.9 | 1.639 | 2123 | 0.933 | 4.8 | 4 |
| F robotics + mount 1.0 W/K at 30 °C | 25.33 | 0.592 | magnet | 100.0 | 150.0 | 0.913 | 1183 | 0.945 | 10.3 | 3 |

`P_rotor` is the 2-D mechanical power times k_flux 0.95139, per `shaft_view`.
`!` A is again flagged: the robotics map is non-monotone, so the row is the
first pass only. **C now has no continuous rating where it had 13.4 A at
10 000 rpm** — the magnet's eddy loss grows with frequency (8.6 W against 8.0 W)
while its only exit with an uncooled bore is still the air gap, so at 13 000 rpm
20 m/s over the housing is not enough on its own.

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


## Appendix — what `surface_fit=True` would move, measured on the stored records

Read-only, no FEM: the network is fitted BOTH ways to each duty's own stored
thermal map (the run store supplies the electromagnetic summary and the heat
capacities) and `coupled_time_to_limit.solve` is run on each.

| duty | flag | residual % | t_limit cold | t_limit rated | limiting | winding asymptote °C | magnet asymptote °C |
|---|---|---|---|---|---|---|---|
| CIANO10 200 opt / L155 motor / peak 1x9 mm | OFF | 92.8 | 322.3 s | 309.7 s | bearing | 1379.1 | 1041.5 |
|  | **ON** | **10.8** | 322.3 s | 309.7 s | bearing | **163.1** | **185.5** |
| CIANO10 200 opt / L155 motor / rated 1x9 mm | OFF | 92.9 | 507.3 s | — | bearing | 1045.5 | 768.2 |
|  | **ON** | **17.9** | 507.3 s | — | bearing | **111.1** | **112.9** |
| CIANO10 200 opt / L180 gen / peak 1x9 mm | OFF | 92.3 | 334.5 s | 33.2 s | bearing | 1345.0 | 1025.2 |
|  | **ON** | **12.5** | 334.5 s | 33.2 s | bearing | **165.3** | **184.8** |
| CIANO10 200 opt / L180 gen / rated 1x9 mm | OFF | 93.0 | 362.4 s | — | bearing | 1231.8 | 922.0 |
|  | **ON** | **8.9** | 362.4 s | — | bearing | **147.5** | **164.3** |

**No time to a limit moves** on these four: they are BEARING-limited, the
bearing rides the rotor node, and on a housed liquid/air-jacketed machine
`surface_fit` only changes the HOUSING conductance, which is a stator-side path.
What does move is everything the network says about the STEADY state: the fit
residual falls from ~93 % to 9–18 %, and the winding asymptote from 1045–1379 °C
(a machine the network thinks runs away) to 111–165 °C, which is where the
solved maps actually sit. The housing conductance the OFF network was using is
a natural-convection correlation; the ON one is the 68–92 W/K the jacket
actually carries.

`CIANO10 200 opt / L180 motor` could not be measured: no run payload is left in
the die's run store for those two duties.

### The L13 peak record's 24 s — it cannot be recomputed, and it can only grow

`CIANO28 85 20SW1200 / L13 / peak` stores `time_to_limit_s = 24.411` (the "24 s"
on the catalog chip and in §8). **That number cannot be recomputed from the
records on this disk with either flag**, and the reason is not the flag: the
stored `thermal` block is not one map. Its `components` are the LIMITED-mode
snapshot (winding mean 183.5 °C, max exactly 200.0 — the machine AT the
crossing) while its `cooling` block is the runaway steady solve
(`mount.t_housing_mean_c = 297.3 °C`, 514.6 W into the mount). The route's own
record says so: `time_to_limit.network.map_winding_mean_c = 392.4 °C` against
the 183.5 in the block beside it. Refitting either flag to that mixture gives a
67 % / 55 % residual and `within_limits: true`, i.e. no time at all.

What CAN be said about the direction, and it is the thing that matters for a
number already sent to a client:

* the correction only ADDS heat-rejection paths and only REDUCES the internal
  drive — on this map the winding's own conductance to the room goes from the
  0.277 W/K the map states to the **0.681 W/K its heat budget actually removes**
  (2.5×), and 97.7 W of the 676.2 W of copper is taken off the winding→stator
  drive;
* from COLD both changes slow the winding down, because at t = 0 the iron is at
  ambient too and the winding→stator path carries nothing while the
  winding→room path already carries `G·(T_w − 40)`.

So **24 s is a lower bound: with the correction the winding takes longer, not
less, to reach 200 °C.** The number the owner has sent is on the safe side. By
how much cannot be stated without re-running that duty's coupled loop, which
would replace his record and was not done.

### A third mismatch of the same family, found and not touched

On the same L13 map the MOUNT is driven by the housing WALL (297.3 °C) in the
2-D solve and by the stator NODE MEAN (118.3 °C) in the network — 514.6 W
against 157 W for the same 2.0 W/K. It belongs with the two `routes/thermal.py`
findings above and is left for the owner for the same reason.
