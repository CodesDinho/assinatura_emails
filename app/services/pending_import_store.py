"""Armazena cargas de RH pendentes sem alterar a publicação corporativa."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile


def _safe_filename(filename: str) -> str:
    name = Path(filename or "carga").name
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or "carga"


def save_pending_import(
    directory, file_obj, filename, *, uploaded_by: str, row_count: int,
    valid_email_count: int | None = None, missing_email_count: int | None = None,
    status: str = "PENDENTE",
) -> dict:
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    safe_name = _safe_filename(filename)
    target = root / f"{stamp}_{safe_name}"
    descriptor, temporary_name = tempfile.mkstemp(prefix=".pending_", dir=root)
    os.close(descriptor)
    try:
        file_obj.seek(0)
        with open(temporary_name, "wb") as destination:
            shutil.copyfileobj(file_obj, destination)
        temporary = Path(temporary_name)
        digest = hashlib.sha256(temporary.read_bytes()).hexdigest()
        temporary.replace(target)
    finally:
        Path(temporary_name).unlink(missing_ok=True)
    metadata = {
        "filename": safe_name,
        "stored_name": target.name,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
        "uploaded_by": uploaded_by,
        "row_count": int(row_count),
        "sha256": digest,
        "valid_email_count": int(valid_email_count if valid_email_count is not None else row_count),
        "missing_email_count": int(missing_email_count or 0),
        "status": status,
    }
    target.with_suffix(target.suffix + ".json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return metadata


def confirm_pending_import(directory, stored_name: str, *, uploaded_by: str) -> dict:
    """Confirma uma carga validada sem alterar a publicação corporativa."""
    root = Path(directory).resolve()
    safe_name = Path(stored_name or "").name
    if not safe_name or safe_name != stored_name:
        raise ValueError("Carga aguardando confirmação não encontrada.")
    target = (root / safe_name).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("Carga aguardando confirmação não encontrada.") from exc
    metadata_path = target.with_suffix(target.suffix + ".json")
    if not target.is_file() or not metadata_path.is_file():
        raise ValueError("Carga aguardando confirmação não encontrada.")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("status") != "AGUARDANDO_CONFIRMACAO" or metadata.get("uploaded_by") != uploaded_by:
        raise ValueError("Esta carga não pode mais ser confirmada por este usuário.")
    metadata["status"] = "PENDENTE"
    metadata["confirmed_at"] = datetime.now(timezone.utc).isoformat()
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata


def list_pending_imports(directory) -> list[dict]:
    root = Path(directory)
    if not root.exists():
        return []
    entries = []
    for path in root.glob("*.json"):
        try:
            entries.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return sorted(entries, key=lambda item: item.get("uploaded_at", ""), reverse=True)
