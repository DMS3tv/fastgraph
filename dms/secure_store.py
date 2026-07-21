"""Encrypt/decrypt locally-stored Squiglink credentials (dms.settings_manager
persists the result inside settings.json as squiglink_credentials_encrypted).

Threat model
------------
The encryption key is derived (via PBKDF2HMAC, see `_build_fernet`) from the
machine's hostname and the current OS username (`_machine_secret`) - not from
a user-supplied passphrase, and not from an OS-level secret store.

- What this protects against: settings.json being copied off the machine (or
  read by a different OS user account on the same machine) and the
  credentials inside it being recovered. A copy taken elsewhere cannot
  reproduce the same hostname+username pair and therefore cannot derive the
  key.
- What this does NOT protect against: any other process running as the same
  OS user on the same machine. Such a process can call `_machine_secret()`
  (or simply reproduce its inputs) and derive the identical key, so this is
  not a sandboxing or privilege boundary - it only guards against casual
  exfiltration of the settings file itself.
- OS keychain integration (Keychain on macOS, Credential Manager on Windows,
  Secret Service/libsecret on Linux) was deliberately not used here, to avoid
  the added per-platform dependency and packaging complexity across the
  app's build targets (see build_macos.sh / build_windows.ps1). This is a
  conscious trade-off, not an oversight.
"""

import base64
import getpass
import hashlib
import logging
import os
import platform
from typing import Optional, Tuple

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = logging.getLogger(__name__)


class CredentialDecryptionError(Exception):
    """Raised when a stored credential blob is present and structurally
    well-formed but could not actually be decrypted (or the decrypted
    payload was malformed) - e.g. because it was encrypted on a different
    machine or under a different OS username (see module docstring), or the
    stored value was corrupted/tampered with.

    This is distinct from `decrypt_credentials` returning None, which means
    nothing was stored in the first place - callers should treat "nothing
    stored" (silently leave fields blank) and "could not decrypt what was
    stored" (tell the user their saved credentials are unusable) differently.
    """


def _machine_secret() -> bytes:
    node = platform.node() or ""
    user = getpass.getuser() or ""
    raw = f"DMSFastgraph|{user}|{node}".encode("utf-8")
    return hashlib.sha256(raw).digest()


def _build_fernet(salt_b64: str) -> Fernet:
    salt = base64.urlsafe_b64decode(salt_b64.encode("ascii"))
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=250_000,
    )
    key = base64.urlsafe_b64encode(kdf.derive(_machine_secret()))
    return Fernet(key)


def encrypt_credentials(username: str, password: str) -> dict:
    salt = base64.urlsafe_b64encode(os.urandom(16)).decode("ascii")
    f = _build_fernet(salt)
    payload = f"{username}\n{password}".encode("utf-8")
    token = f.encrypt(payload).decode("ascii")
    return {"salt": salt, "token": token}


def decrypt_credentials(blob: Optional[dict]) -> Optional[Tuple[str, str]]:
    """Decrypt a previously-`encrypt_credentials`-produced blob.

    Returns None when there is nothing usable to decrypt (blob is absent, or
    not the expected shape). Raises `CredentialDecryptionError` when a
    well-formed blob is present but decryption (or parsing the decrypted
    payload) actually fails - this is a distinguishable outcome from "nothing
    stored" so callers can tell the user their saved credentials need to be
    re-entered instead of silently treating it as "no credentials saved yet".
    """
    if not isinstance(blob, dict):
        return None
    salt = blob.get("salt")
    token = blob.get("token")
    if not isinstance(salt, str) or not isinstance(token, str):
        return None
    try:
        f = _build_fernet(salt)
        payload = f.decrypt(token.encode("ascii")).decode("utf-8")
        parts = payload.split("\n", 1)
        if len(parts) != 2:
            raise ValueError("Decrypted credential payload has an unexpected shape.")
    except (InvalidToken, ValueError, TypeError) as exc:
        logger.warning("Stored Squiglink credentials could not be decrypted: %s", exc)
        raise CredentialDecryptionError(
            "Stored Squiglink credentials could not be decrypted (machine or "
            "username changed?)."
        ) from exc
    return parts[0], parts[1]
