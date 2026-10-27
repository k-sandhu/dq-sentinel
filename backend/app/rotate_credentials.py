"""Offline DSN key rotation. Secrets are read only from environment variables."""
import os

from app.crypto import CredentialEncryptionError, rewrite_credentials
from app.db import get_engine


def main() -> None:
    old = os.environ.get("DQ_ENCRYPTION_KEY", "")
    new = os.environ.get("DQ_NEW_ENCRYPTION_KEY", "")
    if not old or not new or old == new:
        raise CredentialEncryptionError("Set distinct DQ_ENCRYPTION_KEY and DQ_NEW_ENCRYPTION_KEY.")
    count = rewrite_credentials(get_engine(), old, new)
    print(f"Rotated {count} connection credentials.")  # noqa: T201 - operator CLI


if __name__ == "__main__":
    main()
