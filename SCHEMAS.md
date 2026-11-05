# Tarjoman — Schemas, DDL, Configuration, Artifacts

Companion to `IMPLEMENTATION_PLAN.md`. Everything here is normative: the implementer must not rename tables, columns, enum values, config keys or artifact fields without updating this file and `DECISIONS.md`.

---

## 1. Domain enums (single source of truth in `core/models.py`)

```python
class ChapterKind(StrEnum):   FRONT="front"; BODY="body"; BACK="back"
class SegmentKind(StrEnum):   PROSE="prose"; HEADING="heading"; VERSE="verse"; QUOTE="quote"
                              FOOTNOTE="footnote"; CAPTION="caption"; TABLE="table"
class SegmentStatus(StrEnum): PENDING="pending"; DRAFTED="drafted"; REVISED="revised"
                              DONE="done"; FLAGGED="flagged"; FAILED="failed"; SKIPPED="skipped"
class ChapterStatus(StrEnum): PENDING="pending"; ANALYZED="analyzed"; TRANSLATING="translating"
                              TRANSLATED="translated"; AUDITED="audited"; REVIEWED="reviewed"; FINAL="final"
class RunStatus(StrEnum):     RUNNING="running"; COMPLETED="completed"; FAILED="failed"
                              ABORTED="aborted"; PAUSED="paused"
class PassKind(StrEnum):      DRAFT="draft"; REVISION="revision"; REPAIR="repair"; HUMAN="human"
class GlossaryKind(StrEnum):  CHARACTER="character"; PLACE="place"; ORGANIZATION="organization"
                              TERM="term"; MOTIF="motif"; FORM_OF_ADDRESS="form_of_address"
                              TITLE="title"; UNIT="unit"
class GlossaryStatus(StrEnum):PROPOSED="proposed"; APPROVED="approved"; REJECTED="rejected"; DEPRECATED="deprecated"
class Severity(StrEnum):      INFO="info"; WARN="warn"; ERROR="error"; CRITICAL="critical"
class MqmType(StrEnum):       OMISSION="omission"; ADDITION="addition"; MISTRANSLATION="mistranslation"
                              UNTRANSLATED="untranslated"; FLUENCY="fluency"; TERMINOLOGY="terminology"
                              LOCALE="locale"; STYLE="style"
class RightsBasis(StrEnum):   PUBLIC_DOMAIN="public_domain"; LICENSED="licensed"; USER_SUPPLIED="user_supplied"

# Agent-facing severity (critics, auditors, read-through). Persisted form is Severity.
AgentSeverity = Literal["minor", "major", "critical"]
```

**Severity is two-vocabularies-by-design.** Agents (P06/P07/P10/P11) speak `minor | major | critical` because that is how editors triage; persistence and linters speak `info | warn | error | critical`. `agents/base.py:normalize_severity()` maps `minor→warn`, `major→error`, `critical→critical` at the boundary. Only the 4-valued form appears in the DB, `flags.csv`, exit-code decisions and export gating. Never persist an agent-level severity.

`CRITIQUE_TYPE` is an open string vocabulary (MQM type or a style subtype such as `rhythm`, `word_choice`, `register`, `translationese`, `dialogue`, `imagery`, `pacing`), persisted as-is; do not model it as a provider-unfriendly `Enum | str` union.

`L01…L20` are lint codes (Section 7.2 of the plan), stored as plain strings with a frozen registry in `persian/lint.py`.

---

## 2. Pydantic models (agent I/O)

All models: `model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)`. JSON Schema is generated with `model_json_schema()` for structured output; enums are serialized as their string values. Unknown fields are a validation error (feeds the repair ladder, never silently dropped).

