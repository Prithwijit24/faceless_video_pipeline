"""
Lightweight per-channel story history, so every generated video gets a fresh
story and past topics are never repeated.

History is stored as one JSON file per channel, which lets GitHub Actions
cache each channel independently (see the workflow's actions/cache steps).
Locally the same code just writes files under ./history/.

File format (history/<channel_id>.json):
[
  {"title": "...", "topic": "...", "date": "2026-08-13"},
  ...
]

Env vars:
  HISTORY_DIR - where history files live (default: <repo>/history)
"""
import os
import re
import json
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HISTORY_DIR = os.environ.get("HISTORY_DIR") or os.path.join(ROOT, "history")

# Tune how aggressively a new story is treated as a repeat of an old one.
TITLE_JACCARD_THRESHOLD = 0.6   # title word-overlap (e.g. 4 of 7 words)
TOPIC_JACCARD_THRESHOLD = 0.55  # narration word-overlap


def _path(channel_id):
    return os.path.join(HISTORY_DIR, f"{channel_id}.json")


def load(channel_id):
    """Return the list of recorded stories for a channel ([] if none yet)."""
    try:
        with open(_path(channel_id)) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save(channel_id, stories):
    os.makedirs(HISTORY_DIR, exist_ok=True)
    tmp = _path(channel_id) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(stories, f, indent=2)
    os.replace(tmp, _path(channel_id))


def record(channel_id, title, topic):
    """Append a story to the channel's history and persist it."""
    stories = load(channel_id)
    stories.append({
        "title": title,
        "topic": topic,
        "date": date.today().isoformat(),
    })
    _save(channel_id, stories)


def _normalize(text):
    words = re.sub(r"[^a-z0-9\s]", " ", (text or "").lower())
    return set(re.sub(r"\s+", " ", words).strip().split())


def _jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def is_duplicate(channel_id, title, topic):
    """True if the title/topic is too close to any previously recorded story
    for this channel. Checks normalized exact title, title word overlap, and
    opening-narration word overlap."""
    norm_title = _normalize(title)
    norm_topic = _normalize(topic)
    for s in load(channel_id):
        if _normalize(s.get("title")) == norm_title:
            return True
        if _jaccard(norm_title, _normalize(s.get("title"))) >= TITLE_JACCARD_THRESHOLD:
            return True
        if _jaccard(norm_topic, _normalize(s.get("topic"))) >= TOPIC_JACCARD_THRESHOLD:
            return True
    return False


def avoid_list(channel_id, limit=60):
    """Recent 'title - topic' lines to feed the LLM as topics to avoid."""
    stories = load(channel_id)[-limit:]
    return [
        f"- {s.get('title', '?')} ({s.get('topic', '')[:60]})"
        for s in stories
    ]
