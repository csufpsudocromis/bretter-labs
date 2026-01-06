# Backend image for Bretter Labs (FastAPI + Uvicorn)
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY backend /app/backend

# Default envs can be overridden at runtime
ENV BLABS_KUBE_NAMESPACE=labs \
    BLABS_KUBE_IMAGE_PVC=golden-images \
    BLABS_STORAGE_ROOT=/mnt/lab-images \
    BLABS_DATABASE_PATH=/data/app.db

EXPOSE 8000

CMD ["uvicorn", "backend.src.main:app", "--host", "0.0.0.0", "--port", "8000"]
