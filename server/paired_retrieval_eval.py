"""Offline paired retrieval experiment. Not registered as a production tool.

Caller supplies frozen packets/corpus/vectors and one fixed-model completion
callback. No graph client, index refresh, provider discovery, or retry is used.
Artifacts are private; the functions never print questions, packets, or answers.
"""
from __future__ import annotations

import asyncio
import copy
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import time
import uuid

import graph
from evaluate_answers import classify, hash_json

BODY_CAP = 20_000
ARMS = ("hybrid", "wiki_only")


@dataclass(frozen=True)
class AnswerSettings:
    model: str
    temperature: int = 0
    max_tokens: int = 400
    seed: int = 0

    def __post_init__(self):
        if not self.model or self.temperature != 0 or self.max_tokens != 400:
            raise ValueError("Use one explicit model with temperature=0 and max_tokens=400")


def corpus_map(documents, embedding_model):
    if not isinstance(embedding_model, str) or not embedding_model:
        raise ValueError("An explicit embedding model identity is required")
    mapped = {}
    for doc in documents:
        name = doc["name"]
        if not name or name in mapped or doc.get("embedding_model") != embedding_model:
            raise ValueError("Corpus identities and embedding model must be consistent")
        if not isinstance(doc.get("body"), str):
            raise ValueError("A frozen source body is required")
        mapped[name] = dict(doc)
    return mapped


def cosine(left, right):
    if len(left) != len(right) or not left or not all(math.isfinite(x) for x in (*left, *right)):
        raise ValueError("Invalid query/document vector")
    denominator = math.sqrt(sum(x*x for x in left) * sum(x*x for x in right))
    if denominator == 0:
        raise ValueError("Zero query/document vector")
    return sum(a*b for a, b in zip(left, right)) / denominator


def wiki_packet(question, query_vector, documents, *, embedding_model, k=8):
    """Exact vector query over a frozen wiki snapshot; performs zero graph reads.

    Exclusion is applied later to BOTH frozen arms, without candidate refill.
    Retrieval sees no expected claims or reviewed/gold source identities.
    """
    if not 1 <= k <= 8:
        raise ValueError("Wiki candidate limit must be between 1 and 8")
    docs = corpus_map(documents, embedding_model)
    ranked = sorted(((cosine(query_vector, doc["vector"]), name) for name, doc in docs.items()),
                    key=lambda item: (-item[0], item[1]))[:k]
    return {"query": question, "facts": [], "entities": [], "source_excerpt": None,
            "semantic_notes": [{"name": name, "score": score,
                                "content": graph._fact_body_excerpt(docs[name]["body"], question)}
                               for score, name in ranked]}


