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
from dataclasses import dataclass

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
_TARGET_TOKEN = re.compile(rf'{_LITERAL}|(?i:https?://)[^\s`]+|[^\s,;?]+')
_TARGET_ROLE = re.compile(
    r'(?<![\w./:-])(?:checks?(?:\s+for)?|for|targets?)(?![\w/:-])'
    r'\s*(?:(?:is|:|=)\s*)?', re.I,
)
_OTHER_ROLE = re.compile(
    rf'\b(?:Host/SNI|DNS|control|proxy|destination)(?:\s+(?:is|:|=))?\s+(?:for\s+)?'
    rf'(?:{_LITERAL}|[^\s,;]+)|\bmethod\s*(?:is|:|=)\s*(?:{_LITERAL}|[^\s,;]+)'
    rf'|\b(?:via|using|uses?|deliver(?:ed)?|named|called)\s+{_LITERAL}'
    rf'|\b(?:(?:independent\s+(?:remote|external)|caller)\s+)?origin(?:\s+(?:is|:|=))?\s+{_LITERAL}'
    rf'|\bobserved execution at\s+{_LITERAL}|\bstatus(?:\s+(?:is|:|=))?\s+{_LITERAL}'
    rf'|\bmethod\s+{_LITERAL}|\bfor this check only\b', re.I,
)
_DOMAIN_VALUE = re.compile(r'[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+\.?')
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
_PROPERTY_SLOT = re.compile(
    rf'(?P<subject>{_LITERAL}|(?i:the caller origin|the origin|it|this check|the method))'
    r'(?P<role>(?i:\s+(?:has\s+)?(?:caller\s+origin|origin|status)))?'
    r'\s*(?P<separator>(?i:is|was|remains))?\s*(?P<punctuation>[:=])?\s*'
    rf'(?P<value>{_LITERAL}|[^\n;]+?(?=[.!?](?:\s|$)|$|\n))'
)
_ALLOWED_PROPERTY_VALUE = re.compile(r'(?:approved|executed|observed|supported|available|safe)', re.I)
_CONCEPT_HEAD_TEXT = (
    r'(?:(?:current|approved|independent)[- ]+){0,3}'
    r'(?:(?:network|external|independent)[- ]+vantages?|caller[- ]origins?|'
    r'independent[- ]+(?:remote|external)[- ]+origins?)'
)
_CONCEPT_HEAD = re.compile(r'^' + _CONCEPT_HEAD_TEXT + r'(?=$|\s|-(?:outside|from)\b)', re.I)
_CONCEPT_LITERAL = re.compile(
    _CONCEPT_HEAD_TEXT + r'(?:[- ]+(?:from[- ]+)?outside[- ]+(?:the[- ]+)?Devbox)?', re.I,
)
_CUTOFF_CUE = r'(?<![\w./:-])(?:after|since|before|until|later\s+than|earlier\s+than|prior\s+to|between)(?![\w/:-])'



@dataclass(frozen=True)
class _RawProperty:
    subject: str
    role: str
    value: str
    value_span: tuple[int, int]
    quoted: bool


def _raw_properties(text: str) -> list[_RawProperty]:
    # Complete declared observations are not ambiguous property fields.
    declared = [match.span() for grammar in (_ORIGIN, _DELIVERY, _OBSERVATION)
                for match in grammar.finditer(text)]
    fields = []
    for match in _PROPERTY_SLOT.finditer(text):
        if any(start <= match.start() < end for start, end in declared):
            continue
        subject = match['subject']
        intrinsic_origin = subject.casefold() in {'the origin', 'the caller origin'}
        if not (match['role'] or intrinsic_origin or match['separator'] or match['punctuation']):
            continue
        role = 'origin' if intrinsic_origin or 'origin' in (match['role'] or '').casefold() else 'status'
        value = match['value'].strip()
        quoted = value.startswith('`') and value.endswith('`')
        value = value[1:-1] if quoted else re.sub(r'\s+for\s+' + _LITERAL + r'$', '', value)
        fields.append(_RawProperty(subject, role, value, match.span('value'), quoted))
    return fields


_MONTHS = {name.casefold(): index for index, name in enumerate(
    ('January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
     'September', 'October', 'November', 'December'), 1)}


