import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path


_LOG_LOCK = threading.Lock()


def append_request_log(log_path, email, status, details=""):
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "normalized_email": str(email or "").strip().lower(),
        "request_id": str(uuid.uuid4()),
        "status": status,
        "details": details,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    with _LOG_LOCK, path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def recent_request_logs(log_path, limit=15):
    path = Path(log_path)
    if not path.exists():
        return []
    with _LOG_LOCK, path.open("r", encoding="utf-8") as stream:
        records = []
        for line in stream:
            try:
                records.append(json.loads(line))
            except (ValueError, TypeError):
                continue
    return list(reversed(records[-limit:]))


def successful_request_emails(log_path):
    """Return normalized e-mails that completed signature generation/sending."""
    path = Path(log_path)
    if not path.exists():
        return set()
    emails = set()
    with _LOG_LOCK, path.open("r", encoding="utf-8") as stream:
        for line in stream:
            try:
                record = json.loads(line)
            except (ValueError, TypeError):
                continue
            if record.get("status") == "sent" and record.get("normalized_email"):
                emails.add(str(record["normalized_email"]).strip().lower())
    return emails
