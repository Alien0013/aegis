"""Voice tools: speech-to-text and text-to-speech via OpenAI-compatible audio APIs.

Enabled by adding "voice" to tools.toolsets. Uses the active provider's base_url +
auth, so it works with OpenAI (and any compatible endpoint that implements
/audio/transcriptions and /audio/speech).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from pathlib import Path

import httpx

from .base import Tool, ToolContext, ToolResult

_SENTENCE_END = ".!?;\n"


def synthesize_speech(text: str, config, *, voice: str = "alloy", model: str = "tts-1",
                      timeout: float = 180) -> bytes:
    """Synthesize one chunk of text to audio bytes via the provider's /audio/speech endpoint.
    Shared by the speak tool and the streaming pipeline."""
    from ..providers import build_provider

    provider = build_provider(config)
    headers = {"Content-Type": "application/json", **provider.auth.headers()}
    payload = {"model": model, "input": text, "voice": voice}
    with httpx.Client(timeout=timeout) as c:
        r = c.post(f"{provider.base_url}/audio/speech", headers=headers, json=payload)
        r.raise_for_status()
        return r.content


class StreamingSpeech:
    """Buffer streamed assistant text into speakable chunks so TTS can start before the full
    reply is done. ``feed(delta)`` returns any newly-complete chunks (whole sentences once the
    buffer passes ``min_chars``, or a soft cut at ``max_chars`` for runaway sentences);
    ``flush()`` returns whatever is left at the end."""

    def __init__(self, min_chars: int = 60, max_chars: int = 240):
        self._buf = ""
        self.min_chars = max(1, min_chars)
        self.max_chars = max(self.min_chars, max_chars)

    def feed(self, delta: str) -> list[str]:
        self._buf += delta or ""
        out: list[str] = []
        while True:
            chunk = self._take()
            if chunk is None:
                break
            out.append(chunk)
        return out

    def _take(self) -> str | None:
        buf = self._buf
        n = len(buf)
        if n < self.min_chars:
            return None
        for i in range(self.min_chars - 1, n):       # earliest sentence end past min_chars
            if buf[i] in _SENTENCE_END and (i + 1 >= n or buf[i + 1].isspace()):
                self._buf = buf[i + 1:].lstrip()
                return buf[: i + 1].strip() or None
        if n >= self.max_chars:                      # no sentence end — soft-cut at a space
            cut = buf.rfind(" ", self.min_chars, self.max_chars)
            if cut == -1:
                cut = self.max_chars
            self._buf = buf[cut:].lstrip()
            return buf[:cut].strip() or None
        return None

    def flush(self) -> list[str]:
        rest = self._buf.strip()
        self._buf = ""
        return [rest] if rest else []


def stream_speak(deltas: Iterable[str], config, on_audio: Callable[[bytes, str], None], *,
                 voice: str = "alloy", model: str = "tts-1", min_chars: int = 60) -> None:
    """Low-latency TTS pipeline: feed streamed text deltas, synthesize each completed chunk as
    soon as it forms, and hand (audio_bytes, chunk_text) to ``on_audio``. A failed chunk is
    skipped rather than sinking the stream."""
    streamer = StreamingSpeech(min_chars=min_chars)

    def _say(chunk: str) -> None:
        if not chunk:
            return
        try:
            on_audio(synthesize_speech(chunk, config, voice=voice, model=model), chunk)
        except Exception:  # noqa: BLE001 — one chunk failing must not stop the rest
            pass

    for delta in deltas:
        for chunk in streamer.feed(delta):
            _say(chunk)
    for chunk in streamer.flush():
        _say(chunk)


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
        try:
            data = synthesize_speech(args["text"], ctx.config,
                                     voice=args.get("voice", "alloy"),
                                     model=args.get("model", "tts-1"))
        except Exception as e:  # noqa: BLE001
            return ToolResult.error(f"tts failed (provider must support audio): {e}")
        out = Path(args.get("path") or (ctx.cwd / f"speech-{int(time.time())}.mp3"))
        out.write_bytes(data)
        return ToolResult.ok(f"saved speech to {out}", display=f"tts → {out.name}")


def voice_tools() -> list[Tool]:
    return [TranscribeTool(), SpeakTool()]
