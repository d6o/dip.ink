"""Bounded checks for a metadata method's resource scope.

A positive needs one explicit method and all claimed resources in one source
clause. A later clause, another method, a heading, or another source cannot
supply missing scope. All source clauses can supply contrary limits. This is
not an entailment parser or an authorization check.

False negatives are intentional. Positive names need backticks, a path, or a
compound identifier. Pronouns, coordinated methods, generic workload scope,
and indirect positive descriptions cannot establish a capability.
"""
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

RESOURCE_SCOPE_REASON = (
    "The cited text does not establish the stated metadata method and resource "
    "scope, or contains a contrary scope limit."
)

_RESOURCE_PATTERNS = {
    "longhorn": r"\blonghorn\b",
    "volume": r"\bvolumes?\b",
    "replica": r"\breplicas?\b",
    "node_disk": r"\bnode[- ]+disks?\b",
    "disk": r"(?<!node )(?<!node-)\bdisks?\b",
    "capacity": r"\bcapacity\b",
    "backup": r"\bbackups?\b",
    "pod": r"\bpods?\b",
    "deployment": r"\bdeployments?\b",
    "statefulset": r"\bstatefulsets?\b",
    "cronjob": r"\bcron[- ]?jobs?\b",
    "job": r"(?<!cron )(?<!cron-)\bjobs?\b",
    "pv": r"\b(?:pvs?|persistent[- ]volumes?)\b",
    "pvc": r"\b(?:pvcs?|persistent[- ]volume[- ]claims?)\b",
    "node": r"\bnodes?\b(?![- ]+disks?)",
    "logs": r"\blogs?\b",
    "list": r"\blist(?:s|ing)?\b",
}
_RESOURCE_RE = {name: re.compile(pattern, re.I) for name, pattern in _RESOURCE_PATTERNS.items()}
_INTENT = re.compile(r"\b(?:channels?|methods?|readers?|tools?|CLI|REST|API|endpoints?|supports?|capabilit\w*)\b", re.I)
_READ = re.compile(r"\b(?:metadata|read[- ]only|reads?|lists?|logs?)\b", re.I)
_AFFIRM = re.compile(r"\b(?:supports?|supported|approved)\b", re.I)
_LIMIT = re.compile(r"(?<!read[- ])(?<![-\w])(?:only|limited\s+to|limits?\b.{0,60}\bto|restricted\s+to)\b", re.I)
_NEGATIVE = re.compile(
    r"\b(?:no|not|never|cannot|unsupported|unavailable|fails?|failed|failure|denied|blocked)\b"
    r"|binding_window_unsupported", re.I,
)
_UNCERTAIN = re.compile(
    r"\b(?:no|not|never|cannot|may|might|could|would|should|if|whether|unknown|unverified|"
    r"unsupported|unavailable|fails?|failed|failure|denied|blocked|pending|planned|proposed|"
    r"draft|fixture|example|stub|intent|intended|source[- ]only|source code|reviewed source)\b|\?", re.I,
)
_BOUNDED = re.compile(r"\b(?:cited|retrieved|supplied|recorded)\s+(?:source\s+)?(?:text|sources?|evidence|notes?)\b", re.I)
_LACK = re.compile(
    r"\b(?:does?\s+not|cannot)\s+(?:establish|identify|confirm|verify|show)\b"
    r"|\bno\b.{0,100}\b(?:found|recorded|identified|established|contains?|channel|method)\b", re.I,
)
_NAME = r"`[^`\n]+`|[A-Za-z0-9_/.:-]+"
_NAMED = re.compile(
    rf"\b(?:use|uses|using|via|through)\s+(?:the\s+)?(?P<after>{_NAME})"
    rf"|(?P<before>{_NAME})\s+(?:(?:only|currently|explicitly|also)\s+)?(?:is|supports?|reads?|lists?|provides?|allows?|cannot|does\s+not|has)\b"
    rf"|\b(?:method|channel|reader|tool|endpoint)\s*(?:is|:|=)\s*(?:the\s+)?(?P<is>{_NAME})"
    rf"|\b(?:by|for)\s+(?P<for>{_NAME})", re.I,
)
_BACKTICK = re.compile(r"`([^`\n]+)`")
_NOT_METHOD = {
    "read-only", "read_only", "node-disk", "node-disks", "pod-list", "pod-logs",
    "binding_window_unsupported", "exact-user-agent", "kinds", "cron-job", "cron-jobs",
}
_STORAGE = {"longhorn", "volume", "replica", "disk", "node_disk", "capacity", "backup", "pv", "pvc", "node"}
_UNAVAILABLE = re.compile(
    r"\b(?:is|remains)\s+(?:(?:currently|still)\s+)?(?:unavailable|unsupported|"
    r"uninstalled|blocked|not\s+(?:supported|installed|available|approved))\b"
    r"|binding_window_unsupported", re.I,
)
_GLOBAL_ABSENCE = re.compile(
    r"\bno\s+(?:(?:approved|supported|installed|read[- ]only|longhorn|volume|replica|"
    r"node|disk|backup|metadata|cron[- ]?jobs?|jobs?)\s+)*(?:channels?|methods?|readers?|tools?|endpoints?)\b", re.I,
)