```python
# ---------- analysis ----------
class Motif(BaseModel):
    motif: str
    why_it_matters: str

class BookBrief(BaseModel):
    genre: str
    subgenre: str
    era_and_setting: str
    pov: str
    narrative_tense: str
    narrative_distance: str
    register: str
    register_target_fa: Literal["literary-standard", "formal-classic", "colloquial-narrative", "mixed"]
    address_policy: str
    sentence_rhythm: str
    dialogue_style: str
    figurative_language: str
    humor_irony: str
    cultural_references_policy: str
    name_policy: str
    measurement_policy: str
    foreign_text_policy: str
    taboo_policy: str
    footnote_policy: Literal["none", "sparing", "allowed"]
    style_references: list[str] = Field(min_length=0, max_length=2)
    do_rules: list[str] = Field(min_length=5, max_length=10)
    dont_rules: list[str] = Field(min_length=5, max_length=10)
    key_motifs: list[Motif] = Field(default_factory=list, max_length=8)
    uncertainties: list[str] = Field(default_factory=list, max_length=5)

class ContinuityFacts(BaseModel):
    characters: list[str] = []
    relationships: list[str] = []
    places_and_objects: list[str] = []
    time: list[str] = []
    open_threads: list[str] = []

class ChapterSummary(BaseModel):
    summary_en: str = Field(min_length=200, max_length=2000)
    continuity: ContinuityFacts
    tone_notes: str
    translation_landmines: list[str] = Field(default_factory=list, max_length=3)

class Entity(BaseModel):
    en: str
    kind: GlossaryKind
    surface_forms: list[str] = Field(default_factory=list)
    count: int = Field(ge=1)
    gender: Literal["male", "female", "unknown", "not_applicable"] = "unknown"
    role: str = ""
    context_quote: str = ""
    first_segment_id: str = ""
    needs_translation: bool = True
    notes: str = ""

class EntityBatch(BaseModel):
    entities: list[Entity]
    uncertain: list[dict[str, str]] = Field(default_factory=list)   # {"en","why"}

class GlossaryProposal(BaseModel):
    en: str
    kind: GlossaryKind
    fa: str
    alternatives: list[str] = Field(default_factory=list, max_length=2)
    transliteration: str = ""
    gender: Literal["male", "female", "unknown", "not_applicable"] = "unknown"
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    aliases_fa: list[str] = Field(default_factory=list)
    notes: str = ""

class GlossaryProposals(BaseModel):
    proposals: list[GlossaryProposal]
    refuse: list[dict[str, str]] = Field(default_factory=list)     # {"en","why"}

# ---------- translation ----------
class TranslatedSegment(BaseModel):
    id: str
    fa_markup: str = Field(min_length=1)
    notes: str = ""

class GlossarySuggestion(BaseModel):
    en: str
    fa: str
    kind: GlossaryKind
    reason: str
    segment_id: str

class WindowTranslation(BaseModel):
    segments: list[TranslatedSegment]
    glossary_suggestions: list[GlossarySuggestion] = Field(default_factory=list)
    window_notes: str = ""
    applied: list[dict[str, Any]] = Field(default_factory=list)     # reviser only
    skipped: list[dict[str, Any]] = Field(default_factory=list)     # reviser only

class TranslatorOutput(BaseModel):
    """Narrow schema for P05 (translator). The reviser (P08) uses WindowTranslation."""
    segments: list[TranslatedSegment]
    glossary_suggestions: list[GlossarySuggestion] = Field(default_factory=list)
    window_notes: str = ""

class CritiqueIssue(BaseModel):
    segment_id: str
    type: str                         # MQM type or style subtype — open vocabulary, see §1
    severity: AgentSeverity           # minor|major|critical; mapped on persist
    quote_source: str = ""
    quote_target: str = ""
    problem: str
    suggested_fix: str = ""

class Critique(BaseModel):
    issues: list[CritiqueIssue] = Field(default_factory=list)
    verdict: Literal["clean", "minor_issues", "major_issues", "needs_redraft"]
    segment_verdicts: dict[str, str] = Field(default_factory=dict)

class LedgerCharacter(BaseModel):
    en: str
    fa: str
    role: str = ""
    gender: Literal["male", "female", "unknown"] = "unknown"
    address_forms: dict[str, Literal["تو", "شما"]] = {}
    notes: str = ""

class LedgerUpdate(BaseModel):
    characters: list[LedgerCharacter] = []
    terms: list[dict[str, str]] = []          # {"en","fa","kind","status"}
    decisions: list[dict[str, str]] = []      # {"key","value"}
    open_questions: list[str] = Field(default_factory=list, max_length=5)
    corrections: list[dict[str, str]] = []    # {"key","old","new","why"}

class AuditFinding(BaseModel):
    kind: Literal["character", "address", "terminology", "pronoun", "timeline", "physical", "voice"]
    severity: AgentSeverity
    segment_id: str
    evidence: str
    conflicts_with: str = ""
    explanation: str
    suggested_resolution: str = ""

class AuditFindings(BaseModel):
    findings: list[AuditFinding] = []
    ledger_corrections: list[dict[str, str]] = []
    verdict: Literal["consistent", "minor_drift", "inconsistent"]

class ReadThroughIssue(BaseModel):
    passage: str
    problem: Literal["rhythm", "translationese", "register", "dialogue", "clarity", "imagery", "voice_break"]
    why_it_hurts: str
    suggested_rewrite: str
    severity: AgentSeverity

class ReadThroughFindings(BaseModel):
    issues: list[ReadThroughIssue] = Field(max_length=5)
    voice_verdict: Literal["consistent", "drifting", "broken"]
    chapter_impression: str

class JudgeError(BaseModel):
    type: MqmType
    severity: Literal["minor", "major", "critical"]
    quote_source: str = ""
    quote_target: str = ""
    comment: str = ""

class JudgeVerdict(BaseModel):
    scores: dict[str, int]      # five keys, each 1..5 — validated in a model_validator
    errors: list[JudgeError] = []
    verdict: Literal["pass", "minor_issues", "major_issues", "reject"]
    rationale: str
```

Validation rules beyond types (implement in `model_validator`s):
- `TranslatorOutput.segments` / `WindowTranslation.segments` ids must equal the requested window ids exactly (checked in the agent wrapper, not in the model).
- `JudgeVerdict.scores` keys must be exactly `{fidelity, naturalness, style_fidelity, terminology, cultural_adaptation}` with ints 1–5.
- **Quote evidence is canonicalized before matching**: strip sentinels, NFKC, collapse whitespace, fold Persian digits. A quote that still does not match is **not** a validation failure — it downgrades the issue to `warn` and stores `unverified_quote: true` in `context_json`. Routing a near-miss quote into the repair ladder is how a pipeline livelocks.
- Agent severities pass through `normalize_severity()` (`minor→warn`, `major→error`, `critical→critical`) before persistence; nothing else may write `issues.severity`.

