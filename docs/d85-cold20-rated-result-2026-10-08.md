# D85/L13 actual cold rated point

One reviewed resumed request completed through the normal authenticated production route after the owner requested cold calculations and parallel work. It is a single simulated reference point, not a qualified cold passport or continuous S1 rating.

Run `cold20-d85-l13-rated-long-27b857362ada282d8e1572aa`: 13 mm, 85 mm OD, 24 slots/28 poles, 18 turns, 4S/one parallel path/star, current drive, 1000 rpm, 25.88 Arms, gamma 2 degrees, coil/magnet 20 C, 120 steps/one period. Request SHA `4b6c6a519444e9fdb685961bbebd8eba452e1e5b34d9b61792add8671c19e017`; raw response SHA `c8411c59582fa26824f8ca11b255543e6ba4745779a838a0b7b5c1399bb2b5f5`.

HTTP 200, `ok=true`, job done, 554.83 seconds. The request used the fresh ordinary workspace93 build with explicit rated mesh; selected D85/L13/peak/null reference stayed unchanged. Request-scoped shaft included follows the recorded owner accounting decision; the captured config still says reference. The earlier point-specific torque residual exception is not extended. No API restart or card write: `written_to_last=false`, `written_to_duty=false`.

| Returned quantity | Value |
|---|---:|
| Kt | 0.217 Nm/Arms |
| Km | 0.5018 Nm/sqrt(W) |
| Km/mass | 1.2641 Nm/(sqrt(W) kg) |
| Electromagnetic torque | 5.615 Nm |
| Phase resistance at 20 C | 0.062303 ohm |
| PM flux linkage | 0.00833557 Wb |
| Loaded Ld / Lq | 0.1400 / 0.1423 mH |
| Zero-current incremental Ld0 / Lq0 | 0.1435 / 0.1438 mH |

Kv 47.240832 is explicitly `kv_walked=true`, corrected by linear Br temperature scaling from the magnet card at 120 C; it is not a separately measured cold no-load FEM Kv. Torque basis is 2-D with no 3-D correction. Raw convergence/steady/mesh/part-state solver readback flags are absent from this response; no qualification is inferred from HTTP success. Separate no-load, MTPA, field-weakening, short-circuit, hot/thermal/voltage/mechanical and maximum-S1 gates remain.

The first 300-second attempt and its cancellation remain in `d85-cold20-first-attempt-2026-10-08.md`. The resumed attempt changed only the run identity and allowed budget. An initial local inline preparation command failed from quoting before SSH or submission; the saved file-based preparer then succeeded. No duplicate rated gamma-2 FEM is needed in the proposed seven-point batch.

Evidence: `scratch_s1_20261006/d85_finish_20261007/d85-cold20-launch-20261008/actual-resumed-cold20-audit.md`, raw response and client JSONL. GPT-6 implementer/reviewer/root; no new model escalation. Independent audit accepted the response scope; cold values are not yet installed in the private Configure card.
