"""Read installed versions. Commands are explicit argv; no shell is used."""

import json
import os
import re
import selectors
import signal
import subprocess
import time

from .errors import CheckError
from .version import canonical

COMMAND_TIMEOUT = 20
OUTPUT_LIMIT = 1024 * 1024


def command(argv):
    try:
        with subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True) as process:
            try:
                output = {"stdout": bytearray(), "stderr": bytearray()}
                deadline = time.monotonic() + COMMAND_TIMEOUT
                size = 0
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
                    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
                    while selector.get_map():
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise CheckError("Installed-version command timed out")
                        for ready, _ in selector.select(min(remaining, 0.1)):
                            chunk = os.read(ready.fd, 16384)
                            if not chunk:
                                selector.unregister(ready.fileobj)
                                continue
                            size += len(chunk)
                            if size > OUTPUT_LIMIT:
                                raise CheckError("Installed-version command exceeded the output limit")
                            output[ready.data].extend(chunk)
                try:
                    code = process.wait(timeout=max(0.001, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    raise CheckError("Installed-version command timed out") from None
                if code:
                    raise CheckError("Installed-version command exited unsuccessfully")
                return (output["stdout"] or output["stderr"]).decode("utf-8", errors="replace").strip()
            except BaseException:
                # Reap the whole process group, including descendants holding pipes.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                raise
    except OSError:
        raise CheckError("Installed-version command could not be executed") from None


def detect(spec):
    kind = spec.get("type", "unknown")
    if kind == "unknown":
        return None
    if kind == "static":
        raw = spec["version"]
    elif kind == "dpkg":
        raw = command(["dpkg-query", "-W", "-f=${Version}", "--", spec["package"]]).split(":", 1)[-1].rsplit("-", 1)[0]
    elif kind == "rpm":
        raw = command(["rpm", "-q", "--qf", "%{VERSION}", "--", spec["package"]])
    elif kind == "command":
        raw = command(spec["argv"])
    elif kind == "docker":
        container = json.loads(command(["docker", "container", "inspect", "--", spec["container"]]))[0]
        image = json.loads(command(["docker", "image", "inspect", container["Image"]]))[0]
        labels = image.get("Config", {}).get("Labels") or {}
        if spec.get("label"):
            raw = labels.get(spec["label"], "")
        elif spec.get("use_tag", False):
            ref = container["Config"]["Image"].split("@", 1)[0]
            raw = ref.rsplit("/", 1)[-1].split(":", 1)[-1] if ":" in ref.rsplit("/", 1)[-1] else ""
        else:
            raw = labels.get("org.opencontainers.image.version", "")
        # Missing labels and floating tags mean unknown, not current.
        if not raw or raw in {"latest", "main", "master", "release", "stable"}:
            return None
    else:
        raise ValueError("Unsupported installed-version detector")
    if spec.get("pattern"):
        match = re.search(spec["pattern"], raw)
        if not match:
            raise CheckError("Installed version did not match configured pattern")
        raw = match.group(1)
    version = canonical(raw)
    if version is None:
        if kind == "docker":
            return None
        raise CheckError("Installed version has no supported stable numeric version")
    return version
