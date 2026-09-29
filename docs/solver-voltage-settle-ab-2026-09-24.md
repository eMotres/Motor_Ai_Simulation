# Plain sinusoidal-voltage settling: four-period default

The P2 solver uses four settling electrical periods by default **only** for
the built-in sinusoidal voltage source without coupled eddy currents or
demagnetisation. An explicit `SB_V_SETTLE_PERIODS` value remains authoritative.
PWM, custom sources, eddy-voltage and demagnetising voltage runs keep their
existing source policy. The selected count is passed into the same schedule,
settling trim and source-context machinery; the result reports the actual
`voltage_settle_periods`, its source-policy count and the selection reason.

This narrow change follows an A/B run on the pinned `p2_voltage` case supplied
by the orchestrator. The four-period run solved 360 frames in 162.1 seconds;
the ten-period baseline was already available. No new FEM run was made for
this implementation.

| Quantity | 10-period baseline | 4-period run | Relative change |
| --- | ---: | ---: | ---: |
| Mean torque (N·m) | 0.236931812540 | 0.236932188252 | +0.00016% |
| Torque ripple (%) | 2.675041777 | 2.674823586 | -0.00816% |
| Torque peak-to-peak (N·m) | 0.006338025 | 0.006337518 | — |
| Peak voltage (V) | 8.667083388 | 8.667071032 | — |

The four-period run had `v_dc = 0 A` and maximum circuit residual
`4.87e-14 V`. These are one fixture's checks of the settled reported window,
not proof that four periods suffice for other electrical time constants or
for eddy/demagnetisation. Those modes therefore retain ten periods unless the
caller explicitly overrides the setting. Tests for this change exercise the
selection and schedule wiring without running FEM.
