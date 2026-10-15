"""Safe error codes for document management. Details stay on the server."""


class DocumentError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


MESSAGES: dict[str, str] = {
    "not_pdf": "That file is not a PDF.",
    "too_large": "That file is larger than the 25 MB upload limit.",
    "over_capacity": "That upload would pass the 50 MB index limit.",
    "encrypted": "Encrypted PDFs are not supported.",
    "empty": "The file is empty.",
    "bad_url": "That URL is not allowed.",
    "download_failed": "The URL could not be downloaded.",
    "not_found": "That document was not found.",
    "corrupt": "The stored document record could not be read.",
    "csrf": "The form expired. Reload the page and try again.",
    "unauthorized": "Sign in required.",
    "lockout": "Too many sign-in attempts. Try again in a few minutes.",
    "bad_password": "The password is wrong.",
    "not_configured": "Admin login is not configured.",
    "password_short": "Use at least 8 characters.",
    "password_mismatch": "Those passwords do not match.",
    "already_set": "An admin password is already set. Sign in with it.",
}
