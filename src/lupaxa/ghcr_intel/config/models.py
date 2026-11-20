"""
Configuration models.

Missing optional values receive defaults. Unknown keys and invalid types
are rejected instead of being replaced.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from lupaxa.ghcr_intel.constants import CHECK_NAMES, DEFAULT_PROFILE, UNGROUPED
from lupaxa.ghcr_intel.dates import DEFAULT_DATE_FORMAT, require_date_format
from lupaxa.ghcr_intel.errors import ConfigError
from lupaxa.ghcr_intel.names import parse_image

__all__ = [
    "AppConfig",
    "CacheSettings",
    "ChecksSettings",
    "DefaultsSettings",
    "DisplaySettings",
    "FileSettings",
    "GitHubSettings",
    "LoggingSettings",
    "OwnerSpec",
    "PackageEntry",
    "PackageFile",
    "PackageSpec",
    "ProfileSpec",
    "ProfilesFile",
    "StaleSettings",
    "SyncSettings",
    "ViewSettings",
]

_VIEW_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_LOG_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
_IMAGE = "An image is owner/name. A slash after the owner is part of the name."


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class GitHubSettings(_StrictModel):
    """GitHub and registry locations. Credentials are never read from this file."""

    api_url: str = "https://api.github.com"
    registry_url: str = "https://ghcr.io"

    @field_validator("api_url", "registry_url")
    @classmethod
    def _https_url(cls, value: str) -> str:
        text = value.strip().rstrip("/")
        if not text.startswith("https://"):
            message = "must be an https URL."
            raise ValueError(message)
        return text


class FileSettings(_StrictModel):
    """Paths of the profile and package files, relative to config.yml."""

    profiles: str = "profiles.yml"
    packages: str = "packages.yml"


class CacheSettings(_StrictModel):
    """Per-category freshness windows, in seconds."""

    enabled: bool = True
    metadata_ttl: int = Field(default=3600, ge=0)
    versions_ttl: int = Field(default=1800, ge=0)
    manifests_ttl: int = Field(default=21600, ge=0)


class SyncSettings(_StrictModel):
    """Bounded synchronisation settings."""

    concurrency: int = Field(default=10, ge=1)
    timeout: int = Field(default=15, gt=0)
    retries: int = Field(default=3, ge=0)
    interval: int = Field(default=0, ge=0)
    version_limit: int = Field(default=100, ge=1)
    cache: CacheSettings = Field(default_factory=CacheSettings)


class LoggingSettings(_StrictModel):
    """Logging level stored in configuration."""

    level: str = "INFO"

    @field_validator("level")
    @classmethod
    def _level(cls, value: str) -> str:
        text = value.strip().upper()
        if text not in _LOG_LEVELS:
            message = "logging.level must be CRITICAL, ERROR, WARNING, INFO, or DEBUG."
            raise ValueError(message)
        return text


class ProfileSpec(_StrictModel):
    """One profile. Omitted checks are inherited, then taken from defaults."""

    extends: str | None = None
    metadata: bool | None = None
    tags: bool | None = None
    digests: bool | None = None
    architectures: bool | None = None
    stale: bool | None = None

    @field_validator("extends")
    @classmethod
    def _extends(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if text == "":
            message = "extends must name a profile."
            raise ValueError(message)
        return text


class DefaultsSettings(_StrictModel):
    """Default profile name and the check flags used when no profile sets them."""

    profile: str = DEFAULT_PROFILE
    metadata: bool = True
    tags: bool = True
    digests: bool = False
    architectures: bool = False
    stale: bool = False

    @field_validator("profile")
    @classmethod
    def _profile(cls, value: str) -> str:
        text = value.strip()
        if text == "":
            message = "defaults.profile must not be blank."
            raise ValueError(message)
        return text


class StaleSettings(_StrictModel):
    """Age thresholds for the stale check, in days."""

    package_days: int = Field(default=180, ge=0)
    tag_days: int = Field(default=90, ge=0)


class ChecksSettings(_StrictModel):
    """Thresholds for checks that are more than a boolean."""

    stale: StaleSettings = Field(default_factory=StaleSettings)


class ViewSettings(_StrictModel):
    """One named list and an optional detail layout."""

    list: tuple[str, ...] | None = None
    detail: tuple[str, ...] | None = None

    @field_validator("list", "detail", mode="before")
    @classmethod
    def _side(cls, value: object) -> tuple[str, ...] | None:
        if value is None:
            return None
        if not isinstance(value, (list, tuple)):
            message = "a view side must be a list of field names."
            raise ValueError(message)
        labels: list[str] = []
        for item in value:
            if not isinstance(item, str):
                message = "a view side must be a list of field names."
                raise ValueError(message)
            text = item.strip()
            if text == "":
                message = "a view side must not contain a blank field name."
                raise ValueError(message)
            labels.append(text)
        return tuple(labels)

    @model_validator(mode="after")
    def _requires_a_side(self) -> ViewSettings:
        if self.list is None and self.detail is None:
            message = "a view must define list and/or detail."
            raise ValueError(message)
        return self


class DisplaySettings(_StrictModel):
    """
    How dates are shown, which view is selected, and the named views.

    Attributes:
        date_format: Pattern for dates in tables and reports.
        view: Name of the selected view. ``default`` is always valid.
        views: Named views, each with an optional list and detail.

    """

    date_format: str = DEFAULT_DATE_FORMAT
    view: str = "default"
    views: dict[str, ViewSettings] = Field(default_factory=dict)

    @field_validator("date_format")
    @classmethod
    def _date_format(cls, value: str) -> str:
        return require_date_format(value)

    @field_validator("view")
    @classmethod
    def _view(cls, value: str) -> str:
        return value.strip()

    @field_validator("views")
    @classmethod
    def _views(cls, value: dict[str, ViewSettings]) -> dict[str, ViewSettings]:
        rebuilt: dict[str, ViewSettings] = {}
        for name, entry in value.items():
            text = name.strip()
            if not _VIEW_NAME.match(text):
                message = f"invalid view name {name!r}."
                raise ValueError(message)
            if text in rebuilt:
                message = f"duplicate view name {text!r}."
                raise ValueError(message)
            rebuilt[text] = entry
        return rebuilt

    @model_validator(mode="after")
    def _active_view(self) -> DisplaySettings:
        if self.view != "default" and self.view not in self.views:
            message = f"unknown display view {self.view!r}."
            raise ValueError(message)
        return self


class AppConfig(_StrictModel):
    """The main ``config.yml`` document."""

    version: int
    github: GitHubSettings = Field(default_factory=GitHubSettings)
    files: FileSettings = Field(default_factory=FileSettings)
    defaults: DefaultsSettings = Field(default_factory=DefaultsSettings)
    display: DisplaySettings = Field(default_factory=DisplaySettings)
    sync: SyncSettings = Field(default_factory=SyncSettings)
    checks: ChecksSettings = Field(default_factory=ChecksSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)

    @field_validator("version")
    @classmethod
    def _version(cls, value: int) -> int:
        if value != 1:
            message = "version must be 1."
            raise ValueError(message)
        return value


class ProfilesFile(_StrictModel):
    """The ``profiles.yml`` document."""

    profiles: dict[str, ProfileSpec]

    @field_validator("profiles", mode="before")
    @classmethod
    def _profiles(cls, value: object) -> object:
        if not isinstance(value, dict):
            message = "profiles must be an object."
            raise ValueError(message)
        rebuilt: dict[str, object] = {}
        for name, body in value.items():
            if not isinstance(name, str) or name.strip() == "":
                message = "profile names must be non-blank strings."
                raise ValueError(message)
            text = name.strip()
            if text in rebuilt:
                message = f"duplicate profile name {text!r}."
                raise ValueError(message)
            rebuilt[text] = body
        return rebuilt


class PackageEntry(_StrictModel):
    """One package written as an object under an owner."""

    name: str
    profile: str | None = None
    group: str | None = None
    enabled: bool | None = None
    tags: list[str] = Field(default_factory=list)
    checks: dict[str, bool] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return _image_part(value)

    @field_validator("profile", "group")
    @classmethod
    def _optional_label(cls, value: str | None) -> str | None:
        return _clean_optional(value)

    @field_validator("tags")
    @classmethod
    def _tags(cls, value: list[str]) -> list[str]:
        return _clean_tags(value)

    @field_validator("checks")
    @classmethod
    def _checks(cls, value: dict[str, bool]) -> dict[str, bool]:
        return _clean_checks(value)


class OwnerSpec(_StrictModel):
    """One GitHub user or organisation and the packages it owns."""

    name: str
    group: str | None = None
    profile: str | None = None
    packages: list[str | PackageEntry]

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return _image_part(value)

    @field_validator("profile", "group")
    @classmethod
    def _optional_label(cls, value: str | None) -> str | None:
        return _clean_optional(value)


class PackageFile(_StrictModel):
    """The ``packages.yml`` document."""

    owners: list[OwnerSpec]


class PackageSpec(_StrictModel):
    """One configured image, flattened from an owner entry."""

    owner: str
    name: str
    group: str = UNGROUPED
    profile: str = DEFAULT_PROFILE
    enabled: bool = True
    tags: list[str] = Field(default_factory=list)
    checks: dict[str, bool] = Field(default_factory=dict)

    @field_validator("owner", "name")
    @classmethod
    def _identity_text(cls, value: str) -> str:
        return _image_part(value)

    @field_validator("group", "profile")
    @classmethod
    def _required_label(cls, value: str) -> str:
        text = value.strip()
        if text == "":
            message = "must not be blank."
            raise ValueError(message)
        return text

    @field_validator("tags")
    @classmethod
    def _tags(cls, value: list[str]) -> list[str]:
        return _clean_tags(value)

    @field_validator("checks")
    @classmethod
    def _checks(cls, value: dict[str, bool]) -> dict[str, bool]:
        return _clean_checks(value)

    @model_validator(mode="after")
    def _image(self) -> PackageSpec:
        parsed_owner, parsed_name = parse_image(f"{self.owner}/{self.name}")
        if parsed_owner != self.owner or parsed_name != self.name:
            raise ConfigError(_IMAGE)
        return self


def _image_part(value: str) -> str:
    text = value.strip()
    if text == "":
        raise ConfigError(_IMAGE)
    return text


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if text == "":
        message = "must not be blank."
        raise ValueError(message)
    return text


def _clean_tags(value: list[str]) -> list[str]:
    cleaned: list[str] = []
    for tag in value:
        text = tag.strip()
        if text == "":
            message = "tags must not contain a blank value."
            raise ValueError(message)
        cleaned.append(text)
    return cleaned


def _clean_checks(value: dict[str, bool]) -> dict[str, bool]:
    unknown = sorted(set(value) - set(CHECK_NAMES))
    if unknown:
        message = f"Unknown check names: {', '.join(unknown)}."
        raise ValueError(message)
    return value
