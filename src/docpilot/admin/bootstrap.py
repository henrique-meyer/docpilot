"""First-visitor admin password.

Environment values win. Otherwise the first password saved here becomes the
admin password. The password itself is not stored.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
from dataclasses import replace
from pathlib import Path
from typing import Protocol

from docpilot.admin.auth import hash_password
from docpilot.config import Settings

MIN_PASSWORD_CHARS = 8
_BLOB = "admin/login.json"


class LoginStore(Protocol):
    def load(self) -> tuple[str, str] | None: ...

    def create(self, password_hash: str, session_secret: str) -> bool: ...


class FileLoginStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def load(self) -> tuple[str, str] | None:
        path = self.root / "admin-login.json"
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        return _pair(payload)

    def create(self, password_hash: str, session_secret: str) -> bool:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / "admin-login.json"
        body = _dump(password_hash, session_secret)
        try:
            handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        with os.fdopen(handle, "wb") as file:
            file.write(body)
        return True


class BlobLoginStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._container: object | None = None

    def load(self) -> tuple[str, str] | None:
        from azure.core.exceptions import ResourceNotFoundError

        client = self._client()
        try:
            payload = client.download_blob(_BLOB).readall()  # type: ignore[attr-defined]
        except ResourceNotFoundError:
            return None
        if not isinstance(payload, bytes):
            return None
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            return None
        return _pair(parsed)

    def create(self, password_hash: str, session_secret: str) -> bool:
        from azure.core.exceptions import ResourceExistsError
        from azure.storage.blob import ContentSettings

        client = self._client()
        try:
            client.upload_blob(  # type: ignore[attr-defined]
                _BLOB,
                _dump(password_hash, session_secret),
                overwrite=False,
                content_settings=ContentSettings(content_type="application/json"),
            )
        except ResourceExistsError:
            return False
        return True

    def _client(self) -> object:
        if self._container is not None:
            return self._container
        from azure.core.exceptions import ResourceExistsError
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobServiceClient

        if self._settings.storage_connection_string:
            service = BlobServiceClient.from_connection_string(
                self._settings.storage_connection_string
            )
        else:
            service = BlobServiceClient(
                self._settings.storage_account_url,
                credential=DefaultAzureCredential(),
            )
        container = service.get_container_client(self._settings.storage_container)
        try:
            container.create_container()
        except ResourceExistsError:
            pass
        self._container = container
        return container


class AdminLogin:
    def __init__(self, store: LoginStore, settings: Settings) -> None:
        self._store = store
        self._lock = threading.Lock()
        self._pending = ""
        self.settings = settings
        if not settings.admin_configured:
            found = store.load()
            if found is not None:
                self.settings = replace(
                    settings,
                    admin_password_hash=found[0],
                    session_secret=found[1],
                )

    def prepare(self) -> Settings:
        """Return settings that can sign the setup form before a password exists."""
        with self._lock:
            if self.settings.admin_configured:
                return self.settings
            if not self._pending:
                self._pending = secrets.token_urlsafe(32)
            return replace(self.settings, session_secret=self._pending, admin_password_hash="pending")

    def claim(self, password: str) -> bool:
        """Save the password if nobody else has. False means sign in instead."""
        with self._lock:
            self._adopt_stored()
            if self.settings.admin_configured:
                return False
            secret = self._pending or secrets.token_urlsafe(32)
            digest = hash_password(password)
            if not self._store.create(digest, secret):
                self._adopt_stored()
                return False
            self.settings = replace(
                self.settings,
                admin_password_hash=digest,
                session_secret=secret,
            )
            self._pending = ""
            return True

    def _adopt_stored(self) -> None:
        if self.settings.admin_configured:
            return
        found = self._store.load()
        if found is None:
            return
        self.settings = replace(
            self.settings,
            admin_password_hash=found[0],
            session_secret=found[1],
        )


def build_login_store(settings: Settings) -> LoginStore:
    if settings.storage_connection_string or settings.storage_account_url:
        return BlobLoginStore(settings)
    return FileLoginStore(settings.data_dir)


def _pair(payload: object) -> tuple[str, str] | None:
    if not isinstance(payload, dict):
        return None
    password_hash = payload.get("password_hash")
    session_secret = payload.get("session_secret")
    if not isinstance(password_hash, str) or not isinstance(session_secret, str):
        return None
    if not password_hash or not session_secret:
        return None
    return password_hash, session_secret


def _dump(password_hash: str, session_secret: str) -> bytes:
    return json.dumps(
        {"password_hash": password_hash, "session_secret": session_secret}
    ).encode("utf-8")
