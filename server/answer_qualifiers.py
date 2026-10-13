"""Fail closed for three narrow answer qualifiers, without external services.

Only readable text from cited sources belongs in ``source_texts``. Source keys,
headings on separate lines, and nearby unrelated sentences do not prove a role
or a bound. This module does not establish freshness or grant authority.
"""
import re

REQUEST_REASON = "The cited text does not establish the requested per-request bounds and units."
LEAD_REASON = "The cited text does not bind the complete address to the requested project lead."
HOLD_REASON = "The cited text does not establish the requested project-specific operational hold."
ERASURE_REASON = "The cited text does not establish completed removal or verified absence of the retained copies."

_NAME = r"[A-Za-z0-9][A-Za-z0-9._-]*"
_ADDRESS = re.compile(rf"(?<![\w./~-])({_NAME})/({_NAME})~({_NAME}):([0-9]+)(?![\w:~/-])")
_LEAD_QUESTION = re.compile(rf"\b({_NAME})(?:'s)?\s+project\s+lead(?:'s)?\s+address\b", re.I)
_REQUEST = re.compile(r"\b(?:per[- ]request|(?:each|every|single) request|request[- ](?:limit|cap|bound|maximum)s?)\b", re.I)
_ALLOWANCE = re.compile(r"\b(?:(?:per|each|every)[- ](?:unit|day|month|account)|cumulative|allowance|daily|monthly)\b", re.I)
_OPERATION = re.compile(r"\b(?:rollouts?|workload admissions?|admission|deployments?)\b", re.I)
_HOLD = re.compile(r"\b(?:holds?|held|paused?|blocked|suspended)\b", re.I)
_UNCERTAIN = re.compile(r"\b(?:no|not|never|unknown|unconfirmed|unsupported|might|may|could|if|whether|proposed|retired|lifted|released|ended)\b|\?", re.I)
_MEASURE = re.compile(
    r"(?<![\w.-])(?P<number>\d+(?:,\d{3})*(?:\.\d+)?)\s*"
    r"(?P<label>(?:(?:input|output|prompt|completion|response|UTF-8|text)\s+)*)"
    r"(?P<unit>tokens?|bytes?|characters?|messages?|KiB|MiB|KB|MB)\b", re.I)
_DIRECTION = re.compile(r"\b(input|output|prompt|completion|response)\b", re.I)


# Requests and plans are not observations. Group copy words, but do not
# equate copies with a serving target, metadata receipt, or producer stop.
_COPY = r"(?:backups?|archives?|copies|copy)"
_COPY_RE = re.compile(r"(?<!\w)" + _COPY + r"(?!\w)", re.I)
_ERASURE_INTENT = re.compile(
    r"\b(?:should|must|shall|would|could|may|might|requested?|instruct(?:ion|ed)?|"
    r"desired|intended|intend|planned?|proposed|goal|requires?|requirement|prohibits?|"
    r"permission|authorized?|unverified|unconfirmed|not|never)\b|\b(?:do not|not verified)\b", re.I
)
_ERASURE_NEGATIVE = re.compile(
    r"\b(?:zero|0|no)\s+(?:(?:dedicated|backup|retained|shared)\s+){0,3}"
    + _COPY + r"\s+(?:(?:were|are|have been|was|is)\s+)?(?:removed|deleted|erased)\b"
    r"|\b(?:not|never|no)\s+(?:complete(?:d)?|removed|deleted|erased)\b"
    r"|\b(?:incomplete|pending|blocked)\b"
    r"|\b(?:copies|archives?|backups?)\s+(?:are |were |still |remain |remains )*(?:retained|present|remain|remains)\b",
    re.I,
)
_ERASURE_ABSENCE = re.compile(
    r"\b(?:no|zero|0)\s+(?:(?:old|retained|shared|remaining|dedicated|local|new)\s+){0,4}"
    + _COPY + r"\s+(?:(?:are|were|is|was|still)\s+)?(?:remain(?:ing|s)?|left|present|exist(?:s)?)\b"
    r"|\b(?:all|every)\s+(?:(?:old|retained|shared|dedicated|local|new)\s+){0,4}"
    + _COPY + r"\s+(?:(?:are|were|have been|was|is)\s+)?(?:removed|deleted|erased|gone)\b"
    r"|\b" + _COPY + r"\s+(?:are|were|have been|is|was|has been)\s+(?:gone|removed|deleted|erased)\b"
    r"|\b(?:backup|archive|copy)\s+(?:cleanup|removal|erasure)\s+(?:(?:is|was|has been)\s+)?complete(?:d)?\b",
    re.I,
)


