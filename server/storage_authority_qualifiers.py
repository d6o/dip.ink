"""Bounded source records for storage scope, not an authorization engine.

Only complete narrative records can support a selected answer. Literal roles,
conditions, phases, and protection limits stay in one record. Raw text can
veto a record, but cannot establish it. Unknown prose fails closed. A pass
never supplies current caller permission or a new operational observation.
"""
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from temporal_negative_qualifiers import _source_body_text

STORAGE_AUTHORITY_REASON = (
    "The cited bodies do not establish the stated storage owner, operation, "
    "phase, conditions, and protection limits together. Limit the answer to "
    "source records, not current permission."
)
STORAGE_AUTHORITY_BODY_REASON = (
    "Complete cited source text is unavailable for storage-authority validation."
)
STORAGE_AUTHORITY_SUFFIX = " This is a source record, not current permission or a new live check."
_LITERAL = r"`[^`\n]+`"
_RAW_LITERAL = _LITERAL + r'''|"[^"\n]*"|'[^'\n]*' '''.strip()
_ACTION_WORDS = r"expansion|cleanup|resize|deletion|write|patch"
_RAW_OBJECT = (r"(?:" + _RAW_LITERAL + r"|[A-Za-z0-9_/.#:-]+(?:[ \t]+[A-Za-z0-9_/.#:-]+){0,7}?)")
_OBJECT_TYPE = r"(?:(?:the\s+)?(?:storage object|storage volume|filesystem|PVC|PV|volume|disk)\s+)?"
_ACTION_PREDICATE = r"(?:is|remains|stays|requires|cannot|blocked)\b"
_DIRECTED_RESTRICTION = re.compile(
    rf"\b(?P<action>{_ACTION_WORDS})\s+"
    rf"(?:(?:of|for)\s+(?P<determiner>(?:the|a|an)\s+)?"
    rf"(?P<kind>{_OBJECT_TYPE})(?P<object>{_RAW_OBJECT})\s+)?{_ACTION_PREDICATE}", re.I,
)
_REVERSE_RESTRICTION = re.compile(
    rf"(?P<determiner>(?:the|a|an)\s+)?"
    rf"(?P<kind>(?:the\s+)?(?:storage object|storage volume|filesystem|PVC|PV|volume|disk)\s+)?"
    rf"(?P<object>{_RAW_OBJECT})\s+(?P<action>{_ACTION_WORDS})\s+{_ACTION_PREDICATE}", re.I,
)
_DATE = r"20\d{2}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)?"
_STORAGE = re.compile(
    r"\b(?:storage|filesystems?|file[- ]systems?|shared[- ]home|home[- ]PVC|"
    r"capacity|PVCs?|PVs?|Longhorn|disks?|volumes?|expansion|resize|unpin)\b", re.I,
)
_INTENT = re.compile(
    r"\b(?:own(?:s|ers?|ership)?|authority|authorities|permissions?|constraints?|protect\w*|"
    r"holds?|gates?|conditions?|risks?|clean[- ]?up|expan\w*|resize|retention|unpin|"
    r"delet\w*|remov\w*|patch\w*|writ\w*|mutat\w*)\b", re.I,
)
_CONCEPT_WORD = (
    r"(?:storage|filesystems?|file[ -]systems?|shared-home|home[ -]PVCs?|capacity|"
    r"PVCs?|PVs?|Longhorn|disks?|volumes?|expand(?:s)?|expansion|resize|unpin|"
    r"owners?|ownership|authority|authorities|permissions?|constraints?|protect(?:ion|ed|s)?|"
    r"holds?|gates?|conditions?|risks?|clean[ -]?up|retention|delet(?:e|ion)|remov(?:e|al)|"
    r"patch(?:es)?|writ(?:e|es)|mutat(?:e|ions?)|operations?)"
)
_CONCEPT = re.compile(_CONCEPT_WORD + r"(?:[ -]+" + _CONCEPT_WORD + r")*", re.I)
# These are complete source-record forms, not operational instructions.
# Conditions and limits are mandatory and remain part of the record identity.
GUARDS = ("fresh StorageClass allowVolumeExpansion, exact UID/RV/old size, ext4, "
          "healthy attached two-RW replicas, and physical scheduled headroom")
