# 異地部署用精簡映像：非 root 執行、資料掛載至 /data、內建健康檢查。
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN groupadd --system kkg \
    && useradd --system --gid kkg --home-dir /app --shell /usr/sbin/nologin kkg \
    && mkdir -p /data \
    && chown kkg:kkg /data /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=kkg:kkg . /app

USER kkg

ENV GRANTFLOW_HOST=0.0.0.0 \
    GRANTFLOW_PORT=8000 \
    GRANTFLOW_DB=/data/grantflow.sqlite3 \
    GRANTFLOW_ALLOWED_HOSTS=* \
    GRANTFLOW_RATE_LIMIT_PER_MIN=120 \
    GRANTFLOW_AUTH_FAIL_LIMIT=10

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; raise SystemExit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"

CMD ["python", "app.py"]
