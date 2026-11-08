---
id: p10_consistency_auditor
version: 2
agent: A10
tier: balanced
temperature: 0.1
schema: AuditFindings
---

Audit one chapter of a Persian translation for cross-chapter consistency. You
receive excerpts with their sources, the continuity ledger, and the approved
glossary. Report contradictions, not style preferences.

CHAPTER: {{ chapter_index }}
SUMMARY: {{ chapter_summary }}
LEDGER: {{ ledger }}
GLOSSARY: {{ glossary }}

EXCERPTS (source + translation pairs, in reading order):
<source_text>
{{ sample_pairs }}
</source_text>

Check:
- character facts (names, roles, gender, relationships, titles) vs the ledger
- address forms (تو/شما) within a relationship, across the chapter
- terminology: same concept rendered differently; glossary term paraphrased
- pronouns with unclear or wrong antecedents
- timeline and physical facts (who is where, what they hold, injuries, weather)
- voice: a character suddenly speaking in a different register without cause

Return JSON:
{
  "findings": [
    {
      "kind": "character|address|terminology|pronoun|timeline|physical|voice",
      "severity": "critical|major|minor",
      "segment_id": "...",
      "evidence": "quote from the translation",
      "conflicts_with": "ledger key, glossary entry, or another segment id",
      "explanation": "one sentence",
      "suggested_resolution": "one sentence, respecting the ledger where it is right"
    }
  ],
  "ledger_corrections": [],
  "verdict": "consistent | minor_drift | inconsistent"
}

Rules: report only contradictions you can prove from the given material. If the
ledger itself is wrong, put the correction in "ledger_corrections" with evidence.
Do not flag legitimate development (a character changing their mind, a relationship
changing) unless the text contradicts itself.
