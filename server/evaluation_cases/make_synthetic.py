"""Build the public synthetic dataset without provider calls or private input."""
import json
from pathlib import Path


def build():
    cases = []
    def add(identity, category, question, claims, forbidden, sources, answer, citations, action="accepted", temporal=None):
        packet = {"facts": [{"fact": source["body"], "source_slug": source["slug"], "current": source.get("current", True)} for source in sources],
                  "source_excerpt": {"slug": sources[-1]["slug"], "content": sources[-1]["body"]} if sources else None,
                  "semantic_notes": [], "entities": []}
        if temporal:
            packet["temporal_context"] = temporal
        cases.append({"id": identity, "category": category, "question": question,
                      "expected_claims": claims, "forbidden_claims": forbidden, "reviewed_sources": sources,
                      "draft": {"answer": answer, "confidence": "medium" if answer else "not_found", "sources": citations},
                      "packet": packet, "expected_action": action})
    def source(slug, date, body, current=True):
        return {"slug": slug, "date": date, "body": body, "current": current}

    for i, (old, new) in enumerate((("port 6379", "port 6380"), ("region east", "region west"),
                                   ("daily imports", "hourly imports"), ("local files", "object storage"))):
        name = f"Fixture service {i + 1}"
        a = source(f"2026-01-10-00000{i}-service-original", "2026-01-10", f"{name} used {old} on January 10.", False)
        b = source(f"2026-02-10-00000{i}-service-change", "2026-02-10", f"{name} changed to {new} on February 10, replacing {old}.")
        add(f"temporal-current-{i}", "temporal-change", f"What configuration is currently recorded for {name}?",
            [new], [f"currently uses {old}"], [a, b], f"{name} uses {new} in the February record.", [b["slug"]],
            temporal={"mode": "current", "eligible_sources": [b["slug"]], "source_dates": {b["slug"]: "2026-02-10T00:00:00Z"}})
        add(f"temporal-history-{i}", "temporal-change", f"What configuration did {name} use on January 10, 2026?",
            [old], [new], [a], f"{name} used {old} on January 10.", [a["slug"]])

    for i, (left, right) in enumerate((("Birch", "Birch Lab"), ("Cedar", "Cedar Archive"),
                                     ("Maple", "Maple Tools"), ("Willow", "Willow Demo"))):
        for j, (name, other) in enumerate(((left, right), (right, left))):
            database, other_database = ("PostgreSQL", "SQLite") if j == 0 else ("SQLite", "PostgreSQL")
            a = source(f"2026-03-01-0000{i}{j}-project-a", "2026-03-01", f"Fixture project {name} uses {database}.")
            b = source(f"2026-03-01-0100{i}{j}-project-b", "2026-03-01", f"Fixture project {other} uses {other_database}.")
            add(f"project-{i}-{j}", "similar-projects", f"Which database does fixture project {name} use, rather than fixture project {other}?",
                [database], [f"{name} uses {other_database}"], [b, a], f"Fixture project {name} uses {database}.", [a["slug"]])

    for i, absent in enumerate(("recovery time", "approved budget", "deployment owner", "backup retention",
                                "production hostname", "incident cause", "data residency")):
        a = source(f"2026-04-01-00000{i}-limited-record", "2026-04-01", "The fixture records a completed unit test only.")
        question = f"What {absent} is established for this fixture?"
        if i == 6:
            question += " Please use only reviewed evidence, distinguish a unit-test receipt from an operational guarantee, keep the named question intact, and state that evidence is absent when no source establishes the requested attribute."
        add(f"absent-{i}", "absent-facts", question, [], ["guaranteed"], [a], None, [], "abstained")

    for i, claim in enumerate(("a retry limit of three", "manual approval", "a queue audit", "a new index",
                               "read-only access", "a weekly report", "an explicit rollback")):
        a = source(f"2026-10-30-00000{i}-new-fixture-note", "2026-10-30", f"Fixture update {i + 1} records {claim}.")
        add(f"new-note-{i}", "new-notes", f"What did fixture update {i + 1}, added October 30, 2026, record?", [claim], ["nothing was recorded"],
            [a], f"Fixture update {i + 1} records {claim}.", [a["slug"]])
    return {"schema_version": 1, "dataset_version": "synthetic-memory-1", "synthetic": True,
            "description": "Thirty synthetic reviewed-evidence cases; not a production accuracy benchmark.", "cases": cases}


if __name__ == "__main__":
    Path(__file__).with_name("synthetic-memory-v1.json").write_text(json.dumps(build(), indent=2, ensure_ascii=False) + "\n")
