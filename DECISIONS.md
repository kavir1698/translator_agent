# Implementation decisions

One line per choice where the spec was silent or the simplest option was chosen. Milestone prefix.

- M0: The project lives at the spec-folder root (`pyproject.toml` + `src/tarjoman/`), so `books/` and `configs/` match the planned paths.
- M0: Provider calls are synchronous (`Provider.complete` exactly as plan §9.1); pipeline concurrency will be a thread pool over independent windows, not an asyncio rewrite.
- M0: `BaseSettings` source order is env → dotenv → init(YAML) → secrets, because plan §4 requires env to override YAML.
- M0: Model tiers carry their own `pricing_usd_per_mtok`; `deepseek-v4-pro` is priced at 8× flash (peak $1.20/M input, $4.80/M output) so `tj estimate` reproduces the plan's $4–6 quality-mode band. Replace with the official tariff when published.
- M0: `tj estimate` falls back to a documented 90k-word profile when no book workspace exists; it never contacts a provider.
- M0: Cache hits are reported as `LLMResult(cached_tokens=input tokens, cost_usd=0, finish_reason="cache")`; the agents layer records them as `cached=1`.
- M0: `tests/fixtures` starts empty; M1's fixture builder will generate books there. No third-party text is shipped.
- M0: `tj doctor --models` only pings when an API key is present; without one it reports a warning and still exits 0 (acceptance requirement).
- M0: Prompt P13 is extracted verbatim for reference but not wired (post-v1, per PROMPTS.md).

## M1 — ingest and segmentation
- M1: Non-translatable blocks (image, `<hr>`, pre, nav) are preserved in `source/blocks.json` and are not rows in `segments`; `segments` holds translatable blocks only, so coverage/segment counts stay meaningful.
- M1: A block's inline nodes carry a hidden `tag~N` occurrence marker in `path` steps so sibling elements of the same tag rebuild with the right boundaries; the marker never reaches HTML (the tag is parsed back out).
- M1: A table block is one segment in v1 (`source_text` = rows joined with ` | `), while the full table HTML is preserved in its node list for byte-exact round-trip. Cell-level segments are deferred to M4; recorded here because plan 6.0.6 prefers cells.
- M1: PDF lines are clustered by baseline (box bottom), not box top, because ascenders/descenders otherwise split one visual line; paragraph breaks come from vertical gaps and indentation.
- M1: `tj init` reads `artifacts/structure.yaml` when present (created by hand or by `--force` re-init); there is no `--structure` flag in the v1 CLI surface.
- M1: DOCX hyperlinks and footnotes are not exposed by python-docx; they are documented limitations (the reader records skipped elements in `metadata.json`).
- M1: `tj inspect --diff` compares the stored SHA-256 with the extracted original file; per-segment diffing arrives with `tj retranslate --stale-only` in M6.

## M2 — Persian module
- M2: `normalize_fa` takes `strip_edges`; markup text runs are normalized with `strip_edges=False` so spaces between a sentinel and its run are not eaten.
- M2: L14 density thresholds (را/که/passive/initial-و) apply only to segments of >= 100 words; short segments produced constant false positives and the blacklist + `می‌باشد` checks still run at any length.
- M2: The ASCII comma conversion ignores commas between Persian digits (so `1,234` keeps its separator) but still fixes list punctuation after a Persian digit.
- M2: L19 is approximated from chapter HTML (every `href="#x"` needs an `id="x"` and every footnote segment a translation) until footnote target ids are modelled in M4.

## M3 — analysis, brief and optional glossary
- M3: `tj analyze --review-brief` (and `pipeline.analyze.brief_approval: manual`) writes the draft and exits 3 (paused), so the next command is explicit (`tj analyze --approve`); it never sits interactively.
- M3: `--draft-only` stops after `brief.draft.yaml` and does not freeze even when approval is `auto`.
- M3: An approved `brief.yaml` is reused on later `tj analyze` runs; `--only <chapter>` limits summaries/entities to that chapter but still uses the whole-book brief.
- M3: Chapters over 8,000 tokens are summarized in two chunk calls, then P02 is called once more with both chunk summaries as the data source to merge them; if that merge call still fails schema validation, a deterministic union/join fallback is used.
- M3: The deterministic proper-noun scan keeps capitalized tokens seen mid-sentence or at least twice, with a small English common-word blocklist; LLM entities are merged by `rapidfuzz` token-set ratio ≥ 90 and the most frequent surface wins.
- M3: In `auto` mode only proposals at or above `glossary.auto_approve_min_confidence` are approved automatically; lower-confidence character/place entries stay `proposed` and are gate-1b blockers.
- M3: `provided` mode parses YAML (`entries:`) or CSV (`en,fa[,kind,...]`), forces entries to `approved`, and writes `artifacts/glossary.yaml`; `sync` re-imports that artifact honoring each entry's status and marks DB entries missing from the artifact `deprecated` (it is the source of truth).
- M3: Glossary suggestions are stored with `status=pending`; `tj glossary pending` lists them. Promotion into an approved entry stays a human action.
- M3: `BookBrief.register` now uses `Field(...)`; without it Pydantic silently defaulted the field to the metaclass `register` method and omitted it from the JSON schema's required list.
- M3: CLI offline runs can set `TARJOMAN_MOCK_FIXTURES=<dir>` to use `MockProvider`; the real provider remains the default.

