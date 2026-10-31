#!/usr/bin/env python3
"""Manage the package repositories listed in packages.toml.

Usage:
    control.py upstream     open or refresh a `chore/upstream` pull request
                            in every repository whose sources fell behind
    control.py sync <sha>   open or refresh a `chore/sync` pull request in
                            every repository whose copy of app/ differs
    control.py merge        merge the pull requests this bot opened once
                            every check on them passed
    control.py keys         give every self-updating package its update
                            key and open a `chore/update-key` pull request
                            committing the public half
    control.py status       print a Markdown table of every repository

GH_TOKEN is the GitHub App's installation token. BOT_NAME and BOT_EMAIL
are the identity its commits carry, and BOT_LOGIN the login its pull
requests are opened under. ONELF names the onelf binary `keys` generates
keys with.
"""

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ORG = "onelf-pkgs"
ROOT = Path(__file__).resolve().parent.parent
PLACEHOLDER = "@__SHARED_REF__"
PASSED = {"SUCCESS", "NEUTRAL", "SKIPPED"}


def repos():
    with open(ROOT / "packages.toml", "rb") as f:
        return tomllib.load(f)["repos"]


def run(*args, cwd=None):
    return subprocess.run(
        args, cwd=cwd, check=True, text=True, stdout=subprocess.PIPE
    ).stdout.strip()


def gh_json(*args):
    return json.loads(run("gh", *args))


