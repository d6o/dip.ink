"""Bounded checks for release phase and scope, not general entailment.

A complete narrative record keeps the app, revision, method, resources, and
phase together. Source dates label records; they do not prove completion or
supply an event time. Raw text can veto support, but cannot establish it.
A pass grants no permission and makes no new live observation.
"""
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone

from temporal_negative_qualifiers import _source_body_text

RELEASE_SCOPE_REASON = (
    "The cited bodies do not establish this release phase and scope together. "
    "Keep the proposal, completed dev receipt, and production readiness separate. "
    "Limit the answer to the source records or request verification."
)
RELEASE_SCOPE_BODY_REASON = (
    "Complete cited source text is unavailable for release-scope validation."
)
RELEASE_SCOPE_SUFFIX = " This answer makes no new live check."
_LITERAL = r"`[^`\n]+`"
_REVISION = r"`[A-Za-z0-9][A-Za-z0-9._:+-]*`"
_DATE = r"20\d{2}-\d{2}-\d{2}"
_SCOPE = "fixed-app metadata, code ConfigMaps, and a conditional Deployment patch"
_PREVIEW_SCOPE = "isolated scratch and loopback resources"
_PREFIX = rf"(?:(?:On|As of) (?P<event_date>{_DATE}), )?"
_RECORD_PREFIX = r"(?:(?:The (?:cited|later) source records that|Recorded evidence:) )?"
_COMPLETE = re.compile(
    _PREFIX + _RECORD_PREFIX
    + rf"(?P<app>{_LITERAL}) (?:completed|deployed)(?P<verified> and verified)? "
    + rf"an app-only dev release of (?P<revision>{_REVISION}) via (?P<method>{_LITERAL}), "
    + rf"limited to (?P<scope>{re.escape(_SCOPE)}); "
    + r"this dev-only release supersedes the earlier dev hold, "
    + r"not production durability, backup, or restore verification; "
    + r"it provides no exec or backup interface and transfers no permission"
)
_PREVIEW = re.compile(
    _PREFIX + _RECORD_PREFIX
    + rf"(?P<app>{_LITERAL}) (?:proposed|requested) a disposable manual-test preview "
    + rf"of (?P<revision>{_REVISION})(?: via (?P<method>{_LITERAL}))?, "
    + rf"limited to (?P<scope>{re.escape(_PREVIEW_SCOPE)}), "
    + r"with separate admission required and no admission receipt or execution; "
    + r"the existing instance, PVC, workers, and routes stay unchanged"
)
# A named role accepts a plain value, a copula, or a field separator. A
# role-free release property requires a copula or field separator instead.
# This prevents ordinary release narrative from becoming a property field.
_COPULA = r"\s*(?:(?:is|was|remains)\b\s*[:=]?|[:=])\s*"
_ROLE_VALUE = rf"(?:{_COPULA}|\s+)"
_PROPERTY_END = r"(?=\n|\.(?:\s|$)|$)"
_PROPERTY_VALUE = rf"(?P<value>{_LITERAL}|[^\n]*?)"
_GENERIC_PROPERTY = re.compile(
    rf"\b(?:(?:the|this)\s+)?(?:release(?:\s+|[-_])(?P<role>status|scope|phase){_ROLE_VALUE}"
    rf"|release{_COPULA}){_PROPERTY_VALUE}{_PROPERTY_END}", re.I,
)
_PROPERTY = re.compile(
    rf"(?P<method>{_LITERAL})\s+(?:has\s+)?release(?:\s+|[-_])(?P<role>status|scope|phase)"
    rf"{_ROLE_VALUE}{_PROPERTY_VALUE}"
    rf"(?:\s+for (?P<app>{_LITERAL}) revision (?P<revision>{_REVISION}))?{_PROPERTY_END}", re.I,
)
_CONTEXT_FIELD = re.compile(
    rf"(?P<role>app|application|subject|revision|method){_ROLE_VALUE}(?P<identity>{_LITERAL})", re.I,
)
_CONTEXT_HEADER = re.compile(
    rf"(?:#{{1,6}}\s+)?(?P<app>{_LITERAL}|[A-Za-z][A-Za-z0-9_.-]*)\s+release\s+record", re.I,
)
_CONCEPT_WORD = r"(?:the|a|an|current|dev|app-only|dev-only|production|releases?|deployments?|scopes?|interfaces?|readiness|durability)"
_CONCEPT_PHRASE = re.compile(_CONCEPT_WORD + rf"(?:[ \t]+{_CONCEPT_WORD}){{0,7}}", re.I)


