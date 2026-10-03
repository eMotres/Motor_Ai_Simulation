# Third-party notices

This project (GNU AGPL-3.0-or-later, copyright MOTRES d.o.o. and contributors)
uses the runtime dependencies below. Versions and licences were read from the
installed package metadata (`importlib.metadata`, `node_modules/*/package.json`)
on 2026-09-29; where the metadata was empty the licence was taken from the
project's own licence file. Each dependency remains under its own licence.
The MKL/pypardiso chain entries were re-read on 2026-09-30 after intel-openmp
and intel-cmplr-lib-ur were removed from the deploy image (note 2).

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
| **gmsh** | 4.15.2 | **GPL-2.0-or-later** (see note 1) |
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

| Package | Pulled in by | Licence | AGPL-3.0 verdict |
|---|---|---|---|
| casadi | cadquery | LGPL-3.0-or-later | compatible |
| OpenCASCADE Technology (in cadquery-ocp) | cadquery | LGPL-2.1 with OCCT exception | compatible |
| GEOS (in shapely) | shapely | LGPL-2.1 | compatible |
| vtk, trame-* | cadquery-ocp | BSD-3-Clause / Apache-2.0 / MIT | compatible |

### Optional (not installed by default)

| Package | Install | Licence |
|---|---|---|
| triangle | `requirements-triangle.txt` or extra `[triangle]` | Python wrapper LGPL-3.0; bundled Triangle C code by J. R. Shewchuk: **free for non-commercial use only** (see note 3) |
| netgen-mesher (+ netgen-occt) | `requirements-netgen.txt` or extra `[netgen]`; default geometry mesher (`MOTOR_AI_SIM_GEO_CDT`, default `netgen`) | netgen-mesher: LGPL-2.1-only (its wheel also bundles GLU/Xmu/Xt/OpenGL loader libraries for the unused GUI); netgen-occt: OpenCASCADE Technology, LGPL-2.1 with the OCCT exception. Used in-process as a dynamically linked library (Python wheel with shared objects) that the user can replace by installing another build of the same package — see note 4 |
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
| postprocessing | 6.38.3 | Zlib |
| react | 19.2.4 | MIT |
| react-dom | 19.2.4 | MIT |
| react-hook-form | 7.71.1 | MIT |
| recharts | 3.8.1 | MIT |
| three | 0.183.2 | MIT |
| zod | 4.3.6 | MIT |
| zustand | 5.0.11 | MIT |

Transitive npm production dependencies (`npm ls --omit=dev --all`, 275
packages, audited 2026-09-29): MIT 175, Apache-2.0 57, ISC 22, BSD-3-Clause
17, and one each of MIT AND ISC (victory-vendor), Zlib, 0BSD and MIT
(webgl-constants, licence file only). No copyleft-incompatible or
non-commercial licence.

Development-only tools (pytest, black, ruff, mypy, Vite, TypeScript, ESLint)
are not distributed with the software and are not listed.

## Notes

1. **gmsh (GPL-2.0-or-later).** "Or later" allows use under GPL-3.0, and
   GPL-3.0 section 13 explicitly permits combination with AGPL-3.0 code, so
   distributing this project under the AGPL together with gmsh is compatible.
   gmsh is used as an unmodified Python package (`import gmsh`).
2. **Intel MKL (via pypardiso) — installed WITHOUT intel-openmp (2026-09-30).**
   `mkl`, `onemkl-license`, `tbb` and `tcmlib` are under the **Intel
   Simplified Software License (ISSL, October 2022)**: binary
   redistribution and use permitted, with attribution and no reverse
   engineering, and — unlike the EULA below — **no SaaS or
   reciprocal-open-source restriction at all**. They are proprietary, freely
   redistributable, and not a "System Library" under GPL/AGPL section 1, so
   they are not a default dependency and are never shipped in the default
   container image; every solver falls back to SciPy's SuperLU when
   pypardiso is absent. Operators may install the chain on their own
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
3. **Triangle (optional, being phased out).** Its licence permits only
   non-commercial use without the author's permission, a restriction the AGPL
   does not allow, so it is **not a dependency of and not bundled in** the AGPL
   distribution: it is an optional extra the operator installs separately.
   MOTRES currently uses the project non-commercially and installs it on its
   own machines so that results stay identical during the transition to gmsh
   (docs/MESHER_TRANSITION.md). Without it the geometry mesher falls back to
   gmsh (one log line) and the 2-D view uses mapbox-earcut with a shapely
   (GEOS) constrained-Delaunay fallback. It will be removed at stage S5.
4. **Netgen (LGPL-2.1) — the default geometry mesher.** `netgen-mesher` is
   pinned at `6.2.2607` and imported in-process as an unmodified library
   (`import netgen`); it is dynamically linked (Python extension modules and
   shared objects inside the wheel), so a user can replace it by installing
   another build of the same package (`pip install netgen-mesher==<version>`)
   without relinking this program. Its bundled OpenCASCADE (`netgen-occt`) is
   LGPL-2.1 with the OCCT exception. Netgen is preferred over gmsh because
   LGPL, unlike GPL, imposes no condition on code that merely calls the
   library, which matters for running next to proprietary Intel MKL in the
   same process. gmsh (note 1) remains selectable but is no longer the
   default. Netgen cannot run on the owner's Windows workstation (App Control
   blocks its DLLs); Windows development machines use WSL2, or set
   `MOTOR_AI_SIM_GEO_CDT=triangle` explicitly.
