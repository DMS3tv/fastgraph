import base64
import getpass
import hashlib
import os
import platform
from typing import Optional, Tuple

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


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
    if not isinstance(blob, dict):
        return None
    salt = blob.get("salt")
    token = blob.get("token")
    if not isinstance(salt, str) or not isinstance(token, str):
        return None
    try:
        f = _build_fernet(salt)
        payload = f.decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        return None
    parts = payload.split("\n", 1)
    if len(parts) != 2:
        return None
    return parts[0], parts[1]
