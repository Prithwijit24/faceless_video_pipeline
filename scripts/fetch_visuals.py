"""
Step 3: Generate a story-matched background image for each scene using free
AI image generation, with Pexels stock footage as an automatic fallback.

Free AI sources, tried in order per scene:
  1. Pollinations.ai        - no key needed at all, free (FLUX model). Fast,
                             so it is tried first for every scene.
  2. AI Horde (aihorde.net) - free community image API, rendered by volunteer
     workers. Needs a free HORDE_IMAGE_API_KEY (https://aihorde.net/register).
     Only used for the scenes Pollinations missed; those jobs are submitted at
     once and polled together, so one queue wait covers them all.
  3. Pexels stock video     - needs PEXELS_API_KEY (free tier).
  4. Pexels stock photo     - last resort (Ken Burns zoom applied later).

Prompts are built from the scene's actual narration text plus an optional
per-channel visual_style (config/channels.yaml), so backgrounds actually
match what's being said instead of relying on loose keyword search.

Reads: output/script.json, output/audio/durations.json, config/channels.yaml
Writes: output/visuals/scene_0.jpg, scene_1.jpg, ... (AI images)
        output/visuals/manifest.json

Optional env vars:
  HORDE_IMAGE_API_KEY - free key (aihorde.net). If unset, Horde is skipped and
                        Pollinations failures go straight to Pexels.
  HORDE_IMAGE_MODEL   - preferred model. Defaults to the least-loaded
                        photorealistic model with active workers.
  HORDE_TIMEOUT       - seconds to wait on the Horde queue before falling back
                        to Pexels (default 600).
  PEXELS_API_KEY      - only used as a fallback when AI generation fails.
"""
import env  # loads .env from the project root via python-dotenv (no-op if missing)

import os
import json
import time
import yaml
import requests
import subprocess
import image_safety

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(ROOT, "output")
VISUALS_DIR = os.path.join(OUTPUT_DIR, "visuals")
CONFIG_PATH = os.path.join(ROOT, "config", "channels.yaml")

HORDE_API_KEY = os.environ.get("HORDE_IMAGE_API_KEY")
HORDE_MODEL = os.environ.get("HORDE_IMAGE_MODEL")
try:
    HORDE_TIMEOUT = int(os.environ.get("HORDE_TIMEOUT", "600"))
except ValueError:
    HORDE_TIMEOUT = 600  # malformed env value - never block the pipeline
HORDE_BASE = "https://aihorde.net/api/v2"
# Horde requires multiples of 64, and under heavy demand it temporarily caps
# the max side per request (we've seen 1024, then 660x660, within an hour).
# Each submit walks this ladder down on a 403 until a size is accepted; all
# options are close enough to 9:16 for the Ken Burns pass.
HORDE_SIZE_LADDER = [(576, 1024), (512, 896), (448, 768), (384, 640)]
# Photorealistic models likely to be suitable for video backgrounds.
HORDE_CANDIDATES = [
    "Juggernaut XL",
    "ICBINP - I Can't Believe It's Not Photography",
    "Deliberate",
    "AlbedoBase XL (SDXL)",
]

PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY")
PEXELS_HEADERS = {"Authorization": PEXELS_API_KEY} if PEXELS_API_KEY else {}

DEFAULT_VISUAL_STYLE = (
    "cinematic, dramatic lighting, photorealistic, rich detail, "
    "vertical composition, no text, no watermark"
)

# Always appended to every image prompt. The image AIs (especially free
# ones) can drift toward nudity on dark/human-themed prompts - these tokens
# keep generations safe for work. Prompt wording alone is NOT a guarantee;
# hard safety is enforced by the API params below (safe=true on
# Pollinations, nsfw=false + censor_nsfw + trusted workers on AI Horde).
SAFETY_SUFFIX = (
    "STRICTLY SAFE FOR WORK, family friendly, suitable for all ages, "
    "prefer environments, landscapes, architecture, objects, artifacts, "
    "symbols, astronomy, nature, and abstract scenes; NO PEOPLE, NO HUMANS, "
    "NO FACES, NO BODIES, NO SKIN, no nudity, no exposed body parts, "
    "no underwear, no lingerie, no sexual content, no erotic or suggestive "
    "imagery, no obscene or vulgar imagery, no gore, no blood, no violence, "
    "no weapons, no drugs, no text, no watermark, professional cinematic "
    "photography"
)


