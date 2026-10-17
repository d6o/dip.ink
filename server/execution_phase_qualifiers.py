"""Bounded execution-phase records, not current capability or authority.

One narrative record binds the operation, identities, time, deadlines, and
proof domain. Raw values can veto that record, but cannot supply provenance.
This grammar does not infer general entailment or inspect an executable.
"""
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from temporal_negative_qualifiers import _source_body_text

EXECUTION_PHASE_REASON = (
    "The cited bodies do not establish the stated execution phase, method, "
    "identities, deadlines, effects, and proof domain in one coherent record. "
    "Limit the answer to dated source records, not current permission or a new live check."
)
EXECUTION_PHASE_BODY_REASON = "Complete cited source text is unavailable for execution-phase validation."
EXECUTION_PHASE_SUFFIX = " This is a dated source record, not current permission or a new live check."
PREFIX = "The cited execution-phase records: "
SAFE_PERMISSION = "The cited execution-phase records do not establish current execution permission."
SAFE_IDENTITY = "The cited execution-phase records do not establish a fresh executable identity or lease."
_QUOTED = r'''`[^`\n]+`|"[^"\n]+"|'[^'\n]+' '''.strip()
_PLAIN = r"[A-Za-z0-9_/.#:+-]+"
_VALUE = rf"(?:{_QUOTED}|{_PLAIN})"
_STAMP = r"20\d{2}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z"
_IDENTITIES = ("phase", "method", "action", "command", "executable", "version", "source identity", "resource",
               "task", "app", "caller", "candidate")
_FACETS = ("TERM", "kill", "caller deadline", "environment", "selection", "observed effect", "proof domain")
_ROLES = _IDENTITIES + _FACETS + ("start", "end", "operation date")
_ROLE_PATTERN = "|".join(re.escape(role) for role in sorted(_ROLES, key=len, reverse=True))
_SOURCE_ROLE_PATTERN = _ROLE_PATTERN + "|sourceIdentity|path|target"
_QUERY_ROLE_PATTERN = _SOURCE_ROLE_PATTERN + "|root|object|operation|scope|who"
_HEADER = re.compile(rf"On (?P<start>{_STAMP}) to (?P<end>{_STAMP}), execution phase (?P<phase>{_VALUE}) records ")
# Optional actor roles remain explicit constraints when a question names them.
_DECLARATIONS = ", ".join(rf"{role} (?P<{role.replace(' ', '_')}>{_VALUE})" for role in _IDENTITIES[1:8])
_ACTORS = "".join(rf"(?:, {role} (?P<{role}>{_VALUE}))?" for role in _IDENTITIES[8:])
_DETAILS = "; ".join(rf"{role} (?P<{role.replace(' ', '_')}>{_VALUE})" for role in _FACETS)
_GRAMMAR = re.compile(_HEADER.pattern + _DECLARATIONS + _ACTORS + ": " + _DETAILS)
_CONCEPT = re.compile(r"\b(?:execution[- ]phases?|dry[- ]runs?|actual[- ]cleanup|filesystem[- ]probes?|write/fsync|proof[- ]domains?)\b", re.I)
_PREVIEW_FLAGS = re.compile(r"(?<![\w/.-])-n[ \t]+-cache(?![\w/.-])", re.I)
_CONCEPT_TOKEN = r"(?:[A-Za-z]+(?:-[A-Za-z]+)*|write/fsync|v?\d+(?:\.\d+){1,3}|-n|-cache)"
_CONCEPT_TEXT = re.compile(rf"{_CONCEPT_TOKEN}(?: +{_CONCEPT_TOKEN})*", re.I)
_VERSION_FACET_NOUN = r"(?:limits?|deadlines?)"
_METADATA_FORM = re.compile(
    r"^(?:(?:currently|now),?\s*)?(?:what|which)\s+(?:(?:is|are)\s+(?:the\s+)?)?"
    rf"(?:filename|file name|definition|meaning|title|(?:version|revision)(?!\s+{_VERSION_FACET_NOUN}\b))\b", re.I,
)
_PREDICATE = r"(?:provides?|requires?|supports?|establish(?:es)?|prov(?:e|es)|confirms?|permits?|allows?)"
_INTENT = re.compile(rf"\b(?:what|which|can|does|describe|show|explain|select|selection|permission|effects?|identity|deadlines?|{_PREDICATE})\b", re.I)
_DATA = re.compile(r"\b(?:filename|file name|path|title|source|note|record|revision|version|tag|digest|named|called)\s*(?:(?:is|of|:|=)\s*)?$", re.I)
_ROLE_BEFORE = re.compile(rf"\b(?P<role>{_QUERY_ROLE_PATTERN})\b\s*(?:(?:is|:|=)\s*)?$", re.I)
_PROPERTY = re.compile(rf"(?<!\w)(?P<quote>[`\"']?)(?P<role>{_SOURCE_ROLE_PATTERN})(?P=quote)\s*(?:(?:is|was|remains)\s*[:=]?|[:=])\s*(?P<value>{_QUOTED}|[^\n;]+)", re.I)
_RAW_FIELD = re.compile(rf"^(?P<indent>[ \t]*)(?:(?:>\s*|[-+*]\s+))?(?P<key>(?P<quote>[`\"']?)(?:{_SOURCE_ROLE_PATTERN})(?P=quote))[ \t]*(?:(?:is|was|remains)[ \t]*[:=]?|[:=])[ \t]*(?P<value>[^\n]*)$", re.I)
_FAILURE = re.compile(r"\b(?:failed|failure|denied|unknown|unverified|unavailable|blocked|not checked|not observed|not selected|not tested)\b", re.I)
_PERMISSION = re.compile(r"\b(?:current|caller|now)\b.{0,70}\b(?:permission|execution|selection)\b.{0,35}\b(?:approved|granted|authorized|allowed)\b", re.I)
_PROMOTION = re.compile(r"\b(?:proves?|confirms?|establish(?:es)?|guarantees?)\b.{0,65}\b(?:executable identity|hash|integrity|lease|environment|no side effects|future effects)\b", re.I)


