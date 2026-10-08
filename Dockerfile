FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    EMPLOYEE_WORKBOOK_PATH=/app/data/colaboradores_ativos_2409.xlsx \
    SHARED_SQLITE_PATH=/shared/slack_apps.db \
    PENDING_IMPORTS_PATH=/app/data/pending_imports \
    REQUEST_LOG_PATH=/app/instance/request_logs.jsonl \
    GENERATED_FILES_PATH=/app/instance/generated \
    EMPLOYEE_OVERRIDES_PATH=/app/instance/employee_overrides.db \
    ADMIN_USERS_PATH=/app/config/users.json

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /app/data /app/instance \
    && chown -R app:app /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=app:app . .

USER app
EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/health', timeout=3)" || exit 1

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "4", "--timeout", "60", "--access-logfile", "-", "wsgi:app"]
