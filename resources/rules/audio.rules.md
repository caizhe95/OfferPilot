# Audio Diagnosis Rules

## Audio Processing
- Audio files must be in wav or mp3 format.
- ASR transcription requires user approval (medium risk tool).
- On ASR denial, prompt user to paste a transcript manually.
- Save the transcript to the session.

## Transcript Expression Scoring
- 语音维度只依据 ASR 转写文本和程序化文本特征，不代表声学评测。
- 可观察：
  - Chinese filler words: 嗯, 呃, 啊, 就是, 那个, 然后(overuse), 对吧
  - Sentence completion patterns
  - Topic transition quality
  - Repetition density

## Fallback
- If ASR fails, allow manual transcript input.
- If transcript is too short (under 50 characters), flag as insufficient.
