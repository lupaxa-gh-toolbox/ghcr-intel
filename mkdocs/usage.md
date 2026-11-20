# Commands

`sync` is the only command that calls GitHub. `list`, `package list`, `package show`, `report`, `audit`, and `tui` read the local cache.

`list` and `package list` show the same table. `--view NAME` on `list`, `package list`, `package show`, `report`, `audit`, and `tui` selects a saved view for that command. It does not write the file. An unknown name exits 2.

## Package

| Command                                             | Behaviour                                                                           |
| --------------------------------------------------- | ----------------------------------------------------------------------------------- |
| `ghcr-intel list`                                   | Show the cached table. Same as `package list`                                       |
| `ghcr-intel package add OWNER/NAME`                 | Add an image. Optional `--group`, `--profile`, and repeated `--tag`. No GitHub call |
| `ghcr-intel package list`                           | Show the cached table                                                               |
| `ghcr-intel package show OWNER NAME`                | Show one image. The owner and the name are two arguments                            |
| `ghcr-intel package remove OWNER/NAME`              | Remove an image from the list. Cached data is kept                                  |
| `ghcr-intel package set-group OWNER/NAME GROUP`     | Assign a group. Groups do not change which checks run                               |
| `ghcr-intel package set-profile OWNER/NAME PROFILE` | Choose the checks for one image                                                     |
| `ghcr-intel package enable OWNER/NAME`              | Include an image in synchronisation again                                           |
| `ghcr-intel package disable OWNER/NAME`             | Keep an image in the list and skip synchronisation                                  |

`--tag` on `package add` is a local label. It is not a ghcr.io image tag. Repeat the option to add more than one label. Those labels appear in the `Tags` column. ghcr.io tag names appear in `Image tags`.

`package disable` writes `enabled: false` on that image. The image stays in the file and `list` still shows it. The Enabled value is `no`. `sync` does not request it. `package enable` writes `enabled: true`.

`package show` exits 5 when the image is not configured.

## Sync, Report, and Audit

| Command                   | Behaviour                                                             |
| ------------------------- | --------------------------------------------------------------------- |
| `ghcr-intel sync`         | Refresh stale or missing information. A terminal shows a progress bar |
| `ghcr-intel sync --force` | Ignore cache freshness and `sync.interval`                            |
| `ghcr-intel report`       | Write a report of the selected list. The default is Markdown          |
| `ghcr-intel audit`        | Show images with a failing check                                      |
| `ghcr-intel tui`          | Open the terminal interface on the cached view                        |

On a terminal, `sync` shows a progress bar on stderr. Otherwise one line is printed when the completed count changes, and again when a rate-limit wait starts or ends. The request count on that line is the total for the run.

`sync.concurrency` images run at once. The default is 10. `sync --concurrency N` overrides that number for one run. A value below 1 exits 2.

`list`, `sync`, and `report` accept `--group` and `--profile`. The match ignores case. `sync` then skips disabled images in that set.

When GitHub names a wait, sync pauses and the progress line says `waiting for GitHub rate limit`. A 429 that does not name a wait is retried immediately.

`report` uses the selected list, the human labels, and `display.date_format`. Markdown, JSON, and CSV use the same cell text. The Markdown title is `# GHCR Intel Report`. A cell escapes `|` as `\|` and a newline as `<br>`.

`report --output FILE` writes that text to a file. The file stays plain.

`audit` includes images whose status is Stale, Error, Unavailable, or Forbidden, and images whose architectures check failed. It accepts `table` and `json` only.

`--fail-on-checks` on `list`, `package list`, `package show`, `sync`, `report`, and `audit` exits 6 when that set is non-empty.

## Configuration Commands

| Command                          | Behaviour                                                          |
| -------------------------------- | ------------------------------------------------------------------ |
| `ghcr-intel config init`         | Write the three default files. `--force` overwrites existing files |
| `ghcr-intel config show`         | Print the resolved settings. The token is not included             |
| `ghcr-intel config validate`     | Load the files and report the package count                        |
| `ghcr-intel config paths`        | Print the configuration and cache paths                            |
| `ghcr-intel profile list`        | List profile names                                                 |
| `ghcr-intel profile tree`        | Show each profile and its parent                                   |
| `ghcr-intel profile validate`    | Resolve every profile                                              |
| `ghcr-intel profile show NAME`   | Show the effective checks for one profile                          |
| `ghcr-intel cache info`          | Show the cache path, size, and row counts                          |
| `ghcr-intel cache clear --yes`   | Delete cached documents and sync runs. The image list stays        |
| `ghcr-intel cache vacuum`        | Rebuild the SQLite file                                            |

`config init` keeps a file that already exists unless `--force` is passed. When nothing is written, the command exits 2.

`profile show` prints each check as `true` or `false` after inheritance. The check names are `metadata`, `tags`, `digests`, `architectures`, and `stale`.

## Formats

JSON, CSV, and Markdown stay plain. The coloured table is the human view.

| Command        | `--format` values                  | Default    |
| -------------- | ---------------------------------- | ---------- |
| `list`         | `table`, `json`, `csv`, `markdown` | `table`    |
| `package list` | `table`, `json`, `csv`, `markdown` | `table`    |
| `package show` | `table`, `json`, `csv`, `markdown` | `table`    |
| `report`       | `table`, `json`, `csv`, `markdown` | `markdown` |
| `audit`        | `table`, `json`                    | `table`    |

An unknown format exits 2.

A terminal table uses colour. Headers are bold cyan. The detail field column is cyan. Public is green, private is magenta, a stale or forbidden value is yellow, and an error is red.

JSON, CSV, Markdown, and a report file stay plain.

## Shared Flags

| Flag        | Behaviour                                                      |
| ----------- | -------------------------------------------------------------- |
| `--config`  | Use this main configuration file instead of the default path   |
| `--verbose` | Write informational logs to stderr                             |
| `--debug`   | Write debug logs to stderr. A GitHub token is redacted         |
| `--version` | Print the version and exit                                     |

## Terminal Interface

With no arguments, an interactive terminal opens the terminal interface on the cached view. Otherwise the help text is printed.

The summary and the search box sit under the header. Search filters the owner, package name, and description.

| Key      | Action                                         |
| -------- | ---------------------------------------------- |
| Enter    | Open the image detail                          |
| Escape   | Close a popup, cancel a sync, or clear search  |
| `/`      | Search the owner, package, and description     |
| `r`      | Synchronise                                    |
| `p`      | Profiles                                       |
| `c`      | Configuration paths                            |
| `v`      | Pick a saved view for this session             |
| `Ctrl+Q` | Quit                                           |

`v` does not write the file. `q` alone does not quit, because the search field uses that key.

`ghcr-intel tui --view tags` opens on the shipped example view.

## Exit Codes

| Code | Meaning                                           |
| ---- | ------------------------------------------------- |
| 0    | Success                                           |
| 1    | General failure                                   |
| 2    | Configuration or usage                            |
| 3    | Authentication                                    |
| 4    | Sync incomplete or cancelled                      |
| 5    | Package is not configured                         |
| 6    | `--fail-on-checks`, and at least one check failed |
