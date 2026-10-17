"""Bounded sandbox source records, not an execution or authorization engine.

One complete narrative record must retain every feature and every scope role.
Raw source text can veto support. It cannot supply positive provenance. A
source record never selects a method or grants current execution permission.
Unknown prose refuses a selection. This is not a general entailment parser.
"""
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from temporal_negative_qualifiers import _source_body_text

SANDBOX_CONTRACT_REASON = (
    "The cited bodies do not establish the stated sandbox features, method, "
    "task, app, root, phase, and scope in one coherent record. Limit the answer "
    "to source records, not method selection or current execution permission."
)
SANDBOX_CONTRACT_BODY_REASON = (
    "Complete cited source text is unavailable for sandbox-contract validation."
)
SANDBOX_CONTRACT_SUFFIX = " This is a source record, not current permission or a new live check."
SAFE_UNKNOWN = "The cited source text does not establish current sandbox execution permission."
BOUNDED_LACK = "The cited source records do not establish the full requested sandbox contract."
PREFIX = "The cited source records: "
FEATURES = (
    "private mount/PID/network namespaces",
    "a new private proc mount",
    "kernel denial of AF_INET and AF_INET6 socket creation",
    "an allowlist of Node, Git, libraries, and source only",
    "no host credential access",
    "no container-socket access",
    "an external timeout",
    "owned-process cleanup",
)
LIMIT = "this source record grants no current candidate execution or method selection"
PARTIAL_FEATURES = (
    "private user/network/PID/IPC/UTS namespaces and chroot; closed file descriptors, "
    "a cleared environment, dropped capabilities, noNewPrivs, and seccomp; "
    "no real HOME, repository, tailnet socket, host proc, or external interface; "
    "proc mount failed with errno 13; no normal private proc mount; "
    "allocation cause remains unknown; no Node/Git/libraries/source allowlist contract; "
    "this partial historical record is not a full sandbox contract or a method selection"
)
_LITERAL = r"`[^`\n]+`"
_QUOTES = r'''`[^`\n]+`|"[^"\n]+"|'[^'\n]+' '''.strip()
_DATE = r"20\d{2}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)?"
_ROLES = ("method", "task", "app", "root", "phase", "scope")
_HEADER = (
    rf"On (?P<event>{_DATE}), method (?P<method>{_LITERAL}) records a "
    rf"(?P<kind>disposable test sandbox contract|partial historical disposable test sandbox contract) "
    rf"for task (?P<task>{_LITERAL}), app (?P<app>{_LITERAL}), root (?P<root>{_LITERAL}), "
    rf"phase (?P<phase>{_LITERAL}), and scope (?P<scope>{_LITERAL}): "
)
_FULL = re.compile(_HEADER + re.escape("; ".join(FEATURES) + "; " + LIMIT))
_PARTIAL = re.compile(_HEADER + re.escape(PARTIAL_FEATURES))
_CONCEPT = re.compile(
    r"\b(?:sandboxes?|sandbox|disposable[- ]tests?|private[- ]namespaces?|"
    r"sandbox[- ]contracts?|execution[- ]gates?)\b", re.I,
)
_INTENT = re.compile(
    r"\b(?:contracts?|provides?|features?|requires?|constraints?|permissions?|"
    r"isolation|execute|execution|select|selection|methods?|gates?|safe|denial|allowlists?)\b", re.I,
)
_CONCEPT_PHRASE = re.compile(
    r"(?:(?:exact|existing|current|private|new|fresh|kernel|external|owned|process|"
    r"devbox|disposable|tests?|sandbox(?:es)?|contracts?|execution|gates?|features?|"
    r"permissions?|constraints?|isolation|mount|PID|network|namespaces?|proc|denial|"
    r"AF_INET6?|socket|creation|allowlist(?:ed|s)?|Node|Git|libraries|source|only|"
    r"no|host|credential|container|access|timeout|cleanup|and|or)[ /_-]*)+", re.I,
)
_DATA_ROLE = re.compile(
    r"\b(?:method|filename|file name|path|title|source|note|record|revision|version|tag|digest|build|named|called)"
    r"\s*(?:(?:is|of|:|=)\s*)?$", re.I,
)
_ROLE = re.compile(r"\b(?P<role>method|task|app|root|phase|scope|object|action|operation)\s*(?:(?:is|:|=)\s*)?$", re.I)
_PLAIN = r"[A-Za-z0-9_/.#:-]+"
_PROPERTY = re.compile(
    rf"(?<!\w)(?P<quote>[`\"']?)(?P<role>method|task|app|root|phase|scope|object|action|operation|"
    rf"proc mount|socket denial|allowlist|timeout|cleanup|contract|status|host credential access|container-socket access)(?P=quote)\s*"
    rf"(?:(?:is|was|remains)\s*[:=]?|[:=])\s*(?P<value>{_QUOTES}|[^\n;]+)", re.I,
)
# These qualifiers veto a full record only in an actual source scope.
_FEATURE = re.compile(
    r"\b(?:proc|mount|namespaces?|AF_INET6?|sockets?|allowlist|Node|Git|libraries|"
    r"credentials?|container[- ]sockets?|timeout|cleanup|contract|execution|gate|candidate|sandbox)\b", re.I,
)
_FAILURE = re.compile(
    r"\b(?:failed|fails?|failure|denied|errno\s*13|unknown|unverified|not installed|"
    r"not checked|not tested|unavailable|absent|disabled|blocked|held|incomplete|partial|plan|planned|proposal|soft timeout|"
    r"RuntimeDefault|proxy egress|existing empty|host proc)\b", re.I,
)
_NEGATIVE_FEATURE = re.compile(
    r"\b(?:no|not|without|cannot|missing)\s+(?:(?:a|the|new|normal|private|fresh|kernel|external|owned)[- ]+)*"
    r"(?:proc|mount|namespaces?|AF_INET6?|socket|allowlist|timeout|cleanup|full|complete|execution)", re.I,
)
_UNSAFE_FEATURE = re.compile(
    r"\bAF_INET6?\b.{0,60}\bsocket\b.{0,40}\b(?:allowed|permitted|succeeds?|accessible)\b"
    r"|\b(?:host credentials?|container[- ]socket)\b.{0,50}\b(?:allowed|permitted|accessible)\b"
    r"|\ballowlist\s+(?:includes|allows|contains)\s+(?!Node, Git, libraries, and source only\b)"
    r"|\bcleanup\s+excludes\b", re.I,
)
_PERMISSION = re.compile(
    r"\b(?:current|caller|now)\b.{0,70}\b(?:permission|execution)\b.{0,30}"
    r"\b(?:approved|granted|authorized|allowed)\b", re.I,
)


