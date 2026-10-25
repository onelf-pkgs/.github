# onelf-pkgs/.github

The control repository of [onelf-pkgs](https://github.com/onelf-pkgs).
Every package lives in a repository of its own and builds there; this
repository is the one place they are maintained from.

| Path | Holds |
|------|-------|
| `packages.toml` | the package repositories under management |
| `.github/workflows/pkg-release.yml` | the reusable workflow every package builds and releases with |
| `app/` | what is synced into every package repository |
| `scripts/upstream.py` | finds and applies upstream releases |
| `scripts/control.py` | proposes, merges and reports across the repositories |
| `profile/` | the organization profile |

## How a package moves

1. **Upstream releases.** The daily `control` workflow reads each
   package's `package.toml`, and when a source has a newer release it
   opens a `chore/upstream` pull request in that repository with the new
   version and SHA-256, and `release` reset to 1.
2. **The pull request builds.** The package's `release` workflow builds
   and tests it on every architecture.
3. **Green pull requests are merged.** The next `control` run merges every
   pull request it opened whose checks all passed. Nothing waits for a
   person unless a check fails.
4. **The merge releases.** On `main`, the same workflow builds again and,
   since the tag `v<version>-<release>` does not exist yet, publishes a
   release with build provenance attestations.

The `Package status` issue in this repository lists every package: its
version, what upstream has, its latest release and its last build. The
control workflow rewrites it on every run.

A change to the shared workflows reaches the packages the same way: a
push to `app/` here makes the `sync` workflow open a `chore/sync` pull
request in every package repository, with `@__SHARED_REF__` replaced by
the commit synced from, and the control workflow merges each once it
passes.

## A package repository

| File | Holds |
|------|-------|
| `package.toml` | `name`, `release`, `arches`, and `[sources.<name>]` with `version`, `url`, `sha256` and `upstream` |
| `build.sh` | runs as root in a Void Linux (glibc) container and writes `dist/` |
| `test.sh` | optional; runs after `build.sh` in the same container |
| `.github/workflows/release.yml` | synced from `app/`; never edited in place |

The source named like the package gives the version. `build/cache` is
kept between runs for anything slower to build than the package itself.
Raise `release` by hand when the recipe changes and the sources do not.

To add one, create the repository in the organization, add its name to
`packages.toml`, and push: the sync opens its first pull request.

## Setup

The control and sync workflows act on other repositories, which a
workflow's `GITHUB_TOKEN` cannot do, so they authenticate as a GitHub App
owned by the organization. Create it once:

1. Organization settings, Developer settings, GitHub Apps, New GitHub
   App. No webhook. Repository permissions:
   - Contents: read and write
   - Pull requests: read and write
   - Workflows: read and write
   - Checks: read
   - Commit statuses: read
   - Actions: read
2. Install it on all repositories of the organization.
3. In this repository, add the variable `APP_CLIENT_ID` with the app's
   client ID (`Iv23...`, on its settings page) and the secret
   `APP_PRIVATE_KEY` with a private key generated for it.

Nothing else holds a credential. Each package repository releases with
its own `GITHUB_TOKEN`.
