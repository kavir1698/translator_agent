---
id: p03_entities
version: 2
agent: A3
tier: cheap
temperature: 0.0
schema: EntityBatch
---

Extract entities that must be translated consistently across a whole book.

<source_text batch="{{ batch_index }}">
{{ source_text }}
</source_text>

Already known (do not re-report unless you have a correction):
{{ known_entities }}

Return JSON:
{
  "entities": [
    {
      "en": "canonical English form",
      "kind": "character | place | organization | term | motif | form_of_address | title | unit",
      "surface_forms": ["every form seen: Mr. Bennet, Bennet, Bennet's"],
      "count": 3,
      "gender": "male | female | unknown | not_applicable",
      "role": "short role if a character, else empty",
      "context_quote": "the shortest phrase that shows how it is used",
      "first_segment_id": "c003.s0012",
      "needs_translation": true,
      "notes": "ambiguities only"
    }
  ],
  "uncertain": [{"en": "...", "why": "..."}]
}

Rules:
- Include only things that recur or are structurally important. A name that appears
  once in passing is not a glossary entry unless it is a place/object the plot needs.
- Do not include common nouns unless they are coined terms, ranks, or motifs.
- "form_of_address" covers Sir/Madam/Mr./Mrs./Miss/Doctor/etc. as used for specific
  characters, not as general vocabulary.
- Do not translate anything. Do not suggest Persian forms.
- The batch arrives as labelled segments; set "first_segment_id" to the id of the
  first segment where the entity appears.
- Prefer the most frequent surface form as "en"; keep possessives and titles in
  "surface_forms".

Return only the JSON object.
