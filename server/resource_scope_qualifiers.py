"""Bounded checks for a metadata method's resource scope.

A positive needs one explicit method and all claimed resources in one source
clause. A later clause, another method, a heading, or another source cannot
supply missing scope. Only narrative body clauses can support a capability.
All raw source clauses can supply contrary limits. This is not an entailment
parser or an authorization check.

False negatives are intentional. Positive names need backticks, a path, or a
compound identifier. Pronouns, coordinated methods, generic workload scope,
and indirect positive descriptions cannot establish a capability. A positive
target accepts only the declared resource and qualifier vocabulary. Other
predicate words and unquoted identities fail closed. Unbound answer clauses
need a known background or recorded-history form. Unknown clauses abstain.
"""
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from temporal_negative_qualifiers import _normalize_negation, _source_body_text

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
_INTENT = re.compile(
    r"(?<![\w./:-])(?:channels?|methods?|readers?|tools?|CLI|REST|API|endpoints?|"
    r"supports?|capabilit\w*)(?![\w/:-]|\.[A-Za-z0-9])", re.I,
)
_NAMED_READ = re.compile(r"\b(?:read[- ]only|reads?|lists?|gets?|fetch(?:es)?|queries|query|access(?:es)?|inspects?)\b", re.I)
_GENERIC_SCOPE = re.compile(r"\b(?:resources?|kinds?|operations?|scope|capabilit\w*)\b", re.I)
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
_MUTATING = re.compile(
    r"\bread[- /]?write\b|\b(?:delet(?:e|es|ed|ing)|writ(?:e|es|ing)|wrote|written|"
    r"updat(?:e|es|ed|ing)|patch(?:es|ed|ing)?|remov(?:e|es|ed|ing)|"
    r"destroy(?:s|ed|ing)?|modif(?:y|ies|ied|ying)|creat(?:e|es|ed|ing)|"
    r"apply|applies|applied|applying|mutat(?:e|es|ed|ing))\b", re.I,
)
_MUTATION_NOUN = re.compile(r"\b(?:deletions?|removals?|destructions?|creations?|modifications?|mutations?)\b", re.I)
_BENIGN_MUTATION_RECORD = re.compile(
    r"\s*(?:The\s+)?(?:deletion|removal|destruction|creation|modification|mutation)\s+"
    r"(?:date|history)\s+(?:is|was|remains)\s+(?:recorded|documented|historical)[.!]?\s*", re.I,
)
_SCOPE_WORDS = {
    'a', 'an', 'the', 'and', 'or', 'with', 'for', 'of', 'to', 'in', 'on', 'from', 'via', 'through',
    'supported', 'approved', 'installed', 'live', 'native', 'current', 'currently', 'explicitly',
    'read', 'only', 'narrow', 'named', 'fixed', 'limited', 'restricted', 'exact', 'user', 'agent', 'ua',
    'metadata', 'method', 'methods', 'channel', 'channels', 'reader', 'readers', 'tool', 'tools',
    'endpoint', 'endpoints', 'cli', 'api', 'rest', 'capability', 'capabilities', 'operation', 'operations',
    'kubernetes', 'workload', 'workloads', 'internal', 'longhorn', 'volume', 'volumes', 'replica', 'replicas',
    'node', 'nodes', 'disk', 'disks', 'capacity', 'backup', 'backups', 'pod', 'pods', 'deployment',
    'deployments', 'statefulset', 'statefulsets', 'cron', 'cronjob', 'cronjobs', 'job', 'jobs',
    'pv', 'pvs', 'pvc', 'pvcs', 'persistent', 'claim', 'claims', 'log', 'logs', 'list', 'lists', 'listing',
}
_SELECTION = re.compile(r"\b(?:use|uses|using|via|through)\b", re.I)
_AMBIGUOUS_SELECTION = re.compile(
    r"\b(?:use|uses|using|via|through)\s+(?:it|this|that|them|these|those|"
    r"the\s+(?:reader|method|helper|tool|channel|endpoint))\b", re.I,
)
_TO_ACTION = re.compile(r"\bto\s+(?P<verb>[A-Za-z]+(?:-[A-Za-z]+)?)\b", re.I)
_MODAL_ACTION = re.compile(
    r"\b(?:can|could|may|might|will|would)\s+(?:(?:also|only|still)\s+)*"
    r"(?P<verb>[A-Za-z]+(?:-[A-Za-z]+)?)\b", re.I,
)
_READ_ACTIONS = {'read', 'list', 'get', 'fetch', 'query', 'inspect', 'show', 'describe', 'retrieve', 'access'}
_CLAIM_WORDS = _SCOPE_WORDS | _READ_ACTIONS | {
    'use', 'uses', 'using', 'is', 'are', 'can', 'could', 'cited', 'retrieved', 'supplied', 'recorded',
    'text', 'source', 'sources', 'evidence', 'notes', 'records', 'support', 'as', 'partial', 'fact',
}
_BENIGN_BACKGROUND = re.compile(
    r'\s*(?:(?:This|It)\s+is\s+)?(?:(?:Recorded|Historical|Background)\s+)?'
    r'(?:background|context|information)[.!]?\s*', re.I,
)
_PARTIAL = re.compile(r"\b(?:partial|source[- ]only)\b", re.I)
_BOUNDED = re.compile(r"\b(?:cited|retrieved|supplied|recorded)\s+(?:source\s+)?(?:text|sources?|evidence|notes?)\b", re.I)
_LACK = re.compile(
    r"\b(?:does?\s+not|cannot)\s+(?:establish|identify|confirm|verify|show)\b"
    r"|\bno\b.{0,100}\b(?:found|recorded|identified|established|contains?|channel|method)\b", re.I,
)
_NAME = r"`[^`\n]+`|[A-Za-z0-9_/.:-]+"
_CONTRACTED_AUX = (
    r"(?:isn|aren|wasn|weren|hasn|haven|hadn|doesn|don|didn|can|won|wouldn|couldn|"
    r"shouldn|mustn|needn|ain)['\u2018\u2019\u02bc\uff07\u201b]t"
)
_NAMED = re.compile(
    rf"\b(?:use|uses|using|via|through)\s+(?:the\s+)?(?P<after>{_NAME})"
    rf"|(?P<before>{_NAME})\s+(?:(?:only|currently|explicitly|also)\s+)?(?:is|supports?|reads?|lists?|provides?|allows?|cannot|does\s+not|has|{_CONTRACTED_AUX})\b"
    rf"|\b(?:method|channel|reader|tool|endpoint)\s*(?:is|:|=)\s*(?:the\s+)?(?P<is>{_NAME})"
    rf"|\b(?:by|for)\s+(?P<for>{_NAME})", re.I,
)
_BACKTICK = re.compile(r"`([^`\n]+)`")
_COORDINATED_NAMES = re.compile(
    r'`[^`\n]+`(?:\s*(?:,|\b(?:and/or|and|or|with)\b)\s*`[^`\n]+`)+', re.I,
)
_NOT_METHOD = {
    "read-only", "read_only", "node-disk", "node-disks", "pod-list", "pod-logs",
    "binding_window_unsupported", "exact-user-agent", "kinds", "cron-job", "cron-jobs",
}
_STORAGE = {"longhorn", "volume", "replica", "disk", "node_disk", "capacity", "backup", "pv", "pvc", "node"}
_UNAVAILABLE = re.compile(
    r"\b(?:is|remains)\s+(?:(?:currently|still)\s+)?(?:unavailable|unsupported|"
    r"uninstalled|blocked|not\s+(?:supported|installed|available|approved))\b"
    r"|\b(?:does|do)\s+not\s+support\b"
    r"|\b(?:cannot|can not|ca not)\s+(?:read|list|get|fetch|query|inspect|access)\b"
    r"|binding_window_unsupported", re.I,
)
_GLOBAL_ABSENCE = re.compile(
    r"\bno\s+(?:(?:approved|supported|installed|read[- ]only|longhorn|volume|replica|"
    r"node|disk|backup|metadata|cron[- ]?jobs?|jobs?)\s+)*(?:channels?|methods?|readers?|tools?|endpoints?)\b", re.I,
)