LIMITS = ("no cleanup, deletion, private-content access, partial or worktree removal, "
          "snapshot or checkpoint removal, repair, fence release, custody release, "
          "capture, or activation release")
_OWNER = re.compile(
    rf"Project (?P<owner>{_LITERAL}) owns ticket (?P<ticket>{_LITERAL}) for "
    rf"(?P<action>expansion) of (?P<object>{_LITERAL}) as the sole writer, "
    rf"conditional on (?P<conditions>{re.escape(GUARDS)}); (?P<limits>{re.escape(LIMITS)}); "
    rf"existing partials, worktrees, custody, and hold (?P<hold>{_LITERAL}) stay unchanged"
)
_COMPLETE = re.compile(
    rf"On (?P<event>{_DATE}), project (?P<owner>{_LITERAL}) completed "
    rf"(?P<action>expansion) of (?P<object>{_LITERAL}) via (?P<method>{_LITERAL}) "
    rf"under ticket (?P<ticket>{_LITERAL}) from (?P<old>{_LITERAL}) to (?P<new>{_LITERAL}), "
    rf"with (?P<conditions>{re.escape(GUARDS)}); (?P<limits>{re.escape(LIMITS)}); "
    rf"existing partials, worktrees, custody, and hold (?P<hold>{_LITERAL}) stay unchanged; "
    r"this is temporary logical relief, not independence, durability, or a cause diagnosis; "
    r"this completed operation grants no new operation or protection release"
)
_METADATA = re.compile(
    rf"(?P<object>{_LITERAL}) has recorded capacity (?P<size>{_LITERAL}); "
    r"this is metadata only, not an actor or permission record"
)
_HISTORY = re.compile(
    rf"On (?P<event>{_DATE}), project (?P<owner>{_LITERAL}) completed (?P<action>cleanup) "
    rf"of (?P<object>{_LITERAL}) under ticket (?P<ticket>{_LITERAL}); "
    r"this historical record grants no new cleanup or deletion permission"
)
_RETENTION = re.compile(
    rf"(?P<object>{_LITERAL}) retention requires 72 hours after final verified activation "
    rf"and an explicit guarded retirement decision by project (?P<retirement_owner>{_LITERAL}); blocked activation leaves "
    r"the timestamp unset; elapsed time alone grants no unpin permission; "
    r"current candidate and rollback roots stay protected; activation requires "
    rf"the explicit release of hold (?P<hold>{_LITERAL}) and cleanup requires fresh UID/RV guards"
)
_READ = re.compile(
    rf"(?P<method>{_LITERAL}) supports read-only metadata for (?P<object>{_LITERAL}); "
    r"method acceptance grants no write permission or protection release"
)
_GRAMMARS = {"owner": _OWNER, "completed": _COMPLETE, "metadata": _METADATA,
             "history": _HISTORY, "retention": _RETENTION, "read": _READ}
_UNKNOWN = re.compile(
    r"The (?:cited|retrieved|supplied) (?:source text|bodies|evidence) "
    r"(?:does not establish|does not identify) current storage permission"
    r"(?:\. Current permission remains unverified)?\.?"
)
_HOLD = re.compile(
    r"\b(?:no|not|never|cannot|blocked|protected|unchanged|hold|holds|requires?|"
    r"conditional|only|sole|writer|owner|owns|gates?|guards?|retains?|unverified)\b", re.I,
)
_PROPERTY = re.compile(
    r"(?<![\w])(?P<quote>[`\"']?)(?P<role>owner|project|ticket|method|hold|action|operation|conditions|scope|phase|protection limits|sole writer)"
    r"(?P=quote)\s*(?:(?:is|was|remains)\s*[:=]?|[:=])\s*"
    rf"(?P<value>{_LITERAL}|\"[^\"\n]*\"|'[^'\n]*'|[^\n]*?)(?=$|\n)", re.I,
)
_POSSIBLE_CURRENT_PERMISSION = re.compile(
    r"\b(?:current|caller|now)\b.{0,100}\b(?:permission|authorized|approved|granted|can|may)\b", re.I,
)
_PROTECTION = re.compile(
    r"\b(?:(?:The|This|All|Existing|Current|Its|Their)\s+)*(?:activation|"
    r"partials?|worktrees?|custody|fence|capture|retention|retirement|protection|hold|"
    r"rollback|candidate|timestamp|it\b|they\b)", re.I,
)


