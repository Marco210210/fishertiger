"""Persistent, password-hashed accounts for the self-hosted web application."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


USERNAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{2,31}\Z")
PBKDF2_ITERATIONS = 310_000
MIN_PASSWORD_LENGTH = 10
MAX_USERS = 20


class AccountError(ValueError):
    """A safe validation error that can be returned to the browser."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _password_record(password: str) -> dict[str, Any]:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS
    )
    return {
        "algorithm": "pbkdf2_sha256",
        "iterations": PBKDF2_ITERATIONS,
        "salt": base64.b64encode(salt).decode("ascii"),
        "hash": base64.b64encode(digest).decode("ascii"),
    }


def _password_matches(password: str, record: dict[str, Any]) -> bool:
    try:
        if record.get("algorithm") != "pbkdf2_sha256":
            return False
        iterations = int(record["iterations"])
        salt = base64.b64decode(record["salt"], validate=True)
        expected = base64.b64decode(record["hash"], validate=True)
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, iterations
        )
    except (KeyError, TypeError, ValueError):
        return False
    return hmac.compare_digest(actual, expected)


def _validate_username(username: Any) -> str:
    if not isinstance(username, str) or not USERNAME.fullmatch(username):
        raise AccountError(
            "invalid_username",
            "Il nome utente deve avere 3-32 caratteri: lettere, numeri, punto, trattino o underscore.",
        )
    return username


def _validate_password(password: Any) -> str:
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        raise AccountError(
            "weak_password",
            f"La password deve contenere almeno {MIN_PASSWORD_LENGTH} caratteri.",
        )
    if len(password) > 256:
        raise AccountError("invalid_password", "La password e troppo lunga.")
    return password


class CredentialStore:
    """Thread-safe JSON credential store; plaintext passwords never touch disk."""

    def __init__(
        self,
        path: Path | str,
        *,
        bootstrap_username: str | None = None,
        bootstrap_password: str | None = None,
    ) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        if self.path.exists():
            self._read()
            return
        if not bootstrap_username or not bootstrap_password:
            raise ValueError(
                "A new credential store requires bootstrap_username and bootstrap_password"
            )
        username = _validate_username(bootstrap_username)
        password = _validate_password(bootstrap_password)
        self._write(
            {
                "version": 1,
                "users": {
                    username: {
                        "admin": True,
                        "created_at": _now(),
                        "password": _password_record(password),
                    }
                },
            }
        )

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"Credential file is invalid: {error}") from error
        if data.get("version") != 1 or not isinstance(data.get("users"), dict):
            raise ValueError("Credential file has an unsupported structure")
        return data

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", dir=self.path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def authenticate(self, username: str, password: str) -> dict[str, Any] | None:
        with self._lock:
            user = self._read()["users"].get(username)
            if not isinstance(user, dict) or not _password_matches(
                password, user.get("password", {})
            ):
                return None
            return {"username": username, "is_admin": bool(user.get("admin"))}

    def list_users(self) -> list[dict[str, Any]]:
        with self._lock:
            users = self._read()["users"]
            return [
                {
                    "username": username,
                    "is_admin": bool(user.get("admin")),
                    "created_at": user.get("created_at"),
                }
                for username, user in sorted(users.items(), key=lambda pair: pair[0].lower())
            ]

    def create_user(self, username: Any, password: Any) -> dict[str, Any]:
        username = _validate_username(username)
        password = _validate_password(password)
        with self._lock:
            data = self._read()
            users = data["users"]
            if username in users:
                raise AccountError("user_exists", "Esiste gia un utente con questo nome.")
            if len(users) >= MAX_USERS:
                raise AccountError("too_many_users", "E stato raggiunto il limite di utenti.")
            created_at = _now()
            users[username] = {
                "admin": False,
                "created_at": created_at,
                "password": _password_record(password),
            }
            self._write(data)
        return {"username": username, "is_admin": False, "created_at": created_at}

    def change_password(self, username: str, current: Any, new: Any) -> None:
        current = _validate_password(current)
        new = _validate_password(new)
        with self._lock:
            data = self._read()
            user = data["users"].get(username)
            if not isinstance(user, dict) or not _password_matches(
                current, user.get("password", {})
            ):
                raise AccountError("wrong_password", "La password attuale non e corretta.")
            if hmac.compare_digest(current, new):
                raise AccountError("same_password", "Scegli una password diversa da quella attuale.")
            user["password"] = _password_record(new)
            user["password_changed_at"] = _now()
            self._write(data)

    def delete_user(self, username: Any, *, requested_by: str) -> None:
        username = _validate_username(username)
        if username == requested_by:
            raise AccountError("cannot_delete_self", "Non puoi eliminare il tuo account.")
        with self._lock:
            data = self._read()
            if username not in data["users"]:
                raise AccountError("user_not_found", "Utente non trovato.")
            del data["users"][username]
            self._write(data)
