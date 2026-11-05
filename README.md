# Tarjoman — Implementation Plan handoff

**Goal:** a local, agentic English→Persian literary translator for a personal library. Input: an EPUB or a text-layer PDF. Output: open, editable Persian files (DOCX + EPUB + Markdown), optionally bilingual, convertible to PDF. Model: DeepSeek V4.1 Flash (`deepseek-flash`). Budget: **≤ $10 per average book**. Interface: local CLI only. Language: Iranian Persian.

This folder is written to be handed to **Kilo Code** (or any coding agent). It is a build spec, not a discussion document.

## Files

| File | What it is | Read when |
|---|---|---|
| [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) | Master spec: product, constraints, 14 design decisions, evidence base, architecture, data model, pipeline semantics, QA linters, evaluation, cost model, milestones M0–M11 (+M4.5), risks, appendices | Always, start here |
| [`PROMPTS.md`](PROMPTS.md) | The 12 production prompts wired in v1 (brief, summaries, entities, glossary, translator, fidelity critic, literary editor, reviser, ledger curator, consistency auditor, read-through, judge); P13 back-translator is post-v1 reference only | At M3–M10 |
| [`SCHEMAS.md`](SCHEMAS.md) | Enums, Pydantic models, complete SQLite DDL, `deepseek.yaml`, artifact formats, mock fixtures | At M0 (DDL/config) and whenever wiring an agent |
| [`REVIEW.md`](REVIEW.md) | Adversarial review + resolution log (blocking findings fixed; v1.1 backlog) | When a contract looks oddly specific |
| `../research/prior-art.md` | Sourced evidence: OSS prior art, TransAgents/DelTA, metrics, glossary engineering, Persian LLM findings | When a design choice looks arbitrary |
| `../research/persian-engineering.md` | Persian normalization code points, ZWNJ, fonts, EPUB 3.3 RTL recipe, library licences, tokenizer fertility | At M2 (normalization) and M8 (export) |

## Locked product decisions (2026-11-05)

| Constraint | Implementation |
|---|---|
| Input: **EPUB preferred, else text-layer PDF** | EPUB first-class; PDF best-effort with a quality score; **no scanned PDFs / no OCR** (confirmed) |
| Model: DeepSeek V4.1 Flash latest | `deepseek-flash`, thinking **off by default** (measured, not assumed — low-effort thinking on critics is an M10 experiment), 1M context; `deepseek-v4-pro` only in `quality` cost mode |
| ≤ $10 per average book | Default cap 10; `economy`/`standard`/`quality` modes; off-peak tariff reported; cached prefix |
| Open, editable, PDF-convertible output | DOCX (primary editing/PDF) + EPUB 3 (reading) + Markdown; PDF via LibreOffice |
| Bilingual | Optional side product (`--bilingual`), off by default |
| Glossary | Optional: `off` (default), `provided`, `auto`; never a prerequisite |
| Automation | **Fully unattended by default**; review/anchors/brief-approval exist only behind flags |
| Scheduling | **Always starts immediately**; the owner decides when to run it |
| Interface | Local CLI (`tj`); no server, no web UI |
| Language | Iranian Persian only |
| Rights | Private use; provenance recorded, no gate (plan §1.5) |
| Coding style | Kilo `/ponytail` skill (below) |

## How to use it with Kilo Code

Start a session in the target repo and send something like:

> Implement milestones M0–M11 from `literary-translator/IMPLEMENTATION_PLAN.md`, in order, following `PROMPTS.md` and `SCHEMAS.md` exactly. Read all documents in that folder first. The default provider is DeepSeek `deepseek-flash` with thinking **off by default** (never enable it globally; the M10 bake-off tests low-effort thinking per agent); PDF import is best-effort; the glossary is optional and defaults to `off`; the zero-config path (`tj init book.epub && tj translate`) must work without any human approval step. Use your `/ponytail` skill for implementation style: concise, YAGNI, no unneeded abstraction. After each milestone: run `ruff check`, `mypy`, `pytest -q`, then run that milestone's acceptance commands from Section 10 and report the result. Do not add dependencies outside Section 4.4. Do not use ebooklib (AGPL) or hazm at runtime. Keep one commit per milestone. If a spec detail is genuinely ambiguous, pick the simplest option consistent with Section 2 and record it in `DECISIONS.md`; do not ask me unless it changes the architecture or the cost model. You may split milestones across sequential subagents (see below) — freeze `SCHEMAS.md` and the prompt files first, and you own integration and acceptance; a subagent never declares a milestone done.

