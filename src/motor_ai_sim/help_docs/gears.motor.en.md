# Gears: With motor

The gearbox behind a saved motor: what the motor must deliver for the output load you give, and how the motor's saved points compare. Saved results only; nothing is solved here.

Steps:
1. Pick the Motor (a die you may see) and its Configuration.
2. Pick the Gearbox source: Current design (as edited on the left) or the open saved record (enabled only when a record is open in Saved gearboxes).
3. Edit the load rows (mode name, output torque, output speed, and the motor duty that answers the row; Automatic uses the rule for the mode: continuous by its rated duty, peak by a short-time duty). Row adds one, the bin icon removes one.
4. Press Calculate. The button is disabled until a motor and configuration are chosen.

Each row shows a status chip: OK, Missing (the motor has no usable saved point for this row; the reason is shown, the gearbox side is still calculated) or Gearbox failed (no comparison is made). For OK rows: the required motor torque and speed, the saved point, the margin (saved minus required; negative means the saved point is short), losses of mesh, bearings and churning, chain efficiency, masses and torque per mass.

Other chips: "Motoring only" (the chain is computed for motoring, reverse is not computed), "Shaft torque" or "Field torque (legacy)" for how the saved motor torque is defined, and record flags such as "Result outdated" when an open record changed.

If the required point differs from the saved one by more than 2 percent, no chain number is given.
