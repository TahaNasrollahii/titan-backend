# syntax=docker/dockerfile:1

# ---------- build: resolve dependencies into a virtualenv ----------
FROM python:3.13-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /bin/uv
# The venv lives outside /app so a dev bind-mount of the source tree cannot hide it.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# ---------- runtime ----------
FROM python:3.13-slim
RUN groupadd --system titan && useradd --system --gid titan --create-home titan
WORKDIR /app
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DJANGO_SETTINGS_MODULE=config.settings.prod

COPY --from=builder /opt/venv /opt/venv
COPY --chown=titan:titan . .

# Placeholder secrets only exist for this build step; real ones come from the runtime environment.
RUN SECRET_KEY=collectstatic FIELD_ENCRYPTION_KEY=collectstatic ALLOWED_HOSTS=localhost \
    python manage.py collectstatic --noinput \
    && mkdir -p /app/media && chown -R titan:titan /app/media /app/staticfiles

USER titan
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health/')" || exit 1
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", \
     "--access-logfile", "-", "--forwarded-allow-ips", "*"]
