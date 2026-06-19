"""OpenAI-compatible HTTP server: expose AEGIS as a /v1/chat/completions backend.

Lets any OpenAI-client tool point at AEGIS. Optional bearer auth via
``server.api_key`` in config or the ``AEGIS_SERVER_KEY`` env var.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from collections import OrderedDict
from datetime import datetime
from io import BytesIO
from http.server import BaseHTTPRequestHandler
from typing import Any, Iterable
from urllib.parse import parse_qs, unquote, urlparse

from aiohttp import web

from . import config as cfg_paths
from .config import Config
from .surface import SurfaceRunner, normalize_service_tier, runtime_controls_meta
from .types import Message, ToolCall, new_id
from .util import estimate_tokens, now_iso

logger = logging.getLogger(__name__)

_MAX_BODY_BYTES = 10 * 1024 * 1024
_DEFAULT_MAX_STORED_RESPONSES = 100
_DEFAULT_RESPONSE_AUTO_TRUNCATION_MESSAGES = 100
_MAX_STORED_RUN_EVENTS = 500
_TERMINAL_RUN_STATUSES = {"completed", "error", "cancelled", "interrupted"}
_MAX_SESSION_KEY_CHARS = 256
_REASONING_EFFORTS = {"off", "none", "minimal", "low", "medium", "high", "xhigh"}
_TEXT_CONTENT_PART_TYPES = {"text", "input_text", "output_text"}
_IMAGE_CONTENT_PART_TYPES = {"image_url", "input_image"}
_FILE_CONTENT_PART_TYPES = {"file", "input_file"}
_AUDIO_CONTENT_PART_TYPES = {"audio", "input_audio"}
_REFUSAL_CONTENT_PART_TYPES = {"refusal"}
_MESSAGE_PHASES = {"commentary", "final_answer"}
_OPAQUE_RESPONSE_INPUT_ITEM_TYPES = {
    "code_interpreter_call",
    "computer_call",
    "computer_call_output",
    "additional_tools",
    "apply_patch_call",
    "apply_patch_call_output",
    "compaction",
    "compaction_trigger",
    "custom_tool_call",
    "custom_tool_call_output",
    "file_search_call",
    "image_generation_call",
    "item_reference",
    "local_shell_call",
    "local_shell_call_output",
    "mcp_approval_request",
    "mcp_approval_response",
    "mcp_call",
    "mcp_list_tools",
    "reasoning",
    "shell_call",
    "shell_call_output",
    "tool_search_call",
    "tool_search_output",
    "web_search_call",
}
_CHAT_IDEMPOTENCY_FINGERPRINT_KEYS = [
    "model",
    "messages",
    "metadata",
    "session_id",
    "provider",
    "cwd",
    "tools",
    "tool_choice",
    "parallel_tool_calls",
    "temperature",
    "top_p",
    "max_tokens",
    "max_completion_tokens",
    "reasoning",
    "reasoning_effort",
    "response_format",
    "seed",
    "stop",
    "stream",
    "service_tier",
    "fast",
    "_session_id_header",
    "_provider_header",
    "_cwd_header",
    "_session_key_header",
]
_RESPONSES_IDEMPOTENCY_FINGERPRINT_KEYS = [
    "input",
    "messages",
    "instructions",
    "previous_response_id",
    "conversation",
    "conversation_history",
    "metadata",
    "session_id",
    "model",
    "provider",
    "cwd",
    "tools",
    "tool_choice",
    "parallel_tool_calls",
    "temperature",
    "top_p",
    "max_output_tokens",
    "max_completion_tokens",
    "response_format",
    "reasoning",
    "text",
    "include",
    "store",
    "truncation",
    "stream",
    "service_tier",
    "fast",
    "_session_id_header",
    "_provider_header",
    "_cwd_header",
    "_session_key_header",
]


def _api_session_id_from_body(
    body: dict[str, Any],
    *,
    default: str | None = None,
) -> tuple[str | None, tuple[int, dict[str, Any]] | None]:
    raw = body.get("id") or body.get("session_id")
    if raw is None or raw == "":
        return default, None
    session_id = str(raw).strip()
    if (
        not session_id
        or any(ch in session_id for ch in "\r\n\x00")
        or "/" in session_id
        or "?" in session_id
        or "#" in session_id
    ):
        return None, (400, {"ok": False, "error": "Invalid session ID", "code": "invalid_session_id"})
    if len(session_id) > _MAX_SESSION_KEY_CHARS:
        return None, (400, {"ok": False, "error": "Session ID too long", "code": "invalid_session_id"})
    return session_id, None


def _api_job_id_error(
    job_id: str,
    *,
    method: str,
    path: str,
    headers: Any,
) -> tuple[int, dict[str, Any]] | None:
    from .cron import _SAFE_JOB_ID_RE

    text = str(job_id or "").strip()
    valid = (
        bool(text)
        and text not in {".", ".."}
        and "/" not in text
        and "\\" not in text
        and "\x00" not in text
        and _SAFE_JOB_ID_RE.fullmatch(text) is not None
    )
    if valid:
        return None
    try:
        forwarded_for = headers.get("X-Forwarded-For", "")
        user_agent = headers.get("User-Agent", "")
    except AttributeError:
        forwarded_for = ""
        user_agent = ""
    logger.warning(
        "Cron jobs API rejected invalid job_id %r method=%s path=%s forwarded_for=%s user_agent=%s",
        text,
        method,
        path,
        forwarded_for,
        user_agent,
    )
    return 400, {"ok": False, "error": "Invalid job ID", "code": "invalid_job_id", "id": text}


def _coerce_request_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off", ""}:
        return False
    return default


def _request_service_tier(body: dict[str, Any], metadata: dict[str, Any] | None = None) -> str:
    """Normalize service-tier controls from API body or metadata."""
    metadata = metadata or {}
    raw = metadata.get("service_tier") or body.get("service_tier")
    if raw in (None, "") and "fast" in body:
        raw = "priority" if _coerce_request_bool(body.get("fast"), False) else "normal"
    return normalize_service_tier(raw)


def _request_max_tokens(body: dict[str, Any], *keys: str) -> tuple[int | None, dict[str, Any] | None]:
    for key in keys:
        if key not in body:
            continue
        raw = body.get(key)
        if raw in (None, ""):
            continue
        if isinstance(raw, bool):
            return None, _openai_error(
                f"'{key}' must be a positive integer",
                code="invalid_max_tokens",
                param=key,
            )
        value: int | None = None
        if isinstance(raw, int):
            value = raw
        elif isinstance(raw, str) and raw.strip().isdigit():
            value = int(raw.strip())
        if value is None or value <= 0:
            return None, _openai_error(
                f"'{key}' must be a positive integer",
                code="invalid_max_tokens",
                param=key,
            )
        return value, None
    return None, None


def _request_reasoning_effort(
    body: dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any] | None]:
    metadata = metadata or {}
    param = "reasoning"
    raw = metadata.get("reasoning_effort")
    if raw in (None, ""):
        raw = body.get("reasoning_effort")
        param = "reasoning_effort"
    if raw in (None, "") and "reasoning" in body:
        reasoning = body.get("reasoning")
        if isinstance(reasoning, dict):
            raw = reasoning.get("effort")
            param = "reasoning.effort"
        else:
            raw = reasoning
            param = "reasoning"
    if raw in (None, ""):
        return "", None
    value = str(raw or "").strip().lower()
    if value == "max":
        value = "xhigh"
    if value == "none":
        value = "off"
    if value not in _REASONING_EFFORTS:
        return "", _openai_error(
            "'reasoning' effort must be one of none, off, minimal, low, medium, high, or xhigh",
            code="invalid_reasoning_effort",
            param=param,
        )
    return value, None


def _parse_cors_origins(value: Any) -> tuple[str, ...]:
    if not value:
        return ()
    if isinstance(value, str):
        items = value.split(",")
    elif isinstance(value, (list, tuple, set)):
        items = value
    else:
        items = [value]
    return tuple(str(item).strip() for item in items if str(item).strip())


def _configured_cors_origins(config: Config | None) -> tuple[str, ...]:
    value = None
    if config is not None:
        value = config.get("server.cors_origins")
    if not value:
        value = os.environ.get("AEGIS_SERVER_CORS_ORIGINS") or os.environ.get("API_SERVER_CORS_ORIGINS")
    return _parse_cors_origins(value)


def _cors_headers(config: Config | None, origin: str = "") -> dict[str, str] | None:
    origin = str(origin or "").strip()
    if not origin:
        return {}
    origins = _configured_cors_origins(config)
    if not origins:
        return None
    headers = {
        "Access-Control-Allow-Methods": "GET,POST,PATCH,PUT,DELETE,OPTIONS",
        "Access-Control-Allow-Headers": (
            "Authorization, Content-Type, Accept, OpenAI-Beta, Idempotency-Key, "
            "X-Aegis-Session, X-Aegis-Provider, X-Aegis-Cwd, "
            "X-Hermes-Session-Id, X-Hermes-Session-Key"
        ),
        "Access-Control-Max-Age": "600",
    }
    if "*" in origins:
        headers["Access-Control-Allow-Origin"] = "*"
        return headers
    if origin not in origins:
        return None
    headers["Access-Control-Allow-Origin"] = origin
    headers["Vary"] = "Origin"
    return headers


def _origin_allowed(config: Config | None, origin: str = "") -> bool:
    return not origin or _cors_headers(config, origin) is not None


def _security_headers() -> dict[str, str]:
    return {
        "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "X-XSS-Protection": "0",
        "Referrer-Policy": "no-referrer",
    }


def _response_headers(config: Config | None, origin: str = "") -> dict[str, str]:
    headers = _security_headers()
    cors = _cors_headers(config, origin)
    if cors:
        headers.update(cors)
    return headers


def _json_bytes(obj: Any) -> bytes:
    return json.dumps(obj, default=str).encode()


def _openai_error(message: str, *, type_: str = "invalid_request_error",
                  code: str | None = None, param: str | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {"message": message, "type": type_}
    if code:
        error["code"] = code
    if param:
        error["param"] = param
    return {"error": error}


def _request_fingerprint(body: dict[str, Any], keys: list[str]) -> str:
    subset = {key: body.get(key) for key in keys}
    blob = json.dumps(subset, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _content(value: Any) -> tuple[str, list[str]]:
    """OpenAI content string/parts -> AEGIS text + image references."""
    if isinstance(value, str):
        return value, []
    if not isinstance(value, list):
        return "" if value is None else str(value), []
    texts: list[str] = []
    images: list[str] = []
    for part in value:
        if not isinstance(part, dict):
            texts.append(str(part))
            continue
        ptype = str(part.get("type") or "").strip().lower()
        if ptype in _TEXT_CONTENT_PART_TYPES:
            texts.append(str(part.get("text", "")))
        elif ptype in _REFUSAL_CONTENT_PART_TYPES:
            texts.append(str(part.get("refusal", "")))
        elif ptype in _IMAGE_CONTENT_PART_TYPES:
            image = _image_url_from_part(part)
            if image:
                images.append(str(image))
            else:
                label = _image_file_id_from_part(part)
                if label:
                    texts.append(f"[image: {label}]")
        elif ptype in _FILE_CONTENT_PART_TYPES:
            label = _file_label_from_part(part)
            if label:
                texts.append(f"[file: {label}]")
        elif ptype in _AUDIO_CONTENT_PART_TYPES:
            label = _audio_label_from_part(part)
            if label:
                texts.append(f"[audio: {label}]")
    return "\n".join(t for t in texts if t), images


def _image_url_from_part(part: dict[str, Any]) -> Any:
    image = part.get("image_url") or part.get("image")
    if isinstance(image, dict):
        image = image.get("url")
    return image


def _image_file_id_from_part(part: dict[str, Any]) -> str:
    image = part.get("image_url") or part.get("image")
    if isinstance(image, dict):
        value = image.get("file_id")
        if isinstance(value, str) and value.strip():
            return value.strip()
    value = part.get("file_id")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return ""


def _file_label_from_part(part: dict[str, Any]) -> str:
    for key in ("file_id", "file_url", "filename", "name"):
        value = part.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if part.get("file_data") is not None:
        return "inline file"
    return ""


def _audio_payload_from_part(part: dict[str, Any]) -> Any:
    audio = part.get("input_audio")
    if audio is None:
        audio = part.get("audio")
    return audio


def _audio_label_from_part(part: dict[str, Any]) -> str:
    audio = _audio_payload_from_part(part)
    fmt = ""
    if isinstance(audio, dict):
        fmt = str(audio.get("format") or "").strip()
        if isinstance(audio.get("data"), str) and audio.get("data", "").strip():
            return f"inline audio ({fmt})" if fmt else "inline audio"
        if isinstance(audio.get("url"), str) and audio.get("url", "").strip():
            return audio["url"].strip()
        if isinstance(audio.get("file_id"), str) and audio.get("file_id", "").strip():
            return audio["file_id"].strip()
    elif isinstance(audio, str) and audio.strip():
        return "inline audio"
    for key in ("file_id", "filename", "name", "url"):
        value = part.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if isinstance(part.get("data"), str) and part.get("data", "").strip():
        fmt = str(part.get("format") or "").strip()
        return f"inline audio ({fmt})" if fmt else "inline audio"
    return ""


def _image_url_validation_error(image: Any, *, param: str) -> dict[str, Any] | None:
    if not isinstance(image, str) or not image.strip():
        return _openai_error(
            "Image content parts require a non-empty image URL",
            code="invalid_image_content",
            param=param,
        )
    url = image.strip()
    lowered = url.lower()
    if lowered.startswith("data:"):
        if not lowered.startswith("data:image/") or "," not in url:
            return _openai_error(
                "Only image data URLs are supported. Non-image data payloads are not supported.",
                code="unsupported_content_type",
                param=param,
            )
        return None
    if not (lowered.startswith("http://") or lowered.startswith("https://")):
        return _openai_error(
            "Image inputs must use http(s) URLs or data:image/... URLs.",
            code="invalid_image_url",
            param=param,
        )
    return None


def _image_part_validation_error(part: dict[str, Any], *, param: str) -> dict[str, Any] | None:
    image = _image_url_from_part(part)
    if image is not None:
        return _image_url_validation_error(image, param=f"{param}.image_url")
    file_id = _image_file_id_from_part(part)
    if file_id:
        return None
    return _openai_error(
        "Image content parts require a non-empty image URL or file_id",
        code="invalid_image_content",
        param=f"{param}.image_url",
    )


def _content_part_validation_error(
    value: Any,
    *,
    param: str,
    allow_files: bool = False,
) -> dict[str, Any] | None:
    if value is None or isinstance(value, str):
        return None
    if not isinstance(value, list):
        return None
    for index, part in enumerate(value):
        part_param = f"{param}[{index}]"
        if not isinstance(part, dict):
            return _openai_error(
                "Content array entries must be objects",
                code="invalid_content_part",
                param=part_param,
            )
        ptype = str(part.get("type") or "").strip().lower()
        if ptype in _FILE_CONTENT_PART_TYPES:
            if not allow_files:
                return _openai_error(
                    "Inline image inputs are supported, but uploaded files and document inputs are not supported.",
                    code="unsupported_content_type",
                    param=f"{part_param}.type",
                )
            if not _file_label_from_part(part):
                return _openai_error(
                    "File content parts require 'file_id', 'file_url', 'filename', or 'file_data'.",
                    code="invalid_file_content",
                    param=f"{part_param}.file_id",
                )
            if "file_data" in part and not isinstance(part.get("file_data"), str):
                return _openai_error(
                    "File content parts require string 'file_data' when provided.",
                    code="invalid_file_content",
                    param=f"{part_param}.file_data",
                )
            continue
        if ptype in _REFUSAL_CONTENT_PART_TYPES:
            if not isinstance(part.get("refusal", ""), str):
                return _openai_error(
                    "Refusal content parts require a string 'refusal' field",
                    code="invalid_refusal_content",
                    param=f"{part_param}.refusal",
                )
            continue
        if ptype in _AUDIO_CONTENT_PART_TYPES:
            if not _audio_label_from_part(part):
                return _openai_error(
                    "Audio content parts require 'input_audio', 'audio', 'data', 'url', or 'file_id'.",
                    code="invalid_audio_content",
                    param=f"{part_param}.input_audio",
                )
            audio = _audio_payload_from_part(part)
            if audio is not None and not isinstance(audio, (str, dict)):
                return _openai_error(
                    "Audio content parts require string or object audio payloads.",
                    code="invalid_audio_content",
                    param=f"{part_param}.input_audio",
                )
            if isinstance(audio, dict):
                data = audio.get("data")
                if data is not None and not isinstance(data, str):
                    return _openai_error(
                        "Audio content parts require string 'data' when provided.",
                        code="invalid_audio_content",
                        param=f"{part_param}.input_audio.data",
                    )
                fmt = audio.get("format")
                if fmt is not None and not isinstance(fmt, str):
                    return _openai_error(
                        "Audio content parts require string 'format' when provided.",
                        code="invalid_audio_content",
                        param=f"{part_param}.input_audio.format",
                    )
            if "data" in part and not isinstance(part.get("data"), str):
                return _openai_error(
                    "Audio content parts require string 'data' when provided.",
                    code="invalid_audio_content",
                    param=f"{part_param}.data",
                )
            continue
        if ptype not in _TEXT_CONTENT_PART_TYPES | _IMAGE_CONTENT_PART_TYPES:
            return _openai_error(
                f"Unsupported content part type: {ptype or '<missing>'}",
                code="unsupported_content_part",
                param=f"{part_param}.type",
            )
        if ptype in _TEXT_CONTENT_PART_TYPES and not isinstance(part.get("text", ""), str):
            return _openai_error(
                "Text content parts require a string 'text' field",
                code="invalid_text_content",
                param=f"{part_param}.text",
            )
        if ptype in _IMAGE_CONTENT_PART_TYPES:
            err = _image_part_validation_error(part, param=part_param)
            if err is not None:
                return err
    return None


def _response_content_validation_error(value: Any, *, param: str) -> dict[str, Any] | None:
    if isinstance(value, dict):
        if _is_function_call_output_input_item(value):
            if not str(value.get("call_id") or value.get("tool_call_id") or "").strip():
                return _openai_error(
                    "function_call_output input items require a non-empty 'call_id'",
                    code="invalid_function_call_output",
                    param=f"{param}.call_id",
                )
            if "output" not in value:
                return _openai_error(
                    "function_call_output input items require an 'output' field",
                    code="invalid_function_call_output",
                    param=f"{param}.output",
                )
            return _function_output_validation_error(value.get("output"), param=f"{param}.output")
        if _is_function_call_input_item(value):
            if not str(value.get("call_id") or value.get("id") or "").strip():
                return _openai_error(
                    "function_call input items require a non-empty 'call_id'",
                    code="invalid_function_call",
                    param=f"{param}.call_id",
                )
            if not str(value.get("name") or "").strip():
                return _openai_error(
                    "function_call input items require a non-empty 'name'",
                    code="invalid_function_call",
                    param=f"{param}.name",
                )
            arguments = value.get("arguments", {})
            if arguments is not None and not isinstance(arguments, (str, dict, list)):
                return _openai_error(
                    "function_call input items require 'arguments' to be a string, object, or array",
                    code="invalid_function_call",
                    param=f"{param}.arguments",
                )
            return None
        if _is_opaque_response_input_item(value):
            return None
        if "content" in value:
            if value.get("phase") is not None and str(value.get("phase") or "") not in _MESSAGE_PHASES:
                return _openai_error(
                    "Message phase must be 'commentary' or 'final_answer'",
                    code="invalid_message_phase",
                    param=f"{param}.phase",
                )
            return _content_part_validation_error(
                value.get("content"),
                param=f"{param}.content",
                allow_files=True,
            )
        if "type" in value:
            return _content_part_validation_error([value], param=param, allow_files=True)
        return None
    if isinstance(value, list):
        for index, item in enumerate(value):
            err = _response_content_validation_error(item, param=f"{param}[{index}]")
            if err is not None:
                return err
        return None
    return _content_part_validation_error(value, param=param, allow_files=True)


def _content_has_visible_payload(value: Any) -> bool:
    text, images = _content(value)
    return bool(str(text or "").strip() or images)


def _is_function_call_output_input_item(item: Any) -> bool:
    return isinstance(item, dict) and str(item.get("type") or "") == "function_call_output"


def _is_function_call_input_item(item: Any) -> bool:
    return isinstance(item, dict) and str(item.get("type") or "") == "function_call"


def _response_item_type(item: dict[str, Any]) -> str:
    return str(item.get("type") or "").strip().lower()


def _is_opaque_response_input_item(item: Any) -> bool:
    return (
        isinstance(item, dict)
        and "role" not in item
        and _response_item_type(item) in _OPAQUE_RESPONSE_INPUT_ITEM_TYPES
    )


def _is_response_content_part_item(item: Any) -> bool:
    return (
        isinstance(item, dict)
        and "role" not in item
        and "content" not in item
        and _response_item_type(item)
        in (
            _TEXT_CONTENT_PART_TYPES
            | _IMAGE_CONTENT_PART_TYPES
            | _FILE_CONTENT_PART_TYPES
            | _AUDIO_CONTENT_PART_TYPES
            | _REFUSAL_CONTENT_PART_TYPES
        )
    )


def _function_output_validation_error(value: Any, *, param: str) -> dict[str, Any] | None:
    if isinstance(value, list):
        return _content_part_validation_error(value, param=param, allow_files=True)
    if isinstance(value, dict):
        if "content" in value:
            return _content_part_validation_error(
                value.get("content"),
                param=f"{param}.content",
                allow_files=True,
            )
        if "type" in value:
            return _content_part_validation_error([value], param=param, allow_files=True)
    return None


def _response_input_item_visible(item: Any) -> bool:
    if not isinstance(item, dict):
        return _content_has_visible_payload(item)
    if _is_function_call_output_input_item(item):
        return bool(str(item.get("call_id") or item.get("tool_call_id") or "").strip())
    if _is_function_call_input_item(item):
        return False
    if _is_opaque_response_input_item(item):
        return False
    if "content" in item:
        return _content_has_visible_payload(item.get("content"))
    text = item.get("text", item.get("input_text"))
    if text is not None and str(text).strip():
        return True
    if str(item.get("type") or "").strip().lower() in _FILE_CONTENT_PART_TYPES:
        return bool(_file_label_from_part(item))
    if str(item.get("type") or "").strip().lower() in _AUDIO_CONTENT_PART_TYPES:
        return bool(_audio_label_from_part(item))
    image = _image_url_from_part(item)
    return bool(image or _image_file_id_from_part(item))


def _chat_messages_validation_error(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, list) or not value:
        return _openai_error("Missing or invalid 'messages' field", param="messages")
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            return _openai_error(
                "Missing or invalid 'messages' field",
                param=f"messages[{index}]",
            )
        err = _content_part_validation_error(item.get("content"), param=f"messages[{index}].content")
        if err is not None:
            return err
    has_user_payload = any(
        str(item.get("role") or "").lower() == "user"
        and _content_has_visible_payload(item.get("content", ""))
        for item in value
    )
    if not has_user_payload:
        return _openai_error("No user message found in messages", param="messages")
    return None


def _responses_input_validation_error(
    body: dict[str, Any],
    *,
    allow_omitted: bool = False,
) -> dict[str, Any] | None:
    if "input" in body:
        raw = body.get("input")
    elif "messages" in body:
        raw = body.get("messages")
    else:
        if allow_omitted:
            return None
        return _openai_error("Missing 'input' field", param="input")

    err = _response_content_validation_error(raw, param="input")
    if err is not None:
        return err

    has_payload = False
    if isinstance(raw, str):
        has_payload = bool(raw.strip())
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and "role" in item:
                if str(item.get("role") or "").lower() == "user" and _response_input_item_visible(item):
                    has_payload = True
                    break
            elif _response_input_item_visible(item):
                has_payload = True
                break
    elif isinstance(raw, dict):
        if "role" in raw:
            has_payload = str(raw.get("role") or "").lower() == "user" and _response_input_item_visible(raw)
        else:
            has_payload = _response_input_item_visible(raw)
    elif raw is not None:
        has_payload = bool(str(raw).strip())

    if not has_payload:
        return _openai_error("No user message found in input", param="input")
    return None


def _response_include_values(value: Any) -> tuple[list[str], dict[str, Any] | None]:
    if value in (None, ""):
        return [], None
    if isinstance(value, str):
        text = value.strip()
        return ([text] if text else []), None
    if isinstance(value, list):
        items: list[str] = []
        for index, item in enumerate(value):
            if not isinstance(item, str):
                return [], _openai_error(
                    "'include' items must be strings",
                    code="invalid_include",
                    param=f"include[{index}]",
                )
            text = item.strip()
            if text:
                items.append(text)
        return items, None
    return [], _openai_error(
        "'include' must be a string or an array of strings",
        code="invalid_include",
        param="include",
    )


def _attach_response_include(response: dict[str, Any], include: list[str]) -> dict[str, Any]:
    if include:
        response["include"] = list(include)
    return response


def _tool_calls_from_payload(payload: dict[str, Any]) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for raw in payload.get("tool_calls", []) or []:
        if not isinstance(raw, dict):
            continue
        call_id = str(raw.get("id") or raw.get("call_id") or new_id("call"))
        if "function" in raw and isinstance(raw.get("function"), dict):
            func = raw["function"]
            name = str(func.get("name") or raw.get("name") or "")
            args = func.get("arguments", {})
        else:
            name = str(raw.get("name") or "")
            args = raw.get("arguments", {})
        if isinstance(args, str):
            try:
                args = json.loads(args) if args else {}
            except json.JSONDecodeError:
                args = {"__raw__": args}
        if not isinstance(args, dict):
            args = {"value": args}
        if name:
            calls.append(ToolCall(id=call_id, name=name, arguments=args))
    return calls


def _response_function_output_text(value: Any) -> str:
    value = _canonical_response_function_output(value)
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        text, _images = _content(value)
        if text or not value:
            return text
    if isinstance(value, dict):
        if "content" in value:
            return _response_function_output_text(value.get("content"))
        for key in ("text", "input_text", "output"):
            if key in value:
                return _response_function_output_text(value.get(key))
    if value is None:
        return ""
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except TypeError:
        return str(value)


def _canonical_response_function_output(value: Any) -> Any:
    if isinstance(value, list):
        parts: list[dict[str, Any]] = []
        for part in value:
            normalized = _canonical_response_function_output_part(part)
            if normalized is not None:
                parts.append(normalized)
        return parts if parts else ""
    if isinstance(value, dict):
        if "content" in value:
            return _canonical_response_function_output(value.get("content"))
        normalized = _canonical_response_function_output_part(value)
        if normalized is not None:
            return [normalized]
        for key in ("text", "input_text", "output"):
            if key in value:
                return _canonical_response_function_output(value.get(key))
    return value


def _canonical_response_function_output_part(
    part: Any,
    *,
    text_type: str = "input_text",
) -> dict[str, Any] | None:
    if isinstance(part, str):
        return {"type": text_type, "text": part}
    if not isinstance(part, dict):
        return None
    ptype = str(part.get("type") or "").strip().lower()
    if ptype in _TEXT_CONTENT_PART_TYPES:
        text = part.get("text", "")
        out: dict[str, Any] = {"type": text_type, "text": str(text)}
        if "annotations" in part:
            out["annotations"] = part["annotations"]
        return out
    if ptype in _REFUSAL_CONTENT_PART_TYPES:
        return {"type": "refusal", "refusal": str(part.get("refusal", ""))}
    if ptype in _IMAGE_CONTENT_PART_TYPES:
        image = _image_url_from_part(part)
        file_id = _image_file_id_from_part(part)
        if not image and not file_id:
            return None
        out: dict[str, Any] = {"type": "input_image"}
        if image:
            out["image_url"] = str(image)
        if file_id:
            out["file_id"] = file_id
        detail = part.get("detail")
        image_obj = part.get("image_url") or part.get("image")
        if isinstance(image_obj, dict):
            detail = image_obj.get("detail", detail)
        if detail is not None:
            out["detail"] = str(detail)
        return out
    if ptype in _FILE_CONTENT_PART_TYPES:
        if not _file_label_from_part(part):
            return None
        out: dict[str, Any] = {"type": "input_file"}
        for key in ("file_id", "file_url", "filename", "file_data", "mime_type", "detail"):
            if part.get(key) is not None:
                out[key] = str(part[key])
        return out
    if ptype in _AUDIO_CONTENT_PART_TYPES:
        if not _audio_label_from_part(part):
            return None
        out: dict[str, Any] = {"type": "input_audio"}
        audio = _audio_payload_from_part(part)
        if isinstance(audio, dict):
            out["input_audio"] = dict(audio)
        elif isinstance(audio, str):
            out["input_audio"] = {"data": audio}
        else:
            payload = {
                key: part[key]
                for key in ("data", "format", "url", "file_id", "filename")
                if part.get(key) is not None
            }
            if payload:
                out["input_audio"] = payload
        return out
    return None


def _canonical_response_message_content(value: Any, *, role: str = "user") -> list[dict[str, Any]]:
    text_type = "output_text" if str(role or "").lower() == "assistant" else "input_text"
    if isinstance(value, list):
        parts: list[dict[str, Any]] = []
        for part in value:
            normalized = _canonical_response_function_output_part(part, text_type=text_type)
            if normalized is not None:
                parts.append(normalized)
        return parts
    if isinstance(value, dict):
        if "content" in value:
            return _canonical_response_message_content(value.get("content"), role=role)
        normalized = _canonical_response_function_output_part(value, text_type=text_type)
        if normalized is not None:
            return [normalized]
        for key in ("text", "input_text", "output_text"):
            if key in value:
                return _canonical_response_message_content(value.get(key), role=role)
    if value is None:
        return [{"type": text_type, "text": ""}]
    return [{"type": text_type, "text": str(value)}]


def _canonical_response_message_item(item: dict[str, Any]) -> dict[str, Any]:
    role = str(item.get("role") or "user")
    out: dict[str, Any] = {
        "type": "message",
        "role": role,
        "content": _canonical_response_message_content(item.get("content", ""), role=role),
    }
    for key in ("id", "status", "name", "phase"):
        if item.get(key) is not None:
            out[key] = item[key]
    return out


def _canonical_function_call_output_item(item: dict[str, Any]) -> dict[str, Any]:
    out = dict(item)
    call_id = str(out.get("call_id") or out.get("tool_call_id") or "")
    if call_id:
        out["call_id"] = call_id
    if "output" in out:
        out["output"] = _canonical_response_function_output(out.get("output"))
    return out


def _canonical_function_call_item(item: dict[str, Any]) -> dict[str, Any]:
    call_id = str(item.get("call_id") or item.get("id") or "")
    name = str(item.get("name") or "")
    arguments = item.get("arguments", {})
    if isinstance(arguments, str):
        arguments_text = arguments
    else:
        arguments_text = json.dumps(
            arguments if arguments is not None else {},
            sort_keys=True,
            default=str,
        )
    out = dict(item)
    out.update({
        "type": "function_call",
        "call_id": call_id,
        "name": name,
        "arguments": arguments_text,
    })
    return out


def _canonical_opaque_response_input_item(item: dict[str, Any]) -> dict[str, Any]:
    return dict(item)


def _function_call_message(item: dict[str, Any]) -> Message:
    canonical = _canonical_function_call_item(item)
    arguments = canonical.get("arguments") or "{}"
    try:
        parsed_arguments = json.loads(arguments) if isinstance(arguments, str) and arguments else {}
    except json.JSONDecodeError:
        parsed_arguments = {"__raw__": str(arguments)}
    if not isinstance(parsed_arguments, dict):
        parsed_arguments = {"value": parsed_arguments}
    message = Message.assistant(
        "",
        tool_calls=[
            ToolCall(
                id=str(canonical.get("call_id") or ""),
                name=str(canonical.get("name") or ""),
                arguments=parsed_arguments,
            )
        ],
    )
    message.meta["response_input_item"] = canonical
    return message


def _function_call_output_message(item: dict[str, Any]) -> Message:
    call_id = str(item.get("call_id") or item.get("tool_call_id") or "")
    canonical = _canonical_function_call_output_item(item)
    message = Message(
        role="tool",
        content=_response_function_output_text(canonical.get("output")),
        tool_call_id=call_id,
        name=item.get("name"),
        meta={"response_input_item": canonical},
    )
    return message


def _opaque_response_input_item_message(item: dict[str, Any]) -> Message:
    item_type = _response_item_type(item)
    role = "tool" if item_type.endswith("_output") or item_type.endswith("_response") else "assistant"
    return Message(
        role=role,
        content="",
        meta={
            "response_input_item": _canonical_opaque_response_input_item(item),
            "_responses_opaque_input_item": True,
        },
    )


def _message_with_response_input_item(item: dict[str, Any]) -> Message:
    payload = dict(item)
    payload.pop("type", None)
    payload.setdefault("role", "user")
    message = _convert_message(payload)
    message.meta["response_input_item"] = _canonical_response_message_item(item)
    return message


def _convert_message(m: dict[str, Any]) -> Message:
    role = str(m.get("role") or "user")
    text, images = _content(m.get("content", ""))
    if role in ("system", "developer"):
        message = Message.user(f"<{role}_instructions>\n{text}\n</{role}_instructions>")
        message.meta["_instruction_role"] = role
        return message
    if role == "assistant":
        return Message.assistant(text, tool_calls=_tool_calls_from_payload(m))
    if role == "tool":
        return Message(
            role="tool",
            content=text,
            tool_call_id=m.get("tool_call_id") or m.get("call_id"),
            name=m.get("name"),
        )
    return Message.user(text, images=images)


def _convert(messages: list[dict]) -> tuple[list[Message], Message]:
    """Return (history_without_last_user, last_user_message)."""
    internal = [_convert_message(m) for m in messages]
    last_user = Message.user("")
    for i in range(len(internal) - 1, -1, -1):
        if internal[i].role == "user":
            last_user = internal.pop(i)
            break
    return internal, last_user


def _content_seed_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text") or item.get("input_text")
                if text is None and str(item.get("type") or "").strip().lower() == "output_text":
                    text = item.get("text")
                if text is not None:
                    parts.append(str(text))
            elif item is not None:
                parts.append(str(item))
        return "\n".join(parts)
    if content is None:
        return ""
    try:
        return json.dumps(content, sort_keys=True, default=str)
    except TypeError:
        return str(content)


def _derive_chat_session_id(system_prompt: str | None, first_user_message: str) -> str:
    seed = f"{system_prompt or ''}\n{first_user_message}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
    return f"api-{digest}"


def _derive_chat_session_id_from_messages(messages: list[dict]) -> str:
    system_prompt = ""
    first_user = ""
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "")
        if role == "system" and not system_prompt:
            system_prompt = _content_seed_text(message.get("content"))
        elif role == "user" and not first_user:
            first_user = _content_seed_text(message.get("content"))
            break
    return _derive_chat_session_id(system_prompt, first_user)


def _usage(source) -> dict[str, Any]:
    usage = source
    if isinstance(usage, dict):
        prompt = int(usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0)
        completion = int(usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0)
        total = int(usage.get("total_tokens", prompt + completion) or 0)
        prompt_details = usage.get("input_tokens_details") or usage.get("prompt_tokens_details") or {}
        completion_details = usage.get("output_tokens_details") or usage.get("completion_tokens_details") or {}
        return {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
            "prompt_tokens_details": dict(prompt_details) if isinstance(prompt_details, dict) else {},
            "completion_tokens_details": (
                dict(completion_details) if isinstance(completion_details, dict) else {}
            ),
        }
    if not all(hasattr(usage, key) for key in ("input_tokens", "output_tokens")):
        usage = getattr(getattr(source, "budget", None), "usage", None)
    if usage is None:
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    prompt = int(getattr(usage, "input_tokens", 0) or 0)
    completion = int(getattr(usage, "output_tokens", 0) or 0)
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
        "prompt_tokens_details": {"cached_tokens": int(getattr(usage, "cache_read", 0) or 0)},
        "completion_tokens_details": {},
    }


def _response_usage(source) -> dict[str, Any]:
    """Responses API usage with OpenAI-style keys plus chat aliases."""

    usage = _usage(source)
    prompt = int(usage.get("prompt_tokens", 0) or 0)
    completion = int(usage.get("completion_tokens", 0) or 0)
    return {
        "input_tokens": prompt,
        "output_tokens": completion,
        "total_tokens": int(usage.get("total_tokens", prompt + completion) or 0),
        "input_tokens_details": dict(usage.get("prompt_tokens_details") or {}),
        "output_tokens_details": dict(usage.get("completion_tokens_details") or {}),
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "prompt_tokens_details": dict(usage.get("prompt_tokens_details") or {}),
        "completion_tokens_details": dict(usage.get("completion_tokens_details") or {}),
    }


def _models(config: Config) -> list[dict[str, Any]]:
    from .providers import registry

    by_id: dict[str, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []

    def add(model: str | None, provider: str = "") -> None:
        mid = str(model or "").strip()
        if not mid:
            return
        if mid in by_id:
            row = by_id[mid]
            providers = row.setdefault("providers", [])
            if provider and provider not in providers:
                providers.append(provider)
            if provider and not row.get("provider"):
                row["provider"] = provider
                row["owned_by"] = provider
            return
        row = {"id": mid, "object": "model", "owned_by": provider or "aegis"}
        if provider:
            row["provider"] = provider
            row["providers"] = [provider]
        else:
            row["providers"] = []
        by_id[mid] = row
        rows.append(row)

    add(config.get("model.default"), config.get("model.provider", ""))
    for row in registry.model_inventory(config):
        add(row.get("id"), row.get("provider", ""))
    return rows


def _model(config: Config, model_id: str) -> dict[str, Any] | None:
    wanted = str(model_id or "").strip()
    if not wanted:
        return None
    for row in _models(config):
        if str(row.get("id") or "") == wanted:
            return row
    return None


def _event_metadata(event: dict[str, Any]) -> dict[str, Any]:
    keys = ("type", "name", "tool_name", "status", "summary", "preview", "is_error", "duration_ms")
    return {key: event[key] for key in keys if key in event}


def _message_payload(message: Message, index: int | None = None) -> dict[str, Any]:
    payload = message.to_dict()
    if index is not None:
        payload["index"] = index
    return payload


def _message_from_payload(payload: dict[str, Any]) -> Message:
    if "role" in payload:
        return Message.from_dict(payload)
    return Message.user(str(payload.get("content") or payload.get("text") or ""))


def _session_payload(session) -> dict[str, Any]:
    return {
        "id": session.id,
        "title": session.title,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
        "parent_id": session.parent_id,
        "profile": session.profile,
        "meta": session.meta,
        "todos": session.todos,
        "message_count": len(session.messages),
        "messages": [_message_payload(m, i) for i, m in enumerate(session.messages)],
    }


def _tool_result_text(event: dict[str, Any]) -> str:
    for key in ("preview", "summary"):
        value = event.get(key)
        if value is not None and str(value):
            return str(value)
    if "data" not in event:
        return ""
    value = event.get("data")
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except TypeError:
        return str(value)


def _response_output(text: str) -> list[dict[str, Any]]:
    return [{
        "id": new_id("msg"),
        "type": "message",
        "status": "completed",
        "role": "assistant",
        "content": [{"type": "output_text", "text": text or ""}],
    }]


def _response_output_items(result, text: str | None = None) -> list[dict[str, Any]]:
    final_text = text if text is not None else (getattr(result, "text", "") if result is not None else "")
    items: list[dict[str, Any]] = []
    pending_calls: dict[str, int] = {}
    for event in getattr(result, "events", []) or []:
        if not isinstance(event, dict):
            continue
        if event.get("type") == "tool_start":
            call_id = str(event.get("id") or event.get("tool_call_id") or new_id("call"))
            args = event.get("args", event.get("arguments", {}))
            arguments = args if isinstance(args, str) else json.dumps(args if args is not None else {}, default=str)
            pending_calls[call_id] = len(items)
            items.append({
                "id": new_id("fc"),
                "type": "function_call",
                "status": "in_progress",
                "name": str(event.get("name") or event.get("tool_name") or ""),
                "arguments": arguments,
                "call_id": call_id,
            })
            continue
        if event.get("type") == "tool_result":
            call_id = str(event.get("id") or event.get("tool_call_id") or "")
            if not call_id or call_id not in pending_calls:
                continue
            items[pending_calls[call_id]]["status"] = "completed"
            result_text = _tool_result_text(event)
            items.append({
                "id": new_id("fco"),
                "type": "function_call_output",
                "status": "failed" if event.get("is_error") else "completed",
                "call_id": call_id,
                "output": [{"type": "input_text", "text": result_text}],
            })
    return items + _response_output(final_text)


def _response_object(
    response_id: str,
    result,
    *,
    status: str = "completed",
    metadata_extra: dict[str, Any] | None = None,
    parallel_tool_calls: bool = True,
) -> dict[str, Any]:
    text = getattr(result, "text", "") if result is not None else ""
    agent = getattr(result, "agent", None)
    provider = getattr(agent, "provider", None)
    session = getattr(result, "session", None)
    metadata = {
        "session_id": getattr(session, "id", ""),
        "trace_id": getattr(result, "trace_id", ""),
        "turn_id": getattr(result, "turn_id", ""),
        "run_id": getattr(result, "run_id", ""),
    }
    metadata.update(metadata_extra or {})
    return {
        "id": response_id,
        "object": "response",
        "created_at": int(time.time()),
        "status": status,
        "model": getattr(provider, "model", ""),
        "error": None,
        "incomplete_details": None,
        "parallel_tool_calls": parallel_tool_calls,
        "output": _response_output_items(result, text),
        "output_text": text,
        "usage": _response_usage(getattr(result, "usage", None) or agent),
        "metadata": metadata,
    }


def _response_input_item_to_message(item: Any) -> Message:
    if _is_function_call_input_item(item):
        return _function_call_message(item)
    if _is_function_call_output_input_item(item):
        return _function_call_output_message(item)
    if _is_opaque_response_input_item(item):
        return _opaque_response_input_item_message(item)
    if isinstance(item, dict):
        if str(item.get("type") or "") == "message":
            return _message_with_response_input_item(item)
        if "role" in item:
            return _message_with_response_input_item(item)
        if "type" in item:
            return _message_with_response_input_item({
                "type": "message",
                "role": "user",
                "content": [item],
            })
        return _convert_message({"role": "user", "content": item})
    return _convert_message({"role": "user", "content": item})


def _responses_messages(body: dict[str, Any]) -> tuple[list[Message], Message]:
    if "input" not in body and "messages" not in body:
        last_user = Message.user("")
        last_user.meta["_responses_synthetic_prompt"] = True
        return [], last_user
    raw = body.get("messages", body.get("input", ""))
    if isinstance(raw, str):
        internal = [Message.user(raw)]
    elif isinstance(raw, list):
        internal = []
        pending_content_parts: list[dict[str, Any]] = []

        def flush_content_parts() -> None:
            nonlocal pending_content_parts
            if not pending_content_parts:
                return
            internal.append(_response_input_item_to_message({
                "type": "message",
                "role": "user",
                "content": pending_content_parts,
            }))
            pending_content_parts = []

        for item in raw:
            if _is_response_content_part_item(item):
                pending_content_parts.append(dict(item))
                continue
            flush_content_parts()
            internal.append(_response_input_item_to_message(item))
        flush_content_parts()
    elif isinstance(raw, dict):
        internal = [_response_input_item_to_message(raw)]
    else:
        internal = [Message.user(str(raw or ""))]

    last_user: Message | None = None
    trailing_non_instruction = False
    for i in range(len(internal) - 1, -1, -1):
        message = internal[i]
        if message.role == "user" and not _is_instruction_wrapper(message):
            if not trailing_non_instruction:
                last_user = internal.pop(i)
            break
        if not _is_instruction_wrapper(message):
            trailing_non_instruction = True
    if last_user is None:
        last_user = Message.user("")
        last_user.meta["_responses_synthetic_prompt"] = True
    return internal, last_user


def _parse_response_history(value: Any) -> tuple[list[Message], str]:
    if value in (None, ""):
        return [], ""
    if not isinstance(value, list):
        return [], "'conversation_history' must be an array of message objects"
    messages: list[Message] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict) or "role" not in item:
            return [], f"conversation_history[{index}] must have 'role' and 'content' fields"
        messages.append(_convert_message(item))
    return messages, ""


def _instruction_message(instructions: str | None) -> Message | None:
    text = str(instructions or "").strip()
    if not text:
        return None
    return Message.user(f"<system_instructions>\n{text}\n</system_instructions>")


def _is_instruction_wrapper(message: Message) -> bool:
    meta_role = str((getattr(message, "meta", {}) or {}).get("_instruction_role") or "")
    if message.role == "user" and meta_role in {"system", "developer"}:
        return True
    text = (message.content or "").lstrip()
    return message.role == "user" and (
        text.startswith("<system_instructions>")
        or text.startswith("<developer_instructions>")
    )


def _is_synthetic_response_prompt(message: Message) -> bool:
    return bool((getattr(message, "meta", {}) or {}).get("_responses_synthetic_prompt"))


def _is_opaque_response_item_message(message: Message) -> bool:
    return bool((getattr(message, "meta", {}) or {}).get("_responses_opaque_input_item"))


def _history_payload(messages: list[Message]) -> list[dict[str, Any]]:
    return [
        _message_payload(m)
        for m in messages
        if (
            not _is_instruction_wrapper(m)
            and not _is_synthetic_response_prompt(m)
            and not _is_opaque_response_item_message(m)
        )
    ]


def _history_from_state(state: dict[str, Any] | None) -> list[Message]:
    if not state:
        return []
    raw = state.get("conversation_history")
    if not isinstance(raw, list):
        return []
    messages: list[Message] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            messages.append(Message.from_dict(item))
        except Exception:  # noqa: BLE001
            try:
                messages.append(_convert_message(item))
            except Exception:  # noqa: BLE001
                continue
    return messages


def _response_conversation_history(
    prior_history: list[Message],
    last_user: Message,
    result,
) -> list[dict[str, Any]]:
    session = getattr(result, "session", None)
    session_messages = getattr(session, "messages", None)
    if isinstance(session_messages, list) and session_messages:
        filtered = [
            m for m in session_messages
            if (
                isinstance(m, Message)
                and m.role != "system"
                and not _is_instruction_wrapper(m)
                and not _is_synthetic_response_prompt(m)
            )
        ]
        if filtered and len(filtered) >= len(prior_history) + 1:
            return _history_payload(filtered)
    history = list(prior_history)
    history.append(last_user)
    text = getattr(result, "text", "") if result is not None else ""
    message = getattr(result, "message", None)
    history.append(message if isinstance(message, Message) else Message.assistant(text))
    return _history_payload(history)


def _response_input_items(messages: list[Message], instructions: str | None = None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if instructions:
        items.append({
            "type": "message",
            "role": "system",
            "content": [{"type": "input_text", "text": str(instructions)}],
        })
    for message in messages:
        if not isinstance(message, Message) or _is_synthetic_response_prompt(message):
            continue
        raw_item = (message.meta or {}).get("response_input_item")
        if _is_function_call_output_input_item(raw_item):
            items.append(_canonical_function_call_output_item(raw_item))
            continue
        if _is_function_call_input_item(raw_item):
            items.append(_canonical_function_call_item(raw_item))
            continue
        if _is_opaque_response_input_item(raw_item):
            items.append(_canonical_opaque_response_input_item(raw_item))
            continue
        if isinstance(raw_item, dict) and str(raw_item.get("type") or "") == "message":
            items.append(_canonical_response_message_item(raw_item))
            continue
        if _is_instruction_wrapper(message):
            continue
        if message.role == "assistant" and message.tool_calls:
            if (message.content or "").strip():
                items.append(_canonical_response_message_item({
                    "type": "message",
                    "role": "assistant",
                    "content": message.content or "",
                }))
            for tool_call in message.tool_calls:
                items.append(_canonical_function_call_item({
                    "type": "function_call",
                    "call_id": getattr(tool_call, "id", "") or "",
                    "name": getattr(tool_call, "name", "") or "",
                    "arguments": getattr(tool_call, "arguments", {}) or {},
                }))
            continue
        if message.role == "tool":
            items.append({
                "type": "function_call_output",
                "call_id": message.tool_call_id or "",
                "output": [{"type": "input_text", "text": str(message.content or "")}],
            })
            continue
        items.append(_canonical_response_message_item({
            "type": "message",
            "role": message.role or "user",
            "content": message.content or "",
        }))
    return items


def _clean_stored_input_item(item: dict[str, Any]) -> dict[str, Any]:
    row = dict(item)
    row.pop("object", None)
    row.pop("response_id", None)
    return row


def _prior_opaque_response_input_items(state: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(state, dict):
        return []
    raw = state.get("input_items")
    if not isinstance(raw, list):
        return []
    return [
        _clean_stored_input_item(item)
        for item in raw
        if isinstance(item, dict) and _is_opaque_response_input_item(item)
    ]


def _stored_response_input_items(
    messages: list[Message],
    instructions: str | None = None,
    *,
    previous_state: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    items = _response_input_items(messages, instructions)
    prior_opaque = _prior_opaque_response_input_items(previous_state)
    if not prior_opaque:
        return items
    existing = {
        (str(item.get("type") or ""), str(item.get("id") or ""))
        for item in items
        if isinstance(item, dict) and item.get("id") is not None
    }
    prepend = [
        item for item in prior_opaque
        if (str(item.get("type") or ""), str(item.get("id") or "")) not in existing
    ]
    if not prepend:
        return items
    insertion = 1 if items and items[0].get("role") == "system" else 0
    return items[:insertion] + prepend + items[insertion:]


def _state_input_items(state: dict[str, Any], response_id: str) -> list[dict[str, Any]]:
    raw = state.get("input_items")
    if isinstance(raw, list) and raw:
        items = [item for item in raw if isinstance(item, dict)]
    else:
        items = _response_input_items(_history_from_state(state), state.get("instructions"))
    out: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        row = dict(item)
        row.setdefault("id", f"{response_id}_input_{index}")
        row.setdefault("object", "response.input_item")
        row.setdefault("response_id", response_id)
        out.append(row)
    return out


def _token_count_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        return "\n".join(_token_count_text(item) for item in value)
    if isinstance(value, dict):
        parts: list[str] = []
        for key in (
            "role",
            "type",
            "name",
            "text",
            "input_text",
            "output_text",
            "refusal",
            "arguments",
            "output",
            "summary",
            "encrypted_content",
            "content",
            "tool_call_id",
            "call_id",
            "file_id",
            "file_url",
            "image_url",
            "audio",
        ):
            if key in value:
                parts.append(_token_count_text(value.get(key)))
        if parts:
            return "\n".join(part for part in parts if part)
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except TypeError:
        return str(value)


def _estimate_response_input_tokens(
    input_items: list[dict[str, Any]],
    *,
    body: dict[str, Any] | None = None,
) -> int:
    text = _token_count_text(input_items)
    body = body or {}
    for key in ("tools", "tool_choice", "response_format", "text", "reasoning"):
        if key in body:
            text = f"{text}\n{_token_count_text(body.get(key))}"
    return estimate_tokens(text)


def _response_compaction_message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict):
                for key in ("text", "input_text", "output_text", "refusal"):
                    if part.get(key) is not None:
                        parts.append(str(part.get(key) or ""))
                        break
                else:
                    parts.append(_token_count_text(part))
            elif part is not None:
                parts.append(str(part))
        return "\n".join(part for part in parts if part)
    if isinstance(content, dict):
        for key in ("text", "input_text", "output_text", "refusal"):
            if content.get(key) is not None:
                return str(content.get(key) or "")
    return _token_count_text(content)


def _response_compaction_summary(input_items: list[dict[str, Any]], input_tokens: int) -> str:
    roles: dict[str, int] = {}
    last_user = ""
    message_previews: list[str] = []
    for item in input_items:
        if not isinstance(item, dict) or str(item.get("type") or "") != "message":
            continue
        role = str(item.get("role") or "user")
        roles[role] = roles.get(role, 0) + 1
        text = " ".join(_response_compaction_message_text(item.get("content")).split())
        if text:
            message_previews.append(f"{role}: {text[:240]}")
        if role == "user":
            if text.strip():
                last_user = text.strip()
    role_text = ", ".join(f"{role}:{count}" for role, count in sorted(roles.items())) or "none"
    parts = [
        f"Compacted {len(input_items)} response input item(s).",
        f"Estimated input tokens before compaction: {input_tokens}.",
        f"Message roles: {role_text}.",
    ]
    if message_previews:
        parts.append("Recent turns:\n" + "\n".join(f"- {line}" for line in message_previews[-8:]))
    if last_user:
        parts.append(f"Most recent user input: {last_user[:1000]}")
    return "\n".join(parts)


def _response_compaction_encrypted_content(summary: str) -> str:
    digest = hashlib.sha256(summary.encode("utf-8")).hexdigest()
    return f"aegis-local-compaction:{digest}"


def _with_response_message_defaults(item: dict[str, Any]) -> dict[str, Any]:
    row = dict(item)
    row.setdefault("id", new_id("msg"))
    row.setdefault("status", "completed")
    return row


def _response_compaction_output_items(input_items: list[dict[str, Any]], summary: str) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    first_message: dict[str, Any] | None = None
    for item in input_items:
        if not isinstance(item, dict) or str(item.get("type") or "") != "message":
            continue
        role = str(item.get("role") or "user")
        if role in {"system", "developer"}:
            output.append(_with_response_message_defaults(item))
            continue
        if first_message is None:
            first_message = item
    if first_message is not None:
        output.append(_with_response_message_defaults(first_message))
    output.append({
        "id": new_id("cmp"),
        "type": "compaction",
        "encrypted_content": _response_compaction_encrypted_content(summary),
    })
    return output


def _history_from_compaction_output(output_items: list[dict[str, Any]], summary: str | None = None) -> list[dict[str, Any]]:
    messages: list[Message] = []
    for item in output_items:
        if isinstance(item, dict) and str(item.get("type") or "") == "message":
            role = str(item.get("role") or "user")
            if role not in {"system", "developer"}:
                messages.append(_response_input_item_to_message(item))
    if summary:
        messages.append(Message(
            role="assistant",
            content="Compaction summary:\n" + summary,
            meta={"_responses_compaction_summary": True},
        ))
    return _history_payload(messages)


def _response_input_items_payload(
    state: dict[str, Any],
    response_id: str,
    query: dict[str, list[str]],
) -> dict[str, Any]:
    limit, order, _error = _response_input_items_params(query)
    if _error:
        limit, order = 20, "desc"
    after = str((query.get("after") or [""])[0] or "")
    before = str((query.get("before") or [""])[0] or "")
    items = _state_input_items(state, response_id)
    total_count = len(items)
    if order == "desc":
        items = list(reversed(items))
    start = 0
    end = len(items)
    ids = [str(item.get("id") or "") for item in items]
    if after:
        start = ids.index(after) + 1 if after in ids else len(items)
    if before:
        end = ids.index(before) if before in ids else 0
    window = items[start:end]
    data = window[:limit]
    return {
        "object": "list",
        "data": data,
        "response_id": response_id,
        "limit": limit,
        "order": order,
        "total_count": total_count,
        "has_more": limit < len(window),
        "first_id": data[0]["id"] if data else None,
        "last_id": data[-1]["id"] if data else None,
    }


def _response_input_items_params(query: dict[str, list[str]]) -> tuple[int, str, dict[str, Any] | None]:
    raw_limit = (query.get("limit") or ["20"])[0] or 20
    try:
        limit = int(raw_limit)
    except (TypeError, ValueError):
        return 20, "desc", _openai_error(
            "'limit' must be an integer between 1 and 100",
            code="invalid_limit",
            param="limit",
        )
    if limit < 1 or limit > 100:
        return 20, "desc", _openai_error(
            "'limit' must be between 1 and 100",
            code="invalid_limit",
            param="limit",
        )
    order = str((query.get("order") or ["desc"])[0] or "desc").strip().lower()
    if order not in {"asc", "desc"}:
        return 20, "desc", _openai_error(
            "'order' must be either 'asc' or 'desc'",
            code="invalid_order",
            param="order",
        )
    return limit, order, None


def _response_truncation_mode(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("type") or value.get("mode")
    return str(value or "").strip().lower()


def _responses_auto_truncation_limit(config: Config) -> int:
    raw = config.get(
        "server.responses_auto_truncation_messages",
        _DEFAULT_RESPONSE_AUTO_TRUNCATION_MESSAGES,
    )
    return max(1, int(raw or _DEFAULT_RESPONSE_AUTO_TRUNCATION_MESSAGES))


def _maybe_truncate_response_history(
    messages: list[Message],
    body: dict[str, Any],
    config: Config,
) -> list[Message]:
    if _response_truncation_mode(body.get("truncation")) != "auto":
        return messages
    limit = _responses_auto_truncation_limit(config)
    if len(messages) <= limit:
        return messages
    return list(messages[-limit:])


def _capabilities(config: Config) -> dict[str, Any]:
    from .providers import registry

    try:
        report = registry.provider_report(config)
    except Exception:  # noqa: BLE001
        report = {}
    endpoint_descriptors = [
        {"name": "chat_completions", "path": "/v1/chat/completions", "methods": ["POST"], "streaming": True},
        {"name": "responses", "path": "/v1/responses", "methods": ["POST"], "streaming": True,
         "stateful": True},
        {"name": "responses.retrieve", "path": "/v1/responses/{response_id}", "methods": ["GET", "DELETE"]},
        {"name": "responses.input_items", "path": "/v1/responses/{response_id}/input_items", "methods": ["GET"]},
        {"name": "responses.input_tokens", "path": "/v1/responses/input_tokens", "methods": ["POST"]},
        {"name": "responses.cancel", "path": "/v1/responses/{response_id}/cancel", "methods": ["POST"]},
        {"name": "responses.compact", "path": "/v1/responses/compact", "methods": ["POST"]},
        {"name": "conversations", "path": "/v1/conversations", "methods": ["GET", "POST"]},
        {"name": "conversations.retrieve", "path": "/v1/conversations/{conversation_id}", "methods": ["GET", "DELETE"]},
        {"name": "conversations.items", "path": "/v1/conversations/{conversation_id}/items", "methods": ["GET"]},
        {"name": "models", "path": "/v1/models", "methods": ["GET"]},
        {"name": "models.retrieve", "path": "/v1/models/{model_id}", "methods": ["GET"]},
        {"name": "health", "path": "/v1/health", "methods": ["GET"]},
        {"name": "health.detailed", "path": "/v1/health/detailed", "methods": ["GET"]},
        {"name": "skills", "path": "/v1/skills", "methods": ["GET"]},
        {"name": "toolsets", "path": "/v1/toolsets", "methods": ["GET"]},
        {"name": "runs", "path": "/v1/runs", "methods": ["GET", "POST"]},
        {"name": "runs.detail", "path": "/v1/runs/{run_id}", "methods": ["GET"]},
        {"name": "runs.events", "path": "/v1/runs/{run_id}/events", "methods": ["GET"], "streaming": True},
        {"name": "runs.stop", "path": "/v1/runs/{run_id}/stop", "methods": ["POST"]},
        {"name": "runs.approval", "path": "/v1/runs/{run_id}/approval", "methods": ["GET", "POST"]},
        {"name": "jobs", "path": "/api/jobs", "methods": ["GET", "POST"]},
        {"name": "jobs.detail", "path": "/api/jobs/{job_id}", "methods": ["GET", "PATCH", "DELETE"]},
        {"name": "jobs.control", "path": "/api/jobs/{job_id}/{pause|resume|run|trigger}", "methods": ["POST"]},
        {"name": "sessions", "path": "/api/sessions", "methods": ["GET", "POST"]},
        {"name": "sessions.chat", "path": "/api/sessions/{session_id}/chat", "methods": ["POST"], "streaming": True},
        {"name": "session_checks", "path": "/api/session-checks", "methods": ["GET", "POST"]},
        {"name": "session_checks.repair", "path": "/api/session-checks/repair", "methods": ["POST"]},
    ]
    return {
        "object": "hermes.api_server.capabilities",
        "legacy_object": "capabilities",
        "server": "aegis",
        "transport": "aiohttp",
        "auth": {
            "type": "bearer",
            "required": bool(config.get("server.api_key") or os.environ.get("AEGIS_SERVER_KEY")),
        },
        "limits": {
            "max_body_bytes": _MAX_BODY_BYTES,
            "max_concurrent_runs": int(config.get("server.max_concurrent_runs", 8) or 8),
            "run_events_timeout_seconds": float(config.get("server.run_events_timeout_seconds", 3600) or 3600),
            "responses_auto_truncation_messages": _responses_auto_truncation_limit(config),
        },
        "endpoints": {
            "chat_completions": True,
            "responses": True,
            "models": True,
            "sessions": True,
            "runs": True,
            "streaming": True,
            "health": True,
            "skills": True,
            "toolsets": True,
            "jobs": True,
        },
        "endpoint_descriptors": endpoint_descriptors,
        "features": {
            "tools": True,
            "mcp": True,
            "sessions": True,
            "run_history": True,
            "trace_events": True,
            "responses_persistence": True,
            "response_input_items": True,
            "previous_response_id": True,
            "response_conversations": True,
            "responses_truncation_auto": True,
            "idempotency_keys": True,
            "sse_named_events": True,
            "tool_progress_events": True,
            "cors_options": True,
            "run_approvals": True,
            "job_control": True,
            "cancellation": "active server-process runs",
            "session_continuity_header": "X-Hermes-Session-Id",
            "session_key_header": "X-Hermes-Session-Key",
        },
        "provider": report.get("active") or report.get("model") or {},
    }


def _skills_payload(config: Config) -> dict[str, Any]:
    from .skills import SkillsLoader

    rows = []
    for skill in sorted(SkillsLoader(config).available(), key=lambda s: s.name):
        rows.append({
            "id": skill.name,
            "object": "skill",
            "name": skill.name,
            "description": skill.description,
            "path": str(skill.path),
            "directory": str(skill.dir),
            "source": getattr(skill, "source", ""),
            "toolsets": list(getattr(skill, "toolsets", []) or []),
        })
    return {"object": "list", "data": rows, "count": len(rows)}


def _toolsets_payload(config: Config) -> dict[str, Any]:
    from .dashboard import _dashboard_toolsets

    rows = []
    for row in _dashboard_toolsets(config):
        name = str(row.get("name") or "")
        rows.append({"id": name, "object": "toolset", **row})
    return {
        "object": "list",
        "data": rows,
        "count": len(rows),
        "active": [row["name"] for row in rows if bool(row.get("enabled"))],
    }


def _job_payload(job) -> dict[str, Any]:
    return {
        "id": job.id,
        "object": "job",
        "name": getattr(job, "name", "") or "",
        "schedule": job.schedule,
        "prompt": job.prompt,
        "enabled": bool(job.enabled),
        "channel": job.channel,
        "deliver": job.deliver,
        "script": job.script,
        "skills": list(job.skills or []),
        "context_from": list(getattr(job, "context_from", []) or []),
        "no_agent": bool(job.no_agent),
        "model": getattr(job, "model", "") or "",
        "enabled_toolsets": list(getattr(job, "enabled_toolsets", []) or []),
        "workdir": getattr(job, "workdir", "") or "",
        "state": job.state,
        "last_error": job.last_error,
        "last_run": job.last_run,
        "next_run": job.next_run,
        "run_count": int(getattr(job, "run_count", 0) or 0),
        "max_runs": int(getattr(job, "max_runs", 0) or 0),
        "runs": list(getattr(job, "runs", []) or []),
    }


def _jobs_summary(jobs: list[Any]) -> dict[str, Any]:
    states: dict[str, int] = {}
    next_runs = []
    error_jobs = []
    for job in jobs:
        state = str(getattr(job, "state", "") or "idle")
        states[state] = states.get(state, 0) + 1
        next_run = float(getattr(job, "next_run", 0.0) or 0.0)
        if next_run > 0:
            next_runs.append(next_run)
        if str(getattr(job, "last_error", "") or ""):
            error_jobs.append(str(getattr(job, "id", "") or ""))
    return {
        "count": len(jobs),
        "enabled": sum(1 for job in jobs if bool(getattr(job, "enabled", False))),
        "paused": sum(1 for job in jobs if not bool(getattr(job, "enabled", False))),
        "states": states,
        "running": states.get("running", 0),
        "errors": states.get("error", 0),
        "last_error_count": len(error_jobs),
        "error_job_ids": error_jobs[:20],
        "next_run": min(next_runs) if next_runs else 0.0,
    }


def _epoch_from_run_time(value: Any) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value or "").strip()
    if not text:
        return 0
    try:
        return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return 0


def _public_api_run_status(status: Any) -> str:
    text = str(status or "")
    if text == "ok":
        return "completed"
    if text == "cancelling":
        return "stopping"
    return text


def _with_hermes_run_aliases(record: dict[str, Any]) -> dict[str, Any]:
    out = dict(record)
    run_id = str(out.get("run_id") or out.get("id") or "")
    out["id"] = str(out.get("id") or run_id)
    out["run_id"] = run_id
    out["object"] = "hermes.run"
    out["status"] = _public_api_run_status(out.get("status"))
    out["output"] = out.get("output", out.get("result", ""))
    return out


def _public_stored_run_record(run: dict[str, Any]) -> dict[str, Any]:
    data = run.get("data") if isinstance(run.get("data"), dict) else {}
    public_status = _public_api_run_status(run.get("status"))
    created_at = _epoch_from_run_time(data.get("created_at") or run.get("started_at"))
    updated_at = _epoch_from_run_time(data.get("updated_at") or run.get("ended_at") or run.get("started_at"))
    return _with_hermes_run_aliases({
        "id": run.get("id", ""),
        "legacy_object": data.get("object") or "run",
        "status": public_status,
        "created_at": created_at,
        "updated_at": updated_at,
        "session_id": run.get("session_id", ""),
        "result": run.get("result_preview", ""),
        "error": run.get("error", ""),
        "trace_id": run.get("trace_id", ""),
        "surface_run_id": data.get("surface_run_id", ""),
        "cancel_reason": data.get("cancel_reason", ""),
        "last_event": data.get("last_event") or (f"run.{public_status}" if public_status else ""),
        "model": data.get("model", ""),
        "session_key": data.get("session_key", ""),
        "surface": run.get("surface", ""),
        "kind": run.get("kind", ""),
        "title": run.get("title", ""),
        "started_at": run.get("started_at", ""),
        "ended_at": run.get("ended_at", ""),
        "prompt_preview": run.get("prompt_preview", ""),
    })


def _persist_api_run_record(record: dict[str, Any], *, title: str = "", prompt: str = "") -> None:
    run_id = str(record.get("id") or "")
    if not run_id:
        return
    from .runs import RunStore

    store = RunStore()
    row = store.get(run_id)
    data = dict(row.get("data") or {}) if row else {}
    data.update({
        "api": "runs",
        "object": record.get("object") or "run",
        "server_run_id": run_id,
        "created_at": record.get("created_at") or data.get("created_at") or int(time.time()),
        "updated_at": record.get("updated_at") or int(time.time()),
        "last_event": record.get("last_event") or data.get("last_event", ""),
        "model": record.get("model") or data.get("model", ""),
        "session_key": record.get("session_key") or data.get("session_key", ""),
        "surface_run_id": record.get("surface_run_id") or data.get("surface_run_id", ""),
        "cancel_reason": record.get("cancel_reason") or data.get("cancel_reason", ""),
    })
    events = record.get("events")
    if isinstance(events, list):
        data["events"] = [dict(event) for event in events[-_MAX_STORED_RUN_EVENTS:] if isinstance(event, dict)]
        data["event_sequence"] = int(record.get("event_sequence") or len(events))
    status = str(record.get("status") or "running")
    if row is None:
        row = {
            "id": run_id,
            "surface": "serve",
            "kind": "serve",
            "status": status,
            "title": title,
            "session_id": record.get("session_id", ""),
            "trace_id": record.get("trace_id", ""),
            "started_at": "",
            "ended_at": "",
            "prompt_preview": prompt[:1200],
            "result_preview": "",
            "error": "",
            "data": data,
        }
    row["status"] = status
    if title:
        row["title"] = title
    if prompt and not row.get("prompt_preview"):
        row["prompt_preview"] = prompt[:1200]
    row["session_id"] = record.get("session_id") or row.get("session_id", "")
    row["trace_id"] = record.get("trace_id") or row.get("trace_id", "")
    row["result_preview"] = record.get("result") or row.get("result_preview", "")
    row["error"] = record.get("error") or row.get("error", "")
    if status in _TERMINAL_RUN_STATUSES and not row.get("ended_at"):
        row["ended_at"] = now_iso()
    row["data"] = data
    store.write(row)


def _append_stored_run_event(
    data: dict[str, Any],
    run_id: str,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    events = data.get("events")
    if not isinstance(events, list):
        events = []
        data["events"] = events
    sequence = int(data.get("event_sequence") or len(events))
    event = {
        "id": new_id("evt"),
        "object": "hermes.run.event",
        "type": event_type,
        "event": event_type,
        "run_id": run_id,
        "sequence_number": sequence,
        "created_at": int(time.time()),
    }
    if payload:
        event.update(payload)
    events.append(event)
    if len(events) > _MAX_STORED_RUN_EVENTS:
        del events[:-_MAX_STORED_RUN_EVENTS]
    data["event_sequence"] = sequence + 1
    data["last_event"] = event_type
    return event


def _close_pending_stored_approvals(data: dict[str, Any], run_id: str, reason: str) -> int:
    events = data.get("events") if isinstance(data.get("events"), list) else []
    requests: dict[str, dict[str, Any]] = {}
    responded: set[str] = set()
    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or event.get("event") or "")
        approval_id = str(event.get("approval_id") or "")
        if not approval_id:
            continue
        if event_type == "approval.request":
            requests[approval_id] = event
        elif event_type == "approval.responded":
            responded.add(approval_id)
    closed = 0
    for approval_id, request in requests.items():
        if approval_id in responded:
            continue
        _append_stored_run_event(data, run_id, "approval.responded", {
            "approval_id": approval_id,
            "approved": False,
            "choice": "deny",
            "cancelled": True,
            "reason": reason,
            "prompt": str(request.get("prompt") or ""),
        })
        closed += 1
    return closed


def _recover_stale_api_runs() -> int:
    try:
        from .runs import RunStore

        store = RunStore()
        stale: list[dict[str, Any]] = []
        for status in ("queued", "running", "cancelling"):
            stale.extend(store.list(surface="serve", status=status, limit=500))
    except Exception:  # noqa: BLE001
        return 0
    recovered = 0
    reason = "AEGIS API server restarted before this run completed."
    for row in stale:
        data = row.get("data") if isinstance(row.get("data"), dict) else {}
        if data.get("api") != "runs" and data.get("server_run_id") != row.get("id"):
            continue
        run_id = str(row["id"])
        data = dict(data)
        _close_pending_stored_approvals(data, run_id, reason)
        events = data.get("events") if isinstance(data.get("events"), list) else []
        if not any(
            isinstance(event, dict)
            and str(event.get("type") or event.get("event") or "") == "run.interrupted"
            for event in events
        ):
            _append_stored_run_event(data, run_id, "run.interrupted", {
                "status": "interrupted",
                "reason": reason,
                "session_id": row.get("session_id", ""),
            })
        data.update({
            "interrupted_by_server_start": True,
            "last_event": "run.interrupted",
        })
        try:
            store.finish(
                run_id,
                status="interrupted",
                error=reason,
                data=data,
            )
        except Exception:  # noqa: BLE001
            continue
        recovered += 1
    if recovered:
        suffix = "" if recovered == 1 else "s"
        print(f"  ! recovered {recovered} stale API run{suffix}")
    return recovered


def _public_run_record(record: dict[str, Any]) -> dict[str, Any]:
    public = {k: v for k, v in record.items() if k not in {"agent", "thread", "events"}}
    public["legacy_object"] = public.get("object") or "run"
    return _with_hermes_run_aliases(public)


def _coerce_csv_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw = value.split(",")
    elif isinstance(value, list):
        raw = value
    else:
        raw = [value]
    out: list[str] = []
    for item in raw:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def _conversation_id(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("id") or value.get("conversation_id")
    text = str(value or "").strip()
    return text


class ResponseStore:
    """SQLite-backed state store for the OpenAI-compatible Responses surface."""

    def __init__(self, config: Config):
        self.path = cfg_paths.sub("server_responses.sqlite3")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_size = max(1, int(config.get("server.responses_store_max", _DEFAULT_MAX_STORED_RESPONSES)
                                   or _DEFAULT_MAX_STORED_RESPONSES))
        self._lock = threading.RLock()
        with self._connect() as db:
            self._configure_db(db)
            db.execute(
                "CREATE TABLE IF NOT EXISTS responses ("
                "id TEXT PRIMARY KEY, created_at INTEGER NOT NULL, "
                "status TEXT NOT NULL, body TEXT NOT NULL, "
                "accessed_at REAL NOT NULL DEFAULT 0)"
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(responses)").fetchall()}
            if "accessed_at" not in columns:
                db.execute("ALTER TABLE responses ADD COLUMN accessed_at REAL NOT NULL DEFAULT 0")
            db.execute(
                "CREATE TABLE IF NOT EXISTS conversations ("
                "name TEXT PRIMARY KEY, response_id TEXT NOT NULL, "
                "created_at INTEGER NOT NULL DEFAULT 0, "
                "updated_at INTEGER NOT NULL DEFAULT 0, "
                "metadata TEXT NOT NULL DEFAULT '{}')"
            )
            conversation_columns = {
                row[1] for row in db.execute("PRAGMA table_info(conversations)").fetchall()
            }
            for column, ddl in (
                ("created_at", "ALTER TABLE conversations ADD COLUMN created_at INTEGER NOT NULL DEFAULT 0"),
                ("updated_at", "ALTER TABLE conversations ADD COLUMN updated_at INTEGER NOT NULL DEFAULT 0"),
                ("metadata", "ALTER TABLE conversations ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'"),
            ):
                if column not in conversation_columns:
                    db.execute(ddl)
        self._tighten_file_permissions()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.path), timeout=30)

    def _configure_db(self, db: sqlite3.Connection) -> None:
        try:
            db.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:
            try:
                db.execute("PRAGMA journal_mode=DELETE")
            except sqlite3.DatabaseError:
                pass
        try:
            db.execute("PRAGMA busy_timeout=30000")
        except sqlite3.DatabaseError:
            pass

    def _tighten_file_permissions(self) -> None:
        for candidate in (self.path, self.path.with_name(self.path.name + "-wal"),
                          self.path.with_name(self.path.name + "-shm")):
            try:
                if candidate.exists():
                    candidate.chmod(0o600)
            except OSError:
                pass

    def _state_payload(self, response: dict[str, Any], state: dict[str, Any] | None = None) -> dict[str, Any]:
        state = dict(state or {})
        if isinstance(state.get("response"), dict):
            payload = state
            payload["response"] = response
            return payload
        return {
            "response": response,
            "conversation_history": list(state.get("conversation_history") or []),
            "input_items": list(state.get("input_items") or []),
            "instructions": state.get("instructions"),
            "session_id": state.get("session_id") or (response.get("metadata") or {}).get("session_id"),
            "conversation": state.get("conversation") or (response.get("metadata") or {}).get("conversation"),
        }

    @staticmethod
    def _normalize_state(body: dict[str, Any]) -> dict[str, Any]:
        if isinstance(body.get("response"), dict):
            body.setdefault("conversation_history", [])
            body.setdefault("input_items", [])
            body.setdefault("instructions", None)
            body.setdefault("session_id", (body.get("response", {}).get("metadata") or {}).get("session_id"))
            return body
        return {
            "response": body,
            "conversation_history": body.get("_conversation_history", []),
            "input_items": body.get("_input_items", []),
            "instructions": body.get("instructions"),
            "session_id": (body.get("metadata") or {}).get("session_id"),
            "conversation": (body.get("metadata") or {}).get("conversation"),
        }

    def put(self, response: dict[str, Any], state: dict[str, Any] | None = None) -> None:
        payload = self._state_payload(response, state)
        body = json.dumps(payload, default=str)
        response_id = str(response.get("id") or "")
        now = time.time()
        with self._lock, self._connect() as db:
            self._configure_db(db)
            db.execute(
                "INSERT OR REPLACE INTO responses (id, created_at, status, body, accessed_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    response_id,
                    int(response.get("created_at") or time.time()),
                    str(response.get("status") or ""),
                    body,
                    now,
                ),
            )
            count = int(db.execute("SELECT COUNT(*) FROM responses").fetchone()[0] or 0)
            if count > self.max_size:
                evict = [
                    row[0] for row in db.execute(
                        "SELECT id FROM responses ORDER BY accessed_at ASC LIMIT ?",
                        (count - self.max_size,),
                    ).fetchall()
                ]
                if evict:
                    placeholders = ",".join("?" for _ in evict)
                    db.execute(f"DELETE FROM conversations WHERE response_id IN ({placeholders})", evict)
                    db.execute(f"DELETE FROM responses WHERE id IN ({placeholders})", evict)
        self._tighten_file_permissions()

    def get_state(self, response_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as db:
            self._configure_db(db)
            row = db.execute("SELECT body FROM responses WHERE id = ?", (response_id,)).fetchone()
            if row is None:
                return None
            db.execute("UPDATE responses SET accessed_at = ? WHERE id = ?", (time.time(), response_id))
        try:
            body = json.loads(row[0])
        except (TypeError, json.JSONDecodeError):
            with self._lock, self._connect() as db:
                db.execute("DELETE FROM conversations WHERE response_id = ?", (response_id,))
                db.execute("DELETE FROM responses WHERE id = ?", (response_id,))
            return None
        return self._normalize_state(body) if isinstance(body, dict) else None

    def get(self, response_id: str) -> dict[str, Any] | None:
        state = self.get_state(response_id)
        if state is None:
            return None
        response = state.get("response")
        return response if isinstance(response, dict) else None

    def delete(self, response_id: str) -> bool:
        with self._lock, self._connect() as db:
            self._configure_db(db)
            db.execute("DELETE FROM conversations WHERE response_id = ?", (response_id,))
            cur = db.execute("DELETE FROM responses WHERE id = ?", (response_id,))
            return cur.rowcount > 0

    def get_conversation(self, name: str) -> str | None:
        with self._lock, self._connect() as db:
            self._configure_db(db)
            row = db.execute("SELECT response_id FROM conversations WHERE name = ?", (name,)).fetchone()
        return str(row[0]) if row else None

    def _conversation_payload(self, row: sqlite3.Row | tuple[Any, ...]) -> dict[str, Any]:
        name, response_id, created_at, updated_at, metadata = row
        try:
            parsed_metadata = json.loads(metadata or "{}")
        except (TypeError, json.JSONDecodeError):
            parsed_metadata = {}
        if not isinstance(parsed_metadata, dict):
            parsed_metadata = {}
        return {
            "id": str(name or ""),
            "object": "conversation",
            "created_at": int(created_at or 0),
            "updated_at": int(updated_at or 0),
            "metadata": parsed_metadata,
            "latest_response_id": str(response_id or "") or None,
        }

    def get_conversation_record(self, name: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as db:
            self._configure_db(db)
            row = db.execute(
                "SELECT name, response_id, created_at, updated_at, metadata "
                "FROM conversations WHERE name = ?",
                (name,),
            ).fetchone()
        return self._conversation_payload(row) if row else None

    def list_conversations(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit or 100), 1000))
        offset = max(0, int(offset or 0))
        with self._lock, self._connect() as db:
            self._configure_db(db)
            rows = db.execute(
                "SELECT name, response_id, created_at, updated_at, metadata "
                "FROM conversations ORDER BY updated_at DESC, name ASC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [self._conversation_payload(row) for row in rows]

    def create_conversation(self, name: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        now = int(time.time())
        metadata_blob = json.dumps(metadata or {}, sort_keys=True, default=str)
        with self._lock, self._connect() as db:
            self._configure_db(db)
            db.execute(
                "INSERT OR IGNORE INTO conversations "
                "(name, response_id, created_at, updated_at, metadata) VALUES (?, ?, ?, ?, ?)",
                (name, "", now, now, metadata_blob),
            )
            row = db.execute(
                "SELECT name, response_id, created_at, updated_at, metadata "
                "FROM conversations WHERE name = ?",
                (name,),
            ).fetchone()
        return self._conversation_payload(row)

    def set_conversation(self, name: str, response_id: str) -> None:
        now = int(time.time())
        with self._lock, self._connect() as db:
            self._configure_db(db)
            db.execute(
                "INSERT INTO conversations (name, response_id, created_at, updated_at, metadata) "
                "VALUES (?, ?, ?, ?, '{}') "
                "ON CONFLICT(name) DO UPDATE SET response_id = excluded.response_id, updated_at = excluded.updated_at",
                (name, response_id, now, now),
            )

    def delete_conversation(self, name: str) -> bool:
        with self._lock, self._connect() as db:
            self._configure_db(db)
            cur = db.execute("DELETE FROM conversations WHERE name = ?", (name,))
            return cur.rowcount > 0

    def stats(self) -> dict[str, Any]:
        with self._lock, self._connect() as db:
            self._configure_db(db)
            response_count = int(db.execute("SELECT COUNT(*) FROM responses").fetchone()[0] or 0)
            conversation_count = int(db.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] or 0)
            statuses = {
                str(row[0] or "unknown"): int(row[1] or 0)
                for row in db.execute("SELECT status, COUNT(*) FROM responses GROUP BY status").fetchall()
            }
        return {
            "path": str(self.path),
            "max_size": self.max_size,
            "responses": response_count,
            "conversations": conversation_count,
            "statuses": statuses,
        }


class IdempotencyCache:
    """Small in-process LRU cache for OpenAI-style Idempotency-Key replays."""

    def __init__(self, *, max_items: int = 1000, ttl_seconds: float = 300) -> None:
        self.max_items = max(1, int(max_items or 1000))
        self.ttl_seconds = max(1.0, float(ttl_seconds or 300))
        self._lock = threading.RLock()
        self._items: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._inflight: dict[tuple[str, str], dict[str, Any]] = {}

    def _purge_locked(self) -> None:
        now = time.time()
        expired = [key for key, item in self._items.items()
                   if now - float(item.get("ts") or 0) > self.ttl_seconds]
        for key in expired:
            self._items.pop(key, None)
        while len(self._items) > self.max_items:
            self._items.popitem(last=False)

    def get(self, key: str, fingerprint: str) -> dict[str, Any] | None:
        if not key:
            return None
        with self._lock:
            self._purge_locked()
            item = self._items.get(key)
            if not item or item.get("fingerprint") != fingerprint:
                return None
            self._items.move_to_end(key)
            response = item.get("response")
            return dict(response) if isinstance(response, dict) else None

    def put(self, key: str, fingerprint: str, response: dict[str, Any]) -> None:
        if not key:
            return
        with self._lock:
            self._items[key] = {
                "fingerprint": fingerprint,
                "response": dict(response),
                "ts": time.time(),
            }
            self._items.move_to_end(key)
            self._purge_locked()

    def begin(self, key: str, fingerprint: str) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None]:
        """Return (state, cached_response, flight).

        ``state`` is one of: disabled, cached, owner, waiter.
        """
        if not key:
            return "disabled", None, None
        cached = self.get(key, fingerprint)
        if cached is not None:
            return "cached", cached, None
        flight_key = (key, fingerprint)
        with self._lock:
            cached = self.get(key, fingerprint)
            if cached is not None:
                return "cached", cached, None
            flight = self._inflight.get(flight_key)
            if flight is None:
                flight = {"event": threading.Event(), "response": None, "error": None}
                self._inflight[flight_key] = flight
                return "owner", None, flight
            return "waiter", None, flight

    def wait(self, flight: dict[str, Any] | None) -> dict[str, Any]:
        if flight is None:
            return {}
        flight["event"].wait()
        error = flight.get("error")
        if error is not None:
            raise error
        response = flight.get("response")
        return dict(response) if isinstance(response, dict) else {}

    def finish(self, key: str, fingerprint: str, flight: dict[str, Any] | None, response: dict[str, Any]) -> None:
        if not key or flight is None:
            return
        self.put(key, fingerprint, response)
        flight_key = (key, fingerprint)
        with self._lock:
            flight["response"] = dict(response)
            flight["event"].set()
            self._inflight.pop(flight_key, None)

    def fail(self, key: str, fingerprint: str, flight: dict[str, Any] | None, error: BaseException) -> None:
        if not key or flight is None:
            return
        flight_key = (key, fingerprint)
        with self._lock:
            flight["error"] = error
            flight["event"].set()
            self._inflight.pop(flight_key, None)

    def get_or_compute(self, key: str, fingerprint: str, compute) -> dict[str, Any]:
        state, cached, flight = self.begin(key, fingerprint)
        if state == "disabled":
            return compute()
        if state == "cached" and cached is not None:
            return cached
        if state == "waiter":
            return self.wait(flight)
        try:
            response = compute()
        except Exception as exc:  # noqa: BLE001
            self.fail(key, fingerprint, flight, exc)
            raise
        self.finish(key, fingerprint, flight, response)
        return dict(response)


def make_handler(config: Config):
    api_key = config.get("server.api_key") or os.environ.get("AEGIS_SERVER_KEY")
    runner = SurfaceRunner(config, include_mcp=True)
    response_store = ResponseStore(config)
    idempotency_cache = IdempotencyCache(
        max_items=int(config.get("server.idempotency_cache_max", 1000) or 1000),
        ttl_seconds=float(config.get("server.idempotency_ttl_seconds", 300) or 300),
    )
    _recover_stale_api_runs()
    active_runs: dict[str, dict[str, Any]] = {}
    active_responses: dict[str, dict[str, Any]] = {}
    approvals: dict[str, dict[str, Any]] = {}
    state_lock = threading.RLock()
    max_concurrent_runs = max(1, int(config.get("server.max_concurrent_runs", 8) or 8))
    run_status_ttl = max(0.0, float(config.get("server.run_status_ttl_seconds", 3600) or 3600))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def _authed(self) -> bool:
            if not api_key:
                return True
            return self.headers.get("Authorization", "") == f"Bearer {api_key}"

        def _origin(self) -> str:
            return str(self.headers.get("Origin", "") or "")

        def _forbid_disallowed_origin(self) -> bool:
            if _origin_allowed(config, self._origin()):
                return False
            self._json(403, {"error": "cors origin not allowed"})
            return True

        def _json(self, code: int, obj: Any, extra_headers: dict[str, str] | None = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            for name, value in _response_headers(config, self._origin()).items():
                self.send_header(name, value)
            for name, value in (extra_headers or {}).items():
                if value:
                    self.send_header(name, value)
            self.end_headers()
            self.wfile.write(_json_bytes(obj))

        def _session_key(self) -> tuple[str | None, tuple[int, dict[str, Any]] | None]:
            raw = str(self.headers.get("X-Hermes-Session-Key") or "").strip()
            if not raw:
                return None, None
            if not api_key:
                return None, (
                    403,
                    {"error": "X-Hermes-Session-Key requires API key authentication"},
                )
            if any(ch in raw for ch in "\r\n\x00") or any(ord(ch) < 32 for ch in raw):
                return None, (400, {"error": "Invalid session key"})
            if len(raw) > _MAX_SESSION_KEY_CHARS:
                return None, (400, {"error": "Session key too long"})
            return raw, None

        def _session_headers(
            self,
            *,
            session_id: str | None = None,
            session_key: str | None = None,
        ) -> dict[str, str]:
            headers: dict[str, str] = {}
            if session_id:
                headers["X-Hermes-Session-Id"] = str(session_id)
            if session_key:
                headers["X-Hermes-Session-Key"] = str(session_key)
            return headers

        def _write_sse(self, obj: Any, *, event: str = "message") -> bool:
            try:
                if event:
                    self.wfile.write(f"event: {event}\n".encode())
                self.wfile.write(f"data: {json.dumps(obj, default=str)}\n\n".encode())
                self.wfile.flush()
                return True
            except (BrokenPipeError, ConnectionResetError):
                self._notify_sse_disconnect()
                return False

        def _notify_sse_disconnect(self) -> None:
            callback = getattr(self, "_aegis_on_disconnect", None)
            if not callable(callback):
                return
            self._aegis_on_disconnect = None
            try:
                callback()
            except Exception:  # noqa: BLE001
                pass

        def _route(self) -> tuple[str, dict[str, list[str]]]:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            return path, parse_qs(parsed.query)

        def _read_json(self) -> tuple[dict[str, Any] | None, str]:
            try:
                n = int(self.headers.get("content-length", 0))
            except ValueError:
                return None, "invalid content-length"
            if n < 0:
                return None, "invalid content-length"
            if n > _MAX_BODY_BYTES:
                return None, "request body too large"
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError:
                return None, "invalid json"
            if not isinstance(body, dict):
                return None, "json body must be an object"
            return body, ""

        def _health(self, *, detailed: bool = False) -> dict[str, Any]:
            payload: dict[str, Any] = {
                "ok": True,
                "status": "ok",
                "server": "aegis",
                "time": int(time.time()),
            }
            if detailed:
                stores: dict[str, Any] = {}
                runtime: dict[str, Any] = {}
                diagnostics: dict[str, Any] = {}
                try:
                    stores["responses"] = response_store.stats()
                except Exception as exc:  # noqa: BLE001
                    stores["responses"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                    payload["ok"] = False
                    payload["status"] = "degraded"
                try:
                    from .runs import RunStore

                    run_rows = RunStore().list(limit=500)
                    run_statuses: dict[str, int] = {}
                    for row in run_rows:
                        status = str(row.get("status") or "unknown")
                        run_statuses[status] = run_statuses.get(status, 0) + 1
                    stores["runs"] = {
                        "count": len(run_rows),
                        "statuses": run_statuses,
                    }
                except Exception as exc:  # noqa: BLE001
                    stores["runs"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                    payload["ok"] = False
                    payload["status"] = "degraded"
                try:
                    from .cron import CronStore

                    jobs = CronStore().list()
                    stores["jobs"] = _jobs_summary(jobs)
                except Exception as exc:  # noqa: BLE001
                    stores["jobs"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                    payload["ok"] = False
                    payload["status"] = "degraded"
                try:
                    from .session_checks import cross_session_integrity_report

                    stale_seconds = float(config.get("server.stale_run_health_seconds", 6 * 60 * 60)
                                          or 6 * 60 * 60)
                    stale_resume_seconds = float(
                        config.get("server.stale_resume_pending_health_seconds", 24 * 60 * 60)
                        or 24 * 60 * 60
                    )
                    report = cross_session_integrity_report(
                        session_limit=100,
                        run_limit=500,
                        stale_running_seconds=stale_seconds,
                        stale_resume_pending_seconds=stale_resume_seconds,
                    )
                    diagnostics["cross_session"] = report
                    if not bool(report.get("ok", True)):
                        payload["ok"] = False
                        payload["status"] = "degraded"
                except Exception as exc:  # noqa: BLE001
                    diagnostics["cross_session"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                    payload["ok"] = False
                    payload["status"] = "degraded"
                with state_lock:
                    self._sweep_runs_locked()
                    runtime = {
                        "active_runs": self._active_run_count_locked(),
                        "pending_approvals": len(approvals),
                        "active_responses": len(active_responses),
                    }
                payload.update({
                    "models": _models(config),
                    "capabilities": _capabilities(config),
                    "max_body_bytes": _MAX_BODY_BYTES,
                    "runtime": runtime,
                    "stores": stores,
                    "diagnostics": diagnostics,
                })
            return payload

        def _send_sse_headers(self, extra_headers: dict[str, str] | None = None) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            for name, value in _response_headers(config, self._origin()).items():
                self.send_header(name, value)
            for name, value in (extra_headers or {}).items():
                if value:
                    self.send_header(name, value)
            self.end_headers()

        def _sweep_runs_locked(self) -> None:
            if run_status_ttl <= 0:
                ttl = 0.0
            else:
                ttl = run_status_ttl
            now = time.time()
            expired: list[str] = []
            for run_id, rec in list(active_runs.items()):
                status = str(rec.get("status") or "")
                if status not in _TERMINAL_RUN_STATUSES:
                    continue
                updated = float(rec.get("updated_at_ts") or rec.get("created_at_ts") or now)
                if now - updated >= ttl:
                    expired.append(run_id)
            for run_id in expired:
                active_runs.pop(run_id, None)
                for approval_id, pending in list(approvals.items()):
                    if pending.get("run_id") == run_id:
                        approvals.pop(approval_id, None)

        def _active_run_count_locked(self) -> int:
            self._sweep_runs_locked()
            return sum(
                1 for rec in active_runs.values()
                if str(rec.get("status") or "") not in _TERMINAL_RUN_STATUSES
            )

        def _set_run_state_locked(self, run_id: str, **updates: Any) -> dict[str, Any] | None:
            rec = active_runs.get(run_id)
            if rec is None:
                return None
            rec.update(updates)
            now = time.time()
            rec["updated_at_ts"] = now
            rec["updated_at"] = int(now)
            return rec

        def _append_run_event_locked(
            self,
            run_id: str,
            event_type: str,
            payload: dict[str, Any] | None = None,
        ) -> dict[str, Any] | None:
            rec = active_runs.get(run_id)
            if rec is None:
                return None
            events = rec.setdefault("events", [])
            if not isinstance(events, list):
                events = []
                rec["events"] = events
            sequence = int(rec.get("event_sequence") or len(events))
            now = time.time()
            event_id = new_id("evt")
            event = {
                "id": event_id,
                "object": "hermes.run.event",
                "type": event_type,
                "event": event_type,
                "run_id": run_id,
                "sequence_number": sequence,
                "created_at": int(now),
            }
            if payload:
                event.update(payload)
                event.update({
                    "id": event_id,
                    "object": "hermes.run.event",
                    "type": event_type,
                    "event": event_type,
                    "run_id": run_id,
                    "sequence_number": sequence,
                    "created_at": int(event.get("created_at") or now),
                })
            events.append(event)
            if len(events) > _MAX_STORED_RUN_EVENTS:
                del events[:-_MAX_STORED_RUN_EVENTS]
            rec["event_sequence"] = sequence + 1
            rec["last_event"] = event_type
            rec["updated_at_ts"] = now
            rec["updated_at"] = int(now)
            return event

        def _release_run_approvals_locked(self, run_id: str, reason: str) -> None:
            for pending in list(approvals.values()):
                if pending.get("run_id") != run_id or pending.get("answered"):
                    continue
                pending["approved"] = False
                pending["answered"] = True
                pending["cancelled"] = True
                pending["cancel_reason"] = reason
                self._append_run_event_locked(run_id, "approval.responded", {
                    "approval_id": pending.get("id"),
                    "approved": False,
                    "choice": "deny",
                    "cancelled": True,
                    "reason": reason,
                    "prompt": pending.get("prompt", ""),
                })
                event = pending.get("event")
                if event is not None:
                    event.set()

        def _request_stop_run_locked(self, run_id: str, reason: str = "stop requested") -> dict[str, Any] | None:
            rec = active_runs.get(run_id)
            if rec is None:
                return None
            if str(rec.get("status") or "") in _TERMINAL_RUN_STATUSES:
                return None
            rec["cancel_requested"] = True
            rec["cancel_reason"] = reason
            self._release_run_approvals_locked(run_id, reason)
            self._append_run_event_locked(run_id, "run.stopping", {"reason": reason})
            agent = rec.get("agent")
            if agent is not None:
                cancel = getattr(agent, "cancel", None)
                if callable(cancel):
                    cancel()
                elif getattr(agent, "cancel_event", None) is not None:
                    agent.cancel_event.set()
            return self._set_run_state_locked(run_id, status="cancelling", last_event="run.stopping")

        def _cancel_agent(self, agent: Any) -> None:
            if agent is None:
                return
            cancel = getattr(agent, "cancel", None)
            if callable(cancel):
                cancel()
                return
            cancel_event = getattr(agent, "cancel_event", None)
            if cancel_event is not None:
                cancel_event.set()

        def _start_cancel_watchdog(
            self,
            agent_getter,
            should_continue,
            *,
            worker_getter=None,
            name: str = "aegis-api-stream-cancel",
        ) -> None:
            def reapply_cancel() -> None:
                deadline = time.monotonic() + 1.5
                while should_continue() and time.monotonic() < deadline:
                    self._cancel_agent(agent_getter())
                    worker = worker_getter() if callable(worker_getter) else None
                    if worker is not None and not worker.is_alive():
                        break
                    time.sleep(0.025)

            threading.Thread(target=reapply_cancel, daemon=True, name=name).start()

        def _prepare_stream_agent(
            self,
            runner: Any,
            *,
            session: Any = None,
            session_id: str | None = None,
            title: str | None = None,
            history: Iterable[Message] | None = None,
            model: str | None = None,
            provider_name: str | None = None,
            cwd: str | None = None,
            surface: str = "serve",
            meta: dict[str, Any] | None = None,
        ) -> tuple[Any, Any | None]:
            """Best-effort explicit session/agent binding for cancellable streams."""

            load_session = getattr(runner, "load_or_create_session", None)
            if session is None and callable(load_session):
                session = load_session(
                    session_id,
                    title=title,
                    history=history,
                    surface=surface,
                    meta=meta,
                )
            elif session is not None and meta:
                current_meta = getattr(session, "meta", None)
                if isinstance(current_meta, dict):
                    changed = False
                    for key, value in meta.items():
                        if (
                            key in {"runtime_controls", "runtime"}
                            and isinstance(current_meta.get(key), dict)
                            and isinstance(value, dict)
                        ):
                            merged = dict(current_meta.get(key) or {})
                            merged.update(value)
                            value = merged
                        if current_meta.get(key) != value:
                            current_meta[key] = value
                            changed = True
                    if changed:
                        save = getattr(getattr(runner, "store", None), "save", None)
                        if callable(save):
                            try:
                                save(session)
                            except Exception:  # noqa: BLE001
                                pass
            make_agent = getattr(runner, "make_agent", None)
            if session is not None and callable(make_agent):
                return session, make_agent(
                    session=session,
                    cwd=cwd,
                    model=model,
                    provider_name=provider_name,
                )
            return session, None

        def _mark_response_cancelled(self, response: dict[str, Any], reason: str) -> dict[str, Any]:
            cancelled = dict(response)
            cancelled["status"] = "cancelled"
            cancelled["error"] = None
            cancelled["incomplete_details"] = cancelled.get("incomplete_details") or {"reason": reason}
            metadata = cancelled.get("metadata") if isinstance(cancelled.get("metadata"), dict) else {}
            metadata = dict(metadata)
            metadata["cancel_reason"] = reason
            metadata["cancelled_at"] = int(time.time())
            cancelled["metadata"] = metadata
            return cancelled

        def _register_response_locked(
            self,
            response_id: str,
            *,
            response: dict[str, Any] | None = None,
            state: dict[str, Any] | None = None,
            agent: Any = None,
            session: Any = None,
            store_response: bool = True,
        ) -> dict[str, Any]:
            rec = active_responses.setdefault(response_id, {
                "id": response_id,
                "cancel_requested": False,
                "cancel_reason": "",
                "status": "running",
                "created_at_ts": time.time(),
            })
            if response is not None:
                rec["response"] = dict(response)
            if state is not None:
                rec["state"] = dict(state)
            if agent is not None:
                rec["agent"] = agent
            if session is not None:
                rec["session"] = session
            rec["store_response"] = bool(store_response)
            rec["updated_at_ts"] = time.time()
            if rec.get("cancel_requested") and agent is not None:
                self._cancel_agent(agent)
            return rec

        def _request_cancel_response_locked(
            self,
            response_id: str,
            reason: str = "API cancel requested",
        ) -> dict[str, Any] | None:
            rec = active_responses.get(response_id)
            if rec is None:
                return None
            rec["cancel_requested"] = True
            rec["cancel_reason"] = reason
            rec["status"] = "cancelling"
            rec["updated_at_ts"] = time.time()
            self._cancel_agent(rec.get("agent"))
            return rec

        def _response_cancel_requested(self, response_id: str) -> tuple[bool, str]:
            with state_lock:
                rec = active_responses.get(response_id)
                if rec is None:
                    return False, ""
                return bool(rec.get("cancel_requested")), str(rec.get("cancel_reason") or "API cancel requested")

        def _finish_response(self, response_id: str, response: dict[str, Any] | None = None) -> None:
            with state_lock:
                rec = active_responses.get(response_id)
                if rec is not None and response is not None:
                    rec["response"] = dict(response)
                active_responses.pop(response_id, None)

        def _prepare_response_agent(
            self,
            response_id: str,
            *,
            session_id: str | None,
            title: str,
            history: list[Message],
            model: Any,
            provider_name: Any,
            cwd: Any,
            session_key: str | None,
            service_tier: str = "",
            runtime_controls: dict[str, Any] | None = None,
        ) -> tuple[Any, Any]:
            load_session = getattr(runner, "load_or_create_session", None)
            make_agent = getattr(runner, "make_agent", None)
            if not callable(load_session) or not callable(make_agent):
                return None, None
            meta = {
                "request_id": response_id,
                "api": "responses",
                **({"gateway_session_key": session_key} if session_key else {}),
            }
            controls = dict(runtime_controls or {})
            if service_tier and "service_tier" not in controls:
                controls["service_tier"] = service_tier
            if controls:
                meta.update(runtime_controls_meta(controls))
            session = load_session(
                session_id,
                title=title,
                history=history,
                surface="serve",
                meta=meta,
            )
            agent = make_agent(
                session=session,
                model=model,
                provider_name=provider_name,
                cwd=cwd,
            )
            return session, agent

        def _cancel_response(self, response_id: str) -> None:
            state = response_store.get_state(response_id)
            response = (state or {}).get("response") if state else None
            active_state = None
            store_response = True
            with state_lock:
                rec = self._request_cancel_response_locked(response_id, "API cancel requested")
                if not isinstance(response, dict) and rec is not None and isinstance(rec.get("response"), dict):
                    response = dict(rec["response"])
                if rec is not None:
                    active_state = dict(rec.get("state") or {})
                    store_response = bool(rec.get("store_response", True))
            if not isinstance(response, dict):
                return self._json(404, {"error": "response not found", "id": response_id})
            cancelled = self._mark_response_cancelled(response, "API cancel requested")
            persisted_state = state if state is not None else active_state
            if persisted_state is not None and store_response and _coerce_request_bool(cancelled.get("store"), True):
                response_store.put(cancelled, persisted_state)
            with state_lock:
                rec = active_responses.get(response_id)
                if rec is not None:
                    rec["response"] = dict(cancelled)
                    rec["status"] = "cancelled"
            return self._json(200, cancelled)

        def _job_detail(self, job_id: str, *, method: str = "GET") -> tuple[int, dict[str, Any]]:
            from .cron import CronStore

            invalid = _api_job_id_error(job_id, method=method, path=getattr(self, "path", ""), headers=self.headers)
            if invalid is not None:
                return invalid
            job = CronStore().get(job_id)
            if job is None:
                return 404, {"ok": False, "error": "job not found", "id": job_id}
            return 200, {"ok": True, "job": _job_payload(job)}

        def _create_job(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
            from .cron import CronStore, _scan_cron_prompt

            if not body.get("schedule") or not body.get("prompt"):
                return 400, {"ok": False, "error": "schedule and prompt are required"}
            prompt_error = _scan_cron_prompt(str(body.get("prompt") or ""))
            if prompt_error:
                return 400, {"ok": False, "error": prompt_error}
            store = CronStore()
            try:
                job = store.add(
                    str(body["schedule"]),
                    str(body["prompt"]),
                    name=str(body.get("name") or ""),
                    channel=str(body.get("channel") or ""),
                    script=str(body.get("script") or ""),
                    skills=_coerce_csv_list(body.get("skills")),
                    context_from=_coerce_csv_list(body.get("context_from")),
                    deliver=str(body.get("deliver") or ""),
                    no_agent=_coerce_request_bool(body.get("no_agent"), False),
                    model=str(body.get("model") or ""),
                    enabled_toolsets=_coerce_csv_list(body.get("enabled_toolsets") or body.get("toolsets")),
                    workdir=str(body.get("workdir") or ""),
                    max_runs=int(body.get("max_runs") or 0),
                )
            except (TypeError, ValueError) as exc:
                return 400, {"ok": False, "error": str(exc)}
            return 201, {"ok": True, "id": job.id, "job": _job_payload(job)}

        def _update_job(self, job_id: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
            from .cron import CronStore, _scan_cron_prompt

            invalid = _api_job_id_error(job_id, method="PATCH", path=getattr(self, "path", ""), headers=self.headers)
            if invalid is not None:
                return invalid
            updates = {key: body[key] for key in (
                "schedule", "prompt", "name", "channel", "enabled", "script", "skills", "context_from",
                "deliver", "no_agent", "max_runs", "model", "enabled_toolsets", "workdir",
            ) if key in body}
            if "toolsets" in body and "enabled_toolsets" not in updates:
                updates["enabled_toolsets"] = body["toolsets"]
            if "prompt" in updates:
                prompt_error = _scan_cron_prompt(str(updates.get("prompt") or ""))
                if prompt_error:
                    return 400, {"ok": False, "error": prompt_error}
            if "skills" in updates:
                updates["skills"] = _coerce_csv_list(updates["skills"])
            if "context_from" in updates:
                updates["context_from"] = _coerce_csv_list(updates["context_from"])
            if "enabled_toolsets" in updates:
                updates["enabled_toolsets"] = _coerce_csv_list(updates["enabled_toolsets"])
            if "enabled" in updates:
                updates["enabled"] = _coerce_request_bool(updates["enabled"], True)
            if "no_agent" in updates:
                updates["no_agent"] = _coerce_request_bool(updates["no_agent"], False)
            try:
                if "max_runs" in updates:
                    updates["max_runs"] = int(updates["max_runs"] or 0)
                job = CronStore().update(job_id, **updates)
            except (TypeError, ValueError) as exc:
                return 400, {"ok": False, "error": str(exc), "id": job_id}
            if job is None:
                return 404, {"ok": False, "error": "job not found", "id": job_id}
            return 200, {"ok": True, "id": job.id, "job": _job_payload(job)}

        def _run_job_now(self, job_id: str) -> tuple[int, dict[str, Any]]:
            from .cron import CronStore, build_delivery_sink, run_job

            invalid = _api_job_id_error(job_id, method="POST", path=getattr(self, "path", ""), headers=self.headers)
            if invalid is not None:
                return invalid
            store = CronStore()
            if store.get(job_id) is None:
                return 404, {"ok": False, "error": "job not found", "id": job_id}
            sink = build_delivery_sink(config, verbose=False)
            return 200, run_job(config, job_id, sink=sink, store=store, verbose=False)

        def _session_detail(self, session_id: str) -> tuple[int, dict[str, Any]]:
            from .session import SessionStore

            session = SessionStore().load(session_id)
            if session is None:
                return 404, {"ok": False, "error": "session not found", "id": session_id}
            return 200, {"ok": True, "session": _session_payload(session)}

        def _run_events(self, run_id: str) -> tuple[int, dict[str, Any]]:
            with state_lock:
                self._sweep_runs_locked()
                active = active_runs.get(run_id)
                if active is not None:
                    return 200, {"ok": True, "id": run_id, "events": list(active.get("events") or [])}
            from .runs import RunStore
            from .tracing import TraceStore

            run = RunStore().get(run_id)
            if run is None:
                return 404, {"ok": False, "error": "run not found", "id": run_id}
            data = run.get("data") if isinstance(run.get("data"), dict) else {}
            trace_id = str(run.get("trace_id") or "")
            trace = TraceStore.from_config(config).get_trace(trace_id) if trace_id else None
            stored_events = data.get("events") if isinstance(data.get("events"), list) else []
            return 200, {
                "ok": True,
                "id": run_id,
                "run": _public_stored_run_record(run),
                "events": stored_events or (trace or {}).get("spans", []),
                "trace": trace,
            }

        def _stream_run_events(self, run_id: str) -> None:
            code, payload = self._run_events(run_id)
            if code != 200:
                return self._json(code, payload)
            self._send_sse_headers()

            deadline = time.time() + float(config.get("server.run_events_timeout_seconds", 3600) or 3600)
            sent = 0
            while True:
                with state_lock:
                    self._sweep_runs_locked()
                    active = active_runs.get(run_id)
                    if active is None:
                        events = list(payload.get("events") or [])
                        detail = payload.get("run") if isinstance(payload.get("run"), dict) else {}
                        status = str(detail.get("status") or "completed")
                        detail = detail or {"id": run_id, "status": status}
                    else:
                        events = list(active.get("events") or [])
                        status = str(active.get("status") or "running")
                        detail = _public_run_record(active)
                for event in events[sent:]:
                    if not self._write_sse(event, event="event"):
                        return
                sent = len(events)
                if status in _TERMINAL_RUN_STATUSES or active is None:
                    self._write_sse(detail, event="done")
                    self.wfile.write(b"data: [DONE]\n\n")
                    return
                if time.time() >= deadline:
                    self._write_sse({"id": run_id, "status": "timeout"}, event="timeout")
                    self.wfile.write(b"data: [DONE]\n\n")
                    return
                time.sleep(0.2)

        def do_OPTIONS(self):  # noqa: N802
            cors = _cors_headers(config, self._origin())
            if cors is None:
                self.send_response(403)
                for name, value in _security_headers().items():
                    self.send_header(name, value)
                self.end_headers()
                return
            self.send_response(204)
            headers = _security_headers()
            if cors:
                headers.update(cors)
            for name, value in headers.items():
                self.send_header(name, value)
            self.end_headers()

        def _run_detail(self, run_id: str) -> tuple[int, dict[str, Any]]:
            with state_lock:
                self._sweep_runs_locked()
                active = active_runs.get(run_id)
                if active is not None:
                    public = _public_run_record(active)
                    return 200, {"ok": True, **public, "run": public}
            from .runs import RunStore

            run = RunStore().get(run_id)
            if run is None:
                return 404, {"ok": False, "error": "run not found", "id": run_id, "run_id": run_id}
            public = _public_stored_run_record(run)
            return 200, {"ok": True, **public, "run": public}

        def _run_exists(self, run_id: str) -> bool:
            if not run_id:
                return False
            with state_lock:
                self._sweep_runs_locked()
                if run_id in active_runs:
                    return True
            from .runs import RunStore

            return RunStore().get(run_id) is not None

        def _run_not_found_payload(self, run_id: str) -> dict[str, Any]:
            return {
                "ok": False,
                "error": {
                    "message": f"Run not found: {run_id}",
                    "code": "run_not_found",
                },
                "run_id": run_id,
                "id": run_id,
            }

        def _session_check_param(
            self,
            query: dict[str, list[str]],
            body: dict[str, Any] | None,
            names: tuple[str, ...],
            default: Any,
        ) -> Any:
            body = body or {}
            for name in names:
                if name in body:
                    return body.get(name)
                values = query.get(name)
                if values:
                    return values[0]
            return default

        def _session_check_limits(
            self,
            query: dict[str, list[str]],
            body: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            def as_int(names: tuple[str, ...], default: int) -> int:
                raw = self._session_check_param(query, body, names, default)
                try:
                    return int(raw)
                except (TypeError, ValueError):
                    return default

            def as_float(names: tuple[str, ...], default: float) -> float:
                raw = self._session_check_param(query, body, names, default)
                try:
                    return float(raw)
                except (TypeError, ValueError):
                    return default

            return {
                "session_limit": as_int(("session_limit", "sessions"), 500),
                "run_limit": as_int(("run_limit", "runs"), 500),
                "stale_running_seconds": as_float(("stale_running_seconds", "stale_seconds"), 21600.0),
                "stale_resume_pending_seconds": as_float(
                    ("stale_resume_pending_seconds", "stale_resume_seconds"),
                    86400.0,
                ),
            }

        def _session_checks_report(
            self,
            query: dict[str, list[str]],
            body: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            from .session_checks import cross_session_integrity_report

            return cross_session_integrity_report(**self._session_check_limits(query, body))

        def _repair_session_checks(
            self,
            query: dict[str, list[str]],
            body: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            from .session_checks import cross_session_integrity_report, repair_cross_session_integrity

            limits = self._session_check_limits(query, body)
            reason = str((body or {}).get("resume_reason") or (body or {}).get("reason") or "api_session_check_repair")
            repair = repair_cross_session_integrity(
                session_limit=limits["session_limit"],
                run_limit=limits["run_limit"],
                stale_running_seconds=limits["stale_running_seconds"],
                stale_resume_pending_seconds=limits["stale_resume_pending_seconds"],
                resume_reason=reason,
            )
            report = cross_session_integrity_report(**limits)
            return {
                "object": "hermes.cross_session_integrity_repair_result",
                "ok": bool(repair.get("ok", False)) and str(report.get("status") or "") != "error",
                "repair": repair,
                "report": report,
            }

        def do_GET(self):  # noqa: N802
            if self._forbid_disallowed_origin():
                return
            path, query = self._route()
            if path in {"/health", "/v1/health"}:
                return self._json(200, self._health())
            if not self._authed():
                return self._json(401, {"error": "unauthorized"})
            if path in {"/health/detailed", "/v1/health/detailed"}:
                return self._json(200, self._health(detailed=True))
            if path == "/v1/models":
                return self._json(200, {"object": "list", "data": _models(config)})
            if path.startswith("/v1/models/"):
                model_id = unquote(path[len("/v1/models/"):])
                row = _model(config, model_id)
                if row is None:
                    return self._json(404, {
                        "error": {
                            "message": f"Model not found: {model_id}",
                            "type": "invalid_request_error",
                            "code": "model_not_found",
                        },
                    })
                return self._json(200, row)
            if path == "/v1/capabilities":
                return self._json(200, _capabilities(config))
            if path == "/v1/skills":
                return self._json(200, _skills_payload(config))
            if path == "/v1/toolsets":
                return self._json(200, _toolsets_payload(config))
            if path in {"/api/session-checks", "/api/cross-session/checks", "/api/harness/cross-session"}:
                return self._json(200, self._session_checks_report(query))
            if path == "/v1/conversations":
                try:
                    limit = max(1, min(int((query.get("limit") or ["100"])[0] or 100), 1000))
                except (TypeError, ValueError):
                    limit = 100
                try:
                    offset = max(0, int((query.get("offset") or ["0"])[0] or 0))
                except (TypeError, ValueError):
                    offset = 0
                rows = response_store.list_conversations(limit=limit + 1, offset=offset)
                page = rows[:limit]
                return self._json(200, {
                    "object": "list",
                    "data": page,
                    "limit": limit,
                    "offset": offset,
                    "has_more": len(rows) > limit,
                })
            if path.startswith("/v1/conversations/") and path.endswith("/items"):
                conversation_id = unquote(path.split("/")[-2])
                conversation = response_store.get_conversation_record(conversation_id)
                if conversation is None:
                    return self._json(404, {"error": "conversation not found", "id": conversation_id})
                response_id = conversation.get("latest_response_id")
                limit, order, param_error = _response_input_items_params(query)
                if param_error is not None:
                    return self._json(400, param_error)
                if not response_id:
                    return self._json(200, {
                        "object": "list",
                        "data": [],
                        "conversation_id": conversation_id,
                        "response_id": None,
                        "limit": limit,
                        "order": order,
                        "total_count": 0,
                        "has_more": False,
                        "first_id": None,
                        "last_id": None,
                    })
                state = response_store.get_state(str(response_id))
                if state is None:
                    return self._json(404, {
                        "error": "conversation latest response not found",
                        "id": conversation_id,
                        "response_id": response_id,
                    })
                payload = _response_input_items_payload(state, str(response_id), query)
                payload["conversation_id"] = conversation_id
                return self._json(200, payload)
            if path.startswith("/v1/conversations/"):
                conversation_id = unquote(path.rsplit("/", 1)[-1])
                conversation = response_store.get_conversation_record(conversation_id)
                if conversation is None:
                    return self._json(404, {"error": "conversation not found", "id": conversation_id})
                return self._json(200, conversation)
            if path.startswith("/v1/responses/") and path.endswith("/input_items"):
                rid = path.split("/")[-2]
                state = response_store.get_state(rid)
                if state is None:
                    return self._json(404, {"error": "response not found", "id": rid})
                _limit, _order, param_error = _response_input_items_params(query)
                if param_error is not None:
                    return self._json(400, param_error)
                return self._json(200, _response_input_items_payload(state, rid, query))
            if path.startswith("/v1/responses/"):
                rid = path.rsplit("/", 1)[-1]
                response = response_store.get(rid)
                if response is None:
                    return self._json(404, {"error": "response not found", "id": rid})
                return self._json(200, response)
            if path == "/v1/runs":
                from .runs import RunStore

                limit = int((query.get("limit") or ["50"])[0] or 50)
                limit = max(1, min(limit, 500))
                with state_lock:
                    self._sweep_runs_locked()
                    active_records = [_public_run_record(rec) for rec in active_runs.values()]
                    active_ids = {str(rec.get("id") or "") for rec in active_records}
                stored = [
                    _public_stored_run_record(row)
                    for row in RunStore().list(limit=max(1, limit))
                    if str(row.get("id") or "") not in active_ids
                ]
                rows = active_records + stored
                return self._json(200, {"object": "list", "data": rows[:limit]})
            if path.startswith("/v1/runs/") and path.endswith("/events"):
                run_id = path.split("/")[-2]
                stream = (
                    "text/event-stream" in self.headers.get("Accept", "")
                    or _coerce_request_bool((query.get("stream") or [None])[0], False)
                )
                if stream:
                    return self._stream_run_events(run_id)
                code, payload = self._run_events(run_id)
                return self._json(code, payload)
            if path.startswith("/v1/runs/") and path.endswith("/approval"):
                run_id = path.split("/")[-2]
                with state_lock:
                    pending = [dict(v, event=None) for v in approvals.values()
                               if v.get("run_id") == run_id and not v.get("answered")]
                if not pending and not self._run_exists(run_id):
                    return self._json(404, self._run_not_found_payload(run_id))
                return self._json(200, {"ok": True, "run_id": run_id, "pending": pending})
            if path.startswith("/v1/runs/"):
                code, payload = self._run_detail(path.rsplit("/", 1)[-1])
                return self._json(code, payload)
            if path == "/api/sessions":
                from .session import SessionStore

                limit = int((query.get("limit") or ["100"])[0] or 100)
                limit = max(1, min(limit, 1000))
                offset = max(0, int((query.get("offset") or ["0"])[0] or 0))
                include_internal = _coerce_request_bool((query.get("include_internal") or [None])[0], False)
                rows = SessionStore().list(min(offset + limit + 1, 2000), include_internal=include_internal)
                page = rows[offset:offset + limit]
                return self._json(200, {
                    "ok": True,
                    "object": "list",
                    "data": page,
                    "sessions": page,
                    "limit": limit,
                    "offset": offset,
                    "has_more": len(rows) > offset + limit,
                    "include_internal": include_internal,
                })
            if path.startswith("/api/sessions/") and path.endswith("/messages"):
                session_id = path.split("/")[-2]
                code, payload = self._session_detail(session_id)
                if code != 200:
                    return self._json(code, payload)
                session = payload["session"]
                messages = list(session["messages"])
                limit = max(1, min(int((query.get("limit") or [str(len(messages) or 1)])[0] or 1), 1000))
                offset = max(0, int((query.get("offset") or ["0"])[0] or 0))
                page = messages[offset:offset + limit]
                return self._json(200, {
                    "ok": True,
                    "object": "list",
                    "id": session_id,
                    "session_id": session_id,
                    "data": page,
                    "messages": page,
                    "limit": limit,
                    "offset": offset,
                    "has_more": len(messages) > offset + limit,
                })
            if path.startswith("/api/sessions/"):
                code, payload = self._session_detail(path.rsplit("/", 1)[-1])
                return self._json(code, payload)
            if path == "/api/jobs":
                from .cron import CronStore

                raw_jobs = CronStore().list()
                jobs = [_job_payload(job) for job in raw_jobs]
                try:
                    offset = max(0, int((query.get("offset") or ["0"])[0] or 0))
                except (TypeError, ValueError):
                    offset = 0
                requested_limit = (query.get("limit") or [None])[0]
                if requested_limit is None:
                    limit = max(0, len(jobs) - offset)
                else:
                    try:
                        limit = max(1, min(int(requested_limit or 1), 1000))
                    except (TypeError, ValueError):
                        limit = 100
                page = jobs[offset:offset + limit] if limit else []
                return self._json(200, {
                    "ok": True,
                    "object": "list",
                    "jobs": page,
                    "data": page,
                    "count": len(jobs),
                    "limit": limit,
                    "offset": offset,
                    "has_more": len(jobs) > offset + limit,
                    "summary": _jobs_summary(raw_jobs),
                })
            if path.startswith("/api/jobs/"):
                code, payload = self._job_detail(path.rsplit("/", 1)[-1])
                return self._json(code, payload)
            return self._json(404, {"error": "not found"})

        def do_DELETE(self):  # noqa: N802
            if self._forbid_disallowed_origin():
                return
            if not self._authed():
                return self._json(401, {"error": "unauthorized"})
            path, _query = self._route()
            if path.startswith("/v1/conversations/"):
                conversation_id = unquote(path.rsplit("/", 1)[-1])
                existed = response_store.delete_conversation(conversation_id)
                return self._json(
                    200 if existed else 404,
                    {
                        "ok": existed,
                        "id": conversation_id,
                        "object": "conversation",
                        "deleted": bool(existed),
                    },
                )
            if path.startswith("/v1/responses/"):
                rid = path.rsplit("/", 1)[-1]
                existed = response_store.delete(rid)
                return self._json(
                    200 if existed else 404,
                    {
                        "ok": existed,
                        "id": rid,
                        "object": "response",
                        "deleted": bool(existed),
                    },
                )
            if path.startswith("/api/jobs/"):
                from .cron import CronStore

                job_id = path.rsplit("/", 1)[-1]
                invalid = _api_job_id_error(job_id, method="DELETE", path=getattr(self, "path", ""), headers=self.headers)
                if invalid is not None:
                    code, payload = invalid
                    return self._json(code, payload)
                ok = CronStore().remove(job_id)
                return self._json(200 if ok else 404, {"ok": ok, "id": job_id})
            if path.startswith("/api/sessions/"):
                from .session import SessionStore

                sid = path.rsplit("/", 1)[-1]
                ok = SessionStore().delete(sid)
                return self._json(200 if ok else 404, {"ok": ok, "id": sid})
            return self._json(404, {"error": "not found"})

        def do_PATCH(self):  # noqa: N802
            if self._forbid_disallowed_origin():
                return
            if not self._authed():
                return self._json(401, {"error": "unauthorized"})
            path, _query = self._route()
            body, error = self._read_json()
            if error:
                return self._json(413 if error == "request body too large" else 400, {"error": error})
            if path.startswith("/api/sessions/"):
                from .session import SessionStore

                store = SessionStore()
                sid = path.rsplit("/", 1)[-1]
                session = store.load(sid)
                if session is None:
                    return self._json(404, {"ok": False, "error": "session not found", "id": sid})
                if "title" in body:
                    session.title = str(body.get("title") or session.title)
                if isinstance(body.get("meta"), dict):
                    session.meta.update(body["meta"])
                if isinstance(body.get("todos"), list):
                    session.todos = body["todos"]
                store.save(session)
                return self._json(200, {"ok": True, "session": _session_payload(session)})
            if path.startswith("/api/jobs/"):
                job_id = path.rsplit("/", 1)[-1]
                code, payload = self._update_job(job_id, body)
                return self._json(code, payload)
            return self._json(404, {"error": "not found"})

        def do_PUT(self):  # noqa: N802
            return self.do_PATCH()

        def do_POST(self):  # noqa: N802
            if self._forbid_disallowed_origin():
                return
            if not self._authed():
                return self._json(401, {"error": "unauthorized"})
            path, _query = self._route()
            body, error = self._read_json()
            if error:
                return self._json(413 if error == "request body too large" else 400, {"error": error})
            if path == "/v1/chat/completions":
                return self._post_chat_completion(body)
            if path == "/v1/responses/input_tokens":
                return self._post_response_input_tokens(body)
            if path == "/v1/responses/compact":
                return self._post_response_compact(body)
            if path == "/v1/responses":
                return self._post_response(body)
            if path == "/v1/conversations":
                conversation_id = _conversation_id(body.get("id") or body.get("conversation"))
                if not conversation_id:
                    conversation_id = new_id("conv")
                metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
                conversation = response_store.create_conversation(conversation_id, metadata)
                return self._json(201, conversation)
            if path.startswith("/v1/responses/") and path.endswith("/cancel"):
                return self._cancel_response(path.split("/")[-2])
            if path == "/v1/runs":
                return self._post_run(body)
            if path.startswith("/v1/runs/") and path.endswith(("/stop", "/cancel")):
                run_id = path.split("/")[-2]
                return self._stop_run(run_id)
            if path.startswith("/v1/runs/") and path.endswith("/approval"):
                run_id = path.split("/")[-2]
                return self._post_approval(run_id, body)
            if path in {
                "/api/session-checks/repair",
                "/api/cross-session/checks/repair",
                "/api/harness/cross-session/repair",
            }:
                return self._json(200, self._repair_session_checks(_query, body))
            if path in {"/api/session-checks", "/api/cross-session/checks", "/api/harness/cross-session"}:
                if str(body.get("action") or "").lower() == "repair" or _coerce_request_bool(body.get("repair"), False):
                    return self._json(200, self._repair_session_checks(_query, body))
                return self._json(200, self._session_checks_report(_query, body))
            if path == "/api/sessions":
                from .session import Session, SessionStore

                store = SessionStore()
                requested_id, id_error = _api_session_id_from_body(body)
                if id_error is not None:
                    code, payload = id_error
                    return self._json(code, payload)
                if requested_id and store.load(requested_id) is not None:
                    return self._json(
                        409,
                        {
                            "ok": False,
                            "error": f"Session already exists: {requested_id}",
                            "code": "session_exists",
                        },
                    )
                title = str(body.get("title") or "api session")
                if requested_id:
                    session = Session(id=requested_id, title=title or requested_id, profile=cfg_paths.current_profile())
                else:
                    session = Session.create(title)
                if isinstance(body.get("meta"), dict):
                    session.meta.update(body["meta"])
                if isinstance(body.get("metadata"), dict):
                    session.meta.update(body["metadata"])
                session.meta["source"] = "api_server"
                runtime_controls: dict[str, Any] = {}
                if body.get("model"):
                    runtime_controls["model"] = body.get("model")
                if body.get("provider"):
                    runtime_controls["provider"] = body.get("provider")
                service_tier = _request_service_tier(body, body.get("metadata") if isinstance(body.get("metadata"), dict) else None)
                if service_tier:
                    runtime_controls["service_tier"] = service_tier
                reasoning_effort, reasoning_error = _request_reasoning_effort(
                    body,
                    body.get("metadata") if isinstance(body.get("metadata"), dict) else None,
                )
                if reasoning_error is not None:
                    return self._json(400, reasoning_error)
                if reasoning_effort:
                    runtime_controls["reasoning_effort"] = reasoning_effort
                if runtime_controls:
                    session.meta.update(runtime_controls_meta(runtime_controls))
                system_prompt = body.get("system_prompt")
                if system_prompt is not None:
                    if not isinstance(system_prompt, str):
                        return self._json(
                            400,
                            {
                                "ok": False,
                                "error": "system_prompt must be a string",
                                "code": "invalid_system_prompt",
                            },
                        )
                    session.meta["system_prompt"] = system_prompt
                    if system_prompt:
                        session.messages.append(Message.system(system_prompt))
                store.save(session)
                return self._json(201, {"ok": True, "object": "hermes.session", "session": _session_payload(session)})
            if path.startswith("/api/sessions/") and path.endswith("/messages"):
                from .session import SessionStore

                store = SessionStore()
                sid = path.split("/")[-2]
                session = store.load(sid)
                if session is None:
                    return self._json(404, {"ok": False, "error": "session not found", "id": sid})
                msg = _message_from_payload(body)
                session.messages.append(msg)
                store.save(session)
                return self._json(200, {"ok": True, "message": _message_payload(msg, len(session.messages) - 1)})
            if path.startswith("/api/sessions/") and path.endswith("/fork"):
                from .session import SessionStore

                store = SessionStore()
                sid = path.split("/")[-2]
                parent = store.load(sid)
                if parent is None:
                    return self._json(404, {"ok": False, "error": "session not found", "id": sid})
                requested_id, id_error = _api_session_id_from_body(body)
                if id_error is not None:
                    code, payload = id_error
                    return self._json(code, payload)
                if requested_id and store.load(requested_id) is not None:
                    return self._json(
                        409,
                        {
                            "ok": False,
                            "error": f"Session already exists: {requested_id}",
                            "code": "session_exists",
                        },
                    )
                child = store.fork(parent, carry_summary=_coerce_request_bool(body.get("carry_summary"), True))
                if requested_id:
                    old_id = child.id
                    child.id = requested_id
                    child.parent_id = parent.id
                    store.delete(old_id)
                child.messages = [Message.from_dict(message.to_dict()) for message in parent.messages]
                if body.get("model"):
                    runtime_controls = {"model": body.get("model")}
                else:
                    runtime_controls = {}
                if body.get("provider"):
                    runtime_controls["provider"] = body.get("provider")
                service_tier = _request_service_tier(body)
                if service_tier:
                    runtime_controls["service_tier"] = service_tier
                reasoning_effort, reasoning_error = _request_reasoning_effort(body)
                if reasoning_error is not None:
                    return self._json(400, reasoning_error)
                if reasoning_effort:
                    runtime_controls["reasoning_effort"] = reasoning_effort
                if runtime_controls:
                    child.meta.update(runtime_controls_meta(runtime_controls))
                if body.get("title"):
                    child.title = str(body["title"])
                store.save(child)
                return self._json(201, {"ok": True, "object": "hermes.session", "session": _session_payload(child)})
            if path.startswith("/api/sessions/") and path.endswith(("/chat", "/chat/stream")):
                parts = path.split("/")
                session_id = parts[-3] if path.endswith("/chat/stream") else parts[-2]
                stream = path.endswith("/chat/stream") or _coerce_request_bool(body.get("stream"), False)
                return self._post_session_chat(session_id, body, stream=stream)
            if path == "/api/jobs":
                code, payload = self._create_job(body)
                return self._json(code, payload)
            if path.startswith("/api/jobs/") and path.endswith(("/run", "/trigger")):
                job_id = path.split("/")[-2]
                code, payload = self._run_job_now(job_id)
                return self._json(code, payload)
            if path.startswith("/api/jobs/") and path.endswith(("/pause", "/resume")):
                from .cron import CronStore

                parts = path.split("/")
                job_id = parts[-2]
                invalid = _api_job_id_error(job_id, method="POST", path=getattr(self, "path", ""), headers=self.headers)
                if invalid is not None:
                    code, payload = invalid
                    return self._json(code, payload)
                enabled = parts[-1] == "resume"
                ok = CronStore().set_enabled(job_id, enabled)
                code, payload = self._job_detail(job_id) if ok else (404, {"ok": False, "error": "job not found", "id": job_id})
                if ok:
                    payload["ok"] = True
                    payload["paused"] = not enabled
                return self._json(code, payload)
            return self._json(404, {"error": "not found"})

        def _post_chat_completion(self, body: dict[str, Any]) -> None:
            session_key, session_key_error = self._session_key()
            if session_key_error is not None:
                code, payload = session_key_error
                return self._json(code, payload)
            messages = body.get("messages")
            validation_error = _chat_messages_validation_error(messages)
            if validation_error is not None:
                return self._json(400, validation_error)
            assert isinstance(messages, list)
            history, last_user = _convert(messages)
            model = body.get("model")
            stream = _coerce_request_bool(body.get("stream"), False)
            metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
            metadata = dict(metadata)
            if session_key:
                metadata["session_key"] = session_key
            session_id = (
                metadata.get("session_id")
                or body.get("session_id")
                or self.headers.get("X-Aegis-Session")
                or self.headers.get("X-Hermes-Session-Id")
                or None
            )
            if not session_id:
                session_id = _derive_chat_session_id_from_messages(body.get("messages", []))
            provider_name = (
                metadata.get("provider")
                or body.get("provider")
                or self.headers.get("X-Aegis-Provider")
                or None
            )
            cwd = (
                metadata.get("cwd")
                or body.get("cwd")
                or self.headers.get("X-Aegis-Cwd")
                or None
            )
            service_tier = _request_service_tier(body, metadata)
            reasoning_effort, reasoning_error = _request_reasoning_effort(body, metadata)
            if reasoning_error is not None:
                return self._json(400, reasoning_error)
            max_tokens, max_tokens_error = _request_max_tokens(
                body,
                "max_completion_tokens",
                "max_tokens",
            )
            if max_tokens_error is not None:
                return self._json(400, max_tokens_error)
            runtime_controls: dict[str, Any] = {}
            if service_tier:
                runtime_controls["service_tier"] = service_tier
            if reasoning_effort:
                runtime_controls["reasoning_effort"] = reasoning_effort

            cid = new_id("chatcmpl")

            if not stream:
                idempotency_key = str(self.headers.get("Idempotency-Key", "") or "")
                idempotency_body = {
                    **body,
                    "_session_id_header": self.headers.get("X-Aegis-Session") or self.headers.get("X-Hermes-Session-Id"),
                    "_provider_header": self.headers.get("X-Aegis-Provider"),
                    "_cwd_header": self.headers.get("X-Aegis-Cwd"),
                    "_session_key_header": session_key,
                }
                idempotency_fp = _request_fingerprint(idempotency_body, _CHAT_IDEMPOTENCY_FINGERPRINT_KEYS)
                def compute_response() -> dict[str, Any]:
                    run_meta = {
                        "request_id": cid,
                        **({"gateway_session_key": session_key} if session_key else {}),
                    }
                    if runtime_controls:
                        run_meta.update(runtime_controls_meta(runtime_controls))
                    response_session, response_agent = self._prepare_stream_agent(
                        runner,
                        session_id=str(session_id) if session_id else None,
                        title=last_user[:80] if isinstance(last_user, str) else None,
                        history=history,
                        model=model,
                        provider_name=provider_name,
                        cwd=cwd,
                        surface="serve",
                        meta=run_meta,
                    )
                    disconnect_cancelled = False
                    cancel_watchdog_started = False

                    def cancel_response_agent() -> None:
                        nonlocal disconnect_cancelled, cancel_watchdog_started
                        if disconnect_cancelled:
                            return
                        disconnect_cancelled = True
                        self._cancel_agent(response_agent)
                        if not cancel_watchdog_started:
                            cancel_watchdog_started = True
                            self._start_cancel_watchdog(
                                lambda: response_agent,
                                lambda: disconnect_cancelled,
                                name="aegis-api-chat-cancel",
                            )

                    if response_agent is not None:
                        self._aegis_on_disconnect = cancel_response_agent
                    run_kwargs: dict[str, Any] = {
                        "model": model,
                        "provider_name": provider_name,
                        "cwd": cwd,
                        "stream": False,
                        "surface": "serve",
                        "meta": run_meta,
                    }
                    if max_tokens is not None:
                        run_kwargs["max_tokens"] = max_tokens
                    if response_agent is not None:
                        run_kwargs.update({
                            "session": response_session,
                            "agent": response_agent,
                            "reuse_agent": False,
                        })
                    else:
                        run_kwargs.update({
                            "session_id": session_id,
                            "history": history,
                        })
                    try:
                        result = runner.run_prompt(last_user, **run_kwargs)
                    finally:
                        if getattr(self, "_aegis_on_disconnect", None) is cancel_response_agent:
                            self._aegis_on_disconnect = None
                    if disconnect_cancelled:
                        raise BrokenPipeError("HTTP client disconnected")
                    response_metadata = {
                        "session_id": result.session.id,
                        "trace_id": result.trace_id,
                        "run_id": result.run_id,
                    }
                    if service_tier:
                        response_metadata["service_tier"] = service_tier
                    if session_key:
                        response_metadata["session_key"] = session_key
                    return {
                        "id": cid, "object": "chat.completion", "created": int(time.time()),
                        "model": result.agent.provider.model,
                        "choices": [{"index": 0, "message": {"role": "assistant", "content": result.text},
                                     "finish_reason": "stop"}],
                        "usage": _usage(getattr(result, "usage", None) or result.agent),
                        "metadata": response_metadata,
                    }

                response = idempotency_cache.get_or_compute(idempotency_key, idempotency_fp, compute_response)
                response_metadata = response.get("metadata") if isinstance(response.get("metadata"), dict) else {}
                response_session_id = response_metadata.get("session_id")
                return self._json(200, response, self._session_headers(
                    session_id=str(response_session_id or session_id or ""),
                    session_key=session_key,
                ))

            # streaming
            run_meta = {
                "request_id": cid,
                **({"gateway_session_key": session_key} if session_key else {}),
            }
            if runtime_controls:
                run_meta.update(runtime_controls_meta(runtime_controls))
            stream_session, stream_agent = self._prepare_stream_agent(
                runner,
                session_id=str(session_id) if session_id else None,
                history=history,
                model=model,
                provider_name=provider_name,
                cwd=cwd,
                surface="serve",
                meta=run_meta,
            )
            stream_closed = False
            disconnect_cancelled = False
            cancel_watchdog_started = False

            def cancel_stream_agent() -> None:
                nonlocal cancel_watchdog_started, disconnect_cancelled
                if disconnect_cancelled:
                    return
                disconnect_cancelled = True
                self._cancel_agent(stream_agent)
                if not cancel_watchdog_started:
                    cancel_watchdog_started = True
                    self._start_cancel_watchdog(
                        lambda: stream_agent,
                        lambda: disconnect_cancelled and not stream_closed,
                    )

            if stream_agent is not None:
                self._aegis_on_disconnect = cancel_stream_agent
            self._send_sse_headers(self._session_headers(session_id=session_id, session_key=session_key))

            def write_sse_payload(payload: dict[str, Any], *, event: str | None = None) -> bool:
                nonlocal stream_closed
                if stream_closed:
                    return False
                try:
                    if event:
                        self.wfile.write(f"event: {event}\n".encode())
                    self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode())
                    self.wfile.flush()
                    return True
                except (BrokenPipeError, ConnectionResetError):
                    stream_closed = True
                    self._notify_sse_disconnect()
                    return False

            def write_sse_done() -> None:
                nonlocal stream_closed
                if stream_closed:
                    return
                try:
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    stream_closed = True
                    self._notify_sse_disconnect()

            role_chunk = {
                "id": cid,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model or config.get("model.default", ""),
                "choices": [{"index": 0, "delta": {"role": "assistant"}}],
            }
            if not write_sse_payload(role_chunk):
                return

            def emit(e: dict) -> None:
                if stream_closed:
                    cancel_stream_agent()
                    return
                if e.get("type") == "assistant_delta":
                    chunk = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
                             "model": model or config.get("model.default", ""),
                             "choices": [{"index": 0, "delta": {"content": e["text"]}}]}
                else:
                    meta = _event_metadata(e)
                    if not meta:
                        return
                    chunk = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
                             "model": model or config.get("model.default", ""),
                             "choices": [{"index": 0, "delta": {}}],
                             "metadata": {"event": meta}}
                    if meta.get("type") in {"tool_start", "tool_result"}:
                        write_sse_payload({
                            "id": cid,
                            "object": "hermes.tool.progress",
                            "created": int(time.time()),
                            "type": meta.get("type"),
                            "name": meta.get("name") or meta.get("tool_name") or "",
                            "status": meta.get("status") or ("done" if meta.get("type") == "tool_result" else "running"),
                            "summary": meta.get("summary") or meta.get("preview") or "",
                            "metadata": {"event": meta},
                        }, event="hermes.tool.progress")
                        if stream_closed:
                            return
                if not write_sse_payload(chunk):
                    cancel_stream_agent()

            run_kwargs: dict[str, Any] = {
                "model": model,
                "provider_name": provider_name,
                "cwd": cwd,
                "stream": True,
                "surface": "serve",
                "meta": run_meta,
                "on_event": emit,
            }
            if max_tokens is not None:
                run_kwargs["max_tokens"] = max_tokens
            if stream_session is not None and stream_agent is not None:
                run_kwargs.update({"session": stream_session, "agent": stream_agent, "reuse_agent": False})
            else:
                run_kwargs.update({"session_id": session_id, "history": history})
            try:
                result = runner.run_prompt(last_user, **run_kwargs)
            finally:
                self._aegis_on_disconnect = None
            if disconnect_cancelled or stream_closed:
                return
            final_metadata = {
                "session_id": result.session.id,
                "trace_id": result.trace_id,
                "run_id": result.run_id,
            }
            if service_tier:
                final_metadata["service_tier"] = service_tier
            if session_key:
                final_metadata["session_key"] = session_key
            final = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
                     "model": result.agent.provider.model,
                     "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                     "usage": _usage(getattr(result, "usage", None) or result.agent),
                     "metadata": final_metadata}
            write_sse_payload(final)
            write_sse_done()

        def _response_request_context(self, body: dict[str, Any]) -> tuple[dict[str, Any] | None, tuple[int, Any] | None]:
            instructions = str(body.get("instructions") or "").strip() or None
            previous_id = str(body.get("previous_response_id") or "").strip()
            conversation = _conversation_id(body.get("conversation"))
            if previous_id and conversation:
                return None, (400, {"error": "Cannot use both 'conversation' and 'previous_response_id'"})
            if conversation and not previous_id:
                previous_id = response_store.get_conversation(conversation) or ""

            explicit_history, history_error = _parse_response_history(body.get("conversation_history"))
            if history_error:
                return None, (400, {"error": history_error})
            previous_state = None
            if previous_id:
                previous_state = response_store.get_state(previous_id)
                if previous_state is None:
                    return None, (404, {"error": f"Previous response not found: {previous_id}"})
                if not explicit_history:
                    explicit_history = _history_from_state(previous_state)

            input_history, last_user = _responses_messages(body)
            state_history = list(explicit_history) + list(input_history)
            state_history = _maybe_truncate_response_history(state_history, body, config)
            return {
                "instructions": instructions,
                "previous_id": previous_id,
                "conversation": conversation,
                "previous_state": previous_state,
                "state_history": state_history,
                "last_user": last_user,
                "messages": state_history + [last_user],
            }, None

        def _post_response_input_tokens(self, body: dict[str, Any]) -> None:
            validation_error = _responses_input_validation_error(body)
            if validation_error is not None:
                return self._json(400, validation_error)
            context, error = self._response_request_context(body)
            if error is not None:
                code, payload = error
                return self._json(code, payload)
            assert context is not None
            input_items = _stored_response_input_items(
                context["messages"],
                context["instructions"],
                previous_state=context["previous_state"],
            )
            return self._json(200, {
                "object": "response.input_tokens",
                "input_tokens": _estimate_response_input_tokens(input_items, body=body),
            })

        def _post_response_compact(self, body: dict[str, Any]) -> None:
            validation_error = _responses_input_validation_error(body)
            if validation_error is not None:
                return self._json(400, validation_error)
            context, error = self._response_request_context(body)
            if error is not None:
                code, payload = error
                return self._json(code, payload)
            assert context is not None

            input_items = _stored_response_input_items(
                context["messages"],
                context["instructions"],
                previous_state=context["previous_state"],
            )
            input_tokens = _estimate_response_input_tokens(input_items, body=body)
            summary = _response_compaction_summary(input_items, input_tokens)
            output = _response_compaction_output_items(input_items, summary)
            output_tokens = estimate_tokens(summary)
            response_id = new_id("resp")
            response = {
                "id": response_id,
                "object": "response.compaction",
                "created_at": int(time.time()),
                "metadata": {},
                "output": output,
                "usage": {
                    "input_tokens": input_tokens,
                    "input_tokens_details": {"cached_tokens": 0},
                    "output_tokens": output_tokens,
                    "output_tokens_details": {"reasoning_tokens": 0},
                    "total_tokens": input_tokens + output_tokens,
                },
            }
            if body.get("model") is not None:
                response["model"] = str(body.get("model") or "")
            previous_id = context.get("previous_id") or None
            conversation = context.get("conversation") or None
            if previous_id:
                response["previous_response_id"] = previous_id
            if conversation:
                response["conversation"] = conversation
            store_response = _coerce_request_bool(body.get("store"), True)
            if store_response:
                response_store.put(response, {
                    "conversation_history": _history_from_compaction_output(output, summary),
                    "input_items": output,
                    "instructions": context["instructions"],
                    "conversation": conversation,
                })
                if conversation:
                    response_store.set_conversation(conversation, response_id)
            return self._json(200, response)

        def _post_response(self, body: dict[str, Any]) -> None:
            session_key, session_key_error = self._session_key()
            if session_key_error is not None:
                code, payload = session_key_error
                return self._json(code, payload)
            include_values, include_error = _response_include_values(body.get("include"))
            if include_error is not None:
                return self._json(400, include_error)
            response_id = new_id("resp")
            model = body.get("model")
            metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
            metadata = dict(metadata)
            if session_key:
                metadata["session_key"] = session_key
            instructions = str(body.get("instructions") or "").strip() or None
            previous_id = str(body.get("previous_response_id") or "").strip()
            conversation = _conversation_id(body.get("conversation"))
            if previous_id and conversation:
                return self._json(400, {"error": "Cannot use both 'conversation' and 'previous_response_id'"})
            if conversation and not previous_id:
                previous_id = response_store.get_conversation(conversation) or ""

            explicit_history, history_error = _parse_response_history(body.get("conversation_history"))
            if history_error:
                return self._json(400, {"error": history_error})
            previous_state = None
            if previous_id:
                previous_state = response_store.get_state(previous_id)
                if previous_state is None:
                    return self._json(404, {"error": f"Previous response not found: {previous_id}"})
                if not explicit_history:
                    explicit_history = _history_from_state(previous_state)
            validation_error = _responses_input_validation_error(
                body,
                allow_omitted=previous_state is not None,
            )
            if validation_error is not None:
                return self._json(400, validation_error)

            input_history, last_user = _responses_messages(body)
            state_history = list(explicit_history) + list(input_history)
            state_history = _maybe_truncate_response_history(state_history, body, config)
            history = [m for m in state_history if not _is_opaque_response_item_message(m)]
            instruction = _instruction_message(instructions)
            if instruction is not None:
                history.insert(0, instruction)

            if previous_state is not None:
                previous_response = previous_state.get("response") if isinstance(previous_state, dict) else {}
                prev_meta_raw = previous_response.get("metadata") if isinstance(previous_response, dict) else {}
                prev_meta = prev_meta_raw if isinstance(prev_meta_raw, dict) else {}
                if not metadata.get("session_id") and not body.get("session_id"):
                    inherited_session = previous_state.get("session_id") or prev_meta.get("session_id")
                    if inherited_session:
                        metadata["session_id"] = inherited_session
            if conversation:
                metadata["conversation"] = conversation
                if not metadata.get("session_id") and not body.get("session_id"):
                    metadata["session_id"] = f"conversation:{conversation}"
            if previous_id:
                metadata["previous_response_id"] = previous_id
            store_response = _coerce_request_bool(body.get("store"), True)
            stream = _coerce_request_bool(body.get("stream"), False)
            session_id = (
                metadata.get("session_id")
                or body.get("session_id")
                or self.headers.get("X-Aegis-Session")
                or self.headers.get("X-Hermes-Session-Id")
            )
            provider_name = metadata.get("provider") or body.get("provider")
            cwd = metadata.get("cwd") or body.get("cwd")
            service_tier = _request_service_tier(body, metadata)
            if service_tier:
                metadata["service_tier"] = service_tier
            reasoning_effort, reasoning_error = _request_reasoning_effort(body, metadata)
            if reasoning_error is not None:
                return self._json(400, reasoning_error)
            if reasoning_effort:
                metadata["reasoning_effort"] = reasoning_effort
            max_tokens, max_tokens_error = _request_max_tokens(
                body,
                "max_output_tokens",
                "max_completion_tokens",
            )
            if max_tokens_error is not None:
                return self._json(400, max_tokens_error)
            runtime_controls: dict[str, Any] = {}
            if service_tier:
                runtime_controls["service_tier"] = service_tier
            if reasoning_effort:
                runtime_controls["reasoning_effort"] = reasoning_effort
            parallel_tool_calls = _coerce_request_bool(body.get("parallel_tool_calls"), True)
            response_title = last_user.content[:80] or response_id
            idempotency_key = str(self.headers.get("Idempotency-Key", "") or "")
            idempotency_body = {
                **body,
                "_session_id_header": self.headers.get("X-Aegis-Session") or self.headers.get("X-Hermes-Session-Id"),
                "_provider_header": self.headers.get("X-Aegis-Provider"),
                "_cwd_header": self.headers.get("X-Aegis-Cwd"),
                "_session_key_header": session_key,
            }
            idempotency_fp = _request_fingerprint(idempotency_body, _RESPONSES_IDEMPOTENCY_FINGERPRINT_KEYS)
            if stream:
                sequence = 0
                message_item_id = new_id("msg")
                message_opened = False
                message_output_index: int | None = None
                next_output_index = 0
                text_parts: list[str] = []
                pending_tool_calls: dict[str, dict[str, Any]] = {}
                streamed_output_items: list[dict[str, Any]] = []
                disconnect_cancelled = False
                cancel_watchdog_started = False

                def send_event(event_name: str, payload: dict[str, Any]) -> bool:
                    nonlocal sequence
                    payload.setdefault("type", event_name)
                    payload.setdefault("sequence_number", sequence)
                    sequence += 1
                    return self._write_sse(payload, event=event_name)

                def stream_cached_response(response: dict[str, Any]) -> None:
                    response_metadata = response.get("metadata") if isinstance(response.get("metadata"), dict) else {}
                    cached_session_id = response_metadata.get("session_id") or session_id
                    cached_response_id = str(response.get("id") or response_id)
                    self._send_sse_headers(self._session_headers(
                        session_id=str(cached_session_id or ""),
                        session_key=session_key,
                    ))
                    created = dict(response)
                    created["status"] = "in_progress"
                    created["output"] = []
                    send_event("response.created", {"response": created})
                    send_event("response.in_progress", {"response": dict(created)})
                    for output_index, raw_item in enumerate(response.get("output") or []):
                        if not isinstance(raw_item, dict):
                            continue
                        item = dict(raw_item)
                        send_event("response.output_item.added", {
                            "output_index": output_index,
                            "item": item,
                        })
                        if item.get("type") == "message":
                            item_id = str(item.get("id") or new_id("msg"))
                            for content_index, part in enumerate(item.get("content") or []):
                                if not isinstance(part, dict):
                                    continue
                                if str(part.get("type") or "") != "output_text":
                                    continue
                                text = str(part.get("text") or "")
                                if text:
                                    send_event("response.output_text.delta", {
                                        "response_id": cached_response_id,
                                        "item_id": item_id,
                                        "output_index": output_index,
                                        "content_index": content_index,
                                        "delta": text,
                                        "logprobs": [],
                                    })
                                send_event("response.output_text.done", {
                                    "response_id": cached_response_id,
                                    "item_id": item_id,
                                    "output_index": output_index,
                                    "content_index": content_index,
                                    "text": text,
                                    "logprobs": [],
                                })
                        send_event("response.output_item.done", {
                            "output_index": output_index,
                            "item": item,
                        })
                    status = str(response.get("status") or "completed")
                    if status == "cancelled":
                        send_event("response.cancelled", {"response": response})
                    elif status == "failed":
                        send_event("response.failed", {
                            "response": response,
                            "error": response.get("error"),
                        })
                    else:
                        send_event("response.completed", {"response": response})
                    try:
                        self.wfile.write(b"data: [DONE]\n\n")
                    except (BrokenPipeError, ConnectionResetError):
                        pass

                stream_idempotency_state, stream_idempotency_cached, stream_idempotency_flight = (
                    idempotency_cache.begin(idempotency_key, idempotency_fp)
                )
                stream_idempotency_owner = stream_idempotency_state in {"owner"}
                if stream_idempotency_state == "waiter":
                    stream_idempotency_cached = idempotency_cache.wait(stream_idempotency_flight)
                if stream_idempotency_state in {"cached", "waiter"} and stream_idempotency_cached is not None:
                    return stream_cached_response(stream_idempotency_cached)

                def finish_stream_idempotency(response: dict[str, Any]) -> None:
                    if stream_idempotency_owner:
                        idempotency_cache.finish(
                            idempotency_key,
                            idempotency_fp,
                            stream_idempotency_flight,
                            response,
                        )

                def open_message_item() -> bool:
                    nonlocal message_opened, message_output_index, next_output_index
                    if message_opened:
                        return True
                    message_opened = True
                    message_output_index = next_output_index
                    next_output_index += 1
                    return send_event("response.output_item.added", {
                        "output_index": message_output_index,
                        "item": {
                            "id": message_item_id,
                            "type": "message",
                            "status": "in_progress",
                            "role": "assistant",
                            "content": [],
                        },
                    })

                def emit_tool_start(e: dict[str, Any]) -> None:
                    nonlocal next_output_index
                    call_id = str(e.get("id") or e.get("tool_call_id") or new_id("call"))
                    args = e.get("args", e.get("arguments", {}))
                    if isinstance(args, str):
                        arguments = args
                    else:
                        arguments = json.dumps(args if args is not None else {}, default=str)
                    item_id = new_id("fc")
                    output_index = next_output_index
                    next_output_index += 1
                    item = {
                        "id": item_id,
                        "type": "function_call",
                        "status": "in_progress",
                        "name": str(e.get("name") or e.get("tool_name") or ""),
                        "call_id": call_id,
                        "arguments": arguments,
                    }
                    pending_tool_calls[call_id] = {
                        "item_id": item_id,
                        "output_index": output_index,
                        "name": item["name"],
                        "arguments": arguments,
                        "call_id": call_id,
                        "streamed_item_index": len(streamed_output_items),
                    }
                    streamed_output_items.append({
                        "id": item_id,
                        "type": "function_call",
                        "status": "in_progress",
                        "name": item["name"],
                        "arguments": arguments,
                        "call_id": call_id,
                    })
                    send_event("response.output_item.added", {
                        "output_index": output_index,
                        "item": item,
                    })

                def emit_tool_result(e: dict[str, Any]) -> None:
                    nonlocal next_output_index
                    call_id = str(e.get("id") or e.get("tool_call_id") or "")
                    pending = pending_tool_calls.pop(call_id, None) if call_id else None
                    if pending is None:
                        return
                    done_item = {
                        "id": pending["item_id"],
                        "type": "function_call",
                        "status": "completed",
                        "name": pending["name"],
                        "call_id": pending["call_id"],
                        "arguments": pending["arguments"],
                    }
                    send_event("response.output_item.done", {
                        "output_index": pending["output_index"],
                        "item": done_item,
                    })
                    streamed_index = pending.get("streamed_item_index")
                    if isinstance(streamed_index, int) and 0 <= streamed_index < len(streamed_output_items):
                        streamed_output_items[streamed_index] = dict(done_item)
                    result_text = _tool_result_text(e)
                    output_parts = [{"type": "input_text", "text": result_text}]
                    output_status = "failed" if e.get("is_error") else "completed"
                    output_item = {
                        "id": new_id("fco"),
                        "type": "function_call_output",
                        "call_id": pending["call_id"],
                        "output": output_parts,
                        "status": output_status,
                    }
                    output_index = next_output_index
                    next_output_index += 1
                    streamed_output_items.append({
                        "id": output_item["id"],
                        "type": "function_call_output",
                        "status": output_status,
                        "call_id": pending["call_id"],
                        "output": output_parts,
                    })
                    send_event("response.output_item.added", {
                        "output_index": output_index,
                        "item": output_item,
                    })
                    send_event("response.output_item.done", {
                        "output_index": output_index,
                        "item": output_item,
                    })

                def persist_stream_response(response: dict[str, Any], result=None) -> None:
                    if not store_response:
                        return
                    full_history = _response_conversation_history(state_history, last_user, result)
                    response_store.put(response, {
                        "conversation_history": full_history,
                        "input_items": _stored_response_input_items(
                            state_history + [last_user],
                            instructions,
                            previous_state=previous_state,
                        ),
                        "instructions": instructions,
                        "session_id": response.get("metadata", {}).get("session_id") or session_id,
                        "conversation": conversation,
                    })
                    if conversation:
                        response_store.set_conversation(conversation, response_id)

                self._send_sse_headers(self._session_headers(session_id=session_id, session_key=session_key))

                def response_stream_agent():
                    with state_lock:
                        rec = active_responses.get(response_id)
                        return rec.get("agent") if rec is not None else None

                def start_response_cancel_watchdog() -> None:
                    nonlocal cancel_watchdog_started
                    if cancel_watchdog_started:
                        return
                    cancel_watchdog_started = True
                    self._start_cancel_watchdog(
                        response_stream_agent,
                        lambda: disconnect_cancelled and response_id in active_responses,
                        name="aegis-api-response-cancel",
                    )

                def cancel_on_disconnect() -> None:
                    nonlocal disconnect_cancelled
                    disconnect_cancelled = True
                    with state_lock:
                        self._request_cancel_response_locked(response_id, "SSE client disconnected")
                    start_response_cancel_watchdog()

                self._aegis_on_disconnect = cancel_on_disconnect
                created_response = {
                    "id": response_id,
                    "object": "response",
                    "created_at": int(time.time()),
                    "status": "in_progress",
                    "model": model or config.get("model.default", ""),
                    "output": [],
                    "metadata": metadata,
                    "instructions": instructions,
                    "previous_response_id": previous_id or None,
                    "conversation": conversation or None,
                    "store": store_response,
                    "parallel_tool_calls": parallel_tool_calls,
                }
                _attach_response_include(created_response, include_values)
                with state_lock:
                    self._register_response_locked(
                        response_id,
                        response=created_response,
                        store_response=store_response,
                    )
                send_event("response.created", {
                    "response": {
                        **created_response,
                    },
                })
                send_event("response.in_progress", {
                    "response": {
                        **created_response,
                    },
                })
                if store_response:
                    response_store.put(created_response, {
                        "conversation_history": _history_payload(state_history + [last_user]),
                        "input_items": _stored_response_input_items(
                            state_history + [last_user],
                            instructions,
                            previous_state=previous_state,
                        ),
                        "instructions": instructions,
                        "session_id": session_id,
                        "conversation": conversation,
                    })
                with state_lock:
                    self._register_response_locked(
                        response_id,
                        response=created_response,
                        store_response=store_response,
                    )

                def emit(e: dict[str, Any]) -> None:
                    if e.get("type") == "assistant_delta":
                        delta = str(e.get("text") or "")
                        if not delta:
                            return
                        if not open_message_item():
                            return
                        text_parts.append(delta)
                        send_event("response.output_text.delta", {
                            "response_id": response_id,
                            "item_id": message_item_id,
                            "output_index": message_output_index if message_output_index is not None else 0,
                            "content_index": 0,
                            "delta": delta,
                            "logprobs": [],
                        })
                        return
                    if e.get("type") == "tool_start":
                        emit_tool_start(e)
                        return
                    if e.get("type") == "tool_result":
                        emit_tool_result(e)
                        return
                    meta = _event_metadata(e)
                    if meta:
                        send_event("aegis.event", {
                            "response_id": response_id,
                            "event": meta,
                        })

                try:
                    response_session, response_agent = self._prepare_response_agent(
                        response_id,
                        session_id=str(session_id) if session_id else None,
                        title=response_title,
                        history=history,
                        model=model,
                        provider_name=provider_name,
                        cwd=cwd,
                        session_key=session_key,
                        service_tier=service_tier,
                        runtime_controls=runtime_controls,
                    )
                    with state_lock:
                        self._register_response_locked(
                            response_id,
                            agent=response_agent,
                            session=response_session,
                            store_response=store_response,
                        )
                    run_kwargs: dict[str, Any] = {
                        "model": model,
                        "provider_name": provider_name,
                        "cwd": cwd,
                        "stream": True,
                        "surface": "serve",
                        "meta": {
                            "request_id": response_id,
                            "api": "responses",
                            **({"gateway_session_key": session_key} if session_key else {}),
                        },
                        "on_event": emit,
                    }
                    if max_tokens is not None:
                        run_kwargs["max_tokens"] = max_tokens
                    if runtime_controls:
                        run_kwargs["meta"].update(runtime_controls_meta(runtime_controls))
                    if response_agent is not None:
                        run_kwargs.update({
                            "session": response_session,
                            "agent": response_agent,
                            "reuse_agent": False,
                        })
                    else:
                        run_kwargs.update({
                            "session_id": session_id,
                            "history": history,
                        })
                    result = runner.run_prompt(last_user, **run_kwargs)
                    if disconnect_cancelled or getattr(self.wfile, "closed", False):
                        text = "".join(text_parts)
                        cancelled_metadata = dict(metadata)
                        if response_session is not None and not cancelled_metadata.get("session_id"):
                            cancelled_metadata["session_id"] = getattr(response_session, "id", "")
                        cancelled = _response_object(
                            response_id,
                            None,
                            status="cancelled",
                            metadata_extra=cancelled_metadata,
                            parallel_tool_calls=parallel_tool_calls,
                        )
                        cancelled.update({
                            "model": model or config.get("model.default", ""),
                            "output": list(streamed_output_items) + (_response_output(text) if text else []),
                            "output_text": text,
                            "instructions": instructions,
                            "previous_response_id": previous_id or None,
                            "conversation": conversation or None,
                            "store": store_response,
                        })
                        _attach_response_include(cancelled, include_values)
                        cancelled = self._mark_response_cancelled(cancelled, "SSE client disconnected")
                        if store_response:
                            history_snapshot = list(state_history)
                            history_snapshot.append(last_user)
                            if text:
                                history_snapshot.append(Message.assistant(text))
                            response_store.put(cancelled, {
                                "conversation_history": _history_payload(history_snapshot),
                                "input_items": _stored_response_input_items(
                                    state_history + [last_user],
                                    instructions,
                                    previous_state=previous_state,
                                ),
                                "instructions": instructions,
                                "session_id": cancelled.get("metadata", {}).get("session_id") or session_id,
                                "conversation": conversation,
                            })
                            if conversation:
                                response_store.set_conversation(conversation, response_id)
                        finish_stream_idempotency(cancelled)
                        self._finish_response(response_id, cancelled)
                        return
                except Exception as exc:  # noqa: BLE001
                    text = "".join(text_parts)
                    cancelled, cancel_reason = self._response_cancel_requested(response_id)
                    failed = _response_object(
                        response_id,
                        None,
                        status="cancelled" if cancelled else "failed",
                        metadata_extra=metadata,
                        parallel_tool_calls=parallel_tool_calls,
                    )
                    failed.update({
                        "model": model or config.get("model.default", ""),
                        "output": list(streamed_output_items) + (_response_output(text) if text else []),
                        "output_text": text,
                        "error": None if cancelled else {
                            "message": f"{type(exc).__name__}: {exc}",
                            "type": "server_error",
                        },
                        "instructions": instructions,
                        "previous_response_id": previous_id or None,
                        "conversation": conversation or None,
                        "store": store_response,
                    })
                    _attach_response_include(failed, include_values)
                    if cancelled:
                        failed = self._mark_response_cancelled(failed, cancel_reason)
                    if store_response:
                        history_snapshot = list(state_history)
                        history_snapshot.append(last_user)
                        if text:
                            history_snapshot.append(Message.assistant(text))
                        response_store.put(failed, {
                            "conversation_history": _history_payload(history_snapshot),
                            "input_items": _stored_response_input_items(
                                state_history + [last_user],
                                instructions,
                                previous_state=previous_state,
                            ),
                            "instructions": instructions,
                            "session_id": session_id,
                            "conversation": conversation,
                        })
                        if conversation:
                            response_store.set_conversation(conversation, response_id)
                    finish_stream_idempotency(failed)
                    if cancelled:
                        send_event("response.cancelled", {"response": failed})
                    else:
                        send_event("response.failed", {"response": failed, "error": failed["error"]})
                    self._finish_response(response_id, failed)
                    try:
                        self.wfile.write(b"data: [DONE]\n\n")
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    return

                cancelled, cancel_reason = self._response_cancel_requested(response_id)
                response = _response_object(
                    response_id,
                    result,
                    status="cancelled" if cancelled else "completed",
                    metadata_extra=metadata,
                    parallel_tool_calls=parallel_tool_calls,
                )
                response["instructions"] = instructions
                response["previous_response_id"] = previous_id or None
                response["conversation"] = conversation or None
                response["store"] = store_response
                _attach_response_include(response, include_values)
                final_text = response.get("output_text") or "".join(text_parts)
                if streamed_output_items:
                    response["output"] = list(streamed_output_items) + _response_output(final_text)
                if cancelled:
                    final_text = "".join(text_parts)
                    response["output_text"] = final_text
                    response["output"] = list(streamed_output_items) + (_response_output(final_text) if final_text else [])
                    response = self._mark_response_cancelled(response, cancel_reason)
                elif final_text or message_opened:
                    if not message_opened:
                        open_message_item()
                    out_index = message_output_index if message_output_index is not None else 0
                    send_event("response.output_text.done", {
                        "response_id": response_id,
                        "item_id": message_item_id,
                        "output_index": out_index,
                        "content_index": 0,
                        "text": final_text,
                        "logprobs": [],
                    })
                    send_event("response.output_item.done", {
                        "output_index": out_index,
                        "item": {
                            "id": message_item_id,
                            "type": "message",
                            "status": "completed",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": final_text}],
                        },
                    })
                persist_stream_response(response, result)
                if cancelled:
                    send_event("response.cancelled", {"response": response})
                else:
                    send_event("response.completed", {
                        "response": response,
                    })
                finish_stream_idempotency(response)
                self._finish_response(response_id, response)
                try:
                    self.wfile.write(b"data: [DONE]\n\n")
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return
            def compute_response() -> dict[str, Any]:
                response: dict[str, Any] | None = None
                disconnect_cancelled = False
                cancel_watchdog_started = False

                def current_response_agent():
                    with state_lock:
                        rec = active_responses.get(response_id)
                        return rec.get("agent") if rec is not None else None

                def start_response_cancel_watchdog() -> None:
                    nonlocal cancel_watchdog_started
                    if cancel_watchdog_started:
                        return
                    cancel_watchdog_started = True
                    self._start_cancel_watchdog(
                        current_response_agent,
                        lambda: disconnect_cancelled and response_id in active_responses,
                        name="aegis-api-response-nonstream-cancel",
                    )

                def cancel_on_disconnect() -> None:
                    nonlocal disconnect_cancelled
                    if disconnect_cancelled:
                        return
                    disconnect_cancelled = True
                    with state_lock:
                        self._request_cancel_response_locked(response_id, "HTTP client disconnected")
                    start_response_cancel_watchdog()

                pending_response = {
                    "id": response_id,
                    "object": "response",
                    "created_at": int(time.time()),
                    "status": "in_progress",
                    "model": model or config.get("model.default", ""),
                    "output": [],
                    "output_text": "",
                    "usage": _response_usage(None),
                    "error": None,
                    "incomplete_details": None,
                    "metadata": metadata,
                    "instructions": instructions,
                    "previous_response_id": previous_id or None,
                    "conversation": conversation or None,
                    "store": store_response,
                    "parallel_tool_calls": parallel_tool_calls,
                }
                _attach_response_include(pending_response, include_values)
                pending_state = {
                    "conversation_history": _history_payload(state_history + [last_user]),
                    "input_items": _stored_response_input_items(
                        state_history + [last_user],
                        instructions,
                        previous_state=previous_state,
                    ),
                    "instructions": instructions,
                    "session_id": session_id,
                    "conversation": conversation,
                }
                with state_lock:
                    self._register_response_locked(
                        response_id,
                        response=pending_response,
                        state=pending_state,
                        store_response=store_response,
                    )
                self._aegis_on_disconnect = cancel_on_disconnect
                try:
                    response_session, response_agent = self._prepare_response_agent(
                        response_id,
                        session_id=str(session_id) if session_id else None,
                        title=response_title,
                        history=history,
                        model=model,
                        provider_name=provider_name,
                        cwd=cwd,
                        session_key=session_key,
                        service_tier=service_tier,
                        runtime_controls=runtime_controls,
                    )
                    with state_lock:
                        self._register_response_locked(
                            response_id,
                            agent=response_agent,
                            session=response_session,
                            store_response=store_response,
                        )
                    run_kwargs: dict[str, Any] = {
                        "model": model,
                        "provider_name": provider_name,
                        "cwd": cwd,
                        "stream": False,
                        "surface": "serve",
                        "meta": {
                            "request_id": response_id,
                            "api": "responses",
                            **({"gateway_session_key": session_key} if session_key else {}),
                        },
                    }
                    if max_tokens is not None:
                        run_kwargs["max_tokens"] = max_tokens
                    if runtime_controls:
                        run_kwargs["meta"].update(runtime_controls_meta(runtime_controls))
                    if response_agent is not None:
                        run_kwargs.update({
                            "session": response_session,
                            "agent": response_agent,
                            "reuse_agent": False,
                        })
                    else:
                        run_kwargs.update({
                            "session_id": session_id,
                            "history": history,
                        })
                    result = runner.run_prompt(last_user, **run_kwargs)
                    if disconnect_cancelled:
                        raise BrokenPipeError("HTTP client disconnected")
                    cancelled, cancel_reason = self._response_cancel_requested(response_id)
                    response = _response_object(
                        response_id,
                        result,
                        status="cancelled" if cancelled else "completed",
                        metadata_extra=metadata,
                        parallel_tool_calls=parallel_tool_calls,
                    )
                    if cancelled:
                        response = self._mark_response_cancelled(response, cancel_reason)
                    response["instructions"] = instructions
                    response["previous_response_id"] = previous_id or None
                    response["conversation"] = conversation or None
                    response["store"] = store_response
                    _attach_response_include(response, include_values)
                    if store_response:
                        full_history = _response_conversation_history(state_history, last_user, result)
                        response_store.put(response, {
                            "conversation_history": full_history,
                            "input_items": _stored_response_input_items(
                                state_history + [last_user],
                                instructions,
                                previous_state=previous_state,
                            ),
                            "instructions": instructions,
                            "session_id": response.get("metadata", {}).get("session_id") or session_id,
                            "conversation": conversation,
                        })
                        if conversation:
                            response_store.set_conversation(conversation, response_id)
                    return response
                finally:
                    if getattr(self, "_aegis_on_disconnect", None) is cancel_on_disconnect:
                        self._aegis_on_disconnect = None
                    self._finish_response(response_id, response)

            response = idempotency_cache.get_or_compute(idempotency_key, idempotency_fp, compute_response)
            response_session_id = None
            response_metadata = response.get("metadata") if isinstance(response.get("metadata"), dict) else {}
            response_session_id = response_metadata.get("session_id")
            return self._json(200, response, self._session_headers(
                session_id=str(response_session_id or session_id or ""),
                session_key=session_key,
            ))

        def _post_session_chat(self, session_id: str, body: dict[str, Any], *, stream: bool = False) -> None:
            from .session import SessionStore

            session_key, session_key_error = self._session_key()
            if session_key_error is not None:
                code, payload = session_key_error
                return self._json(code, payload)
            store = SessionStore()
            session = store.load(session_id)
            if session is None:
                return self._json(404, {"ok": False, "error": "session not found", "id": session_id})
            prompt = body.get("prompt", body.get("input", body.get("message", "")))
            service_tier = _request_service_tier(body)
            reasoning_effort, reasoning_error = _request_reasoning_effort(body)
            if reasoning_error is not None:
                return self._json(400, reasoning_error)
            max_tokens, max_tokens_error = _request_max_tokens(
                body,
                "max_completion_tokens",
                "max_output_tokens",
                "max_tokens",
            )
            if max_tokens_error is not None:
                return self._json(400, max_tokens_error)
            runtime_controls: dict[str, Any] = {}
            if service_tier:
                runtime_controls["service_tier"] = service_tier
            if reasoning_effort:
                runtime_controls["reasoning_effort"] = reasoning_effort
            if stream:
                stream_run_id = new_id("run")
                message_id = new_id("msg")
                sequence = 0
                stream_closed = False
                cancel_watchdog_started = False
                run_meta = {
                    "request_id": stream_run_id,
                    "api": "session_chat_stream",
                    **({"gateway_session_key": session_key} if session_key else {}),
                }
                if runtime_controls:
                    run_meta.update(runtime_controls_meta(runtime_controls))
                stream_session, stream_agent = self._prepare_stream_agent(
                    runner,
                    session=session,
                    model=body.get("model"),
                    provider_name=body.get("provider"),
                    cwd=body.get("cwd"),
                    surface="serve",
                    meta=run_meta,
                )
                session = stream_session or session
                disconnect_cancelled = False

                def cancel_stream_agent() -> None:
                    nonlocal cancel_watchdog_started, disconnect_cancelled
                    if disconnect_cancelled:
                        return
                    disconnect_cancelled = True
                    self._cancel_agent(stream_agent)
                    if not cancel_watchdog_started:
                        cancel_watchdog_started = True
                        self._start_cancel_watchdog(
                            lambda: stream_agent,
                            lambda: disconnect_cancelled and not stream_closed,
                            name="aegis-api-session-stream-cancel",
                        )

                if stream_agent is not None:
                    self._aegis_on_disconnect = cancel_stream_agent
                self._send_sse_headers(self._session_headers(session_id=session.id, session_key=session_key))

                def send_event(event_name: str, payload: dict[str, Any]) -> bool:
                    nonlocal sequence, stream_closed
                    if stream_closed:
                        return False
                    sequence += 1
                    payload.setdefault("session_id", session.id)
                    payload.setdefault("run_id", stream_run_id)
                    payload.setdefault("sequence_number", sequence)
                    payload.setdefault("created_at", int(time.time()))
                    ok = self._write_sse(payload, event=event_name)
                    if not ok:
                        stream_closed = True
                        cancel_stream_agent()
                    return ok

                if not send_event("run.started", {
                    "user_message": {"role": "user", "content": str(prompt)},
                }):
                    return
                if not send_event("message.started", {
                    "message": {"id": message_id, "role": "assistant", "status": "in_progress"},
                    "message_id": message_id,
                }):
                    return

                def emit(ev: dict[str, Any]) -> None:
                    if stream_closed:
                        cancel_stream_agent()
                        return
                    if ev.get("type") == "assistant_delta":
                        delta = str(ev.get("text") or "")
                        if delta:
                            if not send_event("assistant.delta", {
                                "message_id": message_id,
                                "delta": delta,
                            }):
                                cancel_stream_agent()
                        return
                    meta = _event_metadata(ev)
                    if meta:
                        if not send_event("event", {"message_id": message_id, "event": meta}):
                            cancel_stream_agent()

                run_kwargs: dict[str, Any] = {
                    "session": session,
                    "model": body.get("model"),
                    "provider_name": body.get("provider"),
                    "cwd": body.get("cwd"),
                    "surface": "serve",
                    "stream": True,
                    "meta": run_meta,
                    "on_event": emit,
                }
                if max_tokens is not None:
                    run_kwargs["max_tokens"] = max_tokens
                if stream_agent is not None:
                    run_kwargs.update({"agent": stream_agent, "reuse_agent": False})
                try:
                    result = runner.run_prompt(str(prompt), **run_kwargs)
                finally:
                    self._aegis_on_disconnect = None
                if disconnect_cancelled or stream_closed:
                    return
                final_session_id = getattr(getattr(result, "session", None), "id", session.id)
                completed_common = {
                    "session_id": final_session_id,
                    "run_id": getattr(result, "run_id", stream_run_id) or stream_run_id,
                    "trace_id": getattr(result, "trace_id", ""),
                    "turn_id": getattr(result, "turn_id", ""),
                }
                send_event("assistant.completed", {
                    **completed_common,
                    "message_id": message_id,
                    "content": getattr(result, "text", ""),
                    "completed": True,
                    "partial": False,
                    "interrupted": False,
                })
                send_event("run.completed", {
                    **completed_common,
                    "message_id": message_id,
                    "completed": True,
                    "usage": _usage(getattr(result, "usage", None) or getattr(result, "agent", None)),
                })
                send_event("done", {
                    **completed_common,
                    "message_id": message_id,
                })
                if not stream_closed:
                    try:
                        self.wfile.write(b"data: [DONE]\n\n")
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        stream_closed = True
                        self._notify_sse_disconnect()
                return
            run_meta = {
                **({"gateway_session_key": session_key} if session_key else {}),
            }
            if runtime_controls:
                run_meta.update(runtime_controls_meta(runtime_controls))
            response_session, response_agent = self._prepare_stream_agent(
                runner,
                session=session,
                model=body.get("model"),
                provider_name=body.get("provider"),
                cwd=body.get("cwd"),
                surface="serve",
                meta=run_meta,
            )
            session = response_session or session
            disconnect_cancelled = False
            cancel_watchdog_started = False

            def cancel_response_agent() -> None:
                nonlocal disconnect_cancelled, cancel_watchdog_started
                if disconnect_cancelled:
                    return
                disconnect_cancelled = True
                self._cancel_agent(response_agent)
                if not cancel_watchdog_started:
                    cancel_watchdog_started = True
                    self._start_cancel_watchdog(
                        lambda: response_agent,
                        lambda: disconnect_cancelled,
                        name="aegis-api-session-cancel",
                    )

            if response_agent is not None:
                self._aegis_on_disconnect = cancel_response_agent
            run_kwargs: dict[str, Any] = {
                "session": session,
                "model": body.get("model"),
                "provider_name": body.get("provider"),
                "cwd": body.get("cwd"),
                "surface": "serve",
                "stream": False,
                "meta": run_meta,
            }
            if max_tokens is not None:
                run_kwargs["max_tokens"] = max_tokens
            if response_agent is not None:
                run_kwargs.update({"agent": response_agent, "reuse_agent": False})
            try:
                result = runner.run_prompt(str(prompt), **run_kwargs)
            finally:
                if getattr(self, "_aegis_on_disconnect", None) is cancel_response_agent:
                    self._aegis_on_disconnect = None
            if disconnect_cancelled:
                raise BrokenPipeError("HTTP client disconnected")
            return self._json(200, {
                "ok": True,
                "id": result.session.id,
                "message": _message_payload(getattr(result, "message", Message.assistant(result.text))),
                "text": result.text,
                "run_id": result.run_id,
                "trace_id": result.trace_id,
            })

        def _post_run(self, body: dict[str, Any]) -> None:
            session_key, session_key_error = self._session_key()
            if session_key_error is not None:
                code, payload = session_key_error
                return self._json(code, payload)
            with state_lock:
                active_count = self._active_run_count_locked()
                if active_count >= max_concurrent_runs:
                    return self._json(429, {
                        "error": f"too many concurrent runs (max {max_concurrent_runs})",
                        "code": "rate_limit_exceeded",
                    })
            run_id = new_id("run")
            prompt = str(body.get("prompt", body.get("input", "")) or "")
            if not prompt:
                return self._json(400, _openai_error("Missing 'input' field", param="input"))
            session_id = str(body.get("session_id") or "") or None
            title = str(body.get("title") or prompt[:80] or run_id)
            service_tier = _request_service_tier(body)
            run_meta = {
                "server_run_id": run_id,
                **({"gateway_session_key": session_key} if session_key else {}),
            }
            if service_tier:
                run_meta.update(runtime_controls_meta({"service_tier": service_tier}))
            session = runner.load_or_create_session(session_id, title=title, surface="serve", meta=run_meta)
            now = time.time()
            record = {
                "id": run_id,
                "object": "run",
                "status": "queued",
                "created_at": int(now),
                "created_at_ts": now,
                "updated_at": int(now),
                "updated_at_ts": now,
                "session_id": session.id,
                "events": [],
                "result": "",
                "error": "",
                "trace_id": "",
                "surface_run_id": "",
                "cancel_requested": False,
                "cancel_reason": "",
                "last_event": "run.queued",
                "model": body.get("model") or "",
                "service_tier": service_tier,
                "session_key": session_key or "",
                "event_sequence": 0,
            }
            with state_lock:
                active_runs[run_id] = record
                self._append_run_event_locked(run_id, "run.queued", {
                    "status": "queued",
                    "session_id": session.id,
                    "model": body.get("model") or "",
                })
            _persist_api_run_record(record, title=title, prompt=prompt)

            approval_grants: dict[str, str] = {}

            def approver(question: str) -> bool | str:
                remembered_choice = approval_grants.get(question)
                if remembered_choice in {"session", "always"}:
                    run_snapshot = None
                    with state_lock:
                        self._append_run_event_locked(run_id, "approval.reused", {
                            "approved": True,
                            "choice": remembered_choice,
                            "prompt": question,
                        })
                        rec = active_runs.get(run_id)
                        run_snapshot = dict(rec) if rec is not None else None
                    if run_snapshot is not None:
                        _persist_api_run_record(run_snapshot, title=title, prompt=prompt)
                    return "always"
                approval_id = new_id("approval")
                event = threading.Event()
                pending = {
                    "id": approval_id,
                    "run_id": run_id,
                    "prompt": question,
                    "answered": False,
                    "approved": False,
                    "event": event,
                    "created_at": int(time.time()),
                }
                run_snapshot = None
                with state_lock:
                    approvals[approval_id] = pending
                    self._append_run_event_locked(run_id, "approval.request", {
                        "approval_id": approval_id,
                        "prompt": question,
                        "status": "pending",
                    })
                    rec = active_runs.get(run_id)
                    run_snapshot = dict(rec) if rec is not None else None
                if run_snapshot is not None:
                    _persist_api_run_record(run_snapshot, title=title, prompt=prompt)
                timeout = float(config.get("server.approval_timeout_seconds", 3600) or 3600)
                answered = event.wait(max(0.1, timeout))
                with state_lock:
                    pending_state = approvals.get(approval_id)
                    if pending_state is None:
                        return False
                    if not answered and not pending_state.get("answered"):
                        pending_state["approved"] = False
                        pending_state["choice"] = "timeout"
                        pending_state["answered"] = True
                        self._append_run_event_locked(run_id, "approval.responded", {
                            "approval_id": approval_id,
                            "approved": False,
                            "choice": "timeout",
                            "timeout": True,
                            "prompt": question,
                        })
                        rec = active_runs.get(run_id)
                        run_snapshot = dict(rec) if rec is not None else None
                    else:
                        run_snapshot = None
                    approved = bool(pending_state.get("approved"))
                    choice = str(pending_state.get("choice") or ("once" if approved else "deny"))
                if run_snapshot is not None:
                    _persist_api_run_record(run_snapshot, title=title, prompt=prompt)
                if approved and choice in {"session", "always"}:
                    approval_grants[question] = choice
                    return "always"
                return approved

            def worker() -> None:
                try:
                    agent = runner.make_agent(
                        session=session,
                        model=body.get("model"),
                        provider_name=body.get("provider"),
                        cwd=body.get("cwd"),
                        approver=approver,
                    )
                    stop_before_prompt = False
                    with state_lock:
                        rec = self._set_run_state_locked(run_id, status="running", agent=agent, last_event="run.running")
                        if rec is not None and rec.get("cancel_requested"):
                            reason = str(rec.get("cancel_reason") or "stop requested")
                            rec = self._request_stop_run_locked(run_id, reason)
                            if rec is not None:
                                rec = self._set_run_state_locked(
                                    run_id,
                                    status="cancelled",
                                    result="",
                                    error="",
                                    last_event="run.cancelled",
                                )
                                self._append_run_event_locked(run_id, "run.cancelled", {
                                    "status": "cancelled",
                                    "session_id": session.id,
                                    "reason": reason,
                                })
                            stop_before_prompt = True
                        elif rec is not None:
                            self._append_run_event_locked(run_id, "run.running", {
                                "status": "running",
                                "session_id": session.id,
                            })
                        run_snapshot = dict(rec) if rec is not None else None
                    if run_snapshot is not None:
                        _persist_api_run_record(run_snapshot, title=title, prompt=prompt)
                    if stop_before_prompt:
                        return

                    def emit(ev: dict[str, Any]) -> None:
                        with state_lock:
                            rec = active_runs.get(run_id)
                            if rec is not None:
                                event_type = str(ev.get("type") or ev.get("event") or "event")
                                payload = dict(ev)
                                meta = _event_metadata(ev)
                                if meta:
                                    payload.setdefault("metadata", meta)
                                self._append_run_event_locked(run_id, event_type, payload)

                    result = runner.run_prompt(
                        prompt,
                        session=session,
                        agent=agent,
                        surface="serve",
                        meta=run_meta,
                        stream=_coerce_request_bool(body.get("stream"), False),
                        on_event=emit,
                    )
                    with state_lock:
                        rec = active_runs.get(run_id)
                        if rec is not None:
                            status = "cancelled" if rec.get("cancel_requested") else "completed"
                            rec = self._set_run_state_locked(run_id, **{
                                "status": status,
                                "result": result.text,
                                "trace_id": result.trace_id,
                                "surface_run_id": result.run_id,
                                "session_id": result.session.id,
                                "last_event": "run.cancelled" if status == "cancelled" else "run.completed",
                            })
                            if rec is not None:
                                self._append_run_event_locked(run_id, "run.cancelled" if status == "cancelled" else "run.completed", {
                                    "status": status,
                                    "session_id": result.session.id,
                                    "trace_id": result.trace_id,
                                    "surface_run_id": result.run_id,
                                })
                            run_snapshot = dict(rec) if rec is not None else None
                        else:
                            run_snapshot = None
                    if run_snapshot is not None:
                        _persist_api_run_record(run_snapshot, title=title, prompt=prompt)
                except Exception as exc:  # noqa: BLE001
                    with state_lock:
                        rec = active_runs.get(run_id)
                        if rec is not None:
                            status = "cancelled" if rec.get("cancel_requested") else "error"
                            rec = self._set_run_state_locked(
                                run_id,
                                status=status,
                                error=f"{type(exc).__name__}: {exc}" if status == "error" else "",
                                last_event="run.cancelled" if status == "cancelled" else "run.error",
                            )
                            self._append_run_event_locked(run_id, "run.cancelled" if status == "cancelled" else "run.error", {
                                "status": status,
                                "error": f"{type(exc).__name__}: {exc}" if status == "error" else "",
                            })
                            run_snapshot = dict(rec) if rec is not None else None
                        else:
                            run_snapshot = None
                    if run_snapshot is not None:
                        _persist_api_run_record(run_snapshot, title=title, prompt=prompt)

            thread = threading.Thread(target=worker, daemon=True, name=f"aegis-api-run-{run_id}")
            with state_lock:
                active_runs[run_id]["thread"] = thread
            thread.start()
            public = _public_run_record(record)
            public["status"] = "started"
            return self._json(202, public, self._session_headers(
                session_id=session.id,
                session_key=session_key,
            ))

        def _stop_run(self, run_id: str) -> None:
            with state_lock:
                self._sweep_runs_locked()
                rec = self._request_stop_run_locked(run_id, "API stop requested")
                run_snapshot = dict(rec) if rec is not None else None
            if run_snapshot is None:
                from .runs import RunStore

                stored = RunStore().get(run_id)
                if stored is None:
                    return self._json(404, self._run_not_found_payload(run_id))
                public = _public_stored_run_record(stored)
                status = str(public.get("status") or "unknown")
                return self._json(409, {
                    "ok": False,
                    "error": {
                        "message": f"Run is not active: {run_id}",
                        "code": "run_not_active",
                    },
                    "id": run_id,
                    "run_id": run_id,
                    "status": status,
                    "run": public,
                })
            _persist_api_run_record(run_snapshot)
            public = _public_run_record(run_snapshot)
            public["status"] = "stopping"
            return self._json(200, {"ok": True, **public, "run": public})

        def _post_approval(self, run_id: str, body: dict[str, Any]) -> None:
            approval_id = str(body.get("approval_id") or body.get("id") or "")
            resolve_all = _coerce_request_bool(body.get("resolve_all", body.get("all")), False)
            raw_choice = str(body.get("choice") or "").strip().lower()
            aliases = {"approve": "once", "approved": "once", "allow": "once", "yes": "once", "true": "once"}
            choice = aliases.get(raw_choice, raw_choice)
            if choice:
                if choice not in {"once", "session", "always", "deny"}:
                    return self._json(400, {
                        "error": "Invalid approval choice; expected one of: once, session, always, deny",
                        "code": "invalid_approval_choice",
                    })
                approved = choice != "deny"
            else:
                approved = _coerce_request_bool(body.get("approved", body.get("approve")), False)
                choice = "once" if approved else "deny"
            with state_lock:
                if resolve_all:
                    pending_items = [
                        item for item in approvals.values()
                        if item.get("run_id") == run_id and not item.get("answered")
                    ]
                elif approval_id:
                    pending = approvals.get(approval_id)
                    pending_items = [
                        pending
                    ] if (
                        pending is not None
                        and pending.get("run_id") == run_id
                        and not pending.get("answered")
                    ) else []
                else:
                    pending = next((v for v in approvals.values()
                                    if v.get("run_id") == run_id and not v.get("answered")), None)
                    pending_items = [pending] if pending is not None else []
                if pending_items:
                    for pending in pending_items:
                        pending["approved"] = approved
                        pending["choice"] = choice
                        pending["answered"] = True
                        self._append_run_event_locked(run_id, "approval.responded", {
                            "approval_id": pending["id"],
                            "approved": approved,
                            "choice": choice,
                            "prompt": pending.get("prompt", ""),
                        })
                    rec = active_runs.get(run_id)
                    run_snapshot = dict(rec) if rec is not None else None
                    waiter_events = [pending.get("event") for pending in pending_items]
                else:
                    run_snapshot = None
                    waiter_events = []
            if not pending_items:
                if not self._run_exists(run_id):
                    return self._json(404, self._run_not_found_payload(run_id))
                return self._json(409, {
                    "ok": False,
                    "error": {
                        "message": f"Run has no pending approval: {run_id}",
                        "code": "approval_not_pending",
                    },
                    "run_id": run_id,
                })
            if run_snapshot is not None:
                _persist_api_run_record(run_snapshot)
            for event in waiter_events:
                if event is not None:
                    event.set()
            first_pending = pending_items[0]
            return self._json(200, {
                "ok": True,
                "object": "hermes.run.approval_response",
                "run_id": run_id,
                "approval_id": first_pending["id"],
                "approval_ids": [pending["id"] for pending in pending_items],
                "approved": approved,
                "choice": choice,
                "resolved": len(pending_items),
            })

    return Handler


def make_app(config: Config) -> web.Application:
    """Build the aiohttp OpenAI-compatible API adapter used by ``aegis serve``.

    ``make_handler`` remains for in-process tests and embedders; this adapter
    preserves the same route behavior by executing that handler behind aiohttp's
    transport instead of Python's stdlib HTTP server.
    """
    handler_cls = make_handler(config)

    class _AiohttpWFile:
        def __init__(self, loop: asyncio.AbstractEventLoop, chunks: asyncio.Queue[tuple[bytes, concurrent.futures.Future[int] | None]]):
            self._loop = loop
            self._chunks = chunks
            self._buffer = bytearray()
            self._closed = threading.Event()
            self._streaming = threading.Event()

        def write(self, data: bytes | bytearray | memoryview) -> int:
            if self._closed.is_set():
                raise BrokenPipeError("SSE client disconnected")
            payload = bytes(data)
            if not payload:
                return 0
            self._buffer.extend(payload)
            ack: concurrent.futures.Future[int] | None = None
            if self._streaming.is_set():
                ack = concurrent.futures.Future()
            self._loop.call_soon_threadsafe(self._chunks.put_nowait, (payload, ack))
            if ack is not None:
                return ack.result()
            return len(payload)

        def flush(self) -> None:
            return None

        def getvalue(self) -> bytes:
            return bytes(self._buffer)

        def set_streaming(self) -> None:
            self._streaming.set()

        def close(self) -> None:
            self._closed.set()

        @property
        def closed(self) -> bool:
            return self._closed.is_set()

    async def dispatch(request: web.Request) -> web.StreamResponse:
        origin = str(request.headers.get("Origin", "") or "")
        if request.method.upper() == "OPTIONS":
            cors = _cors_headers(config, origin)
            if cors is None:
                return web.Response(status=403, headers=_security_headers())
            headers = _security_headers()
            if cors:
                headers.update(cors)
            return web.Response(status=204, headers=headers)
        if not _origin_allowed(config, origin):
            return web.json_response(
                {"error": "cors origin not allowed"},
                status=403,
                headers=_security_headers(),
            )
        try:
            body = await request.read()
        except web.HTTPRequestEntityTooLarge:
            return web.json_response(
                {"error": "request body too large"},
                status=413,
                headers=_response_headers(config, origin),
            )
        loop = asyncio.get_running_loop()
        chunks: asyncio.Queue[tuple[bytes, concurrent.futures.Future[int] | None]] = asyncio.Queue()
        headers_ready = asyncio.Event()
        adapter = object.__new__(handler_cls)
        adapter.path = request.rel_url.raw_path_qs
        adapter.headers = request.headers
        adapter.rfile = BytesIO(body)
        adapter.wfile = _AiohttpWFile(loop, chunks)
        adapter._aegis_status = 200
        adapter._aegis_headers: list[tuple[str, str]] = []
        adapter._aegis_headers_sent = False
        adapter._aegis_on_disconnect = None
        disconnect_notified = False

        def notify_disconnect() -> None:
            nonlocal disconnect_notified
            if disconnect_notified:
                return
            disconnect_notified = True
            adapter.wfile.close()
            callback = getattr(adapter, "_aegis_on_disconnect", None)
            if callable(callback):
                try:
                    callback()
                except Exception:  # noqa: BLE001
                    pass

        def client_disconnected() -> bool:
            transport = request.transport
            return bool(transport is None or transport.is_closing())

        def send_response(code: int, message: str | None = None) -> None:  # noqa: ARG001
            adapter._aegis_status = int(code)

        def send_header(name: str, value: str) -> None:
            adapter._aegis_headers.append((str(name), str(value)))

        def end_headers() -> None:
            adapter._aegis_headers_sent = True
            loop.call_soon_threadsafe(headers_ready.set)
            return None

        adapter.send_response = send_response
        adapter.send_header = send_header
        adapter.end_headers = end_headers

        method = request.method.upper()
        func = getattr(adapter, f"do_{method}", None)
        if func is None:
            return web.json_response(
                {"error": "method not allowed"},
                status=405,
                headers=_response_headers(config, origin),
            )

        task = asyncio.create_task(asyncio.to_thread(func))
        header_wait = asyncio.create_task(headers_ready.wait())

        def consume_task_exception(done_task: asyncio.Task) -> None:
            try:
                done_task.exception()
            except BaseException:  # noqa: BLE001
                pass

        def task_exception() -> BaseException | None:
            try:
                return task.exception()
            except asyncio.CancelledError as exc:
                return exc

        def disconnected_response() -> web.Response:
            header_wait.cancel()
            task.add_done_callback(consume_task_exception)
            return web.Response(status=499, headers=_response_headers(config, origin))

        async def wait_for_task_or_headers() -> str:
            while True:
                if client_disconnected():
                    notify_disconnect()
                    return "disconnect"
                done, _pending = await asyncio.wait(
                    {task, header_wait},
                    timeout=0.1,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if task in done:
                    return "task"
                if header_wait in done:
                    return "headers"

        async def wait_for_task_or_disconnect() -> bool:
            while not task.done():
                if client_disconnected():
                    notify_disconnect()
                    return False
                done, _pending = await asyncio.wait({task}, timeout=0.1)
                if task in done:
                    return True
            return True

        first_done = await wait_for_task_or_headers()
        if first_done == "disconnect":
            return disconnected_response()
        error = task_exception() if task.done() else None
        if error is not None:
            header_wait.cancel()
            return web.json_response(
                {"error": f"{type(error).__name__}: {error}"},
                status=500,
                headers=_response_headers(config, origin),
            )
        if not adapter._aegis_headers_sent:
            if not await wait_for_task_or_disconnect():
                return disconnected_response()
            header_wait.cancel()
        headers = {name: value for name, value in adapter._aegis_headers}
        content_type = headers.get("Content-Type", headers.get("content-type", ""))
        if "text/event-stream" not in content_type.lower():
            if not await wait_for_task_or_disconnect():
                return disconnected_response()
            header_wait.cancel()
            error = task_exception()
            if error is not None:
                return web.json_response(
                    {"error": f"{type(error).__name__}: {error}"},
                    status=500,
                    headers=_response_headers(config, origin),
                )
            merged_headers = {**_response_headers(config, origin), **headers}
            return web.Response(status=adapter._aegis_status, headers=merged_headers, body=adapter.wfile.getvalue())

        response = web.StreamResponse(
            status=adapter._aegis_status,
            headers={**_response_headers(config, origin), **headers},
        )
        await response.prepare(request)
        adapter.wfile.set_streaming()
        try:
            while True:
                try:
                    chunk, ack = await asyncio.wait_for(chunks.get(), timeout=0.1)
                    try:
                        await response.write(chunk)
                    except (BrokenPipeError, ConnectionResetError, RuntimeError):
                        notify_disconnect()
                        if ack is not None and not ack.done():
                            ack.set_exception(BrokenPipeError("SSE client disconnected"))
                        break
                    else:
                        if ack is not None and not ack.done():
                            ack.set_result(len(chunk))
                except asyncio.TimeoutError:
                    if client_disconnected():
                        notify_disconnect()
                        break
                    if task.done():
                        break
            while not adapter.wfile.closed:
                try:
                    chunk, ack = chunks.get_nowait()
                except asyncio.QueueEmpty:
                    break
                try:
                    await response.write(chunk)
                except (BrokenPipeError, ConnectionResetError, RuntimeError):
                    notify_disconnect()
                    if ack is not None and not ack.done():
                        ack.set_exception(BrokenPipeError("SSE client disconnected"))
                    break
                else:
                    if ack is not None and not ack.done():
                        ack.set_result(len(chunk))
            if not adapter.wfile.closed and task.exception() is not None:
                payload = {"error": f"{type(task.exception()).__name__}: {task.exception()}"}
                await response.write(f"event: error\ndata: {json.dumps(payload)}\n\n".encode())
        finally:
            header_wait.cancel()
        if not adapter.wfile.closed:
            try:
                await response.write_eof()
            except (BrokenPipeError, ConnectionResetError, RuntimeError):
                notify_disconnect()
        return response

    app = web.Application(client_max_size=_MAX_BODY_BYTES)
    app.router.add_route("*", "/{tail:.*}", dispatch)
    return app


def serve(config: Config, host: str = "127.0.0.1", port: int = 8790) -> None:
    print(f"AEGIS OpenAI-compatible aiohttp API on http://{host}:{port}/v1  (Ctrl+C to stop)")
    try:
        web.run_app(make_app(config), host=host, port=port, print=None)
    except KeyboardInterrupt:
        print("\nserver stopped.")
