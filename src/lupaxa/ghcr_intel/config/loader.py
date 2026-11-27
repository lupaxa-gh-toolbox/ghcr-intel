"""Load the three YAML files and flatten owners into package records."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

from ruamel.yaml import YAML
from ruamel.yaml.constructor import DuplicateKeyError
from ruamel.yaml.error import YAMLError

from lupaxa.ghcr_intel.config.models import AppConfig, OwnerSpec, PackageEntry, PackageFile, PackageSpec, ProfilesFile, ProfileSpec
from lupaxa.ghcr_intel.config.profiles import ConfigProfiles
from lupaxa.ghcr_intel.constants import UNGROUPED
from lupaxa.ghcr_intel.errors import ConfigError

__all__ = [
    "LoadedConfig",
    "load_config",
]


@dataclass(frozen=True)
class LoadedConfig:
    """A validated configuration and the package records it names."""

    path: Path
    config: AppConfig
    config_profiles: ConfigProfiles
    packages: tuple[PackageSpec, ...]
    profiles_path: Path
    packages_path: Path


def load_config(path: Path) -> LoadedConfig:
    """
    Load the main file plus the profile and package files it names.

    Unknown keys and invalid types propagate as ``ValidationError``. A missing
    file, unreadable file, or invalid YAML raises ``ConfigError``.

    Args:
        path: Main configuration file.

    Returns:
        Validated configuration and the flattened package list.

    Raises:
        ConfigError: A file is missing, unreadable, or not a YAML object.
        ValidationError: A document contains an unknown key or an invalid type.

    """

    config_path = path.expanduser()
    config = AppConfig.model_validate(_read_document(config_path))
    root = config_path.parent
    profiles_path = _resolve_file(root, config.files.profiles)
    packages_path = _resolve_file(root, config.files.packages)
    profiles = ProfilesFile.model_validate(_read_document(profiles_path)).profiles
    packages = _load_packages(packages_path, config.defaults.profile)
    _require_known_profiles(profiles, packages, config.defaults.profile)
    return LoadedConfig(
        path=config_path, config=config, config_profiles=ConfigProfiles(profiles=profiles, defaults=config.defaults), packages=packages, profiles_path=profiles_path, packages_path=packages_path
    )


def _resolve_file(root: Path, reference: str) -> Path:
    candidate = Path(reference).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    return candidate


def _read_document(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise ConfigError(f"Configuration file not found: {path}")
    suffix = path.suffix.casefold()
    if suffix not in {".yml", ".yaml"}:
        raise ConfigError(f"Unsupported configuration file: {path.name}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"Cannot read {path}: {exc}") from exc
    return _read_yaml(path, text)


def _read_yaml(path: Path, text: str) -> dict[str, object]:
    yaml = YAML(typ="safe")
    yaml.allow_duplicate_keys = False
    try:
        loaded = yaml.load(text)
    except DuplicateKeyError as exc:
        raise ConfigError(f"Duplicate key in {path}: {exc}") from exc
    except YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ConfigError(f"{path} must contain an object.")
    mapped: dict[str, object] = {}
    for key, value in cast("dict[object, object]", loaded).items():
        mapped[str(key)] = value
    return mapped


def _load_packages(path: Path, default_profile: str) -> tuple[PackageSpec, ...]:
    document = PackageFile.model_validate(_read_document(path))
    packages: list[PackageSpec] = []
    seen: dict[str, str] = {}
    owners: set[str] = set()
    for index, owner in enumerate(document.owners):
        _one_owner(owner, index, path, default_profile, packages, seen, owners)
    return tuple(packages)


def _one_owner(owner: OwnerSpec, index: int, path: Path, default_profile: str, packages: list[PackageSpec], seen: dict[str, str], owners: set[str]) -> None:
    if owner.name.casefold() in owners:
        raise ConfigError(f"Duplicate owner {owner.name}.")
    owners.add(owner.name.casefold())
    for package_index, entry in enumerate(owner.packages):
        label = f"{path} owners[{index}].packages[{package_index}]"
        spec = _one_package(entry, owner, default_profile, label)
        key = f"{spec.owner.casefold()}/{spec.name.casefold()}"
        if key in seen:
            raise ConfigError(f"Duplicate package {spec.owner}/{spec.name} (already listed as {seen[key]}).")
        seen[key] = f"{spec.owner}/{spec.name}"
        packages.append(spec)


def _one_package(entry: str | PackageEntry, owner: OwnerSpec, default_profile: str, label: str) -> PackageSpec:
    if isinstance(entry, str):
        name = entry
        group = owner.group
        profile = owner.profile
        tags: list[str] = []
        checks: dict[str, bool] = {}
        enabled = True
    else:
        name = entry.name
        group = owner.group if entry.group is None else entry.group
        profile = owner.profile if entry.profile is None else entry.profile
        tags = list(entry.tags)
        checks = dict(entry.checks)
        enabled = True if entry.enabled is None else entry.enabled
    try:
        return PackageSpec(owner=owner.name, name=name, group=group or UNGROUPED, profile=profile or default_profile, enabled=enabled, tags=tags, checks=checks)
    except ConfigError as exc:
        raise ConfigError(f"{label}: {exc}") from exc


def _require_known_profiles(profiles: dict[str, ProfileSpec], packages: tuple[PackageSpec, ...], default_profile: str) -> None:
    if default_profile not in profiles:
        raise ConfigError(f"defaults.profile references unknown profile {default_profile}.")
    for package in packages:
        if package.profile not in profiles:
            raise ConfigError(f"{package.owner}/{package.name} references unknown profile {package.profile}.")
