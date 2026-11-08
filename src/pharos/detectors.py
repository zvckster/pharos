"""Read installed versions. Commands are explicit argv; no shell is used."""

import json
import re
import subprocess

from .version import canonical


def command(argv):
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=20, check=True)
    except (subprocess.SubprocessError, OSError):
        raise RuntimeError("Installed-version command failed or timed out") from None
    if len(result.stdout) > 1024 * 1024:
        raise ValueError("Installed-version command returned too much output")
    return result.stdout.strip()


def detect(spec):
    kind = spec.get("type", "unknown")
    if kind == "unknown":
        return None
    if kind == "static":
        raw = spec["version"]
    elif kind == "dpkg":
        raw = command(["dpkg-query", "-W", "-f=${Version}", "--", spec["package"]]).split(":", 1)[-1]
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
            raise ValueError("Installed version did not match configured pattern")
        raw = match.group(1)
    version = canonical(raw)
    if version is None:
        if kind == "docker":
            return None
        raise ValueError("Installed version has no supported stable numeric version")
    return version
