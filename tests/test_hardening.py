"""Fault injection and regressions found during the 0.1.1 audit."""

import json
import random
import sys
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

from test_pharos import CHECK, RELEASE

from pharos import detectors, engine, notifications, sources
from pharos.cli import main
from pharos.storage import read_state, write_json
from pharos.version import canonical


class VersionRegression(unittest.TestCase):
    def test_abbreviated_prereleases_are_not_stable(self):
        for tag in ["1.2.3a1", "1.2.3b2", "1.2.3c1", "1.2.3-m1", "1.2.3-milestone1", "v1.2.3~rc1", "v1.2.3-unknown"]:
            with self.subTest(tag=tag):
                self.assertIsNone(canonical(tag))

    def test_more_than_four_components_not_partially_parsed(self):
        for tag in ["1.2.3.4.5", "v1.2.3.4.5.6", "12.34.56.78.90"]:
            self.assertIsNone(canonical(tag))

    def test_build_metadata_does_not_imply_prerelease(self):
        self.assertEqual(canonical("v1.2.3+preview.1"), "1.2.3")
        self.assertEqual(canonical("v1.2.3+build.4.5.6"), "1.2.3")

    def test_ambiguous_output_requires_explicit_pattern(self):
        self.assertIsNone(canonical("app 1.2.3; runtime 4.5.6"))

    def test_normalization_property(self):
        rng = random.Random(20261108)
        for _ in range(1000):
            parts = [rng.randrange(10000) for _ in range(rng.randrange(2, 5))]
            expected = ".".join(map(str, parts))
            self.assertEqual(canonical("v" + expected), expected)
            self.assertEqual(canonical(canonical(expected)), expected)
            self.assertIsNone(canonical(expected + "-rc1"))

    def test_known_packaging_versions(self):
        for tag in ["1.2.3-ls25", "1.2.3-alpine", "1.2.3-1ubuntu2", "1.2.3-deb12"]:
            self.assertEqual(canonical(tag), "1.2.3")


class ProcessRegression(unittest.TestCase):
    def test_stderr_version_fallback(self):
        argv = [sys.executable, "-c", 'import sys; print("app 1.2.3", file=sys.stderr)']
        self.assertEqual(detectors.detect({"type": "command", "argv": argv, "pattern": r"app ([0-9.]+)"}), "1.2.3")

    def test_stdout_preferred_to_warning(self):
        argv = [sys.executable, "-c", 'import sys; print("1.2.3"); print("warning 9.0.0", file=sys.stderr)']
        self.assertEqual(detectors.command(argv), "1.2.3")

    def test_stdout_and_stderr_limits(self):
        for stream in ["stdout", "stderr"]:
            argv = [sys.executable, "-c", f'import sys; sys.{stream}.write("x" * 1000000)']
            with patch.object(detectors, "OUTPUT_LIMIT", 4096), self.assertRaisesRegex(ValueError, "output limit"):
                detectors.command(argv)

    def test_timeout_reaps_child(self):
        started = time.monotonic()
        with patch.object(detectors, "COMMAND_TIMEOUT", 0.15), self.assertRaisesRegex(ValueError, "timed out"):
            detectors.command([sys.executable, "-c", "import time; time.sleep(30)"])
        self.assertLess(time.monotonic() - started, 3)

    def test_timeout_kills_descendants_holding_pipes(self):
        argv = [
            sys.executable,
            "-c",
            'import subprocess,sys; subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"])',
        ]
        started = time.monotonic()
        with patch.object(detectors, "COMMAND_TIMEOUT", 0.2), self.assertRaisesRegex(ValueError, "timed out"):
            detectors.command(argv)
        self.assertLess(time.monotonic() - started, 3)

    def test_nonzero_and_missing_command(self):
        for argv in [[sys.executable, "-c", "raise SystemExit(1)"], ["/pharos-nonexistent-command"]]:
            with self.assertRaises(ValueError):
                detectors.command(argv)


