"""
Runs the full pipeline end to end:
script -> voiceover -> music -> visuals -> subtitles -> assemble -> notify

Run locally with:  python scripts/pipeline.py
(after `pip install -r requirements.txt` and exporting the env vars listed
in README.md, plus having ffmpeg installed on your system/PATH)

Interactive mode: before each step it checks the step's prerequisites
(env vars, input files, ffmpeg), shows the result, then asks whether to
run it - so you never confirm a step that is guaranteed to fail:

    --- fetch_visuals.py: NOT READY - 1 problem
        x output/audio/durations.json missing (run generate_voiceover.py first)
    Run fetch_visuals.py? [y/N/q]

  - Enter or "y" runs the step. When prerequisites are missing the default
    flips to "n" - press y to run it anyway
  - "n" skips it and moves to the next one
  - "q" stops the pipeline right there

Useful for testing in the terminal:

    python scripts/pipeline.py                  # interactive prompts + checks
    python scripts/pipeline.py --check          # test all steps' prereqs, run nothing
    python scripts/pipeline.py --yes            # run everything, no prompts
    python scripts/pipeline.py --list           # show the steps in order

When stdin is not a terminal (e.g. GitHub Actions, or piping output), the
pipeline runs every step without prompting, so automation is unaffected.
"""
import env  # loads .env from the project root via python-dotenv (no-op if missing)

import argparse
import os
import shutil
import subprocess
import sys

STEPS = [
    ("generate_script.py", []),
    ("generate_voiceover.py", []),
    ("generate_music.py", []),
    ("fetch_visuals.py", []),
    ("generate_subtitles.py", []),
    ("assemble_video.py", []),
    ("notify.py", []),
]

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(os.path.dirname(SCRIPTS_DIR), "output")
CONFIG_PATH = os.path.join(os.path.dirname(SCRIPTS_DIR), "config", "channels.yaml")

# ------------------------------------------------------------- prereq checks


def _env(*names):
    """Names of the env vars that are not set."""
    return [n for n in names if not os.environ.get(n)]


def _missing_ffmpeg():
    """ffmpeg/ffprobe binaries that are not on PATH."""
    return [t for t in ("ffmpeg", "ffprobe") if shutil.which(t) is None]


def _missing_visual_safety_dependency():
    """NudeNet is mandatory because the visual safety wall fails closed."""
    try:
        import nudenet  # noqa: F401
    except Exception:
        return ["nudenet (install with: pip install -r requirements.txt)"]
    return []


def _channel_problem():
    """Problem with CHANNEL_ID, or None when it's fine (or uncheckable)."""
    ch = os.environ.get("CHANNEL_ID")
    if not ch:
        return "CHANNEL_ID not set (e.g. export CHANNEL_ID=mythology)"
    try:
        import yaml
        with open(CONFIG_PATH) as f:
            cfg = yaml.safe_load(f)
        ids = [c["id"] for c in cfg.get("channels", [])]
    except Exception:
        return None  # config unreadable - don't block the user on it
    if ch not in ids:
        return (f"CHANNEL_ID '{ch}' not in config/channels.yaml "
                f"(available: {', '.join(ids)})")
    return None


