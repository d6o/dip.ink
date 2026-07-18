#!/usr/bin/env python3
"""Parse repository workflow, deployment, and Compose YAML with duplicate-key checks."""
from __future__ import annotations

import copy
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


class StrictYaml12Loader(yaml.SafeLoader):
    """Safe loader with YAML 1.2 booleans and duplicate mapping rejection."""


StrictYaml12Loader.yaml_implicit_resolvers = copy.deepcopy(
    yaml.SafeLoader.yaml_implicit_resolvers
)
for first, resolvers in list(StrictYaml12Loader.yaml_implicit_resolvers.items()):
    StrictYaml12Loader.yaml_implicit_resolvers[first] = [
        pair for pair in resolvers if pair[0] != "tag:yaml.org,2002:bool"
    ]
StrictYaml12Loader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|false)$", re.IGNORECASE),
    list("tTfF"),
)


def construct_mapping(loader: StrictYaml12Loader, node: yaml.MappingNode, deep: bool = False):
    loader.flatten_mapping(node)
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"unhashable mapping key: {key!r}",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"duplicate key: {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


StrictYaml12Loader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    construct_mapping,
)


def yaml_files() -> list[Path]:
    paths: set[Path] = {ROOT / "docker-compose.yml"}
    for base in (ROOT / ".github", ROOT / "template" / ".github", ROOT / "deploy"):
        if not base.exists():
            continue
        paths.update(base.rglob("*.yml"))
        paths.update(base.rglob("*.yaml"))
    return sorted(path for path in paths if path.is_file())


def main() -> int:
    files = yaml_files()
    if not files:
        print("no YAML files found", file=sys.stderr)
        return 1

    documents = 0
    failures: list[str] = []
    for path in files:
        relative = path.relative_to(ROOT)
        try:
            parsed = list(yaml.load_all(path.read_text(encoding="utf-8"), Loader=StrictYaml12Loader))
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            failures.append(f"{relative}: {exc}")
            continue
        nonempty = [document for document in parsed if document is not None]
        if not nonempty:
            failures.append(f"{relative}: contains no YAML document")
            continue
        for index, document in enumerate(nonempty, start=1):
            if not isinstance(document, dict):
                failures.append(
                    f"{relative}: document {index} must be a mapping, got {type(document).__name__}"
                )
        documents += len(nonempty)

    if failures:
        for failure in failures:
            print(f"ERROR {failure}", file=sys.stderr)
        return 1

    print(f"parsed {documents} YAML document(s) across {len(files)} file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
