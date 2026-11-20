"""Catalogue labels and named list and detail layouts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from lupaxa.ghcr_intel.config.models import DisplaySettings
from lupaxa.ghcr_intel.errors import ConfigError

__all__ = [
    "CATALOGUE",
    "Layout",
    "RIGHT_ALIGNED",
    "hardcoded_list",
    "resolve_layout",
]

CATALOGUE: Final[tuple[str, ...]] = (
    "Organisation",
    "Package",
    "Visibility",
    "Description",
    "Linked repository",
    "Group",
    "Profile",
    "Tags",
    "Enabled",
    "Status",
    "Cache",
    "Created",
    "Updated",
    "Synchronised",
    "Newest tag",
    "Image tags",
    "Tag count",
    "Versions fetched",
    "Versions truncated",
    "Digest",
    "Architectures",
    "Stale",
)

RIGHT_ALIGNED: Final[frozenset[str]] = frozenset(
    {
        "Tag count",
        "Versions fetched",
    },
)


@dataclass(frozen=True)
class Layout:
    """Resolved list and detail columns for one view name."""

    list_fields: tuple[str, ...]
    detail_fields: tuple[str, ...] | None


def hardcoded_list() -> tuple[str, ...]:
    """Return the seven default list columns."""

    return (
        "Organisation",
        "Package",
        "Visibility",
        "Status",
        "Updated",
        "Newest tag",
        "Linked repository",
    )


def resolve_layout(display: DisplaySettings, name: str) -> Layout:
    """
    Resolve one named view into list and detail columns.

    ``default`` always exists. A saved entry replaces only the sides it sets.
    Unknown names, unknown labels, and duplicates raise ``ConfigError``.

    """

    if name != "default" and name not in display.views:
        message = f"unknown display view {name!r}."
        raise ConfigError(message)
    entry = display.views.get(name)
    if entry is None:
        return Layout(hardcoded_list(), None)
    list_fields = (
        hardcoded_list() if entry.list is None else _catalogue_labels(entry.list)
    )
    detail_fields = None if entry.detail is None else _catalogue_labels(entry.detail)
    return Layout(list_fields, detail_fields)


def _catalogue_labels(values: tuple[str, ...]) -> tuple[str, ...]:
    folded = {label.casefold(): label for label in CATALOGUE}
    seen: set[str] = set()
    chosen: list[str] = []
    for raw in values:
        label = folded.get(raw.strip().casefold())
        if label is None:
            message = f"unknown display field {raw!r}."
            raise ConfigError(message)
        if label in seen:
            message = f"duplicate display field {label!r}."
            raise ConfigError(message)
        seen.add(label)
        chosen.append(label)
    return tuple(chosen)
