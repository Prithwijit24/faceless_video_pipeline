"""
Step 3: Fetch a channel-appropriate background music bed from Freesound
(freesound.org) and prepare it to exactly match the voiceover duration with
fade in/out. Only Creative Commons 0 tracks are used, so no attribution is
required and the audio can be used commercially.

The bed is mixed under the voiceover later by assemble_video.py with
sidechain ducking (music automatically lowers while the narrator speaks), so
it adds atmosphere without making the story unclear.

Skipped gracefully when FREESOUND_API_TOKEN is not set, the search returns
nothing, or the download fails - the video then simply has voiceover only.

Get a free token: create an account at freesound.org, then
https://freesound.org/apiv2/apply/ to register an app and get a token.

Reads: output/script.json, output/audio/full.mp3, config/channels.yaml
Writes: output/music/source.mp3 (raw download, for inspection)
        output/music/bed.mp3    (looped+faded, duration == voiceover duration)

Optional env vars:
  FREESOUND_API_TOKEN - API token (required for music)
  FREESOUND_QUERY     - override the channel's music_query search terms
"""
import env  # loads .env from the project root via python-dotenv (no-op if missing)

import os
import json
import yaml
import subprocess
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(ROOT, "output")
AUDIO_DIR = os.path.join(OUTPUT_DIR, "audio")
MUSIC_DIR = os.path.join(OUTPUT_DIR, "music")
CONFIG_PATH = os.path.join(ROOT, "config", "channels.yaml")

FREESOUND_API = "https://freesound.org/apiv2"
# Accept both spellings: the docs call it a "token", most people name the
# env var API_KEY. Either works.
FREESOUND_API_TOKEN = os.environ.get("FREESOUND_API_TOKEN") or os.environ.get("FREESOUND_API_KEY")
QUERY_OVERRIDE = os.environ.get("FREESOUND_QUERY")


def ffprobe_duration(path):
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", path,
        ],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


def search_track(query, min_duration):
    """Search CC0 tracks at least as long as min_duration. Returns the first
    hit dict or None."""
    params = {
        "token": FREESOUND_API_TOKEN,
        "query": query,
        "filter": f'license:"Creative Commons 0" duration:[{int(min_duration)} TO 180]',
        "fields": "id,name,duration,previews",
        "page_size": 5,
        "sort": "score",
    }
    r = requests.get(f"{FREESOUND_API}/search/", params=params, timeout=30)
    r.raise_for_status()
    hits = r.json().get("results", [])
    return hits[0] if hits else None


def download_track(hit, out_path):
    # Freesound's full-resolution /download/ endpoint needs OAuth2 - a plain
    # API token always gets a 401 there. The public preview stream works with
    # no auth, and since the bed is looped to the voiceover length and mixed
    # quietly under the narration, HQ preview quality is more than enough.
    url = hit["previews"]["preview-hq-mp3"]
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    with open(out_path, "wb") as f:
        f.write(r.content)


def main():
    if not FREESOUND_API_TOKEN:
        print("[generate_music] FREESOUND_API_TOKEN / FREESOUND_API_KEY not set - skipping background music")
        return

    with open(os.path.join(OUTPUT_DIR, "script.json")) as f:
        script = json.load(f)
    with open(CONFIG_PATH) as f:
        cfg = yaml.safe_load(f)

    channel = next((c for c in cfg["channels"] if c["id"] == script["channel_id"]), {})
    query = QUERY_OVERRIDE or channel.get("music_query") or "ambient"

    full_path = os.path.join(AUDIO_DIR, "full.mp3")
    if not os.path.exists(full_path):
        print("[generate_music] output/audio/full.mp3 missing - run generate_voiceover first")
        return
    full_dur = ffprobe_duration(full_path)

    os.makedirs(MUSIC_DIR, exist_ok=True)
    try:
        hit = search_track(query, min(full_dur, 30))
        if not hit:
            print(f"[generate_music] no CC0 tracks found for query '{query}' - skipping music")
            return
        print(f"[generate_music] found '{hit.get('name')}' ({hit.get('duration')}s) for '{query}'")
        # Sanity-check the downloaded file really is audio (not a JSON error).
        if hit.get("previews"):
            print(f"[generate_music] using CC0 preview stream (full download needs OAuth2)")
        source_path = os.path.join(MUSIC_DIR, "source.mp3")
        download_track(hit, source_path)
    except requests.exceptions.RequestException as e:
        status = e.response.status_code if e.response is not None else "?"
        print(f"[generate_music] freesound request failed (HTTP {status}) - skipping music")
        return
    except Exception as e:
        print(f"[generate_music] download failed ({type(e).__name__}) - skipping music")
        return

    # Loop the track to fill the voiceover exactly, then fade in/out so the
    # bed never clicks in or out.
    fade_out_start = max(0.0, full_dur - 2.5)
    bed_path = os.path.join(MUSIC_DIR, "bed.mp3")
    subprocess.run(
        ["ffmpeg", "-y",
         "-stream_loop", "-1", "-i", source_path,
         "-t", str(full_dur),
         "-af", f"afade=t=in:st=0:d=1.5,afade=t=out:st={fade_out_start:.2f}:d=2.5",
         "-ar", "44100", "-ac", "2",
         "-c:a", "libmp3lame", "-q:a", "2",
         bed_path],
        check=True, capture_output=True,
    )
    bed_dur = ffprobe_duration(bed_path)
    print(f"[generate_music] wrote {bed_path} ({bed_dur:.2f}s == voiceover)")


if __name__ == "__main__":
    main()
