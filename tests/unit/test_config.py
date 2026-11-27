from pathlib import Path

from pydantic import ValidationError
import pytest

from lupaxa.ghcr_intel.config.defaults import generate_default_files
from lupaxa.ghcr_intel.config.loader import load_config
from lupaxa.ghcr_intel.config.paths import config_directory, config_file, data_directory
from lupaxa.ghcr_intel.config.profiles import resolve_profile
from lupaxa.ghcr_intel.errors import ConfigError


def test_shipped_files_load_and_the_package_list_is_empty(tmp_path: Path) -> None:
    config = tmp_path / "config.yml"
    written, kept = generate_default_files((config, tmp_path / "profiles.yml", tmp_path / "packages.yml"), force=False)
    assert len(written) == 3
    assert kept == ()
    loaded = load_config(config)
    assert loaded.packages == ()
    assert loaded.config.defaults.profile == "standard"
    assert loaded.config.display.views["tags"].list == ("Organisation", "Package", "Newest tag", "Updated")
    assert (tmp_path / "packages.yml").read_text(encoding="utf-8") == "owners: []\n"


def test_unknown_key_is_rejected(tmp_path: Path) -> None:
    config = tmp_path / "config.yml"
    generate_default_files((config, tmp_path / "profiles.yml", tmp_path / "packages.yml"), force=False)
    text = config.read_text(encoding="utf-8") + "extra: true\n"
    config.write_text(text, encoding="utf-8")
    with pytest.raises(ValidationError):
        load_config(config)


def test_string_metadata_flag_is_rejected(tmp_path: Path) -> None:
    config = tmp_path / "config.yml"
    generate_default_files((config, tmp_path / "profiles.yml", tmp_path / "packages.yml"), force=False)
    text = config.read_text(encoding="utf-8").replace("  metadata: true\n", '  metadata: "yes"\n', 1)
    config.write_text(text, encoding="utf-8")
    with pytest.raises(ValidationError):
        load_config(config)


def test_string_timeout_is_rejected(tmp_path: Path) -> None:
    config = tmp_path / "config.yml"
    generate_default_files((config, tmp_path / "profiles.yml", tmp_path / "packages.yml"), force=False)
    text = config.read_text(encoding="utf-8").replace("  timeout: 15\n", '  timeout: "15"\n', 1)
    config.write_text(text, encoding="utf-8")
    with pytest.raises(ValidationError):
        load_config(config)


def test_unknown_nested_key_is_rejected(tmp_path: Path) -> None:
    config = tmp_path / "config.yml"
    generate_default_files((config, tmp_path / "profiles.yml", tmp_path / "packages.yml"), force=False)
    text = config.read_text(encoding="utf-8").replace("  api_url: https://api.github.com\n", "  api_url: https://api.github.com\n  extra: true\n", 1)
    config.write_text(text, encoding="utf-8")
    with pytest.raises(ValidationError):
        load_config(config)


def test_explicit_false_overrides_an_inherited_true(tmp_path: Path) -> None:
    config = tmp_path / "config.yml"
    generate_default_files((config, tmp_path / "profiles.yml", tmp_path / "packages.yml"), force=False)
    loaded = load_config(config)
    resolved = resolve_profile(loaded.config_profiles, "standard", {"tags": False})
    assert resolved.enabled["metadata"] is True
    assert resolved.enabled["tags"] is False


def test_child_profile_overrides_its_parent(tmp_path: Path) -> None:
    config = tmp_path / "config.yml"
    generate_default_files((config, tmp_path / "profiles.yml", tmp_path / "packages.yml"), force=False)
    loaded = load_config(config)
    detailed = resolve_profile(loaded.config_profiles, "detailed")
    assert detailed.enabled["tags"] is True
    assert detailed.enabled["digests"] is True
    assert detailed.enabled["architectures"] is True
    assert detailed.enabled["stale"] is True
    watchlist = resolve_profile(loaded.config_profiles, "watchlist")
    assert watchlist.enabled["metadata"] is True
    assert watchlist.enabled["digests"] is True
    assert watchlist.enabled["architectures"] is False
    assert watchlist.enabled["stale"] is True


def test_omitted_flag_uses_the_file_defaults(tmp_path: Path) -> None:
    config = tmp_path / "config.yml"
    generate_default_files((config, tmp_path / "profiles.yml", tmp_path / "packages.yml"), force=False)
    (tmp_path / "profiles.yml").write_text("profiles:\n  bare:\n    tags: false\n", encoding="utf-8")
    text = config.read_text(encoding="utf-8")
    text = text.replace("profile: standard", "profile: bare", 1)
    text = text.replace("metadata: true", "metadata: false", 1)
    config.write_text(text, encoding="utf-8")
    loaded = load_config(config)
    resolved = resolve_profile(loaded.config_profiles, "bare")
    assert resolved.enabled["metadata"] is False
    assert resolved.enabled["tags"] is False
    assert resolved.enabled["digests"] is False
    assert resolved.enabled["architectures"] is False
    assert resolved.enabled["stale"] is False


def test_owner_file_parses_a_nested_package_name(tmp_path: Path) -> None:
    config = tmp_path / "config.yml"
    generate_default_files((config, tmp_path / "profiles.yml", tmp_path / "packages.yml"), force=False)
    (tmp_path / "packages.yml").write_text("owners:\n  - name: octocat\n    group: examples\n    packages:\n      - hello/world\n", encoding="utf-8")
    loaded = load_config(config)
    assert loaded.packages[0].owner == "octocat"
    assert loaded.packages[0].name == "hello/world"
    assert loaded.packages[0].group == "examples"
    assert loaded.packages[0].profile == "standard"


