"""Full-text tokens for the native provider: jieba segmentation, shared by FTS5 and tsvector."""

import logging
import re
from functools import cache
from typing import Any

_WORD = re.compile(r"\w+")


@cache
def _jieba() -> Any:
    import jieba

    jieba.setLogLevel(logging.WARNING)
    return jieba


def tokens(text: str) -> list[str]:
    """Lower-cased word tokens; anything that is not a word (punctuation, operators) is dropped,
    so the result is safe to splice into FTS5 and tsquery expressions."""
    return [t.lower() for t in _jieba().cut_for_search(text) if _WORD.fullmatch(t)]
