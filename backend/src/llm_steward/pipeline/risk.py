"""Rule-based risk (design §5): high-risk keywords from the namespace config, and built-in PII
detection. Rules run first; the LLM's label (P3b) can only raise the result, never lower it.

PII found in knowledge is handled by the namespace's `memory.pii.action`: `redact` (default)
replaces each value with `[REDACTED:<kind>]`, `block` refuses the write, `allow` keeps it. Any PII
makes the entry high risk, so a person reviews it before it is shared.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from llm_steward.errors import Unprocessable
from llm_steward.models.enums import RiskLevel

PiiAction = Literal["block", "redact", "allow"]

_SECRET = re.compile(
    r"sk-[A-Za-z0-9_-]{20,}"  # OpenAI-style keys
    r"|(?:stw|lrk)_[A-Za-z0-9]+_[A-Za-z0-9_-]{16,}"  # llm-steward / lite-rag keys
    r"|AKIA[0-9A-Z]{16}"  # AWS access key ids
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|(?i:\b(?:api[_-]?key|secret|token|password)\s*[:=]\s*\S{8,})"
)
# Bounded and anchored: an unbounded local part made a long run of letters with no '@' quadratic
# (every start re-scanned the rest of the run), on the event loop.
_EMAIL = re.compile(
    r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}"
    r"(?:\.[A-Za-z0-9-]{1,63}){0,8}\.[A-Za-z]{2,24}"
)
_CN_ID = re.compile(r"(?<![0-9A-Za-z])\d{17}[\dXx](?![0-9A-Za-z])")
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
# A mainland mobile number, also as written by people: "+86 138 0013 8000", "138-0013-8000".
_PHONE = re.compile(r"(?<![\d+])(?:\+?86[ -]?)?1[3-9]\d(?:[ -]?\d{4}){2}(?!\d)")
_ID_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)


def _cn_id_ok(value: str) -> bool:
    total = sum(int(d) * w for d, w in zip(value[:17], _ID_WEIGHTS, strict=True))
    return "10X98765432"[total % 11] == value[17].upper()


def _luhn_ok(value: str) -> bool:
    digits = [int(d) for d in value if d.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


# Most specific first: a span claimed by one kind is not matched again by a later one.
_DETECTORS: tuple[tuple[str, re.Pattern[str], Any], ...] = (
    ("secret", _SECRET, None),
    ("email", _EMAIL, None),
    ("cn_id", _CN_ID, _cn_id_ok),
    ("bank_card", _CARD, _luhn_ok),
    ("phone", _PHONE, None),
)


def find_pii(text: str) -> list[tuple[str, tuple[int, int]]]:
    """(kind, span) of each PII value in `text`, in text order."""
    found: list[tuple[str, tuple[int, int]]] = []
    for kind, pattern, valid in _DETECTORS:
        for m in pattern.finditer(text):
            span = m.span()
            if valid is not None and not valid(m.group()):
                continue
            if any(a < span[1] and span[0] < b for _, (a, b) in found):
                continue
            found.append((kind, span))
    return sorted(found, key=lambda f: f[1])


def _redact(text: str, found: list[tuple[str, tuple[int, int]]]) -> str:
    """`found` in text order; the text is rebuilt once, not once per value."""
    parts, last = [], 0
    for kind, (a, b) in found:
        parts += [text[last:a], f"[REDACTED:{kind}]"]
        last = b
    return "".join([*parts, text[last:]])


def redact_pii(text: str) -> str:
    """`text` with every PII value replaced, whatever the namespace's policy: for what is written
    about knowledge (reasons, notes), which is never itself the knowledge being shared."""
    return _redact(text, find_pii(text))


@dataclass(frozen=True)
class RiskPolicy:
    high_risk_keywords: tuple[str, ...] = ()
    pii_action: PiiAction = "redact"

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "RiskPolicy":
        """From a namespace's config (design §8): `kb.high_risk_keywords`, `memory.pii.action`."""
        keywords = tuple(config.get("kb", {}).get("high_risk_keywords", ()))
        action = config.get("memory", {}).get("pii", {}).get("action", "redact")
        return cls(keywords, action)


@dataclass(frozen=True)
class Assessment:
    level: RiskLevel
    title: str  # redacted when the policy says so
    content: str
    reasons: list[str] = field(default_factory=list)  # never repeat a PII value
    tags: tuple[str, ...] = ()


def assess(title: str, content: str, policy: RiskPolicy, tags: tuple[str, ...] = ()) -> Assessment:
    """Title, content and tags: everything of an entry that is stored and shared."""
    reasons: list[str] = []
    text = "\n".join((title, content, *tags)).casefold()
    reasons += [f"keyword: {k}" for k in policy.high_risk_keywords if k.casefold() in text]
    in_title, in_content = find_pii(title), find_pii(content)
    in_tags = [find_pii(tag) for tag in tags]
    found = in_title + in_content + [f for tag in in_tags for f in tag]
    kinds = list(dict.fromkeys(kind for kind, _ in found))
    if kinds and policy.pii_action == "block":
        raise Unprocessable(f"personal data found ({', '.join(kinds)}); this namespace blocks it")
    reasons += [f"pii: {kind}" for kind in kinds]
    if policy.pii_action == "redact":
        title, content = _redact(title, in_title), _redact(content, in_content)
        tags = tuple(_redact(tag, f) for tag, f in zip(tags, in_tags, strict=True))
    level = RiskLevel.HIGH if reasons else RiskLevel.LOW
    return Assessment(level, title, content, reasons, tags)