---

## 3. SQLite DDL (`core/schema.sql`)

Connection setup: `sqlite3.connect(path, check_same_thread=False)`, `row_factory=sqlite3.Row`, then

```sql
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA synchronous=NORMAL;
PRAGMA busy_timeout=5000;
PRAGMA temp_store=MEMORY;
```

One `threading.RLock` inside `Database` for writes; reads are lock-free. Every multi-row write is one transaction. Schema migrations: `schema_version` table + numbered `migrations/00N_*.sql`, applied in order inside a transaction, with a backup before each upgrade.

```sql
CREATE TABLE schema_version (version INTEGER NOT NULL);

CREATE TABLE books (
  id              INTEGER PRIMARY KEY,
  slug            TEXT    NOT NULL UNIQUE,
  title_en        TEXT    NOT NULL,
  title_fa        TEXT,
  author          TEXT,
  translator      TEXT,
  source_path     TEXT    NOT NULL,
  source_sha256   TEXT    NOT NULL,
  source_format   TEXT    NOT NULL DEFAULT 'epub'
                  CHECK (source_format IN ('epub','pdf','docx','txt','md')),
  import_quality  REAL,                          -- PDF only, 0..1 (see plan 6.0.1)
  rights_basis    TEXT    NOT NULL DEFAULT 'user_supplied'
                  CHECK (rights_basis IN ('public_domain','licensed','user_supplied')),
  language        TEXT    NOT NULL DEFAULT 'fa',
  metadata_json   TEXT    NOT NULL DEFAULT '{}',
  brief_hash      TEXT,
  source_metrics_json TEXT NOT NULL DEFAULT '{}',
  created_at      TEXT    NOT NULL
);

CREATE TABLE chapters (
  id          INTEGER PRIMARY KEY,
  book_id     INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  idx         INTEGER NOT NULL,
  kind        TEXT    NOT NULL CHECK (kind IN ('front','body','back')),
  title_en    TEXT,
  title_fa    TEXT,
  source_doc  TEXT,
  status      TEXT    NOT NULL DEFAULT 'pending'
              CHECK (status IN ('pending','analyzed','translating','translated','audited','reviewed','final')),
  word_count  INTEGER NOT NULL DEFAULT 0,
  rolling_summary_fa TEXT,                       -- 2-3 sentence "chapter so far", refreshed every 20 windows
  created_at  TEXT    NOT NULL,
  UNIQUE (book_id, idx)
);

CREATE TABLE segments (
  id            TEXT    PRIMARY KEY,             -- c003.s0042
  chapter_id    INTEGER NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
  idx           INTEGER NOT NULL,
  kind          TEXT    NOT NULL,
  source_text   TEXT    NOT NULL,               -- sentinel-free plain text
  source_html   TEXT    NOT NULL,               -- original outer HTML (for audit)
  nodes_json    TEXT    NOT NULL DEFAULT '[]',  -- inline node list (see plan 6.0.3)
  content_hash  TEXT    NOT NULL,
  status        TEXT    NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','drafted','revised','done','flagged','failed','skipped')),
  risk_score    INTEGER NOT NULL DEFAULT 0 CHECK (risk_score BETWEEN 0 AND 100),
  tokens_est    INTEGER NOT NULL DEFAULT 0,
  sentence_count INTEGER NOT NULL DEFAULT 0,
  dialogue_ratio REAL   NOT NULL DEFAULT 0,
  lang_mix      REAL    NOT NULL DEFAULT 0,     -- share of non-Latin letters; >0.3 => "foreign" treatment
  split_of      TEXT    REFERENCES segments(id) ON DELETE SET NULL,  -- set when an oversized prose block was split
  created_at    TEXT    NOT NULL,
  UNIQUE (chapter_id, idx)
);

CREATE TABLE translations (
  id            INTEGER PRIMARY KEY,
  segment_id    TEXT    NOT NULL REFERENCES segments(id) ON DELETE CASCADE,
  version       INTEGER NOT NULL,
  is_current    INTEGER NOT NULL DEFAULT 0 CHECK (is_current IN (0,1)),
  pass          TEXT    NOT NULL CHECK (pass IN ('draft','revision','repair','human')),
  fa_text       TEXT    NOT NULL,               -- sentinel-free, normalized
  fa_markup     TEXT    NOT NULL,               -- sentinels + inline nodes
  notes_json    TEXT    NOT NULL DEFAULT '{}',
  glossary_hash TEXT,                            -- hash of approved glossary used for this version
  glossary_terms_json TEXT NOT NULL DEFAULT '[]',-- EN terms injected into this window (invalidation support)
  model         TEXT    NOT NULL,
  prompt_hash   TEXT,
  tokens_in     INTEGER NOT NULL DEFAULT 0,
  tokens_out    INTEGER NOT NULL DEFAULT 0,
  cost_usd      REAL    NOT NULL DEFAULT 0,
  latency_ms    INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT    NOT NULL,
  UNIQUE (segment_id, version)
);
CREATE UNIQUE INDEX ux_translations_current ON translations(segment_id) WHERE is_current = 1;

CREATE TABLE issues (
  id            INTEGER PRIMARY KEY,
  segment_id    TEXT    REFERENCES segments(id) ON DELETE CASCADE,   -- NULL for chapter/book-scope issues
  chapter_id    INTEGER REFERENCES chapters(id) ON DELETE CASCADE,
  stage         TEXT    NOT NULL,               -- draft|critique|qa|audit|readthrough|export
  code          TEXT    NOT NULL,               -- L01..L20 or agent code
  severity      TEXT    NOT NULL CHECK (severity IN ('info','warn','error','critical')),
  message       TEXT    NOT NULL,
  quote_source  TEXT    DEFAULT '',
  quote_target  TEXT    DEFAULT '',
  context_json  TEXT    NOT NULL DEFAULT '{}',
  dedupe_key    TEXT    NOT NULL,               -- ALWAYS namespaced: "seg:c003.s0042:..." or "ch:c003:..."
  status        TEXT    NOT NULL DEFAULT 'open' CHECK (status IN ('open','resolved','waived')),
  resolved_by   TEXT,
  resolution    TEXT,
  resolved_at   TEXT,
  created_at    TEXT    NOT NULL,
  UNIQUE (stage, code, dedupe_key)
);

CREATE TABLE glossary (
  id            INTEGER PRIMARY KEY,
  book_id       INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  term_en       TEXT    NOT NULL,
  term_en_norm  TEXT    NOT NULL,               -- casefold, possessive/punct stripped
  term_fa       TEXT,
  kind          TEXT    NOT NULL,
  aliases_json  TEXT    NOT NULL DEFAULT '[]',  -- source-side alternative forms
  aliases_fa_json TEXT  NOT NULL DEFAULT '[]',  -- accepted Persian inflections
  status        TEXT    NOT NULL CHECK (status IN ('proposed','approved','rejected','deprecated')),
  source        TEXT    NOT NULL CHECK (source IN ('candidate','manual','imported','suggested')),
  confidence    REAL    NOT NULL DEFAULT 0,
  gender        TEXT    NOT NULL DEFAULT 'unknown' CHECK (gender IN ('male','female','unknown','not_applicable')),
  notes         TEXT    NOT NULL DEFAULT '',
  approved_by   TEXT,
  updated_at    TEXT    NOT NULL,
  UNIQUE (book_id, term_en_norm, kind)
);

-- Suggestions emitted by A5 at translate time; never enforced; promoted by a human.
CREATE TABLE glossary_suggestions (
  id            INTEGER PRIMARY KEY,
  book_id       INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  segment_id    TEXT    NOT NULL REFERENCES segments(id) ON DELETE CASCADE,
  term_en       TEXT    NOT NULL,
  term_en_norm  TEXT    NOT NULL,
  term_fa       TEXT    NOT NULL,
  kind          TEXT    NOT NULL,
  reason        TEXT    NOT NULL DEFAULT '',
  status        TEXT    NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','promoted','rejected')),
  created_at    TEXT    NOT NULL,
  UNIQUE (book_id, term_en_norm, segment_id)
);

CREATE TABLE ledger (
  id            INTEGER PRIMARY KEY,
  book_id       INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  chapter_idx   INTEGER NOT NULL,
  kind          TEXT    NOT NULL,               -- character|term|decision|question|anchor
  key           TEXT    NOT NULL,
  value_json    TEXT    NOT NULL,
  created_at    TEXT    NOT NULL,
  UNIQUE (book_id, kind, key)
);

CREATE TABLE chapter_summaries (
  chapter_id    INTEGER PRIMARY KEY REFERENCES chapters(id) ON DELETE CASCADE,
  summary_en    TEXT    NOT NULL,
  continuity_json TEXT  NOT NULL DEFAULT '{}',
  tone_notes    TEXT    NOT NULL DEFAULT '',
  landmines_json TEXT   NOT NULL DEFAULT '[]',
  created_at    TEXT    NOT NULL
);

CREATE TABLE runs (
  id            TEXT    PRIMARY KEY,            -- run_20261105T183000_ab12cd
  book_id       INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  stage         TEXT    NOT NULL,
  status        TEXT    NOT NULL CHECK (status IN ('running','completed','failed','aborted','paused')),
  config_json   TEXT    NOT NULL,
  window_plan_hash TEXT,                        -- sha256 of the planned window list; resume requires a match
  artifacts_hash_json TEXT NOT NULL DEFAULT '{}',-- brief/glossary/anchors hashes used by this run
  stats_json    TEXT    NOT NULL DEFAULT '{}',
  cost_usd      REAL    NOT NULL DEFAULT 0,
  started_at    TEXT    NOT NULL,
  finished_at   TEXT
);

CREATE TABLE llm_calls (
  id            INTEGER PRIMARY KEY,
  run_id        TEXT    REFERENCES runs(id) ON DELETE SET NULL,
  agent         TEXT    NOT NULL,               -- A1..A13
  model         TEXT    NOT NULL,
  prompt_hash   TEXT    NOT NULL,
  input_hash    TEXT    NOT NULL,
  cached        INTEGER NOT NULL DEFAULT 0,
  tokens_in     INTEGER NOT NULL DEFAULT 0,
  tokens_out    INTEGER NOT NULL DEFAULT 0,
  cost_usd      REAL    NOT NULL DEFAULT 0,
  latency_ms    INTEGER NOT NULL DEFAULT 0,
  retries       INTEGER NOT NULL DEFAULT 0,
  finish_reason TEXT,
  error         TEXT,
  created_at    TEXT    NOT NULL
);

CREATE TABLE cache (
  key           TEXT    PRIMARY KEY,
  value_json    TEXT    NOT NULL,
  created_at    TEXT    NOT NULL,
  last_hit_at   TEXT
);

CREATE INDEX ix_segments_chapter   ON segments(chapter_id, idx);
CREATE INDEX ix_segments_status    ON segments(status);
CREATE INDEX ix_translations_seg   ON translations(segment_id, version DESC);
CREATE INDEX ix_issues_seg         ON issues(segment_id, status);
CREATE INDEX ix_issues_status      ON issues(status, severity);
CREATE INDEX ix_llm_calls_run      ON llm_calls(run_id);
CREATE INDEX ix_glossary_status    ON glossary(book_id, status, kind);
CREATE INDEX ix_gloss_sugg_seg     ON glossary_suggestions(segment_id, status);
```

