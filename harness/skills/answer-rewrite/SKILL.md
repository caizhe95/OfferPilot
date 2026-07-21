---
name: answer-rewrite
description: Rewrite and optimize a candidate interview answer when the user asks to optimize, rewrite, or improve their answer. Produces both a spoken interview version and a formal written version.
---

# Answer Rewrite

Use this skill to rewrite and optimize a candidate's interview answer.

## Workflow

1. Identify the original interview question and candidate answer.
2. Call `search_knowledge` for relevant interview knowledge to inform the rewrite.
3. Analyze the original answer's weaknesses.
4. Produce two optimized versions:
   - **Interview spoken version**: Natural, conversational tone for verbal delivery
   - **Formal written version**: Polished prose for written submission
5. Include a brief comparison highlighting what was improved.

## Constraints

- Preserve the original meaning and key points.
- Do not add fabricated information.
- The spoken version should sound natural when read aloud.
- Output in Chinese.

## References

- Use `references/rewrite-patterns.md` for common rewrite patterns and techniques.
