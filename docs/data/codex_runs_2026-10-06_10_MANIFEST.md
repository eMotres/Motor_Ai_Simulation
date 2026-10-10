# Codex run evidence 2026-10-06..10 — archive manifest

Codex (OpenAI GPT-5.6 Sol/Luna/Astra orchestration) committed its raw run
evidence into git on the local, never-pushed line
`fix/dim-dashboard-s1-housing` (tip `4d9c581f`, 155 commits on `702a2b33`).
That evidence is **not** in the repository. It is preserved byte for byte on
the server, and this file plus the per-file table
[`codex_runs_2026-10-06_10_MANIFEST.tsv`](codex_runs_2026-10-06_10_MANIFEST.tsv)
(path, size, sha256, git blob id) are what the repository keeps.

## Where

Server `176.9.84.229`, directory
`/srv/motres/archive/codex_runs_2026-10-06_10/` (root only, files read-only):

| File | Bytes | sha256 | Contents |
|---|---:|---|---|
| `codex_runs_2026-10-06_10.tar.gz` | 122,169,808 | `8fbd37e63e60c2fcc28a948696648ac1c2a60f8b9095312bda66c4d81d43d879` | `git archive 4d9c581f -- scratch_s1_20261006 coordination/opus55-d85-api-audit-2026-10-09.md` under the prefix `codex_runs_2026-10-06_10/`: 4,193 files, 337,432,897 bytes uncompressed |
| `codex_runs_2026-10-06_10_MANIFEST.tsv` | 1,059,468 | `f5e19ea42266f0b58c30ff4ce71c02bae819273bc4bd687c257a04cfe6e6cb74` | the same table as in this folder |
| `codex-local-line_702a2b33..4d9c581f.bundle` | 57,120,177 | `f2ee2569681490e5103639bba25b6a41b17bd94d372636b10d512b22761bd5ef` | git bundle of the whole local line (all 155 commits, history and authorship) |
| `d85-fixed-speed-f8c7f097950d492b_server-stage.tar.gz` | 39,488,167 | `b3cbdda8dd6ac543421af4508d7b36029cc47faeab22518b304a2dcffaf3891a` | the D85 15-point speed run as the server left it (`/var/lib/motres/d85-fixed-speed-f8c7f097950d492b`); its last three points finished after `4d9c581f` and exist only here |
| `SHA256SUMS`, `SHA256SUMS.server` | | | checksums of the above |

Verify: `cd /srv/motres/archive/codex_runs_2026-10-06_10 && sha256sum -c SHA256SUMS SHA256SUMS.server`.
Restore one file: `tar -xzf codex_runs_2026-10-06_10.tar.gz 'codex_runs_2026-10-06_10/<path>'`.
Restore the history: `git fetch <bundle> fix/dim-dashboard-s1-housing:codex-local-line`.

## What is in it

| Folder | Files | MB |
|---|---:|---:|
| `scratch_s1_20261006/d85_finish_20261007` | 3,607 | 251.6 |
| `scratch_s1_20261006/d85-fixed-speed-execution-20261010` | 210 | 67.8 |
| `scratch_s1_20261006/d85-fixed-angle-execution-20261010` | 114 | 15.3 |
| `scratch_s1_20261006/propeller-nonempty-catalog-20261010` | 53 | 0.8 |
| `scratch_s1_20261006/propeller-all-curves-repair-20261009` | 29 | 0.6 |
| `scratch_s1_20261006/propeller-rpm-charts-20261009` | 35 | 0.5 |
| `scratch_s1_20261006/propeller-rpm-charts-simplified-20261009` | 47 | 0.4 |
| 8 smaller D85 / propeller folders | 97 | 0.3 |
| `coordination/opus55-d85-api-audit-2026-10-09.md` (Russian-language audit note, kept out of the English-only repo) | 1 | 0.04 |

By type: 2,150 JSON, 642 Python, 391 text, 292 Markdown, 256 logs, 135 YAML,
37 tar transports, 29 JSONL, 11 PNG, 3 NPZ. They are FEM request/response
payloads, container logs, preflight and cleanup records, review verdicts,
staged web/API release packages and launcher scripts of the D85 card work and
the propeller UI releases.

## What was kept in the repository instead

- `docs/data/passport_d85/L13_live_store_2026-10-06.json` — a copy of the
  D85 passport store the live server serves
  (`/srv/motres/shared/passports/CIANO28 85 20SW1200/L13.json`, sha256
  `3605ffd9…7fe53`, identical to the archived
  `d85-live-passport-store-L13-1843.json`; git stores it with LF line
  endings, a CRLF checkout reproduces the server bytes exactly). It is kept
  as a reference, **not** under `config/passports/`: its PWM variants carry no
  `motor_pwm_loss_W` (the store's own provenance excludes motor-side PWM
  loss), which fails `tests/test_passport_store.py::
  test_every_stored_variant_is_readable_by_configure`. The live server's copy
  is unaffected.
- The 48 Codex notes under `docs/` and the coordination records — see
  `docs/CODEX_WORK_2026-10-06_10.md`.

No HTML cards and no other product-read file exist in the evidence; nothing
in `src/`, `web/` or `deploy/` reads a `scratch_*` path.

`.gitignore` now ignores `scratch_*/`, so raw run folders cannot be committed
again by accident (the long-tracked `scratch_perf/` is unaffected).
