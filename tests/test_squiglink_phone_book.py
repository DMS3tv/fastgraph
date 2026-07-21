import errno
import json

import pytest

from dms.session import SessionData
from dms.squiglink import (
    RemotePhoneBookMissingError,
    RemotePhoneBookReadError,
    build_phone_book_name_stem,
    build_upload_name_stem,
    merge_phone_book_entry,
    read_remote_phone_book,
    upload_export_sftp,
    write_remote_phone_book,
)


def _session(
    brand: str = "Apple",
    model: str = "AirPods Pro 2",
    channel_side: str = "L",
) -> SessionData:
    return SessionData(rig="KB500X", brand=brand, model=model, channel_side=channel_side)


def test_build_upload_name_stem_defaults_modifier_to_channel_side() -> None:
    s = _session()
    assert build_upload_name_stem(s, "") == "Apple AirPods Pro 2 L"


def test_build_upload_name_stem_normalizes_modifier() -> None:
    s = _session()
    assert build_upload_name_stem(s, "small tips L") == "Apple AirPods Pro 2 small tips L"
    assert build_upload_name_stem(s, "  small   tips   L1  ") == "Apple AirPods Pro 2 small tips L1"
    assert build_upload_name_stem(s, "small tips R.txt") == "Apple AirPods Pro 2 small tips R"


def test_build_upload_name_stem_requires_channel_side() -> None:
    try:
        build_upload_name_stem(_session(channel_side=""), "")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "channel side" in str(exc).lower()


def test_build_phone_book_name_stem_omits_side_suffix() -> None:
    s = _session(channel_side="L")
    assert build_phone_book_name_stem(s, "") == "Apple AirPods Pro 2"
    assert build_phone_book_name_stem(s, "L") == "Apple AirPods Pro 2"
    assert build_phone_book_name_stem(s, "R1") == "Apple AirPods Pro 2"
    assert build_phone_book_name_stem(s, "small tips L") == "Apple AirPods Pro 2 small tips"
    assert build_phone_book_name_stem(s, "small tips R2.txt") == "Apple AirPods Pro 2 small tips"


def test_merge_existing_phone_with_string_file_converts_to_list() -> None:
    phone_book = [
        {
            "name": "Apple",
            "phones": [
                {
                    "name": "AirPods Pro 2",
                    "file": "Apple AirPods Pro 2",
                    "reviewScore": "★★★★★",
                    "reviewLink": "x",
                    "price": "$250",
                    "shopLink": "y",
                    "collab": "z",
                }
            ],
        }
    ]

    merge_phone_book_entry(phone_book, _session(), "Apple AirPods Pro 2 small tips")
    phone = phone_book[0]["phones"][0]
    assert phone["file"] == ["Apple AirPods Pro 2", "Apple AirPods Pro 2 small tips"]
    assert phone["prefix"] == "Apple AirPods Pro 2"
    assert phone["reviewScore"] == "★★★★★"
    assert phone["collab"] == "z"


def test_merge_existing_phone_with_list_appends_unique_only() -> None:
    phone_book = [
        {
            "name": "Apple",
            "phones": [
                {
                    "name": "AirPods Pro 2",
                    "file": ["Apple AirPods Pro 2", "Apple AirPods Pro 2 small tips"],
                    "prefix": "Existing Prefix",
                    "reviewScore": "",
                    "reviewLink": "",
                    "price": "",
                    "shopLink": "",
                    "suffix": "demo",
                }
            ],
        }
    ]

    merge_phone_book_entry(phone_book, _session(), "Apple AirPods Pro 2 small tips")
    phone = phone_book[0]["phones"][0]
    assert phone["file"] == ["Apple AirPods Pro 2", "Apple AirPods Pro 2 small tips"]
    assert phone["prefix"] == "Existing Prefix"
    assert phone["suffix"] == "demo"


def test_merge_creates_new_brand_and_phone_minimal_shape() -> None:
    phone_book: list[dict] = []
    merge_phone_book_entry(phone_book, _session("Aful", "Explorer"), "Aful Explorer")

    assert phone_book == [
        {
            "name": "Aful",
            "phones": [
                {
                    "name": "Explorer",
                    "file": ["Aful Explorer"],
                    "reviewScore": "",
                    "reviewLink": "",
                    "price": "",
                    "shopLink": "",
                }
            ],
        }
    ]


