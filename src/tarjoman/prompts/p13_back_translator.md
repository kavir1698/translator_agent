---
id: p13_back_translator
version: 2
agent: A13
tier: quality
temperature: 0.2
schema: WindowTranslation
---

Translate this Persian passage back into plain, literal English for a quality
check. Do not improve it, do not make it literary, do not fix its logic. Preserve
its structure, its ambiguities, and its flaws. Keep names as they are transliterated.

<persian>
{{ fa_text }}
</persian>

Return only this JSON object: {"segments": [{"id": "{{ segment_id }}", "fa_markup": "<literal English here>"}]}
