# Archived terminal-work physical-certificate preflight — 2026-09-24

## Result

**No physical certificate is available from the saved states.** The new read-only preflight reruns the existing run04 closure diagnostic, inventories the corrected run15 capture and frozen-current run16 capture, and checks current source bytes against each capture's SHA-256 provenance. It returns nonzero while `certified=false`; it does not select a torque method or change the production threshold.

Run15 stores all 96 current/linkage/angle waveform samples, but full P2 field/material states only at frames **0, 1, 48 and 49**. These give two endpoint pairs spanning one electrical period, `0→48` and `1→49`. Frames **2–47** have no full-state `.npz`/metadata pair in this capture. The endpoint energy changes were previously reported as `+1.0902078498853385e−6 J` and `+8.860720643322217e−7 J`; they are not pass/fail closure evidence without a stated tolerance and mapped-field error bound. The present preflight records those historical values but marks their reproduction blocked when the capture's source hash no longer matches.

Run16 reports 96 converged states for **48 frozen-current ±one-slip-cell equilibrium pairs**, and all 96 NPZ/metadata file pairs are present. It is not a transient trajectory. The previously reported pair-mean coenergy secant is `0.4095018981 N·m` versus run15 cubic terminal path work `0.4096487787 N·m`, a difference of `−0.0001468806 N·m`. The result is a useful finite discrete comparison, but it does not establish a continuous derivative through the integer moving-weld projection or bound the secant's step error.

## Provenance and reproduced diagnostics

Run15 waveform bytes match the capture-review hash at SHA-256 `df9173f6ddcb51818e4e658892fd087fe1a0d86c8c875b948669c218870ac404`; its provenance file does not separately record that waveform hash. The provenance records Git head `68de0cae2dc34ceffbec4757904107e82ffffc15`. Run15 and run16 both expect these solver source hashes from capture time:

| Source | Expected at capture | Current bytes | Match |
|---|---|---|---|
| `fem_solver_2d.py` | `3bdb54776806de88b6b03801b697beb78096cc64d80978e18b7b6c1a56dca61f` | `f6a5ef4462db1b029e4111918603890f7ca95379e8a6d0faa80127d7939722f4` | no |
| `field_ops.py` | `3a30cb1499695357e2e9021dcabd3b100d22ab8155754e610d943cbf858f46e6` | `0ea5eb6841b7d475a7e344febaed465ee96c129857be45d91d97c1bd9a9e693e` | no |
| `sb_postproc.py` | `264ef283083a24200132033a6576c9c246c398f9028607a01ad37186724de48b` | `cfe1c450fcd678be32d3d3cb4a6224407d43fb3499f8cb7538e71f180cb88f68` | no |
| `p2_nonlinear.py` | `655626d95bec54a35852f140ddc38bccb5061d7d68e9944194858d7aadc2a0da` | same | yes |

Accordingly, the archived run15 energy/linkage reconstruction and run16 paired-secant reviewer both stop at their source-provenance guards in the current checkout. The run15 source-linkage and run16 energy/linkage values above remain prior archived report results, not re-executed checks in this audit. The raw terminal waveform hash still validates. The preflight does reproduce the byte-bound run04 diagnostic from `scripts/torque_offline_closure.py`:

This current-checkout source drift blocks those two reviewer replays; it does not by itself invalidate the unchanged archived data or establish a physical validation failure. The machine-readable result separates this provenance guard from missing certificate requirements and labels the run13/run14 gap as a historical value rather than a new calculation.

| Run04 saved-data diagnostic | Value |
|---|---:|
| Terminal path work, saved endpoint | `0.4084804159 N·m` |
| Artificial cyclic closure comparison | `0.4084789433 N·m` |
| Whole-machine potential change, frames 0→48 | `+1.09020785e−6 J` |
| Potential-change/angle arithmetic term | `+1.21458e−6 N·m` |
| Raw Maxwell mean | `0.4081997845 N·m` |
| Terminal path minus raw Maxwell | `+0.0002806314 N·m` |
| Archived source/linkage mismatch maximum | `5.65267315e−9 Wb` |

The `+0.2650477869242 N·m` terminal-work-minus-Maxwell mean reported in the separate run13/run14 merged 120-position study is a raw mean discrepancy at a different GEO150 operating point. It is neither a closure residual nor an error bar and must not be conflated with the GEO30 run15/run16 comparisons.

## Remaining evidence gap

The available endpoint energy differences are measurable but nonzero; there is no declared closure tolerance and no error estimate for rotor-field mapping or angular discretization. The run16 calculation establishes 48 one-cell finite secants only. There is no all-period sequence of frozen-current displacements at multiple step sizes, nor a justified limit/error bound for the moving-weld derivative. No saved result establishes a complete electromechanical port-energy identity with a consistent PM reference, terminal source work, stored-energy change and mechanical work under a declared residual threshold. The production terminal-work selector must therefore remain unchanged on this evidence.

## Reproduction

Read-only command:

```powershell
& 'C:\Users\vadim\AppData\Local\Programs\Python\Python311\python.exe' scripts/torque_energy_certificate_review.py
```

It emits JSON and exits nonzero while any certificate remains unresolved. It performs no FEM solve, API call, configuration write, filtering, or source-array mutation. Focused tests:

```powershell
& 'C:\Users\vadim\AppData\Local\Programs\Python\Python311\python.exe' -m pytest tests/test_torque_energy_certificate_review.py --noconftest -o addopts= -p no:cacheprovider -q
```