def release_scope_required(question: str) -> bool:
    """Select release interface and release scope questions only."""
    # A method path cannot create intent. Quoted concepts can retain intent.
    outside_literals = re.sub(_LITERAL, " ", question)
    if (not re.search(r"\b(?:releases?|deployments?)\b", outside_literals, re.I)
            and re.search(r"\b(?:filename|file name|meaning|definition|spelling|names?|named|called|path|location|mean)\b",
                          outside_literals, re.I)):
        return False
    text = re.sub(_LITERAL, lambda m: m[0][1:-1] if _CONCEPT_PHRASE.fullmatch(
        m[0][1:-1],
    ) else " ", question)
    return bool(re.search(r"\b(?:releases?|deployments?)\b", text, re.I)
                and re.search(r"\b(?:scopes?|interfaces?|app-only|dev-only|readiness|durability)\b", text, re.I))


def _units(text: str, *, soft_lines: bool = False) -> list[str]:
    # Periods inside literal methods and revisions do not end a statement.
    literals = []
    marker = "\x00release-literal"
    while marker in text:
        marker += "_"

    def mask(match):
        literals.append(match[0])
        return marker + str(len(literals) - 1) + "\x00"

    masked = re.sub(_LITERAL, mask, text)
    if soft_lines:
        masked = re.sub(r"(?<![.!?\n])\n(?!\n)", " ", masked)
    token = re.compile(re.escape(marker) + r"(\d+)\x00")
    return [token.sub(lambda m: literals[int(m[1])], part.strip().rstrip(".!?"))
            for part in re.split(r"\n+|(?<=[.!?])\s+", masked) if part.strip()]


@dataclass(frozen=True)
class _Record:
    phase: str
    app: str
    revision: str
    method: str | None
    scope: str
    verified: bool
    event_date: str | None
    historical: bool = False


def _record(unit: str) -> _Record | None:
    unit = re.sub(r"^[-+*]\s+", "", unit)
    historical = unit.startswith("Historical record: ")
    if historical:
        unit = unit[len("Historical record: "):]
    for phase, grammar in (("completed app-only dev", _COMPLETE), ("proposal", _PREVIEW)):
        match = grammar.fullmatch(unit)
        if match:
            if match["event_date"]:
                try:
                    datetime.fromisoformat(match["event_date"])
                except ValueError:
                    return None
            return _Record(phase, match["app"], match["revision"], match["method"],
                           match["scope"], bool(match.groupdict().get("verified")),
                           match["event_date"], historical)
    return None


def _records(text: str, *, soft_lines: bool = False) -> list[_Record]:
    return [record for unit in _units(text, soft_lines=soft_lines) if (record := _record(unit))]


def _raw_record_events(text: str) -> list[tuple[int, _Record]]:
    # Use the same complete grammar for raw vetoes, with soft whitespace.
    # Code, titles, and scalars remain eligible only for a veto, not support.
    events = []
    for phase, grammar in (("completed app-only dev", _COMPLETE), ("proposal", _PREVIEW)):
        pattern = re.sub(r"\\ | ", lambda _: r"[ \t\r\n]+", grammar.pattern)
        raw = re.compile(r"(?P<history>Historical[ \t]+record:[ \t]+)?" + pattern)
        for match in raw.finditer(text):
            if match["event_date"]:
                try:
                    datetime.fromisoformat(match["event_date"])
                except ValueError:
                    continue
            events.append((match.end(), _Record(
                phase, match["app"], match["revision"], match["method"], match["scope"],
                bool(match.groupdict().get("verified")), match["event_date"], bool(match["history"]),
            )))
    return events


def _supports(source: _Record, claim: _Record) -> bool:
    return (source.phase, source.app, source.revision, source.method, source.scope) == (
        claim.phase, claim.app, claim.revision, claim.method, claim.scope,
    ) and (not claim.verified or source.verified) and (
        not claim.event_date or source.event_date == claim.event_date
    )


def _stamp(value: str) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)
    except (AttributeError, ValueError):
        return None


def _property_mismatch(role: str | None, value: str, claim: _Record) -> bool:
    scope = (role or "").casefold() == "scope"
    expected = claim.scope if scope else claim.phase
    value = " ".join(value.strip().strip("`").split()).casefold()
    if scope:
        value = value.removeprefix("only ")
    return value != expected.casefold()


