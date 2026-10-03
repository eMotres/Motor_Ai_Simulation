# Third-party notices

This project (Apache License 2.0, copyright MOTRES d.o.o. and contributors; see
LICENSE and NOTICE) uses the runtime dependencies below. Versions and licences were read from the
installed package metadata (`importlib.metadata`, `node_modules/*/package.json`)
on 2026-09-29; where the metadata was empty the licence was taken from the
project's own licence file. Each dependency remains under its own licence.
The MKL/pypardiso chain entries were re-read on 2026-09-30 after intel-openmp
and intel-cmplr-lib-ur were removed from the deploy image (note 2).
2026-10-03: the project licence changed from AGPL-3.0-or-later to Apache-2.0;
Triangle removed (note 3); gmsh runs only in a separate worker program
(note 1); Netgen is the default mesher (note 4). The installed set was
re-audited on 2026-10-03 from the package metadata of a server build (production
requirements + MKL chain + netgen 6.2.2607, no triangle; 121 distributions).

## Dependency model under Apache-2.0

Apache-2.0 is permissive: anyone may use, modify and redistribute this code,
commercially or not. Distributing it together with its dependencies requires
that each dependency's own terms are met, and that no dependency imposes its
licence on our code. The rules the code base follows:

| Kind | How it is used | Examples |
|---|---|---|
| Permissive (MIT, BSD, Apache-2.0, ISC, PSF, Zlib, HPND) | in process, freely | numpy, scipy, scikit-fem, shapely, fastapi, cadquery |
| LGPL | in process **only as a dynamically linked, replaceable library**, unmodified | Netgen + netgen-occt (note 4), OpenCASCADE (cadquery-ocp), GEOS (shapely), casadi (pulled by cadquery) |
| GPL | **never in a process with our code**; only as a separate program over a pipe | gmsh, in `gmsh_worker_main` (note 1) |
| Proprietary, freely redistributable | **optional at runtime**, never in the default image; the code runs without it | Intel MKL via pypardiso, ISSL (note 2) |
| Non-commercial / field-of-use | **not accepted** | Triangle, removed (note 3) |

## Python (requirements.txt)

| Package | Version | Licence |
|---|---|---|
| fastapi | 0.129.0 | MIT |
| uvicorn | 0.41.0 | BSD-3-Clause |
| pydantic | 2.12.5 | MIT |
| python-multipart | 0.0.32 | Apache-2.0 |
| numpy | 2.4.4 | BSD-3-Clause (bundled parts: 0BSD, MIT, Zlib, CC0-1.0) |
| scipy | 1.17.1 | BSD-3-Clause |
| shapely | 2.1.2 | BSD-3-Clause (links GEOS, LGPL-2.1) |
| scikit-fem | 12.0.1 | BSD-3-Clause |
| **gmsh** | 4.15.2 | **GPL-2.0-or-later**; separate worker program only (note 1) |
| netgen-mesher (+ netgen-occt 7.8.1) | 6.2.2607 | LGPL-2.1-only (netgen-occt: OpenCASCADE, LGPL-2.1 with the OCCT exception); in process, dynamically linked (note 4) |
| cadquery | 2.7.0 | Apache-2.0 |
| cadquery-ocp | 7.8.1.1.post1 | Apache-2.0 bindings; OpenCASCADE Technology LGPL-2.1 with OCCT exception |
| matplotlib | 3.10.8 | Matplotlib licence (PSF-based, BSD-compatible) |
| pyyaml | 6.0.3 | MIT |
| trimesh | 4.11.2 | MIT |
| pyjwt | 2.10.1 | MIT |
| argon2-cffi | 25.1.0 | MIT |
| anthropic | >= 0.40 | MIT |
| cma | 4.4.4 | BSD-3-Clause |
| sympy | 1.14.0 | BSD-3-Clause |
| mapbox-earcut | 2.0.0 | ISC (earcut.hpp: ISC) |
| meshio | 5.3.5 | MIT |
| scikit-learn | 1.9 | BSD-3-Clause |
| pillow | 12.2.0 | MIT-CMU (HPND) |
| psutil | 7.2.2 | BSD-3-Clause |
| reportlab | 5.0.0 | BSD-3-Clause |
| python-docx | 1.2.0 | MIT |
| openpyxl | 3.1.5 | MIT |
| mcp | 2.2.0 | MIT |

### Transitive Python dependencies (installed by the packages above)

All permissive (MIT, BSD, Apache-2.0, ISC, PSF, MIT-CMU) except those listed
here, audited from the resolved dependency closure of `requirements.txt` on
2026-09-29:

