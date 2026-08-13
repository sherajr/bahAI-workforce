"""
Videographer — local video generation via a ComfyUI server (LTX-Video / Wan 2.2).

ComfyUI itself is a separate, manually-launched local process (portable
install outside this repo, started via its own Desktop shortcut) — this module
is purely an HTTP client against its API and makes no filesystem assumptions
about where ComfyUI lives, so it works against any reachable ComfyUI instance,
not just the one on this machine.

Two backends, both verified working end-to-end 2026-08-12:
- "ltx"   — fast (~20-40s/clip), lower quality. Text-to-video OR image-to-video.
- "wan22" — slower (~2-5min/clip on an 8GB card), noticeably better motion.
            Image-to-video only: no text-to-video graph is wired up here
            because that path was never verified working.

Used directly as a callable building block, and as the backend behind
`video_provider.ComfyUIProvider` for the Video Generation pipeline.
"""

import json
import os
import time
import uuid
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=str(Path(__file__).parent.parent / ".env"))

COMFYUI_URL = os.getenv("COMFYUI_URL", "http://127.0.0.1:8188")

OUTPUTS_DIR = Path(__file__).parent.parent / "outputs"
OUTPUTS_DIR.mkdir(exist_ok=True)

LTX_CHECKPOINT = "ltxv-2b-0.9.8-distilled-fp8.safetensors"
LTX_TEXT_ENCODER = "t5xxl_fp8_e4m3fn_scaled.safetensors"

WAN_DIFFUSION_MODEL = "wan2.2_ti2v_5B_fp16.safetensors"
WAN_TEXT_ENCODER = "umt5_xxl_fp8_e4m3fn_scaled.safetensors"
WAN_VAE = "wan2.2_vae.safetensors"

DEFAULT_NEGATIVE_LTX = (
    "low quality, worst quality, deformed, distorted, disfigured, motion smear, "
    "motion artifacts, fused fingers, bad anatomy, weird hand, ugly"
)
DEFAULT_NEGATIVE_WAN = (
    "blurry, low quality, distorted, deformed, static, still, jpeg artifacts, "
    "ugly, watermark, text"
)

# Native first-and-last-frame conditioning: NOT available on either local
# model, and the reason matters because the naive check says otherwise.
#
# ComfyUI ships `WanFirstLastFrameToVideo`, and wiring it to Wan 2.2 TI2V-5B
# SUBMITS AND RUNS CLEANLY — no node error, no runtime error, a real mp4 lands
# in 21s. The output is nonetheless corrupted garbage (probe, 2026-08-12: mean
# abs difference from the conditioning image 97/255 vs ~23 for a working i2v
# run, brightness 186 vs the source's 114, visually a smeared colour field).
# That node targets the Wan 2.1 FLF2V architecture; the 2.2 VAE's latent
# format doesn't match. Node presence and a zero exit code BOTH report
# "supported" here — only inspecting pixels reveals the truth, which is
# exactly why this is hardcoded from an empirical probe rather than detected
# from the node list at runtime. Re-probe before flipping it.
FLF_SUPPORT: dict[str, tuple[bool, str]] = {
    "ltx": (False, "LTX-Video's keyframe guide (LTXVAddGuide) is not wired up in this "
                   "integration — only start-frame conditioning is verified."),
    "wan22": (False, "Wan 2.2 TI2V-5B has no working end-frame conditioning: ComfyUI's "
                     "WanFirstLastFrameToVideo runs without error against it but "
                     "produces corrupted output (verified by probe, 2026-08-12)."),
}


class VideoGenerationError(RuntimeError):
    pass


class VideoCancelled(VideoGenerationError):
    """Raised when a caller's should_cancel() asked to stop mid-generation."""


def is_server_up(timeout: float = 3.0) -> bool:
    try:
        requests.get(f"{COMFYUI_URL}/system_stats", timeout=timeout).raise_for_status()
        return True
    except requests.RequestException:
        return False


def supports_first_last_frame(model: str) -> tuple[bool, str]:
    """(supported, explanation) — see FLF_SUPPORT for why this is hardcoded."""
    return FLF_SUPPORT.get(model, (False, "Unknown model."))


