# Diagnosis Rules

## Scoring Requirements
- Score all five content dimensions: concept_accuracy, structure_completeness, engineering_depth, example_quality, question_alignment.
- Score all five voice dimensions: fluency, filler_words, redundancy, spoken_clarity, answer_pacing.
- Use 0-10 scale for each dimension.
- Provide a brief explanation for each score.

## Content Assessment
- Compare the answer against the rubric in the triggered Skill reference.
- Identify specific strengths and weaknesses.
- Do NOT give full marks unless the answer is truly exceptional.
- A short answer (under 100 characters) should flag "insufficient content" and score low on structure_completeness and engineering_depth.
- An off-topic answer should score low on question_alignment.

## Voice Assessment
- When only text is available, mark scores with an asterisk (*) to indicate text-based estimation.
- Count filler words explicitly when possible.
- Redundancy detection: flag repeated phrases, circular reasoning, over-explanation.

## Report Structure
- Follow the output contract from the triggered Skill's references.
- Must include both content and voice dimension tables.
- Must include improvement advice.
- Must include reference answer and likely follow-up questions.

## Constraints
- Preserve the original question verbatim.
- Do not replace the question with extracted keywords.
- If question or answer is missing, state "信息不足，无法诊断".