def _scope_text(text: str) -> str:
    for method in _methods(text):
        text = re.sub(re.escape(method), " ", text, flags=re.I)
    return text


def _resources(text: str) -> set[str]:
    # Method identities cannot supply resource scope or capability adjectives.
    text = _scope_text(text)
    # PV and PVC names do not establish a general volume operation.
    text = re.sub(r"\bpersistent[- ]volume[- ]claims?\b", "PVC", text, flags=re.I)
    text = re.sub(r"\bpersistent[- ]volumes?\b", "PV", text, flags=re.I)
    return {name for name, pattern in _RESOURCE_RE.items() if pattern.search(text)}


def _name(value: str) -> str | None:
    quoted = value.startswith("`")
    value = value.strip("`").rstrip(".").casefold()
    if (not value or value in _NOT_METHOD or value in _RESOURCE_RE
            or value.removesuffix('s') in _RESOURCE_RE
            or (not quoted and not re.search(r"[-_/:.]", value))):
        return None
    return value


def _methods(text: str) -> list[str]:
    names = [_name(value) for match in _NAMED.finditer(text) for value in match.groups() if value]
    # A quoted compound name can also occur in a negative or a scope limit.
    names += [_name("`" + value + "`") for value in _BACKTICK.findall(text)
              if re.search(r"[-_/:.]", value)]
    return list(dict.fromkeys(name for name in names if name))


def _mentions(text: str, name: str) -> bool:
    return bool(re.search(r"(?<![\w./:-])" + re.escape(name) + r"(?![\w./:-])", text, re.I))


def _clauses(text: str) -> list[str]:
    # Keep commas and "and" inside resource lists. Two named methods fail closed.
    return [part.strip() for part in re.split(
        r"[\n;]+|(?<=[.!?])\s+|\s+\b(?:but|while|whereas|however)\b\s+", text.replace("*", "")
    ) if part.strip()]


@dataclass
class _Clause:
    text: str
    explicit: list[str]
    methods: list[str]
    resources: set[str]


def _source_clauses(text: str) -> list[_Clause]:
    result = []
    previous: list[str] = []
    for text_clause in _clauses(text):
        explicit = _methods(text_clause)
        # Only contrary clauses can use an earlier method in the same source.
        # This preserves a following "logs fail" qualifier, not positive scope.
        methods = explicit or (previous if _NEGATIVE.search(text_clause) or _LIMIT.search(text_clause) else [])
        result.append(_Clause(text_clause, explicit, methods, _resources(text_clause)))
        if explicit:
            previous = explicit
    return result


def resource_scope_required(question: str) -> bool:
    """Select explicit metadata scope questions, not general storage questions."""
    scope = re.sub(r"(?:[\w./:-]+/)?devbox-kube-read", " ", _scope_text(question), flags=re.I)
    return bool(
        (_READ.search(scope) or re.search(r"\b(?:resources?|kinds?|operations?|scope)\b", scope, re.I))
        and ((_resources(question) and _INTENT.search(question))
             or _mentions(question, "devbox-kube-read"))
    )


def _overlap(left: set[str], right: set[str]) -> bool:
    if left & right:
        return True
    # A broader denial also limits its named storage resources.
    return bool(("longhorn" in left and right & _STORAGE)
                or (left & {"disk", "node"} and "node_disk" in right))


def _limit_excludes(allowed: set[str], claimed: set[str]) -> bool:
    operations = {"logs", "list"}
    return bool((claimed - operations) - allowed or (
        allowed & operations and (claimed & operations) - allowed
    ))


