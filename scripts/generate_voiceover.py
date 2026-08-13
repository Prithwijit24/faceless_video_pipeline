"""
Step 2: Generate Hindi voiceover audio per scene using edge-tts (free, no API key).

Splits each scene into sentences, synthesizes each separately, and stitches
them back together with short real-silence gaps for natural pacing. The
script's 5-second "hook" line is synthesized first and prepended to the full
track. Finally the whole voiceover gets a clarity EQ pass (cut rumble, boost
bass for warmth, boost treble for crispness).

Reads: output/script.json, config/channels.yaml
Writes:
  output/audio/scene_0.mp3, scene_1.mp3, ...  (includes trailing pause)
  output/audio/hook.mp3      (the 5-second hook line, when script has one)
  output/audio/full.mp3      (hook + all scenes, EQ'd - consumed by subtitles,
                              music mixing, and final assembly)
  output/audio/durations.json    (scene durations, includes trailing pause -
                                   consumed as-is by fetch_visuals.py /
                                   assemble_video.py; the hook is NOT listed
                                   here, assemble_video probes hook.mp3 itself)

Optional env vars:
  TTS_VOICE   - override the channel's edge-tts voice
  TTS_RATE    - override the channel's speaking rate (e.g. "-4%")
  TTS_PITCH   - override the channel's pitch shift (e.g. "-12Hz")
  TTS_RETRIES - max attempts per sentence when edge-tts drops a synthesis
                (default 3; the service is free and transiently flaky)
"""
import env  # loads .env from the project root via python-dotenv (no-op if missing)

import os
import re
import json
import yaml
import asyncio
import subprocess

import edge_tts

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(ROOT, "output")
AUDIO_DIR = os.path.join(OUTPUT_DIR, "audio")
TMP_DIR = os.path.join(AUDIO_DIR, "tmp")
CONFIG_PATH = os.path.join(ROOT, "config", "channels.yaml")

VOICE_OVERRIDE = os.environ.get("TTS_VOICE")
RATE_OVERRIDE = os.environ.get("TTS_RATE")
PITCH_OVERRIDE = os.environ.get("TTS_PITCH")
try:
    TTS_RETRIES = max(1, int(os.environ.get("TTS_RETRIES", "3")))
except ValueError:
    TTS_RETRIES = 3  # malformed env value - keep the default


def ffprobe_duration(path):
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", path,
        ],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


# Periods that are NOT sentence ends: decimals (3.5), common abbreviations
# (Dr., Mr., Mrs., Ms., Prof., Sr., Jr., St., etc., approx., vs.), and
# dotted acronyms (U.S., U.K.). They are hidden behind a placeholder before
# splitting so the sentence splitter ignores them, then restored afterwards.
# ("No." counts only when followed by a digit, e.g. "No. 5" - a standalone
# "No." is a real sentence end and still splits.)
# Known trade-off: a sentence that genuinely ends in a protected token (e.g.
# "I live in the U.S. It's great.") stays merged with the next sentence.
# Rare in short narration, accepted for the abbreviation/decimal handling.
_SENTENCE_PUNCT = re.compile(r"(?<=[.!?])\s+")
_NON_SENTENCE_DOT = re.compile(
    r"\b\d+\.\d+\b"
    r"|\b(?:Dr|Mr|Mrs|Ms|Prof|Sr|Jr|St|etc|approx|vs)\.(?=\s|$)"
    r"|\bNo\.(?=\s*\d)"
    r"|\b(?:[A-Za-z]\.){2,}(?=\s|$)",
    re.IGNORECASE,
)
_DOT_HOLDER = "\x00"  # sentinel unlikely to appear in narration text


def split_sentences(text):
    text = text.strip()
    hidden = _NON_SENTENCE_DOT.sub(lambda m: m.group(0).replace(".", _DOT_HOLDER), text)
    parts = _SENTENCE_PUNCT.split(hidden)
    parts = [p.replace(_DOT_HOLDER, ".").strip() for p in parts if p.strip()]
    return parts or [text]


async def synth(text, voice, rate, pitch, out_path):
    """Synthesize one sentence with edge-tts. edge-tts is a free service and
    occasionally drops a synthesis (NoAudioReceived / connection resets) -
    retry with a short backoff instead of failing the whole video over one
    sentence. Tune with TTS_RETRIES."""
    last_exc = None
    for attempt in range(1, TTS_RETRIES + 1):
        try:
            communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
            await communicate.save(out_path)
            return
        except Exception as exc:
            last_exc = exc
            if attempt < TTS_RETRIES:
                wait = 2 * attempt
                print(f"[generate_voiceover] edge-tts attempt {attempt}/{TTS_RETRIES} "
                      f"failed ({type(exc).__name__}: {exc}) - retrying in {wait}s")
                await asyncio.sleep(wait)
    raise last_exc