def check_step(step):
    """Prereq check for one step. Returns (ready, blockers, notes):
    - ready: True when every required prerequisite is present
    - blockers: missing REQUIRED prereqs that will make the step fail
    - notes: informational (missing optional things, graceful fallbacks)
    """
    if step == "generate_script.py":
        blockers = []
        llm_key = (os.environ.get("LLM_API_KEY")
                   or os.environ.get("AGNES_API_KEY")
                   or os.environ.get("GROQ_API_KEY"))
        if not llm_key:
            blockers.append("no LLM key - set LLM_API_KEY / AGNES_API_KEY / GROQ_API_KEY")
        elif env.looks_unfilled(llm_key):
            blockers.append("LLM key looks like an unfilled template placeholder "
                            "(e.g. 'your-agnes-key') - put your real key in .env")
        channel = _channel_problem()
        if channel:
            blockers.append(channel)
        return not blockers, blockers, []

    if step == "generate_voiceover.py":
        blockers = []
        if not os.path.exists(os.path.join(OUTPUT_DIR, "script.json")):
            blockers.append("output/script.json missing (run generate_script.py first)")
        blockers += _missing_ffmpeg()
        return not blockers, blockers, []

    if step == "generate_music.py":
        blockers = []
        if not os.path.exists(os.path.join(OUTPUT_DIR, "audio", "full.mp3")):
            blockers.append("output/audio/full.mp3 missing (run generate_voiceover.py first)")
        blockers += _missing_ffmpeg()
        notes = []
        if len(_env("FREESOUND_API_TOKEN", "FREESOUND_API_KEY")) == 2:
            notes.append("no Freesound key (FREESOUND_API_TOKEN / FREESOUND_API_KEY) - "
                         "background music will be skipped (voiceover-only video)")
        return not blockers, blockers, notes

    if step == "fetch_visuals.py":
        blockers = []
        for rel, hint in (
            ("script.json", "run generate_script.py first"),
            ("audio/durations.json", "run generate_voiceover.py first"),
        ):
            if not os.path.exists(os.path.join(OUTPUT_DIR, rel)):
                blockers.append(f"output/{rel} missing ({hint})")
        blockers += _missing_ffmpeg()
        blockers += _missing_visual_safety_dependency()
        notes = []
        if len(_env("HORDE_IMAGE_API_KEY", "PEXELS_API_KEY")) == 2:
            notes.append("no HORDE_IMAGE_API_KEY / PEXELS_API_KEY - if Pollinations "
                         "fails, scenes have no fallback")
        return not blockers, blockers, notes

    if step == "generate_subtitles.py":
        blockers = []
        if not os.path.exists(os.path.join(OUTPUT_DIR, "audio", "full.mp3")):
            blockers.append("output/audio/full.mp3 missing (run generate_voiceover.py first)")
        return not blockers, blockers, []

    if step == "assemble_video.py":
        blockers = []
        for rel, hint in (
            ("audio/full.mp3", "run generate_voiceover.py first"),
            ("audio/durations.json", "run generate_voiceover.py first"),
            ("visuals/manifest.json", "run fetch_visuals.py first"),
        ):
            if not os.path.exists(os.path.join(OUTPUT_DIR, rel)):
                blockers.append(f"output/{rel} missing ({hint})")
        blockers += _missing_ffmpeg()
        blockers += _missing_visual_safety_dependency()
        notes = []
        if not os.path.exists(os.path.join(OUTPUT_DIR, "subtitles.srt")):
            notes.append("no subtitles.srt - captions will be skipped")
        if not os.path.exists(os.path.join(OUTPUT_DIR, "music", "bed.mp3")):
            notes.append("no music bed - audio will be voiceover only")
        return not blockers, blockers, notes

    if step == "notify.py":
        blockers = []
        if not os.path.exists(os.path.join(OUTPUT_DIR, "final.mp4")):
            blockers.append("output/final.mp4 missing (run assemble_video.py first)")
        if not os.path.exists(os.path.join(OUTPUT_DIR, "script.json")):
            blockers.append("output/script.json missing (run generate_script.py first)")
        notes = []
        missing_tg = _env("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
        if missing_tg:
            # A per-channel bot token (<CHANNEL_ID_UPPER>_TELEGRAM_BOT_TOKEN)
            # replaces the shared TELEGRAM_BOT_TOKEN when set.
            channel = (os.environ.get("CHANNEL_ID") or "").strip().upper()
            if channel and not _env(f"{channel}_TELEGRAM_BOT_TOKEN"):
                missing_tg = [m for m in missing_tg if m != "TELEGRAM_BOT_TOKEN"]
            if missing_tg:
                notes.append("no " + " / ".join(missing_tg) + " - caption will be "
                             "printed instead of sent")
        return not blockers, blockers, notes

    return True, [], []


def format_status(ready, blockers):
    if ready:
        return "READY"
    return f"NOT READY ({len(blockers)} problem{'s' if len(blockers) != 1 else ''})"


# ------------------------------------------------------------- interactive


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Run the faceless reel pipeline (script -> voiceover -> "
                    "visuals -> subtitles -> assemble -> notify).",
    )
    parser.add_argument(
        "-y", "--yes", action="store_true",
        help="Run every step without prompting (the default anyway when "
             "stdin is not a terminal, e.g. in GitHub Actions).",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Check every step's prerequisites (env vars, input files, "
             "ffmpeg) and print a report without running anything.",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="Print the steps in order and exit.",
    )
    return parser.parse_args(argv)


def confirm_step(step, step_args, default_yes):
    """Ask whether to run one step. Returns True to run, False to skip,
    None to quit the whole pipeline. Enter picks the default: yes when the
    step is ready, no when prerequisites are missing."""
    label = f"{step} {' '.join(step_args)}".strip()
    hint = "[Y/n/q]" if default_yes else "[y/N/q]"
    while True:
        try:
            answer = input(f"\nRun {label}? {hint} ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            # Terminal closed or Ctrl-C - treat as quit.
            print()
            return None
        if answer == "":
            return default_yes
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        if answer in ("q", "quit"):
            return None
        print("Please answer y (yes), n (no), or q (quit).")


def print_check_report():
    print("Pre-flight check (nothing runs):")
    print(f"  environment source: {env.env_source()}\n")
    for step, _ in STEPS:
        ready, blockers, notes = check_step(step)
        print(f"  {step}: {format_status(ready, blockers)}")
        for b in blockers:
            print(f"      x {b}")
        for n in notes:
            print(f"      i {n}")


def main(argv=None):
    args = parse_args(argv)

    if args.list:
        for step, _ in STEPS:
            print(step)
        return

    if args.check:
        print_check_report()
        return

    # Prompt only when there is a human at a terminal. In CI (GitHub
    # Actions) or when output is piped there is no TTY, so input() would
    # hang or raise EOFError - just run everything instead.
    interactive = sys.stdin.isatty() and not args.yes

    if interactive:
        print("Interactive mode: prerequisites are checked before each step. "
              "y = run, n = skip, q = stop. (Ctrl-C also stops.)")

    ran, skipped = [], []
    stopped = False
    for step, step_args in STEPS:
        path = os.path.join(SCRIPTS_DIR, step)

        if interactive:
            ready, blockers, notes = check_step(step)
            print(f"\n--- {step}: {format_status(ready, blockers)}")
            for b in blockers:
                print(f"    x {b}")
            for n in notes:
                print(f"    i {n}")

            choice = confirm_step(step, step_args, default_yes=ready)
            if choice is None:
                print("\nStopped by user.")
                stopped = True
                break
            if not choice:
                print(f"--- Skipping {step}")
                skipped.append(step)
                continue

        label = f"{step} {' '.join(step_args)}".strip()
        print(f"\n=== Running {label} ===")
        result = subprocess.run([sys.executable, path] + step_args)
        if result.returncode != 0:
            print(f"!!! {label} failed, stopping pipeline.")
            sys.exit(result.returncode)
        ran.append(step)

    if not ran:
        print("\nNo steps ran.")
        return

    summary = f"\n=== Completed {len(ran)} step(s): {', '.join(ran)} ==="
    if skipped:
        summary += f"\n=== Skipped {len(skipped)}: {', '.join(skipped)} ==="
    if stopped:
        summary += "\n=== Pipeline stopped by user - remaining steps not run ==="
    print(summary)

    if not skipped and not stopped:
        print("=== Pipeline complete: output/final.mp4 sent to Telegram ===")
    else:
        print("=== Pipeline finished (not fully complete - see above) ===")


if __name__ == "__main__":
    main()