# ---------------------------------------------------------------- helpers


def looks_like_image(path):
    """Cheap sanity check that a file is really an image (not an error page)."""
    if not os.path.exists(path) or os.path.getsize(path) < 1000:
        return False
    with open(path, "rb") as f:
        head = f.read(12)
    return (
        head.startswith(b"\xff\xd8\xff")          # JPEG
        or head.startswith(b"\x89PNG")            # PNG
        or (head.startswith(b"RIFF") and head[8:12] == b"WEBP")
    )


def build_image_prompt(scene, style):
    """Turn a scene into an image prompt that matches the story.
    Uses the scene's visual_keywords (English, safe-for-work by prompt
    instruction) rather than raw narration text - narration can contain
    words that make image models drift toward nudity."""
    keywords = scene.get("visual_keywords") or []
    if keywords:
        text = " ".join(keywords)
    else:
        text = " ".join(scene["text"].split())
    return f"{text} {style} {SAFETY_SUFFIX}"


# ---------------------------------------------------------------- AI Horde


def pick_horde_model():
    """Pick the least-loaded photorealistic model with active workers, or fall
    back to HORDE_IMAGE_MODEL (or the first candidate) if none are idle."""
    preferred = HORDE_MODEL or HORDE_CANDIDATES[0]
    try:
        r = requests.get(f"{HORDE_BASE}/status/models?type=image", timeout=30)
        r.raise_for_status()
        by_name = {m["name"]: m for m in r.json()}
    except requests.exceptions.RequestException:
        return preferred

    candidates = list(HORDE_CANDIDATES)
    if preferred not in candidates:
        candidates.insert(0, preferred)
    idle = [c for c in candidates if by_name.get(c, {}).get("count", 0) > 0]
    if not idle:
        return preferred
    idle.sort(key=lambda c: by_name[c].get("queued", 0))
    chosen = idle[0]
    if preferred and chosen != preferred:
        print(f"[fetch_visuals] horde: '{preferred}' busy - using '{chosen}'")
    return chosen


def submit_horde(prompt, model, seed):
    """Submit one image job, walking the size ladder down if the queue's
    heavy-demand cap tightens mid-run. Returns (job_id, size) or (None, None)."""
    for size in HORDE_SIZE_LADDER:
        payload = {
            "prompt": prompt,
            "params": {
                "width": size[0],
                "height": size[1],
                "steps": 20,
                "cfg_scale": 7.0,
                "seed": str(seed),  # the current horde API expects a string
                "n": 1,
            },
            "models": [model],
            # Strict safety: SFW-only workers, censor anything that slips
            # through, and restrict to vetted trusted workers.
            "nsfw": False,
            "censor_nsfw": True,
            "trusted_workers": True,
        }
        try:
            r = requests.post(
                f"{HORDE_BASE}/generate/async",
                json=payload,
                headers={"apikey": HORDE_API_KEY, "Content-Type": "application/json"},
                timeout=30,
            )
            if r.status_code in (200, 202):  # 202 Accepted = job created
                return r.json()["id"], size
            if r.status_code == 403:
                body_msg = ""
                try:
                    body_msg = r.json().get("message", "")
                except Exception:
                    pass
                if "for requests over" in body_msg or "heavy demand" in body_msg:
                    continue  # heavy-demand size cap - try the next smaller size
                print(f"  (horde submit failed: HTTP 403 - {body_msg[:120] or 'forbidden'})")
                return None, None
            detail = ""
            try:
                body = r.json()
                detail = " - " + " ".join(str(body.get("message", "")).split())[:120]
                errors = body.get("errors")
                if errors:
                    detail += " (" + ", ".join(f"{k}: {v}" for k, v in errors.items())[:120] + ")"
            except Exception:
                pass
            print(f"  (horde submit failed: HTTP {r.status_code}{detail})")
            return None, None
        except requests.exceptions.RequestException as e:
            status = e.response.status_code if e.response is not None else "?"
            print(f"  (horde submit failed: HTTP {status})")
            return None, None
        except Exception as e:
            print(f"  (horde submit failed: {type(e).__name__})")
            return None, None
    print("  (horde submit failed: all sizes rejected by heavy-demand cap)")
    return None, None


