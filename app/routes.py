import os
import secrets
import time
import uuid
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from flask import current_app, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from app.db import get_connection
from app.services.admin_auth import authenticate_user
from app.services.email_sender import send_signature_email
from app.services.employee_importer import (
    build_employee_record,
    normalize_email,
    read_employee_rows,
    validate_employee_row,
)
from app.services.signature_generator import generate_signature_image
from app.services.workbook_store import save_employee_to_workbook


_LOGIN_ATTEMPTS = {}
_MAX_LOGIN_ATTEMPTS = 5
_LOGIN_WINDOW_SECONDS = 15 * 60


def _find_employee_by_email(email):
    connection = get_connection(current_app.config["DATABASE_PATH"])
    try:
        row = connection.execute(
            "SELECT * FROM employees WHERE lower(email)=? AND active=1 LIMIT 1",
            (email.lower(),),
        ).fetchone()
        return dict(row) if row else None
    finally:
        connection.close()


def _log_request(email, status, details=""):
    connection = get_connection(current_app.config["DATABASE_PATH"])
    try:
        connection.execute(
            "INSERT INTO request_logs (normalized_email, request_id, status, details, created_at) VALUES (?, ?, ?, ?, ?)",
            (normalize_email(email), str(uuid.uuid4()), status, details, datetime.now(timezone.utc).isoformat()),
        )
        connection.commit()
    finally:
        connection.close()


