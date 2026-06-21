# Module: geometry-2d-aerostator  ·  capability `geometry.2d`

You are the agent that owns the **2D geometry** module. Your job: turn a motor
parameter set into a neutral `GeometryIR(dim=2)` — and nothing else.

## Contract (do not break)
- **Output:** `motor_ai_sim.contracts.GeometryIR` with `dim == 2`.
- **Input:** `run(payload)` where `payload = {"params": {...override geometry...}, "rotor_angle_deg": float}` (both optional; absent → live config).
- **Must pass:** `motor_ai_sim.contracts.conformance.geometry.assert_geometry_provider(provider, dim=2)`.
- Couple to other modules **only** through capabilities + IR. Never import another module.
- Bumping anything in `contracts/` is a cross-module event (semver + review) — not done here.

## What this module IS / ISN'T
- IS: the AeroStator Core fully-parametric cross-section (stator, rotor, magnets,
  coils, shaft, air regions, slip radius, outer BC) → regions with roles + materials.
- ISN'T: meshing, physics, winding *current* assignment (the EM module maps phases),
  cost, or 3D. Keep those out.

## Downstream that depends on you
- `mesh` consumes your `GeometryIR`.
- **`geometry-3d` (capability `geometry.3d`, `depends_on=["geometry.2d"]`)** will
  extrude/revolve your 2D regions into solids. So: keep region `id`s stable and
  meaningful, keep `symmetry`/radii correct — 3D builds on them.

## UI
This module contributes the **Geometry** panel (`ui.panel_id = "geometry"`,
`frontend_module = "components/geometry/GeometryPanel"`). The portal frontend is
itself modular: the panel is registered from this manifest, not hard-wired.

## Verify
```
PYTHONPATH=src python -m motor_ai_sim.modules._selfcheck
```
Real 40 mm config currently yields 117 regions (14 magnets, 96 coil wires) and
round-trips through JSON. Run this after any change; it must stay green.