| Package | Pulled in by | Licence | Apache-2.0 distribution |
|---|---|---|---|
| casadi | cadquery | LGPL-3.0-or-later | compatible: unmodified, dynamically linked Python extension, replaceable |
| OpenCASCADE Technology (in cadquery-ocp) | cadquery | LGPL-2.1 with OCCT exception | compatible: dynamically linked, replaceable |
| GEOS (in shapely) | shapely | LGPL-2.1 | compatible: dynamically linked, replaceable |
| vtk, trame-* | cadquery-ocp | BSD-3-Clause / Apache-2.0 / MIT | compatible |
| setuptools | pip / packaging | MIT | compatible |

### Optional (not installed by default)

| Package | Install | Licence |
|---|---|---|
| pypardiso | `requirements-pardiso.txt`, or `--build-arg WITH_PARDISO=1` for `deploy/Dockerfile.api` | BSD-3-Clause |
| mkl (pulled by pypardiso) | same as pypardiso | Intel Simplified Software License (ISSL) — see note 2 |
| onemkl-license (pulled by mkl) | same as pypardiso | Intel Simplified Software License (ISSL) — see note 2 |
| tbb (pulled by mkl) | same as pypardiso | Intel Simplified Software License (ISSL) — see note 2 |
| tcmlib (pulled by tbb) | same as pypardiso | Intel Simplified Software License (ISSL) — see note 2 |

## Web client (web/package.json, runtime dependencies)

| Package | Version | Licence |
|---|---|---|
| @emotion/react | 11.14.0 | MIT |
| @emotion/styled | 11.14.1 | MIT |
| @hookform/resolvers | 5.2.2 | MIT |
| @mui/icons-material | 7.3.8 | MIT |
| @mui/material | 7.3.8 | MIT |
| @react-three/drei | 10.7.7 | MIT |
| @react-three/fiber | 9.5.0 | MIT |
| @react-three/postprocessing | 3.0.4 | MIT |
| firebase | 12.14.0 | Apache-2.0 |
| i18next | 26.4.2 | MIT |
| i18next-icu | 2.4.4 | MIT |
| i18next-resources-to-backend | 1.2.3 | MIT |
| intl-messageformat | 11.2.15 | BSD-3-Clause |
| postprocessing | 6.38.3 | Zlib |
| react | 19.2.4 | MIT |
| react-dom | 19.2.4 | MIT |
| react-hook-form | 7.71.1 | MIT |
| react-i18next | 17.0.15 | MIT |
| recharts | 3.8.1 | MIT |
| three | 0.183.2 | MIT |
| zod | 4.3.6 | MIT |
| zustand | 5.0.11 | MIT |

Transitive npm production dependencies (`web/package-lock.json`, non-dev
entries, re-audited 2026-10-03, 285 packages): MIT 183, Apache-2.0 58, ISC 22,
BSD-3-Clause 18, and one each of MIT AND ISC (victory-vendor), Zlib, 0BSD and
MIT (webgl-constants, licence file only). No copyleft or non-commercial
licence.

Development-only tools (pytest, black, ruff, mypy, Vite, TypeScript, ESLint)
are not distributed with the software and are not listed.

## Notes

1. **gmsh (GPL-2.0-or-later) — a separate program, never linked with the
   API process.** gmsh is used unmodified (the PyPI wheel). It is not
   combined with the Apache-2.0 API / compute process and never shares an
   address space with Intel MKL. Inside its own worker program gmsh runs
   next to this project's mesh-building code; Apache-2.0 is compatible with
   GPL-3.0, which gmsh's "or later" allows, so that program may be
   distributed under the GPL. Operators who do not want gmsh at all can leave
   it out: the default mesher is Netgen, and only the paths that still use
   gmsh (OCC 2-D meshing, mechanical and static 3-D meshes, the optional gmsh
   CDT backend) need it.

   **Process separation (2026-10-03).** gmsh is imported ONLY by the gmsh
   worker, a separate program (`python -m
   motor_ai_sim.simulation.gmsh_worker_main`) that the API and compute
   processes start and talk to over a pipe with plain data (pickled numpy
   arrays, meshes, polygons). No module the API/compute process imports
   loads gmsh — every gmsh path (2-D OCC meshing, the gmsh CDT backend,
   thermal and mechanical meshes, static 3-D meshes, version/provenance
   reads) goes through the worker (`motor_ai_sim/simulation/gmsh_worker.py`).
   The worker in turn refuses to import Intel MKL / pypardiso (an import
   hook raises), so GPL gmsh and proprietary MKL never share an address
   space. The worker only combines gmsh with GPL-compatible code (this
   project's Apache-2.0 code, the Python standard library,
   numpy/scipy/shapely/scikit-fem and their OpenBLAS/GEOS builds). Enforced by tests/test_gmsh_isolation.py
   (fresh API process: `gmsh` never in `sys.modules`, no libgmsh mapped;
   worker: no MKL module or library mapped) and
   tests/test_gmsh_process_boundary.py (no `import gmsh` outside
   worker-side functions).
