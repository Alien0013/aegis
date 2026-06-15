"""Central redacted rotating logging for AEGIS."""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

_initialized = False
_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_NOISY_LOGGERS = ("httpx", "httpcore", "urllib3", "asyncio", "uvicorn.access")
COMPONENT_PREFIXES = {
    "gateway": ("aegis.gateway",),
    "gui": ("aegis.dashboard", "aegis.dashboard_fastapi", "uvicorn"),
}


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        try:
            from .redact import redact_secrets

            return redact_secrets(rendered)
        except Exception:  # noqa: BLE001
            return rendered


class ComponentFilter(logging.Filter):
    def __init__(self, prefixes: tuple[str, ...]):
        super().__init__()
        self.prefixes = prefixes

    def filter(self, record: logging.LogRecord) -> bool:
        return record.name.startswith(self.prefixes)


def _add_handler(
    root: logging.Logger,
    path: Path,
    *,
    level: int,
    max_bytes: int,
    backup_count: int,
    log_filter: logging.Filter | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    resolved = str(path.resolve())
    for handler in root.handlers:
        if isinstance(handler, logging.handlers.RotatingFileHandler):
            if str(Path(handler.baseFilename).resolve()) == resolved:
                if log_filter and not any(isinstance(f, type(log_filter)) for f in handler.filters):
                    handler.addFilter(log_filter)
                return
    handler = logging.handlers.RotatingFileHandler(
        path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.setLevel(level)
    handler.setFormatter(RedactingFormatter(_LOG_FORMAT))
    if log_filter:
        handler.addFilter(log_filter)
    root.addHandler(handler)


def setup_logging(*, mode: str | None = None, force: bool = False) -> Path:
    """Configure log files. Safe to call repeatedly."""
    global _initialized
    from . import config as cfg

    log_dir = cfg.logs_dir()
    if force:
        root = logging.getLogger()
        for handler in list(root.handlers):
            if isinstance(handler, logging.handlers.RotatingFileHandler):
                root.removeHandler(handler)
                handler.close()
        _initialized = False

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    _add_handler(root, log_dir / "agent.log", level=logging.INFO,
                 max_bytes=5 * 1024 * 1024, backup_count=3)
    _add_handler(root, log_dir / "aegis.log", level=logging.INFO,
                 max_bytes=2 * 1024 * 1024, backup_count=3)
    _add_handler(root, log_dir / "errors.log", level=logging.WARNING,
                 max_bytes=2 * 1024 * 1024, backup_count=2)

    if mode == "gateway":
        _add_handler(
            root,
            log_dir / "gateway.log",
            level=logging.INFO,
            max_bytes=5 * 1024 * 1024,
            backup_count=3,
            log_filter=ComponentFilter(COMPONENT_PREFIXES["gateway"]),
        )
    if mode == "gui":
        _add_handler(
            root,
            log_dir / "gui.log",
            level=logging.INFO,
            max_bytes=10 * 1024 * 1024,
            backup_count=5,
            log_filter=ComponentFilter(COMPONENT_PREFIXES["gui"]),
        )

    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    _initialized = True
    return log_dir


def logger(name: str = "aegis") -> logging.Logger:
    if not _initialized:
        setup_logging()
    return logging.getLogger(name)


def log_exc(context: str) -> None:
    try:
        logger().exception(context)
    except Exception:  # noqa: BLE001
        pass


def info(msg: str) -> None:
    try:
        logger().info(msg)
    except Exception:  # noqa: BLE001
        pass
