"""CLI commands, cache reads, and exit codes."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import respx
from typer.testing import CliRunner

from lupaxa.ghcr_intel.cli import app
from lupaxa.ghcr_intel.errors import AuthError
from lupaxa.ghcr_intel.storage.database import Database

runner = CliRunner()

_PACKAGE_ADD_PROBE = """\
import sys

from typer.testing import CliRunner

from lupaxa.ghcr_intel.cli import app

result = CliRunner().invoke(
    app,
    ["package", "add", "octocat/hello-world"],
)
if result.exit_code != 0:
    sys.stderr.write(result.output)
    raise SystemExit(result.exit_code or 1)
loaded = [
    name
    for name in (
        "httpx",
        "lupaxa.ghcr_intel.ghcr.packages",
        "lupaxa.ghcr_intel.ghcr.registry",
    )
    if name in sys.modules
]
if loaded:
    sys.stderr.write("loaded " + ", ".join(loaded))
    raise SystemExit(1)
"""


@pytest.fixture
def config_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("GHCR_INTEL_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("GHCR_INTEL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("GHCR_INTEL_CONFIG", raising=False)
    return tmp_path


def test_package_add_writes_packages_without_http(config_home: Path) -> None:
    assert len(respx.routes) == 0
    result = runner.invoke(app, ["package", "add", "octocat/hello-world", "--group", "examples"])
    text = (config_home / "packages.yml").read_text(encoding="utf-8")
    assert result.exit_code == 0
    assert "octocat" in text
    assert "hello-world" in text
    assert "examples" in text
    assert len(respx.routes) == 0
    assert len(respx.calls) == 0


def test_package_add_does_not_load_httpx(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "src")
    env["GHCR_INTEL_CONFIG_DIR"] = str(tmp_path)
    env.pop("GHCR_INTEL_CONFIG", None)
    completed = subprocess.run(  # noqa: S603
        [sys.executable, "-c", _PACKAGE_ADD_PROBE],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert completed.returncode == 0, completed.stderr
    text = (tmp_path / "packages.yml").read_text(encoding="utf-8")
    assert "octocat" in text
    assert "hello-world" in text


def test_package_add_flags_stay_on_one_image(config_home: Path) -> None:
    added = runner.invoke(
        app,
        [
            "package",
            "add",
            "octocat/hello-world",
            "--group",
            "examples",
            "--profile",
            "detailed",
        ],
    )
    other = runner.invoke(app, ["package", "add", "octocat/other"])
    first = runner.invoke(app, ["package", "show", "octocat", "hello-world", "--format", "json"])
    second = runner.invoke(app, ["package", "show", "octocat", "other", "--format", "json"])
    assert added.exit_code == 0
    assert other.exit_code == 0
    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    first_row = json.loads(first.stdout)[0]
    second_row = json.loads(second.stdout)[0]
    assert first_row["Group"] == "examples"
    assert first_row["Profile"] == "detailed"
    assert second_row["Group"] == "ungrouped"
    assert second_row["Profile"] == "standard"


def test_version_prints_the_package_version() -> None:
    from lupaxa.ghcr_intel import __version__

    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == f"ghcr-intel v{__version__}"


def test_config_option_selects_the_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GHCR_INTEL_CONFIG", raising=False)
    target = tmp_path / "picked" / "config.yml"
    result = runner.invoke(app, ["--config", str(target), "config", "init"])
    assert result.exit_code == 0
    assert target.is_file()
    assert (target.parent / "packages.yml").is_file()


def test_list_filters_group_without_case(config_home: Path) -> None:
    assert runner.invoke(app, ["package", "add", "octocat/hello-world", "--group", "Examples"]).exit_code == 0
    assert runner.invoke(app, ["package", "add", "octocat/other", "--group", "elsewhere"]).exit_code == 0
    matched = runner.invoke(app, ["list", "--group", "examples"])
    missed = runner.invoke(app, ["list", "--group", "missing"])
    assert matched.exit_code == 0
    assert "hello-world" in matched.stdout
    assert "other" not in matched.stdout
    assert "hello-world" not in missed.stdout
    assert "\x1b" not in matched.stdout


def test_disable_keeps_the_image_and_marks_it_off(config_home: Path) -> None:
    assert runner.invoke(app, ["package", "add", "octocat/hello-world"]).exit_code == 0
    disabled = runner.invoke(app, ["package", "disable", "octocat/hello-world"])
    text = (config_home / "packages.yml").read_text(encoding="utf-8")
    shown = runner.invoke(app, ["package", "show", "octocat", "hello-world", "--format", "json"])
    assert disabled.exit_code == 0
    assert "enabled: false" in text
    assert json.loads(shown.stdout)[0]["Enabled"] == "no"
    enabled = runner.invoke(app, ["package", "enable", "octocat/hello-world"])
    again = (config_home / "packages.yml").read_text(encoding="utf-8")
    assert enabled.exit_code == 0
    assert "enabled: true" in again


def test_sync_passes_concurrency_and_rejects_zero(config_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from lupaxa.ghcr_intel.sync.engine import SyncReport, synchronise

    assert runner.invoke(app, ["config", "init"]).exit_code == 0
    seen: dict[str, object] = {}

    def token() -> str:
        return "test-token"

    async def fake(*_args: object, **kwargs: object) -> SyncReport:
        seen["concurrency"] = kwargs["concurrency"]
        return SyncReport(incomplete=False)

    monkeypatch.setattr("lupaxa.ghcr_intel.auth.token_from_gh", token)
    monkeypatch.setattr("lupaxa.ghcr_intel.sync.engine.synchronise", fake)
    result = runner.invoke(app, ["sync", "--concurrency", "3", "--force"])
    assert result.exit_code == 0
    assert seen["concurrency"] == 3

    monkeypatch.setattr("lupaxa.ghcr_intel.sync.engine.synchronise", synchronise)
    rejected = runner.invoke(app, ["sync", "--concurrency", "0"])
    assert rejected.exit_code == 2


def test_report_output_writes_plain_text(config_home: Path) -> None:
    assert runner.invoke(app, ["package", "add", "octocat/hello-world"]).exit_code == 0
    target = config_home / "report.md"
    result = runner.invoke(app, ["report", "--output", str(target)])
    text = target.read_text(encoding="utf-8")
    assert result.exit_code == 0
    assert result.stdout == ""
    assert text.startswith("# GHCR Intel Report\n")
    assert "hello-world" in text
    assert "\x1b" not in text


def test_terminal_table_keeps_status_colour(monkeypatch: pytest.MonkeyPatch) -> None:
    from io import StringIO

    from rich.text import Text

    from lupaxa.ghcr_intel.cli import stdout_console

    class _Tty(StringIO):
        def isatty(self) -> bool:
            return True

    buffer = _Tty()
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setattr(sys, "stdout", buffer)
    stdout_console().print(Text("public", style="green"))
    assert "\x1b" in buffer.getvalue()


def test_package_add_stores_repeated_tags(config_home: Path) -> None:
    result = runner.invoke(
        app,
        [
            "package",
            "add",
            "octocat/hello-world",
            "--tag",
            "demo",
            "--tag",
            "sample",
        ],
    )
    text = (config_home / "packages.yml").read_text(encoding="utf-8")
    assert result.exit_code == 0
    assert "demo" in text
    assert "sample" in text


def test_package_list_reads_the_cache_and_does_not_fetch(config_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert runner.invoke(app, ["config", "init"]).exit_code == 0
    assert runner.invoke(app, ["package", "add", "octocat/hello-world", "--group", "examples"]).exit_code == 0
    _seed_public(config_home / "data")
    called = {"fetch": 0}

    def explode(*_args: object, **_kwargs: object) -> None:
        called["fetch"] += 1
        message = "fetch_package"
        raise RuntimeError(message)

    monkeypatch.setattr("lupaxa.ghcr_intel.ghcr.packages.fetch_package", explode)
    monkeypatch.setattr("lupaxa.ghcr_intel.sync.engine.read_package", explode)
    result = runner.invoke(app, ["package", "list"])
    assert result.exit_code == 0
    assert "octocat" in result.stdout
    assert "public" in result.stdout
    assert called["fetch"] == 0
    listed = runner.invoke(app, ["list"])
    assert listed.exit_code == 0
    assert "octocat" in listed.stdout
    assert called["fetch"] == 0


def test_profile_list_tree_and_validate(config_home: Path) -> None:
    assert runner.invoke(app, ["config", "init"]).exit_code == 0
    listed = runner.invoke(app, ["profile", "list"])
    assert listed.exit_code == 0
    assert "standard" in listed.stdout
    tree = runner.invoke(app, ["profile", "tree"])
    assert tree.exit_code == 0
    assert "standard extends base" in tree.stdout
    validated = runner.invoke(app, ["profile", "validate"])
    assert validated.exit_code == 0
    assert "Profiles are valid." in validated.stdout


def test_config_show_prints_the_settings(config_home: Path) -> None:
    assert runner.invoke(app, ["config", "init"]).exit_code == 0
    shown = runner.invoke(app, ["config", "show"])
    assert shown.exit_code == 0
    payload = json.loads(shown.stdout)
    assert payload["github"]["registry_url"] == "https://ghcr.io"
    assert payload["github"]["api_url"] == "https://api.github.com"


def test_package_remove_and_set_group(config_home: Path) -> None:
    assert runner.invoke(app, ["package", "add", "octocat/hello-world"]).exit_code == 0
    grouped = runner.invoke(app, ["package", "set-group", "octocat/hello-world", "examples"])
    assert grouped.exit_code == 0
    assert "examples" in (config_home / "packages.yml").read_text(encoding="utf-8")
    removed = runner.invoke(app, ["package", "remove", "octocat/hello-world"])
    assert removed.exit_code == 0
    missing = runner.invoke(app, ["package", "show", "octocat", "hello-world"])
    assert missing.exit_code == 5


def test_cache_info_clear_and_vacuum(config_home: Path) -> None:
    assert runner.invoke(app, ["config", "init"]).exit_code == 0
    assert runner.invoke(app, ["package", "add", "octocat/hello-world"]).exit_code == 0
    _seed_public(config_home / "data")
    info = runner.invoke(app, ["cache", "info"])
    assert info.exit_code == 0
    assert "documents:" in info.stdout
    cleared = runner.invoke(app, ["cache", "clear", "--yes"])
    assert cleared.exit_code == 0
    vacuumed = runner.invoke(app, ["cache", "vacuum"])
    assert vacuumed.exit_code == 0


def test_profile_show_standard_prints_effective_checks(config_home: Path) -> None:
    assert runner.invoke(app, ["config", "init"]).exit_code == 0
    result = runner.invoke(app, ["profile", "show", "standard"])
    assert result.exit_code == 0
    assert "tags: true" in result.stdout
    assert "digests: false" in result.stdout


def test_package_show_missing_exits_5(config_home: Path) -> None:
    assert runner.invoke(app, ["config", "init"]).exit_code == 0
    result = runner.invoke(app, ["package", "show", "octocat", "missing"])
    assert result.exit_code == 5


def test_set_profile_writes_the_package_object(config_home: Path) -> None:
    assert runner.invoke(app, ["package", "add", "octocat/hello-world"]).exit_code == 0
    result = runner.invoke(app, ["package", "set-profile", "octocat/hello-world", "detailed"])
    text = (config_home / "packages.yml").read_text(encoding="utf-8")
    missing = runner.invoke(app, ["package", "set-profile", "octocat/missing", "standard"])
    assert result.exit_code == 0
    assert "detailed" in text
    assert missing.exit_code == 5


def test_config_init_twice_exits_2_and_force_restores_empty_owners(config_home: Path) -> None:
    assert runner.invoke(app, ["config", "init"]).exit_code == 0
    assert runner.invoke(app, ["package", "add", "octocat/hello-world", "--group", "examples"]).exit_code == 0
    second = runner.invoke(app, ["config", "init"])
    forced = runner.invoke(app, ["config", "init", "--force"])
    text = (config_home / "packages.yml").read_text(encoding="utf-8")
    assert second.exit_code == 2
    assert forced.exit_code == 0
    assert text == "owners: []\n"


def test_unknown_view_on_package_list_exits_2(config_home: Path) -> None:
    assert runner.invoke(app, ["config", "init"]).exit_code == 0
    result = runner.invoke(app, ["package", "list", "--view", "missing"])
    assert result.exit_code == 2


def test_sync_auth_error_exits_3_before_fetch(config_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert runner.invoke(app, ["config", "init"]).exit_code == 0
    assert runner.invoke(app, ["package", "add", "octocat/hello-world"]).exit_code == 0
    called = {"fetch": 0, "sync": 0}

    def deny() -> str:
        raise AuthError("The GitHub CLI token is missing.")

    async def fetch(*_args: object, **_kwargs: object) -> None:
        called["fetch"] += 1
        message = "fetch"
        raise AssertionError(message)

    async def synchronise(*_args: object, **_kwargs: object) -> None:
        called["sync"] += 1
        message = "synchronise"
        raise AssertionError(message)

    monkeypatch.setattr("lupaxa.ghcr_intel.auth.token_from_gh", deny)
    monkeypatch.setattr("lupaxa.ghcr_intel.ghcr.packages.fetch_package", fetch)
    monkeypatch.setattr("lupaxa.ghcr_intel.sync.engine.read_package", fetch)
    monkeypatch.setattr("lupaxa.ghcr_intel.sync.engine.synchronise", synchronise)
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 3
    assert called["fetch"] == 0
    assert called["sync"] == 0


def test_fail_on_checks_exits_6_when_the_view_is_stale(config_home: Path) -> None:
    assert runner.invoke(app, ["config", "init"]).exit_code == 0
    assert runner.invoke(app, ["package", "add", "octocat/hello-world", "--profile", "detailed"]).exit_code == 0
    _seed_stale(config_home / "data")
    listed = runner.invoke(app, ["package", "list", "--fail-on-checks"])
    audited = runner.invoke(app, ["audit", "--fail-on-checks"])
    assert listed.exit_code == 6
    assert audited.exit_code == 6


def test_sync_incomplete_exits_4_and_force_is_passed(config_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from lupaxa.ghcr_intel.sync.engine import SyncReport

    assert runner.invoke(app, ["config", "init"]).exit_code == 0
    seen: dict[str, object] = {}

    def token() -> str:
        return "test-token"

    async def synchronise(
        _config: object,
        _packages: object,
        *,
        token: str,
        force: bool,
        now: float,
        database: object = None,
        profiles: object = None,
        fetch_package: object = None,
        fetch_manifest: object = None,
        on_progress: object = None,
        concurrency: int | None = None,
    ) -> SyncReport:
        del (now, database, profiles, fetch_package, fetch_manifest, on_progress, concurrency)
        seen["token"] = token
        seen["force"] = force
        return SyncReport(incomplete=True)

    monkeypatch.setattr("lupaxa.ghcr_intel.auth.token_from_gh", token)
    monkeypatch.setattr("lupaxa.ghcr_intel.sync.engine.synchronise", synchronise)
    incomplete = runner.invoke(app, ["sync", "--force"])
    assert incomplete.exit_code == 4
    assert seen["token"] == "test-token"
    assert seen["force"] is True

    async def finished(*_args: object, **kwargs: object) -> SyncReport:
        seen["force"] = kwargs["force"]
        return SyncReport(incomplete=False)

    monkeypatch.setattr("lupaxa.ghcr_intel.sync.engine.synchronise", finished)
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0
    assert seen["force"] is False


def test_sync_prints_progress_and_the_summary(config_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from rich.console import Console

    from lupaxa.ghcr_intel.sync.engine import SyncReport

    assert runner.invoke(app, ["config", "init"]).exit_code == 0
    monkeypatch.setattr(Console, "is_terminal", property(lambda _self: False))

    def token() -> str:
        return "test-token"

    async def synchronise(*_args: object, on_progress: object = None, **_kwargs: object) -> SyncReport:
        report = SyncReport(images=1, successful=1, requests=2, cache_hits=1, latest="octocat/one", elapsed=1.25)
        assert callable(on_progress)
        on_progress(report)
        return report

    monkeypatch.setattr("lupaxa.ghcr_intel.auth.token_from_gh", token)
    monkeypatch.setattr("lupaxa.ghcr_intel.sync.engine.synchronise", synchronise)
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0
    assert "octocat/one  1/1" in result.stderr
    assert "requests 2" in result.stderr
    assert "Synchronisation completed" in result.stdout
    assert "Images: 1" in result.stdout
    assert "API requests: 2" in result.stdout
    assert "Cache hits: 1" in result.stdout
    assert "Elapsed: 1.2s" in result.stdout


def test_report_starts_with_the_markdown_title(config_home: Path) -> None:
    assert runner.invoke(app, ["package", "add", "octocat/hello-world"]).exit_code == 0
    result = runner.invoke(app, ["report"])
    assert result.exit_code == 0
    assert result.stdout.startswith("# GHCR Intel Report\n")
    assert "octocat" in result.stdout
    assert "hello-world" in result.stdout


def test_config_validate_and_paths(config_home: Path) -> None:
    assert runner.invoke(app, ["package", "add", "octocat/hello-world"]).exit_code == 0
    validated = runner.invoke(app, ["config", "validate"])
    paths = runner.invoke(app, ["config", "paths"])
    assert validated.exit_code == 0
    assert "1 package" in validated.stdout
    assert paths.exit_code == 0
    assert "packages.yml" in paths.stdout
    assert "cache.sqlite" in paths.stdout


def test_without_a_tty_prints_help_and_does_not_import_tui() -> None:
    sys.modules.pop("lupaxa.ghcr_intel.tui.application", None)
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "Usage:" in result.stdout
    assert "lupaxa.ghcr_intel.tui.application" not in sys.modules


def test_tty_without_arguments_calls_run_tui(config_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import types

    assert runner.invoke(app, ["config", "init"]).exit_code == 0
    tui_module = types.ModuleType("lupaxa.ghcr_intel.tui")
    tui_module.__path__ = []  # type: ignore[attr-defined]
    application = types.ModuleType("lupaxa.ghcr_intel.tui.application")
    seen: dict[str, object] = {}

    def run_tui(service: object, view: str | None = None) -> None:
        seen["service"] = service
        seen["view"] = view

    application.run_tui = run_tui  # type: ignore[attr-defined]
    tui_module.application = application  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "lupaxa.ghcr_intel.tui", tui_module)
    monkeypatch.setitem(sys.modules, "lupaxa.ghcr_intel.tui.application", application)
    monkeypatch.setattr("lupaxa.ghcr_intel.cli._interactive", lambda: True)
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert seen["view"] is None
    assert seen["service"] is not None
    assert "Usage:" not in result.stdout


def test_audit_json_includes_only_failing_images(config_home: Path) -> None:
    assert runner.invoke(app, ["config", "init"]).exit_code == 0
    assert runner.invoke(app, ["package", "add", "octocat/hello-world", "--profile", "detailed"]).exit_code == 0
    assert runner.invoke(app, ["package", "add", "octocat/fresh", "--profile", "standard"]).exit_code == 0
    _seed_stale(config_home / "data")
    _seed_current(config_home / "data", "octocat/fresh")
    result = runner.invoke(app, ["audit", "--format", "json"])
    assert result.exit_code == 0
    rows = json.loads(result.stdout)
    names = [row["Package"] for row in rows]
    assert names == ["hello-world"]
    assert rows[0]["Status"] == "Stale"


def test_package_add_keeps_a_slash_in_the_name(config_home: Path) -> None:
    result = runner.invoke(app, ["package", "add", "octocat/hello/world"])
    text = (config_home / "packages.yml").read_text(encoding="utf-8")
    assert result.exit_code == 0
    assert "hello/world" in text


def test_invalid_image_exits_2(config_home: Path) -> None:
    result = runner.invoke(app, ["package", "add", "hello-world"])
    assert result.exit_code == 2


def _seed_public(data: Path) -> None:
    database = Database(data / "cache.sqlite")
    database.put(
        "octocat/hello-world",
        "metadata",
        body=json.dumps(
            {
                "name": "hello-world",
                "visibility": "public",
                "description": "An example image",
                "created_at": "2024-01-02T03:04:05Z",
                "updated_at": "2024-06-01T00:00:00Z",
                "repository": "https://github.com/octocat/hello-world",
                "http_status": 200,
            }
        ),
        fetched_at=1_700_000_000.0,
        error=None,
    )


def _seed_stale(data: Path) -> None:
    database = Database(data / "cache.sqlite")
    database.put(
        "octocat/hello-world",
        "metadata",
        body=json.dumps(
            {
                "name": "hello-world",
                "visibility": "public",
                "updated_at": "2020-01-01T00:00:00Z",
                "created_at": "2020-01-01T00:00:00Z",
                "http_status": 200,
            }
        ),
        fetched_at=1_700_000_000.0,
        error=None,
    )


def _seed_current(data: Path, package: str) -> None:
    database = Database(data / "cache.sqlite")
    database.put(
        package,
        "metadata",
        body=json.dumps(
            {
                "name": package.split("/", 1)[1],
                "visibility": "public",
                "updated_at": "2026-11-18T00:00:00Z",
                "created_at": "2026-11-18T00:00:00Z",
                "http_status": 200,
            }
        ),
        fetched_at=1_763_596_800.0,
        error=None,
    )