2. **Intel MKL (via pypardiso) — installed WITHOUT intel-openmp (2026-09-30).**
   `mkl`, `onemkl-license`, `tbb` and `tcmlib` are under the **Intel
   Simplified Software License (ISSL, October 2022)**: binary
   redistribution and use permitted, with attribution and no reverse
   engineering, and — unlike the EULA below — **no SaaS or
   reciprocal-open-source restriction at all**. They are proprietary but freely
   redistributable; under Apache-2.0 nothing forbids loading them next to
   our code (no licence exception is needed). They are still OPTIONAL at
   runtime: not a default dependency, never shipped in the default container
   image, and every solver falls back to SciPy's SuperLU when pypardiso is
   absent, so the distributed software is complete without them. They never
   load into the gmsh worker (note 1). Operators may install the chain on their own
   machines (`requirements-pardiso.txt`, or `--build-arg WITH_PARDISO=1` for
   `deploy/Dockerfile.api`) for a several-times-faster transient solve.

   Two packages `mkl`'s PyPI wheel would otherwise pull in transitively —
   **`intel-openmp` and `intel-cmplr-lib-ur`** (which in turn pulls `umf`) —
   are **deliberately excluded**, in the image and in the pinned install
   command, by installing `mkl`, `onemkl-license`, `tbb`, `tcmlib` and
   `pypardiso` with `pip install --no-deps` at pinned versions. Those two
   ship under the "Intel End User License Agreement for Developer Tools"
   (August 2024), a materially different and more restrictive agreement
   whose §3.1(x) bars linking or distributing so that any part becomes
   Reciprocal Open Source Software and whose §3.1(xi) bars SaaS /
   service-bureau use — both wrong for AeroStator's hosted API. They are not
   needed: MKL's shared libraries select their threading backend at runtime
   via `dlopen()`, keyed on `MKL_THREADING_LAYER`, and only the `INTEL`
   backend ever touches `libiomp5.so` (the EULA'd library). The image sets
   `MKL_THREADING_LAYER=SEQUENTIAL`, which has no dependency on
   `libiomp5.so` at all; `src/motor_ai_sim/simulation/
   pardiso_threading_guard.py` refuses PARDISO (SuperLU fallback) and logs
   loudly if that variable is ever unset or reset to `INTEL`. See
   `C:\Users\vadim\Downloads\mkl_without_intel_openmp_2026-09-30.md` for the
   full licence analysis and the sandbox verification this recipe is based
   on. `umf` (Apache-2.0 with LLVM exceptions) is not installed either — it
   was only ever a dependency of the now-excluded `intel-cmplr-lib-ur`.
3. **Triangle — removed (2026-10-03).** Its licence permits only
   non-commercial use without the author's permission. The Triangle CDT
   backend, `requirements-triangle.txt`, the `[triangle]` extra, the
   `WITH_TRIANGLE` image build argument and every `import triangle` were
   deleted; the API image build fails if the package is ever installed.
   The 2-D view's earcut fallback uses shapely (GEOS, LGPL-2.1).
4. **Netgen (LGPL-2.1) — the default geometry mesher.** `netgen-mesher` is
   pinned at `6.2.2607` and imported in-process as an unmodified library
   (`import netgen`); it is dynamically linked (Python extension modules and
   shared objects inside the wheel), so a user can replace it by installing
   another build of the same package (`pip install netgen-mesher==<version>`)
   without relinking this program. Its bundled OpenCASCADE (`netgen-occt`) is
   LGPL-2.1 with the OCCT exception. Netgen is preferred over gmsh because
   LGPL, unlike GPL, imposes no condition on code that merely calls the
   library, which matters for running next to proprietary Intel MKL in the
   same process. Under Apache-2.0: Netgen is used unmodified through its
   public Python API; its LGPL terms (the user's right to replace the
   library, its source being available upstream) apply to Netgen only and do
   not extend to our code. gmsh (note 1) remains selectable (`MOTOR_AI_SIM_GEO_CDT=gmsh`)
   but is not the default and always runs in its worker process. Netgen
   cannot run on the owner's Windows workstation (App Control blocks its
   DLLs); Windows development machines run the API and solves under WSL2 or
   on the server.
