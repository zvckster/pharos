"""Private, atomic state and process locking."""

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path


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


def read_state(directory):
    path = Path(directory) / "state.json"
    if not path.exists():
        return {"schema_version": 1, "checks": {}, "pending": [], "sources": {}}
    state = json.loads(path.read_text())
    if state.get("schema_version") != 1:
        raise ValueError("Unsupported state schema; refusing to overwrite")
    for field, kind in (("checks", dict), ("pending", list), ("sources", dict)):
        if not isinstance(state.get(field), kind):
            raise ValueError("Invalid state structure; refusing to overwrite")
    return state
