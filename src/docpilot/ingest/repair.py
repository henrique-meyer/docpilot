"""Join words that PDF line breaks split apart.

IRS publications end a line in the middle of a word and keep the hyphen, and
they insert a space after a leading T or Y. A new ingest stores the joined
words. The same repair runs on a citation that was indexed before this, so
those snippets read correctly without a new ingest.
"""

import re

# A line that ends in one of these is a compound, not a word broken in half.
_KEEP_HYPHEN = frozenset({"non", "self", "anti", "co", "cross"})

_FLAT_HYPHEN = re.compile(r"([A-Za-z]+)- ([a-z]+)")
_SPACE_BEFORE_HYPHEN = re.compile(r"([A-Za-z0-9]) -([A-Za-z0-9])")
_SPLIT_TY = re.compile(r"(?<![A-Za-z])([TY]) ([a-z]+)")


def repair_passage(text: str) -> str:
    """Return text with line-break splits joined."""
    normalized = (
        text.replace("\u00ad", "-")
        .replace("\u00a0", " ")
        .replace("\u2007", " ")
        .replace("\u2009", " ")
    )
    if "\n" in normalized or "\r" in normalized:
        lines = [line.strip() for line in normalized.splitlines() if line.strip()]
        return _repair_inline(_join_lines(lines))
    return _repair_inline(normalized)


def _join_lines(lines: list[str]) -> str:
    if not lines:
        return ""
    joined = [lines[0]]
    for line in lines[1:]:
        previous = joined[-1]
        if previous.endswith("-") and line[:1].isalnum():
            piece = _hyphen_piece(previous)
            if line[0].islower() and piece.lower() not in _KEEP_HYPHEN:
                joined[-1] = previous[:-1] + line
            else:
                joined[-1] = previous + line
        else:
            joined.append(line)
    return " ".join(joined)


def _hyphen_piece(text: str) -> str:
    end = len(text) - 1
    start = end - 1
    while start >= 0 and text[start].isalpha():
        start -= 1
    return text[start + 1 : end]


def _repair_inline(text: str) -> str:
    return _SPLIT_TY.sub(
        r"\1\2",
        _SPACE_BEFORE_HYPHEN.sub(r"\1-\2", _FLAT_HYPHEN.sub(_flat_hyphen, text)),
    )


def _flat_hyphen(match: re.Match[str]) -> str:
    left = match.group(1)
    right = match.group(2)
    if left.lower() in _KEEP_HYPHEN:
        return f"{left}-{right}"
    return left + right
