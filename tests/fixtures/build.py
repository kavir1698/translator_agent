"""Synthetic books for tests. Project-authored text; no third-party material is shipped."""

from __future__ import annotations

import base64
import zipfile
from pathlib import Path

import yaml
from docx import Document
from reportlab.lib.pagesizes import A5
from reportlab.pdfgen import canvas

TITLE = "The Test Book"
AUTHOR = "A. Writer"

SAMPLE_PARAGRAPHS = [
    "The old man walked down to the harbour before sunrise, carrying a folded net "
    "over one shoulder and a small tin box in his other hand. The water was calm "
    "and grey, and the boats moved slowly against their ropes as if they were "
    "still dreaming.",
    "He had done this every morning for forty years, and he no longer needed to "
    "look at the sky to know what the day would bring. The gulls knew it too, and "
    "they followed him along the stones, waiting for the first small fish.",
    "By noon the wind had changed, and the whole village seemed to lean westward. "
    "Women closed their shutters, children were called inside, and the men who had "
    "no business on the water stood at the wall and watched the horizon.",
    "That evening the old man sat alone in the kitchen and listened to the rain. "
    "He thought about his brother, who had left the island many years ago, and "
    "about the letter that had never come, and he was not sad.",
]

_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)

_CONTAINER = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>
"""

_OPF = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="pub-id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="pub-id">urn:uuid:11111111-2222-3333-4444-555555555555</dc:identifier>
    <dc:title>{title}</dc:title>
    <dc:creator>{author}</dc:creator>
    <dc:language>en</dc:language>
    <dc:publisher>Fixture Press</dc:publisher>
    <dc:rights>Public Domain</dc:rights>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="c1" href="text/chapter-001.xhtml" media-type="application/xhtml+xml"/>
    <item id="c2" href="text/chapter-002.xhtml" media-type="application/xhtml+xml"/>
    <item id="img" href="images/pixel.png" media-type="image/png" properties="cover-image"/>
  </manifest>
  <spine>
    <itemref idref="c1"/>
    <itemref idref="c2"/>
  </spine>
</package>
"""

_NAV = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>Contents</title></head>
<body><nav epub:type="toc"><ol><li><a href="text/chapter-001.xhtml">One</a></li></ol></nav></body>
</html>
"""


def _xhtml(title: str, body: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"
      lang="en" xml:lang="en">
<head><title>{title}</title></head>
<body>
{body}
</body>
</html>
"""


