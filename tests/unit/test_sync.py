import asyncio
import json
from pathlib import Path
import sqlite3
import subprocess

import httpx
import pytest

from lupaxa.ghcr_intel.auth import token_from_gh
from lupaxa.ghcr_intel.config.models import AppConfig, PackageSpec, ProfileSpec
from lupaxa.ghcr_intel.config.profiles import ConfigProfiles, resolve_profile
from lupaxa.ghcr_intel.errors import AuthError, UsageError
from lupaxa.ghcr_intel.ghcr.packages import PackageFetch
from lupaxa.ghcr_intel.ghcr.registry import ManifestFetch
from lupaxa.ghcr_intel.storage.database import Database, Document
from lupaxa.ghcr_intel.sync.engine import SyncReport, synchronise
from lupaxa.ghcr_intel.views import ImageView, build_view, detail_rows, field_text

_TOKEN = "t"
_NOW = 1_700_000_000.0
_OLD = "2020-01-01T00:00:00Z"
_TAGGED: list[object] = [
    {
        "name": "sha256:tagged",
        "created_at": _OLD,
        "metadata": {"container": {"tags": ["1.0"]}},
    },
]


def _config(
    *,
    metadata: bool = True,
    tags: bool = False,
    digests: bool = False,
    architectures: bool = False,
    stale: bool = False,
    retries: int = 0,
    interval: int = 0,
    concurrency: int = 10,
    cache_enabled: bool = True,
) -> AppConfig:
    return AppConfig.model_validate(
        {
            "version": 1,
            "defaults": {
                "metadata": metadata,
                "tags": tags,
                "digests": digests,
                "architectures": architectures,
                "stale": stale,
            },
            "sync": {
                "retries": retries,
                "interval": interval,
                "concurrency": concurrency,
                "cache": {"enabled": cache_enabled},
            },
        }
    )


config = _config()
one = PackageSpec(owner="octocat", name="one")
two = PackageSpec(owner="octocat", name="two")


def _fetched(
    name: str = "one", *, status: int = 200, error: str | None = None, versions: list[object] | None = None, versions_error: str | None = None, versions_status: int | None = None
) -> PackageFetch:
    body = None
    if status == 200 and error is None:
        body = {
            "name": name,
            "visibility": "public",
            "description": "hello",
            "created_at": _OLD,
            "updated_at": _OLD,
            "repository": {"html_url": f"https://github.com/octocat/{name}"},
        }
    return PackageFetch(status, body, error, versions, versions_error, "org", versions_status)


def _runs(database: Database) -> list[tuple[int, float | None]]:
    connection = sqlite3.connect(database.path)
    try:
        rows = connection.execute("SELECT id, finished_at FROM sync_runs ORDER BY id").fetchall()
    finally:
        connection.close()
    return [(int(row[0]), row[1]) for row in rows]


def _insert_run(database: Database, finished_at: float) -> None:
    connection = sqlite3.connect(database.path)
    try:
        connection.execute(
            """
            INSERT INTO sync_runs (started_at, finished_at, stats_json)
            VALUES (?, ?, ?)
            """,
            (finished_at - 10, finished_at, "{}"),
        )
        connection.commit()
    finally:
        connection.close()


async def _refuse_package(*_args: object, **_kwargs: object) -> PackageFetch:
    raise AssertionError("package was fetched")


async def _refuse_manifest(*_args: object, **_kwargs: object) -> ManifestFetch:
    raise AssertionError("manifest was fetched")


def _stored_view(config: AppConfig, database: Database) -> ImageView:
    profiles = ConfigProfiles(profiles={"standard": ProfileSpec()}, defaults=config.defaults)
    documents: list[Document] = []
    for category in ("metadata", "versions", "manifest"):
        document = database.get("octocat/one", category)
        if document is not None:
            documents.append(document)
    return build_view(config, one, resolve_profile(profiles, one.profile, one.checks), documents, now=_NOW)


def test_documents_round_trip(tmp_path: Path) -> None:
    database = Database(tmp_path / "cache.sqlite")
    assert database.get("octocat/one", "metadata") is None
    database.put("octocat/one", "metadata", body="{}", fetched_at=10.0, error=None)
    assert database.get("octocat/one", "metadata") == Document("octocat/one", "metadata", "{}", 10.0, None)
    database.put("octocat/one", "metadata", body='{"a": 1}', fetched_at=11.0, error="x")
    replaced = database.get("octocat/one", "metadata")
    assert replaced is not None
    assert replaced.body == '{"a": 1}'
    assert replaced.fetched_at == 11.0
    assert replaced.error == "x"
    assert database.get("octocat/one", "versions") is None


