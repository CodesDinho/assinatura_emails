import secrets
import time
import re
from functools import wraps
from pathlib import Path

from flask import abort, current_app, flash, redirect, render_template, request, send_file, session, url_for
from werkzeug.security import check_password_hash

from app.services.admin_auth import authenticate_user, create_admin_user, list_active_users
from app.services.email_sender import send_approval_request_email, send_signature_email
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
from app.services.signature_approval_store import (
    claim_signature_request,
    complete_signature_request,
    create_signature_request,
    fail_signature_request,
    get_signature_request,
    get_validator_settings,
    list_signature_requests,
    save_validator_settings,
)
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
            return redirect(url_for("admin_login", next=request.full_path.rstrip("?")))
        return view_func(*args, **kwargs)
    return wrapper


def _is_signature_validator():
    user = session.get("admin_user") or {}
    expected = get_validator_settings()["validator_username"]
    return str(user.get("username", "")).strip().lower() == str(expected).strip().lower()


def _is_admin_manager():
    user = session.get("admin_user") or {}
    return str(user.get("role", "")).strip().lower() == "administrador"


def _signature_validator_required(view_func):
    @wraps(view_func)
    @_admin_login_required
    def wrapper(*args, **kwargs):
        if not _is_signature_validator():
            abort(403)
        return view_func(*args, **kwargs)
    return wrapper