def fetch_horde_images(items):
    """Submit one Horde job per (scene_index, prompt, seed) item, then poll all
    jobs together until they finish or HORDE_TIMEOUT elapses. Returns
    {scene_index: image_path} for every image delivered in time."""
    results = {}
    model = pick_horde_model()
    jobs = {}
    for idx, prompt, seed in items:
        job_id, _size = submit_horde(prompt, model, seed)
        if job_id:
            jobs[idx] = job_id
    if not jobs:
        return results
    print(f"[fetch_visuals] horde: {len(jobs)} job(s) queued (model: {model}), "
          f"waiting up to {HORDE_TIMEOUT}s")
    deadline = time.time() + HORDE_TIMEOUT
    while jobs and time.time() < deadline:
        for idx in list(jobs):
            try:
                s = requests.get(
                    f"{HORDE_BASE}/generate/status/{jobs[idx]}",
                    headers={"apikey": HORDE_API_KEY},
                    timeout=30,
                ).json()
            except requests.exceptions.RequestException:
                continue  # transient network blip - retry next loop
            if s.get("faulted"):
                print(f"  (horde scene {idx} faulted, using fallback)")
                del jobs[idx]
                continue
            if not s.get("done"):
                continue
            gen = s["generations"][0]
            state = gen.get("state")
            del jobs[idx]
            try:
                if state not in ("ok", "r2"):
                    # e.g. "censored" - the horde substitutes a placeholder image
                    print(f"  (horde scene {idx}: generation {state}, using fallback)")
                    continue
                img_url = gen.get("r2") or gen["img"]
                out_path = os.path.join(VISUALS_DIR, f"scene_{idx}.jpg")
                img = requests.get(img_url, timeout=60)
                img.raise_for_status()
                with open(out_path, "wb") as f:
                    f.write(img.content)
                if looks_like_image(out_path):
                    safe, reasons = image_safety.check_image(out_path)
                    if safe:
                        results[idx] = out_path
                    else:
                        print(
                            f"  (horde scene {idx}: safety wall rejected "
                            f"{', '.join(reasons)}, using fallback)"
                        )
                        try:
                            os.remove(out_path)
                        except OSError:
                            pass
                else:
                    print(f"  (horde scene {idx}: bad image bytes, using fallback)")
            except Exception as e:
                print(f"  (horde scene {idx} download failed: {type(e).__name__})")
        if jobs and time.time() < deadline:
            time.sleep(10)
    if jobs:
        print(f"[fetch_visuals] horde: {len(jobs)} scene(s) still queued after "
              f"{HORDE_TIMEOUT}s - using fallbacks for those")
    return results


# ---------------------------------------------------------------- Pollinations