def frames_for_seconds(seconds: float, model: str = "ltx", fps: int = 24) -> int:
    """
    Frame count for a duration, honouring each model's constraint. LTX needs
    8n+1 frames (a wrong count fails or produces a broken clip); Wan is
    tolerant but is kept on a multiple of 4 plus one.
    """
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        seconds = 3.5
    seconds = min(6.0, max(1.0, seconds))
    raw = int(round(seconds * fps))
    step = 8 if model == "ltx" else 4
    n = max(1, round((raw - 1) / step))
    return n * step + 1


def _upload_image(image_path: Path) -> str:
    """Upload an image into ComfyUI's input store; returns the filename ComfyUI assigned it."""
    with open(image_path, "rb") as f:
        files = {"image": (image_path.name, f, "image/png")}
        resp = requests.post(
            f"{COMFYUI_URL}/upload/image", files=files, data={"overwrite": "true"}, timeout=60
        )
    resp.raise_for_status()
    return resp.json()["name"]


def _interrupt():
    try:
        requests.post(f"{COMFYUI_URL}/interrupt", timeout=10)
    except requests.RequestException:
        pass


def _submit_and_wait(prompt_graph: dict, timeout_s: int, progress=None,
                     should_cancel=None) -> dict:
    client_id = uuid.uuid4().hex
    resp = requests.post(
        f"{COMFYUI_URL}/prompt",
        json={"prompt": prompt_graph, "client_id": client_id},
        timeout=30,
    )
    if resp.status_code != 200:
        raise VideoGenerationError(f"ComfyUI rejected the workflow: {resp.text[:800]}")
    data = resp.json()
    if data.get("node_errors"):
        raise VideoGenerationError(
            f"ComfyUI workflow has node errors: {json.dumps(data['node_errors'])[:1500]}")
    prompt_id = data["prompt_id"]

    deadline = time.time() + timeout_s
    last_note = 0.0
    while time.time() < deadline:
        if should_cancel and should_cancel():
            _interrupt()
            raise VideoCancelled("Generation cancelled.")
        hist = requests.get(f"{COMFYUI_URL}/history/{prompt_id}", timeout=30).json()
        entry = hist.get(prompt_id)
        if entry:
            status = entry.get("status", {})
            if status.get("status_str") == "error":
                raise VideoGenerationError(
                    f"ComfyUI run failed: {json.dumps(status)[:1500]}")
            if entry.get("outputs"):
                return entry["outputs"]
        if progress and time.time() - last_note > 15:
            last_note = time.time()
            progress(f"rendering... {int(time.time() - (deadline - timeout_s))}s elapsed")
        time.sleep(2)
    _interrupt()
    raise VideoGenerationError(
        f"Timed out after {timeout_s}s waiting for ComfyUI (prompt_id={prompt_id})")


