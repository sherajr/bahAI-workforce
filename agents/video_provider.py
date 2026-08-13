"""
Provider adapter boundary for the Video Generation pipeline.

The pipeline never talks to a model directly — it asks a provider what it can
do, then picks a strategy. This is the smallest interface that supports the
owner spec's requirements: text-to-video, image-to-video, first-and-last-frame
conditioning where genuinely available, progress, cancellation, error
reporting, retry, and capability detection.

Honesty rule (explicit in the spec, and the same discipline as hard rules 4/8):
a provider must NEVER claim first-and-last-frame conditioning it does not
have. Capability here is DETECTED against the live backend — the node must
exist AND be verified usable with the configured model — and the chosen
fallback is returned to the caller and shown in the UI, never hidden.

Two providers ship:
  comfyui  — the local ComfyUI server (LTX-Video, Wan 2.2) via videographer.py
  mock     — a development provider that writes a real, clearly-marked
             placeholder file. It never pretends to be a generated clip:
             every asset it produces is labelled is_mock=True end to end.
"""

import os
import shutil
import uuid
from pathlib import Path

OUTPUTS_DIR = Path(__file__).parent.parent / "outputs"
OUTPUTS_DIR.mkdir(exist_ok=True)

# --- Strategies, in the spec's fallback order -------------------------------

STRATEGY_NATIVE_FLF = "native_flf"
STRATEGY_FIRST_FRAME = "first_frame_i2v"
STRATEGY_CHAIN_EXTRACT = "chain_extract"
STRATEGY_TEXT_ONLY = "text_to_video"

STRATEGY_LABELS = {
    STRATEGY_NATIVE_FLF: "Native first-and-last-frame conditioning",
    STRATEGY_FIRST_FRAME: "First-frame image-to-video (last frame used as a visual target only)",
    STRATEGY_CHAIN_EXTRACT: "First-frame image-to-video, real last frame extracted from the clip",
    STRATEGY_TEXT_ONLY: "Text-to-video (no frame conditioning available)",
}


class VideoProviderError(RuntimeError):
    pass


class ClipSpec:
    """One clip to generate. Deliberately plain so any backend can consume it."""

    def __init__(self, prompt: str, negative_prompt: str = "", first_frame: str | None = None,
                 last_frame: str | None = None, seconds: float = 3.5, seed: int | None = None,
                 width: int | None = None, height: int | None = None,
                 low_resource: bool = True, image_strength: float | None = None):
        self.prompt = prompt
        self.negative_prompt = negative_prompt
        self.first_frame = first_frame
        self.last_frame = last_frame
        self.seconds = seconds
        self.seed = seed
        self.width = width
        self.height = height
        self.low_resource = low_resource
        # How strictly the clip must start on `first_frame`. Chained generation
        # sets this to 1.0 because the handed-in frame IS the previous clip's
        # final frame — drifting off it is exactly the discontinuity chaining
        # exists to remove. Providers with no such control ignore it.
        self.image_strength = image_strength


class BaseVideoProvider:
    name = "base"
    label = "Base"

    def capabilities(self) -> dict:
        raise NotImplementedError

    def available(self) -> tuple[bool, str]:
        """(is_up, human-readable reason if not)."""
        raise NotImplementedError

    def generate_clip(self, spec: ClipSpec, progress=None, should_cancel=None) -> dict:
        raise NotImplementedError


# --- ComfyUI (local: LTX-Video + Wan 2.2) -----------------------------------

