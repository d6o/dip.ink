"""Conservative lexical checks for named-service credential-read methods.

Pass only readable text from all cited sources, never keys or descriptions.
This module does not establish freshness or authorize a live operation. It
requires a direct clause with the service, credential operation, named method,
and an approved live capability (or a supported installed live capability).

False negatives are intentional. Pronouns, lowercase service names, indirect
method descriptions, cross-sentence bindings, and complex coordinated prose
are unsupported. Positive source clauses need "method for Service credential
reads" or "credential reader for Service" syntax. Other word orders can fail.
This is not a general entailment or grammatical parser.
"""
import re
from collections.abc import Mapping, Sequence

CREDENTIAL_METHOD_REASON = (
    "The cited text does not establish the stated approved live method for "
    "the requested service credential reads."
)

_NAME = r"[A-Z][A-Za-z0-9]*(?:[.-][A-Za-z0-9]+)*"
_MOD = r"(?:(?:live|current|protected|exact|approved|supported)\s+)*"
_CREDENTIAL = r"(?:credentials?|secrets?)"
_READ = r"(?:reads?|readers?|access|retriev(?:e|al)|fetch(?:es)?)"
_VERB = r"(?:reads?|access(?:es)?|retrieves?|fetch(?:es)?)"
_METHOD = re.compile(r"\b(?:methods?|readers?|helpers?|CLI|SDK|native|commands?)\b", re.I)
# Keep names next to their operation. A location question does not qualify.
_SERVICE_OPERATION = re.compile(
    rf"(?<![\w.-])(?P<before>{_NAME})(?:'s)?\s+{_MOD}{_CREDENTIAL}[- ]{_READ}\b"
    rf"|\b{_VERB}\s+{_MOD}(?P<middle>{_NAME})(?:'s)?\s+{_CREDENTIAL}\b"
    rf"|\b(?:{_CREDENTIAL}[- ]{_READ}|{_VERB}\s+{_MOD}{_CREDENTIAL})"
    rf"(?:\s+method)?\s+(?:from|in|for|of)\s+(?P<after>{_NAME})(?![\w-])"
)
_GENERIC_NAMES = {
    "The", "A", "An", "What", "Which", "No", "None", "Neither", "This", "It", "Its", "There",
    "Current", "Approved", "Live", "Native", "Installed", "Supported", "Exact", "Protected", "Local",
    "Credential", "Credentials", "Secret", "Secrets", "Service", "Services", "Method", "Methods",
    "Reader", "Readers", "Helper", "Helpers", "CLI", "SDK", "API", "Server", "Client", "Provider",
}
_UNCERTAIN = re.compile(
    r"\b(?:no|not|never|none|unknown|unconfirmed|unverified|unsupported|unapproved|"
    r"uninstalled|unavailable|absent|missing|pending|blocked|may|maybe|might|could|would|should|must|"
    r"if|whether|planned|proposed|draft|audit|reviewed|placeholder|internal|"
    r"source[- ]only|source code|source implementation|fixture|example|stub|"
    r"filter(?:ing|ed)?|fails?|failure)\b|\?", re.I
)
_LIVE = re.compile(r"\blive\b", re.I)
_APPROVED = re.compile(r"\bapproved\b", re.I)
_INSTALLED = re.compile(r"\binstalled\b", re.I)
_SUPPORTED = re.compile(r"\bsupported\b", re.I)
_EXACT_UA = re.compile(r"\bexact[- ]User[- ]Agent\b|\bUser[- ]Agent\s+is\s+exact\b", re.I)
# These shapes express absence or an unknown method, not a method selection.
_ABSENCE = re.compile(
    r"\b(?:no|none|neither)\b.{0,100}\b(?:method|reader|helper|capability|approved|installed)\b"
    r"|\b(?:method|reader|helper|capability)\b.{0,60}\b(?:unknown|unavailable|absent|"
    r"unapproved|uninstalled|not (?:approved|installed|available|supported|known))\b"
    r"|\b(?:cannot|can not|could not)\s+(?:establish|identify|confirm|verify)\b"
    r"|\b(?:not|never)\s+(?:an?\s+)?(?:approved|supported|installed|native|live)\b", re.I
)
_SELECT = re.compile(
    r"\b(?:use|uses|using|recommend|recommends|recommended|via|through)\b"
    r"|\b(?:approved|supported|installed|native|current)\b.{0,60}\b(?:method|reader|helper|CLI|SDK)\b"
    r"|\b(?:method|reader|helper|answer)\s*(?:is\b|:|=)", re.I
)
_TOKEN = r"[A-Za-z0-9][A-Za-z0-9._/-]*"
# Only named selections qualify. Backticks can delimit multiword names.
_NAMED_SELECTION = re.compile(
    rf"\b(?:use|uses|using|recommend|recommends|via|through|with)\s+(?:the\s+)?"
    rf"(?P<after>`[^`\n]+`|{_TOKEN})"
    rf"|\b(?:method|reader|helper)\s*(?:is|:|=)\s+(?:the\s+)?(?P<is>`[^`\n]+`|{_TOKEN})"
    rf"|(?P<before>`[^`\n]+`|{_TOKEN})\s+is\s+(?:the\s+|an?\s+)?"
    rf"(?:(?:currently|approved|supported|installed|native|live|current|exact[- ]User[- ]Agent)\s+)+"
    rf"(?:(?:credential|secret)[- ])?(?:method|reader|helper|CLI|SDK|capability)\b", re.I
)
_INVALID_METHOD = {"a", "an", "the", "no", "none", "not", "unknown", "unavailable", "approved", "installed"}
_DIRECT_TARGET = re.compile(
    r"\b(?P<label>(?:(?:credential|secret)[- ])?(?:method|reader|helper|CLI|SDK|capability))"
    r"\s+for\s+(?P<target>.+)", re.I
)