def prepare(question, frozen, docs, exclusions):
    """Shared exclusion, cap, subject, temporal and qualifier policy for both arms."""
    allowed = set(docs) - exclusions
    packet = copy.deepcopy({key: frozen[key] for key in
                            ("query", "facts", "entities", "source_excerpt", "semantic_notes", "temporal_candidates")
                            if key in frozen})
    original_sources = set(graph._allowed_provenance(packet)) | {
        h["name"] for h in packet.get("temporal_candidates", []) if h.get("name")
    }
    packet["query"] = question
    packet["facts"] = [f for f in packet.get("facts", []) if f.get("source_slug") in allowed]
    packet["semantic_notes"] = [h for h in packet.get("semantic_notes", []) if h.get("name") in allowed]
    packet["temporal_candidates"] = [h for h in packet.get("temporal_candidates", []) if h.get("name") in allowed]
    excerpt = packet.get("source_excerpt") or {}
    packet["source_excerpt"] = excerpt if excerpt.get("slug") in allowed else None
    # Aggregated summaries cannot be attributed safely when excluding artifacts.
    removed_entities = len(packet.get("entities", []))
    packet["entities"] = []
    for key in ("temporal_context", "subject_context", "required_qualifiers", "credential_validation_texts"):
        packet.pop(key, None)
    # Use the same original body and excerpt selector for readable overlapping
    # sources in both arms. Names-only candidates remain names-only vetoes.
    for item, name in ([(packet["source_excerpt"], packet["source_excerpt"]["slug"])] if packet["source_excerpt"] else []) + [
        (hit, hit["name"]) for hit in packet["semantic_notes"]
    ]:
        if item.get("content"):
            item["content"] = graph._fact_body_excerpt(docs[name]["body"], question)
    remaining = BODY_CAP
    for item, field in ([(f, "fact") for f in packet["facts"]]
                        + ([(packet["source_excerpt"], "content")] if packet["source_excerpt"] else [])
                        + [(h, "content") for h in packet["semantic_notes"]]):
        if not item.get(field):
            continue
        item[field] = str(item[field])[:min(2500, remaining)]
        remaining -= len(item[field])
    packet["facts"] = [f for f in packet["facts"] if f.get("fact")]
    subjects = graph._answer_subjects(question)
    packet = graph._subject_evidence_packet(packet, subjects)
    requirements = graph.qualifier_requirements(question)
    if requirements:
        packet["required_qualifiers"] = list(requirements)
    if graph.credential_services(question):
        packet["credential_validation_texts"] = {
            name: docs[name]["body"] for name in graph._allowed_provenance(packet) if name in allowed
        }
    current = graph._current_state_question(question)
    if current:
        packet["temporal_context"] = graph._temporal_context(packet)
    model_packet = graph._current_evidence_packet(packet) if current else {
        key: value for key, value in packet.items() if key not in {"temporal_candidates", "credential_validation_texts"}
    }
    abstain = bool((subjects and graph._subject_support_missing(packet, subjects))
                   or (current and not packet["temporal_context"]["eligible_sources"]))
    report = {"excluded_sources": sorted(original_sources & exclusions),
              "sources_outside_corpus": sorted(original_sources - set(docs)),
              "unattributed_entities_removed": removed_entities, "readable_chars_before_pruning": BODY_CAP - remaining}
    return packet, model_packet, abstain, report