def generate_image_pollinations(prompt, out_path, seed):
    """Generate and immediately pass the image through the local safety wall."""
    try:
        url = "https://image.pollinations.ai/prompt/" + requests.utils.quote(prompt)
        r = requests.get(
            url,
            params={
                "width": 1080,
                "height": 1920,
                "model": "flux",
                "nologo": "true",
                "safe": "true",
                "seed": seed,
            },
            timeout=120,
        )
        r.raise_for_status()
        with open(out_path, "wb") as f:
            f.write(r.content)
        if not looks_like_image(out_path):
            return False

        safe, reasons = image_safety.check_image(out_path)
        if not safe:
            print(
                f"  (pollinations image rejected by safety wall: "
                f"{', '.join(reasons)})"
            )
            try:
                os.remove(out_path)
            except OSError:
                pass
            return False
        return True
    except requests.exceptions.RequestException as e:
        status = e.response.status_code if e.response is not None else "?"
        print(f"  (pollinations failed: HTTP {status})")
        return False
    except RuntimeError:
        # Safety detector unavailable: fail closed. Do not silently continue
        # with an unscreened image.
        raise
    except Exception as e:
        print(f"  (pollinations failed: {type(e).__name__})")
        return False


# ---------------------------------------------------------------- Pexels fallback


def search_video(query, min_duration):
    r = requests.get(
        "https://api.pexels.com/videos/search",
        headers=PEXELS_HEADERS,
        params={"query": query, "orientation": "portrait", "per_page": 5},
        timeout=30,
    )
    r.raise_for_status()
    results = r.json().get("videos", [])
    for v in results:
        if v.get("duration", 0) >= min_duration:
            # pick the highest-res portrait file available
            files = sorted(
                [f for f in v["video_files"] if f.get("width", 0) <= f.get("height", 99999)],
                key=lambda f: f.get("width", 0), reverse=True,
            )
            if files:
                return files[0]["link"]
    # fall back to first result regardless of duration (we'll loop it later)
    if results:
        files = sorted(results[0]["video_files"], key=lambda f: f.get("width", 0), reverse=True)
        if files:
            return files[0]["link"]
    return None


def search_photo(query):
    r = requests.get(
        "https://api.pexels.com/v1/search",
        headers=PEXELS_HEADERS,
        params={"query": query, "orientation": "portrait", "per_page": 3},
        timeout=30,
    )
    r.raise_for_status()
    photos = r.json().get("photos", [])
    if photos:
        return photos[0]["src"]["portrait"]
    return None


def download(url, out_path):
    r = requests.get(url, stream=True, timeout=60)
    r.raise_for_status()
    with open(out_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=8192):
            f.write(chunk)


def fetch_pexels_fallback(i, duration, keywords, channel_name, manifest):
    """Original stock-footage behavior, used only when AI generation fails."""
    if not PEXELS_API_KEY:
        raise SystemExit(
            f"[fetch_visuals] scene {i}: AI generation failed and PEXELS_API_KEY is not set, "
            "so there is no fallback. Set PEXELS_API_KEY (or HORDE_IMAGE_API_KEY) and try again."
        )
    found_video = None
    for kw in keywords:
        found_video = search_video(kw, duration)
        if found_video:
            break

    label = keywords[0] if keywords else channel_name
    if found_video:
        ext_path = os.path.join(VISUALS_DIR, f"scene_{i}.mp4")
        download(found_video, ext_path)
        try:
            safe, reasons = image_safety.check_video(ext_path)
        except Exception as exc:
            try:
                os.remove(ext_path)
            except OSError:
                pass
            raise SystemExit(
                f"[fetch_visuals] scene {i}: visual safety wall failed closed "
                f"while checking Pexels video: {type(exc).__name__}: {exc}"
            )
        if safe:
            manifest.append({"index": i, "type": "video", "path": ext_path, "source": "pexels"})
            print(f"[fetch_visuals] scene {i}: pexels video fallback ({label})")
            return
        print(
            f"[fetch_visuals] scene {i}: Pexels video rejected by safety wall "
            f"({', '.join(reasons)}); trying photo fallback"
        )
        try:
            os.remove(ext_path)
        except OSError:
            pass

    photo_url = None
    for kw in keywords:
        photo_url = search_photo(kw)
        if photo_url:
            break
    if not photo_url:
        photo_url = search_photo(channel_name)
    if not photo_url:
        raise SystemExit(
            f"[fetch_visuals] scene {i}: AI generation and Pexels search all came back empty. "
            "Try again later."
        )
    img_path = os.path.join(VISUALS_DIR, f"scene_{i}.jpg")
    download(photo_url, img_path)
    try:
        safe, reasons = image_safety.check_image(img_path)
    except Exception as exc:
        try:
            os.remove(img_path)
        except OSError:
            pass
        raise SystemExit(
            f"[fetch_visuals] scene {i}: visual safety wall failed closed "
            f"while checking Pexels photo: {type(exc).__name__}: {exc}"
        )
    if not safe:
        try:
            os.remove(img_path)
        except OSError:
            pass
        raise SystemExit(
            f"[fetch_visuals] scene {i}: Pexels photo rejected by safety wall "
            f"({', '.join(reasons)}). No unsafe visual will be published."
        )
    manifest.append({"index": i, "type": "photo", "path": img_path, "source": "pexels"})
    print(f"[fetch_visuals] scene {i}: pexels photo fallback ({label})")