class SourceRegression(unittest.TestCase):
    def test_null_links_are_optional(self):
        self.assertEqual(
            sources.parse({"type": "gitlab"}, [{"tag_name": "v1.2.3", "_links": None}])["version"], "1.2.3"
        )

    def test_future_clock_does_not_keep_cache_forever(self):
        source = {"type": "github", "repo": "owner/app"}
        with patch.object(sources, "fetch", return_value=([{"tag_name": "v1.0.0"}], None)):
            _, cache = sources.latest(source, {})
        cache["checked_at"] = time.time() + 100000
        with patch.object(sources, "fetch", return_value=([{"tag_name": "v1.1.0"}], None)) as fetch:
            self.assertEqual(sources.latest(source, cache)[0]["version"], "1.1.0")
            fetch.assert_called_once()

    def test_fetch_http_errors_do_not_leak_url(self):
        error = urllib.error.HTTPError("https://private.example/token-secret", 429, "secret", {}, None)
        opener = MagicMock()
        opener.open.side_effect = error
        with (
            patch("urllib.request.build_opener", return_value=opener),
            self.assertRaisesRegex(ValueError, "HTTP 429") as caught,
        ):
            sources.fetch("https://example.org")
        self.assertNotIn("secret", str(caught.exception))

    def test_http_failure_closes_response_body(self):
        import io

        response_body = io.BytesIO(b"remote error")
        error = urllib.error.HTTPError("https://example.org", 429, "", {}, response_body)
        opener = MagicMock()
        opener.open.side_effect = error
        with patch("urllib.request.build_opener", return_value=opener), self.assertRaises(ValueError):
            sources.fetch("https://example.org")
        self.assertTrue(response_body.closed)

    def test_fetch_not_modified(self):
        opener = MagicMock()
        opener.open.side_effect = urllib.error.HTTPError("https://example.org", 304, "", {}, None)
        with patch("urllib.request.build_opener", return_value=opener):
            self.assertEqual(sources.fetch("https://example.org", "etag"), (None, "etag"))

    def test_fetch_oversized_body(self):
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value.read.return_value = b"x" * 20
        with (
            patch("urllib.request.build_opener", return_value=opener),
            patch.object(sources, "LIMIT", 10),
            self.assertRaisesRegex(ValueError, "exceeds"),
        ):
            sources.fetch("https://example.org")