def _fetch_output_video(outputs: dict, save_node_id: str) -> bytes:
    # ComfyUI's SaveVideo node reports its file under the "images" key, same
    # slot used for still-image previews — this is ComfyUI's own convention,
    # not a bug in this module.
    node_out = outputs.get(save_node_id)
    if not node_out or not node_out.get("images"):
        raise VideoGenerationError(f"No video output found on node {save_node_id}: {outputs}")
    vid = node_out["images"][0]
    resp = requests.get(
        f"{COMFYUI_URL}/view",
        params={"filename": vid["filename"], "subfolder": vid.get("subfolder", ""),
                "type": vid.get("type", "output")},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.content


def _save_video_locally(video_bytes: bytes, prefix: str) -> Path:
    out_path = OUTPUTS_DIR / f"{prefix}-{uuid.uuid4().hex[:8]}.mp4"
    out_path.write_bytes(video_bytes)
    return out_path


def extract_last_frame(video_path: str | Path, dest: str | Path | None = None) -> Path | None:
    """
    Pull the real final frame out of a generated clip. This is what makes the
    `chain_extract` continuity strategy honest: rather than assuming the clip
    ended on the planned last frame, the NEXT shot starts from the frame that
    actually got rendered. Returns None if PyAV isn't available (the caller
    then falls back to the planned frame and says so).
    """
    try:
        import av  # noqa: F401  (optional dependency; see requirements.txt)
    except ImportError:
        return None
    import av
    src = str(video_path)
    out = Path(dest) if dest else OUTPUTS_DIR / f"lastframe-{uuid.uuid4().hex[:8]}.png"
    try:
        container = av.open(src)
        frame = None
        for f in container.decode(video=0):
            frame = f
        container.close()
        if frame is None:
            return None
        frame.to_image().save(str(out))
        return out
    except Exception:
        return None


def _ltx_graph(positive: str, negative: str, *, image_filename: str | None,
               width: int, height: int, length: int, seed: int,
               image_strength: float | None = None) -> tuple[dict, str]:
    common = {
        "44": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": LTX_CHECKPOINT}},
        "38": {"class_type": "CLIPLoader",
               "inputs": {"clip_name": LTX_TEXT_ENCODER, "type": "ltxv", "device": "default"}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": positive, "clip": ["38", 0]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["38", 0]}},
    }
    if image_filename:
        common["78"] = {"class_type": "LoadImage", "inputs": {"image": image_filename}}
        common["77"] = {"class_type": "LTXVImgToVideo", "inputs": {
            "positive": ["6", 0], "negative": ["7", 0], "vae": ["44", 2], "image": ["78", 0],
            "width": width, "height": height, "length": length, "batch_size": 1,
            # 0.15 is the ComfyUI template's value and is right for a loose
            # "inspired by this image" animation. Chained generation passes 1.0
            # instead: there the input frame is the previous clip's actual final
            # frame, so the clip must START on it rather than drift away.
            "strength": 0.15 if image_strength is None else float(image_strength),
        }}
        cond_pos, cond_neg, latent = ["77", 0], ["77", 1], ["77", 2]
    else:
        common["70"] = {"class_type": "EmptyLTXVLatentVideo", "inputs": {
            "width": width, "height": height, "length": length, "batch_size": 1,
        }}
        cond_pos, cond_neg, latent = ["6", 0], ["7", 0], ["70", 0]

    common["69"] = {"class_type": "LTXVConditioning",
                    "inputs": {"positive": cond_pos, "negative": cond_neg, "frame_rate": 25}}
    common["71"] = {"class_type": "LTXVScheduler", "inputs": {
        "steps": 8, "max_shift": 2.05, "base_shift": 0.95, "stretch": True,
        # terminal MUST stay 0.1 — dropping it to 0.0 silently renders every
        # frame pure black (hit for real 2026-08-12, confirmed by A/B test).
        # Not exposed as a parameter on purpose.
        "terminal": 0.1,
        "latent": latent,
    }}
    common["73"] = {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}}
    common["72"] = {"class_type": "SamplerCustom", "inputs": {
        "model": ["44", 0], "add_noise": True, "noise_seed": seed, "cfg": 1.0,
        "positive": ["69", 0], "negative": ["69", 1], "sampler": ["73", 0],
        "sigmas": ["71", 0], "latent_image": latent,
    }}
    common["8"] = {"class_type": "VAEDecode", "inputs": {"samples": ["72", 0], "vae": ["44", 2]}}
    common["80"] = {"class_type": "CreateVideo", "inputs": {"images": ["8", 0], "fps": 24}}
    common["81"] = {"class_type": "SaveVideo", "inputs": {
        "video": ["80", 0], "filename_prefix": "api/LTX", "format": "auto", "codec": "auto",
    }}
    return common, "81"


def _wan22_graph(positive: str, negative: str, image_filename: str, *,
                 width: int, height: int, length: int, seed: int) -> tuple[dict, str]:
    graph = {
        "37": {"class_type": "UNETLoader",
               "inputs": {"unet_name": WAN_DIFFUSION_MODEL, "weight_dtype": "default"}},
        "38": {"class_type": "CLIPLoader",
               "inputs": {"clip_name": WAN_TEXT_ENCODER, "type": "wan", "device": "default"}},
        "39": {"class_type": "VAELoader", "inputs": {"vae_name": WAN_VAE}},
        "56": {"class_type": "LoadImage", "inputs": {"image": image_filename}},
        "55": {"class_type": "Wan22ImageToVideoLatent", "inputs": {
            "vae": ["39", 0], "start_image": ["56", 0], "width": width, "height": height,
            "length": length, "batch_size": 1,
        }},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": positive, "clip": ["38", 0]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["38", 0]}},
        "48": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["37", 0], "shift": 8.0}},
        "3": {"class_type": "KSampler", "inputs": {
            "model": ["48", 0], "positive": ["6", 0], "negative": ["7", 0],
            "latent_image": ["55", 0], "seed": seed, "steps": 20, "cfg": 5.0,
            "sampler_name": "uni_pc", "scheduler": "simple", "denoise": 1.0,
        }},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["39", 0]}},
        "57": {"class_type": "CreateVideo", "inputs": {"images": ["8", 0], "fps": 24}},
        "58": {"class_type": "SaveVideo", "inputs": {
            "video": ["57", 0], "filename_prefix": "api/Wan22", "format": "auto", "codec": "auto",
        }},
    }
    return graph, "58"


