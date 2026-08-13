# Faceless Reel Pipeline (free, plug-and-play, 4 channels x 2 videos/day)

Automatically writes **Hindi** scripts (with a punchy 5-second hook and a
complete beginning/middle/end), generates clear Hindi voiceovers, adds
situational background music that ducks under the voice, sources matching
background footage, burns in Devanagari subtitles, and sends the finished
videos straight to you on Telegram — for 4 channels, 2 videos each per day
(8 total), fully on autopilot, for $0. You download from Telegram and post
wherever you like.

## What it uses (all free)
| Step | Tool | Cost |
|---|---|---|
| Script (Hindi, hook + full arc) | AGNES API (agnes-2.0-flash, thinking mode) | Free |
| Voiceover (Hindi, pitch/EQ tuned) | edge-tts | Free, no key needed |
| Background music (situational, ducked) | Freesound (CC0-only, no attribution) | Free token |
| Backgrounds | AI images: Pollinations.ai (no key), else AI Horde (free community API), else Pexels stock | Free |
| Subtitles (Hindi, Devanagari) | faster-whisper (runs locally) | Free |
| Assembly | ffmpeg | Free |
| Scheduling | GitHub Actions | Free (2,000 min/mo private repos) |
| Delivery | Telegram Bot API | Free |

## How the 8-videos-a-day setup works
`config/channels.yaml` defines 4 channels (mythology, horror, space,
psychology by default - rename/edit freely). The GitHub Actions workflow
uses a **matrix** of `channel x video_index (1,2)`, so it spins up 8
independent jobs each run: `mythology-1`, `mythology-2`, `horror-1`, ...
Each job writes a script, voiceover, visuals, subtitles, assembles the
video, and sends it to you on Telegram.

---

## Test it locally first (recommended before touching GitHub Actions)

### 1. Install prerequisites
```bash
cd faceless-pipeline
pip install -r requirements.txt
```
You also need **ffmpeg** installed on your system:
- macOS: `brew install ffmpeg`
- Ubuntu/Debian/WSL: `sudo apt-get update && sudo apt-get install -y ffmpeg`
- Windows: install via https://ffmpeg.org/download.html and add it to PATH

Verify it's on your PATH:
```bash
ffmpeg -version
```

