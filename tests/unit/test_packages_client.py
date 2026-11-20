import httpx
import pytest
import respx

from lupaxa.ghcr_intel.ghcr.packages import fetch_package

_TOKEN = "package-token"
_ACCEPT = "application/vnd.github+json"


@pytest.mark.asyncio
@respx.mock
async def test_org_404_then_user_200_and_the_slash_is_one_segment() -> None:
    org = respx.get(
        "https://api.github.com/orgs/octocat/packages/container/hello%2Fworld",
    ).respond(404, json={"message": "Not Found"})
    user = respx.get(
        "https://api.github.com/users/octocat/packages/container/hello%2Fworld",
    ).respond(200, json={"name": "hello/world", "visibility": "public"})
    versions = respx.get(
        "https://api.github.com/users/octocat/packages/container/hello%2Fworld/versions",
    ).respond(200, json=[])
    async with httpx.AsyncClient() as client:
        fetched = await fetch_package(
            client,
            api_url="https://api.github.com",
            owner="octocat",
            name="hello/world",
            version_limit=100,
            want_versions=True,
            github_token=_TOKEN,
        )
    assert org.called
    assert user.called
    assert versions.called
    assert versions.calls[0].request.url.params["per_page"] == "100"
    assert fetched.endpoint == "user"
    assert fetched.metadata_status == 200
    assert fetched.metadata_body == {"name": "hello/world", "visibility": "public"}
    assert fetched.metadata_error is None
    assert fetched.versions_body == []
    assert fetched.versions_error is None
    assert fetched.versions_status == 200


@pytest.mark.asyncio
@respx.mock
async def test_both_404_does_not_request_versions() -> None:
    org = respx.get(url__regex=r".*/orgs/.*").respond(
        404,
        json={"message": "Not Found"},
    )
    user = respx.get(url__regex=r".*/users/.*").respond(
        404,
        json={"message": "Not Found"},
    )
    async with httpx.AsyncClient() as client:
        fetched = await fetch_package(
            client,
            api_url="https://api.github.com",
            owner="octocat",
            name="missing",
            version_limit=100,
            want_versions=True,
            github_token=_TOKEN,
        )
    assert org.call_count == 1
    assert user.call_count == 1
    assert fetched.metadata_status == 404
    assert fetched.metadata_error == "missing"
    assert fetched.endpoint == "user"
    assert fetched.versions_body is None
    assert fetched.versions_error is None
    assert fetched.versions_status is None


@pytest.mark.asyncio
@respx.mock
async def test_forbidden_skips_versions() -> None:
    respx.get(url__regex=r".*/orgs/octocat/packages/container/hello$").respond(
        403,
        json={"message": "no"},
    )
    versions = respx.get(url__regex=r".*/versions")
    async with httpx.AsyncClient() as client:
        fetched = await fetch_package(
            client,
            api_url="https://api.github.com",
            owner="octocat",
            name="hello",
            version_limit=100,
            want_versions=True,
            github_token=_TOKEN,
        )
    assert fetched.metadata_status == 403
    assert fetched.metadata_error == "The token cannot read this image."
    assert fetched.endpoint == "org"
    assert fetched.versions_body is None
    assert fetched.versions_status is None
    assert versions.called is False


@pytest.mark.asyncio
async def test_package_and_versions_send_the_bearer_token() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/versions"):
            return httpx.Response(200, json=[])
        if "/users/" in request.url.path:
            return httpx.Response(200, json={"name": "hello"})
        return httpx.Response(404, json={"message": "Not Found"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        fetched = await fetch_package(
            client,
            api_url="https://api.github.com",
            owner="octocat",
            name="hello",
            version_limit=100,
            want_versions=True,
            github_token=_TOKEN,
        )

    assert fetched.metadata_status == 200
    package = [
        request for request in seen if not request.url.path.endswith("/versions")
    ]
    versions = [request for request in seen if request.url.path.endswith("/versions")]
    assert len(package) == 2
    assert len(versions) == 1
    for request in (*package, *versions):
        assert request.headers["authorization"] == f"Bearer {_TOKEN}"
        assert request.headers["accept"] == _ACCEPT
