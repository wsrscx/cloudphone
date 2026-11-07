from __future__ import annotations

import base64
import hashlib
import hmac
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from cloudphone.auth import AuthService, AuthenticationError, canonical_auth_message
from cloudphone.db import Database


class AuthServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temporary.name) / "test.db")
        self.psk = bytes(range(32))
        self.service = AuthService(
            self.db,
            expected_client_id="only-client",
            client_psk=self.psk,
            server_secret=b"s" * 32,
            challenge_ttl_seconds=120,
            token_ttl_seconds=3600,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _key_material() -> tuple[ec.EllipticCurvePrivateKey, bytes]:
        private = ec.generate_private_key(ec.SECP256R1())
        public_der = private.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        return private, public_der

    def _verify(self, private: ec.EllipticCurvePrivateKey, public_der: bytes, now: int = 1_000_000) -> dict[str, object]:
        challenge = self.service.issue_challenge("only-client", now)
        canonical = canonical_auth_message(
            "only-client", str(challenge["challengeId"]), str(challenge["nonce"]), now, public_der
        )
        return self.service.verify(
            client_id="only-client",
            challenge_id=str(challenge["challengeId"]),
            timestamp_ms=now,
            hmac_b64=base64.b64encode(hmac.new(self.psk, canonical, hashlib.sha256).digest()).decode(),
            public_key_b64=base64.b64encode(public_der).decode(),
            key_signature_b64=base64.b64encode(private.sign(canonical, ec.ECDSA(hashes.SHA256()))).decode(),
            now_ms=now,
        )

    def test_hmac_and_p256_binding_issue_token(self) -> None:
        private, public_der = self._key_material()
        response = self._verify(private, public_der)
        authenticated = self.service.authenticate_token(str(response["accessToken"]), now_ms=1_000_001)
        self.assertEqual(authenticated.client_id, "only-client")
        self.assertEqual(authenticated.key_fingerprint, hashlib.sha256(public_der).hexdigest())

    def test_challenge_is_one_time_even_after_bad_proof(self) -> None:
        private, public_der = self._key_material()
        challenge = self.service.issue_challenge("only-client", 1_000_000)
        canonical = canonical_auth_message(
            "only-client", str(challenge["challengeId"]), str(challenge["nonce"]), 1_000_000, public_der
        )
        arguments = dict(
            client_id="only-client",
            challenge_id=str(challenge["challengeId"]),
            timestamp_ms=1_000_000,
            hmac_b64=base64.b64encode(b"x" * 32).decode(),
            public_key_b64=base64.b64encode(public_der).decode(),
            key_signature_b64=base64.b64encode(private.sign(canonical, ec.ECDSA(hashes.SHA256()))).decode(),
            now_ms=1_000_000,
        )
        with self.assertRaises(AuthenticationError):
            self.service.verify(**arguments)
        arguments["hmac_b64"] = base64.b64encode(hmac.new(self.psk, canonical, hashlib.sha256).digest()).decode()
        with self.assertRaises(AuthenticationError):
            self.service.verify(**arguments)

    def test_second_keystore_key_cannot_replace_binding(self) -> None:
        first_private, first_public = self._key_material()
        second_private, second_public = self._key_material()
        self._verify(first_private, first_public)
        with self.assertRaises(AuthenticationError):
            self._verify(second_private, second_public, 1_001_000)

    def test_reset_invalidates_existing_token(self) -> None:
        private, public_der = self._key_material()
        token = str(self._verify(private, public_der)["accessToken"])
        self.db.reset_client_binding()
        with self.assertRaises(AuthenticationError):
            self.service.authenticate_token(token, now_ms=1_000_001)

    def test_rejects_clock_skew_before_expensive_signature_check(self) -> None:
        private, public_der = self._key_material()
        challenge = self.service.issue_challenge("only-client", 1_000_000)
        with self.assertRaises(AuthenticationError):
            self.service.verify(
                client_id="only-client", challenge_id=str(challenge["challengeId"]), timestamp_ms=1,
                hmac_b64="", public_key_b64=base64.b64encode(public_der).decode(), key_signature_b64="",
                now_ms=1_000_000,
            )
