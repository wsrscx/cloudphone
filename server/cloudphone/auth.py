from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from .db import ChallengeRecord, Database


class AuthenticationError(ValueError):
    pass


def _b64encode(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _b64decode(value: str, field: str, max_bytes: int = 8192) -> bytes:
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise AuthenticationError(f"invalid {field}") from exc
    if len(decoded) > max_bytes:
        raise AuthenticationError(f"{field} is too large")
    return decoded


def canonical_auth_message(client_id: str, challenge_id: str, nonce: str, timestamp_ms: int, public_key_der: bytes) -> bytes:
    fingerprint = hashlib.sha256(public_key_der).hexdigest()
    return f"v1\n{client_id}\n{challenge_id}\n{nonce}\n{timestamp_ms}\n{fingerprint}".encode("utf-8")


@dataclass(frozen=True, slots=True)
class AuthenticatedClient:
    client_id: str
    key_fingerprint: str


class AuthService:
    def __init__(self, db: Database, *, expected_client_id: str, client_psk: bytes, server_secret: bytes, challenge_ttl_seconds: int = 120, token_ttl_seconds: int = 86_400):
        self.db = db
        self.expected_client_id = expected_client_id
        self.client_psk = client_psk
        self.server_secret = server_secret
        self.challenge_ttl_seconds = challenge_ttl_seconds
        self.token_ttl_seconds = token_ttl_seconds

    def issue_challenge(self, client_id: str, now_ms: int | None = None) -> dict[str, object]:
        if not hmac.compare_digest(client_id, self.expected_client_id):
            raise AuthenticationError("unknown client")
        now = int(time.time() * 1000) if now_ms is None else now_ms
        record = ChallengeRecord(
            id=secrets.token_urlsafe(24),
            client_id=client_id,
            nonce=_b64encode(secrets.token_bytes(32)),
            expires_at_ms=now + self.challenge_ttl_seconds * 1000,
        )
        self.db.create_challenge(record)
        return {"challengeId": record.id, "nonce": record.nonce, "expiresAtMs": record.expires_at_ms, "serverTimeMs": now}

    def verify(self, *, client_id: str, challenge_id: str, timestamp_ms: int, hmac_b64: str, public_key_b64: str, key_signature_b64: str, now_ms: int | None = None) -> dict[str, object]:
        now = int(time.time() * 1000) if now_ms is None else now_ms
        if not hmac.compare_digest(client_id, self.expected_client_id):
            raise AuthenticationError("unknown client")
        if abs(now - timestamp_ms) > self.challenge_ttl_seconds * 1000:
            raise AuthenticationError("client clock is outside the allowed window")
        challenge = self.db.consume_challenge(challenge_id, client_id, now)
        if challenge is None:
            raise AuthenticationError("challenge is expired, unknown, or already used")
        public_key_der = _b64decode(public_key_b64, "publicKey")
        provided_hmac = _b64decode(hmac_b64, "hmac", 64)
        signature = _b64decode(key_signature_b64, "keySignature", 256)
        canonical = canonical_auth_message(client_id, challenge.id, challenge.nonce, timestamp_ms, public_key_der)
        expected_hmac = hmac.new(self.client_psk, canonical, hashlib.sha256).digest()
        if not hmac.compare_digest(provided_hmac, expected_hmac):
            raise AuthenticationError("invalid shared-secret proof")
        try:
            public_key = serialization.load_der_public_key(public_key_der)
            if not isinstance(public_key, ec.EllipticCurvePublicKey) or not isinstance(public_key.curve, ec.SECP256R1):
                raise AuthenticationError("publicKey must be a P-256 SubjectPublicKeyInfo value")
            public_key.verify(signature, canonical, ec.ECDSA(hashes.SHA256()))
        except (ValueError, TypeError, InvalidSignature) as exc:
            raise AuthenticationError("invalid device-key proof") from exc
        fingerprint = hashlib.sha256(public_key_der).hexdigest()
        if not self.db.bind_or_verify_client(client_id, public_key_der, fingerprint):
            raise AuthenticationError("this server is bound to another client key")
        expires_at_ms = now + self.token_ttl_seconds * 1000
        return {"accessToken": self._issue_token(client_id, fingerprint, expires_at_ms), "expiresAtMs": expires_at_ms}

    def _issue_token(self, client_id: str, fingerprint: str, expires_at_ms: int) -> str:
        payload = json.dumps(
            {"v": 1, "cid": client_id, "fp": fingerprint, "exp": expires_at_ms, "jti": secrets.token_urlsafe(12)},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        encoded = base64.urlsafe_b64encode(payload).rstrip(b"=")
        signature = hmac.new(self.server_secret, encoded, hashlib.sha256).digest()
        return f"{encoded.decode('ascii')}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode('ascii')}"

    def authenticate_token(self, token: str, now_ms: int | None = None) -> AuthenticatedClient:
        try:
            encoded_text, signature_text = token.split(".", 1)
            encoded = encoded_text.encode("ascii")
            signature = base64.urlsafe_b64decode(signature_text + "=" * (-len(signature_text) % 4))
            expected = hmac.new(self.server_secret, encoded, hashlib.sha256).digest()
            if not hmac.compare_digest(signature, expected):
                raise AuthenticationError("invalid access token")
            payload_raw = base64.urlsafe_b64decode(encoded_text + "=" * (-len(encoded_text) % 4))
            payload = json.loads(payload_raw)
        except (ValueError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
            raise AuthenticationError("invalid access token") from exc
        now = int(time.time() * 1000) if now_ms is None else now_ms
        if payload.get("v") != 1 or payload.get("cid") != self.expected_client_id or not isinstance(payload.get("fp"), str) or int(payload.get("exp", 0)) < now:
            raise AuthenticationError("expired or invalid access token")
        binding = self.db.get_client_binding()
        if not binding or binding["client_id"] != payload["cid"] or binding["fingerprint"] != payload["fp"]:
            raise AuthenticationError("client binding was reset")
        return AuthenticatedClient(payload["cid"], payload["fp"])
