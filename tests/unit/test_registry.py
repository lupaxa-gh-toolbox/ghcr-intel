import httpx
import pytest
import respx

from lupaxa.ghcr_intel.ghcr.registry import fetch_manifest

_TOKEN = "token"
_GITHUB = "github-token"

INDEX = {
    "manifests": [
        {"platform": {"os": "linux", "architecture": "amd64"}},
        {"platform": {"os": "linux", "architecture": "arm", "variant": "v7"}},
    ],
}
SINGLE = {"config": {"digest": "sha256:abc"}, "layers": []}
_CHALLENGE = 'Bearer realm="https://ghcr.io/token",service="ghcr.io"'
_MANIFEST = "https://ghcr.io/v2/octocat/hello/manifests/latest"
_ACCEPT = (
    "application/vnd.oci.image.index.v1+json, "
    "application/vnd.docker.distribution.manifest.list.v2+json, "
    "application/vnd.oci.image.manifest.v1+json, "
    "application/vnd.docker.distribution.manifest.v2+json"
)


@pytest.mark.asyncio
@respx.mock
async def test_index_and_single_manifest_platforms() -> None:
    respx.get(_MANIFEST).respond(200, json=INDEX)
    token = respx.get("https://ghcr.io/token")
    async with httpx.AsyncClient() as client:
        fetched = await fetch_manifest(client, registry_url="https://ghcr.io", owner="octocat", name="hello", tag="latest", github_token=_TOKEN)
    request = respx.calls.last.request
    assert fetched.platforms == ("linux/amd64", "linux/arm/v7")
    assert fetched.error is None
    assert token.called is False
    assert request.headers["authorization"] == "Bearer token"
    assert request.headers["accept"] == _ACCEPT


@pytest.mark.asyncio
@respx.mock
async def test_bearer_challenge_is_exchanged_once() -> None:
    manifest = respx.get(_MANIFEST)
    manifest.side_effect = [
        httpx.Response(401, headers={"www-authenticate": _CHALLENGE}),
        httpx.Response(200, json=SINGLE),
    ]
    token = respx.get("https://ghcr.io/token").respond(200, json={"token": "registry"})
    async with httpx.AsyncClient() as client:
        fetched = await fetch_manifest(client, registry_url="https://ghcr.io", owner="octocat", name="hello", tag="latest", github_token=_GITHUB)
    assert token.called
    assert token.calls[0].request.url.params["service"] == "ghcr.io"
    assert token.calls[0].request.url.params["scope"] == "repository:octocat/hello:pull"
    assert token.calls[0].request.headers["authorization"] == "Bearer github-token"
    assert manifest.calls[1].request.headers["authorization"] == "Bearer registry"
    assert manifest.calls[0].request.headers["accept"] == _ACCEPT
    assert manifest.calls[1].request.headers["accept"] == _ACCEPT
    assert fetched.platforms == ()
    assert fetched.error is None
    assert manifest.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_second_401_is_forbidden_and_does_not_exchange_again() -> None:
    respx.get(_MANIFEST).respond(401, headers={"www-authenticate": _CHALLENGE})
    token = respx.get("https://ghcr.io/token").respond(200, json={"token": "registry"})
    async with httpx.AsyncClient() as client:
        fetched = await fetch_manifest(client, registry_url="https://ghcr.io", owner="octocat", name="hello", tag="latest", github_token=_GITHUB)
    assert fetched.error == "Forbidden"
    assert fetched.platforms == ()
    assert token.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_single_manifest_stores_one_platform() -> None:
    respx.get(_MANIFEST).respond(200, json={"os": "linux", "architecture": "arm64"})
    async with httpx.AsyncClient() as client:
        fetched = await fetch_manifest(client, registry_url="https://ghcr.io", owner="octocat", name="hello", tag="latest", github_token=_TOKEN)
    assert fetched.platforms == ("linux/arm64",)
    assert fetched.error is None


