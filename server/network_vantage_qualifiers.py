"""Bounded source checks for network origin and capd delivery claims.

Only three recorded statement forms and two cited-record negative forms pass.
A positive needs one complete narrative clause, not fields from separate
sources. Literal identities, routes, dates, and qualifications must match.
Raw text can veto support but cannot establish a capability. This is not a
network diagnostic, a general entailment parser, or an authorization check.
"""
import re
from collections.abc import Mapping, Sequence
from datetime import date

from temporal_negative_qualifiers import _normalize_negation, _source_body_text

NETWORK_VANTAGE_REASON = (
    "The cited bodies do not establish the stated caller origin, route, or capd "
    "delivery. Destination and client options do not establish an independent "
    "origin. Limit the answer to the cited records or request verification."
)
NETWORK_VANTAGE_BODY_REASON = (
    "Complete cited source text is unavailable for network-vantage validation."
)
_DATE = r"20\d{2}-\d{2}-\d{2}"
_LITERAL = r"`[^`\n]+`"
_DOMAIN = re.compile(r"(?<![\w./-])(?:[a-zA-Z0-9-]+\.)+(?:dev|com|net|org|ink|io|gd|app|bot)(?![\w/-])")
_ORIGIN = re.compile(
    rf"(?:On|As of) (?P<date>{_DATE}), (?P<method>{_LITERAL}) is an approved network check "
    rf"for (?P<target>{_LITERAL}) from independent remote origin (?P<origin>{_LITERAL}) "
    rf"outside the Devbox via (?P<route>{_LITERAL}), with Host/SNI (?P<host>{_LITERAL}), "
    rf"with observed execution at (?P=origin), for this check only"
)
_DELIVERY = re.compile(
    rf"On (?P<date>{_DATE}), capd delivered (?P<method>{_LITERAL}) as (?:a|an) "
    rf"(?P<kind>(?:approved )?safe (?:native )?(?:client|network diagnostic method)) "
    rf"for (?P<target>{_LITERAL}) with observed successful invocation of (?P=method)"
)
_OBSERVATION = re.compile(
    rf"On (?P<date>{_DATE}), (?P<method>{_LITERAL}) observed HTTP (?P<status>[1-5][0-9]{{2}}) "
    rf"for (?P<target>{_LITERAL}) from the Devbox with destination (?P<destination>{_LITERAL}), "
    rf"DNS (?P<dns>{_LITERAL}), proxy mode (?P<mode>unknown|direct|proxied), "
    rf"and Host/SNI (?P<host>{_LITERAL})"
)
_BOUND = r"The (?:cited|retrieved|held) (?:network )?(?:records|sources|evidence|text) (?:do|does) not establish "
_ORIGIN_LACK = re.compile(_BOUND + rf"an approved outside-Devbox origin for (?P<target>{_LITERAL})")
_DELIVERY_LACK = re.compile(_BOUND + rf"capd delivery of a safe client or network diagnostic method after (?P<date>{_DATE})")
_NEGATIVE = re.compile(
    r"\b(?:no|not|never|cannot|blocked|unsupported|unavailable|draft|planned|proposed|source[- ]only)\b"
    r"|\bsame\s+devbox\b|\bcaller[- ]local\b", re.I,
)
_MONTHS = {name.casefold(): index for index, name in enumerate(
    ('January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
     'September', 'October', 'November', 'December'), 1)}


def network_vantage_required(question: str) -> bool:
    """Select origin questions and capd client-delivery questions only."""
    # A method name cannot supply intent. A service or concept can retain
    # ordinary code formatting when the surrounding question supplies intent.
    outside_literals = re.sub(_LITERAL, ' ', question)
    concept_intent = re.search(
        r'\b(?:obtain|supply|provides?|deliver\w*|approved|independent|external|outside|origins?)\b',
        outside_literals, re.I,
    )

    def intent_literal(match):
        value = match[0][1:-1].strip()
        if value.casefold() == 'capd':
            return value
        if concept_intent and re.fullmatch(
            r'(?:network|external|independent) vantages?|caller[- ]origins?|'
            r'outside[- ](?:the[- ])?Devbox|safe client|network diagnostic(?: method)?', value, re.I,
        ):
            return value
        return ' '

    text = re.sub(_LITERAL, intent_literal, question)
    return bool(re.search(
        r"\b(?:(?:network|external|independent)\s+vantages?|caller[- ]origins?|independent\s+(?:remote|external)\s+origin)\b"
        r"|\boutside[- ](?:the[- ])?devbox\b", text, re.I,
    ) or (re.search(r'\bcapd\b', text, re.I)
          and re.search(r'\b(?:deliver\w*|provided?)\b', text, re.I)
          and re.search(r'\b(?:clients?|network\s+diagnostic)\b', text, re.I)))