def network_vantage_required(question: str) -> bool:
    """Select origin questions and capd client-delivery questions only."""
    # A method name cannot supply intent. A service or concept can retain
    # ordinary code formatting when the surrounding question supplies intent.
    outside_literals = re.sub(_LITERAL, ' ', question)
    metadata_prefix = re.search(
        r'^\s*(?:what|which)\s+(?:(?:is|are)\s+)?(?:the\s+)?(?:current\s+)?'
        r'(?:version|location|meaning|definition|path|filename)\b', outside_literals, re.I,
    )
    metadata_tail = re.search(r'\b(?:version|location|meaning|definition|path|filename|mean)\s*\??\s*$',
                              outside_literals, re.I)
    operative_question = re.match(r'\s*(?:what|which|how|where|can|could|does|did|is|are)\b', outside_literals, re.I)
    operative_request = operative_question and (
        re.search(r'\b(?:can|could|should|may|will)\b', outside_literals, re.I) or _requested_target(outside_literals)[0]
    )
    concept_intent = not metadata_prefix and (operative_request or (not metadata_tail and re.search(
        r'\b(?:obtain|supply|provides?|deliver\w*|approved|independent|external|outside|origins?)\b',
        outside_literals, re.I,
    )))

    def intent_literal(match):
        value = match[0][1:-1].strip()
        if value.casefold() == 'capd':
            return value
        identity = _identity_subject(question, match.start(), match.end()) or any(
            role.start() <= match.start() < role.end() for role in _OTHER_ROLE.finditer(question)
        )
        if concept_intent and not identity and (
            _CONCEPT_HEAD.match(value) or re.fullmatch(
                r'outside[- ](?:the[- ])?Devbox|safe client|network diagnostic(?: method)?', value, re.I,
            )
        ):
            return value.replace('-', ' ')
        return ' '

    text = re.sub(_LITERAL, intent_literal, question)
    return bool(re.search(
        r"(?<![\w./:-])(?:(?:network|external|independent)[- ]+vantages?|caller[- ]origins?|independent[- ]+(?:remote|external)[- ]+origins?)(?![\w/:-])"
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
        # An explicit method or route argument is data, not a cutoff cue.
        if re.search(r'\b(?:deliver(?:ed)?|using|via|named|called|for|checks?|targets?|Host/SNI|DNS|proxy|destination)\s+(?:the\s+)?$',
                     question[:match.start()], re.I):
            return ' '
        if (value.casefold() in {'after', 'since', 'before', 'until', 'later', 'than', 'earlier', 'prior', 'to', 'between'}
                or re.match(r'^(?:after|since|before|until|later\s+than|earlier\s+than|prior\s+to|between)(?:\s+|$)', value, re.I)
                or re.fullmatch(_DATE + r'(?:T[^\s]+)?', value)
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
    # The role parser checks quoted fields separately. Identity text stays opaque.
    return _normalize_negation(re.sub(_LITERAL, ' ', text))


def _mentions(text: str, literal: str) -> bool:
    if literal in re.findall(_LITERAL, text):
        return True
    value = literal[1:-1]
    outside_literals = re.sub(_LITERAL, ' ', text)
    return bool(re.search(r'(?<![\w./:-])' + re.escape(value)
                          + r'(?![\w/:-]|\.[A-Za-z0-9])', outside_literals))


def _network_identity(value: str) -> bool:
    return bool(re.fullmatch(r'(?i:https?://)[^/\s]+(?:/[^\s]*)?', value)
                or _DOMAIN_VALUE.fullmatch(value)
                or re.fullmatch(r'\[[A-Fa-f0-9:]+\]|[A-Fa-f0-9]*:[A-Fa-f0-9:]+', value))


def _identity_subject(text: str, start: int, end: int) -> bool:
    before, after = text[:start], text[end:]
    source = re.search(r'^(?:[\w-]+:\s*|[-+*]\s+)?(?:(?:On|As of) ' + _DATE + r',\s*)?$', before)
    if source and re.match(r'\s+(?:is|was|remains|has|origin|status|executed|executes|ran|runs?|observed)\b', after, re.I):
        return True
    return bool(re.search(r'\b(?:can|could|does|did|will)\s+$', before, re.I)
                and re.match(r'\s+(?:supply|provide|obtain|check|use|execute)\b', after, re.I))


def _target_atom(text: str, offset: int) -> tuple[str | None, int]:
    token = _TARGET_TOKEN.match(text, offset)
    if not token:
        return None, offset
    value = token[0]
    if value.startswith('`') and value.endswith('`'):
        return value[1:-1], token.end()
    if _network_identity(value):
        return value, token.end()
    return None, token.end()


def _temporal_literal(text: str, start: int, end: int, value: str) -> bool:
    before = text[:start].rstrip()
    cue_prefix = re.search(_CUTOFF_CUE + r'\s*$', before, re.I)
    date_value = (re.fullmatch(_DATE + r'(?:T[^\s]+)?', value)
                  or re.fullmatch(r'[A-Za-z]+ \d{1,2},? 20\d{2}', value))
    if date_value:
        return bool(cue_prefix)
    if not re.match(r'^(?:after|since|before|until|later\s+than|earlier\s+than|prior\s+to|between)(?:\s+|$)', value, re.I):
        return False
    # A temporal qualifier follows a completed target or receipt noun role.
    if re.search(r'\b(?:client|method|delivery|diagnostic|receipts?|records?|evidence)\s*$', before, re.I):
        return True
    for role in _TARGET_ROLE.finditer(before):
        target, target_end = _target_atom(before, role.end())
        if target is not None and not before[target_end:].strip():
            return True
    return not before and bool(re.match(r'\s*,', text[end:]))


def _concept_role(text: str, start: int, value: str) -> bool:
    before = text[:start]
    if _CONCEPT_LITERAL.fullmatch(value):
        return bool(re.fullmatch(
            r'\s*(?:what|which|is|are|can)\s+(?:(?:is|are)\s+)?'
            r'(?:(?:a|an|the|current|approved|independent)\s+)*', before, re.I,
        ))
    if re.fullmatch(r'safe client|network diagnostic(?: method)?', value, re.I):
        return bool(re.search(r'\bdeliver(?:ed)?\s+(?:a|an|the)\s*$', before, re.I))
    if re.fullmatch(r'outside[- ](?:the[- ])?Devbox', value, re.I):
        return bool(re.search(r'\b(?:is|are)\s*$', before, re.I))
    return False


def _requested_target(text: str) -> tuple[bool, str | None]:
    """Read one primary target role, not a union of domain-shaped words.

    Explicit unsupported or multiple targets stay invalid. Other network
    roles and method literals cannot supply the primary target identity.
    """
    literals = [match.span() for match in re.finditer(_LITERAL, text)]

    def inside_literal(offset):
        return any(start <= offset < end for start, end in literals)

    masked = list(text)
    other_roles = [field.value_span for field in _raw_properties(text) if field.quoted]
    for role in _OTHER_ROLE.finditer(text):
        if not inside_literal(role.start()):
            other_roles.append(role.span())
            masked[role.start():role.end()] = ' ' * (role.end() - role.start())
    readable = ''.join(masked)
    targets = []
    consumed = []
    for role in _TARGET_ROLE.finditer(readable):
        if inside_literal(role.start()):
            continue
        target, end = _target_atom(readable, role.end())
        if target is None:
            return True, None
        targets.append(target)
        consumed.append((role.start(), end))
        # A coordinated target list is outside this single-target grammar.
        connector = re.match(r'\s*(?:,\s*)?(?:and|or)\s+', readable[end:], re.I)
        if connector and _target_atom(readable, end + connector.end())[0] is not None:
            return True, None
    # A quoted DNS name, URL, or IP also needs a declared role.
    for start, end in literals:
        covered = any(left <= start < right for left, right in consumed + other_roles)
        value = text[start + 1:end - 1]
        temporal = _temporal_literal(text, start, end, value)
        concept = _concept_role(text, start, value)
        service = value.casefold() == 'capd' and re.match(
            r'\s+(?:deliver\w*|provide\w*|safe\s+client|client|network\s+diagnostic)\b', text[end:], re.I,
        )
        declared = covered or temporal or concept or service or _identity_subject(text, start, end)
        if not declared:
            # Unsupported quoted targets must not become an absent target.
            return True, None
    # An unbound domain or URL is ambiguous, not an absent requested target.
    remaining = list(readable)
    for start, end in literals + consumed:
        remaining[start:end] = ' ' * (end - start)
    remainder = ''.join(remaining)
    if any(_target_atom(remainder, token.start())[0] is not None for token in re.finditer(r'\S+', remainder)):
        return True, None
    unique = set(targets)
    return (True, unique.pop()) if len(unique) == 1 else (bool(targets), None)


def _target_matches(question: str, target: str) -> bool:
    explicit, requested = _requested_target(question)
    return not explicit or requested == target[1:-1]


def _body_has_target(body: str, target: str) -> bool:
    if _requested_target(body) == (True, target):
        return True
    # A target can be the explicit subject of a local access observation.
    return bool(re.search(
        rf'^(?:(?:On|As of) {_DATE},\s*)?' + _literal_reference('`' + target + '`')
        + r'\s+(?:succeeds|fails|returns)\b', body,
    ))


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


def _local_origin_veto(clause: str, match, bound_target: bool) -> bool:
    # Keep literal role values. A destination named Devbox is not an origin.
    method = _literal_reference(match['method'])
    target = _literal_reference(match['target'])
    local = r'(?i:(?:the\s+)?(?:`Devbox`|Devbox))(?![\w/:-])'
    relations = (
        method + r'(?i:\s+(?:executed|executes|ran|runs?)\s+for\s+)'
        + target + r'(?i:\s+(?:inside|on|from|at)\s+)' + local,
        method + r'(?i:\s+(?:executed|executes|ran|runs?)\s+(?:inside|on|from|at)\s+)'
        + local + r'(?i:\s+for\s+)' + target,
        method + r'(?i:\s+observed\s+HTTP\s+[1-5][0-9]{2}\s+for\s+)'
        + target + r'(?i:\s+from\s+)' + local,
        r'(?i:\b(?:all|every|both|only)\s+(?:observations?|checks?|requests?|executions?)\s+for\s+)'
        + target + r'(?i:\s+(?:originate[sd]?|execute[sd]?|run|ran|are|were)\s+(?:on|from|at)\s+)'
        + local,
    )
    if any(re.search(relation, clause) for relation in relations):
        return True
    if not bound_target:
        return False
    # A short role clause inherits only this method's target in this source.
    short = (
        method + r'(?i:\s+(?:executed|executes|ran|runs?)\s+(?:inside|on|from|at)\s+)'
        + local + r'(?i:(?!\s+for\b))',
    )
    return any(re.search(relation, clause) for relation in short)


def _property_veto(field: _RawProperty, match, kind: str, method: str | None, target: str | None) -> bool:
    named = field.subject.startswith('`')
    if named and field.subject != match['method']:
        return False
    if target not in (None, match['target'][1:-1]):
        return False
    bound = method == match['method'] and target == match['target'][1:-1]
    if not named and not bound:
        return False
    value = _normalize_negation(field.value)
    if field.role == 'origin':
        if kind != 'origin' or not bound:
            return False
        if re.fullmatch(r'(?:the |same )?Devbox|caller-local|unknown', value, re.I):
            return True
        # Other literal origin identities stay opaque, not positive evidence.
        return bool(re.match(r'^(?:no|not|never|cannot)\s+', value, re.I))
    if field.value == match['method'][1:-1]:
        return False
    return not _ALLOWED_PROPERTY_VALUE.fullmatch(value)


def _contrary(values: list[str], match, kind: str) -> bool:
    for text in values:
        method_context = _mentions(text, match['method'])
        target_context = _mentions(text, match['target'])
        previous_method = None
        previous_target = None
        for clause in _clauses(text):
            props = _properties(clause)
            fields = _raw_properties(clause)
            subject = re.search(
                rf'^(?:[\w-]+:\s*|[-+*]\s+)?(?:(?:On|As of) {_DATE},\s*)?'
                rf'(?P<method>{_LITERAL})\s+', clause,
            )
            named_subject = subject['method'] if subject else next(
                (field.subject for field in fields if field.subject.startswith('`')), None,
            )
            if named_subject:
                if previous_method != named_subject:
                    previous_target = None
                previous_method = named_subject
            explicit, clause_target = _requested_target(clause)
            if explicit and clause_target is not None:
                previous_target = clause_target
            # A named local check must not transfer its limits to another check.
            inherited = previous_method == match['method'] or (previous_method is None and method_context)
            universal = target_context and re.search(r'^\s*(?:all|every|only|both)\b', props, re.I)
            relevant = (inherited and previous_target in (None, match['target'][1:-1])) or universal
            if kind == 'origin' and _local_origin_veto(
                clause, match, previous_method == match['method'] and previous_target == match['target'][1:-1],
            ):
                return True
            if not relevant:
                continue
            property_unknown = any(_property_veto(field, match, kind, previous_method, previous_target)
                                   for field in fields)
            if (property_unknown or _NEGATIVE.search(props)
                    or re.search(r'\b(?:status\s+(?:is\s+)?|(?:is|was|remains)\s+)unknown\b', props, re.I)):
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
    explicit_target, requested_target = _requested_target(question)
    if explicit_target and requested_target is None:
        return NETWORK_VANTAGE_REASON
    cutoff_cues = re.findall(_CUTOFF_CUE, _cutoff_text(question), re.I)
    if len(cutoff_cues) > 1 or (cutoff_cues and cutoff is None):
        # Unsupported or multiple constraints cannot become a history query.
        return NETWORK_VANTAGE_REASON
    checked = False
    for claim in _clauses(answer):
        if re.fullmatch(r'(?:Recorded background|Background context|This is historical background)', claim):
            continue
        lack = _ORIGIN_LACK.fullmatch(claim)
        if lack:
            if (not _target_matches(question, lack['target'])
                    or not any(_body_has_target(body, lack['target'][1:-1])
                               and re.search(r'\b(?:network|Devbox|origin|vantage)\b', body, re.I) for body in bodies)
                    or any(_origin_candidate(clause, lack['target']) for clause in raw)):
                return NETWORK_VANTAGE_REASON
            checked = True
            continue
        lack = _DELIVERY_LACK.fullmatch(claim)
        if lack:
            if (not cutoff or _day(lack['date']) != cutoff
                    or not any(re.search(r'\b(?:capd|network|Devbox|origin|vantage)\b', body, re.I)
                               and (not explicit_target or _body_has_target(body, requested_target)) for body in bodies)
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