def _unquote(value: str) -> str:
    return value[1:-1] if len(value) > 1 and value[0] in "`\"'" and value[-1] == value[0] else value


def _canonical_role(role: str) -> str:
    aliases = {"path": "executable", "sourceidentity": "source identity", "target": "resource"}
    return aliases.get(role.lower(), next((value for value in _ROLES if value.lower() == role.lower()), role.lower()))


def _operative_concept(text: str) -> bool:
    return bool(_CONCEPT.search(text) or _PREVIEW_FLAGS.search(text))


def _bound_literal_role(question: str, match: re.Match) -> re.Match | None:
    # Keep offsets stable so only this role and literal occurrence is masked.
    before = re.sub(r"[*_]", " ", question[:match.start()])
    return _ROLE_BEFORE.search(before)


def _facet_pair(role: str, value: str, relation: str | None) -> bool:
    """Bare facet nouns are not explicit identity assignments."""
    return not relation and (
        (role.lower() == "executable" and value.lower() == "identity")
        or (role.lower() in {"version", "revision"} and bool(re.fullmatch(_VERSION_FACET_NOUN, value, re.I)))
    )


def _literal_role(question: str, match: re.Match) -> str:
    before = re.sub(r"[*_]", "", question[:match.start()])
    if role := _bound_literal_role(question, match):
        return _canonical_role(role["role"])
    if re.search(r"\b(?:via|through|using)\s+(?:method\s+)?$", before, re.I):
        return "method"
    if _DATA.search(before):
        return "data"
    value = _unquote(match[0])
    if _operative_concept(value) and _CONCEPT_TEXT.fullmatch(value):
        return "concept"
    return "unbound"


