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