def test_merge_matches_brand_and_model_case_and_whitespace_insensitive() -> None:
    phone_book = [
        {
            "name": "  APPLE  ",
            "phones": [
                {
                    "name": " airpods pro 2 ",
                    "file": "Apple AirPods Pro 2",
                    "reviewScore": "",
                    "reviewLink": "",
                    "price": "",
                    "shopLink": "",
                }
            ],
        }
    ]
    merge_phone_book_entry(phone_book, _session(), "Apple AirPods Pro 2 sample 2")
    phone = phone_book[0]["phones"][0]
    assert phone["file"] == ["Apple AirPods Pro 2", "Apple AirPods Pro 2 sample 2"]


def test_upload_export_sftp_targets_data_directory(monkeypatch, tmp_path) -> None:
    local = tmp_path / "local.txt"
    local.write_text("x", encoding="utf-8")
    calls = {}

    class _FakeTransport:
        def __init__(self, _addr):
            pass

        def connect(self, username: str, password: str) -> None:
            assert username == "u"
            assert password == "p"

        def close(self) -> None:
            pass

    class _FakeSFTP:
        def put(self, local_path: str, remote_path: str) -> None:
            calls["local"] = local_path
            calls["remote"] = remote_path

        def close(self) -> None:
            pass

    monkeypatch.setattr("dms.squiglink.paramiko.Transport", _FakeTransport)
    monkeypatch.setattr(
        "dms.squiglink.paramiko.SFTPClient.from_transport",
        lambda _transport: _FakeSFTP(),
    )

    upload_export_sftp(
        local_path=local,
        host="h",
        port=2022,
        username="u",
        password="p",
        remote_filename="Apple AirPods Pro 2 L0.txt",
    )
    assert calls["local"] == str(local)
    assert calls["remote"] == "data/Apple AirPods Pro 2 L0.txt"


# --- fakes for write_remote_phone_book / read_remote_phone_book -----------


class _FakeSFTPWriteFile:
    def __init__(self, store: dict, path: str, fail: bool) -> None:
        self._store = store
        self._path = path
        self._fail = fail
        self._buf = bytearray()

    def __enter__(self) -> "_FakeSFTPWriteFile":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            self._store[self._path] = bytes(self._buf)
        return False

    def write(self, data: bytes) -> None:
        if self._fail:
            raise OSError("simulated write failure")
        self._buf.extend(data)


class _FakeSFTP:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.calls: list[tuple] = []
        self.write_should_fail = False
        self.posix_rename_should_fail = False
        self.rename_should_fail = False

    def file(self, path: str, mode: str) -> _FakeSFTPWriteFile:
        self.calls.append(("file", path, mode))
        return _FakeSFTPWriteFile(self.files, path, fail=self.write_should_fail)

    def posix_rename(self, src: str, dst: str) -> None:
        self.calls.append(("posix_rename", src, dst))
        if self.posix_rename_should_fail:
            raise OSError("posix_rename not supported")
        self.files[dst] = self.files.pop(src)

    def rename(self, src: str, dst: str) -> None:
        self.calls.append(("rename", src, dst))
        if self.rename_should_fail:
            raise OSError("rename failed")
        self.files[dst] = self.files.pop(src)

    def remove(self, path: str) -> None:
        self.calls.append(("remove", path))
        if path not in self.files:
            raise FileNotFoundError(path)
        del self.files[path]


def test_write_remote_phone_book_writes_temp_then_posix_renames() -> None:
    sftp = _FakeSFTP()
    remote_path = "data/phone_book.json"

    write_remote_phone_book(sftp, [{"name": "Apple", "phones": []}], remote_path=remote_path)

    # The target path must never be opened directly for writing (that would
    # truncate the shared file in place).
    write_opens = [c for c in sftp.calls if c[0] == "file"]
    assert len(write_opens) == 1
    tmp_path = write_opens[0][1]
    assert tmp_path != remote_path
    assert tmp_path.startswith(remote_path + ".tmp-")

    # The rename call moves the temp file over the real target.
    rename_calls = [c for c in sftp.calls if c[0] == "posix_rename"]
    assert rename_calls == [("posix_rename", tmp_path, remote_path)]

    assert remote_path in sftp.files
    assert tmp_path not in sftp.files
    assert json.loads(sftp.files[remote_path].decode("utf-8")) == [{"name": "Apple", "phones": []}]