def _ordinary_phrase(value: str) -> bool:
    """Exclude path and identifier punctuation from modifier phrases."""
    return bool(re.fullmatch(r"[A-Za-z0-9 ,()'-]+", value) and re.search(r"[A-Za-z]", value)
                and (not re.search(r"\d", value) or re.search(r"\s", value)))


def _literal_role(question: str, match: re.Match) -> str:
    """Classify one literal without changing its identity bytes."""
    before = re.sub(r"[*_]", "", question[:match.start()])
    after = re.sub(r"[*_]", "", question[match.end():])
    nouns = {"owner": "(?:project|owner)", "ticket": "ticket", "hold": "hold",
             "method": "method", "action": "(?:action|operation)",
             "object": "(?:target|storage object|storage volume|filesystem|PVC|PV|volume|disk|Longhorn)"}
    for role, noun in nouns.items():
        if (re.search(rf"\b{noun}\s*(?:(?:is|:|=)\s*)?$", before, re.I)
                or re.match(rf"\s+{noun}\b", after, re.I)):
            property_question = (
                role == "object" and (_CONCEPT.fullmatch(match[0][1:-1])
                                      or (_ordinary_phrase(match[0][1:-1])
                                          and _INTENT.search(match[0][1:-1])))
                and re.search(r"\b(?:filesystem|PVC|PV|volume|disk|Longhorn)\s*$", before, re.I)
                and re.match(r"\s+(?:apply|applies|govern|governs|are|required)\b", after, re.I)
            )
            if not property_question:
                return role
    if re.search(r"\b(?:via|through|using)\s+(?:the\s+)?$", before, re.I):
        return "method"
    # A method subject must stay opaque in a read-capability question.
    if (re.search(r"\b(?:can|does|do|could)\s*$", before, re.I)
            and re.match(r"\s+(?:read|list|get|fetch|query|inspect|access|support)\w*\b", after, re.I)):
        return "method"
    if (re.search(r"\b(?:source|note|record|filename|file name|path|title|version|revision|tag|digest|image|build)"
                  r"\s*(?:(?:is|of|:|=)\s*)?$", before, re.I)
            or re.search(r"\b(?:named|called|for reference)\s*$", before, re.I)
            or re.match(r"\s+(?:filename|path|title|version|revision|tag|digest)\b", after, re.I)):
        return "data"
    if re.search(r"\b(?:expansion of|cleanup of|resize of)\s+(?:the\s+)?$", before, re.I):
        return "object"
    value = match[0][1:-1]
    if _CONCEPT.fullmatch(value):
        if re.search(r"\b(?:toward|towards|concerning|regarding|about|for|to)\s*$", before, re.I):
            return "ambiguous_concept"
        return "concept"
    # Unknown ordinary modifiers cannot remove recognized safety intent.
    # Identifier punctuation stays opaque. Ambiguous phrases cannot bind facts.
    outside = re.sub(_LITERAL, " ", question)
    known_literals = ' '.join(item[0][1:-1] for item in re.finditer(_LITERAL, question)
                              if _CONCEPT.fullmatch(item[0][1:-1]))
    combined = value + ' ' + outside + ' ' + known_literals
    if (_ordinary_phrase(value)
            and (_STORAGE.search(value) or _INTENT.search(value))
            and _STORAGE.search(combined) and _INTENT.search(combined)):
        return "ambiguous_concept"
    if re.search(r"\bowns?\s+(?:the\s+)?$", before, re.I):
        return "object"
    # An unbound identity cannot silently become a generic scope question.
    return "unbound"


