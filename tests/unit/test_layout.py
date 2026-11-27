import pytest

from lupaxa.ghcr_intel.config.models import AppConfig
from lupaxa.ghcr_intel.dates import format_date
from lupaxa.ghcr_intel.errors import ConfigError
from lupaxa.ghcr_intel.layout import CATALOGUE, RIGHT_ALIGNED, hardcoded_list, resolve_layout


def test_hardcoded_list_is_fixed() -> None:
    assert hardcoded_list() == ("Organisation", "Package", "Visibility", "Status", "Updated", "Newest tag", "Linked repository")


def test_default_view_keeps_the_hardcoded_detail() -> None:
    config = AppConfig.model_validate({"version": 1})
    layout = resolve_layout(config.display, "default")
    assert layout.list_fields == hardcoded_list()
    assert layout.detail_fields is None


def test_tags_view_replaces_only_the_list() -> None:
    config = AppConfig.model_validate(
        {
            "version": 1,
            "display": {
                "views": {
                    "tags": {
                        "list": ["organisation", "package", "newest tag", "updated"],
                    },
                },
            },
        }
    )
    layout = resolve_layout(config.display, "tags")
    assert layout.list_fields == ("Organisation", "Package", "Newest tag", "Updated")
    assert layout.detail_fields is None


def test_metadata_error_is_not_a_catalogue_label() -> None:
    config = AppConfig.model_validate({"version": 1, "display": {"views": {"bad": {"list": ["Metadata error"]}}}})
    with pytest.raises(ConfigError):
        resolve_layout(config.display, "bad")


def test_date_without_a_clock_fills_the_asked_time_with_dashes() -> None:
    assert format_date("2026-01-02", "DD/MM/YYYY HH:mm UTC") == "02/01/2026 --:-- UTC"


def test_catalogue_is_the_spec_order() -> None:
    assert CATALOGUE == (
        "Organisation",
        "Package",
        "Visibility",
        "Description",
        "Linked repository",
        "Group",
        "Profile",
        "Tags",
        "Enabled",
        "Status",
        "Cache",
        "Created",
        "Updated",
        "Synchronised",
        "Newest tag",
        "Image tags",
        "Tag count",
        "Versions fetched",
        "Versions truncated",
        "Digest",
        "Architectures",
        "Stale",
    )
    assert set(RIGHT_ALIGNED) == {"Tag count", "Versions fetched"}


def test_default_entry_replaces_only_the_sides_it_sets() -> None:
    config = AppConfig.model_validate(
        {
            "version": 1,
            "display": {"views": {"default": {"detail": ["digest", "stale"]}}},
        }
    )
    layout = resolve_layout(config.display, "default")
    assert layout.list_fields == hardcoded_list()
    assert layout.detail_fields == ("Digest", "Stale")


def test_a_named_view_can_replace_both_sides() -> None:
    config = AppConfig.model_validate(
        {
            "version": 1,
            "display": {
                "views": {
                    "both": {
                        "list": ["package"],
                        "detail": ["status", "cache"],
                    },
                },
            },
        }
    )
    layout = resolve_layout(config.display, "both")
    assert layout.list_fields == ("Package",)
    assert layout.detail_fields == ("Status", "Cache")


def test_unknown_view_is_rejected() -> None:
    config = AppConfig.model_validate({"version": 1})
    with pytest.raises(ConfigError):
        resolve_layout(config.display, "missing")


def test_duplicate_label_is_rejected() -> None:
    config = AppConfig.model_validate(
        {
            "version": 1,
            "display": {"views": {"dup": {"list": ["Package", "package"]}}},
        }
    )
    with pytest.raises(ConfigError):
        resolve_layout(config.display, "dup")


def test_a_clock_uses_the_second_mm_as_the_minute() -> None:
    assert format_date("2026-01-02T15:04:05Z", "DD/MM/YYYY HH:MM:ss") == ("02/01/2026 15:04:05")


def test_an_offset_is_shown_in_utc() -> None:
    assert format_date("2026-11-18T23:06:41+01:00", "YYYY-MM-DD HH:mm") == ("2026-11-18 22:06")


def test_the_model_normalises_the_shared_date_pattern() -> None:
    config = AppConfig.model_validate({"version": 1, "display": {"date_format": "DD/MM/YYYY HH:MM:SS"}})
    assert config.display.date_format == "DD/MM/YYYY HH:mm:ss"
