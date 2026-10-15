"""Bounded guards for negative receipt claims after an explicit cutoff.

This is not a temporal entailment engine. Universal absence never follows
from a retrieved subset, even when a source has a later timestamp. A bounded
negative must name the retrieved evidence in each negative clause. Readable
source clauses must start with an observation date and name a receipt topic.
Metadata dates, source keys, examples, and queue state cannot supply it.
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
_DATE = re.compile(
    r"^(?:as of|on|at)\s+(?P<stamp>" + _STAMP + r")(?![\w:/+-]|\.[A-Za-z0-9])", re.I
)
_CANDIDATE_DATE = re.compile(
    r"(?<![\w-])(?P<stamp>" + _STAMP + r")(?![\w:/+-]|\.[A-Za-z0-9])", re.I
)
_TOPIC = re.compile(r"\b(?:custody|recipients?|receipts?|restore[- ]identity|backups?)\b", re.I)
_NEGATIVE = re.compile(
    r"\b(?:no|none|never|neither|absent|missing|unverified|unconfirmed|unavailable|unknown|"
    r"lack(?:s|ed)?|without|not|cannot|nothing|blocked|pending|unproved|unproven|"
    r"zero|0|empty|outstanding|predates?|precedes?|has yet to|remains? to be)\b", re.I
)
_RESTRICTIVE_ABSENCE = re.compile(
    r"\b(?:all|every|each|only|latest|last|most recent)\b.*"
    r"\b(?:before|earlier|older|prior|old|pre[- ]cutoff)(?:\b|(?=20\d{2}))"
    r"|\b(?:before|earlier|older|prior|old|pre[- ]cutoff)(?:\b|(?=20\d{2})).*"
    r"\b(?:all|every|each|only|latest|last|most recent)\b", re.I,
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
    r"^\s*(?:(?:[-+*]|\d+[.)])\s+)?(?:[\"'`]|\*\*|__)?"
    r"(?:captured|created|updated|modified|valid_at|invalid_at|indexed|ingested|timestamp|"
    r"date|as_of|title|name|description|summary|slug|source_slug|filename|url|published|metadata)"
    r"(?:[\"'`]|\*\*|__)?\s*:", re.I
)
_EXCLUDED_HEADING = re.compile(
    r"\b(?:examples?|fixtures?|templates?|samples?|illustrative|hypothetical|publication|"
    r"unrelated|source metadata|note metadata|document metadata|frontmatter|generated|source[-_ ]slug|title)\b", re.I
)
_NON_OBSERVATION = re.compile(
    r"\b(?:examples?|illustrative|hypothetical|synthetic|fixtures?|samples?|templates?|"
    r"unrelated|publication|published|filename|file name|URL|source[-_ ]slug|title|"
    r"build|built|maintenance|deployments?|releases?|images?)\b|https?://|\be\.g\.", re.I
)


def _time(stamp: str) -> datetime | None:
    try:
        value = datetime.fromisoformat(stamp.upper().replace("Z", "+00:00"))
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    except ValueError:
        return None


def _question_cutoff(question: str):
    # Presentation in a question must not bypass private source validation.
    text = re.sub(r'[`*]+', '', question)
    text = re.sub(r'(?<!\w)_{1,2}(' + _STAMP + r')_{1,2}(?!\w)', r'\1', text, flags=re.I)
    return _CUTOFF.search(text)


def temporal_negative_required(question: str) -> bool:
    """Select explicit ISO cutoff questions about receipts or backup custody."""
    match = _question_cutoff(question)
    return bool(match and _time(match['stamp']) and _TOPIC.search(question))


_SETEXT_HEADING = re.compile(r'^ {0,3}(?:=+|-+)[ \t]*$')
_SOURCE_FENCE = re.compile(
    r'^(?P<indent> {0,3})(?P<container>(?:(?:[-+*]|\d{1,9}[.)])[ \t]+)*)'
    r'(?P<marker>`{3,}|~{3,})(?P<info>.*)$'
)
_HTML_OPEN = re.compile(r'<!--|<(pre|code)\b[^>]*>', re.I)


def _source_body_text(text: str) -> str:
    """Return narrative source text without metadata, titles, or code examples.

    A closing fence uses the opening character and at least its length.
    An unclosed fence excludes the remaining body. This private helper does
    not establish capability or authority. Raw contrary checks stay separate.
    Metadata values stay excluded across blank lines. A root-level heading
    or document separator ends that context. This is not a general YAML,
    Markdown, or HTML parser.
    """
    lines = []
    first_content = True
    frontmatter = False
    fence_character = None
    fence_length = 0
    fence_min_indent = 0
    fence_max_indent = 3
    html_closer = None
    excluded_level = None
    quoted_paragraph = False
    metadata_continuation = False
    source_lines = text.splitlines()
    for index, line in enumerate(source_lines):
        if first_content:
            line = line.lstrip('\ufeff')
        stripped = line.strip()
        if first_content:
            first_value = stripped.lstrip('\ufeff')
            if not first_value:
                lines.append('')
                continue
            first_content = False
            if first_value == '---':
                frontmatter = True
                continue
        if frontmatter:
            if stripped in {'---', '...'}:
                frontmatter = False
            continue
        if fence_character:
            closing = re.fullmatch(r'(?P<indent>[ \t]*)(?P<marker>`+|~+)[ \t]*', line)
            if (closing and closing['marker'][0] == fence_character
                    and len(closing['marker']) >= fence_length
                    and fence_min_indent <= len(closing['indent'].expandtabs(4)) <= fence_max_indent):
                fence_character = None
                fence_length = 0
                lines.append('')
            continue
        cursor = 0
        html_line = bool(html_closer)
        while True:
            if html_closer:
                closing = re.search(html_closer, line[cursor:], re.I)
                if not closing:
                    break
                cursor += closing.end()
                html_closer = None
            opened = _HTML_OPEN.search(line, cursor)
            if not opened:
                break
            html_line = True
            cursor = opened.end()
            html_closer = r'-->' if opened[1] is None else r'</' + opened[1] + r'\s*>'
        if html_line:
            lines.append('')
            continue
        opening = _SOURCE_FENCE.match(line)
        if opening:
            fence_character = opening['marker'][0]
            fence_length = len(opening['marker'])
            fence_min_indent = len((opening['indent'] + opening['container']).expandtabs(4)) if opening['container'] else 0
            fence_max_indent = fence_min_indent + 3
            lines.append('')
            continue
        if metadata_continuation:
            if not stripped:
                lines.append('')
                continue
            if line in {'---', '...'}:
                metadata_continuation = False
                lines.append('')
                continue
            if not re.match(r'^#{1,6}\s+', line):
                continue
            metadata_continuation = False
        if _SETEXT_HEADING.fullmatch(line) and lines and lines[-1].strip():
            title_lines = []
            while lines and lines[-1].strip():
                title_lines.append(lines.pop())
            level = 1 if stripped.startswith('=') else 2
            if excluded_level is None or level <= excluded_level:
                excluded_level = level if _EXCLUDED_HEADING.search(' '.join(title_lines)) else None
            lines.append('')
            continue
        heading = re.match(r'^ {0,3}(#{1,6})\s+(.+)', line)
        if heading:
            level = len(heading[1])
            if excluded_level is not None and level > excluded_level:
                continue
            excluded_level = level if _EXCLUDED_HEADING.search(heading[2]) else None
            lines.append('')
            quoted_paragraph = False
            continue
        if not stripped:
            quoted_paragraph = False
            lines.append('')
            continue
        if stripped.startswith('>'):
            quoted_paragraph = True
        if _METADATA.match(line):
            metadata_continuation = True
            lines.append('')
            continue
        if excluded_level is not None or quoted_paragraph or line.startswith(('    ', '\t')):
            continue
        label = re.fullmatch(
            r'(?:For example|Examples?|Fixtures?|Templates?|Samples?|Source metadata)\s*[:,]?', stripped, re.I,
        )
        next_is_heading = index + 1 < len(source_lines) and _SETEXT_HEADING.fullmatch(source_lines[index + 1])
        if label and not next_is_heading:
            # A plain example label ends at the next explicit section.
            excluded_level = 7
            lines.append('')
            continue
        lines.append(line)
    return '\n'.join(lines)


def _observation_text(text: str) -> str:
    paragraphs = [part for part in re.split(r'\n\s*\n', _source_body_text(text))
                  if not _NON_OBSERVATION.search(part)]
    # An inline filename or quoted timestamp cannot supply the date.
    body = re.sub(r'`+[^`\n]*`+', '', '\n\n'.join(paragraphs))
    return re.sub(r'^\s*(?:[-+*]|\d+[.)])\s+', '', body, flags=re.M)


def _normalize_negation(text: str) -> str:
    for mark in ('\u2018', '\u2019', '\u02bc', '\uff07', '\u201b'):
        text = text.replace(mark, "'")
    # Expose contracted negatives before the early positive-answer return.
    return re.sub(r"\b([A-Za-z]+)n't\b", lambda match: match[1] + ' not', text, flags=re.I)


def _clauses(text: str, *, source: bool = False) -> list[str]:
    if source:
        text = _observation_text(text)
    # Inspect every draft line, including metadata-shaped negative claims.
    text = _normalize_negation(text.replace('`', '').replace('*', ''))
    return [part.strip() for part in re.split(
        r"[\n;]+|(?<=[.!?])\s+|\s+\b(?:and|but|while|however|whereas)\b\s+", text
    ) if part.strip()]


def _safety_contexts(text: str) -> list[str]:
    # Keep conjunctions and wrapped lines until the sentence-level vetoes run.
    text = _normalize_negation(text.replace('`', '').replace('*', ''))
    return [re.sub(r'\s+', ' ', part).strip()
            for part in re.split(r'(?<=[.!?])\s+', text) if part.strip()]


def _affirmative_observation(clause: str) -> bool:
    return any(not _NEGATED_OBSERVATION.search(clause[:match.start()])
               for match in _OBSERVATION.finditer(clause))


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
    cutoff = _time(_question_cutoff(question)['stamp'])
    if any(_TOPIC.search(context) and _RESTRICTIVE_ABSENCE.search(context)
           for context in _safety_contexts(answer)):
        return TEMPORAL_NEGATIVE_REASON
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
        # Do not hide possible contrary receipts with the metadata filter.
        # A positive candidate can require abstention without proving delivery.
        for context in _safety_contexts(text):
            if _TOPIC.search(context) and _affirmative_observation(context) and any(
                (when := _time(match['stamp'])) and when > cutoff
                for match in _CANDIDATE_DATE.finditer(context)
            ):
                return TEMPORAL_NEGATIVE_REASON
        observations = []
        for clause in _clauses(text, source=True):
            if not _TOPIC.search(clause) or _QUEUE.search(clause):
                continue
            dates = [when for match in _DATE.finditer(clause) if (when := _time(match['stamp']))]
            if not dates or not (_NEGATIVE.search(clause) or _OBSERVATION.search(clause)):
                continue
            observations.append(clause)
            if any(when > cutoff for when in dates) and _affirmative_observation(clause):
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
