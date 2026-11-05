"""Block model, classification and block-stream extraction (plan 6.0.2)."""

from __future__ import annotations

import html as html_lib
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from lxml import etree
from lxml import html as lxml_html

from .inline import (
    _attr_string,
    canonical_element,
    extract_nodes,
    local_name,
    plain_text_from_nodes,
    reconstruct_nodes,
)
from .normalize_source import normalize_source


class BlockKind(StrEnum):
    HEADING = "heading"
    PROSE = "prose"
    QUOTE = "quote"
    VERSE = "verse"
    FOOTNOTE = "footnote"
    CAPTION = "caption"
    TABLE = "table"
    IMAGE = "image"
    PRE = "pre"
    BREAK = "break"
    NAV = "nav"


TRANSLATABLE: frozenset[BlockKind] = frozenset(
    {
        BlockKind.HEADING,
        BlockKind.PROSE,
        BlockKind.QUOTE,
        BlockKind.VERSE,
        BlockKind.FOOTNOTE,
        BlockKind.CAPTION,
        BlockKind.TABLE,
    }
)
SKIPPED: frozenset[BlockKind] = frozenset(
    {BlockKind.IMAGE, BlockKind.PRE, BlockKind.BREAK, BlockKind.NAV}
)
FRONT_TYPES = {
    "titlepage",
    "copyright-page",
    "dedication",
    "epigraph",
    "preface",
    "foreword",
    "halftitlepage",
}
BACK_TYPES = {"backmatter", "index", "appendix", "glossary", "bibliography", "colophon"}

_HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_CONTAINERS = {
    "body",
    "div",
    "section",
    "article",
    "main",
    "header",
    "footer",
    "ol",
    "ul",
    "dl",
    "figure",
}
_BLOCKS = _HEADINGS | {
    "p",
    "blockquote",
    "pre",
    "hr",
    "table",
    "figcaption",
    "aside",
    "nav",
    "li",
    "dt",
    "dd",
    "img",
    "svg",
}
_SKIP_TAGS = {"script", "style", "noscript", "template", "head"}


def epub_type(element: etree._Element) -> str:
    for key, value in element.attrib.items():
        raw = str(key)
        if raw.endswith("}type") or raw in {"epub:type", "epub:type".upper()}:
            return str(value)
    return ""


def class_tokens(element: etree._Element) -> set[str]:
    return {token.lower() for token in str(element.get("class", "")).split()}


def classify(element: etree._Element, *, parent_kind: BlockKind | None = None) -> BlockKind:
    tag = local_name(element.tag)
    classes = class_tokens(element)
    etype = epub_type(element)

    if tag == "nav" or etype == "toc" or "toc" in classes:
        return BlockKind.NAV
    if tag in _HEADINGS:
        return BlockKind.HEADING
    if tag in {"pre", "code"} or "code" in classes:
        return BlockKind.PRE
    if tag == "hr" or "break" in classes:
        return BlockKind.BREAK
    if tag in {"img", "svg"}:
        return BlockKind.IMAGE
    if tag == "table":
        return BlockKind.TABLE
    if tag == "figcaption":
        return BlockKind.CAPTION
    if tag in {"aside"} or etype == "footnote" or "footnote" in classes:
        return BlockKind.FOOTNOTE
    if tag == "blockquote" or {"quote", "epigraph"} & classes:
        return BlockKind.QUOTE
    if "verse" in classes or "poem" in classes:
        return BlockKind.VERSE
    if tag == "p":
        breaks = sum(1 for child in element if local_name(child.tag) == "br")
        if breaks >= 2:
            return BlockKind.VERSE
        return BlockKind.PROSE
    if parent_kind == BlockKind.VERSE:
        return BlockKind.VERSE
    return BlockKind.PROSE


@dataclass
class Block:
    kind: BlockKind
    tag: str
    attrs: dict[str, str]
    html: str
    nodes: list[dict[str, Any]]
    text: str
    skip: bool = False
    meta: dict[str, Any] = field(default_factory=dict)

    def rebuild(self) -> str:
        """Reconstruct the block's outer HTML from its node list."""
        inner = reconstruct_nodes(self.nodes)
        if self.tag in {"br", "img", "hr", "wbr"}:
            return f"<{self.tag}{_attr_string(self.attrs)}/>"
        return f"<{self.tag}{_attr_string(self.attrs)}>{inner}</{self.tag}>"

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "tag": self.tag,
            "attrs": self.attrs,
            "html": self.html,
            "nodes": self.nodes,
            "text": self.text,
            "skip": self.skip,
            "meta": self.meta,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Block:
        return cls(
            kind=BlockKind(data["kind"]),
            tag=data["tag"],
            attrs=dict(data.get("attrs", {})),
            html=data["html"],
            nodes=list(data.get("nodes", [])),
            text=data["text"],
            skip=bool(data.get("skip", False)),
            meta=dict(data.get("meta", {})),
        )


def _simple_attrs(element: etree._Element) -> dict[str, str]:
    return {
        local_name(key).split("}")[-1]: value
        for key, value in element.attrib.items()
        if not local_name(key).startswith("xmlns")
    }


def table_text(element: etree._Element) -> str:
    rows = []
    for row in element.iter():
        if local_name(row.tag) != "tr":
            continue
        cells = []
        for cell in row:
            if local_name(cell.tag) in {"td", "th"}:
                cells.append(normalize_source("".join(cell.itertext())).strip())
        if any(cells):
            rows.append(" | ".join(cells))
    return "\n".join(rows)


def make_block(element: etree._Element, kind: BlockKind | None = None) -> Block:
    resolved = kind or classify(element)
    nodes = extract_nodes(element)
    tag = local_name(element.tag)
    if resolved is BlockKind.TABLE:
        text = table_text(element)
    else:
        text = plain_text_from_nodes(nodes, verse=resolved is BlockKind.VERSE)
    text = normalize_source(text)
    attrs = _simple_attrs(element)
    return Block(
        kind=resolved,
        tag=tag,
        attrs=attrs,
        html=canonical_element(element),
        nodes=nodes,
        text=text,
        skip=resolved in SKIPPED,
        meta={"epub_type": epub_type(element), "classes": sorted(class_tokens(element))},
    )


def _has_block_children(element: etree._Element) -> bool:
    return any(local_name(child.tag) in _BLOCKS for child in element)


def _loose_text_block(text: str) -> Block:
    fragment = lxml_html.fragment_fromstring(
        f"<p>{html_lib.escape(text, quote=False)}</p>", create_parent="div"
    )
    return make_block(fragment[0], BlockKind.PROSE)


def iter_blocks(root: etree._Element) -> list[Block]:
    """Flatten a document body into an ordered block stream."""
    blocks: list[Block] = []

    def visit(element: etree._Element) -> None:
        tag = local_name(element.tag)
        if not tag or tag in _SKIP_TAGS:
            return
        if tag in _CONTAINERS:
            if _has_block_children(element):
                if (element.text or "").strip():
                    blocks.append(_loose_text_block(element.text or ""))
                for child in element:
                    visit(child)
                    if (child.tail or "").strip():
                        blocks.append(_loose_text_block(child.tail or ""))
            elif (element.text or "").strip() or len(element):
                blocks.append(make_block(element, BlockKind.PROSE))
            return
        if tag in _BLOCKS:
            blocks.append(make_block(element))
            return
        if (element.text or "").strip():
            blocks.append(make_block(element, BlockKind.PROSE))

    visit(root)
    return [block for block in blocks if block.text or block.kind in {BlockKind.IMAGE, BlockKind.BREAK}]
