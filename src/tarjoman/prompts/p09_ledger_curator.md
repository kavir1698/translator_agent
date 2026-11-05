---
id: p09_ledger_curator
version: 2
agent: A9
tier: cheap
temperature: 0.1
schema: LedgerUpdate
---

Maintain the continuity ledger for a book being translated into Persian. The
ledger is what keeps later chapters consistent: names, roles, address forms,
decisions and open questions. You update it from what this chapter established.

CHAPTER: {{ chapter_index }}
ENGLISH SUMMARY: {{ chapter_summary }}
PERSIAN EXCERPT (voice/terminology evidence only): {{ chapter_excerpt_fa }}
NEW TERMS THIS CHAPTER: {{ new_terms }}
TRANSLATOR NOTES: {{ translator_notes }}
EXISTING LEDGER (do not duplicate; correct only with evidence):
{{ existing_ledger }}

Return JSON:
{
  "characters": [{"en": "...", "fa": "...", "role": "...", "gender": "male|female|unknown",
                  "address_forms": {"<other character>": "تو|شما"},
                  "notes": "only continuity-relevant facts"}],
  "terms": [{"en": "...", "fa": "...", "kind": "...", "status": "proposed|approved"}],
  "decisions": [{"key": "short stable key", "value": "the decision, e.g. narration=past-simple; dialogue=controlled-colloquial"}],
  "open_questions": ["max 5, for the human editor"],
  "corrections": [{"key": "...", "old": "...", "new": "...", "why": "evidence from this chapter"}]
}

Rules:
- Only record what the text establishes. Never infer a relationship that is not shown.
- Address forms: record only pairs whose address form actually appeared (تو/شما).
- `address_forms` keys are normalized lowercase English names (casefold, titles/possessives stripped) — the same normalization as `glossary.term_en_norm`; never use a display form like `Mr. Bennet` as a key.
- If an existing ledger entry conflicts with this chapter, put it in "corrections"
  with the evidence, do not silently rewrite it.
- Keep values short and machine-usable; no prose paragraphs.

Return only the JSON object.
