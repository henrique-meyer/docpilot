"""Runtime settings. Secrets come from the environment, never from git."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from docpilot.analytics import valid_measurement_id

INDEX_LIMIT_BYTES = 50 * 1024 * 1024
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
DEFAULT_CHUNK_TOKENS = 700
DEFAULT_OVERLAP_TOKENS = 80


def load_dotenv(path: Path | None = None) -> None:
    """Fill missing environment variables from a local dotenv file.

    Existing variables win, so a shell or a test can override the file.
    Values are never logged. The file stays out of git.
    ``DOCPILOT_ENV_FILE`` points at a dotenv file outside this checkout.
    Otherwise the file is ``.env`` in the working directory.
    """
    if path is not None:
        env_file = path
    else:
        configured = os.environ.get("DOCPILOT_ENV_FILE", "").strip()
        env_file = Path(configured) if configured else Path.cwd() / ".env"
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


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    return float(raw)


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
    chat_deployment: str = "gpt-6.1-sol"
    max_question_chars: int = 1000
    ask_rate_limit: int = 30
    ask_rate_window_seconds: int = 60
    daily_token_cap: int = 20000
    eval_correctness: float = 0.8
    eval_citation_accuracy: float = 0.9
    eval_groundedness: float = 0.8
    eval_red_team_pass_rate: float = 1.0
    agent_max_tool_calls: int = 6
    agent_timeout_seconds: int = 30
    otlp_endpoint: str = ""
    appinsights_connection_string: str = ""
    otel_sample_ratio: float = 1.0
    ga_measurement_id: str = ""

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
            chat_deployment=os.environ.get("AZURE_OPENAI_CHAT_DEPLOYMENT", "gpt-6.1-sol").strip()
            or "gpt-6.1-sol",
            max_question_chars=_int("DOCPILOT_MAX_QUESTION_CHARS", 1000),
            ask_rate_limit=_int("DOCPILOT_ASK_RATE_LIMIT", 30),
            ask_rate_window_seconds=_int("DOCPILOT_ASK_RATE_WINDOW_SECONDS", 60),
            daily_token_cap=_int("DOCPILOT_DAILY_TOKEN_CAP", 20000),
            eval_correctness=_float("DOCPILOT_EVAL_CORRECTNESS", 0.8),
            eval_citation_accuracy=_float("DOCPILOT_EVAL_CITATION_ACCURACY", 0.9),
            eval_groundedness=_float("DOCPILOT_EVAL_GROUNDEDNESS", 0.8),
            eval_red_team_pass_rate=_float("DOCPILOT_EVAL_RED_TEAM_PASS_RATE", 1.0),
            agent_max_tool_calls=_int("DOCPILOT_AGENT_MAX_TOOL_CALLS", 6),
            agent_timeout_seconds=_int("DOCPILOT_AGENT_TIMEOUT_SECONDS", 30),
            otlp_endpoint=os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip(),
            appinsights_connection_string=os.environ.get(
                "APPLICATIONINSIGHTS_CONNECTION_STRING", ""
            ).strip(),
            otel_sample_ratio=_float("DOCPILOT_OTEL_SAMPLE_RATIO", 1.0),
            ga_measurement_id=valid_measurement_id(
                os.environ.get("DOCPILOT_GA_MEASUREMENT_ID", "")
            ),
        )
