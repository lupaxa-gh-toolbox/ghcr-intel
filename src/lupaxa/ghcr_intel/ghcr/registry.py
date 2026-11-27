"""Read one image manifest from the container registry."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import cast

import httpx

from lupaxa.ghcr_intel.ghcr.limits import limit_delay
from lupaxa.ghcr_intel.names import registry_name

__all__ = [
    "ManifestFetch",
    "fetch_manifest",
]

_ACCEPT = ", ".join(
    (
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    )
)
_FORBIDDEN = "Forbidden"
_UNAVAILABLE = "Unavailable"
_UNKNOWN = "Unknown"
_CHALLENGE_PARAM = re.compile(r'([A-Za-z][A-Za-z0-9]*)\s*=\s*"([^"]*)"')


@dataclass(frozen=True)
class ManifestFetch:
    """Platforms from one manifest, or a short error word."""

    platforms: tuple[str, ...]
    error: str | None
    retry_after: float | None = None


async def fetch_manifest(client: httpx.AsyncClient, *, registry_url: str, owner: str, name: str, tag: str, github_token: str) -> ManifestFetch:
    """
    Read one manifest. Exchange a bearer challenge once.

    The first request sends the GitHub token. A 401 with a ``realm`` is
    exchanged for one registry token and the manifest is retried once. A
    missing realm, or a second 401, is Forbidden. 404 is Unavailable. Any
    other failure is Unknown.

    """

    url = _manifest_url(registry_url, owner, name, tag)
    try:
        response = await _manifest(client, url, github_token)
    except httpx.RequestError:
        return _failed(_UNKNOWN)
    if response.status_code != 401:
        return _from_response(response)
    return await _after_challenge(client, url, response, owner, name, github_token)


async def _after_challenge(client: httpx.AsyncClient, url: str, response: httpx.Response, owner: str, name: str, github_token: str) -> ManifestFetch:
    challenge = _challenge(response.headers.get("www-authenticate"))
    if challenge is None:
        return _failed(_FORBIDDEN)
    realm, service = challenge
    try:
        registry_token = await _registry_token(client, realm, service, owner, name, github_token)
    except httpx.RequestError:
        return _failed(_UNKNOWN)
    if registry_token is None:
        return _failed(_UNKNOWN)
    try:
        retried = await _manifest(client, url, registry_token)
    except httpx.RequestError:
        return _failed(_UNKNOWN)
    return _from_response(retried)


async def _manifest(client: httpx.AsyncClient, url: str, token: str) -> httpx.Response:
    return await client.get(
        url,
        headers={
            "Accept": _ACCEPT,
            "Authorization": f"Bearer {token}",
        },
    )


def _manifest_url(registry_url: str, owner: str, name: str, tag: str) -> str:
    root = registry_url.rstrip("/")
    repository = registry_name(owner, name)
    return f"{root}/v2/{repository}/manifests/{tag}"


async def _registry_token(client: httpx.AsyncClient, realm: str, service: str | None, owner: str, name: str, github_token: str) -> str | None:
    params = {"scope": f"repository:{registry_name(owner, name)}:pull"}
    if service is not None:
        params["service"] = service
    response = await client.get(realm, params=params, headers={"Authorization": f"Bearer {github_token}"})
    if response.status_code != 200:
        return None
    return _issued(_object(_payload(response)))


def _issued(body: dict[str, object] | None) -> str | None:
    if body is None:
        return None
    for key in ("token", "access_token"):
        value = body.get(key)
        if isinstance(value, str) and value != "":
            return value
    return None


def _challenge(header: str | None) -> tuple[str, str | None] | None:
    if header is None:
        return None
    scheme, _, _rest = header.partition(" ")
    if scheme.lower() != "bearer":
        return None
    fields = {match.group(1).lower(): match.group(2) for match in _CHALLENGE_PARAM.finditer(header)}
    realm = fields.get("realm", "")
    if realm == "":
        return None
    service = fields.get("service", "")
    if service == "":
        return realm, None
    return realm, service


def _from_response(response: httpx.Response) -> ManifestFetch:
    status = response.status_code
    delay = limit_delay(status, response.headers)
    if status == 401:
        return _failed(_FORBIDDEN, delay)
    if status == 404:
        return _failed(_UNAVAILABLE, delay)
    if status != 200:
        return _failed(_UNKNOWN, delay)
    body = _object(_payload(response))
    if body is None:
        return _failed(_UNKNOWN)
    return ManifestFetch(_platforms(body), None)


def _platforms(body: dict[str, object]) -> tuple[str, ...]:
    listed = body.get("manifests")
    if isinstance(listed, list):
        return _index_platforms(listed)
    single = _named_platform(body)
    if single is None:
        return ()
    return (single,)


def _index_platforms(listed: list[object]) -> tuple[str, ...]:
    found: list[str] = []
    for item in listed:
        platform = _index_platform(item)
        if platform is not None:
            found.append(platform)
    return tuple(found)


def _index_platform(item: object) -> str | None:
    entry = _object(item)
    if entry is None:
        return None
    return _named_platform(_object(entry.get("platform")))


def _named_platform(body: dict[str, object] | None) -> str | None:
    if body is None:
        return None
    system = body.get("os")
    architecture = body.get("architecture")
    if not isinstance(system, str) or system == "":
        return None
    if not isinstance(architecture, str) or architecture == "":
        return None
    variant = body.get("variant")
    if isinstance(variant, str) and variant != "":
        return f"{system}/{architecture}/{variant}"
    return f"{system}/{architecture}"


def _failed(error: str, retry_after: float | None = None) -> ManifestFetch:
    return ManifestFetch((), error, retry_after)


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