def build_epub(path: Path) -> Path:
    chapter_one = _xhtml(
        "Chapter One",
        f"""<h1>Chapter One</h1>
<p>He was <em>very</em> angry, <a href="#n1" epub:type="noteref">she said</a>.</p>
<p>{SAMPLE_PARAGRAPHS[0]}</p>
<p>{SAMPLE_PARAGRAPHS[1]}</p>
<p class="verse">The first line<br/>The second line<br/>The third line</p>
<table><tr><th>Name</th><th>Value</th></tr><tr><td>Alpha</td><td>One</td></tr></table>
<p>Text with a footnote.<a href="#fn1" epub:type="noteref">1</a></p>
<aside id="fn1" epub:type="footnote">Footnote text for the first chapter.</aside>
<figure><img src="../images/pixel.png" alt="A single pixel"/><figcaption>A caption for the figure.</figcaption></figure>
<hr/>
<p>After the break.</p>""",
    )
    chapter_two = _xhtml(
        "Chapter Two",
        f"""<h1>Chapter Two</h1>
<p>{SAMPLE_PARAGRAPHS[2]}</p>
<blockquote>A quoted line, kept short.</blockquote>
<p>{SAMPLE_PARAGRAPHS[3]}</p>""",
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", _CONTAINER, compress_type=zipfile.ZIP_DEFLATED)
        archive.writestr(
            "OEBPS/content.opf",
            _OPF.format(title=TITLE, author=AUTHOR),
            compress_type=zipfile.ZIP_DEFLATED,
        )
        archive.writestr("OEBPS/nav.xhtml", _NAV, compress_type=zipfile.ZIP_DEFLATED)
        archive.writestr("OEBPS/text/chapter-001.xhtml", chapter_one, compress_type=zipfile.ZIP_DEFLATED)
        archive.writestr("OEBPS/text/chapter-002.xhtml", chapter_two, compress_type=zipfile.ZIP_DEFLATED)
        archive.writestr("OEBPS/images/pixel.png", _PIXEL_PNG, compress_type=zipfile.ZIP_DEFLATED)
    return path


def build_docx(path: Path) -> Path:
    document = Document()
    document.core_properties.title = TITLE
    document.core_properties.author = AUTHOR
    document.add_heading("Chapter One", level=1)
    first = document.add_paragraph()
    first.add_run("He was ")
    first.add_run("very").bold = True
    first.add_run(" calm, and ")
    first.add_run("she said").italic = True
    first.add_run(" nothing at all.")
    document.add_paragraph(SAMPLE_PARAGRAPHS[0])
    document.add_paragraph(SAMPLE_PARAGRAPHS[1])
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Name"
    table.cell(0, 1).text = "Value"
    table.cell(1, 0).text = "Alpha"
    table.cell(1, 1).text = "One"
    document.add_heading("Chapter Two", level=1)
    document.add_paragraph(SAMPLE_PARAGRAPHS[2])
    document.add_paragraph(SAMPLE_PARAGRAPHS[3])
    document.save(str(path))
    return path


def build_txt(path: Path) -> Path:
    content = (
        "# Chapter One\n\n"
        f"{SAMPLE_PARAGRAPHS[0]}\n\n"
        "He was *very* calm, and **she said** nothing.\n\n"
        "> A quoted line, kept short.\n\n"
        f"{SAMPLE_PARAGRAPHS[1]}\n\n"
        "# Chapter Two\n\n"
        f"{SAMPLE_PARAGRAPHS[2]}\n\n"
        f"{SAMPLE_PARAGRAPHS[3]}\n"
    )
    path.write_text(content, encoding="utf-8")
    return path


def _wrap(text: str, width: int = 78) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        if sum(len(item) + 1 for item in current) + len(word) > width:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return lines


def _draw_paragraph(pdf: canvas.Canvas, text: str, x: float, y: float, size: float) -> float:
    pdf.setFont("Helvetica", size)
    for line in _wrap(text):
        pdf.drawString(x, y, line)
        y -= size * 1.35
    return y


def build_pdf(path: Path) -> Path:
    width, height = A5
    pdf = canvas.Canvas(str(path), pagesize=A5)
    margin = 42

    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawString(margin, height - 60, TITLE)
    y = height - 100
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(margin, y, "Chapter One")
    y -= 26
    for paragraph in SAMPLE_PARAGRAPHS[:2]:
        y = _draw_paragraph(pdf, paragraph, margin, y, 10.5) - 16
    pdf.showPage()

    y = height - 60
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(margin, y, "Chapter Two")
    y -= 26
    for paragraph in SAMPLE_PARAGRAPHS[2:]:
        y = _draw_paragraph(pdf, paragraph, margin, y, 10.5) - 16
    pdf.showPage()

    y = height - 60
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(margin, y, "Chapter Three")
    y -= 26
    _draw_paragraph(pdf, SAMPLE_PARAGRAPHS[0], margin, y, 10.5)
    pdf.showPage()
    pdf.save()
    return path


def build_image_pdf(path: Path) -> Path:
    width, height = A5
    pdf = canvas.Canvas(str(path), pagesize=A5)
    for _ in range(3):
        pdf.setFillColorRGB(0.2, 0.2, 0.2)
        pdf.rect(40, 40, width - 80, height - 80, fill=1, stroke=0)
        pdf.showPage()
    pdf.save()
    return path


def build_structure_yaml(path: Path) -> Path:
    data = {
        "version": 1,
        "chapters": [
            {"title_en": "Chapter One", "start_text": SAMPLE_PARAGRAPHS[0][:28], "kind": "body"},
            {"title_en": "Chapter Two", "start_text": SAMPLE_PARAGRAPHS[2][:28], "kind": "body"},
        ],
    }
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


def build_all(directory: Path) -> dict[str, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    return {
        "epub": build_epub(directory / "book.epub"),
        "docx": build_docx(directory / "book.docx"),
        "txt": build_txt(directory / "book.md"),
        "pdf": build_pdf(directory / "book.pdf"),
        "image_pdf": build_image_pdf(directory / "image-only.pdf"),
        "structure": build_structure_yaml(directory / "structure.yaml"),
    }


if __name__ == "__main__":
    target = Path(__file__).with_name("generated")
    for name, built in build_all(target).items():
        print(f"{name}: {built}")
