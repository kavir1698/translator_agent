from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from lxml import html as lxml_html

from shahrzad.ingest.inline import extract_nodes, render_with_sentinels
from shahrzad.persian.lint import (
    CHECK_REGISTRY,
    GlossaryTerm,
    LintBook,
    LintChapter,
    LintSegment,
    LintTranslation,
    LintWindow,
    auto_fix,
    check_book,
    check_chapter,
    check_segment,
    check_window,
)
from shahrzad.persian.markup import (
    normalize_markup,
    normalize_translation,
    rebuilt_html,
    validate_markup,
)
from shahrzad.persian.normalize import has_banned_codepoints, normalize_fa
from shahrzad.persian.numerals import (
    numbers_match,
    parse_number,
    spelled_number_present,
    to_ascii_digits,
    to_persian_digits,
)

GOLDEN = [
    ("سلام", "سلام"),
    ("كتاب", "کتاب"),
    ("علي", "علی"),
    ("مصطفى", "مصطفی"),
    ("ــــ", ""),
    ("٠١٢٣", "۰۱۲۳"),
    ("0123", "۰۱۲۳"),
    ("12345", "۱۲۳۴۵"),
    ("می‌باشد", "می‌باشد"),
    ("سلام...", "سلام…"),
    ("«سلام»", "«سلام»"),
    ('"سلام"', "«سلام»"),
    ("سلام,دنیا", "سلام،دنیا"),
    ("سلام?", "سلام؟"),
    ("سلام;", "سلام؛"),
    ("50%", "۵۰٪"),
    ("۵۰ %", "۵۰٪"),
    ("a  b", "a b"),
    ("  سلام  ", "سلام"),
    ("سلام\n\n\n\nدنیا", "سلام\n\nدنیا"),
    ("می رود", "می رود"),
    ("كتابها", "کتابها"),
    ("مُحَمَّد", "محمد"),
    ("ﷲ", "الله"),
    ("ﻻ", "لا"),
    ("سلام\u200f", "سلام"),
    ("سلام\u00a0دنیا", "سلام دنیا"),
    ("سلام\u200bدنیا", "سلامدنیا"),
    ("می‌‌رود", "می‌رود"),
    ("‌سلام", "سلام"),
    ("سلام‌", "سلام"),
    ("سلام ،", "سلام،"),
    ("« سلام»", "«سلام»"),
    ("سلام (دنیا)", "سلام (دنیا)"),
    ("123.45", "۱۲۳.۴۵"),
    ("1,234", "۱,۲۳۴"),
    ("سلام  دنیا", "سلام دنیا"),
    ("«سلام»...", "«سلام»…"),
    ("ـسلامـ", "سلام"),
    ("ى", "ی"),
    ("ك", "ک"),
    ("ة", "ة"),
    ("ﻛﺘﺎﺏ", "کتاب"),
    ("سلام\tدنیا", "سلام دنیا"),
    ("۵۰٪", "۵۰٪"),
    ("می‌باشد.", "می‌باشد."),
    ("این... آن", "این… آن"),
    ("", ""),
    ("ﬁ", "fi"),
    ("سلام ‌ دنیا", "سلام دنیا"),
]


@pytest.mark.parametrize(("source", "expected"), GOLDEN)
def test_normalization_golden(source: str, expected: str):
    assert normalize_fa(source) == expected


def test_banned_codepoints_removed():
    nasty = "ﻛﺘﺎﺏ ﻋﻠﻲ ــــ ٠١٢٣"
    cleaned = normalize_fa(nasty)
    assert has_banned_codepoints(cleaned) == []
    assert "ك" not in cleaned and "ي" not in cleaned
    assert "０" not in cleaned


def test_digit_modes():
    assert normalize_fa("123", digits="ascii") == "123"
    assert normalize_fa("۱۲۳", digits="ascii") == "123"
    assert to_persian_digits("123٤٥") == "۱۲۳۴۵"
    assert to_ascii_digits("۱۲۳٤٥") == "12345"


def test_keep_diacritics():
    assert normalize_fa("مُحَمَّد", keep_diacritics=True) == "مُحَمَّد"


