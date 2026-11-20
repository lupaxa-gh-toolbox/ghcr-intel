<p align="center">
  <a href="https://github.com/lupaxa-gh-toolbox">
    <img src="https://raw.githubusercontent.com/the-lupaxa-project/brand-assets/master/logos/organisations/gh-toolbox/readme-logo.png" alt="GitHub Toolbox" />
  </a>
</p>

<h1 align="center">GHCR Intel</h1>

**GHCR Intel** watches an explicit list of container images on ghcr.io.
It stores the results locally and can show them without a network connection.
Groups organise the list. Profiles decide which checks run.
`sync` is the only command that calls GitHub.

## Installation

Install the latest release from PyPI.

```bash
pip install lupaxa-ghcr-intel
```

Verify the installation.

```bash
ghcr-intel --help
```

## Quick Start

Write the three default files.

```bash
ghcr-intel config init
```

The generated `packages.yml` is empty:

```yaml
owners: []
```

An existing file is kept unless you pass `--force`.

> **Note:** The example below is not written into that file. A new install does not sync an image it does not own.

```yaml
owners:
  - name: octocat
    group: examples
    profile: standard
    packages:
      - hello-world
```

`hello-world` means `ghcr.io/octocat/hello-world`.

`Tags` are local labels on an image. `Image tags` are the ghcr.io tag names found on the fetched versions.

The default list has seven columns, in order: Organisation, Package,
Visibility, Status, Updated, Newest tag, and Linked repository.
`display.view` stays `default`. `--view tags` selects the shipped example
view and does not write the file. That view is Organisation, Package,
Newest tag, and Updated.

```bash
ghcr-intel list --view tags
```

## Documentation

The site source is in `mkdocs/`. Published documentation is at [ghcr-intel.thelupaxaproject.org](https://ghcr-intel.thelupaxaproject.org/).

<a href="https://github.com/the-lupaxa-project">
    <img src="https://raw.githubusercontent.com/the-lupaxa-project/brand-assets/master/logos/components/footer-for-child-orgs.svg" alt="The Lupaxa Project Footer" width="100%" />
</a>