def exists(path):
    return subprocess.run(["gh", "api", path], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode == 0


def clone(repo, dest):
    token = os.environ["GH_TOKEN"]
    url = f"https://x-access-token:{token}@github.com/{ORG}/{repo}.git"
    run("git", "clone", "--quiet", "--depth", "1", url, str(dest))
    run("git", "config", "user.name", os.environ["BOT_NAME"], cwd=dest)
    run("git", "config", "user.email", os.environ["BOT_EMAIL"], cwd=dest)


def propose(repo, checkout, branch, title, body):
    """Commit what changed in checkout to branch and keep one pull request
    open for it. An unchanged proposal is left alone, so its checks do not
    run again for nothing."""
    run("git", "add", "-A", cwd=checkout)
    if not run("git", "status", "--porcelain", cwd=checkout):
        return
    run("git", "commit", "--quiet", "-m", title, cwd=checkout)
    remote = subprocess.run(
        ["git", "fetch", "--quiet", "--depth", "1", "origin", branch],
        cwd=checkout,
    )
    if remote.returncode == 0 and not run(
        "git", "diff", "--name-only", "FETCH_HEAD", "HEAD", cwd=checkout
    ):
        print(f"{repo}: {branch} is already proposed")
        return
    run("git", "push", "--quiet", "--force", "origin", f"HEAD:{branch}", cwd=checkout)
    existing = gh_json(
        "pr", "list", "--repo", f"{ORG}/{repo}", "--head", branch,
        "--state", "open", "--json", "number",
    )
    if existing:
        number = str(existing[0]["number"])
        run("gh", "pr", "edit", number, "--repo", f"{ORG}/{repo}",
            "--title", title, "--body", body)
        print(f"{repo}: updated #{number}")
    else:
        print(run("gh", "pr", "create", "--repo", f"{ORG}/{repo}", "--head", branch,
                  "--title", title, "--body", body))


def upstream():
    for repo in repos():
        with tempfile.TemporaryDirectory() as tmp:
            checkout = Path(tmp) / repo
            clone(repo, checkout)
            moved = run(sys.executable, str(ROOT / "scripts/upstream.py"),
                        "bump", str(checkout / "package.toml"))
            if not moved:
                print(f"{repo}: current")
                continue
            names = ", ".join(line.split()[0] + " " + line.split()[-1]
                              for line in moved.splitlines())
            title = f"chore: follow upstream to {names}"
            body = ("Upstream released:\n\n"
                    + "\n".join(f"- {line}" for line in moved.splitlines())
                    + "\n\nMerged by the control repository once every check passes.")
            propose(repo, checkout, "chore/upstream", title, body)


def sync(sha):
    app = ROOT / "app"
    for repo in repos():
        with tempfile.TemporaryDirectory() as tmp:
            checkout = Path(tmp) / repo
            clone(repo, checkout)
            for source in sorted(p for p in app.rglob("*") if p.is_file()):
                target = checkout / source.relative_to(app)
                target.parent.mkdir(parents=True, exist_ok=True)
                text = source.read_text().replace(PLACEHOLDER, f"@{sha}")
                target.write_text(text)
                shutil.copymode(source, target)
            propose(repo, checkout, "chore/sync",
                    "chore: sync the shared workflows",
                    f"Synced from {ORG}/.github@{sha}.\n\n"
                    "Merged by the control repository once every check passes.")


def merge():
    login = os.environ["BOT_LOGIN"]
    for repo in repos():
        pulls = gh_json(
            "pr", "list", "--repo", f"{ORG}/{repo}", "--author", f"app/{login}",
            "--state", "open", "--json", "number,title,statusCheckRollup,mergeable",
        )
        for pull in pulls:
            checks = pull["statusCheckRollup"]
            states = {c.get("conclusion") or c.get("state") for c in checks}
            if pull["mergeable"] != "MERGEABLE" or not checks or not states <= PASSED:
                print(f"{repo}#{pull['number']}: waiting ({', '.join(sorted(map(str, states))) or 'no checks'})")
                continue
            run("gh", "pr", "merge", str(pull["number"]), "--repo", f"{ORG}/{repo}",
                "--squash", "--delete-branch")
            print(f"{repo}#{pull['number']}: merged {pull['title']}")


def keys():
    """Store a new keypair's secret as ONELF_SIGNING_KEY in the `release`
    environment of every package with `self-update = true` that has no key
    yet, and propose its public half as update.pub. A key on main is never
    replaced, since every bundle released since embeds it. The environment,
    usable by main alone, is created by hand when the repository is."""
    failed = []
    for repo in repos():
        full = f"{ORG}/{repo}"
        package = tomllib.loads(run("gh", "api", f"repos/{full}/contents/package.toml",
                                    "-H", "Accept: application/vnd.github.raw"))
        if not package.get("self-update"):
            continue
        if not exists(f"repos/{full}/environments/release"):
            print(f"{repo}: no release environment; create it as README.md says")
            failed.append(repo)
            continue
        policies = gh_json("api", f"repos/{full}/environments/release/deployment-branch-policies")
        allowed = [f"{b['type']}:{b['name']}" for b in policies["branch_policies"]]
        if allowed != ["branch:main"]:
            print(f"{repo}: the release environment must allow main alone, not {allowed or 'any branch'}")
            failed.append(repo)
            continue

        committed = exists(f"repos/{full}/contents/update.pub")
        stored = exists(f"repos/{full}/environments/release/secrets/ONELF_SIGNING_KEY")
        if committed and stored:
            print(f"{repo}: has its key")
            continue
        if committed:
            print(f"{repo}: update.pub is on main but the release environment has no "
                  "ONELF_SIGNING_KEY; restore it or rotate the key by hand")
            failed.append(repo)
            continue
        pending = gh_json("pr", "list", "--repo", full, "--head", "chore/update-key",
                          "--state", "open", "--json", "number")
        if stored and pending:
            print(f"{repo}: key proposed in #{pending[0]['number']}")
            continue

        # No released bundle embeds a key that is not on main, so a new
        # pair may replace a secret left without its pull request.
        with tempfile.TemporaryDirectory() as tmp:
            secret, public = Path(tmp) / "key", Path(tmp) / "pub"
            run(os.environ["ONELF"], "key", "new", "--secret", str(secret),
                "--public", str(public))
            subprocess.run(["gh", "secret", "set", "ONELF_SIGNING_KEY", "--env", "release",
                            "--repo", full],
                           input=base64.b64encode(secret.read_bytes()).decode(),
                           text=True, check=True)
            checkout = Path(tmp) / repo
            clone(repo, checkout)
            shutil.copyfile(public, checkout / "update.pub")
            propose(repo, checkout, "chore/update-key",
                    "feat: sign self-updates with this package's key",
                    "Commits the public half of this package's onelf update key. Its "
                    "secret half is already in the `release` environment as "
                    "`ONELF_SIGNING_KEY`. From the next release on, every bundle is "
                    "signed and accepts only signed updates; until then they update "
                    "unsigned.\n\nMerged by the control repository once every check passes.")
    if failed:
        raise SystemExit(f"failed: {', '.join(failed)}")


def status():
    print("| Package | Version | Upstream | Latest release | Last build |")
    print("|---------|---------|----------|----------------|------------|")
    for repo in repos():
        full = f"{ORG}/{repo}"
        with tempfile.TemporaryDirectory() as tmp:
            toml = Path(tmp) / "package.toml"
            toml.write_text(run("gh", "api", f"repos/{full}/contents/package.toml",
                                "-H", "Accept: application/vnd.github.raw"))
            package = tomllib.loads(toml.read_text())
            behind = run(sys.executable, str(ROOT / "scripts/upstream.py"),
                         "latest", str(toml))
        version = f"{package['sources'][package['name']]['version']}-{package['release']}"
        upstream_cell = "<br>".join(
            f"{name} {have}" + ("" if have == newest else f" &rarr; **{newest}**")
            for name, have, newest in (line.split() for line in behind.splitlines())
        )
        releases = gh_json("api", f"repos/{full}/releases?per_page=1")
        release = (f"[{releases[0]['tag_name']}]({releases[0]['html_url']})"
                   if releases else "none")
        runs = gh_json("api", f"repos/{full}/actions/workflows/release.yml/runs"
                              "?branch=main&per_page=1")["workflow_runs"]
        build = (f"[{runs[0]['conclusion'] or runs[0]['status']}]({runs[0]['html_url']})"
                 if runs else "none")
        print(f"| [{repo}](https://github.com/{full}) | {version} | {upstream_cell} "
              f"| {release} | {build} |")


def main():
    commands = {"upstream": upstream, "merge": merge, "keys": keys, "status": status}
    if len(sys.argv) == 3 and sys.argv[1] == "sync":
        sync(sys.argv[2])
    elif len(sys.argv) == 2 and sys.argv[1] in commands:
        commands[sys.argv[1]]()
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