_MIXED = (
    "كتاب علي مُحَمَّد ٠١٢٣ 0123 \"سلام\" 50% ... سلام,دنیا می‌باشد ــــ "
    "「ﷲ」 ﻛﺘﺎﺏ ‌‌ ‌سلام سلام‌ 1,234 ۱۲۳.۴۵ « سلام» 🎈 \u200b \u200f \u00a0"
)


@pytest.mark.parametrize("_", range(10))
def test_idempotence_on_10k_corpus(_: int):
    corpus = (_MIXED * 80)[:10_000]
    once = normalize_fa(corpus)
    assert normalize_fa(once) == once


@given(st.text(alphabet=list(_MIXED + "abcXYZ  \n\t"), max_size=120))
@settings(max_examples=60, deadline=None)
def test_idempotence_property(text: str):
    once = normalize_fa(text)
    assert normalize_fa(once) == once


def test_numbers():
    assert numbers_match("He paid 1,234 dollars.", "او ۱۲۳۴ دلار پرداخت.") is True
    assert numbers_match("He paid 1,234 dollars.", "او ۱۲۳۵ دلار پرداخت.") is False
    assert parse_number("۱۲۳٫۴۵") == 123.45


def test_spelled_numbers():
    assert spelled_number_present(19, "قرن نوزدهم") is True
    assert spelled_number_present(20, "بیست و یک کتاب") is True
    assert spelled_number_present(3, "سه روز") is True
    assert spelled_number_present(19, "قرن بیستم") is False
    assert spelled_number_present(1.5, "یک و نیم") is False


def _nodes(fragment: str):
    element = lxml_html.fragment_fromstring(fragment)
    return extract_nodes(element), element


def test_markup_sentinels_roundtrip():
    nodes, _ = _nodes("<p>He was <em>very</em> angry, <strong>she</strong> said.</p>")
    markup = render_with_sentinels(nodes)
    assert "⟨1⟩" not in markup or "⟨/1⟩" in markup
    assert validate_markup(markup, nodes) == []


def test_markup_detects_missing_and_crossed_pairs():
    nodes, _ = _nodes("<p>He was <em>very</em> <strong>angry</strong></p>")
    markup = render_with_sentinels(nodes)
    broken = markup.replace("⟨/2⟩", "")
    assert any(problem.code in {"wrong_count", "unclosed_pair"} for problem in validate_markup(broken, nodes))
    crossed = markup.replace("⟨2⟩", "⟨X⟩").replace("⟨/2⟩", "⟨2⟩").replace("⟨X⟩", "⟨/2⟩")
    assert validate_markup(crossed, nodes)


def test_markup_normalizes_text_runs_only():
    nodes = [
        {"id": 1, "path": ["em~1"], "kind": "text", "text": "x"},
        {"id": 2, "path": [], "kind": "text", "text": " 0123 "},
    ]
    result = normalize_translation("⟨1⟩كتاب⟨/1⟩ 0123", nodes)
    assert result.fa_markup.startswith("⟨1⟩")
    assert "⟨/" in result.fa_markup
    assert "۰۱۲۳" in result.fa_markup
    assert result.problems == []
    assert "۰۱۲۳" in result.fa_text
    assert normalize_markup("⟨1⟩كتاب⟨/1⟩") == "⟨1⟩کتاب⟨/1⟩"


def test_rebuilt_html_keeps_markup():
    nodes, _ = _nodes("<p>He was <em>very</em> angry.</p>")
    markup = render_with_sentinels(nodes)
    html = rebuilt_html(markup, nodes)
    assert "<em>" in html and "very" in html


def test_auto_fix_only_mechanical():
    assert auto_fix("ميرود") == "می‌رود"
    assert auto_fix("كتاب 0123, خوب") == "کتاب ۰۱۲۳، خوب"


# --------------------------------------------------------------------------- linters


def seg(source: str, *, kind: str = "prose", nodes=None, status: str = "done", html: str = "") -> LintSegment:
    return LintSegment(
        id="c001.s0001",
        kind=kind,
        source_text=source,
        source_html=html or f"<p>{source}</p>",
        nodes=nodes or [],
        status=status,
    )


def tr(text: str, markup: str = "") -> LintTranslation:
    return LintTranslation(fa_text=text, fa_markup=markup)


def codes(issues) -> set[str]:
    return {issue.code for issue in issues}


