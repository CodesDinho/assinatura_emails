import hashlib
import hmac
import json
from pathlib import Path


def authenticate_user(users_path, username, password):
    """Authenticate an active user against PBKDF2 hashes stored on disk."""
    path = Path(users_path)
    if not path.exists():
        return None

    users = json.loads(path.read_text(encoding="utf-8")).get("usuarios", [])
    normalized_username = (username or "").strip().lower()
    user = next(
        (
            item
            for item in users
            if item.get("ativo")
            and str(item.get("username", "")).strip().lower() == normalized_username
        ),
        None,
    )
    if not user:
        return None

    try:
        calculated = hashlib.pbkdf2_hmac(
            "sha256",
            (password or "").encode("utf-8"),
            bytes.fromhex(user["salt"]),
            int(user.get("iteracoes", 600_000)),
        )
        valid = hmac.compare_digest(calculated, bytes.fromhex(user["senha_hash"]))
    except (KeyError, TypeError, ValueError):
        return None

    if not valid:
        return None
    return {
        "username": user["username"],
        "name": user.get("nome", user["username"]),
        "role": user.get("perfil", "Usuário"),
    }


def list_active_users(users_path):
    path = Path(users_path)
    if not path.exists():
        return []
    users = json.loads(path.read_text(encoding="utf-8")).get("usuarios", [])
    return [
        {
            "username": item["username"],
            "name": item.get("nome", item["username"]),
            "role": item.get("perfil", "Usuário"),
        }
        for item in users
        if item.get("ativo") and item.get("username")
    ]