def execution_phase_required(question: str) -> bool:
    """Select operative phase clauses. Identity data cannot select a guard."""
    def literal(match):
        return _unquote(match[0]) if _literal_role(question, match) == "concept" else " "

    text = re.sub(r"[*_]", "", re.sub(_QUOTED, literal, question))
    text = re.sub(r"(?:;|\band\b)\s*(?=(?:what|which|how|can|does|describe|show|explain)\b)", "\n\n", text, flags=re.I)
    # Plain identities stay opaque too. Predicate words are not identities.
    def identity(match):
        facet = _facet_pair(match["role"], match["value"], match["relation"])
        predicate = not match["relation"] and re.fullmatch(_PREDICATE + r"|is|are|of|for", match["value"], re.I)
        return match[0] if facet or predicate else match["label"] + " "

    text = re.sub(
        rf"(?P<label>\b(?P<role>{_QUERY_ROLE_PATTERN}|filename|title|source)\b\s*)"
        rf"(?P<relation>(?:is\b|:|=)\s*)?(?P<value>{_PLAIN})", identity, text, flags=re.I,
    )
    for clause in _units(text):
        if _METADATA_FORM.search(clause) and not re.search(rf"\b{_PREDICATE}\b", clause, re.I):
            continue
        metadata = re.search(r"\b(?:filename|file name|definition|meaning|version|revision|title|means?)\b", clause, re.I)
        operative = re.search(rf"\b(?:{_PREDICATE}|executable\s+identity|limits|permission|selection|effects?|deadlines?)\b", clause, re.I)
        if metadata and not operative:
            continue
        if _operative_concept(clause) and _INTENT.search(clause):
            return True
    return False


def _question_roles(question: str) -> dict[str, set[str]]:
    roles: dict[str, set[str]] = {}
    masked_chars = list(question)
    concepts = []
    for match in re.finditer(_QUOTED, question):
        role = _literal_role(question, match)
        if role not in {"concept", "data"}:
            roles.setdefault(role, set()).add(_unquote(match[0]))
        elif role == "concept":
            concepts.append(_unquote(match[0]))
        binding = _bound_literal_role(question, match)
        start = binding.start() if binding else match.start()
        masked_chars[start:match.end()] = " " * (match.end() - start)
    masked = "".join(masked_chars)
    for match in re.finditer(
        rf"\b(?P<role>{_QUERY_ROLE_PATTERN})\b\s*(?P<relation>(?:is\b|:|=)\s*)?(?P<value>{_PLAIN})", masked, re.I,
    ):
        grammar = not match["relation"] and re.fullmatch(
            _PREDICATE + r"|is|are|was|were|does|do|did|can|could|will|would|should|may|must|of|for|and|or|record|records|recorded|that|which",
            match["value"], re.I,
        )
        if _facet_pair(match["role"], match["value"], match["relation"]) or grammar:
            continue
        role = _canonical_role(match["role"])
        roles.setdefault(role, set()).add(match["value"])
    for match in re.finditer(rf"\b(?:via|through|using)\s+(?:method\s+)?({_PLAIN})", masked, re.I):
        roles.setdefault("method", set()).add(match[1])
    for match in re.finditer(rf"\b(?:on|at|during)\s+(?P<when>{_STAMP}|20\d{{2}}-\d{{2}}-\d{{2}})(?![\w:+.-])", masked, re.I):
        value = match["when"]
        roles.setdefault("start" if "T" in value else "operation date", set()).add(value)
    natural = masked + "\n" + "\n".join(concepts)
    for match in re.finditer(r"\bGo[ \t]+(?P<version>\d+(?:\.\d+){1,3})[ \t]+(?=(?:(?:(?:direct|brokered)[ \t]+)?(?:cache-only[ \t]+)?dry[- ]run|clean[ \t]+-n[ \t]+-cache)\b)", natural, re.I):
        roles.setdefault("version", set()).add(match["version"])
    for match in re.finditer(r"\b(?P<method>direct|brokered)[ \t]+(?=(?:Go[ \t]+\d+(?:\.\d+){1,3}[ \t]+)?(?:(?:cache-only[ \t]+)?dry[- ]run|clean[ \t]+-n[ \t]+-cache)\b)", natural, re.I):
        roles.setdefault("method", set()).add(match["method"].lower())
    for match in re.finditer(r"\bfor[ \t]+(?P<task>[A-Za-z][A-Za-z0-9_.-]*#[1-9]\d*)\b", natural):
        roles.setdefault("task", set()).add(match["task"])
    return roles


