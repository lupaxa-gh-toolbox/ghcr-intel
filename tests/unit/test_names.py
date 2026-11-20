import pytest

from lupaxa.ghcr_intel.errors import ConfigError
from lupaxa.ghcr_intel.names import api_package_segment, parse_image, registry_name


def test_parse_image_keeps_a_slash_inside_the_name() -> None:
    assert parse_image("octocat/hello/world") == ("octocat", "hello/world")


def test_parse_image_rejects_a_bare_name() -> None:
    with pytest.raises(ConfigError):
        parse_image("hello-world")


def test_api_segment_encodes_a_slash_and_the_registry_keeps_it() -> None:
    assert api_package_segment("hello/world") == "hello%2Fworld"
    assert registry_name("octocat", "hello/world") == "octocat/hello/world"
