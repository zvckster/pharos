"""Pharos command-line interface."""

import argparse
import json
import os
import signal
import sys
import threading
from pathlib import Path

from . import __version__
from .config import load
from .engine import run
from .storage import locked


def parser():
    root = argparse.ArgumentParser(description="Pharos — stable release alerts without rebuild noise")
    root.add_argument("--version", action="version", version=__version__)
    sub = root.add_subparsers(dest="action", required=True)
    for name in ("check", "watch", "validate", "report"):
        command = sub.add_parser(name)
        command.add_argument("--config", default=os.environ.get("PHAROS_CONFIG", "pharos.json"))
        command.add_argument(
            "--state-dir", default=os.environ.get("PHAROS_STATE_DIR", str(Path.home() / ".local/state/pharos"))
        )
        if name in {"check", "watch"}:
            command.add_argument(
                "--notify", action="store_true", help="Deliver pending notifications to configured channels"
            )
            command.add_argument(
                "--dry-run",
                action="store_true",
                help="Write preview.json without changing state or sending notifications",
            )
            command.add_argument("--force-refresh", action="store_true", help="Bypass the one-hour source cache")
    return root


def main(argv=None):
    os.umask(0o077)
    args = parser().parse_args(argv)
    try:
        if args.action == "report":
            print((Path(args.state_dir) / "latest.json").read_text())
            return 0
        config = load(args.config)
        if args.action == "validate":
            print(
                f"Configuration valid: {len(config['checks'])} checks, {len(config.get('notifications', []))} notification channels"
            )
            return 0
        stop = threading.Event()
        if args.action == "watch":
            for sig in (signal.SIGTERM, signal.SIGINT):
                signal.signal(sig, lambda *_: stop.set())
        result = 0
        while not stop.is_set():
            with locked(args.state_dir):
                report = run(config, args.state_dir, args.dry_run, args.notify, args.force_refresh)
            print(
                json.dumps(
                    {
                        "checked": len(report["checks"]),
                        "new_releases": len(report["changes"]),
                        "errors": len(report["errors"]),
                        "notification_errors": len(report["notification_errors"]),
                        "pending": report["pending_notifications"],
                        "dry_run": args.dry_run,
                    }
                ),
                flush=True,
            )
            result = 1 if report["errors"] or report["notification_errors"] else 0
            if args.action == "check" or stop.wait(config.get("interval_seconds", 21600)):
                return result
            # Reload editable catalogue between checks.
            config = load(args.config)
        return result
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, RecursionError):
        print(
            "Pharos failed: check configuration, state integrity, file permissions or process lock. Existing state was preserved where possible.",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
