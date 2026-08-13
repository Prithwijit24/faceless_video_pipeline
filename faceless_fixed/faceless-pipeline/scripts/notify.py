"""
Step 6 (final): Ping you that the video is ready by sending it directly
via a free Telegram bot (works for clips under ~49MB, which covers
virtually every 45-75s faceless reel).

Create a bot for free via @BotFather on Telegram, then message your new
bot once so it can message you back, and grab your chat id from
https://api.telegram.org/bot<token>/getUpdates

Each channel can use its own bot via a per-channel token env var named
<CHANNEL_ID_UPPER>_TELEGRAM_BOT_TOKEN (e.g. MYTHOLOGY_TELEGRAM_BOT_TOKEN).
If that's unset, the shared TELEGRAM_BOT_TOKEN is used instead. A channel
can also target a different chat via the optional `telegram_chat_id` key
in config/channels.yaml, falling back to TELEGRAM_CHAT_ID.

Reads: output/script.json, output/final.mp4
"""
import env  # loads .env from the project root via python-dotenv (no-op if missing)

import os
import json
import yaml
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(ROOT, "output")
CONFIG_PATH = os.path.join(ROOT, "config", "channels.yaml")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DEFAULT_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


def get_bot_token(channel_id):
    """Per-channel bot token (e.g. MYTHOLOGY_TELEGRAM_BOT_TOKEN), falling
    back to the shared TELEGRAM_BOT_TOKEN when unset."""
    return os.environ.get(f"{channel_id.upper()}_TELEGRAM_BOT_TOKEN") or BOT_TOKEN


def get_chat_id(channel_id):
    try:
        with open(CONFIG_PATH) as f:
            cfg = yaml.safe_load(f)
        channel = next((c for c in cfg["channels"] if c["id"] == channel_id), None)
        override = (channel or {}).get("telegram_chat_id", "").strip()
        return override or DEFAULT_CHAT_ID
    except Exception:
        return DEFAULT_CHAT_ID


def main():
    with open(os.path.join(OUTPUT_DIR, "script.json")) as f:
        script = json.load(f)

    video_path = os.path.join(OUTPUT_DIR, "final.mp4")
    caption = f"🎬 {script['channel_name']}: {script['title']}"

    chat_id = get_chat_id(script["channel_id"])
    bot_token = get_bot_token(script["channel_id"])

    if not bot_token or not chat_id:
        print("[notify] bot token / chat id not set, skipping notification.")
        print(f"[notify] {caption}")
        return

    size_mb = os.path.getsize(video_path) / (1024 * 1024)
    url = f"https://api.telegram.org/bot{bot_token}/sendVideo"

    if size_mb <= 49:
        with open(video_path, "rb") as f:
            resp = requests.post(
                url,
                data={"chat_id": chat_id, "caption": caption, "supports_streaming": True},
                files={"video": f},
                timeout=120,
            )
    else:
        # Too big for Telegram's bot upload limit - fall back to a text
        # alert (grab the file from the Actions run artifacts instead).
        resp = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            data={
                "chat_id": chat_id,
                "text": f"{caption}\n\n(File is {size_mb:.1f}MB, too big to send here - "
                        f"grab it from the GitHub Actions run artifacts instead.)",
            },
            timeout=30,
        )

    if resp.status_code != 200:
        print(f"[notify] Telegram API error: {resp.status_code} {resp.text}")
    else:
        print(f"[notify] sent to Telegram (chat {chat_id})")


if __name__ == "__main__":
    main()
