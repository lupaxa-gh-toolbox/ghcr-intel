"""Cached image table. The widgets do not call GitHub."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
import time

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Input, OptionList, Static

from lupaxa.ghcr_intel.config.loader import LoadedConfig
from lupaxa.ghcr_intel.config.models import DisplaySettings, PackageSpec
from lupaxa.ghcr_intel.config.paths import data_directory
from lupaxa.ghcr_intel.config.profiles import resolve_profile
from lupaxa.ghcr_intel.errors import AuthError, ConfigError, UsageError
from lupaxa.ghcr_intel.layout import RIGHT_ALIGNED, Layout, resolve_layout
from lupaxa.ghcr_intel.present import paint
from lupaxa.ghcr_intel.storage.database import Database, Document
from lupaxa.ghcr_intel.views import ImageView, build_view, detail_rows, field_text

__all__ = ["ImageDetail", "IntelTui", "ViewPicker", "run_tui"]


class ImageDetail(ModalScreen[None]):
    """Popup of one image. Escape closes it."""

    BINDINGS = [Binding("escape", "dismiss", "Close", priority=True)]

    def __init__(self, image: ImageView, fields: tuple[str, ...] | None) -> None:
        """
        Create the popup.

        Args:
            image: Image to show.
            fields: Catalogue labels. ``None`` keeps every detail row.

        """

        super().__init__()
        self.image = image
        self.fields = fields

    def compose(self) -> ComposeResult:
        """Build the field and value list."""

        with VerticalScroll(id="dialog"):
            yield Static(_title(self.image), id="detail-title")
            for label, value in _detail_lines(self.image, self.fields):
                with Horizontal(classes="detail-row"):
                    yield Static(label, classes="detail-field")
                    yield Static(value, classes="detail-value")


class ViewPicker(ModalScreen[str | None]):
    """Saved view names. Enter chooses one. Escape closes the list."""

    BINDINGS = [Binding("escape", "dismiss", "Close", priority=True)]

    def __init__(self, names: tuple[str, ...]) -> None:
        """
        Create the picker.

        Args:
            names: View names, with ``default`` first.

        """

        super().__init__()
        self.names = names

    def compose(self) -> ComposeResult:
        """Build the view list."""

        with VerticalScroll(id="dialog"):
            yield Static("Views", id="detail-title")
            yield OptionList(*self.names, id="view-list")

    def on_mount(self) -> None:
        """Focus the list so Enter selects the highlighted view."""

        self.query_one(OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """
        Close the picker with the chosen name.

        Args:
            event: Selection from the list.

        """

        self.dismiss(_prompt(event))


class IntelTui(App[None]):
    """Image browser backed by the local cache."""

    TITLE = "GHCR Intel"
    CSS = """
    #summary { height: auto; padding: 0 1; }
    #search { margin: 0 1; }
    DataTable { height: 1fr; }
    DataTable > .datatable--header {
        color: cyan;
        text-style: bold;
    }
    DataTable > .datatable--header-cursor {
        color: cyan;
    }
    ImageDetail { align: center middle; }
    ViewPicker { align: center middle; }
    #dialog {
        width: 90%;
        max-width: 120;
        height: auto;
        max-height: 90%;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    #detail-title { text-style: bold; padding: 0 0 1 0; height: auto; }
    .detail-row { height: auto; width: 100%; }
    .detail-field { width: 24; height: auto; color: cyan; text-style: bold; }
    .detail-value { width: 1fr; height: auto; }
    """
    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", key_display="control + q", priority=True),
        Binding("r", "sync", "Sync"),
        Binding("slash", "search", "Search"),
        Binding("p", "profiles", "Profiles"),
        Binding("c", "config", "Config"),
        Binding("v", "views", "Views"),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, service: LoadedConfig, view: str | None = None) -> None:
        """
        Create the interface.

        Args:
            service: Loaded configuration. Choosing a view does not write it.
            view: Named layout from the command line. ``None`` uses the
                configured view.

        """

        super().__init__()
        self.service = service
        self.view_name = service.config.display.view if view is None else view
        self.images: list[ImageView] = []
        self._layout: Layout | None = None
        self._mode = "images"
        self._sync_task: asyncio.Task[None] | None = None

    def compose(self) -> ComposeResult:
        """Build the header, summary, search, table, and footer."""

        yield Header()
        yield Static("Loading cache…", id="summary")
        yield Input(placeholder="Search images...", id="search")
        yield DataTable(id="table")
        yield Footer()

    def on_mount(self) -> None:
        """Load the cached images and focus the table."""

        table = self.query_one(DataTable)
        table.cursor_type = "row"
        self.images = _images(self.service)
        self._show(resolve_layout(self.service.config.display, self.view_name))
        table.focus()

    def action_views(self) -> None:
        """Open the saved view picker."""

        self.push_screen(ViewPicker(_view_names(self.service.config.display)), self._choose_view)

    def on_input_changed(self, event: Input.Changed) -> None:
        """Filter the image table as the search box changes."""

        if event.input.id == "search" and self._mode == "images":
            self._fill(event.value)

    def action_search(self) -> None:
        """Focus the search box."""

        self.query_one("#search", Input).focus()

    async def action_sync(self) -> None:
        """Start synchronisation without blocking the interface."""

        if self._sync_task is not None and not self._sync_task.done():
            return
        self._sync_task = asyncio.create_task(self._run_sync())

    def action_cancel(self) -> None:
        """Cancel synchronisation, return to the image table, or clear search."""

        if self._sync_task is not None and not self._sync_task.done():
            self._sync_task.cancel()
            return
        if self._mode != "images" and self._layout is not None:
            self._show(self._layout)
            return
        search = self.query_one("#search", Input)
        if search.value:
            search.value = ""
            self.query_one(DataTable).focus()

    def action_profiles(self) -> None:
        """Show profiles and how many images use each one."""

        self._mode = "profiles"
        table = self.query_one(DataTable)
        table.clear(columns=True)
        table.add_columns("Profile", "Inheritance", "Images")
        counts: dict[str, int] = {}
        for image in self.images:
            counts[image.profile] = counts.get(image.profile, 0) + 1
        try:
            for name in self.service.config_profiles.profiles:
                resolved = resolve_profile(self.service.config_profiles, name)
                table.add_row(name, " -> ".join(resolved.chain), str(counts.get(name, 0)))
        except ConfigError as exc:
            self._summary(str(exc))
            return
        self._summary("Profiles. Press escape to return.")

    def action_config(self) -> None:
        """Show configuration paths. The token is not included."""

        loaded = self.service
        cache = data_directory() / "cache.sqlite"
        self._summary(
            "\n".join(
                [
                    f"Config: {loaded.path}",
                    f"Profiles: {loaded.profiles_path}",
                    f"Packages: {loaded.packages_path}",
                    f"Concurrency: {loaded.config.sync.concurrency}",
                    f"Cache: {cache}",
                    "",
                    "Press control + q to quit.",
                ]
            )
        )

    async def _run_sync(self) -> None:
        from lupaxa.ghcr_intel.auth import token_from_gh
        from lupaxa.ghcr_intel.reports.progress import status_line
        from lupaxa.ghcr_intel.sync.engine import synchronise

        try:
            report = await synchronise(
                self.service.config,
                self.service.packages,
                token=token_from_gh(),
                force=False,
                now=time.time(),
                profiles=self.service.config_profiles,
                on_progress=lambda item: self._summary(status_line(item)),
            )
        except asyncio.CancelledError:
            self._refresh()
            self._summary("Synchronisation cancelled.")
            return
        except UsageError as exc:
            self._summary(str(exc))
            self.notify(str(exc), severity="warning")
            return
        except AuthError as exc:
            self._summary(str(exc))
            self.notify(str(exc), severity="error")
            return
        self._refresh()
        if report.incomplete:
            self._summary("Synchronisation did not finish.")
            return
        self._summary("Synchronisation completed.")

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """
        Show the selected image.

        Args:
            event: Table selection.

        """

        if self._mode != "images" or self._layout is None:
            return
        image = _selected(self.images, event.row_key.value)
        if image is None:
            return
        self.push_screen(ImageDetail(image, self._layout.detail_fields))

    def _choose_view(self, name: str | None) -> None:
        if name is None:
            return
        self.view_name = name
        self._show(resolve_layout(self.service.config.display, name))

    def _refresh(self) -> None:
        self.images = _images(self.service)
        if self._layout is not None:
            self._show(self._layout)

    def _summary(self, text: str) -> None:
        self.query_one("#summary", Static).update(text)

    def _show(self, layout: Layout) -> None:
        self._layout = layout
        self._mode = "images"
        table = self.query_one(DataTable)
        table.clear(columns=True)
        fields = layout.list_fields
        for label in fields:
            table.add_column(_heading(label), key=label)
        self._fill(self.query_one("#search", Input).value)
        self._summary(_counts(self.images))

    def _fill(self, query: str) -> None:
        if self._layout is None:
            return
        folded = query.casefold()
        table = self.query_one(DataTable)
        table.clear()
        fields = self._layout.list_fields
        for image in self.images:
            if folded and folded not in _haystack(image):
                continue
            table.add_row(*(_cell(image, label) for label in fields), key=_image_key(image))


def run_tui(service: LoadedConfig, view: str | None = None) -> None:
    """
    Run the terminal interface until the operator quits.

    Args:
        service: Loaded configuration. The files are not written.
        view: Named layout from the command line. ``None`` uses the
            configured view.

    """

    IntelTui(service, view).run()


def _counts(images: list[ImageView]) -> str:
    total = len(images)
    settled = {"Current", "Not synchronised"}
    review = sum(1 for image in images if image.status not in settled)
    return f"Images {total}    Need review {review}"


def _title(image: ImageView) -> Text:
    return _painted(image, "Package", detail=True)


def _detail_lines(image: ImageView, fields: tuple[str, ...] | None) -> list[tuple[str, Text]]:
    rows = detail_rows(image)
    if fields is not None:
        chosen = set(fields)
        rows = [(label, value) for label, value in rows if label in chosen]
    return [(label, _paint(image, label, value)) for label, value in rows]


def _painted(image: ImageView, label: str, *, detail: bool) -> Text:
    return _paint(image, label, field_text(image, label, detail=detail))


def _paint(image: ImageView, label: str, value: str) -> Text:
    return paint(label, value, status=image.status, metadata_error=image.metadata_error)


def _cell(image: ImageView, label: str) -> Text:
    painted = _painted(image, label, detail=False)
    if label in RIGHT_ALIGNED:
        painted.justify = "right"
    return painted


def _heading(label: str) -> str | Text:
    if label in RIGHT_ALIGNED:
        return Text(label, justify="right")
    return label


def _images(service: LoadedConfig) -> list[ImageView]:
    database = Database(data_directory() / "cache.sqlite")
    now = time.time()
    return [_image(service, package, database, now) for package in service.packages]


def _image(service: LoadedConfig, package: PackageSpec, database: Database, now: float) -> ImageView:
    profile = resolve_profile(service.config_profiles, package.profile, package.checks)
    return build_view(service.config, package, profile, _documents(database, package), now=now)


def _documents(database: Database, package: PackageSpec) -> list[Document]:
    key = _image_key_parts(package.owner, package.name)
    found: list[Document] = []
    for category in ("metadata", "versions", "manifest"):
        document = database.get(key, category)
        if document is not None:
            found.append(document)
    return found


def _view_names(display: DisplaySettings) -> tuple[str, ...]:
    others = tuple(name for name in display.views if name != "default")
    return ("default", *others)


def _selected(images: Sequence[ImageView], key: str | None) -> ImageView | None:
    if key is None:
        return None
    for image in images:
        if _image_key(image) == key:
            return image
    return None


def _haystack(image: ImageView) -> str:
    return f"{image.owner}/{image.package} {image.description}".casefold()


def _image_key(image: ImageView) -> str:
    return _image_key_parts(image.owner, image.package)


def _image_key_parts(owner: str, name: str) -> str:
    return f"{owner}/{name}"


def _prompt(event: OptionList.OptionSelected) -> str:
    prompt = event.option.prompt
    if isinstance(prompt, str):
        return prompt
    return str(prompt)
