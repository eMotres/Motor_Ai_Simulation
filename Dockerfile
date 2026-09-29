# FEM backend image for Google Cloud Run.
# Builds in Cloud Build (`gcloud run deploy --source .`) — no local Docker needed.
FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MPLBACKEND=Agg \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src

# System libraries OpenCASCADE (CadQuery) + gmsh need, headless (no display).
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglu1-mesa libxrender1 libxext6 libsm6 libice6 libx11-6 \
        libxcursor1 libxinerama1 libxft2 libxfixes3 libfontconfig1 \
        libgomp1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY src/    ./src/
COPY config/ ./config/
COPY VERSION ./VERSION

# Cloud Run injects $PORT (default 8080). One uvicorn worker — the FEM solver
# spawns its own process pool internally.
ENV PORT=8080
CMD ["sh", "-c", "uvicorn motor_ai_sim.api:app --host 0.0.0.0 --port ${PORT:-8080}"]