def make_silence(path, duration_ms):
    duration_sec = max(duration_ms, 1) / 1000.0
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
         "-t", str(duration_sec), "-q:a", "9", "-acodec", "libmp3lame", path],
        check=True, capture_output=True,
    )


def concat_audio(paths, out_path):
    list_file = os.path.join(TMP_DIR, f"concat_{os.path.basename(out_path)}.txt")
    with open(list_file, "w") as f:
        for p in paths:
            f.write(f"file '{os.path.abspath(p)}'\n")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file,
         "-c", "copy", out_path],
        check=True, capture_output=True,
    )


def main():
    with open(os.path.join(OUTPUT_DIR, "script.json")) as f:
        script = json.load(f)
    with open(CONFIG_PATH) as f:
        cfg = yaml.safe_load(f)

    channel = next((c for c in cfg["channels"] if c["id"] == script["channel_id"]), None)
    if channel is None:
        raise SystemExit(
            f"channel_id '{script['channel_id']}' not found in channels.yaml "
            "- run generate_script.py first or add the channel to config/channels.yaml"
        )
    voice = VOICE_OVERRIDE or channel["voice"]
    rate = RATE_OVERRIDE or channel.get("rate") or cfg["video"].get("default_rate", "-5%")
    pitch = PITCH_OVERRIDE or channel.get("pitch") or cfg["video"].get("default_pitch", "+10Hz")

    # Per-channel pause overrides fall back to the global video: block defaults.
    sentence_pause_ms = channel.get("sentence_pause_ms") or cfg["video"].get("sentence_pause_ms", 150)
    scene_pause_ms = channel.get("scene_pause_ms") or cfg["video"].get("scene_pause_ms", 250)

    os.makedirs(AUDIO_DIR, exist_ok=True)
    os.makedirs(TMP_DIR, exist_ok=True)

    sentence_silence = os.path.join(TMP_DIR, "sentence_silence.mp3")
    scene_silence = os.path.join(TMP_DIR, "scene_silence.mp3")
    make_silence(sentence_silence, sentence_pause_ms)
    make_silence(scene_silence, scene_pause_ms)

    durations = []
    scene_paths = []
    scenes = script["scenes"]

    for i, scene in enumerate(scenes):
        sentences = split_sentences(scene["text"])
        pieces = []
        for j, sentence in enumerate(sentences):
            sent_path = os.path.join(TMP_DIR, f"scene_{i}_sent_{j}.mp3")
            asyncio.run(synth(sentence, voice, rate, pitch, sent_path))
            pieces.append(sent_path)
            if j < len(sentences) - 1:
                pieces.append(sentence_silence)

        if i < len(scenes) - 1:
            pieces.append(scene_silence)

        scene_out = os.path.join(AUDIO_DIR, f"scene_{i}.mp3")
        concat_audio(pieces, scene_out)
        dur = ffprobe_duration(scene_out)
        durations.append(dur)
        scene_paths.append(scene_out)
        print(f"[generate_voiceover] scene {i}: {len(sentences)} sentence(s), {dur:.2f}s")

    # 5-second hook: synthesized first, prepended to the full track.
    hook_path = os.path.join(AUDIO_DIR, "hook.mp3")
    hook_text = (script.get("hook") or "").strip()
    all_pieces = list(scene_paths)
    if hook_text:
        asyncio.run(synth(hook_text, voice, rate, pitch, hook_path))
        hook_dur = ffprobe_duration(hook_path)
        all_pieces = [hook_path] + all_pieces
        print(f"[generate_voiceover] hook ({hook_dur:.2f}s): {hook_text}")
    else:
        if os.path.exists(hook_path):
            os.remove(hook_path)
        print("[generate_voiceover] no hook in script - skipping")

    # Raw concatenation, then a clarity EQ pass on the final voiceover:
    #   highpass=70  - cut sub-bass rumble that muddies speech
    #   bass=g=3     - add warmth/body ("high base") for a fuller voice
    #   treble=g=1.5 - add crispness so every word is clear
    raw_full = os.path.join(TMP_DIR, "full_raw.mp3")
    full_path = os.path.join(AUDIO_DIR, "full.mp3")
    concat_audio(all_pieces, raw_full)
    subprocess.run(
        ["ffmpeg", "-y", "-i", raw_full,
         "-af", "highpass=f=70,bass=g=3,treble=g=1.5",
         "-q:a", "2", "-acodec", "libmp3lame", full_path],
        check=True, capture_output=True,
    )
    os.remove(raw_full)

    with open(os.path.join(AUDIO_DIR, "durations.json"), "w") as f:
        json.dump(durations, f, indent=2)

    full_dur = ffprobe_duration(full_path)
    print(f"[generate_voiceover] voice={voice} rate={rate} pitch={pitch} "
          f"full={full_dur:.2f}s done")


if __name__ == "__main__":
    main()
