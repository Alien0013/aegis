"""Streaming TTS: the sentence-chunker emits speakable chunks as text streams in, and the
stream_speak pipeline synthesizes each chunk as soon as it forms."""

from __future__ import annotations

import aegis.tools.voice as voice
from aegis.tools.voice import StreamingSpeech, stream_speak


def test_emits_whole_sentences_in_order():
    s = StreamingSpeech(min_chars=12, max_chars=200)
    out: list[str] = []
    for piece in ["The quick brown ", "fox jumps. ", "Then it ", "rests well. ", "End"]:
        out += s.feed(piece)
    out += s.flush()
    assert out[0] == "The quick brown fox jumps."
    assert "Then it rests well." in out
    assert out[-1] == "End"


def test_holds_below_min_until_flush():
    s = StreamingSpeech(min_chars=50)
    assert s.feed("short. ") == []          # under the floor — don't synthesize a fragment
    assert s.flush() == ["short."]


def test_runaway_sentence_soft_cuts_at_a_space():
    s = StreamingSpeech(min_chars=10, max_chars=20)
    out = s.feed("aaaa bbbb cccc dddd eeee ffff")   # no sentence end, longer than max
    assert out and all(len(c) <= 20 for c in out)   # forced a cut at a word boundary


def test_stream_speak_synthesizes_each_chunk(monkeypatch):
    monkeypatch.setattr(voice, "synthesize_speech",
                        lambda text, config, **kw: b"AUDIO:" + text.encode())
    got: list[tuple[str, bytes]] = []
    stream_speak(["Hello world. ", "Second one. ", "tail"], config=None,
                 on_audio=lambda audio, chunk: got.append((chunk, audio)), min_chars=8)
    chunks = [c for c, _ in got]
    assert "Hello world." in chunks and "Second one." in chunks and "tail" in chunks
    assert all(audio.startswith(b"AUDIO:") for _, audio in got)


def test_stream_speak_skips_failed_chunks(monkeypatch):
    def boom(text, config, **kw):
        if "bad" in text:
            raise RuntimeError("synth down")
        return b"ok"
    monkeypatch.setattr(voice, "synthesize_speech", boom)
    got: list[str] = []
    stream_speak(["good one. ", "bad one. ", "good two."], config=None,
                 on_audio=lambda audio, chunk: got.append(chunk), min_chars=6)
    assert "good one." in got and "good two." in got   # the failing chunk didn't sink the rest
