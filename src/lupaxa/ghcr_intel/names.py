"""Image identity. The GitHub API and the registry encode names differently."""

from __future__ import annotations

from urllib.parse import quote

from lupaxa.ghcr_intel.errors import ConfigError


def parse_image(value: str) -> tuple[str, str]:
    """Return ``(owner, name)``. ``name`` may contain slashes."""

    owner, separator, name = value.strip().partition("/")
    if separator == "" or owner == "" or name == "" or "/" in owner:
        raise ConfigError("An image is owner/name. A slash after the owner is part of the name.")
    return owner, name


def api_package_segment(name: str) -> str:
    """Encode a package name as one GitHub API path segment."""

    return quote(name, safe="")


def registry_name(owner: str, name: str) -> str:
    """Return the OCI repository path. Slashes in ``name`` stay slashes."""

    return f"{owner}/{name}"