def test_registry_covers_all_codes():
    assert [f"L{index:02d}" for index in range(1, 21)] == list(CHECK_REGISTRY)
    scopes = {code: meta["scope"] for code, meta in CHECK_REGISTRY.items()}
    assert scopes["L03"] == "window" and scopes["L15"] == "chapter" and scopes["L09"] == "book"


def test_l01_coverage():
    assert "L01" in codes(check_segment(seg("hello"), None))
    assert "L01" in codes(check_segment(seg("hello"), tr("")))
    assert "L01" not in codes(check_segment(seg("hello"), tr("سلام")))
    assert codes(check_segment(seg("1", status="skipped"), None)) == set()


def test_l02_markup():
    nodes = [{"id": 1, "path": ["em~1"], "kind": "text", "text": "x"}]
    assert "L02" in codes(check_segment(seg("hello", nodes=nodes), tr("سلام", "⟨1⟩سلام")))
    assert "L02" not in codes(
        check_segment(seg("hello", nodes=nodes), tr("سلام", "⟨1⟩سلام⟨/1⟩"))
    )


def test_l03_window_glossary():
    window = LintWindow(id="c001.w001", segments=[seg("Santiago went home.")])
    glossary = [GlossaryTerm(en="Santiago", fa="سانتیاگو", kind="character")]
    missing = check_window(window, {"c001.s0001": tr("او به خانه رفت.")}, glossary)
    assert "L03" in codes(missing)
    present = check_window(window, {"c001.s0001": tr("سانتیاگو به خانه رفت.")}, glossary)
    assert "L03" not in codes(present)


def test_l04_script_purity():
    assert "L04" in codes(check_segment(seg("hello"), tr("كتاب علي")))
    assert "L04" in codes(check_segment(seg("hello"), tr("سلام ١٢٣")))
    assert "L04" in codes(check_segment(seg("hello"), tr("هذا كتاب")))
    warnings = check_segment(seg("hello"), tr("نتیجة کار"))
    assert any(issue.code == "L04" and issue.severity == "warn" for issue in warnings)
    assert "L04" not in codes(check_segment(seg("hello"), tr("سلام دنیا")))


def test_l05_digits():
    assert "L05" in codes(check_segment(seg("Chapter 5"), tr("فصل 5")))
    assert "L05" in codes(check_segment(seg("Chapter 5"), tr("فصل ۶")))
    assert "L05" not in codes(check_segment(seg("Chapter 5"), tr("فصل ۵")))
    assert "L05" not in codes(check_segment(seg("Chapter 5"), tr("فصل پنجم")))
    assert "L05" not in codes(check_segment(seg("19th century"), tr("قرن نوزدهم")))
    assert "L05" in codes(check_segment(seg("19th century"), tr("قرن بیستم")))
    assert "L05" not in codes(check_segment(seg("no numbers"), tr("سلام")))


def test_l06_punctuation():
    assert "L06" in codes(check_segment(seg("hello"), tr("سلام,دنیا")))
    assert "L06" in codes(check_segment(seg("hello"), tr("سلام...")))
    assert "L06" not in codes(check_segment(seg("hello"), tr("سلام، دنیا…")))


def test_l07_zwnj():
    assert "L07" in codes(check_segment(seg("hello"), tr("او می رود")))
    assert "L07" in codes(check_segment(seg("hello"), tr("کتاب ها")))
    assert "L07" not in codes(check_segment(seg("hello"), tr("او می‌رود")))


def test_l08_length_drift():
    source = "one two three four five six seven eight nine ten"
    assert "L08" in codes(check_segment(seg(source), tr("کوتاه")))
    assert "L08" not in codes(
        check_segment(seg(source), tr("یک دو سه چهار پنج شش هفت هشت نه ده"))
    )


def test_l09_repetition():
    sentence = "این یک جمله تکراری است"
    book = LintBook(
        segments=[
            LintSegment(id=f"c001.s000{index}", source_text="x") for index in range(1, 4)
        ],
        translations={f"c001.s000{index}": tr(sentence) for index in range(1, 4)},
    )
    assert "L09" in codes(check_book(book))
    clean = LintBook(
        segments=[seg("a")],
        translations={"c001.s0001": tr("یک جمله یکتا و کاملاً متفاوت")},
    )
    assert "L09" not in codes(check_book(clean))


