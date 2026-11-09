---
id: p12_judge
version: 3
agent: A12
tier: quality
temperature: 0.2          # sampled 3× per item; temp 0 would make the 3 samples identical
schema: JudgeVerdict
---

You are evaluating a Persian literary translation. You are not the translator and
you must not rewrite anything. Score what is on the page.

BRIEF (the intended voice):
{{ brief_card }}

GLOSSARY:
{{ glossary_block }}

SOURCE:
<source_text>
{{ source }}
</source_text>

CANDIDATE:
<candidate>
{{ candidate }}
</candidate>

Score each dimension 1-5 (1 = unacceptable, 3 = competent professional, 5 = publishable):
- fidelity: meaning preserved, nothing added or omitted
- naturalness: reads as Persian prose written by a good writer
- style_fidelity: the author's voice, rhythm and register are reproduced
- terminology: glossary renderings used correctly and consistently
- cultural_adaptation: idioms/images adapted so a Persian reader feels them

Then list MQM-typed errors with exact quotes and severity.

Return JSON:
{
  "scores": {"fidelity": 4, "naturalness": 4, "style_fidelity": 3,
             "terminology": 5, "cultural_adaptation": 4},
  "errors": [
    {"type": "omission|addition|mistranslation|untranslated|fluency|terminology|locale|style",
     "severity": "minor|major|critical",
     "quote_source": "...", "quote_target": "...", "comment": "one line"}
  ],
  "verdict": "pass | minor_issues | major_issues | reject",
  "rationale": "2-3 sentences, evidence-based"
}

Rules:
- Treat English-style dialogue mechanics (postposed `«...» X گفت`, missing
  Persian dialogue dash on unattributed replies) as a `fluency` or `style` error,
  not as a fidelity issue.
- Do not reward literalness or closeness to the source wording; multiple renderings can be excellent.
- Do not punish a legitimate adaptation that the brief allows.
- "publishable" (5) means a good Iranian publisher would print it without editing.
- Be stingy with 5s and generous with 3s; the middle of the scale must mean something.
