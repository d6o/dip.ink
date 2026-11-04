"""Reproducibility and denominator tests; all evidence is synthetic."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

os.environ.setdefault("NEO4J_PASSWORD", "test-password")
os.environ.setdefault("OPENAI_API_KEY", "test-only")
os.environ.setdefault("WIKI_MCP_BACKGROUND_REINDEX", "0")
import evaluate_answers as evaluation
from evaluation_cases.make_synthetic import build

DATA = Path(__file__).parents[1] / "evaluation_cases" / "synthetic-memory-v1.json"


class EvaluationTests(unittest.TestCase):
    def test_versioned_thirty_case_dataset_matches_builder_and_validates(self):
        data = evaluation.load_dataset(DATA)
        self.assertEqual(data, build())
        self.assertEqual(len(data["cases"]), 30)
        self.assertEqual(len({c["category"] for c in data["cases"]}), 4)
        self.assertTrue(any(len(c["question"]) > 200 for c in data["cases"]))
        for case in data["cases"]:
            with self.subTest(case=case["id"]):
                _, passed = evaluation.fixture(case)
                self.assertTrue(passed)
        self.assertEqual(evaluation.hash_json(data), evaluation.hash_json(json.loads(json.dumps(data, indent=4))))

    def test_abstentions_errors_and_missing_receipts_stay_in_denominator(self):
        data = build(); data["cases"] = data["cases"][:3]
        answers = iter([{"answer": "fixture", "confidence": "high"},
                        {"answer": None, "confidence": "not_found"}, RuntimeError("PRIVATE_FIXTURE")])
        def answer(question):
            value = next(answers)
            if isinstance(value, Exception):
                raise value
            return value
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "capture.jsonl"
            console = io.StringIO()
            with contextlib.redirect_stdout(console), contextlib.redirect_stderr(console):
                result = evaluation.capture(data, output, mode="live", answer=answer)
            self.assertEqual((result["total"], result["answer"], result["abstention"], result["error"]), (3, 1, 1, 1))
            self.assertIsNone(result["human_supported_fraction_of_all"])
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            self.assertNotIn("PRIVATE_FIXTURE", output.read_text() + console.getvalue())
            records = [json.loads(line) for line in output.read_text().splitlines()]
            self.assertEqual(records[1]["question"], data["cases"][0]["question"])
            partial = evaluation.summary(records[0], records[1:2])
            self.assertEqual(partial["missing"], 2)
            review = {"dataset_sha256": records[0]["dataset_sha256"], "run_id": records[0]["run_id"],
                      "verdicts": {records[1]["id"]: "supported"}}
            self.assertEqual(evaluation.summary(records[0], records[1:], review)["human_supported_fraction_of_all"], 1/3)
            review["run_id"] = "wrong"
            with self.assertRaises(ValueError):
                evaluation.summary(records[0], records[1:], review)
            with self.assertRaises(FileExistsError):
                evaluation.capture(data, output, mode="live", answer=answer)

    def test_mcp_sends_full_questions_in_body_with_user_agent_and_no_write_tool(self):
        calls = []
        question = "A full fixture question " + "context " * 40
        def open_request(request, **kwargs):
            body = json.loads(request.data)
            calls.append((request, body))
            result = ({"structuredContent": {"answer": "fixture", "confidence": "medium", "sources": []}}
                      if body["method"] == "tools/call" else {})
            response = io.BytesIO(json.dumps({"jsonrpc": "2.0", "id": body.get("id"), "result": result}).encode())
            response.headers = {}
            return response
        client = evaluation.MemoryClient("https://memory.example.invalid/mcp")
        client.opener = Mock(open=open_request)
        client.initialize(); client.answer(question)
        self.assertEqual(calls[-1][1]["params"], {"name": "graph_answer", "arguments": {"question": question}})
        self.assertEqual(len(calls), 3)
        for request, body in calls:
            self.assertEqual(request.get_header("User-agent"), evaluation.USER_AGENT)
            self.assertNotIn(question, request.full_url)

    def test_duplicate_receipts_cannot_inflate_denominator(self):
        metadata = {"case_count": 2, "case_ids": ["a", "b"]}
        with self.assertRaises(ValueError):
            evaluation.summary(metadata, [{"id": "a", "status": "answer"}] * 2)
