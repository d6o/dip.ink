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
_DATE = r"20\d{2}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)?"
_STORAGE = re.compile(
    r"\b(?:storage|filesystems?|file[- ]systems?|shared[- ]home|home[- ]PVC|"
    r"capacity|PVCs?|PVs?|Longhorn|disks?|volumes?|expansion|resize|unpin)\b", re.I,
)
_INTENT = re.compile(
    r"\b(?:own(?:s|er|ership)?|authority|permissions?|constraints?|protect\w*|"
    r"holds?|gates?|conditions?|risks?|clean[- ]?up|expan\w*|resize|retention|unpin|"
    r"delet\w*|remov\w*|patch\w*|writ\w*|mutat\w*)\b", re.I,
)
_CONCEPT = re.compile(
    r"(?:storage|filesystem|file system|shared-home|home PVC|capacity|cleanup|expansion|"
    r"resize|retention|unpin|protection|authority)(?:[ -]+(?:storage|filesystem|capacity|"
    r"risk|cleanup|expansion|constraints|authority|permission))*", re.I,
)
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
    rf"On (?P<event>{_DATE}), project (?P<owner>{_LITERAL}) completed cleanup "
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
    r"(?:does not establish|does not identify) (?:the storage owner and protection gates|"
    r"current storage permission|a storage operation method|the requested storage scope)"
    r"(?:\. Current permission remains unverified)?\.?"
)
_HOLD = re.compile(
    r"\b(?:no|not|never|cannot|blocked|protected|unchanged|hold|holds|requires?|"
    r"conditional|only|sole|writer|owner|owns|gates?|guards?|retains?|unverified)\b", re.I,
)
_PROPERTY = re.compile(
    rf"\b(?P<role>owner|project|ticket|method|hold|conditions|scope|phase|protection limits|sole writer)"
    rf"\s*(?:(?:is|was|remains)\s*[:=]?|[:=])\s*(?P<value>{_LITERAL}|[^\n]*?)(?=$|\n)", re.I,
)
_PROTECTION = re.compile(
    r"^(?:(?:The|This|All|Existing|Current|Its|Their)\s+)*(?:activation|cleanup|"
    r"partials?|worktrees?|custody|fence|capture|retention|retirement|protection|hold|"
    r"rollback|candidate|timestamp|it\b|they\b)", re.I,
)


def storage_authority_required(question: str) -> bool:
    """Select storage scope questions, including formatted concept words."""
    # A filename alone cannot supply intent. Quoted concept words can.
    text = re.sub(_LITERAL, lambda m: m[0][1:-1] if _CONCEPT.fullmatch(m[0][1:-1]) else " ", question)
    text = re.sub(r"[*_]", "", text)
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


def _question_roles(question: str) -> dict[str, str]:
    # Explicit roles bind literally. An incidental mention cannot bind a role.
    roles = {}
    for role, pattern in {
        "owner": rf"\bproject\s+({_LITERAL})",
        "ticket": rf"\bticket\s+({_LITERAL})",
        "object": rf"\b(?:storage object|filesystem|PVC|expansion of|cleanup of)\s+({_LITERAL})",
        "method": rf"\b(?:via|through|using|method)\s+({_LITERAL})",
        "hold": rf"\bhold\s+({_LITERAL})",
    }.items():
        matches = list(dict.fromkeys(re.findall(pattern, question, re.I)))
        if len(matches) == 1:
            roles[role] = matches[0]
        elif matches:
            roles[role] = "\x00ambiguous"
    # A plain named filesystem is also an explicit question role.
    # Do not invent aliases between a filesystem, a PVC, and another object.
    named_objects = list(dict.fromkeys(re.findall(
        r"\b(?:the|for|of|owns?)\s+([A-Z][A-Za-z0-9_.-]*(?:[ -](?:shared[- ]home|home))? "
        r"(?:filesystem|PVC))\b", question,
    )))
    if named_objects:
        identities = set('`' + value + '`' for value in named_objects)
        if "object" in roles:
            identities.add(roles["object"])
        roles["object"] = next(iter(identities)) if len(identities) == 1 else "\x00ambiguous"
    return roles


def _contrary(raw: str, record: _Record, answer_units: set[str]) -> bool:
    """Raw text only vetoes. It cannot fill a missing positive field."""
    object_name = record.fields["object"]
    raw_objects = {match["object"] for grammar in _GRAMMARS.values() for match in grammar.finditer(raw)}
    scoped_context = raw_objects == {object_name}
    for unit in _units(raw):
        # Narrative, metadata scalars, headings, and code can all retain holds.
        # A raw record can occur inside a scalar or a fenced example.
        remainder = unit
        for phase, grammar in _GRAMMARS.items():
            for match in grammar.finditer(unit):
                fields = match.groupdict()
                scoped_context = fields["object"] == object_name
                if not scoped_context:
                    continue
                if record.phase == "owner" and phase == "owner" and fields != record.fields:
                    return True
                if (record.phase in {"completed", "history"} and phase == record.phase
                        and fields.get("event") == record.fields.get("event") and fields != record.fields):
                    return True
                # A complete negative limit stays attached to its own phase.
                # The surrounding raw text can still contain a contrary hold.
                if match[0] in answer_units or phase in {"history", "completed", "metadata", "read"}:
                    remainder = remainder.replace(match[0], "")
        # Match the literal object even when raw prose omits backticks.
        identity = object_name[1:-1]
        mentions = re.search(r"(?<![\w./:-])" + re.escape(identity) + r"(?![\w./:-])", remainder)
        if mentions:
            scoped_context = True
        # A new explicit storage object ends inherited veto context.
        elif re.search(rf"(?:storage object|filesystem|PVC)\s+{_LITERAL}", remainder, re.I):
            scoped_context = False
        elif re.search(rf"^{_LITERAL}\s+(?:is|remains|has)\b", remainder) and _STORAGE.search(remainder):
            scoped_context = False
        authority_scope = record.phase in {"owner", "retention", "read"}
        if authority_scope and scoped_context:
            for prop in _PROPERTY.finditer(remainder):
                role = prop["role"].lower()
                role = "owner" if role == "project" else role
                # Raw fields can contradict a record, never supply its roles.
                if record.fields.get(role) != prop["value"]:
                    return True
        event_conflict = (record.fields.get("event") and record.fields["event"] in remainder
                          and mentions)
        if (_HOLD.search(remainder) and (authority_scope or event_conflict)
                and (mentions or (scoped_context and _PROTECTION.search(remainder)))):
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
        if any(record.fields.get(role) != identity for role, identity in roles.items()):
            return STORAGE_AUTHORITY_REASON
        if any(_contrary(raw, record, set(units)) for raw in cited + _values(retrieved_texts)):
            return STORAGE_AUTHORITY_REASON
    return None