def _operative_question_text(question: str) -> str:
    """Keep concept presentation separate from explicit literal identities."""
    outside = re.sub(_LITERAL, " ", question)
    name_question = (not _STORAGE.search(outside) and re.search(
        r"\b(?:filename|file name|path|title|version|revision|tag|digest|meaning|definition|spell\w*|means?|named|called)\b", outside, re.I,
    ))

    def concept(match):
        if not name_question and _literal_role(question, match) in {"concept", "ambiguous_concept"}:
            return match[0][1:-1]
        return " "

    return re.sub(r"[*_]", "", re.sub(_LITERAL, concept, question))


def storage_authority_required(question: str) -> bool:
    """Select storage scope questions, including formatted concept words."""
    # A filename alone cannot supply intent. Quoted concept words can.
    text = _operative_question_text(question)
    cleanup_scope = (re.search(r"\b(?:clean[- ]?up|retention)\b", text, re.I)
                     and re.search(r"\b(?:own(?:s|er|ership)?|authority|permissions?|protect\w*|constraints?)\b",
                                   text, re.I))
    return bool((_STORAGE.search(text) and _INTENT.search(text)) or cleanup_scope)


def _units(text: str) -> list[str]:
    # Keep periods in literal paths, tickets, and event times intact.
    literals = []
    marker = "\x00storage-literal"
    while marker in text:
        marker += "_"

    def mask(match):
        literals.append(match[0])
        return marker + str(len(literals) - 1) + "\x00"

    masked = re.sub(_LITERAL + "|" + _DATE, mask, text)
    masked = re.sub(r"(?<![.!?\n])\n(?!\n)", " ", masked)
    token = re.compile(re.escape(marker) + r"(\d+)\x00")
    return [token.sub(lambda m: literals[int(m[1])], part.strip().rstrip(".!?"))
            for part in re.split(r"\n+|(?<=[.!?])\s+", masked) if part.strip()]


def _raw_clause_units(text: str) -> list[str]:
    """Separate raw restriction clauses without changing quoted identities."""
    parts = re.split('(' + _RAW_LITERAL + ')', text)
    for index in range(0, len(parts), 2):
        parts[index] = re.sub(r";|\s+\b(?:but|while|whereas|however)\b\s+", '\n\n', parts[index], flags=re.I)
    return _units(''.join(parts))


@dataclass(frozen=True)
class _Record:
    phase: str
    text: str
    fields: dict[str, str]


def _record(unit: str) -> _Record | None:
    for phase, grammar in _GRAMMARS.items():
        if match := grammar.fullmatch(unit):
            fields = match.groupdict()
            if fields.get("event"):
                try:
                    datetime.fromisoformat(fields["event"].replace("Z", "+00:00"))
                except ValueError:
                    return None
            return _Record(phase, unit, fields)
    return None


def _records(text: str) -> list[_Record]:
    return [record for unit in _units(text) if (record := _record(unit))]


def _values(texts: Mapping[str, str] | Sequence[str]) -> list[str]:
    # A source key, title, or timestamp never supplies a role or permission.
    return [str(value or "") for value in (texts.values() if isinstance(texts, Mapping) else texts)]


