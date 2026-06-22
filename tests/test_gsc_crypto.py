import os
import unittest

from cryptography.fernet import Fernet, InvalidToken

from gsc_crypto import decrypt_secret, encrypt_secret


class GscCryptoTests(unittest.TestCase):
    ENV_NAME = "GSC_TOKEN_ENCRYPTION_KEYS"

    def setUp(self):
        self.original_keys = os.environ.get(self.ENV_NAME)

    def tearDown(self):
        if self.original_keys is None:
            os.environ.pop(self.ENV_NAME, None)
        else:
            os.environ[self.ENV_NAME] = self.original_keys

    def _set_keys(self, *keys):
        os.environ[self.ENV_NAME] = ", ".join(
            key.decode("ascii") if isinstance(key, bytes) else key
            for key in keys
        )

    def test_round_trip_encryption_uses_versioned_ciphertext(self):
        self._set_keys(Fernet.generate_key())
        ciphertext = encrypt_secret("refresh-token")
        self.assertTrue(ciphertext.startswith("v1:"))
        self.assertEqual(decrypt_secret(ciphertext), "refresh-token")

    def test_ciphertext_never_contains_plaintext(self):
        self._set_keys(Fernet.generate_key())
        plaintext = "distinct-sensitive-refresh-token"
        self.assertNotIn(plaintext, encrypt_secret(plaintext))

    def test_decrypt_supports_rotation_with_old_key_later_in_ring(self):
        old_key = Fernet.generate_key()
        new_key = Fernet.generate_key()
        self._set_keys(old_key)
        old_ciphertext = encrypt_secret("old-token")
        self._set_keys(new_key, old_key)
        self.assertEqual(decrypt_secret(old_ciphertext), "old-token")

    def test_encryption_after_rotation_uses_newest_key(self):
        old_key = Fernet.generate_key()
        new_key = Fernet.generate_key()
        self._set_keys(new_key, old_key)
        new_ciphertext = encrypt_secret("new-token")
        self._set_keys(old_key)
        with self.assertRaises(InvalidToken):
            decrypt_secret(new_ciphertext)
        self._set_keys(new_key, old_key)
        self.assertEqual(decrypt_secret(new_ciphertext), "new-token")

    def test_missing_encryption_keys_raises_runtime_error(self):
        os.environ.pop(self.ENV_NAME, None)
        with self.assertRaises(RuntimeError):
            encrypt_secret("refresh-token")

    def test_whitespace_and_commas_only_key_ring_raises_runtime_error(self):
        os.environ[self.ENV_NAME] = "  , ,\t,  "
        with self.assertRaises(RuntimeError):
            encrypt_secret("refresh-token")

    def test_empty_plaintext_encryption_raises_value_error(self):
        self._set_keys(Fernet.generate_key())
        with self.assertRaises(ValueError):
            encrypt_secret("")

    def test_encrypt_rejects_non_string_inputs_without_leaking_values(self):
        self._set_keys(Fernet.generate_key())
        for value in (123456789, None, b"sensitive-bytes"):
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(TypeError) as error:
                    encrypt_secret(value)
                self.assertEqual(str(error.exception), "Secret value must be a string")

    def test_decrypt_rejects_non_string_inputs_without_leaking_values(self):
        self._set_keys(Fernet.generate_key())
        for value in (123456789, None, b"sensitive-bytes"):
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(TypeError) as error:
                    decrypt_secret(value)
                self.assertEqual(str(error.exception), "Secret value must be a string")

    def test_unsupported_or_malformed_version_raises_value_error(self):
        self._set_keys(Fernet.generate_key())
        for ciphertext in ("v2:not-supported", "missing-version-separator", "v1:"):
            with self.subTest(ciphertext=ciphertext):
                with self.assertRaises(ValueError):
                    decrypt_secret(ciphertext)

    def test_non_ascii_malformed_ciphertext_raises_safe_value_error(self):
        self._set_keys(Fernet.generate_key())
        with self.assertRaises(ValueError) as error:
            decrypt_secret("v1:\N{SNOWMAN}")
        self.assertEqual(str(error.exception), "Malformed encrypted secret")

    def test_invalid_key_material_raises_safe_exception(self):
        invalid_key = "not-valid-fernet-key-material"
        self._set_keys(invalid_key)
        with self.assertRaises(ValueError) as error:
            encrypt_secret("refresh-token")
        self.assertNotIn(invalid_key, str(error.exception))

    def test_ciphertext_from_unknown_key_raises_invalid_token(self):
        known_key = Fernet.generate_key()
        unknown_key = Fernet.generate_key()
        unknown_token = Fernet(unknown_key).encrypt(b"refresh-token").decode("ascii")
        self._set_keys(known_key)
        with self.assertRaises(InvalidToken):
            decrypt_secret(f"v1:{unknown_token}")


if __name__ == "__main__":
    unittest.main()
