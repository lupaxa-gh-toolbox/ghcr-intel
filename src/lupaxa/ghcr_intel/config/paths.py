"""
Resolve configuration and cache directories.

macOS and Linux use the XDG paths (``~/.config`` and ``~/.local/share``).
Windows uses platformdirs. ``GHCR_INTEL_CONFIG``, ``GHCR_INTEL_CONFIG_DIR``,
and ``GHCR_INTEL_DATA_DIR`` override those locations. ``GITHUB_INTEL_CONFIG``
and ``GITHUB_INTEL_CONFIG_DIR`` are not read.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys

from platformdirs import PlatformDirs
from platformdirs.unix import Unix

__all__ = [
    "config_directory",
    "config_file",
    "data_directory",
]

_APP_NAME = "ghcr-intel"


def config_directory() -> Path:
    """
    Return the directory that holds config.yml.

    ``GHCR_INTEL_CONFIG_DIR`` overrides the platform default.

    Returns:
        Configuration directory. The directory is not created.

    """

    override = _environment("GHCR_INTEL_CONFIG_DIR")
    if override is not None:
        return Path(override).expanduser()
    return Path(_dirs().user_config_dir)


def data_directory() -> Path:
    """
    Return the directory that holds the SQLite cache.

    ``GHCR_INTEL_DATA_DIR`` overrides the platform default.

    Returns:
        Data directory. The directory is not created.

    """

    override = _environment("GHCR_INTEL_DATA_DIR")
    if override is not None:
        return Path(override).expanduser()
    return Path(_dirs().user_data_dir)


def config_file() -> Path:
    """
    Return the main configuration file.

    ``GHCR_INTEL_CONFIG`` selects a file directly. Otherwise the path is
    ``config.yml`` inside :func:`config_directory`.

    Returns:
        Path to the main configuration file. The file is not created.

    """

    override = _environment("GHCR_INTEL_CONFIG")
    if override is not None:
        return Path(override).expanduser()
    return config_directory() / "config.yml"


def _environment(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return None
    return value


def _dirs() -> PlatformDirs | Unix:
    if sys.platform == "win32":
        return PlatformDirs(appname=_APP_NAME, appauthor=False)
    return Unix(appname=_APP_NAME, appauthor=False)