def _units(text: str) -> list[str]:
    """Preserve literal bytes and operation times across soft line breaks."""
    saved = []
    marker = "\x00execution-literal"
    while marker in text:
        marker += "_"

    def protect(match):
        saved.append(match[0])
        return marker + str(len(saved) - 1) + "\x00"

    value = re.sub(_QUOTED + "|" + _STAMP, protect, text)
    value = re.sub(r"(?<![.!?\n])\n(?!\n)", " ", value)
    value = re.sub(r"[ \t]+", " ", value)
    token = re.compile(re.escape(marker) + r"(\d+)\x00")
    return [token.sub(lambda m: saved[int(m[1])], part.strip().rstrip(".!?"))
            for part in re.split(r"\n+|(?<=[.!?])\s+", value) if part.strip()]


@dataclass(frozen=True)
class _Record:
    fields: dict[str, str]


def _record(unit: str) -> _Record | None:
    match = _GRAMMAR.fullmatch(unit)
    if not match:
        return None
    fields = {key.replace("_", " "): _unquote(value) for key, value in match.groupdict().items() if value is not None}
    try:
        if datetime.fromisoformat(fields["start"]) > datetime.fromisoformat(fields["end"]):
            return None
    except ValueError:
        return None
    domains = {
        "dry-run": ({"address selection only", "unknown"}, {"no deletion or headroom effect", "unknown"}),
        "actual-cleanup": ({"cache cleanup only", "unknown"}, {"dated free-block relief", "cleanup failed", "unknown"}),
        "filesystem-probe": ({"UID/device/write success only", "unknown"}, {"write/fsync success", "write failed", "unknown"}),
        "path-record": ({"dated path metadata only", "unknown"}, {"recorded path/version", "unknown"}),
        "broker-staging": ({"staging effects unknown", "unknown"}, {"unknown"}),
        "proposal": ({"no execution proof", "unknown"}, {"not executed", "unknown"}),
    }
    phase = fields["phase"]
    environments = {"recorded conditions only", "unknown"}
    if phase in {"dry-run", "actual-cleanup"}:
        environments.add("clean environment")
    if fields["environment"] not in environments:
        return None
    if phase not in domains or fields["proof domain"] not in domains[phase][0] or fields["observed effect"] not in domains[phase][1]:
        return None
    if fields["method"] not in {"direct", "brokered"} or fields["selection"] not in {"spent", "one-shot", "unselected", "unknown"}:
        return None
    if phase in {"dry-run", "actual-cleanup"}:
        flags = " -n -cache" if phase == "dry-run" else " -cache"
        if not re.fullmatch(r"[^\s]+ clean" + re.escape(flags), fields["command"]) or fields["action"] != "cache-clean":
            return None
    phase_actions = {
        "filesystem-probe": ("write-check", "write/fsync"),
        "path-record": ("path-metadata", "path metadata"),
        "broker-staging": ("stage", "broker Ensure"),
    }
    if phase in phase_actions and (fields["action"], fields["command"]) != phase_actions[phase]:
        return None
    if phase == "broker-staging" and fields["method"] != "brokered":
        return None
    if any(not re.fullmatch(r"(?:\d+(?:\.\d+)?s|unknown|not applicable)", fields[role]) for role in ("TERM", "kill", "caller deadline")):
        return None
    fields["operation date"] = fields["start"][:10]
    return _Record(fields)


def _records(text: str) -> list[_Record]:
    return [record for unit in _units(text) if (record := _record(unit))]


def _values(texts: Mapping[str, str] | Sequence[str]) -> list[str]:
    return [str(value or "") for value in (texts.values() if isinstance(texts, Mapping) else texts)]


def _matches(record: _Record, roles: dict[str, set[str]]) -> bool:
    return all(values == {record.fields.get(role)} for role, values in roles.items())


