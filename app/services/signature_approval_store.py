import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from flask import current_app


def _connection():
    path = Path(current_app.config["SIGNATURE_APPROVALS_PATH"])
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS signature_requests (
            request_id TEXT PRIMARY KEY,
            employee_email TEXT NOT NULL,
            employee_name TEXT NOT NULL,
            job_title TEXT NOT NULL DEFAULT '',
            phone TEXT NOT NULL DEFAULT '',
            signature_path TEXT NOT NULL,
            whatsapp_path TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            reviewed_at TEXT,
            reviewed_by TEXT,
            error_message TEXT NOT NULL DEFAULT ''
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS approval_settings (
            settings_id INTEGER PRIMARY KEY CHECK (settings_id = 1),
            validator_username TEXT NOT NULL,
            validator_email TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            updated_by TEXT NOT NULL
        )
        """
    )
    return connection


def get_validator_settings():
    with _connection() as connection:
        row = connection.execute(
            "SELECT * FROM approval_settings WHERE settings_id = 1"
        ).fetchone()
        if not row:
            now = datetime.now(timezone.utc).isoformat()
            connection.execute(
                """
                INSERT INTO approval_settings (
                    settings_id, validator_username, validator_email, updated_at, updated_by
                ) VALUES (1, ?, ?, ?, 'configuração inicial')
                """,
                (
                    current_app.config["DEFAULT_SIGNATURE_VALIDATOR_USERNAME"],
                    current_app.config["DEFAULT_SIGNATURE_VALIDATOR_EMAIL"],
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM approval_settings WHERE settings_id = 1"
            ).fetchone()
    return dict(row)


def save_validator_settings(username, email, updated_by):
    now = datetime.now(timezone.utc).isoformat()
    with _connection() as connection:
        connection.execute(
            """
            INSERT INTO approval_settings (
                settings_id, validator_username, validator_email, updated_at, updated_by
            ) VALUES (1, ?, ?, ?, ?)
            ON CONFLICT(settings_id) DO UPDATE SET
                validator_username=excluded.validator_username,
                validator_email=excluded.validator_email,
                updated_at=excluded.updated_at,
                updated_by=excluded.updated_by
            """,
            (username, email, now, updated_by),
        )
    return get_validator_settings()


def create_signature_request(request_id, employee, signature_path, whatsapp_path):
    created_at = datetime.now(timezone.utc).isoformat()
    with _connection() as connection:
        connection.execute(
            """
            INSERT INTO signature_requests (
                request_id, employee_email, employee_name, job_title, phone,
                signature_path, whatsapp_path, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                request_id,
                employee["email"],
                employee["full_name"],
                employee.get("job_title", ""),
                employee.get("phone", ""),
                str(signature_path),
                str(whatsapp_path),
                created_at,
            ),
        )
    return get_signature_request(request_id)


def get_signature_request(request_id):
    with _connection() as connection:
        row = connection.execute(
            "SELECT * FROM signature_requests WHERE request_id = ?", (request_id,)
        ).fetchone()
    return dict(row) if row else None


def list_signature_requests(limit=100):
    with _connection() as connection:
        rows = connection.execute(
            """
            SELECT * FROM signature_requests
            ORDER BY CASE status WHEN 'pending' THEN 0 WHEN 'failed' THEN 1 ELSE 2 END,
                     created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def claim_signature_request(request_id, reviewer):
    """Atomically reserve one pending/failed request and prevent duplicate sends."""
    with _connection() as connection:
        cursor = connection.execute(
            """
            UPDATE signature_requests
            SET status = 'sending', reviewed_by = ?, error_message = ''
            WHERE request_id = ? AND status IN ('pending', 'failed')
            """,
            (reviewer, request_id),
        )
    return cursor.rowcount == 1


def complete_signature_request(request_id, reviewer):
    reviewed_at = datetime.now(timezone.utc).isoformat()
    with _connection() as connection:
        connection.execute(
            """
            UPDATE signature_requests
            SET status = 'approved', reviewed_at = ?, reviewed_by = ?, error_message = ''
            WHERE request_id = ? AND status = 'sending'
            """,
            (reviewed_at, reviewer, request_id),
        )


def fail_signature_request(request_id, message):
    with _connection() as connection:
        connection.execute(
            """
            UPDATE signature_requests SET status = 'failed', error_message = ?
            WHERE request_id = ? AND status = 'sending'
            """,
            (str(message or "Falha ao enviar")[:500], request_id),
        )
