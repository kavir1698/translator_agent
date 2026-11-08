"""Best-effort text-layer PDF reader (pypdfium2); no OCR."""

from __future__ import annotations

import html
import statistics
from dataclasses import dataclass, field
from pathlib import Path

from lxml import html as lxml_html

from shahrzad.core.errors import PdfNoTextLayer

from .blocks import BlockKind, iter_blocks
from .types import Document, IngestResult


@dataclass
class Line:
    text: str
    left: float
    right: float
    top: float
    bottom: float
    height: float


@dataclass
class Page:
    width: float
    height: float
    lines: list[Line] = field(default_factory=list)
    chars: int = 0


def _cluster_lines(chars: list[tuple[str, tuple[float, float, float, float]]]) -> list[Line]:
    """Cluster by baseline (box bottom), which is stable across ascenders/descenders."""
    buckets: list[list[tuple[str, tuple[float, float, float, float]]]] = []
    references: list[float] = []
    ordered = sorted(
        (item for item in chars if item[0] not in "\r\n"),
        key=lambda item: (-item[1][1], item[1][0]),
    )
    for char, box in ordered:
        bottom = box[1]
        placed = False
        for index in range(len(buckets) - 1, max(-1, len(buckets) - 5), -1):
            if abs(references[index] - bottom) <= 3.5:
                buckets[index].append((char, box))
                placed = True
                break
        if not placed:
            buckets.append([(char, box)])
            references.append(bottom)

    lines: list[Line] = []
    for bucket in buckets:
        items = sorted(bucket, key=lambda item: item[1][0])
        text = "".join(item[0] for item in items)
        if not text.strip():
            continue
        lefts = [item[1][0] for item in items]
        rights = [item[1][2] for item in items]
        bottoms = [item[1][1] for item in items]
        tops = [item[1][3] for item in items]
        lines.append(
            Line(
                text=text.strip(),
                left=min(lefts),
                right=max(rights),
                top=max(tops),
                bottom=min(bottoms),
                height=max(tops) - min(bottoms),
            )
        )
    lines.sort(key=lambda line: (-round(line.top, 1), line.left))
    return lines


def _repeated_lines(pages: list[Page]) -> set[str]:
    total = len(pages)
    if total < 2:
        return set()
    counts: dict[str, int] = {}
    for page in pages:
        for line in page.lines:
            if line.top > page.height * 0.92 or line.bottom < page.height * 0.08:
                counts[line.text] = counts.get(line.text, 0) + 1
    return {text for text, count in counts.items() if count > total * 0.3}


def _page_paragraphs(page: Page, repeated: set[str]) -> tuple[list[str], int, int]:
    """Return HTML fragments, hyphen joins and removed repeated lines."""
    lines = [line for line in page.lines if line.text not in repeated]
    removed = len(page.lines) - len(lines)
    if not lines:
        return [], 0, removed
    heights = [line.height for line in lines if line.height > 0]
    median_height = statistics.median(heights) if heights else 10.0
    gaps = [
        lines[index - 1].bottom - lines[index].top
        for index in range(1, len(lines))
        if lines[index - 1].bottom > lines[index].top
    ]
    median_gap = statistics.median(gaps) if gaps else median_height * 0.2
    modal_left = statistics.median([line.left for line in lines])

    fragments: list[str] = []
    paragraph: list[str] = []
    joins = 0
    previous: Line | None = None
    for line in lines:
        is_heading = line.height > median_height * 1.18 and len(line.text) < 90
        if is_heading:
            if paragraph:
                fragments.append(f"<p>{html.escape(' '.join(paragraph), quote=False)}</p>")
                paragraph = []
            level = 1 if line.height > median_height * 1.45 else 2
            fragments.append(f"<h{level}>{html.escape(line.text, quote=False)}</h{level}>")
            previous = line
            continue
        new_paragraph = (
            previous is None
            or (previous.bottom - line.top) > max(median_gap * 2.2, median_height * 0.8)
            or (line.left - modal_left) > 9
        )
        if new_paragraph and paragraph:
            fragments.append(f"<p>{html.escape(' '.join(paragraph), quote=False)}</p>")
            paragraph = []
        elif paragraph and previous is not None and previous.text.endswith("-") and line.text[:1].islower():
            paragraph[-1] = paragraph[-1][:-1] + line.text
            joins += 1
            previous = line
            continue
        paragraph.append(line.text)
        previous = line
    if paragraph:
        fragments.append(f"<p>{html.escape(' '.join(paragraph), quote=False)}</p>")
    return fragments, joins, removed


def _quality(pages: list[Page], fragments: list[str], joins: int, removed: int, lines: int) -> float:
    text_pages = sum(1 for page in pages if page.chars >= 20)
    text_fraction = text_pages / max(1, len(pages))
    word_counts = [
        len(fragment.split())
        for fragment in fragments
        if fragment.startswith("<p")
    ]
    sane = sum(1 for words in word_counts if 20 <= words <= 180)
    sanity = sane / max(1, len(word_counts))
    join_penalty = min(1.0, joins / max(1, lines) * 10)
    removed_penalty = min(1.0, removed / max(1, lines) * 10)
    return round(
        0.4 * text_fraction + 0.3 * sanity + 0.2 * (1 - join_penalty) + 0.1 * (1 - removed_penalty),
        4,
    )


def read_pdf(path: str | Path) -> IngestResult:
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(str(path))
    pages: list[Page] = []
    try:
        for page in document:
            width, height = page.get_size()
            textpage = page.get_textpage()
            chars: list[tuple[str, tuple[float, float, float, float]]] = []
            count = textpage.count_chars()
            for index in range(count):
                char = textpage.get_text_range(index, 1)
                box = textpage.get_charbox(index)
                chars.append((char, (float(box[0]), float(box[1]), float(box[2]), float(box[3]))))
            lines = _cluster_lines(chars)
            pages.append(Page(width=width, height=height, lines=lines, chars=len(chars)))
            textpage.close()
            page.close()
    finally:
        document.close()

    total_chars = sum(page.chars for page in pages)
    text_pages = sum(1 for page in pages if page.chars >= 20)
    if total_chars < 20 or text_pages / max(1, len(pages)) < 0.8:
        raise PdfNoTextLayer()

    repeated = _repeated_lines(pages)
    fragments: list[str] = []
    joins = 0
    removed = 0
    for page in pages:
        page_fragments, page_joins, page_removed = _page_paragraphs(page, repeated)
        fragments.extend(page_fragments)
        joins += page_joins
        removed += page_removed

    quality = _quality(pages, fragments, joins, removed, sum(len(page.lines) for page in pages))
    wrapper = lxml_html.fragment_fromstring("".join(fragments) or "<p></p>", create_parent="div")
    blocks = iter_blocks(wrapper)
    title = next((block.text for block in blocks if block.kind is BlockKind.HEADING), None)
    return IngestResult(
        source_format="pdf",
        documents=[Document(id="doc001", blocks=blocks, title=title)],
        title=title or Path(path).stem,
        import_quality=quality,
        metadata={
            "pages": len(pages),
            "hyphen_joins": joins,
            "repeated_lines_removed": removed,
        },
    )