@pytest.mark.asyncio
async def test_fresh_metadata_is_not_refetched(tmp_path: Path) -> None:
    now = _NOW
    database = Database(tmp_path / "cache.sqlite")
    database.put("octocat/one", "metadata", body="{}", fetched_at=now - 10, error=None)

    report = await synchronise(config, (one,), database=database, token=_TOKEN, force=False, now=now, fetch_package=_refuse_package, fetch_manifest=None)
    assert report.incomplete is False
    stored = database.get("octocat/one", "metadata")
    assert stored is not None
    assert stored.body == "{}"
    assert stored.fetched_at == now - 10


@pytest.mark.asyncio
async def test_one_failure_continues(tmp_path: Path) -> None:
    seen: list[str] = []

    async def fetch_package(*_args: object, owner: str, name: str, **_kwargs: object) -> PackageFetch:
        seen.append(name)
        assert owner == "octocat"
        status = 500 if name == "one" else 200
        error = "failed" if status == 500 else None
        return PackageFetch(status, None, error, None, None, "org")

    database = Database(tmp_path / "cache.sqlite")
    report = await synchronise(config, (one, two), database=database, token=_TOKEN, force=True, now=_NOW, fetch_package=fetch_package, fetch_manifest=None)
    assert seen == ["one", "two"]
    assert report.incomplete is True
    failed = database.get("octocat/one", "metadata")
    assert failed is not None
    assert failed.error == "failed"
    succeeded = database.get("octocat/two", "metadata")
    assert succeeded is not None
    assert succeeded.error is None


@pytest.mark.asyncio
async def test_interval_blocks_a_second_sync(tmp_path: Path) -> None:
    database = Database(tmp_path / "cache.sqlite")
    _insert_run(database, _NOW - 10)
    blocking = _config(interval=900)

    with pytest.raises(UsageError, match="interval"):
        await synchronise(blocking, (one,), database=database, token=_TOKEN, force=False, now=_NOW, fetch_package=_refuse_package, fetch_manifest=None)
    assert database.get("octocat/one", "metadata") is None
    assert len(_runs(database)) == 1

    calls = 0

    async def fetch_package(*_args: object, **_kwargs: object) -> PackageFetch:
        nonlocal calls
        calls += 1
        return _fetched()

    report = await synchronise(blocking, (one,), database=database, token=_TOKEN, force=True, now=_NOW, fetch_package=fetch_package, fetch_manifest=None)
    assert calls == 1
    assert report.incomplete is False
    runs = _runs(database)
    assert len(runs) == 2
    assert runs[1][1] == _NOW


@pytest.mark.asyncio
async def test_architectures_off_skips_the_manifest(tmp_path: Path) -> None:
    local = _config(tags=True, architectures=True)
    profiles = ConfigProfiles(profiles={"standard": ProfileSpec(architectures=False)}, defaults=local.defaults)
    manifest_calls = 0

    async def fetch_package(*_args: object, **_kwargs: object) -> PackageFetch:
        return _fetched(versions=list(_TAGGED))

    async def fetch_manifest(*_args: object, **_kwargs: object) -> ManifestFetch:
        nonlocal manifest_calls
        manifest_calls += 1
        raise AssertionError("manifest was fetched")

    report = await synchronise(
        local, (one,), database=Database(tmp_path / "cache.sqlite"), token=_TOKEN, force=False, now=_NOW, fetch_package=fetch_package, fetch_manifest=fetch_manifest, profiles=profiles
    )
    assert manifest_calls == 0
    assert report.incomplete is False


@pytest.mark.asyncio
async def test_missing_token_raises_before_fetch(tmp_path: Path) -> None:
    database = Database(tmp_path / "cache.sqlite")
    with pytest.raises(AuthError, match="token"):
        await synchronise(config, (one,), database=database, token=None, force=True, now=_NOW, fetch_package=_refuse_package, fetch_manifest=_refuse_manifest)
    assert database.get("octocat/one", "metadata") is None
    assert _runs(database) == []


@pytest.mark.asyncio
async def test_disabled_stale_check_passes_no_stale_result(tmp_path: Path) -> None:
    database = Database(tmp_path / "cache.sqlite")

    async def fetch_package(*_args: object, **_kwargs: object) -> PackageFetch:
        return _fetched(versions=list(_TAGGED))

    report = await synchronise(_config(tags=True, stale=False), (one,), database=database, token=_TOKEN, force=False, now=_NOW, fetch_package=fetch_package, fetch_manifest=_refuse_manifest)
    assert report.incomplete is False
    stored = database.get("octocat/one", "metadata")
    assert stored is not None
    body = json.loads(stored.body)
    assert body["status"] == "Current"
    assert body["detail"] == "Metadata was read."
    assert body["repository"] == "https://github.com/octocat/one"
    assert body["visibility"] == "public"
    assert body["description"] == "hello"
    versions = database.get("octocat/one", "versions")
    assert versions is not None
    assert json.loads(versions.body) == _TAGGED
    assert versions.error is None


