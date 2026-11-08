# Review & resolutions

An independent adversarial review was run against the first draft of these documents. This file is the audit trail: what was found, and where it was fixed. It is not part of the spec; read it only to understand why certain contracts look the way they do.

## Verdict

- **Draft review (snapshot 18:52):** "not implementable as-is" — 15 blocking items, 33 cross-document mismatches. Central risk: validation failed closed (severity vocabulary, sentinel × normalization ordering, exact-substring quote evidence) and an `error` issue had no accept path, so export was unreachable.
- **After fixes (this version):** all 15 blocking items are addressed below; the remaining items are non-blocking polish, tracked as backlog. The spec is implementable milestone-by-milestone.

## Blocking items → resolution

| # | Finding | Fixed in |
|---|---|---|
| 1 | Severity vocabulary mismatch (agents vs DB); `MqmType \| str` cannot be provider-simplified | `SCHEMAS.md` §1 (`AgentSeverity`, mapping, `normalize_severity`), §2; `PROMPTS.md` shared conventions; `CritiqueIssue.type: str` |
| 2 | Normalization corrupts sentinel ids | `SCHEMAS.md` §6 (exact persist order: parse → validate → normalize text runs → reassemble → strip); `IMPLEMENTATION_PLAN.md` 6.0.3 |
| 3 | P05 declares `system_rules` with no slot; "stable prefix" claim false | `PROMPTS.md` P05 (`book_rules`, cache layout paragraph, `rolling_summary`); `IMPLEMENTATION_PLAN.md` 6.4 block 1 |
| 4 | Linter API cannot express window/chapter/book checks | `IMPLEMENTATION_PLAN.md` 7.1 (four scoped functions + scope assignment) |
| 5 | DDL missing plan/glossary/segment columns | `SCHEMAS.md` §3 (`runs.window_plan_hash`, `segments.lang_mix`, `segments.split_of`, `translations.glossary_hash`, `translations.glossary_terms_json`, `glossary_suggestions`, `chapters.rolling_summary_fa`, `issues.status`, `llm_calls.retries`) |
| 6 | `fa_html` / `html_original` vs `fa_markup` / `source_html` | `IMPLEMENTATION_PLAN.md` 6.0.3, 6.0.6; `SCHEMAS.md` §6 |
| 7 | No issue accept/waive path; NULL-safe dedupe; `is_current` invariant | `IMPLEMENTATION_PLAN.md` 5.1, 5.2; `SCHEMAS.md` §3 (`issues.status`, namespaced `dedupe_key`, invariant text) |
| 8 | Exact-substring quote validation loops | `SCHEMAS.md` §2 (canonicalized matching, downgrade to warn, `unverified_quote`) |
| 9 | `issue_index` unnumbered; conflicting sentinel-order rules | `PROMPTS.md` P08 (numbered issues), P05 (move-as-a-block rule); `IMPLEMENTATION_PLAN.md` 6.0.3 (one rule) |
| 10 | Undefined P09/P11 inputs; missing block 5b | `PROMPTS.md` P09 (`chapter_excerpt_fa`, `translator_notes`), P11 (`style_state`); `IMPLEMENTATION_PLAN.md` 6.4 block 5b + `chapters.rolling_summary_fa` |
| 11 | Eviction could delete the window source | `IMPLEMENTATION_PLAN.md` 6.4 (non-evictable blocks, explicit eviction order) |
| 12 | 4,096-token output caps cause truncation | `IMPLEMENTATION_PLAN.md` 4.5 (16,384/8,192), 9.6 (pre-flight split) |
| 13 | Glossary invalidation unimplementable | `SCHEMAS.md` §3 (`glossary_hash`, `glossary_terms_json`); `IMPLEMENTATION_PLAN.md` 6.5, 6.10 |
| 14 | Gate 2 has no milestone/CLI; several flags undefined | `IMPLEMENTATION_PLAN.md` 3.3.1 (CLI surface), 6.5.1, **M4.5**; `anchors.draft.yaml` in 4.2/models; README |
| 15 | Fixtures, MockProvider, calibration set, human-eval scope undefined | `IMPLEMENTATION_PLAN.md` M1 (synthetic fixture builder), M10 (calibration + release-owner MQM); `SCHEMAS.md` §5.9 (MockProvider format); README ("what can/cannot be verified") |

