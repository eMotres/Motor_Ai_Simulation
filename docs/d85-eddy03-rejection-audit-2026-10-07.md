# D85 `eddy_true_03` rejection audit — 2026-10-07

## Finding

The saved D85 attempt `eddy_true_03` was correctly unacceptable for the requested steady, demagnetisation-enabled point. At point 3 it requested 6.47 A RMS, 1000 RPM, 60 steps per period, coil temperature 180 °C, magnet temperature 150 °C, coupled eddy currents on, and demagnetisation on. The response records nonlinear convergence and eddy settlement as true, but `steady_state=false` and `demag_settled=false`; the candidate records `acceptance=false` and `published=false`.

The solver's stated reason is specific: after 32 demagnetisation pre-pass periods, mean-torque drift between the last two periods was 0.00213 against a 0.001 tolerance. Ripple drift was 0.027 percentage points against a 0.127 tolerance, so ripple drift did not trip that tolerance. The reported mean torque was 1.2373229586 Nm and ripple was 12.4%. This evidence establishes a failed settling/acceptance check for this point. It does not establish general physical instability, nor that a different discretisation or longer run would pass.

The response was not a cached ledger result (`probe_match=false`); its recorded wall time was about 1293 s. The original generator caught the harness's ordinary `Exception` on rejection, continued with points 4 and 5 (12.94 A at 250 and 500 RPM), and started point 6 (12.94 A at 1000 RPM). The owned container was stopped at the recorded 20-second timeout with exit 137. That stop is not evidence of another completed physical result, and no later point-6 completion is present in the post-rejection log.

## Evidence and provenance

Evidence is retained in `scratch_s1_20261006/d85_finish_20261007/`:

- `configure_eddy03_rejected_candidate.json` records the negative, unpublished candidate and point-3 gates.
- `configure_eddy03_solve003.response.json.gz` retains the complete point-3 solver reply.
- `configure_eddy03_solves_after_rejection.jsonl` shows the generator continuing after point 3 and the unfinished point-6 start.

The recorded provenance is solver SHA256 `cca2567aee37e2face2bfde85ca9ee2007427275ad7702ffdcb67b6b3ccef86d`; snapshot `ce4d6412caa901d18e0ee2615073b523883e172b023d06f56776a2baac2c5f77`; geometry `744e408997d27c8878362229e864ee6e440cccb818dbe5485202d053e37bf021`; materials `7cae09c102b712be855a6fe991b4b5138087929a81e60583ea23660a336ad877`; and point-3 request `8a2ad9ca2015ceb2176cf687e7cd453741e07b086fd4edb25225ea8c7f068f94`. The recorded gzip and decompressed-JSON hashes are `8171353593740c74f9a6e87d4b8ec5a0fb2a20d307bd055b502edd3821168742` and `ea219a2a088ec682ef39769493e62597ba55d44d49d75764c1cc2adf2fe3a2af`. The project checkpoint states that these two hashes were independently rechecked after the workspace limit was renewed.

## Harness correction and verification

The owned harness now persists a rejected candidate before raising `_SolverAcceptanceAbort`, a `BaseException` subtype that bypasses the legacy generator's `except Exception` recovery. The outer harness catches that exact stop signal after route restoration and returns a blocked status without replacing the negative candidate. A regression fixture uses an attempt-scoped checkpoint, a fake generator that catches ordinary exceptions, and asserts that only the first callback runs while both the negative candidate and compressed raw reply remain present.

The fixed-RPM point adapter now retains decoded raw responses and their canonical JSON hashes on early refusal paths, including failed envelope/source checks and missing-coupling, wrong-solve-mode, or non-converged responses. The pure point/search tests exercise stubs only; they do not run FEM or qualify a physical point.

Focused verification on 2026-10-07:

- `python -m unittest discover -s scratch_s1_20261006/d85_finish_20261007 -p test_configure_reference_status.py -v` — 8 tests passed.
- Python 3.11 with the release `src` directory first on `sys.path`, running `tests/test_propeller_s1_point.py tests/test_propeller_s1_search.py -q` — 48 tests passed in 0.28 s.

No solver was relaunched, no production/API state was changed, and these software tests do not qualify the D85 S1 operating point or establish a maximum-RPM propeller rating. The rejected result remains rejected; any future physical verification must be a separately authorized, bounded run with its raw evidence retained.
