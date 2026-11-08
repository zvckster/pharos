"""Validate configuration before any subprocess or network side effect."""

import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from .version import canonical

SOURCES = {"github", "gitlab", "forgejo", "json", "manual"}
DETECTORS = {"static", "docker", "dpkg", "rpm", "command", "unknown"}
CHANNELS = {"ntfy", "email", "webhook", "gotify"}


def text(value, field):
    if not isinstance(value, str) or not value.strip() or any(ord(char) < 32 for char in value):
        raise ValueError(f"{field} must be a nonempty string without control characters")
    return value


def _url(value, schemes):
    text(value, "URL")
    try:
        parsed = urlsplit(value)
        port = parsed.port
        valid = parsed.scheme in schemes and parsed.hostname and not parsed.username and not parsed.password
        valid = valid and not parsed.fragment and not any(char.isspace() for char in value)
        if port is not None and not 1 <= port <= 65535:
            valid = False
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("URL must use an allowed scheme, a valid host/port, and no embedded credentials or fragment")
    return parsed


def https_url(value):
    return _url(value, {"https"})


def env_reference(obj, field):
    if field in obj and (not isinstance(obj[field], str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", obj[field])):
        raise ValueError("Invalid secret environment variable name")


def unique_id(value, seen):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_.-]+", value) or value in seen:
        raise ValueError("IDs must be unique letters, digits, dots, underscores or hyphens")
    seen.add(value)


def load(path):
    config = json.loads(Path(path).read_text())
    if not isinstance(config, dict) or type(config.get("schema_version")) is not int or config["schema_version"] != 1:
        raise ValueError("Expected schema_version: 1")
    if not isinstance(config.get("checks"), list) or not config["checks"]:
        raise ValueError("checks must be a nonempty array")
    ids = set()
    for check in config["checks"]:
        if not isinstance(check, dict):
            raise ValueError("Each check must be an object")
        ident = check.get("id")
        unique_id(ident, ids)
        text(check.get("name", ident), "Check name")
        source = check.get("source")
        if not isinstance(source, dict) or not isinstance(source.get("type"), str) or source["type"] not in SOURCES:
            raise ValueError("Unsupported or missing release source")
        kind = source["type"]
        if kind == "github":
            repo = source.get("repo")
            if not isinstance(repo, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
                raise ValueError("GitHub repo must be owner/repository")
        if kind in {"forgejo", "gitlab", "json"}:
            https_url(source.get("url"))
        env_reference(source, "token_env")
        if "version_field" in source:
            text(source["version_field"], "version_field")
        if "major" in source and (type(source["major"]) is not int or source["major"] < 0):
            raise ValueError("major must be a nonnegative integer")
        detector = check.get("installed", {"type": "unknown"})
        if (
            not isinstance(detector, dict)
            or not isinstance(detector.get("type"), str)
            or detector["type"] not in DETECTORS
        ):
            raise ValueError("Unsupported installed-version detector")
        dtype = detector["type"]
        required = {"static": "version", "docker": "container", "dpkg": "package", "rpm": "package"}
        if dtype in required:
            text(detector.get(required[dtype]), required[dtype])
        if dtype == "static" and canonical(detector["version"]) is None:
            raise ValueError("Static installed version is not a supported stable version")
        if dtype == "command":
            argv = detector.get("argv")
            if (
                not isinstance(argv, list)
                or not argv
                or not all(isinstance(arg, str) and "\x00" not in arg for arg in argv)
                or not argv[0]
            ):
                raise ValueError("command argv must be a nonempty array of strings without NUL bytes")
        if "use_tag" in detector and not isinstance(detector["use_tag"], bool):
            raise ValueError("use_tag must be boolean")
        if "label" in detector:
            text(detector["label"], "label")
        if "pattern" in detector:
            text(detector["pattern"], "pattern")
            try:
                groups = re.compile(detector["pattern"]).groups
            except re.error:
                raise ValueError("Version pattern is not a valid regular expression") from None
            if groups < 1:
                raise ValueError("Version pattern needs a capture group")
        if "notify" in check and not isinstance(check["notify"], bool):
            raise ValueError("notify must be boolean")
        if "note" in check:
            text(check["note"], "note")
    if not isinstance(config.get("notifications", []), list):
        raise ValueError("notifications must be an array")
    names = set()
    for channel in config.get("notifications", []):
        if not isinstance(channel, dict):
            raise ValueError("Each notification channel must be an object")
        unique_id(channel.get("id"), names)
        if not isinstance(channel.get("type"), str) or channel["type"] not in CHANNELS:
            raise ValueError("Unsupported notification type")
        if channel["type"] == "email":
            for field in ("host", "from"):
                text(channel.get(field), field)
            if not isinstance(channel.get("to"), list) or not channel["to"]:
                raise ValueError("Email requires a nonempty to array")
            for recipient in channel["to"]:
                text(recipient, "Email recipient")
            if channel.get("security", "starttls") not in ("starttls", "tls"):
                raise ValueError("Email security must be starttls or tls")
            if "port" in channel and (type(channel["port"]) is not int or not 1 <= channel["port"] <= 65535):
                raise ValueError("Email port must be an integer between 1 and 65535")
            if "username" in channel:
                text(channel["username"], "username")
        else:
            _url(channel.get("url"), {"http", "https"})
        for field in ("token_env", "password_env"):
            env_reference(channel, field)
        if channel["type"] == "gotify" and not channel.get("token_env"):
            raise ValueError("Gotify requires token_env")
        if channel["type"] == "ntfy":
            text(channel.get("topic", "pharos"), "ntfy topic")
        if "priority" in channel and type(channel["priority"]) is not int:
            raise ValueError("Notification priority must be an integer")
    interval = config.get("interval_seconds", 21600)
    if type(interval) is not int or interval < 60:
        raise ValueError("interval_seconds must be an integer >= 60")
    return config
