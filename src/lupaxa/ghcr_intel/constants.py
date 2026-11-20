"""Stable names shared by configuration."""

from __future__ import annotations

from typing import Final

__all__ = [
    "CHECK_NAMES",
    "DEFAULT_PROFILE",
    "UNGROUPED",
]

CHECK_NAMES: Final[tuple[str, ...]] = (
    "metadata",
    "tags",
    "digests",
    "architectures",
    "stale",
)

DEFAULT_PROFILE: Final[str] = "standard"
UNGROUPED: Final[str] = "ungrouped"