def _question_roles(question: str) -> dict[str, set[str]]:
    # Explicit roles bind literally. Multiple identities refuse a selection.
    # A generic interrogative role does not name an identity.
    plain = r"[A-Za-z0-9_/.#:-]+"
    named = r"(?:[A-Z][A-Za-z0-9_/.#:-]*|[A-Za-z0-9_/.#:-]*[0-9_/.#:-][A-Za-z0-9_/.#:-]*)"
    roles: dict[str, set[str]] = {}
    for match in re.finditer(_LITERAL, question):
        role = _literal_role(question, match)
        role = "unbound" if role == "ambiguous_concept" else role
        if role not in {"concept", "data"}:
            identity = match[0][1:-1] if role == "action" else match[0]
            roles.setdefault(role, set()).add(identity)

    # Literal roles come only from the classifier above. A plain-role scan
    # must not reinterpret a concept literal or cross its opaque marker.
    plain_question = re.sub(_LITERAL, "\x00", question)

    def collect(role, pattern, *, flags=re.I):
        for identity in re.findall(pattern, plain_question, flags):
            identity = identity if identity.startswith('`') else '`' + identity + '`'
            roles.setdefault(role, set()).add(identity)

    nouns = {"owner": "(?:project|owner)", "ticket": "ticket", "hold": "hold",
             "method": "method", "object": "(?:target|storage object|storage volume|filesystem|PVC|PV|volume|disk|Longhorn)"}
    for role, noun in nouns.items():
        collect(role, rf"\b{noun}\s+({_LITERAL})")
        # Plain compound identities and capitalized names are explicit too.
        collect(role, rf"\b(?i:{noun})\s+({named})(?![\w./#:-])", flags=0)
        # These frames make even an unfamiliar plain value explicit.
        frame = rf"\b(?:under|by|from|with|for|to|of|does|did|can|is|has)\s+(?:the\s+)?{noun}\s+"
        collect(role, frame + rf"({_LITERAL}|{plain})")
        for match in re.finditer(frame, question, re.I):
            if not re.match(rf"(?:{_LITERAL}|{plain})(?=\s|[?,)]|$)", question[match.end():]):
                roles.setdefault(role, set()).add("\x00unsupported")
    for role, noun in {"owner": "project", "ticket": "ticket"}.items():
        for match in re.finditer(rf"\b{noun}\s+({_LITERAL}|{plain})", question, re.I):
            generic = (re.search(r"\b(?:Which|What)\s*$", question[:match.start()], re.I)
                       and re.match(r"(?:owns?\b|and existing tickets own\b|(?:does|can|is|has)\b)",
                                    question[match.start(1):], re.I))
            if not generic:
                identity = match[1] if match[1].startswith('`') else '`' + match[1] + '`'
                roles.setdefault(role, set()).add(identity)
    for role, noun in nouns.items():
        collect(role, rf"\b{noun}\s*(?:is|:|=)\s*({_LITERAL}|{plain})")
    method_frame = r"\b(?:via|through|using)\s+(?:the\s+)?(?:method\s+)?"
    collect("method", method_frame + rf"({_LITERAL}|{plain})")
    for match in re.finditer(method_frame, question, re.I):
        if not re.match(rf"(?:{_LITERAL}|{plain})(?=\s|[?,)]|$)", question[match.end():]):
            roles.setdefault("method", set()).add("\x00unsupported")
    collect("object", rf"({_LITERAL})\s+(?:filesystem|PVC|storage object)\b")
    collect("object", rf"\b(?:expansion of|cleanup of|resize of)\s+(?:the\s+)?({_LITERAL})")
    # Do not invent an alias between a filesystem and a PVC.
    collect("object", r"\b(?:the|for|of|owns?)\s+(?:the\s+)?"
            r"([A-Z][A-Za-z0-9_.-]*(?:[ -](?:shared[- ]home|home))? (?:filesystem|PVC))\b", flags=0)
    for identity in re.findall(
        r"\b(?:the|for|of|owns?)\s+(?:the\s+)?([A-Z][A-Za-z0-9_.-]*(?:[ -](?:shared[- ]home|home))? (?:filesystem|PVC))\b",
        _operative_question_text(question),
    ):
        roles.setdefault("object", set()).add('`' + identity + '`')
    # A plain compound name before an object type is still an explicit role.
    collect("object", rf"\b(?:owns?|for|of)\s+(?:the\s+)?({named})\s+(?:filesystem|PVC)\b", flags=0)
    collect("object", rf"\bstorage object\s+({_LITERAL}|{plain})")
    # Action alternatives are choices, not an ambiguous identity union.
    outside = _operative_question_text(question)
    actions = {"expansion": r"\bexpan(?:sion|d|ds)\b", "cleanup": r"\bclean[- ]?up\b",
               "resize": r"\bresize\b", "deletion": r"\bdelet(?:e|ion)\b",
               "write": r"\bwrit(?:e|ing)\b", "patch": r"\bpatch\b"}
    requested = {action for action, pattern in actions.items() if re.search(pattern, outside, re.I)}
    for identity in re.findall(rf"\b(?:action|operation)\s+({_LITERAL}|{plain})", question, re.I):
        requested.add(identity[1:-1] if identity.startswith('`') else identity)
    if requested:
        roles["action"] = requested
    return roles


