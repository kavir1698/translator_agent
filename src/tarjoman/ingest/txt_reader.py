"""TXT / Markdown reader."""

from __future__ import annotations

import html
import re
from pathlib import Path

from lxml import html as lxml_html

from .blocks import BlockKind, iter_blocks
from .types import Document, IngestResult

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_HR = re.compile(r"^\s*(\*\s*\*\s*\*|-\s*-\s*-|___+|\*\*\*+)\s*$")
_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def md_inline(text: str) -> str:
    value = html.escape(text, quote=False)
    value = re.sub(r"`([^`]+)`", r"<code>\1</code>", value)
    value = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", value)
    value = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", value)

    def link(match: re.Match[str]) -> str:
        label, url = match.group(1), match.group(2)
        if not _safe_url(url):
            return label
        return f'<a href="{html.escape(url, quote=True)}">{label}</a>'

    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link, value)


def _safe_url(url: str) -> bool:
    lowered = url.strip().lower()
    if lowered.startswith(("http://", "https://", "mailto:", "/", "#", "./", "../")):
        return True
    return ":" not in lowered


def _markdown_fragments(text: str) -> list[str]:
    lines = text.splitlines()
    fragments: list[str] = []
    paragraph: list[str] = []
    index = 0

    def flush() -> None:
        if paragraph:
            fragments.append(f"<p>{md_inline(' '.join(paragraph))}</p>")
            paragraph.clear()

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if stripped.startswith("```"):
            flush()
            index += 1
            code: list[str] = []
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code.append(lines[index])
                index += 1
            fragments.append(f"<pre>{html.escape(chr(10).join(code))}</pre>")
            index += 1
            continue
        if not stripped:
            flush()
            index += 1
            continue
        heading = _HEADING.match(stripped)
        if heading:
            flush()
            level = len(heading.group(1))
            fragments.append(f"<h{level}>{md_inline(heading.group(2))}</h{level}>")
            index += 1
            continue
        if _HR.match(stripped):
            flush()
            fragments.append("<hr/>")
            index += 1
            continue
        if stripped.startswith(">"):
            flush()
            quote: list[str] = []
            while index < len(lines) and lines[index].strip().startswith(">"):
                quote.append(lines[index].strip()[1:].strip())
                index += 1
            fragments.append(f"<blockquote>{md_inline(' '.join(quote))}</blockquote>")
            continue
        if _BULLET.match(line):
            flush()
            item = _BULLET.sub("", line).strip()
            fragments.append(f"<p>{md_inline(item)}</p>")
            index += 1
            continue
        paragraph.append(stripped)
        index += 1
    flush()
    return fragments


def _plain_fragments(text: str) -> list[str]:
    fragments: list[str] = []
    for chunk in re.split(r"\n\s*\n", text):
        cleaned = " ".join(line.strip() for line in chunk.splitlines()).strip()
        if not cleaned:
            continue
        fragments.append(f"<p>{html.escape(cleaned, quote=False)}</p>")
    return fragments


def read_text(path: str | Path, *, markdown: bool = False) -> IngestResult:
    file = Path(path)
    raw = _decode(file.read_bytes())
    fragments = _markdown_fragments(raw) if markdown else _plain_fragments(raw)
    if fragments:
        wrapper = lxml_html.fragment_fromstring("".join(fragments), create_parent="div")
        blocks = iter_blocks(wrapper)
    else:
        blocks = []
    title = next((block.text for block in blocks if block.kind is BlockKind.HEADING), None)
    return IngestResult(
        source_format="md" if markdown else "txt",
        documents=[Document(id="doc001", blocks=blocks, title=title)],
        title=title or file.stem,
    )
