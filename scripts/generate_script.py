"""
Step 1: Generate a short video script as structured JSON, for one channel.

Uses AGNES (OpenAI-compatible, https://apihub.agnes-ai.com/v1) with the
agnes-2.0-flash model and thinking mode enabled, so stories get planned
before they are written. Get a key at https://apihub.agnes-ai.com

All narration is written in Hindi (Devanagari), every script carries a
punchy 5-second "hook" line, and the prompt enforces a complete three-act
story arc so videos always have a real beginning and a definitive ending.

Every generated story is checked against the channel's history
(scripts/history.py -> history/<channel>.json). If the title or topic is too
close to a previous story, the script is regenerated with those topics listed
as forbidden, so no two videos repeat the same story.

Reads: config/channels.yaml, history/<channel_id>.json
Writes: output/script.json
{
  "channel_id": "...",
  "channel_name": "...",
  "title": "...",
  "hook": "...",
  "scenes": [
    {"text": "...", "visual_keywords": ["...", "..."]},
    ...
  ]
}

Required env vars:
  LLM_API_KEY or AGNES_API_KEY (or GROQ_API_KEY as a fallback provider)
  CHANNEL_ID   - must match an id in config/channels.yaml

Optional env vars:
  LLM_BASE_URL  - OpenAI-compatible endpoint (default: AGNES; set it to
                  https://api.groq.com/openai/v1 to use Groq explicitly)
  LLM_MODEL     - model name (default: agnes-2.0-flash, or llama-3.3-70b
                  when falling back to Groq)
  LLM_THINKING  - "1" (default) enables the model's thinking/reasoning mode
  LLM_MAX_TOKENS- max output tokens (default 4096)
  SCRIPT_RETRIES- max regenerate attempts when the LLM repeats a story
                   (default 3)
  JSON_RETRIES  - max attempts when the LLM returns malformed JSON
                   (default 3)
"""
import env  # loads .env from the project root via python-dotenv (no-op if missing)

import os
import re
import json
import yaml
import requests
import history
import safety

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(ROOT, "output")
CONFIG_PATH = os.path.join(ROOT, "config", "channels.yaml")

AGNES_URL = "https://apihub.agnes-ai.com/v1"
GROQ_URL = "https://api.groq.com/openai/v1"

# Provider resolution: explicit LLM_* overrides win, then AGNES, then Groq
# as a backward-compatible fallback (so existing setups keep working).
USING_AGNES = bool(os.environ.get("LLM_API_KEY") or os.environ.get("AGNES_API_KEY"))
LLM_API_KEY = (
    os.environ.get("LLM_API_KEY")
    or os.environ.get("AGNES_API_KEY")
    or os.environ.get("GROQ_API_KEY")
)
LLM_BASE_URL = os.environ.get("LLM_BASE_URL") or (AGNES_URL if USING_AGNES else GROQ_URL)
LLM_MODEL = os.environ.get("LLM_MODEL") or ("agnes-2.0-flash" if USING_AGNES else "llama-3.3-70b-versatile")
LLM_THINKING = os.environ.get("LLM_THINKING", "1").lower() not in ("0", "false", "no")
try:
    # Generous budget: thinking mode spends tokens on reasoning before the
    # answer, and an 8-scene script's JSON is long - too small a cap silently
    # truncates the JSON mid-array.
    LLM_MAX_TOKENS = max(256, int(os.environ.get("LLM_MAX_TOKENS", "4096")))
except ValueError:
    LLM_MAX_TOKENS = 4096
CHANNEL_ID = os.environ.get("CHANNEL_ID")


def load_config():
    with open(CONFIG_PATH, "r") as f:
        return yaml.safe_load(f)


def get_channel(cfg):
    if not CHANNEL_ID:
        raise SystemExit("Missing CHANNEL_ID environment variable.")
    match = next((c for c in cfg["channels"] if c["id"] == CHANNEL_ID), None)
    if not match:
        available = ", ".join(c["id"] for c in cfg["channels"])
        raise SystemExit(f"CHANNEL_ID '{CHANNEL_ID}' not found in channels.yaml. Available: {available}")
    return match


