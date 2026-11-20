"""Terminal table, detail, and the session view picker."""

from __future__ import annotations

from pathlib import Path

import pytest
from rich.text import Text
from textual.widgets import DataTable, Input, OptionList

from lupaxa.ghcr_intel.config.editor import add_package
from lupaxa.ghcr_intel.config.loader import load_config
from lupaxa.ghcr_intel.sync.engine import SyncReport
from lupaxa.ghcr_intel.tui.application import ImageDetail, IntelTui, ViewPicker

_DEFAULT_HEADERS = [
    "Organisation",
    "Package",
    "Visibility",
    "Status",
    "Updated",
    "Newest tag",
    "Linked repository",
]
_TAGS_HEADERS = ["Organisation", "Package", "Newest tag", "Updated"]
_FILES = ("config.yml", "profiles.yml", "packages.yml")


def _headers(table: DataTable) -> list[str]:
    return [column.label.plain for column in table.ordered_columns]


def _snapshot(root: Path) -> dict[str, str]:
    return {name: (root / name).read_text(encoding="utf-8") for name in _FILES}


def _prompts(screen: ViewPicker) -> list[str]:
    options = screen.query_one(OptionList)
    return [
        str(options.get_option_at_index(index).prompt)
        for index in range(options.option_count)
    ]


async def test_tui_lists_one_image_and_switches_views_for_the_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GHCR_INTEL_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("GHCR_INTEL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("GHCR_INTEL_CONFIG", raising=False)
    add_package(tmp_path / "config.yml", "octocat/hello-world")
    loaded = load_config(tmp_path / "config.yml")
    assert loaded.config.display.view == "default"
    assert "tags" in loaded.config.display.views
    before = _snapshot(tmp_path)

    interface = IntelTui(loaded)
    async with interface.run_test() as pilot:
        await pilot.pause()
        app = pilot.app
        assert isinstance(app, IntelTui)
        assert app.view_name == "default"
        table = app.query_one(DataTable)
        assert table.row_count == 1
        assert _headers(table) == _DEFAULT_HEADERS
        row = table.get_row_at(0)
        organisation = row[_DEFAULT_HEADERS.index("Organisation")]
        status = row[_DEFAULT_HEADERS.index("Status")]
        assert isinstance(organisation, Text)
        assert organisation.plain == "octocat"
        assert isinstance(status, Text)
        assert status.plain == "Not synchronised"
        assert str(status.style) == "dim"

        table.focus()
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, ImageDetail)
        labels = [str(widget.content) for widget in app.screen.query(".detail-field")]
        values = list(app.screen.query(".detail-value"))
        status_value = values[labels.index("Status")].content
        assert isinstance(status_value, Text)
        assert status_value.plain == "This image has not been synchronised."
        assert str(status_value.style) == "dim"
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, ImageDetail)
        assert _headers(app.query_one(DataTable)) == _DEFAULT_HEADERS

        await pilot.press("v")
        await pilot.pause()
        assert isinstance(app.screen, ViewPicker)
        names = ["default"]
        names.extend(name for name in loaded.config.display.views if name != "default")
        assert _prompts(app.screen) == names
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, ViewPicker)
        assert app.view_name == "default"
        assert _headers(app.query_one(DataTable)) == _DEFAULT_HEADERS

        await pilot.press("v")
        await pilot.pause()
        assert isinstance(app.screen, ViewPicker)
        prompts = _prompts(app.screen)
        for _step in range(prompts.index("tags")):
            await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()
        assert not isinstance(app.screen, ViewPicker)
        assert app.view_name == "tags"
        assert _headers(app.query_one(DataTable)) == _TAGS_HEADERS
        assert loaded.config.display.view == "default"
        assert _snapshot(tmp_path) == before


async def test_tui_search_filters_the_table(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GHCR_INTEL_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("GHCR_INTEL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("GHCR_INTEL_CONFIG", raising=False)
    config = tmp_path / "config.yml"
    add_package(config, "octocat/hello-world")
    add_package(config, "octocat/other")
    loaded = load_config(config)

    interface = IntelTui(loaded)
    async with interface.run_test() as pilot:
        await pilot.pause()
        app = pilot.app
        table = app.query_one(DataTable)
        search = app.query_one("#search", Input)
        assert search.placeholder == "Search images..."
        assert table.row_count == 2
        search.focus()
        await pilot.press("o", "t", "h", "e", "r")
        await pilot.pause()
        assert search.value == "other"
        assert table.row_count == 1
        package = table.get_row_at(0)[_DEFAULT_HEADERS.index("Package")]
        assert isinstance(package, Text)
        assert package.plain == "other"
        await pilot.press("escape")
        await pilot.pause()
        assert search.value == ""
        assert table.row_count == 2
        table.focus()
        await pilot.press("slash")
        await pilot.pause()
        assert app.focused is search


def test_footer_uses_the_same_keys_as_the_other_intel_tools() -> None:
    labels = [binding.description for binding in IntelTui.BINDINGS]
    assert labels == [
        "Quit",
        "Sync",
        "Search",
        "Profiles",
        "Config",
        "Views",
        "Cancel",
    ]


async def test_profiles_config_and_sync_use_the_footer_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GHCR_INTEL_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("GHCR_INTEL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("GHCR_INTEL_CONFIG", raising=False)
    config = tmp_path / "config.yml"
    add_package(config, "octocat/hello-world")
    loaded = load_config(config)

    async def finished(*_args: object, **_kwargs: object) -> SyncReport:
        return SyncReport(incomplete=False)

    monkeypatch.setattr(
        "lupaxa.ghcr_intel.auth.token_from_gh",
        lambda: "token",
    )
    monkeypatch.setattr(
        "lupaxa.ghcr_intel.sync.engine.synchronise",
        finished,
    )

    interface = IntelTui(loaded)
    async with interface.run_test() as pilot:
        await pilot.pause()
        app = pilot.app
        summary = app.query_one("#summary")
        assert "Images 1" in str(summary.content)
        await pilot.press("p")
        await pilot.pause()
        assert _headers(app.query_one(DataTable)) == [
            "Profile",
            "Inheritance",
            "Images",
        ]
        assert "Press escape to return." in str(summary.content)
        await pilot.press("escape")
        await pilot.pause()
        assert _headers(app.query_one(DataTable)) == _DEFAULT_HEADERS
        await pilot.press("c")
        await pilot.pause()
        assert str(loaded.path) in str(summary.content)
        await pilot.press("r")
        await pilot.pause()
        assert "Synchronisation completed." in str(summary.content)