def _erasure_conflict(clause: str) -> bool:
    # "No backups remain" is an absence statement, not positive retention.
    # Mask absence spans before testing for contrary observations.
    if re.search(r"\b(?:zero|0|no)\s+(?:(?:dedicated|shared|retained|backup)\s+){0,3}"
                 + _COPY + r"\s+(?:(?:were|are|have been|was|is)\s+)?(?:removed|deleted|erased)\b", clause, re.I):
        return True
    remainder = _ERASURE_ABSENCE.sub("", clause)
    return bool(_COPY_RE.search(remainder) and _ERASURE_NEGATIVE.search(remainder))


_COPY_OBJECT_RE = re.compile(
    r"(?<!\w)(?P<scope>(?:(?:dedicated|shared|retained|old|local|new|remaining)\s+)*)"
    r"(?P<kind>backups?|archives?|copies|copy)(?!\w)", re.I
)
_SERVING_SCOPE = re.compile(r"\bserving[- ]targets?\b|\bsource (?:code|plan|retirement)\b|\bexecutable examples?\b", re.I)


def _copy_objects(clause: str) -> set[tuple[str, tuple[str, ...]]]:
    """Keep copy kind and explicit modifiers; do not infer broader scope."""
    objects = set()
    for match in _COPY_OBJECT_RE.finditer(clause):
        kind = match['kind'].casefold()
        kind = 'copy' if kind in ('copies', 'copy') else kind.removesuffix('s')
        objects.add((kind, tuple(sorted(match['scope'].casefold().split()))))
    return objects


def _ticket_scope(question: str, text: str) -> set[str]:
    ids = set(re.findall(r"\b[a-z0-9._-]+#\d+\b", text.casefold()))
    if not ids and re.search(r"\bstatus\b", question, re.I):
        status_scope = re.split(r"\bstatus\b", question, flags=re.I)[-1]
        ids = set(re.findall(r"\b[a-z0-9._-]+#\d+\b", status_scope.casefold()))
    return ids


def _different_explicit_tickets(question: str, claim: str, evidence: str) -> bool:
    claimed = _ticket_scope(question, claim)
    recorded = _ticket_scope("", evidence)
    return bool(claimed and recorded and claimed.isdisjoint(recorded))


def _copy_conflict_overlaps(claim: str, conflict: str) -> bool:
    """Only contrary observations of a compatible copy object can veto it.

    Generic copy evidence may cover backups or archives. Explicit dedicated
    and shared scopes are separate, as are old and new scopes. Other unclear
    combinations remain conservative; this is not object identity resolution.
    """
    for kind, scope in _copy_objects(claim):
        for other_kind, other_scope in _copy_objects(conflict):
            if kind != other_kind and 'copy' not in (kind, other_kind):
                continue
            tags, other_tags = set(scope), set(other_scope)
            if any((a in tags and b in other_tags) or (b in tags and a in other_tags)
                   for a, b in (('dedicated', 'shared'), ('old', 'new'))):
                continue
            return True
    return False


def _erasure_claims(text: str) -> list[str]:
    claims = []
    for clause in _clauses(text):
        match = _ERASURE_ABSENCE.search(clause)
        if not match or _ERASURE_INTENT.search(clause):
            continue
        # Direct predicates also match 'zero copies were removed'. That is
        # explicitly no effect, not a positive completion claim.
        prefix = clause[:match.start()]
        if re.search(r"\b(?:zero|0|no)\s+(?:(?:dedicated|shared|retained)\s+){0,3}$", prefix, re.I):
            continue
        # A counted subset can be removed while other copies remain. This
        # total-erasure guard does not relabel that bounded historical result.
        if re.search(r"\b(?:[1-9]\d*|one|two|three|four|five|some|several|a|an)\s+"
                     r"(?:(?:dedicated|shared|retained|old|local|new)\s+){0,3}$", prefix, re.I):
            continue
        claims.append(clause)
    return claims


