from __future__ import annotations

import base64
import hashlib
import hmac
import secrets


_PREFIX = "$cloudphone-scrypt$v=1"
_N = 1 << 14
_R = 8
_P = 1
_DKLEN = 32


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_password(password: str) -> str:
    if not password:
        raise ValueError("password must not be empty")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN
    )
    return f"{_PREFIX}$n={_N}$r={_R}$p={_P}${_encode(salt)}${_encode(digest)}"


def verify_password(password_hash: str, password: str) -> bool:
    if password_hash.startswith("$argon2"):
        try:
            from argon2 import PasswordHasher
            from argon2.exceptions import InvalidHashError, VerifyMismatchError

            return bool(PasswordHasher().verify(password_hash, password))
        except (ImportError, InvalidHashError, VerifyMismatchError, ValueError):
            return False

    try:
        prefix, n_text, r_text, p_text, salt_text, digest_text = password_hash.rsplit("$", 5)
        if prefix != _PREFIX:
            return False
        n = int(n_text.removeprefix("n="))
        r = int(r_text.removeprefix("r="))
        p = int(p_text.removeprefix("p="))
        if (n, r, p) != (_N, _R, _P):
            return False
        salt = _decode(salt_text)
        expected = _decode(digest_text)
        if len(salt) != 16 or len(expected) != _DKLEN:
            return False
        actual = hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=len(expected)
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError, UnicodeError):
        return False
