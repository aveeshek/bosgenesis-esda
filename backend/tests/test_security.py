from backend.app.config import Settings
from backend.app.auth.security import (
    SessionPrincipal,
    create_session_cookie,
    hash_password,
    read_session_cookie,
    verify_password,
)


def test_password_hash_roundtrip() -> None:
    encoded = hash_password("secret")
    assert verify_password("secret", encoded)
    assert not verify_password("wrong", encoded)


def test_session_cookie_roundtrip() -> None:
    principal = SessionPrincipal(user_id="usr_1", username="admin", roles=["admin"])
    cookie = create_session_cookie(principal, "test-secret")
    parsed = read_session_cookie(cookie, "test-secret")
    assert parsed is not None
    assert parsed.username == "admin"
    assert parsed.roles == ["admin"]


def test_session_cookie_rejects_bad_signature() -> None:
    principal = SessionPrincipal(user_id="usr_1", username="admin", roles=["admin"])
    cookie = create_session_cookie(principal, "test-secret")
    assert read_session_cookie(cookie, "other-secret") is None


def test_settings_redacted_summary_masks_url_passwords() -> None:
    settings = Settings(
        database_url="postgresql+psycopg://postgres:super-secret@10.0.0.1:5432/esda",
        secret_key="real-secret",
        azure_openai_api_key="real-key",
    )
    summary = settings.redacted_summary()

    assert summary["secret_key"] == "***"
    assert summary["azure_openai_api_key"] == "***"
    assert "super-secret" not in summary["database_url"]
    assert summary["database_url"] == "postgresql+psycopg://postgres:***@10.0.0.1:5432/esda"