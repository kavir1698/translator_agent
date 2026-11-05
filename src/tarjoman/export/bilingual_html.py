"""Self-contained bilingual HTML side product (source | translation)."""

from __future__ import annotations

import html as html_lib
from pathlib import Path

from lxml import etree
from lxml import html as lxml_html

from tarjoman.core.atomic import atomic_write_bytes
from tarjoman.core.config import Settings
from tarjoman.persian.markup import rebuilt_html

from .models import ExportBook, ExportChapter, ExportSegment
from .xhtml import chapter_title, isolate_latin

_SKIP = {"script", "style", "noscript", "template", "head"}
_INLINE = {"strong", "b", "em", "i", "u", "sup", "sub", "code"}

_STYLE = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
html { direction: rtl; }
body {
  margin: 0 auto; max-width: 1100px; padding: 1.5rem;
  font-family: "Vazirmatn", "Noto Naskh Arabic", serif;
  line-height: 1.8; color: #1d1d1f; background: #fafafa;
}
header { border-bottom: 2px solid #d0d0d5; margin-bottom: 1.25rem; padding-bottom: 1rem; }
h1 { margin: 0 0 .35rem; font-size: 1.7rem; }
.meta { color: #5a5a60; font-size: .9rem; }
.controls { display: flex; flex-wrap: wrap; gap: .75rem; align-items: center; margin: 1rem 0 1.5rem; }
.controls label { font-size: .9rem; }
.controls input[type="search"], .controls select {
  padding: .45rem .6rem; border: 1px solid #b9b9c0; border-radius: .35rem;
  font: inherit; background: #fff; color: inherit; min-width: 12rem;
}
.controls input[type="search"] { direction: ltr; text-align: left; }
section.chapter { margin-bottom: 2rem; }
h2 { font-size: 1.2rem; border-bottom: 1px solid #dcdce1; padding-bottom: .35rem; }
table.segments { width: 100%; border-collapse: collapse; table-layout: fixed; }
thead th { text-align: right; font-size: .85rem; color: #5a5a60; padding: .4rem .6rem; border-bottom: 1px solid #d0d0d5; }
td { vertical-align: top; padding: .6rem; border-bottom: 1px solid #e6e6ea; overflow-wrap: anywhere; }
td.src { background: #f3f5f9; border-left: 1px solid #e0e3e9; }
td.tgt { background: #fff; }
.text { white-space: pre-wrap; }
tbody tr.active td, tbody tr:focus td { background: #fff6d9; }
.badge {
  display: inline-block; margin: .35rem .2rem 0 0; padding: .1rem .4rem;
  border-radius: .3rem; font-family: ui-monospace, monospace; font-size: .72rem;
  direction: ltr; unicode-bidi: isolate; background: #efe9d8; color: #5a4300;
}
.badge.sev-error, .badge.sev-critical { background: #fbe1e1; color: #8c1c1c; }
.badge.sev-warn { background: #fff2cc; color: #6b5200; }
.hint { color: #6a6a70; font-size: .82rem; }
@media (max-width: 720px) { table.segments, thead, tbody, tr, td, th { display: block; } td.src { border-left: 0; } }
"""

_SCRIPT = """
(function () {
  var rows = Array.prototype.slice.call(document.querySelectorAll("tr.seg"));
  var search = document.getElementById("filter-text");
  var chapter = document.getElementById("filter-chapter");
  var only = document.getElementById("filter-issues");
  function apply() {
    var query = (search.value || "").toLowerCase();
    var chapterValue = chapter.value;
    rows.forEach(function (row) {
      var text = (row.textContent || "").toLowerCase();
      var visible =
        (!query || text.indexOf(query) !== -1) &&
        (!chapterValue || row.getAttribute("data-chapter") === chapterValue) &&
        (!only.checked || row.getAttribute("data-issues") === "1");
      row.style.display = visible ? "" : "none";
    });
  }
  [search, chapter, only].forEach(function (field) {
    field.addEventListener("input", apply);
    field.addEventListener("change", apply);
  });
  document.addEventListener("keydown", function (event) {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") { return; }
    var tag = event.target && event.target.tagName;
    if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") { return; }
    var visible = rows.filter(function (row) { return row.style.display !== "none"; });
    if (!visible.length) { return; }
    var index = visible.findIndex(function (row) { return row.classList.contains("active"); });
    index = event.key === "ArrowDown" ? index + 1 : index - 1;
    if (index < 0) { index = visible.length - 1; }
    if (index >= visible.length) { index = 0; }
    visible.forEach(function (row) { row.classList.remove("active"); });
    visible[index].classList.add("active");
    visible[index].scrollIntoView({ block: "center" });
    event.preventDefault();
  });
})();
"""


def _escape(value: str) -> str:
    return html_lib.escape(value, quote=False)


def _attr(value: str) -> str:
    return html_lib.escape(value, quote=True)


def _local(tag: object) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].lower()


def _parse(fragment: str) -> etree._Element | None:
    if not fragment or not fragment.strip():
        return None
    try:
        return lxml_html.fragment_fromstring(fragment, create_parent="div")
    except (etree.ParserError, ValueError):
        return None


def _serialize_inline(element: etree._Element) -> str:
    parts = [_escape(element.text or "")]
    for child in element:
        tag = _local(child.tag)
        if tag in _SKIP:
            pass
        elif tag == "br":
            parts.append("<br/>")
        elif tag == "img":
            parts.append(_escape(str(child.get("alt", ""))))
        elif tag == "a":
            parts.append(_serialize_inline(child))
        elif tag in _INLINE:
            parts.append(f"<{tag}>{_serialize_inline(child)}</{tag}>")
        else:
            parts.append(_serialize_inline(child))
        parts.append(_escape(child.tail or ""))
    return "".join(parts)


def _translation_html(segment: ExportSegment) -> str:
    fragment = rebuilt_html(segment.fa_markup, segment.nodes)
    root = _parse(fragment)
    if root is None:
        return ""
    return isolate_latin(_serialize_inline(root))


def _badges(segment: ExportSegment) -> str:
    parts: list[str] = []
    for issue in segment.issues:
        code = str(issue.get("code") or "").strip()
        severity = str(issue.get("severity") or "").strip()
        if not code:
            continue
        message = str(issue.get("message") or "")
        parts.append(
            f'<span class="badge sev-{_attr(severity)}" title="{_attr(message)}">'
            f"{_escape(code)} · {_escape(severity)}</span>"
        )
    return "".join(parts)


def _row(segment: ExportSegment, chapter: ExportChapter, include_source: bool) -> str:
    source_cell = ""
    if include_source:
        source_cell = (
            '<td class="src" dir="ltr" lang="en">'
            f'<div class="text">{_escape(segment.source_text)}</div></td>'
        )
    target = _translation_html(segment)
    return (
        f'<tr class="seg" data-chapter="{chapter.idx}" '
        f'data-issues="{1 if segment.issues else 0}" tabindex="0">'
        f"{source_cell}"
        f'<td class="tgt" dir="rtl"><div class="text">{target}</div>'
        f"{_badges(segment)}</td></tr>"
    )


def _chapter_section(
    chapter: ExportChapter, include_source: bool
) -> str:
    header = "<th>متن اصلی</th>" if include_source else ""
    rows = "\n".join(
        _row(segment, chapter, include_source) for segment in chapter.segments
    )
    return (
        f'<section class="chapter" data-chapter="{chapter.idx}" id="chapter-{chapter.idx}">'
        f"<h2>{_escape(chapter_title(chapter))}</h2>"
        '<table class="segments"><thead><tr>'
        f"{header}<th>ترجمه</th></tr></thead><tbody>{rows}</tbody></table>"
        "</section>"
    )


def _chapters(book: ExportBook, include_source: bool) -> str:
    return "\n".join(_chapter_section(chapter, include_source) for chapter in book.chapters)


def _chapter_options(book: ExportBook) -> str:
    return "\n".join(
        f'<option value="{chapter.idx}">{chapter.idx}. {_escape(chapter_title(chapter))}</option>'
        for chapter in book.chapters
    )


def _document(book: ExportBook, include_source: bool) -> str:
    title = book.title_fa or book.title_en
    translator = f" — ترجمه: {_escape(book.translator)}" if book.translator else ""
    author = f" — {_escape(book.author)}" if book.author else ""
    return f"""<!DOCTYPE html>
<html dir="rtl" lang="fa">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_escape(title)} — دوزبانه</title>
<style>{_STYLE}</style>
</head>
<body>
<header>
<h1>{_escape(title)}</h1>
<div class="meta">{_escape(book.title_en)}{author}{translator}</div>
</header>
<div class="controls">
<input id="filter-text" type="search" placeholder="جست‌وجو…" aria-label="جست‌وجو">
<select id="filter-chapter" aria-label="فصل">
<option value="">همهٔ فصل‌ها</option>
{_chapter_options(book)}
</select>
<label><input id="filter-issues" type="checkbox"> فقط با ایراد</label>
<span class="hint">کلیدهای ↑/↓ برای رفتن به ردیف بعد یا قبل</span>
</div>
{_chapters(book, include_source)}
<script>{_SCRIPT}</script>
</body>
</html>
"""


def write_bilingual_html(book: ExportBook, path: Path, settings: Settings) -> Path:
    """Write one self-contained bilingual HTML file and return its path."""
    include_source = settings.export.bilingual_html.include_source
    return atomic_write_bytes(path, _document(book, include_source).encode("utf-8"))