def _scope_text(text: str) -> str:
    for method in _methods(text):
        text = text.replace('`' + method + '`', ' ')
        if re.search(r'[-_/:.]', method):
            text = re.sub(r'(?<![\w./:-])' + re.escape(method) + r'(?![\w./:-])', ' ', text)
    # Normalize property words, never a quoted literal or identity token.
    parts = re.split(r'(`+[^`\n]*`+)', text)
    return ''.join(part if index % 2 else _normalize_negation(part)
                   for index, part in enumerate(parts))


def _mutation_claim(scope: str) -> bool:
    if _MUTATING.search(scope):
        return True
    # Unknown mutation-noun clauses cannot become harmless background.
    return bool(_MUTATION_NOUN.search(scope) and not _BENIGN_MUTATION_RECORD.fullmatch(scope))


def _resources(text: str) -> set[str]:
    # Method identities cannot supply resource scope or capability adjectives.
    text = _scope_text(text)
    # PV and PVC names do not establish a general volume operation.
    text = re.sub(r"\bpersistent[- ]volume[- ]claims?\b", "PVC", text, flags=re.I)
    text = re.sub(r"\bpersistent[- ]volumes?\b", "PV", text, flags=re.I)
    return {name for name, pattern in _RESOURCE_RE.items() if pattern.search(text)}


