from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("NEO4J_PASSWORD", "test-password")
os.environ.setdefault("OPENAI_API_KEY", "test-only")
os.environ.setdefault("WIKI_MCP_EMBED_PROVIDER", "openai")
os.environ.setdefault("WIKI_MCP_BACKGROUND_REINDEX", "0")
os.environ.setdefault("WIKI_ROOT", "/tmp/wiki-mcp-test-global-root")

import httpx  # noqa: E402
import core  # noqa: E402
import server  # noqa: E402
import wiki  # noqa: E402


class FakeRequest:
    def __init__(self, **query):
        self.query_params = query


class BlockingHandlerThreadTests(unittest.TestCase):
    def test_http_search_runs_embedding_and_metrics_off_loop(self):
        loop_thread = threading.current_thread()
        worker_threads: list[threading.Thread] = []

        def fake_impl(query: str, k: int):
            worker_threads.append(threading.current_thread())
            return []

        async def run():
            with mock.patch.object(wiki, "_http_search_impl", side_effect=fake_impl):
                response = await wiki.http_search(FakeRequest(q="hello", k="3"))
            self.assertEqual(response.status_code, 200)

        asyncio.run(run())
        self.assertNotEqual(worker_threads[0], loop_thread)

    def test_http_reindex_runs_git_scan_and_embedding_off_loop(self):
        loop_thread = threading.current_thread()
        worker_threads: list[threading.Thread] = []

        def fake_reindex(index):
            worker_threads.append(threading.current_thread())
            return {"ok": True, "ready": True}

        async def run():
            with mock.patch.object(wiki, "_reindex_once", side_effect=fake_reindex):
                response = await wiki.http_reindex(FakeRequest())
            self.assertEqual(response.status_code, 200)

        asyncio.run(run())
        self.assertNotEqual(worker_threads[0], loop_thread)

    def test_http_metrics_file_read_runs_off_loop(self):
        loop_thread = threading.current_thread()
        worker_threads: list[threading.Thread] = []

        def fake_read(days: float):
            worker_threads.append(threading.current_thread())
            return []

        async def run():
            with mock.patch.object(core, "read_metrics", side_effect=fake_read):
                response = await server.metrics(FakeRequest(days="1"))
            self.assertEqual(response.status_code, 200)

        asyncio.run(run())
        self.assertNotEqual(worker_threads[0], loop_thread)


class EventLoopResponsivenessTests(unittest.TestCase):
    """Require /live to finish before the blocked worker can finish.

    A fixed sleep can end before /live starts after a process pause. A held
    worker proves concurrent progress without a wall-clock latency benchmark.
    """

    CASES = (
        ("/api/search?q=slow", "GET", wiki, "_http_search_impl"),
        ("/api/reindex", "POST", wiki, "_reindex_once"),
        ("/api/metrics?days=1", "GET", core, "read_metrics"),
    )

    async def assert_live_before_worker_release(self, path, method, target, attribute):
        loop = asyncio.get_running_loop()
        loop_thread = threading.current_thread()
        started = asyncio.Event()
        release = threading.Event()
        worker_threads = []

        def blocked(*_args, **_kwargs):
            worker_threads.append(threading.current_thread())
            loop.call_soon_threadsafe(started.set)
            if threading.current_thread() is loop_thread:
                raise AssertionError("worker ran on the event loop")
            # This timeout guards fixture deadlocks, not response latency.
            if not release.wait(10):
                raise AssertionError("fixture did not release the worker")
            if path == "/api/reindex":
                return {"ok": True, "ready": True}
            return []

        transport = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with mock.patch.object(target, attribute, side_effect=blocked):
                request = client.get(path) if method == "GET" else client.post(path)
                slow_task = asyncio.create_task(request)
                try:
                    await asyncio.wait_for(started.wait(), timeout=5)
                    self.assertNotEqual(worker_threads[0], loop_thread, "worker ran on the event loop")
                    self.assertFalse(slow_task.done(), f"worker ended before /live for {path}")
                    live = await asyncio.wait_for(client.get("/live"), timeout=5)
                    # The request must finish while its sibling worker stays held.
                    self.assertFalse(release.is_set())
                    self.assertFalse(slow_task.done(), f"/live waited for the worker at {path}")
                    self.assertEqual(live.status_code, 200)
                finally:
                    release.set()
                    slow_response = await asyncio.wait_for(slow_task, timeout=5)
        self.assertEqual(slow_response.status_code, 200)

    def test_slow_search_reindex_and_metrics_do_not_delay_live(self):
        async def run():
            for case in self.CASES:
                with self.subTest(path=case[0]):
                    await self.assert_live_before_worker_release(*case)

        asyncio.run(run())

    def test_responsiveness_fixture_rejects_inline_workers(self):
        async def inline(function, *args, **kwargs):
            return function(*args, **kwargs)

        for case in self.CASES:
            with self.subTest(path=case[0]), \
                 mock.patch.object(wiki.anyio.to_thread, "run_sync", new=inline), \
                 self.assertRaisesRegex(AssertionError, "worker ran on the event loop"):
                asyncio.run(self.assert_live_before_worker_release(*case))


class BoundedMetricsLogTests(unittest.TestCase):
    def test_query_log_rotates_and_read_is_bounded(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "queries.jsonl"
            with mock.patch.object(core, "METRICS_PATH", path), \
                 mock.patch.object(core, "METRICS_MAX_BYTES", 300), \
                 mock.patch.object(core, "METRICS_BACKUPS", 2):
                now = time.time()
                for index in range(40):
                    core.record_query({
                        "ts": now + index / 1000,
                        "tool": "test",
                        "payload": "x" * 120,
                        "index": index,
                    })

                files = sorted(Path(td).glob("queries.jsonl*"))
                self.assertLessEqual(len(files), 3)
                self.assertTrue(path.exists())
                # Rotation is checked before each append, so a file may exceed
                # the threshold by at most one bounded event.
                self.assertTrue(all(file.stat().st_size < 600 for file in files))
                events = core.read_metrics(1)

            self.assertLessEqual(len(events), 5000)
            self.assertTrue(events)
            self.assertTrue(all(isinstance(event, dict) for event in events))
            self.assertEqual(events, sorted(events, key=lambda event: event["ts"]))


if __name__ == "__main__":
    unittest.main()
