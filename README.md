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
   release with build provenance attestations. Releases are immutable:
   once published, no asset changes and the tag is never reused, so a
   fix ships as a new `release`. The attestations are signed by the
   shared workflow in this repository, so verifying an asset names it:

   ```sh
   gh attestation verify <file> --repo onelf-pkgs/<name> --signer-repo onelf-pkgs/.github
   ```

The `Package status` issue in this repository lists every package: its
version, what upstream has, its latest release and its last build. The
control workflow rewrites it on every run.

A change to the shared workflows reaches the packages the same way: a
push to `app/` or `pkg-release.yml` here makes the `sync` workflow open
a `chore/sync` pull request in every package repository, with `@__SHARED_REF__` replaced by
the commit synced from, and the control workflow merges each once it
passes.

## Signed self-updates

A package whose bundles update themselves sets `self-update = true` in
`package.toml`. The control workflow then generates its keypair, stores
the secret half as `ONELF_SIGNING_KEY` in the repository's `release`
environment, and opens a `chore/update-key` pull request committing the
public half as `update.pub`. Once that merges, `build.sh` embeds the key
and the release signs every `.onelf`, publishing each signature beside
it. Until then the bundles update unsigned. A key on `main` is never
replaced, since every bundle released since embeds it.

## A package repository

| File | Holds |
|------|-------|
| `package.toml` | `name`, `release`, `arches`, and `[sources.<name>]` with `version`, `url`, `sha256` and `upstream`; `self-update = true` for a package whose bundles update themselves |
| `build.sh` | runs as root in a Void Linux (glibc) container, with the onelf release `pkg-release.yml` pins on `PATH`, and writes `dist/` |
| `test.sh` | optional; runs after `build.sh` in the same container |
| `update.pub` | the public half of the update key, proposed by the control workflow |
| `.github/workflows/release.yml` | synced from `app/`; never edited in place |

The source named like the package gives the version. `build/cache` is
kept between runs for anything slower to build than the package itself.
Raise `release` by hand when the recipe changes and the sources do not.

To add one, create the repository in the organization, set it to merge
pull requests by squash only, titled by the pull request, as every
repository here does:

```sh
gh api -X PATCH repos/onelf-pkgs/<name> \
  -F allow_squash_merge=true -F allow_merge_commit=false -F allow_rebase_merge=false \
  -f squash_merge_commit_title=PR_TITLE -f squash_merge_commit_message=BLANK \
  -F delete_branch_on_merge=true
```

create its `release` environment, which the publish job runs in and
main alone may use:

```sh
gh api -X PUT repos/onelf-pkgs/<name>/environments/release --input - <<'EOF'
{"deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}}
EOF
gh api -X POST repos/onelf-pkgs/<name>/environments/release/deployment-branch-policies \
  -f name=main -f type=branch
```

push its first commit to `main`, with `app/.github/workflows/release.yml`
copied in and `@__SHARED_REF__` replaced by this repository's commit,
and only then add its name to `packages.toml` and push. The sync cannot
open a pull request against a repository with no `main`, and the branch
it pushes there would become the default.

## Setup

The control and sync workflows act on other repositories, which a
workflow's `GITHUB_TOKEN` cannot do, so they authenticate as a GitHub App
owned by the organization. Create it once:

1. Organization settings, Developer settings, GitHub Apps, New GitHub
   App. No webhook. Repository permissions:
   - Contents: read and write
   - Pull requests: read and write
   - Workflows: read and write
   - Environments: read and write
   - Checks: read
   - Commit statuses: read
   - Actions: read
2. Install it on all repositories of the organization.
3. In this repository, add the variable `APP_CLIENT_ID` with the app's
   client ID (`Iv23...`, on its settings page) and the secret
   `APP_PRIVATE_KEY` with a private key generated for it.

Nothing else holds a credential. Each package repository releases with
its own `GITHUB_TOKEN`.

The organization enforces immutable releases on every repository,
which the publish job relies on:

```sh
gh api -X PUT orgs/onelf-pkgs/settings/immutable-releases -f enforced_repositories=all
```