def _name(value: str) -> str | None:
    quoted = value.startswith('`') and value.endswith('`')
    value = value[1:-1] if quoted else value
    if not quoted and '/' not in value:
        value = value.rstrip('.')
    label = value.casefold()
    if (not value or label in _NOT_METHOD or label in _RESOURCE_RE
            or label.removesuffix('s') in _RESOURCE_RE
            or (not quoted and not re.search(r'[-_/:.]', value))):
        return None
    if not value.startswith(('/', './', '../', '~/')):
        remainder = value
        for pattern in _RESOURCE_RE.values():
            remainder = pattern.sub(' ', remainder)
        remainder = re.sub(r'\b(?:metadata|and|or|read[- ]only|narrow|named|fixed|kinds?)\b', ' ', remainder, flags=re.I)
        if not re.sub(r'[\s,/|()+-]', '', remainder):
            return None
    return value


def _quoted_names(text: str) -> list[str]:
    return list(dict.fromkeys(name for value in _BACKTICK.findall(text)
                             if (name := _name('`' + value + '`'))))


def _methods(text: str) -> list[str]:
    names = [_name(value) for match in _NAMED.finditer(text) for value in match.groups() if value]
    # A quoted compound name can also occur in a negative or a scope limit.
    names += [_name("`" + value + "`") for value in _BACKTICK.findall(text)
              if re.search(r"[-_/:.]", value)]
    names += [name for chain in _COORDINATED_NAMES.finditer(text) for name in _quoted_names(chain[0])]
    return list(dict.fromkeys(name for name in names if name))


def _mentions(text: str, name: str) -> bool:
    return bool(re.search(r"(?<![\w./:-])" + re.escape(name) + r"(?![\w./:-])", text))


def _clauses(text: str) -> list[str]:
    # Protect literal identities before presentation removal and clause splits.
    prefix = '\x00scope-literal'
    while prefix in text:
        prefix += '_'
    literals = []

    def protect(match):
        literals.append(match[0])
        return prefix + str(len(literals) - 1) + '\x00'

    masked = re.sub(r'`+[^`\n]*`+', protect, text)
    for marker in (r'\*\*', r'\*'):
        masked = re.sub(r'(?<![\w./:-])' + marker + r'(\S(?:.*?\S)?)' + marker
                        + r'(?![\w./:-])', r'\1', masked)
    parts = re.split(r'[\n;]+|(?<=[.!?])\s+|\s+\b(?:but|while|whereas|however)\b\s+', masked)
    token = re.compile(re.escape(prefix) + r'(\d+)\x00')
    return [token.sub(lambda match: literals[int(match[1])], part.strip())
            for part in parts if part.strip()]


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
        scope = _scope_text(text_clause)
        contrary = bool(_NEGATIVE.search(scope) or _LIMIT.search(scope) or _mutation_claim(scope))
        # A raw quoted identity can veto without establishing positive scope.
        possible = list(dict.fromkeys(explicit + _quoted_names(text_clause))) if contrary else explicit
        methods = possible or (previous if contrary else [])
        result.append(_Clause(text_clause, explicit, methods, _resources(text_clause)))
        if possible:
            previous = possible
    return result