def _day(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _cutoff_text(question: str) -> str:
    def literal(match):
        value = match[0][1:-1]
        if (value.casefold() in {'after', 'since'} or re.fullmatch(_DATE + r'(?:T[^\s]+)?', value)
                or re.fullmatch(r'[A-Za-z]+ \d{1,2},? 20\d{2}', value)):
            return value
        return ' '
    return re.sub(r'\*', '', re.sub(_LITERAL, literal, question))


def _cutoff(question: str) -> date | None:
    text = _cutoff_text(question)
    iso = re.search(r'\b(?:after|since)\s+(' + _DATE + r')(?![\w:+-])', text, re.I)
    if iso:
        # Day-only delivery statements cannot establish intra-day order.
        if re.match(r'\s+(?:at\s+)?\d{1,2}:\d{2}', text[iso.end():], re.I):
            return None
        return _day(iso[1])
    named = re.search(r'\b(?:after|since)\s+([A-Za-z]+)\s+(\d{1,2}),?\s+(20\d{2})\b', text, re.I)
    if named and named[1].casefold() in _MONTHS:
        return _day(f'{named[3]}-{_MONTHS[named[1].casefold()]:02}-{int(named[2]):02}')
    return None


def _clauses(text: str) -> list[str]:
    # Protect literal punctuation and case before clause splits.
    prefix = '\x00network-literal'
    while prefix in text:
        prefix += '_'
    literals = []

    def protect(match):
        literals.append(match[0])
        return prefix + str(len(literals) - 1) + '\x00'

    masked = re.sub(_LITERAL, protect, text)
    masked = re.sub(r'\*\*(.*?)\*\*', r'\1', masked)
    parts = re.split(r'[\n;]+|(?<=[.!?])\s+', masked)
    token = re.compile(re.escape(prefix) + r'(\d+)\x00')
    return [token.sub(lambda m: literals[int(m[1])], part.strip().rstrip('.!?'))
            for part in parts if part.strip()]


def _properties(text: str) -> str:
    # Property negation must not change a literal identity or route.
    return _normalize_negation(re.sub(_LITERAL, ' ', text))


def _mentions(text: str, literal: str) -> bool:
    if literal in re.findall(_LITERAL, text):
        return True
    value = literal[1:-1]
    outside_literals = re.sub(_LITERAL, ' ', text)
    return bool(re.search(r'(?<![\w./:-])' + re.escape(value)
                          + r'(?![\w/:-]|\.[A-Za-z0-9])', outside_literals))


def _target_matches(question: str, target: str) -> bool:
    targets = _DOMAIN.findall(question)
    return not targets or target[1:-1] in targets


def _origin_candidate(clause: str, target: str) -> bool:
    props = _properties(clause)
    return bool(_mentions(clause, target) and not _NEGATIVE.search(props)
                and re.search(r'\bapproved\b', props, re.I)
                and re.search(r'\b(?:independent remote origin|outside[- ](?:the[- ])?devbox)\b', props, re.I))


def _delivery_candidate(clause: str, cutoff: date | None) -> bool:
    props = _properties(clause)
    direct_assertion = re.search(r'\bcapd\s+delivered\s+(?!\s*(?:no|not|nothing)\b)', props, re.I)
    if not (direct_assertion or (
        re.search(r'\bcapd\b', props, re.I)
        and re.search(r'\b(?:delivery receipt|observed successful invocation)\b', props, re.I)
        and not _NEGATIVE.search(props)
    )):
        return False
    dates = [_day(value) for value in re.findall(_DATE, props)]
    # An undated apparent delivery can veto a negative, not prove delivery.
    return not dates or not cutoff or any(when and when > cutoff for when in dates)


def _literal_reference(literal: str) -> str:
    return (r'(?:' + re.escape(literal) + r'|(?<![\w./:`-])'
            + re.escape(literal[1:-1]) + r'(?![\w/:`-]|\.[A-Za-z0-9]))')


def _local_origin_veto(clause: str, match) -> bool:
    # Keep literal role values. A destination named Devbox is not an origin.
    method = _literal_reference(match['method'])
    target = _literal_reference(match['target'])
    local = r'(?i:(?:the\s+)?(?:`Devbox`|Devbox))(?![\w/:-])'
    relations = (
        method + r'(?i:\s+(?:has\s+)?(?:caller\s+)?origin\s+(?:(?:is|was)\s+)?)'
        + local + r'(?i:\s+for\s+)' + target,
        method + r'(?i:\s+(?:executed|executes|ran|runs?)\s+for\s+)'
        + target + r'(?i:\s+(?:on|from|at)\s+)' + local,
        method + r'(?i:\s+(?:executed|executes|ran|runs?)\s+(?:on|from|at)\s+)'
        + local + r'(?i:\s+for\s+)' + target,
        method + r'(?i:\s+observed\s+HTTP\s+[1-5][0-9]{2}\s+for\s+)'
        + target + r'(?i:\s+from\s+)' + local,
        r'(?i:\b(?:all|every|both|only)\s+(?:observations?|checks?|requests?|executions?)\s+for\s+)'
        + target + r'(?i:\s+(?:originate[sd]?|execute[sd]?|run|ran|are|were)\s+(?:on|from|at)\s+)'
        + local,
    )
    return any(re.search(relation, clause) for relation in relations)


def _contrary(values: list[str], match, kind: str) -> bool:
    for text in values:
        method_context = _mentions(text, match['method'])
        target_context = _mentions(text, match['target'])
        previous_method = None
        for clause in _clauses(text):
            props = _properties(clause)
            subject = re.search(
                rf'^(?:(?:On|As of) {_DATE},\s*)?(?P<method>{_LITERAL})\s+', clause,
            )
            if subject:
                previous_method = subject['method']
            # A named local check must not transfer its limits to another check.
            inherited = previous_method == match['method'] or (previous_method is None and method_context)
            universal = target_context and re.search(r'^\s*(?:all|every|only|both)\b', props, re.I)
            relevant = inherited or universal
            if kind == 'origin' and _local_origin_veto(clause, match):
                return True
            if not relevant:
                continue
            if _NEGATIVE.search(props):
                return True
            if kind == 'origin' and re.search(r'\borigin\s+(?:is\s+)?unknown\b', props, re.I):
                return True
    return False


def network_vantage_problem(
    question: str, answer: str, source_texts: Mapping[str, str] | Sequence[str],
) -> str | None:
    """Validate each complete draft clause against complete cited bodies.

    Only mapping values count as evidence. The caller must supply all raw
    citations before the display cap. A pass does not grant execution rights.
    """
    if not network_vantage_required(question):
        return None
    values = list(source_texts.values() if isinstance(source_texts, Mapping) else source_texts)
    if not values or any(not text.strip() for text in values):
        return NETWORK_VANTAGE_BODY_REASON
    raw = [clause for text in values for clause in _clauses(text)]
    bodies = [clause for text in values for clause in _clauses(_source_body_text(text))]
    cutoff = _cutoff(question)
    if re.search(r'(?<![\w./:-])(?:after|since)(?![\w/:-])', _cutoff_text(question), re.I) and cutoff is None:
        # An unsupported cutoff must not become an unrestricted history query.
        return NETWORK_VANTAGE_REASON
    checked = False
    for claim in _clauses(answer):
        if re.fullmatch(r'(?:Recorded background|Background context|This is historical background)', claim):
            continue
        lack = _ORIGIN_LACK.fullmatch(claim)
        if lack:
            if (not _target_matches(question, lack['target'])
                    or not any(_mentions(body, lack['target'])
                               and re.search(r'\b(?:network|Devbox|origin|vantage)\b', body, re.I) for body in bodies)
                    or any(_origin_candidate(clause, lack['target']) for clause in raw)):
                return NETWORK_VANTAGE_REASON
            checked = True
            continue
        lack = _DELIVERY_LACK.fullmatch(claim)
        if lack:
            if (not cutoff or _day(lack['date']) != cutoff
                    or not any(re.search(r'\b(?:capd|network|Devbox|origin|vantage)\b', body, re.I) for body in bodies)
                    or any(_delivery_candidate(clause, cutoff) for clause in raw)):
                return NETWORK_VANTAGE_REASON
            checked = True
            continue
        matched = False
        for kind, grammar in (('origin', _ORIGIN), ('delivery', _DELIVERY), ('observation', _OBSERVATION)):
            match = grammar.fullmatch(claim)
            if not match:
                continue
            # Complete clauses keep source-specific qualifiers and identities.
            if (claim not in bodies or not _day(match['date'])
                    or not _target_matches(question, match['target'])
                    or _contrary(values, match, kind)):
                return NETWORK_VANTAGE_REASON
            if kind == 'origin' and (re.search(r'\bdevbox\b', match['origin'], re.I)
                                     or match['origin'] in (match['target'], match['host'])):
                return NETWORK_VANTAGE_REASON
            if kind == 'delivery' and cutoff and _day(match['date']) <= cutoff:
                return NETWORK_VANTAGE_REASON
            matched = True
            checked = True
            break
        if not matched:
            # An observation or a scoped negative cannot authorize a tail.
            return NETWORK_VANTAGE_REASON
    return None if checked else NETWORK_VANTAGE_REASON