def _unquote(value: str) -> str:
    return value[1:-1] if len(value) > 1 and value[0] in "`\"'" and value[-1] == value[0] else value


def _literal_role(question: str, match: re.Match) -> str:
    before = re.sub(r"[*_]", "", question[:match.start()])
    if role := _ROLE.search(before):
        return role["role"].lower()
    if re.search(r"\b(?:via|using|through)\s+(?:method\s+)?$", before, re.I):
        return "method"
    if _DATA_ROLE.search(before) or re.match(
        r"\s+(?:method|filename|path|title|source|revision|version|tag|digest)\b", question[match.end():], re.I,
    ):
        return "data"
    value = _unquote(match[0])
    if _CONCEPT_PHRASE.fullmatch(value):
        return "concept"
    if re.fullmatch(r"[A-Za-z0-9 ,()'-]+", value) and (_CONCEPT.search(value) or _INTENT.search(value)):
        return "ambiguous_concept"
    return "unbound"


def sandbox_contract_required(question: str) -> bool:
    """Collect operative contract clauses, not metadata-only name clauses."""
    # Explicit identity roles stay opaque. A formatted concept before a
    # version predicate can still express an operative contract constraint.
    def concept(match):
        role = _literal_role(question, match)
        if role in {"concept", "ambiguous_concept"}:
            return _unquote(match[0])
        if (role == "data" and _CONCEPT_PHRASE.fullmatch(_unquote(match[0]))
                and not _DATA_ROLE.search(question[:match.start()])
                and re.match(r"\s+(?:version|revision)\s+(?:provides?|requires?|supports?)\b",
                             question[match.end():], re.I)):
            return _unquote(match[0])
        return " "

    text = re.sub(r"[*_]", "", re.sub(_QUOTES, concept, question))
    # A later metadata question cannot cancel an earlier operative clause.
    text = re.sub(r"(?:\s+and\s+|;\s*)(?=(?:what|which|how|can|does|describe|identify|explain|state|show)\b)",
                  "\n\n", text, flags=re.I)
    for clause in _units(text):
        clause = re.sub(r"^(?:Currently|Now),?\s*", "", clause, flags=re.I)
        if re.match(r"\s*what\s+is\s+(?:the\s+)?method\s+(?!for\b|that\b|which\b)", clause, re.I):
            continue

        def identity(match):
            # A predicate after a role noun is not a named identity.
            if match[2].casefold() in {"is", "are", "of", "for", "provides", "provide",
                                       "requires", "require", "supports", "support", "applies", "apply"}:
                return match[0]
            return match[1] + " "

        clause = re.sub(
            rf"(\b(?:method|task|app|root|phase|scope|source|filename|revision|version)\s*(?:(?:is|:|=)\s*)?)({_PLAIN})",
            identity, clause, flags=re.I,
        )
        metadata = re.search(
            r"\b(?:filename|file name|meaning|definition|version|revision|title|spell|means?)\b", clause, re.I,
        )
        operative = re.search(
            r"\b(?:provides?|features?|requires?|constraints?|permissions?|isolation|execute|execution|"
            r"select|selection|gates?|safe|denial|allowlists?)\b", clause, re.I,
        )
        if metadata and not operative:
            continue
        if _CONCEPT.search(clause) and _INTENT.search(clause):
            return True
    return False


