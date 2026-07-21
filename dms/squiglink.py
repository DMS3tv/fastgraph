import errno
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

import paramiko

from dms.session import SessionData


PHONE_BOOK_REMOTE_PATH = "data/phone_book.json"
DATA_UPLOAD_DIR = "data"


class RemotePhoneBookError(Exception):
    pass


class RemotePhoneBookMissingError(RemotePhoneBookError):
    pass


class RemotePhoneBookInvalidError(RemotePhoneBookError):
    pass


class RemotePhoneBookReadError(Exception):
    """Raised when the remote phone book could not be read for a reason other
    than the file genuinely being missing (network/permission errors, or a
    payload that fails to parse as JSON)."""

    pass


def build_upload_name_stem(session: SessionData, name_modifier: str) -> str:
    base = f"{session.brand.strip()} {session.model.strip()}".strip()
    side = (session.channel_side or "").strip().upper()
    if side not in {"L", "R"}:
        raise ValueError("Channel side must be set to L or R before Squiglink upload.")
    modifier = " ".join(name_modifier.strip().split())
    if modifier.lower().endswith(".txt"):
        modifier = modifier[:-4].strip()
    if not modifier:
        modifier = side
    return f"{base} {modifier}".strip()


def build_phone_book_name_stem(session: SessionData, name_modifier: str) -> str:
    base = f"{session.brand.strip()} {session.model.strip()}".strip()
    modifier = " ".join(name_modifier.strip().split())
    if modifier.lower().endswith(".txt"):
        modifier = modifier[:-4].strip()
    if not modifier:
        return base
    # Phone-book entries should not carry terminal side/unit tags such as L, R, L1, R2.
    modifier = re.sub(r"(?:^|\s+)[LR](?:\d+)?$", "", modifier, flags=re.IGNORECASE).strip()
    if not modifier:
        return base
    return f"{base} {modifier}".strip()


def upload_export_sftp(
    local_path: Path,
    host: str,
    port: int,
    username: str,
    password: str,
    remote_filename: str | None = None,
) -> None:
    """
    Upload exported file to Squiglink endpoint over SFTP.
    Upload exported file to the account-scoped Squiglink data directory.
    """
    transport = paramiko.Transport((host, int(port)))
    try:
        transport.connect(username=username, password=password)
        sftp = paramiko.SFTPClient.from_transport(transport)
        try:
            filename = (remote_filename or local_path.name).strip().split("/")[-1]
            sftp.put(str(local_path), f"{DATA_UPLOAD_DIR}/{filename}")
        finally:
            sftp.close()
    finally:
        transport.close()


def read_remote_phone_book(
    sftp: paramiko.SFTPClient,
    remote_path: str = PHONE_BOOK_REMOTE_PATH,
) -> list[dict[str, Any]]:
    try:
        with sftp.file(remote_path, "r") as f:
            payload = f.read().decode("utf-8")
    except (OSError, paramiko.SSHException) as exc:
        # paramiko surfaces a missing SFTP path as FileNotFoundError, or as a
        # plain OSError/IOError with errno set to ENOENT. Anything else (a
        # dropped connection, permission denied, etc.) is NOT "missing" and
        # must not be treated as an invitation to create a fresh phone book.
        is_missing = isinstance(exc, FileNotFoundError) or getattr(exc, "errno", None) == errno.ENOENT
        if is_missing:
            raise RemotePhoneBookMissingError(f"Remote phone book missing: {remote_path}") from exc
        raise RemotePhoneBookReadError(f"Failed to read remote phone book: {exc}") from exc

    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RemotePhoneBookReadError(f"Remote phone book is invalid JSON: {exc}") from exc

    if not isinstance(parsed, list):
        raise RemotePhoneBookInvalidError("Remote phone book JSON must be a list.")
    return parsed


def merge_phone_book_entry(
    phone_book: list[dict[str, Any]],
    session: SessionData,
    uploaded_name_stem: str,
) -> list[dict[str, Any]]:
    brand_name = session.brand.strip()
    model_name = session.model.strip()
    prefix_value = f"{brand_name} {model_name}".strip()
    brand_key = brand_name.casefold()
    model_key = model_name.casefold()

    brand_bucket: dict[str, Any] | None = None
    for item in phone_book:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if name.casefold() == brand_key:
            brand_bucket = item
            break

    if brand_bucket is None:
        brand_bucket = {"name": brand_name, "phones": []}
        phone_book.append(brand_bucket)

    phones = brand_bucket.get("phones")
    if not isinstance(phones, list):
        phones = []
        brand_bucket["phones"] = phones

    phone_entry: dict[str, Any] | None = None
    for phone in phones:
        if not isinstance(phone, dict):
            continue
        name = str(phone.get("name") or "").strip()
        if name.casefold() == model_key:
            phone_entry = phone
            break

    if phone_entry is None:
        phone_entry = {
            "name": model_name,
            "file": [uploaded_name_stem],
            "reviewScore": "",
            "reviewLink": "",
            "price": "",
            "shopLink": "",
        }
        phones.append(phone_entry)
        return phone_book

    existing_file = phone_entry.get("file")
    if isinstance(existing_file, str):
        files = [existing_file]
    elif isinstance(existing_file, list):
        files = [str(v) for v in existing_file if str(v).strip()]
    else:
        files = []

    if uploaded_name_stem not in files:
        files.append(uploaded_name_stem)
    phone_entry["file"] = files

    if len(files) > 1:
        if "prefix" not in phone_entry or not str(phone_entry.get("prefix") or "").strip():
            phone_entry["prefix"] = prefix_value

    return phone_book


def _remote_temp_path(remote_path: str) -> str:
    """Build a sibling temp-file name for `remote_path` (POSIX-style remote
    paths; do not use pathlib.Path, which applies local/Windows semantics)."""
    suffix = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
    return f"{remote_path}.tmp-{suffix}"


def write_remote_phone_book(
    sftp: paramiko.SFTPClient,
    phone_book: list[dict[str, Any]],
    remote_path: str = PHONE_BOOK_REMOTE_PATH,
) -> None:
    """Write the shared remote phone book without ever truncating the live
    file in place. The payload is written to a temp file in the same remote
    directory and then atomically swapped into place, so a dropped connection
    or interrupted write can never leave `remote_path` half-written."""
    payload = json.dumps(phone_book, indent=4, ensure_ascii=False) + "\n"
    tmp_path = _remote_temp_path(remote_path)
    try:
        with sftp.file(tmp_path, "w") as f:
            f.write(payload.encode("utf-8"))

        try:
            sftp.posix_rename(tmp_path, remote_path)
        except (OSError, paramiko.SSHException):
            # Server doesn't support the POSIX-rename extension (or it failed
            # for some other reason) - fall back to remove-then-rename.
            try:
                sftp.remove(remote_path)
            except FileNotFoundError:
                pass
            sftp.rename(tmp_path, remote_path)
    except Exception:
        try:
            sftp.remove(tmp_path)
        except OSError:
            pass
        raise
