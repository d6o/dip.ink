#!/usr/bin/env python3
"""Static cross-lane contracts that should fail loudly when release inputs drift."""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PINNED_RELEASE = "v0.1.0"


def load_yaml(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.relative_to(ROOT)} is not a YAML mapping")
    return data


def workflow_triggers(data: dict) -> dict:
    # PyYAML implements YAML 1.1 and therefore parses the unquoted key `on` as True.
    value = data.get("on", data.get(True, {}))
    return value if isinstance(value, dict) else {}


def check_template(errors: list[str]) -> None:
    workflow_dir = ROOT / "template" / ".github" / "workflows"
    expected = ["curator.yml", "reviewqueue.yml", "synthesis.yml"]
    for name in expected:
        path = workflow_dir / name
        if not path.is_file():
            errors.append(f"missing template workflow: {path.relative_to(ROOT)}")
            continue
        try:
            workflow = load_yaml(path)
        except (OSError, UnicodeError, yaml.YAMLError, ValueError) as exc:
            errors.append(str(exc))
            continue
        concurrency = workflow.get("concurrency")
        group = concurrency.get("group") if isinstance(concurrency, dict) else None
        if group != "memory-repo-writer":
            errors.append(
                f"{path.relative_to(ROOT)} concurrency.group is {group!r}; expected 'memory-repo-writer'"
            )
        jobs = workflow.get("jobs")
        if not isinstance(jobs, dict) or not jobs:
            errors.append(f"{path.relative_to(ROOT)} has no jobs")
            continue
        for job_name, job in jobs.items():
            if not isinstance(job, dict):
                continue
            container = job.get("container")
            image = container.get("image") if isinstance(container, dict) else None
            if image != f"ghcr.io/d6o/dip.ink/pi-runner:{PINNED_RELEASE}":
                errors.append(
                    f"{path.relative_to(ROOT)} job {job_name!r} must use pi-runner:{PINNED_RELEASE}; got {image!r}"
                )

    source_root = ROOT / "template" / "wiki" / "sources" / "notes"
    bootstrap_pages: list[Path] = []
    if source_root.is_dir():
        for page in source_root.rglob("*.md"):
            relative = page.relative_to(source_root)
            parts = relative.parts
            if (
                len(parts) == 5
                and re.fullmatch(r"\d{4}", parts[0])
                and re.fullmatch(r"\d{2}", parts[1])
                and re.fullmatch(r"\d{2}", parts[2])
                and page.stem == page.parent.name
            ):
                bootstrap_pages.append(page)
    if not bootstrap_pages:
        errors.append(
            "template has no canonical bootstrap source note at "
            "wiki/sources/notes/YYYY/MM/DD/<slug>/<slug>.md"
        )

    # Docs/contracts must not reintroduce the removed secret-scanning path.
    owned_docs = [
        ROOT / "README.md",
        ROOT / "curator" / "README.md",
        ROOT / "template" / "README.md",
        ROOT / "template" / "AGENTS.md",
    ]
    stale_patterns = [
        re.compile(r"scanned for literal credential", re.I),
        re.compile(r"redacted in place", re.I),
        re.compile(r"Browse it in Obsidian", re.I),
        re.compile(r"two MCP servers", re.I),
    ]
    for path in owned_docs:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in stale_patterns:
            if pattern.search(text):
                errors.append(
                    f"{path.relative_to(ROOT)} still contains stale wording matching /{pattern.pattern}/"
                )


