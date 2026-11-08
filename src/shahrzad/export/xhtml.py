"""Segment rendering for XHTML plus Latin-run bidi isolation."""

from __future__ import annotations

import html as html_lib
import re

from lxml import etree
from lxml import html as lxml_html

from shahrzad.persian.markup import rebuilt_html

from .assets import SourceAssets, find_asset
from .models import ExportChapter, ExportSegment

_ALLOWED_TAGS = {
    "a",
    "abbr",
    "b",
    "big",
    "br",
    "caption",
    "cite",
    "code",
    "col",
    "colgroup",
    "del",
    "dfn",
    "em",
    "hr",
    "i",
    "img",
    "ins",
    "kbd",
    "mark",
    "q",
    "s",
    "small",
    "span",
    "strike",
    "strong",
    "sub",
    "sup",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "time",
    "tr",
    "u",
    "var",
}
_DROP_TAGS = {
    "audio",
    "base",
    "button",
    "embed",
    "form",
    "iframe",
    "input",
    "link",
    "math",
    "meta",
    "object",
    "option",
    "script",
    "select",
    "style",
    "svg",
    "template",
    "textarea",
    "video",
}
_VOID_TAGS = {"br", "col", "hr", "img"}
_INLINE_ATTRS = {"class", "dir", "epub:type", "id", "lang", "role", "title"}
_CELL_ATTRS = _INLINE_ATTRS | {"colspan", "headers", "rowspan", "scope"}
_IMG_ATTRS = _INLINE_ATTRS | {"alt", "height", "src", "width"}
_A_ATTRS = _INLINE_ATTRS | {"href"}
_SKIP = {"script", "style", "noscript", "template", "head"}
_PUNCTUATION = set(" \t\n\r-–—.,;:!?/\\()[]{}'\"«»„“”‘’&%+*=<>@#$")
_HEADING = re.compile(r"<h([1-6])\b", re.IGNORECASE)
_SOURCE_ID = re.compile(r'\bid="([^"]+)"')
_LTR_CLASS = re.compile(r"(?:^|\s)ltr(?:\s|$)")
_TABLE_SEPARATOR = re.compile(r"(</t[dh]>)\s*\|\s*(?=<t[dh])", re.IGNORECASE)


def _local_name(tag: object) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].lower()


def _escape(text: str) -> str:
    return html_lib.escape(text, quote=False)


def _attr_string(attributes: dict[str, str]) -> str:
    rendered = " ".join(
        f'{key}="{html_lib.escape(str(value), quote=True)}"'
        for key, value in sorted(attributes.items())
        if not key.startswith("xmlns")
    )
    return f" {rendered}" if rendered else ""


def _safe_href(value: str) -> str | None:
    text = value.strip()
    if not text or text.startswith("//"):
        return None
    if text.startswith(("#", "/", "./", "../")):
        return text
    if ":" in text.split("/", 1)[0]:
        return None
    return text


def _safe_src(value: str) -> str | None:
    text = value.strip()
    if not text or text.startswith(("//", "#")):
        return None
    if text.startswith(("/", "./", "../")):
        return text
    if ":" in text.split("/", 1)[0]:
        return None
    return text


def _safe_attributes(tag: str, attributes: dict[str, object]) -> dict[str, str]:
    allowed = _A_ATTRS if tag == "a" else _IMG_ATTRS if tag == "img" else (
        _CELL_ATTRS if tag in {"td", "th"} else _INLINE_ATTRS
    )
    result: dict[str, str] = {}
    for key, value in attributes.items():
        name = str(key).split("}")[-1]
        if name.lower().startswith("on") or name not in allowed:
            continue
        text = str(value)
        if name == "href":
            safe = _safe_href(text)
            if safe is None:
                continue
            text = safe
        elif name == "src":
            safe = _safe_src(text)
            if safe is None:
                continue
            text = safe
        result[name] = text
    return result


def _is_ltr(element: etree._Element) -> bool:
    classes = str(element.get("class", ""))
    if _LTR_CLASS.search(classes):
        return True
    if str(element.get("dir", "")).lower() == "ltr":
        return True
    return str(element.get("lang", "")).lower() == "en"


def _is_run_char(char: str) -> bool:
    if char.isascii():
        return char.isalnum() or char in _PUNCTUATION
    return "\u00c0" <= char <= "\u024f"


def _needs_isolation(text: str) -> bool:
    for char in text:
        if char.isascii() and (char.isalpha() or char.isdigit()):
            return True
        if "\u00c0" <= char <= "\u024f" and char.isalpha():
            return True
    return False


def _isolate_text(text: str) -> str:
    parts: list[str] = []
    run: list[str] = []

    def flush() -> None:
        if not run:
            return
        value = "".join(run)
        if _needs_isolation(value):
            parts.append(
                '<span class="ltr" dir="ltr" lang="en">' + _escape(value) + "</span>"
            )
        else:
            parts.append(_escape(value))
        run.clear()

    for char in text:
        if _is_run_char(char):
            run.append(char)
        else:
            flush()
            parts.append(_escape(char))
    flush()
    return "".join(parts)


