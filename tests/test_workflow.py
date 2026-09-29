from pathlib import Path

from app import create_app
from app.services.email_sender import resolve_smtp_settings
from app.services.employee_importer import build_employee_record, normalize_email, validate_employee_row
from app.services.signature_generator import ICON_BLUE, _font, generate_signature_image
from app.services.admin_auth import authenticate_user
from app.services.request_log import append_request_log, successful_request_emails
from app.services.workbook_store import (
    find_employee_by_email,
    list_employees,
    merge_emails_from_workbook,
    replace_employee_workbook_from_upload,
    save_employee_to_workbook,
)


def test_normalize_email():
    assert normalize_email("  Ana.Maria@Empresa.COM ") == "ana.maria@empresa.com"


def test_validate_employee_row_rejects_invalid_email():
    row = {"nome": "João Silva", "cargo": "Analista", "email": "joao@invalid", "ativo": "SIM"}
    issues = validate_employee_row(row)
    assert "email" in issues


def test_validate_employee_row_allows_missing_email_during_import():
    row = {"nome": "João Silva", "cargo": "Analista", "email": "", "ativo": "SIM"}

    assert validate_employee_row(row, require_email=False) == {}


def test_validate_employee_row_still_rejects_filled_invalid_email_during_import():
    row = {"nome": "João Silva", "cargo": "Analista", "email": "joao@invalid", "ativo": "SIM"}

    assert "email" in validate_employee_row(row, require_email=False)


def test_build_employee_record_accepts_optional_celular():
    record = build_employee_record({
        "nome": "Ana Silva",
        "cargo": "Analista",
        "email": "ana@empresa.com",
        "celular": "+55 41 99999-0000",
    })
    assert record["phone"] == "+55 41 99999-0000"


def test_generate_signature_image_creates_png(tmp_path):
    target = tmp_path / "assinatura.png"
    result = generate_signature_image(
        name="Maria da Silva",
        role="Analista de Negócios",
        email="maria.silva@empresa.com",
        output_path=target,
    )
    assert result.exists()
    assert result.suffix.lower() == ".png"
    assert result.stat().st_size > 0
    from PIL import Image

    with Image.open(result) as generated:
        assert generated.size == (532, 173)
        # The approved PPT no longer contains the social-media icon strip.
        assert generated.crop((400, 145, 500, 173)).getcolors(maxcolors=1) == [(2800, (255, 255, 255))]
        # Its separator extends through the right side of the signature.
        assert generated.getpixel((500, 69)) == ICON_BLUE


def test_signature_uses_bundled_poppins_bold():
    assert _font(12).getname() == ("Poppins", "Bold")


def test_public_lookup_route(tmp_path):
    from openpyxl import Workbook

    workbook_path = tmp_path / "colaboradores.xlsx"
    workbook = Workbook()
    workbook.active.append(["Nome", "Cargo", "Email", "Celular", "Ativo"])
    workbook.save(workbook_path)
    app = create_app(test_config={"TESTING": True, "EMPLOYEE_WORKBOOK_PATH": str(workbook_path)})
    with app.test_client() as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json["status"] == "ok"
        home = client.get("/")
        assert 'href="/admin/login"' in home.text
        assert "Área administrativa" in home.text
        assert "entre em contato com o RH" in home.text
        assert "Opções &gt; Email &gt; Assinaturas" in home.text
        assert "respostas e encaminhamentos" in home.text


def test_workbook_is_the_employee_source_of_truth(tmp_path):
    from openpyxl import Workbook

    workbook_path = tmp_path / "colaboradores.xlsx"
    workbook = Workbook()
    workbook.active.append(["Nome", "Cargo", "Email", "Celular", "Ativo"])
    workbook.active.append(["Pessoa Ativa", "Analista", "ativa@empresa.com", "123", "SIM"])
    workbook.active.append(["Pessoa sem E-mail", "Operador", "", "", "SIM"])
    workbook.active.append(["Pessoa Inativa", "Gestor", "inativa@empresa.com", "", "NÃO"])
    workbook.save(workbook_path)

    employees = list_employees(workbook_path)
    assert len(employees) == 3
    assert find_employee_by_email(workbook_path, "ATIVA@EMPRESA.COM")["full_name"] == "Pessoa Ativa"
    assert find_employee_by_email(workbook_path, "inativa@empresa.com") is None


