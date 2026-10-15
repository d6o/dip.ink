"""Bounded guards for negative receipt claims after an explicit cutoff.

This is not a temporal entailment engine. Universal absence never follows
from a retrieved subset, even when a source has a later timestamp. A bounded
negative must name the retrieved evidence in each negative clause. Readable
source clauses must contain a date, a receipt topic, and an observation.
Metadata dates, source keys, and queue state cannot supply that observation.
Unsupported prose fails closed. This module does not verify external state.
"""
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone

TEMPORAL_NEGATIVE_REASON = (
    "The cited dated evidence does not support the temporal absence claim. "
    "Limit the answer to retrieved dated evidence or request verification."
)
_STAMP = r"20\d{2}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2}))?"
_CUTOFF = re.compile(r"\b(?:after|since|later than)\s*(?P<stamp>" + _STAMP + r")(?![\w:+-])", re.I)
_DATE = re.compile(r"\b(?:as of|on|at)\s+(?P<stamp>" + _STAMP + r")(?![\w:+-])", re.I)
_TOPIC = re.compile(r"\b(?:custody|recipients?|receipts?|restore[- ]identity|backups?)\b", re.I)
_NEGATIVE = re.compile(
    r"\b(?:no|none|never|neither|absent|missing|unverified|unconfirmed|unavailable|unknown|"
    r"lack(?:s|ed)?|without|not|cannot|nothing|blocked|pending|unproved|unproven|"
    r"zero|0|empty|outstanding|predates?|precedes?|has yet to|remains? to be)\b", re.I
)
_LIMIT = re.compile(
    r"^(?:The\s+)?(?:retrieved|cited|supplied)\s+(?:dated\s+)?"
    r"(?:evidence|sources?|notes?|records?|texts?)\s+"
    r"(?:contains?|includes?|records?|documents?|provides?)\s+no\b", re.I
)
_UNIVERSAL = re.compile(
    r"\b(?:anywhere|everywhere|globally|externally|all sources|exists?|existence|"
    r"prov(?:e|es|ing)|means|therefore|thus|complete coverage)\b", re.I
)
_OBSERVATION = re.compile(r"\b(?:verified|confirmed|received|exists?|recorded|documented|established|provided)\b", re.I)
_NEGATED_OBSERVATION = re.compile(
    r"\b(?:no|not|never)\s+(?:(?:new|qualifying|verified|independent|custody|backup|"
    r"age|off-cluster|protected|receipts?|recipients?|restore[- ]identity|is|are)\s+){0,6}$", re.I
)
_QUEUE = re.compile(r"\b(?:queues?|ingest|indexed|cataloged|backlog|index)\b", re.I)
_METADATA = re.compile(
    r"^\s*(?:captured|created|updated|modified|valid_at|invalid_at|indexed|ingested|timestamp|date|as_of)\s*:", re.I
)


def _time(stamp: str) -> datetime | None:
    try:
        value = datetime.fromisoformat(stamp.upper().replace("Z", "+00:00"))
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    except ValueError:
        return None


def temporal_negative_required(question: str) -> bool:
    """Select explicit ISO cutoff questions about receipts or backup custody."""
    match = _CUTOFF.search(question)
    return bool(match and _time(match['stamp']) and _TOPIC.search(question))


def _clauses(text: str) -> list[str]:
    # Metadata is not an observation. Keep timestamps intact within a clause.
    lines = [line for line in text.replace('`', '').replace('*', '').splitlines()
             if not _METADATA.match(line)]
    return [part.strip() for part in re.split(
        r"[\n;]+|(?<=[.!?])\s+|\s+\b(?:and|but|however|whereas)\b\s+", '\n'.join(lines)
    ) if part.strip()]


def temporal_negative_problem(
    question: str, answer: str, source_texts: Mapping[str, str] | Sequence[str],
) -> str | None:
    """Reject universal temporal absence; admit only bounded dated negatives.

    Positive answers are outside this guard. Each negative clause must limit
    its claim to retrieved evidence. Every cited body needs a dated receipt
    observation. A post-cutoff positive receipt defeats a retrieved absence.
    A source date alone never establishes external coverage.
    """
    if not temporal_negative_required(question):
        return None
    cutoff = _time(_CUTOFF.search(question)['stamp'])
    claims = _clauses(answer)
    negatives = [clause for clause in claims if _NEGATIVE.search(clause)]
    if not negatives:
        return None
    # Do not permit a short "No" or an implicit universal in another clause.
    if any(not _LIMIT.search(clause) or _UNIVERSAL.search(clause)
           or len(_NEGATIVE.findall(clause)) != 1 for clause in negatives):
        return TEMPORAL_NEGATIVE_REASON
    values = list(source_texts.values() if isinstance(source_texts, Mapping) else source_texts)
    if not values:
        return TEMPORAL_NEGATIVE_REASON
    for text in values:
        observations = []
        for clause in _clauses(text):
            if not _TOPIC.search(clause) or _QUEUE.search(clause):
                continue
            dates = [when for match in _DATE.finditer(clause) if (when := _time(match['stamp']))]
            if not dates or not (_NEGATIVE.search(clause) or _OBSERVATION.search(clause)):
                continue
            observations.append(clause)
            affirmative = any(
                not _NEGATED_OBSERVATION.search(clause[:match.start()])
                for match in _OBSERVATION.finditer(clause)
            )
            if any(when > cutoff for when in dates) and affirmative:
                return TEMPORAL_NEGATIVE_REASON
        if not observations:
            return TEMPORAL_NEGATIVE_REASON
    # A bounded absence must still identify the receipt topic and cutoff.
    if any(not _TOPIC.search(clause) or not _CUTOFF.search(clause) for clause in negatives):
        return TEMPORAL_NEGATIVE_REASON
    if any(_time(_CUTOFF.search(clause)['stamp']) != cutoff
           or clause[_CUTOFF.search(clause).end():].strip(' .!?') for clause in negatives):
        return TEMPORAL_NEGATIVE_REASON
    return None