def build_prompt(channel, cfg, avoid_lines=None):
    target_words = cfg["video"]["target_words"]
    min_scenes = cfg["video"]["min_scenes"]
    max_scenes = cfg["video"]["max_scenes"]

    avoid_section = ""
    if avoid_lines:
        avoid_section = (
            "\nThese stories have ALREADY been made on this channel. Pick a clearly "
            "DIFFERENT story - do not reuse, rephrase, or closely resemble any of them:\n"
            + "\n".join(avoid_lines)
        )

    return f"""You are a professional scriptwriter for short-form vertical videos (TikTok/Reels/Shorts) about {channel['prompt']}.

WRITE EVERYTHING IN ENGLISH - the title, the hook, and ALL scene narration must be in clear, natural, conversational English. Short simple sentences, spoken-narration style.

THE STORY MUST BE COMPLETE - a clear BEGINNING, MIDDLE, and END. This is the most important rule. Never start mid-story and never stop mid-story:
- ACT 1 - OPENING (scene 1): Open in the middle of something intriguing to grab the viewer, then immediately ground the story: who/what/where is this about? Never open with a generic line like "Have you ever wondered..." or "Today we will talk about...".
- ACT 2 - BUILD (middle scenes): Escalate step by step. Each scene MUST flow naturally from the previous one - no topic jumps, no disconnected facts. Reveal one new layer per scene.
- ACT 3 - RESOLUTION (final scene): Land the payoff or the twist, then CLOSE with a definitive final line. The ending must feel finished - like the last chapter of a book. Never trail off, never stop mid-thought, never end on an open question. A viewer who watches to the end must feel the story was completely told.

Rules:
- Total narration across all scenes: about {target_words} words.
- Split it into {min_scenes} to {max_scenes} scenes. Each scene is 1-3 sentences, punchy, spoken narration style (no stage directions).
- Keep sentences short and clean (roughly 8-16 words each). Avoid long run-on sentences joined with "and" - break them into separate sentences instead. This matters: the narration is split sentence-by-sentence for pacing, so clean sentence breaks = natural-sounding pauses.
- Back every claim with a concrete detail: a name, a number, a date, a place, or a specific object. Zero vague filler like "it was amazing" or "the universe is vast" - SHOW the detail. This is what makes a video feel informative.
- \"hook\": a SEPARATE short line, 8-12 words (spoken in about 5 seconds), that grabs attention in the very first seconds of the video. It should tease the story's most intriguing detail WITHOUT spoiling the ending - e.g. a shocking fact, a question that begs an answer, or a promise of what's coming. This line is shown big on screen and spoken first.
- \"title\": a short catchy title (4-8 words).
- For each scene, also give 2-4 short VISUAL SEARCH KEYWORDS IN ENGLISH (things you could film or find stock footage of) that match what's being said - these feed the image generator, so make them SAFE FOR WORK: describe locations, objects, atmospheres, and clothing - never bodies, skin, or suggestive subjects.

FAMILY-SAFE CONTENT - STRICT AND NON-NEGOTIABLE (these videos are published on YouTube):
- ZERO profanity, swear words, vulgarity, or obscenity in ANY language.
- ZERO sexual content, innuendo, nudity references, or suggestive themes.
- ZERO graphic violence, gore, blood, or gruesome descriptions. Horror must be psychological suspense and dread - never gore.
- ZERO drug references, self-harm, or hateful content.
- The title, hook, narration, AND visual keywords must ALL be suitable for a general audience of all ages.
{avoid_section}
Respond with ONLY valid JSON, no markdown fences, no commentary, in this exact shape:
{{
  "title": "short catchy title",
  "hook": "punchy 8-12 word hook line",
  "scenes": [
    {{"text": "English narration...", "visual_keywords": ["safe", "english", "keywords"]}}
  ]
}}
"""


def clean_json(raw):
    raw = (raw or "").strip()
    raw = re.sub(r"^```(json)?", "", raw).strip()
    raw = re.sub(r"```$", "", raw).strip()
    # Some models prefix stray prose before the JSON - keep only the
    # outermost {...} object.
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        raw = raw[start : end + 1]
    return raw


def generate_script(prompt):
    """Call the LLM (AGNES by default) and return the parsed script dict.
    Retries on malformed JSON (the model occasionally emits a stray
    character); raises the last parse error if every attempt is unparseable."""
    try:
        retries = max(1, int(os.environ.get("JSON_RETRIES", "3")))
    except ValueError:
        retries = 3

    payload = {
        "model": LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 1.0,
        "max_tokens": LLM_MAX_TOKENS,
    }
    # Thinking/reasoning mode. AGNES documents chat_template_kwargs.enable_thinking;
    # a top-level "thinking": true is also accepted. Skip for plain Groq.
    if LLM_THINKING and "groq" not in LLM_BASE_URL.lower():
        payload["chat_template_kwargs"] = {"enable_thinking": True}
        payload["thinking"] = True

    last_error = None
    for attempt in range(1, retries + 1):
        resp = requests.post(
            f"{LLM_BASE_URL.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {LLM_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=120,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"].get("content") or ""
        try:
            return json.loads(clean_json(content))
        except json.JSONDecodeError as e:
            last_error = e
            if attempt < retries:
                print(f"[generate_script] malformed JSON from model "
                      f"(attempt {attempt}/{retries}) - retrying")
    raise last_error