# These complete question shapes assign data roles, not resource operations.
# Unknown forms keep the original selector. This is not a general query parser.
# Multiword identities remain data, but their roles keep the old selector.
_QUESTION_ID = r"(?:`[^`\s]+`|[A-Za-z0-9_/.:-]+)"
# An unquoted app needs a complete app token, not a path suffix.
_QUESTION_APP = r"(?:`[^`\s]+`|[A-Za-z][A-Za-z0-9_.-]*)"
_QUESTION_RELEASE_CONCEPT = (
    r"(?:(?:dev\s+app-only|app-only\s+dev|dev|app-only)\s+)?"
    r"release\s+(?:interface|scope)"
)
_QUESTION_RELEASE_CONCEPT = (
    r"(?:`" + _QUESTION_RELEASE_CONCEPT + r"`|" + _QUESTION_RELEASE_CONCEPT + r")"
)
# The release validator does not bind "through" to a method.
_QUESTION_RELEASE_HEAD = (
    r"(?P<subject>" + _QUESTION_APP + r")\s+"
    r"(?P<concept>" + _QUESTION_RELEASE_CONCEPT + r")"
    r"(?:\s+(?:using|via)\s+(?P<method>" + _QUESTION_ID + r"))?"
)
_QUESTION_RELEASE_REVISION = r"(?:\s+for\s+revision\s+(?P<revision>" + _QUESTION_ID + r"))?"
_QUESTION_RELEASE_RECORD = re.compile(
    r"\s*Which\s+" + _QUESTION_RELEASE_HEAD
    + r"(?:\s+and\s+(?P<scope>scope))?\s+did\s+the\s+source\s+record"
    + _QUESTION_RELEASE_REVISION + r"[?.]?\s*", re.I,
)
_QUESTION_RELEASE_CURRENT = re.compile(
    r"\s*What\s+is\s+the\s+current\s+" + _QUESTION_RELEASE_HEAD
    + r"(?:\s+after\s+the\s+(?P<event>"
    r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+\d{1,2}\s+deployment(?:\s+of\s+" + _QUESTION_ID + r")?))?"
    r"(?:,\s+and\s+which\s+(?P<scope>scope)\s+does\s+it\s+cover)?"
    + _QUESTION_RELEASE_REVISION + r"[?.]?\s*", re.I,
)


def _question_operation_text(question: str) -> str:
    """Remove only bound data roles in a complete release-record question.

    A method identifies the recorded release interface here. It does not ask
    for a metadata operation. A scope and a deployment event describe that
    release. Any extra operation or unknown clause retains the original text.
    Multiword identities remain outside this bounded subset, even for real methods.
    This function cannot supply positive scope to an answer or source.
    """
    for pattern in (_QUESTION_RELEASE_RECORD, _QUESTION_RELEASE_CURRENT):
        match = pattern.fullmatch(question)
        if match:
            chars = list(question)
            for role, value in match.groupdict().items():
                if value is not None:
                    start, end = match.span(role)
                    chars[start:end] = ' ' * (end - start)
            return ''.join(chars)
    return question


def resource_scope_required(question: str) -> bool:
    """Select resource operations, not bound release-record data roles."""
    operation_text = _question_operation_text(question)
    methods = list(dict.fromkeys(_methods(operation_text) + _quoted_names(operation_text)))
    scope = _scope_text(operation_text)
    for method in methods:
        scope = scope.replace('`' + method + '`', ' ')
    named_read = methods and (_NAMED_READ.search(scope) or _GENERIC_SCOPE.search(scope))
    described_scope = (
        (_READ.search(scope) or _GENERIC_SCOPE.search(scope))
        and _resources(operation_text) and _INTENT.search(scope)
    )
    return bool(named_read or described_scope)


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
    scope = _scope_text(clause.text)
    if not clause.methods and _GLOBAL_ABSENCE.search(scope) and _overlap(clause.resources, resources):
        return True
    if method not in clause.methods:
        return False
    if not clause.resources and _UNAVAILABLE.search(scope):
        return True
    if (_mutation_claim(scope) and not _NEGATIVE.search(scope)
            and (not clause.resources or _overlap(clause.resources, resources))):
        return True
    if _LIMIT.search(scope) and clause.resources and _limit_excludes(clause.resources, resources):
        return True
    return bool(_NEGATIVE.search(scope) and _overlap(clause.resources, resources))


def _positive_scope_target(target: str) -> bool:
    # One predicate can name resources and qualifiers, not another predicate.
    scope = _scope_text(target)
    for token in re.findall(r'[A-Za-z0-9_]+(?:[-/.:][A-Za-z0-9_]+)+', scope):
        if _name(token) is not None:
            return False
    words = re.findall(r'[A-Za-z][A-Za-z0-9_]*', scope)
    return bool(words and all(word.casefold() in _SCOPE_WORDS for word in words))