def test_resolve_smtp_settings_supports_sgq_env_names(monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("SMTP_PORT", raising=False)
    monkeypatch.delenv("SMTP_USERNAME", raising=False)
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    monkeypatch.delenv("SMTP_FROM", raising=False)
    monkeypatch.delenv("SMTP_USE_TLS", raising=False)

    monkeypatch.setenv("SGQ_SMTP_HOST", "smtp.emailnuvem.com.br")
    monkeypatch.setenv("SGQ_SMTP_PORT", "465")
    monkeypatch.setenv("SGQ_SMTP_USER", "suporte.dinho@dinhodistribuidora.com.br")
    monkeypatch.setenv("SGQ_SMTP_PASSWORD", "secret")
    monkeypatch.setenv("SGQ_FROM_EMAIL", "suporte.dinho@dinhodistribuidora.com.br")
    monkeypatch.setenv("SGQ_SMTP_TLS", "false")
    monkeypatch.setenv("SGQ_SMTP_SSL", "true")

    settings = resolve_smtp_settings()

    assert settings["host"] == "smtp.emailnuvem.com.br"
    assert settings["port"] == 465
    assert settings["username"] == "suporte.dinho@dinhodistribuidora.com.br"
    assert settings["password"] == "secret"
    assert settings["from_email"] == "suporte.dinho@dinhodistribuidora.com.br"
    assert settings["use_tls"] is False
    assert settings["use_ssl"] is True


def test_configured_internal_users_authenticate():
    users_path = Path(__file__).resolve().parent.parent / "config" / "users.json"
    assert authenticate_user(users_path, "suporte.dinho", "suporte.dinho")["username"] == "suporte.dinho"
    assert authenticate_user(users_path, "rh", "rh")["username"] == "rh"
    assert authenticate_user(users_path, "rh", "senha-incorreta") is None


def test_save_employee_adds_phone_columns_without_losing_existing_data(tmp_path):
    from openpyxl import Workbook, load_workbook

    workbook_path = tmp_path / "colaboradores.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["MAT", "Nome", "Cargo"])
    sheet.append([10, "Ana Silva", "Analista"])
    workbook.save(workbook_path)

    save_employee_to_workbook(workbook_path, {
        "full_name": "Ana Silva",
        "job_title": "Analista Sênior",
        "email": "ana@dinhodistribuidora.com.br",
        "phone": "+55 41 99999-0000",
        "active": True,
    }, original={"full_name": "Ana Silva", "email": ""})

    saved = load_workbook(workbook_path, data_only=True).active
    assert saved.cell(2, 1).value == 10
    values = {saved.cell(1, column).value: saved.cell(2, column).value for column in range(1, saved.max_column + 1)}
    assert values["Email"] == "ana@dinhodistribuidora.com.br"
    assert values["Celular"] == "+55 41 99999-0000"
    assert values["Cargo"] == "Analista Sênior"


def test_xlsx_import_replaces_entire_base_and_preserves_every_column(tmp_path):
    from io import BytesIO
    from openpyxl import Workbook, load_workbook

    workbook_path = tmp_path / "colaboradores.xlsx"
    old = Workbook()
    old.active.append(["Nome", "Cargo", "Email"])
    old.active.append(["Registro antigo", "Cargo antigo", "antigo@empresa.com"])
    old.save(workbook_path)

    uploaded = Workbook()
    uploaded.active.append([
        "MAT", "Nome", "Razão Social", "Lotação", "Admissão", "CPF",
        "Cargo", "Email", "Celular", "Ativo", "Campo adicional",
    ])
    uploaded.active.append([
        123, "Pessoa Nova", "Empresa", "Unidade", "01/01/2026", "000",
        "Cargo atualizado", "nova@empresa.com", "9999", "SIM", "Preservado",
    ])
    payload = BytesIO()
    uploaded.save(payload)

    result = replace_employee_workbook_from_upload(workbook_path, payload)

    saved = load_workbook(workbook_path, data_only=True).active
    headers = [cell.value for cell in saved[1]]
    values = {headers[index]: saved.cell(2, index + 1).value for index in range(len(headers))}
    assert saved.max_row == 2
    assert values["Nome"] == "Pessoa Nova"
    assert values["Cargo"] == "Cargo atualizado"
    assert values["Campo adicional"] == "Preservado"
    assert "Registro antigo" not in {cell.value for row in saved.iter_rows() for cell in row}
    assert list(tmp_path.joinpath("backups").glob("*.xlsx"))
    assert len(result["sha256"]) == 64
    assert result["size"] == workbook_path.stat().st_size


def test_admin_area_requires_login_and_saves_new_employee_to_excel(tmp_path):
    from openpyxl import Workbook, load_workbook

    workbook_path = tmp_path / "colaboradores.xlsx"
    workbook = Workbook()
    workbook.active.append(["MAT", "Nome", "Cargo"])
    workbook.save(workbook_path)
    users_path = Path(__file__).resolve().parent.parent / "config" / "users.json"
    app = create_app(test_config={
        "TESTING": True,
        "SECRET_KEY": "test-secret",
        "EMPLOYEE_WORKBOOK_PATH": str(workbook_path),
        "ADMIN_USERS_PATH": str(users_path),
        "REQUEST_LOG_PATH": str(tmp_path / "requests.jsonl"),
        "GENERATED_FILES_PATH": str(tmp_path / "generated"),
    })

    with app.test_client() as client:
        assert client.get("/admin").status_code == 302
        login_page = client.get("/admin/login")
        token = login_page.text.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
        login = client.post("/admin/login", data={"username": "rh", "password": "rh", "csrf_token": token})
        assert login.status_code == 302

        new_page = client.get("/admin/colaboradores/novo")
        token = new_page.text.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
        created = client.post("/admin/colaboradores/novo", data={
            "csrf_token": token,
            "full_name": "Maria Teste",
            "job_title": "Analista",
            "email": "maria.teste@dinhodistribuidora.com.br",
            "phone": "+55 41 98888-7777",
            "active": "1",
        })
        assert created.status_code == 302

    saved = load_workbook(workbook_path, data_only=True).active
    headers = [cell.value for cell in saved[1]]
    assert saved.cell(2, headers.index("Celular") + 1).value == "+55 41 98888-7777"
    assert len(list(tmp_path.joinpath("backups").glob("*.xlsx"))) >= 1
    assert not list(tmp_path.glob("*.sqlite*"))


def test_employee_can_update_phone_before_generating_signature(tmp_path, monkeypatch):
    from openpyxl import Workbook, load_workbook

    workbook_path = tmp_path / "colaboradores.xlsx"
    workbook = Workbook()
    workbook.active.append(["Nome", "Cargo", "Email", "Celular", "Ativo"])
    workbook.active.append(["Maria Teste", "Analista", "maria@empresa.com", "1111", "SIM"])
    workbook.save(workbook_path)
    log_path = tmp_path / "requests.jsonl"
    app = create_app(test_config={
        "TESTING": True,
        "SECRET_KEY": "test-secret",
        "EMPLOYEE_WORKBOOK_PATH": str(workbook_path),
        "REQUEST_LOG_PATH": str(log_path),
        "GENERATED_FILES_PATH": str(tmp_path / "generated"),
    })
    monkeypatch.setattr("app.routes.send_signature_email", lambda *args, **kwargs: {"status": "simulated"})

    with app.test_client() as client:
        confirmation = client.post("/consultar", data={"email": "maria@empresa.com"})
        assert confirmation.status_code == 200
        assert 'name="phone"' in confirmation.text
        assert 'value="1111"' in confirmation.text
        assert "Caso seja necessária a edição de cargo" in confirmation.text
        token = confirmation.text.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
        sent = client.post("/solicitar", data={
            "csrf_token": token,
            "email": "maria@empresa.com",
            "phone": "+55 41 99999-0000",
        })
        assert sent.status_code == 200
        assert "maria@empresa.com" in successful_request_emails(log_path)

    saved = load_workbook(workbook_path, data_only=True).active
    assert saved.cell(2, 4).value == "+55 41 99999-0000"
    assert list(tmp_path.joinpath("backups").glob("*.xlsx"))


def test_admin_dashboard_marks_generated_signature_and_has_sortable_headers(tmp_path):
    from openpyxl import Workbook

    workbook_path = tmp_path / "colaboradores.xlsx"
    workbook = Workbook()
    workbook.active.append(["Nome", "Cargo", "Email", "Celular", "Ativo"])
    workbook.active.append(["Maria Teste", "Analista", "maria@empresa.com", "", "SIM"])
    workbook.save(workbook_path)
    log_path = tmp_path / "requests.jsonl"
    append_request_log(log_path, "maria@empresa.com", "sent", "assinatura enviada")
    users_path = Path(__file__).resolve().parent.parent / "config" / "users.json"
    app = create_app(test_config={
        "TESTING": True,
        "SECRET_KEY": "test-secret",
        "EMPLOYEE_WORKBOOK_PATH": str(workbook_path),
        "ADMIN_USERS_PATH": str(users_path),
        "REQUEST_LOG_PATH": str(log_path),
    })

    with app.test_client() as client:
        login_page = client.get("/admin/login")
        token = login_page.text.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
        client.post("/admin/login", data={"username": "rh", "password": "rh", "csrf_token": token})
        dashboard = client.get("/admin")
        assert dashboard.status_code == 200
        assert 'data-column="0"' in dashboard.text
        assert "Assinatura gerada" in dashboard.text
        assert 'class="signature-state generated">Sim' in dashboard.text


def test_merge_emails_matches_normalized_names_without_overwriting(tmp_path):
    from openpyxl import Workbook

    employees_path = tmp_path / "colaboradores.xlsx"
    employees = Workbook()
    employees.active.append(["Nome", "Cargo", "Email"])
    employees.active.append(["Ana Luíza de Carvalho", "Analista", ""])
    employees.active.append(["E-mail Existente", "Gestor", "manter@empresa.com"])
    employees.save(employees_path)

    emails_path = tmp_path / "emails.xlsx"
    emails = Workbook()
    emails.active.append(["NOME", "Email"])
    emails.active.append(["  ANA LUIZA DE CARVALHO ", "ana.luiza@empresa.com"])
    emails.active.append(["E-MAIL EXISTENTE", "substituir@empresa.com"])
    emails.save(emails_path)

    result = merge_emails_from_workbook(employees_path, emails_path)

    assert result["updated"] == 1
    assert find_employee_by_email(employees_path, "ana.luiza@empresa.com")["full_name"] == "Ana Luíza de Carvalho"
    assert find_employee_by_email(employees_path, "manter@empresa.com")["full_name"] == "E-mail Existente"
    assert list(tmp_path.joinpath("backups").glob("*.xlsx"))
