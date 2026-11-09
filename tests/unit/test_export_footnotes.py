from __future__ import annotations

from typing import Any

from lxml import etree

from shahrzad.export.assets import SourceAssets
from shahrzad.export.epub_writer import _chapter_body
from shahrzad.export.models import ExportChapter, ExportSegment


def _atomic(node_id: int, html: str) -> dict[str, Any]:
    return {"id": node_id, "path": [], "kind": "atomic", "text": "", "atomic_html": html}


def _segment(
    segment_id: str,
    kind: str,
    *,
    fa_markup: str,
    nodes: list[dict[str, Any]] | None = None,
    source_html: str = "",
    idx: int = 1,
) -> ExportSegment:
    return ExportSegment(
        id=segment_id,
        idx=idx,
        kind=kind,
        source_text=fa_markup,
        source_html=source_html,
        nodes=nodes or [],
        fa_text=fa_markup,
        fa_markup=fa_markup,
        notes={},
        issues=[],
    )


def _parse(body: str) -> etree._Element:
    wrapped = (
        '<html xmlns:epub="http://www.idpf.org/2007/ops"><body>'
        f"{body}</body></html>"
    )
    return etree.fromstring(wrapped.encode("utf-8"))


def _assert_internal_links_resolve(root: etree._Element) -> None:
    ids = set(root.xpath("//@id"))
    for href in root.xpath("//@href"):
        value = str(href)
        if value.startswith("#") and value != "#chapter-top":
            assert value[1:] in ids, f"dangling internal link {value!r}"


def test_footnote_keeps_existing_referrer_id_and_resolves_backlink() -> None:
    chapter = ExportChapter(
        idx=1,
        kind="body",
        title_en="One",
        title_fa=None,
        segments=[
            _segment(
                "c001.s0001",
                "prose",
                fa_markup="⟨1⟩ متن",
                nodes=[
                    _atomic(
                        1,
                        '<a epub:type="noteref" href="#fn1" id="ref1" '
                        'role="doc-noteref">*</a>',
                    )
                ],
            ),
            _segment(
                "c001.s0002",
                "footnote",
                fa_markup="پانوشت ⟨2⟩",
                nodes=[_atomic(2, '<a href="#ref1">*</a>')],
                source_html='<div id="fn1">note</div>',
            ),
        ],
    )

    body = _chapter_body(chapter, SourceAssets())
    root = _parse(body)

    assert len(root.xpath("//a[@id='ref1']")) == 1
    assert not root.xpath("//a[@id='noteref-fn1']")
    assert root.xpath("//a[@class='backlink']/@href") == ["#ref1"]
    _assert_internal_links_resolve(root)


def test_footnote_adds_noteref_id_when_referrer_has_none() -> None:
    chapter = ExportChapter(
        idx=2,
        kind="body",
        title_en="Two",
        title_fa=None,
        segments=[
            _segment(
                "c002.s0001",
                "prose",
                fa_markup="⟨1⟩ متن",
                nodes=[_atomic(1, '<a epub:type="noteref" href="#fn2">*</a>')],
            ),
            _segment(
                "c002.s0002",
                "footnote",
                fa_markup="پانوشت",
                source_html='<div id="fn2">note</div>',
            ),
        ],
    )

    body = _chapter_body(chapter, SourceAssets())
    root = _parse(body)

    assert root.xpath("//a[@id='noteref-fn2']")
    assert root.xpath("//a[@class='backlink']/@href") == ["#noteref-fn2"]
    _assert_internal_links_resolve(root)


def test_footnote_backlink_to_referrer_in_another_chapter_is_made_local() -> None:
    chapter = ExportChapter(
        idx=3,
        kind="body",
        title_en="Three",
        title_fa=None,
        segments=[
            _segment(
                "c003.s0001",
                "footnote",
                fa_markup="پانوشت ⟨1⟩",
                nodes=[_atomic(1, '<a href="#ref-from-earlier-chapter">*</a>')],
                source_html='<div id="fn3">note</div>',
            ),
        ],
    )

    body = _chapter_body(chapter, SourceAssets())
    root = _parse(body)

    assert not root.xpath("//a[@href='#ref-from-earlier-chapter']")
    assert "#chapter-top" in root.xpath("//a[@class='backlink']/@href")
    _assert_internal_links_resolve(root)
