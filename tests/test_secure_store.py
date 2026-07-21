import pytest

from dms.secure_store import (
    CredentialDecryptionError,
    decrypt_credentials,
    encrypt_credentials,
)


def test_round_trip_encrypt_decrypt() -> None:
    blob = encrypt_credentials("alice", "hunter2")
    assert decrypt_credentials(blob) == ("alice", "hunter2")


def test_none_input_returns_none() -> None:
    assert decrypt_credentials(None) is None


def test_missing_dict_returns_none() -> None:
    assert decrypt_credentials({}) is None


def test_malformed_shape_returns_none() -> None:
    assert decrypt_credentials({"salt": 123, "token": "abc"}) is None
    assert decrypt_credentials({"salt": "abc"}) is None


def test_tampered_blob_raises_credential_decryption_error() -> None:
    blob = encrypt_credentials("alice", "hunter2")
    tampered = dict(blob)
    # Flip a character in the token so Fernet's HMAC check fails.
    token = tampered["token"]
    flipped_char = "A" if token[0] != "A" else "B"
    tampered["token"] = flipped_char + token[1:]

    with pytest.raises(CredentialDecryptionError):
        decrypt_credentials(tampered)


def test_wrong_salt_raises_credential_decryption_error() -> None:
    blob = encrypt_credentials("alice", "hunter2")
    other_blob = encrypt_credentials("bob", "swordfish")
    mismatched = {"salt": other_blob["salt"], "token": blob["token"]}

    with pytest.raises(CredentialDecryptionError):
        decrypt_credentials(mismatched)
