import base64
import contextlib
import hashlib
import json
import logging
import re
import socket
from collections.abc import Callable
from pathlib import Path
from typing import Any

import paramiko

from dms.session import SessionData

logger = logging.getLogger(__name__)

PHONE_BOOK_REMOTE_PATH = "data/phone_book.json"
DATA_UPLOAD_DIR = "data"
DEFAULT_CONNECT_TIMEOUT = 20.0
# (host, port, fingerprint, key_type) -> True to trust and pin this key.
HostKeyConfirmCallback = Callable[[str, int, str, str], bool]


class SquiglinkHostKeyError(Exception):
    """Base class for the trust-on-first-use host-key checks."""


class SquiglinkHostKeyMismatch(SquiglinkHostKeyError):
    """The server presented a different key than the one pinned for this host.

    Raised before any credential leaves the machine.
    """

    def __init__(self, host: str, port: int, expected: str, actual: str) -> None:
        self.host = host
        self.port = int(port)
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"Host key for {host}:{int(port)} does not match the key trusted earlier.\n"
            f"Expected: {expected}\nReceived: {actual}\n"
            "Nothing was sent. This is either a server key rotation or an "
            "interception attempt; verify the fingerprint with Squiglink before "
            "removing the stored key."
        )


class SquiglinkHostKeyUnknown(SquiglinkHostKeyError):
    """The host is not pinned yet and the key was not confirmed."""

    def __init__(self, host: str, port: int, fingerprint: str, key_type: str) -> None:
        self.host = host
        self.port = int(port)
        self.fingerprint = fingerprint
        self.key_type = key_type
        super().__init__(
            f"Host key for {host}:{int(port)} is not trusted yet "
            f"({key_type} {fingerprint}). The connection was closed before "
            "sending credentials."
        )


def host_key_fingerprint(key: Any) -> str:
    """Return ``sha256:<base64>`` for a paramiko public key.

    The digest is the base64 of the SHA-256 over the key's wire encoding, with
    the base64 padding stripped, so the value after the prefix is byte-for-byte
    what ``ssh-keygen -lf`` prints and a user can compare by eye.
    """
    digest = hashlib.sha256(key.asbytes()).digest()
    return "sha256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


def _fingerprints_match(stored: str, actual: str) -> bool:
    def _normalize(value: str) -> str:
        return str(value or "").strip().rstrip("=").casefold()

    return bool(stored) and _normalize(stored) == _normalize(actual)


def host_key_id(host: str, port: int) -> str:
    """Settings key for one endpoint: ``"<host>:<port>"``."""
    return f"{host}:{int(port)}"


def _scrub_secret(text: str, secret: str | None) -> str:
    """Replace every occurrence of ``secret`` in ``text`` with ``***``.

    Some SFTP servers echo the supplied credential back in an error string;
    diagnostics are written to the console log, so the password is removed
    before the message is emitted.
    """
    message = str(text)
    if secret:
        message = message.replace(secret, "***")
    return message


class RemotePhoneBookError(Exception):
    pass


class RemotePhoneBookMissingError(RemotePhoneBookError):
    pass


class RemotePhoneBookInvalidError(RemotePhoneBookError):
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


def _emit_diagnostic(stage: str, **details: Any) -> None:
    logger.log(
        logging.WARNING if stage.endswith("failed") else logging.DEBUG,
        stage.replace("_", " ").capitalize(),
        extra={"source": "squiglink", "details": {"stage": stage, **details}},
    )


def _transport_diagnostics(transport: paramiko.Transport) -> dict[str, Any]:
    result: dict[str, Any] = {
        "server_version": getattr(transport, "remote_version", None),
        "transport_active": bool(getattr(transport, "active", False)),
        "authenticated": bool(transport.is_authenticated())
        if hasattr(transport, "is_authenticated")
        else False,
        "inbound_cipher": getattr(transport, "remote_cipher", None),
        "outbound_cipher": getattr(transport, "local_cipher", None),
    }
    try:
        key = transport.get_remote_server_key()
        result["host_key_type"] = key.get_name()
        result["host_key_bits"] = key.get_bits()
        result["host_key_fingerprint_sha256"] = key.fingerprint
    except Exception:
        pass
    return result


def _verify_host_key(
    transport: paramiko.Transport,
    host: str,
    port: int,
    host_keys: dict[str, str] | None,
    confirm_host_key: HostKeyConfirmCallback | None,
) -> str:
    """Run the trust-on-first-use check and return the server's fingerprint.

    Called after ``start_client()`` and before authentication, so a mismatched
    or unconfirmed key aborts the connection with the password still local.
    """
    key = transport.get_remote_server_key()
    fingerprint = host_key_fingerprint(key)
    key_type = str(key.get_name())
    identifier = host_key_id(host, port)
    stored = str((host_keys or {}).get(identifier) or "")

    if stored:
        if not _fingerprints_match(stored, fingerprint):
            _emit_diagnostic(
                "host_key_mismatch",
                host=host,
                port=int(port),
                key_type=key_type,
                expected_fingerprint=stored,
                actual_fingerprint=fingerprint,
            )
            raise SquiglinkHostKeyMismatch(host, int(port), stored, fingerprint)
        _emit_diagnostic(
            "host_key_verified",
            host=host,
            port=int(port),
            key_type=key_type,
            fingerprint=fingerprint,
            trusted="pinned",
        )
        return fingerprint

    accepted = False
    if confirm_host_key is not None:
        accepted = bool(confirm_host_key(host, int(port), fingerprint, key_type))
    if not accepted:
        _emit_diagnostic(
            "host_key_rejected",
            host=host,
            port=int(port),
            key_type=key_type,
            fingerprint=fingerprint,
        )
        raise SquiglinkHostKeyUnknown(host, int(port), fingerprint, key_type)

    _emit_diagnostic(
        "host_key_verified",
        host=host,
        port=int(port),
        key_type=key_type,
        fingerprint=fingerprint,
        trusted="accepted",
    )
    return fingerprint


