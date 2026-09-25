"""Verify Fernet encrypt/decrypt works with current cryptography version."""

import pytest
from cryptography.fernet import Fernet, InvalidToken


def test_fernet_roundtrip():
    key = Fernet.generate_key()
    f = Fernet(key)

    plaintext = "my-secret-api-key-abc123"
    token = f.encrypt(plaintext.encode("utf-8"))
    decrypted = f.decrypt(token).decode("utf-8")

    assert decrypted == plaintext, f"Roundtrip failed: {decrypted} != {plaintext}"


def test_fernet_invalid_key_fails():
    key = Fernet.generate_key()
    f = Fernet(key)

    token = f.encrypt(b"sensitive-data")

    other = Fernet(Fernet.generate_key())
    with pytest.raises(InvalidToken):
        other.decrypt(token)


def test_fernet_multi_roundtrip():
    key = Fernet.generate_key()
    f = Fernet(key)

    values = [
        "password123",
        "token with spaces and symbols !@#$%",
        "a" * 1000,
        "ñandú con acentos y UTF-8: 日本語",
    ]
    for v in values:
        token = f.encrypt(v.encode("utf-8"))
        assert f.decrypt(token).decode("utf-8") == v


def test_fernet_tampered_token():
    key = Fernet.generate_key()
    f = Fernet(key)

    token = bytearray(f.encrypt(b"secret"))
    token[-1] ^= 0xFF
    with pytest.raises(InvalidToken):
        f.decrypt(bytes(token))
