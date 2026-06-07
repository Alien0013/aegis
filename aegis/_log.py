"""Lightweight rotating logger so swallowed exceptions are recorded, not lost."""

from __future__ import annotations

import logging
import logging.handlers

_logger: logging.Logger | None = None


def logger() -> logging.Logger:
    global _logger
    if _logger is None:
        from . import config as cfg
        lg = logging.getLogger("aegis")
        lg.setLevel(logging.INFO)
        lg.propagate = False
        if not lg.handlers:
            try:
                fh = logging.handlers.RotatingFileHandler(
                    cfg.logs_dir() / "aegis.log", maxBytes=2_000_000, backupCount=3)
                fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
                lg.addHandler(fh)
            except Exception:  # noqa: BLE001
                lg.addHandler(logging.NullHandler())
        _logger = lg
    return _logger


def log_exc(context: str) -> None:
    """Record the current exception (call inside an except block)."""
    try:
        logger().exception(context)
    except Exception:  # noqa: BLE001
        pass


def info(msg: str) -> None:
    try:
        logger().info(msg)
    except Exception:  # noqa: BLE001
        pass
