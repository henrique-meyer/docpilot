"""Google Analytics 4 tag. The measurement ID is public in the page, and it stays out of git."""

from __future__ import annotations

import re
from contextvars import ContextVar, Token

from markupsafe import Markup

_MEASUREMENT_ID = re.compile(r"^G-[A-Z0-9]{4,20}$")
_current_id: ContextVar[str] = ContextVar("ga_measurement_id", default="")


def valid_measurement_id(raw: str) -> str:
    value = raw.strip()
    if _MEASUREMENT_ID.fullmatch(value):
        return value
    return ""


def bind_measurement_id(raw: str) -> Token[str]:
    return _current_id.set(valid_measurement_id(raw))


def reset_measurement_id(token: Token[str]) -> None:
    _current_id.reset(token)


def analytics_head() -> Markup:
    measurement_id = _current_id.get()
    if not measurement_id:
        return Markup("")
    source = f"https://www.googletagmanager.com/gtag/js?id={measurement_id}"
    return Markup(
        f'<script async src="{source}"></script>\n'
        "<script>\n"
        "  window.dataLayer = window.dataLayer || [];\n"
        "  function gtag(){dataLayer.push(arguments);}\n"
        "  gtag('js', new Date());\n"
        f"  gtag('config', '{measurement_id}');\n"
        "</script>"
    )