## Model policy (product owner, 2026-11-05)
- M3+: `deepseek-v4-pro` is never used. DeepSeek V4.1 Flash is the default draft/critic model (DeepSeek API, `DEEPSEEK_API_KEY`).
- The separate quality/reviewer/judge model is `gpt-6-luna` via OpenRouter (`OPENROUTER_API_KEY`), configured per tier in `configs/models/deepseek.yaml`.
- Model tiers may declare `provider`, `base_url` and `api_key_env`; `LiteLLMProvider` resolves the key and endpoint per tier, so one profile can span two providers.
- `provider.api_base` was removed: endpoint selection belongs to the model profile tier, avoiding a second conflicting source of truth.
- `gpt-6-luna` pricing in the profile is an estimation placeholder; replace it with the current OpenRouter tariff before relying on `tj estimate` for quality-mode budgets.

## Post-review fixes
- Force re-init archives the entire workspace under `backups/<slug>.bak-<ts>/` and removes all DB/WAL/SHM files before rebuilding; the same-hash early return is bypassed when `--force` is set.
- Ingest writes are one transaction and a `source/init.json` marker records hash + counts; incomplete workspaces are rejected instead of being reported as complete.
- Schema bootstrap and migrations are transactional; migration backups use `Connection.backup()` after a WAL checkpoint.
- Env overrides are string-first (`off` stays a string); `.env` is loaded from the project root; `project.books_dir` is threaded through every path helper.
- Sentinels now rebuild translated HTML in model order; `<br>` atomics become newlines in `fa_text`; noterefs are atomic with `target_id`; HTML comments are ignored.
- L10 uses token boundaries; Lint dedupe keys map to the DB `seg:`/`ch:` namespaces; L06 shares the punctuation letter class; L04 uses the shared banned-codepoint table.
- EPUB non-linear spine items are read (after linear body) and skipped documents are reported in metadata; entries have decompression limits. Markdown link URLs are scheme-checked and attribute-escaped.
## M4 — translation core
- M4: `plan_windows` operates on paragraph segments; an oversized segment becomes a single-segment window (the >1,800-token intra-paragraph split stays deferred). Table blocks are rendered with explicit ` | ` cell separators and newline row separators so markers cannot fuse cells; L08 is skipped for table blocks (cell concatenation makes a word ratio meaningless).
- M4: The translator uses the `balanced` tier in `economy`/`standard` and reserves the `quality` tier for A6–A8 in `quality` mode. This matches the plan's cost table and keeps `gpt-6-luna` off every ordinary draft.
- M4: Markup/id validation runs before caching; invalid responses are retried once uncached with a stricter sentinel reminder, then stored best-effort with an L02 issue (never silently dropped). A `finish_reason=length` or repeated schema failure falls back to per-segment calls.
- M4: `done` is decided by open `qa`-stage error issues only; critic/audit issues flag risk and review but do not move a segment out of `done`. Coverage is L01-based (current translation, non-empty, ≥1 Persian letter) and scoped to the selected chapters, so `--chapters 1` reports 100% when chapter 1 is complete.
- M4: Cross-turn issue hygiene: `upsert_issue` reopens a resolved issue but never clears a human waiver; lint issues are reconciled (resolved) when the current translation no longer reproduces them; window-scope issues are reconciled per window; critique issues are resolved when an accepted revision/repair applies them.
- M4: `--no-cache` bypasses reads but still writes; `--refresh` deletes this book's cache rows first. A rerun with nothing to translate makes no calls and reports a 100% cache-hit rate.
- M4: Prompt templates now wrap optional blocks in Jinja conditionals so an empty glossary/cast/anchor block leaves no empty heading for the model to fill.
- M4: No real-provider smoke run was performed in this environment; the mock-provider acceptance run is the recorded evidence.
## M4.5 — anchor calibration
- M4.5: `tj anchors propose` selects up to 3 deterministic pairs from the first body chapter (longest non-dialogue prose, highest dialogue ratio, most inline emphasis; tie-break by segment id). Approval requires 2–3 pairs and records `anchors_hash` in `books.metadata_json` and the run artifact hashes.
- M4.5: Missing anchors only warn when the selected scope includes chapter 2+; `--require-anchors` turns that into a configuration error. `--no-anchors` skips them entirely; combining the two flags is a usage error.
## M5 — critics, reviser, budget
- M5: Critics run sequentially (not in parallel) to keep the synchronous provider/cache path deterministic; the budget guard is re-checked before each critic, revision, repair and ledger call.
- M5: Unverified critic quotes are downgraded to `warn` and are never sent to the reviser, preventing repair livelock. Accepted revisions clear their critique issues; rejected revisions keep the draft and record `revision_regressed`.
## M6 — orchestration
- M6: Resume is status + cache based: done segments are never re-requested, a new run reuses cached responses, and window-scope/translate-stage issues are resolved when their work succeeds. Per-window exceptions are recorded as `WINDOW_FAILED` and the chapter continues.
- M6: Parallel chapters share one SQLite connection under the existing write lock and serialize reads too; ledger curation is skipped in parallel mode (the initial warning now matches behavior) and when a chapter translated zero segments.
- M6: Ledger writes merge the existing human-editable `ledger.jsonl` over DB rows and replace the file atomically. Stale glossary redo retranslates all stale segments because an approved-glossary change invalidates the stable cached prefix for every window; above 300 entries this would become window-targeted.
- M6: `--replan` retranslates the selected scope as new versions (append-only history) instead of only bypassing the plan-hash check.
- M6: `runs/<id>/report.md` includes status, tariff, cost mode, coverage, cache hit rate, cost by agent, cost/1k words and the 20 riskiest segments; the manifest reads actual prompt versions and token calibrations, and marks a no-brief run `experimental`.
## M7 — review workflow
- M7: `tj review build` always rebuilds `review/report.html` from the live DB after a scoped `run_qa`; the report lists every in-scope segment (flagged rows carry their QA risk/codes) so reviewers can inspect beyond the flagged sample, with client-side filters.
- M7: Corrections are parsed and markup-validated in full before the first DB write; any invalid line aborts the whole file with the 1-based line, segment id and problem codes.
- M7: Accepted corrections insert `pass=human`/`model=human` versions and keep the current version's glossary hash/terms; status moves to `done` only when no open `qa` error/critical remains, otherwise `flagged`, via a BFS over `_SEGMENT_TRANSITIONS`.
- M7: `needs_work` writes `REVIEW_NEEDS_WORK` (stage `qa`, severity `warn`) after the re-lint (so `run_qa` cannot resolve it immediately) and forces the segment to `flagged`.
- M7: Accepted segments' issues are resolved with `resolved_by`/`resolution` from the correction; failed lint reopens reproduced issues through the existing dedupe path and leaves them open.
- M7: `flags.csv` keeps the exact 10-column header and is updated atomically; a corrected segment missing from the file gets a row appended so `reviewed=true` always exists.
## M8a — EPUB 3 + Markdown/plain-text export
- M8a: Images and covers are extracted from the original EPUB/DOCX (`zipfile`), deduped by SHA-256 and named `cover.<ext>` / `img-<sha256[:10]>.<ext>`; a missing/broken source yields no assets and never fails the export. `find_asset` normalizes backslashes, URL-decodes, strips `./`/`../`, then falls back to the basename.
- M8a: Export determinism uses `MAX(translations.created_at)` for `dcterms:modified`, a stable `urn:uuid:uuid5(slug + source_sha256)`, `ZipInfo.date_time=(1980,1,1,0,0,0)`, stored `mimetype`, deflated entries sorted by name, and UTF-8 bytes with `\n` for Markdown/plain text. Wall clock is used only when no translation exists (the incomplete path).
- M8a: The completeness gate checks non-skipped body segments for a current translation containing a Persian letter, enforces `review.min_coverage_for_export`, and counts open `error`/`critical` issues; `--allow-incomplete` writes a warning page containing the exact text `TRANSLATION INCOMPLETE`.
- M8a: Fonts are the committed Vazirmatn WOFF2 files plus `OFL.txt`, embedded without obfuscation; nothing is downloaded at export or test time.
- M8a: Per-chapter endnotes keep original footnote ids where present; body noteref anchors get a synthetic `noteref-<id>` only on first use so the endnote backlink has a real target. All XHTML roots carry `lang="fa" xml:lang="fa" dir="rtl"`.
- M8a: Latin bidi isolation wraps runs containing Latin letters (with their adjacent punctuation) in `<span class="ltr" dir="ltr" lang="en">` and leaves existing `.ltr`/`dir="ltr"`/`lang="en"` content untouched.
- M8a: The internal EPUB validator always runs (mimetype order/compression, manifest/spine hrefs, XHTML XML + language/dir, unique ids, local href targets, no scripts/remote resources, required `dc:*` and `dcterms:modified`, spine rtl). External `epubcheck` runs only when the binary exists and fails on `ERROR`; otherwise it is skipped silently.
- M8a: `docx` is accepted by format validation but requesting it raises `ConfigError("docx export is added in M8b")`; the default configured format list skips it with a warning. `--pdf` and `--bilingual` are accepted but warn that their effects arrive in M8b rather than being silently ignored.

