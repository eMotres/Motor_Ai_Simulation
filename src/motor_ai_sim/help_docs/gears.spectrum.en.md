# Gears: Duty spectrum

The life and pass or fail verdict of the design over a real duty: a list of rows, each with output torque, output speed, hours and ambient temperature. The rows together are the required-life block.

Under the tab, edit the rows (Row adds one). Optional peak event: peak torque at the output, the number of peak events, and the peak speed (zero is allowed). Without a peak speed and count, the peak event fatigue is not evaluated. The peak inputs have no separate result panel.

Press Calculate. The result shows:
- Passed or Not passed, the preset used and the reasons (overall state, parts not evaluated, preset check). Estimated results may pass their thresholds but stay labelled as estimates.
- The preset checks as a table: check, value, limit, pass.
- Gear life by Palmgren-Miner damage per member and mechanism (root bending, flank pitting); "endurance" means zero damage inside the supported model, not unlimited physical life.
- Bearing life, if bearings are defined.

Known limits: internal gear (ring) root bending is unsupported, so a planetary life cannot be fully green; reversing load fatigue is unsupported; static strength needs supplied allowables. These show in the "Not covered" list.

This is where parts and the preset are evaluated; a Point is not.