def test_write_remote_phone_book_falls_back_when_posix_rename_unsupported() -> None:
    sftp = _FakeSFTP()
    remote_path = "data/phone_book.json"
    sftp.files[remote_path] = b"[]"
    sftp.posix_rename_should_fail = True

    write_remote_phone_book(sftp, [{"name": "Aful", "phones": []}], remote_path=remote_path)

    call_kinds = [c[0] for c in sftp.calls]
    assert "posix_rename" in call_kinds
    assert "remove" in call_kinds
    assert "rename" in call_kinds
    assert call_kinds.index("posix_rename") < call_kinds.index("remove") < call_kinds.index("rename")

    assert json.loads(sftp.files[remote_path].decode("utf-8")) == [{"name": "Aful", "phones": []}]


def test_write_remote_phone_book_removes_temp_on_write_failure() -> None:
    sftp = _FakeSFTP()
    remote_path = "data/phone_book.json"
    sftp.write_should_fail = True

    with pytest.raises(OSError):
        write_remote_phone_book(sftp, [{"name": "Aful", "phones": []}], remote_path=remote_path)

    # The target must never have been opened/truncated directly.
    assert all(c[1] != remote_path for c in sftp.calls if c[0] == "file")
    assert remote_path not in sftp.files

    # Best-effort cleanup of the temp file must have been attempted.
    remove_calls = [c for c in sftp.calls if c[0] == "remove"]
    assert len(remove_calls) == 1
    tmp_path = remove_calls[0][1]
    assert tmp_path.startswith(remote_path + ".tmp-")


def test_write_remote_phone_book_removes_temp_when_fallback_rename_fails() -> None:
    sftp = _FakeSFTP()
    remote_path = "data/phone_book.json"
    sftp.posix_rename_should_fail = True
    sftp.rename_should_fail = True

    with pytest.raises(OSError):
        write_remote_phone_book(sftp, [{"name": "Aful", "phones": []}], remote_path=remote_path)

    assert remote_path not in sftp.files
    remove_calls = [c for c in sftp.calls if c[0] == "remove"]
    # One remove for clearing the target before the fallback rename, and one
    # for the final best-effort temp-file cleanup.
    assert len(remove_calls) >= 1


class _FakeSFTPReadFile:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self) -> "_FakeSFTPReadFile":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def read(self) -> bytes:
        return self._payload


class _FakeReadSFTP:
    def __init__(self, payload: bytes | None = None, error: Exception | None = None) -> None:
        self._payload = payload
        self._error = error

    def file(self, path: str, mode: str) -> _FakeSFTPReadFile:
        if self._error is not None:
            raise self._error
        return _FakeSFTPReadFile(self._payload or b"[]")


def test_read_remote_phone_book_file_not_found_raises_missing() -> None:
    sftp = _FakeReadSFTP(error=FileNotFoundError("no such file"))
    with pytest.raises(RemotePhoneBookMissingError):
        read_remote_phone_book(sftp, remote_path="data/phone_book.json")


def test_read_remote_phone_book_ioerror_enoent_raises_missing() -> None:
    err = IOError("no such file")
    err.errno = errno.ENOENT
    sftp = _FakeReadSFTP(error=err)
    with pytest.raises(RemotePhoneBookMissingError):
        read_remote_phone_book(sftp, remote_path="data/phone_book.json")


def test_read_remote_phone_book_generic_oserror_raises_read_error() -> None:
    err = OSError("permission denied")
    err.errno = errno.EACCES
    sftp = _FakeReadSFTP(error=err)
    with pytest.raises(RemotePhoneBookReadError):
        read_remote_phone_book(sftp, remote_path="data/phone_book.json")


def test_read_remote_phone_book_invalid_json_raises_read_error() -> None:
    sftp = _FakeReadSFTP(payload=b"not json")
    with pytest.raises(RemotePhoneBookReadError):
        read_remote_phone_book(sftp, remote_path="data/phone_book.json")
