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
  import_quality  REAL,
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
  rolling_summary_fa TEXT,
  created_at  TEXT    NOT NULL,
  UNIQUE (book_id, idx)
);

CREATE TABLE segments (
  id            TEXT    PRIMARY KEY,
  chapter_id    INTEGER NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
  idx           INTEGER NOT NULL,
  kind          TEXT    NOT NULL,
  source_text   TEXT    NOT NULL,
  source_html   TEXT    NOT NULL,
  nodes_json    TEXT    NOT NULL DEFAULT '[]',
  content_hash  TEXT    NOT NULL,
  status        TEXT    NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','drafted','revised','done','flagged','failed','skipped')),
  risk_score    INTEGER NOT NULL DEFAULT 0 CHECK (risk_score BETWEEN 0 AND 100),
  tokens_est    INTEGER NOT NULL DEFAULT 0,
  sentence_count INTEGER NOT NULL DEFAULT 0,
  dialogue_ratio REAL   NOT NULL DEFAULT 0,
  lang_mix      REAL    NOT NULL DEFAULT 0,
  split_of      TEXT    REFERENCES segments(id) ON DELETE SET NULL,
  created_at    TEXT    NOT NULL,
  UNIQUE (chapter_id, idx)
);

CREATE TABLE translations (
  id            INTEGER PRIMARY KEY,
  segment_id    TEXT    NOT NULL REFERENCES segments(id) ON DELETE CASCADE,
  version       INTEGER NOT NULL,
  is_current    INTEGER NOT NULL DEFAULT 0 CHECK (is_current IN (0,1)),
  pass          TEXT    NOT NULL CHECK (pass IN ('draft','revision','repair','human')),
  fa_text       TEXT    NOT NULL,
  fa_markup     TEXT    NOT NULL,
  notes_json    TEXT    NOT NULL DEFAULT '{}',
  glossary_hash TEXT,
  glossary_terms_json TEXT NOT NULL DEFAULT '[]',
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
  segment_id    TEXT    REFERENCES segments(id) ON DELETE CASCADE,
  chapter_id    INTEGER REFERENCES chapters(id) ON DELETE CASCADE,
  stage         TEXT    NOT NULL,
  code          TEXT    NOT NULL,
  severity      TEXT    NOT NULL CHECK (severity IN ('info','warn','error','critical')),
  message       TEXT    NOT NULL,
  quote_source  TEXT    DEFAULT '',
  quote_target  TEXT    DEFAULT '',
  context_json  TEXT    NOT NULL DEFAULT '{}',
  dedupe_key    TEXT    NOT NULL,
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
  term_en_norm  TEXT    NOT NULL,
  term_fa       TEXT,
  kind          TEXT    NOT NULL,
  aliases_json  TEXT    NOT NULL DEFAULT '[]',
  aliases_fa_json TEXT  NOT NULL DEFAULT '[]',
  status        TEXT    NOT NULL CHECK (status IN ('proposed','approved','rejected','deprecated')),
  source        TEXT    NOT NULL CHECK (source IN ('candidate','manual','imported','suggested')),
  confidence    REAL    NOT NULL DEFAULT 0,
  gender        TEXT    NOT NULL DEFAULT 'unknown' CHECK (gender IN ('male','female','unknown','not_applicable')),
  notes         TEXT    NOT NULL DEFAULT '',
  approved_by   TEXT,
  updated_at    TEXT    NOT NULL,
  UNIQUE (book_id, term_en_norm, kind)
);

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
  kind          TEXT    NOT NULL,
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
  id            TEXT    PRIMARY KEY,
  book_id       INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  stage         TEXT    NOT NULL,
  status        TEXT    NOT NULL CHECK (status IN ('running','completed','failed','aborted','paused')),
  config_json   TEXT    NOT NULL,
  window_plan_hash TEXT,
  artifacts_hash_json TEXT NOT NULL DEFAULT '{}',
  stats_json    TEXT    NOT NULL DEFAULT '{}',
  cost_usd      REAL    NOT NULL DEFAULT 0,
  started_at    TEXT    NOT NULL,
  finished_at   TEXT
);

CREATE TABLE llm_calls (
  id            INTEGER PRIMARY KEY,
  run_id        TEXT    REFERENCES runs(id) ON DELETE SET NULL,
  agent         TEXT    NOT NULL,
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
