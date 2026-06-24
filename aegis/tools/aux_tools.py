"""Auxiliary-model tools: cheap side-model calls that don't belong on the main agent loop.

- ``vision_analyze`` — describe/answer questions about an image with the vision aux model.
- ``web_extract`` — fetch a URL and return an aux-model summary/extraction (vs raw ``web_fetch``).

Both resolve their model via ``build_aux_provider(config, purpose=...)`` so they honor the
per-task ``auxiliary.vision`` / ``auxiliary.web_extract`` slots (falling back to the main model).
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from ..types import Message
from .base import Tool, ToolContext, ToolResult


def _aux_provider(ctx: ToolContext, purpose: str):
    from ..providers.registry import build_aux_provider
    return build_aux_provider(getattr(ctx, "config", None), purpose=purpose)


class VisionAnalyzeTool(Tool):
    name = "vision_analyze"
    description = (
        "Analyze an image with a vision model and answer a question about it. `image` may be a "
        "local file path, an http(s) URL, or a base64 data URL. Use for screenshots, diagrams, "
        "photos, or charts — not for generating images."
    )
    groups = ["network"]
    toolset = "vision"
    parameters = {
        "type": "object",
        "properties": {
            "image": {"type": "string", "description": "File path, http(s) URL, or base64 data URL."},
            "prompt": {"type": "string", "description": "What to look for / the question to answer."},
        },
        "required": ["image"],
    }

    def run(self, args, ctx: ToolContext) -> ToolResult:
        src = (args.get("image") or "").strip()
        if not src:
            return ToolResult.error("image is required (path, url, or data URL).")
        prompt = args.get("prompt") or "Describe this image in detail."
        try:
            data_url = self._to_data_url(src, ctx)
        except Exception as e:  # noqa: BLE001
            return ToolResult.error(f"could not load image: {e}")
        try:
            provider = _aux_provider(ctx, "vision")
            resp = provider.complete(
                [Message.system("You are a precise visual analyst. Be concise and factual."),
                 Message.user(prompt, images=[data_url])],
                tools=None, stream=False,
            )
        except Exception as e:  # noqa: BLE001
            return ToolResult.error(f"vision model call failed: {e}")
        text = (resp.text or "").strip() or "(no description returned)"
        return ToolResult.ok(text, display=f"analyzed image ({prompt[:40]})")

    @staticmethod
    def _to_data_url(src: str, ctx: ToolContext) -> str:
        if src.startswith("data:image/"):
            return src
        if src.startswith(("http://", "https://")):
            import base64

            from .. import net_safety
            blocked = net_safety.guard(src, getattr(ctx, "config", None))
            if blocked:
                raise ValueError(blocked)
            r = net_safety.request("GET", src, getattr(ctx, "config", None), timeout=30)
            r.raise_for_status()
            mime = r.headers.get("content-type", "image/png").split(";")[0]
            b64 = base64.b64encode(r.content).decode()
            return f"data:{mime};base64,{b64}"
        from ..util import encode_image
        path = Path(src).expanduser()
        if not path.is_file():
            raise FileNotFoundError(src)
        return encode_image(path)


class WebExtractTool(Tool):
    name = "web_extract"
    description = (
        "Fetch a web page and return a focused, aux-model summary/extraction of it (cheaper on "
        "context than raw web_fetch for large pages). Optionally pass `query` to weight the "
        "extraction toward a specific question."
    )
    groups = ["network"]
    toolset = "web"
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "query": {"type": "string", "description": "Optional focus for the extraction."},
        },
        "required": ["url"],
    }

    def run(self, args, ctx: ToolContext) -> ToolResult:
        url = (args.get("url") or "").strip()
        if not url:
            return ToolResult.error("url is required.")
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        from .. import net_safety
        blocked = net_safety.guard(url, getattr(ctx, "config", None))
        if blocked:
            return ToolResult.error(blocked)
        try:
            r = net_safety.request("GET", url, getattr(ctx, "config", None), timeout=30)
            r.raise_for_status()
            ctype = r.headers.get("content-type", "")
            body = r.text
        except net_safety.BlockedURL as e:
            return ToolResult.error(str(e))
        except Exception as e:  # noqa: BLE001
            return ToolResult.error(f"fetch failed: {e}")
        from .builtin import _html_to_text
        text = _html_to_text(body) if "html" in ctype else body
        text = text[:60_000]
        query = args.get("query") or ""
        instruction = (
            "Extract the key information from this web page as concise, factual bullet points. "
            "Preserve specific facts, names, numbers, and any code. Drop boilerplate/nav/ads."
            + (f"\nFocus on: {query}" if query else "")
        )
        try:
            provider = _aux_provider(ctx, "web_extract")
            resp = provider.complete(
                [Message.system(instruction), Message.user(text)], tools=None, stream=False,
            )
        except Exception as e:  # noqa: BLE001
            return ToolResult.error(f"extract model call failed: {e}")
        out = (resp.text or "").strip() or "(no content extracted)"
        return ToolResult.ok(out, display=f"extracted {url[:50]}")


class MediaAnalyzeTool(Tool):
    name = "media_analyze"
    description = (
        "Analyze image, audio, or video media. Images use the vision model; audio is "
        "transcribed with STT; video is sampled into frames with ffmpeg and analyzed by "
        "the vision model. Returns clear setup errors when the needed provider/dependency "
        "is unavailable."
    )
    groups = ["network", "runtime"]
    toolset = "vision"
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Local media file path."},
            "media_type": {"type": "string", "enum": ["auto", "image", "audio", "video"]},
            "prompt": {"type": "string", "description": "Question or focus for the analysis."},
            "max_frames": {"type": "integer", "description": "Maximum video frames to sample (default 4)."},
            "model": {"type": "string", "description": "Optional STT model for audio."},
        },
        "required": ["path"],
    }

    def run(self, args, ctx: ToolContext) -> ToolResult:
        path = Path(str(args.get("path") or "")).expanduser()
        if not path.is_file():
            return ToolResult.error(f"media file not found: {path}")
        media_type = str(args.get("media_type") or "auto").strip().lower()
        if media_type == "auto":
            media_type = self._infer_type(path)
        prompt = str(args.get("prompt") or "Describe the important content.").strip()
        if media_type == "image":
            return VisionAnalyzeTool().run({"image": str(path), "prompt": prompt}, ctx)
        if media_type == "audio":
            from .voice import TranscribeTool

            result = TranscribeTool().run({"path": str(path), "model": args.get("model", "whisper-1")}, ctx)
            if result.is_error:
                return result
            return ToolResult.ok(
                f"Audio transcript:\n{result.content}",
                display=f"analyzed audio {path.name}",
                data={"transcript": result.content, "path": str(path)},
            )
        if media_type == "video":
            return self._analyze_video(path, prompt, int(args.get("max_frames", 4) or 4), ctx)
        return ToolResult.error("media_type must be auto, image, audio, or video.")

    @staticmethod
    def _infer_type(path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}:
            return "image"
        if suffix in {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".flac"}:
            return "audio"
        if suffix in {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}:
            return "video"
        return "image"

    def _analyze_video(self, path: Path, prompt: str, max_frames: int, ctx: ToolContext) -> ToolResult:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return ToolResult.error("video analysis needs ffmpeg on PATH to sample frames.")
        max_frames = max(1, min(max_frames, 12))
        with tempfile.TemporaryDirectory(prefix="aegis-video-frames-") as td:
            frame_pattern = str(Path(td) / "frame-%03d.jpg")
            cmd = [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(path),
                "-vf",
                "fps=1/10",
                "-frames:v",
                str(max_frames),
                frame_pattern,
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if proc.returncode != 0:
                return ToolResult.error(f"ffmpeg frame extraction failed: {(proc.stderr or proc.stdout)[:500]}")
            frames = sorted(Path(td).glob("frame-*.jpg"))
            if not frames:
                return ToolResult.error("ffmpeg did not extract any frames from the video.")
            summaries: list[str] = []
            for index, frame in enumerate(frames, 1):
                result = VisionAnalyzeTool().run(
                    {
                        "image": str(frame),
                        "prompt": f"{prompt}\nThis is sampled video frame {index} of {len(frames)}.",
                    },
                    ctx,
                )
                if result.is_error:
                    return result
                summaries.append(f"Frame {index}: {result.content.strip()}")
        return ToolResult.ok(
            "Video frame analysis:\n" + "\n\n".join(summaries),
            display=f"analyzed video {path.name}",
            data={"frames_analyzed": len(summaries), "path": str(path)},
        )


def aux_tools() -> list[Tool]:
    return [VisionAnalyzeTool(), WebExtractTool(), MediaAnalyzeTool()]
