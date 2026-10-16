# Geometry Plugin-SDK

The portal is built around one parametric geometry (the AeroStator Core), but the
whole point of the contracts-first architecture is that **any** 2D motor topology
can be plugged in — without editing the core. This is how.

## The idea

Every downstream module — mesh, the EM/thermal solvers, cost, optimization —
speaks only the `GeometryIR` **contract** (regions with a role, an exterior ring,
optional holes, a material, and magnet polarity / winding). None of them know or
care *which* generator produced the geometry. So a new topology only has to:

1. produce a valid `GeometryIR`, and
2. pass the geometry **conformance gate**.

…and the entire pipeline (mesh → solve → cost → optimize) accepts it unchanged.

## Add a geometry in 3 steps

1. **Write the provider.** Drop a `.py` file in
   `src/motor_ai_sim/modules/geometry_plugins/` with a class that has:
   - `CAPABILITY = "geometry.2d.<your_id>"`  (namespaced so it coexists with the
     built-in `geometry.2d`),
   - `manifest()` → `ModuleManifest(..., outputs=["GeometryIR"])`,
   - `run(payload)` → a `GeometryIR`.

   The easy path is to build shapely polygons and call the adapter:
   ```python
   from motor_ai_sim.contracts.adapters import geometry_ir_from_polys
   polys = {"stator": ..., "rotor": ..., "shaft": ...,
            "in_band": ...,            # air gap
            "magnets": [(poly, +1.0), (poly, -1.0), ...],
            "coils":   [poly, poly, ...],
            "mid_r_mm": slip_radius, "r_outer_boundary_mm": outer}
   return geometry_ir_from_polys(polys, parameters={...}, materials={...})
   ```

2. **Pass conformance.** Your provider must satisfy:
   ```python
   from motor_ai_sim.contracts.conformance import assert_geometry_provider
   assert_geometry_provider(MyGeometry())   # raises if the IR is invalid
   ```
   It checks: returns a `GeometryIR`, `dim==2`, ≥1 region, every region exterior
   has ≥3 points, the 2D roles **stator / rotor / magnet / coil** are all present,
   and the IR round-trips through JSON (the wire format between modules).

3. **That's it.** `default_registry()` auto-discovers the file (any class whose
   `CAPABILITY` starts with `geometry.`) and registers it. Nothing else to edit.

## Use it

Run any pipeline against your geometry by naming its capability first:
```
POST /api/kernel/study  {"capabilities": ["geometry.2d.<your_id>", "mesh", "cost"]}
```
mesh/cost find the geometry by role (any `geometry.*` upstream result), so they
work with your plugin exactly as with the built-in geometry. It also appears
automatically in `GET /api/modules` and the Admin → Platform Modules list.

## Reference implementation

`geometry_plugins/example_ring_spm.py` — a surface-PM (SPM) 12-slot/8-pole
cross-section built from scratch (no CadQuery). Verified end-to-end: conformance
passes; `geometry.2d.ring_spm → cost` yields real masses; `geometry.2d.ring_spm →
mesh` meshes (~8k nodes). Copy it as your starting point.

## Notes / limits

- **em_static** consumes the prebuilt `MeshIR` directly. The sliding-band
  transient and the thermal solve own their own (motion / loss-coupled) meshes —
  that is by design, not a gap.
- Materials are referenced by name in the IR; cost maps region role → density
  bucket, so cost works even before a material library entry exists.
- Conformance is about the **contract**, not a specific motor — keep topology
  specifics inside your provider.
