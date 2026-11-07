import unittest

from cloudphone.passwords import hash_password, verify_password


class PasswordHashTests(unittest.TestCase):
    def test_scrypt_password_round_trip(self) -> None:
        password_hash = hash_password("correct-admin-password")

        self.assertTrue(verify_password(password_hash, "correct-admin-password"))
        self.assertFalse(verify_password(password_hash, "wrong-password"))

    def test_invalid_password_hash_is_rejected(self) -> None:
        self.assertFalse(verify_password("not-a-password-hash", "password"))


if __name__ == "__main__":
    unittest.main()
