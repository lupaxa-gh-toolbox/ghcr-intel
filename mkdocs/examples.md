# Examples

Documentation examples use `octocat/hello-world` and the group `examples`. The generated `packages.yml` stays `owners: []`. Copy the list below when you want the documented image.

## One Image

```yaml
owners:
  - name: octocat
    group: examples
    profile: standard
    packages:
      - hello-world
```

`hello-world` means `ghcr.io/octocat/hello-world`.

Add the same image from the command line. This does not call GitHub.

```bash
ghcr-intel package add octocat/hello-world --group examples --profile standard
ghcr-intel config validate
ghcr-intel sync
ghcr-intel package show octocat hello-world
```

A package may be a string, as above, or an object. An object may set `profile`, `group`, and `tags`. Omitted profile uses the owner profile, then `standard`. Omitted group is `ungrouped`.

## Tags and Image Tags

`Tags` are local labels from `packages.yml`. Pass them with a repeated `--tag` on `package add`, or set `tags` on the package object. A local label is not a ghcr.io tag.

`Image tags` are the ghcr.io tag names found on the fetched versions. They appear after `sync` when the `tags` check is on. The shipped `standard` profile turns that check on.

## Default Columns and the Tags View

`display.view` stays `default`. `package list` then uses seven columns, in order:

1. Organisation
2. Package
3. Visibility
4. Status
5. Updated
6. Newest tag
7. Linked repository

The shipped `tags` view is a worked example:

```yaml
display:
  view: default
  views:
    tags:
      list:
        - Organisation
        - Package
        - Newest tag
        - Updated
```

Select it for one command. The file is not written.

```bash
ghcr-intel package list --view tags
```

An unknown view name exits 2. The same `--view` option works on `package show`, `report`, `audit`, and `tui`.