# ---------------------------------------------------------------- main


def main():
    with open(os.path.join(OUTPUT_DIR, "script.json")) as f:
        script = json.load(f)
    with open(os.path.join(OUTPUT_DIR, "audio", "durations.json")) as f:
        durations = json.load(f)
    with open(CONFIG_PATH) as f:
        cfg = yaml.safe_load(f)

    channel = next((c for c in cfg["channels"] if c["id"] == script["channel_id"]), {})
    style = channel.get("visual_style") or DEFAULT_VISUAL_STYLE
    os.makedirs(VISUALS_DIR, exist_ok=True)
    manifest = []

    scenes = script["scenes"]
    prompts = [build_image_prompt(scene, style) for scene in scenes]
    seeds = [1000 + i for i in range(len(scenes))]

    # 1) Pollinations first - fast and free, no key needed.
    # Every candidate is screened locally. Unsafe candidates are discarded and
    # regenerated with a different seed; they never enter the manifest.
    try:
        visual_retries = max(1, int(os.environ.get("VISUAL_RETRIES", "3")))
    except ValueError:
        visual_retries = 3

    pending = []
    for i in range(len(scenes)):
        img_path = os.path.join(VISUALS_DIR, f"scene_{i}.jpg")
        accepted = False
        for attempt in range(visual_retries):
            if generate_image_pollinations(
                prompts[i], img_path, seeds[i] + attempt * 7919
            ):
                manifest.append({
                    "index": i,
                    "type": "photo",
                    "path": img_path,
                    "source": "pollinations",
                })
                print(
                    f"[fetch_visuals] scene {i}: AI image (pollinations, "
                    f"attempt {attempt + 1}/{visual_retries})"
                )
                accepted = True
                break
        if not accepted:
            pending.append(i)

    # 2) AI Horde for the scenes Pollinations missed - all submitted at once,
    #    so one shared queue wait covers them.
    if pending and HORDE_API_KEY:
        horde_imgs = fetch_horde_images([(i, prompts[i], seeds[i]) for i in pending])
        for i in list(horde_imgs):  # horde_imgs keys are always a subset of pending
            manifest.append({"index": i, "type": "photo", "path": horde_imgs[i], "source": "horde"})
            print(f"[fetch_visuals] scene {i}: AI image (horde)")
            pending.remove(i)
    elif pending:
        print("[fetch_visuals] HORDE_IMAGE_API_KEY not set - skipping horde for failed scenes")

    # 3) Pexels stock as the last resort.
    for i in pending:
        scene = scenes[i]
        keywords = scene.get("visual_keywords", [script["channel_name"]])
        fetch_pexels_fallback(i, durations[i], keywords, script["channel_name"], manifest)

    manifest.sort(key=lambda m: m["index"])
    with open(os.path.join(VISUALS_DIR, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)

    print("[fetch_visuals] done")


if __name__ == "__main__":
    main()
