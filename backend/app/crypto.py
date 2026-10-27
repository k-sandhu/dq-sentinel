"""Authenticated encryption of source DSNs; key material stays outside the database."""
from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings

PREFIX = "enc:v1:"

class CredentialEncryptionError(RuntimeError):
    """Safe operator-facing credential configuration error."""

def cipher(key: str) -> Fernet:
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, TypeError, UnicodeError) as exc:
        raise CredentialEncryptionError("DQ_ENCRYPTION_KEY must be a valid Fernet key.") from exc

def encrypt_dsn(value: str, key: str | None = None) -> str:
    key = get_settings().encryption_key if key is None else key
    if not key:
        if not get_settings().allow_plaintext_dsns or get_settings().is_production:
            raise CredentialEncryptionError("Set DQ_ENCRYPTION_KEY to store connection credentials.")
        return value
    return PREFIX + cipher(key).encrypt(value.encode()).decode("ascii")

def decrypt_dsn(value: str, key: str | None = None) -> str:
    if not value.startswith("enc:"):
        return value
    if not value.startswith(PREFIX):
        raise CredentialEncryptionError("Unsupported encrypted DSN version; restore a compatible application.")
    key = get_settings().encryption_key if key is None else key
    if not key:
        raise CredentialEncryptionError("DQ_ENCRYPTION_KEY is missing; restore the original key.")
    try:
        return cipher(key).decrypt(value[len(PREFIX):].encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeError, ValueError) as exc:
        raise CredentialEncryptionError("Cannot decrypt stored credentials; restore the original DQ_ENCRYPTION_KEY.") from exc


def rewrite_credentials(engine, old_key: str, new_key: str) -> int:
    """Authenticate every row then atomically rewrite; serialize competing writers.

    Call with processes stopped when rotating keys. The transaction never logs values.
    """
    from sqlalchemy import text

    if new_key:
        cipher(new_key)
    with engine.connect() as conn:
        if engine.dialect.name == "sqlite":
            conn.exec_driver_sql("BEGIN IMMEDIATE")
        elif engine.dialect.name == "postgresql":
            conn.execute(text("SELECT pg_advisory_xact_lock(1683059531)"))
            conn.execute(text("LOCK TABLE connections IN EXCLUSIVE MODE"))
        else:
            raise CredentialEncryptionError("Unsupported metadata database for credential migration.")
        try:
            rows = conn.execute(text("SELECT id, dsn FROM connections")).all()
            replacements = []
            for row in rows:
                plain = decrypt_dsn(row.dsn, old_key)
                if new_key and (old_key != new_key or not row.dsn.startswith(PREFIX)):
                    replacements.append({"id": row.id, "dsn": encrypt_dsn(plain, new_key)})
            if replacements:
                conn.execute(text("UPDATE connections SET dsn=:dsn WHERE id=:id"), replacements)
            conn.commit()
            return len(replacements)
        except BaseException:
            conn.rollback()
            raise
