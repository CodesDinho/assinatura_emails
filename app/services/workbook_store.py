import os
import shutil
import tempfile
import threading
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from openpyxl import Workbook, load_workbook


_WORKBOOK_LOCK = threading.RLock()


def _key(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(character for character in text if not unicodedata.combining(character)).strip().lower()


def _safe_excel_text(value):
    text = str(value or "").strip()
    return "'" + text if text.startswith("=") else text


def _is_active(value):
    return _key(value or "sim") not in {"nao", "no", "n", "0", "false", "inativo"}


def _headers(sheet):
    return {_key(cell.value): index for index, cell in enumerate(sheet[1], start=1) if cell.value is not None}


def _backup(path):
    backup_dir = path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = backup_dir / f"{path.stem}_{stamp}{path.suffix}"
    shutil.copy2(path, target)
    return target


def _atomic_save(workbook, path):
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.stem}_", suffix=path.suffix, dir=path.parent)
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        workbook.save(temporary_path)
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def ensure_employee_columns(workbook_path):
    """Create required columns, with backup and atomic replacement."""
    path = Path(workbook_path)
    if not path.exists():
        raise FileNotFoundError(f"Planilha não encontrada: {path}")
    with _WORKBOOK_LOCK:
        workbook = load_workbook(path)
        try:
            sheet = workbook.active
            existing = _headers(sheet)
            changed = False
            for normalized, label in (("email", "Email"), ("celular", "Celular"), ("ativo", "Ativo")):
                if normalized not in existing:
                    sheet.cell(1, sheet.max_column + 1, label)
                    changed = True
            if changed:
                _backup(path)
                _atomic_save(workbook, path)
            return changed
        finally:
            workbook.close()


def list_employees(workbook_path):
    """Read every named employee directly from the official workbook."""
    path = Path(workbook_path)
    if not path.exists():
        raise FileNotFoundError(f"Planilha não encontrada: {path}")
    with _WORKBOOK_LOCK:
        workbook = load_workbook(path, read_only=True, data_only=True)
        try:
            sheet = workbook.active
            rows = sheet.iter_rows(values_only=True)
            header_values = next(rows, ())
            headers = {_key(value): index for index, value in enumerate(header_values) if value is not None}
            employees = []
            for row_number, row in enumerate(rows, start=2):
                def value(*names):
                    for name in names:
                        column = headers.get(name)
                        if column is not None and column < len(row):
                            result = row[column]
                            if result is not None:
                                return str(result).strip()
                    return ""

                full_name = value("nome", "name", "full_name")
                if not full_name:
                    continue
                employees.append({
                    "id": row_number,
                    "full_name": full_name,
                    "job_title": value("cargo", "cargo oficial", "role", "job_title"),
                    "email": value("email", "e-mail", "e_mail").lower(),
                    "phone": value("celular", "telefone", "telefone celular", "celular corporativo", "phone"),
                    "active": _is_active(value("ativo", "active")),
                })
            return employees
        finally:
            workbook.close()


def find_employee_by_email(workbook_path, email, active_only=True):
    normalized = str(email or "").strip().lower()
    for employee in list_employees(workbook_path):
        if employee["email"] == normalized and (employee["active"] or not active_only):
            return employee
    return None


def get_employee(workbook_path, row_number):
    return next((item for item in list_employees(workbook_path) if item["id"] == row_number), None)


def save_employee_to_workbook(workbook_path, employee, original=None):
    """Update or append an employee, backing up and atomically replacing the workbook."""
    path = Path(workbook_path)
    if not path.exists():
        raise FileNotFoundError(f"Planilha não encontrada: {path}")
    with _WORKBOOK_LOCK:
        workbook = load_workbook(path)
        try:
            sheet = workbook.active
            headers = _headers(sheet)
            required = {"nome": "Nome", "cargo": "Cargo", "email": "Email", "celular": "Celular", "ativo": "Ativo"}
            for normalized, label in required.items():
                if normalized not in headers:
                    column = sheet.max_column + 1
                    sheet.cell(1, column, label)
                    headers[normalized] = column

            original = original or {}
            row_number = original.get("id")
            if not row_number:
                original_name = _key(original.get("full_name"))
                original_email = _key(original.get("email"))
                for row in range(2, sheet.max_row + 1):
                    row_name = _key(sheet.cell(row, headers["nome"]).value)
                    row_email = _key(sheet.cell(row, headers["email"]).value)
                    if (original_email and row_email == original_email) or (original_name and row_name == original_name):
                        row_number = row
                        break
            if not row_number:
                row_number = sheet.max_row + 1

            values = {
                "nome": employee["full_name"], "cargo": employee["job_title"],
                "email": employee["email"], "celular": employee.get("phone", ""),
                "ativo": "SIM" if employee.get("active", True) else "NÃO",
            }
            for field, value in values.items():
                sheet.cell(int(row_number), headers[field], _safe_excel_text(value))

            _backup(path)
            _atomic_save(workbook, path)
            return int(row_number)
        finally:
            workbook.close()


def replace_employee_workbook(workbook_path, employees):
    """Replace the official workbook from a validated import, retaining a backup."""
    path = Path(workbook_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _WORKBOOK_LOCK:
        workbook = Workbook()
        try:
            sheet = workbook.active
            sheet.title = "Colaboradores"
            sheet.append(["Nome", "Cargo", "Email", "Celular", "Ativo"])
            for employee in employees:
                sheet.append([
                    _safe_excel_text(employee["full_name"]), _safe_excel_text(employee["job_title"]),
                    _safe_excel_text(employee["email"]), _safe_excel_text(employee.get("phone", "")),
                    "SIM" if employee.get("active", True) else "NÃO",
                ])
            if path.exists():
                _backup(path)
            _atomic_save(workbook, path)
        finally:
            workbook.close()