def _admin_login_required(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not session.get("admin_user"):
            return redirect(url_for("admin_login"))
        return view_func(*args, **kwargs)
    return wrapper


def _csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def _valid_csrf():
    expected = session.get("csrf_token", "")
    supplied = request.form.get("csrf_token", "")
    return bool(expected and supplied and secrets.compare_digest(expected, supplied))


def _login_key(username):
    return (request.remote_addr or "unknown", (username or "").strip().lower())


def _login_is_blocked(key):
    now = time.monotonic()
    attempts = [stamp for stamp in _LOGIN_ATTEMPTS.get(key, []) if now - stamp < _LOGIN_WINDOW_SECONDS]
    _LOGIN_ATTEMPTS[key] = attempts
    return len(attempts) >= _MAX_LOGIN_ATTEMPTS


def _employee_from_form():
    row = {
        "nome": request.form.get("full_name", ""),
        "cargo": request.form.get("job_title", ""),
        "email": request.form.get("email", ""),
        "celular": request.form.get("phone", ""),
        "ativo": "SIM" if request.form.get("active") == "1" else "NÃO",
    }
    return build_employee_record(row), validate_employee_row(row)


def register_routes(app):
    app.jinja_env.globals["csrf_token"] = _csrf_token

    @app.route("/", methods=["GET"])
    def home():
        return render_template("home.html")

    @app.route("/consultar", methods=["POST"])
    def consultar_email():
        email = normalize_email(request.form.get("email"))
        if not email:
            return render_template("home.html", error="Informe seu e-mail corporativo.")

        employee = _find_employee_by_email(email)
        if not employee:
            _log_request(email, "not_found", "Consulta sem registro correspondente")
            return render_template("home.html", error="Nenhum cadastro ativo foi encontrado para esse e-mail.")

        return render_template("confirm.html", employee=employee)

    @app.route("/solicitar", methods=["POST"])
    def solicitar_assinatura():
        email = normalize_email(request.form.get("email"))
        if not email:
            return render_template("home.html", error="E-mail obrigatório.")

        employee = _find_employee_by_email(email)
        if not employee:
            _log_request(email, "not_found_sent", "Tentativa de solicitação sem cadastro")
            return render_template("home.html", error="Não foi possível localizar seu cadastro ativo.")

        signature_path = None
        try:
            temp_dir = Path(current_app.config["DATABASE_PATH"]).parent / "generated"
            temp_dir.mkdir(parents=True, exist_ok=True)
            signature_path = temp_dir / f"{email.split('@')[0]}_assinatura.png"
            generate_signature_image(
                employee["full_name"],
                employee["job_title"],
                email,
                signature_path,
                phone=employee.get("phone", ""),
            )

            response = send_signature_email(email, str(signature_path), employee["full_name"])
            if response.get("status") in {"sent", "simulated"}:
                _log_request(email, "sent", "assinatura enviada")
                return render_template("success.html", email=email)

            _log_request(email, "failed", response.get("message", "Falha ao enviar"))
            return render_template("home.html", error="Não foi possível enviar a assinatura nesse momento.")
        finally:
            if signature_path and signature_path.exists():
                try:
                    signature_path.unlink()
                except OSError:
                    pass

    @app.route("/admin/login", methods=["GET", "POST"])
    def admin_login():
        if session.get("admin_user"):
            return redirect(url_for("admin_dashboard"))
        if request.method == "POST":
            if not _valid_csrf():
                flash("A sessão expirou. Tente novamente.")
                return render_template("admin_login.html"), 400
            username = request.form.get("username", "")
            password = request.form.get("password", "")
            key = _login_key(username)
            if _login_is_blocked(key):
                flash("Muitas tentativas. Aguarde 15 minutos antes de tentar novamente.")
                return render_template("admin_login.html"), 429

            user = authenticate_user(current_app.config["ADMIN_USERS_PATH"], username, password)
            expected_hash = current_app.config.get("ADMIN_PASSWORD_HASH") or ""
            if not user and username == current_app.config.get("ADMIN_USERNAME") and expected_hash and check_password_hash(expected_hash, password):
                user = {"username": username, "name": username, "role": "Administrador"}
            if user:
                session.clear()
                session["admin_user"] = user
                session.permanent = True
                _csrf_token()
                _LOGIN_ATTEMPTS.pop(key, None)
                return redirect(url_for("admin_dashboard"))
            _LOGIN_ATTEMPTS.setdefault(key, []).append(time.monotonic())
            flash("Credenciais inválidas.")
        return render_template("admin_login.html")

    @app.route("/admin")
    @_admin_login_required
    def admin_dashboard():
        connection = get_connection(current_app.config["DATABASE_PATH"])
        try:
            rows = connection.execute("SELECT * FROM employees ORDER BY full_name").fetchall()
            total = len(rows)
            active = connection.execute("SELECT COUNT(*) as count FROM employees WHERE active=1").fetchone()["count"]
            inactive = total - active
            request_rows = connection.execute("SELECT * FROM request_logs ORDER BY created_at DESC LIMIT 15").fetchall()
            return render_template("admin_dashboard.html", employees=rows, total=total, active=active, inactive=inactive, request_rows=request_rows)
        finally:
            connection.close()

    @app.route("/admin/colaboradores/novo", methods=["GET", "POST"])
    @_admin_login_required
    def admin_employee_new():
        employee = {"full_name": "", "job_title": "", "email": "", "phone": "", "active": 1}
        if request.method == "POST":
            if not _valid_csrf():
                flash("A sessão expirou. Tente novamente.")
                return render_template("admin_employee_form.html", employee=employee, is_new=True), 400
            employee, issues = _employee_from_form()
            if issues:
                return render_template("admin_employee_form.html", employee=employee, issues=issues, is_new=True), 400

            connection = get_connection(current_app.config["DATABASE_PATH"])
            try:
                duplicate = connection.execute("SELECT id FROM employees WHERE lower(email)=?", (employee["email"],)).fetchone()
                if duplicate:
                    return render_template("admin_employee_form.html", employee=employee, issues={"email": "E-mail já cadastrado."}, is_new=True), 409
                now = datetime.now(timezone.utc).isoformat()
                connection.execute(
                    "INSERT INTO employees (full_name, job_title, email, active, phone, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (employee["full_name"], employee["job_title"], employee["email"], int(employee["active"]), employee["phone"], now, now),
                )
                save_employee_to_workbook(current_app.config["EMPLOYEE_WORKBOOK_PATH"], employee)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()
            flash("Colaborador cadastrado e salvo na planilha.")
            return redirect(url_for("admin_dashboard"))
        return render_template("admin_employee_form.html", employee=employee, is_new=True)

    @app.route("/admin/colaboradores/<int:employee_id>/editar", methods=["GET", "POST"])
    @_admin_login_required
    def admin_employee_edit(employee_id):
        connection = get_connection(current_app.config["DATABASE_PATH"])
        try:
            stored = connection.execute("SELECT * FROM employees WHERE id=?", (employee_id,)).fetchone()
            if not stored:
                flash("Colaborador não encontrado.")
                return redirect(url_for("admin_dashboard"))
            original = dict(stored)
            employee = original
            if request.method == "POST":
                if not _valid_csrf():
                    flash("A sessão expirou. Tente novamente.")
                    return render_template("admin_employee_form.html", employee=employee, is_new=False), 400
                employee, issues = _employee_from_form()
                duplicate = connection.execute(
                    "SELECT id FROM employees WHERE lower(email)=? AND id<>?", (employee["email"], employee_id)
                ).fetchone()
                if duplicate:
                    issues["email"] = "E-mail já cadastrado."
                if issues:
                    return render_template("admin_employee_form.html", employee=employee, issues=issues, is_new=False), 400
                connection.execute(
                    "UPDATE employees SET full_name=?, job_title=?, email=?, active=?, phone=?, updated_at=? WHERE id=?",
                    (employee["full_name"], employee["job_title"], employee["email"], int(employee["active"]), employee["phone"], datetime.now(timezone.utc).isoformat(), employee_id),
                )
                save_employee_to_workbook(current_app.config["EMPLOYEE_WORKBOOK_PATH"], employee, original=original)
                connection.commit()
                flash("Dados atualizados na aplicação e na planilha.")
                return redirect(url_for("admin_dashboard"))
            return render_template("admin_employee_form.html", employee=employee, is_new=False)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @app.route("/admin/import", methods=["GET", "POST"])
    @_admin_login_required
    def admin_import():
        if request.method == "POST":
            if not _valid_csrf():
                flash("A sessão expirou. Tente novamente.")
                return render_template("admin_import.html"), 400
            uploaded = request.files.get("file")
            if not uploaded or not uploaded.filename:
                flash("Selecione um arquivo CSV ou XLSX.")
                return render_template("admin_import.html")

            data = read_employee_rows(uploaded.stream, uploaded.filename)
            preview = []
            duplicates = []
            errors = []
            seen = set()

            for row in data:
                record = build_employee_record(row)
                validation_issues = validate_employee_row(row)
                if validation_issues:
                    errors.append({"row": row, "issues": validation_issues})
                    continue

                email = record["email"]
                if email in seen or email in duplicates:
                    duplicates.append(email)
                    continue
                seen.add(email)
                preview.append(record)

            if errors or duplicates:
                return render_template("admin_import.html", preview=preview, errors=errors, duplicates=duplicates)

            connection = get_connection(current_app.config["DATABASE_PATH"])
            try:
                connection.execute("DELETE FROM employees")
                connection.executemany(
                    "INSERT INTO employees (full_name, job_title, email, active, phone, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [
                        (
                            item["full_name"],
                            item["job_title"],
                            item["email"],
                            1 if item["active"] else 0,
                            item["phone"],
                            datetime.now(timezone.utc).isoformat(),
                            datetime.now(timezone.utc).isoformat(),
                        )
                        for item in preview
                    ],
                )
                connection.commit()
            finally:
                connection.close()

            flash("Importação concluída com sucesso.")
            return redirect(url_for("admin_dashboard"))

        return render_template("admin_import.html")

    @app.route("/admin/logout", methods=["POST"])
    @_admin_login_required
    def admin_logout():
        if not _valid_csrf():
            flash("A sessão expirou. Tente novamente.")
            return redirect(url_for("admin_dashboard"))
        session.clear()
        return redirect(url_for("home"))

    return app
