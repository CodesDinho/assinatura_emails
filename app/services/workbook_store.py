import os
import tempfile
import unicodedata
from pathlib import Path

from openpyxl import load_workbook


def _key(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(character for character in text if not unicodedata.combining(character)).strip().lower()


def _safe_excel_text(value):
    text = str(value or "").strip()
    if text.startswith("="):
        return "'" + text
    return text


def ensure_employee_columns(workbook_path):
    """Create the editable administrative columns when they do not exist."""
    path = Path(workbook_path)
    if not path.exists():
        raise FileNotFoundError(f"Planilha não encontrada: {path}")
    workbook = load_workbook(path)
    sheet = workbook.active
    existing = {_key(cell.value) for cell in sheet[1] if cell.value is not None}
    changed = False
    for normalized, label in (("email", "Email"), ("celular", "Celular"), ("ativo", "Ativo")):
        if normalized not in existing:
            sheet.cell(1, sheet.max_column + 1, label)
            changed = True
    if changed:
        workbook.save(path)
    workbook.close()
    return changed


def save_employee_to_workbook(workbook_path, employee, original=None):
    """Update or append an employee while preserving the workbook's existing columns."""
    path = Path(workbook_path)
    if not path.exists():
        raise FileNotFoundError(f"Planilha não encontrada: {path}")

    workbook = load_workbook(path)
    sheet = workbook.active
    headers = {
        _key(cell.value): index
        for index, cell in enumerate(sheet[1], start=1)
        if cell.value is not None
    }
    required = {"nome": "Nome", "cargo": "Cargo", "email": "Email", "celular": "Celular", "ativo": "Ativo"}
    for normalized, label in required.items():
        if normalized not in headers:
            column = sheet.max_column + 1
            sheet.cell(1, column, label)
            headers[normalized] = column

    original = original or {}
    original_name = _key(original.get("full_name"))
    original_email = _key(original.get("email"))
    row_number = None
    for row in range(2, sheet.max_row + 1):
        row_name = _key(sheet.cell(row, headers["nome"]).value)
        row_email = _key(sheet.cell(row, headers["email"]).value)
        if (original_email and row_email == original_email) or (original_name and row_name == original_name):
            row_number = row
            break

    if row_number is None:
        row_number = sheet.max_row + 1

    values = {
        "nome": employee["full_name"],
        "cargo": employee["job_title"],
        "email": employee["email"],
        "celular": employee.get("phone", ""),
        "ativo": "SIM" if employee.get("active", True) else "NÃO",
    }
    for field, value in values.items():
        sheet.cell(row_number, headers[field], _safe_excel_text(value))

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.stem}_", suffix=path.suffix, dir=path.parent)
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        workbook.save(temporary_path)
        os.replace(temporary_path, path)
    finally:
        workbook.close()
        if temporary_path.exists():
            temporary_path.unlink()

    return row_number
