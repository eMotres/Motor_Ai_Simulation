# Preserve P2 settling-prefix scalar samples

P2 voltage-drive and demagnetisation settling windows are removed in sequence
by `drop_settling_frames`. That remains the source of the operating-window
metrics. Before the first destructive trim, the solver now snapshots its
compact scalar histories into the additive `P2_transient_sample_history`
result field. Existing reported torque, current, flux, time, losses, and
settling calculations are unchanged.

The snapshot carries absolute pre-rebase time, the mechanical angle actually
used by each solve frame, torque, three phase currents and flux linkages,
voltage diagnostics and applied voltages, eddy scalar losses, and iteration
counts. Each channel is retained independently with its own sample count.
Metadata marks the core waveform aligned and supplies the raw index and
absolute time/angle bounds of the retained window only when the core channel
lengths agree; otherwise those derived indices and bounds are `null`. This
avoids padding or pairing samples by assumption. The existing sequential
voltage and demag trims and their requested frame counts are listed separately.

The snapshot does not copy per-element B histories, rotor-potential vectors,
or eddy loss-density maps. Their before/after sample counts are recorded so
the payload can show that these larger histories were trimmed without
duplicating their array contents. The P1 legacy settling trim and UI/report
behavior are outside this change.

Standalone synthetic checks exercise the real in-place trim helper across
consecutive voltage/demag-style trims, confirm original absolute time and
angle bounds survive rebasing, and verify that unequal channel lengths are
reported without inferred alignment. They passed with:

```powershell
& 'C:\Users\vadim\AppData\Local\Programs\Python\Python311\python.exe' -B tests/test_transient_sample_retention.py
```