def story_topic(data):
    """A fingerprint of the story's subject, for dedup comparisons. Uses the
    whole narration (not just scene 1) so reworded retellings are caught."""
    if data.get("scenes"):
        return " ".join(s["text"] for s in data["scenes"])[:400]
    return data.get("title", "")


def unsafe_terms(data):
    """Prohibited terms found across the script's title, hook, narration,
    and visual keywords - anything a family-safe screen must reject."""
    return safety.find_prohibited(
        " ".join([
            data.get("title", ""),
            data.get("hook", ""),
            *(s.get("text", "") for s in data.get("scenes", [])),
            *(k for s in data.get("scenes", []) for k in s.get("visual_keywords", [])),
        ])
    )


def main():
    if not LLM_API_KEY:
        raise SystemExit("Missing LLM_API_KEY / AGNES_API_KEY / GROQ_API_KEY environment variable or secret.")
    if env.looks_unfilled(LLM_API_KEY):
        # Catches the .env.example values left as-is (e.g. 'your-agnes-key')
        # before any API call is made - a 401 with a placeholder is confusing.
        raise SystemExit(
            "LLM_API_KEY looks like an unfilled template placeholder "
            "(e.g. 'your-agnes-key'). Put your real key in .env and try again."
        )

    cfg = load_config()
    channel = get_channel(cfg)
    try:
        max_retries = max(1, int(os.environ.get("SCRIPT_RETRIES", "3")))
    except ValueError:
        max_retries = 3

    avoid = history.avoid_list(channel["id"])
    data = None
    last_reason = None
    for attempt in range(1, max_retries + 1):
        prompt = build_prompt(channel, cfg, avoid)
        data = generate_script(prompt)
        data["channel_id"] = channel["id"]
        data["channel_name"] = channel["display_name"]

        topic = story_topic(data)

        # Family-safe gate FIRST: a story that fails the screen must never be
        # accepted, no matter what else is wrong with it - so this check runs
        # before the dedup check (otherwise a script that is both a duplicate
        # and unsafe could slip through on the final retry).
        prohibited = unsafe_terms(data)
        if prohibited:
            print(f"[generate_script] unsafe content detected ({', '.join(prohibited)}) "
                  f"- regenerating ({attempt}/{max_retries})")
            line = f"- forbidden topics (do NOT mention these words or any form of them): " \
                   f"{', '.join(prohibited[:8])}"
            if line not in avoid:
                avoid.append(line)
            last_reason = "content"
            continue

        if history.is_duplicate(channel["id"], data["title"], topic):
            print(f"[generate_script] '{data['title']}' is too close to a previous story "
                  f"- regenerating ({attempt}/{max_retries})")
            line = f"- {data['title']} ({topic[:60]})"
            if line not in avoid:
                avoid.append(line)
            last_reason = "duplicate"
            continue

        # Every video needs a hook - if the model skipped it, derive one from
        # the opening scene so the 5-second hook overlay/audio always exists.
        if not data.get("hook"):
            words = " ".join(data["scenes"][0]["text"].split())
            data["hook"] = " ".join(words.split()[:12])

        history.record(channel["id"], data["title"], topic)
        break
    else:
        # Never ship text that failed the family-safe screen - this is a hard
        # stop, not a warning. Re-screen the last attempt as a belt-and-braces
        # check so no path can accept unsafe content. The safety wall has
        # no environment-variable bypass.
        if last_reason == "content" or unsafe_terms(data):
            raise SystemExit(
                f"[generate_script] {max_retries} attempts all failed the family-safe "
                "content screen (profanity/obscenity/nudity/gore). Set CONTENT_FILTER=0 "
                "to disable the screen, or try again later."
            )
        # Exhausted retries on duplicates - accept the last attempt, but don't
        # record it: it is a repeat of an existing entry, so recording would
        # just bloat history.
        print(f"[generate_script] WARNING: {max_retries} attempts all too close to a "
              "previous story - accepting the last one anyway")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_path = os.path.join(OUTPUT_DIR, "script.json")
    with open(out_path, "w") as f:
        json.dump(data, f, indent=2)

    print(f"[generate_script] channel={channel['id']} model={LLM_MODEL} thinking={'on' if LLM_THINKING else 'off'} "
          f"title='{data['title']}' scenes={len(data['scenes'])}")
    print(f"[generate_script] wrote {out_path}")


if __name__ == "__main__":
    main()
