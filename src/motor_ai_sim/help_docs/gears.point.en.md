# Gears: Point

One operating point of the design on the left: the torque and speed you entered at the input or the output.

Press Calculate. The result shows:
- Overall ratio, input and output torque and speed, efficiency forward and in reverse (back-driving), power in and loss, backlash at the output, torsional stiffness, mass, envelope size.
- Per stage and gear: the tooth-root safety factor S_F and the flank safety factor S_H, each with a state chip.
- Per bearing (only when catalogue bearings were chosen): basic life L10h, static factor S0, oil film.
- The lubrication regime of each mesh.

A single point never gets a pass or fail: the line "Point: no verdict" says so, and the reasons list what was not evaluated. For a verdict use Duty spectrum.

A dash means the value could not be calculated; hover the chip or the reason for the cause (missing input, unsupported case, upstream failure). Missing bearings mean no bearing life. "Failed" bearing records give a diagnostic life only, never an aggregate.

If you change the design after calculating, "Inputs changed" shows and the old result stays on screen until you calculate again. Errors in an input field appear in red next to the field and in a red box above the page.
