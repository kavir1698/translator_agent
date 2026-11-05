---
id: p02_summarizer
version: 2
agent: A2
tier: cheap
temperature: 0.2
schema: ChapterSummary
---

Summarize this chapter for a translator who will translate it and the following
chapters. The summary is working memory, not marketing copy.

<source_text chapter="{{ chapter_index }}" title="{{ chapter_title }}">
{{ source_text }}
</source_text>

Return JSON:
{
  "summary_en": "120-180 words. What happens, who does what, emotional arc, tone.",
  "continuity": {
    "characters": ["names introduced or developed in this chapter"],
    "relationships": ["who now knows/owes/loves/hates whom — only what changed"],
    "places_and_objects": ["places, objects, documents that will matter later"],
    "time": ["time jumps, elapsed time, season/date facts"],
    "open_threads": ["unresolved questions, promises, threats"]
  },
  "tone_notes": "one or two sentences on voice/pacing specific to this chapter",
  "translation_landmines": ["max 3: puns, ambiguities, dialect, cultural references a translator must not flatten"]
}

Rules:
- Use the names exactly as they appear in the source.
- Do not interpret or praise. Do not summarize the author's themes unless the text states them.
- 120-180 words for "summary_en"; other fields are short lists.
- If the chapter is mostly dialogue, say what the conversation is *doing* (negotiating, confessing, threatening), not just what it is about.

Return only the JSON object.
