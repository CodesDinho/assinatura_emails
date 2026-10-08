import secrets
import time
from functools import wraps
from pathlib import Path

from flask import current_app, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from app.services.admin_auth import authenticate_user
from app.services.email_sender import send_signature_email
from app.services.employee_importer import (
    build_employee_record,
    normalize_email,
    normalize_text,
    read_employee_rows,
    validate_employee_row,
)
from app.services.corporate_employee_store import (
    equalization_status,
    find_employee_by_email,
    find_employee_by_id,
    list_employees,
    lorac_coverage,
    readiness_summary,
)
from app.services.employee_override_store import delete_employee_override, save_employee_override
from app.services.pending_import_store import list_pending_imports, save_pending_import
from app.services.request_log import append_request_log, recent_request_logs, successful_request_emails
from app.services.signature_generator import generate_signature_image
from app.services.whatsapp_card_generator import InvalidProfilePhoto, generate_whatsapp_card


_LOGIN_ATTEMPTS = {}
_MAX_LOGIN_ATTEMPTS = 5
_LOGIN_WINDOW_SECONDS = 15 * 60


def _find_employee_by_email(email):
    return find_employee_by_email(email)


def _base_status():
    """Centraliza o bloqueio obrigatório antes de qualquer consumo da base."""
    status = equalization_status()
    return status if not status["equalized"] else None


def _unavailable_response(status, *, code=503):
    return render_template("home.html", error=status["message"]), code


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