def _role_mismatch(record: _Record, roles: dict[str, set[str]]) -> bool:
    for role, identities in roles.items():
        if role == "action":
            if record.fields.get(role) and record.fields[role] not in identities:
                return True
        elif len(identities) != 1 or record.fields.get(role) not in identities:
            return True
    return False


def _contrary(raw: str, record: _Record, answer_units: set[str]) -> bool:
    """Raw text only vetoes. It cannot fill a missing positive field."""
    object_name = record.fields["object"]
    desired_action = record.fields.get("action")
    authority_scope = record.phase in {"owner", "retention", "read"}
    # Unknown prefixes can veto. Only actual source anchors change context.
    # Object context and action context stay separate and follow source order.
    object_context = None
    action_context = None
    seen_objects: set[str] = set()

    def clause_problem(text):
        nonlocal object_context, action_context
        identity = object_name[1:-1]
        mentions = re.search(r"(?<![\w./:-])" + re.escape(identity) + r"(?![\w./:-])", text)
        if mentions:
            if object_context is False:
                action_context = None
            object_context = True
        elif (re.search(rf"(?:storage object|storage volume|filesystem|PVC|PV|volume)\s+{_LITERAL}", text, re.I)
              or (re.search(rf"^\s*{_LITERAL}\s+(?:is|remains|has)\b", text) and _STORAGE.search(text))):
            object_context, action_context = False, None
        remainder = text
        for prop in _PROPERTY.finditer(text):
            role = {"project": "owner", "protection limits": "limits", "operation": "action"}.get(
                prop["role"].lower(), prop["role"].lower(),
            )
            value = prop["value"]
            if value[:1] in {'`', '"', "'"} and value[-1:] == value[:1]:
                value = value[1:-1]
            if role == "action":
                if value not in {"expansion", "cleanup", "resize", "deletion", "write", "patch", "read", "retention"}:
                    if authority_scope and object_context is not False:
                        return True
                else:
                    action_context = value
                remainder = remainder.replace(prop[0], "")
                continue
            if role in {"owner", "ticket", "method", "hold"}:
                value = '`' + value + '`'
            related_action = desired_action is None or action_context in {None, desired_action}
            if authority_scope and object_context is not False and related_action:
                if record.fields.get(role) != value:
                    return True
                remainder = remainder.replace(prop[0], "")
        # Directed source roles bind an action, an optional object, and a
        # restriction predicate. A matching clause can restore its own scope.
        restrictions = sorted((match.start(), -match.end(), kind, match)
                              for kind, pattern in (("directed", _DIRECTED_RESTRICTION), ("reverse", _REVERSE_RESTRICTION))
                              for match in pattern.finditer(remainder))
        cursor = 0
        held = bool(_HOLD.search(remainder))
        for start, negative_end, kind, restriction in restrictions:
            if start < cursor:
                continue
            cursor = -negative_end
            source_object = restriction["object"]
            if source_object:
                quoted = source_object[:1] in {'`', '"', "'"} and source_object[-1:] == source_object[:1]
                if quoted:
                    source_object = source_object[1:-1]
                identity = '`' + source_object + '`'
                if not quoted:
                    # Prefer an exact source name before a syntactic article
                    # or type prefix. Quoted identity bytes never lose words.
                    prefix = (restriction.groupdict().get("determiner") or '') + (restriction.groupdict().get("kind") or '')
                    exact = list(dict.fromkeys((prefix + source_object, source_object)))
                    candidates = exact + [candidate for value in exact
                                          if (candidate := ' '.join(value.split())) not in exact]
                    identity = next(('`' + candidate + '`' for candidate in candidates
                                     if '`' + candidate + '`' == object_name or '`' + candidate + '`' in seen_objects), identity)
                method_role = re.search(r"\b(?:method|via|through|using)\s*$", remainder[:start], re.I)
                bare_identity = (identity in seen_objects or '/' in source_object
                                 or (re.search(r"\b(?:filesystem|PVC|PV|volume|disk)$", source_object, re.I)
                                     and not _CONCEPT.fullmatch(source_object)))
                bound_object = (kind == "directed" or (not method_role and (
                    quoted or restriction.groupdict().get("kind") or bare_identity)))
                if bound_object:
                    object_context = identity == object_name
            action_context = restriction["action"].lower()
            # A later action or object cannot cancel this potential limit.
            if held and authority_scope and object_context is not False and action_context == desired_action:
                return True
        related_action = desired_action is None or action_context in {None, desired_action}
        event_conflict = record.fields.get("event") and record.fields["event"] in remainder and mentions
        if held and (authority_scope or event_conflict) and object_context is not False:
            if related_action and (mentions or _PROTECTION.search(remainder)):
                return True
            # Unknown restriction forms abstain only in the actual local
            # object context. Literal method names do not supply action words.
            outside = re.sub(_RAW_LITERAL, ' ', remainder)
            head = re.match(r"\s*(?:[#>*-]+\s*)?(?P<action>" + _ACTION_WORDS + r")\b", outside, re.I)
            if head and head["action"].lower() == desired_action:
                return True
        return False

    def segment_problem(text):
        return any(clause_problem(clause) for clause in _raw_clause_units(text))

    for unit in _units(raw):
        events = sorted((match.start(), match.end(), phase, match)
                        for phase, grammar in _GRAMMARS.items() for match in grammar.finditer(unit))
        cursor = 0
        for start, end, phase, match in events:
            if start < cursor:
                continue
            if segment_problem(unit[cursor:start]):
                return True
            fields = match.groupdict()
            seen_objects.add(fields["object"])
            object_context = fields["object"] == object_name
            action_context = fields.get("action")
            if object_context:
                if record.phase == "owner" and phase == "owner" and fields != record.fields:
                    return True
                if (record.phase in {"completed", "history"} and phase == record.phase
                        and fields.get("event") == record.fields.get("event") and fields != record.fields):
                    return True
                if match[0] not in answer_units and phase not in {"history", "completed", "metadata", "read"}:
                    if segment_problem(match[0]):
                        return True
            cursor = end
        if segment_problem(unit[cursor:]):
            return True
    return False