def _clauses(text: str) -> list[str]:
    # A separate heading or another predicate cannot supply a binding.
    # Preserve "installed and supported" as one capability expression.
    text = text.replace("*", "")
    text = re.sub(r"\b(installed|supported|approved)\s+and\s+(?=installed|supported|approved)",
                  r"\1 ", text, flags=re.I)
    return [part.strip() for part in re.split(
        r"[\n;,]+|(?<=[.!?])\s+|\s+\b(?:and|but|while|whereas|however)\b\s+", text
    ) if part.strip()]


def _operation_services(text: str) -> list[str]:
    text = text.replace("`", "").replace("*", "")
    names = [name for match in _SERVICE_OPERATION.finditer(text)
             for name in match.groups() if name and name not in _GENERIC_NAMES]
    return list(dict.fromkeys(name.casefold() for name in names))


def credential_services(question: str) -> list[str]:
    """Return lowercase explicit names next to credential-read operations.

    A method intent must occur in the same clause. Generic nouns and credential
    locations do not qualify. Return the names in their order in the question.
    """
    return list(dict.fromkeys(
        service for clause in _clauses(question) if _METHOD.search(clause)
        for service in _operation_services(clause)
    ))


def _mentions(text: str, name: str) -> bool:
    return bool(re.search(r"(?<![\w./-])" + re.escape(name) + r"(?![\w./-])", text, re.I))


def _method_names(clause: str) -> list[str]:
    names = [name.strip("`").rstrip(".").casefold() for match in _NAMED_SELECTION.finditer(clause)
             for name in match.groups() if name]
    # Also accept a final, backtick-delimited name after "method ... is".
    if _METHOD.search(clause):
        names += re.findall(r"\bis\s+`([^`\n]+)`", clause, re.I)
    return list(dict.fromkeys(name.casefold() for name in names if name not in _INVALID_METHOD))


def _blanket_absence(clause: str) -> bool:
    # An unnamed absence cannot justify a positive method selection either.
    names = [name for name in re.findall(_NAME, clause) if name not in _GENERIC_NAMES]
    return bool(not names and _ABSENCE.search(clause) and _METHOD.search(clause))


def _direct_target(clause: str) -> bool:
    # Accept "method for Service credential reads" or "credential reader for Service".
    for match in _DIRECT_TARGET.finditer(clause):
        target = re.sub(r"^" + _MOD, "", match["target"].replace("`", ""))
        if (_SERVICE_OPERATION.match(target)
                or _SERVICE_OPERATION.match(match["label"] + " for " + target)):
            return True
    return False


def _delivered(clause: str, service: str, method: str, required: str) -> bool:
    return bool(
        _operation_services(clause) == [service] and _mentions(clause, method)
        and not _UNCERTAIN.search(clause) and _LIVE.search(clause)
        and (_APPROVED.search(clause) or (_INSTALLED.search(clause) and _SUPPORTED.search(clause)))
        and _METHOD.search(clause) and _SELECT.search(clause) and _direct_target(clause)
        and _method_names(clause) == [method]
        and (not _EXACT_UA.search(required) or _EXACT_UA.search(clause))
        and all(not re.search(r"\b" + word + r"\b", required, re.I)
                or re.search(r"\b" + word + r"\b", clause, re.I)
                for word in ("native", "installed"))
    )


def credential_method_problem(question: str, answer: str, source_texts: Mapping[str, str] | Sequence[str]) -> str | None:
    """Refuse an unsupported positive method selection for the named services.

    A safely negative answer can pass. The caller must supply escalation and
    must not interpret this result as live authorization. All selected methods
    need direct cited support. A contrary service-wide absence fails closed.
    """
    services = credential_services(question)
    if not services:
        return None
    claims = _clauses(answer)
    values = source_texts.values() if isinstance(source_texts, Mapping) else source_texts
    evidence = [clause for text in values for clause in _clauses(text)]
    selections = [clause for clause in claims
                  if (_SELECT.search(clause) or _operation_services(clause))
                  and not (_ABSENCE.search(clause) and not _method_names(clause))]
    if not selections:
        # Do not accept an empty answer or an unqualified non-answer.
        safe = any(_ABSENCE.search(clause) for clause in claims)
        sourced = all(any(_mentions(clause, service) and _ABSENCE.search(clause)
                         for clause in evidence) for service in services)
        return None if safe and sourced else CREDENTIAL_METHOD_REASON
    if any(_ABSENCE.search(clause) and (len(services) == 1 or any(_mentions(clause, service) for service in services))
           for clause in claims):
        return CREDENTIAL_METHOD_REASON
    for claim in selections:
        methods = _method_names(claim)
        if not methods or _UNCERTAIN.search(claim):
            return CREDENTIAL_METHOD_REASON
        if any(service not in services for service in _operation_services(claim)):
            return CREDENTIAL_METHOD_REASON
        # An implicit service is safe only for a single-service question.
        bound = [service for service in services if _mentions(claim, service)]
        if not bound:
            bound = services if len(services) == 1 else []
        if not bound:
            return CREDENTIAL_METHOD_REASON
        for service in bound:
            if any(_blanket_absence(clause) or (_mentions(clause, service) and _ABSENCE.search(clause)
                   and (re.search(r"\b(?:no|none|neither)\b", clause, re.I)
                        or any(_mentions(clause, method) for method in methods)))
                   for clause in evidence):
                return CREDENTIAL_METHOD_REASON
            if any(not any(_delivered(clause, service, method, question + " " + claim) for clause in evidence)
                   for method in methods):
                return CREDENTIAL_METHOD_REASON
    return None