### About `/ponytail` (yes, use it — with one guardrail)

Ponytail is a ruleset that makes the agent stop at the first rung that works (does this need to exist? is it in the repo? stdlib? native feature? one line?), and its own documentation says validation, error handling, security and accessibility are never on the chopping block. That is a good match for this project: the spec explicitly forbids unneeded dependencies and abstractions.

Use it, but scope it explicitly, because a minimalism skill will happily delete *ceremony the spec requires*:

- Good for: project scaffolding, CLI plumbing, exporter templates, test helpers, avoiding a framework.
- **Not** for: the sentinel round-trip validator, structured-output repair ladder, atomic `is_current` flips, budget guard, exit-code contract, retry classification, or the acceptance tests. Those are MUSTs in Section 0/9 and they win over brevity.
- Use `full`, not `ultra`. Run `/ponytail-review` on the diff at the end of M1, M4, and M8; if it proposes deleting a validator or a test, reject it and note why in `DECISIONS.md`.

Do **not** put the skill into the normative spec: it is a Kilo session setting, so it belongs in this handoff, not in `IMPLEMENTATION_PLAN.md` (a different coding agent should still be able to build the same system).

## Using subagents to build it (Kilo work only — never the shipped pipeline)

**The product must not use subagents or an agent loop.** Each translation window is already an independent, stateless API call with a ~7k-token context pack; nothing accumulates across windows, so there is no runtime context to save (plan §3.4, D2). Adding a per-chunk "subagent" at runtime would add cost, latency and non-determinism and would break prompt-cache hits.

**Your coding work can be delegated**, and the milestones are sized for it:

- **One subagent per milestone, in order** (M0 → M2 → …). Do not parallelize milestones that touch the same files; M8a/M8b share `export/` and must be sequenced.
- **Freeze interfaces before delegating.** `SCHEMAS.md` (DDL, models, config) and the prompt files are read-only inputs for a subagent; if one needs a change, it proposes it, *you* apply it, then the next milestone starts.
- **Give each subagent**: the milestone text, its acceptance commands from Section 10, the exact files it owns (write scope), and the instruction to run `ruff`/`mypy`/`pytest` before returning.
- **You verify every diff** and run the acceptance commands yourself. A subagent never declares a milestone done; "tests pass in its own worktree" is not acceptance.
- **Keep M0–M2 in one continuous context** if you can: they establish the conventions (config shape, DB DAL, prompt loader, fixture generator) that every later subagent must follow. Splitting the foundation usually costs more than it saves.
- Why this helps: it saves *the coding agent's* context window, not the translator's — the two are unrelated, which is why the plan keeps them separate.

## Practical notes for the implementing agent

- Build against `MockProvider` first; the entire test suite must pass with **no API keys and no network**.
- The riskiest code is `ingest/inline.py` (markup round-trip), `ingest/pdf_reader.py` (layout reconstruction) and `export/epub_writer.py` (RTL/OPF). Write their tests before their features.
- `tests/fixtures/build.py` generates synthetic EPUB/DOCX/TXT/PDF fixtures (project-authored text). Do not download or ship third-party books.
- After M4, run one real chapter through `deepseek-flash` (thinking off) before building M5+, to validate prompt quality and the real cost.
- Known M8b limitation: DOCX footnotes are emitted as chapter endnotes because python-docx has no footnote API, and the optional LibreOffice PDF is readable but not print-ready.

**What the agent can and cannot verify:** M0–M9 are testable offline with mock data. Real-provider smoke runs are manual and recorded in `DECISIONS.md`. Literary quality is screened by the judge and the MQM/BWS packets but **proven only by a human reader** — never claim it from CI.

## Quickstart (target UX — build this, then keep it working)

```bash
tj init book.epub            # or book.pdf
tj estimate                  # cost at peak/off-peak, all three modes
tj translate                 # zero config: auto brief, no glossary, no anchors
tj export                    # epub + docx + md
tj export --pdf              # optional, needs LibreOffice
tj export --bilingual        # optional side product
tj review build              # optional report for flagged segments
```

Optional consistency: `tj init book.epub --glossary terms.yaml`, or `tj analyze --auto-glossary`. Optional gates: `--review-brief`, `--manual-glossary`.

## Credentials

Copy `.env.example` to `.env` and fill in the keys you use:

- `DEEPSEEK_API_KEY` — default draft/critic model (`deepseek-flash`).
- `OPENROUTER_API_KEY` — quality tier used by the brief, glossary proposer and judge (`gpt-6-luna` in `configs/models/deepseek.yaml`).