Invariants enforced in code (not expressible in SQLite):
- inserting a new current translation and clearing the old one happen in one transaction;
- a segment has `is_current=1` on exactly one version **if and only if** it has at least one translation — `pending`, `skipped` and `failed` segments with no translation have none (the partial index enforces at most one, the code enforces at least one for `drafted/revised/done/flagged`);
- `issues.dedupe_key` is always namespaced (`seg:` or `ch:`), so chapter-scope rows (`segment_id NULL`) dedupe correctly despite SQLite's NULL-distinct behaviour;
- `issues.status='waived'` requires `resolved_by` and `resolution`; a waived `error` no longer blocks export but stays visible in the report;
- `chapters.status` only moves forward (validated transitions), except `reviewed → translated` after corrections (allowed, logged);
- `translations.glossary_hash` equals the hash of the approved glossary at write time; a mismatch with the current hash marks the version `stale` for `tj qa --stale-glossary`.

---

## 4. Configuration (`configs/default.yaml`)

Load order: defaults → `configs/default.yaml` → book config (`books/<slug>/config.yaml`) → env (`TARJOMAN_*`) → CLI flags. Unknown keys are an error (typo protection). `tj doctor` prints the resolved config with secrets redacted.

```yaml
# Tarjoman configuration — every key is required to exist in this file (defaults),
# book configs override a subset. Values shown are the defaults.

project:
  name: tarjoman
  books_dir: books
  timezone: UTC

book:
  slug: null                   # derived from title if omitted
  source_lang: en
  target_lang: fa              # fa-IR content; dc:language is "fa"
  rights_basis: user_supplied  # informational only: public_domain | licensed | user_supplied
  translator: "Tarjoman"
  cover: null                  # path override; default uses source cover

provider:
  default: deepseek
  profile: deepseek            # deepseek | local | custom
  models: configs/models/deepseek.yaml
  api_base: https://api.deepseek.com
  thinking: disabled           # MUST be disabled by default (see plan 4.5/9.6)
  thinking_overrides: {}       # per-agent experiment, e.g. {A6: low, A8: low}; requires M10 evidence
  reasoning_allowance: 2048    # extra output-token headroom when thinking is enabled for an agent
  tariff: auto                 # auto | peak | offpeak; estimate always prints both
  timeout_s: 180
  max_retries: 4
  max_inflight: 4
  fallbacks: []
  log_prompts: false

glossary:
  mode: off                    # off (default) | provided | auto
  path: null                   # required when mode=provided (yaml/csv with en,fa[,kind])
  approval: auto               # auto | manual (manual gates characters/places)
  auto_approve_min_confidence: 0.90

pipeline:
  cost_mode: auto              # auto | economy | standard | quality (plan 3.6)
  window:
    max_paragraphs: 8
    max_source_tokens: 900
    min_paragraphs: 4
    overlap_paragraphs: 2      # context only, never retranslated
  critics:
    fidelity: true
    style: true
    max_revision_rounds: 1
    force_fidelity_on_length_outlier: true
  passes:
    readthrough: true
    backtranslate: false       # post-v1 only
    consistency_audit: true
  analyze:
    sample_token_budget: 18000
    summary_model_tier: cheap
    brief_approval: auto       # auto | manual
  resume: true

persian:
  digits: persian              # persian | ascii (ascii is required for technical books)
  keep_diacritics: false
  zwnj_repair: high_precision  # off | high_precision
  ellipsis: unicode            # unicode => … (N06); ascii => ...
  auto_fix: [digits, punctuation, whitespace]

review:
  enabled: true                # generates report + flags; never blocks export
  flag_threshold: 55
  sample_fraction: 0.05
  min_coverage_for_export: 1.0

budget:
  max_usd: 10                  # product-owner ceiling per book
  abort_on_exceed: true
  warn_at_fraction: 0.8

export:
  formats: [epub, docx, md, txt]
  pdf: false                   # requires LibreOffice; see plan 6.8.5
  bilingual: false             # optional side product
  epub:
    rtl: true
    embed_fonts: true
    font_body: Vazirmatn
    endnotes_per_chapter: true
    validate_with_epubcheck: true
  docx:
    page: A5
    endnotes: true
  markdown:
    front_matter: true
  bilingual_html:
    include_source: true

eval:
  golden_dir: tests/golden
  calibration_dir: eval/calibration
  judge_model: deepseek-v4-pro # REQUIRED; different model from the translator (same provider caveat)
  samples_per_item: 3
  temperature: 0.2             # sampling needs variance; three temperature-0 samples are identical

logging:
  level: INFO
  json_logs: true
  console: rich

concurrency:
  parallel_chapters: 1         # 1 = reading order (recommended); >1 trades coherence for speed
```

