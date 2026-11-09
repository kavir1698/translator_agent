# Shahrzad (شهرزاد)

**English** | [فارسی](README.fa.md)

Shahrzad is a local, agentic English → Iranian Persian literary translator for a personal library. Point it at an EPUB (preferred) or a text-layer PDF, or at DOCX/TXT/Markdown, and it produces open, editable Persian files: EPUB 3 (RTL), DOCX (RTL), Markdown and plain text. Bilingual HTML and a DOCX→PDF conversion are optional.

It is a local CLI (`shz`) with no server, no web UI and no telemetry. Translation is fully automatic by default; brief approval, glossary review, anchor calibration and human review are optional gates. The default budget ceiling is **$10 per book**, and `shz estimate` shows the projected cost before anything is spent.

## v1 scope

| Area | Shipped in v1.0.0 |
|---|---|
| Language | English → Iranian standard Persian (`fa-IR`) only |
| Input | EPUB 3/2 (preferred), text-layer PDF (best-effort), DOCX, TXT, Markdown |
| Output | EPUB 3 RTL, DOCX RTL, Markdown, plain text; optional bilingual HTML; optional PDF via LibreOffice |
| Automation | Fully unattended by default; gates exist only behind flags |
| Glossary | Optional: `off` (default), `provided`, or `auto` |
| Cost | Three modes (`economy`, `standard`, `quality`), `$10` default cap, budget pause/resume |
| Interface | Local CLI only |
| Evaluation | Mechanical lint/QA ships. The M10 evaluation harness and model bake-off (`shz eval`) are **post-v1 in this build** |

Not supported in v1: scanned PDFs/OCR, DRM-protected files, print-ready/typeset PDF, other language pairs, Dari/Tajik, a server or web UI.

## How it works

Shahrzad is a deterministic, resumable pipeline, not a free-roaming agent loop. The processing commands run a fixed sequence of passes over a SQLite database, and every LLM call is schema-validated, cached, attributed and costed. The frozen specification is in [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) §3.4 and §6.3–§6.7; this section is the working summary.

### Pipeline at a glance

```mermaid
flowchart LR
    A[shz init<br/>extract · chapterize · segment] --> B[shz analyze (optional)<br/>brief · summaries · entities · glossary]
    A --> C[shz translate<br/>windows · draft · critique · revise · repair]
    B --> C
    C --> D[shz audit<br/>deterministic audit · sampled consistency · read-through]
    D --> E[shz review (optional)<br/>human edits]
    E --> F[shz export<br/>EPUB · DOCX · MD · TXT]
    D --> F
```

| Stage | What it does | LLM calls |
|---|---|---|
| `shz init` | Extracts the source, chapterizes it, splits paragraphs into stable-ID segments, records inline markup and token estimates, and writes `book.db`. PDFs also get an import-quality score. | None |
| `shz analyze` (optional) | Builds the book brief, chapter summaries, entity list and optional glossary candidates. Without an approved brief, translation uses conservative built-in defaults and marks the run `experimental`. | A1–A4 |
| `shz translate` | Plans windows, assembles a context pack per window, then drafts, critiques, revises, repairs and lints. Also curates the story ledger at chapter close. | A5–A9 |
| `shz audit` | Runs the deterministic glossary/name audit, samples windows for the consistency auditor, reads each Persian chapter without the source, and repairs targeted audit errors. | A10–A11, A8 for repairs |
| `shz review` (optional) | Builds a self-contained HTML report of flagged segments; applying corrections stores them as human versions and re-runs QA. | None |
| `shz export` | Read-only transform of the database into EPUB 3 RTL, DOCX RTL, Markdown and plain text (plus optional bilingual HTML and PDF), with internal validation. | None |

### Units: segments and windows

The paragraph is the unit of translation; a **segment** is a paragraph, heading, verse stanza, table row or footnote text. Stable IDs (`c003.s0042`) keep source and translation aligned and make every pass resumable.

