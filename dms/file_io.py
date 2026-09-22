"""Crash-safe JSON/text persistence helpers shared by every on-disk store.

Two problems this module solves:

- A crash (or a full disk) part-way through ``open(path, "w")`` truncates the
  real file and loses saved settings, calibration or credentials. Every write
  here goes to a sibling temporary file, is flushed and ``fsync``-ed, and only
  then replaces the target with ``os.replace``, which is atomic on POSIX and
  on Windows.
- A corrupt file was previously swallowed and silently replaced with defaults.
  ``load_json_with_backup`` instead renames the damaged file out of the way and
  returns a human-readable message, so the UI can tell the user where the old
  content went.

Session stores pass ``validate`` to :func:`atomic_write_text` so the bytes on
disk are parsed back into a session before anything is swapped in.
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

__all__ = [
    "atomic_write_json",
    "atomic_write_text",
    "backup_corrupt_file",
    "ensure_extension",
    "load_json_with_backup",
    "same_session_file",
]


_DEFAULT_MODE = 0o600


def _fsync_directory(directory: Path) -> None:
    """Flush the directory entry so the rename survives a power loss."""
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except (OSError, AttributeError):
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _apply_mode(path: Path, mode: int | None) -> None:
    if mode is None:
        return
    # Windows and some network filesystems do not implement POSIX modes.
    with contextlib.suppress(OSError, NotImplementedError):
        os.chmod(path, mode)


def atomic_write_text(
    path: Path | str,
    text: str,
    *,
    mode: int | None = _DEFAULT_MODE,
    encoding: str = "utf-8",
    validate: Callable[[str], None] | None = None,
) -> None:
    """Write ``text`` to ``path`` atomically, creating parent directories.

    The content lands in ``<path>.tmp-<pid>`` first. If anything raises before
    the final ``os.replace`` the original file is left completely untouched and
    the temporary file is removed. ``validate`` receives the text read back
    from the temporary file; raising from it cancels the swap.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target.with_name(f"{target.name}.tmp-{os.getpid()}")
    try:
        with open(temp_path, "w", encoding=encoding, newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if validate is not None:
            validate(temp_path.read_text(encoding=encoding))
        _apply_mode(temp_path, mode)
        os.replace(temp_path, target)
        _fsync_directory(target.parent)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def atomic_write_json(
    path: Path | str,
    data: Any,
    *,
    mode: int | None = _DEFAULT_MODE,
    indent: int = 2,
) -> None:
    """Serialize ``data`` and write it with :func:`atomic_write_text`.

    Serialization happens before the temporary file is opened so an
    unserializable value can never leave a half-written file behind.
    """
    serialized = json.dumps(data, indent=indent, ensure_ascii=False)
    atomic_write_text(path, serialized, mode=mode)


def ensure_extension(path: Path | str, suffix: str) -> Path:
    """Return ``path`` ending in the full lowercase ``suffix``.

    For a compound suffix such as ``.fastgraph-rnd.json`` a name ending in the
    first part gains ``.json``, and a bare ``.json`` is widened to the full
    suffix; any other name simply has ``suffix`` appended.
    """
    path = Path(path)
    name = path.name
    lower_name = name.lower()
    head, _, last = suffix.rpartition(".")
    if lower_name.endswith(suffix):
        return path.with_name(name[: -len(suffix)] + suffix)
    if head and lower_name.endswith(head):
        return path.with_name(f"{name}.{last}")
    if lower_name.endswith(f".{last}"):
        return path.with_name(name[: -len(last) - 1] + suffix)
    return path.with_name(name + suffix)


def same_session_file(left: str | Path | None, right: str | Path | None) -> bool:
    """Return whether two paths name the same session file on disk."""
    if not left or not right:
        return False
    left_path = Path(left).expanduser()
    right_path = Path(right).expanduser()
    try:
        if left_path.exists() and right_path.exists():
            return left_path.samefile(right_path)
    except OSError:
        pass
    try:
        return left_path.resolve(strict=False) == right_path.resolve(strict=False)
    except OSError:
        return left_path == right_path


def backup_corrupt_file(path: Path | str) -> Path | None:
    """Rename ``path`` to ``<name>.corrupt-<timestamp>`` and return the target.

    Returns ``None`` when the file could not be moved; callers then still fall
    back to defaults but say so without naming a backup that does not exist.
    """
    source = Path(path)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = source.with_name(f"{source.name}.corrupt-{stamp}")
    counter = 1
    while backup.exists():
        backup = source.with_name(f"{source.name}.corrupt-{stamp}-{counter}")
        counter += 1
    try:
        os.replace(source, backup)
    except OSError:
        return None
    return backup


def load_json_with_backup(path: Path | str) -> tuple[dict | None, str | None]:
    """Load a JSON object, moving an unreadable file aside instead of losing it.

    Returns ``(data, error)``:

    - ``(dict, None)`` when the file parsed as a JSON object.
    - ``(None, None)`` when the file simply does not exist.
    - ``(None, message)`` when the file was corrupt (bad JSON, bad encoding, or
      valid JSON that is not an object) or unreadable. A corrupt file is first
      renamed to ``<name>.corrupt-<YYYYmmdd-HHMMSS>`` and the message names that
      backup so the user can recover it by hand.
    """
    source = Path(path)
    if not source.exists():
        return None, None

    try:
        raw = source.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        return None, _corrupt_message(source, f"file is not valid UTF-8 ({exc.reason})")
    except OSError as exc:
        # Unreadable but not necessarily damaged; never move it aside.
        return None, f"Could not read {source.name}: {exc}. Using defaults for this session."

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, _corrupt_message(source, f"invalid JSON at line {exc.lineno}: {exc.msg}")

    if not isinstance(parsed, dict):
        return None, _corrupt_message(
            source, f"expected a JSON object, found {type(parsed).__name__}"
        )

    return parsed, None


def _corrupt_message(source: Path, reason: str) -> str:
    backup = backup_corrupt_file(source)
    if backup is None:
        return (
            f"{source.name} is damaged ({reason}) and could not be moved aside. "
            "Defaults are in use for this session."
        )
    return (
        f"{source.name} is damaged ({reason}). "
        f"It was saved as {backup.name} and defaults are in use."
    )
