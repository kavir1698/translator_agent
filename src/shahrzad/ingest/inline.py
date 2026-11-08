"""Inline markup <-> node list, canonical HTML, and sentinel rendering (plan 6.0.3)."""

from __future__ import annotations

import html
import re
from typing import Any
from xml.sax.saxutils import escape as _xml_escape

from lxml import etree
from lxml import html as lxml_html

PathStep = str

FORMAT_TAGS = {
    "em",
    "i",
    "strong",
    "b",
    "u",
    "s",
    "strike",
    "del",
    "ins",
    "small",
    "big",
    "sup",
    "sub",
    "code",
    "cite",
    "q",
    "span",
    "a",
    "mark",
    "abbr",
    "time",
    "dfn",
    "kbd",
    "var",
}
VOID_TAGS = {"br", "img", "hr", "wbr", "meta", "link", "input", "col"}
ATOMIC_TAGS = {"img", "svg", "br", "math", "object", "embed", "audio", "video", "iframe"}
SKIP_TAGS = {"script", "style", "noscript", "template", "head"}

SENTINEL_STYLES = (
    ("⟨", "⟩"),
    ("⟦", "⟧"),
    ("[[#", "]]"),
)
_SENTINEL = re.compile(r"⟨/?(\d+)⟩")
_ALT_SENTINEL = re.compile(r"⟦/?(\d+)⟧")
_BRACKET_OPEN = re.compile(r"\[\[#(\d+)\]\]")
_BRACKET_CLOSE = re.compile(r"\[\[/(\d+)\]\]")


def local_name(tag: object) -> str:
    if not isinstance(tag, str):
        return ""
    text = tag
    if "}" in text:
        text = text.rsplit("}", 1)[-1]
    return text.lower()


def _epub_type(element: etree._Element) -> str:
    for key, value in element.attrib.items():
        raw = str(key)
        if raw.endswith("}type") or raw == "epub:type":
            return str(value)
    return ""


def _is_noteref(element: etree._Element) -> bool:
    return "noteref" in _epub_type(element).split()


def path_step(element: etree._Element, occurrence: int | None = None) -> str:
    tag = local_name(element.tag)
    parts = [f"{tag}~{occurrence}" if occurrence is not None else tag]
    for key, value in sorted(element.attrib.items()):
        name = local_name(key).split("}")[-1]
        if name.startswith("xmlns"):
            continue
        parts.append(f"{name}={value}")
    return "|".join(parts)


def parse_path_step(step: str) -> tuple[str, dict[str, str]]:
    parts = step.split("|")
    tag = parts[0].split("~", 1)[0]
    attrs: dict[str, str] = {}
    for part in parts[1:]:
        if "=" in part:
            key, value = part.split("=", 1)
            attrs[key] = value
    return tag, attrs


def _attr_string(attrs: dict[str, str]) -> str:
    if not attrs:
        return ""
    rendered = " ".join(
        f'{key}="{html.escape(value, quote=True)}"' for key, value in sorted(attrs.items())
    )
    return f" {rendered}"


def canonical_element(element: etree._Element) -> str:
    """Deterministic HTML serialization: lowercase tags, sorted attributes."""
    tag = local_name(element.tag)
    if not tag or tag in SKIP_TAGS:
        return ""
    attrs = {
        local_name(key).split("}")[-1]: value
        for key, value in element.attrib.items()
        if not local_name(key).startswith("xmlns")
    }
    if tag in VOID_TAGS:
        return f"<{tag}{_attr_string(attrs)}/>"
    inner = [html.escape(element.text or "", quote=False)]
    for child in element:
        inner.append(canonical_element(child))
        inner.append(html.escape(child.tail or "", quote=False))
    return f"<{tag}{_attr_string(attrs)}>{''.join(inner)}</{tag}>"


def canonical_fragment(fragment: str) -> str:
    """Canonicalize an HTML fragment for byte-stable round-trip comparison."""
    if not fragment.strip():
        return ""
    try:
        parent = lxml_html.fragment_fromstring(fragment, create_parent="div")
    except (etree.ParserError, ValueError):
        return html.escape(fragment)
    parts = [html.escape(parent.text or "", quote=False)]
    for child in parent:
        parts.append(canonical_element(child))
        parts.append(html.escape(child.tail or "", quote=False))
    return "".join(parts)


def element_outer_html(element: etree._Element) -> str:
    return canonical_element(element)


def element_inner_html(element: etree._Element) -> str:
    inner = [html.escape(element.text or "", quote=False)]
    for child in element:
        inner.append(canonical_element(child))
        inner.append(html.escape(child.tail or "", quote=False))
    return "".join(inner)


# ----------------------------------------------------------------------- nodes


