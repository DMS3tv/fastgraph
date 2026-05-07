from dms.session import SessionData
from dms.squiglink import (
    build_phone_book_name_stem,
    build_upload_name_stem,
    merge_phone_book_entry,
    upload_export_sftp,
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
