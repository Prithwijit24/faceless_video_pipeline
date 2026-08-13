<div align="center">

# 🎬 Faceless Video Pipeline

**Free · Plug-and-Play · Fully Automated · $0/month**

AI-written **Hindi** reels — script, voiceover, music, visuals & subtitles — assembled and delivered to your Telegram, on autopilot.

![License](https://img.shields.io/badge/License-MIT-green.svg)
![Cost](https://img.shields.io/badge/Cost-%240-brightgreen.svg)
![Language](https://img.shields.io/badge/Language-Python-blue.svg)
![Output](https://img.shields.io/badge/Output-8%20videos%2Fday-orange.svg)
![Channels](https://img.shields.io/badge/Channels-4-purple.svg)

</div>

---

## ✨ What is this?

A fully automated pipeline that produces **short Hindi faceless videos** from scratch — no editing, no recording, no manual work. It writes a proper story (with a punchy 5-second hook and a complete beginning/middle/end), speaks it with a natural Hindi voice, adds situational background music that ducks under the narration, generates matching visuals, burns in Devanagari subtitles, and drops the finished reel into your Telegram chat.

> 🎯 **Scale:** 4 channels × 2 videos/day = **8 videos every day**, for **$0**.

## 🚀 Features

| | Feature | How it works |
|---|---|---|
| 📝 | **AI scriptwriting** | Hindi scripts with a 5-second hook + full story arc, planned before writing (thinking mode) |
| 🎙️ | **Natural voiceover** | Hindi TTS with pitch/EQ tuning — male & female voices per channel |
| 🎵 | **Background music** | Situational, CC0-only tracks that sidechain-duck under the voice |
| 🖼️ | **Matching visuals** | AI images prompted from the actual narration, with stock-video fallback |
| 💬 | **Devanagari subtitles** | Hindi captions generated locally from the audio |
| 🎬 | **Video assembly** | Hook clip, ducked music, styled captions — all via ffmpeg |
| ⏰ | **Autopilot scheduling** | GitHub Actions runs the whole thing daily, free |
| 📲 | **Telegram delivery** | Finished videos land directly in your chat |
| 🛡️ | **Family-safe by default** | Fail-closed content wall — scripts, images, and final video are all screened |

## 🧩 How it works

```
generate_script.py     → 📝 script.json (Hindi, hook, full story arc)
generate_voiceover.py  → 🎙️ audio/*.mp3 + hook.mp3
generate_music.py      → 🎵 music/bed.mp3 (CC0, optional)
fetch_visuals.py       → 🖼️ visuals/* (AI images → stock fallback, safety-screened)
generate_subtitles.py  → 💬 subtitles.srt (Hindi, Devanagari)
assemble_video.py      → 🎬 final.mp4 (hook + ducked music + captions)
notify.py              → 📲 final.mp4 → your Telegram
```

Each step is independent — rerun just the one that broke, no need to redo the rest.

## 🧰 Tech stack — everything is free

| Step | Tool | Cost |
|---|---|---|
| 📝 Script (Hindi, hook + full arc) | AGNES API (`agnes-2.0-flash`, thinking mode) | Free |
| 🎙️ Voiceover (Hindi, pitch/EQ tuned) | `edge-tts` | Free, no key needed |
| 🎵 Background music (situational, ducked) | Freesound (CC0-only, no attribution) | Free token |
| 🖼️ Backgrounds | Pollinations.ai (no key) → AI Horde → Pexels stock | Free |
| 💬 Subtitles (Hindi, Devanagari) | `faster-whisper` (runs locally) | Free |
| 🎬 Assembly | ffmpeg | Free |
| ⏰ Scheduling | GitHub Actions (2,000 min/mo on private repos) | Free |
| 📲 Delivery | Telegram Bot API | Free |

## 📁 Repository layout

| Path | What it is |
|---|---|
| 📂 `faceless_fixed/faceless-pipeline/` | The pipeline: step scripts, channel config, story history, and output |
| 🗂️ `.github/workflows/generate-video.yml` | Scheduled GitHub Actions workflow (daily 08:00 UTC) |
| 🖼️ `channel_logo/` | Per-channel logos (`mythology`, `horror`, `space`, `psychology`) |
| 📜 `LICENSE` | MIT license |

## 🚀 Quick start

```bash
cd faceless_fixed/faceless-pipeline
pip install -r requirements.txt     # + ffmpeg on your system
cp .env.example .env                # add your free API keys
python scripts/pipeline.py          # watch it go
```

📖 **Full guide** — prerequisites, free API keys, local testing, and GitHub Actions setup are all in the
[detailed pipeline README](faceless_fixed/faceless-pipeline/README.md).

## 🛡️ Safety & content policy

- **Scripts** are prompted to avoid profanity, gore, nudity, drugs, and hateful content — then screened locally; unsafe output is regenerated.
- **Visuals** prefer environments, objects, landscapes, and abstract shots (no people/faces) and every image is screened with **NudeNet**.
- **Fail-closed**: if any asset can't be screened, the pipeline stops — it never ships an unscreened video.
- **Audio** is mastered to a consistent loudness (`-14 LUFS`) with the music ducked behind the narration.

## 🤝 Contributing

Found a bug or have an idea? Open an issue or PR — the pipeline is built to be tweaked (channels, prompts, voices, schedule, and more are all config-driven).

## 📜 License

MIT — see [LICENSE](LICENSE). © 2026 [Prithwijit24](https://github.com/Prithwijit24)
