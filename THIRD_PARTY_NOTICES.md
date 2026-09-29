# Third-party notices

This project (GNU AGPL-3.0-or-later, copyright MOTRES d.o.o. and contributors)
uses the runtime dependencies below. Versions and licences were read from the
installed package metadata (`importlib.metadata`, `node_modules/*/package.json`)
on 2026-09-29; where the metadata was empty the licence was taken from the
project's own licence file. Each dependency remains under its own licence.

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
| pypardiso | 0.4.7 | BSD-3-Clause; pulls **Intel MKL** (Intel Simplified Software License, proprietary, redistributable; see note 2) |
| psutil | 7.2.2 | BSD-3-Clause |
| reportlab | 5.0.0 | BSD-3-Clause |
| python-docx | 1.2.0 | MIT |
| openpyxl | 3.1.5 | MIT |
| mcp | 2.2.0 | MIT |

### Optional (not installed by default)

| Package | Version | Licence |
|---|---|---|
| triangle (extra `[triangle]`, `requirements-triangle.txt`) | 20250106 | Python wrapper LGPL-3.0; bundled Triangle C code by J. R. Shewchuk: **free for non-commercial use only** (see note 3) |

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

Development-only tools (pytest, black, ruff, mypy, Vite, TypeScript, ESLint)
are not distributed with the software and are not listed.

## Notes

1. **gmsh (GPL-2.0-or-later).** "Or later" allows use under GPL-3.0, and
   GPL-3.0 section 13 explicitly permits combination with AGPL-3.0 code, so
   the AGPL distribution of this project is compatible. gmsh is used as an
   unmodified Python package (`import gmsh`). A **commercial (non-AGPL)
   distribution** by MOTRES that includes or links gmsh would still be bound by
   the GPL for gmsh; such an offering needs either a gmsh commercial licence
   (available from the gmsh authors) or a build without gmsh.
2. **Intel MKL (via pypardiso).** Proprietary but freely redistributable
   library installed from PyPI by the user/deployment, not shipped in this
   source repository. The AGPL/GPL "system library" reasoning does not clearly
   cover it; pypardiso is optional in the code (scipy's solver is used when it
   is absent), so binary/container distributions can omit it.
3. **Triangle.** Its licence forbids commercial use without the author's
   permission, which is incompatible with both the AGPL (no field-of-use
   restrictions) and MOTRES's commercial licences. It is therefore an
   optional extra: without it the FEM mesher uses gmsh and the 2-D view uses
   mapbox-earcut.
