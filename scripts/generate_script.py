"""
Step 1: Generate a short video script as structured JSON, for one channel.

Uses AGNES (OpenAI-compatible, https://apihub.agnes-ai.com/v1) with the
agnes-2.0-flash model and thinking mode enabled, so stories get planned
before they are written. Get a key at https://apihub.agnes-ai.com

All narration is written in English, every script carries a punchy ~5 second
"hook" line to grab attention in the first seconds, and the prompt enforces a
complete three-act story arc (with a mandatory conclusion) so videos always
have a real beginning, middle, and definitive ending.

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
                    or violates a hard constraint (length/scene-count/
                    conclusion) (default 5)
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
    min_words = int(target_words * 0.9)
    max_words = int(target_words * 1.1)

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
- ACT 3 - RESOLUTION (final scene): Land the payoff or the twist, then CLOSE with a definitive final line. The ending must feel finished - like the last chapter of a book. Never trail off, never stop mid-thought, never end on an open question. A viewer who watches to the end must feel the story was completely told. THIS CONCLUSION IS MANDATORY - every script must end with a finished, conclusive final scene. The final scene must give a clear takeaway or lesson so the viewer leaves with a satisfying answer.

Rules:
- Total narration across the WHOLE script (hook + all scenes combined): about {target_words} words. This is a HARD BUDGET - never write more than {max_words} words or fewer than {min_words} words. The narration is timed for a 35-40 second video, so every sentence must be essential - cut filler, adjectives, and repetition.
- Split it into {min_scenes} to {max_scenes} scenes. Each scene is 1-2 short sentences, punchy, spoken narration style (no stage directions).
- Keep sentences short and clean (roughly 8-16 words each). Avoid long run-on sentences joined with "and" - break them into separate sentences instead. This matters: the narration is split sentence-by-sentence for pacing, so clean sentence breaks = natural-sounding pauses.
- Back every claim with a concrete detail: a name, a number, a date, a place, or a specific object. Zero vague filler like "it was amazing" or "the universe is vast" - SHOW the detail. This is what makes a video feel informative.
- \"hook\": a SEPARATE punchy line of 11-13 words (spoken in about 5 seconds) that hooks the viewer in the very first seconds - a shocking fact, a bold claim, or a question that begs an answer, teasing the story's most intriguing detail WITHOUT spoiling the ending. This line is shown big on screen and spoken first. The hook is MANDATORY - always include it, and keep it to 11-13 words so it lands in ~5 seconds.
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
  "hook": "punchy 11-13 word hook line (spoken in ~5 seconds)",
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


# A JSON string literal (escaped quotes/backslashes handled), used to salvage
# fields out of otherwise-broken model output.
_JSON_STRING = r'"((?:[^"\\]|\\.)*)"'


def _decode_json_string(fragment):
    """Properly unescape a captured JSON string fragment (escapes like
    newline or unicode are decoded)."""
    return json.loads('"' + fragment + '"')


def _is_script(data):
    """True when a parsed value has the bare minimum script shape (a title
    and at least one scene). Anything else is treated as unparseable so the
    model gets another chance."""
    return (
        isinstance(data, dict)
        and isinstance(data.get("title"), str)
        and bool(data["title"].strip())
        and isinstance(data.get("scenes"), list)
        and bool(data["scenes"])
    )


def _salvage_script(content):
    """Last-resort recovery: pull title/hook/scenes out of broken JSON with
    regexes, keyed on the exact schema the prompt requests. This recovers
    from common LLM slip-ups like a missing comma ("Expecting ',' delimiter")
    or one corrupted field - no model round-trip needed. Returns a script dict
    on success, or None when the output is too broken to trust."""
    def grab(key):
        m = re.search(r'"' + re.escape(key) + r'"\s*:\s*' + _JSON_STRING, content)
        return _decode_json_string(m.group(1)) if m else None

    title = grab("title")
    hook = grab("hook")
    texts = re.findall(r'"text"\s*:\s*' + _JSON_STRING, content)
    arrays = re.findall(r'"visual_keywords"\s*:\s*\[(.*?)\]', content, re.DOTALL)

    # Reject garbage/severely truncated replies: a salvage needs at least a
    # mini three-scene arc, otherwise it's better to re-roll with the model.
    if not title or len(texts) < 3:
        return None

    scenes = []
    for i, text in enumerate(texts):
        keywords = []
        if i < len(arrays):
            keywords = [
                _decode_json_string(k) for k in re.findall(_JSON_STRING, arrays[i])
            ]
        scenes.append({"text": text, "visual_keywords": keywords})

    data = {"title": title, "scenes": scenes}
    if hook:
        data["hook"] = hook
    return data


def parse_script_json(content):
    """Parse the model's reply as the script JSON, tolerating the common LLM
    slip-ups (markdown fences, stray prose, trailing commas, missing commas,
    one broken field) before giving up. Returns the script dict; raises
    json.JSONDecodeError when the output cannot be salvaged."""
    cleaned = clean_json(content)

    # 1) Plain parse of the cleaned text.
    try:
        data = json.loads(cleaned)
        if _is_script(data):
            return data
    except json.JSONDecodeError:
        pass

    # 2) Tolerate trailing commas (a very common LLM slip).
    try:
        fixed = re.sub(r",\s*([}\]])$", r"\1", cleaned, flags=re.MULTILINE)
        if fixed != cleaned:
            data = json.loads(fixed)
            if _is_script(data):
                return data
    except json.JSONDecodeError:
        pass

    # 3) Last resort: regex salvage of the known schema (handles missing
    #    commas like the "Expecting ',' delimiter" failures).
    data = _salvage_script(cleaned)
    if data is not None:
        return data

    raise json.JSONDecodeError("model output is not valid script JSON", cleaned, 0)


def generate_script(prompt):
    """Call the LLM (AGNES by default) and return the parsed script dict.
    Retries on malformed JSON; each retry feeds the failed output back to the
    model with the parse error so it can fix the formatting instead of
    re-rolling the same mistake. Raises the last parse error if every attempt
    is unparseable."""
    try:
        retries = max(1, int(os.environ.get("JSON_RETRIES", "3")))
    except ValueError:
        retries = 3

    last_error = None
    last_content = None
    for attempt in range(1, retries + 1):
        messages = [{"role": "user", "content": prompt}]
        if last_content is not None:
            messages.append({
                "role": "user",
                "content": (
                    "Your previous reply was not valid JSON "
                    f"(parse error: {last_error}). It was:\n\n"
                    f"{last_content[:2000]}\n\n"
                    "Fix ONLY the JSON formatting errors (missing commas, stray "
                    "characters, truncation) and reply with ONLY the corrected "
                    "JSON in the exact shape from the original request - no "
                    "markdown fences, no commentary, no extra text."
                ),
            })

        payload = {
            "model": LLM_MODEL,
            "messages": messages,
            "temperature": 1.0,
            "max_tokens": LLM_MAX_TOKENS,
        }
        # Thinking/reasoning mode. AGNES documents chat_template_kwargs.enable_thinking;
        # a top-level "thinking": true is also accepted. Skip for plain Groq.
        if LLM_THINKING and "groq" not in LLM_BASE_URL.lower():
            payload["chat_template_kwargs"] = {"enable_thinking": True}
            payload["thinking"] = True

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
            return parse_script_json(content)
        except json.JSONDecodeError as e:
            last_error = e
            last_content = content
            if attempt < retries:
                print(f"[generate_script] malformed JSON from model "
                      f"(attempt {attempt}/{retries}) - retrying")
    raise last_error


def narration_word_count(data):
    """Total spoken words: the hook plus all scene narration. This is what
    the length budget (35-40s video) is enforced against."""
    words = sum(len(s.get("text", "").split()) for s in data.get("scenes", []))
    words += len((data.get("hook") or "").split())
    return words


def has_conclusion(data):
    """True when the script lands a definitive ending. The conclusion is
    mandatory - a final scene that trails off, ends mid-sentence, or closes on
    an open question is rejected so the video never feels unfinished."""
    scenes = data.get("scenes") or []
    if not scenes:
        return False
    last = (scenes[-1].get("text") or "").strip()
    if not last:
        return False
    if last.endswith(("?", "...", "…")):
        return False
    return last[-1] in (".", "!")


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
        max_retries = max(1, int(os.environ.get("SCRIPT_RETRIES", "8")))
    except ValueError:
        max_retries = 8

    avoid = history.avoid_list(channel["id"])
    data = None
    last_reason = None
    min_scenes = cfg["video"]["min_scenes"]
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

        # Conclusion is MANDATORY - reject scripts whose final scene ends on
        # an open question or trails off, and regenerate with a hint instead
        # of shipping an unfinished story.
        if not has_conclusion(data):
            print(f"[generate_script] final scene does not end with a definitive "
                  f"conclusion - regenerating ({attempt}/{max_retries})")
            line = ("- make sure the FINAL scene ends with a definitive concluding "
                    "line - a finished period or exclamation, never a question "
                    "mark or '...'")
            if line not in avoid:
                avoid.append(line)
            last_reason = "conclusion"
            continue

        # Scene-count floor - fewer scenes means fewer pacing pauses, which
        # drags the video under the 35s floor. Enforce the channel's min so
        # the spoken length stays in the 35-40s window.
        if len(data.get("scenes", [])) < min_scenes:
            print(f"[generate_script] only {len(data['scenes'])} scenes "
                  f"(need at least {min_scenes}) - regenerating ({attempt}/{max_retries})")
            line = (f"- split the story into at least {min_scenes} scenes "
                    f"(you wrote too few)")
            if line not in avoid:
                avoid.append(line)
            last_reason = "scenes"
            continue

        # Length budget - the model overshoots the soft word target, so enforce
        # a hard range around it. This is what keeps videos in the 35-40s
        # window. Off-budget scripts are regenerated with the exact count so
        # the model knows how much to trim or add.
        target_words = cfg["video"]["target_words"]
        min_words = int(target_words * 0.9)
        max_words = int(target_words * 1.1)
        word_count = narration_word_count(data)
        if word_count < min_words or word_count > max_words:
            print(f"[generate_script] narration is {word_count} words "
                  f"(need {min_words}-{max_words}) - regenerating ({attempt}/{max_retries})")
            line = (f"- keep the total narration (hook + all scenes) between "
                    f"{min_words} and {max_words} words - you wrote {word_count}. "
                    f"Cut filler and repetition, keep only essential sentences.")
            if line not in avoid:
                avoid.append(line)
            last_reason = "length"
            continue

        # Every video needs a hook - if the model skipped it, derive one from
        # the opening scene so the ~5 second hook overlay/audio always exists.
        if not data.get("hook"):
            words = " ".join(data["scenes"][0]["text"].split())
            data["hook"] = " ".join(words.split()[:13])
        else:
            # Hard cap at 11-13 words so the hook always lands in ~5 seconds
            # (the channel voices speak roughly 2.3 words/sec). Truncating the
            # tail of a tease line is safe - it still hooks the viewer.
            hook_words = data["hook"].split()
            if len(hook_words) > 13:
                data["hook"] = " ".join(hook_words[:13])

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
        # A conclusion is mandatory - never accept a script that ends
        # unfinished, no matter how many retries it took.
        if last_reason == "conclusion":
            raise SystemExit(
                f"[generate_script] {max_retries} attempts all failed the mandatory "
                "conclusion check (final scene must end with a definitive line). "
                "Try again later."
            )
        # The length budget is a hard requirement too (35-40s videos) - never
        # ship a script that would make the video several times too long.
        if last_reason == "length":
            target_words = cfg["video"]["target_words"]
            raise SystemExit(
                f"[generate_script] {max_retries} attempts all missed the "
                f"{int(target_words * 0.9)}-{int(target_words * 1.1)} word length "
                "budget (keeps videos at 35-40s). Try again later."
            )
        # Too few scenes is also a hard requirement - it keeps the video in the
        # 35-40s window, so never ship a script with too few scenes.
        if last_reason == "scenes":
            raise SystemExit(
                f"[generate_script] {max_retries} attempts all produced too few "
                f"scenes (need at least {min_scenes}; keeps videos at 35-40s). "
                "Try again later."
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