@pytest.mark.asyncio
async def test_enabled_stale_check_stores_the_stale_sentence(tmp_path: Path) -> None:
    database = Database(tmp_path / "cache.sqlite")

    async def fetch_package(*_args: object, **_kwargs: object) -> PackageFetch:
        return _fetched(versions=list(_TAGGED))

    await synchronise(_config(tags=True, stale=True), (one,), database=database, token=_TOKEN, force=False, now=_NOW, fetch_package=fetch_package, fetch_manifest=_refuse_manifest)
    stored = database.get("octocat/one", "metadata")
    assert stored is not None
    body = json.loads(stored.body)
    assert body["status"] == "Stale"
    assert "days" in body["detail"]
    assert "Stale" not in body["detail"]


@pytest.mark.asyncio
async def test_transport_error_is_stored_and_the_next_image_continues(tmp_path: Path) -> None:
    seen: list[str] = []

    async def fetch_package(*_args: object, name: str, **_kwargs: object) -> PackageFetch:
        seen.append(name)
        if name == "one":
            raise httpx.ConnectError("boom")
        return _fetched(name)

    database = Database(tmp_path / "cache.sqlite")
    report = await synchronise(config, (one, two), database=database, token=_TOKEN, force=True, now=_NOW, fetch_package=fetch_package, fetch_manifest=_refuse_manifest)
    assert seen == ["one", "two"]
    assert report.incomplete is True
    failed = database.get("octocat/one", "metadata")
    assert failed is not None
    assert failed.error == "boom"
    assert database.get("octocat/two", "metadata") is not None


@pytest.mark.asyncio
async def test_invalid_url_is_stored_and_the_next_image_continues(tmp_path: Path) -> None:
    seen: list[str] = []

    async def fetch_package(*_args: object, name: str, **_kwargs: object) -> PackageFetch:
        seen.append(name)
        if name == "one":
            raise httpx.InvalidURL("not a url")
        return _fetched(name)

    database = Database(tmp_path / "cache.sqlite")
    report = await synchronise(config, (one, two), database=database, token=_TOKEN, force=True, now=_NOW, fetch_package=fetch_package, fetch_manifest=_refuse_manifest)
    assert seen == ["one", "two"]
    assert report.incomplete is True
    failed = database.get("octocat/one", "metadata")
    assert failed is not None
    assert failed.error == "not a url"


@pytest.mark.asyncio
async def test_retry_repeats_a_server_error_then_recovers(tmp_path: Path) -> None:
    calls = 0

    async def fetch_package(*_args: object, **_kwargs: object) -> PackageFetch:
        nonlocal calls
        calls += 1
        if calls < 3:
            return PackageFetch(500, None, "failed", None, None, "org")
        return _fetched()

    report = await synchronise(
        _config(retries=2), (one,), database=Database(tmp_path / "cache.sqlite"), token=_TOKEN, force=True, now=_NOW, fetch_package=fetch_package, fetch_manifest=_refuse_manifest
    )
    assert calls == 3
    assert report.incomplete is False


@pytest.mark.asyncio
async def test_retry_repeats_a_versions_server_error_then_recovers(tmp_path: Path) -> None:
    calls = 0

    async def fetch_package(*_args: object, **_kwargs: object) -> PackageFetch:
        nonlocal calls
        calls += 1
        if calls < 3:
            return _fetched(versions_error="failed", versions_status=503)
        return _fetched(versions=[], versions_status=200)

    report = await synchronise(
        _config(tags=True, retries=2), (one,), database=Database(tmp_path / "cache.sqlite"), token=_TOKEN, force=True, now=_NOW, fetch_package=fetch_package, fetch_manifest=_refuse_manifest
    )
    assert calls == 3
    assert report.incomplete is False


@pytest.mark.asyncio
async def test_exhausted_retries_store_the_error(tmp_path: Path) -> None:
    calls = 0

    async def fetch_package(*_args: object, **_kwargs: object) -> PackageFetch:
        nonlocal calls
        calls += 1
        return PackageFetch(429, None, "failed", None, None, "org")

    database = Database(tmp_path / "cache.sqlite")
    report = await synchronise(_config(retries=2), (one,), database=database, token=_TOKEN, force=True, now=_NOW, fetch_package=fetch_package, fetch_manifest=_refuse_manifest)
    assert calls == 3
    assert report.incomplete is True
    stored = database.get("octocat/one", "metadata")
    assert stored is not None
    assert stored.error == "failed"


