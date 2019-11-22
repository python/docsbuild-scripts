#!/usr/bin/env python

from pathlib import Path
import argparse
import asyncio
import logging
import re
import subprocess

import httpx
from tabulate import tabulate
import git

import build_docs

logger = logging.getLogger(__name__)


def parse_args():
    parser = argparse.ArgumentParser(
        description="""Check the version of our build in different branches
        Hint: Use with | column -t"""
    )
    parser.add_argument("cpython_clone", help="Path to a clone of cpython", type=Path)
    return parser.parse_args()


def remote_by_url(repo: git.Repo, url_pattern: str):
    """Find a remote of repo matching the regex url_pattern.
    """
    for remote in repo.remotes:
        for url in remote.urls:
            if re.search(url_pattern, url):
                return remote


def find_sphinx_spec(text: str):
    if found := re.search(
        """sphinx[=<>~]{1,2}[0-9.]{3,}|needs_sphinx = [0-9.'"]*""", text, flags=re.I
    ):
        return found.group(0).replace(" ", "")


def find_sphinx_in_file(repo: git.Repo, branch, filename):
    upstream = remote_by_url(repo, "github.com.python").name
    try:
        return find_sphinx_spec(repo.git.show(f"{upstream}/{branch}:{filename}"))
    except git.exc.GitCommandError:
        return "ø"


CONF_FILES = {
    "travis": ".travis.yml",
    "azure": ".azure-pipelines/docs-steps.yml",
    "requirements.txt": "Doc/requirements.txt",
    "conf.py": "Doc/conf.py",
    "Makefile": "Doc/Makefile",
    "Mac installer": "Mac/BuildScript/build-installer.py",
}


def search_sphinx_versions_in_cpython(repo: git.Repo):
    repo.git.fetch("https://github.com/python/cpython")
    table = []
    for _, branch, _ in sorted(build_docs.BRANCHES):
        table.append(
            [
                branch,
                *[
                    find_sphinx_in_file(repo, branch, filename)
                    for filename in CONF_FILES.values()
                ],
            ]
        )
    print(tabulate(table, headers=["branch", *CONF_FILES.keys()], tablefmt='rst'))


async def get_version_in_prod(language, version):
    url = f"https://docs.python.org/{language}/{version}".replace("/en/", "/")
    response = await httpx.get(url, timeout=5)
    text = response.text.encode("ASCII", errors="ignore").decode("ASCII")
    if created_using := re.search(
        r"sphinx.pocoo.org.*?([0-9.]+[0-9])", text, flags=re.M
    ):
        return created_using.group(1)
    return "ø"


async def which_sphinx_is_used_in_production():
    table = []
    for version, _, _ in sorted(build_docs.BRANCHES):
        table.append(
            [
                version,
                *await asyncio.gather(
                    *[
                        get_version_in_prod(language, version)
                        for language in build_docs.LANGUAGES
                    ]
                ),
            ]
        )
    print(tabulate(table, headers=["branch", *build_docs.LANGUAGES], tablefmt='rst'))


def main():
    logging.basicConfig(level=logging.INFO)
    args = parse_args()
    repo = git.Repo(args.cpython_clone)
    print(
        "Docs build server is configured to use",
        find_sphinx_in_file(git.Repo(), "master", "requirements.txt"),
    )
    print()
    print("Sphinx configuration in various branches:", end="\n\n")
    search_sphinx_versions_in_cpython(repo)
    print()
    print("Sphinx build as seen on docs.python.org:", end="\n\n")
    asyncio.run(which_sphinx_is_used_in_production())


if __name__ == "__main__":
    main()
