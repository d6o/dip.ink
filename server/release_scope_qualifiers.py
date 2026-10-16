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
_GENERIC_PROPERTY = re.compile(
    rf"(?i:\b(?:(?:the|this)\s+)?release(?:[-_ ](?P<role>status|scope|phase))?\s*(?:is|was|remains|:|=))"
    rf"\s*(?P<value>{_LITERAL}|[^\n.]+?)(?=\n|\.(?:\s|$)|$)"
)
_PROPERTY = re.compile(
    rf"(?P<method>{_LITERAL}) release (?P<role>status|scope) "
    rf"(?:is|was|remains|:)\s*(?P<value>{_LITERAL}|[^\n.]+?)"
    rf"(?: for (?P<app>{_LITERAL}) revision (?P<revision>{_REVISION}))?(?=\n|\.(?:\s|$)|$)"
)


def release_scope_required(question: str) -> bool:
    """Select release interface and release scope questions only."""
    # A method path cannot create intent. Quoted concepts can retain intent.
    text = re.sub(_LITERAL, lambda m: m[0][1:-1] if re.fullmatch(
        r"(?:release|deployment|scope|interface|dev app-only|app-only dev|dev-only)",
        m[0][1:-1], re.I,
    ) else " ", question)
    return bool(re.search(r"\b(?:releases?|deployments?)\b", text, re.I)
                and re.search(r"\b(?:scope|interface|app-only|dev-only|readiness|durability)\b", text, re.I))


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


def _raw_veto(text: str, claim: _Record) -> bool:
    # Explicit raw properties retain their veto in titles, code, and scalars.
    # Optional app/revision roles restrict a property to that exact subject.
    named_properties = list(_PROPERTY.finditer(text))
    for match in named_properties:
        if match["method"] != claim.method:
            continue
        if match["app"] and (match["app"], match["revision"]) != (claim.app, claim.revision):
            continue
        value = match["value"].strip().strip("`")
        expected = claim.phase if match["role"] == "status" else claim.scope
        if value != expected:
            return True
    # Short properties inherit only one complete local identity. Raw fields
    # can restrict that identity but cannot establish a positive record.
    raw_records = _records(text)
    identities = {(record.app, record.revision, record.method) for record in raw_records}
    identity = (claim.app, claim.revision, claim.method)
    bound = (claim.app in text and claim.revision in text
             and (claim.method is None or claim.method in text)
             and identities.issubset({identity}))
    if bound:
        for match in _GENERIC_PROPERTY.finditer(text):
            if any(field.start() <= match.start() < field.end() for field in named_properties):
                continue
            value = match["value"].strip().strip("`")
            expected = claim.scope if (match["role"] or "").casefold() == "scope" else claim.phase
            if value.removeprefix("only ") != expected:
                return True
    # A contrary complete raw record also vetoes; raw records never support.
    for other in raw_records:
        if (other.app, other.revision, other.method) != (claim.app, claim.revision, claim.method):
            continue
        if other.phase != claim.phase and not other.historical:
            return True
    return False


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
    requested_revisions = re.findall(
        r"\b(?:deployment|release|revision|commit)\s+(?:of\s+)?`?"
        r"([a-f0-9]{7,40}|v\d+(?:\.\d+){1,3})(?![\w.-])", question, re.I,
    )
    for claim in claims:
        # The requested app must have its own complete record.
        if not re.search(r"(?<!\w)" + re.escape(claim.app[1:-1]) + r"(?!\w)", question, re.I):
            return RELEASE_SCOPE_REASON
        if not claim.historical and any(
            not claim.revision[1:-1].startswith(revision) for revision in requested_revisions
        ):
            return RELEASE_SCOPE_REASON
        supporters = [name for name, records in bodies.items() if _stamp(dates.get(name, ""))
                      and any(_supports(record, claim) for record in records)]
        if not supporters:
            return RELEASE_SCOPE_REASON
        if any(_raw_veto(text, claim) for text in cited.values()):
            return RELEASE_SCOPE_REASON
        if not current or claim.historical:
            continue
        # Relevant retrieved source bodies can veto even without a citation.
        # Missing text cannot turn a conflict into affirmative support.
        for name, text in retrieved.items():
            when = _stamp(dates.get(name, ""))
            if not when:
                continue
            later = any(when >= _stamp(dates[source]) for source in supporters)
            if later and claim.app in text and _raw_veto(text, claim):
                return RELEASE_SCOPE_REASON
            for other in _records(_source_body_text(text), soft_lines=True):
                if other.app != claim.app or other.historical or _supports(other, claim):
                    continue
                if later:
                    # A later proposal does not supersede an actual dev receipt.
                    # Every other phase/revision conflict needs explicit scope.
                    if other.phase == "completed app-only dev" or claim.phase == other.phase:
                        return RELEASE_SCOPE_REASON
    return None
