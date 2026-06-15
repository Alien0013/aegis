"""Daytona execution environment placeholder.

AEGIS exposes the named environment seam, but does not yet ship
Daytona SDK wiring. Selecting it fails closed in the backend dispatcher.
"""

from __future__ import annotations


class DaytonaEnvironment:
    def __init__(self, *args, **kwargs) -> None:
        raise RuntimeError("daytona backend is not configured in AEGIS")