@pytest.mark.asyncio
async def test_server_error_keeps_the_last_good_metadata(tmp_path: Path) -> None:
    database = Database(tmp_path / "cache.sqlite")
    good = '{"name": "one"}'
    database.put("octocat/one", "metadata", body=good, fetched_at=_NOW - 10_000, error=None)
    calls = 0

    async def fetch_package(*_args: object, **_kwargs: object) -> PackageFetch:
        nonlocal calls
        calls += 1
        return PackageFetch(500, None, "failed", None, None, "org")

    failed = await synchronise(config, (one,), database=database, token=_TOKEN, force=False, now=_NOW, fetch_package=fetch_package, fetch_manifest=_refuse_manifest)
    assert calls == 1
    assert failed.incomplete is True
    stored = database.get("octocat/one", "metadata")
    assert stored is not None
    assert stored.body == good
    assert stored.error is None
    assert stored.fetched_at == _NOW - 10_000

    again = await synchronise(config, (one,), database=database, token=_TOKEN, force=False, now=_NOW, fetch_package=fetch_package, fetch_manifest=_refuse_manifest)
    assert calls == 2
    assert again.incomplete is True
    stored = database.get("octocat/one", "metadata")
    assert stored is not None
    assert stored.body == good


@pytest.mark.asyncio
async def test_exhausted_versions_retries_keep_the_last_good_row(tmp_path: Path) -> None:
    database = Database(tmp_path / "cache.sqlite")
    good = json.dumps(_TAGGED)
    database.put("octocat/one", "metadata", body="{}", fetched_at=_NOW - 10, error=None)
    database.put("octocat/one", "versions", body=good, fetched_at=_NOW - 10_000, error=None)
    calls = 0

    async def fetch_package(*_args: object, **_kwargs: object) -> PackageFetch:
        nonlocal calls
        calls += 1
        return _fetched(versions_error="failed", versions_status=503)

    report = await synchronise(_config(tags=True, retries=2), (one,), database=database, token=_TOKEN, force=False, now=_NOW, fetch_package=fetch_package, fetch_manifest=_refuse_manifest)
    assert calls == 3
    assert report.incomplete is True
    stored = database.get("octocat/one", "versions")
    assert stored is not None
    assert stored.body == good
    assert stored.error is None
    assert stored.fetched_at == _NOW - 10_000


@pytest.mark.asyncio
async def test_transport_error_keeps_the_last_good_metadata(tmp_path: Path) -> None:
    database = Database(tmp_path / "cache.sqlite")
    good = '{"name": "one"}'
    database.put("octocat/one", "metadata", body=good, fetched_at=_NOW - 10_000, error=None)

    async def fetch_package(*_args: object, **_kwargs: object) -> PackageFetch:
        raise httpx.ConnectError("boom")

    report = await synchronise(config, (one,), database=database, token=_TOKEN, force=False, now=_NOW, fetch_package=fetch_package, fetch_manifest=_refuse_manifest)
    assert report.incomplete is True
    stored = database.get("octocat/one", "metadata")
    assert stored is not None
    assert stored.body == good
    assert stored.error is None
    assert stored.fetched_at == _NOW - 10_000


@pytest.mark.asyncio
async def test_not_found_is_not_retried_or_incomplete(tmp_path: Path) -> None:
    calls = 0

    async def fetch_package(*_args: object, **_kwargs: object) -> PackageFetch:
        nonlocal calls
        calls += 1
        return PackageFetch(404, None, "missing", None, None, "org")

    database = Database(tmp_path / "cache.sqlite")
    report = await synchronise(_config(retries=3), (one,), database=database, token=_TOKEN, force=True, now=_NOW, fetch_package=fetch_package, fetch_manifest=_refuse_manifest)
    assert calls == 1
    assert report.incomplete is False
    stored = database.get("octocat/one", "metadata")
    assert stored is not None
    assert stored.error == "missing"
    body = json.loads(stored.body)
    assert body["status"] == "Unavailable"
    assert body["detail"] == "GitHub could not find this image."


@pytest.mark.asyncio
async def test_finished_versions_answer_is_complete(tmp_path: Path) -> None:
    calls = 0

    async def fetch_package(*_args: object, **_kwargs: object) -> PackageFetch:
        nonlocal calls
        calls += 1
        return _fetched(versions_error="failed", versions_status=403)

    database = Database(tmp_path / "forbidden.sqlite")
    report = await synchronise(_config(tags=True, retries=2), (one,), database=database, token=_TOKEN, force=True, now=_NOW, fetch_package=fetch_package, fetch_manifest=_refuse_manifest)
    assert calls == 1
    assert report.incomplete is False
    stored = database.get("octocat/one", "metadata")
    assert stored is not None
    assert json.loads(stored.body)["status"] == "Error"
    versions = database.get("octocat/one", "versions")
    assert versions is not None
    assert versions.error == "failed"

    calls = 0

    async def exhaust(*_args: object, **_kwargs: object) -> PackageFetch:
        nonlocal calls
        calls += 1
        return _fetched(versions_error="failed", versions_status=503)

    failed = Database(tmp_path / "retry.sqlite")
    again = await synchronise(_config(tags=True, retries=2), (one,), database=failed, token=_TOKEN, force=True, now=_NOW, fetch_package=exhaust, fetch_manifest=_refuse_manifest)
    assert calls == 3
    assert again.incomplete is True


