# Propellers: Designer

Evaluate an own propeller with a blade-element model. The banner "BEMT, generic airfoil polars, not validated" is always on: results are screening estimates.

Top row: Diameter (in), Blades, Motor, Polar (flat plate rough model, with camber, or viscous mfoil table), and Evaluate (shows "Evaluating" while it runs). Below, the blade parameters are grouped as Root (inner blade), Blade, Airfoil by zone, and Blades and diameter; each has bounds and "Reset to seed".

After Evaluate:
- Tiles: thrust, torque, shaft power, speed, tip Mach, thrust per shaft watt, air speed at the motor.
- Constraint chips: "Constraints met" or "Constraint violated" with the rule (torque within the motor limit, tip Mach within the limit, solver converged), and flags such as "Root flow not validated" or "Inputs unconfirmed".
- Charts: planform and twist, air speed by radius, torque against speed with the motor limit; a table "Zones as manufactured".
- Runs and the two-objective view: every Evaluate and Sweep adds a run; "Use" picks one, "Clear runs" empties the list.

Panels:
- Sweep: choose a Variable, From, To, Points, then "Run sweep".
- Optimiser: Population, Generations, Seed, which groups to Vary (Blade, Root, Airfoils), the Objectives (thrust per W, air at motor), "Enforce tip Mach" with its limit, then "Run optimiser". It reports the number of designs and the front.
- Drafts: type a name, Save, Open or Delete a saved draft.
- Stored optimisation runs: runs kept in the workspace, filtered by Blades; open a run to see how it converged, Refresh, and "Load best into the Designer".

An invalid design shows "The design is not valid" with the broken rules.
