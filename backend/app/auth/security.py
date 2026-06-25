import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass


SESSION_TTL_SECONDS = 8 * 60 * 60


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return f"pbkdf2_sha256${base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(digest).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, salt_b64, digest_b64 = encoded.split("$", 2)
        if scheme != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt_b64.encode())
        expected = base64.urlsafe_b64decode(digest_b64.encode())
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


@dataclass(frozen=True)
class SessionPrincipal:
    user_id: str
    username: str
    roles: list[str]


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode((data + padding).encode("ascii"))


def create_session_cookie(principal: SessionPrincipal, secret_key: str) -> str:
    payload = {
        "user_id": principal.user_id,
        "username": principal.username,
        "roles": principal.roles,
        "exp": int(time.time()) + SESSION_TTL_SECONDS,
    }
    payload_b64 = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(secret_key.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256).digest()
    return f"{payload_b64}.{_b64(sig)}"


def read_session_cookie(cookie_value: str | None, secret_key: str) -> SessionPrincipal | None:
    if not cookie_value or "." not in cookie_value:
        return None
    payload_b64, sig_b64 = cookie_value.split(".", 1)
    expected = hmac.new(secret_key.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256).digest()
    try:
        actual = _unb64(sig_b64)
    except Exception:
        return None
    if not hmac.compare_digest(actual, expected):
        return None
    try:
        payload = json.loads(_unb64(payload_b64).decode("utf-8"))
    except Exception:
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    return SessionPrincipal(
        user_id=str(payload["user_id"]),
        username=str(payload["username"]),
        roles=list(payload.get("roles", [])),
    )