A **window** is the deterministic chunk an LLM call sees (the code's term for a "chunk"):

- up to **8 segments** and **900 source tokens** by default (`pipeline.window.max_paragraphs`, `max_source_tokens`);
- a segment is never split across windows; a single oversized segment becomes its own window;
- a chapter's first heading travels with at least one following block;
- adjacent dialogue paragraphs are kept together when the budget allows;
- each window carries the previous **2** segments (source **and** finished Persian) as context only — the overlap is never retranslated.

Windows are planned in reading order and hashed. Within a chapter they run **strictly sequentially**, because each window's context includes the previous window's output; chapters can be parallelized with `--parallel-chapters`. The plan is stored in the run manifest, and a changed plan is rejected unless you pass `shz translate --replan`.

### The translation loop (per window)

```text
build context pack
  → A5 draft
  → A6 fidelity critic  +  A7 literary editor
  → A8 revise (quoted issues only, ≤ 1 round)
  → deterministic repair (lint errors only, ≤ 2 rounds)
  → segment + window lint, risk score, status update
  → next window
```

1. **Context pack.** A stable prefix (system rules, brief card, approved glossary, voice anchors) plus volatile context (chapter summary, rolling chapter summary, relevant ledger entries, previous two segments, the window source). The prefix is byte-stable so provider prompt caching can hit.
2. **Draft.** A5 translates the window's pending segments in one structured `TranslatorOutput` response; segment IDs must match exactly, and missing IDs are re-requested rather than invented.
3. **Review.** A6 (fidelity) and A7 (literary editor) each run **once per window** and return per-segment, quoted findings. This is the same chunk boundary as the draft, so review does not happen per chapter or per paragraph.
4. **Revise.** A8 applies only concrete, verified findings, once per window by default (`critics.max_revision_rounds: 1`). A regression guard rejects a revision that introduces markup damage, empty text, new lint errors or a length outlier, keeping the draft.
5. **Repair.** Deterministic lint errors trigger up to two targeted A8 repair rounds per window, covering only the failing segments.
6. **Check.** Segment- and window-scope linters run, issues are deduplicated, a risk score is computed, and each segment moves from `drafted` to `done` when clean, or to `flagged`/`failed` when it needs attention. A revision or repair adds a new version without discarding the history.

`economy` mode skips A7 and runs A6 only when a length error (`L08`) is present; `standard` runs both critics; `quality` moves A6/A7/A8 to the quality model tier. Every pass is a fresh request — context is rebuilt from the database, never carried as conversation history — so a window can be retried or resumed without cross-contamination.

### Continuity between windows and chapters

- **Brief** — frozen voice, register, address and footnote policy (A1).
- **Glossary** — approved terms are injected into the pack; when off, a deterministic proper-noun list is used instead.
- **Chapter summary** — English summary of the chapter (A2).
- **Rolling summary** — the last few Persian sentences of the chapter, refreshed periodically.
- **Ledger** — characters, terms, decisions and open questions; A9 curates it at chapter close and the packer injects only the slice relevant to the current window. Ledger chaining is disabled in `--parallel-chapters` mode, where continuity relies on the precomputed summaries instead.
- **Anchors** — optional approved source/Persian voice pairs from chapter 1.

After a chapter, L15 compares its Persian style metrics (segment length, dialogue ratio, unique-word ratio) with the book's running baseline and warns on style drift.

### Translation review vs. audit review

These are deliberately different scopes:

- **Inside `shz translate`, reviewing and revising are per window** — A6, A7 and A8 see one window at a time, with the same overlap and context pack as the draft.
- **`shz audit` is chapter/book scoped.** A10 samples up to `--sample N` windows per chapter (default 3) for consistency findings; A11 receives the **entire Persian chapter** with no source to catch translationese and rhythm problems; deterministic audits scan every segment; audit repairs are per segment.
- **`shz review` is per flagged segment.** A human edits individual segments in `review/report.html`; `shz review apply` inserts a `pass=human` version and re-runs the linters.

### State, resumability and determinism

`book.db` is the source of truth: chapters, segments, append-only translation versions (draft, revision, repair, human), open issues, glossary, ledger, run statistics and the response cache. Re-running an unchanged window reuses cached responses and creates no new versions. Every call records model, prompt hash, tokens, cost and latency, so a run can pause at the budget cap and resume exactly where it stopped. In sequential mode, the same database, artifacts and config produce the same window plan, cache keys and exports.

For the exhaustive specification — context-pack budgets, linter catalogue, severity policy, state machines and export rules — see [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) and [`SCHEMAS.md`](SCHEMAS.md).

## Requirements

- **Python 3.12** and [`uv`](https://docs.astral.sh/uv/).
- API keys for the providers in your model profile: `DEEPSEEK_API_KEY` (default `deepseek-flash`) and `OPENROUTER_API_KEY` (the configured `quality` tier). Keys are read from env or `.env`; they are never printed.
- Optional: **LibreOffice** (`soffice`) for `--pdf`, and **Java + epubcheck** for external EPUB validation.
- No database server, model download or system service is required. The test suite and `shz doctor` work offline without keys.

## Install

```bash
uv sync
cp .env.example .env          # PowerShell: Copy-Item .env.example .env
uv run shz doctor
```

Fill in the keys you use in `.env` (it is gitignored). `shz doctor` reports whether each key is present without printing its value, and exits `0` when the only findings are warnings.

## Tutorial: EPUB or text-layer PDF

The commands below are the exact v1 surface. You can run them from the project root with `uv run shz …`, or just `shz …` after activating the environment.

### 1. Create a workspace

```bash
uv run shz init book.epub --slug my-book
# or, for a PDF with a selectable text layer:
uv run shz init book.pdf --slug my-book
```

`shz init` extracts the source, chapterizes it, segments paragraphs, and writes `books/my-book/book.db`. It never calls an LLM.

For a PDF, `shz init` records an import-quality score (0–1). Run `shz inspect` to see it:

```bash
uv run shz --book my-book inspect
```

A score below `0.6` blocks translation by default. Fix the chapter boundaries in `books/my-book/artifacts/structure.yaml`, or proceed deliberately with `shz --book my-book translate --accept-pdf-quality`. There is no OCR: an image-only PDF fails with `PDF_NO_TEXT_LAYER`.

### 2. Optional: analysis, brief approval and glossary

The zero-config path does not require this step: `shz translate` falls back to a conservative built-in brief (and marks the run `experimental`). Analysis usually improves voice and consistency.

```bash
uv run shz --book my-book analyze
```

`pipeline.analyze.brief_approval` defaults to `auto`, so this validates and freezes `books/my-book/artifacts/brief.yaml` without asking. To review it yourself instead:

```bash
uv run shz --book my-book analyze --review-brief
# edit books/my-book/artifacts/brief.draft.yaml, then:
uv run shz --book my-book analyze --approve
```

Glossary modes are optional:

- `off` (default): no glossary and no approval step; names stay stable through a deterministic proper-noun list.
- `provided`: `shz init book.epub --glossary terms.yaml`, or set `glossary.mode: provided` and `glossary.path` in the book config.
- `auto`: `uv run shz --book my-book analyze --auto-glossary`, then review proposals with `shz --book my-book glossary review` and `shz --book my-book glossary approve`.

### 3. Estimate the cost

```bash
uv run shz --book my-book estimate
```

This prints the token table and the projected cost for `economy`, `standard` and `quality` at peak and off-peak tariffs. It makes no provider calls. Add `--chapters 3` to estimate a prefix, or `--json` for machine-readable output.

### 4. Translate

```bash
uv run shz --book my-book translate
```

This translates every chapter in reading order and resumes from the database if interrupted. While it runs, a live progress bar on stderr shows the current chapter and phase (drafting, reviewing, revising, repairing, checking), windows completed, segments translated and cost so far. It appears automatically in an interactive terminal; use `--progress` or `--no-progress` to force it on or off.

Useful flags: `--chapters 1,3-5`, `--dry-run` (plan and estimate, no calls), `--max-usd 5`, `--cost-mode economy`, `--parallel-chapters 4`, `--no-cache`, `--refresh`.

Exit codes: `0` success, `2` usage/config error, `3` paused at the budget cap (state is saved; raise the cap and re-run), `4` finished with unresolved error issues or coverage below 100% (see the run report).

### 5. Audit

```bash
uv run shz --book my-book audit
```

The audit runs sampled consistency checks, a deterministic glossary/name audit, targeted repairs, and the source-free Persian read-through. It only audits chapters that have translations. Useful flags: `--chapter 3`, `--sample 5`, `--no-readthrough`, `--max-usd 2`, `--json`.

The report is under `books/my-book/runs/<run_id>/report.md`.

The audit is diagnostic. It reports findings and applies only a limited set of targeted repairs; it does **not** approve chapters. A finished audit can still leave many open findings, and therefore the export gate closed.

### 6. Optional: human review

```bash
uv run shz --book my-book review build
# open books/my-book/review/report.html, edit flagged segments,
# download corrections.jsonl, then:
uv run shz --book my-book review apply corrections.jsonl
```

Review is never required and never blocks export by itself. `shz review build` writes a self-contained `review/report.html` and `review/flags.csv`; `shz review apply` inserts human versions, re-runs QA, and updates the flags.

### 7. Export

```bash
uv run shz --book my-book export --formats epub,docx,md,txt
```

Optional extras:

```bash
uv run shz --book my-book export --formats epub,docx --pdf
uv run shz --book my-book export --formats epub,docx --bilingual
uv run shz --book my-book export --allow-incomplete   # deliberately skips the completeness gate
```

Outputs land in `books/my-book/out/` as `<filename>.fa.epub`, `<filename>.fa.docx`, `<filename>.fa.md`, `<filename>.fa.txt`, plus `<filename>.fa.pdf` (`--pdf`) and `<filename>.bilingual.html` (`--bilingual`). `<filename>` is `export.filename`, defaulting to the workspace slug; set `book.title_fa` for the Persian metadata title and `export.filename` for a Latin transliteration of that title. The EPUB is validated internally on every export; external `epubcheck` runs only if installed. Every exported book ends with the project address, `https://github.com/kavir1698/translator_agent`; Shahrzad does not add an automatic "translated by" credit. Pass `--translator` only when a human translator should be credited in the metadata.

Standalone separator paragraphs are preserved (for example `· · ·`); inline prose ellipses are normalized to `…` according to `persian.ellipsis`.

EPUB export uses the source book's nav/NCX as the chapter structure. It merges continuation documents into one chapter, uses TOC labels for front matter, and regenerates the Contents page with valid internal links. If the source has no usable TOC it falls back to the stored chapter rows.

If the source TOC order itself is wrong, correct it with a per-book `artifacts/toc_order.yaml`; no content-specific rules are built into the program:

```yaml
moves:
  - item: "Chapter to move"
    after: "Chapter it should follow"
```

Titles are matched case- and punctuation-insensitively, and moves apply in order. Without this file, the source TOC order is preserved. This regrouping applies to the EPUB only; DOCX/Markdown/TXT keep the stored chapter order.

### 8. Quality gates, flagged segments and `--allow-incomplete`

`shz export` is a hard gate. It requires:

1. every body segment to be `done`, and
2. zero open error/critical issues.

`shz audit` is diagnostic: it can add findings faster than it repairs them, so a completed audit may still leave the gate closed (the run report shows `adherence`, `A10`/`A11` findings and `repairs`). Flagged segments, including separator-only paragraphs such as `· · ·`, count as incomplete even when they already have a Persian translation. The 100% minimum comes from `review.min_coverage_for_export`.

To clear the gate:

- `shz review build` then `shz review apply corrections.jsonl` — edit flagged segments, insert human corrections, and re-run QA.
- `shz qa accept --issue <id> --note "..." --reviewer "<name>"` — waive a finding deliberately.
- `shz export ... --allow-incomplete` — bypass both gates and write an edition marked incomplete (the EPUB gets a `TRANSLATION INCOMPLETE` page). This does **not** bypass the built-in EPUB validator; malformed XHTML, duplicate ids or dangling internal links still fail.

The translation progress bar counts every window planned for the run. The run summary counts only windows that actually translated something, so the numbers can differ (for example `421/421` on the bar and `140 windows` in the summary). Re-running a paused run starts the bar at zero again; it does not restart the book.

### 9. Example: whole book in `quality` mode, workspace beside the source

`--cost-mode` overrides the auto-selected mode for that run: `economy` skips the literary editor, `standard` is the default, and `quality` reserves the configured quality tier for the critics and reviser (the shipped profile uses `gpt-6-luna` via OpenRouter, so `OPENROUTER_API_KEY` is required). Run `shz estimate` first; it prints all three modes at both tariffs.

By default the workspace lives under `books/` in the project. To keep the database, run artifacts and output beside the source file, point `project.books_dir` at that folder with an absolute path, then pass that folder to `--out`:

```powershell
$InputDir = "D:\library"
$Book     = Join-Path $InputDir "book.epub"
$env:SHAHRZAD_PROJECT__BOOKS_DIR = $InputDir

uv run shz init $Book --slug book
uv run shz --book book estimate
uv run shz --book book translate --cost-mode quality --max-usd 10
uv run shz --book book audit
uv run shz --book book export --formats epub,docx,md,txt --out $InputDir
```

This creates the workspace at `D:\library\book\` and writes `book.fa.epub`, `book.fa.docx`, `book.fa.md` and `book.fa.txt` into `D:\library\`. Run the commands from the project root; `SHAHRZAD_PROJECT__BOOKS_DIR` applies to the current shell session. Add `SHAHRZAD_PROJECT__BOOKS_DIR=D:\library` to `.env` to make it the default. If the export gate reports unresolved issues, resolve or waive them (`shz qa`) or pass `--allow-incomplete` deliberately.

In bash the same override is:

```bash
export SHAHRZAD_PROJECT__BOOKS_DIR="$HOME/library"
uv run shz init "$HOME/library/book.epub" --slug book
uv run shz --book book translate --cost-mode quality --max-usd 10
uv run shz --book book export --formats epub,docx,md,txt --out "$HOME/library"
```

## Command reference

Global options: `--book/-b <slug>` (required when more than one workspace exists) and `--verbose/-v`. Global options must come **before** the command, e.g. `shz --book my-book translate`.

| Command | Flags / subcommands |
|---|---|
| `shz doctor` | `--json`, `--models`, `--tokenizer`, `--epubcheck` |
| `shz estimate` | `--chapters N`, `--words N`, `--json` |
| `shz init <FILE>` | `--slug`, `--rights-basis`, `--translator`, `--glossary <FILE>`, `--force` |
| `shz inspect` | `--json`, `--diff` |
| `shz analyze` | `--sample-budget N`, `--draft-only`, `--approve`, `--only N`, `--review-brief`, `--auto-glossary` |
| `shz translate` | `--chapters`, `--segments`, `--windows`, `--dry-run`, `--exit-after N`, `--max-usd N`, `--cost-mode`, `--allow-overrun`, `--force`, `--no-cache`, `--refresh`, `--parallel-chapters N`, `--calibrate`, `--replan`, `--only-stale`, `--require-anchors`, `--no-anchors`, `--review-brief`, `--local-only`, `--allow-cloud`, `--accept-pdf-quality`, `--progress/--no-progress` |
| `shz retranslate` | `--segments`, `--chapter`, `--window`, `--stale-only`, `--progress/--no-progress` |
| `shz audit` | `--chapter N`, `--sample N`, `--readthrough/--no-readthrough`, `--max-usd N`, `--json` |
| `shz export` | `--formats epub,docx,md,txt`, `--out <DIR>`, `--allow-incomplete`, `--generated-at <ISO-8601>`, `--pdf`, `--bilingual` |
| `shz glossary` | `list`, `review`, `approve`, `reject`, `edit`, `add`, `import`, `export`, `sync`, `pending` |
| `shz qa` | `--chapter`, `--group consistency\|literary\|loss\|all`, `--stale-glossary`, `--json`; `accept`, `resolve` |
| `shz anchors` | `propose`, `approve`, `reject`, `list` |
| `shz review` | `build`, `apply` |

`shz translate --review-brief` only prints a warning; it does not gate the run. The real manual brief gate is `shz analyze --review-brief` (which exits `3`) followed by `shz analyze --approve`. `--allow-cloud` is accepted for licensed-book runs but is informational in v1; `--local-only` requires `provider.profile: local`.

There is **no `shz eval` command in this build**. The evaluation harness, judge and model bake-off described in the implementation plan are deferred post-v1.

### `shz glossary`

| Subcommand | Arguments / flags |
|---|---|
| `list` | `--status proposed\|approved\|rejected\|deprecated`, `--json` |
| `review` | `--min-confidence N`, `--json` |
| `approve` | `[IDS...]`, `--min-confidence N` |
| `reject` | `IDS...` |
| `edit` | `ENTRY_ID`, `--fa`, `--en`, `--kind`, `--aliases-fa`, `--note` |
| `add` | `--en`, `--fa`, `--kind`, `--aliases-en`, `--aliases-fa`, `--note` |
| `import` | `FILE`, `--honor-status` |
| `export` | `--out <FILE>` |
| `sync` | — |
| `pending` | `--json` |

Glossary kinds: `character`, `place`, `organization`, `term`, `motif`, `form_of_address`, `title`, `unit`.

### `shz qa`, `shz anchors`, `shz review`

| Command | Arguments / flags |
|---|---|
| `shz qa accept` | `--issue ID`, `--note TEXT`, `--reviewer NAME` |
| `shz qa resolve` | `--issue ID` |
| `shz anchors approve` | `--ids a,b` (default: all candidates) |
| `shz anchors reject` | `--ids a,b` |
| `shz review build` | `--chapter N`, `--out <FILE>` |
| `shz review apply` | `CORRECTIONS.jsonl`, `--dry-run`, `--reviewer NAME`, `--force` |

## Configuration

Configuration is merged in this order (later wins):

1. `configs/default.yaml` — every section and default value.
2. `books/<slug>/config.yaml` — optional per-book overrides.
3. `SHAHRZAD_*` environment variables — nested keys use `__`, e.g. `SHAHRZAD_BUDGET__MAX_USD=5`.
4. CLI flags.

`.env` is loaded from the project root (or `SHAHRZAD_HOME`) and never overrides an already-set environment variable. Unknown config keys are rejected.

Reference files:

- [`configs/default.yaml`](configs/default.yaml) — the complete default configuration.
- [`configs/sample.book.yaml`](configs/sample.book.yaml) — a fully commented per-book example.
- [`configs/examples/book-economy.yaml`](configs/examples/book-economy.yaml) — tighter budget/economy mode.
- [`configs/examples/book-auto-glossary.yaml`](configs/examples/book-auto-glossary.yaml) — auto glossary mode.
- [`configs/models/deepseek.yaml`](configs/models/deepseek.yaml) — default model profile.
- [`configs/models/local.yaml`](configs/models/local.yaml) — optional local OpenAI-compatible endpoint (Ollama/vLLM).
- [`configs/models/example-other.yaml`](configs/models/example-other.yaml) — template for another provider.

Key defaults:

| Key | Default | Meaning |
|---|---|---|
| `book.translator` | `""` | Optional human translator metadata; book outputs always show the project URL |
| `glossary.mode` | `off` | `off`, `provided` or `auto` |
| `pipeline.analyze.brief_approval` | `auto` | Set `manual` (or use `--review-brief`) to gate the brief |
| `provider.thinking` | `disabled` | Keep disabled in v1; per-agent overrides require M10 evidence |
| `pipeline.cost_mode` | `auto` | `auto`, `economy`, `standard` or `quality` |
| `budget.max_usd` | `10` | Per-book ceiling; pauses with exit `3` when reached |
| `review.min_coverage_for_export` | `1.0` | Completeness gate |
| `export.formats` | `[epub, docx, md, txt]` | Default output formats |

## Workspace layout

`shz init` creates `books/<slug>/`:

| Path | Contents |
|---|---|
| `book.db` | SQLite source of truth: chapters, segments, translation versions, issues, glossary, runs, cache |
| `config.yaml` | Optional per-book overrides (for example `glossary.mode: provided`) |
| `source/` | Untouched original file plus extracted chapters/images and ingest metadata |
| `artifacts/` | Human-facing knowledge: `brief.yaml`/`brief.draft.yaml`, `glossary.yaml`/`glossary.candidates.yaml`, `proper_nouns.yaml`, `ledger.jsonl`, optional `anchors.yaml`, `structure.yaml`, `chapter_summaries.jsonl` |
| `runs/<run_id>/` | Per-run `manifest.json`, `calls.jsonl` and `report.md` (cost, coverage, repairs, riskiest segments) |
| `review/` | `report.html`, `flags.csv` and imported `corrections.jsonl` |
| `out/` | Final EPUB/DOCX/Markdown/plain-text files, optional PDF and bilingual HTML |

Exports are read-only transforms of the database and artifacts; they never mutate `book.db`. Re-initializing with `shz init --force` archives the old workspace under `backups/<slug>.bak-<timestamp>/` before rebuilding it.

## Cost and model policy

- Default draft and critic model: `deepseek-flash`, thinking **off**. `standard` mode is the default; an average 90k-word book is roughly $1–2.
- `quality` mode reserves the configured `quality` tier for the critics/reviser. In the shipped profile that tier is `gpt-6-luna` via OpenRouter (`OPENROUTER_API_KEY`).
- `economy` mode skips the literary editor; `auto` selects a mode from the book size and cap.
- `shz estimate` reports all three modes at both tariffs before spending. Off-peak pricing is reported, but Shahrzad never waits for it.
- Every LLM call is cached, attributed and costed. Re-running an unchanged book makes no new paid calls.
- On reaching `budget.max_usd`, a run persists its state and exits `3`. Resume with the same command after raising `budget.max_usd`/`--max-usd` or passing `--allow-overrun`.

## Troubleshooting

| Symptom | What to do |
|---|---|
| `shz doctor` warns `api_key ... missing` | Add `DEEPSEEK_API_KEY` and/or `OPENROUTER_API_KEY` to `.env`. Offline tests use the mock provider and need no key. |
| PDF import quality is low or translation refuses it | Run `shz inspect`, fix `artifacts/structure.yaml`, or pass `shz translate --accept-pdf-quality`. Scanned/image-only PDFs are unsupported (no OCR). |
| `epubcheck` not found | External validation is optional. The internal validator always runs and the export is still produced. Install Java + epubcheck to enable the external check. |
| `soffice` not found / `--pdf` skipped | Install LibreOffice or run the printed manual `soffice --headless --convert-to pdf …` command. The result is readable but not print-ready. |
| Run exits `3` (budget paused) | State is saved. Raise `budget.max_usd` or `--max-usd`, or pass `--allow-overrun`, then re-run the same command. |
| Export blocks and exits `4` (incomplete) | The gate needs every body segment `done` and zero open error/critical issues. Flagged segments (including `· · ·` separators) count as incomplete. Resolve or waive them with `shz review` / `shz qa accept`, or pass `--allow-incomplete` to write a marked incomplete edition. See section 8. |
| Audit finished but export is still blocked | Expected: audit reports and partially repairs; it does not approve chapters. Read `runs/<run_id>/report.md`, apply corrections or waive findings, then export. `--allow-incomplete` bypasses the gate but keeps the notice. |
| EPUB validation failed (duplicate id, not well-formed XML, dangling `href="#..."`) | The built-in validator runs on every export and is not bypassed by `--allow-incomplete`. Fix the reported chapter markup or the exporter, re-run, and inspect with `scripts/run_epubcheck.sh`. |
| `WinError 5` renaming `ledger.jsonl.tmp` to `ledger.jsonl` | A Windows sync/antivirus lock on the workspace. The current build retries and records a warning instead of failing the chapter; if it persists, pause Koofr/antivirus or exclude `book/` during the run. |
| `multiple book workspaces found` | Pass the global option before the command, e.g. `shz --book my-book translate`, or set one workspace per project root. |
| Provider/network failure | Check the key and connectivity; the run is resumable and cached calls are not re-billed. |

## Limitations

- DOCX footnotes are emitted as per-chapter **endnotes** (python-docx has no footnote API).
- The optional PDF is a LibreOffice conversion: readable, but **not print-ready** (limited kashida/justification and page mirroring).
- External `epubcheck` is optional; the built-in validator checks archive structure, manifest/spine, RTL language tags, links and active content.
- No OCR and no scanned PDFs. Text-layer PDF import is best-effort and reports an import-quality score.
- Verse is preserved best-effort; tables are translated as row-oriented segments.
- EPUB footnotes are per-chapter endnotes. If automatic chapter splitting puts a footnote in a later chapter than its referrer, that backlink falls back to the chapter top instead of the earlier chapter.
- Separator-only paragraphs such as `· · ·` can be left as review-required prose, so they may count against export coverage even though the separator itself is correct. `--allow-incomplete` or a review pass clears the gate.
- Literary quality is screened by mechanical QA; a human Persian reader remains the only real quality gate.
- The M10 evaluation harness, quality judge, MQM/BWS packets and model bake-off are **not shipped** in v1.0.0; `shz eval` does not exist.

## Development

```bash
uv run ruff check
uv run mypy
uv run pytest -q                    # offline suite (no keys, no network)
uv run pytest -q -m online          # opt-in real-provider tests; needs keys and network
```

CI runs lint, type checking and the offline suite only (`pytest -q -m "not online"`). The fixtures are synthetic, project-authored books generated by `tests/fixtures/build.py`; no third-party text is shipped.

The specification files are frozen inputs for development (name references were updated for the Shahrzad rename):

- [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) — product, architecture, pipeline, milestones M0–M11.
- [`PROMPTS.md`](PROMPTS.md) — the production prompt contracts.
- [`SCHEMAS.md`](SCHEMAS.md) — enums, models, SQLite DDL and artifact formats.
- [`DECISIONS.md`](DECISIONS.md) — implementation choices and deviations.
- [`REVIEW.md`](REVIEW.md) — adversarial review and resolution log.

The shipped pipeline is a deterministic, typed sequence of passes, not a free-roaming agent loop; subagents belong to the build workflow, never to runtime translation.

## Provenance and rights

Shahrzad assumes the source file is supplied for **private use**. `book.rights_basis` defaults to `user_supplied` and is recorded in the EPUB colophon and run manifests; it is informational only and never gates a step. Publishing a translation of an in-copyright book requires the rightsholder's permission. The software does not clear rights and is not legal advice.

## Manual real-provider check

With keys configured, the remaining manual acceptance step is one real chapter through the default profile:

```bash
uv run shz init book.epub --slug smoke
uv run shz --book smoke translate --chapters 1 --max-usd 1
uv run shz --book smoke inspect
```

Record the reported cost, coverage and finish reasons. `shz doctor` reports key presence only (never the value). The offline mock-provider suite, including `tests/integration/test_tutorial_e2e.py` for EPUB and text-layer PDF, remains the automated acceptance evidence.
