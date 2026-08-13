"""
Step 6: Assemble the final vertical video.

For each scene: scale/crop the visual to fill 1080x1920 and trim/loop it to
match that scene's voiceover duration (photos get a slow Ken Burns zoom).
A 5-second HOOK clip is prepended (the script's hook line spoken over scene
0's visual with the hook text shown big on screen - Devanagari-safe, burned
via libass for proper Hindi text shaping). Then all clips are concatenated,
the voiceover is mixed with the background music bed (sidechain ducking: the
music automatically lowers while the narrator speaks), and Hindi subtitles
are burned in from the .srt file.

Reads: output/script.json, output/audio/durations.json, output/audio/full.mp3,
       output/audio/hook.mp3, output/music/bed.mp3, output/visuals/manifest.json,
       output/subtitles.srt, config/channels.yaml
Writes: output/final.mp4
"""
import env  # loads .env from the project root via python-dotenv (no-op if missing)

import os
import json
import yaml
import image_safety
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(ROOT, "output")
VISUALS_DIR = os.path.join(OUTPUT_DIR, "visuals")
CLIPS_DIR = os.path.join(OUTPUT_DIR, "clips")
CONFIG_PATH = os.path.join(ROOT, "config", "channels.yaml")


def run(cmd):
    subprocess.run(cmd, check=True, capture_output=True)


def ffprobe_duration(path):
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", path,
        ],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


def normalize_video_clip(src, duration, out_path, w, h):
    # Loop the source if it's shorter than needed, then trim to exact duration,
    # scale+crop to fill the target frame (no black bars), strip audio.
    vf = (
        f"scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},fps=30,format=yuv420p"
    )
    run([
        "ffmpeg", "-y",
        "-stream_loop", "-1", "-i", src,
        "-t", str(duration),
        "-vf", vf,
        "-an",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        out_path,
    ])


def normalize_photo_clip(src, duration, out_path, w, h):
    # Slow zoom-in (Ken Burns) on a still photo for the scene's duration.
    zoom_frames = int(duration * 30)
    vf = (
        f"scale={w * 2}:{h * 2},"
        f"zoompan=z='min(zoom+0.0008,1.3)':d={zoom_frames}:s={w}x{h}:fps=30,"
        f"format=yuv420p"
    )
    run([
        "ffmpeg", "-y", "-loop", "1", "-i", src,
        "-t", str(duration),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        out_path,
    ])


def normalize_visual(item, duration, out_path, w, h):
    if item["type"] == "video":
        normalize_video_clip(item["path"], duration, out_path, w, h)
    else:
        normalize_photo_clip(item["path"], duration, out_path, w, h)


def ass_timestamp(seconds):
    s = int(seconds)
    cs = int(round((seconds - s) * 100))
    return f"0:{s // 60:02d}:{s % 60:02d}.{cs:02d}"


def wrap_hook(text, words_per_line=6):
    words = text.split()
    lines = [" ".join(words[i:i + words_per_line]) for i in range(0, len(words), words_per_line)]
    return "\\N".join(lines)


def build_hook_ass(hook_text, duration, out_path):
    # Devanagari needs real text shaping, so the hook is burned as an .ass
    # overlay through libass (HarfBuzz) instead of drawtext.
    body = wrap_hook(hook_text.replace(",", " ").replace(":", " "))
    ass = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Hook,Noto Sans Devanagari,76,&H00FFFFFF,&H000000FF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,4,1,5,40,40,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,{ass_timestamp(0)},{ass_timestamp(duration)},Hook,,0,0,0,,{{\\fad(250,300)}}{body}