`.env` is gitignored. `tj doctor` reports whether each key is present without printing its value.

## What the system does

```
ingest (EPUB | PDF | DOCX | TXT) → chapters, paragraph segments, inline markup as sentinels
analyze                          → book brief (auto-approved), chapter summaries, name list
glossary (optional)              → off | provided file | auto proposals
translate                        → window of 4–8 paragraphs: draft → fidelity critic + literary editor
                                   → revise → deterministic Persian lint → targeted repair
bookkeeping                      → ledger of characters/terms/decisions keeps later chapters consistent
audit                            → consistency check + Persian-only read-through
review (optional)                → only flagged/high-risk segments, side-by-side HTML
export                           → DOCX + EPUB 3 (RTL) + Markdown; optional bilingual; optional PDF
```

## The five ideas in your original concept, and how they survive the new constraints

| Your requirement | Implementation |
|---|---|
| Not literal; recreate intent, rhythm, emotion | Paragraph-unit translation (D3), style anchors, literary-editor pass (P07), read-through pass (P11), anti-translationese linter (L14) |
| Tone profile adherence | `BookBrief.register_target_fa` + `do_rules`/`dont_rules` derived from a real sample (P01), injected into every prompt |
| Dynamic glossary with exact equivalents | **Optional** artifact with three-layer enforcement when present (prompt → L03/L12 → repair); in `off` mode a deterministic proper-noun list keeps names stable |
| Rolling context / memory | Chapter summaries + append-only ledger (characters, terms, decisions, address forms) + previous paragraphs in both languages |
| Idioms/metaphors adapted | Explicit prompt rules (P05/P07), cultural-adaptation scoring in the judge, idiom/culture golden sets |
| Output only the Persian text | Structured JSON per segment (`TranslatorOutput`), no commentary; notes kept in a separate field |

## Key numbers (validate with `tj estimate`)

- 90k-word novel: ~620 LLM calls, ~2.8M input + ~0.53M output tokens. On `deepseek-flash` peak pricing ($0.30/M miss input, $0.006/M hit input, $1.20/M output) with a cached prefix, that is **≈$1–2 per book** in `standard` mode; `quality` mode (v4-pro critics) is ≈$4–6; off-peak halves both. The $10 ceiling leaves room for retries and a 300k-word book.
- Persian costs ~1.5–2.5× the tokens of the equivalent English; the pipeline budgets on the target side.
- Quality: judged on errors per 1,000 words, not percent, and no BLEU/COMET gates (they fail on literature — plan §8.1). Without a human editor the judge is a screening proxy, not proof.

## Decisions not to relitigate

1. Deterministic typed pipeline, not a free-roaming agent loop (D2).
2. Paragraph = translation unit; window = LLM call; sentence = analysis unit.
3. Glossary is optional, but when provided it is machine-verified (prompt adherence alone is not trusted).
4. Dual critics (fidelity vs literary), then a reviser — not one "improve this" pass.
5. No BLEU/COMET/BERTScore gating; MQM-style errors + human preference when available.
6. DeepSeek `deepseek-flash`, thinking **off by default** — the draft needs temperature control, and test-time-scaling evidence says reasoning helps post-editing more than one-pass translation, so low-effort thinking on critics is an M10 experiment, not a default; `deepseek-v4-pro` only for `quality`-mode critics and the judge.
7. PDF input is best-effort with a visible quality score; EPUB is the reference-quality path.
8. DOCX is the editable deliverable; EPUB the reading deliverable; Markdown the open/diffable one; PDF is a conversion with known RTL limitations.
9. No rights gate; provenance is recorded, private use assumed.
10. No vector DB, no LangChain/LangGraph, no ebooklib (AGPL), no hazm at runtime, no server.

## Still open (defaults are safe)

1. Footnotes: default is sparing endnotes (max 1 per 2,000 words) — enough for culture-heavy books?
2. Any verse-heavy book (poetry/epic)? Verse is preserved best-effort.
3. Any existing glossary/term list or previously translated chapters to seed with `--glossary`?

Locked: EPUB preferred / text-layer PDF fallback (no scans), fully unattended, immediate start (owner schedules), Iranian Persian, local CLI.

## Definition of done for v1

An EPUB (or a text-layer PDF) translates end-to-end unattended on `deepseek-flash`, resumes after a kill without duplicate spend, and produces DOCX + epubcheck-clean RTL EPUB + Markdown with 100% coverage, no banned Arabic code points, and a cost report under $10 — plus a judge/MQM report that states plainly what was measured and what was not.
