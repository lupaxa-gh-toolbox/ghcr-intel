"""List and detail cells for one stored image."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from typing import cast

from lupaxa.ghcr_intel.config.models import AppConfig, PackageSpec
from lupaxa.ghcr_intel.config.profiles import ResolvedProfile
from lupaxa.ghcr_intel.dates import format_date
from lupaxa.ghcr_intel.layout import CATALOGUE
from lupaxa.ghcr_intel.storage.database import Document
from lupaxa.ghcr_intel.sync.readings import StaleResult, read_versions, stale_result, status_text

__all__ = [
    "ImageView",
    "build_view",
    "cache_word",
    "detail_rows",
    "field_text",
]

_VERSION_CHECKS = ("tags", "digests", "architectures", "stale")
_PACKAGE_ANSWER = frozenset({401, 403, 404})
_NOT_SYNCHRONISED = "Not synchronised"
_NOT_SYNCHRONISED_DETAIL = "This image has not been synchronised."
_METADATA_FIELDS = frozenset(
    {
        "Visibility",
        "Description",
        "Linked repository",
        "Created",
        "Updated",
    }
)
_TAG_FIELDS = frozenset({"Newest tag", "Image tags", "Tag count"})
_VERSION_FIELDS = frozenset({"Versions fetched", "Versions truncated"})


@dataclass(frozen=True)
class ImageView:
    """One image as shown on a list and on a detail."""

    owner: str
    package: str
    group: str
    profile: str
    local_tags: tuple[str, ...]
    enabled: bool
    visibility: str
    description: str
    linked_repository: str
    status: str
    status_detail: str
    cache: str
    created: str
    updated: str
    synchronised: str
    newest_tag: str
    image_tags: tuple[str, ...]
    tag_count: int
    versions_fetched: int
    versions_truncated: bool
    digest: str
    architectures: str
    architectures_error: str
    stale_word: str
    metadata_error: str
    versions_error: str
    date_format: str
    metadata_on: bool
    tags_on: bool
    digests_on: bool
    architectures_on: bool
    stale_on: bool
    versions_on: bool


def build_view(config: AppConfig, package: PackageSpec, profile: ResolvedProfile, documents: Sequence[Document], *, now: float) -> ImageView:
    """
    Build one image row from configuration and stored documents.

    ``status_text`` receives ``stale=None`` when the stale check is off. A
    manifest error stays on Architectures and does not change Status.

    """

    metadata = _document(documents, "metadata")
    versions = _document(documents, "versions")
    enabled = profile.enabled
    versions_on = _versions_on(enabled)
    stored_versions = versions if versions_on else None
    body = _object("" if metadata is None else metadata.body)
    page = read_versions(_items(stored_versions), limit=config.sync.version_limit)
    stale = _age(config, body, enabled=enabled, newest_tag_at=page.newest_tag_at, now=now)
    status, detail = _status(metadata, stored_versions, body, enabled=enabled, stale=stale)
    architectures, architectures_error = _shown_architectures(_document(documents, "manifest"), enabled=enabled)
    fetched_at = None if metadata is None else metadata.fetched_at
    metadata_error = _shown_metadata_error(metadata, enabled=enabled)
    return ImageView(
        owner=package.owner,
        package=package.name,
        group=package.group,
        profile=profile.name,
        local_tags=tuple(package.tags),
        enabled=package.enabled,
        visibility=_text(body, "visibility"),
        description=_text(body, "description"),
        linked_repository=_text(body, "repository"),
        status=status,
        status_detail=detail,
        cache=_cache_text(fetched_at, now=now, ttl=float(config.sync.cache.metadata_ttl), http_status=_http_status(body) if metadata is not None else 0, error=metadata_error),
        created=_text(body, "created_at"),
        updated=_text(body, "updated_at"),
        synchronised=_instant(fetched_at),
        newest_tag=page.newest_tag,
        image_tags=page.tags,
        tag_count=len(page.tags),
        versions_fetched=page.fetched,
        versions_truncated=page.truncated,
        digest=page.digest,
        architectures=architectures,
        architectures_error=architectures_error,
        stale_word="yes" if stale is not None and stale.failed else "no",
        metadata_error=metadata_error,
        versions_error=_optional_error(stored_versions),
        date_format=config.display.date_format,
        metadata_on=enabled["metadata"],
        tags_on=enabled["tags"],
        digests_on=enabled["digests"],
        architectures_on=enabled["architectures"],
        stale_on=enabled["stale"],
        versions_on=versions_on,
    )


def field_text(view: ImageView, label: str, *, detail: bool) -> str:
    """
    Return one catalogue cell.

    Status is the short word on a list and the sentence on a detail. A
    disabled check is blank on a list and ``SKIPPED — {check} is disabled.``
    on a detail. Tags and Image tags are joined. Versions truncated is
    ``yes`` or ``no``.

    """

    if label == "Status":
        return view.status_detail if detail else view.status
    fixed = _fixed(view, label)
    if fixed is not None:
        return fixed
    checked = _checked(view, label, detail=detail)
    if checked is not None:
        return checked
    message = f"unknown display field {label!r}."
    raise ValueError(message)


def detail_rows(view: ImageView) -> list[tuple[str, str]]:
    """
    Return the detail rows for one image.

    ``Metadata error`` is first when that text is non-empty. The catalogue
    labels follow, in catalogue order.

    """

    rows: list[tuple[str, str]] = []
    if view.metadata_error != "":
        rows.append(("Metadata error", view.metadata_error))
    rows.extend((label, field_text(view, label, detail=True)) for label in CATALOGUE)
    return rows


def cache_word(fetched_at: float | None, now: float, ttl: float) -> str:
    """
    Name the metadata cache from its age.

    A missing fetch is ``not-synchronised``. A document at least ``ttl``
    seconds old is ``stale``. A younger document is ``cached``.

    """

    if fetched_at is None:
        return "not-synchronised"
    if now - fetched_at >= ttl:
        return "stale"
    return "cached"


def _fixed(view: ImageView, label: str) -> str | None:
    values = {
        "Organisation": view.owner,
        "Package": view.package,
        "Group": view.group,
        "Profile": view.profile,
        "Tags": ", ".join(view.local_tags),
        "Enabled": "yes" if view.enabled else "no",
        "Cache": view.cache,
        "Synchronised": format_date(view.synchronised, view.date_format),
    }
    return values.get(label)


def _checked(view: ImageView, label: str, *, detail: bool) -> str | None:
    if label in _METADATA_FIELDS:
        return _gated(view.metadata_on, "metadata", _metadata_value(view, label), detail=detail)
    if label in _TAG_FIELDS:
        return _failed_or_value(view, "tags", view.tags_on, _tag_value(view, label), detail=detail)
    if label == "Digest":
        return _failed_or_value(view, "digests", view.digests_on, view.digest, detail=detail)
    if label == "Architectures":
        return _gated(view.architectures_on, "architectures", view.architectures, detail=detail)
    if label == "Stale":
        return _gated(view.stale_on, "stale", view.stale_word, detail=detail)
    if label in _VERSION_FIELDS:
        return _versions_cell(view, label, detail=detail)
    return None


def _metadata_value(view: ImageView, label: str) -> str:
    if label == "Visibility":
        return view.visibility
    if label == "Description":
        return view.description
    if label == "Linked repository":
        return view.linked_repository
    if label == "Created":
        return format_date(view.created, view.date_format)
    return format_date(view.updated, view.date_format)


def _tag_value(view: ImageView, label: str) -> str:
    if label == "Newest tag":
        return view.newest_tag
    if label == "Image tags":
        return ", ".join(view.image_tags)
    return str(view.tag_count)


def _failed_or_value(view: ImageView, check: str, enabled: bool, value: str, *, detail: bool) -> str:
    if not enabled:
        return _skipped(check, detail=detail)
    if view.versions_error != "":
        return "Error"
    return value


def _versions_cell(view: ImageView, label: str, *, detail: bool) -> str:
    if not view.versions_on:
        return _skipped("tags", detail=detail)
    if label == "Versions fetched":
        return str(view.versions_fetched)
    return "yes" if view.versions_truncated else "no"


def _gated(enabled: bool, check: str, value: str, *, detail: bool) -> str:
    if not enabled:
        return _skipped(check, detail=detail)
    return value


def _skipped(check: str, *, detail: bool) -> str:
    if not detail:
        return ""
    return f"SKIPPED — {check} is disabled."


def _status(metadata: Document | None, versions: Document | None, body: dict[str, object], *, enabled: Mapping[str, bool], stale: StaleResult | None) -> tuple[str, str]:
    if metadata is None and (enabled["metadata"] or versions is None):
        return _NOT_SYNCHRONISED, _NOT_SYNCHRONISED_DETAIL
    return status_text(
        metadata_status=_metadata_status(metadata, body, enabled=enabled),
        metadata_error=_metadata_failure(metadata, enabled=enabled),
        versions_error=_versions_failure(versions),
        stale=stale,
        metadata_enabled=enabled["metadata"],
    )


def _age(config: AppConfig, body: dict[str, object], *, enabled: Mapping[str, bool], newest_tag_at: str, now: float) -> StaleResult | None:
    if not enabled["stale"]:
        return None
    return stale_result(updated=_text(body, "updated_at"), newest_tag_at=newest_tag_at, now=now, package_days=config.checks.stale.package_days, tag_days=config.checks.stale.tag_days, enabled=True)


def _shown_architectures(document: Document | None, *, enabled: Mapping[str, bool]) -> tuple[str, str]:
    if not enabled["architectures"]:
        return "", ""
    return _architectures(document)


def _architectures(document: Document | None) -> tuple[str, str]:
    if document is None:
        return "", ""
    error = _stored_error(document)
    if error != "":
        return error, error
    platforms = _object(document.body).get("platforms")
    if not isinstance(platforms, list):
        return "", ""
    names = [item for item in platforms if isinstance(item, str) and item != ""]
    return ", ".join(names), ""


def _cache_text(fetched_at: float | None, *, now: float, ttl: float, http_status: int, error: str) -> str:
    word = cache_word(fetched_at, now, ttl)
    if error == "":
        return word
    token = "unavailable" if http_status == 404 else "error"
    return f"{word}, {token}"


def _metadata_status(metadata: Document | None, body: dict[str, object], *, enabled: Mapping[str, bool]) -> int:
    if metadata is None:
        return 0
    status = _http_status(body)
    if not enabled["metadata"] and status not in _PACKAGE_ANSWER:
        return 0
    return status


def _metadata_failure(metadata: Document | None, *, enabled: Mapping[str, bool]) -> str | None:
    if metadata is None or not enabled["metadata"]:
        return None
    return _blank_as_none(_stored_error(metadata))


def _shown_metadata_error(metadata: Document | None, *, enabled: Mapping[str, bool]) -> str:
    failure = _metadata_failure(metadata, enabled=enabled)
    if failure is None:
        return ""
    return failure


def _versions_failure(versions: Document | None) -> str | None:
    return None if versions is None else _blank_as_none(_stored_error(versions))


def _optional_error(document: Document | None) -> str:
    if document is None:
        return ""
    return _stored_error(document)


def _blank_as_none(value: str) -> str | None:
    if value == "":
        return None
    return value


def _document(documents: Sequence[Document], category: str) -> Document | None:
    for document in documents:
        if document.category == category:
            return document
    return None


def _stored_error(document: Document) -> str:
    if document.error is None:
        return ""
    return document.error


def _object(body: str) -> dict[str, object]:
    if body == "":
        return {}
    try:
        loaded = cast("object", json.loads(body))
    except json.JSONDecodeError:
        return {}
    if not isinstance(loaded, dict):
        return {}
    mapped: dict[str, object] = {}
    for key, value in loaded.items():
        if isinstance(key, str):
            mapped[key] = cast("object", value)
    return mapped


def _items(document: Document | None) -> list[object]:
    if document is None or document.body == "":
        return []
    try:
        loaded = cast("object", json.loads(document.body))
    except json.JSONDecodeError:
        return []
    if not isinstance(loaded, list):
        return []
    return [cast("object", item) for item in loaded]


def _text(body: dict[str, object], key: str) -> str:
    value = body.get(key)
    if isinstance(value, str):
        return value
    return ""


def _http_status(body: dict[str, object]) -> int:
    value = body.get("http_status")
    if isinstance(value, bool) or not isinstance(value, int):
        return 200
    return value


def _instant(moment: float | None) -> str:
    if moment is None:
        return ""
    return datetime.fromtimestamp(moment, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _versions_on(enabled: Mapping[str, bool]) -> bool:
    return any(enabled[name] for name in _VERSION_CHECKS)
