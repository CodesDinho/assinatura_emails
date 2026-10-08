from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
import sqlite3


DEFAULT_PATH = Path(__file__).resolve().parents[2] / "instance" / "employee_overrides.db"


def override_database_path() -> Path:
    try:
        from flask import current_app

        configured = current_app.config.get("EMPLOYEE_OVERRIDES_PATH")
    except RuntimeError:
        configured = None
    return Path(configured or os.getenv("EMPLOYEE_OVERRIDES_PATH", str(DEFAULT_PATH)))


def _connection() -> sqlite3.Connection:
    path = override_database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=10000")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS employee_overrides (
            employee_id INTEGER PRIMARY KEY,
            source_registration TEXT NOT NULL DEFAULT '',
            source_name TEXT NOT NULL,
            full_name TEXT NOT NULL,
            job_title TEXT NOT NULL,
            email TEXT NOT NULL DEFAULT '',
            phone TEXT NOT NULL DEFAULT '',
            active INTEGER NOT NULL CHECK (active IN (0, 1)),
            registration TEXT NOT NULL DEFAULT '',
            company TEXT NOT NULL DEFAULT '',
            updated_by TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            deleted INTEGER NOT NULL DEFAULT 0 CHECK (deleted IN (0, 1))
        )
        """
    )
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(employee_overrides)")}
    if "deleted" not in columns:
        connection.execute(
            "ALTER TABLE employee_overrides ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0"
        )
    return connection


def load_employee_overrides() -> dict[int, dict]:
    with _connection() as connection:
        rows = connection.execute("SELECT * FROM employee_overrides").fetchall()
    return {int(row["employee_id"]): dict(row) for row in rows}


def save_employee_override(employee_id: int, source: dict, values: dict, updated_by: str) -> None:
    updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _connection() as connection:
        connection.execute(
            """
            INSERT INTO employee_overrides (
                employee_id, source_registration, source_name, full_name, job_title,
                email, phone, active, registration, company, updated_by, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(employee_id) DO UPDATE SET
                source_registration=excluded.source_registration,
                source_name=excluded.source_name,
                full_name=excluded.full_name,
                job_title=excluded.job_title,
                email=excluded.email,
                phone=excluded.phone,
                active=excluded.active,
                registration=excluded.registration,
                company=excluded.company,
                updated_by=excluded.updated_by,
                updated_at=excluded.updated_at,
                deleted=0
            """,
            (
                employee_id,
                source.get("source_registration", source.get("registration", "")),
                source.get("source_name", source.get("full_name", "")),
                values["full_name"],
                values["job_title"],
                values["email"],
                values["phone"],
                1 if values["active"] else 0,
                values["registration"],
                values["company"],
                updated_by,
                updated_at,
            ),
        )
        connection.commit()


def delete_employee_override(employee_id: int, source: dict, updated_by: str) -> None:
    updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _connection() as connection:
        connection.execute(
            """
            INSERT INTO employee_overrides (
                employee_id, source_registration, source_name, full_name, job_title,
                email, phone, active, registration, company, updated_by, updated_at, deleted
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(employee_id) DO UPDATE SET
                source_registration=excluded.source_registration,
                source_name=excluded.source_name,
                updated_by=excluded.updated_by,
                updated_at=excluded.updated_at,
                deleted=1
            """,
            (
                employee_id,
                source.get("source_registration", source.get("registration", "")),
                source.get("source_name", source.get("full_name", "")),
                source["full_name"],
                source["job_title"],
                source["email"],
                source["phone"],
                1 if source["active"] else 0,
                source["registration"],
                source["company"],
                updated_by,
                updated_at,
            ),
        )
        connection.commit()