@pytest.mark.asyncio
async def test_metadata_off_forbidden_keeps_status(tmp_path: Path) -> None:
    database = Database(tmp_path / "cache.sqlite")
    local = _config(metadata=False, tags=True)

    async def fetch_package(*_args: object, **_kwargs: object) -> PackageFetch:
        return _fetched(status=403, error="The token cannot read this image.")

    report = await synchronise(local, (one,), database=database, token=_TOKEN, force=True, now=_NOW, fetch_package=fetch_package, fetch_manifest=_refuse_manifest)
    view = _stored_view(local, database)
    assert report.incomplete is False
    assert view.status == "Forbidden"
    assert view.status_detail == "The token cannot read this image."
    assert view.metadata_error == ""
    assert field_text(view, "Visibility", detail=False) == ""
    assert field_text(view, "Visibility", detail=True) == ("SKIPPED — metadata is disabled.")
    assert all(label != "Metadata error" for label, _value in detail_rows(view))


@pytest.mark.asyncio
async def test_metadata_off_missing_package_is_unavailable(tmp_path: Path) -> None:
    database = Database(tmp_path / "cache.sqlite")
    local = _config(metadata=False, tags=True)

    async def fetch_package(*_args: object, **_kwargs: object) -> PackageFetch:
        return _fetched(status=404, error="missing")

    report = await synchronise(local, (one,), database=database, token=_TOKEN, force=True, now=_NOW, fetch_package=fetch_package, fetch_manifest=_refuse_manifest)
    view = _stored_view(local, database)
    assert report.incomplete is False
    assert view.status == "Unavailable"
    assert view.status_detail == "GitHub could not find this image."
    assert view.metadata_error == ""
    assert field_text(view, "Visibility", detail=False) == ""


@pytest.mark.asyncio
async def test_manifest_is_stored_when_architectures_is_on(tmp_path: Path) -> None:
    async def fetch_package(*_args: object, **_kwargs: object) -> PackageFetch:
        return _fetched(versions=list(_TAGGED))

    async def fetch_manifest(*_args: object, tag: str, **_kwargs: object) -> ManifestFetch:
        assert tag == "1.0"
        return ManifestFetch(("linux/amd64", "linux/arm/v7"), None)

    database = Database(tmp_path / "cache.sqlite")
    report = await synchronise(_config(tags=True, architectures=True), (one,), database=database, token=_TOKEN, force=False, now=_NOW, fetch_package=fetch_package, fetch_manifest=fetch_manifest)
    assert report.incomplete is False
    stored = database.get("octocat/one", "manifest")
    assert stored is not None
    assert stored.error is None
    assert json.loads(stored.body)["platforms"] == ["linux/amd64", "linux/arm/v7"]


@pytest.mark.asyncio
async def test_empty_newest_tag_skips_the_manifest(tmp_path: Path) -> None:
    async def fetch_package(*_args: object, **_kwargs: object) -> PackageFetch:
        return _fetched(
            versions=[
                {
                    "name": "sha256:plain",
                    "created_at": _OLD,
                    "metadata": {"container": {"tags": []}},
                },
            ]
        )

    report = await synchronise(
        _config(tags=True, architectures=True), (one,), database=Database(tmp_path / "cache.sqlite"), token=_TOKEN, force=False, now=_NOW, fetch_package=fetch_package, fetch_manifest=_refuse_manifest
    )
    assert report.incomplete is False


@pytest.mark.asyncio
async def test_fresh_manifest_is_not_refetched(tmp_path: Path) -> None:
    database = Database(tmp_path / "cache.sqlite")
    database.put("octocat/one", "metadata", body="{}", fetched_at=_NOW - 10, error=None)
    database.put("octocat/one", "versions", body=json.dumps(_TAGGED), fetched_at=_NOW - 10, error=None)
    database.put("octocat/one", "manifest", body='{"platforms": ["linux/amd64"]}', fetched_at=_NOW - 10, error=None)
    report = await synchronise(_config(tags=True, architectures=True), (one,), database=database, token=_TOKEN, force=False, now=_NOW, fetch_package=_refuse_package, fetch_manifest=_refuse_manifest)
    assert report.incomplete is False
    stored = database.get("octocat/one", "manifest")
    assert stored is not None
    assert stored.fetched_at == _NOW - 10


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", ["tags", "digests", "architectures", "stale"])
async def test_version_checks_request_versions(flag: str, tmp_path: Path) -> None:
    seen: dict[str, object] = {}

    async def fetch_package(*_args: object, **kwargs: object) -> PackageFetch:
        seen.update(kwargs)
        return _fetched(versions=[])

    await synchronise(_config(**{flag: True}), (one,), database=Database(tmp_path / "cache.sqlite"), token=_TOKEN, force=False, now=_NOW, fetch_package=fetch_package, fetch_manifest=_refuse_manifest)
    assert seen["want_versions"] is True


