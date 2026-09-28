from pathlib import Path

from app import create_app
from app.db import seed_local_employee_data
from app.services.email_sender import resolve_smtp_settings
from app.services.employee_importer import build_employee_record, normalize_email, validate_employee_row
from app.services.signature_generator import generate_signature_image
from app.services.admin_auth import authenticate_user
from app.services.workbook_store import save_employee_to_workbook


def test_normalize_email():
    assert normalize_email("  Ana.Maria@Empresa.COM ") == "ana.maria@empresa.com"


def test_validate_employee_row_rejects_invalid_email():
    row = {"nome": "João Silva", "cargo": "Analista", "email": "joao@invalid", "ativo": "SIM"}
    issues = validate_employee_row(row)
    assert "email" in issues


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
        # The latest approved PPT no longer has the social-media icon strip.
        assert generated.crop((400, 145, 500, 173)).getcolors(maxcolors=1) == [
            (100 * 28, (255, 255, 255))
        ]


def test_public_lookup_route(tmp_path):
    app = create_app(test_config={"TESTING": True, "DATABASE_PATH": str(tmp_path / "db.sqlite3")})
    with app.test_client() as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json["status"] == "ok"
        home = client.get("/")
        assert 'href="/admin/login"' in home.text
        assert "Área administrativa" in home.text


def test_seed_local_employee_data(tmp_path):
    from openpyxl import Workbook

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    workbook = Workbook()
    workbook.active.append(["Nome", "Cargo", "Email", "Celular", "Ativo"])
    workbook.active.append([
        "Adriano Jarochevski",
        "Analista",
        "adriano.jarochevski@dinhodistribuidora.com.br",
        "+55 41 99999-0000",
        "SIM",
    ])
    workbook.save(data_dir / "colaboradores.xlsx")
    database_path = tmp_path / "seed.sqlite3"
    rows = seed_local_employee_data(str(database_path), str(data_dir))
    assert rows > 0

    row = __import__("app.db", fromlist=["get_connection"]).get_connection(str(database_path)).execute(
        "SELECT full_name, job_title, email FROM employees WHERE email='adriano.jarochevski@dinhodistribuidora.com.br' LIMIT 1"
    ).fetchone()
    assert row is not None
    assert row["job_title"] not in ("", "Colaborador")


def test_seed_does_not_delete_persisted_employees(tmp_path):
    import sqlite3
    from openpyxl import Workbook

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    workbook = Workbook()
    workbook.active.append(["Nome", "Cargo", "Email", "Ativo"])
    workbook.active.append(["Pessoa da Planilha", "Analista", "planilha@empresa.com", "SIM"])
    workbook.save(data_dir / "colaboradores.xlsx")

    database_path = tmp_path / "employees.sqlite3"
    seed_local_employee_data(str(database_path), str(data_dir))
    connection = sqlite3.connect(database_path)
    connection.execute(
        "INSERT INTO employees (full_name, job_title, email, active) VALUES (?, ?, ?, ?)",
        ("Cadastro Administrativo", "Gestor", "admin@empresa.com", 1),
    )
    connection.commit()
    connection.close()

    seed_local_employee_data(str(database_path), str(data_dir))

    connection = sqlite3.connect(database_path)
    emails = {row[0] for row in connection.execute("SELECT email FROM employees")}
    connection.close()
    assert emails == {"planilha@empresa.com", "admin@empresa.com"}


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
        "DATABASE_PATH": str(tmp_path / "db.sqlite3"),
        "EMPLOYEE_WORKBOOK_PATH": str(workbook_path),
        "ADMIN_USERS_PATH": str(users_path),
        "SEED_LOCAL_DATA": False,
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
