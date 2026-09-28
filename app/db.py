import re
import sqlite3
from pathlib import Path

from openpyxl import load_workbook

from app.services.employee_importer import normalize_email, normalize_name


def _normalized_name(value):
    return re.sub(r"\s+", " ", (value or "").strip()).upper()


def get_connection(database_path: str):
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database(database_path: str):
    database_file = Path(database_path)
    database_file.parent.mkdir(parents=True, exist_ok=True)

    connection = get_connection(str(database_file))
    try:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS employees (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT NOT NULL,
                job_title TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                active INTEGER NOT NULL DEFAULT 1,
                phone TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS request_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                normalized_email TEXT NOT NULL,
                request_id TEXT NOT NULL,
                status TEXT NOT NULL,
                details TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        connection.commit()
    finally:
        connection.close()


def seed_local_employee_data(database_path: str, data_dir: str | None = None) -> int:
    """Synchronize spreadsheet employees without deleting persisted records.

    The database lives in a Docker volume and may contain administrative changes.
    Startup synchronization therefore uses upserts; a missing, partial, or stale
    spreadsheet must never empty the employees table.
    """
    initialize_database(database_path)
    source_dir = Path(data_dir) if data_dir else Path(__file__).resolve().parent.parent / "data"
    if not source_dir.exists():
        return 0

    merged = {}
    for workbook_path in sorted(source_dir.glob("*.xlsx")):
        workbook = load_workbook(workbook_path, data_only=True)
        sheet = workbook[workbook.sheetnames[0]]
        rows = list(sheet.iter_rows(values_only=True))
        if not rows:
            continue

        headers = [str(value or "").strip() for value in rows[0]]
        for row in rows[1:]:
            mapped = dict(zip(headers, row))
            full_name = normalize_name(
                mapped.get("Nome")
                or mapped.get("NOME")
                or mapped.get("name")
                or mapped.get("full_name")
            )
            if not full_name:
                continue

            key = _normalized_name(full_name)
            record = merged.setdefault(key, {"full_name": full_name, "job_title": "", "email": "", "phone": ""})

            cargo = str(
                mapped.get("Cargo")
                or mapped.get("cargo")
                or mapped.get("Cargo Oficial")
                or mapped.get("job_title")
                or mapped.get("role")
                or ""
            ).strip()
            if cargo and not record["job_title"]:
                record["job_title"] = cargo

            email = normalize_email(
                mapped.get("Email")
                or mapped.get("email")
                or mapped.get("e-mail")
                or mapped.get("e_mail")
            )
            if email and not record["email"]:
                record["email"] = email

            phone = str(
                mapped.get("Telefone")
                or mapped.get("telefone")
                or mapped.get("Celular")
                or mapped.get("celular")
                or mapped.get("Telefone Celular")
                or mapped.get("Celular Corporativo")
                or mapped.get("phone")
                or ""
            ).strip()
            if phone and not record["phone"]:
                record["phone"] = phone

            if "Ativo" in mapped or "ativo" in mapped:
                active_value = str(mapped.get("Ativo") or mapped.get("ativo") or "SIM").strip().upper()
                record["active"] = active_value not in {"NÃO", "NAO", "NO", "N", "0", "FALSE", "INATIVO"}

    if not merged:
        return 0

    connection = get_connection(database_path)
    try:
        imported = 0
        imported_emails = set()
        for record in merged.values():
            job_title = record["job_title"]
            email = record["email"]
            if not email or email in imported_emails:
                continue

            connection.execute(
                """
                INSERT INTO employees
                    (full_name, job_title, email, active, phone, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                ON CONFLICT(email) DO UPDATE SET
                    full_name=excluded.full_name,
                    job_title=excluded.job_title,
                    active=excluded.active,
                    phone=excluded.phone,
                    updated_at=datetime('now')
                """,
                (record["full_name"], job_title, email, 1 if record.get("active", True) else 0, record["phone"]),
            )
            imported_emails.add(email)
            imported += 1

        connection.commit()
        return imported
    finally:
        connection.close()