**Model profile** (`configs/models/deepseek.yaml`, default) — see plan §4.5; aliases (`quality`/`balanced`/`cheap`) are the only values allowed in code. DeepSeek defaults: `balanced`/`cheap` = `deepseek-flash`, `quality` = `deepseek-v4-pro`, `thinking: disabled`, `max_output_tokens` 32768/16384. `pricing_usd_per_mtok` must distinguish `cache_hit_input`, `input` and `output`, and `cost.py` doubles them during peak hours. `eval.judge_model` is an explicit id (`deepseek-v4-pro`), never an alias.

Derived/validated:
- `fa_ratio` (target tokens per source token) is not config: it lives in `books/<slug>/book.db` (`books.source_metrics_json`) and is recalibrated from usage.
- `persian.digits: ascii` is required for technical books; the validator then flags Persian digits as errors.
- `review.min_coverage_for_export: 1.0` blocks export when coverage < 100%.
- `provider.thinking` defaults to `disabled`; a per-agent override (`thinking_overrides`) is only allowed as an M10-tested experiment, must add `reasoning_allowance` to the output budget, and must appear in the estimate with its own cost/latency line.
- `glossary.mode: provided` requires `glossary.path` to exist and parse; `off` ignores it; `auto` runs the proposal agents.
- `budget.max_usd` above 50 warns (the product ceiling is 10).

