"""Helpers for rendering gateway message timestamps exactly once."""

from __future__ import annotations

import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any


_HUMAN_TIMESTAMP_RE = re.compile(
    r"^\[(?P<dow>[A-Z][a-z]{2}) "
    r"(?P<date>\d{4}-\d{2}-\d{2}) "
    r"(?P<time>\d{2}:\d{2}:\d{2})"
    r"(?: (?P<tz>[A-Za-z0-9_+\-/:]+))?\]\s*"
)

_ISO_TIMESTAMP_RE = re.compile(r"^\[(?P<iso>\d{4}-\d{2}-\d{2}T[^\]]+)\]\s*")


def _local_tz():
    return datetime.now().astimezone().tzinfo


def coerce_message_timestamp(ts_value: Any, tz=None) -> float | None:
    """Coerce timestamp-like values to Unix epoch seconds."""
    if ts_value is None:
        return None
    if isinstance(ts_value, (int, float)):
        value = float(ts_value)
        if value > 10_000_000_000:
            value /= 1000.0
        return value
    if hasattr(ts_value, "timestamp"):
        try:
            return float(ts_value.timestamp())
        except Exception:  # noqa: BLE001
            return None
    if isinstance(ts_value, str):
        text = ts_value.strip()
        if not text:
            return None
        parsed = _parse_timestamp_prefix(text, tz=tz)
        if parsed is not None:
            return parsed
        try:
            return coerce_message_timestamp(float(text), tz=tz)
        except (TypeError, ValueError):
            pass
        try:
            dt = datetime.fromisoformat(text)
        except (TypeError, ValueError):
            try:
                dt = datetime.strptime(text, "%Y-%m-%dT%H:%M:%S%z")
            except (TypeError, ValueError):
                return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz or _local_tz())
        return float(dt.timestamp())
    return None


def format_message_timestamp(ts_value: Any, tz=None) -> str:
    epoch = coerce_message_timestamp(ts_value, tz=tz)
    if epoch is None:
        return ""
    dt = datetime.fromtimestamp(epoch, tz=tz) if tz is not None else datetime.fromtimestamp(epoch).astimezone()
    return "[" + dt.strftime("%a %Y-%m-%d %H:%M:%S %Z") + "]"


def strip_leading_message_timestamps(content: str, tz=None) -> tuple[str, float | None]:
    if not isinstance(content, str) or not content:
        return content, None

    text = content
    embedded_epoch: float | None = None
    while True:
        match = _HUMAN_TIMESTAMP_RE.match(text) or _ISO_TIMESTAMP_RE.match(text)
        if not match:
            break
        parsed = _parse_timestamp_match(match, tz=tz)
        if parsed is not None:
            embedded_epoch = parsed
        text = text[match.end():]
    return text, embedded_epoch


def render_user_content_with_timestamp(content: str, ts_value: Any = None, tz=None) -> str:
    clean_content, embedded_epoch = strip_leading_message_timestamps(content, tz=tz)
    effective_ts = embedded_epoch if embedded_epoch is not None else ts_value
    prefix = format_message_timestamp(effective_ts, tz=tz)
    if not prefix:
        return clean_content
    return f"{prefix} {clean_content}" if clean_content else prefix


def _parse_timestamp_prefix(text: str, tz=None) -> float | None:
    match = _HUMAN_TIMESTAMP_RE.match(text) or _ISO_TIMESTAMP_RE.match(text)
    if not match:
        return None
    return _parse_timestamp_match(match, tz=tz)


def _parse_timestamp_match(match: re.Match, tz=None) -> float | None:
    if "iso" in match.groupdict() and match.group("iso"):
        iso_text = match.group("iso")
        try:
            dt = datetime.fromisoformat(iso_text)
        except ValueError:
            try:
                dt = datetime.strptime(iso_text, "%Y-%m-%dT%H:%M:%S%z")
            except ValueError:
                return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz or _local_tz())
        return float(dt.timestamp())

    try:
        dt = datetime.strptime(
            f"{match.group('date')} {match.group('time')}",
            "%Y-%m-%d %H:%M:%S",
        )
    except ValueError:
        return None
    tz_name = match.groupdict().get("tz")
    if tz_name:
        try:
            parsed = parsedate_to_datetime(
                f"{match.group('dow')}, {dt.day} {dt.strftime('%b')} "
                f"{dt.year} {match.group('time')} {tz_name}"
            )
            if parsed.tzinfo is not None:
                return float(parsed.timestamp())
        except (TypeError, ValueError):
            pass
    dt = dt.replace(tzinfo=tz or _local_tz())
    return float(dt.timestamp())
