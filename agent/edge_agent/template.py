"""Template variable interpolation for agent instructions.

Supports ``{{currentDate}}``, ``{{url:https://...}}``, and custom
user-supplied variables.  Unknown placeholders are left as-is.
"""

from __future__ import annotations

import datetime
import re
import urllib.request

from edge_agent.logger import get_logger

_PLACEHOLDER_RE = re.compile(r"\{\{(.+?)\}\}")
_URL_FETCH_TIMEOUT = 10
_URL_MAX_BYTES = 1_048_576  # 1 MB

logger = get_logger("template")


def _fetch_url(url: str) -> str:
    """Fetch *url*.  Only http(s) schemes are accepted; no host filtering is applied."""
    if not url.startswith(("https://", "http://")):
        raise ValueError(
            f"Only http(s) URLs are allowed in {{{{url:…}}}} "
            f"placeholders, got: {url!r}"
        )
    with urllib.request.urlopen(url, timeout=_URL_FETCH_TIMEOUT) as resp:
        data = resp.read(_URL_MAX_BYTES + 1)
        if len(data) > _URL_MAX_BYTES:
            logger.warning(
                "URL response exceeded %d bytes, truncating: %s",
                _URL_MAX_BYTES, url,
            )
            data = data[:_URL_MAX_BYTES]
        return data.decode()


def _resolve(key: str, variables: dict[str, str]) -> str | None:
    """Return the replacement for *key*, or ``None`` to leave it unchanged."""
    if key == "currentDate":
        return datetime.date.today().isoformat()

    if key.startswith("url:"):
        return _fetch_url(key[4:])

    return variables.get(key)


def render_template(
    template: str,
    variables: dict[str, str] | None = None,
) -> str:
    """Replace ``{{…}}`` placeholders in *template*.

    Built-in variables
    ~~~~~~~~~~~~~~~~~~
    - ``{{currentDate}}`` — today's date in ISO-8601 format
    - ``{{url:https://…}}`` — fetched URL body (decoded as UTF-8)

    Custom variables are looked up in *variables*.  Any placeholder
    whose key is not recognised is left unchanged in the output.
    """
    vars_map = variables or {}

    def _replacer(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        replacement = _resolve(key, vars_map)
        if replacement is None:
            return match.group(0)
        return replacement

    return _PLACEHOLDER_RE.sub(_replacer, template)
