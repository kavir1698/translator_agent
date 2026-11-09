# Shahrzad — Implementation Plan

**An agentic English → Persian literary translation system**
Version 1.0 · Greenfield build · Target implementer: Kilo Code
Deliverable of this document: a complete, unambiguous specification that an AI coding agent can implement milestone by milestone without re-designing the architecture.

---

## 0. How to use this plan (read this first, Kilo)

You are implementing a new Python project from scratch. Follow these rules:

1. **Read all four documents before writing code:**
   - `IMPLEMENTATION_PLAN.md` (this file) — architecture, pipeline semantics, milestones.
   - `PROMPTS.md` — the exact prompt templates every agent uses. Copy them into the repo's prompt files *verbatim*, then keep them versioned.
   - `SCHEMAS.md` — Pydantic models, SQLite DDL, config file, artifact formats.
   - `README.md` — handoff summary and open questions.
2. **Implement milestones in order** (Section 10). Each milestone is independently shippable and has explicit acceptance criteria. Do not start milestone N+1 before `pytest` is green and the acceptance commands of milestone N pass.
3. **Do not re-architect.** Where this spec is silent, choose the simplest option consistent with Section 2 (Design Principles) and record the choice in `DECISIONS.md` (one line per decision).
4. **One commit per milestone**, message `M<N>: <summary>`. Keep the tree green at every commit.
5. **Never add a dependency not listed in Section 4.4** without writing a justification in `DECISIONS.md`. Prefer the standard library. No web frameworks, no vector databases, no LangChain/LangGraph in v1.
6. **No network in tests.** Every LLM call goes through the provider interface; tests use `MockProvider` with recorded fixtures.
7. **No silent failures.** Every `except` either re-raises with context or records a structured issue in the DB. Logging is JSON, never `print` (except CLI user output).
8. **All user-visible strings, paths and errors are actionable.** Error messages must say what failed, for which segment, and what to run next.
9. Prompts live in files, not inline strings. The prompt file content hash is part of the LLM cache key.
10. Python 3.12, full type hints, `ruff` + `mypy` clean, `pytest` coverage ≥ 80% on `core/`, `ingest/`, `persian/`, `pipeline/`.

**The product in one sentence:** a resumable, cost-aware CLI pipeline that ingests an English book (EPUB/DOCX/TXT), builds a *book brief*, an *approved glossary* and *chapter summaries*, translates paragraph-by-paragraph in context-aware windows using a draft → dual-critique → revise loop, runs deterministic Persian QA, lets a human review only what is flagged, and exports a valid RTL EPUB 3 (plus DOCX and a bilingual review HTML).

**The hard problems this plan solves, and where:**
| Problem | Section |
|---|---|
| Long-book coherence (who is "he", which "house", what voice) | 6.1, 6.4, 6.6 |
| Glossary consistency at book scale | 6.2, 6.7, 7 |
| Literary quality, not translationese | 2 (D7), 6.5, PROMPTS.md P5–P8 |
| Preserving emphasis, links, footnote refs through translation | 6.0.3, 7 (L02) |
| Persian orthography / typography correctness | 7, 6.8.1, SCHEMAS.md |
| Cost and time predictability for a 300-page book | 3.6, 9.5 |
| Resumability after crashes / rate limits | 9.3, 9.4 |
| Knowing whether a change made quality better | 8 |

---

## 1. Product definition

### 1.1 Goal

Produce an **editor-grade Persian literary translation** of an English book for the owner's personal library — good enough that a Persian reader who does not read English experiences the same voice, rhythm, characterization, humor and continuity — at **≤ $10 and a few hours per average book**, with every step reviewable and every output file editable.

Success is **not** "fluent Persian". Success is: the Persian reads as a book, not as a translation; a bilingual reviewer can verify any paragraph without reading the whole book linearly; and the owner can fix what is flagged and re-export without re-paying for the rest.

### 1.2 Users

| User | Needs |
|---|---|
| Owner / reader (primary) | One command, predictable cost, editable outputs, no mandatory setup or approval steps |
| Bilingual reviewer (optional) | Side-by-side review of flagged segments, glossary authority when a glossary exists, corrections that re-enter the pipeline |
| Developer / operator | Resumable runs, clear errors, no server, no vendor lock-in |

### 1.3 v1 scope

| In scope | Out of scope (v1) |
|---|---|
| English → **Iranian standard Persian only** (`fa-IR`) | Other language pairs, Dari, Tajik |
| Input: EPUB 3/2, **PDF with a text layer (best-effort)**, DOCX, TXT, Markdown | Scanned PDFs / OCR, DRM-protected files |
| Output: **EPUB 3 (RTL), DOCX (RTL), Markdown**; optional bilingual HTML; **PDF via DOCX→LibreOffice** | Print-ready/typeset PDF, InDesign/IDML, audiobook scripts |
| Prose fiction and non-fiction; basic poetry/verse preservation | Complex textbooks (math, multi-column figures with callouts) |
| Zero-config quickstart; human gates **optional** (brief, glossary, review) | Multi-user server, real-time collaboration, accounts |
| **Local CLI only** (`shz`), no server, no telemetry | Hosted web app / SaaS (possible later, M12+) |

### 1.4 Quality bar (v1 acceptance, measured in Section 8)

The industry's own numbers set the frame: vendors claim "98%" sentence accuracy, and a publisher points out that 99% accuracy in a 300-page book is still ~900 wrong words. So the acceptance target is expressed as **errors per 1,000 words**, not percent.

- **Coverage:** 100% of in-scope segments have a non-empty translation; no untranslated Latin-script runs of ≥ 4 words (L10) outside the `protected_latin` whitelist, and every shorter Latin run is an approved proper noun or glossary alias.
- **Glossary adherence:** ≥ 98% of checked term occurrences use the approved Persian form (deterministic, L03/L12); zero unapproved name variants at export.
- **Markup integrity:** 100% of inline emphasis/link/footnote-reference nodes preserved and correctly re-nested (L02) — verified by reconstruction byte-equality in tests.
- **Persian mechanics:** zero errors in the auto-fixable classes (banned Arabic code points, ASCII punctuation, digit script) after QA; ZWNJ findings ≤ 5 per 10,000 words, all human-reviewed.
- **Literary quality (acceptance gate):** with a human Persian editor available, ≤ 8 major errors per 1,000 words and 0 critical (meaning-changing) errors per 1,000 words on a 3-chapter MQM review; without an editor (the default private-use case), the T2 judge proxy targets the same band but is treated as a **screening signal, not proof** — the report states which mode was used. No claim of human parity. On the `bench/` golden set, the chosen configuration must beat the one-shot baseline (same model, single prompt, no artifacts) on BWS win-rate with a bootstrap CI excluding 0.5, and must not regress on judge `fidelity` (Section 8).
- **Operability:** a 100k-word book completes unattended, is resumable after `kill -9` at any point without duplicate spend, and produces a cost report accurate to ±15%.

### 1.5 Book provenance (no legal gate)

The product owner supplies the source file for private, non-published use. The tool therefore records provenance but does not interrogate it:

- `books.rights_basis` defaults to `user_supplied` at `shz init`; `public_domain` and `licensed` are allowed values, but there is **no acknowledgement prompt and no export gate** — the user already asserted they will not publish.
- The EPUB colophon and `runs/<id>/manifest.json` record the basis + source file hash, so provenance is auditable if the book is ever shared.
- The README states plainly: publishing a translation of an in-copyright book needs the rightsholder's permission; the software does not clear rights and is not a legal opinion.

This replaces the earlier hard gate: a translation tool for one's own library must not nag. Keep the field, drop the friction.

### 1.6 Confirmed product constraints (2026-11-05)

These are binding for the build; the rest of this spec has been adjusted to them.

| Constraint | Implementation consequence |
|---|---|
| Model: **DeepSeek V4.1 Flash** (`deepseek-flash`), latest version | Default provider profile `deepseek`; model ids live only in `configs/models/deepseek.yaml`; bake-off still validates Persian quality |
| **≤ $10 per average book** | Default `budget.max_usd: 10`; cost modes (`economy`/`standard`/`quality`) with per-book estimates; tariff shown at peak and off-peak; aggressive prompt caching |
| Input: **EPUB preferred, text-layer PDF fallback** | EPUB is first-class; PDF is best-effort with a visible import-quality score and a mandatory structure review when low. **No scanned PDFs** — the owner confirmed all PDFs have a text layer; OCR is out of scope |
| Output: **open, editable, PDF-convertible** | DOCX (primary editing/PDF path) + EPUB 3 (reading) + Markdown (diff/archive); PDF only via DOCX→LibreOffice |
| Bilingual edition | **Optional side product** (`--bilingual`), off by default |
| Glossary | **Optional**: `off` (default), `provided` (`--glossary file`), or `auto` (`--auto-glossary`); never a prerequisite |
| Automation | **Fully unattended by default**: no brief/glossary/anchor/review step blocks a run; gates exist only behind flags |
| Scheduling | **Always start immediately** when the user runs the command; never wait for off-peak. The report states the tariff used and the cost |
| Interface | Local CLI only; no server, no web UI in v1 |
| Language | Iranian Persian only; no Dari/Tajik code paths, no locale switch |
| Legal | Provenance recorded, no gate (see 1.5) |

---

## 2. Design principles (the 14 decisions that shape the code)

Each decision states the choice, why, and the concrete consequence for the implementer.

**D1 — Book-level analysis precedes the first translated word.**
Before translation we build: a *Book Brief* (tone profile, register, conventions), optionally a *Glossary* of entities/terms with approved Persian equivalents, per-chapter English summaries, and **up to 3 *style anchors*** (source/Persian pairs; minimum 2, maximum 3) once chapter 1 is approved. Rationale: consistency and voice cannot be recovered paragraph-locally, and post-hoc fixing costs more than planning. Consequence: three **optional** human checkpoints (brief/glossary, chapter-1 calibration, flagged review); the default zero-config path runs without any of them, and every translation prompt still receives compact, structured context.

**D2 — Typed pipeline, not an open-ended agent loop.**
The orchestrator is a deterministic state machine. "Agents" are just `(prompt template, model tier, temperature, output schema, validators)` bundles called at fixed points. Rationale: literary translation needs reproducibility, resumability, cost ceilings and debuggability; free-roaming multi-agent chatter burns money and makes failures untraceable. Consequence: `pipeline/` contains explicit passes; `agents/` contains prompt-bound functions; adding LangGraph later is an optional adapter, not a rewrite.

**D3 — The paragraph is the unit of translation; the sentence is the unit of analysis.**
Segments are paragraphs (or verse stanzas, headings, footnote texts). Rationale: literary rhythm lives at paragraph level; sentence-level translation destroys flow; paragraph-level keeps one prompt = one coherent unit of meaning. Consequence: `segments` table, stable IDs, and sentence-level utilities used only by linters and the fidelity critic.

**D4 — Window + overlap for local context; precomputed summaries for global coherence.**
Each LLM call translates a *window* of 4–8 paragraphs (≤ 900 source tokens, configurable) and receives the immediately preceding **2** paragraphs in both languages plus the chapter summary, a rolling chapter summary, and a compression of the story ledger. Rationale: models lose coherence over long contexts and long outputs, but windows alone lose cross-paragraph flow. Consequence: `pipeline/window.py` (deterministic planner) and `context/packer.py` (budgeted context assembly).

**D5 — When a glossary is used, it is a human-approved artifact on disk, enforced three ways; when it is not, names are still kept stable.**
`artifacts/glossary.yaml` is the source of truth (reviewable in git). Enforcement = prompt injection of the approved entries + deterministic presence check + targeted repair. New terms found mid-translation become *suggestions*, never silent choices. Rationale: the product owner wants glossary-optional, but when terminology is pinned it must actually stick. Consequence: `glossary.mode: off|provided|auto`; `off` (default) injects a deterministic proper-noun list and relies on L12, `provided`/`auto` add the full three-layer enforcement.

**D6 — Three-pass translation per window: draft → dual critique → revise, with structured per-segment output.**
Draft for voice; a *fidelity critic* for meaning/omission/addition; a *literary editor* for Persian rhythm, register and naturalness; a *reviser* that applies only concrete, quoted issues. Rationale: a single prompt asking for both fidelity and beauty underperforms; separating the objectives measurably helps and gives an audit trail. Consequence: 3–4 calls per window with JSON schemas, versioned translations per pass, and a cost model that assumes ~4× one-shot.

**D7 — Two independent quality axes, two critics, different temperatures.**
Fidelity critic: temperature 0.1, evidence-driven, must quote source and translation. Literary editor: temperature 0.35, must propose minimal edits and may defend the draft. Never merge them into one "check and improve" prompt. Consequence: separate prompt files, separate issue types, separate severity vocabularies.

**D8 — Every LLM call is cacheable, attributable and costed.**
All calls go through one provider layer. Cache key = hash(model, temperature, messages, schema, prompt version). Every call records tokens, cost, latency, finish reason, run id. Consequence: re-runs are cheap and deterministic; prompt experiments are A/B-able; the cost report is trustworthy.

**D9 — SQLite is the operational source of truth; YAML is the human-facing knowledge; exports are pure functions.**
Translations, statuses, issues, calls live in `book.db`. Brief, glossary, ledger, anchors are YAML/JSONL because humans edit them. Export never invents content and never mutates the DB. Consequence: `db.py` with a thin DAL and explicit DDL, `export/` as pure transformations, `artifacts/` version-controllable.

**D10 — Three optional human checkpoints; the default path is unattended.**
Checkpoint 1: brief + glossary approval (only in `--review-brief` / `glossary.approval: manual`). Checkpoint 2: chapter-1 calibration to set anchors (recommended once per book, never required). Checkpoint 3: review of flagged segments before export (optional; export always allowed with `--allow-incomplete` or with warnings). Rationale: human taste is the bottleneck asset, so spend it where it changes the outcome — but a personal-library tool must work with zero human steps on day one. Consequence: every gate is opt-in via config/flag, and the zero-config path (init → translate → export) is an acceptance criterion.

**D11 — Code-first QA; LLM judges only rank quality.**
Mechanical properties (coverage, markup, digits, punctuation, script purity, glossary presence, numbers, repetition, length outlier, blacklisted translationese) are deterministic linters with stable codes (L01–L20). LLM judging is reserved for literary quality scoring and the whole-book read-through. Consequence: QA is free, fast, reproducible and testable; LLM QA is budgeted and sampled.

**D12 — Evaluation exists from M2 (mechanical) and M10 (judge/bake-off); model choice is empirical.**
A small golden set of hand-checked passages, a rubric-based judge, a pairwise preference protocol and a model bake-off script. Rationale: "which model is best at literary EN→FA" is an empirical question that changes every few months; hard-coding a vendor is a liability. Consequence: `eval/` package, `shz eval bakeoff`, `configs/models/*.yaml`; linters and their metrics ship from M2, and no default model is blessed before the bake-off.

**D13 — Persian correctness is enforced, not hoped for.**
Character normalization (Arabic ی/ک → Persian, digits, ZWNJ), punctuation and typography are handled by a dedicated module with table-driven rules and tests, applied to model output before persistence. Rationale: LLMs emit Arabic-script variants, ASCII punctuation and broken half-spaces constantly; a book with inconsistent orthography is unpublishable. Consequence: `persian/normalize.py` (write rules), `persian/lint.py` (read/verify rules), never mix the two concerns.

**D14 — No embeddings, no vector DB in v1.**
Glossary subsetting = term occurrence scan in the window (exact + alias, longest-match). Deduplication = `rapidfuzz` token-set ratio. Rationale: 300 glossary entries and 10k segments do not need a vector index; adding one adds a model download, a failure mode and a dependency. Consequence: `context/glossary_match.py` is ~80 lines; revisit only if glossary > 1,000 entries or cross-book memory is added.

### 2.1 Evidence base (why these decisions, briefly)

Each row is a sourced finding and the design choice it forces. Full notes: `research/prior-art.md`.