def open_sftp_connection(
    host: str,
    port: int,
    username: str,
    password: str,
    host_keys: dict[str, str] | None = None,
    confirm_host_key: HostKeyConfirmCallback | None = None,
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
) -> tuple[paramiko.Transport, paramiko.SFTPClient]:
    """Open an authenticated SFTP channel with a verified host key.

    ``host_keys`` maps ``"host:port"`` to a pinned ``sha256:<base64>``
    fingerprint. An endpoint that is absent from the mapping is offered to
    ``confirm_host_key``; the default of ``None`` rejects every unknown key, so
    a caller that forgets to supply the prompt fails closed. When the callback
    returns True the caller is responsible for persisting the fingerprint it
    was handed.
    """
    _emit_diagnostic(
        "connection_start",
        host=host,
        port=int(port),
        username_length=len(username),
        username_whitespace_trimmed=username != username.strip(),
        paramiko_version=paramiko.__version__,
        authentication_method="password",
        connect_timeout_s=float(connect_timeout),
    )
    # A bare Transport((host, port)) blocks forever on a black-holed route;
    # connecting the socket ourselves bounds the wait.
    sock = socket.create_connection((host, int(port)), timeout=float(connect_timeout))
    try:
        transport = paramiko.Transport(sock)
    except Exception:
        with contextlib.suppress(OSError):
            sock.close()
        raise
    try:
        _emit_diagnostic("transport_created")
        # start_client() completes the key exchange only. Authentication is a
        # separate call below so the host key can be checked in between.
        transport.start_client(timeout=float(connect_timeout))
        _verify_host_key(
            transport,
            host,
            int(port),
            host_keys,
            confirm_host_key,
        )
        transport.auth_password(username, password)
        _emit_diagnostic(
            "authentication_succeeded",
            **_transport_diagnostics(transport),
        )
        sftp = paramiko.SFTPClient.from_transport(transport)
        _emit_diagnostic(
            "sftp_subsystem_opened",
            **_transport_diagnostics(transport),
        )
        return transport, sftp
    except Exception as exc:
        _emit_diagnostic(
            "connection_failed",
            exception_type=f"{type(exc).__module__}.{type(exc).__name__}",
            exception_message=_scrub_secret(exc, password),
            **_transport_diagnostics(transport),
        )
        # Transport.close() also shuts the socket we handed it.
        transport.close()
        raise


def upload_export_sftp(
    local_path: Path,
    host: str,
    port: int,
    username: str,
    password: str,
    remote_filename: str | None = None,
    host_keys: dict[str, str] | None = None,
    confirm_host_key: HostKeyConfirmCallback | None = None,
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
) -> None:
    """
    Upload exported file to Squiglink endpoint over SFTP.
    Upload exported file to the account-scoped Squiglink data directory.
    """
    transport, sftp = open_sftp_connection(
        host=host,
        port=port,
        username=username,
        password=password,
        host_keys=host_keys,
        confirm_host_key=confirm_host_key,
        connect_timeout=connect_timeout,
    )
    try:
        try:
            filename = (remote_filename or local_path.name).strip().split("/")[-1]
            _emit_diagnostic(
                "measurement_upload_start",
                remote_directory=DATA_UPLOAD_DIR,
                filename=filename,
                bytes=local_path.stat().st_size,
            )
            sftp.put(str(local_path), f"{DATA_UPLOAD_DIR}/{filename}")
            _emit_diagnostic("measurement_upload_complete", filename=filename)
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
    except FileNotFoundError as exc:
        raise RemotePhoneBookMissingError(f"Remote phone book missing: {remote_path}") from exc
    except OSError as exc:
        raise RemotePhoneBookMissingError(f"Remote phone book missing: {remote_path}") from exc

    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RemotePhoneBookInvalidError("Remote phone book is invalid JSON.") from exc

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

    if len(files) > 1 and not str(phone_entry.get("prefix") or "").strip():
        phone_entry["prefix"] = prefix_value

    return phone_book


def write_remote_phone_book(
    sftp: paramiko.SFTPClient,
    phone_book: list[dict[str, Any]],
    remote_path: str = PHONE_BOOK_REMOTE_PATH,
) -> None:
    payload = json.dumps(phone_book, indent=4, ensure_ascii=False) + "\n"
    with sftp.file(remote_path, "w") as f:
        f.write(payload.encode("utf-8"))
