"""Release comparison, quiet baselines, persistent outbox and grouped delivery."""

import concurrent.futures
import datetime
import hashlib
import json
from pathlib import Path

from . import detectors, notifications, sources
from .storage import read_state, write_json
from .version import newer


def observe(state, check, release, installed):
    identity = hashlib.sha256(json.dumps(check["source"], sort_keys=True).encode()).hexdigest()
    previous = state["checks"].get(check["id"], {})
    if previous.get("identity") != identity:
        previous = {}
    version = release["version"]
    seen = previous.get("seen", [])
    old = previous.get("latest")
    change = None
    if old and version not in seen and newer(version, old) and check.get("notify", True):
        if installed is None or newer(version, installed):
            change = {"id": check["id"], "name": check.get("name", check["id"]), "installed": installed, **release}
    if version not in seen:
        seen.append(version)
    state["checks"][check["id"]] = {"identity": identity, "latest": version, "seen": seen}
    return change


def event(changes):
    identity = hashlib.sha256(json.dumps(sorted((c["id"], c["version"]) for c in changes)).encode()).hexdigest()[:24]
    title = (
        f"{changes[0]['name']} {changes[0]['version']} released"
        if len(changes) == 1
        else f"{len(changes)} new stable releases"
    )
    return {"id": identity, "title": title, "changes": changes, "delivered": []}


def inspect(check, cache, force):
    release, cache = sources.latest(check["source"], cache, force)
    installed = detectors.detect(check.get("installed", {"type": "unknown"}))
    return release, installed, cache


def run(config, directory, dry_run=False, notify=False, force=False):
    directory = Path(directory)
    state = read_state(directory)
    changes, checks, errors = [], [], []
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        jobs = {
            check["id"]: pool.submit(inspect, check, state["sources"].get(check["id"], {}), force)
            for check in config["checks"]
            if check["source"]["type"] != "manual"
        }
        for check in config["checks"]:
            row = {"id": check["id"], "name": check.get("name", check["id"])}
            if check["source"]["type"] == "manual":
                row.update(status="manual", note=check.get("note", "No reliable release feed configured"))
            else:
                try:
                    release, installed, cache = jobs[check["id"]].result()
                    state["sources"][check["id"]] = cache
                    status = (
                        "unknown"
                        if installed is None
                        else "behind"
                        if newer(release["version"], installed)
                        else "ahead"
                        if newer(installed, release["version"])
                        else "current"
                    )
                    row.update(
                        installed=installed,
                        latest=release["version"],
                        url=release["url"],
                        status=status,
                        notify=check.get("notify", True),
                    )
                    change = observe(state, check, release, installed)
                    if change:
                        changes.append(change)
                except Exception:
                    # Avoid leaking command output or private feed URLs in reports.
                    row["status"] = "error"
                    errors.append({"check": check["id"], "error": "Release or installed-version check failed"})
            checks.append(row)
    if changes:
        state["pending"].append(event(changes))
    state["failed_runs"] = state.get("failed_runs", 0) + 1 if errors else 0
    if errors and state["failed_runs"] >= 3 and not state.get("degraded"):
        ident = hashlib.sha256(datetime.datetime.now(datetime.UTC).isoformat().encode()).hexdigest()[:24]
        state["pending"].append(
            {
                "id": ident,
                "title": "Pharos checks need attention",
                "diagnostic": True,
                "message": "Three consecutive checks were incomplete. Review latest.json for failing check IDs.",
                "delivered": [],
            }
        )
        state["degraded"] = True
    if not errors:
        state["degraded"] = False
    delivery_errors = []

    def persist(value):
        write_json(directory / "state.json", value)

    if not dry_run:
        # Durable outbox precedes every network side effect.
        persist(state)
        if notify:
            delivery_errors = notifications.deliver(state, config.get("notifications", []), persist)
    report = {
        "schema_version": 1,
        "checked_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "dry_run": dry_run,
        "checks": checks,
        "changes": changes,
        "errors": errors,
        "notification_errors": delivery_errors,
        "pending_notifications": len(state["pending"]),
    }
    write_json(directory / ("preview.json" if dry_run else "latest.json"), report)
    return report
