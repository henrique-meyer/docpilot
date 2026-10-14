"""Single-password admin session.

The password hash and the cookie signing key come from the environment.
Failed attempts lock the client address out after repeated tries.
"""

import secrets
import time
from dataclasses import dataclass, field

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from starlette.requests import Request
from starlette.responses import Response

from docpilot.config import Settings

COOKIE = "docpilot_session"
_HASHER = PasswordHasher()


@dataclass
class Session:
    subject: str | None
    csrf: str


@dataclass
class LoginGuard:
    max_failures: int
    window_seconds: int
    lock_seconds: int
    _attempts: dict[str, list[float]] = field(default_factory=dict)
    _locked_until: dict[str, float] = field(default_factory=dict)

    def remaining_lock(self, client: str, now: float | None = None) -> float:
        moment = time.time() if now is None else now
        until = self._locked_until.get(client, 0.0)
        if until <= moment:
            return 0.0
        return until - moment

    def record_failure(self, client: str, now: float | None = None) -> None:
        moment = time.time() if now is None else now
        recent = [item for item in self._attempts.get(client, []) if moment - item < self.window_seconds]
        recent.append(moment)
        self._attempts[client] = recent
        if len(recent) >= self.max_failures:
            self._locked_until[client] = moment + self.lock_seconds
            self._attempts[client] = []

    def record_success(self, client: str) -> None:
        self._attempts.pop(client, None)
        self._locked_until.pop(client, None)


def password_matches(password_hash: str, password: str) -> bool:
    try:
        return _HASHER.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def hash_password(password: str) -> str:
    return _HASHER.hash(password)


def read_session(request: Request, settings: Settings) -> Session | None:
    if not settings.session_secret:
        return None
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    try:
        raw = _serializer(settings).loads(token, max_age=settings.session_max_age)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(raw, dict):
        return None
    csrf = raw.get("csrf")
    subject = raw.get("sub")
    if not isinstance(csrf, str) or not csrf:
        return None
    if subject is not None and not isinstance(subject, str):
        return None
    return Session(subject=subject, csrf=csrf)


def write_session(response: Response, settings: Settings, subject: str | None) -> str:
    csrf = secrets.token_urlsafe(32)
    token = _serializer(settings).dumps({"sub": subject, "csrf": csrf})
    response.set_cookie(
        COOKIE,
        token,
        max_age=settings.session_max_age,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    return csrf


def clear_session(response: Response, settings: Settings) -> None:
    response.delete_cookie(COOKIE, path="/", secure=settings.cookie_secure, httponly=True, samesite="lax")


def client_address(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        first = forwarded.split(",", 1)[0].strip()
        if first:
            return first
    if request.client is None:
        return "unknown"
    return request.client.host


def csrf_matches(session: Session | None, provided: str) -> bool:
    if session is None or not provided:
        return False
    return secrets.compare_digest(session.csrf, provided)


def _serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret, salt="docpilot-admin-session")
