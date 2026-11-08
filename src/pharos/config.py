"""Configuration validation. Secrets are resolved only at delivery time."""

import json
import re
from pathlib import Path
from urllib.parse import urlsplit

SOURCES = {"github", "gitlab", "forgejo", "json", "manual"}
DETECTORS = {"static", "docker", "dpkg", "rpm", "command", "unknown"}
CHANNELS = {"ntfy", "email", "webhook", "gotify"}


def https_url(value):
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Release source URLs must use HTTPS without embedded credentials")


def load(path):
    config = json.loads(Path(path).read_text())
    if not isinstance(config, dict) or config.get("schema_version") != 1:
        raise ValueError("Expected schema_version: 1")
    if not isinstance(config.get("checks"), list) or not config["checks"]:
        raise ValueError("checks must be a nonempty array")
    ids = set()
    for check in config["checks"]:
        if not isinstance(check, dict):
            raise ValueError("Each check must be an object")
        ident = check.get("id", "")
        if not isinstance(ident, str) or not re.fullmatch(r"[a-zA-Z0-9_.-]+", ident) or ident in ids:
            raise ValueError("Check IDs must be unique letters, digits, dots, underscores or hyphens")
        ids.add(ident)
        if not isinstance(check.get("name", ident), str):
            raise ValueError(f"{ident}: name must be a string")
        source = check.get("source", {})
        if not isinstance(source, dict):
            raise ValueError(f"{ident}: source must be an object")
        kind = source.get("type")
        if kind not in SOURCES:
            raise ValueError(f"{ident}: unsupported release source")
        if kind == "github" and not re.fullmatch(r"[\w.-]+/[\w.-]+", source.get("repo", "")):
            raise ValueError(f"{ident}: GitHub repo must be owner/repository")
        if kind in {"forgejo", "gitlab", "json"}:
            https_url(source.get("url", ""))
        detector = check.get("installed", {"type": "unknown"})
        if not isinstance(detector, dict):
            raise ValueError(f"{ident}: installed must be an object")
        dtype = detector.get("type")
        if dtype not in DETECTORS:
            raise ValueError(f"{ident}: unsupported installed-version detector")
        required = {"static": "version", "docker": "container", "dpkg": "package", "rpm": "package", "command": "argv"}
        if dtype in required and not detector.get(required[dtype]):
            raise ValueError(f"{ident}: missing {required[dtype]}")
        if dtype == "command" and (
            not isinstance(detector["argv"], list) or not all(isinstance(a, str) for a in detector["argv"])
        ):
            raise ValueError(f"{ident}: command argv must be an array of strings")
        if detector.get("pattern"):
            try:
                groups = re.compile(detector["pattern"]).groups
            except re.error:
                raise ValueError(f"{ident}: version pattern is not a valid regular expression") from None
            if groups < 1:
                raise ValueError(f"{ident}: version pattern needs a capture group")
        for option in ("notify",):
            if option in check and not isinstance(check[option], bool):
                raise ValueError(f"{ident}: {option} must be boolean")
        if "major" in source and (type(source["major"]) is not int or source["major"] < 0):
            raise ValueError(f"{ident}: major must be a nonnegative integer")
    if not isinstance(config.get("notifications", []), list):
        raise ValueError("notifications must be an array")
    names = set()
    for channel in config.get("notifications", []):
        if not isinstance(channel, dict):
            raise ValueError("Each notification channel must be an object")
        name = channel.get("id", "")
        if not name or name in names or channel.get("type") not in CHANNELS:
            raise ValueError("Notification IDs must be unique and types supported")
        names.add(name)
        if channel["type"] == "email":
            if (
                not channel.get("host")
                or not channel.get("from")
                or not isinstance(channel.get("to"), list)
                or not channel["to"]
            ):
                raise ValueError("Email requires host, from and a nonempty to array")
            if channel.get("security", "starttls") not in {"starttls", "tls"}:
                raise ValueError("Email security must be starttls or tls")
        else:
            url = urlsplit(channel.get("url", ""))
            if url.scheme not in {"https", "http"} or not url.hostname or url.username or url.password:
                raise ValueError("Notification URL must be HTTP(S), without embedded credentials")
        for field in ("token_env", "password_env"):
            if field in channel and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", channel[field]):
                raise ValueError("Invalid secret environment variable name")
    interval = config.get("interval_seconds", 21600)
    if type(interval) is not int or interval < 60:
        raise ValueError("interval_seconds must be an integer >= 60")
    return config