## M8b — DOCX, PDF conversion, bilingual HTML
- M8b: DOCX is python-docx only; every paragraph gets `w:bidi`, every run gets `w:rtl`, `w:rFonts` for `ascii`/`hAnsi`/`cs`, and matching `w:sz`/`w:szCs`; document defaults use `w:lang w:bidi="fa"`. A5 is the default page (A4/Letter supported, unknown values fall back to A5 silently), with mirrored margins and 16–18 mm margins.
- M8b: Chapters start on a new page; the title comes from `chapter_title` and a leading heading segment identical to it is skipped, matching EPUB. Footnotes become per-chapter endnotes under `یادداشت‌ها` because python-docx has no footnote API (README limitation). Core properties use the book title/author/translator and `generated_at`; after `document.save`, the archive is rewritten with `ZipInfo.date_time=(1980,1,1,0,0,0)`, so two exports of unchanged state are byte-identical.
- M8b: Table segments are rebuilt into real Word tables; verse uses explicit run line breaks; quotes are indented, captions italic, and `strong`/`b`/`em`/`i`/`u` inline markup is preserved. Links/noterefs render as translated text. Images embed inline at native size capped at 120 mm, or fall back to alt text; the cover image is page one when extracted assets provide it.
- M8b: `--pdf` uses LibreOffice via `soffice --headless --convert-to pdf --outdir "<out>" "<docx>"` with a 300 s timeout and no shell. A missing or failing `soffice` never aborts the export; the warning always carries the exact manual command and the "readable but not print-ready" caveat. `--pdf` forces a DOCX even when `docx` is not in `--formats`.
- M8b: Bilingual HTML is one self-contained file (inline CSS/JS, no `http(s)` resources, no script `src`) with `dir="rtl" lang="fa"`, per-chapter sections, two-cell `source | translation` rows carrying `dir="ltr" lang="en"` / `dir="rtl"`, issue badges from `segment.issues`, and text/chapter/only-with-issues filters plus arrow-key navigation. `export.bilingual_html.include_source: false` omits the source column.
- M8b: `docx` is now a normal format and the default format list continues to come from `settings.export.formats`; the M8a placeholder warning/error is removed.