@pytest.mark.asyncio
async def test_metadata_only_does_not_request_versions(tmp_path: Path) -> None:
    seen: dict[str, object] = {}

    async def fetch_package(*_args: object, **kwargs: object) -> PackageFetch:
        seen.update(kwargs)
        return _fetched()

    await synchronise(config, (one,), database=Database(tmp_path / "cache.sqlite"), token=_TOKEN, force=False, now=_NOW, fetch_package=fetch_package, fetch_manifest=_refuse_manifest)
    assert seen["want_versions"] is False
    assert seen["owner"] == "octocat"
    assert seen["name"] == "one"
    assert seen["version_limit"] == 100


@pytest.mark.asyncio
async def test_stale_versions_refetch_even_when_metadata_is_fresh(tmp_path: Path) -> None:
    database = Database(tmp_path / "cache.sqlite")
    database.put("octocat/one", "metadata", body="{}", fetched_at=_NOW - 10, error=None)

    async def fetch_package(*_args: object, **kwargs: object) -> PackageFetch:
        assert kwargs["want_versions"] is True
        return _fetched(versions=list(_TAGGED))

    await synchronise(_config(tags=True), (one,), database=database, token=_TOKEN, force=False, now=_NOW, fetch_package=fetch_package, fetch_manifest=_refuse_manifest)
    stored = database.get("octocat/one", "metadata")
    assert stored is not None
    assert stored.fetched_at == _NOW
    versions = database.get("octocat/one", "versions")
    assert versions is not None
    assert json.loads(versions.body) == _TAGGED


@pytest.mark.asyncio
async def test_fresh_versions_are_not_requested_again(tmp_path: Path) -> None:
    database = Database(tmp_path / "cache.sqlite")
    database.put("octocat/one", "metadata", body="{}", fetched_at=_NOW - 10_000, error=None)
    database.put("octocat/one", "versions", body='["keep"]', fetched_at=_NOW - 10, error=None)

    async def fetch_package(*_args: object, **kwargs: object) -> PackageFetch:
        assert kwargs["want_versions"] is False
        return _fetched(versions=[{"name": "replaced"}])

    await synchronise(_config(tags=True), (one,), database=database, token=_TOKEN, force=False, now=_NOW, fetch_package=fetch_package, fetch_manifest=_refuse_manifest)
    versions = database.get("octocat/one", "versions")
    assert versions is not None
    assert versions.body == '["keep"]'
    assert versions.fetched_at == _NOW - 10
    metadata = database.get("octocat/one", "metadata")
    assert metadata is not None
    assert metadata.fetched_at == _NOW


@pytest.mark.asyncio
async def test_disabled_cache_refetches_fresh_metadata(tmp_path: Path) -> None:
    database = Database(tmp_path / "cache.sqlite")
    database.put("octocat/one", "metadata", body="{}", fetched_at=_NOW - 10, error=None)
    calls = 0

    async def fetch_package(*_args: object, **_kwargs: object) -> PackageFetch:
        nonlocal calls
        calls += 1
        return _fetched()

    await synchronise(_config(cache_enabled=False), (one,), database=database, token=_TOKEN, force=False, now=_NOW, fetch_package=fetch_package, fetch_manifest=_refuse_manifest)
    assert calls == 1


@pytest.mark.asyncio
async def test_interval_zero_does_not_block(tmp_path: Path) -> None:
    database = Database(tmp_path / "cache.sqlite")
    _insert_run(database, _NOW - 10)
    calls = 0

    async def fetch_package(*_args: object, **_kwargs: object) -> PackageFetch:
        nonlocal calls
        calls += 1
        return _fetched()

    report = await synchronise(_config(interval=0), (one,), database=database, token=_TOKEN, force=False, now=_NOW, fetch_package=fetch_package, fetch_manifest=_refuse_manifest)
    assert calls == 1
    assert report.incomplete is False


