---
name: audio-diagnosis
description: Diagnose interview answers from audio uploads or transcripts when the user provides an audio file or a transcript text. Analyzes both content dimensions and voice dimensions from transcript text, with optional ASR integration.
---

# Audio Diagnosis

Use this skill to diagnose interview answers from audio uploads or transcript text.

## Workflow

1. If an audio file is provided, call `transcribe_audio` for ASR (requires permission).
2. If ASR fails or is denied, prompt user to manually paste a transcript.
3. Once transcript is obtained, proceed with diagnosis:
   - Call `search_knowledge` for relevant knowledge.
   - Call `score_answer` for content scoring.
   - Call `analyze_voice_text` for transcript-based voice scoring.
   - Call `generate_followup` for follow-up questions.
4. Produce the final Markdown report using `references/transcript-voice-rubric.md`.

## Constraints

- ASR transcription must go through permission gate (medium risk).
- On ASR denial, fall back to manual transcript input.
- Voice dimensions are scored from transcript text patterns (filler words, redundancy, clarity).
- Output in Chinese.

## References

- Use `references/transcript-voice-rubric.md` for voice scoring criteria from transcripts.
- Use `interview-diagnosis/references/output-contract.md` for the report format.
