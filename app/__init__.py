import os
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify

from app.routes import register_routes

BASE_DIR = Path(__file__).resolve().parent.parent
INSTANCE_DIR = BASE_DIR / "instance"


def create_app(test_config=None):
    load_dotenv(BASE_DIR / ".env")
    app = Flask(__name__, template_folder=str(BASE_DIR / "app" / "templates"), static_folder=str(BASE_DIR / "app" / "static"))

    app.config.from_mapping(
        SECRET_KEY=os.getenv("SECRET_KEY", "dev-secret-key"),
        EMPLOYEE_WORKBOOK_PATH=os.getenv(
            "EMPLOYEE_WORKBOOK_PATH",
            str(BASE_DIR / "data" / "colaboradores_ativos_2409.xlsx"),
        ),
        ADMIN_USERS_PATH=os.getenv("ADMIN_USERS_PATH", str(BASE_DIR / "config" / "users.json")),
        REQUEST_LOG_PATH=os.getenv("REQUEST_LOG_PATH", str(INSTANCE_DIR / "request_logs.jsonl")),
        GENERATED_FILES_PATH=os.getenv("GENERATED_FILES_PATH", str(INSTANCE_DIR / "generated")),
        ADMIN_USERNAME=os.getenv("ADMIN_USERNAME", "admin"),
        ADMIN_PASSWORD_HASH=os.getenv("ADMIN_PASSWORD_HASH", ""),
        MAX_REQUESTS_PER_MINUTE=int(os.getenv("MAX_REQUESTS_PER_MINUTE", "20")),
        RATE_LIMIT_WINDOW_SECONDS=int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60")),
        SMTP_HOST=os.getenv("SMTP_HOST") or os.getenv("SGQ_SMTP_HOST", ""),
        SMTP_PORT=int(os.getenv("SMTP_PORT") or os.getenv("SGQ_SMTP_PORT", "587")),
        SMTP_USERNAME=os.getenv("SMTP_USERNAME") or os.getenv("SGQ_SMTP_USER", ""),
        SMTP_PASSWORD=os.getenv("SMTP_PASSWORD") or os.getenv("SGQ_SMTP_PASSWORD", ""),
        SMTP_FROM=os.getenv("SMTP_FROM") or os.getenv("SGQ_FROM_EMAIL", "noreply@empresa.com"),
        SMTP_USE_TLS=(os.getenv("SMTP_USE_TLS") or os.getenv("SGQ_SMTP_TLS", "false")).lower() == "true",
        SMTP_USE_SSL=(os.getenv("SMTP_SSL") or os.getenv("SGQ_SMTP_SSL", "true")).lower() == "true",
        APP_BASE_URL=os.getenv("APP_BASE_URL", "http://localhost:5000"),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true",
        PERMANENT_SESSION_LIFETIME=1800,
    )

    if test_config:
        app.config.update(test_config)

    INSTANCE_DIR.mkdir(exist_ok=True)
    from app.services.workbook_store import ensure_employee_columns

    if Path(app.config["EMPLOYEE_WORKBOOK_PATH"]).exists():
        ensure_employee_columns(app.config["EMPLOYEE_WORKBOOK_PATH"])
    register_routes(app)

    @app.route("/health")
    def health():
        workbook = Path(app.config["EMPLOYEE_WORKBOOK_PATH"])
        available = workbook.exists()
        return jsonify({"status": "ok" if available else "error", "workbook": str(workbook), "workbook_available": available}), 200 if available else 503

    return app


##após fazer uma alteração, se quiser comitar e fazer deply use:
##.\scripts\commit-deploy.ps1 -Message "Msg commit"