def _supported(clause: _Clause, method: str, resources: set[str], claim: str, question: str) -> bool:
    scope = _scope_text(clause.text)
    binding = re.search(
        r"(?<![\w./:-])`?" + re.escape(method)
        + r"`?\s+(?i:(?:(?:only|currently|explicitly)\s+)?(?:is|supports?))\b",
        clause.text,
    )
    target = clause.text[binding.end():] if binding else ""
    quoted = _quoted_names(clause.text)
    coordinated = any(_quoted_names(chain[0]) for chain in _COORDINATED_NAMES.finditer(clause.text))
    if not (not coordinated and clause.explicit == [method] and (not quoted or quoted == [method])
            and binding and resources <= _resources(target)
            and _AFFIRM.search(scope) and _READ.search(scope)
            and not _UNCERTAIN.search(scope) and not _mutation_claim(scope)
            and _positive_scope_target(target)):
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
    claim_scope = _scope_text(claim)
    for source in evidence:
        if source.methods != [method]:
            continue
        scope = _scope_text(source.text)
        if _LIMIT.search(claim_scope):
            if _LIMIT.search(scope) and resources == source.resources:
                return True
        elif (_NEGATIVE.search(scope) and resources <= source.resources) or (
            _LIMIT.search(scope) and source.resources and not resources & source.resources
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
    values = list(source_texts.values() if isinstance(source_texts, Mapping) else source_texts)
    # Keep all raw limits. Only eligible body text can establish a capability.
    evidence = [clause for text in values for clause in _source_clauses(text)]
    body_evidence = [clause for text in values for clause in _source_clauses(_source_body_text(text))]
    requested = _resources(question)
    previous: list[str] = []
    checked = False
    for claim in _clauses(answer):
        explicit = _methods(claim)
        resources = _resources(claim)
        methods = explicit or previous
        if explicit:
            previous = explicit
        claim_scope = _scope_text(claim)
        if (_AMBIGUOUS_SELECTION.search(claim_scope)
                or (_SELECTION.search(claim_scope) and not explicit)):
            # A partial fact cannot supply an unnamed operative selection.
            return RESOURCE_SCOPE_REASON
        actions = list(_MODAL_ACTION.finditer(claim_scope))
        if _SELECTION.search(claim_scope):
            actions.extend(_TO_ACTION.finditer(claim_scope))
        if actions and not _NEGATIVE.search(claim_scope) and any(
            action['verb'].casefold() not in _READ_ACTIONS or len(explicit) != 1 or not resources
            for action in actions
        ):
            return RESOURCE_SCOPE_REASON
        if _mutation_claim(claim_scope) and not (
            _BOUNDED.search(claim_scope) and _LACK.search(claim_scope) and not _SELECTION.search(claim_scope)
        ):
            # This read-oriented guard never establishes a mutating operation.
            return RESOURCE_SCOPE_REASON
        if _BOUNDED.search(claim_scope) and _LACK.search(claim_scope):
            target = resources or requested
            relevant = [source for source in evidence if source.resources & target
                        or any(_mentions(source.text, method) for method in methods)]
            if not relevant or any(
                source.explicit and (not methods or source.explicit == methods)
                and _AFFIRM.search(_scope_text(source.text)) and not _UNCERTAIN.search(_scope_text(source.text))
                and _overlap(source.resources, target)
                and not _LIMIT.search(_scope_text(source.text))
                for source in relevant
            ):
                return RESOURCE_SCOPE_REASON
            checked = True
            continue
        if _NEGATIVE.search(claim_scope) or _LIMIT.search(claim_scope):
            if not _negative_supported(claim, methods, resources, body_evidence):
                return RESOURCE_SCOPE_REASON
            checked = True
            continue
        if not explicit and not resources and not _INTENT.search(claim_scope):
            if _BENIGN_BACKGROUND.fullmatch(claim_scope) or _BENIGN_MUTATION_RECORD.fullmatch(claim_scope):
                continue
            # Unknown capability-looking tails do not count as background.
            return RESOURCE_SCOPE_REASON
        # An unnamed or unscoped selection refers to the requested scope.
        resources = resources or requested
        partial_fact = (
            _BOUNDED.search(claim_scope) and _PARTIAL.search(claim_scope)
            and not _SELECTION.search(claim_scope) and not actions
        )
        if not partial_fact:
            # A named resource cannot erase the requested read operation.
            resources |= requested & {'logs', 'list'}
        if resources == {"longhorn"}:
            resources |= requested
        if any(word.casefold() not in _CLAIM_WORDS for word in re.findall(r'[A-Za-z][A-Za-z0-9_]*', claim_scope)):
            return RESOURCE_SCOPE_REASON
        if any(_quoted_names(chain[0]) for chain in _COORDINATED_NAMES.finditer(claim)):
            return RESOURCE_SCOPE_REASON
        selected = list(dict.fromkeys(explicit + _quoted_names(claim)))
        if not resources or len(selected) != 1:
            return RESOURCE_SCOPE_REASON
        method = selected[0]
        if (any(_contrary(source, method, resources) for source in evidence)
                or not any(_supported(source, method, resources, claim, question) for source in body_evidence)):
            return RESOURCE_SCOPE_REASON
        checked = True
    return None if checked else RESOURCE_SCOPE_REASON
