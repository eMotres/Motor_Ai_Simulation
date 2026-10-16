# NS4 terminal-work alias attribution (2026-09-24)

This is a read-only finite-grid decomposition of the archived run13 even and
run14 odd G150 24s/28p states. The script
scripts/torque_terminal_work_alias_review.py reuses the strict hash, frame,
convergence, angle, current, and linkage checks from
scripts/torque_terminal_work_sampling_review.py. It then applies the production
sb_postproc.terminal_work_mean to N=20/30/40/60/120 decimations, including
every phase offset. No solver, FEM, API, config, or source archive is modified.

For the validated 120-position sequence, the normalized full complex spectra
are retained for all three phase currents and linkages. For each coarse signed
bin k and each phase, the report retains every ordered source pair (q,p) in
the same residue class, with its coarse complex contribution, diagonal
120-point reference subtraction, and their complex difference. The reported
pair rows include zero-valued terms and both signs of conjugate pairs. The
phase multiplier is exp(+2πi(p-q)r/120), and the coarse derivative multiplier
uses the actual signed mechanical angle step. The even-grid Nyquist multiplier
is zero as in production; source/reference Nyquist bins remain present.

The decomposition is bilinear: its terms are conj(I[q])*Psi[p], not a fold or
sum of source work bins. For q=p it subtracts the corresponding 120-point
work term; q≠p terms are cross-alias contributions with no 120-point
cycle-mean counterpart. Summing all pairs and coarse signed bins reproduces
the direct production helper delta. Source samples are not filtered, demeaned,
or discarded from the 120-point reference record; each coarse offset explicitly
selects its stated subset of source positions.

## Result

The 120-position production terminal-work reference is **40.5125048341 Nm**.
For N=20 offset 0, the direct coarse-minus-reference delta is
**+0.000672597316388 Nm**. Its complete pair attribution is:

| Source current order q | Source linkage order p | Conjugate-pair real delta (Nm) |
|---:|---:|---:|
| +1 / −1 | −19 / +19 | +0.000401122654298 |
| +1 / −1 | −39 / +39 | +0.000117338215265 |
| +1 / −1 | −59 / +59 | +0.000103039229167 |
| +1 / −1 | +41 / −41 | +0.000083318489905 |
| +1 / −1 | +21 / −21 | −0.000032221272241 |
| **Sum** | | **+0.000672597316394** |

These are the dominant terms at the precision shown; the full JSON preserves every other pair
and bin as well. The linkage orders −19, −39, −59, +41, +21 all fold to the
coarse signed order +1 at N=20, with conjugate partners at −1. Accordingly,
**+0.000672597316396 Nm** of the delta has at least one source order above
the N=20 Nyquist order 10; only **−5.3e−15 Nm** is attributed to pairs whose
two source orders are both within that Nyquist range. The high-linkage-source
contribution entering coarse ±1 is **+0.000672597316394 Nm**. Terms involving
a high current source order sum to about **1.6e−15 Nm**. The measurable delta
is therefore attributable to the aliased high-order linkage/current cross
terms in these discrete arrays, rather than a meaningful change in the
within-Nyquist source-order pairs.

The exact N20 deltas across offsets 0..5 are
[+0.000672597316, +0.000470601125, +0.000170158580, −0.000271283906,
−0.000533638209, −0.000508434907] Nm.

| N | Offsets | Deltas versus N=120 (Nm), in offset order | Maximum absolute delta (Nm) |
|---:|---:|---|---:|
| 20 | 6 | +0.000672597, +0.000470601, +0.000170159, −0.000271284, −0.000533638, −0.000508435 | 0.000672597 |
| 30 | 4 | +0.002076745, −0.001634979, −0.001870666, +0.001428900 | 0.002076745 |
| 40 | 3 | +0.000200657, −0.000031519, −0.000169138 | 0.000200657 |
| 60 | 2 | +0.000103039, −0.000103039 | 0.000103039 |
| 120 | 1 | 0 | 0 |

Running python scripts/torque_terminal_work_alias_review.py writes a JSON-safe
full report to stdout; --output PATH also saves it. The output includes all
120-point source samples and complex coefficients, all offsets, all source
pair rows grouped by phase and coarse signed bin, binwise complex closure,
and high/low source-order attribution. The script's arithmetic gate passes
when pair expansion closes to the direct production-helper delta within
5e−11 Nm and the alias-fold FFTs close within 2e−12 coefficient units.

This is an **arithmetic-only gate**: passed=true means the discrete bilinear
decomposition agrees with the production helper and strict archived data.
certified=false remains explicit. The 120-position sequence is only the finest
archived integer-slip grid, not a continuum or physical truth. There are no
corresponding 120-position NS1 or NS2 sequences, so this result cannot correct
or attribute those symmetry modes. It does not establish stored-energy
closure or the physical validity of the terminal-work torque.
