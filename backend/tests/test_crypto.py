"""Credential storage and transactional migration invariants."""
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text

from app.crypto import CredentialEncryptionError, decrypt_dsn, encrypt_dsn, rewrite_credentials


def key():
    return Fernet.generate_key().decode()


def database(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'meta.sqlite').as_posix()}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE connections (id INTEGER PRIMARY KEY, dsn TEXT NOT NULL)"))
    return engine


def test_round_trip_and_wrong_key():
    original = "postgresql://user:secret@localhost/db"
    k = key()
    stored = encrypt_dsn(original, k)
    assert stored.startswith("enc:v1:") and original not in stored
    assert decrypt_dsn(stored, k) == original
    with pytest.raises(CredentialEncryptionError, match="restore"):
        decrypt_dsn(stored, key())
    with pytest.raises(CredentialEncryptionError, match="Unsupported"):
        decrypt_dsn("enc:v2:garbage", k)


def test_upgrade_is_atomic_idempotent_and_rotation(tmp_path):
    engine = database(tmp_path)
    old, new = key(), key()
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO connections VALUES (1, :dsn)"), {"dsn": "sqlite:///legacy"})
        conn.execute(text("INSERT INTO connections VALUES (2, :dsn)"), {"dsn": encrypt_dsn("sqlite:///other", old)})
    assert rewrite_credentials(engine, old, old) == 1
    assert rewrite_credentials(engine, old, old) == 0
    with engine.connect() as conn:
        before = conn.execute(text("SELECT dsn FROM connections ORDER BY id")).scalars().all()
    with pytest.raises(CredentialEncryptionError):
        rewrite_credentials(engine, key(), new)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT dsn FROM connections ORDER BY id")).scalars().all() == before
    assert rewrite_credentials(engine, old, new) == 2
    with engine.connect() as conn:
        after = conn.execute(text("SELECT dsn FROM connections ORDER BY id")).scalars().all()
    assert [decrypt_dsn(v, new) for v in after] == ["sqlite:///legacy", "sqlite:///other"]


def test_api_stores_ciphertext_and_masks_credentials(client, admin_headers):
    from app.db import session_factory
    from app.models import Connection

    dsn = "postgresql://alice:secret@localhost/test?password=othersecret"
    response = client.post(
        "/api/v1/connections", json={"name": "encrypted-storage-test", "dsn": dsn},
        headers=admin_headers,
    )
    assert response.status_code == 201
    assert "secret" not in response.text
    assert "****" in response.json()["dsn_masked"]
    with session_factory()() as db:
        stored = db.get(Connection, response.json()["id"]).dsn
        assert stored.startswith("enc:v1:")
        assert "secret" not in stored
        assert decrypt_dsn(stored) == dsn


def test_prod_requires_key():
    from pydantic import ValidationError

    from app.config import Settings

    with pytest.raises(ValidationError, match="DQ_ENCRYPTION_KEY"):
        Settings(env="prod", secret_key="x" * 40, bootstrap_admin_password="strong",
                 encryption_key="", _env_file=None)


def test_concurrent_startup_upgrades_once(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    engine = database(tmp_path)
    k = key()
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO connections VALUES (1, 'sqlite:///legacy')"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(rewrite_credentials, engine, k, k) for _ in range(2)]
        assert sorted(f.result() for f in futures) == [0, 1]
    with engine.connect() as conn:
        assert decrypt_dsn(conn.execute(text("SELECT dsn FROM connections")).scalar_one(), k) == "sqlite:///legacy"


def test_bad_ciphertext_never_rewrites_legacy_rows(tmp_path):
    engine = database(tmp_path)
    k = key()
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO connections VALUES (1, 'sqlite:///legacy')"))
        conn.execute(text("INSERT INTO connections VALUES (2, 'enc:v1:broken')"))
    with pytest.raises(CredentialEncryptionError):
        rewrite_credentials(engine, k, k)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT dsn FROM connections WHERE id=1")).scalar_one() == "sqlite:///legacy"


def test_invalid_dsn_does_not_echo_input(client, admin_headers):
    secret = "unique-invalid-secret"
    response = client.post("/api/v1/connections",
                           json={"name": "invalid-encryption-test", "dsn": secret},
                           headers=admin_headers)
    assert response.status_code == 400
    assert secret not in response.text


@pytest.mark.parametrize("password", ["dummy-password?tail", "dummy-password#tail",
                                      "dummy-password@tail", "dummy-password:tail",
                                      "dummy-password%40tail"])
def test_mask_dsn_never_displays_password_or_query(password):
    from app.api.serialize import mask_dsn

    result = mask_dsn(f"postgresql://alice:{password}@localhost/test?password=query-secret")
    assert "dummy-password" not in result
    assert "query-secret" not in result
    assert "fragment-secret" not in mask_dsn(
        "postgresql://alice:pass@localhost/test#fragment-secret"
    )
    assert "***" in result or result == "[invalid connection URL]"


def test_mask_dsn_malformed_fails_closed():
    from app.api.serialize import mask_dsn

    assert mask_dsn("bad secret") == "[invalid connection URL]"


@pytest.mark.parametrize("env", ["prod", "misspelled"])
def test_settings_validation_does_not_render_secrets(env):
    from pydantic import ValidationError

    from app.config import Settings

    secret = key()
    with pytest.raises(ValidationError) as exc:
        Settings(env=env, encryption_key=secret, secret_key="insecure", _env_file=None)
    rendered = str(exc.value)
    assert secret not in rendered
    assert "input_value" not in rendered
    assert secret not in str(exc.value.errors(include_input=False))


def test_ambiguous_raw_at_in_password_fails_closed():
    from app.api.serialize import mask_dsn

    result = mask_dsn(
        "postgresql://alice:dummy-password@sensitive-password-tail@localhost/test"
        "?password=query-secret"
    )
    assert result == "[invalid connection URL]"
    assert "dummy-password" not in result
    assert "sensitive-password-tail" not in result
    encoded = mask_dsn("postgresql://alice:dummy-password%40tail@localhost/test")
    assert "dummy-password" not in encoded
    assert "tail" not in encoded
    assert "****" in encoded
