"""Synthetic model evidence, isolated from diagnostic notes and real credentials."""
import os
import unittest
from unittest import mock

os.environ.setdefault("NEO4J_PASSWORD", "test-password")
os.environ.setdefault("OPENAI_API_KEY", "test-only")
os.environ.setdefault("WIKI_MCP_BACKGROUND_REINDEX", "0")
import graph

MODEL_SOURCE = "2026-10-03-120000-image-model-example"
DEPLOY_SOURCE = "2026-10-04-120000-deployment-example"
UUID = "12345678-1234-1234-1234-123456789abc"
QUESTION = "Where is Diego's OpenRouter API key stored in Infisical, and which OpenRouter image-generation models and request format have worked in his past projects?"


def packet(text, slug=MODEL_SOURCE):
    return {"facts": [], "entities": [], "semantic_notes": [],
            "source_excerpt": {"slug": slug, "content": text}}


def draft(text, sources=None):
    return {"answer": text, "confidence": "high", "sources": sources or [MODEL_SOURCE], "escalate": False}


class ImageModelSnapshotTests(unittest.IsolatedAsyncioTestCase):
    def test_exact_generic_fixture_does_not_request_deployment_qualification(self):
        text = "The image model used was vendor/model-v4.1-vector."
        self.assertIsNone(graph._snapshot_dates("Which image model worked?", text, packet(text), [MODEL_SOURCE]))
        result, _, action = graph._validate_distilled_answer(draft(text), packet(text), "Which image model worked?")
        self.assertEqual(result["answer"], text)
        self.assertEqual(action, "accepted")

    def test_plain_image_generation_and_package_paths_do_not_imply_deployment(self):
        for identifier in ("vendor/model-v4.1-vector", "provider/model-v2.7-beta", "vendor/model:4.1",
                           "release/model-v4.1", "vendor/worker-v4.1-vector", "package-v4.1.tar.gz",
                           "/opt/models/vendor/model-v4.1-vector.json"):
            with self.subTest(identifier=identifier):
                text = f"The image pipeline used `{identifier}`."
                self.assertIsNone(graph._snapshot_dates("Which image generation model or package worked?", text, packet(text), [MODEL_SOURCE]))

    def test_identifier_selection_and_support_use_the_same_complete_tokens(self):
        for identifier in ("vendor/model-v4.1-vector", "package-v4.1.tar.gz", "/opt/releases/package-v4.1.json",
                           "v4.1.2", UUID, "abcdef1234", "sha256:" + "a" * 64,
                           "registry.example/org/app:v4.1", "registry.example:5000/org/app:4.1.2",
                           "registry.example/org/app:release-candidate", "app:4.1.2",
                           "registry.example/org/app@sha256:" + "b" * 64):
            with self.subTest(identifier=identifier):
                text = f"The deployment recorded `{identifier}`."
                self.assertIn(identifier.casefold(), graph._snapshot_identifiers(text))
                dates = graph._snapshot_dates("Which deployment was recorded?", text, packet(text), [MODEL_SOURCE])
                self.assertEqual(dates, {MODEL_SOURCE: "2026-10-03T12:00:00Z"})
                result, _, action = graph._validate_distilled_answer(draft(text), packet(text), "Which deployment was recorded?")
                self.assertIsNotNone(result["answer"])
                self.assertEqual(action, "downgraded")
                self.assertIn("Live state is not verified", result["answer"])

    def test_shared_version_fragment_cannot_support_another_complete_identifier(self):
        text = "The deployment used vendor/model-v4.1-vector."
        source = "The deployment used other/model-v4.1-vector."
        self.assertEqual(graph._snapshot_dates("Which deployment was recorded?", text, packet(source), [MODEL_SOURCE]), {})
        source = "The deployment source mentions v4.1 only."
        self.assertEqual(graph._snapshot_dates("Which deployment was recorded?", text, packet(source), [MODEL_SOURCE]), {})

    def test_mixed_answer_still_requires_the_real_deployment_identifier(self):
        model = "The image model was vendor/model-v4.1-vector."
        deploy = f"The Worker deployment ID was {UUID}."
        p = packet(model)
        p["semantic_notes"] = [{"name": DEPLOY_SOURCE, "content": deploy}]
        question = "Which image model worked, and which Worker deployment was recorded?"
        result, _, action = graph._validate_distilled_answer(draft(model + " " + deploy, [MODEL_SOURCE, DEPLOY_SOURCE]), p, question)
        self.assertIsNotNone(result["answer"])
        self.assertEqual(action, "downgraded")
        self.assertIn(DEPLOY_SOURCE, result["observation_dates"])
        for unsupported in (UUID.replace("12345678", "abcdef12"), "sha256:" + "c" * 64,
                            "registry.example/org/other:v4.1"):
            result, _, action = graph._validate_distilled_answer(
                draft(model + f" The Worker deployment used {unsupported}.", [MODEL_SOURCE, DEPLOY_SOURCE]), p, question)
            self.assertIsNone(result["answer"])
            self.assertEqual(action, "qualifier_mismatch")

    def test_a_model_path_cannot_supply_a_standalone_deployment_id(self):
        p = packet(f"The image model ID was vendor/model-{UUID}.")
        self.assertEqual(graph._snapshot_dates("Which Worker deployment was recorded?", UUID, p, [MODEL_SOURCE]), {})

    def test_timestamp_is_not_a_tag_and_model_name_is_not_context(self):
        self.assertEqual(graph._snapshot_identifiers("Deployed at 10:30."), set())
        text = "The model was vendor/deployed-v4.1."
        self.assertIsNone(graph._snapshot_dates("Which image model worked?", text, packet(text), [MODEL_SOURCE]))

    async def test_original_compound_question_uses_actual_answer_path_with_synthetic_evidence(self):
        # These are source facts, not an audit note that tells the validator its answer.
        # The vault path is not a credential value. No vault or provider request occurs.
        evidence = ("Diego's OpenRouter API key is stored in Infisical at /services/openrouter/API_KEY. "
                    "A past image-generation project used recraft/recraft-v4.1-vector. "
                    "Its request used chat/completions with modalities [image, text] and a user message.")
        p = packet(evidence)
        parsed = draft(evidence)
        self.assertFalse(graph._current_state_question(QUESTION))
        with mock.patch.object(graph, "_assemble_packet", mock.AsyncMock(return_value=p)) as assemble, \
             mock.patch.object(graph, "_distill", mock.AsyncMock(return_value=parsed)) as distill, \
             mock.patch.object(graph, "_record_query"):
            result = await graph._graph_answer_impl(QUESTION)
        self.assertEqual(assemble.call_args.args[0], QUESTION)
        self.assertEqual(distill.call_args.args[0], QUESTION)
        self.assertEqual(result["answer"], evidence)
        self.assertEqual(result["sources"], [MODEL_SOURCE])
        self.assertNotIn("observation_dates", result)
        self.assertNotIn("superseded_note", result)
