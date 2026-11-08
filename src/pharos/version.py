"""Conservative numeric version parsing, including calendar and packaging versions."""

import re

_PATTERN = re.compile(r"(?<![0-9.])([0-9]+(?:\.[0-9]+){1,3})(?![0-9.])")
_UNSTABLE = re.compile(
    r"(?:alpha|beta|rc|dev|nightly|canary|snapshot|preview|experimental|milestone|pre|next)(?:[0-9.\-_]|$)",
    re.IGNORECASE,
)
_PACKAGING = re.compile(r"^(?:[-_.]?(?:ls[0-9]+|alpine|ubuntu|deb|rhel|el[0-9]+|lts|stable|release)[\w.-]*)$", re.I)


def canonical(value: str | None, *, allow_revision: bool = True) -> str | None:
    if not isinstance(value, str) or len(value) > 4096:
        return None
    # SemVer build metadata cannot turn a stable version into a prerelease.
    base = value.split("+", 1)[0]
    if _UNSTABLE.search(base):
        return None
    match = _PATTERN.search(base)
    if match is None:
        return None
    components = match[1].split(".")
    if any(len(part) > 12 for part in components):
        return None
    suffix = base[match.end() :]
    # Whitespace may delimit an installed-version command's descriptive output.
    token_suffix = suffix.split(maxsplit=1)[0] if suffix and not suffix[0].isspace() else ""
    if token_suffix:
        revision = allow_revision and re.fullmatch(r"-[0-9][\w.+~:-]*", token_suffix)
        if not _PACKAGING.fullmatch(token_suffix) and not revision:
            return None
    if _PATTERN.search(suffix[len(token_suffix) :]):
        return None
    return ".".join(str(int(part)) for part in components)


def key(value: str) -> tuple[int, ...]:
    parts = tuple(int(x) for x in value.split("."))
    return parts + (0,) * (4 - len(parts))


def newer(a: str, b: str) -> bool:
    return key(a) > key(b)