def register_routes(app):
    app.jinja_env.globals["csrf_token"] = _csrf_token

    @app.after_request
    def disable_admin_cache(response):
        if request.path.startswith("/admin"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    @app.route("/", methods=["GET"])
    def home():
        return render_template("home.html")

    @app.route("/consultar", methods=["POST"])
    def consultar_email():
        email = normalize_email(request.form.get("email"))
        if not email:
            return render_template("home.html", error="Informe seu e-mail corporativo.")
        status = _base_status()
        if status:
            return _unavailable_response(status)
        employee = _find_employee_by_email(email)
        if not employee:
            known_employee = find_employee_by_email(
                email, active_only=True, require_valid_email=False
            )
            if known_employee and known_employee.get("duplicate_email"):
                _log_request(email, "duplicate_email", "E-mail vinculado a mais de um colaborador ativo")
                return render_template(
                    "home.html",
                    error=(
                        "Este e-mail está vinculado a mais de um colaborador ativo. "
                        "Por segurança, a geração foi bloqueada. Solicite ao RH a correção dos cadastros."
                    ),
                )
            if known_employee and not known_employee["email_ready"]:
                _log_request(email, "email_not_ready", "Colaborador ativo sem e-mail válido")
                return render_template(
                    "home.html",
                    error="Seu cadastro está ativo, mas ainda não possui um e-mail válido para envio. Solicite a regularização à TI.",
                )
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
        status = _base_status()
        if status:
            return _unavailable_response(status)
        employee = _find_employee_by_email(email)
        if not employee:
            _log_request(email, "not_found_sent", "Tentativa de solicitação sem cadastro")
            return render_template("home.html", error="Não foi possível localizar seu cadastro ativo.")
        email = employee["email"]

        phone = " ".join(request.form.get("phone", "").strip().split())
        if len(phone) > 30:
            employee["phone"] = phone
            return render_template("confirm.html", employee=employee, phone_error="Informe um celular com até 30 caracteres."), 400
        if phone != employee.get("phone", ""):
            updated_employee = dict(employee)
            updated_employee["phone"] = phone
            employee = updated_employee

        uploaded_photo = request.files.get("profile_photo")
        if not uploaded_photo or not uploaded_photo.filename:
            return render_template(
                "confirm.html",
                employee=employee,
                photo_error="Selecione uma foto do seu rosto para gerar a imagem do WhatsApp.",
            ), 400

        signature_path = None
        whatsapp_card_path = None
        try:
            temp_dir = Path(current_app.config["GENERATED_FILES_PATH"])
            temp_dir.mkdir(parents=True, exist_ok=True)
            request_id = secrets.token_hex(8)
            file_prefix = email.split("@")[0]
            signature_path = temp_dir / f"{file_prefix}_{request_id}_assinatura.png"
            whatsapp_card_path = temp_dir / f"{file_prefix}_{request_id}_whatsapp.png"
            generate_signature_image(employee["full_name"], employee["job_title"], email, signature_path, phone=employee.get("phone", ""))
            try:
                generate_whatsapp_card(
                    employee["full_name"], employee["job_title"], uploaded_photo, whatsapp_card_path
                )
            except InvalidProfilePhoto as exc:
                return render_template("confirm.html", employee=employee, photo_error=str(exc)), 400
            response = send_signature_email(
                email,
                str(signature_path),
                employee["full_name"],
                whatsapp_card_path=str(whatsapp_card_path),
            )
            if response.get("status") in {"sent", "simulated"}:
                _log_request(email, "sent", "assinatura enviada")
                return render_template("success.html", email=email)
            _log_request(email, "failed", response.get("message", "Falha ao enviar"))
            if response.get("code") in {"smtp_configuration", "smtp_authentication"}:
                error = (
                    "O serviço de e-mail recusou a autenticação. A TI já pode identificar a falha "
                    "na configuração SMTP; tente novamente após a correção."
                )
            else:
                error = "Não foi possível enviar a assinatura nesse momento. Tente novamente mais tarde."
            return render_template("home.html", error=error)
        finally:
            if signature_path and signature_path.exists():
                try:
                    signature_path.unlink()
                except OSError:
                    pass
            if whatsapp_card_path and whatsapp_card_path.exists():
                try:
                    whatsapp_card_path.unlink()
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
        status = equalization_status()
        rows = []
        summary = {"active": 0, "ready": 0, "missing_email": 0}
        lorac = {"total": 0, "ready": 0, "pending": [], "pending_count": 0, "coverage_percent": 100.0}
        if status["equalized"]:
            rows = list_employees(active_only=False)
            summary = readiness_summary()
            lorac = lorac_coverage()
        generated_emails = successful_request_emails(current_app.config["REQUEST_LOG_PATH"])
        for employee in rows:
            employee["signature_generated"] = employee["email"] in generated_emails
        logs = recent_request_logs(current_app.config["REQUEST_LOG_PATH"])
        pending = list_pending_imports(current_app.config["PENDING_IMPORTS_PATH"])
        return render_template(
            "admin_dashboard.html",
            employees=rows,
            total=len(rows),
            active=summary["active"],
            inactive=len(rows) - summary["active"],
            ready=summary["ready"],
            missing_email=summary["missing_email"],
            corporate_status=status,
            lorac_coverage=lorac,
            pending_imports=pending,
            request_rows=logs,
        )

    @app.route("/admin/colaboradores/novo", methods=["GET", "POST"])
    @_admin_login_required
    def admin_employee_new():
        flash("A base corporativa é somente leitura. Cadastros devem ser tratados no projeto de equalização.")
        return redirect(url_for("admin_dashboard"))

    @app.route("/admin/colaboradores/<int:employee_id>/editar", methods=["GET", "POST"])
    @_admin_login_required
    def admin_employee_edit(employee_id):
        status = equalization_status()
        if not status["equalized"]:
            flash(status["message"])
            return redirect(url_for("admin_dashboard"))
        employee = find_employee_by_id(employee_id)
        if not employee:
            flash("Colaborador não encontrado na publicação corporativa atual.")
            return redirect(url_for("admin_dashboard"))
        if request.method == "GET":
            return render_template("admin_employee_form.html", employee=employee, is_new=False)
        if not _valid_csrf():
            flash("A sessão expirou. Tente novamente.")
            return render_template("admin_employee_form.html", employee=employee, is_new=False), 400

        edited = {
            **employee,
            "full_name": normalize_text(request.form.get("full_name")),
            "job_title": normalize_text(request.form.get("job_title")),
            "email": normalize_email(request.form.get("email")),
            "phone": normalize_text(request.form.get("phone")),
            "registration": normalize_text(request.form.get("registration")),
            "company": normalize_text(request.form.get("company")),
            "active": request.form.get("active") == "1",
        }
        issues = validate_employee_row(edited, require_email=False)
        if len(edited["phone"]) > 30:
            issues["phone"] = "Informe um celular com até 30 caracteres."
        if edited["email"] and edited["active"]:
            conflict = next(
                (
                    item for item in list_employees(active_only=True)
                    if item["id"] != employee_id and item["email"] == edited["email"]
                ),
                None,
            )
            if conflict:
                issues["email"] = (
                    f"Este e-mail já está vinculado ao colaborador {conflict['full_name']}. "
                    "Remova-o desse cadastro antes de atribuí-lo aqui."
                )
        if issues:
            return render_template(
                "admin_employee_form.html", employee=edited, issues=issues, is_new=False
            ), 400

        save_employee_override(
            employee_id,
            employee,
            edited,
            session["admin_user"]["username"],
        )
        flash(f"Dados de {edited['full_name']} atualizados com segurança.")
        return redirect(url_for("admin_dashboard"))

    @app.route("/admin/colaboradores/<int:employee_id>/excluir", methods=["POST"])
    @_admin_login_required
    def admin_employee_delete(employee_id):
        if not _valid_csrf():
            flash("A sessão expirou. Nenhum registro foi excluído.")
            return redirect(url_for("admin_dashboard"))
        status = equalization_status()
        if not status["equalized"]:
            flash(status["message"])
            return redirect(url_for("admin_dashboard"))
        employee = find_employee_by_id(employee_id)
        if not employee:
            flash("Colaborador não encontrado ou já excluído.")
            return redirect(url_for("admin_dashboard"))
        delete_employee_override(
            employee_id,
            employee,
            session["admin_user"]["username"],
        )
        flash(
            f"Registro de {employee['full_name']} excluído deste sistema. "
            "O banco corporativo do RH não foi alterado."
        )
        return redirect(url_for("admin_dashboard"))

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
            filename = Path(uploaded.filename).name
            result = save_pending_import(
                current_app.config["PENDING_IMPORTS_PATH"],
                uploaded.stream,
                filename,
                uploaded_by=session["admin_user"]["username"],
                row_count=len(preview),
            )
            message = (
                f"Carga pendente recebida: {filename} — {len(preview)} colaboradores analisados. "
                f"Arquivo confirmado: {result['sha256'][:12]}. Ela não alterou a base corporativa. "
                "Solicite à TI que execute a etapa 4 — Analisar Email/SharePoint x RH no projeto de "
                "equalização e publique a prévia homologada."
            )
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