def _question_roles(question: str) -> dict[str, set[str]]:
    roles: dict[str, set[str]] = {}
    for match in re.finditer(_QUOTES, question):
        role = _literal_role(question, match)
        if role not in {"concept", "data"}:
            roles.setdefault(role, set()).add(_unquote(match[0]))
    masked = re.sub(_QUOTES, "\x00", question)
    for match in re.finditer(
        rf"\b(?P<role>method|task|app|root|phase|scope|object|action|operation)\s*(?:(?:is|:|=)\s*)?(?P<value>{_PLAIN})", masked, re.I,
    ):
        # A noun in an interrogative does not declare an identity.
        if (re.search(r"\b(?:what|which|same|one|each|every|a|the)\s*$", masked[:match.start()], re.I)
                and match["value"].casefold() in {"is", "are", "provides", "provide", "applies", "apply",
                                                    "can", "does", "for", "of", "and", "or", "that", "which"}):
            continue
        roles.setdefault(match["role"].lower(), set()).add(match["value"])
    for match in re.finditer(rf"\b(?:via|through|using)\s+(?:method\s+)?({_PLAIN})", masked, re.I):
        roles.setdefault("method", set()).add(match[1])
    return roles


def _units(text: str) -> list[str]:
    """Keep identity bytes and event times intact across source soft wraps."""
    saved = []
    marker = "\x00sandbox-literal"
    while marker in text:
        marker += "_"

    def protect(match):
        saved.append(match[0])
        return marker + str(len(saved) - 1) + "\x00"

    value = re.sub(_QUOTES + "|" + _DATE, protect, text)
    value = re.sub(r"(?<![.!?\n])\n(?!\n)", " ", value)
    value = re.sub(r"[ \t]+", " ", value)
    token = re.compile(re.escape(marker) + r"(\d+)\x00")
    return [token.sub(lambda m: saved[int(m[1])], part.strip().rstrip(".!?"))
            for part in re.split(r"\n+|(?<=[.!?])\s+", value) if part.strip()]


@dataclass(frozen=True)
class _Record:
    text: str
    full: bool
    fields: dict[str, str]


def _record(text: str) -> _Record | None:
    for full, grammar in ((True, _FULL), (False, _PARTIAL)):
        if match := grammar.fullmatch(text):
            fields = match.groupdict()
            if (fields["kind"] == "disposable test sandbox contract") != full:
                return None
            try:
                datetime.fromisoformat(fields["event"].replace("Z", "+00:00"))
            except ValueError:
                return None
            if full and _unquote(fields["phase"]) not in {"test", "historical-test", "preflight"}:
                return None
            return _Record(text, full, fields)
    return None


def _records(text: str) -> list[_Record]:
    return [record for unit in _units(text) if (record := _record(unit))]


def _matches(record: _Record, roles: dict[str, set[str]]) -> bool:
    return all(role in _ROLES and values == {_unquote(record.fields[role])} for role, values in roles.items())


def _values(texts: Mapping[str, str] | Sequence[str]) -> list[str]:
    return [str(value or "") for value in (texts.values() if isinstance(texts, Mapping) else texts)]


def _raw_units(text: str) -> list[str]:
    # Raw presentation is veto-only. A heading or fence cannot hide a limit.
    text = re.sub(r"(?m)^[ \t]*(?:`{3,}|~{3,})([^\n]*)$",
                  lambda match: "\n\n" + match[1] + "\n\n", text)
    text = re.sub(r"(?m)^\s*#{1,6}\s+", "", text)
    return _units(text)