def test_omitted_group_is_ungrouped_and_an_object_can_override(tmp_path: Path) -> None:
    config = tmp_path / "config.yml"
    generate_default_files((config, tmp_path / "profiles.yml", tmp_path / "packages.yml"), force=False)
    (tmp_path / "packages.yml").write_text(
        "owners:\n"
        "  - name: octocat\n"
        "    packages:\n"
        "      - plain\n"
        "      - name: nested/name\n"
        "        group: examples\n"
        "        profile: detailed\n"
        "        tags:\n"
        "          - one\n"
        "        checks:\n"
        "          tags: false\n",
        encoding="utf-8",
    )
    loaded = load_config(config)
    plain, nested = loaded.packages
    assert plain.owner == "octocat"
    assert plain.name == "plain"
    assert plain.group == "ungrouped"
    assert plain.profile == "standard"
    assert plain.tags == []
    assert plain.checks == {}
    assert nested.name == "nested/name"
    assert nested.group == "examples"
    assert nested.profile == "detailed"
    assert nested.tags == ["one"]
    assert nested.checks == {"tags": False}


def test_existing_files_are_kept_until_force(tmp_path: Path) -> None:
    config = tmp_path / "config.yml"
    generate_default_files((config, tmp_path / "profiles.yml", tmp_path / "packages.yml"), force=False)
    (tmp_path / "packages.yml").write_text("owners: []\n", encoding="utf-8")
    written, kept = generate_default_files((config, tmp_path / "profiles.yml", tmp_path / "packages.yml"), force=False)
    assert written == ()
    assert len(kept) == 3
    written, kept = generate_default_files((config, tmp_path / "profiles.yml", tmp_path / "packages.yml"), force=True)
    assert len(written) == 3
    assert kept == ()


def test_new_config_directory_and_files_are_private(tmp_path: Path) -> None:
    root = tmp_path / "cfg"
    written, kept = generate_default_files((root / "config.yml", root / "profiles.yml", root / "packages.yml"), force=False)
    assert kept == ()
    assert (root.stat().st_mode & 0o777) == 0o700
    assert len(written) == 3
    for path in written:
        assert (path.stat().st_mode & 0o777) == 0o600


def test_unknown_check_name_is_rejected(tmp_path: Path) -> None:
    config = tmp_path / "config.yml"
    generate_default_files((config, tmp_path / "profiles.yml", tmp_path / "packages.yml"), force=False)
    loaded = load_config(config)
    with pytest.raises(ConfigError):
        resolve_profile(loaded.config_profiles, "standard", {"stats": True})


def test_missing_profile_is_rejected(tmp_path: Path) -> None:
    config = tmp_path / "config.yml"
    generate_default_files((config, tmp_path / "profiles.yml", tmp_path / "packages.yml"), force=False)
    loaded = load_config(config)
    with pytest.raises(ConfigError, match="missing"):
        resolve_profile(loaded.config_profiles, "missing")


def test_circular_profile_names_the_cycle(tmp_path: Path) -> None:
    config = tmp_path / "config.yml"
    generate_default_files((config, tmp_path / "profiles.yml", tmp_path / "packages.yml"), force=False)
    (tmp_path / "profiles.yml").write_text("profiles:\n  standard:\n    extends: detailed\n  detailed:\n    extends: standard\n", encoding="utf-8")
    loaded = load_config(config)
    with pytest.raises(ConfigError, match="standard") as caught:
        resolve_profile(loaded.config_profiles, "standard")
    assert "detailed" in str(caught.value)


def test_blank_package_name_is_rejected(tmp_path: Path) -> None:
    config = tmp_path / "config.yml"
    generate_default_files((config, tmp_path / "profiles.yml", tmp_path / "packages.yml"), force=False)
    (tmp_path / "packages.yml").write_text("owners:\n  - name: octocat\n    packages:\n      - ''\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(config)


def test_date_format_must_name_the_calendar_once(tmp_path: Path) -> None:
    config = tmp_path / "config.yml"
    generate_default_files((config, tmp_path / "profiles.yml", tmp_path / "packages.yml"), force=False)
    text = config.read_text(encoding="utf-8").replace("  date_format: DD/MM/YYYY HH:mm UTC\n", "  date_format: nope\n", 1)
    config.write_text(text, encoding="utf-8")
    with pytest.raises(ValidationError):
        load_config(config)


def test_paths_honour_ghcr_intel_env_and_ignore_github_intel(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GITHUB_INTEL_CONFIG_DIR", str(tmp_path / "github-config"))
    monkeypatch.setenv("GITHUB_INTEL_CONFIG", str(tmp_path / "github.yml"))
    monkeypatch.delenv("GHCR_INTEL_CONFIG", raising=False)
    monkeypatch.delenv("GHCR_INTEL_CONFIG_DIR", raising=False)
    monkeypatch.delenv("GHCR_INTEL_DATA_DIR", raising=False)
    assert config_directory().name == "ghcr-intel"
    assert data_directory().name == "ghcr-intel"
    assert config_file().name == "config.yml"
    assert "github-config" not in str(config_directory())
    monkeypatch.setenv("GHCR_INTEL_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("GHCR_INTEL_DATA_DIR", str(tmp_path / "data"))
    assert config_directory() == tmp_path / "config"
    assert data_directory() == tmp_path / "data"
    assert config_file() == tmp_path / "config" / "config.yml"
    monkeypatch.setenv("GHCR_INTEL_CONFIG", str(tmp_path / "custom.yml"))
    assert config_file() == tmp_path / "custom.yml"
