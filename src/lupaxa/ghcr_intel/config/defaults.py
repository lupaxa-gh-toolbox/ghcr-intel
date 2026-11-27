"""The three configuration files shipped with a new install."""

from __future__ import annotations

from pathlib import Path

from lupaxa.ghcr_intel.errors import ConfigError

__all__ = [
    "generate_default_files",
]

_CONFIG = """\
version: 1

github:
  api_url: https://api.github.com
  registry_url: https://ghcr.io

files:
  profiles: profiles.yml
  packages: packages.yml

defaults:
  profile: standard
  metadata: true
  tags: true
  digests: false
  architectures: false
  stale: false

display:
  date_format: DD/MM/YYYY HH:mm UTC
  view: default
  views:
    tags:
      list:
        - Organisation
        - Package
        - Newest tag
        - Updated

sync:
  concurrency: 10
  timeout: 15
  retries: 3
  interval: 0
  version_limit: 100
  cache:
    enabled: true
    metadata_ttl: 3600
    versions_ttl: 1800
    manifests_ttl: 21600

checks:
  stale:
    package_days: 180
    tag_days: 90

logging:
  level: INFO
"""

_PROFILES = """\
profiles:
  base:
    metadata: true
    tags: false
    digests: false
    architectures: false
    stale: false

  standard:
    extends: base
    tags: true

  detailed:
    extends: standard
    digests: true
    architectures: true
    stale: true

  watchlist:
    extends: standard
    digests: true
    stale: true
"""

_PACKAGES = "owners: []\n"


def generate_default_files(paths: tuple[Path, Path, Path], *, force: bool) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    """
    Write the shipped config, profiles, and packages files.

    An existing file is left unchanged unless ``force`` is true. A directory
    created for these files is mode ``0o700``. Each file written is mode
    ``0o600``.

    Args:
        paths: ``config.yml``, ``profiles.yml``, and ``packages.yml``, in
            that order.
        force: Replace files that already exist.

    Returns:
        The paths written, then the paths left unchanged.

    Raises:
        ConfigError: A directory or file cannot be created.

    """

    written: list[Path] = []
    kept: list[Path] = []
    for path, text in zip(paths, (_CONFIG, _PROFILES, _PACKAGES), strict=True):
        _ensure_directory(path.parent)
        if path.exists() and not force:
            kept.append(path)
            continue
        _write(path, text)
        written.append(path)
    return tuple(written), tuple(kept)


def _ensure_directory(directory: Path) -> None:
    created = not directory.exists()
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigError(f"Cannot create {directory}: {exc}") from exc
    if created:
        _restrict(directory, 0o700)


def _write(path: Path, text: str) -> None:
    try:
        path.write_text(text, encoding="utf-8", newline="\n")
    except OSError as exc:
        raise ConfigError(f"Cannot write {path}: {exc}") from exc
    _restrict(path, 0o600)


def _restrict(path: Path, mode: int) -> None:
    try:
        path.chmod(mode)
    except OSError as exc:
        raise ConfigError(f"Cannot set permissions on {path}: {exc}") from exc
