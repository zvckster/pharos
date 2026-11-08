"""Private, atomic state and process locking."""

import fcntl
import json
import math
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .version import canonical


def write_json(path, value):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix="." + path.name)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def locked(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / "run.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another Pharos check is already running") from None
        yield


def _strings(value):
    return isinstance(value, list) and all(isinstance(item, str) for item in value) and len(value) == len(set(value))


def _release(value, *, require_url=False):
    if not isinstance(value, dict):
        return False
    if require_url and "url" not in value:
        return False
    version = value.get("version")
    return isinstance(version, str) and canonical(version) == version and isinstance(value.get("url", ""), str)


def read_state(directory):
    path = Path(directory) / "state.json"
    if not path.exists():
        return {"schema_version": 1, "checks": {}, "pending": [], "sources": {}}
    state = json.loads(path.read_text())
    valid = isinstance(state, dict) and type(state.get("schema_version")) is int and state["schema_version"] == 1
    if not valid:
        raise ValueError("Unsupported state schema; refusing to overwrite")
    for field, kind in (("checks", dict), ("pending", list), ("sources", dict)):
        if not isinstance(state.get(field), kind):
            raise ValueError("Invalid state structure; refusing to overwrite")
    for check in state["checks"].values():
        if not isinstance(check, dict) or not isinstance(check.get("identity"), str) or not _strings(check.get("seen")):
            raise ValueError("Invalid observation state; refusing to overwrite")
        if not _release({"version": check.get("latest")}) or any(not _release({"version": v}) for v in check["seen"]):
            raise ValueError("Invalid observed version; refusing to overwrite")
    for cache in state["sources"].values():
        if (
            not isinstance(cache, dict)
            or not isinstance(cache.get("identity"), str)
            or not _release(cache.get("release"), require_url=True)
        ):
            raise ValueError("Invalid source cache; refusing to overwrite")
        if (
            type(cache.get("checked_at")) not in (int, float)
            or not 0 <= cache["checked_at"] <= 1e12
            or not math.isfinite(cache["checked_at"])
        ):
            raise ValueError("Invalid cache timestamp; refusing to overwrite")
        if cache.get("etag") is not None and not isinstance(cache["etag"], str):
            raise ValueError("Invalid cache ETag; refusing to overwrite")
    for event in state["pending"]:
        if (
            not isinstance(event, dict)
            or not isinstance(event.get("id"), str)
            or not isinstance(event.get("title"), str)
        ):
            raise ValueError("Invalid pending event; refusing to overwrite")
        if not _strings(event.get("delivered")) or ("targets" in event and not _strings(event["targets"])):
            raise ValueError("Invalid notification acknowledgements; refusing to overwrite")
        if event.get("diagnostic"):
            if not isinstance(event.get("message"), str):
                raise ValueError("Invalid diagnostic event; refusing to overwrite")
        else:
            changes = event.get("changes")
            if not isinstance(changes, list) or not changes:
                raise ValueError("Invalid release event; refusing to overwrite")
            for change in changes:
                if not _release(change, require_url=True) or any(
                    not isinstance(change.get(field), str) for field in ("id", "name")
                ):
                    raise ValueError("Invalid release change; refusing to overwrite")
                if change.get("installed") is not None and not isinstance(change["installed"], str):
                    raise ValueError("Invalid installed version; refusing to overwrite")
    if type(state.get("failed_runs", 0)) is not int or state.get("failed_runs", 0) < 0:
        raise ValueError("Invalid failure counter; refusing to overwrite")
    if "degraded" in state and not isinstance(state["degraded"], bool):
        raise ValueError("Invalid degraded flag; refusing to overwrite")
    return state
