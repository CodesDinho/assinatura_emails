"""Leitura somente leitura da base corporativa homologada de RH."""

from __future__ import annotations

import os
from pathlib import Path
import sqlite3


DEFAULT_DATABASE_PATH = Path("/shared/slack_apps.db")
NOT_EQUALIZED_MESSAGE = (
    "A base de dados ainda não está equalizada. Solicite à TI que execute a rotina no projeto de "
    "equalização de usuários, sistemas e equipamentos."
)
UNAVAILABLE_MESSAGE = (
    "A base corporativa está indisponível. Solicite à TI que verifique a rotina no projeto de "
    "equalização de usuários, sistemas e equipamentos."
)


def database_path() -> Path:
    try:
        from flask import current_app

        configured = current_app.config.get("SHARED_SQLITE_PATH")
    except RuntimeError:
        configured = None
    return Path(configured or os.getenv("SHARED_SQLITE_PATH", str(DEFAULT_DATABASE_PATH)))


def _connection() -> sqlite3.Connection:
    path = database_path()
    if not path.is_file():
        raise FileNotFoundError(f"SQLite corporativo não encontrado: {path}")
    connection = sqlite3.connect(
        f"file:{path.resolve().as_posix()}?mode=ro", uri=True, timeout=10
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=10000")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def equalization_status() -> dict:
    """Informa se existe uma publicação homologada pronta para consumo."""
    try:
        with _connection() as connection:
            schema_ready = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='rh_importacoes'"
            ).fetchone() is not None
            view_ready = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='view' AND name='rh_assinaturas_colaboradores'"
            ).fetchone() is not None
            if not schema_ready or not view_ready:
                return {"available": True, "equalized": False, "message": NOT_EQUALIZED_MESSAGE, "latest": None}
            latest = connection.execute(
                """
                SELECT source_hash, arquivo_origem, imported_at, imported_by, status,
                       ativos, com_email, sem_email
                FROM rh_importacoes
                WHERE status='PUBLICADO'
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
            if latest is None:
                return {"available": True, "equalized": False, "message": NOT_EQUALIZED_MESSAGE, "latest": None}
            return {
                "available": True,
                "equalized": True,
                "message": "Base corporativa equalizada e publicada.",
                "latest": dict(latest),
            }
    except (FileNotFoundError, sqlite3.Error) as exc:
        return {
            "available": False,
            "equalized": False,
            "message": UNAVAILABLE_MESSAGE,
            "latest": None,
            "error": str(exc),
        }


def _employee(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "full_name": row["nome"],
        "job_title": row["cargo"] or "",
        "email": (row["email"] or "").strip().lower(),
        "phone": row["celular"] or "",
        "active": row["ativo"] == "SIM",
        "email_ready": bool(row["email_valido"]),
        "status_validation": row["status_validacao"],
        "registration": row["mat"],
        "company": row["razao_social"],
    }


def list_employees(*, active_only: bool = True) -> list[dict]:
    where = "WHERE ativo_rh=1" if active_only else ""
    with _connection() as connection:
        rows = connection.execute(
            f"""
            SELECT id, nome, cargo, email, celular, ativo, email_valido,
                   status_validacao, mat, razao_social
            FROM rh_assinaturas_colaboradores
            {where}
            ORDER BY nome COLLATE NOCASE
            """
        ).fetchall()
    return [_employee(row) for row in rows]


def find_employee_by_email(
    email: str, *, active_only: bool = True, require_valid_email: bool = True
) -> dict | None:
    normalized = str(email or "").strip().lower()
    if not normalized:
        return None
    clauses = ["lower(trim(email))=?"]
    if active_only:
        clauses.append("ativo_rh=1")
    if require_valid_email:
        clauses.append("email_valido=1")
    with _connection() as connection:
        row = connection.execute(
            f"""
            SELECT id, nome, cargo, email, celular, ativo, email_valido,
                   status_validacao, mat, razao_social
            FROM rh_assinaturas_colaboradores
            WHERE {' AND '.join(clauses)}
            ORDER BY ativo_rh DESC, id
            LIMIT 1
            """,
            (normalized,),
        ).fetchone()
    return _employee(row) if row is not None else None


def readiness_summary() -> dict:
    with _connection() as connection:
        row = connection.execute(
            """
            SELECT
                SUM(CASE WHEN ativo_rh=1 THEN 1 ELSE 0 END) AS active,
                SUM(CASE WHEN ativo_rh=1 AND email_valido=1 THEN 1 ELSE 0 END) AS ready,
                SUM(CASE WHEN ativo_rh=1 AND COALESCE(email_valido, 0)=0 THEN 1 ELSE 0 END) AS missing
            FROM rh_assinaturas_colaboradores
            """
        ).fetchone()
    return {
        "active": int(row["active"] or 0),
        "ready": int(row["ready"] or 0),
        "missing_email": int(row["missing"] or 0),
    }
