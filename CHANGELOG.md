# Changelog

All notable changes to Shahrzad are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Dialogue attribution now follows Persian order in the translator, literary
  editor, read-through and judge prompts: `بریگز پرسید: «چقدر؟»`, never the
  English postposed `«چقدر؟» بریگز پرسید.` Unattributed reply lines use the
  Persian dialogue dash (`— «...»`). L14 also warns on postposed tags.
- Renamed the project and Python package from Tarjoman to Shahrzad. The CLI is
  now `shz`; environment variables use the `SHAHRZAD_` prefix.
- Exported books now end with the project URL
  (`https://github.com/kavir1698/translator_agent`) instead of an automatic
  translator credit. `book.translator` defaults to empty; `--translator` still
  records an explicit human credit in the metadata.

### Fixed

- Inline emphasis no longer loses the space before the italic run: translator
  sentinel-edge spaces are preserved, dropped boundary spaces are restored from
  the source nodes, and export repairs existing stored translations.

### Added

- **Live translation progress:** `shz translate` and `shz retranslate` render a
  live progress bar on stderr with the current chapter and phase
  (drafting/reviewing/revising/repairing/checking), windows completed, segments
  translated and cost so far. It is enabled automatically in an interactive
  terminal and can be forced with `--progress` or silenced with `--no-progress`.
  Pipeline callers can subscribe via the new `progress=` callback on
  `run_translate`; display failures never abort a run.

## [1.0.0] - 2026-11-05

First v1 release: an offline-verified, local English → Iranian Persian literary
translation pipeline with a resumable CLI, cost controls, review workflow and
open export formats.

### Added

- **Ingest:** EPUB 3/2 (preferred), text-layer PDF with an import-quality score,
  DOCX, TXT and Markdown readers; deterministic chapterization, optional
  `structure.yaml` overrides, paragraph segmentation, inline-markup nodes with
  sentinel round-trip preservation, source normalization and stable ids.
- **Persian module:** normalization of Arabic-script variants, Persian digits,
  punctuation and ZWNJ; deterministic linters L01–L20; translationese,
  protected-Latin and stopword data files.
- **Analysis:** book brief (auto-approved by default), optional manual brief
  gate, per-chapter summaries, entity extraction, proper-noun list and the
  optional glossary modes `off`, `provided` and `auto`.
- **Translation:** deterministic window planner, budgeted context pack, draft →
  fidelity critic + literary editor → reviser loop, targeted repair ladder,
  caching, response accounting, risk scores, append-only translation versions,
  resume and optional parallel chapters.
- **Anchor calibration:** `shz anchors propose/approve/reject/list` and optional
  style anchors for chapters 2+.
- **Review:** `shz review build` writes a self-contained RTL `review/report.html`
  and `review/flags.csv`; `shz review apply` accepts `corrections.jsonl` as human
  versions with stale-version protection.
- **Audit:** whole-book consistency auditor, deterministic glossary/name audit,
  source-free Persian read-through, targeted audit repairs and
  `runs/<id>/report.md` with cost, adherence and repair tables.
- **Export:** EPUB 3 RTL with bundled Vazirmatn WOFF2 and an internal validator,
  DOCX RTL with tables/verse/endnotes, Markdown, plain text, optional bilingual
  HTML and optional LibreOffice PDF conversion. Outputs are written atomically.
- **CLI:** `shz init`, `inspect`, `analyze`, `glossary`, `anchors`, `translate`,
  `retranslate`, `qa`, `audit`, `review`, `export`, `estimate` and `doctor`.
  Stable exit codes: `0` success, `1` runtime failure, `2` usage/config,
  `3` budget pause, `4` incomplete translation.
- **`shz doctor`:** Python/SQLite/package versions, writable books directory,
  bundled export fonts, workspace SQLite `quick_check`, schema version,
  book/chapter/segment counts, artifact presence, prompts, keys (presence only),
  optional model pings, tokenizer calibration and optional tool checks.
- **Configuration:** documented defaults, fully commented per-book examples,
  nested `SHAHRZAD_*` environment overrides and rejected unknown keys.
- **Documentation:** a v1 user guide with the install → init → (optional)
  analyze/approve → translate → audit → (optional) review → export tutorial.
- **CI:** GitHub Actions workflow for `ruff`, `mypy`, the offline pytest suite
  and `uv build`, with no secrets and no provider calls.
- **Acceptance test:** `tests/integration/test_tutorial_e2e.py` exercises the
  documented offline path for both an EPUB and a text-layer PDF, including
  export validation and the no-key `shz doctor` contract.

### Changed

- Package version is `1.0.0` in `pyproject.toml` and `shahrzad.__version__`.
- The export completeness gate now raises the exit-`4` incomplete error,
  matching the documented CLI contract, while remaining a `RuntimeFailure`
  subclass for existing callers.
- README was rewritten from a build handoff into an accurate user guide while
  retaining links to the frozen specification and provenance context.

### Fixed

- Documentation no longer implies that an evaluation command ships: there is no
  `shz eval` in this build. The M10 harness, judge, MQM/BWS packets and model
  bake-off are explicitly deferred.
- `shz doctor --json` keeps its `{"checks": [...], "failed": bool}` shape while
  reporting the new checks.

### Notes

- **M10 evaluation and bake-off are deferred.** Mechanical QA ships; the quality
  judge, calibration ratings, evaluation reports and `shz bakeoff`/`shz eval` do
  not.
- **No real-provider or public-domain-novella run was performed in this
  environment.** Network and real-key execution were out of scope for this
  build; `shz doctor` checks key presence only. Do not treat the mock-provider
  acceptance suite as evidence of real literary quality; a human reader remains
  the acceptance gate, and the manual real-provider command is documented in the
  README.
- The fixture generator produces synthetic, project-authored text. It is not a
  public-domain novella and must not be described as one.
- Releasing `v1.0.0` includes creating the git tag `v1.0.0`; the tag is a release
  step and is intentionally **not** created by this working-tree change.
- Known limitations: DOCX footnotes are endnotes; LibreOffice PDF output is
  readable but not print-ready; external epubcheck is optional; no OCR.
