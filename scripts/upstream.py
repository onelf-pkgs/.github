#!/usr/bin/env python3
"""Follow upstream releases for the sources a package repository builds.

Each package repository describes its sources in package.toml:

    [sources.mesa]
    version = "26.2.5"
    url = "https://archive.mesa3d.org/mesa-{version}.tar.xz"
    sha256 = "..."
    upstream = { gitlab = "gitlab.freedesktop.org/mesa/mesa", tag = "^mesa-(...)$" }

`upstream` names a GitHub repository (`github = "owner/repo"`, read from
its releases) or a GitLab project (`gitlab = "host/group/project"`, read
from its tags), and `tag`, a pattern whose first group is the version.

Usage:
    upstream.py latest <package.toml>   print `<source> <current> <latest>`
    upstream.py bump <package.toml>     move every source to its latest
                                        release, print what moved

A bump downloads the new source to hash it and resets `release` to 1.
"""

import hashlib
import json
import os
import re
import sys
import tomllib
import urllib.parse
import urllib.request


def get(url, headers=None):
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def version_key(version):
    return tuple(int(part) for part in version.split("."))


def github_tags(repo):
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    releases = get(f"https://api.github.com/repos/{repo}/releases?per_page=100", headers)
    return [r["tag_name"] for r in releases if not r["draft"] and not r["prerelease"]]


def gitlab_tags(project, pattern):
    host, path = project.split("/", 1)
    quoted = urllib.parse.quote(path, safe="")
    # A project's tags include every old naming scheme it ever used, so
    # ask only for the ones starting with the pattern's literal prefix.
    prefix = re.match(r"\^?([\w-]*)", pattern).group(1)
    search = urllib.parse.quote("^" + prefix)
    tags = get(
        f"https://{host}/api/v4/projects/{quoted}/repository/tags"
        f"?order_by=version&sort=desc&per_page=100&search={search}"
    )
    return [t["name"] for t in tags]


def latest(upstream):
    if "github" in upstream:
        tags = github_tags(upstream["github"])
    elif "gitlab" in upstream:
        tags = gitlab_tags(upstream["gitlab"], upstream["tag"])
    else:
        raise SystemExit(f"unknown upstream: {upstream}")
    pattern = re.compile(upstream["tag"])
    versions = [m.group(1) for m in map(pattern.match, tags) if m]
    if not versions:
        raise SystemExit(f"no tag matches {upstream['tag']}")
    return max(versions, key=version_key)


def sha256_of(url):
    digest = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=600) as response:
        while chunk := response.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def outdated(path):
    with open(path, "rb") as f:
        package = tomllib.load(f)
    for name, source in package.get("sources", {}).items():
        if "upstream" not in source:
            continue
        newest = latest(source["upstream"])
        yield name, source, newest


def set_in_section(text, section, key, value):
    """Rewrite `key = ...` inside `[section]`, leaving the rest as written."""
    header = re.compile(rf"^\[{re.escape(section)}\]\s*$", re.M)
    start = header.search(text)
    if not start:
        raise SystemExit(f"no [{section}] in package.toml")
    end = re.compile(r"^\[", re.M).search(text, start.end())
    stop = end.start() if end else len(text)
    body = text[start.end():stop]
    line = re.compile(rf"^{re.escape(key)}\s*=.*$", re.M)
    if not line.search(body):
        raise SystemExit(f"no {key} in [{section}]")
    body = line.sub(lambda _: f"{key} = {value}", body, count=1)
    return text[: start.end()] + body + text[stop:]


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in ("latest", "bump"):
        raise SystemExit(__doc__)
    command, path = sys.argv[1], sys.argv[2]

    if command == "latest":
        for name, source, newest in outdated(path):
            print(name, source["version"], newest)
        return

    with open(path) as f:
        text = f.read()
    moved = False
    for name, source, newest in outdated(path):
        if version_key(newest) <= version_key(source["version"]):
            continue
        url = source["url"].replace("{version}", newest)
        text = set_in_section(text, f"sources.{name}", "version", f'"{newest}"')
        text = set_in_section(text, f"sources.{name}", "sha256", f'"{sha256_of(url)}"')
        print(f"{name} {source['version']} -> {newest}")
        moved = True
    if moved:
        text = re.sub(r"^release\s*=.*$", "release = 1", text, count=1, flags=re.M)
        with open(path, "w") as f:
            f.write(text)


if __name__ == "__main__":
    main()
