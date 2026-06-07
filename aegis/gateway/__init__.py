"""Multi-channel gateway: one agent engine serving many platforms."""

from .base import BasePlatformAdapter, MessageEvent
from .runner import GatewayRunner

__all__ = ["BasePlatformAdapter", "MessageEvent", "GatewayRunner"]
