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
    duplicate_email = "duplicate_email" in row.keys() and bool(row["duplicate_email"])
    return {
        "id": row["id"],
        "full_name": row["nome"],
        "job_title": row["cargo"] or "",
        "email": (row["email"] or "").strip().lower(),
        "phone": row["celular"] or "",
        "active": row["ativo"] == "SIM",
        "email_ready": bool(row["email_valido"]) and not duplicate_email,
        "duplicate_email": duplicate_email,
        "status_validation": row["status_validacao"],
        "registration": row["mat"],
        "company": row["razao_social"],
    }


def list_employees(*, active_only: bool = True) -> list[dict]:
    where = "WHERE ativo_rh=1" if active_only else ""
    with _connection() as connection:
        rows = connection.execute(
            f"""
            SELECT employee.id, employee.nome, employee.cargo, employee.email,
                   employee.celular, employee.ativo, employee.email_valido,
                   employee.status_validacao, employee.mat, employee.razao_social,
                   CASE WHEN trim(COALESCE(employee.email, '')) <> '' AND EXISTS (
                       SELECT 1 FROM rh_assinaturas_colaboradores duplicate
                       WHERE duplicate.id <> employee.id AND duplicate.ativo_rh=1
                         AND duplicate.email_valido=1
                         AND lower(trim(duplicate.email))=lower(trim(employee.email))
                   ) THEN 1 ELSE 0 END AS duplicate_email
            FROM rh_assinaturas_colaboradores employee
            {where}
            ORDER BY employee.nome COLLATE NOCASE
            """
        ).fetchall()
    return [_employee(row) for row in rows]


def find_employee_by_email(
    email: str, *, active_only: bool = True, require_valid_email: bool = True
) -> dict | None:
    normalized = str(email or "").strip().lower()
    if not normalized:
        return None
    clauses = ["lower(trim(employee.email))=?"]
    if active_only:
        clauses.append("employee.ativo_rh=1")
    if require_valid_email:
        clauses.append("employee.email_valido=1")
    with _connection() as connection:
        row = connection.execute(
            f"""
            SELECT employee.id, employee.nome, employee.cargo, employee.email,
                   employee.celular, employee.ativo, employee.email_valido,
                   employee.status_validacao, employee.mat, employee.razao_social,
                   0 AS duplicate_email
            FROM rh_assinaturas_colaboradores employee
            WHERE {' AND '.join(clauses)}
              AND NOT EXISTS (
                  SELECT 1 FROM rh_assinaturas_colaboradores duplicate
                  WHERE duplicate.id <> employee.id AND duplicate.ativo_rh=1
                    AND duplicate.email_valido=1
                    AND lower(trim(duplicate.email))=lower(trim(employee.email))
              )
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
                SUM(CASE WHEN employee.ativo_rh=1 AND employee.email_valido=1 AND NOT EXISTS (
                    SELECT 1 FROM rh_assinaturas_colaboradores duplicate
                    WHERE duplicate.id <> employee.id AND duplicate.ativo_rh=1
                      AND duplicate.email_valido=1
                      AND lower(trim(duplicate.email))=lower(trim(employee.email))
                ) THEN 1 ELSE 0 END) AS ready,
                SUM(CASE WHEN employee.ativo_rh=1 AND (COALESCE(employee.email_valido, 0)=0 OR EXISTS (
                    SELECT 1 FROM rh_assinaturas_colaboradores duplicate
                    WHERE duplicate.id <> employee.id AND duplicate.ativo_rh=1
                      AND duplicate.email_valido=1
                      AND lower(trim(duplicate.email))=lower(trim(employee.email))
                )) THEN 1 ELSE 0 END) AS missing
            FROM rh_assinaturas_colaboradores employee
            """
        ).fetchone()
    return {
        "active": int(row["active"] or 0),
        "ready": int(row["ready"] or 0),
        "missing_email": int(row["missing"] or 0),
    }


def lorac_coverage() -> dict:
    """Audit active RH employees that also have an active Lorac identity."""
    with _connection() as connection:
        rows = connection.execute(
            """
            SELECT id, nome, cargo, email, sharepoint_upn, email_valido,
                   status_validacao
            FROM rh_assinaturas_colaboradores
            WHERE ativo_rh=1 AND COALESCE(sharepoint_ativo, 0)=1
            ORDER BY nome COLLATE NOCASE
            """
        ).fetchall()
    pending = [
        {
            "id": row["id"], "full_name": row["nome"],
            "job_title": row["cargo"] or "",
            "email": (row["email"] or "").strip().lower(),
            "lorac_upn": row["sharepoint_upn"] or "",
            "status_validation": row["status_validacao"],
        }
        for row in rows
        if not row["email_valido"] or not (row["email"] or "").strip()
    ]
    total = len(rows)
    ready = total - len(pending)
    return {
        "total": total, "ready": ready, "pending": pending,
        "pending_count": len(pending),
        "coverage_percent": round((ready / total * 100) if total else 100.0, 1),
    }