"""
    with open(out_path, "w") as f:
        f.write(ass)


def concat_clips(clip_paths, out_path):
    list_file = os.path.join(CLIPS_DIR, "concat_list.txt")
    with open(list_file, "w") as f:
        for p in clip_paths:
            f.write(f"file '{os.path.abspath(p)}'\n")
    run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file,
        "-c", "copy", out_path,
    ])


def mix_audio(video_path, voiceover_path, music_path, music_volume, out_path, voice_volume=1.8, target_lufs=-14):
    """
    Mix a deliberately forward voiceover with ducked music.

    The voice is compressed and given a modest gain before the final loudness
    pass. This avoids the common failure mode where a long reel with pauses
    measures "normal" integrated LUFS but the spoken words still sound quiet.
    """
    try:
        voice_volume = float(os.environ.get("VOICE_VOLUME", str(voice_volume)))
    except ValueError:
        voice_volume = 1.8

    try:
        target_lufs = float(os.environ.get("TARGET_LUFS", str(target_lufs)))
    except ValueError:
        target_lufs = -14.0

    if music_path and os.path.exists(music_path):
        # Keep the bed deliberately quiet. Sidechain compression ducks it
        # further whenever narration is present.
        fc = (
            "[1:a]aresample=44100,"
            "highpass=f=70,"
            "acompressor=threshold=-20dB:ratio=3:attack=5:release=90:makeup=2,"
            f"volume={voice_volume}[vo];"
            "[vo]asplit=2[vo1][vo2];"
            f"[2:a]volume={music_volume}[m];"
            "[m][vo1]sidechaincompress=threshold=0.025:ratio=10:attack=10:"
            "release=300[duck];"
            "[vo2][duck]amix=inputs=2:duration=first:normalize=0,"
            f"loudnorm=I={target_lufs}:TP=-1.5:LRA=7[a]"
        )
        inputs = ["-i", video_path, "-i", voiceover_path, "-i", music_path]
    else:
        fc = (
            "[1:a]aresample=44100,"
            "highpass=f=70,"
            "acompressor=threshold=-20dB:ratio=3:attack=5:release=90:makeup=2,"
            f"volume={voice_volume},"
            f"loudnorm=I={target_lufs}:TP=-1.5:LRA=7[a]"
        )
        inputs = ["-i", video_path, "-i", voiceover_path]

    run([
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", fc,
        "-map", "0:v", "-map", "[a]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        out_path,
    ])


def burn_subtitles(video_path, srt_path, out_path):
    # Noto Sans Devanagari renders Hindi captions correctly; force_style keeps
    # them big, bold, and centered near the bottom - standard for
    # TikTok/Reels/Shorts readability.
    style = (
        "FontName=Noto Sans Devanagari,FontSize=13,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=0,"
        "Alignment=2,MarginV=80,Bold=1"
    )
    srt_escaped = srt_path.replace(":", "\\:")
    run([
        "ffmpeg", "-y", "-i", video_path,
        "-vf", f"subtitles={srt_escaped}:force_style='{style}'",
        "-c:a", "copy",
        out_path,
    ])


def main():
    with open(os.path.join(OUTPUT_DIR, "script.json")) as f:
        script = json.load(f)
    with open(os.path.join(OUTPUT_DIR, "audio", "durations.json")) as f:
        durations = json.load(f)
    with open(os.path.join(VISUALS_DIR, "manifest.json")) as f:
        manifest = json.load(f)
    with open(CONFIG_PATH) as f:
        cfg = yaml.safe_load(f)

    w, h = (int(x) for x in cfg["video"]["resolution"].split("x"))
    os.makedirs(CLIPS_DIR, exist_ok=True)

    clip_paths = []
    for item in manifest:
        i = item["index"]
        duration = durations[i]

        # Belt-and-braces gate: even stale assets from an older run must pass
        # the local safety wall before they can be assembled.
        try:
            if item["type"] == "video":
                image_safety.require_safe_video(item["path"], f"scene {i}")
            else:
                image_safety.require_safe_image(item["path"], f"scene {i}")
        except Exception as exc:
            raise SystemExit(
                f"[assemble_video] SAFETY WALL BLOCKED scene {i}: {exc}"
            ) from exc

        out_path = os.path.join(CLIPS_DIR, f"scene_{i}.mp4")
        normalize_visual(item, duration, out_path, w, h)
        clip_paths.append(out_path)
        print(f"[assemble_video] normalized scene {i}")

    # 5-second hook: scene 0's visual + the hook line shown big on screen.
    hook_path = os.path.join(OUTPUT_DIR, "audio", "hook.mp3")
    hook_clip = None
    if os.path.exists(hook_path) and script.get("hook") and manifest:
        hook_dur = ffprobe_duration(hook_path)
        hook_base = os.path.join(CLIPS_DIR, "hook_base.mp4")
        normalize_visual(manifest[0], hook_dur, hook_base, w, h)
        hook_ass = os.path.join(OUTPUT_DIR, "hook.ass")
        build_hook_ass(script["hook"], hook_dur, hook_ass)
        hook_clip = os.path.join(CLIPS_DIR, "hook.mp4")
        ass_escaped = hook_ass.replace(":", "\\:")
        run([
            "ffmpeg", "-y", "-i", hook_base,
            "-vf", f"ass={ass_escaped}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            hook_clip,
        ])
        print(f"[assemble_video] hook clip ({hook_dur:.2f}s)")
        clip_paths = [hook_clip] + clip_paths

    concat_path = os.path.join(OUTPUT_DIR, "concat.mp4")
    concat_clips(clip_paths, concat_path)

    music_bed = os.path.join(OUTPUT_DIR, "music", "bed.mp3")
    music_volume = cfg["video"].get("music_volume", 0.12)
    voice_volume = cfg["video"].get("voice_volume", 1.8)
    target_lufs = cfg["video"].get("target_lufs", -14)
    muxed_path = os.path.join(OUTPUT_DIR, "muxed.mp4")
    mix_audio(
        concat_path,
        os.path.join(OUTPUT_DIR, "audio", "full.mp3"),
        music_bed,
        music_volume,
        muxed_path,
        voice_volume=voice_volume,
        target_lufs=target_lufs,
    )

    # Screen the assembled video before it is ever considered publishable.
    try:
        image_safety.require_safe_video(muxed_path, "assembled video")
    except Exception as exc:
        try:
            os.remove(muxed_path)
        except OSError:
            pass
        raise SystemExit(
            f"[assemble_video] SAFETY WALL BLOCKED assembled video: {exc}"
        ) from exc

    final_path = os.path.join(OUTPUT_DIR, "final.mp4")
    srt_path = os.path.join(OUTPUT_DIR, "subtitles.srt")
    if os.path.exists(srt_path):
        burn_subtitles(muxed_path, srt_path, final_path)
    else:
        os.replace(muxed_path, final_path)

    # Final publish gate. No final.mp4 survives if any sampled frame is unsafe.
    try:
        image_safety.require_safe_video(final_path, "final video")
    except Exception as exc:
        try:
            os.remove(final_path)
        except OSError:
            pass
        raise SystemExit(
            f"[assemble_video] SAFETY WALL BLOCKED final.mp4: {exc}"
        ) from exc

    print(f"[assemble_video] wrote {final_path}")


if __name__ == "__main__":
    main()
