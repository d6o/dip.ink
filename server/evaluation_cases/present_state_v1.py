"""Present-state test contract, not a state reducer or architecture benchmark run.

Sources contain only neutral identity, capture time, and raw prose. Expectations
are authored separately in grader metadata. Repeated notes are generated, not
stored as a large expanded dataset. No model, provider, or database calls occur.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import random

ASSESSMENT_AT = "2026-11-06T12:00:00Z"
QUESTION = "Which message transport does ExampleRelay use in production?"
OLD = "transport alpha"
NEW = "transport beta"
OTHER = "transport gamma"


def build():
    cases = []

    def add(number, name, entries, value, support, *, forbidden=(), shuffle=False, controls=None, identity_namespace=None):
        sources = []
        namespace = number if identity_namespace is None else identity_namespace
        for ordinal, (capture, prose) in enumerate(entries):
            stamp = datetime.fromisoformat(capture.replace("Z", "+00:00"))
            sources.append({"id": stamp.strftime("%Y-%m-%d-%H%M%S") + f"-ps-{namespace:02d}-{ordinal:03d}",
                            "captured_at": stamp.isoformat().replace("+00:00", "Z"), "text": prose})
        gold = [sources[index]["id"] for index in support]
        if shuffle:
            random.Random(173).shuffle(sources)
        cases.append({"id": f"ps-{number:02d}", "scenario": name, "assessment_at": ASSESSMENT_AT,
                      "question": QUESTION, "sources": sources,
                      "controls": controls or {},
                      "grader": {"outcome": "value" if value is not None else "uncertain", "value": value,
                                 "supporting_source_ids": gold, "forbidden_active_values": list(forbidden),
                                 "provenance_rule": ("cite supporting evidence for the asserted value" if value is not None else
                                                     "do not choose an active value; cite both receipts when explaining uncertainty")},
                      "backend_trials": {"all_evidence_reader": "unrun", "prepared_reader": "unrun",
                                         "graphiti_ingest_retrieval": "unrun", "state_projection_ingest_retrieval": "unrun",
                                         "document_ingest_retrieval": "unrun"}})

    def old_notes(count=3, *, captured="2026-01-15T00:00:00Z", late=False):
        start = datetime.fromisoformat(captured.replace("Z", "+00:00"))
        text = f"On January 10, 2026, ExampleRelay's production message transport was {OLD}."
        if late:
            text = "Imported historical transcript: " + text + " This records January, not a new activation."
        return [((start + timedelta(minutes=i)).isoformat(), text) for i in range(count)]

    switch = ("2026-03-02T12:00:00Z", f"Completed change: on March 1, 2026, ExampleRelay's production message transport switched from {OLD} to {NEW}. Configuration and traffic checks confirmed {NEW} active.")
    hundred = old_notes(100)
    add(1, "completed switch beats old mention volume", hundred + [switch], NEW, [100], forbidden=[OLD],
        controls={"old_record_count": 100, "ingest_order": "listed"})
    add(2, "same evidence shuffled in arrival and reader order", hundred + [switch], NEW, [100], forbidden=[OLD], shuffle=True, identity_namespace=1,
        controls={"old_record_count": 100, "semantic_pair": "ps-01", "order_seed": 173,
                  "ingest_order": "listed, one source per completed ingest step; capture timestamps stay unchanged"})

    add(3, "older event arrives late", old_notes() + [switch] + old_notes(1, captured="2026-11-04T12:00:00Z", late=True),
        NEW, [3], forbidden=[OLD], controls={"latest_capture_is_not_latest_effective_event": True})
    proposal = ("2026-11-04T12:00:00Z", f"Proposal: move ExampleRelay's production message transport to {NEW}. No rollout was executed; {OLD} remains enabled.")
    add(4, "proposal does not activate", hundred + [proposal], OLD, list(range(101)), forbidden=[NEW],
        controls={"old_record_count": 100, "newest_note_is_not_an_activation": True})
    trial = ("2026-11-04T12:00:00Z", f"Experiment receipt: an isolated ExampleRelay replay tried {NEW}. The experiment did not modify the production message transport.")
    add(5, "experiment does not activate", old_notes() + [trial], OLD, [0, 1, 2], forbidden=[NEW])
    other_project = ("2026-11-04T12:00:00Z", f"Completed change: ExampleNotebook switched its production message transport to {NEW}. This receipt concerns ExampleNotebook only.")
    add(6, "another project does not overwrite", old_notes() + [other_project], OLD, [0, 1, 2], forbidden=[NEW])
    other_setting = ("2026-11-04T12:00:00Z", f"Completed change: ExampleRelay's production audit transport switched to {NEW}. Its message transport configuration was not changed.")
    add(7, "another setting does not overwrite", old_notes() + [other_setting], OLD, [0, 1, 2], forbidden=[NEW])
    other_environment = ("2026-11-04T12:00:00Z", f"Completed change: ExampleRelay's staging message transport switched to {NEW}. No production rollout occurred.")
    add(8, "another environment does not overwrite", old_notes() + [other_environment], OLD, [0, 1, 2], forbidden=[NEW])

    stable = ("2020-01-15T12:00:00Z", f"On January 10, 2020, ExampleRelay's production message transport was configured as {OLD}; verification succeeded.")
    add(9, "stable old evidence does not expire by age", [stable], OLD, [0],
        controls={"no_subsequent_change_receipt": True})
    repeated_new = [(f"2026-03-15T00:{i:02d}:00Z", f"On March 15, 2026, ExampleRelay's production message transport used {NEW}.") for i in range(20)]
    rollback = ("2026-04-02T12:00:00Z", f"Completed rollback: effective April 1, 2026, ExampleRelay's production message transport returned to {OLD}. Verification confirmed {OLD} handling traffic and {NEW} disabled.")
    rollback_sources = old_notes() + [switch] + repeated_new + [rollback]
    add(10, "completed rollback changes active state", rollback_sources, OLD, [len(rollback_sources)-1], forbidden=[NEW],
        controls={"repeated_intermediate_state_count": 20})
    future = ("2026-11-05T12:00:00Z", f"Future plan: activate {NEW} for ExampleRelay's production message transport on December 1, 2026. Preparation only; activation has not run.")
    add(11, "future plan is not active", old_notes() + [future], OLD, [0, 1, 2], forbidden=[NEW])
    false_report = ("2026-11-02T12:00:00Z", f"A rollout report claimed ExampleRelay's production message transport switched to {NEW} on November 1, 2026.")
    correction = ("2026-11-04T12:00:00Z", f"Correction to the November 1 rollout report: it was false. No production message transport switch occurred. Configuration and traffic evidence show ExampleRelay retained {OLD}, not {NEW}.")
    add(12, "earlier false report is corrected", old_notes() + [false_report, correction], OLD, [4], forbidden=[NEW])

    conflict_a = ("2026-11-05T12:00:00Z", f"Independent receipt A: ExampleRelay's production message transport changed to {NEW} at 10:00 UTC on November 5, 2026; verification reported success.")
    conflict_b = ("2026-11-05T12:00:00Z", f"Independent receipt B: ExampleRelay's production message transport changed to {OTHER} at 10:00 UTC on November 5, 2026; verification reported success.")
    add(13, "simultaneous unresolved conflict requires uncertainty", old_notes() + [conflict_a, conflict_b], None, [3, 4],
        forbidden=[OLD, NEW, OTHER], controls={"no_tie_breaking_authority_or_resolution_receipt": True})
    add(14, "old event repeated in many late captures", [switch] + old_notes(100, captured="2026-11-04T12:00:00Z", late=True),
        NEW, [0], forbidden=[OLD], shuffle=True,
        controls={"old_record_count": 100, "latest_capture_is_not_latest_effective_event": True, "order_seed": 173})
    return {"schema_version": 1, "dataset_version": "present-state-1", "synthetic": True,
            "purpose": "Primary present-state freshness contract; backend trials are unrun.", "cases": cases}


def source_inputs(case):
    """Only these fields may enter source ingestion or reader/retrieval inputs."""
    return {"case_id": case["id"], "assessment_at": case["assessment_at"], "question": case["question"],
            "sources": [{key: source[key] for key in ("id", "captured_at", "text")} for source in case["sources"]]}


def reader_packet(case):
    """Format all raw sources in the existing frozen-packet shape. No gold flags.

    This is the pre-preparation control packet. Existing harness preparation may
    prune it; callers must not label a pruned request an all-evidence control.
    """
    inputs = source_inputs(case)
    if sum(len(source["text"]) for source in inputs["sources"]) > 20_000:
        raise ValueError("All-source control exceeds the shared body budget")
    return {"query": inputs["question"], "facts": [], "entities": [], "source_excerpt": None,
            "semantic_notes": [{"name": source["id"], "content": source["text"],
                                "captured_at": source["captured_at"]} for source in inputs["sources"]]}
