"""Command-line interface for GHCR Intel."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
import csv
import io
import json
import os
from pathlib import Path
import sys
import time
from typing import TYPE_CHECKING, Literal, NoReturn, TextIO, TypeVar, cast

from pydantic import ValidationError
from rich.console import Console
from rich.table import Table
from rich.text import Text
import typer

from lupaxa.ghcr_intel import auth
from lupaxa.ghcr_intel.config.defaults import generate_default_files
from lupaxa.ghcr_intel.config.editor import add_package, remove_package, set_enabled, set_group, set_profile
from lupaxa.ghcr_intel.config.loader import LoadedConfig, load_config
from lupaxa.ghcr_intel.config.models import PackageSpec
from lupaxa.ghcr_intel.config.paths import config_file, data_directory
from lupaxa.ghcr_intel.config.profiles import resolve_profile
from lupaxa.ghcr_intel.constants import CHECK_NAMES
from lupaxa.ghcr_intel.errors import ConfigError, IntelError, NotConfigured, UsageError
from lupaxa.ghcr_intel.layout import RIGHT_ALIGNED, Layout, resolve_layout
from lupaxa.ghcr_intel.present import paint
from lupaxa.ghcr_intel.reports.render import audit, failing, render_report
from lupaxa.ghcr_intel.storage.database import Database, Document
from lupaxa.ghcr_intel.views import ImageView, build_view, detail_rows, field_text

if TYPE_CHECKING:
    from lupaxa.ghcr_intel.sync.engine import SyncReport

__all__ = ["app", "main"]

T = TypeVar("T")

_VIEW_FORMATS = frozenset({"table", "json", "csv", "markdown"})

app = typer.Typer(
    name="ghcr-intel",
    help="Monitor an explicit list of ghcr.io container images.",
    add_completion=False,
    invoke_without_command=True,
    no_args_is_help=False,
    pretty_exceptions_enable=False,
    rich_markup_mode=None,
)
package_app = typer.Typer(help="Manage the configured image list.", no_args_is_help=True)
profile_app = typer.Typer(help="Inspect monitoring profiles.", no_args_is_help=True)
config_app = typer.Typer(help="Create, validate, and locate configuration.", no_args_is_help=True)
cache_app = typer.Typer(help="Inspect and clear the local cache.", no_args_is_help=True)
app.add_typer(package_app, name="package")
app.add_typer(profile_app, name="profile")
app.add_typer(config_app, name="config")
app.add_typer(cache_app, name="cache")


_verbose = False
_debug = False


def _version_flag(value: bool) -> None:
    """Print the version and stop the command."""

    if value:
        from lupaxa.ghcr_intel import __version__

        typer.echo(f"ghcr-intel v{__version__}")
        raise typer.Exit


def _interactive() -> bool:
    """Return whether stdin and stdout are both terminals."""

    return sys.stdin.isatty() and sys.stdout.isatty()


@app.callback(invoke_without_command=True)
def root(
    ctx: typer.Context,
    config: Path | None = typer.Option(None, "--config", help="Main configuration file."),
    verbose: bool = typer.Option(False, "--verbose", help="Write informational logs to stderr."),
    debug: bool = typer.Option(False, "--debug", help="Write debug logs to stderr."),
    version: bool = typer.Option(False, "--version", callback=_version_flag, is_eager=True, help="Show the version and exit."),
) -> None:
    """Monitor an explicit list of ghcr.io container images."""

    del version
    global _verbose, _debug
    _verbose = verbose
    _debug = debug
    if config is not None:
        os.environ["GHCR_INTEL_CONFIG"] = str(config)
    _configure_logging()
    if ctx.invoked_subcommand is not None:
        return
    if _interactive():
        _guard(_open_cached_tui)
        return
    typer.echo(ctx.get_help())


@package_app.command("add")
def package_add(
    image: str = typer.Argument(..., metavar="OWNER/NAME"),
    group: str | None = typer.Option(None, "--group", help="Group for this image."),
    profile: str | None = typer.Option(None, "--profile", help="Profile for this image."),
    tag: list[str] | None = typer.Option(None, "--tag", help="Local label. Repeat to add more."),
) -> None:
    """Add an image. This does not call GitHub."""

    _guard(lambda: add_package(config_file(), image, group=group, profile=profile, tags=list(tag or [])))
    stdout_console().print(f"Added {image}.")


@package_app.command("remove")
def package_remove(image: str = typer.Argument(..., metavar="OWNER/NAME")) -> None:
    """Remove an image from the list. Cached data is kept."""

    _guard(lambda: remove_package(config_file(), image))
    stdout_console().print(f"Removed {image}.")


@package_app.command("set-group")
def package_set_group(image: str = typer.Argument(..., metavar="OWNER/NAME"), group: str = typer.Argument(..., metavar="GROUP")) -> None:
    """Assign a group. Groups do not change which checks run."""

    _guard(lambda: set_group(config_file(), image, group))
    stdout_console().print(f"Set {image} to {group}.")


@package_app.command("enable")
def package_enable(image: str = typer.Argument(..., metavar="OWNER/NAME")) -> None:
    """Include an image in synchronisation."""

    _guard(lambda: set_enabled(config_file(), image, enabled=True))
    stdout_console().print(f"Enabled {image}.")


@package_app.command("disable")
def package_disable(image: str = typer.Argument(..., metavar="OWNER/NAME")) -> None:
    """Keep an image in the list and skip synchronisation."""

    _guard(lambda: set_enabled(config_file(), image, enabled=False))
    stdout_console().print(f"Disabled {image}.")


@package_app.command("list")
def package_list(
    view: str | None = typer.Option(None, "--view", help="Saved view. The file is not written."),
    group: str | None = typer.Option(None, "--group"),
    profile: str | None = typer.Option(None, "--profile"),
    output_format: str = typer.Option("table", "--format"),
    fail_on_checks: bool = typer.Option(False, "--fail-on-checks"),
) -> None:
    """Show the cached table."""

    _loaded, layout, views = _guard(lambda: _listed(view, group, profile))
    _guard(lambda: _emit(views, output_format, layout.list_fields, title=None))
    _reject_failed(views, fail_on_checks)


@app.command("list")
def list_command(
    view: str | None = typer.Option(None, "--view", help="Saved view. The file is not written."),
    group: str | None = typer.Option(None, "--group"),
    profile: str | None = typer.Option(None, "--profile"),
    output_format: str = typer.Option("table", "--format"),
    fail_on_checks: bool = typer.Option(False, "--fail-on-checks"),
) -> None:
    """Show the cached table."""

    package_list(view, group, profile, output_format, fail_on_checks)


@package_app.command("show")
def package_show(
    owner: str = typer.Argument(..., metavar="OWNER"),
    name: str = typer.Argument(..., metavar="NAME"),
    view: str | None = typer.Option(None, "--view"),
    output_format: str = typer.Option("table", "--format"),
    fail_on_checks: bool = typer.Option(False, "--fail-on-checks"),
) -> None:
    """Show one image."""

    shown, fields = _guard(lambda: _shown(owner, name, view))
    _guard(lambda: _emit_detail(shown, output_format, fields))
    _reject_failed([shown], fail_on_checks)


@package_app.command("set-profile")
def package_set_profile(image: str = typer.Argument(..., metavar="OWNER/NAME"), profile: str = typer.Argument(..., metavar="PROFILE")) -> None:
    """Choose the checks for one image."""

    _guard(lambda: set_profile(config_file(), image, profile))
    stdout_console().print(f"Set {image} to {profile}.")


@profile_app.command("list")
def profile_list() -> None:
    """List profile names."""

    loaded = _guard(lambda: load_config(config_file()))
    console = stdout_console()
    for name in loaded.config_profiles.profiles:
        console.print(name)


@profile_app.command("tree")
def profile_tree() -> None:
    """Show each profile and its parent."""

    loaded = _guard(lambda: load_config(config_file()))
    console = stdout_console()
    for name, spec in loaded.config_profiles.profiles.items():
        if spec.extends is None:
            console.print(name)
        else:
            console.print(f"{name} extends {spec.extends}")


@profile_app.command("validate")
def profile_validate() -> None:
    """Resolve every profile."""

    loaded = _guard(lambda: load_config(config_file()))

    def check(name: str) -> Callable[[], None]:
        def run() -> None:
            resolve_profile(loaded.config_profiles, name)

        return run

    for name in loaded.config_profiles.profiles:
        _guard(check(name))
    stdout_console().print("Profiles are valid.")


@profile_app.command("show")
def profile_show(name: str = typer.Argument(..., metavar="NAME")) -> None:
    """Show the effective checks for one profile."""

    loaded = _guard(lambda: load_config(config_file()))
    resolved = _guard(lambda: resolve_profile(loaded.config_profiles, name))
    console = stdout_console()
    for check in CHECK_NAMES:
        console.print(f"{check}: {_flag(resolved.enabled[check])}")


@config_app.command("init")
def config_init(force: bool = typer.Option(False, "--force", help="Overwrite existing files with the defaults.")) -> None:
    """Write the three default files."""

    _guard(lambda: _init_config(force))


@config_app.command("show")
def config_show() -> None:
    """Print the resolved settings. The token is not part of the configuration."""

    loaded = _guard(lambda: load_config(config_file()))
    stdout_console().print_json(data=loaded.config.model_dump())


@config_app.command("validate")
def config_validate() -> None:
    """Load the files and report the package count."""

    loaded = _guard(lambda: load_config(config_file()))
    stdout_console().print(f"Configuration is valid ({_package_count(len(loaded.packages))}).")


@config_app.command("paths")
def config_paths() -> None:
    """Print the configuration and cache paths."""

    loaded = _guard(lambda: load_config(config_file()))
    console = stdout_console()
    console.print(f"config: {loaded.path}")
    console.print(f"profiles: {loaded.profiles_path}")
    console.print(f"packages: {loaded.packages_path}")
    console.print(f"cache: {data_directory() / 'cache.sqlite'}")


@cache_app.command("info")
def cache_info() -> None:
    """Show the cache path, size, and row counts."""

    database = _database()
    info = database.info()
    console = stdout_console()
    console.print(f"path: {database.path}")
    for key, value in info.items():
        console.print(f"{key}: {value}")


@cache_app.command("clear")
def cache_clear(yes: bool = typer.Option(False, "--yes", help="Skip the confirmation.")) -> None:
    """Delete cached documents and sync runs. The image list stays."""

    _confirm("Clear all cached data?", yes=yes)
    _database().clear()
    stdout_console().print("Cache cleared.")


@cache_app.command("vacuum")
def cache_vacuum() -> None:
    """Rebuild the SQLite file."""

    _database().vacuum()
    stdout_console().print("Cache vacuumed.")


@app.command("sync")
def sync_command(
    force: bool = typer.Option(False, "--force", help="Ignore cache freshness and sync.interval."),
    group: str | None = typer.Option(None, "--group"),
    profile: str | None = typer.Option(None, "--profile"),
    concurrency: int | None = typer.Option(None, "--concurrency"),
    fail_on_checks: bool = typer.Option(False, "--fail-on-checks"),
) -> None:
    """Refresh stale or missing information. A terminal shows a progress bar."""

    try:
        report = _guard(lambda: _synchronise(force, group, profile, concurrency))
    except KeyboardInterrupt:
        _fail("Synchronisation cancelled.", 4)
    from lupaxa.ghcr_intel.reports.progress import print_sync

    print_sync(report)
    if report.incomplete:
        _fail("Synchronisation did not finish.", 4)
    if fail_on_checks:
        _reject_failed(_guard(_synced_views), True)


@app.command("report")
def report_command(
    output_format: str = typer.Option("markdown", "--format"),
    view: str | None = typer.Option(None, "--view"),
    group: str | None = typer.Option(None, "--group"),
    profile: str | None = typer.Option(None, "--profile"),
    output: Path | None = typer.Option(None, "--output"),
    fail_on_checks: bool = typer.Option(False, "--fail-on-checks"),
) -> None:
    """Write a Markdown report of the selected list."""

    loaded, layout, views = _guard(lambda: _listed(view, group, profile))
    text = _guard(lambda: render_report(views, output_format, date_format=loaded.config.display.date_format, fields=layout.list_fields))
    if output is None:
        sys.stdout.write(text)
    else:
        output.write_text(text, encoding="utf-8")
    _reject_failed(views, fail_on_checks)


@app.command("audit")
def audit_command(output_format: str = typer.Option("table", "--format"), view: str | None = typer.Option(None, "--view"), fail_on_checks: bool = typer.Option(False, "--fail-on-checks")) -> None:
    """Show images with a failing check."""

    loaded, layout, views = _guard(lambda: _listed(view))
    text = _guard(lambda: audit(views, output_format, date_format=loaded.config.display.date_format, fields=layout.list_fields))
    sys.stdout.write(text)
    _reject_failed(views, fail_on_checks)


@app.command("tui")
def tui_command(view: str | None = typer.Option(None, "--view")) -> None:
    """Open the terminal interface on the cached view."""

    _guard(lambda: _open_tui(view))


def main() -> None:
    """Run the GHCR Intel command-line interface."""

    app()


def stdout_console() -> Console:
    """Return a console bound to the current stdout. A terminal keeps colour."""

    interactive = bool(getattr(sys.stdout, "isatty", lambda: False)())
    if not interactive:
        return _console(sys.stdout)
    return Console(file=sys.stdout, highlight=False, soft_wrap=True, force_terminal=True)


def stderr_console() -> Console:
    """Return a console bound to the current stderr."""

    return _console(sys.stderr, stderr=True)


def _console(stream: TextIO, *, stderr: bool = False) -> Console:
    return Console(file=stream, markup=False, highlight=False, soft_wrap=True, width=200, force_terminal=False, color_system=None, stderr=stderr)


def _open_cached_tui() -> None:
    _open_tui(None)


def _open_tui(view: str | None) -> None:
    loaded = load_config(config_file())
    if view is not None:
        resolve_layout(loaded.config.display, view)
    _load_tui()(loaded, view)


def _load_tui() -> Callable[[LoadedConfig, str | None], None]:
    import importlib

    try:
        module = importlib.import_module("lupaxa.ghcr_intel.tui.application")
    except ModuleNotFoundError as exc:
        message = "The terminal interface is unavailable."
        raise IntelError(message) from exc
    runner = getattr(module, "run_tui", None)
    if not callable(runner):
        message = "The terminal interface is unavailable."
        raise IntelError(message)
    return cast("Callable[[LoadedConfig, str | None], None]", runner)


def _init_config(force: bool) -> None:
    path = config_file()
    written, kept = generate_default_files(_default_paths(path), force=force)
    console = stdout_console()
    for item in written:
        console.print(f"Wrote {item}.")
    for item in kept:
        console.print(f"Kept {item}.")
    if not written:
        message = "Configuration files already exist. Use --force to overwrite."
        raise ConfigError(message)


def _default_paths(path: Path) -> tuple[Path, Path, Path]:
    directory = path.parent
    return path, directory / "profiles.yml", directory / "packages.yml"


def _listed(view: str | None, group: str | None = None, profile: str | None = None) -> tuple[LoadedConfig, Layout, list[ImageView]]:
    loaded = load_config(config_file())
    packages = _filtered(loaded.packages, group, profile)
    return loaded, _selected(loaded, view), _collect(loaded, packages)


def _filtered(packages: Sequence[PackageSpec], group: str | None, profile: str | None) -> tuple[PackageSpec, ...]:
    chosen: list[PackageSpec] = []
    for package in packages:
        if group is not None and package.group.casefold() != group.casefold():
            continue
        if profile is not None and package.profile.casefold() != profile.casefold():
            continue
        chosen.append(package)
    return tuple(chosen)


def _shown(owner: str, name: str, view: str | None) -> tuple[ImageView, tuple[str, ...] | None]:
    loaded = load_config(config_file())
    return _one(loaded, owner, name), _selected(loaded, view).detail_fields


def _selected(loaded: LoadedConfig, view: str | None) -> Layout:
    name = loaded.config.display.view if view is None else view
    return resolve_layout(loaded.config.display, name)


def _collect(loaded: LoadedConfig, packages: Sequence[PackageSpec] | None = None) -> list[ImageView]:
    database = _database()
    now = time.time()
    chosen = loaded.packages if packages is None else packages
    return [_view(loaded, package, database, now) for package in chosen]


def _synced_views() -> list[ImageView]:
    return _collect(load_config(config_file()))


def _view(loaded: LoadedConfig, package: PackageSpec, database: Database, now: float) -> ImageView:
    profile = resolve_profile(loaded.config_profiles, package.profile, package.checks)
    return build_view(loaded.config, package, profile, _documents(database, package), now=now)


def _one(loaded: LoadedConfig, owner: str, name: str) -> ImageView:
    return _view(loaded, _configured(loaded, owner, name), _database(), time.time())


def _configured(loaded: LoadedConfig, owner: str, name: str) -> PackageSpec:
    for package in loaded.packages:
        if _same(package, owner, name):
            return package
    raise NotConfigured(f"{owner}/{name} is not configured.")


def _same(package: PackageSpec, owner: str, name: str) -> bool:
    return package.owner.casefold() == owner.casefold() and package.name.casefold() == name.casefold()


def _documents(database: Database, package: PackageSpec) -> list[Document]:
    key = f"{package.owner}/{package.name}"
    found: list[Document] = []
    for category in ("metadata", "versions", "manifest"):
        document = database.get(key, category)
        if document is not None:
            found.append(document)
    return found


def _database() -> Database:
    return Database(data_directory() / "cache.sqlite")


def _synchronise(force: bool, group: str | None, profile: str | None, concurrency: int | None) -> SyncReport:
    loaded = load_config(config_file())
    token = auth.token_from_gh()
    _configure_logging(token)
    from lupaxa.ghcr_intel.reports.progress import syncing
    from lupaxa.ghcr_intel.sync import engine as sync_engine

    now = time.time()
    database = _database()
    sync_engine.assert_sync_allowed(loaded.config, database, force=force, now=now)
    packages = _filtered(loaded.packages, group, profile)
    with syncing() as on_progress:
        return asyncio.run(
            sync_engine.synchronise(loaded.config, packages, token=token, force=force, now=now, database=database, profiles=loaded.config_profiles, on_progress=on_progress, concurrency=concurrency)
        )


def _configure_logging(secret: str | None = None) -> None:
    from lupaxa.ghcr_intel.logging_setup import configure_logging

    level = "INFO"
    path = config_file()
    if path.is_file():
        try:
            level = load_config(path).config.logging.level
        except IntelError:
            level = "INFO"
    secrets = [] if secret is None else [secret]
    configure_logging(level, verbose=_verbose, debug=_debug, secrets=secrets)


def _emit(views: Sequence[ImageView], output_format: str, fields: tuple[str, ...], *, title: str | None, allowed: frozenset[str] = _VIEW_FORMATS) -> None:
    _require_format(output_format, allowed)
    if output_format == "table":
        _print_table(views, fields)
        return
    records = _records(views, fields)
    _write_records(records, fields, output_format, title=title)


def _emit_detail(view: ImageView, output_format: str, fields: tuple[str, ...] | None) -> None:
    pairs = _detail_pairs(view, fields)
    _require_format(output_format, _VIEW_FORMATS)
    if output_format == "table":
        _print_pairs(view, pairs)
        return
    columns = tuple(label for label, _value in pairs)
    _write_records([dict(pairs)], columns, output_format, title=None)


def _require_format(output_format: str, allowed: frozenset[str]) -> None:
    if output_format in allowed:
        return
    choices = ", ".join(sorted(allowed))
    raise UsageError(f"Unknown format {output_format}. Expected {choices}.")


def _records(views: Sequence[ImageView], fields: tuple[str, ...]) -> list[dict[str, str]]:
    return [{field: field_text(view, field, detail=False) for field in fields} for view in views]


def _write_records(records: Sequence[Mapping[str, str]], columns: tuple[str, ...], output_format: str, *, title: str | None) -> None:
    sys.stdout.write(_format_records(records, columns, output_format, title=title))


def _format_records(records: Sequence[Mapping[str, str]], columns: tuple[str, ...], output_format: str, *, title: str | None) -> str:
    if output_format == "json":
        return json.dumps([dict(record) for record in records], indent=2) + "\n"
    if output_format == "csv":
        return _csv(records, columns)
    return _markdown(records, columns, title=title)


def _csv(records: Sequence[Mapping[str, str]], columns: tuple[str, ...]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(columns), lineterminator="\n")
    writer.writeheader()
    for record in records:
        writer.writerow({column: record[column] for column in columns})
    return buffer.getvalue()


def _markdown(records: Sequence[Mapping[str, str]], columns: tuple[str, ...], *, title: str | None) -> str:
    lines: list[str] = []
    if title is not None:
        lines.extend((title, ""))
    lines.append("| " + " | ".join(columns) + " |")
    lines.append("| " + " | ".join("---" for _column in columns) + " |")
    for record in records:
        cells = [_markdown_cell(record[column]) for column in columns]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def _markdown_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>")


def _print_table(views: Sequence[ImageView], fields: tuple[str, ...]) -> None:
    table = Table(header_style="bold cyan", title="Images")
    for field in fields:
        table.add_column(field, justify=_justify(field))
    for view in views:
        table.add_row(*_cells(view, fields))
    stdout_console().print(table)


def _print_pairs(view: ImageView, pairs: Sequence[tuple[str, str]]) -> None:
    table = Table(title=f"{view.owner}/{view.package}", header_style="bold cyan")
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    for label, value in pairs:
        table.add_row(label, _painted(view, label, value))
    stdout_console().print(table)


def _detail_pairs(view: ImageView, fields: tuple[str, ...] | None) -> list[tuple[str, str]]:
    rows = detail_rows(view)
    if fields is None:
        return rows
    chosen = set(fields)
    return [(label, value) for label, value in rows if label in chosen]


def _cells(view: ImageView, fields: Sequence[str]) -> list[Text]:
    return [_painted(view, field, field_text(view, field, detail=False)) for field in fields]


def _painted(view: ImageView, label: str, value: str) -> Text:
    return paint(label, value, status=view.status, metadata_error=view.metadata_error)


def _justify(field: str) -> Literal["left", "right"]:
    if field in RIGHT_ALIGNED:
        return "right"
    return "left"


def _reject_failed(views: Sequence[ImageView], enabled: bool) -> None:
    if not enabled:
        return
    failed = failing(views)
    if not failed:
        return
    count = len(failed)
    noun = "image" if count == 1 else "images"
    verb = "has" if count == 1 else "have"
    _fail(f"{count} {noun} {verb} a failing check.", 6)


def _confirm(message: str, *, yes: bool) -> None:
    if yes or not _interactive():
        return
    typer.confirm(message, abort=True)


def _flag(value: bool) -> str:
    if value:
        return "true"
    return "false"


def _package_count(count: int) -> str:
    if count == 1:
        return "1 package"
    return f"{count} packages"


def _guard(func: Callable[[], T]) -> T:
    try:
        return func()
    except IntelError as exc:
        _fail(str(exc), exc.exit_code)
    except ValidationError as exc:
        _fail(str(exc), 2)


def _fail(message: str, code: int) -> NoReturn:
    stderr_console().print(message)
    raise typer.Exit(code=code)


if __name__ == "__main__":
    main()
