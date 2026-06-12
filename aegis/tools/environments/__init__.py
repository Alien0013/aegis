"""Execution environments used by terminal backends."""

from .base import BaseEnvironment
from .local import LocalEnvironment

__all__ = ["BaseEnvironment", "LocalEnvironment"]
