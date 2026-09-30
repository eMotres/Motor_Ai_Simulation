# eMotres compute worker image (docs/BYO_COMPUTE.md).  Built by CI per release
# and published as ghcr.io/emotres/motres-worker:<platform version>; the tag IS
# the solver version, so a worker can never run code the platform does not.
#   docker build -f deploy/compute-worker/Dockerfile --build-arg VERSION=$(cat VERSION) .
#
# WHAT CHANGED 2026-09-30 (runtime deps)
#   * `pip install -e .` alone only resolves pyproject.toml's
#     [project].dependencies, which is a loose mirror, not the authoritative
#     pinned set — requirements.txt is (deploy/Dockerfile.api installs it the
#     same way).  Found building the open-solver worker image: the built
#     image could not `import motor_ai_sim.simulation` at all
#     (ModuleNotFoundError: sympy, raised from simulation/pdes.py), and the
#     same gap applies to meshio, scikit-learn, pillow, psutil, reportlab,
#     python-docx, openpyxl, pyjwt, pydantic, cma, anthropic, mcp,
#     cadquery-ocp, python-multipart — every one is pinned in requirements.txt
#     but absent from pyproject's dependency list.
#   * native libs for gmsh + OCP (cadquery): this worker runs the SAME solver
#     code as the API, including geometry jobs (docs/BYO_COMPUTE.md §6), so it
#     needs the same shared libraries the API's `base` stage installs
#     (deploy/Dockerfile.api) or `import cadquery` / gmsh's native module can
#     fail to load at runtime even though pip installed the wheel cleanly.
#   * no Intel OpenMP / MKL guard, matching deploy/Dockerfile.api
#     (chore/image-without-intel-openmp): this image is published to
#     third-party BYO nodes on ghcr.io, so it must never carry the Intel
#     EULA'd intel-openmp / intel-cmplr-lib-ur packages even by accident.
FROM python:3.11-slim
ARG VERSION=0
ENV MOTRES_PLATFORM_VERSION=${VERSION} PYTHONUNBUFFERED=1

# gmsh + OCP (cadquery) native runtime libs; libgomp1 for OpenMP (OpenBLAS /
# scipy / scikit-learn).  Mirrors deploy/Dockerfile.api's `base` stage, minus
# fonts-dejavu-core (no report rendering on a worker) and curl (the worker's
# liveness is its heartbeat to the platform, not a local HTTP healthcheck).
RUN apt-get update && apt-get install -y --no-install-recommends \
        libglu1-mesa libgl1 libxrender1 libxcursor1 libxft2 libxinerama1 \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/motres-worker/src
COPY pyproject.toml requirements.txt VERSION ./
COPY src ./src
COPY deploy/compute-worker ./deploy/compute-worker
COPY deploy/node-agent ./deploy/node-agent

# Install the SAME pinned runtime set the API image installs (requirements.txt
# is authoritative), THEN register the local package itself.  `--no-deps` on
# the second install is load-bearing: everything the package needs is already
# installed at the pinned versions above, so `-e .` only needs to create the
# editable link and the `motor-sim` console script (pyproject
# [project.scripts]) — not re-resolve dependencies and risk drifting off the
# pinned set installed a moment ago.
RUN pip install --no-cache-dir -r requirements.txt \
 && pip install --no-cache-dir --no-deps -e .

# ── build guard: intel-openmp / intel-cmplr-lib-ur / libiomp5.so must never
# be in this image (2026-09-30, matches deploy/Dockerfile.api) ─────────────
# requirements.txt never installs pypardiso (that stays in the OPTIONAL
# requirements-pardiso.txt, not installed here), so nothing above should pull
# Intel MKL in at all — this guard fails the build loudly instead of silently
# publishing a restricted-licence binary to every third-party BYO compute node
# that pulls ghcr.io/emotres/motres-worker.
RUN set -eu; \
    fail=0; \
    if pip show intel-openmp >/dev/null 2>&1; then \
        echo "BUILD GUARD FAILED: intel-openmp is installed -- Intel EULA for" \
             "Developer Tools, not redistributable in this image" >&2; \
        fail=1; \
    fi; \
    if pip show intel-cmplr-lib-ur >/dev/null 2>&1; then \
        echo "BUILD GUARD FAILED: intel-cmplr-lib-ur is installed -- Intel EULA" \
             "for Developer Tools, not redistributable in this image" >&2; \
        fail=1; \
    fi; \
    site_packages="$(python3 -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"; \
    hit="$(find "$site_packages" -iname 'libiomp5*.so*' 2>/dev/null || true)"; \
    if [ -n "$hit" ]; then \
        echo "BUILD GUARD FAILED: libiomp5*.so found under $site_packages:" >&2; \
        echo "$hit" >&2; \
        fail=1; \
    fi; \
    if [ "$fail" = "1" ]; then exit 1; fi; \
    echo "build guard ok: no intel-openmp, no intel-cmplr-lib-ur, no libiomp5.so"

RUN useradd --system motres-worker
USER motres-worker
ENTRYPOINT ["python", "deploy/compute-worker/motres_compute_worker.py"]
