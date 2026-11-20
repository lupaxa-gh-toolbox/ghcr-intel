# GHCR Intel

GHCR Intel watches an explicit list of container images on ghcr.io.
An image is `owner/name`, which means `ghcr.io/owner/name`.
A further slash is part of that one package name.

The list is explicit. Nothing is discovered.
Groups organise the list. Profiles decide which checks run.
Results are stored in a local cache, so the list and the detail work
without a network connection. `sync` is the only command that calls GitHub.

Install and the first commands are in [Getting Started](getting-started.md).
Every command is in [Commands](usage.md).
The five checks and the status words are in [Checks](checks.md).
The shipped files are in [Configuration](reference.md).
A filled package list is in [Examples](examples.md).
