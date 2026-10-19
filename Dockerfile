# Automotive Signals - one image, three services (api / worker / web).
# Dependencies are installed in their own layer so code edits do not re-resolve them.
FROM python:3.12-slim AS deps
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim
LABEL org.opencontainers.image.title="automotive-signals" \
      org.opencontainers.image.description="Commercial-vehicle industry signal monitor (FastAPI + Streamlit + worker)"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src

# Non-root runtime user; var/ holds the SQLite fallback and logs.
RUN useradd --create-home --home-dir /home/signals --shell /bin/bash signals

WORKDIR /app
COPY --from=deps /usr/local /usr/local
COPY . .
RUN mkdir -p /app/var && chown -R signals:signals /app

USER signals
EXPOSE 8000 8501

# DATABASE_URL, LLM_PROVIDER, API_TOKEN ... come from the environment (.env).
# `sh` instead of the file's own +x bit: git checkouts on Windows do not keep it.
# Per-service health checks live in docker-compose.yml (only the API serves HTTP).
ENTRYPOINT ["sh", "/app/docker/entrypoint.sh"]
CMD ["uvicorn", "signals.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