**`configs/models/deepseek.yaml` (the shipped default):**

```yaml
provider: deepseek
base_url: https://api.deepseek.com
quality:   {model: "deepseek-v4-pro", temperature: null, max_output_tokens: 32768}
balanced:  {model: "deepseek-flash",  temperature: null, max_output_tokens: 32768}
cheap:     {model: "deepseek-flash",  temperature: null, max_output_tokens: 16384}
thinking: disabled
pricing_usd_per_mtok: {cache_hit_input: 0.003, input: 0.15, output: 0.60}  # off-peak; peak doubles
```

---

## 5. Artifacts

### 5.1 `artifacts/brief.yaml`

```yaml
# approved: 2026-11-05T18:40:12Z  hash: 9f2c...  (written by tj analyze --approve)
book_slug: the-old-man-and-the-sea
brief:
  genre: literary fiction
  subgenre: novella
  era_and_setting: 1940s Cuba, Gulf Stream, a small fishing village
  pov: third person limited, close to the old man
  narrative_tense: past simple, sparse flashbacks in past perfect
  narrative_distance: close; free indirect thought, little interiority naming
  register: plain, concrete, biblical cadence
  register_target_fa: literary-standard
  address_policy: narration neutral; the boy addresses the old man as شما (respect), the old man addresses the boy as تو
  sentence_rhythm: short declaratives, parataxis, few subordinate clauses, deliberate repetition
  dialogue_style: short turns, quoted speech, no dialect markers
  figurative_language: sea/lion/baseball imagery; metaphors carried by repetition, not ornament
  humor_irony: dry, self-deprecating, understated
  cultural_references_policy: keep baseball references; explain nothing; the reader infers
  name_policy: transliterate Spanish names per Persian convention (Santiago → سانتیاگو)
  measurement_policy: keep pounds/miles; no conversions
  foreign_text_policy: Spanish words stay, italicized, no footnote
  taboo_policy: faithful
  footnote_policy: sparing
  style_references:
    - "ترجمه‌های نجف دریابندری از همینینگوی — همان ایجاز و جمله‌های کوتاه"
  do_rules:
    - "جمله‌ها را کوتاه نگه دار؛ پاراگراف‌های بلند همینگوی را نشکن."
    - "تکرار عمدی واژه‌ها را حفظ کن؛ مترادف‌سازی، ریتم را می‌کشد."
    - "تصویر را ترجمه کن، نه توضیحش."
    - "گفت‌وگوها را با «...» و بدون توضیح اضافه بنویس."
    - "لحن پیرمرد را تلخ و آرام نگه دار؛ هرگز احساس را نام نبر."
  dont_rules:
    - "از «می‌باشد» و ساختارهای اداری استفاده نکن."
    - "مثل‌ها و استعاره‌ها را به فارسی توضیح نده."
    - "جمله‌های کوتاه را با «که» به هم وصل نکن."
    - "برای تنوع، مترادف‌سازی و افزودن صفت انجام نده."
    - "واژه‌های لاتین را جایگزین اصطلاح فارسی نکن."
  key_motifs:
    - motif: "the lions on the beach"
      why_it_matters: "returns three times; must be one fixed Persian image"
  uncertainties:
    - "آیا «مرلین» را در متن به فارسی تلفظ کنیم یا لاتین بگذاریم؟"
```

