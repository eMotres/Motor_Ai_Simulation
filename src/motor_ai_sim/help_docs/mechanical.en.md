# Simulations > Mechanical

What it is for: rotor centrifugal stress and retaining-sleeve sizing at speed, contacts and safety factors, then vibration modes, shaft critical speeds, and bearing and windage losses. Staff with the simulation UI. Solve is a deliberate press: nothing re-solves by itself.

Controls (top row):
- One speed: "rpm" is the Electromagnetic tab's operating point; enter a proof speed in the rpm field to override it; clear the field to follow Electromagnetic again. With coupled thermal on the speed and temperatures come from that run and are locked.
- Loads menu: Centrifugal, Torque, or both; torque has its own "T N-m" field. Magnetic pull menu: no pull, EM field or uniform (with "p MPa").
- "interference mm" and the "rotor C" and "sleeve C" temperatures.
- Solve (shows "Solving"), and "Limit speed (SF = 1)", which searches the speed at which the minimum averaged safety factor reaches 1.
- Mesh block: "mesh mm", full rotor or 1 pole sector, and "Build mesh" (draws the mesh without solving; the next Solve reuses it).
- Contact menus for the interfaces: Separation, Bonded or Sliding, and friction "mu". Nothing re-solves until you press Solve.

Results: safety-factor tiles (magnet lift-off, sleeve lift-off, fit at temperature, rotor mass and more), the deformed rotor stress map, and the contact summary. "Add to comparison" stacks the result.

Sections below: Bearings (choose bearing A and B from the catalogue via Browse, grease or oil-air, preload, bearing temperature manual or from Thermal; gives bearing friction and rotor windage), Bearing fit and holder strength, and Modal analysis ("Solve modes" for rotor or stator modes, "Critical speeds" for the shaft line).

Warnings: "result is for a previous geometry - re-Solve" after a geometry change; "Loaded from history" with Recompute.

Usual order: run Electromagnetic, set loads and contacts here, Build mesh if wanted, Solve, then Limit speed, then the modal and bearing sections.
