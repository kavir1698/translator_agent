# Tarjoman

Tarjoman is a local, agentic English → Iranian Persian literary translator for a personal library. Point it at an EPUB (preferred) or a text-layer PDF, or at DOCX/TXT/Markdown, and it produces open, editable Persian files: EPUB 3 (RTL), DOCX (RTL), Markdown and plain text. Bilingual HTML and a DOCX→PDF conversion are optional.

It is a local CLI (`tj`) with no server, no web UI and no telemetry. Translation is fully automatic by default; brief approval, glossary review, anchor calibration and human review are optional gates. The default budget ceiling is **$10 per book**, and `tj estimate` shows the projected cost before anything is spent.

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
| Evaluation | Mechanical lint/QA ships. The M10 evaluation harness and model bake-off (`tj eval`) are **post-v1 in this build** |

Not supported in v1: scanned PDFs/OCR, DRM-protected files, print-ready/typeset PDF, other language pairs, Dari/Tajik, a server or web UI.

> **Status of this build:** v1.0.0 is verified offline (mock provider, synthetic fixtures). No real-provider run and no public-domain-novella run was performed in this environment; the manual real-provider step is documented below and has not been executed here.

## Requirements

- **Python 3.12** and [`uv`](https://docs.astral.sh/uv/).
- API keys for the providers in your model profile: `DEEPSEEK_API_KEY` (default `deepseek-flash`) and `OPENROUTER_API_KEY` (the configured `quality` tier). Keys are read from env or `.env`; they are never printed.
- Optional: **LibreOffice** (`soffice`) for `--pdf`, and **Java + epubcheck** for external EPUB validation.
- No database server, model download or system service is required. The test suite and `tj doctor` work offline without keys.

## Install

```bash
uv sync
cp .env.example .env          # PowerShell: Copy-Item .env.example .env
uv run tj doctor
```

Fill in the keys you use in `.env` (it is gitignored). `tj doctor` reports whether each key is present without printing its value, and exits `0` when the only findings are warnings.

## Tutorial: EPUB or text-layer PDF

The commands below are the exact v1 surface. You can run them from the project root with `uv run tj …`, or just `tj …` after activating the environment.

### 1. Create a workspace

```bash
uv run tj init book.epub --slug my-book
# or, for a PDF with a selectable text layer:
uv run tj init book.pdf --slug my-book
```

`tj init` extracts the source, chapterizes it, segments paragraphs, and writes `books/my-book/book.db`. It never calls an LLM.

For a PDF, `tj init` records an import-quality score (0–1). Run `tj inspect` to see it:

```bash
uv run tj --book my-book inspect
```

A score below `0.6` blocks translation by default. Fix the chapter boundaries in `books/my-book/artifacts/structure.yaml`, or proceed deliberately with `tj --book my-book translate --accept-pdf-quality`. There is no OCR: an image-only PDF fails with `PDF_NO_TEXT_LAYER`.

### 2. Optional: analysis, brief approval and glossary

The zero-config path does not require this step: `tj translate` falls back to a conservative built-in brief (and marks the run `experimental`). Analysis usually improves voice and consistency.

```bash
uv run tj --book my-book analyze
```

`pipeline.analyze.brief_approval` defaults to `auto`, so this validates and freezes `books/my-book/artifacts/brief.yaml` without asking. To review it yourself instead:

```bash
uv run tj --book my-book analyze --review-brief
# edit books/my-book/artifacts/brief.draft.yaml, then:
uv run tj --book my-book analyze --approve
```

Glossary modes are optional:

- `off` (default): no glossary and no approval step; names stay stable through a deterministic proper-noun list.
- `provided`: `tj init book.epub --glossary terms.yaml`, or set `glossary.mode: provided` and `glossary.path` in the book config.
- `auto`: `uv run tj --book my-book analyze --auto-glossary`, then review proposals with `tj --book my-book glossary review` and `tj --book my-book glossary approve`.

### 3. Estimate the cost

```bash
uv run tj --book my-book estimate
```

This prints the token table and the projected cost for `economy`, `standard` and `quality` at peak and off-peak tariffs. It makes no provider calls. Add `--chapters 3` to estimate a prefix, or `--json` for machine-readable output.

### 4. Translate

```bash
uv run tj --book my-book translate
```

This translates every chapter in reading order and resumes from the database if interrupted. While it runs, a live progress bar on stderr shows the current chapter and phase (drafting, reviewing, revising, repairing, checking), windows completed, segments translated and cost so far. It appears automatically in an interactive terminal; use `--progress` or `--no-progress` to force it on or off.

Useful flags: `--chapters 1,3-5`, `--dry-run` (plan and estimate, no calls), `--max-usd 5`, `--cost-mode economy`, `--parallel-chapters 4`, `--no-cache`, `--refresh`.

Exit codes: `0` success, `2` usage/config error, `3` paused at the budget cap (state is saved; raise the cap and re-run), `4` finished with unresolved error issues or coverage below 100% (see the run report).

### 5. Audit

```bash
uv run tj --book my-book audit
```

The audit runs sampled consistency checks, a deterministic glossary/name audit, targeted repairs, and the source-free Persian read-through. It only audits chapters that have translations. Useful flags: `--chapter 3`, `--sample 5`, `--no-readthrough`, `--max-usd 2`, `--json`.

The report is under `books/my-book/runs/<run_id>/report.md`.

### 6. Optional: human review

```bash
uv run tj --book my-book review build
# open books/my-book/review/report.html, edit flagged segments,
# download corrections.jsonl, then:
uv run tj --book my-book review apply corrections.jsonl
```

Review is never required and never blocks export by itself. `tj review build` writes a self-contained `review/report.html` and `review/flags.csv`; `tj review apply` inserts human versions, re-runs QA, and updates the flags.

### 7. Export

```bash
uv run tj --book my-book export --formats epub,docx,md,txt
```

Optional extras:

```bash
uv run tj --book my-book export --formats epub,docx --pdf
uv run tj --book my-book export --formats epub,docx --bilingual
uv run tj --book my-book export --allow-incomplete   # deliberately skips the completeness gate
```

Outputs land in `books/my-book/out/` as `<slug>.fa.epub`, `<slug>.fa.docx`, `<slug>.fa.md`, `<slug>.fa.txt`, plus `<slug>.fa.pdf` (`--pdf`) and `<slug>.bilingual.html` (`--bilingual`). The EPUB is validated internally on every export; external `epubcheck` runs only if installed.

### 8. Example: whole book in `quality` mode, workspace beside the source

`--cost-mode` overrides the auto-selected mode for that run: `economy` skips the literary editor, `standard` is the default, and `quality` reserves the configured quality tier for the critics and reviser (the shipped profile uses `gpt-6-luna` via OpenRouter, so `OPENROUTER_API_KEY` is required). Run `tj estimate` first; it prints all three modes at both tariffs.

By default the workspace lives under `books/` in the project. To keep the database, run artifacts and output beside the source file, point `project.books_dir` at that folder with an absolute path, then pass that folder to `--out`:

```powershell
$InputDir = "D:\library"
$Book     = Join-Path $InputDir "book.epub"
$env:TARJOMAN_PROJECT__BOOKS_DIR = $InputDir

uv run tj init $Book --slug book
uv run tj --book book estimate
uv run tj --book book translate --cost-mode quality --max-usd 10
uv run tj --book book audit
uv run tj --book book export --formats epub,docx,md,txt --out $InputDir
```

This creates the workspace at `D:\library\book\` and writes `book.fa.epub`, `book.fa.docx`, `book.fa.md` and `book.fa.txt` into `D:\library\`. Run the commands from the project root; `TARJOMAN_PROJECT__BOOKS_DIR` applies to the current shell session. Add `TARJOMAN_PROJECT__BOOKS_DIR=D:\library` to `.env` to make it the default. If the export gate reports unresolved issues, resolve or waive them (`tj qa`) or pass `--allow-incomplete` deliberately.

In bash the same override is:

```bash
export TARJOMAN_PROJECT__BOOKS_DIR="$HOME/library"
uv run tj init "$HOME/library/book.epub" --slug book
uv run tj --book book translate --cost-mode quality --max-usd 10
uv run tj --book book export --formats epub,docx,md,txt --out "$HOME/library"
```

## Command reference

Global options: `--book/-b <slug>` (required when more than one workspace exists) and `--verbose/-v`. Global options must come **before** the command, e.g. `tj --book my-book translate`.

| Command | Flags / subcommands |
|---|---|
| `tj doctor` | `--json`, `--models`, `--tokenizer`, `--epubcheck` |
| `tj estimate` | `--chapters N`, `--words N`, `--json` |
| `tj init <FILE>` | `--slug`, `--rights-basis`, `--translator`, `--glossary <FILE>`, `--force` |
| `tj inspect` | `--json`, `--diff` |
| `tj analyze` | `--sample-budget N`, `--draft-only`, `--approve`, `--only N`, `--review-brief`, `--auto-glossary` |
| `tj translate` | `--chapters`, `--segments`, `--windows`, `--dry-run`, `--exit-after N`, `--max-usd N`, `--cost-mode`, `--allow-overrun`, `--force`, `--no-cache`, `--refresh`, `--parallel-chapters N`, `--calibrate`, `--replan`, `--only-stale`, `--require-anchors`, `--no-anchors`, `--review-brief`, `--local-only`, `--allow-cloud`, `--accept-pdf-quality`, `--progress/--no-progress` |
| `tj retranslate` | `--segments`, `--chapter`, `--window`, `--stale-only`, `--progress/--no-progress` |
| `tj audit` | `--chapter N`, `--sample N`, `--readthrough/--no-readthrough`, `--max-usd N`, `--json` |
| `tj export` | `--formats epub,docx,md,txt`, `--out <DIR>`, `--allow-incomplete`, `--generated-at <ISO-8601>`, `--pdf`, `--bilingual` |
| `tj glossary` | `list`, `review`, `approve`, `reject`, `edit`, `add`, `import`, `export`, `sync`, `pending` |
| `tj qa` | `--chapter`, `--group consistency\|literary\|loss\|all`, `--stale-glossary`, `--json`; `accept`, `resolve` |
| `tj anchors` | `propose`, `approve`, `reject`, `list` |
| `tj review` | `build`, `apply` |

`tj translate --review-brief` only prints a warning; it does not gate the run. The real manual brief gate is `tj analyze --review-brief` (which exits `3`) followed by `tj analyze --approve`. `--allow-cloud` is accepted for licensed-book runs but is informational in v1; `--local-only` requires `provider.profile: local`.

There is **no `tj eval` command in this build**. The evaluation harness, judge and model bake-off described in the implementation plan are deferred post-v1.

### `tj glossary`

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

### `tj qa`, `tj anchors`, `tj review`

| Command | Arguments / flags |
|---|---|
| `tj qa accept` | `--issue ID`, `--note TEXT`, `--reviewer NAME` |
| `tj qa resolve` | `--issue ID` |
| `tj anchors approve` | `--ids a,b` (default: all candidates) |
| `tj anchors reject` | `--ids a,b` |
| `tj review build` | `--chapter N`, `--out <FILE>` |
| `tj review apply` | `CORRECTIONS.jsonl`, `--dry-run`, `--reviewer NAME`, `--force` |

## Configuration

Configuration is merged in this order (later wins):

1. `configs/default.yaml` — every section and default value.
2. `books/<slug>/config.yaml` — optional per-book overrides.
3. `TARJOMAN_*` environment variables — nested keys use `__`, e.g. `TARJOMAN_BUDGET__MAX_USD=5`.
4. CLI flags.

`.env` is loaded from the project root (or `TARJOMAN_HOME`) and never overrides an already-set environment variable. Unknown config keys are rejected.

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
| `glossary.mode` | `off` | `off`, `provided` or `auto` |
| `pipeline.analyze.brief_approval` | `auto` | Set `manual` (or use `--review-brief`) to gate the brief |
| `provider.thinking` | `disabled` | Keep disabled in v1; per-agent overrides require M10 evidence |
| `pipeline.cost_mode` | `auto` | `auto`, `economy`, `standard` or `quality` |
| `budget.max_usd` | `10` | Per-book ceiling; pauses with exit `3` when reached |
| `review.min_coverage_for_export` | `1.0` | Completeness gate |
| `export.formats` | `[epub, docx, md, txt]` | Default output formats |

## Workspace layout

`tj init` creates `books/<slug>/`:

| Path | Contents |
|---|---|
| `book.db` | SQLite source of truth: chapters, segments, translation versions, issues, glossary, runs, cache |
| `config.yaml` | Optional per-book overrides (for example `glossary.mode: provided`) |
| `source/` | Untouched original file plus extracted chapters/images and ingest metadata |
| `artifacts/` | Human-facing knowledge: `brief.yaml`/`brief.draft.yaml`, `glossary.yaml`/`glossary.candidates.yaml`, `proper_nouns.yaml`, `ledger.jsonl`, optional `anchors.yaml`, `structure.yaml`, `chapter_summaries.jsonl` |
| `runs/<run_id>/` | Per-run `manifest.json`, `calls.jsonl` and `report.md` (cost, coverage, repairs, riskiest segments) |
| `review/` | `report.html`, `flags.csv` and imported `corrections.jsonl` |
| `out/` | Final EPUB/DOCX/Markdown/plain-text files, optional PDF and bilingual HTML |

Exports are read-only transforms of the database and artifacts; they never mutate `book.db`. Re-initializing with `tj init --force` archives the old workspace under `backups/<slug>.bak-<timestamp>/` before rebuilding it.

## Cost and model policy

- Default draft and critic model: `deepseek-flash`, thinking **off**. `standard` mode is the default; an average 90k-word book is roughly $1–2.
- `quality` mode reserves the configured `quality` tier for the critics/reviser. In the shipped profile that tier is `gpt-6-luna` via OpenRouter (`OPENROUTER_API_KEY`).
- `economy` mode skips the literary editor; `auto` selects a mode from the book size and cap.
- `tj estimate` reports all three modes at both tariffs before spending. Off-peak pricing is reported, but Tarjoman never waits for it.
- Every LLM call is cached, attributed and costed. Re-running an unchanged book makes no new paid calls.
- On reaching `budget.max_usd`, a run persists its state and exits `3`. Resume with the same command after raising `budget.max_usd`/`--max-usd` or passing `--allow-overrun`.

## Troubleshooting

| Symptom | What to do |
|---|---|
| `tj doctor` warns `api_key ... missing` | Add `DEEPSEEK_API_KEY` and/or `OPENROUTER_API_KEY` to `.env`. Offline tests use the mock provider and need no key. |
| PDF import quality is low or translation refuses it | Run `tj inspect`, fix `artifacts/structure.yaml`, or pass `tj translate --accept-pdf-quality`. Scanned/image-only PDFs are unsupported (no OCR). |
| `epubcheck` not found | External validation is optional. The internal validator always runs and the export is still produced. Install Java + epubcheck to enable the external check. |
| `soffice` not found / `--pdf` skipped | Install LibreOffice or run the printed manual `soffice --headless --convert-to pdf …` command. The result is readable but not print-ready. |
| Run exits `3` (budget paused) | State is saved. Raise `budget.max_usd` or `--max-usd`, or pass `--allow-overrun`, then re-run the same command. |
| Export blocks and exits `4` (incomplete) | Finish translation, resolve/waive open error issues (`tj qa`/`tj review`), or pass `--allow-incomplete` to write a marked incomplete edition. |
| `multiple book workspaces found` | Pass the global option before the command, e.g. `tj --book my-book translate`, or set one workspace per project root. |
| Provider/network failure | Check the key and connectivity; the run is resumable and cached calls are not re-billed. |

## Limitations

- DOCX footnotes are emitted as per-chapter **endnotes** (python-docx has no footnote API).
- The optional PDF is a LibreOffice conversion: readable, but **not print-ready** (limited kashida/justification and page mirroring).
- External `epubcheck` is optional; the built-in validator checks archive structure, manifest/spine, RTL language tags, links and active content.
- No OCR and no scanned PDFs. Text-layer PDF import is best-effort and reports an import-quality score.
- Verse is preserved best-effort; tables are translated as row-oriented segments.
- Literary quality is screened by mechanical QA, not proven by CI. A human Persian reader remains the only real quality gate.
- The M10 evaluation harness, quality judge, MQM/BWS packets and model bake-off are **not shipped** in v1.0.0; `tj eval` does not exist.

## Development

```bash
uv run ruff check
uv run mypy
uv run pytest -q                    # offline suite (no keys, no network)
uv run pytest -q -m online          # opt-in real-provider tests; needs keys and network
```

CI runs lint, type checking and the offline suite only (`pytest -q -m "not online"`). The fixtures are synthetic, project-authored books generated by `tests/fixtures/build.py`; no third-party text is shipped.

The frozen specification files are read-only inputs for development:

- [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) — product, architecture, pipeline, milestones M0–M11.
- [`PROMPTS.md`](PROMPTS.md) — the production prompt contracts.
- [`SCHEMAS.md`](SCHEMAS.md) — enums, models, SQLite DDL and artifact formats.
- [`DECISIONS.md`](DECISIONS.md) — implementation choices and deviations.
- [`REVIEW.md`](REVIEW.md) — adversarial review and resolution log.

The shipped pipeline is a deterministic, typed sequence of passes, not a free-roaming agent loop; subagents belong to the build workflow, never to runtime translation.

## Provenance and rights

Tarjoman assumes the source file is supplied for **private use**. `book.rights_basis` defaults to `user_supplied` and is recorded in the EPUB colophon and run manifests; it is informational only and never gates a step. Publishing a translation of an in-copyright book requires the rightsholder's permission. The software does not clear rights and is not legal advice.

## Manual real-provider check (not run here)

With keys configured, the remaining manual acceptance step is one real chapter through the default profile:

```bash
uv run tj init book.epub --slug smoke
uv run tj --book smoke translate --chapters 1 --max-usd 1
uv run tj --book smoke inspect
```

Record the reported cost, coverage and finish reasons. This check was **not** performed in this build: network and real-key execution were out of scope, so no cost or quality claim is made from it. `tj doctor` reports key presence only (never the value). The automated acceptance evidence is the offline mock-provider suite, including `tests/integration/test_tutorial_e2e.py` for EPUB and text-layer PDF.
