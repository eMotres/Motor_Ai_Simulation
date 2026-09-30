# eMotres compute worker image (docs/BYO_COMPUTE.md).  Built by CI per release
# and published as ghcr.io/emotres/motres-worker:<platform version>; the tag IS
# the solver version, so a worker can never run code the platform does not.
#   docker build -f deploy/compute-worker/Dockerfile --build-arg VERSION=$(cat VERSION) .
#
# A distributed image: open-source sparse solvers only (SuiteSparse CHOLMOD +
# MUMPS, simulation/linear_backend.py), no Intel MKL.  The bindings are
# compiled in a throwaway stage, exactly as in deploy/Dockerfile.api.
FROM python:3.11-slim AS opensolvers-build
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libsuitesparse-dev libmumps-seq-dev \
    && rm -rf /var/lib/apt/lists/*
ENV CFLAGS="-I/usr/include/suitesparse -I/usr/include/mumps_seq"
COPY requirements-opensolvers.txt ./
RUN pip wheel --no-cache-dir --no-deps -w /wheels -r requirements-opensolvers.txt

FROM python:3.11-slim
ARG VERSION=0
ENV MOTRES_PLATFORM_VERSION=${VERSION} PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 libopenblas0-pthread \
        libcholmod5 libamd3 libcamd3 libcolamd3 libccolamd3 libbtf2 \
        libklu2 libspqr4 libumfpack6 libsuitesparseconfig7 \
        libmumps-seq-5.7 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/motres-worker/src
COPY pyproject.toml requirements.txt VERSION ./
COPY src ./src
COPY deploy/compute-worker ./deploy/compute-worker
COPY deploy/node-agent ./deploy/node-agent
COPY --from=opensolvers-build /wheels /tmp/opensolver-wheels
RUN pip install --no-cache-dir -e . \
 && pip install --no-cache-dir --no-index /tmp/opensolver-wheels/*.whl \
 && rm -rf /tmp/opensolver-wheels \
 && useradd --system motres-worker
USER motres-worker
ENTRYPOINT ["python", "deploy/compute-worker/motres_compute_worker.py"]
