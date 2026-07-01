"""Runtime/provider readiness checks for the agent loop."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _scalar(value: Any) -> str:
    if value is None:
        return ""
    raw = getattr(value, "value", value)
    return str(raw or "")


def _provider_name(provider: Any) -> str:
    return str(getattr(provider, "name", "") or "").strip()


def _model_id(provider: Any) -> str:
    return str(getattr(provider, "model", "") or "").strip()


def _auth_required(provider: Any, config: Any = None) -> bool | None:
    explicit = getattr(provider, "requires_auth", None)
    if isinstance(explicit, bool):
        return explicit

    name = _provider_name(provider)
    if not name:
        return None
    try:
        from ..providers.registry import _specs_for

        spec = _specs_for(config).get(name)
        if spec is not None:
            return str(getattr(spec, "auth_scheme", "") or "").lower() != "none"
    except Exception:  # noqa: BLE001
        return None
    return None


def _safe_describe_auth(auth: Any) -> str:
    describe = getattr(auth, "describe", None)
    if not callable(describe):
        return type(auth).__name__ if auth is not None else ""
    try:
        return str(describe() or "")
    except Exception as exc:  # noqa: BLE001
        return f"{type(auth).__name__}.describe failed: {exc}"


@dataclass(frozen=True)
class ProviderReadiness:
    ok: bool
    provider_present: bool
    provider_name: str
    model: str
    api_mode: str
    complete_callable: bool
    auth_required: bool | None
    auth_present: bool
    auth_state: str
    auth_description: str
    missing: tuple[str, ...]
    message: str

    def to_meta(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "provider_present": self.provider_present,
            "provider": self.provider_name,
            "model": self.model,
            "api_mode": self.api_mode,
            "complete_callable": self.complete_callable,
            "auth_required": self.auth_required,
            "auth_present": self.auth_present,
            "auth_state": self.auth_state,
            "auth_description": self.auth_description,
            "missing": list(self.missing),
            "message": self.message,
        }

    def to_event(
        self,
        *,
        api_request_id: str = "",
        turn_id: str = "",
        grace: bool = False,
    ) -> dict[str, Any]:
        event = {
            "type": "provider_readiness",
            **self.to_meta(),
            "status": "ok" if self.ok else "error",
        }
        if api_request_id:
            event["api_request_id"] = api_request_id
        if turn_id:
            event["turn_id"] = turn_id
        if grace:
            event["grace"] = True
        return event


def check_provider_readiness(provider: Any, *, config: Any = None) -> ProviderReadiness:
    provider_present = provider is not None
    provider_name = _provider_name(provider) if provider_present else ""
    model = _model_id(provider) if provider_present else ""
    api_mode = _scalar(getattr(provider, "api_mode", "")) if provider_present else ""
    complete = getattr(provider, "complete", None) if provider_present else None
    complete_callable = callable(complete)
    auth_required = _auth_required(provider, config) if provider_present else None
    auth = getattr(provider, "auth", None) if provider_present else None
    auth_present = auth is not None
    auth_description = _safe_describe_auth(auth) if auth_present else ""
    auth_state = "unknown"
    missing: list[str] = []

    if not provider_present:
        missing.append("provider")
    if provider_present and not model:
        missing.append("model")
    if provider_present and not complete_callable:
        missing.append("complete")

    if provider_present:
        if auth_present:
            available = getattr(auth, "available", None)
            if not callable(available):
                auth_state = "unknown"
                missing.append("auth_available")
            else:
                try:
                    if bool(available()):
                        auth_state = "ready"
                    else:
                        auth_state = "missing"
                        missing.append("auth")
                except Exception as exc:  # noqa: BLE001
                    auth_state = "error"
                    auth_description = auth_description or type(auth).__name__
                    missing.append("auth")
                    auth_description = f"{auth_description}; available failed: {exc}"
        elif auth_required is True:
            auth_state = "missing"
            missing.append("auth")
        elif auth_required is False:
            auth_state = "not_required"

    ok = not missing
    return ProviderReadiness(
        ok=ok,
        provider_present=provider_present,
        provider_name=provider_name,
        model=model,
        api_mode=api_mode,
        complete_callable=complete_callable,
        auth_required=auth_required,
        auth_present=auth_present,
        auth_state=auth_state,
        auth_description=auth_description,
        missing=tuple(dict.fromkeys(missing)),
        message="" if ok else _readiness_message(
            provider_name=provider_name,
            model=model,
            auth_description=auth_description,
            missing=tuple(dict.fromkeys(missing)),
        ),
    )


def _readiness_message(
    *,
    provider_name: str,
    model: str,
    auth_description: str,
    missing: tuple[str, ...],
) -> str:
    target = provider_name or "unknown provider"
    if model:
        target = f"{target}/{model}"
    parts = []
    if "provider" in missing:
        parts.append("no provider is configured")
    if "model" in missing:
        parts.append("no model id is configured")
    if "complete" in missing:
        parts.append("provider.complete is not callable")
    if "auth_available" in missing:
        parts.append("auth object cannot report availability")
    if "auth" in missing:
        detail = f" ({auth_description})" if auth_description else ""
        parts.append(f"auth is unavailable{detail}")
    summary = "; ".join(parts) or "provider runtime is incomplete"
    return (
        f"Provider runtime is not ready for {target}: {summary}. "
        "Run `aegis auth status` and configure credentials, or switch provider/model."
    )


def record_provider_readiness(
    session: Any,
    readiness: ProviderReadiness,
    *,
    api_request_id: str = "",
    turn_id: str = "",
    grace: bool = False,
) -> None:
    if session is None:
        return
    meta = readiness.to_meta()
    if api_request_id:
        meta["api_request_id"] = api_request_id
    if turn_id:
        meta["turn_id"] = turn_id
    if grace:
        meta["grace"] = True
    try:
        session.meta["provider_readiness"] = meta
        runtime = dict(session.meta.get("runtime") or {})
        runtime["provider_readiness"] = meta
        session.meta["runtime"] = runtime
    except Exception:  # noqa: BLE001
        pass
