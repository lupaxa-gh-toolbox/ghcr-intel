"""
Single-parent profile inheritance.

Resolution does not depend on the order of profiles in the file.
An explicit false overrides an inherited true.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from lupaxa.ghcr_intel.config.models import DefaultsSettings, ProfileSpec
from lupaxa.ghcr_intel.constants import CHECK_NAMES
from lupaxa.ghcr_intel.errors import ConfigError

__all__ = [
    "ConfigProfiles",
    "ResolvedProfile",
    "resolve_profile",
]


@dataclass(frozen=True)
class ConfigProfiles:
    """Named profiles and the defaults an omitted flag falls back to."""

    profiles: dict[str, ProfileSpec]
    defaults: DefaultsSettings


@dataclass(frozen=True)
class ResolvedProfile:
    """Effective check flags after inheritance and repository overrides."""

    name: str
    chain: tuple[str, ...]
    enabled: dict[str, bool]


def resolve_profile(profiles: ConfigProfiles, name: str, overrides: Mapping[str, bool] | None = None) -> ResolvedProfile:
    """
    Resolve ``name`` from defaults, then parents, then repository overrides.

    The walk starts at ``name`` and follows ``extends`` to the root. Flags
    begin as ``profiles.defaults``. Each ancestor is applied from the root
    toward ``name``, so a child replaces its parent. ``overrides`` are applied
    last. An explicit false replaces an inherited true.

    Args:
        profiles: Named profiles plus the defaults block they inherit from.
        name: Profile to resolve.
        overrides: Repository check overrides applied last.

    Returns:
        Effective flags. ``chain`` runs from the named profile to the root.

    Raises:
        ConfigError: The profile is missing, inheritance is circular, or an
            override names an unknown check.

    """

    chain = _inheritance_chain(profiles.profiles, name)
    enabled = _check_defaults(profiles.defaults)
    for profile_name in reversed(chain):
        _apply(profiles.profiles[profile_name], enabled)
    for check, value in (overrides or {}).items():
        _override(check, value, enabled)
    return ResolvedProfile(name=name, chain=chain, enabled=enabled)


def _check_defaults(settings: DefaultsSettings) -> dict[str, bool]:
    values = {
        "metadata": settings.metadata,
        "tags": settings.tags,
        "digests": settings.digests,
        "architectures": settings.architectures,
        "stale": settings.stale,
    }
    return {check: values[check] for check in CHECK_NAMES}


def _apply(spec: ProfileSpec, enabled: dict[str, bool]) -> None:
    flags = {
        "metadata": spec.metadata,
        "tags": spec.tags,
        "digests": spec.digests,
        "architectures": spec.architectures,
        "stale": spec.stale,
    }
    for check in CHECK_NAMES:
        value = flags[check]
        if value is not None:
            enabled[check] = value


def _override(check: str, value: object, enabled: dict[str, bool]) -> None:
    if check not in CHECK_NAMES:
        raise ConfigError(f"Unknown check name: {check}.")
    if not isinstance(value, bool):
        raise ConfigError(f"Check {check} must be true or false.")
    enabled[check] = value


def _inheritance_chain(profiles: dict[str, ProfileSpec], name: str) -> tuple[str, ...]:
    chain: list[str] = []
    seen: set[str] = set()
    current: str | None = name
    while current is not None:
        if current in seen:
            cycle = " -> ".join([*chain, current])
            raise ConfigError(f"Circular profile inheritance: {cycle}.")
        spec = profiles.get(current)
        if spec is None:
            raise ConfigError(f"Unknown profile: {current}.")
        seen.add(current)
        chain.append(current)
        current = spec.extends
    return tuple(chain)
