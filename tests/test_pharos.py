import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from pharos import config, detectors, engine, notifications, sources
from pharos.cli import main
from pharos.storage import locked, read_state, write_json
from pharos.version import canonical, newer

CHECK = {
    "id": "app",
    "name": "App",
    "source": {"type": "github", "repo": "owner/app"},
    "installed": {"type": "static", "version": "1.0.0"},
}
RELEASE = {"version": "1.1.0", "tag": "v1.1.0", "url": "https://example.org/releases/1.1.0"}


class Versions(unittest.TestCase):
    def test_calendar_and_packaging(self):
        for value, expected in [
            ("v2.3.4", "2.3.4"),
            ("26.07.27.01", "26.7.27.1"),
            ("1.2.3-ls25", "1.2.3"),
            ("2.3.4+build3", "2.3.4"),
        ]:
            self.assertEqual(canonical(value), expected)

    def test_unstable_and_missing(self):
        for value in [None, "main", "latest", "2.0.0-rc.1", "2.0.0beta2", "2.0.0-nightly", "1.3.0-preview.1"]:
            self.assertIsNone(canonical(value))

    def test_numeric_comparison(self):
        self.assertTrue(newer("2.10.0", "2.9.9"))
        self.assertFalse(newer("2.0.0", "2.0"))


class Sources(unittest.TestCase):
    def test_highest_stable_not_feed_order(self):
        data = [
            {"tag_name": "v15.1.1"},
            {"tag_name": "v17.0.1"},
            {"tag_name": "v18.0.0-rc1"},
            {"tag_name": "v19.0.0", "draft": True},
            {"tag_name": "v20.0.0", "prerelease": True},
        ]
        self.assertEqual(sources.parse({"type": "forgejo"}, data)["version"], "17.0.1")

    def test_major_filter(self):
        self.assertEqual(
            sources.parse({"type": "github", "major": 2}, [{"tag_name": "v3.0.0"}, {"tag_name": "v2.10.0"}])["version"],
            "2.10.0",
        )

    def test_json_custom_field(self):
        self.assertEqual(
            sources.parse(
                {"type": "json", "version_field": "version"}, {"version": "v1.3.0", "url": "https://example.org"}
            )["version"],
            "1.3.0",
        )

    def test_no_stable_is_error(self):
        with self.assertRaises(ValueError):
            sources.parse({"type": "github"}, [{"tag_name": "nightly"}])

    def test_cache_and_etag(self):
        source = {"type": "github", "repo": "owner/app"}
        with patch.object(sources, "fetch", return_value=([{"tag_name": "v1.1.0"}], '"etag"')) as fetch:
            release, cache = sources.latest(source, {})
            self.assertEqual(sources.latest(source, cache)[0], release)
            self.assertEqual(fetch.call_count, 1)
        with patch.object(sources, "fetch", return_value=(None, '"etag"')) as fetch:
            self.assertEqual(sources.latest(source, cache, force=True)[0], release)
            self.assertEqual(fetch.call_args.args[1], '"etag"')

    def test_redirect_strips_credentials(self):
        import urllib.request

        request = urllib.request.Request("https://a.example.org", headers={"Authorization": "Bearer secret"})
        redirected = sources.SafeRedirect().redirect_request(request, None, 302, "", {}, "https://b.example.org/path")
        self.assertIsNone(redirected.get_header("Authorization"))
        with self.assertRaises(ValueError):
            sources.SafeRedirect().redirect_request(request, None, 302, "", {}, "http://a.example.org")


