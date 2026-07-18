from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("NEO4J_PASSWORD", "test-password")
os.environ.setdefault("OPENAI_API_KEY", "test-only")

import ingest  # noqa: E402


class FakeDriver:
    def __init__(self, episodes: list[dict] | None = None):
        self.episodes = list(episodes or [])
        self.calls: list[tuple[str, dict]] = []
        self.marked: list[str] = []

    async def execute_query(self, query: str, **params):
        self.calls.append((query, params))
        if "RETURN e.name AS slug" in query:
            rows = []
            for episode in reversed(self.episodes):
                if episode["group_id"] != params["group_id"]:
                    continue
                rows.append({
                    "slug": episode["slug"],
                    "uuid": episode["uuid"],
                    "complete": episode.get("complete"),
                    "content_hash": episode.get("content_hash"),
                    "completed_at": episode.get("completed_at"),
                    "created_at": episode.get("created_at", "2026-07-18T00:00:00Z"),
                    "legacy_content": episode.get("content")
                    if not episode.get("content_hash") else None,
                    "mention_count": episode.get("mention_count", 0),
                })
            return rows, None, None
        if "SET e.dipink_ingest_complete = true" in query:
            for episode in self.episodes:
                if episode["uuid"] == params["uuid"] and episode["group_id"] == params["group_id"]:
                    episode["complete"] = True
                    episode["content_hash"] = params["content_hash"]
                    episode["completed_at"] = episode.get("completed_at") or "2026-07-18T12:00:00Z"
                    self.marked.append(episode["uuid"])
                    return [{"uuid": episode["uuid"]}], None, None
            return [], None, None
        if "DETACH DELETE e" in query:
            self.episodes = [
                episode for episode in self.episodes
                if not (
                    episode["uuid"] == params["uuid"]
                    and episode["group_id"] == params["group_id"]
                )
            ]
            return [], None, None
        raise AssertionError(f"unexpected query: {query}")


class FakeGraph:
    def __init__(self, episodes: list[dict] | None = None):
        self.driver = FakeDriver(episodes)
        self.removed: list[str] = []

    async def remove_episode(self, episode_uuid: str):
        self.removed.append(episode_uuid)
        self.driver.episodes = [
            episode for episode in self.driver.episodes
            if episode["uuid"] != episode_uuid
        ]


def make_note(root: Path, slug: str, body: str) -> tuple[datetime, str, Path]:
    folder = root / slug
    folder.mkdir(parents=True)
    path = folder / f"{slug}.md"
    path.write_text(body, encoding="utf-8")
    return datetime(2026, 7, 18, tzinfo=timezone.utc), slug, path


def episode(
    slug: str,
    body: str,
    *,
    uuid: str = "episode-1",
    complete: bool | None = None,
    content_hash: str | None = None,
    mention_count: int = 0,
    group_id: str = "main",
) -> dict:
    return {
        "slug": slug,
        "uuid": uuid,
        "group_id": group_id,
        "complete": complete,
        "content_hash": content_hash,
        "content": body,
        "mention_count": mention_count,
        "created_at": "2026-07-18T00:00:00Z",
    }


