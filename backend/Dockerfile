# Backend image for Bretter Labs (FastAPI + Uvicorn)
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY VERSION /app/VERSION
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Admin image validation/conversion uses kubectl helper pods.
ARG KUBECTL_VERSION=v1.30.14
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && curl -fsSL -o /usr/local/bin/kubectl "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" \
    && chmod +x /usr/local/bin/kubectl \
    && curl -fsSL -o /usr/local/bin/cosign "https://github.com/sigstore/cosign/releases/latest/download/cosign-linux-amd64" \
    && chmod +x /usr/local/bin/cosign \
    && curl -fsSL "https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh" | sh -s -- -b /usr/local/bin \
    && apt-get purge -y --auto-remove curl \
    && rm -rf /var/lib/apt/lists/*

COPY backend /app/backend
RUN chmod +x /app/backend/entrypoint.sh

# Default envs can be overridden at runtime
ENV BLABS_KUBE_NAMESPACE=labs \
    BLABS_KUBE_IMAGE_PVC=golden-images \
    BLABS_STORAGE_ROOT=/mnt/lab-images \
    BLABS_DATABASE_PATH=/data/app.db

EXPOSE 8000

CMD ["/app/backend/entrypoint.sh"]
