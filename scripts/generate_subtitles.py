"""
Step 5: Transcribe the full voiceover into timed subtitles.

Uses faster-whisper, running fully locally/offline on CPU (free, no API key).
The 'small' model (default) is a good speed/accuracy tradeoff for Hindi on a
GitHub Actions runner; the language is forced to Hindi so the transcription
and timing are tuned for Devanagari output.

Reads: output/audio/full.mp3
Writes: output/subtitles.srt

Optional env vars:
  WHISPER_MODEL    - model size, default "small" (base is weak on Hindi)
  WHISPER_LANGUAGE - transcription language, default "hi" (set empty to
                     auto-detect)
"""
import env  # loads .env from the project root via python-dotenv (no-op if missing)

import os
from faster_whisper import WhisperModel

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(ROOT, "output")

MODEL_SIZE = os.environ.get("WHISPER_MODEL", "small")
LANGUAGE = os.environ.get("WHISPER_LANGUAGE", "en") or None


def format_timestamp(seconds):
    ms = int((seconds - int(seconds)) * 1000)
    s = int(seconds) % 60
    m = (int(seconds) // 60) % 60
    h = int(seconds) // 3600
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def main():
    audio_path = os.path.join(OUTPUT_DIR, "audio", "full.mp3")
    model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")

    segments, info = model.transcribe(
        audio_path,
        language=LANGUAGE,
        word_timestamps=False,
        vad_filter=True,
    )

    srt_path = os.path.join(OUTPUT_DIR, "subtitles.srt")
    with open(srt_path, "w") as f:
        for i, seg in enumerate(segments, start=1):
            f.write(f"{i}\n")
            f.write(f"{format_timestamp(seg.start)} --> {format_timestamp(seg.end)}\n")
            f.write(f"{seg.text.strip()}\n\n")

    lang = info.language if hasattr(info, "language") else LANGUAGE
    print(f"[generate_subtitles] model={MODEL_SIZE} language={lang} wrote {srt_path}")


if __name__ == "__main__":
    main()