def check_release(errors: list[str]) -> None:
    images_path = ROOT / ".github" / "workflows" / "images.yml"
    try:
        images = load_yaml(images_path)
    except (OSError, UnicodeError, yaml.YAMLError, ValueError) as exc:
        errors.append(str(exc))
        images = {}
    triggers = workflow_triggers(images)
    push = triggers.get("push") if isinstance(triggers, dict) else None
    if not isinstance(push, dict):
        errors.append(".github/workflows/images.yml must define push triggers")
    else:
        tags = push.get("tags", [])
        branches = push.get("branches", [])
        if isinstance(tags, str):
            tags = [tags]
        if isinstance(branches, str):
            branches = [branches]
        if "v*" not in tags:
            errors.append("images workflow must trigger on v* tags")
        if "main" not in branches:
            errors.append("images workflow must retain useful main-branch builds")

    images_text = images_path.read_text(encoding="utf-8") if images_path.is_file() else ""
    required_tag_rules = (
        "docker/metadata-action@v5",
        "type=ref,event=tag",
        "type=semver,pattern={{version}}",
        "type=sha,format=long,prefix=",
        "latest=false",
    )
    for rule in required_tag_rules:
        if rule not in images_text:
            errors.append(f"images workflow missing tag rule: {rule}")
    if re.search(r":latest\b", images_text):
        errors.append("images workflow must not publish a mutable :latest tag")

    ci_path = ROOT / ".github" / "workflows" / "ci.yml"
    try:
        ci = load_yaml(ci_path)
    except (OSError, UnicodeError, yaml.YAMLError, ValueError) as exc:
        errors.append(str(exc))
        ci = {}
    expected_jobs = {
        "server-unit",
        "server-neo4j-integration",
        "curator-template",
        "manifests-workflows",
        "pi-extension-typecheck",
        "docker-build",
    }
    jobs = ci.get("jobs", {}) if isinstance(ci, dict) else {}
    missing_jobs = expected_jobs - set(jobs if isinstance(jobs, dict) else {})
    if missing_jobs:
        errors.append(f"CI workflow missing required jobs: {', '.join(sorted(missing_jobs))}")

    required_paths = (
        "server/requirements.txt",
        "server/tests",
        "template/scripts/test-processnotes-supervisor.sh",
        "template/scripts/wikilint.py",
        "template/scripts/wikiindex.py",
        "agent-setup/pi/extensions/memory/index.ts",
        "agent-setup/pi/extensions/recordnotes.ts",
        "server/Dockerfile",
        "curator/pi-runner/Dockerfile",
        "docker-compose.yml",
        "deploy/k8s/kustomization.yaml",
        "deploy/examples/dipink-secrets.example.yaml",
        "deploy/observability/servicemonitor.yaml",
        "deploy/observability/prometheusrule.yaml",
        "deploy/observability/grafana-dashboard.yaml",
        "ci/run-neo4j-integration.sh",
        "ci/check-yaml.py",
        "ci/check-k8s.py",
        "ci/check-repo-contract.py",
        "ci/compose.env",
        "ci/pi/package.json",
        "ci/pi/tsconfig.json",
    )
    for relative in required_paths:
        if not (ROOT / relative).exists():
            errors.append(f"required CI input is missing: {relative}")

    integration_files = sorted((ROOT / "server" / "tests").glob("test_*integration*.py"))
    if not integration_files:
        errors.append(
            "required real-container test module missing: server/tests/test_*integration*.py"
        )

    production_roots = [ROOT / "deploy", ROOT / "template" / ".github" / "workflows"]
    production_text = "\n".join(
        path.read_text(encoding="utf-8")
        for base in production_roots
        if base.exists()
        for path in sorted(base.rglob("*"))
        if path.is_file() and path.suffix in {".yaml", ".yml"}
    )
    mutable = re.findall(
        r"ghcr\.io/d6o/dip\.ink/(?:memory|pi-runner):latest", production_text
    )
    if mutable:
        errors.append(
            f"production manifests/workflows still contain {len(mutable)} mutable :latest image reference(s)"
        )
    if f"ghcr.io/d6o/dip.ink/memory:{PINNED_RELEASE}" not in production_text:
        errors.append(f"deploy manifests do not reference memory:{PINNED_RELEASE}")
    if f"ghcr.io/d6o/dip.ink/pi-runner:{PINNED_RELEASE}" not in production_text:
        errors.append(f"template workflows do not reference pi-runner:{PINNED_RELEASE}")

    # Root docs must not reintroduce packaging drift fixed in this release.
    readme = (ROOT / "README.md").read_text(encoding="utf-8") if (ROOT / "README.md").is_file() else ""
    for phrase in (
        "two MCP servers",
        "Browse it in Obsidian",
        "scanned for literal credential",
        "docker compose up -d --build",
        "deploy/k8s/secrets.example.yaml",
    ):
        if phrase in readme:
            errors.append(f"README.md still contains stale phrase: {phrase}")
    # Ban directory apply of deploy/k8s (with or without trailing slash), but allow
    # applying a single named file or the kustomize path.
    if re.search(r"kubectl apply -f deploy/k8s/?\s", readme) or re.search(
        r"kubectl apply -f deploy/k8s/?$", readme, re.M
    ):
        errors.append(
            "README.md still guides operators to `kubectl apply -f deploy/k8s/` directory apply"
        )
    if re.search(r"memory:latest|pi-runner:latest", readme):
        errors.append("README.md still guides operators to :latest images")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--template", action="store_true", help="check template workflow/bootstrap contracts"
    )
    parser.add_argument(
        "--release", action="store_true", help="check root CI/image/deploy release contracts"
    )
    args = parser.parse_args()
    if not args.template and not args.release:
        parser.error("select --template and/or --release")

    errors: list[str] = []
    if args.template:
        check_template(errors)
    if args.release:
        check_release(errors)

    if errors:
        for error in errors:
            print(f"ERROR {error}", file=sys.stderr)
        return 1
    selected = ", ".join(
        name
        for name, enabled in (("template", args.template), ("release", args.release))
        if enabled
    )
    print(f"repo contracts ok ({selected})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
