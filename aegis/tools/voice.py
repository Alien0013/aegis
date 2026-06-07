"""Voice tools: speech-to-text and text-to-speech via OpenAI-compatible audio APIs.

Enabled by adding "voice" to tools.toolsets. Uses the active provider's base_url +
auth, so it works with OpenAI (and any compatible endpoint that implements
/audio/transcriptions and /audio/speech).
"""

from __future__ import annotations

import time
from pathlib import Path

import httpx

from .base import Tool, ToolContext, ToolResult


class TranscribeTool(Tool):
    name = "transcribe"
    description = "Transcribe an audio file to text (speech-to-text)."
    groups = ["network"]
    toolset = "voice"
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "audio file (wav/mp3/m4a/…)"},
            "model": {"type": "string", "description": "STT model (default whisper-1)"},
        },
        "required": ["path"],
    }

    def run(self, args, ctx: ToolContext) -> ToolResult:
        from ..providers import build_provider

        audio = Path(args["path"]).expanduser()
        if not audio.exists():
            return ToolResult.error(f"no such file: {audio}")
        try:
            provider = build_provider(ctx.config)
            headers = {k: v for k, v in provider.auth.headers().items() if k != "Content-Type"}
            with httpx.Client(timeout=180) as c:
                r = c.post(
                    f"{provider.base_url}/audio/transcriptions",
                    headers=headers,
                    data={"model": args.get("model", "whisper-1")},
                    files={"file": (audio.name, audio.read_bytes())},
                )
                r.raise_for_status()
                text = r.json().get("text", "")
        except Exception as e:  # noqa: BLE001
            return ToolResult.error(f"transcription failed (provider must support audio): {e}")
        return ToolResult.ok(text, display=f"transcribed {audio.name}")


class SpeakTool(Tool):
    name = "speak"
    description = "Synthesize speech from text (text-to-speech). Saves an audio file and returns its path."
    groups = ["network"]
    toolset = "voice"
    parameters = {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "voice": {"type": "string", "description": "voice name (default alloy)"},
            "model": {"type": "string", "description": "TTS model (default tts-1)"},
            "path": {"type": "string"},
        },
        "required": ["text"],
    }

    def run(self, args, ctx: ToolContext) -> ToolResult:
        from ..providers import build_provider

        try:
            provider = build_provider(ctx.config)
            headers = {"Content-Type": "application/json", **provider.auth.headers()}
            payload = {"model": args.get("model", "tts-1"), "input": args["text"],
                       "voice": args.get("voice", "alloy")}
            with httpx.Client(timeout=180) as c:
                r = c.post(f"{provider.base_url}/audio/speech", headers=headers, json=payload)
                r.raise_for_status()
                data = r.content
        except Exception as e:  # noqa: BLE001
            return ToolResult.error(f"tts failed (provider must support audio): {e}")
        out = Path(args.get("path") or (ctx.cwd / f"speech-{int(time.time())}.mp3"))
        out.write_bytes(data)
        return ToolResult.ok(f"saved speech to {out}", display=f"tts → {out.name}")


def voice_tools() -> list[Tool]:
    return [TranscribeTool(), SpeakTool()]