class ComfyUIProvider(BaseVideoProvider):
    """
    Wraps `agents.videographer`. Two models with different trade-offs on an
    8GB card: `ltx` is fast and rough, `wan22` is slower with better motion.
    """

    name = "comfyui"

    # Verified on an 8GB RTX 4070 (2026-08-12): these exact settings produced
    # correct, non-black output. The low-resource preset below stays at or
    # under them.
    MODEL_PRESETS = {
        "ltx": {
            "label": "LTX-Video 2B distilled (fast)",
            "width": 768, "height": 512, "fps": 24,
            "supports_image_to_video": True,
            "supports_text_to_video": True,
            "frame_multiple": 8,      # LTX needs 8n+1 frames
            "typical_seconds": 25,
        },
        "wan22": {
            "label": "Wan 2.2 TI2V-5B (better motion, slower)",
            "width": 832, "height": 480, "fps": 24,
            "supports_image_to_video": True,
            "supports_text_to_video": False,   # no verified t2v graph here
            "frame_multiple": 4,
            "typical_seconds": 180,
        },
    }

    def __init__(self, model: str = "wan22"):
        self.model = model if model in self.MODEL_PRESETS else "wan22"
        self.label = self.MODEL_PRESETS[self.model]["label"]

    # -- capability detection (against the live server, not assumptions) --

    def available(self) -> tuple[bool, str]:
        from agents import videographer
        if videographer.is_server_up():
            return True, ""
        return False, (
            f"ComfyUI is not running at {videographer.COMFYUI_URL}. Start it "
            "(Desktop shortcut 'ComfyUI (LTX Video)') and try again."
        )

    def capabilities(self) -> dict:
        from agents import videographer
        preset = self.MODEL_PRESETS[self.model]
        up, reason = self.available()
        caps = {
            "provider": self.name,
            "model": self.model,
            "label": self.label,
            "available": up,
            "unavailable_reason": reason,
            "text_to_video": preset["supports_text_to_video"],
            "image_to_video": preset["supports_image_to_video"],
            "first_last_frame": False,
            "first_last_frame_note": "",
            "max_seconds": 5.0,
            "width": preset["width"],
            "height": preset["height"],
            "fps": preset["fps"],
            "typical_seconds_per_clip": preset["typical_seconds"],
            "is_mock": False,
        }
        if not up:
            caps["first_last_frame_note"] = "Cannot detect: server offline."
            return caps

        # Native FLF is only claimed when the backend both HAS the node and has
        # been verified to run it with the configured model. `videographer`
        # owns that verification result — see its FLF_SUPPORT notes.
        flf_ok, flf_note = videographer.supports_first_last_frame(self.model)
        caps["first_last_frame"] = flf_ok
        caps["first_last_frame_note"] = flf_note
        return caps

    def generate_clip(self, spec: ClipSpec, progress=None, should_cancel=None) -> dict:
        from agents import videographer
        preset = self.MODEL_PRESETS[self.model]
        width = spec.width or preset["width"]
        height = spec.height or preset["height"]
        if spec.low_resource:
            # Never exceed the verified-safe size on a modest card.
            width = min(width, preset["width"])
            height = min(height, preset["height"])
        length = videographer.frames_for_seconds(spec.seconds, self.model, preset["fps"])

        try:
            result = videographer.generate_video(
                spec.prompt,
                image_path=spec.first_frame,
                last_frame_path=spec.last_frame,
                model=self.model,
                negative_prompt=spec.negative_prompt,
                width=width, height=height, length=length, seed=spec.seed,
                image_strength=spec.image_strength,
                progress=progress, should_cancel=should_cancel,
            )
        except videographer.VideoGenerationError as e:
            raise VideoProviderError(str(e)) from e
        result["is_mock"] = False
        return result


# --- Mock (development / no-GPU) --------------------------------------------