class IngestCompletionTests(unittest.TestCase):
    def setUp(self):
        ingest._NOTE_HASH_CACHE.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.slug = "2026-07-18-120000-ingest-fixture"
        self.note = make_note(self.root, self.slug, "current body")

    def tearDown(self):
        self.tmp.cleanup()

    def _fake_add(self, calls: list[str]):
        async def add(g, **kwargs):
            calls.append(kwargs["name"])
            uuid = f"new-{len(calls)}"
            g.driver.episodes.append(episode(
                kwargs["name"],
                kwargs["episode_body"],
                uuid=uuid,
                complete=None,
                content_hash=None,
                mention_count=0,  # valid zero-fact extraction
                group_id=kwargs["group_id"],
            ))
            return SimpleNamespace(episode=SimpleNamespace(uuid=uuid))
        return add

    def test_zero_fact_episode_is_marked_complete_and_ingested_only_once(self):
        async def run() -> None:
            graph = FakeGraph()
            first = await ingest.assess_ingest(graph.driver, [self.note])
            self.assertEqual(first.missing, {self.slug})
            calls: list[str] = []
            with mock.patch.object(ingest, "add_episode_with_retry", side_effect=self._fake_add(calls)):
                await ingest._run_pending_batch(
                    graph, first, [self.note], group_id="main", prefix="test"
                )
                second = await ingest.assess_ingest(graph.driver, [self.note])
                self.assertEqual(second.done, {self.slug})
                self.assertEqual(second.pending, set())
                selected = [note for note in [self.note] if note[1] in second.pending]
                await ingest._run_pending_batch(
                    graph, second, selected, group_id="main", prefix="test"
                )
            self.assertEqual(calls, [self.slug])
            saved = graph.driver.episodes[0]
            self.assertTrue(saved["complete"])
            self.assertEqual(saved["content_hash"], ingest.episode_content_hash("current body"))
            self.assertEqual(saved["mention_count"], 0)

        asyncio.run(run())

    def test_partial_episode_is_removed_then_retried(self):
        async def run() -> None:
            graph = FakeGraph([episode(self.slug, "current body")])
            assessment = await ingest.assess_ingest(graph.driver, [self.note])
            self.assertEqual(assessment.partial, {self.slug})
            calls: list[str] = []
            with mock.patch.object(ingest, "add_episode_with_retry", side_effect=self._fake_add(calls)):
                await ingest._run_pending_batch(
                    graph, assessment, [self.note], group_id="main", prefix="test"
                )
            self.assertEqual(graph.removed, ["episode-1"])
            self.assertEqual(calls, [self.slug])
            final = await ingest.assess_ingest(graph.driver, [self.note])
            self.assertEqual(final.done, {self.slug})
            self.assertEqual(final.partial, set())

        asyncio.run(run())

    def test_changed_note_uses_graphiti_remove_episode_then_readds_once(self):
        async def run() -> None:
            old_hash = ingest.episode_content_hash("old body")
            graph = FakeGraph([episode(
                self.slug,
                "old body",
                complete=True,
                content_hash=old_hash,
            )])
            assessment = await ingest.assess_ingest(graph.driver, [self.note])
            self.assertEqual(assessment.changed, {self.slug})
            calls: list[str] = []
            with mock.patch.object(ingest, "add_episode_with_retry", side_effect=self._fake_add(calls)):
                await ingest._run_pending_batch(
                    graph, assessment, [self.note], group_id="main", prefix="test"
                )
                final = await ingest.assess_ingest(graph.driver, [self.note])
                selected = [note for note in [self.note] if note[1] in final.pending]
                await ingest._run_pending_batch(
                    graph, final, selected, group_id="main", prefix="test"
                )
            self.assertEqual(graph.removed, ["episode-1"])
            self.assertEqual(calls, [self.slug])
            self.assertEqual(final.done, {self.slug})
            self.assertEqual(final.changed, set())

        asyncio.run(run())

    def test_legacy_edge_episode_counts_done_and_is_lazily_upgraded(self):
        async def run() -> None:
            graph = FakeGraph([episode(
                self.slug,
                "current body",
                mention_count=2,
            )])
            assessment = await ingest.assess_ingest(graph.driver, [self.note])
            self.assertEqual(assessment.done, {self.slug})
            self.assertEqual(assessment.legacy_compatible, {self.slug})
            self.assertEqual(assessment.legacy_upgraded, 1)
            self.assertEqual(graph.driver.marked, ["episode-1"])
            self.assertTrue(graph.driver.episodes[0]["complete"])
            self.assertIsNotNone(assessment.as_dict()["ingest_watermark"])

        asyncio.run(run())

    def test_legacy_content_mismatch_stays_compatible_without_reingest(self):
        async def run() -> None:
            graph = FakeGraph([episode(
                self.slug,
                "historical pre-migration body",
                mention_count=2,
            )])
            assessment = await ingest.assess_ingest(graph.driver, [self.note])
            self.assertEqual(assessment.done, {self.slug})
            self.assertEqual(assessment.changed, set())
            self.assertEqual(assessment.pending, set())
            self.assertEqual(assessment.legacy_upgraded, 0)
            self.assertEqual(graph.driver.marked, [])

        asyncio.run(run())

    def test_legacy_upgrade_can_be_disabled_for_read_only_status(self):
        async def run() -> None:
            graph = FakeGraph([episode(
                self.slug,
                "current body",
                mention_count=2,
            )])
            assessment = await ingest.assess_ingest(
                graph.driver,
                [self.note],
                upgrade_legacy=True,
                legacy_upgrade_limit=0,
            )
            self.assertEqual(assessment.done, {self.slug})
            self.assertEqual(assessment.legacy_compatible, {self.slug})
            self.assertEqual(assessment.legacy_upgraded, 0)
            self.assertEqual(graph.driver.marked, [])

        asyncio.run(run())

    def test_unchanged_explicit_episode_is_not_reprocessed(self):
        async def run() -> None:
            graph = FakeGraph([episode(
                self.slug,
                "current body",
                complete=True,
                content_hash=ingest.episode_content_hash("current body"),
            )])
            assessment = await ingest.assess_ingest(graph.driver, [self.note])
            self.assertEqual(assessment.done, {self.slug})
            self.assertEqual(assessment.pending, set())
            self.assertEqual(graph.removed, [])
            self.assertEqual(graph.driver.marked, [])

        asyncio.run(run())

    def test_blocked_queue_is_excluded_from_discovery(self):
        blocked_slug = "2026-07-18-120001-blocked"
        blocked = self.root / ".blocked" / blocked_slug
        blocked.mkdir(parents=True)
        (blocked / f"{blocked_slug}.md").write_text("blocked", encoding="utf-8")
        old_root, old_inbox = ingest.NOTES_ROOT, ingest.INBOX_ROOTS
        ingest.NOTES_ROOT, ingest.INBOX_ROOTS = self.root, []
        try:
            discovered = {slug for _ts, slug, _path in ingest.discover_notes()}
        finally:
            ingest.NOTES_ROOT, ingest.INBOX_ROOTS = old_root, old_inbox
        self.assertEqual(discovered, {self.slug})


if __name__ == "__main__":
    unittest.main()