def _raw_veto(text: str, claim: _Record) -> bool:
    # Explicit raw properties retain their veto in titles, code, and scalars.
    # Optional app/revision roles restrict a property to that exact subject.
    named_properties = list(_PROPERTY.finditer(text))
    # Context follows source order. It does not combine every identity in
    # the body, and it never borrows an identity from the draft.
    raw_events = _raw_record_events(text)
    events = [(offset, "record", record) for offset, record in raw_events]
    raw_records = [record for _, record in raw_events]
    cursor = 0
    for unit in _units(text):
        offset = text.find(unit, cursor)
        if offset < 0:
            continue
        cursor = offset + len(unit)
        reset = _CONTEXT_FIELD.fullmatch(re.sub(r"^[-+*]\s+", "", unit))
        if reset:
            events.append((cursor, "reset", reset))
        header = _CONTEXT_HEADER.fullmatch(unit)
        if header:
            events.append((cursor, "header", header))
    events.extend((field.end(), "named", field) for field in named_properties)
    for field in _GENERIC_PROPERTY.finditer(text):
        if not any(named.start() <= field.start() < named.end() for named in named_properties):
            events.append((field.start(), "generic", field))
    context = None
    identity = (claim.app, claim.revision, claim.method)
    for _, kind, event in sorted(events, key=lambda event: event[0]):
        if kind == "record":
            context = (event.app, event.revision, event.method)
        elif kind == "reset":
            role = event["role"].casefold()
            if role in {"app", "application", "subject"}:
                context = (event["identity"], None, None)
            elif role == "revision":
                context = ((context or (None, None, None))[0], event["identity"], None)
            else:
                context = (*(context or (None, None, None))[:2], event["identity"])
        elif kind == "header":
            app = event["app"]
            context = (app if app.startswith('`') else '`' + app + '`', None, None)
        elif kind == "named":
            if event["app"]:
                context = (event["app"], event["revision"], event["method"])
            else:
                context = (*(context or (None, None, None))[:2], event["method"])
            if event["method"] == claim.method and all(
                local is None or local == target for local, target in zip(context, identity)
            ) and _property_mismatch(event["role"], event["value"], claim):
                return True
        elif context is not None and all(
            local is None or local == target for local, target in zip(context, identity)
        ) and _property_mismatch(event["role"], event["value"], claim):
            # An incomplete compatible local context can veto, not support.
            return True
    # A contrary complete raw record also vetoes; raw records never support.
    for other in raw_records:
        if (other.app, other.revision, other.method) != (claim.app, claim.revision, claim.method):
            continue
        if other.phase != claim.phase and not other.historical:
            return True
    return False


_QUERY_STOP = {
    "what", "which", "where", "how", "the", "a", "an", "is", "are", "was", "were",
    "current", "dev", "app-only", "dev-only", "production", "release", "releases",
    "deployment", "deployments", "scope", "scopes", "interface", "interfaces",
    "source", "record", "recorded", "after", "before", "for", "not", "and", "or", "it",
    "this", "that", "these", "those", "earlier", "later", "actual", "revision", "revisions",
    "commit", "commits", "method", "methods",
}


def _requested_identities(question: str) -> tuple[dict[str, set[str]], bool, bool]:
    """Read explicit query roles, not a union of all mentioned identities."""
    literals = {}
    marker = "\x00release-query-"
    while marker in question:
        marker += "_"

    def mask(match):
        value = match[0][1:-1]
        if _CONCEPT_PHRASE.fullmatch(value):
            return value
        token = marker + str(len(literals)) + "\x00"
        literals[token] = value
        return token

    text = re.sub(_LITERAL, mask, question)
    token = re.escape(marker) + r"\d+\x00"
    atom = rf"(?:{token}|[A-Za-z0-9_][A-Za-z0-9_./:@+-]*)"
    app_atom = rf"(?:{token}|[A-Za-z][A-Za-z0-9_.-]*)"
    roles = {"app": set(), "method": set(), "revision": set()}
    invalid = False

    def value(raw):
        return literals.get(raw, raw)

    def read(role, offset, *, open_use=False):
        nonlocal invalid
        match = re.match(r"\s*(" + atom + r")", text[offset:])
        if not match:
            invalid = True
            return
        raw = match[1]
        if raw not in literals and raw.casefold() in _QUERY_STOP:
            # An open question such as 'use for this release' has no method
            # argument. An explicit revision role cannot lose its constraint.
            if not open_use or raw.casefold() != "for":
                invalid = True
            return
        roles[role].add(value(raw))
        end = offset + match.end()
        while (more := re.match(r"\s*(?:,?\s*(?:and|or|versus)\s+)(" + atom + r")", text[end:], re.I)):
            raw = more[1]
            if raw not in literals and raw.casefold() in _QUERY_STOP:
                break
            roles[role].add(value(raw))
            end += more.end()

    modifiers = r"(?:(?:current|actual|dev|app-only|dev-only|production)\s+){0,4}"
    app_role = re.compile(
        rf"(?P<first>{app_atom})(?:\s+(?:and|or|versus)\s+(?P<second>{app_atom}))?\s+"
        + modifiers + r"(?:release|deployment)s?\b", re.I,
    )
    for match in app_role.finditer(text):
        if re.search(r"\bnot\s+$", text[:match.start()], re.I):
            continue
        for raw in (match["first"], match["second"]):
            if raw and (raw in literals or raw.casefold() not in _QUERY_STOP):
                roles["app"].add(value(raw))
    for match in re.finditer(r"\b(?:for|about|applies\s+to)\s+(" + app_atom + r")", text, re.I):
        raw = match[1]
        if raw in literals or raw.casefold() not in _QUERY_STOP:
            roles["app"].add(value(raw))
    for match in re.finditer(r"\b(?:via|using|uses?|with\s+method|(?:method|interface)\s*(?:is|:|=))\s*", text, re.I):
        read("method", match.end(), open_use=bool(re.fullmatch(r"uses?\s*", match[0], re.I)))
    for match in re.finditer(r"\b(?:method|interface)\s+(?=" + token + r"|[A-Za-z0-9_.-]+/)", text, re.I):
        read("method", match.end())
    for match in re.finditer(
        r"\b(?:(?:deployment|release)\s+(?:of|at)\s+|(?:revision|commit)s?\s*(?:(?:of|is)\s+|[:=]\s*)?)",
        text, re.I,
    ):
        read("revision", match.end())
    comparison = bool(re.search(r"\b(?:compare|comparison|historical|history)\b", text, re.I))
    return roles, invalid or not roles["app"], comparison


