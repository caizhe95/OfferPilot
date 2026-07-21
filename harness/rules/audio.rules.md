# Audio Diagnosis Rules

## Audio Processing
- Audio files must be in wav or mp3 format.
- ASR transcription requires user approval (medium risk tool).
- On ASR denial, prompt user to paste a transcript manually.
- Save the transcript to the session.

## Transcript Voice Scoring
- Score voice dimensions from transcript text patterns.
- Mark all voice scores with (*) when based on text analysis.
- Look for:
  - Chinese filler words: 嗯, 呃, 啊, 就是, 那个, 然后(overuse), 对吧
  - Sentence completion patterns
  - Topic transition quality
  - Repetition density

## Audio-Specific (Phase 12+)
- When real audio is available, use acoustic features:
  - Speech rate (words per minute)
  - Pause duration and frequency
  - Silence ratio
  - Pitch variation
- Combine text-based and acoustic scores for final voice dimension.

## Fallback
- If ASR fails, allow manual transcript input.
- If transcript is too short (under 50 characters), flag as insufficient.