@pytest.mark.asyncio
@respx.mock
async def test_slash_in_the_package_name_stays_in_the_registry_path() -> None:
    route = respx.get("https://ghcr.io/v2/octocat/hello/world/manifests/1.2.3")
    route.side_effect = [
        httpx.Response(401, headers={"www-authenticate": _CHALLENGE}),
        httpx.Response(200, json={"os": "linux", "architecture": "amd64", "variant": "v8"}),
    ]
    token = respx.get("https://ghcr.io/token").respond(200, json={"token": "registry"})
    async with httpx.AsyncClient() as client:
        fetched = await fetch_manifest(client, registry_url="https://ghcr.io/", owner="octocat", name="hello/world", tag="1.2.3", github_token=_TOKEN)
    assert route.call_count == 2
    assert token.calls[0].request.url.params["scope"] == ("repository:octocat/hello/world:pull")
    assert fetched.platforms == ("linux/amd64/v8",)
    assert fetched.error is None


@pytest.mark.asyncio
@respx.mock
async def test_missing_realm_is_forbidden_without_a_token_exchange() -> None:
    respx.get(_MANIFEST).respond(401)
    token = respx.get("https://ghcr.io/token")
    async with httpx.AsyncClient() as client:
        fetched = await fetch_manifest(client, registry_url="https://ghcr.io", owner="octocat", name="hello", tag="latest", github_token=_GITHUB)
    assert fetched.error == "Forbidden"
    assert fetched.platforms == ()
    assert token.called is False


@pytest.mark.asyncio
@respx.mock
async def test_not_found_is_unavailable() -> None:
    respx.get(_MANIFEST).respond(404)
    async with httpx.AsyncClient() as client:
        fetched = await fetch_manifest(client, registry_url="https://ghcr.io", owner="octocat", name="hello", tag="latest", github_token=_TOKEN)
    assert fetched.error == "Unavailable"
    assert fetched.platforms == ()


@pytest.mark.asyncio
@respx.mock
async def test_other_failure_is_unknown() -> None:
    respx.get(_MANIFEST).respond(500)
    async with httpx.AsyncClient() as client:
        fetched = await fetch_manifest(client, registry_url="https://ghcr.io", owner="octocat", name="hello", tag="latest", github_token=_TOKEN)
    assert fetched.error == "Unknown"
    assert fetched.platforms == ()


@pytest.mark.asyncio
@respx.mock
async def test_access_token_is_used_when_token_is_absent() -> None:
    manifest = respx.get(_MANIFEST)
    manifest.side_effect = [
        httpx.Response(401, headers={"www-authenticate": _CHALLENGE}),
        httpx.Response(200, json={"os": "linux", "architecture": "amd64"}),
    ]
    respx.get("https://ghcr.io/token").respond(200, json={"access_token": "exchanged"})
    async with httpx.AsyncClient() as client:
        fetched = await fetch_manifest(client, registry_url="https://ghcr.io", owner="octocat", name="hello", tag="latest", github_token=_GITHUB)
    assert manifest.calls[1].request.headers["authorization"] == "Bearer exchanged"
    assert fetched.platforms == ("linux/amd64",)
    assert fetched.error is None


@pytest.mark.asyncio
@respx.mock
async def test_a_failed_token_exchange_is_unknown() -> None:
    manifest = respx.get(_MANIFEST).respond(401, headers={"www-authenticate": _CHALLENGE})
    respx.get("https://ghcr.io/token").respond(503)
    async with httpx.AsyncClient() as client:
        fetched = await fetch_manifest(client, registry_url="https://ghcr.io", owner="octocat", name="hello", tag="latest", github_token=_GITHUB)
    assert fetched.error == "Unknown"
    assert manifest.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_a_transport_error_is_unknown() -> None:
    respx.get(_MANIFEST).mock(side_effect=httpx.ConnectError("offline"))
    async with httpx.AsyncClient() as client:
        fetched = await fetch_manifest(client, registry_url="https://ghcr.io", owner="octocat", name="hello", tag="latest", github_token=_TOKEN)
    assert fetched.error == "Unknown"
    assert fetched.platforms == ()