def _revision_matches(requested: str, recorded: str) -> bool:
    if re.fullmatch(r"[a-f0-9]{7,40}", requested) and re.fullmatch(r"[a-f0-9]{7,64}", recorded):
        return recorded.startswith(requested)
    return requested == recorded


def release_scope_problem(
    question: str,
    answer: str,
    source_texts: Mapping[str, str] | Sequence[str],
    *,
    source_dates: Mapping[str, str] | None = None,
    retrieved_texts: Mapping[str, str] | None = None,
    current: bool = False,
) -> str | None:
    """Check all complete cited bodies and the complete draft before caps.

    Mapping keys supply identity only. Dates supply record order only. A
    newer body needs its own completed receipt and explicit dev supersession.
    Uncited records can veto a stale claim, but cannot support a new claim.
    """
    if not release_scope_required(question):
        return None
    cited = dict(source_texts) if isinstance(source_texts, Mapping) else {
        str(index): text for index, text in enumerate(source_texts)
    }
    if not cited or any(not isinstance(text, str) or not text.strip() for text in cited.values()):
        return RELEASE_SCOPE_BODY_REASON
    dates = source_dates or {}
    bodies = {name: _records(_source_body_text(text), soft_lines=True) for name, text in cited.items()}
    claims = [_record(unit) for unit in _units(answer, soft_lines=True)]
    if not claims or any(claim is None for claim in claims):
        return RELEASE_SCOPE_REASON
    retrieved = dict(retrieved_texts or {})
    requested, invalid, comparison = _requested_identities(question)
    multiple = any(len(values) > 1 for values in requested.values())
    if invalid or (multiple and (not comparison or any(not claim.historical for claim in claims))):
        return RELEASE_SCOPE_REASON
    for role, values in requested.items():
        if len(values) > 1 and any(not any(
            _revision_matches(value, claim.revision[1:-1]) if role == "revision"
            else value == getattr(claim, role)[1:-1] if getattr(claim, role) is not None else False
            for claim in claims
        ) for value in values):
            return RELEASE_SCOPE_REASON
    for claim in claims:
        if claim.app[1:-1] not in requested["app"]:
            return RELEASE_SCOPE_REASON
        if requested["method"] and (claim.method is None or claim.method[1:-1] not in requested["method"]):
            return RELEASE_SCOPE_REASON
        if requested["revision"] and not any(
            _revision_matches(revision, claim.revision[1:-1]) for revision in requested["revision"]
        ):
            return RELEASE_SCOPE_REASON
        supporters = [name for name, records in bodies.items() if _stamp(dates.get(name, ""))
                      and any(_supports(record, claim) for record in records)]
        if not supporters:
            return RELEASE_SCOPE_REASON
        if any(_raw_veto(text, claim) for text in cited.values()):
            return RELEASE_SCOPE_REASON
        if claim.historical:
            continue
        # Relevant retrieved source bodies can veto even without a citation.
        # Missing text cannot turn a conflict into affirmative support.
        for name, text in retrieved.items():
            when = _stamp(dates.get(name, ""))
            if not when:
                continue
            later = any(when >= _stamp(dates[source]) for source in supporters)
            if later and _raw_veto(text, claim):
                return RELEASE_SCOPE_REASON
            if not current:
                continue
            for other in _records(_source_body_text(text), soft_lines=True):
                if other.app != claim.app or other.historical or _supports(other, claim):
                    continue
                if later:
                    # A later proposal does not supersede an actual dev receipt.
                    # Every other phase/revision conflict needs explicit scope.
                    if other.phase == "completed app-only dev" or claim.phase == other.phase:
                        return RELEASE_SCOPE_REASON
    return None
