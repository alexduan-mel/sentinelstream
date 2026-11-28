"""Risk factors (Item 1A) and MD&A (Item 7) from a 10-K's primary document, as numbered paragraphs.

A 10-K is one HTML document (inline XBRL since 2019). Its text is read into **blocks** in reading
order: a paragraph-like element (``p``, ``div``, ``li``, a heading) is one block, and a table row is
one block holding its cells. A table with any row of several cells (other than a bullet and its text)
holds figures, and none of its rows is prose. Hidden content is skipped: the inline-XBRL header (``ix:header``) and
anything styled ``display:none``.

**Finding a section.** A heading is a short block that starts with "Item" and a number, such as
"Item 1A. Risk Factors", alone or as a table row's cells. A section runs from its heading to the next
heading of a different item. The table of contents names every item too, so each candidate heading
is measured and the one with the most text wins; a section shorter than ``MIN_SECTION_CHARS`` counts
as not found (a 10-K can also incorporate a section by reference to another document).

**Paragraphs** are the section's text blocks after its heading, numbered from 1 so a change can be
cited as accession number, section and paragraph. Rows of other tables count as text: bullets (a "•"
cell then text) and paragraphs laid out in one-cell tables. Figure rows are left out and counted. So are
page furniture: page numbers, "Table of Contents" links, and short lines repeated on many pages.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field, replace
from html.parser import HTMLParser

SECTIONS: dict[str, str] = {"1a": "Risk factors", "7": "Management's discussion and analysis"}
MIN_SECTION_CHARS = 2_000
HEADING_MAX_CHARS = 200
REPEATED_MAX_CHARS = 100
REPEATED_MIN_COUNT = 3
ANCHOR_LOOKBACK = 4

_SKIP_TAGS = frozenset({"script", "style", "head", "title", "ix:header"})
_VOID_TAGS = frozenset({"br", "img", "hr", "meta", "link", "input", "col", "area", "base", "wbr"})
_BLOCK_TAGS = frozenset({"p", "div", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "center", "blockquote"})
_HIDDEN = re.compile(r"display\s*:\s*none", re.I)
_SPACE = re.compile(r"\s+")
_ITEM = re.compile(r"^item\s*(\d{1,2}[a-c]?)\b", re.I)
_BULLETS = frozenset({"•", "●", "◦", "▪", "■", "·", "-", "–", "—", "o"})
_PAGE_FURNITURE = re.compile(r"^(?:page\s*)?(?:\d{1,3}|[ivxlc]{1,6})\.?$|^table of contents$", re.I)
_SENTENCE_END = re.compile(r"[.:;?!)\"”’]$")
_BULLET_GLUED = re.compile(r"^([•●◦▪■·\u2022])(?=\S)")


@dataclass(frozen=True)
class Block:
    text: str
    # A table row's non-empty cells; None for a paragraph-like element.
    cells: tuple[str, ...] | None = None
    # The last element id seen before the block started: the target of the 10-K's own links to it.
    anchor: str | None = None
    # A row of a table holding figures: any of its rows has several cells and is not a bullet.
    in_figures: bool = False

    @property
    def is_prose(self) -> bool:
        return self.cells is None or not self.in_figures


@dataclass(frozen=True)
class Section:
    item: str
    heading: str
    # The heading's element id, so a link can open the document at the section.
    anchor: str | None = None
    paragraphs: list[str] = field(default_factory=list)
    tables_skipped: int = 0


def _clean(text: str) -> str:
    return _SPACE.sub(" ", text.replace("\xa0", " ").replace("​", "")).strip()


class _BlockReader(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[Block] = []
        self._text: list[str] = []
        self._stack: list[str] = []
        self._hidden_at: int | None = None  # stack depth of the element that hides its subtree
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._anchor: str | None = None
        self._row_anchor: str | None = None
        self.links: set[str] = set()  # in-document link targets (href="#...")
        self._tables: list[int] = []  # for each open table, the index of its first block

    # A block boundary inside a table cell is only a space: the cell stays one piece of text.
    def _boundary(self) -> None:
        if self._cell is not None:
            self._cell.append(" ")
        else:
            self._flush()

    def _flush(self) -> None:
        text = _clean("".join(self._text))
        self._text = []
        if text:
            self.blocks.append(Block(text, anchor=self._anchor))
            self._anchor = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _VOID_TAGS:
            if tag == "br" and self._hidden_at is None:
                self._boundary()
            return
        self._stack.append(tag)
        if self._hidden_at is None and (
            tag in _SKIP_TAGS or any(name == "style" and value and _HIDDEN.search(value) for name, value in attrs)
        ):
            self._hidden_at = len(self._stack)
        if self._hidden_at is not None:
            return
        # Close the block before this element first, so this element's id is not given to it.
        if tag == "table":
            self._flush()
            self._tables.append(len(self.blocks))
        elif tag == "tr":
            self._flush()
            self._row = []
        elif tag in ("td", "th"):
            self._cell = []
        elif tag in _BLOCK_TAGS:
            self._boundary()
        for name, value in attrs:
            if name in ("id", "name") and value and (name == "id" or tag == "a") and not self._text_started():
                self._anchor = value
            elif name == "href" and value and value.startswith("#"):
                self.links.add(value[1:])
        if tag == "tr":
            self._row_anchor = self._anchor

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "br" and self._hidden_at is None:
            self._boundary()

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID_TAGS or tag not in self._stack:
            return
        while self._stack:
            closed = self._stack.pop()
            hidden = self._hidden_at is not None
            if hidden and len(self._stack) < self._hidden_at:
                self._hidden_at = None
            if not hidden:
                self._close(closed)
            if closed == tag:
                break

    def _close(self, tag: str) -> None:
        if tag in ("td", "th"):
            if self._row is not None and self._cell is not None:
                cell = _clean("".join(self._cell))
                if cell:
                    self._row.append(cell)
            self._cell = None
        elif tag == "tr":
            if self._row:
                self.blocks.append(Block(" ".join(self._row), tuple(self._row), self._row_anchor or self._anchor))
                self._anchor = None
            self._row = None
        elif tag == "table":
            self._boundary()
            if self._tables:
                self._mark_figures(self._tables.pop())
        elif tag in _BLOCK_TAGS:
            self._boundary()

    def _mark_figures(self, first: int) -> None:
        """Rows of a table of figures are not prose, including one-cell rows such as "Year Ended"."""
        rows = [i for i in range(first, len(self.blocks)) if self.blocks[i].cells is not None]
        if any(len(cells) > 1 and cells[0] not in _BULLETS for cells in (self.blocks[i].cells for i in rows)):
            for i in rows:
                self.blocks[i] = replace(self.blocks[i], in_figures=True)

    def _text_started(self) -> bool:
        """Whether the block being read already has text: an id inside it does not mark its start."""
        return any(part.strip() for part in (self._cell if self._cell is not None else self._text))

    def handle_data(self, data: str) -> None:
        if self._hidden_at is not None:
            return
        (self._cell if self._cell is not None else self._text).append(data)

    def close(self) -> None:
        super().close()
        self._flush()


def read_blocks(html: str) -> list[Block]:
    reader = _BlockReader()
    reader.feed(html)
    reader.close()
    return reader.blocks


def _join_page_breaks(paragraphs: list[str]) -> list[str]:
    """A paragraph cut by a page break continues in lower case after text with no closing punctuation."""
    joined: list[str] = []
    for text in paragraphs:
        if joined and text[:1].islower() and not _SENTENCE_END.search(joined[-1]):
            joined[-1] = f"{joined[-1]} {text}"
        else:
            joined.append(_BULLET_GLUED.sub(r"\1 ", text))
    return joined


def _item(block: Block) -> str | None:
    if len(block.text) > HEADING_MAX_CHARS:
        return None
    match = _ITEM.match(block.text)
    return match.group(1).lower() if match else None


def _furniture(blocks: list[Block]) -> set[str]:
    """Short lines repeated on many pages (running headers and footers)."""
    counts = Counter(b.text for b in blocks if len(b.text) <= REPEATED_MAX_CHARS)
    return {text for text, count in counts.items() if count >= REPEATED_MIN_COUNT}


def _is_furniture(block: Block, furniture: set[str]) -> bool:
    return bool(_PAGE_FURNITURE.match(block.text)) or block.text in furniture


def find_section(blocks: list[Block], item: str) -> Section | None:
    """The section for ``item`` ("1a", "7"): the candidate heading followed by the most text."""
    headings = [(index, _item(block)) for index, block in enumerate(blocks)]
    headings = [(index, number) for index, number in headings if number is not None]
    best: tuple[int, int, int] | None = None  # (chars, start, end)
    for position, (start, number) in enumerate(headings):
        if number != item:
            continue
        end = next((i for i, n in headings[position + 1 :] if n != item), len(blocks))
        chars = sum(len(b.text) for b in blocks[start + 1 : end] if b.is_prose)
        if best is None or chars > best[0]:
            best = (chars, start, end)
    if best is None or best[0] < MIN_SECTION_CHARS:
        return None
    _, start, end = best
    # A running page header ("Item 1A") can sit just above the heading itself: use the last one.
    anchor = blocks[start].anchor
    while start + 1 < end and _item(blocks[start + 1]) == item:
        start += 1
        anchor = anchor or blocks[start].anchor
    heading = blocks[start]
    anchor = heading.anchor or anchor
    # A heading that starts a page: the 10-K's link points at the page top, above its header line.
    furniture = _furniture(blocks)
    back = start - 1
    while anchor is None and back >= max(0, start - ANCHOR_LOOKBACK) and _is_furniture(blocks[back], furniture):
        anchor = blocks[back].anchor
        back -= 1
    # A heading split over two blocks ("Item 1A." then "Risk Factors") takes the title too.
    title = blocks[start + 1].text if start + 1 < end and len(heading.text) <= 12 else ""
    paragraphs: list[str] = []
    tables = 0
    for block in blocks[start + 1 : end]:
        if not block.is_prose:
            tables += 1
        elif not (_is_furniture(block, furniture) or _item(block) == item):
            paragraphs.append(block.text)
    if title and paragraphs and paragraphs[0] == title:
        paragraphs = paragraphs[1:]
    return Section(
        item=item,
        heading=f"{heading.text} {title}".strip(),
        anchor=anchor,
        paragraphs=_join_page_breaks(paragraphs),
        tables_skipped=tables,
    )


def extract_sections(html: str) -> dict[str, Section | None]:
    blocks = read_blocks(html)
    return {item: find_section(blocks, item) for item in SECTIONS}
