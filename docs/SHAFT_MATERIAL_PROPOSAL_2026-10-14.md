# Shaft material rule (owner 2026-10-14): PREPARED, NOT APPLIED

> "Only the smallest motors have a stainless shaft" and "all the walls and the shafts are aluminium 6061".
> The stainless grade is **provisional (316, "probably", to be confirmed by the owner)**. Nothing below is applied to any die or
> configuration; the change waits for his confirmation.

## The rule (by stator outer diameter, not per-motor guesses)

| Stator OD | Shaft | Note |
|---|---|---|
| <= 40 mm (the 30 mm and 40 mm families) | stainless steel AISI 316 (`Stainless_316`, provisional) | non-magnetic, mu_r about 1.0 |
| 85 mm and larger public dies (85, 100, 150) | aluminium 6061-T6 (`Aluminium_6061`) | non-magnetic |
| 200 mm and larger (private, high-speed) | **not touched** | kept as is |
| between 40 mm and 85 mm | no rule yet | listed as "undecided" |

Every aluminium part of the Full assignment (holders, walls, plates, rotor carrier) already defaults to `Aluminium_6061`
(`config/part_materials.yaml`, `mechanical_parts`); there is no 7075 default.

## What is prepared

* `Stainless_316` in `config/materials_library.yaml` (conductor category: sigma 1.35 MS/m, mu_r 1.005, density 8000, E 193 GPa,
  Rp0.2 205 MPa, Rm 515 MPa, with ASTM A240 / ASM sources, every figure `verify: true`). It is the solver's one card for the
  material, so the same card serves the EM assignment, the masses and the stress/thermal studies. Not assigned anywhere.
* `scripts/shaft_material_proposal.py`: applies the rule to a die catalog and prints, per die and configuration, the shaft
  material the electromagnetic simulation has today, the proposed one, whether the change is magnetic -> non-magnetic, and the
  mass effect. It **never writes** a die; `--patch FILE` produces a `.patch.yaml` proposal for the orchestrator.

## Why it is a PHYSICS change (a separate step)

The shaft is an electromagnetic part. Its material is the configuration's `materials.shaft` (a part the configuration does not
name takes the shipped default of `config/motor_config.yaml`, aluminium 6061), so assigning it moves the shaft's eddy loss and, for a
ferromagnetic shaft, the rotor flux path. Passports and stored duties are then stale and must be re-solved. The component card's
Full assignment follows the same material (the CAD shaft is rescaled by the density ratio).

## What it would change (public dies)

* **Ø30 and Ø40 (CIANO14 30_10, CIANO14 40 L12/L20, the 40 mm high-speed die)**: aluminium 6061 (non-magnetic) -> stainless 316
  (non-magnetic): the field is unchanged to first order; the shaft's conductivity falls from 24.9 to 1.35 MS/m (the shaft eddy loss
  falls with it) and the shaft becomes 2.9 times heavier (simulation tube 4.3 -> 12.6 g on L12 with the CAD shaft; 5.3 -> 15.8 g on
  L20), so the full motor mass rises by about 8 g (L12) and 10 g (L20).
* **CIANO28 85 L13**: today the electromagnetic shaft is `Steel_42CrMo4_QT`, a **magnetic** solid steel (B-H curve, solved eddy
  current); aluminium 6061 is **non-magnetic**. The shaft leaves the rotor flux path: rotor field, torque, EMF and losses change and
  the passport must be re-solved. This is the change that matters. The simulation shaft tube falls from 29.8 g to 10.4 g.
* 100 mm and 150 mm public dies already use aluminium 6061 (the shipped default): nothing changes.

Re-run `python scripts/shaft_material_proposal.py --dies-dir <catalog>` for the current table of a catalog.