## Cross-document mismatches resolved

Severity vocabularies; A7 temp 0.35; A12 temp 0.2; A3 batch unit; D11 lint range; window 4–8 and overlap 2; anchors capped at 3; cost table re-derived from the 6.4 budgets (620 calls / 2.6M in / 0.53M out); Persian token ratio stated as 1.5–2.5×; retry counts; `calls.jsonl` column names; judge dimensions and reference-free protocol; judge family resolution; issue uniqueness; L10 coverage wording; L02 severity; EPUB/DOCX language tag; byte-identical export rule (`dcterms:modified` from newest translation); read-through severity; cast-block wording; `register_target_fa` enum; `first_segment_id`; N01–N09 and `zwnj_forms.txt`; `ة` severity; verse (not "poetry") segment kind; brief example rule counts; glossary YAML (aliases, gender); malformed manifest JSON; P09 status vocabulary; address-form key normalization; P13 marked post-v1; per-book `config.yaml`; D12 eval timeline; foreign-text policy naming.

## Backlog (non-blocking, v1.1)

- DOCX footnotes are endnotes (python-docx has no footnote API) — document, don't fix.
- Verse line-count equality in A7 is a warning, not a hard constraint (translation may legitimately merge two short lines).
- `eval` judge calibration is a 20-paragraph internal set; recalibrate when the judge model changes.
- Unused/soft config keys: `budget.abort_on_exceed`, `persian.ellipsis`, `export.epub.validate_with_epubcheck` — wire or remove as encountered.
- `shz retranslate --stale-only` falls back to whole chapters when a term has no recorded window; good enough for v1.
- Parallel-chapter mode is explicit about the coherence trade-off; do not enable it by default.

## Evidence and provenance

The findings that shaped this spec (TransAgents, DelTA, Karpinska & Iyyer, LITEVAL-CORPUS, GEMBA-MQM, Persian literary failure study, OSS glossary engineering from bilingual_book_maker / TranslateBooksWithLLMs / GalTransl, EPUB 3.3 RTL, Persian normalization) are cited in `IMPLEMENTATION_PLAN.md` §2.1 and §13, with full notes in `../research/`.

## Round 2 — product-owner constraints (2026-11-05)

The product owner fixed the scope after the first review; the spec was revised accordingly (see plan §1.6 and §12.1):

- **Provider:** DeepSeek V4.1 Flash (`deepseek-flash`) is the default, with **thinking off by default** (thinking ignores temperature and bills reasoning as output). Evidence is mixed — reasoning helps context/cohesion and post-editing but plateaus on direct translation — so M10 tests low-effort thinking on critics/reviser; `deepseek-v4-pro` exists only for `quality`-mode critics and the judge.
- **Cost:** official DeepSeek tariff (cache-hit/miss, peak/off-peak) re-derives the cost model; average book ≈ $1–2 standard, ≤ $10 cap; three cost modes; tariff reported, never waited for.
- **Input:** PDF (text layer only) added as a best-effort importer with `import_quality`; EPUB stays first-class; scanned PDFs rejected, OCR out of scope.
- **Glossary:** optional (`off` default, `provided`, `auto`); the approval gate is optional; zero-config path must work.
- **Outputs:** DOCX (editable/PDF path) + EPUB + Markdown; bilingual optional; PDF via LibreOffice with an explicit "not print-ready" caveat.
- **Rights:** no gate; provenance recorded as `user_supplied` (private use).
- **Interface/language:** local CLI only; Iranian Persian only.
- **Coding style:** Kilo's `/ponytail` skill is recommended in the README handoff (YAGNI but validation-preserving); it is deliberately **not** part of the normative spec so other agents can build the same system.
