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
    r"permission|authorized?|unverified|unconfirmed)\b|\b(?:do not|not verified)\b", re.I
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
    r"\b(?:no|zero|0)\s+(?:(?:old|retained|shared|remaining|dedicated)\s+){0,4}"
    + _COPY + r"\s+(?:(?:are|were|is|was|still)\s+)?(?:remain(?:ing|s)?|left|present|exist(?:s)?)\b"
    r"|\b(?:all|every)\s+(?:(?:old|retained|shared|dedicated)\s+){0,4}"
    + _COPY + r"\s+(?:(?:are|were|have been|was|is)\s+)?(?:removed|deleted|erased|gone)\b"
    r"|\b(?:backup|archive|copy)\s+(?:cleanup|removal|erasure)\s+(?:(?:is|was|has been)\s+)?complete(?:d)?\b",
    re.I,
)


def _erasure_conflict(clause: str) -> bool:
    # "No backups remain" is an absence statement, not positive retention.
    # Mask absence spans before testing for contrary observations.
    remainder = _ERASURE_ABSENCE.sub("", clause)
    return bool(_COPY_RE.search(remainder) and _ERASURE_NEGATIVE.search(remainder))


def _completed_erasure(clause: str) -> bool:
    return bool(_ERASURE_ABSENCE.search(clause) and not _ERASURE_INTENT.search(clause)
                and not _erasure_conflict(clause))


def _erasure_problem(question: str, answer: str, evidence: list[str]) -> bool:
    """Refuse a completed-copy-removal claim without observational support.

    A strict whole-scope absence statement needs a corresponding cited
    observation. An instruction, source retirement, or empty serving target
    does not establish it. Conflicting retention evidence fails closed.
    """
    claims = [clause for clause in _clauses(answer) if _ERASURE_ABSENCE.search(clause)
              and not _ERASURE_INTENT.search(clause)]
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
        if ids or subjects:
            if not any(all(_mentions(source, item) for item in ids + subjects) for source in observations):
                return True
    return any(_erasure_conflict(clause) for clause in evidence)


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


def copy_state_supporting_sources(source_texts: dict[str, str]) -> list[str]:
    """Citations with observed absence or explicit retained/zero-removal state.

    Instructions and serving-target results alone do not provide a date for
    retained-copy status. The return value is evidence identity, not permission.
    """
    return [source for source, text in source_texts.items() if any(
        _completed_erasure(clause) or (_erasure_conflict(clause) and not _ERASURE_INTENT.search(clause))
        for clause in _clauses(text)
    )]
