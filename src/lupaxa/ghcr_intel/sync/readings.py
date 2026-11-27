"""Read version lists, stale ages, and status sentences from stored data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

__all__ = [
    "StaleResult",
    "VersionPage",
    "read_versions",
    "stale_result",
    "status_text",
]

_DAY = 86400
_FORBIDDEN = "The token cannot read this image."
_UNAVAILABLE = "GitHub could not find this image."
_CURRENT = "Metadata was read."
_WINDOW = "The package and its newest tag are inside the stale window."
_REQUEST_FAILED = "The request failed."


@dataclass(frozen=True)
class VersionPage:
    """Tags and the newest digest kept from one versions page."""

    tags: tuple[str, ...]
    digest: str
    newest_tag: str
    fetched: int
    truncated: bool
    newest_tag_at: str


@dataclass(frozen=True)
class StaleResult:
    """Ages in days, and whether an enabled stale check failed."""

    failed: bool
    package_days_old: int | None
    tag_days_old: int | None


def read_versions(body: list[object], *, limit: int) -> VersionPage:
    """
    Keep the leading versions and choose the newest tagged digest.

    ``body`` is newest-first. At most ``limit`` items are kept. ``truncated``
    is true when the list is at least ``limit`` long. ``newest_tag`` is
    ``latest`` when the first tagged version has it, otherwise the last tag
    on that version. An untagged page uses the first version's name.

    """

    kept = body[: max(limit, 0)]
    names: list[str] = []
    chosen: tuple[str, str, list[str]] | None = None
    first: tuple[str, str] | None = None
    for item in kept:
        name, created, tags = _version_fields(item)
        if first is None:
            first = (name, created)
        for tag in tags:
            if tag not in names:
                names.append(tag)
        if chosen is None and tags:
            chosen = (name, created, tags)
    if chosen is None:
        digest, newest_tag_at = first if first is not None else ("", "")
        newest_tag = ""
    else:
        digest, newest_tag_at, chosen_tags = chosen
        newest_tag = "latest" if "latest" in chosen_tags else chosen_tags[-1]
    return VersionPage(tags=tuple(sorted(names)), digest=digest, newest_tag=newest_tag, fetched=len(kept), truncated=len(body) >= limit, newest_tag_at=newest_tag_at)


def stale_result(*, updated: str, newest_tag_at: str, now: float, package_days: int, tag_days: int, enabled: bool) -> StaleResult:
    """
    Age the package and the newest tag.

    A missing timestamp does not fail that half. When ``enabled`` is false the
    check does not fail, and the ages are still returned.

    """

    package_days_old = _age_days(updated, now)
    tag_days_old = _age_days(newest_tag_at, now)
    package_failed = package_days_old is not None and package_days_old >= package_days
    tag_failed = tag_days_old is not None and tag_days_old >= tag_days
    return StaleResult(failed=enabled and (package_failed or tag_failed), package_days_old=package_days_old, tag_days_old=tag_days_old)


def status_text(*, metadata_status: int, metadata_error: str | None, versions_error: str | None, stale: StaleResult | None, metadata_enabled: bool) -> tuple[str, str]:
    """
    Return the short status word and one detail sentence.

    The short word is not prefixed onto the sentence. A versions error wins
    over a successful metadata status. ``stale`` of ``None`` means the stale
    check is off.

    """

    failure = _http_failure(metadata_status, metadata_error, metadata_enabled=metadata_enabled)
    if failure is not None:
        return failure
    if versions_error:
        return "Error", versions_error
    if stale is not None and stale.failed:
        return "Stale", _stale_sentence(stale)
    if stale is not None:
        return "Current", _WINDOW
    return "Current", _CURRENT


def _version_fields(item: object) -> tuple[str, str, list[str]]:
    mapping = _mapping(item)
    if mapping is None:
        return "", "", []
    return (_text(mapping, "name"), _text(mapping, "created_at"), _tags(mapping))


def _mapping(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    mapping: dict[str, object] = {}
    for key, item in value.items():
        if isinstance(key, str):
            mapping[key] = item
    return mapping


def _text(mapping: dict[str, object], key: str) -> str:
    value = mapping.get(key)
    if isinstance(value, str):
        return value
    return ""


def _tags(mapping: dict[str, object]) -> list[str]:
    metadata = _mapping(mapping.get("metadata"))
    if metadata is None:
        return []
    container = _mapping(metadata.get("container"))
    if container is None:
        return []
    raw = container.get("tags")
    if not isinstance(raw, list) or not raw:
        return []
    return [item for item in raw if isinstance(item, str)]


def _age_days(value: str, now: float) -> int | None:
    moment = _instant(value)
    if moment is None:
        return None
    return int((now - moment.timestamp()) // _DAY)


def _instant(value: str) -> datetime | None:
    if value == "":
        return None
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None
    return moment.astimezone(UTC)


def _http_failure(status: int, metadata_error: str | None, *, metadata_enabled: bool) -> tuple[str, str] | None:
    if status == 200 or (status == 0 and not metadata_enabled):
        return None
    if status in {401, 403}:
        return "Forbidden", _FORBIDDEN
    if status == 404:
        return "Unavailable", _UNAVAILABLE
    if metadata_error:
        return "Error", metadata_error
    return "Error", _REQUEST_FAILED


def _stale_sentence(stale: StaleResult) -> str:
    package = stale.package_days_old
    tag = stale.tag_days_old
    if package is not None and tag is not None:
        return f"The package was last updated {_days(package)} ago and the newest tag is {_days(tag)} old."
    if package is not None:
        return f"The package was last updated {_days(package)} ago."
    if tag is not None:
        return f"The newest tag is {_days(tag)} old."
    return "The age could not be read."


def _days(count: int) -> str:
    unit = "day" if count == 1 else "days"
    return f"{count} {unit}"
