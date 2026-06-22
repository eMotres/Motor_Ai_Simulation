# Modular passport pipeline — Geometry → Simulation → Passport → Configurator

Goal: a single coherent flow so the Configurator scales a **real catalog motor's
FEM-derived passport**, not a hand-authored constant. Removes the disconnect where
Simulation shows the active motor while Configure shows an unrelated reference.

## The model

A **Motor** (catalog entry + preset) owns:
- `geometry` + `materials`  — edited in the Geometry tab, saved via presets/catalog.
- `passport`               — a reduced-order model derived from FEM:
  - scaling base (T0, R0, Vemf0, losses, mass at one operating point) — from the
    3-solve extraction (loaded / no-load / 1.5×L), mirrors `extract_passport.py`.
  - `speed` curves (Pfe_W, Pmag_W vs rpm) — from the rpm sweep, mirrors
    `extract_speed_curves.py`.
  - `fit` (slot/wire context) + `geo` (cross-section radii) — read from geometry.
  Shape matches `ReferenceMotor` in `web/src/lib/referencePassports.ts`.

Pipeline (each stage a module):
```
GEOMETRY ──save──▶ Motor.geometry
                     │ FEM
SIMULATION ────────▶ results  ("Save this simulation" = scratch Compare snapshot)
                     │ sweep rpm/current + extract  ← admin action
                  Motor.passport  (published into the catalog entry)
                     │ scale instantly (no FEM)
CONFIGURE ─────────▶ user-facing analytical preview (reads catalog passports)
```

## Phases

1. **Backend keystone (this phase).** `src/motor_ai_sim/passport.py::generate_passport(geo_override, op)`
   refactors the two extractor scripts into one reusable function returning the full
   passport dict (base + speed + fit + geo). Admin endpoint
   `POST /api/catalog/{motor_id}/passport` loads the motor's preset geometry, runs
   the generation, and stores `passport` on the catalog entry. `GET` exposes it.
2. **Configurator reads catalog passports.** Frontend fetches catalog motors that
   carry a passport and lists them in the REFERENCE dropdown (mapping the stored
   passport → `ReferenceMotor`). `referencePassports.ts` stays as a fallback/seed.
3. **Admin UI (References & passports manager).** In the Admin tab: list motors,
   "Generate passport" (triggers phase-1 sweep, shows progress), publish/refresh.
4. **Simulation → motor characterization (optional).** Explicit "Save results into
   this motor" distinct from the scratch Compare snapshot.

## Notes
- Generation is FEM-heavy (3 base solves + N-point rpm sweep ≈ minutes). The
  endpoint is admin-only and should run async / show progress (phase 3).
- The rpm sweep mutates `cfg["simulation"]["rpm"]`; the generator must save/restore it.
- Coarse settings (nspp=6, few rpms) for quick smoke tests; full settings for publish.