def _raw_units(text: str) -> list[str]:
    """Keep raw properties separate from narrative soft line breaks."""
    text = re.sub(r"(?m)^[ \t]*(?:`{3,}|~{3,})([^\n]*)$", lambda m: "\n\n" + m[1] + "\n\n", text)
    text = re.sub(r"(?m)^\s*#{1,6}\s+", "", text)
    lines, prose, units = text.splitlines(), [], []
    index = 0
    while index < len(lines):
        field = _RAW_FIELD.fullmatch(lines[index])
        if not field:
            prose.append(lines[index])
            index += 1
            continue
        units.extend(_units("\n".join(prose)))
        prose = []
        value = field["value"]
        index += 1
        if value.strip() in {"", ">", "|", ">-", "|-", ">+", "|+"}:
            continuation = []
            indent = len(field["indent"].expandtabs(4))
            while index < len(lines):
                line = lines[index]
                if _RAW_FIELD.fullmatch(line):
                    break
                if not line.strip():
                    index += 1
                    continue
                depth = len(line) - len(line.lstrip(" \t"))
                if len(line[:depth].expandtabs(4)) <= indent:
                    break
                continuation.append(line.strip())
                index += 1
            value = continuation[0] if len(continuation) == 1 else "\x00unknown-raw-field"
        units.append(field["key"] + ": " + value)
    units.extend(_units("\n".join(prose)))
    return units


def _scope_value(role: str, value: str) -> bool:
    """Unknown scope syntax cannot erase an earlier source qualifier."""
    labels = {role} | {alias for alias in ("path", "target", "sourceIdentity") if _canonical_role(alias) == role}
    annotation = re.fullmatch(
        rf"(?:{_FAILURE.pattern})\s+(?:{'|'.join(re.escape(label) for label in labels)})", value, re.I,
    )
    if annotation or _FAILURE.fullmatch(value) or value.casefold() in {"?", "none", "false", "no"}:
        return False
    if role in {"start", "end", "operation date"}:
        pattern = r"20\d{2}-\d{2}-\d{2}" if role == "operation date" else _STAMP
        if not re.fullmatch(pattern, value):
            return False
        try:
            datetime.fromisoformat(value)
        except ValueError:
            return False
    return True