class StateRegression(unittest.TestCase):
    def test_malformed_state_never_overwritten(self):
        variants = [[], None, 1, True, {"schema_version": True, "checks": {}, "pending": [], "sources": {}}]
        base = {"schema_version": 1, "checks": {}, "pending": [], "sources": {}}
        variants += [
            {**base, "checks": {"app": []}},
            {**base, "pending": [None]},
            {**base, "sources": {"app": {}}},
            {**base, "failed_runs": "three"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            for state in variants:
                with self.subTest(state=state):
                    path.write_text(json.dumps(state))
                    before = path.read_bytes()
                    with self.assertRaises(ValueError):
                        read_state(directory)
                    self.assertEqual(path.read_bytes(), before)

    def test_atomic_replace_failure_preserves_previous_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            write_json(path, {"old": True})
            before = path.read_bytes()
            with patch("os.replace", side_effect=OSError("disk failed")), self.assertRaises(OSError):
                write_json(path, {"new": True})
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual([p.name for p in Path(directory).iterdir()], ["state.json"])

    def test_private_state_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            write_json(path, {"private": True})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_cli_malformed_state_exits_cleanly(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "config.json").write_text(json.dumps({"schema_version": 1, "checks": [CHECK]}))
            (path / "state.json").write_text("[]")
            self.assertEqual(main(["check", "--config", str(path / "config.json"), "--state-dir", directory]), 2)


class OutboxRegression(unittest.TestCase):
    def test_removed_channel_cannot_drop_pending_delivery(self):
        state = {
            "pending": [
                {
                    **engine.event([{**RELEASE, "id": "app", "name": "App"}]),
                    "targets": ["phone", "mail"],
                    "delivered": ["phone"],
                }
            ]
        }
        with patch.object(notifications, "send") as send:
            errors = notifications.deliver(state, [{"id": "phone"}], lambda _: None)
            self.assertEqual(len(state["pending"]), 1)
            self.assertEqual(errors[0]["channel"], "mail")
            send.assert_not_called()
            notifications.deliver(state, [{"id": "phone"}, {"id": "mail"}], lambda _: None)
            self.assertEqual(send.call_count, 1)
            self.assertEqual(state["pending"], [])

    def test_new_channel_not_added_to_existing_event(self):
        state = {"pending": [{**engine.event([{**RELEASE, "id": "app", "name": "App"}]), "targets": ["phone"]}]}
        with patch.object(notifications, "send") as send:
            notifications.deliver(state, [{"id": "phone"}, {"id": "new"}], lambda _: None)
            self.assertEqual(send.call_count, 1)

    def test_rollback_cannot_announce_below_historical_high(self):
        state = {"checks": {}}
        for version in ["1.0.0", "1.3.0", "1.0.0"]:
            engine.observe(state, CHECK, {**RELEASE, "version": version}, "1.0.0")
        self.assertIsNone(engine.observe(state, CHECK, {**RELEASE, "version": "1.2.0"}, "1.0.0"))
        self.assertIsNotNone(engine.observe(state, CHECK, {**RELEASE, "version": "1.4.0"}, "1.0.0"))

    def test_different_sources_have_distinct_event_ids(self):
        state = {"checks": {}}
        engine.observe(state, CHECK, {**RELEASE, "version": "1.0.0"}, "1.0.0")
        first = engine.observe(state, CHECK, RELEASE, "1.0.0")
        other = {**CHECK, "source": {"type": "github", "repo": "other/app"}}
        engine.observe(state, other, {**RELEASE, "version": "1.0.0"}, "1.0.0")
        second = engine.observe(state, other, RELEASE, "1.0.0")
        self.assertNotEqual(engine.event([first])["id"], engine.event([second])["id"])


class ConfigRegression(unittest.TestCase):
    def test_malformed_types_fail_validation_without_traceback(self):
        from pharos.config import load

        base = {"schema_version": 1, "checks": [CHECK]}
        variants = [[], None, 1, True, {**base, "schema_version": True}]
        mutations = [
            ("source", {"type": []}),
            ("source", {"type": "github", "repo": []}),
            ("source", {"type": "github", "repo": "owner/app", "token_env": 123}),
            ("installed", {"type": "command", "argv": [1]}),
            ("installed", {"type": "command", "argv": ["app\x00"]}),
            ("installed", {"type": "docker", "container": "app", "use_tag": "false"}),
            ("installed", {"type": "static", "version": []}),
        ]
        variants += [{**base, "checks": [{**CHECK, field: value}]} for field, value in mutations]
        channels = [
            None,
            {"id": [], "type": "ntfy"},
            {"id": "x", "type": []},
            {"id": "x", "type": "gotify", "url": "https://example.org"},
            {"id": "x", "type": "ntfy", "url": "https://example.org:wrong"},
            {"id": "x", "type": "ntfy", "url": "https://example.org", "token_env": []},
            {"id": "x", "type": "email", "host": "example.org", "from": "pharos@example.org", "to": [123]},
        ]
        variants += [{**base, "notifications": [channel]} for channel in channels]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            for variant in variants:
                with self.subTest(variant=variant):
                    path.write_text(json.dumps(variant))
                    with self.assertRaises(ValueError):
                        load(path)

    def test_unsafe_urls_rejected(self):
        from pharos.config import https_url

        for url in [
            None,
            123,
            "http://example.org",
            "https://user:secret@example.org",
            "https://example.org:0",
            "https://example.org:65536",
            "https://[bad",
            "https://example.org\n/path",
            "https://example.org/#fragment",
        ]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                https_url(url)


class CrashRegression(unittest.TestCase):
    def test_persist_failure_before_send_has_no_network_side_effect(self):
        state = {"pending": [engine.event([{**RELEASE, "id": "app", "name": "App"}])]}
        with patch.object(notifications, "send") as send, self.assertRaises(OSError):
            notifications.deliver(state, [{"id": "phone"}], lambda _: (_ for _ in ()).throw(OSError()))
        send.assert_not_called()

    def test_acknowledgement_persist_failure_stops_delivery(self):
        state = {"pending": [{**engine.event([{**RELEASE, "id": "app", "name": "App"}]), "targets": ["phone", "mail"]}]}
        with patch.object(notifications, "send") as send, self.assertRaises(OSError):
            notifications.deliver(state, [{"id": "phone"}, {"id": "mail"}], lambda _: (_ for _ in ()).throw(OSError()))
        self.assertEqual(send.call_count, 1)


class NotificationResourceRegression(unittest.TestCase):
    def test_http_error_body_closed(self):
        import io

        response_body = io.BytesIO(b"failure")
        error = urllib.error.HTTPError("https://example.org", 503, "", {}, response_body)
        opener = MagicMock()
        opener.open.side_effect = error
        with patch("urllib.request.build_opener", return_value=opener), self.assertRaises(urllib.error.HTTPError):
            notifications.send(
                {"type": "webhook", "url": "https://example.org"},
                engine.event([{**RELEASE, "id": "app", "name": "App"}]),
            )
        self.assertTrue(response_body.closed)


class PackagingRegression(unittest.TestCase):
    def test_dpkg_dotted_distribution_revision(self):
        with patch.object(detectors, "command", return_value="5:29.9.3-1~ubuntu.24.04~noble"):
            self.assertEqual(detectors.detect({"type": "dpkg", "package": "docker-ce"}), "29.9.3")

    def test_packaging_suffix_can_have_dotted_version(self):
        self.assertEqual(canonical("18.6-alpine3.24"), "18.6")
        self.assertEqual(canonical("1.2.3-0ubuntu1.24.04.1"), "1.2.3")

    def test_numeric_semver_prerelease_not_a_source_release(self):
        with self.assertRaises(ValueError):
            sources.parse({"type": "json"}, {"tag_name": "v2.0.0-1"})


class UpgradeRegression(unittest.TestCase):
    def test_old_parser_cache_is_refetched(self):
        import hashlib

        source = {"type": "github", "repo": "owner/app"}
        old_id = hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()
        old_cache = {
            "identity": old_id,
            "checked_at": time.time(),
            "release": {**RELEASE, "version": "9.0.0", "tag": "9.0.0a1"},
        }
        with patch.object(sources, "fetch", return_value=([{"tag_name": "v1.2.3"}], None)) as fetch:
            self.assertEqual(sources.latest(source, old_cache)[0]["version"], "1.2.3")
            fetch.assert_called_once()

    def test_old_observations_rebaseline_quietly(self):
        import hashlib

        identity = hashlib.sha256(json.dumps(CHECK["source"], sort_keys=True).encode()).hexdigest()
        state = {"checks": {"app": {"identity": identity, "latest": "9.0.0", "seen": ["9.0.0"]}}}
        self.assertIsNone(engine.observe(state, CHECK, RELEASE, "1.0.0"))
        self.assertEqual(state["checks"]["app"]["seen"], ["1.1.0"])

    def test_legacy_pending_event_binds_without_repeating_acks(self):
        state = {"pending": [{**engine.event([{**RELEASE, "id": "app", "name": "App"}]), "delivered": ["phone"]}]}
        with patch.object(notifications, "send") as send:
            notifications.deliver(state, [{"id": "phone"}, {"id": "mail"}], lambda _: None)
            self.assertEqual(send.call_count, 1)
            self.assertEqual(send.call_args.args[0]["id"], "mail")
            self.assertEqual(state["pending"], [])

    def test_malformed_optional_links_do_not_hide_valid_release(self):
        self.assertEqual(
            sources.parse({"type": "gitlab"}, [{"tag_name": "v1.2.3", "_links": ["unexpected"]}])["version"], "1.2.3"
        )


class FinalReviewRegression(unittest.TestCase):
    def test_static_extraction_pattern_validates_and_detects(self):
        from pharos.config import load

        detector = {"type": "static", "version": "app 1.2.3 runtime 4.5.6", "pattern": r"app ([0-9.]+)"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"schema_version": 1, "checks": [{**CHECK, "installed": detector}]}))
            load(path)
            self.assertEqual(detectors.detect(detector), "1.2.3")

    def test_missing_pending_url_rejected_before_delivery(self):
        state = {
            "schema_version": 1,
            "checks": {},
            "sources": {},
            "pending": [engine.event([{**RELEASE, "id": "app", "name": "App"}])],
        }
        del state["pending"][0]["changes"][0]["url"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            write_json(path, state)
            before = path.read_bytes()
            with self.assertRaises(ValueError):
                read_state(directory)
            self.assertEqual(path.read_bytes(), before)
