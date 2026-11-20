# Configuration

`config init` writes the three files below. Unknown keys and invalid types are rejected. An omitted flag uses the parent profile, then `defaults`. An explicit false overrides an inherited true.

## Paths

| Path                                     | Role                    |
| ---------------------------------------- | ----------------------- |
| `~/.config/ghcr-intel/config.yml`        | Main file               |
| `~/.config/ghcr-intel/profiles.yml`      | Profiles                |
| `~/.config/ghcr-intel/packages.yml`      | The explicit image list |
| `~/.local/share/ghcr-intel/cache.sqlite` | Local cache             |

The directory is mode `700`. Each file written is mode `600`. `GITHUB_INTEL` is not reused.

| Variable                | Effect                                     |
| ----------------------- | ------------------------------------------ |
| `GHCR_INTEL_CONFIG`     | Select a different main file               |
| `GHCR_INTEL_CONFIG_DIR` | Select a different configuration directory |
| `GHCR_INTEL_DATA_DIR`   | Move the cache                             |

`config paths` prints the configuration file, the profiles file, the packages file, and the cache.

## Main File

`config.yml`:

```yaml
version: 1

github:
  api_url: https://api.github.com
  registry_url: https://ghcr.io

files:
  profiles: profiles.yml
  packages: packages.yml

defaults:
  profile: standard
  metadata: true
  tags: true
  digests: false
  architectures: false
  stale: false

display:
  date_format: DD/MM/YYYY HH:mm UTC
  view: default
  views:
    tags:
      list:
        - Organisation
        - Package
        - Newest tag
        - Updated

sync:
  concurrency: 10
  timeout: 15
  retries: 3
  interval: 0
  version_limit: 100
  cache:
    enabled: true
    metadata_ttl: 3600
    versions_ttl: 1800
    manifests_ttl: 21600

checks:
  stale:
    package_days: 180
    tag_days: 90

logging:
  level: INFO
```

`display.view` stays `default`. The seven default columns are Organisation,
Package, Visibility, Status, Updated, Newest tag, and Linked repository.
The `tags` entry is a worked example.
`--view tags` selects it and does not write the file.

`sync.concurrency` is how many images are refreshed at once. The default is 10. `sync --concurrency` overrides it for one run.

A package entry may set `enabled: false`. The image stays in the file and sync does not request it. Omitting `enabled` includes the image.

`sync.interval` of 0 means a new sync is always allowed.
It blocks starting a sync when it is greater than 0.
It does not change the cache word.
`sync.version_limit` is the number of newest package versions read for one image.
The minimum is 1.

`metadata_ttl` (3600 seconds) decides whether the metadata document is refetched.
`versions_ttl` (1800 seconds) decides whether the version list is refetched.
`manifests_ttl` (21600 seconds) decides whether the manifest is refetched.
`--force` refetches inside those windows and ignores `sync.interval`.

`display.date_format` uses `YYYY`, `MM`, and `DD` once each.
`HH` and `mm` are optional and appear together.
The first `MM` is the month.
When the clock is included, the second `MM` is the minute.
`ss` and `UTC` are optional with the time.

`logging.level` is `CRITICAL`, `ERROR`, `WARNING`, `INFO`, or `DEBUG`. `--verbose` selects INFO and `--debug` selects DEBUG for that command. Log lines do not include the GitHub token.

## Profiles File

`profiles.yml`:

```yaml
profiles:
  base:
    metadata: true
    tags: false
    digests: false
    architectures: false
    stale: false

  standard:
    extends: base
    tags: true

  detailed:
    extends: standard
    digests: true
    architectures: true
    stale: true

  watchlist:
    extends: standard
    digests: true
    stale: true
```

| Profile     | Extends    | What it turns on                        |
| ----------- | ---------- | --------------------------------------- |
| `base`      |            | `metadata`. The other checks stay off   |
| `standard`  | `base`     | `tags`. This is `defaults.profile`      |
| `detailed`  | `standard` | `digests`, `architectures`, and `stale` |
| `watchlist` | `standard` | `digests` and `stale`                   |

`profile show NAME` prints the flags after this inheritance.

## Packages File

The generated `packages.yml` is empty:

```yaml
owners: []
```

A filled file names GitHub users and organisations the same way. This example uses only `octocat`, `hello-world`, and the group `examples`:

```yaml
owners:
  - name: octocat
    group: examples
    profile: standard
    packages:
      - hello-world
```

`hello-world` means `ghcr.io/octocat/hello-world`. A package may be a string or an object. An object may set `profile`, `group`, and `tags`. Omitted profile uses the owner profile, then `defaults.profile` (`standard`). Omitted group is `ungrouped`.

`tags` on a package object are local labels. They show in the `Tags` column. They are not ghcr.io image tags. Those show in `Image tags` after a sync with the `tags` check on.

## Catalogue

These labels are valid on a list and on a detail:

Organisation, Package, Visibility, Description, Linked repository, Group,
Profile, Tags, Enabled, Status, Cache, Created, Updated, Synchronised,
Newest tag, Image tags, Tag count, Versions fetched, Versions truncated,
Digest, Architectures, Stale.

Right-aligned labels are Tag count and Versions fetched.

A written list shows every named field. A field whose check is off is blank on that list. A written detail shows only the named fields.
