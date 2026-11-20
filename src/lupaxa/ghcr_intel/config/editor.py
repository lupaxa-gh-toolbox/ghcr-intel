"""Add a package and set its profile without calling GitHub."""

from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path

from pydantic import ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from lupaxa.ghcr_intel.config.defaults import generate_default_files
from lupaxa.ghcr_intel.config.loader import LoadedConfig, load_config
from lupaxa.ghcr_intel.config.models import PackageSpec
from lupaxa.ghcr_intel.errors import ConfigError, NotConfigured
from lupaxa.ghcr_intel.names import parse_image

__all__ = [
    "add_package",
    "remove_package",
    "set_enabled",
    "set_group",
    "set_profile",
]


def add_package(
    config_path: Path,
    image: str,
    *,
    group: str | None = None,
    profile: str | None = None,
    tags: list[str] | None = None,
) -> None:
    """
    Append one image under its owner.

    Missing configuration files are created from the shipped defaults. The
    GitHub client is not imported. A new owner block is the name and the
    package list. Group, profile, and tags are stored on that image when
    they are passed, and omitted fields stay off the package object.

    Args:
        config_path: Main configuration file.
        image: ``owner/name``. A slash after the owner is part of the name.
        group: Optional group for this image only.
        profile: Optional profile for this image only.
        tags: Local labels stored on the package object.

    Raises:
        ConfigError: The image, profile, or resulting file is invalid.

    """

    owner, name = parse_image(image)
    loaded = _ensure(config_path)
    if _duplicate(loaded.packages, owner, name):
        raise ConfigError(f"{owner}/{name} is already configured.")
    _known_profile(loaded, profile)
    chosen = [] if tags is None else list(tags)

    def mutate(owners: list[object]) -> None:
        _append(owners, owner, name, group=group, profile=profile, tags=chosen)

    _edit(config_path, loaded.packages_path, mutate)


def remove_package(config_path: Path, image: str) -> None:
    """
    Remove one image from the package file. Cached rows are left in place.

    An owner with no remaining packages is removed from the file.

    Args:
        config_path: Main configuration file.
        image: ``owner/name``.

    Raises:
        NotConfigured: The package is not in the file.
        ConfigError: The edited file does not validate.

    """

    owner, name = parse_image(image)
    loaded = load_config(config_path)

    def mutate(owners: list[object]) -> None:
        block, index = _require(owners, owner, name)
        listed = _package_list(block)
        del listed[index]
        if len(listed) == 0:
            owners.remove(block)

    _edit(config_path, loaded.packages_path, mutate)


def set_group(config_path: Path, image: str, group: str) -> None:
    """
    Store ``group`` on one package object.

    Args:
        config_path: Main configuration file.
        image: ``owner/name``.
        group: Group name. It does not change which checks run.

    Raises:
        NotConfigured: The package is not in the file.
        ConfigError: The group is blank or the edited file does not validate.

    """

    owner, name = parse_image(image)
    cleaned = group.strip()
    if cleaned == "":
        raise ConfigError("A group must not be blank.")
    loaded = load_config(config_path)

    def mutate(owners: list[object]) -> None:
        block, index = _require(owners, owner, name)
        listed = _package_list(block)
        listed[index] = _with_group(listed[index], name, cleaned)

    _edit(config_path, loaded.packages_path, mutate)


def set_enabled(config_path: Path, image: str, *, enabled: bool) -> None:
    """
    Include or skip one image during synchronisation.

    The image stays in the file. A string entry is promoted to an object.

    Args:
        config_path: Main configuration file.
        image: ``owner/name``.
        enabled: False skips the image. True includes it again.

    Raises:
        NotConfigured: The package is not in the file.
        ConfigError: The edited file does not validate.

    """

    owner, name = parse_image(image)
    loaded = load_config(config_path)

    def mutate(owners: list[object]) -> None:
        block, index = _require(owners, owner, name)
        listed = _package_list(block)
        listed[index] = _with_enabled(listed[index], name, enabled=enabled)

    _edit(config_path, loaded.packages_path, mutate)


def set_profile(config_path: Path, image: str, profile: str) -> None:
    """
    Store ``profile`` on one package object.

    A string entry is promoted to an object. The file is not created when it
    is missing.

    Args:
        config_path: Main configuration file.
        image: ``owner/name``.
        profile: Profile name that already exists.

    Raises:
        ConfigError: The profile is unknown or the file does not validate.
        NotConfigured: The package is not in the file.

    """

    owner, name = parse_image(image)
    loaded = load_config(config_path)
    _known_profile(loaded, profile)

    def mutate(owners: list[object]) -> None:
        block, index = _require(owners, owner, name)
        listed = _package_list(block)
        listed[index] = _with_profile(listed[index], name, profile)

    _edit(config_path, loaded.packages_path, mutate)


def _ensure(config_path: Path) -> LoadedConfig:
    if config_path.is_file():
        try:
            return load_config(config_path)
        except ConfigError:
            _generate(config_path)
            return load_config(config_path)
    _generate(config_path)
    return load_config(config_path)


def _generate(config_path: Path) -> None:
    directory = config_path.parent
    generate_default_files(
        (config_path, directory / "profiles.yml", directory / "packages.yml"),
        force=False,
    )