## M9 — audit and read-through
- M9: `--sample` windows are chosen at evenly spaced indexes across `plan_windows`; each `[segment]\nEN/FA` pair is capped at ~1,500 characters so the A10 prompt stays bounded.
- M9: A10 findings with a segment id outside the book become chapter-scope findings; A10 `ledger_corrections` persist as chapter-scope `LEDGER_CORRECTION` warns and the verdict is kept in run stats.
- M9: The deterministic name audit marks a variant `error` only when the approved form never appears in that chapter but does appear in another; otherwise `warn`. Ratios 88–93.9 set `ambiguous: true` / `needs_adjudication: true` and stay `warn` for human review rather than entering the automatic repair queue; no extra LLM call is added (A10 sees the same ledger/glossary in its normal pass). A missing character/place name is a `warn` (Persian drops repeated subjects), while a missing term/unit is an `error` and enters the repair queue.
- M9: A11 never creates a translation version; read-through issues are evidence only and are explicitly excluded from the repair queue.
- M9: Repairs consume only open `stage='audit'` error/critical findings with a real segment id, skip current `human` versions and byte-identical output, and reject empty output, new markup problems or extra lint errors.
- M9: Adherence % is the share of audited chapters with no open `error`/`critical` issue from `audit`/`readthrough`; it is not a translation-quality score.
- M9: `--readthrough/--no-readthrough` is tri-state; when omitted the setting `pipeline.passes.readthrough` decides.

