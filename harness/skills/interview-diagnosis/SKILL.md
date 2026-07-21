---
name: interview-diagnosis
description: Diagnose AI Agent / LLM engineering interview answers when the user provides an interview question and candidate answer, including content scoring, transcript-based voice scoring, improvement advice, reference answer, and follow-up questions.
---

# Interview Diagnosis

Use this skill to diagnose one interview question and one candidate answer.

## Workflow

1. Identify the interview question and candidate answer from user input.
2. Call `search_knowledge` for relevant interview knowledge topics.
3. Call `score_answer` for content scoring on five dimensions.
4. Call `analyze_voice_text` for transcript-based voice scoring.
5. Call `generate_followup` for likely follow-up questions.
6. Produce the final Markdown report using `references/output-contract.md`.

## Constraints

- Preserve the original question exactly as given.
- Do not invent knowledge sources.
- Do not replace the question with keywords extracted from the answer.
- If the question or answer is missing, state that information is insufficient.
- Output in Chinese. Keep the report under 2500 characters.

## References

- Use `references/rubric.md` for scoring criteria and dimension definitions.
- Use `references/output-contract.md` for the final report Markdown format.
