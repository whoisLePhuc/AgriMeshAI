""".env file loader for edge_agent.

A clean-room implementation of .env file parsing and loading.
Supports quoted values, comments, variable interpolation, and
the ``export`` prefix. Zero external dependencies.
"""

from __future__ import annotations

import os
import re
import sys
from typing import Iterator

_ESCAPE_MAP = {
    "\\\\": "\\",
    '\\"': '"',
    "\\'": "'",
    "\\n": "\n",
    "\\r": "\r",
    "\\t": "\t",
    "\\a": "\a",
    "\\b": "\b",
    "\\f": "\f",
    "\\v": "\v",
}

_INTERPOLATION_RE = re.compile(
    r"\$\{(?P<name>[^}:]+)(?::- *(?P<default>[^}]*))?\}"
)


# ── line parser ──────────────────────────────────────────────────────────────


def _strip_inline_comment(raw: str) -> str:
    """Remove an inline ``# comment`` from an unquoted value.

    Inline comments must be preceded by whitespace so that values like
    ``http://example.com#anchor`` are preserved.
    """
    idx = 0
    while idx < len(raw):
        pos = raw.find("#", idx)
        if pos == -1:
            break
        if pos > 0 and raw[pos - 1] in (" ", "\t"):
            return raw[:pos].rstrip()
        idx = pos + 1
    return raw.rstrip()


def _unescape_double(value: str) -> str:
    """Process backslash escapes inside double-quoted strings."""
    result: list[str] = []
    i = 0
    while i < len(value):
        if value[i] == "\\" and i + 1 < len(value):
            two = value[i : i + 2]
            if two in _ESCAPE_MAP:
                result.append(_ESCAPE_MAP[two])
                i += 2
                continue
        result.append(value[i])
        i += 1
    return "".join(result)


def _unescape_single(value: str) -> str:
    """Process backslash escapes inside single-quoted strings (only \\' and \\\\)."""
    return value.replace("\\'", "'").replace("\\\\", "\\")


def _parse_line(line: str) -> tuple[str | None, str | None]:
    """Parse a single .env line into a (key, value) pair.

    Returns ``(None, None)`` for blank lines and comments.
    """
    stripped = line.strip()

    if not stripped or stripped.startswith("#"):
        return None, None

    if stripped.startswith("export ") or stripped.startswith("export\t"):
        stripped = stripped[7:].lstrip()

    eq = stripped.find("=")
    if eq == -1:
        return stripped, None

    key = stripped[:eq].rstrip()
    rest = stripped[eq + 1 :].lstrip()

    if not rest:
        return key, ""

    if rest.startswith('"'):
        closing = rest.find('"', 1)
        if closing != -1:
            return key, _unescape_double(rest[1:closing])
        return key, _unescape_double(rest[1:])

    if rest.startswith("'"):
        closing = rest.find("'", 1)
        if closing != -1:
            return key, _unescape_single(rest[1:closing])
        return key, _unescape_single(rest[1:])

    return key, _strip_inline_comment(rest)


def _parse_file(
    path: str,
    encoding: str | None = "utf-8",
) -> Iterator[tuple[str, str | None]]:
    """Yield ``(key, value)`` pairs from a .env file."""
    try:
        with open(path, encoding=encoding) as fh:
            for line in fh:
                key, value = _parse_line(line)
                if key is not None:
                    yield key, value
    except FileNotFoundError:
        return


# ── variable interpolation ──────────────────────────────────────────────────


def _interpolate_value(
    raw: str,
    env: dict[str, str | None],
) -> str:
    """Expand ``${VAR}`` and ``${VAR:-default}`` references in *raw*."""

    def _replacer(m: re.Match[str]) -> str:
        name = m.group("name")
        default = m.group("default")
        resolved = env.get(name)
        if resolved is not None:
            return resolved
        return default if default is not None else ""

    return _INTERPOLATION_RE.sub(_replacer, raw)


def _resolve_all(
    pairs: Iterator[tuple[str, str | None]],
    override: bool,
) -> dict[str, str | None]:
    """Resolve variable interpolation across all parsed pairs."""
    resolved: dict[str, str | None] = {}
    for key, value in pairs:
        if value is None:
            resolved[key] = None
            continue
        lookup: dict[str, str | None] = {}
        if override:
            lookup.update(os.environ)  # type: ignore[arg-type]
            lookup.update(resolved)
        else:
            lookup.update(resolved)
            lookup.update(os.environ)  # type: ignore[arg-type]
        resolved[key] = _interpolate_value(value, lookup)
    return resolved


# ── directory walking ────────────────────────────────────────────────────────


def _ancestors(start: str) -> Iterator[str]:
    """Yield *start* and every parent directory up to the filesystem root."""
    current = os.path.abspath(start)
    while True:
        yield current
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent


# ── public API ───────────────────────────────────────────────────────────────


def find_dotenv(
    filename: str = ".env",
    raise_error_if_not_found: bool = False,
    usecwd: bool = False,
) -> str:
    """Search in increasingly higher folders for the given file.

    Returns the absolute path if found, or an empty string otherwise.
    """

    def _is_interactive() -> bool:
        return hasattr(sys, "ps1") or hasattr(sys, "ps2")

    if usecwd or _is_interactive() or getattr(sys, "frozen", False):
        start = os.getcwd()
    else:
        frame = sys._getframe(1)
        caller_file = frame.f_code.co_filename
        start = os.path.dirname(os.path.abspath(caller_file))

    for directory in _ancestors(start):
        candidate = os.path.join(directory, filename)
        if os.path.isfile(candidate):
            return candidate

    if raise_error_if_not_found:
        raise IOError("File not found")

    return ""


def dotenv_values(
    dotenv_path: str | os.PathLike[str] | None = None,
    *,
    interpolate: bool = True,
    override: bool = True,
    encoding: str | None = "utf-8",
) -> dict[str, str | None]:
    """Parse a ``.env`` file and return its content as a dict.

    Does **not** modify ``os.environ``.
    """
    if dotenv_path is None:
        dotenv_path = find_dotenv(usecwd=True)

    pairs = _parse_file(str(dotenv_path), encoding=encoding)

    if interpolate:
        return _resolve_all(pairs, override=override)

    return dict(pairs)


def load_dotenv(
    dotenv_path: str | os.PathLike[str] | None = None,
    *,
    override: bool = False,
    interpolate: bool = True,
    encoding: str | None = "utf-8",
) -> bool:
    """Parse a ``.env`` file and load its variables into ``os.environ``.

    Returns ``True`` if at least one variable was set.
    """
    if dotenv_path is None:
        dotenv_path = find_dotenv(usecwd=True)

    parsed = dotenv_values(
        dotenv_path,
        interpolate=interpolate,
        override=override,
        encoding=encoding,
    )

    if not parsed:
        return False

    for key, value in parsed.items():
        if key in os.environ and not override:
            continue
        if value is not None:
            os.environ[key] = value

    return True