| Finding | Source | Consequence here |
|---|---|---|
| Paragraph-level prompting beats sentence-level on literary text across 18 pairs (fewer mistranslations, grammar errors, style inconsistencies) — but occasional omissions persist | Karpinska & Iyyer, [arXiv:2304.03245](https://arxiv.org/abs/2304.03245) | Segment = paragraph (D3); per-segment ids + L01/L08/L16 catch omissions |
| Preparation artifacts + editorial roles produce translations preferred by humans and a bilingual LLM judge, while having the **lowest** d-BLEU | TransAgents, [arXiv:2405.11804](https://arxiv.org/abs/2405.11804) | Book brief + glossary + chapter summaries before translation (D1); dual critique (D6/D7); do not trust reference metrics |
| Multi-tier memory (proper nouns, bilingual summary, long/short-term) adds +4.58 consistency and +3.16 COMET; sentence-unit to avoid omissions | DelTA, [arXiv:2410.08143](https://arxiv.org/abs/2410.08143) | Ledger + chapter summary + previous-paragraph context (D4); one memory schema, no free-form chat history |
| Naming unmet constraints in a revise step raises glossary adherence by ~15% | [arXiv:2407.13164](https://arxiv.org/abs/2407.13164) | Glossary enforced by check-then-repair, not by prompt alone (D5) |
| OSS book tools converge on: prep artifacts → translate with per-chunk glossary filter → rule-based error detection → targeted retranslation | bilingual_book_maker, TranslateBooksWithLLMs, GalTransl | Our L01–L20 + repair queue; per-chunk glossary subset (D5/D14) |
| Automatic metrics identify human literary translations ≤ 20% of the time; MQM with novice annotators misjudges ~60%; best-worst scaling identifies them 80–100% | LITEVAL-CORPUS, [arXiv:2410.18697](https://arxiv.org/abs/2410.18697) | No BLEU/COMET gates; human BWS + professional MQM as acceptance (Section 8) |
| Reference-free LLM judges with **error spans** (GEMBA-MQM) approach system-level MQM ranking, but are black boxes | [arXiv:2310.13988](https://arxiv.org/abs/2310.13988) | Judge emits MQM-typed spans; used for regression/A-B only, never the ship gate |
| Persian literary LLM output drifts long (452 words/6 sentences → 636–699 words/15–18 sentences in the reverse direction), adds explanations, flattens register; prompt-only style instruction produced no measurable register change | [Persian literary study](https://jpll.ui.ac.ir/article_30217.html) | L08 length+sentence ratio, L16 meta-commentary, style anchors, read-through pass; never rely on adjectives in prompts |
| Commercial bar ≈98% sentence accuracy and vendors concede it is insufficient for books | [Publishers Weekly](https://www.publishersweekly.com/pw/by-topic/digital/content-and-e-books/article/98849-ai-translation-race-accelerates.html) | Acceptance in errors/1,000 words; human review when available |
| No EN→FA literary benchmark or agentic book translator exists publicly (2026-11) | `research/prior-art.md` §7 | Build an in-house golden set + evaluation protocol as a deliverable, not an afterthought |
| CoT reasoning improves context-aware translation (pronominal anaphora, lexical cohesion) across 12 LLMs; gains are larger for models that are already strong ("wise get wiser") | Ataee et al., LREC 2026, [ACL Anthology](https://aclanthology.org/2026.lrec-1.298/) | Reasoning targets exactly the long-book cohesion problem; test it on the critic/reviser, not blindly on every call |
| Test-time scaling on general reasoning models: limited, inconsistent gains for direct translation that plateau quickly; forcing reasoning past its natural stop degrades quality; **post-editing is where inference-time compute reliably pays off** | Li, Ji & Tiedemann, [arXiv:2510.06471](https://arxiv.org/abs/2510.06471) | Our draft→critic→revise loop *is* post-editing; the right experiment is low-effort thinking on critics/reviser |
| Long-CoT literary translation (fine-tuned DRT-14B) improves literariness in human BWS (+0.36 vs +0.01) but is ~12× slower, and the gain comes from domain fine-tuning more than from toggling a general model's reasoning | DRT, ACL 2025, [arXiv:2412.17498](https://arxiv.org/abs/2412.17498) | Do not assume the thinking flag reproduces a fine-tuned system's gains; measure with the bake-off and cap latency |

---

## 3. Architecture

### 3.1 System diagram

```
                 ┌──────────────────────────────────────────────────────────────┐
                 │  CLI (typer + rich)                                          │
                 │  init · inspect · analyze · glossary · translate · qa        │
                 │  anchors · audit · review · export · eval · estimate · doctor│
                 └───────────────────────────┬──────────────────────────────────┘
                                             │
                              ┌──────────────▼──────────────┐
                              │  Orchestrator (state machine)│
                              │  run rows · stages · budget  │
                              │  resume · concurrency · logs │
                              └───┬──────┬────────┬──────┬───┘
             ┌────────────────────┘      │        │      └───────────────────┐
             ▼                           ▼        ▼                          ▼
     ┌──────────────┐          ┌──────────────┐ ┌──────────────┐     ┌──────────────┐
     │ INGEST       │          │ ANALYZE      │ │ TRANSLATE    │     │ EXPORT       │
     │ (no LLM)     │          │ (LLM tier B) │ │ (LLM tier A) │     │ (no LLM)     │
     │ epub/docx/txt│          │ brief        │ │ window plan  │     │ epub3 RTL    │
     │ chapterize   │          │ entity list  │ │ context pack │     │ docx RTL     │
     │ segment+markup│         │ summaries    │ │ draft/crit/rev│    │ bilingual    │
     └──────┬───────┘          └──────┬───────┘ └──────┬───────┘     └──────┬───────┘
            │                         │                │                    │
            │              ┌──────────▼────────────────▼──────────┐         │
            └──────────────▶          ARTIFACTS + SQLITE           ◀─────────┘
                           │  book.db · brief.yaml · glossary.yaml │
                           │  ledger.jsonl · anchors.yaml · cache  │
                           │  runs/<id>/calls.jsonl · reports      │
                           └───────────────────┬───────────────────┘
                                               │
                                    ┌──────────▼──────────┐
                                    │ QA / AUDIT / REVIEW │
                                    │ linters (code)      │
                                    │ consistency auditor │
                                    │ read-through        │
                                    │ report.html         │
                                    └─────────────────────┘
```

### 3.2 Stage contracts

| Stage | LLM? | Input | Output (persisted) | Failure mode |
|---|---|---|---|---|
| `init` | no | book file, config | `books/<slug>/`, `book.db`, source extracted | hard fail, no partial state |
| `inspect` | no | DB | human/JSON stats: chapters, segments, tokens, markup count, estimated cost | read-only |
| `analyze` | tier B | segments | `brief.yaml`, `glossary.candidates.yaml`, `chapter_summaries` table, style metrics | per-item retry; brief auto-approved by default, `--review-brief` for manual |
| `glossary approve` | optional human | candidates + brief | `glossary.yaml` (approved), DB mirror | only in `provided`/`auto`; never required |
| `translate` | tier A | segments + artifacts | `translations` rows (all passes), `issues`, optional `anchors.yaml` | resumable; per-segment failure allowed and reported |
| `qa` | no | translations | `issues`, risk scores, `review/flags.csv` | deterministic |
| `audit` | tier B/A | full book | consistency issues, `ledger` updates, read-through issues | sampled, budgeted |
| `review` | optional human | report + corrections | new translation versions, reviewed flags | never blocks export |
| `export` | no | DB + artifacts | EPUB/DOCX/HTML/TXT | hard fail with actionable message |

Every stage writes a `runs` row with `stage`, `status`, `config_snapshot`, `stats_json`, `cost_usd`. `shz translate` resumes: it selects segments whose status is not `done`, in deterministic order, and never re-bills cached calls.

### 3.3 Agent roster

Model tiers are config aliases, not vendor lock-in: `quality`, `balanced`, `cheap` (Section 4.5).

| ID | Agent | Stage | Tier | Temp | Called per | Output schema (SCHEMAS.md) |
|---|---|---|---|---|---|---|
| A1 | BriefAnalyst | analyze | quality | 0.2 | 1 / book | `BookBrief` |
| A2 | ChapterSummarizer | analyze | cheap | 0.2 | 1 / chapter | `ChapterSummary` |
| A3 | EntityExtractor | analyze | cheap | 0.0 | batch / ~4k tokens | `EntityBatch` |
| A4 | GlossaryProposer | analyze | quality | 0.3 | 1 / ~40 entities | `GlossaryProposals` |
| A5 | Translator | translate | quality | 0.55 | 1 / window | `TranslatorOutput` |
| A6 | FidelityCritic | translate | quality | 0.1 | 1 / window | `Critique` |
| A7 | LiteraryEditor | translate | quality | 0.35 | 1 / window | `Critique` |
| A8 | Reviser | translate | quality | 0.3 | 0–2 / window | `WindowTranslation` |
| A9 | LedgerCurator | translate | cheap | 0.1 | 1 / chapter | `LedgerUpdate` |
| A10 | ConsistencyAuditor | audit | balanced | 0.1 | sampled windows | `AuditFindings` |
| A11 | ReadThroughEditor | audit | quality | 0.3 | 1 / chapter | `ReadThroughFindings` |
| A12 | QualityJudge | eval | quality | 0.2 (sampled) | per sample | `JudgeVerdict` |
| A13 | BackTranslator | **post-v1** (not wired in v1) | quality | 0.2 | — | `WindowTranslation` |

Design note for the implementer: A6/A7 are deliberately **not** merged, and A8 receives the *union* of their issues with a rule: apply only issues that quote a real span; if critics disagree, fidelity wins for meaning, editor wins for wording. Disagreement is logged for human review (a cheap way to find hard passages).

### 3.3.1 CLI surface (normative — every flag below must exist)

| Command | Required subcommands / flags | Purpose |
|---|---|---|
| `shz init <file>` | `--slug`, `--rights-basis`, `--translator`, `--glossary`, `--force` | create workspace, parse source (EPUB/PDF/DOCX/TXT/MD), write DB |
| `shz inspect` | `--json`, `--diff` | chapter/segment/token/markup stats; `--diff` compares stored source hashes |
| `shz analyze` | `--sample-budget`, `--draft-only`, `--approve`, `--only <chapter>` | brief draft, summaries, entities, glossary candidates |
| `shz glossary` | `list`, `review`, `approve`, `reject`, `edit`, `add`, `import`, `export`, `sync`, `pending`; `--min-confidence` | gate 1 |
| `shz anchors` | `propose`, `approve`, `reject`, `list` | gate 2 (chapter-1 calibration) |
| `shz translate` | `--chapters`, `--segments`, `--windows`, `--dry-run`, `--exit-after`, `--max-usd`, `--cost-mode`, `--allow-overrun`, `--force`, `--no-cache`, `--parallel-chapters`, `--calibrate`, `--replan`, `--only-stale`, `--require-anchors`, `--review-brief`, `--local-only`, `--allow-cloud` | main translation pass |
| `shz qa` | `--chapter`, `--group`, `--stale-glossary`, `--json`; `accept --issue ID --note ... --reviewer ...`, `resolve --issue ID` | lint, flags, issue lifecycle |
| `shz audit` | `--chapter`, `--sample`, `--readthrough/--no-readthrough`, `--max-usd` | consistency audit + Persian read-through |
| `shz review` | `build --chapter --out`, `apply <corrections.jsonl> --dry-run` | gate 3 |
| `shz export` | `--formats epub,docx,md,txt`, `--pdf`, `--bilingual`, `--out`, `--allow-incomplete`, `--generated-at` | editable outputs + optional bilingual + PDF conversion |
| `shz estimate` | `--chapters`, `--json` | cost table before spending |
| `shz doctor` | `--models`, `--tokenizer`, `--epubcheck`, `--json` | environment + config health |
| `shz eval` | `judge --golden --out`, `bakeoff --models --out`, `calibrate` | quality measurement (M10) |
| `shz retranslate` | `--segments`, `--chapter`, `--window`, `--stale-only` | targeted redo without touching history |

There is **no rights gate**: `rights_basis` defaults to `user_supplied`, is informational, and is recorded in the colophon and manifest. `--allow-cloud` only matters for the `licensed` case; `--local-only` is available for privacy. Export never asks for acknowledgement.

### 3.4 Execution model

```
run(stage):
  cfg   = load config snapshot
  db    = open book.db
  todo  = db.select_work(stage)            # deterministic ordering
  budget.check_estimate(todo)
  async with concurrency(cfg.max_inflight):
      for item in todo:                    # chapters or windows
          await process(item)              # idempotent, writes as it goes
  finalize(stage)                          # aggregates, metrics, exit code
```

- **Chapter-level parallelism is opt-in** (`--parallel-chapters N`). Default is reading-order sequential translation, because continuity is the product. Parallel mode relies on precomputed summaries and disables ledger chaining; it is 2–4× faster and measurably (slightly) less coherent — this trade-off must be printed once at start.
- Within a chapter, windows are strictly sequential (each window's context includes the previous window's output). Concurrency happens across chapters, or across the independent analysis items.
- Any item failure is recorded as a segment/ chapter `status=failed` + `issues` row, the run continues, and the final exit code is non-zero with a report. **Never abort the whole book for one bad paragraph.**

**Statelessness is the design.** Every LLM call is a fresh request — system prefix + context pack + window — with no conversation history. Nothing accumulates across windows or chapters; context is rebuilt from the DB/artifacts every time. Consequences: (a) there is no runtime context window to compact, so "delegate each chunk to a subagent" would add an orchestrator layer, latency and cost while saving nothing — the API call already isolates context; (b) any window can be retried or re-run in isolation without cross-contamination; (c) context cost is controlled by the pack budget (6.4), not by multi-agent decomposition. This is the concrete reason D2 rejects a free-roaming agent loop. Subagents belong to the *implementation workflow* (README), never to the shipped pipeline.

### 3.5 Where determinism matters

Same DB + same artifacts + same config ⇒ same window plan, same cache keys, same exports, **in sequential mode**. With `--parallel-chapters`, the set of translations is the same but ledger merge order and `created_at` values differ, so exports are only byte-identical when `--generated-at` is pinned. LLM sampling is the other non-determinism; that is why every pass version is stored. **Export determinism rule:** `dcterms:modified` defaults to the maximum `translations.created_at` in the book, not wall-clock, so re-exporting an unchanged book is byte-identical.

### 3.6 Cost and time model (estimates, to be validated by `shz estimate`)

For a 90,000-word English novel (~120k English model tokens). Persian output is tokenizer-hostile: budget **~1.5–2.5× the English token count** (≈2k output tokens per 750-word window; measure per model). The draft window's pack is ~6.5–8k tokens depending on glossary size (Section 6.4), and ~75% of it is the cacheable prefix.

| Pass | Calls | Input tokens | Output tokens |
|---|---|---|---|
| Analysis (brief, summaries, entities, glossary) | ~55 | ~0.20M | ~0.05M |
| Draft (window ≈ 750 source words, ~7k-token pack) | ~130 | ~0.83M | ~0.16M |
| Fidelity critic | ~130 | ~0.49M | ~0.05M |
| Literary editor | ~130 | ~0.47M | ~0.05M |
| Reviser | ~130 | ~0.72M | ~0.17M |
| Chapter bookkeeping + audit + read-through | ~45 | ~0.12M | ~0.05M |
| **Total** | **~620** | **~2.8M** | **~0.53M** |

**Tariff (DeepSeek API, verified 2026-11):** `deepseek-flash` = DeepSeek-V4.1-Flash-1017. Per 1M tokens: cache-*hit* input **$0.003 off-peak / $0.006 peak**; cache-*miss* input **$0.15 / $0.30**; output **$0.60 / $1.20**. Off-peak is everything outside 01:00–04:00 and 06:00–10:00 UTC, Monday–Friday. Context 1M tokens, max output 384K. Thinking mode is **disabled** for this pipeline (see 9.6) — it would ignore our temperatures and bill reasoning tokens as output.

With the book-stable prefix cached (6.4), roughly 60% of input tokens hit cache. Per-book cost at this token table:

| Cost mode | What runs | Est. peak | Est. off-peak |
|---|---|---|---|
| `economy` | draft + fidelity critic + lint | $0.5–0.8 | $0.25–0.40 |
| `standard` (default) | draft + dual critics + revise + lint | $1.0–1.6 | $0.50–0.80 |
| `quality` | flash draft; `deepseek-v4-pro` critics + reviser | $4–6 | $2–3 |

So an average 90k-word book is **≈$1–2** in standard mode — far inside the **$10 default ceiling**, with headroom for retries and for a 300k-word book (~$4–6 in standard, still under $10). No premium model is needed for the base product; `quality` exists for books where the cheap-model bake-off shows literary weakness. If M10 adopts low-effort thinking on critics/reviser, add roughly 0.3–0.6M output tokens (**≈$0.4–0.7 peak**) and 1.5–2× wall-clock; it still fits the cap. `shz estimate` prints both tariffs and all three modes before any spend; `--dry-run` makes zero API calls. The ±15% cost-accuracy target applies after the first 50 calls calibrate `fa_ratio`. Wall-clock: 1.5–2.5 h sequential, 40–70 min with 4-way chapter parallelism (longer if thinking is enabled).

---

## 4. Repository layout and dependencies

### 4.1 Tree

```
shahrzad/
├─ pyproject.toml                  # uv-managed project, ruff/mypy/pytest config
├─ README.md                       # user guide + legal note + quickstart
├─ DECISIONS.md                    # one-line log of implementation choices
├─ configs/
│  ├─ default.yaml                 # full documented config (see SCHEMAS.md §4)
│  ├─ sample.book.yaml             # a complete book config
│  └─ models/
│     ├─ deepseek.yaml             # DEFAULT: deepseek-flash + deepseek-v4-pro (quality mode)
│     ├─ local.yaml                # optional OpenAI-compatible endpoint (Ollama/vLLM)
│     └─ example-other.yaml        # template for another provider
├─ src/shahrzad/
│  ├─ __init__.py                  # __version__
│  ├─ cli/
│  │  ├─ main.py                   # typer app, global options, exit codes
│  │  ├─ init_cmd.py  inspect_cmd.py  analyze_cmd.py  glossary_cmd.py
│  │  ├─ translate_cmd.py  qa_cmd.py  anchors_cmd.py  audit_cmd.py  review_cmd.py
│  │  ├─ export_cmd.py  estimate_cmd.py  doctor_cmd.py  eval_cmd.py
│  ├─ core/
│  │  ├─ config.py                 # pydantic-settings, YAML merge, validation
│  │  ├─ models.py                 # domain enums + dataclasses (not DB rows)
│  │  ├─ db.py                     # sqlite3 connection, PRAGMA, migrations, DAL
│  │  ├─ schema.sql                # canonical DDL (also embedded for tests)
│  │  ├─ ids.py                    # deterministic segment/chapter/run ids
│  │  ├─ tokens.py                 # token estimator (en/fa), char stats
│  │  ├─ text.py                   # deterministic sentence splitting (en/fa), abbreviation guard
│  │  ├─ cost.py                   # price table + cost accounting
│  │  ├─ errors.py                 # typed exceptions, exit codes
│  │  ├─ logging.py                # JSONL + rich console
│  │  └─ paths.py                  # project layout resolver, safe joins
│  ├─ ingest/
│  │  ├─ detect.py                 # format sniffing (magic bytes)
│  │  ├─ epub_reader.py            # zipfile+container.xml+OPF+spine, lxml html
│  │  ├─ pdf_reader.py            # text-layer PDF -> lines -> paragraphs (pypdfium2), quality score
│  │  ├─ docx_reader.py            # python-docx -> block stream
│  │  ├─ txt_reader.py             # txt/md -> block stream
│  │  ├─ blocks.py                 # Block model, classify: heading/prose/verse/quote/
│  │  │                            #   footnote/front/table/image/nav
│  │  ├─ inline.py                 # inline markup <-> node list + sentinel tokens
│  │  ├─ chapterize.py             # blocks -> chapters (heuristics + structure.yaml)
│  │  └─ normalize_source.py       # whitespace, hyphenation, curly quotes, entities
│  ├─ persian/
│  │  ├─ normalize.py              # canonicalization rules N01..N09 (Section 7.3)
│  │  ├─ lint.py                   # L01..L20 checks + severity + auto-fix registry
│  │  ├─ numerals.py               # digit conversion, number parsing/compare
│  │  ├─ punctuation.py            # Persian punctuation, guillemets, ellipsis
│  │  └─ markup.py                 # reassemble nodes -> html, validate sentinels
│  ├─ llm/
│  │  ├─ provider.py               # Protocol: complete(messages, schema, ...) -> Result
│  │  ├─ litellm_provider.py       # real provider via litellm
│  │  ├─ mock_provider.py          # scripted fixtures, failure injection (tests)
│  │  ├─ schema.py                 # JSON-schema build, parse, repair ladder
│  │  ├─ cache.py                  # sqlite-backed response cache
│  │  ├─ budget.py                 # estimate/guard
│  │  └─ promptlib.py              # load prompt .md, front-matter, jinja render, hash
│  ├─ agents/
│  │  ├─ base.py                   # AgentSpec, run_agent(), validation hooks
│  │  ├─ analyst.py  summarizer.py  entity_extractor.py  glossary_proposer.py
│  │  ├─ translator.py  fidelity_critic.py  literary_editor.py  reviser.py
│  │  ├─ ledger_curator.py  consistency_auditor.py  readthrough.py  judge.py
│  ├─ context/
│  │  ├─ packer.py                 # ContextPack assembly with token budgets
│  │  └─ glossary_match.py         # window term matching (exact + alias)
│  ├─ pipeline/
│  │  ├─ orchestrator.py           # stage state machine, runs table, resume
│  │  ├─ analyze_pass.py
│  │  ├─ translate_pass.py
│  │  ├─ qa_pass.py
│  │  ├─ audit_pass.py
│  │  ├─ export_pass.py
│  │  └─ window.py                 # deterministic window planner
│  ├─ export/
│  │  ├─ epub_writer.py            # EPUB3 writer (custom, zipfile-based)
│  │  ├─ docx_writer.py            # python-docx RTL  (primary editable artifact)
│  │  ├─ markdown_writer.py        # md output (diff/archive; PDF-friendly pandoc path)
│  │  ├─ pdf_convert.py            # DOCX -> PDF via LibreOffice headless (optional)
│  │  ├─ bilingual_html.py         # optional side product: side-by-side edition
│  │  ├─ plain_text.py
│  │  └─ epub_assets/              # css, opf/nav/xhtml jinja templates, fonts
│  ├─ review/
│  │  ├─ report.py                 # single-file html report (jinja2)
│  │  └─ corrections.py            # apply corrections.jsonl -> new versions
│  ├─ eval/
│  │  ├─ golden.py  judge.py  blp.py  mqm.py  metrics.py  bakeoff.py
│  ├─ prompts/                     # *.md with YAML front-matter (see PROMPTS.md)
│  └─ data/
│     ├─ translationese_fa.txt     # blacklist phrases (L14)
│     ├─ zwnj_forms.txt            # frequent joined forms for the high-precision ZWNJ repair (L07)
│     └─ protected_latin.txt       # allowed Latin strings (brands, acronyms)
├─ tests/
│  ├─ unit/  integration/  e2e/  golden/  fixtures/
└─ scripts/
   ├─ run_epubcheck.sh             # optional, skips gracefully if java missing
   └─ bakeoff.sh
```

`src/` layout with a `pyproject.toml` `[project.scripts]` entry `shz = "shahrzad.cli.main:app"`.

### 4.2 Book workspace layout (created by `shz init`)

```
books/<slug>/
├─ book.db                          # sqlite: segments, translations, glossary mirror, runs, cache
├─ config.yaml                      # optional per-book overrides (loaded before env)
├─ source/
│  ├─ original.epub                 # untouched input (sha256 recorded)
│  └─ extracted/                    # chapters/*.xhtml, images/, metadata.json
├─ artifacts/
│  ├─ brief.yaml                    # human-edited, approved
│  ├─ glossary.candidates.yaml      # proposals with confidence (auto mode only)
│  ├─ glossary.yaml                 # approved, authoritative (provided/auto modes only)
│  ├─ ledger.jsonl                  # character/term/decision events
│  ├─ anchors.draft.yaml            # gate-2 proposals (only after --calibrate)
│  ├─ anchors.yaml                  # approved style anchor pairs (optional, gate 2)
│  ├─ structure.yaml                # optional manual chapter overrides
│  └─ chapter_summaries.jsonl       # mirrored in DB, exportable
├─ runs/<run_id>/
│  ├─ manifest.json                 # config snapshot, versions, hashes, cost
│  ├─ calls.jsonl                   # one line per LLM call
│  └─ report.md                     # human-readable run summary
├─ review/
│  ├─ report.html                   # interactive review (single file)
│  ├─ flags.csv
│  └─ corrections.jsonl             # human edits, importable
└─ out/
   ├─ <slug>.fa.epub              # reading format (RTL)
   ├─ <slug>.fa.docx              # editable / PDF-convertible
   ├─ <slug>.fa.md                # open text, diffable
   ├─ <slug>.fa.txt
   ├─ <slug>.fa.pdf               # only with --pdf + LibreOffice
   └─ <slug>.bilingual.html       # only with --bilingual
```

### 4.3 Module responsibilities (non-negotiable boundaries)

- `ingest/` never calls an LLM and never writes to `translations`.
- `persian/` is pure functions over strings; no I/O, no config, table-driven, fully unit-tested.
- `agents/` never touches the DB directly; it takes typed inputs and returns validated Pydantic objects (`base.run_agent` handles calls, cache, retries, cost).
- `pipeline/` owns state transitions and DB writes. It is the only place that decides "what happens next".
- `export/` reads DB read-only and is a pure function of the approved state; it must not modify `book.db`.
- `cli/` contains no business logic: parse → call pipeline → format result.

### 4.4 Dependencies (pin major versions in `pyproject.toml`)

Runtime: `pydantic>=2.7`, `pydantic-settings`, `typer`, `rich`, `pyyaml`, `jinja2`, `lxml`, `python-docx`, `pypdfium2` (PDF text extraction; PDFium/BSD — **not** PyMuPDF), `rapidfuzz`, `tenacity`, `litellm`, `python-dotenv`.
External optional: LibreOffice (`soffice`) for DOCX→PDF conversion; `epubcheck` (Java) for EPUB validation; neither is required by the test suite.
Dev: `pytest`, `pytest-asyncio`, `pytest-cov`, `ruff`, `mypy`, `types-pyyaml`, `hypothesis`, `reportlab` (generate the synthetic PDF fixture only), `fonttools[woff]` (only to regenerate font assets), `epubcheck` (optional external binary, not a pip dep).
Stdlib for: `sqlite3`, `zipfile`, `hashlib`, `unicodedata`, `re`, `asyncio`.
Forbidden in v1: `langchain`, `langgraph`, `llama-index`, `chromadb`, `nltk`, `spacy`, `ebooklib` (**AGPL-3.0 — viral for a distributed tool**), `parsivar` (**no licence declared**), and any runtime import of `hazm`.

Notes the implementer must respect:
- **EPUB I/O is ours** (`zipfile` + `lxml` + Jinja templates, ~250 lines). `ebooklib` is AGPL-3.0 and hides OPF/spine details we must control; do not use it for reading or writing. If a gap appears, a patch to `ebooklib` requires a `DECISIONS.md` entry **and** a licence review.
- **Persian normalization is ours** (`persian/normalize.py`), ~60 lines of table-driven rules, because `hazm.Normalizer` defaults are lossy for literary prose (it rewrites guillemets, forces digits, inserts ZWNJ by dictionary guess). `hazm` (MIT) may be an optional *dev* dependency for cross-checking tokenization; `persiantools` (MIT, zero deps) is acceptable if a Jalali-date helper is ever needed, but neither is required for v1.
- **Fonts:** commit `Vazirmatn-Regular.woff2` + `Vazirmatn-Bold.woff2` and `OFL.txt` under `export/epub_assets/`. Do not add a build step that downloads fonts at install time; no font obfuscation (OFL permits embedding; obfuscation adds a failure mode tied to `dc:identifier`).
- **Licence compatibility** is a review item: all runtime deps must be MIT/BSD/Apache-2.0/OFL. `litellm` is MIT; `python-docx` MIT; `lxml` BSD; `rapidfuzz` MIT.

### 4.5 Model configuration

`configs/models/*.yaml` maps aliases to LiteLLM / OpenAI-compatible model strings and parameters. **Default provider: DeepSeek** (the product owner's choice; see 1.6).

```yaml
# configs/models/deepseek.yaml
provider: deepseek
base_url: https://api.deepseek.com
quality:   {model: "deepseek-v4-pro", temperature: null, max_output_tokens: 32768}  # quality mode: critics/reviser
balanced:  {model: "deepseek-flash",  temperature: null, max_output_tokens: 32768}  # default: translator + critics
cheap:     {model: "deepseek-flash",  temperature: null, max_output_tokens: 16384}  # summaries/entities
thinking: disabled                     # becomes extra_body {"thinking":{"type":"disabled"}}
pricing_usd_per_mtok:                  # off-peak; peak doubles (01:00-04:00, 06:00-10:00 UTC, Mon-Fri)
  cache_hit_input: 0.003
  input: 0.15
  output: 0.60
```

Rules: (a) concrete model IDs are **not** hard-coded in code, only in these YAML files; (b) `shz doctor --models` verifies each alias resolves and can answer a 5-token ping (skipped without API keys); (c) **output caps must be ≥ 8,192 tokens, preferably 16,384–32,768** — a 900-token English window can project ~2k Persian tokens, and a single oversized paragraph needs room; `max_output_tokens: 4096` is a known truncation source; (d) the quality judge is an explicit id, never an alias: `eval.judge_model: deepseek-v4-pro` (a different model from the translator; with a single provider true cross-family judging is impossible, so judge scores are **relative only**, and a human BWS read is the reference when one is available — see 8.4); (e) the bake-off (M10) writes a *decision record* on flash vs pro and on Persian quality; until it runs, `shz translate --dry-run` warns that the profile is unvalidated; (f) **`thinking: disabled` is the default for every agent in v1 — but it is a measured default, not a doctrine.** Rationale: reasoning mode ignores `temperature` and bills chain-of-thought as output, and the literature is mixed (see 9.6). The adapter sets `extra_body={"thinking":{"type":"disabled"}}` on every request; a per-agent override is allowed only through `provider.thinking_overrides`, must be justified by the M10 experiment, and must carry its own cost/latency line in the estimate. (g) 1M context / 384K output is available but does not change the window policy — small windows keep cache economics and review granularity intact.

---

## 5. Data model

Full DDL, Pydantic models and artifact formats are in `SCHEMAS.md`. This section defines the semantics the implementer must preserve.

### 5.1 Entities

| Table | Purpose | Key constraints |
|---|---|---|
| `books` | one row per book | `slug` unique; `source_sha256` recorded to detect swapped files; `source_format ∈ epub,pdf,docx,txt,md`; `import_quality` (PDF); `rights_basis` defaults `user_supplied` (informational) |
| `chapters` | ordered chapters | `(book_id, idx)` unique; `kind ∈ front,body,back`; `status` |
| `segments` | translatable units + computed features | `id` text PK (`c003.s0042`), `(chapter_id, idx)` unique; `nodes_json` holds inline markup; `source_text` is marker-stripped plain text; `tokens_est`, `sentence_count`, `dialogue_ratio`, `lang_mix`, `split_of`, `kind`, `risk_score` are columns on this table (no separate meta table) |
| `translations` | **append-only** versions | `(segment_id, version)` unique; `pass ∈ draft,revision,repair,human`; at most one `is_current=1` (partial unique index) and at least one whenever a translation exists; `glossary_hash` + `glossary_terms_json` enable staleness detection |
| `issues` | findings from linters/critics/auditors | unique on `(stage, code, dedupe_key)` where `dedupe_key` is always namespaced; `severity ∈ info,warn,error,critical`; `status ∈ open,resolved,waived` (waiver needs reviewer + reason) |
| `glossary` | DB mirror of `glossary.yaml` | `term_en_norm` unique per `kind`; `status`; `aliases_json`; `gender` metadata |
| `glossary_suggestions` | A5's mid-translation proposals | unique per `(book, term, segment)`; `status ∈ pending,promoted,rejected`; never enforced |
| `ledger` | continuity memory events | `(book_id, chapter_idx, kind, key)`; `value_json` |
| `chapter_summaries` | English summaries | one per chapter |
| `runs` | one row per CLI stage invocation | `stage`, `status`, `config_json`, `stats_json`, `cost_usd` |
| `llm_calls` | per-call accounting | `run_id`, `agent`, `model`, `prompt_hash`, `input_hash`, `tokens_in/out`, `cost_usd`, `latency_ms`, `finish_reason`, `error` |
| `cache` | response cache | `key` PK, `value_json`, `created_at`, `last_hit_at` |

Rules:
- **No UPDATE of translation text.** Corrections and re-runs insert a new version and flip `is_current` in the same transaction. This gives a full audit trail and makes "what changed and why" answerable.
- Every table has `created_at` (UTC ISO-8601 with `Z`). Times are never local.
- Foreign keys `ON DELETE CASCADE` from `books` down; `PRAGMA foreign_keys=ON`.
- `id` stability: chapter/segment ids derive from document order (`c{idx:03d}`, `s{idx:04d}`), **not** from content, so re-import of the same book keeps ids. Content changes are detected by comparing `source_text` against the stored value; a `content_hash` column supports `shz inspect --diff`.
- `translations.notes_json` carries translator notes (e.g., "wordplay: footnote candidate"), never user-facing prose embedded in `fa_text`.

### 5.2 State machines (validated in `db.transition_*`, invalid transition = bug, raise)

```
segment: pending ──▶ drafted ──▶ revised ──▶ done
              │          │           │
              └──────────┴───────────┴──▶ failed      (retryable from pending)
done ──▶ flagged ──▶ revised ──▶ done              (human review or repair)
any  ──▶ skipped                                    (non-translatable, e.g. page numbers)

chapter: pending ─▶ analyzed ─▶ translating ─▶ translated ─▶ audited ─▶ reviewed ─▶ final

run:     running ──▶ completed | failed | aborted
          paused ──▶ running            (budget stop, resume with same run id)
```

`done` means *current translation passed all error-severity linters*. A segment may be `done` and still flagged for human review (warnings, low judge score, high risk score) — flagging is orthogonal to status and lives in `issues`/`risk_score`. An `error` issue is resolved when a newer translation clears it, or **waived** by a human (`shz qa accept --issue ID --note ... --reviewer ...`, which stores `status=waived` + reviewer + reason); a waived error does not block export but stays visible in the report and the manifest. This is the only path by which a known error can reach a published book, and it is recorded.

### 5.3 Artifact authority

| Artifact | Authority | Written by | Read by |
|---|---|---|---|
| `glossary.yaml` | **human** | `shz glossary add/approve/reject`, `apply-corrections` | all translate prompts, QA |
| `brief.yaml` | **human** (after auto-draft) | `shz analyze` writes `.draft`, human edits, `shz analyze --approve` validates | context packer, prompts |
| `anchors.yaml` | **human** (after gate 2) | `shz translate --calibrate` proposes, human approves | context packer |
| `ledger.jsonl` | machine + human corrections | LedgerCurator, human append | context packer, audit |
| `chapter_summaries.jsonl` | machine | summarizer | context packer |
| `translations` | machine + human | pipeline, corrections | export, QA, review |
| `structure.yaml` | human | manual | ingest/chapterize |

`shz glossary sync` = re-import YAML into DB (idempotent, validates duplicates). CLI edits write both atomically (YAML then DB transaction; on DB failure the YAML change is reverted). The DB never wins over YAML for glossary content.

### 5.4 Risk score (deterministic, 0–100, recomputed by `shz qa`)

```
score = 0
+ 45 if any severity=error issue unresolved (waived errors do not count)
+ 20 if any critic issue severity=critical (omission, addition, mistranslation)
+ 15 if glossary presence check failed
+ 15 if L08 error (word ratio outside [0.55, 1.70] or sentence ratio outside [0.60, 1.80])
+ 10 if L08 warning (word ratio outside [0.75, 1.30])
+ 10 if window had > 5 issues total
+  8 if segment contains ≥ 2 unapproved glossary suggestions
+  7 if kind == verse
+  5 if segment contains a translated footnote reference
cap 100
```
Default `review.flag_threshold = 55`; `shz qa` writes `review/flags.csv` sorted by score, plus a random 5% sample of low-risk segments as `sample` flags so quality is not only judged on failures.

---

## 6. Pipeline specification

### 6.0 Ingest (no LLM)

#### 6.0.1 Format detection and extraction

- Detect by magic bytes, not extension: `PK\x03\x04` + `mimetype`/`META-INF` ⇒ EPUB (or DOCX — distinguish by `[Content_Types].xml` and `word/document.xml`); `PK` + `word/` ⇒ DOCX; else try UTF-8/UTF-16 text ⇒ TXT/MD.
- EPUB: open as zip; read `META-INF/container.xml` → rootfile(s) → OPF. Parse `manifest`, `spine`, `guide`, `metadata` (title, creator(s), language, publisher, rights, identifier, cover image via `properties="cover-image"` or meta name="cover"). Read spine items **in spine order**; also read linear="no" items only for footnote content if referenced. Each spine item → one `Document` (xhtml bytes).
- DOCX: `python-docx`; iterate body in document order (paragraphs and tables); map `Heading 1..6`/`Title` styles to heading blocks; preserve bold/italic runs; footnotes are not exposed by python-docx → v1: warn that DOCX footnotes become inline `[^n]` markers and are treated as text (documented limitation).
- TXT/MD: split on blank lines into paragraphs; Markdown heading lines (`#`) become headings, `>` quote, code fences become `pre` blocks marked non-translatable.
- **PDF (best-effort):** open with `pypdfium2`; iterate pages, extract characters with boxes. **Scanned detection first:** if a page has < 20 extractable characters and < 80% of pages have a text layer, fail with `PDF_NO_TEXT_LAYER — OCR is not supported in v1`. Reconstruct reading order: cluster characters into lines by y-band, detect 1 vs 2 columns from an x-histogram, order lines top-down/left-right; merge lines into paragraphs when the vertical gap is normal **and** the next line starts at the paragraph indent (or the previous line does not end a sentence); drop repeated headers/footers (a line on > 30% of pages at the same y) and page numbers; join end-of-line hyphenation (`exam-\nple` → `example`). Chapter detection is heuristic (largest-font lines matching `chapter|part|book` patterns) and **must be confirmed through `structure.yaml` when `import_quality` is below `high`**. Figures are not extracted in v1 (skipped with an `info` note); footnotes are recovered as smaller-font blocks at the page bottom.
- **PDF import quality (0–1)** is stored as `books.import_quality`: fraction of pages with a text layer, mean words/paragraph sanity (20–180), hyphenation-join rate, repeated-line removal rate. `shz inspect` prints it; `< 0.6` blocks `translate` until the user supplies `structure.yaml` or passes `--accept-pdf-quality`. A low score is shown as a warning, never hidden.
- Compute and store `source.sha256`; refuse to re-init the same slug with a different hash unless `--force` (which archives the old DB as `book.db.bak-<ts>`).

#### 6.0.2 Block model and classification

A `Block` = one structural unit extracted from a document, with `kind`:

| kind | Detection | Translatable? |
|---|---|---|
| `heading` | `h1..h6`, `Heading *` style, Markdown `#` | yes (title case rules apply) |
| `prose` | `p`, `div` with ≥ 1 sentence | yes |
| `quote` | `blockquote`, class containing `quote`/`epigraph` | yes, keep indentation semantics |
| `verse` | `<p>` with `<br>` line breaks, class `verse`/`poem`, or 3+ short lines | yes, preserve line breaks |
| `footnote` | `epub:type="footnote"`, `aside`, class `footnote`, `[^n]` defs | yes, keep marker id |
| `caption` | `figcaption`, `alt` text of images | yes |
| `front` | `epub:type` in `titlepage, copyright-page, dedication, epigraph, preface, foreword` | dedication/epigraph yes; copyright-page/TOC default **no** (config) |
| `nav` | `epub:type="toc"`, `nav` element | no (regenerated) |
| `image` | `img`, `svg` | no (copied; caption handled separately) |
| `table` | `table` | cells translated as segments with `kind=table`, structure preserved |
| `pre`/`code` | `pre`, `code`, class `code` | no (verbatim) |
| `break` | `<hr>`, `***`, `* * *` | no (scene break, preserved as a marker) |

Unknown blocks default to `prose` and are logged once per document. The classifier must be a pure function `classify(element, context) -> BlockKind` with a 60-line lookup table and unit tests per row. **For PDF input** the same `BlockKind` set is produced from font/geometry heuristics: largest-font lines → `heading`; body-size justified lines → `prose`; smaller-than-body text at the page bottom → `footnote`; short lines with large leading → `verse`; centered short lines after a heading → `quote`/`epigraph`.

Two structural pairings must survive ingestion:

- **Footnote pairing.** A `noteref` anchor (`epub:type="noteref"`, `href="#id"`) is paired with the element whose `id` equals the fragment. The pair is recorded as an atomic node `{"kind": "noteref", "target_id": "..."}` inside the referring block, and the target element becomes a `footnote` segment. A reference with no target, or a note with no reference, raises `L19`; an orphan reference is kept as plain text so nothing is lost.
- **Figure/caption pairing.** An `img`/`svg` becomes an atomic node in its block; a `figcaption` (or the image's `alt`) becomes a `caption` segment immediately after it in reading order. Export rebuilds `<figure>` with the original image plus the translated caption, and translates `alt` text for accessibility.

#### 6.0.3 Inline markup preservation (the part that breaks naive tools)

Do **not** pass HTML to the translation model, and do **not** strip markup. Model each block as an ordered list of **text nodes**, where each node knows the inline formatting path it lives under:

```
Block html:  He was <em>very</em> angry, <a href="n1">she said</a>.
nodes: [
  {id: 1, path: [],      text: "He was ",      kind: "text"},
  {id: 2, path: ["em"],  text: "very",         kind: "text"},
  {id: 3, path: [],      text: " angry, ",     kind: "text"},
  {id: 4, path: ["a:href=n1"], text: "she said", kind: "text"},
  {id: 5, path: [],      text: ".",            kind: "text"}
]
```

The translator receives the block as text with **unique sentinels** before each node:

```
He was ⟨2⟩very⟨/2⟩ angry, ⟨4⟩she said⟨/4⟩.
```

Rules for sentinels:
- Format: `⟨<n>⟩` opening and `⟨/<n>⟩` closing (U+27E8/U+27E9). Fallbacks when a model mangles them: `⟦<n>⟧` … `⟦/<n>⟧` (U+27E6/U+27E7), then `[[#<n>]]` … `[[/<n>]]`. **Never use `{{…}}` as a sentinel** — it collides with the Jinja2 prompt templates. If marker validation fails twice, retry with the next format and record which format succeeded.
- Sentinels are allocated per **node with a path**, not per tag: adjacent nodes under the same path share one wrap only if they are contiguous in the original. Wrapping per node is simpler and always correct.
- `kind: "atomic"` nodes (images, `<br>`, footnote refs, hard links, math, foreign text kept verbatim) are emitted as `⟨n⟩` **without** closing tag and their content is frozen: the model must copy the token but not translate inside it. Reconstruction re-inserts the original element.
- If a block has no inline markup (the common case, ~80%+), send plain text with no sentinels. **Never pay for markers you do not need.**

Output contract: the model returns `fa_markup` containing the same sentinel ids. Post-validation (`persian/markup.py`), in this exact order:
1. Every id present exactly once as a pair (or once for atomic); no unknown ids.
2. **One order rule:** a pair may move together with the text it wraps (translation may reorder clauses), but pairs may not cross (`⟨1⟩…⟨2⟩…⟨/2⟩…⟨/1⟩` is valid; `⟨1⟩…⟨2⟩…⟨/1⟩…⟨/2⟩` is not) and an atomic node stays where its text stays. This is the rule the prompt states and the rule the validator enforces — do not have two different rules.
3. Reconstruct HTML by walking the sentinel sequence, wrapping each run in its node's original tags/attributes; atomic nodes re-emit the original element verbatim.
4. `normalize_fa` the **text runs only** (sentinels are never passed to the normalizer), reassemble `fa_markup`, then strip sentinels to derive `fa_text`. See `SCHEMAS.md` §6 for the required order.

Failure ladder for markup: retry the window once with a stricter reminder → retry the single segment → if still broken, store the best-effort `fa_markup` (pairs applied in order) and raise an `error` issue `L02`. Never silently drop emphasis; human review must see it.

#### 6.0.4 Chapterization

Priority order:
1. `artifacts/structure.yaml` if present (explicit list of chapter titles or start-phrases; validated against segments, error if not found).
2. Top-level headings in the spine: a document with ≥ 2 `h1`/`h2` splits at those headings; the heading becomes `chapters.title_en` and its first segment.
3. One chapter per spine document (title = first heading in the doc, else `document <n>`).
4. Fallback: one chapter for the whole book, plus a warning suggesting `structure.yaml`.

Post-processing: merge a chapter with < 40 words into the following chapter (except front matter); split front matter (`kind=front`) from body; detect a chapter that is only an image (cover) and mark `kind=front, skip_translation`. `chapters.title_fa` is filled deterministically after the chapter is translated: it is the current translation of the chapter's first heading segment, with all inline markup stripped; if the chapter has no heading, use `فصل <n>` (front matter uses `پیشگفتار`/`تقدیم`/`یادداشت ناشر` by `kind`). Export never invents a title beyond this fallback, and the nav document is built from `title_fa`.

#### 6.0.5 Source normalization (before segmentation, never for display)

`normalize_source(text)`:
- Unicode NFC; replace `&nbsp;` with space; collapse runs of spaces/tabs; strip trailing spaces.
- Join words hyphenated across line breaks (`exam-\nple` → `example`) **only** when the break is inside a word and the following line starts lowercase; keep real hyphens.
- Remove soft hyphens `U+00AD`; remove zero-width spaces inherited from PDF conversions.
- Curly quotes are preserved in `source_html` but `source_text` uses typographic quotes as-is (fidelity critic compares against `source_text`).
- Do **not** fix the author's grammar or spelling. If the source has an obvious OCR error, record an `info` issue, do not "improve" it.
- Record paragraph-level language mix; blocks with `lang_mix > 0.3` and no Persian are treated as **foreign content** (`kind` stays `prose`, the flag is a column/feature): translate or keep verbatim per `brief.foreign_text_policy`, and record the decision once per foreign language in the ledger. A `foreign` segment is never force-translated into Persian when the brief says keep-original.

#### 6.0.6 Segmentation rules

- One translatable block = one segment. Never split a paragraph. Never merge paragraphs (merge only if a paragraph is a 1–3 word fragment split by a page break and ends without terminal punctuation — then merge with the neighbor and log).
- Headings, captions, table cells, footnote texts and verse stanzas each become their own segment(s); verse = one segment per stanza, with `\n` preserved inside `source_text`.
- Segments whose normalized text has < 2 characters, is only digits/punctuation, or matches `protected_latin` exactly → `skipped`.
- `source_text` stores the marker-free plain text; `nodes_json` stores the markup structure; `source_html` stores the original outer HTML for reconstruction audits.

#### 6.0.7 Acceptance checks for M1

- Reconstruct every block from `nodes_json` and compare to the sanitized original HTML: **byte-identical** (after normalization) for a corpus of 3 EPUBs, 1 DOCX and 1 TXT/Markdown fixture. This round-trip test is mandatory.
- 100% of source paragraphs are represented (count check).
- Re-running `shz init` on the same file is idempotent (same ids, same hashes).

### 6.1 Analysis pass

#### 6.1.1 Sampling (`build_sample(book)`)

Budget: ≤ 18,000 tokens, deterministic:
1. First 4,000 tokens of the body (voice establishment).
2. Full first chapter if < 6,000 tokens, else first 4,000.
3. From each remaining chapter (max 8 chapters, evenly spaced): first 500 tokens + the middle 300 tokens.
4. 12 dialogue-dense paragraphs and 12 narration-dense paragraphs (highest/lowest quote ratio), max 2 per chapter.
5. 3 verse/quote blocks if any.
6. Front-matter dedication/epigraph if any.
Record the sample manifest in `runs/<id>/manifest.json` so the brief is reproducible.

#### 6.1.2 Book Brief (A1, `BookBrief` schema in SCHEMAS.md)

The analyst must output *decisions*, not adjectives. Required fields:

| Field | Example |
|---|---|
| `genre` / `subgenre` | literary fiction / family saga |
| `era_and_setting` | 1920s American South, rural |
| `pov` | first person, retrospective narrator |
| `narrative_tense` | past simple with present-tense interludes |
| `register` | colloquial narration + formal dialogue for one character |
| `register_target_fa` | `literary-standard` (narration in `معیار ادبی`, `گفتاری` only inside quotes) |
| `address_policy` | `تو` between siblings/close friends, `شما` for strangers/elders; record per relationship in ledger |
| `sentence_rhythm` | short declaratives, frequent fragments, comma splices intentional |
| `dialogue_style` | long unbroken turns, heavy dialect — normalize dialect to colloquial Persian markers, not regional slang |
| `humor_irony` | dry, understated — preserve by litotes, never explain |
| `cultural_references_policy` | keep + short footnote for items a Persian reader cannot infer; max 1 footnote per 2,000 words |
| `name_policy` | phonetically transliterate per Persian publishing convention; keep initials and titles (`Mr.` → `آقای`) |
| `measurement_policy` | keep imperial, add Persian equivalent in parentheses only when plot-relevant |
| `foreign_text_policy` | keep French phrases, italicize, footnote first occurrence |
| `taboo_policy` | translate faithfully, no softening, no added explicitness |
| `style_references` | name 1–2 Persian translators/works whose voice is a useful analogue (e.g., translators of Hemingway, Faulkner, Woolf) — a compass, never an instruction to imitate |
| `do_rules` | 5–10 imperative rules derived from the sample |
| `dont_rules` | 5–10 anti-patterns specific to this book |
| `uncertainties` | questions for the human (max 5) |

Then `compute_style_metrics(source)` (deterministic, no LLM): mean/median sentence length, sentence-length distribution, % dialogue chars, paragraph length, punctuation density, unique-word ratio, em-dash/semicolon frequency. Stored per book and used later for **target-side drift detection** (Persian metrics are compared against the book's own translated baseline, not against English directly).

Gate 1a (**optional**): `shz analyze` writes `artifacts/brief.draft.yaml`. With `pipeline.analyze.brief_approval: auto` (default) it validates the draft and freezes `brief.yaml` immediately, recording its hash; with `manual`, a human edits and runs `shz analyze --approve`. Translation never blocks on the brief: if no brief exists it uses the conservative built-in defaults and marks the run `experimental`.

#### 6.1.3 Chapter summaries (A2, parallel)

One call per chapter; output 120–180 English words: what happens, who does what, emotional arc, tone/voice notes, and any continuity facts needed by the next chapter (names introduced, relationships, time jumps, objects/places). Second person neutral, no interpretation beyond what is needed for continuity. Store `chapter_summaries` and export JSONL. If a chapter is > 8,000 tokens, summarize in 2 chunks and merge deterministically (the model gets both chunk summaries and merges in one call).

#### 6.1.4 Entity extraction (A3, batched)

Batches of ~4,000 source tokens; each batch returns entities with `kind`, `surface_forms` (all observed incl. titles/possessives), `count`, `gender` if inferable (for names), `first_segment_id`, and `context_quote` (one short example). Merge deterministically: normalize surface forms (casefold, strip possessives/titles), fuzzy-cluster with `rapidfuzz` token-set ratio ≥ 90, keep the most frequent surface as canonical. Keep entities with `count ≥ 3`, all proper names, all forms of address, all recurring motifs the extractor marks as `motif`; cap at 400. If a book is under 3,000 words, extract in one call.

#### 6.1.5 Gate 1b — glossary approval (**optional**)

This gate exists only for `glossary.mode: auto` with `glossary.approval: manual`. In the default `off` mode it is skipped entirely; in `provided` mode the user's file is validated, not approved.

When it runs, `shz glossary review` prints a table (term, kind, count, proposals, confidence) and supports:
`shz glossary approve <id...>`, `approve --min-confidence 0.85`, `reject`, `edit <id> --fa "..."`, `add --en "..." --fa "..." --kind term`, `import terms.csv`, `export glossary.yaml`.
Rule in this mode: all `character` and `place` entries must be approved before translation; `term`/`motif` may stay proposed and are enforced only after approval (with a warning if a proposed term appears > 10 times).

#### 6.1.6 Glossary content rules (A4 proposer)

- `character`: transliterate, then check against established Persian usage if the model knows it (e.g., classical literature characters usually have canonical Persian forms). Persian has no grammatical gender, so the goal is a recognizable, pronounceable form + consistent spelling; record gender in notes for the editor (helps pronoun choices and honorifics), not for grammar.
- Titles/forms of address: `Mr.`→`آقای`, `Mrs.`→`خانم`, `Miss`→`دوشیزه/خانم` per era and brief; keep the full name mapping (`Mr. Bennet` → `آقای بنت`, with alias `بنت`).
- `place`: established Persian exonyms when they exist (London→لندن, Paris→پاریس); otherwise phonetic transliteration; never translate toponyms literally.
- `org`/`term`: prefer a Persian equivalent if the concept exists; transliterate + short gloss if it does not; keep acronyms in Latin if the Persian reader would expect it (e.g., `FBI` → `افبیآی` per brief).
- `motif`: a recurring image/word; choose a Persian expression that can carry the same connotations and note its first occurrence for the ledger. Motifs must be *approved by a human* — model choice here is stylistic and high-leverage.
- Confidence is calibrated: only ≥ 0.9 may be auto-approved with `--min-confidence 0.9`; everything else requires human eyes.
- Proposer must output `alternatives` (max 2) and a one-line `rationale`, which is shown in review. Reject candidates that are just transliterations of a word that has a normal Persian equivalent.

### 6.2 Glossary modes and enforcement

**Modes (`glossary.mode`):**

| Mode | How it is selected | What runs | Cost |
|---|---|---|---|
| `off` (**default**) | nothing | no A3/A4, no L03. A deterministic capitalized-token scan builds a **proper-noun list** (English forms only) that is injected so names are spelled consistently; L12 detects variants. This is the zero-config path | $0 |
| `provided` | `--glossary terms.yaml\|csv` | user file parsed and validated (`en`, `fa`, optional `kind`/`aliases`); treated as approved; L03+L12 enforce. **Recommended cheap consistency win** | $0 |
| `auto` | `--auto-glossary` | A3/A4 propose candidates + Persian forms; `glossary.approval: auto` auto-approves ≥ 0.9, else manual gate 1b | ~$0.05–0.15 |

The three enforcement layers apply to `provided`/`auto`; in `off` only the name-consistency part of layer 1 and L12 run.

1. **Prompt layer:** all approved entries live in the cached prefix (Section 6.4), so the model sees the whole approved glossary. Only above 300 entries does a per-window subset (cap 60, longest-first) apply to the overflow.
2. **Deterministic layer (L03):** for each approved term with a surface occurrence in the window source, require the approved `fa` form (or an approved alias) to appear in the window output, ignoring ZWNJ/spacing variants; count mismatches. Also flag *unapproved variants*: if a similar Persian form (rapidfuzz ≥ 88) appears but is not the approved form → `error` (drift).
3. **Repair layer:** if L03 fails, run a targeted repair prompt (single call, only failing segments, includes the exact required forms); if it still fails, flag for human review. Never rewrite the glossary silently.

Dynamic suggestions: A5 may return `glossary_suggestions` (term_en, proposed_fa, reason, segment_id). They are stored in `glossary_suggestions` with `status=pending` (never enforced), deduplicated per `(term, segment)`; `shz glossary pending` lists them at chapter close, `promote <id>` creates a `proposed` glossary entry for human approval, `reject <id>` dismisses it. A new recurring proper noun that appears ≥ 3 times without a glossary entry is a `warn` issue `L12` in every mode.

### 6.3 Window planning (`pipeline/window.py`)

Deterministic function `plan_windows(chapter) -> list[Window]`:

```
target: max_paragraphs=8 (config), max_source_tokens=900, overlap=2 paragraphs (context only)
walk segments in order:
  add segment to current window if it fits and no rule forbids
  close window when full, or at a "hard boundary"
never split: verse stanza, footnote text, table row, letter/document block, segment itself
soft preferences:
  - avoid starting a window mid-dialogue-exchange: if previous segment is dialogue and
    current is dialogue, keep them together when budget allows
  - never put a chapter's first heading alone (always with ≥ 1 following prose block)
  - keep a footnote-referencing paragraph in the same window as its footnote text
    when the footnote is a sibling block; if not possible, translate footnote after
  - if a segment alone exceeds max_source_tokens: it becomes a single-segment window
    (allowed up to 2× budget; beyond that, split at paragraph-internal sentence
    boundaries ONLY if kind=prose and the block is > 1,800 tokens, and mark the
    segment `split_of=<parent>` so export re-joins with a space/newline)
window.overlap_prev = last 2 segments of previous window (source + translated FA)
```

Every window gets a deterministic `window_id = f"{chapter_id}.w{idx:03d}"` (3 digits; the whole-book fallback chapter can exceed 99 windows). The plan is stored in the run manifest (json list of segment ids) so resuming after code changes is possible only if the plan hash matches; otherwise the run refuses with "window plan changed; run `shz translate --replan`" (which archives affected translations).

### 6.4 Context pack (`context/packer.py`)

The pack is assembled **stable-prefix first**, because DeepSeek bills cache hits at ~$0.003–0.006/M versus $0.15–0.30/M for misses. Blocks 1–4 are byte-identical for every window of a book once brief/glossary/anchors are fixed, so they form one large cached prefix; only blocks 3b and 5–9 vary per window. Blocks 1–4 and 8 are **non-evictable** — the prefix is nearly free and the source text is the task. Eviction order for the variable suffix: 6 ledger → 7 immediate context → 5b rolling summary → 3b cast → 2 brief card, and if it still does not fit, reduce the window rather than delete the source.

| Pri | Block | Budget | Cache | Content |
|---|---|---|---|---|
| 1 | System rules | 1,600 | **prefix** | Rendered P05 prompt + `book_rules` (brief's do/dont + conventions + register) |
| 2 | Brief card | 500 | **prefix** | genre, era, POV, register, address policy, footnote policy, cultural policy |
| 3 | Approved glossary | 4,000 | **prefix** | ALL approved entries, `EN = FA`, longest-first (≈300 entries). Above 300, the first 300 stay in the prefix and the overflow is a per-window subset. In `glossary.mode: off` this block is the deterministic proper-noun list |
| 4 | Anchors | 700 | **prefix** | 2–3 approved EN/FA pairs (fixed after gate 2) |
| 3b | Cast / attribute block | 80 | suffix | Characters established in the ledger (max 8 most recently active, plus any named here): `EN = FA · gender · role · address form`. Changes per chapter, so it cannot live in the cached prefix |
| 5 | Chapter summary | 250 | `chapter_summaries.summary_en` |
| 5b | Rolling chapter summary | 120 | `chapters.rolling_summary_fa` — 2–3 Persian sentences of "what has happened so far in this chapter", refreshed every 20 windows by a cheap model; gives intra-chapter continuity without re-reading prior windows |
| 6 | Ledger slice | 350 | ≤ 15 events relevant to named entities in the window (character states, address decisions, terms) |
| 7 | Immediate context | 500 | last 2 source segments + their final Persian, labelled `PREV_SOURCE` / `PREV_TRANSLATION` |
| 8 | Window source | 1,000 | sentinel-marked source text, one numbered block per segment |
| 9 | Extra instruction | 120 | e.g. "this window is a letter; keep line breaks", "contains verse" |

System text for A5 is generated by rendering `p05_translator.md` with these variables; output must be a JSON object matching `TranslatorOutput` (ids must match exactly the requested segment ids). **Budget the output on the Persian side**, not by source tokens: `max_output_tokens = clamp(ceil(source_tokens × fa_ratio × 1.3), 512, model_max_output_tokens)` where `fa_ratio` starts at 2.0 and is recalibrated from actual usage (9.6). If a model's cap is only 4,096 (some are), a 900-token window will truncate — the pre-flight in 9.6 splits the window rather than persisting a truncated translation.

Rendering must be **stable across windows for the prefix** (blocks 1–4: prompt file + `book_rules` + full glossary + anchors): no timestamps, no random ordering, no absolute paths (use `book`/`chapter` labels). Volatile blocks go in the user message, and the rendered prefix for consecutive windows must be byte-identical or DeepSeek's cache will miss.

### 6.5 Translation loop (`pipeline/translate_pass.py`)

For each window (sequential within a chapter):

```
1  pack = packer.build(window, state)                       # deterministic
2  draft = A5.run(pack)                                     # TranslatorOutput
   validate ids == window ids, all non-empty, markup valid
   persist draft version (pass="draft", glossary_hash, glossary_terms_json), transition -> drafted
3  findings = []
   if cfg.pipeline.critics.fidelity: findings += A6.run(pack, draft)  # Critique
   if cfg.pipeline.critics.style:    findings += A7.run(pack, draft)  # Critique
   persist issues (dedup by code+segment+quote hash)
4  if any actionable finding and rounds < cfg.pipeline.critics.max_revision_rounds (default 1):
       revised = A8.run(pack, draft, findings)               # apply only quoted issues
       validate; if worse by deterministic metrics (length outlier, new lint errors,
       missing sentinel) -> keep draft and record `revision_regressed`
       else persist revision (pass="revision"), flip is_current
5  run linters (L01..L20) on current translation
   if errors: targeted repair (A8 with only errors) up to 2 rounds
6  persist final current translation, compute risk score, update window state
7  every N windows (default 20): refresh running summary of the chapter (2-3 sentences,
   cheap model) for intra-chapter continuity; always include it in pack block 5b
```

Rules and edge cases:
- **Idempotency:** if a window's cache key and inputs are unchanged, re-running reuses cached LLM responses and produces no new versions; if `--force`, versions are appended.
- **Partial JSON from the model** (e.g., 5 of 7 segments): accept the valid subset, immediately re-request only missing ids in a single "continue" call; never fabricate.
- **Model returns prose preamble:** strip/fail via schema validation; the prompt forbids it. Do not regex-hunt for JSON in prose more than once — after 2 failures, fall back to per-segment single calls.
- **Omission risk:** for each segment compare `len(output)/len(source)` in characters; outside [0.55, 1.8] → `warn` and force fidelity critique for that segment (even if critics are globally disabled).
- **Content-filter blocks:** retry once with `fallbacks` model; if still blocked, mark segment `failed` with `L13 content_filter`, never silently censor.
- **Critic disagreement:** if A6 says "omission" and A8 claims it applied a fix that A6 flags again, keep A6's version, open a `critical` issue and flag the segment; log `disagreement`.
- **Verse:** always run A7; require line-count equality (±1) and flag otherwise.

#### 6.5.1 Gate 2 — first-chapter calibration and anchors

Sequence — **optional**, only when the owner wants the calibration discipline (the default unattended run skips all of it):

1. `shz translate --chapters 1` runs with the brief + glossary but **no anchors**.
2. `shz qa && shz review build --chapter 1` → the human reads chapter 1 in the bilingual report and applies corrections (`shz review apply`).
3. `shz translate --calibrate` (equivalent to `shz anchors propose`) selects up to 3 anchor candidates from chapter 1's `done` segments: longest segment with `kind=prose` and no dialogue quote, longest dialogue-heavy segment, and the segment with the most inline emphasis (deterministic tie-break: segment id). It writes `artifacts/anchors.draft.yaml` with source, current approved translation, and a one-line note per pair.
4. The human edits/approves: `shz anchors approve` writes `artifacts/anchors.yaml`. `shz anchors reject <id>` removes a candidate; if fewer than 2 remain, run with no anchors (warning: voice consistency will rely on the brief alone).
5. Subsequent runs include the approved anchors when present. Missing anchors **never block** the zero-config path: the run logs one warning and continues with the brief (and glossary, if any). `--require-anchors` is available for users who want the discipline; it turns the warning into an error.

Anchors are *voice examples*, not translation memory: they are never reused verbatim for similar sentences, and they are capped at 3 pairs (~700 tokens) so they cannot crowd the glossary out of the context pack.

### 6.6 Bookkeeping after each chapter (`A9 LedgerCurator`)

At chapter close, one cheap call receives: chapter summary, approved glossary additions from this chapter, list of new proper nouns with counts, and the chapter's reported decisions (from translator notes). Output `LedgerUpdate`:
- `characters[]`: `en`, `fa`, `role`, `gender`, `address_forms` (normalized-name keys), `notes`. The chapter where each first appears is the ledger row's `chapter_idx`, not a model field.
- `terms[]`: new terms with `status ∈ proposed|approved` and their current rendering.
- `decisions[]`: e.g. "روایت: گذشته ساده؛ دیالوگها: محاورهی کنترلشده", "خطاب X↔Y: تو", "پینوشت برای اشارهی فرهنگی در فصل ۲".
- `open_questions[]`: items for the human (max 5).
Merged into `ledger.jsonl` (append-only, dedup by `(kind,key)` keeping the latest). The packer uses this to keep the next chapter consistent. Human edits to `ledger.jsonl` are allowed and respected on next sync.

Also at chapter close: compute chapter metrics (mean Persian sentence length, dialogue ratio, unique-word ratio); compare with the book's running baseline; if |z| > 2.5 on any metric → `warn` issue `L15 style_drift` on the chapter. This catches the classic "chapter 9 sounds like a different translator" failure.

### 6.7 Whole-book audit pass (`shz audit`)

Runs after all chapters are `translated`; budget-capped, sampled:

1. **Glossary audit (code):** full scan for unapproved variants and missing forms; produce a repair queue.
2. **Name-form audit (code + LLM):** cluster all Persian name tokens; flag forms not in the glossary; LLM adjudicates only ambiguous cases (is this a new character or a variant?).
3. **Consistency auditor (A10):** for each chapter, sample 3 windows + the chapter summary and ask: contradictions with the ledger, pronouns with unclear antecedents, address-form drift, terminology drift, timeline inconsistencies. Output `AuditFindings` with severity; only `critical`/`error` items become repair tasks.
4. **Read-through editor (A11):** reads the Persian chapter alone (no source) with the brief + `chapters.rolling_summary_fa` as style state, and reports at most 5 issues per chapter: awkward phrasing, translationese, rhythm break, unnatural dialogue, register slips. Rationale: source-free reading is the only way to catch "correct but not Persian". Output includes a suggested rewrite for each issue. **Nothing is auto-repaired from this pass**: `major` issues become review suggestions in the report; `minor` ones are listed only. (P11's severity vocabulary is `minor|major`; the DB mapping still applies.)
5. **Back-translation sample — NOT in v1.** `passes.backtranslate` stays `false` and A13/P13 are post-v1; the measured evidence for back-translation as a quality method is weak (see `research/prior-art.md` §4). Do not wire it in v1; revisit only in M12+.
6. **Aggregate report:** `runs/<id>/report.md` with coverage, adherence %, issue counts by code/severity, cost, time, top 20 riskiest segments, and the list of chapters with style drift.

### 6.8 Export (`shz export`, no LLM)

#### 6.8.1 EPUB 3 (primary; the only publication format in v1)

Structure:
```
mimetype                        (first ZIP entry, STORED, exactly "application/epub+zip")
META-INF/container.xml
OEBPS/content.opf
OEBPS/nav.xhtml
OEBPS/css/book.css
OEBPS/fonts/Vazirmatn-Regular.woff2, Vazirmatn-Bold.woff2, OFL.txt
OEBPS/images/*                  (copied, deduped by hash)
OEBPS/text/chapter-001.xhtml ... (one file per chapter; front matter included)
```

**Packaging (the #1 cause of "EPUB won't open"):** write `mimetype` first with `ZIP_STORED`, then everything else with `ZIP_DEFLATED`. All other entries sorted by name for determinism.

**OPF (`content.opf`):**
- `<package version="3.0" unique-identifier="pub-id" xml:lang="fa">` — include `dir="rtl"` for correctness but **never rely on it**: EPUB 3.3 lists the package-level `dir` as under-implemented by reading systems.
- Metadata: `dc:identifier` (stable UUID derived from `book.slug` + `source_sha256`, so re-exports keep the same id), `dc:title` (Persian), `dc:language` = **`fa`** (not `fa-IR`), `dc:creator` (author, original script), `dc:contributor` (translator/editor from config), `dc:source` (original title + ISBN if present), `dc:rights`, `dc:publisher` optional, `<meta property="dcterms:modified">` **required** by EPUB 3.3, defaulting to the maximum `translations.created_at` in the book (not wall-clock; see 3.5).
- Accessibility metadata (EPUB Accessibility 1.1, cheap and correct): `schema:accessMode=textual`, `schema:accessModeSufficient=textual`, `schema:accessibilityFeature=structuralNavigation`, `schema:accessibilityHazard=none`, `schema:accessibilitySummary`, `dcterms:conformsTo=https://www.w3.org/TR/epub-a11y-11/`.
- `<spine page-progression-direction="rtl">` in reading order. This is the only standardized global page-flow switch; it must be set even though each XHTML also declares `dir`.
- Manifest: `nav` document with `properties="nav"`; cover image with `properties="cover-image"`; fonts with `media-type="font/woff2"`; **no** `encryption.xml` (no font obfuscation — OFL permits embedding, and obfuscation keys off `dc:identifier`, so a changed id silently breaks fonts).
- No remote resources, no scripts, no `-epub-writing-mode`/vertical writing modes.

**XHTML (every file, not just the first):**
```xml
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"
      lang="fa" xml:lang="fa" dir="rtl">
```
`lang`/`xml:lang` drive TTS pronunciation and hyphenation; each spine item is treated independently by many reading systems, so setting it once is a bug.

**CSS (`export/epub_assets/book.css`)** — the exact recipe:
```css
html { direction: rtl; }
body { font-family: "Vazirmatn", "Noto Naskh Arabic", serif;
       text-align: justify; text-justify: inter-word;
       line-height: 1.9; hyphens: none; -epub-hyphens: none;
       word-spacing: 0; letter-spacing: normal; }
p { margin: 0; text-indent: 1.5em; }
h1, h2, h3 { text-align: start; text-indent: 0; line-height: 1.6; }
h2 + p, hr + p { text-indent: 0; }
.verse { white-space: pre-line; text-indent: 0; }
.ltr, code, .isbn { direction: ltr; unicode-bidi: isolate; text-align: left; }
.endnotes { font-size: 0.9em; }
```
Rules: never set `letter-spacing` (breaks cursive joining); never enable hyphenation; never insert U+0640 kashida to fake justification (CSS has no kashida; accept space justification); wrap any Latin run in `<span class="ltr" dir="ltr" lang="en">` or `<bdi dir="ltr">`, preferring `unicode-bidi: isolate` over `embed`/`bidi-override` (override is the #1 cause of reversed English titles); keep the Latin run's own sentence-final period inside the LTR span.

**Notes (footnotes vs endnotes):** v1 emits **endnotes per chapter** with `epub:type="endnote" role="doc-endnote"` and a backlink; `epub:type="footnote"` renders as popups on Thorium/Apple Books but as plain links elsewhere, so it is unreliable across the ecosystem. Keep the original footnote numbering, and use `–` (U+2013) for numeric ranges with Persian digits (ASCII `-` reorders badly next to bidi class AN digits).

**Bidi regression strings** (must render correctly; assert in the manual test checklist and, where possible, in the bilingual HTML test): `ISBN 978-0-00-000000-0`, `2026-11-05`, `10/12`, `(Routledge, 1998)`, `«The Waste Land»`, `۵۰٪`, `ص ۱۲–۱۵`, `A. Smith`, `He said "سلام" and left.`.

**Validation:** `shz export` always runs internal checks — zip order/mimetype stored, every manifest href exists, every fragment id exists, unique ids per file, spine order matches chapters, all `dc:*` present, `dcterms:modified` present, no remote URLs, no scripts, every body segment `done`. Then `scripts/run_epubcheck.sh` runs `epubcheck` if Java is available and fails the export on `ERROR`. Acceptance: **zero errors** on a real book.

**Determinism:** sorted entries, fixed mtime (`1980-01-01T00:00:00Z`), stable UUID, `dcterms:modified` defaulting to the newest translation timestamp; two exports of an unchanged DB are byte-identical.

#### 6.8.2 DOCX (secondary)

python-docx, `w:bidi` on every paragraph, `w:rtl` on runs, `w:rFonts` set for both Latin (`ascii/hAnsi`) and complex-script (`cs`) slots, `w:szCs` matching `w:sz`, Vazirmatn as the document default font, document defaults `w:lang w:bidi="fa"` (same tag as the EPUB to keep one language value across formats), headings mapped to Word heading styles with Persian numbering, page size A5 default (configurable), mirrored margins, footnote texts collected as **endnotes** at the chapter end (python-docx has no footnote API — document this limitation in README), images embedded at original resolution, cover as first page if available. **This is the primary "open, editable, PDF-convertible" artifact** — the user can edit it in Word/LibreOffice and export a PDF by hand, or let `shz export --pdf` do it (6.8.5).

#### 6.8.3 Markdown (open editable text)

`out/<slug>.fa.md`: YAML front-matter (`title`, `author`, `translator`, `lang: fa`, `dir: rtl`), chapters as `##`, paragraphs as blank-line-separated blocks, verse as blockquotes with hard breaks, endnotes as a trailing section. Best for git diff, search, and pandoc/Quarto conversion; lossy for inline styling. This is the third default format because it is the most open and the easiest to re-process.

#### 6.8.4 Bilingual edition (optional side product)

Off by default; `shz export --bilingual` adds it. Single HTML: chapter sections, each segment as a row `source | translation`, `dir` per cell, issue badges, filter box. No external assets. Intended for the translator and for bilingual readers, not as the primary deliverable.

#### 6.8.5 PDF (conversion, not typesetting)

`shz export --pdf` requires LibreOffice (`soffice`) on PATH: run `soffice --headless --convert-to pdf --outdir out out/<slug>.fa.docx`, then verify the file exists, is non-zero and starts with `%PDF`. If `soffice` is missing, print the exact manual command instead of failing the whole export. **State the limitation in the output and README:** a LibreOffice RTL PDF is readable but not print-perfect (no kashida justification, limited page mirroring, endnote styling varies); for a typeset book, the v2 path is XePersian/LaTeX. Never call this "print-ready".

#### 6.8.6 Plain text

`out/<slug>.fa.txt`: chapters separated by `\n\n`, headings as `# `, verse preserved, footnotes as endnotes. Used for diffing and for feeding the read-through/critique pipelines of future books.

### 6.9 Review loop (`review/`)

`shz review build` generates `review/report.html`:
- Header: book title, coverage, adherence %, open issues by severity, cost, model profile, prompt versions.
- Filters: chapter, severity, issue code, risk score, only-flagged, search text; keyboard next/prev.
- Row: segment id, source (with markup indicated), current translation (editable textarea with RTL), issue list (code, severity, message, quotes), translator notes, glossary terms in this segment, and buttons `Accept`, `Needs work`, `Next`.
- Edits are stored in browser memory and exported on click as `corrections.jsonl` (download). The file must be self-contained (no server, no CDN) and must not leak API keys.

`shz review apply corrections.jsonl`:
- **Markup contract:** the editable field contains `fa_markup`; sentinels are rendered as non-editable chips (a toggle shows the raw string). A correction is rejected if any existing sentinel id is missing or duplicated (L02), or if unknown ids appear; the report warns before download. Humans may reorder sentences but may not invent inline markup.
- Inserts a new `translations` version with `pass="human"`, `model="human"`, flips `is_current`, transitions segment to `done` unless lint errors remain, resolves the flags marked accepted.
- Re-runs linters; prints a summary; suggests `shz export`.

### 6.10 Staleness and targeted redo

- Glossary approval after translation starts: every translation stores `glossary_hash` (hash of the approved glossary at write time) and `glossary_terms_json` (the EN terms injected into its window). `shz qa --stale-glossary` marks versions whose hash differs and lists the changed terms; `shz retranslate --stale-only` re-translates exactly the windows whose `glossary_terms_json` intersects those terms (falling back to the whole chapter when a term has no recorded window).
- `shz retranslate --segments c003.s0012,c003.s0013` (or `--chapter 3 --window 4`) creates new draft versions for exactly those segments (using current context pack), preserving history.
- `shz export` refuses if any body segment is not `done` or has unresolved `error` issues, unless `--allow-incomplete` (then the EPUB gets a generated "TRANSLATION INCOMPLETE" note page and the run is marked experimental).

---

## 7. QA: deterministic linters and Persian correctness

### 7.1 Principles

- Every check has a **stable code** (`L01`…`L20`) used in the DB, `flags.csv`, the review UI and tests. Never renumber; deprecate instead.
- Checks are pure functions with **explicit scope** (no I/O, no LLM):
  `check_segment(seg, tr, glossary) -> list[Issue]`,
  `check_window(window, trs, glossary) -> list[Issue]`,
  `check_chapter(chapter, segs, trs) -> list[Issue]`,
  `check_book(book) -> list[Issue]`.
  Scope assignment: **segment** L01, L02, L04–L08, L10, L11, L13, L14, L16, L17, L20; **window** L03, L18 (L03 needs the window's source + translations because a term may span segments); **chapter** L15, L19; **book** L09 (duplicate sentences across the book), L12 (variant inventory). A linter that needs cross-segment data but is written as `check_segment` is a bug.
- **Auto-fix only what is mechanically certain**: digit script, ASCII→Persian punctuation in Persian-context runs, whitespace/ZWNJ-run cleanup, sentinel stripping. Everything semantic is flagged, never rewritten.
- Every check runs after every translation pass and again before export. `shz qa` is idempotent: running it twice produces no duplicate issues (dedupe on `(stage, code, dedupe_key)`, where `dedupe_key` carries the segment/chapter scope and a hash of the quoted evidence).
- Severity policy: `error` blocks `done`/export; `warn` flags for review; `info` is advisory and does not affect risk score.

### 7.2 Check catalogue

| Code | Check | Default severity | Logic (deterministic) | Auto-fix |
|---|---|---|---|---|
| L01 | Coverage | error | every in-scope segment has `is_current` translation, non-empty, ≥ 1 Persian letter | — |
| L02 | Markup integrity | error | sentinel ids in `fa_markup` exactly equal node ids; balanced pairs; atomic nodes present; reconstructed XHTML parses; no stray `⟨` | — |
| L03 | Glossary presence | error | for each approved term with a match in window source, approved FA form (or alias, ZWNJ/space-insensitive) appears; count mismatch | — |
| L04 | Script purity | error (`ة` = warn) | banned code points `U+064A, U+0643, U+0649, U+0640, U+FB50–FDFF, U+FE70–FEFF`, Arabic-Indic digits `U+0660–0669`, Greek/Cyrillic/CJK letters, Arabic-only stopwords `هذا الذي التي …`, `NFKC` first; `ة` U+0629 is a **warn** (it may be legitimate in Arabic loans and needs a lexical decision) | normalize maps |
| L05 | Digits & numerals | error | policy `persian` ⇒ ASCII digits in Persian runs converted; numbers in source appear with equal numeric value in target (parse both sides); date/measurement policy from brief | digit map |
| L06 | Punctuation | warn | ASCII `, ; ?` in Persian-context runs → `، ؛ ؟`; `...` → `…`; guillemets balanced `«»`; no `‎`/`‏` controls; no double spaces; no space before Persian punctuation | yes (high confidence) |
| L07 | ZWNJ / half-space | warn | `می‌`+space before verb stem from shipped list; `ها`/`های` separated by space; `تر`/`ترین` separated; ZWNJ at word edges; ZWNJ doubled | yes, only for whitelisted patterns |
| L08 | Length & sentence drift | warn; error outside hard band | FA/EN **word** ratio outside `[0.75, 1.30]` ⇒ warn; outside `[0.55, 1.70]` ⇒ error; sentence-count ratio outside `[0.60, 1.80]` ⇒ warn (Persian legitimately re-segments; the Persian literary study measured 452w/6s → 636–699w/15–18s in one direction, so this is a real signal) | — |
| L09 | Repetition | warn | any 20+ char substring repeated ≥ 3× in a segment; identical consecutive segments; same sentence appearing in > 2 places | — |
| L10 | Residual source language | error | Latin-script runs of ≥ 4 words outside a whitelist (`data/protected_latin.txt`: ISBN, URLs, brand names, epigraph attributions) ⇒ untranslated block | — |
| L11 | Entity / number carry-over | warn | capitalized source tokens in the segment: every proper noun with an approved glossary entry must have its FA form present; numbers/units compared (L05) | — |
| L12 | Name variants | warn | normalized Persian name tokens fuzzy-matched (rapidfuzz ≥ 88) against glossary entries; any variant not approved is drift; recurring proper noun (≥ 3 uses) with no glossary entry | — |
| L13 | Refusal / content filter | critical | output contains refusal patterns (`I can't`, `as an AI`, `متأسفم`, empty output, `finish_reason=content_filter`) | — |
| L14 | Translationese & register | warn | blacklist (`data/translationese_fa.txt`): `می‌باشد`, `می باشد`, `در حالی که`, `به عنوان یک`, `لازم به ذکر است`, `شایان ذکر است`, `مورد ... قرار گرفت`, `به عمل آمد`, `انجام گردید`; density thresholds: `می‌باشد` > 0 / 1,000 words, passive `گردید` > 2 / 1,000, `که` > 60 / 1,000, sentence-initial `و` > 30%, `را` < 2 / 1,000 or > 40 / 1,000; postposed English-style dialogue tags (`«...» X گفت`); function-word ratio (from shipped stopword list) for language ID | — |
| L15 | Chapter style drift | warn | chapter metrics (mean sentence length, dialogue ratio, unique-word ratio, paragraph length) vs book running baseline; `|z| > 2.5` on ≥ 2 metrics | — |
| L16 | Meta-commentary / addition | critical | regex for `ترجمه:`, `(مترجم)`, `Here is`, `Note:`, `در اینجا`, bracketed translator explanations not present in source; count of added footnote markers vs source; output longer than 1.8× source words | — |
| L17 | Verse line structure | warn | `kind=verse`: line count within ±1 of source, no merged lines | — |
| L18 | Paragraph/block structure | error | segment count and order preserved in the chapter; no stray blank-line duplication; no segment text leaked into a neighbour | — |
| L19 | Footnote/endnote integrity | error | every `noteref` id has a matching translated note; every note is referenced; no orphan markers | — |
| L20 | Heading/caption sanity | warn | headings not punctuated as sentences unless source is; captions present; table cells non-empty | — |

`L03 + L11 + L12` are the consistency triad; `L08 + L14 + L15` are the literary-quality triad; `L02 + L10 + L16 + L19` are the loss/addition triad. These groupings drive `shz qa --group` output.

### 7.3 Persian normalization (applied to every stored translation)

`persian/normalize.py` implements this pipeline, in order, and must be idempotent:

```
N01  NFC → NFKC (folds presentation forms & ligatures, U+FB50–FDFF/U+FE70–FEFF)
N02  explicit character map: U+064A→ی, U+0649→ی, U+0643→ک, U+0640→'', U+200B→'',
     U+200E/U+200F→'', U+00A0→' '
N03  digits: 0-9 and ٠-٩ → ۰-۹ (per config `persian.digits`; `ascii` mode converts the other way)
N04  remove harakat U+064B–U+0655 (keep if `persian.keep_diacritics` or segment kind = verse)
N05  collapse ZWNJ runs; remove ZWNJ adjacent to whitespace / at token edges
N06  typographic quotes: paired `"..."` → «...»; `...` → `…`; ASCII `, ; ?` → Persian
     ONLY when preceded by an Arabic-script letter (preserves English quotations)
N07  `%` → `٪` in Persian runs; decimal `.` → `٫` and thousands `,` → `٬` ONLY when the
     number is inside a Persian sentence and the brief says so (off by default; risky)
N08  whitespace: \r\n→\n, rstrip lines, collapse 3+ newlines to 2, collapse 2+ spaces
N09  NFC finish
```

Hard rules (tested in CI):
- **Never reverse or mirror text.** Guillemets and parentheses are bidi-mirrored; store logical order.
- **Never insert U+0640 (tatweel)** to fake kashida; `hyphens: none`, `letter-spacing: normal` in CSS.
- **`ة` (U+0629) is not auto-replaced** (`نتیجة`→`نتیجه` is a lexical decision). Flag as `L04` **warn**; the model/editor fixes it.
- **ZWNJ is never inserted during normalization.** Insertion is a separate, high-precision repair (`L07`), only for patterns whose joined form is in `data/zwnj_forms.txt` (shipped list of frequent `می‌…`, `…‌ها`, `…‌تر` forms, grown from the book's own text during M2; ~200 entries is enough to start).
- **Do not use `hazm.Normalizer()` defaults anywhere in the pipeline.** It rewrites guillemets, forces digits, and inserts ZWNJ by dictionary guess — lossy for literary prose. `hazm` may be added as a **dev-only** cross-check dependency; it must not be a runtime import. [Research: `research/persian-engineering.md` §1]

### 7.4 Register and translationese scoring

Beyond the blacklist, compute per chapter and per book:
- `mibashad_per_1k` (`می‌باشد` + `می باشد`), `passive_gardid_per_1k`, `ra_per_1k` (`را`), `ke_per_1k`, `sentence_initial_va_ratio`, `mean_sentence_words_fa`, `dialogue_char_ratio`, `unique_word_ratio`, `function_word_ratio`.
- Target bands come from the book's own first approved chapter (the anchor), not from a generic corpus — literary Persian varies legitimately. Flag chapters that deviate; never rewrite automatically.
- Arabic-leakage score: `arabic_only_stopwords_per_10k` must be 0; any hit is `L04` error.

### 7.5 Chapter- and book-level checks

- Chapter: coverage, L15 drift, footnote integrity, glossary adherence %, count of `critical` issues.
- Book: name-variant inventory, address-form consistency (تو/شما per character pair, from ledger), glossary drift table, chapter metric summary, list of segments with risk ≥ threshold, duplicate-sentence report.

### 7.6 `review/flags.csv` columns

`segment_id, chapter, risk_score, codes, severities, source_excerpt, fa_excerpt, sample_flag, reviewed, reviewer_note`
`sample_flag=true` marks the random 5% low-risk sample. `reviewed` is filled by `shz review apply`.

---

## 8. Evaluation harness and quality measurement

### 8.1 What we deliberately do **not** do

- **No BLEU/COMET/chrF gating, ever.** Reference metrics reward surface overlap with one reference; literary translation has many valid renderings and diversity is a virtue. Evidence: on LITEVAL-CORPUS (>2,000 translations, 4 language pairs, €4.5k of annotation) automatic metrics identified human translations in **at most 20%** of cases; MQM with student annotators misjudged ~60% of them as indistinguishable or worse; best-worst scaling identified them **80–100%** of the time. ([LITEVAL-CORPUS](https://arxiv.org/abs/2410.18697))
- **No "percent accuracy" headline.** Ailaysa's arithmetic is the right frame: 99% accuracy in a 300-page book is still ~900 wrong words. Track **errors per 1,000 words**, by MQM category. ([Publishers Weekly](https://www.publishersweekly.com/pw/by-topic/digital/content-and-e-books/article/98849-ai-translation-race-accelerates.html))
- **No ship decision based on an LLM judge alone.** Judge scores are for regression detection and A/B ranking. When a human reviewer is available, their read is the final signal; without one, the report must say the quality is judge-screened, not human-verified.

### 8.2 Three-track evaluation

| Track | What | When | Cost |
|---|---|---|---|
| **T1 Mechanical** | linters L01–L20, coverage, adherence, risk scores, cost/time | every run, CI | free |
| **T2 Judge (LLM)** | reference-free, error-span output in MQM categories + rubric scores | every prompt/model change on the golden set; sampled on full books | cents |
| **T3 Human** | best-worst scaling (BWS) preferences on 250–300 gold paragraphs + professional MQM on 3 sample chapters | before v1 release, before changing default model | translator time |

### 8.3 Golden sets (`tests/golden/` and `eval/`)

All English sources public-domain and internal-only (see 9.9). All Persian references authored by professional translators, licensed for internal eval use.

| Set | Size | Content | Purpose |
|---|---|---|---|
| `mech/` | 30 short blocks | markup, numbers, names, punctuation traps | unit fixtures for lint rules |
| `idioms/` | 40 sentences | English idioms/metaphors + wordplay | T2/T3 idiom handling |
| `voice/` | 20 passages | 3 registers (lyrical, stark, comic), 4 POV setups | style fidelity |
| `dialogue/` | 20 exchanges | interruptions, dialect, address forms | register and تو/شما |
| `culture/` | 15 passages | religious, historical, food, measurement references | adaptation and footnote policy |
| `chapter/` | 3 full chapters (~6–8k words) | end-to-end coherence, continuity | full-loop acceptance |
| `bench/` | 60 paragraphs (held out) | stratified from the above | model bake-off |

Protocol for T3: for each candidate, show two translations of the same paragraph in randomized order; ask "which better recreates the author's voice/meaning for a Persian reader?" (BWS: best, worst, ties allowed). 250 paragraphs × 3 evaluators ≈ 750 judgments per compared system. Report win-rate + bootstrap CI; headline accuracy is **errors/1,000 words** from MQM on 3 chapters, not the win-rate.

### 8.4 T2 judge specification (A12)

- Input: source segment, candidate translation, brief card, glossary subset; **no reference** (references bias toward literalness, and they exist for only part of the book).
- Output (`JudgeVerdict`): `fidelity`, `naturalness`, `style_fidelity`, `terminology`, `cultural_adaptation` (each 1–5 — exactly these five keys, no `overall`), plus `errors[]` with `type` (MQM: omission, addition, mistranslation, untranslated, fluency, terminology, locale, style), `severity` (minor/major/critical), `quote_source`, `quote_target`, `comment`; `verdict ∈ pass, minor_issues, major_issues, reject`.
- 3 samples with randomized source/candidate order, temperature 0.2 (sampling needs variance to be meaningful — three temperature-0 samples are identical), take the median per dimension and the union of errors; never show which model produced the output.
- Calibration: judge must reproduce the ordering of the 20 human-rated calibration paragraphs in `eval/calibration/` (Kendall τ ≥ 0.6) before it is used for decisions. Re-calibrate when the judge model changes.
- The judge is `deepseek-v4-pro` — a different **model** from the translator, though the same provider; with a single-provider setup true cross-family judging is impossible. Consequences: judge scores are used **only for relative comparison** (A/B, regression, routing), never as an absolute quality claim, and human/BWS review remains the gate when an editor is available. If the user later configures a second provider, `eval.judge_model` should move to it. `shz eval` errors when `eval.judge_model` is unset.

### 8.5 Model bake-off (M10, `eval/bakeoff.py`)

Procedure: pick 2–4 candidate models; run identical configs on `bench/` (60 paragraphs) + `chapter/` (1 chapter) with 3 seeds; collect T1 metrics, T2 judge scores, cost, latency, refusal rate; produce `eval/reports/<date>-bakeoff.md` with a table and a **recommendation**, plus a documented decision in `DECISIONS.md`:
- quality tier: highest judge `style_fidelity` + `naturalness` with no fidelity regression vs the cheapest acceptable model;
- balanced tier: best quality per dollar within 10% of the quality-tier score;
- cheap tier: used only for summaries/entities, never for user-visible prose.
Known constraints to record in the report: model availability in the user's jurisdiction, data-retention terms, context window, max output tokens (a 4,096-token cap is too small: a production report documents `finish_reason=length` at 44/60 paragraphs at 4,096; use ≥ 8,192, prefer 16,384), and known Persian weaknesses.

### 8.6 Benchmarks to borrow (sanity only, never gates)

- `FLORES-200` `pes_Arab` for a coarse regression signal on general text.
- `MIZAN` for EN↔FA sanity on news/formal prose.
- Persian idiom sets ([arXiv 2412.09993](https://arxiv.org/abs/2412.09993)) as an external check of idiom handling; Claude-3.5-Sonnet was strongest in both directions in that study.
- `MELAC` / `FarsEval` for *model selection* (Persian cultural/grammar knowledge), not translation quality.
- **There is no EN→FA literary MT benchmark** (verified 2026-11): the in-house gold set is a genuine contribution and must be maintained carefully.

### 8.7 Regression policy

- `pytest -m online` (real API, golden set, small) runs manually/nightly, never in the default CI.
- Any change to prompts (`p01`–`p13`), model profile, or pipeline semantics requires a `shz eval judge --golden bench` run and a before/after table in the PR description.
- Accept a change only if: no T1 regression, judge `fidelity` Δ ≥ −0.1, and at least one of `style_fidelity`/`naturalness` improves by ≥ 0.1. Human BWS is required for changes that aim to improve style.

---

## 9. Reliability, cost, security

### 9.1 Provider abstraction

```python
class Provider(Protocol):
    def complete(self, *, messages: list[Msg], model: str, temperature: float,
                 max_output_tokens: int, response_schema: dict | None,
                 seed: int | None = None) -> LLMResult: ...
# LLMResult: text, parsed (dict|None), tokens_in, tokens_out, cached_tokens,
#            cost_usd, latency_ms, finish_reason, provider_request_id
```
`litellm_provider.py` is the only module importing `litellm`; `mock_provider.py` replays JSON fixture files and can inject failure modes (`invalid_json`, `truncated`, `missing_segment`, `rate_limit`, `content_filter`, `markup_broken`). Unit tests import neither network nor `litellm`.

### 9.2 Structured output ladder (applied by `llm/schema.py`)

1. Native JSON-schema / strict structured output if the model supports it.
2. `json_object` mode + schema in the prompt.
3. Extract the first balanced JSON object from prose.
4. Pydantic validation; on error, one repair call: "your output failed validation with: …; return only corrected JSON".
5. On second failure, fall back to **per-segment single calls** (cheap to lose, easy to retry).
6. On third failure, mark segment `failed` with the raw response stored in `issues.context_json` for debugging. Never fabricate a translation.

### 9.3 Caching and idempotency

- Cache key = `sha256(model | temperature | response_schema_hash | prompt_hash | messages_json)`; stored in `cache` with `value_json`, `created_at`, `last_hit_at`; hit increments counters in `llm_calls` with `cached=true` and `cost_usd=0`.
- `--no-cache` bypasses reads (still writes); `--refresh` deletes entries for the affected segments only.
- Prompt hash = content hash of the rendered template + version from front-matter. Editing a prompt invalidates only the calls that used it.
- Window plan hash stored in `runs`; a changed plan invalidates downstream segment versions only where segment ids changed.
- **Orphan recovery:** on startup, any `runs` row still `running` whose `stats_json.heartbeat_at` is older than `2 × timeout_s` is marked `aborted`; `shz translate` then resumes from segment statuses. A run is never silently resurrected as `running`.

### 9.4 Concurrency, retries, timeouts

- `asyncio` + semaphore `max_inflight` (default 4; configurable). Chapter parallelism opt-in.
- Retry classification: transient = 429, 5xx, timeout, connection reset, `finish_reason=length` (re-request with split window, not a plain retry); permanent = 400, auth, content filter, context-length exceeded (split), schema failures (ladder above).
- Backoff: `min(2**attempt, 60) + jitter(0..1s)`, max 4 retries (5 attempts total; config `provider.max_retries: 4`), honoring `Retry-After` when present.
- Per-call timeout (default 180 s) and per-run watchdog (default 12 h) with a graceful checkpoint.
- `finish_reason=length` on a translation is always treated as "output truncated": split the window at segment boundaries and retry; never persist a truncated segment.

### 9.5 Budget, cost modes, and tariffs

- **Default ceiling: `budget.max_usd: 10`** per book (the product owner's constraint). `--max-usd` overrides it per run.
- `shz estimate` computes expected cost from the window plan and calibrated token ratios, prints the three cost modes at **both tariffs**, and writes `runs/<id>/estimate.json`. `--dry-run` makes zero calls.
- **Cost modes** (`pipeline.cost_mode: auto|economy|standard|quality`, default `auto`): `auto` picks `standard` if the estimate fits the cap, downgrades to `economy` if it does not, and never silently upgrades. Mode definitions and prices are in Section 3.6. `auto` prints the chosen mode and the reason.
- **Tariff awareness, no waiting:** DeepSeek prices double during peak (01:00–04:00 and 06:00–10:00 UTC, Mon–Fri). `shz estimate` shows peak and off-peak, and the run report states which tariff applied and the cost. The tool **always starts immediately** when invoked — scheduling is the owner's job; there is no `--prefer-off-peak` wait mode in v1.
- **Cache accounting:** every call records `cached` tokens and prices them at the cache-hit rate; the report shows cache-hit ratio and the dollars saved. A cache-hit ratio below 50% on translation is a bug in prefix stability (Section 6.4).
- Enforcement: before each call, if `spent + worst_case_call_cost > cap`, stop scheduling, persist state, mark run `paused`, exit code 3 with a resume command. `--allow-overrun` exists but is loud.
- Every call's cost comes from `litellm.completion_cost` when available, else `configs/models/*.yaml` `pricing_usd_per_mtok` (which distinguishes cache-hit, cache-miss and output); unknown pricing ⇒ estimate from tokens and warn once.
- `runs/<id>/report.md` shows cost by agent, cache hit rate, tokens in/out, tariff, and cost per 1,000 source words.

### 9.6 Token budgeting with Persian fertility

- Persian is tokenizer-hostile: expect **≈1.5–2.5×** the tokens of the equivalent English for cl100k-class tokenizers (measure; ratio is tokenizer-specific, `UNVERIFIED` in general). [Research: `research/persian-engineering.md` §5.3]
- Therefore: **never size a translation request by English tokens alone.** `tokens.py` keeps two calibrations, `en_tokens_per_char` and `fa_tokens_per_char`, initialized conservatively (en 0.25, fa 0.45) and **recalibrated from actual provider usage after every 50 calls** (`usage.tokens_out / chars_out`, rolling median, stored in `runs`/`book.db`).
- Window budget is defined on the **source** (≤ 900 tokens) *and* the **expected output** (`estimated_fa_tokens ≤ 2.2 × source_tokens`, and ≤ `model.max_output_tokens − 512`). If both cannot hold, reduce the window to 4–6 paragraphs.
- `max_output_tokens` per call: `clamp(ceil(source_tokens × fa_ratio × 1.3), 512, model_max)`. **Pre-flight:** if the projected output exceeds `model_max_output_tokens − 512`, split the window *before* calling; a response with `finish_reason=length` is never accepted, only split-and-retried.
- **Thinking mode: off by default, measured rather than assumed.** DeepSeek bills reasoning tokens as output and ignores `temperature` in thinking mode. The evidence is genuinely mixed: chain-of-thought improves context-aware translation (pronominal anaphora, lexical cohesion) and is reliably useful for **post-editing/self-correction**, while test-time scaling on general-purpose models gives limited, plateauing gains for direct translation and **degrades quality when reasoning is forced past its natural stop**; a long-CoT literary system runs ~12× slower. Our pipeline is already a self-correction architecture, so the plausible win is on the critics/reviser, not the draft. Therefore: draft off; critics/reviser off in `standard`; M10 tests `thinking=low` on critics+reviser (separately on the draft) and adopts it only if judge/BWS improves with cost inside the cap. When enabled for an agent, `max_output_tokens` must add a `reasoning_allowance` (default 2,048) and reasoning tokens are tracked separately from the `fa_ratio` calibration — otherwise the pre-flight split logic truncates JSON.
- DeepSeek's 1M context and 384K output are not a reason to enlarge windows: small windows keep the cached prefix hot, keep per-window cost predictable, and keep review granular. The caps above still govern.

### 9.7 Observability

- Console: `rich` progress with chapter/window, cost so far, retries, cache hit %.
- `runs/<id>/calls.jsonl`: one JSON object per call, mirroring the `llm_calls` columns plus `retries`: `created_at, run_id, agent, model, prompt_hash, input_hash, cached, tokens_in, tokens_out, cost_usd, latency_ms, retries, finish_reason, error`. No prompt content in logs by default (`--log-prompts` stores under `runs/<id>/prompts/` for debugging, gitignored).
- `shz doctor` reports: python version, sqlite version, disk space, config validity, model alias resolution + ping, `thinking` setting, tokenizer calibration, epubcheck availability, LibreOffice availability (PDF export), font assets present, provenance recorded (informational, no gate), API key presence (never values), cache size.

**Exit codes** (stable contract, scripts depend on it): `0` success; `1` runtime failure; `2` usage/config error; `3` paused by budget or interrupt (state saved; resume with the command printed to stderr); `4` finished with unresolved `error` issues or coverage < 100% (report path printed). `shz translate` returns `4`, not `1`, for a partially failed book so a pipeline can distinguish "bad configuration" from "some segments need a human".

### 9.8 Prompt injection and untrusted content

The book text is untrusted input. Defenses:
- Source is always inside `<source_text segment="c003.s0042">…</source_text>` and the system prompt says content inside is data, never instructions.
- The output schema is closed (`additionalProperties: false`); unexpected fields are rejected.
- Post-validation: output must not contain instruction-following artifacts (`system:`, `assistant:`, tool-call syntax, URLs not present in source), must be Persian, must map to requested segment ids.
- Never execute or fetch anything from model output; footnote text is rendered as escaped text only.
- A paragraph that attempts to instruct the model is still translated (it is literature); the schema validation is what prevents control-flow hijack.

### 9.9 Content policy, privacy, legal

- Translate faithfully; do not soften, censor or add content. If a provider's filter blocks a segment, try the configured fallback model once, then mark `failed` and flag for a human — never silently omit.
- Default: no book text leaves the machine except to the configured provider; `--local-only` restricts to a local OpenAI-compatible endpoint (`api_base`), and `shz doctor` warns when a cloud provider is selected for a book marked `rights_basis: licensed` without `--allow-cloud`.
- API keys only from env/`.env` (gitignored); never printed, never logged, never written into run manifests (store a hash/fingerprint instead).
- `books.rights_basis` defaults to `user_supplied` and is **informational only**: it is recorded in the EPUB colophon and run manifest for provenance, and it never blocks a step. The README states that publishing a translation of an in-copyright book needs the rightsholder's permission; the tool assumes private use and does not clear rights.
- Test fixtures from Project Gutenberg are internal-only (mirrors, offline cache, no redistribution); strip PG headers if a text ever ships.

---

## 10. Milestones (build in this order)

Estimates assume one competent developer; Kilo Code should aim for roughly the same order of magnitude and stop at each acceptance gate. **Treat them as planning units and lower bounds:** an independent review put M1/M8/M10 at 2–4× these numbers and the M0–M11 total at roughly 20–25 focused days for a first implementation. Cut M8b, A13, and verse polish before cutting the ingest round-trip, the glossary verification loop, the critics, or the EPUB writer. **Every milestone ends with `ruff check`, `mypy`, and `pytest -q` green.**

### M0 — Skeleton, config, provider layer, accounting (~0.5 day)
Deliverables: `pyproject.toml`, package tree, `config.py` (defaults + YAML merge + env overrides), `db.py` + `schema.sql` (all tables, `PRAGMA` settings, migration hook), `ids.py`, `tokens.py`, `cost.py`, `errors.py`, `logging.py`, `provider.py` + `mock_provider.py` + `litellm_provider.py`, `promptlib.py`, CLI shell with `shz doctor` and `shz estimate`, `configs/default.yaml` fully documented.
Accept: `uv run shz doctor` exits 0 without keys (warns, does not fail); `uv run pytest -q` green; `ruff`/`mypy` clean; `MockProvider` returns a scripted response and `cost.py` records it; `shz estimate` prints the token table plus the three cost modes at peak and off-peak for a fixture.

### M1 — Ingest and segmentation (~3 days)
Deliverables: format detection, **EPUB / PDF / DOCX / TXT readers**, block classifier, inline-node extraction, chapterization + `structure.yaml`, source normalization, segment creation, `books.import_quality`, `shz init`, `shz inspect`, and **`tests/fixtures/build.py`** — a generator that writes synthetic EPUB, DOCX, TXT and a 3-page text-layer PDF (headings, emphasis, links, footnote refs, a table, verse, an image; PDF via dev-only `reportlab`) so no third-party text is shipped.
Accept: `shz init tests/fixtures/generated/book.epub` creates the workspace; `shz inspect` reports chapters/segments/tokens/markup nodes and PDF import quality; **round-trip test**: reconstruct HTML from nodes and compare to the normalized original for EPUB, DOCX and TXT (byte-equal); the generated PDF imports with `import_quality ≥ 0.7` and paragraph text matching the DOCX fixture within a 5% normalized edit distance; an image-only PDF fails with `PDF_NO_TEXT_LAYER`; re-init is idempotent (same ids/hashes); segment counts match paragraph counts.

### M2 — Persian module (~1 day)
Deliverables: `normalize.py` (N01–N09), `lint.py` (L01–L20 skeleton with L04–L10, L14 implemented), `punctuation.py`, `numerals.py`, `markup.py`, data files (`translationese_fa.txt`, `zwnj_forms.txt`, `protected_latin.txt`, stopwords).
Accept: property test `normalize_fa(normalize_fa(x)) == normalize_fa(x)` on a 10k-char corpus; banned code points removed; a 50-case golden table of input→output pairs passes; each lint code has a positive and a negative fixture; no runtime import of `hazm`.

### M3 — Analysis, optional brief and glossary (~1.5 days)
Deliverables: sample builder, `BriefAnalyst` (**auto-approved by default**; `--review-brief` enables the manual gate), `ChapterSummarizer`, `EntityExtractor` (+ deterministic capitalized-token scan), `GlossaryProposer`, `shz analyze`, `shz glossary *`, and the three glossary modes `off | provided | auto`.
Accept: on a 2-chapter fixture the brief draft validates and is frozen automatically with `brief.approval: auto`; one summary per chapter; ≥ 20 ranked entity candidates in `auto` mode; `--glossary terms.yaml` validates and enables L03; the default `off` mode translates with **no glossary and no approval step**; `--review-brief` blocks until approved.

### M4 — Translation core, one chapter (~2 days)
Deliverables: window planner, context packer, `Translator`, schema validation, cache, translation versioning, linters wired, risk score, `shz translate --chapters 1`, `shz qa`.
Accept: chapter 1 translates end-to-end with the mock provider in CI, plus one manually documented smoke run against a real provider (result and cost recorded in `DECISIONS.md`); **the zero-config path works** — `shz init fixture.epub && shz translate --chapters 1` with no brief approval, no glossary, and no anchors reaches `done`; coverage 100%; all sentinels intact; `shz qa` writes flags; re-running with no changes creates **zero** new versions and reports ~100% cache hits; a deliberately broken mock (`markup_broken`) is caught by L02 and retried.

### M4.5 — Gate 2: anchor calibration (~0.5 day)
Deliverables: `shz translate --calibrate`, `shz anchors propose/approve/reject/list`, `anchors.draft.yaml`, anchor inclusion in the context pack, and the refusal rule (chapters 2+ require `anchors.yaml` unless `--no-anchors`).
Accept: after chapter 1 is `done` and corrections are applied, `--calibrate` writes up to 3 candidates from deterministic selection; approving them writes `anchors.yaml` with a hash recorded in the DB; a run with anchors includes them in every window's pack (asserted from the rendered prompt in a unit test); a run **without** anchors continues with one warning, and `--require-anchors` turns that into an error.

### M5 — Critics, reviser, repair, budget (~1.5 days)
Deliverables: `FidelityCritic`, `LiteraryEditor`, `Reviser`, issue dedupe/severity, repair ladder, revision-regression guard, budget guard, `--dry-run`.
Accept: with a mock draft missing a clause, A6 produces an omission issue and A8 fixes it; a bad revision is rejected by the regression guard (`revision_regressed` recorded); budget cap pauses the run with exit code 3 and a resume hint; `--dry-run` makes zero API calls.

### M6 — Whole-book orchestration (~1.5 days)
Deliverables: resume/continue, `--parallel-chapters`, `LedgerCurator`, chapter-close bookkeeping, per-item failure handling, run manifest/report, staleness detection.
Accept: kill -9 mid-book, restart → resumes with no duplicate paid calls and completes; parallel mode produces all chapters and prints the coherence trade-off warning; one deliberately failing segment does not abort the run; `runs/<id>/report.md` exists with cost table.

### M7 — Review workflow (~1 day)
Deliverables: `report.html` generator (self-contained, RTL, filters, edit + download), `corrections.jsonl` schema, `shz review apply`, flags CSV.
Accept: report opens offline in a browser, filters by code/chapter/risk, exports corrections; applying corrections creates `pass=human` versions, flips `is_current`, resolves flags, re-runs lint; invalid correction (missing segment, broken sentinel) is rejected with an actionable error.

### M8a — EPUB 3 + Markdown export (~2 days)
Deliverables: EPUB 3 writer (zipfile + lxml + templates + Vazirmatn WOFF2 + `OFL.txt`), OPF/nav/XHTML, CSS, internal validator, determinism, `--generated-at`; Markdown writer (front-matter, chapter headings, endnotes) and plain text.
Accept: `shz export --formats epub,md,txt` produces all three; the EPUB has `mimetype` first and stored; `epubcheck` reports **0 errors** (if Java present; otherwise internal checks pass); spine `page-progression-direction="rtl"`, `dir/xml:lang` on every XHTML, CSS direction rtl, a11y metadata present; bidi test strings render correctly in Thorium/Calibre (manual, documented); two exports of an unchanged book are byte-identical; the Markdown round-trips headings/verse/endnotes and opens cleanly in pandoc.

### M8b — DOCX, PDF conversion, optional bilingual (~1.5 days)
Deliverables: DOCX writer (RTL, endnotes) as the primary editable artifact, `shz export --pdf` via LibreOffice (graceful message when absent), `--bilingual` HTML side product, `--formats` selection.
Accept: DOCX opens in Word/LibreOffice with RTL paragraphs and endnotes; PDF conversion produces a non-empty `%PDF` file when LibreOffice exists and prints the manual command when it does not, with the "not print-ready" caveat in the report; bilingual HTML is self-contained and correctly bidi-isolated; `shz export` refuses an incomplete book unless `--allow-incomplete`; bilingual output is absent by default.

### M9 — Audit and read-through (~1.5 days)
Deliverables: `ConsistencyAuditor`, `ReadThroughEditor`, glossary/name audit, repair tasks, `shz audit`, aggregate report.
Accept: a planted inconsistency (same character rendered two ways in chapters 1 and 3) is found; the read-through emits ≤ 5 issues/chapter and no automatic rewrites of `critical`-free prose; repairs create new versions and are listed in the report.

### M10 — Evaluation harness and bake-off (~2 days)
Deliverables: golden-set loader, `QualityJudge`, `blp.py`, `mqm.py` (errors/1,000 words), `metrics.py`, `bakeoff.py`, calibration check, reports; `eval/calibration/reader/` (20 paragraphs) + `eval/calibration/ratings.json`. The ratings are **supplied by the release owner** (a professional Persian reader); the implementer builds the harness, the loader and the validation, and `shz eval calibrate` errors with instructions when `ratings.json` is missing rather than fabricating ratings.

**Thinking-mode experiment (mandatory, because "off" is a default, not a proof).** Four arms on the same `bench/` (60 paragraphs) + one chapter: (1) baseline, thinking off; (2) `thinking=low` on critics+reviser; (3) `thinking=low` on the draft; (4) `quality` mode (v4-pro critics). Record judge dimensions, latency, cost and `finish_reason` failures. **Adoption rule:** low-effort thinking may become an option for `quality` mode only if arm 2 or 3 improves human BWS/judge `style_fidelity` with no `fidelity` regression and stays inside the $10 cap at ≤ 2× wall-clock. It never becomes the `standard` default on the strength of one book.
Accept: `shz eval judge --golden tests/golden/bench` produces a scores table and error spans; the judge reproduces the calibration ordering at τ ≥ 0.6 (or the harness fails loudly); bake-off runs on 2 mock models and writes `eval/reports/<date>.md`; regression policy documented and enforced by a make target. **Human MQM/BWS is the release owner's acceptance activity**, not something the implementer or CI can run — the harness prepares the packets (`eval/packets/*.csv`) and the report template; the plan does not pretend CI can measure literary quality.

### M11 — Polish and release (~1 day)
Deliverables: README tutorial (install → init → analyze → approve → translate → review → export), `shz doctor` complete, example configs, CI (lint/type/unit; online tests opt-in), `CHANGELOG`, `v1.0.0` tag, end-to-end run on a public-domain novella with a real provider (documented, not in CI).
Accept: fresh venv install works; the README tutorial produces a valid EPUB + DOCX + Markdown from a text-layer PDF and from an EPUB; CI green; all docs accurate against the code.

### M12+ (optional, not v1)
Local web UI for review; DelTA-style multi-tier memory if the ledger proves insufficient; translation memory reuse across books; glossary import from existing publisher TMs; PDF via XePersian.

---

## 11. Risks and mitigations

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | Literary quality plateaus below publishable | Med | High | Preparation artifacts, dual critique, read-through pass, optional human checkpoints; set expectations as "editor-grade draft" not "final" |
| R2 | Omissions/hallucinated additions slip through | Med | High | Per-segment JSON ids, L01/L08/L16/L19, fidelity critic, optional back-translation diagnostic (post-v1) |
| R3 | Glossary drift across a book | High | Med | Three-layer enforcement (prompt, L03/L12, targeted repair), human approval, name-variant inventory |
| R4 | Cost overrun on long books | Med | Med | `shz estimate` before spending, calibrated token accounting, budget cap, cache, tiered models |
| R5 | Persian tokenization blows the context/output budget | High | Med | Target-side budget, calibration from usage, split windows on `finish_reason=length` |
| R6 | Model deprecation / provider outage | High | Med | Model aliases in YAML, fallbacks, bake-off re-run, cache makes re-runs cheap |
| R7 | EPUB/RTL compatibility bugs | Med | Med | epubcheck in CI, bidi test strings, Thorium/Calibre manual matrix, no font obfuscation |
| R8 | Human review becomes the bottleneck | Med | High | Flag only high-risk + 5% sample, side-by-side report, corrections import, keyboard-first UI |
| R9 | Copyright misuse | Low | Med | Private-use assumption; `rights_basis` recorded in colophon/manifest; README legal note; no redistribution built in |
| R10 | Prompt-only style control fails (documented in research) | High | Med | Few-shot anchors, style metrics, read-through pass — never rely on adjectives in the prompt |
| R11 | Judge is unreliable or self-preferential | High | Med | Judge is `deepseek-v4-pro` (same provider, different model); scores used only for relative comparison; human BWS when available; calibration set |
| R12 | Scope creep (web UI, other languages, OCR, print PDF) | High | Med | Explicit non-goals; M12 backlog |
| R13 | PDF extraction quality (columns, headers, hyphenation) | High | Med | `import_quality` score, mandatory `structure.yaml` below 0.6, `--accept-pdf-quality` override, generated PDF fixture in CI, "best-effort" documented |
| R14 | Cheap model's Persian literary quality is below the bar | Med | High | Bake-off on the golden set; `quality` cost mode with `deepseek-v4-pro` critics/reviser; read-through pass; human spot check; report quality mode vs standard |
| R15 | Cost/control regression from thinking mode or cache misses | Med | Med | `thinking: disabled` default asserted by `shz doctor` and a unit test; any per-agent override shown in the estimate and report; cache-hit ratio reported with alert below 50%; prefix byte-stability test; cost report states the tariff used |

---

## 12. Answers from the product owner, and what remains

### 12.1 Locked decisions (2026-11-05)

| Question | Answer | Consequence in the build |
|---|---|---|
| Books / rights | User supplies EPUB or PDF; private use; will not publish | No rights gate; provenance recorded only (1.5) |
| Input formats | **EPUB preferred; else a text-layer PDF** — no scanned PDFs | PDF is a best-effort importer with a quality score, never OCR (6.0.1); EPUB stays first-class |
| Model | DeepSeek V4.1 Flash latest (`deepseek-flash`) | Default profile; `thinking` off by default (low-effort on critics is an M10 experiment); `deepseek-v4-pro` only for `quality` mode (4.5) |
| Cost ceiling | ≤ ~$10 per average book | Cap 10, cost modes, off-peak tariffs reported, cached prefix, estimates before spend (3.6, 9.5) |
| Bilingual | Optional side product | `--bilingual`, off by default (6.8.4) |
| Output | Open, editable, PDF-convertible | DOCX primary editable + EPUB + Markdown; PDF via LibreOffice (6.8) |
| Glossary | Optional, never required | `off` default, `provided` and `auto` modes (6.2) |
| Automation | **Fully unattended by default** | No gate blocks a run; review/anchors/brief-approval only behind flags (D10) |
| Scheduling | **Always start immediately**; owner decides when | No off-peak wait mode; report shows tariff + cost (9.5) |
| Interface | Local CLI | No server/web app in v1 (1.3) |
| Language | Iranian Persian only | No Dari/Tajik locale paths |
| Coding style | Kilo `/ponytail` skill for concise code | README handoff only; contracts/validation always win over brevity |

### 12.2 Still open (defaults are safe if unanswered)

1. **Footnotes:** default is sparing endnotes (max 1 per 2,000 words). Keep, or allow more for culture-heavy books?
2. **Verse:** included best-effort; is any target book verse-heavy (poetry collection, epic)?
3. **Seed assets:** any existing glossary, term list, or previously translated chapters to pass with `--glossary`?

Everything else is locked: no OCR/scanned PDFs, unattended by default, immediate start (owner schedules), EPUB preferred.

---

## 13. References and evidence base

**Architecture / prior art**
- TransAgents — multi-agent simulated translation company, preparation + editorial stages, MHP/BLP evaluation: [arXiv:2405.11804](https://arxiv.org/abs/2405.11804)
- DelTA — document-level agent with multi-tier memory, +4.58 consistency, +3.16 COMET: [arXiv:2410.08143](https://arxiv.org/abs/2410.08143)
- Karpinska & Iyyer — paragraph-level literary translation beats sentence-level, omissions persist: [arXiv:2304.03245](https://arxiv.org/abs/2304.03245)
- Translate-and-Revise — constraint feedback raises glossary adherence +15%: [arXiv:2407.13164](https://arxiv.org/abs/2407.13164)
- Terminology-constrained MT (WMT23) — violation detection + refine: [arXiv:2310.05824](https://arxiv.org/abs/2310.05824)
- bilingual_book_maker — session memory, 8,192-token compaction to handoff report, pinned glossary: [github](https://github.com/yihong0618/bilingual_book_maker)
- TranslateBooksWithLLMs — per-chunk glossary filtering (≤50, longest-first, cast block): [github](https://github.com/hydropix/TranslateBooksWithLLMs) · [GLOSSARY.md](https://github.com/hydropix/TranslateBooksWithLLMs/blob/main/docs/GLOSSARY.md)
- GalTransl — production problem-detection rules (repetition, length, script leak, glossary compliance): [github](https://github.com/GalTransl/GalTransl)
- ln-epub-translator — human-editable frozen auto-dictionary; output-token truncation evidence: [github](https://github.com/hanel2527/ln-epub-translator)
- Andrew Ng translation-agent — reflection loop: [github](https://github.com/andrewyng/translation-agent)

**Evaluation**
- LITEVAL-CORPUS — auto metrics identify human translations ≤20%; MQM-with-novices ~60% misjudged; BWS 80–100%: [arXiv:2410.18697](https://arxiv.org/abs/2410.18697)
- GEMBA / GEMBA-MQM — reference-free judge with error spans: [arXiv:2302.14520](https://arxiv.org/abs/2302.14520) · [arXiv:2310.13988](https://arxiv.org/abs/2310.13988)
- LiTransProQA — literary-specific reference-free QA: [arXiv:2505.05423](https://arxiv.org/abs/2505.05423)
- Ataee, Huart & Popescu-Belis — chain-of-thought improves context-aware translation (12 LLMs, EN-FR DiscEvalMT, anaphora/lexical cohesion, "wise get wiser"): [ACL Anthology](https://aclanthology.org/2026.lrec-1.298/)
- Li, Ji & Tiedemann — test-time scaling of reasoning models for MT: limited/plateauing gains for direct translation, forced reasoning degrades, **post-editing benefits**: [arXiv:2510.06471](https://arxiv.org/abs/2510.06471)
- DRT — Deep Reasoning Translation via long CoT (ACL 2025): literariness +0.36 in human BWS, ~12× slower, gains tied to domain fine-tuning: [arXiv:2412.17498](https://arxiv.org/abs/2412.17498)
- MQM error typology: [themqm.org](https://themqm.org/)
- COMET (context only, not used as a gate): [arXiv:2009.12021](https://arxiv.org/abs/2009.12021)

**Provider, cost, tooling**
- DeepSeek models & pricing (`deepseek-flash` = DeepSeek-V4.1-Flash-1017; cache-hit/cache-miss tiers; peak/off-peak hours; 1M context, 384K output): [api-docs.deepseek.com/quick_start/pricing](https://api-docs.deepseek.com/quick_start/pricing/)
- DeepSeek thinking mode (toggle, effort, ignores `temperature`; reasoning tokens billed as output): [api-docs.deepseek.com/guides/thinking_mode](https://api-docs.deepseek.com/guides/thinking_mode)
- DeepSeek-V4.1-Flash model card (763B MoE, MIT, 1M context): [llm-stats.com/models/deepseek-v4.1-flash](https://llm-stats.com/models/deepseek-v4.1-flash)
- Ponytail skill (Kilo marketplace ruleset; YAGNI ladder that explicitly preserves validation/error handling): [github.com/StarQuant/ponytail-skill](https://github.com/StarQuant/ponytail-skill)

**Persian**
- Persian literary GenAI study (failure modes, length drift 452w/6s → 699w/18s): [jpll.ui.ac.ir](https://jpll.ui.ac.ir/article_30217.html)
- Persian idiom translation study: [arXiv:2412.09993](https://arxiv.org/abs/2412.09993)
- MIZAN corpus: [arXiv:1801.02107](https://arxiv.org/abs/1801.02107); FLORES-200: [arXiv:2207.04672](https://arxiv.org/abs/2207.04672)
- MELAC: [ACL](https://aclanthology.org/2025.ijcnlp-long.105/); FarsEval: [arXiv:2504.14690](https://arxiv.org/abs/2504.14690)
- hazm (MIT) normalizer/tokenizers: [github](https://github.com/roshan-research/hazm) · persiantools (MIT, zero deps): [PyPI](https://pypi.org/project/persiantools/)
- Persian orthography study (paywalled, UNVERIFIED): [magiran](https://www.magiran.com/paper/2927289/)

**EPUB / typography**
- EPUB 3.3 (W3C Recommendation, 2026-01-13): [w3.org/TR/epub-33](https://www.w3.org/TR/epub-33/) · Overview: [epub-overview-33](https://www.w3.org/TR/epub-overview-33/) · Accessibility 1.1: [epub-a11y-11](https://www.w3.org/TR/epub-a11y-11/)
- Vazirmatn (OFL-1.1): [github](https://github.com/rastikerdar/vazirmatn) · Estedad: [github](https://github.com/aminabedi68/Estedad)
- epubcheck: [github](https://github.com/w3c/epubcheck)

**Full research notes:** `research/prior-art.md` (130 lines, 20-row tool table) and `research/persian-engineering.md` (249 lines, normalization code, EPUB recipe, library licence table). Every claim marked `UNVERIFIED` in those files must stay unverified in any external communication.

---

## Appendix A — Default Persian style decisions (editable in `brief.yaml`)

| Decision | Default | Alternatives |
|---|---|---|
| Target variety | Iranian standard Persian (`fa-IR`) | Dari, Tajik (not v1) |
| Narration register | Contemporary literary standard (`معیار ادبی`) | classicizing, colloquial |
| Dialogue register | Controlled colloquial (`محاوره‌ی کنترل‌شده`) | full colloquial, formal |
| Address forms | `تو` for family/close friends, `شما` for strangers/elders; per-pair decisions in ledger | uniform `شما` (archaic feel), uniform `تو` |
| Digits | Persian `۰۱۲۳۴۵۶۷۸۹` | ASCII (technical books) |
| Dates | Gregorian with Persian digits (`۱۹۲۵`) | Jalali conversion (only if the brief says so) |
| Dialogue punctuation | `«…»` with `گفت:` inside the same paragraph | em-dash `—` style |
| Ellipsis | `…` (U+2026) | `...` (never) |
| Footnotes | Endnotes at chapter end, max 1 per 2,000 words | book-end endnotes, none |
| Names | Phonetic transliteration + established Persian forms where they exist | keep Latin (non-fiction only) |
| Measurements | Keep imperial; Persian equivalent in parentheses only when plot-relevant | convert all |
| Foreign phrases | Keep, italicized; footnote first occurrence | translate, keep original in brackets |
| Chapter titles | Translated, not transliterated | bilingual half-title |
| Poetry | Preserve line breaks; do not rhyme; prioritize image and rhythm | prose-poem rendering |
| Profanity | Faithful, register-matched | softened (publisher-specific) |

## Appendix B — Glossary of internal terms

- **Segment** — one translatable block (paragraph, heading, stanza, footnote text). Unit of the DB and of review.
- **Window** — 4–10 consecutive segments sent in one LLM call; the unit of cost and context.
- **Context pack** — the budgeted, ordered set of blocks (rules, brief, glossary subset, anchors, summary, ledger, previous paragraphs) attached to a window.
- **Book brief** — approved style/convention decisions for the book (`brief.yaml`).
- **Ledger** — append-only continuity memory: characters, terms, decisions, open questions.
- **Anchor** — an approved source/Persian sentence pair used as a few-shot style reference.
- **Pass** — a translation version produced by a specific stage (`draft`, `revision`, `repair`, `human`).
- **Risk score** — deterministic 0–100 priority for human review.
- **Golden set** — hand-checked evaluation corpus with professional Persian references.
- **BWS** — best-worst scaling; the human preference protocol used for evaluation.
- **MQM** — Multidimensional Quality Metrics; the error typology used by linters, judge and human review.