### 2. Get your free API keys
- **AGNES** (script generation): https://apihub.agnes-ai.com — free key,
  model `agnes-2.0-flash` with thinking mode on (the story gets planned
  before it's written, so videos have a proper beginning/middle/end). A Groq
  key (https://console.groq.com/keys) still works as an automatic fallback.
- **AI Horde** (AI background images, *optional*): https://aihorde.net/register — free community
  API key. Images are rendered by volunteer workers, so queue time varies (usually seconds to a
  few minutes, occasionally ~10 min at peak). Skip it and the pipeline uses Pollinations.ai, which
  needs no key at all.
- **Pexels** (stock footage, *optional fallback*): https://www.pexels.com/api/
- **Freesound** (background music, *optional*): https://freesound.org/apiv2/apply/ — create a
  free account, register an app, copy the token. Only **CC0** tracks are used, so no attribution
  is needed and the music is safe for YouTube. Skip it and videos are made without background
  music (voiceover only).

Backgrounds are generated per scene from the scene's actual narration (so
they match the story): Pollinations.ai first (fast, no key needed), then AI
Horde for any scene Pollinations couldn't do, then Pexels stock video/photo
as a last resort. Only the scenes Pollinations missed get Horde requests;
those are submitted at once and polled together, so one queue wait covers
them, and any still queued after `HORDE_TIMEOUT` (default 600s) fall back to
Pexels. If you skip both AI keys the pipeline still works — it just uses
Pollinations + Pexels.

### 3. (Optional but recommended) Set up the Telegram bot
1. Message **@BotFather** on Telegram, send `/newbot`, copy the **bot token**
2. Message your new bot once (e.g. "hi") so it can reply to you
3. Visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` and find your
   **chat id** in the JSON (`"chat":{"id": ...}`)

If you skip this, the pipeline still runs fine locally — `notify.py` just
prints the caption instead of sending anything.

### 4. Set your environment variables
Easiest: copy the template and fill it in (the pipeline and every step
script load `.env` automatically - no exporting needed):
```bash
cp .env.example .env
# edit .env with your keys
```

The loader resolves `.env` from the **project root** (`faceless-pipeline/.env`)
even when you run a script from another directory. For production/CI, do not
copy `.env`; inject the same variables through the process environment or the
CI secret store. `ENV_FILE=/absolute/path/to/.env` can be used for an explicit
override.
Or keep exporting them in your shell (exported vars always win over `.env`):
```bash
export AGNES_API_KEY="your-agnes-key"   # script LLM (Groq key works as fallback)
export PEXELS_API_KEY="your-pexels-key"
export CHANNEL_ID="mythology"          # must match an id in config/channels.yaml
export VIDEO_INDEX="1"                 # just a label, used in filenames/logs

# optional - AI background images via AI Horde (skip it and the pipeline
# uses Pollinations.ai, which needs no key at all)
export HORDE_IMAGE_API_KEY="your-horde-key"

# optional - background music (Freesound CC0). Get a token at
# freesound.org/apiv2/apply/. Without it, videos have no background music.
export FREESOUND_API_TOKEN="your-freesound-token"

# optional - only needed if you want the Telegram send to actually fire
export TELEGRAM_BOT_TOKEN="your-bot-token"
export TELEGRAM_CHAT_ID="your-chat-id"

# optional - give each channel its own Telegram bot for LOCAL runs. If set,
# the channel's video is sent by that bot; otherwise TELEGRAM_BOT_TOKEN is
# used for all. (message each bot once in Telegram first, or it can't reach
# your chat; in GitHub Actions the shared TELEGRAM_BOT_TOKEN secret is used)
export MYTHOLOGY_TELEGRAM_BOT_TOKEN="your-bot-token"
export HORROR_TELEGRAM_BOT_TOKEN="your-bot-token"
export SPACE_TELEGRAM_BOT_TOKEN="your-bot-token"
export PSYCHOLOGY_TELEGRAM_BOT_TOKEN="your-bot-token"

# optional - point at any OpenAI-compatible endpoint instead of AGNES:
# export LLM_BASE_URL="https://api.groq.com/openai/v1"
# export LLM_MODEL="llama-3.3-70b-versatile"
# export LLM_THINKING="0"   # disable thinking mode if the model lacks it

# optional - dedup tuning: max regenerate attempts when the LLM repeats a story
export SCRIPT_RETRIES="3"

# optional - voiceover/subtitle tuning (defaults are already good for Hindi)
# export TTS_PITCH="+10Hz"     # override the channel's edge-tts pitch shift
# export TTS_RETRIES="3"       # edge-tts attempts per sentence before giving up
#                              # (the free service is transiently flaky - it
#                              # retries automatically with a short backoff)
# export WHISPER_MODEL="small" # subtitle model: base (fast) / small (accurate)
# export WHISPER_LANGUAGE="hi" # set empty to auto-detect

# optional - visual safety wall tuning
# export VISUAL_SAFETY_THRESHOLD="0.40" # strict nudity classes (genitalia,
#                                        # breast, buttocks, anus)
# export VISUAL_SOFT_THRESHOLD="0.60"   # noisy belly/armpit classes (raises
#                                        # the bar to cut false positives)
```
(Windows PowerShell: use `$env:AGNES_API_KEY="..."` instead of `export`.)

### 5. Run the full pipeline
```bash
python scripts/pipeline.py
```
This runs all seven steps in order and prints progress for each one. In
the terminal it's **interactive**: before each step it checks that step's
prerequisites (env vars, input files, ffmpeg), shows the result, then asks
whether to run it:
```
--- generate_script.py: NOT READY (2 problems)
    x no LLM key - set LLM_API_KEY / AGNES_API_KEY / GROQ_API_KEY
    x CHANNEL_ID not set (e.g. export CHANNEL_ID=mythology)
Run generate_script.py? [y/N/q]
```
Press Enter or `y` to run it, `n` to skip it, `q` to stop the pipeline.
When a step is missing required prerequisites the default flips to `n`
(shown as `[y/N/q]`), so you won't accidentally run something that's
guaranteed to fail. Want a report of every step's readiness without
running anything?
```bash
python scripts/pipeline.py --check
```
(When stdin isn't a terminal — e.g. GitHub Actions — it runs everything
without prompting. Use `python scripts/pipeline.py --yes` to force that
on a terminal too, and `--list` to print the steps.)

If everything's set up right, you'll see:
```
=== Pipeline complete: output/final.mp4 sent to Telegram ===
```
and the video shows up in your Telegram chat within a few seconds. The
file is also sitting locally at `output/final.mp4` if you want to check it
before it's even sent.

### 6. Run just one step at a time (for debugging)
Handy if something fails partway through - you don't have to redo the
earlier (slower) steps while fixing one:
```bash
python scripts/generate_script.py       # -> output/script.json
python scripts/generate_voiceover.py    # -> output/audio/*.mp3 (incl. hook.mp3)
python scripts/generate_music.py        # -> output/music/bed.mp3 (Freesound, optional)
python scripts/fetch_visuals.py         # -> output/visuals/* (AI images, Pexels fallback)
python scripts/generate_subtitles.py    # -> output/subtitles.srt
python scripts/assemble_video.py        # -> output/final.mp4 (hook + music + captions)
python scripts/notify.py                # sends output/final.mp4 to Telegram
```
Each step reads whatever the previous ones already wrote to `output/`, so
you can re-run just the one that broke after fixing it.

### 7. Try a different channel
```bash
export CHANNEL_ID="horror"
python scripts/pipeline.py
```

Once a couple of these look good, you're ready to push to GitHub and let
Actions handle it automatically.

---

## Setup on GitHub Actions (once local testing looks good)

### 1. Push this folder to a new GitHub repo (private is fine)

### 2. Add secrets
Repo → Settings → Secrets and variables → Actions → New repository secret:
- `LLM_API_KEY` (AGNES key; falls back to `GROQ_API_KEY` if that's all you have)
- `HORDE_IMAGE_API_KEY` (optional - AI background images; without it the pipeline uses Pollinations.ai)
- `PEXELS_API_KEY` (optional fallback for when AI generation fails)
- `FREESOUND_API_TOKEN` (optional - background music; without it videos are voiceover-only)
- `TELEGRAM_BOT_TOKEN` (shared bot, the fallback for every channel)
- `TELEGRAM_CHAT_ID`
- (optional, per-channel bots) `MYTHOLOGY_TELEGRAM_BOT_TOKEN`,
  `HORROR_TELEGRAM_BOT_TOKEN`, `SPACE_TELEGRAM_BOT_TOKEN`,
  `PSYCHOLOGY_TELEGRAM_BOT_TOKEN` — add one per bot you create; until you
  add these secrets, every channel silently uses `TELEGRAM_BOT_TOKEN`

### 3. Enable Actions
Actions tab → enable workflows. It now runs daily at 08:00 UTC (edit the
`cron:` line in `.github/workflows/generate-video.yml` to change that), and
you can trigger it manually anytime via Actions → "Generate Faceless Reels"
→ "Run workflow" (optionally typing one channel id to run just that channel).

## Customizing
- **Channels**: edit `config/channels.yaml` — each has its own `prompt`
  (topic angle), `voice` (an edge-tts voice; all channels default to Hindi:
  `hi-IN-MadhurNeural` male / `hi-IN-SwaraNeural` female), `rate` and `pitch`
  (voice speed + pitch shift), short `sentence_pause_ms` / `scene_pause_ms`
  (silence gaps), `music_query` (Freesound search terms for that channel's
  background mood), `visual_style` (style suffix for the AI background
  images, so all scenes share one look), and optional `telegram_chat_id` if
  you want a channel's videos in a separate chat/group. Add a 5th channel?
  Also add it to the `matrix.channel` list in the workflow file.
- **5-second hook**: the script's `hook` line is spoken first and shown big
  on screen for the opening seconds (see `build_hook_ass` in
  `scripts/assemble_video.py`). Edit the prompt in `scripts/generate_script.py`
  to change what makes a good hook.
- **Audio loudness**: the narrator is compressed and boosted before the final
  `-14 LUFS` loudness pass. `voice_volume` defaults to `1.8` and
  `music_volume` defaults to `0.12`, with sidechain ducking so music stays
  behind the narration. Override with `VOICE_VOLUME` / `TARGET_LUFS` when needed.
- **Videos per day**: change `matrix.video_index` in the workflow (e.g.
  `[1, 2, 3]` for 3/day/channel).
- **Video length**: `target_words` in `channels.yaml` (~150 words ≈ 50s spoken).
- **Schedule/time**: `cron:` in the workflow (UTC).
- **Subtitle style**: `force_style` in `scripts/assemble_video.py`.
- **Parallelism**: `max-parallel` in the workflow — controls how many of
  the 8 jobs run at once (kept low by default to avoid free-tier rate limits).

## Family-safe content (YouTube-ready)

The pipeline uses a **fail-closed safety wall**. The goal is not merely to ask
an image model for "safe" content; every asset must pass a local check before
it can reach the final video.

- **Scripts**: the LLM prompt forbids profanity, vulgarity, sexual content,
  nudity, graphic violence/gore, drugs, and hateful content. The generated
  title, hook, narration, and visual keywords are then screened by
  `scripts/safety.py`. Unsafe output is regenerated; if retries are exhausted,
  the job stops. The text safety wall is always enabled — there is no
  `CONTENT_FILTER=0` bypass.
- **Visual generation**: prompts explicitly prefer environments, objects,
  landscapes, architecture, artifacts, astronomy, and abstract visuals with
  no people, faces, bodies, or exposed skin. Pollinations and AI Horde also
  receive their provider-level SFW controls.
- **Local visual screening**: `scripts/image_safety.py` uses NudeNet to inspect
  every AI image and every Pexels fallback. Exposed breasts/genitals/buttocks/
  anus are rejected outright. Belly/armpits (NudeNet's noisiest classes, which
  false-positive on midriffs, clothing folds, shadows, and silhouettes) are only
  rejected above a higher bar (`VISUAL_SOFT_THRESHOLD`, default 0.60) so one
  noisy detection can't silently kill a whole video. By default, detected
  human faces are also rejected, which gives the faceless pipeline a much
  stronger safety margin.
- **Video screening**: Pexels video fallbacks are sampled across their duration
  and every sampled frame must pass. `assemble_video.py` then screens the
  assembled video and the final `output/final.mp4` again before declaring it
  publishable.
- **Fail closed**: if NudeNet is missing, cannot initialize, or a video frame
  cannot be extracted for screening, the pipeline stops. It never silently
  publishes an unscreened visual.
- **Visual retries**: rejected AI images are deleted and regenerated with
  different seeds (`VISUAL_RETRIES`, default `3`). If no candidate passes,
  the job fails instead of shipping an unsafe video.

## Audio loudness

The narrator is compressed and boosted before the final loudness pass. The
default target is `-14 LUFS`, with `voice_volume: 1.8` and a quieter
`music_volume: 0.12`. The music is also sidechain-ducked under the narration.
Use `VOICE_VOLUME` or `TARGET_LUFS` only when you have a reason to tune them.

## Known limits of the free-tier version
- AGNES and Pexels free tiers have rate limits — 8 videos/day spread across
  jobs is generally fine; watch for 429s if you push parallelism higher.
- Telegram bot uploads cap around 50MB — comfortably covers a 45-75s reel;
  bigger files fall back to a text alert (grab the file from the GitHub
  Actions run artifacts in that case - each job uploads one automatically).
- `faster-whisper`'s `small` model (default) is accurate enough for Hindi on
  CPU; the first CI run downloads it (~460MB) which adds a minute or two. Set
  `WHISPER_MODEL=base` for faster but less accurate captions.
- Pexels stock footage is only used as a fallback now (when AI generation
  fails or rate-limits). It's keyword-matched, not custom-shot, so thematic
  fit varies — AI images, prompted from the actual narration, match far better.
- AI Horde is a shared volunteer queue — wait time varies with demand. It's
  only used when Pollinations fails, and scenes still queued after
  `HORDE_TIMEOUT` seconds automatically fall back to Pexels, so a slow queue
  never blocks the pipeline.
- Horde images cost a small amount of **kudos** (a free daily allowance on
  your account). When the day's kudos run out, submits are rejected and those
  scenes automatically fall back to Pexels - the pipeline always completes
  either way.
- Story dedup is built in: each generated script is checked against the
  channel's story history (`history/<channel>.json`) and regenerated (up to
  `SCRIPT_RETRIES`, default 3) if its title or topic is too close to a
  previous one. History persists between GitHub Actions runs via the
  workflow's `actions/cache` steps (keyed per channel); locally it's just
  files in `history/`. Committing `history/` to git gives you a second,
  permanent copy (cache retention is generous but not infinite).

## Pipeline flow (per job)
```
generate_script.py     -> output/script.json (Hindi, hook, full story arc)
generate_voiceover.py  -> output/audio/*.mp3, hook.mp3, durations.json
generate_music.py      -> output/music/bed.mp3 (Freesound CC0, optional)
fetch_visuals.py       -> output/visuals/* (AI/Horde/Pexels + hard visual safety wall)
generate_subtitles.py  -> output/subtitles.srt (Hindi)
assemble_video.py      -> output/final.mp4 (hook clip + ducked music + captions)
notify.py               -> sends output/final.mp4 to your Telegram
```