def _completed_erasure(clause: str) -> bool:
    return bool(_erasure_claims(clause) and not _erasure_conflict(clause)
                and not _SERVING_SCOPE.search(clause))


def _erasure_problem(question: str, answer: str, evidence: list[str]) -> bool:
    """Refuse a completed-copy-removal claim without observational support.

    A strict whole-scope absence statement needs a corresponding cited
    observation. An instruction, source retirement, or empty serving target
    does not establish it. Conflicting retention evidence fails closed.
    """
    claims = _erasure_claims(answer)
    if not claims:
        return False
    observations = [clause for clause in evidence if _completed_erasure(clause)]
    if not observations:
        return True
    # Do not transfer a narrow service observation to another ticket/object.
    for claim in claims:
        ids = re.findall(r"\b[a-z0-9._-]+#\d+\b", claim, re.I)
        # A nameless completion claim must not borrow another task's result.
        # For a compound query, the status part names the cleanup task.
        status_scope = re.split(r"\bstatus\b", question, flags=re.I)[-1]
        if not ids and re.search(r"\bstatus\b", question, re.I):
            ids = re.findall(r"\b[a-z0-9._-]+#\d+\b", status_scope, re.I)
        subjects = list(dict.fromkeys(_subjects(question) + _subjects(claim)))
        objects = _copy_objects(claim)
        if not objects or _SERVING_SCOPE.search(claim):
            return True
        if not any(objects == _copy_objects(source)
                   and all(_mentions(source, item) for item in ids + subjects)
                   for source in observations):
            return True
    return any(_erasure_conflict(clause) and _copy_conflict_overlaps(claim, clause)
               and not _different_explicit_tickets(question, claim, clause)
               for claim in claims for clause in evidence)


def qualifier_requirements(question: str) -> tuple[str, ...]:
    """Return stable intent keys for optional source checks before distillation."""
    keys = []
    if _REQUEST.search(question):
        keys.append("request_limits")
    if _LEAD_QUESTION.search(question):
        keys.append("project_lead")
    if _OPERATION.search(question) and _HOLD.search(question):
        keys.append("workload_hold")
    return tuple(keys)


def _clauses(text: str) -> list[str]:
    # Do not split domain names, decimal numbers, or complete native addresses.
    text = re.sub(r"[`*]", "", text)
    return [part.strip() for part in re.split(r"[\n;]+|(?<=[.!?])\s+|\s+but\s+", text) if part.strip()]


def _subjects(question: str) -> list[str]:
    products = [word for word in re.findall(r"(?<!\w)[A-Z][A-Za-z0-9]*(?!\w)", question)
                if sum(char.isupper() for char in word) >= 2 and any(char.islower() for char in word)]
    domains = re.findall(r"(?<![\w/])(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?![\w/])", question)
    return list(dict.fromkeys(name.casefold() for name in products + domains))


