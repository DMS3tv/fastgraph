from __future__ import annotations

import re

import numpy as np

_DECIMAL_COMMA_RE = re.compile(r"^-?\d+,\d+$")


def _plain_float(token: str) -> float | None:
    try:
        return float(token)
    except ValueError:
        return None


def _whitespace_token_float(token: str) -> float | None:
    """Parse a token that whitespace already isolated as its own column.

    Because whitespace (not a comma) is the delimiter here, a comma inside
    the token cannot be a column separator -- it can only be a European
    decimal separator (e.g. "1000,00" means 1000.00). Tolerate that case,
    otherwise parse normally.
    """
    if _DECIMAL_COMMA_RE.match(token):
        token = token.replace(",", ".", 1)
    return _plain_float(token)


def load_two_column_txt_curve(path: str, *, label: str = "Curve") -> tuple[np.ndarray, np.ndarray]:
    """Load a permissive REW-style two-column TXT curve.

    Accepts whitespace or comma delimiters, skips comments/header-like lines,
    and requires at least two valid numeric rows.

    Dialect is resolved independently per row from its whitespace shape,
    which is unambiguous because whitespace and commas can't both be the
    delimiter on the same row:
      - Exactly 2 whitespace-separated tokens: whitespace-delimited row.
        Each token may use a European decimal comma (e.g. "1000,00"),
        since a comma inside an already-whitespace-isolated token can only
        be a decimal separator, never a column delimiter.
      - Exactly 1 whitespace token containing a comma: comma-delimited row
        (e.g. "200,2.0" or "1000,-3.25"). It must split into exactly 2
        plain (dot-decimal) numeric fields; any other split count or a
        non-numeric field is a malformed data row, not a header, so it
        raises rather than being silently dropped.
      - 3+ whitespace tokens: legacy multi-column rows (e.g. freq/mag/phase)
        are preserved by reading only the first two tokens as plain floats.
      - Anything else (plain text with no numeric/comma shape, e.g. a
        "freq mag" header row) is treated as a non-data line and skipped
        silently, matching the previous permissive behavior for headers.
    """
    x_vals: list[float] = []
    y_vals: list[float] = []

    with open(path, "r", encoding="utf-8") as f:
        for line_no, raw_line in enumerate(f, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#") or line.startswith("*"):
                continue

            ws_tokens = line.split()
            x: float | None
            y: float | None

            if len(ws_tokens) == 2:
                x = _whitespace_token_float(ws_tokens[0])
                y = _whitespace_token_float(ws_tokens[1])
                if x is None or y is None:
                    continue  # not numeric-shaped -> treat as header/label line
            elif len(ws_tokens) == 1 and "," in ws_tokens[0]:
                comma_parts = ws_tokens[0].split(",")
                if len(comma_parts) != 2:
                    raise ValueError(
                        f"{label} file '{path}' has an invalid comma-delimited "
                        f"row at line {line_no}: {line!r}"
                    )
                x = _plain_float(comma_parts[0])
                y = _plain_float(comma_parts[1])
                if x is None or y is None:
                    raise ValueError(
                        f"{label} file '{path}' has an invalid data row at "
                        f"line {line_no}: {line!r}"
                    )
            elif len(ws_tokens) >= 3:
                x = _plain_float(ws_tokens[0])
                y = _plain_float(ws_tokens[1])
                if x is None or y is None:
                    continue  # not numeric-shaped -> treat as header/label line
            else:
                continue  # single non-comma token or blank -> not a data row

            if not (np.isfinite(x) and np.isfinite(y)):
                continue
            x_vals.append(x)
            y_vals.append(y)

    if len(x_vals) < 2:
        raise ValueError(f"{label} file '{path}' has fewer than 2 valid data rows.")

    x_arr = np.asarray(x_vals, dtype=float)
    y_arr = np.asarray(y_vals, dtype=float)
    positive = x_arr > 0.0
    if not np.any(positive):
        raise ValueError(f"{label} file '{path}' has no positive frequency rows.")

    x_arr = x_arr[positive]
    y_arr = y_arr[positive]
    if x_arr.size < 2:
        raise ValueError(f"{label} file '{path}' has fewer than 2 positive frequency rows.")

    order = np.argsort(x_arr, kind="stable")
    x_arr = x_arr[order]
    y_arr = y_arr[order]
    return x_arr, y_arr