def storage_authority_problem(
    question: str,
    answer: str,
    cited_texts: Mapping[str, str] | Sequence[str],
    *,
    retrieved_texts: Mapping[str, str] | Sequence[str] = (),
) -> str | None:
    """Check a bounded source-record answer. Never grant current permission."""
    if not storage_authority_required(question):
        return None
    cited = _values(cited_texts)
    if not cited or any(not body.strip() for body in cited):
        return STORAGE_AUTHORITY_BODY_REASON
    if _UNKNOWN.fullmatch(answer.strip()):
        # Conditional ownership does not establish current caller permission.
        # Other negative retrieval facets abstain instead of claiming absence.
        # An unrelated negative clause cannot cancel a possible declaration.
        # Ambiguous declarations abstain; raw text never grants permission.
        if any(_POSSIBLE_CURRENT_PERMISSION.search(unit)
               for raw in cited + _values(retrieved_texts) for unit in _units(raw)):
            return STORAGE_AUTHORITY_REASON
        return None
    prefix = "The cited source records: "
    if not answer.startswith(prefix):
        return STORAGE_AUTHORITY_REASON
    units = _units(answer[len(prefix):])
    records = [_record(unit) for unit in units]
    if not records or any(record is None for record in records):
        return STORAGE_AUTHORITY_REASON
    supported = [record for body in cited for record in _records(_source_body_text(body))]
    roles = _question_roles(question)
    for record in records:
        if not any(record == source for source in supported):
            return STORAGE_AUTHORITY_REASON
        if _role_mismatch(record, roles):
            return STORAGE_AUTHORITY_REASON
        if any(_contrary(raw, record, set(units)) for raw in cited + _values(retrieved_texts)):
            return STORAGE_AUTHORITY_REASON
    return None
