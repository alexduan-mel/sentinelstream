"""Year-over-year changes between two 10-Ks' paragraphs in one section (ADR 0023).

The paragraphs of last year's section and this year's are lined up in three passes:

1. **In place.** ``difflib.SequenceMatcher`` over the two lists finds the longest runs of identical
   paragraphs in the same order. Those are unchanged.
2. **Moved.** A paragraph left over on both sides with identical text was moved, not changed. It is
   counted, not reported.
3. **Edited.** The remaining paragraphs are paired by word-level similarity, best pairs first, when
   at least ``MIN_SIMILARITY`` of their words line up. Those are changed; whatever is left is added
   (this year only) or removed (last year only).

Text is compared after folding case, quotes and whitespace, so typography alone is no change. A
change is **numbers only** when the two texts are the same once every number and month is masked:
MD&A restates figures and dates every year, and the page can show those apart from real rewording.

Changes come out in this year's reading order; a removed paragraph sits after the paragraph that
preceded it last year. Each carries the paragraph numbers on both sides, which with the two
accession numbers and the item make the citation.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Literal

MIN_SIMILARITY = 0.6
# Raise when the comparison changes, so stored diffs are computed again.
COMPARER_VERSION = 1

_QUOTES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-"})
_SPACE = re.compile(r"\s+")
_WORD = re.compile(r"[a-z0-9$%']+|[^\sa-z0-9]")
_NUMBER = re.compile(
    r"\$?\d[\d,]*(?:\.\d+)?%?|\b(?:january|february|march|april|may|june|july|august|september|october"
    r"|november|december)\b"
)

Kind = Literal["added", "removed", "changed"]


@dataclass(frozen=True)
class Change:
    kind: Kind
    # Paragraph numbers from 1, as stored: old in last year's section, new in this year's.
    old_no: int | None
    new_no: int | None
    # Share of words that line up, for a changed paragraph.
    similarity: float | None = None
    numbers_only: bool = False


@dataclass(frozen=True)
class Comparison:
    changes: list[Change]
    unchanged: int
    moved: int

    def count(self, kind: Kind) -> int:
        return sum(1 for change in self.changes if change.kind == kind)


def fold(text: str) -> str:
    return _SPACE.sub(" ", text.translate(_QUOTES).casefold()).strip()


def _words(text: str) -> list[str]:
    return _WORD.findall(text)


def _masked(text: str) -> str:
    return _NUMBER.sub("#", text)


def similarity(old: str, new: str) -> float:
    return SequenceMatcher(None, _words(fold(old)), _words(fold(new)), autojunk=False).ratio()


def compare(old: list[str], new: list[str]) -> Comparison:
    old_folded = [fold(text) for text in old]
    new_folded = [fold(text) for text in new]

    # 1. Identical runs in order.
    match: dict[int, int] = {}  # old index -> new index
    matcher = SequenceMatcher(None, old_folded, new_folded, autojunk=False)
    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            match[block.a + offset] = block.b + offset
    unchanged = len(match)

    # 2. Identical text elsewhere: moved.
    left_new: dict[str, list[int]] = defaultdict(list)
    taken_new = set(match.values())
    for j, text in enumerate(new_folded):
        if j not in taken_new:
            left_new[text].append(j)
    moved = 0
    for i, text in enumerate(old_folded):
        if i not in match and left_new.get(text):
            match[i] = left_new[text].pop(0)
            moved += 1

    # 3. Similar text: changed. Best pairs first; nearer positions break ties.
    old_left = [i for i in range(len(old)) if i not in match]
    taken_new = set(match.values())
    new_left = [j for j in range(len(new)) if j not in taken_new]
    old_words = {i: _words(old_folded[i]) for i in old_left}
    new_words = {j: _words(new_folded[j]) for j in new_left}
    candidates: list[tuple[float, int, int, int]] = []
    for i in old_left:
        for j in new_left:
            matcher = SequenceMatcher(None, old_words[i], new_words[j], autojunk=False)
            if matcher.real_quick_ratio() < MIN_SIMILARITY or matcher.quick_ratio() < MIN_SIMILARITY:
                continue
            ratio = matcher.ratio()
            if ratio >= MIN_SIMILARITY:
                candidates.append((-ratio, abs(i / max(len(old), 1) - j / max(len(new), 1)) * 1000, i, j))
    edited: dict[int, tuple[int, float]] = {}
    used_new: set[int] = set()
    for negative_ratio, _, i, j in sorted(candidates):
        if i in edited or j in used_new:
            continue
        edited[i] = (j, -negative_ratio)
        used_new.add(j)

    changes: list[tuple[float, Change]] = []
    for i, (j, ratio) in edited.items():
        numbers_only = _masked(old_folded[i]) == _masked(new_folded[j])
        changes.append((j, Change("changed", i + 1, j + 1, round(ratio, 3), numbers_only)))
    paired_new = taken_new | used_new
    for j in range(len(new)):
        if j not in paired_new:
            changes.append((j, Change("added", None, j + 1)))
    # A removed paragraph follows, in this year's order, whatever preceded it last year.
    position = -1.0
    for i in range(len(old)):
        if i in match:
            position = match[i]
        elif i in edited:
            position = edited[i][0]
        else:
            position += 0.001
            changes.append((position, Change("removed", i + 1, None)))
    changes.sort(key=lambda pair: pair[0])
    return Comparison(changes=[change for _, change in changes], unchanged=unchanged, moved=moved)


# --- reading a change ------------------------------------------------------------------------------

_TOKEN = re.compile(r"\S+\s*")
_HEADING_MAX_CHARS = 120
_SENTENCE_END = re.compile(r"[.;:!?,)”\"]$")
_BULLET_START = re.compile(r"^[•●◦▪■·•]")

Op = Literal["equal", "delete", "insert"]


def word_changes(old: str, new: str) -> list[tuple[Op, str]]:
    """``new`` against ``old`` word by word: runs of kept, deleted and inserted words, with their spacing.

    Words compare after folding (as ``compare`` does), so a change of quote style is not marked.
    """
    old_tokens, new_tokens = _TOKEN.findall(old), _TOKEN.findall(new)
    matcher = SequenceMatcher(None, [fold(t) for t in old_tokens], [fold(t) for t in new_tokens], autojunk=False)
    segments: list[tuple[Op, str]] = []

    def add(op: Op, tokens: list[str]) -> None:
        text = "".join(tokens)
        if not text:
            return
        if segments and segments[-1][0] == op:
            segments[-1] = (op, segments[-1][1] + text)
        else:
            segments.append((op, text))

    for tag, a1, a2, b1, b2 in matcher.get_opcodes():
        if tag == "equal":
            add("equal", new_tokens[b1:b2])
        else:
            add("delete", old_tokens[a1:a2])
            add("insert", new_tokens[b1:b2])
    return segments


def is_heading(text: str) -> bool:
    """A short line that is not a sentence or a bullet: a section's own heading, such as "Overview"."""
    text = text.strip()
    return 0 < len(text) <= _HEADING_MAX_CHARS and not _SENTENCE_END.search(text) and not _BULLET_START.match(text)


def heading_before(paragraphs: list[str], number: int) -> int | None:
    """The number (from 1) of the nearest heading at or before paragraph ``number``, or None."""
    for index in range(min(number, len(paragraphs)) - 1, -1, -1):
        if is_heading(paragraphs[index]):
            return index + 1
    return None
