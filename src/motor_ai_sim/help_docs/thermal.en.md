# Simulations > Thermal

What it is for: the steady-state temperature map of the loaded machine for a chosen cooling, and the read-only result of the last coupled EM and thermal loop. Staff with the simulation UI.

The tab never makes an electromagnetic run of its own. If the point has no current Electromagnetic result you see a warning and a "Go to Electromagnetic" button: run it there first.

Controls:
- "air C": the air temperature, shown only when air is blown somewhere.
- Solve (shows "Solving" while it runs). It is disabled while the boundary conditions are invalid; the red line says why.
- "Outer cooling source": "Manual cooling parameters" or "Propeller slipstream" (needs an assigned propeller and an Electromagnetic run for the rpm).
- "Outer surface" cooling mode: air, liquid, manual h, none, or Robotics (still air plus mount), with its own fields (air speed, coolant, inlet temperature, flow in L/min, h, emissivity, mount, exposed shaft).
- Rotor bore: air, liquid, none, or still for Robotics; a solid shaft has no bore.
- Frame and mesh options, and a line saying whose cooling settings are used (saved, configuration default, application default or last used).

Results: temperature tiles per part (winding, magnet, rotor, shaft, stator, air gap and so on) against their limits, the temperature map, a 3-D heat-path view, and loss tiles (hot-spot, outer, inner bore, shaft ends, end turns, slot channels, air gap, rotor heat out, total loss). "Add to comparison" stacks the cooling variant in the Local comparison table.

Warnings: a stale note when the geometry changed after the result (re-Solve), a cooling mismatch when the result was solved with other cooling than the fields show, and "Loaded from history" with Recompute.

Below, the Duty cycle editor (S2 and similar cycles: cycle seconds, ED check, start temperature, rest unpowered or another duty, save the found ED) and the "Coupled EM and thermal" read-only block.

Usual order: run Electromagnetic, set cooling here, Solve, compare variants. For the coupled loop switch it on in Electromagnetic.
