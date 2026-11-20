# Getting Started

Install the package, write the default files, and read the cached list. The site source for these pages is `mkdocs/`.

## Requirements

- The `lupaxa-ghcr-intel` package.
- The GitHub CLI (`gh`) when you run `sync`. The token is read with `gh auth token`. It is not stored in the configuration or the cache.

## Install

```bash
pip install lupaxa-ghcr-intel
ghcr-intel --help
```

## Quick Start

Write the three default files. They are `config.yml`, `profiles.yml`, and `packages.yml` in `~/.config/ghcr-intel/`. The directory is mode `700`. Each file written is mode `600`.

```bash
ghcr-intel config init
```

An existing file is kept unless you pass `--force`. When all three files already exist and `--force` is omitted, the command exits 2.

The generated `packages.yml` is empty:

```yaml
owners: []
```

> **Note:** That file does not name an image. A new install does not sync an image it does not own.

One documented list uses `octocat`, `hello-world`, and the group `examples`:

```yaml
owners:
  - name: octocat
    group: examples
    profile: standard
    packages:
      - hello-world
```

`hello-world` means `ghcr.io/octocat/hello-world`. `profile: standard` is the shipped default profile. Omit it and the image still uses `standard`.

`Tags` are local labels stored on the image. `Image tags` are the ghcr.io tag names found on the fetched versions. They are different columns.

The default list has seven columns, in this order:

| Column            | Meaning                            |
| ----------------- | ---------------------------------- |
| Organisation      | GitHub user or organisation        |
| Package           | Image name                         |
| Visibility        | `public`, `private`, or `internal` |
| Status            | Short status word                  |
| Updated           | Package update time                |
| Newest tag        | Newest ghcr.io tag                 |
| Linked repository | Repository linked on the package   |

`display.view` stays `default`, so those seven columns are what `package list` shows.
The shipped `tags` entry is a worked example.
`--view tags` selects it for that command and does not write the file.
The columns are Organisation, Package, Newest tag, and Updated.

```bash
ghcr-intel package list --view tags
```

`package add` does not call GitHub. The first add creates the default files when they are missing.

```bash
ghcr-intel package add octocat/hello-world --group examples
ghcr-intel config validate
ghcr-intel sync
ghcr-intel package list
ghcr-intel package show octocat hello-world
```

A missing token exits 3 before any request. Public and private images use the same token.

With no arguments, an interactive terminal opens the terminal interface on the cached view. A non-interactive terminal prints help.
