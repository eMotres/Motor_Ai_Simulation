# eMotres compute worker image (docs/BYO_COMPUTE.md).  Built by CI per release
# and published as ghcr.io/emotres/motres-worker:<platform version>; the tag IS
# the solver version, so a worker can never run code the platform does not.
#   docker build -f deploy/compute-worker/Dockerfile --build-arg VERSION=$(cat VERSION) .
FROM python:3.11-slim
ARG VERSION=0
ENV MOTRES_PLATFORM_VERSION=${VERSION} PYTHONUNBUFFERED=1
WORKDIR /opt/motres-worker/src
COPY pyproject.toml requirements.txt VERSION ./
COPY src ./src
COPY deploy/compute-worker ./deploy/compute-worker
COPY deploy/node-agent ./deploy/node-agent
RUN pip install --no-cache-dir -e . && useradd --system motres-worker
USER motres-worker
ENTRYPOINT ["python", "deploy/compute-worker/motres_compute_worker.py"]
