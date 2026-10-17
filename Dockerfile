FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock \
    && useradd --create-home --uid 10001 appuser \
    && mkdir /app/runtime && chown appuser:appuser /app/runtime
COPY --chown=appuser:appuser *.py schema.sql ./
COPY --chown=appuser:appuser assets ./assets
USER appuser
EXPOSE 8050
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8050/healthz', timeout=3)"
CMD ["gunicorn", "app:server", "--bind", "0.0.0.0:8050", "--workers", "2", "--threads", "2", "--timeout", "120", "--access-logfile", "-", "--error-logfile", "-"]
