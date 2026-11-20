"""Read one container package from the GitHub API."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import cast

import httpx

from lupaxa.ghcr_intel.ghcr.limits import limit_delay
from lupaxa.ghcr_intel.names import api_package_segment

__all__ = [
    "PackageFetch",
    "fetch_package",
]

_ACCEPT = "application/vnd.github+json"
_FORBIDDEN = "The token cannot read this image."
_MISSING = "missing"
_FAILED = "The request failed."


@dataclass(frozen=True)
class PackageFetch:
    """One package read from the organisation or user endpoint."""

    metadata_status: int
    metadata_body: dict[str, object] | None
    metadata_error: str | None
    versions_body: list[object] | None
    versions_error: str | None
    endpoint: str
    versions_status: int | None = None
    retry_after: float | None = None


async def fetch_package(
    client: httpx.AsyncClient,
    *,
    api_url: str,
    owner: str,
    name: str,
    version_limit: int,
    want_versions: bool,
    github_token: str,
) -> PackageFetch:
    """
    Read a container package from the org endpoint, then the user endpoint.

    A 404 from the organisation is the only reason to try the user. On 200,
    one versions page is read when ``want_versions`` is true. ``per_page`` is
    ``version_limit``. ``versions_status`` is that page's HTTP status, and
    ``None`` when versions are not requested. A 401 or 403 sets
    ``metadata_error`` and skips versions. A 404 from both endpoints sets
    ``metadata_error`` to ``missing``. Every request sends ``github_token``
    as ``Authorization: Bearer`` and ``Accept: application/vnd.github+json``.

    """

    segment = api_package_segment(name)
    response, endpoint = await _metadata(
        client,
        api_url,
        owner,
        segment,
        github_token,
    )
    fetched = _from_metadata(response, endpoint)
    if fetched.metadata_status != 200 or not want_versions:
        return fetched
    versions_body, versions_error, versions_status, delay = await _versions(
        client,
        api_url,
        owner,
        segment,
        endpoint,
        version_limit,
        github_token,
    )
    return replace(
        fetched,
        versions_body=versions_body,
        versions_error=versions_error,
        versions_status=versions_status,
        retry_after=delay if delay is not None else fetched.retry_after,
    )


def _request_headers(github_token: str) -> dict[str, str]:
    return {
        "Accept": _ACCEPT,
        "Authorization": f"Bearer {github_token}",
    }


async def _metadata(
    client: httpx.AsyncClient,
    api_url: str,
    owner: str,
    segment: str,
    github_token: str,
) -> tuple[httpx.Response, str]:
    headers = _request_headers(github_token)
    response = await client.get(
        _package_url(api_url, "org", owner, segment),
        headers=headers,
    )
    if response.status_code != 404:
        return response, "org"
    user = await client.get(
        _package_url(api_url, "user", owner, segment),
        headers=headers,
    )
    return user, "user"


def _from_metadata(response: httpx.Response, endpoint: str) -> PackageFetch:
    status = response.status_code
    delay = limit_delay(status, response.headers)
    if status in {401, 403}:
        return _fetch(status, None, _FORBIDDEN, endpoint, delay)
    if status == 404:
        return _fetch(status, None, _MISSING, endpoint, delay)
    if status != 200:
        return _fetch(status, None, _FAILED, endpoint, delay)
    body = _object(_payload(response))
    error = None if body is not None else _FAILED
    return _fetch(status, body, error, endpoint, delay)


def _fetch(
    status: int,
    body: dict[str, object] | None,
    error: str | None,
    endpoint: str,
    retry_after: float | None,
) -> PackageFetch:
    return PackageFetch(
        status,
        body,
        error,
        None,
        None,
        endpoint,
        retry_after=retry_after,
    )


def _package_url(api_url: str, endpoint: str, owner: str, segment: str) -> str:
    kind = "users" if endpoint == "user" else "orgs"
    root = api_url.rstrip("/")
    return f"{root}/{kind}/{owner}/packages/container/{segment}"


async def _versions(
    client: httpx.AsyncClient,
    api_url: str,
    owner: str,
    segment: str,
    endpoint: str,
    version_limit: int,
    github_token: str,
) -> tuple[list[object] | None, str | None, int, float | None]:
    url = f"{_package_url(api_url, endpoint, owner, segment)}/versions"
    response = await client.get(
        url,
        params={"per_page": version_limit},
        headers=_request_headers(github_token),
    )
    status = response.status_code
    delay = limit_delay(status, response.headers)
    if status in {401, 403}:
        return None, _FORBIDDEN, status, delay
    if status != 200:
        return None, _FAILED, status, delay
    body = _version_list(_payload(response))
    if body is None:
        return None, _FAILED, status, delay
    return body, None, status, delay


def _payload(response: httpx.Response) -> object | None:
    try:
        decoded = response.json()
    except ValueError:
        return None
    return cast("object", decoded)


def _object(payload: object | None) -> dict[str, object] | None:
    if not isinstance(payload, dict):
        return None
    body: dict[str, object] = {}
    for key, value in payload.items():
        if isinstance(key, str):
            body[key] = cast("object", value)
    return body


def _version_list(payload: object | None) -> list[object] | None:
    if not isinstance(payload, list):
        return None
    return [cast("object", item) for item in payload]
