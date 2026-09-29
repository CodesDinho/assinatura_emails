import secrets
import time
from functools import wraps
from pathlib import Path

from flask import current_app, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from app.services.admin_auth import authenticate_user
from app.services.email_sender import send_signature_email
from app.services.employee_importer import build_employee_record, normalize_email, read_employee_rows, validate_employee_row
from app.services.request_log import append_request_log, recent_request_logs, successful_request_emails
from app.services.signature_generator import generate_signature_image
from app.services.workbook_store import (
    find_employee_by_email,
    get_employee,
    list_employees,
    replace_employee_workbook,
    replace_employee_workbook_from_upload,
    save_employee_to_workbook,
)


_LOGIN_ATTEMPTS = {}
_MAX_LOGIN_ATTEMPTS = 5
_LOGIN_WINDOW_SECONDS = 15 * 60


def _find_employee_by_email(email):
    return find_employee_by_email(current_app.config["EMPLOYEE_WORKBOOK_PATH"], email)


def _log_request(email, status, details=""):
    append_request_log(current_app.config["REQUEST_LOG_PATH"], normalize_email(email), status, details)


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
        if not _valid_csrf():
            return render_template("home.html", error="A sessão expirou. Consulte seu e-mail novamente."), 400
        email = normalize_email(request.form.get("email"))
        if not email:
            return render_template("home.html", error="E-mail obrigatório.")
        employee = _find_employee_by_email(email)
        if not employee:
            _log_request(email, "not_found_sent", "Tentativa de solicitação sem cadastro")
            return render_template("home.html", error="Não foi possível localizar seu cadastro ativo.")

        phone = " ".join(request.form.get("phone", "").strip().split())
        if len(phone) > 30:
            employee["phone"] = phone
            return render_template("confirm.html", employee=employee, phone_error="Informe um celular com até 30 caracteres."), 400
        if phone != employee.get("phone", ""):
            updated_employee = dict(employee)
            updated_employee["phone"] = phone
            save_employee_to_workbook(current_app.config["EMPLOYEE_WORKBOOK_PATH"], updated_employee, original=employee)
            employee = updated_employee

        signature_path = None
        try:
            temp_dir = Path(current_app.config["GENERATED_FILES_PATH"])
            temp_dir.mkdir(parents=True, exist_ok=True)
            signature_path = temp_dir / f"{email.split('@')[0]}_assinatura.png"
            generate_signature_image(employee["full_name"], employee["job_title"], email, signature_path, phone=employee.get("phone", ""))
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
        rows = sorted(list_employees(current_app.config["EMPLOYEE_WORKBOOK_PATH"]), key=lambda item: item["full_name"].casefold())
        generated_emails = successful_request_emails(current_app.config["REQUEST_LOG_PATH"])
        for employee in rows:
            employee["signature_generated"] = employee["email"] in generated_emails
        total = len(rows)
        active = sum(1 for employee in rows if employee["active"])
        logs = recent_request_logs(current_app.config["REQUEST_LOG_PATH"])
        return render_template("admin_dashboard.html", employees=rows, total=total, active=active, inactive=total - active, request_rows=logs)

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
            duplicate = find_employee_by_email(current_app.config["EMPLOYEE_WORKBOOK_PATH"], employee["email"], active_only=False)
            if duplicate:
                return render_template("admin_employee_form.html", employee=employee, issues={"email": "E-mail já cadastrado."}, is_new=True), 409
            save_employee_to_workbook(current_app.config["EMPLOYEE_WORKBOOK_PATH"], employee)
            flash("Colaborador cadastrado e salvo na planilha.")
            return redirect(url_for("admin_dashboard"))
        return render_template("admin_employee_form.html", employee=employee, is_new=True)

    @app.route("/admin/colaboradores/<int:employee_id>/editar", methods=["GET", "POST"])
    @_admin_login_required
    def admin_employee_edit(employee_id):
        original = get_employee(current_app.config["EMPLOYEE_WORKBOOK_PATH"], employee_id)
        if not original:
            flash("Colaborador não encontrado.")
            return redirect(url_for("admin_dashboard"))
        employee = original
        if request.method == "POST":
            if not _valid_csrf():
                flash("A sessão expirou. Tente novamente.")
                return render_template("admin_employee_form.html", employee=employee, is_new=False), 400
            employee, issues = _employee_from_form()
            duplicate = find_employee_by_email(current_app.config["EMPLOYEE_WORKBOOK_PATH"], employee["email"], active_only=False)
            if duplicate and duplicate["id"] != employee_id:
                issues["email"] = "E-mail já cadastrado."
            if issues:
                return render_template("admin_employee_form.html", employee=employee, issues=issues, is_new=False), 400
            save_employee_to_workbook(current_app.config["EMPLOYEE_WORKBOOK_PATH"], employee, original=original)
            flash("Dados atualizados na planilha.")
            return redirect(url_for("admin_dashboard"))
        return render_template("admin_employee_form.html", employee=employee, is_new=False)

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
            preview, duplicates, errors, seen = [], [], [], set()
            without_email = 0
            for row in data:
                record = build_employee_record(row)
                # A base oficial do RH também pode conter colaboradores ainda sem
                # e-mail. Eles permanecem no cadastro, mas não conseguem consultar
                # uma assinatura até que o endereço seja preenchido.
                validation_issues = validate_employee_row(row, require_email=False)
                if validation_issues:
                    errors.append({"row": row, "issues": validation_issues})
                    continue
                email = record["email"]
                if email and email in seen:
                    duplicates.append(email)
                    continue
                if email:
                    seen.add(email)
                else:
                    without_email += 1
                preview.append(record)
            if errors or duplicates:
                return render_template("admin_import.html", preview=preview, errors=errors, duplicates=duplicates)
            if uploaded.filename.lower().endswith((".xlsx", ".xlsm")):
                # Preserve the complete RH workbook and replace the previous base;
                # rebuilding only selected fields would discard operational columns.
                replace_employee_workbook_from_upload(
                    current_app.config["EMPLOYEE_WORKBOOK_PATH"], uploaded.stream
                )
            else:
                replace_employee_workbook(current_app.config["EMPLOYEE_WORKBOOK_PATH"], preview)
            message = f"Importação concluída: {len(preview)} colaboradores salvos."
            if without_email:
                message += f" {without_email} registro(s) ainda estão sem e-mail."
            flash(message)
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