## Post-review hardening (M7/M8)
- Review state: `run_qa` now accepts `QaOptions.preserve_flags`; review build/apply use it. Existing `reviewed`/`reviewer_note` values are merged by segment id, out-of-scope rows are retained, and `flags.csv` is replaced atomically (temp + replace). `corrections` reuses the shared `FLAGS_COLUMNS`/`build_flag_row` instead of duplicating the schema.
- Corrections: payloads now carry `translation_version`; review stores it on each report row and `tj review apply` refuses a stale version unless `--force` is passed. Reviewer notes are CSV-formula-neutralized. The review report is written atomically.
- Export safety: EPUB/DOCX/Markdown/plain/bilingual outputs are written to temp paths and replaced only after success; EPUB validates the temp archive, and a present-but-broken epubcheck logs a warning instead of aborting. PDF conversion uses a pristine temp outdir so an old `<stem>.pdf` cannot be reported as current.
- Source safety: EPUB XHTML serialization now allowlists tags/attributes, drops active content and unsafe URLs, and the internal validator rejects active content; source ZIP reads use per-entry/total/ratio budgets from `assets.py`. Markdown URLs are scheme-checked and percent-encoded.
- Bidi source sanitization keeps heading/verse/table/footnote structure; chapter endnotes remain per-chapter.
- `books.title_fa`/`book.title_fa` is now honored for `dc:title` and DOCX core properties; export warns when it is unset instead of silently claiming an all-Persian title.
- Performance: export loading batches segments/current translations/issues, review loading batches translations/issues and reuses the glossary-match map, and each segment HTML fragment is parsed once during rendering. EPUB validation still re-reads the finished archive by design (archive integrity), and DOCX deterministic rewriting streams one entry at a time.

## M11 — polish and release
- M11: Version is `1.0.0` in `pyproject.toml` and `tarjoman.__version__`. Creating the git tag `v1.0.0` is a release step; no tag or commit is created by the M11 working-tree change.
- M11: README is a user guide, not a build handoff. The tutorial order is install → init → optional analyze/approve → translate → audit → optional review → export; the zero-config path is `init → translate → export`, with `tj analyze` documented as optional because translate otherwise uses the built-in default brief and marks the run experimental.
- M11: The command reference is generated from the real typer surface. `tj eval` is absent and the README states that the M10 evaluation harness/bake-off is deferred. `tj translate --review-brief` is documented as a no-op warning; the real manual brief gate is `tj analyze --review-brief` followed by `tj analyze --approve`.
- M11: `tj doctor` adds package version, required-package imports/versions (`python-docx`, `pypdfium2`, `litellm`), a writable/created `books` directory, bundled export-font presence, and workspace checks (`PRAGMA quick_check`, schema version, book/chapter/segment counts, optional artifacts). Missing `brief.yaml`/`glossary.yaml` are informational; missing anchors are a warning only when the workspace has 2+ chapters. Every new check is offline; warnings still exit 0 and the `{"checks", "failed"}` JSON shape is unchanged.
- M11: The export completeness gate raises `IncompleteError` (`RuntimeFailure` subclass) so export exits 4, matching the documented exit-code contract; `IncompleteError` previously existed but was unused and not a `RuntimeFailure`.
- M11: `configs/sample.book.yaml` is a fully commented, valid per-book override covering every `Settings` section; two minimal examples add an economy budget and auto-glossary mode. No new config keys were introduced.
- M11: CI is GitHub Actions on `ubuntu-latest` with `astral-sh/setup-uv`, `uv sync --frozen --dev`, `ruff check`, `mypy`, and `pytest -q -m "not online"`; a dependent job runs `uv build`. CI uses no secrets and makes no provider calls; online tests remain opt-in.
- M11: **No real-provider or public-domain-novella run was performed here**; network and real-key execution were out of scope for this build (key presence is checked only, never printed). The acceptance evidence is the offline mock-provider suite (`tests/integration/test_tutorial_e2e.py` covers both EPUB and text-layer PDF), not a real literary-quality or cost claim. The fixture generator's text is synthetic, not a public-domain novella.
- M11: M10 is deliberately skipped and no `tj eval` command was added; mechanical QA (linters, audit/read-through) is the shipped quality tooling.
