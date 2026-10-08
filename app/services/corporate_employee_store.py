"""Leitura somente leitura da base corporativa homologada de RH."""

from __future__ import annotations

import os
from pathlib import Path
import sqlite3

from app.services.employee_importer import EMAIL_PATTERN
from app.services.employee_override_store import load_employee_overrides


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
        "active": bool(row["ativo_rh"]),
        "email_ready": bool(row["email_valido"]),
        "duplicate_email": False,
        "status_validation": row["status_validacao"],
        "registration": str(row["mat"] or ""),
        "company": row["razao_social"] or "",
        "lorac_upn": row["sharepoint_upn"] or "",
        "sharepoint_active": bool(row["sharepoint_ativo"]),
        "overridden": False,
        "override_updated_by": "",
        "override_updated_at": "",
        "source_registration": str(row["mat"] or ""),
        "source_name": row["nome"],
    }


def _base_employees() -> list[dict]:
    with _connection() as connection:
        rows = connection.execute(
            """
            SELECT employee.id, employee.nome, employee.cargo, employee.email,
                   employee.celular, employee.ativo, employee.email_valido,
                   employee.status_validacao, employee.mat, employee.razao_social,
                   employee.ativo_rh, employee.sharepoint_upn, employee.sharepoint_ativo
            FROM rh_assinaturas_colaboradores employee
            ORDER BY employee.nome COLLATE NOCASE
            """
        ).fetchall()
    return [_employee(row) for row in rows]


def _override_matches_source(employee: dict, override: dict) -> bool:
    stored_registration = str(override.get("source_registration") or "").strip()
    if stored_registration:
        return stored_registration == employee["source_registration"]
    return str(override.get("source_name") or "").strip().casefold() == employee["source_name"].strip().casefold()


def _merged_employees() -> list[dict]:
    employees = _base_employees()
    overrides = load_employee_overrides()
    for employee in employees:
        override = overrides.get(employee["id"])
        if not override or not _override_matches_source(employee, override):
            continue
        employee.update(
            full_name=override["full_name"],
            job_title=override["job_title"],
            email=(override["email"] or "").strip().lower(),
            phone=override["phone"] or "",
            active=bool(override["active"]),
            registration=override["registration"] or "",
            company=override["company"] or "",
            status_validation="CORREÇÃO ADMINISTRATIVA",
            overridden=True,
            override_updated_by=override["updated_by"],
            override_updated_at=override["updated_at"],
        )
        employee["deleted"] = bool(override.get("deleted", 0))
        employee["email_ready"] = bool(EMAIL_PATTERN.fullmatch(employee["email"]))

    for employee in employees:
        employee.setdefault("deleted", False)

    active_email_counts = {}
    for employee in employees:
        if not employee["deleted"] and employee["active"] and employee["email"] and employee["email_ready"]:
            active_email_counts[employee["email"]] = active_email_counts.get(employee["email"], 0) + 1
    for employee in employees:
        employee["duplicate_email"] = (
            employee["active"] and active_email_counts.get(employee["email"], 0) > 1
        )
        if employee["duplicate_email"]:
            employee["email_ready"] = False
    return employees


def list_employees(*, active_only: bool = True) -> list[dict]:
    employees = [employee for employee in _merged_employees() if not employee["deleted"]]
    if active_only:
        employees = [employee for employee in employees if employee["active"]]
    return sorted(employees, key=lambda employee: employee["full_name"].casefold())


def find_employee_by_id(employee_id: int) -> dict | None:
    return next(
        (
            employee for employee in _merged_employees()
            if employee["id"] == employee_id and not employee["deleted"]
        ),
        None,
    )


def find_employee_by_email(
    email: str, *, active_only: bool = True, require_valid_email: bool = True
) -> dict | None:
    normalized = str(email or "").strip().lower()
    if not normalized:
        return None
    matches = [
        employee for employee in _merged_employees()
        if not employee["deleted"] and employee["email"] == normalized
    ]
    if active_only:
        matches = [employee for employee in matches if employee["active"]]
    if require_valid_email:
        matches = [employee for employee in matches if employee["email_ready"]]
    return sorted(matches, key=lambda employee: (not employee["active"], employee["id"]))[0] if matches else None


def readiness_summary() -> dict:
    employees = list_employees(active_only=True)
    return {
        "active": len(employees),
        "ready": sum(1 for employee in employees if employee["email_ready"]),
        "missing_email": sum(1 for employee in employees if not employee["email_ready"]),
    }


def lorac_coverage() -> dict:
    """Audit active RH employees that also have an active Lorac identity."""
    rows = [
        employee for employee in list_employees(active_only=True)
        if employee["sharepoint_active"]
    ]
    pending = [
        {
            "id": row["id"], "full_name": row["full_name"],
            "job_title": row["job_title"],
            "email": row["email"],
            "lorac_upn": row["lorac_upn"],
            "status_validation": row["status_validation"],
        }
        for row in rows
        if not row["email_ready"]
    ]
    total = len(rows)
    ready = total - len(pending)
    return {
        "total": total, "ready": ready, "pending": pending,
        "pending_count": len(pending),
        "coverage_percent": round((ready / total * 100) if total else 100.0, 1),
    }