def _contrary(clause: _Clause, method: str, resources: set[str]) -> bool:
    if not clause.methods and _GLOBAL_ABSENCE.search(clause.text) and _overlap(clause.resources, resources):
        return True
    if clause.methods != [method]:
        return False
    if not clause.resources and _UNAVAILABLE.search(clause.text):
        return True
    if _LIMIT.search(clause.text) and clause.resources and _limit_excludes(clause.resources, resources):
        return True
    return bool(_NEGATIVE.search(clause.text) and _overlap(clause.resources, resources))


def _supported(clause: _Clause, method: str, resources: set[str], claim: str, question: str) -> bool:
    scope = _scope_text(clause.text)
    binding = re.search(
        r"(?<![\w./:-])`?" + re.escape(method)
        + r"`?\s+(?:(?:only|currently|explicitly)\s+)?(?:is|supports?)\b",
        clause.text, re.I,
    )
    target = clause.text[binding.end():] if binding else ""
    if not (clause.explicit == [method] and binding and resources <= _resources(target)
            and _AFFIRM.search(scope) and _READ.search(scope)
            and not _UNCERTAIN.search(scope)):
        return False
    # Preserve a source's narrow operation shapes in the answer.
    if any(re.search(r"\b" + word + r"\b", target, re.I)
           and not re.search(r"\b" + word + r"\b", claim, re.I)
           for word in ("narrow", "named", "fixed")):
        return False
    # A capability adjective cannot transfer from a different source or method.
    return all(not re.search(r"\b" + word + r"\b", claim, re.I)
               or re.search(r"\b" + word + r"\b", scope, re.I)
               for word in ("installed", "live", "native", "approved")) and (
        not re.search(r"\bread[- ]only\b", question + " " + claim, re.I)
        or re.search(r"\bread[- ]only\b", scope, re.I)
    )


def _negative_supported(claim: str, methods: list[str], resources: set[str], evidence: list[_Clause]) -> bool:
    if len(methods) != 1 or not resources:
        return False
    method = methods[0]
    for source in evidence:
        if source.methods != [method]:
            continue
        if _LIMIT.search(claim):
            if _LIMIT.search(source.text) and resources == source.resources:
                return True
        elif (_NEGATIVE.search(source.text) and resources <= source.resources) or (
            _LIMIT.search(source.text) and source.resources and not resources & source.resources
        ):
            # Preserve the exact failure token if the answer states it.
            if "binding_window_unsupported" not in claim.casefold() or "binding_window_unsupported" in source.text.casefold():
                return True
    return False


def resource_scope_problem(
    question: str, answer: str, source_texts: Mapping[str, str] | Sequence[str],
) -> str | None:
    """Reject unsupported scope transfers and preserve cited contrary limits.

    Mapping keys never count as evidence. The caller supplies every complete
    cited source before display caps. A pass is not permission to use a method.
    """
    if not resource_scope_required(question):
        return None
    values = source_texts.values() if isinstance(source_texts, Mapping) else source_texts
    evidence = [clause for text in values for clause in _source_clauses(text)]
    requested = _resources(question)
    previous: list[str] = []
    checked = False
    for claim in _clauses(answer):
        explicit = _methods(claim)
        resources = _resources(claim)
        methods = explicit or previous
        if explicit:
            previous = explicit
        if _BOUNDED.search(claim) and _LACK.search(claim):
            target = resources or requested
            relevant = [source for source in evidence if source.resources & target
                        or any(_mentions(source.text, method) for method in methods)]
            if not relevant or any(
                source.explicit and (not methods or source.explicit == methods)
                and _AFFIRM.search(_scope_text(source.text)) and not _UNCERTAIN.search(_scope_text(source.text))
                and _overlap(source.resources, target)
                and not _LIMIT.search(source.text)
                for source in relevant
            ):
                return RESOURCE_SCOPE_REASON
            checked = True
            continue
        if _NEGATIVE.search(claim) or _LIMIT.search(claim):
            if not _negative_supported(claim, methods, resources, evidence):
                return RESOURCE_SCOPE_REASON
            checked = True
            continue
        if not explicit and not resources and not _INTENT.search(claim):
            continue
        # An unnamed or unscoped selection refers to the requested scope.
        resources = resources or requested
        if resources == {"longhorn"}:
            resources |= requested
        if not resources or len(explicit) != 1:
            return RESOURCE_SCOPE_REASON
        method = explicit[0]
        if (any(_contrary(source, method, resources) for source in evidence)
                or not any(_supported(source, method, resources, claim, question) for source in evidence)):
            return RESOURCE_SCOPE_REASON
        checked = True
    return None if checked else RESOURCE_SCOPE_REASON