def generate_video(
    prompt: str,
    *,
    image_path: str | Path | None = None,
    last_frame_path: str | Path | None = None,
    model: str = "ltx",
    negative_prompt: str | None = None,
    width: int | None = None,
    height: int | None = None,
    length: int | None = None,
    seed: int | None = None,
    image_strength: float | None = None,
    timeout_s: int = 900,
    progress=None,
    should_cancel=None,
) -> dict:
    """
    Generate a short local video via ComfyUI. Raises VideoGenerationError if
    the ComfyUI server isn't reachable or the run fails, VideoCancelled if the
    caller asked to stop.

    `last_frame_path` is accepted for interface parity with providers that
    support end-frame conditioning, but NEITHER local model does (see
    FLF_SUPPORT). It is recorded in the result as `last_frame_used: False` so
    the caller can be honest about it rather than implying it was applied.

    length must be 8n+1 for LTX; use frames_for_seconds() to get a valid one.

    Returns {"video_path": Path, "model", "seed", "last_frame_used", "strategy"}.
    """
    if model not in ("ltx", "wan22"):
        raise ValueError(f"Unknown model {model!r}; use 'ltx' or 'wan22'")
    if model == "wan22" and image_path is None:
        raise ValueError(
            "model='wan22' requires image_path — no verified text-to-video graph for Wan 2.2 here")

    if not is_server_up():
        raise VideoGenerationError(
            f"ComfyUI server not reachable at {COMFYUI_URL}. It runs as a separate local "
            "process (Desktop shortcut 'ComfyUI (LTX Video)') — launch it before calling this."
        )

    image_filename = _upload_image(Path(image_path)) if image_path else None
    seed = seed if seed is not None else uuid.uuid4().int % (2**32)

    if model == "ltx":
        graph, save_node = _ltx_graph(
            prompt, negative_prompt or DEFAULT_NEGATIVE_LTX, image_filename=image_filename,
            width=width or 768, height=height or 512,
            length=length or frames_for_seconds(4, "ltx"), seed=seed,
            image_strength=image_strength,
        )
    else:
        graph, save_node = _wan22_graph(
            prompt, negative_prompt or DEFAULT_NEGATIVE_WAN, image_filename,
            width=width or 832, height=height or 480,
            length=length or frames_for_seconds(3.5, "wan22"), seed=seed,
        )

    outputs = _submit_and_wait(graph, timeout_s, progress=progress, should_cancel=should_cancel)
    video_bytes = _fetch_output_video(outputs, save_node)
    video_path = _save_video_locally(video_bytes, prefix=model)
    return {
        "video_path": video_path,
        "model": model,
        "seed": seed,
        # Honest about what was actually applied — never implies end-frame
        # conditioning happened just because a path was passed in.
        "last_frame_used": False,
        "strategy": "image_to_video" if image_filename else "text_to_video",
    }