def _serialize_children(
    element: etree._Element,
    *,
    isolate: bool,
    in_ltr: bool,
    assets: SourceAssets | None = None,
    image_prefix: str = "",
) -> str:
    parts: list[str] = []

    def text(value: str | None) -> None:
        if not value:
            return
        if isolate and not in_ltr:
            parts.append(_isolate_text(value))
        else:
            parts.append(_escape(value))

    text(element.text)
    for child in element:
        if not isinstance(child.tag, str):
            text(child.tail)
            continue
        tag = _local_name(child.tag)
        if tag in _SKIP or tag in _DROP_TAGS:
            text(child.tail)
            continue
        if tag == "img":
            asset = find_asset(assets, str(child.get("src", ""))) if assets is not None else None
            if asset is None:
                parts.append(_escape(str(child.get("alt", ""))))
            else:
                attributes = dict(child.attrib)
                attributes["src"] = f"{image_prefix}{asset.name}"
                parts.append(f"<img{_attr_string(_safe_attributes('img', attributes))}/>")
        elif tag not in _ALLOWED_TAGS:
            inner = _serialize_children(
                child,
                isolate=isolate,
                in_ltr=in_ltr or _is_ltr(child),
                assets=assets,
                image_prefix=image_prefix,
            )
            parts.append(inner)
        elif tag in _VOID_TAGS:
            parts.append(f"<{tag}{_attr_string(_safe_attributes(tag, dict(child.attrib)))}/>")
        else:
            inner = _serialize_children(
                child,
                isolate=isolate,
                in_ltr=in_ltr or _is_ltr(child),
                assets=assets,
                image_prefix=image_prefix,
            )
            attributes = _safe_attributes(tag, dict(child.attrib))
            parts.append(f"<{tag}{_attr_string(attributes)}>{inner}</{tag}>")
        text(child.tail)
    return "".join(parts)


def _parse(fragment: str) -> etree._Element | None:
    if not fragment.strip():
        return None
    try:
        return lxml_html.fragment_fromstring(fragment, create_parent="div")
    except (etree.ParserError, ValueError):
        return None


def _serialize_fragment(
    fragment: str,
    *,
    isolate: bool,
    assets: SourceAssets | None,
    image_prefix: str,
) -> str:
    root = _parse(fragment)
    if root is None:
        return fragment
    return _serialize_children(
        root,
        isolate=isolate,
        in_ltr=_is_ltr(root),
        assets=assets,
        image_prefix=image_prefix,
    )


def isolate_latin(fragment: str) -> str:
    """Wrap Latin runs in an isolated LTR span, leaving Persian text untouched."""
    return _serialize_fragment(
        fragment, isolate=True, assets=None, image_prefix=""
    )


def heading_level(source_html: str, default: int = 2) -> int:
    match = _HEADING.search(source_html)
    return int(match.group(1)) if match else default


def footnote_id(segment: ExportSegment) -> str:
    match = _SOURCE_ID.search(segment.source_html)
    if match:
        return match.group(1)
    return "fn-" + segment.id.replace(".", "-")


def chapter_title(chapter: ExportChapter) -> str:
    if chapter.title_fa and chapter.title_fa.strip():
        return chapter.title_fa.strip()
    for segment in chapter.segments:
        if segment.kind == "heading" and segment.fa_text.strip():
            return segment.fa_text.strip()
    if chapter.title_en and chapter.title_en.strip():
        return chapter.title_en.strip()
    return f"فصل {chapter.idx}"


def render_segment(
    segment: ExportSegment,
    assets: SourceAssets,
    *,
    image_prefix: str = "../images/",
) -> str:
    """Render one translated segment as a block-level XHTML fragment."""
    if segment.kind == "footnote":
        return ""
    inner = rebuilt_html(segment.fa_markup, segment.nodes)
    rendered = _serialize_fragment(
        inner, isolate=True, assets=assets, image_prefix=image_prefix
    )
    if not rendered:
        return ""
    if segment.kind == "heading":
        level = heading_level(segment.source_html)
        return f"<h{level}>{rendered}</h{level}>"
    if segment.kind == "verse":
        return f'<p class="verse">{rendered}</p>'
    if segment.kind == "quote":
        return f"<blockquote>{rendered}</blockquote>"
    if segment.kind == "table":
        rendered = _TABLE_SEPARATOR.sub(r"\1", rendered)
        return f'<table class="table">{rendered}</table>'
    if segment.kind == "caption":
        return f'<p class="caption">{rendered}</p>'
    return f"<p>{rendered}</p>"


def segment_body(segment: ExportSegment, assets: SourceAssets) -> str:
    """Body markup for a footnote/endnote item (no paragraph wrapper)."""
    inner = rebuilt_html(segment.fa_markup, segment.nodes)
    return _serialize_fragment(
        inner, isolate=True, assets=assets, image_prefix="../images/"
    )
