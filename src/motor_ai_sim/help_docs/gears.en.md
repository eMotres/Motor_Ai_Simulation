# Gears (Simulations > Gears)

What it is for: a screening calculation of a gear or planetary gearbox: ratio, efficiency, safety factors, bearing life, lubrication, mass, a 3-D view, and the gearbox behind a saved motor. It is an estimate, not a qualification. Staff only.

The page has two columns.

Left column, the design (always visible):
- Saved gearboxes: see the separate document.
- Operating point: preset (acceptance limits and required life; the "Humanoid robot joint" preset fills a two-stage example), lubricant, torque and which shaft it acts on (input or output), input speed, direction (forward or reverse), temperature.
- Stages, in order from the input: add a Planetary, Compound planetary or Gear pair stage; give each a unique name, its gears (teeth, module, width, shift, helix, tooth-thickness allowance), planets and the fixed member, and optional catalogue bearings. Up, Down and Remove reorder or delete a stage.

Right column, the result:
- Four sub-tabs: Point, Duty spectrum, 3-D, With motor. Calculate (or Build 3-D) runs the open sub-tab; the first three use the design on the left.
- The "Screening estimate" chip is always shown. "Inputs changed" appears when the design was edited after the last run: calculate again.
- "Not covered by this calculation" at the bottom lists the model limits. Read it before using a number.

Usual order: set the operating point, define the stages, press Calculate on Point, then on Duty spectrum for a pass or fail, then 3-D, then save.

Status chips on values: Valid, Estimated (simplified factors or assumed inputs), Unsupported (the model does not cover it, it can never pass), Failed.