class MockVideoProvider(BaseVideoProvider):
    """
    Development provider. Produces a real file so the whole pipeline (storage,
    review, assembly, export) can be exercised without a GPU — but it is
    labelled a mock at every layer (`is_mock: True`, `model: "mock"`, a
    `MOCK-` filename prefix) so mock output can never be mistaken for a real
    generation. It copies the first frame to a .png placeholder rather than
    faking an mp4 of "generated" video.
    """

    name = "mock"
    label = "Development mock (no real generation)"

    def available(self) -> tuple[bool, str]:
        return True, ""

    def capabilities(self) -> dict:
        return {
            "provider": self.name, "model": "mock", "label": self.label,
            "available": True, "unavailable_reason": "",
            "text_to_video": True, "image_to_video": True,
            "first_last_frame": True,
            "first_last_frame_note": "Mock provider — no real conditioning happens.",
            "max_seconds": 4.0, "width": 768, "height": 512, "fps": 24,
            "typical_seconds_per_clip": 0,
            "is_mock": True,
        }

    def generate_clip(self, spec: ClipSpec, progress=None, should_cancel=None) -> dict:
        if progress:
            progress("mock provider: writing placeholder (no generation)")
        out = OUTPUTS_DIR / f"MOCK-clip-{uuid.uuid4().hex[:8]}.png"
        if spec.first_frame and os.path.exists(spec.first_frame):
            shutil.copy(spec.first_frame, out)
        else:
            out.write_bytes(b"")
        return {
            "video_path": out, "model": "mock", "seed": spec.seed or 0,
            "is_mock": True,
            "note": "Placeholder from the development mock provider — not a generated video.",
        }


# --- Registry + strategy resolution -----------------------------------------

PROVIDERS = {
    "comfyui:wan22": lambda: ComfyUIProvider("wan22"),
    "comfyui:ltx": lambda: ComfyUIProvider("ltx"),
    "mock": lambda: MockVideoProvider(),
}

DEFAULT_PROVIDER = os.getenv("VIDEO_PROVIDER", "comfyui:wan22")


def get_provider(provider_id: str | None = None) -> BaseVideoProvider:
    key = provider_id or DEFAULT_PROVIDER
    factory = PROVIDERS.get(key)
    if not factory:
        raise VideoProviderError(
            f"Unknown video provider {key!r}. Available: {', '.join(PROVIDERS)}"
        )
    return factory()


def list_providers() -> list[dict]:
    """Capability report for every provider — powers the UI's model picker."""
    out = []
    for key in PROVIDERS:
        try:
            caps = get_provider(key).capabilities()
            caps["id"] = key
            out.append(caps)
        except Exception as e:
            out.append({"id": key, "available": False, "unavailable_reason": str(e)[:200],
                        "is_mock": key == "mock"})
    return out


def resolve_strategy(caps: dict, has_first: bool, has_last: bool) -> dict:
    """
    Pick the generation strategy for one shot given real capabilities, in the
    spec's fallback order. Returns the strategy id, its label, and a plain
    explanation for the UI — the caller must SHOW this, never swallow it.
    """
    if has_first and has_last and caps.get("first_last_frame"):
        chosen, why = STRATEGY_NATIVE_FLF, (
            "This provider conditions on both frames directly.")
    elif has_first and caps.get("image_to_video"):
        if has_last:
            chosen, why = STRATEGY_FIRST_FRAME, (
                "This provider cannot condition on an end frame"
                + (f" ({caps.get('first_last_frame_note')})"
                   if caps.get("first_last_frame_note") else "")
                + ". The clip is generated from the first frame; your planned last "
                  "frame is kept as a visual target and used by the continuity check.")
        else:
            chosen, why = STRATEGY_CHAIN_EXTRACT, (
                "Generated from the first frame; the clip's real final frame is "
                "extracted and carried into the next shot for continuity.")
    elif caps.get("text_to_video"):
        chosen, why = STRATEGY_TEXT_ONLY, (
            "No frame available for conditioning — generating from the text prompt alone.")
    else:
        raise VideoProviderError(
            f"{caps.get('label', 'This provider')} cannot generate this shot: it needs a "
            "first frame (image-to-video only) but none is available."
        )
    return {"strategy": chosen, "label": STRATEGY_LABELS[chosen], "why": why,
            "is_mock": bool(caps.get("is_mock"))}
