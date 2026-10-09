import hashlib
import hmac
import json
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def _json_users(users_path):
    path = Path(users_path)
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("usuarios", [])


def _connection(users_path, database_path):
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_users (
            username TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT NOT NULL DEFAULT '',
            role TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            salt TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            iterations INTEGER NOT NULL DEFAULT 600000,
            created_at TEXT NOT NULL,
            created_by TEXT NOT NULL
        )
        """
    )
    columns = {row[1] for row in connection.execute("PRAGMA table_info(admin_users)")}
    permissions_added = "is_admin" not in columns or "is_approver" not in columns
    if "is_admin" not in columns:
        connection.execute("ALTER TABLE admin_users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
    if "is_approver" not in columns:
        connection.execute("ALTER TABLE admin_users ADD COLUMN is_approver INTEGER NOT NULL DEFAULT 0")
    now = datetime.now(timezone.utc).isoformat()
    for item in _json_users(users_path):
        connection.execute(
            """
            INSERT OR IGNORE INTO admin_users (
                username, name, role, active, salt, password_hash, iterations,
                created_at, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'configuração inicial')
            """,
            (
                str(item.get("username", "")).strip().lower(),
                item.get("nome", item.get("username", "")),
                item.get("perfil", "Usuário"),
                1 if item.get("ativo") else 0,
                item.get("salt", ""),
                item.get("senha_hash", ""),
                int(item.get("iteracoes", 600_000)),
                now,
            ),
        )
    if permissions_added:
        connection.execute("UPDATE admin_users SET is_admin=1 WHERE lower(role)='administrador'")
        connection.execute(
            "UPDATE admin_users SET is_approver=1 WHERE lower(role) IN ('validador de assinaturas', 'aprovador')"
        )
    connection.commit()
    return connection


def authenticate_user(users_path, username, password, database_path=None):
    """Authenticate an active user against PBKDF2 hashes stored on disk."""
    normalized_username = (username or "").strip().lower()
    if database_path:
        with _connection(users_path, database_path) as connection:
            row = connection.execute(
                "SELECT * FROM admin_users WHERE username = ? AND active = 1",
                (normalized_username,),
            ).fetchone()
        user = dict(row) if row else None
        salt_key, hash_key, iterations_key = "salt", "password_hash", "iterations"
    else:
        user = next(
            (
                item for item in _json_users(users_path)
                if item.get("ativo")
                and str(item.get("username", "")).strip().lower() == normalized_username
            ),
            None,
        )
        salt_key, hash_key, iterations_key = "salt", "senha_hash", "iteracoes"
    if not user:
        return None

    try:
        calculated = hashlib.pbkdf2_hmac(
            "sha256",
            (password or "").encode("utf-8"),
            bytes.fromhex(user[salt_key]),
            int(user.get(iterations_key, 600_000)),
        )
        valid = hmac.compare_digest(calculated, bytes.fromhex(user[hash_key]))
    except (KeyError, TypeError, ValueError):
        return None

    if not valid:
        return None
    return {
        "username": user["username"],
        "name": user.get("name", user.get("nome", user["username"])),
        "role": user.get("role", user.get("perfil", "Usuário")),
        "is_admin": bool(user.get("is_admin", str(user.get("role", "")).lower() == "administrador")),
        "is_approver": bool(user.get("is_approver", str(user.get("role", "")).lower() in {"validador de assinaturas", "aprovador"})),
    }


def list_active_users(users_path, database_path=None):
    if database_path:
        with _connection(users_path, database_path) as connection:
            rows = connection.execute(
                "SELECT username, name, email, role, is_admin, is_approver FROM admin_users WHERE active = 1 ORDER BY name"
            ).fetchall()
        return [dict(row) for row in rows]
    return [
        {"username": item["username"], "name": item.get("nome", item["username"]), "email": "", "role": item.get("perfil", "Usuário")}
        for item in _json_users(users_path) if item.get("ativo") and item.get("username")
    ]


def create_admin_user(users_path, database_path, username, name, email, role, password, created_by,
                      is_admin=False, is_approver=False):
    salt = secrets.token_bytes(32)
    iterations = 600_000
    password_hash = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    now = datetime.now(timezone.utc).isoformat()
    try:
        with _connection(users_path, database_path) as connection:
            connection.execute(
                """
                INSERT INTO admin_users (
                    username, name, email, role, active, salt, password_hash,
                    iterations, created_at, created_by, is_admin, is_approver
                ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)
                """,
                (username, name, email, role, salt.hex(), password_hash.hex(), iterations, now, created_by,
                 1 if is_admin else 0, 1 if is_approver else 0),
            )
    except sqlite3.IntegrityError:
        raise ValueError("Este nome de usuário já existe.") from None
    return {"username": username, "name": name, "email": email, "role": role}


def update_admin_user(users_path, database_path, username, name, email, password,
                      is_admin, is_approver):
    with _connection(users_path, database_path) as connection:
        existing = connection.execute(
            "SELECT * FROM admin_users WHERE username=? AND active=1", (username,)
        ).fetchone()
        if not existing:
            raise ValueError("Usuário não encontrado.")
        if existing["is_admin"] and not is_admin:
            count = connection.execute(
                "SELECT count(*) FROM admin_users WHERE active=1 AND is_admin=1"
            ).fetchone()[0]
            if count <= 1:
                raise ValueError("Não é possível remover a permissão do último administrador.")
        role = "Administrador" if is_admin else ("Validador de assinaturas" if is_approver else "Usuário")
        values = [name, email, role, int(is_admin), int(is_approver)]
        sql = "UPDATE admin_users SET name=?, email=?, role=?, is_admin=?, is_approver=?"
        if password:
            salt = secrets.token_bytes(32)
            password_hash = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600_000)
            sql += ", salt=?, password_hash=?, iterations=600000"
            values.extend([salt.hex(), password_hash.hex()])
        values.append(username)
        connection.execute(sql + " WHERE username=?", values)


def delete_admin_user(users_path, database_path, username):
    with _connection(users_path, database_path) as connection:
        user = connection.execute(
            "SELECT * FROM admin_users WHERE username=? AND active=1", (username,)
        ).fetchone()
        if not user:
            raise ValueError("Usuário não encontrado.")
        if user["is_admin"]:
            count = connection.execute(
                "SELECT count(*) FROM admin_users WHERE active=1 AND is_admin=1"
            ).fetchone()[0]
            if count <= 1:
                raise ValueError("Não é possível excluir o último administrador.")
        connection.execute("UPDATE admin_users SET active=0 WHERE username=?", (username,))


def migrate_legacy_permissions(users_path, database_path, legacy_username, legacy_email):
    """Executa uma única vez a conversão do aprovador exclusivo para permissões cumulativas."""
    with _connection(users_path, database_path) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS admin_migrations (migration_key TEXT PRIMARY KEY)"
        )
        if connection.execute(
            "SELECT 1 FROM admin_migrations WHERE migration_key='multiple_admins_approvers_v1'"
        ).fetchone():
            return
        connection.execute(
            "UPDATE admin_users SET is_admin=1, is_approver=1 WHERE username='roberson.souza'"
        )
        connection.execute(
            "UPDATE admin_users SET is_admin=1, is_approver=1, email=CASE WHEN email='' THEN ? ELSE email END "
            "WHERE username=?",
            (legacy_email, legacy_username),
        )
        connection.execute(
            "INSERT INTO admin_migrations (migration_key) VALUES ('multiple_admins_approvers_v1')"
        )
