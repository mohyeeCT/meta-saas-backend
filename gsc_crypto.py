import os

from cryptography.fernet import Fernet, MultiFernet


_ENV_NAME = "GSC_TOKEN_ENCRYPTION_KEYS"
_VERSION = "v1"


def _keyring() -> MultiFernet:
    key_values = [
        key.strip()
        for key in os.environ.get(_ENV_NAME, "").split(",")
        if key.strip()
    ]
    if not key_values:
        raise RuntimeError(f"{_ENV_NAME} is required")

    try:
        fernets = [Fernet(key.encode("ascii")) for key in key_values]
    except (TypeError, ValueError, UnicodeError):
        raise ValueError("Invalid GSC token encryption key") from None

    return MultiFernet(fernets)


def encrypt_secret(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("Secret value must be a string")
    if not value:
        raise ValueError("Secret plaintext must not be empty")

    token = _keyring().encrypt(value.encode("utf-8")).decode("ascii")
    return f"{_VERSION}:{token}"


def decrypt_secret(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("Secret value must be a string")

    version, separator, token = value.partition(":")
    if separator != ":" or version != _VERSION or not token:
        raise ValueError("Unsupported or malformed encrypted secret version")

    try:
        plaintext = _keyring().decrypt(token.encode("ascii"))
        return plaintext.decode("utf-8")
    except UnicodeError:
        raise ValueError("Malformed encrypted secret") from None