class Observations(unittest.TestCase):
    def setUp(self):
        self.state = {"checks": {}}

    def observe(self, version, installed="1.0.0", check=None):
        return engine.observe(self.state, check or CHECK, {**RELEASE, "version": version}, installed)

    def test_quiet_baseline_and_rebuild(self):
        self.assertIsNone(self.observe("1.1.0"))
        self.assertIsNone(self.observe("1.1.0"))

    def test_once_and_rollback(self):
        self.observe("1.0.0")
        self.assertIsNotNone(self.observe("1.1.0"))
        self.assertIsNone(self.observe("1.0.0"))
        self.assertIsNone(self.observe("1.1.0"))

    def test_installed_newer_and_report_only(self):
        self.observe("1.0.0")
        self.assertIsNone(self.observe("1.1.0", "1.1.0"))
        self.assertIsNone(self.observe("1.2.0", "2.0.0"))
        self.assertIsNone(self.observe("1.3.0", check={**CHECK, "notify": False}))

    def test_source_change_rebaselines(self):
        self.observe("1.0.0")
        self.assertIsNone(self.observe("9.0.0", check={**CHECK, "source": {"type": "github", "repo": "different/app"}}))

    def test_unknown_installed_can_announce(self):
        self.observe("1.0.0")
        self.assertIsNotNone(self.observe("1.1.0", None))


