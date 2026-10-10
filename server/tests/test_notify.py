from __future__ import annotations

import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

from loops import notify
from loops.notify import Alert


class MemoryStore:
    def __init__(self, fp: str = "", ts: float = 0.0):
        self.fp, self.ts, self.puts = fp, ts, []

    def get(self, name):
        return self.fp, self.ts

    def put(self, name, fp, ts):
        self.fp, self.ts = fp, ts
        self.puts.append((name, fp, ts))


class FakeTelegram:
    def __init__(self, ok: bool = True):
        self.ok, self.requests = ok, []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = self.rfile.read(int(self.headers["Content-Length"]))
                outer.requests.append((self.path, json.loads(body)))
                data = json.dumps({"ok": outer.ok}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()

    @property
    def base(self):
        return f"http://127.0.0.1:{self.server.server_port}"


LAG = Alert("ingest-lag", "ingest pending lag: 10 note(s), oldest 5.0h")
CRED = Alert("component-graph", "component graph not ready (401)", "operator")


class DecideTests(unittest.TestCase):
    def test_new_set_fires(self):
        self.assertEqual(notify.decide("", 0, "a", 100, 12), "fire")
        self.assertEqual(notify.decide("a", 90, "a,b", 100, 12), "fire")

    def test_same_set_is_quiet_until_repeat(self):
        self.assertIsNone(notify.decide("a", 0, "a", 3600, 12))
        self.assertEqual(notify.decide("a", 0, "a", 12 * 3600, 12), "repeat")

    def test_clear_sends_one_resolved(self):
        self.assertEqual(notify.decide("a", 0, "", 10, 12), "resolved")
        self.assertIsNone(notify.decide("", 0, "", 10, 12))


class NotifyTests(unittest.TestCase):
    def env(self, base):
        return mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "secret-token", "TELEGRAM_CHAT_ID": "42"})

    def test_fire_then_quiet_then_resolved(self):
        store = MemoryStore()
        with FakeTelegram() as tg, self.env(tg.base), mock.patch.object(notify, "TELEGRAM_API", tg.base):
            self.assertEqual(notify.notify("memory-alerts", "memory", [LAG, CRED], store=store, now=1000), "fire")
            self.assertIsNone(notify.notify("memory-alerts", "memory", [LAG, CRED], store=store, now=2000))
            self.assertEqual(notify.notify("memory-alerts", "memory", [], store=store, now=3000), "resolved")
            self.assertIsNone(notify.notify("memory-alerts", "memory", [], store=store, now=4000))
        self.assertEqual(len(tg.requests), 2)
        path, body = tg.requests[0]
        self.assertEqual(path, "/botsecret-token/sendMessage")
        self.assertEqual(body["chat_id"], "42")
        self.assertIn("[lead] ingest pending lag", body["text"])
        self.assertIn("[Diego] component graph", body["text"])
        self.assertIn("resolved", tg.requests[1][1]["text"])
        self.assertEqual(store.fp, "")

    def test_unconfigured_sends_nothing(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(notify.notify("x", "memory", [LAG], store=MemoryStore()))

    def test_failed_send_keeps_state_for_retry(self):
        store = MemoryStore()
        with FakeTelegram(ok=False) as tg, self.env(tg.base), mock.patch.object(notify, "TELEGRAM_API", tg.base):
            self.assertEqual(notify.notify("x", "memory", [LAG], store=store, now=10), "send-failed")
        self.assertEqual(store.puts, [])

    def test_no_store_throttles_frequent_job(self):
        broken = mock.Mock()
        broken.get.side_effect = RuntimeError("neo4j down")
        off_slot = 1 * 3600 + 5 * 60   # 01:05 UTC
        on_slot = 4 * 3600 + 5 * 60    # 04:05 UTC
        with FakeTelegram() as tg, self.env(tg.base), mock.patch.object(notify, "TELEGRAM_API", tg.base):
            self.assertIsNone(notify.notify("x", "memory", [LAG], store=broken, now=off_slot))
            self.assertEqual(notify.notify("x", "memory", [LAG], store=broken, now=on_slot), "fire")
            self.assertEqual(
                notify.notify("x", "memory", [LAG], store=broken, now=off_slot, throttle_without_state=False),
                "fire",
            )
        self.assertEqual(len(tg.requests), 2)

    def test_send_error_does_not_print_token(self):
        with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "secret-token", "TELEGRAM_CHAT_ID": "1"}), \
             mock.patch.object(notify, "TELEGRAM_API", "http://127.0.0.1:9"), \
             mock.patch("builtins.print") as printed:
            self.assertFalse(notify.send_telegram("hi"))
        self.assertNotIn("secret-token", " ".join(str(c) for c in printed.call_args_list))


class ClassifyTests(unittest.TestCase):
    def test_owner_and_key(self):
        self.assertEqual(notify.classify_owner("graph_answer errored: insufficient_quota"), "operator")
        self.assertEqual(notify.classify_owner("ingestion: 25/25 recent notes NOT in graph"), "lead")
        self.assertEqual(notify.alert_key("ingestion: 25/25 recent notes NOT in graph"), "ingestion")
        self.assertEqual(notify.alert_key("write path BROKEN: canary drop failed"), "write-path-broken")


if __name__ == "__main__":
    unittest.main()
