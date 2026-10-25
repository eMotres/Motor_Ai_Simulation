# Two-set passport current and voltage conventions

This pipeline supports two identical three-phase windings made by dividing the
parallel paths into equal, electrically in-phase sets. `winding_sets` validates
the coil/path split. It does not model an arbitrary displaced six-phase winding.

Existing passport maps and Configure calculations retain the equivalent
three-phase terminal-current basis. For balanced operation:

- Each inverter carries half the equivalent terminal current.
- Each set sees the full phase voltage; the DC-bus voltage ceiling is unchanged.
- Each set has twice the equivalent winding resistance. Two sets therefore
  produce the same total copper loss as the parallel-connected twin.
- Torque, shaft power, iron/rotor losses, and mass remain whole-machine values.
- Half the copper loss can be allocated to each set by symmetry. This allocation
  is not a separately solved fault or thermal result.

The current split must not be applied to the analytical current-density or loss
equations a second time. Configure converts current at its display/input boundary;
stored maps keep their existing current axis. Controller-board ratings already
refer to one inverter and must be compared on that basis.

A sector containing only one set cannot establish the independent set response.
Six-phase passport jobs use a full ring and retain the requested sector count in
provenance where available. Three-phase sector selection stays on its existing
path. Self and mutual inductance must not be inferred by simply doubling a
three-phase inductance; the solver's set/common/differential-mode definitions
remain authoritative.

## Bounded numerical check

`tests/test_six_phase_passport_equivalence.py` compares the public 30 mm 12-slot,
14-pole, two-parallel-path fixture with its two-set twin. At 60 A total RMS
(30 A per set), 15,000 rpm, four full-ring frames, quadratic elements, and a
template mesh, both nonlinear solves converged with maximum reported Picard
residual `3.99e-08`. Torque was `0.2116952234341791 N m`, DC copper loss
`26.806451794071034 W`, iron loss `3.4787179834038797 W`, and reported total
loss `31.136 W`. Torque, phase flux series, and these losses matched exactly.
The source-decomposition residual was zero and both sets were represented.

This is a balanced-source equivalence check with eddy and demagnetization disabled,
not a mesh-accuracy study or a rating for a customer motor. The 48-slot/56-pole
4S-2P split has separate bookkeeping tests. Stage A 3-D effects, independent
dual-inverter PWM/controller behavior, per-set thermal distributions, and
single-set faults require their own evidence. No fault capability follows from
the balanced equivalent-machine equations.

Run evidence and implementation audit are retained in `_scratch/sixpass/REPORT.md`
and `_scratch/sixpass/equivalence/solver_equivalence.json`. The numerical check used
solver revision `2c1f2aa506ba1e4aad1cc6668f7873e323eb78a8`; the field solver was
not modified for this task.