async def run_pairs(cases, documents, *, embedding_model, settings, complete, output,
                    exclusions, corpus_version, timeout=600):
    """Capture every case/arm, including errors and abstentions, privately.

    complete(**kwargs) must return {content: full raw text, model: actual model,
    finish_reason?: str}. It must disable its own retries and response logging.
    The same settings and production prompt are used in both arms. No fallback.
    """
    cases = copy.deepcopy(cases)
    system_prompt = graph._DISTILL_SYSTEM
    if not 1 <= len(cases) <= 100 or not 1 <= timeout <= 900:
        raise ValueError("Invalid experiment bounds")
    if len({c["id"] for c in cases}) != len(cases):
        raise ValueError("Duplicate case identities")
    docs = corpus_map(documents, embedding_model)
    exclusions = set(exclusions)
    for case in cases:
        if not isinstance(case.get("question"), str) or not case["question"].strip() or set(case["packets"]) != set(ARMS):
            raise ValueError("Full questions and both frozen packets are required")
        for timing in case.get("retrieval_ms", {}).values():
            if not isinstance(timing, (int, float)) or isinstance(timing, bool) or not math.isfinite(timing) or timing < 0:
                raise ValueError("Retrieval timings must be finite nonnegative milliseconds")
        cosine(case["query_vector"], case["query_vector"])
        if case["query_vector_sha256"] != hash_json(case["query_vector"]):
            raise ValueError("Query vector hash mismatch")
        if case["embedding_model"] != embedding_model:
            raise ValueError("Query embedding model mismatch")
        if any(packet.get("query", case["question"]) != case["question"] for packet in case["packets"].values()):
            raise ValueError("Frozen packet question mismatch")
        if case["packets"]["wiki_only"].get("facts") or case["packets"]["wiki_only"].get("entities"):
            raise ValueError("Wiki-only input must not contain graph facts or entities")
    meta = {"type": "run", "run_id": uuid.uuid4().hex, "corpus_version": corpus_version,
            "corpus_sha256": hash_json(documents), "corpus_documents": len(documents), "input_sha256": hash_json(cases),
            "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "prompt_sha256": hashlib.sha256(system_prompt.encode()).hexdigest(),
            "validator_sha256": hashlib.sha256(Path(graph.__file__).read_bytes()).hexdigest(),
            "embedding_model": embedding_model, "settings": asdict(settings), "case_count": len(cases),
            "body_cap": BODY_CAP, "exclusions": sorted(exclusions),
            "exclusions_missing_from_corpus": sorted(exclusions - set(docs))}
    counts = {arm: {"answer": 0, "abstention": 0, "error": 0} for arm in ARMS}
    mismatch_cases = 0
    model_calls = 0
    model_overrides = 0
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as file:
        file.write(json.dumps(meta) + "\n"); file.flush()
        for index, case in enumerate(cases):
            models = []
            # Alternate order deterministically to expose, not hide, order effects.
            order = ARMS if index % 2 == 0 else tuple(reversed(ARMS))
            for arm in order:
                started = time.monotonic()
                frozen = case["packets"][arm]
                row = {"type": "arm", "id": case["id"], "arm": arm, "question": case["question"],
                       "frozen_packet_sha256": hash_json(frozen), "query_vector_sha256": case["query_vector_sha256"],
                       "retrieval_ms": case.get("retrieval_ms", {}).get(arm), "answer_ms": 0}
                try:
                    validation, model_packet, abstain, report = prepare(case["question"], frozen, docs, exclusions)
                    row.update(packet=model_packet, packet_sha256=hash_json(model_packet),
                               packet_chars=len(json.dumps(model_packet, ensure_ascii=False)), exclusion_report=report,
                               preparation_ms=round((time.monotonic() - started) * 1000))
                    parsed = {"answer": None, "confidence": "not_found", "sources": [], "escalate": True}
                    if not abstain:
                        messages = [{"role": "system", "content": system_prompt},
                                    {"role": "user", "content": f"QUESTION: {case['question']}\n\nRETRIEVAL PACKET (JSON):\n{json.dumps(model_packet, ensure_ascii=False)}\n\nReply with ONLY the JSON object."}]
                        model_calls += 1
                        answer_started = time.monotonic()
                        try:
                            raw = await asyncio.wait_for(complete(messages=messages, **asdict(settings)), timeout)
                        finally:
                            row["answer_ms"] = round((time.monotonic() - answer_started) * 1000)
                        if not isinstance(raw.get("content"), str) or not isinstance(raw.get("model"), str) or not raw["model"]:
                            raise ValueError("Completion must report full text and its actual model")
                        row["raw_answer"] = raw["content"]
                        row["reported_model"] = raw["model"]
                        if raw["model"] != settings.model:
                            model_overrides += 1
                        row["finish_reason"] = raw.get("finish_reason")
                        row["usage"] = {key: value for key, value in (raw.get("usage") or {}).items()
                                        if key in {"prompt_tokens", "completion_tokens", "total_tokens"}
                                        and isinstance(value, int) and not isinstance(value, bool) and value >= 0}
                        models.append(raw["model"])
                        parsed = graph._extract_json(raw["content"])
                        if not isinstance(parsed, dict) or "confidence" not in parsed:
                            raise ValueError("Unparseable answer")
                    result, grounded, action = graph._validate_distilled_answer(parsed, validation, case["question"])
                    row.update(result=result, grounded=grounded, grounding_action=action, status=classify(result))
                except Exception as error:
                    row.update(status="error", error_type=type(error).__name__)
                row["duration_ms"] = round((time.monotonic() - started) * 1000)
                counts[arm][row["status"]] += 1
                file.write(json.dumps(row, ensure_ascii=False) + "\n"); file.flush()
            if len(set(models)) > 1:
                mismatch_cases += 1
    return {"case_count": len(cases), "expected_arm_receipts": 2 * len(cases), "counts": counts,
            "reported_model_mismatch_cases": mismatch_cases, "model_calls": model_calls,
            "reported_model_overrides": model_overrides, "input_sha256": meta["input_sha256"],
            "corpus_sha256": meta["corpus_sha256"], "interpretation": "capture only; human adjudication required"}