@pytest.mark.asyncio
async def test_forbidden_manifest_is_complete(tmp_path: Path) -> None:
    calls = 0

    async def fetch_package(*_args: object, **_kwargs: object) -> PackageFetch:
        return _fetched(versions=list(_TAGGED))

    async def fetch_manifest(*_args: object, **_kwargs: object) -> ManifestFetch:
        nonlocal calls
        calls += 1
        return ManifestFetch((), "Forbidden")

    database = Database(tmp_path / "cache.sqlite")
    report = await synchronise(
        _config(tags=True, architectures=True, retries=3), (one,), database=database, token=_TOKEN, force=False, now=_NOW, fetch_package=fetch_package, fetch_manifest=fetch_manifest
    )
    assert calls == 1
    assert report.incomplete is False
    stored = database.get("octocat/one", "manifest")
    assert stored is not None
    assert stored.error == "Forbidden"
    metadata = database.get("octocat/one", "metadata")
    assert metadata is not None
    assert json.loads(metadata.body)["status"] == "Current"


@pytest.mark.asyncio
async def test_unknown_manifest_is_retried(tmp_path: Path) -> None:
    calls = 0

    async def fetch_package(*_args: object, **_kwargs: object) -> PackageFetch:
        return _fetched(versions=list(_TAGGED))

    async def fetch_manifest(*_args: object, **_kwargs: object) -> ManifestFetch:
        nonlocal calls
        calls += 1
        return ManifestFetch((), "Unknown")

    database = Database(tmp_path / "cache.sqlite")
    report = await synchronise(
        _config(tags=True, architectures=True, retries=2), (one,), database=database, token=_TOKEN, force=False, now=_NOW, fetch_package=fetch_package, fetch_manifest=fetch_manifest
    )
    assert calls == 3
    assert report.incomplete is True
    stored = database.get("octocat/one", "manifest")
    assert stored is not None
    assert stored.error == "Unknown"


@pytest.mark.asyncio
async def test_unknown_manifest_keeps_the_last_good_row(tmp_path: Path) -> None:
    database = Database(tmp_path / "cache.sqlite")
    good = '{"platforms": ["linux/amd64"]}'
    database.put("octocat/one", "metadata", body="{}", fetched_at=_NOW - 10, error=None)
    database.put("octocat/one", "versions", body=json.dumps(_TAGGED), fetched_at=_NOW - 10, error=None)
    database.put("octocat/one", "manifest", body=good, fetched_at=_NOW - 100_000, error=None)

    async def fetch_manifest(*_args: object, **_kwargs: object) -> ManifestFetch:
        return ManifestFetch((), "Unknown")

    report = await synchronise(_config(tags=True, architectures=True), (one,), database=database, token=_TOKEN, force=False, now=_NOW, fetch_package=_refuse_package, fetch_manifest=fetch_manifest)
    assert report.incomplete is True
    stored = database.get("octocat/one", "manifest")
    assert stored is not None
    assert stored.body == good
    assert stored.error is None
    assert stored.fetched_at == _NOW - 100_000


def test_token_from_gh_returns_stripped_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert args == ["gh", "auth", "token"]
        assert kwargs.get("shell") is not True
        assert kwargs.get("check") is False
        assert kwargs.get("capture_output") is True
        return subprocess.CompletedProcess(args, 0, stdout="  secret\n", stderr="")

    monkeypatch.setattr("lupaxa.ghcr_intel.auth.subprocess.run", fake_run)
    assert token_from_gh() == "secret"


