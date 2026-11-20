"""Colour one catalogue cell for the list, the detail, and the terminal."""

from __future__ import annotations

from rich.text import Text

__all__ = [
    "paint",
]

_VISIBILITY = {
    "public": "green",
    "private": "magenta",
    "internal": "cyan",
}
_STATUS = {
    "current": "green",
    "stale": "yellow",
    "forbidden": "yellow",
    "error": "red",
    "unavailable": "red",
    "not synchronised": "dim",
}


def paint(label: str, value: str, *, status: str, metadata_error: str) -> Text:
    """
    Colour one cell.

    Status uses the short word in ``status``, including when ``value`` is the
    detail sentence. ``SKIPPED`` is dim. Created, Updated, Synchronised,
    Newest tag, Digest, Architectures, and Linked repository stay plain.

    """

    style = _style(label, value, status=status, metadata_error=metadata_error)
    if style == "":
        return Text(value)
    return Text(value, style=style)


def _style(label: str, value: str, *, status: str, metadata_error: str) -> str:
    if value.startswith("SKIPPED"):
        return "dim"
    if label == "Metadata error" or _failed_package(label, metadata_error):
        return "red"
    if label == "Visibility":
        return _VISIBILITY.get(value.strip().casefold(), "")
    if label == "Status":
        return _STATUS.get(status.strip().casefold(), "")
    if label == "Enabled":
        return _choice(value, yes="green", no="dim")
    if label == "Cache":
        return _cache(value)
    if label == "Stale":
        return _choice(value, yes="yellow", no="green")
    return ""


def _failed_package(label: str, metadata_error: str) -> bool:
    return label == "Package" and metadata_error != ""


def _choice(value: str, *, yes: str, no: str) -> str:
    folded = value.strip().casefold()
    if folded == "yes":
        return yes
    if folded == "no":
        return no
    return ""


def _cache(value: str) -> str:
    folded = value.casefold()
    if "error" in folded or "unavailable" in folded:
        return "red"
    if "stale" in folded:
        return "yellow"
    if "cached" in folded:
        return "green"
    return ""
