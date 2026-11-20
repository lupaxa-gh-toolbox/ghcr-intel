from datetime import UTC, datetime
import json

import pytest

from lupaxa.ghcr_intel.config.models import AppConfig, PackageSpec
from lupaxa.ghcr_intel.config.profiles import ResolvedProfile
from lupaxa.ghcr_intel.present import paint
from lupaxa.ghcr_intel.storage.database import Document
from lupaxa.ghcr_intel.views import (
    ImageView,
    build_view,
    cache_word,
    detail_rows,
    field_text,
)


def _stamp(instant: float) -> str:
    return datetime.fromtimestamp(instant, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


_DAY = 86400
_NOW = 1_763_596_800.0
_OLD = "2020-01-01T00:00:00Z"
_RECENT = _stamp(_NOW - 3 * _DAY)
_PACKAGE = "octocat/hello-world"
_PLAIN = (
    "Created",
    "Updated",
    "Synchronised",
    "Newest tag",
    "Digest",
    "Architectures",
    "Linked repository",
)


def test_list_status_is_short_and_detail_is_the_sentence() -> None:
    view = _stale_view()
    assert field_text(view, "Status", detail=False) == "Stale"
    sentence = field_text(view, "Status", detail=True)
    assert "Stale" not in sentence
    assert "days" in sentence


def test_disabled_digest_is_blank_on_a_list_and_skipped_on_the_detail() -> None:
    view = _current_view(digests=False)
    assert field_text(view, "Digest", detail=False) == ""
    assert field_text(view, "Digest", detail=True) == "SKIPPED — digests is disabled."


def test_metadata_error_is_first_and_the_package_name_is_red() -> None:
    view = _current_view(metadata_error="boom")
    assert detail_rows(view)[0] == ("Metadata error", "boom")
    painted = paint(
        "Package",
        view.package,
        status=view.status,
        metadata_error="boom",
    )
    assert painted.style == "red"


def test_visibility_colours() -> None:
    assert (
        paint("Visibility", "public", status="Current", metadata_error="").style
        == "green"
    )
    assert (
        paint("Visibility", "private", status="Current", metadata_error="").style
        == "magenta"
    )
    assert (
        paint("Visibility", "internal", status="Current", metadata_error="").style
        == "cyan"
    )


def test_cache_word_follows_metadata_age() -> None:
    assert cache_word(fetched_at=None, now=100.0, ttl=3600) == "not-synchronised"
    assert cache_word(fetched_at=50.0, now=100.0, ttl=40) == "stale"
    assert cache_word(fetched_at=90.0, now=100.0, ttl=3600) == "cached"


@pytest.mark.parametrize("word", ["Forbidden", "Unavailable", "Unknown"])
def test_manifest_error_leaves_status_current(word: str) -> None:
    view = _current_view(manifest_error=word)
    assert field_text(view, "Status", detail=False) == "Current"
    assert field_text(view, "Architectures", detail=False) == word
    assert view.architectures_error == word


def test_status_colour_comes_from_the_short_word() -> None:
    sentence = field_text(_stale_view(), "Status", detail=True)
    painted = paint("Status", sentence, status="Stale", metadata_error="")
    assert painted.style == "yellow"
    assert "Stale" not in sentence
    assert paint("Status", "Current", status="Current", metadata_error="").style == (
        "green"
    )
    forbidden = paint("Status", "Forbidden", status="Forbidden", metadata_error="")
    assert forbidden.style == "yellow"
    assert paint("Status", "Error", status="Error", metadata_error="").style == "red"
    assert (
        paint("Status", "Unavailable", status="Unavailable", metadata_error="").style
        == "red"
    )
    assert (
        paint(
            "Status",
            "This image has not been synchronised.",
            status="Not synchronised",
            metadata_error="",
        ).style
        == "dim"
    )


def test_paint_follows_the_colour_table() -> None:
    assert paint("Enabled", "yes", status="Current", metadata_error="").style == "green"
    assert paint("Enabled", "no", status="Current", metadata_error="").style == "dim"
    assert (
        paint("Cache", "cached, error", status="Current", metadata_error="").style
        == "red"
    )
    assert (
        paint("Cache", "unavailable", status="Unavailable", metadata_error="").style
        == "red"
    )
    assert paint("Cache", "stale", status="Stale", metadata_error="").style == "yellow"
    assert paint("Cache", "cached", status="Current", metadata_error="").style == (
        "green"
    )
    assert (
        paint(
            "Cache",
            "not-synchronised",
            status="Not synchronised",
            metadata_error="",
        ).style
        == ""
    )
    assert paint("Stale", "yes", status="Stale", metadata_error="").style == "yellow"
    assert paint("Stale", "no", status="Current", metadata_error="").style == "green"
    skipped = "SKIPPED — digests is disabled."
    assert paint("Digest", skipped, status="Current", metadata_error="").style == "dim"
    assert (
        paint("Metadata error", "boom", status="Error", metadata_error="boom").style
        == "red"
    )
    for label in _PLAIN:
        assert paint(label, "plain", status="Current", metadata_error="").style == ""


def test_updated_uses_the_configured_date() -> None:
    view = _stale_view()
    assert field_text(view, "Updated", detail=False) == "01/01/2020 00:00 UTC"


def test_tags_join_and_truncation_is_a_word() -> None:
    view = _stale_view()
    assert field_text(view, "Tags", detail=False) == "demo, sample"
    assert field_text(view, "Image tags", detail=False) == "1.0, latest"
    assert field_text(view, "Versions truncated", detail=False) == "no"
    full = _current_view(version_limit=1, extra_version=True)
    assert field_text(full, "Versions truncated", detail=False) == "yes"
    assert field_text(full, "Versions fetched", detail=False) == "1"


def test_versions_cells_skip_when_the_request_did_not_run() -> None:
    view = _current_view(tags=False, digests=False, architectures=False, stale=False)
    assert field_text(view, "Versions fetched", detail=False) == ""
    assert field_text(view, "Versions fetched", detail=True) == (
        "SKIPPED — tags is disabled."
    )
    assert field_text(view, "Versions truncated", detail=True) == (
        "SKIPPED — tags is disabled."
    )


def test_versions_error_sets_status_and_the_tag_and_digest_cells() -> None:
    view = _current_view(versions_error="versions failed")
    assert field_text(view, "Status", detail=False) == "Error"
    assert field_text(view, "Status", detail=True) == "versions failed"
    assert field_text(view, "Newest tag", detail=False) == "Error"
    assert field_text(view, "Image tags", detail=True) == "Error"
    assert field_text(view, "Tag count", detail=False) == "Error"
    assert field_text(view, "Digest", detail=False) == "Error"


def test_fresh_stale_check_uses_the_window_sentence() -> None:
    view = _current_view(stale=True)
    assert field_text(view, "Status", detail=False) == "Current"
    assert field_text(view, "Status", detail=True) == (
        "The package and its newest tag are inside the stale window."
    )
    assert field_text(view, "Stale", detail=False) == "no"


def test_age_just_under_the_stale_window_stays_current() -> None:
    view = _view(
        updated=_stamp(_NOW - 180 * _DAY + 1),
        created=_stamp(_NOW - 90 * _DAY + 1),
        profile=_profile(stale=True),
    )
    assert field_text(view, "Status", detail=False) == "Current"
    assert field_text(view, "Status", detail=True) == (
        "The package and its newest tag are inside the stale window."
    )
    assert field_text(view, "Stale", detail=False) == "no"


def test_disabled_metadata_ignores_a_stored_error() -> None:
    view = _current_view(metadata=False, metadata_error="boom", http_status=500)
    assert view.metadata_error == ""
    assert all(label != "Metadata error" for label, _value in detail_rows(view))
    painted = paint(
        "Package",
        view.package,
        status=view.status,
        metadata_error=view.metadata_error,
    )
    assert painted.style != "red"
    cache = field_text(view, "Cache", detail=False)
    assert "error" not in cache
    assert "unavailable" not in cache
    assert field_text(view, "Status", detail=True) == "Metadata was read."


def test_disabled_checks_are_blank_on_a_list_and_skipped_on_the_detail() -> None:
    view = _current_view(architectures=False, metadata=False)
    assert field_text(view, "Architectures", detail=False) == ""
    assert field_text(view, "Architectures", detail=True) == (
        "SKIPPED — architectures is disabled."
    )
    assert field_text(view, "Visibility", detail=False) == ""
    assert field_text(view, "Linked repository", detail=True) == (
        "SKIPPED — metadata is disabled."
    )
    assert field_text(view, "Stale", detail=True) == "SKIPPED — stale is disabled."
    assert field_text(view, "Status", detail=True) == "Metadata was read."


def test_platforms_join_when_the_manifest_succeeded() -> None:
    view = _current_view(platforms=("linux/amd64", "linux/arm64"))
    assert field_text(view, "Architectures", detail=False) == (
        "linux/amd64, linux/arm64"
    )
    assert view.architectures_error == ""


def _stale_view() -> ImageView:
    return _view(
        updated=_OLD,
        created=_OLD,
        profile=_profile(stale=True),
    )


def _current_view(
    *,
    digests: bool = True,
    metadata_error: str = "",
    manifest_error: str = "",
    versions_error: str = "",
    tags: bool = True,
    architectures: bool = True,
    stale: bool = False,
    metadata: bool = True,
    version_limit: int = 100,
    extra_version: bool = False,
    platforms: tuple[str, ...] | None = None,
    http_status: int = 200,
) -> ImageView:
    return _view(
        updated=_RECENT,
        created=_RECENT,
        profile=_profile(
            digests=digests,
            tags=tags,
            architectures=architectures,
            stale=stale,
            metadata=metadata,
        ),
        metadata_error=metadata_error,
        versions_error=versions_error,
        manifest_error=manifest_error,
        version_limit=version_limit,
        extra_version=extra_version,
        platforms=platforms,
        http_status=http_status,
    )


def _view(
    *,
    updated: str,
    created: str,
    profile: ResolvedProfile,
    metadata_error: str = "",
    versions_error: str = "",
    manifest_error: str = "",
    version_limit: int = 100,
    extra_version: bool = False,
    platforms: tuple[str, ...] | None = None,
    http_status: int = 200,
) -> ImageView:
    documents = [
        _metadata(updated, error=metadata_error or None, http_status=http_status),
        _versions(created, error=versions_error or None, extra=extra_version),
    ]
    if platforms is not None or manifest_error:
        documents.append(
            _document(
                "manifest",
                {"platforms": list(platforms or ())},
                fetched_at=_NOW,
                error=manifest_error or None,
            ),
        )
    return build_view(
        _config(version_limit),
        _package(),
        profile,
        documents,
        now=_NOW,
    )


def _config(version_limit: int) -> AppConfig:
    return AppConfig.model_validate(
        {"version": 1, "sync": {"version_limit": version_limit}},
    )


def _profile(**flags: bool) -> ResolvedProfile:
    enabled = {
        "metadata": True,
        "tags": True,
        "digests": True,
        "architectures": True,
        "stale": False,
    }
    enabled.update(flags)
    return ResolvedProfile(name="standard", chain=("standard",), enabled=enabled)


def _package() -> PackageSpec:
    return PackageSpec(
        owner="octocat",
        name="hello-world",
        group="examples",
        profile="standard",
        tags=["demo", "sample"],
    )


def _metadata(
    updated: str,
    *,
    error: str | None,
    http_status: int = 200,
) -> Document:
    return _document(
        "metadata",
        {
            "name": "hello-world",
            "visibility": "public",
            "description": "An example image",
            "created_at": "2024-01-02T03:04:05Z",
            "updated_at": updated,
            "repository": "https://github.com/octocat/hello-world",
            "http_status": http_status,
        },
        fetched_at=_NOW,
        error=error,
    )


def _versions(created: str, *, error: str | None, extra: bool) -> Document:
    items: list[object] = [
        {
            "name": "sha256:abc",
            "created_at": created,
            "metadata": {"container": {"tags": ["1.0", "latest"]}},
        },
    ]
    if extra:
        items.append(
            {
                "name": "sha256:older",
                "created_at": created,
                "metadata": {"container": {"tags": ["0.9"]}},
            },
        )
    return _document("versions", items, fetched_at=_NOW, error=error)


def _document(
    category: str,
    body: dict[str, object] | list[object],
    *,
    fetched_at: float,
    error: str | None,
) -> Document:
    return Document(
        package=_PACKAGE,
        category=category,
        body=json.dumps(body),
        fetched_at=fetched_at,
        error=error,
    )