def _admin_manager_required(view_func):
    @wraps(view_func)
    @_admin_login_required
    def wrapper(*args, **kwargs):
        if not _is_admin_manager():
            abort(403)
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
                signature_path.unlink(missing_ok=True)
                return render_template("confirm.html", employee=employee, photo_error=str(exc)), 400
            approval = create_signature_request(
                request_id, employee, signature_path, whatsapp_card_path
            )
            approval_url = current_app.config["APP_BASE_URL"].rstrip("/") + url_for(
                "admin_signature_request", request_id=request_id
            )
            validator = get_validator_settings()
            notification = send_approval_request_email(validator["validator_email"], approval, approval_url)
            detail = "aguardando aprovação"
            if notification.get("status") not in {"sent", "simulated"}:
                detail += f"; notificação falhou: {notification.get('message', 'erro desconhecido')}"
            _log_request(email, "pending_approval", detail)
            return render_template("success.html", email=email)
        except Exception:
            if not get_signature_request(request_id):
                if signature_path:
                    signature_path.unlink(missing_ok=True)
                if whatsapp_card_path:
                    whatsapp_card_path.unlink(missing_ok=True)
            raise

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
            user = authenticate_user(
                current_app.config["ADMIN_USERS_PATH"],
                username,
                password,
                current_app.config["ADMIN_USERS_DB_PATH"],
            )
            expected_hash = current_app.config.get("ADMIN_PASSWORD_HASH") or ""
            if not user and username == current_app.config.get("ADMIN_USERNAME") and expected_hash and check_password_hash(expected_hash, password):
                user = {"username": username, "name": username, "role": "Administrador"}
            if user:
                session.clear()
                session["admin_user"] = user
                session.permanent = True
                _csrf_token()
                _LOGIN_ATTEMPTS.pop(key, None)
                next_url = request.args.get("next", "")
                if next_url.startswith("/admin/") and not next_url.startswith("//"):
                    return redirect(next_url)
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
        validator_settings = get_validator_settings()
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
            approval_rows=list_signature_requests() if _is_signature_validator() else [],
            is_signature_validator=_is_signature_validator(),
            is_admin_manager=_is_admin_manager(),
            validator_settings=validator_settings,
            admin_users=list_active_users(
                current_app.config["ADMIN_USERS_PATH"], current_app.config["ADMIN_USERS_DB_PATH"]
            ),
        )

    @app.route("/admin/usuarios/novo", methods=["POST"])
    @_admin_manager_required
    def admin_user_create():
        if not _valid_csrf():
            flash("A sessão expirou. O usuário não foi criado.")
            return redirect(url_for("admin_dashboard"))
        username = normalize_text(request.form.get("username")).lower()
        name = normalize_text(request.form.get("name"))
        email = normalize_email(request.form.get("email"))
        password = request.form.get("password", "")
        role = request.form.get("role", "")
        make_validator = request.form.get("make_validator") == "1"
        if not re.fullmatch(r"[a-z0-9._-]{3,64}", username):
            flash("O usuário deve ter de 3 a 64 caracteres: letras minúsculas, números, ponto, hífen ou sublinhado.")
            return redirect(url_for("admin_dashboard"))
        if not name or not email or "@" not in email:
            flash("Informe nome e e-mail válidos para o novo usuário.")
            return redirect(url_for("admin_dashboard"))
        if len(password) < 10:
            flash("A senha inicial deve ter pelo menos 10 caracteres.")
            return redirect(url_for("admin_dashboard"))
        if role not in {"Administrador", "Validador de assinaturas"}:
            flash("Selecione um perfil válido.")
            return redirect(url_for("admin_dashboard"))
        try:
            create_admin_user(
                current_app.config["ADMIN_USERS_PATH"],
                current_app.config["ADMIN_USERS_DB_PATH"],
                username,
                name,
                email,
                role,
                password,
                session["admin_user"]["username"],
            )
        except ValueError as exc:
            flash(str(exc))
            return redirect(url_for("admin_dashboard"))
        if make_validator:
            save_validator_settings(username, email, session["admin_user"]["username"])
        flash(f"Usuário {username} criado com sucesso." + (" Ele agora é o aprovador." if make_validator else ""))
        return redirect(url_for("admin_dashboard"))

    @app.route("/admin/configuracao/aprovador", methods=["POST"])
    @_admin_manager_required
    def admin_validator_settings():
        if not _valid_csrf():
            flash("A sessão expirou. O aprovador não foi alterado.")
            return redirect(url_for("admin_dashboard"))
        username = normalize_text(request.form.get("validator_username")).lower()
        email = normalize_email(request.form.get("validator_email"))
        active_users = list_active_users(
            current_app.config["ADMIN_USERS_PATH"], current_app.config["ADMIN_USERS_DB_PATH"]
        )
        if not any(item["username"].lower() == username for item in active_users):
            flash("Selecione um usuário interno ativo para ser o aprovador.")
            return redirect(url_for("admin_dashboard"))
        if not email or "@" not in email:
            flash("Informe um e-mail válido para o aprovador.")
            return redirect(url_for("admin_dashboard"))
        save_validator_settings(username, email, session["admin_user"]["username"])
        flash(f"Aprovador atualizado para {username} ({email}).")
        return redirect(url_for("admin_dashboard"))

    @app.route("/admin/solicitacoes/<request_id>")
    @_signature_validator_required
    def admin_signature_request(request_id):
        approval = get_signature_request(request_id)
        if not approval:
            abort(404)
        return render_template("admin_signature_request.html", approval=approval)

    @app.route("/admin/solicitacoes/<request_id>/imagem/<kind>")
    @_signature_validator_required
    def admin_signature_image(request_id, kind):
        approval = get_signature_request(request_id)
        if not approval or kind not in {"signature", "whatsapp"}:
            abort(404)
        image_path = Path(approval[f"{kind}_path"])
        generated_root = Path(current_app.config["GENERATED_FILES_PATH"]).resolve()
        try:
            resolved = image_path.resolve(strict=True)
            resolved.relative_to(generated_root)
        except (FileNotFoundError, OSError, ValueError):
            abort(404)
        return send_file(resolved, mimetype="image/png", max_age=0)

    @app.route("/admin/solicitacoes/<request_id>/aprovar", methods=["POST"])
    @_signature_validator_required
    def admin_signature_approve(request_id):
        if not _valid_csrf():
            flash("A sessão expirou. A solicitação não foi aprovada.")
            return redirect(url_for("admin_signature_request", request_id=request_id))
        approval = get_signature_request(request_id)
        if not approval:
            abort(404)
        reviewer = session["admin_user"]["username"]
        if not claim_signature_request(request_id, reviewer):
            flash("Esta solicitação já foi processada ou está sendo enviada.")
            return redirect(url_for("admin_signature_request", request_id=request_id))
        try:
            response = send_signature_email(
                approval["employee_email"],
                approval["signature_path"],
                approval["employee_name"],
                whatsapp_card_path=approval["whatsapp_path"],
            )
        except (OSError, ValueError) as exc:
            response = {"status": "error", "message": f"Falha ao preparar o envio: {exc}"}
        if response.get("status") not in {"sent", "simulated"}:
            fail_signature_request(request_id, response.get("message"))
            _log_request(approval["employee_email"], "approval_send_failed", response.get("message", ""))
            flash("A aprovação foi registrada, mas o e-mail não pôde ser enviado. Você pode tentar novamente.")
            return redirect(url_for("admin_signature_request", request_id=request_id))
        complete_signature_request(request_id, reviewer)
        _log_request(approval["employee_email"], "sent", f"aprovada por {reviewer}")
        for path_value in (approval["signature_path"], approval["whatsapp_path"]):
            try:
                Path(path_value).unlink(missing_ok=True)
            except OSError:
                pass
        flash(f"Assinatura de {approval['employee_name']} aprovada e enviada.")
        return redirect(url_for("admin_dashboard"))

    @app.route("/admin/colaboradores/novo", methods=["GET", "POST"])
    @_admin_manager_required
    def admin_employee_new():
        flash("A base corporativa é somente leitura. Cadastros devem ser tratados no projeto de equalização.")
        return redirect(url_for("admin_dashboard"))

    @app.route("/admin/colaboradores/<int:employee_id>/editar", methods=["GET", "POST"])
    @_admin_manager_required
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
    @_admin_manager_required
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
    @_admin_manager_required
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