def _text_node(node_id: int, path: list[str], text: str) -> dict[str, Any]:
    return {"id": node_id, "path": path, "kind": "text", "text": text}


def _atomic_node(node_id: int, path: list[str], markup: str) -> dict[str, Any]:
    return {"id": node_id, "path": path, "kind": "atomic", "text": "", "atomic_html": markup}


def extract_nodes(element: etree._Element) -> list[dict[str, Any]]:
    """Flatten an element's inline content into ordered text/atomic nodes."""
    nodes: list[dict[str, Any]] = []
    counter = [0]
    sequence = [0]

    def add_text(path: list[str], text: str) -> None:
        if text:
            counter[0] += 1
            nodes.append(_text_node(counter[0], path, text))

    def walk(container: etree._Element, path: list[str]) -> None:
        add_text(path, container.text or "")
        for child in container:
            if not isinstance(child.tag, str):
                add_text(path, child.tail or "")
                continue
            tag = local_name(child.tag)
            sequence[0] += 1
            child_path = [*path, path_step(child, sequence[0])]
            if tag in SKIP_TAGS:
                pass
            elif _is_noteref(child):
                counter[0] += 1
                node = _atomic_node(counter[0], path, canonical_element(child))
                href = str(child.get("href", ""))
                if href.startswith("#"):
                    node["target_id"] = href[1:]
                nodes.append(node)
            elif tag in ATOMIC_TAGS:
                counter[0] += 1
                nodes.append(_atomic_node(counter[0], path, canonical_element(child)))
            else:
                walk(child, child_path)
            add_text(path, child.tail or "")

    walk(element, [])
    return nodes


def plain_text_from_nodes(nodes: list[dict[str, Any]], *, verse: bool = False) -> str:
    parts: list[str] = []
    for node in nodes:
        if node["kind"] == "text":
            parts.append(str(node.get("text", "")))
        elif str(node.get("atomic_html", "")).startswith("<br"):
            parts.append("\n")
    return "".join(parts)


def _open_tag(step: str) -> str:
    tag, attrs = parse_path_step(step)
    return f"<{tag}{_attr_string(attrs)}>"


def _close_tag(step: str) -> str:
    tag, _ = parse_path_step(step)
    return f"</{tag}>"


def reconstruct_nodes(nodes: list[dict[str, Any]]) -> str:
    """Rebuild inner HTML, opening/closing tags at common-prefix boundaries."""
    out: list[str] = []
    open_stack: list[str] = []
    for node in nodes:
        path = [str(step) for step in node.get("path", [])]
        common = 0
        for left, right in zip(open_stack, path, strict=False):
            if left != right:
                break
            common += 1
        for step in reversed(open_stack[common:]):
            out.append(_close_tag(step))
        for step in path[common:]:
            out.append(_open_tag(step))
        open_stack = path
        if node["kind"] == "atomic":
            out.append(str(node.get("atomic_html", "")))
        else:
            out.append(html.escape(str(node.get("text", "")), quote=False))
    for step in reversed(open_stack):
        out.append(_close_tag(step))
    return "".join(out)


def _sentinel_pair(node_id: int, style: int) -> tuple[str, str]:
    if style == 2:
        return f"[[#{node_id}]]", f"[[/{node_id}]]"
    opening, closing = SENTINEL_STYLES[style]
    return f"{opening}{node_id}{closing}", f"{opening}/{node_id}{closing}"


def render_with_sentinels(nodes: list[dict[str, Any]], style: int = 0) -> str:
    """Render nodes as text with markers around every marked node."""
    parts: list[str] = []
    for node in nodes:
        node_id = node["id"]
        if node["kind"] == "atomic":
            parts.append(_sentinel_pair(node_id, style)[0])
        elif node.get("path"):
            opening, closing = _sentinel_pair(node_id, style)
            parts.append(f"{opening}{node['text']}{closing}")
        else:
            parts.append(str(node.get("text", "")))
    return "".join(parts)


def sentinel_ids(text: str, style: int = 0) -> list[int]:
    if style == 0:
        return [int(match) for match in _SENTINEL.findall(text)]
    if style == 1:
        return [int(match) for match in _ALT_SENTINEL.findall(text)]
    return [int(match) for match in _BRACKET_OPEN.findall(text)] + [
        int(match) for match in _BRACKET_CLOSE.findall(text)
    ]


def strip_sentinels(text: str) -> str:
    text = _SENTINEL.sub("", text)
    text = _ALT_SENTINEL.sub("", text)
    text = _BRACKET_OPEN.sub("", text)
    return _BRACKET_CLOSE.sub("", text)


def sentinel_free_text(nodes: list[dict[str, Any]]) -> str:
    return "".join(
        str(node.get("text", "")) for node in nodes if node["kind"] == "text"
    )


def escape_text(text: str) -> str:
    return _xml_escape(text)