def _contrary(raw: str, record: _Record) -> bool:
    """Follow source roles in order. The draft never supplies source context."""
    units = _raw_units(raw)
    # A prefix can inherit only its first actual source anchor.
    anchors = [match for unit in units for match in re.finditer(_HEADER, unit)]
    context = bool(anchors and all(anchors[0][role] == record.fields[role] for role in _ROLES))
    for unit in units:
        # A complete source record supplies context, but its expected negative
        # protection phrases are not contrary claims.
        declared = _record(unit)
        if declared:
            context = all(declared.fields[role] == record.fields[role] for role in _ROLES)
            if context and declared.full != record.full:
                return record.full
            continue
        if anchor := re.search(_HEADER, unit):
            context = all(anchor[role] == record.fields[role] for role in _ROLES)
        remainder = unit
        for prop in _PROPERTY.finditer(unit):
            role, value = prop["role"].lower(), _unquote(prop["value"].strip().rstrip("."))
            if role == "method":
                context = value == _unquote(record.fields["method"])
            elif context and role in _ROLES and value != _unquote(record.fields[role]):
                return True
            elif context and role in {"action", "operation", "object"}:
                return True
            elif context and role not in _ROLES:
                # Unknown feature qualifiers refuse a full-contract selection.
                allowed = {
                    "proc mount": "new private proc mount",
                    "socket denial": "kernel denial of AF_INET and AF_INET6 socket creation",
                    "allowlist": "Node, Git, libraries, and source only",
                    "timeout": "external", "cleanup": "owned-process", "contract": "full",
                    "status": "checked", "host credential access": "denied",
                    "container-socket access": "denied",
                }
                if record.full and value != allowed.get(role):
                    return True
            remainder = remainder.replace(prop[0], "")
        # A direct named method limit can restore its own local scope.
        named = re.search(rf"(?P<explicit>\bmethod\s+)?(?P<method>{_QUOTES})\s+(?:has|uses|requires|cannot|failed|lacks|records|is|was|remains)\b", remainder, re.I)
        named_subject = named and (named["explicit"] or not _CONCEPT_PHRASE.fullmatch(_unquote(named["method"])))
        if named_subject:
            context = _unquote(named["method"]) == _unquote(record.fields["method"])
        plain_subject = re.match(
            rf"(?P<explicit>method\s+)?(?P<method>{_PLAIN})\s+(?:has|uses|requires|cannot|failed|lacks|is|was|remains)\b",
            remainder, re.I,
        )
        if plain_subject and (plain_subject["explicit"] or re.search(r"[0-9_/.#:-]", plain_subject["method"])):
            context = plain_subject["method"] == _unquote(record.fields["method"])
        # Plain method subjects bind exact bytes too.
        for subject in (record.fields["method"], _unquote(record.fields["method"])):
            if re.match(r"(?i:method\s+)?" + re.escape(subject) + r"(?i:\s+(?:has|uses|requires|cannot|failed|lacks|is|was|remains)\b)", remainder):
                context = True
        # A direct observation keeps its explicit task and root. A matching
        # method cannot transfer a different task's failure into this scope.
        if named_subject or plain_subject:
            scopes = re.finditer(
                rf"\b(?P<role>task|app|root|phase|scope)\s+(?P<value>{_QUOTES}|{_PLAIN})",
                remainder, re.I,
            )
            if any(_unquote(match["value"]) != _unquote(record.fields[match["role"].lower()]) for match in scopes):
                context = False
        if context and record.full and _FEATURE.search(remainder) and (
            _FAILURE.search(remainder) or _NEGATIVE_FEATURE.search(remainder) or _UNSAFE_FEATURE.search(remainder)
        ):
            return True
        if context and _PERMISSION.search(remainder):
            return True
    return False


def sandbox_contract_problem(
    question: str, answer: str, cited_texts: Mapping[str, str] | Sequence[str], *,
    retrieved_texts: Mapping[str, str] | Sequence[str] = (),
) -> str | None:
    """Require one complete body record. Never infer capability or permission."""
    if not sandbox_contract_required(question):
        return None
    cited = _values(cited_texts)
    if not cited or any(not body.strip() for body in cited):
        return SANDBOX_CONTRACT_BODY_REASON
    supported = [record for body in cited for record in _records(_source_body_text(body))]
    roles = _question_roles(question)
    if answer.strip() == SAFE_UNKNOWN:
        for raw in cited + _values(retrieved_texts):
            if any(_PERMISSION.search(unit) and re.search(r"\b(?:sandbox|execution)\b", unit, re.I)
                   for unit in _raw_units(raw)):
                return SANDBOX_CONTRACT_REASON
        return None
    if answer.strip() == BOUNDED_LACK:
        # A complete body can defeat this facet even when caller permission
        # stays unknown. Raw text alone cannot establish positive support.
        if any(role not in _ROLES or len(values) != 1 for role, values in roles.items()):
            return SANDBOX_CONTRACT_REASON
        return SANDBOX_CONTRACT_REASON if any(record.full and _matches(record, roles) for record in supported) else None
    if not answer.startswith(PREFIX):
        return SANDBOX_CONTRACT_REASON
    records = [_record(unit) for unit in _units(answer[len(PREFIX):])]
    if not records or any(record is None for record in records):
        return SANDBOX_CONTRACT_REASON
    for record in records:
        if record not in supported or not _matches(record, roles):
            return SANDBOX_CONTRACT_REASON
        if any(_contrary(raw, record) for raw in cited + _values(retrieved_texts)):
            return SANDBOX_CONTRACT_REASON
    return None