def _known_profile(loaded: LoadedConfig, profile: str | None) -> None:
    if profile is None:
        return
    if profile not in loaded.config_profiles.profiles:
        raise ConfigError(f"Unknown profile: {profile}.")


def _duplicate(packages: tuple[PackageSpec, ...], owner: str, name: str) -> bool:
    key = f"{owner.casefold()}/{name.casefold()}"
    return any(
        f"{item.owner.casefold()}/{item.name.casefold()}" == key for item in packages
    )


def _edit(
    config_path: Path,
    packages_path: Path,
    mutate: Callable[[list[object]], None],
) -> None:
    before = packages_path.read_bytes()
    document, owners = _load_document(packages_path)
    mutate(owners)
    _commit(config_path, packages_path, document, before)


def _load_document(path: Path) -> tuple[CommentedMap, list[object]]:
    loaded: object = _round_trip().load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, CommentedMap):
        raise ConfigError(f"{path} must contain an object.")
    owners = loaded.get("owners")
    if not isinstance(owners, list):
        raise ConfigError(f"{path} owners must be a list.")
    return loaded, owners


def _commit(
    config_path: Path,
    packages_path: Path,
    document: CommentedMap,
    before: bytes,
) -> None:
    temporary = packages_path.with_name(packages_path.name + ".part")
    try:
        _dump(temporary, document)
        os.replace(temporary, packages_path)
        _restrict(packages_path)
        _reload(config_path, packages_path, before)
    finally:
        temporary.unlink(missing_ok=True)


def _reload(config_path: Path, packages_path: Path, before: bytes) -> None:
    try:
        load_config(config_path)
    except (ConfigError, ValidationError) as exc:
        packages_path.write_bytes(before)
        _restrict(packages_path)
        if isinstance(exc, ConfigError):
            raise
        raise ConfigError(str(exc)) from exc


def _dump(path: Path, document: CommentedMap) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        _round_trip().dump(document, handle)


def _round_trip() -> YAML:
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.width = 4096
    return yaml


def _restrict(path: Path) -> None:
    try:
        path.chmod(0o600)
    except OSError as exc:
        raise ConfigError(f"Cannot set permissions on {path}: {exc}") from exc


def _append(
    owners: list[object],
    owner: str,
    name: str,
    *,
    group: str | None,
    profile: str | None,
    tags: list[str],
) -> None:
    block = _owner_block(owners, owner)
    if block is None:
        block = _new_owner(owner)
        owners.append(block)
    _package_list(block).append(_entry(name, group, profile, tags))


def _owner_block(owners: list[object], owner: str) -> dict[str, object] | None:
    for block in owners:
        found = _named_block(block, owner)
        if found is not None:
            return found
    return None


def _named_block(block: object, owner: str) -> dict[str, object] | None:
    if not isinstance(block, dict):
        return None
    raw = block.get("name")
    if isinstance(raw, str) and raw.casefold() == owner.casefold():
        return block
    return None


def _new_owner(owner: str) -> CommentedMap:
    block = CommentedMap()
    block["name"] = owner
    block["packages"] = CommentedSeq()
    return block


def _package_list(block: dict[str, object]) -> list[object]:
    listed = block.get("packages")
    if not isinstance(listed, list):
        raise ConfigError("packages must be a list.")
    return listed


def _entry(
    name: str,
    group: str | None,
    profile: str | None,
    tags: list[str],
) -> str | CommentedMap:
    mapping = CommentedMap()
    mapping["name"] = name
    if group is not None:
        mapping["group"] = group
    if profile is not None:
        mapping["profile"] = profile
    if tags:
        mapping["tags"] = CommentedSeq(tags)
    if len(mapping) == 1:
        return name
    return mapping


def _require(
    owners: list[object],
    owner: str,
    name: str,
) -> tuple[dict[str, object], int]:
    block = _owner_block(owners, owner)
    if block is None:
        raise NotConfigured(f"{owner}/{name} is not configured.")
    listed = _package_list(block)
    for index, item in enumerate(listed):
        if _item_name(item).casefold() == name.casefold():
            return block, index
    raise NotConfigured(f"{owner}/{name} is not configured.")


def _item_name(item: object) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        raw = item.get("name")
        if isinstance(raw, str):
            return raw
    raise ConfigError("A package entry must be a name or an object.")


def _with_profile(item: object, name: str, profile: str) -> CommentedMap:
    mapping = _as_mapping(item, name)
    mapping["profile"] = profile
    return mapping


def _with_enabled(item: object, name: str, *, enabled: bool) -> CommentedMap:
    mapping = _as_mapping(item, name)
    mapping["enabled"] = enabled
    return mapping


def _with_group(item: object, name: str, group: str) -> CommentedMap:
    mapping = _as_mapping(item, name)
    mapping["group"] = group
    return mapping


def _as_mapping(item: object, name: str) -> CommentedMap:
    if isinstance(item, str):
        mapping = CommentedMap()
        mapping["name"] = name
        return mapping
    if isinstance(item, CommentedMap):
        return item
    if isinstance(item, dict):
        return _copied(item, name)
    raise ConfigError("A package entry must be a name or an object.")


def _copied(item: dict[object, object], name: str) -> CommentedMap:
    mapping = CommentedMap()
    mapping["name"] = name
    for key, value in item.items():
        if key != "name":
            mapping[str(key)] = value
    return mapping
