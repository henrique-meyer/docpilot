"""Runtime settings. Secrets come from the environment, never from git."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

INDEX_LIMIT_BYTES = 50 * 1024 * 1024
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
DEFAULT_CHUNK_TOKENS = 700
DEFAULT_OVERLAP_TOKENS = 80


def load_dotenv(path: Path | None = None) -> None:
    """Fill missing environment variables from a local .env file.

    Existing variables win, so a shell or a test can override the file.
    Values are never logged. The file stays out of git.
    """
    env_file = path if path is not None else Path.cwd() / ".env"
    if not env_file.is_file():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    return int(raw)


@dataclass(frozen=True)
class Settings:
    admin_password_hash: str
    session_secret: str
    cookie_secure: bool
    storage_account_url: str
    storage_container: str
    storage_connection_string: str
    data_dir: Path
    max_upload_bytes: int = MAX_UPLOAD_BYTES
    index_limit_bytes: int = INDEX_LIMIT_BYTES
    openai_endpoint: str = ""
    search_endpoint: str = ""
    embedding_deployment: str = "text-embedding-3-small"
    search_index: str = "docpilot"
    chunk_tokens: int = DEFAULT_CHUNK_TOKENS
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS
    session_max_age: int = 8 * 60 * 60
    login_max_failures: int = 5
    login_window_seconds: int = 15 * 60
    login_lock_seconds: int = 15 * 60
    retrieval_top_k: int = 8
    retrieval_token_budget: int = 3000

    @property
    def admin_configured(self) -> bool:
        return bool(self.admin_password_hash and self.session_secret)

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv()
        return cls(
            admin_password_hash=os.environ.get("ADMIN_PASSWORD_HASH", "").strip(),
            session_secret=os.environ.get("SESSION_SECRET", "").strip(),
            cookie_secure=_flag("DOCPILOT_COOKIE_SECURE"),
            storage_account_url=os.environ.get("AZURE_STORAGE_ACCOUNT_URL", "").strip(),
            storage_container=os.environ.get("AZURE_STORAGE_CONTAINER", "sources").strip()
            or "sources",
            storage_connection_string=os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "").strip(),
            data_dir=Path(os.environ.get("DOCPILOT_DATA_DIR", ".data")),
            max_upload_bytes=_int("DOCPILOT_MAX_UPLOAD_BYTES", MAX_UPLOAD_BYTES),
            index_limit_bytes=_int("DOCPILOT_INDEX_LIMIT_BYTES", INDEX_LIMIT_BYTES),
            openai_endpoint=os.environ.get("AZURE_OPENAI_ENDPOINT", "").strip(),
            search_endpoint=os.environ.get("AZURE_SEARCH_ENDPOINT", "").strip(),
            embedding_deployment=os.environ.get(
                "AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-small"
            ).strip()
            or "text-embedding-3-small",
            search_index=os.environ.get("AZURE_SEARCH_INDEX", "docpilot").strip() or "docpilot",
            chunk_tokens=_int("DOCPILOT_CHUNK_TOKENS", DEFAULT_CHUNK_TOKENS),
            overlap_tokens=_int("DOCPILOT_OVERLAP_TOKENS", DEFAULT_OVERLAP_TOKENS),
            retrieval_top_k=_int("DOCPILOT_RETRIEVAL_TOP_K", 8),
            retrieval_token_budget=_int("DOCPILOT_RETRIEVAL_TOKEN_BUDGET", 3000),
        )