### 5.2 `artifacts/glossary.yaml`

```yaml
# source of truth for terminology; edited by humans; imported by `tj glossary sync`
version: 1
book_slug: the-old-man-and-the-sea
entries:
  - term_en: Santiago
    term_fa: سانتیاگو
    kind: character
    status: approved
    confidence: 1.0
    aliases_en: ["Santiago's", "Santiagos"]
    aliases_fa: ["سانتیاگو"]
    gender: male
    notes: "protagonist; never translate as «پیرمرد» when used as a name"
  - term_en: the old man
    term_fa: پیرمرد
    kind: motif
    status: approved
    confidence: 0.95
    notes: "title motif; keep definite/indefinite distinction in Persian"
  - term_en: marlin
    term_fa: مارلین
    kind: term
    status: proposed        # not enforced until approved
    confidence: 0.7
```

### 5.3 `artifacts/ledger.jsonl`

One JSON object per line, append-only (upsert key = `kind` + `key`):

```json
{"kind":"character","key":"santiago","value":{"en":"Santiago","fa":"سانتیاگو","role":"fisherman","gender":"male","address_forms":{"manolin":"شما"},"notes":"calls the sea 'la mar' (feminine)"},"chapter":1}
{"kind":"decision","key":"narration.past_simple","value":"روایت با گذشته ساده؛ پرهیز از ماضی استمراری جز در فلاش‌بک‌ها","chapter":1}
{"kind":"anchor","key":"a001","value":{"en":"He was an old man who fished alone in a skiff in the Gulf Stream.","fa":"پیرمردی بود که تنها در قایقی کوچک در جریان خلیج ماهی می‌گرفت."},"chapter":1}
```

### 5.4 `artifacts/anchors.yaml`

```yaml
# approved at gate 2 (first chapter review); minimum 2, maximum 3 pairs; used as few-shot style reference
version: 1
anchors:
  - id: a001
    en: "He was an old man who fished alone in a skiff in the Gulf Stream and he had gone eighty-four days now without taking a fish."
    fa: "پیرمردی بود که تنها در قایقی کوچک در جریان خلیج ماهی می‌گرفت و هشتاد و چهار روز بود که هیچ ماهی‌ای نگرفته بود."
    note: "sentence length, parataxis, past continuous choice"
  - id: a002
    en: "..."
    fa: "..."
    note: "..."
```

### 5.5 `review/corrections.jsonl`

One line per human edit (produced by the HTML report download or hand-written):

```json
{"segment_id":"c003.s0042","fa_markup":"او گفت: «برو، من اینجا می‌مانم.»","note":"kept the dash, fixed address form","reviewer":"editor","action":"accept"}
{"segment_id":"c003.s0051","action":"needs_work","note":"meaning of 'blessed' lost; see source sentence 2"}
```

`tj review apply` accepts `action ∈ {accept, needs_work}`; `accept` inserts a `pass=human` version (normalized, linted), `needs_work` reopens the segment and re-flags it without changing text.

### 5.6 `review/flags.csv`

Header (exact order):
```csv
segment_id,chapter,risk_score,codes,severities,source_excerpt,fa_excerpt,sample_flag,reviewed,reviewer_note
```

### 5.7 `runs/<run_id>/manifest.json`

```json
{
  "run_id": "run_20261105T183000_ab12cd",
  "book_slug": "the-old-man-and-the-sea",
  "stage": "translate",
  "status": "completed",
  "started_at": "2026-11-05T18:30:00Z",
  "finished_at": "2026-11-05T20:12:41Z",
  "config_snapshot": {"...": "resolved config, secrets redacted"},
  "prompt_versions": {"p05_translator": 3, "p06_fidelity_critic": 3},
  "window_plan_hash": "sha256:...",
  "artifacts_hash": {"brief": "sha256:...", "glossary": "sha256:...", "anchors": "sha256:..."},
  "models": {"quality": "provider/model-id", "cheap": "provider/model-id"},
  "stats": {"chapters": 12, "windows": 134, "segments": 1841, "cache_hit_rate": 0.41,
            "retries": 7, "failed_segments": 0, "cost_usd": 12.84},
  "token_ratios": {"en_tokens_per_char": 0.243, "fa_tokens_per_char": 0.47}
}
```

### 5.8 LLM output examples (for tests and mock fixtures)

