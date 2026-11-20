# Checks

A profile decides which checks run for an image. The names are `metadata`, `tags`, `digests`, `architectures`, and `stale`. Any other check name is a configuration error.

An omitted flag uses the parent profile, then `defaults`. An explicit false overrides an inherited true. The shipped profiles are in [Configuration](reference.md).

A field whose check is off is blank on a list. On a detail it shows `SKIPPED — {check} is disabled.`

`Tags` and `Image tags` are different columns. `Tags` is the local label list from `packages.yml`. It is not a check result. `Image tags` is the ghcr.io tag names found on the fetched versions, and it follows the `tags` check.

## Metadata

When `metadata` is on, sync requests the organisation package endpoint. If that returns 404, it requests the user package endpoint. The package name is one URL path segment. A slash in the name is encoded as `%2F`.

The stored fields are the name, visibility, description, created time, updated time, and linked repository. Visibility and the linked repository are metadata fields. They are not separate checks.

## Tags

When `tags`, `digests`, `architectures`, or `stale` is on, sync reads the newest `sync.version_limit` versions. The default limit is 100. GitHub's newest-first order is kept. A full page is stored as truncated.

`Image tags`, Newest tag, and Tag count come from that list.
Versions fetched and Versions truncated follow the same request.
When that request did not run, both show the skipped `tags` sentence,
because the versions request exists to serve tags.

## Digests

The digest is the `name` of the newest version that has a tag. When every fetched version is untagged, the digest is the newest version's `name` and Newest tag is empty.

Digests come from the version list. The manifest request is not used to discover them. The `digests` check has to be on for the Digest column to show that value.

## Architectures

When `architectures` is on and a tagged version was found, sync requests the OCI manifest for the newest tagged version. A manifest index stores its platform list. A single-platform manifest stores that one platform.

A failed manifest does not change Status. Architectures shows Forbidden, Unavailable, or Unknown.

## Stale

When `stale` is on:

- The package is stale when `updated` is older than `checks.stale.package_days` (180).
- The newest tag is stale when that version is older than `checks.stale.tag_days` (90).
- Either condition fails the check.

When `stale` is off, the timestamps are still stored and the image is not failed for age. A successfully read image is Current. The Updated cell still shows the time. The detail sentence is "Metadata was read."

When the check is on and both thresholds are inside the window, the sentence is "The package and its newest tag are inside the stale window."

## Status

The list Status is the short word. The detail Status is one sentence and does not repeat that word.

| Short word       | When                                                           | Detail sentence                                                                 |
| ---------------- | -------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| Not synchronised | No stored metadata                                             | This image has not been synchronised.                                           |
| Unavailable      | Both package endpoints returned 404                            | GitHub could not find this image.                                               |
| Forbidden        | 401 or 403 from the package endpoint                           | The token cannot read this image.                                               |
| Error            | Metadata failed for another reason, or the version list failed | The stored error text.                                                          |
| Stale            | The stale check is on and a threshold is exceeded              | The package was last updated N days ago, the newest tag is N days old, or both. |
| Current          | The image was read and it is not stale                         | The window sentence, or "Metadata was read." when the stale check is off.       |

A failed version list sets Status to Error and stores Error on the tag and digest fields.

When the metadata check is off, Status comes from the versions request: Current, Forbidden, Unavailable, Error, or Not synchronised. Stale still applies when that check is on and a timestamp was stored.

When metadata failed, the detail starts with a Metadata error row. Its value is the stored error text. `Metadata error` is not a catalogue label. Naming it in a view is a configuration error.

## Audit

`audit` lists images whose Status is Stale, Error, Unavailable, or Forbidden, and images whose architectures check failed. `--fail-on-checks` exits 6 when that set is non-empty.

The cache word `stale` is not the stale check. It means the metadata document is at least `metadata_ttl` old. A missing record is `not-synchronised`. The version and manifest windows do not change that word.