def _mentions(text: str, name: str) -> bool:
    return bool(re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", text, re.I))


def _scoped(text: str, subjects: list[str]) -> bool:
    if not all(_mentions(text, subject) for subject in subjects):
        return False
    # A comparison cannot transfer another product's bound to the named product.
    return not set(_subjects(text)).difference(subjects)


def _measurements(text: str) -> list[tuple[str, str, str, str]]:
    values = []
    for match in _MEASURE.finditer(text):
        direction = _DIRECTION.search(match["label"])
        if not direction:
            prefix = re.split(r",|\band\b", text[:match.start()])[-1][-40:]
            directions = list(_DIRECTION.finditer(prefix))
            direction = directions[-1] if directions else None
        field = direction[1].lower() if direction else ""
        field = {"prompt": "input", "completion": "output", "response": "output"}.get(field, field)
        unit = match["unit"].lower().removesuffix("s")
        encoding = "utf-8" if "utf-8" in match["label"].lower() else ""
        values.append((match["number"].replace(",", ""), unit, field, encoding))
    return values


def _request_problem(question: str, answer: str, evidence: list[str]) -> bool:
    subjects = _subjects(question)
    bounds = [clause for clause in evidence
              if _scoped(clause, subjects) and _REQUEST.search(clause)
              and not _ALLOWANCE.search(clause) and not _UNCERTAIN.search(clause)
              and _measurements(clause)]
    if not bounds:
        return True
    support = [value for clause in bounds for value in _measurements(clause)]
    # An answer must state request bounds, not merely repeat an account allowance.
    answer_clauses = _clauses(answer)
    if any(_REQUEST.search(clause) and _ALLOWANCE.search(clause) for clause in answer_clauses):
        return True
    claims = [clause for clause in answer_clauses
              if not _ALLOWANCE.search(clause) and _measurements(clause)]
    if not claims:
        return True
    for clause in claims:
        for number, unit, field, encoding in _measurements(clause):
            if not any(number == n and unit == u
                       and (not field or field == f)
                       and (not encoding or encoding == e)
                       for n, u, f, e in support):
                return True
    return False


def _address_key(match: re.Match) -> tuple[str, str, str, str]:
    # Only the project key is case-insensitive. Preserve worker and session names.
    return (match[1].casefold(), match[2], match[3], match[4])


def _lead_bindings(text: str, project: str) -> set[tuple[str, str, str, str]]:
    bindings = set()
    role = rf"(?:the\s+)?(?:current\s+|recorded\s+)?{re.escape(project)}(?:\s+project)?\s+lead(?:'s)?(?:\s+(?:native\s+|peer\s+)?address)?"
    for clause in _clauses(text):
        if _UNCERTAIN.search(clause):
            continue
        for address in _ADDRESS.finditer(clause):
            before, after = clause[:address.start()], clause[address.end():]
            direct = re.fullmatch(rf"\s*[-#:]?\s*{role}\s*(?:is|was|at|:|=)\s*", before, re.I)
            reverse = re.fullmatch(rf"\s+is\s+{role}[.\s]*", after, re.I)
            if direct or reverse:
                bindings.add(_address_key(address))
    return bindings


def _lead_problem(question: str, answer: str, evidence: list[str]) -> bool:
    project = _LEAD_QUESTION.search(question)[1]
    supported = {address for clause in evidence for address in _lead_bindings(clause, project)
                 if address[0] == project.casefold()}
    claimed = _lead_bindings(answer, project)
    bare = _ADDRESS.fullmatch(answer.strip().strip("` ."))
    if bare:
        claimed.add(_address_key(bare))
    return not claimed or not claimed.issubset(supported)


def _hold_clauses(texts: list[str], subject: str) -> list[str]:
    # Require a direct operational predicate, not mere project co-occurrence.
    name = rf"(?<!\w){re.escape(subject)}(?!\w)"
    operation = r"(?:(?:new|app|application|production|prototype|workload)\s+){0,4}(?:rollouts?|workload admissions?|admission|deployments?)"
    operations = rf"{operation}(?:\s+(?:and|or)\s+{operation}){{0,2}}"
    status = r"(?:on hold|held|paused|blocked|suspended)"
    passive = rf"{operations}\s+(?:are|is|remain|remains|were|was)\s+{status}"
    active = rf"(?:holds?|held|pauses?|blocks?|suspends?)\s+{operations}"
    predicate = re.compile(
        rf"{name}(?:\s+project lead)?\s+{active}\b"
        rf"|{name}(?:'s)?\s+{passive}\b"
        rf"|{name}(?:\s+project lead)?:\s*{passive}\b"
        rf"|\b(?:{passive}|{active})\s+(?:for|by)\s+{name}"
        rf"|\b(?:rollout|workload admission|deployment) hold\s+(?:affects|covers|applies to)\s+{name}", re.I)
    return [clause for clause in texts if predicate.search(clause) and not _UNCERTAIN.search(clause)]


def _hold_problem(question: str, answer: str, evidence: list[str]) -> bool:
    subjects = _subjects(question)
    lead = _LEAD_QUESTION.search(question)
    if lead:
        subjects.append(lead[1].casefold())
    # Without an explicit owner or project, this guard cannot prove the scope.
    found = False
    for subject in subjects:
        supported = _hold_clauses(evidence, subject)
        claimed = _hold_clauses(_clauses(answer), subject)
        for claim in claimed:
            found = True
            # Each named project in a claim needs its own explicit relationship.
            names = [name for name in subjects if _mentions(claim, name)]
            operations = {"admission" if "admission" in match[0].lower() else
                          "rollout" if "rollout" in match[0].lower() else "deployment"
                          for match in _OPERATION.finditer(claim)}
            if not any(all(_hold_clauses([source], name) for name in names)
                       and all(re.search(rf"\b{operation}", source, re.I) for operation in operations)
                       for source in supported):
                return True
    return not found


def qualifier_problem(question: str, answer: str, source_texts: dict[str, str]) -> str | None:
    """Return a static reason for unsupported qualifiers, or None.

    This is a narrow safety check, not an entailment engine. It rejects indirect
    bindings, tables without inline subjects, and ambiguous mixed-scope clauses.
    The caller must separately check provenance, subject identity, and dates.
    """
    requirements = qualifier_requirements(question)
    evidence = [clause for text in source_texts.values() for clause in _clauses(text)]
    if _erasure_problem(question, answer, evidence):
        return ERASURE_REASON
    if "request_limits" in requirements and _request_problem(question, answer, evidence):
        return REQUEST_REASON
    if "project_lead" in requirements and _lead_problem(question, answer, evidence):
        return LEAD_REASON
    if "workload_hold" in requirements and _hold_problem(question, answer, evidence):
        return HOLD_REASON
    return None


def operational_supporting_sources(question: str, answer: str, source_texts: dict[str, str]) -> dict[str, list[str]]:
    """Map each requested role or hold to sources that establish it directly.

    Use this with dated sources only when assigning observation dates. A
    source that merely mentions a product cannot lend its date to an
    undated role binding. Require independent support for each requested
    relationship; ambiguous split clauses fail closed.
    """
    checks = {"project_lead": _lead_problem, "workload_hold": _hold_problem}
    result = {}
    for requirement in qualifier_requirements(question):
        check = checks.get(requirement)
        if check is None:
            continue
        supporters = [source for source, text in source_texts.items()
                      if not check(question, answer, _clauses(text))]
        if not supporters:
            return {}
        result[requirement] = supporters
    return result


def _bounded_removal_statement(clause: str) -> bool:
    """Recognize a counted or serving-target result, not whole-copy absence.

    This supplies a date only when the exact statement appears in a cited
    body. It does not infer facts from a source plan or expand copy scope.
    """
    if any(match.group(0).casefold() not in ('not', 'never') for match in _ERASURE_INTENT.finditer(clause)):
        return False
    if not re.search(r"\b(?:deleted|removed|erased)\b", clause, re.I):
        return False
    counted = re.search(r"\b(?:[1-9]\d*|one|two|three|four|five|some|several)\s+"
                        r"(?:(?:dedicated|shared|retained|old|local|new|backup)\s+){0,4}"
                        + _COPY + r"\b", clause, re.I)
    return bool(counted or re.search(r"\bserving[- ]targets?\b", clause, re.I))


def copy_state_supporting_sources(question: str, answer: str, source_texts: dict[str, str]) -> list[str]:
    """Dates for observed copy state or exact bounded removal statements.

    A bounded serving result can be described as history, but never supports
    whole-copy erasure. Another ticket's observation cannot supply the date.
    """
    answer_clauses = _clauses(answer)
    normalized = {clause.casefold() for clause in answer_clauses}
    supporters = []
    for source, text in source_texts.items():
        evidence = _clauses(text)
        if _erasure_problem(question, answer, evidence):
            continue
        for clause in evidence:
            if any(_different_explicit_tickets(question, claim, clause) for claim in answer_clauses):
                continue
            exact_bounded = clause.casefold() in normalized and _bounded_removal_statement(clause)
            observed = _completed_erasure(clause) or (_erasure_conflict(clause) and not _ERASURE_INTENT.search(clause))
            if exact_bounded or observed:
                supporters.append(source)
                break
    return supporters
