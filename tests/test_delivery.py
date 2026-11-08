"""Delivery contract tests: all HTTP traffic stays on a local test receiver."""

import json
import os
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import MagicMock, patch

from pharos import engine, notifications


class DeliveryContracts(unittest.TestCase):
    def setUp(self):
        self.received = []
        self.status = 200
        owner = self

        class Receiver(BaseHTTPRequestHandler):
            def do_POST(self):
                owner.received.append(
                    (self.path, dict(self.headers), json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                )
                self.send_response(owner.status)
                if owner.status == 302:
                    self.send_header("Location", "/should-not-follow")
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.event = engine.event(
            [
                {
                    "id": "app",
                    "name": "App",
                    "installed": "1.0.0",
                    "version": "1.1.0",
                    "url": "https://example.org/release",
                }
            ]
        )

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_webhook_payload_and_bearer(self):
        with patch.dict(os.environ, {"TEST_TOKEN": "test-only-secret"}):
            notifications.send({"type": "webhook", "url": self.url, "token_env": "TEST_TOKEN"}, self.event)
        _, headers, payload = self.received[0]
        self.assertEqual(headers["Authorization"], "Bearer test-only-secret")
        self.assertEqual(headers["Idempotency-Key"], self.event["id"])
        self.assertEqual(payload["schema_version"], 1)
        self.assertNotIn("delivered", payload)
        self.assertNotIn("test-only-secret", json.dumps(payload))

    def test_gotify_uses_header_not_query_secret(self):
        with patch.dict(os.environ, {"TEST_TOKEN": "test-only-secret"}):
            notifications.send({"type": "gotify", "url": self.url, "token_env": "TEST_TOKEN"}, self.event)
        path, headers, payload = self.received[0]
        self.assertEqual(path, "/message")
        self.assertEqual(headers["X-Gotify-Key"], "test-only-secret")
        self.assertEqual(payload["priority"], 3)

    def test_ntfy_json_contract(self):
        notifications.send({"type": "ntfy", "url": self.url, "topic": "releases"}, self.event)
        self.assertEqual(self.received[0][2]["topic"], "releases")

    def test_failure_and_redirect_do_not_ack(self):
        for status in (503, 302):
            self.status = status
            with self.assertRaises(urllib.error.HTTPError):
                notifications.send({"type": "webhook", "url": self.url}, self.event)
        self.assertEqual(len(self.received), 2)

    def test_smtp_starttls_and_auth(self):
        channel = {
            "type": "email",
            "host": "smtp.example.org",
            "from": "pharos@example.org",
            "to": ["test@example.org"],
            "username": "test",
            "password_env": "TEST_PASSWORD",
        }
        client = MagicMock()
        client.__enter__.return_value = client
        client.send_message.return_value = {}
        with (
            patch.dict(os.environ, {"TEST_PASSWORD": "test-only-password"}),
            patch("smtplib.SMTP", return_value=client) as connect,
        ):
            notifications.send(channel, self.event)
        connect.assert_called_once_with("smtp.example.org", 587, timeout=20)
        client.starttls.assert_called_once()
        client.login.assert_called_once_with("test", "test-only-password")
        self.assertIn(self.event["id"], client.send_message.call_args.args[0]["Message-ID"])

    def test_smtp_partial_failure_is_retryable(self):
        client = MagicMock()
        client.__enter__.return_value = client
        client.send_message.return_value = {"refused@example.org": (450, b"try later")}
        with patch("smtplib.SMTP_SSL", return_value=client), self.assertRaises(RuntimeError):
            notifications.send(
                {
                    "type": "email",
                    "host": "smtp.example.org",
                    "security": "tls",
                    "from": "pharos@example.org",
                    "to": ["test@example.org"],
                },
                self.event,
            )
