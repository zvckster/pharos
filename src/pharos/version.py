"""Compare numeric application versions, including calendar versions.

Unknown version formats are errors, never interpreted as up-to-date.
"""

import re

_PATTERN = re.compile(r"(?<!\d)(\d+(?:\.\d+){1,3})(?![\d.])")
_UNSTABLE = re.compile(
    r"(?:alpha|beta|rc|dev|nightly|canary|snapshot|preview|experimental|pre|next)(?:[\d.\-_]|$)", re.IGNORECASE
)


def canonical(value: str | None) -> str | None:
    if not isinstance(value, str) or _UNSTABLE.search(value):
        return None
    match = _PATTERN.search(value)
    return ".".join(str(int(x)) for x in match[1].split(".")) if match else None


def key(value: str) -> tuple[int, ...]:
    parts = tuple(int(x) for x in value.split("."))
    return parts + (0,) * (4 - len(parts))


def newer(a: str, b: str) -> bool:
    return key(a) > key(b)
