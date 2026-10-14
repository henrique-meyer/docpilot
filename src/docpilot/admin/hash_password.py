"""Print an argon2id hash for ADMIN_PASSWORD_HASH. The password is not stored."""

import getpass
import os

from docpilot.admin.auth import hash_password


def main() -> None:
    password = os.environ.get("DOCPILOT_ADMIN_PASSWORD", "")
    if not password:
        password = getpass.getpass("Admin password: ")
    if not password:
        raise SystemExit("A password is required.")
    print(hash_password(password))


if __name__ == "__main__":
    main()
