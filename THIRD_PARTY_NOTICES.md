# Third-party notices

This project (GNU AGPL-3.0-or-later, copyright MOTRES d.o.o. and contributors)
uses the runtime dependencies below. Versions and licences were read from the
installed package metadata (`importlib.metadata`, `node_modules/*/package.json`)
on 2026-09-29; where the metadata was empty the licence was taken from the
project's own licence file. Each dependency remains under its own licence.

**Owner decision, 2026-09-30 (implementation pending; this file describes
both the current `requirements.txt` state and the planned default
below, clearly labelled):** the default server image and default
distribution drop Intel MKL/`pypardiso` entirely; CHOLMOD (via
`scikit-sparse`) and MUMPS become the default direct-solver backends,
with the OpenBLAS already bundled in the numpy/scipy PyPI wheels
underneath. Gmsh becomes the default mesher once per-configuration
verification passes (`docs/MESHER_TRANSITION.md`); Triangle is removed
outright. `pypardiso`/Intel MKL remains available only as an optional,
user-installed, user-enabled backend -- see "Optional (not installed by
default)" below and
[LICENSE-EXCEPTION](LICENSE-EXCEPTION) (**DRAFT**). See
[docs/LICENSE-EXCEPTION-NOTES.md](docs/LICENSE-EXCEPTION-NOTES.md)
Section 8 for the full record, including the production snapshot from
before this decision.

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
| OpenBLAS | numpy, scipy (PyPI wheels bundle it by default) | BSD-3-Clause | compatible |

### Planned default direct-solver stack (2026-09-30 decision; not yet in requirements.txt, implementation pending)

| Component | Role | Licence | AGPL-3.0 verdict |
|---|---|---|---|
| CHOLMOD (SuiteSparse), via a Python binding such as `scikit-sparse` | Sparse Cholesky factorisation, SPD systems | CHOLMOD itself: LGPL-2.1-or-later (Core/Cholesky modules) with some SuiteSparse modules under GPL-2.0-or-later; `scikit-sparse` (the Python wrapper most likely to be used): dual GPL-2.0-or-later AND LGPL-2.1-or-later | Compatible via the "or later" route (GPL-3.0 section 13 permits AGPL-3.0 combination), same reasoning already used for gmsh (note 1) -- confirm the exact modules pulled in (Supernodal/MatrixOps solving may pull in GPL-only helpers) once the binding is chosen and pinned |
| MUMPS | Sparse direct multifrontal solver, general (non-SPD) systems | CeCILL-C (weak copyleft, LGPL-like; France: CEA/CNRS/Inria), except AMD-ordering and some LAPACK-derived components under BSD-3-Clause, and the optional PORD ordering under its own separate licence | Believed compatible (CeCILL-C is designed to be LGPL-interoperable) but not independently confirmed against the FSF's compatibility list this pass -- flag for counsel; also confirm the licence of whichever Python binding is chosen (e.g. `python-mumps`/`PyMUMPS`) and of PORD if it is enabled |

Package names, exact versions and pins above are **not yet decided or
implemented** as of this revision; this table records the licence
character of the underlying libraries the owner has chosen so the
question can be settled once implementation lands, not a completed
audit of installed artefacts (contrast with the per-artifact table in
docs/LICENSE-EXCEPTION-NOTES.md Section 6, which *is* read from an
actual running image).

### Optional (not installed by default)

| Package | Install | Licence |
|---|---|---|
| triangle | previously `requirements-triangle.txt` / extra `[triangle]`; **removed outright by the 2026-09-30 decision**, not merely staged out (see note 3) | Python wrapper LGPL-3.0; bundled Triangle C code by J. R. Shewchuk: see note 3 for the author's exact terms |
| pypardiso | `requirements-pardiso.txt` or extra `[pardiso]` -- an operator's own opt-in install, never installed or enabled by MOTRES's own build or deploy process as of the 2026-09-30 decision | BSD-3-Clause wrapper; pulls in several separately-licensed Intel artefacts, not one uniform licence -- see note 2 for the per-artifact breakdown |

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
2. **Intel MKL and friends (via the now-optional pypardiso).** Not one
   uniform proprietary licence -- read artefact-by-artefact from the
   package actually installed (`dist-info/LICENSE.txt` inside the
   production container, before the 2026-09-30 decision below removed
   this chain from the default image; full table in
   [docs/LICENSE-EXCEPTION-NOTES.md](docs/LICENSE-EXCEPTION-NOTES.md)
   Section 6):
   - `mkl`, `tbb`, `tcmlib`, `onemkl-license` -- **Intel Simplified
     Software License** (proprietary, binary redistribution permitted
     under stated conditions). Note: the `tbb` *PyPI package* (the
     Intel-built binary) is under this licence, **not** the Apache-2.0
     that covers the separate github.com/uxlfoundation/oneTBB *source*
     project -- different offered licences for different artefacts.
   - `intel-openmp`, `intel-cmplr-lib-ur` -- **Intel End User License
     Agreement for Developer Tools**, materially more restrictive:
     its section 3.1 bars linking/distributing so any part "becomes
     Reciprocal Open Source Software" and bars SaaS/service-bureau use.
     See [LICENSE-EXCEPTION](LICENSE-EXCEPTION) Section D -- this is
     flagged for counsel, not resolved by this pack.
   - `umf` (oneAPI Unified Memory Framework) -- **Apache-2.0 with LLVM
     exceptions**. Not proprietary; was previously (incorrectly) lumped
     into "the MKL chain" as proprietary in an earlier pass of this
     file.
   **2026-09-30 owner decision: this entire chain leaves the default
   server image and default distribution.** `pypardiso` becomes an
   operator's own opt-in install (`requirements-pardiso.txt`), never
   installed or enabled by MOTRES's own build or deploy process. The
   default solvers are CHOLMOD/MUMPS (see the "Planned default
   direct-solver stack" table above). The additional permission in
   [LICENSE-EXCEPTION](LICENSE-EXCEPTION) (**DRAFT, pending legal
   review**) is kept, narrowed to `mkl` and `tbb` only, for operators who
   choose to self-install this optional backend.
3. **Triangle -- removed.** J. R. Shewchuk's own page
   (https://www.cs.cmu.edu/~quake/triangle.html) states: "although
   Triangle is freely available, it is copyrighted by the author and may
   not be sold or included in commercial products without a license" --
   quoted verbatim rather than paraphrased as a blanket "non-commercial
   use only" (that paraphrase overstated the restriction: the author's
   own wording is about selling or including Triangle in a commercial
   product, not about all commercial *use*). The installed wrapper is
   `triangle==20250106`, wrapping Triangle 1.6; its own
   `dist-info/METADATA` declares the wrapper LGPL-3.0, separately from
   the bundled C code's terms above. Given that restriction is still not
   compatible with the AGPL's own redistribution terms, and given other
   MOTRES material describes an aerostator.com "shop" (raising the same
   commercial-use question the author's wording turns on), the owner
   decided 2026-09-30 to **remove Triangle outright** rather than keep
   it as a staged or optional component -- see
   docs/MESHER_TRANSITION.md for the updated plan, including a
   commercial-licence option that was not previously considered. Without
   Triangle the geometry mesher uses gmsh (pending the
   per-configuration verification in docs/MESHER_TRANSITION.md) and the
   2-D view uses mapbox-earcut with a shapely (GEOS) constrained-Delaunay
   fallback.