def _contrary(raw: str, record: _Record, *, facet: str | None = None) -> bool:
    """Use ordered source contexts. A draft never supplies a raw context."""
    units = _raw_units(raw)
    anchors = [match for unit in units if (match := _HEADER.search(unit))]
    context = {key: _unquote(value) for key, value in anchors[0].groupdict().items()
               if _scope_value(key, _unquote(value))} if anchors else {}
    if anchors:
        # The first complete source record also scopes a preceding raw limit.
        first = next((value for unit in units if (value := _record(unit))), None)
        if first:
            context = dict(first.fields)
    for unit in units:
        if declared := _record(unit):
            scope = _IDENTITIES + ("start", "end")
            if (not facet and all(declared.fields.get(role) == record.fields.get(role) for role in scope)
                    and any(declared.fields[role] != record.fields[role] for role in _FACETS)):
                return True
            context = dict(declared.fields)
            continue
        if anchor := _HEADER.search(unit):
            context = {key: _unquote(value) for key, value in anchor.groupdict().items()
                       if _scope_value(key, _unquote(value))}
            if "start" in context:
                context["operation date"] = context["start"][:10]
        remainder = unit
        properties = list(_PROPERTY.finditer(unit))
        for prop in properties:
            role, raw_value = prop["role"], prop["value"].strip().rstrip(".!?")
            role = _canonical_role(role)
            value = _unquote(raw_value)
            if role in _IDENTITIES or role in {"start", "end", "operation date"}:
                known = (re.fullmatch(_QUOTED, raw_value) or re.fullmatch(_PLAIN, raw_value)) and _scope_value(role, value)
                if known:
                    if role == "method" and value != context.get("method"):
                        context = {}
                    context[role] = value
                # An unknown field cannot erase an earlier known qualifier.
            remainder = remainder.replace(prop[0], "")
        remainder = re.sub(r"^[ \t]*[A-Za-z][\w-]*:[ \t]*(?:[>|][-+]?[ \t]*)?", "", remainder)
        subject = re.match(rf"(?:method\s+)?(?P<method>{_VALUE})\s+(?:has|uses|requires|cannot|failed|lacks|is|was|remains)\b", remainder, re.I)
        if subject:
            method = _unquote(subject["method"])
            if method in {"direct", "brokered"} or remainder.lower().startswith("method "):
                if method != context.get("method"):
                    context = {}
                context["method"] = method
        # Explicit local tuple roles restrict a natural negative too.
        if subject or _HEADER.search(unit):
            for scope in re.finditer(rf"\b(?P<role>{_SOURCE_ROLE_PATTERN})\s+(?P<value>{_VALUE})", remainder, re.I):
                role = _canonical_role(scope["role"])
                if role in _IDENTITIES or role in {"start", "end", "operation date"}:
                    if _scope_value(role, _unquote(scope["value"])):
                        context[role] = _unquote(scope["value"])
            for when in re.finditer(rf"\b(?:on|at|during)\s+(?P<when>{_STAMP}|20\d{{2}}-\d{{2}}-\d{{2}})(?![\w:+.-])", remainder, re.I):
                value = when["when"]
                role = "start" if "T" in value else "operation date"
                if _scope_value(role, value):
                    context[role] = value
        if facet:
            relevant = all(context.get(role) == value for role, value in record.fields.items())
        else:
            relevant = "method" in context and all(record.fields.get(role) == value for role, value in context.items()
                                                       if role in _IDENTITIES or role in {"start", "end", "operation date"})
        if not relevant:
            continue
        if facet:
            if (_PERMISSION.search(remainder) if facet == "permission" else _PROMOTION.search(remainder)):
                return True
            if facet == "identity" and any(prop["role"].lower() == "proof domain" and re.search(
                    r"\b(?:fresh|executable identity|hash|integrity|lease)\b", prop["value"], re.I) for prop in properties):
                return True
            continue
        for prop in properties:
            role = _canonical_role(prop["role"])
            if role in _FACETS and _unquote(prop["value"].strip().rstrip(".!?")) != record.fields.get(role):
                return True
        if any(_FAILURE.search(prop["value"]) and not re.fullmatch(_VALUE, prop["value"].strip())
               for prop in properties):
            return True
        if _PERMISSION.search(remainder) or _PROMOTION.search(remainder):
            return True
        if _FAILURE.search(remainder) and re.search(r"\b(?:cleanup|headroom|effect|proof|identity|lease|environment|deadline|TERM|kill|selection|write|fsync)\b", remainder, re.I):
            return True
    return False


def execution_phase_problem(question: str, answer: str, cited_texts: Mapping[str, str] | Sequence[str], *,
                            retrieved_texts: Mapping[str, str] | Sequence[str] = ()) -> str | None:
    """Accept only whole body records or a bounded unknown facet."""
    if not execution_phase_required(question):
        return None
    cited = _values(cited_texts)
    if not cited or any(not value.strip() for value in cited):
        return EXECUTION_PHASE_BODY_REASON
    supported = [record for body in cited for record in _records(_source_body_text(body))]
    raw = cited + _values(retrieved_texts)
    roles = _question_roles(question)
    if answer.strip() in {SAFE_PERMISSION, SAFE_IDENTITY}:
        # These facets do not deny the existence of actual historical bodies.
        if any(role not in _ROLES or len(values) != 1 for role, values in roles.items()):
            return EXECUTION_PHASE_REASON
        target = _Record({role: next(iter(values)) for role, values in roles.items()})
        if any(_contrary(body, target, facet="permission" if answer.strip() == SAFE_PERMISSION else "identity")
               for body in raw):
            return EXECUTION_PHASE_REASON
        return None
    if not answer.startswith(PREFIX):
        return EXECUTION_PHASE_REASON
    records = [_record(unit) for unit in _units(answer[len(PREFIX):])]
    if not records or any(record is None for record in records):
        return EXECUTION_PHASE_REASON
    for record in records:
        if record not in supported or not _matches(record, roles) or any(_contrary(body, record) for body in raw):
            return EXECUTION_PHASE_REASON
    return None