def test_token_from_gh_rejects_a_failed_command(monkeypatch: pytest.MonkeyPatch) -> None:
    leaked = "ghp_should_not_leak"

    def failed(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 1, stdout=leaked, stderr="no")

    monkeypatch.setattr("lupaxa.ghcr_intel.auth.subprocess.run", failed)
    with pytest.raises(AuthError, match="token") as caught:
        token_from_gh()
    assert leaked not in str(caught.value)

    def empty(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 0, stdout="  \n", stderr="")

    monkeypatch.setattr("lupaxa.ghcr_intel.auth.subprocess.run", empty)
    with pytest.raises(AuthError, match="token"):
        token_from_gh()

    def missing(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise OSError("gh is not installed")

    monkeypatch.setattr("lupaxa.ghcr_intel.auth.subprocess.run", missing)
    with pytest.raises(AuthError, match="token") as missing_token:
        token_from_gh()
    assert "gh is not installed" not in str(missing_token.value)


@pytest.mark.asyncio
async def test_sync_reports_progress_as_each_image_finishes(tmp_path: Path) -> None:
    seen: list[tuple[str, int]] = []

    async def fetch_package(*_args: object, owner: str, name: str, **_kwargs: object) -> PackageFetch:
        assert owner == "octocat"
        return _fetched(name)

    def on_progress(report: SyncReport) -> None:
        seen.append((report.latest, report.successful + report.failed))

    database = Database(tmp_path / "cache.sqlite")
    report = await synchronise(config, (one, two), database=database, token=_TOKEN, force=True, now=_NOW, fetch_package=fetch_package, on_progress=on_progress)
    assert report.successful == 2
    assert report.failed == 0
    assert report.requests == 2
    assert report.images == 2
    assert seen[0] == ("", 0)
    assert ("octocat/one", 0) in seen
    assert ("octocat/one", 1) in seen
    assert seen[-1] == ("octocat/two", 2)


@pytest.mark.asyncio
async def test_sync_runs_images_together_up_to_concurrency(tmp_path: Path) -> None:
    started: list[str] = []
    both = asyncio.Event()
    release = asyncio.Event()

    async def fetch_package(*_args: object, name: str, **_kwargs: object) -> PackageFetch:
        started.append(name)
        if len(started) == 2:
            both.set()
        await release.wait()
        return _fetched(name)

    database = Database(tmp_path / "cache.sqlite")
    task = asyncio.create_task(synchronise(_config(concurrency=2), (one, two), database=database, token=_TOKEN, force=True, now=_NOW, fetch_package=fetch_package))
    await asyncio.wait_for(both.wait(), timeout=1)
    assert started == ["one", "two"]
    release.set()
    report = await task
    assert report.successful == 2
    assert report.failed == 0


@pytest.mark.asyncio
async def test_sync_concurrency_one_finishes_an_image_before_the_next(tmp_path: Path) -> None:
    started: list[str] = []
    first = asyncio.Event()
    release = asyncio.Event()

    async def fetch_package(*_args: object, name: str, **_kwargs: object) -> PackageFetch:
        started.append(name)
        if name == "one":
            first.set()
            await release.wait()
        return _fetched(name)

    database = Database(tmp_path / "cache.sqlite")
    task = asyncio.create_task(synchronise(_config(concurrency=1), (one, two), database=database, token=_TOKEN, force=True, now=_NOW, fetch_package=fetch_package))
    await asyncio.wait_for(first.wait(), timeout=1)
    assert started == ["one"]
    release.set()
    report = await task
    assert started == ["one", "two"]
    assert report.successful == 2


@pytest.mark.asyncio
async def test_concurrency_argument_overrides_the_file(tmp_path: Path) -> None:
    started: list[str] = []
    first = asyncio.Event()
    release = asyncio.Event()

    async def fetch_package(*_args: object, name: str, **_kwargs: object) -> PackageFetch:
        started.append(name)
        if name == "one":
            first.set()
            await release.wait()
        return _fetched(name)

    database = Database(tmp_path / "cache.sqlite")
    task = asyncio.create_task(synchronise(_config(concurrency=10), (one, two), database=database, token=_TOKEN, force=True, now=_NOW, fetch_package=fetch_package, concurrency=1))
    await asyncio.wait_for(first.wait(), timeout=1)
    assert started == ["one"]
    release.set()
    await task


@pytest.mark.asyncio
async def test_disabled_image_is_not_requested(tmp_path: Path) -> None:
    calls: list[str] = []

    async def fetch_package(*_args: object, name: str, **_kwargs: object) -> PackageFetch:
        calls.append(name)
        return _fetched(name)

    quiet = PackageSpec(owner="octocat", name="quiet", enabled=False)
    report = await synchronise(_config(), (one, quiet), database=Database(tmp_path / "cache.sqlite"), token=_TOKEN, force=True, now=_NOW, fetch_package=fetch_package, fetch_manifest=_refuse_manifest)
    assert calls == ["one"]
    assert report.images == 1
    assert report.skipped == 1
    assert report.successful == 1


@pytest.mark.asyncio
async def test_retry_after_pauses_and_the_status_names_the_wait(tmp_path: Path) -> None:
    from lupaxa.ghcr_intel.reports.progress import status_line

    calls = 0
    lines: list[str] = []

    async def fetch_package(*_args: object, **_kwargs: object) -> PackageFetch:
        nonlocal calls
        calls += 1
        if calls == 1:
            return PackageFetch(429, None, "failed", None, None, "org", retry_after=0.05)
        return _fetched()

    def on_progress(report: SyncReport) -> None:
        lines.append(status_line(report))

    report = await synchronise(
        _config(retries=1),
        (one,),
        database=Database(tmp_path / "cache.sqlite"),
        token=_TOKEN,
        force=True,
        now=_NOW,
        fetch_package=fetch_package,
        fetch_manifest=_refuse_manifest,
        on_progress=on_progress,
    )
    assert calls == 2
    assert report.incomplete is False
    assert report.rate_pauses == 1
    assert any("waiting for GitHub rate limit" in line for line in lines)
