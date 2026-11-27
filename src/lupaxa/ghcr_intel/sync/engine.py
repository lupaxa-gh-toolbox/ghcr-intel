"""Fetch configured images and store one document per category."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
import json
import time
from typing import Protocol, TypeGuard, cast

import httpx

from lupaxa.ghcr_intel.config.models import AppConfig, PackageSpec, ProfileSpec
from lupaxa.ghcr_intel.config.paths import data_directory
from lupaxa.ghcr_intel.config.profiles import ConfigProfiles, resolve_profile
from lupaxa.ghcr_intel.errors import AuthError, UsageError
from lupaxa.ghcr_intel.ghcr.packages import PackageFetch, fetch_package as read_package
from lupaxa.ghcr_intel.ghcr.registry import ManifestFetch, fetch_manifest as read_manifest
from lupaxa.ghcr_intel.storage.database import Database, Document
from lupaxa.ghcr_intel.sync.readings import StaleResult, read_versions, stale_result, status_text

__all__ = [
    "SyncReport",
    "assert_sync_allowed",
    "synchronise",
]

_VERSION_CHECKS = ("tags", "digests", "architectures", "stale")
_FINISHED = frozenset({401, 403, 404})
_MISSING = "The GitHub CLI token is missing."
_INTERVAL = "The last sync is still inside the sync interval."
_FAILED = "The request failed."


class PackageReader(Protocol):
    """Injected package reader. Tests pass a fake. The default calls GitHub."""

    async def __call__(self, client: httpx.AsyncClient, *, api_url: str, owner: str, name: str, version_limit: int, want_versions: bool, github_token: str) -> PackageFetch:
        """Read one package."""


class ManifestReader(Protocol):
    """Injected manifest reader. Tests pass a fake. The default calls ghcr.io."""

    async def __call__(self, client: httpx.AsyncClient, *, registry_url: str, owner: str, name: str, tag: str, github_token: str) -> ManifestFetch:
        """Read one manifest."""


@dataclass
class SyncReport:
    """Image outcomes and request counters for one run."""

    incomplete: bool = False
    images: int = 0
    successful: int = 0
    failed: int = 0
    requests: int = 0
    cache_hits: int = 0
    latest: str = ""
    elapsed: float = 0.0
    skipped: int = 0
    rate_pauses: int = 0
    rate_wait: float = 0.0
    on_progress: Callable[[SyncReport], None] | None = field(default=None, repr=False)


@dataclass
class _Pause:
    """One shared rate-limit wait for every worker."""

    until: float = 0.0
    pauses: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass(frozen=True)
class _Job:
    config: AppConfig
    profiles: ConfigProfiles
    database: Database
    client: httpx.AsyncClient
    token: str
    force: bool
    now: float
    read_package: PackageReader
    read_manifest: ManifestReader
    report: SyncReport
    concurrency: int
    pause: _Pause


async def synchronise(
    config: AppConfig,
    packages: Sequence[PackageSpec],
    *,
    token: str | None,
    force: bool,
    now: float,
    database: Database | None = None,
    profiles: ConfigProfiles | None = None,
    fetch_package: PackageReader | None = None,
    fetch_manifest: ManifestReader | None = None,
    on_progress: Callable[[SyncReport], None] | None = None,
    concurrency: int | None = None,
) -> SyncReport:
    """
    Refresh each image and store metadata, versions, and manifest documents.

    A missing token raises ``AuthError`` before any request. A positive
    ``sync.interval`` raises ``UsageError`` when the latest finished run is
    still inside that window, unless ``force`` is true, and writes nothing.
    ``on_progress`` is called as the current image and the counters change.
    It must not await.
    Up to ``concurrency`` or ``sync.concurrency`` images run at once. Disabled
    images stay configured and are not requested. A 429 that names a wait
    pauses every worker, and the progress callback sees ``rate_wait``. A 429
    or 5xx is repeated
    ``sync.retries`` times. A remaining retryable status, a transport or URL
    error, a 200 whose body could not be read, or a manifest result of Unknown
    sets ``incomplete``. A 401, 403, or 404 is a finished answer. A retryable
    or transport failure does not replace a previous successful document. A
    stored document with an error is not fresh. Later images still run.

    """

    if token is None:
        raise AuthError(_MISSING)
    if concurrency is not None and concurrency < 1:
        raise UsageError("concurrency must be at least 1.")
    store = database if database is not None else _open_database()
    assert_sync_allowed(config, store, force=force, now=now)
    job_profiles = profiles if profiles is not None else _profiles(config, packages)
    package_reader = read_package if fetch_package is None else fetch_package
    manifest_reader = read_manifest if fetch_manifest is None else fetch_manifest
    active = [package for package in packages if package.enabled]
    report = SyncReport(images=len(active), skipped=len(packages) - len(active))
    report.on_progress = on_progress
    width = config.sync.concurrency if concurrency is None else concurrency
    started = time.monotonic()
    run_id = store.begin_run(now)
    try:
        async with httpx.AsyncClient(timeout=config.sync.timeout) as client:
            job = _Job(config, job_profiles, store, client, token, force, now, package_reader, manifest_reader, report, width, _Pause())
            _notify(report)
            await _run_images(job, active)
    finally:
        report.elapsed = time.monotonic() - started
        store.finish_run(run_id, now, json.dumps({"images": len(packages), "incomplete": report.incomplete}))
    return report


async def _run_images(job: _Job, packages: Sequence[PackageSpec]) -> None:
    pending = list(packages)
    if not pending:
        return
    lock = asyncio.Lock()
    width = min(job.concurrency, len(pending))

    async def worker() -> None:
        while True:
            async with lock:
                if not pending:
                    return
                package = pending.pop(0)
                job.report.latest = f"{package.owner}/{package.name}"
                _notify(job.report)
            failed = await _one_image(job, package)
            if failed:
                job.report.failed += 1
                job.report.incomplete = True
            else:
                job.report.successful += 1
            _notify(job.report)

    tasks = [asyncio.create_task(worker()) for _ in range(width)]
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


async def _one_image(job: _Job, package: PackageSpec) -> bool:
    try:
        return await _sync_package(job, package)
    except httpx.InvalidURL as exc:
        _store_transport(job, package, exc)
        return True
    except httpx.HTTPError as exc:
        _store_transport(job, package, exc)
        return True


async def _sync_package(job: _Job, package: PackageSpec) -> bool:
    enabled = resolve_profile(job.profiles, package.profile, package.checks).enabled
    key = f"{package.owner}/{package.name}"
    want_versions = _wants_versions(enabled)
    metadata = job.database.get(key, "metadata")
    versions = job.database.get(key, "versions") if want_versions else None
    metadata_ttl = job.config.sync.cache.metadata_ttl
    versions_ttl = job.config.sync.cache.versions_ttl
    if enabled["metadata"] and _fresh(job, metadata, metadata_ttl):
        _note_cache(job)
    if want_versions and _fresh(job, versions, versions_ttl):
        _note_cache(job)
    need_metadata = enabled["metadata"] and not _fresh(job, metadata, metadata_ttl)
    need_versions = want_versions and not _fresh(job, versions, versions_ttl)
    fetched = None
    if need_metadata or need_versions:
        fetched = await _read_with_retries(job, package, want_versions=need_versions)
        if need_versions and fetched.metadata_status == 200:
            versions = _put_versions(job, key, fetched)
        if _writes_package(enabled, fetched):
            _put_metadata(job, package, key, fetched, versions, enabled, want_versions)
    manifest_failed = await _sync_manifest(job, package, key, versions, enabled, want_versions)
    return _package_failed(fetched, requested_versions=need_versions) or manifest_failed


async def _read_with_retries(job: _Job, package: PackageSpec, *, want_versions: bool) -> PackageFetch:
    attempts = job.config.sync.retries + 1
    fetched: PackageFetch | None = None
    for _attempt in range(attempts):
        await _wait_for_limit(job)
        _note_request(job)
        fetched = await job.read_package(
            job.client, api_url=job.config.github.api_url, owner=package.owner, name=package.name, version_limit=job.config.sync.version_limit, want_versions=want_versions, github_token=job.token
        )
        if not _retryable(fetched.metadata_status) and not _retryable(fetched.versions_status):
            return fetched
        _hold_limit(job, fetched.retry_after)
    if fetched is None:
        return PackageFetch(0, None, _FAILED, None, None, "")
    return fetched


async def _sync_manifest(job: _Job, package: PackageSpec, key: str, versions: Document | None, enabled: dict[str, bool], want_versions: bool) -> bool:
    if not enabled["architectures"] or not want_versions:
        return False
    page = read_versions(_items(versions), limit=job.config.sync.version_limit)
    if page.newest_tag == "":
        return False
    current = job.database.get(key, "manifest")
    if _fresh(job, current, job.config.sync.cache.manifests_ttl):
        _note_cache(job)
        return False
    fetched = await _manifest_with_retries(job, package, page.newest_tag)
    if fetched.error == "Unknown" and _successful(current) and current.body != "":
        return True
    job.database.put(key, "manifest", body=json.dumps({"platforms": list(fetched.platforms)}), fetched_at=job.now, error=fetched.error)
    return fetched.error == "Unknown"


async def _manifest_with_retries(job: _Job, package: PackageSpec, tag: str) -> ManifestFetch:
    attempts = job.config.sync.retries + 1
    fetched = ManifestFetch((), "Unknown")
    for _attempt in range(attempts):
        await _wait_for_limit(job)
        _note_request(job)
        fetched = await job.read_manifest(job.client, registry_url=job.config.github.registry_url, owner=package.owner, name=package.name, tag=tag, github_token=job.token)
        if fetched.error != "Unknown":
            return fetched
        _hold_limit(job, fetched.retry_after)
    return fetched


def _put_metadata(job: _Job, package: PackageSpec, key: str, fetched: PackageFetch, versions: Document | None, enabled: dict[str, bool], want_versions: bool) -> None:
    if _keeps_success(job, key, "metadata", fetched.metadata_status):
        return
    source = fetched.metadata_body if fetched.metadata_body is not None else {}
    versions_error = _versions_error(versions, want_versions=want_versions)
    word, detail = _status(
        job, source, enabled, http_status=fetched.metadata_status, metadata_error=fetched.metadata_error, versions_error=versions_error, want_versions=want_versions, versions=versions
    )
    job.database.put(
        key, "metadata", body=_metadata_body(source, fallback_name=package.name, http_status=fetched.metadata_status, status=word, detail=detail), fetched_at=job.now, error=fetched.metadata_error
    )


def _put_versions(job: _Job, key: str, fetched: PackageFetch) -> Document:
    previous = job.database.get(key, "versions")
    if _successful(previous) and _retryable(fetched.versions_status):
        return previous
    payload = fetched.versions_body if fetched.versions_body is not None else []
    document = Document(package=key, category="versions", body=json.dumps(payload), fetched_at=job.now, error=fetched.versions_error)
    job.database.put(key, "versions", body=document.body, fetched_at=document.fetched_at, error=document.error)
    return document


def _store_transport(job: _Job, package: PackageSpec, exc: Exception) -> None:
    text = str(exc).strip()
    if text == "":
        text = _FAILED
    key = f"{package.owner}/{package.name}"
    current = job.database.get(key, "metadata")
    if not _metadata_on(job, package) or _successful(current):
        return
    job.database.put(key, "metadata", body=_metadata_body({}, fallback_name=package.name, http_status=0, status="Error", detail=text), fetched_at=job.now, error=text)


def _status(
    job: _Job, source: dict[str, object], enabled: dict[str, bool], *, http_status: int, metadata_error: str | None, versions_error: str | None, want_versions: bool, versions: Document | None
) -> tuple[str, str]:
    stale = _stale(job, source, enabled, want_versions=want_versions, versions=versions)
    return status_text(metadata_status=http_status, metadata_error=metadata_error, versions_error=versions_error, stale=stale, metadata_enabled=enabled["metadata"])


def _stale(job: _Job, source: dict[str, object], enabled: dict[str, bool], *, want_versions: bool, versions: Document | None) -> StaleResult | None:
    if not enabled["stale"]:
        return None
    newest_tag_at = ""
    if want_versions:
        page = read_versions(_items(versions), limit=job.config.sync.version_limit)
        newest_tag_at = page.newest_tag_at
    return stale_result(
        updated=_text(source, "updated_at"), newest_tag_at=newest_tag_at, now=job.now, package_days=job.config.checks.stale.package_days, tag_days=job.config.checks.stale.tag_days, enabled=True
    )


def assert_sync_allowed(config: AppConfig, database: Database, *, force: bool, now: float) -> None:
    """Raise ``UsageError`` when the last run is still inside ``sync.interval``."""

    _require_interval(config, database, force=force, now=now)


def _hold_limit(job: _Job, seconds: float | None) -> None:
    if seconds is None or seconds <= 0:
        return
    job.pause.until = max(job.pause.until, time.monotonic() + seconds)
    job.pause.pauses += 1
    job.report.rate_pauses = job.pause.pauses
    job.report.rate_wait = seconds
    _notify(job.report)


async def _wait_for_limit(job: _Job) -> None:
    while True:
        async with job.pause.lock:
            delay = job.pause.until - time.monotonic()
            if delay <= 0:
                if job.report.rate_wait != 0:
                    job.report.rate_wait = 0.0
                    _notify(job.report)
                return
            job.report.rate_wait = delay
            _notify(job.report)
        await asyncio.sleep(min(1.0, delay))


def _notify(report: SyncReport) -> None:
    if report.on_progress is not None:
        report.on_progress(report)


def _note_request(job: _Job) -> None:
    job.report.requests += 1
    _notify(job.report)


def _note_cache(job: _Job) -> None:
    job.report.cache_hits += 1
    _notify(job.report)


def _require_interval(config: AppConfig, database: Database, *, force: bool, now: float) -> None:
    interval = config.sync.interval
    if force or interval == 0:
        return
    finished = database.latest_finished_at()
    if finished is None:
        return
    if now - finished < interval:
        raise UsageError(_INTERVAL)


def _profiles(config: AppConfig, packages: Sequence[PackageSpec]) -> ConfigProfiles:
    names = {package.profile for package in packages}
    names.add(config.defaults.profile)
    return ConfigProfiles(profiles={name: ProfileSpec() for name in sorted(names)}, defaults=config.defaults)


def _open_database() -> Database:
    root = data_directory()
    root.mkdir(parents=True, exist_ok=True)
    return Database(root / "cache.sqlite")


def _fresh(job: _Job, document: Document | None, ttl: int) -> bool:
    if job.force or not job.config.sync.cache.enabled or document is None:
        return False
    if document.error is not None and document.error != "":
        return False
    return job.now - document.fetched_at < ttl


def _keeps_success(job: _Job, key: str, category: str, status: int | None) -> bool:
    return _retryable(status) and _successful(job.database.get(key, category))


def _successful(document: Document | None) -> TypeGuard[Document]:
    if document is None:
        return False
    return document.error is None or document.error == ""


def _wants_versions(enabled: dict[str, bool]) -> bool:
    return any(enabled[name] for name in _VERSION_CHECKS)


def _writes_package(enabled: dict[str, bool], fetched: PackageFetch) -> bool:
    if enabled["metadata"]:
        return True
    status = fetched.metadata_status
    if status in _FINISHED:
        return True
    return status == 200 and fetched.metadata_error is None


def _metadata_on(job: _Job, package: PackageSpec) -> bool:
    resolved = resolve_profile(job.profiles, package.profile, package.checks)
    return resolved.enabled["metadata"]


def _package_failed(fetched: PackageFetch | None, *, requested_versions: bool) -> bool:
    if fetched is None:
        return False
    status = fetched.metadata_status
    if _retryable(status):
        return True
    if status == 200 and fetched.metadata_error is not None:
        return True
    return requested_versions and _versions_unfinished(fetched)


def _versions_unfinished(fetched: PackageFetch) -> bool:
    if fetched.versions_error is None:
        return False
    return fetched.versions_status not in _FINISHED


def _versions_error(versions: Document | None, *, want_versions: bool) -> str | None:
    if not want_versions or versions is None:
        return None
    return versions.error


def _retryable(status: int | None) -> bool:
    return status is not None and (status == 429 or status >= 500)


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


def _metadata_body(source: dict[str, object], *, fallback_name: str, http_status: int, status: str, detail: str) -> str:
    name = _text(source, "name")
    payload = {
        "name": fallback_name if name == "" else name,
        "visibility": _text(source, "visibility"),
        "description": _description(source),
        "created_at": _text(source, "created_at"),
        "updated_at": _text(source, "updated_at"),
        "repository": _repository(source),
        "http_status": http_status,
        "status": status,
        "detail": detail,
    }
    return json.dumps(payload)


def _repository(body: dict[str, object]) -> str:
    value = body.get("repository")
    if isinstance(value, str):
        return value
    mapping = _mapping(value)
    if mapping is None:
        return ""
    html_url = mapping.get("html_url")
    if isinstance(html_url, str) and html_url != "":
        return html_url
    full_name = mapping.get("full_name")
    if isinstance(full_name, str):
        return full_name
    return ""


def _description(body: dict[str, object]) -> str:
    value = body.get("description")
    if isinstance(value, str):
        return value
    mapping = _mapping(body.get("repository"))
    if mapping is None:
        return ""
    nested = mapping.get("description")
    if isinstance(nested, str):
        return nested
    return ""


def _text(body: dict[str, object], key: str) -> str:
    value = body.get(key)
    if isinstance(value, str):
        return value
    return ""


def _mapping(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    mapped: dict[str, object] = {}
    for key, item in value.items():
        if isinstance(key, str):
            mapped[key] = item
    return mapped
