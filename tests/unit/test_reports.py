import json

import pytest

from lupaxa.ghcr_intel.config.models import AppConfig
from lupaxa.ghcr_intel.dates import DEFAULT_DATE_FORMAT
from lupaxa.ghcr_intel.errors import UsageError
from lupaxa.ghcr_intel.layout import resolve_layout
from lupaxa.ghcr_intel.reports.render import audit, failing, render_report
from lupaxa.ghcr_intel.views import ImageView, field_text

_FIELDS = ("Package", "Description", "Status", "Updated")


def test_markdown_starts_with_the_title_and_escapes_cells() -> None:
    view = _image(description="a|b\nsecond")
    rendered = render_report([view], "markdown", date_format=view.date_format, fields=_FIELDS)
    assert rendered.splitlines() == [
        "# GHCR Intel Report",
        "",
        "| Package | Description | Status | Updated |",
        "| --- | --- | --- | --- |",
        (f"| hello-world | a\\|b<br>second | Current | {field_text(view, 'Updated', detail=False)} |"),
    ]


def test_json_uses_human_labels_and_field_text() -> None:
    view = _image(description="a|b\nsecond", status_detail="not the list word")
    config = AppConfig.model_validate({"version": 1})
    fields = resolve_layout(config.display, "default").list_fields
    payload = json.loads(render_report([view], "json", date_format=config.display.date_format, fields=fields))
    assert list(payload[0]) == list(fields)
    for label in fields:
        assert payload[0][label] == field_text(view, label, detail=False)
    described = json.loads(render_report([view], "json", date_format=view.date_format, fields=("Description", "Status")))
    assert described[0]["Description"] == "a|b\nsecond"
    assert described[0]["Status"] == "Current"


def test_csv_uses_the_same_cell_text() -> None:
    view = _image()
    rendered = render_report([view], "csv", date_format=view.date_format, fields=("Package", "Status"))
    assert rendered.splitlines() == [
        "Package,Status",
        "hello-world,Current",
    ]


def test_table_is_uncoloured_text() -> None:
    view = _image(description="a|b\nsecond")
    rendered = render_report([view], "table", date_format=view.date_format, fields=("Package", "Description"))
    assert "\x1b" not in rendered
    assert "Package" in rendered
    assert "hello-world" in rendered
    assert "a|b" in rendered
    assert "<br>" not in rendered


def test_render_report_rejects_an_unknown_format() -> None:
    view = _image()
    with pytest.raises(UsageError, match="Unknown format yaml") as caught:
        render_report([view], "yaml", date_format=view.date_format, fields=("Package",))
    assert caught.value.exit_code == 2


@pytest.mark.parametrize("status", ["Stale", "Error", "Unavailable", "Forbidden"])
def test_failing_includes_a_failed_status(status: str) -> None:
    view = _image(status=status, architectures_error="")
    assert failing([view]) == [view]


def test_failing_excludes_a_current_image_with_an_empty_architectures_error() -> None:
    current = _image(status="Current", architectures_error="")
    fresh = _image(status="Not synchronised", package="fresh")
    assert failing([current, fresh]) == []


def test_failing_includes_current_when_architectures_error_is_forbidden() -> None:
    view = _image(status="Current", architectures_error="Forbidden")
    assert failing([view]) == [view]
    assert field_text(view, "Status", detail=False) == "Current"


def test_audit_json_includes_stale_and_forbidden_architectures() -> None:
    stale = _image(status="Stale", package="old")
    current = _image(status="Current", package="fresh", architectures_error="")
    blocked = _image(status="Current", package="blocked", architectures_error="Forbidden")
    rendered = audit([stale, current, blocked], "json", date_format=stale.date_format, fields=("Package", "Status", "Architectures"))
    rows = json.loads(rendered)
    assert [row["Package"] for row in rows] == ["old", "blocked"]
    assert rows[0]["Status"] == field_text(stale, "Status", detail=False)
    assert rows[0]["Status"] == "Stale"
    assert rows[1]["Status"] == "Current"
    assert rows[1]["Architectures"] == field_text(blocked, "Architectures", detail=False)
    assert rows[1]["Architectures"] == "Forbidden"


def test_audit_table_omits_a_current_image() -> None:
    stale = _image(status="Stale", package="old")
    current = _image(status="Current", package="fresh")
    rendered = audit([stale, current], "table", date_format=stale.date_format, fields=("Package",))
    assert "\x1b" not in rendered
    assert "old" in rendered
    assert "fresh" not in rendered


@pytest.mark.parametrize("output_format", ["markdown", "csv"])
def test_audit_rejects_formats_other_than_table_and_json(output_format: str) -> None:
    view = _image()
    with pytest.raises(UsageError, match=f"Unknown format {output_format}") as caught:
        audit([view], output_format, date_format=view.date_format, fields=("Package",))
    assert caught.value.exit_code == 2


def _image(
    *,
    package: str = "hello-world",
    status: str = "Current",
    architectures_error: str = "",
    description: str = "An example image",
    status_detail: str = "The package and its newest tag are inside the stale window.",
) -> ImageView:
    architectures = "linux/amd64" if architectures_error == "" else architectures_error
    return ImageView(
        owner="octocat",
        package=package,
        group="examples",
        profile="standard",
        local_tags=("demo",),
        enabled=True,
        visibility="public",
        description=description,
        linked_repository="https://github.com/octocat/hello-world",
        status=status,
        status_detail=status_detail,
        cache="cached",
        created="2024-01-02T03:04:05Z",
        updated="2024-06-01T00:00:00Z",
        synchronised="2024-06-01T00:00:00Z",
        newest_tag="latest",
        image_tags=("1.0", "latest"),
        tag_count=2,
        versions_fetched=1,
        versions_truncated=False,
        digest="sha256:abc",
        architectures=architectures,
        architectures_error=architectures_error,
        stale_word="no",
        metadata_error="",
        versions_error="",
        date_format=DEFAULT_DATE_FORMAT,
        metadata_on=True,
        tags_on=True,
        digests_on=True,
        architectures_on=True,
        stale_on=True,
        versions_on=True,
    )
