from __future__ import annotations

from pathlib import Path

from shahrzad.export import structure
from shahrzad.export.models import ExportBook, ExportChapter, ExportSegment


def _segment(segment_id: str) -> ExportSegment:
    return ExportSegment(
        id=segment_id,
        idx=1,
        kind="prose",
        source_text="source",
        source_html="<p>source</p>",
        nodes=[],
        fa_text="متن",
        fa_markup="متن",
        notes={},
        issues=[],
    )


def _chapter(
    idx: int, doc: str, title: str, *, title_fa: str | None = None, segments: int = 1
) -> ExportChapter:
    return ExportChapter(
        idx=idx,
        kind="body",
        title_en=title,
        title_fa=title_fa,
        segments=[_segment(f"c{idx:03d}.s{n:04d}") for n in range(segments)],
        source_doc=doc,
    )


def _book(chapters: list[ExportChapter]) -> ExportBook:
    return ExportBook(
        slug="book",
        title_en="Book",
        title_fa=None,
        author=None,
        translator=None,
        language="fa",
        rights_basis="user_supplied",
        source_format="epub",
        source_sha256="x",
        source_path=Path("book.epub"),
        metadata={},
        cover=None,
        chapters=chapters,
        generated_at="2026-01-01T00:00:00Z",
        incomplete=False,
        open_errors=0,
    )


def test_epub_structure_groups_continuations_and_moves_essay_after_story(
    monkeypatch,
) -> None:
    chapters = [
        _chapter(3, "doc003", "Chapter 3"),
        _chapter(4, "doc004", "Contents", title_fa="فهرست", segments=2),
        _chapter(5, "doc005", "Chapter 5"),
        _chapter(6, "doc006", "We Begin"),
        _chapter(7, "doc008", 'THOUGHTS ON “IN THE CART”'),
        _chapter(8, "doc009", "IN THE CART"),
        _chapter(9, "doc010", "Chapter 9"),
        _chapter(10, "doc011", "AFTERTHOUGHT #1"),
    ]
    spine = [
        "OEBPS/xhtml/doc001.xhtml",
        "OEBPS/xhtml/doc002.xhtml",
        "OEBPS/xhtml/doc003.xhtml",
        "OEBPS/xhtml/doc004.xhtml",
        "OEBPS/xhtml/doc005.xhtml",
        "OEBPS/xhtml/doc006.xhtml",
        "OEBPS/xhtml/doc007.xhtml",
        "OEBPS/xhtml/doc008.xhtml",
        "OEBPS/xhtml/doc009.xhtml",
        "OEBPS/xhtml/doc010.xhtml",
        "OEBPS/xhtml/doc011.xhtml",
    ]
    toc = [
        structure.TocEntry("Cover", "OEBPS/xhtml/doc001.xhtml"),
        structure.TocEntry("Title Page", "OEBPS/xhtml/doc002.xhtml"),
        structure.TocEntry("Copyright", "OEBPS/xhtml/doc003.xhtml"),
        structure.TocEntry("Contents", "OEBPS/xhtml/doc004.xhtml"),
        structure.TocEntry("Epigraph", "OEBPS/xhtml/doc005.xhtml"),
        structure.TocEntry("We Begin", "OEBPS/xhtml/doc006.xhtml"),
        structure.TocEntry("In the Cart • Anton Chekhov", "OEBPS/xhtml/doc007.xhtml"),
        structure.TocEntry(
            "A Page at a Time: Thoughts on “In the Cart”",
            "OEBPS/xhtml/doc008.xhtml",
        ),
        structure.TocEntry("In the Cart", "OEBPS/xhtml/doc009.xhtml"),
        structure.TocEntry("Afterthought #1", "OEBPS/xhtml/doc011.xhtml"),
    ]
    monkeypatch.setattr(structure, "read_epub_toc", lambda *_: toc)
    monkeypatch.setattr(structure, "read_spine_hrefs", lambda *_: spine)
    monkeypatch.setattr(
        structure,
        "_read_moves",
        lambda *_: [
            (
                "A Page at a Time: Thoughts on “In the Cart”",
                "In the Cart",
            )
        ],
    )

    exported, links, toc_idx, items = structure.build_export_structure(_book(chapters))

    assert toc_idx == 2
    assert [(chapter.idx, chapter.title_en) for chapter in exported] == [
        (1, "Copyright"),
        (2, "Contents"),
        (3, "Epigraph"),
        (4, "We Begin"),
        (5, "IN THE CART"),
        (6, 'THOUGHTS ON “IN THE CART”'),
        (7, "AFTERTHOUGHT #1"),
    ]
    story = next(chapter for chapter in exported if chapter.title_en == "IN THE CART")
    assert len(story.segments) == 2  # chapter 8 + its continuation chapter 9
    assert all(chapter.title_en != "Chapter 9" for chapter in exported)
    assert links["doc007.xhtml"] == "chapter-005.xhtml"
    assert links["doc008.xhtml"] == "chapter-006.xhtml"
    assert links["doc009.xhtml"] == "chapter-005.xhtml"
    assert [label for label, _ in items][-1] == "AFTERTHOUGHT #1"
