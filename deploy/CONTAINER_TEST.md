# Proving the image on Linux — the Stage 6 gate

**Status on 2026-09-15: NOT RUN.** Docker is not installed on the Windows
workstation (`docker` resolves to nothing; no Docker Desktop, WSL2 `Ubuntu`
present but stopped and without a docker engine). The fallback was taken — the
full suite was re-run on Windows against the new `requirements.txt` — and this
file is the runbook for the first run on the server, which is where the real
answer comes from anyway.

Run it **before inviting anyone**, and read it as one question:

> The image installs three dependencies that were never pinned until today, on
> an OS the product has never executed on. Do the two report formats actually
> render, and does the physics still produce the same numbers?

Windows cannot answer either half. `mapbox_earcut` and `OCP` are blocked by App
Control there, so the `triangle` fallback and the DXF+macro FreeCAD bundle are
the *only* paths the workstation has ever exercised; on Linux the native ones
load and become live code for the first time. And no Cyrillic has ever been
rendered through the base-14 font path.

---

## 1. Build both targets

From the repo root on the server:

```bash
docker build -f deploy/Dockerfile.api -t motres-api .
docker build -f deploy/Dockerfile.api --target test -t motres-api:test .
```

The second reuses every layer of the first and adds `pytest`, `httpx` and
`tests/`. Expect ~5 min cold (gmsh + OCP + MKL are ~1.5 GB of wheels) and
seconds warm.

Sanity-check the three new pins landed:

```bash
docker run --rm motres-api python -c \
 "import reportlab, docx, triangle, matplotlib; \
  print(reportlab.Version, docx.__version__ if hasattr(docx,'__version__') else 'ok', \
        triangle.__version__ if hasattr(triangle,'__version__') else 'ok')"
```

And that the native paths Windows has never used now import:

```bash
docker run --rm motres-api python -c \
 "import mapbox_earcut, OCP; print('earcut + OCP: native paths live')"
```

If that line succeeds, `earcut_fallback.py` and the FreeCAD macro bundle stop
being the primary path and become the belt-and-braces they were designed as.
Both stay in the image; neither is removed on the strength of one green run.

## 2. Run the suite inside the image

```bash
docker run --rm \
  -e MOTOR_AI_NO_FILE_LOG=1 \
  motres-api:test python -m pytest -q
```

**~20 minutes.** The physics regression alone is ~16 of them — it re-solves the
reference machines and compares against `tests/physics_baseline.json`, which is
the only assertion in the suite that would catch "MKL on Linux rounds
differently". Do not pass `-x`: a single early failure would hide exactly the
long tail this run exists to sample.

Module by module, if you want to see progress:

```bash
docker run --rm motres-api:test bash -c \
  'for f in tests/test_*.py; do echo "== $f"; python -m pytest -q "$f" || echo "FAIL $f"; done'
```

Expected: the same counts as the Windows run recorded in the Stage 6 commit
message, **minus** any test that skips on a case-insensitive filesystem —
`tests/test_startup_checks.py` has two of those, and on ext4 they should now
*run* rather than skip. That flip is itself a result: it means the collision
check was exercised for the first time on the filesystem it was written for.

## 3. Render an L155 report through the container's own libraries

This is the half that cannot be delegated to a unit test, because what is being
checked is a font.

```bash
# the container runs as uid 10001 (motres), so the output directory has to be
# writable by it — not by you.
mkdir -p out && sudo chown 10001:10001 out

# a copy of the catalog, mounted read-only
docker run --rm \
  -v /srv/motres/seed-src:/seed:ro \
  -v "$PWD/out":/out \
  -e MOTOR_AI_SIM_CONFIG=/seed/motor_config.yaml \
  motres-api python - <<'PY'
from fastapi.testclient import TestClient
from motor_ai_sim.api import app

DIE, CFG = "CIANO10 200 opt", "L155"
with TestClient(app) as c:
    for fmt, ext in (("docx", "docx"), ("pdf", "pdf")):
        r = c.get(f"/api/family/report/{DIE}/{CFG}", params={"format": fmt})
        assert r.status_code == 200, (fmt, r.status_code, r.text[:400])
        out = f"/out/L155.{ext}"
        open(out, "wb").write(r.content)
        print(fmt, len(r.content), "bytes ->", out)
PY
```

Both files must build. Then check the thing the byte count cannot:

* **Cyrillic duty names render as letters, not boxes.** Duty names admit
  Cyrillic deliberately (`routes/family.py`), and the reportlab fallback is the
  Helvetica base-14 set, which has no Cyrillic at all. `report.py` prefers
  DejaVu Sans out of matplotlib's own data path, and the image installs
  `fonts-dejavu-core` on top as insurance — this render is what proves one of
  the two found it. Open the PDF and look at a duty caption; `pdffonts
  out/L155.pdf` should list a DejaVu face.
* **The duty folder names survived the transfer.** `_duty_stem()` reduces a
  duty name to `[A-Za-z0-9_]` plus an 8-hex sha1 **of the original UTF-8
  bytes**, so `пик 200` and `pik 200` are different folders by construction. If
  the report renders but the figures are empty, the stems on disk do not match
  the stems the code derives — an NFD copy, not a font problem. Re-check with
  `scripts/catalog_manifest.py --compare`.

## 4. What a failure means

| symptom | read it as |
|---|---|
| `ImportError: reportlab` / `docx` / `triangle` | the image was built from something other than this `requirements.txt` |
| report builds, Cyrillic is boxes | neither DejaVu path resolved — check `matplotlib.get_data_path()` and that `fonts-dejavu-core` installed |
| physics regression drifts | MKL/PARDISO on Linux vs Windows. Compare against `tests/physics_baseline.json` tolerances before assuming a port bug; record the deltas either way |
| `startup_checks` refuses to boot | two dies differ only by case. **Correct behaviour** — fix the catalog, do not disable the check (`python scripts/check_case_collisions.py --tree /srv/motres`) |
| a test that passed on Windows now fails | the native path (`mapbox_earcut`, `OCP`) is live for the first time. The fallback is not at fault; the native result is the new reference |

## 5. The Windows fallback that was actually run (2026-09-15)

With no Docker available, the suite was re-run on this workstation against the
new `requirements.txt` to prove nothing broke by adding the three pins. That
answers "the dependency change is inert" and answers **nothing** about Linux:
not the native earcut/OCP paths, not MKL's arithmetic, not the fonts, not case
sensitivity. Sections 1–3 above remain owed on the first server.