`p05` valid output:
```json
{
  "segments": [
    {"id": "c001.s0001", "fa_markup": "پیرمردی بود که تنها در قایقی کوچک در ⟨2⟩جریان خلیج⟨/2⟩ ماهی می‌گرفت.", "notes": ""},
    {"id": "c001.s0002", "fa_markup": "هشتاد و چهار روز بود که هیچ ماهی‌ای نگرفته بود.", "notes": ""}
  ],
  "glossary_suggestions": [
    {"en": "skiff", "fa": "قایق کوچک", "kind": "term", "reason": "recurring object, no glossary entry", "segment_id": "c001.s0001"}
  ],
  "window_notes": ""
}
```

`p06` valid output:
```json
{
  "issues": [
    {"segment_id": "c001.s0001", "type": "omission", "severity": "major",
     "quote_source": "and he had gone eighty-four days now without taking a fish",
     "quote_target": "", "problem": "the eighty-four days clause is missing",
     "suggested_fix": "restore the clause after the first sentence"}
  ],
  "verdict": "major_issues",
  "segment_verdicts": {"c001.s0001": "major", "c001.s0002": "minor"}
}
```

---

### 5.9 Other artifacts and test fixtures (previously referenced, now defined)

**`artifacts/structure.yaml`** — optional manual chapter boundaries, overrides heuristic chapterization:
```yaml
version: 1
chapters:
  - title_en: "Chapter One"          # display title; Persian comes from translation
    start_text: "It was a bright cold day in April"   # must match exactly one segment's source_text prefix
    kind: body
  - title_en: "Part Two"
    start_text: "The fall came early that year"
    kind: body
```
Validation: every `start_text` must match exactly one segment (error lists near-misses); order must be increasing; unmatched segments merge into the preceding chapter; `kind ∈ front|body|back`.

**`artifacts/glossary.candidates.yaml`** — machine draft, human-editable before approval:
```yaml
version: 1
generated_at: "2026-11-05T18:35:00Z"
candidates:
  - en: "marlin"
    kind: term
    count: 14
    surface_forms: ["marlin", "the marlin", "marlins"]
    proposed_fa: "مارلین"
    alternatives: ["نیزه‌ماهی"]
    confidence: 0.72
    rationale: "recurring catch; transliteration keeps the Hemingway register"
    status: proposed            # proposed | approved | rejected  (mirrors DB)
```
`tj glossary review` reads this + DB; edits may change `proposed_fa`, `status`; `tj glossary sync` imports it.

**`artifacts/anchors.draft.yaml`** — gate-2 proposals, approved into `anchors.yaml`:
```yaml
version: 1
chapter: 1
candidates:
  - id: a001
    segment_id: c001.s0003
    en: "He was an old man who fished alone..."
    fa: "پیرمردی بود که تنها ماهی می‌گرفت..."
    note: "narration / longest prose"
    status: proposed            # proposed | approved | rejected
```

**`artifacts/chapter_summaries.jsonl`** — one object per line (DB is authoritative; this is export/inspection):
```json
{"chapter_id":3,"idx":3,"summary_en":"...","continuity":{"characters":["..."],"relationships":[],"places_and_objects":[],"time":[],"open_threads":[]},"tone_notes":"...","translation_landmines":["..."]}
```

**`books/<slug>/config.yaml`** — optional per-book overrides of `configs/default.yaml`; loaded between defaults and env as stated in §4. Same schema, partial, unknown keys rejected.

**MockProvider fixture format** (`tests/fixtures/mock/`):
```
tests/fixtures/mock/<prompt_id>/<request_key>.json
request_key = sha256(f"{model}|{temperature}|{rendered_prompt}|{schema_hash}")[:16]
```
```json
{"agent":"A5","prompt_id":"p05_translator","request_hash":"9f2c1a...",
 "fail_times":1,"failure":"invalid_json",
 "responses":[{"segments":[{"id":"c001.s0001","fa_markup":"...","notes":""}],"glossary_suggestions":[],"window_notes":""}]}
```
`MockProvider` raises `MissingFixture` on a cache miss (a test must never pass because the mock silently returned empty text), replays `fail_times` transient failures to exercise retry paths, and supports `--record` against a real provider to generate fixtures after a manual smoke run.

---

## 6. Output contract notes

- JSON Schema for structured output is generated from the Pydantic model with `extra="forbid"` and `required` for all non-defaulted fields; some providers reject `oneOf`/`$ref`, so `llm/schema.py` must inline refs and simplify enums to `{"type":"string","enum":[...]}` before sending. Cache the resulting schema hash.
- The translator is sent `TranslatorOutput` (narrow schema: segments + glossary_suggestions + window_notes); the reviser uses `WindowTranslation` (adds `applied`/`skipped`). Never send the wider schema to the translator — a model will happily invent `applied` entries.
- **Persisted-text pipeline (order matters):** raw model output → parse into `(sentinel | text-run)` tokens → validate sentinel ids → `normalize_fa` each **text run only** (never the sentinel tokens) → reassemble `fa_markup` (normalized text + untouched sentinels) → strip sentinels from the normalized runs to produce `fa_text` → run L02 on the reassembled markup. Normalizing before parsing lets N03's digit map rewrite sentinel ids; stripping before normalizing loses the mapping.
- Column names are single-valued: segments store `source_html` (raw input HTML) and `source_text`; translations store `fa_markup` (sentinels) and `fa_text` (plain). There is no `fa_html` or `html_original` column.
