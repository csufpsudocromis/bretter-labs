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
ARG COSIGN_VERSION=v3.0.5
ARG TRIVY_VERSION=v0.69.3
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl; \
    curl -fsSL -o /usr/local/bin/kubectl "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl"; \
    chmod +x /usr/local/bin/kubectl; \
    curl -fsSL -o /tmp/cosign-linux-amd64 "https://github.com/sigstore/cosign/releases/download/${COSIGN_VERSION}/cosign-linux-amd64"; \
    curl -fsSL -o /tmp/cosign_checksums.txt "https://github.com/sigstore/cosign/releases/download/${COSIGN_VERSION}/cosign_checksums.txt"; \
    (cd /tmp && grep " cosign-linux-amd64$" cosign_checksums.txt | sha256sum -c -); \
    install -m 0755 /tmp/cosign-linux-amd64 /usr/local/bin/cosign; \
    TRIVY_BASENAME="trivy_${TRIVY_VERSION#v}_Linux-64bit"; \
    curl -fsSL -o "/tmp/${TRIVY_BASENAME}.tar.gz" "https://github.com/aquasecurity/trivy/releases/download/${TRIVY_VERSION}/${TRIVY_BASENAME}.tar.gz"; \
    curl -fsSL -o /tmp/trivy_checksums.txt "https://github.com/aquasecurity/trivy/releases/download/${TRIVY_VERSION}/trivy_${TRIVY_VERSION#v}_checksums.txt"; \
    (cd /tmp && grep " ${TRIVY_BASENAME}.tar.gz$" trivy_checksums.txt | sha256sum -c -); \
    tar -xzf "/tmp/${TRIVY_BASENAME}.tar.gz" -C /tmp trivy; \
    install -m 0755 /tmp/trivy /usr/local/bin/trivy; \
    rm -f \
      /tmp/cosign-linux-amd64 \
      /tmp/cosign_checksums.txt \
      /tmp/trivy \
      /tmp/trivy_checksums.txt \
      "/tmp/${TRIVY_BASENAME}.tar.gz"; \
    apt-get purge -y --auto-remove curl; \
    rm -rf /var/lib/apt/lists/*

COPY backend /app/backend
RUN chmod +x /app/backend/entrypoint.sh

# Default envs can be overridden at runtime
ENV BLABS_KUBE_NAMESPACE=labs \
    BLABS_KUBE_IMAGE_PVC=golden-images \
    BLABS_STORAGE_ROOT=/mnt/lab-images \
    BLABS_DATABASE_PATH=/data/app.db

EXPOSE 8000

CMD ["/app/backend/entrypoint.sh"]
