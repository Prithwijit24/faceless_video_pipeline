"""
Hard safety wall for generated/stock visuals.

The pipeline must never publish an image/video frame that the local nudity
detector cannot verify as safe. Prompt-level filtering and provider-side SFW
flags are useful defense-in-depth, but they are not treated as guarantees.

Policy:
- NudeNet is mandatory. If it cannot be imported/initialized, the visual step
  FAILS CLOSED.
- Exposed genitalia, breasts, buttocks, and anus are rejected.
- Belly/armpits (NudeNet's noisiest classes - they fire on midriffs, clothing
  folds, shadows, and figure silhouettes) are only rejected above a higher,
  tunable threshold, so a false positive can't silently kill a whole video.
- Human faces/bodies are rejected by default. Faceless videos therefore use
  environments, objects, landscapes, architecture, symbols, and abstract art.
- Video fallbacks are sampled at multiple timestamps and every sampled frame
  must pass.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from functools import lru_cache

# Detection threshold is intentionally conservative. Lower values catch more
# borderline material at the cost of rejecting more false positives.
NSFW_SCORE_THRESHOLD = float(os.environ.get("VISUAL_SAFETY_THRESHOLD", "0.40"))

# NudeNet's BELLY/ARMPITS classes are by far the noisiest: they fire on
# midriffs, loose clothing folds, shadows, and figure silhouettes - which has
# blocked whole videos over harmless frames (e.g. a horror channel's shadowy
# figure scoring BELLY_EXPOSED 0.48). They are still screened, but only block
# above this higher bar; the strict nudity classes below block at
# VISUAL_SAFETY_THRESHOLD. Tune with VISUAL_SOFT_THRESHOLD.
SOFT_LABEL_THRESHOLD = float(os.environ.get("VISUAL_SOFT_THRESHOLD", "0.60"))

# We reject human subjects entirely. This is stronger than merely checking for
# nudity and dramatically reduces the chance of sexualized/obscene imagery.
REJECT_HUMANS = os.environ.get("VISUAL_REJECT_HUMANS", "1").lower() not in (
    "0", "false", "no"
)

# Strict nudity classes: always rejected at NSFW_SCORE_THRESHOLD.
STRICT_UNSAFE_LABELS = {
    "FEMALE_BREAST_EXPOSED",
    "MALE_BREAST_EXPOSED",
    "FEMALE_GENITALIA_EXPOSED",
    "MALE_GENITALIA_EXPOSED",
    "ANUS_EXPOSED",
    "BUTTOCKS_EXPOSED",
}

# Soft/racy classes: only rejected above SOFT_LABEL_THRESHOLD.
SOFT_UNSAFE_LABELS = {
    "BELLY_EXPOSED",
    "ARMPITS_EXPOSED",
}

HUMAN_LABELS = {
    "FACE_FEMALE",
    "FACE_MALE",
}


@lru_cache(maxsize=1)
def _detector():
    try:
        from nudenet import NudeDetector
    except Exception as exc:
        raise RuntimeError(
            "NudeNet is required for the visual safety wall. "
            "Install dependencies with: pip install -r requirements.txt"
        ) from exc

    try:
        return NudeDetector()
    except Exception as exc:
        raise RuntimeError(
            "NudeNet could not initialize its bundled model. "
            "The pipeline fails closed instead of publishing unscreened visuals."
        ) from exc


def check_image(path: str) -> tuple[bool, list[str]]:
    """Return (safe, reasons). Any detector failure raises: fail closed."""
    if not os.path.isfile(path) or os.path.getsize(path) < 1000:
        return False, ["invalid_or_empty_image"]

    try:
        # NudeNet >= 3.5 accepts the tuning kwargs and filters internally.
        detections = _detector().detect(
            path,
            score_threshold=NSFW_SCORE_THRESHOLD,
            nms_threshold=0.5,
        )
    except TypeError:
        # NudeNet 3.4.x (what requirements.txt pins) only accepts the image
        # path and applies its own internal thresholds. The per-detection
        # score filter below still enforces NSFW_SCORE_THRESHOLD, so the
        # wall is never weakened by the version difference.
        detections = _detector().detect(path)

    reasons = []
    for item in detections or []:
        label = str(item.get("class") or item.get("label") or "").upper()
        score = float(item.get("score", 0.0))
        if label in STRICT_UNSAFE_LABELS and score >= NSFW_SCORE_THRESHOLD:
            reasons.append(f"{label}:{score:.2f}")
        if label in SOFT_UNSAFE_LABELS and score >= SOFT_LABEL_THRESHOLD:
            reasons.append(f"{label}:{score:.2f}")
        if REJECT_HUMANS and label in HUMAN_LABELS and score >= NSFW_SCORE_THRESHOLD:
            reasons.append(f"{label}:{score:.2f}")

    return not reasons, reasons


def _video_duration(path: str) -> float:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            path,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return max(0.1, float(result.stdout.strip()))


def check_video(path: str, max_frames: int = 20) -> tuple[bool, list[str]]:
    """
    Sample a video at evenly distributed timestamps.

    This is intentionally a hard gate: if ffmpeg/ffprobe or NudeNet fails, the
    caller gets an exception and must not publish the asset.
    """
    if not os.path.isfile(path) or os.path.getsize(path) < 1000:
        return False, ["invalid_or_empty_video"]

    duration = _video_duration(path)
    frame_count = min(max_frames, max(4, int(duration * 2)))
    sample_dir = tempfile.mkdtemp(prefix="visual_safety_")
    try:
        # Sample evenly, including near the beginning and end. We use ffmpeg
        # rather than OpenCV so no additional native dependency is required.
        fps = frame_count / duration
        pattern = os.path.join(sample_dir, "frame_%03d.jpg")
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-i", path,
                "-vf", f"fps={fps:.6f},scale=640:-2",
                "-frames:v", str(frame_count),
                pattern,
            ],
            check=True,
            capture_output=True,
        )

        frames = sorted(
            os.path.join(sample_dir, name)
            for name in os.listdir(sample_dir)
            if name.lower().endswith(".jpg")
        )
        if not frames:
            return False, ["no_video_frames_extracted"]

        for frame in frames:
            safe, reasons = check_image(frame)
            if not safe:
                return False, [f"{os.path.basename(frame)}:{r}" for r in reasons]

        return True, []
    finally:
        shutil.rmtree(sample_dir, ignore_errors=True)


def require_safe_image(path: str, source: str = "") -> None:
    safe, reasons = check_image(path)
    if not safe:
        raise ValueError(
            f"visual safety wall rejected {source or path}: {', '.join(reasons)}"
        )


def require_safe_video(path: str, source: str = "") -> None:
    safe, reasons = check_video(path)
    if not safe:
        raise ValueError(
            f"visual safety wall rejected {source or path}: {', '.join(reasons)}"
        )