class StateAndEngine(unittest.TestCase):
    def test_lock_excludes_overlap(self):
        with tempfile.TemporaryDirectory() as directory, locked(directory):
            with self.assertRaises(RuntimeError), locked(directory):
                pass

    def test_corrupt_state_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text('{"schema_version": 99}')
            with self.assertRaises(ValueError):
                read_state(directory)
            self.assertEqual(path.read_text(), '{"schema_version": 99}')

    def test_dry_run_no_state_or_delivery(self):
        with tempfile.TemporaryDirectory() as directory:
            state = read_state(directory)
            engine.observe(state, CHECK, {**RELEASE, "version": "1.0.0"}, "1.0.0")
            path = Path(directory) / "state.json"
            write_json(path, state)
            before = path.read_bytes()
            with (
                patch.object(engine, "inspect", return_value=(RELEASE, "1.0.0", {})),
                patch.object(notifications, "send") as send,
            ):
                report = engine.run({"checks": [CHECK]}, directory, dry_run=True, notify=True)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(len(report["changes"]), 1)
            self.assertTrue((Path(directory) / "preview.json").exists())
            send.assert_not_called()

    def test_error_not_current_and_no_baseline_consumed(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(engine, "inspect", side_effect=RuntimeError("private-token")),
        ):
            report = engine.run({"checks": [CHECK]}, directory)
            self.assertEqual(report["checks"][0]["status"], "error")
            self.assertNotIn("private-token", json.dumps(report))
            self.assertEqual(read_state(directory)["checks"], {})

    def test_grouped_durable_outbox_and_delivery(self):
        with tempfile.TemporaryDirectory() as directory:
            state = read_state(directory)
            for check in [CHECK, {**CHECK, "id": "second"}]:
                engine.observe(state, check, {**RELEASE, "version": "1.0.0"}, "1.0.0")
            write_json(Path(directory) / "state.json", state)

            def send(channel, event):
                durable = read_state(directory)
                self.assertEqual(durable["pending"][0]["id"], event["id"])

            with (
                patch.object(engine, "inspect", return_value=(RELEASE, "1.0.0", {})),
                patch.object(notifications, "send", side_effect=send) as deliver,
            ):
                report = engine.run(
                    {"checks": [CHECK, {**CHECK, "id": "second"}], "notifications": [{"id": "phone", "type": "ntfy"}]},
                    directory,
                    notify=True,
                )
                self.assertEqual(deliver.call_count, 1)
            self.assertEqual(len(report["changes"]), 2)
            self.assertEqual(report["pending_notifications"], 0)

    def test_degraded_alert_once_per_incident(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(engine, "inspect", side_effect=RuntimeError()):
            for _ in range(4):
                engine.run({"checks": [CHECK]}, directory)
            self.assertEqual(len(read_state(directory)["pending"]), 1)


class Notifications(unittest.TestCase):
    def test_partial_retry(self):
        state = {"pending": [engine.event([{**RELEASE, "id": "app", "name": "App", "installed": "1.0.0"}])]}
        channels = [{"id": "phone"}, {"id": "mail"}]
        calls = []

        def send(channel, event):
            calls.append(channel["id"])
            if calls == ["phone", "mail"]:
                raise RuntimeError("secret")

        with patch.object(notifications, "send", side_effect=send):
            errors = notifications.deliver(state, channels, lambda _: None)
            self.assertNotIn("secret", json.dumps(errors))
            self.assertEqual(state["pending"][0]["delivered"], ["phone"])
            notifications.deliver(state, channels, lambda _: None)
        self.assertEqual(calls, ["phone", "mail", "mail"])
        self.assertEqual(state["pending"], [])

    def test_no_channels_preserves_outbox(self):
        state = {"pending": [{"delivered": []}]}
        notifications.deliver(state, [], lambda _: None)
        self.assertEqual(len(state["pending"]), 1)

    def test_unicode_ntfy_and_idempotency_header(self):
        channel = {"type": "ntfy", "url": "https://ntfy.example.org", "topic": "releases"}
        event = engine.event([{**RELEASE, "id": "app", "name": "应用", "installed": "1.0.0"}])
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value.status = 200
        with patch("urllib.request.build_opener", return_value=opener):
            notifications.send(channel, event)
        request = opener.open.call_args.args[0]
        self.assertEqual(json.loads(request.data)["title"], event["title"])
        self.assertEqual(request.get_header("Idempotency-key"), event["id"])

    def test_missing_secret(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(ValueError):
            notifications.secret({"token_env": "MISSING_SECRET"}, "token")


class DetectionAndConfig(unittest.TestCase):
    def test_dpkg_epoch(self):
        with patch.object(detectors, "command", return_value="2:1.20.3-1ubuntu2"):
            self.assertEqual(detectors.detect({"type": "dpkg", "package": "example"}), "1.20.3")

    def test_command_pattern(self):
        with patch.object(detectors, "command", return_value="app v1.2.3 (runtime 4.0.0)"):
            self.assertEqual(
                detectors.detect({"type": "command", "argv": ["app", "--version"], "pattern": r"app v(\d+\.\d+\.\d+)"}),
                "1.2.3",
            )

    def test_docker_missing_labels_unknown(self):
        values = [
            json.dumps([{"Image": "sha256:abc", "Config": {"Image": "app:latest"}}]),
            json.dumps([{"Config": {"Labels": {}}}]),
        ]
        with patch.object(detectors, "command", side_effect=values):
            self.assertIsNone(detectors.detect({"type": "docker", "container": "app"}))

    def test_validate_duplicates_and_insecure_feed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            for checks in [[CHECK, CHECK], [{**CHECK, "source": {"type": "json", "url": "http://example.org"}}]]:
                path.write_text(json.dumps({"schema_version": 1, "checks": checks}))
                with self.assertRaises(ValueError):
                    config.load(path)

    def test_malformed_nested_config_and_pattern(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            for check in [
                None,
                {**CHECK, "source": []},
                {**CHECK, "installed": []},
                {**CHECK, "installed": {"type": "command", "argv": ["app"], "pattern": "no-capture"}},
            ]:
                path.write_text(json.dumps({"schema_version": 1, "checks": [check]}))
                with self.assertRaises(ValueError):
                    config.load(path)

    def test_example_valid(self):
        self.assertEqual(len(config.load(Path(__file__).parents[1] / "examples/pharos.json")["checks"]), 3)

    def test_cli_end_to_end(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"schema_version": 1, "checks": [CHECK]}))
            with patch.object(engine, "inspect", return_value=(RELEASE, "1.0.0", {})):
                self.assertEqual(main(["check", "--config", str(path), "--state-dir", directory]), 0)
            self.assertTrue((Path(directory) / "latest.json").exists())


if __name__ == "__main__":
    unittest.main()