def test_l10_residual_english():
    assert "L10" in codes(
        check_segment(seg("hello"), tr("این یک جمله This is a long English run است"))
    )
    assert "L10" not in codes(check_segment(seg("hello"), tr("ISBN 978-0-00-000000-0")))
    assert "L10" not in codes(check_segment(seg("hello"), tr("سلام دنیا")))


def test_l11_entity_carryover():
    glossary = [GlossaryTerm(en="Santiago", fa="سانتیاگو", kind="character")]
    assert "L11" in codes(
        check_segment(seg("Santiago went home."), tr("او به خانه رفت."), glossary)
    )
    assert "L11" not in codes(
        check_segment(seg("Santiago went home."), tr("سانتیاگو به خانه رفت."), glossary)
    )


def test_l12_name_variants():
    glossary = [GlossaryTerm(en="Santiago", fa="سانتیاگو", kind="character")]
    book = LintBook(
        segments=[LintSegment(id="c001.s0001", source_text="Santiago")],
        translations={"c001.s0001": tr("سانتیاگوو")},
        glossary=glossary,
    )
    assert "L12" in codes(check_book(book))


def test_l13_refusal():
    assert "L13" in codes(check_segment(seg("hello"), tr("I can't translate this")))
    assert "L13" not in codes(check_segment(seg("hello"), tr("سلام")))


def test_l14_translationese():
    assert "L14" in codes(check_segment(seg("hello"), tr("این می‌باشد")))
    assert "L14" in codes(check_segment(seg("hello"), tr("لازم به ذکر است که خوب است")))
    assert "L14" not in codes(check_segment(seg("hello"), tr("این خوب است")))


def test_l15_style_drift():
    chapter = LintChapter(
        idx=1,
        segments=[LintSegment(id="c001.s0001"), LintSegment(id="c001.s0002")],
        translations={
            "c001.s0001": tr("یک. دو. سه."),
            "c001.s0002": tr("چهار. پنج. شش."),
        },
        baseline={"mean_sentence_words": 10.0, "dialogue_ratio": 0.5},
    )
    assert "L15" in codes(check_chapter(chapter))


def test_l16_meta_commentary():
    assert "L16" in codes(check_segment(seg("hello"), tr("ترجمه: سلام")))
    assert "L16" in codes(check_segment(seg("one two"), tr("یک دو سه چهار پنج شش")))
    assert "L16" not in codes(check_segment(seg("hello"), tr("سلام")))


def test_l17_verse():
    source = "line one\nline two\nline three"
    assert "L17" in codes(check_segment(seg(source, kind="verse"), tr("یک خط")))
    assert "L17" not in codes(check_segment(seg(source, kind="verse"), tr("یک\nدو\nسه")))


def test_l18_window_structure():
    window = LintWindow(
        id="c001.w001",
        segments=[LintSegment(id="c001.s0001"), LintSegment(id="c001.s0002")],
    )
    assert "L18" in codes(check_window(window, {"c001.s0001": tr("سلام")}))
    assert "L18" not in codes(
        check_window(
            window, {"c001.s0001": tr("سلام"), "c001.s0002": tr("دنیا")}
        )
    )


def test_l19_footnotes():
    broken = LintChapter(
        idx=1,
        segments=[
            LintSegment(id="c001.s0001", source_html='<p>See <a href="#fn1">1</a></p>'),
        ],
        translations={"c001.s0001": tr("ببینید")},
    )
    assert "L19" in codes(check_chapter(broken))
    fixed = LintChapter(
        idx=1,
        segments=[
            LintSegment(id="c001.s0001", source_html='<p>See <a href="#fn1">1</a></p>'),
            LintSegment(
                id="c001.s0002", kind="footnote", source_html='<aside id="fn1">note</aside>'
            ),
        ],
        translations={"c001.s0001": tr("ببینید"), "c001.s0002": tr("یادداشت")},
    )
    assert "L19" not in codes(check_chapter(fixed))


def test_l20_heading_caption():
    assert "L20" in codes(check_segment(seg("Chapter One", kind="heading"), tr("فصل یک.")))
    assert "L20" not in codes(check_segment(seg("Chapter One", kind="heading"), tr("فصل یک")))
    assert "L20" in codes(check_segment(seg("A caption", kind="caption"), tr("")))
