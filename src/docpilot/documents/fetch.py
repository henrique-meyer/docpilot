"""Download a PDF from an http(s) URL. Private hosts are refused."""

import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import httpx

from docpilot.documents.errors import DocumentError

_BLOCKED_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "metadata",
    "metadata.google.internal",
}
_MAX_REDIRECTS = 3


def download_pdf(url: str, max_bytes: int) -> bytes:
    current = url.strip()
    with httpx.Client(timeout=20.0, follow_redirects=False) as client:
        for _ in range(_MAX_REDIRECTS + 1):
            _require_public_http(current)
            try:
                response = client.get(current, headers={"User-Agent": "DocPilot/0.1"})
            except httpx.HTTPError as exc:
                raise DocumentError("download_failed") from exc
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise DocumentError("download_failed")
                current = urljoin(current, location)
                continue
            if response.status_code >= 400:
                raise DocumentError("download_failed")
            if len(response.content) > max_bytes:
                raise DocumentError("too_large")
            return response.content
    raise DocumentError("download_failed")


def _require_public_http(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise DocumentError("bad_url")
    host = parsed.hostname.strip(".").lower()
    if host in _BLOCKED_HOSTS or _is_blocked_ip(host):
        raise DocumentError("bad_url")
    try:
        addresses = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise DocumentError("download_failed") from exc
    for info in addresses:
        address = info[4][0]
        if _is_blocked_ip(address):
            raise DocumentError("bad_url")


def _is_blocked_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )
