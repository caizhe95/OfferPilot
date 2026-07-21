---
name: followup-coaching
description: Generate likely follow-up questions and answer strategies based on a candidate's weak points when the user asks about potential follow-up questions or what the interviewer might ask next.
---

# Followup Coaching

Use this skill to generate likely follow-up questions and answer strategies.

## Workflow

1. Review the candidate's original answer and diagnosis results.
2. Identify weak dimensions from content and voice scoring.
3. Generate 3-5 likely follow-up questions that an interviewer would ask.
4. For each follow-up, provide:
   - Why the interviewer would ask this
   - A recommended answer strategy
   - Key points to include
5. Call `search_knowledge` if needed for domain-specific follow-ups.

## Constraints

- Follow-up questions should be realistic and based on actual weak points.
- Do not generate questions unrelated to the original topic.
- Output in Chinese.

## References

- Use `references/followup-patterns.md` for common interviewer follow-up patterns.
